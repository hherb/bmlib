"""Ollama local model provider — native API.

Uses the ``ollama`` Python package which talks to the Ollama server's
native REST API (not the OpenAI-compatible endpoint).  This gives access
to Ollama-specific features such as model discovery via ``ollama.show()``
and native parameters (e.g. thinking mode toggles) that are not reliably
exposed through the OpenAI compatibility layer.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    """Ollama local model provider (native API)."""

    PROVIDER_NAME = "ollama"
    DISPLAY_NAME = "Ollama"
    DESCRIPTION = "Local models via Ollama server (free)"
    WEBSITE_URL = "https://ollama.ai"
    SETUP_INSTRUCTIONS = "Install from ollama.ai, then run 'ollama pull <model>'"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: object,
    ) -> None:
        resolved_base_url = base_url or os.environ.get(
            "OLLAMA_HOST", "http://localhost:11434"
        )
        super().__init__(api_key=None, base_url=resolved_base_url, **kwargs)
        self._model_info_cache: dict[str, ModelMetadata] = {}

    # --- Properties ---

    @property
    def is_local(self) -> bool:
        return True

    @property
    def is_free(self) -> bool:
        return True

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def default_base_url(self) -> str:
        return "http://localhost:11434"

    @property
    def default_model(self) -> str:
        return "medgemma4B_it_q8"

    # --- Client ---

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self._base_url)
            except ImportError:
                raise ImportError(
                    "ollama package not installed. Install with: pip install ollama"
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
        think: Optional[bool] = kwargs.get("think")  # type: ignore[assignment]

        ollama_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        options: dict[str, object] = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if top_p is not None:
            options["top_p"] = top_p

        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": ollama_messages,
            "options": options,
        }

        if json_mode:
            request_kwargs["format"] = "json"

        if think is not None:
            request_kwargs["think"] = think

        response = client.chat(**request_kwargs)

        content = response.get("message", {}).get("content", "")
        input_tokens = response.get(
            "prompt_eval_count", self._estimate_tokens(messages)
        )
        output_tokens = response.get("eval_count", len(content) // 4)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason="stop",
        )

    # --- Model discovery (native API) ---

    def list_models(self) -> list[ModelMetadata]:
        try:
            client = self._get_client()
            response = client.list()
            models = []
            model_list = getattr(response, "models", []) or []
            for model_info in model_list:
                name = getattr(model_info, "model", "") or ""
                if name:
                    metadata = self._get_model_info(name)
                    models.append(metadata)
            return models
        except Exception as e:
            logger.warning("Failed to list Ollama models: %s", e)
            return []

    def _get_model_info(self, model_name: str) -> ModelMetadata:
        """Fetch model metadata using ``ollama.show()`` (cached)."""
        if model_name in self._model_info_cache:
            return self._model_info_cache[model_name]

        try:
            client = self._get_client()
            info = client.show(model_name)
            context_window = self._extract_context_window(info)
            details = info.get("details", {})
            parameter_size = details.get("parameter_size", "")
            display_name = (
                f"{model_name} ({parameter_size})" if parameter_size else model_name
            )

            metadata = ModelMetadata(
                model_id=model_name,
                display_name=display_name,
                context_window=context_window,
                pricing=ModelPricing(0.0, 0.0),
                capabilities=ProviderCapabilities(
                    supports_system_messages=True,
                    max_context_window=context_window,
                ),
            )
            self._model_info_cache[model_name] = metadata
            return metadata

        except Exception as e:
            logger.debug("Failed to get model info for %s: %s", model_name, e)
            return ModelMetadata(
                model_id=model_name,
                display_name=model_name,
                context_window=8192,
                pricing=ModelPricing(0.0, 0.0),
            )

    def _extract_context_window(self, info: dict) -> int:
        """Extract context window from ``ollama.show()`` response."""
        model_info = info.get("model_info", {})
        for key, value in model_info.items():
            if "context" in key.lower() and isinstance(value, int):
                return value

        parameters = info.get("parameters", {})
        if isinstance(parameters, dict) and "num_ctx" in parameters:
            return int(parameters["num_ctx"])

        modelfile = info.get("modelfile", "")
        if modelfile and "num_ctx" in modelfile:
            match = re.search(r"num_ctx\s+(\d+)", modelfile)
            if match:
                return int(match.group(1))

        return 8192

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
            response = client.list()
            model_list = getattr(response, "models", []) or []
            if model_list:
                return True, f"Connected. {len(model_list)} models available."
            return True, "Connected. No models installed."
        except ImportError:
            return False, "ollama package not installed"
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        return len(text) // 4

    def _estimate_tokens(self, messages: list[LLMMessage]) -> int:
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 4

    def get_model_pricing(self, model: str) -> ModelPricing:
        return ModelPricing(0.0, 0.0)

    def get_model_metadata(self, model: str) -> Optional[ModelMetadata]:
        return self._get_model_info(model)
