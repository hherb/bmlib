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

//! `TransparencyResult`'s persistence contract, against Python's.
//!
//! The oracle (27 cases) diffs `to_dict`, a round trip and `from_dict`'s defaults.
//! The named tests state what the diff cannot: why the three enum fields are
//! `null` rather than a member, and where #306 makes the port disagree.

use bmlib::transparency::analyzer::TransparencyResult;
use bmlib::transparency::models::{
    FullTextStatus, TransparencyRisk, TransparencyUnknownReason, TrialResultsStatus,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/result_dict_cases.json");
const EXPECTED: &str = include_str!("data/result_dict_expected.json");

/// The instant every case pins, so `analyzed_at` is a value and not a clock read.
const FIXED: &str = "2026-09-26T10:30:00+00:00";

fn build(spec: &Value) -> Result<TransparencyResult, String> {
    let mut data = spec.clone();
    // The corpus carries the instant as text; `from_dict` parses it.
    data["analyzed_at"] = json!(FIXED);
    TransparencyResult::from_dict(&data)
}

fn run(case: &Value) -> Result<Value, String> {
    let a = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "to_dict" => Ok(build(&a["result"])?.to_dict()),
        "from_dict" => Ok(TransparencyResult::from_dict(&a["data"])?.to_dict()),
        "round_trip" => {
            let first = build(&a["result"])?.to_dict();
            let again = TransparencyResult::from_dict(&first)?.to_dict();
            Ok(json!({"first": first, "again": again}))
        }
        other => Err(format!("unknown fn {other:?}")),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected.as_array().expect("expected is a list");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );

        let Ok(got) = run(case) else {
            failures.push(format!("  {name}: the port refused a case Python accepted"));
            continue;
        };
        // A `corrected` case is one where the port deliberately differs — the
        // expectation is then this port's own output, stated in the corpus.
        if case.get("corrected").is_some() {
            if !diverges_as_documented(&got) {
                failures.push(format!(
                    "  {name}: undocumented divergence — {}",
                    serde_json::to_string(&got).unwrap_or_default()
                ));
            }
            continue;
        }
        // A case carrying `volatile` names fields that are a **clock read** in
        // both languages, so their values cannot be diffed; the named fields are
        // compared by the test that owns the rule.
        let (got, want_value) = match case.get("volatile").and_then(Value::as_array) {
            Some(fields) => {
                let mut got = got;
                let mut want_value = want["value"].clone();
                for field in fields.iter().filter_map(Value::as_str) {
                    got[field] = Value::Null;
                    want_value[field] = Value::Null;
                }
                (got, want_value)
            }
            None => (got, want["value"].clone()),
        };
        if got != want_value {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want_value).unwrap_or_default(),
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

/// The one documented divergence: #306's `coi_disclosed` default.
///
/// The case has no `coi_disclosed` key at all, so Python's dataclass default
/// `True` asserts a disclosure was found. The port reports `null`.
fn diverges_as_documented(got: &Value) -> bool {
    got["coi_disclosed"] == Value::Null
}

/// **The three unrecorded enum fields serialise as `null`, never as a member.**
///
/// `None` means *not recorded*; writing the enum's "nothing happened" member would
/// turn an absent field into a claim that a request was made — and a row written
/// before the field existed would be indistinguishable from one whose analysis
/// determined nothing was attempted.
#[test]
fn an_unrecorded_enum_field_is_null_not_a_member() {
    let result = TransparencyResult::new("d", 50, TransparencyRisk::Low);
    let dict = result.to_dict();

    for field in ["unknown_reason", "full_text_status", "trial_results_status"] {
        assert_eq!(dict[field], Value::Null, "{field} must be null");
    }
    // And each serialises to Python's own spelling when it *is* recorded.
    let mut recorded = result.clone();
    recorded.unknown_reason = Some(TransparencyUnknownReason::Unreachable);
    recorded.full_text_status = Some(FullTextStatus::RequestFailed);
    recorded.trial_results_status = Some(TrialResultsStatus::PartlyAnswered);
    let dict = recorded.to_dict();
    assert_eq!(dict["unknown_reason"], json!("unreachable"));
    assert_eq!(dict["full_text_status"], json!("request_failed"));
    assert_eq!(dict["trial_results_status"], json!("partly_answered"));

    // Every risk level and status spelling matches the Python's `.value`.
    assert_eq!(TransparencyRisk::Low.as_str(), "low");
    assert_eq!(TransparencyRisk::Medium.as_str(), "medium");
    assert_eq!(TransparencyRisk::High.as_str(), "high");
    assert_eq!(TransparencyRisk::Unknown.as_str(), "unknown");
    assert_eq!(FullTextStatus::Analyzed.as_str(), "analyzed");
    assert_eq!(FullTextStatus::NotAttempted.as_str(), "not_attempted");
    // `no_identifier` and `disabled` name *why*, so they belong to the reason
    // enum and not to the risk enum — an unknown risk is `unknown` however it
    // came about, and the reason carries the distinction.
    assert_eq!(
        TransparencyUnknownReason::NoIdentifier.as_str(),
        "no_identifier"
    );
    assert_eq!(TransparencyUnknownReason::Disabled.as_str(), "disabled");
    assert_eq!(
        TransparencyUnknownReason::Unreachable.as_str(),
        "unreachable"
    );
}

/// A **round trip is stable**: serialising, reading back and serialising again
/// yields the same object. That is the property a persisted row depends on, and it
/// is why `analyzed_at`'s spelling differing between the two languages is safe —
/// each parses the other's.
#[test]
fn a_round_trip_is_stable() {
    let mut result = TransparencyResult::new("doc", 42, TransparencyRisk::High);
    result.coi_disclosed = Some(false);
    result.risk_indicators = vec!["one".to_string(), "two".to_string()];
    result.tier_downgrade_applied = 3;
    result.industry_funding_detected = true;
    result.industry_funding_confidence = 0.75;
    result.data_availability_level = "restricted".to_string();

    let first = result.to_dict();
    let again = TransparencyResult::from_dict(&first)
        .expect("reads back")
        .to_dict();
    assert_eq!(first, again, "the round trip must be stable");
    // And the field values survived.
    assert_eq!(again["coi_disclosed"], json!(false));
    assert_eq!(again["risk_indicators"], json!(["one", "two"]));
    assert_eq!(again["tier_downgrade_applied"], json!(3));
    assert_eq!(again["industry_funding_confidence"], json!(0.75));
}

/// **A datetime round-trips through both languages' spellings.** Python writes
/// `...+00:00`, this writes `...Z`, and each parses the other — so a row written by
/// either is readable by both. The *string* differs, which is recorded rather than
/// hidden.
#[test]
fn either_datetime_spelling_reads_back_to_the_same_instant() {
    // **Built from the pinned instant**, so the `Z` form under test is the
    // corpus's instant and not a `Utc::now()` that races the assertion.
    let mut data = TransparencyResult::from_dict(&json!({
        "document_id": "d",
        "transparency_score": 10,
        "risk_level": "low",
        "analyzed_at": FIXED,
    }))
    .expect("reads")
    .to_dict();
    let written = data["analyzed_at"].as_str().expect("a string").to_string();
    assert!(
        written.ends_with('Z') || written.ends_with("+00:00"),
        "an ISO-8601 instant: {written}"
    );

    // Accept the other language's spelling of the same instant.
    data["analyzed_at"] = json!(FIXED);
    let from_python = TransparencyResult::from_dict(&data).expect("reads Python's form");
    data["analyzed_at"] = json!(written);
    let from_rust = TransparencyResult::from_dict(&data).expect("reads its own form");
    assert_eq!(
        from_python.analyzed_at, from_rust.analyzed_at,
        "both spellings name one instant"
    );
}

/// A row with **no** optional fields reads back with the Python's defaults —
/// except `coi_disclosed`, which is #306's correction.
#[test]
fn an_absent_field_takes_the_python_default() {
    let data = json!({
        "document_id": "d",
        "transparency_score": 10,
        "risk_level": "low",
    });
    let result = TransparencyResult::from_dict(&data).expect("reads a minimal row");
    assert_eq!(result.document_id, "d");
    assert_eq!(result.data_availability_level, "unknown");
    assert_eq!(result.analyzer_version, "1.0");
    assert!(!result.trial_registered);
    assert!(!result.full_text_analyzed);
    assert!(result.risk_indicators.is_empty());
    assert_eq!(result.tier_downgrade_applied, 0);
    assert_eq!(result.industry_funding_confidence, 0.0);
    // **The correction**: Python's dataclass default is `True`, which asserts a
    // disclosure. `None` says nothing was recorded.
    assert_eq!(result.coi_disclosed, None);
    // An absent timestamp is *now*, since the Python does the same.
    assert!(result.analyzed_at <= chrono::Utc::now());
}

/// **An absent `analyzed_at` is *now***, which the Python does too — so the two
/// cannot be value-diffed and the rule is asserted here instead: a row read back
/// with no timestamp is stamped at read time, and it is a real instant rather
/// than the epoch or `None`.
#[test]
fn an_absent_timestamp_is_now() {
    let before = chrono::Utc::now();
    let result = TransparencyResult::from_dict(&json!({
        "document_id": "d", "transparency_score": 1, "risk_level": "low",
    }))
    .expect("reads");
    let after = chrono::Utc::now();
    assert!(
        result.analyzed_at >= before && result.analyzed_at <= after,
        "stamped at read time, got {}",
        result.analyzed_at
    );
}

/// A malformed row is an error that names the field, where the Python raises.
#[test]
fn a_malformed_row_names_the_field() {
    let base = json!({
        "document_id": "d", "transparency_score": 10, "risk_level": "low",
    });

    let missing_id = json!({"transparency_score": 10, "risk_level": "low"});
    assert!(TransparencyResult::from_dict(&missing_id)
        .expect_err("refuses")
        .contains("document_id"));

    let mut wrong_score = base.clone();
    wrong_score["transparency_score"] = json!("ten");
    assert!(TransparencyResult::from_dict(&wrong_score)
        .expect_err("refuses")
        .contains("transparency_score"));

    let mut bad_enum = base.clone();
    bad_enum["risk_level"] = json!("catastrophic");
    assert!(TransparencyResult::from_dict(&bad_enum)
        .expect_err("refuses")
        .contains("risk_level"));

    // A wrong-typed list is refused rather than silently dropped.
    let mut bad_list = base.clone();
    bad_list["risk_indicators"] = json!("not a list");
    assert!(TransparencyResult::from_dict(&bad_list)
        .expect_err("refuses")
        .contains("risk_indicators"));

    // And a valid row still reads.
    assert!(TransparencyResult::from_dict(&base).is_ok());
}
