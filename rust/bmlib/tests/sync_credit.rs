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

//! `sync`'s per-day credits and counts — the oracle and the named tests.
//!
//! These are the arithmetic a finished day contributes to the report. Small
//! rules, and every one of them is a number a caller reads: the count stored for
//! a resumed day, the count a failed day reports, and the two shapes of error
//! line.

use bmlib::publications::sync::{
    carried_credit, day_error_line, day_record_count, failed_record_count, no_fetcher_line,
};
use bmlib::publications::PartCheckpoint;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

const CASES: &str = include_str!("data/sync_credit_cases.json");
const EXPECTED: &str = include_str!("data/sync_credit_expected.json");

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "carried_credit" => {
            let prior: BTreeMap<String, PartCheckpoint> = args["prior_parts"]
                .as_object()
                .map(|m| {
                    m.iter()
                        .map(|(k, v)| {
                            (
                                k.clone(),
                                PartCheckpoint {
                                    part_scheme: v["part_scheme"]
                                        .as_str()
                                        .unwrap_or_default()
                                        .to_string(),
                                    part_key: v["part_key"]
                                        .as_str()
                                        .unwrap_or_default()
                                        .to_string(),
                                    promised: v["promised"].as_i64().unwrap_or(0),
                                    record_count: v["record_count"].as_i64().unwrap_or(0),
                                },
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            let skipped: BTreeSet<String> = args["skipped_keys"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            json!(carried_credit(&prior, &skipped))
        }
        "day_record_count" => json!(day_record_count(
            args["added"].as_i64().unwrap_or(0),
            args["merged"].as_i64().unwrap_or(0),
            args["carried"].as_i64().unwrap_or(0),
        )),
        "failed_record_count" => json!(failed_record_count(
            args["added"].as_i64().unwrap_or(0),
            args["merged"].as_i64().unwrap_or(0),
            args["failed"].as_i64().unwrap_or(0),
            args["buffered"].as_u64().unwrap_or(0) as usize,
        )),
        "day_error_line" => json!(day_error_line(
            args["source"].as_str().unwrap_or_default(),
            args["date"].as_str().unwrap_or_default(),
            args["error"].as_str().unwrap_or_default(),
        )),
        "no_fetcher_line" => json!(no_fetcher_line(args["source"].as_str().unwrap_or_default())),
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

// ---------------------------------------------------------------------------
// The carried credit
// ---------------------------------------------------------------------------

fn checkpoint(key: &str, promised: i64, record_count: i64) -> PartCheckpoint {
    PartCheckpoint {
        part_scheme: "edat-range".to_string(),
        part_key: key.to_string(),
        promised,
        record_count,
    }
}

/// **Only the skipped parts are credited.** A prior part whose count moved is
/// re-walked, and its records are already in the totals — crediting it as well
/// would double them.
#[test]
fn only_a_skipped_part_is_credited() {
    let prior = BTreeMap::from([
        ("a".to_string(), checkpoint("a", 100, 90)),
        ("b".to_string(), checkpoint("b", 200, 180)),
    ]);

    // Nothing skipped: nothing carried.
    assert_eq!(carried_credit(&prior, &BTreeSet::new()), 0);

    // Only `a` skipped: only `a`'s records.
    let skipped = BTreeSet::from(["a".to_string()]);
    assert_eq!(
        carried_credit(&prior, &skipped),
        90,
        "a re-walked part's records are already in the totals"
    );

    // Both skipped: both.
    let skipped = BTreeSet::from(["a".to_string(), "b".to_string()]);
    assert_eq!(carried_credit(&prior, &skipped), 270);
}

/// The credit is the checkpoint's **`record_count`**, not its `promised`. The
/// promise is what the range was expected to hold; the record count is what was
/// actually stored for it, and it is the latter that describes what is in the
/// database.
#[test]
fn the_credit_is_what_was_stored_not_what_was_promised() {
    let prior = BTreeMap::from([("a".to_string(), checkpoint("a", 100, 42))]);
    let skipped = BTreeSet::from(["a".to_string()]);
    assert_eq!(carried_credit(&prior, &skipped), 42);
}

/// A part this run skipped but that has **no checkpoint** contributes nothing —
/// the two sets come from different places and need not nest.
#[test]
fn a_skipped_key_with_no_checkpoint_contributes_nothing() {
    let prior = BTreeMap::from([("a".to_string(), checkpoint("a", 100, 90))]);
    let skipped = BTreeSet::from(["a".to_string(), "b".to_string()]);
    assert_eq!(carried_credit(&prior, &skipped), 90);
}

// ---------------------------------------------------------------------------
// The counts
// ---------------------------------------------------------------------------

/// The day's stored count is `added + merged + carried`, so a day fetched across
/// three runs is not recorded as holding only the last run's share. It is
/// deliberately **not** the day's own promised count, which would make a resumed
/// day report a size it does not have.
#[test]
fn a_resumed_day_reports_what_it_holds() {
    assert_eq!(day_record_count(0, 0, 5_000), 5_000);
    assert_eq!(day_record_count(3, 4, 500), 507);
    assert_eq!(day_record_count(0, 0, 0), 0);
}

/// **A failed day reports the records already flushed by a finished part plus
/// the ones still buffered.** The buffer alone stopped being the day's whole
/// delivery when the part flush moved to a per-part boundary: reporting it alone
/// would understate a day that failed after several parts were stored.
#[test]
fn a_failed_day_counts_both_the_flushed_parts_and_the_buffer() {
    assert_eq!(
        failed_record_count(0, 0, 0, 12_000),
        12_000,
        "nothing flushed yet: the buffer is the whole delivery"
    );
    assert_eq!(
        failed_record_count(500, 100, 3, 700),
        1_303,
        "one part already stored, 700 still buffered"
    );
    assert_eq!(failed_record_count(500, 100, 0, 0), 600);
}

// ---------------------------------------------------------------------------
// The report lines
// ---------------------------------------------------------------------------

/// A day's error line is built the same way for both sources of a day's errors —
/// the fetch's own `error` and the day-status resolution's list — so they read
/// alike.
#[test]
fn a_day_error_line_names_the_source_and_the_day() {
    assert_eq!(
        day_error_line("pubmed", "2024-06-10", "RemoteProtocolError: timed out"),
        "pubmed/2024-06-10: RemoteProtocolError: timed out"
    );
    // An empty message still produces the prefix, which is what lets a caller
    // see *that* the day failed even when the message says nothing — the reason
    // the error field is checked with `is not None` and not for truthiness.
    assert_eq!(
        day_error_line("pubmed", "2024-06-10", ""),
        "pubmed/2024-06-10: "
    );
}

/// A source with no fetcher is **absent from `sources_synced`** — different from
/// a source whose days all failed, and the two must read differently.
#[test]
fn a_missing_fetcher_has_its_own_line() {
    assert_eq!(
        no_fetcher_line("not-a-source"),
        "No fetcher found for source: not-a-source"
    );
}
