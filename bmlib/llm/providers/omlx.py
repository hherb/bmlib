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

"""oMLX provider — local MLX models on Apple silicon via oMLX's OpenAI-compatible API."""

from __future__ import annotations

from bmlib.llm.context_window import ModelLimits, as_dict, fetch_model_limits
from bmlib.llm.providers.base import ModelPricing
from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider

_FREE_PRICING = ModelPricing(input_cost=0.0, output_cost=0.0)


class OmlxProvider(OpenAICompatibleProvider):
    """Models served by a local oMLX server (no key, no cost)."""

    PROVIDER_NAME = "omlx"
    DISPLAY_NAME = "oMLX"
    DESCRIPTION = "Local MLX models on Apple silicon"
    WEBSITE_URL = "https://github.com/jundot/omlx"
    SETUP_INSTRUCTIONS = "Install and start oMLX; it serves on 127.0.0.1:8000"

    DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
    # oMLX names no default model; this is one worth trying first (as in Rust).
    DEFAULT_MODEL = "Inkling-2-Mini-oQ4e-mtp"

    _FALLBACK_PRICING = _FREE_PRICING

    @property
    def is_local(self) -> bool:
        """oMLX runs on this machine."""
        return True

    @property
    def is_free(self) -> bool:
        """A local model costs nothing per token."""
        return True

    @property
    def requires_api_key(self) -> bool:
        """A local oMLX server needs no key."""
        return False

    def model_limits(self, model: str) -> ModelLimits:
        """The model's ``max_model_len``, from the one-body list (no output limit).

        oMLX has no per-model endpoint, so this lists them all. The openai SDK
        retries transient failures itself, so no backoff here.

        Raises:
            NoSuchModelError: The list has no such model.
            ContextWindowError: The request failed.
        """
        client = self._get_client()
        return fetch_model_limits(
            self.PROVIDER_NAME,
            model,
            lambda: {"data": [as_dict(m) for m in client.models.list()]},
        )
