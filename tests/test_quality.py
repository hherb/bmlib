"""Tests for bmlib.quality data models and metadata filter."""

from __future__ import annotations

from bmlib.quality.data_models import (
    BiasRisk,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    StudyDesign,
    DESIGN_TO_TIER,
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
        br = BiasRisk(selection="low", performance="high", detection="unclear",
                      attrition="low", reporting="high")
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
            StudyDesign.COHORT_PROSPECTIVE, confidence=0.75, sample_size=500,
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

    def test_serialisation_roundtrip(self):
        a = QualityAssessment(
            assessment_tier=3,
            extraction_method="llm_deep_assessment",
            study_design=StudyDesign.RCT,
            quality_tier=QualityTier.TIER_4_EXPERIMENTAL,
            quality_score=8.0,
            confidence=0.85,
            bias_risk=BiasRisk(selection="low", performance="low",
                               detection="unclear", attrition="low",
                               reporting="low"),
            strengths=["Large sample"],
            limitations=["Single center"],
        )
        d = a.to_dict()
        a2 = QualityAssessment.from_dict(d)
        assert a2.study_design == StudyDesign.RCT
        assert a2.quality_score == 8.0
        assert a2.bias_risk.selection == "low"


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
