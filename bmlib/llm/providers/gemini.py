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

from bmlib.llm.providers.base import ModelMetadata, ModelPricing, ProviderCapabilities
from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider


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
        ),
        ModelMetadata(
            model_id="gemini-2.5-pro-preview-05-06",
            display_name="Gemini 2.5 Pro Preview",
            context_window=1_000_000,
            pricing=ModelPricing(input_cost=1.25, output_cost=10.0),
        ),
        ModelMetadata(
            model_id="gemini-2.5-flash-preview-05-20",
            display_name="Gemini 2.5 Flash Preview",
            context_window=1_000_000,
            pricing=ModelPricing(input_cost=0.15, output_cost=0.60),
        ),
    ]
