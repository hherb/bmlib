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

//! The PRISMA 2020 checklist table, against the shared fixture
//! `tests/fixtures/prisma2020_checklist.json`.
//!
//! Python's `tests/test_prisma2020_checklist.py` reads the same file, so the
//! two tables cannot drift apart.

use std::collections::BTreeSet;

use bmlib::quality::prisma2020_checklist::{item, ChecklistItem, Section, CHECKLIST, KEY_ITEMS};
use serde_json::{json, Value};

const FIXTURE: &str = include_str!("../../../tests/fixtures/prisma2020_checklist.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

/// A row as the fixture spells it.
fn row_json(entry: &ChecklistItem) -> Value {
    json!({
        "id": entry.id,
        "section": entry.section.as_str(),
        "name": entry.name,
        "wording": entry.wording,
        "guidance": entry.guidance,
        "condense_hint": entry.condense_hint,
        "key": entry.key,
        "may_be_not_applicable": entry.may_be_not_applicable,
        "reply_chars": entry.reply_chars,
    })
}

#[test]
fn checklist_matches_fixture() {
    let fixture = fixture();
    let rows = fixture["items"].as_array().expect("items is an array");

    assert_eq!(rows.len(), CHECKLIST.len());
    for (row, entry) in rows.iter().zip(CHECKLIST.iter()) {
        assert_eq!(*row, row_json(entry), "row {}", entry.id);
    }
}

#[test]
fn sections_are_their_wire_strings() {
    let all = [
        (Section::Title, "title"),
        (Section::Abstract, "abstract"),
        (Section::Introduction, "introduction"),
        (Section::Methods, "methods"),
        (Section::Results, "results"),
        (Section::Discussion, "discussion"),
        (Section::OtherInformation, "other_information"),
    ];
    for (section, wire) in all {
        assert_eq!(section.as_str(), wire);
    }
}

#[test]
fn ids_are_unique_and_ordered() {
    let ids: Vec<&str> = CHECKLIST.iter().map(|entry| entry.id).collect();
    let unique: BTreeSet<&str> = ids.iter().copied().collect();
    assert_eq!(unique.len(), ids.len());

    let order = |id: &str| {
        let number = id.trim_end_matches(|c: char| c.is_ascii_lowercase());
        (
            number.parse::<u32>().expect("a numbered id"),
            id[number.len()..].to_owned(),
        )
    };
    let keys: Vec<_> = ids.iter().map(|id| order(id)).collect();
    let mut sorted = keys.clone();
    sorted.sort();
    assert_eq!(keys, sorted);

    let sections: Vec<Section> = CHECKLIST.iter().map(|entry| entry.section).collect();
    let mut by_section = sections.clone();
    by_section.sort_by_key(|section| *section as u8);
    assert_eq!(sections, by_section);
}

#[test]
fn key_items_equal_key_rows() {
    let key_rows: BTreeSet<&str> = CHECKLIST
        .iter()
        .filter(|entry| entry.key)
        .map(|entry| entry.id)
        .collect();
    let listed: BTreeSet<&str> = KEY_ITEMS.iter().copied().collect();
    assert_eq!(listed, key_rows);
    assert_eq!(listed.len(), KEY_ITEMS.len());

    let fixture = fixture();
    assert_eq!(json!(KEY_ITEMS), fixture["key_items"]);
}

#[test]
fn forty_two_rows() {
    assert_eq!(CHECKLIST.len(), 42);
}

#[test]
fn item_finds_a_row_by_id() {
    let found = item("13d").expect("13d is a row");
    assert_eq!(found.id, "13d");
    assert_eq!(found.section, Section::Methods);
    assert!(item("13g").is_none());
    assert!(item("").is_none());
}
