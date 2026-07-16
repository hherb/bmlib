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
        coi, _level, score, _ind, ft, _ind_coi = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert coi is True
        assert ft is True
        assert score == 10  # SCORE_COI_DISCLOSED

    def test_coi_absent_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>No disclosure section here.</article>")
        coi, _level, _score, _ind, ft, _ind_coi = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert coi is False  # full text scanned, explicitly absent
        assert ft is True

    def test_coi_unknown_when_no_full_text(self):
        analyzer = TransparencyAnalyzer()
        # inEPMC == "N" so no full text is fetched, abstract has no COI signal.
        client = _FakeFullTextClient(None)
        coi, _level, _score, _ind, ft, _ind_coi = analyzer._check_europepmc(
            client, self._epmc(in_epmc="N"), score=0, indicators=[]
        )
        assert coi is None  # undeterminable, not "absent"
        assert ft is False


class TestIndustryCOIDetection:
    """Industry ties disclosed in a paper's COI statement must be detected.

    The CrossRef funder check only sees structured funder names; a paper whose
    only industry signal is a full-text COI disclosure ("consultant for X",
    "speaker fees from Y") must still set industry_funding_detected.
    """

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

    _TAGGED_COI_XML = (
        "<article><body><sec><title>Methods</title>"
        "<p>Participants were recruited via the hospital.</p></sec></body>"
        '<back><fn-group><fn fn-type="COI-statement"><p>Dr X has served as a '
        "consultant for Pfizer and received speaker fees from Novartis.</p></fn>"
        "</fn-group></back></article>"
    )

    def test_industry_coi_in_tagged_statement_detected(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(self._TAGGED_COI_XML)
        coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is True

    def test_untagged_prose_coi_statement_detected(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><p>Competing interests: Dr Y is an employee of AcmePharma "
            "and serves on the advisory board of BioCorp.</p></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is True

    def test_neutral_coi_statement_not_flagged(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert coi is True
        assert ft is True
        assert industry is False

    def test_keywords_outside_coi_section_not_flagged(self):
        # "advisory board" in the methods of a community-engagement study must
        # not read as an industry tie when the tagged COI statement is clean.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><sec><title>Methods</title>"
            "<p>A community advisory board reviewed the study design.</p></sec></body>"
            '<back><fn-group><fn fn-type="COI-statement"><p>The authors declare no '
            "competing interests.</p></fn></fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, self._epmc(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is False

    def test_no_full_text_means_no_industry_signal(self):
        # Text-derived industry detection requires the full text; an abstract
        # alone (rarely carrying a real COI statement) must not trigger it.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client,
            self._epmc(in_epmc="N", abstract="Conflict of interest: consultant for Pfizer."),
            score=0,
            indicators=[],
        )
        assert ft is False
        assert industry is False

    def test_analyze_ors_fulltext_signal_into_result(self, monkeypatch):
        import httpx

        epmc_record = self._epmc()
        full_text = self._TAGGED_COI_XML

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                if "crossref" in url:
                    return _FakeResponse(status_code=200, json_data={"message": {}})
                if "fullTextXML" in url:
                    return _FakeResponse(status_code=200, text=full_text)
                if "europepmc" in url:
                    return _FakeResponse(status_code=200, json_data=epmc_record)
                if "openalex" in url:
                    return _FakeResponse(status_code=200, json_data={})
                return _FakeResponse(status_code=404)

        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _Client())
        analyzer = TransparencyAnalyzer()
        result = analyzer.analyze("doc1", doi="10.1234/x")
        assert result.industry_funding_detected is True
        assert 0.0 < result.industry_funding_confidence < 0.8  # moderate, below CrossRef's
        assert any("COI" in ind for ind in result.risk_indicators)


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


class TestFindTrialIds:
    """Only a paper's OWN registered trial should be credited — not the trials
    a review or pooled analysis merely cites (phrasings taken from real
    EuropePMC abstracts)."""

    def _epmc(self, abstract):
        return {"resultList": {"result": [{"abstractText": abstract}]}}

    def test_registered_rct_clinicaltrials_gov_phrasing_credited(self):
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc(
            "Funded by the National Institutes of Health; ClinicalTrials.gov number, NCT01206062."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT01206062"]

    def test_registered_rct_label_form_credited(self):
        # "NCT number: NCT..." / "(NCT) Identified Number: NCT..." label forms.
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc(
            "Trial registration National Clinical Trial (NCT) Identified Number: NCT04088331."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT04088331"]

    def test_two_linked_own_trials_credited(self):
        # A paper reporting its own two linked registrations (e.g. ROMANA 1/2).
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc(
            "Trial registration NCT identifiers: ROMANA 1: NCT01387269; ROMANA 2: NCT01387282."
        )
        result = analyzer._find_trial_ids(None, None, None, epmc=epmc)
        assert result == ["NCT01387269", "NCT01387282"]

    def test_review_listing_many_trials_not_credited(self):
        # A pooled analysis / review enumerating its constituent trials.
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc(
            "Trial registry name and numbers: ASCEND (NCT01416181), "
            "ADVANCE (NCT00906399), DECIDE (NCT01064401)."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []

    def test_review_prose_listing_included_trials_not_credited(self):
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc(
            "We included five randomized controlled trials (NCT01111111, "
            "NCT02222222, NCT03333333, NCT04444444, NCT05555555) in the analysis."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []

    def test_bare_nct_without_registration_language_not_credited(self):
        # A single NCT mentioned with no registration cue is ambiguous; the
        # conservative choice is not to credit it as the paper's registration.
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc("Outcomes were compared across 20 high-volume centers (NCT03461341).")
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []

    def test_registration_cue_after_id_credited(self):
        # The cue may follow the id: "NCT…; registered at ClinicalTrials.gov".
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc(
            "This study (NCT01234567, registered at ClinicalTrials.gov) enrolled 400 patients."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT01234567"]

    def test_lowercase_nct_id_credited_and_normalized(self):
        # NCT ids are conventionally upper-case but must match regardless of
        # case, and be returned in the canonical upper-case form.
        analyzer = TransparencyAnalyzer()
        epmc = self._epmc("Trial registration: nct01206062.")
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT01206062"]

    def test_no_nct_returns_empty(self):
        analyzer = TransparencyAnalyzer()
        assert analyzer._find_trial_ids(None, None, None, epmc=self._epmc("No trials here.")) == []


class TestCheckTrialRegistration:
    """The registration credit (and downstream results check) must follow the
    own-vs-cited distinction."""

    def _epmc(self, abstract):
        return {"resultList": {"result": [{"abstractText": abstract}]}}

    def test_review_not_credited_registration_score(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                # ClinicalTrials.gov results endpoint should never be reached.
                raise AssertionError("results endpoint must not be queried for a review")

        epmc = self._epmc("We included three trials (NCT01111111, NCT02222222, NCT03333333).")
        registered, compliant, score, indicators = analyzer._check_trial_registration(
            _Client(), pmid="123", doi=None, score=0, indicators=[], epmc=epmc
        )
        assert registered is False
        assert compliant is False
        assert score == 0

    def test_registered_rct_credited_registration_score(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={"hasResults": False})

        epmc = self._epmc("ClinicalTrials.gov number, NCT01206062.")
        registered, _compliant, score, _indicators = analyzer._check_trial_registration(
            _Client(), pmid="123", doi=None, score=0, indicators=[], epmc=epmc
        )
        assert registered is True
        assert score == 20  # SCORE_TRIAL_REGISTERED


class TestDataAvailabilityPatterns:
    """Negated data-availability phrasing must not read as data sharing."""

    def _epmc(self, abstract):
        return {"resultList": {"result": [{"abstractText": abstract, "inEPMC": "N"}]}}

    def test_not_available_upon_request_is_not_available(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        _coi, level, score, _ind, _ft, _ind_coi = analyzer._check_europepmc(
            client,
            self._epmc("The data are not available upon reasonable request."),
            score=0,
            indicators=[],
        )
        assert level == "not_available"
        assert score == 0  # no on_request credit awarded

    def test_available_upon_request_still_credited(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        _coi, level, score, _ind, _ft, _ind_coi = analyzer._check_europepmc(
            client,
            self._epmc("Data are available from the authors upon reasonable request."),
            score=0,
            indicators=[],
        )
        assert level == "on_request"
        assert score == 10  # SCORE_DATA_ON_REQUEST

    def test_mixed_statement_negation_takes_precedence(self):
        # Deliberate: when an abstract carries both a sharing cue and a
        # negation ("code on GitHub" + "data not available"), the conservative
        # negation-first ordering of _DATA_PATTERNS wins.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        _coi, level, score, _ind, _ft, _ind_coi = analyzer._check_europepmc(
            client,
            self._epmc(
                "Analysis code is available on GitHub; individual patient data are not available."
            ),
            score=0,
            indicators=[],
        )
        assert level == "not_available"
        assert score == 0


class TestAnalyzeApiReachability:
    """A run where no external API responds must be UNKNOWN, not HIGH."""

    class _DeadClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            raise RuntimeError("network down")

    def test_total_outage_returns_unknown(self, monkeypatch):
        import httpx

        monkeypatch.setattr(
            httpx, "Client", lambda *a, **k: TestAnalyzeApiReachability._DeadClient()
        )
        analyzer = TransparencyAnalyzer()
        result = analyzer.analyze("doc1", doi="10.1234/x")
        assert result.risk_level == TransparencyRisk.UNKNOWN
        assert result.transparency_score == 0

    def test_reachable_but_empty_paper_still_scores(self, monkeypatch):
        # A paper the APIs know nothing transparent about must still be scored
        # (not UNKNOWN) — reachability is what distinguishes the two cases.
        import httpx

        class _EmptyClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                if "crossref" in url:
                    return _FakeResponse(status_code=200, json_data={"message": {}})
                if "europepmc" in url and "fullTextXML" not in url:
                    return _FakeResponse(
                        status_code=200,
                        json_data={"resultList": {"result": [{"abstractText": "", "inEPMC": "N"}]}},
                    )
                if "openalex" in url:
                    return _FakeResponse(status_code=200, json_data={})
                return _FakeResponse(status_code=404)

        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _EmptyClient())
        analyzer = TransparencyAnalyzer()
        result = analyzer.analyze("doc1", doi="10.1234/x")
        assert result.risk_level != TransparencyRisk.UNKNOWN
        assert result.risk_level == TransparencyRisk.HIGH  # score 0 but measured


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
