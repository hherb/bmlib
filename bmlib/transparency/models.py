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


@dataclass
class TransparencySettings:
    """User-configurable transparency thresholds."""
    enabled: bool = True
    score_threshold: int = 40          # Below this -> HIGH risk
    industry_funding_triggers_downgrade: bool = True
    missing_coi_triggers_downgrade: bool = True
    tier_downgrade_amount: int = 1
    filtering_enabled: bool = False    # Whether to exclude high-risk papers
    max_concurrent_analyses: int = 3
    cache_results: bool = True


@dataclass
class TransparencyResult:
    """Result of a transparency analysis for a single document."""
    document_id: str
    transparency_score: int                    # 0-100
    risk_level: TransparencyRisk

    industry_funding_detected: bool = False
    industry_funding_confidence: float = 0.0
    data_availability_level: str = "unknown"
    coi_disclosed: bool = True
    trial_registered: bool = False
    trial_results_compliant: bool = False
    outcome_switching_detected: bool = False

    risk_indicators: list[str] = field(default_factory=list)
    tier_downgrade_applied: int = 0

    analyzed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    analyzer_version: str = "1.0"
    full_text_analyzed: bool = False

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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransparencyResult:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        analyzed_at_raw = data.get("analyzed_at")
        if analyzed_at_raw:
            analyzed_at = datetime.fromisoformat(analyzed_at_raw)
        else:
            analyzed_at = datetime.now(tz=UTC)

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
        )


def calculate_risk_level(
    score: int,
    industry_funding: bool,
    data_availability: str,
    coi_disclosed: bool,
    settings: TransparencySettings,
) -> TransparencyRisk:
    """Determine risk level from transparency metrics.

    Risk levels:
    - HIGH: score < threshold OR (industry + restricted data) OR missing COI
    - MEDIUM: score <= 70 OR industry funding present
    - LOW: score > 70 and transparent
    """
    if score < settings.score_threshold:
        return TransparencyRisk.HIGH

    if settings.industry_funding_triggers_downgrade:
        restricted = data_availability in ("restricted", "not_available", "not_stated")
        if industry_funding and restricted:
            return TransparencyRisk.HIGH

    if settings.missing_coi_triggers_downgrade and not coi_disclosed:
        return TransparencyRisk.HIGH

    if score <= MEDIUM_RISK_SCORE_THRESHOLD:
        return TransparencyRisk.MEDIUM

    if industry_funding:
        return TransparencyRisk.MEDIUM

    return TransparencyRisk.LOW
