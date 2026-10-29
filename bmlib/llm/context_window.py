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

"""The context windows bmlib knows by heart.

:data:`KNOWN_CONTEXT_WINDOWS` maps ``(provider, model)`` to a window in
tokens. Every row is a figure read off the provider's own model page;
``tests/fixtures/context_window.json`` is the record of each row's source and
read date, and both this table and the Rust port
(``bmlib::llm::context_window``) are tested equal to it. Keys match exactly:
a dated snapshot id or a different case is *unknown*, not guessed, because a
wrong window silently truncates or overflows a prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

KNOWN_CONTEXT_WINDOWS: Mapping[tuple[str, str], int] = {
    ("openai", "gpt-6-astra"): 1_050_000,
    ("openai", "gpt-6.1-sol"): 1_050_000,
    ("openai", "gpt-6.1-terra"): 1_050_000,
    ("openai", "gpt-6-luna"): 1_050_000,
    ("openai", "gpt-4o-mini"): 128_000,
}


def known_context_window(provider: str, model: str) -> int | None:
    """Return the context window (tokens) for an exact ``(provider, model)``.

    Returns ``None`` when the pair is not in :data:`KNOWN_CONTEXT_WINDOWS`;
    there is no prefix, case-folding or suffix matching.
    """
    return KNOWN_CONTEXT_WINDOWS.get((provider, model))


#: Providers whose metadata names the window, with the body field that holds
#: it. Ollama and DeepSeek need more than one field and are handled apart.
_FIELD_BY_PROVIDER: Mapping[str, str] = {
    "anthropic": "max_input_tokens",
    "gemini": "inputTokenLimit",
    "mistral": "max_context_length",
}

#: DeepSeek lists every model in one body; this is the per-model field.
DEEPSEEK_WINDOW_FIELD = "context_window"

#: Ollama keys: the architecture names the prefix of the window's key.
OLLAMA_ARCHITECTURE_KEY = "general.architecture"
OLLAMA_WINDOW_KEY_SUFFIX = ".context_length"


class ContextWindowError(RuntimeError):
    """A context window could not be fetched or read.

    Carries the ``provider`` and ``model`` so a caller can report which lookup
    failed without parsing the message.
    """

    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class NoSuchModelError(ContextWindowError):
    """The provider has no model with this name (as opposed to: no window)."""


def _as_count(provider: str, model: str, what: str, value: Any) -> int | None:
    """Return ``value`` if it is a positive integer count, else ``None``.

    A bool, float, string, zero or negative number is refused (WARNING, quoting
    the value): a wrong window silently truncates or overflows a prompt, so
    "unknown" is the honest answer.
    """
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    logger.warning(
        "%s/%s: %s is %r, not a token count; window unknown", provider, model, what, value
    )
    return None


def _field(provider: str, model: str, body: Mapping[str, Any], name: str) -> int | None:
    """Read ``name`` from ``body`` as a count; absent is unknown (INFO)."""
    if name not in body:
        logger.info("%s/%s: metadata has no %r; window unknown", provider, model, name)
        return None
    return _as_count(provider, model, repr(name), body[name])


def read_context_window(provider: str, model: str, body: Mapping[str, Any]) -> int | None:
    """Read ``model``'s context window (tokens) out of a provider's metadata.

    ``body`` is the JSON the provider returned: the model object (anthropic,
    gemini, mistral), the model list (deepseek) or ``/api/show`` (ollama).
    Returns ``None`` when the body does not state a usable window.

    Raises:
        NoSuchModelError: deepseek's list has no entry for ``model``.
        ValueError: ``provider`` has no metadata to read.
    """
    if provider in _FIELD_BY_PROVIDER:
        return _field(provider, model, body, _FIELD_BY_PROVIDER[provider])
    if provider == "deepseek":
        entry = next((e for e in body.get("data") or [] if e.get("id") == model), None)
        if entry is None:
            raise NoSuchModelError(
                f"deepseek has no model {model!r}", provider=provider, model=model
            )
        return _field(provider, model, entry, DEEPSEEK_WINDOW_FIELD)
    if provider == "ollama":
        # ``modelinfo`` is the spelling the ollama client library exposes.
        info = body.get("model_info") or body.get("modelinfo") or {}
        arch = info.get(OLLAMA_ARCHITECTURE_KEY)
        if not isinstance(arch, str):
            logger.info(
                "%s/%s: metadata has no %r; window unknown",
                provider,
                model,
                OLLAMA_ARCHITECTURE_KEY,
            )
            return None
        return _field(provider, model, info, arch + OLLAMA_WINDOW_KEY_SUFFIX)
    raise ValueError(f"{provider!r} has no model metadata to read a context window from")
