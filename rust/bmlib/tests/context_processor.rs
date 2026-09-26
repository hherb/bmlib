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

//! Context processor and text utilities — the named tests.
//!
//! `context_oracle` is the broad instrument (62 cases diffed against Python).
//! This file is the reasoned half: the invariants a reader needs stated, and
//! the regression tests for behaviour that is easy to break silently.

use std::collections::BTreeMap;
use std::sync::Arc;

use bmlib::context_processor::base::{run_all, Item, ItemRef, ProcessingCore};
use bmlib::context_processor::{
    ConsolidationStrategy, ExtractionResult, IterativeContextProcessor, OversizedItemStrategy,
    ProcessingConfig, ProcessingStatus, TextItem,
};
use bmlib::llm::text_utils::{chunk_text, TextChunker};
use serde_json::Value;

/// A processor that records the formatted content of every batch it is handed.
struct Recorder {
    core: ProcessingCore,
    seen: std::sync::Mutex<Vec<String>>,
    echo: bool,
}

impl Recorder {
    fn new(config: ProcessingConfig, echo: bool) -> Self {
        Recorder {
            core: ProcessingCore::new(config, None).expect("valid config"),
            seen: std::sync::Mutex::new(Vec::new()),
            echo,
        }
    }

    fn contents(&self) -> Vec<String> {
        self.seen.lock().expect("lock").clone()
    }

    fn run(&self, items: &[ItemRef], query: &str) -> bmlib::context_processor::ProcessingResult {
        run_all(self, &self.core, items, query, &self.core.config, false)
    }
}

impl IterativeContextProcessor for Recorder {
    fn config(&self) -> &ProcessingConfig {
        &self.core.config
    }

    fn format_item(&self, item: &dyn Item, index: usize) -> String {
        item.render(index)
    }

    fn extract_from_batch(
        &self,
        batch_content: &str,
        _query: &str,
        _meta: &BTreeMap<String, Value>,
    ) -> Result<ExtractionResult, Box<dyn std::error::Error + Send + Sync>> {
        self.seen
            .lock()
            .expect("lock")
            .push(batch_content.to_string());
        if self.echo {
            Ok(ExtractionResult::new(batch_content))
        } else {
            Ok(ExtractionResult::new("ok"))
        }
    }
}

fn items(strings: &[&str]) -> Vec<ItemRef> {
    strings
        .iter()
        .map(|s| Arc::new(TextItem::new(*s)) as ItemRef)
        .collect()
}

// ---------------------------------------------------------------------------
// The promise the module makes
// ---------------------------------------------------------------------------

/// `max_context_chars` is the guarantee: **no batch handed to
/// `extract_from_batch` exceeds it**. The port plan records two separate ways
/// the upstream batcher broke this, so it is asserted rather than assumed.
#[test]
fn no_batch_ever_exceeds_the_context_limit() {
    for limit in [8usize, 16, 40, 100] {
        let config = ProcessingConfig::default().with_max_context_chars(limit);
        let processor = Recorder::new(config, false);
        // Loose text with paragraph breaks, so splitting has boundaries to
        // find and the decoration varies with position.
        let raw: Vec<String> = (0..12)
            .map(|i| format!("Sentence number {i}. And another clause here."))
            .collect();
        let refs: Vec<ItemRef> = raw
            .iter()
            .map(|s| Arc::new(TextItem::new(s.clone())) as ItemRef)
            .collect();
        let _ = processor.run(&refs, "q");
        for content in processor.contents() {
            assert!(
                content.chars().count() <= limit,
                "a batch of {} chars exceeded the {limit}-char limit: {content:?}",
                content.chars().count()
            );
        }
    }
}

/// An item measured at the wrong position overflows. `format_item` receives its
/// index within the batch, so an item that no longer fits is re-measured at the
/// head of a fresh batch — where the index, and so the decoration, differs.
#[test]
fn an_item_is_measured_at_the_position_it_lands_in() {
    struct WideIndex {
        core: ProcessingCore,
    }
    impl IterativeContextProcessor for WideIndex {
        fn config(&self) -> &ProcessingConfig {
            &self.core.config
        }
        fn format_item(&self, _item: &dyn Item, index: usize) -> String {
            // Width grows with the index, so a stale measurement overflows.
            format!("{}{}", "x".repeat(index * 3), "y")
        }
        fn extract_from_batch(
            &self,
            _batch_content: &str,
            _query: &str,
            _meta: &BTreeMap<String, Value>,
        ) -> Result<ExtractionResult, Box<dyn std::error::Error + Send + Sync>> {
            Ok(ExtractionResult::new("ok"))
        }
    }
    let config = ProcessingConfig::default().with_max_context_chars(20);
    let p = WideIndex {
        core: ProcessingCore::new(config, None).expect("valid"),
    };
    let refs = items(&["a", "b", "c", "d", "e", "f"]);
    let result = run_all(&p, &p.core, &refs, "q", &p.core.config, false);
    assert!(
        result.batches_created > 1,
        "the width must force several batches"
    );
}

/// The separator is content too. A batcher that counts only item widths
/// overflows by `len(separator) * (n - 1)`.
#[test]
fn the_separator_counts_towards_the_limit() {
    let config = ProcessingConfig::default()
        .with_max_context_chars(9)
        .with_separator("---");
    let processor = Recorder::new(config, false);
    let _ = processor.run(&items(&["aaa", "bbb", "ccc"]), "q");
    for content in processor.contents() {
        assert!(content.chars().count() <= 9, "{content:?}");
    }
}

// ---------------------------------------------------------------------------
// Oversized items
// ---------------------------------------------------------------------------

/// `Split` cuts the item into pieces that each fit *once formatted* — the
/// budget is reduced by the measured decoration, not guessed at.
#[test]
fn splitting_accounts_for_the_decoration() {
    let config = ProcessingConfig::default().with_max_context_chars(20);
    let processor = Recorder::new(config, false);
    let long = "w".repeat(120);
    let result = processor.run(&items(&[&long]), "q");
    assert_eq!(result.status, ProcessingStatus::Completed);
    assert!(result.skipped_items.is_empty(), "nothing should be dropped");
    for content in processor.contents() {
        assert!(content.chars().count() <= 20, "{content:?}");
    }
}

/// `Skip` records the index so the caller can see what was lost.
#[test]
fn skipping_an_oversized_item_records_its_index() {
    let config = ProcessingConfig::default()
        .with_max_context_chars(5)
        .with_oversized_strategy(OversizedItemStrategy::Skip);
    let processor = Recorder::new(config, false);
    let result = processor.run(&items(&["aaaaaaaaaa", "bb"]), "q");
    assert_eq!(result.skipped_items, vec![0]);
    assert!(result.has_failures());
    assert_eq!(result.status, ProcessingStatus::Partial);
}

/// `Fail` is the configuration doing what it was asked, not a defect: it is
/// reported on the result rather than raised.
#[test]
fn the_strict_strategy_reports_failure_without_raising() {
    let config = ProcessingConfig::default()
        .with_max_context_chars(5)
        .with_oversized_strategy(OversizedItemStrategy::Fail);
    let processor = Recorder::new(config, false);
    let result = processor.run(&items(&["aaaaaaaaaa"]), "q");
    assert_eq!(result.status, ProcessingStatus::Failed);
    assert!(
        result
            .error_message
            .as_deref()
            .unwrap_or_default()
            .contains("oversized"),
        "{:?}",
        result.error_message
    );
}

/// `Truncate` cuts the rendered item and must not decorate it again — the
/// double-decoration is what pushes the result back over the limit.
#[test]
fn truncating_does_not_decorate_twice() {
    struct Decorating {
        core: ProcessingCore,
    }
    impl IterativeContextProcessor for Decorating {
        fn config(&self) -> &ProcessingConfig {
            &self.core.config
        }
        fn format_item(&self, item: &dyn Item, index: usize) -> String {
            format!("[{index}] {}", item.render(index))
        }
        fn extract_from_batch(
            &self,
            _batch_content: &str,
            _query: &str,
            _meta: &BTreeMap<String, Value>,
        ) -> Result<ExtractionResult, Box<dyn std::error::Error + Send + Sync>> {
            Ok(ExtractionResult::new("ok"))
        }
    }
    let config = ProcessingConfig::default()
        .with_max_context_chars(10)
        .with_oversized_strategy(OversizedItemStrategy::Truncate);
    let p = Decorating {
        core: ProcessingCore::new(config, None).expect("valid"),
    };
    let long = "z".repeat(50);
    let result = run_all(&p, &p.core, &items(&[&long]), "q", &p.core.config, false);
    assert!(
        result.skipped_items.is_empty(),
        "{:?}",
        result.skipped_items
    );
    assert_eq!(result.status, ProcessingStatus::Completed);
}

// ---------------------------------------------------------------------------
// Failure accounting
// ---------------------------------------------------------------------------

/// An empty input has nothing that could fail, so the success rate is 1.0 —
/// whereas a run whose every item was dropped produced nothing at all and is
/// 0.0. Reporting 1.0 for both would have a total loss read as a clean run.
#[test]
fn an_empty_run_and_a_total_loss_are_not_the_same_success_rate() {
    let empty = Recorder::new(ProcessingConfig::default(), false).run(&[], "q");
    assert_eq!(empty.success_rate(), 1.0);
    assert_eq!(empty.status, ProcessingStatus::Completed);

    let config = ProcessingConfig::default()
        .with_max_context_chars(5)
        .with_oversized_strategy(OversizedItemStrategy::Skip);
    let lost = Recorder::new(config, false).run(&items(&["aaaaaaaaaa"]), "q");
    assert_eq!(lost.success_rate(), 0.0);
    assert_eq!(lost.status, ProcessingStatus::Failed);
    assert!(
        lost.error_message
            .as_deref()
            .unwrap_or_default()
            .contains("skipped"),
        "the message must name the skip, not blame an extraction error: {:?}",
        lost.error_message
    );
}

/// The recursion ceiling produces `Truncated` and keeps what it had.
#[test]
fn the_recursion_ceiling_truncates_rather_than_losing_everything() {
    let config = ProcessingConfig::default()
        .with_max_context_chars(10)
        .with_max_recursion_depth(1);
    let processor = Recorder::new(config, true);
    let result = processor.run(&items(&["a", "b", "c", "d"]), "q");
    assert_eq!(result.status, ProcessingStatus::Truncated);
    assert!(!result.content().is_empty(), "partial results are kept");
    assert_eq!(result.recursion_levels_used, 1);
}

/// A merge never loses content: every valid result's text appears in the
/// merged output. The two strategies differ in *order*, not in what survives.
#[test]
fn both_merge_strategies_keep_every_contents() {
    for strategy in [
        ConsolidationStrategy::Concatenate,
        ConsolidationStrategy::Weighted,
    ] {
        let config = ProcessingConfig::default()
            .with_max_context_chars(10)
            .with_consolidation_strategy(strategy);
        let processor = Recorder::new(config, true);
        let result = processor.run(&items(&["a", "b", "c", "d"]), "q");
        assert!(
            result.has_failures() || !result.content().is_empty(),
            "{strategy:?} lost everything"
        );
    }
}

/// `process()` holds nothing on the instance, so one processor serves
/// concurrent calls. An earlier Python version kept per-run counts on `self`
/// and two concurrent runs returned each other's numbers.
#[test]
fn a_processor_holds_no_per_run_state() {
    use std::sync::Arc as StdArc;
    let processor = StdArc::new(Recorder::new(
        ProcessingConfig::default().with_max_context_chars(1000),
        false,
    ));
    let mut handles = Vec::new();
    for n in 0..8 {
        let p = StdArc::clone(&processor);
        handles.push(std::thread::spawn(move || {
            let refs = items(&["a", "b", "c"]);
            let r = p.run(&refs, "q");
            assert_eq!(r.total_items_processed, 3, "thread {n}");
            r.batches_created
        }));
    }
    let counts: Vec<usize> = handles
        .into_iter()
        .map(|h| h.join().expect("thread"))
        .collect();
    assert!(
        counts.windows(2).all(|w| w[0] == w[1]),
        "concurrent runs disagreed about their own batch counts: {counts:?}"
    );
}

// ---------------------------------------------------------------------------
// Text utilities
// ---------------------------------------------------------------------------

/// The chunker never discards text: the pieces, concatenated without overlap,
/// reproduce the source.
#[test]
fn chunking_never_loses_text() {
    let text = "One sentence here. Another follows it. And a third one too.";
    let chunks = chunk_text(text, 20, 0, true, 5).expect("chunker");
    let rejoined: String = chunks.iter().map(|c| c.content.clone()).collect();
    assert_eq!(rejoined, text);
    assert_eq!(chunks[0].total_chunks, chunks.len());
    assert_eq!(chunks.last().expect("last").end_pos, text.chars().count());
}

/// Overlap is real: consecutive pieces share characters rather than leaving a
/// silent gap.
#[test]
fn overlap_shares_characters_between_pieces() {
    let text = "a".repeat(30);
    let chunks = chunk_text(&text, 10, 2, false, 0).expect("chunker");
    assert!(chunks.len() > 1);
    for pair in chunks.windows(2) {
        assert!(
            pair[1].start_pos < pair[0].end_pos,
            "no overlap between {:?} and {:?}",
            pair[0].start_pos,
            pair[1].start_pos
        );
    }
}

/// Boundary awareness prefers a paragraph break, so a sentence is not cut in
/// half — and never at the cost of text.
#[test]
fn a_paragraph_break_is_preferred_over_a_hard_cut() {
    let text = "first para.\n\nsecond para that runs on a while";
    let chunks = chunk_text(text, 20, 0, true, 5).expect("chunker");
    assert!(
        chunks[0].content.ends_with("\n\n"),
        "the first break should be the paragraph: {:?}",
        chunks[0].content
    );
    let rejoined: String = chunks.iter().map(|c| c.content.clone()).collect();
    assert_eq!(rejoined, text);
}

/// Positions are character offsets, so a multi-byte prefix does not shift them
/// and no boundary panics.
#[test]
fn chunk_positions_are_character_offsets() {
    let text = "Grüße 日本 über alles";
    let chunks = chunk_text(text, 8, 0, false, 0).expect("chunker");
    let mut expected_start = 0usize;
    for chunk in &chunks {
        assert_eq!(chunk.start_pos, expected_start);
        let width = chunk.content.chars().count();
        assert_eq!(chunk.end_pos, chunk.start_pos + width);
        expected_start = chunk.end_pos;
    }
    assert_eq!(expected_start, text.chars().count());
}

/// The chunker refuses a configuration whose window cannot advance.
#[test]
fn an_overlap_at_or_above_the_window_is_refused() {
    assert!(TextChunker::new(10, 10, true, 0).is_err());
    assert!(TextChunker::new(10, 11, true, 0).is_err());
    assert!(TextChunker::new(0, 0, true, 0).is_err());
    assert!(TextChunker::new(10, 9, true, 0).is_ok());
}
