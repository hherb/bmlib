# BaseAgent Metrics + Consolidated JSON Extraction — Design

**Date:** 2026-07-28
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #17
**Roadmap:** Phase 1 of the bmlibrarian → bmlib port
(`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`, item 15 — the keystone
that unblocks the Phase 4 agent family)

## Problem

Two related bodies of work, both centred on `bmlib/agents/base.py`.

**1. `BaseAgent` is thinner than the agents that need to port onto it.**
`~/src/bmlibrarian/src/bmlibrarian/agents/base.py` (~1271 lines) carries
per-agent performance accounting, an embedding helper, and a connection test
that the prompt-driven agent family depends on. Porting those agents without
these first means each one grows its own copy.

**2. JSON extraction is implemented twice inside bmlib (issue #17).**
`llm/utils.py::extract_json` and `llm/json_repair.py::extract_and_repair_json`
each hand-roll fence detection and escape-aware brace scanning.
`BaseAgent.parse_json` runs both in sequence. The duplication is not
agent-only: the Anthropic and OpenAI-compatible providers call `extract_json`
on **every** `json_mode` response, so any change has to hold there too.

`extract_json` has **no direct tests today** — it is covered only indirectly
through `parse_json` and the provider paths.

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| Metrics vs global `TokenTracker` | Per-agent, independent | Feed the global tracker; opt-in bridge; skip metrics |
| `chat_json` retry scope | **No behaviour change** — logging label + retry counting only | Retry transient errors; opt-in retry flag |
| Issue #17 | **One shared locator** | Share primitives only, keep two pipelines |
| Cross-project consolidation | **Adopt all three harvested ideas** | Defer salvage; dict-preference only |

## Cross-project survey (why this is best-of-breed)

Five JSON implementations exist across the projects. bmlib's repair stage is
already the strongest; the survey found three ideas worth taking and nothing
else.

**Ancestors or strictly weaker — nothing to harvest:**

- `bmlibrarian/utils/json_repair.py` — direct ancestor of bmlib's, identical
  function list, dated 2025-11-23. bmlib's has since been hardened
  (state-machine single-quote handling, O(1) `prev_nonspace` tracking).
  Redundant today.
- `bmlibrarian/paperchecker/components/statement_extractor._extract_json` —
  ancestor of `extract_and_repair_json`'s locator, no repair stage.
- `bmlibrarian_lite/llm/providers/anthropic._extract_json` — bmlib's
  `extract_json` with a greedy `re.search(r"\{.*\}")` instead of balanced
  scanning: the exact defect bmlib's own docstring calls out.
- `biasbuster/biasbuster/annotators/repair_json` — regex-only, and carrying a
  real bug: its quote-balance loop indexes `text[text.index(ch) - 1]`, which
  looks up the *first* occurrence of that character rather than the current
  position, so the check is meaningless on any text with a repeated quote.
- `biasbuster/studies/.../extract_json_object`, `eval_ollama._extract_json_object`
  — balanced scanners equivalent to bmlib's, object-only.

**Adopted:**

1. **Field-level salvage** — biasbuster's `lenient_extract` ("Strategy B").
   When the document will not parse as a whole, recover the load-bearing
   fields individually. Its docstring names the real case: the model
   malformed only the `evidence_quotes` array at the tail while `judgement`
   and `justification` were intact. bmlib today is all-or-nothing and loses
   the whole response.
2. **A repaired response is a truncation signal** — biasbuster hand-rolls
   `REQUIRED_ANNOTATION_FIELDS` because repair closes the braces and returns a
   *valid but half-empty* object. bmlib already computes this
   (`extract_and_repair_json` returns `was_repaired`); `parse_json` discards
   it.
3. **`isinstance(result, dict)` before accepting a candidate** —
   biasbuster's `assessment_decomposed` guards this at all three of its parse
   stages. Convergent evolution on the same gap: `parse_json` is annotated
   `-> dict` but can return a list.

**Migration:** bmlibrarian depends on `bmlib[ollama]>=0.5.1,<0.6.0`,
BioMedicalNews on bmlib from git, biasbuster on a local editable `bmlib[all]`.
Only bmlibrarian_lite is standalone. "Delete your copy, import from bmlib"
becomes a small PR in each once this ships. bmlibrarian's `<0.6.0` pin needs
lifting when the next release is cut.

## Goals

- `BaseAgent` gains per-agent metrics, embeddings, and a connection test,
  without acquiring hidden global state.
- One JSON span locator, shared by both public extractors.
- bmlib becomes a strict superset of all five implementations.
- Every existing test stays green **unchanged**, except where a behaviour
  delta is named below. That is the acceptance criterion, not a hope.

## Non-goals

- Queue/orchestrator hooks, `set_callback`, `_get_ollama_options`,
  `get_agent_type` (ABC), `_display_model_info`, `get_available_models` — all
  dropped. bmlib's `BaseAgent` stays injection-only.
- Upstream's `_parse_json_response` — bmlib's `parse_json` is strictly better
  (it has a repair stage).
- Changing what `chat_json` retries.
- Migrating the downstream apps (separate PRs in their own repos, blocked on a
  bmlib release).

## Design

### 1. `bmlib/agents/metrics.py` (new) — `PerformanceMetrics`

```python
@dataclass
class PerformanceMetrics:
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    total_retries: int = 0
    total_wall_time_seconds: float = 0.0
    start_time: float | None = None
    end_time: float | None = None
```

Methods: `add_request(prompt_tokens, completion_tokens, wall_time_seconds)`,
`add_retry()`, `mark_start()`, `mark_end()`, `reset()`, `snapshot()`,
`to_dict()` / `from_dict()`, `format_report(title=None)`.
Properties: `elapsed_time_seconds`, `tokens_per_second`,
`average_tokens_per_request`.

**Dropped from upstream:** `total_model_time_seconds` and
`total_prompt_eval_seconds`. Upstream reads those from Ollama's raw nanosecond
fields; nothing reaches bmlib's `LLMResponse` — `duration_seconds` is declared
but **no provider populates it** — so both would be permanently `0.0` and
every derived report would quietly lie. `tokens_per_second` therefore divides
by wall time, which is the throughput the caller actually observed.

**`format_report` lives on the dataclass**, not on `BaseAgent`: upstream's
version calls an abstract `get_agent_type()`, and bmlib's `BaseAgent` is
deliberately not an ABC. `BaseAgent.format_metrics_report()` delegates,
passing `type(self).__name__`.

**Thread-safe.** `+=` is a read-modify-write, and `TransparencyAnalyzer` sets
the precedent that a bmlib object may be shared across workers. An internal
`threading.Lock` (`field(repr=False, compare=False)`) guards mutation and
snapshot reads; it is excluded from `to_dict()`.

### 2. `bmlib/agents/base.py`

- `self._metrics = PerformanceMetrics()`. The `metrics` property returns a
  **snapshot copy** taken under the lock — a caller can neither corrupt the
  live counters nor read a torn set. `reset_metrics()` / `start_metrics()` /
  `stop_metrics()` mutate the live object.
- `chat()` times each call with `time.monotonic()` and records tokens + wall
  time **on success only**. A raised call records nothing; documented, not
  silent.
- `chat_json(..., retry_context: str = "")` — the label is folded into the
  existing retry/error log lines so a failure says which task produced it.
  Each retry iteration calls `add_retry()`, so `total_requests` counts
  attempts and `total_retries` counts the extra ones. **No change to what is
  retried.**
- `embed(text, model=None) -> list[float]` — routes to `LLMClient.embed()`,
  resolves `model or self.embedding_model`, raises `ValueError` on an empty
  vector. `embed_batch(texts, model=None, max_batch_size=None)` alongside it;
  an empty list short-circuits without contacting the provider.
  Embeddings are deliberately **not** recorded into `PerformanceMetrics` —
  mixing them in would distort `tokens_per_second`, which is about generation.
- `test_connection() -> bool` — provider parsed from `self.model`, delegated
  to `LLMClient.test_connection(provider)`. Reachability only; model
  availability stays `llm.list_models(provider)`. Upstream's Ollama
  model-membership check costs a second round-trip and false-negatives on
  cloud models.
- `__init__` gains `embedding_model: str | None = None`, **appended last** —
  same positional-stability rule as `Publication.pmcid`.
- `parse_json` logs at WARNING when the repair stage was what rescued the
  response: a repaired response is the truncation signal biasbuster hand-rolls
  a required-field set to detect.

### 3. `bmlib/llm/utils.py` — one locator

```python
def iter_json_spans(text: str) -> Iterator[str]:
    """Yield candidate JSON spans in priority order, without validating."""
    # 1. ```json fence contents, document order
    # 2. other fence contents whose body starts with { or [
    # 3. remaining fence contents
    # 4. balanced {...} / [...] spans, document order, escape-aware
    # 5. brace-only balanced spans not already yielded by stage 4
    # 6. opener-to-end tail — only when 4 and 5 yielded nothing (truncation)
```

Stage 4 counts only the pair type of the span's own opener, which reproduces
today's `_iter_balanced_objects` for `{` and the repair path's scan for a
leading `[`.

Stage 5 is load-bearing, not belt-and-braces. On `[{"a":1}]` stage 4 yields
only the outer array — the object is swallowed inside it — so dict-preference
would return the array where today's brace-only scan returns `{"a":1}`. That
is a silent change in every provider's `json_mode` path. Stage 5 re-offers the
nested object as a lower-priority candidate, and today's outcome is preserved.

Both public functions collapse to a policy over that generator:

- `extract_json(text)` — first candidate that parses **to a dict**; failing
  that, the first that parses at all; failing that, *text* unchanged. A
  truncated tail never parses, so stage 5 self-excludes and needs no flag.
- `extract_and_repair_json(text, repair=True)` — first candidate that
  validates **or repairs**, walking on to the next when one does neither;
  `ValueError` once they are exhausted. The `(span, was_repaired)` return is
  unchanged.

Both policies are one-liners over the same generator, with no stage tagging.
That matters: "take the first candidate and raise" would have been wrong for
`extract_and_repair_json`, because today it *rejects* a bare fence whose body
does not start with `{`/`[` and falls through to the brace scan. Walking the
candidates reproduces that without the generator having to explain which stage
a span came from.

`json_repair.py` sheds roughly 50 lines of hand-rolled scanning.

#### Behaviour deltas (deliberate, each pinned by a test)

1. **Bare top-level arrays become visible to `extract_json`.** No observable
   change in `parse_json` — today it reaches the same array one stage later
   via the repair fallback. In the providers it is a **fix**: today a model
   returning `[...]` wrapped in prose gets the prose handed back unchanged.
2. **Dict-preference.** On `[1,2] {"a":1}`, `extract_json` returns the object.
   Today the object also wins (its brace scan is object-only), so this
   preserves today's outcome under the widened stage 4 — which is exactly why
   dict-preference is the right resolution rather than document order.
   Together with stage 5 it also preserves `[{"a":1}]` → `{"a":1}`. That
   inherits today's silent drop of any siblings in `[{"a":1},{"b":2}]`;
   changing it is a contract decision, filed as a follow-up rather than
   smuggled in here.
3. **Fence priority.** A bare fence containing parseable junk ahead of a
   ` ```json ` fence no longer wins. Pathological; strictly better.
4. **`extract_and_repair_json` no longer stakes everything on one span.**
   Today it picks a single span and raises if that span will not repair; now
   it moves to the next candidate. Strictly more robust, and no existing test
   distinguishes the two — each of them has exactly one plausible span.

### 4. `bmlib/llm/json_repair.py` — `salvage_json_fields`

```python
def salvage_json_fields(text: str, keys: Iterable[str]) -> dict[str, Any]:
    """Best-effort recovery of individual top-level fields.

    Returns whatever could be located; never raises, returns {} on total
    failure.
    """
```

For each key, scan `re.finditer(r'"<key>"\s*:', text)` and hand the position
after the colon to `json.JSONDecoder().raw_decode()`. That parses exactly one
JSON value of any type and reports where it ended — it handles escapes,
nesting and numbers natively, which is strictly better than biasbuster's
per-type regexes and its `json.loads('"' + raw + '"')` escape trick. When
`raw_decode` fails (a truncated value at the tail — the motivating case), the
substring from the value's start to end of text is passed through
`repair_json()` and decoded once more.

**Not wired into `parse_json`.** Silently returning partial data would turn a
loud failure into a quiet wrong answer. Callers opt in explicitly after
catching the `ValueError`.

Known limitation, documented: a key name appearing inside a string value can
be matched. Acceptable for best-effort salvage on a document that has already
failed to parse.

### 5. Exports

`bmlib.agents`: `PerformanceMetrics`. `bmlib.llm`: `iter_json_spans`,
`salvage_json_fields`. `extract_json` stays reachable at
`bmlib.llm.utils.extract_json` as today.

## Testing

TDD throughout: behaviour tests first, watched fail, then implementation.

**`tests/test_agents.py`** — accumulation across `chat`/`chat_json`; retries
counted separately from requests; the `metrics` snapshot is isolated from the
live object; a failed call records nothing; `reset`/`start`/`stop`;
`format_report`; `to_dict`/`from_dict` round-trip; `tokens_per_second` on wall
time plus its zero-division guards; `embed` empty-vector `ValueError`;
`embed_batch` empty-list short-circuit; `test_connection` true and false;
`retry_context` present in the log record (`caplog`); the repair-stage WARNING.

**Thread safety** — N threads × M `add_request` calls produce exact totals,
same shape as the existing backends thread test.

**`tests/test_json_extraction.py` (new)** — `iter_json_spans` stage ordering
and escape handling; characterisation tests pinning the four divergences the
old pipelines had, so a future "obvious" unification fails loudly instead of
silently changing provider behaviour; the three deltas above; `extract_json`
gets direct coverage for the first time; `salvage_json_fields` including the
truncated-tail case, a missing key, and a key inside a string value.

Existing `tests/test_json_repair.py` stays unchanged and must stay green.

## Documentation

`docs/manual/agents.md` (metrics, embed, test_connection, retry_context),
`docs/manual/llm.md` (`iter_json_spans`, `salvage_json_fields`, revised
extractor comparison table), `CHANGELOG.md` under `[Unreleased]`, `CLAUDE.md`
(agents module description + the new test-file mapping row), then
`HANDOVER.md` / `ROADMAP.md`.

## Follow-ups to file as issues

- `BaseAgent.parse_json` is annotated `-> dict` but can return a list when a
  response contains no JSON object at all. True today as well; dict-preference
  narrows it but does not close it. Deciding between a runtime guard and a
  widened annotation is a separate, breaking-ish call.
- Downstream migration: delete the copies in bmlibrarian, biasbuster and
  bmlibrarian_lite in favour of `bmlib.llm`.
