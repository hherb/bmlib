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

//! The shared `find_passage` contract — the same file Python's
//! `tests/test_passage_match_contract.py` asserts against. Spans are
//! code-point (`char`) indices.

use std::collections::HashSet;

use bmlib::citations::passage_match::find_passage;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/passage_match.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn cases() -> Vec<Value> {
    fixture()["cases"].as_array().expect("cases").clone()
}

#[test]
fn contract_case() {
    for case in cases() {
        let name = case["name"].as_str().unwrap();
        let m = find_passage(
            case["passage"].as_str().unwrap(),
            case["text"].as_str().unwrap(),
            case["min_similarity"].as_f64().unwrap(),
        )
        .unwrap_or_else(|e| panic!("{name}: {e}"));
        let expected = &case["expected"];
        assert_eq!(
            m.matched(),
            expected["matched"].as_bool().unwrap(),
            "{name}"
        );
        assert_eq!(m.exact, expected["exact"].as_bool().unwrap(), "{name}");
        let got = (
            m.span.as_ref().map(|s| s.start as u64),
            m.span.as_ref().map(|s| s.end as u64),
            m.span.as_ref().map(|s| s.text.as_str()),
        );
        let want = (
            expected["start"].as_u64(),
            expected["end"].as_u64(),
            expected["text"].as_str(),
        );
        assert_eq!(got, want, "{name}");
        if let Some(similarity) = expected.get("similarity") {
            assert_eq!(m.similarity, similarity.as_f64().unwrap(), "{name}");
        }
        if let Some(floor) = expected.get("min_similarity_at_least") {
            assert!(m.similarity >= floor.as_f64().unwrap(), "{name}");
        }
    }
}

#[test]
fn contract_invalid_threshold() {
    let thresholds = fixture()["invalid_thresholds"].clone();
    for threshold in thresholds.as_array().expect("invalid_thresholds") {
        let threshold = threshold.as_f64().unwrap();
        assert!(find_passage("a", "a", threshold).is_err(), "{threshold}");
    }
}

#[test]
fn every_case_name_is_unique() {
    let cases = cases();
    let names: HashSet<&str> = cases.iter().map(|c| c["name"].as_str().unwrap()).collect();
    assert_eq!(names.len(), cases.len());
}
