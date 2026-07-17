# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Cochrane-style Markdown and HTML formatters.

Render :mod:`bmlib.quality.cochrane_models` assessments as the
study-characteristics and risk-of-bias tables from the Cochrane Handbook
template, in Markdown or HTML.
"""

from __future__ import annotations

from bmlib.quality.cochrane_models import (
    ROB_JUDGEMENT_HIGH,
    ROB_JUDGEMENT_LOW,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
)

# Markdown formatting tokens.
MD_BOLD_START = "**"
MD_BOLD_END = "**"
MD_ITALIC_START = "*"
MD_ITALIC_END = "*"


# ---------------------------------------------------------------------------
# Markdown formatters
# ---------------------------------------------------------------------------


def format_study_characteristics_markdown(study_chars: CochraneStudyCharacteristics) -> str:
    """Format a study-characteristics table as Markdown."""
    lines: list[str] = []

    lines.append(f"### {study_chars.study_id}")
    lines.append("")
    lines.append(f"{MD_ITALIC_START}Study characteristics{MD_ITALIC_END}")
    lines.append("")

    lines.append(
        f"| {MD_BOLD_START}Characteristic{MD_BOLD_END} | {MD_BOLD_START}Description{MD_BOLD_END} |"
    )
    lines.append("|---|---|")

    lines.append(f"| Methods | {study_chars.methods} |")

    # Participants may render across multiple lines.
    participant_lines = study_chars.participants.format_for_table().split("\n")
    first_line = True
    for p_line in participant_lines:
        if p_line.strip():
            if first_line:
                lines.append(f"| Participants | {p_line} |")
                first_line = False
            else:
                lines.append(f"| | {p_line} |")

    lines.append(f"| Interventions | {study_chars.interventions.description} |")
    lines.append(f"| Outcomes | {study_chars.outcomes.description} |")

    # Notes may render across multiple blocks.
    notes_lines = study_chars.notes.format_for_table().split("\n\n")
    first_line = True
    for n_line in notes_lines:
        if n_line.strip():
            if first_line:
                lines.append(f"| Notes | {n_line} |")
                first_line = False
            else:
                lines.append(f"| | {n_line} |")

    lines.append("")
    return "\n".join(lines)


def format_risk_of_bias_markdown(rob: CochraneRiskOfBias) -> str:
    """Format a risk-of-bias assessment as a Markdown table."""
    lines: list[str] = []

    lines.append(f"{MD_ITALIC_START}Risk of bias{MD_ITALIC_END}")
    lines.append("")
    lines.append(
        f"| {MD_BOLD_START}Bias{MD_BOLD_END} "
        f"| {MD_BOLD_START}Authors' judgement{MD_BOLD_END} "
        f"| {MD_BOLD_START}Support for judgement{MD_BOLD_END} |"
    )
    lines.append("|---|---|---|")

    for item in rob.to_list():
        domain_with_type = f"{item.domain} ({item.bias_type})"
        if item.outcome_type:
            domain_with_type = f"{item.domain}"
        lines.append(f"| {domain_with_type} | {item.judgement} | {item.support_for_judgement} |")

    lines.append("")
    return "\n".join(lines)


def format_complete_assessment_markdown(assessment: CochraneStudyAssessment) -> str:
    """Format a full assessment (characteristics + RoB + summary) as Markdown."""
    lines: list[str] = []

    lines.append(format_study_characteristics_markdown(assessment.study_characteristics))
    lines.append(format_risk_of_bias_markdown(assessment.risk_of_bias))

    if assessment.overall_quality_score is not None or assessment.evidence_level:
        lines.append(f"{MD_ITALIC_START}Assessment Summary{MD_ITALIC_END}")
        lines.append("")
        if assessment.overall_quality_score is not None:
            lines.append(f"- **Quality Score:** {assessment.overall_quality_score:.1f}/10")
        if assessment.overall_confidence is not None:
            lines.append(f"- **Assessment Confidence:** {assessment.overall_confidence:.0%}")
        if assessment.evidence_level:
            lines.append(f"- **Evidence Level:** {assessment.evidence_level}")
        lines.append("")

    if assessment.assessment_notes:
        lines.append(f"{MD_ITALIC_START}Notes{MD_ITALIC_END}")
        lines.append("")
        for note in assessment.assessment_notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)


def format_multiple_assessments_markdown(
    assessments: list[CochraneStudyAssessment],
    title: str = "Characteristics of included studies",
) -> str:
    """Format several assessments as one "Characteristics of included studies" doc."""
    lines: list[str] = []

    lines.append(f"## {title}")
    lines.append("")
    for assessment in assessments:
        lines.append(format_complete_assessment_markdown(assessment))
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Risk of bias summary
# ---------------------------------------------------------------------------


def format_risk_of_bias_summary_markdown(assessments: list[CochraneStudyAssessment]) -> str:
    """Format a cross-study risk-of-bias summary matrix as Markdown."""
    lines: list[str] = []

    if not assessments:
        return "No assessments to summarize."

    lines.append("## Risk of Bias Summary")
    lines.append("")

    domains = [
        "Random sequence generation (selection bias)",
        "Allocation concealment (selection bias)",
        "Baseline outcome measurements (selection bias)",
        "Baseline characteristics (selection bias)",
        "Blinding of participants and personnel (performance bias)",
        "Blinding of outcome assessment - subjective (detection bias)",
        "Blinding of outcome assessment - objective (detection bias)",
        "Incomplete outcome data (attrition bias)",
        "Selective reporting (reporting bias)",
    ]

    study_ids = [a.study_id for a in assessments]
    lines.append("| Domain | " + " | ".join(study_ids) + " |")
    lines.append("|---" + "|---" * len(study_ids) + "|")

    domain_attrs = [
        "random_sequence_generation",
        "allocation_concealment",
        "baseline_outcome_measurements",
        "baseline_characteristics",
        "blinding_participants_personnel",
        "blinding_outcome_assessment_subjective",
        "blinding_outcome_assessment_objective",
        "incomplete_outcome_data",
        "selective_reporting",
    ]

    for domain, attr in zip(domains, domain_attrs):
        judgements = [
            _format_judgement_symbol(getattr(a.risk_of_bias, attr).judgement) for a in assessments
        ]
        lines.append(f"| {domain} | " + " | ".join(judgements) + " |")

    lines.append("")
    lines.append("**Legend:** + Low risk | - High risk | ? Unclear risk")
    lines.append("")

    return "\n".join(lines)


def _format_judgement_symbol(judgement: str) -> str:
    """Map a judgement to its summary-table symbol (+, -, ?)."""
    if judgement == ROB_JUDGEMENT_LOW:
        return "+"
    if judgement == ROB_JUDGEMENT_HIGH:
        return "-"
    return "?"


# ---------------------------------------------------------------------------
# HTML formatters
# ---------------------------------------------------------------------------


def format_study_characteristics_html(study_chars: CochraneStudyCharacteristics) -> str:
    """Format a study-characteristics table as HTML (style with the CSS)."""
    html_parts: list[str] = []

    html_parts.append(f'<h3 class="study-id">{_escape_html(study_chars.study_id)}</h3>')
    html_parts.append('<p class="section-header"><em>Study characteristics</em></p>')

    html_parts.append('<table class="cochrane-characteristics">')
    html_parts.append("<thead>")
    html_parts.append("<tr><th>Characteristic</th><th>Description</th></tr>")
    html_parts.append("</thead>")
    html_parts.append("<tbody>")

    html_parts.append(f"<tr><td>Methods</td><td>{_escape_html(study_chars.methods)}</td></tr>")

    participants_text = study_chars.participants.format_for_table()
    html_parts.append(
        f"<tr><td>Participants</td><td>"
        f"{_escape_html(participants_text).replace(chr(10), '<br>')}</td></tr>"
    )

    html_parts.append(
        f"<tr><td>Interventions</td><td>"
        f"{_escape_html(study_chars.interventions.description)}</td></tr>"
    )
    html_parts.append(
        f"<tr><td>Outcomes</td><td>{_escape_html(study_chars.outcomes.description)}</td></tr>"
    )

    notes_text = study_chars.notes.format_for_table()
    html_parts.append(
        f"<tr><td>Notes</td><td>{_escape_html(notes_text).replace(chr(10), '<br>')}</td></tr>"
    )

    html_parts.append("</tbody>")
    html_parts.append("</table>")

    return "\n".join(html_parts)


def format_risk_of_bias_html(rob: CochraneRiskOfBias) -> str:
    """Format a risk-of-bias assessment as an HTML table."""
    html_parts: list[str] = []

    html_parts.append('<p class="section-header"><em>Risk of bias</em></p>')

    html_parts.append('<table class="cochrane-risk-of-bias">')
    html_parts.append("<thead>")
    html_parts.append(
        "<tr><th>Bias</th><th>Authors' judgement</th><th>Support for judgement</th></tr>"
    )
    html_parts.append("</thead>")
    html_parts.append("<tbody>")

    for item in rob.to_list():
        judgement_class = _get_judgement_css_class(item.judgement)
        domain_text = f"{item.domain} ({item.bias_type})"
        html_parts.append(
            "<tr>"
            f"<td>{_escape_html(domain_text)}</td>"
            f'<td class="{judgement_class}">{_escape_html(item.judgement)}</td>'
            f"<td>{_escape_html(item.support_for_judgement)}</td>"
            "</tr>"
        )

    html_parts.append("</tbody>")
    html_parts.append("</table>")

    return "\n".join(html_parts)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _get_judgement_css_class(judgement: str) -> str:
    """Return the CSS class name for a judgement."""
    if judgement == ROB_JUDGEMENT_LOW:
        return "judgement-low"
    if judgement == ROB_JUDGEMENT_HIGH:
        return "judgement-high"
    return "judgement-unclear"


# ---------------------------------------------------------------------------
# CSS for HTML output
# ---------------------------------------------------------------------------

COCHRANE_CSS = """
<style>
.cochrane-characteristics, .cochrane-risk-of-bias {
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 1.5em;
    font-size: 0.9em;
}

.cochrane-characteristics th, .cochrane-characteristics td,
.cochrane-risk-of-bias th, .cochrane-risk-of-bias td {
    border: 1px solid #ccc;
    padding: 8px 12px;
    text-align: left;
    vertical-align: top;
}

.cochrane-characteristics th, .cochrane-risk-of-bias th {
    background-color: #f5f5f5;
    font-weight: bold;
}

.cochrane-characteristics td:first-child {
    font-weight: bold;
    width: 150px;
    background-color: #fafafa;
}

.cochrane-risk-of-bias td:first-child {
    width: 35%;
}

.cochrane-risk-of-bias td:nth-child(2) {
    width: 15%;
    text-align: center;
}

.section-header {
    margin-top: 1em;
    margin-bottom: 0.5em;
}

.study-id {
    margin-top: 1.5em;
    padding-bottom: 0.5em;
    border-bottom: 2px solid #333;
}

.judgement-low {
    background-color: #d4edda;
    color: #155724;
}

.judgement-high {
    background-color: #f8d7da;
    color: #721c24;
}

.judgement-unclear {
    background-color: #fff3cd;
    color: #856404;
}
</style>
"""


def get_cochrane_css() -> str:
    """Return the CSS stylesheet for Cochrane HTML output."""
    return COCHRANE_CSS
