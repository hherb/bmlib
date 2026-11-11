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

//! The shared contract for quotes split at their ellipses — the same file
//! Python's `tests/test_quote_pieces_contract.py` asserts against. Spans are
//! code-point (`char`) indices.

use bmlib::citations::passage_match::ELLIPSIS_PIECE_MIN_WORDS;
use bmlib::citations::split_at_ellipses;
use bmlib::quality::verify_quotes;
use serde_json::{json, Value};

const FIXTURE: &str = include_str!("../../../tests/fixtures/quote_pieces.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

#[test]
fn min_piece_words_matches_the_contract() {
    assert_eq!(
        fixture()["min_piece_words"].as_u64(),
        Some(ELLIPSIS_PIECE_MIN_WORDS as u64)
    );
}

#[test]
fn split_cases() {
    for case in fixture()["split_cases"].as_array().expect("split_cases") {
        let name = case["name"].as_str().unwrap();
        let want: Vec<String> = serde_json::from_value(case["pieces"].clone()).unwrap();
        assert_eq!(
            split_at_ellipses(case["quote"].as_str().unwrap()),
            want,
            "{name}"
        );
    }
}

#[test]
fn verify_cases() {
    for case in fixture()["verify_cases"].as_array().expect("verify_cases") {
        let name = case["name"].as_str().unwrap();
        let (citations, not_found, contradicted, dropped) = verify_quotes(
            Some(&case["quotes"]),
            case["text"].as_str().unwrap(),
            case["min_similarity"].as_f64().unwrap(),
        )
        .unwrap_or_else(|e| panic!("{name}: {e}"));
        let got = json!({
            "citations": citations
                .iter()
                .map(|c| json!({"start": c.start, "end": c.end, "text": c.text}))
                .collect::<Vec<_>>(),
            "not_found": not_found,
            "contradicted": contradicted
                .iter()
                .map(|c| json!([c.quote, c.contradiction.as_str()]))
                .collect::<Vec<_>>(),
            "dropped": dropped,
        });
        assert_eq!(got, case["expected"], "{name}");
    }
}
