# `parse_json`'s Return Contract, and the Fragment It Hides — Design

**Date:** 2026-07-29
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #33
**Ships as:** its own PR, separate from #36 — unrelated defect, unrelated module

## Problem

`BaseAgent.parse_json()` is annotated `-> dict` and returns whatever the
response parsed to. A model that emits a top-level array — fenced, or as the
only JSON in the response — yields a `list`. `chat_json()` inherits the same
false annotation.

The issue frames this as a two-option choice: raise on a non-dict (breaking,
makes the annotation true) or widen the annotation (honest, pushes an
`isinstance` check onto callers). Investigating it surfaced a third fact that
changes the calculus, and which the issue records as "related, same decision":

**`extract_json()` silently discards data.** For an unfenced
`[{"a": 1}, {"b": 2}]`, `iter_json_spans()` yields the whole array at stage 4
and then the *nested* `{"a": 1}` at stage 5. The dict preference at
`llm/utils.py:208` accepts the nested object, so `extract_json()` returns
`{"a": 1}` and the sibling vanishes with no error anywhere. This runs on
**every** `json_mode` response from the Anthropic and OpenAI-compatible
providers, not just through `BaseAgent`.

That reframes the choice. Raising on a non-dict does not fix the truncation —
it *hides* it, and inconsistently: `parse_json()` tries `json.loads(text)`
first, so a bare `[{"a": 1}, {"b": 2}]` would raise while the same array
embedded in prose would return `{"a": 1}` and be accepted as a dict.

Two further facts constrain the answer:

- Upstream agents queued for the bmlibrarian port prompt for top-level arrays
  (`agents/document_interrogation_agent.py:662`,
  `agents/systematic_review/planner.py:129`). A dict-only contract locks them
  out of `BaseAgent`, or forces them onto raw `chat()` plus their own parsing —
  which is the duplication issue #17 just removed.
- bmlib's own two `chat_json()` callers (`quality/quality_agent.py:150`,
  `quality/study_classifier.py:128`) do want dicts, and today a list reaches
  `_parse_data()` and dies on `.get()` with `AttributeError`, caught by a broad
  `except Exception` and degraded to `unclassified()` — no retry, no diagnosis.

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| `parse_json` contract | **Widen to `dict \| list`, add opt-in `require_dict`** | Raise unconditionally (breaking, blocks the array-shaped ports, hides the fragment bug); widen only (leaves the data loss) |
| Nested-fragment preference | **Last resort, never a preference** | Leave as-is; remove stage 5 from `extract_json` entirely |
| Strict-mode ergonomics | **`@overload` on `Literal[True]`** so strict callers get a real `dict` | Plain `dict \| list` return with `isinstance` at every call site |
| bmlib's own callers | **Pass `require_dict=True`** | Leave them to fail late on `.get()` |
| PR split | **Separate PR from #36** | One combined PR |

## Design

### 1. `extract_json()` prefers a whole span over a fragment

Split the acceptance policy out of the walk, and run it twice:

```python
def extract_json(text: str) -> str:
    whole = _first_acceptable(text, nested_objects=False)
    if whole is not None:
        return whole
    # Nothing at the top level parsed. Only now is an object dug out of the
    # inside of a span worth having.
    fragment = _first_acceptable(text, nested_objects=True)
    return fragment if fragment is not None else text


def _first_acceptable(text: str, *, nested_objects: bool) -> str | None:
    """The existing fence-then-dict-then-first-parseable policy, unchanged."""
```

`_first_acceptable` is today's loop body verbatim — fenced candidate wins on
parse alone, then dict preference, then first-parseable fallback — returning
`None` instead of `text` when nothing parses.

Behaviour changes in exactly one case: when the only parseable JSON is an array
containing objects, the array is returned whole instead of its first element.
Everything else is preserved, including the rescue case a nested fragment
exists for (`Here: [garbage {"a": 1} garbage]` — no whole span parses, so the
second walk still finds it).

The second walk re-offers the stage 1–4 candidates the first walk already
rejected. That is deliberate: it costs a re-parse of known-bad candidates on a
path that only runs when the response was malformed, and it keeps the two walks
identical in policy rather than introducing stage-aware bookkeeping into
`iter_json_spans()`. Every genuinely new candidate in the second walk comes
from the brace-only pass, so it is an object and returns immediately.

**Why this does not contradict the two existing deliberate non-fixes.** A fence
still wins on parse alone — the same principle, that a fence is the model's own
delimitation of its answer, now extended to unfenced spans. And
`extract_and_repair_json()` keeps `nested_objects=False` with no last-resort
second walk, because the asymmetry between the two consumers is real:
*validating* a fragment reports what is there, while *repairing* one closes
brackets around it and fabricates a structure the model never emitted.

### 2. Widened annotations

`parse_json`, `chat_json` and `_try_parse` become `dict | list` (`| None` for
`_try_parse`). Docstrings state that a response whose JSON is an array yields
that array whole.

### 3. Opt-in strictness

```python
@staticmethod
def parse_json(text: str, *, require_dict: bool = False) -> dict | list: ...

def chat_json(self, ..., require_dict: bool = False) -> dict | list: ...
```

`parse_json` raises `ValueError` naming the shape when `require_dict` is set and
the result is not a dict.

`chat_json` performs its own `isinstance` check rather than relying on
`parse_json` raising, because it needs to distinguish the two failures in
`last_error`: "unparseable response" and "expected a JSON object, got list" are
different diagnoses, and message-sniffing a `ValueError` to tell them apart
would be fragile. The check appears twice — once on the normal return path,
once on the truncation path's `_try_parse` result, which must not hand back a
list either. Each is one condition, and both are covered by tests.

The shape failure `continue`s inside the existing retry loop, so a model that
returns an array when asked for an object gets the normal backoff retries and,
if it persists, a final `ValueError` naming the shape. For bmlib's two callers
that converts a silent `unclassified()` into up to three attempts at a usable
answer.

`@overload` on `Literal[True]` / `Literal[False]` narrows the return to `dict`
for strict callers, so the widened annotation costs them no `isinstance`
friction. Note that CI runs ruff only — no type checker — so the overloads
serve downstream consumers' checkers and are verified by reading, not by our
build. Runtime behaviour is covered by tests either way.

## Testing

`tests/test_json_extraction.py`:

- an unfenced array of objects is returned whole, not reduced to its first
  element (the defect);
- a nested object is still rescued when no enclosing span parses (the rescue
  case the stage exists for);
- `test_fenced_array_of_objects_is_returned_whole` re-run unchanged as the
  guard that the fence rule survived;
- an incidental array before the requested object still loses to the object
  (the dict preference is unchanged).

`tests/test_agents.py`:

- `parse_json` returns a list for an array-only response;
- `parse_json(require_dict=True)` raises `ValueError` naming the shape;
- `chat_json(require_dict=True)` **retries** a list response rather than
  returning it, and its final error names the shape, not "unparseable";
- `chat_json(require_dict=True)` rejects a list that arrives via the
  truncation path's `_try_parse`;
- default `chat_json` still returns a list unchanged (non-breaking).

## Documentation

- `CHANGELOG.md` under `[Unreleased]`: the widened contract, the new keyword,
  and the extraction change — flagged as a behaviour change for anyone whose
  prompts produce arrays embedded in prose, since they will now receive the
  whole array where they previously received its first element.
- `docs/manual/agents.md` and `docs/manual/llm.md`: the contract and
  `require_dict`.
- `HANDOVER.md` deliberate-non-fix list: a new entry for the two-walk policy —
  specifically that collapsing it back to a single walk restores the silent
  truncation, and that `extract_and_repair_json` deliberately has no equivalent
  second walk.

## Verification

`uv run pytest tests/ -v`, then `uvx ruff@0.15.20 check . && uvx ruff@0.15.20
format --check .` — the CI-pinned ruff, not the older one in `.venv`.
