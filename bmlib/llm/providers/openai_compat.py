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

"""Base class for providers that expose an OpenAI-compatible chat API.

Subclasses set class-level constants for provider metadata, base URL,
API key env var, default model, fallback model list, and pricing dict.
The ``openai`` Python SDK handles the actual HTTP calls.

Usage::

    class MyProvider(OpenAICompatibleProvider):
        PROVIDER_NAME = "myprovider"
        DISPLAY_NAME = "My Provider"
        DESCRIPTION = "My LLM provider"
        WEBSITE_URL = "https://myprovider.ai"
        SETUP_INSTRUCTIONS = "Get API key at myprovider.ai"
        API_KEY_ENV_VAR = "MY_API_KEY"
        DEFAULT_BASE_URL = "https://api.myprovider.ai/v1"
        DEFAULT_MODEL = "my-model"
        FALLBACK_MODELS = [...]
        MODEL_PRICING = {...}
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN_ESTIMATE = 4
CACHE_TTL_SECONDS = 3600


class OpenAICompatibleProvider(BaseProvider):
    """Base for providers that support the OpenAI chat completions API."""

    # --- Subclass MUST override these ---
    API_KEY_ENV_VAR: str = ""
    DEFAULT_BASE_URL: str = ""
    DEFAULT_MODEL: str = ""
    FALLBACK_MODELS: list[ModelMetadata] = []
    MODEL_PRICING: dict[str, ModelPricing] = {}

    _FALLBACK_PRICING = ModelPricing(input_cost=1.0, output_cost=3.0)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        resolved_key = api_key or os.environ.get(self.API_KEY_ENV_VAR, "")
        resolved_url = base_url or self.DEFAULT_BASE_URL
        super().__init__(api_key=resolved_key or None, base_url=resolved_url, **kwargs)
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0.0

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
        return self.API_KEY_ENV_VAR

    @property
    def default_base_url(self) -> str:
        return self.DEFAULT_BASE_URL

    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    # --- Client ---

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package not installed. Install with: pip install openai"
                )
            self._client = OpenAI(
                api_key=self._api_key or "unused",
                base_url=self._base_url,
            )
        return self._client

    # --- Chat ---

    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        model = model or self.default_model
        client = self._get_client()

        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]

        openai_messages: list[dict[str, str]] = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if top_p is not None:
            request_kwargs["top_p"] = top_p
        if json_mode:
            request_kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**request_kwargs)

        choice = response.choices[0]
        content = choice.message.content or ""

        if json_mode and content:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                content = _extract_json(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            stop_reason=choice.finish_reason,
        )

    # --- Model listing ---

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < CACHE_TTL_SECONDS
        ):
            return self._models_cache

        try:
            client = self._get_client()
            api_response = client.models.list()
            model_list = api_response.data if hasattr(api_response, "data") else []
            models = []
            for m in model_list:
                model_id = m.id
                pricing = self.MODEL_PRICING.get(model_id, self._FALLBACK_PRICING)
                models.append(
                    ModelMetadata(
                        model_id=model_id,
                        display_name=model_id,
                        context_window=128_000,
                        pricing=pricing,
                        capabilities=ProviderCapabilities(
                            supports_system_messages=True,
                            max_context_window=128_000,
                        ),
                    )
                )
            if models:
                self._models_cache = models
                self._cache_timestamp = time.time()
                return models
        except Exception as e:
            logger.warning(
                "Failed to fetch models from %s API: %s", self.DISPLAY_NAME, e
            )

        return list(self.FALLBACK_MODELS)

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
            result = client.models.list()
            data = result.data if hasattr(result, "data") else []
            return True, f"Connected. {len(data)} models available."
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def get_model_pricing(self, model: str) -> ModelPricing:
        return self.MODEL_PRICING.get(model, self._FALLBACK_PRICING)


def _extract_json(text: str) -> str:
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
