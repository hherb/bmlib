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

//! Reading a PRISMA 2020 part's reply under the strict-evidence rule: the
//! logging check lives here because `common::log_capture` is reachable only
//! from integration tests. Python's `tests/test_prisma2020_models.py` is the
//! twin.

mod common;

use bmlib::quality::prisma2020_models::{
    read_part_reply, read_suitability, shown_judgement, Judgement,
};
use serde_json::{json, Value};

const TEXT: &str = "Methods. We searched MEDLINE and Embase from inception to March 2020.";
const QUOTE: &str = "We searched MEDLINE and Embase from inception to March 2020";

fn entry(id: &str, judgement: &str, quotes: &[&str]) -> Value {
    json!({"id": id, "judgement": judgement, "explanation": "why", "quotes": quotes})
}

fn reply(items: Vec<Value>) -> Value {
    json!({ "items": items })
}

#[test]
fn unbacked_claim_is_not_reported() {
    for claim in [
        Judgement::Reported,
        Judgement::PartlyReported,
        Judgement::ReportedElsewhere,
        Judgement::NotApplicable,
    ] {
        assert_eq!(
            shown_judgement(claim, 0, false),
            (Judgement::NotReported, true)
        );
    }
}

#[test]
fn backed_claim_stands() {
    assert_eq!(
        shown_judgement(Judgement::PartlyReported, 1, false),
        (Judgement::PartlyReported, false)
    );
}

#[test]
fn not_applicable_needs_the_flag() {
    assert_eq!(
        shown_judgement(Judgement::NotApplicable, 1, false),
        (Judgement::NotReported, true)
    );
    assert_eq!(
        shown_judgement(Judgement::NotApplicable, 1, true),
        (Judgement::NotApplicable, false)
    );
}

#[test]
fn not_reported_is_never_upgraded() {
    let part = read_part_reply(
        &reply(vec![entry("7", "not_reported", &[QUOTE])]),
        &["7"],
        TEXT,
        0.85,
    )
    .unwrap();
    let row = &part.items[0];
    assert_eq!(row.model_judgement, Judgement::NotReported);
    assert_eq!(row.judgement, Judgement::NotReported);
    assert!(!row.unbacked);
    assert_eq!(row.citations.len(), 1);
}

#[test]
fn backed_item_is_read_with_its_citation() {
    let part = read_part_reply(
        &reply(vec![entry("7", "reported", &[QUOTE])]),
        &["7"],
        TEXT,
        0.85,
    )
    .unwrap();
    let row = &part.items[0];
    assert_eq!(row.id, "7");
    assert_eq!((row.judgement, row.unbacked), (Judgement::Reported, false));
    assert_eq!(row.to_json()["judgement"], "reported");
}

#[test]
fn not_applicable_follows_each_rows_flag() {
    // The real rows: 7 may not be not_applicable, 13d may (it has a synthesis to lack).
    let text = "We did not assess the certainty of the evidence. Because no study reported the outcome, no meta-analysis was done.";
    let part = read_part_reply(
        &reply(vec![
            entry(
                "7",
                "not_applicable",
                &["We did not assess the certainty of the evidence"],
            ),
            entry("13d", "not_applicable", &["no meta-analysis was done"]),
        ]),
        &["7", "13d"],
        text,
        0.85,
    )
    .expect("read");
    let (seven, thirteen_d) = (&part.items[0], &part.items[1]);
    assert_eq!(
        (seven.judgement, seven.unbacked),
        (Judgement::NotReported, true)
    );
    assert_eq!(seven.citations.len(), 1);
    assert_eq!(
        (thirteen_d.judgement, thirteen_d.unbacked),
        (Judgement::NotApplicable, false)
    );
}

#[test]
fn missing_item_is_listed() {
    let part = read_part_reply(
        &reply(vec![entry("7", "reported", &[QUOTE])]),
        &["7", "8"],
        TEXT,
        0.85,
    )
    .unwrap();
    assert_eq!(part.missing, vec!["8"]);
    assert_eq!(part.items.len(), 1);
}

#[test]
fn extra_item_is_ignored() {
    common::log_capture::start();
    let part = read_part_reply(
        &reply(vec![
            entry("7", "reported", &[QUOTE]),
            entry("99", "reported", &[]),
        ]),
        &["7"],
        TEXT,
        0.85,
    )
    .unwrap();
    assert_eq!(part.items.len(), 1);
    assert_eq!(part.unasked, vec!["99"]);
    assert_eq!(part.to_json()["unasked"], json!(["99"]));
    let records = common::log_capture::take("bmlib.quality.prisma2020_models");
    assert!(records
        .iter()
        .any(|r| r.level == log::Level::Warn && r.message.contains("99")));
}

#[test]
fn unasked_ids_keep_the_reply_s_order_and_spelling() {
    // #807: an app shows these, so they are the reply's own ids, off the
    // checklist or not, an integer read as its digits as an asked one is.
    let mut five = entry("5", "reported", &[]);
    five["id"] = json!(5);
    let part = read_part_reply(
        &reply(vec![
            entry("99", "reported", &[]),
            entry("7", "reported", &[QUOTE]),
            five,
            entry("13a", "not_reported", &[]),
        ]),
        &["7"],
        TEXT,
        0.85,
    )
    .unwrap();
    assert_eq!(part.unasked, vec!["99", "5", "13a"]);
}

#[test]
fn a_reply_of_only_asked_items_lists_none_unasked() {
    let part = read_part_reply(
        &reply(vec![entry("7", "reported", &[QUOTE])]),
        &["7", "8"],
        TEXT,
        0.85,
    )
    .unwrap();
    assert!(part.unasked.is_empty());
    assert_eq!(part.to_json()["unasked"], json!([]));
}

#[test]
fn unknown_judgement_is_unreadable() {
    let error =
        read_part_reply(&reply(vec![entry("7", "maybe", &[])]), &["7"], TEXT, 0.85).unwrap_err();
    assert!(error.contains("maybe"), "{error}");
}

#[test]
fn duplicate_id_is_unreadable() {
    let error = read_part_reply(
        &reply(vec![
            entry("7", "reported", &[]),
            entry("7", "reported", &[]),
        ]),
        &["7"],
        TEXT,
        0.85,
    )
    .unwrap_err();
    assert!(error.contains("duplicate"), "{error}");
}

#[test]
fn integer_id_is_read_as_its_digits() {
    // Ruling 8: `"id": 7` is the likeliest model slip; a re-ask would cost a part.
    let mut item = entry("7", "reported", &[QUOTE]);
    item["id"] = json!(7);
    let part = read_part_reply(&reply(vec![item]), &["7"], TEXT, 0.85).expect("read");
    assert_eq!(
        part.items.iter().map(|row| row.id).collect::<Vec<_>>(),
        ["7"]
    );
    assert!(part.missing.is_empty());
}

#[test]
fn other_id_types_are_unreadable() {
    for bad in [json!(true), json!(7.0), Value::Null, json!(["7"])] {
        let mut item = entry("7", "reported", &[]);
        item["id"] = bad.clone();
        let error = read_part_reply(&reply(vec![item]), &["7"], TEXT, 0.85).unwrap_err();
        assert!(error.contains("id"), "{bad}: {error}");
    }
}

#[test]
fn items_must_be_a_list() {
    let error = read_part_reply(&json!({"items": "no"}), &["7"], TEXT, 0.85).unwrap_err();
    assert!(error.contains("items"), "{error}");
}

#[test]
fn suitable_is_sr_or_ma() {
    for (sr, ma, suitable) in [
        (true, false, true),
        (false, true, true),
        (false, false, false),
    ] {
        let result = read_suitability(&json!({
            "is_systematic_review": sr, "is_meta_analysis": ma,
            "document_type": "x", "variant": null, "rationale": "r", "confidence": 0.9
        }))
        .unwrap();
        assert_eq!(result.is_suitable, suitable);
    }
}

#[test]
fn variant_kept_as_given() {
    let result = read_suitability(&json!({
        "is_systematic_review": true, "is_meta_analysis": false,
        "document_type": "SR", "variant": "Living SR (odd)", "rationale": "r", "confidence": 3
    }))
    .unwrap();
    assert_eq!(result.variant.as_deref(), Some("Living SR (odd)"));
    assert_eq!(result.confidence, 1.0);
    assert_eq!(result.to_json()["variant"], "Living SR (odd)");
}

#[test]
fn suitability_flag_must_be_boolean() {
    let error =
        read_suitability(&json!({"is_systematic_review": "yes", "is_meta_analysis": false}))
            .unwrap_err();
    assert!(error.contains("is_systematic_review"), "{error}");
}
