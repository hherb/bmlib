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

"""Full-text retrieval service with 3-tier fallback chain.

Tier 1: Europe PMC XML -> JATS parser -> HTML
Tier 2: Unpaywall -> open-access PDF URL
Tier 3: DOI resolution -> publisher website URL
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from bmlib.fulltext.jats_parser import JATSParser
from bmlib.fulltext.models import FullTextResult

logger = logging.getLogger(__name__)

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
DOI_BASE = "https://doi.org"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"
TIMEOUT = 30.0


class FullTextError(Exception):
    """Error during full-text retrieval."""


class FullTextService:
    """Retrieves full text from multiple sources with fallback."""

    def __init__(self, email: str, timeout: float = TIMEOUT) -> None:
        self.email = email
        self.timeout = timeout

    def _http_get(self, url: str, **kwargs: object) -> httpx.Response:
        """HTTP GET with timeout. Separated for testability."""
        with httpx.Client(timeout=self.timeout) as client:
            return client.get(url, **kwargs)

    def fetch_fulltext(
        self,
        *,
        pmc_id: str | None = None,
        doi: str | None = None,
        pmid: str = "",
    ) -> FullTextResult:
        """Fetch full text using 3-tier fallback chain.

        Tries: Europe PMC XML -> Unpaywall PDF -> DOI resolution.
        Always attempts Europe PMC first, discovering PMC ID if needed.
        """
        # Tier 1a: Europe PMC with known PMC ID
        if pmc_id:
            try:
                html = self._fetch_europepmc(pmc_id)
                logger.info("Full text retrieved from Europe PMC for %s", pmc_id)
                return FullTextResult(source="europepmc", html=html)
            except Exception:
                logger.debug("Europe PMC failed for %s", pmc_id, exc_info=True)

        # Tier 1b: Discover PMC ID via Europe PMC search, then fetch XML
        if not pmc_id and (doi or pmid):
            try:
                discovered_pmc_id = self._resolve_pmc_id(doi=doi, pmid=pmid)
                if discovered_pmc_id:
                    html = self._fetch_europepmc(discovered_pmc_id)
                    logger.info(
                        "Full text retrieved from Europe PMC via discovered %s",
                        discovered_pmc_id,
                    )
                    return FullTextResult(source="europepmc", html=html)
            except Exception:
                logger.debug(
                    "Europe PMC discovery failed for doi=%s pmid=%s",
                    doi, pmid, exc_info=True,
                )

        # Tier 2: Unpaywall
        if doi:
            try:
                pdf_url = self._fetch_unpaywall(doi)
                logger.info("PDF URL found via Unpaywall for DOI %s", doi)
                return FullTextResult(source="unpaywall", pdf_url=pdf_url)
            except Exception:
                logger.debug("Unpaywall failed for DOI %s", doi, exc_info=True)

        # Tier 3: DOI fallback
        if doi:
            logger.info("Falling back to DOI URL for %s", doi)
            return FullTextResult(source="doi", web_url=f"{DOI_BASE}/{doi}")

        # Final fallback: PubMed URL
        if pmid:
            logger.info("Falling back to PubMed URL for PMID %s", pmid)
            return FullTextResult(source="doi", web_url=f"{PUBMED_BASE}/{pmid}/")

        raise FullTextError("No identifiers provided")

    def _resolve_pmc_id(
        self, *, doi: str | None = None, pmid: str = "",
    ) -> str | None:
        """Search Europe PMC to discover a PMC ID for a paper.

        Returns the PMC ID if the paper has full text in Europe PMC,
        or None if not found.
        """
        if doi:
            query = f"DOI:{doi}"
        elif pmid:
            query = f"EXT_ID:{pmid}"
        else:
            return None

        url = (
            f"{EUROPE_PMC_BASE}/search"
            f"?query={quote(query, safe=':')}&format=json&resultType=core&pageSize=1"
        )
        resp = self._http_get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None

        hit = results[0]
        if hit.get("inEPMC") == "Y" and hit.get("pmcid"):
            return hit["pmcid"]

        return None

    def _fetch_europepmc(self, pmc_id: str) -> str:
        """Fetch JATS XML from Europe PMC and parse to HTML."""
        normalized = pmc_id if pmc_id.startswith("PMC") else f"PMC{pmc_id}"
        url = f"{EUROPE_PMC_BASE}/{normalized}/fullTextXML"

        resp = self._http_get(url, headers={"Accept": "application/xml"})
        if resp.status_code == 404:
            raise FullTextError(f"No full text in Europe PMC for {normalized}")
        if resp.status_code != 200:
            raise FullTextError(f"Europe PMC HTTP {resp.status_code}")

        parser = JATSParser(resp.content, known_pmc_id=normalized)
        return parser.to_html()

    def _fetch_unpaywall(self, doi: str) -> str:
        """Query Unpaywall for open-access PDF URL."""
        encoded_doi = quote(doi, safe="")
        url = f"{UNPAYWALL_BASE}/{encoded_doi}?email={self.email}"

        resp = self._http_get(url, headers={"Accept": "application/json"})
        if resp.status_code == 404:
            raise FullTextError(f"DOI not found in Unpaywall: {doi}")
        if resp.status_code != 200:
            raise FullTextError(f"Unpaywall HTTP {resp.status_code}")

        data = resp.json()
        best = data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf") or best.get("url")
        if pdf_url:
            return pdf_url

        for loc in data.get("oa_locations") or []:
            pdf_url = loc.get("url_for_pdf") or loc.get("url")
            if pdf_url:
                return pdf_url

        raise FullTextError(f"No open-access PDF found for DOI {doi}")
