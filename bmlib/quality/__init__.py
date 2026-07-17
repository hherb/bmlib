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

"""Tiered quality assessment for biomedical publications.

Three-tier pipeline (cheapest first, escalating on demand):

- **Tier 1**: PubMed metadata classification (free, instant)
- **Tier 2**: LLM study-design classification (cheap model)
- **Tier 3**: Deep methodological assessment (capable model)
"""

from bmlib.quality.cochrane_models import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
)
from bmlib.quality.data_models import (
    DESIGN_TO_SCORE,
    DESIGN_TO_TIER,
    BiasRisk,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    StudyDesign,
)
from bmlib.quality.extractors import (
    extract_sample_size_dimension,
    extract_study_type,
)
from bmlib.quality.manager import QualityManager
from bmlib.quality.scoring_models import AssessmentDetail, DimensionScore

__all__ = [
    "BiasRisk",
    "QualityAssessment",
    "QualityFilter",
    "QualityManager",
    "QualityTier",
    "StudyDesign",
    "DESIGN_TO_TIER",
    "DESIGN_TO_SCORE",
    # Cochrane-aligned models
    "CochraneInterventions",
    "CochraneNotes",
    "CochraneOutcomes",
    "CochraneParticipants",
    "CochraneRiskOfBias",
    "CochraneStudyAssessment",
    "CochraneStudyCharacteristics",
    "RiskOfBiasItem",
    "RiskOfBiasJudgement",
    "create_default_cochrane_risk_of_bias",
    "create_default_risk_of_bias_item",
    # Rule-based extractors + audit-trail scoring models
    "AssessmentDetail",
    "DimensionScore",
    "extract_sample_size_dimension",
    "extract_study_type",
]
