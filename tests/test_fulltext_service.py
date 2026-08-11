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

"""Tests for bmlib.fulltext.service."""

import copy
import errno
import importlib.metadata
import logging
import os
import pickle
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmlib.fulltext.cache import FullTextCache
from bmlib.fulltext.models import FullTextResult, FullTextSourceEntry
from bmlib.fulltext.pdf_converter import ConversionResult
from bmlib.fulltext.service import (
    FullTextError,
    FullTextService,
    FullTextUnavailableError,
    _extract_free_pdf_url,
    _normalise_pmc_id,
    _sanitize_identifier,
    _TierFailures,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _idconv_miss() -> MagicMock:
    """NCBI's ID Converter with no record for the identifier."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "ok", "records": []}
    return resp


def _ncbi_miss() -> MagicMock:
    """NCBI's efetch with nothing for this PMC ID."""
    resp = MagicMock()
    resp.status_code = 404
    return resp


class TestFetchEuropePMC:
    def test_success(self):
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_data

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(pmc_id="PMC123", doi=None, pmid="456")

        assert result.source == "europepmc"
        assert result.html is not None
        assert "<h1>" in result.html

    def test_404_falls_through(self):
        mock_404 = MagicMock()
        mock_404.status_code = 404
        # Search for PDF render URL (returns no free PDF)
        mock_search_no_pdf = MagicMock()
        mock_search_no_pdf.status_code = 200
        mock_search_no_pdf.json.return_value = {
            "resultList": {"result": [{"pmcid": "PMC123", "inEPMC": "Y"}]}
        }
        mock_unpaywall_404 = MagicMock()
        mock_unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        # PMC XML 404 -> NCBI 404 -> search (no PDF) -> Unpaywall 404 -> DOI
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_404, _ncbi_miss(), mock_search_no_pdf, mock_unpaywall_404],
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test", pmid="456")

        assert result.source == "doi"
        assert result.web_url == "https://doi.org/10.1/test"


class TestDiscoverPMCID:
    def test_discovers_pmc_id_from_doi(self):
        """When no PMC ID given, search Europe PMC by DOI and fetch fulltext."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()

        # Search response: paper is in EPMC with a PMCID
        mock_search = MagicMock()
        mock_search.status_code = 200
        mock_search.json.return_value = {
            "resultList": {"result": [{"pmcid": "PMC999", "inEPMC": "Y", "doi": "10.1/test"}]}
        }

        # Fulltext XML response
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = xml_data

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=[mock_search, mock_xml]):
            result = service.fetch_fulltext(pmc_id=None, doi="10.1/test", pmid="")

        assert result.source == "europepmc"
        assert result.html is not None

    def test_discovers_pmc_id_from_pmid(self):
        """When no PMC ID or DOI, search Europe PMC by PMID."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()

        mock_search = MagicMock()
        mock_search.status_code = 200
        mock_search.json.return_value = {
            "resultList": {"result": [{"pmcid": "PMC888", "inEPMC": "Y"}]}
        }
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = xml_data

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=[mock_search, mock_xml]):
            result = service.fetch_fulltext(pmc_id=None, doi=None, pmid="12345")

        assert result.source == "europepmc"

    def test_not_in_epmc_falls_through(self):
        """Paper found in search but not in EPMC -> skip to Unpaywall."""
        mock_search = MagicMock()
        mock_search.status_code = 200
        mock_search.json.return_value = {
            "resultList": {"result": [{"pmcid": None, "inEPMC": "N", "doi": "10.1/test"}]}
        }
        mock_unpaywall_404 = MagicMock()
        mock_unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_search, _idconv_miss(), mock_unpaywall_404],
        ):
            result = service.fetch_fulltext(pmc_id=None, doi="10.1/test", pmid="")

        assert result.source == "doi"
        assert result.web_url == "https://doi.org/10.1/test"


class TestFetchUnpaywall:
    def test_success(self):
        mock_pmc_404 = MagicMock()
        mock_pmc_404.status_code = 404

        # Search for PDF render URL (returns no free PDF)
        mock_search_no_pdf = MagicMock()
        mock_search_no_pdf.status_code = 200
        mock_search_no_pdf.json.return_value = {
            "resultList": {"result": [{"pmcid": "PMC123", "inEPMC": "Y"}]}
        }

        unpaywall_json = {
            "best_oa_location": {
                "url_for_pdf": "https://example.com/paper.pdf",
                "url": "https://example.com/paper",
                "host_type": "publisher",
                "license": "cc-by",
            }
        }
        mock_unpaywall = MagicMock()
        mock_unpaywall.status_code = 200
        mock_unpaywall.json.return_value = unpaywall_json

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_pmc_404, _ncbi_miss(), mock_search_no_pdf, mock_unpaywall],
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test", pmid="456")

        assert result.source == "unpaywall"
        assert result.pdf_url == "https://example.com/paper.pdf"


class TestFetchDOIFallback:
    def test_no_pmc_no_unpaywall(self):
        # No PMC ID -> discovery search returns no match -> Unpaywall fails -> DOI fallback
        mock_search_empty = MagicMock()
        mock_search_empty.status_code = 200
        mock_search_empty.json.return_value = {"resultList": {"result": []}}
        mock_unpaywall_404 = MagicMock()
        mock_unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_search_empty, _idconv_miss(), mock_unpaywall_404],
        ):
            result = service.fetch_fulltext(pmc_id=None, doi="10.1/test", pmid="456")
        assert result.source == "doi"
        assert result.web_url == "https://doi.org/10.1/test"

    def test_no_identifiers(self):
        service = FullTextService(email="test@example.com")
        with pytest.raises(FullTextError):
            service.fetch_fulltext(pmc_id=None, doi=None, pmid="")


class TestKnownSources:
    def test_jats_xml_source_tried_first(self):
        """When fulltext_sources contains XML, fetch and parse it."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_data

        sources = [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.pdf", format="pdf", source="medrxiv"
            ),
            FullTextSourceEntry(url="https://medrxiv.org/jats.xml", format="xml", source="medrxiv"),
        ]

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(fulltext_sources=sources)

        assert result.source == "medrxiv"
        assert result.html is not None
        assert "<h1>" in result.html

    def test_xml_fails_falls_to_pdf(self):
        """If XML fetch fails, PDF source should be returned."""
        mock_fail = MagicMock()
        mock_fail.status_code = 500

        sources = [
            FullTextSourceEntry(url="https://biorxiv.org/jats.xml", format="xml", source="biorxiv"),
            FullTextSourceEntry(
                url="https://biorxiv.org/paper.pdf", format="pdf", source="biorxiv"
            ),
        ]

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=mock_fail):
            result = service.fetch_fulltext(fulltext_sources=sources, doi="10.1/test")

        assert result.pdf_url == "https://biorxiv.org/paper.pdf"

    def test_all_known_fail_falls_to_europepmc(self):
        """If all known sources fail, existing discovery chain runs."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_epmc = MagicMock()
        mock_epmc.status_code = 200
        mock_epmc.content = xml_data

        sources = [
            FullTextSourceEntry(url="https://broken.org/jats.xml", format="xml", source="broken"),
        ]

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=[mock_fail, mock_epmc]):
            result = service.fetch_fulltext(
                fulltext_sources=sources,
                pmc_id="PMC123",
            )

        assert result.source == "europepmc"

    def test_no_sources_backwards_compatible(self):
        """Without fulltext_sources, existing behavior is unchanged."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_data

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(pmc_id="PMC123")

        assert result.source == "europepmc"

    def test_html_source_returns_web_url(self):
        """HTML sources should be returned as web_url."""
        sources = [
            FullTextSourceEntry(
                url="https://pmc.ncbi.nlm.nih.gov/PMC123/", format="html", source="pmc"
            ),
        ]

        service = FullTextService(email="test@example.com")
        result = service.fetch_fulltext(fulltext_sources=sources)

        assert result.source == "pmc"
        assert result.web_url == "https://pmc.ncbi.nlm.nih.gov/PMC123/"


class TestFullTextSourceEntry:
    def test_to_dict_and_from_dict(self):
        entry = FullTextSourceEntry(
            url="https://example.com/paper.pdf",
            format="pdf",
            source="biorxiv",
            open_access=True,
            version="preprint",
        )
        d = entry.to_dict()
        assert d == {
            "url": "https://example.com/paper.pdf",
            "format": "pdf",
            "source": "biorxiv",
            "open_access": True,
            "version": "preprint",
        }
        restored = FullTextSourceEntry.from_dict(d)
        assert restored == entry

    def test_from_dict_legacy_without_open_access(self):
        """Old dicts without open_access should default to True."""
        legacy = {"url": "https://example.com/a.xml", "format": "xml", "source": "medrxiv"}
        entry = FullTextSourceEntry.from_dict(legacy)
        assert entry.open_access is True
        assert entry.version is None

    def test_to_dict_omits_none_version(self):
        entry = FullTextSourceEntry(url="https://x.com/a.pdf", format="pdf", source="test")
        d = entry.to_dict()
        assert "version" not in d


class TestFullTextError:
    def test_no_identifiers_message(self):
        err = FullTextError("No identifiers provided")
        assert "No identifiers" in str(err)


class TestSanitizeIdentifier:
    def test_doi_sanitized(self):
        # Readable prefix is preserved (a collision-proof hash is appended).
        assert _sanitize_identifier("10.1234/test.paper-1").startswith("10.1234_test.paper-1_")

    def test_slashes_replaced(self):
        result = _sanitize_identifier("10.1101/2024.01.15.123456")
        assert "/" not in result

    def test_safe_chars_preserved(self):
        result = _sanitize_identifier("simple_name-1.0")
        assert result.startswith("simple_name-1.0_")

    def test_distinct_identifiers_do_not_collide(self):
        # Two DOIs that sanitise to the same prefix must map to different keys.
        a = _sanitize_identifier("10.1/a:b")
        b = _sanitize_identifier("10.1/a/b")
        assert a != b

    def test_deterministic(self):
        assert _sanitize_identifier("10.1/a:b") == _sanitize_identifier("10.1/a:b")


class TestCacheIntegration:
    """Tests for FullTextCache integration in FullTextService."""

    PDF_MAGIC = b"%PDF-1.4 fake content for testing"

    def test_cached_html_returned_without_network(self, tmp_path):
        """If HTML is in the disk cache, return it immediately."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<h1>Cached</h1>", _sanitize_identifier("10.1234/test"))

        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get") as mock_get:
            result = service.fetch_fulltext(
                doi="10.1234/test",
                identifier="10.1234/test",
            )
            mock_get.assert_not_called()

        assert result.source == "cached"
        assert result.html == "<h1>Cached</h1>"

    def test_cached_pdf_returned_without_network(self, tmp_path):
        """If PDF is in the disk cache, return file_path immediately."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf(self.PDF_MAGIC, _sanitize_identifier("10.1234/test"))

        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get") as mock_get:
            result = service.fetch_fulltext(
                doi="10.1234/test",
                identifier="10.1234/test",
            )
            mock_get.assert_not_called()

        assert result.source == "cached"
        assert result.file_path is not None
        assert result.file_path.endswith(".pdf")

    def test_fetched_jats_html_saved_to_cache(self, tmp_path):
        """After fetching JATS XML from Europe PMC, HTML is saved to disk cache."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_data

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(
                pmc_id="PMC123",
                identifier="10.1234/test",
            )

        assert result.source == "europepmc"
        cached_html = cache.get_html(_sanitize_identifier("10.1234/test"))
        assert cached_html is not None
        assert "<h1>" in cached_html

    def test_pdf_downloaded_and_cached(self, tmp_path):
        """When Unpaywall returns a PDF URL, the PDF is downloaded and cached."""
        # Europe PMC search returns nothing
        mock_search_empty = MagicMock()
        mock_search_empty.status_code = 200
        mock_search_empty.json.return_value = {"resultList": {"result": []}}

        # Unpaywall returns a PDF URL
        mock_unpaywall = MagicMock()
        mock_unpaywall.status_code = 200
        mock_unpaywall.json.return_value = {
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        }

        # PDF download response
        mock_pdf = MagicMock()
        mock_pdf.status_code = 200
        mock_pdf.content = self.PDF_MAGIC

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_search_empty, _idconv_miss(), mock_unpaywall, mock_pdf],
        ):
            result = service.fetch_fulltext(
                doi="10.1234/test",
                identifier="10.1234/test",
            )

        assert result.source == "unpaywall"
        assert result.pdf_url == "https://example.com/paper.pdf"
        assert result.file_path is not None
        assert result.file_path.endswith(".pdf")
        # Verify file on disk
        assert Path(result.file_path).exists()

    def test_invalid_pdf_rejected_keeps_url(self, tmp_path):
        """If downloaded PDF data is invalid, file_path stays None but pdf_url remains."""
        mock_search_empty = MagicMock()
        mock_search_empty.status_code = 200
        mock_search_empty.json.return_value = {"resultList": {"result": []}}

        mock_unpaywall = MagicMock()
        mock_unpaywall.status_code = 200
        mock_unpaywall.json.return_value = {
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        }

        # Invalid PDF data (HTML error page)
        mock_pdf = MagicMock()
        mock_pdf.status_code = 200
        mock_pdf.content = b"<html>Access Denied</html>"

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_search_empty, _idconv_miss(), mock_unpaywall, mock_pdf],
        ):
            result = service.fetch_fulltext(
                doi="10.1234/test",
                identifier="10.1234/test",
            )

        assert result.source == "unpaywall"
        assert result.pdf_url == "https://example.com/paper.pdf"
        assert result.file_path is None

    def test_no_identifier_skips_caching(self, tmp_path):
        """Without identifier, caching is bypassed entirely."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_data

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(pmc_id="PMC123")

        assert result.source == "europepmc"
        # Nothing cached since no identifier was provided
        assert not list((tmp_path / "html").iterdir())

    def test_known_source_xml_cached(self, tmp_path):
        """JATS XML from known sources is also cached."""
        xml_data = (FIXTURES / "sample_article.xml").read_bytes()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_data

        sources = [
            FullTextSourceEntry(
                url="https://medrxiv.org/jats.xml",
                format="xml",
                source="medrxiv",
            ),
        ]

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(
                fulltext_sources=sources,
                identifier="10.1234/test",
            )

        assert result.source == "medrxiv"
        assert cache.get_html(_sanitize_identifier("10.1234/test")) is not None

    def test_known_source_pdf_downloaded_and_cached(self, tmp_path):
        """PDF from known sources is downloaded and cached."""
        mock_pdf = MagicMock()
        mock_pdf.status_code = 200
        mock_pdf.content = self.PDF_MAGIC

        sources = [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.pdf",
                format="pdf",
                source="medrxiv",
            ),
        ]

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", return_value=mock_pdf):
            result = service.fetch_fulltext(
                fulltext_sources=sources,
                identifier="10.1234/test",
            )

        assert result.source == "medrxiv"
        assert result.file_path is not None
        assert Path(result.file_path).exists()


class TestBodylessJATS:
    """A JATS document with no <body> carries only the abstract.

    medRxiv's ``jatsxml`` URL serves such a document for some preprints. It
    returns HTTP 200 and parses cleanly, so it must not be mistaken for a
    successful full-text retrieval — the PDF holds the actual article.
    """

    PDF_MAGIC = b"%PDF-1.4 fake pdf content"

    @staticmethod
    def _sources() -> list[FullTextSourceEntry]:
        """The two entries medRxiv's fetcher records for a preprint."""
        return [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.full.pdf",
                format="pdf",
                source="medrxiv",
            ),
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.source.xml",
                format="xml",
                source="medrxiv",
            ),
        ]

    def test_falls_through_to_pdf(self, tmp_path):
        """A body-less XML must not shadow the PDF that has the real text."""
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        mock_pdf = MagicMock()
        mock_pdf.status_code = 200
        mock_pdf.content = self.PDF_MAGIC

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", side_effect=[mock_xml, mock_pdf]):
            result = service.fetch_fulltext(
                fulltext_sources=self._sources(),
                identifier="10.1234/test",
            )

        # The PDF wins, and its URL travels with the result so a caller can
        # always offer the original (figures, layout) alongside any text.
        assert result.pdf_url == "https://medrxiv.org/paper.full.pdf"
        assert result.file_path is not None

    def test_abstract_only_html_is_not_cached(self, tmp_path):
        """The abstract-only render must not poison the disk cache."""
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        mock_pdf = MagicMock()
        mock_pdf.status_code = 200
        mock_pdf.content = self.PDF_MAGIC

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", side_effect=[mock_xml, mock_pdf]):
            service.fetch_fulltext(
                fulltext_sources=self._sources(),
                identifier="10.1234/test",
            )

        cached = cache.get_html(_sanitize_identifier("10.1234/test"))
        assert cached is None or "Data Availability" not in cached

    def test_used_as_last_resort(self, tmp_path):
        """With nothing better available, the abstract still beats nothing."""
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        xml_only = [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.source.xml",
                format="xml",
                source="medrxiv",
            ),
        ]

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        # XML first, then the Europe PMC search that finds nothing.
        mock_search = MagicMock()
        mock_search.status_code = 200
        mock_search.json.return_value = {"resultList": {"result": []}}
        with patch.object(
            service, "_http_get", side_effect=[mock_xml, mock_search, _idconv_miss(), mock_search]
        ):
            result = service.fetch_fulltext(
                fulltext_sources=xml_only,
                doi="10.1234/test",
                identifier="10.1234/test",
            )

        assert result.html is not None
        assert "Why More Doctors" in result.html
        assert result.content_kind == "abstract"

    def test_last_resort_carries_the_resolved_link(self, tmp_path):
        """The abstract is worth more paired with a link than alone."""
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        xml_only = [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.source.xml", format="xml", source="medrxiv"
            ),
        ]
        mock_search = MagicMock()
        mock_search.status_code = 200
        mock_search.json.return_value = {"resultList": {"result": []}}

        service = FullTextService(email="test@example.com", cache=FullTextCache(cache_dir=tmp_path))
        with patch.object(
            service, "_http_get", side_effect=[mock_xml, mock_search, _idconv_miss(), mock_search]
        ):
            result = service.fetch_fulltext(
                fulltext_sources=xml_only, doi="10.1234/test", identifier="10.1234/test"
            )

        assert result.web_url == "https://doi.org/10.1234/test"

    def test_last_resort_abstract_is_never_cached(self, tmp_path):
        """Caching it would make the abstract permanent for this identifier."""
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        xml_only = [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.source.xml", format="xml", source="medrxiv"
            ),
        ]
        mock_search = MagicMock()
        mock_search.status_code = 200
        mock_search.json.return_value = {"resultList": {"result": []}}

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(
            service, "_http_get", side_effect=[mock_xml, mock_search, _idconv_miss(), mock_search]
        ):
            service.fetch_fulltext(
                fulltext_sources=xml_only, doi="10.1234/test", identifier="10.1234/test"
            )

        assert cache.get_html(_sanitize_identifier("10.1234/test")) is None

    def test_abstract_is_merged_into_a_pdf_that_yielded_no_text(self, tmp_path):
        """A PDF entry counts as success on its URL alone.

        When the download fails there is no text and no file — returning that
        bare link would discard an abstract already in hand.
        """
        mock_xml = MagicMock()
        mock_xml.status_code = 200
        mock_xml.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        dead_pdf = MagicMock()
        dead_pdf.status_code = 404
        dead_pdf.content = b""

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", side_effect=[mock_xml, dead_pdf]):
            result = service.fetch_fulltext(
                fulltext_sources=self._sources(), identifier="10.1234/test"
            )

        assert result.pdf_url == "https://medrxiv.org/paper.full.pdf"
        assert result.file_path is None
        assert result.html is not None
        assert "Why More Doctors" in result.html
        assert result.content_kind == "abstract"


class TestBodylessEuropePMC:
    """Europe PMC serves body-less JATS too, on both of its paths.

    The known-PMC-ID tier additionally has to mark the attempt as failed so
    the free-PDF lookup below it still runs.
    """

    @staticmethod
    def _bodyless() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "abstract_only_article.xml").read_bytes()
        return resp

    @staticmethod
    def _search(pmcid: str | None = None, pdf_url: str | None = None) -> MagicMock:
        hit: dict = {}
        if pmcid:
            hit["pmcid"] = pmcid
            hit["inEPMC"] = "Y"
        if pdf_url:
            hit["fullTextUrlList"] = {
                "fullTextUrl": [{"documentStyle": "pdf", "availability": "Free", "url": pdf_url}]
            }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"resultList": {"result": [hit] if hit else []}}
        return resp

    def test_known_pmc_id_falls_through_to_the_free_pdf(self, tmp_path):
        """A body-less XML must not shadow the PDF render URL beneath it."""
        pdf = MagicMock()
        pdf.status_code = 200
        pdf.content = b"%PDF-1.4 fake pdf content"

        service = FullTextService(email="test@example.com", cache=FullTextCache(cache_dir=tmp_path))
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                self._bodyless(),
                _ncbi_miss(),
                self._search(pdf_url="https://europepmc.org/x.pdf"),
                pdf,
            ],
        ):
            result = service.fetch_fulltext(
                pmc_id="PMC123", doi="10.1234/test", identifier="10.1234/test"
            )

        assert result.source == "europepmc_pdf"
        assert result.pdf_url == "https://europepmc.org/x.pdf"

    def test_known_pmc_id_body_less_xml_is_not_cached(self, tmp_path):
        """It must not become the permanent answer for this identifier."""
        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._bodyless(), _ncbi_miss(), self._search(), self._search()],
        ):
            result = service.fetch_fulltext(
                pmc_id="PMC123", doi="10.1234/test", identifier="10.1234/test"
            )

        assert cache.get_html(_sanitize_identifier("10.1234/test")) is None
        assert result.content_kind == "abstract"

    def test_discovered_pmc_id_body_less_xml_is_not_full_text(self, tmp_path):
        """The discovery path needs the same guard as the known-ID path."""
        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        # search (discovers PMC999) -> body-less XML -> unpaywall miss
        unpaywall = MagicMock()
        unpaywall.status_code = 404
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(pmcid="PMC999"), self._bodyless(), _ncbi_miss(), unpaywall],
        ):
            result = service.fetch_fulltext(doi="10.1234/test", identifier="10.1234/test")

        assert result.content_kind == "abstract"
        assert cache.get_html(_sanitize_identifier("10.1234/test")) is None
        assert result.web_url == "https://doi.org/10.1234/test"


class TestPDFTextExtraction:
    """The seam that carries a PDF's extracted text into ``result.html``.

    ``render_html`` is unit-tested in ``test_pdf_converter``; what matters
    here is that ``fetch_fulltext`` actually reaches it, honours
    ``convert_pdfs``, marks what it produced, and survives a backend that is
    absent or fails.
    """

    PDF_MAGIC = b"%PDF-1.4 fake pdf content"

    @staticmethod
    def _sources() -> list[FullTextSourceEntry]:
        return [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.full.pdf", format="pdf", source="medrxiv"
            ),
        ]

    @staticmethod
    def _conversion(text: str = "Extracted article prose from the PDF.") -> ConversionResult:
        return ConversionResult(
            success=True,
            text=text,
            format="plaintext",
            page_count=1,
            converted_pages=1,
            char_count=len(text),
            page_texts=[text],
        )

    def _stub_converter(self, conversion: ConversionResult) -> MagicMock:
        converter = MagicMock()
        converter.convert.return_value = conversion
        return converter

    def _fetch(self, tmp_path, get_converter, **service_kwargs):
        pdf = MagicMock()
        pdf.status_code = 200
        pdf.content = self.PDF_MAGIC
        service = FullTextService(
            email="test@example.com", cache=FullTextCache(cache_dir=tmp_path), **service_kwargs
        )
        with (
            patch.object(service, "_http_get", return_value=pdf),
            patch("bmlib.fulltext.service.get_converter", get_converter),
        ):
            return service, service.fetch_fulltext(
                fulltext_sources=self._sources(), identifier="10.1234/test"
            )

    def test_text_is_attached_and_the_original_survives(self, tmp_path):
        """Extraction recovers prose, not figures — the PDF stays on offer."""
        _, result = self._fetch(
            tmp_path, MagicMock(return_value=self._stub_converter(self._conversion()))
        )

        assert "Extracted article prose" in (result.html or "")
        assert result.content_kind == "extracted"
        assert result.pdf_url == "https://medrxiv.org/paper.full.pdf"
        assert result.file_path is not None

    def test_convert_pdfs_false_opts_out(self, tmp_path):
        get_converter = MagicMock(return_value=self._stub_converter(self._conversion()))
        _, result = self._fetch(tmp_path, get_converter, convert_pdfs=False)

        assert result.html is None
        assert result.content_kind == "none"
        assert result.file_path is not None
        get_converter.assert_not_called()

    def test_a_missing_pdf_extra_is_survivable(self, tmp_path):
        """Without bmlib[pdf] the caller still gets the PDF itself."""
        _, result = self._fetch(
            tmp_path, MagicMock(side_effect=ImportError("Install with: pip install bmlib[pdf]"))
        )

        assert result.html is None
        assert result.file_path is not None

    def test_a_failed_conversion_leaves_no_html(self, tmp_path):
        """convert() reports failure in its result rather than raising."""
        failed = ConversionResult(
            success=False,
            text="",
            format="plaintext",
            page_count=3,
            converted_pages=0,
            char_count=0,
            error_message="Invalid or corrupted PDF",
        )
        _, result = self._fetch(tmp_path, MagicMock(return_value=self._stub_converter(failed)))

        assert result.html is None
        assert result.content_kind == "none"

    def test_a_scanned_pdf_yielding_no_text_is_reported(self, tmp_path, caplog):
        """An image-only scan extracts cleanly to nothing — say so."""
        empty = ConversionResult(
            success=True,
            text="",
            format="plaintext",
            page_count=2,
            converted_pages=2,
            char_count=0,
            warnings=["Page 1: No extractable text", "Page 2: No extractable text"],
        )
        with caplog.at_level("WARNING"):
            _, result = self._fetch(tmp_path, MagicMock(return_value=self._stub_converter(empty)))

        assert result.html is None
        assert "no extractable text" in caplog.text.lower()

    def test_a_partial_extraction_is_flagged(self, tmp_path, caplog):
        """Text covering half the pages must not pass for a whole article."""
        partial = ConversionResult(
            success=True,
            text="Only the first page came through here.",
            format="plaintext",
            page_count=4,
            converted_pages=1,
            char_count=38,
            page_texts=["Only the first page came through here."],
        )
        with caplog.at_level("WARNING"):
            _, result = self._fetch(tmp_path, MagicMock(return_value=self._stub_converter(partial)))

        assert result.html is not None
        assert "incomplete" in caplog.text.lower()

    def test_a_cache_hit_still_carries_the_extracted_text(self, tmp_path):
        """Regression: the text used to be produced once and then lost.

        Only the PDF bytes are cached, so a second fetch returned a bare
        ``file_path`` and the inline article text silently disappeared the
        moment a paper was viewed twice.
        """
        get_converter = MagicMock(return_value=self._stub_converter(self._conversion()))
        service, first = self._fetch(tmp_path, get_converter)

        with patch("bmlib.fulltext.service.get_converter", get_converter):
            second = service.fetch_fulltext(
                fulltext_sources=self._sources(), identifier="10.1234/test"
            )

        assert second.source == "cached"
        assert second.html == first.html
        assert second.content_kind == "extracted"
        assert second.file_path is not None

    def test_cached_jats_html_is_reported_as_full_text(self, tmp_path):
        """Only body-carrying JATS is ever written to the HTML cache."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<h1>Article</h1>", _sanitize_identifier("10.1234/test"))
        service = FullTextService(email="test@example.com", cache=cache)

        result = service.fetch_fulltext(identifier="10.1234/test")

        assert result.content_kind == "fulltext"


class TestIDConverter:
    """NCBI's ID Converter — the second source for a PMC ID.

    Europe PMC's search only reports a PMC ID when it *both* indexed the paper
    and flagged its full text as available there. The converter depends on
    neither, so it is what rescues a paper Europe PMC's index missed. It is
    third-party text on the way to a URL, and it is consulted on a path that
    already holds a free-PDF URL, so the two properties that matter are that a
    malformed id never reaches a URL and that a failure here costs nothing
    that was already found.
    """

    @staticmethod
    def _reply(**fields: object) -> MagicMock:
        """One converter record, as the API returns it."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok", "records": [fields]}
        return resp

    def test_a_pmcid_is_returned(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC7614751")):
            assert (
                service._resolve_pmc_id_via_idconv(doi="10.1/test", failures=_TierFailures())
                == "PMC7614751"
            )

    def test_the_pmid_is_preferred_when_both_are_known(self):
        """A PMID is an exact key; a DOI is text whose formatting is what missed."""
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            service._resolve_pmc_id_via_idconv(
                doi="10.1/test", pmid="12345", failures=_TierFailures()
            )

        assert mock_get.call_args.kwargs["params"]["ids"] == "12345"

    def test_the_doi_is_used_when_there_is_no_pmid(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            service._resolve_pmc_id_via_idconv(doi="10.1/test", failures=_TierFailures())

        assert mock_get.call_args.kwargs["params"]["ids"] == "10.1/test"

    def test_no_identifier_makes_no_request(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            assert service._resolve_pmc_id_via_idconv(failures=_TierFailures()) is None
            mock_get.assert_not_called()

    def test_an_error_record_resolves_to_nothing(self):
        """``status: error`` is how the converter reports an id it cannot map."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(status="error", errmsg="invalid article id")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    def test_a_record_no_longer_live_resolves_to_nothing(self):
        """``live: "false"`` means PMC no longer serves it — the fetch would fail."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(pmcid="PMC123", live="false")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    @pytest.mark.parametrize("pmcid", ["../../etc/passwd", "PMC123\n"])
    def test_a_malformed_pmcid_is_refused(self, pmcid):
        """It would otherwise be interpolated into a URL path unchecked.

        The trailing newline is the case an anchored ``match()`` misses: ``$``
        matches before it. This site checks the regex directly rather than
        through ``_normalise_pmc_id``, so it needs its own coverage.
        """
        service = FullTextService(email="test@example.com")
        reply = self._reply(pmcid=pmcid)
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    def test_an_empty_record_list_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok", "records": []}
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    def test_a_failed_request_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 500
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    def test_a_transport_failure_is_not_raised(self):
        """It is called where a free-PDF URL is already in hand.

        Letting the exception out would leave the enclosing ``except`` to
        swallow it and skip the rest of the block — trading a working PDF tier
        for a failed converter lookup.
        """
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=RuntimeError("connection reset")):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    def test_unparseable_json_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures()) is None

    def test_the_api_key_is_sent_only_when_configured(self):
        without = FullTextService(email="test@example.com")
        with patch.object(without, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            without._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures())
        assert "api_key" not in mock_get.call_args.kwargs["params"]

        with_key = FullTextService(email="test@example.com", ncbi_api_key="secret")
        with patch.object(
            with_key, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            with_key._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures())
        assert mock_get.call_args.kwargs["params"]["api_key"] == "secret"

    def test_the_caller_is_identified_to_ncbi(self):
        """NCBI asks for tool and email on every request."""
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            service._resolve_pmc_id_via_idconv(pmid="99", failures=_TierFailures())

        params = mock_get.call_args.kwargs["params"]
        assert params["tool"] == "bmlib"
        assert params["email"] == "test@example.com"


class TestNCBIPMCFetch:
    """NCBI's own copy of a PMC article, via ``efetch db=pmc``.

    Europe PMC's ``fullTextXML`` endpoint serves the corpus its ``inEPMC``
    flag describes. When that flag says no — or the article store simply does
    not have it — NCBI is the source that does, and it is reachable with the
    same PMC ID.
    """

    def test_full_text_is_parsed(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            html, has_body = service._fetch_ncbi_pmc("PMC123")

        assert has_body is True
        assert "<h1>" in html

    def test_the_numeric_id_is_sent(self):
        """efetch's documented form for db=pmc is the digits alone."""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp) as mock_get:
            service._fetch_ncbi_pmc("PMC123")

        params = mock_get.call_args.kwargs["params"]
        assert params["id"] == "123"
        assert params["db"] == "pmc"

    def test_a_bare_numeric_id_is_accepted(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp) as mock_get:
            service._fetch_ncbi_pmc("123")

        assert mock_get.call_args.kwargs["params"]["id"] == "123"

    def test_a_malformed_pmc_id_never_reaches_a_url(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            with pytest.raises(FullTextError):
                service._fetch_ncbi_pmc("../../etc/passwd")
            mock_get.assert_not_called()

    def test_a_stub_with_no_article_raises(self):
        """A non-OA reply parses cleanly into nothing.

        Returned rather than raised, it would be promoted to the last-resort
        abstract — near-empty HTML labelled ``content_kind="abstract"``, worse
        than the DOI link it displaced and permanent for a caller that
        persists results.
        """
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "ncbi_pmc_stub.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            with pytest.raises(FullTextError):
                service._fetch_ncbi_pmc("PMC123")

    def test_a_body_less_article_with_an_abstract_is_returned(self):
        """Front matter carrying a real abstract is worth having.

        This is the case the stub guard must not swallow: it is the same
        body-less document Europe PMC serves, and the caller holds it back as
        a last resort exactly as it does there.
        """
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "abstract_only_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            html, has_body = service._fetch_ncbi_pmc("PMC123")

        assert has_body is False
        assert html

    def test_a_failed_request_raises(self):
        resp = MagicMock()
        resp.status_code = 503

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=resp):
            with pytest.raises(FullTextError):
                service._fetch_ncbi_pmc("PMC123")

    def test_the_api_key_is_sent_only_when_configured(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        without = FullTextService(email="test@example.com")
        with patch.object(without, "_http_get", return_value=resp) as mock_get:
            without._fetch_ncbi_pmc("PMC123")
        assert "api_key" not in mock_get.call_args.kwargs["params"]

        with_key = FullTextService(email="test@example.com", ncbi_api_key="secret")
        with patch.object(with_key, "_http_get", return_value=resp) as mock_get:
            with_key._fetch_ncbi_pmc("PMC123")
        assert mock_get.call_args.kwargs["params"]["api_key"] == "secret"


class TestPMCIDValidation:
    """``PMC\\d+`` enforced where the id becomes a URL, not where it arrives."""

    def test_a_bare_number_is_prefixed(self):
        assert _normalise_pmc_id("123") == "PMC123"

    def test_a_prefixed_id_is_unchanged(self):
        assert _normalise_pmc_id("PMC123") == "PMC123"

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "PMC",
            "PMC12a",
            "pmc123",
            "PMC123/../etc",
            "PMC 123",
            "http://x/PMC123",
            # `$` matches before a trailing newline, so an anchored match()
            # would let this through into a URL path.
            "PMC123\n",
        ],
    )
    def test_anything_else_raises(self, value):
        with pytest.raises(FullTextError):
            _normalise_pmc_id(value)

    def test_europe_pmc_validates_too(self):
        """One guard, both fetch helpers — Europe PMC's id is third-party too."""
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            with pytest.raises(FullTextError):
                service._fetch_europepmc("../../etc/passwd")
            mock_get.assert_not_called()


class TestPMCIDFallbackChain:
    """Where the two new steps sit in the chain, and what they must not cost.

    The order is the load-bearing part. Europe PMC's search returns the PMC ID
    *and* the free-PDF URL in one request, so the converter is consulted only
    after that search comes back without an id — never before it.
    """

    @staticmethod
    def _search(pmcid: str | None = None, pdf_url: str | None = None) -> MagicMock:
        hit: dict = {}
        if pmcid:
            hit["pmcid"] = pmcid
            hit["inEPMC"] = "Y"
        if pdf_url:
            hit["fullTextUrlList"] = {
                "fullTextUrl": [{"documentStyle": "pdf", "availability": "Free", "url": pdf_url}]
            }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"resultList": {"result": [hit] if hit else []}}
        return resp

    @staticmethod
    def _idconv(pmcid: str | None = None) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        records = [{"pmcid": pmcid}] if pmcid else []
        resp.json.return_value = {"status": "ok", "records": records}
        return resp

    @staticmethod
    def _xml(name: str = "sample_article.xml") -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / name).read_bytes()
        return resp

    def test_the_converter_rescues_a_search_that_found_nothing(self):
        """Europe PMC's index missed it; NCBI's mapping did not."""
        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(), self._idconv("PMC999"), self._xml()],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"

    def test_the_converter_is_not_consulted_when_the_search_found_an_id(self):
        """It costs a request, so it is spent only where the service gave up."""
        service = FullTextService(email="test@example.com")
        with patch.object(
            service, "_http_get", side_effect=[self._search(pmcid="PMC1"), self._xml()]
        ) as mock_get:
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc"
        assert mock_get.call_count == 2

    def test_a_converter_failure_does_not_cost_the_free_pdf_url(self):
        """The search already paid for that URL before the converter ran.

        No ``identifier``, so there is no cache and ``_download_and_cache_pdf``
        returns before making a request — two mocks, not three.
        """
        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                self._search(pdf_url="https://europepmc.org/x.pdf"),
                RuntimeError("connection reset"),
            ],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc_pdf"
        assert result.pdf_url == "https://europepmc.org/x.pdf"

    def test_ncbi_is_tried_for_a_caller_supplied_id(self):
        """The gap is the same whoever found the id — Tier 1a gets it too."""
        epmc_404 = MagicMock()
        epmc_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=[epmc_404, self._xml()]):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "ncbi_pmc"
        assert result.content_kind == "fulltext"

    def test_ncbi_is_tried_for_a_converter_discovered_id(self):
        epmc_404 = MagicMock()
        epmc_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(), self._idconv("PMC999"), epmc_404, self._xml()],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "ncbi_pmc"

    def test_ncbi_full_text_beats_the_free_pdf_beneath_it(self):
        """Structured JATS outranks a PDF that needs an optional extra to read."""
        epmc_404 = MagicMock()
        epmc_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                epmc_404,
                self._xml(),
                self._search(pdf_url="https://europepmc.org/x.pdf"),
            ],
        ) as mock_get:
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "ncbi_pmc"
        # The PDF-recovery search was never reached: NCBI answered first.
        assert mock_get.call_count == 2

    def test_an_ncbi_stub_does_not_become_the_last_resort_abstract(self):
        """The stub carries no text; the DOI link it would displace is better."""
        epmc_404 = MagicMock()
        epmc_404.status_code = 404
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                epmc_404,
                self._xml("ncbi_pmc_stub.xml"),
                self._search(),
                unpaywall_404,
            ],
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "doi"
        assert result.html is None

    def test_the_converter_is_consulted_when_the_search_itself_failed(self):
        """A transport failure at Europe PMC must not suppress the second source.

        A search that raised is exactly when an independent resolver earns its
        request. Folding the converter back inside the search's ``except``
        would skip it here — the enclosing handler would swallow the error and
        leave the block before the converter was reached.
        """
        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[RuntimeError("connection reset"), self._idconv("PMC999"), self._xml()],
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"

    def test_an_ncbi_abstract_becomes_the_last_resort(self):
        """A body-less NCBI reply carrying a real abstract is worth holding back.

        ``("ncbi_pmc", "abstract")`` is a new pair a caller can persist, so it
        is pinned through the chain and not only at ``_fetch_ncbi_pmc``. It is
        reachable only when Europe PMC *raised* rather than returning body-less
        — otherwise ``abstract_only`` is already filled from there.
        """
        epmc_404 = MagicMock()
        epmc_404.status_code = 404
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[
                epmc_404,
                self._xml("abstract_only_article.xml"),
                self._search(),
                unpaywall_404,
            ],
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "ncbi_pmc"
        assert result.content_kind == "abstract"
        assert result.html
        # The DOI link is hung off the abstract rather than displacing it.
        assert result.web_url == "https://doi.org/10.1/test"

    def test_ncbi_is_not_tried_without_a_pmc_id(self):
        """Neither the caller nor either resolver produced one."""
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service,
            "_http_get",
            side_effect=[self._search(), self._idconv(), unpaywall_404],
        ) as mock_get:
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.source == "doi"
        assert mock_get.call_count == 3


class TestHttpGet:
    """The one method that actually builds an httpx client.

    Every other test in this file patches ``_http_get`` itself, so its body is
    otherwise never executed — measured: replacing it with an unconditional
    ``raise AssertionError`` left the whole suite green, which is how the
    ``self._httpx`` indirection reached review unpinned.
    """

    def _fake_httpx(self, response):
        """An httpx stand-in whose ``Client()`` context yields a mock client."""
        client = MagicMock()
        client.get.return_value = response
        fake = MagicMock()
        fake.Client.return_value.__enter__.return_value = client
        return fake, client

    def test_the_client_carries_the_configured_timeout_and_follows_redirects(self):
        """``follow_redirects`` is load-bearing, not decoration.

        The DOI tier resolves through ``doi.org``, which answers with a 302 to
        the publisher; without this the chain would store the redirect stub.
        """
        response = MagicMock()
        fake_httpx, client = self._fake_httpx(response)

        service = FullTextService(email="test@example.com", timeout=12.5)
        with patch("bmlib.fulltext.service._require_httpx", return_value=fake_httpx):
            result = service._http_get("https://example.org/x", params={"a": "1"})

        assert result is response
        fake_httpx.Client.assert_called_once_with(timeout=12.5, follow_redirects=True)
        client.get.assert_called_once_with("https://example.org/x", params={"a": "1"})

    def test_the_client_is_closed_even_when_the_request_raises(self):
        """The ``with`` block owns the socket; a raising GET must not leak it."""
        fake_httpx, client = self._fake_httpx(MagicMock())
        client.get.side_effect = RuntimeError("connection reset")

        service = FullTextService(email="test@example.com")
        with patch("bmlib.fulltext.service._require_httpx", return_value=fake_httpx):
            with pytest.raises(RuntimeError, match="connection reset"):
                service._http_get("https://example.org/x")

        fake_httpx.Client.return_value.__exit__.assert_called_once()


# --- Package imports (issue #64) --------------------------------------------

#: Prelude that makes ``httpx`` unimportable, as a core-only install would.
#:
#: A ``sys.meta_path`` finder rather than a ``sys.modules`` sentinel, so the
#: failure is the ``ModuleNotFoundError`` a real absent dependency raises
#: rather than the "halted; None in sys.modules" ``ImportError`` a sentinel
#: produces.
_MASK_HTTPX = """
import sys


class _NoHttpx:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith("httpx."):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


sys.meta_path.insert(0, _NoHttpx())
for _name in [m for m in sys.modules if m == "httpx" or m.startswith("httpx.")]:
    del sys.modules[_name]
"""

#: Every module in ``bmlib.fulltext`` — seven of the ten that were unimportable
#: in a core-only install before this fix. ``_FETCHER_MODULES`` below holds the
#: other three. Measured one fresh interpreter per module, since a failed
#: import leaves the half-initialised parent in ``sys.modules`` and its
#: siblings then falsely read as fine.
_FULLTEXT_MODULES = [
    "bmlib.fulltext",
    "bmlib.fulltext.cache",
    "bmlib.fulltext.jats_parser",
    "bmlib.fulltext.models",
    "bmlib.fulltext.pdf_converter",
    "bmlib.fulltext.segmenter",
    "bmlib.fulltext.service",
]

#: The collateral half: each imports ``bmlib.fulltext.models`` for one
#: dataclass, and all three take an injected HTTP client rather than importing
#: httpx themselves.
_FETCHER_MODULES = [
    "bmlib.publications.fetchers.biorxiv",
    "bmlib.publications.fetchers.openalex",
    "bmlib.publications.fetchers.pubmed",
]


def _run(body: str, *, mask_httpx: bool = True, env: dict[str, str] | None = None):
    """Run ``body`` in a fresh interpreter, optionally with httpx masked.

    A subprocess because ``sys.modules`` in this process already holds httpx
    and every module under test — the trap that under-reported the defect when
    it was first measured.

    ``env`` is merged **over** the caller's environment rather than replacing
    it. Replacing drops ``PYTHONPATH``, which is the only way bmlib is
    reachable in an uninstalled checkout, and on Windows drops the
    ``USERPROFILE``/``HOMEPATH`` pair that :meth:`pathlib.Path.home` reads
    there — leaving a home-redirecting test asserting against a directory
    nothing could ever have been written to.
    """
    prelude = _MASK_HTTPX if mask_httpx else ""
    return subprocess.run(
        [sys.executable, "-c", prelude + body],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env} if env else None,
    )


class TestPackageImports:
    """What importing the package requires, and what it still offers.

    Issue #64: ``fulltext/__init__.py`` eagerly re-exported the service, whose
    top-level ``import httpx`` gated the whole subpackage — including the
    pure-dataclass ``models`` and the stdlib-only ``SectionSegmenter``.
    """

    def test_the_mask_itself_blocks_httpx(self):
        """Negative control: without this, every masked test below is vacuous.

        Four of the tests in this class run under the mask. A mask that
        silently failed to mask would let all four pass on a machine where
        httpx is installed, which is every machine that runs this suite.
        """
        completed = _run("import httpx\n")

        assert completed.returncode != 0
        assert "No module named 'httpx'" in completed.stderr

    def test_the_stdlib_only_modules_import_without_httpx(self):
        """Every module in the package, each in its own fresh interpreter."""
        for name in _FULLTEXT_MODULES:
            completed = _run(f"import {name}\n")
            assert completed.returncode == 0, f"{name}: {completed.stderr}"

    def test_the_segmenter_is_reachable_from_the_package_without_httpx(self):
        """The reported symptom: a segmenter that makes no HTTP request.

        It imports ``re``, ``statistics``, ``typing`` and
        ``bmlib.fulltext.models``, and is documented as standalone.
        """
        completed = _run(
            "from bmlib.fulltext import SectionSegmenter\nprint(SectionSegmenter.__module__)\n"
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "bmlib.fulltext.segmenter"

    def test_the_fetchers_import_without_httpx(self):
        """Collateral damage, in its own test so a regression names itself.

        Each fetcher imports ``bmlib.fulltext.models`` for
        ``FullTextSourceEntry`` and so inherited the gate. None imports httpx
        itself — all three take an injected client, and ``sync()`` builds the
        default one behind its own deferred import.
        """
        for name in _FETCHER_MODULES:
            completed = _run(f"import {name}\n")
            assert completed.returncode == 0, f"{name}: {completed.stderr}"

    def test_the_service_names_the_extra_when_httpx_is_missing(self, tmp_path):
        """Constructing the service without httpx is the guarded ImportError.

        Not the bare ``ModuleNotFoundError`` the eager import raised — the
        class name is the discriminator, since ``ModuleNotFoundError`` is a
        subclass of ``ImportError`` and an ``except ImportError`` would catch
        both.

        The home directory is redirected because the constructor otherwise
        creates a default ``FullTextCache`` on disk; asserting it stayed
        *entirely* empty pins the guard as the **first** statement, so a
        failed construction leaves nothing behind. Emptiness rather than three
        named paths, because ``_default_cache_dir()`` picks a different one per
        platform (``Library/Caches`` on darwin, ``AppData/Local`` on Windows,
        ``.cache`` elsewhere) and naming a subset lets the assertion pass
        vacuously wherever it guessed wrong. ``USERPROFILE`` is set alongside
        ``HOME`` since :meth:`pathlib.Path.home` reads only the former on
        Windows.
        """
        home = tmp_path / "home"
        home.mkdir()
        completed = _run(
            "from bmlib.fulltext import FullTextService\n"
            "try:\n"
            "    FullTextService(email='test@example.com')\n"
            "except ImportError as e:\n"
            "    print(type(e).__name__)\n"
            "    print(e)\n"
            "else:\n"
            "    print('NO ERROR')\n",
            env={"HOME": str(home), "USERPROFILE": str(home)},
        )

        assert completed.returncode == 0, completed.stderr
        kind, message = completed.stdout.strip().splitlines()[:2]
        assert kind == "ImportError", f"expected the guarded error, got {kind}"
        assert "bmlib[fulltext]" in message
        assert not any(home.iterdir()), f"construction left {list(home.iterdir())}"

    def test_a_broken_httpx_is_not_reported_as_an_absent_one(self, tmp_path):
        """The guard reports what was raised instead of asserting the cause.

        ``except ImportError`` also catches the ``ModuleNotFoundError`` a
        *present* httpx raises for its own missing dependency, and an
        ``ImportError`` raised inside httpx on a version skew. Diagnosing
        either as "not installed" sends the reader to ``pip install
        bmlib[fulltext]``, which reports "Requirement already satisfied" and
        changes nothing — so they run it, see success, retry, and get the
        identical error. ``_attach_pdf_text`` spells out the same reasoning
        for PyMuPDF.
        """
        shim = tmp_path / "shim"
        shim.mkdir()
        (shim / "httpx.py").write_text(
            "raise ImportError(\"cannot import name 'HTTPTransport' from 'httpx._core'\")\n"
        )
        completed = _run(
            "from bmlib.fulltext import FullTextService\n"
            "try:\n"
            "    FullTextService(email='test@example.com')\n"
            "except ImportError as e:\n"
            "    print(e)\n"
            "    print(type(e.__cause__).__name__)\n",
            mask_httpx=False,
            env={"PYTHONPATH": os.pathsep.join([str(shim), os.environ.get("PYTHONPATH", "")])},
        )

        assert completed.returncode == 0, completed.stderr
        message, cause = completed.stdout.strip().splitlines()[:2]
        assert "HTTPTransport" in message, f"the real cause was dropped: {message}"
        assert "bmlib[fulltext]" in message
        assert cause == "ImportError"

    def test_the_service_survives_pickling_and_deep_copying(self, tmp_path):
        """It retains no module object, which nothing can pickle.

        A configured service is exactly what a bulk caller hands to a
        ``ProcessPoolExecutor``; holding the httpx module on the instance made
        that a ``TypeError: cannot pickle 'module' object`` far from its cause.
        """
        service = FullTextService(
            email="test@example.com", cache=FullTextCache(cache_dir=tmp_path), timeout=7.0
        )

        restored = pickle.loads(pickle.dumps(service))

        assert restored.email == "test@example.com"
        assert restored.timeout == 7.0
        assert restored.cache.cache_dir == tmp_path
        assert copy.deepcopy(service).email == "test@example.com"

    def test_the_extra_the_error_message_names_is_a_real_one(self):
        """Otherwise the message and the packaging can drift apart silently.

        ``_require_httpx`` prescribes ``pip install bmlib[fulltext]`` as a
        string; nothing else ties that name to ``pyproject.toml``.
        """
        extras = importlib.metadata.metadata("bmlib").get_all("Provides-Extra") or []

        assert "fulltext" in extras
        assert "all" in extras

    def test_importing_the_package_does_not_import_httpx(self):
        """Lazy, not merely importable — measured where httpx *is* installed.

        Every other test here masks httpx, so all of them would still pass if
        the package imported it eagerly on a machine that has it. This one
        catches that.
        """
        completed = _run(
            "import sys\nimport bmlib.fulltext\nprint('httpx' in sys.modules)\n",
            mask_httpx=False,
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "False"

    def test_importing_the_package_does_not_load_the_service(self):
        """What the PEP 562 indirection buys, stated on its own.

        The guarded import in ``FullTextService.__init__`` already makes the
        package importable without httpx, so the test above passes with or
        without ``__getattr__`` — found by mutation, and the reason this test
        exists. What deferring adds is that ``service`` is not loaded until
        someone asks for it, which is what keeps the guarantee structural: no
        future top-level import in that module can gate the parser, the models
        or the segmenter again.
        """
        completed = _run(
            "import sys\n"
            "import bmlib.fulltext\n"
            "print('bmlib.fulltext.service' in sys.modules)\n"
            "bmlib.fulltext.FullTextService\n"
            "print('bmlib.fulltext.service' in sys.modules)\n",
            mask_httpx=False,
        )

        assert completed.returncode == 0, completed.stderr
        # Not loaded by the import; loaded by the first attribute access.
        assert completed.stdout.split() == ["False", "True"]

    def test_the_service_is_still_reachable_from_the_package(self):
        """Deferred, not removed: the import path callers use must not change."""
        from bmlib.fulltext import FullTextError, FullTextService

        assert FullTextService.__module__ == "bmlib.fulltext.service"
        assert FullTextError.__module__ == "bmlib.fulltext.service"

    def test_the_deferred_names_are_still_exported(self):
        import bmlib.fulltext as package

        assert "FullTextService" in package.__all__
        assert "FullTextError" in package.__all__

    def test_a_name_the_package_does_not_have_still_raises(self):
        """``__getattr__`` must not swallow a typo into something falsy."""
        import bmlib.fulltext as package

        with pytest.raises(AttributeError, match="no attribute 'not_a_real_name'"):
            package.not_a_real_name

    def test_dir_lists_the_deferred_names_without_hiding_anything(self):
        """The default ``__dir__`` omits them — they are not attributes yet.

        Adding them by returning ``__all__`` alone would trade one omission
        for a larger one: the submodules and every dunder disappear, breaking
        REPL completion for ``bmlib.fulltext.models``. Both halves are
        asserted, since the presence check alone passes under the narrowing.
        """
        import bmlib.fulltext as package

        listed = dir(package)
        assert {"FullTextService", "FullTextError"} <= set(listed)
        assert {"cache", "models", "jats_parser", "segmenter"} <= set(listed)
        assert "__name__" in listed

    def test_a_resolved_name_is_bound_and_not_re_resolved(self):
        """PEP 562's own recommendation: cache the lookup in ``globals()``.

        Pinned because the binding is what keeps repeat attribute access off
        this code path, and what puts the name in ``__dict__`` for tooling
        that reads it directly.
        """
        completed = _run(
            "import bmlib.fulltext as p\n"
            "print('FullTextService' in vars(p))\n"
            "p.FullTextService\n"
            "print('FullTextService' in vars(p))\n",
            mask_httpx=False,
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.split() == ["False", "True"]


class TestTheFailureSummaryReadsCorrectly:
    """``_TierFailures.describe()`` at its source, not through a tier chain.

    The wording is a documented interface — ``docs/manual/fulltext.md`` tells
    operators to grep for it — so it is pinned where it is produced. Reached
    only through ``fetch_fulltext``, three of its four shapes were asserted by
    loose substring and the singular branch not at all.
    """

    def test_nothing_recorded_says_so_without_claiming_an_absence(self):
        """Reachable when every attempt returned something, just not full text.

        It must not read as "every source answered that it had nothing" —
        that is a claim about sources this object never saw.
        """
        assert _TierFailures().describe() == "no attempt reported a failure"

    def test_one_fault_is_singular(self):
        failures = _TierFailures()
        failures.record(OSError("network is down"))
        assert failures.describe() == "1 attempt failed (OSError)"

    def test_many_faults_are_counted_and_their_types_sorted_and_deduplicated(self):
        """Types are a set for the reader's benefit; the count is not."""
        failures = _TierFailures()
        failures.record(OSError("a"))
        failures.record(TypeError("b"))
        failures.record(OSError("c"))
        assert failures.describe() == "3 attempts failed (OSError, TypeError)"

    def test_an_absence_is_counted_apart_from_a_fault(self):
        """The distinction the whole report exists to draw."""
        failures = _TierFailures()
        failures.record(FullTextUnavailableError("no OA copy"))
        failures.note_absence()
        assert failures.describe() == "2 sources had nothing"

    def test_faults_and_absences_are_reported_side_by_side(self):
        failures = _TierFailures()
        failures.record(OSError("down"))
        failures.record(FullTextUnavailableError("no OA copy"))
        assert failures.describe() == "1 attempt failed (OSError); 1 source had nothing"

    def test_an_unavailable_error_is_an_absence_not_a_fault(self):
        """``FullTextUnavailableError`` subclasses ``FullTextError`` — the
        sorting must key on the subclass, not on the base."""
        failures = _TierFailures()
        failures.record(FullTextError("Unpaywall HTTP 503"))
        failures.record(FullTextUnavailableError("DOI not found in Unpaywall"))
        assert failures.faults == ["FullTextError"]
        assert failures.absences == 1


class TestAnExhaustedChainReportsItself:
    """Issue #67 — a total retrieval failure must not read as "no free full text".

    Every tier swallows its own exception at DEBUG and moves on, which is
    right: a dead Unpaywall must not cost the DOI fallback. What it cost was
    that the *more* complete the failure, the quieter it got — a caller who
    had lost the network saw a normal-looking result for every paper in a
    corpus, with nothing above DEBUG to say so.
    """

    def test_a_chain_where_every_attempt_failed_says_so_and_counts_them(self, caplog):
        """The case that was silent: every attempt raised, nothing came back."""
        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", side_effect=OSError("network is down")),
        ):
            result = service.fetch_fulltext(doi="10.1/test", pmid="456")

        # The result itself is unchanged — the chain still degrades to a link.
        assert result.source == "doi"
        assert result.content_kind == "none"

        # Anchored, not a bare substring: "13 attempts failed" contains
        # "3 attempts failed", and Tier 0 records once per fetcher-supplied
        # source, so a two-digit count is reachable in production.
        assert "nothing was retrieved; 3 attempts failed (OSError)" in caplog.text
        # Three attempts are made on this path: the Europe PMC search, the ID
        # Converter, and Unpaywall. The PDF-render lookup does not run — it is
        # gated on xml_failed, which only the Tier 1a block sets, and Tier 1a
        # is skipped without a pmc_id.
        assert "had nothing" not in caplog.text

    def test_a_chain_that_was_offered_nothing_reports_absences_not_failures(self, caplog):
        """The control: the same empty-handed result, a different cause.

        Every source answers, and answers that it has nothing. Point 2 of the
        issue — "all nine raised" must read differently from "all nine
        returned empty" — is exactly this pair of tests, and the pair only
        works if the two produce different text.
        """
        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {"resultList": {"result": []}}
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                side_effect=[empty_search, _idconv_miss(), unpaywall_404],
            ),
        ):
            result = service.fetch_fulltext(doi="10.1/test", pmid="456")

        assert result.source == "doi"
        # Same three attempts as the test above, same empty-handed result —
        # and it must not say anything failed.
        assert "nothing was retrieved; 3 sources had nothing" in caplog.text
        assert "failed" not in caplog.text

    def test_an_unreachable_source_is_a_failure_not_an_absence(self, caplog):
        """The regression this pair was blind to.

        Every source returning HTTP 503 is a lost network or a firewall. Both
        resolvers used to report it by returning ``None`` — the same thing an
        empty result set returns — so a total outage produced the summary that
        means "an ordinary paywalled paper", which is issue #67 verbatim.
        """
        down = MagicMock()
        down.status_code = 503

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=down),
        ):
            result = service.fetch_fulltext(doi="10.1/test", pmid="456")

        assert result.source == "doi"
        assert "3 attempts failed (FullTextError)" in caplog.text
        assert "had nothing" not in caplog.text

    def test_the_abstract_only_exit_carries_the_same_report(self, caplog):
        """The one warning that already existed keeps working, and gains the count.

        It used to be the *only* warning on this path, which is what made the
        total failure quieter than the partial one — so its count is asserted
        exactly, not by a substring both branches satisfy.
        """
        body_less = MagicMock()
        body_less.status_code = 200
        body_less.content = (FIXTURES / "abstract_only_article.xml").read_bytes()
        unpaywall_404 = MagicMock()
        unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                # Every request the chain makes is supplied. Letting the list
                # run out instead made StopIteration the second recorded type,
                # so the only multi-type summary in the suite was a mock
                # artefact and the count moved whenever a tier was added.
                side_effect=[
                    body_less,  # Tier 1a: Europe PMC, abstract but no body
                    OSError("network is down"),  # Tier 1c: NCBI PMC
                    OSError("network is down"),  # PDF render URL lookup
                    unpaywall_404,  # Tier 2: Unpaywall has no OA copy
                ],
            ),
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.content_kind == "abstract"
        assert (
            "returning the abstract only; 2 attempts failed (OSError); 1 source had nothing"
            in caplog.text
        )

    def test_a_successful_retrieval_reports_nothing(self, caplog):
        """Negative control: the warning is not simply always emitted."""
        full_text = MagicMock()
        full_text.status_code = 200
        full_text.content = (FIXTURES / "sample_article.xml").read_bytes()

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=full_text),
        ):
            result = service.fetch_fulltext(pmc_id="PMC123")

        assert result.content_kind == "fulltext"
        assert caplog.text == ""

    def test_a_chain_that_failed_and_then_recovered_stays_silent(self, caplog):
        """Stronger than the control above, which never fails an attempt.

        A per-attempt warning would put a line into every bulk run for every
        transient blip on a chain that went on to succeed. Only exhaustion is
        worth a WARNING.
        """
        full_text = MagicMock()
        full_text.status_code = 200
        full_text.content = (FIXTURES / "sample_article.xml").read_bytes()
        idconv_hit = MagicMock()
        idconv_hit.status_code = 200
        idconv_hit.json.return_value = {"records": [{"pmcid": "PMC123", "live": "true"}]}

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                # The Europe PMC search blips; the ID Converter recovers the
                # PMC ID and Europe PMC then serves the article.
                side_effect=[OSError("transient"), idconv_hit, full_text],
            ),
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.content_kind == "fulltext"
        assert caplog.text == ""

    def test_a_call_with_identifiers_that_all_failed_does_not_blame_the_caller(self, caplog):
        """The third empty-handed exit, which reported nothing at all.

        With a ``pmc_id`` and no DOI or PMID there is no fallback URL, so the
        chain raises. It used to raise ``No identifiers provided`` — an
        identifier *was* provided — and skipped the summary entirely, which is
        the same misdirection as #67 on the one path that has no result to
        return.
        """
        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", side_effect=OSError("network is down")),
            pytest.raises(FullTextError, match="no DOI or PMID to fall back on"),
        ):
            service.fetch_fulltext(pmc_id="PMC123")

        assert "2 attempts failed (OSError)" in caplog.text

    def test_a_call_with_no_identifiers_at_all_still_says_so(self, caplog):
        """The documented escaping case keeps its message — and stays quiet.

        An empty call is not an exhausted chain: no source was asked
        anything, so summarising the attempts would describe a run that never
        happened.
        """
        service = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            with pytest.raises(FullTextError, match="No identifiers provided"):
                service.fetch_fulltext()

        assert caplog.text == ""

    def test_an_article_404_is_an_absence_from_every_source(self, caplog):
        """One status code must not land in two buckets for the same reason.

        Europe PMC mapped an article 404 to an absence while NCBI and the
        fetcher-URL helper mapped it to a fault, so a paper both PMC endpoints
        404 on reported failures beside its absences — putting an ordinary
        stale URL into the bucket the manual tells operators to act on.

        A *search* endpoint is the exception, and deliberately so: Europe PMC
        answers "no such paper" with HTTP 200 and an empty result list, so a
        404 there means the API path is wrong, which is a fault. Hence the
        one failed attempt below.
        """
        missing = MagicMock()
        missing.status_code = 404

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=missing),
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", doi="10.1/test")

        assert result.source == "doi"
        # Europe PMC, NCBI and Unpaywall each answered "not here"; only the
        # render-URL search counts as broken.
        assert "1 attempt failed (FullTextError); 3 sources had nothing" in caplog.text

    def test_a_fetcher_supplied_url_that_404s_is_an_absence(self, caplog):
        """Tier 0's own 404, which used to be a fault.

        A stored source URL going stale is ordinary — bioRxiv reorganises,
        a publisher moves a path — and counting it as a failure inflated the
        actionable bucket for every corpus with fetcher-supplied sources.
        """
        missing = MagicMock()
        missing.status_code = 404
        sources = [FullTextSourceEntry(source="biorxiv", url="http://x/a.xml", format="xml")]

        service = FullTextService(email="test@example.com")
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=missing),
        ):
            service.fetch_fulltext(fulltext_sources=sources, pmid="456")

        # The Tier 0 entry is the absence. The two faults are the Europe PMC
        # search and the ID Converter, both endpoint 404s — see the test above
        # for why those stay faults.
        assert "2 attempts failed (FullTextError); 1 source had nothing" in caplog.text


class TestACorruptCacheEntryDoesNotAbortTheRun:
    """A cache read is best-effort, exactly as a cache write is (#71).

    ``_check_cache`` was called unguarded and ``get_html`` does a bare
    ``read_text``, so a file truncated mid-multibyte-sequence raised
    ``UnicodeDecodeError`` straight out of ``fetch_fulltext()`` — a hard stop
    where re-fetching over the network was available, and one bad file made a
    paper permanently unfetchable.
    """

    CACHE_ID = _sanitize_identifier("10.1234/test")

    def _cache_holding_a_corrupt_entry(self, tmp_path) -> FullTextCache:
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>whatever was there before</p>", self.CACHE_ID)
        path = tmp_path / "html" / f"{self.CACHE_ID}.html"
        # Truncated mid-multibyte-sequence: what a killed process, a full
        # disk or a filesystem fault leaves behind.
        path.write_bytes("<p>Ω</p>".encode()[:4])
        return cache

    @staticmethod
    def _full_text_response() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()
        return resp

    def test_the_corrupt_entry_really_is_unreadable(self, tmp_path):
        """Negative control: without it every test below could be vacuous."""
        cache = self._cache_holding_a_corrupt_entry(tmp_path)

        with pytest.raises(UnicodeDecodeError):
            cache.get_html(self.CACHE_ID)

    def test_an_unreadable_entry_falls_through_to_the_network(self, tmp_path):
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)

        with patch.object(service, "_http_get", return_value=self._full_text_response()):
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"

    def test_the_unreadable_entry_is_warned_about(self, tmp_path, caplog):
        """Per article, unlike the write warning.

        A failed *write* is a property of the directory, so it is said once;
        an unreadable file is a property of that one file, and naming it is
        what lets an operator find it.

        Two *different* corrupt articles are used rather than two runs over
        one, because a run heals the entry it could not read. Once-per-service
        gating would suppress the second line, which is the regression this
        guards; asserting a single warning from a single fetch would not.
        """
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        other = _sanitize_identifier("10.1234/other")
        cache.save_html("<p>x</p>", other)
        (tmp_path / "html" / f"{other}.html").write_bytes("<p>Ω</p>".encode()[:4])
        service = FullTextService(email="test@example.com", cache=cache)

        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=self._full_text_response()),
        ):
            service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/test")
            service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/other")

        warnings = [r for r in caplog.records if "Could not read the cached" in r.message]
        assert len(warnings) == 2
        assert {self.CACHE_ID, other} == {w.args[0] for w in warnings}

    def test_the_warning_names_the_exception_type(self, tmp_path, caplog):
        """So a bmlib bug does not read as an ordinary bad file.

        ``%s`` on the exception renders its message alone, and an
        ``AttributeError`` printed under a sentence about an unreadable cache
        file is #72's failure in miniature. ``_TierFailures`` renders the type
        for exactly this reason, and a bare ``OSError()`` would otherwise
        print an empty pair of brackets.
        """
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)

        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=self._full_text_response()),
        ):
            service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/test")

        assert "UnicodeDecodeError" in caplog.text

    def test_an_entry_that_fails_for_any_other_reason_falls_through_too(self, tmp_path):
        """The guard is deliberately broad, and narrowing it restores the bug.

        A decode error is only the shape #71 was reported in. A cached file
        the process cannot read — wrong permissions, an I/O fault, a path
        that is not a regular file — fails with an ``OSError`` instead, and a
        guard written for ``UnicodeDecodeError`` alone would let that abort
        the run exactly as before. Provoked with a directory standing where
        the file should be, which raises for every user including root.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        (tmp_path / "html" / f"{self.CACHE_ID}.html").mkdir()
        service = FullTextService(email="test@example.com", cache=cache)

        with patch.object(service, "_http_get", return_value=self._full_text_response()):
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"

    def test_a_successful_re_fetch_replaces_the_bad_entry(self, tmp_path):
        """Which is why the guard does not delete the file it could not read."""
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)

        with patch.object(service, "_http_get", return_value=self._full_text_response()):
            service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/test")

        assert "<h1>" in (cache.get_html(self.CACHE_ID) or "")

    def test_a_readable_entry_is_still_served_from_cache(self, tmp_path):
        """Negative control: the guard does not swallow good cache hits."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<h1>Cached</h1>", self.CACHE_ID)
        service = FullTextService(email="test@example.com", cache=cache)

        with patch.object(service, "_http_get") as mock_get:
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1234/test")
            mock_get.assert_not_called()

        assert result.source == "cached"


class TestACorruptCacheEntryHeals:
    """Falling through to the network is not enough on its own.

    "A successful re-fetch overwrites it" holds only when the chain returns
    JATS full text. An article served as a PDF writes ``pdfs/`` and never
    touches ``html/``, and because the undecodable HTML entry is consulted
    *first*, the freshly cached PDF is unreachable behind it — so the article
    warns and re-downloads on every run, permanently. The unreadable entry is
    therefore moved aside rather than left in place.
    """

    CACHE_ID = _sanitize_identifier("10.1234/test")
    PDF_MAGIC = b"%PDF-1.4 fake content for testing"

    def _cache_holding_a_corrupt_entry(self, tmp_path) -> FullTextCache:
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>whatever was there before</p>", self.CACHE_ID)
        (tmp_path / "html" / f"{self.CACHE_ID}.html").write_bytes("<p>Ω</p>".encode()[:4])
        return cache

    def _resolve_via_unpaywall_pdf(self, service) -> object:
        search_empty = MagicMock()
        search_empty.status_code = 200
        search_empty.json.return_value = {"resultList": {"result": []}}
        unpaywall = MagicMock()
        unpaywall.status_code = 200
        unpaywall.json.return_value = {
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        }
        pdf = MagicMock()
        pdf.status_code = 200
        pdf.content = self.PDF_MAGIC
        return patch.object(
            service,
            "_http_get",
            side_effect=[search_empty, _idconv_miss(), unpaywall, pdf],
        )

    def test_an_article_served_as_a_pdf_is_cached_on_the_next_run(self, tmp_path):
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        service = FullTextService(email="test@example.com", cache=cache, convert_pdfs=False)

        with self._resolve_via_unpaywall_pdf(service):
            first = service.fetch_fulltext(doi="10.1234/test", identifier="10.1234/test")
        assert first.source == "unpaywall"

        with patch.object(service, "_http_get") as mock_get:
            second = service.fetch_fulltext(doi="10.1234/test", identifier="10.1234/test")
            mock_get.assert_not_called()

        assert second.source == "cached"
        assert second.file_path is not None

    def test_the_unreadable_bytes_are_kept_beside_the_cache(self, tmp_path):
        """Moved aside, not deleted — a failed re-fetch leaves the evidence."""
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        corrupt = (tmp_path / "html" / f"{self.CACHE_ID}.html").read_bytes()
        service = FullTextService(email="test@example.com", cache=cache, convert_pdfs=False)

        with self._resolve_via_unpaywall_pdf(service):
            service.fetch_fulltext(doi="10.1234/test", identifier="10.1234/test")

        aside = tmp_path / "html" / f"{self.CACHE_ID}.html.corrupt"
        assert aside.read_bytes() == corrupt

    def test_a_retrieval_that_also_fails_does_not_lose_the_entry(self, tmp_path):
        """Negative control: nothing is destroyed when the network is down too."""
        cache = self._cache_holding_a_corrupt_entry(tmp_path)
        service = FullTextService(email="test@example.com", cache=cache, convert_pdfs=False)

        with patch.object(service, "_http_get", side_effect=OSError("network down")):
            service.fetch_fulltext(doi="10.1234/test", identifier="10.1234/test")

        assert (tmp_path / "html" / f"{self.CACHE_ID}.html.corrupt").exists()

    def test_a_broken_pdf_backend_is_not_blamed_on_the_cache(self, tmp_path):
        """``_check_cache`` re-extracts, so a converter fault reaches its guard.

        Narrowed to ``ImportError``, a ``ValueError`` from ``get_converter()``
        escaped ``_attach_pdf_text`` entirely and surfaced two frames up as
        "could not read the cached full text" — discarding a cached PDF that
        read perfectly, and re-downloading it into the identical deterministic
        fault. It is reported where it happens instead.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf(self.PDF_MAGIC, self.CACHE_ID)
        service = FullTextService(email="test@example.com", cache=cache, convert_pdfs=True)

        with (
            patch("bmlib.fulltext.service.get_converter", side_effect=ValueError("no such")),
            patch.object(service, "_http_get") as mock_get,
        ):
            result = service.fetch_fulltext(doi="10.1234/test", identifier="10.1234/test")
            mock_get.assert_not_called()

        assert result.source == "cached"
        assert result.file_path is not None


class TestCacheWriteFailuresAreReported:
    """A cache that cannot be written to means every run re-fetches — say so once."""

    @staticmethod
    def _service_with_an_unwritable_cache(tmp_path) -> FullTextService:
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html = MagicMock(side_effect=OSError("read-only file system"))
        return FullTextService(email="test@example.com", cache=cache)

    @staticmethod
    def _full_text_response() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()
        return resp

    def test_a_failed_cache_write_is_warned_about(self, tmp_path, caplog):
        service = self._service_with_an_unwritable_cache(tmp_path)
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=self._full_text_response()),
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/test")

        # The retrieval itself still succeeds — the content is already in hand.
        assert result.content_kind == "fulltext"
        assert "cache" in caplog.text.lower()
        # Report what was raised rather than asserting the cause, as the
        # conventions require of every guarded optional path.
        assert "read-only file system" in caplog.text

    def test_a_real_full_disk_is_reported_the_same_way(self, tmp_path, caplog, monkeypatch):
        """The seam #70 actually changed, exercised without a mock.

        Every other test in this class fakes the failure by replacing
        ``save_html`` outright, so the real ``_atomic_write`` never runs in a
        service-level test at all — and #70 turned ``save_html`` from a method
        that never raised into one that does. Faulting ``os.fsync`` instead
        drives the genuine path: the write raises, the retrieval still
        succeeds, and nothing is left in the cache directory.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)

        def no_space(fd: int) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", no_space)

        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=self._full_text_response()),
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/test")

        assert result.content_kind == "fulltext"
        assert "No space left on device" in caplog.text
        assert cache.get_html(_sanitize_identifier("10.1/test")) is None
        assert list((tmp_path / "html").iterdir()) == []

    def test_the_cache_write_warning_is_said_once(self, tmp_path, caplog):
        """The cause is a property of the directory, not of the article.

        Warning per article would put one line per paper into a bulk run's
        log; the ``bmlib[pdf]`` warning set the one-shot precedent.
        """
        service = self._service_with_an_unwritable_cache(tmp_path)
        with (
            caplog.at_level("WARNING"),
            patch.object(service, "_http_get", return_value=self._full_text_response()),
        ):
            for n in range(3):
                service.fetch_fulltext(pmc_id="PMC123", identifier=f"10.1/test-{n}")

        assert len([r for r in caplog.records if "cache" in r.getMessage().lower()]) == 1

    def test_a_failed_pdf_cache_write_is_warned_about_too(self, tmp_path, caplog):
        """The half that was silent.

        An unwritable directory stops PDFs caching exactly as it stops HTML,
        and a corpus served mostly by Unpaywall never writes HTML at all — so
        a warning only the HTML path could emit stayed silent for precisely
        the callers it was meant to reach. Worse, the failure was folded into
        the download's own handler and logged as "PDF download failed", which
        names the wrong thing.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf = MagicMock(side_effect=OSError("read-only file system"))
        service = FullTextService(email="test@example.com", cache=cache)

        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {"resultList": {"result": []}}
        unpaywall = MagicMock()
        unpaywall.status_code = 200
        unpaywall.json.return_value = {"best_oa_location": {"url_for_pdf": "http://x/a.pdf"}}
        pdf = MagicMock()
        pdf.status_code = 200
        pdf.content = b"%PDF-1.4 fake"

        with (
            caplog.at_level("WARNING"),
            patch.object(
                service, "_http_get", side_effect=[empty_search, _idconv_miss(), unpaywall, pdf]
            ),
        ):
            result = service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

        # The PDF URL is still returned — only the caching failed.
        assert result.pdf_url == "http://x/a.pdf"
        assert result.file_path is None
        assert "read-only file system" in caplog.text

    def test_html_and_pdf_write_failures_share_one_warning(self, tmp_path, caplog):
        """One unwritable directory, one line — not one per format."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html = MagicMock(side_effect=OSError("read-only file system"))
        cache.save_pdf = MagicMock(side_effect=OSError("read-only file system"))
        service = FullTextService(email="test@example.com", cache=cache)

        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {"resultList": {"result": []}}
        unpaywall = MagicMock()
        unpaywall.status_code = 200
        unpaywall.json.return_value = {"best_oa_location": {"url_for_pdf": "http://x/a.pdf"}}
        pdf = MagicMock()
        pdf.status_code = 200
        pdf.content = b"%PDF-1.4 fake"

        with caplog.at_level("WARNING"):
            with patch.object(service, "_http_get", return_value=self._full_text_response()):
                service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/html")
            with patch.object(
                service, "_http_get", side_effect=[empty_search, _idconv_miss(), unpaywall, pdf]
            ):
                service.fetch_fulltext(doi="10.1/test", identifier="10.1/pdf")

        assert len([r for r in caplog.records if "full-text cache" in r.getMessage()]) == 1


class TestAnUncreatableCacheDirectoryDoesNotAbortConstruction:
    """#75 — the last place in fulltext/ where the cache was not best-effort.

    Everywhere else already degrades: a failed write warns once (#67), a
    failed read falls through to the network and quarantines the entry (#71).
    The one place it was fatal is the one place the caller has done nothing
    wrong yet.

    The faults below are real filesystem and stdlib faults, not a stubbed
    constructor, so each test exercises the exception the platform actually
    raises rather than one chosen to match the guard.
    """

    @staticmethod
    def _default_dir_at(path, monkeypatch) -> Path:
        """Point the default cache location at ``path``, and return it.

        Every test here goes through this one seam so a rename of
        ``_default_cache_dir`` breaks them together rather than breaking the
        negative controls separately from what they control.
        """
        monkeypatch.setattr("bmlib.fulltext.cache._default_cache_dir", lambda: path)
        return path

    @classmethod
    def _blocked_default_dir(cls, tmp_path, monkeypatch) -> Path:
        """Point the default cache location at a file, and return it."""
        blocker = tmp_path / "notadir"
        blocker.write_text("I am a file, not a directory")
        return cls._default_dir_at(blocker, monkeypatch)

    def test_a_file_in_the_way_leaves_a_service_with_no_cache(self, tmp_path, monkeypatch):
        """Also the "no fallback location" half of the decision.

        A guard that relocated to a temp directory would leave
        ``service.cache`` set, so this fails under that change too.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)

        service = FullTextService(email="test@example.com")

        assert service.cache is None

    def test_a_usable_default_directory_still_yields_a_cache(self, tmp_path, monkeypatch):
        """Negative control for every ``cache is None`` assertion here.

        A guard that returned ``None`` unconditionally — or a fault that never
        fired — would satisfy those assertions while proving nothing.
        """
        self._default_dir_at(tmp_path / "fresh", monkeypatch)

        service = FullTextService(email="test@example.com")

        assert isinstance(service.cache, FullTextCache)

    def test_an_unexpected_error_from_the_cache_still_propagates(self, monkeypatch):
        """The upper bound on the guard, which prose alone cannot hold.

        ``except (OSError, RuntimeError)`` is deliberately not
        ``except Exception``: inside that one constructor ``RuntimeError`` has
        exactly one source, so a bmlib bug must still surface as one. Widening
        the guard catches strictly more, so nothing already here can fail on
        it — this is the test that does.
        """

        def _exploding_cache():
            raise ValueError("a bmlib bug, not an environment fault")

        monkeypatch.setattr("bmlib.fulltext.service.FullTextCache", _exploding_cache)

        with pytest.raises(ValueError, match="a bmlib bug"):
            FullTextService(email="test@example.com")

    def test_a_home_directory_that_cannot_be_determined_is_survived(self, monkeypatch):
        """The half ``except OSError`` alone would miss.

        ``_default_cache_dir()`` runs before any ``mkdir`` and calls
        ``Path.home()``, which raises ``RuntimeError`` — not ``OSError`` —
        when there is no ``HOME`` and no passwd entry, which is an ordinary
        distroless container.
        """
        monkeypatch.setattr(os.path, "expanduser", lambda p: p)
        # Precondition: assert the mechanism, so a future Python changing how
        # Path.home() resolves fails loudly here instead of leaving the test
        # passing for the wrong reason.
        with pytest.raises(RuntimeError):
            Path.home()

        service = FullTextService(email="test@example.com")

        assert service.cache is None

    def test_the_warning_names_what_was_raised(self, tmp_path, monkeypatch, caplog):
        """A bmlib bug must not read as an ordinary environment fault (#71).

        ``str()`` on a ``FileExistsError`` carries the errno and the path but
        never the class name, so the type is interpolated separately.
        """
        blocker = self._blocked_default_dir(tmp_path, monkeypatch)

        with caplog.at_level("WARNING"):
            FullTextService(email="test@example.com")

        assert "FileExistsError" in caplog.text
        assert str(blocker) in caplog.text
        # Says what it costs the operator, in the words the unwritable-cache
        # warning already uses — the same fault with the same consequence.
        assert "re-fetch" in caplog.text.lower()

    def test_the_warning_names_the_cost_and_a_remedy(self, tmp_path, monkeypatch, caplog):
        """ "Nothing will be cached" alone understates the degraded run.

        A PDF is fetched *into* the cache, so with no cache it is not fetched
        at all and a PDF-only article comes back as a bare URL. That is lost
        content, not repeated traffic, and an operator told only about caching
        would go looking for a network fault. The remedy is named because it
        is the one sentence that ends the problem, as
        :meth:`_attach_pdf_text`'s warning already does for ``bmlib[pdf]``.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)

        with caplog.at_level("WARNING"):
            FullTextService(email="test@example.com")

        assert "bare URL" in caplog.text
        assert "cache_dir" in caplog.text

    def test_a_caller_supplied_cache_never_reaches_the_guard(self, tmp_path, monkeypatch):
        """An explicit cache is used as given, fault in the default or not."""
        self._blocked_default_dir(tmp_path, monkeypatch)
        supplied = FullTextCache(cache_dir=tmp_path / "mine")

        service = FullTextService(email="test@example.com", cache=supplied)

        assert service.cache is supplied

    def test_retrieval_still_works_with_no_cache(self, tmp_path, monkeypatch, caplog):
        """Degrading must degrade, not relocate the crash to the first fetch.

        Every ``self.cache`` use site is already guarded, so no new plumbing
        was needed — but "already guarded" is a claim about code that had
        never run, since ``self.cache`` could not be ``None`` before #75.
        This is what executes it.

        The silence assertion is what *pins* those guards, and it is not a
        stylistic preference about logs. Delete either
        ``self.cache is not None`` check and the retrieval still succeeds:
        ``_check_cache(None, ...)`` raises ``AttributeError`` into #71's
        best-effort read handler and ``_cache_html`` raises into #67's write
        handler, so the only symptom is a pair of WARNINGs blaming the
        environment for a bmlib bug — per article, per run. That is the exact
        failure those two issues exist to prevent, and without this assertion
        the whole suite stays green through it.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com")
        assert service.cache is None

        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        # The construction warning above is expected; only the retrieval is
        # required to be quiet.
        caplog.clear()
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(service, "_http_get", return_value=resp),
        ):
            result = service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/test")

        assert result.source == "europepmc"
        assert result.content_kind == "fulltext"
        assert result.html is not None

        noisy = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert noisy == []

    def test_nothing_is_written_where_the_cache_would_have_gone(self, tmp_path, monkeypatch):
        """Nothing is created, and the blocking file is left exactly as it was.

        A guard that swallowed the fault but left a half-built cache behind
        would pass every assertion above. This is also the "no writability
        probe" half of the decision: a probe would litter the operator's cache
        directory with a file that is not an article, and this asserts the
        directory holds nothing but what the test put there.
        """
        blocker = self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com")

        resp = MagicMock()
        resp.status_code = 200
        resp.content = (FIXTURES / "sample_article.xml").read_bytes()

        with patch.object(service, "_http_get", return_value=resp):
            service.fetch_fulltext(pmc_id="PMC123", identifier="10.1/test")

        assert blocker.is_file()
        assert blocker.read_text() == "I am a file, not a directory"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["notadir"]

    @staticmethod
    def _unpaywall_pdf_only() -> list[MagicMock]:
        """Europe PMC finds nothing; Unpaywall offers a free PDF."""
        search = MagicMock()
        search.status_code = 200
        search.json.return_value = {"resultList": {"result": []}}

        unpaywall = MagicMock()
        unpaywall.status_code = 200
        unpaywall.json.return_value = {
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}
        }
        return [search, _idconv_miss(), unpaywall]

    def test_a_pdf_left_as_a_url_does_not_blame_a_missing_identifier(
        self, tmp_path, monkeypatch, caplog
    ):
        """The one dead branch that starts lying once it is reachable.

        ``if not cache_id or not self.cache:`` logged "no identifier was
        given" for both. With a failed cache and an identifier in hand that is
        simply false, and it is the only line the operator gets about why a
        PDF they asked to have extracted was left as a URL.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com", convert_pdfs=True)

        caplog.clear()
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            result = service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

        assert result.pdf_url == "https://example.com/paper.pdf"
        assert "no identifier was given" not in caplog.text
        assert "cache could not be created" in caplog.text

    def test_the_no_cache_pdf_line_stays_at_debug(self, tmp_path, monkeypatch, caplog):
        """It repeats per article, and construction already said it.

        ``caplog.text`` cannot tell DEBUG from WARNING, so the test above
        would pass just as well if this line were promoted — and a bulk run
        would then carry one operator-visible line per paper restating the
        startup warning. The sibling "no identifier" line stays at INFO
        because nothing warned about an identifier the caller had not passed.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com", convert_pdfs=True)

        caplog.clear()
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

        matched = [r for r in caplog.records if "cache could not be created" in r.getMessage()]
        assert [r.levelno for r in matched] == [logging.DEBUG]

    def test_the_no_cache_pdf_line_is_not_gated_on_convert_pdfs(
        self, tmp_path, monkeypatch, caplog
    ):
        """The caller who turned extraction off is the one who wanted the file.

        The ``convert_pdfs`` gate on the sibling branch is right there — that
        message is *about* extraction. Here the download is skipped either
        way, so ``file_path`` is lost whatever the flag says, and gating the
        only diagnostic on it leaves that caller with nothing at any level.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com", convert_pdfs=False)

        caplog.clear()
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            result = service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

        assert result.file_path is None
        assert "cache could not be created" in caplog.text

    def test_no_cache_and_no_identifier_reports_the_cache(self, tmp_path, monkeypatch, caplog):
        """Which diagnostic wins when both are absent, now that both can be.

        Splitting the old single guard made the order a decided behaviour. The
        cache is reported because it is the fault the caller cannot fix per
        call: passing an ``identifier`` would change nothing here.
        """
        self._blocked_default_dir(tmp_path, monkeypatch)
        service = FullTextService(email="test@example.com", convert_pdfs=True)

        caplog.clear()
        with (
            caplog.at_level(logging.DEBUG),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.pdf_url == "https://example.com/paper.pdf"
        assert "cache could not be created" in caplog.text
        assert "no identifier was given" not in caplog.text

    def test_a_genuinely_missing_identifier_still_says_so(self, tmp_path, caplog):
        """Negative control: the message above is suppressed, not deleted.

        Without this, deleting the "no identifier was given" line outright
        would pass the test above while losing a real diagnostic.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache, convert_pdfs=True)

        with (
            caplog.at_level("INFO"),
            patch.object(service, "_http_get", side_effect=self._unpaywall_pdf_only()),
        ):
            result = service.fetch_fulltext(doi="10.1/test")

        assert result.pdf_url == "https://example.com/paper.pdf"
        assert "no identifier was given" in caplog.text


class TestFreePDFAvailability:
    """Issue #79 — Tier 1d accepted ``availability == "Free"`` only.

    Europe PMC labels a ``fullTextUrl`` entry with a display string *and* a
    short code, and "Free" is the rare label. Measured over 600 recent
    MEDLINE records (``scripts/sample_free_pdf_urls.py``), of 326 entries with
    ``documentStyle == "pdf"``: 312 were "Open access" (95.7%) and 14 were
    "Free" (4.3%). Both are the identical ``?pdf=render`` URL on the identical
    host, so the tier was discarding about 95% of the PDFs it exists to find,
    with nothing logged at any level.
    """

    @staticmethod
    def _hit(
        style: str = "pdf",
        availability: object = "Open access",
        code: str | None = "OA",
        url: str = "https://ex/a.pdf",
    ) -> dict[str, object]:
        """One search hit carrying a single ``fullTextUrl`` entry.

        One builder for every test in this class, so a rejection test cannot
        pass because its fixture was malformed in some unrelated way — the
        acceptance tests use the same builder and would fail too.

        ``availability`` is typed ``object`` rather than ``str | None``
        because one test deliberately supplies the malformed non-string shape
        that used to raise ``TypeError`` out of the hashing ``in`` test.
        """
        entry: dict[str, object] = {"documentStyle": style, "url": url}
        if availability is not None:
            entry["availability"] = availability
        if code is not None:
            entry["availabilityCode"] = code
        return {"fullTextUrlList": {"fullTextUrl": [entry]}}

    def test_an_open_access_pdf_is_taken(self) -> None:
        """The 95.7% case that was being discarded."""
        assert _extract_free_pdf_url(self._hit()) == "https://ex/a.pdf"

    def test_a_free_pdf_is_still_taken(self) -> None:
        """The 4.3% case that already worked — the widening must not narrow."""
        hit = self._hit(availability="Free", code="F")
        assert _extract_free_pdf_url(hit) == "https://ex/a.pdf"

    def test_a_subscription_entry_is_rejected(self) -> None:
        """The whole point of an allow-list: never download a paywalled PDF."""
        hit = self._hit(availability="Subscription required", code="S")
        assert _extract_free_pdf_url(hit) is None

    def test_an_entry_with_no_code_falls_back_to_the_label(self) -> None:
        """Every entry in the 1,263-entry sample carried a code; nothing
        documents that they must, so the display string stays a fallback."""
        hit = self._hit(code=None)
        assert _extract_free_pdf_url(hit) == "https://ex/a.pdf"

    def test_an_empty_code_falls_back_to_the_label_rather_than_rejecting(self) -> None:
        """``if isinstance(code, str) and code:`` — the ``and code`` half.

        An empty ``availabilityCode`` is not a code bmlib failed to
        recognise; it is an entry that carried none, so it takes the display
        string's fallback. Nothing else in this class distinguishes that from
        ``if isinstance(code, str):``, which would reject this entry outright.
        """
        hit = self._hit(availability="Open access", code="")
        assert _extract_free_pdf_url(hit) == "https://ex/a.pdf"

    def test_a_non_string_availability_is_rejected_without_raising(self) -> None:
        """A malformed payload is an entry to skip, not a bmlib defect.

        ``x in frozenset`` hashes ``x``, so an ``availability`` arriving as a
        JSON object raised ``TypeError: unhashable type: 'dict'`` — and
        ``TypeError`` is in ``_BUG_TYPES``, so issue #72's new warning would
        have accused bmlib of a defect over Europe PMC's bytes.
        """
        hit = self._hit(availability={"value": "Open access"}, code=None)
        assert _extract_free_pdf_url(hit) is None

    def test_an_unknown_code_is_rejected_even_when_the_label_looks_free(self) -> None:
        """The under-credit rule, and the reason the code is authoritative.

        A future code bmlib has never seen must cost a retrieval rather than
        risk a paywalled download, so a present-but-unknown code is *not*
        allowed to fall back to the label it happens to carry.
        """
        hit = self._hit(availability="Open access", code="OA2")
        assert _extract_free_pdf_url(hit) is None

    def test_a_non_pdf_entry_is_rejected_however_free(self) -> None:
        """``documentStyle`` still gates: the HTML entry is not a PDF."""
        hit = self._hit(style="html")
        assert _extract_free_pdf_url(hit) is None

    def test_an_open_access_render_url_now_reaches_the_pdf_tier(self) -> None:
        """End to end: the tier fires where it used to fall through to a link."""
        search = MagicMock()
        search.status_code = 200
        search.json.return_value = {"resultList": {"result": [dict(self._hit(), inEPMC="N")]}}
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        with patch.object(service, "_http_get", side_effect=[search, _idconv_miss()]):
            result = service.fetch_fulltext(doi="10.1/test", identifier=None)

        assert result.source == "europepmc_pdf"
        assert result.pdf_url == "https://ex/a.pdf"


class TestWarnOnce:
    """The one-shot mechanism shared by #68's exception path and #72's bug path.

    Both failure modes hit every article in a run when they hit at all, so
    per-article is never right for either; and both need *per-cause* keys, so
    a second distinct fault is not hidden by the first — the "the more complete
    the failure, the quieter it gets" shape #67 existed to fix.
    """

    def test_the_same_key_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        service = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            service._warn_once("k", "something went wrong: %s", "detail")
            service._warn_once("k", "something went wrong: %s", "detail")
        assert caplog.text.count("something went wrong") == 1

    def test_different_keys_each_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        """The reason it is a keyed set and not a boolean."""
        service = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            service._warn_once("a", "first fault")
            service._warn_once("b", "second fault")
        assert "first fault" in caplog.text
        assert "second fault" in caplog.text

    def test_two_services_do_not_share_suppression(self, caplog: pytest.LogCaptureFixture) -> None:
        """Per service, like the booleans it replaces — not process-wide."""
        one = FullTextService(email="test@example.com")
        two = FullTextService(email="test@example.com")
        with caplog.at_level("WARNING"):
            one._warn_once("k", "the fault")
            two._warn_once("k", "the fault")
        assert caplog.text.count("the fault") == 2


class TestASwallowedBugDoesNotStayAtDebug:
    """Issue #72 — ``describe()`` is consulted at one exit: total exhaustion.

    A bug that every PMC tier hits, papered over by one tier that still works,
    was reported nowhere. The scenario: an ``AttributeError`` from every PMC
    tier — the shape a ``JATSArticle`` API change takes — with Unpaywall
    healthy. Every article in a corpus silently drops from structured JATS to a
    bare ``pdf_url``, and the library reports success.
    """

    @staticmethod
    def _unpaywall_ok() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"best_oa_location": {"url_for_pdf": "https://e/a.pdf"}}
        return resp

    def _run(
        self, service: FullTextService, exc: BaseException, caplog: pytest.LogCaptureFixture
    ) -> object:
        """Europe PMC's search raises *exc*; Unpaywall then succeeds."""
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                side_effect=[exc, _idconv_miss(), self._unpaywall_ok()],
            ),
        ):
            return service.fetch_fulltext(doi="10.1/test")

    def test_an_attribute_error_is_reported_even_though_a_later_tier_succeeds(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The issue verbatim."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        result = self._run(service, AttributeError("no attribute 'has_body'"), caplog)

        assert result.source == "unpaywall"  # the run still "succeeds"
        assert "AttributeError" in caplog.text
        assert "defect" in caplog.text

    def test_a_type_error_is_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, TypeError("str expected"), caplog)
        assert "TypeError" in caplog.text

    def test_a_network_error_is_not_reported_as_a_defect(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The control. An unreachable host is not a bmlib bug, and #67's
        exhaustion report already covers it."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, OSError("network is down"), caplog)
        assert "defect" not in caplog.text

    def test_a_malformed_json_body_is_not_reported_as_a_defect(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``json.JSONDecodeError`` **is** a ``ValueError``.

        This is why ``ValueError`` can never be a member of ``_BUG_TYPES``:
        every ``resp.json()`` on a malformed body raises one, and they are
        ordinary remote-data failures.
        """
        import json

        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, json.JSONDecodeError("bad", "doc", 0), caplog)
        assert "defect" not in caplog.text

    def test_malformed_xml_is_not_reported_as_a_defect(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``ET.ParseError`` **is** a ``SyntaxError``.

        The companion exclusion, and the less obvious of the two: "a
        SyntaxError is always a bug" is intuitive and exactly backwards here.
        """
        import xml.etree.ElementTree as ET

        service = FullTextService(email="test@example.com", convert_pdfs=False)
        self._run(service, ET.ParseError("mismatched tag"), caplog)
        assert "defect" not in caplog.text

    def test_two_different_defect_types_are_both_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Per type, not per service: the second must not hide behind the first."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        with (
            caplog.at_level("WARNING"),
            patch.object(
                service,
                "_http_get",
                side_effect=[
                    AttributeError("first"),
                    _idconv_miss(),
                    TypeError("second"),
                ],
            ),
        ):
            service.fetch_fulltext(doi="10.1/test")

        assert "AttributeError" in caplog.text
        assert "TypeError" in caplog.text
        assert caplog.text.count("which bmlib does not raise deliberately") == 2

    def test_the_same_defect_type_is_reported_once_per_service(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A defect that hits every article must not produce a line per article."""
        service = FullTextService(email="test@example.com", convert_pdfs=False)
        for _ in range(3):
            with (
                caplog.at_level("WARNING"),
                patch.object(service, "_http_get", side_effect=AttributeError("boom")),
            ):
                service.fetch_fulltext(doi="10.1/test")

        assert caplog.text.count("which bmlib does not raise deliberately") == 1

    def test_a_bare_tier_failures_record_still_works(self) -> None:
        """``on_bug`` defaults to ``None``: existing direct construction is safe."""
        failures = _TierFailures()
        failures.record(TypeError("boom"))
        assert failures.faults == ["TypeError"]


class TestAFailedPDFDownloadIsReported:
    """Issue #68 — three distinct outcomes, all swallowed at DEBUG.

    A non-200 for a URL some tier just declared a free PDF, a magic-byte
    rejection, and any exception at all. With ``convert_pdfs=True`` the caller
    asked for text and got none, and a full disk across a 10,000-paper run
    looked exactly like 10,000 publishers 404ing.

    The three stay distinguishable in the message: reporting a read-only
    directory as "PDF validation failed" is the mistake ``_save_pdf_to_cache``
    already avoids between a failed write and a failed validation.
    """

    @staticmethod
    def _service(tmp_path: Path) -> FullTextService:
        return FullTextService(
            email="test@example.com",
            cache=FullTextCache(cache_dir=tmp_path),
            convert_pdfs=False,
        )

    def _fetch(
        self,
        service: FullTextService,
        response_or_exc: object,
        caplog: pytest.LogCaptureFixture,
    ) -> FullTextResult:
        search = MagicMock()
        search.status_code = 200
        search.json.return_value = {
            "resultList": {
                "result": [
                    {
                        "inEPMC": "N",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "documentStyle": "pdf",
                                    "availabilityCode": "OA",
                                    "url": "https://e/a.pdf",
                                }
                            ]
                        },
                    }
                ]
            }
        }
        with (
            caplog.at_level("DEBUG"),
            patch.object(
                service, "_http_get", side_effect=[search, _idconv_miss(), response_or_exc]
            ),
        ):
            return service.fetch_fulltext(doi="10.1/test", identifier="10.1/test")

    def test_a_404_is_reported_as_an_http_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Asserted on text unique to the WARNING, not merely on ``"404"``.

        The helper captures at DEBUG, and the pre-existing
        ``logger.debug("PDF download failed (%s) for %s", "http-404", ...)``
        line already contains "404" — so the bare substring stayed green with
        the whole ``_warn_once`` call deleted from
        ``_report_pdf_download_failure``. "Could not download" appears in no
        other line, and only the WARNING renders the status as ``HTTP 404``
        rather than as the key fragment ``http-404``.
        """
        resp = MagicMock()
        resp.status_code = 404
        result = self._fetch(self._service(tmp_path), resp, caplog)

        assert result.pdf_url == "https://e/a.pdf"
        assert result.file_path is None
        assert "Could not download" in caplog.text
        assert "HTTP 404" in caplog.text

    def test_a_landing_page_is_reported_as_not_a_pdf(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """HTTP 200 whose body is HTML — the Unpaywall failure mode."""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<!DOCTYPE html><html>not a pdf</html>"
        self._fetch(self._service(tmp_path), resp, caplog)

        assert "not a PDF" in caplog.text
        assert "404" not in caplog.text

    def test_a_network_failure_is_reported_as_an_exception(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Asserted on WARNING-unique text for the same reason as the 404 case.

        ``"OSError"`` alone is satisfied by the ``exc_info=True`` traceback on
        the DEBUG line below the warning. This test is backstopped by
        ``test_an_exception_is_warned_once_per_source_and_type``, so it was a
        redundancy rather than a hole — tightened anyway, so no test in this
        class asserts on text a different log line also emits.
        """
        self._fetch(self._service(tmp_path), OSError("no route to host"), caplog)
        assert "Could not download" in caplog.text
        assert "Further OSError failures will not be repeated" in caplog.text

    def test_an_exception_is_warned_once_per_source_and_type(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A lost network fails every article, so this can never be per-article
        — the one cause whose cadence needed no measurement.

        Asserted on text unique to the WARNING rather than
        ``"no route to host"``: the DEBUG line right below the warning logs
        with ``exc_info=True``, and the traceback text repeats
        ``"no route to host"`` on every one of the three iterations, so that
        substring's count is never 1 regardless of suppression.
        """
        service = self._service(tmp_path)
        with caplog.at_level("WARNING"):
            for _ in range(3):
                self._fetch(service, OSError("no route to host"), caplog)
        assert caplog.text.count("Further OSError failures will not be repeated") == 1

    def test_a_successful_download_reports_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The control: a working download must stay quiet."""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"%PDF-1.7\n" + b"x" * 100
        result = self._fetch(self._service(tmp_path), resp, caplog)

        assert result.file_path is not None
        assert "not a PDF" not in caplog.text
        assert "Could not download" not in caplog.text

    def test_a_failed_cache_write_is_not_reported_as_a_bad_pdf(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The distinction the signature change exists for.

        ``_save_pdf_to_cache`` returned ``None`` for both a failed write and a
        failed validation, so reporting on ``None`` alone would blame a
        read-only directory on the publisher's bytes.
        """
        service = self._service(tmp_path)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"%PDF-1.7\n" + b"x" * 100
        with patch.object(service.cache, "save_pdf", side_effect=OSError("read-only file system")):
            self._fetch(service, resp, caplog)

        assert "nothing is being cached" in caplog.text
        assert "not a PDF" not in caplog.text

    def test_two_different_causes_on_the_same_source_are_both_reported(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Per (origin, cause), not per origin.

        The exact regression that bit this plan's Task 4: a test that could
        not fail if the key collapsed to just the origin, since a single
        failing case would still pass with a fixed or origin-only key. Driven
        through two distinct causes — an HTTP failure, then a landing page —
        from the same tier, and both messages must appear.
        """
        service = self._service(tmp_path)
        not_found = MagicMock()
        not_found.status_code = 404
        landing_page = MagicMock()
        landing_page.status_code = 200
        landing_page.content = b"<!DOCTYPE html><html>not a pdf</html>"

        self._fetch(service, not_found, caplog)
        self._fetch(service, landing_page, caplog)

        assert "HTTP 404" in caplog.text
        assert "not a PDF" in caplog.text


class TestTheDownloadFailureKeyspaceIsBounded:
    """The one-shot is only one-shot if its keyspace cannot grow with the corpus.

    ``_report_pdf_download_failure`` originally keyed on ``result.source``.
    That is a constant for Tier 1d (``"europepmc_pdf"``) and Tier 2
    (``"unpaywall"``), but Tier 0 sets it from a fetcher-supplied
    :class:`FullTextSourceEntry`, and OpenAlex derives that from the
    location's **venue display name** — one distinct, remote-data-derived
    string per journal or repository.

    So a bulk sync over an OpenAlex-fed corpus warned once *per article*, each
    line claiming the report was one-shot, at the site with the worst measured
    failure rate: Tier 0's PDF locations are the same arbitrary-repository
    population as Unpaywall's, measured at 64.3% failure with 14 of 28 being
    landing pages. That is precisely the drowned bulk log the pre-registered
    5% rule and the live sampler existed to prevent.

    The fix is a bounded ``origin`` written out at each of the three call
    sites, so the key's value set is an enumeration rather than a function of
    the data. The venue still appears in the *message*, so the first report
    loses nothing.
    """

    @staticmethod
    def _service(tmp_path: Path) -> FullTextService:
        return FullTextService(
            email="test@example.com",
            cache=FullTextCache(cache_dir=tmp_path),
            convert_pdfs=False,
        )

    @staticmethod
    def _fetch_from(service: FullTextService, source: str, response: object) -> FullTextResult:
        """Drive Tier 0 with one fetcher-supplied PDF entry named *source*."""
        entry = FullTextSourceEntry(url=f"https://ex/{source}.pdf", format="pdf", source=source)
        with patch.object(service, "_http_get", side_effect=[response]):
            return service.fetch_fulltext(fulltext_sources=[entry], identifier=f"10.1/{source}")

    def test_two_venues_failing_the_same_way_are_reported_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two OpenAlex-shaped venue names, one identical HTTP failure.

        Keyed on ``result.source`` this warns twice — and, over a real
        corpus, once per article.
        """
        service = self._service(tmp_path)
        not_found = MagicMock()
        not_found.status_code = 404

        with caplog.at_level("WARNING"):
            first = self._fetch_from(service, "The Lancet", not_found)
            second = self._fetch_from(service, "Zenodo", not_found)

        # Both articles still come back with their URL as a fallback.
        assert first.pdf_url == "https://ex/The Lancet.pdf"
        assert second.pdf_url == "https://ex/Zenodo.pdf"
        assert caplog.text.count("Could not download") == 1

    def test_the_first_report_still_names_the_specific_venue(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Bounding the *key* must not cost the reader the detail.

        The guard against "fix" it by dropping the source from the message
        too, which would leave an operator with no idea which repository
        failed.
        """
        service = self._service(tmp_path)
        not_found = MagicMock()
        not_found.status_code = 404

        with caplog.at_level("WARNING"):
            self._fetch_from(service, "The Lancet", not_found)

        assert "The Lancet" in caplog.text

    def test_two_venues_raising_the_same_exception_are_reported_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The exception path builds its own key and had the same defect."""
        service = self._service(tmp_path)

        with caplog.at_level("WARNING"):
            self._fetch_from(service, "The Lancet", OSError("no route to host"))
            self._fetch_from(service, "Zenodo", OSError("no route to host"))

        assert caplog.text.count("Could not download") == 1

    def test_a_different_tier_is_still_reported_separately(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The negative control, and a deliberate property: bounding the key
        must not collapse it to the cause alone.

        Unpaywall's landing-page noise suppressing a Europe PMC report is the
        outcome the per-origin half of the key exists to prevent, so a Tier 0
        failure must not silence the identical failure from Tier 2.
        """
        service = self._service(tmp_path)
        not_found = MagicMock()
        not_found.status_code = 404
        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {"resultList": {"result": []}}
        unpaywall = MagicMock()
        unpaywall.status_code = 200
        unpaywall.json.return_value = {"best_oa_location": {"url_for_pdf": "https://u/a.pdf"}}

        with caplog.at_level("WARNING"):
            self._fetch_from(service, "The Lancet", not_found)
            with patch.object(
                service,
                "_http_get",
                side_effect=[empty_search, _idconv_miss(), unpaywall, not_found],
            ):
                result = service.fetch_fulltext(doi="10.1/unpaywall", identifier="10.1/unpaywall")

        # The Unpaywall tier really did reach its download, rather than the
        # chain ending somewhere earlier and the count passing by accident.
        assert result.source == "unpaywall"
        assert caplog.text.count("Could not download") == 2
