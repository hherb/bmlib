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
//!
//! **Independent of `agents::metrics`, and the two answer different questions**:
//! this one is *what has this process spent*, and that one is *what did this
//! agent do*. Merging them would make a single agent's cost unattributable, which
//! is the figure a caller profiling a pipeline actually wants.

use chrono::{DateTime, Utc};
use std::collections::BTreeMap;
use std::sync::{Mutex, OnceLock};

/// One recorded call.
#[derive(Debug, Clone, PartialEq)]
pub struct TokenUsageRecord {
    /// The model that answered.
    pub model: String,
    /// Tokens sent.
    pub input_tokens: i64,
    /// Tokens generated.
    pub output_tokens: i64,
    /// When the call was recorded.
    pub timestamp: DateTime<Utc>,
    /// What it cost, as the provider priced it.
    pub cost_usd: f64,
}

/// One model's share of the total.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ModelUsage {
    /// Tokens sent to this model.
    pub input_tokens: i64,
    /// Tokens it generated.
    pub output_tokens: i64,
    /// What it cost.
    pub cost_usd: f64,
    /// How many calls it answered.
    pub calls: i64,
}

/// An aggregate over every recorded call.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct TokenUsageSummary {
    /// Tokens sent, across all models.
    pub total_input_tokens: i64,
    /// Tokens generated, across all models.
    pub total_output_tokens: i64,
    /// Their sum.
    pub total_tokens: i64,
    /// Total cost.
    pub total_cost_usd: f64,
    /// How many calls were recorded.
    pub call_count: i64,
    /// Per-model breakdown, keyed by the model string.
    ///
    /// A `BTreeMap` rather than a hash map, so the order is stable: a caller
    /// rendering the breakdown gets the same order twice, and a test can compare
    /// it without sorting.
    pub by_model: BTreeMap<String, ModelUsage>,
}

#[derive(Debug, Default)]
struct Inner {
    records: Vec<TokenUsageRecord>,
    total_input: i64,
    total_output: i64,
    total_cost: f64,
}

/// A thread-safe token and cost tracker.
///
/// Interior mutability, so recording takes `&self` — the tracker is shared across
/// a process, and `+=` on shared counters is a read-modify-write.
#[derive(Debug, Default)]
pub struct TokenTracker {
    inner: Mutex<Inner>,
}

impl TokenTracker {
    /// An empty tracker.
    #[must_use]
    pub fn new() -> Self {
        TokenTracker::default()
    }

    /// Record one call.
    pub fn record_usage(&self, model: &str, input_tokens: i64, output_tokens: i64, cost: f64) {
        let mut inner = self.inner.lock().expect("tracker lock");
        inner.records.push(TokenUsageRecord {
            model: model.to_string(),
            input_tokens,
            output_tokens,
            // Stamped here rather than taken from the caller: a tracker that
            // accepted a timestamp could be handed one out of order, and the
            // recent-records view is positional rather than sorted.
            timestamp: Utc::now(),
            cost_usd: cost,
        });
        inner.total_input += input_tokens;
        inner.total_output += output_tokens;
        inner.total_cost += cost;
    }

    /// An aggregate over every recorded call.
    #[must_use]
    pub fn get_summary(&self) -> TokenUsageSummary {
        let inner = self.inner.lock().expect("tracker lock");
        let mut by_model: BTreeMap<String, ModelUsage> = BTreeMap::new();
        for record in &inner.records {
            let entry = by_model.entry(record.model.clone()).or_default();
            entry.input_tokens += record.input_tokens;
            entry.output_tokens += record.output_tokens;
            entry.cost_usd += record.cost_usd;
            entry.calls += 1;
        }
        TokenUsageSummary {
            total_input_tokens: inner.total_input,
            total_output_tokens: inner.total_output,
            // Summed from the parts rather than accumulated separately, so the
            // figure cannot drift from them.
            total_tokens: inner.total_input + inner.total_output,
            total_cost_usd: inner.total_cost,
            call_count: inner.records.len() as i64,
            by_model,
        }
    }

    /// Clear every record and counter.
    pub fn reset(&self) {
        let mut inner = self.inner.lock().expect("tracker lock");
        inner.records.clear();
        inner.total_input = 0;
        inner.total_output = 0;
        inner.total_cost = 0.0;
    }

    /// The `count` most recent records, oldest first.
    ///
    /// A `count` larger than the number recorded yields them all — the source's
    /// `[-count:]` does the same, where a strict slice would panic.
    #[must_use]
    pub fn get_recent_records(&self, count: usize) -> Vec<TokenUsageRecord> {
        let inner = self.inner.lock().expect("tracker lock");
        let start = inner.records.len().saturating_sub(count);
        inner.records[start..].to_vec()
    }

    /// Every record, for a caller that wants the lot.
    #[must_use]
    pub fn records(&self) -> Vec<TokenUsageRecord> {
        self.inner.lock().expect("tracker lock").records.clone()
    }
}

/// The process-wide tracker.
static GLOBAL: OnceLock<Mutex<TokenTracker>> = OnceLock::new();

fn global() -> &'static Mutex<TokenTracker> {
    GLOBAL.get_or_init(|| Mutex::new(TokenTracker::new()))
}

/// Run a closure against the global tracker.
///
/// A closure rather than returning a reference, because the tracker is behind a
/// mutex and handing out a guard would let a caller hold the process-wide lock
/// across unrelated work — including another `with_token_tracker` call, which
/// would deadlock.
pub fn with_token_tracker<R>(f: impl FnOnce(&TokenTracker) -> R) -> R {
    let guard = global().lock().expect("global tracker lock");
    f(&guard)
}

/// Replace the global tracker's contents with an empty tracker.
///
/// The Python swaps the object for a fresh instance. Kept as a reset of the same
/// instance: anything already holding the global's address keeps working, where a
/// swap would leave such a holder recording into an orphan.
pub fn reset_token_tracker() {
    global().lock().expect("global tracker lock").reset();
}
