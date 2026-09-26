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

//! JSON repair and span location — the named tests.
//!
//! `json_oracle` is the broad instrument (64 cases diffed against Python, with
//! four marked as deliberate corrections). This file is the reasoned half: the
//! regression suite for issue #299, and the properties a reader needs stated.

use bmlib::llm::json_repair::{
    extract_and_repair_json, repair_json_default, safe_json_loads, salvage_json_fields,
};
use bmlib::llm::utils::{extract_json, iter_json_spans};
use serde_json::json;

// ---------------------------------------------------------------------------
// Issue #299 — closers must be emitted in reverse-open order
// ---------------------------------------------------------------------------

/// The defect, exactly.
///
/// Python appended every `]` and **then** every `}`, which is equivalent only
/// when every `[` precedes every `{`. For an interleaved `[` `{` it produced
/// `[{"a": 1}, {"b": 2]}` — not JSON — so repair failed, the caller fell
/// through to a fragment extractor, and **the second object was silently
/// dropped**. The stack here closes in reverse-open order.
#[test]
fn an_interleaved_array_of_objects_is_repaired_not_truncated() {
    let repaired = repair_json_default("[{\"a\": 1}, {\"b\": 2").expect("repairable");
    assert_eq!(repaired, "[{\"a\": 1}, {\"b\": 2}]");
    let parsed: serde_json::Value = serde_json::from_str(&repaired).expect("valid JSON");
    assert_eq!(
        parsed.as_array().map(Vec::len),
        Some(2),
        "both objects must survive: {parsed}"
    );
}

/// The same shape one level up: an object holding an array holding an object.
#[test]
fn an_interleaved_object_holding_an_array_is_repaired() {
    let repaired = repair_json_default("{\"items\": [{\"a\": 1").expect("repairable");
    assert_eq!(repaired, "{\"items\": [{\"a\": 1}]}");
    serde_json::from_str::<serde_json::Value>(&repaired).expect("valid JSON");
}

/// Three interleaved openers, so a two-counter implementation cannot pass by
/// coincidence.
#[test]
fn three_interleaved_openers_close_in_order() {
    let repaired = repair_json_default("{\"a\": [{\"b\": [{\"c\": 1").expect("repairable");
    assert_eq!(repaired, "{\"a\": [{\"b\": [{\"c\": 1}]}]}");
    serde_json::from_str::<serde_json::Value>(&repaired).expect("valid JSON");
}

/// The regression is *silent* in Python: the extraction path returns the first
/// object and no error. Here it must return both.
#[test]
fn the_extraction_path_recovers_every_object() {
    let (text, repaired) =
        extract_and_repair_json("[{\"a\": 1}, {\"b\": 2", true).expect("extractable");
    assert!(repaired, "this needed repair");
    let parsed: serde_json::Value = serde_json::from_str(&text).expect("valid JSON");
    assert_eq!(parsed.as_array().map(Vec::len), Some(2));
}

/// The shapes Python already handled must keep working — the fix must not
/// regress the ordering that happened to be correct.
#[test]
fn the_shapes_python_already_repaired_still_work() {
    assert_eq!(
        repair_json_default("{\"items\": [{\"id\": 1}, {\"id\": 2}").expect("ok"),
        "{\"items\": [{\"id\": 1}, {\"id\": 2}]}"
    );
    assert_eq!(
        repair_json_default("{\"a\": 1, \"b\": [1, 2").expect("ok"),
        "{\"a\": 1, \"b\": [1, 2]}"
    );
    assert_eq!(
        repair_json_default("{\"n\": 12").expect("ok"),
        "{\"n\": 12}"
    );
    assert_eq!(
        repair_json_default("{\"a\": \"unterminated").expect("ok"),
        "{\"a\": \"unterminated\"}"
    );
}

// ---------------------------------------------------------------------------
// The other repairs, each pinned by behaviour rather than shape
// ---------------------------------------------------------------------------

#[test]
fn every_documented_repair_still_works() {
    let cases = [
        ("{'a': 1}", json!({"a": 1})),
        ("{\"a\": 1,}", json!({"a": 1})),
        ("[1, 2,]", json!([1, 2])),
        ("{\"a\": \"x\" \"b\": \"y\"}", json!({"a": "x", "b": "y"})),
        (
            "{key: \"value\", other: 5}",
            json!({"key": "value", "other": 5}),
        ),
        ("{\"a\": \"line1\nline2\"}", json!({"a": "line1\nline2"})),
        ("{\"a\": \"x\ty\"}", json!({"a": "x\ty"})),
    ];
    for (input, expected) in cases {
        let repaired = repair_json_default(input).unwrap_or_else(|e| panic!("{input:?}: {e}"));
        let parsed: serde_json::Value = serde_json::from_str(&repaired)
            .unwrap_or_else(|e| panic!("{input:?} -> {repaired:?}: {e}"));
        assert_eq!(parsed, expected, "input {input:?}");
    }
}

/// An apostrophe inside a double-quoted string is not a quote delimiter. A
/// naive transliteration of the state machine converts it and corrupts the
/// text.
#[test]
fn an_apostrophe_inside_a_double_quoted_string_survives() {
    let repaired = repair_json_default("{\"a\": \"it's fine\"}").expect("ok");
    let parsed: serde_json::Value = serde_json::from_str(&repaired).expect("valid");
    assert_eq!(parsed["a"], json!("it's fine"));
}

/// A brace inside a string does not close the span, and does not unbalance the
/// closer stack either.
#[test]
fn a_brace_inside_a_string_is_not_structure() {
    let repaired = repair_json_default("{\"a\": \"} not a closer\"").expect("ok");
    let parsed: serde_json::Value = serde_json::from_str(&repaired).expect("valid");
    assert_eq!(parsed["a"], json!("} not a closer"));
}

// ---------------------------------------------------------------------------
// Boundaries and contracts
// ---------------------------------------------------------------------------

/// Empty input is refused, and the three call sites word the refusal
/// differently — as Python does.
#[test]
fn empty_input_is_refused_by_each_entry_point() {
    assert_eq!(
        repair_json_default("").unwrap_err().to_string(),
        "Cannot repair empty JSON string"
    );
    assert_eq!(
        safe_json_loads("", true, 3).unwrap_err().to_string(),
        "Cannot parse empty JSON string"
    );
    assert_eq!(
        extract_and_repair_json("", true).unwrap_err().to_string(),
        "Cannot extract JSON from empty response"
    );
    // Whitespace-only counts as empty for all three.
    assert!(repair_json_default("   ").is_err());
    assert!(safe_json_loads("\n\t", true, 3).is_err());
    assert!(extract_and_repair_json("  ", true).is_err());
}

/// `repair: false` still says *why* the last candidate was rejected rather
/// than claiming no JSON was found — the candidate was found and rejected.
#[test]
fn disabling_repair_still_names_the_parse_failure() {
    let err = extract_and_repair_json("{'a': 1}", false)
        .unwrap_err()
        .to_string();
    assert!(
        err.starts_with("Cannot parse extracted JSON:"),
        "the failure must name the parse, not the search: {err}"
    );
}

/// Salvage recovers an intact field beside a truncated tail — and never
/// raises, which is its whole contract.
#[test]
fn salvage_recovers_intact_fields_beside_a_truncated_tail() {
    let recovered = salvage_json_fields(
        "{\"title\": \"T\", \"items\": [1, 2",
        &["title".to_string(), "items".to_string()],
    );
    assert_eq!(recovered.get("title"), Some(&json!("T")));
    assert_eq!(recovered.get("items"), Some(&json!([1, 2])));
    // Malformed input is not an error here.
    assert!(salvage_json_fields("not json", &["a".to_string()]).is_empty());
    assert!(salvage_json_fields("", &["a".to_string()]).is_empty());
}

/// `extract_json` prefers a dict at the top level but a *fence* wins outright,
/// because a fence is the model's own delimitation of its answer.
#[test]
fn a_fence_beats_the_dict_preference_but_prose_does_not() {
    assert_eq!(extract_json("```json\n[1,2]\n```", true), "[1,2]");
    assert_eq!(
        extract_json("text [1,2] then {\"a\": 1}", true),
        "{\"a\": 1}",
        "without a fence, the object is preferred"
    );
}

/// A nested-object fragment is refused when `allow_fragments` is false. This is
/// the contract the #299 fix depends on: the caller with a repair stage wants
/// the whole span, not the first object out of it.
#[test]
fn fragments_are_refused_when_asked() {
    let text = "[{\"a\": 1}, {\"b\": 2";
    assert_eq!(
        extract_json(text, false),
        text,
        "a caller that can repair must not be handed a fragment"
    );
    assert_eq!(
        extract_json(text, true),
        "{\"a\": 1}",
        "the fragment is the last resort when no repair stage exists"
    );
}

/// Span location never yields the same text twice: an identical span parses
/// and repairs identically, so re-offering it only buys a second run of the
/// repair loop on a span that has already failed.
#[test]
fn no_span_is_offered_twice() {
    let spans = iter_json_spans("{\"a\": 1} and [{\"b\": 2}]", true);
    let mut sorted = spans.clone();
    sorted.sort();
    sorted.dedup();
    assert_eq!(sorted.len(), spans.len(), "duplicate span in {spans:?}");
}

/// An unbalanced tail is offered whole, which is what truncated model output
/// looks like — but only when nothing balanced, since otherwise a later opener
/// is nested inside the unbalanced span rather than a sibling.
#[test]
fn an_unbalanced_tail_is_offered_only_when_nothing_balanced() {
    let spans = iter_json_spans("prose {\"a\": 1, \"b\": 2", true);
    assert!(
        spans.iter().any(|s| s == "{\"a\": 1, \"b\": 2"),
        "{spans:?}"
    );
    let balanced = iter_json_spans("{\"a\": 1} and {\"b\": 2}", true);
    assert!(
        !balanced.iter().any(|s| s.contains(" and ")),
        "a balanced document has no tail span: {balanced:?}"
    );
}
