"""LLM provider registry.

Providers are registered by name and lazily instantiated.  New providers
can be added at runtime via :func:`register_provider`.
"""

from __future__ import annotations

from typing import Type

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
_REGISTRY: dict[str, Type[BaseProvider]] = {}


def register_provider(name: str, cls: Type[BaseProvider]) -> None:
    """Register a provider class under *name*."""
    _REGISTRY[name] = cls


def list_providers() -> list[str]:
    """Return names of all registered providers."""
    _ensure_builtins()
    return list(_REGISTRY.keys())


def get_provider(name: str, **kwargs) -> BaseProvider:
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
