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

//! Cochrane renderers — the named tests.
//!
//! `formatter_oracle` compares 32 rendered artefacts byte for byte. This file
//! states the properties behind them, and the two that are easy to get wrong
//! silently: escaping order, and the percentage format.

use bmlib::quality::cochrane_formatter::{
    domain_label, escape_html, format_complete_assessment_markdown,
    format_risk_of_bias_summary_markdown, format_study_characteristics_html,
    format_study_characteristics_markdown, judgement_css_class, judgement_symbol, COCHRANE_CSS,
};
use bmlib::quality::cochrane_models::{
    create_default_cochrane_risk_of_bias, create_default_risk_of_bias_item, CochraneInterventions,
    CochraneNotes, CochraneOutcomes, CochraneParticipants, CochraneStudyAssessment,
    CochraneStudyCharacteristics, ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_LOW, ROB_JUDGEMENT_UNCLEAR,
};

fn chars() -> CochraneStudyCharacteristics {
    CochraneStudyCharacteristics::new(
        "Andrei 2011",
        "Parallel randomised trial",
        CochraneParticipants::new("Romania", "Chronic heart failure"),
        CochraneInterventions::new("Hospital at home"),
        CochraneOutcomes::new("Mortality, cost"),
        CochraneNotes::default(),
    )
}

fn assessment() -> CochraneStudyAssessment {
    CochraneStudyAssessment::new(chars(), create_default_cochrane_risk_of_bias())
}

// ---------------------------------------------------------------------------
// HTML escaping
// ---------------------------------------------------------------------------

/// The ampersand is escaped **first**. Doing it after `<` would leave the
/// `&lt;` it just produced to be escaped again into `&amp;lt;`, which renders
/// as literal text.
#[test]
fn escaping_the_ampersand_first_is_what_makes_it_correct() {
    assert_eq!(escape_html("<"), "&lt;");
    assert_eq!(escape_html("&"), "&amp;");
    assert_eq!(
        escape_html("<b>"),
        "&lt;b&gt;",
        "the tags' own ampersands must not be double-escaped"
    );
    assert_eq!(escape_html("&<"), "&amp;&lt;");
    assert_eq!(escape_html("\"'"), "&quot;&#39;");
    // Text with nothing to escape is returned unchanged.
    assert_eq!(escape_html("plain text"), "plain text");
}

/// Newlines become `<br>` **after** escaping. The reverse would introduce `<`
/// characters that the escaper then neutralises, rendering the breaks as text.
#[test]
fn a_newline_becomes_a_br_and_not_an_escaped_literal() {
    let mut c = chars();
    c.notes.funding_source = Some("F".to_string());
    c.notes.ethical_approval = Some("E".to_string());
    let html = format_study_characteristics_html(&c);

    assert!(
        html.contains("Funding: F<br><br>Ethical approval: E"),
        "both newlines of the blank-line separator must be real <br>: {html}"
    );
    assert!(
        !html.contains("&lt;br&gt;"),
        "the break must not be escaped into literal text: {html}"
    );
}

/// The markup the renderer itself writes is never escaped — a tag with an
/// attribute is emitted verbatim, and only the *data* is escaped.
#[test]
fn the_renderers_own_markup_is_not_escaped() {
    let html = format_study_characteristics_html(&chars());
    assert!(html.contains("<table class=\"cochrane-characteristics\">"));
    assert!(html.contains("<td>Methods</td>"));
    assert!(!html.contains("&lt;table"));
}

/// A study id carrying markup is escaped in the heading, not rendered.
#[test]
fn a_study_id_with_markup_is_escaped() {
    let mut c = chars();
    c.study_id = "A & B <script>".to_string();
    let html = format_study_characteristics_html(&c);
    assert!(html.contains("A &amp; B &lt;script&gt;"));
    assert!(!html.contains("<script>"));
}

// ---------------------------------------------------------------------------
// Formatting details that are easy to get wrong
// ---------------------------------------------------------------------------

/// A lone confidence enters the summary block, which is **the fix for a
/// renderer defect** (#312): Python's guard tests only `overall_quality_score
/// is not None or assessment.evidence_level`, so a caller stating 80%
/// certainty and nothing else got an assessment whose output never mentions
/// it. The four `corrected` cases in the oracle cover it; this states the
/// property.
#[test]
fn a_confidence_alone_renders_the_summary() {
    let mut a = assessment();
    a.overall_confidence = Some(0.8);
    let md = format_complete_assessment_markdown(&a);
    assert!(md.contains("Assessment Summary"), "{md}");
    assert!(md.contains("Assessment Confidence:** 80%"), "{md}");
}

/// Python's `{:.0%}` renders a 0–1 value as a **whole-number percentage**, so
/// `0.666` is `"67%"`. Rendering the raw float, or multiplying by the wrong
/// factor, both look plausible and are wrong.
#[test]
fn confidence_renders_as_a_whole_number_percentage() {
    let mut a = assessment();
    a.overall_confidence = Some(0.666);
    let md = format_complete_assessment_markdown(&a);
    assert!(
        md.contains("Assessment Confidence:** 67%"),
        "0.666 must render as 67%: {md}"
    );
}

/// A confidence of **zero** still renders, because the guard is `is not None`
/// and not truthiness. Treating it as absent would drop a stated 0% — the
/// strongest possible statement of no confidence.
#[test]
fn a_zero_confidence_is_rendered_not_dropped() {
    let mut a = assessment();
    a.overall_confidence = Some(0.0);
    let md = format_complete_assessment_markdown(&a);
    assert!(md.contains("Assessment Confidence:** 0%"), "{md}");
}

/// A zero quality score renders for the same reason.
#[test]
fn a_zero_quality_score_is_rendered_not_dropped() {
    let mut a = assessment();
    a.overall_quality_score = Some(0.0);
    let md = format_complete_assessment_markdown(&a);
    assert!(md.contains("Quality Score:** 0.0/10"), "{md}");
}

/// The quality score always carries one decimal place, so `8` renders `8.0`.
#[test]
fn the_quality_score_always_has_one_decimal() {
    let mut a = assessment();
    a.overall_quality_score = Some(8.0);
    a.overall_confidence = Some(0.5);
    let md = format_complete_assessment_markdown(&a);
    assert!(md.contains("Quality Score:** 8.0/10"), "{md}");
}

/// The summary block appears when either the score or the evidence level is
/// set, and not otherwise — an assessment with neither gets no heading.
#[test]
fn the_summary_block_appears_only_when_it_has_something() {
    let bare = format_complete_assessment_markdown(&assessment());
    assert!(!bare.contains("Assessment Summary"), "{bare}");

    let mut with_level = assessment();
    with_level.evidence_level = Some("Level 2".to_string());
    assert!(format_complete_assessment_markdown(&with_level).contains("Assessment Summary"));
}

/// The detection-bias rows omit the bias-type suffix because their `domain`
/// text already names the outcome; the other seven append it.
#[test]
fn a_domain_label_omits_its_type_only_when_it_names_an_outcome() {
    let selection =
        create_default_risk_of_bias_item("Random sequence generation", "selection bias", None);
    assert_eq!(
        domain_label(&selection),
        "Random sequence generation (selection bias)"
    );

    let detection = create_default_risk_of_bias_item(
        "Blinding of outcome assessment",
        "detection bias",
        Some("subjective"),
    );
    assert_eq!(domain_label(&detection), "Blinding of outcome assessment");

    // An *empty* outcome type is treated as absent, matching Python's
    // truthiness test, so the suffix comes back.
    let empty = create_default_risk_of_bias_item("D", "detection bias", Some(""));
    assert_eq!(domain_label(&empty), "D (detection bias)");
}

/// The three judgements map to distinct symbols and classes, and **anything
/// unrecognised is unclear** rather than a fourth category.
#[test]
fn the_judgement_symbols_and_classes_are_total() {
    assert_eq!(judgement_symbol(ROB_JUDGEMENT_LOW), "+");
    assert_eq!(judgement_symbol(ROB_JUDGEMENT_HIGH), "-");
    assert_eq!(judgement_symbol(ROB_JUDGEMENT_UNCLEAR), "?");
    assert_eq!(judgement_symbol("nonsense"), "?");

    assert_eq!(judgement_css_class(ROB_JUDGEMENT_LOW), "judgement-low");
    assert_eq!(judgement_css_class(ROB_JUDGEMENT_HIGH), "judgement-high");
    assert_eq!(
        judgement_css_class(ROB_JUDGEMENT_UNCLEAR),
        "judgement-unclear"
    );
    assert_eq!(judgement_css_class("nonsense"), "judgement-unclear");
}

// ---------------------------------------------------------------------------
// Multi-study output
// ---------------------------------------------------------------------------

/// An empty summary says so rather than rendering a table with no columns.
#[test]
fn an_empty_summary_says_so() {
    assert_eq!(
        format_risk_of_bias_summary_markdown(&[]),
        "No assessments to summarize."
    );
}

/// The summary matrix has one **row per domain** and one column per study, so
/// the separator row must have exactly as many cells as the header.
#[test]
fn the_summary_matrix_has_a_column_per_study() {
    let items: Vec<CochraneStudyAssessment> = (1..=3)
        .map(|i| {
            let mut c = chars();
            c.study_id = format!("Study {i}");
            CochraneStudyAssessment::new(c, create_default_cochrane_risk_of_bias())
        })
        .collect();
    let md = format_risk_of_bias_summary_markdown(&items);

    let header = md.lines().find(|l| l.contains("Domain")).expect("header");
    assert_eq!(header, "| Domain | Study 1 | Study 2 | Study 3 |");
    let separator = md
        .lines()
        .find(|l| l.starts_with("|---"))
        .expect("separator");
    assert_eq!(separator, "|---|---|---|---|");

    // Nine domain rows plus the header, separator, blank line, legend and a
    // trailing blank.
    let domain_rows = md
        .lines()
        .filter(|l| l.starts_with("| ") && !l.contains("Domain"))
        .count();
    assert_eq!(domain_rows, 9, "one row per domain: {md}");
}

/// Markdown row labels are not HTML-escaped — the Markdown renderer writes
/// text as-is, and escaping there would put entities in a plain-text artefact.
#[test]
fn the_markdown_renderer_does_not_escape() {
    let mut c = chars();
    c.methods = "<b>bold</b> & 'quoted'".to_string();
    let md = format_study_characteristics_markdown(&c);
    assert!(md.contains("<b>bold</b> & 'quoted'"), "{md}");
    assert!(!md.contains("&lt;b&gt;"));
}

/// The stylesheet is reproduced exactly, leading newline included: a caller
/// may compare or cache it, so reformatting it changes the artefact.
#[test]
fn the_stylesheet_is_reproduced_exactly() {
    assert!(COCHRANE_CSS.starts_with("\n<style>"));
    assert!(COCHRANE_CSS.ends_with("</style>\n"));
    assert_eq!(COCHRANE_CSS.len(), 1146);
    assert_eq!(COCHRANE_CSS.matches('\n').count(), 63);
}
