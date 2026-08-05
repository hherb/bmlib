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

import pytest

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
    collapse_risk_of_bias,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
)
from bmlib.quality.data_models import BiasRisk


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


class TestCollapsingTheNineDomainsOntoTheFive:
    """``collapse_risk_of_bias()`` reduces Cochrane's nine domains to the
    five ``BiasRisk`` speaks, deriving the grouping from each item's own
    ``bias_type`` rather than a hard-coded per-domain table."""

    def test_all_low_collapses_to_all_low(self) -> None:
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW

        assert collapse_risk_of_bias(rob) == BiasRisk(
            selection="low",
            performance="low",
            detection="low",
            attrition="low",
            reporting="low",
        )

    def test_the_worst_judgement_in_a_group_wins(self) -> None:
        """Four domains feed ``selection``; one high makes the group high."""
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW
        rob.allocation_concealment.judgement = ROB_JUDGEMENT_HIGH

        assert collapse_risk_of_bias(rob).selection == "high"

    def test_unclear_outranks_low(self) -> None:
        """An unreported domain is not a clean bill of health: you cannot
        claim low selection-bias risk when allocation concealment was never
        described."""
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW
        rob.allocation_concealment.judgement = ROB_JUDGEMENT_UNCLEAR

        assert collapse_risk_of_bias(rob).selection == "unclear"

    def test_high_outranks_unclear(self) -> None:
        rob = create_default_cochrane_risk_of_bias()
        rob.random_sequence_generation.judgement = ROB_JUDGEMENT_HIGH
        # The other three selection domains are Unclear by default.

        assert collapse_risk_of_bias(rob).selection == "high"

    def test_the_two_detection_domains_collapse_together(self) -> None:
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW
        rob.blinding_outcome_assessment_subjective.judgement = ROB_JUDGEMENT_HIGH

        collapsed = collapse_risk_of_bias(rob)
        assert collapsed.detection == "high"
        # ...and nothing else moved with it.
        assert collapsed.performance == "low"
        assert collapsed.attrition == "low"

    def test_a_judgement_in_another_casing_still_collapses(self) -> None:
        """A hand-built item carrying "low" rather than "Low risk" is
        normalised through ``RiskOfBiasJudgement.from_string()`` instead of
        being counted as unclear."""
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = "low"

        assert collapse_risk_of_bias(rob).selection == "low"

    def test_a_bias_type_in_another_casing_still_collapses(self) -> None:
        """``item.bias_type.strip().lower()`` normalises casing before the
        lookup; until this test, nothing exercised it with a value that was
        not already lowercase, so the normalisation was dead code."""
        rob = create_default_cochrane_risk_of_bias()
        rob.random_sequence_generation.judgement = ROB_JUDGEMENT_HIGH
        rob.random_sequence_generation.bias_type = "Selection Bias"

        assert collapse_risk_of_bias(rob).selection == "high"

    def test_an_unrecognised_bias_type_raises(self) -> None:
        """``RiskOfBiasItem`` is public and a caller may build one with any
        ``bias_type``.  Dropping it silently would emit a ``BiasRisk`` that
        looks complete and is not — the same reason
        ``_Analysis.note_data_level()`` raises on an unknown level."""
        rob = create_default_cochrane_risk_of_bias()
        rob.selective_reporting.bias_type = "funding bias"

        with pytest.raises(ValueError, match="funding bias"):
            collapse_risk_of_bias(rob)

    def test_every_default_domain_maps_to_a_domain_the_collapse_knows(self) -> None:
        """The guard above must never fire for bmlib's own nine domains."""
        collapse_risk_of_bias(create_default_cochrane_risk_of_bias())


class TestTheCondensationProvenanceField:
    def test_it_defaults_to_none(self) -> None:
        """``None`` means the paper went to the model whole."""
        assert _sample_assessment().condensed_from_chars is None

    def test_it_round_trips(self) -> None:
        assessment = _sample_assessment()
        assessment.condensed_from_chars = 91_234

        restored = CochraneStudyAssessment.from_dict(assessment.to_dict())
        assert restored.condensed_from_chars == 91_234

    def test_a_dict_without_the_key_loads_as_none(self) -> None:
        data = _sample_assessment().to_dict()
        del data["condensed_from_chars"]

        assert CochraneStudyAssessment.from_dict(data).condensed_from_chars is None


class TestTheCondensationStatusField:
    """``condensation_status`` is the machine-readable counterpart of the
    prose note ``_condense`` appends to ``assessment_notes``: the
    ``ProcessingStatus`` value the condensation pass finished with, or
    ``None`` when the text was never condensed."""

    def test_it_defaults_to_none(self) -> None:
        """``None`` means the paper went to the model whole."""
        assert _sample_assessment().condensation_status is None

    def test_it_round_trips(self) -> None:
        assessment = _sample_assessment()
        assessment.condensation_status = "partial"

        restored = CochraneStudyAssessment.from_dict(assessment.to_dict())
        assert restored.condensation_status == "partial"

    def test_a_dict_without_the_key_loads_as_none(self) -> None:
        data = _sample_assessment().to_dict()
        del data["condensation_status"]

        assert CochraneStudyAssessment.from_dict(data).condensation_status is None
