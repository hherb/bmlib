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

"""Tests for bmlib.quality Cochrane models and formatters."""

from __future__ import annotations

from bmlib.quality.cochrane_formatter import (
    format_complete_assessment_markdown,
    format_multiple_assessments_markdown,
    format_risk_of_bias_html,
    format_risk_of_bias_markdown,
    format_risk_of_bias_summary_markdown,
    format_study_characteristics_html,
    format_study_characteristics_markdown,
    get_cochrane_css,
)
from bmlib.quality.cochrane_models import (
    ROB_JUDGEMENT_HIGH,
    ROB_JUDGEMENT_LOW,
    ROB_JUDGEMENT_UNCLEAR,
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
)


def _sample_assessment(study_id: str = "Andrei 2011") -> CochraneStudyAssessment:
    chars = CochraneStudyCharacteristics(
        study_id=study_id,
        methods="Parallel randomised trial",
        participants=CochraneParticipants(
            setting="Romania",
            population="People with chronic heart failure",
            total_participants=45,
            group_sizes={"intervention": 25, "control": 20},
        ),
        interventions=CochraneInterventions(description="Hospital at home"),
        outcomes=CochraneOutcomes(description="Mortality, cost"),
        notes=CochraneNotes(funding_source="University grant"),
    )
    rob = create_default_cochrane_risk_of_bias()
    rob.random_sequence_generation = RiskOfBiasItem(
        domain="Random sequence generation",
        bias_type="selection bias",
        judgement=ROB_JUDGEMENT_LOW,
        support_for_judgement="Computer-generated randomisation",
    )
    rob.selective_reporting = RiskOfBiasItem(
        domain="Selective reporting",
        bias_type="reporting bias",
        judgement=ROB_JUDGEMENT_HIGH,
        support_for_judgement="Protocol not available",
    )
    return CochraneStudyAssessment(
        study_characteristics=chars,
        risk_of_bias=rob,
        overall_quality_score=6.5,
        overall_confidence=0.8,
        evidence_level="Level 2",
    )


class TestRiskOfBiasJudgement:
    def test_from_string_variations(self):
        assert RiskOfBiasJudgement.from_string("low") is RiskOfBiasJudgement.LOW
        assert RiskOfBiasJudgement.from_string("HIGH RISK") is RiskOfBiasJudgement.HIGH
        assert RiskOfBiasJudgement.from_string("unclear_risk") is RiskOfBiasJudgement.UNCLEAR

    def test_from_string_unknown_defaults_unclear(self):
        assert RiskOfBiasJudgement.from_string("nonsense") is RiskOfBiasJudgement.UNCLEAR


class TestRiskOfBiasItem:
    def test_to_dict_omits_none_outcome_type(self):
        item = RiskOfBiasItem("D", "selection bias", ROB_JUDGEMENT_LOW, "reason")
        assert "outcome_type" not in item.to_dict()

    def test_to_dict_includes_outcome_type(self):
        item = RiskOfBiasItem(
            "D", "detection bias", ROB_JUDGEMENT_LOW, "reason", outcome_type="subjective"
        )
        assert item.to_dict()["outcome_type"] == "subjective"

    def test_round_trip(self):
        item = RiskOfBiasItem("D", "selection bias", ROB_JUDGEMENT_HIGH, "reason")
        assert RiskOfBiasItem.from_dict(item.to_dict()) == item


class TestDefaults:
    def test_default_item_is_unclear(self):
        item = create_default_risk_of_bias_item("D", "selection bias")
        assert item.judgement == ROB_JUDGEMENT_UNCLEAR

    def test_default_rob_has_nine_domains_all_unclear(self):
        rob = create_default_cochrane_risk_of_bias()
        items = rob.to_list()
        assert len(items) == 9
        assert all(i.judgement == ROB_JUDGEMENT_UNCLEAR for i in items)

    def test_summary_counts(self):
        rob = create_default_cochrane_risk_of_bias()
        counts = rob.get_summary_counts()
        assert counts[ROB_JUDGEMENT_UNCLEAR] == 9
        assert counts[ROB_JUDGEMENT_LOW] == 0


class TestCochraneRiskOfBias:
    def test_round_trip(self):
        rob = create_default_cochrane_risk_of_bias()
        assert CochraneRiskOfBias.from_dict(rob.to_dict()).to_dict() == rob.to_dict()

    def test_to_list_order_starts_with_random_sequence(self):
        rob = create_default_cochrane_risk_of_bias()
        assert rob.to_list()[0].domain == "Random sequence generation"


class TestStudyCharacteristics:
    def test_post_init_sets_created_at(self):
        chars = _sample_assessment().study_characteristics
        assert chars.created_at is not None

    def test_round_trip(self):
        chars = _sample_assessment().study_characteristics
        restored = CochraneStudyCharacteristics.from_dict(chars.to_dict())
        assert restored.study_id == chars.study_id
        assert restored.participants.total_participants == 45

    def test_participants_format_for_table_includes_group_sizes(self):
        chars = _sample_assessment().study_characteristics
        formatted = chars.participants.format_for_table()
        assert "N=45" in formatted
        assert "intervention: 25" in formatted

    def test_notes_format_for_table(self):
        notes = CochraneNotes(funding_source="NIH", trial_registration="NCT01234567")
        formatted = notes.format_for_table()
        assert "Funding: NIH" in formatted
        assert "Trial registration: NCT01234567" in formatted


class TestCochraneStudyAssessment:
    def test_round_trip(self):
        assessment = _sample_assessment()
        restored = CochraneStudyAssessment.from_dict(assessment.to_dict())
        assert restored.study_id == "Andrei 2011"
        assert restored.overall_quality_score == 6.5

    def test_convenience_properties(self):
        assessment = _sample_assessment()
        assert assessment.study_id == "Andrei 2011"
        assert assessment.document_id is None


class TestMarkdownFormatters:
    def test_study_characteristics_markdown_has_header_and_methods(self):
        md = format_study_characteristics_markdown(_sample_assessment().study_characteristics)
        assert "### Andrei 2011" in md
        assert "| Methods | Parallel randomised trial |" in md

    def test_risk_of_bias_markdown_lists_all_domains(self):
        md = format_risk_of_bias_markdown(_sample_assessment().risk_of_bias)
        # 9 domain rows + header + separator.
        assert md.count("\n|") >= 9
        assert "Random sequence generation" in md

    def test_complete_assessment_includes_summary(self):
        md = format_complete_assessment_markdown(_sample_assessment())
        assert "Quality Score:" in md
        assert "Evidence Level:" in md

    def test_multiple_assessments_has_title_and_separator(self):
        md = format_multiple_assessments_markdown(
            [_sample_assessment("A 2011"), _sample_assessment("B 2012")],
            title="Characteristics of included studies",
        )
        assert "## Characteristics of included studies" in md
        assert "---" in md

    def test_summary_uses_judgement_symbols(self):
        md = format_risk_of_bias_summary_markdown([_sample_assessment()])
        assert "+" in md  # low risk
        assert "-" in md  # high risk
        assert "Legend" in md

    def test_summary_empty(self):
        assert "No assessments" in format_risk_of_bias_summary_markdown([])

    def test_summary_labels_derived_from_items(self):
        md = format_risk_of_bias_summary_markdown([_sample_assessment()])
        assert "Random sequence generation (selection bias)" in md
        # Detection-bias domains already name the outcome type in the domain.
        assert "Blinding of outcome assessment (subjective outcomes)" in md
        assert "(subjective outcomes) (detection bias)" not in md


class TestHtmlFormatters:
    def test_study_characteristics_html_escapes(self):
        chars = _sample_assessment().study_characteristics
        chars.methods = "a < b & c"
        html = format_study_characteristics_html(chars)
        assert "&lt;" in html
        assert "&amp;" in html
        assert "<table" in html

    def test_risk_of_bias_html_has_judgement_class(self):
        html = format_risk_of_bias_html(_sample_assessment().risk_of_bias)
        assert "judgement-low" in html
        assert "judgement-high" in html

    def test_markdown_and_html_domain_labels_agree(self):
        # Both renderers must label detection-bias domains identically:
        # the domain text alone, without a redundant bias-type suffix.
        rob = _sample_assessment().risk_of_bias
        md = format_risk_of_bias_markdown(rob)
        html = format_risk_of_bias_html(rob)
        label = "Blinding of outcome assessment (subjective outcomes)"
        assert label in md
        assert label in html
        assert f"{label} (detection bias)" not in md
        assert f"{label} (detection bias)" not in html

    def test_css_returned(self):
        assert ".cochrane-risk-of-bias" in get_cochrane_css()
