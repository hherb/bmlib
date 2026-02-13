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

"""Tests for bmlib.transparency models."""

from __future__ import annotations

from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    calculate_risk_level,
)


class TestTransparencyRisk:
    def test_high_risk_low_score(self):
        settings = TransparencySettings(score_threshold=40)
        risk = calculate_risk_level(
            score=20, industry_funding=False, data_availability="full_open",
            coi_disclosed=True, settings=settings,
        )
        assert risk == TransparencyRisk.HIGH

    def test_high_risk_industry_restricted(self):
        settings = TransparencySettings(industry_funding_triggers_downgrade=True)
        risk = calculate_risk_level(
            score=60, industry_funding=True, data_availability="restricted",
            coi_disclosed=True, settings=settings,
        )
        assert risk == TransparencyRisk.HIGH

    def test_high_risk_missing_coi(self):
        settings = TransparencySettings(missing_coi_triggers_downgrade=True)
        risk = calculate_risk_level(
            score=80, industry_funding=False, data_availability="full_open",
            coi_disclosed=False, settings=settings,
        )
        assert risk == TransparencyRisk.HIGH

    def test_medium_risk_borderline(self):
        settings = TransparencySettings()
        risk = calculate_risk_level(
            score=60, industry_funding=False, data_availability="full_open",
            coi_disclosed=True, settings=settings,
        )
        assert risk == TransparencyRisk.MEDIUM

    def test_medium_risk_industry(self):
        settings = TransparencySettings()
        risk = calculate_risk_level(
            score=80, industry_funding=True, data_availability="full_open",
            coi_disclosed=True, settings=settings,
        )
        assert risk == TransparencyRisk.MEDIUM

    def test_low_risk(self):
        settings = TransparencySettings()
        risk = calculate_risk_level(
            score=85, industry_funding=False, data_availability="full_open",
            coi_disclosed=True, settings=settings,
        )
        assert risk == TransparencyRisk.LOW


class TestTransparencyResult:
    def test_roundtrip(self):
        result = TransparencyResult(
            document_id="doc1",
            transparency_score=75,
            risk_level=TransparencyRisk.LOW,
            industry_funding_detected=False,
            coi_disclosed=True,
            trial_registered=True,
            risk_indicators=["Minor concern"],
        )
        d = result.to_dict()
        r2 = TransparencyResult.from_dict(d)
        assert r2.document_id == "doc1"
        assert r2.transparency_score == 75
        assert r2.risk_level == TransparencyRisk.LOW
        assert r2.trial_registered is True
        assert len(r2.risk_indicators) == 1
