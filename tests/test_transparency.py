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


def _epmc_record(abstract="", in_epmc="Y"):
    """Build a minimal EuropePMC search-result envelope for _check_europepmc."""
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


class TestCheckEuropePMC:
    """Tests that COI/data-availability are read from full text, not abstract."""

    def test_coi_detected_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        coi, _level, score, _ind, ft, _ind_coi = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert coi is True
        assert ft is True
        assert score == 10  # SCORE_COI_DISCLOSED

    def test_coi_absent_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>No disclosure section here.</article>")
        coi, _level, _score, _ind, ft, _ind_coi = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert coi is False  # full text scanned, explicitly absent
        assert ft is True

    def test_coi_unknown_when_no_full_text(self):
        analyzer = TransparencyAnalyzer()
        # inEPMC == "N" so no full text is fetched, abstract has no COI signal.
        client = _FakeFullTextClient(None)
        coi, _level, _score, _ind, ft, _ind_coi = analyzer._check_europepmc(
            client, _epmc_record(in_epmc="N"), score=0, indicators=[]
        )
        assert coi is None  # undeterminable, not "absent"
        assert ft is False


class TestStructuralCOIDetection:
    """A tagged COI section counts as disclosure even without a cue phrase (issue #13).

    The JATS tag itself is structural proof that a COI statement exists; the
    cue-phrase scan remains the fallback for untagged text.
    """

    _TAGGED_CUELESS_XML = (
        '<article><back><fn-group><fn fn-type="COI-statement"><p>Dr X has '
        "served as a consultant for Pfizer.</p></fn></fn-group></back></article>"
    )

    def test_tagged_section_without_cue_phrase_counts_as_disclosed(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(self._TAGGED_CUELESS_XML)
        coi, _level, score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert coi is True  # the tag is structural proof of a disclosure
        assert industry is True  # and its content discloses industry ties
        assert score == 10  # SCORE_COI_DISCLOSED credited exactly once

    def test_tagged_section_with_cue_phrase_scores_exactly_once(self):
        # Structural and cue-phrase evidence together must not double-credit.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>The authors '
            "declare no competing interests.</p></fn></fn-group></back></article>"
        )
        coi, _level, score, _ind, ft, _industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert coi is True
        assert score == 10  # SCORE_COI_DISCLOSED, once

    def test_empty_tagged_section_is_not_a_disclosure(self):
        # A COI container with no statement text proves nothing.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p> </p></fn>'
            "</fn-group></back></article>"
        )
        coi, _level, score, _ind, ft, _industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert coi is False
        assert score == 0

    def test_empty_tagged_section_does_not_mask_untagged_disclosure(self):
        # A whitespace-only COI container must not stop the cue-phrase
        # fallback from finding an untagged disclosure elsewhere — for the
        # disclosure itself AND for the industry ties it declares.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p> </p></fn>'
            "</fn-group><p>Conflict of interest: Dr X received speaker fees "
            "from Pfizer.</p></back></article>"
        )
        coi, _level, score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert coi is True
        assert industry is True
        assert score == 10


class TestIndustryCOIDetection:
    """Industry ties disclosed in a paper's COI statement must be detected.

    The CrossRef funder check only sees structured funder names; a paper whose
    only industry signal is a full-text COI disclosure ("consultant for X",
    "speaker fees from Y") must still set industry_funding_detected.
    """

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
            client, _epmc_record(), score=0, indicators=[]
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
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is True

    def test_neutral_coi_statement_not_flagged(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
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
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is False

    def test_enumerated_denial_not_flagged(self):
        # ICMJE-style disclosures often enumerate the relationship types they
        # deny; the keywords appear but inside a negated sentence.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>None of the '
            "authors served as a consultant for, received speaker fees from, or "
            "sat on the advisory board of any company.</p></fn></fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is False

    def test_mixed_disclosure_still_flagged(self):
        # A denial sentence next to a genuine disclosure sentence must still flag.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>Dr X is a '
            "consultant for Pfizer. The remaining authors declare no competing "
            "interests.</p></fn></fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is True

    def test_non_industry_employee_not_flagged(self):
        # "Employee of" a government body is a genuine disclosure but not an
        # industry tie.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>JW is an '
            "employee of the National Institutes of Health.</p></fn>"
            "</fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is False

    def test_academic_employee_not_flagged(self):
        # University employment disclosed in a COI statement is not industry.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>MK is an '
            "employee of the University of Melbourne.</p></fn>"
            "</fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is False

    def test_editorial_advisory_board_not_flagged(self):
        # Journal editorial advisory board membership is not an industry tie.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>AB serves on '
            "the editorial advisory board of the Journal of Cardiology.</p></fn>"
            "</fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is False

    def test_industry_tie_alongside_non_industry_employment_still_flagged(self):
        # The non-industry guard must not swallow a genuine industry tie in
        # the same statement.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>JW is an '
            "employee of the National Institutes of Health. TR has served on the "
            "advisory board of AcmePharma.</p></fn></fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is True

    def test_single_quoted_jats_attribute_detected(self):
        # JATS attributes may be single-quoted; the tagged-section route must
        # still find the COI container. (This statement carries no COI cue
        # phrase, so the fallback-window route would never scan it.)
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><back><fn-group><fn fn-type='COI-statement'><p>Dr X has "
            "served as a consultant for Pfizer.</p></fn></fn-group></back></article>"
        )
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client, _epmc_record(), score=0, indicators=[]
        )
        assert ft is True
        assert industry is True

    def test_no_full_text_means_no_industry_signal(self):
        # Text-derived industry detection requires the full text; an abstract
        # alone (rarely carrying a real COI statement) must not trigger it.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        _coi, _level, _score, _ind, ft, industry = analyzer._check_europepmc(
            client,
            _epmc_record(in_epmc="N", abstract="Conflict of interest: consultant for Pfizer."),
            score=0,
            indicators=[],
        )
        assert ft is False
        assert industry is False

    def test_analyze_ors_fulltext_signal_into_result(self, monkeypatch):
        import httpx

        epmc_record = _epmc_record()
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

    def test_missing_has_results_is_false(self):
        # The request is narrowed to `fields=hasResults`, so no other key can
        # come back. An absent key means unanswered, which is reported as
        # "no posted results" rather than inferred from an unrequested payload.
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={})

        assert analyzer._check_trial_results(_Client(), "NCT12345678") is False

    def test_request_is_narrowed_to_has_results(self):
        analyzer = TransparencyAnalyzer()
        seen: dict = {}

        class _Client:
            def get(self, url, **kwargs):
                seen.update(kwargs.get("params") or {})
                return _FakeResponse(status_code=200, json_data={"hasResults": True})

        analyzer._check_trial_results(_Client(), "NCT12345678")
        assert seen == {"fields": "hasResults"}


class TestFindTrialIds:
    """Only a paper's OWN registered trial should be credited — not the trials
    a review or pooled analysis merely cites (phrasings taken from real
    EuropePMC abstracts)."""

    def test_registered_rct_clinicaltrials_gov_phrasing_credited(self):
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record(
            "Funded by the National Institutes of Health; ClinicalTrials.gov number, NCT01206062."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT01206062"]

    def test_registered_rct_label_form_credited(self):
        # "NCT number: NCT..." / "(NCT) Identified Number: NCT..." label forms.
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record(
            "Trial registration National Clinical Trial (NCT) Identified Number: NCT04088331."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT04088331"]

    def test_two_linked_own_trials_credited(self):
        # A paper reporting its own two linked registrations (e.g. ROMANA 1/2).
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record(
            "Trial registration NCT identifiers: ROMANA 1: NCT01387269; ROMANA 2: NCT01387282."
        )
        result = analyzer._find_trial_ids(None, None, None, epmc=epmc)
        assert result == ["NCT01387269", "NCT01387282"]

    def test_review_listing_many_trials_not_credited(self):
        # A pooled analysis / review enumerating its constituent trials.
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record(
            "Trial registry name and numbers: ASCEND (NCT01416181), "
            "ADVANCE (NCT00906399), DECIDE (NCT01064401)."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []

    def test_review_prose_listing_included_trials_not_credited(self):
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record(
            "We included five randomized controlled trials (NCT01111111, "
            "NCT02222222, NCT03333333, NCT04444444, NCT05555555) in the analysis."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []

    def test_bare_nct_without_registration_language_not_credited(self):
        # A single NCT mentioned with no registration cue is ambiguous; the
        # conservative choice is not to credit it as the paper's registration.
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record("Outcomes were compared across 20 high-volume centers (NCT03461341).")
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []

    def test_registration_cue_after_id_credited(self):
        # The cue may follow the id: "NCT…; registered at ClinicalTrials.gov".
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record(
            "This study (NCT01234567, registered at ClinicalTrials.gov) enrolled 400 patients."
        )
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT01234567"]

    def test_lowercase_nct_id_credited_and_normalized(self):
        # NCT ids are conventionally upper-case but must match regardless of
        # case, and be returned in the canonical upper-case form.
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record("Trial registration: nct01206062.")
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == ["NCT01206062"]

    def test_no_nct_returns_empty(self):
        analyzer = TransparencyAnalyzer()
        epmc = _epmc_record("No trials here.")
        assert analyzer._find_trial_ids(None, None, None, epmc=epmc) == []


class TestCheckTrialRegistration:
    """The registration credit (and downstream results check) must follow the
    own-vs-cited distinction."""

    def test_review_not_credited_registration_score(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                # ClinicalTrials.gov results endpoint should never be reached.
                raise AssertionError("results endpoint must not be queried for a review")

        epmc = _epmc_record("We included three trials (NCT01111111, NCT02222222, NCT03333333).")
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

        epmc = _epmc_record("ClinicalTrials.gov number, NCT01206062.")
        registered, _compliant, score, _indicators = analyzer._check_trial_registration(
            _Client(), pmid="123", doi=None, score=0, indicators=[], epmc=epmc
        )
        assert registered is True
        assert score == 20  # SCORE_TRIAL_REGISTERED


class TestDataAvailabilityPatterns:
    """Negated data-availability phrasing must not read as data sharing."""

    def test_not_available_upon_request_is_not_available(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        _coi, level, score, _ind, _ft, _ind_coi = analyzer._check_europepmc(
            client,
            _epmc_record("The data are not available upon reasonable request.", in_epmc="N"),
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
            _epmc_record(
                "Data are available from the authors upon reasonable request.", in_epmc="N"
            ),
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
            _epmc_record(
                "Analysis code is available on GitHub; individual patient data are not available.",
                in_epmc="N",
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

    def test_concurrent_analyze_does_not_cross_contaminate_reachability(self, monkeypatch):
        """One analyzer shared across threads must not leak reachability.

        ``TransparencySettings.max_concurrent_analyses`` invites callers to
        run several analyses at once. Reachability is per-analysis state: a
        thread whose APIs answered must not be reported UNKNOWN because a
        concurrent thread reset the flag, and a thread whose APIs were all
        down must not be scored because a concurrent thread succeeded.
        """
        import threading

        import httpx

        from bmlib.transparency import analyzer as analyzer_mod

        # Reachability, not throttling, is under test here; the real 0.35 s
        # interval would otherwise dominate the runtime.
        monkeypatch.setattr(analyzer_mod, "_MIN_REQUEST_INTERVAL_SECONDS", 0.0)

        # Both analyses run concurrently through one patched factory that
        # dispatches on thread name, so the two threads never race to install
        # a mock. The barrier guarantees they are genuinely interleaved
        # inside analyze() rather than running back to back.
        barrier = threading.Barrier(2, timeout=5)
        synced = threading.local()

        class _SplitClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                # Rendezvous once per thread. The two analyses issue
                # different numbers of requests, so waiting on every call
                # would desynchronise and stall on the timeout.
                if not getattr(synced, "done", False):
                    synced.done = True
                    barrier.wait()
                if threading.current_thread().name == "dead":
                    raise RuntimeError("network down")
                if "crossref" in url:
                    return _FakeResponse(status_code=200, json_data={"message": {}})
                return _FakeResponse(status_code=404)

        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _SplitClient())

        analyzer = TransparencyAnalyzer()  # one instance, shared
        results: dict[str, TransparencyRisk] = {}

        def run() -> None:
            name = threading.current_thread().name
            results[name] = analyzer.analyze(name, doi="10.1234/x").risk_level

        threads = [
            threading.Thread(target=run, name="live"),
            threading.Thread(target=run, name="dead"),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert results["dead"] == TransparencyRisk.UNKNOWN
        assert results["live"] != TransparencyRisk.UNKNOWN


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


class TestTransparencyResultRoundTrip:
    """to_dict/from_dict must not silently drop analysis provenance."""

    def test_full_text_analyzed_survives_round_trip(self):
        # Regression: full_text_analyzed was in neither to_dict nor from_dict,
        # so a persisted result came back claiming the full text was never
        # read. That matters because `coi_disclosed is False` only means
        # "scanned and absent" when the full text really was analysed.
        original = TransparencyResult(
            document_id="doc1",
            transparency_score=55,
            risk_level=TransparencyRisk.MEDIUM,
            coi_disclosed=False,
            full_text_analyzed=True,
        )
        restored = TransparencyResult.from_dict(original.to_dict())
        assert restored.full_text_analyzed is True
        assert restored.coi_disclosed is False

    def test_round_trip_preserves_every_field(self):
        original = TransparencyResult(
            document_id="doc2",
            transparency_score=80,
            risk_level=TransparencyRisk.LOW,
            industry_funding_detected=True,
            industry_funding_confidence=0.8,
            data_availability_level="full_open",
            coi_disclosed=True,
            trial_registered=True,
            trial_results_compliant=True,
            risk_indicators=["a", "b"],
            tier_downgrade_applied=1,
            analyzer_version="1.0",
            full_text_analyzed=True,
        )
        assert TransparencyResult.from_dict(original.to_dict()) == original


class TestSettingsEnabled:
    """`enabled=False` must actually disable analysis."""

    def test_disabled_settings_short_circuits_analysis(self, monkeypatch):
        import httpx

        def _boom(*a, **k):
            raise AssertionError("no HTTP client may be created when disabled")

        monkeypatch.setattr(httpx, "Client", _boom)

        analyzer = TransparencyAnalyzer(settings=TransparencySettings(enabled=False))
        result = analyzer.analyze("doc1", doi="10.1234/x")

        assert result.risk_level == TransparencyRisk.UNKNOWN
        assert result.transparency_score == 0
        assert result.risk_indicators == ["Transparency analysis disabled in settings"]

    def test_enabled_by_default(self):
        assert TransparencySettings().enabled is True
