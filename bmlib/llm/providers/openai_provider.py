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

"""OpenAI provider — GPT models via the OpenAI API."""

from __future__ import annotations

from bmlib.llm.providers.base import ModelMetadata, ModelPricing, ProviderCapabilities
from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI GPT models."""

    PROVIDER_NAME = "openai"
    DISPLAY_NAME = "OpenAI"
    DESCRIPTION = "GPT models via OpenAI API"
    WEBSITE_URL = "https://platform.openai.com"
    SETUP_INSTRUCTIONS = "Get API key from platform.openai.com/api-keys"

    API_KEY_ENV_VAR = "OPENAI_API_KEY"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o"

    MODEL_PRICING = {
        "gpt-4o": ModelPricing(input_cost=2.50, output_cost=10.0),
        "gpt-4o-mini": ModelPricing(input_cost=0.15, output_cost=0.60),
        "gpt-4-turbo": ModelPricing(input_cost=10.0, output_cost=30.0),
        "o1": ModelPricing(input_cost=15.0, output_cost=60.0),
        "o1-mini": ModelPricing(input_cost=3.0, output_cost=12.0),
        "o3-mini": ModelPricing(input_cost=1.10, output_cost=4.40),
    }

    FALLBACK_MODELS = [
        ModelMetadata(
            model_id="gpt-4o",
            display_name="GPT-4o",
            context_window=128_000,
            pricing=ModelPricing(input_cost=2.50, output_cost=10.0),
            capabilities=ProviderCapabilities(
                supports_vision=True,
                supports_function_calling=True,
                supports_system_messages=True,
                max_context_window=128_000,
            ),
        ),
        ModelMetadata(
            model_id="gpt-4o-mini",
            display_name="GPT-4o Mini",
            context_window=128_000,
            pricing=ModelPricing(input_cost=0.15, output_cost=0.60),
        ),
        ModelMetadata(
            model_id="o3-mini",
            display_name="o3-mini",
            context_window=200_000,
            pricing=ModelPricing(input_cost=1.10, output_cost=4.40),
        ),
    ]
