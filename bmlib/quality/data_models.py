"""Data models for the quality assessment pipeline.

Defines enums for study design and quality tiers, plus the core
:class:`QualityAssessment` dataclass that all three tiers produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import total_ordering
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Study design
# ---------------------------------------------------------------------------

class StudyDesign(Enum):
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"
    RCT = "rct"
    COHORT_PROSPECTIVE = "cohort_prospective"
    COHORT_RETROSPECTIVE = "cohort_retrospective"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_SERIES = "case_series"
    CASE_REPORT = "case_report"
    GUIDELINE = "guideline"
    EDITORIAL = "editorial"
    LETTER = "letter"
    COMMENT = "comment"
    OTHER = "other"
    UNKNOWN = "unknown"


# String → enum lookup (tolerant of LLM output variations)
STUDY_DESIGN_MAPPING: dict[str, StudyDesign] = {
    "systematic_review": StudyDesign.SYSTEMATIC_REVIEW,
    "systematic review": StudyDesign.SYSTEMATIC_REVIEW,
    "meta_analysis": StudyDesign.META_ANALYSIS,
    "meta-analysis": StudyDesign.META_ANALYSIS,
    "rct": StudyDesign.RCT,
    "randomized controlled trial": StudyDesign.RCT,
    "randomised controlled trial": StudyDesign.RCT,
    "cohort_prospective": StudyDesign.COHORT_PROSPECTIVE,
    "prospective cohort": StudyDesign.COHORT_PROSPECTIVE,
    "cohort_retrospective": StudyDesign.COHORT_RETROSPECTIVE,
    "retrospective cohort": StudyDesign.COHORT_RETROSPECTIVE,
    "cohort": StudyDesign.COHORT_PROSPECTIVE,
    "case_control": StudyDesign.CASE_CONTROL,
    "case-control": StudyDesign.CASE_CONTROL,
    "cross_sectional": StudyDesign.CROSS_SECTIONAL,
    "cross-sectional": StudyDesign.CROSS_SECTIONAL,
    "case_series": StudyDesign.CASE_SERIES,
    "case series": StudyDesign.CASE_SERIES,
    "case_report": StudyDesign.CASE_REPORT,
    "case report": StudyDesign.CASE_REPORT,
    "guideline": StudyDesign.GUIDELINE,
    "editorial": StudyDesign.EDITORIAL,
    "letter": StudyDesign.LETTER,
    "comment": StudyDesign.COMMENT,
    "other": StudyDesign.OTHER,
    "unknown": StudyDesign.UNKNOWN,
}


# ---------------------------------------------------------------------------
# Quality tiers (Oxford CEBM–inspired)
# ---------------------------------------------------------------------------

@total_ordering
class QualityTier(Enum):
    """Evidence quality tier.  Higher value = stronger evidence."""
    UNCLASSIFIED = 0
    TIER_1_ANECDOTAL = 1       # case reports, editorials, letters
    TIER_2_OBSERVATIONAL = 2   # cross-sectional, case-control
    TIER_3_CONTROLLED = 3      # cohort studies
    TIER_4_EXPERIMENTAL = 4    # RCTs
    TIER_5_SYNTHESIS = 5       # systematic reviews, meta-analyses

    def __lt__(self, other):
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented


# Design → tier mapping
DESIGN_TO_TIER: dict[StudyDesign, QualityTier] = {
    StudyDesign.SYSTEMATIC_REVIEW: QualityTier.TIER_5_SYNTHESIS,
    StudyDesign.META_ANALYSIS: QualityTier.TIER_5_SYNTHESIS,
    StudyDesign.GUIDELINE: QualityTier.TIER_5_SYNTHESIS,
    StudyDesign.RCT: QualityTier.TIER_4_EXPERIMENTAL,
    StudyDesign.COHORT_PROSPECTIVE: QualityTier.TIER_3_CONTROLLED,
    StudyDesign.COHORT_RETROSPECTIVE: QualityTier.TIER_3_CONTROLLED,
    StudyDesign.CASE_CONTROL: QualityTier.TIER_2_OBSERVATIONAL,
    StudyDesign.CROSS_SECTIONAL: QualityTier.TIER_2_OBSERVATIONAL,
    StudyDesign.CASE_SERIES: QualityTier.TIER_1_ANECDOTAL,
    StudyDesign.CASE_REPORT: QualityTier.TIER_1_ANECDOTAL,
    StudyDesign.EDITORIAL: QualityTier.TIER_1_ANECDOTAL,
    StudyDesign.LETTER: QualityTier.TIER_1_ANECDOTAL,
    StudyDesign.COMMENT: QualityTier.TIER_1_ANECDOTAL,
    StudyDesign.OTHER: QualityTier.UNCLASSIFIED,
    StudyDesign.UNKNOWN: QualityTier.UNCLASSIFIED,
}

# Design → default numeric score (0–10)
DESIGN_TO_SCORE: dict[StudyDesign, float] = {
    StudyDesign.SYSTEMATIC_REVIEW: 9.0,
    StudyDesign.META_ANALYSIS: 9.0,
    StudyDesign.GUIDELINE: 8.5,
    StudyDesign.RCT: 8.0,
    StudyDesign.COHORT_PROSPECTIVE: 6.0,
    StudyDesign.COHORT_RETROSPECTIVE: 5.0,
    StudyDesign.CASE_CONTROL: 4.5,
    StudyDesign.CROSS_SECTIONAL: 4.0,
    StudyDesign.CASE_SERIES: 3.0,
    StudyDesign.CASE_REPORT: 2.0,
    StudyDesign.EDITORIAL: 1.5,
    StudyDesign.LETTER: 1.5,
    StudyDesign.COMMENT: 1.0,
    StudyDesign.OTHER: 0.0,
    StudyDesign.UNKNOWN: 0.0,
}


# ---------------------------------------------------------------------------
# Bias risk (Cochrane RoB)
# ---------------------------------------------------------------------------

@dataclass
class BiasRisk:
    """Cochrane Risk-of-Bias across five domains."""
    selection: str = "unclear"     # "low", "unclear", "high"
    performance: str = "unclear"
    detection: str = "unclear"
    attrition: str = "unclear"
    reporting: str = "unclear"

    def to_dict(self) -> dict[str, str]:
        return {
            "selection": self.selection,
            "performance": self.performance,
            "detection": self.detection,
            "attrition": self.attrition,
            "reporting": self.reporting,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BiasRisk:
        valid = ("low", "unclear", "high")
        def v(k): return data.get(k, "unclear") if data.get(k) in valid else "unclear"
        return cls(
            selection=v("selection"), performance=v("performance"),
            detection=v("detection"), attrition=v("attrition"),
            reporting=v("reporting"),
        )


# ---------------------------------------------------------------------------
# Quality assessment result
# ---------------------------------------------------------------------------

@dataclass
class QualityAssessment:
    """Result from any tier of the quality pipeline."""

    assessment_tier: int = 0             # 0=unclassified, 1=metadata, 2=haiku, 3=sonnet
    extraction_method: str = "none"
    study_design: StudyDesign = StudyDesign.UNKNOWN
    quality_tier: QualityTier = QualityTier.UNCLASSIFIED
    quality_score: float = 0.0           # 0–10
    evidence_level: Optional[str] = None # Oxford CEBM level
    is_randomized: Optional[bool] = None
    is_controlled: Optional[bool] = None
    is_blinded: Optional[str] = None     # none / single / double / triple
    is_prospective: Optional[bool] = None
    is_multicenter: Optional[bool] = None
    sample_size: Optional[int] = None
    confidence: float = 0.0              # 0–1
    bias_risk: Optional[BiasRisk] = None
    strengths: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    extraction_details: list[str] = field(default_factory=list)

    # Transparency integration
    transparency_result: Any = None
    original_quality_tier: Optional[QualityTier] = None
    transparency_adjusted: bool = False

    # --- Factories ---

    @classmethod
    def unclassified(cls) -> QualityAssessment:
        return cls()

    @classmethod
    def from_metadata(
        cls,
        design: StudyDesign,
        confidence: float = 0.9,
    ) -> QualityAssessment:
        return cls(
            assessment_tier=1,
            extraction_method="pubmed_metadata",
            study_design=design,
            quality_tier=DESIGN_TO_TIER.get(design, QualityTier.UNCLASSIFIED),
            quality_score=DESIGN_TO_SCORE.get(design, 0.0),
            confidence=confidence,
        )

    @classmethod
    def from_classification(
        cls,
        study_design: StudyDesign,
        confidence: float = 0.7,
        sample_size: Optional[int] = None,
        is_blinded: Optional[str] = None,
    ) -> QualityAssessment:
        return cls(
            assessment_tier=2,
            extraction_method="llm_classifier",
            study_design=study_design,
            quality_tier=DESIGN_TO_TIER.get(study_design, QualityTier.UNCLASSIFIED),
            quality_score=DESIGN_TO_SCORE.get(study_design, 0.0),
            confidence=confidence,
            sample_size=sample_size,
            is_blinded=is_blinded,
        )

    # --- Filtering ---

    def passes_filter(self, qfilter: QualityFilter) -> bool:
        if qfilter.min_tier is not None and self.quality_tier < qfilter.min_tier:
            return False
        if qfilter.require_randomization and not self.is_randomized:
            return False
        if qfilter.require_blinding and self.is_blinded in (None, "none"):
            return False
        if (
            qfilter.min_sample_size is not None
            and self.sample_size is not None
            and self.sample_size < qfilter.min_sample_size
        ):
            return False
        return True

    # --- Serialisation ---

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "assessment_tier": self.assessment_tier,
            "extraction_method": self.extraction_method,
            "study_design": self.study_design.value,
            "quality_tier": self.quality_tier.value,
            "quality_score": self.quality_score,
            "evidence_level": self.evidence_level,
            "is_randomized": self.is_randomized,
            "is_controlled": self.is_controlled,
            "is_blinded": self.is_blinded,
            "is_prospective": self.is_prospective,
            "is_multicenter": self.is_multicenter,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "strengths": self.strengths,
            "limitations": self.limitations,
            "transparency_adjusted": self.transparency_adjusted,
        }
        if self.bias_risk:
            d["bias_risk"] = self.bias_risk.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualityAssessment:
        design_str = data.get("study_design", "unknown")
        design = STUDY_DESIGN_MAPPING.get(design_str, StudyDesign.UNKNOWN)
        bias = BiasRisk.from_dict(data["bias_risk"]) if "bias_risk" in data else None
        return cls(
            assessment_tier=data.get("assessment_tier", 0),
            extraction_method=data.get("extraction_method", "none"),
            study_design=design,
            quality_tier=QualityTier(data.get("quality_tier", 0)),
            quality_score=data.get("quality_score", 0.0),
            evidence_level=data.get("evidence_level"),
            is_randomized=data.get("is_randomized"),
            is_controlled=data.get("is_controlled"),
            is_blinded=data.get("is_blinded"),
            is_prospective=data.get("is_prospective"),
            is_multicenter=data.get("is_multicenter"),
            sample_size=data.get("sample_size"),
            confidence=data.get("confidence", 0.0),
            bias_risk=bias,
            strengths=data.get("strengths", []),
            limitations=data.get("limitations", []),
            transparency_adjusted=data.get("transparency_adjusted", False),
        )


# ---------------------------------------------------------------------------
# Quality filter (user preferences)
# ---------------------------------------------------------------------------

@dataclass
class QualityFilter:
    """User-configurable quality filter thresholds."""
    min_tier: Optional[QualityTier] = None
    require_randomization: bool = False
    require_blinding: bool = False
    min_sample_size: Optional[int] = None
    use_metadata_only: bool = False
    use_llm_classification: bool = True
    use_detailed_assessment: bool = False
