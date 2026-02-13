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

"""Tests for bmlib.publications.fetchers.openalex — OpenAlex fetcher."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from bmlib.publications.fetchers.openalex import (
    _normalize,
    _reconstruct_abstract,
    fetch_openalex,
)
from bmlib.publications.models import FetchResult, SyncProgress

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_raw_work(
    *,
    doi: str = "https://doi.org/10.1234/test.2024",
    pmid: str = "https://pubmed.ncbi.nlm.nih.gov/12345678",
    title: str = "Test Publication Title",
    abstract_inverted_index: dict | None = None,
    authorships: list | None = None,
    primary_location: dict | None = None,
    locations: list | None = None,
    primary_topic: dict | None = None,
    open_access: dict | None = None,
    license_value: str | None = "cc-by-4.0",
    work_type: str = "journal-article",
    publication_date: str = "2024-06-15",
) -> dict:
    """Build a realistic raw OpenAlex work record."""
    if abstract_inverted_index is None:
        abstract_inverted_index = {
            "This": [0],
            "is": [1],
            "a": [2],
            "test": [3],
            "abstract.": [4],
        }

    if authorships is None:
        authorships = [
            {"author": {"display_name": "Alice Smith"}},
            {"author": {"display_name": "Bob Jones"}},
        ]

    if primary_location is None:
        primary_location = {
            "source": {"display_name": "Nature Medicine"},
        }

    if locations is None:
        locations = [
            {
                "source": {"display_name": "Nature Medicine"},
                "landing_page_url": "https://nature.com/articles/test",
                "pdf_url": "https://nature.com/articles/test.pdf",
                "version": "publishedVersion",
            },
            {
                "source": {"display_name": "PubMed Central"},
                "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
                "pdf_url": None,
                "version": "acceptedVersion",
            },
        ]

    if primary_topic is None:
        primary_topic = {"display_name": "Oncology"}

    if open_access is None:
        open_access = {"is_oa": True}

    return {
        "doi": doi,
        "ids": {"pmid": pmid},
        "title": title,
        "abstract_inverted_index": abstract_inverted_index,
        "authorships": authorships,
        "primary_location": primary_location,
        "locations": locations,
        "primary_topic": primary_topic,
        "open_access": open_access,
        "license": license_value,
        "type": work_type,
        "publication_date": publication_date,
    }


def _make_api_response(
    results: list[dict],
    total_count: int | None = None,
    next_cursor: str | None = None,
) -> dict:
    """Build an OpenAlex API response envelope."""
    if total_count is None:
        total_count = len(results)
    return {
        "meta": {
            "count": total_count,
            "next_cursor": next_cursor,
        },
        "results": results,
    }


def _mock_client(responses: list[dict]) -> MagicMock:
    """Create a mock httpx client that returns the given responses in order."""
    client = MagicMock()
    mock_responses = []
    for resp_data in responses:
        mock_resp = MagicMock()
        mock_resp.json.return_value = resp_data
        mock_resp.raise_for_status.return_value = None
        mock_responses.append(mock_resp)
    client.get.side_effect = mock_responses
    return client


# ---------------------------------------------------------------------------
# Test _reconstruct_abstract
# ---------------------------------------------------------------------------


class TestReconstructAbstract:
    def test_none_input_returns_none(self):
        assert _reconstruct_abstract(None) is None

    def test_empty_dict_returns_none(self):
        assert _reconstruct_abstract({}) is None

    def test_simple_reconstruction(self):
        inverted = {"Hello": [0], "world": [1]}
        assert _reconstruct_abstract(inverted) == "Hello world"

    def test_words_at_multiple_positions(self):
        inverted = {"the": [0, 4], "cat": [1], "sat": [2], "on": [3], "mat": [5]}
        result = _reconstruct_abstract(inverted)
        assert result == "the cat sat on the mat"

    def test_out_of_order_positions(self):
        inverted = {"end": [2], "the": [1], "beginning": [0]}
        assert _reconstruct_abstract(inverted) == "beginning the end"


# ---------------------------------------------------------------------------
# Test _normalize
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_full_record(self):
        raw = _make_raw_work()
        result = _normalize(raw)

        assert result["doi"] == "10.1234/test.2024"
        assert result["pmid"] == "12345678"
        assert result["title"] == "Test Publication Title"
        assert result["authors"] == ["Alice Smith", "Bob Jones"]
        assert result["journal"] == "Nature Medicine"
        assert result["abstract"] == "This is a test abstract."
        assert result["publication_date"] == "2024-06-15"
        assert result["keywords"] == ["Oncology"]
        assert result["is_open_access"] is True
        assert result["license"] == "cc-by-4.0"
        assert result["publication_types"] == ["journal-article"]
        assert result["source"] == "openalex"

    def test_doi_prefix_stripped(self):
        raw = _make_raw_work(doi="https://doi.org/10.5555/example")
        result = _normalize(raw)
        assert result["doi"] == "10.5555/example"

    def test_pmid_prefix_stripped(self):
        raw = _make_raw_work(pmid="https://pubmed.ncbi.nlm.nih.gov/99887766")
        result = _normalize(raw)
        assert result["pmid"] == "99887766"

    def test_none_doi(self):
        raw = _make_raw_work(doi=None)
        result = _normalize(raw)
        assert result["doi"] is None

    def test_none_pmid(self):
        raw = _make_raw_work()
        raw["ids"] = {}
        result = _normalize(raw)
        assert result["pmid"] is None

    def test_missing_ids_dict(self):
        raw = _make_raw_work()
        del raw["ids"]
        result = _normalize(raw)
        assert result["pmid"] is None

    def test_no_primary_topic(self):
        raw = _make_raw_work(primary_topic=None)
        raw["primary_topic"] = None
        result = _normalize(raw)
        assert result["keywords"] == []

    def test_no_open_access(self):
        raw = _make_raw_work(open_access=None)
        raw["open_access"] = None
        result = _normalize(raw)
        assert result["is_open_access"] is False

    def test_fulltext_sources_from_locations(self):
        raw = _make_raw_work()
        result = _normalize(raw)
        fts = result["fulltext_sources"]

        # Location 1 has both landing_page_url and pdf_url -> 2 entries
        # Location 2 has only landing_page_url (pdf_url is None) -> 1 entry
        assert len(fts) == 3

        # First location: html from Nature Medicine (publishedVersion -> published)
        assert fts[0]["source"] == "Nature Medicine"
        assert fts[0]["url"] == "https://nature.com/articles/test"
        assert fts[0]["format"] == "html"
        assert fts[0]["version"] == "published"

        # First location: pdf from Nature Medicine
        assert fts[1]["source"] == "Nature Medicine"
        assert fts[1]["url"] == "https://nature.com/articles/test.pdf"
        assert fts[1]["format"] == "pdf"
        assert fts[1]["version"] == "published"

        # Second location: html from PMC (acceptedVersion -> accepted)
        assert fts[2]["source"] == "PubMed Central"
        assert fts[2]["url"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"
        assert fts[2]["format"] == "html"
        assert fts[2]["version"] == "accepted"

    def test_version_mapping(self):
        locations = [
            {
                "source": {"display_name": "Repo"},
                "landing_page_url": "https://repo.example.com/1",
                "pdf_url": None,
                "version": "submittedVersion",
            },
        ]
        raw = _make_raw_work(locations=locations)
        result = _normalize(raw)
        assert result["fulltext_sources"][0]["version"] == "preprint"

    def test_no_locations(self):
        raw = _make_raw_work(locations=[])
        result = _normalize(raw)
        assert result["fulltext_sources"] == []

    def test_empty_authorships(self):
        raw = _make_raw_work(authorships=[])
        result = _normalize(raw)
        assert result["authors"] == []

    def test_no_type(self):
        raw = _make_raw_work()
        raw["type"] = None
        result = _normalize(raw)
        assert result["publication_types"] == []


# ---------------------------------------------------------------------------
# Test fetch_openalex
# ---------------------------------------------------------------------------


class TestFetchOpenAlex:
    def test_single_page_with_full_record(self):
        """Fetch a single-page response with one record containing all fields."""
        raw = _make_raw_work()
        api_response = _make_api_response([raw], total_count=1, next_cursor=None)
        client = _mock_client([api_response])

        records: list[dict] = []
        result = fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert isinstance(result, FetchResult)
        assert result.source == "openalex"
        assert result.date == "2024-06-15"
        assert result.record_count == 1
        assert result.status == "completed"
        assert result.error is None

        assert len(records) == 1
        rec = records[0]
        assert rec["doi"] == "10.1234/test.2024"
        assert rec["pmid"] == "12345678"
        assert rec["abstract"] == "This is a test abstract."
        assert rec["source"] == "openalex"
        assert len(rec["fulltext_sources"]) == 3

    def test_multi_page_pagination(self):
        """Verify cursor-based pagination follows next_cursor across pages."""
        raw1 = _make_raw_work(title="Paper 1")
        raw2 = _make_raw_work(title="Paper 2")

        page1 = _make_api_response([raw1], total_count=2, next_cursor="cursor_page2")
        page2 = _make_api_response([raw2], total_count=2, next_cursor=None)
        client = _mock_client([page1, page2])

        records: list[dict] = []
        result = fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert result.record_count == 2
        assert result.status == "completed"
        assert len(records) == 2
        assert records[0]["title"] == "Paper 1"
        assert records[1]["title"] == "Paper 2"

        # Client should have been called twice
        assert client.get.call_count == 2

        # Second call should use the cursor from first page
        second_call_params = client.get.call_args_list[1][1]["params"]
        assert second_call_params["cursor"] == "cursor_page2"

    def test_empty_response(self):
        """An empty result set returns ok with zero records."""
        api_response = _make_api_response([], total_count=0, next_cursor=None)
        client = _mock_client([api_response])

        records: list[dict] = []
        result = fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert result.status == "completed"
        assert result.record_count == 0
        assert result.error is None
        assert len(records) == 0

    def test_http_error_returns_failed(self):
        """An HTTP error results in a failed FetchResult with error message."""
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500 Internal Server Error")
        client.get.return_value = mock_resp

        records: list[dict] = []
        result = fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert result.status == "failed"
        assert result.error is not None
        assert "500" in result.error
        assert result.record_count == 0
        assert len(records) == 0

    def test_progress_callback_fires(self):
        """The on_progress callback is invoked after each page."""
        raw = _make_raw_work()
        api_response = _make_api_response([raw], total_count=1, next_cursor=None)
        client = _mock_client([api_response])

        progress_reports: list[SyncProgress] = []
        records: list[dict] = []

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            on_progress=progress_reports.append,
            email="test@example.com",
        )

        assert len(progress_reports) == 1
        sp = progress_reports[0]
        assert isinstance(sp, SyncProgress)
        assert sp.source == "openalex"
        assert sp.date == "2024-06-15"
        assert sp.records_processed == 1
        assert sp.records_total == 1
        assert sp.status == "in_progress"

    def test_progress_fires_per_page(self):
        """Progress fires once per page, tracking cumulative counts."""
        raw1 = _make_raw_work(title="P1")
        raw2 = _make_raw_work(title="P2")
        page1 = _make_api_response([raw1], total_count=2, next_cursor="next")
        page2 = _make_api_response([raw2], total_count=2, next_cursor=None)
        client = _mock_client([page1, page2])

        progress_reports: list[SyncProgress] = []
        records: list[dict] = []

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            on_progress=progress_reports.append,
            email="test@example.com",
        )

        assert len(progress_reports) == 2
        assert progress_reports[0].records_processed == 1
        assert progress_reports[0].records_total == 2
        assert progress_reports[1].records_processed == 2
        assert progress_reports[1].records_total == 2

    def test_no_progress_callback(self):
        """Fetch works without a progress callback (None)."""
        raw = _make_raw_work()
        api_response = _make_api_response([raw], total_count=1, next_cursor=None)
        client = _mock_client([api_response])

        records: list[dict] = []
        result = fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            on_progress=None,
            email="test@example.com",
        )

        assert result.status == "completed"
        assert result.record_count == 1

    def test_mailto_param_sent(self):
        """The mailto parameter is sent with each request."""
        api_response = _make_api_response([], total_count=0, next_cursor=None)
        client = _mock_client([api_response])

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=lambda r: None,
            email="test@example.org",
        )

        call_params = client.get.call_args[1]["params"]
        assert call_params["mailto"] == "test@example.org"

    def test_api_key_param_sent_when_provided(self):
        """The api_key parameter is included when provided."""
        api_response = _make_api_response([], total_count=0, next_cursor=None)
        client = _mock_client([api_response])

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=lambda r: None,
            email="test@example.com",
            api_key="my-secret-key",
        )

        call_params = client.get.call_args[1]["params"]
        assert call_params["api_key"] == "my-secret-key"

    def test_api_key_param_absent_when_none(self):
        """The api_key parameter is omitted when not provided."""
        api_response = _make_api_response([], total_count=0, next_cursor=None)
        client = _mock_client([api_response])

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=lambda r: None,
            email="test@example.com",
        )

        call_params = client.get.call_args[1]["params"]
        assert "api_key" not in call_params

    def test_first_cursor_is_star(self):
        """The first request uses cursor='*'."""
        api_response = _make_api_response([], total_count=0, next_cursor=None)
        client = _mock_client([api_response])

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=lambda r: None,
            email="test@example.com",
        )

        call_params = client.get.call_args[1]["params"]
        assert call_params["cursor"] == "*"

    def test_date_filter_format(self):
        """The date filter uses the correct OpenAlex format."""
        api_response = _make_api_response([], total_count=0, next_cursor=None)
        client = _mock_client([api_response])

        fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=lambda r: None,
            email="test@example.com",
        )

        call_params = client.get.call_args[1]["params"]
        expected_filter = "from_publication_date:2024-06-15,to_publication_date:2024-06-15"
        assert call_params["filter"] == expected_filter

    def test_http_error_mid_pagination(self):
        """An error on the second page reports records from the first page."""
        raw = _make_raw_work()
        page1 = _make_api_response([raw], total_count=2, next_cursor="next")

        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = page1
        mock_resp1.raise_for_status.return_value = None

        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status.side_effect = Exception("HTTP 429 Rate Limited")

        client = MagicMock()
        client.get.side_effect = [mock_resp1, mock_resp2]

        records: list[dict] = []
        result = fetch_openalex(
            client,
            date(2024, 6, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert result.status == "failed"
        assert result.record_count == 1  # one record from first page
        assert "429" in result.error
        assert len(records) == 1
