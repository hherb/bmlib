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
            ("<t><u>x</u></t>", "__x__"),
            ("<t><underline>x</underline></t>", "__x__"),
        ]
        for xml, expected in cases:
            assert _text_with_formatting(ET.fromstring(xml)) == expected, xml

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
