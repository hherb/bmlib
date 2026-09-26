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

//! The parse-unwind audit — the oracle and the named tests.
//!
//! The corpus exercises **every field alone** and then in combination, so a
//! message that drifts and a message that fires on the wrong field are both
//! caught. The named tests state what the oracle cannot: what a clean unwind
//! guarantees, and why the ordering is load-bearing.

use bmlib::fulltext::parse_audit::{unwind_diagnostics, ParseUnwindState};
use serde_json::Value;

const CASES: &str = include_str!("data/parse_audit_cases.json");
const EXPECTED: &str = include_str!("data/parse_audit_expected.json");

fn state_of(spec: &Value) -> ParseUnwindState {
    let get = |key: &str| spec.get(key).and_then(Value::as_u64).unwrap_or(0) as u32;
    let list = |key: &str| {
        spec.get(key)
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default()
    };
    ParseUnwindState {
        nested_article_depth: get("nested_article_depth"),
        open_sections: get("open_sections"),
        open_figures: get("open_figures"),
        open_tables: get("open_tables"),
        open_captions: get("open_captions"),
        open_formulas: get("open_formulas"),
        open_contrib_groups: get("open_contrib_groups"),
        open_contribs: get("open_contribs"),
        open_definition_items: get("open_definition_items"),
        open_award_groups: get("open_award_groups"),
        open_funder_named_content: get("open_funder_named_content"),
        open_container_headings: get("open_container_headings"),
        unfilled_author_slots: get("unfilled_author_slots"),
        unfilled_figure_slots: get("unfilled_figure_slots"),
        unfilled_table_slots: get("unfilled_table_slots"),
        excess_text_buffers: get("excess_text_buffers"),
        open_elements: list("open_elements"),
        stuck_flags: list("stuck_flags"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected["cases"].as_array().expect("cases");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = unwind_diagnostics(&state_of(&case["args"]["state"]));
        let expected_messages: Vec<String> = want["value"]
            .as_array()
            .expect("messages")
            .iter()
            .map(|v| v.as_str().unwrap_or_default().to_string())
            .collect();
        if got != expected_messages {
            failures.push(format!(
                "  {name}\n    python: {expected_messages:#?}\n    rust:   {got:#?}"
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// **A clean unwind produces nothing**, which is what makes every message a
/// claim that the parser is wrong rather than a noisy warning. A field holding a
/// raw depth rather than the *excess* would break this: the reader's text stack
/// always holds one buffer, so the default would read as an imbalance.
#[test]
fn a_clean_unwind_reports_nothing() {
    assert!(
        unwind_diagnostics(&ParseUnwindState::default()).is_empty(),
        "the default state must be the clean one"
    );
}

/// **The corpus covers every field**, and this test is what keeps that true: a
/// field added to the state without a case would otherwise be a rule nothing
/// exercises.
#[test]
fn every_state_field_fires_on_its_own() {
    let expected = serde_json::from_str::<Value>(EXPECTED).expect("expected");
    let fields = expected["fields"]["int_fields"]
        .as_array()
        .expect("int fields")
        .iter()
        .map(|v| v.as_str().expect("name").to_string())
        .collect::<Vec<_>>();
    assert!(
        fields.len() >= 16,
        "the corpus must cover the whole state: {fields:?}"
    );

    // Each one alone produces exactly one message, and the message names the
    // **count**.
    for field in &fields {
        let mut state = ParseUnwindState::default();
        match field.as_str() {
            "nested_article_depth" => state.nested_article_depth = 3,
            "open_sections" => state.open_sections = 3,
            "open_figures" => state.open_figures = 3,
            "open_tables" => state.open_tables = 3,
            "open_captions" => state.open_captions = 3,
            "open_formulas" => state.open_formulas = 3,
            "open_contrib_groups" => state.open_contrib_groups = 3,
            "open_contribs" => state.open_contribs = 3,
            "open_definition_items" => state.open_definition_items = 3,
            "open_award_groups" => state.open_award_groups = 3,
            "open_funder_named_content" => state.open_funder_named_content = 3,
            "open_container_headings" => state.open_container_headings = 3,
            "unfilled_author_slots" => state.unfilled_author_slots = 3,
            "unfilled_figure_slots" => state.unfilled_figure_slots = 3,
            "unfilled_table_slots" => state.unfilled_table_slots = 3,
            "excess_text_buffers" => state.excess_text_buffers = 3,
            other => panic!("a state field with no case: {other}"),
        }
        let messages = unwind_diagnostics(&state);
        assert_eq!(messages.len(), 1, "{field} alone: {messages:?}");
        assert!(
            messages[0].starts_with('3'),
            "{field} must name its count: {}",
            messages[0]
        );
    }
}

/// **The nested-article line comes first when several fire.** It is the only
/// imbalance that discards the *rest of the document* rather than the content it
/// was routing, so it is the one to read first — and a reader who stops at the
/// first line gets the most consequential one.
#[test]
fn the_nested_article_line_is_reported_first() {
    let state = ParseUnwindState {
        // Alphabetically last of the set, and reported first.
        unfilled_table_slots: 1,
        open_sections: 1,
        nested_article_depth: 2,
        ..ParseUnwindState::default()
    };
    let messages = unwind_diagnostics(&state);
    assert!(messages[0].contains("sub-article"), "{messages:#?}");

    // With no nested article, the order is the declaration order — which is the
    // order of the struct's fields, so the check is that sections precede
    // tables. Note the second line is the **slot** message, not the frame one:
    // `unfilled_table_slots` and `open_tables` are separate fields because they
    // are separate claims, and only the frame one says `<table-wrap> still
    // open`.
    let state = ParseUnwindState {
        unfilled_table_slots: 1,
        open_sections: 1,
        ..ParseUnwindState::default()
    };
    let messages = unwind_diagnostics(&state);
    assert!(messages[0].contains("<sec>"), "{messages:#?}");
    assert!(
        messages[1].contains("table slot(s) reserved and never filled"),
        "{messages:#?}"
    );
    assert_eq!(messages.len(), 2);

    // The frame imbalance is its own message, and it does name the element.
    let state = ParseUnwindState {
        open_tables: 1,
        ..ParseUnwindState::default()
    };
    assert!(
        unwind_diagnostics(&state)[0].contains("<table-wrap> still open"),
        "the frame message names the element; the slot message names the cost"
    );
}

/// Every message names **what the imbalance cost**, not merely what was left
/// open: *"2 `<fig>` still open"* is not actionable on its own, and *"their
/// figures were never built"* is.
#[test]
fn every_message_names_the_cost() {
    let state = ParseUnwindState {
        open_figures: 2,
        ..ParseUnwindState::default()
    };
    let messages = unwind_diagnostics(&state);
    assert!(messages[0].contains("never built"), "{}", messages[0]);

    let state = ParseUnwindState {
        open_captions: 1,
        ..ParseUnwindState::default()
    };
    assert!(
        unwind_diagnostics(&state)[0].contains("filed as caption text"),
        "the cost of a stranded caption is where the prose went"
    );
}

/// The two **list** fields are reported by name, and the element stack is joined
/// outermost-first with a ` > ` — the shape an operator reads as a path.
#[test]
fn the_element_stack_is_reported_as_a_path() {
    let state = ParseUnwindState {
        open_elements: vec!["article".to_string(), "body".to_string(), "sec".to_string()],
        ..ParseUnwindState::default()
    };
    let message = &unwind_diagnostics(&state)[0];
    assert!(message.contains("article > body > sec"), "{message}");

    let state = ParseUnwindState {
        stuck_flags: vec!["in_caption".to_string(), "in_figure".to_string()],
        ..ParseUnwindState::default()
    };
    let message = &unwind_diagnostics(&state)[0];
    assert!(message.contains("in_caption, in_figure"), "{message}");
}

/// **The counts can diverge**, which is why `open_contribs` and
/// `unfilled_author_slots` are two fields: a non-author frame reserves no slot,
/// and a `<contrib>` naming nobody gives its slot back, so neither number is
/// derivable from the other. Reporting one would report the wrong number for the
/// other's case.
#[test]
fn a_stranded_frame_and_an_unfilled_slot_are_separate_claims() {
    let frames = ParseUnwindState {
        open_contribs: 3,
        ..ParseUnwindState::default()
    };
    let slots = ParseUnwindState {
        unfilled_author_slots: 3,
        ..ParseUnwindState::default()
    };
    let frames_message = &unwind_diagnostics(&frames)[0];
    let slots_message = &unwind_diagnostics(&slots)[0];
    assert!(
        frames_message.contains("<contrib> still open"),
        "{frames_message}"
    );
    assert!(
        slots_message.contains("reserved and never filled"),
        "{slots_message}"
    );
    assert_ne!(
        frames_message, slots_message,
        "the two imbalances cost different things"
    );

    // And both at once is two messages, not one.
    let both = ParseUnwindState {
        open_contribs: 3,
        unfilled_author_slots: 3,
        ..ParseUnwindState::default()
    };
    assert_eq!(unwind_diagnostics(&both).len(), 2);
}
