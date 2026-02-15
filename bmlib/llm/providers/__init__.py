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

"""LLM provider registry.

Providers are registered by name and lazily instantiated.  New providers
can be added at runtime via :func:`register_provider`.
"""

from __future__ import annotations

from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)

__all__ = [
    "BaseProvider",
    "ModelMetadata",
    "ModelPricing",
    "ProviderCapabilities",
    "get_provider",
    "list_providers",
    "register_provider",
]

# Registry: provider name → class
_REGISTRY: dict[str, type[BaseProvider]] = {}


def register_provider(name: str, cls: type[BaseProvider]) -> None:
    """Register a provider class under *name*."""
    _REGISTRY[name] = cls


def list_providers() -> list[str]:
    """Return names of all registered providers."""
    _ensure_builtins()
    return list(_REGISTRY.keys())


def get_provider(name: str, **kwargs: object) -> BaseProvider:
    """Instantiate and return a provider by name.

    Raises :class:`ValueError` if the provider is not registered and
    its built-in module cannot be imported.
    """
    _ensure_builtins()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown provider {name!r}. Available: {list(_REGISTRY.keys())}"
        )
    return cls(**kwargs)


def _ensure_builtins() -> None:
    """Lazily register built-in providers on first access."""
    if _REGISTRY:
        return

    # Anthropic
    try:
        from bmlib.llm.providers.anthropic import AnthropicProvider
        _REGISTRY["anthropic"] = AnthropicProvider
    except ImportError:
        pass

    # Ollama
    try:
        from bmlib.llm.providers.ollama import OllamaProvider
        _REGISTRY["ollama"] = OllamaProvider
    except ImportError:
        pass

    # OpenAI
    try:
        from bmlib.llm.providers.openai_provider import OpenAIProvider
        _REGISTRY["openai"] = OpenAIProvider
    except ImportError:
        pass

    # DeepSeek
    try:
        from bmlib.llm.providers.deepseek import DeepSeekProvider
        _REGISTRY["deepseek"] = DeepSeekProvider
    except ImportError:
        pass

    # Mistral
    try:
        from bmlib.llm.providers.mistral import MistralProvider
        _REGISTRY["mistral"] = MistralProvider
    except ImportError:
        pass

    # Gemini
    try:
        from bmlib.llm.providers.gemini import GeminiProvider
        _REGISTRY["gemini"] = GeminiProvider
    except ImportError:
        pass
