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

//! `is_industry_funder` against the hand-labelled funder corpus.
//!
//! The corpus is the repository's existing `tests/data/funder_names.json` — 417
//! names sampled live from CrossRef `funder[].name` and PubMed `<Grant><Agency>`
//! and labelled by a person for issue #36. It is **not** a corpus written for this
//! test, which is what makes it worth diffing: the port's own fixtures encode the
//! port author's idea of what a funder name looks like, and cannot catch a matcher
//! that agrees only with them.

use bmlib::transparency::analyzer::is_industry_funder;
use serde_json::Value;

const CASES: &str = include_str!("data/funder_matcher_cases.json");
const EXPECTED: &str = include_str!("data/funder_matcher_expected.json");

#[test]
fn the_port_agrees_with_python_on_every_labelled_funder() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["args"]["name"].as_str().unwrap_or_default();
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let py = want["python"].as_bool().unwrap_or(false);
        let rust = is_industry_funder(name);
        if py != rust {
            failures.push(format!(
                "  python={py} rust={rust} label={} name={name:?}",
                want["label"].as_str().unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} funder names diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}
