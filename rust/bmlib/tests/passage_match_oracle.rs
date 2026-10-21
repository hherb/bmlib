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

//! The differential oracle for `citations::passage_match`: Rust versus Python.
//!
//! `tests/data/passage_match_cases.json` is the single copy of the cases, read
//! by `rust/oracle/dump_passage_match.py` (which writes
//! `tests/data/passage_match_expected.json` from the Python library) and by this
//! test, which runs each case through the port and asserts the two
//! `serde_json::Value`s are equal. `similarity` therefore compares as `f64` with
//! `==`. Regenerate from the repository root:
//!
//! ```text
//! .venv/bin/python rust/oracle/dump_passage_match.py \
//!     < rust/bmlib/tests/data/passage_match_cases.json \
//!     > rust/bmlib/tests/data/passage_match_expected.json
//! ```

use bmlib::citations::{
    find_passage, fits_text, max_edit_points, normalize_with_offsets, occurs_once,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/passage_match_cases.json");
const EXPECTED: &str = include_str!("data/passage_match_expected.json");

/// Run one case and return its result as JSON.
fn run(case: &Value) -> Value {
    let name = case["name"].as_str().unwrap_or_default();
    let args = &case["args"];
    let text = args["text"].as_str().unwrap_or_default();
    let size = |key: &str| args[key].as_u64().expect("size argument") as usize;
    let similarity = args["min_similarity"].as_f64().unwrap_or_default();

    match case["fn"].as_str().unwrap_or_default() {
        "find_passage" => {
            let passage = args["passage"].as_str().unwrap_or_default();
            let m = find_passage(passage, text, similarity)
                .unwrap_or_else(|e| panic!("case {name:?}: {e:?}"));
            let (start, end, span_text) = match &m.span {
                Some(s) => (json!(s.start), json!(s.end), json!(s.text)),
                None => (Value::Null, Value::Null, Value::Null),
            };
            json!({
                "similarity": m.similarity,
                "start": start,
                "end": end,
                "text": span_text,
                "exact": m.exact,
            })
        }
        "normalize_with_offsets" => {
            let (norm, offsets) = normalize_with_offsets(text);
            json!([norm, offsets])
        }
        "occurs_once" => json!(occurs_once(
            args["fragment"].as_str().unwrap_or_default(),
            text
        )),
        "fits_text" => json!(fits_text(
            size("passage_length"),
            size("text_length"),
            similarity
        )),
        "max_edit_points" => json!(max_edit_points(size("passage_length"), similarity)),
        other => panic!("case {name:?}: unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(
        cases.len(),
        expected.len(),
        "the case file and the expected file disagree on how many cases there are — \
         regenerate the expectations"
    );

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(
            name,
            want["name"].as_str().unwrap_or_default(),
            "case/expected name mismatch — the two files are out of step"
        );
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "case {name:?} errored in Python: {}",
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
        "{} of {} cases diverge from Python:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}
