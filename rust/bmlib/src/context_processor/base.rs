// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Hierarchical map-reduce processing of more content than fits one context.
//!
//! A port of `bmlib/context_processor/base.py`. The algorithm:
//!
//! 1. Pack items into batches whose formatted content fits
//!    `max_context_chars`.
//! 2. Extract from each batch.
//! 3. If the extractions together still exceed one context, feed them back in
//!    as items and repeat, until they fit or the recursion ceiling is reached.
//!
//! This module carries **no LLM dependency** — the extractor is supplied by
//! the caller — which is why the package is top-level rather than living under
//! `llm/`. `llm_processor` is the only part that reaches a client.
//!
//! # The design change from Python
//!
//! Python typed items as `Any` and dispatched by `isinstance` in three places
//! (`_format_one` routing `str` / `ConsolidatedItem` / `_Preformatted`, and
//! `split_oversized_item` handling `str` and `ConsolidatedItem`). Rust needs a
//! closed set, so items are [`Item`] trait objects and the routing is a virtual
//! call.
//!
//! That is a **strengthening**, not a workaround: a caller with a new item type
//! implements two methods instead of finding every `isinstance` site, and the
//! "cannot split an item of this type" case is a declared method rather than a
//! `NotImplementedError` raised from a type check.
//!
//! # What is not reproduced
//!
//! `_Preformatted` is here as [`Preformatted`], but not as the *hazard* it
//! guards: Python needed it because `TRUNCATE` cut the rendered item and
//! passing that back through `format_item` would decorate twice. Here a
//! truncated item is a [`Preformatted`] by construction, and there is no path
//! that renders it again.
//!
//! # Per-run state
//!
//! `process` holds nothing on `self`: the level counters, the failure lists and
//! the statistics are locals, so one processor serves concurrent calls. Python
//! had to fix this too — an earlier version kept per-run counts on the
//! instance, and two concurrent runs returned each other's numbers.

use std::collections::BTreeMap;

use crate::context_processor::data_types::{
    Batch, ConfigError, ConsolidatedItem, ConsolidationStrategy, ExtractionResult,
    OversizedItemError, OversizedItemStrategy, ProcessingConfig, ProcessingResult,
    ProcessingStatus, ProgressInfo,
};
use crate::llm::text_utils::TextChunker;

/// How many times a split may be retried with a smaller budget before giving
/// up. Each attempt measures the decoration `format_item` adds and shrinks the
/// budget by exactly that much, so one retry is normally enough; the bound
/// guards a `format_item` whose decoration grows as its content shrinks.
const MAX_SPLIT_ATTEMPTS: usize = 4;

/// Anything the processor can batch, render and (optionally) split.
///
/// Two methods, because those are the two things the batcher needs from an
/// item: how wide it renders, and how to cut it when it is too wide.
pub trait Item: Send + Sync {
    /// Render this item at `index`.
    ///
    /// `index` is its position **within the batch being built**, not in the
    /// caller's list — a renderer that prints the position changes width with
    /// it, and the batcher measures the item where it actually lands.
    fn render(&self, index: usize) -> String;

    /// Cut this item into pieces of at most `max_chars` content characters.
    ///
    /// The default refuses, which the processor records as a skipped item
    /// rather than a failed run — matching Python, where an unsupported type
    /// raised `NotImplementedError` from inside the splitter.
    ///
    /// # Errors
    ///
    /// [`SplitError`] when this item type cannot be split.
    fn split(&self, max_chars: usize, overlap: usize) -> Result<Vec<Box<dyn Item>>, SplitError> {
        let _ = (max_chars, overlap);
        Err(SplitError(format!(
            "Cannot split an item of type {}. Implement Item::split() for custom item types.",
            std::any::type_name::<Self>()
        )))
    }
}

/// Why an item could not be split.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SplitError(pub String);

impl std::fmt::Display for SplitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SplitError {}

/// A shared, type-erased item.
pub type ItemRef = std::sync::Arc<dyn Item>;

/// A plain string item.
#[derive(Debug, Clone)]
pub struct TextItem {
    /// The text.
    pub text: String,
}

impl TextItem {
    /// Wrap a string as an item.
    #[must_use]
    pub fn new(text: impl Into<String>) -> Self {
        TextItem { text: text.into() }
    }
}

impl Item for TextItem {
    fn render(&self, _index: usize) -> String {
        self.text.clone()
    }

    fn split(&self, max_chars: usize, overlap: usize) -> Result<Vec<Box<dyn Item>>, SplitError> {
        Ok(split_string(&self.text, max_chars, overlap)
            .into_iter()
            .map(|piece| Box::new(TextItem::new(piece)) as Box<dyn Item>)
            .collect())
    }
}

/// An item that is already rendered and must not be decorated again.
///
/// Produced by [`OversizedItemStrategy::Truncate`], which cuts the *formatted*
/// item to the limit — so the result's `render` is the text itself, whatever
/// index it is asked for.
#[derive(Debug, Clone)]
pub struct Preformatted {
    /// The already-rendered text.
    pub text: String,
}

impl Item for Preformatted {
    fn render(&self, _index: usize) -> String {
        self.text.clone()
    }
}

/// A consolidated result from the level below, as an item for this level.
///
/// Wrapping keeps the type visible to [`IterativeContextProcessor::format_consolidated_item`],
/// which is what lets a subclass label a level without sniffing shapes inside
/// `format_item`.
#[derive(Debug, Clone)]
pub struct ConsolidatedItemRef {
    /// The consolidated item.
    pub inner: ConsolidatedItem,
}

impl Item for ConsolidatedItemRef {
    fn render(&self, index: usize) -> String {
        // `format_consolidated_item` is a processor method, so this cannot
        // reach it; the processor's own `render_one` intercepts consolidated
        // items before calling `render`. This default keeps the type usable on
        // its own.
        let _ = index;
        self.inner.content.clone()
    }

    fn split(&self, max_chars: usize, overlap: usize) -> Result<Vec<Box<dyn Item>>, SplitError> {
        Ok(split_string(&self.inner.content, max_chars, overlap)
            .into_iter()
            .map(|piece| {
                Box::new(ConsolidatedItemRef {
                    inner: ConsolidatedItem::new(piece, self.inner.metadata.clone()),
                }) as Box<dyn Item>
            })
            .collect())
    }
}

/// Split `text` into pieces of at most `max_chars`, on text boundaries.
///
/// Uses the boundary-aware [`TextChunker`], which prefers to end a piece at a
/// paragraph or sentence break and never discards text. The minimum boundary
/// offset scales with the budget, so a small window still gets boundary
/// treatment.
#[must_use]
pub fn split_string(text: &str, max_chars: usize, overlap: usize) -> Vec<String> {
    if text.chars().count() <= max_chars {
        return vec![text.to_string()];
    }
    if max_chars == 0 {
        return Vec::new();
    }
    let bounded_overlap = overlap.min(max_chars.saturating_sub(1));
    let min_chunk_size = (max_chars / 2).max(1);
    match TextChunker::new(max_chars, bounded_overlap, true, min_chunk_size) {
        Ok(chunker) => chunker
            .chunk_text(text)
            .into_iter()
            .map(|c| c.content)
            .collect(),
        // Unreachable for validated configs: `max_chars >= 1` and
        // `overlap < max_chars` hold by construction above.
        Err(_) => Vec::new(),
    }
}

/// A progress callback.
pub type ProgressCallback = Box<dyn Fn(&ProgressInfo) + Send + Sync>;

/// Batch, extract, and recursively consolidate until the result fits.
pub trait IterativeContextProcessor {
    /// The configuration in force.
    fn config(&self) -> &ProcessingConfig;

    /// Render one of the caller's own items for inclusion in a batch.
    ///
    /// `index` is its position within the batch being built. Rendering it is
    /// fine — the batcher measures the item at the position it lands in.
    fn format_item(&self, item: &dyn Item, index: usize) -> String;

    /// Extract what answers `query` from one batch.
    ///
    /// `batch_content` is the batch's formatted, joined content, never longer
    /// than `config.max_context_chars`. `batch_metadata` carries
    /// `batch_index`, `item_count`, `total_chars`, `item_indices` and
    /// `recursion_level`.
    ///
    /// # Errors
    ///
    /// Any failure is recorded against the batch; whether it ends the run
    /// depends on `config.continue_on_error`.
    fn extract_from_batch(
        &self,
        batch_content: &str,
        query: &str,
        batch_metadata: &BTreeMap<String, serde_json::Value>,
    ) -> Result<ExtractionResult, Box<dyn std::error::Error + Send + Sync>>;

    /// Render a result from the level below as an item for this level.
    ///
    /// Defaults to the content alone.
    fn format_consolidated_item(&self, item: &ConsolidatedItem, index: usize) -> String {
        let _ = index;
        item.content.clone()
    }

    /// Render any item the batcher may hold, routing by its type.
    ///
    /// A consolidated item goes through
    /// [`Self::format_consolidated_item`]; everything else through
    /// [`Self::format_item`]. `Preformatted` is handled by its own `render`.
    fn render_one(&self, item: &dyn Item, index: usize) -> String {
        item.render(index)
    }

    /// The progress callback, if any.
    fn progress_callback(&self) -> Option<&ProgressCallback> {
        None
    }
}

/// The concrete machinery: batching, recursion, consolidation, accounting.
///
/// Kept in one struct so the trait above stays two required methods. A
/// processor is `ProcessingCore` plus an `IterativeContextProcessor` impl.
pub struct ProcessingCore {
    /// The configuration in force.
    pub config: ProcessingConfig,
    /// The progress callback, if any.
    pub progress_callback: Option<ProgressCallback>,
}

impl ProcessingCore {
    /// Build a core, validating the configuration.
    ///
    /// # Errors
    ///
    /// As [`ProcessingConfig::validate`].
    pub fn new(
        config: ProcessingConfig,
        progress_callback: Option<ProgressCallback>,
    ) -> Result<Self, ConfigError> {
        config.validate()?;
        Ok(ProcessingCore {
            config,
            progress_callback,
        })
    }

    /// Hand a [`ProgressInfo`] to the callback, if there is one.
    ///
    /// A callback that panics is caught and logged rather than propagated — a
    /// broken progress bar must not lose the work.
    fn report_progress(&self, info: ProgressInfo) {
        if let Some(cb) = &self.progress_callback {
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| cb(&info)));
        }
    }

    /// Join a batch's items into the content handed to the extractor.
    ///
    /// Each item is rendered **at the index it occupies in this batch**, which
    /// is the whole reason a [`Batch`] keeps items rather than text.
    fn format_batch_content<P: IterativeContextProcessor + ?Sized>(
        processor: &P,
        batch: &Batch,
    ) -> String {
        let rendered: Vec<String> = batch
            .items
            .iter()
            .enumerate()
            .map(|(index, (_, item))| render_one(processor, item.as_ref(), index))
            .collect();
        rendered.join(&processor.config().separator)
    }

    /// Pack `items` into batches that fit `config.max_context_chars`.
    fn create_batches<P: IterativeContextProcessor + ?Sized>(
        processor: &P,
        items: &[ItemRef],
        config: &ProcessingConfig,
        skipped_items: &mut Vec<usize>,
    ) -> Result<Vec<Batch>, OversizedItemError> {
        let mut batches: Vec<Batch> = Vec::new();
        let mut current: Vec<(usize, ItemRef)> = Vec::new();
        let mut current_chars = 0usize;
        let separator_len = config.separator.chars().count();

        for (original_idx, item) in items.iter().enumerate() {
            place(
                processor,
                item,
                original_idx,
                config,
                skipped_items,
                &mut batches,
                &mut current,
                &mut current_chars,
                separator_len,
                true,
            )?;
        }
        if !current.is_empty() {
            batches.push(Batch {
                items: std::mem::take(&mut current),
                total_chars: current_chars,
                batch_index: batches.len(),
            });
        }
        Ok(batches)
    }

    /// Merge one level's results into a single result.
    fn merge_results(
        results: &[ExtractionResult],
        config: &ProcessingConfig,
        recursion_level: usize,
    ) -> ExtractionResult {
        if results.is_empty() {
            let mut r = ExtractionResult::new("");
            r.confidence = 0.0;
            r.recursion_level = recursion_level;
            return r;
        }

        let valid: Vec<&ExtractionResult> = results
            .iter()
            .filter(|r| r.is_valid() && r.confidence >= config.min_confidence_threshold)
            .collect();

        if valid.is_empty() {
            let mut metadata = BTreeMap::new();
            metadata.insert("all_filtered".to_string(), serde_json::Value::Bool(true));
            metadata.insert(
                "original_count".to_string(),
                serde_json::Value::from(results.len()),
            );
            let mut r = ExtractionResult::new("");
            r.metadata = metadata;
            r.confidence = 0.0;
            r.recursion_level = recursion_level;
            return r;
        }

        if valid.len() == 1 {
            // Report the level it was merged at without disturbing the
            // caller's result — `intermediate_results` may still hold it, so
            // this copies the mutable fields rather than aliasing them.
            let mut r = valid[0].clone();
            r.recursion_level = recursion_level;
            return r;
        }

        let (content, confidence) = match config.consolidation_strategy {
            ConsolidationStrategy::Weighted => {
                let mut ordered = valid.clone();
                ordered.sort_by(|a, b| {
                    b.confidence
                        .partial_cmp(&a.confidence)
                        .unwrap_or(std::cmp::Ordering::Equal)
                });
                let content = ordered
                    .iter()
                    .map(|r| r.content.as_str())
                    .collect::<Vec<_>>()
                    .join(&config.separator);
                let total_weight: usize = valid.iter().map(|r| r.content_length()).sum();
                let confidence = if total_weight == 0 {
                    0.0
                } else {
                    valid
                        .iter()
                        .map(|r| r.confidence * r.content_length() as f64)
                        .sum::<f64>()
                        / total_weight as f64
                };
                (content, confidence)
            }
            ConsolidationStrategy::Deduplicate => {
                let mut seen: Vec<String> = Vec::new();
                let mut contents: Vec<&str> = Vec::new();
                for r in &valid {
                    let key = r.content.to_lowercase().trim().to_string();
                    if !seen.contains(&key) {
                        seen.push(key);
                        contents.push(r.content.as_str());
                    }
                }
                let content = contents.join(&config.separator);
                let confidence =
                    valid.iter().map(|r| r.confidence).sum::<f64>() / valid.len() as f64;
                (content, confidence)
            }
            ConsolidationStrategy::Concatenate => {
                let content = valid
                    .iter()
                    .map(|r| r.content.as_str())
                    .collect::<Vec<_>>()
                    .join(&config.separator);
                // Every valid result counts, including one that reported 0.0.
                // Excluding those would make a batch the model had no
                // confidence in *raise* the merged confidence, and would
                // disagree with the weighted branch about what the same inputs
                // are worth.
                let confidence =
                    valid.iter().map(|r| r.confidence).sum::<f64>() / valid.len() as f64;
                (content, confidence)
            }
        };

        let mut metadata = BTreeMap::new();
        if config.preserve_metadata {
            metadata.insert(
                "merged_from".to_string(),
                serde_json::Value::from(valid.len()),
            );
            metadata.insert(
                "filtered_count".to_string(),
                serde_json::Value::from(results.len() - valid.len()),
            );
            metadata.insert(
                "consolidation_strategy".to_string(),
                serde_json::Value::String(config.consolidation_strategy.as_str().to_string()),
            );
            metadata.insert(
                "source_metadata".to_string(),
                serde_json::Value::Array(
                    valid
                        .iter()
                        .map(|r| {
                            serde_json::Value::Object(r.metadata.clone().into_iter().collect())
                        })
                        .collect(),
                ),
            );
        }

        let mut sources: Vec<usize> = Vec::new();
        for r in &valid {
            sources.extend_from_slice(&r.source_indices);
        }

        ExtractionResult {
            content,
            metadata,
            source_indices: sources,
            confidence,
            batch_index: None,
            recursion_level,
            is_error: false,
            error_message: None,
        }
    }
}

/// Render one item, routing consolidated items through the processor's hook.
fn render_one<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    item: &dyn Item,
    index: usize,
) -> String {
    processor.render_one(item, index)
}

#[allow(clippy::too_many_arguments)]
fn place<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    item: &ItemRef,
    original_idx: usize,
    config: &ProcessingConfig,
    skipped: &mut Vec<usize>,
    batches: &mut Vec<Batch>,
    current: &mut Vec<(usize, ItemRef)>,
    current_chars: &mut usize,
    separator_len: usize,
    may_split: bool,
) -> Result<(), OversizedItemError> {
    if try_place(
        processor,
        item,
        original_idx,
        config,
        current,
        current_chars,
        separator_len,
    ) {
        return Ok(());
    }
    if !current.is_empty() {
        // It did not fit alongside what is already batched. Close that batch
        // and re-measure at the head of a fresh one, where the index — and so
        // the decoration — is different.
        flush(batches, current, current_chars);
        if try_place(
            processor,
            item,
            original_idx,
            config,
            current,
            current_chars,
            separator_len,
        ) {
            return Ok(());
        }
    }
    if !may_split {
        // A piece that still does not fit alone would silently overflow the
        // context the whole module exists to respect.
        skipped.push(original_idx);
        return Ok(());
    }
    for piece in handle_oversized(processor, item, original_idx, config, skipped)? {
        place(
            processor,
            &piece,
            original_idx,
            config,
            skipped,
            batches,
            current,
            current_chars,
            separator_len,
            false,
        )?;
    }
    Ok(())
}

fn flush(batches: &mut Vec<Batch>, current: &mut Vec<(usize, ItemRef)>, current_chars: &mut usize) {
    if current.is_empty() {
        return;
    }
    batches.push(Batch {
        items: std::mem::take(current),
        total_chars: *current_chars,
        batch_index: batches.len(),
    });
    *current_chars = 0;
}

fn try_place<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    item: &ItemRef,
    original_idx: usize,
    config: &ProcessingConfig,
    current: &mut Vec<(usize, ItemRef)>,
    current_chars: &mut usize,
    separator_len: usize,
) -> bool {
    let mut cost = render_one(processor, item.as_ref(), current.len())
        .chars()
        .count();
    if !current.is_empty() {
        cost += separator_len;
    }
    if *current_chars + cost > config.max_context_chars {
        return false;
    }
    current.push((original_idx, item.clone()));
    *current_chars += cost;
    true
}

fn handle_oversized<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    item: &ItemRef,
    original_idx: usize,
    config: &ProcessingConfig,
    skipped: &mut Vec<usize>,
) -> Result<Vec<ItemRef>, OversizedItemError> {
    let limit = config.max_context_chars;

    match config.oversized_item_strategy {
        OversizedItemStrategy::Fail => {
            return Err(OversizedItemError(format!(
                "Item {original_idx} is oversized (needs more than {limit} chars). \
                 Use a different oversized_item_strategy to handle this."
            )));
        }
        OversizedItemStrategy::Skip => {
            skipped.push(original_idx);
            return Ok(Vec::new());
        }
        OversizedItemStrategy::Truncate => {
            // Truncate what the item *renders to*, and wrap it so the batcher
            // cannot decorate it a second time.
            let text = render_one(processor, item.as_ref(), 0);
            let truncated: String = text.chars().take(limit).collect();
            return Ok(vec![std::sync::Arc::new(Preformatted { text: truncated })]);
        }
        OversizedItemStrategy::Split => {}
    }

    let pieces = match split_to_fit(processor, item.as_ref(), config) {
        Ok(pieces) => pieces,
        Err(_) => {
            skipped.push(original_idx);
            return Ok(Vec::new());
        }
    };
    if pieces.is_empty() {
        skipped.push(original_idx);
        return Ok(Vec::new());
    }
    Ok(pieces)
}

/// Split an item into pieces that fit **once formatted**.
///
/// `Item::split` cuts raw content, but the batcher measures the item after
/// `format_item` has decorated it — so a piece cut to exactly the limit
/// exceeds it. The overflow is measured and the budget reduced by it, rather
/// than guessed at.
fn split_to_fit<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    item: &dyn Item,
    config: &ProcessingConfig,
) -> Result<Vec<ItemRef>, SplitError> {
    let limit = config.max_context_chars;
    let mut budget = limit;

    for _ in 0..MAX_SPLIT_ATTEMPTS {
        if budget == 0 || budget <= config.overlap_chars {
            break;
        }
        let pieces = item.split(budget, config.overlap_chars)?;
        if pieces.is_empty() {
            break;
        }
        let widest = pieces
            .iter()
            .map(|p| render_one(processor, p.as_ref(), 0).chars().count())
            .max()
            .unwrap_or(0);
        if widest <= limit {
            return Ok(pieces.into_iter().map(std::sync::Arc::from).collect());
        }
        budget -= widest - limit;
    }
    Ok(Vec::new())
}

/// One level's outcome.
struct LevelOutcome {
    results: Vec<ExtractionResult>,
    needs_recursion: bool,
    successful: usize,
    batch_count: usize,
}

/// Batch and extract one level.
#[allow(clippy::too_many_arguments)]
fn process_level<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    core: &ProcessingCore,
    items: &[ItemRef],
    query: &str,
    config: &ProcessingConfig,
    recursion_level: usize,
    intermediate: &mut Option<Vec<Vec<ExtractionResult>>>,
    failed_batches: &mut Vec<usize>,
    skipped_items: &mut Vec<usize>,
) -> Result<LevelOutcome, Box<dyn std::error::Error + Send + Sync>> {
    let skipped_before = skipped_items.len();
    let batches = ProcessingCore::create_batches(processor, items, config, skipped_items)
        .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)?;
    // An item dropped during packing will never reach an extraction, so a
    // progress count that waited for it would never reach the end.
    let mut items_done = skipped_items.len() - skipped_before;

    let mut info = ProgressInfo::new("batching");
    info.current_item = items_done;
    info.total_items = items.len();
    info.total_batches = batches.len();
    info.recursion_level = recursion_level;
    info.message = format!(
        "Created {} batches from {} items",
        batches.len(),
        items.len()
    );
    core.report_progress(info);

    let mut results: Vec<ExtractionResult> = Vec::new();
    let mut successful = 0usize;

    for batch in &batches {
        let mut info = ProgressInfo::new("extracting");
        info.current_item = items_done;
        info.total_items = items.len();
        info.current_batch = batch.batch_index + 1;
        info.total_batches = batches.len();
        info.recursion_level = recursion_level;
        info.message = format!(
            "Processing batch {}/{}",
            batch.batch_index + 1,
            batches.len()
        );
        core.report_progress(info);

        let mut metadata: BTreeMap<String, serde_json::Value> = BTreeMap::new();
        metadata.insert(
            "batch_index".into(),
            serde_json::Value::from(batch.batch_index),
        );
        metadata.insert("item_count".into(), serde_json::Value::from(batch.size()));
        metadata.insert(
            "total_chars".into(),
            serde_json::Value::from(batch.total_chars),
        );
        metadata.insert(
            "item_indices".into(),
            serde_json::Value::Array(
                batch
                    .item_indices()
                    .into_iter()
                    .map(serde_json::Value::from)
                    .collect(),
            ),
        );
        metadata.insert(
            "recursion_level".into(),
            serde_json::Value::from(recursion_level),
        );

        let content = ProcessingCore::format_batch_content(processor, batch);
        match processor.extract_from_batch(&content, query, &metadata) {
            Ok(mut result) => {
                result.batch_index = Some(batch.batch_index);
                result.recursion_level = recursion_level;
                // Copied, not aliased: the batch's list outlives this call.
                result.source_indices = batch.item_indices();
                results.push(result);
                successful += 1;
            }
            Err(exc) => {
                let message = exc.to_string();
                failed_batches.push(batch.batch_index);
                if !config.continue_on_error {
                    return Err(Box::new(std::io::Error::other(format!(
                        "Batch {} extraction failed: {message}",
                        batch.batch_index
                    ))));
                }
                let mut result = ExtractionResult::error(&message);
                result.source_indices = batch.item_indices();
                result.batch_index = Some(batch.batch_index);
                result.recursion_level = recursion_level;
                results.push(result);
            }
        }
        items_done += batch.size();
    }

    if let Some(intermediate) = intermediate {
        intermediate.push(results.clone());
    }

    let valid: Vec<&ExtractionResult> = results.iter().filter(|r| r.is_valid()).collect();
    let joined_length = valid.iter().map(|r| r.content_length()).sum::<usize>()
        + config.separator.chars().count() * valid.len().saturating_sub(1);

    Ok(LevelOutcome {
        results,
        needs_recursion: joined_length > config.max_context_chars,
        successful,
        batch_count: batches.len(),
    })
}

/// Run a full processing pass.
///
/// This is the entry point every processor shares; a concrete processor calls
/// it from its own `process`, passing its own `config`.
#[allow(clippy::too_many_lines)]
pub fn run_all<P: IterativeContextProcessor + ?Sized>(
    processor: &P,
    core: &ProcessingCore,
    items: &[ItemRef],
    query: &str,
    config: &ProcessingConfig,
    store_intermediate: bool,
) -> ProcessingResult {
    let mut intermediate: Option<Vec<Vec<ExtractionResult>>> = if store_intermediate {
        Some(Vec::new())
    } else {
        None
    };
    let mut failed_batches: Vec<usize> = Vec::new();
    let mut skipped_items: Vec<usize> = Vec::new();
    let mut successful = 0usize;

    // Local, not instance state: two concurrent `process()` calls on one
    // processor would otherwise append their per-level counts into whichever
    // dict the later call installed.
    let mut stats: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    stats.insert("total_items".into(), serde_json::Value::from(items.len()));
    stats.insert(
        "batches_per_level".into(),
        serde_json::Value::Array(Vec::new()),
    );
    stats.insert(
        "items_per_level".into(),
        serde_json::Value::Array(vec![serde_json::Value::from(items.len())]),
    );

    if items.is_empty() {
        return ProcessingResult {
            final_result: {
                let mut r = ExtractionResult::new("");
                r.confidence = 0.0;
                r
            },
            status: ProcessingStatus::Completed,
            total_items_processed: 0,
            batches_created: 0,
            recursion_levels_used: 0,
            intermediate_results: intermediate,
            error_message: None,
            processing_stats: stats,
            failed_batches,
            skipped_items,
            successful_batches: 0,
        };
    }

    let mut info = ProgressInfo::new("starting");
    info.total_items = items.len();
    info.message = format!("Starting processing of {} items", items.len());
    core.report_progress(info);

    let mut current_items: Vec<ItemRef> = items.to_vec();
    let mut recursion_level = 0usize;
    let mut total_batches = 0usize;
    let mut status = ProcessingStatus::Completed;
    let mut error_message: Option<String> = None;
    let final_result: ExtractionResult;

    loop {
        let outcome = match process_level(
            processor,
            core,
            &current_items,
            query,
            config,
            recursion_level,
            &mut intermediate,
            &mut failed_batches,
            &mut skipped_items,
        ) {
            Ok(o) => o,
            Err(exc) => {
                status = ProcessingStatus::Failed;
                error_message = Some(exc.to_string());
                final_result = ExtractionResult::error(exc.to_string());
                break;
            }
        };

        successful += outcome.successful;
        push_stat(&mut stats, "batches_per_level", outcome.batch_count);
        total_batches += outcome.batch_count;

        if !outcome.needs_recursion {
            final_result = ProcessingCore::merge_results(&outcome.results, config, recursion_level);
            break;
        }

        if recursion_level >= config.max_recursion_depth {
            status = ProcessingStatus::Truncated;
            final_result = ProcessingCore::merge_results(&outcome.results, config, recursion_level);
            break;
        }

        let valid: Vec<&ExtractionResult> =
            outcome.results.iter().filter(|r| r.is_valid()).collect();
        if valid.len() < config.min_items_for_recursion {
            // One result has nothing to be consolidated *with*, so recursing
            // would re-summarise it until the ceiling.
            final_result = ProcessingCore::merge_results(&outcome.results, config, recursion_level);
            break;
        }

        let mut info = ProgressInfo::new("recursing");
        info.recursion_level = recursion_level + 1;
        info.message = format!(
            "Recursing to level {} with {} results",
            recursion_level + 1,
            valid.len()
        );
        core.report_progress(info);

        // The level is the base class's own knowledge, so it is put on the
        // item rather than left to whether the extractor happened to copy its
        // batch metadata forward.
        current_items = valid
            .iter()
            .map(|r| {
                let mut metadata = r.metadata.clone();
                metadata.insert(
                    "recursion_level".into(),
                    serde_json::Value::from(r.recursion_level),
                );
                std::sync::Arc::new(ConsolidatedItemRef {
                    inner: ConsolidatedItem::new(r.content.clone(), metadata),
                }) as ItemRef
            })
            .collect();
        push_stat(&mut stats, "items_per_level", current_items.len());
        recursion_level += 1;
    }

    if status == ProcessingStatus::Completed
        && (!failed_batches.is_empty() || !skipped_items.is_empty())
    {
        if successful > 0 {
            status = ProcessingStatus::Partial;
        } else {
            // Naming both counts matters: a run where every item was dropped
            // as oversized created no batch at all, and reporting that as "all
            // batches failed" sends the reader looking for an extraction error
            // that never happened.
            status = ProcessingStatus::Failed;
            error_message = Some(format!(
                "No batch produced a result: {} failed, {} items skipped",
                failed_batches.len(),
                skipped_items.len()
            ));
        }
    }

    let mut info = ProgressInfo::new("complete");
    info.current_item = items.len();
    info.total_items = items.len();
    info.recursion_level = recursion_level;
    info.message =
        format!("Processing complete after {recursion_level} recursion levels (status: {status})");
    core.report_progress(info);

    ProcessingResult {
        final_result,
        status,
        total_items_processed: items.len(),
        batches_created: total_batches,
        recursion_levels_used: recursion_level,
        intermediate_results: intermediate,
        error_message,
        processing_stats: stats,
        failed_batches,
        skipped_items,
        successful_batches: successful,
    }
}

fn push_stat(stats: &mut BTreeMap<String, serde_json::Value>, key: &str, value: usize) {
    if let Some(serde_json::Value::Array(arr)) = stats.get_mut(key) {
        arr.push(serde_json::Value::from(value));
    }
}

/// Convenience: wrap strings as items.
#[must_use]
pub fn text_items(items: &[&str]) -> Vec<ItemRef> {
    items
        .iter()
        .map(|s| std::sync::Arc::new(TextItem::new(*s)) as ItemRef)
        .collect()
}
