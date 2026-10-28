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

//! The shared full-text-check contract, `tests/fixtures/full_text_assessment.json`.
//!
//! The same file Python's `tests/test_full_text_assessment_contract.py` asserts
//! against: each case's Markdown, converted and assessed, gives exactly the
//! expected assessment, and the threshold is the fixture's.

use bmlib::fulltext::canonical::{assess_full_text, canonical_from_markdown, MIN_BODY_WORDS};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/full_text_assessment.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

#[test]
fn every_assessment_is_the_expected_one() {
    let fixture = fixture();
    let mut failures = Vec::new();
    for case in fixture["cases"].as_array().expect("a list of cases") {
        let markdown = case["markdown"].as_str().expect("a markdown string");
        let actual = serde_json::to_value(
            assess_full_text(&canonical_from_markdown(markdown)).expect("a valid text"),
        )
        .expect("serialisable");
        if actual != case["expected"] {
            failures.push(format!(
                "{}:\n  expected {}\n  actual   {}",
                case["name"], case["expected"], actual
            ));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn the_threshold_is_the_contracts() {
    assert_eq!(
        fixture()["min_body_words"].as_u64(),
        Some(MIN_BODY_WORDS as u64)
    );
}
