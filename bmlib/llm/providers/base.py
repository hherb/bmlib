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

"""Abstract base class for LLM providers.

All LLM providers must inherit from :class:`BaseProvider` and implement the
abstract methods.  This ensures a consistent interface across Anthropic,
Ollama, OpenAI-compatible servers, and any future providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bmlib.llm.data_types import LLMMessage, LLMResponse


@dataclass
class ProviderCapabilities:
    """Describes what a provider can do."""

    supports_streaming: bool = False
    supports_function_calling: bool = False
    supports_vision: bool = False
    supports_system_messages: bool = True
    max_context_window: int = 128_000


@dataclass
class ModelPricing:
    """Cost per million tokens (USD)."""

    input_cost: float = 0.0
    output_cost: float = 0.0


@dataclass
class ModelMetadata:
    """Information about a specific model."""

    model_id: str
    display_name: str
    context_window: int
    pricing: ModelPricing
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    is_deprecated: bool = False


class BaseProvider(ABC):
    """Abstract base class for LLM providers.

    Class attributes to override:
        PROVIDER_NAME   – short identifier (e.g. ``"anthropic"``).
        DISPLAY_NAME    – human-readable label.
        DESCRIPTION     – one-liner.
        WEBSITE_URL     – provider's website.
        SETUP_INSTRUCTIONS – how to get started.
    """

    PROVIDER_NAME: str
    DISPLAY_NAME: str
    DESCRIPTION: str
    WEBSITE_URL: str
    SETUP_INSTRUCTIONS: str

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url or self.default_base_url
        self._client: object = None
        self._extra_config = kwargs

    # --- Provider characteristics (abstract properties) ---

    @property
    @abstractmethod
    def is_local(self) -> bool: ...

    @property
    @abstractmethod
    def is_free(self) -> bool: ...

    @property
    @abstractmethod
    def requires_api_key(self) -> bool: ...

    @property
    def api_key_env_var(self) -> str:
        return ""

    @property
    @abstractmethod
    def default_base_url(self) -> str: ...

    @property
    @abstractmethod
    def default_model(self) -> str: ...

    # --- Core operations ---

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse: ...

    @abstractmethod
    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]: ...

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]: ...

    @abstractmethod
    def count_tokens(self, text: str, model: str | None = None) -> int: ...

    # --- Cost helpers ---

    def get_model_pricing(self, model: str) -> ModelPricing:
        return ModelPricing(input_cost=0.0, output_cost=0.0)

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        pricing = self.get_model_pricing(model)
        return (
            (input_tokens / 1_000_000) * pricing.input_cost
            + (output_tokens / 1_000_000) * pricing.output_cost
        )

    # --- Utility ---

    def get_model_metadata(self, model: str) -> ModelMetadata | None:
        for m in self.list_models():
            if m.model_id == model:
                return m
        return None

    def validate_model(self, model: str) -> bool:
        return any(m.model_id == model for m in self.list_models())

    def format_model_string(self, model: str) -> str:
        return f"{self.PROVIDER_NAME}:{model}"
