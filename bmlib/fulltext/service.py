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
Tier 1b': Discover PMC ID via NCBI's ID Converter when the search found none
Tier 1c: NCBI PMC efetch for whichever PMC ID was resolved
Tier 1d: Europe PMC PDF render URL (when XML unavailable but free PDF exists)
Tier 2:  Unpaywall -> open-access PDF URL
Tier 3:  DOI resolution -> publisher website URL
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:  # Annotation only; the real import is guarded in __init__.
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
NCBI_IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
EUTILS_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUTILS_TOOL_NAME = "bmlib"
TIMEOUT = 30.0

# A PMC ID reaches a URL path in two fetch helpers, and one of its sources is
# third-party JSON. Validated where it is used rather than where it arrives,
# so caller-supplied, Europe-PMC-supplied and converter-supplied ids are
# covered by one guard. Matched with fullmatch(), never match(): `$` also
# matches before a trailing newline, so an anchored match() would accept
# "PMC123\n" — the same reason _NCT_ID_RE in transparency uses fullmatch.
_PMC_ID_RE = re.compile(r"PMC\d+")


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


def _normalise_pmc_id(pmc_id: str) -> str:
    """Prefix a bare numeric PMC ID and validate the result.

    A PMC ID is interpolated into a URL path by both PMC fetch helpers, and
    reaches them from three places: the caller, Europe PMC's search response
    and NCBI's ID Converter. Only the first is under bmlib's control, so the
    check lives at the point of use and covers all three.

    Args:
        pmc_id: A PMC ID, with or without the ``PMC`` prefix.

    Returns:
        The prefixed, validated ID.

    Raises:
        FullTextError: If the value is not ``PMC`` followed by digits. Every
            tier already catches this and moves on, so a malformed ID costs a
            log line rather than a request.
    """
    normalized = pmc_id if pmc_id.startswith("PMC") else f"PMC{pmc_id}"
    if not _PMC_ID_RE.fullmatch(normalized):
        raise FullTextError(f"Not a usable PMC ID: {pmc_id!r}")
    return normalized


class FullTextService:
    """Retrieves full text from multiple sources with fallback."""

    def __init__(
        self,
        email: str,
        timeout: float = TIMEOUT,
        cache: FullTextCache | None = None,
        convert_pdfs: bool = True,
        ncbi_api_key: str | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            email: Contact address sent to Unpaywall, as its API requires.
            timeout: Per-request timeout in seconds.
            cache: Disk cache to use. A default one is created when omitted.
            convert_pdfs: Whether to extract text from a retrieved PDF into
                :attr:`FullTextResult.html` (marked
                ``content_kind="extracted"``), so a PDF-only article can still
                be read inline. Requires the ``bmlib[pdf]`` extra; without it
                the result simply carries no HTML. The PDF's URL and path are
                reported either way, since extracted text loses figures and
                layout.

                Applies only when the PDF is cached to disk — that is, when
                :meth:`fetch_fulltext` was given an ``identifier`` and a cache
                is configured. Without one there is no file to extract from
                and this setting has no effect.
            ncbi_api_key: Optional NCBI API key, sent with the ID Converter and
                ``efetch`` requests. It does not change this service's pacing —
                bmlib throttles nothing — but it moves those requests into the
                key's 10 requests/second allowance instead of the 3
                requests/second shared by everything on the IP. Declared last
                so positional construction stays stable.

        Raises:
            ImportError: If httpx is not installed, naming the extra that
                supplies it. Raised before anything else happens, so a
                construction that fails leaves no cache directory behind.
        """
        try:
            import httpx
        except ImportError as e:
            raise ImportError(
                "httpx is required for full-text retrieval. "
                "Install with: pip install bmlib[fulltext]"
            ) from e
        self._httpx = httpx

        self.email = email
        self.timeout = timeout
        self.cache = cache if cache is not None else FullTextCache()
        self.convert_pdfs = convert_pdfs
        self.ncbi_api_key = ncbi_api_key
        # Guards the one-off warning in _attach_pdf_text when the bmlib[pdf]
        # extra is missing: worth saying once, not once per article.
        self._pdf_extra_warned = False

    def _http_get(self, url: str, **kwargs: object) -> httpx.Response:
        """HTTP GET with timeout. Separated for testability."""
        with self._httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
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
          1b'. Discover PMC ID via NCBI's ID Converter when the search
               reported none, then fetch XML
          1c. NCBI PMC efetch for whichever PMC ID was resolved
          1d. Europe PMC PDF render URL (free PDF when XML unavailable)
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
                return self._with_abstract_fallback(result, abstract_only)

        # Tier 1a: Europe PMC with known PMC ID
        xml_failed = False
        # Whichever PMC ID we end up holding — the caller's or a resolved one.
        # NCBI's tier below spends it, so it is set before the fetch that may
        # raise, not after.
        resolved_pmc_id: str | None = pmc_id
        if pmc_id:
            try:
                html, has_body = self._fetch_europepmc(pmc_id)
                if has_body:
                    logger.info("Full text retrieved from Europe PMC for %s", pmc_id)
                    self._cache_html(html, cache_id)
                    return FullTextResult(source="europepmc", html=html, content_kind="fulltext")
                logger.info("Europe PMC XML for %s has no body — looking further", pmc_id)
                if abstract_only is None:
                    abstract_only = FullTextResult(
                        source="europepmc", html=html, content_kind="abstract"
                    )
                # Treated as a failure so the free-PDF lookup below still runs.
                xml_failed = True
            except Exception:
                logger.debug("Europe PMC failed for %s", pmc_id, exc_info=True)
                xml_failed = True

        # Tier 1b: Discover PMC ID via Europe PMC search, then fetch XML
        pdf_render_url: str | None = None
        if not pmc_id and (doi or pmid):
            discovered_pmc_id: str | None = None
            try:
                discovered_pmc_id, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi, pmid=pmid
                )
            except Exception:
                logger.debug(
                    "Europe PMC search failed for doi=%s pmid=%s",
                    doi,
                    pmid,
                    exc_info=True,
                )

            # Tier 1b′: the search reports a PMC ID only for what Europe PMC
            # both indexed and holds. NCBI's converter depends on neither, and
            # is asked second because that one search also returned the
            # free-PDF URL Tier 1d needs. It sits outside the search's `except`
            # deliberately: a search that raised is precisely when a second,
            # independent resolver is worth having, and folding this back into
            # that block would skip it there.
            if not discovered_pmc_id:
                discovered_pmc_id = self._resolve_pmc_id_via_idconv(doi=doi, pmid=pmid)

            if discovered_pmc_id:
                resolved_pmc_id = discovered_pmc_id
                try:
                    html, has_body = self._fetch_europepmc(discovered_pmc_id)
                    if has_body:
                        logger.info(
                            "Full text retrieved from Europe PMC via discovered %s",
                            discovered_pmc_id,
                        )
                        self._cache_html(html, cache_id)
                        return FullTextResult(
                            source="europepmc", html=html, content_kind="fulltext"
                        )
                    logger.info(
                        "Europe PMC XML for discovered %s has no body — looking further",
                        discovered_pmc_id,
                    )
                    if abstract_only is None:
                        abstract_only = FullTextResult(
                            source="europepmc", html=html, content_kind="abstract"
                        )
                except Exception:
                    logger.debug(
                        "Europe PMC fetch failed for discovered %s",
                        discovered_pmc_id,
                        exc_info=True,
                    )

        # Tier 1c: NCBI's own copy, for whichever PMC ID we hold. Reaching here
        # means Europe PMC gave no body for it — it serves the corpus its
        # inEPMC flag describes, and NCBI serves PMC itself. Ahead of the PDF
        # tier because structured JATS beats a PDF that needs bmlib[pdf] to
        # read at all.
        if resolved_pmc_id:
            try:
                html, has_body = self._fetch_ncbi_pmc(resolved_pmc_id)
                if has_body:
                    logger.info("Full text retrieved from NCBI PMC for %s", resolved_pmc_id)
                    self._cache_html(html, cache_id)
                    return FullTextResult(source="ncbi_pmc", html=html, content_kind="fulltext")
                logger.info("NCBI PMC XML for %s has no body — looking further", resolved_pmc_id)
                if abstract_only is None:
                    abstract_only = FullTextResult(
                        source="ncbi_pmc", html=html, content_kind="abstract"
                    )
            except Exception:
                logger.debug("NCBI PMC failed for %s", resolved_pmc_id, exc_info=True)

        # When XML failed with a known PMC ID, search for PDF render URL
        if xml_failed and not pdf_render_url and (doi or pmid):
            try:
                _, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi,
                    pmid=pmid,
                )
            except Exception:
                logger.debug("PDF URL resolution failed", exc_info=True)

        # Tier 1d: Europe PMC PDF render (when XML unavailable but free PDF exists)
        if pdf_render_url:
            logger.info("PDF available from Europe PMC render: %s", pdf_render_url)
            result = FullTextResult(source="europepmc_pdf", pdf_url=pdf_render_url)
            self._download_and_cache_pdf(pdf_render_url, cache_id, result)
            return self._with_abstract_fallback(result, abstract_only)

        # Tier 2: Unpaywall
        if doi:
            try:
                pdf_url = self._fetch_unpaywall(doi)
                logger.info("PDF URL found via Unpaywall for DOI %s", doi)
                result = FullTextResult(source="unpaywall", pdf_url=pdf_url)
                self._download_and_cache_pdf(pdf_url, cache_id, result)
                return self._with_abstract_fallback(result, abstract_only)
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
            if web_url:
                abstract_only.web_url = web_url
            logger.warning(
                "No full text found for doi=%s pmid=%s — returning the abstract only", doi, pmid
            )
            return abstract_only

        if web_url:
            return FullTextResult(source="doi" if doi else "pubmed", web_url=web_url)

        raise FullTextError("No identifiers provided")

    def _with_abstract_fallback(
        self,
        result: FullTextResult,
        abstract_only: FullTextResult | None,
    ) -> FullTextResult:
        """Carry a held-back abstract onto a result that has no text of its own.

        A PDF tier counts as a success as soon as it has a URL — the download
        may have failed, or there may have been no cache to extract from.
        Returning that alone would discard an abstract already in hand and
        leave the reader a bare link, which is the outcome the whole fallback
        exists to prevent. The link stays on the result either way.

        Args:
            result: The winning tier's result, modified in place.
            abstract_only: A body-less JATS rendering seen earlier, if any.

        Returns:
            ``result``, with the abstract merged in when it had no text.
        """
        if abstract_only is None or result.html:
            return result
        result.html = abstract_only.html
        result.content_kind = "abstract"
        logger.info("PDF yielded no text — pairing the link with the abstract-only rendering")
        return result

    def _try_known_sources(
        self,
        sources: list[FullTextSourceEntry],
        *,
        cache_id: str | None = None,
    ) -> tuple[FullTextResult | None, FullTextResult | None]:
        """Try fetcher-provided fulltext sources in priority order.

        Priority: xml (JATS) > pdf > html.

        Returns:
            A tuple of ``(result, abstract_only)``. ``result`` is the best
            source that worked — JATS full text, a PDF, or a link — or
            ``None`` when every entry failed; only a ``content_kind`` of
            ``"fulltext"`` means article text was actually retrieved.
            ``abstract_only`` holds a body-less JATS rendering if one was
            seen. It is never worth stopping on, because a publisher that
            serves an abstract-only JATS (medRxiv does) generally serves the
            complete article as a PDF alongside it; the caller merges it back
            in via :meth:`_with_abstract_fallback` if nothing better turns up.
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
                            abstract_only = FullTextResult(
                                source=entry.source, html=html, content_kind="abstract"
                            )
                        continue
                    logger.info("Full text from JATS XML (%s)", entry.source)
                    self._cache_html(html, cache_id)
                    return (
                        FullTextResult(source=entry.source, html=html, content_kind="fulltext"),
                        abstract_only,
                    )
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
        """Return a cached FullTextResult if available on disk.

        Only HTML that came from a JATS ``<body>`` is ever written to the
        cache, so a cached HTML hit is always full text. Text extracted from
        a PDF is not cached — it is re-derived here from the cached PDF, so a
        cache hit carries the same ``html`` and ``content_kind`` as the
        original retrieval instead of silently dropping to a bare file path.
        Re-extraction is local CPU work on a file already on disk; caching the
        output instead would make it indistinguishable from real full text on
        the next hit.
        """
        html = self.cache.get_html(cache_id)
        if html:
            logger.info("Cache hit (HTML) for %s", cache_id)
            return FullTextResult(source="cached", html=html, content_kind="fulltext")
        pdf_path = self.cache.get_pdf(cache_id)
        if pdf_path:
            logger.info("Cache hit (PDF) for %s", cache_id)
            result = FullTextResult(source="cached", file_path=pdf_path)
            self._attach_pdf_text(pdf_path, result)
            return result
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

        On success, sets ``result.file_path`` to the cached file and — when
        ``convert_pdfs`` is on and a backend is available — ``result.html``
        and ``result.content_kind`` from the PDF's extracted text.
        On failure (network error or invalid PDF), leaves result unchanged
        so the caller can still use ``result.pdf_url`` as a fallback.
        """
        if not cache_id or not self.cache:
            if self.convert_pdfs:
                logger.info(
                    "convert_pdfs is on but no identifier was given — a PDF is only "
                    "extracted once cached, so %s is left as a URL",
                    pdf_url,
                )
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

        A no-op when ``convert_pdfs`` is off or ``result.html`` is already
        populated — an earlier tier's text is never overwritten.

        Otherwise best-effort: a missing ``bmlib[pdf]`` extra or an unreadable
        PDF leaves the result untouched, so the caller still has the PDF
        itself. ``result.pdf_url`` and ``result.file_path`` are deliberately
        left in place — extracted text recovers the prose but not figures,
        tables or layout, so the original stays worth offering.

        Every way this can come up empty is logged at WARNING: a scanned PDF
        that yields nothing is invisible otherwise, and a partial extraction
        must not be mistaken for a whole article.
        """
        if not self.convert_pdfs or result.html:
            return
        try:
            converter = get_converter()
        except ImportError as e:
            # Only constructing the backend can raise this, and only for a
            # missing extra — but report what was actually raised rather than
            # asserting the cause, so a broken PyMuPDF install is not
            # misreported as an uninstalled one.
            if not self._pdf_extra_warned:
                logger.warning(
                    "convert_pdfs is enabled but no PDF backend is usable (%s); "
                    "PDFs will be returned as links only. Install bmlib[pdf].",
                    e,
                )
                self._pdf_extra_warned = True
            return

        try:
            # convert() reports backend failures in its result rather than
            # raising; this guards the unexpected (a missing cache file, an
            # unreadable one, a bug in render_html).
            conversion = converter.convert(Path(pdf_path))
            html = render_html(conversion)
        except Exception:
            logger.warning("PDF text extraction failed for %s", pdf_path, exc_info=True)
            return

        if not conversion.success:
            logger.warning(
                "PDF text extraction failed for %s: %s", pdf_path, conversion.error_message
            )
            return
        if not html:
            logger.warning(
                "PDF %s yielded no extractable text over %d page(s) — likely a scan; %s",
                pdf_path,
                conversion.page_count,
                conversion.warnings[:3] or "no warnings reported",
            )
            return
        if not conversion.is_complete:
            logger.warning(
                "PDF %s extracted only %d of %d pages — the attached text is incomplete",
                pdf_path,
                conversion.converted_pages,
                conversion.page_count,
            )

        result.html = html
        result.content_kind = "extracted"
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

    def _ncbi_params(self, **params: str) -> dict[str, str]:
        """Add the identification NCBI asks of every caller.

        ``tool`` and ``email`` identify bmlib; ``api_key`` is sent only when
        configured, and moves the request into the key's allowance rather than
        the 3 requests/second shared by everything on the IP.
        """
        params.update(tool=EUTILS_TOOL_NAME, email=self.email)
        if self.ncbi_api_key:
            params["api_key"] = self.ncbi_api_key
        return params

    def _resolve_pmc_id_via_idconv(
        self,
        *,
        doi: str | None = None,
        pmid: str = "",
    ) -> str | None:
        """Resolve a PMC ID through NCBI's ID Converter.

        The second source for a PMC ID, consulted only when the Europe PMC
        search returned none. Europe PMC reports one only when it both indexed
        the paper and flagged its full text as available there; the converter
        depends on neither.

        Asked by PMID when there is one — an exact numeric key — and by DOI
        otherwise, since a DOI-formatting miss is one of the divergences this
        recovers.

        Returns:
            The PMC ID, or ``None`` if the converter has no live record for the
            identifier, reports an error, answers with something unusable, or
            cannot be reached. It never raises: the caller has a free-PDF URL
            in hand by this point, and an exception would cost it.
        """
        if pmid:
            ids = pmid
        elif doi:
            ids = doi
        else:
            return None

        try:
            resp = self._http_get(
                NCBI_IDCONV_URL,
                params=self._ncbi_params(ids=ids, format="json"),
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug("ID Converter HTTP %s for %s", resp.status_code, ids)
                return None

            records = resp.json().get("records") or []
            if not records:
                return None

            record = records[0]
            if record.get("status") == "error":
                logger.debug("ID Converter has no record for %s: %s", ids, record.get("errmsg"))
                return None
            # Reported as the string "false" for a record PMC no longer serves.
            if str(record.get("live", "true")).lower() == "false":
                logger.debug("ID Converter record for %s is no longer live", ids)
                return None

            pmc_id = record.get("pmcid")
            if not isinstance(pmc_id, str) or not _PMC_ID_RE.fullmatch(pmc_id):
                if pmc_id:
                    logger.warning("ID Converter returned an unusable PMC ID: %r", pmc_id)
                return None

            logger.info("PMC ID %s resolved via NCBI ID Converter for %s", pmc_id, ids)
            return pmc_id
        except Exception:
            logger.debug("ID Converter lookup failed for %s", ids, exc_info=True)
            return None

    def _fetch_europepmc(self, pmc_id: str) -> tuple[str, bool]:
        """Fetch JATS XML from Europe PMC and parse to HTML.

        Returns:
            A tuple of the rendered HTML and whether the document had a body,
            as for :meth:`_fetch_jats_xml`.
        """
        normalized = _normalise_pmc_id(pmc_id)
        url = f"{EUROPE_PMC_BASE}/{normalized}/fullTextXML"

        resp = self._http_get(url, headers={"Accept": "application/xml"})
        if resp.status_code == 404:
            raise FullTextError(f"No full text in Europe PMC for {normalized}")
        if resp.status_code != 200:
            raise FullTextError(f"Europe PMC HTTP {resp.status_code}")

        parser = JATSParser(resp.content, known_pmc_id=normalized)
        article, html = parser.parse_with_html()
        return html, article.has_body

    def _fetch_ncbi_pmc(self, pmc_id: str) -> tuple[str, bool]:
        """Fetch a PMC article from NCBI's own copy via E-utilities ``efetch``.

        Europe PMC's ``fullTextXML`` serves the corpus its ``inEPMC`` flag
        describes; NCBI serves PMC itself. For an article PMC holds and Europe
        PMC does not, this is the only source that answers.

        Returns:
            A tuple of the rendered HTML and whether the document had a body,
            as for :meth:`_fetch_europepmc`.

        Raises:
            FullTextError: On a bad ID, a non-200 response, or a reply
                carrying no article at all. That last case is efetch's answer
                for an article whose publisher does not release XML: it is
                HTTP 200 and parses cleanly into a document with no body *and*
                no abstract. Returned rather than raised, it would be promoted
                to the last-resort abstract and become near-empty HTML
                labelled as one.
        """
        normalized = _normalise_pmc_id(pmc_id)
        resp = self._http_get(
            EUTILS_EFETCH_URL,
            params=self._ncbi_params(
                db="pmc",
                id=normalized.removeprefix("PMC"),
                retmode="xml",
            ),
            headers={"Accept": "application/xml"},
        )
        if resp.status_code != 200:
            raise FullTextError(f"NCBI PMC HTTP {resp.status_code}")

        article, html = JATSParser(resp.content, known_pmc_id=normalized).parse_with_html()
        if not article.has_body and not article.abstract_sections:
            raise FullTextError(f"NCBI PMC returned no article content for {normalized}")
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
