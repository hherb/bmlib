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

"""Tier 1: PubMed metadata-based quality classification.

Maps PubMed publication types (assigned by NLM indexers) to
:class:`StudyDesign` enums.  Free and instant — always run first.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from bmlib.quality.data_models import (
    QualityAssessment,
    StudyDesign,
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


def _normalize_type(s: str) -> str:
    """Normalize a publication type string for case-insensitive matching."""
    return s.strip().lower().replace("-", " ").replace("_", " ")


# Build a case-insensitive lookup: normalized form → canonical PubMed key
_NORMALIZED_LOOKUP: dict[str, str] = {
    _normalize_type(k): k for k in PUBMED_TYPE_TO_DESIGN
}


def classify_from_metadata(
    publication_types: Sequence[str],
) -> QualityAssessment:
    """Classify study design from PubMed publication types.

    Performs case-insensitive matching and normalizes hyphens/underscores,
    so ``"systematic review"``, ``"Systematic Review"``, and
    ``"systematic-review"`` all match.

    Args:
        publication_types: List of publication type strings from PubMed
            or other sources (EuropePMC, categories, etc.).

    Returns:
        A :class:`QualityAssessment` at tier 1 (metadata).  Returns
        ``QualityAssessment.unclassified()`` if no types match.
    """
    if not publication_types:
        return QualityAssessment.unclassified()

    # Build normalized set for O(1) lookup
    normalized_inputs = {_normalize_type(t): t for t in publication_types}

    # Walk priority list and take the first match (case-insensitive)
    for ptype in TYPE_PRIORITY:
        if _normalize_type(ptype) in normalized_inputs:
            design = PUBMED_TYPE_TO_DESIGN[ptype]
            return QualityAssessment.from_metadata(
                design=design,
                confidence=METADATA_HIGH_CONFIDENCE,
            )

    # Try remaining known types not in the priority list
    for norm_input in normalized_inputs:
        if norm_input in _NORMALIZED_LOOKUP:
            canonical = _NORMALIZED_LOOKUP[norm_input]
            design = PUBMED_TYPE_TO_DESIGN[canonical]
            return QualityAssessment.from_metadata(
                design=design,
                confidence=METADATA_HIGH_CONFIDENCE * 0.8,
            )

    logger.debug(
        "No metadata classification match for types: %s", list(publication_types)
    )
    return QualityAssessment.unclassified()
