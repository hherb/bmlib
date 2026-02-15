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

from bmlib.fulltext.service import FullTextError, FullTextService

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


class TestFullTextError:
    def test_no_identifiers_message(self):
        err = FullTextError("No identifiers provided")
        assert "No identifiers" in str(err)
