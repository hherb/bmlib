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

//! Splitting the PRISMA 2020 checklist to a reply budget, against the shared
//! fixture `tests/fixtures/prisma2020_parts.json`.
//!
//! Python's `tests/test_prisma2020_parts.py` reads the same file.

use bmlib::quality::prisma2020_checklist::{item, ChecklistItem, CHECKLIST};
use bmlib::quality::prisma2020_parts::{
    part_query, plan_parts, PartPlan, PartsError, PART_OVERHEAD_CHARS,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/prisma2020_parts.json");

fn rows(ids: &[&str]) -> Vec<&'static ChecklistItem> {
    ids.iter()
        .map(|id| item(id).unwrap_or_else(|| panic!("no item {id}")))
        .collect()
}

fn all() -> Vec<&'static ChecklistItem> {
    CHECKLIST.iter().collect()
}

#[test]
fn fixture_cases() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture is JSON");
    for case in fixture["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let ids: Vec<&str> = case["item_ids"]
            .as_array()
            .expect("item_ids")
            .iter()
            .map(|v| v.as_str().expect("id"))
            .collect();
        let budget = case["reply_budget_chars"].as_u64().expect("budget") as usize;
        let result = plan_parts(&rows(&ids), budget);
        let expected = &case["expected"];
        if let Some(kind) = expected.get("error") {
            let err = result.expect_err(name);
            assert_eq!(err.kind(), kind.as_str().unwrap(), "{name}");
            let PartsError::ItemTooLarge { id, .. } = err;
            assert_eq!(id, expected["id"].as_str().unwrap(), "{name}");
        } else {
            let parts = result.expect(name);
            let got: Vec<Vec<&str>> = parts.iter().map(|p| p.item_ids.clone()).collect();
            let want: Vec<Vec<&str>> = expected
                .as_array()
                .expect("expected parts")
                .iter()
                .map(|p| {
                    p.as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_str().unwrap())
                        .collect()
                })
                .collect();
            assert_eq!(got, want, "{name}");
        }
    }
}

#[test]
fn large_budget_is_one_part() {
    let parts = plan_parts(&all(), 1_000_000_000).unwrap();
    assert_eq!(parts.len(), 1);
    assert_eq!(parts[0].item_ids.len(), 42);
}

#[test]
fn budget_below_one_item_is_refused() {
    let err = plan_parts(&all(), PART_OVERHEAD_CHARS + 899).unwrap_err();
    assert_eq!(err.kind(), "item_too_large");
    assert_eq!(
        err,
        PartsError::ItemTooLarge {
            id: "1",
            needed: 1300,
            budget: 1299
        }
    );
}

#[test]
fn empty_items_give_no_parts() {
    assert!(plan_parts(&[], 16_000).unwrap().is_empty());
}

#[test]
fn order_is_checklist_order() {
    let parts = plan_parts(&all(), 16_000).unwrap();
    let flat: Vec<&str> = parts.iter().flat_map(|p| p.item_ids.clone()).collect();
    let want: Vec<&str> = CHECKLIST.iter().map(|i| i.id).collect();
    assert_eq!(flat, want);
}

#[test]
fn label_spans_sections() {
    let part = |ids: &[&'static str]| PartPlan {
        item_ids: ids.to_vec(),
    };
    assert_eq!(part(&["5", "6", "13c"]).label(), "Methods 5–13c");
    assert_eq!(part(&["13d", "17"]).label(), "Methods 13d – Results 17");
    assert_eq!(part(&["13d"]).label(), "Methods 13d");
    assert_eq!(
        part(&["23d", "24a"]).label(),
        "Discussion 23d – Other information 24a"
    );
}

#[test]
fn part_query_names_every_hint() {
    let items = rows(&["7", "13d"]);
    let want = format!(
        "Everything needed to judge these PRISMA 2020 items: {}; {}",
        items[0].condense_hint, items[1].condense_hint
    );
    assert_eq!(part_query(&items), want);
}
