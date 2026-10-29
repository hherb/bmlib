# bmlib.context_processor — Processing More Than One Context Holds

Hierarchical map-reduce over content that exceeds an LLM's context window.
Batch the items to fit, extract from each batch, then feed the extractions
back in as items and repeat until what remains fits in a single context.

The alternative — truncating the input — loses information silently, and
gives no way to tell an answer drawn from everything apart from one drawn
from the first 4,000 characters.

**Available since 0.7.0.**

## When to use this rather than `bmlib.llm.text_utils`

| Need | Use |
|------|-----|
| Split one long *string* and run one map + one reduce over its chunks | [`process_with_map_reduce()`](llm.md) |
| Summarise one long string progressively | [`process_with_rolling_summary()`](llm.md) |
| A *list of items* of any type, recursion to any depth, per-batch failure accounting, and a policy for items too big to fit alone | **this module** |

`text_utils` is the shallow case; this is the general one. The processor
uses `TextChunker` from `text_utils` internally when it has to split an
oversized item, so pieces still break on paragraph and sentence boundaries.

## Imports

Every name in `bmlib.context_processor.__all__`:

```python
from bmlib.context_processor import (
    IterativeContextProcessor,   # the harness (abstract)
    LLMChunkProcessor,           # ready-made subclass, extracts via BaseAgent
    ScoredChunk,                 # type alias: tuple[str, float]
    ProgressCallback,            # type alias: Callable[[ProgressInfo], None]
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
    ExtractionResult,
    ConsolidatedItem,
    Batch,
    OversizedItemStrategy,
    OversizedItemError,          # raised by the FAIL strategy
    ConsolidationStrategy,
    ProgressInfo,
    # ProcessingConfig's defaults
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_OVERLAP_CHARS,
    DEFAULT_MAX_RECURSION_DEPTH,
    DEFAULT_MIN_ITEMS_FOR_RECURSION,
    DEFAULT_SEPARATOR,
    # LLMChunkProcessor's prompt templates
    DEFAULT_EXTRACTION_PROMPT,
    DEFAULT_CONSOLIDATION_PROMPT,
    STRUCTURED_EXTRACTION_PROMPT,
)
```

No optional dependency: the core is pure Python. `LLMChunkProcessor` needs
whatever provider extra its agent's model uses, and is resolved only when
you name it — importing the package for `IterativeContextProcessor` alone
does not pull in `BaseAgent`, `bmlib.templates` or jinja2. The same holds
for the other names that live beside it — `ScoredChunk` and the three
prompt templates.

## Quick start

```python
from bmlib.agents import BaseAgent
from bmlib.llm import LLMClient
from bmlib.context_processor import LLMChunkProcessor, ProcessingConfig

agent = BaseAgent(llm=LLMClient(), model="ollama:gpt-oss:20b")
processor = LLMChunkProcessor(
    agent,
    config=ProcessingConfig(max_context_chars=8000),
)

# Chunks from a semantic search: (text, relevance score)
result = processor.process(chunks, query="What outcomes were reported?")

print(result.content)          # the consolidated answer
print(result.status)           # COMPLETED / PARTIAL / TRUNCATED / FAILED
print(result.batches_created)  # batches extracted, across all levels
```

`batches_created` counts batches, not model calls: with
`use_structured_output` a batch whose JSON needs repairing costs the agent
more than one call. Read `agent.metrics.total_requests` for that.

## The algorithm

1. **Pack.** Items are greedily packed into batches whose *formatted*
   content fits `max_context_chars`. Each item is measured at the position
   it actually lands in, so a `format_item()` that renders the index is
   measured correctly.
2. **Extract.** `extract_from_batch()` is called once per batch.
3. **Recurse.** If the extractions joined together still exceed
   `max_context_chars`, each is wrapped in a `ConsolidatedItem` and the
   whole process runs again over those. Otherwise they are merged and
   returned.
4. **Stop.** At `max_recursion_depth` the run returns what it has with
   status `TRUNCATED`. It also stops early when fewer than
   `min_items_for_recursion` results survive — a single result has nothing
   to be consolidated *with*, so recursing would only re-summarise it.

`max_context_chars` is the promise the module makes: no batch handed to
`extract_from_batch()` exceeds it.

## Writing a processor

Two methods are required.

```python
from bmlib.context_processor import (
    ExtractionResult,
    IterativeContextProcessor,
)

class AbstractProcessor(IterativeContextProcessor):
    def format_item(self, item, index: int) -> str:
        """Render one item into the batch."""
        return f"[{index + 1}] {item['title']}\n{item['abstract']}"

    def extract_from_batch(self, batch_content, query, batch_metadata):
        """Answer *query* from one batch's worth of content."""
        answer = my_model(f"{query}\n\n{batch_content}")
        return ExtractionResult(content=answer, confidence=0.8)
```

Two more are optional:

| Method | Default | Override when |
|--------|---------|---------------|
| `format_consolidated_item(item, index)` | the content alone | you want the level a summary came from shown to the model |
| `split_oversized_item(item, max_chars, overlap)` | handles `str` and `ConsolidatedItem` | your items are neither |

An item type that cannot be split raises `NotImplementedError` from the
default, which the harness turns into a skip (recorded on
`ProcessingResult.skipped_items`) rather than a failed run.

`extract_from_batch()` receives the batch's formatted, joined content (never
longer than `max_context_chars`) and a fresh `batch_metadata` dict holding
`batch_index`, `item_count`, `total_chars`, `item_indices` and
`recursion_level`. The harness fills in the returned result's
`batch_index`, `recursion_level` and `source_indices` itself; anything the
method raises is recorded against the batch (see `continue_on_error`).

### Running it

```python
processor = AbstractProcessor(config=None, progress_callback=None)

result = processor.process(
    items,                      # list of whatever format_item() understands
    query="...",
    config=None,                # overrides the instance's config for this call
    store_intermediate=False,   # keep each level's results on the result
)
```

The constructor takes `config: ProcessingConfig | None` (defaults when
omitted) and `progress_callback: ProgressCallback | None`. `process()`
holds no per-run state on the instance, so one processor can serve
concurrent calls. An empty `items` list returns at once with status
`COMPLETED`, empty content and confidence 0.0, without calling the
extractor or the progress callback.

### What an "item" is changes with the level

Level 0 processes the caller's items. Every level above processes
`ConsolidatedItem`s — the results of the level below. They are routed to
`format_consolidated_item()`, so `format_item()` only ever sees the
caller's own type and never has to sniff for shapes it did not produce.

```python
def format_consolidated_item(self, item, index: int) -> str:
    level = item.metadata.get("recursion_level", 0)
    return f"[Summary from level {level}]\n{item.content}"
```

`recursion_level` is always present in a `ConsolidatedItem`'s metadata:
the harness knows the level and puts it there, whether or not the
extractor copied its batch metadata forward.

## `ProcessingConfig`

Frozen — every batching decision reads it, and mutating it mid-run would
leave the recorded statistics describing a configuration that never ran.
Pass `config=` to `process()` to vary it per call.

| Field | Default | Meaning |
|-------|---------|---------|
| `max_context_chars` | `DEFAULT_MAX_CONTEXT_CHARS` = 4000 | Characters allowed in one batch's formatted content. Must be positive |
| `overlap_chars` | `DEFAULT_OVERLAP_CHARS` = 0 | Overlap between pieces of a split item. Non-negative and at most half of `max_context_chars`: the stride is the difference, and the piece count grows without bound as it shrinks |
| `max_recursion_depth` | `DEFAULT_MAX_RECURSION_DEPTH` = 5 | Consolidation levels before giving up with `TRUNCATED`. Non-negative |
| `min_items_for_recursion` | `DEFAULT_MIN_ITEMS_FOR_RECURSION` = 2 | Below this many results, stop rather than re-summarise. At least 1 |
| `separator` | `DEFAULT_SEPARATOR` = `"\n\n---\n\n"` | Joins items in a batch, and results in a merge |
| `preserve_metadata` | `True` | Carry each result's metadata into the merged result |
| `oversized_item_strategy` | `SPLIT` | See below |
| `consolidation_strategy` | `CONCATENATE` | See below |
| `continue_on_error` | `True` | Record a failed batch and carry on |
| `min_confidence_threshold` | 0.0 | Drop results below this confidence before merging. Between 0.0 and 1.0 |

A bound violated at construction raises `ValueError` naming the field.

### `OversizedItemStrategy`

What to do with an item that does not fit **on its own**, measured after
`format_item()` has decorated it.

| Value | Behaviour |
|-------|-----------|
| `SPLIT` | Cut it into pieces that fit. The budget passed to `split_oversized_item()` already allows for the decoration, measured rather than guessed |
| `TRUNCATE` | Keep the leading part; the remainder is lost. The kept part is *not* decorated a second time |
| `SKIP` | Drop it, recording its index on `ProcessingResult.skipped_items` |
| `FAIL` | Raise `OversizedItemError` (a `ValueError`). Through `process()` that surfaces as status `FAILED` with the reason on `error_message`, like every other failure — but reported as the configuration working, not as an unexpected error with a traceback |

### `ConsolidationStrategy`

| Value | Behaviour |
|-------|-----------|
| `CONCATENATE` | Join in order |
| `WEIGHTED` | Join most-confident first; the merged confidence is weighted by content length |
| `DEDUPLICATE` | Drop results whose content repeats (case-insensitive, ignoring leading and trailing whitespace) before joining |

Under `CONCATENATE` and `DEDUPLICATE` the merged confidence is the plain
mean over every surviving result. Error results and results below
`min_confidence_threshold` are dropped first; when nothing survives, the
merged result is empty with confidence 0.0 and metadata
`{"all_filtered": True, "original_count": n}`. With `preserve_metadata` a
merge of several results carries `merged_from`, `filtered_count`,
`consolidation_strategy` and `source_metadata` (each result's metadata, in
order); a single surviving result is returned as a copy of itself.

## `ExtractionResult`, `ConsolidatedItem`, `Batch`

`ExtractionResult` is what `extract_from_batch()` returns:

| Field | Default | Meaning |
|-------|---------|---------|
| `content` | *(required)* | The extracted text |
| `metadata` | `{}` | Anything the extractor wants carried forward |
| `source_indices` | `[]` | Items behind this result; filled in by the harness |
| `confidence` | 1.0 | 0.0–1.0; read by `WEIGHTED` and `min_confidence_threshold` |
| `batch_index` | `None` | Filled in by the harness |
| `recursion_level` | 0 | Filled in by the harness |
| `is_error` | `False` | True for the stand-in the harness records for a failed batch |
| `error_message` | `None` | Why, when `is_error` |

Properties: `content_length`, and `is_valid` — not an error and carrying
content; only valid results are merged or carried up a level.

`ConsolidatedItem(content, metadata={})` is a result fed back in as an item
for the level above (see [above](#what-an-item-is-changes-with-the-level)).

`Batch(items, item_indices=[], total_chars=0, batch_index=0)`, with a `size`
property, is the batcher's own carrier. It is exported for annotation;
nothing public hands you one — `extract_from_batch()` gets the formatted
content and `batch_metadata` instead. Its `total_chars` equals the length of
that content.

## `ProcessingResult`

```python
result = processor.process(items, query="...")

result.final_result           # ExtractionResult
result.content                # final_result.content
result.status                 # ProcessingStatus
result.error_message          # why, when the run failed
result.is_complete            # status is COMPLETED
result.is_partial             # status is PARTIAL
result.has_failures           # any failed batch or skipped item
result.success_rate           # successful_batches / batches_created
result.total_items_processed  # len(items)
result.batches_created        # batches across every level
result.successful_batches
result.failed_batches         # batch indices whose extraction raised
result.skipped_items          # item indices dropped as oversized
result.recursion_levels_used  # consolidation passes beyond the first
result.intermediate_results   # per-level results, or None unless store_intermediate
result.processing_stats       # {"total_items", "batches_per_level", "items_per_level"}
```

Batch indices restart at each level, so a value in `failed_batches` can
repeat in a run that recursed; likewise `skipped_items` above level 0
indexes that level's consolidated items, not the caller's list.

A run with no batches has no ratio to report, so `success_rate` answers
from what was lost: 1.0 for an empty input, which had nothing that could
fail, and 0.0 when every item was dropped as oversized before a batch
could be built.

### Status

| Status | Meaning |
|--------|---------|
| `COMPLETED` | Everything processed and consolidated |
| `PARTIAL` | Some batches failed or items were skipped, but at least one batch succeeded |
| `TRUNCATED` | The recursion ceiling was reached; the result is what fitted |
| `FAILED` | Every batch failed, or `continue_on_error=False` and one did |

`ProcessingStatus` also has `PENDING` and `IN_PROGRESS`; `process()` never
returns either. A `TRUNCATED` run stays `TRUNCATED` even when batches also
failed.

Failures are reported on the result, not raised — including an unexpected
error inside `format_item()`. Check the status; do not assume success.

## `LLMChunkProcessor`

The ready-made subclass. Accepts plain strings or `(text, score)` tuples
(`ScoredChunk`, `tuple[str, float]`) — the shape a semantic search returns —
and runs every model call through
a `BaseAgent`, so token accounting, retries and JSON repair are the ones
the rest of bmlib uses.

```python
processor = LLMChunkProcessor(
    agent,                          # BaseAgent — carries the model
    extraction_prompt=None,         # template for level 0
    consolidation_prompt=None,      # template for every level above
    config=None,
    progress_callback=None,
    use_structured_output=False,
    temperature=None,               # None → the agent's own default
    max_tokens=None,
)
```

Prompt templates must contain `{query}` and `{content}`; a template
missing either raises `ValueError` at construction. Substitution is by
replacement, not `str.format()`, so a template may carry literal braces —
a JSON example, a regex — without doubling them.

Level 0 uses the extraction prompt, every level above the consolidation
prompt, which asks the model to merge rather than extract. The defaults are
exported, so a custom template can start from one:

| Constant | Used |
|----------|------|
| `DEFAULT_EXTRACTION_PROMPT` | `extraction_prompt=None` without structured output |
| `STRUCTURED_EXTRACTION_PROMPT` | `extraction_prompt=None` with `use_structured_output=True`; asks for the JSON below |
| `DEFAULT_CONSOLIDATION_PROMPT` | `consolidation_prompt=None` |

Items are rendered under a one-line header — `[Chunk 1, score 0.87]` for a
scored chunk, `[Item 1]` for a string, `[Consolidated level 0, item 1]` for
a level above — and a scored chunk that has to be split keeps its score on
every piece. A tuple whose score is a `bool` is not a scored chunk. A model
call that fails is raised as `RuntimeError` and recorded against its batch;
prose extraction records confidence 0.9.

### Structured output

With `use_structured_output=True`, level 0 asks for JSON and reads the
model's own confidence and key findings from it, through the agent's JSON
repair and retry:

```json
{
  "extracted_content": "...",
  "confidence": 0.9,
  "key_findings": ["...", "..."]
}
```

A confidence outside 0.0–1.0 is clamped, and an unusable one falls back to
0.9 — `min_confidence_threshold` and the weighted merge both assume the
range. A non-empty `key_findings` list is kept on the result's metadata
under `key_findings`. The call is `chat_json(require_dict=True)`, so a
top-level array is retried, not accepted. Consolidation levels always use
prose: asking for JSON there would demand the model re-wrap a summary it was
told to write as text.

## Progress reporting

```python
def show(info):
    print(f"{info.stage}: {info.message} ({info.progress_percent:.0f}%)")

processor = LLMChunkProcessor(agent, progress_callback=show)
```

The callback's type is exported as `ProgressCallback`
(`Callable[[ProgressInfo], None]`). `ProgressInfo` carries `stage`,
`current_item`, `total_items`, `current_batch` (1-based), `total_batches`,
`recursion_level` and `message`, and the derived `progress_percent`
(0.0–100.0; 0.0 when `total_items` is 0).

Stages are `starting`, `batching`, `extracting`, `recursing`, `complete`.
An exception raised by the callback is logged and swallowed — a broken
progress bar must not lose the work.

`progress_percent` is `current_item / total_items` **within the current
level**, so it restarts at each `recursing` step — `recursion_level` says
which level a given update belongs to. An item dropped by the oversized
strategy counts as accounted for the moment the batcher drops it: no
extraction will ever reach it, and a bar waiting for one would never
fill.

## Notes for maintainers

The port from bmlibrarian fixed four defects; each has a regression test
named for it in `tests/test_context_processor.py`.

- **The bin-packing runs once per level.** Upstream re-ran it purely to
  count the batches for its statistics, re-formatting every item and
  re-splitting every oversized one. `_process_level()` returns the count
  it already has.
- **Split budgets are measured, not assumed.** A piece cut to
  `max_chars` of *raw* text exceeds the limit once `format_item()`
  decorates it. `_split_to_fit()` measures the overflow and reduces the
  budget by exactly that much, then verifies.
- **`TRUNCATE` does not decorate twice.** The truncated text is wrapped so
  the batcher renders it as-is.
- **Items are measured where they land.** An item that starts a new batch
  is re-measured at index 0 of that batch, so `total_chars` equals the
  length of the content the extractor actually receives.

Two further properties are pinned by tests rather than left to reading:
`tests/test_context_processor.py::TestTheContextLimitIsNeverExceeded`
asserts the limit from *inside* `extract_from_batch` — the only place that
sees what a model would actually be sent — across every oversized strategy
and the recursion levels where `format_consolidated_item()` decorates
instead of `format_item()`, and includes a negative control proving the
assertion can fail. `TestPackageImports` pins that importing the package
does not pull in the LLM stack.

`estimate_item_size()` from upstream was deliberately **not** ported: the
batcher must call `format_item()` on every item anyway, so the estimate
saved nothing while allowing the oversized decision and the packing
measurement to disagree — an underestimated item was never split and
silently overflowed its batch.

Design record:
`docs/superpowers/specs/2026-08-02-context-processor-design.md`.
