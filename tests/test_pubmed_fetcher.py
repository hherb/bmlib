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

import xml.etree.ElementTree as ET
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from bmlib.publications.fetchers.pubmed import (
    EFETCH_URL,
    ESEARCH_URL,
    _format_abstract_markdown,
    _parse_article_xml,
    _text_with_formatting,
    fetch_pubmed,
)
from bmlib.publications.models import FetchedRecord, SyncProgress

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
    express, and all three load-bearing here:

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
    """A day larger than the cap cannot complete, and must not claim to.

    NCBI's search backend serves the first 9,999 records of a history session
    and refuses the rest: *"To obtain more than 9,999 PubMed records, consider
    using EDirect…"*. bmlib queries `[Date - Publication]`, where that is not
    an edge case: measured 2026-08-20, every first-of-month day is over the cap
    (49,543-90,571 records, since a record carrying only a year and month is
    indexed at day 1) and every 1 January is 212,439-315,282.

    Before this guard the walk asked for record 10,000 anyway, got an HTTP 400
    whose body it discarded, and failed the day with
    ``Client error '400 Bad Request'`` — accurate, since the day genuinely
    cannot be completed, but naming neither the cause nor the remedy, and
    re-fetching twenty pages of already-stored records on every later run to
    reach the same wall. Issue #105 is what makes such a day fetchable; until
    it lands the day is refused, not partially stored.
    """

    def test_a_day_over_the_cap_is_failed_not_completed_short(self):
        client = _FakeEUtils(12_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"

    def test_the_error_names_the_cap_and_what_was_never_offered(self):
        """An operator cannot act on `400 Bad Request`; they can act on this."""
        client = _FakeEUtils(12_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.error is not None
        assert "12000" in result.error
        assert "9999" in result.error
        assert "2001" in result.error  # 12,000 - 9,999, out of reach
        assert "#105" in result.error  # the remedy, not just the diagnosis

    def test_the_day_is_refused_before_a_single_record_is_fetched(self):
        """One esearch and nothing else — the walk is not begun.

        The day ends `failed` either way, and a failed day is re-offered on
        every later run, so the whole question is what the doomed run costs.
        Walking first would buy the first 9,999 records once and re-fetch them
        forever after: some 72 such days in a six-year backfill, ~3 GB per run,
        storing nothing new.
        """
        client = _FakeEUtils(12_000)
        on_record = MagicMock()

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=on_record)

        assert client.pages == []
        assert len(client.calls) == 1
        on_record.assert_not_called()
        assert result.record_count == 0

    def test_one_record_past_the_cap_is_already_too_many(self):
        """The boundary is a count, not an approximation: 10,000 is refused.

        Without this the cap could drift up by one and nothing would notice —
        a 10,000-record day would walk, come back 9,999 short by one, and
        complete on the shortfall note.
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

    def test_the_1_january_shape_says_what_it_could_not_reach(self):
        """A day 30x the cap reports the cap, not a shortfall it invented.

        Had the walk run first, 9,999 delivered against 30,000 promised would
        be 33% — under `SHORTFALL_FAILURE_RATIO`, so the day would fail with
        "treated as truncated rather than short", describing a walk that
        stopped early when nothing stopped early.
        """
        client = _FakeEUtils(30_000)

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        assert "floor" not in result.error
        assert "cannot be reached" in result.error

    def test_a_stall_below_the_cap_is_still_reported_as_a_stall(self):
        """The cap must not become the explanation for every failure."""
        client = _FakeEUtils(1500, absent=frozenset(range(500, 1000)))

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, date(2024, 1, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        assert "empty page" in result.error
