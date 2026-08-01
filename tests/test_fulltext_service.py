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
from bmlib.fulltext.pdf_converter import ConversionResult
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
        # Search for PDF render URL (returns no free PDF)
        mock_search_no_pdf = MagicMock()
        mock_search_no_pdf.status_code = 200
        mock_search_no_pdf.json.return_value = {
            "resultList": {"result": [{"pmcid": "PMC123", "inEPMC": "Y"}]}
        }
        mock_unpaywall_404 = MagicMock()
        mock_unpaywall_404.status_code = 404

        service = FullTextService(email="test@example.com")
        # PMC XML 404 -> search (no PDF) -> Unpaywall 404 -> DOI fallback
        with patch.object(
            service,
            "_http_get",
            side_effect=[mock_404, mock_search_no_pdf, mock_unpaywall_404],
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
            side_effect=[mock_search, mock_unpaywall_404],
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
            side_effect=[mock_pmc_404, mock_search_no_pdf, mock_unpaywall],
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
            side_effect=[mock_search_empty, mock_unpaywall_404],
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
            side_effect=[mock_search_empty, mock_unpaywall, mock_pdf],
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
            side_effect=[mock_search_empty, mock_unpaywall, mock_pdf],
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
        with patch.object(service, "_http_get", side_effect=[mock_xml, mock_search, mock_search]):
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
        with patch.object(service, "_http_get", side_effect=[mock_xml, mock_search, mock_search]):
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
        with patch.object(service, "_http_get", side_effect=[mock_xml, mock_search, mock_search]):
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
            service, "_http_get", side_effect=[self._bodyless(), self._search(), self._search()]
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
            side_effect=[self._search(pmcid="PMC999"), self._bodyless(), unpaywall],
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
            assert service._resolve_pmc_id_via_idconv(doi="10.1/test") == "PMC7614751"

    def test_the_pmid_is_preferred_when_both_are_known(self):
        """A PMID is an exact key; a DOI is text whose formatting is what missed."""
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            service._resolve_pmc_id_via_idconv(doi="10.1/test", pmid="12345")

        assert mock_get.call_args.kwargs["params"]["ids"] == "12345"

    def test_the_doi_is_used_when_there_is_no_pmid(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            service._resolve_pmc_id_via_idconv(doi="10.1/test")

        assert mock_get.call_args.kwargs["params"]["ids"] == "10.1/test"

    def test_no_identifier_makes_no_request(self):
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get") as mock_get:
            assert service._resolve_pmc_id_via_idconv() is None
            mock_get.assert_not_called()

    def test_an_error_record_resolves_to_nothing(self):
        """``status: error`` is how the converter reports an id it cannot map."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(status="error", errmsg="invalid article id")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_record_no_longer_live_resolves_to_nothing(self):
        """``live: "false"`` means PMC no longer serves it — the fetch would fail."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(pmcid="PMC123", live="false")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_malformed_pmcid_is_refused(self):
        """It would otherwise be interpolated into a URL path unchecked."""
        service = FullTextService(email="test@example.com")
        reply = self._reply(pmcid="../../etc/passwd")
        with patch.object(service, "_http_get", return_value=reply):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_an_empty_record_list_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok", "records": []}
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_failed_request_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 500
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_a_transport_failure_is_not_raised(self):
        """It is called where a free-PDF URL is already in hand.

        Letting the exception out would leave the enclosing ``except`` to
        swallow it and skip the rest of the block — trading a working PDF tier
        for a failed converter lookup.
        """
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", side_effect=RuntimeError("connection reset")):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_unparseable_json_resolves_to_nothing(self):
        service = FullTextService(email="test@example.com")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch.object(service, "_http_get", return_value=resp):
            assert service._resolve_pmc_id_via_idconv(pmid="99") is None

    def test_the_api_key_is_sent_only_when_configured(self):
        without = FullTextService(email="test@example.com")
        with patch.object(without, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            without._resolve_pmc_id_via_idconv(pmid="99")
        assert "api_key" not in mock_get.call_args.kwargs["params"]

        with_key = FullTextService(email="test@example.com", ncbi_api_key="secret")
        with patch.object(
            with_key, "_http_get", return_value=self._reply(pmcid="PMC1")
        ) as mock_get:
            with_key._resolve_pmc_id_via_idconv(pmid="99")
        assert mock_get.call_args.kwargs["params"]["api_key"] == "secret"

    def test_the_caller_is_identified_to_ncbi(self):
        """NCBI asks for tool and email on every request."""
        service = FullTextService(email="test@example.com")
        with patch.object(service, "_http_get", return_value=self._reply(pmcid="PMC1")) as mock_get:
            service._resolve_pmc_id_via_idconv(pmid="99")

        params = mock_get.call_args.kwargs["params"]
        assert params["tool"] == "bmlib"
        assert params["email"] == "test@example.com"
