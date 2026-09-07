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

import dataclasses
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bmlib.transparency.analyzer import (
    _BUG_TYPES,
    _CLINICALTRIALS_ORDINARY_STATUSES,
    _CROSSREF_ORDINARY_STATUSES,
    _DATA_LEVEL_RANK,
    _DATA_PATTERNS,
    _DEPOSITION_DATABANK_LEVELS,
    _EUROPEPMC_SEARCH_ORDINARY_STATUSES,
    _FULL_TEXT_PROVENANCE_INDICATORS,
    _INDICATOR_COI_IN_PUBMED,
    _INDICATOR_COI_UNKNOWN,
    _INDICATOR_DATA_DEPOSITED_PREFIX,
    _INDICATOR_DATA_NOT_AVAILABLE,
    _INDICATOR_FUNDERS_NOT_READABLE,
    _INDICATOR_INDUSTRY_COI,
    _INDICATOR_NO_COI_IN_FULLTEXT,
    _INDICATOR_NO_FUNDER_INFO,
    _INDICATOR_NO_POSTED_RESULTS,
    _INDICATOR_RESULTS_NOT_CHECKABLE,
    _INDICATORS_RETRACTED_BY_PUBMED_COI,
    _NESTED_ARTICLE_ALTERNATION,
    _NESTED_ARTICLE_ELEMENTS,
    _NESTED_ARTICLE_TOKEN_RE,
    _OPENALEX_ORDINARY_STATUSES,
    _PUBMED_ORDINARY_STATUSES,
    _STATUSES_WITH_NO_PROVENANCE_LINE,
    _TRIAL_REGISTRY_NAMES,
    _UNTERMINATED_OPENER_NAMES,
    DEFAULT_INDUSTRY_CONFIDENCE,
    EUROPEPMC_REST_BASE,
    SCORE_CITED,
    SCORE_COI_DISCLOSED,
    SCORE_DATA_FULL_OPEN,
    SCORE_DATA_ON_REQUEST,
    SCORE_FUNDER_INFO,
    SCORE_OPEN_ACCESS,
    SCORE_RESULTS_POSTED,
    SCORE_TRIAL_REGISTERED,
    TEXT_INDUSTRY_CONFIDENCE,
    TransparencyAnalyzer,
    _Analysis,
    _epmc_records,
    _find_trial_ids,
    _json_bool,
    _json_count,
    _json_object,
    _json_text,
    _merge_pubmed_signals,
    _note_full_text_provenance,
    _parse_pubmed_signals,
    _pmid_from_epmc,
    _PubMedSignals,
    _score_data_availability,
    _strip_nested_articles,
    _UnterminatedMarkupError,
    _user_agent,
)
from bmlib.transparency.models import (
    _ANSWERED_TRIAL_RESULTS_STATUSES,
    _NOT_REFUSED_FULL_TEXT_STATUSES,
    _REFUSED_FULL_TEXT_STATUSES,
    _UNANSWERED_TRIAL_RESULTS_STATUSES,
    FullTextStatus,
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    TransparencyUnknownReason,
    TrialResultsStatus,
    calculate_risk_level,
)


class _FakeResponse:
    #: Distinguishes "no body configured" from a body that *is* `None` or an
    #: empty array. `json_data or {}` could not: it silently served `{}` — a
    #: well-formed empty object — for both, so eight rows of the hostile-body
    #: net claimed to serve a shape the fixture could not deliver, and a
    #: future `false`/`0` row would have been neutered the same way (PR #208's
    #: review). No caller passes either value except through this default.
    _UNSET = object()

    def __init__(self, status_code=200, json_data=_UNSET, text=""):
        self.status_code = status_code
        self._json = {} if json_data is _FakeResponse._UNSET else json_data
        self.text = text

    @property
    def content(self) -> bytes:
        """The encoded body, as httpx serves it.

        The analyzer quantifies a refusal in *bytes*, which `text` cannot
        answer for a body carrying non-ASCII — so the fake has to distinguish
        the two exactly as httpx does, or the test pins the wrong number.
        """
        return self.text.encode("utf-8")

    def json(self):
        return self._json


class _FakeFullTextClient:
    """A fake httpx client that serves a single full-text XML body.

    **It matches the whole URL, not its suffix, and that is the point.**
    This fake used to accept any ``url.endswith("/fullTextXML")``, so the
    path the analyzer built was asserted nowhere and issue #184 — an extra
    ``{source}/`` segment that made every live fetch 404 — sat undetected
    behind tests that each *looked* like a full-text test. Matching exactly
    turns the ones that actually fetch into URL checks for free, the
    ``parser_log`` fixture's trick one module over: a fake that serves any
    path can only ever confirm that the analyzer asked for something.

    Measured, and say which tree each number is of: with the suffix match,
    reintroducing the defect passes **236 of `main`'s 236**; with the whole
    URL matched here and in :class:`_RecordingClient`, it reddens **52 of
    this branch's 249**, of which **43 are tests that predate the branch**.
    Not *every* test reaching the fake: those passing ``None`` or
    ``in_epmc="N"`` never fetch, so they are silent on the address by
    construction and correctly stay green.

    ``served_urls`` records what was asked for, so a test can assert on the
    URL directly rather than only through the body it got back.
    """

    def __init__(self, full_text: str | None, ext_id: str = "PMC123"):
        self._full_text = full_text
        self._url = f"{EUROPEPMC_REST_BASE}/{ext_id}/fullTextXML"
        self.served_urls: list[str] = []

    def get(self, url, **kwargs):
        self.served_urls.append(url)
        if url == self._url:
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

    @pytest.mark.parametrize(
        ("weaker", "stronger"),
        [
            ("unknown", "not_available"),
            ("unknown", "on_request"),
            ("unknown", "full_open"),
            ("not_available", "on_request"),
            ("not_available", "full_open"),
            ("on_request", "full_open"),
        ],
    )
    def test_the_stronger_data_level_wins_in_either_arrival_order(self, weaker, stronger):
        # Two sources produce `data_level` and neither can know which ran
        # first, so the merge must not depend on order — the same rule
        # `industry_confidence` follows.
        forwards, backwards = _Analysis(), _Analysis()
        forwards.note_data_level(weaker)
        forwards.note_data_level(stronger)
        backwards.note_data_level(stronger)
        backwards.note_data_level(weaker)
        assert forwards.data_level == stronger
        assert backwards.data_level == stronger

    def test_an_explicit_denial_outranks_silence(self):
        # `not_available` is a finding; `unknown` is the absence of one.
        analysis = _Analysis()
        analysis.note_data_level("not_available")
        analysis.note_data_level("unknown")
        assert analysis.data_level == "not_available"

    def test_a_level_outside_the_ranking_raises(self):
        # "restricted" is a level `calculate_risk_level` accepts from callers
        # who compute it themselves, and one the analyzer has never produced.
        # Ranking an unknown string at zero would silently demote it below
        # everything; failing loudly is the point.
        with pytest.raises(KeyError):
            _Analysis().note_data_level("restricted")


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


class TestTheFullTextUrlIsTheOneEuropePmcServes:
    """The address the analyzer asks for, pinned against the live API's shape.

    Issue #184: the URL carried an extra ``{source}/`` segment
    (``.../rest/PMC/PMC13426601/fullTextXML``), which Europe PMC answers with
    its own HTTP 404 and ``content-length: 0``. Every fetch failed, silently —
    a non-200 was then the one outcome that deliberately did not warn, since
    narrowed to the 404 alone by #191 — so the
    module scored every open-access paper on its abstract alone, losing up to
    30 points and the ability to ever set ``coi_disclosed=False``.

    Measured against the live API on 2026-09-05, and it is the *path shape*
    rather than one endpoint or one article: the single-segment form serves
    200 for PMC12900525, PMC3258128, PMC10030002, PMC13426601 and six ``PPR``
    accessions, while ``{source}/{ext_id}``, the bare numeric id and the PMID
    all 404.
    """

    def test_the_url_carries_the_accession_and_no_source_segment(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC123")
        assert client.served_urls == [
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML"
        ]

    def test_the_source_is_not_in_the_url_whatever_it_says(self):
        """``source`` addresses nothing here — ``MED`` and ``PMC`` build one URL.

        The defect's own shape: a record's ``source`` was interpolated as a
        path segment, so this is the assertion that fails on it rather than
        one that merely happens to.
        """
        analyzer = TransparencyAnalyzer()
        built = []
        for source in ("PMC", "MED", "PPR"):
            client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
            analyzer._fetch_europepmc_fulltext(client, source, "PMC123")
            built.append(client.served_urls)
        # Named, not merely equal: three empty lists are all equal too, so a
        # mutant that stops fetching altogether satisfies the differential
        # assertion on its own. The positive control is what excludes it.
        expected = [f"{EUROPEPMC_REST_BASE}/PMC123/fullTextXML"]
        assert built == [expected, expected, expected]

    def test_a_preprint_accession_is_passed_through_unnormalised(self):
        """A ``PPR`` accession is the address, so it must not be made a PMCID.

        75,760 of Europe PMC's 12,220,678 ``IN_EPMC:Y`` records — 0.62%,
        their own hit counts rather than a draw, 2026-09-05 — are preprints
        carrying no ``pmcid`` at all, so ``record["id"]`` is what addresses
        them and it is a ``PPR…`` accession. Six of them serve 200 on this
        form. That is why this fix is *not* ``fulltext/service.py``'s
        ``_normalise_pmc_id``, which would reject every one of them: the two
        modules agree on the URL and must not be deduplicated into agreeing
        on the identifier.
        """
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PPR1303959")
        fetch = analyzer._fetch_europepmc_fulltext(client, "PPR", "PPR1303959")
        assert client.served_urls == [
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PPR1303959/fullTextXML"
        ]
        assert fetch.text is not None

    def test_a_record_with_no_pmcid_is_addressed_by_its_id(self):
        """The line that actually supplies a preprint's accession.

        Every other test in this class calls ``_fetch_europepmc_fulltext``
        directly, which steps over ``record["pmcid"] or record["id"]`` in
        ``_check_europepmc`` — the *only* place a ``PPR…`` accession is
        chosen. Deleting that fallback, which loses the address for all
        75,760 preprints this class's docstrings argue about, passed the
        whole suite: the module's most-argued claim had nothing behind it.

        So this one goes through ``_check_europepmc``, with a record shaped
        as Europe PMC serves a preprint — ``pmcid`` absent entirely, ``id``
        carrying the accession (verified live: ``SRC:PPR AND IN_EPMC:Y``
        records return ``{'id': 'PPR1303959', 'pmcid': None}``).
        """
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PPR1303959")
        analysis = _Analysis()
        analyzer._check_europepmc(
            client,
            {
                "resultList": {
                    "result": [
                        {
                            "abstractText": "",
                            "inEPMC": "Y",
                            "source": "PPR",
                            "id": "PPR1303959",
                        }
                    ]
                }
            },
            analysis,
        )
        assert client.served_urls == [f"{EUROPEPMC_REST_BASE}/PPR1303959/fullTextXML"]
        assert analysis.full_text_analyzed is True

    def test_a_pmcid_is_preferred_over_the_id_that_stands_in_for_it(self):
        """The other half: ``id`` is the fallback, not the address.

        A ``MED`` record carries both — ``id`` being the PMID — and the PMID
        form is measured to 404, so preferring it would break every record
        that has a PMCID. Pins the ``or`` in both directions.
        """
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        analysis = _Analysis()
        analyzer._check_europepmc(
            client,
            {
                "resultList": {
                    "result": [
                        {
                            "abstractText": "",
                            "inEPMC": "Y",
                            "source": "MED",
                            "id": "12345678",
                            "pmcid": "PMC123",
                        }
                    ]
                }
            },
            analysis,
        )
        assert client.served_urls == [f"{EUROPEPMC_REST_BASE}/PMC123/fullTextXML"]
        assert analysis.full_text_analyzed is True

    def test_the_two_modules_agree_on_the_base_and_on_a_pmcid(self):
        """The defect was the two modules disagreeing, so pin them together.

        ``fulltext/service.py`` was always right; ``transparency`` was the
        broken one. Importing both here creates no runtime dependency —
        ``transparency`` still needs nothing from ``fulltext``, and the two
        deliberately hold **two** constants rather than one, since sharing
        one would be the dependency this module does not have.

        **Say what this pins and what it does not.** It pins the bases equal,
        and — for a plain PMCID, where the two modules' *identifiers* also
        agree — that ``transparency``'s URL is the one ``service.py``'s
        normalisation and base compose to. It does not evaluate
        ``service.py``'s own f-string, so that module regaining a path
        segment (this defect, one module over) is not caught here; nothing
        short of calling its fetch would catch that, and the identifiers
        diverge by design for ``PPR`` — the test above.
        """
        from bmlib.fulltext.service import EUROPE_PMC_BASE, _normalise_pmc_id

        assert EUROPEPMC_REST_BASE == EUROPE_PMC_BASE

        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC3258128")
        analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC3258128")
        assert client.served_urls == [
            f"{EUROPE_PMC_BASE}/{_normalise_pmc_id('PMC3258128')}/fullTextXML"
        ]

    def test_a_record_naming_no_source_is_still_fetched(self):
        """``source`` addressed the article until #184; now it addresses nothing.

        The guard beside the URL required *both*, which was right while the
        source was a path segment and is over-strict now — it would refuse a
        fetch that works. Asking what else a guard was holding when its
        reason goes is this module's own rule.
        """
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        fetch = analyzer._fetch_europepmc_fulltext(client, None, "PMC123")
        assert client.served_urls == [f"{EUROPEPMC_REST_BASE}/PMC123/fullTextXML"]
        assert fetch.text is not None
        assert fetch.status is FullTextStatus.ANALYZED

    def test_an_unnamed_source_does_not_print_as_a_path_segment(self, caplog):
        """``None/PMC123`` is the shape this fix removed from the URL.

        ``subject`` names the article in six log lines, four of them
        refusal WARNINGs. Built unconditionally as ``f"{source}/{ext_id}"``
        it renders a two-segment path for a record naming no source — in the
        one module whose signature defect *was* a spurious two-segment path,
        printed beside the corrected single-segment URL on the same DEBUG
        line. Nothing pinned the rendering in either direction.
        """
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._fetch_europepmc_fulltext(client, None, "PMC999", "doc-1")
        assert "None/PMC999" not in caplog.text
        assert "for PMC999 (document doc-1)" in caplog.text

    def test_a_named_source_still_names_the_subject(self, caplog):
        """The other direction: a source that *is* named stays in the line."""
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._fetch_europepmc_fulltext(client, "MED", "PMC999", "doc-1")
        assert "MED/PMC999 (document doc-1)" in caplog.text

    def test_a_record_naming_no_accession_is_not_fetched(self):
        """The other half of the guard is the half that still holds."""
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        fetch = analyzer._fetch_europepmc_fulltext(client, "PMC", None)
        assert client.served_urls == []
        assert fetch.text is None
        assert fetch.status is FullTextStatus.NOT_ATTEMPTED

    def test_a_404_names_the_url_at_debug(self, caplog):
        """#184 lived a release inside this silence, so the URL is logged.

        DEBUG and not WARNING, and that level is measured: of 150
        ``IN_EPMC:Y`` records probed on 2026-09-05, no ``isOpenAccess: N``
        record served (0 of 53) and ``isOpenAccess: Y`` still 404'd in 35 of
        97 — so a 404 is the ordinary majority outcome for this module's
        gate, and warning on it would be noise on every closed-access paper.
        The draw is of 404s and, since #191, so is the branch: every other
        status WARNs, which
        ``test_a_status_other_than_404_is_not_a_statement_about_this_article``
        pins.
        The assertion is on the *URL* because the URL is the claim: ``HTTP
        %d`` would pass whatever address the module asked for, which is
        exactly how #184 stayed hidden.

        The level is asserted on the record that carries the URL rather than
        via ``caplog.at_level``, which admits anything at or above DEBUG — so
        a line moved to INFO passed this and its ``does_not_warn`` companion
        both.
        """
        analyzer = TransparencyAnalyzer()
        # Serves only PMC123, so asking for anything else is a 404.
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            fetch = analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC999")
        assert fetch.status is FullTextStatus.NOT_SERVED
        url = f"{EUROPEPMC_REST_BASE}/PMC999/fullTextXML"
        named = [r for r in caplog.records if url in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.DEBUG

    def test_a_404_does_not_warn(self, caplog):
        """The other half of the level claim, and the half a mutant flips.

        A **404**, not any non-200: since #191 every other status WARNs, so
        the old name gave the opposite answer to the test that pins it.
        """
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article>body</article>", ext_id="PMC123")
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC999")
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_the_search_endpoint_is_built_from_the_same_base(self):
        """One base, so a move cannot leave the two Europe PMC calls apart.

        The assertion is the **literal** URL, not ``f"{EUROPEPMC_REST_BASE}
        /search"``: written against the constant, source and assertion move
        together, so the one thing the name promises — that a drift in the
        base is caught — is the one thing it could not detect.
        """
        analyzer = TransparencyAnalyzer()
        seen = []

        class _Client:
            def get(self, url, **kwargs):
                seen.append(url)
                return _FakeResponse(status_code=200, json_data={})

        analyzer._query_europepmc(_Client(), "DOI:10.1/x")
        assert seen == ["https://www.ebi.ac.uk/europepmc/webservices/rest/search"]


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
                if url.endswith("/fullTextXML"):
                    # One address, for `_RecordingClient`'s reason: a
                    # substring match is satisfied by #184's two-segment form.
                    if url != f"{EUROPEPMC_REST_BASE}/PMC123/fullTextXML":
                        return _FakeResponse(status_code=404)
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


class TestANestedArticleIsNotThisArticles:
    """Issue #119 — reviewer prose must not answer for the article.

    ``_check_europepmc`` scans the raw ``fullTextXML`` body, and a
    ``<sub-article>`` holds a complete article of its own: a peer-review round,
    an author response, a translation, or Europe PMC's injected
    ``associated-data`` block. Every one of those is written in the exact
    vocabulary these scans hunt for — a reviewer's "I declare no competing
    interests" was read as the *article's* disclosure — so the regions are
    removed before anything reads the string.

    Measured over PMC's ``oa_comm`` baseline package
    ``PMC012xxxxxx`` (2025-06-26, 97,909 articles): 3,382 (3.45%) carry a
    region this removes — 3,377 a ``<sub-article>``, 5 more a top-level
    ``<response response-type="reply">`` and no ``<sub-article>`` — and 602 of
    those (0.61% of the corpus) have at least one of the four scan outputs move
    once the regions go: 499 the data-availability level, 125 the COI cue
    phrase (4 of them flipping the stored tri-state, the tagged section usually
    still firing), 6 the industry-COI signal and 1 the tagged section itself.
    None of the five ``<response>`` articles is among the 602, so that element
    is rare rather than absent.

    Two populations here measure **empty** and are tested as guards rather than
    as shapes anyone has seen: no article leaves a region open, and none is
    emptied by the removal (all 3,389 carriers across this corpus and an
    880-article Europe PMC draw keep their ``<body>``, the least of them
    retaining 32.2% of its bytes). Nesting, by contrast, is exercised — 98 of
    the 3,382 carriers nest — and so are siblings, 2,855 of them.

    **The lexer's four skip tokens have no measured population on the input
    this module actually reads.** Three archive articles carry a ``<response>``
    inside a commented-out Springer ``<authorqueries>`` block, but Europe PMC's
    ``fullTextXML`` serves those same three with no comments at all, and
    carries a comment in 0 of the 880-article draw against 25.6% of the
    archive. They are tested because the argument for them is structural, not
    because a deposit has been seen to need them.
    """

    # ---- the lexer ----
    #
    # In well-formed XML a literal "<" can only open markup, so the constructs
    # below are the *complete* set of places the characters "<sub-article" can
    # appear without being a start tag. Each has a test, because a scanner over
    # markup is only as good as the list of things it knows are not markup.

    def test_a_nested_article_is_removed_and_its_neighbours_are_kept(self):
        stripped = _strip_nested_articles(
            "<article><body><p>Ours.</p>"
            '<sub-article article-type="peer-review"><body><p>Theirs.</p></body></sub-article>'
            "<p>Ours again.</p></body></article>"
        )
        assert stripped == "<article><body><p>Ours.</p><p>Ours again.</p></body></article>"

    def test_a_response_is_removed_too(self):
        stripped = _strip_nested_articles(
            "<article><p>Ours.</p><response><p>Theirs.</p></response></article>"
        )
        assert "Theirs" not in stripped
        assert "Ours" in stripped

    def test_a_nested_nested_article_does_not_end_the_region_early(self):
        # JATS nests these — a <response> sits inside the <sub-article> it
        # answers — so the inner close must not re-admit the outer's prose.
        # A flag rather than a depth reads "Outer tail" as the article's.
        stripped = _strip_nested_articles(
            "<article><p>Ours.</p>"
            "<sub-article><p>Round one.</p>"
            "<response><p>Reply.</p></response>"
            "<p>Outer tail.</p></sub-article>"
            "<p>Ours again.</p></article>"
        )
        assert stripped == "<article><p>Ours.</p><p>Ours again.</p></article>"

    def test_a_self_closing_nested_article_removes_nothing(self):
        # It opens no region, so treating it as an open would swallow the rest
        # of the document — and, with nothing to close it, refuse the article.
        stripped = _strip_nested_articles("<article><sub-article/><p>Ours.</p></article>")
        assert stripped == "<article><sub-article/><p>Ours.</p></article>"

    def test_a_nested_article_named_in_a_comment_opens_no_region(self):
        # Exact equality, not `"Ours" in stripped`: the fixture's element is
        # unbalanced, so deleting the comment branch returns None and the
        # containment check dies by TypeError — a kill that would survive the
        # branch being deleted if a publisher's comment were balanced, which
        # is the only shape ever seen. Springer's is.
        xml = (
            "<article><!-- <authorqueries><aq><response>Answered</response></aq>"
            "</authorqueries> --><p>Ours.</p></article>"
        )
        assert _strip_nested_articles(xml) == xml

    def test_a_multi_line_comment_is_still_one_token(self):
        # `re.DOTALL` is what makes "." cross a newline, and every other
        # fixture here is one line. Real comments are not: the Springer
        # deposits this rule was measured on span lines. Without the flag the
        # comment ends at the first newline and its <response> is stripped.
        xml = (
            "<article><!-- <authorqueries>\n<aq><response>Answered</response></aq>\n"
            "</authorqueries> --><p>Ours.</p></article>"
        )
        assert _strip_nested_articles(xml) == xml

    def test_a_nested_article_named_in_a_cdata_section_opens_no_region(self):
        xml = "<article><p><![CDATA[write <sub-article> to nest one]]></p><p>Ours.</p></article>"
        assert _strip_nested_articles(xml) == xml

    def test_a_multi_line_cdata_section_is_still_one_token(self):
        # As above: without `re.DOTALL` the section is not matched at all, the
        # <sub-article> inside it reads as an open, and the whole article is
        # refused — a silent fallback to the abstract, for a document that was
        # served in full.
        xml = "<article><p><![CDATA[write\n<sub-article>\nto nest one]]></p><p>Ours.</p></article>"
        assert _strip_nested_articles(xml) == xml

    def test_a_nested_article_named_in_a_processing_instruction_opens_no_region(self):
        xml = '<article><?publisher drop="<sub-article>"?><p>Ours.</p></article>'
        assert _strip_nested_articles(xml) == xml

    def test_a_multi_line_processing_instruction_is_still_one_token(self):
        xml = '<article><?publisher drop="\n<sub-article>\n"?><p>Ours.</p></article>'
        assert _strip_nested_articles(xml) == xml

    def test_a_nested_article_named_in_the_doctype_internal_subset_opens_no_region(self):
        # The entity's replacement text is an *opening* tag on purpose: a
        # self-closing one is already refused by the rule above, so a fixture
        # written that way passes whether or not the doctype is lexed —
        # measured, as a mutant deleting the doctype token survived it.
        stripped = _strip_nested_articles(
            '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS 1.4//EN" "JATS.dtd"'
            ' [<!ENTITY review "<sub-article>">]>'
            "<article><p>Ours.</p></article>"
        )
        assert stripped is not None
        assert "Ours" in stripped

    def test_an_unclosed_nested_article_refuses_the_document(self):
        # The tail cannot be shown to be the article's own text. Keeping it is
        # the defect; dropping it silently would manufacture "no COI statement
        # in full text", which is what triggers the missing-COI downgrade. So
        # the document is refused and the analysis falls back to the abstract.
        assert (
            _strip_nested_articles("<article><p>Ours.</p><sub-article><p>Theirs.</p></article>")
            is None
        )

    def test_an_unmatched_close_is_not_an_imbalance(self):
        # Malformed the other way round, and harmless: no nested prose can
        # reach the scans through it, so refusing the article would cost a
        # real signal to no purpose.
        stripped = _strip_nested_articles("<article><p>Ours.</p></sub-article></article>")
        assert stripped is not None
        assert "Ours" in stripped

    def test_a_document_carrying_none_is_returned_unchanged(self):
        xml = "<article><body><p>Ours.</p></body></article>"
        assert _strip_nested_articles(xml) == xml

    def test_sibling_regions_keep_the_article_prose_between_them(self):
        # The dominant real shape: 2,855 of the 3,382 carriers hold two or
        # more top-level regions. None of them has prose between two rounds,
        # so nothing in a corpus would catch a splice that dropped it.
        stripped = _strip_nested_articles(
            "<article><p>One.</p>"
            "<sub-article><p>Round one.</p></sub-article>"
            "<p>Two.</p>"
            "<sub-article><p>Round two.</p></sub-article>"
            "<p>Three.</p></article>"
        )
        assert stripped == "<article><p>One.</p><p>Two.</p><p>Three.</p></article>"

    def test_an_unescaped_gt_in_an_attribute_does_not_truncate_the_tag(self):
        # ">" is legal unescaped in an XML attribute value; only "<" must be
        # escaped. Read to the first ">", a self-closing tag loses its "/",
        # reads as an open, and refuses the whole article — a full text
        # discarded for a well-formed document.
        xml = '<article><p>Ours.</p><sub-article specific-use="a>b"/><p>More.</p></article>'
        assert _strip_nested_articles(xml) == xml
        stripped = _strip_nested_articles(
            '<article><p>Ours.</p><sub-article xlink:title="a > b">'
            "<p>Theirs.</p></sub-article><p>More.</p></article>"
        )
        assert stripped == "<article><p>Ours.</p><p>More.</p></article>"

    def test_a_doctype_internal_subset_may_contain_a_closing_bracket(self):
        # "]" is legal inside an entity's replacement text, so the subset ends
        # at the "]" before the ">", not at the first one. Closed at the first,
        # the doctype token does not match and the article is refused.
        xml = (
            '<!DOCTYPE article [<!ENTITY range "1]2"><!ENTITY r "<sub-article>">]>'
            "<article><p>Ours.</p></article>"
        )
        assert _strip_nested_articles(xml) == xml

    def test_a_doctype_system_literal_may_contain_a_greater_than(self):
        xml = (
            '<!DOCTYPE article SYSTEM "j>ats.dtd" [<!ENTITY r "<sub-article>">]>'
            "<article><p>Ours.</p></article>"
        )
        assert _strip_nested_articles(xml) == xml

    def test_an_element_whose_name_merely_begins_with_one_of_these_is_not_one(self):
        # "-", "." and ":" are all legal in an XML name and all word
        # boundaries, so `\b` admitted <response-note> and <sub-article-x>
        # and stripped prose no JATS element owns. Measured 0 across 98,789
        # articles, so this is defence-in-depth against a name the set gains
        # or a vocabulary JATS does not own — not a shape anyone has seen.
        for xml in (
            '<article><p>The authors <response-note id="n1">see note</response-note>'
            " declare nothing.</p></article>",
            "<article><sub-article-supplement><p>Ours.</p></sub-article-supplement></article>",
            "<article><response.x>Ours.</response.x></article>",
            "<article><ns:response>Ours.</ns:response></article>",
            "<article><responses><p>Ours.</p></responses></article>",
        ):
            assert _strip_nested_articles(xml) == xml

    def test_each_branch_sets_only_its_own_groups(self):
        # The loop tells a start tag from a comment by the groups being unset,
        # and an unterminated construct from either by a group of its own.
        # Named groups make that independent of the pattern's shape; with
        # positional ones, a group added to any earlier branch would have made
        # a comment look like a start tag, with nothing failing.
        for token in (
            "<!-- c -->",
            "<![CDATA[c]]>",
            "<?pi c?>",
            '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS 1.4//EN" "JATS.dtd">',
        ):
            match = _NESTED_ARTICLE_TOKEN_RE.match(token)
            assert match is not None, token
            assert match.group("closing") is None
            assert match.group("element") is None
            assert match.group("attributes") is None
            assert match.group("unterminated") is None
        opening = _NESTED_ARTICLE_TOKEN_RE.match("<sub-article>")
        assert opening is not None
        assert opening.group("closing") == ""
        assert opening.group("element") == "sub-article"
        assert opening.group("attributes") == ""
        assert opening.group("unterminated") is None
        # The refusal branch is the mirror image: it is reached only when
        # every branch above it failed, and it names the opener it stopped at.
        unterminated = _NESTED_ARTICLE_TOKEN_RE.match("<!-- c")
        assert unterminated is not None
        # Without the "<", which is outside the group so that every top-level
        # branch opens with the same literal — see the test below.
        assert unterminated.group("unterminated") == "!--"
        assert unterminated.group("closing") is None
        assert unterminated.group("element") is None
        assert unterminated.group("attributes") is None

    # ---- what the scans then see ----

    def test_a_reviewers_disclosure_is_not_this_articles(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods and results.</p></body>"
            '<sub-article article-type="peer-review"><body>'
            "<p>The reviewers declare no competing interests.</p>"
            "</body></sub-article></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is False
        assert _INDICATOR_NO_COI_IN_FULLTEXT in analysis.indicators

    def test_the_articles_own_disclosure_is_still_found(self):
        # The control: the same statement in the article's own back matter,
        # beside a review round, is still the article's.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods.</p></body>"
            '<sub-article article-type="peer-review"><body>'
            "<p>The reviewers declare no competing interests.</p>"
            "</body></sub-article>"
            '<back><fn-group><fn fn-type="COI-statement">'
            "<p>The authors declare no conflict of interest.</p></fn></fn-group></back>"
            "</article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True

    def test_a_review_rounds_data_statement_does_not_set_the_level(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods.</p></body>"
            "<sub-article><body><p>The data are available upon request.</p></body>"
            "</sub-article></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.data_level == "unknown"

    def test_the_articles_own_data_statement_still_sets_the_level(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>The data are available upon request.</p></body>"
            "<sub-article><body><p>Round one.</p></body></sub-article></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.data_level == "on_request"

    def test_an_industry_tie_disclosed_in_a_review_round_is_not_this_articles(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods.</p></body>"
            '<sub-article article-type="peer-review"><back>'
            '<fn-group><fn fn-type="COI-statement">'
            "<p>Reviewer 2 is an employee of Genentech.</p>"
            "</fn></fn-group></back></sub-article>"
            '<back><fn-group><fn fn-type="COI-statement">'
            "<p>The authors declare no conflict of interest.</p></fn></fn-group></back>"
            "</article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True
        assert analysis.industry_funding is False
        assert _INDICATOR_INDUSTRY_COI not in analysis.indicators

    def test_a_refused_document_is_not_scanned_as_full_text(self, caplog):
        # An imbalance leaves the COI status *unknown*, never "absent": only
        # an explicit False triggers the missing-COI HIGH-risk rule, and no
        # document was successfully scanned here. WARNING rather than ERROR —
        # a publisher's deposit can reach this, so it is not a bmlib defect.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods.</p></body><sub-article><p>Round one.</p></article>"
        )
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is False
        assert analysis.coi_disclosed is None
        # Served and refused, so the *status* says so (issue #161) and the
        # provenance line `analyze()` appends from it says so in prose (issue
        # #203). This step's COI line makes the COI claim and nothing else: it
        # used to carry "(full text served but not usable)" inside it, and the
        # PubMed retraction then took that away with the COI half.
        assert analysis.full_text_status is FullTextStatus.UNCLOSED_REGION
        assert _INDICATOR_COI_UNKNOWN in analysis.indicators
        # The level is asserted, not just the message: `at_level(WARNING)`
        # admits ERROR, and ERROR is the level this module reserves for "bmlib
        # is wrong" — the distinction this test's own comment turns on.
        matching = [r for r in caplog.records if "unclosed nested article" in r.getMessage()]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING

    def test_a_body_that_never_terminates_is_reported_as_what_it_is(self, caplog):
        # The third segmentation outcome (issue #160), and a different claim
        # from the other two: an unclosed region is a document bmlib will not
        # segment, and this is a document that did not arrive — an HTTP 200 is
        # not a promise that the whole body came with it. Reported apart from
        # the other refusal rather than folded into it, which would put this
        # module's own issue #161 shape one level down.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article><body><p>Methods.</p><!-- truncated here")
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is False
        assert analysis.coi_disclosed is None
        assert analysis.full_text_status is FullTextStatus.UNTERMINATED_MARKUP
        assert _INDICATOR_COI_UNKNOWN in analysis.indicators
        matching = [r for r in caplog.records if "is not well-formed" in r.getMessage()]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING
        # The construct, not merely the fact: "which one and where" is what an
        # operator cannot re-derive without lexing the body a second time.
        assert "unterminated comment" in matching[0].getMessage()
        assert "offset 30" in matching[0].getMessage()

    def test_another_exception_from_the_strip_is_not_swallowed(self, monkeypatch):
        # The narrow `except _UnterminatedMarkupError` is the whole of what
        # keeps a bmlib defect out of the abstract fallback, and widening it
        # to `except Exception` passed all 204 tests. That is the swallow
        # #159 moved this call out of the request handler to avoid: the tier
        # chain would report the article as unavailable and the defect would
        # never surface. Only the documented raise may be caught here.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article><body><p>Methods.</p></body></article>")

        def _boom(_xml):
            raise ZeroDivisionError("a bmlib defect, not a truncated body")

        monkeypatch.setattr("bmlib.transparency.analyzer._strip_nested_articles", _boom)
        with pytest.raises(ZeroDivisionError):
            analyzer._check_europepmc(client, _epmc_record(), _Analysis())

    def test_a_document_that_is_all_nested_articles_is_reported_not_dropped(self, caplog):
        # `_strip_nested_articles` returns "" here, which the caller's
        # `if full_text:` reads as "nothing was served" — the one outcome that
        # would otherwise reach storage with no signal anywhere. Measured
        # empty: all 3,389 carriers across both corpora keep their <body>.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<sub-article><p>Round one.</p></sub-article>")
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is False
        assert analysis.coi_disclosed is None
        matching = [r for r in caplog.records if "entirely nested articles" in r.getMessage()]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING

    def test_a_tagged_coi_section_of_a_review_round_is_not_this_articles(self):
        # The fourth reader, and the only one that can assert a disclosure
        # with no cue phrase anywhere in the document (issue #13). It is the
        # "1 the tagged COI section" row of the measurement. The negative
        # control below is what stops this passing for the wrong reason: the
        # sibling test plants a container in *both* places, so it holds with
        # or without the strip and pins the industry reader instead.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods and results, no disclosure wording at all.</p></body>"
            '<sub-article article-type="peer-review"><back><fn-group>'
            '<fn fn-type="COI-statement"><p>Reviewer 1 has nothing to declare.</p></fn>'
            "</fn-group></back></sub-article></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is False
        assert _INDICATOR_NO_COI_IN_FULLTEXT in analysis.indicators

    def test_the_articles_own_tagged_coi_section_is_still_found(self):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods and results, no disclosure wording at all.</p></body>"
            '<sub-article article-type="peer-review"><body><p>Round one.</p></body></sub-article>'
            '<back><fn-group><fn fn-type="COI-statement">'
            "<p>Reviewer 1 has nothing to declare.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.coi_disclosed is True

    def test_the_articles_own_industry_tie_is_still_found(self):
        # The positive control for the industry reader: the same disclosure,
        # in the article's own back matter, beside a review round.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Methods.</p></body>"
            '<sub-article article-type="peer-review"><body><p>Round one.</p></body></sub-article>'
            '<back><fn-group><fn fn-type="COI-statement">'
            "<p>Dr Smith is an employee of Genentech.</p></fn></fn-group></back></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.industry_funding is True
        assert _INDICATOR_INDUSTRY_COI in analysis.indicators


def _top_level_alternatives(pattern: str) -> list[str]:
    """Split a regex source on the ``|`` that separate its top-level branches.

    Deliberately a splitter and not a parse: it tracks escapes, character
    classes and group nesting, which is all that is needed to say where each
    branch of this one pattern begins.
    """
    branches: list[str] = []
    current: list[str] = []
    depth = 0
    in_class = False
    escaped = False
    for char in pattern:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if in_class:
            current.append(char)
            if char == "]":
                in_class = False
            continue
        if char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            branches.append("".join(current))
            current = []
            continue
        current.append(char)
    branches.append("".join(current))
    return branches


class TestMarkupTheContractDoesNotDescribe:
    """Issue #160 — what a body that is not well-formed costs this scan.

    ``_strip_nested_articles`` documents its input as *"a ``fullTextXML`` body
    as Europe PMC served it"*, assumed well-formed, and the assumption is
    sound as a description of the corpus, and measured over more of it than
    the issue sampled: **0 of 98,789 articles** carries either shape — every
    article of the ``oa_comm`` ``PMC012xxxxxx`` baseline package (97,909,
    archive rendition) and of an 880-article Europe PMC draw (served
    rendition), against the issue's 3,880. What it did not have
    is any behaviour for the case it excludes, and a contract nothing enforces
    is a contract the transport can break: an HTTP 200 carrying a truncated
    body is not a shape a publisher deposits, it is a shape a network
    produces.

    Two consequences, both fixed here, and neither reachable from a deposit.
    An end tag closed a region opened by the *other* element, which re-admits
    the rest of the outer round as the article's prose — the exact defect
    #119 removed, from inside the fix for it. And every skip branch scanned to
    end-of-string when its terminator was absent while ``finditer`` retried at
    every later opener, so the lex was quadratic: 256 kB of a repeated
    ``<!DOCTYPE a[`` took **33.6s** and 224 kB of an unterminated tag 33.3s,
    each doubling costing about four times the last.
    ``_HTTP_TIMEOUT_SECONDS`` bounds the request, not the post-processing, so
    that body did not fail — it stalled, reaching neither the refusal nor the
    warning.

    Both fixes are in the fail-closed direction the module already takes, and
    **neither needs a constant drawn from a corpus** — the issue's other two
    remedies, a size cap and a work multiple, each wanted a threshold nothing
    had measured, and real articles reach 3.4 MB.
    """

    # ---- an end tag closes the element that opened the region ----

    @pytest.mark.parametrize(
        ("outer", "stray"), [("sub-article", "response"), ("response", "sub-article")]
    )
    def test_an_end_tag_closes_only_the_element_that_named_it(self, outer, stray):
        # The depth used to be a bare count, so `</response>` closed a region
        # a <sub-article> had opened and the reviewer prose after it came back
        # as this article's. A stack of names costs one list and fixes it: the
        # mismatched end tag is ignored, and the region ends where it says it
        # ends.
        #
        # **Both directions, because the rule is per element and the fixture
        # was not.** Written with <sub-article> outside only, a mutant that
        # let a <response>-opened region close on any end tag passed all 204
        # tests and re-admitted the reviewer prose verbatim — the module's own
        # "mutation testing needs both edges", one element over. Neither
        # element is privileged in `open_elements`, so neither may be in the
        # fixture.
        stripped = _strip_nested_articles(
            "<article><p>Ours.</p>"
            f"<{outer}><p>Reviewer prose.</p></{stray}>"
            f"<p>MORE REVIEWER PROSE.</p></{outer}>"
            "<p>Ours again.</p></article>"
        )
        assert stripped == "<article><p>Ours.</p><p>Ours again.</p></article>"

    def test_regions_closed_in_the_wrong_order_refuse_the_document(self):
        # Improperly nested the other way: the inner region is still open when
        # the outer one closes. Ignoring the mismatch leaves a region open at
        # the end, which is the refusal the module already makes — no tail is
        # kept, and the analysis falls back to the abstract.
        assert (
            _strip_nested_articles(
                "<article><p>Ours.</p><sub-article><response><p>Theirs.</p>"
                "</sub-article></response><p>Ours again.</p></article>"
            )
            is None
        )

    def test_an_unmatched_end_tag_between_two_regions_still_costs_nothing(self):
        # The depth-0 case the docstring already scopes, kept as the negative
        # control for the rule above: a stray end tag outside every region
        # admits no nested prose, so refusing the article would lose a signal
        # it really carries. It must also not move the resume point — reading
        # it as a close would splice the prose after it onto the region
        # before it.
        stripped = _strip_nested_articles(
            "<article><p>One.</p>"
            "<sub-article><p>Round one.</p></sub-article>"
            "</response><p>Two.</p></article>"
        )
        assert stripped == "<article><p>One.</p></response><p>Two.</p></article>"

    # ---- a construct that never terminates ----

    @pytest.mark.parametrize(
        ("kind", "xml"),
        [
            ("comment", "<article><p>Ours.</p><!-- <sub-article> and then the body ends"),
            ("CDATA section", "<article><p>Ours.</p><![CDATA[ <sub-article> then the body ends"),
            ("processing instruction", "<article><p>Ours.</p><?publisher <sub-article> ends"),
            ("doctype", "<!DOCTYPE article [<!ENTITY r '<sub-article>'> <article><p>Ours."),
            ("tag", "<article><p>Ours.</p><sub-article xml:lang='en' specific-use"),
        ],
    )
    def test_an_unterminated_construct_refuses_the_document(self, kind, xml):
        # Each of the five is a construct the lexer must skip whole, and each
        # one that never terminates says the body is not what the contract
        # describes. Refusing is what bounds the work: the alternative is the
        # scan continuing over a string whose markup it can no longer locate,
        # quadratically, and then reading the unterminated construct's own
        # content as this article's markup.
        with pytest.raises(_UnterminatedMarkupError) as excinfo:
            _strip_nested_articles(xml)
        assert kind in str(excinfo.value)

    def test_the_refusal_names_the_first_opener_rather_than_a_later_one(self):
        # Deterministic half of the bound below: bailing at the *first*
        # unterminated opener is what makes one failed scan the whole cost. A
        # loop that noted the refusal and carried on would still raise, and so
        # would still satisfy the five cases above, at O(n^2) — it is the
        # *offset* that says it stopped at the first opener rather than
        # walking the whole string.
        xml = "<article>" + "<!--x" * 2_000
        with pytest.raises(_UnterminatedMarkupError) as excinfo:
            _strip_nested_articles(xml)
        assert "offset 9" in str(excinfo.value)

    def test_an_unterminated_construct_does_not_lex_quadratically(self):
        # The only end-to-end proof of the bound, so it is a wall-clock
        # assertion with a margin rather than a ratio: this shape took 33.6s
        # at 256 kB before the refusal and takes ~1 ms after, so the ceiling is
        # ~1,400x the measured time and ~0.06x the defect's. Doubling
        # the input doubles the ceiling's slack rather than eating it, which
        # is the property under test.
        xml = "<!DOCTYPE a[" * 21_333  # ~256 kB, the issue's largest shape
        start = time.perf_counter()
        with pytest.raises(_UnterminatedMarkupError):
            _strip_nested_articles(xml)
        assert time.perf_counter() - start < 2.0

    # ---- the refusal fires on no well-formed document ----

    def test_a_well_formed_construct_never_reaches_the_refusal_branch(self):
        # The negative control the parametrisation above needs: every one of
        # the five terminates here, so the branch that refuses is last in the
        # alternation and unreachable on the input the contract describes.
        # This states that property directly. It is not the only thing that
        # would catch a fallback matched too early — moving the branch to the
        # front of the alternation reddens 31 tests, 27 of them in
        # `TestANestedArticleIsNotThisArticles`, which predates this class —
        # so keep it for saying so outright, not for being the sole guard.
        for token in (
            "<!-- c -->",
            "<![CDATA[c]]>",
            "<?pi c?>",
            '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS 1.4//EN" "JATS.dtd">',
            '<!DOCTYPE article [<!ENTITY r "<sub-article>">]>',
            "<sub-article>",
            "</sub-article>",
            '<sub-article specific-use="a>b"/>',
        ):
            match = _NESTED_ARTICLE_TOKEN_RE.match(token)
            assert match is not None, token
            assert match.group("unterminated") is None, token
            assert match.end() == len(token), token

    def test_the_refusal_group_names_every_opener_it_can_capture(self):
        # `.get(opener, "tag")` asserts that anything the refusal branch can
        # capture and that is not in the name table is one of the two element
        # tags. That was true by hand-enumeration and by nothing else: an
        # alternative added to the group alone comes out labelled "tag", with
        # the branch count still 6 and the test below still green.
        #
        # So this reads the alternatives out of the *pattern* rather than
        # probing a list written here — a test that enumerates its own inputs
        # can only confirm them, and the first cut of this one did exactly
        # that and let the mutant through. The group's non-tag half is derived
        # from the table in `analyzer.py`; this is what stops it being
        # un-derived.
        branch = _top_level_alternatives(_NESTED_ARTICLE_TOKEN_RE.pattern)[-1]
        inner = branch[branch.index("<unterminated>") + len("<unterminated>") : -1]
        alternatives = _top_level_alternatives(inner)
        named = {opener[1:] for opener in _UNTERMINATED_OPENER_NAMES}
        element_form = r"/?(?:" + _NESTED_ARTICLE_ALTERNATION + r")(?![-.:\w])"
        for alternative in alternatives:
            # `re.escape` may spell a literal differently from the table, so
            # compare what each one matches, not how it is written.
            assert alternative == element_form or any(
                re.fullmatch(alternative, candidate) for candidate in named
            ), alternative
        # And every named opener is actually reachable through the branch.
        for opener in _UNTERMINATED_OPENER_NAMES:
            match = _NESTED_ARTICLE_TOKEN_RE.match(opener + " ")
            assert match is not None, opener
            assert "<" + match.group("unterminated") == opener, opener

    def test_every_branch_of_the_lexer_opens_with_the_literal(self):
        # `sre` derives a prefix for the whole pattern only when every
        # top-level branch begins with the same literal, and then skips from
        # "<" to "<" rather than trying the pattern at every position. A
        # branch that opens with a group defeats that analysis silently.
        # Three configurations, and the labels are the point: over 7.8 MB of
        # real articles, 13.4 ms with no refusal branch, 26.6 ms with it and
        # the literal outside the group, 191 ms with it inside. The two forms
        # that differ by two characters are therefore **26.6 and 191 — a 7.2x
        # placement tax**, not the 14x an earlier draft claimed, which was
        # 191 against the no-guard baseline and so counted the guard's own
        # 1.9x cost a second time. Factoring the alternatives inside the group
        # recovers ~8% of the penalty and not the penalty. Nothing but this
        # test stands between the two forms, with the whole suite green.
        branches = _top_level_alternatives(_NESTED_ARTICLE_TOKEN_RE.pattern)
        assert len(branches) == 6, branches
        for branch in branches:
            assert branch.startswith("<"), branch


class TestTheRestatedSetMatchesTheParsers:
    """`_NESTED_ARTICLE_ELEMENTS` is stated twice, so something must compare them.

    `bmlib.transparency` deliberately depends on nothing in `bmlib.fulltext`,
    so the set and its completeness argument are restated rather than imported
    (``docs/DECISIONS.md``). That leaves a rule enforced by prose — "if the
    rule changes, change both" — and this repo's own precedent is that a rule
    enforced by prose is not enforced. A *test* may import both where the
    module may not, and the drift that matters is undetectable otherwise:
    adding an element to the parser's set only, which leaves the transparency
    scan reading a region the parser knows is not this article's.
    """

    def test_the_two_sets_hold_the_same_elements(self):
        from bmlib.fulltext import jats_parser

        assert set(_NESTED_ARTICLE_ELEMENTS) == set(jats_parser._NESTED_ARTICLE_ELEMENTS)

    def test_the_transparency_copy_is_ordered(self):
        # It is joined into a regex alternation, so it needs a deterministic
        # order; the parser's is a frozenset, whose iteration order is not.
        # Copying the parser's container across would make the compiled
        # pattern differ between processes.
        assert isinstance(_NESTED_ARTICLE_ELEMENTS, tuple)


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
        epmc = _epmc_record(
            "Funded by the National Institutes of Health; ClinicalTrials.gov number, NCT01206062."
        )
        assert _find_trial_ids(epmc) == ["NCT01206062"]

    def test_registered_rct_label_form_credited(self):
        # "NCT number: NCT..." / "(NCT) Identified Number: NCT..." label forms.
        epmc = _epmc_record(
            "Trial registration National Clinical Trial (NCT) Identified Number: NCT04088331."
        )
        assert _find_trial_ids(epmc) == ["NCT04088331"]

    def test_two_linked_own_trials_credited(self):
        # A paper reporting its own two linked registrations (e.g. ROMANA 1/2).
        epmc = _epmc_record(
            "Trial registration NCT identifiers: ROMANA 1: NCT01387269; ROMANA 2: NCT01387282."
        )
        result = _find_trial_ids(epmc)
        assert result == ["NCT01387269", "NCT01387282"]

    def test_review_listing_many_trials_not_credited(self):
        # A pooled analysis / review enumerating its constituent trials.
        epmc = _epmc_record(
            "Trial registry name and numbers: ASCEND (NCT01416181), "
            "ADVANCE (NCT00906399), DECIDE (NCT01064401)."
        )
        assert _find_trial_ids(epmc) == []

    def test_review_prose_listing_included_trials_not_credited(self):
        epmc = _epmc_record(
            "We included five randomized controlled trials (NCT01111111, "
            "NCT02222222, NCT03333333, NCT04444444, NCT05555555) in the analysis."
        )
        assert _find_trial_ids(epmc) == []

    def test_bare_nct_without_registration_language_not_credited(self):
        # A single NCT mentioned with no registration cue is ambiguous; the
        # conservative choice is not to credit it as the paper's registration.
        epmc = _epmc_record("Outcomes were compared across 20 high-volume centers (NCT03461341).")
        assert _find_trial_ids(epmc) == []

    def test_registration_cue_after_id_credited(self):
        # The cue may follow the id: "NCT…; registered at ClinicalTrials.gov".
        epmc = _epmc_record(
            "This study (NCT01234567, registered at ClinicalTrials.gov) enrolled 400 patients."
        )
        assert _find_trial_ids(epmc) == ["NCT01234567"]

    def test_lowercase_nct_id_credited_and_normalized(self):
        # NCT ids are conventionally upper-case but must match regardless of
        # case, and be returned in the canonical upper-case form.
        epmc = _epmc_record("Trial registration: nct01206062.")
        assert _find_trial_ids(epmc) == ["NCT01206062"]

    def test_no_nct_returns_empty(self):
        epmc = _epmc_record("No trials here.")
        assert _find_trial_ids(epmc) == []


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
        analyzer._check_trial_registration(_Client(), analysis, epmc=epmc)
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
        analyzer._check_trial_registration(_Client(), analysis, epmc=epmc)
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
        analyzer._check_trial_registration(_Client(), analysis, epmc=epmc)
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

    def _europepmc_level(self, abstract: str) -> _Analysis:
        """Run the Europe PMC step over *abstract* and return the carrier."""
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        analyzer._check_europepmc(
            _FakeFullTextClient(None), _epmc_record(abstract, in_epmc="N"), analysis
        )
        return analysis

    def test_not_available_upon_request_is_not_available(self):
        analysis = self._europepmc_level("The data are not available upon reasonable request.")
        assert analysis.data_level == "not_available"
        _score_data_availability(analysis)
        assert analysis.score == 0  # no on_request credit awarded
        # Membership, not equality: this abstract carries no COI cue phrase
        # and no full text, so `_check_europepmc` also writes
        # `_INDICATOR_COI_UNKNOWN` — a real but unrelated finding this test
        # is not about. Asserting the full list would couple a
        # data-availability test to COI-detection behaviour.
        assert _INDICATOR_DATA_NOT_AVAILABLE in analysis.indicators

    def test_available_upon_request_still_credited(self):
        analysis = self._europepmc_level(
            "Data are available from the authors upon reasonable request."
        )
        assert analysis.data_level == "on_request"
        # The step nominates; analyze() scores the winner exactly once.
        assert analysis.score == 0
        _score_data_availability(analysis)
        assert analysis.score == SCORE_DATA_ON_REQUEST

    def test_mixed_statement_negation_takes_precedence(self):
        # Deliberate: when an abstract carries both a sharing cue and a
        # negation ("code on GitHub" + "data not available"), the conservative
        # negation-first ordering of _DATA_PATTERNS wins.
        analysis = self._europepmc_level(
            "Analysis code is available on GitHub; individual patient data are not available."
        )
        assert analysis.data_level == "not_available"
        _score_data_availability(analysis)
        assert analysis.score == 0

    def test_a_step_that_found_nothing_does_not_lower_an_established_level(self):
        # This replaces `test_a_level_this_step_did_not_find_is_not_scored`,
        # which pinned the pre-merge rule that this step assigns `data_level`
        # outright. With a second producer that rule inverts: finding nothing
        # is not evidence against what another source found, so nominating
        # "unknown" must be a no-op rather than a demotion. The half that
        # still holds — this step never scores a level it did not find — now
        # holds because the step scores nothing at all.
        analysis = _Analysis(data_level="full_open")
        analyzer = TransparencyAnalyzer()
        analyzer._check_europepmc(
            _FakeFullTextClient(None),
            _epmc_record("This abstract says nothing about data.", in_epmc="N"),
            analysis,
        )
        assert analysis.data_level == "full_open"
        assert analysis.score == 0

    def test_the_component_is_awarded_once_however_many_sources_nominated(self):
        # The hazard deferring the award exists to remove.
        analysis = _Analysis()
        analysis.note_data_level("full_open")
        analysis.note_data_level("full_open")
        _score_data_availability(analysis)
        assert analysis.score == SCORE_DATA_FULL_OPEN

    def test_a_level_nobody_established_scores_nothing_and_says_nothing(self):
        # The branch every other test reaches only by implication: "unknown"
        # falls off the end of the chain, so it must award no points *and*
        # write no indicator. Silence is not a finding — an indicator here
        # would report an absence of evidence as evidence.
        analysis = _Analysis()
        _score_data_availability(analysis)
        assert analysis.data_level == "unknown"
        assert analysis.score == 0
        assert analysis.indicators == []

    def test_every_pattern_maps_to_a_level_the_ranking_knows(self):
        # `_check_europepmc` feeds these values straight to
        # `note_data_level()`, which raises on anything outside
        # `_DATA_LEVEL_RANK`. The trap is baited: "restricted" and
        # "not_stated" are levels `calculate_risk_level()` genuinely accepts,
        # so adding a pattern for one reads as reasonable — and would then
        # throw a KeyError out of `analyze()` for every paper whose text
        # matched it. Nothing but this test stands between the two maps.
        assert set(_DATA_PATTERNS.values()) <= set(_DATA_LEVEL_RANK)


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
            # Named here because the test claims *every* field; both sides
            # otherwise default to `None` and the field rides along uncovered.
            full_text_status=FullTextStatus.ANALYZED,
        )
        assert TransparencyResult.from_dict(original.to_dict()) == original
        assert set(original.to_dict()) == {f.name for f in dataclasses.fields(TransparencyResult)}


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
    databanks: tuple[tuple[str, tuple[str, ...] | None], ...] = (),
    agencies: tuple[str, ...] = (),
) -> str:
    """Build a minimal PubmedArticleSet response.

    *databanks* is a tuple of ``(DataBankName, accession numbers)`` pairs.
    Accessions of ``None`` omit ``<AccessionNumberList>`` altogether; an empty
    tuple emits it empty. PubMed produces both.
    """
    databank_xml = "".join(
        f"<DataBank><DataBankName>{name}</DataBankName>"
        + (
            ""
            if accessions is None
            else "<AccessionNumberList>"
            + "".join(f"<AccessionNumber>{a}</AccessionNumber>" for a in accessions)
            + "</AccessionNumberList>"
        )
        + "</DataBank>"
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
    """Fake httpx client that dispatches on URL and records every request.

    **It serves full text at one address only**, for the reason
    :class:`_FakeFullTextClient` does — and this is the fake that matters
    most, being the only one reached through :meth:`analyze`, the end-to-end
    path issue #184 actually broke. It matched ``"fullTextXML" in url``, a
    *substring* test looser still than the ``endswith`` #184 removed from the
    other fake, so the whole-URL net stopped one level short of the path a
    caller exercises.

    Routing stays on the endpoint and only *serving* is address-checked: a
    request for the wrong full-text URL has to 404 the way the live API does,
    not fall through to the ``"europepmc" in url`` branch below and be
    answered with a search payload.
    """

    def __init__(
        self,
        *,
        crossref: dict | None = None,
        epmc: dict | None = None,
        full_text: str | None = None,
        pubmed: str | None = None,
        trial_has_results: bool = False,
        ext_id: str = "PMC123",
        full_text_status_code: int = 200,
        trial_status_code: int = 200,
    ):
        self.crossref = crossref
        self.epmc = epmc
        self.full_text = full_text
        self.pubmed = pubmed
        self.trial_has_results = trial_has_results
        #: What ClinicalTrials.gov answers with. Defaults to 200 so every
        #: existing fixture is unchanged; PR #195's review needs a non-200 to
        #: reach `_check_trial_registration`, which is where issue #194's
        #: false claim was actually stored.
        self.trial_status_code = trial_status_code
        #: What the full-text address answers with. Defaults to 200 so every
        #: existing fixture is unchanged; issue #191 needs a 503 to reach
        #: `analyze()`, and `_StatusClient` cannot — it is not a context
        #: manager, so it cannot stand in for the client `analyze()` builds.
        self.full_text_status_code = full_text_status_code
        #: The one address full text is served at. ``PMC123`` is the accession
        #: `_epmc_payload` and `_epmc_record` deposit, and the three literals
        #: have to agree — loudly, since a drift reddens every test that
        #: fetches rather than silently serving nothing.
        self.full_text_url = f"{EUROPEPMC_REST_BASE}/{ext_id}/fullTextXML"
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
        if url.endswith("/fullTextXML"):
            if self.full_text is None or url != self.full_text_url:
                return _FakeResponse(status_code=404)
            if self.full_text_status_code != 200:
                return _FakeResponse(status_code=self.full_text_status_code)
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
            if self.trial_status_code != 200:
                return _FakeResponse(status_code=self.trial_status_code)
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


def _epmc_payload(
    *,
    abstract: str = "",
    pmid: str | None = None,
    in_epmc: str = "N",
    addressable: bool = False,
) -> dict:
    """Build a EuropePMC search envelope for an ``analyze()``-level test.

    ``addressable`` adds the ``source``/``pmcid`` pair without which
    :meth:`TransparencyAnalyzer._fetch_europepmc_fulltext` returns
    ``NOT_ATTEMPTED`` before issuing a request. It defaults ``False`` because
    most callers here are testing something else — but a test about full text
    that omits it is testing nothing, which is how
    ``test_a_pubmed_statement_retracts_the_full_text_absence_indicator`` came
    to assert the absence of an indicator that was never added.
    """
    record: dict = {"abstractText": abstract, "inEPMC": in_epmc}
    if pmid is not None:
        record["pmid"] = pmid
    if addressable:
        record["source"] = "PMC"
        record["pmcid"] = "PMC123"
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
        # GENBANK/PDB accessions are a data-availability signal, deliberately
        # out of scope here — they must not be mistaken for trial registration.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)))
        assert signals.trial_accessions == ()
        assert signals.registration_not_checkable is False

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

    @pytest.mark.parametrize("name", ["JMACCT", "REPEC", "UMIN CTR"])
    def test_registries_nlm_publishes_are_all_recognised(self, name):
        # All three appear in NLM's DataBankName vocabulary and none was in
        # bmlib's set: JMACCT and REPEC were missing outright, and UMIN's
        # registry was spelled "umin-ctr" where NLM's table says "UMIN CTR",
        # so the exact-match test failed on the string PubMed emits. Each
        # silently cost the paper SCORE_TRIAL_REGISTERED.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=((name, ("X1",)),)))
        assert signals.registration_not_checkable is True

    def test_a_deposition_accession_is_collected(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)))
        assert signals.deposition_databanks == ("GENBANK",)

    def test_pubmeds_own_spelling_is_kept(self):
        # The name is rendered to humans in the indicator line.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GenBank", ("MN908947",)),)))
        assert signals.deposition_databanks == ("GenBank",)

    def test_repository_matching_ignores_case(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("figshare", ("10.6084/m9",)),)))
        assert signals.deposition_databanks == ("figshare",)

    def test_one_repository_named_twice_is_one_entry(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("GENBANK", ("A1",)), ("GenBank", ("A2",))))
        )
        assert signals.deposition_databanks == ("GENBANK",)

    def test_repositories_are_kept_in_document_order(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(databanks=(("PDB", ("1ABC",)), ("SRA", ("SRP000001",))))
        )
        assert signals.deposition_databanks == ("PDB", "SRA")

    @pytest.mark.parametrize("accessions", [None, (), ("",), ("   ",)])
    def test_a_repository_without_a_usable_accession_proves_nothing(self, accessions):
        # A repository name with no accession is an assertion with no referent
        # — nothing a reader could go and fetch — so it is not the structured
        # proof of a deposit this signal claims to be.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("GENBANK", accessions),)))
        assert signals.deposition_databanks == ()

    @pytest.mark.parametrize(
        "name", ["OMIM", "RefSeq", "UniProtKB", "PubChem-Compound", "GDB", "dbSNP"]
    )
    def test_a_curated_reference_database_is_not_a_deposit(self, name):
        # NLM lists these beside the deposition repositories, but an OMIM
        # number says the paper is about a known condition and a RefSeq
        # accession names a sequence NCBI curated — neither is evidence that
        # these authors shared their data. dbSNP is the sharpest case: it sits
        # right beside dbVar in the deposit set, but a dbSNP citation is
        # overwhelmingly an rs-number reference, not a submission.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=((name, ("X1",)),)))
        assert signals.deposition_databanks == ()

    def test_a_controlled_access_repository_is_collected_too(self):
        # dbGaP is genuine deposition; the merge step is what knows it is
        # controlled-access and worth `on_request` rather than `full_open`.
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("dbGaP", ("phs000001",)),)))
        assert signals.deposition_databanks == ("dbGaP",)

    def test_a_registry_and_a_repository_in_one_list_feed_both_branches(self):
        signals = _parse_pubmed_signals(
            _pubmed_xml(
                databanks=(
                    ("ClinicalTrials.gov", ("NCT01234567",)),
                    ("GENBANK", ("MN908947",)),
                )
            )
        )
        assert signals.trial_accessions == ("NCT01234567",)
        assert signals.deposition_databanks == ("GENBANK",)

    def test_an_unrecognised_databank_name_is_ignored(self):
        signals = _parse_pubmed_signals(_pubmed_xml(databanks=(("SomeNewRegistry", ("X1",)),)))
        assert signals.deposition_databanks == ()
        assert signals.registration_not_checkable is False

    def test_the_deposition_and_registry_name_sets_are_disjoint(self):
        # `_parse_pubmed_signals` checks deposition membership first and
        # `continue`s, so a name in both families would always be read as a
        # deposit and never reach the registry branch — silently dropping
        # `trial_registered`, `SCORE_TRIAL_REGISTERED` (20) and the registry
        # indicator while a deposit scores 20 instead. The total would look
        # plausible and nothing would raise. Nothing enforces the two
        # vocabularies stay disjoint except this test; if it ever fails, the
        # fix is to remove the name from whichever of the two it does not
        # belong in, not to reorder the branches in the parser.
        assert not set(_DEPOSITION_DATABANK_LEVELS) & _TRIAL_REGISTRY_NAMES

    def test_every_repository_maps_to_a_level_the_ranking_knows(self):
        # `_merge_pubmed_signals` subscripts `_DEPOSITION_DATABANK_LEVELS` and
        # feeds the result straight to `note_data_level()`, which raises on a
        # level outside `_DATA_LEVEL_RANK`. A typo in a value here would
        # therefore surface as a KeyError escaping `analyze()` for exactly
        # those papers that deposited data — the ones this feature exists to
        # credit — rather than at import time.
        assert set(_DEPOSITION_DATABANK_LEVELS.values()) <= set(_DATA_LEVEL_RANK)

    def test_no_repository_nominates_a_level_weaker_than_on_request(self):
        # A deposit is positive evidence. Mapping one to "unknown" or
        # "not_available" would be a contradiction the type system cannot
        # catch: both are keys of `_DATA_LEVEL_RANK`, so the test above would
        # still pass and the paper would silently score nothing — or, at
        # "not_available", earn a "Data explicitly not available" indicator
        # off the back of an accession proving the opposite.
        assert all(
            _DATA_LEVEL_RANK[level] >= _DATA_LEVEL_RANK["on_request"]
            for level in _DEPOSITION_DATABANK_LEVELS.values()
        )


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
            # `addressable=True` is load-bearing: without it no request is made
            # and `_INDICATOR_NO_COI_IN_FULLTEXT` is never appended, so the
            # assertion below passes on an empty list and pins nothing.
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True),
            full_text="<article><body><p>Methods and results.</p></body></article>",
            pubmed=_pubmed_xml(coi="The authors declare none."),
        )
        result = self._analyze(monkeypatch, client, pmid="1")
        assert result.full_text_analyzed is True
        assert result.coi_disclosed is True
        assert _INDICATOR_NO_COI_IN_FULLTEXT not in result.risk_indicators
        assert _INDICATOR_COI_IN_PUBMED in result.risk_indicators

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


class TestDataDepositionMerge:
    """PubMed's deposition accessions are the second producer of `data_level`."""

    def test_a_deposition_accession_establishes_full_open(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("GENBANK",)), analysis)
        assert analysis.data_level == "full_open"

    def test_a_controlled_access_deposit_is_only_on_request(self):
        # dbGaP data needs Data Access Committee approval, which is what
        # `on_request` already means. The design's testing plan promised
        # "dbGaP alone scores 10" — score it, not just the level, so this
        # class is self-contained.
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP",)), analysis)
        _score_data_availability(analysis)
        assert analysis.data_level == "on_request"
        assert analysis.score == SCORE_DATA_ON_REQUEST

    def test_the_strongest_of_several_deposits_wins(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP", "GENBANK")), analysis)
        assert analysis.data_level == "full_open"

    def test_an_accession_outranks_a_full_text_denial(self):
        # The consequential case. A clinical paper's "data are not available"
        # is routinely about individual patient records, while the accession
        # is a sequence on a public server right now. Hard evidence of a real
        # deposit beats a substring match whose subject we cannot determine —
        # and the denial indicator is never written, so nothing contradicts.
        analysis = _Analysis()
        analysis.note_data_level("not_available")
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("GENBANK",)), analysis)
        _score_data_availability(analysis)
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN
        assert _INDICATOR_DATA_NOT_AVAILABLE not in analysis.indicators

    def test_a_deposit_never_lowers_a_stronger_established_level(self):
        analysis = _Analysis()
        analysis.note_data_level("full_open")
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP",)), analysis)
        _score_data_availability(analysis)
        assert analysis.data_level == "full_open"
        assert analysis.score == SCORE_DATA_FULL_OPEN  # 20, not 20 + 10

    def test_the_repositories_are_named_in_an_indicator(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("GENBANK", "PDB")), analysis)
        assert _INDICATOR_DATA_DEPOSITED_PREFIX + "GENBANK, PDB" in analysis.indicators

    def test_the_indicator_is_written_even_when_the_level_it_nominated_lost(self):
        # The line reports what PubMed said, which stays true regardless of
        # which level won. A sub-step publishes its own finding; it does not
        # read the merged field back to decide whether to mention it.
        analysis = _Analysis()
        analysis.note_data_level("full_open")
        _merge_pubmed_signals(_PubMedSignals(deposition_databanks=("dbGaP",)), analysis)
        assert _INDICATOR_DATA_DEPOSITED_PREFIX + "dbGaP" in analysis.indicators

    def test_no_deposits_means_no_indicator_and_no_level(self):
        analysis = _Analysis()
        _merge_pubmed_signals(_PubMedSignals(), analysis)
        assert analysis.data_level == "unknown"
        assert analysis.indicators == []

    def test_analyze_credits_a_deposition_accession_end_to_end(self, monkeypatch):
        client = _RecordingClient(
            epmc=_epmc_payload(abstract="A study of a virus.", pmid="12345678"),
            pubmed=_pubmed_xml(databanks=(("GENBANK", ("MN908947",)),)),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="12345678")
        assert result.data_availability_level == "full_open"
        assert result.transparency_score == SCORE_DATA_FULL_OPEN
        assert _INDICATOR_DATA_DEPOSITED_PREFIX + "GENBANK" in result.risk_indicators

    def test_data_not_available_indicator_is_written_last(self, monkeypatch):
        # `_score_data_availability()` now runs once, in `analyze()`, after
        # every sub-step — including trial registration — rather than inline
        # inside `_check_europepmc` as it did before the once-at-the-end
        # refactor. `_INDICATOR_DATA_NOT_AVAILABLE` is therefore always the
        # last line appended, not wherever the EuropePMC step happened to sit
        # in the pipeline. The fixture needs a later indicator to make that
        # observable: a paper whose abstract denies data availability *and*
        # whose PubMed record registers a trial PubMed cannot follow up
        # (`_INDICATOR_RESULTS_NOT_CHECKABLE`, from the trial-registration
        # step that runs after the data-availability merge). Under the old,
        # inline-scoring code this fixture produces the data indicator
        # *before* the trial one, so this assertion would have failed there —
        # confirmed by running it against the pre-refactor analyzer
        # (commit 11f47ff), where `risk_indicators` ends with
        # "Trial registration found; posted-results status could not be
        # checked", not the data indicator.
        client = _RecordingClient(
            epmc=_epmc_payload(
                abstract="Data are not available due to patient privacy.", pmid="12345678"
            ),
            pubmed=_pubmed_xml(databanks=(("ISRCTN", ("ISRCTN12345678",)),)),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="12345678")
        assert result.data_availability_level == "not_available"
        # Proves the fixture actually discriminates: without a later
        # indicator, the assertion below would pass under any ordering.
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in result.risk_indicators
        assert result.risk_indicators[-1] == _INDICATOR_DATA_NOT_AVAILABLE


class TestABodyTruncatedBetweenTagsIsRefused:
    """Issue #183 — the half of issue #160 that fix does not reach.

    Issue #160 bounds the lexer and refuses a construct that never
    *terminates*. A body truncated **between tags** opens no such construct,
    so it used to be accepted and scanned as a complete article: no refusal,
    no ``None``, and no log line at any level. Downstream that gave
    ``full_text_analyzed=True`` and ``coi_disclosed=False`` with the indicator
    *"No COI disclosure found in full text"* — for a disclosure that was in
    the lost tail — which is the missing-COI HIGH downgrade fired on evidence
    that does not exist. Loud and losing the full text (issue #160's half) is
    strictly better than silent and scoring it wrong.
    """

    def test_a_body_truncated_between_tags_is_refused(self, caplog):
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><sec><title>Methods</title><p>Some prose.</p></sec>"
        )
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is False
        # The whole point: `None`, never `False`. Only `False` triggers the
        # missing-COI downgrade, and there is no evidence for it here.
        assert analysis.coi_disclosed is None
        assert analysis.full_text_status is FullTextStatus.TRUNCATED
        matching = [r for r in caplog.records if "did not arrive whole" in r.getMessage()]
        assert len(matching) == 1
        # WARNING, not ERROR: ERROR is what this module reserves for "bmlib is
        # wrong", and a truncated body is a network product.
        assert matching[0].levelno == logging.WARNING

    def test_a_trailing_comment_after_the_root_is_not_a_truncation(self):
        # The negative control, and the measured one. Issue #183 proposed
        # `xml.rstrip().endswith("</article>")`, which refuses this shape —
        # and it is not hypothetical: **1,727 of the 97,909 archive articles
        # (1.76%) and 23 of the 8,118 served ones (0.28%) end this way**, all
        # of the form `</article><!--requester-ID gmcconne-->`. Trailing
        # comments, PIs and whitespace after the root are legal XML. Testing
        # for the *presence* of the end tag rather than for its position
        # costs nothing and refuses none of them.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><back><fn-group>"
            '<fn fn-type="COI-statement"><p>The authors declare no conflict of interest.</p></fn>'
            "</fn-group></back></article><!--requester-ID gmcconne-->"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.full_text_status is FullTextStatus.ANALYZED
        assert analysis.coi_disclosed is True

    def test_trailing_whitespace_and_a_processing_instruction_are_not_a_truncation(self):
        # The other two legal trailing constructs, for the same reason.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><back><fn-group>"
            '<fn fn-type="COI-statement"><p>Nothing to declare.</p></fn>'
            "</fn-group></back></article>\n<?oxygen-final?>\n  "
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_analyzed is True
        assert analysis.full_text_status is FullTextStatus.ANALYZED

    def test_the_truncation_check_is_the_net_for_what_the_others_miss(self, caplog):
        # Ordering, pinned rather than left to the reading order of the
        # function. A truncated body can satisfy several refusals at once —
        # truncation is the *cause* and the rest are symptoms — and each of
        # the three specific checks knows something this one does not: which
        # construct and at what offset, which element was left open, that
        # nothing but nested articles arrived. So the truncation check runs
        # last and reports only what nothing more specific claimed.
        analyzer = TransparencyAnalyzer()
        # Truncated *and* leaving a region open. Both are true; the specific
        # one is the one stored.
        client = _FakeFullTextClient("<article><body><p>Ours.</p><sub-article><p>Theirs.</p>")
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_status is FullTextStatus.UNCLOSED_REGION
        assert not [r for r in caplog.records if "did not arrive whole" in r.getMessage()]

    def test_an_unterminated_construct_still_reports_itself(self, caplog):
        # The same ordering rule one check further up, and the one that would
        # cost most if it went the other way: a body truncated mid-comment has
        # no `</article>` either, so a truncation check placed ahead of the
        # lex would make issue #160's message — which names the construct and
        # the offset — unreachable for the only input that produces it.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<article><body><p>Methods.</p><!-- truncated here")
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_status is FullTextStatus.UNTERMINATED_MARKUP
        assert not [r for r in caplog.records if "did not arrive whole" in r.getMessage()]

    def test_a_body_that_is_entirely_nested_still_reports_itself(self, caplog):
        # And the third. A body of nothing but nested articles carries no
        # `</article>` of its own, so without this ordering the entirely-nested
        # report — added deliberately as the one outcome that would otherwise
        # reach storage with no signal at all — would become unreachable.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient("<sub-article><p>Round one.</p></sub-article>")
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_status is FullTextStatus.ENTIRELY_NESTED
        assert not [r for r in caplog.records if "did not arrive whole" in r.getMessage()]


class TestARefusedFullTextLeavesATrace:
    """Issue #161 — a served-but-refused full text is not "unavailable".

    ``_fetch_europepmc_fulltext`` has several outcomes and all the failing
    ones used to be indistinguishable in anything a caller stores:
    ``full_text_analyzed=False`` and the indicator *"COI disclosure status
    unknown (full text unavailable)"*, which is **false** for a refusal —
    Europe PMC served HTTP 200 with a document. Results are cacheable and
    driven concurrently, so a refusal is stored, permanent and unmarked, and
    the score silently loses up to 30 points on that path — enough to reach
    HIGH against the default ``score_threshold`` and set
    ``tier_downgrade_applied``. The same argument as ``FetchResult.note`` ->
    ``SyncReport.notes`` in ``publications/``: permanent *and* invisible is
    the pair these rules exist to break up.
    """

    def test_a_404_is_an_outcome_that_is_really_unavailable(self):
        # Not *the only* one, which is what this was called until #191:
        # `NOT_SERVED` and `REQUEST_FAILED` are both genuinely unavailable,
        # and `NOT_ATTEMPTED` is a third. What is pinned here is that a 404
        # reaches the status whose prose says EuropePMC served none — the
        # claim that used to be a parenthetical on the COI line and is a
        # provenance line keyed on the member since issue #203.
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        analyzer._check_europepmc(_FakeFullTextClient(None), _epmc_record(), analysis)
        assert analysis.full_text_status is FullTextStatus.NOT_SERVED
        assert _INDICATOR_COI_UNKNOWN in analysis.indicators
        assert "served none" in _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.NOT_SERVED]

    def test_a_record_with_no_full_text_was_never_attempted(self):
        # `inEPMC != "Y"` means Europe PMC never claimed to hold full text, so
        # nothing was requested. Distinct from a request that was made and
        # answered with a 404, which is what `NOT_SERVED` records since #191
        # — every other way of getting no document is `REQUEST_FAILED`.
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        analyzer._check_europepmc(_FakeFullTextClient(None), _epmc_record(in_epmc="N"), analysis)
        assert analysis.full_text_status is FullTextStatus.NOT_ATTEMPTED

    def test_a_refusal_says_it_was_served_rather_than_unavailable(self):
        # The prose half. `risk_indicators` is persisted, so this reaches a
        # stored result too — but as prose for humans, which is exactly why
        # the enum beside it exists (the `unknown_reason` argument, issue #21).
        # Since issue #203 that prose is the provenance line keyed on the
        # status, not a parenthetical on the COI line: same claim, out of
        # reach of the PubMed retraction that used to remove it.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Ours.</p><sub-article><p>Theirs.</p></article>"
        )
        analysis = _Analysis()
        analyzer._check_europepmc(client, _epmc_record(), analysis)
        assert analysis.full_text_status is FullTextStatus.UNCLOSED_REGION
        # The substring has to be unique to this member's line: `"served"`
        # alone, which this asserted until PR #205's review, occurs in five of
        # the eight and so passed on any other refusal's wording.
        assert (
            "nested-article region is left open"
            in _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.UNCLOSED_REGION]
        )
        assert _INDICATOR_COI_UNKNOWN in analysis.indicators

    def test_every_refusal_is_a_refusal_and_the_other_two_are_not(self):
        # The grouping the issue's own question needs — "which of my stored
        # results were computed without the full text I was served?" — put on
        # the enum so no caller re-enumerates it, and so a member added later
        # has to choose a side.
        assert FullTextStatus.TRUNCATED.is_refusal
        assert FullTextStatus.UNTERMINATED_MARKUP.is_refusal
        assert FullTextStatus.UNCLOSED_REGION.is_refusal
        assert FullTextStatus.ENTIRELY_NESTED.is_refusal
        assert not FullTextStatus.ANALYZED.is_refusal
        assert not FullTextStatus.NOT_SERVED.is_refusal
        assert not FullTextStatus.NOT_ATTEMPTED.is_refusal

    def test_every_status_chooses_a_side(self):
        # The rule the docstring states — "a member added later has to choose
        # a side" — mechanised rather than asserted. Enumerating the members
        # above stays green when another appears, and one omitted from the
        # refused set reads as `is_refusal is False`, which routes into the
        # "full text unavailable" indicator: for a served document that is
        # the falsehood issue #161 exists to remove, so the silent default
        # runs the wrong way. `TestTheAuditNetIsComplete`'s rule, applied to
        # an enum: a rule enforced by prose is not enforced.
        #
        # It has since collected one: `REQUEST_FAILED` (#187/#190/#191) is the
        # eighth member, and this test is what made it choose a side rather
        # than default into the wrong one. Written ordinal-free now, because
        # the prose said "an eighth member" in four places after the eighth
        # had arrived.
        assert (_REFUSED_FULL_TEXT_STATUSES | _NOT_REFUSED_FULL_TEXT_STATUSES) == set(
            FullTextStatus
        )
        assert not (_REFUSED_FULL_TEXT_STATUSES & _NOT_REFUSED_FULL_TEXT_STATUSES)
        # And the sets are what `is_refusal` actually reads, so neither can
        # drift into being a description of the property rather than its
        # definition.
        for status in FullTextStatus:
            assert status.is_refusal is (status in _REFUSED_FULL_TEXT_STATUSES)

    def test_a_record_promising_full_text_with_no_address_is_reported(self, caplog):
        # Reachable only under `inEPMC == "Y"`, so EuropePMC has claimed to
        # hold the full text and then given nothing to address it by. That is
        # a malformed record, not a closed-access paper, and it used to be
        # indistinguishable from one: no request, no log at any level.
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        record = {"resultList": {"result": [{"abstractText": "", "inEPMC": "Y"}]}}
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(_FakeFullTextClient(None), record, analysis, "doc-9")
        assert analysis.full_text_status is FullTextStatus.NOT_ATTEMPTED
        matching = [r for r in caplog.records if "no address for it" in r.getMessage()]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING
        assert "doc-9" in matching[0].getMessage()

    def test_an_ordinary_closed_access_record_is_not_reported(self, caplog):
        # The negative control: `inEPMC != "Y"` is the ordinary case and must
        # stay silent, or the warning above fires on most of the corpus.
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(
                _FakeFullTextClient(None), _epmc_record(in_epmc="N"), analysis, "doc-9"
            )
        assert analysis.full_text_status is FullTextStatus.NOT_ATTEMPTED
        assert not [r for r in caplog.records if "no address for it" in r.getMessage()]

    def test_the_warning_names_the_analysis_and_not_only_the_article(self, caplog):
        # `source`/`ext_id` resolve the *article*; `document_id` is the
        # caller's own key and the only field joining a log line to a stored
        # result. It was available two frames up and not threaded down.
        analyzer = TransparencyAnalyzer()
        client = _FakeFullTextClient(
            "<article><body><p>Ours.</p><sub-article><p>Theirs.</p></article>"
        )
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(client, _epmc_record(), analysis, document_id="doc-42")
        matching = [r for r in caplog.records if "unclosed nested article" in r.getMessage()]
        assert len(matching) == 1
        assert "doc-42" in matching[0].getMessage()

    def test_the_warning_quantifies_what_was_served(self, caplog):
        # "The message quantifies nothing" — contrast
        # `JATSArticle.suppressed_nested_articles`, which exists in `fulltext/`
        # for this and reports a count. How much arrived is what tells a
        # truncation at the first tag from one in the last paragraph.
        analyzer = TransparencyAnalyzer()
        body = "<article><body><sec><title>Methods</title><p>Some prose.</p></sec>"
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(_FakeFullTextClient(body), _epmc_record(), analysis)
        matching = [r for r in caplog.records if "did not arrive whole" in r.getMessage()]
        assert len(matching) == 1
        assert str(len(body.encode("utf-8"))) in matching[0].getMessage()

    def test_the_quantity_is_bytes_and_not_characters(self, caplog):
        # `resp.text` is decoded, so `len()` on it under-reports any body
        # carrying non-ASCII — routine in this corpus. The number exists to be
        # compared against a `Content-Length` or a corpus size distribution,
        # so the two readings must not be confused. Here they differ by 20,
        # and the negative half is what pins it: asserting only the byte count
        # would pass on a message that also printed the character count.
        analyzer = TransparencyAnalyzer()
        body = "<article><body><p>Δοκιμή — μέθοδοι καὶ ἀποτελέσματα.</p>"
        assert len(body.encode("utf-8")) != len(body)
        analysis = _Analysis()
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            analyzer._check_europepmc(_FakeFullTextClient(body), _epmc_record(), analysis)
        message = next(
            r.getMessage() for r in caplog.records if "did not arrive whole" in r.getMessage()
        )
        assert f"{len(body.encode('utf-8'))} bytes served" in message
        assert f"{len(body)} bytes served" not in message

    def test_the_status_reaches_the_stored_result(self, monkeypatch):
        # End to end: the field is on `TransparencyResult`, not only on the
        # private carrier, since the carrier never leaves this module.
        client = _RecordingClient(epmc=_epmc_payload(pmid="12345678"))
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="12345678")
        assert result.full_text_status is FullTextStatus.NOT_ATTEMPTED

    def test_a_refusal_reaches_the_stored_result_as_that_refusal(self, monkeypatch):
        # The end-to-end claim the issue actually makes, and the one that was
        # unpinned: *which* refusal it was has to survive to storage. Every
        # `analyze()`-level fixture used to omit `source`/`pmcid`, so no such
        # test ever issued a full-text request and all of them landed on the
        # `NOT_ATTEMPTED` default — under which
        # `full_text_status=ANALYZED if full_text_analyzed else NOT_ATTEMPTED`,
        # which discards every refusal distinction and is exactly the
        # information content this change removes, passed the whole suite.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True),
            full_text="<article><body><p>Methods and results.</p>",
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        # The whole URL, not a substring of it: `"fullTextXML" in url` is
        # satisfied by #184's two-segment form too, so this assertion passed
        # while every live fetch 404'd.
        assert client.full_text_url in client.urls()
        assert result.full_text_status is FullTextStatus.TRUNCATED
        assert result.full_text_analyzed is False
        assert _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.TRUNCATED] in result.risk_indicators
        # And it round-trips, since the point is that a *stored* result answers.
        assert (
            TransparencyResult.from_dict(result.to_dict()).full_text_status
            is FullTextStatus.TRUNCATED
        )

    def test_a_pubmed_statement_retracts_the_refused_indicator_too(self, monkeypatch):
        # The third COI line. `_merge_pubmed_signals` retracted the other two
        # and not this one, so a served-and-refused full text plus a PubMed
        # <CoiStatement> stored "status unknown" beside "disclosure found",
        # against `coi_disclosed=True` — permanently, in a persisted field,
        # which is issue #161's own failure mode inside the fix for it.
        #
        # There is one COI line now, and what it *stopped* carrying is the
        # provenance: `TestAProvenanceLineIsNotACoiClaim` is where the other
        # half of this scenario is asserted (issue #203).
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True),
            full_text="<article><body><p>Ours.</p><sub-article><p>Theirs.</p></article>",
            pubmed=_pubmed_xml(coi="Dr X consults for Y."),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.full_text_status is FullTextStatus.UNCLOSED_REGION
        assert result.coi_disclosed is True
        assert _INDICATOR_COI_IN_PUBMED in result.risk_indicators
        assert _INDICATOR_COI_UNKNOWN not in result.risk_indicators

    def test_every_coi_line_written_before_pubmed_is_retracted_by_it(self):
        # The rule itself, rather than one instance of it: every indicator the
        # COI branch can append while the status is undeterminable has to be in
        # the retraction set, or the next one added escapes it the way this one
        # did.
        #
        # It was three lines, and issue #203 made it two — not by dropping a
        # claim but by taking the *provenance* out of two of them, since a
        # retraction is all-or-nothing and those two also said what became of
        # the full text. What must be in the set is a line asserting something
        # about the COI status and nothing else.
        assert _INDICATOR_COI_UNKNOWN in _INDICATORS_RETRACTED_BY_PUBMED_COI
        assert _INDICATOR_NO_COI_IN_FULLTEXT in _INDICATORS_RETRACTED_BY_PUBMED_COI
        # And the line PubMed puts in their place is not itself retracted.
        assert _INDICATOR_COI_IN_PUBMED not in _INDICATORS_RETRACTED_BY_PUBMED_COI

    def test_the_document_id_is_threaded_from_analyze(self, monkeypatch, caplog):
        # The other half of the plumbing. The class above already pins
        # `_check_europepmc` -> `_fetch_europepmc_fulltext`; this pins
        # `analyze()` -> `_check_europepmc`, which defaults the parameter to
        # `""` and so loses the join key silently — the `_stamp_source()`
        # hazard, one module over. Deleting the argument at the call site
        # survived the whole suite before this existed.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True),
            full_text="<article><body><p>Methods and results.</p>",
        )
        _install_fake_client(monkeypatch, client)
        with caplog.at_level(logging.WARNING, logger="bmlib.transparency.analyzer"):
            TransparencyAnalyzer().analyze("doc-77", pmid="1")
        message = next(
            r.getMessage() for r in caplog.records if "did not arrive whole" in r.getMessage()
        )
        assert "doc-77" in message


class TestFullTextStatusOnTheResult:
    """The field's own contract, mirroring `unknown_reason`'s (issue #21).

    Declared last for positional stability, serialised by value, read
    defensively, and **additive rather than breaking**: results persisted
    before it existed load with ``None``, which means *not recorded* and not
    ``NOT_ATTEMPTED`` — a legacy result carrying ``full_text_analyzed=True``
    must not come back claiming nothing was attempted.
    """

    def test_it_defaults_to_not_recorded(self):
        result = TransparencyResult("doc-1", 50, TransparencyRisk.MEDIUM)
        assert result.full_text_status is None

    def test_it_round_trips_by_value(self):
        result = TransparencyResult(
            "doc-1",
            50,
            TransparencyRisk.MEDIUM,
            full_text_status=FullTextStatus.TRUNCATED,
        )
        payload = result.to_dict()
        assert payload["full_text_status"] == "truncated"
        assert TransparencyResult.from_dict(payload).full_text_status is FullTextStatus.TRUNCATED

    def test_not_attempted_round_trips_and_does_not_collapse_to_none(self):
        # The other direction of "`None` means *not recorded*, never
        # `NOT_ATTEMPTED`". Pinned only from the `None` side, a `to_dict` that
        # wrote `None` for `NOT_ATTEMPTED` — or a `from_dict` that read it back
        # as `None` — is invisible, and both erase a determinate answer this
        # version does record.
        result = TransparencyResult(
            "doc-1", 50, TransparencyRisk.MEDIUM, full_text_status=FullTextStatus.NOT_ATTEMPTED
        )
        payload = result.to_dict()
        assert payload["full_text_status"] == "not_attempted"
        assert TransparencyResult.from_dict(payload).full_text_status is (
            FullTextStatus.NOT_ATTEMPTED
        )

    def test_a_result_persisted_before_the_field_existed_loads(self):
        # The additive-not-breaking half: the *key* is read defensively.
        payload = TransparencyResult(
            "doc-1", 50, TransparencyRisk.MEDIUM, full_text_analyzed=True
        ).to_dict()
        del payload["full_text_status"]
        loaded = TransparencyResult.from_dict(payload)
        assert loaded.full_text_status is None
        # And `None` must not be read as a determinate claim about a result
        # that plainly did analyse full text.
        assert loaded.full_text_analyzed is True

    def test_an_unrecognised_member_raises_rather_than_loading_as_none(self):
        # Exactly as `risk_level` and `unknown_reason` do: a member this
        # version does not know about is a result it cannot interpret, and
        # inventing `None` would report it as never recorded.
        payload = TransparencyResult("doc-1", 50, TransparencyRisk.MEDIUM).to_dict()
        payload["full_text_status"] = "teleported"
        # `match=` is load-bearing: a bare `pytest.raises(ValueError)` is also
        # satisfied by `__post_init__`'s status/flag invariant, so a `from_dict`
        # that mapped an unrecognised value onto a *member* would still pass.
        with pytest.raises(ValueError, match="teleported"):
            TransparencyResult.from_dict(payload)

    def test_analyzed_and_the_flag_must_agree(self):
        # The one direction that can be enforced. `full_text_analyzed` is the
        # field that qualifies a stored `coi_disclosed=False`, so a status
        # disagreeing with it makes the pair uninterpretable.
        with pytest.raises(ValueError, match="if and only if"):
            TransparencyResult(
                "doc-1",
                50,
                TransparencyRisk.MEDIUM,
                full_text_analyzed=False,
                full_text_status=FullTextStatus.ANALYZED,
            )
        with pytest.raises(ValueError, match="if and only if"):
            TransparencyResult(
                "doc-1",
                50,
                TransparencyRisk.MEDIUM,
                full_text_analyzed=True,
                full_text_status=FullTextStatus.TRUNCATED,
            )

    def test_not_recording_the_status_imposes_nothing(self):
        # The converse is deliberately unenforced, for `unknown_reason`'s own
        # reason: refusing to construct a legacy result would make the field
        # a breaking change rather than an additive one.
        TransparencyResult("doc-1", 50, TransparencyRisk.MEDIUM, full_text_analyzed=True)
        TransparencyResult("doc-1", 50, TransparencyRisk.MEDIUM, full_text_analyzed=False)

    def test_the_field_is_appended_rather_than_sorted(self):
        # Downstream projects construct this dataclass positionally, so a new
        # field beside its logical neighbours would shift every following
        # argument by one with no error raised anywhere — the reason
        # `unknown_reason` and `Publication.pmcid` are where they are.
        #
        # The rule is "append", not "sort", so this pins the *tail order*
        # rather than which field happens to be last: asserting the latter
        # made a correctly-appended fourth field (issue #198's
        # `trial_results_status`) look like a violation while an insertion
        # *between* two of these — the actual defect — would have passed
        # whenever it was not at the very end.
        names = [f.name for f in dataclasses.fields(TransparencyResult)]
        assert names[-3:] == ["unknown_reason", "full_text_status", "trial_results_status"]


class _RaisingClient:
    """A client whose ``get`` raises, so the request never produces a response.

    Issue #187 is about what a raised exception is *stored* as, and the
    exception type is the whole input: a ``ConnectError`` is the environment
    and a ``TypeError`` is bmlib. Nothing else here varies.
    """

    def __init__(self, exc: BaseException):
        self._exc = exc
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        raise self._exc


class _StatusClient:
    """A client that answers the full-text URL with one chosen status code.

    :class:`_FakeFullTextClient` can only serve 200 or 404, which is why the
    404-only draw in the suite was silent about every other code — the gap
    issue #191 is. The URL is matched whole, for the reason that fake gives.
    """

    def __init__(self, status_code: int, text: str = "", ext_id: str = "PMC123"):
        self._status_code = status_code
        self._text = text
        self._url = f"{EUROPEPMC_REST_BASE}/{ext_id}/fullTextXML"
        self.served_urls: list[str] = []

    def get(self, url, **kwargs):
        self.served_urls.append(url)
        if url == self._url:
            return _FakeResponse(status_code=self._status_code, text=self._text)
        return _FakeResponse(status_code=404)


class TestAnAttemptThatGotNoAnswerSaysSo:
    """Issues #187, #190 and #191 — three ways `NOT_SERVED` was a false claim.

    ``NOT_SERVED`` is documented as *"Requested and not served"*, and issue
    #161 made it a determinate, machine-readable, persisted value. Three
    outcomes reached it that Europe PMC never asserted:

    * a **bmlib defect** on the request line, swallowed at DEBUG and stored as
      a Europe PMC absence (#187);
    * a **429, 503 or 403**, stored identically to the 404 whose ordinariness
      is the whole measured basis for the DEBUG level, and — with
      ``cache_results`` on and no retry anywhere in the module — cached as a
      permanent absence (#191);
    * an **empty HTTP 200 body**, which reached the *entirely-nested* branch
      and stored ``ENTIRELY_NESTED``, an ``is_refusal`` outcome, for a
      response that carried no document at all (#190).

    ``REQUEST_FAILED`` is the honest answer to all three: the attempt produced
    no document **and Europe PMC did not say it holds none**, which is what a
    404 says and nothing else here does.

    **Measured**, 2026-09-05, 200 live probes of ``fullTextXML`` stratified by
    source (MED/PMC/PPR) and publication year, built exactly as
    ``_check_europepmc`` builds them: 119 served, 81 non-200, and **81 of the
    81 were 404**. So a status other than 200 or 404 is the ordinary outcome
    of nothing — 0 of the 81 non-200s, the eligible denominator rather than
    0 of 200, and the floor the WARNING rests on, not a
    proof that Europe PMC never emits one. Among the 119 served, **0 carried
    an empty body**, the smallest being 2,622 bytes (median 85,925), so
    #190's population measures empty too; the fix is not carried by a rate but
    by the branch it lands in being wrong for it.
    """

    # ---- #191: a 404 is the measured outcome; nothing else is ----

    def test_a_404_is_what_not_served_now_means(self, caplog):
        # The narrowing's positive half, and the one case the DEBUG level was
        # actually measured on: Europe PMC answered, and its answer is that it
        # serves no open-access full text for this article.
        analyzer = TransparencyAnalyzer()
        client = _StatusClient(404)
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            fetch = analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC123")
        assert fetch.status is FullTextStatus.NOT_SERVED
        named = [r for r in caplog.records if "HTTP 404" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.DEBUG
        # The URL, because this was the one test in the class that #184's net
        # did not reach for free: `_StatusClient` 404s every URL it does not
        # recognise, so a wrong address produces the same 404 / `NOT_SERVED` /
        # DEBUG this test asserts, and it passed with the two-segment form in
        # place. Re-introducing that segment now reddens 16 of the class's 26
        # tests, this one among them; without this line it was 15 of 26.
        # `_StatusClient.served_urls` existed and was read by nothing until
        # PR #192's review — a recorder nothing asserts on drifts.
        assert client.served_urls == [f"{EUROPEPMC_REST_BASE}/PMC123/fullTextXML"]

    @pytest.mark.parametrize("status_code", [403, 429, 500, 502, 503, 504])
    def test_a_status_other_than_404_is_not_a_statement_about_this_article(
        self, status_code, caplog
    ):
        # `inEPMC: Y` and a 503 says nothing whatever about whether Europe PMC
        # holds this article's full text — which is exactly what `NOT_SERVED`
        # claimed on its behalf.
        analyzer = TransparencyAnalyzer()
        client = _StatusClient(status_code)
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            fetch = analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC123")
        assert fetch.status is FullTextStatus.REQUEST_FAILED
        assert fetch.status is not FullTextStatus.NOT_SERVED
        named = [r for r in caplog.records if f"HTTP {status_code}" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING

    def test_the_unmeasured_status_line_carries_the_url_the_404_line_carries(self, caplog):
        # #184 lived a release inside this silence and the URL is what named
        # it, so the louder branch must not carry less than the quiet one.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._fetch_europepmc_fulltext(_StatusClient(503), "PMC", "PMC123")
        url = f"{EUROPEPMC_REST_BASE}/PMC123/fullTextXML"
        assert [r for r in caplog.records if url in r.getMessage()]

    # ---- #187: a raised request ----

    def test_a_transport_error_is_not_a_europepmc_absence(self, caplog):
        # The environment failed. Europe PMC asserted nothing, so storing
        # "requested and not served" puts a claim in its mouth — and results
        # are cached, so the claim is permanent.
        analyzer = TransparencyAnalyzer()
        client = _RaisingClient(OSError("connection reset"))
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            fetch = analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC123")
        assert fetch.status is FullTextStatus.REQUEST_FAILED
        named = [r for r in caplog.records if "connection reset" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING
        # The *type* as well as the message, which the ERROR branch beside
        # this one already pins and this one did not: dropping
        # `type(e).__name__` from the WARNING line survived the whole suite,
        # leaving a `ConnectTimeout` and a `ReadTimeout` indistinguishable in
        # an operator's log at exactly the moment the distinction is the
        # question. `str(OSError("connection reset"))` does not contain
        # "OSError", so this assertion cannot pass on the message alone.
        assert "OSError" in named[0].getMessage()

    @pytest.mark.parametrize(
        "exc",
        [
            TypeError("client is not what this code assumes"),
            AttributeError("'NoneType' object has no attribute 'get'"),
            NameError("name 'ext' is not defined"),
            KeyError("params"),
            IndexError("tuple index out of range"),
        ],
    )
    def test_a_bmlib_defect_is_reported_as_one(self, exc, caplog):
        # `fulltext/service.py`'s `_BUG_TYPES` rule, restated here: a type
        # that can only mean bmlib is wrong must never be held at DEBUG.
        # ERROR is the level the parse audit fixes for the same claim.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            fetch = analyzer._fetch_europepmc_fulltext(_RaisingClient(exc), "PMC", "PMC123")
        assert fetch.status is FullTextStatus.REQUEST_FAILED
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert type(exc).__name__ in errors[0].getMessage()

    def test_a_bmlib_defect_does_not_cost_the_analysis(self):
        # Re-raising was the issue's other option and is refused: every other
        # step in `analyze()` swallows, `fulltext/service.py` — the precedent
        # the issue itself cites — reports at ERROR and continues, and one
        # defect must not lose a paper its abstract-level score.
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        record = {
            "resultList": {
                "result": [
                    {
                        "abstractText": "The authors declare no competing interests.",
                        "inEPMC": "Y",
                        "pmcid": "PMC123",
                    }
                ]
            }
        }
        analyzer._check_europepmc(_RaisingClient(TypeError("boom")), record, analysis)
        assert analysis.full_text_status is FullTextStatus.REQUEST_FAILED
        # The abstract was still scanned, which is the whole point of not raising.
        assert analysis.coi_disclosed is True

    def test_an_environment_failure_is_not_reported_as_a_bmlib_defect(self, caplog):
        # The negative control the ERROR level needs: ERROR must mean only
        # "bmlib is wrong", or it stops meaning anything. `OSError` is
        # deliberately outside `_BUG_TYPES` — it is the environment — and
        # `ValueError` carries `json.JSONDecodeError`, `SyntaxError` carries
        # `ET.ParseError`, and `RuntimeError` carries `RecursionError`.
        analyzer = TransparencyAnalyzer()
        for exc in (OSError("down"), ValueError("bad json"), SyntaxError("bad xml")):
            caplog.clear()
            with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
                analyzer._fetch_europepmc_fulltext(_RaisingClient(exc), "PMC", "PMC123")
            assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    # ---- #190: an empty HTTP 200 body ----

    def test_an_empty_body_is_not_a_refusal_that_did_not_happen(self):
        # `_strip_nested_articles("")` returns `""` — falsy but not `None` —
        # so the unclosed-region branch does not fire and the emptiness check
        # below it does, which is the branch meaning *everything served was
        # nested*. Nothing was served, so nothing can have been nested.
        analyzer = TransparencyAnalyzer()
        fetch = analyzer._fetch_europepmc_fulltext(_StatusClient(200, ""), "PMC", "PMC123")
        assert fetch.status is FullTextStatus.REQUEST_FAILED
        assert fetch.status is not FullTextStatus.ENTIRELY_NESTED
        assert not fetch.status.is_refusal

    def test_an_empty_body_does_not_store_a_refusal_indicator(self):
        # The half that reaches a stored result. `ENTIRELY_NESTED.is_refusal`
        # is True, so the caller appended *"COI disclosure status unknown
        # (full text served but not usable)"* into the persisted
        # `risk_indicators` — a claim that Europe PMC served a document bmlib
        # declined to scan, for a response carrying no document. Issue #161
        # exists to remove exactly this, and did not reach it.
        analyzer = TransparencyAnalyzer()
        analysis = _Analysis()
        record = {"resultList": {"result": [{"abstractText": "", "inEPMC": "Y", "id": "PMC123"}]}}
        analyzer._check_europepmc(_StatusClient(200, ""), record, analysis)
        assert analysis.full_text_status is FullTextStatus.REQUEST_FAILED
        assert _INDICATOR_COI_UNKNOWN in analysis.indicators
        # The claim itself, which since issue #203 is the provenance line the
        # status selects: nothing was served, so nothing can read as
        # served-but-unusable.
        assert "served, but" not in _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.REQUEST_FAILED]

    def test_the_empty_body_line_does_not_contradict_itself(self, caplog):
        # It logged *"is entirely nested articles (0 bytes served)"*. Both
        # halves of that sentence cannot be true, and the parenthesis is the
        # half that is.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._fetch_europepmc_fulltext(_StatusClient(200, ""), "PMC", "PMC123")
        assert not [r for r in caplog.records if "nested" in r.getMessage()]
        named = [r for r in caplog.records if "empty body" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING

    def test_a_body_that_really_is_entirely_nested_still_says_so(self):
        # The negative control the new guard needs. It runs ahead of three
        # checks, so it has to be shown not to have swallowed the one whose
        # branch it was landing in — a guard placed one line too high reports
        # every nested-only document as an empty response.
        analyzer = TransparencyAnalyzer()
        client = _StatusClient(200, "<sub-article><p>Reviewer.</p></sub-article>")
        fetch = analyzer._fetch_europepmc_fulltext(client, "PMC", "PMC123")
        assert fetch.status is FullTextStatus.ENTIRELY_NESTED

    def test_a_whitespace_only_body_is_still_entirely_nested_not_empty(self):
        # The boundary the guard is deliberately drawn at, and **this test is
        # the only thing holding it** — `test_an_empty_body_is_not_a_refusal_
        # that_did_not_happen` cannot help, `""` stripping empty too. "Nothing
        # arrived" is `served == ""`; a body carrying bytes that strip to
        # nothing *did* arrive and is a document-shaped claim, so it belongs
        # to the emptiness check below rather than to this one.
        #
        # The document is a **wholly-whitespace body**, and naming it exactly
        # is the point: an earlier draft of the comment at the guard said
        # `not served.strip()` would take *"a document whose regions strip out
        # leaving whitespace"* out of the entirely-nested branch, and it would
        # not — `"  <sub-article>…</sub-article>  "` has a truthy
        # `served.strip()`, so both spellings reach that branch identically.
        # Mutating the guard reddens exactly this test and no other.
        analyzer = TransparencyAnalyzer()
        fetch = analyzer._fetch_europepmc_fulltext(_StatusClient(200, "   \n  "), "PMC", "PMC123")
        assert fetch.status is FullTextStatus.ENTIRELY_NESTED

    def test_the_document_the_boundary_does_not_decide(self):
        # The negative half of the comment above, so the corrected claim is
        # measured rather than asserted: a body whose regions strip out
        # leaving whitespace reaches `ENTIRELY_NESTED` under *either* spelling
        # of the guard, because its own `.strip()` is truthy. Without this,
        # the rationale at the guard is a sentence no test can contradict —
        # which is how the wrong document survived into three files.
        body = "  <sub-article><p>Reviewer.</p></sub-article>  "
        assert body.strip()
        analyzer = TransparencyAnalyzer()
        fetch = analyzer._fetch_europepmc_fulltext(_StatusClient(200, body), "PMC", "PMC123")
        assert fetch.status is FullTextStatus.ENTIRELY_NESTED

    # ---- the partition ----

    def test_the_new_member_is_not_a_refusal(self):
        # Nothing was served, so there is nothing to have refused — the same
        # side `NOT_SERVED` and `NOT_ATTEMPTED` are on. `test_every_status_
        # chooses_a_side` is what forces the choice to be made at all; this
        # records which way it went and why.
        assert not FullTextStatus.REQUEST_FAILED.is_refusal
        assert FullTextStatus.REQUEST_FAILED in _NOT_REFUSED_FULL_TEXT_STATUSES

    def test_it_round_trips_by_value(self):
        result = TransparencyResult(
            "doc-1", 50, TransparencyRisk.MEDIUM, full_text_status=FullTextStatus.REQUEST_FAILED
        )
        payload = result.to_dict()
        assert payload["full_text_status"] == "request_failed"
        assert TransparencyResult.from_dict(payload).full_text_status is (
            FullTextStatus.REQUEST_FAILED
        )

    # ---- the member has to survive to a stored result ----

    @pytest.mark.parametrize(
        ("kwargs", "issue"),
        [
            ({"full_text": "", "full_text_status_code": 200}, "190"),
            ({"full_text": "<article/>", "full_text_status_code": 503}, "191"),
        ],
    )
    def test_it_reaches_the_stored_result_as_itself(self, kwargs, issue, monkeypatch):
        # `test_a_refusal_reaches_the_stored_result_as_that_refusal` one
        # member on, and for its stated reason: what this member *adds* over
        # `NOT_SERVED` is that a stored result can be audited for "would
        # re-running change this?", and nothing asserted it on a
        # `TransparencyResult` — only on the private `_Analysis` carrier,
        # which never leaves the module. That precedent's own comment records
        # this exact gap going undetected once already.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True), **kwargs
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert client.full_text_url in client.urls(), issue
        assert result.full_text_status is FullTextStatus.REQUEST_FAILED
        assert result.full_text_analyzed is False
        # Not a refusal's line, which is #190's whole complaint: nothing was
        # served, so nothing can have been served-but-unusable. Asserted over
        # the whole refused side rather than against one string, since issue
        # #203 replaced the single refusal line with one per member.
        assert not [
            line
            for line in result.risk_indicators
            if line in {_FULL_TEXT_PROVENANCE_INDICATORS[st] for st in _REFUSED_FULL_TEXT_STATUSES}
        ]
        # And it survives the trip through storage as itself.
        assert TransparencyResult.from_dict(result.to_dict()).full_text_status is (
            FullTextStatus.REQUEST_FAILED
        )


class TestTheRestatedBugTypesMatchTheOtherModules:
    """`_BUG_TYPES` is stated twice, so something must compare them.

    `TestTheRestatedSetMatchesTheParsers`' argument, one constant over.
    `bmlib.transparency` depends on nothing in `bmlib.fulltext`, so the
    deny-list is restated rather than imported — and that leaves *"if the rule
    changes, change both"* enforced by prose, which in this repo is not
    enforced. A test may import both where the module may not.

    Agreeing is the right relation here, unlike the sampler predicates that
    must **differ** from the parser's: this is one claim about Python's
    exception hierarchy — which types can only mean the caller is wrong — and
    not a judgement about anyone's data. The drift that matters is one-sided:
    a type added to `fulltext`'s copy alone goes on being held at DEBUG here.
    """

    def test_the_two_deny_lists_hold_the_same_types(self):
        from bmlib.fulltext import service

        assert set(_BUG_TYPES) == set(service._BUG_TYPES)

    @pytest.mark.parametrize(
        "excluded",
        [
            ValueError("bad json"),
            json.JSONDecodeError("m", "d", 0),
            SyntaxError("bad xml"),
            ET.ParseError("not well-formed"),
            RuntimeError("something"),
            RecursionError("maximum recursion depth exceeded"),
            OSError("down"),
        ],
    )
    def test_the_exclusions_that_are_load_bearing_stay_excluded(self, excluded):
        # Naming them, because all three pairs read as omissions and are not:
        # `json.JSONDecodeError` IS a `ValueError` and every `resp.json()` on
        # a malformed body raises one; `ET.ParseError` IS a `SyntaxError`;
        # `RecursionError` IS a `RuntimeError`. Admitting any of the three
        # would report a remote-data failure as a bmlib defect at ERROR,
        # which is the level's own rule broken from the other side.
        #
        # **`isinstance`, never `not in _BUG_TYPES`**, and the difference is
        # a mutant that survived the whole suite: replacing `KeyError,
        # IndexError` with their shared base `LookupError` in *both* copies
        # passed 3218 tests. A membership test sees only the names it was
        # given, so a deny-list silently widened to every subclass of a base
        # it does not name reads as unchanged — while the code decides by
        # `isinstance`, which walks the hierarchy. Test the relation the code
        # uses, not the one that is easier to write. Each concrete subclass is
        # listed beside its base for the same reason: the base alone cannot
        # detect a widening *to* that base.
        assert not isinstance(excluded, _BUG_TYPES)

    def test_the_deny_list_admits_no_type_beyond_the_five_it_names(self):
        # The other end of the same mutant. Every entry must be one of the
        # five named types exactly — not a subclass and not a base — so
        # widening `KeyError, IndexError` to `LookupError` reddens here even
        # though both copies agree and no excluded name moved.
        assert set(_BUG_TYPES) == {TypeError, AttributeError, NameError, KeyError, IndexError}


class TestTheUserAgentIsOneClinicalTrialsGovAccepts:
    """Issue #194 — ClinicalTrials.gov refused the header ``analyze()`` sent.

    `analyze()` builds one client for the whole analysis and sets a
    ``User-Agent`` on it, which overrides the one httpx would have sent. That
    header is refused at ClinicalTrials.gov's edge with a bare 134-byte
    ``403 Forbidden`` page, so `_check_trial_results` returned ``False`` for
    every trial bmlib ever asked about — indistinguishable, in a ``bool``,
    from *"this trial posted no results"*. The cost was
    ``SCORE_RESULTS_POSTED`` never awarded to any paper and the indicator
    *"Registered trial without posted results"* persisted as a false claim
    about the trial.

    **Measured, not read off a doc** (2026-09-06, alternating User-Agents
    against ``/api/v2/studies/{nct}?fields=hasResults``): six alternating
    rounds of bmlib's header and httpx's default gave 403/200 six times of
    six, four accessions all 403'd on bmlib's, and of thirteen header shapes
    tried only the five carrying the token ``python-httpx`` served 200 —
    ``curl``, ``python-requests``, ``Python-urllib``, ``Go-http-client``,
    ``PostmanRuntime`` and a browser string were all refused. So the rule is
    the token, and the token can sit anywhere in the header.

    **What is pinned here is the token and nothing about the edge.** No test
    can hold a remote allow-list; no test in this suite makes a live request,
    which is exactly why a live-only policy went unseen. The guard is
    ``scripts/sample_api_failures.py``, and these tests only stop the token
    being dropped from the header by someone tidying it.
    """

    def test_the_header_carries_the_token_the_edge_admits(self):
        assert "python-httpx" in _user_agent("who@example.org", "0.28.1")

    def test_the_header_still_identifies_bmlib_and_a_contact_address(self):
        # The token is *added to* bmlib's identification, never substituted
        # for it: CrossRef and NCBI both ask a caller to say who it is, and
        # answering "python-httpx" would trade one API's policy for two
        # others'.
        header = _user_agent("who@example.org", "0.28.1")
        assert "bmlib/" in header
        assert "who@example.org" in header

    def test_the_httpx_version_is_the_real_one_not_a_literal(self):
        # The version is passed in from the caller's own `httpx.__version__`
        # rather than written here, so the header stays true as httpx moves.
        # A bare `python-httpx` also serves 200, so this is honesty rather
        # than necessity — and the reason it is worth a test is that a
        # hard-coded version is a lie that never fails loudly.
        assert "python-httpx/9.9.9" in _user_agent("who@example.org", "9.9.9")

    def test_analyze_sends_it(self, monkeypatch):
        # End to end, because the header is only load-bearing where
        # `analyze()` actually installs it — the module could hold a perfect
        # `_user_agent` and pass something else to the client. `#184`'s whole
        # lesson: pin the value at the level a caller exercises.
        import httpx

        from bmlib.transparency import analyzer as analyzer_mod

        captured: dict = {}

        client = _RecordingClient(epmc=_epmc_payload())

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return client

        monkeypatch.setattr(analyzer_mod, "_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(httpx, "Client", _capture)

        TransparencyAnalyzer(email="who@example.org").analyze("doc", pmid="1")

        assert captured["headers"]["User-Agent"] == _user_agent(
            "who@example.org", httpx.__version__
        )


class _VerbatimResponse:
    """A response whose ``json()`` returns exactly what it was handed.

    **Not `_FakeResponse`**, which does ``json_data or {}`` — so a test
    passing a JSON *list* or an empty object gets a non-empty dict instead,
    and an assertion about either is vacuous. Two mutants survived the whole
    suite on that: `not isinstance(data, dict)` weakened to `data is None`,
    and `epmc is None` weakened to `not epmc`. Ask which line of the fixture
    the assertion depends on.
    """

    def __init__(self, status_code: int = 200, payload: object = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    def json(self):
        return self._payload


class _AnsweringClient:
    """Answers every request with one status, recording what was asked.

    Deliberately not `_StatusClient`: that one is shaped for the full-text
    path, and these five helpers are addressed by five different hosts.
    """

    def __init__(self, status_code: int = 200, payload: object = None, text: str = ""):
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        return _VerbatimResponse(status_code=self.status_code, payload=self.payload, text=self.text)


class _NoDecoderClient:
    """Answers 200 with an object that has no ``json`` — *bmlib* being wrong.

    The other side of `_UndecodableClient`: there the remote sent something
    unparseable, here bmlib is holding a response object it is wrong about.
    Both used to print the same WARNING about the remote's body.
    """

    def get(self, url, **kwargs):
        class _Resp:
            status_code = 200
            text = ""

        return _Resp()


class _UnreadableTextClient:
    """Answers 200 with a body whose ``.text`` raises — `_request_text`'s branch."""

    def get(self, url, **kwargs):
        class _Resp:
            status_code = 200

            @property
            def text(self):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        return _Resp()


class _TrialListBodyClient:
    """Serves a registered trial whose ClinicalTrials.gov body is a JSON list.

    Enough of `analyze()`'s pipeline to reach `_check_trial_registration` with
    one accession: PubMed supplies it through `<DataBankList>`, which is the
    route that wins over the abstract heuristic.
    """

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        if "clinicaltrials" in url:
            return _VerbatimResponse(status_code=200, payload=[{"hasResults": True}])
        if "eutils" in url:
            return _VerbatimResponse(
                status_code=200,
                text=_pubmed_xml(databanks=(("ClinicalTrials.gov", ("NCT00000001",)),)),
            )
        return _VerbatimResponse(status_code=200, payload={"resultList": {"result": []}})


class _UndecodableClient:
    """Answers 200 with a body that will not parse — a remote's failure, not ours."""

    def get(self, url, **kwargs):
        class _Resp:
            status_code = 200

            @property
            def text(self):
                return "{"

            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        return _Resp()


def _call_helper(analyzer, name, client):
    """Invoke one of the five dropped-response helpers by name."""
    return {
        "crossref": lambda: analyzer._query_crossref(client, "10.1/x"),
        "europepmc": lambda: analyzer._query_europepmc(client, 'DOI:"10.1/x"'),
        "pubmed": lambda: analyzer._query_pubmed(client, "123"),
        "openalex": lambda: analyzer._query_openalex(client, "10.1/x"),
        "trials": lambda: analyzer._check_trial_results(client, "NCT00000001"),
    }[name]()


#: The five, named once. Every test below is parametrised over this rather
#: than over a list written per test, because issue #193 was one fix applied
#: to one of five copies of a shape — and a sixth helper added to the module
#: and not here would be exactly as unexamined as these five were.
_DROPPED_RESPONSE_HELPERS = ("crossref", "europepmc", "pubmed", "openalex", "trials")


class TestADroppedResponseGetsALine:
    """Issue #193 — five helpers threw a response away without saying so.

    Each wrapped its request in ``except Exception`` -> ``logger.debug`` ->
    ``return None``, which leaves two silences of different kinds:

    * a :data:`_BUG_TYPES` member — bmlib being wrong — held at a level
      nobody enables, which is issue #187 unfixed in five more places;
    * **a non-200 falling off the end with no line at any level**, since the
      ``except`` catches only raises. Not a level problem but an absence:
      there was no DEBUG line to turn on.

    ``_query_europepmc``'s copy is the one that gated the rest. ``analyze()``
    calls ``_check_europepmc`` only when the search returned a record, so in
    an outage the search 503s, the full-text step is never reached, and the
    result stores ``NOT_ATTEMPTED`` — *"No request was made"* — at HIGH with a
    tier downgrade, from zero log lines. That half is
    :class:`TestAnOutageIsNotAnAnswer`.
    """

    @pytest.mark.parametrize("helper", _DROPPED_RESPONSE_HELPERS)
    def test_a_non_200_is_no_longer_silent(self, helper, caplog):
        # The absence itself. Before this, *nothing* was emitted at any level
        # for a 500 — so there was no line to raise the level of, and no
        # amount of enabling DEBUG would have shown an operator an outage.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            _call_helper(analyzer, helper, _AnsweringClient(500))
        assert [r for r in caplog.records if "500" in r.getMessage()]

    @pytest.mark.parametrize("helper", _DROPPED_RESPONSE_HELPERS)
    def test_a_non_200_warns_unless_the_draw_earned_it_quiet(self, helper, caplog):
        # A 500 is nobody's ordinary outcome, so it warns everywhere. The
        # statuses that *are* ordinary are named per endpoint and tested
        # below; this is the branch they are the exception to.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            _call_helper(analyzer, helper, _AnsweringClient(500))
        named = [r for r in caplog.records if "500" in r.getMessage()]
        assert named
        assert all(r.levelno == logging.WARNING for r in named)

    @pytest.mark.parametrize("helper", _DROPPED_RESPONSE_HELPERS)
    def test_a_bmlib_defect_is_reported_as_one(self, helper, caplog):
        # Issue #187's rule, extended to the five places it was not applied.
        # A `TypeError` out of a request is bmlib being wrong about its own
        # client, and holding that at DEBUG is what `fulltext/service.py`
        # keeps `_BUG_TYPES` for. ERROR is `jats_parser`'s level for the
        # identical claim.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            _call_helper(analyzer, helper, _RaisingClient(TypeError("not a client")))
        named = [r for r in caplog.records if "not a client" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.ERROR
        # The traceback, which is the whole of what an operator can act on:
        # without it the report is "bmlib logged a TypeError".
        assert named[0].exc_info is not None

    @pytest.mark.parametrize("helper", _DROPPED_RESPONSE_HELPERS)
    def test_an_environment_failure_is_not_reported_as_a_bmlib_defect(self, helper, caplog):
        # The other side of the same rule, and the one that keeps ERROR
        # meaning only "bmlib is wrong": a reset connection is the network,
        # and an ERROR on it would make the level useless for the case above.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            _call_helper(analyzer, helper, _RaisingClient(OSError("connection reset")))
        named = [r for r in caplog.records if "connection reset" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING
        # The type as well as the message — `str(OSError("connection reset"))`
        # does not contain "OSError", so this cannot pass on the message
        # alone. Dropping it leaves a `ConnectTimeout` and a `ReadTimeout`
        # indistinguishable in a log, the mutant PR #192's review found one
        # method over.
        assert "OSError" in named[0].getMessage()

    @pytest.mark.parametrize("helper", _DROPPED_RESPONSE_HELPERS)
    def test_nothing_is_raised_out_of_any_of_them(self, helper):
        # `analyze()` wraps none of these, so a helper that starts raising
        # costs the whole analysis. Reporting and continuing is the module's
        # rule, argued at `_fetch_europepmc_fulltext`'s own handler.
        analyzer = TransparencyAnalyzer()
        for client in (_RaisingClient(TypeError("x")), _RaisingClient(OSError("y"))):
            # `is None` for all five, not `in (None, False)`: since PR #195's
            # review `_check_trial_results` is a tri-state, so every one of
            # them spells "no answer" the same way. The looser assertion
            # accepted two contracts and so could not see that change.
            assert _call_helper(analyzer, helper, client) is None

    _JSON_HELPERS = ("crossref", "europepmc", "openalex", "trials")

    @pytest.mark.parametrize("helper", _JSON_HELPERS)
    def test_a_body_that_will_not_decode_is_reported_and_not_dropped(self, helper, caplog):
        # A 200 carrying something that is not JSON is the remote's failure,
        # not ours — `json.JSONDecodeError` is a `ValueError` and deliberately
        # outside `_BUG_TYPES` — so it WARNs rather than ERRORing, and it says
        # what happened instead of reading as "the query failed".
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            result = _call_helper(analyzer, helper, _UndecodableClient())
        assert result is None
        named = [r for r in caplog.records if "Expecting value" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING

    @pytest.mark.parametrize("helper", _JSON_HELPERS)
    def test_a_decode_that_raises_a_bug_type_is_reported_as_a_bmlib_defect(self, helper, caplog):
        # **The decode layer had its own copy of the shape issue #187 is
        # about** (PR #195's review). It wrapped `resp.json()` in a bare
        # `except Exception` and WARNed unconditionally, so a response object
        # bmlib was wrong about — one with no `.json` at all — was reported as
        # *"the remote answered 200 with a body that is not JSON"*: a
        # `_BUG_TYPES` member dressed as a claim about CrossRef. That is #187
        # inside the fix for it, one layer up, and the level is now read from
        # the type by `_report_swallowed_exception` at all three sites.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            result = _call_helper(analyzer, helper, _NoDecoderClient())
        assert result is None
        named = [r for r in caplog.records if "bmlib defect" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.ERROR
        assert named[0].exc_info is not None
        # And it must not also be reported as the remote's malformed body,
        # which is the line it used to get instead.
        assert not [r for r in caplog.records if "not JSON" in r.getMessage()]

    def test_a_text_body_that_cannot_be_read_is_reported(self, caplog):
        # `_request_text`'s own handler, which no test reached — `"pubmed"`
        # was dropped from the parametrisation above with no comment, so the
        # branch was 0% covered and its level and wording were unpinned. It is
        # the step that supplies the `<CoiStatement>` the retraction set
        # depends on, so losing it silently is not cosmetic.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            result = analyzer._query_pubmed(_UnreadableTextClient(), "123")
        assert result is None
        named = [r for r in caplog.records if "could not be read" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING
        assert "UnicodeDecodeError" in named[0].getMessage()

    def test_a_trial_body_that_is_not_an_object_is_not_a_finding(self):
        # **The fixture matters here more than the assertion**, and the first
        # one chose the single payload that could not tell the guard from its
        # own mutant: `payload=[]` is falsy, so weakening
        # `not isinstance(data, dict)` to `not data` survived the whole suite
        # (PR #195's review). A *non-empty* list separates them.
        #
        # **Since issue #199 it no longer separates them here**, and the
        # comment that said it did was left behind by the change that made it
        # false (PR #208's review): `_request_json` refuses a non-object body
        # a layer down, so this list never reaches `_check_trial_results` at
        # all and the test now passes through the `None` path. What it still
        # pins is the *outcome* — the answer is `None` and no longer `False`,
        # a body that is not a JSON object answering the question no more than
        # a 404 does, which was issue #194's conflation surviving its own fix.
        # What pins the guard itself is the test below, which is the only way
        # left to reach it.
        analyzer = TransparencyAnalyzer()
        client = _AnsweringClient(200, payload=[{"hasResults": True}])
        assert analyzer._check_trial_results(client, "NCT1") is None

    def test_an_unusable_body_is_refused_at_this_site_too(self, monkeypatch):
        # **`docs/DECISIONS.md` says this guard must not be narrowed to
        # `data is None`; until now nothing made that true** — the narrowing
        # passed the entire suite, because `_request_json` closed the only
        # path that reached it with a non-object (PR #208's review). A rule
        # enforced by prose is not enforced, which is this repo's own
        # `TestTheAuditNetIsComplete` lesson.
        #
        # Stubbing the boundary is the point rather than a convenience: what
        # the guard defends against is a *future* change to `_request_json`'s
        # promise, which mypy would accept and no end-to-end fixture can
        # stage. The payload is non-empty so it also separates the guard from
        # its own truthiness mutant, as the test above used to.
        analyzer = TransparencyAnalyzer()
        monkeypatch.setattr(
            TransparencyAnalyzer,
            "_request_json",
            lambda self, *a, **k: [{"hasResults": True}],
        )
        assert analyzer._check_trial_results(object(), "NCT1") is None

    def test_such_a_body_does_not_escape_analyze(self, monkeypatch):
        # The other half, at the level a caller exercises. Under the mutant
        # above, `.get()` on a list raises `AttributeError` out of
        # `_check_trial_registration`, which `analyze()` wraps nowhere — so
        # the whole analysis is lost to a body one endpoint sent. The unit
        # test cannot see that; this one does.
        import httpx

        from bmlib.transparency import analyzer as analyzer_mod

        monkeypatch.setattr(analyzer_mod, "_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: _TrialListBodyClient())
        result = TransparencyAnalyzer().analyze("doc-1", pmid="123")
        assert result.trial_registered is True
        # Asked, and the answer was unusable — so "could not be checked", and
        # emphatically not "without posted results".
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in result.risk_indicators
        assert _INDICATOR_NO_POSTED_RESULTS not in result.risk_indicators

    def test_a_200_still_marks_an_api_reachable(self):
        # The property `analyze()` reports UNKNOWN from. Set on the 200 and
        # before the body is read, exactly as before: a remote that answered
        # and then sent something unreadable was still reachable, and
        # demoting the whole analysis to UNKNOWN over a malformed body would
        # be a larger claim than the evidence supports.
        analyzer = TransparencyAnalyzer()
        analyzer._query_crossref(_UndecodableClient(), "10.1/x")
        assert analyzer._api_reachable is True


class TestAnOutageIsNotAnAnswer:
    """Issue #193's other half — ``NOT_ATTEMPTED`` covered two different claims.

    ``analyze()`` reaches the full-text step only when the EuropePMC *search*
    returned a record, so an outage skipped it entirely and the result stored
    ``NOT_ATTEMPTED``, documented *"No request was made — EuropePMC never
    claimed to hold full text … or there was no record to ask about"*. Both
    halves of that sentence are claims about EuropePMC's answer, and in an
    outage there was no answer: one member covering two causes puts words in a
    third party's mouth, which is issues #187/#190/#191 exactly, one step up
    the call chain.

    ``SEARCH_FAILED`` is the split. It is not a refusal — nothing was served —
    and ``test_every_status_chooses_a_side`` is what made it pick.
    """

    def _analyze_with(self, monkeypatch, client, **ids) -> TransparencyResult:
        import httpx

        from bmlib.transparency import analyzer as analyzer_mod

        monkeypatch.setattr(analyzer_mod, "_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
        return TransparencyAnalyzer().analyze("doc-1", **(ids or {"pmid": "123"}))

    def test_a_search_that_never_answered_says_so(self, monkeypatch):
        result = self._analyze_with(monkeypatch, _EveryRequestFails(503))
        assert result.full_text_status is FullTextStatus.SEARCH_FAILED

    def test_a_search_that_answered_with_an_empty_body_is_not_an_outage(self, monkeypatch):
        # `is None`, not falsiness — and this is the test that separates them,
        # because an envelope carrying an empty result *list* is still a
        # truthy dict. A 200 whose whole body is `{}` is EuropePMC answering,
        # so the full-text step was skipped for its answer and not for its
        # absence. Mutate the old half of a condition you extend.
        result = self._analyze_with(monkeypatch, _AnsweringClient(200, payload={}))
        assert result.full_text_status is FullTextStatus.NOT_ATTEMPTED

    def test_a_search_that_answered_with_no_record_still_reads_not_attempted(self, monkeypatch):
        # The distinction the split exists for, from the other side: here
        # EuropePMC *did* answer, and its answer was that it has nothing for
        # this identifier. "No request was made" is then true of the full-text
        # step and nothing is being claimed on anyone's behalf.
        client = _RecordingClient(epmc={"resultList": {"result": []}})
        result = self._analyze_with(monkeypatch, client)
        assert result.full_text_status is FullTextStatus.NOT_ATTEMPTED

    def test_it_is_not_a_refusal(self):
        # Nothing was served, so there is nothing to have refused — and the
        # caller must not persist "full text served but not usable" for a
        # request that produced no response at all, which is issue #190's
        # defect one step up.
        assert FullTextStatus.SEARCH_FAILED.is_refusal is False

    def test_the_result_carries_an_indicator_a_reader_can_act_on(self, monkeypatch):
        # **A partial outage, which is the scenario issue #193 measured** —
        # CrossRef answering and EuropePMC not. It is the one that matters,
        # because a *total* outage already reports UNKNOWN/UNREACHABLE and
        # says so; here the analysis completes, is scored, and reaches HIGH
        # with a tier downgrade, and `risk_indicators` was empty. The status
        # is the machine-readable half; this is the half a human reads, and
        # it is persisted.
        result = self._analyze_with(monkeypatch, _CrossRefOnlyClient(), doi="10.1/x")
        assert _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.SEARCH_FAILED] in (
            result.risk_indicators
        )
        assert result.full_text_status is FullTextStatus.SEARCH_FAILED
        # Not UNKNOWN: an API answered, so the result is a real verdict —
        # which is exactly why the reason has to be carried on it.
        assert result.risk_level is not TransparencyRisk.UNKNOWN
        # And the harm the docstring names, asserted rather than described:
        # without these two lines the test passes for a LOW-risk result, which
        # would not need an indicator at all.
        assert result.risk_level is TransparencyRisk.HIGH
        # An `int`, not a flag — the number of tiers a consumer should drop.
        assert result.tier_downgrade_applied >= 1

    def test_a_total_outage_still_records_which_step_never_ran(self, monkeypatch):
        # The early UNREACHABLE return substitutes its own indicator, and
        # rightly — UNKNOWN already says nothing was measured. But it reads
        # `full_text_status` off the carrier rather than restating a
        # constant, so the finer answer survives into the stored result.
        result = self._analyze_with(monkeypatch, _EveryRequestFails(503))
        assert result.unknown_reason is TransparencyUnknownReason.UNREACHABLE
        assert result.full_text_status is FullTextStatus.SEARCH_FAILED

    def test_that_indicator_is_retracted_when_pubmed_supplies_a_coi_statement(self):
        # The COI claim this branch appends is retracted by a structured
        # `<CoiStatement>`, which refutes it — and a line added to the
        # appending site and not to the retracting one stores "status
        # unknown" beside "disclosure found", issue #161's own failure mode
        # inside its fix.
        #
        # It was a *fourth* line saying two things at once until issue #203,
        # and the half that must **not** be retracted — that the search
        # produced no answer — is now the provenance line, asserted in
        # `TestAProvenanceLineIsNotACoiClaim`.
        assert _INDICATOR_COI_UNKNOWN in _INDICATORS_RETRACTED_BY_PUBMED_COI
        assert (
            _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.SEARCH_FAILED]
            not in _INDICATORS_RETRACTED_BY_PUBMED_COI
        )

    def test_the_search_failure_is_logged_where_the_analysis_can_see_it(self, monkeypatch, caplog):
        # **Unique to the line.** `"503" in message` was satisfied by any of
        # the three endpoints this fixture fails, so suppressing the EuropePMC
        # line entirely left the test green (PR #195's review) — which is this
        # repo's standing rule about a log assertion that a neighbouring line
        # already satisfies. The API and the search URL together name one.
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            self._analyze_with(monkeypatch, _EveryRequestFails(503))
        named = [
            r
            for r in caplog.records
            if "503" in r.getMessage()
            and "EuropePMC" in r.getMessage()
            and f"{EUROPEPMC_REST_BASE}/search" in r.getMessage()
        ]
        assert named
        assert all(r.levelno == logging.WARNING for r in named)

    def test_the_analysis_reports_what_the_outage_cost_it(self, monkeypatch, caplog):
        # `_request` reports the *request* and deliberately claims no
        # consequence — it is shared by five call sites whose consequences
        # differ, and its old tail ("that component is not scored") was wrong
        # for this one, the search gating the whole full-text step rather than
        # one component. So `analyze()` states the cost, joined to the stored
        # result by `document_id` (issue #161's field).
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            self._analyze_with(monkeypatch, _CrossRefOnlyClient(), doi="10.1/x")
        named = [r for r in caplog.records if "no full-text request" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING
        assert "doc-1" in named[0].getMessage()
        assert str(SCORE_COI_DISCLOSED + SCORE_DATA_FULL_OPEN) in named[0].getMessage()

    def test_a_search_that_raised_reads_the_same(self, monkeypatch):
        # The status is about the *answer*, not about how it failed to
        # arrive: a transport failure and a 503 both leave EuropePMC having
        # said nothing.
        result = self._analyze_with(monkeypatch, _EveryRequestRaises(OSError("reset")))
        assert result.full_text_status is FullTextStatus.SEARCH_FAILED


class TestTheProseAgreesWithThePartition:
    """``is_refusal``'s docstring enumerates the non-refusal side, and it went stale.

    The partition itself is mechanised — ``test_every_status_chooses_a_side``
    makes a new member choose — but the *docstring* lists the members by hand,
    and issue #193's was added to the frozenset and not to the prose, while
    ``docs/manual/transparency.md`` was updated (PR #195's review). It is the
    docstring a downstream reads off the public API, so a list that omits a
    member is a wrong answer given confidently.

    ``TestTheAuditNetIsComplete``'s rule, applied to prose: a rule enforced by
    prose is not enforced, so enforce the prose.
    """

    def test_the_docstring_names_every_non_refusal(self):
        doc = FullTextStatus.is_refusal.__doc__ or ""
        missing = [m.name for m in _NOT_REFUSED_FULL_TEXT_STATUSES if m.name not in doc]
        assert not missing, f"is_refusal's docstring does not name {missing}"

    def test_it_names_no_refusal_among_them(self):
        # The converse, so the remedy cannot be "paste every member in": the
        # docstring's list is of the ``False`` side, and a refusal appearing
        # in it would be a wrong answer rather than a missing one.
        doc = FullTextStatus.is_refusal.__doc__ or ""
        wrongly_named = [m.name for m in _REFUSED_FULL_TEXT_STATUSES if m.name in doc]
        assert not wrongly_named, f"is_refusal's docstring lists {wrongly_named} as not refusals"


class TestEveryDroppedResponseHelperIsUnderTest:
    """The "sixth helper" claim was prose, in the comment that made it.

    ``_DROPPED_RESPONSE_HELPERS`` says in its own comment that *"a sixth
    helper added to the module and not here would be exactly as unexamined as
    these five were"* — and then leaves that to whoever adds one. Issue #193
    was one fix applied to one of five copies of a shape, so the failure mode
    is exactly a request-making helper nobody enumerated.

    The remedy is this repository's own, from ``TestTheAuditNetIsComplete``
    and ``TestOnlyAnAccumulatingElementReadsTheBuffer``: walk the module and
    fail on a method the list does not cover.
    """

    def _helpers_that_make_a_request(self) -> set[str]:
        import ast
        import inspect

        from bmlib.transparency import analyzer as analyzer_mod

        tree = ast.parse(inspect.getsource(analyzer_mod))
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        analyzer = next((c for c in classes if c.name == "TransparencyAnalyzer"), None)
        # Fails closed: "no class found" would turn this green.
        assert analyzer is not None, "TransparencyAnalyzer not found in the module source"
        found = set()
        for node in analyzer.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Attribute) and func.attr in {
                    "_request_json",
                    "_request_text",
                }:
                    found.add(node.name)
        assert found, "the walk found no request-making helper at all"
        return found

    def test_the_list_covers_every_helper_that_makes_a_request(self):
        covered = {
            "crossref": "_query_crossref",
            "europepmc": "_query_europepmc",
            "pubmed": "_query_pubmed",
            "openalex": "_query_openalex",
            "trials": "_check_trial_results",
        }
        assert set(covered) == set(_DROPPED_RESPONSE_HELPERS)
        unexamined = self._helpers_that_make_a_request() - set(covered.values())
        assert not unexamined, (
            f"{sorted(unexamined)} call `_request_json`/`_request_text` and are not in "
            "`_DROPPED_RESPONSE_HELPERS`, so none of the level, silence or "
            "return-contract tests above cover them"
        )

    def test_the_walk_would_notice_a_sixth(self):
        # The negative control the rule needs: a walk that found nothing, or
        # that could not fail, would make the assertion above vacuous.
        assert len(self._helpers_that_make_a_request()) == len(_DROPPED_RESPONSE_HELPERS)


class TestPubMedsUnusable200IsNoLongerTheQuietOne:
    """PubMed was the one endpoint of five where *"answered, and unusably"* left no line.

    ``_request_text`` returns ``None`` having already logged, but it returns
    ``""`` for a 200 carrying nothing — and ``_check_pubmed``'s falsy test
    could not tell the two apart, so an empty body was dropped in silence
    while the four JSON endpoints WARNed for the identical situation. A body
    that was not parsable XML logged at DEBUG, which is the level
    ``_request_json``'s own docstring argues "names the wrong stage".

    Not cosmetic: empty signals mean no ``<CoiStatement>``, so nothing in
    :data:`_INDICATORS_RETRACTED_BY_PUBMED_COI` is retracted, *"COI disclosure
    status unknown"* stands, and the missing-COI downgrade can fire — which is
    the sentence issue #193 justifies itself with, applied to the path it did
    not take. Issue #190 one endpoint over (PR #195's review).
    """

    def test_an_empty_body_is_reported(self, caplog):
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            signals = analyzer._check_pubmed(_AnsweringClient(200, text=""), "123")
        assert signals == _PubMedSignals()
        named = [r for r in caplog.records if "empty body" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING
        # The PMID, so the line joins to a stored result.
        assert "123" in named[0].getMessage()

    def test_a_body_that_is_not_xml_is_reported(self, caplog):
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._check_pubmed(_AnsweringClient(200, text="<not-xml"), "123")
        named = [r for r in caplog.records if "not parsable XML" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING

    def test_a_request_that_already_reported_is_not_reported_twice(self, caplog):
        # `is None` and not falsiness, for `_check_europepmc`'s reason one
        # module section over: a non-200 has been logged by `_request`
        # already, and a second line about an "empty body" would name a body
        # that never arrived.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._check_pubmed(_AnsweringClient(503), "123")
        assert not [r for r in caplog.records if "empty body" in r.getMessage()]


class TestAnUnansweredTrialIsNotAFinding:
    """Issue #194's other half — correcting the header did not make the ``bool`` honest.

    ``_check_trial_results`` returned ``bool``, and ``False`` meant both
    *"ClinicalTrials.gov says no results are posted"* and *"ClinicalTrials.gov
    did not answer"*. The caller turned the second into the first: it appended
    *"Registered trial without posted results"*, a **false claim about the
    trial**, into ``risk_indicators`` — which is persisted — and withheld
    ``SCORE_RESULTS_POSTED``.

    That is what made #194 invisible for a release. The edge 403'd every
    request, so every registered trial bmlib ever analysed was published as
    non-compliant, and two downstreams rendered it: BioMedicalNews's reading
    pane and bmlibrarian_lite's risk badge both print *"results not posted"*
    straight off ``trial_results_compliant``.

    **Correcting the ``User-Agent`` narrowed that from "always" to "whenever
    ClinicalTrials.gov does not answer" and left the conflation in place**, so
    a 404, a 403 or an unusable body still manufactured the same false
    finding. The tri-state is the fix (PR #195's review), and
    ``_INDICATOR_RESULTS_NOT_CHECKABLE`` — already defined, already used one
    branch over for a registration in another registry — is the honest line
    that was unreachable from the failure path.
    """

    def _registration(self, client, ids=("NCT00000001",)) -> _Analysis:
        analysis = _Analysis()
        TransparencyAnalyzer()._check_trial_registration(
            client,
            analysis,
            epmc=None,
            pubmed=_PubMedSignals(trial_accessions=tuple(ids)),
        )
        return analysis

    @pytest.mark.parametrize("status", [403, 404, 500])
    def test_a_refused_request_is_not_a_missing_result(self, status):
        # The three scenarios reproduced in the review, one per status. Before
        # the tri-state each of these stored the false claim.
        analysis = self._registration(_AnsweringClient(status))
        assert analysis.trial_registered is True
        assert _INDICATOR_NO_POSTED_RESULTS not in analysis.indicators
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in analysis.indicators
        assert analysis.results_compliant is False

    def test_an_answered_no_is_still_a_finding(self):
        # The other side, and the one that keeps the fix from being a blanket
        # softening: ClinicalTrials.gov *did* answer, and said no. That is a
        # real finding and must keep its indicator.
        analysis = self._registration(_AnsweringClient(200, payload={"hasResults": False}))
        assert _INDICATOR_NO_POSTED_RESULTS in analysis.indicators
        assert _INDICATOR_RESULTS_NOT_CHECKABLE not in analysis.indicators

    def test_posted_results_are_scored(self):
        # **`SCORE_RESULTS_POSTED` was awarded by no test in the suite** and,
        # because of #194, by no analysis in production either — so the branch
        # this PR exists to make reachable had nothing asserting it works.
        # A regression sending `_check_trial_results` back to a constant
        # falsehood reproduces #194 in full with the suite green.
        analysis = self._registration(_AnsweringClient(200, payload={"hasResults": True}))
        assert analysis.results_compliant is True
        assert analysis.score == SCORE_TRIAL_REGISTERED + SCORE_RESULTS_POSTED
        assert _INDICATOR_NO_POSTED_RESULTS not in analysis.indicators
        assert _INDICATOR_RESULTS_NOT_CHECKABLE not in analysis.indicators

    def test_one_refusal_does_not_hide_another_trials_posted_results(self):
        # The mix the review found: the accession that *has* results is the
        # one refused. `any()` over a `bool` could not tell that from "neither
        # has results", so the paper lost 15 points and gained a false line.
        client = _PerAccessionClient(
            {"NCT00000001": (403, None), "NCT00000002": (200, {"hasResults": False})}
        )
        analysis = self._registration(client, ids=("NCT00000001", "NCT00000002"))
        # One accession answered, and its answer was "no" — so the finding
        # stands, but it is now a finding about the trial that answered.
        assert _INDICATOR_NO_POSTED_RESULTS in analysis.indicators

    def test_none_answering_across_several_accessions_is_not_checkable(self):
        client = _PerAccessionClient({"NCT00000001": (403, None), "NCT00000002": (503, None)})
        analysis = self._registration(client, ids=("NCT00000001", "NCT00000002"))
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in analysis.indicators
        assert _INDICATOR_NO_POSTED_RESULTS not in analysis.indicators

    def test_the_search_stops_at_the_first_posted_result(self):
        # The `any()`'s short-circuit, kept: a posted result is final, so the
        # remaining accessions are not requested. Only that answer may stop
        # the loop — a `None` must not, since a later accession may be the one
        # that answers.
        client = _PerAccessionClient(
            {"NCT00000001": (200, {"hasResults": True}), "NCT00000002": (200, {"hasResults": True})}
        )
        self._registration(client, ids=("NCT00000001", "NCT00000002"))
        assert client.asked == ["NCT00000001"]

    def test_a_refusal_does_not_stop_the_loop(self):
        client = _PerAccessionClient(
            {"NCT00000001": (403, None), "NCT00000002": (200, {"hasResults": True})}
        )
        analysis = self._registration(client, ids=("NCT00000001", "NCT00000002"))
        assert client.asked == ["NCT00000001", "NCT00000002"]
        assert analysis.results_compliant is True

    def test_the_refusal_is_not_silent(self, caplog):
        # It reaches a stored result as "could not be checked", and it reaches
        # an operator as a line naming the accession — issue #193's rule, at
        # the endpoint issue #194 was about.
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            self._registration(_AnsweringClient(403))
        named = [
            r for r in caplog.records if "403" in r.getMessage() and "NCT00000001" in r.getMessage()
        ]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING


class _PerAccessionClient:
    """Answers ClinicalTrials.gov per accession, recording the order asked."""

    def __init__(self, table: dict[str, tuple[int, object]]):
        self.table = table
        self.asked: list[str] = []

    def get(self, url, **kwargs):
        for nct, (status, payload) in self.table.items():
            if nct in url:
                self.asked.append(nct)
                return _VerbatimResponse(status_code=status, payload=payload)
        raise AssertionError(f"unexpected url {url!r}")


class _EveryRequestFails:
    """A client whose every request answers one non-200 status."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        return _FakeResponse(status_code=self.status_code)


class _EveryRequestRaises:
    """A client whose every request raises."""

    def __init__(self, error: Exception):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        raise self.error


class _CrossRefOnlyClient:
    """CrossRef answers; every other API is down.

    The shape issue #193 was measured with, and the one that produces a
    scored, non-UNKNOWN verdict out of a failure — ``_api_reachable`` is set
    by the one API that answered, so the analysis completes at
    ``SCORE_FUNDER_INFO`` alone and reaches ``HIGH``.

    ``urls`` records every request, which is how issue #202 is asserted: the
    duplicate search it is about is invisible in the *result*, and visible
    only in what went out.
    """

    def __init__(self):
        self.urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        if "crossref" in url:
            return _FakeResponse(
                status_code=200,
                json_data={"message": {"funder": [{"name": "Some University"}]}},
            )
        return _FakeResponse(status_code=503)


class TestAQuietStatusIsOneADrawEarned:
    """The mechanism, and the claim that nothing has earned it yet.

    ``_request`` takes a per-endpoint set of statuses that log at DEBUG rather
    than WARNING. All five sets are empty, and that is a *measurement* — 366
    probes over 240 records on 2026-09-06 returned 0 non-200s at any of the
    five endpoints — not a placeholder. Both halves need pinning: an empty set
    that no test exercises is indistinguishable from a mechanism that does not
    work, and an emptiness nothing asserts is one a later session fills in
    without a draw.
    """

    _SETS = {
        "CrossRef": _CROSSREF_ORDINARY_STATUSES,
        "EuropePMC search": _EUROPEPMC_SEARCH_ORDINARY_STATUSES,
        "PubMed": _PUBMED_ORDINARY_STATUSES,
        "OpenAlex": _OPENALEX_ORDINARY_STATUSES,
        "ClinicalTrials.gov": _CLINICALTRIALS_ORDINARY_STATUSES,
    }

    def test_a_status_the_draw_measured_ordinary_would_be_quiet(self, caplog):
        # The mechanism, exercised directly rather than through an endpoint,
        # because no endpoint names a status today. Without this the five
        # empty sets would be untested wiring, and the first session to
        # measure one ordinary would be the first to find out whether it
        # works.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._request(
                _AnsweringClient(404),
                "https://example.org/x",
                api="Somewhere",
                subject="x",
                quiet_statuses=frozenset({404}),
            )
        named = [r for r in caplog.records if "404" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.DEBUG

    def test_a_status_beside_a_quiet_one_still_warns(self, caplog):
        # The branch must be no wider than the draw — issue #191 in one
        # assertion. A set naming 404 says nothing about 503.
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._request(
                _AnsweringClient(503),
                "https://example.org/x",
                api="Somewhere",
                subject="x",
                quiet_statuses=frozenset({404}),
            )
        named = [r for r in caplog.records if "503" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING

    @pytest.mark.parametrize("endpoint", sorted(_SETS))
    def test_no_endpoint_claims_an_ordinary_status_today(self, endpoint):
        # The measured claim, pinned so that populating one of these needs a
        # fresh run of `scripts/sample_api_failures.py` and a red test to
        # explain — not a judgement call in a review. The draw's own numbers
        # are in the comment above the sets; what they support is an upper
        # bound (2.1%–6.8% depending on the endpoint), and a bound is not a
        # licence to call any particular status ordinary.
        assert self._SETS[endpoint] == frozenset()

    #: Which constant each helper must be wired to, and how to call it.
    _WIRING = (
        ("crossref", "_CROSSREF_ORDINARY_STATUSES"),
        ("europepmc", "_EUROPEPMC_SEARCH_ORDINARY_STATUSES"),
        ("pubmed", "_PUBMED_ORDINARY_STATUSES"),
        ("openalex", "_OPENALEX_ORDINARY_STATUSES"),
        ("trials", "_CLINICALTRIALS_ORDINARY_STATUSES"),
    )

    @pytest.mark.parametrize(("helper", "constant"), _WIRING)
    def test_each_helper_is_wired_to_its_own_constant(self, helper, constant, monkeypatch, caplog):
        # **The mechanism above and the constants beside it were pinned; the
        # wire between them was not** (PR #195's review). Deleting all five
        # `quiet_statuses=` kwargs passed the entire suite, because every set
        # is empty and an empty set is also the parameter's default — so the
        # day a draw fills one in, the endpoint would keep WARNING while five
        # documents said DEBUG. That is issue #191's silence with the fix
        # installed.
        #
        # A status no endpoint will ever measure ordinary (418) keeps this
        # from ever agreeing with a real future set by luck.
        from bmlib.transparency import analyzer as analyzer_mod

        monkeypatch.setattr(analyzer_mod, constant, frozenset({418}))
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            _call_helper(analyzer, helper, _AnsweringClient(418))
        named = [r for r in caplog.records if "418" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.DEBUG

    @pytest.mark.parametrize(("helper", "constant"), _WIRING)
    def test_no_helper_reads_another_endpoints_constant(
        self, helper, constant, monkeypatch, caplog
    ):
        # The other half: a call site handed the *wrong* one of five
        # identically-typed constants is undetectable while all five are
        # empty. Fill every set except this helper's, and it must still warn.
        from bmlib.transparency import analyzer as analyzer_mod

        for _, other in self._WIRING:
            if other != constant:
                monkeypatch.setattr(analyzer_mod, other, frozenset({418}))
        analyzer = TransparencyAnalyzer()
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            _call_helper(analyzer, helper, _AnsweringClient(418))
        named = [r for r in caplog.records if "418" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING

    def test_every_request_is_paced(self, monkeypatch):
        # `_rate_limit` moved from five call sites into one, and nothing
        # noticed when it was deleted — the whole suite passed, 12.1s faster
        # (PR #195's review). It is bmlib's only politeness control across
        # five APIs, and this repository has already shipped one sampler run
        # that hit a host 300 times in 300 seconds.
        analyzer = TransparencyAnalyzer()
        calls: list[int] = []
        monkeypatch.setattr(TransparencyAnalyzer, "_rate_limit", lambda self: calls.append(1))
        analyzer._request(_AnsweringClient(200), "https://example.org/x", api="A", subject="x")
        assert len(calls) == 1

    def test_per_request_headers_reach_the_client(self):
        # `headers=headers` in `_request`'s `client.get` was deletable with
        # the whole suite green, which would silently drop the
        # `Accept: application/json` CrossRef and OpenAlex are sent. The
        # `params` half was already pinned; this is its twin.
        seen: dict = {}

        class _Client:
            def get(self, url, **kwargs):
                seen.update(kwargs)
                return _VerbatimResponse(status_code=200, payload={})

        TransparencyAnalyzer()._query_crossref(_Client(), "10.1/x")
        assert seen["headers"] == {"Accept": "application/json"}

    def test_the_log_line_names_the_url(self):
        # Issue #184 lived a whole release inside a silence, and the URL is
        # what named it. Asserted on the message rather than on the call, so
        # a line that stops interpolating it reddens.
        analyzer = TransparencyAnalyzer()
        client = _AnsweringClient(500)
        import logging as _logging

        records: list[_logging.LogRecord] = []
        handler = _logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger_ = _logging.getLogger("bmlib.transparency.analyzer")
        logger_.addHandler(handler)
        try:
            analyzer._request(client, "https://example.org/thing", api="X", subject="s")
        finally:
            logger_.removeHandler(handler)
        assert any("https://example.org/thing" in r.getMessage() for r in records)


class TestTheTrialIdScanReadsTheRecordAnalyzeAlreadyFetched:
    """Issue #202 — a failed EuropePMC search was re-issued, against a docstring.

    ``_find_trial_ids`` documented that it *"reuses the EuropePMC record
    already fetched by ``analyze``, falling back to a fresh query only if it
    was not supplied, so the same search is not issued twice per document"* —
    and decided that with ``if data is None``, which is exactly what a
    **failed** search returns. So during an outage the identical failing
    search went out twice, and since PR #195 gave the failure a log line, an
    operator counting Europe PMC failures double-counted every document.

    The fix is not a sentinel distinguishing the two ``None``s: it is that the
    scan has no business making a request at all. ``analyze()`` has already
    fetched the record and is the only caller, so the parameter is mandatory
    and the promise is structural rather than documented. A trial id scraped
    out of an abstract bmlib never received is not a thing that can happen.
    """

    def test_the_scan_makes_no_request_of_its_own(self):
        # Structural, and the whole of the fix: no client to make one with.
        # A record that never arrived yields nothing, and quietly — there is
        # no second request to fail and no second line to log.
        assert _find_trial_ids(None) == []
        assert _find_trial_ids(_epmc_record("ClinicalTrials.gov number, NCT01206062.")) == [
            "NCT01206062"
        ]

    def test_a_failed_search_is_not_issued_twice(self, monkeypatch):
        # The measurement in the issue, asserted: one document, one search.
        # `_CrossRefOnlyClient` is the partial outage — CrossRef answers, so
        # the analysis completes and is scored, which is the case where the
        # duplicate was reachable at all.
        client = _CrossRefOnlyClient()
        _install_fake_client(monkeypatch, client)
        TransparencyAnalyzer().analyze("doc-1", doi="10.1/x")
        searches = [u for u in client.urls if u.startswith(f"{EUROPEPMC_REST_BASE}/search")]
        assert len(searches) == 1

    def test_the_outage_is_reported_once_per_document(self, monkeypatch, caplog):
        # The cost the issue ranks second, and the one a human sees: two
        # identical WARNINGs for one document, so an operator counting
        # EuropePMC failures counts each document twice.
        client = _CrossRefOnlyClient()
        _install_fake_client(monkeypatch, client)
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            TransparencyAnalyzer().analyze("doc-1", doi="10.1/x")
        named = [
            r
            for r in caplog.records
            if "EuropePMC" in r.getMessage() and f"{EUROPEPMC_REST_BASE}/search" in r.getMessage()
        ]
        assert len(named) == 1

    def test_a_pubmed_accession_is_still_checked_when_the_search_failed(self, monkeypatch):
        # The half that must **not** change. PubMed's `<DataBankList>`
        # accession does not come from the EuropePMC record, so a document
        # whose search failed still has a followable registration — and
        # skipping the abstract heuristic must not skip the results check
        # with it.
        client = _RecordingClient(
            epmc=None,
            pubmed=_pubmed_xml(databanks=(("ClinicalTrials.gov", ("NCT01206062",)),)),
            trial_has_results=True,
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="123")
        assert result.full_text_status is FullTextStatus.SEARCH_FAILED
        assert result.trial_registered is True
        assert result.trial_results_compliant is True


class TestAProvenanceLineIsNotACoiClaim:
    """Issue #203 — one string carried two claims, and the retraction took both.

    Three branches wrote a *"COI disclosure status unknown (…)"* line whose
    parenthetical said what became of the full text, and all three sat in
    ``_INDICATORS_RETRACTED_BY_PUBMED_COI``. A PubMed ``<CoiStatement>``
    refutes the COI half and says nothing whatever about the other, so a
    result could reach ``HIGH`` with a tier downgrade whose only
    human-readable line was a COI **success** — issue #193's own complaint,
    reintroduced through the retraction set.

    The issue names the search-failure branch. The other two have the same
    shape and the same consequence, which is this repo's standing rule: the
    guard written on one branch is the guard the others need. So the COI claim
    is one line for all three, and what became of the full text is a
    **provenance** line keyed on :class:`FullTextStatus` — appended once,
    after every step has run, which puts it structurally beyond the retraction
    rather than merely absent from a set.
    """

    def test_every_status_says_what_happened(self):
        # `TestTheAuditNetIsComplete`'s rule, one module over: a member added
        # later must choose, or the prose silently stops describing the enum.
        # An exclusion is *named*, not defaulted.
        covered = set(_FULL_TEXT_PROVENANCE_INDICATORS) | _STATUSES_WITH_NO_PROVENANCE_LINE
        assert covered == set(FullTextStatus)
        assert not set(_FULL_TEXT_PROVENANCE_INDICATORS) & _STATUSES_WITH_NO_PROVENANCE_LINE

    @pytest.mark.parametrize("status", list(FullTextStatus))
    def test_every_status_reaches_the_result_as_its_own_line(self, status):
        # The partition test above compares two collections and says nothing
        # about the **append**, so suppressing the line for four of the eight
        # members left the whole suite green, as did appending it twice (PR
        # #205's review). Only five members had an `analyze()`-level assertion,
        # and the four without included issue #191's 404 — the commonest
        # non-200 the sampler measured, 81 of 81.
        #
        # Exact list equality rather than membership, since that is what makes
        # one assertion cover both mutants at once.
        analysis = _Analysis(full_text_status=status)
        _note_full_text_provenance(analysis)
        expected = _FULL_TEXT_PROVENANCE_INDICATORS.get(status)
        assert analysis.indicators == ([] if expected is None else [expected])

    def test_a_status_in_neither_collection_raises(self, monkeypatch):
        # The fail-closed half, which is what makes the exclusion set
        # load-bearing at runtime instead of documentation. A `.get()` here
        # dropped the line in silence, and what it drops is invisible — the
        # result simply carries one line fewer — so the red test above was the
        # only protection. Deleting an entry stands in for the real hazard,
        # a member added to the enum and to neither collection.
        monkeypatch.delitem(_FULL_TEXT_PROVENANCE_INDICATORS, FullTextStatus.NOT_SERVED)
        analysis = _Analysis(full_text_status=FullTextStatus.NOT_SERVED)
        with pytest.raises(KeyError):
            _note_full_text_provenance(analysis)

    def test_no_two_statuses_share_a_line(self):
        # A copy-paste makes two outcomes indistinguishable in the one half a
        # human reads, which is the defect this whole family is about.
        lines = list(_FULL_TEXT_PROVENANCE_INDICATORS.values())
        assert len(set(lines)) == len(lines)

    def test_no_provenance_line_is_retractable(self):
        # The rule itself, mechanised. Membership of the retraction set is
        # what took the provenance away, so a provenance line landing in it
        # is issue #203 verbatim — and it would land there silently, since
        # nothing else compares the two collections.
        assert not (
            set(_FULL_TEXT_PROVENANCE_INDICATORS.values()) & _INDICATORS_RETRACTED_BY_PUBMED_COI
        )

    def test_a_pubmed_statement_does_not_retract_the_outage_line(self, monkeypatch):
        # The case the issue ran: EuropePMC down, PubMed serving a
        # <CoiStatement>. The COI claim goes, the outage stays.
        client = _RecordingClient(epmc=None, pubmed=_pubmed_xml(coi="Dr X consults for Y."))
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="123")
        assert result.full_text_status is FullTextStatus.SEARCH_FAILED
        assert result.coi_disclosed is True
        assert _INDICATOR_COI_IN_PUBMED in result.risk_indicators
        assert _INDICATOR_COI_UNKNOWN not in result.risk_indicators
        assert _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.SEARCH_FAILED] in (
            result.risk_indicators
        )

    def test_a_pubmed_statement_does_not_retract_the_refusal_line(self, monkeypatch):
        # The second branch, which the issue does not name and which has the
        # same consequence: a document EuropePMC *served* and bmlib declined
        # to scan, whose only record of that was the retracted line.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True),
            full_text="<article><body><p>Ours.</p><sub-article><p>Theirs.</p></article>",
            pubmed=_pubmed_xml(coi="Dr X consults for Y."),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.full_text_status is FullTextStatus.UNCLOSED_REGION
        assert _INDICATOR_COI_IN_PUBMED in result.risk_indicators
        assert _INDICATOR_COI_UNKNOWN not in result.risk_indicators
        assert _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.UNCLOSED_REGION] in (
            result.risk_indicators
        )

    def test_a_pubmed_statement_does_not_retract_the_unavailable_line(self, monkeypatch):
        # The third branch — the commonest of the three by a wide margin,
        # every closed-access paper reaching it.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="N"),
            pubmed=_pubmed_xml(coi="Dr X consults for Y."),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.full_text_status is FullTextStatus.NOT_ATTEMPTED
        assert _INDICATOR_COI_IN_PUBMED in result.risk_indicators
        assert _INDICATOR_COI_UNKNOWN not in result.risk_indicators
        assert _FULL_TEXT_PROVENANCE_INDICATORS[FullTextStatus.NOT_ATTEMPTED] in (
            result.risk_indicators
        )

    def test_a_scanned_document_gets_no_provenance_line(self, monkeypatch):
        # The negative control the named exclusion needs: `ANALYZED` is
        # excluded because there is nothing to explain, so a line appearing
        # here would be noise on every successful analysis — and a test that
        # only ever asserts presence cannot tell an unconditional append from
        # a conditional one.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1", in_epmc="Y", addressable=True),
            full_text="<article><body><p>Competing interests: none declared.</p></body></article>",
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.full_text_status is FullTextStatus.ANALYZED
        assert not [
            line
            for line in result.risk_indicators
            if line in set(_FULL_TEXT_PROVENANCE_INDICATORS.values())
        ]

    def test_the_coi_claim_is_one_line_for_all_three(self, monkeypatch):
        # Three parentheticals became one claim, so the retraction set holds
        # what it says it holds: COI claims. The distinction they carried is
        # not lost — it moved to the provenance line, where it is not a COI
        # claim and cannot be retracted.
        client = _RecordingClient(epmc=_epmc_payload(pmid="1", in_epmc="N"))
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert _INDICATOR_COI_UNKNOWN in result.risk_indicators
        assert result.coi_disclosed is None


class TestTrialResultsStatus:
    """Issue #198 — a bare ``bool`` answered three different questions.

    ``trial_results_compliant`` is ``False`` for a trial ClinicalTrials.gov
    said has no posted results, for one nobody managed to ask about, for a
    registration in a registry that has no answer to give, and for a paper
    with no registered trial at all. ``risk_indicators`` distinguishes the
    middle two from the first, and both downstreams render the flag rather
    than the indicator — so the field that is easiest to read is the one that
    cannot be read correctly.

    :class:`FullTextStatus`'s argument (issue #161) one endpoint over, and
    held to the same rules: a grouping property so no call site enumerates
    members, a mechanised partition so one added later must choose a side,
    serialisation by value, and ``None`` meaning *not recorded* rather than
    any determinate outcome.
    """

    def test_every_status_chooses_a_side(self):
        # The partition, exactly as `_REFUSED_FULL_TEXT_STATUSES` is pinned:
        # a member omitted from both sets is a red test rather than a silent
        # default, and the silent default here runs the wrong way — an
        # unlisted member would read as *not answered*, which is the safe
        # side for a reader and the wrong side for a finding.
        assert _ANSWERED_TRIAL_RESULTS_STATUSES | _UNANSWERED_TRIAL_RESULTS_STATUSES == set(
            TrialResultsStatus
        )
        assert not _ANSWERED_TRIAL_RESULTS_STATUSES & _UNANSWERED_TRIAL_RESULTS_STATUSES

    def test_the_docstring_names_every_answered_member(self):
        # `TestTheProseAgreesWithThePartition`'s guard, brought across with the
        # precedent it copies (PR #205's review). `is_answered`'s docstring
        # enumerates the `True` side by hand, and that is the read a downstream
        # gets off the public API — the same list that went stale for
        # `is_refusal` when issue #193 added a member to the frozenset and not
        # to the prose. A sixth member joining the answered set is otherwise
        # silent: the partition test still passes, and so does the enumeration
        # below, which names only the members it knows.
        doc = TrialResultsStatus.is_answered.__doc__ or ""
        missing = [m.name for m in _ANSWERED_TRIAL_RESULTS_STATUSES if m.name not in doc]
        assert not missing, f"is_answered's docstring does not name {missing}"

    def test_the_docstring_names_no_unanswered_member_among_them(self):
        # The converse, so the remedy cannot be "paste every member in".
        doc = TrialResultsStatus.is_answered.__doc__ or ""
        wrongly_named = [m.name for m in _UNANSWERED_TRIAL_RESULTS_STATUSES if m.name in doc]
        assert not wrongly_named, f"is_answered's docstring lists {wrongly_named} as answered"

    def test_only_an_answer_counts_as_answered(self):
        assert TrialResultsStatus.POSTED.is_answered is True
        assert TrialResultsStatus.NOT_POSTED.is_answered is True
        assert TrialResultsStatus.REQUEST_FAILED.is_answered is False
        assert TrialResultsStatus.NOT_CHECKABLE.is_answered is False
        assert TrialResultsStatus.NOT_REGISTERED.is_answered is False

    def test_results_posted_is_recorded(self, monkeypatch):
        client = _RecordingClient(
            epmc=_epmc_payload(abstract="ClinicalTrials.gov number, NCT01206062.", pmid="1"),
            trial_has_results=True,
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.trial_results_status is TrialResultsStatus.POSTED
        assert result.trial_results_compliant is True

    def test_an_answered_no_is_distinguishable_from_an_unanswered_one(self, monkeypatch):
        # The distinction the whole issue is about, asserted as a pair rather
        # than one at a time: both store `trial_results_compliant=False`, and
        # a downstream rendering that flag says "results not posted" for both.
        epmc = _epmc_payload(abstract="ClinicalTrials.gov number, NCT01206062.", pmid="1")
        answered = _RecordingClient(epmc=epmc, trial_has_results=False)
        _install_fake_client(monkeypatch, answered)
        said_no = TransparencyAnalyzer().analyze("doc-1", pmid="1")

        refused = _RecordingClient(epmc=epmc, trial_status_code=403)
        _install_fake_client(monkeypatch, refused)
        never_answered = TransparencyAnalyzer().analyze("doc-1", pmid="1")

        assert said_no.trial_results_compliant is never_answered.trial_results_compliant is False
        assert said_no.trial_results_status is TrialResultsStatus.NOT_POSTED
        assert never_answered.trial_results_status is TrialResultsStatus.REQUEST_FAILED

    def test_a_registry_with_no_answer_to_give_is_its_own_member(self, monkeypatch):
        # Registration established in another registry: ClinicalTrials.gov was
        # never asked and could not have answered, so *"would re-running
        # change this?"* is `no` — which is the question that separates this
        # from `REQUEST_FAILED`, and the reason the indicator they share is
        # not enough on its own.
        client = _RecordingClient(
            epmc=_epmc_payload(pmid="1"),
            pubmed=_pubmed_xml(databanks=(("ISRCTN", ("ISRCTN12345678",)),)),
        )
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.trial_registered is True
        assert result.trial_results_status is TrialResultsStatus.NOT_CHECKABLE
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in result.risk_indicators

    def test_a_paper_with_no_trial_says_there_was_nothing_to_ask(self, monkeypatch):
        client = _RecordingClient(epmc=_epmc_payload(pmid="1"))
        _install_fake_client(monkeypatch, client)
        result = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert result.trial_registered is False
        assert result.trial_results_status is TrialResultsStatus.NOT_REGISTERED

    def test_the_flag_and_the_status_cannot_disagree(self):
        # The pair `full_text_status`/`full_text_analyzed` is held to, for the
        # same reason: the flag is the compatibility field, so a stored result
        # where the two disagree is uninterpretable whichever one is believed.
        #
        # **Both directions**, as `test_analyzed_and_the_flag_must_agree` does.
        # Asserted from the `POSTED` side alone, `!=` weakens to `and not` with
        # the whole suite green (PR #205's review) — and the direction that
        # drops is a non-`POSTED` status beside a `True` flag, which is exactly
        # the shape an in-place upgrade of a legacy row produces.
        with pytest.raises(ValueError, match="if and only if"):
            TransparencyResult(
                document_id="d",
                transparency_score=50,
                risk_level=TransparencyRisk.MEDIUM,
                trial_results_compliant=False,
                trial_results_status=TrialResultsStatus.POSTED,
            )
        with pytest.raises(ValueError, match="if and only if"):
            TransparencyResult(
                document_id="d",
                transparency_score=50,
                risk_level=TransparencyRisk.MEDIUM,
                trial_results_compliant=True,
                trial_results_status=TrialResultsStatus.NOT_POSTED,
            )

    def test_it_defaults_to_not_recorded(self):
        # `None` is the default, so a result this version does not fill in is
        # a legacy row's equal — which is what the "every path records it"
        # test below exists to make unreachable in practice.
        assert TransparencyResult("d", 50, TransparencyRisk.MEDIUM).trial_results_status is None

    def test_a_result_that_never_recorded_it_loads_as_not_recorded(self):
        # `None` is *not recorded*, never `NOT_REGISTERED`: a result persisted
        # before the field existed may perfectly well carry
        # `trial_results_compliant=True`, and reading that back as a
        # determinate "no registration" would be a worse answer than admitting
        # the field was not written.
        legacy = {
            "document_id": "d",
            "transparency_score": 50,
            "risk_level": "medium",
            "trial_registered": True,
            "trial_results_compliant": True,
        }
        assert TransparencyResult.from_dict(legacy).trial_results_status is None

    def test_not_registered_round_trips_and_does_not_collapse_to_none(self):
        # `full_text_status`'s own rule, and it matters more here: this is the
        # default, the value all three early returns write, and the value every
        # paper without a trial carries. Pinned only from the `None` side, a
        # `to_dict` writing `None` for it is invisible and undoes the whole
        # *"every path this version writes records it"* guarantee at
        # serialisation — measured green against the suite (PR #205's review).
        result = TransparencyResult(
            "d",
            50,
            TransparencyRisk.MEDIUM,
            trial_results_status=TrialResultsStatus.NOT_REGISTERED,
        )
        payload = result.to_dict()
        assert payload["trial_results_status"] == "not_registered"
        assert TransparencyResult.from_dict(payload).trial_results_status is (
            TrialResultsStatus.NOT_REGISTERED
        )

    def test_it_round_trips_by_value(self):
        result = TransparencyResult(
            document_id="d",
            transparency_score=50,
            risk_level=TransparencyRisk.MEDIUM,
            trial_registered=True,
            trial_results_compliant=False,
            trial_results_status=TrialResultsStatus.REQUEST_FAILED,
        )
        payload = result.to_dict()
        assert payload["trial_results_status"] == "request_failed"
        assert TransparencyResult.from_dict(payload).trial_results_status is (
            TrialResultsStatus.REQUEST_FAILED
        )

    def test_a_member_this_version_does_not_know_raises(self):
        # `unknown_reason` and `full_text_status` both refuse rather than
        # loading `None`: a member from a later bmlib is a result this one
        # cannot interpret, and `None` would report it as never recorded.
        #
        # **`match=` is load-bearing**, exactly as it is for `full_text_status`
        # one class up. Without it a `from_dict` mapping an unrecognised value
        # onto `POSTED` still passes: the `ValueError` then comes from
        # `__post_init__`, the default `trial_results_compliant=False`
        # contradicting it, and the test reports an enum lookup it never
        # reached (measured green, PR #205's review).
        with pytest.raises(ValueError, match="witnessed_by_a_notary"):
            TransparencyResult.from_dict(
                {
                    "document_id": "d",
                    "transparency_score": 50,
                    "risk_level": "medium",
                    "trial_results_status": "witnessed_by_a_notary",
                }
            )

    def test_every_path_this_version_writes_records_it(self, monkeypatch):
        # The rule `full_text_status` established: if any current path left it
        # `None`, a current row would be indistinguishable from a legacy one
        # and the *not recorded* reading would be worthless.
        #
        # **Three early returns, and this covered two** (PR #205's review):
        # dropping the field from the disabled return was green. Both fields
        # are asserted on each, since `full_text_status` had the same hole on
        # the disabled path — issue #161's own rule, unpinned at one of the
        # three returns it names.
        disabled = TransparencyAnalyzer(settings=TransparencySettings(enabled=False)).analyze(
            "doc-1", pmid="1"
        )
        assert disabled.unknown_reason is TransparencyUnknownReason.DISABLED
        assert disabled.trial_results_status is TrialResultsStatus.NOT_REGISTERED
        assert disabled.full_text_status is FullTextStatus.NOT_ATTEMPTED

        no_identifier = TransparencyAnalyzer().analyze("doc-1")
        assert no_identifier.unknown_reason is TransparencyUnknownReason.NO_IDENTIFIER
        assert no_identifier.trial_results_status is TrialResultsStatus.NOT_REGISTERED
        assert no_identifier.full_text_status is FullTextStatus.NOT_ATTEMPTED
        client = _EveryRequestFails(503)
        _install_fake_client(monkeypatch, client)
        outage = TransparencyAnalyzer().analyze("doc-1", pmid="1")
        assert outage.unknown_reason is TransparencyUnknownReason.UNREACHABLE
        assert outage.trial_results_status is not None


class TestTheManualListsEveryExportedName:
    """The manual's import block declared itself complete and was not checked.

    It read *"The list of six names below is the complete
    ``bmlib.transparency.__all__``"* over a block of seven, while ``__all__``
    held eight: issue #198's :class:`TrialResultsStatus` was exported and never
    added, so a downstream copying that block does not get the enum the whole
    change is about (PR #205's review). The count had been stale since
    :class:`FullTextStatus` as well, which is what makes it a drift rather than
    one slip — and the manual is the copy a downstream reads, so the guard
    written on the source is the guard the manual needs
    (``TestTheStatedCountsAreWhatTheCorpusHolds``' rule one file over).

    It **fails closed**: a block that has moved or been reformatted out of
    recognition raises rather than passing over nothing.
    """

    MANUAL = Path(__file__).resolve().parents[1] / "docs" / "manual" / "transparency.md"

    #: The canonical block, scoped to the `## Imports` section. The manual also
    #: shows deliberately partial imports in usage snippets — one names three
    #: of the eight — so an unscoped search either finds two blocks or, worse,
    #: checks the wrong one.
    SECTION_RE = re.compile(r"^## Imports$(?P<body>.*?)^---$", re.MULTILINE | re.DOTALL)
    BLOCK_RE = re.compile(
        r"^from bmlib\.transparency import \(\n(?P<names>.*?)^\)$",
        re.MULTILINE | re.DOTALL,
    )

    def _listed_names(self) -> set[str]:
        section = self.SECTION_RE.search(self.MANUAL.read_text(encoding="utf-8"))
        if section is None:
            raise AssertionError(f"no `## Imports` section found in {self.MANUAL.name}")
        blocks = self.BLOCK_RE.findall(section["body"])
        if len(blocks) != 1:
            raise AssertionError(
                f"expected exactly one parenthesised `from bmlib.transparency import` "
                f"block in {self.MANUAL.name}'s `## Imports` section, found {len(blocks)}"
            )
        names = {
            stripped
            for line in blocks[0].splitlines()
            if (stripped := line.split("#", 1)[0].strip().rstrip(","))
        }
        if not names:
            raise AssertionError("the manual's import block lists no names")
        return names

    def test_the_manual_lists_every_exported_name(self):
        from bmlib.transparency import __all__ as exported

        listed = self._listed_names()
        assert listed == set(exported), (
            f"manual lists {sorted(listed)}; __all__ is {sorted(exported)}"
        )

    def test_the_manual_states_no_count_of_them(self):
        # The count is what went stale, twice, while the list beside it was
        # only one name short. A prose number over a list a test already checks
        # is a second thing to keep in step for no gain — the ordinal-free rule
        # `_NOT_REFUSED_FULL_TEXT_STATUSES` states for enum members, applied to
        # the manual.
        text = self.MANUAL.read_text(encoding="utf-8")
        stale = re.search(r"list of \w+ names below is the complete", text)
        assert stale is None, f"the manual states a count again: {stale.group(0)!r}"


class _MalformedBodyClient:
    """A fake client that serves one endpoint a hostile body and the rest good ones.

    Every JSON endpoint answers HTTP 200 throughout, because a non-200 is
    already reported and already returns ``None`` — the whole subject here is
    a remote that *answered* and sent a shape the reader did not expect.
    ``requested`` is what makes the net non-vacuous: a hostile body served at
    an endpoint ``analyze()`` never asks for asserts nothing at all.
    """

    #: A well-formed payload per endpoint, so exactly one thing is wrong at a
    #: time and the analysis has something left to score.
    GOOD = {
        "crossref": {"message": {"funder": [{"name": "Acme Pharmaceuticals Inc"}]}},
        "epmc": {
            "resultList": {
                "result": [
                    {
                        "id": "PMC123",
                        "pmcid": "PMC123",
                        # Distinct from the `pmid="1"` the other column
                        # supplies, so the anti-vacuity test below can tell
                        # "derived from the record" from "passed by the
                        # caller" — the whole difference the axis exists for.
                        "pmid": "9999001",
                        "source": "PMC",
                        "inEPMC": "N",
                        "abstractText": ("Trial registration: NCT01234567 (ClinicalTrials.gov)."),
                        "isOpenAccess": "Y",
                    }
                ]
            }
        },
        "openalex": {"open_access": {"is_oa": True}, "cited_by_count": 3},
        "trial": {"hasResults": True},
    }

    def __init__(self, endpoint: str | None = None, body=None):
        self.bodies = dict(self.GOOD)
        if endpoint is not None:
            self.bodies[endpoint] = body
        self.requested: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        for name, fragment in (
            ("crossref", "crossref"),
            ("epmc", "europepmc"),
            ("openalex", "openalex"),
            ("trial", "clinicaltrials"),
        ):
            if fragment in url and not url.endswith("/fullTextXML"):
                self.requested.append(name)
                return _FakeResponse(status_code=200, json_data=self.bodies[name])
        # Full text and PubMed are read as text, not JSON, and are a different
        # layer; they 404 so this net stays about the JSON readers.
        return _FakeResponse(status_code=404)


#: Bodies a remote can legally send at HTTP 200 that are not the shape the
#: reader assumes. Two kinds, and they are guarded at two different places:
#: the body itself not being a JSON object, which `_request_json` refuses; and
#: a *value inside* an object having the wrong type, which no boundary guard
#: can reach because the object did arrive.
_NON_OBJECT_BODIES = [
    ("list-of-one", [{"a": 1}]),
    ("empty-list", []),
    ("string", "nope"),
    ("number", 7),
    ("true", True),
    ("null", None),
]

_WRONG_TYPED_VALUES = [
    ("crossref", "message-is-list", {"message": []}),
    ("crossref", "funder-is-object", {"message": {"funder": {"name": "x"}}}),
    ("crossref", "funder-item-is-string", {"message": {"funder": ["Acme"]}}),
    ("crossref", "funder-name-is-object", {"message": {"funder": [{"name": {"a": 1}}]}}),
    ("epmc", "resultList-is-list", {"resultList": []}),
    ("epmc", "result-is-object", {"resultList": {"result": {"id": "PMC1"}}}),
    ("epmc", "record-is-string", {"resultList": {"result": ["PMC1"]}}),
    ("epmc", "result-is-number", {"resultList": {"result": 7}}),
    (
        "epmc",
        "abstractText-is-object",
        {"resultList": {"result": [{"id": "PMC1", "abstractText": {"x": 1}}]}},
    ),
    (
        "epmc",
        "inEPMC-is-object",
        {"resultList": {"result": [{"id": "PMC1", "inEPMC": {"x": 1}}]}},
    ),
    ("openalex", "open_access-is-list", {"open_access": [], "cited_by_count": 1}),
    (
        "openalex",
        "cited_by_count-is-string",
        {"open_access": {"is_oa": True}, "cited_by_count": "3"},
    ),
    (
        "openalex",
        "cited_by_count-is-null",
        {"open_access": {"is_oa": True}, "cited_by_count": None},
    ),
    ("trial", "hasResults-is-string", {"hasResults": "yes"}),
    # **Chosen so the row can fail.** `"yes"` above is the one string whose
    # truthiness coincides with the right answer, so it could never separate
    # `bool()` from a real read; `"no"` is the same shape stating the opposite
    # (PR #208's review). Kept as well as, not instead of — the pair is what
    # shows the row is about the type and not about the word.
    ("trial", "hasResults-is-the-string-no", {"hasResults": "no"}),
    ("trial", "hasResults-is-object", {"hasResults": {}}),
    ("openalex", "is_oa-is-the-string-false", {"open_access": {"is_oa": "false"}}),
    ("openalex", "cited_by_count-is-true", {"open_access": {}, "cited_by_count": True}),
    ("openalex", "cited_by_count-is-fractional", {"open_access": {}, "cited_by_count": 3.5}),
]

_HOSTILE_BODIES = [
    (endpoint, f"top:{label}", body)
    for endpoint in ("crossref", "epmc", "openalex", "trial")
    for label, body in _NON_OBJECT_BODIES
] + _WRONG_TYPED_VALUES


class TestNoShapeARemoteSendsEscapesAnalyze:
    """``analyze()`` survives every JSON shape a remote can answer 200 with.

    Issue #199. ``analyze()``'s documented contract is that a dead or
    misbehaving API costs a *component* and not the analysis — and it wraps
    none of its steps, so anything a reader raises leaves a public method.
    Measured against ``main`` with the corpus below — 43 bodies over two
    identifier columns, 86 rows — **48 rows escaped, 24 in each column**: 40
    ``AttributeError``, 6 ``TypeError`` and 2 ``KeyError``, every one of them
    a :data:`_BUG_TYPES` member, so had they been caught one layer down they
    would have been reported as a bmlib defect they are not. Per column that
    is 20 / 3 / 1. *A first cut of this docstring said 23 = 18 + 4 + 1, which
    no committed instrument re-derived — the 18 was the count of ``.get()``
    escapes, carried into the exception tally (PR #208's review).*

    **Six of the 24 are not a ``.get()`` at all** — two ``.lower()`` calls,
    two ``>`` comparisons, and ``result[0]`` raising ``KeyError`` when
    ``result`` is an object and ``TypeError`` when it is a scalar — so the
    guard the issue describes, applied to every ``.get()`` in the module,
    still leaves them. That is why the net is written over ``analyze()``
    rather than over the four readers: it is keyed on the contract, not on
    the expression that happened to break it.

    To re-derive: check out ``main``, copy this file over it, drop the imports
    ``main`` lacks, and run ``-k test_a_hostile_body_costs_its_component``.
    """

    def _analyze(self, monkeypatch, client, ids="doi+pmid"):
        import httpx

        monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
        # The pacer is not what this net is about, and at four requests a row
        # it would cost the suite roughly a minute of sleeping.
        monkeypatch.setattr(TransparencyAnalyzer, "_rate_limit", lambda self: None)
        analyzer = TransparencyAnalyzer(settings=TransparencySettings(), email="t@example.com")
        kwargs = {"doi+pmid": {"doi": "10.1/x", "pmid": "1"}, "doi": {"doi": "10.1/x"}}[ids]
        return analyzer.analyze("doc1", **kwargs)

    #: **The identifier the caller supplies is an axis of this net, not a
    #: fixture detail** (PR #208's review). `analyze()` reads
    #: ``pmid or _pmid_from_epmc(epmc)``, so supplying a PMID short-circuits a
    #: whole reader — and that reader was the one issue #199's fix missed,
    #: still raising ``AttributeError``/``KeyError``/``TypeError`` out of a
    #: public method on any DOI-only analysis. Every row below ran green with
    #: ``pmid="1"`` while four of them escaped without it.
    #:
    #: This is the gap the endpoint-level anti-vacuity assertion cannot see:
    #: EuropePMC *was* requested either way, so `requested` was satisfied
    #: while a reader of its answer never ran.
    _IDENTIFIERS = ("doi+pmid", "doi")

    @pytest.mark.parametrize("ids", _IDENTIFIERS)
    @pytest.mark.parametrize(
        ("endpoint", "label", "body"),
        [pytest.param(e, la, b, id=f"{e}-{la}") for e, la, b in _HOSTILE_BODIES],
    )
    def test_a_hostile_body_costs_its_component_and_not_the_analysis(
        self, monkeypatch, endpoint, label, body, ids
    ):
        client = _MalformedBodyClient(endpoint, body)
        result = self._analyze(monkeypatch, client, ids)

        assert isinstance(result, TransparencyResult)
        # The contract's second half, and the one a bare "did not raise" would
        # miss: the other three components still ran, so the analysis was not
        # demoted to UNKNOWN by one remote's malformed answer.
        assert result.risk_level is not TransparencyRisk.UNKNOWN
        # Anti-vacuity: a hostile body served at an endpoint `analyze()` never
        # asks for asserts nothing. Without this, deleting a whole request
        # would turn its rows green.
        assert endpoint in client.requested

    def test_a_funder_that_is_not_a_list_scores_nothing_and_says_so(self, monkeypatch):
        # **The net above cannot see this one**, and that is the point of
        # writing it separately: `funder` arriving as an object is truthy and
        # iterates into its keys, so nothing raises — CrossRef is simply
        # credited with funder information it did not send. Dropping the
        # `isinstance` and keeping the truthiness test survived every test in
        # this file until this assertion existed. (The file's own count is not
        # quoted: it moves with every added test, and four documents carried a
        # stale 459 — PR #208's review.)
        #
        # A contract test asks "did the analysis survive?"; this asks "what
        # did it conclude?", which is the half a `_BUG_TYPES` net is blind to.
        client = _MalformedBodyClient(
            "crossref", {"message": {"funder": {"name": "Acme Pharmaceuticals Inc"}}}
        )
        result = self._analyze(monkeypatch, client)

        assert result.industry_funding_detected is False
        # **And it says which of the two things happened.** CrossRef *did*
        # send a `funder` — one naming an industry entity — so storing "no
        # funder information in CrossRef" was a false claim about the record,
        # not merely a lost component (PR #208's review). That is issue #191's
        # rule one endpoint over, and the reason this assertion is on the
        # indicator's identity rather than on its presence.
        assert _INDICATOR_FUNDERS_NOT_READABLE in result.risk_indicators
        assert _INDICATOR_NO_FUNDER_INFO not in result.risk_indicators

    def test_a_trial_body_stating_no_results_is_not_stored_as_posted(self, monkeypatch):
        # **The worst of the "read wrongly without raising" family**, and the
        # one the row above could not catch. `bool("no")` is `True`, so
        # ClinicalTrials.gov stating *no results* was persisted as results
        # posted, with `trial_results_compliant` set and
        # `SCORE_RESULTS_POSTED` awarded — a false claim in the affirmative
        # about a trial, at the exact site issue #194 made one for a release.
        #
        # Asserting the *status* and not just the score: the enum is what a
        # downstream branches on, and `REQUEST_FAILED` is the honest answer —
        # ClinicalTrials.gov was asked and did not answer the question.
        client = _MalformedBodyClient("trial", {"hasResults": "no"})
        result = self._analyze(monkeypatch, client)

        assert result.trial_results_status is TrialResultsStatus.REQUEST_FAILED
        assert result.trial_results_compliant is False
        assert _INDICATOR_RESULTS_NOT_CHECKABLE in result.risk_indicators

    def test_a_trial_body_that_really_says_posted_still_scores(self, monkeypatch):
        # The negative control: a guard refusing every `hasResults` would
        # satisfy the row above while deleting the component.
        client = _MalformedBodyClient()
        result = self._analyze(monkeypatch, client)

        assert result.trial_results_status is TrialResultsStatus.POSTED
        assert result.trial_results_compliant is True

    def test_an_open_access_flag_that_is_a_string_scores_nothing(self, monkeypatch):
        # Same class, lower stakes: `{"is_oa": "false"}` is a truthy string
        # and awarded `SCORE_OPEN_ACCESS` for a body stating the opposite.
        # Measured against the control below, so the assertion is on the
        # points the flag is worth rather than on an absolute score.
        lying = self._analyze(
            monkeypatch, _MalformedBodyClient("openalex", {"open_access": {"is_oa": "false"}})
        )
        honest = self._analyze(
            monkeypatch, _MalformedBodyClient("openalex", {"open_access": {"is_oa": False}})
        )
        assert lying.transparency_score == honest.transparency_score

    def test_a_real_open_access_flag_still_scores(self, monkeypatch):
        # The negative control for the row above.
        oa = self._analyze(
            monkeypatch, _MalformedBodyClient("openalex", {"open_access": {"is_oa": True}})
        )
        not_oa = self._analyze(
            monkeypatch, _MalformedBodyClient("openalex", {"open_access": {"is_oa": False}})
        )
        assert oa.transparency_score > not_oa.transparency_score

    def test_a_real_funder_list_still_scores(self, monkeypatch):
        # The negative control for the row above: a guard that refused every
        # funder list would satisfy it while deleting the component.
        client = _MalformedBodyClient()
        result = self._analyze(monkeypatch, client)

        assert "No funder information in CrossRef" not in result.risk_indicators
        assert result.industry_funding_detected is True

    @pytest.mark.parametrize("ids", _IDENTIFIERS)
    def test_the_good_bodies_reach_every_endpoint_this_net_serves(self, monkeypatch, ids):
        # The net's own negative control. Each row above is only as strong as
        # the request behind it, and three of the four endpoints are reached
        # conditionally — so if a future change stopped `analyze()` asking one
        # of them, that endpoint's rows would pass while testing nothing.
        client = _MalformedBodyClient()
        self._analyze(monkeypatch, client, ids)
        assert set(client.requested) == {"crossref", "epmc", "openalex", "trial"}

    def test_the_doi_only_rows_actually_reach_the_reader_they_exist_for(self, monkeypatch):
        # Anti-vacuity for the axis itself. Asserting the *endpoint* was
        # requested cannot show that `_pmid_from_epmc` ran — it runs only
        # because no PMID was supplied — so this pins the one observable that
        # separates the two columns: the PMID reaching `_check_pubmed` is the
        # one read out of EuropePMC's record.
        seen: list[str | None] = []
        client = _MalformedBodyClient()
        real = TransparencyAnalyzer._check_pubmed

        def _spy(self, http_client, pmid):
            seen.append(pmid)
            return real(self, http_client, pmid)

        monkeypatch.setattr(TransparencyAnalyzer, "_check_pubmed", _spy)
        self._analyze(monkeypatch, client, "doi")
        assert seen == ["9999001"]


class TestReadingADecodedJSONBody:
    """The coercers' own rules, where the end-to-end net cannot separate them.

    The net above asserts a contract — that nothing escapes ``analyze()`` —
    so it is blind to a value that is read *wrongly* without raising. These
    are those cases.
    """

    def test_a_json_true_is_not_a_count(self):
        # `True` is an `int` in Python, so the obvious `isinstance(value, int)`
        # accepts it and `True > 0` awards SCORE_CITED for a body that stated
        # no count at all. Nothing raises, so no contract test can see it.
        assert _json_count(True) == 0
        assert _json_count(False) == 0

    def test_a_real_count_survives(self):
        # The negative control for the row above: a guard that refused
        # everything would satisfy it while removing the signal entirely.
        assert _json_count(3) == 3

    def test_a_value_that_cannot_be_compared_is_no_count(self):
        # These two are what raised `TypeError: '>' not supported` out of
        # `analyze()`; `None` is the one a `.get(k, 0)` default cannot rescue,
        # since the key is present.
        assert _json_count("3") == 0
        assert _json_count(None) == 0

    def test_a_string_is_not_a_boolean(self):
        # `bool("no")` is `True` — the read that stored "results posted" for a
        # body stating the opposite. Both spellings, because a remote that
        # sends one may send the other.
        assert _json_bool("no") is None
        assert _json_bool("false") is None
        assert _json_bool("true") is None

    def test_a_real_boolean_survives_in_both_directions(self):
        # The negative control, and it needs both: a coercer returning `None`
        # for everything would satisfy the row above, and one returning
        # `True` for every boolean would satisfy half of this.
        assert _json_bool(True) is True
        assert _json_bool(False) is False

    def test_a_number_is_not_a_boolean(self):
        # `1` and `0` are what `bool()` would have accepted silently; `None`
        # is what an absent key already gives, and the three must agree that
        # the remote did not answer.
        assert _json_bool(1) is None
        assert _json_bool(0) is None
        assert _json_bool(None) is None

    def test_a_fractional_count_is_refused(self):
        # Not a hazard — `3.5 > 0` is fine — but the return type says `int`,
        # so accepting one would make the annotation false rather than the
        # comparison unsafe.
        assert _json_count(3.5) == 0

    def test_a_present_null_does_not_reach_the_reader(self):
        # `x.get("k", {})` and `(x.get("k") or "")` both look like these and
        # are not: the first returns the default only for an *absent* key, and
        # the second rescues `null` while passing an object straight through.
        assert _json_object(None) == {}
        assert _json_object([]) == {}
        assert _json_text(None) == ""
        assert _json_text({"a": 1}) == ""

    def test_a_well_formed_value_is_returned_unchanged(self):
        # The coercers must not be silently emptying good bodies, which every
        # assertion above would tolerate.
        assert _json_object({"a": 1}) == {"a": 1}
        assert _json_text("x") == "x"

    def test_the_records_before_a_bad_one_are_kept(self):
        # Truncation, not a filter: everything ahead of the bad record is at
        # its own rank and safe to read.
        body = {"resultList": {"result": [{"id": "PMC1"}, "junk", {"id": "PMC3"}]}}
        assert _epmc_records(body) == [{"id": "PMC1"}]

    def test_a_bad_head_record_does_not_promote_the_paper_behind_it(self):
        # **The reason it truncates rather than filters** (PR #208's review).
        # EuropePMC returns best-match-first and every reader takes
        # `records[0]` as *this paper*, so a filter whose head was bad made a
        # different article the subject — silently, and with its trial
        # accession and its PMID then attributed to this paper. A shortened
        # list is a lost component; a shifted one is the wrong answer.
        other = {"id": "PMC999", "pmid": "99999999", "abstractText": "NCT07654321"}
        body = {"resultList": {"result": ["junk", other]}}
        assert _epmc_records(body) == []
        assert _find_trial_ids(body) == []
        assert _pmid_from_epmc(body) is None

    def test_a_result_that_is_not_a_list_yields_no_records(self):
        # `result` arriving as an object is one of the escapes in issue #199
        # that no `.get()` guard reaches: `result[0]` raised `KeyError: 0`.
        # (Not "the one" — a scalar `result` raises `TypeError` at the same
        # expression, and the two `.lower()` and two `>` escapes are equally
        # out of a `.get()` guard's reach. PR #208's review.)
        assert _epmc_records({"resultList": {"result": {"id": "PMC1"}}}) == []
        assert _epmc_records({"resultList": []}) == []
        assert _epmc_records(None) == []

    def test_a_result_that_is_a_scalar_yields_no_records(self):
        # **This row is what makes the list test a list test.** An object, a
        # string and an absent key all iterate — or do not — into the same
        # empty answer, so weakening `isinstance(result, list)` to
        # `result is None` survived every other assertion here. A number does
        # not iterate at all, and is the shape that separates them.
        assert _epmc_records({"resultList": {"result": 7}}) == []
        assert _epmc_records({"resultList": {"result": True}}) == []


class TestANonObjectBodyIsReported:
    """A response thrown away leaves a line — issue #193's rule, one shape on."""

    def test_it_warns_once_and_names_the_type(self, caplog):
        analyzer = TransparencyAnalyzer()
        client = _AnsweringClient(200, payload=[{"hasResults": True}])
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            result = analyzer._request_json(
                client, "https://example.test/x", api="CrossRef", subject="10.1/x"
            )
        assert result is None
        named = [r for r in caplog.records if "not an object" in r.getMessage()]
        assert len(named) == 1
        assert named[0].levelno == logging.WARNING
        # The type is named because "not an object" does not say whether an
        # array or a bare string arrived, and those are different remotes
        # misbehaving in different ways.
        # **Parenthesised, because a bare `"list"` is not unique to this
        # line** — the repo's own rule about a substring assertion, and it was
        # vacuous twice over: mutating `type(data).__name__` to `type(data)`
        # (which prints `<class 'list'>`) survived, and so did hard-coding the
        # literal, which would report every non-object body as an array
        # (PR #208's review).
        assert "(list)" in named[0].getMessage()

    def test_it_is_not_reported_as_a_bmlib_defect(self, caplog):
        # A body the remote chose is the remote's failure. ERROR is reserved
        # for a `_BUG_TYPES` member, which can only mean bmlib is wrong —
        # reporting this at that level is issue #187 inside the fix for it.
        analyzer = TransparencyAnalyzer()
        client = _AnsweringClient(200, payload="nope")
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._request_json(
                client, "https://example.test/x", api="CrossRef", subject="10.1/x"
            )
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    def test_it_names_the_type_that_actually_arrived(self, caplog):
        # The negative control for the assertion above: a line hard-coding
        # `"list"` would satisfy it while telling every reader the wrong
        # thing about a bare string.
        analyzer = TransparencyAnalyzer()
        client = _AnsweringClient(200, payload="nope")
        with caplog.at_level(logging.DEBUG, logger="bmlib.transparency.analyzer"):
            analyzer._request_json(
                client, "https://example.test/x", api="CrossRef", subject="10.1/x"
            )
        named = [r for r in caplog.records if "not an object" in r.getMessage()]
        assert len(named) == 1
        assert "(str)" in named[0].getMessage()


class TestOnlyTheHelperWalksTheEuropePMCResultList:
    """Every reader of ``resultList`` goes through :func:`_epmc_records`.

    **This rule lived in a docstring and slipped in the commit that wrote
    it** (PR #208's review). `_epmc_records` was added to put the readers of
    ``resultList.result`` on one answer, its docstring said there were *two*,
    and there were three — :func:`_pmid_from_epmc` kept its hand-rolled copy,
    so four ``_BUG_TYPES`` members still escaped a public ``analyze()`` on
    every DOI-only analysis. Nothing pushed back, because nothing could.

    This repo's answer to that is not a better docstring: it is
    ``TestTheAuditNetIsComplete`` and
    ``TestOnlyAnAccumulatingElementReadsTheBuffer`` one package over — *a rule
    enforced by prose is not enforced*. A fourth reader is a likelier change
    than a rewrite of the helper, and it is exactly the change this catches.

    Keyed on the **literal** rather than on the call, because that is what a
    hand-rolled walk must contain and what a delegating one need not: a reader
    that goes through the helper never spells ``resultList`` at all.
    """

    #: The one function allowed to name the key. Not a list to be appended to
    #: — a second entry here is the defect, and it should have to be argued in
    #: a diff rather than added in passing.
    SOLE_READER = "_epmc_records"

    def _functions_naming_the_key(self) -> set[str]:
        import ast

        from bmlib.transparency import analyzer as analyzer_mod

        source = Path(analyzer_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        naming: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            body = list(node.body)
            # A docstring is prose about the walk, not a walk — this class
            # would otherwise fail on the very comments explaining the rule.
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            for stmt in body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Constant) and sub.value == "resultList":
                        naming.add(node.name)
        return naming

    def test_exactly_one_function_names_the_key(self):
        assert self._functions_naming_the_key() == {self.SOLE_READER}

    def test_the_walk_can_see_a_hand_rolled_reader(self):
        # The negative control, and this class needs one badly: a walk that
        # found nothing — a renamed constant, a parse that silently returned
        # no functions, a docstring filter that ate every node — would report
        # an empty set and pass the moment `SOLE_READER` were dropped. So
        # assert the instrument is looking at something, and that it is
        # looking inside a function body rather than at the module.
        import ast

        from bmlib.transparency import analyzer as analyzer_mod

        naming = self._functions_naming_the_key()
        assert naming, "the walk found no reader at all — it is not looking"
        source = Path(analyzer_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert any(
            isinstance(n, ast.FunctionDef) and n.name == self.SOLE_READER for n in ast.walk(tree)
        ), f"{self.SOLE_READER} is gone; this rule now guards nothing"
