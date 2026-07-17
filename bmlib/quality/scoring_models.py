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

"""Audit-trail models for multi-dimensional quality scoring.

A :class:`DimensionScore` holds one dimension's score plus a list of
:class:`AssessmentDetail` entries recording *what* was extracted, *how much*
it contributed, and *why*. Rule-based extractors (see
:mod:`bmlib.quality.extractors`) and LLM assessors both populate these,
giving a fully reproducible per-component audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Dimension names (the multi-dimensional evidence-weight vocabulary).
DIMENSION_STUDY_DESIGN = "study_design"
DIMENSION_SAMPLE_SIZE = "sample_size"
DIMENSION_METHODOLOGICAL_QUALITY = "methodological_quality"
DIMENSION_RISK_OF_BIAS = "risk_of_bias"
DIMENSION_REPLICATION_STATUS = "replication_status"

ALL_DIMENSIONS = [
    DIMENSION_STUDY_DESIGN,
    DIMENSION_SAMPLE_SIZE,
    DIMENSION_METHODOLOGICAL_QUALITY,
    DIMENSION_RISK_OF_BIAS,
    DIMENSION_REPLICATION_STATUS,
]


@dataclass
class AssessmentDetail:
    """One audit-trail entry for a scored component.

    Attributes:
        dimension: Dimension name (e.g. "study_design", "sample_size").
        component: Specific component assessed (e.g. "randomization").
        extracted_value: Value found in the paper (e.g. "double-blind", "450").
        score_contribution: Points contributed to the dimension score.
        evidence_text: Relevant excerpt from the paper, if any.
        reasoning: Explanation for the score, if any.
    """

    dimension: str
    component: str
    extracted_value: str | None
    score_contribution: float
    evidence_text: str | None = None
    reasoning: str | None = None

    def to_dict(self) -> dict:
        """Serialise to a dict."""
        return {
            "dimension": self.dimension,
            "component": self.component,
            "extracted_value": self.extracted_value,
            "score_contribution": self.score_contribution,
            "evidence_text": self.evidence_text,
            "reasoning": self.reasoning,
        }


@dataclass
class DimensionScore:
    """A single dimension's score with its contributing audit-trail entries.

    Attributes:
        dimension_name: Name of this dimension.
        score: Final score for this dimension (typically 0-10).
        details: Component assessments that contributed to the score.
    """

    dimension_name: str
    score: float
    details: list[AssessmentDetail] = field(default_factory=list)

    def add_detail(
        self,
        component: str,
        value: str,
        contribution: float,
        evidence: str | None = None,
        reasoning: str | None = None,
    ) -> None:
        """Append an audit-trail entry for a component of this dimension."""
        self.details.append(
            AssessmentDetail(
                dimension=self.dimension_name,
                component=component,
                extracted_value=value,
                score_contribution=contribution,
                evidence_text=evidence,
                reasoning=reasoning,
            )
        )

    def to_dict(self) -> dict:
        """Serialise to a dict, including all detail entries."""
        return {
            "dimension_name": self.dimension_name,
            "score": self.score,
            "details": [d.to_dict() for d in self.details],
        }
