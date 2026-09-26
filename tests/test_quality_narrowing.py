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

"""A reader of model JSON narrows every value to the type it is annotated with.

Issues #295, #310, #312, #317, #318, #319 and #320, filed by the Rust port's
audit: one rule, that ``data.get(k, default)`` returns its default only for an
**absent** key, so a present ``null`` or a wrong-typed value reached a field
annotated otherwise — and either raised (taking a whole assessment down to
UNCLASSIFIED, #295) or was stored as a value nobody stated (``int(True)`` as a
sample size of 1, #320).  The prompts *sanction* ``null``, so a model obeying
its instructions is what reaches most of these.
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import Any
from unittest.mock import MagicMock

import pytest

from bmlib.llm.data_types import LLMResponse
from bmlib.quality._json_fields import (
    as_bool,
    as_design,
    as_dict,
    as_float,
    as_int,
    as_int_map,
    as_str_list,
    as_text,
)
from bmlib.quality.cochrane_assessor import CochraneAssessor, _clamped_confidence
from bmlib.quality.cochrane_formatter import format_complete_assessment_markdown
from bmlib.quality.cochrane_models import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    create_default_cochrane_risk_of_bias,
)
from bmlib.quality.data_models import BiasRisk, QualityAssessment, QualityTier, StudyDesign
from bmlib.quality.quality_agent import QualityAgent
from bmlib.quality.study_classifier import StudyClassifier

# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------


class TestTheHelpers:
    """Each helper answers one question: is this value the annotated type?"""

    @pytest.mark.parametrize("value", [None, [], "x", 3, True])
    def test_as_dict_reads_anything_but_an_object_as_empty(self, value: object) -> None:
        assert as_dict(value) == {}

    def test_as_dict_returns_the_object_itself(self) -> None:
        obj = {"a": 1}
        assert as_dict(obj) is obj

    @pytest.mark.parametrize("value", [None, 5, 5.0, True, [], {}])
    def test_as_text_refuses_a_non_string(self, value: object) -> None:
        assert as_text(value) is None

    def test_as_text_keeps_a_string_verbatim(self) -> None:
        assert as_text("  Romania ") == "  Romania "

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (45, 45),
            (100.5, 100),
            ("45", 45),
            (" 45 ", 45),
            (True, None),
            (False, None),
            ("45 participants", None),
            (float("inf"), None),
            (float("nan"), None),
            ([45], None),
            (None, None),
        ],
    )
    def test_as_int(self, value: object, expected: int | None) -> None:
        """A boolean is an ``int`` in Python, and ``int(True)`` is 1 — a
        measured sample size where the model stated a flag (#320).  A float is
        truncated, which is what ``int()`` always did here; a non-finite one
        is refused, since ``int(inf)`` raises ``OverflowError`` — outside the
        ``(ValueError, TypeError)`` the old reader caught."""
        assert as_int(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.7, 0.7),
            (1, 1.0),
            ("0.9", 0.9),
            (True, None),
            (False, None),
            ("high", None),
            ("nan", None),
            (float("nan"), None),
            (float("-inf"), None),
            (None, None),
            ({}, None),
        ],
    )
    def test_as_float(self, value: object, expected: float | None) -> None:
        assert as_float(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(True, True), (False, False), ("true", None), (1, None), (None, None)],
    )
    def test_as_bool(self, value: object, expected: bool | None) -> None:
        assert as_bool(value) is expected

    def test_as_str_list_keeps_only_the_strings(self) -> None:
        assert as_str_list(["a", 1, None, "b", ["c"]]) == ["a", "b"]

    @pytest.mark.parametrize("value", [None, "large sample", 5, {"a": "b"}])
    def test_as_str_list_refuses_a_non_list(self, value: object) -> None:
        assert as_str_list(value) is None

    def test_as_int_map_keeps_only_the_counts(self) -> None:
        assert as_int_map({"intervention": 25, "control": True, "sham": "20", "x": "n/a"}) == {
            "intervention": 25,
            "sham": 20,
        }

    @pytest.mark.parametrize("value", [None, [25, 20], "25"])
    def test_as_int_map_refuses_a_non_object(self, value: object) -> None:
        assert as_int_map(value) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (" RCT ", StudyDesign.RCT),
            ("Case Report", StudyDesign.CASE_REPORT),
            ("a novel design", StudyDesign.UNKNOWN),
            (None, StudyDesign.UNKNOWN),
            (5, StudyDesign.UNKNOWN),
        ],
    )
    def test_as_design(self, value: object, expected: StudyDesign) -> None:
        assert as_design(value) is expected

    def test_a_refused_value_is_named_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("DEBUG", logger="bmlib.quality._json_fields"):
            as_int(True, "sample_size")

        assert caplog.messages == ["Reading sample_size as unstated: the answer was a bool"]

    def test_a_null_is_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """``null`` is what the prompts ask for; only an out-of-contract value
        is worth a line."""
        with caplog.at_level("DEBUG", logger="bmlib.quality._json_fields"):
            as_int(None, "sample_size")

        assert caplog.messages == []


# ---------------------------------------------------------------------------
# Tier 3 (#295, #320)
# ---------------------------------------------------------------------------


def _tier3(**fields: Any) -> QualityAssessment:
    data: dict[str, Any] = {"study_design": "rct", "quality_score": 8, "confidence": 0.9}
    data.update(fields)
    return QualityAgent(llm=MagicMock(), model="x:y")._parse_data(data)


class TestTierThreeSurvivesANull:
    """#295: each of these raised, and ``assess()``'s broad ``except`` turned
    a parseable reply into ``unclassified()`` with no retry — which
    ``QualityManager`` then let *replace* a conclusive Tier 1 result."""

    def test_a_null_design_characteristics(self) -> None:
        result = _tier3(design_characteristics=None)

        assert result.study_design is StudyDesign.RCT
        assert result.quality_tier is QualityTier.TIER_4_EXPERIMENTAL
        assert (result.is_randomized, result.is_blinded) == (None, None)

    def test_a_null_bias_risk(self) -> None:
        assert _tier3(bias_risk=None).bias_risk == BiasRisk()

    def test_a_null_study_design(self) -> None:
        result = _tier3(study_design=None)

        assert result.study_design is StudyDesign.UNKNOWN
        assert result.assessment_tier == 3

    def test_a_null_quality_score_takes_the_default(self) -> None:
        assert _tier3(quality_score=None).quality_score == 0.0

    def test_a_null_confidence_takes_the_default(self) -> None:
        assert _tier3(confidence=None).confidence == 0.5

    def test_the_whole_assess_call_survives(self) -> None:
        """End to end: the reply reaches ``assess()`` and comes back
        classified, not ``unclassified()``."""
        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content=json.dumps(
                {
                    "study_design": "rct",
                    "quality_score": 8,
                    "confidence": 0.9,
                    "design_characteristics": None,
                    "bias_risk": None,
                }
            ),
            stop_reason="stop",
        )
        result = QualityAgent(llm=llm, model="x:y").assess("Title", "Abstract")

        assert result.study_design is StudyDesign.RCT
        assert result.assessment_tier == 3
        assert llm.chat.call_count == 1


class TestTierThreeNarrowsEveryValue:
    """#320 and its neighbours: a wrong-typed value reads as unstated."""

    def test_a_boolean_sample_size_is_not_a_count(self) -> None:
        assert _tier3(sample_size=True).sample_size is None
        assert _tier3(sample_size=False).sample_size is None

    def test_a_real_sample_size_is_kept(self) -> None:
        assert _tier3(sample_size=120).sample_size == 120
        assert _tier3(sample_size="120").sample_size == 120

    def test_an_infinite_sample_size_does_not_take_the_assessment_down(self) -> None:
        """``json.loads`` accepts ``Infinity``; ``int(inf)`` raised
        ``OverflowError`` out of the old reader's ``except``."""
        data = json.loads('{"study_design": "rct", "sample_size": Infinity}')
        result = QualityAgent(llm=MagicMock(), model="x:y")._parse_data(data)

        assert result.sample_size is None
        assert result.study_design is StudyDesign.RCT

    def test_a_string_is_not_a_list_of_strengths(self) -> None:
        assert _tier3(strengths="large sample").strengths == []

    def test_a_number_is_not_a_list_of_limitations(self) -> None:
        assert _tier3(limitations=5).limitations == []

    def test_a_list_keeps_only_its_strings(self) -> None:
        assert _tier3(strengths=["large", 1, None, "blinded"]).strengths == ["large", "blinded"]

    def test_a_numeric_evidence_level_is_not_a_level(self) -> None:
        assert _tier3(evidence_level=5).evidence_level is None
        assert _tier3(evidence_level="1b").evidence_level == "1b"

    def test_a_string_flag_is_not_a_boolean(self) -> None:
        """``is_randomized`` is what ``require_randomization`` tests; a string
        there read as an answer while failing the filter."""
        result = _tier3(design_characteristics={"randomized": "yes", "controlled": True})

        assert result.is_randomized is None
        assert result.is_controlled is True

    def test_a_boolean_confidence_is_not_a_measurement(self) -> None:
        """``float(True)`` is 1.0 — the most confident answer there is."""
        assert _tier3(confidence=True).confidence == 0.5

    def test_a_non_finite_score_is_not_a_score(self) -> None:
        data = json.loads('{"study_design": "rct", "quality_score": NaN, "confidence": NaN}')
        result = QualityAgent(llm=MagicMock(), model="x:y")._parse_data(data)

        assert (result.quality_score, result.confidence) == (0.0, 0.5)


# ---------------------------------------------------------------------------
# Tier 2
# ---------------------------------------------------------------------------


def _tier2(**fields: Any) -> QualityAssessment:
    data: dict[str, Any] = {"study_design": "rct", "confidence": 0.9}
    data.update(fields)
    return StudyClassifier(llm=MagicMock(), model="x:y")._parse_data(data)


class TestTierTwoNarrowsEveryValue:
    def test_a_null_study_design_is_unknown(self) -> None:
        assert _tier2(study_design=None).study_design is StudyDesign.UNKNOWN

    def test_a_null_confidence_takes_the_default(self) -> None:
        assert _tier2(confidence=None).confidence == 0.5

    def test_a_boolean_confidence_takes_the_default(self) -> None:
        assert _tier2(confidence=True).confidence == 0.5

    def test_a_boolean_sample_size_is_not_a_count(self) -> None:
        assert _tier2(sample_size=True).sample_size is None

    def test_a_well_formed_reply_is_unchanged(self) -> None:
        result = _tier2(sample_size=40, blinding="double")

        assert result.study_design is StudyDesign.RCT
        assert (result.confidence, result.sample_size, result.is_blinded) == (0.9, 40, "double")


# ---------------------------------------------------------------------------
# The Cochrane section models (#317)
# ---------------------------------------------------------------------------


class TestANullFieldInACochraneSection:
    """#317: ``_as_dict`` reached a null *section*, never a null *field*, and
    ``COCHRANE_RESPONSE_FORMAT`` says *"Use null for any field the text does
    not report"*."""

    def test_a_null_setting_reads_not_reported(self) -> None:
        participants = CochraneParticipants.from_dict({"setting": None, "population": 5})

        assert (participants.setting, participants.population) == ("Not reported", "Not reported")
        assert participants.format_for_table() == "Setting: Not reported\n\nNot reported"

    def test_a_null_flag_reads_false(self) -> None:
        assert (
            CochraneParticipants.from_dict(
                {"baseline_characteristics_reported": None}
            ).baseline_characteristics_reported
            is False
        )
        assert (
            CochraneParticipants.from_dict(
                {"baseline_characteristics_reported": "yes"}
            ).baseline_characteristics_reported
            is False
        )

    def test_counts_and_lists_are_narrowed(self) -> None:
        participants = CochraneParticipants.from_dict(
            {
                "total_participants": True,
                "group_sizes": {"intervention": 25, "control": None},
                "inclusion_criteria": "adults",
                "exclusion_criteria": ["pregnancy", 3],
            }
        )

        assert participants.total_participants is None
        assert participants.group_sizes == {"intervention": 25}
        assert participants.inclusion_criteria is None
        assert participants.exclusion_criteria == ["pregnancy"]

    def test_interventions(self) -> None:
        interventions = CochraneInterventions.from_dict(
            {"description": None, "control_description": 5, "intervention_groups": ["A", 1]}
        )

        assert interventions.description == "Not reported"
        assert interventions.control_description is None
        assert interventions.intervention_groups == ["A"]

    def test_outcomes(self) -> None:
        outcomes = CochraneOutcomes.from_dict({"description": None, "primary_outcomes": "death"})

        assert outcomes.description == "Not reported"
        assert outcomes.primary_outcomes is None

    def test_notes(self) -> None:
        notes = CochraneNotes.from_dict({"funding_source": 5, "additional_notes": ["x", None]})

        assert notes.funding_source is None
        assert notes.additional_notes == ["x"]
        assert notes.format_for_table() == "x"

    def test_the_assessor_reaches_the_same_rule(self) -> None:
        reply = _full_response()
        reply["study_characteristics"]["participants"] = {"setting": None, "population": "Adults"}
        reply["study_characteristics"]["methods"] = 5

        assessment = _assess(reply)

        assert assessment.study_characteristics.participants.setting == "Not reported"
        assert assessment.study_characteristics.methods == "Not reported"


# ---------------------------------------------------------------------------
# Reading back a partial Cochrane assessment (#310)
# ---------------------------------------------------------------------------


class TestAPartialCharacteristicsTable:
    def test_the_six_keys_default_like_their_siblings(self) -> None:
        chars = CochraneStudyCharacteristics.from_dict({"study_id": "Andrei 2011"})

        assert chars.study_id == "Andrei 2011"
        assert chars.methods == "Not reported"
        assert chars.participants.setting == "Not reported"
        assert chars.interventions.description == "Not reported"
        assert chars.outcomes.description == "Not reported"
        assert chars.notes == CochraneNotes()

    def test_a_missing_study_id_reads_not_reported(self) -> None:
        assert CochraneStudyCharacteristics.from_dict({}).study_id == "Not reported"

    def test_a_null_section_takes_its_own_defaults(self) -> None:
        chars = CochraneStudyCharacteristics.from_dict({"participants": None, "notes": "none"})

        assert chars.participants.setting == "Not reported"
        assert chars.notes == CochraneNotes()


def _complete_assessment() -> CochraneStudyAssessment:
    chars = CochraneStudyCharacteristics(
        study_id="Andrei 2011",
        methods="Parallel randomised trial",
        participants=CochraneParticipants(setting="Romania", population="Adults"),
        interventions=CochraneInterventions(description="Hospital at home"),
        outcomes=CochraneOutcomes(description="Mortality"),
        notes=CochraneNotes(),
    )
    return CochraneStudyAssessment(
        study_characteristics=chars,
        risk_of_bias=create_default_cochrane_risk_of_bias(),
        overall_confidence=0.8,
    )


class TestAnIncompleteAssessmentIsRefusedByName:
    """Nine "Unclear risk" domains would be a fabricated assessment, so a
    dict missing them is not read as one — but the refusal names what is
    missing, as a ``ValueError``, rather than escaping as a bare ``KeyError``."""

    def test_a_missing_risk_of_bias(self) -> None:
        data = _complete_assessment().to_dict()
        del data["risk_of_bias"]

        with pytest.raises(ValueError, match="risk_of_bias"):
            CochraneStudyAssessment.from_dict(data)

    def test_a_missing_characteristics_table(self) -> None:
        data = _complete_assessment().to_dict()
        data["study_characteristics"] = None

        with pytest.raises(ValueError, match="study_characteristics"):
            CochraneStudyAssessment.from_dict(data)

    def test_a_missing_domain(self) -> None:
        data = _complete_assessment().to_dict()["risk_of_bias"]
        del data["allocation_concealment"]

        with pytest.raises(ValueError, match="allocation_concealment"):
            CochraneRiskOfBias.from_dict(data)

    def test_a_domain_that_is_not_an_object(self) -> None:
        data = _complete_assessment().to_dict()["risk_of_bias"]
        data["selective_reporting"] = "Low risk"

        with pytest.raises(ValueError, match="selective_reporting"):
            CochraneRiskOfBias.from_dict(data)

    def test_the_assessor_and_the_model_name_the_same_nine_domains(self) -> None:
        """The reader derives its keys from the dataclass; the assessor's
        table is still written out, so it is held to the same list."""
        from bmlib.quality.cochrane_assessor import _ROB_DOMAINS

        assert [key for key, *_ in _ROB_DOMAINS] == [
            f.name for f in dataclasses.fields(CochraneRiskOfBias)
        ]

    def test_a_domain_missing_its_judgement(self) -> None:
        with pytest.raises(ValueError, match="judgement"):
            RiskOfBiasItem.from_dict(
                {"domain": "d", "bias_type": "selection bias", "support_for_judgement": "s"}
            )

    def test_a_complete_assessment_still_round_trips(self) -> None:
        original = _complete_assessment()
        restored = CochraneStudyAssessment.from_dict(original.to_dict())

        assert restored.to_dict() == original.to_dict()


class TestAQualityAssessmentReadsBackWhatItWrote:
    """#310's own reproduction: ``to_dict`` writes a plain-dict
    ``cochrane_assessment`` through verbatim, so ``from_dict`` must read it
    back — and a partial one is kept as the dict it was, rather than taking
    the Tier 1-3 fields down with it or being filled with fabricated
    domains."""

    def test_the_issues_reproduction(self) -> None:
        partial = {"study_characteristics": {"study_id": "Andrei 2011"}}
        written = QualityAssessment(
            assessment_tier=4,
            study_design=StudyDesign.RCT,
            quality_score=8.0,
            cochrane_assessment=partial,
        ).to_dict()

        restored = QualityAssessment.from_dict(written)

        assert restored.cochrane_assessment == partial
        assert restored.study_design is StudyDesign.RCT
        assert restored.quality_score == 8.0
        assert restored.to_dict() == written

    def test_a_complete_assessment_is_rebuilt_as_the_model(self) -> None:
        written = QualityAssessment(
            assessment_tier=4, cochrane_assessment=_complete_assessment()
        ).to_dict()

        restored = QualityAssessment.from_dict(written).cochrane_assessment

        assert isinstance(restored, CochraneStudyAssessment)
        assert restored.study_id == "Andrei 2011"

    def test_a_non_dict_value_is_kept_verbatim(self) -> None:
        written = QualityAssessment(cochrane_assessment="see attached").to_dict()

        assert QualityAssessment.from_dict(written).cochrane_assessment == "see attached"


# ---------------------------------------------------------------------------
# The Cochrane assessor's top-level reads (#318, #319) and its confidence
# ---------------------------------------------------------------------------


def _full_response(**overrides: Any) -> dict[str, Any]:
    domains = [
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
    data: dict[str, Any] = {
        "study_characteristics": {
            "methods": "Parallel randomised trial",
            "participants": {"setting": "Romania", "population": "Adults"},
            "interventions": {"description": "Hospital at home"},
            "outcomes": {"description": "Mortality"},
            "notes": {},
        },
        "risk_of_bias": {
            d: {"judgement": "Low risk", "support_for_judgement": "Computer-generated"}
            for d in domains
        },
        "overall_confidence": 0.8,
        "evidence_level": "Level 2 (moderate-high)",
        "assessment_notes": ["A note"],
    }
    data.update(overrides)
    return data


def _assess(reply: dict[str, Any]) -> CochraneStudyAssessment:
    assessment = _assessor(json.dumps(reply)).assess("T", "text")
    assert assessment is not None
    return assessment


def _assessor(*replies: str) -> CochraneAssessor:
    llm = MagicMock()
    llm.chat = MagicMock(
        return_value=LLMResponse(
            content=replies[0], model="test:model", input_tokens=1, output_tokens=1
        )
    )
    return CochraneAssessor(llm=llm, model="test:model")


class TestTheCochraneAssessorNarrows:
    def test_a_numeric_evidence_level_is_not_a_level(self) -> None:
        """#318: a number compares unequal to every level string, so a
        downstream ``== "1a"`` read "not level 1a" rather than "unreadable"."""
        assert _assess(_full_response(evidence_level=5)).evidence_level is None

    def test_the_notes_keep_only_their_strings(self) -> None:
        """#319: the container was checked and not its members."""
        reply = _full_response(assessment_notes=["a", 1, None, "b"])

        assert _assess(reply).assessment_notes == ["a", "b"]

    def test_notes_that_are_not_a_list_are_none(self) -> None:
        reply = _full_response(assessment_notes="a single note")

        assert _assess(reply).assessment_notes is None

    def test_a_numeric_support_text_is_not_support(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["allocation_concealment"]["support_for_judgement"] = 5

        rob = _assess(reply).risk_of_bias

        assert rob.allocation_concealment.support_for_judgement == (
            "Not reported or insufficient information to assess"
        )

    def test_the_read_back_path_narrows_the_same_fields(self) -> None:
        data = _complete_assessment().to_dict()
        data.update(evidence_level=5, assessment_notes=["a", 1], overall_confidence=True)

        restored = CochraneStudyAssessment.from_dict(data)

        assert restored.evidence_level is None
        assert restored.assessment_notes == ["a"]
        assert restored.overall_confidence is None


class TestTheCochraneConfidence:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.8, 0.8),
            ("0.8", 0.8),
            (1.4, 1.0),
            (-0.2, 0.0),
            (True, None),
            (False, None),
            (float("nan"), None),
            (float("inf"), None),
            ("high", None),
            (None, None),
        ],
    )
    def test_clamped_confidence(self, value: object, expected: float | None) -> None:
        """``float(True)`` is 1.0 and ``min(1.0, max(0.0, nan))`` is 0.0, so a
        flag passed ``min_confidence`` at full confidence and a NaN was
        recorded as a measured zero — each a number nobody reported."""
        result = _clamped_confidence(value)

        if expected is None:
            assert result is None
        else:
            assert result is not None
            assert result == pytest.approx(expected)
            assert not math.isnan(result)


# ---------------------------------------------------------------------------
# The formatter (#312)
# ---------------------------------------------------------------------------


class TestTheSummaryBlockGuard:
    def test_a_confidence_alone_is_rendered(self) -> None:
        out = format_complete_assessment_markdown(_complete_assessment())

        assert "Assessment Summary" in out
        assert "- **Assessment Confidence:** 80%" in out

    def test_none_of_the_three_renders_no_block(self) -> None:
        assessment = _complete_assessment()
        assessment.overall_confidence = None

        assert "Assessment Summary" not in format_complete_assessment_markdown(assessment)
