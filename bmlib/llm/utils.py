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
from collections.abc import Callable, Iterator

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


def _iter_balanced(text: str, openers: str) -> Iterator[str]:
    """Yield each outermost balanced span, in document order.

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
                yield text[start : i + 1]
                start = -1


def iter_json_spans(text: str, *, nested_objects: bool = True) -> Iterator[str]:
    """Yield candidate JSON spans from *text*, best first, without validating.

    Callers apply their own acceptance policy — validate, or validate-or-repair
    — by walking the candidates in order.  Stages, in priority order:

    1. ```json fenced bodies, in document order.
    2. Other fenced bodies that start with ``{`` or ``[``.
    3. The remaining fenced bodies.
    4. Balanced ``{...}``/``[...]`` spans, in document order.
    5. Brace-only balanced spans not already yielded, so an object nested in
       an array is still offered as a candidate. Skipped if *nested_objects*
       is False.
    6. The text from the first opener to the end — only when nothing balanced,
       which is what truncated model output looks like.

    No span is yielded twice, compared by text rather than by position.  The
    stages overlap heavily — stages 4 and 5 rescan fence interiors as plain
    text, so every fenced body reaches the balanced scan a second time — and
    a repeated candidate is pure waste to the caller: an identical string
    parses and repairs identically, so re-offering it only buys a second run
    of :func:`~bmlib.llm.json_repair.repair_json`'s attempt loop on a span
    that has already failed.

    Args:
        text: The text to scan for JSON spans.
        nested_objects: When False, stage 5 is skipped, so an object nested
            inside a larger span is not offered as a separate candidate.
            Consumers that repair a candidate want this off — repairing a
            nested fragment silently discards the structure around it.
            The default is True because that is what the parameter name reads
            as, but note the imbalance: every caller in bmlib passes it
            explicitly, and only :func:`extract_json`'s last-resort second
            walk passes True.
    """
    if not text:
        return

    fences = [(lang, body.strip()) for lang, body in _FENCE_RE.findall(text)]
    taken: set[int] = set()
    yielded: set[str] = set()

    for stage in ("json", "jsonish", "rest"):
        for index, (lang, body) in enumerate(fences):
            if index in taken or not body:
                continue
            if stage == "json" and lang != "json":
                continue
            if stage == "jsonish" and not body.startswith(("{", "[")):
                continue
            taken.add(index)
            if body not in yielded:
                yielded.add(body)
                yield body

    balanced_found = False
    passes = ("{[", "{") if nested_objects else ("{[",)
    for openers in passes:
        for span in _iter_balanced(text, openers):
            # Set before the dedup check: a span that repeats one already
            # yielded still means the text balanced, so stage 6 must not fire.
            balanced_found = True
            if span not in yielded:
                yielded.add(span)
                yield span

    if not balanced_found:
        first = _first_opener(text)
        if first >= 0:
            tail = text[first:]
            if tail not in yielded:
                yield tail


def extract_json(text: str, *, allow_fragments: bool = True) -> str:
    """Extract a JSON span from text that may contain prose or code blocks.

    Applies :func:`_first_acceptable` to the candidates from
    :func:`iter_json_spans` twice: once over whole spans only, and — if
    nothing there parsed — once more with the nested-object stage enabled.
    Returns *text* unchanged when nothing parses at all.

    A fenced candidate wins on parse alone, ahead of dict preference: a fence
    is the model's own delimitation of its answer, so a fenced JSON array
    must not be reduced to an object plucked from inside it by a later,
    unfenced stage (stages 4/5 rescan fence interiors as plain text). Without
    a fence, the dict preference still applies to *top-level* spans — callers
    here — the Anthropic and OpenAI-compatible providers' ``json_mode`` path,
    and :meth:`bmlib.agents.BaseAgent.parse_json` — want the object a model
    was asked for, not an incidental array that happens to appear earlier.

    An object reachable *only* from inside another span is a last resort, not
    a preference. Preferring it reduced an array of objects to its first
    element and dropped every sibling with no error anywhere.

    Args:
        text: The text to extract a JSON span from.
        allow_fragments: When False, the second walk is skipped, so *text*
            comes back unchanged rather than an object dug out of the inside
            of a span. A caller that can *repair* has something better to try
            than a fragment: for a truncated ``'[{"a": 1}, {"b": 2}'`` the
            fragment is only the first object, while repair closes the bracket
            and recovers both. :meth:`bmlib.agents.BaseAgent.parse_json`
            passes False for exactly that reason and re-asks with the default
            once repair has also failed. The providers' ``json_mode`` path
            takes the default: it has no repair stage, so a fragment is the
            best it can do.
    """
    # Built on first need rather than up front: it costs a second pass of
    # _FENCE_RE over the whole response, and the common case — the first
    # candidate parses to a dict — never consults it.  This runs on every
    # json_mode response from the Anthropic and OpenAI-compatible providers.
    # Hoisted into a closure shared by both walks so it is built at most once
    # overall rather than once per walk.
    fenced: set[str] | None = None

    def fences() -> set[str]:
        nonlocal fenced
        if fenced is None:
            fenced = {body.strip() for _, body in _FENCE_RE.findall(text)}
        return fenced

    whole = _first_acceptable(text, fences, nested_objects=False)
    if whole is not None:
        return whole
    if not allow_fragments:
        return text

    # Nothing at the top level parsed.  Only now is an object dug out of the
    # inside of a span worth having.  The stage 1-4 candidates the first walk
    # rejected are re-offered and still fail, so every value this walk can
    # return comes from the brace-only pass and is therefore a dict.
    fragment = _first_acceptable(text, fences, nested_objects=True)
    return fragment if fragment is not None else text


def _first_acceptable(
    text: str, fences: Callable[[], set[str]], *, nested_objects: bool
) -> str | None:
    """Fence, then dict, then the best non-dict span; None when nothing parses.

    The non-dict fallback is *ranked*, not first-parseable: a span that is a
    list holding at least one object beats any other non-dict span.  Without
    the ranking, an incidental parseable span earlier in the response — an
    empty ``[]``, a list of strings — would be accepted by the first walk, so
    the second walk would never run and the payload nested in a later array
    would be substituted by unrelated data that parses cleanly and survives
    every downstream shape check.

    Args:
        text: The text to scan.
        fences: Memoised accessor for the set of fenced bodies in *text*,
            shared across both of :func:`extract_json`'s walks.
        nested_objects: Passed through to :func:`iter_json_spans`.
    """
    fallback: str | None = None
    with_objects: str | None = None

    for candidate in iter_json_spans(text, nested_objects=nested_objects):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, RecursionError):
            # RecursionError, not just a decode failure: json.loads() descends
            # recursively, so a candidate nested past the interpreter's stack
            # limit — '{"j": ' * 20000, what a repetition-looping model emits —
            # blows the stack.  extract_json() is documented never to raise and
            # runs unconditionally on every json_mode response, so an
            # undecodable candidate is skipped like any other.
            continue
        if isinstance(parsed, dict):
            return candidate
        if candidate in fences():
            return candidate
        if with_objects is None and isinstance(parsed, list):
            if any(isinstance(item, dict) for item in parsed):
                with_objects = candidate
        if fallback is None:
            fallback = candidate

    return with_objects if with_objects is not None else fallback
