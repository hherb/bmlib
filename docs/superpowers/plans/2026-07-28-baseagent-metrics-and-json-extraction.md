# BaseAgent Metrics + Consolidated JSON Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `BaseAgent` per-agent performance metrics, embeddings and a connection test, and collapse bmlib's two hand-rolled JSON extractors onto a single shared span locator (issue #17).

**Architecture:** A new `bmlib/agents/metrics.py` holds a thread-safe `PerformanceMetrics` dataclass that `BaseAgent` accumulates into; it is independent of the global `TokenTracker`. A new `iter_json_spans()` in `bmlib/llm/utils.py` yields candidate JSON spans in priority order without validating, and both `extract_json()` and `extract_and_repair_json()` become one-line policies over it. `salvage_json_fields()` is added to `bmlib/llm/json_repair.py` as an opt-in last resort for documents that will not parse at all.

**Tech Stack:** Python ≥3.11, stdlib only (`json`, `re`, `threading`, `time`), pytest, ruff.

**Spec:** [`docs/superpowers/specs/2026-07-28-baseagent-metrics-and-json-extraction-design.md`](../specs/2026-07-28-baseagent-metrics-and-json-extraction-design.md)

## Global Constraints

- **AGPL-3 header** at the top of every source file. Copy verbatim from any existing file in the repo.
- **`from __future__ import annotations`** as the first import in every module.
- **Type hints** on every parameter and return; **docstrings** on every public module, class and function (Google style, matching the surrounding module).
- **No new dependencies.** Everything here is stdlib.
- **`uv` only, never bare pip.** Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not `.venv`'s:** `uvx ruff@0.15.20 check .` and `uvx ruff@0.15.20 format --check .`. The `.venv` copy is 0.6.5 and false-flags `UP038` on `ollama.py`.
- **Line length 100**, target py311, lint rules E, F, I, N, W, UP.
- **Every existing test must stay green unchanged.** That is the acceptance criterion for the extraction work. If an existing test fails, the implementation is wrong — do not edit the test.
- Branch is already created: `feature/baseagent-metrics-json-extraction`.

## File Structure

| File | Responsibility |
|---|---|
| `bmlib/llm/utils.py` (modify) | `iter_json_spans()` locator + `extract_json()` policy |
| `bmlib/llm/json_repair.py` (modify) | `extract_and_repair_json()` policy + new `salvage_json_fields()` |
| `bmlib/llm/__init__.py` (modify) | export `iter_json_spans`, `salvage_json_fields` |
| `bmlib/agents/metrics.py` (create) | `PerformanceMetrics` dataclass, thread-safe |
| `bmlib/agents/base.py` (modify) | metrics wiring, `retry_context`, repair warning, `embed`/`embed_batch`/`test_connection` |
| `bmlib/agents/__init__.py` (modify) | export `PerformanceMetrics` |
| `tests/test_json_extraction.py` (create) | locator, both extractors, salvage |
| `tests/test_agents.py` (modify) | metrics, embeddings, connection test |

---

### Task 1: `iter_json_spans()` — the shared locator

**Files:**
- Modify: `bmlib/llm/utils.py`
- Test: `tests/test_json_extraction.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `iter_json_spans(text: str) -> Iterator[str]` — yields candidate JSON spans in priority order, **without validating them**. Tasks 2 and 3 build their policies on it.

Stage order (this is the whole contract):

1. contents of ` ```json ` fences, document order
2. contents of other fences whose stripped body starts with `{` or `[`
3. contents of the remaining fences
4. balanced `{...}`/`[...]` spans, counting only the pair type of the span's own opener
5. balanced `{...}` spans (brace-only) not already yielded by stage 4
6. the opener-to-end tail — **only** when stages 4 and 5 yielded nothing (truncated output)

Stage 5 exists so that `[{"a":1}]` still offers the inner object as a candidate. Without it, dict-preference in Task 2 would return the outer array where today's brace-only scan returns `{"a":1}` — a silent behaviour change in every provider's `json_mode` path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_json_extraction.py` with the AGPL header (copy from `tests/test_agents.py`), then:

```python
"""Tests for the shared JSON span locator and the two extractors built on it."""

from __future__ import annotations

import json

import pytest

from bmlib.llm.utils import extract_json, iter_json_spans


class TestIterJsonSpans:
    def test_json_fence_comes_first(self):
        text = '```\nnot json\n```\n```json\n{"a": 1}\n```'
        assert list(iter_json_spans(text))[0] == '{"a": 1}'

    def test_bare_fence_with_json_body_beats_plain_fence(self):
        text = '```\nprose\n```\n```\n{"a": 1}\n```'
        spans = list(iter_json_spans(text))
        assert spans.index('{"a": 1}') < spans.index("prose")

    def test_balanced_span_in_prose(self):
        text = 'The answer is {"score": 5} according to the model.'
        assert '{"score": 5}' in list(iter_json_spans(text))

    def test_braces_inside_strings_do_not_break_balancing(self):
        text = 'Result: {"expr": "f(x) = {x}", "ok": true} done.'
        assert '{"expr": "f(x) = {x}", "ok": true}' in list(iter_json_spans(text))

    def test_escaped_quote_does_not_end_the_string(self):
        text = r'{"a": "he said \"hi\"", "b": 2}'
        assert text in list(iter_json_spans(text))

    def test_array_span_and_nested_object_are_both_offered(self):
        # Stage 4 yields the array; stage 5 yields the object nested in it.
        spans = list(iter_json_spans('[{"a": 1}]'))
        assert '[{"a": 1}]' in spans
        assert '{"a": 1}' in spans
        assert spans.index('[{"a": 1}]') < spans.index('{"a": 1}')

    def test_two_objects_are_yielded_in_document_order(self):
        spans = list(iter_json_spans('noise {not valid} more {"good": 1} end'))
        assert spans.index("{not valid}") < spans.index('{"good": 1}')

    def test_unbalanced_opener_yields_the_tail(self):
        # Nothing balances, so the truncated tail is the only candidate.
        assert list(iter_json_spans('Result: {"a": 1, "b": [2')) == ['{"a": 1, "b": [2']

    def test_tail_is_not_yielded_when_something_balanced(self):
        spans = list(iter_json_spans('{"a": 1} trailing {'))
        assert spans == ['{"a": 1}']

    def test_no_json_yields_nothing(self):
        assert list(iter_json_spans("no json here")) == []

    def test_empty_text_yields_nothing(self):
        assert list(iter_json_spans("")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_json_extraction.py -v`
Expected: FAIL — `ImportError: cannot import name 'iter_json_spans' from 'bmlib.llm.utils'`

- [ ] **Step 3: Implement the locator**

In `bmlib/llm/utils.py`, add `from collections.abc import Iterator` to the imports and replace the module body below the header/docstring with this (keep `extract_json` exactly as it is for now — Task 2 rewires it):

```python
# Fence opener, optional language tag, optional trailing newline, then the
# body up to the closing fence.  ``(.*?)`` is non-greedy so consecutive
# fences do not merge into one block.
_FENCE_RE = re.compile(r"```(\w*)[ \t]*\r?\n?(.*?)```", re.DOTALL)

# Which closer ends a span opened by which opener.
_CLOSERS = {"{": "}", "[": "]"}


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

    1. ``\`\`\`json`` fenced bodies, in document order.
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
        first = min(
            (i for i in (text.find("{"), text.find("[")) if i >= 0),
            default=-1,
        )
        if first >= 0:
            yield text[first:]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_json_extraction.py -v`
Expected: PASS (11 tests)

Then confirm nothing else moved: `uv run pytest tests/ -q` — expected 825 passed, 32 skipped.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/llm/utils.py tests/test_json_extraction.py
git commit -m "feat(llm): add iter_json_spans(), one locator for both extractors

Yields candidate JSON spans in priority order without validating, so each
consumer can apply its own acceptance policy.  Stage 5 (brace-only spans)
keeps an object nested inside an array available as a candidate."
```

---

### Task 2: `extract_json()` on the locator, with dict-preference

**Files:**
- Modify: `bmlib/llm/utils.py`
- Test: `tests/test_json_extraction.py`

**Interfaces:**
- Consumes: `iter_json_spans(text) -> Iterator[str]` from Task 1.
- Produces: `extract_json(text: str) -> str` — unchanged signature. New policy: the first candidate that parses **to a dict**; failing that, the first that parses at all; failing that, *text* unchanged.

Dict-preference is the resolution biasbuster's `assessment_decomposed` reached independently (it guards `isinstance(result, dict)` at all three of its parse stages). It is what makes the widened stage 4 safe: without it, `[{"a":1}]` would start returning the array where today's brace-only scan returns the object.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_json_extraction.py`:

```python
class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == '{"a": 1}'

    def test_object_from_json_fence(self):
        assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_object_from_bare_fence(self):
        assert extract_json('```\n{"a": 42}\n```') == '{"a": 42}'

    def test_object_from_prose(self):
        text = 'Here is the result: {"score": 0.8} end.'
        assert extract_json(text) == '{"score": 0.8}'

    def test_skips_unparseable_candidate(self):
        text = 'noise {not valid} more prose {"good": 1} trailing'
        assert extract_json(text) == '{"good": 1}'

    def test_returns_input_unchanged_when_nothing_parses(self):
        assert extract_json("no json here") == "no json here"

    def test_prefers_a_dict_over_an_earlier_array(self):
        # Preserves the pre-consolidation outcome: the brace-only scan was
        # object-only, so the object won.  Dict-preference keeps that.
        assert extract_json('[1, 2] then {"a": 1}') == '{"a": 1}'

    def test_prefers_the_object_nested_in_a_single_element_array(self):
        assert extract_json('[{"a": 1}]') == '{"a": 1}'

    def test_falls_back_to_an_array_when_no_object_parses(self):
        # No dict candidate anywhere, so the array is better than nothing —
        # previously the whole prose-wrapped text came back unchanged.
        assert extract_json("Values: [1, 2, 3] done") == "[1, 2, 3]"

    def test_truncated_text_is_left_alone(self):
        # The tail candidate never parses, so it self-excludes.
        text = '{"a": 1, "b": [2'
        assert extract_json(text) == text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_json_extraction.py::TestExtractJson -v`
Expected: FAIL on `test_prefers_the_object_nested_in_a_single_element_array` and `test_falls_back_to_an_array_when_no_object_parses` (the old implementation is brace-only and never sees arrays outside a fence).

- [ ] **Step 3: Rewrite `extract_json`**

Replace the existing `extract_json` and delete the now-unused `_iter_balanced_objects`:

```python
def extract_json(text: str) -> str:
    """Extract a JSON span from text that may contain prose or code blocks.

    Walks the candidates from :func:`iter_json_spans` and returns the first
    that parses **to a dict**, falling back to the first that parses at all.
    Returns *text* unchanged when nothing parses.

    The preference for objects is deliberate: callers here — the Anthropic
    and OpenAI-compatible providers' ``json_mode`` path, and
    :meth:`bmlib.agents.BaseAgent.parse_json` — want the object a model was
    asked for, not an incidental array that happens to appear earlier.
    """
    fallback: str | None = None

    for candidate in iter_json_spans(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return candidate
        if fallback is None:
            fallback = candidate

    return fallback if fallback is not None else text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_json_extraction.py -v`
Expected: PASS

Run the whole suite: `uv run pytest tests/ -q` — expected 825 passed, 32 skipped. `tests/test_agents.py::TestParseJson` and the provider `json_mode` tests are the regression guard here; they must pass **unedited**.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/llm/utils.py tests/test_json_extraction.py
git commit -m "refactor(llm): build extract_json() on the shared locator

Adds dict-preference so the widened span scan cannot start returning an
array where the old brace-only scan returned an object.  extract_json()
gets direct test coverage for the first time."
```

---

### Task 3: `extract_and_repair_json()` on the locator

**Files:**
- Modify: `bmlib/llm/json_repair.py`
- Test: `tests/test_json_extraction.py`

**Interfaces:**
- Consumes: `iter_json_spans(text)` from Task 1.
- Produces: `extract_and_repair_json(response: str, repair: bool = True) -> tuple[str, bool]` — unchanged signature and return. New policy: the first candidate that validates **or repairs**; walk on when one does neither; `ValueError` once exhausted.

Walking matters. Taking the first candidate blindly would be wrong: today the function *rejects* a bare fence whose body does not start with `{`/`[` and falls through to the brace scan. Walking reproduces that without the locator having to tag which stage a span came from — and is strictly more robust besides.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_json_extraction.py`:

```python
from bmlib.llm.json_repair import extract_and_repair_json


class TestExtractAndRepairJsonPolicy:
    def test_walks_past_a_candidate_that_cannot_repair(self):
        # The junk fence body is offered first; the real object follows.
        text = '```\nnot json at all\n```\nbut here: {"a": 1}'
        extracted, repaired = extract_and_repair_json(text)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is False

    def test_walks_past_an_unrepairable_brace_span(self):
        text = 'noise {not valid} more prose {"good": 1} trailing'
        extracted, _ = extract_and_repair_json(text)
        assert json.loads(extracted) == {"good": 1}

    def test_array_span_wins_over_its_nested_object(self):
        # Unlike extract_json, this has no dict-preference: the outermost
        # span is the model's actual output and repair should target it.
        extracted, _ = extract_and_repair_json('[{"a": 1}]')
        assert json.loads(extracted) == [{"a": 1}]

    def test_repair_disabled_still_finds_valid_json(self):
        extracted, repaired = extract_and_repair_json('{"a": 1}', repair=False)
        assert json.loads(extracted) == {"a": 1}
        assert repaired is False

    def test_repair_disabled_raises_on_malformed(self):
        with pytest.raises(ValueError):
            extract_and_repair_json('{"a": 1,}', repair=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_json_extraction.py::TestExtractAndRepairJsonPolicy -v`
Expected: FAIL — `test_walks_past_a_candidate_that_cannot_repair` and `test_walks_past_an_unrepairable_brace_span` raise `ValueError` under the current single-span implementation.

- [ ] **Step 3: Rewrite `extract_and_repair_json`**

In `bmlib/llm/json_repair.py`, add `from bmlib.llm.utils import iter_json_spans` to the imports and replace the whole function body (lines 463-548, everything from `if not response or not response.strip():` to the end of the function) with:

```python
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
```

Keep the docstring, and update its Returns/Raises prose to say the candidates are walked in priority order.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_json_extraction.py tests/test_json_repair.py -v`
Expected: PASS — including all of `tests/test_json_repair.py::TestExtractAndRepairJson` **unedited**.

Run the whole suite: `uv run pytest tests/ -q` — expected 825 passed, 32 skipped.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/llm/json_repair.py tests/test_json_extraction.py
git commit -m "refactor(llm): build extract_and_repair_json() on the shared locator

Closes the extraction half of #17: ~50 lines of hand-rolled fence and
brace scanning are gone.  A candidate that will neither parse nor repair
no longer aborts the extraction — the next candidate gets its turn."
```

---

### Task 4: `salvage_json_fields()`

**Files:**
- Modify: `bmlib/llm/json_repair.py`, `bmlib/llm/__init__.py`
- Test: `tests/test_json_extraction.py`

**Interfaces:**
- Consumes: `repair_json`, `JSONRepairError` (already in the module).
- Produces: `salvage_json_fields(text: str, keys: Iterable[str]) -> dict[str, Any]` — best-effort recovery of individual top-level fields; never raises; `{}` when nothing is found. Exported as `bmlib.llm.salvage_json_fields`.

This is biasbuster's `lenient_extract` generalised. Its motivating case, from that docstring: the model malformed only the `evidence_quotes` array at the tail while `judgement` and `justification` were intact — today bmlib loses the whole response.

**Not wired into `parse_json`.** Silently returning partial data would turn a loud failure into a quiet wrong answer. Callers opt in after catching the `ValueError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_json_extraction.py`:

```python
from bmlib.llm.json_repair import salvage_json_fields


class TestSalvageJsonFields:
    def test_recovers_fields_from_valid_json(self):
        text = '{"a": 1, "b": "x"}'
        assert salvage_json_fields(text, ["a", "b"]) == {"a": 1, "b": "x"}

    def test_recovers_intact_fields_when_the_tail_is_malformed(self):
        # The motivating case: only the trailing array was mangled.
        text = '{"judgement": "high", "quotes": ["a", "b'
        result = salvage_json_fields(text, ["judgement", "quotes"])
        assert result["judgement"] == "high"
        assert result["quotes"] == ["a", "b"]

    def test_recovers_values_of_every_json_type(self):
        text = '{"s": "x", "n": 1.5, "b": true, "z": null, "o": {"k": [1]}}'
        result = salvage_json_fields(text, ["s", "n", "b", "z", "o"])
        assert result == {"s": "x", "n": 1.5, "b": True, "z": None, "o": {"k": [1]}}

    def test_decodes_escapes_in_string_values(self):
        text = r'{"j": "he said \"hi\"\nthen left"}'
        assert salvage_json_fields(text, ["j"])["j"] == 'he said "hi"\nthen left'

    def test_missing_key_is_simply_absent(self):
        assert salvage_json_fields('{"a": 1}', ["a", "nope"]) == {"a": 1}

    def test_never_raises_on_junk(self):
        assert salvage_json_fields("no json here", ["a"]) == {}

    def test_empty_text_returns_empty_dict(self):
        assert salvage_json_fields("", ["a"]) == {}

    def test_first_match_wins(self):
        # Documented limitation: a key name occurring inside a string value
        # can be matched.  The first occurrence is what is taken.
        text = '{"note": "raw "judgement": "wrong" here", "judgement": "right"}'
        assert salvage_json_fields(text, ["judgement"])["judgement"] == "wrong"

    def test_exported_from_package(self):
        from bmlib.llm import salvage_json_fields as pkg_salvage

        assert pkg_salvage is salvage_json_fields
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_json_extraction.py::TestSalvageJsonFields -v`
Expected: FAIL — `ImportError: cannot import name 'salvage_json_fields'`

- [ ] **Step 3: Implement it**

Add `from collections.abc import Iterable` to `bmlib/llm/json_repair.py`'s imports, then append:

```python
def salvage_json_fields(text: str, keys: Iterable[str]) -> dict[str, Any]:
    """Recover individual top-level fields from a document that will not parse.

    A last resort for responses :func:`extract_and_repair_json` gives up on.
    A long structured answer is often malformed in only one place — typically
    a truncated array at the tail — while the fields the caller actually needs
    are intact.  This locates each requested key and decodes just the value
    that follows it.

    Deliberately **not** wired into :meth:`bmlib.agents.BaseAgent.parse_json`:
    returning partial data automatically would turn a loud failure into a
    quiet wrong answer.  Catch the :class:`ValueError` and opt in.

    Args:
        text: Raw response, valid JSON or not.
        keys: Field names to look for.

    Returns:
        A dict of the keys that could be recovered.  Never raises; returns an
        empty dict when nothing is found.

    Note:
        Matching is textual, so a key name appearing inside a string value can
        be matched.  The first occurrence wins.
    """
    recovered: dict[str, Any] = {}
    if not text:
        return recovered

    decoder = json.JSONDecoder()

    for key in keys:
        pattern = re.compile(r'"' + re.escape(key) + r'"\s*:\s*')
        for match in pattern.finditer(text):
            value, found = _decode_value_at(decoder, text, match.end())
            if found:
                recovered[key] = value
                break

    return recovered


def _decode_value_at(
    decoder: json.JSONDecoder, text: str, index: int
) -> tuple[Any, bool]:
    """Decode one JSON value starting at *index*, repairing a truncated tail.

    Returns ``(value, True)`` on success and ``(None, False)`` on failure —
    a bare ``None`` return would be indistinguishable from a decoded
    ``null``.
    """
    try:
        value, _ = decoder.raw_decode(text, index)
        return value, True
    except ValueError:
        pass

    # The value runs to the end of a truncated document: close it and retry.
    try:
        value, _ = decoder.raw_decode(repair_json(text[index:]), 0)
        return value, True
    except (JSONRepairError, ValueError):
        return None, False
```

Then export it in `bmlib/llm/__init__.py`: add `salvage_json_fields` to the `from bmlib.llm.json_repair import (...)` block and to `__all__`, and add `iter_json_spans` from `bmlib.llm.utils` to both as well (Task 1 created it; this is where it becomes public API).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_json_extraction.py -v`
Expected: PASS

Run the whole suite: `uv run pytest tests/ -q` — expected 825 passed, 32 skipped.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/llm/json_repair.py bmlib/llm/__init__.py tests/test_json_extraction.py
git commit -m "feat(llm): add salvage_json_fields() for field-level recovery

Generalises biasbuster's lenient_extract: when a long structured response
is malformed in one place, recover the fields that are intact instead of
losing the whole thing.  json.JSONDecoder.raw_decode handles every value
type natively, and a truncated tail goes through repair_json() first."
```

---

### Task 5: `PerformanceMetrics`

**Files:**
- Create: `bmlib/agents/metrics.py`
- Modify: `bmlib/agents/__init__.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PerformanceMetrics` with fields `total_prompt_tokens`, `total_completion_tokens`, `total_tokens`, `total_requests`, `total_retries`, `total_wall_time_seconds`, `start_time`, `end_time`; methods `add_request(prompt_tokens: int, completion_tokens: int, wall_time_seconds: float) -> None`, `add_retry() -> None`, `mark_start() -> None`, `mark_end() -> None`, `reset() -> None`, `snapshot() -> PerformanceMetrics`, `to_dict() -> dict[str, Any]`, `from_dict(data: dict[str, Any]) -> PerformanceMetrics` (classmethod), `format_report(title: str | None = None) -> str`; properties `elapsed_time_seconds`, `tokens_per_second`, `average_tokens_per_request`. Task 6 wires it into `BaseAgent`.

Upstream's `total_model_time_seconds` and `total_prompt_eval_seconds` are **deliberately absent**: they come from Ollama's raw nanosecond fields, nothing reaches bmlib's `LLMResponse` (`duration_seconds` is declared but no provider populates it), so both would be permanently `0.0` and every derived report would quietly lie.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents.py` (add `import threading` and `from bmlib.agents.metrics import PerformanceMetrics` at the top):

```python
class TestPerformanceMetrics:
    def test_starts_empty(self):
        m = PerformanceMetrics()
        assert m.total_tokens == 0
        assert m.total_requests == 0
        assert m.tokens_per_second == 0.0
        assert m.average_tokens_per_request == 0.0
        assert m.elapsed_time_seconds == 0.0

    def test_add_request_accumulates(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        m.add_request(10, 5, 1.0)
        assert m.total_prompt_tokens == 110
        assert m.total_completion_tokens == 55
        assert m.total_tokens == 165
        assert m.total_requests == 2
        assert m.total_wall_time_seconds == 3.0

    def test_tokens_per_second_uses_wall_time(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        assert m.tokens_per_second == 25.0

    def test_average_tokens_per_request(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 1.0)
        m.add_request(50, 0, 1.0)
        assert m.average_tokens_per_request == 100.0

    def test_add_retry_is_separate_from_requests(self):
        m = PerformanceMetrics()
        m.add_request(1, 1, 0.1)
        m.add_retry()
        assert m.total_requests == 1
        assert m.total_retries == 1

    def test_elapsed_time_between_marks(self):
        m = PerformanceMetrics()
        m.mark_start()
        m.mark_end()
        assert m.elapsed_time_seconds >= 0.0
        assert m.end_time is not None

    def test_mark_start_clears_a_previous_end(self):
        m = PerformanceMetrics()
        m.mark_start()
        m.mark_end()
        m.mark_start()
        assert m.end_time is None

    def test_reset_clears_everything(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        m.add_retry()
        m.mark_start()
        m.reset()
        assert m.total_tokens == 0
        assert m.total_retries == 0
        assert m.start_time is None

    def test_snapshot_is_independent(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        snap = m.snapshot()
        m.add_request(1, 1, 0.1)
        assert snap.total_tokens == 150
        assert m.total_tokens == 152

    def test_to_dict_round_trips(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        m.add_retry()
        restored = PerformanceMetrics.from_dict(m.to_dict())
        assert restored.total_tokens == m.total_tokens
        assert restored.total_retries == m.total_retries
        assert restored.total_wall_time_seconds == m.total_wall_time_seconds

    def test_to_dict_has_no_lock(self):
        assert "_lock" not in PerformanceMetrics().to_dict()

    def test_format_report_includes_the_numbers(self):
        m = PerformanceMetrics()
        m.add_request(100, 50, 2.0)
        report = m.format_report(title="ScoringAgent")
        assert "ScoringAgent" in report
        assert "150" in report
        assert "1" in report

    def test_format_report_without_title(self):
        assert "===" not in PerformanceMetrics().format_report()

    def test_concurrent_add_request_loses_nothing(self):
        # += is a read-modify-write; a shared agent must not drop counts.
        m = PerformanceMetrics()

        def worker() -> None:
            for _ in range(200):
                m.add_request(1, 1, 0.001)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert m.total_requests == 1600
        assert m.total_tokens == 3200

    def test_exported_from_package(self):
        from bmlib.agents import PerformanceMetrics as pkg_metrics

        assert pkg_metrics is PerformanceMetrics
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agents.py::TestPerformanceMetrics -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bmlib.agents.metrics'`

- [ ] **Step 3: Implement it**

Create `bmlib/agents/metrics.py` with the AGPL header, then:

```python
"""Per-agent performance accounting.

:class:`PerformanceMetrics` is instance-scoped and answers "what did this
agent do".  It is deliberately independent of
:class:`bmlib.llm.TokenTracker`, the process-wide cost ledger that answers
"what has this process spent" — an agent never writes to global state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PerformanceMetrics:
    """Cumulative statistics for one agent's LLM calls.

    Thread-safe: an agent instance may be shared across workers, and ``+=``
    is a read-modify-write.

    Attributes:
        total_prompt_tokens: Tokens sent to the model.
        total_completion_tokens: Tokens generated by the model.
        total_tokens: Sum of the two.
        total_requests: Successful requests, counting every attempt.
        total_retries: Attempts beyond the first, across all requests.
        total_wall_time_seconds: Wall-clock time inside successful requests.
        start_time: When collection started, or ``None``.
        end_time: When collection ended, or ``None`` if still running.
    """

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    total_retries: int = 0
    total_wall_time_seconds: float = 0.0
    start_time: float | None = None
    end_time: float | None = None
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def add_request(
        self, prompt_tokens: int, completion_tokens: int, wall_time_seconds: float
    ) -> None:
        """Record one successful LLM request."""
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_tokens += prompt_tokens + completion_tokens
            self.total_requests += 1
            self.total_wall_time_seconds += wall_time_seconds

    def add_retry(self) -> None:
        """Record one retry attempt."""
        with self._lock:
            self.total_retries += 1

    def mark_start(self) -> None:
        """Mark the start of a collection period, clearing any end mark."""
        with self._lock:
            self.start_time = time.time()
            self.end_time = None

    def mark_end(self) -> None:
        """Mark the end of a collection period."""
        with self._lock:
            self.end_time = time.time()

    def reset(self) -> None:
        """Clear all counters and marks."""
        with self._lock:
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0
            self.total_tokens = 0
            self.total_requests = 0
            self.total_retries = 0
            self.total_wall_time_seconds = 0.0
            self.start_time = None
            self.end_time = None

    def snapshot(self) -> PerformanceMetrics:
        """Return an independent copy, read under the lock."""
        with self._lock:
            copy = PerformanceMetrics(
                total_prompt_tokens=self.total_prompt_tokens,
                total_completion_tokens=self.total_completion_tokens,
                total_tokens=self.total_tokens,
                total_requests=self.total_requests,
                total_retries=self.total_retries,
                total_wall_time_seconds=self.total_wall_time_seconds,
                start_time=self.start_time,
                end_time=self.end_time,
            )
        return copy

    @property
    def elapsed_time_seconds(self) -> float:
        """Seconds from :meth:`mark_start` to :meth:`mark_end`, or to now."""
        if self.start_time is None:
            return 0.0
        end = self.end_time if self.end_time is not None else time.time()
        return end - self.start_time

    @property
    def tokens_per_second(self) -> float:
        """Completion tokens per second of wall time.

        Wall time, not model-inference time: no provider reports inference
        time through bmlib, and this is the throughput the caller observed.
        """
        if self.total_wall_time_seconds > 0:
            return self.total_completion_tokens / self.total_wall_time_seconds
        return 0.0

    @property
    def average_tokens_per_request(self) -> float:
        """Mean total tokens per request."""
        if self.total_requests > 0:
            return self.total_tokens / self.total_requests
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict, derived values included."""
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "total_retries": self.total_retries,
            "total_wall_time_seconds": round(self.total_wall_time_seconds, 3),
            "elapsed_time_seconds": round(self.elapsed_time_seconds, 3),
            "tokens_per_second": round(self.tokens_per_second, 2),
            "average_tokens_per_request": round(self.average_tokens_per_request, 1),
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceMetrics:
        """Rebuild from :meth:`to_dict` output, ignoring derived values."""
        return cls(
            total_prompt_tokens=data.get("total_prompt_tokens", 0),
            total_completion_tokens=data.get("total_completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            total_requests=data.get("total_requests", 0),
            total_retries=data.get("total_retries", 0),
            total_wall_time_seconds=data.get("total_wall_time_seconds", 0.0),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
        )

    def format_report(self, title: str | None = None) -> str:
        """Render a human-readable report.

        Args:
            title: Heading to print above the numbers.  Omitted entirely
                when ``None``.
        """
        lines: list[str] = []
        if title:
            lines.append(f"=== {title} Performance Metrics ===")

        retries = f" ({self.total_retries} retries)" if self.total_retries else ""
        lines.append(f"Requests:     {self.total_requests:,}{retries}")
        lines.append(
            f"Tokens:       {self.total_tokens:,} total "
            f"({self.total_prompt_tokens:,} prompt + "
            f"{self.total_completion_tokens:,} completion)"
        )

        elapsed = self.elapsed_time_seconds
        if elapsed > 0:
            lines.append(
                f"Time:         {elapsed:.2f}s elapsed "
                f"({self.total_wall_time_seconds:.2f}s in requests)"
            )
        else:
            lines.append(f"Time:         {self.total_wall_time_seconds:.2f}s in requests")

        if self.tokens_per_second > 0:
            lines.append(f"Speed:        {self.tokens_per_second:.1f} tokens/sec")
        if self.total_requests > 0:
            lines.append(f"Avg/Request:  {self.average_tokens_per_request:.0f} tokens")

        return "\n".join(lines)
```

Then in `bmlib/agents/__init__.py`:

```python
from bmlib.agents.base import BaseAgent
from bmlib.agents.metrics import PerformanceMetrics

__all__ = ["BaseAgent", "PerformanceMetrics"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agents.py -v`
Expected: PASS (15 new tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/agents/metrics.py bmlib/agents/__init__.py tests/test_agents.py
git commit -m "feat(agents): add thread-safe PerformanceMetrics

Per-agent accounting, independent of the global TokenTracker.  Upstream's
model-inference and prompt-eval timers are omitted: no provider reports
them through bmlib, so they would be permanently zero."
```

---

### Task 6: Wire metrics into `BaseAgent`, add `retry_context` and the repair warning

**Files:**
- Modify: `bmlib/agents/base.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: `PerformanceMetrics` from Task 5.
- Produces: `BaseAgent.metrics` (property → snapshot), `reset_metrics()`, `start_metrics()`, `stop_metrics()`, `format_metrics_report() -> str`, and `chat_json(..., retry_context: str = "")`.

`chat()` records **on success only** — a raised call records nothing. `total_requests` counts every attempt; `total_retries` counts the attempts beyond the first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents.py`:

```python
class TestAgentMetrics:
    def test_chat_records_a_request(self):
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="hi", model="test", input_tokens=10, output_tokens=4
        )

        agent.chat([agent.user_msg("test")])

        assert agent.metrics.total_requests == 1
        assert agent.metrics.total_prompt_tokens == 10
        assert agent.metrics.total_completion_tokens == 4
        assert agent.metrics.total_wall_time_seconds >= 0.0

    def test_failed_chat_records_nothing(self):
        agent = _make_agent()
        agent.llm.chat.side_effect = RuntimeError("provider exploded")

        with pytest.raises(RuntimeError):
            agent.chat([agent.user_msg("test")])

        assert agent.metrics.total_requests == 0

    @patch("bmlib.agents.base.time.sleep")
    def test_chat_json_counts_attempts_and_retries(self, mock_sleep):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response(""),
            LLMResponse(
                content='{"a": 1}', model="test", input_tokens=5, output_tokens=2
            ),
        ]

        agent.chat_json([agent.user_msg("test")])

        assert agent.metrics.total_requests == 2
        assert agent.metrics.total_retries == 1

    def test_metrics_property_is_a_snapshot(self):
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="hi", model="test", input_tokens=1, output_tokens=1
        )
        agent.chat([agent.user_msg("test")])

        snap = agent.metrics
        agent.chat([agent.user_msg("test")])

        assert snap.total_requests == 1
        assert agent.metrics.total_requests == 2

    def test_reset_start_stop(self):
        agent = _make_agent()
        agent.llm.chat.return_value = LLMResponse(
            content="hi", model="test", input_tokens=1, output_tokens=1
        )
        agent.chat([agent.user_msg("test")])
        agent.reset_metrics()
        assert agent.metrics.total_requests == 0

        agent.start_metrics()
        agent.stop_metrics()
        assert agent.metrics.end_time is not None

    def test_format_metrics_report_names_the_agent_class(self):
        agent = _make_agent()
        assert "BaseAgent" in agent.format_metrics_report()

    @patch("bmlib.agents.base.time.sleep")
    def test_retry_context_appears_in_the_log(self, mock_sleep, caplog):
        agent = _make_agent()
        agent.llm.chat.side_effect = [
            _make_response(""),
            _make_response('{"a": 1}'),
        ]

        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            agent.chat_json([agent.user_msg("t")], retry_context="citation extraction")

        assert "citation extraction" in caplog.text

    def test_repaired_response_logs_a_warning(self, caplog):
        # A response that only parsed after repair is the truncation signal.
        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            BaseAgent.parse_json('{"design": "rct", "scores": [1, 2')
        assert "repair" in caplog.text.lower()

    def test_clean_response_logs_no_warning(self, caplog):
        with caplog.at_level("WARNING", logger="bmlib.agents.base"):
            BaseAgent.parse_json('{"a": 1}')
        assert caplog.text == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agents.py::TestAgentMetrics -v`
Expected: FAIL — `AttributeError: 'BaseAgent' object has no attribute 'metrics'`

- [ ] **Step 3: Implement it**

In `bmlib/agents/base.py`:

1. Import: `from bmlib.agents.metrics import PerformanceMetrics`.
2. In `__init__`, after `self.max_tokens = max_tokens`, add `self._metrics = PerformanceMetrics()`.
3. Replace the body of `chat()`:

```python
        start = time.monotonic()
        response = self.llm.chat(
            messages=messages,
            model=self.model,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            json_mode=json_mode,
            **kwargs,
        )
        self._metrics.add_request(
            response.input_tokens, response.output_tokens, time.monotonic() - start
        )
        return response
```

and extend its docstring with: "Records the call into :attr:`metrics` on success; a request that raises records nothing."

4. Add `retry_context: str = ""` to `chat_json`'s keyword-only parameters, and at the top of the method:

```python
        context = f" for {retry_context}" if retry_context else ""
```

Then thread `context` through the four existing log calls and the final raise — for example:

```python
                logger.warning(
                    "Retry %d/%d%s after %.0fs (previous: %s)",
                    attempt + 1,
                    max_retries,
                    context,
                    delay,
                    last_error,
                )
                time.sleep(delay)
                self._metrics.add_retry()
```

...and:

```python
        raise ValueError(f"Failed after {max_retries} attempts{context}: {last_error}")
```

Document the parameter: "retry_context: Label naming the task, folded into the retry and failure log lines so a failure says what was being attempted."

5. Add the metrics accessors after `chat_json`:

```python
    # --- Performance metrics ---

    @property
    def metrics(self) -> PerformanceMetrics:
        """An independent snapshot of this agent's cumulative statistics."""
        return self._metrics.snapshot()

    def reset_metrics(self) -> None:
        """Clear all accumulated metrics."""
        self._metrics.reset()

    def start_metrics(self) -> None:
        """Mark the start of a metrics collection period."""
        self._metrics.mark_start()

    def stop_metrics(self) -> None:
        """Mark the end of a metrics collection period."""
        self._metrics.mark_end()

    def format_metrics_report(self) -> str:
        """Render this agent's metrics as a human-readable report."""
        return self._metrics.format_report(title=type(self).__name__)
```

6. In `parse_json`, replace the repair block so a rescue is logged:

```python
        # Last resort: repair common LLM JSON defects (single quotes, trailing
        # commas, truncation, unquoted keys) after extracting the JSON span.
        try:
            repaired, was_repaired = extract_and_repair_json(text)
            parsed = json.loads(repaired)
        except (ValueError, json.JSONDecodeError):
            pass
        else:
            if was_repaired:
                # Repair closes brackets, so a truncated response can parse
                # into a valid but incomplete object.  Say so.
                logger.warning(
                    "LLM JSON needed repair — the response may be truncated: %s",
                    text[:200],
                )
            return parsed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agents.py -v`
Expected: PASS, including every pre-existing `TestChatJson` and `TestParseJson` test **unedited**.

Run the whole suite: `uv run pytest tests/ -q` — expected 825 passed, 32 skipped.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/agents/base.py tests/test_agents.py
git commit -m "feat(agents): accumulate PerformanceMetrics in BaseAgent

chat() times and records every successful call; chat_json() counts its
retries and takes a retry_context label so a failure names the task.
parse_json() now warns when the repair stage was what rescued a response
— a repaired response is the truncation signal biasbuster hand-rolls a
required-field set to detect."
```

---

### Task 7: `embed()`, `embed_batch()` and `test_connection()` on `BaseAgent`

**Files:**
- Modify: `bmlib/agents/base.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: `LLMClient.embed()`, `LLMClient.embed_batch()`, `LLMClient.test_connection()`.
- Produces: `BaseAgent.embed(text: str, model: str | None = None) -> list[float]`, `BaseAgent.embed_batch(texts: list[str], model: str | None = None, max_batch_size: int | None = None) -> list[list[float]]`, `BaseAgent.test_connection() -> bool`, and the constructor parameter `embedding_model: str | None = None`.

`embedding_model` goes **last** in `__init__`, after `max_tokens`. Downstream projects construct positionally; any other placement silently shifts every following argument. Same rule as `Publication.pmcid`.

Embeddings are deliberately **not** recorded into `PerformanceMetrics` — mixing them in would distort `tokens_per_second`, which is about generation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents.py` (add `from bmlib.llm.data_types import BatchEmbeddingResponse, EmbeddingResponse` to the imports):

```python
class TestAgentEmbeddings:
    def test_embed_returns_the_vector(self):
        agent = _make_agent()
        agent.llm.embed.return_value = EmbeddingResponse(
            embedding=[0.1, 0.2], model="e", dimensions=2
        )

        assert agent.embed("hello") == [0.1, 0.2]

    def test_embed_uses_the_configured_embedding_model(self):
        mock_llm = MagicMock()
        agent = BaseAgent(llm=mock_llm, model="test:model", embedding_model="ollama:e2")
        mock_llm.embed.return_value = EmbeddingResponse(embedding=[1.0], model="e2")

        agent.embed("hello")

        assert mock_llm.embed.call_args.kwargs["model"] == "ollama:e2"

    def test_embed_argument_overrides_the_default(self):
        mock_llm = MagicMock()
        agent = BaseAgent(llm=mock_llm, model="test:model", embedding_model="ollama:e2")
        mock_llm.embed.return_value = EmbeddingResponse(embedding=[1.0], model="e3")

        agent.embed("hello", model="ollama:e3")

        assert mock_llm.embed.call_args.kwargs["model"] == "ollama:e3"

    def test_embed_raises_on_an_empty_vector(self):
        agent = _make_agent()
        agent.llm.embed.return_value = EmbeddingResponse(embedding=[], model="e")

        with pytest.raises(ValueError, match="[Ee]mpty embedding"):
            agent.embed("hello")

    def test_embed_does_not_touch_generation_metrics(self):
        agent = _make_agent()
        agent.llm.embed.return_value = EmbeddingResponse(embedding=[1.0], model="e")

        agent.embed("hello")

        assert agent.metrics.total_requests == 0

    def test_embed_batch_returns_vectors_in_order(self):
        agent = _make_agent()
        agent.llm.embed_batch.return_value = BatchEmbeddingResponse(
            embeddings=[[1.0], [2.0]], model="e", dimensions=1
        )

        assert agent.embed_batch(["a", "b"]) == [[1.0], [2.0]]

    def test_embed_batch_short_circuits_on_an_empty_list(self):
        agent = _make_agent()

        assert agent.embed_batch([]) == []
        agent.llm.embed_batch.assert_not_called()

    def test_embed_batch_raises_on_a_count_mismatch(self):
        agent = _make_agent()
        agent.llm.embed_batch.return_value = BatchEmbeddingResponse(
            embeddings=[[1.0]], model="e", dimensions=1
        )

        with pytest.raises(ValueError, match="2 texts"):
            agent.embed_batch(["a", "b"])


class TestAgentConnection:
    def test_reports_a_reachable_provider(self):
        agent = _make_agent()
        agent.llm.test_connection.return_value = True

        assert agent.test_connection() is True
        agent.llm.test_connection.assert_called_once_with("test")

    def test_reports_an_unreachable_provider(self):
        agent = _make_agent()
        agent.llm.test_connection.return_value = False

        assert agent.test_connection() is False

    def test_falls_back_to_the_client_default_provider(self):
        mock_llm = MagicMock()
        mock_llm.default_provider = "ollama"
        mock_llm.test_connection.return_value = True
        agent = BaseAgent(llm=mock_llm, model="bare-model-name")

        assert agent.test_connection() is True
        mock_llm.test_connection.assert_called_once_with("ollama")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agents.py::TestAgentEmbeddings tests/test_agents.py::TestAgentConnection -v`
Expected: FAIL — `TypeError: BaseAgent.__init__() got an unexpected keyword argument 'embedding_model'`

- [ ] **Step 3: Implement it**

In `bmlib/agents/base.py`:

1. Add `embedding_model: str | None = None` as the **last** parameter of `__init__`, store `self.embedding_model = embedding_model`, and document it in the class docstring: "embedding_model: Default model string for :meth:`embed`. ``None`` lets the client pick its default provider's default. Declared last so positional construction stays stable across versions."

2. Add after the metrics accessors:

```python
    # --- Embeddings ---

    def embed(self, text: str, model: str | None = None) -> list[float]:
        """Embed *text*, returning the raw vector.

        Args:
            text: The text to embed.
            model: Model string, overriding :attr:`embedding_model` for this
                call.  ``None`` falls back to the agent's default, then to
                the client's.

        Returns:
            The embedding vector.

        Raises:
            ValueError: If the provider returns an empty vector.

        Note:
            Embedding calls are not recorded into :attr:`metrics`: mixing
            them into ``tokens_per_second`` would distort a figure that is
            about generation.
        """
        response = self.llm.embed(text=text, model=model or self.embedding_model)
        if not response.embedding:
            raise ValueError(f"Empty embedding returned by model {response.model!r}")
        return response.embedding

    def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
        max_batch_size: int | None = None,
    ) -> list[list[float]]:
        """Embed *texts* in as few provider requests as possible.

        Several times faster than looping :meth:`embed` on bulk corpora.

        Args:
            texts: The texts to embed.  An empty list returns ``[]`` without
                contacting the provider.
            model: Model string, overriding :attr:`embedding_model`.
            max_batch_size: Maximum texts per provider request; ``None`` lets
                the provider choose.

        Returns:
            One vector per input text, in input order.

        Raises:
            ValueError: If the provider returns a different number of vectors
                than texts given.
        """
        if not texts:
            return []
        response = self.llm.embed_batch(
            texts=texts,
            model=model or self.embedding_model,
            max_batch_size=max_batch_size,
        )
        if len(response.embeddings) != len(texts):
            raise ValueError(
                f"Provider returned {len(response.embeddings)} embeddings "
                f"for {len(texts)} texts"
            )
        return response.embeddings

    # --- Connectivity ---

    def test_connection(self) -> bool:
        """Report whether this agent's provider is reachable.

        Reachability only — whether *this* model is installed is a separate
        question, answered by ``llm.list_models(provider)``.
        """
        provider = (
            self.model.split(":", 1)[0] if ":" in self.model else self.llm.default_provider
        )
        return bool(self.llm.test_connection(provider))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agents.py -v`
Expected: PASS

Run the whole suite: `uv run pytest tests/ -q` — expected 825 passed, 32 skipped.

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add bmlib/agents/base.py tests/test_agents.py
git commit -m "feat(agents): add embed(), embed_batch() and test_connection()

embedding_model is declared last on __init__ so positional construction
stays stable, the same rule Publication.pmcid follows.  Embedding calls
stay out of PerformanceMetrics: tokens_per_second is about generation."
```

---

### Task 8: Documentation, changelog and follow-up issue

**Files:**
- Modify: `docs/manual/agents.md`, `docs/manual/llm.md`, `CHANGELOG.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code-facing.

- [ ] **Step 1: Update `docs/manual/agents.md`**

Add a `## Performance Metrics` section documenting `agent.metrics` (snapshot semantics), `reset_metrics()`/`start_metrics()`/`stop_metrics()`, `format_metrics_report()`, and the `PerformanceMetrics` fields — stating that it is independent of `TokenTracker` and that embeddings are excluded. Add `## Embeddings` for `embed()`/`embed_batch()`/`embedding_model`, and `## Connectivity` for `test_connection()`. Document `retry_context` in the existing `chat_json` section.

Update the numbered JSON-parsing pipeline near line 285: stage 2 now says `extract_json()` walks the candidates from `iter_json_spans()` and prefers an object; stage 3 says `extract_and_repair_json()` walks the same candidates, validating or repairing each, and that a repaired response logs a WARNING because it may be truncated. Mention `salvage_json_fields()` as the opt-in last resort.

- [ ] **Step 2: Update `docs/manual/llm.md`**

Document `iter_json_spans()` (the six stages, in order) and `salvage_json_fields()` with a worked example. Revise the `extract_json` vs `extract_and_repair_json` comparison table near line 1073 — both now share one locator and differ only in acceptance policy (validate + prefer-dict, versus validate-or-repair).

- [ ] **Step 3: Update `CHANGELOG.md` under `[Unreleased]`**

Under `### Added`: `PerformanceMetrics` and the `BaseAgent` accessors; `BaseAgent.embed()`/`embed_batch()`/`test_connection()`/`embedding_model`; `chat_json(retry_context=...)`; `iter_json_spans()`; `salvage_json_fields()`.

Under `### Changed`: `extract_json()` and `extract_and_repair_json()` rebuilt on the shared locator (closes #17), naming the four deltas from the spec — arrays visible to `extract_json`, dict-preference, fence priority, and candidate-walking instead of one-span-or-bust. Note that `parse_json()` now warns when repair was what rescued a response.

- [ ] **Step 4: Update `CLAUDE.md`**

In the directory tree add `agents/metrics.py`. Extend the `agents/` module description to mention metrics, embeddings and the connection test. In the test-file mapping table add the row `| llm/ | ... test_json_extraction.py |` alongside the existing llm entries.

- [ ] **Step 5: Verify everything and commit**

```bash
uv run pytest tests/ -v
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
git add -A
git commit -m "docs: document BaseAgent metrics, embeddings and the JSON locator"
```

- [ ] **Step 6: File the follow-up issue**

```bash
gh issue create --title "BaseAgent.parse_json is annotated -> dict but can return a list" --body "Split out of the #17 consolidation.

\`BaseAgent.parse_json()\` is annotated \`-> dict\`, but returns whatever the response parsed to. A model emitting a top-level array (fenced, or as the only JSON in the response) yields a list. This predates the #17 work; dict-preference in \`extract_json()\` narrows it — an object anywhere in the response now wins — but does not close it.

Options: raise \`ValueError\` when the result is not a dict (breaking for anyone relying on lists today), or widen the annotation to \`dict | list\`. Needs a call on which contract we want.

Related: \`extract_json()\` on \`[{\"a\": 1}, {\"b\": 2}]\` returns the first object and silently drops the sibling. Same decision covers it."
```

---

## Self-Review

**Spec coverage:** `PerformanceMetrics` → Task 5. Metrics wiring, `retry_context`, repair warning → Task 6. `embed`/`embed_batch`/`test_connection`/`embedding_model` → Task 7. `iter_json_spans` → Task 1. `extract_json` + dict-preference → Task 2. `extract_and_repair_json` + candidate walking → Task 3. `salvage_json_fields` → Task 4. Exports → Tasks 4 and 5. Docs/CHANGELOG/CLAUDE.md → Task 8. Both spec follow-ups → Task 8 Step 6 (the `parse_json` annotation) and the PR description (downstream migration).

**Deviation from the spec, deliberate:** the spec listed five locator stages; this plan has six. Stage 5 (brace-only spans) is new and load-bearing — without it, `extract_json('[{"a":1}]')` returns the array where today's brace-only scan returns the object, which would be a silent behaviour change in every provider's `json_mode` path. The spec's delta 1 should be read as covering this; the plan's Task 1 rationale and Task 2's `test_prefers_the_object_nested_in_a_single_element_array` pin it.

**Type consistency:** `add_request(prompt_tokens, completion_tokens, wall_time_seconds)` is called with exactly those three arguments in Task 6. `snapshot()` returns `PerformanceMetrics` and is what the `metrics` property returns. `iter_json_spans(text) -> Iterator[str]` is consumed as an iterable of `str` in Tasks 2, 3 and by nothing else. `extract_and_repair_json` keeps its `tuple[str, bool]` return, which Task 6's `parse_json` unpacks as `repaired, was_repaired`.
