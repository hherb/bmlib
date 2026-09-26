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

//! Process-wide token and cost accounting.

use bmlib::llm::{reset_token_tracker, with_token_tracker, TokenTracker};

/// **Every model is broken out**, and the totals are the sums of the parts — a
/// caller profiling a pipeline wants to know which model is costing them.
#[test]
fn the_summary_aggregates_by_model() {
    let tracker = TokenTracker::new();
    tracker.record_usage("anthropic:claude", 100, 50, 0.01);
    tracker.record_usage("anthropic:claude", 200, 60, 0.02);
    tracker.record_usage("openai:gpt-4o", 300, 70, 0.03);

    let summary = tracker.get_summary();
    assert_eq!(summary.total_input_tokens, 600);
    assert_eq!(summary.total_output_tokens, 180);
    assert_eq!(summary.total_tokens, 780, "the sum of the parts");
    assert_eq!(summary.call_count, 3);
    assert!((summary.total_cost_usd - 0.06).abs() < 1e-12);

    let claude = &summary.by_model["anthropic:claude"];
    assert_eq!(claude.input_tokens, 300);
    assert_eq!(claude.output_tokens, 110);
    assert_eq!(claude.calls, 2);
    let gpt = &summary.by_model["openai:gpt-4o"];
    assert_eq!(gpt.calls, 1);
    assert_eq!(summary.by_model.len(), 2);
}

/// An empty tracker summarises to zeroes rather than to a panic or a `None`.
#[test]
fn an_empty_tracker_summarises_to_zeroes() {
    let summary = TokenTracker::new().get_summary();
    assert_eq!(summary.total_tokens, 0);
    assert_eq!(summary.call_count, 0);
    assert!(summary.by_model.is_empty());
    assert_eq!(summary.total_cost_usd, 0.0);
}

/// **A `count` larger than what was recorded yields them all**, where a strict
/// slice would panic — the source's `[-count:]` does the same.
#[test]
fn recent_records_clamp_to_what_exists() {
    let tracker = TokenTracker::new();
    tracker.record_usage("m", 1, 1, 0.0);
    tracker.record_usage("m", 2, 2, 0.0);
    tracker.record_usage("m", 3, 3, 0.0);

    // The most recent two, **oldest first**.
    let recent = tracker.get_recent_records(2);
    assert_eq!(recent.len(), 2);
    assert_eq!(recent[0].input_tokens, 2);
    assert_eq!(recent[1].input_tokens, 3);

    // A count past the end is everything, not a panic.
    assert_eq!(tracker.get_recent_records(99).len(), 3);
    // And zero is nothing.
    assert!(tracker.get_recent_records(0).is_empty());
}

/// A record is stamped when it is **recorded**, not by the caller — a tracker
/// that accepted a timestamp could be handed one out of order, and the recent view
/// is positional rather than sorted.
#[test]
fn a_record_is_stamped_on_arrival() {
    let tracker = TokenTracker::new();
    let before = chrono::Utc::now();
    tracker.record_usage("m", 1, 1, 0.0);
    let after = chrono::Utc::now();
    let record = &tracker.records()[0];
    assert!(record.timestamp >= before && record.timestamp <= after);
    assert_eq!(record.model, "m");
}

/// `reset` clears the records **and** the running totals, so a later summary does
/// not report the earlier spend.
#[test]
fn reset_clears_records_and_totals() {
    let tracker = TokenTracker::new();
    tracker.record_usage("m", 100, 50, 1.0);
    tracker.reset();
    let summary = tracker.get_summary();
    assert_eq!(summary.total_input_tokens, 0);
    assert_eq!(summary.total_tokens, 0);
    assert_eq!(summary.total_cost_usd, 0.0);
    assert_eq!(summary.call_count, 0);
    assert!(summary.by_model.is_empty());
    assert!(tracker.get_recent_records(10).is_empty());

    // And it still works afterwards.
    tracker.record_usage("n", 5, 5, 0.0);
    assert_eq!(tracker.get_summary().call_count, 1);
}

/// The global tracker is shared, so two callers see each other's records — which
/// is the whole point of a process-wide figure.
#[test]
fn the_global_tracker_is_shared_and_resettable() {
    reset_token_tracker();
    with_token_tracker(|tracker| tracker.record_usage("global", 10, 20, 0.1));
    let total = with_token_tracker(|tracker| tracker.get_summary().total_tokens);
    assert_eq!(total, 30);

    reset_token_tracker();
    let after = with_token_tracker(|tracker| tracker.get_summary());
    assert_eq!(after.total_tokens, 0, "reset empties it");
    assert_eq!(after.call_count, 0);
}

/// The tracker is usable from several threads, and every call is counted — the
/// reason the Python holds a lock, and the reason this one takes `&self`.
#[test]
fn concurrent_recording_counts_every_call() {
    use std::sync::Arc;
    let tracker = Arc::new(TokenTracker::new());
    let mut handles = Vec::new();
    for _ in 0..8 {
        let tracker = Arc::clone(&tracker);
        handles.push(std::thread::spawn(move || {
            for _ in 0..100 {
                tracker.record_usage("m", 1, 2, 0.001);
            }
        }));
    }
    for handle in handles {
        handle.join().expect("thread");
    }
    let summary = tracker.get_summary();
    assert_eq!(summary.call_count, 800, "no lost updates");
    assert_eq!(summary.total_input_tokens, 800);
    assert_eq!(summary.total_output_tokens, 1600);
    assert!((summary.total_cost_usd - 0.8).abs() < 1e-9);
}

/// The per-model breakdown is **ordered**, so a caller rendering it gets the same
/// order twice and a test can compare it without sorting.
#[test]
fn the_breakdown_is_deterministically_ordered() {
    let tracker = TokenTracker::new();
    for model in ["zebra", "alpha", "middle"] {
        tracker.record_usage(model, 1, 1, 0.0);
    }
    let keys: Vec<String> = tracker.get_summary().by_model.keys().cloned().collect();
    assert_eq!(keys, vec!["alpha", "middle", "zebra"]);
}
