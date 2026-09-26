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

//! The differential oracle: Rust versus Python, over the rule-based quality
//! extractors.
//!
//! Same instrument as the other oracles, fourth corpus, using the
//! `corrected` mechanism introduced for the JSON corpus.
//!
//! # The corrected-expectation mechanism
//!
//! The port targets a **corrected** bmlib, so on the defects it fixes the
//! oracle *must* disagree with Python. **Thirteen** cases here carry a
//! `corrected` block, covering issues #294 (a digit-grouped sample size),
//! #297 (negation-blind power/CI bonuses) and #298 (priority over evidence).
//!
//! A corpus that simply pinned the corrected output would hide the fact that
//! Python says something else — and the next porter would have no way to tell
//! an intentional fix from a mistake. So a case carries an optional
//! `corrected` object: the value the port is *required* to produce, plus the
//! reason and the issue number. The test then asserts three things:
//!
//! 1. Python's answer is what the corpus says it is (so the correction is
//!    still describing real Python behaviour, not a stale note);
//! 2. Rust produces the `corrected` value;
//! 3. the two genuinely differ (so a correction that Python has since adopted
//!    is reported rather than silently passing).
//!
//! Every case without a `corrected` object is diffed strictly.
//!
//! Regenerating (from the repository root):
//!
//! ```text
//! .venv/bin/python rust/oracle/dump_quality.py < rust/oracle/quality_cases.json \
//!     > rust/bmlib/tests/data/quality_expected.json
//! ```

use std::collections::BTreeMap;

use bmlib::quality::extractors::{
    calculate_sample_size_score, extract_sample_size_dimension, extract_study_type,
    find_sample_size, get_extracted_sample_size, get_extracted_study_type, has_ci_reporting,
    has_power_calculation, prepare_extractor_search_text,
};
use bmlib::quality::scoring_models::DimensionScore;
use serde_json::{json, Value};

const CASES: &str = include_str!("data/quality_cases.json");
const EXPECTED: &str = include_str!("data/quality_expected.json");

/// The document argument, as the extractors take it.
fn document(spec: &Value) -> BTreeMap<String, String> {
    spec.as_object()
        .map(|o| {
            o.iter()
                .map(|(k, v)| (k.clone(), v.as_str().unwrap_or_default().to_string()))
                .collect()
        })
        .unwrap_or_default()
}

/// A `DimensionScore`'s details, as the corpus writes them.
fn details_json(d: &DimensionScore) -> Value {
    json!(d
        .details
        .iter()
        .map(|x| json!({
            "dimension": x.dimension,
            "component": x.component,
            "extracted_value": x.extracted_value,
            "score_contribution": x.score_contribution,
            "evidence_text": x.evidence_text,
            "reasoning": x.reasoning,
        }))
        .collect::<Vec<_>>())
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    let doc = document(&args["document"]);

    match fn_name {
        "find_sample_size" => json!(find_sample_size(
            args["text"].as_str().unwrap_or_default(),
            args.get("min_n").and_then(Value::as_i64).unwrap_or(5),
            args.get("max_n")
                .and_then(Value::as_i64)
                .unwrap_or(1_000_000)
        )),
        "calculate_sample_size_score" => json!(calculate_sample_size_score(
            args["n"].as_i64().unwrap_or(0),
            args.get("log_multiplier")
                .and_then(Value::as_f64)
                .unwrap_or(2.0)
        )),
        "has_power_calculation" => json!(has_power_calculation(
            args["text"].as_str().unwrap_or_default()
        )),
        "has_ci_reporting" => json!(has_ci_reporting(args["text"].as_str().unwrap_or_default())),
        "prepare_search_text" => json!(prepare_extractor_search_text(&doc)),
        "extract_study_type" => {
            let d = extract_study_type(&doc);
            json!({
                "score": d.score,
                "type": get_extracted_study_type(&d),
                "details": details_json(&d),
            })
        }
        "extract_sample_size_dimension" => {
            let d = extract_sample_size_dimension(&doc);
            json!({
                "score": d.score,
                "n": get_extracted_sample_size(&d),
                "details": details_json(&d),
            })
        }
        "dimension_score_roundtrip" => {
            let d: DimensionScore =
                serde_json::from_value(args["data"].clone()).expect("dimension score deserialises");
            json!({
                "dimension_name": d.dimension_name,
                "score": d.score,
                "details": details_json(&d),
            })
        }
        other => panic!("unknown fn {other:?}"),
    }
}

/// The part of an error message that bmlib writes, before any parser detail.
fn error_prefix(v: &Value) -> String {
    let message = v.get("error").and_then(Value::as_str).unwrap_or_default();
    match message.split_once(':') {
        Some((head, _)) => head.to_string(),
        None => message.to_string(),
    }
}

#[test]
fn the_port_agrees_with_python_except_where_it_fixes_a_defect() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    let mut corrected_seen: Vec<String> = Vec::new();

    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        let python_value = &want["value"];
        let got = run(case);

        match case.get("corrected") {
            None => {
                // A case that errored in Python cannot be a strict
                // expectation, and is not marked corrected either — that
                // would be a corpus bug.
                assert!(
                    want["ok"].as_bool().unwrap_or(false),
                    "case {name:?} errored in Python ({}) and carries no `corrected` block",
                    want["error"]
                );
                // Two levels of `ok` are in play and conflating them is the
                // bug this comment exists to prevent. The **outer** one is the
                // oracle harness's: did running the Python function complete?
                // For a case whose *point* is that the function raised, the
                // harness succeeded, so the outer `ok` is true. The **inner**
                // one is the function's own outcome. A case is therefore
                // "expected to fail" iff the inner `ok` is false — and that is
                // what decides how strictly to compare.
                let python_failed = python_value.get("ok") == Some(&Value::Bool(false))
                    || !want["ok"].as_bool().unwrap_or(false);

                let agrees = if !python_failed {
                    got == *python_value
                } else {
                    // Compared on outcome, error *kind*, and the *prefix* of
                    // the message — not on the parser's wording. `serde_json`
                    // and Python's `json` describe a syntax error differently
                    // ("expected ident" vs "Expecting value"); that is a
                    // difference between two JSON parsers, not two bmlibs. The
                    // prefix is the part bmlib writes.
                    got.get("ok") == python_value.get("ok")
                        && got.get("type") == python_value.get("type")
                        && error_prefix(&got) == error_prefix(python_value)
                };
                if !agrees {
                    failures.push(format!(
                        "  {name}\n    python: {}\n    rust:   {}",
                        serde_json::to_string(python_value).unwrap_or_default(),
                        serde_json::to_string(&got).unwrap_or_default()
                    ));
                }
            }
            Some(expected_corrected) => {
                corrected_seen.push(name.to_string());
                // 1. Python's answer is still what the corpus recorded.
                //    Compared against the *value*, since the corrected block
                //    also carries `why` and `issue` annotations.
                if want["ok"].as_bool().unwrap_or(false) {
                    let corrected_payload = expected_corrected
                        .get("value")
                        .cloned()
                        .unwrap_or(Value::Null);
                    if python_value == &corrected_payload {
                        failures.push(format!(
                            "  {name}: marked corrected but Python already returns the \
                             corrected value — the correction is stale, remove it"
                        ));
                    }
                } else if !expected_corrected["ok"].as_bool().unwrap_or(false) {
                    failures.push(format!("  {name}: a corrected case must be `ok`"));
                }
                // 2. Rust produces the corrected value. A `corrected` block is
                //    a full oracle result (`ok` + `value`) plus the `why` and
                //    `issue` annotations, but `got` is the bare value the
                //    function returned — so it is compared against
                //    `corrected["value"]`, not against the block. Comparing
                //    the block is what made every corrected case fail with a
                //    diff that looked identical.
                let payload = expected_corrected
                    .get("value")
                    .cloned()
                    .unwrap_or(Value::Null);
                if got != payload {
                    failures.push(format!(
                        "  {name} (corrected, issue #{})\n    want: {}\n    rust: {}",
                        expected_corrected["issue"],
                        serde_json::to_string(&payload).unwrap_or_default(),
                        serde_json::to_string(&got).unwrap_or_default()
                    ));
                }
            }
        }
    }

    assert!(
        !corrected_seen.is_empty(),
        "no corrected cases were exercised — the #294/#297/#298 fixes are unpinned"
    );
    assert!(
        failures.is_empty(),
        "{} divergence(s) over {} cases:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// The corrected cases must be the #299 ones, and there must be four of them.
///
/// Stated separately because the mechanism above would still pass if a
/// `corrected` block were attached to an unrelated case.
#[test]
fn the_corrected_cases_are_exactly_the_three_extractor_defects() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let mut marked: Vec<(String, u64)> = Vec::new();
    for case in cases.as_array().expect("list") {
        if let Some(c) = case.get("corrected") {
            marked.push((
                case["name"].as_str().unwrap_or_default().to_string(),
                c["issue"].as_u64().unwrap_or(0),
            ));
        }
    }
    assert_eq!(
        marked.len(),
        13,
        "expected thirteen corrected cases: {marked:?}"
    );
    let mut issues: Vec<u64> = marked.iter().map(|(_, i)| *i).collect();
    issues.sort_unstable();
    issues.dedup();
    assert_eq!(
        issues,
        vec![294, 297, 298],
        "the corrected cases must cover exactly the three extractor defects"
    );
    for (name, _) in &marked {
        // Names are `294/...`, `297/...`, `298/...`.
        let prefix = name.split('/').next().unwrap_or_default();
        assert!(
            prefix.parse::<u64>().is_ok(),
            "{name} is not named for its issue, so a corrected case could be \
             attached to an unrelated input unnoticed"
        );
    }
}
