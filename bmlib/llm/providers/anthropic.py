# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Anthropic Claude API provider."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)
from bmlib.llm.utils import extract_json

logger = logging.getLogger(__name__)

# Default context window for Anthropic models (tokens)
DEFAULT_CONTEXT_WINDOW = 200_000

# How long to cache the model list (seconds)
CACHE_TTL_SECONDS = 3600

# Rough chars-per-token ratio for fallback estimation
CHARS_PER_TOKEN_ESTIMATE = 4


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
        "claude-sonnet-4-5-20250929": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-5-sonnet-20241022": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-5-haiku-20241022": ModelPricing(input_cost=1.0, output_cost=5.0),
        "claude-3-opus-20240229": ModelPricing(input_cost=15.0, output_cost=75.0),
        "claude-3-sonnet-20240229": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-haiku-20240307": ModelPricing(input_cost=0.25, output_cost=1.25),
    }

    # Fallback pricing when a model ID is not in MODEL_PRICING
    _FALLBACK_PRICING = ModelPricing(input_cost=3.0, output_cost=15.0)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(api_key=resolved_api_key, base_url=base_url, **kwargs)
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0.0

    # --- Properties ---

    @property
    def is_local(self) -> bool:
        """Whether this provider runs locally."""
        return False

    @property
    def is_free(self) -> bool:
        """Whether this provider is free to use."""
        return False

    @property
    def requires_api_key(self) -> bool:
        """Whether an API key is required."""
        return True

    @property
    def api_key_env_var(self) -> str:
        """Environment variable name for the API key."""
        return "ANTHROPIC_API_KEY"

    @property
    def default_base_url(self) -> str:
        """Default API base URL."""
        return "https://api.anthropic.com"

    @property
    def default_model(self) -> str:
        """Default model to use when none is specified."""
        return "claude-sonnet-4-20250514"

    # --- Client ---

    def _get_client(self) -> Any:
        """Lazily initialise and return the ``anthropic.Anthropic`` client."""
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
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        """Send a chat request to the Anthropic API.

        Args:
            messages: Conversation messages.
            model: Model identifier (e.g. ``"claude-sonnet-4-20250514"``).
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Extra options (``top_p``, ``json_mode``).
        """
        model = model or self.default_model
        client = self._get_client()

        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
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
                content = extract_json(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )

    # --- Model listing ---

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        """List available models from the Anthropic API (cached).

        Args:
            force_refresh: Bypass the cache and re-query the API.
        """
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < CACHE_TTL_SECONDS
        ):
            return self._models_cache

        try:
            client = self._get_client()
            api_models = client.models.list()
            models = []
            for model in api_models:
                model_id = model.id
                pricing = self.MODEL_PRICING.get(model_id, self._FALLBACK_PRICING)
                display_name = getattr(model, "display_name", model_id)
                models.append(
                    ModelMetadata(
                        model_id=model_id,
                        display_name=display_name,
                        context_window=DEFAULT_CONTEXT_WINDOW,
                        pricing=pricing,
                        capabilities=ProviderCapabilities(
                            supports_vision=True,
                            supports_function_calling=True,
                            supports_system_messages=True,
                            max_context_window=DEFAULT_CONTEXT_WINDOW,
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
                    context_window=DEFAULT_CONTEXT_WINDOW,
                    pricing=p,
                )
                for mid, p in self.MODEL_PRICING.items()
            ]

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity to the Anthropic API."""
        try:
            client = self._get_client()
            models = list(client.models.list())
            return True, f"Connected. {len(models)} models available."
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Count tokens in *text* using the Anthropic token-counting API.

        Falls back to a rough character-based estimate on failure.
        """
        try:
            client = self._get_client()
            result = client.messages.count_tokens(
                model=model or self.default_model,
                messages=[{"role": "user", "content": text}],
            )
            return result.input_tokens
        except Exception:
            return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def get_model_pricing(self, model: str) -> ModelPricing:
        """Return pricing for *model*, falling back to default rates."""
        return self.MODEL_PRICING.get(model, self._FALLBACK_PRICING)

