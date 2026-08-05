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
  4. Tier 4: Cochrane-aligned assessment (if explicitly requested), which
     enriches the shallower tier's classification with nine-domain
     risk-of-bias detail rather than replacing it
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence

from bmlib.llm import LLMClient
from bmlib.quality.cochrane_assessor import CochraneAssessor
from bmlib.quality.cochrane_models import CochraneStudyAssessment, collapse_risk_of_bias
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
        llm: LLM client for Tier 2/3/4.
        classifier_model: Model string for Tier 2 (cheap/fast).
        assessor_model: Model string for Tier 3 and Tier 4 (capable).
        template_engine: Optional template engine.
    """

    def __init__(
        self,
        llm: LLMClient,
        classifier_model: str,
        assessor_model: str,
        template_engine: TemplateEngine | None = None,
    ) -> None:
        # Sampling is left to each agent's own defaults (0.1/1024 and
        # 0.2/1024), which is where the reasoning for those numbers lives.
        # The classifier's budget is far above the ~50 tokens its JSON needs
        # because small local models preface it with commentary despite being
        # asked for JSON alone: a tight ceiling truncates that preamble and
        # loses the JSON with it, leaving every paper UNCLASSIFIED.
        self.classifier = StudyClassifier(
            llm=llm,
            model=classifier_model,
            template_engine=template_engine,
        )
        self.assessor = QualityAgent(
            llm=llm,
            model=assessor_model,
            template_engine=template_engine,
        )
        # The Cochrane pass wants a capable model for the same reason Tier 3
        # does, so it shares ``assessor_model``.  A second model parameter
        # buys nothing until someone needs the two to differ.
        self.cochrane = CochraneAssessor(
            llm=llm,
            model=assessor_model,
            template_engine=template_engine,
        )

    def assess(
        self,
        title: str | None,
        abstract: str | None,
        *,
        publication_types: Sequence[str] = (),
        filter_settings: QualityFilter | None = None,
        full_text: str | None = None,
    ) -> QualityAssessment:
        """Run the tiered assessment pipeline for a single paper.

        Args:
            title: Paper title. May be ``None``.
            abstract: Paper abstract. May be ``None`` — sources omit it often
                enough, and a nullable database column delivers the gap that
                way. The LLM tiers work around it rather than raising, so one
                gappy record cannot abort a batch.
            publication_types: PubMed publication types (for Tier 1).
            filter_settings: Controls which tiers are enabled.
            full_text: The paper's full text, for the Cochrane pass. Falls
                back to *abstract* when absent — an abstract yields a weak
                risk-of-bias assessment, but a weak one beats none.
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

        if (
            metadata_is_confident
            and not filt.use_detailed_assessment
            and not filt.use_cochrane_assessment
        ):
            return metadata_result

        # --- Tier 4: Cochrane assessment ---
        # Deeper than Tier 3, so a *successful* pass supersedes Tier 3 exactly
        # as Tier 3 supersedes Tier 2: it is skipped, not run and discarded.
        # "Supersedes" means "runs instead of, when it works", not "suppresses
        # even on failure" — a routine transport failure here must not stop the
        # Tier 3 assessment the caller explicitly enabled from running, so a
        # ``None`` falls through to Tier 3 and then Tier 2 exactly as if
        # ``use_cochrane_assessment`` had not been set.  Tier 2 is a separate
        # question, answered on the success path below: it is not superseded
        # but *depended on*, for the study design Tier 4 does not produce.
        if filt.use_cochrane_assessment:
            cochrane = self.cochrane.assess(title, full_text or abstract)
            if cochrane is not None:
                logger.debug(
                    "Tier 4: Cochrane assessment of %s", cochrane.study_characteristics.study_id
                )
                # A Cochrane assessment carries no study design, so the result
                # it enriches has to supply one.  Tier 1 does when the metadata
                # was conclusive; when it was not, the cheap classifier does.
                # That case is not an edge: a bioRxiv or medRxiv record has no
                # PubMed publication types at all, so Tier 1 is inconclusive
                # for exactly the papers whose full text makes a Cochrane pass
                # worth paying for.  Enriching the unclassified Tier 1 result
                # there returned UNKNOWN at score 0.0 and confidence 0.0 —
                # worse than the Tier 2 answer the caller had left enabled —
                # with a full nine-domain bias table attached to it.  Tier 3
                # stays superseded regardless: it is the expensive tier whose
                # work the Cochrane pass actually replaces.
                base = metadata_result
                if not metadata_is_confident and filt.use_llm_classification:
                    base = self._classify(title, abstract)
                return self._enrich_with_cochrane(base, cochrane)
            logger.debug("Tier 4: no Cochrane assessment; falling through to Tier 3/Tier 2")

        # --- Tier 3: deep assessment ---
        # When a detailed assessment is requested it supersedes Tier 2, so we
        # skip the (cheap but non-free) classifier entirely rather than running
        # it and discarding its result.
        if filt.use_detailed_assessment:
            assessment = self.assessor.assess(title, abstract)
            logger.debug(
                "Tier 3: %s score=%.1f",
                assessment.study_design.value,
                assessment.quality_score,
            )
            return assessment

        # --- Tier 2: LLM classification ---
        if filt.use_llm_classification:
            return self._classify(title, abstract)

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
        keys, and optionally ``"publication_types"`` and ``"full_text"``.

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
                full_text=paper.get("full_text"),
            )
            results.append(assessment)
            if progress_callback:
                progress_callback(i + 1, total, assessment)
        return results

    def _classify(self, title: str | None, abstract: str | None) -> QualityAssessment:
        """Run Tier 2 and log what it decided.

        Shared by the Tier 2 step and the Cochrane branch, which needs a study
        design the Cochrane pass does not produce.  One place, so the two
        cannot drift into logging or calling the classifier differently.

        Args:
            title: Paper title.
            abstract: Paper abstract.

        Returns:
            The classifier's assessment.
        """
        classification = self.classifier.classify(title, abstract)
        logger.debug(
            "Tier 2: %s (confidence %.2f)",
            classification.study_design.value,
            classification.confidence,
        )
        return classification

    @staticmethod
    def _enrich_with_cochrane(
        base: QualityAssessment,
        cochrane: CochraneStudyAssessment,
    ) -> QualityAssessment:
        """Fold a Cochrane assessment into the classification it enriches.

        *base* is the shallower tier's result — Tier 1 when the metadata was
        conclusive, Tier 2 when it was not and the classifier was enabled.  It
        supplies ``study_design``, ``quality_tier``, ``quality_score`` and
        ``confidence``, which a Cochrane assessment does not produce; the
        Cochrane pass supplies the bias detail, which neither of those tiers
        can see.  Neither ``evidence_level`` nor ``confidence`` is copied
        across: Cochrane's ``evidence_level`` is free-form model text where
        this one is Oxford CEBM, and Cochrane's ``overall_confidence``
        describes the model's certainty about the nine bias-risk domains, not
        about the ``study_design`` / ``quality_tier`` / ``quality_score`` this
        method leaves untouched — so overwriting ``confidence`` with it would
        let a caller's ``if a.confidence >= t: trust a.study_design`` pattern
        discard a highly-confident classification because the model was unsure
        about blinding.  Both values stay reachable on the attached object.

        Args:
            base: The classification result to enrich.
            cochrane: The assessment to fold in.

        Returns:
            A new assessment; *base* is not modified.
        """
        # ``replace()`` copies shallowly, so the mutable fields are re-listed:
        # otherwise the "copy" shares them with the original and mutating
        # either rewrites both.
        return dataclasses.replace(
            base,
            assessment_tier=4,
            extraction_method="llm_cochrane_assessment",
            bias_risk=collapse_risk_of_bias(cochrane.risk_of_bias),
            cochrane_assessment=cochrane,
            strengths=list(base.strengths),
            limitations=list(base.limitations),
            extraction_details=[*base.extraction_details, "Cochrane assessment via LLM"],
        )
