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

//! PubMed transport reading and the day-level branch.
//!
//! The corpus (16 cases) diffs ESearch reading against Python. The named tests
//! state why a rejected search must not read as a quiet day, and pin the day
//! branch's ordering.

use bmlib::publications::fetchers::pubmed::{
    checkpointed_but_empty_message, day_step, read_esearch, DayStep, EFETCH_MAX_RETRIEVABLE,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/pubmed_transport_cases.json");
const EXPECTED: &str = include_str!("data/pubmed_transport_expected.json");

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "read_esearch" => match read_esearch(args["xml"].as_str().unwrap_or_default()) {
            Ok(result) => json!({
                "ok": true,
                "value": {
                    "count": result.count,
                    "web_env": result.web_env,
                    "query_key": result.query_key,
                }
            }),
            Err(e) => json!({"ok": false, "error": e}),
        },
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
            "{name}: Python errored: {}",
            want["error"]
        );
        // The corpus records a `{ok, value|error}` result for the reading cases;
        // the request-shape cases are exercised by the named tests instead,
        // because the URL a transport builds is its own business.
        if case["fn"] != "read_esearch" {
            continue;
        }
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
// Reading an ESearch
// ---------------------------------------------------------------------------

/// **An `<ERROR>` document must not read as a quiet day.** NCBI answers a bad
/// request — an unknown db, an invalid term, a throttled key — with HTTP 200 and
/// an error document that has no `<Count>` at all. Treating an absent element as
/// zero would report a rejected search as a day with no publications.
#[test]
fn a_rejected_search_is_an_error_not_a_quiet_day() {
    let err = read_esearch("<eSearchResult><ERROR>Invalid db</ERROR></eSearchResult>")
        .expect_err("refused");
    assert!(err.contains("no usable <Count>"), "{err}");
    assert!(
        err.contains("Invalid db"),
        "NCBI's own words must appear: {err}"
    );

    // A truthiness fallback would collapse this into "the day was quiet", reached
    // one step earlier than the session guard — and past it, since an error
    // document carries no session either.
    let quiet = read_esearch("<eSearchResult><Count>0</Count></eSearchResult>").expect("a number");
    assert_eq!(quiet.count, 0, "a real zero is a number, not an error");
}

/// A count that is not a whole number is refused, and the refusal names no NCBI
/// error because there is none to quote.
#[test]
fn a_non_numeric_count_is_refused() {
    for xml in [
        "<eSearchResult><Count>many</Count></eSearchResult>",
        "<eSearchResult><Count>4.2</Count></eSearchResult>",
        "<eSearchResult><Count>-1</Count></eSearchResult>",
        "<eSearchResult><Count></Count></eSearchResult>",
        "<eSearchResult></eSearchResult>",
    ] {
        let err = read_esearch(xml).expect_err("refused");
        assert!(err.contains("no usable <Count>"), "{xml} gave {err}");
        assert!(
            !err.contains("NCBI said"),
            "{xml} has no error to quote: {err}"
        );
    }
}

/// A padded count is read, because the element is text and the source pads.
#[test]
fn a_padded_count_is_read() {
    let result =
        read_esearch("<eSearchResult><Count>  42  </Count></eSearchResult>").expect("readable");
    assert_eq!(result.count, 42);
}

/// The session is read independently of the count, so a count with no session
/// arrives as `None` rather than as an error — which is what lets the day branch
/// decide what that means.
#[test]
fn a_count_without_a_session_reads_as_none() {
    let result =
        read_esearch("<eSearchResult><Count>42</Count></eSearchResult>").expect("readable");
    assert_eq!(result.count, 42);
    assert_eq!(result.web_env, None);
    assert_eq!(result.query_key, None);

    let with = read_esearch(
        "<eSearchResult><Count>42</Count><WebEnv>W</WebEnv><QueryKey>1</QueryKey></eSearchResult>",
    )
    .expect("readable");
    assert_eq!(with.web_env.as_deref(), Some("W"));
    assert_eq!(with.query_key.as_deref(), Some("1"));
}

// ---------------------------------------------------------------------------
// The day branch
// ---------------------------------------------------------------------------

/// **No records, no checkpoints** is the ordinary quiet day and completes.
#[test]
fn a_quiet_day_completes() {
    assert_eq!(day_step(0, true, 0), DayStep::QuietDay);
    assert_eq!(day_step(0, false, 0), DayStep::QuietDay);
}

/// **No records *with* checkpoints is refused**, and this pair is the widest of
/// bmlib's own two counts: an earlier run walked, stored and checkpointed these
/// parts, and the day now claims to hold nothing at all.
///
/// Completing on the weaker one is worse here than at part level, because `sync`
/// drops this day's part rows the moment it completes — so the same transaction
/// that loses the records destroys the checkpoints that would have made
/// re-fetching them cheap.
#[test]
fn a_zero_under_load_is_refused_when_parts_are_checkpointed() {
    assert_eq!(
        day_step(0, true, 3),
        DayStep::CheckpointedButEmpty {
            parts: 3,
            records: 0
        }
    );
    assert!(day_step(0, true, 3).is_refusal());

    let message = checkpointed_but_empty_message("2024-06-10", 3, 12_000);
    assert!(message.contains("2024-06-10"), "{message}");
    assert!(message.contains("3 part(s)"), "{message}");
    assert!(message.contains("12000 records"), "{message}");
    // The remedy is stated, because the operator's next question is what to do.
    assert!(message.contains("download_day_parts"), "{message}");
}

/// **The over-cap branch sits ahead of the session guard**, and the order is
/// load-bearing: the session opened at day level is unused on that path, since
/// each part opens its own — so a day-level search reporting a count without a
/// `WebEnv` is no obstacle to fetching the day, and refusing would lose a
/// fetchable day to a re-offer on every later run.
#[test]
fn an_over_cap_day_is_partitioned_even_without_a_session() {
    assert_eq!(
        day_step(EFETCH_MAX_RETRIEVABLE + 1, false, 0),
        DayStep::Partitioned,
        "an over-cap day must not be refused for a missing session"
    );
    assert!(!day_step(EFETCH_MAX_RETRIEVABLE + 1, false, 0).is_refusal());
}

/// An under-cap day **without** a session is refused: ESearch is sent
/// `usehistory=y` and every page reads the session back, so without it each page
/// asks for an empty `WebEnv` and gets a document holding no articles — an
/// unguarded fetch walks the entire count in useless requests and then reports
/// `completed` with nothing. A broken fetch wearing the shape of a quiet day.
#[test]
fn an_under_cap_day_without_a_session_is_refused() {
    assert_eq!(day_step(100, false, 0), DayStep::NoSession);
    assert!(day_step(100, false, 0).is_refusal());

    // With a session it walks normally.
    assert_eq!(day_step(100, true, 0), DayStep::SingleSession);
    assert!(!day_step(100, true, 0).is_refusal());
}

/// The cap is **inclusive**: a day of exactly the cap is one session, not parts.
#[test]
fn the_cap_is_inclusive() {
    assert_eq!(
        day_step(EFETCH_MAX_RETRIEVABLE, true, 0),
        DayStep::SingleSession
    );
    assert_eq!(day_step(1, true, 0), DayStep::SingleSession);
}
