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

"""Quality Manager — orchestrates the tiered assessment pipeline.

Assessment flow:
  1. Tier 1: PubMed metadata classification (free, instant)
  2. Tier 2: LLM classification via cheap model (if metadata inconclusive)
  3. Tier 3: Deep assessment via capable model (if explicitly requested)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from bmlib.llm import LLMClient
from bmlib.quality.data_models import (
    QualityAssessment,
    QualityFilter,
    QualityTier,
)
from bmlib.quality.metadata_filter import classify_from_metadata
from bmlib.quality.quality_agent import QualityAgent
from bmlib.quality.study_classifier import StudyClassifier
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

# Accept Tier 1 result without LLM fallback if confidence ≥ this
METADATA_ACCEPTANCE_THRESHOLD = 0.9


class QualityManager:
    """Orchestrates tiered quality assessment.

    Args:
        llm: LLM client for Tier 2/3.
        classifier_model: Model string for Tier 2 (cheap/fast).
        assessor_model: Model string for Tier 3 (capable).
        template_engine: Optional template engine.
    """

    def __init__(
        self,
        llm: LLMClient,
        classifier_model: str,
        assessor_model: str,
        template_engine: TemplateEngine | None = None,
    ) -> None:
        self.classifier = StudyClassifier(
            llm=llm,
            model=classifier_model,
            template_engine=template_engine,
            temperature=0.1,
            max_tokens=256,
        )
        self.assessor = QualityAgent(
            llm=llm,
            model=assessor_model,
            template_engine=template_engine,
            temperature=0.2,
            max_tokens=1024,
        )

    def assess(
        self,
        title: str,
        abstract: str,
        *,
        publication_types: Sequence[str] = (),
        filter_settings: QualityFilter | None = None,
    ) -> QualityAssessment:
        """Run the tiered assessment pipeline for a single paper.

        Args:
            title: Paper title.
            abstract: Paper abstract.
            publication_types: PubMed publication types (for Tier 1).
            filter_settings: Controls which tiers are enabled.
        """
        filt = filter_settings or QualityFilter()

        # --- Tier 1: metadata ---
        metadata_result = classify_from_metadata(publication_types)
        if metadata_result.quality_tier != QualityTier.UNCLASSIFIED:
            logger.debug(
                "Tier 1: %s (confidence %.2f)",
                metadata_result.study_design.value,
                metadata_result.confidence,
            )

        if filt.use_metadata_only:
            return metadata_result

        metadata_is_confident = (
            metadata_result.confidence >= METADATA_ACCEPTANCE_THRESHOLD
            and metadata_result.quality_tier != QualityTier.UNCLASSIFIED
        )

        if metadata_is_confident and not filt.use_detailed_assessment:
            return metadata_result

        # --- Tier 2: LLM classification ---
        if filt.use_llm_classification:
            classification = self.classifier.classify(title, abstract)
            logger.debug(
                "Tier 2: %s (confidence %.2f)",
                classification.study_design.value,
                classification.confidence,
            )

            if not filt.use_detailed_assessment:
                return classification

        # --- Tier 3: deep assessment ---
        if filt.use_detailed_assessment:
            assessment = self.assessor.assess(title, abstract)
            logger.debug(
                "Tier 3: %s score=%.1f",
                assessment.study_design.value,
                assessment.quality_score,
            )
            return assessment

        # Fallback
        return metadata_result

    def assess_batch(
        self,
        papers: list[dict],
        *,
        filter_settings: QualityFilter | None = None,
        progress_callback: Callable[[int, int, QualityAssessment], None] | None = None,
    ) -> list[QualityAssessment]:
        """Assess a batch of papers.

        Each dict in *papers* should have ``"title"`` and ``"abstract"``
        keys, and optionally ``"publication_types"``.

        Args:
            papers: List of paper dicts.
            filter_settings: Controls which tiers are enabled.
            progress_callback: Optional ``(current, total, assessment)`` callback.

        Returns:
            List of assessments (same order as input).
        """
        results: list[QualityAssessment] = []
        total = len(papers)
        for i, paper in enumerate(papers):
            assessment = self.assess(
                title=paper.get("title", ""),
                abstract=paper.get("abstract", ""),
                publication_types=paper.get("publication_types", ()),
                filter_settings=filter_settings,
            )
            results.append(assessment)
            if progress_callback:
                progress_callback(i + 1, total, assessment)
        return results
