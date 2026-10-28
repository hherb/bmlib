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

//! The shared chunker contract, `tests/fixtures/canonical_chunks.json`.
//!
//! The same file Python's `tests/test_canonical_chunks_contract.py` asserts
//! against: each case's canonical text, chunked with its sizes, gives exactly
//! the expected chunks; the chunker's version and defaults are the fixture's;
//! and each of its `refusals` (sizes) is refused by both languages alike.

use bmlib::fulltext::canonical::{
    chunk_canonical, CanonicalText, ChunkError, CHUNKER_VERSION, DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/canonical_chunks.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn size(value: &Value) -> usize {
    usize::try_from(value.as_u64().expect("a size")).expect("fits")
}

#[test]
fn every_case_gives_the_expected_chunks() {
    let mut failures = Vec::new();
    for case in fixture()["cases"].as_array().expect("a list of cases") {
        let canonical = CanonicalText::from_value(case["canonical"].clone())
            .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        let chunks = chunk_canonical(
            &canonical,
            size(&case["max_chars"]),
            size(&case["overlap_chars"]),
        )
        .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        let actual = serde_json::to_value(chunks).expect("serialisable");
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
fn the_version_and_defaults_are_the_contracts() {
    let fixture = fixture();
    assert_eq!(u64::from(CHUNKER_VERSION), fixture["chunker_version"]);
    assert_eq!(DEFAULT_MAX_CHARS, size(&fixture["default_max_chars"]));
    assert_eq!(
        DEFAULT_OVERLAP_CHARS,
        size(&fixture["default_overlap_chars"])
    );
}

#[test]
fn every_bad_size_is_refused() {
    let fixture = fixture();
    let canonical = CanonicalText::from_value(fixture["cases"][0]["canonical"].clone())
        .expect("the first case's text is valid");
    for refusal in fixture["refusals"].as_array().expect("a list of refusals") {
        let result = chunk_canonical(
            &canonical,
            size(&refusal["max_chars"]),
            size(&refusal["overlap_chars"]),
        );
        assert!(
            matches!(result, Err(ChunkError::Sizes { .. })),
            "{} accepted",
            refusal["name"]
        );
    }
}
