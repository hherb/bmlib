"""Tiered quality assessment for biomedical publications.

Three-tier pipeline (cheapest first, escalating on demand):

- **Tier 1**: PubMed metadata classification (free, instant)
- **Tier 2**: LLM study-design classification (cheap model)
- **Tier 3**: Deep methodological assessment (capable model)
"""

from bmlib.quality.data_models import (
    BiasRisk,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    StudyDesign,
    DESIGN_TO_TIER,
    DESIGN_TO_SCORE,
)
from bmlib.quality.manager import QualityManager

__all__ = [
    "BiasRisk",
    "QualityAssessment",
    "QualityFilter",
    "QualityManager",
    "QualityTier",
    "StudyDesign",
    "DESIGN_TO_TIER",
    "DESIGN_TO_SCORE",
]
