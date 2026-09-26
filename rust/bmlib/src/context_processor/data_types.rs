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

//! Data types for hierarchical map-reduce context processing.
//!
//! A port of `bmlib/context_processor/data_types.py`. The processor batches
//! items to fit an LLM context window, extracts from each batch, and
//! recursively consolidates the extractions until what remains fits in one
//! context. These are the carriers for its configuration, its per-batch
//! results, and its final report.

use std::collections::BTreeMap;

/// Default maximum characters in one batch's formatted content.
pub const DEFAULT_MAX_CONTEXT_CHARS: usize = 4_000;
/// Default characters of overlap when splitting an oversized item.
pub const DEFAULT_OVERLAP_CHARS: usize = 0;
/// Default levels of recursive consolidation allowed.
pub const DEFAULT_MAX_RECURSION_DEPTH: usize = 5;
/// Default number of results below which consolidation is not attempted.
pub const DEFAULT_MIN_ITEMS_FOR_RECURSION: usize = 2;
/// Default string joining items within a batch, and results within a merge.
pub const DEFAULT_SEPARATOR: &str = "\n\n---\n\n";

/// An item did not fit and [`OversizedItemStrategy::Fail`] was in force.
///
/// A distinct type so the run can tell *the configuration doing exactly what
/// it was asked* apart from a genuine defect, and report it without a
/// traceback. Python subclasses `ValueError` because that is what the strict
/// strategy always documented itself as raising.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OversizedItemError(pub String);

impl std::fmt::Display for OversizedItemError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for OversizedItemError {}

/// Outcome of a processing run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcessingStatus {
    /// Not started.
    Pending,
    /// Running.
    InProgress,
    /// Finished with nothing lost.
    Completed,
    /// Finished with everything lost.
    Failed,
    /// The recursion ceiling was reached; results are partial.
    Truncated,
    /// Finished, but some batches failed or some items were skipped.
    Partial,
}

impl ProcessingStatus {
    /// The wire spelling, matching Python's enum values.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            ProcessingStatus::Pending => "pending",
            ProcessingStatus::InProgress => "in_progress",
            ProcessingStatus::Completed => "completed",
            ProcessingStatus::Failed => "failed",
            ProcessingStatus::Truncated => "truncated",
            ProcessingStatus::Partial => "partial",
        }
    }
}

impl std::fmt::Display for ProcessingStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// What to do with an item larger than `max_context_chars` on its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OversizedItemStrategy {
    /// Cut it into pieces that fit.
    Split,
    /// Keep the leading part; the rest is lost.
    Truncate,
    /// Drop it entirely, recording its index.
    Skip,
    /// Raise [`OversizedItemError`] — strict mode.
    Fail,
}

/// How to merge the extraction results of one level into one result.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConsolidationStrategy {
    /// Join in order, with the separator.
    Concatenate,
    /// Join most-confident first.
    Weighted,
    /// Drop repeated content before joining.
    Deduplicate,
}

impl ConsolidationStrategy {
    /// The wire spelling, matching Python's enum values.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            ConsolidationStrategy::Concatenate => "concatenate",
            ConsolidationStrategy::Weighted => "weighted",
            ConsolidationStrategy::Deduplicate => "deduplicate",
        }
    }
}

/// Why a [`ProcessingConfig`] was rejected.
#[derive(Debug, Clone, PartialEq)]
pub enum ConfigError {
    /// `max_context_chars` was zero.
    NonPositiveMaxContextChars,
    /// `overlap_chars` was more than half of `max_context_chars`.
    OverlapTooLarge {
        /// The overlap supplied.
        overlap: usize,
        /// The window supplied.
        max_context_chars: usize,
    },
    /// `min_items_for_recursion` was zero.
    MinItemsForRecursionZero,
    /// `min_confidence_threshold` was outside `0.0..=1.0` or NaN.
    ConfidenceOutOfRange {
        /// The threshold supplied.
        value: f64,
    },
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::NonPositiveMaxContextChars => {
                write!(f, "max_context_chars must be positive, got 0")
            }
            ConfigError::OverlapTooLarge {
                overlap,
                max_context_chars,
            } => write!(
                f,
                "overlap_chars ({overlap}) must be at most half of \
                 max_context_chars ({max_context_chars}), so that a split \
                 advances far enough to terminate in a sane number of pieces"
            ),
            ConfigError::MinItemsForRecursionZero => {
                write!(f, "min_items_for_recursion must be at least 1, got 0")
            }
            ConfigError::ConfidenceOutOfRange { value } => write!(
                f,
                "min_confidence_threshold must be between 0.0 and 1.0, got {value}"
            ),
        }
    }
}

impl std::error::Error for ConfigError {}

/// Configuration for one processing run.
///
/// Every batching decision reads it, and a caller mutating it mid-run would
/// leave the recorded statistics describing a configuration that never ran —
/// so it is built through [`ProcessingConfig::new`], which validates.
#[derive(Debug, Clone, PartialEq)]
pub struct ProcessingConfig {
    /// Maximum characters in one batch's formatted content. **This is the
    /// promise the whole module makes**: no batch handed to
    /// `extract_from_batch` exceeds it.
    pub max_context_chars: usize,
    /// Characters of overlap between pieces when an oversized item is split.
    /// Zero for discrete items. At most half of `max_context_chars`, so that
    /// a split advances.
    pub overlap_chars: usize,
    /// Levels of recursive consolidation allowed before the run gives up and
    /// returns [`ProcessingStatus::Truncated`].
    pub max_recursion_depth: usize,
    /// Below this many results, consolidation is not attempted — one result
    /// has nothing to be merged with, so recursing would just re-summarise it
    /// until the ceiling.
    pub min_items_for_recursion: usize,
    /// String joining items within a batch, and results within a consolidation.
    pub separator: String,
    /// Carry each result's metadata into the merged result's `source_metadata`.
    pub preserve_metadata: bool,
    /// Answer to an item that does not fit alone.
    pub oversized_item_strategy: OversizedItemStrategy,
    /// How results are merged.
    pub consolidation_strategy: ConsolidationStrategy,
    /// Record a failed batch and carry on. When false, the first failure ends
    /// the run.
    pub continue_on_error: bool,
    /// Results below this confidence are dropped before merging.
    pub min_confidence_threshold: f64,
}

impl Default for ProcessingConfig {
    fn default() -> Self {
        ProcessingConfig {
            max_context_chars: DEFAULT_MAX_CONTEXT_CHARS,
            overlap_chars: DEFAULT_OVERLAP_CHARS,
            max_recursion_depth: DEFAULT_MAX_RECURSION_DEPTH,
            min_items_for_recursion: DEFAULT_MIN_ITEMS_FOR_RECURSION,
            separator: DEFAULT_SEPARATOR.to_string(),
            preserve_metadata: true,
            oversized_item_strategy: OversizedItemStrategy::Split,
            consolidation_strategy: ConsolidationStrategy::Concatenate,
            continue_on_error: true,
            min_confidence_threshold: 0.0,
        }
    }
}

impl ProcessingConfig {
    /// A default configuration.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Validate the bounds.
    ///
    /// # Errors
    ///
    /// If `max_context_chars` is zero, `overlap_chars` exceeds half of it,
    /// `min_items_for_recursion` is zero, or `min_confidence_threshold` is
    /// outside `0.0..=1.0`.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.max_context_chars == 0 {
            return Err(ConfigError::NonPositiveMaxContextChars);
        }
        // An overlap at or above the window leaves no room to advance, so a
        // split would emit the same leading piece forever. Short of that, the
        // stride is `max_context_chars - overlap_chars` and the piece count
        // grows as it shrinks: at an overlap one below the window, a megabyte
        // becomes a million batches and a million model calls. Half the window
        // is the largest overlap that keeps the piece count within twice its
        // minimum.
        if self.overlap_chars * 2 > self.max_context_chars {
            return Err(ConfigError::OverlapTooLarge {
                overlap: self.overlap_chars,
                max_context_chars: self.max_context_chars,
            });
        }
        if self.min_items_for_recursion < 1 {
            return Err(ConfigError::MinItemsForRecursionZero);
        }
        if !(0.0..=1.0).contains(&self.min_confidence_threshold) {
            return Err(ConfigError::ConfidenceOutOfRange {
                value: self.min_confidence_threshold,
            });
        }
        Ok(())
    }

    /// Build and validate in one step.
    ///
    /// # Errors
    ///
    /// As [`Self::validate`].
    pub fn validated(self) -> Result<Self, ConfigError> {
        self.validate()?;
        Ok(self)
    }

    /// Set `max_context_chars`, returning the builder for chaining.
    #[must_use]
    pub fn with_max_context_chars(mut self, n: usize) -> Self {
        self.max_context_chars = n;
        self
    }

    /// Set `overlap_chars`.
    #[must_use]
    pub fn with_overlap_chars(mut self, n: usize) -> Self {
        self.overlap_chars = n;
        self
    }

    /// Set `max_recursion_depth`.
    #[must_use]
    pub fn with_max_recursion_depth(mut self, n: usize) -> Self {
        self.max_recursion_depth = n;
        self
    }

    /// Set `min_items_for_recursion`.
    #[must_use]
    pub fn with_min_items_for_recursion(mut self, n: usize) -> Self {
        self.min_items_for_recursion = n;
        self
    }

    /// Set the separator.
    #[must_use]
    pub fn with_separator(mut self, s: impl Into<String>) -> Self {
        self.separator = s.into();
        self
    }

    /// Set `oversized_item_strategy`.
    #[must_use]
    pub fn with_oversized_strategy(mut self, s: OversizedItemStrategy) -> Self {
        self.oversized_item_strategy = s;
        self
    }

    /// Set `consolidation_strategy`.
    #[must_use]
    pub fn with_consolidation_strategy(mut self, s: ConsolidationStrategy) -> Self {
        self.consolidation_strategy = s;
        self
    }

    /// Set `continue_on_error`.
    #[must_use]
    pub fn with_continue_on_error(mut self, v: bool) -> Self {
        self.continue_on_error = v;
        self
    }

    /// Set `min_confidence_threshold`.
    #[must_use]
    pub fn with_min_confidence_threshold(mut self, v: f64) -> Self {
        self.min_confidence_threshold = v;
        self
    }

    /// Set `preserve_metadata`.
    #[must_use]
    pub fn with_preserve_metadata(mut self, v: bool) -> Self {
        self.preserve_metadata = v;
        self
    }
}

/// What one extraction pass produced.
#[derive(Debug, Clone, PartialEq)]
pub struct ExtractionResult {
    /// The extracted or summarised text.
    pub content: String,
    /// Anything the extractor wants carried forward.
    pub metadata: BTreeMap<String, serde_json::Value>,
    /// Indices of the original items behind this result, for traceability.
    pub source_indices: Vec<usize>,
    /// 0.0–1.0 confidence. Used by the `Weighted` strategy and by
    /// `min_confidence_threshold`.
    pub confidence: f64,
    /// Which batch produced it.
    pub batch_index: Option<usize>,
    /// Depth it was produced at; 0 is the first pass.
    pub recursion_level: usize,
    /// True when extraction failed and this stands in for it.
    pub is_error: bool,
    /// Why, when `is_error`.
    pub error_message: Option<String>,
}

impl ExtractionResult {
    /// A successful result carrying `content`.
    #[must_use]
    pub fn new(content: impl Into<String>) -> Self {
        ExtractionResult {
            content: content.into(),
            metadata: BTreeMap::new(),
            source_indices: Vec::new(),
            confidence: 1.0,
            batch_index: None,
            recursion_level: 0,
            is_error: false,
            error_message: None,
        }
    }

    /// A zero-confidence result standing in for a failure.
    #[must_use]
    pub fn error(message: impl Into<String>) -> Self {
        let message = message.into();
        let mut metadata = BTreeMap::new();
        metadata.insert(
            "error".to_string(),
            serde_json::Value::String(message.clone()),
        );
        ExtractionResult {
            content: String::new(),
            metadata,
            source_indices: Vec::new(),
            confidence: 0.0,
            batch_index: None,
            recursion_level: 0,
            is_error: true,
            error_message: Some(message),
        }
    }

    /// Length of [`Self::content`] in characters.
    #[must_use]
    pub fn content_length(&self) -> usize {
        self.content.chars().count()
    }

    /// True when this is a non-error result carrying content.
    #[must_use]
    pub fn is_valid(&self) -> bool {
        !self.is_error && !self.content.is_empty()
    }

    /// Set the confidence, returning the builder for chaining.
    #[must_use]
    pub fn with_confidence(mut self, confidence: f64) -> Self {
        self.confidence = confidence;
        self
    }

    /// Set the metadata.
    #[must_use]
    pub fn with_metadata(mut self, metadata: BTreeMap<String, serde_json::Value>) -> Self {
        self.metadata = metadata;
        self
    }
}

/// An extraction result fed back in as an item for the next level.
///
/// The recursion changes what an "item" is: level 0 processes the caller's
/// items, every level above processes the results of the level below. Giving
/// those a type of their own is what lets `format_consolidated_item` exist —
/// upstream used an anonymous `(content, metadata)` tuple, so every subclass
/// had to sniff tuple shapes inside `format_item` to tell a consolidated item
/// from one of its own.
#[derive(Debug, Clone, PartialEq)]
pub struct ConsolidatedItem {
    /// The consolidated text.
    pub content: String,
    /// Metadata from the result it was made from.
    pub metadata: BTreeMap<String, serde_json::Value>,
}

impl ConsolidatedItem {
    /// Build a consolidated item.
    #[must_use]
    pub fn new(content: impl Into<String>, metadata: BTreeMap<String, serde_json::Value>) -> Self {
        ConsolidatedItem {
            content: content.into(),
            metadata,
        }
    }
}

/// Items grouped to fit one context window.
///
/// `items` holds the **original items with their source indices**, not their
/// rendered text, because a rendered text is not enough: `format_item`
/// receives the item's position within the batch, so a batch's content must be
/// produced by rendering each item at the index it finally landed at. Storing
/// text here would freeze a decoration that depends on position — and because
/// an item that no longer fits is re-measured at the head of a fresh batch,
/// that position genuinely changes during packing.
#[derive(Clone)]
pub struct Batch {
    /// The items in this batch, with their indices in the source list, in
    /// the order they were placed.
    pub items: Vec<(usize, crate::context_processor::ItemRef)>,
    /// Length of the batch's formatted content. Never greater than
    /// `config.max_context_chars`.
    pub total_chars: usize,
    /// Sequential index within the level.
    pub batch_index: usize,
}

impl std::fmt::Debug for Batch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Batch")
            .field("items", &self.items.len())
            .field("total_chars", &self.total_chars)
            .field("batch_index", &self.batch_index)
            .finish()
    }
}

impl Batch {
    /// Number of items in this batch.
    #[must_use]
    pub fn size(&self) -> usize {
        self.items.len()
    }

    /// The source indices of this batch's items.
    #[must_use]
    pub fn item_indices(&self) -> Vec<usize> {
        self.items.iter().map(|(idx, _)| *idx).collect()
    }
}

/// The complete report from a processing run.
#[derive(Debug, Clone)]
pub struct ProcessingResult {
    /// The consolidated result.
    pub final_result: ExtractionResult,
    /// How the run ended.
    pub status: ProcessingStatus,
    /// Items the caller supplied.
    pub total_items_processed: usize,
    /// Batches across every level.
    pub batches_created: usize,
    /// Consolidation passes beyond the first.
    pub recursion_levels_used: usize,
    /// Per-level results, when `store_intermediate`.
    pub intermediate_results: Option<Vec<Vec<ExtractionResult>>>,
    /// Why, when the run failed.
    pub error_message: Option<String>,
    /// Per-level counts.
    pub processing_stats: BTreeMap<String, serde_json::Value>,
    /// Indices of batches whose extraction raised. Batch indices restart at
    /// each level, so a value can repeat across a run that recursed.
    pub failed_batches: Vec<usize>,
    /// Indices of items dropped as oversized. At levels above 0 these index
    /// that level's consolidated items rather than the caller's list.
    pub skipped_items: Vec<usize>,
    /// Batches that produced a result.
    pub successful_batches: usize,
}

impl ProcessingResult {
    /// True when the run finished with nothing failed or truncated.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.status == ProcessingStatus::Completed
    }

    /// True when the run finished but something was lost.
    #[must_use]
    pub fn is_partial(&self) -> bool {
        self.status == ProcessingStatus::Partial
    }

    /// True when any batch failed or any item was skipped.
    #[must_use]
    pub fn has_failures(&self) -> bool {
        !self.failed_batches.is_empty() || !self.skipped_items.is_empty()
    }

    /// The final result's content.
    #[must_use]
    pub fn content(&self) -> &str {
        &self.final_result.content
    }

    /// Fraction of batches that produced a result.
    ///
    /// A run with no batches has no ratio to report, and the two ways of
    /// arriving there are opposites: an empty input had nothing that could
    /// fail (1.0), while a run whose every item was dropped as oversized
    /// produced nothing at all (0.0). Reporting 1.0 for both would have a
    /// total loss read as a clean run.
    #[must_use]
    pub fn success_rate(&self) -> f64 {
        if self.batches_created == 0 {
            return if self.has_failures() { 0.0 } else { 1.0 };
        }
        self.successful_batches as f64 / self.batches_created as f64
    }
}

/// A progress update handed to the caller's callback.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgressInfo {
    /// One of `starting`, `batching`, `extracting`, `recursing`, `complete`.
    pub stage: String,
    /// Items of this level accounted for so far — extracted, or dropped by
    /// the oversized strategy. An item dropped during packing counts
    /// immediately, since no extraction will ever reach it and a bar waiting
    /// for one would never fill.
    pub current_item: usize,
    /// Items at this level.
    pub total_items: usize,
    /// Batch index reached (1-based, for display).
    pub current_batch: usize,
    /// Batches at this level.
    pub total_batches: usize,
    /// Depth.
    pub recursion_level: usize,
    /// Human-readable summary.
    pub message: String,
}

impl ProgressInfo {
    /// Build a progress update with the given stage.
    #[must_use]
    pub fn new(stage: impl Into<String>) -> Self {
        ProgressInfo {
            stage: stage.into(),
            current_item: 0,
            total_items: 0,
            current_batch: 0,
            total_batches: 0,
            recursion_level: 0,
            message: String::new(),
        }
    }

    /// Progress through this level's items, 0.0–100.0.
    #[must_use]
    pub fn progress_percent(&self) -> f64 {
        if self.total_items == 0 {
            return 0.0;
        }
        (self.current_item as f64 / self.total_items as f64) * 100.0
    }
}
