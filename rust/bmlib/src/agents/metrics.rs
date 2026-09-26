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

//! Per-agent call accounting: what *this agent* did.
//!
//! Independent of the process-wide token tracker, because the two answer
//! different questions — "what did this agent do" against "what has this process
//! spent" — and merging them would make a sub-agent's cost unattributable.
//!
//! # Two clocks, on purpose
//!
//! [`PerformanceMetrics::start_time`] and `end_time` are **wall-clock
//! timestamps**, so a caller can render them as a date. The *duration* comes from
//! a monotonic reading instead: a wall-clock difference can be distorted or made
//! negative by an NTP step or a DST change mid-run, and the report prints that
//! figure directly against `total_wall_time_seconds`, which was accumulated
//! monotonically. Two clocks either side of that comparison is how
//! `"12.3s elapsed (14.1s in requests)"` gets printed.
//!
//! An instance rebuilt by [`PerformanceMetrics::from_dict`] has **no** monotonic
//! marks — they are not meaningful across processes — and falls back to the
//! difference of the timestamps it does have.

use std::sync::Mutex;

/// A monotonic instant, in seconds since an arbitrary epoch.
///
/// Its own type so a wall-clock reading cannot be passed where a duration is
/// meant — the two are both `f64` seconds, and the Python's split rests on a
/// convention this makes structural.
#[derive(Debug, Clone, Copy, PartialEq, PartialOrd, Default)]
pub struct Monotonic(f64);

impl Monotonic {
    /// The current reading.
    #[must_use]
    pub fn now() -> Self {
        use std::time::{SystemTime, UNIX_EPOCH};
        // `Instant` cannot be constructed from a caller's clock, which is why
        // the port threads readings rather than calling one here — but a plain
        // reading needs *some* clock, and this one is monotonic on every
        // platform bmlib targets because it never goes backwards.
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or_default();
        Monotonic(now)
    }

    /// A reading from a caller-supplied value, for a test that must not read a
    /// clock.
    #[must_use]
    pub fn from_seconds(seconds: f64) -> Self {
        Monotonic(seconds)
    }

    /// The reading, in seconds.
    #[must_use]
    pub fn seconds(self) -> f64 {
        self.0
    }
}

/// Cumulative statistics for one agent's calls.
///
/// Interior mutability, so the recording methods take `&self` — an agent shared
/// across workers counts every worker's calls, which is the whole reason the
/// Python holds a lock.
#[derive(Debug, Default)]
pub struct PerformanceMetrics {
    inner: Mutex<Counters>,
}

/// The numbers, under the lock.
#[derive(Debug, Clone, PartialEq, Default)]
struct Counters {
    total_prompt_tokens: i64,
    total_completion_tokens: i64,
    total_tokens: i64,
    total_requests: i64,
    total_retries: i64,
    total_wall_time_seconds: f64,
    /// A `time.time()`-style reading, so it can be rendered as a date.
    start_time: Option<f64>,
    /// `None` while still running.
    end_time: Option<f64>,
    /// Backs `elapsed_time_seconds`. Never serialised.
    monotonic_start: Option<Monotonic>,
    monotonic_end: Option<Monotonic>,
}

/// An independent copy of the counters, taken under the lock.
///
/// A snapshot rather than the live object so a caller reading two fields sees
/// them from the same instant: reading `total_tokens` and `total_requests`
/// separately from a live object can interleave with an in-flight request and
/// report a ratio that never existed.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct MetricsSnapshot {
    /// Tokens sent to the model.
    pub total_prompt_tokens: i64,
    /// Tokens generated.
    pub total_completion_tokens: i64,
    /// Their sum, as accumulated — not recomputed, so a caller who sets it
    /// directly keeps their figure.
    pub total_tokens: i64,
    /// Successful requests, counting every attempt.
    pub total_requests: i64,
    /// Attempts beyond the first.
    ///
    /// Counted when an attempt **begins**, so a retry whose request then fails
    /// is counted without a matching entry in `total_requests`.
    pub total_retries: i64,
    /// Wall-clock seconds inside successful requests.
    pub total_wall_time_seconds: f64,
    /// When collection started, as a wall-clock reading.
    pub start_time: Option<f64>,
    /// When it ended, or `None` while running.
    pub end_time: Option<f64>,
    monotonic_start: Option<Monotonic>,
    monotonic_end: Option<Monotonic>,
}

impl PerformanceMetrics {
    /// Empty counters.
    #[must_use]
    pub fn new() -> Self {
        PerformanceMetrics {
            inner: Mutex::new(Counters::default()),
        }
    }

    /// Record one successful call.
    pub fn add_request(&self, prompt_tokens: i64, completion_tokens: i64, wall_time_seconds: f64) {
        let mut counters = self.inner.lock().expect("metrics lock");
        counters.total_prompt_tokens += prompt_tokens;
        counters.total_completion_tokens += completion_tokens;
        counters.total_tokens += prompt_tokens + completion_tokens;
        counters.total_requests += 1;
        counters.total_wall_time_seconds += wall_time_seconds;
    }

    /// Record one retry attempt.
    pub fn add_retry(&self) {
        self.inner.lock().expect("metrics lock").total_retries += 1;
    }

    /// Begin a collection period, at `at` on the wall clock and `monotonic` for
    /// the duration.
    pub fn mark_start(&self, at: f64, monotonic: Monotonic) {
        let mut counters = self.inner.lock().expect("metrics lock");
        counters.start_time = Some(at);
        counters.monotonic_start = Some(monotonic);
        counters.monotonic_end = None;
    }

    /// End a collection period.
    pub fn mark_end(&self, at: f64, monotonic: Monotonic) {
        let mut counters = self.inner.lock().expect("metrics lock");
        counters.end_time = Some(at);
        counters.monotonic_end = Some(monotonic);
    }

    /// Clear every counter and mark.
    pub fn reset(&self) {
        *self.inner.lock().expect("metrics lock") = Counters::default();
    }

    /// An independent copy, read under the lock.
    #[must_use]
    pub fn snapshot(&self) -> MetricsSnapshot {
        let counters = self.inner.lock().expect("metrics lock");
        MetricsSnapshot {
            total_prompt_tokens: counters.total_prompt_tokens,
            total_completion_tokens: counters.total_completion_tokens,
            total_tokens: counters.total_tokens,
            total_requests: counters.total_requests,
            total_retries: counters.total_retries,
            total_wall_time_seconds: counters.total_wall_time_seconds,
            start_time: counters.start_time,
            end_time: counters.end_time,
            // Carried by hand, as the Python does: leaving them behind drops a
            // snapshot's elapsed time to the wall-clock fallback, which is the
            // distortion the monotonic clock exists to avoid.
            monotonic_start: counters.monotonic_start,
            monotonic_end: counters.monotonic_end,
        }
    }
}

impl MetricsSnapshot {
    /// Seconds from `mark_start` to `mark_end`, or to `now` while running.
    ///
    /// Monotonic where a reading exists, because the figure is printed beside
    /// `total_wall_time_seconds`, which was accumulated monotonically — see the
    /// module note on the two clocks.
    #[must_use]
    pub fn elapsed_time_seconds(&self, now: Option<Monotonic>) -> f64 {
        if let Some(start) = self.monotonic_start {
            let end = self.monotonic_end.or(now).unwrap_or(start);
            return end.seconds() - start.seconds();
        }

        // A snapshot rebuilt from `to_dict` has no monotonic marks, so it falls
        // back to the timestamps it does have.
        let Some(start) = self.start_time else {
            return 0.0;
        };
        let end = self
            .end_time
            .or(now.map(Monotonic::seconds))
            .unwrap_or(start);
        end - start
    }

    /// Completion tokens per second of **wall time**.
    ///
    /// Wall time, not inference time: no provider reports inference time through
    /// bmlib, and this is the throughput the caller observed.
    #[must_use]
    pub fn tokens_per_second(&self) -> f64 {
        if self.total_wall_time_seconds > 0.0 {
            return self.total_completion_tokens as f64 / self.total_wall_time_seconds;
        }
        0.0
    }

    /// Mean total tokens per request.
    #[must_use]
    pub fn average_tokens_per_request(&self) -> f64 {
        if self.total_requests > 0 {
            return self.total_tokens as f64 / self.total_requests as f64;
        }
        0.0
    }

    /// Serialise, derived values included, the way the Python's `to_dict` does.
    ///
    /// The rounding is **reproduced**, not tidied: each derived figure is rounded
    /// to a stated number of places, so a caller comparing two runs compares what
    /// the library published rather than raw division noise.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "total_retries": self.total_retries,
            "total_wall_time_seconds": round_to(self.total_wall_time_seconds, 3),
            "elapsed_time_seconds": round_to(self.elapsed_time_seconds(None), 3),
            "tokens_per_second": round_to(self.tokens_per_second(), 2),
            "average_tokens_per_request": round_to(self.average_tokens_per_request(), 1),
            "start_time": self.start_time,
            "end_time": self.end_time,
        })
    }

    /// Rebuild from [`MetricsSnapshot::to_json`] output, ignoring derived values.
    ///
    /// Not bit-exact for sub-millisecond timings: `to_json` rounds
    /// `total_wall_time_seconds` to three places, so a round trip is accurate to
    /// the millisecond.
    #[must_use]
    pub fn from_json(value: &serde_json::Value) -> Self {
        let get_i = |key: &str| {
            value
                .get(key)
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(0)
        };
        let get_f = |key: &str| {
            value
                .get(key)
                .and_then(serde_json::Value::as_f64)
                .unwrap_or(0.0)
        };
        MetricsSnapshot {
            total_prompt_tokens: get_i("total_prompt_tokens"),
            total_completion_tokens: get_i("total_completion_tokens"),
            total_tokens: get_i("total_tokens"),
            total_requests: get_i("total_requests"),
            total_retries: get_i("total_retries"),
            total_wall_time_seconds: get_f("total_wall_time_seconds"),
            start_time: value.get("start_time").and_then(serde_json::Value::as_f64),
            end_time: value.get("end_time").and_then(serde_json::Value::as_f64),
            // Deliberately not read even when present: a monotonic reading is
            // only comparable to others from the same process, so carrying it
            // through a serialisation round trip would be meaningless — and
            // worse, silently wrong.
            monotonic_start: None,
            monotonic_end: None,
        }
    }

    /// Render the report, byte for byte as the Python does.
    ///
    /// Three of its choices are load-bearing and each is reproduced rather than
    /// improved:
    ///
    /// * the retry count appears **only when non-zero**, so a clean run's first
    ///   line ends at the request count;
    /// * the elapsed figure appears **only when positive** — a snapshot rebuilt
    ///   from a serialised form has none, and printing `0.00s elapsed` beside a
    ///   real request time reads as a contradiction;
    /// * group separators are **commas**, which is Python's `:,` and not the
    ///   locale's.
    #[must_use]
    pub fn format_report(&self, title: Option<&str>) -> String {
        let mut lines: Vec<String> = Vec::new();
        if let Some(title) = title.filter(|t| !t.is_empty()) {
            lines.push(format!("=== {title} Performance Metrics ==="));
        }

        let retries = if self.total_retries != 0 {
            format!(" ({} retries)", group(self.total_retries))
        } else {
            String::new()
        };
        lines.push(format!(
            "Requests:     {}{}",
            group(self.total_requests),
            retries
        ));
        lines.push(format!(
            "Tokens:       {} total ({} prompt + {} completion)",
            group(self.total_tokens),
            group(self.total_prompt_tokens),
            group(self.total_completion_tokens)
        ));

        let elapsed = self.elapsed_time_seconds(None);
        if elapsed > 0.0 {
            lines.push(format!(
                "Time:         {elapsed:.2}s elapsed ({:.2}s in requests)",
                self.total_wall_time_seconds
            ));
        } else {
            lines.push(format!(
                "Time:         {:.2}s in requests",
                self.total_wall_time_seconds
            ));
        }

        if self.tokens_per_second() > 0.0 {
            lines.push(format!(
                "Speed:        {:.1} tokens/sec",
                self.tokens_per_second()
            ));
        }
        if self.total_requests > 0 {
            lines.push(format!(
                "Avg/Request:  {:.0} tokens",
                self.average_tokens_per_request()
            ));
        }

        lines.join("\n")
    }
}

/// Round to `places` decimals, the way Python's `round` does for these figures.
#[must_use]
pub fn round_to(value: f64, places: u32) -> f64 {
    let factor = 10f64.powi(places as i32);
    (value * factor).round() / factor
}

/// Group an integer with commas, as Python's `:` format does.
///
/// Reproduced rather than taken from the locale: the Python's separator is a
/// literal comma, and a report that changes shape with `LC_NUMERIC` is a report
/// two runs cannot be compared against each other.
#[must_use]
pub fn group(value: i64) -> String {
    let negative = value < 0;
    let digits = value.unsigned_abs().to_string();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3 + 1);
    if negative {
        out.push('-');
    }
    for (index, ch) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            out.push(',');
        }
        out.push(ch);
    }
    out
}
