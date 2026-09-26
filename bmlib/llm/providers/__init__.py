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

import importlib
import sys
from importlib.util import find_spec
from typing import Any

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

# Tracks whether the built-in providers have been registered. A dedicated flag
# (rather than testing ``_REGISTRY`` truthiness) is required so that a custom
# provider registered via :func:`register_provider` before any lookup does not
# make ``_ensure_builtins`` believe the built-ins are already present.
_builtins_registered: bool = False


# Built-in providers: name → (module, class, SDK module, extra).  Every
# provider module imports its SDK lazily, inside ``_get_client()``, so
# importing the module proves nothing about the SDK — registration probes the
# SDK itself (#303).  The four OpenAI-compatible providers share one SDK.
_BUILTIN_PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "anthropic": ("bmlib.llm.providers.anthropic", "AnthropicProvider", "anthropic", "anthropic"),
    "ollama": ("bmlib.llm.providers.ollama", "OllamaProvider", "ollama", "ollama"),
    "openai": ("bmlib.llm.providers.openai_provider", "OpenAIProvider", "openai", "openai"),
    "deepseek": ("bmlib.llm.providers.deepseek", "DeepSeekProvider", "openai", "openai"),
    "mistral": ("bmlib.llm.providers.mistral", "MistralProvider", "openai", "openai"),
    "gemini": ("bmlib.llm.providers.gemini", "GeminiProvider", "openai", "openai"),
}


def _normalise_name(name: str) -> str:
    """The registry's spelling of a provider name: stripped, lowercase.

    :class:`~bmlib.llm.client.LLMClient` lowercases the provider of a
    ``"provider:model"`` string, so a name kept in any other case could be
    listed but never routed to.
    """
    return name.strip().lower()


def register_provider(name: str, cls: type[BaseProvider]) -> None:
    """Register a provider class under *name*, case-insensitively.

    The built-ins are registered first, so registering one of their names —
    even before any lookup — overrides it rather than being overwritten by
    the first lookup's lazy registration.
    """
    _ensure_builtins()
    _REGISTRY[_normalise_name(name)] = cls


def list_providers() -> list[str]:
    """Return names of all registered providers.

    A built-in whose SDK is not installed is absent, so the list answers
    "what can this installation use", not "what does bmlib support".
    """
    _ensure_builtins()
    return list(_REGISTRY.keys())


def get_provider(name: str, **kwargs: Any) -> BaseProvider:
    """Instantiate and return a provider by name, case-insensitively.

    Raises:
        ImportError: If *name* is a built-in whose SDK is not installed —
            naming the extra that provides it, since "unknown provider" would
            be a false diagnosis.
        ValueError: If no provider is registered under *name*.
    """
    _ensure_builtins()
    key = _normalise_name(name)
    cls = _REGISTRY.get(key)
    if cls is None:
        builtin = _BUILTIN_PROVIDERS.get(key)
        if builtin is not None:
            _, _, sdk, extra = builtin
            raise ImportError(
                f"Provider {key!r} needs the {sdk!r} package, which is not installed. "
                f"Install with: pip install bmlib[{extra}]"
            )
        raise ValueError(f"Unknown provider {name!r}. Available: {list(_REGISTRY.keys())}")
    # `**kwargs: Any`, not `object`, because the bag is *splatted* into a
    # typed signature below. `object` is the stricter annotation and reads
    # like the safer one, but it makes this call uncheckable rather than
    # checked: a constructor parameter declared `str | None` cannot accept
    # an `object`, so every provider here would be an error. Bags that are
    # only inspected or forwarded untyped keep `object`.
    return cls(**kwargs)


def _ensure_builtins() -> None:
    """Lazily register built-in providers on first access.

    The flag is set only after registration succeeds: a failure part-way
    through must not be latched, or every later lookup would silently resolve
    without the built-ins. Registration is idempotent, so a retry after a
    transient failure simply re-registers.
    """
    global _builtins_registered
    if _builtins_registered:
        return
    _register_builtins()
    _builtins_registered = True


def _sdk_installed(module: str) -> bool:
    """Whether *module* can be imported, without importing it.

    A module already in :data:`sys.modules` counts before the finder is
    asked: :func:`~importlib.util.find_spec` raises ``ValueError`` for one
    whose ``__spec__`` is ``None`` — a stub a test or an application put
    there — and that module imports perfectly well.
    """
    if sys.modules.get(module) is not None:
        return True
    # A ``None`` entry is the interpreter's own import block, and find_spec
    # answers ``None`` for it — so only a real module short-circuits above.
    return find_spec(module) is not None


def _register_builtins() -> None:
    """Register every built-in provider whose SDK is installed.

    The SDK is probed with :func:`importlib.util.find_spec` rather than
    imported: an import is what ``_get_client()`` does on first use, and
    doing it here would load every installed SDK on the first lookup.  The
    provider modules themselves are bmlib's own and import only the
    standard library, so a failure importing one is a defect and propagates
    — it is not the "SDK missing" case this function exists to skip.
    """
    for name, (module, class_name, sdk, _extra) in _BUILTIN_PROVIDERS.items():
        if _sdk_installed(sdk):
            _REGISTRY[name] = getattr(importlib.import_module(module), class_name)
