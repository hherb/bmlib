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

"""Full-text retrieval service with multi-tier fallback chain.

Tier 1a: Europe PMC XML -> JATS parser -> HTML
Tier 1b: Discover PMC ID via search, then Europe PMC XML
Tier 1c: Europe PMC PDF render URL (when XML unavailable but free PDF exists)
Tier 2:  Unpaywall -> open-access PDF URL
Tier 3:  DOI resolution -> publisher website URL
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

import httpx

# The sanitizer's canonical implementation lives in bmlib.fulltext.cache so
# the cache can apply the same scheme as a defense in depth for direct callers.
from bmlib.fulltext.cache import FullTextCache
from bmlib.fulltext.cache import sanitize_identifier as _sanitize_identifier
from bmlib.fulltext.jats_parser import JATSParser
from bmlib.fulltext.models import FullTextResult, FullTextSourceEntry

# Imported eagerly — pdf_converter loads its PyMuPDF backend lazily, so this
# costs nothing when the optional ``bmlib[pdf]`` extra is absent.
from bmlib.fulltext.pdf_converter import get_converter, render_html

logger = logging.getLogger(__name__)

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
DOI_BASE = "https://doi.org"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"
TIMEOUT = 30.0


class FullTextError(Exception):
    """Error during full-text retrieval."""


def _extract_free_pdf_url(result: dict[str, object]) -> str | None:
    """Extract free PDF URL from Europe PMC fullTextUrlList.

    The Europe PMC search API includes ``fullTextUrlList`` with entries
    for free PDFs (``?pdf=render`` URLs) even when JATS XML is unavailable.
    """
    url_list = result.get("fullTextUrlList")
    if not isinstance(url_list, dict):
        return None
    for entry in url_list.get("fullTextUrl", []):
        if (
            isinstance(entry, dict)
            and entry.get("documentStyle") == "pdf"
            and entry.get("availability") == "Free"
        ):
            url = entry.get("url")
            if isinstance(url, str):
                return url
    return None


class FullTextService:
    """Retrieves full text from multiple sources with fallback."""

    def __init__(
        self,
        email: str,
        timeout: float = TIMEOUT,
        cache: FullTextCache | None = None,
        convert_pdfs: bool = True,
    ) -> None:
        """Initialise the service.

        Args:
            email: Contact address sent to Unpaywall, as its API requires.
            timeout: Per-request timeout in seconds.
            cache: Disk cache to use. A default one is created when omitted.
            convert_pdfs: Whether to extract text from a retrieved PDF into
                :attr:`FullTextResult.html`, so a PDF-only article can still
                be read inline. Requires the ``bmlib[pdf]`` extra; without it
                the result simply carries no HTML. The PDF's URL and path are
                reported either way, since extracted text loses figures and
                layout.
        """
        self.email = email
        self.timeout = timeout
        self.cache = cache if cache is not None else FullTextCache()
        self.convert_pdfs = convert_pdfs

    def _http_get(self, url: str, **kwargs: object) -> httpx.Response:
        """HTTP GET with timeout. Separated for testability."""
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            return client.get(url, **kwargs)

    def fetch_fulltext(
        self,
        *,
        fulltext_sources: list[FullTextSourceEntry] | None = None,
        pmc_id: str | None = None,
        doi: str | None = None,
        pmid: str = "",
        identifier: str | None = None,
    ) -> FullTextResult:
        """Fetch full text using known sources + 3-tier fallback chain.

        Args:
            fulltext_sources: Known source URLs from the fetcher.
            pmc_id: PubMed Central ID if known.
            doi: Digital Object Identifier.
            pmid: PubMed ID.
            identifier: Cache key (typically DOI). When provided, enables
                disk caching of retrieved content via :class:`FullTextCache`.

        Tries:
          Cache: check disk cache for HTML/PDF (if identifier given)
          0.  Known sources from fetcher (JATS XML > PDF > HTML)
          1a. Europe PMC XML (known PMC ID)
          1b. Discover PMC ID via Europe PMC search, then fetch XML
          1c. Europe PMC PDF render URL (free PDF when XML unavailable)
          2.  Unpaywall PDF URL
          3.  DOI / PubMed URL fallback
        """
        cache_id = _sanitize_identifier(identifier) if identifier else None

        # Cache check — return immediately if content already on disk
        if cache_id and self.cache:
            cached = self._check_cache(cache_id)
            if cached is not None:
                return cached

        # A body-less JATS rendering picked up along the way. Held back as a
        # last resort rather than returned, since it carries only the
        # abstract while a later tier may still find the whole article.
        abstract_only: FullTextResult | None = None

        # Tier 0: Try fetcher-provided sources
        if fulltext_sources:
            result, abstract_only = self._try_known_sources(fulltext_sources, cache_id=cache_id)
            if result is not None:
                return result

        # Tier 1a: Europe PMC with known PMC ID
        xml_failed = False
        if pmc_id:
            try:
                html, has_body = self._fetch_europepmc(pmc_id)
                if has_body:
                    logger.info("Full text retrieved from Europe PMC for %s", pmc_id)
                    self._cache_html(html, cache_id)
                    return FullTextResult(source="europepmc", html=html)
                logger.info("Europe PMC XML for %s has no body — looking further", pmc_id)
                if abstract_only is None:
                    abstract_only = FullTextResult(source="europepmc", html=html)
                # Treated as a failure so the free-PDF lookup below still runs.
                xml_failed = True
            except Exception:
                logger.debug("Europe PMC failed for %s", pmc_id, exc_info=True)
                xml_failed = True

        # Tier 1b: Discover PMC ID via Europe PMC search, then fetch XML
        pdf_render_url: str | None = None
        if not pmc_id and (doi or pmid):
            try:
                discovered_pmc_id, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi, pmid=pmid
                )
                if discovered_pmc_id:
                    html, has_body = self._fetch_europepmc(discovered_pmc_id)
                    if has_body:
                        logger.info(
                            "Full text retrieved from Europe PMC via discovered %s",
                            discovered_pmc_id,
                        )
                        self._cache_html(html, cache_id)
                        return FullTextResult(source="europepmc", html=html)
                    logger.info(
                        "Europe PMC XML for discovered %s has no body — looking further",
                        discovered_pmc_id,
                    )
                    if abstract_only is None:
                        abstract_only = FullTextResult(source="europepmc", html=html)
            except Exception:
                logger.debug(
                    "Europe PMC discovery failed for doi=%s pmid=%s",
                    doi,
                    pmid,
                    exc_info=True,
                )

        # When XML failed with a known PMC ID, search for PDF render URL
        if xml_failed and not pdf_render_url and (doi or pmid):
            try:
                _, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi,
                    pmid=pmid,
                )
            except Exception:
                logger.debug("PDF URL resolution failed", exc_info=True)

        # Tier 1c: Europe PMC PDF render (when XML unavailable but free PDF exists)
        if pdf_render_url:
            logger.info("PDF available from Europe PMC render: %s", pdf_render_url)
            result = FullTextResult(source="europepmc_pdf", pdf_url=pdf_render_url)
            self._download_and_cache_pdf(pdf_render_url, cache_id, result)
            return result

        # Tier 2: Unpaywall
        if doi:
            try:
                pdf_url = self._fetch_unpaywall(doi)
                logger.info("PDF URL found via Unpaywall for DOI %s", doi)
                result = FullTextResult(source="unpaywall", pdf_url=pdf_url)
                self._download_and_cache_pdf(pdf_url, cache_id, result)
                return result
            except Exception:
                logger.debug("Unpaywall failed for DOI %s", doi, exc_info=True)

        # Tier 3: DOI / PubMed fallback. When a body-less JATS was seen
        # earlier, keep its abstract and hang the link off it — the reader
        # gets both, rather than a bare link.
        web_url = None
        if doi:
            logger.info("Falling back to DOI URL for %s", doi)
            web_url = f"{DOI_BASE}/{doi}"
        elif pmid:
            logger.info("Falling back to PubMed URL for PMID %s", pmid)
            web_url = f"{PUBMED_BASE}/{pmid}/"

        if abstract_only is not None:
            abstract_only.web_url = web_url
            logger.info("No full text found — returning the abstract-only rendering")
            return abstract_only

        if web_url:
            return FullTextResult(source="doi" if doi else "pubmed", web_url=web_url)

        raise FullTextError("No identifiers provided")

    def _try_known_sources(
        self,
        sources: list[FullTextSourceEntry],
        *,
        cache_id: str | None = None,
    ) -> tuple[FullTextResult | None, FullTextResult | None]:
        """Try fetcher-provided fulltext sources in priority order.

        Priority: xml (JATS) > pdf > html.

        Returns:
            A tuple of ``(result, abstract_only)``. ``result`` is the full
            text when one was found, else ``None``. ``abstract_only`` holds a
            body-less JATS rendering if one was seen — worth showing when
            every other tier comes up empty, but never worth stopping on,
            since the PDF behind it usually has the whole article.
        """
        priority = {"xml": 0, "pdf": 1, "html": 2}
        sorted_sources = sorted(
            sources,
            key=lambda s: priority.get(s.format, 99),
        )

        abstract_only: FullTextResult | None = None
        for entry in sorted_sources:
            try:
                if entry.format == "xml":
                    html, has_body = self._fetch_jats_xml(entry.url)
                    if not has_body:
                        # Not cached: a later fetch may find a populated
                        # document, and caching this would make the abstract
                        # permanent.
                        logger.info(
                            "JATS XML from %s has no body — keeping it only as a "
                            "fallback and looking for the full article",
                            entry.source,
                        )
                        if abstract_only is None:
                            abstract_only = FullTextResult(source=entry.source, html=html)
                        continue
                    logger.info("Full text from JATS XML (%s)", entry.source)
                    self._cache_html(html, cache_id)
                    return FullTextResult(source=entry.source, html=html), abstract_only
                elif entry.format == "pdf":
                    logger.info("PDF available from %s", entry.source)
                    result = FullTextResult(source=entry.source, pdf_url=entry.url)
                    self._download_and_cache_pdf(entry.url, cache_id, result)
                    return result, abstract_only
                elif entry.format == "html":
                    logger.info("HTML source from %s", entry.source)
                    return FullTextResult(source=entry.source, web_url=entry.url), abstract_only
            except Exception:
                logger.debug(
                    "Known source %s (%s) failed",
                    entry.source,
                    entry.url,
                    exc_info=True,
                )
                continue

        return None, abstract_only

    # --- Cache helpers --------------------------------------------------------

    def _check_cache(self, cache_id: str) -> FullTextResult | None:
        """Return a cached FullTextResult if available on disk."""
        html = self.cache.get_html(cache_id)
        if html:
            logger.info("Cache hit (HTML) for %s", cache_id)
            return FullTextResult(source="cached", html=html)
        pdf_path = self.cache.get_pdf(cache_id)
        if pdf_path:
            logger.info("Cache hit (PDF) for %s", cache_id)
            return FullTextResult(source="cached", file_path=pdf_path)
        return None

    def _cache_html(self, html: str, cache_id: str | None) -> None:
        """Save HTML to disk cache if caching is enabled."""
        if cache_id and self.cache:
            try:
                self.cache.save_html(html, cache_id)
            except Exception:
                logger.debug("Failed to cache HTML for %s", cache_id, exc_info=True)

    def _download_and_cache_pdf(
        self,
        pdf_url: str,
        cache_id: str | None,
        result: FullTextResult,
    ) -> None:
        """Download a PDF and save it to the disk cache.

        On success, sets ``result.file_path`` to the cached file.
        On failure (network error or invalid PDF), leaves result unchanged
        so the caller can still use ``result.pdf_url`` as a fallback.
        """
        if not cache_id or not self.cache:
            return
        try:
            resp = self._http_get(pdf_url)
            if resp.status_code != 200:
                logger.debug("PDF download HTTP %s for %s", resp.status_code, pdf_url)
                return
            path = self.cache.save_pdf(resp.content, cache_id)
            if path:
                result.file_path = path
                logger.info("PDF cached to %s", path)
                self._attach_pdf_text(path, result)
            else:
                logger.debug("PDF validation failed for %s", pdf_url)
        except Exception:
            logger.debug("PDF download failed for %s", pdf_url, exc_info=True)

    def _attach_pdf_text(self, pdf_path: str, result: FullTextResult) -> None:
        """Extract a cached PDF's text into ``result.html``.

        Best-effort: a missing ``bmlib[pdf]`` extra or an unreadable PDF
        leaves the result untouched, so the caller still has the PDF itself.
        ``result.pdf_url`` and ``result.file_path`` are deliberately left in
        place — extracted text recovers the prose but not figures, tables or
        layout, so the original stays worth offering.
        """
        if not self.convert_pdfs or result.html:
            return
        try:
            conversion = get_converter().convert(Path(pdf_path))
            html = render_html(conversion)
        except ImportError:
            logger.debug("PDF text extraction unavailable — install bmlib[pdf]")
            return
        except Exception:
            logger.debug("PDF text extraction failed for %s", pdf_path, exc_info=True)
            return
        if html:
            result.html = html
            logger.info("Extracted %d chars of text from PDF %s", conversion.char_count, pdf_path)

    # --- Fetch helpers --------------------------------------------------------

    def _fetch_jats_xml(self, url: str) -> tuple[str, bool]:
        """Fetch JATS XML from an arbitrary URL and parse to HTML.

        Returns:
            A tuple of the rendered HTML and whether the document actually
            had a body. A body-less document renders to little more than the
            abstract, so the caller must keep looking for the real full text.
        """
        resp = self._http_get(url, headers={"Accept": "application/xml"})
        if resp.status_code != 200:
            raise FullTextError(f"JATS XML fetch failed: HTTP {resp.status_code}")
        article, html = JATSParser(resp.content).parse_with_html()
        return html, article.has_body

    def _resolve_pmc_id_and_pdf_url(
        self,
        *,
        doi: str | None = None,
        pmid: str = "",
    ) -> tuple[str | None, str | None]:
        """Search Europe PMC to discover a PMC ID and free PDF URL.

        Returns a tuple of (pmc_id, pdf_render_url). Either or both may
        be None. The PDF render URL comes from the ``fullTextUrlList``
        in the search response and provides a free PDF when JATS XML is
        unavailable.
        """
        if doi:
            query = f"DOI:{doi}"
        elif pmid:
            query = f"EXT_ID:{pmid}"
        else:
            return None, None

        url = (
            f"{EUROPE_PMC_BASE}/search"
            f"?query={quote(query, safe=':')}&format=json&resultType=core&pageSize=1"
        )
        resp = self._http_get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return None, None

        data = resp.json()
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None, None

        hit = results[0]
        pmc_id = hit.get("pmcid") if hit.get("inEPMC") == "Y" else None

        # Extract free PDF render URL from fullTextUrlList
        pdf_render_url = _extract_free_pdf_url(hit)

        return pmc_id, pdf_render_url

    def _fetch_europepmc(self, pmc_id: str) -> tuple[str, bool]:
        """Fetch JATS XML from Europe PMC and parse to HTML.

        Returns:
            A tuple of the rendered HTML and whether the document had a body,
            as for :meth:`_fetch_jats_xml`.
        """
        normalized = pmc_id if pmc_id.startswith("PMC") else f"PMC{pmc_id}"
        url = f"{EUROPE_PMC_BASE}/{normalized}/fullTextXML"

        resp = self._http_get(url, headers={"Accept": "application/xml"})
        if resp.status_code == 404:
            raise FullTextError(f"No full text in Europe PMC for {normalized}")
        if resp.status_code != 200:
            raise FullTextError(f"Europe PMC HTTP {resp.status_code}")

        parser = JATSParser(resp.content, known_pmc_id=normalized)
        article, html = parser.parse_with_html()
        return html, article.has_body

    def _fetch_unpaywall(self, doi: str) -> str:
        """Query Unpaywall for open-access PDF URL."""
        encoded_doi = quote(doi, safe="")
        encoded_email = quote(self.email, safe="")
        url = f"{UNPAYWALL_BASE}/{encoded_doi}?email={encoded_email}"

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
