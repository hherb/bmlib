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

//! PDF text assembly — the oracle and the named tests.
//!
//! The corpus (53 cases) diffs every pure helper against Python's. The named
//! tests state why the rules exist: one block per *line*, the dominant span, and
//! what "complete" means.

use bmlib::fulltext::pdf_text::{
    group_paragraphs, line_to_block, normalize_line, render_html, repeated_lines, span_text_weight,
    split_on_short_lines, ConversionResult, PARAGRAPH_BREAK_RATIO, REPEATED_LINE_MIN_PAGES,
    REPEATED_LINE_RATIO, SPAN_BOLD_FLAG, SPAN_ITALIC_FLAG,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/pdf_text_cases.json");
const EXPECTED: &str = include_str!("data/pdf_text_expected.json");

fn run(case: &Value) -> Value {
    let a = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "normalize" => json!(normalize_line(a["line"].as_str().unwrap_or_default())),
        "span_text_weight" => json!(span_text_weight(&a["span"])),
        "line_to_block" => match line_to_block(&a["line"], a["page_num"].as_i64().unwrap_or(0)) {
            None => Value::Null,
            Some(b) => json!({
                "text": b.text, "page_num": b.page_num, "font_size": b.font_size,
                "font_name": b.font_name, "is_bold": b.is_bold, "is_italic": b.is_italic,
                "x": b.x, "y": b.y, "width": b.width, "height": b.height,
            }),
        },
        "repeated_lines" => {
            let pages: Vec<String> = a["pages"]
                .as_array()
                .map(|p| {
                    p.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            let mut found: Vec<String> = repeated_lines(&pages).into_iter().collect();
            found.sort();
            json!(found)
        }
        "split_on_short_lines" => {
            let lines = strings(&a["lines"]);
            json!(split_on_short_lines(
                &lines,
                a["break_below"].as_f64().unwrap_or(0.0)
            ))
        }
        "group_paragraphs" => json!(group_paragraphs(&strings(&a["lines"]))),
        "render_html" => {
            let pages = a.get("page_texts").map(strings).unwrap_or_default();
            json!(render_html(
                a["success"].as_bool().unwrap_or(false),
                a["text"].as_str().unwrap_or_default(),
                &pages
            ))
        }
        "is_complete" => json!(ConversionResult {
            success: a["success"].as_bool().unwrap_or(false),
            page_count: a["page_count"].as_u64().unwrap_or(0) as usize,
            converted_pages: a["converted_pages"].as_u64().unwrap_or(0) as usize,
            char_count: a["char_count"].as_u64().unwrap_or(0) as usize,
            ..ConversionResult::default()
        }
        .is_complete()),
        "completion_ratio" => json!(ConversionResult {
            page_count: a["page_count"].as_u64().unwrap_or(0) as usize,
            converted_pages: a["converted_pages"].as_u64().unwrap_or(0) as usize,
            ..ConversionResult::default()
        }
        .completion_ratio()),
        other => panic!("unknown fn {other:?}"),
    }
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
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

/// **One block per _line_, not per span.** A reader starts a new span at every
/// font change, so a heading numbered in a different weight (`"2."` +
/// `"Materials and Methods"`) or a sentence holding an italic gene name would
/// otherwise shatter into fragments no anchored heading pattern can match.
#[test]
fn a_line_is_one_block_with_its_dominant_span_attributes() {
    let line = json!({
        "spans": [
            {"text": "2.", "size": 10.0, "font": "Body", "flags": 0},
            {"text": "Materials and Methods", "size": 14.0, "font": "Bold", "flags": 16},
        ],
        "bbox": [0.0, 0.0, 200.0, 14.0],
    });
    let block = line_to_block(&line, 3).expect("a block");
    // The text is the **concatenation** of the spans, and note the missing space:
    // spans carry their own trailing spaces, so joining would double them.
    assert_eq!(block.text, "2.Materials and Methods");
    // The attributes are the dominant span's, so the numbering does not restyle
    // the heading.
    assert_eq!(block.font_name, "Bold");
    assert_eq!(block.font_size, 14.0);
    assert!(block.is_bold);
    assert_eq!(block.page_num, 3);
    assert_eq!(block.width, 200.0);
    assert_eq!(block.height, 14.0);
}

/// **Ties go to the first span**, which `max_by_key` does not do — it returns the
/// last maximum. A reader that picked the last would take the superscript's
/// attributes for a line whose real weight came first.
#[test]
fn a_tie_for_the_dominant_span_goes_to_the_first() {
    let line = json!({
        "spans": [
            {"text": "aa", "size": 11.0, "font": "First", "flags": 0},
            {"text": "bb", "size": 13.0, "font": "Second", "flags": 2},
        ]
    });
    let block = line_to_block(&line, 0).expect("a block");
    assert_eq!(block.font_name, "First");
    assert_eq!(block.font_size, 11.0);
    assert!(
        !block.is_italic,
        "the second span's italic flag is not adopted"
    );
}

/// Only **non-whitespace** characters count toward dominance, so a span of spaces
/// cannot become dominant by width.
#[test]
fn dominance_counts_non_whitespace_only() {
    assert_eq!(span_text_weight(&json!({"text": "a b c"})), 3);
    assert_eq!(span_text_weight(&json!({"text": "   "})), 0);
    assert_eq!(span_text_weight(&json!({})), 0);
    // A wide whitespace span beside a narrow word: the word wins.
    let line = json!({
        "spans": [
            {"text": "          ", "font": "Whitespace", "size": 99.0},
            {"text": "word", "font": "Real", "size": 12.0},
        ]
    });
    assert_eq!(line_to_block(&line, 0).expect("a block").font_name, "Real");
}

/// The two flag bits are PyMuPDF's, and a line with neither is neither.
#[test]
fn the_span_flag_bits_are_read() {
    assert_eq!(SPAN_BOLD_FLAG, 16);
    assert_eq!(SPAN_ITALIC_FLAG, 2);
    let bold = line_to_block(&json!({"spans": [{"text": "x", "flags": 16}]}), 0).expect("block");
    assert!(bold.is_bold && !bold.is_italic);
    let italic = line_to_block(&json!({"spans": [{"text": "x", "flags": 2}]}), 0).expect("block");
    assert!(!italic.is_bold && italic.is_italic);
    let both = line_to_block(&json!({"spans": [{"text": "x", "flags": 18}]}), 0).expect("block");
    assert!(both.is_bold && both.is_italic);
    let neither = line_to_block(&json!({"spans": [{"text": "x", "flags": 0}]}), 0).expect("block");
    assert!(!neither.is_bold && !neither.is_italic);
}

/// A line with no non-whitespace text is **no block at all**, so the caller can
/// skip it rather than carrying an empty entry into the heading tests.
#[test]
fn a_blank_line_is_no_block() {
    assert!(line_to_block(&json!({"spans": [{"text": "   "}]}), 0).is_none());
    assert!(line_to_block(&json!({}), 0).is_none());
    assert!(line_to_block(&json!({"spans": []}), 0).is_none());
}

/// **Furniture is counted at most once per page**, so a phrase that merely repeats
/// *within* one page is not mistaken for a running head. Three pages of `"Dup"`
/// repeated internally must not all be stripped.
#[test]
fn furniture_is_counted_once_per_page() {
    // One page repeating a line three times, plus two others: not furniture.
    let pages = vec![
        "Dup\nDup\nDup".to_string(),
        "Other one".to_string(),
        "Other two".to_string(),
    ];
    assert!(
        repeated_lines(&pages).is_empty(),
        "{:?}",
        repeated_lines(&pages)
    );

    // The same line on every page is furniture.
    let pages = vec![
        "Head\nBody one".to_string(),
        "Head\nBody two".to_string(),
        "Head\nBody three".to_string(),
    ];
    let found = repeated_lines(&pages);
    assert!(found.contains("Head"), "{found:?}");
    assert!(!found.contains("Body one"), "{found:?}");

    // Below the page floor nothing is furniture at all: a two-page document's
    // repeated header is one of only a handful of lines.
    assert_eq!(REPEATED_LINE_MIN_PAGES, 3);
    assert!(repeated_lines(&["Head\nA".to_string(), "Head\nB".to_string()]).is_empty());
    assert_eq!(REPEATED_LINE_RATIO, 0.6);
}

/// A line is escaped for HTML, `&` first — or the ampersands the later
/// replacements introduce would be escaped twice.
#[test]
fn html_escaping_handles_the_ampersand_first() {
    let html = render_html(true, "A <b>bold</b> & \"quoted\" 'x'", &[]);
    assert_eq!(
        html,
        "<p>A &lt;b&gt;bold&lt;/b&gt; &amp; &quot;quoted&quot; &#x27;x&#x27;</p>"
    );
    // No `<` survives un-escaped from the document.
    assert!(!html.contains("<b>"), "{html}");
}

/// **A failed or empty conversion renders as nothing**, not as an empty
/// paragraph — a caller inserting the result should not get a blank line.
#[test]
fn a_failed_conversion_renders_nothing() {
    assert_eq!(render_html(false, "some text", &[]), "");
    assert_eq!(render_html(true, "   ", &[]), "");
    assert_eq!(render_html(true, "", &[]), "");
    assert_eq!(render_html(true, "text", &[]), "<p>text</p>");
}

/// Hard-wrapped lines are reflowed, and a line well short of the column width
/// ends the paragraph.
#[test]
fn short_lines_end_a_paragraph() {
    let wide = "x".repeat(80);
    let lines = vec![wide.clone(), "short".to_string(), wide.clone()];
    let paragraphs = group_paragraphs(&lines);
    assert_eq!(paragraphs.len(), 2, "{paragraphs:?}");
    assert!(paragraphs[0].ends_with("short"), "{paragraphs:?}");
    assert_eq!(PARAGRAPH_BREAK_RATIO, 0.85);

    // A break threshold below every line's length yields one paragraph.
    let joined = split_on_short_lines(&lines, 1.0);
    assert_eq!(joined.len(), 1);
    assert_eq!(joined[0].split(' ').count(), 3);
}

/// **"Complete" needs all three clauses.** A run that succeeded on three of ten
/// pages is not a complete conversion, and `success` alone would call it one.
#[test]
fn completeness_needs_every_page_and_some_text() {
    let partial = ConversionResult {
        success: true,
        page_count: 10,
        converted_pages: 3,
        char_count: 500,
        ..ConversionResult::default()
    };
    assert!(!partial.is_complete(), "three of ten pages");
    assert_eq!(partial.completion_ratio(), 0.3);

    let empty = ConversionResult {
        success: true,
        page_count: 10,
        converted_pages: 10,
        char_count: 0,
        ..ConversionResult::default()
    };
    assert!(!empty.is_complete(), "no characters came through");

    let failed = ConversionResult {
        success: false,
        page_count: 10,
        converted_pages: 10,
        char_count: 500,
        ..ConversionResult::default()
    };
    assert!(!failed.is_complete(), "the run failed");

    let complete = ConversionResult {
        success: true,
        page_count: 10,
        converted_pages: 10,
        char_count: 500,
        ..ConversionResult::default()
    };
    assert!(complete.is_complete());
    assert_eq!(complete.completion_ratio(), 1.0);
}

/// **`completion_ratio` is `0.0` for a document with no pages**, because a caller
/// filtering on it is asking what fraction came through, and nothing did.
///
/// Note `is_complete()` is **`true`** for the same input — and that is not a
/// contradiction: an empty document has every one of its zero pages converted, so
/// it is complete and its ratio is undefined. The two answer different questions,
/// and the first cut of this port conflated them by returning `1.0`.
#[test]
fn an_empty_document_has_no_ratio_but_is_complete() {
    let empty = ConversionResult {
        success: true,
        page_count: 0,
        converted_pages: 0,
        char_count: 10,
        ..ConversionResult::default()
    };
    assert_eq!(empty.completion_ratio(), 0.0, "nothing came through");
    assert!(empty.is_complete(), "every one of zero pages was converted");
}

/// The failure helper names its cause and reports nothing converted.
#[test]
fn a_failure_result_names_its_cause() {
    let failure = ConversionResult::failure("the file is not a PDF");
    assert!(!failure.success);
    assert!(!failure.is_complete());
    assert_eq!(
        failure.error_message.as_deref(),
        Some("the file is not a PDF")
    );
    assert_eq!(failure.completion_ratio(), 0.0);
    // And it renders as nothing.
    assert_eq!(render_html(failure.success, &failure.text, &[]), "");
    // `Display` names both the status and the completeness.
    let text = failure.to_string();
    assert!(text.contains("FAILED"), "{text}");
    assert!(text.contains("incomplete"), "{text}");
}
