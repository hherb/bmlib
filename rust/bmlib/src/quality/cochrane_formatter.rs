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

//! Markdown and HTML renderers for the Cochrane tables.
//!
//! A port of `bmlib/quality/cochrane_formatter.py`. Two renderers over the
//! same models: Markdown for text output, HTML for the styled report, sharing
//! [`domain_label`] so both produce identical row labels.
//!
//! # The two escaping rules are not the same, and neither is optional
//!
//! **HTML escaping happens before newline-to-`<br>` conversion**, never after.
//! The order looks interchangeable and is not: `.replace("\n", "<br>")` first
//! would introduce `<` characters that `_escape_html` then turns into `&lt;`,
//! rendering the breaks as literal text. Python happens to write it in the safe
//! order inside one f-string, and this port keeps that order explicitly.
//!
//! **A bracketed date range in the notes is left as written.** The formatter
//! does not "fix" punctuation the deposit carries; a downstream or the source
//! owns that.

use crate::quality::cochrane_models::{
    CochraneRiskOfBias, CochraneStudyAssessment, CochraneStudyCharacteristics, RiskOfBiasItem,
    ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_LOW,
};

/// Markdown bold start.
pub const MD_BOLD_START: &str = "**";
/// Markdown bold end.
pub const MD_BOLD_END: &str = "**";
/// Markdown italic start.
pub const MD_ITALIC_START: &str = "*";
/// Markdown italic end.
pub const MD_ITALIC_END: &str = "*";

/// The table label for a risk-of-bias domain.
///
/// Detection-bias domains are split by outcome type and already name the
/// outcome in their `domain` text, so the bias-type suffix is omitted for them.
/// Shared by both renderers so the Markdown and HTML tables agree.
#[must_use]
pub fn domain_label(item: &RiskOfBiasItem) -> String {
    match item.outcome_type.as_ref().filter(|s| !s.is_empty()) {
        Some(_) => item.domain.clone(),
        None => format!("{} ({})", item.domain, item.bias_type),
    }
}

/// Map a judgement to its summary-table symbol.
#[must_use]
pub fn judgement_symbol(judgement: &str) -> &'static str {
    if judgement == ROB_JUDGEMENT_LOW {
        "+"
    } else if judgement == ROB_JUDGEMENT_HIGH {
        "-"
    } else {
        "?"
    }
}

/// The CSS class name for a judgement.
#[must_use]
pub fn judgement_css_class(judgement: &str) -> &'static str {
    if judgement == ROB_JUDGEMENT_LOW {
        "judgement-low"
    } else if judgement == ROB_JUDGEMENT_HIGH {
        "judgement-high"
    } else {
        "judgement-unclear"
    }
}

/// Escape HTML special characters, **ampersand first**.
///
/// The order is what makes it correct: escaping `<` before `&` would leave the
/// `&lt;` it produced to be escaped again into `&amp;lt;`.
#[must_use]
pub fn escape_html(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

/// Escape, then turn newlines into `<br>` — in that order, deliberately.
///
/// See the module docs: the reverse turns the breaks into text.
fn escape_then_breaks(text: &str) -> String {
    escape_html(text).replace('\n', "<br>")
}

// ---------------------------------------------------------------------------
// Markdown
// ---------------------------------------------------------------------------

/// Format a study-characteristics table as Markdown.
#[must_use]
pub fn format_study_characteristics_markdown(study_chars: &CochraneStudyCharacteristics) -> String {
    let mut lines: Vec<String> = Vec::new();

    lines.push(format!("### {}", study_chars.study_id));
    lines.push(String::new());
    lines.push(format!(
        "{MD_ITALIC_START}Study characteristics{MD_ITALIC_END}"
    ));
    lines.push(String::new());

    lines.push(format!(
        "| {MD_BOLD_START}Characteristic{MD_BOLD_END} | {MD_BOLD_START}Description{MD_BOLD_END} |"
    ));
    lines.push("|---|---|".to_string());

    lines.push(format!("| Methods | {} |", study_chars.methods));

    // Participants may render across multiple lines; only the first carries
    // the row label, so the rest are continuation rows.
    let mut first = true;
    for p_line in study_chars.participants.format_for_table().split('\n') {
        if p_line.trim().is_empty() {
            continue;
        }
        if first {
            lines.push(format!("| Participants | {p_line} |"));
            first = false;
        } else {
            lines.push(format!("| | {p_line} |"));
        }
    }

    lines.push(format!(
        "| Interventions | {} |",
        study_chars.interventions.description
    ));
    lines.push(format!(
        "| Outcomes | {} |",
        study_chars.outcomes.description
    ));

    // Notes render across blank-line-separated blocks.
    let mut first = true;
    for n_line in study_chars.notes.format_for_table().split("\n\n") {
        if n_line.trim().is_empty() {
            continue;
        }
        if first {
            lines.push(format!("| Notes | {n_line} |"));
            first = false;
        } else {
            lines.push(format!("| | {n_line} |"));
        }
    }

    lines.push(String::new());
    lines.join("\n")
}

/// Format a risk-of-bias assessment as a Markdown table.
#[must_use]
pub fn format_risk_of_bias_markdown(rob: &CochraneRiskOfBias) -> String {
    let mut lines: Vec<String> = Vec::new();

    lines.push(format!("{MD_ITALIC_START}Risk of bias{MD_ITALIC_END}"));
    lines.push(String::new());
    lines.push(format!(
        "| {MD_BOLD_START}Bias{MD_BOLD_END} \
         | {MD_BOLD_START}Authors' judgement{MD_BOLD_END} \
         | {MD_BOLD_START}Support for judgement{MD_BOLD_END} |"
    ));
    lines.push("|---|---|---|".to_string());

    for item in rob.to_list() {
        lines.push(format!(
            "| {} | {} | {} |",
            domain_label(item),
            item.judgement,
            item.support_for_judgement
        ));
    }

    lines.push(String::new());
    lines.join("\n")
}

/// Format a full assessment — characteristics, RoB, and summary — as Markdown.
#[must_use]
pub fn format_complete_assessment_markdown(assessment: &CochraneStudyAssessment) -> String {
    let mut lines: Vec<String> = Vec::new();

    lines.push(format_study_characteristics_markdown(
        &assessment.study_characteristics,
    ));
    lines.push(format_risk_of_bias_markdown(&assessment.risk_of_bias));

    // Corrected from Python, which tests only `overall_quality_score is not
    // None or assessment.evidence_level`. `overall_confidence` is rendered by
    // the block but absent from the guard, so a confidence set on its own is
    // dropped entirely: the caller states 80% certainty and the output says
    // nothing. Nothing caught it because every fixture in the Python suite sets
    // a score or an evidence level alongside the confidence, which enters the
    // block and renders the line.
    //
    // The guard now asks what the block actually requires, which is what the
    // three renderable fields have in common.
    if assessment.overall_quality_score.is_some()
        || assessment.overall_confidence.is_some()
        || assessment.evidence_level.is_some()
    {
        lines.push(format!(
            "{MD_ITALIC_START}Assessment Summary{MD_ITALIC_END}"
        ));
        lines.push(String::new());
        if let Some(score) = assessment.overall_quality_score {
            // Python's `:.1f` — one decimal place, always.
            lines.push(format!("- **Quality Score:** {score:.1}/10"));
        }
        if let Some(confidence) = assessment.overall_confidence {
            // Python's `:.0%` — a percentage with no decimals. `{:.0}` on a
            // 0–1 value would be off by two orders of magnitude.
            lines.push(format!(
                "- **Assessment Confidence:** {:.0}%",
                confidence * 100.0
            ));
        }
        if let Some(level) = &assessment.evidence_level {
            lines.push(format!("- **Evidence Level:** {level}"));
        }
        lines.push(String::new());
    }

    if let Some(notes) = assessment
        .assessment_notes
        .as_ref()
        .filter(|n| !n.is_empty())
    {
        lines.push(format!("{MD_ITALIC_START}Notes{MD_ITALIC_END}"));
        lines.push(String::new());
        for note in notes {
            lines.push(format!("- {note}"));
        }
        lines.push(String::new());
    }

    lines.join("\n")
}

/// Format several assessments as one "Characteristics of included studies" doc.
#[must_use]
pub fn format_multiple_assessments_markdown(
    assessments: &[CochraneStudyAssessment],
    title: &str,
) -> String {
    let mut lines: Vec<String> = Vec::new();

    lines.push(format!("## {title}"));
    lines.push(String::new());
    for assessment in assessments {
        lines.push(format_complete_assessment_markdown(assessment));
        lines.push("---".to_string());
        lines.push(String::new());
    }

    lines.join("\n")
}

/// Format a cross-study risk-of-bias summary matrix as Markdown.
#[must_use]
pub fn format_risk_of_bias_summary_markdown(assessments: &[CochraneStudyAssessment]) -> String {
    if assessments.is_empty() {
        return "No assessments to summarize.".to_string();
    }

    let mut lines: Vec<String> = Vec::new();
    lines.push("## Risk of Bias Summary".to_string());
    lines.push(String::new());

    let study_ids: Vec<&str> = assessments.iter().map(|a| a.study_id()).collect();
    lines.push(format!("| Domain | {} |", study_ids.join(" | ")));
    lines.push(format!("|---{}|", "|---".repeat(study_ids.len())));

    // Every assessment's `to_list()` yields the nine domains in canonical
    // Cochrane order, so transposing gives one matrix row per domain. The
    // ninth row is reached by index rather than by iterating a zipped
    // iterator, which in Rust would silently stop at the shortest.
    let rows: Vec<Vec<&RiskOfBiasItem>> = assessments
        .iter()
        .map(|a| a.risk_of_bias.to_list())
        .collect();
    for index in 0..9 {
        let Some(first_item) = rows.first().and_then(|r| r.get(index)) else {
            break;
        };
        let label = domain_label(first_item);
        let judgements: Vec<&str> = rows
            .iter()
            .filter_map(|r| r.get(index))
            .map(|item| judgement_symbol(&item.judgement))
            .collect();
        lines.push(format!("| {label} | {} |", judgements.join(" | ")));
    }

    lines.push(String::new());
    lines.push("**Legend:** + Low risk | - High risk | ? Unclear risk".to_string());
    lines.push(String::new());

    lines.join("\n")
}

// ---------------------------------------------------------------------------
// HTML
// ---------------------------------------------------------------------------

/// Format a study-characteristics table as HTML.
#[must_use]
pub fn format_study_characteristics_html(study_chars: &CochraneStudyCharacteristics) -> String {
    let mut parts: Vec<String> = Vec::new();

    parts.push(format!(
        "<h3 class=\"study-id\">{}</h3>",
        escape_html(&study_chars.study_id)
    ));
    parts.push("<p class=\"section-header\"><em>Study characteristics</em></p>".to_string());

    parts.push("<table class=\"cochrane-characteristics\">".to_string());
    parts.push("<thead>".to_string());
    parts.push("<tr><th>Characteristic</th><th>Description</th></tr>".to_string());
    parts.push("</thead>".to_string());
    parts.push("<tbody>".to_string());

    parts.push(format!(
        "<tr><td>Methods</td><td>{}</td></tr>",
        escape_html(&study_chars.methods)
    ));
    parts.push(format!(
        "<tr><td>Participants</td><td>{}</td></tr>",
        escape_then_breaks(&study_chars.participants.format_for_table())
    ));
    parts.push(format!(
        "<tr><td>Interventions</td><td>{}</td></tr>",
        escape_html(&study_chars.interventions.description)
    ));
    parts.push(format!(
        "<tr><td>Outcomes</td><td>{}</td></tr>",
        escape_html(&study_chars.outcomes.description)
    ));
    parts.push(format!(
        "<tr><td>Notes</td><td>{}</td></tr>",
        escape_then_breaks(&study_chars.notes.format_for_table())
    ));

    parts.push("</tbody>".to_string());
    parts.push("</table>".to_string());

    parts.join("\n")
}

/// Format a risk-of-bias assessment as an HTML table.
#[must_use]
pub fn format_risk_of_bias_html(rob: &CochraneRiskOfBias) -> String {
    let mut parts: Vec<String> = vec![
        "<p class=\"section-header\"><em>Risk of bias</em></p>".to_string(),
        "<table class=\"cochrane-risk-of-bias\">".to_string(),
        "<thead>".to_string(),
        "<tr><th>Bias</th><th>Authors' judgement</th><th>Support for judgement</th></tr>"
            .to_string(),
        "</thead>".to_string(),
        "<tbody>".to_string(),
    ];

    for item in rob.to_list() {
        parts.push(format!(
            "<tr><td>{}</td><td class=\"{}\">{}</td><td>{}</td></tr>",
            escape_html(&domain_label(item)),
            judgement_css_class(&item.judgement),
            escape_html(&item.judgement),
            escape_html(&item.support_for_judgement)
        ));
    }

    parts.push("</tbody>".to_string());
    parts.push("</table>".to_string());

    parts.join("\n")
}

/// The CSS stylesheet for Cochrane HTML output.
///
/// Reproduced byte for byte, including its leading newline: the string is
/// embedded in a page and a caller may compare or cache it, so reformatting it
/// would be a change to the artefact rather than to the code.
pub const COCHRANE_CSS: &str = "\n<style>\n.cochrane-characteristics, .cochrane-risk-of-bias {\n    border-collapse: collapse;\n    width: 100%;\n    margin-bottom: 1.5em;\n    font-size: 0.9em;\n}\n\n.cochrane-characteristics th, .cochrane-characteristics td,\n.cochrane-risk-of-bias th, .cochrane-risk-of-bias td {\n    border: 1px solid #ccc;\n    padding: 8px 12px;\n    text-align: left;\n    vertical-align: top;\n}\n\n.cochrane-characteristics th, .cochrane-risk-of-bias th {\n    background-color: #f5f5f5;\n    font-weight: bold;\n}\n\n.cochrane-characteristics td:first-child {\n    font-weight: bold;\n    width: 150px;\n    background-color: #fafafa;\n}\n\n.cochrane-risk-of-bias td:first-child {\n    width: 35%;\n}\n\n.cochrane-risk-of-bias td:nth-child(2) {\n    width: 15%;\n    text-align: center;\n}\n\n.section-header {\n    margin-top: 1em;\n    margin-bottom: 0.5em;\n}\n\n.study-id {\n    margin-top: 1.5em;\n    padding-bottom: 0.5em;\n    border-bottom: 2px solid #333;\n}\n\n.judgement-low {\n    background-color: #d4edda;\n    color: #155724;\n}\n\n.judgement-high {\n    background-color: #f8d7da;\n    color: #721c24;\n}\n\n.judgement-unclear {\n    background-color: #fff3cd;\n    color: #856404;\n}\n</style>\n";

/// The CSS stylesheet for Cochrane HTML output.
#[must_use]
pub fn get_cochrane_css() -> &'static str {
    COCHRANE_CSS
}
