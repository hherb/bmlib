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

"""Repair malformed JSON emitted by LLMs.

LLMs frequently produce JSON with predictable syntax errors:

- Missing commas between array elements or object properties
- Trailing commas before closing brackets
- Single quotes instead of double quotes
- Unescaped newlines, tabs, or control characters inside strings
- Truncated output (missing closing brackets)
- Unquoted object keys (JavaScript-style)

This module repairs those issues before parsing. It complements
:func:`bmlib.llm.utils.extract_json` (which only *locates* JSON) by
actually *fixing* it. :class:`bmlib.agents.BaseAgent.parse_json` uses it as
a last-resort fallback.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bmlib.llm.utils import iter_json_spans

logger = logging.getLogger(__name__)

# Configuration constants
MAX_REPAIR_ATTEMPTS: int = 3
MAX_JSON_LENGTH: int = 1_000_000  # 1 MB max


class JSONRepairError(Exception):
    """Raised when JSON cannot be repaired."""


def repair_json(json_str: str, max_attempts: int = MAX_REPAIR_ATTEMPTS) -> str:
    """Attempt to repair malformed JSON from an LLM response.

    Applies a series of fixes for common LLM JSON errors. If the JSON is
    already valid, returns it unchanged.

    Args:
        json_str: Potentially malformed JSON string.
        max_attempts: Maximum repair iterations.

    Returns:
        Repaired JSON string.

    Raises:
        JSONRepairError: If JSON cannot be repaired after all attempts.
        ValueError: If input is empty or too large.
    """
    if not json_str or not json_str.strip():
        raise ValueError("Cannot repair empty JSON string")

    if len(json_str) > MAX_JSON_LENGTH:
        raise ValueError(f"JSON string too large (max {MAX_JSON_LENGTH} bytes)")

    original_str = json_str

    # First, try to parse as-is.
    try:
        json.loads(json_str)
        return json_str  # Already valid
    except json.JSONDecodeError:
        pass  # Need repairs

    repaired = json_str
    # Apply repair strategies iteratively.
    for attempt in range(max_attempts):
        try:
            repaired = _apply_repairs(json_str)
            json.loads(repaired)

            if repaired != original_str:
                logger.debug("JSON repaired successfully on attempt %d", attempt + 1)

            return repaired

        except json.JSONDecodeError as e:
            if attempt < max_attempts - 1:
                # Feed the partially-repaired version into the next iteration.
                json_str = repaired
                logger.debug("JSON repair attempt %d failed: %s, retrying", attempt + 1, e)
            else:
                logger.warning("JSON repair failed after %d attempts: %s", max_attempts, e)
                raise JSONRepairError(
                    f"Cannot repair JSON after {max_attempts} attempts: {e}"
                ) from e

    # Should not reach here, but guard against it.
    raise JSONRepairError("JSON repair failed unexpectedly")


def _apply_repairs(json_str: str) -> str:
    """Apply all repair strategies to a JSON string in dependency order."""
    repairs = [
        _fix_single_quotes,
        _fix_unescaped_newlines,
        _fix_unescaped_tabs,
        _fix_unescaped_control_chars,
        _fix_trailing_commas,
        _fix_missing_commas,
        _fix_truncated_json,
        _fix_unquoted_keys,
    ]

    result = json_str
    for repair_func in repairs:
        try:
            result = repair_func(result)
        except Exception as e:  # noqa: BLE001 — one bad repair must not abort the rest
            logger.debug("Repair function %s failed: %s", repair_func.__name__, e)

    return result


def _fix_single_quotes(json_str: str) -> str:
    """Convert single-quote string delimiters to double quotes.

    Uses a state machine so apostrophes inside double-quoted strings are
    left untouched — only quotes that actually delimit a value convert.
    """
    result: list[str] = []
    in_double_string = False
    in_single_string = False
    # Last non-whitespace character of the input seen so far — tracked
    # incrementally so the opener check below stays O(1) per character.
    prev_nonspace = ""
    i = 0

    while i < len(json_str):
        char = json_str[i]
        prev_char = json_str[i - 1] if i > 0 else ""

        # Pass escape sequences straight through.
        if prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\"):
            result.append(char)
            if not char.isspace():
                prev_nonspace = char
            i += 1
            continue

        if char == '"' and not in_single_string:
            in_double_string = not in_double_string
            result.append(char)
        elif char == "'" and not in_double_string:
            if in_single_string:
                # Closing single quote — convert to double.
                result.append('"')
                in_single_string = False
            else:
                # Treat as a string opener only in a value/key position
                # (after : [ , { or a preceding quote), otherwise it is an
                # apostrophe.
                if prev_nonspace and prev_nonspace in ":,[{'\"":
                    result.append('"')
                    in_single_string = True
                else:
                    result.append(char)
        else:
            result.append(char)

        if not char.isspace():
            prev_nonspace = char
        i += 1

    return "".join(result)


def _fix_unescaped_newlines(json_str: str) -> str:
    """Escape raw newlines/carriage returns that appear inside strings."""
    result: list[str] = []
    in_string = False
    i = 0

    while i < len(json_str):
        char = json_str[i]
        prev_char = json_str[i - 1] if i > 0 else ""
        is_escaped = prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\")

        if char == '"' and not is_escaped:
            in_string = not in_string
            result.append(char)
        elif char == "\n" and in_string:
            result.append("\\n")
        elif char == "\r" and in_string:
            result.append("\\r")
        else:
            result.append(char)

        i += 1

    return "".join(result)


def _fix_unescaped_tabs(json_str: str) -> str:
    """Escape raw tabs that appear inside strings."""
    result: list[str] = []
    in_string = False
    i = 0

    while i < len(json_str):
        char = json_str[i]
        prev_char = json_str[i - 1] if i > 0 else ""
        is_escaped = prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\")

        if char == '"' and not is_escaped:
            in_string = not in_string
            result.append(char)
        elif char == "\t" and in_string:
            result.append("\\t")
        else:
            result.append(char)

        i += 1

    return "".join(result)


def _fix_unescaped_control_chars(json_str: str) -> str:
    """Escape control characters (except tab/newline/cr) inside strings."""
    control_char_pattern = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

    result: list[str] = []
    in_string = False
    i = 0

    while i < len(json_str):
        char = json_str[i]
        prev_char = json_str[i - 1] if i > 0 else ""
        is_escaped = prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\")

        if char == '"' and not is_escaped:
            in_string = not in_string
            result.append(char)
        elif in_string and control_char_pattern.match(char):
            result.append(f"\\u{ord(char):04x}")
        else:
            result.append(char)

        i += 1

    return "".join(result)


def _fix_trailing_commas(json_str: str) -> str:
    """Remove commas that immediately precede a closing bracket/brace."""
    result: list[str] = []
    in_string = False
    i = 0

    while i < len(json_str):
        char = json_str[i]
        prev_char = json_str[i - 1] if i > 0 else ""
        is_escaped = prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\")

        if char == '"' and not is_escaped:
            in_string = not in_string
            result.append(char)
        elif char == "," and not in_string:
            rest = json_str[i + 1 :].lstrip()
            if rest and rest[0] in "]}":
                pass  # Skip the trailing comma.
            else:
                result.append(char)
        else:
            result.append(char)

        i += 1

    return "".join(result)


def _fix_missing_commas(json_str: str) -> str:
    """Insert missing commas between adjacent values, objects, or arrays.

    Handles ``"a" "b"`` → ``"a", "b"``, ``} {`` → ``}, {``, ``] [`` →
    ``], [``, and ``"k": "v" "next":`` → ``"k": "v", "next":``.
    """
    result: list[str] = []
    in_string = False
    # Last non-whitespace character appended so far — tracked incrementally
    # so the value-boundary checks below stay O(1) per character.
    prev_nonspace = ""
    i = 0

    while i < len(json_str):
        char = json_str[i]
        prev_char = json_str[i - 1] if i > 0 else ""
        is_escaped = prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\")

        if char == '"' and not is_escaped:
            if not in_string and prev_nonspace and prev_nonspace in '"}]0123456789':
                close_quote = _find_closing_quote(json_str, i)
                if close_quote > i:
                    # Whether this quoted token is a key or a value, a
                    # separator is missing before it as long as anything
                    # follows its closing quote.
                    j = close_quote + 1
                    while j < len(json_str) and json_str[j].isspace():
                        j += 1
                    if j < len(json_str):
                        result.append(",")

            in_string = not in_string
            result.append(char)

        elif char in "{[" and not in_string:
            if prev_nonspace and prev_nonspace in '"}]0123456789':
                result.append(",")
            result.append(char)

        else:
            result.append(char)

        if not char.isspace():
            prev_nonspace = char
        i += 1

    return "".join(result)


def _find_closing_quote(s: str, start: int) -> int:
    """Return the index of the closing quote for the string opened at *start*.

    Returns ``-1`` if no unescaped closing quote is found.
    """
    i = start + 1
    while i < len(s):
        if s[i] == '"':
            num_backslashes = 0
            j = i - 1
            while j >= start + 1 and s[j] == "\\":
                num_backslashes += 1
                j -= 1
            if num_backslashes % 2 == 0:
                return i
        i += 1
    return -1


def _fix_truncated_json(json_str: str) -> str:
    """Close truncated JSON by appending the missing brackets/braces."""
    open_braces = 0
    open_brackets = 0
    in_string = False

    for i, char in enumerate(json_str):
        prev_char = json_str[i - 1] if i > 0 else ""
        is_escaped = prev_char == "\\" and not (i >= 2 and json_str[i - 2] == "\\")

        if char == '"' and not is_escaped:
            in_string = not in_string
        elif not in_string:
            if char == "{":
                open_braces += 1
            elif char == "}":
                open_braces -= 1
            elif char == "[":
                open_brackets += 1
            elif char == "]":
                open_brackets -= 1

    result = json_str.rstrip()

    # Close an unterminated string first.
    if in_string:
        result += '"'

    # Drop a dangling comma before appending closers.
    result = result.rstrip(",")

    result += "]" * open_brackets
    result += "}" * open_braces

    return result


def _fix_unquoted_keys(json_str: str) -> str:
    """Quote JavaScript-style unquoted object keys, skipping string contents."""
    pattern = r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)"

    def quote_key(match: re.Match) -> str:
        return f'{match.group(1)}"{match.group(2)}"{match.group(3)}'

    # Locate string ranges so the substitution only touches structure.
    string_ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(json_str):
        if json_str[i] == '"':
            if i == 0 or json_str[i - 1] != "\\":
                start = i
                end = _find_closing_quote(json_str, i)
                if end > 0:
                    string_ranges.append((start, end))
                    i = end + 1
                    continue
        i += 1

    result: list[str] = []
    last_end = 0
    for start, end in string_ranges:
        portion = re.sub(pattern, quote_key, json_str[last_end:start])
        result.append(portion)
        result.append(json_str[start : end + 1])
        last_end = end + 1

    if last_end < len(json_str):
        result.append(re.sub(pattern, quote_key, json_str[last_end:]))

    return "".join(result)


def safe_json_loads(
    json_str: str, repair: bool = True, max_attempts: int = MAX_REPAIR_ATTEMPTS
) -> Any:
    """Parse JSON, optionally repairing malformed input first.

    Args:
        json_str: JSON string to parse.
        repair: Whether to attempt repair on parse failure.
        max_attempts: Maximum repair attempts when *repair* is true.

    Returns:
        Parsed JSON data (dict, list, or primitive).

    Raises:
        ValueError: If the JSON cannot be parsed (even after repair).
    """
    if not json_str or not json_str.strip():
        raise ValueError("Cannot parse empty JSON string")

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        if not repair:
            raise ValueError(f"Invalid JSON: {e}") from e

    try:
        repaired = repair_json(json_str, max_attempts)
        return json.loads(repaired)
    except (JSONRepairError, json.JSONDecodeError) as e:
        raise ValueError(f"Cannot parse JSON even after repair: {e}") from e


def extract_and_repair_json(response: str, repair: bool = True) -> tuple[str, bool]:
    """Extract a JSON string from an LLM response and optionally repair it.

    Handles pure JSON, JSON in markdown code blocks, and JSON embedded in
    explanatory prose. Walks candidate JSON spans in priority order: the
    first candidate that validates (or repairs, if enabled) is returned.

    Args:
        response: Raw LLM response string.
        repair: Whether to attempt repair on malformed candidates.

    Returns:
        Tuple of ``(extracted_json_string, was_repaired)``.

    Raises:
        ValueError: If no JSON can be found or repaired in *response*.
    """
    if not response or not response.strip():
        raise ValueError("Cannot extract JSON from empty response")

    last_error: Exception | None = None

    for candidate in iter_json_spans(response.strip()):
        try:
            json.loads(candidate)
            return candidate, False
        except json.JSONDecodeError as e:
            last_error = e

        if not repair:
            raise ValueError(f"Invalid JSON: {last_error}") from last_error

        try:
            repaired = repair_json(candidate)
            json.loads(repaired)  # Validate it parses.
            return repaired, True
        except (JSONRepairError, json.JSONDecodeError) as e:
            # This candidate is a dead end; the next one may not be.
            last_error = e

    if last_error is None:
        raise ValueError("No JSON found in response")
    raise ValueError(f"Cannot parse extracted JSON: {last_error}")
