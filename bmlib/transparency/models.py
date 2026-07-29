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

"""Data models for transparency analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# Score at or below which risk is MEDIUM (unless other factors override)
MEDIUM_RISK_SCORE_THRESHOLD = 70


class TransparencyRisk(Enum):
    """Risk level based on transparency analysis."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class TransparencyUnknownReason(Enum):
    """Why an analysis could not determine a transparency score.

    ``UNKNOWN`` is returned for three unrelated reasons, and a caller may well
    want to treat them differently — retry an outage later, skip a disabled
    analyzer silently. Each is also named in ``risk_indicators``, but as prose
    for humans; this enum is the machine-readable form (issue #21).
    """

    DISABLED = "disabled"  # settings.enabled is False
    NO_IDENTIFIER = "no_identifier"  # neither PMID nor DOI was supplied
    UNREACHABLE = "unreachable"  # no external API answered


@dataclass
class TransparencySettings:
    """User-configurable transparency thresholds and orchestration hints.

    Two groups of fields, with different owners:

    *Honoured by the analyzer* — ``enabled`` short-circuits
    :meth:`~bmlib.transparency.analyzer.TransparencyAnalyzer.analyze`, and
    ``score_threshold``, ``industry_funding_triggers_downgrade``,
    ``missing_coi_triggers_downgrade`` and ``tier_downgrade_amount`` feed
    :func:`calculate_risk_level` and the tier downgrade.

    *Honoured by the caller* — ``filtering_enabled``, ``max_concurrent_analyses``
    and ``cache_results`` describe how a consuming application should
    orchestrate analyses. The library analyses one document per call and does
    no filtering, threading, or caching of its own; it carries these so an
    application has a single place to configure transparency behaviour.
    """

    # --- Honoured by the analyzer ---
    enabled: bool = True
    score_threshold: int = 40  # Below this -> HIGH risk
    industry_funding_triggers_downgrade: bool = True
    missing_coi_triggers_downgrade: bool = True
    tier_downgrade_amount: int = 1

    # --- Honoured by the caller (see class docstring) ---
    filtering_enabled: bool = False  # Whether to exclude high-risk papers
    max_concurrent_analyses: int = 3
    cache_results: bool = True


@dataclass
class TransparencyResult:
    """Result of a transparency analysis for a single document."""

    document_id: str
    transparency_score: int  # 0-100
    risk_level: TransparencyRisk

    industry_funding_detected: bool = False
    industry_funding_confidence: float = 0.0
    data_availability_level: str = "unknown"
    coi_disclosed: bool | None = True
    trial_registered: bool = False
    trial_results_compliant: bool = False
    # Reserved: no detection is implemented, so this is always False.
    # Deciding it would mean comparing a trial's pre-registered primary
    # outcomes against those actually reported — see ROADMAP.md. Kept in the
    # schema so persisted results do not need migrating when it lands.
    outcome_switching_detected: bool = False

    risk_indicators: list[str] = field(default_factory=list)
    tier_downgrade_applied: int = 0

    analyzed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    analyzer_version: str = "1.0"
    full_text_analyzed: bool = False

    # Set if and only if `risk_level` is UNKNOWN. Declared last for the same
    # reason as `Publication.pmcid`: downstream projects construct this
    # dataclass positionally, so inserting a field beside its logical
    # neighbours (`risk_level`, `risk_indicators`) would shift every following
    # argument by one with no error raised anywhere.
    unknown_reason: TransparencyUnknownReason | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "document_id": self.document_id,
            "transparency_score": self.transparency_score,
            "risk_level": self.risk_level.value,
            "industry_funding_detected": self.industry_funding_detected,
            "industry_funding_confidence": self.industry_funding_confidence,
            "data_availability_level": self.data_availability_level,
            "coi_disclosed": self.coi_disclosed,
            "trial_registered": self.trial_registered,
            "trial_results_compliant": self.trial_results_compliant,
            "outcome_switching_detected": self.outcome_switching_detected,
            "risk_indicators": self.risk_indicators,
            "tier_downgrade_applied": self.tier_downgrade_applied,
            "analyzed_at": self.analyzed_at.isoformat(),
            "analyzer_version": self.analyzer_version,
            # Provenance, not a finding — but it qualifies `coi_disclosed`:
            # only when the full text was read does `False` mean "scanned and
            # absent" rather than "undeterminable". Dropping it on the way to
            # storage made a persisted `coi_disclosed=False` uninterpretable.
            "full_text_analyzed": self.full_text_analyzed,
            # Enum serialised by value, mirroring `risk_level`.
            "unknown_reason": self.unknown_reason.value if self.unknown_reason else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransparencyResult:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        analyzed_at_raw = data.get("analyzed_at")
        if analyzed_at_raw:
            analyzed_at = datetime.fromisoformat(analyzed_at_raw)
        else:
            analyzed_at = datetime.now(tz=UTC)

        # Absent from results persisted before the field existed, and null on
        # every determinate result, so it is read defensively rather than
        # indexed.
        unknown_reason_raw = data.get("unknown_reason")
        unknown_reason = (
            TransparencyUnknownReason(unknown_reason_raw) if unknown_reason_raw else None
        )

        return cls(
            document_id=data["document_id"],
            transparency_score=data["transparency_score"],
            risk_level=TransparencyRisk(data["risk_level"]),
            industry_funding_detected=data.get("industry_funding_detected", False),
            industry_funding_confidence=data.get("industry_funding_confidence", 0.0),
            data_availability_level=data.get("data_availability_level", "unknown"),
            coi_disclosed=data.get("coi_disclosed", True),
            trial_registered=data.get("trial_registered", False),
            trial_results_compliant=data.get("trial_results_compliant", False),
            outcome_switching_detected=data.get("outcome_switching_detected", False),
            risk_indicators=data.get("risk_indicators", []),
            tier_downgrade_applied=data.get("tier_downgrade_applied", 0),
            analyzed_at=analyzed_at,
            analyzer_version=data.get("analyzer_version", "1.0"),
            full_text_analyzed=data.get("full_text_analyzed", False),
            unknown_reason=unknown_reason,
        )


def calculate_risk_level(
    score: int,
    industry_funding: bool,
    data_availability: str,
    coi_disclosed: bool | None,
    settings: TransparencySettings,
) -> TransparencyRisk:
    """Determine risk level from transparency metrics.

    Risk levels:
    - HIGH: score < threshold OR (industry + restricted data) OR missing COI
    - MEDIUM: score <= 70 OR industry funding present
    - LOW: score > 70 and transparent

    ``coi_disclosed`` is tri-state: ``True`` (a COI statement was found),
    ``False`` (full text was inspected and no COI statement exists), or
    ``None`` (could not be determined — e.g. full text unavailable). Only an
    *explicit* ``False`` triggers the missing-COI downgrade; ``None`` does not,
    to avoid penalising papers merely because their COI status is unknown.
    """
    if score < settings.score_threshold:
        return TransparencyRisk.HIGH

    if settings.industry_funding_triggers_downgrade:
        restricted = data_availability in ("restricted", "not_available", "not_stated")
        if industry_funding and restricted:
            return TransparencyRisk.HIGH

    if settings.missing_coi_triggers_downgrade and coi_disclosed is False:
        return TransparencyRisk.HIGH

    if score <= MEDIUM_RISK_SCORE_THRESHOLD:
        return TransparencyRisk.MEDIUM

    if industry_funding:
        return TransparencyRisk.MEDIUM

    return TransparencyRisk.LOW
