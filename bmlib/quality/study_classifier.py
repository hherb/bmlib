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

"""Tier 2: LLM-based study-design classification.

Uses a cheap/fast model (e.g. Haiku, or a local model via Ollama) to
classify study design from title + abstract.  Cost: ~$0.001 per document.
"""

from __future__ import annotations

import logging

from bmlib.agents.base import BaseAgent
from bmlib.quality.data_models import (
    STUDY_DESIGN_MAPPING,
    QualityAssessment,
    StudyDesign,
)

logger = logging.getLogger(__name__)

# Maximum abstract length sent to the classifier (characters)
MAX_ABSTRACT_CHARS = 3000

CLASSIFIER_SYSTEM_PROMPT = """\
You are a biomedical study design classifier.  Classify the paper's OWN
methodology — NOT the methodology of studies it references.

Focus on language like "this study", "we conducted", "our analysis".
Ignore phrases like "previous studies have shown" or "a recent meta-analysis found".

Return ONLY valid JSON, no explanation."""


CLASSIFIER_USER_TEMPLATE = """\
Classify this paper's study design:

Title: {title}
Abstract: {abstract}

Return JSON:
{{
    "study_design": "<see list below>",
    "confidence": <0.0 to 1.0>,
    "sample_size": <number or null>,
    "blinding": "none|single|double|triple|null"
}}

Valid study_design values: systematic_review, meta_analysis, rct,
cohort_prospective, cohort_retrospective, case_control,
cross_sectional, case_series, case_report, editorial,
letter, guideline, other, unknown."""


class StudyClassifier(BaseAgent):
    """Tier 2 study-design classifier using a cheap LLM."""

    def classify(
        self,
        title: str,
        abstract: str,
    ) -> QualityAssessment:
        """Classify study design from title and abstract.

        Returns a Tier 2 :class:`QualityAssessment`.  On failure,
        returns ``QualityAssessment.unclassified()``.
        """
        prompt = CLASSIFIER_USER_TEMPLATE.format(
            title=title,
            abstract=abstract[:MAX_ABSTRACT_CHARS],
        )

        try:
            response = self.chat(
                messages=[
                    self.system_msg(CLASSIFIER_SYSTEM_PROMPT),
                    self.user_msg(prompt),
                ],
                json_mode=True,
                temperature=0.1,
                max_tokens=256,
            )
            return self._parse(response.content)
        except Exception as e:
            logger.warning("Study classification failed: %s", e)
            return QualityAssessment.unclassified()

    def _parse(self, text: str) -> QualityAssessment:
        """Parse the LLM JSON response into a :class:`QualityAssessment`."""
        data = self.parse_json(text)
        design_str = data.get("study_design", "unknown").lower().strip()
        design = STUDY_DESIGN_MAPPING.get(design_str, StudyDesign.UNKNOWN)
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))

        sample_size = None
        if data.get("sample_size") is not None:
            try:
                sample_size = int(data["sample_size"])
            except (ValueError, TypeError):
                pass

        blinding = data.get("blinding")
        if blinding not in ("none", "single", "double", "triple"):
            blinding = None

        return QualityAssessment.from_classification(
            study_design=design,
            confidence=confidence,
            sample_size=sample_size,
            is_blinded=blinding,
        )
