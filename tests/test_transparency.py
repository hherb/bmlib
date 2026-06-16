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

from bmlib.transparency.analyzer import TransparencyAnalyzer
from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    calculate_risk_level,
)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeFullTextClient:
    """A fake httpx client that serves a single full-text XML body."""

    def __init__(self, full_text: str | None):
        self._full_text = full_text

    def get(self, url, **kwargs):
        if url.endswith("/fullTextXML"):
            if self._full_text is None:
                return _FakeResponse(status_code=404, text="")
            return _FakeResponse(status_code=200, text=self._full_text)
        return _FakeResponse(status_code=404)


class TestTransparencyRisk:
    def test_high_risk_low_score(self):
        settings = TransparencySettings(score_threshold=40)
        risk = calculate_risk_level(
            score=20,
            industry_funding=False,
            data_availability="full_open",
            coi_disclosed=True,
            settings=settings,
        )
        assert risk == TransparencyRisk.HIGH

    def test_high_risk_industry_restricted(self):
        settings = TransparencySettings(industry_funding_triggers_downgrade=True)
        risk = calculate_risk_level(
            score=60,
            industry_funding=True,
            data_availability="restricted",
            coi_disclosed=True,
            settings=settings,
        )
        assert risk == TransparencyRisk.HIGH

    def test_high_risk_missing_coi(self):
        settings = TransparencySettings(missing_coi_triggers_downgrade=True)
        risk = calculate_risk_level(
            score=80,
            industry_funding=False,
            data_availability="full_open",
            coi_disclosed=False,
            settings=settings,
        )
        assert risk == TransparencyRisk.HIGH

    def test_unknown_coi_does_not_trigger_downgrade(self):
        """coi_disclosed=None (undeterminable) must NOT force HIGH risk."""
        settings = TransparencySettings(missing_coi_triggers_downgrade=True)
        risk = calculate_risk_level(
            score=80,
            industry_funding=False,
            data_availability="full_open",
            coi_disclosed=None,
            settings=settings,
        )
        assert risk == TransparencyRisk.LOW

    def test_medium_risk_borderline(self):
        settings = TransparencySettings()
        risk = calculate_risk_level(
            score=60,
            industry_funding=False,
            data_availability="full_open",
            coi_disclosed=True,
            settings=settings,
        )
        assert risk == TransparencyRisk.MEDIUM

    def test_medium_risk_industry(self):
        settings = TransparencySettings()
        risk = calculate_risk_level(
            score=80,
            industry_funding=True,
            data_availability="full_open",
            coi_disclosed=True,
            settings=settings,
        )
        assert risk == TransparencyRisk.MEDIUM

    def test_low_risk(self):
        settings = TransparencySettings()
        risk = calculate_risk_level(
            score=85,
            industry_funding=False,
            data_availability="full_open",
            coi_disclosed=True,
            settings=settings,
        )
        assert risk == TransparencyRisk.LOW


class TestCheckEuropePMC:
    """Tests that COI/data-availability are read from full text, not abstract."""

    def _epmc(self, in_epmc="Y", abstract=""):
        return {
            "resultList": {
                "result": [
                    {
                        "abstractText": abstract,
                        "inEPMC": in_epmc,
                        "source": "PMC",
                        "pmcid": "PMC123",
                    }
                ]
            }
        }

    def test_coi_detected_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        coi, _level, score, _ind, ft = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert coi is True
        assert ft is True
        assert score == 10  # SCORE_COI_DISCLOSED

    def test_coi_absent_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>No disclosure section here.</article>")
        coi, _level, _score, _ind, ft = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert coi is False  # full text scanned, explicitly absent
        assert ft is True

    def test_coi_unknown_when_no_full_text(self):
        analyzer = TransparencyAnalyzer()
        # inEPMC == "N" so no full text is fetched, abstract has no COI signal.
        client = _FakeFullTextClient(None)
        coi, _level, _score, _ind, ft = analyzer._check_europepmc(
            client, self._epmc(in_epmc="N"), score=0, indicators=[]
        )
        assert coi is None  # undeterminable, not "absent"
        assert ft is False


class TestCheckTrialResults:
    """Tests that posted-results detection reads the correct v2 API field."""

    def test_has_results_true(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={"hasResults": True})

        assert analyzer._check_trial_results(_Client(), "NCT12345678") is True

    def test_has_results_false(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={"hasResults": False})

        assert analyzer._check_trial_results(_Client(), "NCT12345678") is False

    def test_results_section_fallback(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={"resultsSection": {"x": 1}})

        assert analyzer._check_trial_results(_Client(), "NCT12345678") is True


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
