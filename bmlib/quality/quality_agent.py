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

"""Tier 3: Deep methodological quality assessment.

Uses a more capable model (e.g. Sonnet) for comprehensive assessment
including bias risk, strengths, and limitations.

Cost: ~$0.003 per document.  Use selectively — only when detailed
assessment is explicitly requested.
"""

from __future__ import annotations

import logging

from bmlib.agents.base import BaseAgent
from bmlib.quality.data_models import (
    DESIGN_TO_TIER,
    STUDY_DESIGN_MAPPING,
    BiasRisk,
    QualityAssessment,
    QualityTier,
    StudyDesign,
)

logger = logging.getLogger(__name__)

# Maximum abstract length sent for deep assessment (characters)
MAX_ABSTRACT_CHARS = 4000

ASSESSMENT_SYSTEM_PROMPT = """\
You are a research quality assessment expert.
Evaluate the methodological quality of biomedical research papers.

CRITICAL RULES:
1. Extract ONLY information that is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. If information is unclear or not mentioned, use null or "unclear"
4. Focus on THIS study's methodology, not studies it references
5. Return ONLY valid JSON, no explanation"""


ASSESSMENT_USER_TEMPLATE = """\
Assess this research paper's methodological quality:

Title: {title}
Abstract: {abstract}

Return JSON:
{{
    "study_design": "<see list below>",
    "quality_score": <1-10>,
    "evidence_level": "1a|1b|2a|2b|3a|3b|4|5|null",
    "design_characteristics": {{
        "randomized": true|false|null,
        "controlled": true|false|null,
        "blinded": "none"|"single"|"double"|"triple"|null,
        "prospective": true|false|null,
        "multicenter": true|false|null
    }},
    "sample_size": <number or null>,
    "bias_risk": {{
        "selection": "low"|"unclear"|"high",
        "performance": "low"|"unclear"|"high",
        "detection": "low"|"unclear"|"high",
        "attrition": "low"|"unclear"|"high",
        "reporting": "low"|"unclear"|"high"
    }},
    "strengths": ["2-3 methodological strengths"],
    "limitations": ["2-3 methodological limitations"],
    "confidence": <0.0 to 1.0>
}}

Valid study_design values: systematic_review, meta_analysis, rct,
cohort_prospective, cohort_retrospective, case_control,
cross_sectional, case_series, case_report, editorial,
letter, guideline, other.

Focus on THIS study's methodology, not studies it references."""


class QualityAgent(BaseAgent):
    """Tier 3 deep quality assessor."""

    def assess(
        self,
        title: str,
        abstract: str,
    ) -> QualityAssessment:
        """Perform detailed quality assessment.

        Returns a Tier 3 :class:`QualityAssessment`.  On failure,
        returns ``QualityAssessment.unclassified()``.
        """
        prompt = ASSESSMENT_USER_TEMPLATE.format(
            title=title,
            abstract=abstract[:MAX_ABSTRACT_CHARS],
        )

        try:
            data = self.chat_json(
                messages=[
                    self.system_msg(ASSESSMENT_SYSTEM_PROMPT),
                    self.user_msg(prompt),
                ],
                temperature=0.2,
                max_tokens=1024,
            )
            return self._parse_data(data)
        except Exception as e:
            logger.warning("Quality assessment failed after retries: %s", e)
            return QualityAssessment.unclassified()

    def _parse_data(self, data: dict) -> QualityAssessment:
        """Convert parsed JSON dict into a :class:`QualityAssessment`."""
        design_str = data.get("study_design", "unknown").lower().strip()
        design = STUDY_DESIGN_MAPPING.get(design_str, StudyDesign.UNKNOWN)

        chars = data.get("design_characteristics", {})
        bias_data = data.get("bias_risk", {})

        blinding = chars.get("blinded")
        if blinding not in ("none", "single", "double", "triple"):
            blinding = None

        sample_size = None
        if data.get("sample_size") is not None:
            try:
                sample_size = int(data["sample_size"])
            except (ValueError, TypeError):
                pass

        quality_score = max(0.0, min(10.0, float(data.get("quality_score", 0))))
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))

        return QualityAssessment(
            assessment_tier=3,
            extraction_method="llm_deep_assessment",
            study_design=design,
            quality_tier=DESIGN_TO_TIER.get(design, QualityTier.UNCLASSIFIED),
            quality_score=quality_score,
            evidence_level=data.get("evidence_level"),
            is_randomized=chars.get("randomized"),
            is_controlled=chars.get("controlled"),
            is_blinded=blinding,
            is_prospective=chars.get("prospective"),
            is_multicenter=chars.get("multicenter"),
            sample_size=sample_size,
            confidence=confidence,
            bias_risk=BiasRisk.from_dict(bias_data),
            strengths=data.get("strengths", []),
            limitations=data.get("limitations", []),
            extraction_details=["Detailed assessment via LLM"],
        )
