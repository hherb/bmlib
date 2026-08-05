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
from unittest.mock import MagicMock, patch

from bmlib.context_processor import (
    ExtractionResult,
    LLMChunkProcessor,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
)
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

    def test_a_null_confidence_becomes_none(self) -> None:
        """A reply with ``"overall_confidence": null`` (or the key absent —
        ``dict.get`` returns ``None`` either way) takes the early ``if value
        is None`` return in ``_clamped_confidence``, distinct from the
        unparseable-string path above, which takes the ``except`` branch."""
        assessment = make_assessor(json.dumps(_full_response(overall_confidence=None))).assess(
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

    def test_an_unreported_confidence_is_not_treated_as_a_low_one(self) -> None:
        """Absence of evidence is not negative evidence: a model that garbled
        its confidence has still produced a usable assessment."""
        assessor = make_assessor(json.dumps(_full_response(overall_confidence="high")))

        assert assessor.assess("T", "text", min_confidence=0.9) is not None


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

    @patch("bmlib.agents.base.time.sleep")
    def test_it_is_retried_once_before_giving_up(self, mock_sleep: MagicMock) -> None:
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
    @patch("bmlib.agents.base.time.sleep")
    def test_an_unparseable_response_returns_none(self, mock_sleep: MagicMock) -> None:
        assert make_assessor("not json at all").assess("T", "text") is None

    @patch("bmlib.agents.base.time.sleep")
    def test_a_top_level_array_returns_none(self, mock_sleep: MagicMock) -> None:
        assert make_assessor("[1, 2, 3]").assess("T", "text") is None


class TestCondensingOversizedText:
    """Text larger than one context is reduced to an evidence digest first, so
    the nine-domain judgement is made once, over content that fits."""

    @staticmethod
    def _tiny_config() -> ProcessingConfig:
        return ProcessingConfig(max_context_chars=200)

    def test_text_below_the_threshold_is_not_condensed(self) -> None:
        assessor = make_assessor(condense_config=self._tiny_config())
        assessment = assessor.assess("T", "x" * 100)

        assert assessment.condensed_from_chars is None
        assert assessment.condensation_status is None
        assert assessor.llm.chat.call_count == 1

    def test_oversized_text_is_condensed_and_says_so(self) -> None:
        # ``_condense`` is stubbed for the same reason as the test below: a
        # real run's call count depends on the chunking, and every reply it
        # consumes off the queue advances what the *assessment* call receives.
        # Worse, the stub queue repeats its last reply — the assessment JSON —
        # so a real run feeds ~1.5k of JSON back in as digest content, which
        # balloons into recursion levels and exhausts the queue. The real
        # condensation is exercised by the two tests below, against replies
        # short enough to converge.
        assessor = make_assessor(json.dumps(_full_response()), condense_config=self._tiny_config())
        assessor._condense = (  # type: ignore[method-assign]
            lambda text, label: ("a digest", [], ProcessingStatus.COMPLETED)
        )
        text = "x " * 400
        assessment = assessor.assess("T", text)

        assert assessment is not None
        # ``assess()`` strips the text before this branch ever sees it, so the
        # recorded length is of the stripped text, not the caller's literal.
        assert assessment.condensed_from_chars == len(text.strip())
        assert assessment.condensation_status == "completed"

    def test_condensation_reduces_every_chunk_of_the_paper(self) -> None:
        """The real harness run, against a reply short enough that the
        extractions fit one context and no recursion is needed."""
        assessor = make_assessor("short evidence", condense_config=self._tiny_config())

        condensed = assessor._condense("x " * 400, "a study")

        assert condensed is not None
        digest, notes, status = condensed
        assert "short evidence" in digest
        assert assessor.llm.chat.call_count > 1  # the paper really was chunked
        assert notes == []  # a clean run records nothing
        assert status is ProcessingStatus.COMPLETED

    def test_the_digest_reaches_the_model_instead_of_the_paper(self) -> None:
        # ``_condense`` is stubbed rather than run: how many model calls a
        # real condensation consumes depends on the chunking, so asserting on
        # the *last* call's content while the stub queue advances underneath
        # makes the assertion depend on that count.  What this test is for is
        # the wiring — that the digest replaces the paper in the prompt.
        assessor = make_assessor(json.dumps(_full_response()), condense_config=self._tiny_config())
        assessor._condense = (  # type: ignore[method-assign]
            lambda text, label: ("DIGEST-MARKER", [], ProcessingStatus.COMPLETED)
        )
        assessor.assess("T", "ORIGINAL-MARKER " * 40)

        final_prompt = assessor.llm.chat.call_args.kwargs["messages"][-1].content
        assert "DIGEST-MARKER" in final_prompt
        assert "ORIGINAL-MARKER" not in final_prompt

    def test_an_empty_digest_returns_none_rather_than_judging_nothing(self) -> None:
        """Running the Cochrane prompt over an empty string returns a
        confident nine-domain assessment of no paper at all."""
        assessor = make_assessor("", "", condense_config=self._tiny_config())

        assert assessor.assess("T", "x " * 400) is None

    def test_the_assessment_is_never_made_from_a_failed_condensation(self) -> None:
        assessor = make_assessor(json.dumps(_full_response()), condense_config=self._tiny_config())
        assessor._condense = lambda text, label: None  # type: ignore[method-assign]

        assert assessor.assess("T", "x " * 400) is None


class TestTheDigestSizeGuaranteeIsEnforced:
    """``TRUNCATED`` names the harness's own recursion ceiling, not the size
    of the digest it produced: ``_merge_results()`` returns whatever the
    last level held once ``max_recursion_depth`` is reached, oversized or
    not. So ``_condense`` has to measure the digest it is about to hand back
    rather than trust ``ProcessingStatus`` to imply it fits — the same
    "measured, not assumed" principle ``context_processor`` already applies
    to itself. A 21,269-char digest emerging from a 200-char budget is the
    concrete failure this guards against."""

    @staticmethod
    def _stub_result(content: str, status: ProcessingStatus) -> ProcessingResult:
        return ProcessingResult(
            final_result=ExtractionResult(content=content, confidence=1.0),
            status=status,
            total_items_processed=1,
            batches_created=1,
            recursion_levels_used=0,
            successful_batches=1,
        )

    def test_a_digest_that_still_exceeds_the_budget_is_not_judged(self) -> None:
        assessor = make_assessor(condense_config=ProcessingConfig(max_context_chars=200))
        oversized = self._stub_result("x" * 21_269, ProcessingStatus.TRUNCATED)

        with patch.object(LLMChunkProcessor, "process", return_value=oversized):
            assert assessor._condense("y" * 500, "a study") is None

    def test_the_guard_does_not_reject_a_digest_that_actually_fits(self) -> None:
        """The negative control: a guard that always returns ``None`` would
        also make the test above pass. This proves the guard can tell a
        digest that fits from one that does not, rather than never firing
        at all — or always firing."""
        assessor = make_assessor(condense_config=ProcessingConfig(max_context_chars=200))
        within_budget = self._stub_result("x" * 199, ProcessingStatus.TRUNCATED)

        with patch.object(LLMChunkProcessor, "process", return_value=within_budget):
            condensed = assessor._condense("y" * 500, "a study")

        assert condensed is not None
        digest, notes, status = condensed
        assert digest == "x" * 199
        assert status is ProcessingStatus.TRUNCATED
        assert notes  # TRUNCATED still records the degradation note


class TestBothCondensationDegradationBranchesReachTheAssessment:
    """Coverage the design spec's "Coverage to hit" list required and which
    was dropped when the plan was amended mid-execution: a ``FAILED``
    condensation must stop the assessment, and a ``PARTIAL`` one — some
    batches failed, but there is still a digest — must not."""

    @staticmethod
    def _stub_result(status: ProcessingStatus, content: str = "usable digest") -> ProcessingResult:
        return ProcessingResult(
            final_result=ExtractionResult(content=content, confidence=1.0),
            status=status,
            total_items_processed=1,
            batches_created=1,
            recursion_levels_used=0,
            error_message="boom" if status is ProcessingStatus.FAILED else None,
            successful_batches=0 if status is ProcessingStatus.FAILED else 1,
        )

    def test_a_failed_condensation_means_no_assessment(self) -> None:
        assessor = make_assessor(
            json.dumps(_full_response()), condense_config=ProcessingConfig(max_context_chars=200)
        )
        failed = self._stub_result(ProcessingStatus.FAILED, content="")

        with patch.object(LLMChunkProcessor, "process", return_value=failed):
            assert assessor._condense("x " * 400, "a study") is None
            assert assessor.assess("T", "x " * 400) is None

    def test_a_partial_condensation_still_returns_an_assessment_carrying_the_note(self) -> None:
        assessor = make_assessor(
            json.dumps(_full_response()), condense_config=ProcessingConfig(max_context_chars=200)
        )
        partial = self._stub_result(ProcessingStatus.PARTIAL)

        with patch.object(LLMChunkProcessor, "process", return_value=partial):
            condensed = assessor._condense("x " * 400, "a study")
            assessment = assessor.assess("T", "x " * 400)

        assert condensed is not None
        digest, notes, status = condensed
        assert status is ProcessingStatus.PARTIAL
        assert notes and "partial" in notes[0]

        assert assessment is not None
        assert assessment.condensation_status == "partial"
        assert any("partial" in note for note in (assessment.assessment_notes or []))


class TestBatchAssessment:
    def test_it_returns_one_assessment_per_successful_study(self) -> None:
        assessor = make_assessor()
        results = assessor.assess_batch(
            [
                {"title": "First", "text": "text one", "study_id": "A 2020"},
                {"title": "Second", "text": "text two", "study_id": "B 2021"},
            ]
        )

        assert [a.study_id for a in results] == ["A 2020", "B 2021"]

    @patch("bmlib.agents.base.time.sleep")
    def test_a_failed_study_is_skipped_and_the_rest_are_kept(self, mock_sleep: MagicMock) -> None:
        assessor = make_assessor("not json", "not json", "not json", json.dumps(_full_response()))
        results = assessor.assess_batch(
            [
                {"title": "First", "text": "text one", "study_id": "A 2020"},
                {"title": "Second", "text": "text two", "study_id": "B 2021"},
            ]
        )

        assert [a.study_id for a in results] == ["B 2021"]

    def test_progress_is_reported_for_every_study(self) -> None:
        seen: list[tuple[int, int, str]] = []
        make_assessor().assess_batch(
            [{"title": "First", "text": "t"}, {"title": "Second", "text": "t"}],
            progress_callback=lambda current, total, title: seen.append((current, total, title)),
        )

        assert seen == [(1, 2, "First"), (2, 2, "Second")]


class TestStatistics:
    """Upstream incremented ``total_assessments`` only on the success path,
    after every failure had already returned — so ``success_rate`` was
    ``successful / successful`` and could only ever be 1.0."""

    @patch("bmlib.agents.base.time.sleep")
    def test_the_success_rate_reports_failures(self, mock_sleep: MagicMock) -> None:
        assessor = make_assessor("not json", "not json", "not json", json.dumps(_full_response()))
        assessor.assess_batch([{"title": "First", "text": "t"}, {"title": "Second", "text": "t"}])

        stats = assessor.get_stats()
        assert stats["total_assessments"] == 2
        assert stats["successful_assessments"] == 1
        assert stats["failed_assessments"] == 1
        assert stats["success_rate"] == 0.5

    @patch("bmlib.agents.base.time.sleep")
    def test_successes_and_failures_account_for_every_attempt(self, mock_sleep: MagicMock) -> None:
        assessor = make_assessor("not json")
        assessor.assess("T", "text")
        assessor.assess(None, None)

        stats = assessor.get_stats()
        assert (
            stats["successful_assessments"] + stats["failed_assessments"]
            == stats["total_assessments"]
        )

    def test_an_empty_run_reports_a_zero_rate(self) -> None:
        assert make_assessor().get_stats()["success_rate"] == 0.0


class TestPublicExports:
    def test_the_assessor_and_the_collapse_are_public(self) -> None:
        import bmlib.quality as quality

        assert "CochraneAssessor" in quality.__all__
        assert "collapse_risk_of_bias" in quality.__all__
        assert quality.CochraneAssessor is CochraneAssessor
