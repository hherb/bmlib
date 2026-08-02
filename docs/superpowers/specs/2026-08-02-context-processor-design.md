# Design — porting `context_processor` from bmlibrarian

_2026-08-02. Phase 1 item 2 of the bmlibrarian → bmlib porting effort
(`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`)._

## The problem it solves

A caller holds more material than the model's context window can take —
sixty semantic-search chunks, a hundred abstracts, one very long document —
and needs one answer over all of it. Truncating loses information silently.
The pattern here is hierarchical map-reduce: batch the items to fit, extract
from each batch, then treat the extractions as new items and repeat until
what remains fits in one context.

`bmlib.llm.text_utils` already has `process_with_map_reduce()`, which does
**one** map and **one** reduce over the chunks of a *single string*. That is
the shallow case. This module is the general one: a list of arbitrary items,
recursion to arbitrary depth, per-batch failure accounting, and a
configurable answer to "what do I do with an item that does not fit on its
own?".

## Source and scope

Upstream: `~/src/bmlibrarian/src/bmlibrarian/agents/context_processor/`
(1817 lines over `base.py`, `data_types.py`, `semantic_chunk_processor.py`).

| Upstream | Disposition |
|---|---|
| `data_types.py` | Ported, modernised. |
| `base.py` — `IterativeContextProcessor` | Ported, four defects fixed (below). |
| `semantic_chunk_processor.py` — `SemanticChunkProcessor` | **Rewritten** onto `BaseAgent`. Upstream calls the raw Ollama client (`llm_client.chat(model=…, options={"num_predict": …})`, `response["message"]["content"]`), which is exactly the coupling a port must sever. |
| `create_prisma_chunk_processor` | **Not ported.** PRISMA 2020 is a domain concept belonging to the application, not to a general library. |

Target: **`bmlib/context_processor/`**, top-level. The core ABC has no LLM
dependency at all — it is batching, recursion and consolidation over
caller-supplied items — so it does not belong under `agents/`; only the
concrete `LLMChunkProcessor` imports `BaseAgent`.

## Defects fixed in the port

Each is a real behaviour bug in the upstream implementation, and each gets a
regression test named after it.

1. **The bin-packing ran twice per level.** `process()` called
   `_create_batches(current_items, config, None)` a second time purely to
   record `len(batches)` in the statistics — re-formatting every item and
   re-running `split_oversized_item()` on every oversized one. For an
   LLM-formatted item that is pure duplicated work, and the second run
   re-emits every SKIP/SPLIT log line, so the logs claim twice the skips
   that happened. Fixed by having `_process_level()` return the count it
   already computed.

2. **Split pieces were measured before formatting.** `split_oversized_item()`
   cuts *raw* content to `max_chars`, but the batcher then measures
   `format_item(piece)`, which adds the decoration (`"[Chunk 3, Score:
   0.82]\n"`). A piece cut to exactly the limit therefore exceeds it once
   decorated, lands in a batch of its own, and overflows the context —
   breaking the one guarantee `max_context_chars` makes. Fixed by splitting
   against a *decoration-adjusted* budget, measured from the item itself.

3. **TRUNCATE double-decorated.** The strategy formatted the item, truncated
   the formatted string to `max_context_chars`, and returned it as a plain
   `str` — which the batcher then passed through `format_item()` again. The
   decoration appears twice and the result is over the limit again, by the
   width of the second decoration. Fixed by truncating to a
   decoration-adjusted budget and marking the piece so it is not re-decorated.

4. **Boundary items were measured with the wrong index.** In the packing
   loop the item that *starts* a new batch was formatted with
   `len(current_items)` — the outgoing batch's item count — and that length
   became the new batch's `total_chars`. Where `format_item()` includes the
   index (as every upstream implementation does), the recorded size is wrong
   by the width of the index difference, and it is wrong in the direction of
   under-counting. Fixed by measuring boundary items at their true position.

## Design decisions

- **`_split_string()` delegates to `TextChunker`.** Upstream cut blind on
  character count, mid-word. bmlib already ships a boundary-aware chunker
  that never discards text; the processor uses it and keeps the naive cut
  only as the fallback when a boundary cannot be found within budget.
- **`ProcessingConfig` is frozen.** It is read on every batching decision and
  a caller mutating it mid-run would make the statistics describe a
  configuration that no longer exists. `process(config=…)` already takes a
  per-call override, which is the supported way to vary it.
- **`format_item()` stays the single source of truth for size.**
  `estimate_item_size()` is kept as the documented performance escape hatch,
  but the oversized *decision* and the packing measurement now agree, where
  upstream used the estimate for one and the format for the other — an
  underestimate meant an oversized item was never split and silently
  overflowed its batch.
- **Failure accounting is unchanged.** `PARTIAL` when some batches failed and
  at least one succeeded, `FAILED` when none did, `TRUNCATED` at the
  recursion ceiling. This is the part of upstream that was right.

## Rejected

- **Folding the core into `bmlib/llm/text_utils.py`.** The two overlap
  conceptually, but `text_utils` is string-in/string-out helpers with no
  class hierarchy; adding an ABC with four override points to it would make
  the smaller, simpler thing harder to find.
- **Making the core generic over item type (`Generic[T]`).** Tempting, and
  it would type `format_item` precisely. But the recursion *changes the item
  type* — level 0 processes the caller's items, level 1 processes
  `(content, metadata)` tuples the processor itself made — so a single type
  parameter would be a lie at exactly the point the class earns its keep.
- **Dropping `ConsolidationStrategy.WEIGHTED`/`DEDUPLICATE`.** They are thin,
  but they are the reason `confidence` exists on `ExtractionResult`, and a
  caller whose extractor reports confidence has no other way to spend it.
