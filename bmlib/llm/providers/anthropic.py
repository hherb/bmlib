"""Anthropic Claude API provider."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """Anthropic Claude API provider."""

    PROVIDER_NAME = "anthropic"
    DISPLAY_NAME = "Anthropic"
    DESCRIPTION = "Claude models via Anthropic API"
    WEBSITE_URL = "https://console.anthropic.com"
    SETUP_INSTRUCTIONS = "Get API key from console.anthropic.com/account/keys"

    MODEL_PRICING: dict[str, ModelPricing] = {
        "claude-opus-4-20250514": ModelPricing(input_cost=15.0, output_cost=75.0),
        "claude-sonnet-4-20250514": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-5-sonnet-20241022": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-5-haiku-20241022": ModelPricing(input_cost=1.0, output_cost=5.0),
        "claude-3-opus-20240229": ModelPricing(input_cost=15.0, output_cost=75.0),
        "claude-3-sonnet-20240229": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-haiku-20240307": ModelPricing(input_cost=0.25, output_cost=1.25),
    }

    CACHE_TTL = 3600  # seconds

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: object,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(api_key=resolved_api_key, base_url=base_url, **kwargs)
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0

    # --- Properties ---

    @property
    def is_local(self) -> bool:
        return False

    @property
    def is_free(self) -> bool:
        return False

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def api_key_env_var(self) -> str:
        return "ANTHROPIC_API_KEY"

    @property
    def default_base_url(self) -> str:
        return "https://api.anthropic.com"

    @property
    def default_model(self) -> str:
        return "claude-sonnet-4-20250514"

    # --- Client ---

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                kwargs: dict[str, object] = {"api_key": self._api_key}
                if self._base_url and self._base_url != self.default_base_url:
                    kwargs["base_url"] = self._base_url
                self._client = anthropic.Anthropic(**kwargs)
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Install with: pip install anthropic"
                )
        return self._client

    # --- Core operations ---

    def chat(
        self,
        messages: list[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        model = model or self.default_model
        client = self._get_client()

        top_p: Optional[float] = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]

        # Separate system message (Anthropic API requirement)
        system_content = ""
        chat_messages: list[dict[str, str]] = []
        for msg in messages:
            if msg.role == "system":
                system_content = msg.content
            else:
                chat_messages.append({"role": msg.role, "content": msg.content})

        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content:
            request_kwargs["system"] = system_content
        if top_p is not None:
            request_kwargs["top_p"] = top_p

        response = client.messages.create(**request_kwargs)

        content = ""
        if response.content:
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

        if json_mode:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                content = self._extract_json(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )

    def _extract_json(self, text: str) -> str:
        """Extract JSON from text that may contain markdown code blocks."""
        code_block_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL
        )
        if code_block_match:
            candidate = code_block_match.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(0)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        return text

    # --- Model listing ---

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < self.CACHE_TTL
        ):
            return self._models_cache

        try:
            client = self._get_client()
            api_models = client.models.list()
            models = []
            for model in api_models:
                model_id = model.id
                pricing = self.MODEL_PRICING.get(
                    model_id, ModelPricing(input_cost=3.0, output_cost=15.0)
                )
                models.append(
                    ModelMetadata(
                        model_id=model_id,
                        display_name=getattr(model, "display_name", model_id),
                        context_window=getattr(model, "context_window", 200_000),
                        pricing=pricing,
                        capabilities=ProviderCapabilities(
                            supports_vision=True,
                            supports_function_calling=True,
                            supports_system_messages=True,
                            max_context_window=getattr(
                                model, "context_window", 200_000
                            ),
                        ),
                    )
                )
            self._models_cache = models
            self._cache_timestamp = time.time()
            return models
        except Exception as e:
            logger.warning("Failed to fetch models from Anthropic API: %s", e)
            return [
                ModelMetadata(
                    model_id=mid,
                    display_name=mid,
                    context_window=200_000,
                    pricing=p,
                )
                for mid, p in self.MODEL_PRICING.items()
            ]

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        try:
            import anthropic
            client = self._get_client()
            models = list(client.models.list())
            return True, f"Connected. {len(models)} models available."
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        try:
            client = self._get_client()
            return client.count_tokens(text)
        except Exception:
            return len(text) // 4

    def get_model_pricing(self, model: str) -> ModelPricing:
        return self.MODEL_PRICING.get(
            model, ModelPricing(input_cost=3.0, output_cost=15.0)
        )
