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

"""JSON as the Rust port's ``serde_json`` reads it (private, stdlib only).

Python's ``json`` reads ``NaN``, ``Infinity`` and ``-Infinity``, reads
``1e999`` as ``inf``, keeps an integer of any size (up to ``int()``'s 4,300
digits), and accepts a lone surrogate escape and any nesting the stack allows.
``serde_json`` (1.0.151, the version ``rust/Cargo.lock`` pins) refuses all of
them, so a body one language reads the other refused. Shared by
:mod:`bmlib.llm.utils`, which re-exports it for the ``chat_json`` path (#572),
and :mod:`bmlib.publications.retraction_status`, which must not import
:mod:`bmlib.llm`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

#: serde_json's nesting limit: 127 levels of arrays or objects parse, 128 do not.
SERDE_MAX_DEPTH = 127

_REFUSALS: ContextVar[list[str] | None] = ContextVar("bmlib_json_refusals", default=None)


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON value")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"number out of range: {text[:40]}")
    return value


def _float_ranged_int(text: str) -> int:
    value = int(text)  # past 4,300 digits int() itself raises ValueError
    try:
        float(value)
    except OverflowError:
        raise ValueError(f"number out of range: {text[:40]}...") from None
    return value


def _encodable(text: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("a lone surrogate escape is not a Unicode string") from None


def check_serde_parity(value: Any, depth: int = 0) -> None:
    """Refuse what decoded here and ``serde_json`` refuses: deep nesting, lone surrogates.

    Raises:
        ValueError: For a string or key holding a lone surrogate, or arrays
            and objects nested deeper than :data:`SERDE_MAX_DEPTH`.
    """
    if isinstance(value, str):
        _encodable(value)
    elif isinstance(value, dict | list):
        if depth == SERDE_MAX_DEPTH:
            raise ValueError(f"nested deeper than {SERDE_MAX_DEPTH} levels")
        for key in value if isinstance(value, dict) else ():
            _encodable(key)
        for child in value.values() if isinstance(value, dict) else value:
            check_serde_parity(child, depth + 1)


def strict_json_decoder() -> json.JSONDecoder:
    """A fresh decoder refusing the numbers :func:`strict_json_loads` refuses.

    For :meth:`~json.JSONDecoder.raw_decode`, which reports a refusal as the
    hook's :class:`ValueError` (a ``JSONDecodeError`` is one too). It does not
    walk the result: call :func:`check_serde_parity` on what it returns.
    """
    return json.JSONDecoder(
        parse_constant=_refuse_constant,
        parse_float=_finite_float,
        parse_int=_float_ranged_int,
    )


@contextmanager
def watch_refusals() -> Iterator[list[str]]:
    """Collect the reason of every refusal :func:`strict_json_loads` makes in the block.

    A refusal is not a syntax error: the text was JSON-shaped and held
    something JSON does not define. A caller that falls back to another span
    of the same text when a decode fails asks this, so a value picked around a
    refused one is not handed back as the answer (#572's review). Nested
    blocks each see their own refusals, and the enclosing block sees them too.
    """
    outer = _REFUSALS.get()
    seen: list[str] = []
    token = _REFUSALS.set(seen)
    try:
        yield seen
    finally:
        _REFUSALS.reset(token)
        if outer is not None:
            outer.extend(seen)


def strict_json_loads(text: str) -> Any:
    """:func:`json.loads` reading only what RFC 8259 and ``serde_json`` read.

    Refuses ``NaN``, ``Infinity``, ``-Infinity``, a float that overflows to
    infinity, an integer past the binary64 range, a lone surrogate escape and
    nesting deeper than :data:`SERDE_MAX_DEPTH`, as ``serde_json`` does. A
    number that underflows (``1e-999``) reads as ``0.0`` in both, and an
    integer inside the binary64 range stays an exact ``int`` here, where Rust
    holds one outside ``i64``/``u64`` as a float.

    Nesting deep enough to exhaust the interpreter's stack (the decoder
    recurses) is refused the same way, never a ``RecursionError`` (#758).

    Raises:
        json.JSONDecodeError: For every refusal, so each caller's existing
            ``except json.JSONDecodeError`` catches these too. A refusal's
            position is 0, not the refused value's: the hooks are not told
            where they are.
    """
    try:
        value = strict_json_decoder().decode(text)
        check_serde_parity(value)
    except json.JSONDecodeError:
        raise
    except (ValueError, RecursionError) as exc:
        reason = "nested too deeply to decode" if isinstance(exc, RecursionError) else str(exc)
        seen = _REFUSALS.get()
        if seen is not None:
            seen.append(reason)
        raise json.JSONDecodeError(reason, text, 0) from exc
    return value
