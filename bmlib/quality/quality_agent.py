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
from bmlib.llm import LLMClient
from bmlib.quality._json_fields import (
    as_bool,
    as_design,
    as_dict,
    as_float,
    as_int,
    as_str_list,
    as_text,
)
from bmlib.quality.data_models import (
    DESIGN_TO_TIER,
    BiasRisk,
    QualityAssessment,
    QualityTier,
)
from bmlib.templates import TemplateEngine

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
    """Tier 3 deep quality assessor.

    As with :class:`~bmlib.quality.study_classifier.StudyClassifier`, the
    sampling defaults live here rather than at the call site so they hold
    however the agent is constructed.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> None:
        super().__init__(
            llm=llm,
            model=model,
            template_engine=template_engine,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def assess(
        self,
        title: str | None,
        abstract: str | None,
    ) -> QualityAssessment:
        """Perform detailed quality assessment.

        As in the Tier 2 classifier, either field may be ``None``: a gap is
        something to work around, not a reason to abort the caller's batch.
        With both missing there is nothing to assess and no LLM call is made.

        Returns a Tier 3 :class:`QualityAssessment`.  On failure,
        returns ``QualityAssessment.unclassified()``.
        """
        title = title or ""
        abstract = abstract or ""
        if not title.strip() and not abstract.strip():
            # Left to itself the model would return fully-formed strengths and
            # limitations for a paper it was told nothing about.
            logger.warning("Cannot assess: both title and abstract are empty")
            return QualityAssessment.unclassified()

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
                # _parse_data() calls .get(): a top-level array would raise
                # AttributeError into the handler below and degrade the paper
                # to UNCLASSIFIED without ever retrying.
                require_dict=True,
            )
            return self._parse_data(data)
        except Exception as e:
            logger.warning("Quality assessment failed after retries: %s", e)
            return QualityAssessment.unclassified()

    def _parse_data(self, data: dict) -> QualityAssessment:
        """Convert parsed JSON dict into a :class:`QualityAssessment`.

        Every value is narrowed to the type its field holds, with absent,
        ``null`` and wrong-typed all reading as unstated (see
        :mod:`bmlib.quality._json_fields`).  The prompt tells the model to
        answer ``null`` for what the text does not report, and a ``null``
        section used to raise here — after ``chat_json`` had returned, so no
        retry ran and ``assess()`` degraded the paper to UNCLASSIFIED, which
        ``QualityManager`` then let replace a conclusive Tier 1 result (#295).
        """
        design = as_design(data.get("study_design"), "study_design")

        chars = as_dict(data.get("design_characteristics"), "design_characteristics")
        bias_data = as_dict(data.get("bias_risk"), "bias_risk")

        blinding = chars.get("blinded")
        if blinding not in ("none", "single", "double", "triple"):
            blinding = None

        quality_score = as_float(data.get("quality_score"), "quality_score")
        confidence = as_float(data.get("confidence"), "confidence")

        return QualityAssessment(
            assessment_tier=3,
            extraction_method="llm_deep_assessment",
            study_design=design,
            quality_tier=DESIGN_TO_TIER.get(design, QualityTier.UNCLASSIFIED),
            quality_score=max(0.0, min(10.0, 0.0 if quality_score is None else quality_score)),
            evidence_level=as_text(data.get("evidence_level"), "evidence_level"),
            # A flag is a JSON boolean or unstated.  ``require_randomization``
            # tests ``not is_randomized``, so any non-empty string passed it:
            # ``"no"`` and ``"unclear"`` — the second being the word this
            # prompt offers for anything unclear — both admitted a paper as
            # randomised.  Absent stays ``None``, not ``False``, so a model
            # that said nothing is not recorded as denying it.
            is_randomized=as_bool(chars.get("randomized"), "randomized"),
            is_controlled=as_bool(chars.get("controlled"), "controlled"),
            is_blinded=blinding,
            is_prospective=as_bool(chars.get("prospective"), "prospective"),
            is_multicenter=as_bool(chars.get("multicenter"), "multicenter"),
            sample_size=as_int(data.get("sample_size"), "sample_size"),
            confidence=max(0.0, min(1.0, 0.5 if confidence is None else confidence)),
            bias_risk=BiasRisk.from_dict(bias_data),
            strengths=as_str_list(data.get("strengths"), "strengths") or [],
            limitations=as_str_list(data.get("limitations"), "limitations") or [],
            extraction_details=["Detailed assessment via LLM"],
        )
