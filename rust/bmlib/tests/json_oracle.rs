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

//! The differential oracle: Rust versus Python, over JSON repair and span
//! location.
//!
//! Same instrument as the other two oracles, third corpus — with one addition
//! this one needs and they did not.
//!
//! # The corrected-expectation mechanism
//!
//! The port targets a **corrected** bmlib, so on the defects it fixes the
//! oracle *must* disagree with Python. Three of those live here, all #299:
//! Python appends every `]` then every `}`, so `[{"a": 1}, {"b": 2` cannot be
//! repaired and the caller silently receives `{"a": 1}`.
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
//! .venv/bin/python rust/oracle/dump_json.py < rust/oracle/json_cases.json \
//!     > rust/bmlib/tests/data/json_expected.json
//! ```

use bmlib::llm::json_repair::{
    extract_and_repair_json, repair_json_default, safe_json_loads, salvage_json_fields,
};
use bmlib::llm::utils::{extract_json, iter_json_spans};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/json_cases.json");
const EXPECTED: &str = include_str!("data/json_expected.json");

fn repair_case(text: &str) -> Value {
    match repair_json_default(text) {
        Ok(v) => json!({"ok": true, "value": v}),
        Err(e) => json!({
            "ok": false,
            "error": e.to_string(),
            "type": if e.to_string().starts_with("Cannot repair empty") { "ValueError" } else { "JSONRepairError" }
        }),
    }
}

fn run(case: &Value) -> Value {
    let name = case["name"].as_str().unwrap_or_default();
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    let text = args["text"].as_str().unwrap_or_default();

    match fn_name {
        "repair_json" => repair_case(text),
        "safe_json_loads" => {
            let repair = args.get("repair").and_then(Value::as_bool).unwrap_or(true);
            match safe_json_loads(text, repair, 3) {
                Ok(v) => json!({"ok": true, "value": v}),
                Err(e) => json!({"ok": false, "error": e.to_string(), "type": "ValueError"}),
            }
        }
        "extract_and_repair_json" => {
            let repair = args.get("repair").and_then(Value::as_bool).unwrap_or(true);
            match extract_and_repair_json(text, repair) {
                Ok((t, repaired)) => json!({"ok": true, "text": t, "repaired": repaired}),
                Err(e) => json!({"ok": false, "error": e.to_string(), "type": "ValueError"}),
            }
        }
        "extract_json" => json!(extract_json(
            text,
            args.get("allow_fragments")
                .and_then(Value::as_bool)
                .unwrap_or(true)
        )),
        "iter_json_spans" => json!(iter_json_spans(
            text,
            args.get("nested_objects")
                .and_then(Value::as_bool)
                .unwrap_or(true)
        )),
        "salvage_json_fields" => {
            let keys: Vec<String> = args["keys"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            let recovered = salvage_json_fields(text, &keys);
            Value::Object(recovered.into_iter().collect())
        }
        other => panic!("case {name:?}: unknown fn {other:?}"),
    }
}

/// The part of an error message that bmlib writes, before any parser detail.
///
/// bmlib's messages are "<bmlib's claim>: <decoder's words>"; only the claim is
/// ours to match.
fn error_prefix(v: &Value) -> String {
    let message = v.get("error").and_then(Value::as_str).unwrap_or_default();
    match message.split_once(':') {
        Some((head, _)) => head.to_string(),
        None => message.to_string(),
    }
}

/// The corrected payload, with the annotation keys removed.
fn strip_annotation(corrected: &Value) -> Value {
    let mut obj = corrected.as_object().cloned().unwrap_or_default();
    obj.remove("why");
    obj.remove("issue");
    Value::Object(obj)
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
                if want["ok"].as_bool().unwrap_or(false) {
                    if python_value == expected_corrected {
                        failures.push(format!(
                            "  {name}: marked corrected but Python already returns the \
                             corrected value — the correction is stale, remove it"
                        ));
                    }
                } else if !expected_corrected["ok"].as_bool().unwrap_or(false) {
                    failures.push(format!("  {name}: a corrected case must be `ok`"));
                }
                // 2. Rust produces the corrected value. The annotation keys
                //    (`why`, `issue`) are documentation, not payload, so they
                //    are stripped before comparing.
                let payload = strip_annotation(expected_corrected);
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
        "no corrected cases were exercised — #299's fix is unpinned"
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
fn the_corrected_cases_are_exactly_the_closer_order_defect() {
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
    assert_eq!(marked.len(), 4, "expected four corrected cases: {marked:?}");
    for (name, issue) in &marked {
        assert_eq!(*issue, 299, "{name} cites issue {issue}, not #299");
    }
}
