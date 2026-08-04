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

"""Tests for bmlib.quality data models and metadata filter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bmlib.quality.data_models import (
    DESIGN_TO_TIER,
    BiasRisk,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    StudyDesign,
)
from bmlib.quality.metadata_filter import classify_from_metadata


class TestStudyDesign:
    def test_all_designs_have_tier_mapping(self):
        for design in StudyDesign:
            assert design in DESIGN_TO_TIER


class TestQualityTier:
    def test_ordering(self):
        assert QualityTier.TIER_5_SYNTHESIS > QualityTier.TIER_4_EXPERIMENTAL
        assert QualityTier.TIER_4_EXPERIMENTAL > QualityTier.TIER_1_ANECDOTAL
        assert QualityTier.UNCLASSIFIED < QualityTier.TIER_1_ANECDOTAL


class TestBiasRisk:
    def test_roundtrip(self):
        br = BiasRisk(
            selection="low",
            performance="high",
            detection="unclear",
            attrition="low",
            reporting="high",
        )
        d = br.to_dict()
        br2 = BiasRisk.from_dict(d)
        assert br2.selection == "low"
        assert br2.performance == "high"

    def test_invalid_values_default_to_unclear(self):
        br = BiasRisk.from_dict({"selection": "invalid", "performance": None})
        assert br.selection == "unclear"
        assert br.performance == "unclear"


class TestQualityAssessment:
    def test_unclassified(self):
        a = QualityAssessment.unclassified()
        assert a.quality_tier == QualityTier.UNCLASSIFIED
        assert a.assessment_tier == 0

    def test_from_metadata(self):
        a = QualityAssessment.from_metadata(StudyDesign.RCT)
        assert a.quality_tier == QualityTier.TIER_4_EXPERIMENTAL
        assert a.assessment_tier == 1
        assert a.confidence == 0.9

    def test_from_classification(self):
        a = QualityAssessment.from_classification(
            StudyDesign.COHORT_PROSPECTIVE,
            confidence=0.75,
            sample_size=500,
        )
        assert a.quality_tier == QualityTier.TIER_3_CONTROLLED
        assert a.assessment_tier == 2
        assert a.sample_size == 500

    def test_passes_filter_min_tier(self):
        a = QualityAssessment.from_metadata(StudyDesign.CASE_REPORT)
        f = QualityFilter(min_tier=QualityTier.TIER_3_CONTROLLED)
        assert not a.passes_filter(f)

        a2 = QualityAssessment.from_metadata(StudyDesign.RCT)
        assert a2.passes_filter(f)

    def test_passes_filter_defaults(self):
        a = QualityAssessment.unclassified()
        f = QualityFilter()
        assert a.passes_filter(f)

    def test_require_randomization_keeps_metadata_rct(self):
        # Regression: a metadata/classifier RCT must pass require_randomization,
        # not be silently rejected because is_randomized was never populated.
        f = QualityFilter(require_randomization=True)
        rct_meta = QualityAssessment.from_metadata(StudyDesign.RCT)
        assert rct_meta.is_randomized is True
        assert rct_meta.passes_filter(f)

        rct_cls = QualityAssessment.from_classification(StudyDesign.RCT)
        assert rct_cls.is_randomized is True
        assert rct_cls.passes_filter(f)

    def test_require_randomization_rejects_observational(self):
        f = QualityFilter(require_randomization=True)
        cohort = QualityAssessment.from_metadata(StudyDesign.COHORT_PROSPECTIVE)
        assert cohort.is_randomized is False
        assert not cohort.passes_filter(f)

    def test_serialisation_roundtrip(self):
        a = QualityAssessment(
            assessment_tier=3,
            extraction_method="llm_deep_assessment",
            study_design=StudyDesign.RCT,
            quality_tier=QualityTier.TIER_4_EXPERIMENTAL,
            quality_score=8.0,
            confidence=0.85,
            bias_risk=BiasRisk(
                selection="low",
                performance="low",
                detection="unclear",
                attrition="low",
                reporting="low",
            ),
            strengths=["Large sample"],
            limitations=["Single center"],
        )
        d = a.to_dict()
        a2 = QualityAssessment.from_dict(d)
        assert a2.study_design == StudyDesign.RCT
        assert a2.quality_score == 8.0
        assert a2.bias_risk.selection == "low"

    def test_an_attached_cochrane_assessment_survives_the_round_trip_and_its_absence_loads_as_none(
        self,
    ) -> None:
        """``to_dict()``/``from_dict()`` gained a ``cochrane_assessment`` branch
        alongside ``bias_risk`` — this round-trips a *real* Cochrane object
        (not a mock) through the container's own serialisation, then checks
        the other side of the same branch: a dict with no key at all (what
        ``to_dict()`` produces when the field is unset) must load back as
        ``None`` rather than raising on the missing key."""
        from bmlib.quality.cochrane_models import (
            ROB_JUDGEMENT_HIGH,
            CochraneInterventions,
            CochraneNotes,
            CochraneOutcomes,
            CochraneParticipants,
            CochraneStudyAssessment,
            CochraneStudyCharacteristics,
            create_default_cochrane_risk_of_bias,
        )

        rob = create_default_cochrane_risk_of_bias()
        rob.selective_reporting.judgement = ROB_JUDGEMENT_HIGH
        characteristics = CochraneStudyCharacteristics(
            study_id="Andrei 2011",
            methods="Parallel randomised trial",
            participants=CochraneParticipants(
                setting="Romania",
                population="Chronic heart failure",
                total_participants=45,
            ),
            interventions=CochraneInterventions(description="Hospital at home"),
            outcomes=CochraneOutcomes(description="Mortality, cost"),
            notes=CochraneNotes(funding_source="None declared"),
        )
        cochrane = CochraneStudyAssessment(
            study_characteristics=characteristics,
            risk_of_bias=rob,
            overall_confidence=0.8,
        )
        a = QualityAssessment(
            assessment_tier=4,
            extraction_method="llm_cochrane_assessment",
            study_design=StudyDesign.RCT,
            quality_tier=QualityTier.TIER_4_EXPERIMENTAL,
            cochrane_assessment=cochrane,
        )

        rebuilt = QualityAssessment.from_dict(a.to_dict())

        assert rebuilt.cochrane_assessment is not None
        assert rebuilt.cochrane_assessment.study_id == "Andrei 2011"
        assert (
            rebuilt.cochrane_assessment.risk_of_bias.selective_reporting.judgement
            == ROB_JUDGEMENT_HIGH
        )

        without_cochrane = QualityAssessment.unclassified()
        assert "cochrane_assessment" not in without_cochrane.to_dict()
        assert QualityAssessment.from_dict(without_cochrane.to_dict()).cochrane_assessment is None


class TestMetadataFilter:
    def test_rct_classification(self):
        result = classify_from_metadata(["Randomized Controlled Trial"])
        assert result.study_design == StudyDesign.RCT
        assert result.quality_tier == QualityTier.TIER_4_EXPERIMENTAL

    def test_systematic_review(self):
        result = classify_from_metadata(["Systematic Review", "Meta-Analysis"])
        assert result.study_design == StudyDesign.SYSTEMATIC_REVIEW

    def test_empty_types(self):
        result = classify_from_metadata([])
        assert result.quality_tier == QualityTier.UNCLASSIFIED

    def test_unknown_type(self):
        result = classify_from_metadata(["Some Unknown Type"])
        assert result.quality_tier == QualityTier.UNCLASSIFIED

    def test_priority_resolution(self):
        # RCT should take priority over editorial
        result = classify_from_metadata(["Editorial", "Randomized Controlled Trial"])
        assert result.study_design == StudyDesign.RCT

    def test_case_insensitive_matching(self):
        result = classify_from_metadata(["systematic review"])
        assert result.study_design == StudyDesign.SYSTEMATIC_REVIEW

    def test_hyphenated_type(self):
        result = classify_from_metadata(["meta-analysis"])
        assert result.study_design == StudyDesign.META_ANALYSIS

    def test_mixed_case_with_noise(self):
        # EuropePMC categories include journal name as noise
        result = classify_from_metadata(
            [
                "European Radiology",
                "Systematic Review",
                "Meta-Analysis",
            ]
        )
        assert result.study_design == StudyDesign.SYSTEMATIC_REVIEW

    def test_lowercase_from_categories(self):
        result = classify_from_metadata(
            [
                "some journal",
                "randomized controlled trial",
            ]
        )
        assert result.study_design == StudyDesign.RCT

    def test_cohort_outranks_case_control(self):
        # A paper tagged with both should resolve to the stronger cohort design.
        result = classify_from_metadata(["Case-Control Study", "Cohort Study"])
        assert result.study_design == StudyDesign.COHORT_PROSPECTIVE

    def test_multicenter_study_not_classified_as_rct(self):
        # "Multicenter Study" is an organisational attribute, not a design.
        result = classify_from_metadata(["Multicenter Study"])
        assert result.quality_tier == QualityTier.UNCLASSIFIED

    def test_comparative_study_not_classified(self):
        # "Comparative Study" is a generic tag, not a specific design.
        result = classify_from_metadata(["Comparative Study"])
        assert result.quality_tier == QualityTier.UNCLASSIFIED

    def test_bare_observational_study_not_classified(self):
        # "Observational Study" is PubMed's catch-all; it must not be asserted
        # as a specific prospective-cohort design at high confidence.
        result = classify_from_metadata(["Observational Study"])
        assert result.quality_tier == QualityTier.UNCLASSIFIED

    def test_observational_with_specific_subtype_still_classifies(self):
        result = classify_from_metadata(["Observational Study", "Retrospective Study"])
        assert result.study_design == StudyDesign.COHORT_RETROSPECTIVE


class TestQualityManager:
    def _manager(self):
        from unittest.mock import MagicMock

        from bmlib.quality.manager import QualityManager

        mgr = QualityManager(
            llm=MagicMock(),
            classifier_model="ollama:x",
            assessor_model="ollama:y",
        )
        mgr.classifier = MagicMock()
        mgr.assessor = MagicMock()
        mgr.assessor.assess.return_value = QualityAssessment(
            assessment_tier=3, study_design=StudyDesign.RCT
        )
        mgr.classifier.classify.return_value = QualityAssessment(
            assessment_tier=2, study_design=StudyDesign.RCT
        )
        return mgr

    def test_detailed_assessment_skips_tier2(self):
        # Requesting a detailed (Tier 3) assessment must not also spend a
        # Tier 2 classifier call whose result would be discarded.
        mgr = self._manager()
        f = QualityFilter(use_detailed_assessment=True, use_llm_classification=True)
        result = mgr.assess("Title", "Abstract", publication_types=[], filter_settings=f)
        assert result.assessment_tier == 3
        mgr.assessor.assess.assert_called_once()
        mgr.classifier.classify.assert_not_called()

    def test_classification_only_runs_tier2(self):
        mgr = self._manager()
        f = QualityFilter(use_llm_classification=True, use_detailed_assessment=False)
        result = mgr.assess("Title", "Abstract", publication_types=[], filter_settings=f)
        assert result.assessment_tier == 2
        mgr.classifier.classify.assert_called_once()
        mgr.assessor.assess.assert_not_called()


class TestMissingAbstract:
    """A record with no abstract must degrade, not raise.

    Sources routinely omit abstracts, and the value arrives here as ``None``
    rather than ``""`` when it came from a nullable database column. Both
    LLM tiers slice the abstract to a character budget, so an unguarded
    ``None`` raises ``TypeError`` mid-run and takes the whole scoring pass
    down with it.
    """

    def _stub_llm(self):
        from unittest.mock import MagicMock

        llm = MagicMock()
        # Whatever the agent asks of the model, hand back a usable answer so
        # the test exercises prompt construction rather than parsing.
        llm.chat.return_value = MagicMock(
            content='{"study_design": "rct", "confidence": 0.9}',
            stop_reason="stop",
        )
        return llm

    def test_classifier_handles_none_abstract(self):
        from bmlib.quality.study_classifier import StudyClassifier

        classifier = StudyClassifier(llm=self._stub_llm(), model="ollama:x")
        result = classifier.classify("A title", None)

        assert result is not None
        assert result.study_design == StudyDesign.RCT

    def test_assessor_handles_none_abstract(self):
        from bmlib.quality.quality_agent import QualityAgent

        agent = QualityAgent(llm=self._stub_llm(), model="ollama:x")
        result = agent.assess("A title", None)

        assert result is not None

    def test_classifier_handles_none_title(self):
        """A None title must not reach the model as the literal text "None".

        ``str.format`` renders None rather than raising, so asserting only
        that a result comes back cannot fail — the prompt has to be checked.
        """
        from bmlib.quality.study_classifier import StudyClassifier

        llm = self._stub_llm()
        classifier = StudyClassifier(llm=llm, model="ollama:x")
        assert classifier.classify(None, "An abstract") is not None

        prompt = llm.chat.call_args.kwargs["messages"][-1].content
        assert "Title: \n" in prompt
        assert "None" not in prompt

    def test_assessor_handles_none_title(self):
        from bmlib.quality.quality_agent import QualityAgent

        llm = self._stub_llm()
        agent = QualityAgent(llm=llm, model="ollama:x")
        assert agent.assess(None, "An abstract") is not None

        prompt = llm.chat.call_args.kwargs["messages"][-1].content
        assert "None" not in prompt

    @pytest.mark.parametrize(
        "filt",
        [
            QualityFilter(use_llm_classification=True, use_detailed_assessment=False),
            QualityFilter(use_llm_classification=True, use_detailed_assessment=True),
        ],
        ids=["tier2", "tier3"],
    )
    def test_the_manager_survives_a_none_abstract(self, filt):
        """The production entry point, not just the agents beneath it."""
        from bmlib.quality.manager import QualityManager

        llm = self._stub_llm()
        mgr = QualityManager(llm=llm, classifier_model="ollama:x", assessor_model="ollama:y")
        result = mgr.assess("A title", None, publication_types=[], filter_settings=filt)

        assert result is not None

        prompt = llm.chat.call_args.kwargs["messages"][-1].content
        assert "None" not in prompt


class TestNothingToAssess:
    """With neither title nor abstract there is nothing to work from.

    An empty prompt does not produce an empty answer — the model invents a
    plausible design, and a fabricated tier-2 RCT is indistinguishable
    downstream from a real classification. Refusing is the honest result,
    and it costs no tokens.
    """

    def _llm(self):
        from unittest.mock import MagicMock

        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content='{"study_design": "rct", "confidence": 0.95}', stop_reason="stop"
        )
        return llm

    @pytest.mark.parametrize("title,abstract", [(None, None), ("", ""), ("  ", "\n")])
    def test_classifier_refuses_without_calling_the_model(self, title, abstract):
        from bmlib.quality.study_classifier import StudyClassifier

        llm = self._llm()
        result = StudyClassifier(llm=llm, model="ollama:x").classify(title, abstract)

        assert result.quality_tier == QualityTier.UNCLASSIFIED
        assert result.study_design != StudyDesign.RCT
        llm.chat.assert_not_called()

    @pytest.mark.parametrize("title,abstract", [(None, None), ("", ""), ("  ", "\n")])
    def test_assessor_refuses_without_calling_the_model(self, title, abstract):
        from bmlib.quality.quality_agent import QualityAgent

        llm = self._llm()
        result = QualityAgent(llm=llm, model="ollama:y").assess(title, abstract)

        assert result.quality_tier == QualityTier.UNCLASSIFIED
        llm.chat.assert_not_called()

    def test_a_title_alone_is_still_worth_classifying(self):
        """Weak but honest — the guard must not swallow a usable title."""
        from bmlib.quality.study_classifier import StudyClassifier

        llm = self._llm()
        result = StudyClassifier(llm=llm, model="ollama:x").classify("A randomised trial", None)

        assert result.study_design == StudyDesign.RCT
        llm.chat.assert_called_once()


class TestTunedSamplingDefaults:
    """The tuned sampling belongs to the agents, not to QualityManager.

    These values were once forced at the call site, so they held however the
    agent was built. Moving them to the constructor would have quietly
    dropped a directly-constructed agent to BaseAgent's generic 0.3/4096.
    """

    def _llm(self):
        from unittest.mock import MagicMock

        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content='{"study_design": "rct", "confidence": 0.9}', stop_reason="stop"
        )
        return llm

    def test_a_directly_constructed_classifier_keeps_them(self):
        from bmlib.quality.study_classifier import StudyClassifier

        llm = self._llm()
        StudyClassifier(llm=llm, model="ollama:x").classify("Title", "Abstract")

        assert llm.chat.call_args.kwargs["temperature"] == 0.1
        assert llm.chat.call_args.kwargs["max_tokens"] == 1024

    def test_a_directly_constructed_assessor_keeps_them(self):
        from bmlib.quality.quality_agent import QualityAgent

        llm = self._llm()
        QualityAgent(llm=llm, model="ollama:y").assess("Title", "Abstract")

        assert llm.chat.call_args.kwargs["temperature"] == 0.2
        assert llm.chat.call_args.kwargs["max_tokens"] == 1024


class TestGenerationBudget:
    """The configured token budget must actually reach the model.

    Both LLM tiers used to repeat their temperature and ``max_tokens`` at the
    call site, which silently overrode whatever the constructor was given —
    so raising the budget had no effect. A ceiling too low for a chatty model
    truncates the response and every paper falls back to UNCLASSIFIED.
    """

    def _llm(self):
        from unittest.mock import MagicMock

        llm = MagicMock()
        llm.chat.return_value = MagicMock(
            content='{"study_design": "rct", "confidence": 0.9}', stop_reason="stop"
        )
        return llm

    def _manager(self, llm):
        from bmlib.quality.manager import QualityManager

        return QualityManager(llm=llm, classifier_model="ollama:x", assessor_model="ollama:y")

    def test_classifier_budget_reaches_the_model(self):
        llm = self._llm()
        self._manager(llm).classifier.classify("Title", "Abstract")

        assert llm.chat.call_args.kwargs["max_tokens"] == 1024
        assert llm.chat.call_args.kwargs["temperature"] == 0.1

    def test_assessor_budget_reaches_the_model(self):
        llm = self._llm()
        self._manager(llm).assessor.assess("Title", "Abstract")

        assert llm.chat.call_args.kwargs["max_tokens"] == 1024
        assert llm.chat.call_args.kwargs["temperature"] == 0.2

    def test_a_caller_can_override_the_budget(self):
        from bmlib.quality.study_classifier import StudyClassifier

        llm = self._llm()
        StudyClassifier(llm=llm, model="ollama:x", max_tokens=4096).classify("T", "A")

        assert llm.chat.call_args.kwargs["max_tokens"] == 4096


class TestArrayResponseIsRetried:
    """A model answering with an array must not silently cost the assessment.

    Both LLM tiers hand ``chat_json``'s result straight to ``_parse_data()``,
    which calls ``.get()`` on it. A list therefore raised ``AttributeError``
    into the broad ``except Exception``, and the paper degraded to
    UNCLASSIFIED with no retry and nothing in the log naming the shape. Both
    tiers now pass ``require_dict=True``, so the wrong shape is retried like
    any other bad response.
    """

    def _llm(self, *contents: str):
        from unittest.mock import MagicMock

        llm = MagicMock()
        llm.chat.side_effect = [
            MagicMock(content=content, stop_reason="stop") for content in contents
        ]
        return llm

    @patch("bmlib.agents.base.time.sleep")
    def test_the_classifier_retries_and_recovers(self, mock_sleep):
        from bmlib.quality.study_classifier import StudyClassifier

        llm = self._llm(
            '[{"study_design": "rct", "confidence": 0.9}]',
            '{"study_design": "rct", "confidence": 0.9}',
        )
        result = StudyClassifier(llm=llm, model="ollama:x").classify("Title", "Abstract")

        assert result.study_design == StudyDesign.RCT
        assert llm.chat.call_count == 2

    @patch("bmlib.agents.base.time.sleep")
    def test_the_assessor_retries_and_recovers(self, mock_sleep):
        from bmlib.quality.quality_agent import QualityAgent

        llm = self._llm(
            '[{"study_design": "rct"}]',
            '{"study_design": "rct", "confidence": 0.9}',
        )
        result = QualityAgent(llm=llm, model="ollama:y").assess("Title", "Abstract")

        assert result.study_design == StudyDesign.RCT
        assert llm.chat.call_count == 2


class TestTheCochraneRoute:
    """The tiered pipeline can produce Cochrane data, not merely represent it."""

    @staticmethod
    def _manager_with_cochrane(assessment=None):
        from unittest.mock import MagicMock

        from bmlib.quality.manager import QualityManager

        mgr = QualityManager(llm=MagicMock(), classifier_model="o:x", assessor_model="o:y")
        mgr.classifier = MagicMock()
        mgr.assessor = MagicMock()
        mgr.cochrane = MagicMock()
        mgr.cochrane.assess.return_value = assessment
        return mgr

    @staticmethod
    def _cochrane_assessment():
        from unittest.mock import MagicMock

        from bmlib.quality.cochrane_models import (
            ROB_JUDGEMENT_HIGH,
            CochraneStudyAssessment,
            create_default_cochrane_risk_of_bias,
        )

        rob = create_default_cochrane_risk_of_bias()
        rob.selective_reporting.judgement = ROB_JUDGEMENT_HIGH
        chars = MagicMock()
        return CochraneStudyAssessment(
            study_characteristics=chars,
            risk_of_bias=rob,
            overall_confidence=0.85,
            evidence_level="Level 2 (moderate-high)",
        )

    def test_the_flag_routes_to_the_cochrane_assessor(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.assessment_tier == 4
        assert result.extraction_method == "llm_cochrane_assessment"
        assert result.cochrane_assessment is not None
        mgr.cochrane.assess.assert_called_once()

    def test_the_five_domain_bias_risk_is_populated_from_the_nine(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.bias_risk is not None
        assert result.bias_risk.reporting == "high"
        assert result.bias_risk.selection == "unclear"

    def test_the_tier_1_classification_survives(self):
        """Cochrane produces no study design, so the free metadata tier still
        supplies it rather than being thrown away."""
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess(
            "Title",
            "Abstract",
            publication_types=["Randomized Controlled Trial"],
            filter_settings=f,
        )

        assert result.study_design == StudyDesign.RCT
        assert result.quality_tier == QualityTier.TIER_4_EXPERIMENTAL

    def test_the_full_text_is_preferred_over_the_abstract(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        mgr.assess("Title", "Abstract", full_text="The whole paper", filter_settings=f)

        assert mgr.cochrane.assess.call_args.args[1] == "The whole paper"

    def test_the_abstract_is_used_when_there_is_no_full_text(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        mgr.assess("Title", "Abstract", filter_settings=f)

        assert mgr.cochrane.assess.call_args.args[1] == "Abstract"

    def test_cochrane_supersedes_tier_3(self):
        """Deeper than Tier 3, so the shallower tier is skipped rather than
        run and discarded — as Tier 3 already supersedes Tier 2."""
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True, use_detailed_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.assessment_tier == 4
        mgr.assessor.assess.assert_not_called()
        mgr.classifier.classify.assert_not_called()

    def test_confident_metadata_does_not_short_circuit_the_cochrane_pass(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        mgr.assess(
            "Title",
            "Abstract",
            publication_types=["Randomized Controlled Trial"],
            filter_settings=f,
        )

        mgr.cochrane.assess.assert_called_once()

    def test_a_failed_pass_degrades_to_the_tier_1_result(self):
        """Not to nothing — and the two are distinguishable."""
        mgr = self._manager_with_cochrane(None)
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess(
            "Title",
            "Abstract",
            publication_types=["Randomized Controlled Trial"],
            filter_settings=f,
        )

        assert result.assessment_tier == 1
        assert result.cochrane_assessment is None
        assert result.study_design == StudyDesign.RCT

    def test_the_evidence_level_vocabularies_are_not_mixed(self):
        """CochraneStudyAssessment.evidence_level is free-form model text
        ("Level 2 (moderate-high)"); QualityAssessment.evidence_level is
        Oxford CEBM ("1a"…"5").  Copying one into the other puts a foreign
        vocabulary in a field callers parse."""
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.evidence_level != "Level 2 (moderate-high)"
        assert result.cochrane_assessment.evidence_level == "Level 2 (moderate-high)"

    def test_the_model_confidence_is_carried_across(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.confidence == 0.85

    def test_the_flag_is_off_by_default(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())

        mgr.assess("Title", "Abstract", filter_settings=QualityFilter())

        mgr.cochrane.assess.assert_not_called()
