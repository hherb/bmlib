"""Tier 3: Deep methodological quality assessment.

Uses a more capable model (e.g. Sonnet) for comprehensive assessment
including bias risk, strengths, and limitations.

Cost: ~$0.003 per document.  Use selectively — only when detailed
assessment is explicitly requested.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from bmlib.agents.base import BaseAgent
from bmlib.quality.data_models import (
    BiasRisk,
    QualityAssessment,
    QualityTier,
    StudyDesign,
    STUDY_DESIGN_MAPPING,
    DESIGN_TO_TIER,
)

logger = logging.getLogger(__name__)

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
    "study_design": "systematic_review|meta_analysis|rct|cohort_prospective|cohort_retrospective|case_control|cross_sectional|case_series|case_report|editorial|letter|guideline|other",
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
            abstract=abstract[:4000],
        )

        try:
            response = self.chat(
                messages=[
                    self.system_msg(ASSESSMENT_SYSTEM_PROMPT),
                    self.user_msg(prompt),
                ],
                json_mode=True,
                temperature=0.2,
                max_tokens=1024,
            )
            return self._parse(response.content)
        except Exception as e:
            logger.warning("Quality assessment failed: %s", e)
            return QualityAssessment.unclassified()

    def _parse(self, text: str) -> QualityAssessment:
        data = self.parse_json(text)

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
