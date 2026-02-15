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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmlib.fulltext.cache import FullTextCache
from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.fulltext.service import FullTextError, FullTextService, _sanitize_identifier

FIXTURES = Path(__file__).parent / "fixtures"


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
        mock_unpaywall_404 = MagicMock()
        mock_unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        # PMC ID given: PMC 404 -> skip discovery -> Unpaywall 404 -> DOI fallback
        with patch.object(service, "_http_get", side_effect=[mock_404, mock_unpaywall_404]):
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
            "resultList": {
                "result": [{"pmcid": "PMC999", "inEPMC": "Y", "doi": "10.1/test"}]
            }
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
            "resultList": {
                "result": [{"pmcid": "PMC888", "inEPMC": "Y"}]
            }
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
            "resultList": {
                "result": [{"pmcid": None, "inEPMC": "N", "doi": "10.1/test"}]
            }
        }
        mock_unpaywall_404 = MagicMock()
        mock_unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        with patch.object(
            service, "_http_get", side_effect=[mock_search, mock_unpaywall_404],
        ):
            result = service.fetch_fulltext(pmc_id=None, doi="10.1/test", pmid="")

        assert result.source == "doi"
        assert result.web_url == "https://doi.org/10.1/test"


class TestFetchUnpaywall:
    def test_success(self):
        mock_pmc_404 = MagicMock()
        mock_pmc_404.status_code = 404

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
        with patch.object(service, "_http_get", side_effect=[mock_pmc_404, mock_unpaywall]):
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
            service, "_http_get", side_effect=[mock_search_empty, mock_unpaywall_404],
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
            FullTextSourceEntry(url="https://medrxiv.org/paper.pdf", format="pdf", source="medrxiv"),
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
            FullTextSourceEntry(url="https://biorxiv.org/paper.pdf", format="pdf", source="biorxiv"),
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
                fulltext_sources=sources, pmc_id="PMC123",
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
            FullTextSourceEntry(url="https://pmc.ncbi.nlm.nih.gov/PMC123/", format="html", source="pmc"),
        ]

        service = FullTextService(email="test@example.com")
        result = service.fetch_fulltext(fulltext_sources=sources)

        assert result.source == "pmc"
        assert result.web_url == "https://pmc.ncbi.nlm.nih.gov/PMC123/"


class TestFullTextSourceEntry:
    def test_to_dict_and_from_dict(self):
        entry = FullTextSourceEntry(
            url="https://example.com/paper.pdf", format="pdf",
            source="biorxiv", open_access=True, version="preprint",
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
        assert _sanitize_identifier("10.1234/test.paper-1") == "10.1234_test.paper-1"

    def test_slashes_replaced(self):
        result = _sanitize_identifier("10.1101/2024.01.15.123456")
        assert "/" not in result

    def test_safe_chars_preserved(self):
        result = _sanitize_identifier("simple_name-1.0")
        assert result == "simple_name-1.0"


class TestCacheIntegration:
    """Tests for FullTextCache integration in FullTextService."""

    PDF_MAGIC = b"%PDF-1.4 fake content for testing"

    def test_cached_html_returned_without_network(self, tmp_path):
        """If HTML is in the disk cache, return it immediately."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<h1>Cached</h1>", "10.1234_test")

        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get") as mock_get:
            result = service.fetch_fulltext(
                doi="10.1234/test", identifier="10.1234/test",
            )
            mock_get.assert_not_called()

        assert result.source == "cached"
        assert result.html == "<h1>Cached</h1>"

    def test_cached_pdf_returned_without_network(self, tmp_path):
        """If PDF is in the disk cache, return file_path immediately."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf(self.PDF_MAGIC, "10.1234_test")

        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get") as mock_get:
            result = service.fetch_fulltext(
                doi="10.1234/test", identifier="10.1234/test",
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
                pmc_id="PMC123", identifier="10.1234/test",
            )

        assert result.source == "europepmc"
        cached_html = cache.get_html("10.1234_test")
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
            service, "_http_get",
            side_effect=[mock_search_empty, mock_unpaywall, mock_pdf],
        ):
            result = service.fetch_fulltext(
                doi="10.1234/test", identifier="10.1234/test",
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
            service, "_http_get",
            side_effect=[mock_search_empty, mock_unpaywall, mock_pdf],
        ):
            result = service.fetch_fulltext(
                doi="10.1234/test", identifier="10.1234/test",
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
                url="https://medrxiv.org/jats.xml", format="xml", source="medrxiv",
            ),
        ]

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", return_value=mock_response):
            result = service.fetch_fulltext(
                fulltext_sources=sources, identifier="10.1234/test",
            )

        assert result.source == "medrxiv"
        assert cache.get_html("10.1234_test") is not None

    def test_known_source_pdf_downloaded_and_cached(self, tmp_path):
        """PDF from known sources is downloaded and cached."""
        mock_pdf = MagicMock()
        mock_pdf.status_code = 200
        mock_pdf.content = self.PDF_MAGIC

        sources = [
            FullTextSourceEntry(
                url="https://medrxiv.org/paper.pdf", format="pdf", source="medrxiv",
            ),
        ]

        cache = FullTextCache(cache_dir=tmp_path)
        service = FullTextService(email="test@example.com", cache=cache)
        with patch.object(service, "_http_get", return_value=mock_pdf):
            result = service.fetch_fulltext(
                fulltext_sources=sources, identifier="10.1234/test",
            )

        assert result.source == "medrxiv"
        assert result.file_path is not None
        assert Path(result.file_path).exists()
