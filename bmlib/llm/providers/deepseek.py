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

"""DeepSeek provider — DeepSeek models via OpenAI-compatible API."""

from __future__ import annotations

from bmlib.llm.providers.base import ModelMetadata, ModelPricing
from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek models via OpenAI-compatible API."""

    PROVIDER_NAME = "deepseek"
    DISPLAY_NAME = "DeepSeek"
    DESCRIPTION = "DeepSeek models (DeepSeek-V3, R1)"
    WEBSITE_URL = "https://platform.deepseek.com"
    SETUP_INSTRUCTIONS = "Get API key from platform.deepseek.com/api_keys"

    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    MODEL_PRICING = {
        "deepseek-chat": ModelPricing(input_cost=0.27, output_cost=1.10),
        "deepseek-reasoner": ModelPricing(input_cost=0.55, output_cost=2.19),
    }

    FALLBACK_MODELS = [
        ModelMetadata(
            model_id="deepseek-chat",
            display_name="DeepSeek-V3 (Chat)",
            context_window=64_000,
            pricing=ModelPricing(input_cost=0.27, output_cost=1.10),
        ),
        ModelMetadata(
            model_id="deepseek-reasoner",
            display_name="DeepSeek-R1 (Reasoner)",
            context_window=64_000,
            pricing=ModelPricing(input_cost=0.55, output_cost=2.19),
        ),
    ]
