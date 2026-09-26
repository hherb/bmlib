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

//! The PDF section segmenter — the oracle and the named tests.
//!
//! The corpus (125 cases) diffs every pattern, the heading predicate, the
//! paragraph rule and 13 whole documents against Python's. The named tests state
//! the rules the oracle cannot: why the fallback has its own confidence, and why
//! a negative gap is not a paragraph break.

use bmlib::fulltext::models::{Section, SectionType, TextBlock};
use bmlib::fulltext::segmenter::{
    extract_title, is_potential_header, join_blocks, match_section_type, median_font_size,
    SectionSegmenter, DEFAULT_FONT_SIZE, FALLBACK_CONFIDENCE, MAX_HEADING_CHARS,
    PARTIAL_MATCH_CONFIDENCE, SECTION_PATTERNS,
};
use serde_json::{json, Value};

/// A block with placeholder geometry, for a test that sets only what it is about.
///
/// Local to this test rather than a `TextBlock::for_test()` on the model: a
/// test-only constructor in the public API is a thing a downstream caller can
/// find and use, and it would then have to keep working.
fn base_block() -> TextBlock {
    TextBlock {
        text: "x".to_string(),
        page_num: 0,
        font_size: 12.0,
        font_name: "Body".to_string(),
        is_bold: false,
        is_italic: false,
        x: 0.0,
        y: 0.0,
        width: 100.0,
        height: 10.0,
    }
}

const CASES: &str = include_str!("data/segmenter_cases.json");
const EXPECTED: &str = include_str!("data/segmenter_expected.json");

fn block_of(spec: &Value) -> TextBlock {
    let f = |key: &str, default: f64| spec.get(key).and_then(Value::as_f64).unwrap_or(default);
    TextBlock {
        text: spec["text"].as_str().unwrap_or_default().to_string(),
        page_num: spec.get("page_num").and_then(Value::as_i64).unwrap_or(0),
        font_size: f("font_size", 12.0),
        font_name: spec
            .get("font_name")
            .and_then(Value::as_str)
            .unwrap_or("Body")
            .to_string(),
        is_bold: spec
            .get("is_bold")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        is_italic: spec
            .get("is_italic")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        x: f("x", 0.0),
        y: f("y", 0.0),
        width: f("width", 100.0),
        height: f("height", 10.0),
    }
}

fn blocks_of(value: &Value) -> Vec<TextBlock> {
    value
        .as_array()
        .map(|a| a.iter().map(block_of).collect())
        .unwrap_or_default()
}

fn render_section(section: &Section) -> Value {
    json!({
        "section_type": section.section_type.as_str(),
        "title": section.title,
        "content": section.content,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "confidence": section.confidence,
        "subsections": section.subsections.iter().map(render_section).collect::<Vec<_>>(),
    })
}

fn run(case: &Value) -> Value {
    let a = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "median_font_size" => json!(median_font_size(&blocks_of(&a["blocks"]))),
        "join_blocks" => json!(join_blocks(&blocks_of(&a["blocks"]))),
        "match_section_type" => {
            let (section_type, confidence) =
                match_section_type(a["text"].as_str().unwrap_or_default());
            json!({"section_type": section_type.as_str(), "confidence": confidence})
        }
        "is_potential_header" => json!(is_potential_header(
            &block_of(&a["block"]),
            a["median_font_size"].as_f64().unwrap_or(12.0),
            1.2,
            10.0,
        )),
        "segment_document" => {
            let metadata = a.get("metadata").cloned().unwrap_or(json!({}));
            // Owned, because `metadata` moves into the document below.
            let file_path = metadata
                .get("file_path")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let title = metadata
                .get("title")
                .and_then(Value::as_str)
                .map(str::to_string);
            let document = SectionSegmenter::default().segment_document(
                &blocks_of(&a["blocks"]),
                &file_path,
                title.as_deref(),
                metadata,
            );
            json!({
                "file_path": document.file_path,
                "title": document.title,
                "sections": document.sections.iter().map(render_section).collect::<Vec<_>>(),
            })
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
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

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

/// **The anchored match wins at 1.0 and the word-bounded search is the 0.7
/// fallback**, so a caller can tell a heading the document declares from one a
/// phrase inside it merely resembles.
#[test]
fn a_partial_match_carries_its_own_confidence() {
    assert_eq!(
        match_section_type("Supplementary Materials"),
        (SectionType::Supplementary, 1.0),
        "an exact heading"
    );
    assert_eq!(
        match_section_type("Supplementary materials online"),
        (SectionType::Supplementary, PARTIAL_MATCH_CONFIDENCE),
        "a phrase containing the heading"
    );
    assert_eq!(
        match_section_type("A completely unrelated heading"),
        (SectionType::Unknown, 0.0)
    );
    assert_eq!(match_section_type(""), (SectionType::Unknown, 0.0));
}

/// **Leading numbering and trailing punctuation are stripped before matching**, so
/// `"2.3 Methods"` and `"Discussion:"` classify — and the stripping happens once,
/// so `" 2.1 Background: "` does both.
#[test]
fn numbering_and_punctuation_are_stripped() {
    for text in ["Methods", "2.3 Methods", "4) Methods", "  2.1  Methods.  "] {
        assert_eq!(match_section_type(text).0, SectionType::Methods, "{text:?}");
    }
    for text in ["Discussion", "Discussion:", "Discussion.", "Discussion?"] {
        assert_eq!(
            match_section_type(text).0,
            SectionType::Discussion,
            "{text:?}"
        );
    }
    // Matching is case-insensitive.
    assert_eq!(
        match_section_type("INTRODUCTION").0,
        SectionType::Introduction
    );
    assert_eq!(
        match_section_type("MaTeRiAlS aNd MeThOdS").0,
        SectionType::Methods
    );
    // A bare number is not a heading, and classifies as nothing.
    assert_eq!(match_section_type("12.").0, SectionType::Unknown);
}

/// **Every pattern in the table is reachable**, which is what keeps the table's
/// transcription honest: a pattern mistyped so it can never match is a section
/// type that silently never appears.
#[test]
fn every_pattern_compiles_and_matches_something() {
    let mut total = 0usize;
    for (section_type, patterns) in SECTION_PATTERNS {
        assert!(
            !patterns.is_empty(),
            "{} must have at least one pattern",
            section_type.as_str()
        );
        total += patterns.len();
    }
    assert_eq!(total, 61, "the table's size is a decision, not a detail");

    // **Every pattern is exercised by the oracle**, which carries one case per
    // heading spelling — 94 of the 125 cases are pattern cases. This test asserts
    // that the corpus still covers the table, so a pattern added without a case
    // fails here rather than going unexercised.
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let pattern_cases = cases
        .as_array()
        .expect("cases")
        .iter()
        .filter(|c| c["fn"].as_str() == Some("match_section_type"))
        .count();
    assert!(
        pattern_cases >= total,
        "the corpus must have at least one case per pattern: {pattern_cases} cases, {total} patterns"
    );
}

// ---------------------------------------------------------------------------
// The heading predicate
// ---------------------------------------------------------------------------

/// **A heading is short, has letters, and is either heading-sized or bold at body
/// size.** Each clause refuses a real shape: a body line, a bare `"3."`, and a
/// paragraph.
#[test]
fn the_heading_predicate_refuses_prose_and_numbering() {
    let heading = |text: &str, size: f64, bold: bool| {
        let mut block = base_block();
        block.text = text.to_string();
        block.font_size = size;
        block.is_bold = bold;
        block
    };
    // **`14.0` is _not_ heading-sized at a median of 12.0 with a 1.2 threshold**:
    // `12.0 * 1.2` is `14.399999999999999` in floating point, so 14.0 falls below
    // it. A first cut of this test used 14.0 and asserted `true`, which is the
    // arithmetic error the corpus case for this shape also carries — both were
    // checked against Python, which agrees. 15.0 is the honest value.
    assert!(!is_potential_header(
        &heading("Methods", 14.0, false),
        12.0,
        1.2,
        10.0
    ));
    assert!(is_potential_header(
        &heading("Methods", 15.0, false),
        12.0,
        1.2,
        10.0
    ));
    // Body-sized and not bold: prose.
    assert!(!is_potential_header(
        &heading("Some text here", 12.0, false),
        12.0,
        1.2,
        10.0
    ));
    // Body-sized but bold: **the rescue**, so a journal that sets headings at body
    // size still segments.
    assert!(is_potential_header(
        &heading("Methods", 12.0, true),
        12.0,
        1.2,
        10.0
    ));
    // Below the absolute floor, bold or not.
    assert!(!is_potential_header(
        &heading("Methods", 9.0, true),
        12.0,
        1.2,
        10.0
    ));
    // Too long is prose whatever its font.
    assert!(!is_potential_header(
        &heading(&"x".repeat(101), 20.0, false),
        12.0,
        1.2,
        10.0
    ));
    assert!(is_potential_header(
        &heading(&"x".repeat(100), 20.0, false),
        12.0,
        1.2,
        10.0
    ));
    // No alphabetic character: a bare number is numbering, not a heading.
    assert!(!is_potential_header(
        &heading("3.", 20.0, false),
        12.0,
        1.2,
        10.0
    ));
}

/// **The length bound counts characters, not bytes.** A heading of 101
/// multi-byte letters is still too long, where a byte count would refuse far
/// shorter text.
#[test]
fn the_length_bound_counts_characters() {
    let long = TextBlock {
        text: "\u{e9}".repeat(MAX_HEADING_CHARS + 1),
        font_size: 20.0,
        ..base_block()
    };
    assert!(!is_potential_header(&long, 12.0, 1.2, 10.0));
    let at_bound = TextBlock {
        text: "\u{e9}".repeat(MAX_HEADING_CHARS),
        font_size: 20.0,
        ..base_block()
    };
    assert!(is_potential_header(&at_bound, 12.0, 1.2, 10.0));
}

// ---------------------------------------------------------------------------
// Medians, gaps and titles
// ---------------------------------------------------------------------------

/// **The median, not the mean**, so headings and footnotes cannot drag the
/// body-text estimate — which is the number every heading decision is measured
/// against.
#[test]
fn the_body_size_is_a_median() {
    let sizes = [10.0, 12.0, 14.0];
    assert_eq!(median_font_size(&blocks_with(&sizes)), 12.0);
    // A mean would be dragged by the 40: (10+12+14+40)/4 = 19.
    let sizes = [10.0, 12.0, 14.0, 40.0];
    assert_eq!(median_font_size(&blocks_with(&sizes)), 13.0);
    // Non-positive sizes are ignored, and none at all gives the assumed body.
    assert_eq!(median_font_size(&blocks_with(&[0.0, 12.0, 0.0])), 12.0);
    assert_eq!(median_font_size(&blocks_with(&[0.0])), DEFAULT_FONT_SIZE);
    assert_eq!(median_font_size(&[]), DEFAULT_FONT_SIZE);
}

fn blocks_with(sizes: &[f64]) -> Vec<TextBlock> {
    sizes
        .iter()
        .map(|s| TextBlock {
            font_size: *s,
            ..base_block()
        })
        .collect()
}

/// **A negative gap is not a paragraph break.** A column or page boundary sends
/// the gap negative, so a paragraph continuing across the boundary stays one
/// paragraph — and a PDF gives no signal that would distinguish it from one that
/// ends at it.
#[test]
fn a_negative_gap_is_not_a_break() {
    let block = |y: f64, height: f64| TextBlock {
        y,
        height,
        ..base_block()
    };
    // **The gap is not the second block's `y`.** With both a `y` offset and a
    // height, the gap is `next.y - (previous.y + previous.height)`, and the
    // threshold is the **next** block's height times the ratio. A first cut of
    // this test read `y` as the gap and asserted a break where the source has
    // none — Python agrees with the port on every value below.
    //
    // Touching blocks: gap 0, no break.
    assert_eq!(join_blocks(&[block(0.0, 10.0), block(10.0, 10.0)]), "x\nx");
    // Exactly the threshold of 15 is **not** greater than it.
    assert_eq!(join_blocks(&[block(0.0, 10.0), block(25.0, 10.0)]), "x\nx");
    // One past it breaks.
    assert_eq!(
        join_blocks(&[block(0.0, 10.0), block(26.0, 10.0)]),
        "x\n\nx"
    );
    // A next block *above* the previous one sends the gap negative, so no break.
    assert_eq!(join_blocks(&[block(100.0, 10.0), block(0.0, 10.0)]), "x\nx");
    // **A degenerate next height makes the threshold zero**, so any positive gap
    // breaks — whatever the previous block's height was.
    assert_eq!(join_blocks(&[block(0.0, 0.0), block(1.0, 0.0)]), "x\n\nx");
    assert_eq!(join_blocks(&[block(0.0, 10.0), block(14.0, 0.0)]), "x\n\nx");
    assert_eq!(join_blocks(&[block(0.0, 10.0), block(20.0, 0.0)]), "x\n\nx");
    // And a degenerate *previous* block does not: the threshold is the next one's.
    assert_eq!(join_blocks(&[block(0.0, 0.0), block(1.0, 10.0)]), "x\nx");
}

/// **The title fallback needs the font to exceed the median by half again**,
/// otherwise an ordinary line becomes the title of every PDF whose metadata is
/// blank.
#[test]
fn the_title_fallback_needs_a_larger_font() {
    let title = TextBlock {
        text: "The Title".to_string(),
        font_size: 24.0,
        page_num: 0,
        ..base_block()
    };
    let body = TextBlock {
        text: "body".to_string(),
        font_size: 12.0,
        page_num: 0,
        ..base_block()
    };
    assert_eq!(
        extract_title(&[title.clone(), body.clone()], None, 12.0),
        Some("The Title".to_string())
    );
    // Exactly 1.5x is not greater than it.
    let modest = TextBlock {
        font_size: 18.0,
        ..title.clone()
    };
    assert_eq!(extract_title(&[modest, body.clone()], None, 12.0), None);
    // No first-page block at all: no fallback.
    let page_two = TextBlock {
        page_num: 1,
        ..title.clone()
    };
    assert_eq!(extract_title(&[page_two], None, 12.0), None);
    // **A corroborated metadata title wins over the font**, and one the page does
    // not print is refused so the font fallback gets its chance.
    assert_eq!(
        extract_title(
            &[title.clone(), body.clone()],
            Some("Not Printed Anywhere"),
            12.0
        ),
        Some("The Title".to_string())
    );
}

/// **`FALLBACK_CONFIDENCE` is for sections that contain rather than classify** —
/// front matter, and the whole-document fallback when no heading was found.
#[test]
fn the_container_confidences_are_distinct() {
    assert_eq!(FALLBACK_CONFIDENCE, 0.5);
    assert_eq!(PARTIAL_MATCH_CONFIDENCE, 0.7);
    const {
        assert!(
            FALLBACK_CONFIDENCE < PARTIAL_MATCH_CONFIDENCE,
            "a container is less sure than a partial heading match"
        )
    };

    // A document with blocks but no headings is one UNKNOWN section at the
    // fallback confidence.
    let blocks = vec![
        TextBlock {
            text: "Just prose".to_string(),
            font_size: 12.0,
            ..base_block()
        },
        TextBlock {
            text: "More prose".to_string(),
            font_size: 12.0,
            y: 20.0,
            ..base_block()
        },
    ];
    let document = SectionSegmenter::default().segment_document(&blocks, "", None, json!({}));
    assert_eq!(document.sections.len(), 1);
    assert_eq!(document.sections[0].section_type, SectionType::Unknown);
    assert_eq!(document.sections[0].title, "Full Text");
    assert_eq!(document.sections[0].confidence, FALLBACK_CONFIDENCE);

    // No blocks at all is no sections.
    let document = SectionSegmenter::default().segment_document(&[], "", None, json!({}));
    assert!(document.sections.is_empty());
}
