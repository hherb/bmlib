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

"""Tests for the PubMed E-utilities fetcher."""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from bmlib.publications.fetchers.pubmed import (
    EFETCH_URL,
    ESEARCH_URL,
    PART_SCHEME,
    _format_abstract_markdown,
    _parse_article_xml,
    _text_with_formatting,
    fetch_pubmed,
)
from bmlib.publications.models import FetchedRecord, PartCheckpoint, SyncProgress

# ---------------------------------------------------------------------------
# Sample XML fragments
# ---------------------------------------------------------------------------

FULL_ARTICLE_XML = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>12345678</PMID>
    <Article>
      <ArticleTitle>Effects of aspirin on cardiovascular outcomes</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">Heart disease is the leading cause of death.</AbstractText>
        <AbstractText Label="METHODS">We conducted a randomized trial.</AbstractText>
        <AbstractText Label="RESULTS">Aspirin reduced events by 20%.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author>
          <LastName>Smith</LastName>
          <ForeName>John A</ForeName>
        </Author>
        <Author>
          <LastName>Jones</LastName>
          <ForeName>Mary B</ForeName>
        </Author>
      </AuthorList>
      <Journal>
        <Title>The Lancet</Title>
        <JournalIssue>
          <PubDate>
            <Year>2024</Year>
            <Month>Jan</Month>
            <Day>15</Day>
          </PubDate>
        </JournalIssue>
      </Journal>
      <PublicationTypeList>
        <PublicationType UI="D016428">Journal Article</PublicationType>
        <PublicationType UI="D016449">Randomized Controlled Trial</PublicationType>
      </PublicationTypeList>
    </Article>
    <MeshHeadingList>
      <MeshHeading>
        <DescriptorName>Aspirin</DescriptorName>
      </MeshHeading>
      <MeshHeading>
        <DescriptorName>Cardiovascular Diseases</DescriptorName>
      </MeshHeading>
    </MeshHeadingList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="doi">10.1016/S0140-6736(24)00001-1</ArticleId>
      <ArticleId IdType="pmc">PMC9999999</ArticleId>
      <ArticleId IdType="pubmed">12345678</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

MINIMAL_ARTICLE_XML = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>99999999</PMID>
    <Article>
      <ArticleTitle>A minimal record</ArticleTitle>
      <Journal>
        <Title>Some Journal</Title>
        <JournalIssue>
          <PubDate>
            <Year>2024</Year>
          </PubDate>
        </JournalIssue>
      </Journal>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">99999999</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""


def _make_esearch_xml(count: int, web_env: str = "WEBENV123", query_key: str = "1") -> str:
    """Build a minimal eSearchResult XML string."""
    return (
        f"<eSearchResult>"
        f"<Count>{count}</Count>"
        f"<WebEnv>{web_env}</WebEnv>"
        f"<QueryKey>{query_key}</QueryKey>"
        f"</eSearchResult>"
    )


def _make_efetch_xml(*article_xmls: str) -> str:
    """Wrap article XML strings in a PubmedArticleSet root."""
    return "<PubmedArticleSet>" + "".join(article_xmls) + "</PubmedArticleSet>"


# ---------------------------------------------------------------------------
# Tests for _parse_article_xml
# ---------------------------------------------------------------------------


class TestParseArticleXml:
    """Tests for _parse_article_xml with full and minimal articles."""

    def test_full_article_all_fields(self):
        """All fields are correctly extracted from a complete PubmedArticle."""
        el = ET.fromstring(FULL_ARTICLE_XML)
        result = _parse_article_xml(el)

        assert isinstance(result, FetchedRecord)
        assert result.pmid == "12345678"
        assert result.title == "Effects of aspirin on cardiovascular outcomes"
        assert "**BACKGROUND:** Heart disease is the leading cause of death." in result.abstract
        assert "**METHODS:** We conducted a randomized trial." in result.abstract
        assert "**RESULTS:** Aspirin reduced events by 20%." in result.abstract
        assert result.authors == ["Smith, John A", "Jones, Mary B"]
        assert result.journal == "The Lancet"
        assert result.publication_date == "2024-01-15"
        assert result.doi == "10.1016/S0140-6736(24)00001-1"
        assert result.pmc_id == "PMC9999999"
        assert result.keywords == ["Aspirin", "Cardiovascular Diseases"]
        assert result.publication_types == ["Journal Article", "Randomized Controlled Trial"]
        assert result.source == "pubmed"

        # Fulltext sources
        assert len(result.fulltext_sources) == 2
        pmc_source = result.fulltext_sources[0]
        assert pmc_source.source == "pmc"
        assert "PMC9999999" in pmc_source.url
        assert pmc_source.format == "html"
        doi_source = result.fulltext_sources[1]
        assert doi_source.source == "publisher"
        assert "10.1016/S0140-6736(24)00001-1" in doi_source.url
        assert doi_source.format == "html"

    def test_minimal_article_missing_optional_fields(self):
        """Missing optional fields (DOI, abstract, authors) are handled gracefully."""
        el = ET.fromstring(MINIMAL_ARTICLE_XML)
        result = _parse_article_xml(el)

        assert isinstance(result, FetchedRecord)
        assert result.pmid == "99999999"
        assert result.title == "A minimal record"
        assert result.abstract is None
        assert result.authors == []
        assert result.journal == "Some Journal"
        assert result.publication_date == "2024"
        assert result.doi is None
        assert result.pmc_id is None
        assert result.keywords == []
        assert result.publication_types == []
        assert result.fulltext_sources == []
        assert result.source == "pubmed"

    def test_publication_types_feed_the_quality_metadata_filter(self):
        """Parsed publication types classify via the free Tier 1 metadata filter.

        The filter keys off ``publication_types``; a record fetched without
        them falls through to the paid LLM tiers, so this guards the whole
        free path rather than just the parser.
        """
        from bmlib.quality import StudyDesign
        from bmlib.quality.metadata_filter import classify_from_metadata

        el = ET.fromstring(FULL_ARTICLE_XML)
        result = _parse_article_xml(el)

        assessment = classify_from_metadata(result.publication_types)
        assert assessment.study_design is StudyDesign.RCT
        assert assessment.is_randomized is True

    def test_publication_type_without_text_is_skipped(self):
        """An empty PublicationType element does not yield a blank entry."""
        xml = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>1</PMID>
    <Article>
      <ArticleTitle>T</ArticleTitle>
      <PublicationTypeList>
        <PublicationType/>
        <PublicationType>Review</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
</PubmedArticle>
"""
        result = _parse_article_xml(ET.fromstring(xml))
        assert result.publication_types == ["Review"]

    def test_numeric_month(self):
        """Numeric month values are zero-padded correctly."""
        xml = """\
        <PubmedArticle>
          <MedlineCitation>
            <PMID>11111111</PMID>
            <Article>
              <ArticleTitle>Numeric month test</ArticleTitle>
              <Journal>
                <Title>Test Journal</Title>
                <JournalIssue>
                  <PubDate>
                    <Year>2024</Year>
                    <Month>3</Month>
                    <Day>5</Day>
                  </PubDate>
                </JournalIssue>
              </Journal>
            </Article>
          </MedlineCitation>
          <PubmedData><ArticleIdList/></PubmedData>
        </PubmedArticle>
        """
        el = ET.fromstring(xml)
        result = _parse_article_xml(el)
        assert isinstance(result, FetchedRecord)
        assert result.publication_date == "2024-03-05"

    def test_season_month_falls_back_to_year(self):
        """A non-numeric, non-month value (e.g. a season) must not produce an
        invalid date like '2024-Winter' — fall back to year only."""
        xml = """\
        <PubmedArticle>
          <MedlineCitation>
            <PMID>22222222</PMID>
            <Article>
              <ArticleTitle>Season month test</ArticleTitle>
              <Journal>
                <Title>Test Journal</Title>
                <JournalIssue>
                  <PubDate>
                    <Year>2024</Year>
                    <Month>Winter</Month>
                  </PubDate>
                </JournalIssue>
              </Journal>
            </Article>
          </MedlineCitation>
          <PubmedData><ArticleIdList/></PubmedData>
        </PubmedArticle>
        """
        el = ET.fromstring(xml)
        result = _parse_article_xml(el)
        assert result.publication_date == "2024"

    def test_medline_date_fallback(self):
        """When Year is missing, MedlineDate is used as fallback (first 4 chars)."""
        xml = """\
        <PubmedArticle>
          <MedlineCitation>
            <PMID>33333333</PMID>
            <Article>
              <ArticleTitle>MedlineDate test</ArticleTitle>
              <Journal>
                <Title>J</Title>
                <JournalIssue>
                  <PubDate>
                    <MedlineDate>2024 Jan-Feb</MedlineDate>
                  </PubDate>
                </JournalIssue>
              </Journal>
            </Article>
          </MedlineCitation>
          <PubmedData><ArticleIdList/></PubmedData>
        </PubmedArticle>
        """
        el = ET.fromstring(xml)
        result = _parse_article_xml(el)
        assert isinstance(result, FetchedRecord)
        assert result.publication_date == "2024"

    def test_no_pubdate_returns_none(self):
        """When PubDate element is completely missing, publication_date is None."""
        xml = """\
        <PubmedArticle>
          <MedlineCitation>
            <PMID>44444444</PMID>
            <Article>
              <ArticleTitle>No date test</ArticleTitle>
              <Journal>
                <Title>J</Title>
                <JournalIssue/>
              </Journal>
            </Article>
          </MedlineCitation>
          <PubmedData><ArticleIdList/></PubmedData>
        </PubmedArticle>
        """
        el = ET.fromstring(xml)
        result = _parse_article_xml(el)
        assert isinstance(result, FetchedRecord)
        assert result.publication_date is None

    def test_author_last_name_only(self):
        """Authors with only a last name (no fore name) are included."""
        xml = """\
        <PubmedArticle>
          <MedlineCitation>
            <PMID>22222222</PMID>
            <Article>
              <ArticleTitle>Author test</ArticleTitle>
              <AuthorList>
                <Author>
                  <LastName>Consortium</LastName>
                </Author>
              </AuthorList>
              <Journal>
                <Title>J</Title>
                <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
              </Journal>
            </Article>
          </MedlineCitation>
          <PubmedData><ArticleIdList/></PubmedData>
        </PubmedArticle>
        """
        el = ET.fromstring(xml)
        result = _parse_article_xml(el)
        assert isinstance(result, FetchedRecord)
        assert result.authors == ["Consortium"]


# ---------------------------------------------------------------------------
# Tests for fetch_pubmed
# ---------------------------------------------------------------------------


class TestFetchPubmed:
    """Tests for the fetch_pubmed function with mocked HTTP client."""

    def test_fetch_two_articles(self):
        """esearch returns count=2, efetch returns 2 articles, both are emitted."""
        client = MagicMock()
        target = date(2024, 1, 15)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(2)

        efetch_response = MagicMock()
        efetch_response.text = _make_efetch_xml(FULL_ARTICLE_XML, MINIMAL_ARTICLE_XML)

        client.get.side_effect = [esearch_response, efetch_response]

        records: list[FetchedRecord] = []
        on_record = MagicMock(side_effect=lambda r: records.append(r))

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, target, on_record=on_record)

        assert result.source == "pubmed"
        assert result.date == "2024-01-15"
        assert result.record_count == 2
        assert result.status == "completed"
        assert result.error is None

        assert on_record.call_count == 2
        assert isinstance(records[0], FetchedRecord)
        assert records[0].pmid == "12345678"
        assert isinstance(records[1], FetchedRecord)
        assert records[1].pmid == "99999999"

        # Verify esearch was called with correct URL
        first_call = client.get.call_args_list[0]
        assert first_call[0][0] == ESEARCH_URL

        # Verify efetch was called with correct URL
        second_call = client.get.call_args_list[1]
        assert second_call[0][0] == EFETCH_URL

    def test_fetch_empty_day(self):
        """esearch returns count=0, no efetch calls, returns complete with 0."""
        client = MagicMock()
        target = date(2024, 12, 25)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(0)

        client.get.return_value = esearch_response

        on_record = MagicMock()

        result = fetch_pubmed(client, target, on_record=on_record)

        assert result.source == "pubmed"
        assert result.date == "2024-12-25"
        assert result.record_count == 0
        assert result.status == "completed"
        assert result.error is None

        # Only esearch should be called
        assert client.get.call_count == 1
        on_record.assert_not_called()

    def test_progress_callback_fires(self):
        """on_progress is called after each page of results."""
        client = MagicMock()
        target = date(2024, 6, 1)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(2)

        efetch_response = MagicMock()
        efetch_response.text = _make_efetch_xml(FULL_ARTICLE_XML, MINIMAL_ARTICLE_XML)

        client.get.side_effect = [esearch_response, efetch_response]

        on_record = MagicMock()
        on_progress = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            fetch_pubmed(client, target, on_record=on_record, on_progress=on_progress)

        assert on_progress.call_count == 1
        progress_arg = on_progress.call_args[0][0]
        assert isinstance(progress_arg, SyncProgress)
        assert progress_arg.source == "pubmed"
        assert progress_arg.date == "2024-06-01"
        assert progress_arg.records_processed == 2
        assert progress_arg.records_total == 2
        assert progress_arg.status == "in_progress"

    def test_api_key_passed_to_requests(self):
        """When api_key is provided, it is included in esearch and efetch params."""
        client = MagicMock()
        target = date(2024, 3, 1)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(1)

        efetch_response = MagicMock()
        efetch_response.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        client.get.side_effect = [esearch_response, efetch_response]

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            fetch_pubmed(client, target, on_record=on_record, api_key="MY_KEY")

        # Check esearch call includes api_key
        esearch_params = client.get.call_args_list[0][1]["params"]
        assert esearch_params["api_key"] == "MY_KEY"

        # Check efetch call includes api_key
        efetch_params = client.get.call_args_list[1][1]["params"]
        assert efetch_params["api_key"] == "MY_KEY"

    def test_esearch_error_returns_error_result(self):
        """If esearch raises an exception, return an error FetchResult."""
        client = MagicMock()
        client.get.side_effect = ConnectionError("Network error")

        on_record = MagicMock()

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=on_record)

        assert result.status == "failed"
        assert "Network error" in result.error
        assert result.record_count == 0
        on_record.assert_not_called()

    def test_a_count_without_a_history_session_fails_instead_of_fetching(self):
        """Count>0 with no WebEnv/QueryKey is a failed fetch, not an empty day.

        ESearch is sent ``usehistory=y`` and every page of the fetch reads
        the session back. A response carrying a count but no session leaves
        both `None`, which httpx encodes as an empty parameter — so each
        page asks NCBI for `WebEnv=` and gets an answer holding no
        `PubmedArticle`. Unguarded, that walks the whole count in useless
        requests and reports `completed` with 0 records: a broken fetch
        wearing the shape of a quiet day.
        """
        client = MagicMock()
        esearch_response = MagicMock()
        esearch_response.text = "<eSearchResult><Count>5000</Count></eSearchResult>"
        client.get.return_value = esearch_response

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 1), on_record=on_record)

        assert result.status == "failed"
        assert result.record_count == 0
        # "history session" appears once in bmlib and only on this line; the
        # field names alone would not discriminate, since a real efetch HTTP
        # error embeds `WebEnv=` in the URL it quotes back.
        assert "history session" in result.error
        on_record.assert_not_called()
        # esearch only — not one efetch page was attempted.
        assert client.get.call_count == 1

    @pytest.mark.parametrize(
        "session_xml,present",
        [
            ("<WebEnv>MCID_abc</WebEnv>", "WebEnv"),
            ("<QueryKey>1</QueryKey>", "QueryKey"),
        ],
    )
    def test_half_a_history_session_fails_like_none_at_all(self, session_xml, present):
        """One of WebEnv/QueryKey is as unusable as neither.

        ``_efetch_page`` needs both, so a response carrying only one is the
        same broken fetch. Pins the ``or`` in the guard: with ``and`` in its
        place both of these walk the full count in useless requests and
        report ``completed`` with 0 records, and nothing else in the suite
        notices.
        """
        client = MagicMock()
        esearch_response = MagicMock()
        esearch_response.text = f"<eSearchResult><Count>5000</Count>{session_xml}</eSearchResult>"
        client.get.return_value = esearch_response

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 1), on_record=on_record)

        assert result.status == "failed"
        assert result.record_count == 0
        assert "history session" in result.error
        on_record.assert_not_called()
        assert client.get.call_count == 1
        # The half that *was* present is not what made it fail.
        assert present in esearch_response.text

    def test_a_rejected_search_is_not_reported_as_a_quiet_day(self):
        """An <ERROR> document with no <Count> is a failed fetch, not zero records.

        NCBI answers a bad request — unknown db, invalid term, throttled
        key — with HTTP 200 and an ``<ERROR>`` body carrying no ``<Count>``.
        Reading an absent element as 0 returns ``completed`` at the
        ``count == 0`` branch, which is *before* the history-session guard
        and so slips past it: the same "broken fetch wearing the shape of a
        quiet day", one step earlier. `sync` then stores the day as done and
        never retries it.
        """
        client = MagicMock()
        esearch_response = MagicMock()
        esearch_response.text = (
            "<eSearchResult><ERROR>Invalid db name: pubmedd</ERROR></eSearchResult>"
        )
        client.get.return_value = esearch_response

        on_record = MagicMock()

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=on_record)

        assert result.status == "failed"
        assert result.record_count == 0
        assert "<Count>" in result.error
        # NCBI's own words are carried through, not swallowed.
        assert "Invalid db name: pubmedd" in result.error
        on_record.assert_not_called()
        assert client.get.call_count == 1

    def test_a_genuinely_empty_day_still_completes(self):
        """`<Count>0</Count>` is a quiet day, not a failure.

        The counterpart to the test above: the fix distinguishes an *absent*
        count from a parsed zero, so a real zero must keep completing — and
        must not be pushed into the history-session guard, which would turn
        every empty day into a failed fetch that `sync` retries forever.
        """
        client = MagicMock()
        esearch_response = MagicMock()
        esearch_response.text = "<eSearchResult><Count>0</Count></eSearchResult>"
        client.get.return_value = esearch_response

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=MagicMock())

        assert result.status == "completed"
        assert result.record_count == 0
        assert result.error is None

    def test_efetch_error_returns_partial_result(self):
        """If efetch raises an exception, return error with partial count."""
        client = MagicMock()
        target = date(2024, 1, 1)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(1000)

        # First efetch page succeeds, second fails
        efetch_response_ok = MagicMock()
        efetch_response_ok.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        efetch_response_err = MagicMock()
        efetch_response_err.raise_for_status.side_effect = Exception("Server error")

        client.get.side_effect = [esearch_response, efetch_response_ok, efetch_response_err]

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, target, on_record=on_record)

        assert result.status == "failed"
        assert result.record_count == 1  # only the first page succeeded
        assert "Server error" in result.error

    def test_no_progress_callback_when_none(self):
        """When on_progress is None, fetch completes without error."""
        client = MagicMock()
        target = date(2024, 1, 15)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(1)

        efetch_response = MagicMock()
        efetch_response.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        client.get.side_effect = [esearch_response, efetch_response]

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, target, on_record=on_record, on_progress=None)

        assert result.status == "completed"
        assert result.record_count == 1

    def test_rate_limiting_with_key(self):
        """With an API key, rate limit delay is RATE_LIMIT_WITH_KEY (0.1s)."""
        client = MagicMock()
        target = date(2024, 1, 1)

        # count > page_size to trigger pagination and rate limiting
        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(600)

        efetch_page1 = MagicMock()
        efetch_page1.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        efetch_page2 = MagicMock()
        efetch_page2.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        client.get.side_effect = [esearch_response, efetch_page1, efetch_page2]

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep") as mock_sleep:
            fetch_pubmed(client, target, on_record=on_record, api_key="KEY")

        # Sleep should be called once between page 1 and page 2
        # (not after the last page)
        mock_sleep.assert_called_once_with(0.1)

    def test_rate_limiting_without_key(self):
        """Without an API key, rate limit delay is RATE_LIMIT_WITHOUT_KEY (0.34s)."""
        client = MagicMock()
        target = date(2024, 1, 1)

        esearch_response = MagicMock()
        esearch_response.text = _make_esearch_xml(600)

        efetch_page1 = MagicMock()
        efetch_page1.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        efetch_page2 = MagicMock()
        efetch_page2.text = _make_efetch_xml(MINIMAL_ARTICLE_XML)

        client.get.side_effect = [esearch_response, efetch_page1, efetch_page2]

        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep") as mock_sleep:
            fetch_pubmed(client, target, on_record=on_record, api_key=None)

        mock_sleep.assert_called_once_with(0.34)


# ---------------------------------------------------------------------------
# Inline formatting and Markdown abstracts
# ---------------------------------------------------------------------------


class TestInlineFormatting:
    """Tests for _text_with_formatting — mixed content and Markdown mapping."""

    def test_a_title_with_markup_is_not_truncated(self):
        """The whole title survives markup.

        Regression guard. The previous implementation read ``el.text``, which is
        only the text *before the first child*, so this title parsed as
        "Effects of H" — silently discarding most of the primary field.
        """
        el = ET.fromstring(
            "<ArticleTitle>Effects of H<sub>2</sub>O and <i>E. coli</i> on outcomes</ArticleTitle>"
        )
        assert _text_with_formatting(el) == "Effects of H~2~O and *E. coli* on outcomes"

    def test_a_plain_element_is_its_text(self):
        assert _text_with_formatting(ET.fromstring("<t>Plain text</t>")) == "Plain text"

    def test_none_is_the_empty_string(self):
        assert _text_with_formatting(None) == ""

    def test_an_empty_element_is_the_empty_string(self):
        assert _text_with_formatting(ET.fromstring("<t/>")) == ""

    def test_each_inline_tag_maps_to_its_marker(self):
        cases = [
            ("<t><b>x</b></t>", "**x**"),
            ("<t><bold>x</bold></t>", "**x**"),
            ("<t><i>x</i></t>", "*x*"),
            ("<t><italic>x</italic></t>", "*x*"),
            ("<t><sup>x</sup></t>", "^x^"),
            ("<t><sub>x</sub></t>", "~x~"),
        ]
        for xml, expected in cases:
            assert _text_with_formatting(ET.fromstring(xml)) == expected, xml

    def test_underline_is_not_rendered_as_bold(self):
        """``<u>`` must not borrow ``<b>``'s markers.

        Markdown has no underline, and ``__x__`` is *strong* emphasis — the
        same output ``<b>`` produces. Mapping ``<u>`` to it would render the
        two identically while asserting the source said "bold", which is the
        ambiguity ``sub``/``sup`` earned their Pandoc markers to avoid. Passing
        the text through undecorated loses only presentation.
        """
        for xml in ("<t><u>x</u></t>", "<t><underline>x</underline></t>"):
            assert _text_with_formatting(ET.fromstring(xml)) == "x", xml

    def test_an_unknown_tag_contributes_its_text_undecorated(self):
        el = ET.fromstring("<t>a <unknown>b</unknown> c</t>")
        assert _text_with_formatting(el) == "a b c"

    def test_nested_formatting_nests(self):
        el = ET.fromstring("<t><b>bold and <i>italic</i></b></t>")
        assert _text_with_formatting(el) == "**bold and *italic***"

    def test_a_space_inside_a_formatted_run_is_not_eaten(self):
        """Whitespace inside a formatted run survives.

        Upstream stripped at *every* recursion level, so the space belonging to
        ``<b>Randomised </b>`` vanished and the two runs welded into
        ``**Randomised****trial**`` — not merely ugly, but broken Markdown.
        Stripping happens once, at the outermost call.
        """
        el = ET.fromstring("<t><b>Randomised </b><b>trial</b></t>")
        assert _text_with_formatting(el) == "**Randomised** **trial**"

    def test_a_trailing_space_before_a_sibling_run_is_not_eaten(self):
        el = ET.fromstring("<t>A <b>bold </b><i>italic</i> tail</t>")
        assert _text_with_formatting(el) == "A **bold** *italic* tail"

    def test_surrounding_whitespace_is_stripped_once(self):
        el = ET.fromstring("<t>  padded <b>x</b>  </t>")
        assert _text_with_formatting(el) == "padded **x**"


class TestProseIsEscapedAgainstItsOwnMarkup:
    """Declaring a field Markdown must not corrupt text that was fine before.

    The escape set is measured, not guessed: across 3,403 real titles and
    abstract sections, escaping ``\\ ` * ~ ^`` altered 0.35% of them and removed
    every construct a CommonMark parser found, while adding ``_`` and
    ``[``/``]`` churned 4.3% and fixed nothing further.
    """

    def test_a_star_allele_is_not_emphasis(self):
        """The measured asterisk case, from real records.

        ``CYP2C19 (*1, *2, *3, *17 alleles)`` is standard pharmacogenomic
        notation; unescaped, a CommonMark parser reads the run between the
        first two stars as emphasis and renders ``(<em>1, </em>2, ...)``.
        """
        el = ET.fromstring("<t>CYP2C19 (*1, *2, *3, *17 alleles)</t>")
        assert _text_with_formatting(el) == r"CYP2C19 (\*1, \*2, \*3, \*17 alleles)"

    def test_an_approximately_tilde_cannot_pair_with_a_subscript_marker(self):
        """The commonest case, and one this module created.

        ``~`` means "approximately" throughout scientific prose ("AUC ~ 0.80",
        "(~88%)"). Emitting ``~2~`` for ``<sub>`` made the character
        meaningful, so an unescaped literal pair now silently subscripts
        everything between them under a Pandoc renderer.
        """
        el = ET.fromstring("<t>AUC ~ 0.80 and ~88% of H<sub>2</sub>O</t>")
        assert _text_with_formatting(el) == r"AUC \~ 0.80 and \~88% of H~2~O"

    def test_a_caret_is_escaped_but_a_superscript_marker_is_not(self):
        el = ET.fromstring("<t>2^10 vs m<sup>2</sup></t>")
        assert _text_with_formatting(el) == r"2\^10 vs m^2^"

    def test_a_backslash_is_escaped_so_the_escapes_are_unambiguous(self):
        el = ET.fromstring(r"<t>path\to\file</t>")
        assert _text_with_formatting(el) == r"path\\to\\file"

    def test_a_backtick_cannot_open_a_code_span(self):
        el = ET.fromstring("<t>the `gene` locus</t>")
        assert _text_with_formatting(el) == r"the \`gene\` locus"

    def test_an_intraword_underscore_is_left_alone(self):
        """Escaping ``_`` would be pure noise.

        CommonMark makes intraword ``_`` inert, so gene and variant names —
        which is nearly every underscore PubMed carries — need no escape. The
        measurement says escaping it alters 10 more fields and fixes none.
        """
        el = ET.fromstring("<t>TP53_R175H and BRCA1_var</t>")
        assert _text_with_formatting(el) == "TP53_R175H and BRCA1_var"

    def test_brackets_are_left_alone(self):
        """A bare ``[...]`` is not a link without a following ``(...)``.

        These are common in PubMed ("[This corrects the article ...]", "[grant
        number X]") and were the whole cost of the wider escape set.
        """
        el = ET.fromstring("<t>[This corrects the article DOI: 10.1/x.]</t>")
        assert _text_with_formatting(el) == "[This corrects the article DOI: 10.1/x.]"

    def test_escaping_reaches_tail_text_as_well_as_element_text(self):
        """Every text node is escaped, not merely the first.

        Tails are a separate node from an element's own text; escaping only the
        latter would leave everything after the first child unprotected.
        """
        el = ET.fromstring("<t>a*b <b>x</b> c*d</t>")
        assert _text_with_formatting(el) == r"a\*b **x** c\*d"

    def test_text_inside_a_formatted_run_is_escaped_too(self):
        el = ET.fromstring("<t><b>2*3</b></t>")
        assert _text_with_formatting(el) == r"**2\*3**"

    def test_an_abstract_label_is_escaped(self):
        el = ET.fromstring(
            '<Abstract><AbstractText Label="Costs*">Prose.</AbstractText></Abstract>'
        )
        assert _format_abstract_markdown(el) == r"**COSTS\*:** Prose."

    def test_an_affiliation_is_escaped_like_any_other_prose(self):
        """Affiliations share the walker, so they share the contract.

        Worth pinning separately because this column is a join key: anything
        matching it against an institution name from elsewhere must compare
        against the escaped form.
        """
        el = ET.fromstring("<Affiliation>Dept of Physics, Building C*, Univ X</Affiliation>")
        assert _text_with_formatting(el) == r"Dept of Physics, Building C\*, Univ X"

    def test_plain_prose_is_untouched(self):
        """The negative control: escaping must not churn ordinary text.

        Without this, a rule that escaped everything would pass every test
        above while making 4% of stored abstracts worse.
        """
        text = "Patients (n = 42) improved by 15% [95% CI 3-27]; P < 0.05, TP53_R175H."
        el = ET.fromstring(f"<t>{text.replace('<', '&lt;')}</t>")
        assert _text_with_formatting(el) == text


class TestAbstractMarkdown:
    """Tests for _format_abstract_markdown."""

    def test_none_yields_none(self):
        assert _format_abstract_markdown(None) is None

    def test_an_abstract_with_no_text_yields_none(self):
        assert _format_abstract_markdown(ET.fromstring("<Abstract/>")) is None

    def test_an_abstract_of_only_blank_text_yields_none(self):
        el = ET.fromstring("<Abstract><AbstractText>   </AbstractText></Abstract>")
        assert _format_abstract_markdown(el) is None

    def test_an_unlabelled_abstract_is_bare_text(self):
        el = ET.fromstring("<Abstract><AbstractText>Just prose.</AbstractText></Abstract>")
        assert _format_abstract_markdown(el) == "Just prose."

    def test_a_label_becomes_a_bold_upper_case_heading(self):
        el = ET.fromstring(
            '<Abstract><AbstractText Label="Background">Prose.</AbstractText></Abstract>'
        )
        assert _format_abstract_markdown(el) == "**BACKGROUND:** Prose."

    def test_nlm_category_supplies_a_label_when_label_is_absent(self):
        """A section labelled only by NlmCategory keeps its label.

        The previous implementation read the ``Label`` attribute alone, so every
        section labelled the other way lost its heading and ran into its
        neighbour.
        """
        el = ET.fromstring(
            '<Abstract><AbstractText NlmCategory="METHODS">Prose.</AbstractText></Abstract>'
        )
        assert _format_abstract_markdown(el) == "**METHODS:** Prose."

    def test_label_wins_over_nlm_category(self):
        el = ET.fromstring(
            '<Abstract><AbstractText Label="Patients" NlmCategory="METHODS">P.</AbstractText>'
            "</Abstract>"
        )
        assert _format_abstract_markdown(el) == "**PATIENTS:** P."

    def test_a_placeholder_nlm_category_is_not_a_label(self):
        """UNASSIGNED and UNLABELLED mean "no label", not a heading of that name."""
        for category in ("UNASSIGNED", "UNLABELLED"):
            el = ET.fromstring(
                f'<Abstract><AbstractText NlmCategory="{category}">Prose.</AbstractText></Abstract>'
            )
            assert _format_abstract_markdown(el) == "Prose.", category

    def test_sections_are_separated_by_a_blank_line(self):
        el = ET.fromstring(
            '<Abstract><AbstractText Label="BACKGROUND">One.</AbstractText>'
            '<AbstractText Label="METHODS">Two.</AbstractText></Abstract>'
        )
        assert _format_abstract_markdown(el) == "**BACKGROUND:** One.\n\n**METHODS:** Two."

    def test_a_section_with_no_text_is_skipped(self):
        el = ET.fromstring(
            '<Abstract><AbstractText Label="BACKGROUND">One.</AbstractText>'
            '<AbstractText Label="METHODS"/></Abstract>'
        )
        assert _format_abstract_markdown(el) == "**BACKGROUND:** One."

    def test_inline_formatting_survives_into_the_abstract(self):
        el = ET.fromstring(
            '<Abstract><AbstractText Label="RESULTS">CO<sub>2</sub> fell over 10 m<sup>2</sup>.'
            "</AbstractText></Abstract>"
        )
        assert _format_abstract_markdown(el) == "**RESULTS:** CO~2~ fell over 10 m^2^."


# ---------------------------------------------------------------------------
# Grants and author affiliations
# ---------------------------------------------------------------------------

GRANTS_AND_AFFILIATIONS_XML = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>55555555</PMID>
    <Article>
      <ArticleTitle>A funded study</ArticleTitle>
      <GrantList>
        <Grant>
          <GrantID>R01 HL123456</GrantID>
          <Agency>NHLBI NIH HHS</Agency>
          <Country>United States</Country>
        </Grant>
        <Grant>
          <Agency>Wellcome Trust</Agency>
        </Grant>
        <Grant>
          <Country>Nowhere</Country>
        </Grant>
      </GrantList>
      <AuthorList>
        <Author>
          <LastName>Smith</LastName>
          <ForeName>John A</ForeName>
          <AffiliationInfo>
            <Affiliation>Department of Cardiology, St Elsewhere.</Affiliation>
          </AffiliationInfo>
          <AffiliationInfo>
            <Affiliation>Institute of Statistics, Elsewhere University.</Affiliation>
          </AffiliationInfo>
        </Author>
        <Author>
          <LastName>Jones</LastName>
          <ForeName>Mary B</ForeName>
        </Author>
        <Author>
          <LastName>Brown</LastName>
          <AffiliationInfo>
            <Affiliation>Pfizer Inc, New York.</Affiliation>
          </AffiliationInfo>
        </Author>
      </AuthorList>
    </Article>
  </MedlineCitation>
</PubmedArticle>
"""


class TestGrantExtraction:
    """Tests for <GrantList> parsing."""

    def test_grants_are_extracted(self):
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))

        assert len(result.grants) == 2
        first = result.grants[0]
        assert first.agency == "NHLBI NIH HHS"
        assert first.grant_id == "R01 HL123456"
        assert first.country == "United States"

    def test_a_grant_may_name_only_an_agency(self):
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))

        second = result.grants[1]
        assert second.agency == "Wellcome Trust"
        assert second.grant_id is None
        assert second.country is None

    def test_a_grant_naming_neither_agency_nor_id_is_dropped(self):
        """A country alone identifies no award, so it is not a grant.

        Storing it would put a row carrying no usable information in front of
        anyone counting a paper's funders.
        """
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))

        assert all(g.country != "Nowhere" for g in result.grants)

    def test_a_record_without_grants_has_an_empty_list(self):
        result = _parse_article_xml(ET.fromstring(MINIMAL_ARTICLE_XML))
        assert result.grants == []


class TestAffiliationExtraction:
    """Tests for <AffiliationInfo> parsing."""

    def test_one_row_per_author_affiliation_pair(self):
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))

        assert len(result.author_affiliations) == 3
        assert [a.affiliation for a in result.author_affiliations] == [
            "Department of Cardiology, St Elsewhere.",
            "Institute of Statistics, Elsewhere University.",
            "Pfizer Inc, New York.",
        ]

    def test_the_author_name_matches_the_authors_list_format(self):
        """Affiliation rows name their author the way ``authors`` does.

        Upstream formatted these "Smith John" while the author list uses
        "Smith, John A", so joining the two on name was guesswork. They agree
        here.
        """
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))

        assert result.authors == ["Smith, John A", "Jones, Mary B", "Brown"]
        assert result.author_affiliations[0].author == "Smith, John A"
        assert result.author_affiliations[0].author in result.authors
        assert result.author_affiliations[2].author == "Brown"

    def test_position_is_the_index_in_the_author_list(self):
        """Position survives so first and senior authorship stay recoverable."""
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))

        # Smith is author 0 (both affiliations); Jones (1) has none; Brown is 2.
        assert [a.position for a in result.author_affiliations] == [0, 0, 2]

    def test_an_author_without_an_affiliation_contributes_no_row(self):
        result = _parse_article_xml(ET.fromstring(GRANTS_AND_AFFILIATIONS_XML))
        assert all(a.author != "Jones, Mary B" for a in result.author_affiliations)

    def test_a_record_without_affiliations_has_an_empty_list(self):
        result = _parse_article_xml(ET.fromstring(MINIMAL_ARTICLE_XML))
        assert result.author_affiliations == []

    def test_position_indexes_the_xml_author_list_not_the_authors_field(self):
        """A consortium consumes a position but contributes no name.

        ``position`` is an index into ``<AuthorList>``, not into
        ``FetchedRecord.authors`` — a ``<CollectiveName>`` author has no
        personal name, so it is absent from ``authors`` while still occupying
        its place in the paper's author order. The two lists therefore differ
        in length whenever one is present, and ``authors[a.position]`` is the
        wrong way to resolve an affiliation's author; match on ``author``
        instead. What position is *for* — is this the first or the senior
        author — is answered correctly either way.
        """
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><AuthorList>"
            "<Author><CollectiveName>The Trial Group</CollectiveName></Author>"
            "<Author><LastName>Smith</LastName><ForeName>J</ForeName>"
            "<AffiliationInfo><Affiliation>St Elsewhere</Affiliation></AffiliationInfo>"
            "</Author>"
            "</AuthorList></Article></MedlineCitation></PubmedArticle>"
        )
        result = _parse_article_xml(el)

        assert result.authors == ["Smith, J"]
        assert result.author_affiliations[0].author == "Smith, J"
        assert result.author_affiliations[0].position == 1

    def test_an_affiliation_carrying_markup_survives_whole(self):
        """`<Affiliation>` is not a leaf element, so it needs the same walker.

        NLM's DTD declares it ``(%text;)*`` — the same content model as
        ``<ArticleTitle>``, admitting ``b``/``i``/``sup``/``sub``/``u``. Read
        with a bare ``.text`` it fails in the two ways this port exists to fix:
        trailing markup truncates the institution, and *leading* markup makes
        ``.text`` ``None``, which the emptiness guard then drops — losing the
        affiliation row altogether rather than merely shortening it.
        """
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><AuthorList>"
            "<Author><LastName>Smith</LastName>"
            "<AffiliationInfo><Affiliation>Dept of Chemistry, Univ X<sup>1</sup>, Rome."
            "</Affiliation></AffiliationInfo>"
            "<AffiliationInfo><Affiliation><sup>2</sup>Istituto Nazionale, Rome."
            "</Affiliation></AffiliationInfo>"
            "</Author></AuthorList></Article></MedlineCitation></PubmedArticle>"
        )
        result = _parse_article_xml(el)

        assert [a.affiliation for a in result.author_affiliations] == [
            "Dept of Chemistry, Univ X^1^, Rome.",
            "^2^Istituto Nazionale, Rome.",
        ]


class TestRepeatedEntriesAreDeduplicated:
    """PubMed repeats identical entries; they must not become duplicate rows.

    Measured against the live API: 31 of 575 `<Grant>` entries across 200
    NIH-funded records were exact duplicates of another entry in the same
    record, affecting 14 records. Stored verbatim they inflate any count of a
    paper's funders, and no downstream reader can tell a real second award
    from PubMed's repetition.
    """

    def test_an_exactly_repeated_grant_is_stored_once(self):
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><GrantList>"
            "<Grant><GrantID>K23 NR020044</GrantID><Agency>NINR NIH HHS</Agency>"
            "<Country>United States</Country></Grant>"
            "<Grant><GrantID>K23 NR020044</GrantID><Agency>NINR NIH HHS</Agency>"
            "<Country>United States</Country></Grant>"
            "</GrantList></Article></MedlineCitation></PubmedArticle>"
        )
        result = _parse_article_xml(el)

        assert len(result.grants) == 1
        assert result.grants[0].grant_id == "K23 NR020044"

    def test_grants_differing_in_any_field_are_both_kept(self):
        """Only an *exact* repeat is a repeat."""
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><GrantList>"
            "<Grant><GrantID>R01 A</GrantID><Agency>NHLBI</Agency></Grant>"
            "<Grant><GrantID>R01 B</GrantID><Agency>NHLBI</Agency></Grant>"
            "<Grant><GrantID>R01 A</GrantID><Agency>NINR</Agency></Grant>"
            "</GrantList></Article></MedlineCitation></PubmedArticle>"
        )
        result = _parse_article_xml(el)

        assert [(g.agency, g.grant_id) for g in result.grants] == [
            ("NHLBI", "R01 A"),
            ("NHLBI", "R01 B"),
            ("NINR", "R01 A"),
        ]

    def test_the_first_occurrence_order_is_kept(self):
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><GrantList>"
            "<Grant><Agency>First</Agency></Grant>"
            "<Grant><Agency>Second</Agency></Grant>"
            "<Grant><Agency>First</Agency></Grant>"
            "</GrantList></Article></MedlineCitation></PubmedArticle>"
        )
        assert [g.agency for g in _parse_article_xml(el).grants] == ["First", "Second"]

    def test_a_repeated_affiliation_for_one_author_is_stored_once(self):
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><AuthorList><Author><LastName>Smith</LastName>"
            "<AffiliationInfo><Affiliation>St Elsewhere</Affiliation></AffiliationInfo>"
            "<AffiliationInfo><Affiliation>St Elsewhere</Affiliation></AffiliationInfo>"
            "</Author></AuthorList></Article></MedlineCitation></PubmedArticle>"
        )
        result = _parse_article_xml(el)

        assert [a.affiliation for a in result.author_affiliations] == ["St Elsewhere"]

    def test_two_authors_at_the_same_institution_both_keep_it(self):
        """Deduplication is per author, not per paper."""
        el = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><AuthorList>"
            "<Author><LastName>Smith</LastName>"
            "<AffiliationInfo><Affiliation>St Elsewhere</Affiliation></AffiliationInfo></Author>"
            "<Author><LastName>Jones</LastName>"
            "<AffiliationInfo><Affiliation>St Elsewhere</Affiliation></AffiliationInfo></Author>"
            "</AuthorList></Article></MedlineCitation></PubmedArticle>"
        )
        result = _parse_article_xml(el)

        assert [(a.author, a.position) for a in result.author_affiliations] == [
            ("Smith", 0),
            ("Jones", 1),
        ]


# ---------------------------------------------------------------------------
# Reconciling the walk against esearch's count (issue #88)
# ---------------------------------------------------------------------------


class TestTheWalkIsReconciledAgainstTheCount:
    """A walk that stopped short must not report as a quiet day.

    ``sync()`` writes a ``completed`` day to ``download_days`` and
    ``_days_needing_fetch()`` never offers it again, so every row here is a
    permanently-absent day if it reports success.
    """

    def _client(self, esearch_xml: str, *efetch_texts: str) -> MagicMock:
        client = MagicMock()
        responses = []
        esearch_response = MagicMock()
        esearch_response.text = esearch_xml
        responses.append(esearch_response)
        for text in efetch_texts:
            efetch_response = MagicMock()
            efetch_response.text = text
            responses.append(efetch_response)
        client.get.side_effect = responses
        return client

    def test_an_error_document_served_as_http_200_fails(self):
        """NCBI answers an evicted history session with <ERROR> and HTTP 200.

        ``raise_for_status()`` never fires and ``findall("PubmedArticle")``
        returns [], so the day used to report ``completed`` with 0 records.
        """
        client = self._client(
            _make_esearch_xml(5000),
            "<eFetchResult><ERROR>Unable to obtain query #1</ERROR></eFetchResult>",
        )
        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=on_record)

        assert result.status == "failed"
        assert result.error is not None
        assert "Unable to obtain query #1" in result.error
        on_record.assert_not_called()

    def test_an_error_document_stops_the_walk_instead_of_paging_on(self):
        """The point of failing early: 10 useless requests were being made."""
        client = self._client(
            _make_esearch_xml(5000),
            "<eFetchResult><ERROR>Unable to obtain query #1</ERROR></eFetchResult>",
        )

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        # One esearch plus one efetch, not one efetch per page of the count.
        assert client.get.call_count == 2

    def test_a_session_dying_mid_walk_fails(self):
        """One article, then empty pages — reproduced verbatim in issue #88."""
        client = self._client(
            _make_esearch_xml(5000),
            _make_efetch_xml(FULL_ARTICLE_XML),
            _make_efetch_xml(),
        )
        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=on_record)

        assert result.status == "failed"
        assert result.error is not None
        assert "empty page" in result.error
        # The one article that did arrive is still emitted and still counted.
        assert on_record.call_count == 1
        assert result.record_count == 1

    def test_an_empty_page_stops_the_walk(self):
        client = self._client(
            _make_esearch_xml(5000),
            _make_efetch_xml(FULL_ARTICLE_XML),
            _make_efetch_xml(),
        )

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert client.get.call_count == 3  # esearch + two efetch pages, not ten

    def test_a_session_dying_on_a_late_page_fails(self):
        """Half a day arrived, so only the empty page itself says it is broken.

        A ratio floor cannot catch this: 500 of 1,000 clears it. It is the
        shape a history session takes when it expires late in a long walk.
        """
        page = _make_efetch_xml(*([MINIMAL_ARTICLE_XML] * 500))
        client = self._client(_make_esearch_xml(1000), page, _make_efetch_xml())

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.record_count == 500

    def test_a_day_of_book_chapters_is_not_a_shortfall(self):
        """<PubmedBookArticle> is delivered by the server and skipped by the parser.

        Reconciling *parsed records* against the count would fail every day
        carrying a book chapter — and a failed day is re-fetched on every later
        run, forever. Delivery is counted from what the server handed over.
        """
        book = "<PubmedBookArticle><BookDocument><PMID>1</PMID></BookDocument></PubmedBookArticle>"
        client = self._client(
            _make_esearch_xml(3),
            "<PubmedArticleSet>" + FULL_ARTICLE_XML + book + book + "</PubmedArticleSet>",
        )
        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=on_record)

        assert result.status == "completed"
        assert result.error is None
        assert on_record.call_count == 1  # only the journal article is parsed

    def test_a_small_shortfall_still_completes(self):
        """A record withdrawn between search and fetch is not a broken walk."""
        client = self._client(
            _make_esearch_xml(2),
            _make_efetch_xml(FULL_ARTICLE_XML),
        )

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "completed"
        assert result.error is None
        assert result.record_count == 1

    def test_a_page_of_delete_citations_is_not_delivery(self):
        """Delivery counts record elements by name, not every child of the set.

        ``<DeleteCitation>`` is a legal child of ``<PubmedArticleSet>``.
        Counted as delivery it is wrong in the expensive direction twice: it
        inflates the count so a real shortfall clears the floor, and — because
        the stall rule is ``delivered == 0`` — a page carrying nothing else
        stops looking like the stall it is, so the walk pages on.

        The book-chapter test above cannot catch this: it distinguishes
        *delivered* from *parsed*, which any child-counting expression also
        satisfies.
        """
        deletes = "<DeleteCitation><PMID>1</PMID><PMID>2</PMID></DeleteCitation>"
        client = self._client(
            _make_esearch_xml(3),
            "<PubmedArticleSet>" + deletes + "</PubmedArticleSet>",
        )
        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=on_record)

        assert result.status == "failed"
        assert result.error is not None
        assert "empty page" in result.error
        on_record.assert_not_called()

    def test_a_full_walk_still_completes(self):
        """Negative control: reconciliation must not fail an ordinary day."""
        client = self._client(
            _make_esearch_xml(2),
            _make_efetch_xml(FULL_ARTICLE_XML, MINIMAL_ARTICLE_XML),
        )

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "completed"
        assert result.error is None
        assert result.record_count == 2


# ---------------------------------------------------------------------------
# What indexes the walk, and where PubMed stops serving a session
# ---------------------------------------------------------------------------


def _tiny_article(pmid: int) -> str:
    """The smallest record `_parse_article_xml` reads, for pages counted in thousands."""
    return (
        f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID>"
        f"<Article><ArticleTitle>Record {pmid}</ArticleTitle></Article>"
        f"</MedlineCitation></PubmedArticle>"
    )


class _FakeResponse:
    """Only what the fetcher touches: `.text` and `.raise_for_status()`."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeEUtils:
    """E-utilities as measured live on 2026-08-20 (see `docs/DECISIONS.md`).

    Three behaviours, none of which a `MagicMock` list of canned pages can
    express. The first two are what these tests exercise; the third is the
    trap a regression walks into, since bmlib no longer asks a page that could
    meet it:

    * ``retstart`` indexes the *session's UID list*. A page carries the
      records of the slice it names — measured by comparing a page's record
      elements against esearch's own ``IdList``, which matched in order,
      ``<PubmedBookArticle>`` entries included.
    * The search backend refuses ``retstart`` above 9,998 with **HTTP 400**,
      not with an empty page: *"'retstart' cannot be larger than 9998. For
      PubMed, ESearch can only retrieve the first 9,999 records matching the
      query."*
    * A page whose window crosses record 9,999 is **silently clamped** to it —
      ``retstart=9500&retmax=500`` delivered 499 records, HTTP 200, no notice.

    *absent* names indexes whose UID yields no record element, which is the
    only way a page below the cap comes back short. That is the case
    delivery-driven paging would get wrong, so it is the case the stride is
    pinned against.
    """

    # Deliberately an independent literal, not `EFETCH_MAX_RETRIEVABLE - 1`: a
    # fake that moves with the constant under test can only ever confirm it,
    # and the cap±1 mutants die precisely because this does not move. Same rule
    # `sample_pdf_metadata_titles.py` follows in declining to import
    # `_titles.normalise`. Do not "deduplicate" these.
    MAX_RETSTART = 9998

    def __init__(self, count: int, *, absent: frozenset[int] = frozenset()) -> None:
        self.count = count
        self.absent = absent
        self.calls: list[dict] = []

    def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        params = dict(params or {})
        self.calls.append(params)
        if url == ESEARCH_URL:
            return _FakeResponse(_make_esearch_xml(self.count))
        retstart, retmax = int(params["retstart"]), int(params["retmax"])
        if retstart > self.MAX_RETSTART:
            return _FakeResponse(
                "<eFetchResult><ERROR>Search backend cannot retrieve history data."
                " Reason: Exception: 'retstart' cannot be larger than 9998.</ERROR>"
                "</eFetchResult>",
                status_code=400,
            )
        end = min(retstart + retmax, self.count, self.MAX_RETSTART + 1)
        body = "".join(
            _tiny_article(1000 + i) for i in range(retstart, end) if i not in self.absent
        )
        return _FakeResponse(f"<PubmedArticleSet>{body}</PubmedArticleSet>")

    @property
    def pages(self) -> list[dict]:
        """The efetch calls, in order."""
        return [c for c in self.calls if "retstart" in c]


class TestTheWalkIsIndexedByTheSetNotByWhatArrived:
    """Issue #96 — `retstart` offsets the UID list, so the stride is fixed.

    The concern #96 raised was that `range(0, count, EFETCH_PAGE_SIZE)` asks
    for records 0-499, 500-999, … whatever the previous page returned, so a
    short non-empty page would leave records never requested. Measuring the
    live API answered it the other way: the records between what arrived and
    the next offset *were* requested — they are UIDs the server had nothing to
    return for — and advancing by what arrived would re-request the tail of
    every short page instead, delivering duplicates and counting them as
    delivery, which is what would hide a real shortfall from
    `reconcile_delivery`.
    """

    def test_a_short_page_does_not_shift_the_following_offset(self):
        """Two absent records on page 1; page 2 still starts at 500."""
        client = _FakeEUtils(1500, absent=frozenset({3, 400}))

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert [page["retstart"] for page in client.pages] == [0, 500, 1000]
        assert result.status == "completed"

    def test_a_short_page_is_not_re_requested(self):
        """Every record arrives exactly once — the duplicate-delivery guard."""
        client = _FakeEUtils(1500, absent=frozenset({3, 400}))
        seen = []

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            fetch_pubmed(client, date(2024, 1, 15), on_record=lambda r: seen.append(r.pmid))

        assert len(seen) == len(set(seen)) == 1498


class TestPubMedServesOnlyTheFirstRecordsOfASession:
    """A day larger than the cap cannot complete through *one session*.

    NCBI's search backend serves the first 9,999 records of a history session
    and refuses the rest: *"To obtain more than 9,999 PubMed records, consider
    using EDirect…"*. bmlib queries `[Date - Publication]`, where that is not
    an edge case: measured 2026-08-20, every first-of-month day is over the cap
    (49,543-90,571 records, since a record carrying only a year and month is
    indexed at day 1) and every 1 January is 212,439-315,282.

    Before this guard the walk asked for record 10,000 anyway, got an HTTP 400
    whose body it discarded, and failed the day with
    ``Client error '400 Bad Request'`` — accurate, since the day genuinely
    cannot be completed through one session, but naming neither the cause nor
    the remedy, and re-fetching twenty pages of already-stored records on
    every later run to reach the same wall.

    One day-size did not fail, and it is the reason this guard closes a
    *silent* loss rather than only an unhelpful message: a day of exactly
    10,000 records never issues a ``retstart`` above 9,998, so it never met the
    400 at all — it walked to its natural end, was clamped to 9,999 delivered
    against 10,000 promised, cleared the shortfall floor and was recorded
    ``completed``. Durable, never re-offered, one record gone. See
    ``test_one_record_past_the_cap_is_already_too_many``.

    Issue #105 is what makes such a day fetchable: `_fetch_partitioned` now
    splits it into Entrez-date ranges that each fit and walks each as an
    ordinary session (see `TestAnOverCapDayIsFetchedRatherThanRefused`, which
    uses a fake whose ESearch count actually varies with the term). `_FakeEUtils`
    below reports the same count for *every* term regardless of range, which is
    not a realistic distribution — no query ever narrows it — so every day here
    lands in `_plan_partitions`'s other failure mode: it looks unsplittable,
    since halving the Entrez-date range never reduces the count either. The day
    still ends up `failed`, and still without a record stored or a session
    opened, which is what these tests continue to pin.
    """

    def test_a_day_over_the_cap_is_failed_not_completed_short(self):
        client = _FakeEUtils(12_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"

    def test_the_error_names_the_cap_and_why_it_could_not_be_split(self):
        """An operator cannot act on `400 Bad Request`; they can act on this."""
        client = _FakeEUtils(12_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.error is not None
        assert "12000" in result.error
        assert "9999" in result.error
        assert "cannot be split further" in result.error

    def test_no_records_are_stored_when_the_day_cannot_be_split(self):
        """The ladder's own planning probes never open a session or fetch a page.

        The day ends `failed` either way, and a failed day is re-offered on
        every later run, so the whole question is what the doomed run costs.
        Walking first would buy the first 9,999 records once and re-fetch them
        forever after: some 72 such days in a six-year backfill, ~3 GB per run,
        storing nothing new. The ladder itself costs several ESearches (it
        narrows the Entrez-date range looking for one that fits) — more than
        the single pre-#105 refusal did — but every one of them is a `retmax=0`
        count probe, never an `efetch` page.
        """
        client = _FakeEUtils(12_000)
        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=on_record)

        assert client.pages == []
        # More than the single pre-#105 refusal, but bounded. `_FakeEUtils`
        # reports 12,000 for every range regardless of width, which no real
        # backend can do: a range and its own half both holding the whole day
        # means `descend` measures a right child as large as its parent and
        # explores both halves to the floor, where a real day's right child
        # measures 0 and prunes. So this bound is the fake's worst case
        # (measured: 36), not the cost of a real unsplittable day — that one
        # puts every record on one Entrez date and costs 23 probes, against
        # 71 for a realistic 401,500-record day that does partition.
        # `TestTheEdatLadder` asserts the same kind of bound on
        # `_plan_partitions` directly.
        assert 1 < len(client.calls) < 40
        on_record.assert_not_called()
        assert result.record_count == 0

    def test_one_record_past_the_cap_is_already_too_many(self):
        """The boundary is a count, not an approximation: 10,000 is refused.

        Without this the cap could drift up by one and nothing would notice —
        a 10,000-record day would walk, come back 9,999 short by one, and
        complete on the shortfall note. 10,000 is the special count because
        ``range(0, 10000, 500)`` stops at 9,500: the walk never asks past the
        boundary, so no 400 fires and the silent clamp is the only thing that
        happens. 10,001 asks for ``retstart=10000`` and does get the 400.
        """
        client = _FakeEUtils(10_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert client.pages == []

    def test_a_day_exactly_at_the_cap_completes(self):
        """Negative control: the boundary itself is reachable."""
        client = _FakeEUtils(9999)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "completed"
        assert result.error is None
        assert result.record_count == 9999

    def test_a_day_that_is_a_multiple_of_the_cap_fails_by_being_unsplittable(self):
        """A day 30x the cap still fails loudly, not with a shortfall it invented.

        Had the walk run first, 9,999 delivered against 30,000 promised would
        be 33% — under `SHORTFALL_FAILURE_RATIO`, so the day would fail with
        "treated as truncated rather than short", describing a walk that
        stopped early when nothing stopped early. Under #105 the day is never
        walked at all: `_FakeEUtils` reports 30,000 for every Entrez-date
        range regardless of width, so the ladder can never find one that
        fits and the day fails as unsplittable instead.
        """
        client = _FakeEUtils(30_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        # "floor" is `reconcile_delivery`'s word for the shortfall message
        # (`fetchers/_reconcile.py`), which is what this must *not* be
        # diagnosed as. Cross-module wording, so if that message is reworded
        # this assertion stops guarding and `test_fetch_reconciliation.py` is
        # where the rename would be noticed.
        assert "floor" not in result.error
        assert "cannot be split further" in result.error

    def test_a_stall_below_the_cap_is_still_reported_as_a_stall(self):
        """The cap must not become the explanation for every failure."""
        client = _FakeEUtils(1500, absent=frozenset(range(500, 1000)))

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        assert "empty page" in result.error


# ---------------------------------------------------------------------------
# The search term is built separately from _esearch (issue #105 prep)
# ---------------------------------------------------------------------------


class TestTheSearchTermIsBuiltSeparately:
    """The ladder counts arbitrary terms, so term-building is not _esearch's job."""

    def test_day_term_is_the_publication_date_field(self):
        from bmlib.publications.fetchers.pubmed import _day_term

        assert _day_term(date(2024, 1, 1)) == '("2024/01/01"[Date - Publication])'

    def test_esearch_sends_the_term_it_was_given(self):
        from bmlib.publications.fetchers.pubmed import _esearch

        client = MagicMock()
        response = MagicMock()
        response.text = _make_esearch_xml(7)
        client.get.return_value = response

        count, web_env, query_key = _esearch(client, "SOME TERM", None)

        assert count == 7
        assert client.get.call_args.kwargs["params"]["term"] == "SOME TERM"

    def test_counting_does_not_open_a_history_session(self):
        from bmlib.publications.fetchers.pubmed import _esearch

        client = MagicMock()
        response = MagicMock()
        response.text = _make_esearch_xml(3)
        client.get.return_value = response

        _esearch(client, "SOME TERM", None, usehistory=False)

        assert "usehistory" not in client.get.call_args.kwargs["params"]


# ---------------------------------------------------------------------------
# The page walk is shared between a whole day and a sub-query (issue #105 prep)
# ---------------------------------------------------------------------------


class TestTheWalkIsSharedBetweenWholeDaysAndParts:
    """One loop, so the stride and the stall rule cannot drift apart."""

    def test_walk_reports_processed_delivered_and_no_stall(self):
        from bmlib.publications.fetchers.pubmed import _walk_session

        client = MagicMock()
        response = MagicMock()
        response.text = _make_efetch_xml(FULL_ARTICLE_XML, MINIMAL_ARTICLE_XML)
        client.get.return_value = response
        records = []

        outcome = _walk_session(
            client, "WE", "1", 2, on_record=records.append, api_key=None, rate_limit=0.0
        )

        assert (outcome.processed, outcome.delivered, outcome.stalled) == (2, 2, False)
        assert outcome.error is None
        assert len(records) == 2

    def test_an_empty_page_before_the_promise_is_met_is_a_stall(self):
        from bmlib.publications.fetchers.pubmed import _walk_session

        client = MagicMock()
        response = MagicMock()
        response.text = _make_efetch_xml()
        client.get.return_value = response

        outcome = _walk_session(
            client, "WE", "1", 5000, on_record=lambda r: None, api_key=None, rate_limit=0.0
        )

        assert outcome.stalled is True
        assert outcome.delivered == 0

    def test_a_failing_page_returns_an_error_rather_than_raising(self):
        from bmlib.publications.fetchers.pubmed import _walk_session

        client = MagicMock()
        client.get.side_effect = RuntimeError("connection reset")

        outcome = _walk_session(
            client, "WE", "1", 10, on_record=lambda r: None, api_key=None, rate_limit=0.0
        )

        assert outcome.error is not None
        assert "connection reset" in outcome.error


# ---------------------------------------------------------------------------
# The EDAT ladder plans an over-cap day into ranges that each fit (#105)
# ---------------------------------------------------------------------------


class TestTheEdatLadder:
    """Parts must tile the day exactly — coverage is the whole guarantee."""

    @staticmethod
    def _span(term: str) -> tuple[date, date]:
        """Return the EDAT range *term* restricts to."""
        m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
        assert m is not None, f"not an EDAT-range term: {term}"
        return (
            datetime.strptime(m.group(1), "%Y/%m/%d").date(),
            datetime.strptime(m.group(2), "%Y/%m/%d").date(),
        )

    @staticmethod
    def _counter(distribution: dict[date, int]):
        """Return a count_fn over a synthetic EDAT -> record-count distribution."""

        def count_fn(term: str) -> int:
            m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
            if m is None:  # the bare day term
                return sum(distribution.values())
            lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
            hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
            return sum(n for d, n in distribution.items() if lo <= d <= hi)

        return count_fn

    def test_parts_tile_the_day_exactly(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        distribution = {date(2023, 1, 1) + timedelta(days=i): 400 for i in range(100)}
        total = sum(distribution.values())  # 40,000 — four times the cap

        parts = _plan_partitions(self._counter(distribution), "DAY", total)

        assert sum(p.promised for p in parts) == total
        assert all(p.promised <= 9999 for p in parts)
        # Disjoint: no two parts share a date.
        spans = sorted((p.lo, p.hi) for p in parts)
        for (_, prev_hi), (next_lo, _) in zip(spans, spans[1:]):
            assert prev_hi < next_lo

    def test_an_empty_range_is_skipped_not_recursed(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        # Corrected from the brief (SDD ruling R3): a single Entrez day of
        # 20,000 exceeds the unpatched 9,999 cap and cannot be split further,
        # so descend() would reach lo == hi and raise _UnsplittableDayError before
        # the empty-range skip this test targets is ever exercised. Five days
        # of 4,000 keep the same 20,000 total while staying splittable.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 4000 for i in range(5)}
        calls: list[str] = []
        inner = self._counter(distribution)

        def counting(term: str) -> int:
            calls.append(term)
            return inner(term)

        parts = _plan_partitions(counting, "DAY", 20000)

        # Every part returned holds records; the empty centuries cost nothing
        # below themselves.
        assert all(p.promised > 0 for p in parts)
        assert len(calls) < 60

    def test_a_single_entrez_day_over_the_cap_raises(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions, _UnsplittableDayError

        distribution = {date(2023, 6, 1): 25000}

        with pytest.raises(_UnsplittableDayError) as exc_info:
            _plan_partitions(self._counter(distribution), "DAY", 25000)

        assert exc_info.value.edat_day == date(2023, 6, 1)
        assert exc_info.value.count == 25000

    def test_a_root_that_does_not_cover_the_day_raises(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions, _RootNotCoveringError

        # Corrected from the brief (SDD ruling R4): 9,000 per day, not
        # 10,000 — 10,000 exceeds the cap on its own, which would make each
        # single day unsplittable and mask what the root probe is being
        # tested for. Two days of 9,000 (18,000 in the root) against a day
        # claiming 30,000: the root probe must raise before any descent.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 9000 for i in range(2)}

        with pytest.raises(_RootNotCoveringError):
            _plan_partitions(self._counter(distribution), "DAY", 30000)

    def test_a_root_that_is_long_proceeds(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        # A record indexed between the two counts lands inside the range.
        # Corrected from the brief per the same ruling as above: 9,000 per
        # day (18,000 total) against a day claiming 17,999.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 9000 for i in range(2)}

        parts = _plan_partitions(self._counter(distribution), "DAY", 17999)

        assert sum(p.promised for p in parts) == 18000

    def test_a_left_child_larger_than_its_parent_measures_the_right_instead_of_dropping_it(
        self,
    ):
        # `descend` derives every right-hand child by subtraction, which is
        # only sound while both counts describe the same instant. Planning
        # issues one ESearch per split, so a range whose count *grows* between
        # its parent's probe and its own leaves `n - left` at or below zero —
        # and the `n <= 0` arm that legitimately skips a measured-empty range
        # would then discard a range nobody ever counted. Every part planned
        # around it reconciles perfectly, so the loss surfaces only in the
        # day total, where anything under the 50% floor completes on a note:
        # `completed` is durable, so those records are gone for good.
        #
        # Measuring the right child costs one ESearch on a path that should
        # never be taken, and is the same answer the root probe gives to the
        # same disagreement.
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        # `known_count` supplies the stale parent directly: 11,000 is what the
        # day held when its count was taken, while the 2023 cluster has since
        # grown to 12,000. The first plan reaches the identical state through
        # `n - left` alone, since every right-hand child's count is derived.
        populated = {
            date(2023, 6, 1): 4000,
            date(2023, 6, 2): 4000,
            date(2023, 6, 3): 4000,
            date(2088, 7, 1): 5000,
        }

        def count_fn(term: str) -> int:
            lo, hi = self._span(term)
            return sum(v for d, v in populated.items() if lo <= d <= hi)

        parts = _plan_partitions(count_fn, "DAY", 11000, known_count=11000)

        covering = [p for p in parts if p.lo <= date(2088, 7, 1) <= p.hi]
        assert covering, "the range holding the 2088 records was dropped from the plan"
        assert sum(p.promised for p in covering) == 5000

    def test_a_right_child_derived_to_exactly_zero_is_measured_not_skipped(self):
        # The boundary case of the one above, and the one that actually
        # reaches production: drift that consumes the right child exactly
        # leaves `n - left == 0`, which is indistinguishable from the
        # measured-empty range the `n <= 0` arm exists to prune cheaply.
        #
        # A derived zero is the only wrong derivation that cannot heal. A
        # derived count that is merely too low still yields a part, and that
        # part re-counts itself when its session opens; a derived zero yields
        # no part at all, so the range is never visited and nothing downstream
        # can notice. Measuring it costs one ESearch on the two or three nodes
        # per day whose parent's records all sit in the left half.
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        populated = {
            date(2023, 6, 1): 4000,
            date(2023, 6, 2): 4000,
            date(2023, 6, 3): 4000,
            date(2088, 7, 1): 5000,
        }

        def count_fn(term: str) -> int:
            lo, hi = self._span(term)
            return sum(v for d, v in populated.items() if lo <= d <= hi)

        # 12,000 is what the day held when its count was taken — exactly the
        # 2023 cluster, with the 2088 records indexed into it since.
        parts = _plan_partitions(count_fn, "DAY", 12000, known_count=12000)

        covering = [p for p in parts if p.lo <= date(2088, 7, 1) <= p.hi]
        assert covering, "the range holding the 2088 records was dropped from the plan"
        assert sum(p.promised for p in covering) == 5000

    def test_a_genuinely_empty_right_child_still_yields_no_part(self):
        # The negative control for the two above: measuring a derived zero
        # must not turn an empty range into a part, or every plan would carry
        # the structurally empty centuries at either end of the root.
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        populated = {date(2023, 6, 1) + timedelta(days=i): 4000 for i in range(3)}

        def count_fn(term: str) -> int:
            lo, hi = self._span(term)
            return sum(v for d, v in populated.items() if lo <= d <= hi)

        parts = _plan_partitions(count_fn, "DAY", 12000, probe_root=False)

        assert all(p.promised > 0 for p in parts)
        assert sum(p.promised for p in parts) == 12000

    def test_the_part_key_format_is_pinned(self):
        from bmlib.publications.fetchers.pubmed import _part_key

        # The skip rule is a string comparison: a silent format change costs a
        # full re-fetch of every unfinished day, with nothing raised.
        assert _part_key(date(2023, 4, 10), date(2023, 8, 31)) == "edat:2023-04-10:2023-08-31"


# ---------------------------------------------------------------------------
# #105: fetching an over-cap day as parts (`_fetch_partitioned`)
# ---------------------------------------------------------------------------


def _eutils_client(
    count_fn, *, article_xml=MINIMAL_ARTICLE_XML, session_count_fn=None, max_calls=300
):
    """A fake E-utilities client: ESearch answers from *count_fn*, EFetch slices.

    EFetch synthesises the page its parameters name, exactly as the live
    backend does — the slice of the session's UID list at ``retstart``, capped
    at ``retmax``. Modelling that rather than a fixed list of bodies is what
    lets a test exercise a ladder whose terms it never has to spell out.

    *session_count_fn*, when given, answers every ESearch that carries the
    ``usehistory`` parameter — the day-level search and each part's own
    fetch-time search, both of which open a session — while *count_fn*
    answers the planning probes `_plan_partitions` issues with
    ``usehistory=False``. `_esearch` omits the ``usehistory`` parameter
    entirely rather than sending it false, which is what makes the two
    distinguishable here, and what makes a part's fetch-time re-check (the
    count NCBI reports once the session actually opens, as opposed to the
    count planning saw) testable independently. Without *session_count_fn*,
    both kinds share *count_fn*.

    *max_calls* is a hard backstop, not a tuning knob: every legitimate use
    in this file stays under a few dozen calls, so 300 is never meant to be
    approached. Its job is to convert a re-partition loop that regressed back
    to infinite (dropping `known_count` in favour of a fresh recount — see
    `_plan_partitions`'s docstring) into a fast, loud `RuntimeError` from
    inside the mock. `time.sleep` is patched to a no-op in every test that
    uses this fake, so nothing else would ever stop such a loop or even slow
    it down — an `assert client.get.call_count < N` after the call can only
    fire if `fetch_pubmed` returns first, and an infinite loop never returns.
    """
    import itertools

    sessions: dict[str, int] = {}
    counter = itertools.count(1)
    calls = itertools.count(1)

    def get(url, params=None):
        if next(calls) > max_calls:
            raise RuntimeError(
                f"client.get called more than {max_calls} times — suspected infinite"
                " re-partition loop rather than a genuine over-cap day"
            )
        params = params or {}
        response = MagicMock()
        if url == ESEARCH_URL:
            key = str(next(counter))
            fn = count_fn
            if session_count_fn is not None and "usehistory" in params:
                fn = session_count_fn
            sessions[key] = fn(params["term"])
            response.text = _make_esearch_xml(sessions[key], query_key=key)
            return response
        if url == EFETCH_URL:
            total = sessions[params["query_key"]]
            n = max(0, min(int(params["retmax"]), total - int(params["retstart"])))
            response.text = _make_efetch_xml(*([article_xml] * n))
            return response
        raise AssertionError(f"unexpected URL {url}")

    client = MagicMock()
    client.get.side_effect = get
    return client


def _distribution_counter(distribution: dict[date, int]):
    """Return a count_fn over a synthetic EDAT -> record-count distribution."""

    def count_fn(term: str) -> int:
        m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
        if m is None:
            return sum(distribution.values())
        lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
        hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
        return sum(n for d, n in distribution.items() if lo <= d <= hi)

    return count_fn


@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 2)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 2)
class TestAnOverCapDayIsFetchedRatherThanRefused:
    """#105: the day that used to be refused outright is now fetched in parts."""

    def test_every_record_of_an_over_cap_day_arrives(self):
        distribution = {date(2023, 6, 1): 2, date(2023, 6, 2): 2, date(2023, 6, 3): 2}
        client = _eutils_client(_distribution_counter(distribution))
        records = []

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=records.append, api_key=None)

        assert result.status == "completed"
        assert result.record_count == 6
        assert len(records) == 6

    def test_an_unsplittable_entrez_day_fails_the_day_and_names_it(self):
        distribution = {date(2023, 6, 1): 5}
        client = _eutils_client(_distribution_counter(distribution))

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "2023-06-01" in result.error
        assert "cannot be split further" in result.error

    def test_a_root_that_does_not_cover_fails_the_day(self):
        # The bare day term reports 8; the root EDAT range holds only 4.
        def count_fn(term: str) -> int:
            if "[EDAT]" not in term:
                return 8
            return _distribution_counter({date(2023, 6, 1): 2, date(2023, 6, 2): 2})(term)

        client = _eutils_client(count_fn)

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "lie outside the ladder" in result.error

    def test_a_regrown_part_that_is_already_a_single_day_fails_unsplittable(self):
        # Planning sees the part fitting; the part's own ESearch then reports
        # more than a session can serve, for a part that has already narrowed
        # to a single Entrez day. It cannot be split any further, so the day
        # fails loudly rather than walking into the silent clamp.
        #
        # This exercises the raise-at-`lo == hi` branch of the re-split, not
        # the "split into more than one part" branch — see
        # `test_a_part_that_grew_past_the_cap_is_split_again` below for that.
        client = _eutils_client(
            _distribution_counter({date(2023, 6, 1): 2, date(2023, 6, 2): 2}),
            session_count_fn=lambda term: 4,
        )

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "cannot be split further" in result.error

    def test_a_part_that_grew_past_the_cap_is_split_again(self):
        # Four Entrez days of one record each, cap patched to 2: planning's
        # binary search stops as soon as a range's count fits, which happens
        # here at a *pair* of days rather than at a single one (verified by
        # calling `_plan_partitions` directly against this distribution) — so
        # each initial part spans two populated days, not one, and re-split
        # actually has more than a single day to divide.
        #
        # The growth is applied to the *distribution*, once, when the first
        # part's own session opens: that part then counts 3 against a cap of
        # 2 and is re-planned, and the re-plan's probes see the same grown
        # state its `known_count` came from. Inflating only the session's
        # answer — the shape this fake used before — models no real backend:
        # the extra record exists in no Entrez date, so the ladder's
        # `right = parent - left` subtraction hands the surplus to a range
        # that truly holds nothing, and that phantom part then reports 0 at
        # its own session and fails the day (which is the #105 F2 rule
        # working, not this path breaking — see
        # `test_a_part_reporting_zero_after_planning_measured_it_fails_the_day`).
        distribution = {
            date(2023, 6, 1): 1,
            date(2023, 6, 2): 1,
            date(2023, 6, 3): 1,
            date(2023, 6, 4): 1,
        }
        base = _distribution_counter(distribution)
        grown: list[bool] = []

        def session_count_fn(term: str) -> int:
            m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
            if m is None:
                return base(term)
            lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
            hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
            populated = [d for d in distribution if lo <= d <= hi]
            if len(populated) >= 2 and not grown:
                # The first part's session: 2023-06-01 gained a record
                # between planning and now, so this part holds 3 where
                # planning measured 2 — over the patched cap.
                grown.append(True)
                distribution[date(2023, 6, 1)] += 1
            return base(term)

        client = _eutils_client(base, session_count_fn=session_count_fn)
        records = []

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=records.append, api_key=None)

        # Every record still arrives — re-splitting is invisible to the
        # caller — and the day completes rather than failing. Five, not four:
        # the record the part gained is fetched too, which is the whole point
        # of re-splitting rather than walking the part as planned.
        assert grown, "setup: the first part must have grown past the cap"
        assert result.status == "completed"
        assert result.record_count == 5
        assert len(records) == 5

    def test_a_disagreeing_recount_terminates_instead_of_looping(self):
        # The session count says over-cap; a fresh planning count of the same
        # range says it fits. Without `known_count` driving the re-plan off
        # the count that triggered it, a fresh recount here would form a
        # single-part plan identical to the part just popped, which would be
        # re-queued, re-fetched, re-found over-cap, and looped forever —
        # `_eutils_client`'s own call-count backstop is what turns that into
        # a fast failure here instead of a hang (`time.sleep` is a no-op and
        # `pyproject.toml` sets no pytest timeout, so nothing else would).
        client = _eutils_client(
            _distribution_counter({date(2023, 6, 1): 2, date(2023, 6, 2): 2}),
            session_count_fn=lambda term: 4,
        )

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert client.get.call_count < 100, "the re-plan must narrow, not repeat"

    def test_progress_reports_the_days_total_not_a_parts(self):
        distribution = {date(2023, 6, 1): 2, date(2023, 6, 2): 2}
        client = _eutils_client(_distribution_counter(distribution))
        seen: list[SyncProgress] = []

        fetch_pubmed(
            client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_progress=seen.append,
            api_key=None,
        )

        assert seen, "expected at least one progress report"
        assert {p.records_total for p in seen} == {4}
        assert [p.records_processed for p in seen] == sorted(p.records_processed for p in seen)


class TestTheUnderCapPathIsUnchanged:
    """A negative control: an ordinary day must not pay for the ladder."""

    def test_an_ordinary_day_issues_no_partitioning_search(self):
        client = _eutils_client(lambda term: 2)

        fetch_pubmed(client, date(2024, 3, 15), on_record=lambda r: None)

        terms = [
            c.kwargs["params"]["term"]
            for c in client.get.call_args_list
            if "term" in c.kwargs.get("params", {})
        ]
        assert terms == ['("2024/03/15"[Date - Publication])']


# ---------------------------------------------------------------------------
# #105 review: a lost or skipped part must fail the day, not complete it
# ---------------------------------------------------------------------------


def _partitioned_client(distribution: dict[date, int], *, part_overrides=None):
    """A fake for scenarios needing per-part control over one part's behaviour.

    Like `_eutils_client`, every planning probe (`usehistory=False`) and the
    day-level search answer from *distribution*. Unlike `_eutils_client`, a
    session-opening ESearch whose EDAT range contains exactly one populated
    date consults *part_overrides* (a mapping from that date to a dict of
    overrides) before falling back to the real distribution — which is what
    lets one part of a multi-part day misbehave while the others fetch
    normally, the shape every test in this section needs.

    Recognised overrides, all optional:

    * ``count``: the ESearch-declared count for that part (default: the real
      distribution count).
    * ``no_session``: if true, the ESearch response omits ``WebEnv`` and
      ``QueryKey`` entirely rather than reporting them.
    * ``efetch_pages``: a list of raw EFetch response bodies, served in order
      to that part's successive EFetch calls, in place of the normal
      auto-generated pages — for scripting a stall (an empty page before the
      count is met) or a partial-but-nonzero shortfall.
    * ``efetch_error``: if true, EFetch returns a body `_efetch_page` refuses
      to parse (an ``<eFetchResult><ERROR>`` document), in place of a page.

    A range containing zero or more-than-one populated date always falls back
    to the real distribution count — the empty-range skips and the
    multi-day-to-single-day narrowing that planning performs on its own.
    """
    import itertools

    part_overrides = part_overrides or {}
    sessions: dict[str, dict] = {}
    counter = itertools.count(1)

    def which_day(term: str) -> date | None:
        m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
        if m is None:
            return None
        lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
        hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
        days = [d for d in distribution if lo <= d <= hi]
        return days[0] if len(days) == 1 else None

    def real_count(term: str) -> int:
        m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
        if m is None:
            return sum(distribution.values())
        lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
        hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
        return sum(n for d, n in distribution.items() if lo <= d <= hi)

    def get(url, params=None):
        params = params or {}
        response = MagicMock()
        if url == ESEARCH_URL:
            key = str(next(counter))
            if "usehistory" in params:
                day = which_day(params["term"])
                override = part_overrides.get(day, {}) if day is not None else {}
                count = override.get("count", real_count(params["term"]))
                if override.get("no_session"):
                    response.text = f"<eSearchResult><Count>{count}</Count></eSearchResult>"
                else:
                    response.text = _make_esearch_xml(count, query_key=key)
                sessions[key] = {"override": override, "total": count, "page_idx": 0}
            else:
                response.text = _make_esearch_xml(real_count(params["term"]))
            return response
        if url == EFETCH_URL:
            state = sessions[params["query_key"]]
            override = state["override"]
            pages = override.get("efetch_pages")
            if pages is not None:
                idx = state["page_idx"]
                state["page_idx"] += 1
                if idx >= len(pages):
                    raise AssertionError("efetch called past the scripted pages")
                response.text = pages[idx]
                return response
            if override.get("efetch_error"):
                response.text = "<eFetchResult><ERROR>boom</ERROR></eFetchResult>"
                return response
            total = state["total"]
            n = max(0, min(int(params["retmax"]), total - int(params["retstart"])))
            response.text = _make_efetch_xml(*([MINIMAL_ARTICLE_XML] * n))
            return response
        raise AssertionError(f"unexpected URL {url}")

    client = MagicMock()
    client.get.side_effect = get
    return client


@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
class TestALostOrSkippedPartFailsTheDay:
    """A regression that loses one part of a multi-part day must not complete it.

    `sync()` never re-offers a day recorded `completed`, so each scenario
    here is a distinct way `_fetch_partitioned` could silently record success
    over a part that did not actually deliver — and each must fail the day.
    """

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 1)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 1)
    def test_a_stalled_part_fails_the_day(self):
        distribution = {date(2023, 6, 1): 1, date(2023, 6, 2): 1}
        client = _partitioned_client(
            distribution,
            part_overrides={date(2023, 6, 2): {"efetch_pages": [_make_efetch_xml()]}},
        )

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "2023-06-02" in result.error
        assert "stopped short" in result.error
        # The other part's record still arrived before the stall was found.
        assert result.record_count == 1

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 5)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 6)
    def test_a_parts_own_shortfall_below_the_floor_fails_the_day(self):
        # Promised 6 (two pages: retmax 5 then 1); both pages deliver a
        # nonzero-but-partial count, so the part-level walk ends naturally
        # rather than stalling, and still comes up short of the 50% floor —
        # a different `reconcile_delivery` branch than the stall above.
        distribution = {date(2023, 6, 1): 6, date(2023, 6, 2): 1}
        client = _partitioned_client(
            distribution,
            part_overrides={
                date(2023, 6, 1): {
                    "efetch_pages": [
                        _make_efetch_xml(MINIMAL_ARTICLE_XML),
                        _make_efetch_xml(MINIMAL_ARTICLE_XML),
                    ]
                }
            },
        )

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "2023-06-01" in result.error
        assert "50% floor" in result.error

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 1)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 1)
    def test_a_parts_efetch_error_fails_the_day(self):
        distribution = {date(2023, 6, 1): 1, date(2023, 6, 2): 1}
        client = _partitioned_client(
            distribution,
            part_overrides={date(2023, 6, 2): {"efetch_error": True}},
        )

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "2023-06-02" in result.error
        assert "boom" in result.error

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 1)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 1)
    def test_a_part_without_a_history_session_fails_the_day(self):
        distribution = {date(2023, 6, 1): 1, date(2023, 6, 2): 1}
        client = _partitioned_client(
            distribution,
            part_overrides={date(2023, 6, 2): {"no_session": True}},
        )

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "2023-06-02" in result.error
        assert "without a history session" in result.error

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 1)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 1)
    def test_a_part_reporting_zero_after_planning_measured_it_fails_the_day(self):
        # Planning measured this range at one record; the part's own ESearch
        # now reports none. Dropping it there was silent (#105 review, F2):
        # three such parts of a five-part day still deliver 2 of 5, and a
        # bigger day's proportion clears the day-level floor outright, so the
        # day would be recorded `completed`, never re-offered, and those
        # records permanently absent behind at most one shortfall note. The
        # asymmetry is the tell: a part *delivering* 0 of 1 fails the day, so
        # a part *claiming* 0 having just been measured at 1 cannot pass.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 1 for i in range(5)}
        populated = sorted(distribution)
        overrides = {d: {"count": 0} for d in populated[1:4]}
        client = _partitioned_client(distribution, part_overrides=overrides)

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        # Part-level, and it names the part: the day fails on the first such
        # part rather than waiting for the day total, so only the part before
        # it was walked.
        assert "part edat:" in result.error
        assert "delivered 0 of 1" in result.error
        assert result.record_count == 1

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 4)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 4)
    def test_the_day_level_reconcile_catches_a_cumulative_loss_no_part_reported(self):
        # Five four-record days; four of them report a count of 1 when their
        # own session opens and deliver exactly that. Every part therefore
        # reconciles *perfectly against its own count* — no part-level rule
        # fires — while the day has delivered 8 of the 20 records its own
        # count promised, below the 50% floor. Only the final, day-level
        # `reconcile_delivery` call can catch that: it is the guard against a
        # regression that loses parts of a day without any part reporting a
        # problem, and it must not be reachable only through some part-level
        # rule that happened to fire first.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 4 for i in range(5)}
        overrides = {d: {"count": 1} for d in sorted(distribution)[:4]}
        client = _partitioned_client(distribution, part_overrides=overrides)

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "50% floor" in result.error
        # Day-level, not part-level: no single part's own reconcile fired.
        assert "part" not in result.error
        assert result.record_count == 8


# ---------------------------------------------------------------------------
# #105 review, F3: a planning ESearch failure is a failed FetchResult, not a
# raise — the under-cap path's answer to the same transient
# ---------------------------------------------------------------------------


@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 2)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 2)
class TestAPlanningFailureIsReturnedNotRaised:
    """Planning is ESearch, so it fails the way every other request does.

    `sync()` absorbs a raise and fails the day either way, so no records are
    at stake — but the under-cap path returns `FetchResult(status="failed")`
    for exactly these transients, and one public function must not answer the
    same connection reset with a return value or an exception depending on
    how large the day happened to be. Each test would *error* rather than
    fail if the exception escaped, which is the whole assertion.
    """

    def test_an_error_document_during_planning_fails_the_day(self):
        # NCBI answers a rejected search with HTTP 200 and an <ERROR>
        # document, which `_esearch` reports as a `ValueError`.
        def get(url, params=None):
            params = params or {}
            response = MagicMock()
            if url != ESEARCH_URL:
                raise AssertionError("nothing may be fetched when planning failed")
            if "usehistory" in params:
                response.text = _make_esearch_xml(5000, query_key="1")
            else:
                response.text = (
                    "<eSearchResult><ERROR>Search Backend failed</ERROR></eSearchResult>"
                )
            return response

        client = MagicMock()
        client.get.side_effect = get

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "planning the Entrez-date parts failed" in result.error
        assert "Search Backend failed" in result.error
        assert result.record_count == 0

    def test_a_transport_error_during_planning_fails_the_day(self):
        def get(url, params=None):
            params = params or {}
            if url == ESEARCH_URL and "usehistory" in params:
                response = MagicMock()
                response.text = _make_esearch_xml(5000, query_key="1")
                return response
            raise ConnectionError("connection reset")

        client = MagicMock()
        client.get.side_effect = get

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "ConnectionError: connection reset" in result.error

    def test_a_transport_error_while_re_partitioning_fails_the_day(self):
        # The second planning phase: a part whose own session reports it over
        # the cap is re-planned, and that descent issues counting probes of
        # its own. Same rule, a different call site.
        distribution = {
            date(2023, 6, 1): 1,
            date(2023, 6, 2): 1,
            date(2023, 6, 3): 1,
            date(2023, 6, 4): 1,
        }
        base = _distribution_counter(distribution)
        re_planning: list[bool] = []

        def count_fn(term: str) -> int:
            if re_planning:
                raise ConnectionError("connection reset")
            return base(term)

        def session_count_fn(term: str) -> int:
            m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
            if m is None:
                return base(term)
            lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
            hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
            populated = [d for d in distribution if lo <= d <= hi]
            if len(populated) >= 2:
                # Over the patched cap, so this part is re-planned.
                re_planning.append(True)
                return 3
            return base(term)

        client = _eutils_client(count_fn, session_count_fn=session_count_fn)

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert re_planning, "setup: a part must have reported itself over the cap"
        assert result.status == "failed"
        assert "re-partitioning part edat:" in result.error
        assert "ConnectionError: connection reset" in result.error


# ---------------------------------------------------------------------------
# Task 7 review, F1: a noted (short-but-above-floor) part is flushed but not
# checkpointed
# ---------------------------------------------------------------------------


@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 4)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 4)
class TestANotedPartIsFlushedButNotCheckpointed:
    """A part that reconciled with a note is flushed, then re-walked, not skipped.

    Two rules that must not be collapsed into one. **Not checkpointed:**
    checkpointing a part that delivered short of its own promise — but not
    short enough to fail — would let a later resumed run skip it and credit
    the full ``promised`` it never actually delivered: silently manufacturing
    the very records the note reported missing, with no note surviving into
    that resumed run's ``FetchResult`` to say so (the note that *was* raised
    dies with whatever aborted the run that raised it). Leaving a noted part
    off the checkpoint means whichever run finally completes the day re-walks
    it and carries its note honestly.

    **Still flushed:** ``on_part_finished`` is the only thing that empties the
    caller's record buffer, so reporting a noted part not at all would make
    the per-part memory bound conditional on the source behaving — a degraded
    NCBI delivering 37 noted parts of a 242,216-record day would hold every
    one of those records in memory at once (#105 review, F1).
    """

    # date(2023, 6, 1) promises 4; EFETCH_PAGE_SIZE == EFETCH_MAX_RETRIEVABLE
    # == 4 here, so its one page's retmax covers the whole promise and the
    # walk ends naturally rather than stalling when that page is scripted to
    # deliver only 3. 3/4 = 75%, above the 50% floor, so this is a *note*,
    # not a failure — the branch both tests below are about. date(2023, 6, 2)
    # promises 1 and is left to fetch normally, delivering it in full, so this
    # day has exactly one noted part and one clean part, in that order.
    DISTRIBUTION = {date(2023, 6, 1): 4, date(2023, 6, 2): 1}

    @classmethod
    def _client(cls):
        return _partitioned_client(
            cls.DISTRIBUTION,
            part_overrides={
                date(2023, 6, 1): {"efetch_pages": [_make_efetch_xml(*([MINIMAL_ARTICLE_XML] * 3))]}
            },
        )

    def test_a_noted_part_is_not_checkpointed_but_a_clean_part_is(self):
        reported: list[PartCheckpoint | None] = []

        result = fetch_pubmed(
            self._client(),
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_finished=reported.append,
        )

        assert result.status == "completed"
        assert result.note is not None, "the day must still surface the part's shortfall"
        checkpoints = [c for c in reported if c is not None]
        assert len(checkpoints) == 1, "only the clean part may be checkpointed"
        assert checkpoints[0].promised == 1, "the noted part (promised 4) must not be checkpointed"

    def test_a_noted_parts_records_are_still_flushed(self):
        """The buffer is drained at the noted part's own boundary, not later.

        Modelled on what `sync()` does with the callback: records accumulate
        until it fires, and it empties them. Asserting on the buffer's size
        at each call is the only way to see the difference — afterwards, a
        run that flushed per part and one that held everything to the end
        both leave every record delivered.
        """
        buffered: list[FetchedRecord] = []
        flushed: list[tuple[int, PartCheckpoint | None]] = []

        def on_part_finished(checkpoint: PartCheckpoint | None) -> None:
            flushed.append((len(buffered), checkpoint))
            buffered.clear()

        result = fetch_pubmed(
            self._client(),
            date(2024, 1, 1),
            on_record=buffered.append,
            on_part_finished=on_part_finished,
        )

        assert result.status == "completed"
        assert [n for n, _ in flushed] == [3, 1], (
            "each part's records must be flushed at its own boundary, the noted one included"
        )
        assert flushed[0][1] is None, "the noted part is flushed with no checkpoint"
        assert flushed[1][1] is not None, "the clean part is flushed with one"
        assert buffered == [], "nothing may be left holding records after the last part"


# ---------------------------------------------------------------------------
# Task 7 review, F2: on_part_finished must fire only after a part fully
# reconciles — never for a part that errored or that failed reconciliation
# ---------------------------------------------------------------------------


@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
class TestAFailedPartIsNotCheckpointed:
    """`on_part_finished` must never fire for a part that did not reconcile.

    A checkpoint written for a part that errored, or whose delivery fell
    below the reconciliation floor, would let a later resumed run skip that
    part and credit records it never actually confirmed — durably recording
    a day as complete over a part that was never shown to have finished.
    Both scenarios here reuse the exact setups
    `TestALostOrSkippedPartFailsTheDay` already uses to fail the day; this
    class asserts the added constraint that neither one ever checkpoints.
    """

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 1)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 1)
    def test_an_efetch_error_checkpoints_nothing(self):
        # The override targets the *first*-processed date (parts walk in
        # ascending date order — see `TestResumingAnOverCapDay`), so nothing
        # has reconciled and been checkpointed before this part fails. Using
        # the later date instead would still correctly leave the failing
        # part off the checkpoint, but `done` would legitimately hold the
        # earlier, cleanly-reconciled part — a false failure of this test,
        # not a bug in the code.
        distribution = {date(2023, 6, 1): 1, date(2023, 6, 2): 1}
        client = _partitioned_client(
            distribution,
            part_overrides={date(2023, 6, 1): {"efetch_error": True}},
        )
        done: list[PartCheckpoint] = []

        result = fetch_pubmed(
            client, date(2024, 1, 1), on_record=lambda r: None, on_part_finished=done.append
        )

        assert result.status == "failed"
        assert done == [], "a part that errored mid-walk must not be checkpointed"

    @patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 5)
    @patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 6)
    def test_a_reconciliation_failure_checkpoints_nothing(self):
        # Same scripted shortfall as
        # TestALostOrSkippedPartFailsTheDay
        # .test_a_parts_own_shortfall_below_the_floor_fails_the_day:
        # both pages deliver a nonzero-but-partial count, so the walk ends
        # naturally rather than stalling, and still comes up short of the 50%
        # floor — reconciliation failure, not an error. The override targets
        # date(2023, 6, 1), the first-processed part (see the ordering note
        # in test_an_efetch_error_checkpoints_nothing above), so `done`
        # staying empty is attributable only to this failure.
        distribution = {date(2023, 6, 1): 6, date(2023, 6, 2): 1}
        client = _partitioned_client(
            distribution,
            part_overrides={
                date(2023, 6, 1): {
                    "efetch_pages": [
                        _make_efetch_xml(MINIMAL_ARTICLE_XML),
                        _make_efetch_xml(MINIMAL_ARTICLE_XML),
                    ]
                }
            },
        )
        done: list[PartCheckpoint] = []

        result = fetch_pubmed(
            client, date(2024, 1, 1), on_record=lambda r: None, on_part_finished=done.append
        )

        assert result.status == "failed"
        assert done == [], "a part whose reconciliation failed must not be checkpointed"


# ---------------------------------------------------------------------------
# Task 7: resuming an over-cap day (skip a checkpointed part, credit it)
# ---------------------------------------------------------------------------


@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 2)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 2)
class TestResumingAnOverCapDay:
    """A part finished by an earlier run is not fetched twice.

    The ladder halves a range and stops as soon as a part fits, so a leaf's
    key is never a literal like ``"edat:2023-06-01:2023-06-01"`` in general —
    real leaves against a live distribution can span months or years. These
    tests never hard-code a key or a range: every case discovers the real
    parts from a first ("prior") run of :func:`fetch_pubmed` against this
    class's own fake client, and drives the resume case from what that run
    actually reported through ``on_part_finished``.
    """

    # Deliberately asymmetric (2 vs 1, not 2 vs 2): `reconcile_delivery`'s
    # shortfall floor is exclusive at 50% (delivering exactly half a day
    # passes with a note, not a failure — see `_reconcile.py`), so checkpointing
    # a part worth exactly half the day would let a broken credit line pass
    # `test_a_skipped_part_is_credited_to_the_day_total` by accident. Here the
    # ladder's leaves land on single Entrez days matching these counts (pinned
    # by the same ordering `test_a_checkpointed_part_is_not_fetched_again`
    # relies on: parts are produced, and so checkpointed, in ascending-date
    # order), so the part skipped first is the 2-of-3 part — losing it
    # uncredited drops delivery to 1 of 3, under the floor.
    DISTRIBUTION = {date(2023, 6, 1): 2, date(2023, 6, 2): 1}

    def test_a_completed_part_is_reported_and_can_be_skipped(self):
        client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []

        result = fetch_pubmed(
            client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_finished=done.append,
        )

        assert result.status == "completed"
        assert done, "an over-cap day must checkpoint at least one part"
        assert all(c.part_scheme == PART_SCHEME for c in done)
        assert all(c.record_count == c.promised for c in done)
        assert sum(c.record_count for c in done) == sum(self.DISTRIBUTION.values())

    def test_a_checkpointed_part_is_not_fetched_again(self):
        first_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []
        first_records: list[FetchedRecord] = []
        fetch_pubmed(
            first_client,
            date(2024, 1, 1),
            on_record=first_records.append,
            on_part_finished=done.append,
        )
        assert done, "setup: an over-cap day must checkpoint at least one part"
        first_efetch_calls = [c for c in first_client.get.call_args_list if c.args[0] == EFETCH_URL]

        second_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        second_records: list[FetchedRecord] = []
        prior = {done[0].part_key: done[0]}

        result = fetch_pubmed(
            second_client,
            date(2024, 1, 1),
            on_record=second_records.append,
            completed_parts=prior,
        )

        assert result.status == "completed"
        second_efetch_calls = [
            c for c in second_client.get.call_args_list if c.args[0] == EFETCH_URL
        ]
        # EFETCH_PAGE_SIZE == EFETCH_MAX_RETRIEVABLE == 2 in this class, so
        # every part fits in exactly one EFetch page: skipping done[0] must
        # remove exactly its own record count and exactly one EFetch call —
        # not merely fewer of each, which a fetch-then-discard implementation
        # would also satisfy.
        assert len(second_records) == len(first_records) - done[0].record_count, (
            "the checkpointed part must not be re-fetched"
        )
        assert len(second_efetch_calls) == len(first_efetch_calls) - 1, (
            "skipping a part must skip exactly its own EFetch call"
        )

    def test_a_skipped_part_is_credited_to_the_day_total(self):
        # Without crediting, the second run delivers only the unskipped
        # part's records against the day's full count and fails the floor.
        # This test is the negative control for that: it must complete.
        first_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []
        fetch_pubmed(
            first_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_finished=done.append,
        )
        assert done, "setup: an over-cap day must checkpoint at least one part"

        second_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        prior = {done[0].part_key: done[0]}

        result = fetch_pubmed(
            second_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            completed_parts=prior,
        )

        assert result.status == "completed"
        assert result.error is None

    def test_a_part_whose_count_moved_is_fetched_again(self):
        # The stored promise no longer matches what this run's plan reports
        # for the same part key. Skipping on key alone would lose whatever
        # the part gained since it was checkpointed, which is the whole
        # reason the rule compares counts and not just keys.
        first_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []
        fetch_pubmed(
            first_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_finished=done.append,
        )
        assert done, "setup: an over-cap day must checkpoint at least one part"
        moved = dataclasses.replace(done[0], promised=done[0].promised - 1)

        second_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        records: list[FetchedRecord] = []
        prior = {moved.part_key: moved}

        result = fetch_pubmed(
            second_client,
            date(2024, 1, 1),
            on_record=records.append,
            completed_parts=prior,
        )

        assert result.status == "completed"
        assert len(records) == sum(self.DISTRIBUTION.values()), (
            "the moved part must be re-fetched, not skipped"
        )

    def test_a_skipped_part_is_reported_and_a_fetched_one_is_not(self):
        # `sync()` credits a resumed day's stored parts by summing exactly the
        # checkpoints the fetcher skipped. A part it re-walked must not be in
        # that sum — the run stored its records itself — so "skipped" has to be
        # reported, not inferred from what was checkpointed.
        first_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []
        fetch_pubmed(
            first_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_finished=done.append,
        )
        assert len(done) > 1, "setup: this day must hold a skipped part and a fetched one"

        second_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        skipped: list[str] = []

        result = fetch_pubmed(
            second_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            completed_parts={done[0].part_key: done[0]},
            on_part_skipped=skipped.append,
        )

        assert result.status == "completed"
        assert skipped == [done[0].part_key]

    def test_a_part_whose_count_moved_is_not_reported_as_skipped(self):
        # It is re-walked, so its records are stored by this run. Reporting it
        # as skipped would credit its stored count on top of them.
        first_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []
        fetch_pubmed(
            first_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_finished=done.append,
        )
        assert done, "setup: an over-cap day must checkpoint at least one part"
        moved = dataclasses.replace(done[0], promised=done[0].promised - 1)

        second_client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        skipped: list[str] = []

        fetch_pubmed(
            second_client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            completed_parts={moved.part_key: moved},
            on_part_skipped=skipped.append,
        )

        assert skipped == []

    def test_an_under_cap_day_ignores_the_resume_arguments(self):
        client = _eutils_client(lambda term: 2)
        done: list[PartCheckpoint] = []

        result = fetch_pubmed(
            client, date(2024, 3, 15), on_record=lambda r: None, on_part_finished=done.append
        )

        assert result.status == "completed"
        assert done == [], "a day that needs no partitioning has no parts to checkpoint"
