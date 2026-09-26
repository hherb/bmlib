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
//! Same instrument as the other two oracles, third corpus.
//!
//! # The corrections that used to live here, and why they are gone
//!
//! The port targets a **corrected** bmlib, so on the defects it fixes the
//! oracle *must* disagree with Python. Four cases here carried a `corrected`
//! object for #299 — Python appended every `]` then every `}`, so
//! `[{"a": 1}, {"b": 2` could not be repaired and the caller silently received
//! `{"a": 1}`; the port closes the openers innermost first.
//!
//! **Python adopted that fix** in `e9db0f9` ("fix(llm, agents): seven defects
//! the Rust-port audit filed"), so the corrections were retired and every case
//! is now diffed strictly. The mechanism — a `corrected` object holding the
//! value the port is required to produce, plus a `why` and an `issue`, with the
//! test asserting Python still says something else — is still used by the
//! corpora whose defects Python has *not* adopted; see `quality_oracle.rs`.
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
        // A case that errored in Python cannot be a strict expectation. The
        // **outer** `ok` is the oracle harness's — did running the Python
        // function complete? — so a case whose *point* is that the function
        // raised still has an outer `ok` of true, and an inner one of false,
        // which is what decides how strictly to compare.
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "case {name:?} errored in Python ({})",
            want["error"]
        );
        let python_value = &want["value"];
        let got = run(case);

        let python_failed = python_value.get("ok") == Some(&Value::Bool(false));

        let agrees = if !python_failed {
            got == *python_value
        } else {
            // Compared on outcome, error *kind*, and the *prefix* of the
            // message — not on the parser's wording. `serde_json` and Python's
            // `json` describe a syntax error differently ("expected ident" vs
            // "Expecting value"); that is a difference between two JSON
            // parsers, not two bmlibs. The prefix is the part bmlib writes.
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

    assert!(
        failures.is_empty(),
        "{} divergence(s) over {} cases:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// The four `#299` cases are still here, and are now strict agreements.
///
/// They used to carry `corrected` blocks: the port closes openers innermost
/// first where Python appended every `]` then every `}`, so on these inputs the
/// oracle was *required* to disagree. **Python adopted the fix** in
/// `e9db0f9` ("fix(llm, agents): seven defects the Rust-port audit filed"), so
/// the corrections were retired and the cases are diffed against Python like
/// every other. Naming them separately keeps the coverage visible: a corpus edit
/// that dropped the interleaved-truncation inputs would otherwise leave this
/// file green while #299's behaviour went unpinned.
#[test]
fn the_interleaved_truncation_cases_are_still_present() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let cases = cases.as_array().expect("list");
    let names: Vec<&str> = cases.iter().filter_map(|c| c["name"].as_str()).collect();
    for name in [
        "repair/truncated-interleaved-299",
        "repair/truncated-interleaved-nested-299",
        "repair/deeply-nested",
        "extract/truncated-interleaved-299",
    ] {
        assert!(names.contains(&name), "case {name:?} is missing: {names:?}");
    }
    // And no case claims a correction any more: Python has adopted them all, so
    // a `corrected` block reappearing here would be a stale note rather than a
    // divergence.
    let marked: Vec<&str> = cases
        .iter()
        .filter(|c| c.get("corrected").is_some())
        .filter_map(|c| c["name"].as_str())
        .collect();
    assert!(marked.is_empty(), "stale corrections: {marked:?}");
}
