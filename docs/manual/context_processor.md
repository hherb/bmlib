# bmlib.context_processor — Processing More Than One Context Holds

Hierarchical map-reduce over content that exceeds an LLM's context window.
Batch the items to fit, extract from each batch, then feed the extractions
back in as items and repeat until what remains fits in a single context.

The alternative — truncating the input — loses information silently, and
gives no way to tell an answer drawn from everything apart from one drawn
from the first 4,000 characters.

**Available since (unreleased).**

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

```python
from bmlib.context_processor import (
    IterativeContextProcessor,   # the harness (abstract)
    LLMChunkProcessor,           # ready-made subclass, extracts via BaseAgent
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
    ExtractionResult,
    ConsolidatedItem,
    OversizedItemStrategy,
    ConsolidationStrategy,
    ProgressInfo,
)
```

No optional dependency: the core is pure Python. `LLMChunkProcessor` needs
whatever provider extra its agent's model uses.

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
print(result.batches_created)  # model calls made, across all levels
```

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

Three more are optional:

| Method | Default | Override when |
|--------|---------|---------------|
| `format_consolidated_item(item, index)` | the content alone | you want the level a summary came from shown to the model |
| `split_oversized_item(item, max_chars, overlap)` | handles `str` and `ConsolidatedItem` | your items are neither |

An item type that cannot be split raises `NotImplementedError` from the
default, which the harness turns into a skip (recorded on
`ProcessingResult.skipped_items`) rather than a failed run.

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
| `max_context_chars` | 4000 | Characters allowed in one batch's formatted content |
| `overlap_chars` | 0 | Overlap between pieces of a split item. Must be less than `max_context_chars` |
| `max_recursion_depth` | 5 | Consolidation levels before giving up with `TRUNCATED` |
| `min_items_for_recursion` | 2 | Below this many results, stop rather than re-summarise |
| `separator` | `"\n\n---\n\n"` | Joins items in a batch, and results in a merge |
| `preserve_metadata` | `True` | Carry each result's metadata into the merged result |
| `oversized_item_strategy` | `SPLIT` | See below |
| `consolidation_strategy` | `CONCATENATE` | See below |
| `continue_on_error` | `True` | Record a failed batch and carry on |
| `min_confidence_threshold` | 0.0 | Drop results below this confidence before merging |

### `OversizedItemStrategy`

What to do with an item that does not fit **on its own**, measured after
`format_item()` has decorated it.

| Value | Behaviour |
|-------|-----------|
| `SPLIT` | Cut it into pieces that fit. The budget passed to `split_oversized_item()` already allows for the decoration, measured rather than guessed |
| `TRUNCATE` | Keep the leading part; the remainder is lost. The kept part is *not* decorated a second time |
| `SKIP` | Drop it, recording its index on `ProcessingResult.skipped_items` |
| `FAIL` | Raise `ValueError`. Through `process()` that surfaces as status `FAILED` with the reason on `error_message`, like every other failure |

### `ConsolidationStrategy`

| Value | Behaviour |
|-------|-----------|
| `CONCATENATE` | Join in order |
| `WEIGHTED` | Join most-confident first; the merged confidence is weighted by content length |
| `DEDUPLICATE` | Drop results whose content repeats (case- and whitespace-insensitive) before joining |

## `ProcessingResult`

```python
result = processor.process(items, query="...")

result.content                # final_result.content
result.status                 # ProcessingStatus
result.is_complete            # nothing failed, skipped, or truncated
result.has_failures           # any failed batch or skipped item
result.success_rate           # successful_batches / batches_created
result.failed_batches         # batch indices whose extraction raised
result.skipped_items          # item indices dropped as oversized
result.recursion_levels_used
result.processing_stats       # per-level batch and item counts
```

### Status

| Status | Meaning |
|--------|---------|
| `COMPLETED` | Everything processed and consolidated |
| `PARTIAL` | Some batches failed or items were skipped, but at least one batch succeeded |
| `TRUNCATED` | The recursion ceiling was reached; the result is what fitted |
| `FAILED` | Every batch failed, or `continue_on_error=False` and one did |

Failures are reported on the result, not raised — including an unexpected
error inside `format_item()`. Check the status; do not assume success.

## `LLMChunkProcessor`

The ready-made subclass. Accepts plain strings or `(text, score)` tuples
— the shape a semantic search returns — and runs every model call through
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
prompt, which asks the model to merge rather than extract.

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
range. Consolidation levels always use prose: asking for JSON there would
demand the model re-wrap a summary it was told to write as text.

## Progress reporting

```python
def show(info):
    print(f"{info.stage}: {info.message} ({info.progress_percent:.0f}%)")

processor = LLMChunkProcessor(agent, progress_callback=show)
```

Stages are `starting`, `batching`, `extracting`, `recursing`, `complete`.
An exception raised by the callback is logged and swallowed — a broken
progress bar must not lose the work.

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

`estimate_item_size()` from upstream was deliberately **not** ported: the
batcher must call `format_item()` on every item anyway, so the estimate
saved nothing while allowing the oversized decision and the packing
measurement to disagree — an underestimated item was never split and
silently overflowed its batch.

Design record:
`docs/superpowers/specs/2026-08-02-context-processor-design.md`.
