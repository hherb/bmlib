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

import logging
import time

import pytest

from bmlib.transparency.analyzer import (
    _DATA_LEVEL_RANK,
    _DATA_PATTERNS,
    _DEPOSITION_DATABANK_LEVELS,
    _INDICATOR_COI_IN_PUBMED,
    _INDICATOR_COI_UNKNOWN,
    _INDICATOR_DATA_DEPOSITED_PREFIX,
    _INDICATOR_DATA_NOT_AVAILABLE,
    _INDICATOR_INDUSTRY_COI,
    _INDICATOR_NO_COI_IN_FULLTEXT,
    _INDICATOR_NO_POSTED_RESULTS,
    _INDICATOR_RESULTS_NOT_CHECKABLE,
    _NESTED_ARTICLE_ELEMENTS,
    _NESTED_ARTICLE_TOKEN_RE,
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
    _score_data_availability,
    _strip_nested_articles,
    _UnterminatedMarkupError,
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
        assert _INDICATOR_COI_UNKNOWN in analysis.indicators
        matching = [r for r in caplog.records if "is not well-formed" in r.getMessage()]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING
        # The construct, not merely the fact: "which one and where" is what an
        # operator cannot re-derive without lexing the body a second time.
        assert "unterminated comment" in matching[0].getMessage()
        assert "offset 30" in matching[0].getMessage()

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
    sound as a description of the corpus: **0 of 3,880 sampled deposits** is
    malformed (3,000 drawn at random from the ``oa_comm`` ``PMC012xxxxxx``
    baseline package plus all 880 of a Europe PMC draw). What it did not have
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
    unterminated construct took **22.9s**, and each doubling cost four times
    the last. ``_HTTP_TIMEOUT_SECONDS`` bounds the request, not the
    post-processing, so that body did not fail — it stalled, reaching neither
    the refusal nor the warning.

    Both fixes are in the fail-closed direction the module already takes, and
    **neither needs a constant drawn from a corpus** — the issue's other two
    remedies, a size cap and a work multiple, each wanted a threshold nothing
    had measured, and real articles reach 3.4 MB.
    """

    # ---- an end tag closes the element that opened the region ----

    def test_an_end_tag_closes_only_the_element_that_named_it(self):
        # The depth used to be a bare count, so `</response>` closed a region
        # a <sub-article> had opened and the reviewer prose after it came back
        # as this article's. A stack of names costs one list and fixes it: the
        # mismatched end tag is ignored, and the region ends where it says it
        # ends.
        stripped = _strip_nested_articles(
            "<article><p>Ours.</p>"
            "<sub-article><p>Reviewer prose.</p></response>"
            "<p>MORE REVIEWER PROSE.</p></sub-article>"
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
        # loop that noted the refusal and carried on would still return None
        # and still pass every test above, at O(n^2).
        xml = "<article>" + "<!--x" * 2_000
        with pytest.raises(_UnterminatedMarkupError) as excinfo:
            _strip_nested_articles(xml)
        assert "offset 9" in str(excinfo.value)

    def test_an_unterminated_construct_does_not_lex_quadratically(self):
        # The only end-to-end proof of the bound, so it is a wall-clock
        # assertion with a margin rather than a ratio: this shape took 22.9s
        # before the refusal and takes single-digit milliseconds after, so the
        # ceiling is ~300x the measured time and ~0.1x the defect's. Doubling
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
        # alternation and unreachable. Without this, a fallback that matched
        # ahead of the real branches would refuse every article that carries a
        # comment and still pass every test above.
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

    def test_every_branch_of_the_lexer_opens_with_the_literal(self):
        # `sre` derives a prefix for the whole pattern only when every
        # top-level branch begins with the same literal, and then skips from
        # "<" to "<" rather than trying the pattern at every position. A
        # branch that opens with a group defeats that analysis silently:
        # measured over 7.8 MB of real articles, the refusal branch written
        # `(?P<unterminated><!--|...)` cost **191 ms against 13.5 ms**, and
        # factoring the alternatives inside the group did not recover it. The
        # correct form differs by two characters, so nothing but this test
        # stands between the two — a 14x tax on every article, with the whole
        # suite green.
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
