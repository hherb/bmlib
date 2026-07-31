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

import pytest

from bmlib.transparency.analyzer import (
    _DATA_ARCHIVE_NAMES,
    _DATA_LEVEL_RANK,
    _DATA_LEVEL_SCORES,
    _DATA_PATTERNS,
    _INDICATOR_COI_IN_PUBMED,
    _INDICATOR_COI_UNKNOWN,
    _INDICATOR_DATA_NOT_AVAILABLE,
    _INDICATOR_INDUSTRY_COI,
    _INDICATOR_NO_COI_IN_FULLTEXT,
    _INDICATOR_NO_POSTED_RESULTS,
    _INDICATOR_RESULTS_NOT_CHECKABLE,
    _TRIAL_REGISTRY_NAMES,
    DEFAULT_INDUSTRY_CONFIDENCE,
    SCORE_CITED,
    SCORE_COI_DISCLOSED,
    SCORE_DATA_FULL_OPEN,
    SCORE_DATA_ON_REQUEST,
    SCORE_FUNDER_INFO,
    SCORE_OPEN_ACCESS,
    TEXT_INDUSTRY_CONFIDENCE,
    TransparencyAnalyzer,
    _Analysis,
    _merge_pubmed_signals,
    _parse_pubmed_signals,
    _PubMedSignals,
)
from bmlib.transparency.models import (
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    TransparencyUnknownReason,
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


class TestAnalysisCarrier:
    """The accumulator carrier's own semantics, before anything uses it."""

    def test_defaults_match_a_fresh_analysis(self):
        analysis = _Analysis()
        assert analysis.score == 0
        assert analysis.indicators == []
        assert analysis.industry_funding is False
        assert analysis.industry_confidence == 0.0
        assert analysis.data_level == "unknown"
        assert analysis.coi_disclosed is None
        assert analysis.trial_registered is False
        assert analysis.results_compliant is False
        assert analysis.full_text_analyzed is False
        assert analysis.funder_info_scored is False

    def test_each_carrier_gets_its_own_indicator_list(self):
        # A mutable default shared across instances would leak one analysis's
        # findings into the next.
        first, second = _Analysis(), _Analysis()
        first.indicators.append("x")
        assert second.indicators == []

    def test_funder_info_is_awarded_once(self):
        analysis = _Analysis()
        analysis.award_funder_info()
        analysis.award_funder_info()
        assert analysis.score == SCORE_FUNDER_INFO
        assert analysis.funder_info_scored is True

    def test_funder_info_is_not_awarded_when_already_spent(self):
        # The hazard the method exists for: whichever source runs first spends
        # the component, and the second must not spend it again.
        analysis = _Analysis(funder_info_scored=True)
        analysis.award_funder_info()
        assert analysis.score == 0

    def test_an_industry_funder_is_recorded_with_structured_confidence(self):
        analysis = _Analysis()
        analysis.note_industry_funder("Genentech Inc.")
        assert analysis.industry_funding is True
        assert analysis.industry_confidence == DEFAULT_INDUSTRY_CONFIDENCE
        assert analysis.indicators == ["Industry funder: Genentech Inc."]

    def test_one_funder_is_one_indicator_however_often_it_is_reported(self):
        analysis = _Analysis()
        analysis.note_industry_funder("Genentech Inc.")
        analysis.note_industry_funder("Genentech Inc.")
        assert analysis.indicators == ["Industry funder: Genentech Inc."]

    def test_a_funder_never_lowers_an_established_confidence(self):
        analysis = _Analysis(industry_confidence=0.95)
        analysis.note_industry_funder("Genentech Inc.")
        assert analysis.industry_confidence == 0.95

    def test_an_industry_coi_is_weaker_evidence_than_a_funder_record(self):
        analysis = _Analysis()
        analysis.note_industry_coi()
        assert analysis.industry_funding is True
        assert analysis.industry_confidence == TEXT_INDUSTRY_CONFIDENCE
        assert analysis.indicators == [_INDICATOR_INDUSTRY_COI]

    def test_a_coi_signal_never_lowers_a_funder_record_s_confidence(self):
        # Arrival order must not decide the confidence: a structured funder
        # record outranks COI prose whichever is seen first.
        analysis = _Analysis()
        analysis.note_industry_funder("Genentech Inc.")
        analysis.note_industry_coi()
        assert analysis.industry_confidence == DEFAULT_INDUSTRY_CONFIDENCE

    def test_a_deposition_establishes_open_data(self):
        analysis = _Analysis()
        analysis.note_data_deposition("GENBANK")
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN
        assert analysis.indicators == ["Data deposited in GENBANK"]

    def test_one_archive_is_one_indicator_however_often_it_is_reported(self):
        analysis = _Analysis()
        analysis.note_data_deposition("GENBANK")
        analysis.note_data_deposition("GENBANK")
        assert analysis.indicators == ["Data deposited in GENBANK"]

    def test_stronger_evidence_swaps_the_credit_rather_than_adding_to_it(self):
        # The two data-availability awards are documented as mutually
        # exclusive — that is what makes the best attainable total exactly 100.
        # A second producer that simply added its own credit would score one
        # component twice.
        analysis = _Analysis()
        analysis.note_data_availability("on_request")
        analysis.note_data_deposition("GENBANK")
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN

    def test_a_level_reported_twice_is_credited_once(self):
        analysis = _Analysis()
        analysis.note_data_availability("full_open")
        analysis.note_data_availability("full_open")
        assert analysis.score == SCORE_DATA_FULL_OPEN

    def test_a_level_reported_twice_records_one_indicator(self):
        # The score assertion above cannot see a broken tie rule: the credit
        # swap is self-cancelling on a tie (+20 − 20 == 0). The indicator is
        # what shows it — and a re-appended line would survive the retraction
        # below, because `list.remove()` drops one occurrence.
        analysis = _Analysis()
        analysis.note_data_availability("not_available")
        analysis.note_data_availability("not_available")
        analysis.note_data_deposition("GENBANK")
        assert _INDICATOR_DATA_NOT_AVAILABLE not in analysis.indicators

    def test_an_absence_never_erases_a_finding(self):
        # `unknown` is the absence of a finding, not a weaker one, so a step
        # that found nothing must leave another step's finding standing.
        analysis = _Analysis()
        analysis.note_data_availability("on_request")
        analysis.note_data_availability("unknown")
        assert analysis.data_level == "on_request"
        assert analysis.score == SCORE_DATA_ON_REQUEST

    def test_a_weaker_finding_does_not_displace_a_stronger_one(self):
        analysis = _Analysis()
        analysis.note_data_deposition("GENBANK")
        analysis.note_data_availability("not_available")
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN
        assert _INDICATOR_DATA_NOT_AVAILABLE not in analysis.indicators

    def test_the_not_available_line_is_retracted_when_it_is_superseded(self):
        # A full text can say "individual patient data are not available" while
        # the sequences went to GenBank. Leaving the line in place would
        # contradict `data_availability_level` — the situation the COI
        # indicators are named constants for.
        analysis = _Analysis()
        analysis.note_data_availability("not_available")
        assert _INDICATOR_DATA_NOT_AVAILABLE in analysis.indicators
        analysis.note_data_deposition("GENBANK")
        assert _INDICATOR_DATA_NOT_AVAILABLE not in analysis.indicators

    def test_a_level_outside_the_vocabulary_raises(self):
        # The rank table *is* the vocabulary. Ranking an unlisted level weakest
        # would swallow a typo and silently drop the finding it names.
        with pytest.raises(KeyError):
            _Analysis().note_data_availability("open-ish")

    def test_every_level_the_text_scan_can_produce_is_in_the_vocabulary(self):
        # The raise above is the right behaviour for a caller's typo, but
        # `_DATA_PATTERNS` is *this module's own* producer of level strings and
        # the only one. A pattern added with a misspelled level would not score
        # wrongly — it would raise `KeyError` out of `analyze()`, which wraps
        # none of its sub-steps, on every paper whose text matches it.
        assert set(_DATA_PATTERNS.values()) <= set(_DATA_LEVEL_RANK)

    def test_every_level_that_scores_is_in_the_vocabulary(self):
        # This one drifts *silently*: the credit is read with
        # `.get(level, 0)`, so a scores key that no longer matches a rank key
        # awards nothing and takes nothing back, with no error to notice.
        assert set(_DATA_LEVEL_SCORES) <= set(_DATA_LEVEL_RANK)


class TestCheckEuropePMC:
    """Tests that COI/data-availability are read from full text, not abstract."""

    def test_coi_detected_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True
        assert analysis.full_text_analyzed is True
        assert analysis.score == 10  # SCORE_COI_DISCLOSED

    def test_coi_absent_in_full_text(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>No disclosure section here.</article>")
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is False  # full text scanned, explicitly absent
        assert analysis.full_text_analyzed is True

    def test_coi_unknown_when_no_full_text(self):
        analyzer = TransparencyAnalyzer()
        # inEPMC == "N" so no full text is fetched, abstract has no COI signal.
        client = _FakeFullTextClient(None)
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(in_epmc="N"), analysis)
        assert analysis.coi_disclosed is None  # undeterminable, not "absent"
        assert analysis.full_text_analyzed is False


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
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.coi_disclosed is True  # the tag is structural proof of a disclosure
        assert analysis.industry_funding is True  # and its content discloses industry ties
        assert analysis.score == 10  # SCORE_COI_DISCLOSED credited exactly once

    def test_tagged_section_with_cue_phrase_scores_exactly_once(self):
        # Structural and cue-phrase evidence together must not double-credit.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>The authors '
            "declare no competing interests.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True
        assert analysis.score == 10  # SCORE_COI_DISCLOSED, once

    def test_empty_tagged_section_is_not_a_disclosure(self):
        # A COI container with no statement text proves nothing.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p> </p></fn>'
            "</fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.coi_disclosed is False
        assert analysis.score == 0

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
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.coi_disclosed is True
        assert analysis.industry_funding is True
        assert analysis.score == 10


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
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is True

    def test_untagged_prose_coi_statement_detected(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><p>Competing interests: Dr Y is an employee of AcmePharma "
            "and serves on the advisory board of BioCorp.</p></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is True

    def test_neutral_coi_statement_not_flagged(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article>The authors declare no conflict of interest.</article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is False

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
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is False

    def test_enumerated_denial_not_flagged(self):
        # ICMJE-style disclosures often enumerate the relationship types they
        # deny; the keywords appear but inside a negated sentence.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>None of the '
            "authors served as a consultant for, received speaker fees from, or "
            "sat on the advisory board of any company.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is False

    def test_mixed_disclosure_still_flagged(self):
        # A denial sentence next to a genuine disclosure sentence must still flag.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>Dr X is a '
            "consultant for Pfizer. The remaining authors declare no competing "
            "interests.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is True

    def test_non_industry_employee_not_flagged(self):
        # "Employee of" a government body is a genuine disclosure but not an
        # industry tie.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>JW is an '
            "employee of the National Institutes of Health.</p></fn>"
            "</fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is False

    def test_academic_employee_not_flagged(self):
        # University employment disclosed in a COI statement is not industry.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>MK is an '
            "employee of the University of Melbourne.</p></fn>"
            "</fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is False

    def test_editorial_advisory_board_not_flagged(self):
        # Journal editorial advisory board membership is not an industry tie.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>AB serves on '
            "the editorial advisory board of the Journal of Cardiology.</p></fn>"
            "</fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is False

    def test_industry_tie_alongside_non_industry_employment_still_flagged(self):
        # The non-industry guard must not swallow a genuine industry tie in
        # the same statement.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            '<article><back><fn-group><fn fn-type="COI-statement"><p>JW is an '
            "employee of the National Institutes of Health. TR has served on the "
            "advisory board of AcmePharma.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is True

    def test_single_quoted_jats_attribute_detected(self):
        # JATS attributes may be single-quoted; the tagged-section route must
        # still find the COI container. (This statement carries no COI cue
        # phrase, so the fallback-window route would never scan it.)
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><back><fn-group><fn fn-type='COI-statement'><p>Dr X has "
            "served as a consultant for Pfizer.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.industry_funding is True

    def test_no_full_text_means_no_industry_signal(self):
        # Text-derived industry detection requires the full text; an abstract
        # alone (rarely carrying a real COI statement) must not trigger it.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        analysis = _Analysis()
        analyzer._check_europepmc(
            client,
            _epmc_record(in_epmc="N", abstract="Conflict of interest: consultant for Pfizer."),
            analysis,
        )
        assert analysis.full_text_analyzed is False
        assert analysis.industry_funding is False

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
        analysis = _Analysis()
        analyzer._check_trial_registration(_Client(), "123", None, analysis, epmc=epmc)
        assert analysis.trial_registered is False
        assert analysis.results_compliant is False
        assert analysis.score == 0

    def test_registered_rct_credited_registration_score(self):
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={"hasResults": False})

        epmc = _epmc_record("ClinicalTrials.gov number, NCT01206062.")
        analysis = _Analysis()
        analyzer._check_trial_registration(_Client(), "123", None, analysis, epmc=epmc)
        assert analysis.trial_registered is True
        assert analysis.score == 20  # SCORE_TRIAL_REGISTERED

    def test_an_inbound_results_flag_does_not_stand_in_for_this_check(self):
        # `_INDICATOR_NO_POSTED_RESULTS` reports what *this* step established:
        # it asked ClinicalTrials.gov and was told there are no results. A
        # `results_compliant` that arrived True must not suppress that, or a
        # later-added step writing the field would silently retract a finding
        # it knows nothing about.
        analyzer = TransparencyAnalyzer()

        class _Client:
            def get(self, url, **kwargs):
                return _FakeResponse(status_code=200, json_data={"hasResults": False})

        epmc = _epmc_record("ClinicalTrials.gov number, NCT01206062.")
        analysis = _Analysis(results_compliant=True)
        analyzer._check_trial_registration(_Client(), "123", None, analysis, epmc=epmc)
        assert _INDICATOR_NO_POSTED_RESULTS in analysis.indicators


class TestCheckOpenAlex:
    """The one sub-step nothing else in this file calls directly."""

    class _Client:
        """Serves one OpenAlex payload and records that it was asked."""

        def __init__(self, payload: dict):
            self._payload = payload
            self.requested: list[str] = []

        def get(self, url, **kwargs):
            self.requested.append(url)
            return _FakeResponse(status_code=200, json_data=self._payload)

    def test_both_credits_add_to_the_score_already_accumulated(self):
        # This step returned a bare `int` before the carrier, so the migration
        # hazard is assigning `analysis.score` instead of adding to it — which
        # a zero starting score would hide.
        analysis = _Analysis(score=SCORE_FUNDER_INFO)
        client = self._Client({"open_access": {"is_oa": True}, "cited_by_count": 7})
        TransparencyAnalyzer()._check_openalex(client, "10.1234/x", analysis)
        assert analysis.score == SCORE_FUNDER_INFO + SCORE_OPEN_ACCESS + SCORE_CITED

    def test_an_uncited_closed_work_earns_nothing(self):
        # `_query_openalex` swallows every exception, so an unchanged score on
        # its own would read the same way if the request had failed outright.
        # Asserting the query was actually made is what separates the two.
        analysis = _Analysis()
        client = self._Client({"open_access": {"is_oa": False}, "cited_by_count": 0})
        TransparencyAnalyzer()._check_openalex(client, "10.1234/x", analysis)
        assert any("openalex" in url for url in client.requested)
        assert analysis.score == 0


class TestDataAvailabilityPatterns:
    """Negated data-availability phrasing must not read as data sharing."""

    def test_not_available_upon_request_is_not_available(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        analysis = _Analysis()
        analyzer._check_europepmc(
            client,
            _epmc_record("The data are not available upon reasonable request.", in_epmc="N"),
            analysis,
        )
        assert analysis.data_level == "not_available"
        assert analysis.score == 0  # no on_request credit awarded

    def test_available_upon_request_still_credited(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        analysis = _Analysis()
        analyzer._check_europepmc(
            client,
            _epmc_record(
                "Data are available from the authors upon reasonable request.", in_epmc="N"
            ),
            analysis,
        )
        assert analysis.data_level == "on_request"
        assert analysis.score == 10  # SCORE_DATA_ON_REQUEST

    def test_mixed_statement_negation_takes_precedence(self):
        # Deliberate: when an abstract carries both a sharing cue and a
        # negation ("code on GitHub" + "data not available"), the conservative
        # negation-first ordering of _DATA_PATTERNS wins.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        analysis = _Analysis()
        analyzer._check_europepmc(
            client,
            _epmc_record(
                "Analysis code is available on GitHub; individual patient data are not available.",
                in_epmc="N",
            ),
            analysis,
        )
        assert analysis.data_level == "not_available"
        assert analysis.score == 0

    def test_a_level_this_step_did_not_find_is_not_scored(self):
        # The step publishes what it found rather than reading the carrier back
        # to decide the credit. Reading it back would score a level another
        # step established as if EuropePMC's text had shown it — the positional
        # hazard the carrier was introduced to remove, respelled as state.
        #
        # The inbound level survives, which is the half of this that changed
        # when `<DataBankList>` made PubMed the field's second producer: the
        # step no longer owns the field outright, so "I found nothing" is a
        # no-op rather than an erasure.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        analysis = _Analysis(data_level="full_open", score=SCORE_DATA_FULL_OPEN)
        analyzer._check_europepmc(
            client,
            _epmc_record("This abstract says nothing about data.", in_epmc="N"),
            analysis,
        )
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN

    def test_this_step_does_not_downgrade_another_producers_finding(self):
        # A deposition accession is structured publisher metadata; a phrase
        # matched in running text is not. The stronger finding stands, and the
        # line that would contradict it is never added.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(None)
        analysis = _Analysis(data_level="full_open", score=SCORE_DATA_FULL_OPEN)
        analyzer._check_europepmc(
            client,
            _epmc_record("Individual patient data are not available.", in_epmc="N"),
            analysis,
        )
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN
        assert _INDICATOR_DATA_NOT_AVAILABLE not in analysis.indicators


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


class TestUnknownReason:
    """Issue #21 — the cause of an UNKNOWN result must be readable as data.

    ``analyze()`` returns UNKNOWN at score 0 for three unrelated reasons. A
    caller that wants to retry a network outage but silently skip a disabled
    analyzer had to match on ``risk_indicators`` prose, which is documentation
    rather than API.
    """

    def test_disabled_analysis_reports_disabled(self, monkeypatch):
        import httpx

        def _boom(*a, **k):
            raise AssertionError("no HTTP client may be created when disabled")

        monkeypatch.setattr(httpx, "Client", _boom)

        analyzer = TransparencyAnalyzer(settings=TransparencySettings(enabled=False))
        result = analyzer.analyze("doc1", doi="10.1234/x")

        assert result.unknown_reason is TransparencyUnknownReason.DISABLED

    def test_missing_identifier_reports_no_identifier(self):
        analyzer = TransparencyAnalyzer()
        result = analyzer.analyze("doc1")

        assert result.risk_level == TransparencyRisk.UNKNOWN
        assert result.unknown_reason is TransparencyUnknownReason.NO_IDENTIFIER

    def test_total_outage_reports_unreachable(self, monkeypatch):
        import httpx

        monkeypatch.setattr(
            httpx, "Client", lambda *a, **k: TestAnalyzeApiReachability._DeadClient()
        )
        analyzer = TransparencyAnalyzer()
        result = analyzer.analyze("doc1", doi="10.1234/x")

        assert result.risk_level == TransparencyRisk.UNKNOWN
        assert result.unknown_reason is TransparencyUnknownReason.UNREACHABLE

    def test_the_three_causes_are_distinguishable(self, monkeypatch):
        """The point of the field: three UNKNOWNs, three different values."""
        import httpx

        monkeypatch.setattr(
            httpx, "Client", lambda *a, **k: TestAnalyzeApiReachability._DeadClient()
        )
        disabled = TransparencyAnalyzer(settings=TransparencySettings(enabled=False)).analyze(
            "doc1", doi="10.1234/x"
        )
        no_id = TransparencyAnalyzer().analyze("doc2")
        unreachable = TransparencyAnalyzer().analyze("doc3", doi="10.1234/x")

        reasons = {disabled.unknown_reason, no_id.unknown_reason, unreachable.unknown_reason}
        assert len(reasons) == 3

    def test_a_measured_result_carries_no_reason(self, monkeypatch):
        """Invariant: a reason is present if and only if the risk is UNKNOWN.

        ``calculate_risk_level()`` never returns UNKNOWN, so every UNKNOWN the
        analyzer produces comes from one of the three early returns — and
        nothing else may claim a reason.
        """
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
                return _FakeResponse(status_code=404)

        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _EmptyClient())
        result = TransparencyAnalyzer().analyze("doc1", doi="10.1234/x")

        assert result.risk_level != TransparencyRisk.UNKNOWN
        assert result.unknown_reason is None

    def test_default_is_none(self):
        assert (
            TransparencyResult(
                document_id="doc1",
                transparency_score=50,
                risk_level=TransparencyRisk.MEDIUM,
            ).unknown_reason
            is None
        )

    def test_a_reason_on_a_determinate_result_is_rejected(self):
        # The invariant is documented; this makes it enforced in the one
        # direction that cannot collide with legacy data.
        with pytest.raises(ValueError, match="only when risk_level is UNKNOWN"):
            TransparencyResult(
                document_id="doc1",
                transparency_score=80,
                risk_level=TransparencyRisk.LOW,
                unknown_reason=TransparencyUnknownReason.DISABLED,
            )

    def test_a_legacy_unknown_without_a_reason_still_constructs(self):
        # The converse is deliberately not enforced: results persisted before
        # the field existed are UNKNOWN with no reason, and refusing them would
        # make an additive field a breaking change.
        result = TransparencyResult(
            document_id="doc1",
            transparency_score=0,
            risk_level=TransparencyRisk.UNKNOWN,
        )
        assert result.unknown_reason is None

    def test_survives_round_trip(self):
        original = TransparencyResult(
            document_id="doc1",
            transparency_score=0,
            risk_level=TransparencyRisk.UNKNOWN,
            unknown_reason=TransparencyUnknownReason.UNREACHABLE,
        )
        assert TransparencyResult.from_dict(original.to_dict()) == original

    def test_serialised_by_value_like_transparency_risk(self):
        result = TransparencyResult(
            document_id="doc1",
            transparency_score=0,
            risk_level=TransparencyRisk.UNKNOWN,
            unknown_reason=TransparencyUnknownReason.NO_IDENTIFIER,
        )
        assert result.to_dict()["unknown_reason"] == "no_identifier"

    def test_a_result_persisted_before_this_field_existed_still_loads(self):
        # Results stored by earlier versions carry no `unknown_reason` key;
        # from_dict() must default rather than raise.
        legacy = {
            "document_id": "doc1",
            "transparency_score": 0,
            "risk_level": "unknown",
            "risk_indicators": ["Transparency APIs unreachable — score not determinable"],
        }
        assert TransparencyResult.from_dict(legacy).unknown_reason is None


# ---------------------------------------------------------------------------
# PubMed E-utilities step (issue #18)
# ---------------------------------------------------------------------------


def _pubmed_xml(
    *,
    coi: str | None = None,
    databanks: tuple[tuple[str, tuple[str, ...]], ...] = (),
    agencies: tuple[str, ...] = (),
) -> str:
    """Build a minimal PubmedArticleSet response.

    *databanks* is a tuple of ``(DataBankName, accession numbers)`` pairs.
    """
    databank_xml = "".join(
        f"<DataBank><DataBankName>{name}</DataBankName><AccessionNumberList>"
        + "".join(f"<AccessionNumber>{a}</AccessionNumber>" for a in accessions)
        + "</AccessionNumberList></DataBank>"
        for name, accessions in databanks
    )
    grant_xml = "".join(
        f"<Grant><GrantID>G{i}</GrantID><Agency>{agency}</Agency>"
        f"<Country>United States</Country></Grant>"
        for i, agency in enumerate(agencies)
    )
    return (
        '<?xml version="1.0" ?><PubmedArticleSet><PubmedArticle><MedlineCitation>'
        "<PMID>12345678</PMID><Article>"
        "<ArticleTitle>A study</ArticleTitle>"
        + (f"<GrantList>{grant_xml}</GrantList>" if grant_xml else "")
        + (f"<DataBankList>{databank_xml}</DataBankList>" if databank_xml else "")
        + "</Article>"
        + (f"<CoiStatement>{coi}</CoiStatement>" if coi is not None else "")
        + "</MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )


class _RecordingClient:
    """Fake httpx client that dispatches on URL and records every request."""

    def __init__(
        self,
        *,
        crossref: dict | None = None,
        epmc: dict | None = None,
        full_text: str | None = None,
        pubmed: str | None = None,
        trial_has_results: bool = False,
    ):
        self.crossref = crossref
        self.epmc = epmc
        self.full_text = full_text
        self.pubmed = pubmed
        self.trial_has_results = trial_has_results
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((url, params))
        if "crossref" in url:
            if self.crossref is None:
                return _FakeResponse(status_code=404)
            return _FakeResponse(status_code=200, json_data=self.crossref)
        if "fullTextXML" in url:
            if self.full_text is None:
                return _FakeResponse(status_code=404)
            return _FakeResponse(status_code=200, text=self.full_text)
        if "europepmc" in url:
            if self.epmc is None:
                return _FakeResponse(status_code=404)
            return _FakeResponse(status_code=200, json_data=self.epmc)
        if "eutils" in url:
            if self.pubmed is None:
                return _FakeResponse(status_code=404)
            return _FakeResponse(status_code=200, text=self.pubmed)
        if "clinicaltrials" in url:
            return _FakeResponse(status_code=200, json_data={"hasResults": self.trial_has_results})
        return _FakeResponse(status_code=404)

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]

    def params_for(self, fragment: str) -> dict:
        for url, params in self.calls:
            if fragment in url:
                return params
        raise AssertionError(f"no request matched {fragment!r}")


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, client: _RecordingClient) -> None:
    """Serve *client* to every ``analyze()`` call and drop the rate limit.

    Module-level rather than a helper on one test class, because three classes
    install a fake client this way and reaching across classes for it couples
    them for no reason.
    """
    import httpx

    from bmlib.transparency import analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)


def _epmc_payload(*, abstract: str = "", pmid: str | None = None, in_epmc: str = "N") -> dict:
    record: dict = {"abstractText": abstract, "inEPMC": in_epmc}
    if pmid is not None:
        record["pmid"] = pmid
    return {"resultList": {"result": [record]}}


class TestPubMedSignalParsing:
    """The PubMed record is parsed without HTTP, so parsing is tested alone."""

    def test_coi_statement_detected(self):
        signals = _parse_pubmed_signals(_pubmed_xml(coi="The authors declare none."))
        assert signals.coi_statement is True

    def test_whitespace_only_coi_statement_is_not_a_disclosure(self):
        signals = _parse_pubmed_signals(_pubmed_xml(coi="   "))
        assert signals.coi_statement is False

    def test_absent_coi_statement(self):
        assert _parse_pubmed_signals(_pubmed_xml()).coi_statement is False

    def test_a_coi_statement_opening_with_markup_is_still_a_disclosure(self):
        # The MEDLINE DTD declares CoiStatement as (%text;)*, so <b>/<i>/<sup>
        # are legal inside it. Reading the element's `.text` alone sees only
        # the leading text node — empty here — and would report a disclosure
        # that is plainly present as absent.
        xml = _pubmed_xml(coi="PLACEHOLDER").replace(
            "PLACEHOLDER", "<b>Conflict of interest:</b> Dr X consults for Y."
        )
        assert _parse_pubmed_signals(xml).coi_statement is True

    def test_clinicaltrials_accessions_collected_and_upper_cased(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("ClinicalTrials.gov", ("nct01234567", "NCT07654321")),))
        )
        assert signals.trial_accessions == ("NCT01234567", "NCT07654321")

    def test_non_clinicaltrials_registry_registers_without_accessions(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("ISRCTN", ("ISRCTN12345678",)),)))
        assert signals.registration_not_checkable is True
        assert signals.trial_accessions == ()

    def test_registry_name_matching_ignores_case(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("clinicaltrials.gov", ("NCT01234567",)),))
        )
        assert signals.trial_accessions == ("NCT01234567",)

    def test_a_malformed_accession_never_reaches_a_url(self):
        # An accession is publisher-supplied text that would be interpolated
        # into the ClinicalTrials.gov URL path, so it is validated before it is
        # carried forward. The registration itself still counts — it just
        # cannot be followed up, which is what `registration_not_checkable`
        # records.
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("ClinicalTrials.gov", ("../../../evil", "NCT-nope")),))
        )
        assert signals.trial_accessions == ()
        assert signals.registration_not_checkable is True

    def test_data_deposition_databank_is_not_a_registration(self):
        # GENBANK/PDB accessions are a data-availability signal — they must not
        # be mistaken for trial registration.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)))
        assert signals.trial_accessions == ()
        assert signals.registration_not_checkable is False

    def test_a_data_deposition_databank_is_collected(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)))
        assert signals.data_banks == ("GENBANK",)

    def test_a_databank_without_accessions_still_counts(self):
        # <AccessionNumberList> is optional in the MEDLINE DTD, and the name is
        # the publisher's assertion of deposition. The trial branch beside this
        # one already treats a registration with no usable accession as
        # established, so the same rule applies here.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("Dryad", ()),)))
        assert signals.data_banks == ("Dryad",)

    def test_a_blank_databank_name_is_not_a_deposition(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=((" ", ("X1",)),)))
        assert signals.data_banks == ()

    def test_one_archive_named_twice_is_one_deposition(self):
        # DataBankName is a controlled vocabulary, matched case-insensitively
        # by the registry branch in the same loop. One archive is one finding
        # however the publisher spelled it; the first spelling is what a human
        # is shown.
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("GenBank", ("MN908947",)), ("GENBANK", ("MN908948",))))
        )
        assert signals.data_banks == ("GenBank",)

    def test_a_trial_registry_is_never_a_deposition(self):
        # No entry may be counted as both a registration and data sharing.
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("ClinicalTrials.gov", ("NCT01234567",)), ("SRA", ("SRP1",))))
        )
        assert signals.data_banks == ("SRA",)

    def test_the_two_databank_sets_are_disjoint(self):
        # Nothing may be classified as both, and the sets are what decides it.
        # Before the allowlist, the `if/continue` guaranteed this structurally.
        assert not (_TRIAL_REGISTRY_NAMES & _DATA_ARCHIVE_NAMES)

    def test_both_databank_sets_are_lowercased(self):
        # The lookup compares against `name.lower()`, so a member written with
        # capitals never matches and is silently dead — no error, no missed
        # test, just a signal that stops being read. Six archive names have
        # canonical spellings with internal capitals (dbGaP, dbSNP, dbVar,
        # BioProject, PubChem-Substance, PubChem-BioAssay), which is exactly
        # what gets pasted from NLM's table when the next one is added.
        for name in _TRIAL_REGISTRY_NAMES | _DATA_ARCHIVE_NAMES:
            assert name == name.lower(), f"{name!r} can never match"

    def test_a_curated_database_is_not_a_deposition(self):
        # OMIM, RefSeq, SWISSPROT and the UniProt family are curated or
        # derived: an author cannot deposit into them, so an accession there
        # cites a third-party record rather than evidencing data sharing.
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("OMIM", ("143890",)), ("RefSeq", ("NM_000546",))))
        )
        assert signals.data_banks == ()

    def test_a_databank_in_neither_set_is_credited_as_neither(self):
        # The decisive property: a name NLM adds after this release is not
        # silently read as open data. The deposition branch is an allowlist
        # precisely so a vocabulary gap under-credits rather than over-credits.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("SomeNewBank", ("X1",)),)))
        assert signals.data_banks == ()
        assert signals.trial_accessions == ()
        assert signals.registration_not_checkable is False

    @pytest.mark.parametrize(
        ("registry", "accession"),
        [("JMACCT", "JMA-IIA00123"), ("REPEC", "PER-001-19")],
    )
    def test_a_registry_nlm_lists_is_recognised(self, registry, accession):
        # Both are on NLM's list and neither was in the set. REPEC has no
        # PubMed records today, so only this test stands between it and a
        # silent deletion.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=((registry, (accession,)),)))
        assert signals.registration_not_checkable is True
        assert signals.data_banks == ()

    def test_grant_agencies_collected(self):
        signals = _parse_pubmed_signals(_pubmed_xml(agencies=("NCI NIH HHS", "Wellcome Trust")))
        assert signals.funders == ("NCI NIH HHS", "Wellcome Trust")

    def test_repeated_agencies_are_collapsed(self):
        # PubMed emits one <Grant> per grant number, so an agency funding four
        # grants on one paper appears four times in the XML. Left as-is, each
        # repeat adds its own "Industry funder: …" line to the result.
        signals = _parse_pubmed_signals(
            _pubmed_xml(agencies=("Genentech Inc.", "NCI NIH HHS", "Genentech Inc."))
        )
        assert signals.funders == ("Genentech Inc.", "NCI NIH HHS")

    def test_malformed_xml_yields_no_signals(self):
        assert _parse_pubmed_signals("<PubmedArticleSet><trunca") == _PubMedSignals()

    def test_empty_article_set_yields_no_signals(self):
        assert _parse_pubmed_signals("<PubmedArticleSet/>") == _PubMedSignals()


class TestPubMedRequest:
    """The request itself: identification, the API key, and when it is issued."""

    def test_api_key_is_sent_when_configured(self, monkeypatch):
        client = self._run(monkeypatch, TransparencyAnalyzer(pubmed_api_key="KEY123"), pmid="1")
        assert client.params_for("eutils")["api_key"] == "KEY123"

    def test_no_api_key_parameter_when_unset(self, monkeypatch):
        client = self._run(monkeypatch, TransparencyAnalyzer(), pmid="1")
        assert "api_key" not in client.params_for("eutils")

    def test_the_request_identifies_the_caller(self, monkeypatch):
        # NCBI asks every E-utilities caller to identify itself with tool+email.
        client = self._run(monkeypatch, TransparencyAnalyzer(email="me@example.org"), pmid="1")
        params = client.params_for("eutils")
        assert params["email"] == "me@example.org"
        assert params["tool"] == "bmlib"

    def test_pmid_recovered_from_the_europepmc_record(self, monkeypatch):
        # A DOI-only caller still gets the PubMed step: the Europe PMC record
        # already fetched carries the PMID, so it costs no extra request.
        client = self._run(
            monkeypatch,
            TransparencyAnalyzer(),
            doi="10.1234/x",
            epmc=_epmc_payload(pmid="999888"),
        )
        assert client.params_for("eutils")["id"] == "999888"

    def test_no_request_without_a_pmid(self, monkeypatch):
        client = self._run(
            monkeypatch, TransparencyAnalyzer(), doi="10.1234/x", epmc=_epmc_payload()
        )
        assert not any("eutils" in url for url in client.urls())

    def test_a_successful_response_counts_as_reachable(self, monkeypatch):
        # PubMed answering alone means the analysis measured something, so the
        # result must be scored rather than reported UNKNOWN.
        client = _RecordingClient(pubmed=_pubmed_xml(coi="None declared."))
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc1", pmid="12345678")
        assert result.risk_level != TransparencyRisk.UNKNOWN
        assert result.unknown_reason is None

    # --- helpers ---

    def _run(self, monkeypatch, analyzer, *, pmid=None, doi=None, epmc=None):
        client = _RecordingClient(epmc=epmc, pubmed=_pubmed_xml())
        _install_fake_client(monkeypatch, client)
        analyzer.analyze("doc1", pmid=pmid, doi=doi)
        return client


class TestFunderInfoIsScoredOnce:
    """`SCORE_FUNDER_INFO` is worth 15 points once, across every funder source.

    CrossRef happens to run first today, which is what made the flag safe to
    compute fresh there. `_Analysis.award_funder_info()` is what keeps it safe
    when it no longer does.
    """

    def test_crossref_respects_an_already_spent_component(self):
        client = _RecordingClient(crossref={"message": {"funder": [{"name": "Some Trust"}]}})
        analysis = _Analysis(funder_info_scored=True)
        TransparencyAnalyzer()._check_crossref(client, "10.1234/x", analysis)
        assert analysis.score == 0
        assert analysis.funder_info_scored is True

    def test_crossref_spends_it_when_nothing_has(self):
        client = _RecordingClient(crossref={"message": {"funder": [{"name": "Some Trust"}]}})
        analysis = _Analysis()
        TransparencyAnalyzer()._check_crossref(client, "10.1234/x", analysis)
        assert analysis.score == SCORE_FUNDER_INFO
        assert analysis.funder_info_scored is True

    def test_a_repeated_crossref_funder_is_one_indicator(self):
        # CrossRef lists one record per award, so an organisation funding two
        # awards on one paper appears twice. The indicator list is a set of
        # findings: one funder is one finding.
        client = _RecordingClient(
            crossref={
                "message": {"funder": [{"name": "Genentech Inc."}, {"name": "Genentech Inc."}]}
            }
        )
        analysis = _Analysis()
        TransparencyAnalyzer()._check_crossref(client, "10.1234/x", analysis)
        assert analysis.indicators == ["Industry funder: Genentech Inc."]

    def test_two_sources_reporting_funders_spend_the_component_once(self, monkeypatch):
        # CrossRef funder records *and* PubMed grants on the same paper. The
        # sub-step tests above pin each in isolation; this pins the composition
        # that analyze() actually runs.
        #
        # The assertion is on the whole score, which works only because this
        # fixture scores nothing else: the abstract is empty, the record is not
        # in EuropePMC, and no OpenAlex or ClinicalTrials.gov response is
        # served. Keep it that way — a fixture that starts scoring elsewhere
        # breaks this test for a reason it is not about.
        client = _RecordingClient(
            crossref={"message": {"funder": [{"name": "Some Trust"}]}},
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(agencies=("Another Trust",)),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc1", doi="10.1234/x")
        assert result.transparency_score == SCORE_FUNDER_INFO


class TestPubMedSignalMerge:
    """How PubMed's signals combine with the ones already gathered."""

    def _analyze(self, monkeypatch, client, **kwargs):
        _install_fake_client(monkeypatch, client)
        return TransparencyAnalyzer().analyze("doc1", **kwargs)

    def test_coi_statement_establishes_disclosure_when_full_text_is_unavailable(self, monkeypatch):
        # The gap this closes: a closed-access paper yields no full text, so
        # COI status was previously undeterminable even though PubMed carries
        # the publisher's statement as structured metadata.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"), pubmed=_pubmed_xml(coi="The authors declare none.")
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.coi_disclosed is True
        assert result.transparency_score == SCORE_COI_DISCLOSED

    def test_a_pubmed_statement_retracts_the_undeterminable_indicator(self, monkeypatch):
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"), pubmed=_pubmed_xml(coi="Dr X consults for Y.")
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert _INDICATOR_COI_UNKNOWN not in result.risk_indicators
        assert _INDICATOR_COI_IN_PUBMED in result.risk_indicators

    def test_a_pubmed_statement_retracts_the_full_text_absence_indicator(self, monkeypatch):
        # Full text was scanned and carried no COI statement, but PubMed has
        # one: leaving "No COI disclosure found in full text" in the result
        # would contradict coi_disclosed=True.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y"),
            full_text="<article><body><p>Methods and results.</p></body></article>",
            pubmed=_pubmed_xml(coi="The authors declare none."),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.coi_disclosed is True
        assert _INDICATOR_NO_COI_IN_FULLTEXT not in result.risk_indicators

    def test_coi_is_not_scored_twice(self, monkeypatch):
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", abstract="Conflict of interest: none."),
            pubmed=_pubmed_xml(coi="The authors declare none."),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.transparency_score == SCORE_COI_DISCLOSED

    def test_an_absent_pubmed_statement_leaves_the_status_unknown(self, monkeypatch):
        # A record without <CoiStatement> means the publisher supplied none to
        # PubMed — not that the paper carries none. Demoting None to False
        # would trigger the missing-COI downgrade on no evidence.
        client = _RecordingClient(epmc=_epmc_payload(pmid="1"), pubmed=_pubmed_xml())
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.coi_disclosed is None

    def test_grants_award_funder_info_when_crossref_found_none(self, monkeypatch):
        # A PMID-only analysis never reaches CrossRef, so PubMed's GrantList is
        # its only possible funder signal.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"), pubmed=_pubmed_xml(agencies=("NCI NIH HHS",))
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.transparency_score == SCORE_FUNDER_INFO

    def test_funder_info_is_not_scored_twice(self, monkeypatch):
        client = _RecordingClient(
            crossref={"message": {"funder": [{"name": "National Cancer Institute"}]}},
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(agencies=("NCI NIH HHS",)),
        )
        result = self._analyze(monkeypatch, client, doi="10.1234/x")
        assert result.transparency_score == SCORE_FUNDER_INFO

    def test_an_industry_agency_carries_structured_confidence(self, monkeypatch):
        # A grant agency is structured metadata, the same evidence class as a
        # CrossRef funder record — not the weaker text-derived signal.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"), pubmed=_pubmed_xml(agencies=("Genentech Inc.",))
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.industry_funding_detected is True
        assert result.industry_funding_confidence == DEFAULT_INDUSTRY_CONFIDENCE

    def test_databank_registration_bypasses_the_abstract_heuristic(self, monkeypatch):
        # Five distinct NCT ids in the abstract read as a review's citation
        # list, so the heuristic credits nothing. The publisher's DataBankList
        # entry asserts *this* paper's registration, so it is trusted.
        abstract = "Registered at ClinicalTrials.gov: " + ", ".join(
            f"NCT0000000{i}" for i in range(1, 6)
        )
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", abstract=abstract),
            pubmed=_pubmed_xml(databanks=(("ClinicalTrials.gov", ("NCT01234567",)),)),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.trial_registered is True
        assert any("clinicaltrials.gov/api" in url for url in client.urls())

    def test_registration_elsewhere_makes_no_claim_about_posted_results(self, monkeypatch):
        # ClinicalTrials.gov cannot answer for an ISRCTN registration, so the
        # result must not read as "registered but nothing posted".
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(databanks=(("ISRCTN", ("ISRCTN12345678",)),)),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.trial_registered is True
        assert result.trial_results_compliant is False
        assert _INDICATOR_NO_POSTED_RESULTS not in result.risk_indicators
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in result.risk_indicators
        assert not any("clinicaltrials.gov/api" in url for url in client.urls())

    def test_an_unusable_clinicaltrials_accession_is_not_called_another_registry(self, monkeypatch):
        # The registration *is* at ClinicalTrials.gov; only its accession was
        # unusable. An indicator saying "registered outside ClinicalTrials.gov"
        # would be a plain falsehood, so the line names the consequence
        # (not checkable) rather than guessing at the cause.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(databanks=(("ClinicalTrials.gov", ("NCT1234",)),)),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.trial_registered is True
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in result.risk_indicators
        assert not any(
            "outside" in ind.lower() or "clinicaltrials.gov" in ind.lower()
            for ind in result.risk_indicators
        )

    def test_one_industry_funder_is_one_indicator(self, monkeypatch):
        # CrossRef and PubMed can both name the same funder. The indicator list
        # is a set of findings; one funder is one finding however many sources
        # report it.
        client = _RecordingClient(
            crossref={"message": {"funder": [{"name": "Genentech Inc."}]}},
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(agencies=("Genentech Inc.",)),
        )
        result = self._analyze(monkeypatch, client, doi="10.1234/x")
        assert result.risk_indicators.count("Industry funder: Genentech Inc.") == 1

    def test_a_deposition_record_establishes_open_data(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(data_banks=("GENBANK", "PDB")), analysis)
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN
        assert "Data deposited in GENBANK" in analysis.indicators
        assert "Data deposited in PDB" in analysis.indicators

    def test_a_deposition_record_supersedes_the_full_texts_denial(self, monkeypatch):
        # A paper can say "individual patient data are not available" and still
        # have deposited its sequences. The structured accession is the harder
        # evidence, and the line that contradicts it is retracted rather than
        # left for whoever reads the indicators to reconcile.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", abstract="The data are not available."),
            pubmed=_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.data_availability_level == "full_open"
        assert _INDICATOR_DATA_NOT_AVAILABLE not in result.risk_indicators
        assert "Data deposited in GENBANK" in result.risk_indicators

    def test_a_closed_access_paper_can_earn_open_data(self, monkeypatch):
        # The reason for the signal: without full text there is nothing for the
        # text scan to read, so PubMed's databank list is the only
        # data-availability evidence such a paper can carry.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="N"),
            pubmed=_pubmed_xml(databanks=(("Dryad", ("10.5061/dryad.x",)),)),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.full_text_analyzed is False
        assert result.data_availability_level == "full_open"
        assert result.transparency_score >= SCORE_DATA_FULL_OPEN

    def test_the_merge_applies_both_of_its_branches_to_one_list(self):
        # The COI branch retracts lines while the funder branch appends. When
        # the merge returned a copy, a caller that ignored the return value
        # saw a half-applied merge; mutating the carrier makes that
        # unrepresentable. This pins that both branches land together.
        analysis = _Analysis(indicators=[_INDICATOR_NO_COI_IN_FULLTEXT], coi_disclosed=False)
        _merge_pubmed_signals(
            _PubMedSignals(coi_statement=True, funders=("Genentech Inc.",)),
            analysis,
        )
        assert _INDICATOR_NO_COI_IN_FULLTEXT not in analysis.indicators
        assert _INDICATOR_COI_IN_PUBMED in analysis.indicators
        assert "Industry funder: Genentech Inc." in analysis.indicators
        assert analysis.coi_disclosed is True

    def test_an_unreachable_pubmed_is_survivable(self, monkeypatch):
        client = _RecordingClient(epmc=_epmc_payload(pmid="1"), pubmed=None)
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.risk_level != TransparencyRisk.UNKNOWN
