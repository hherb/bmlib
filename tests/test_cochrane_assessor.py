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

"""Tests for the Cochrane assessment agent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from bmlib.llm.data_types import LLMResponse
from bmlib.quality.cochrane_assessor import CochraneAssessor
from bmlib.quality.cochrane_models import (
    ROB_JUDGEMENT_HIGH,
    ROB_JUDGEMENT_LOW,
    ROB_JUDGEMENT_UNCLEAR,
)


def _full_response(**overrides: Any) -> dict[str, Any]:
    """A well-formed model reply, before any per-test overrides."""
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
            "participants": {"setting": "Romania", "population": "Chronic heart failure"},
            "interventions": {"description": "Hospital at home"},
            "outcomes": {"description": "Mortality, cost"},
            "notes": {"funding_source": "None declared"},
        },
        "risk_of_bias": {
            d: {"judgement": ROB_JUDGEMENT_LOW, "support_for_judgement": "Computer-generated"}
            for d in domains
        },
        "overall_confidence": 0.8,
        "evidence_level": "Level 2 (moderate-high)",
        "assessment_notes": ["A note"],
    }
    data.update(overrides)
    return data


def make_assessor(*replies: str, **kwargs: Any) -> CochraneAssessor:
    """An assessor whose client returns *replies* in order, repeating the last."""
    llm = MagicMock()
    responses = [
        LLMResponse(content=reply, model="test:model", input_tokens=1, output_tokens=1)
        for reply in (replies or (json.dumps(_full_response()),))
    ]
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return CochraneAssessor(llm=llm, model="test:model", **kwargs)


class TestAWellFormedAssessment:
    def test_it_returns_a_complete_assessment(self) -> None:
        assessment = make_assessor().assess("A trial", "Full text", study_id="Andrei 2011")

        assert assessment is not None
        assert assessment.study_id == "Andrei 2011"
        assert assessment.study_characteristics.methods == "Parallel randomised trial"
        assert assessment.risk_of_bias.allocation_concealment.judgement == ROB_JUDGEMENT_LOW
        assert assessment.overall_confidence == 0.8
        assert assessment.evidence_level == "Level 2 (moderate-high)"

    def test_the_nine_domains_carry_their_cochrane_metadata(self) -> None:
        """The model supplies judgement and support; the domain name, bias
        type and outcome type are bmlib's, not the model's."""
        rob = make_assessor().assess("A trial", "Full text").risk_of_bias

        assert rob.random_sequence_generation.domain == "Random sequence generation"
        assert rob.random_sequence_generation.bias_type == "selection bias"
        assert rob.blinding_outcome_assessment_subjective.outcome_type == "subjective"
        assert rob.blinding_outcome_assessment_objective.outcome_type == "objective"
        assert rob.selective_reporting.bias_type == "reporting bias"

    def test_uncondensed_text_reports_no_condensation(self) -> None:
        assessment = make_assessor().assess("A trial", "Short text")

        assert assessment.condensed_from_chars is None


class TestIdentity:
    def test_the_caller_supplied_study_id_wins(self) -> None:
        assessment = make_assessor().assess("T", "text", study_id="Smith 2020", document_id=7)
        assert assessment.study_id == "Smith 2020"

    def test_a_document_id_is_the_first_fallback(self) -> None:
        """No surname is guessed from an author list.  Upstream derived one
        with ``first_author.split()[-1]``, which reads "van der Berg" as
        "Berg" and any "Surname, Given" string backwards."""
        assessment = make_assessor().assess("T", "text", document_id=7)
        assert assessment.study_id == "Study 7"

    def test_the_title_is_the_last_fallback(self) -> None:
        assessment = make_assessor().assess("A trial of things", "text")
        assert assessment.study_id == "A trial of things"

    def test_identifiers_reach_the_characteristics_table(self) -> None:
        assessment = make_assessor().assess(
            "T", "text", pmid="12345678", doi="10.1/x", document_id=7
        )
        chars = assessment.study_characteristics
        assert (chars.pmid, chars.doi, chars.document_id) == ("12345678", "10.1/x", 7)


class TestNothingToAssess:
    def test_a_blank_title_and_text_returns_none_without_calling_the_model(self) -> None:
        """Left to itself the model returns a fully-formed nine-domain
        judgement for a paper it was told nothing about."""
        assessor = make_assessor()
        assert assessor.assess(None, None) is None
        assessor.llm.chat.assert_not_called()

    def test_a_title_alone_is_still_assessed(self) -> None:
        assert make_assessor().assess("A trial", None) is not None


class TestJudgementsAreNormalised:
    """Upstream wrote the model's raw string into ``RiskOfBiasItem.judgement``.
    A model answering "low" stores an invalid value: ``__post_init__`` warns,
    ``get_summary_counts()`` then skips that domain entirely, and the summary
    silently reports eight domains instead of nine."""

    def test_a_lowercase_judgement_is_normalised(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["allocation_concealment"]["judgement"] = "low"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.allocation_concealment.judgement == ROB_JUDGEMENT_LOW

    def test_an_underscored_judgement_is_normalised(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["selective_reporting"]["judgement"] = "high_risk"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.selective_reporting.judgement == ROB_JUDGEMENT_HIGH

    def test_every_domain_is_counted_in_the_summary(self) -> None:
        """The consequence the normalisation exists to prevent."""
        reply = _full_response()
        reply["risk_of_bias"]["allocation_concealment"]["judgement"] = "low"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert sum(rob.get_summary_counts().values()) == 9

    def test_an_unintelligible_judgement_becomes_unclear(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["incomplete_outcome_data"]["judgement"] = "probably fine"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.incomplete_outcome_data.judgement == ROB_JUDGEMENT_UNCLEAR


class TestConfidence:
    def test_a_confidence_outside_the_range_is_clamped(self) -> None:
        """A model reporting 1.4 would outrank every honest result and defeat
        ``min_confidence``."""
        assessment = make_assessor(json.dumps(_full_response(overall_confidence=1.4))).assess(
            "T", "text"
        )
        assert assessment.overall_confidence == 1.0

    def test_a_negative_confidence_is_clamped(self) -> None:
        assessment = make_assessor(json.dumps(_full_response(overall_confidence=-0.2))).assess(
            "T", "text"
        )
        assert assessment.overall_confidence == 0.0

    def test_an_unusable_confidence_becomes_none(self) -> None:
        assessment = make_assessor(json.dumps(_full_response(overall_confidence="high"))).assess(
            "T", "text"
        )
        assert assessment.overall_confidence is None

    def test_min_confidence_is_honoured(self) -> None:
        """Upstream declared DEFAULT_MIN_CONFIDENCE = 0.4, threaded it through
        the signature, and never read it."""
        assessor = make_assessor(json.dumps(_full_response(overall_confidence=0.3)))
        assert assessor.assess("T", "text", min_confidence=0.5) is None

    def test_min_confidence_defaults_to_dropping_nothing(self) -> None:
        assessor = make_assessor(json.dumps(_full_response(overall_confidence=0.0)))
        assert assessor.assess("T", "text") is not None


class TestAMissingRiskOfBiasSection:
    """A Cochrane assessment without any risk-of-bias section is not a
    Cochrane assessment.  Upstream accepted it and fabricated nine "Unclear
    risk" defaults out of nothing."""

    def test_a_response_with_no_risk_of_bias_block_is_rejected(self) -> None:
        reply = _full_response()
        del reply["risk_of_bias"]

        assert make_assessor(json.dumps(reply)).assess("T", "text") is None

    def test_a_response_with_an_empty_risk_of_bias_block_is_rejected(self) -> None:
        assert (
            make_assessor(json.dumps(_full_response(risk_of_bias={}))).assess("T", "text") is None
        )

    def test_it_is_retried_once_before_giving_up(self) -> None:
        """Two whole attempts, not three: a model that omits the section twice
        has misread the prompt, and the bound keeps the worst case at six
        model calls rather than nine."""
        bad = _full_response()
        del bad["risk_of_bias"]
        assessor = make_assessor(json.dumps(bad), json.dumps(_full_response()))

        assert assessor.assess("T", "text") is not None
        assert assessor.llm.chat.call_count == 2

    def test_a_single_missing_domain_defaults_to_unclear(self) -> None:
        """Honest per-domain degradation of an otherwise good answer, which is
        a different thing from fabricating the whole section."""
        reply = _full_response()
        del reply["risk_of_bias"]["selective_reporting"]

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.selective_reporting.judgement == ROB_JUDGEMENT_UNCLEAR
        assert "insufficient information" in rob.selective_reporting.support_for_judgement


class TestAnUnusableResponse:
    def test_an_unparseable_response_returns_none(self) -> None:
        assert make_assessor("not json at all").assess("T", "text") is None

    def test_a_top_level_array_returns_none(self) -> None:
        assert make_assessor("[1, 2, 3]").assess("T", "text") is None
