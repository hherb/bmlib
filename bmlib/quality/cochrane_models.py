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

"""Cochrane-aligned data models for study assessment.

Models that match the Cochrane Handbook requirements for systematic
reviews:

- **Study characteristics table** — Methods, Participants, Interventions,
  Outcomes, Notes.
- **Risk of Bias assessment** — the nine standard Cochrane RoB domains,
  each with a judgement and supporting text.

These are a strict superset of :class:`bmlib.quality.BiasRisk`: where
``BiasRisk`` records five domains as bare strings, this captures nine
domains with rationale plus the full study-characteristics table.

Reference: Cochrane Handbook for Systematic Reviews of Interventions
(https://training.cochrane.org/handbook).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Risk of bias judgement options (Cochrane standard).
ROB_JUDGEMENT_LOW = "Low risk"
ROB_JUDGEMENT_HIGH = "High risk"
ROB_JUDGEMENT_UNCLEAR = "Unclear risk"

# Valid judgement values for validation.
VALID_ROB_JUDGEMENTS = {ROB_JUDGEMENT_LOW, ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_UNCLEAR}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskOfBiasJudgement(Enum):
    """Cochrane Risk of Bias judgement categories."""

    LOW = "Low risk"
    HIGH = "High risk"
    UNCLEAR = "Unclear risk"

    @classmethod
    def from_string(cls, value: str) -> RiskOfBiasJudgement:
        """Convert a string to a judgement, tolerating case and variations.

        Unknown values fall back to :attr:`UNCLEAR` (with a warning).
        """
        value_lower = value.lower().strip()

        if value_lower in ("low", "low risk", "low_risk"):
            return cls.LOW
        if value_lower in ("high", "high risk", "high_risk"):
            return cls.HIGH
        if value_lower in ("unclear", "unclear risk", "unclear_risk", "unknown"):
            return cls.UNCLEAR

        logger.warning("Unknown RoB judgement '%s', defaulting to UNCLEAR", value)
        return cls.UNCLEAR


# ---------------------------------------------------------------------------
# Risk of Bias data models
# ---------------------------------------------------------------------------


@dataclass
class RiskOfBiasItem:
    """A single risk-of-bias domain assessment.

    Attributes:
        domain: Name of the bias domain (e.g. "Random sequence generation").
        bias_type: Category of bias (e.g. "selection bias").
        judgement: One of "Low risk", "High risk", "Unclear risk".
        support_for_judgement: Text explaining the basis for the judgement.
        outcome_type: For detection bias, "subjective" or "objective".
    """

    domain: str
    bias_type: str
    judgement: str
    support_for_judgement: str
    outcome_type: str | None = None

    def __post_init__(self) -> None:
        """Warn if the judgement is not one of the allowed values."""
        if self.judgement not in VALID_ROB_JUDGEMENTS:
            logger.warning(
                "Invalid RoB judgement '%s' for domain '%s', expected one of %s",
                self.judgement,
                self.domain,
                VALID_ROB_JUDGEMENTS,
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict (``outcome_type`` omitted when unset)."""
        result = {
            "domain": self.domain,
            "bias_type": self.bias_type,
            "judgement": self.judgement,
            "support_for_judgement": self.support_for_judgement,
        }
        if self.outcome_type:
            result["outcome_type"] = self.outcome_type
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskOfBiasItem:
        """Build a :class:`RiskOfBiasItem` from a dict."""
        return cls(
            domain=data["domain"],
            bias_type=data["bias_type"],
            judgement=data["judgement"],
            support_for_judgement=data["support_for_judgement"],
            outcome_type=data.get("outcome_type"),
        )


@dataclass
class CochraneRiskOfBias:
    """Complete Cochrane Risk of Bias assessment across nine domains.

    Selection bias (4): random sequence generation, allocation concealment,
    baseline outcome measurements, baseline characteristics. Performance bias
    (1): blinding of participants and personnel. Detection bias (2): blinding
    of outcome assessment, split by subjective/objective outcomes. Attrition
    bias (1): incomplete outcome data. Reporting bias (1): selective reporting.
    """

    random_sequence_generation: RiskOfBiasItem
    allocation_concealment: RiskOfBiasItem
    baseline_outcome_measurements: RiskOfBiasItem
    baseline_characteristics: RiskOfBiasItem
    blinding_participants_personnel: RiskOfBiasItem
    blinding_outcome_assessment_subjective: RiskOfBiasItem
    blinding_outcome_assessment_objective: RiskOfBiasItem
    incomplete_outcome_data: RiskOfBiasItem
    selective_reporting: RiskOfBiasItem

    def to_dict(self) -> dict[str, Any]:
        """Serialise all nine domains to a dict."""
        return {
            "random_sequence_generation": self.random_sequence_generation.to_dict(),
            "allocation_concealment": self.allocation_concealment.to_dict(),
            "baseline_outcome_measurements": self.baseline_outcome_measurements.to_dict(),
            "baseline_characteristics": self.baseline_characteristics.to_dict(),
            "blinding_participants_personnel": self.blinding_participants_personnel.to_dict(),
            "blinding_outcome_assessment_subjective": (
                self.blinding_outcome_assessment_subjective.to_dict()
            ),
            "blinding_outcome_assessment_objective": (
                self.blinding_outcome_assessment_objective.to_dict()
            ),
            "incomplete_outcome_data": self.incomplete_outcome_data.to_dict(),
            "selective_reporting": self.selective_reporting.to_dict(),
        }

    def to_list(self) -> list[RiskOfBiasItem]:
        """Return the domains as a list in Cochrane table order."""
        return [
            self.random_sequence_generation,
            self.allocation_concealment,
            self.baseline_outcome_measurements,
            self.baseline_characteristics,
            self.blinding_participants_personnel,
            self.blinding_outcome_assessment_subjective,
            self.blinding_outcome_assessment_objective,
            self.incomplete_outcome_data,
            self.selective_reporting,
        ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneRiskOfBias:
        """Build a :class:`CochraneRiskOfBias` from a dict."""
        return cls(
            random_sequence_generation=RiskOfBiasItem.from_dict(data["random_sequence_generation"]),
            allocation_concealment=RiskOfBiasItem.from_dict(data["allocation_concealment"]),
            baseline_outcome_measurements=RiskOfBiasItem.from_dict(
                data["baseline_outcome_measurements"]
            ),
            baseline_characteristics=RiskOfBiasItem.from_dict(data["baseline_characteristics"]),
            blinding_participants_personnel=RiskOfBiasItem.from_dict(
                data["blinding_participants_personnel"]
            ),
            blinding_outcome_assessment_subjective=RiskOfBiasItem.from_dict(
                data["blinding_outcome_assessment_subjective"]
            ),
            blinding_outcome_assessment_objective=RiskOfBiasItem.from_dict(
                data["blinding_outcome_assessment_objective"]
            ),
            incomplete_outcome_data=RiskOfBiasItem.from_dict(data["incomplete_outcome_data"]),
            selective_reporting=RiskOfBiasItem.from_dict(data["selective_reporting"]),
        )

    def get_summary_counts(self) -> dict[str, int]:
        """Count how many domains fall into each judgement category."""
        counts = {
            ROB_JUDGEMENT_LOW: 0,
            ROB_JUDGEMENT_HIGH: 0,
            ROB_JUDGEMENT_UNCLEAR: 0,
        }
        for item in self.to_list():
            if item.judgement in counts:
                counts[item.judgement] += 1
        return counts


# ---------------------------------------------------------------------------
# Study characteristics data models
# ---------------------------------------------------------------------------


@dataclass
class CochraneParticipants:
    """Participants section of the Cochrane study-characteristics table."""

    setting: str
    population: str
    inclusion_criteria: list[str] | None = None
    exclusion_criteria: list[str] | None = None
    total_participants: int | None = None
    group_sizes: dict[str, int] | None = None
    baseline_characteristics_reported: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict."""
        return {
            "setting": self.setting,
            "population": self.population,
            "inclusion_criteria": self.inclusion_criteria,
            "exclusion_criteria": self.exclusion_criteria,
            "total_participants": self.total_participants,
            "group_sizes": self.group_sizes,
            "baseline_characteristics_reported": self.baseline_characteristics_reported,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneParticipants:
        """Build from a dict, defaulting missing text to "Not reported"."""
        return cls(
            setting=data.get("setting", "Not reported"),
            population=data.get("population", "Not reported"),
            inclusion_criteria=data.get("inclusion_criteria"),
            exclusion_criteria=data.get("exclusion_criteria"),
            total_participants=data.get("total_participants"),
            group_sizes=data.get("group_sizes"),
            baseline_characteristics_reported=data.get("baseline_characteristics_reported", False),
        )

    def format_for_table(self) -> str:
        """Format participant info for the characteristics table."""
        lines = [f"Setting: {self.setting}", "", self.population]

        if self.total_participants:
            if self.group_sizes:
                group_str = ", ".join(f"{k}: {v}" for k, v in self.group_sizes.items())
                lines.append(f"N={self.total_participants} ({group_str})")
            else:
                lines.append(f"N={self.total_participants}")

        return "\n".join(lines)


@dataclass
class CochraneInterventions:
    """Interventions section of the Cochrane study-characteristics table."""

    description: str
    intervention_groups: list[str] | None = None
    control_description: str | None = None
    duration: str | None = None
    setting: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict."""
        return {
            "description": self.description,
            "intervention_groups": self.intervention_groups,
            "control_description": self.control_description,
            "duration": self.duration,
            "setting": self.setting,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneInterventions:
        """Build from a dict, defaulting a missing description."""
        return cls(
            description=data.get("description", "Not reported"),
            intervention_groups=data.get("intervention_groups"),
            control_description=data.get("control_description"),
            duration=data.get("duration"),
            setting=data.get("setting"),
        )


@dataclass
class CochraneOutcomes:
    """Outcomes section of the Cochrane study-characteristics table."""

    description: str
    primary_outcomes: list[str] | None = None
    secondary_outcomes: list[str] | None = None
    outcome_timepoints: list[str] | None = None
    outcome_assessment_methods: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict."""
        return {
            "description": self.description,
            "primary_outcomes": self.primary_outcomes,
            "secondary_outcomes": self.secondary_outcomes,
            "outcome_timepoints": self.outcome_timepoints,
            "outcome_assessment_methods": self.outcome_assessment_methods,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneOutcomes:
        """Build from a dict, defaulting a missing description."""
        return cls(
            description=data.get("description", "Not reported"),
            primary_outcomes=data.get("primary_outcomes"),
            secondary_outcomes=data.get("secondary_outcomes"),
            outcome_timepoints=data.get("outcome_timepoints"),
            outcome_assessment_methods=data.get("outcome_assessment_methods"),
        )


@dataclass
class CochraneNotes:
    """Notes section of the Cochrane study-characteristics table.

    Captures follow-up, funding, conflicts of interest, ethics, and trial
    registration — the transparency-relevant metadata Cochrane requires.
    """

    follow_up_periods: list[str] | None = None
    funding_source: str | None = None
    conflicts_of_interest: str | None = None
    ethical_approval: str | None = None
    trial_registration: str | None = None
    publication_status: str | None = None
    additional_notes: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict."""
        return {
            "follow_up_periods": self.follow_up_periods,
            "funding_source": self.funding_source,
            "conflicts_of_interest": self.conflicts_of_interest,
            "ethical_approval": self.ethical_approval,
            "trial_registration": self.trial_registration,
            "publication_status": self.publication_status,
            "additional_notes": self.additional_notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneNotes:
        """Build from a dict."""
        return cls(
            follow_up_periods=data.get("follow_up_periods"),
            funding_source=data.get("funding_source"),
            conflicts_of_interest=data.get("conflicts_of_interest"),
            ethical_approval=data.get("ethical_approval"),
            trial_registration=data.get("trial_registration"),
            publication_status=data.get("publication_status"),
            additional_notes=data.get("additional_notes"),
        )

    def format_for_table(self) -> str:
        """Format the notes for the characteristics table."""
        lines = []

        if self.follow_up_periods:
            lines.append(f"Follow-up at {', '.join(self.follow_up_periods)}")
        if self.funding_source:
            lines.append(f"Funding: {self.funding_source}")
        if self.conflicts_of_interest:
            lines.append(f"Conflicts of interest: {self.conflicts_of_interest}")
        if self.ethical_approval:
            lines.append(f"Ethical approval: {self.ethical_approval}")
        if self.trial_registration:
            lines.append(f"Trial registration: {self.trial_registration}")
        if self.publication_status:
            lines.append(f"Publication status: {self.publication_status}")
        if self.additional_notes:
            lines.extend(self.additional_notes)

        return "\n\n".join(lines) if lines else "No additional notes"


@dataclass
class CochraneStudyCharacteristics:
    """The complete Cochrane study-characteristics table for one study.

    Five sections — Methods, Participants, Interventions, Outcomes, Notes —
    plus optional identifying metadata.
    """

    study_id: str
    methods: str
    participants: CochraneParticipants
    interventions: CochraneInterventions
    outcomes: CochraneOutcomes
    notes: CochraneNotes

    document_id: int | None = None
    document_title: str | None = None
    pmid: str | None = None
    doi: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Stamp a UTC creation time when none was supplied."""
        if self.created_at is None:
            self.created_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict (``created_at`` as ISO 8601)."""
        return {
            "study_id": self.study_id,
            "methods": self.methods,
            "participants": self.participants.to_dict(),
            "interventions": self.interventions.to_dict(),
            "outcomes": self.outcomes.to_dict(),
            "notes": self.notes.to_dict(),
            "document_id": self.document_id,
            "document_title": self.document_title,
            "pmid": self.pmid,
            "doi": self.doi,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneStudyCharacteristics:
        """Build from a dict produced by :meth:`to_dict`."""
        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])

        return cls(
            study_id=data["study_id"],
            methods=data["methods"],
            participants=CochraneParticipants.from_dict(data["participants"]),
            interventions=CochraneInterventions.from_dict(data["interventions"]),
            outcomes=CochraneOutcomes.from_dict(data["outcomes"]),
            notes=CochraneNotes.from_dict(data["notes"]),
            document_id=data.get("document_id"),
            document_title=data.get("document_title"),
            pmid=data.get("pmid"),
            doi=data.get("doi"),
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# Complete Cochrane assessment
# ---------------------------------------------------------------------------


@dataclass
class CochraneStudyAssessment:
    """A complete Cochrane-aligned study assessment.

    Combines the study-characteristics table with the nine-domain risk-of-bias
    assessment, plus optional overall scoring metadata (a superset of the
    Cochrane template).
    """

    study_characteristics: CochraneStudyCharacteristics
    risk_of_bias: CochraneRiskOfBias

    overall_quality_score: float | None = None  # 0-10 scale
    overall_confidence: float | None = None  # 0-1 scale
    evidence_level: str | None = None  # e.g. "Level 2 (moderate-high)"
    assessment_notes: list[str] | None = None

    assessment_version: str = "2.0.0"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict."""
        return {
            "study_characteristics": self.study_characteristics.to_dict(),
            "risk_of_bias": self.risk_of_bias.to_dict(),
            "overall_quality_score": self.overall_quality_score,
            "overall_confidence": self.overall_confidence,
            "evidence_level": self.evidence_level,
            "assessment_notes": self.assessment_notes,
            "assessment_version": self.assessment_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CochraneStudyAssessment:
        """Build from a dict produced by :meth:`to_dict`."""
        return cls(
            study_characteristics=CochraneStudyCharacteristics.from_dict(
                data["study_characteristics"]
            ),
            risk_of_bias=CochraneRiskOfBias.from_dict(data["risk_of_bias"]),
            overall_quality_score=data.get("overall_quality_score"),
            overall_confidence=data.get("overall_confidence"),
            evidence_level=data.get("evidence_level"),
            assessment_notes=data.get("assessment_notes"),
            assessment_version=data.get("assessment_version", "2.0.0"),
        )

    @property
    def study_id(self) -> str:
        """The study identifier (from the characteristics table)."""
        return self.study_characteristics.study_id

    @property
    def document_id(self) -> int | None:
        """The document id (from the characteristics table), if any."""
        return self.study_characteristics.document_id


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def create_default_risk_of_bias_item(
    domain: str,
    bias_type: str,
    outcome_type: str | None = None,
) -> RiskOfBiasItem:
    """Create an "Unclear risk" item for when information is unavailable."""
    return RiskOfBiasItem(
        domain=domain,
        bias_type=bias_type,
        judgement=ROB_JUDGEMENT_UNCLEAR,
        support_for_judgement="Not reported or insufficient information to assess",
        outcome_type=outcome_type,
    )


def create_default_cochrane_risk_of_bias() -> CochraneRiskOfBias:
    """Create a RoB assessment with all nine domains set to "Unclear risk"."""
    return CochraneRiskOfBias(
        random_sequence_generation=create_default_risk_of_bias_item(
            "Random sequence generation", "selection bias"
        ),
        allocation_concealment=create_default_risk_of_bias_item(
            "Allocation concealment", "selection bias"
        ),
        baseline_outcome_measurements=create_default_risk_of_bias_item(
            "Baseline outcome measurements", "selection bias"
        ),
        baseline_characteristics=create_default_risk_of_bias_item(
            "Baseline characteristics", "selection bias"
        ),
        blinding_participants_personnel=create_default_risk_of_bias_item(
            "Blinding of participants and personnel", "performance bias"
        ),
        blinding_outcome_assessment_subjective=create_default_risk_of_bias_item(
            "Blinding of outcome assessment (subjective outcomes)",
            "detection bias",
            outcome_type="subjective",
        ),
        blinding_outcome_assessment_objective=create_default_risk_of_bias_item(
            "Blinding of outcome assessment (objective outcomes)",
            "detection bias",
            outcome_type="objective",
        ),
        incomplete_outcome_data=create_default_risk_of_bias_item(
            "Incomplete outcome data", "attrition bias"
        ),
        selective_reporting=create_default_risk_of_bias_item(
            "Selective reporting", "reporting bias"
        ),
    )
