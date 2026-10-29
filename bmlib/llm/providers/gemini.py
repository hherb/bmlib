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

"""Google Gemini provider — Gemini models via OpenAI-compatible API."""

from __future__ import annotations

from bmlib.llm.context_window import (
    ContextWindowError,
    NoSuchModelError,
    fetch_context_window,
)
from bmlib.llm.providers.base import ModelMetadata, ModelPricing, ProviderCapabilities
from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider

HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
HTTP_ERROR = 400

LOOKUP_TIMEOUT_SECONDS = 30.0

#: The OpenAI-compatible base URL ends in this; the model metadata lives
#: on the native API one level up.
OPENAI_SUFFIX = "/openai"


class GeminiProvider(OpenAICompatibleProvider):
    """Google Gemini models via OpenAI-compatible API."""

    PROVIDER_NAME = "gemini"
    DISPLAY_NAME = "Google Gemini"
    DESCRIPTION = "Gemini models via Google AI Studio"
    WEBSITE_URL = "https://aistudio.google.com"
    SETUP_INSTRUCTIONS = "Get API key from aistudio.google.com/apikey"

    API_KEY_ENV_VAR = "GEMINI_API_KEY"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    DEFAULT_MODEL = "gemini-2.0-flash"

    MODEL_PRICING = {
        "gemini-2.0-flash": ModelPricing(input_cost=0.10, output_cost=0.40),
        "gemini-2.0-flash-lite": ModelPricing(input_cost=0.0, output_cost=0.0),
        "gemini-1.5-pro": ModelPricing(input_cost=1.25, output_cost=5.0),
        "gemini-1.5-flash": ModelPricing(input_cost=0.075, output_cost=0.30),
        "gemini-2.5-pro-preview-05-06": ModelPricing(input_cost=1.25, output_cost=10.0),
        "gemini-2.5-flash-preview-05-20": ModelPricing(input_cost=0.15, output_cost=0.60),
    }

    FALLBACK_MODELS = [
        ModelMetadata(
            model_id="gemini-2.0-flash",
            display_name="Gemini 2.0 Flash",
            context_window=1_000_000,
            pricing=ModelPricing(input_cost=0.10, output_cost=0.40),
            capabilities=ProviderCapabilities(
                supports_streaming=True,
                supports_vision=True,
                supports_function_calling=True,
                supports_system_messages=True,
                max_context_window=1_000_000,
            ),
        ),
        ModelMetadata(
            model_id="gemini-1.5-pro",
            display_name="Gemini 1.5 Pro",
            context_window=2_000_000,
            pricing=ModelPricing(input_cost=1.25, output_cost=5.0),
            capabilities=ProviderCapabilities(supports_streaming=True),
        ),
        ModelMetadata(
            model_id="gemini-2.5-pro-preview-05-06",
            display_name="Gemini 2.5 Pro Preview",
            context_window=1_000_000,
            pricing=ModelPricing(input_cost=1.25, output_cost=10.0),
            capabilities=ProviderCapabilities(supports_streaming=True),
        ),
        ModelMetadata(
            model_id="gemini-2.5-flash-preview-05-20",
            display_name="Gemini 2.5 Flash Preview",
            context_window=1_000_000,
            pricing=ModelPricing(input_cost=0.15, output_cost=0.60),
            capabilities=ProviderCapabilities(supports_streaming=True),
        ),
    ]

    def context_window(self, model: str) -> int | None:
        """The model's ``inputTokenLimit``, from the native ``models.get``.

        The OpenAI-compatible endpoint lists no windows, so this asks the
        native API (the base URL without its ``/openai``). A 404 is
        :class:`NoSuchModelError`; a 429, a 5xx or a transport error is
        retried with backoff; any other refusal is not.

        Raises:
            NoSuchModelError: Gemini has no such model.
            ContextWindowError: The request failed.
        """
        import httpx

        native = self._base_url.rstrip("/").removesuffix(OPENAI_SUFFIX)

        def fetch() -> object:
            resp = httpx.get(
                f"{native}/models/{model}",
                headers={"x-goog-api-key": self._api_key or ""},
                timeout=LOOKUP_TIMEOUT_SECONDS,
            )
            if resp.status_code == HTTP_NOT_FOUND:
                raise NoSuchModelError(
                    f"gemini has no model {model!r}", provider=self.PROVIDER_NAME, model=model
                )
            status = resp.status_code
            if status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR:
                resp.raise_for_status()  # HTTPStatusError: retried
            elif status >= HTTP_ERROR:
                # Any other refusal (400, 403) will not change on a retry.
                raise ContextWindowError(
                    f"gemini refused the lookup for {model!r}: HTTP {status}",
                    provider=self.PROVIDER_NAME,
                    model=model,
                )
            return resp.json()

        return fetch_context_window(
            self.PROVIDER_NAME,
            model,
            fetch,
            retry_on=(httpx.HTTPStatusError, httpx.TransportError),
        )
