# bmlib::context_processor — More Than One Context Holds

Hierarchical map-reduce over content that exceeds one context window: pack the
items into batches that fit, extract from each batch, feed the extractions
back in as items, and repeat until what remains fits. The Python page is
[context_processor.md](../../../../docs/manual/context_processor.md); this one
describes the Rust API and where it differs.

| Need | Feature |
|---|---|
| The harness (`IterativeContextProcessor`, `ProcessingCore`, `run_all`), the data types | none |
| `llm_processor::LlmChunkProcessor` over a `ContextModel` you supply | none |

The harness has no LLM dependency: whatever answers a batch is yours.
`max_context_chars` is the promise the module makes — no batch content handed
to `extract_from_batch` is longer, in characters, than that. For one long
string and a single map and reduce, `llm::process_with_map_reduce` is the
shallow case ([llm.md](llm.md)).

## Items

Python's items were `Any`, dispatched with `isinstance`. Here an item is an
`ItemRef` (`Arc<dyn Item>`), and the `Item` trait declares what the harness
needs: `render(index)`, `split(max_chars, overlap)` (the default refuses,
which the harness records as a skipped item) and `routing()`. `TextItem` wraps
a string (`text_items(&[…])` builds a list of them), `ConsolidatedItemRef`
carries a result from the level below, and `Preformatted` is printed verbatim
— what `OversizedItemStrategy::Truncate` produces. A text item that has to be
split breaks on paragraph and sentence boundaries through `llm::TextChunker`.

## Writing a processor

Implement `IterativeContextProcessor` (`config`, `format_item`,
`extract_from_batch`; `format_consolidated_item` is optional and defaults to
the content alone), keep a `ProcessingCore` for the configuration and the
progress callback, and run it with `base::run_all`, which is not re-exported
at the module root. Here a stand-in for a model keeps a batch's first 40
characters:

```rust
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use bmlib::context_processor::base::run_all;
use bmlib::context_processor::{
    text_items, ExtractionResult, Item, IterativeContextProcessor, OversizedItemStrategy,
    ProcessingConfig, ProcessingCore, ProcessingStatus,
};
use serde_json::Value;

struct FirstWords {
    core: ProcessingCore,
}

impl IterativeContextProcessor for FirstWords {
    fn config(&self) -> &ProcessingConfig {
        &self.core.config
    }
    fn format_item(&self, item: &dyn Item, index: usize) -> String {
        format!("[{}] {}", index + 1, item.render(index)) // index within the batch
    }
    fn extract_from_batch(
        &self,
        batch_content: &str,
        _query: &str,
        _batch_metadata: &BTreeMap<String, Value>,
    ) -> Result<ExtractionResult, Box<dyn std::error::Error + Send + Sync>> {
        assert!(batch_content.chars().count() <= self.core.config.max_context_chars);
        let summary: String = batch_content.chars().take(40).collect();
        Ok(ExtractionResult::new(summary).with_confidence(0.8))
    }
}

let stages = Arc::new(Mutex::new(Vec::new()));
let seen = Arc::clone(&stages);
let config = ProcessingConfig::new().with_max_context_chars(120);
let processor = FirstWords {
    core: ProcessingCore::new(
        config.clone(),
        Some(Box::new(move |info| seen.lock().unwrap().push(info.stage.clone()))),
    )?,
};

let texts: Vec<String> = (1..=12)
    .map(|i| format!("Finding {i:02}: the effect was small and imprecise."))
    .collect();
let items = text_items(&texts.iter().map(String::as_str).collect::<Vec<_>>());

let result = run_all(&processor, &processor.core, &items, "What was found?", &config, false);
assert_eq!(result.status, ProcessingStatus::Completed);
assert_eq!(result.batches_created, 6 + 3 + 2); // two items a batch, three levels
assert_eq!(result.recursion_levels_used, 2);
assert!(result.content().chars().count() <= 120);
let stages = stages.lock().unwrap();
assert_eq!((stages[0].as_str(), stages[stages.len() - 1].as_str()), ("starting", "complete"));

// An item too big to fit alone, under the Skip strategy:
let strict = ProcessingConfig::new()
    .with_max_context_chars(120)
    .with_oversized_strategy(OversizedItemStrategy::Skip);
let skipper = FirstWords { core: ProcessingCore::new(strict.clone(), None)? };
let long = "x".repeat(500);
let items = text_items(&[long.as_str(), "A short finding."]);
let result = run_all(&skipper, &skipper.core, &items, "q", &strict, false);
assert_eq!(result.skipped_items, vec![0]);
assert_eq!(result.status, ProcessingStatus::Partial);
assert!(result.is_partial());
# Ok::<(), bmlib::context_processor::ConfigError>(())
```

`run_all(processor, core, items, query, config, store_intermediate)` takes the
configuration for this run explicitly, as Python's `process(…, config=…)`
does; batch content is joined with the processor's own `config().separator`,
so pass the same configuration you built the core with. It holds no per-run
state, so one processor serves concurrent runs. `store_intermediate` keeps
every level's results in `intermediate_results`.

`extract_from_batch` receives `batch_metadata` holding `batch_index`,
`item_count`, `total_chars`, `item_indices` and `recursion_level`; the harness
sets the returned result's `batch_index`, `recursion_level` and
`source_indices` itself. An `Err` is recorded against the batch
(`failed_batches`) and, with `continue_on_error` off, ends the run as
`Failed`. Above level 0 the items are `ConsolidatedItemRef`s routed to
`format_consolidated_item`, whose metadata always carries `recursion_level`.

## Configuration and results

`ProcessingConfig` has public fields and `with_*` builders. Its defaults are
Python's: `max_context_chars` 4,000, `overlap_chars` 0 (at most half the
window), `max_recursion_depth` 5, `min_items_for_recursion` 2, separator
`"\n\n---\n\n"`, `oversized_item_strategy` `Split` (or `Truncate`, `Skip`,
`Fail`), `consolidation_strategy` `Concatenate` (or `Weighted`, most confident
first, and `Deduplicate`), `continue_on_error` on, `min_confidence_threshold`
0.0. `ProcessingCore::new` validates it (`ConfigError`); `validate()` and
`validated()` do the same on their own.

`ProcessingResult` carries `final_result` (an `ExtractionResult`; `content()`
is its text), `status`, `total_items_processed`, `batches_created`,
`recursion_levels_used`, `failed_batches`, `skipped_items`,
`successful_batches`, `processing_stats` (with `batches_per_level` and
`items_per_level`) and `error_message`. `ProcessingStatus` is `Completed`,
`Partial` (some batches failed or items were skipped, but something
succeeded), `Failed`, or `Truncated` (the recursion ceiling was reached while
the results still did not fit). `is_complete()`, `is_partial()`,
`has_failures()` and `success_rate()` summarise it. An empty item list returns
at once, `Completed`, with empty content and confidence 0.

A progress callback (`ProgressCallback`, a boxed `Fn(&ProgressInfo)`) sees the
stages `starting`, `batching`, `extracting`, `recursing` and `complete`; a
callback that panics is caught, so a broken progress bar does not lose the
run. `ProgressInfo::progress_percent()` restarts at each level.

## The model-backed processor

`llm_processor::LlmChunkProcessor` is Python's `LLMChunkProcessor`, bound to a
`ContextModel` rather than a `BaseAgent`. `ContextModel` has two methods,
`complete` (prose) and `complete_json` (the structured shape); implement it
over [`LlmClient`](llm.md) yourself, returning `Err` for a completion stopped
at the output ceiling, since a cut-off extraction has lost passages. There are
no default prompts: `PromptTemplates` must carry `{query}` and `{content}`,
filled in one pass by replacement, so literal braces need no doubling.

```rust
use std::sync::Arc;
use bmlib::context_processor::llm_processor::{
    ChunkItem, ContextModel, LlmChunkProcessor, PromptTemplates,
};
use bmlib::context_processor::{ItemRef, ProcessingConfig, ProcessingStatus};
use serde_json::{json, Value};

struct Stub;

impl ContextModel for Stub {
    fn complete(&self, prompt: &str, _temperature: f64, _max_tokens: i64) -> Result<String, String> {
        Ok(format!("read {} characters", prompt.chars().count()))
    }
    fn complete_json(&self, prompt: &str, _t: f64, _m: i64) -> Result<Value, String> {
        assert!(prompt.contains("[Chunk 1, score 0.87]"));
        Ok(json!({
            "extracted_content": "Assessors were blinded.",
            "confidence": 1.5,
            "key_findings": ["blinded outcome assessment"],
        }))
    }
}

let templates = PromptTemplates {
    extraction_prompt: "Question: {query}\n\nPassages:\n{content}".into(),
    consolidation_prompt: "Merge these notes for {query}:\n{content}".into(),
};
let processor = LlmChunkProcessor::new(
    &Stub, templates, ProcessingConfig::new(), true /* structured */, 0.0, 512,
)?;

let chunks: Vec<ItemRef> = vec![
    Arc::new(ChunkItem::scored("Outcome assessors were masked.", 0.87)),
    Arc::new(ChunkItem::new("Participants were not blinded.")),
];
let result = processor.process_items(&chunks, "Was it blinded?");
assert_eq!(result.status, ProcessingStatus::Completed);
assert_eq!(result.content(), "Assessors were blinded.");
assert_eq!(result.final_result.confidence, 1.0); // clamped into 0..=1
assert_eq!(result.final_result.metadata["key_findings"], json!(["blinded outcome assessment"]));

let bad = PromptTemplates { extraction_prompt: "{content}".into(), ..processor.templates().clone() };
assert!(LlmChunkProcessor::new(&Stub, bad, ProcessingConfig::new(), false, 0.0, 512).is_err());
# Ok::<(), String>(())
```

- Level 0 uses the extraction prompt and every level above the consolidation
  prompt, always in prose. With structured output on, level 0 calls
  `complete_json` and reads `extracted_content`, `confidence` (clamped; an
  unusable one is `DEFAULT_EXTRACTION_CONFIDENCE`, 0.9) and `key_findings`
  (kept in the result's metadata). An `extracted_content` that is absent or
  not a string is no content, at confidence 0.0 with no findings (#570; a
  `null` used to be stored as the text `"null"`). A prose extraction records 0.9.
- `ChunkItem` renders `[Item N]`, or `[Chunk N, score S]` with a score, and a
  split chunk keeps its score on every piece. A level above renders as
  `[Consolidated level L, item N]`.
- `process(text, query)` runs one string; `process_items` a list. Neither
  stores intermediate results, and this processor takes no progress callback.
- A failed batch is recorded as `LLM extraction failed: …`.
  `LlmChunkProcessor::new` returns `Err(String)` for a template short a
  placeholder or an invalid configuration, where Python raises `ValueError`.

`quality::LlmCondenser` is the production use: the Cochrane and PICO agents'
condensation of an oversized paper.
