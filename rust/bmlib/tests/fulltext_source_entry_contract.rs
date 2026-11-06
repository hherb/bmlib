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

//! The shared `FullTextSourceEntry` JSON contract — the same file Python's
//! `tests/test_fulltext_source_entry_contract.py` asserts against: an entry
//! read from JSON writes back the same object in both languages, and one
//! without `url`, `format` or `source` is refused (#624).

use bmlib::fulltext::FullTextSourceEntry;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/fulltext_source_entry.json");

/// The fixture's list under `key`.
fn cases(key: &str) -> Vec<Value> {
    let contract: Value = serde_json::from_str(FIXTURE).expect("fixture parses");
    contract[key].as_array().expect("a list").clone()
}

/// Reading then writing an entry gives the fixture's `output`.
#[test]
fn an_entry_writes_back_as_the_contract_says() {
    for case in cases("cases") {
        let entry: FullTextSourceEntry = serde_json::from_value(case["input"].clone())
            .unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
        let written = serde_json::to_value(&entry).expect("serialises");
        assert_eq!(written, case["output"], "{}", case["name"]);
    }
}

/// An entry missing `url`, `format` or `source` does not read.
#[test]
fn an_entry_without_a_required_field_is_refused() {
    for case in cases("refused") {
        assert!(
            serde_json::from_value::<FullTextSourceEntry>(case["input"].clone()).is_err(),
            "{}",
            case["name"]
        );
    }
}
