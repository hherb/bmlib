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
