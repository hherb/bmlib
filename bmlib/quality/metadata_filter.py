"""Tier 1: PubMed metadata-based quality classification.

Maps PubMed publication types (assigned by NLM indexers) to
:class:`StudyDesign` enums.  Free and instant — always run first.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from bmlib.quality.data_models import (
    QualityAssessment,
    QualityTier,
    StudyDesign,
    DESIGN_TO_TIER,
)

logger = logging.getLogger(__name__)

# Confidence when matched from metadata
METADATA_HIGH_CONFIDENCE = 0.9

# PubMed publication type → StudyDesign
PUBMED_TYPE_TO_DESIGN: dict[str, StudyDesign] = {
    "Systematic Review": StudyDesign.SYSTEMATIC_REVIEW,
    "Meta-Analysis": StudyDesign.META_ANALYSIS,
    "Randomized Controlled Trial": StudyDesign.RCT,
    "Controlled Clinical Trial": StudyDesign.RCT,
    "Clinical Trial": StudyDesign.RCT,
    "Clinical Trial, Phase I": StudyDesign.RCT,
    "Clinical Trial, Phase II": StudyDesign.RCT,
    "Clinical Trial, Phase III": StudyDesign.RCT,
    "Clinical Trial, Phase IV": StudyDesign.RCT,
    "Pragmatic Clinical Trial": StudyDesign.RCT,
    "Equivalence Trial": StudyDesign.RCT,
    "Multicenter Study": StudyDesign.RCT,
    "Observational Study": StudyDesign.COHORT_PROSPECTIVE,
    "Cohort Study": StudyDesign.COHORT_PROSPECTIVE,
    "Longitudinal Study": StudyDesign.COHORT_PROSPECTIVE,
    "Prospective Study": StudyDesign.COHORT_PROSPECTIVE,
    "Retrospective Study": StudyDesign.COHORT_RETROSPECTIVE,
    "Case-Control Study": StudyDesign.CASE_CONTROL,
    "Cross-Sectional Study": StudyDesign.CROSS_SECTIONAL,
    "Twin Study": StudyDesign.CROSS_SECTIONAL,
    "Validation Study": StudyDesign.CROSS_SECTIONAL,
    "Comparative Study": StudyDesign.CROSS_SECTIONAL,
    "Case Reports": StudyDesign.CASE_REPORT,
    "Practice Guideline": StudyDesign.GUIDELINE,
    "Guideline": StudyDesign.GUIDELINE,
    "Consensus Development Conference": StudyDesign.GUIDELINE,
    "Editorial": StudyDesign.EDITORIAL,
    "Letter": StudyDesign.LETTER,
    "Comment": StudyDesign.COMMENT,
    "Review": StudyDesign.OTHER,
    "Published Erratum": StudyDesign.OTHER,
    "Retracted Publication": StudyDesign.OTHER,
}

# Resolution priority (most specific first)
TYPE_PRIORITY: list[str] = [
    "Systematic Review",
    "Meta-Analysis",
    "Randomized Controlled Trial",
    "Controlled Clinical Trial",
    "Pragmatic Clinical Trial",
    "Clinical Trial, Phase III",
    "Clinical Trial, Phase IV",
    "Clinical Trial, Phase II",
    "Clinical Trial, Phase I",
    "Clinical Trial",
    "Case-Control Study",
    "Cohort Study",
    "Longitudinal Study",
    "Prospective Study",
    "Retrospective Study",
    "Cross-Sectional Study",
    "Observational Study",
    "Case Reports",
    "Practice Guideline",
    "Guideline",
    "Editorial",
    "Letter",
    "Comment",
]


def classify_from_metadata(
    publication_types: Sequence[str],
) -> QualityAssessment:
    """Classify study design from PubMed publication types.

    Args:
        publication_types: List of publication type strings from PubMed.

    Returns:
        A :class:`QualityAssessment` at tier 1 (metadata).  Returns
        ``QualityAssessment.unclassified()`` if no types match.
    """
    if not publication_types:
        return QualityAssessment.unclassified()

    type_set = set(publication_types)

    # Walk priority list and take the first match
    for ptype in TYPE_PRIORITY:
        if ptype in type_set:
            design = PUBMED_TYPE_TO_DESIGN[ptype]
            return QualityAssessment.from_metadata(
                design=design,
                confidence=METADATA_HIGH_CONFIDENCE,
            )

    # Try remaining types not in the priority list
    for ptype in publication_types:
        if ptype in PUBMED_TYPE_TO_DESIGN:
            design = PUBMED_TYPE_TO_DESIGN[ptype]
            return QualityAssessment.from_metadata(
                design=design,
                confidence=METADATA_HIGH_CONFIDENCE * 0.8,
            )

    return QualityAssessment.unclassified()
