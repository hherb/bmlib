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
from typing import Any

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)

# Rough chars-per-token ratio for fallback estimation
CHARS_PER_TOKEN_ESTIMATE = 4

# Default context window when model metadata is unavailable (tokens)
FALLBACK_CONTEXT_WINDOW = 8192

# Pricing for local models (always free)
_FREE_PRICING = ModelPricing(0.0, 0.0)


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Extract a field from a dict or Pydantic-model response.

    The ``ollama`` SDK (>=0.4) returns Pydantic models with subscript
    access but without ``.get()``.  Older versions returned plain dicts.
    This helper handles both transparently.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class OllamaProvider(BaseProvider):
    """Ollama local model provider (native API)."""

    PROVIDER_NAME = "ollama"
    DISPLAY_NAME = "Ollama"
    DESCRIPTION = "Local models via Ollama server (free)"
    WEBSITE_URL = "https://ollama.ai"
    SETUP_INSTRUCTIONS = "Install from ollama.ai, then run 'ollama pull <model>'"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
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
        """Whether this provider runs locally."""
        return True

    @property
    def is_free(self) -> bool:
        """Whether this provider is free to use."""
        return True

    @property
    def requires_api_key(self) -> bool:
        """Whether an API key is required."""
        return False

    @property
    def default_base_url(self) -> str:
        """Default Ollama server URL."""
        return "http://localhost:11434"

    @property
    def default_model(self) -> str:
        """Default model to use when none is specified."""
        return "medgemma4B_it_q8"

    # --- Client ---

    def _get_client(self) -> Any:
        """Lazily initialise and return the ``ollama.Client``."""
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
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        """Send a chat request to the local Ollama server.

        Args:
            messages: Conversation messages.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Extra options (``top_p``, ``json_mode``, ``think``).
        """
        model = model or self.default_model
        client = self._get_client()

        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]
        think: bool | None = kwargs.get("think")  # type: ignore[assignment]

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

        # ollama >=0.4 returns Pydantic models; older versions return dicts.
        message = _safe_get(response, "message")
        content: str = _safe_get(message, "content", "") if message else ""
        input_tokens: int = (
            _safe_get(response, "prompt_eval_count")
            or self._estimate_tokens(messages)
        )
        output_tokens: int = (
            _safe_get(response, "eval_count")
            or len(content) // CHARS_PER_TOKEN_ESTIMATE
        )

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason="stop",
        )

    # --- Model discovery (native API) ---

    def list_models(self) -> list[ModelMetadata]:
        """List models currently available on the Ollama server."""
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
            context_window = _extract_context_window(info)
            details = _safe_get(info, "details") or {}
            parameter_size = _safe_get(details, "parameter_size", "")
            display_name = (
                f"{model_name} ({parameter_size})" if parameter_size else model_name
            )

            metadata = ModelMetadata(
                model_id=model_name,
                display_name=display_name,
                context_window=context_window,
                pricing=_FREE_PRICING,
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
                context_window=FALLBACK_CONTEXT_WINDOW,
                pricing=_FREE_PRICING,
            )

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity to the local Ollama server."""
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

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate token count (character-based heuristic)."""
        return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def _estimate_tokens(self, messages: list[LLMMessage]) -> int:
        """Estimate total token count across all messages."""
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // CHARS_PER_TOKEN_ESTIMATE

    def get_model_pricing(self, model: str) -> ModelPricing:
        """Return pricing for *model* (always free for Ollama)."""
        return _FREE_PRICING

    def get_model_metadata(self, model: str) -> ModelMetadata | None:
        """Return metadata for *model*, fetching via ``ollama.show()``."""
        return self._get_model_info(model)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _extract_context_window(info: Any) -> int:
    """Extract context window size from an ``ollama.show()`` response.

    Checks (in order): ``model_info`` keys, ``parameters.num_ctx``,
    and ``modelfile`` text.  Falls back to :data:`FALLBACK_CONTEXT_WINDOW`.
    """
    model_info = _safe_get(info, "model_info") or {}
    if isinstance(model_info, dict):
        for key, value in model_info.items():
            if "context" in key.lower() and isinstance(value, int):
                return value
    else:
        # Pydantic model — iterate via items if available
        items = getattr(model_info, "items", None)
        if callable(items):
            for key, value in items():
                if "context" in str(key).lower() and isinstance(value, int):
                    return value

    parameters = _safe_get(info, "parameters") or {}
    if isinstance(parameters, dict) and "num_ctx" in parameters:
        return int(parameters["num_ctx"])

    modelfile = _safe_get(info, "modelfile", "")
    if modelfile and "num_ctx" in modelfile:
        match = re.search(r"num_ctx\s+(\d+)", modelfile)
        if match:
            return int(match.group(1))

    return FALLBACK_CONTEXT_WINDOW
