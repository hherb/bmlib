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

//! The shared canonical-text contract, `tests/fixtures/canonical_text.json`.
//!
//! The same file Python's `tests/test_canonical_text_contract.py` asserts
//! against: every converter turns each case's input (for a PDF, the segmenter's
//! `SegmentedDocument` as Python's `to_dict()` writes it) into exactly the expected
//! `CanonicalText`, spans counted in code points, and the expected value
//! round-trips through `CanonicalText::from_value`. Each of its `refusals` (a
//! variant of its `valid` value) is refused by both languages alike.

use bmlib::fulltext::canonical::{
    canonical_from_jats, canonical_from_markdown, canonical_from_plain_text,
    canonical_from_segmented, CanonicalText,
};
use bmlib::fulltext::jats_reader::parse;
use bmlib::fulltext::models::SegmentedDocument;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/canonical_text.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn cases() -> Vec<Value> {
    fixture()["cases"]
        .as_array()
        .expect("a list of cases")
        .clone()
}

fn convert(case: &Value) -> CanonicalText {
    if case["format"] == "pdf" {
        let document: SegmentedDocument = serde_json::from_value(case["input"].clone())
            .expect("the case's input is a SegmentedDocument");
        return canonical_from_segmented(&document);
    }
    let input = case["input"].as_str().expect("an input string");
    match case["format"].as_str().expect("a format") {
        "plain_text" => canonical_from_plain_text(input, case["title"].as_str().unwrap_or("")),
        "markdown" => canonical_from_markdown(input),
        "jats" => canonical_from_jats(&parse(input).expect("the case's JATS parses")),
        other => panic!("unknown format {other:?} in {}", case["name"]),
    }
}

#[test]
fn every_converter_output_is_the_expected_canonical_text() {
    let mut failures = Vec::new();
    for case in cases() {
        let actual = serde_json::to_value(convert(&case)).expect("serialisable");
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
fn every_expected_value_round_trips() {
    for case in cases() {
        let parsed = CanonicalText::from_value(case["expected"].clone())
            .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        assert_eq!(
            serde_json::to_value(parsed).expect("serialisable"),
            case["expected"],
            "{}",
            case["name"]
        );
    }
}

#[test]
fn the_value_the_refusals_vary_is_accepted() {
    let valid = fixture()["valid"].clone();
    let parsed = CanonicalText::from_value(valid.clone()).expect("the valid value is accepted");
    assert_eq!(serde_json::to_value(parsed).expect("serialisable"), valid);
}

#[test]
fn every_malformed_value_is_refused() {
    let fixture = fixture();
    let refusals = fixture["refusals"].as_array().expect("a list of refusals");
    let accepted: Vec<&Value> = refusals
        .iter()
        .filter(|refusal| CanonicalText::from_value(refusal["value"].clone()).is_ok())
        .map(|refusal| &refusal["name"])
        .collect();
    assert!(accepted.is_empty(), "accepted: {accepted:?}");
}
