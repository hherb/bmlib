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
import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

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

#: Largest count Rust's ``u64`` holds; Python refuses more so both agree.
MAX_WINDOW = 2**64 - 1

#: Ollama keys: the architecture names the prefix of the window's key.
OLLAMA_ARCHITECTURE_KEY = "general.architecture"
OLLAMA_WINDOW_KEY_SUFFIX = ".context_length"


#: A lookup that fails in a way worth retrying (a dropped connection, a 5xx) is
#: tried this many times in all before it is reported.
CONTEXT_LOOKUP_ATTEMPTS = 3

#: Seconds before the first retry; each further retry waits twice as long.
CONTEXT_LOOKUP_BASE_DELAY_SECONDS = 1.0

T = TypeVar("T")


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

    A bool, float, string, zero, negative or beyond-:data:`MAX_WINDOW` number
    is refused (WARNING, quoting the value): a wrong window silently truncates
    or overflows a prompt, so "unknown" is the honest answer.
    """
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_WINDOW:
        return value
    logger.warning(
        "%s/%s: %s is %r, not a token count; window unknown", provider, model, what, value
    )
    return None


def _wrong_type(provider: str, model: str, what: str, value: Any) -> None:
    """Log (WARNING) that ``what`` is not the JSON object it should be."""
    logger.warning(
        "%s/%s: %s is %r, not a JSON object; window unknown", provider, model, what, value
    )


def _field(provider: str, model: str, obj: Any, name: str) -> int | None:
    """Read ``name`` from the object ``obj`` as a count.

    An absent field is unknown (INFO); a wrong-typed ``obj`` or a refused
    value is unknown (WARNING).
    """
    if not isinstance(obj, Mapping):
        _wrong_type(provider, model, "metadata", obj)
        return None
    if name not in obj:
        logger.info("%s/%s: metadata has no %r; window unknown", provider, model, name)
        return None
    return _as_count(provider, model, repr(name), obj[name])


def read_context_window(provider: str, model: str, body: Any) -> int | None:
    """Read ``model``'s context window (tokens) out of a provider's metadata.

    ``body`` is the JSON the provider returned: the model object (anthropic,
    gemini, mistral), the model list (deepseek) or ``/api/show`` (ollama).
    Returns ``None`` when the body does not state a usable window, including
    when ``body``, ``data`` or ``model_info`` is not the JSON type it should
    be. Never raises for the shape of valid JSON.

    Raises:
        NoSuchModelError: deepseek's list has no entry for ``model``.
        ValueError: ``provider`` has no metadata to read.
    """
    if provider in _FIELD_BY_PROVIDER:
        return _field(provider, model, body, _FIELD_BY_PROVIDER[provider])
    if provider == "deepseek":
        data = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(data, list):
            _wrong_type(provider, model, "data", data)
            return None
        # Non-object entries are skipped; with duplicate ids the first wins.
        entry = next((e for e in data if isinstance(e, Mapping) and e.get("id") == model), None)
        if entry is None:
            raise NoSuchModelError(
                f"deepseek has no model {model!r}", provider=provider, model=model
            )
        return _field(provider, model, entry, DEEPSEEK_WINDOW_FIELD)
    if provider == "ollama":
        # ``modelinfo`` is the spelling the ollama client library exposes.
        candidates = (
            [body.get(k) for k in ("model_info", "modelinfo")] if isinstance(body, Mapping) else []
        )
        info = next((c for c in candidates if isinstance(c, Mapping)), None)
        if info is None:
            _wrong_type(provider, model, "model_info", candidates)
            return None
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


def with_backoff(call: Callable[[], T], retry_on: tuple[type[BaseException], ...]) -> T:
    """Call ``call()``, retrying on ``retry_on`` with exponential backoff.

    Makes up to :data:`CONTEXT_LOOKUP_ATTEMPTS` attempts, sleeping
    ``CONTEXT_LOOKUP_BASE_DELAY_SECONDS * 2**attempt`` between them (not after
    the last). The final failure is re-raised as it came; an exception not in
    ``retry_on`` is never retried, because a refusal will not change on a
    second ask.
    """
    for attempt in range(CONTEXT_LOOKUP_ATTEMPTS):
        try:
            return call()
        except retry_on as exc:
            if attempt == CONTEXT_LOOKUP_ATTEMPTS - 1:
                raise
            delay = CONTEXT_LOOKUP_BASE_DELAY_SECONDS * 2**attempt
            logger.warning("context-window lookup failed (%s); retrying in %.1fs", exc, delay)
            time.sleep(delay)
    raise AssertionError("unreachable: the last attempt returns or raises")  # pragma: no cover


def as_dict(obj: Any) -> Any:
    """Turn an SDK model object into the plain dict ``read_context_window`` reads.

    A dict is returned as is; the openai and anthropic SDK models offer
    ``to_dict()``, pydantic models (ollama's ``ShowResponse``) ``model_dump()``.
    Anything else is returned unchanged, so the reader reports it as unknown.
    """
    for method in ("to_dict", "model_dump"):
        convert = getattr(obj, method, None)
        if callable(convert):
            return convert()
    return obj


def fetch_context_window(
    provider: str,
    model: str,
    fetch: Callable[[], Any],
    *,
    not_found: tuple[type[BaseException], ...] = (),
    retry_on: tuple[type[BaseException], ...] = (),
) -> int | None:
    """Fetch a provider's metadata with ``fetch`` and read the window from it.

    ``fetch`` returns the body ``read_context_window`` takes. It is retried
    (see :func:`with_backoff`) only on ``retry_on``. A ``not_found`` exception
    becomes :class:`NoSuchModelError`; any other failure, once retries are
    spent, becomes :class:`ContextWindowError` naming the provider and model.
    Both are chained to the original. A :class:`ContextWindowError` raised by
    ``fetch`` itself passes through.
    """
    try:
        body = with_backoff(fetch, retry_on) if retry_on else fetch()
    except ContextWindowError:
        raise
    except not_found as exc:
        raise NoSuchModelError(
            f"{provider} has no model {model!r}", provider=provider, model=model
        ) from exc
    except Exception as exc:
        raise ContextWindowError(
            f"could not fetch {provider}'s context window for {model!r}: {exc}",
            provider=provider,
            model=model,
        ) from exc
    return read_context_window(provider, model, body)
