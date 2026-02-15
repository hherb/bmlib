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

"""Mistral AI provider — Mistral models via OpenAI-compatible API."""

from __future__ import annotations

from bmlib.llm.providers.base import ModelMetadata, ModelPricing, ProviderCapabilities
from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    """Mistral AI models via OpenAI-compatible API."""

    PROVIDER_NAME = "mistral"
    DISPLAY_NAME = "Mistral AI"
    DESCRIPTION = "Mistral models (Large, Small, Codestral)"
    WEBSITE_URL = "https://console.mistral.ai"
    SETUP_INSTRUCTIONS = "Get API key from console.mistral.ai/api-keys"

    API_KEY_ENV_VAR = "MISTRAL_API_KEY"
    DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
    DEFAULT_MODEL = "mistral-large-latest"

    MODEL_PRICING = {
        "mistral-large-latest": ModelPricing(input_cost=2.0, output_cost=6.0),
        "mistral-small-latest": ModelPricing(input_cost=0.1, output_cost=0.3),
        "codestral-latest": ModelPricing(input_cost=0.3, output_cost=0.9),
        "ministral-8b-latest": ModelPricing(input_cost=0.1, output_cost=0.1),
        "pixtral-large-latest": ModelPricing(input_cost=2.0, output_cost=6.0),
    }

    FALLBACK_MODELS = [
        ModelMetadata(
            model_id="mistral-large-latest",
            display_name="Mistral Large",
            context_window=128_000,
            pricing=ModelPricing(input_cost=2.0, output_cost=6.0),
            capabilities=ProviderCapabilities(
                supports_vision=True,
                supports_function_calling=True,
                supports_system_messages=True,
                max_context_window=128_000,
            ),
        ),
        ModelMetadata(
            model_id="mistral-small-latest",
            display_name="Mistral Small",
            context_window=128_000,
            pricing=ModelPricing(input_cost=0.1, output_cost=0.3),
        ),
        ModelMetadata(
            model_id="codestral-latest",
            display_name="Codestral",
            context_window=256_000,
            pricing=ModelPricing(input_cost=0.3, output_cost=0.9),
        ),
    ]
