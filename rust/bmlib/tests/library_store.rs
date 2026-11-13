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

//! The request's identifiers as library identifiers, from
//! `tests/fixtures/fulltext_library_ids.json` — the file Python's
//! `tests/test_fulltext_library_store.py` asserts against.

use bmlib::fulltext::library_ids;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/fulltext_library_ids.json");

#[test]
fn library_ids_match_the_fixture() {
    let root: Value = serde_json::from_str(FIXTURE).expect("fixture is JSON");
    for case in root["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().unwrap();
        let (ids, dropped) = library_ids(
            case["pmid"].as_str().unwrap(),
            case["doi"].as_str(),
            case["pmc_id"].as_str(),
        );
        let got = serde_json::json!({"pmid": ids.pmid, "doi": ids.doi, "pmcid": ids.pmcid});
        assert_eq!(got, case["ids"], "{name}");
        let want: Vec<&str> = case["dropped"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(dropped, want, "{name}");
    }
}
