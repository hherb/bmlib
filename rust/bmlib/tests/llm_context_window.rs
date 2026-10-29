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

//! The shared context-window contract, `tests/fixtures/context_window.json`
//! (the same file Python's `tests/test_context_window.py` reads).

use bmlib::llm::{known_context_window, KNOWN_CONTEXT_WINDOWS};
use serde_json::Value;
use std::collections::HashSet;

const FIXTURE: &str = include_str!("../../../tests/fixtures/context_window.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("fixture is JSON")
}

#[test]
fn table_matches_the_fixture() {
    let fx = fixture();
    let rows = fx["table"].as_array().unwrap();
    let from_fixture: HashSet<(String, String, u64)> = rows
        .iter()
        .map(|r| {
            (
                r["provider"].as_str().unwrap().to_string(),
                r["model"].as_str().unwrap().to_string(),
                r["window"].as_u64().unwrap(),
            )
        })
        .collect();
    assert_eq!(from_fixture.len(), rows.len(), "duplicate fixture rows");
    let from_code: HashSet<(String, String, u64)> = KNOWN_CONTEXT_WINDOWS
        .iter()
        .map(|(p, m, w)| (p.to_string(), m.to_string(), *w))
        .collect();
    assert_eq!(
        KNOWN_CONTEXT_WINDOWS.len(),
        from_code.len(),
        "duplicate code rows"
    );
    assert_eq!(from_fixture, from_code);
}

#[test]
fn table_lookups() {
    let fx = fixture();
    for case in fx["table_lookups"].as_array().unwrap() {
        let expected = case["expect"]["window"].as_u64();
        let got = known_context_window(
            case["provider"].as_str().unwrap(),
            case["model"].as_str().unwrap(),
        );
        assert_eq!(got, expected, "{}", case["name"]);
    }
}
