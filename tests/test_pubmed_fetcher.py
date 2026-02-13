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

from bmlib.publications.fetchers.pubmed import (
    EFETCH_URL,
    ESEARCH_URL,
    _parse_article_xml,
    fetch_pubmed,
)
from bmlib.publications.models import SyncProgress

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

        assert result["pmid"] == "12345678"
        assert result["title"] == "Effects of aspirin on cardiovascular outcomes"
        assert "BACKGROUND: Heart disease is the leading cause of death." in result["abstract"]
        assert "METHODS: We conducted a randomized trial." in result["abstract"]
        assert "RESULTS: Aspirin reduced events by 20%." in result["abstract"]
        assert result["authors"] == ["Smith, John A", "Jones, Mary B"]
        assert result["journal"] == "The Lancet"
        assert result["publication_date"] == "2024-01-15"
        assert result["doi"] == "10.1016/S0140-6736(24)00001-1"
        assert result["pmc_id"] == "PMC9999999"
        assert result["keywords"] == ["Aspirin", "Cardiovascular Diseases"]
        assert result["source"] == "pubmed"

        # Fulltext sources
        assert len(result["fulltext_sources"]) == 2
        pmc_source = result["fulltext_sources"][0]
        assert pmc_source["source"] == "pmc"
        assert "PMC9999999" in pmc_source["url"]
        doi_source = result["fulltext_sources"][1]
        assert doi_source["source"] == "publisher"
        assert "10.1016/S0140-6736(24)00001-1" in doi_source["url"]

    def test_minimal_article_missing_optional_fields(self):
        """Missing optional fields (DOI, abstract, authors) are handled gracefully."""
        el = ET.fromstring(MINIMAL_ARTICLE_XML)
        result = _parse_article_xml(el)

        assert result["pmid"] == "99999999"
        assert result["title"] == "A minimal record"
        assert result["abstract"] is None
        assert result["authors"] == []
        assert result["journal"] == "Some Journal"
        assert result["publication_date"] == "2024"
        assert result["doi"] is None
        assert result["pmc_id"] is None
        assert result["keywords"] == []
        assert result["fulltext_sources"] == []
        assert result["source"] == "pubmed"

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
        assert result["publication_date"] == "2024-03-05"

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
        assert result["authors"] == ["Consortium"]


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

        records: list[dict] = []
        on_record = MagicMock(side_effect=lambda r: records.append(r))

        with patch("bmlib.publications.fetchers.pubmed.time.sleep"):
            result = fetch_pubmed(client, target, on_record=on_record)

        assert result.source == "pubmed"
        assert result.date == "2024-01-15"
        assert result.record_count == 2
        assert result.status == "complete"
        assert result.error is None

        assert on_record.call_count == 2
        assert records[0]["pmid"] == "12345678"
        assert records[1]["pmid"] == "99999999"

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
        assert result.status == "complete"
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

        assert result.status == "error"
        assert "Network error" in result.error
        assert result.record_count == 0
        on_record.assert_not_called()

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

        assert result.status == "error"
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

        assert result.status == "complete"
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
