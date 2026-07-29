# `parse_json`'s Return Contract, and the Fragment It Hides — Design

**Date:** 2026-07-29 (reviewed and amended 2026-07-30)
**Status:** Approved (interactive session; scope decisions recorded below)
**Refs:** #33 — deliberately not "Closes": the issue stays open until the
implementation lands, since this document is design only
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
`{"a": 1}` and the sibling vanishes with no error anywhere. The reach is wider
than `BaseAgent`: the Anthropic and OpenAI-compatible providers both call
`extract_json()` on their `json_mode` path.

How much wider needs stating precisely, because it bounds the blast radius of
the fix. Both providers guard the call
(`providers/anthropic.py:253`, `providers/openai_compat.py:254`):

```python
if json_mode and content:
    try:
        json.loads(content)
    except json.JSONDecodeError:
        content = extract_json(content)
```

A *bare* array response parses, so it never reaches `extract_json()` and is
already returned whole today. A *fenced* array does reach it — the fence
markers stop the outer `json.loads` — but the fence rule already returns it
whole. What is left, and what the defect actually costs, is an unfenced array
of objects sitting in prose. So the blast radius of both the bug and the fix is
that one shape, not every `json_mode` response.

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
| Non-dict fallback | **Ranked — a list holding objects beats any other non-dict span** | First-parseable (lets an incidental `[]` earlier in the response mask the payload entirely) |
| Strict-mode ergonomics | **`@overload` on `Literal[True]`** so strict callers get a real `dict` | Plain `dict \| list` return with `isinstance` at every call site |
| bmlib's own callers | **Pass `require_dict=True`** | Leave them to fail late on `.get()` |
| PR split | **Separate PR from #36** | One combined PR |

## Design

### 1. `extract_json()` prefers a whole span over a fragment

Split the acceptance policy out of the walk, and run it twice:

```python
def extract_json(text: str) -> str:
    fenced: set[str] | None = None

    def fences() -> set[str]:
        # Still built on first need — see the note below — but now at most
        # once across both walks rather than once per walk.
        nonlocal fenced
        if fenced is None:
            fenced = {body.strip() for _, body in _FENCE_RE.findall(text)}
        return fenced

    whole = _first_acceptable(text, fences, nested_objects=False)
    if whole is not None:
        return whole
    # Nothing at the top level parsed. Only now is an object dug out of the
    # inside of a span worth having.
    fragment = _first_acceptable(text, fences, nested_objects=True)
    return fragment if fragment is not None else text


def _first_acceptable(
    text: str, fences: Callable[[], set[str]], *, nested_objects: bool
) -> str | None:
    """Fence, then dict, then the best non-dict span; None when nothing parses."""
    fallback: str | None = None
    with_objects: str | None = None

    for candidate in iter_json_spans(text, nested_objects=nested_objects):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, RecursionError):
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
```

This is today's loop body — fenced candidate wins on parse alone, then dict
preference — with two changes: it returns `None` instead of `text` when nothing
parses, and its non-dict fallback is *ranked* rather than first-parseable. A
span that is a list holding at least one object beats any other non-dict span.

**Why the fallback has to be ranked.** Prefer-whole-over-fragment is not by
itself enough, because walk 1 accepting *any* parseable non-dict span means
walk 2 never runs. Where the payload is nested inside a *later* array and
something incidental parses earlier, an unranked first-parseable fallback
returns the incidental span:

| Input | Today | Unranked | Ranked |
|---|---|---|---|
| `[] and [{"a":1}]` | `{"a":1}` | `[]` | `[{"a":1}]` |
| `x ["s"] y [{"a":1}]` | `{"a":1}` | `["s"]` | `[{"a":1}]` |
| `Prose [1, 2] and [{"a": 1}]` | `{"a":1}` | `[1, 2]` | `[{"a": 1}]` |

The unranked column is a worse failure than the bug being fixed. Truncating
`[{"a": 1}, {"b": 2}]` to `{"a": 1}` at least returns data the model emitted;
returning `[]` substitutes *unrelated* data, and `[]` parses cleanly and
survives any downstream shape check, so nothing anywhere would notice. Ranking
costs one `isinstance` in a loop that already parses every candidate.

Behaviour changes exactly when the JSON the model emitted is an array holding
objects: it is now returned whole, where today it is reduced to the first
object nested inside it. That covers all three rows of the table above and the
bare `[{"a": 1}, {"b": 2}]` defect. Preserved:

- the rescue case a nested fragment exists for (`Here: [garbage {"a": 1}
  garbage]` — no whole span parses, so the second walk still finds it);
- a top-level dict still beating an earlier array (`[1, 2] then {"a": 1}`);
- an array with no objects in it (`Values: [1, 2, 3] done`), which has no
  nested candidate to lose to and so never entered the ranking;
- a fenced span, which returns before either fallback is consulted.

The second walk re-offers the stage 1–4 candidates the first walk already
rejected. That is deliberate: it costs a re-parse of known-bad candidates on a
path that only runs when the response was malformed, and it keeps the two walks
identical in policy rather than introducing stage-aware bookkeeping into
`iter_json_spans()`. Every genuinely new candidate in the second walk comes
from the brace-only pass, so it is an object and returns immediately.

That last sentence is load-bearing and worth stating why it holds: stage 6
cannot newly fire in the second walk. `balanced_found` for `nested_objects=True`
is a superset of the `nested_objects=False` case — it ORs in the brace-only
pass — so if the second walk reaches stage 6, the first walk did too, and it
offered the identical tail. New candidates therefore only ever come from the
brace-only pass, and a span opening with `{` either parses to a dict or fails.

It follows that neither fallback branch is reachable in the second walk, so the
ranking is a walk-1 concern only. Walk 2 runs at all only when walk 1 returned
`None`, and since `fallback` is set for *any* parseable non-dict, that means
nothing in stages 1–4/6 parsed. Those candidates are re-offered unchanged and
still do not parse, so every value the second walk can return is a dict.

Three mechanical notes for the implementer:

- `llm/utils.py` currently imports only `Iterator` from `collections.abc`; the
  `fences` parameter annotation needs `Callable` added to that import.
- Hoist the `fenced` set into `extract_json()` as a memoised closure, rather
  than building it eagerly and passing a plain set. Two walks each holding
  their own lazy build would run `_FENCE_RE.findall` over the whole response
  twice; building it up front instead would run it *always*, including on the
  common path the existing comment at `llm/utils.py:190-194` exists to protect
  — first candidate parses to a dict, fence set never consulted. The closure
  keeps both properties, and that comment stays true as written.
- `iter_json_spans()`'s `nested_objects` default becomes `True` for exactly one
  caller — the second walk — while both other call sites pass `False`
  explicitly. Leave the default alone rather than flipping it: `True` is what
  the parameter name reads as, and a `False` default would make
  `extract_and_repair_json()`'s explicit `False` look redundant and invite its
  removal. Note the imbalance in the docstring instead.

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

The module docstring's usage example (`agents/base.py:31-37`) is part of this:
it declares `def score(...) -> dict` and returns `self.parse_json(...)`. It is
the first thing a reader of the module sees, so leaving it annotated `-> dict`
re-teaches the contract this change corrects.

### 3. Opt-in strictness

```python
@staticmethod
def parse_json(text: str, *, require_dict: bool = False) -> dict | list: ...

# chat_json's signature is already keyword-only after `messages`; the new
# parameter joins the existing block after the `*`, not before it.
def chat_json(
    self,
    messages: list[LLMMessage],
    *,
    ...,
    require_dict: bool = False,
) -> dict | list: ...
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

**With one exception, mirroring the truncation path.** `chat_json` already
refuses to retry a truncation at temperature 0 (`agents/base.py:220-227`):
greedy sampling reproduces the identical completion, so the retry is provably
futile and it raises immediately. That argument transfers verbatim — a model
that returned an array at temperature 0 returns the same array on the next
attempt, from the same messages. So the shape check must make the same
distinction the truncation check makes: retry at temperature > 0, raise
immediately at temperature 0. Letting it `continue` unconditionally would put
two adjacent branches in one method on opposite rules for the same reason, and
bill downstream strict callers for three identical calls to get there.

This does not change anything for bmlib's own two callers — `quality_agent`
defaults to `temperature=0.2` and `study_classifier` to `0.1`, so both take the
retrying branch. It matters for a downstream caller pinning temperature 0 for
reproducibility, which is exactly the caller most likely to want
`require_dict`.

`@overload` on `Literal[True]` / `Literal[False]` narrows the return to `dict`
for strict callers, so the widened annotation costs them no `isinstance`
friction. Note that CI runs ruff only — no type checker — so the overloads
serve downstream consumers' checkers and are verified by reading, not by our
build. Runtime behaviour is covered by tests either way.

Because nothing in the build checks them, pin the `@overload` / `@staticmethod`
decorator order deliberately and verify it **once** against a real type checker
(`uvx mypy`, throwaway — not added to CI). Stacking these two has been
order-sensitive across checker versions, and a wrong order fails silently here:
ruff will not flag it, the tests will not catch it, and the only symptom is a
downstream consumer's checker inferring `dict | list` where the overload was
supposed to give it `dict`. That is the whole benefit of the decision, lost
with no signal.

## Testing

`tests/test_json_extraction.py`:

New:

- an unfenced array of objects is returned whole, not reduced to its first
  element (the defect);
- a nested object is still rescued when no enclosing span parses (the rescue
  case the stage exists for);
- **the ranked fallback** — an array of objects preceded by an incidental
  parseable span (`'[] and [{"a":1}]'`) returns `'[{"a":1}]'`, not `'[]'`. This
  is the guard on the failure mode described above, where an unranked
  first-parseable fallback substitutes unrelated data that parses cleanly and
  so fails silently everywhere downstream. Worth a second case with a non-empty
  incidental span (`'x ["s"] y [{"a":1}]'`), since `[]` is falsy and an
  implementation that leans on truthiness rather than `is None` would pass the
  first case by accident;
- an array of scalars with no objects anywhere still returns the *first*
  parseable span, confirming the ranking only engages when objects are present.

**Rewritten — two existing tests assert the behaviour this design changes, and
both must be inverted.** `test_prefers_the_object_nested_in_a_single_element_array`
(`test_json_extraction.py:113`) and `test_unfenced_nested_object_still_wins`
(`:133`) are the same assertion under two names —
`extract_json('[{"a": 1}]') == '{"a": 1}'` — and `[{"a": 1}]` is exactly the
"an array holding objects" case, so both now yield
`'[{"a": 1}]'`. A prototype of the full policy above — memoised fence closure,
two walks, ranked fallback — run against the current suite fails these two and
no others (`960 passed, 2 failed`).

The second one's comment currently reads *"this is the pre-consolidation
behaviour and **must not change**"*. That invariant is deliberately retired
here: it was written to pin the *fence* asymmetry — that dict preference still
applied without a fence — at a time when the sibling-dropping cost of that
preference had not been noticed. Replace both with a single test asserting the
array is returned whole, carrying a comment that says the nested stage is now a
last resort and why. Leaving the old comment in place would read as a standing
prohibition on this change and invite the next session to revert it.

Unchanged, and worth re-running deliberately:

- `test_fenced_array_of_objects_is_returned_whole` — the guard that the fence
  rule survived;
- `test_prefers_a_dict_over_an_earlier_array` (`'[1, 2] then {"a": 1}'`) — an
  incidental array before the requested object still loses to the object. Note
  what this does and does not pin: the dict preference survives for a
  **top-level** dict, which is the only case this test covers. A dict reachable
  *only* from inside an array no longer wins — that is the change, and the
  ranked-fallback tests above are what pin the replacement behaviour;
- `test_falls_back_to_an_array_when_no_object_parses`
  (`"Values: [1, 2, 3] done"`) — a near miss that still passes. It looks like
  the changed case but is not: the array holds no objects, so no nested
  candidate ever competed with it.

`tests/test_agents.py`:

- `parse_json` returns a list for an array-only response;
- `parse_json(require_dict=True)` raises `ValueError` naming the shape;
- `chat_json(require_dict=True)` **retries** a list response rather than
  returning it, and its final error names the shape, not "unparseable";
- `chat_json(require_dict=True, temperature=0)` raises on the **first** list
  response without retrying — assert the call count, not just the exception,
  since the wrong behaviour here still ends in a `ValueError` naming the shape
  and is invisible to an exception-only assertion. Mirror
  `test_truncated_at_temperature_zero_fails_fast` (`test_agents.py:153`), which
  asserts both `agent.llm.chat.call_count == 1` and
  `mock_sleep.assert_not_called()`; pair it with a temperature > 0 twin
  asserting `call_count == 3`, as
  `test_truncated_at_nonzero_temperature_retries_then_names_cause` does;
- `chat_json(require_dict=True)` rejects a list that arrives via the
  truncation path's `_try_parse`;
- default `chat_json` still returns a list unchanged (non-breaking).

## Documentation

- `CHANGELOG.md` under `[Unreleased]`: the widened contract, the new keyword,
  and the extraction change — flagged as a behaviour change for anyone whose
  prompts produce an array **of objects** embedded in prose, since they will
  now receive the whole array where they previously received its first
  element. Scope the note to that shape: a fenced array already came back
  whole, a bare array never reached `extract_json()`, and an array of scalars
  had no nested candidate to lose to.
- `docs/manual/agents.md` and `docs/manual/llm.md`: the contract and
  `require_dict`. Three spots in `agents.md` state the current contract
  outright and will otherwise survive the change intact:
  - `:252` — the literal signature block `def parse_json(text: str) -> dict`;
  - `:207` — the failure-mode table, which has a single row mapping
    `parse_json()` raising `ValueError` to the log message `"unparseable
    response"`. The whole reason `chat_json` does its own `isinstance` check
    instead of catching that `ValueError` is that these are two distinct
    diagnoses, so the table needs a second row for the shape failure;
  - `:18` — the one-line summary of `parse_json()` in the method table.
- `HANDOVER.md` deliberate-non-fix list, **two edits, not one**:
  - a new entry for the two-walk policy — specifically that collapsing it back
    to a single walk restores the silent truncation, that reducing the ranked
    fallback to first-parseable is the *other* way to restore it (and a worse
    one, since it substitutes unrelated data rather than truncating), and that
    `extract_and_repair_json` deliberately has no equivalent second walk;
  - an amendment to the existing `extract_and_repair_json() passes
    nested_objects=False` entry. It currently justifies itself with
    *"`extract_json()` keeps the nested stage, because it only ever
    validates"* — the very reasoning this design demotes. The stage does
    survive, so the entry is not wrong, but left as written the list holds two
    entries whose rationales read as contradictory. Reword it to say
    `extract_json()` keeps the nested stage *as a last resort only*, and that
    the asymmetry with repair is now about fabricating structure rather than
    about validate-versus-repair licence generally.

## Verification

`uv run pytest tests/ -v`, then `uvx ruff@0.15.20 check . && uvx ruff@0.15.20
format --check .` — the CI-pinned ruff, not the older one in `.venv`.
