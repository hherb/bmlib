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

"""Shared utility functions for LLM providers."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

# Fence opener, optional language tag, optional trailing newline, then the
# body up to the closing fence.  ``(.*?)`` is non-greedy so consecutive
# fences do not merge into one block.
_FENCE_RE = re.compile(r"```(\w*)[ \t]*\r?\n?(.*?)```", re.DOTALL)

# Which closer ends a span opened by which opener.
_CLOSERS = {"{": "}", "[": "]"}


def _first_opener(text: str, openers: str = "{[") -> int:
    """Find the index of the first opener outside any quoted string.

    Honoring backslash escapes, so an escaped quote does not end a string.
    Returns -1 if no opener is found outside a string.
    """
    in_str = False
    escape = False

    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch in openers:
            return i

    return -1


def _iter_balanced(text: str, openers: str) -> Iterator[tuple[int, str]]:
    """Yield ``(start_index, span)`` for each outermost balanced span.

    Only the pair type of a span's *own* opener is counted, so a ``[`` span
    is not disturbed by the braces nested inside it.  Quoted strings — and
    escapes within them — are honoured, so a brace inside a string value
    never affects nesting.  A span that never balances ends the scan: any
    later opener is nested inside it, not a sibling.
    """
    in_str = False
    escape = False
    depth = 0
    start = -1
    opener = ""
    closer = ""

    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif depth == 0:
            if ch in openers:
                opener, closer = ch, _CLOSERS[ch]
                start = i
                depth = 1
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                yield start, text[start : i + 1]
                start = -1


def iter_json_spans(text: str) -> Iterator[str]:
    """Yield candidate JSON spans from *text*, best first, without validating.

    Callers apply their own acceptance policy — validate, or validate-or-repair
    — by walking the candidates in order.  Stages, in priority order:

    1. ```json fenced bodies, in document order.
    2. Other fenced bodies that start with ``{`` or ``[``.
    3. The remaining fenced bodies.
    4. Balanced ``{...}``/``[...]`` spans, in document order.
    5. Brace-only balanced spans not already yielded, so an object nested in
       an array is still offered as a candidate.
    6. The text from the first opener to the end — only when nothing balanced,
       which is what truncated model output looks like.
    """
    if not text:
        return

    fences = [(lang, body.strip()) for lang, body in _FENCE_RE.findall(text)]
    taken: set[int] = set()

    for stage in ("json", "jsonish", "rest"):
        for index, (lang, body) in enumerate(fences):
            if index in taken or not body:
                continue
            if stage == "json" and lang != "json":
                continue
            if stage == "jsonish" and not body.startswith(("{", "[")):
                continue
            taken.add(index)
            yield body

    seen: set[tuple[int, int]] = set()
    balanced_found = False
    for openers in ("{[", "{"):
        for start, span in _iter_balanced(text, openers):
            balanced_found = True
            key = (start, len(span))
            if key in seen:
                continue
            seen.add(key)
            yield span

    if not balanced_found:
        first = _first_opener(text)
        if first >= 0:
            yield text[first:]


def _iter_balanced_objects(text: str):
    """Yield substrings of *text* that are balanced ``{...}`` blocks.

    Brace counting respects string literals (and escaped quotes) so braces
    inside JSON string values do not affect nesting. Each top-level object is
    yielded in order, allowing the caller to pick the first that parses.
    """
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    yield text[start : i + 1]


def extract_json(text: str) -> str:
    """Extract JSON from text that may contain markdown code blocks.

    Tries code-block extraction first, then scans for the first balanced
    ``{...}`` object that parses as JSON. Returns the original *text*
    unchanged if no JSON can be found.
    """
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block_match:
        candidate = code_block_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Scan for the first balanced object that actually parses. A greedy
    # ``\{.*\}`` would span from the first "{" to the last "}", swallowing
    # prose between two separate objects and failing to parse.
    for candidate in _iter_balanced_objects(text):
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue

    return text
