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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:  # Annotation only; the real import is guarded in _require_httpx.
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


class FullTextUnavailableError(FullTextError):
    """A source answered, and it has no free full text for this article.

    Split from its base because the exhaustion report (issue #67) is built on
    telling a broken chain from an ordinary paywalled paper, and
    ``FullTextError`` alone cannot carry that: ``Unpaywall HTTP 503`` and
    ``DOI not found in Unpaywall`` were the same type, so a total outage and
    a paper nobody serves for free produced byte-identical summaries.

    Raised where a source replied and had nothing. A transport or protocol
    fault — a 5xx, a timeout, unparseable JSON — stays a plain
    ``FullTextError``. Nothing that catches ``FullTextError`` is affected,
    and the tier chain swallows both alike.
    """


def _plural(n: int, noun: str) -> str:
    """Render ``n`` with its noun, pluralised the naive way."""
    return f"1 {noun}" if n == 1 else f"{n} {noun}s"


# Exception types that can only mean a bmlib defect, never a remote-data or
# environment failure. `except Exception` at the tier level is right for
# transport errors; it is wrong to hold a TypeError at DEBUG under any
# circumstances (issue #72).
#
# A deny-list rather than an allow-list because the legitimate failures are
# varied — FullTextError, httpx.HTTPError, json.JSONDecodeError, ET.ParseError,
# OSError — while the set that always means a defect is small and stable.
# NameError carries UnboundLocalError in by inheritance.
#
# What is *excluded* is the load-bearing part, and two of them are counter-
# intuitive, so they are named rather than left to be rediscovered:
#
#   ValueError    json.JSONDecodeError IS a ValueError — every resp.json() on
#                 a malformed body raises one.
#   SyntaxError   xml.etree.ElementTree.ParseError IS a SyntaxError — malformed
#                 remote XML raises one.
#   RuntimeError  RecursionError is one, and Path.home() raises one.
#   OSError       environment, not defect.
#
# AttributeError is knowingly imperfect in the other direction: it is reachable
# from remote data, since `data.get("resultList", {}).get("result", [])` raises
# it when Europe PMC returns a non-dict there. It stays, because that is a bmlib
# defect too — a missing shape check — and the message describes what happened
# rather than accusing the article.
_BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
)


@dataclass
class _TierFailures:
    """Why one :meth:`FullTextService.fetch_fulltext` call came up empty.

    Every tier that makes a request catches its own exception, logs at DEBUG
    and moves on. That is correct — an unreachable Unpaywall must not cost
    the DOI fallback — but it left a chain that failed *everywhere*
    indistinguishable from one that was simply offered nothing, and silent at
    any level a caller normally runs at (issue #67).

    Faults and absences are kept apart rather than counted together, because
    the only question an operator can act on is whether anything went
    *wrong*. A source replying "no free full text" is the ordinary outcome
    for most papers; a ``ConnectError`` across a corpus is a lost network,
    and a ``TypeError`` is a bug. :class:`FullTextUnavailableError` is what sorts
    one from the other, so a source that reports an absence by raising is
    counted beside one that reports it by returning.

    Faults are a list because their type *names* are rendered; absences are a
    count because only their number is. Nothing here holds two fields
    describing the same events, so they cannot drift apart.

    Type names, not messages: a message carries the URL and the identifier,
    so nine of them would be as long as the DEBUG log this summary exists to
    replace. Each full message and traceback is already at DEBUG.
    """

    faults: list[str] = field(default_factory=list)
    absences: int = 0
    # Called at the moment a defect-shaped exception is swallowed, not at an
    # exit. Every alternative reads this record at some exit, which is exactly
    # issue #72: `describe()` is already consulted at one exit, and that is why
    # the bug was invisible. Reporting at the swallow is the only shape a new
    # early return cannot silently re-break. Optional so that a bare
    # `_TierFailures()` — which the tests construct — still works.
    on_bug: Callable[[BaseException], None] | None = None

    def record(self, exc: BaseException) -> None:
        """Note one swallowed exception, filed by what it means."""
        if isinstance(exc, FullTextUnavailableError):
            self.absences += 1
            return
        self.faults.append(type(exc).__name__)
        if self.on_bug is not None and isinstance(exc, _BUG_TYPES):
            self.on_bug(exc)

    def note_absence(self) -> None:
        """Note a source that reported an absence by returning, not raising."""
        self.absences += 1

    def describe(self) -> str:
        """Summarise the attempts for a log line.

        Worded as *attempts*, never tiers: Tier 0 records once per
        fetcher-supplied source, so the number is not bounded by the chain's
        eight tiers and "9 tiers raised" was emittable from a run that
        attempted four.
        """
        parts = []
        if self.faults:
            kinds = ", ".join(sorted(set(self.faults)))
            parts.append(f"{_plural(len(self.faults), 'attempt')} failed ({kinds})")
        if self.absences:
            parts.append(f"{_plural(self.absences, 'source')} had nothing")
        return "; ".join(parts) if parts else "no attempt reported a failure"


def _require_httpx() -> ModuleType:
    """Import httpx, naming the extra that supplies it.

    Returns the module rather than binding it on the service. A module object
    cannot be pickled, so storing one would make ``FullTextService`` unusable
    across a process pool, and reading it as instance state would let any
    object that reached :meth:`FullTextService._http_get` without running
    ``__init__`` fail with an ``AttributeError`` that the tier chain swallows.
    After the first call this costs a ``sys.modules`` lookup.

    Returns:
        The imported ``httpx`` module.

    Raises:
        ImportError: If httpx cannot be imported, naming the extra *and*
            reporting what was actually raised. ``ImportError`` also covers
            ``ModuleNotFoundError`` from httpx's own dependencies, so
            asserting the cause would tell someone with a broken httpx to
            install one they already have — the reasoning `_attach_pdf_text`
            spells out for the analogous PyMuPDF case.
    """
    try:
        import httpx
    except ImportError as e:
        raise ImportError(
            f"httpx is required for full-text retrieval, but importing it failed "
            f"({e}). Install with: pip install bmlib[fulltext]"
        ) from e
    return httpx


# Europe PMC labels a fullTextUrl entry's access twice over: a display string
# (`availability`) and a short controlled code (`availabilityCode`). Both are
# read — the code decides when present, the string is the fallback for an entry
# carrying none.
#
# An allow-list, never a deny-list on "Subscription required": an unknown future
# value must under-credit, costing one retrieval, rather than send bmlib to
# download a paywalled PDF. Transparency's _DEPOSITION_DATABANK_LEVELS is the
# same decision for the same reason.
#
# Measured over 600 recent MEDLINE records — all 1,263 fullTextUrl entries,
# of which 326 were documentStyle=pdf (scripts/sample_free_pdf_urls.py):
#
#     availability             code   pdf entries   share
#     Open access              OA             312   95.7%
#     Free                     F               14    4.3%
#     Subscription required    S                0      --
#
# There was no fourth value and every entry carried a code. Accepting only
# "Free" — which is what this did until issue #79 — therefore discarded 95.7%
# of the free PDFs Tier 1d exists to find, silently: both accepted labels are
# the identical https://europepmc.org/articles/PMC…?pdf=render shape on the
# identical host, and there is no log line for "a PDF entry was seen and not
# taken".
_FREE_PDF_AVAILABILITY_CODES = frozenset({"OA", "F"})
_FREE_PDF_AVAILABILITY_LABELS = frozenset({"Open access", "Free"})


def _entry_is_free(entry: dict[str, object]) -> bool:
    """Whether a ``fullTextUrl`` entry is one bmlib may download.

    Args:
        entry: One entry from Europe PMC's ``fullTextUrlList``.

    Returns:
        ``True`` when the entry's access code is one bmlib accepts, or — for an
        entry carrying no code — its display string is. A code that is present
        but unrecognised returns ``False`` **without** consulting the string:
        falling back there would let a future code bmlib has never evaluated
        through on the strength of a label, which is the opposite of the
        under-credit rule the allow-list exists to keep.

        Both values are type-checked before they are compared. ``x in
        frozenset`` *hashes* ``x``, so an ``availability`` arriving as a JSON
        object or array — a shape Europe PMC does not document itself out of —
        raised ``TypeError: unhashable type``. That is in :data:`_BUG_TYPES`,
        so a malformed remote payload would have been reported as a bmlib
        defect (issue #72's warning) rather than as an entry to skip.
    """
    code = entry.get("availabilityCode")
    if isinstance(code, str) and code:
        return code in _FREE_PDF_AVAILABILITY_CODES
    availability = entry.get("availability")
    return isinstance(availability, str) and availability in _FREE_PDF_AVAILABILITY_LABELS


def _extract_free_pdf_url(result: dict[str, object]) -> str | None:
    """Extract a free PDF URL from Europe PMC's ``fullTextUrlList``.

    The search API includes ``fullTextUrlList`` with ``?pdf=render`` entries
    for PDFs it serves itself, even when JATS XML is unavailable — which is
    exactly when Tier 1d needs one.
    """
    url_list = result.get("fullTextUrlList")
    if not isinstance(url_list, dict):
        return None
    for entry in url_list.get("fullTextUrl", []):
        if (
            isinstance(entry, dict)
            and entry.get("documentStyle") == "pdf"
            and _entry_is_free(entry)
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


def _default_cache() -> FullTextCache | None:
    """Construct the default disk cache, or degrade to no caching.

    The cache is best-effort everywhere else in this module — a failed write
    warns once and retrieval continues, a failed read falls through to the
    network — and construction was the last place an environment fault about
    the *cache* could abort a run that had every chance of succeeding without
    one.

    Only the *default* is guarded. A caller who constructs a
    :class:`~bmlib.fulltext.cache.FullTextCache` themselves asked for a cache
    specifically, and still gets the raise: degrading there would return an
    object whose every method then fails one at a time, rather than failing
    once, clearly, at construction.

    Taking **no parameters** is what makes that asymmetry structural rather
    than conventional: there is no way to route a caller-supplied
    ``cache_dir`` through the degrading path, so the only caller who can
    reach it is one who expressed no preference. Adding a ``cache_dir``
    argument here would quietly undo the decision.

    ``OSError`` covers the three ``mkdir`` calls — a file standing where the
    directory should be (``FileExistsError``; ``exist_ok=True`` suppresses that
    only when the target *is* a directory), a read-only parent, a file as an
    intermediate component, a full disk. ``RuntimeError`` covers the step
    before them: ``_default_cache_dir()`` calls ``Path.home()``, which raises
    that, not ``OSError``, when there is no ``HOME`` and no passwd entry. The
    pair is deliberately not ``Exception`` — inside this one constructor
    ``RuntimeError`` has exactly one *source*, so the guard stays narrow enough
    that a bmlib bug still surfaces as one. Its subclasses ``RecursionError``
    and ``NotImplementedError`` come along by inheritance, which is why the
    message interpolates the type: such a case reads as itself rather than
    passing for an ordinary environment fault.

    The warning names what the degraded run costs, not just that it is
    degraded. Saying only "nothing will be cached" would understate it: a
    PDF is fetched *into* the cache, so with no cache there is no download at
    all and a PDF-only article comes back as a bare URL. That is lost content,
    not merely repeated network traffic, and it is the half an operator would
    otherwise discover from the results.

    Returns:
        The cache, or ``None`` if it could not be created.
    """
    try:
        return FullTextCache()
    except (OSError, RuntimeError) as exc:
        logger.warning(
            "Could not create the full-text cache directory (%s: %s); retrieval "
            "still works and full text still parses, but nothing will be cached, "
            "so every run re-fetches — and a PDF-only article comes back as a bare "
            "URL, since a PDF is downloaded into the cache and extracted only once "
            "cached. Pass cache=FullTextCache(cache_dir=...) to use a writable "
            "location.",
            type(exc).__name__,
            exc,
        )
        return None


class FullTextService:
    """Retrieves full text from multiple sources with fallback.

    Attributes:
        cache: The disk cache, or ``None``. It is ``None`` only when the
            ``cache`` argument was omitted *and* the default could not be
            built — a caller who passes one always gets it back. Code that
            calls a method on it needs a ``None`` check; code that supplied
            the cache can reason that the branch is unreachable, but the type
            no longer records that, so the check is still required to type.
    """

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
            cache: Disk cache to use. A default one is created when omitted;
                if that directory cannot be created — a file standing where it
                should be, a read-only parent, no determinable home directory —
                the service warns once and runs without a cache rather than
                failing to construct, since retrieval does not need one, and
                :attr:`cache` is then ``None``. In that state a JATS article
                still parses to full text, but a PDF is never downloaded, so a
                PDF-only article carries only ``pdf_url``. A cache passed here
                is used as given, and one constructed directly still raises.
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
            ImportError: If httpx cannot be imported, naming the extra that
                supplies it — see :func:`_require_httpx`. Checked before
                anything else happens, so a construction that fails leaves no
                cache directory behind. The module is not retained; the check
                is here so the failure lands at construction rather than on
                the first request.
        """
        _require_httpx()

        self.email = email
        self.timeout = timeout
        self.cache: FullTextCache | None = cache if cache is not None else _default_cache()
        self.convert_pdfs = convert_pdfs
        self.ncbi_api_key = ncbi_api_key
        # Faults that are a property of the environment or of bmlib itself
        # rather than of one article: warned once each, keyed by cause, so a
        # second distinct fault is still reported instead of hiding behind the
        # first. Per service, not process-wide — a caller that builds a fresh
        # service has a fresh environment to learn about.
        self._warned: set[str] = set()

    def _http_get(self, url: str, **kwargs: object) -> httpx.Response:
        """HTTP GET with timeout. Separated for testability."""
        httpx = _require_httpx()
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
          1b'. Discover PMC ID via NCBI's ID Converter when the search
               reported none, then fetch XML
          1c. NCBI PMC efetch for whichever PMC ID was resolved
          1d. Europe PMC PDF render URL (free PDF when XML unavailable)
          2.  Unpaywall PDF URL
          3.  DOI / PubMed URL fallback
        """
        cache_id = _sanitize_identifier(identifier) if identifier else None

        # Cache check — return immediately if content already on disk
        if cache_id and self.cache is not None:
            try:
                cached = self._check_cache(self.cache, cache_id)
            except Exception as exc:
                # A cache *read* is best-effort exactly as a cache write is.
                # An entry truncated by a killed process or a filesystem fault
                # raised UnicodeDecodeError straight out of this method (#71):
                # it broke the documented FullTextError-only contract, and it
                # was a hard stop where re-fetching over the network was
                # available, so one bad file made a paper permanently
                # unfetchable and took a bulk sync down with it.
                #
                # The guard covers everything _check_cache does, which is a
                # read and — for a cached PDF — the re-extraction that follows
                # it. Both are better re-fetched than raised, and neither can
                # be told apart from here, which is why the exception type is
                # reported rather than a cause asserted: _TierFailures does the
                # same, so that a TypeError among the faults reads as the bug
                # it is instead of hiding behind a sentence about bad files.
                #
                # Warned per article, unlike the once-per-service write
                # warning: an unwritable directory is a property of the
                # directory, while this is a property of one file. Not counted
                # on the exhaustion report below — the cache is not a retrieval
                # attempt, and this line already says more than that report's
                # two buckets could.
                logger.warning(
                    "Could not read the cached full text for %s (%s: %s); re-fetching.",
                    cache_id,
                    type(exc).__name__,
                    exc,
                )
                logger.debug("Cache read failed for %s", cache_id, exc_info=True)
                # Moved aside, not deleted: the bytes stay available under a
                # .corrupt suffix, but out of the lookup path. Left where it
                # was, an undecodable HTML entry is consulted ahead of the PDF
                # entry, so it hides a good PDF behind it and the same warning
                # and the same network fetch repeat on every run forever — a
                # re-fetch only overwrites it when the chain happens to return
                # JATS full text.
                self._quarantine_cache_entry(self.cache, cache_id)
                cached = None
            if cached is not None:
                return cached

        # A body-less JATS rendering picked up along the way. Held back as a
        # last resort rather than returned, since it carries only the
        # abstract while a later tier may still find the whole article.
        abstract_only: FullTextResult | None = None

        # Every tier below swallows its own exception so the next one still
        # runs; this is what remembers that they did.
        failures = _TierFailures(on_bug=self._warn_swallowed_bug)

        # Tier 0: Try fetcher-provided sources
        if fulltext_sources:
            result, abstract_only = self._try_known_sources(
                fulltext_sources, cache_id=cache_id, failures=failures
            )
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
            except Exception as exc:
                logger.debug("Europe PMC failed for %s", pmc_id, exc_info=True)
                failures.record(exc)
                xml_failed = True

        # Tier 1b: Discover PMC ID via Europe PMC search, then fetch XML
        pdf_render_url: str | None = None
        if not pmc_id and (doi or pmid):
            discovered_pmc_id: str | None = None
            try:
                discovered_pmc_id, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi, pmid=pmid, failures=failures
                )
            except Exception as exc:
                logger.debug(
                    "Europe PMC search failed for doi=%s pmid=%s",
                    doi,
                    pmid,
                    exc_info=True,
                )
                failures.record(exc)

            # Tier 1b′: the search reports a PMC ID only for what Europe PMC
            # both indexed and holds. NCBI's converter depends on neither, and
            # is asked second because that one search also returned the
            # free-PDF URL Tier 1d needs. It sits outside the search's `except`
            # deliberately: a search that raised is precisely when a second,
            # independent resolver is worth having, and folding this back into
            # that block would skip it there.
            if not discovered_pmc_id:
                discovered_pmc_id = self._resolve_pmc_id_via_idconv(
                    doi=doi, pmid=pmid, failures=failures
                )

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
                except Exception as exc:
                    logger.debug(
                        "Europe PMC fetch failed for discovered %s",
                        discovered_pmc_id,
                        exc_info=True,
                    )
                    failures.record(exc)

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
            except Exception as exc:
                logger.debug("NCBI PMC failed for %s", resolved_pmc_id, exc_info=True)
                failures.record(exc)

        # When XML failed with a known PMC ID, search for PDF render URL
        if xml_failed and not pdf_render_url and (doi or pmid):
            try:
                _, pdf_render_url = self._resolve_pmc_id_and_pdf_url(
                    doi=doi,
                    pmid=pmid,
                    failures=failures,
                )
            except Exception as exc:
                logger.debug("PDF URL resolution failed", exc_info=True)
                failures.record(exc)

        # Tier 1d: Europe PMC PDF render (when XML unavailable but free PDF exists)
        if pdf_render_url:
            logger.info("PDF available from Europe PMC render: %s", pdf_render_url)
            result = FullTextResult(source="europepmc_pdf", pdf_url=pdf_render_url)
            self._download_and_cache_pdf(pdf_render_url, cache_id, result, origin="europepmc_pdf")
            return self._with_abstract_fallback(result, abstract_only)

        # Tier 2: Unpaywall
        if doi:
            try:
                pdf_url = self._fetch_unpaywall(doi)
                logger.info("PDF URL found via Unpaywall for DOI %s", doi)
                result = FullTextResult(source="unpaywall", pdf_url=pdf_url)
                self._download_and_cache_pdf(pdf_url, cache_id, result, origin="unpaywall")
                return self._with_abstract_fallback(result, abstract_only)
            except Exception as exc:
                logger.debug("Unpaywall failed for DOI %s", doi, exc_info=True)
                failures.record(exc)

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

        # An empty call is not an exhausted chain: nothing was asked of any
        # source, so there is no failure to summarise and the report below
        # would claim otherwise. Raised ahead of it for that reason.
        if not (fulltext_sources or pmc_id or doi or pmid):
            raise FullTextError("No identifiers provided")

        # One report for every empty-handed exit — the two returns below and
        # the raise. The reason it is not inside the abstract branch where it
        # started: keeping it there made the *more* complete failure the
        # quieter one. A caller whose every attempt failed got a result shaped
        # exactly like a paper that genuinely has no free full text, and
        # nothing above DEBUG to tell them apart (issue #67). The summary is
        # what distinguishes them.
        if abstract_only is not None:
            outcome = "returning the abstract only"
        elif web_url is not None:
            outcome = "nothing was retrieved"
        else:
            outcome = "nothing was retrieved and there is no link to fall back on"
        logger.warning(
            "No full text found for doi=%s pmid=%s — %s; %s",
            doi,
            pmid,
            outcome,
            failures.describe(),
        )

        if abstract_only is not None:
            if web_url:
                abstract_only.web_url = web_url
            return abstract_only

        if web_url is None:
            # Identifiers were given — the empty call raised above — so this
            # is an exhausted chain with no link to degrade to. Saying "no
            # identifiers provided" here, as it used to, sent the reader
            # looking in the wrong place.
            raise FullTextError(
                f"Nothing retrieved and no DOI or PMID to fall back on — {failures.describe()}"
            )

        return FullTextResult(source="doi" if doi else "pubmed", web_url=web_url)

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
        failures: _TierFailures,
    ) -> tuple[FullTextResult | None, FullTextResult | None]:
        """Try fetcher-provided fulltext sources in priority order.

        Priority: xml (JATS) > pdf > html.

        Args:
            sources: The fetcher's known source URLs.
            cache_id: Sanitised cache key, or ``None`` to skip caching.
            failures: The caller's exhaustion report. Every entry that raised
                is recorded on it — once per *entry*, not once for the tier —
                so the caller can say whether an empty-handed chain broke or
                was simply offered nothing.

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
                    self._download_and_cache_pdf(entry.url, cache_id, result, origin="known_source")
                    return result, abstract_only
                elif entry.format == "html":
                    logger.info("HTML source from %s", entry.source)
                    return FullTextResult(source=entry.source, web_url=entry.url), abstract_only
            except Exception as exc:
                logger.debug(
                    "Known source %s (%s) failed",
                    entry.source,
                    entry.url,
                    exc_info=True,
                )
                failures.record(exc)
                continue

        return None, abstract_only

    # --- Cache helpers --------------------------------------------------------

    def _check_cache(self, cache: FullTextCache, cache_id: str) -> FullTextResult | None:
        """Return a cached FullTextResult if available on disk.

        Only HTML that came from a JATS ``<body>`` is ever written to the
        cache, so a cached HTML hit is always full text. Text extracted from
        a PDF is not cached — it is re-derived here from the cached PDF, so a
        cache hit carries the same ``html`` and ``content_kind`` as the
        original retrieval instead of silently dropping to a bare file path.
        Re-extraction is local CPU work on a file already on disk; caching the
        output instead would make it indistinguishable from real full text on
        the next hit.

        Args:
            cache: The cache to read, known non-``None``. Taken as an argument
                rather than off ``self`` because :attr:`cache` became optional
                in #75, and a precondition the caller has to remember is one it
                can forget. As a parameter it is *checkable*: the narrowing and
                the use sit in one function body, where a type checker can
                discharge the obligation. Nothing in this repository does —
                CI runs ruff, not mypy — so the guarantee is one a downstream's
                checker gets, and one a reader can verify locally.
            cache_id: Sanitised cache key.
        """
        html = cache.get_html(cache_id)
        if html:
            logger.info("Cache hit (HTML) for %s", cache_id)
            return FullTextResult(source="cached", html=html, content_kind="fulltext")
        pdf_path = cache.get_pdf(cache_id)
        if pdf_path:
            logger.info("Cache hit (PDF) for %s", cache_id)
            result = FullTextResult(source="cached", file_path=pdf_path)
            self._attach_pdf_text(pdf_path, result)
            return result
        return None

    def _quarantine_cache_entry(self, cache: FullTextCache, cache_id: str) -> None:
        """Move an unreadable cache entry aside, never raising.

        :meth:`FullTextCache.quarantine` is already best-effort, but this runs
        from inside the handler that exists to keep ``fetch_fulltext`` to its
        ``FullTextError``-only contract. Tidying up must not become the thing
        that breaks it, so a caller-supplied cache that does not implement the
        method, or an unforeseen fault inside one that does, is logged and
        dropped rather than allowed to escape.

        Args:
            cache: The cache to quarantine in, known non-``None``. A parameter
                for the same reason as :meth:`_check_cache`'s.
            cache_id: Sanitised cache key of the entry to move aside.
        """
        try:
            cache.quarantine(cache_id)
        except Exception:
            logger.debug("Could not quarantine the cache entry for %s", cache_id, exc_info=True)

    def _warn_cache_write_failed(self, exc: BaseException) -> None:
        """Report an unwritable cache, once per service.

        Best-effort: the content is already in hand, so a write that fails
        costs nothing this call. It costs every *later* call — a read-only
        cache directory or a full disk means the whole corpus is re-fetched
        over the network on every run, permanently. Said once, like the
        missing-``bmlib[pdf]`` warning: the cause is a property of the
        directory, not of the article, so one line per paper would be noise.

        Shared by the HTML and PDF writes rather than living in either. A
        corpus served mostly by PDFs never writes HTML, so a warning that
        only the HTML path could emit stayed silent for exactly the callers
        it was meant to reach.
        """
        self._warn_once(
            "cache-write",
            "Could not write to the full-text cache (%s); retrieval still "
            "works, but nothing is being cached, so every run re-fetches.",
            exc,
        )

    def _warn_swallowed_bug(self, exc: BaseException) -> None:
        """Report a tier failure that can only be a bmlib defect.

        Once per exception type per service: a defect that hits one tier hits
        it for every article, so a line per article would be unreadable at
        exactly the moment it mattered — but a *second*, different defect must
        still be reported rather than hidden by the first.
        """
        name = type(exc).__name__
        self._warn_once(
            f"bug:{name}",
            "A full-text tier failed with %s (%s), which bmlib does not raise "
            "deliberately — this is a defect, possibly provoked by an "
            "unexpected API response. Full text may be silently degraded for "
            "every article in this run while later tiers keep succeeding. Run "
            "with DEBUG logging for the traceback and please report it; "
            "further %s failures will not be repeated.",
            name,
            exc,
            name,
        )

    def _warn_once(self, key: str, msg: str, *args: object) -> None:
        """Emit a WARNING the first time *key* is seen on this service.

        Args:
            key: What is being reported. Include the *cause* — not just the
                site — so two different faults at one site are both reported.
            msg: A ``%``-style format string, interpolated lazily by logging.
            args: Its arguments.
        """
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(msg, *args)

    def _cache_html(self, html: str, cache_id: str | None) -> None:
        """Save HTML to disk cache if caching is enabled.

        Reads :attr:`cache` directly, unlike :meth:`_check_cache`,
        :meth:`_quarantine_cache_entry` and :meth:`_save_pdf_to_cache`. Those
        three are reached only from a site that has already established a
        cache, so they take it as a parameter; this method and
        :meth:`_download_and_cache_pdf` *are* those sites. Giving it a
        parameter too would push the same branch out into its four
        unconditional call sites.

        Failing to write costs only a re-fetch next run — the HTML is already
        in hand and is returned either way — so no cache means nothing to say
        here beyond the warning construction has already emitted.
        """
        if cache_id and self.cache is not None:
            try:
                self.cache.save_html(html, cache_id)
            except Exception as e:
                self._warn_cache_write_failed(e)
                logger.debug("Failed to cache HTML for %s", cache_id, exc_info=True)

    def _download_and_cache_pdf(
        self,
        pdf_url: str,
        cache_id: str | None,
        result: FullTextResult,
        *,
        origin: str,
    ) -> None:
        """Download a PDF and save it to the disk cache.

        On success, sets ``result.file_path`` to the cached file and — when
        ``convert_pdfs`` is on and a backend is available — ``result.html``
        and ``result.content_kind`` from the PDF's extracted text.
        On failure (network error or invalid PDF), leaves result unchanged
        so the caller can still use ``result.pdf_url`` as a fallback.

        Returns without downloading at all when there is nowhere to put the
        file: no cache (#75) or no ``identifier`` to key one by. The URL stays
        on the result in both cases.

        Args:
            pdf_url: The PDF to fetch.
            cache_id: Sanitised cache key, or ``None`` to skip the download.
            result: The tier's result, modified in place.
            origin: Which tier is downloading — one of ``"europepmc_pdf"``,
                ``"unpaywall"`` or ``"known_source"``. Used, and *only* used,
                to key the one-shot download-failure warnings.

                It exists because ``result.source`` cannot key them.
                :data:`_warn_once`'s suppression is only one-shot if its
                keyspace is bounded, and Tier 0's ``result.source`` comes from
                a fetcher-supplied :class:`FullTextSourceEntry`: OpenAlex
                derives it from the location's **venue display name**
                (``bmlib/publications/fetchers/openalex.py``), so it is one
                distinct, remote-data-derived string per journal or
                repository. Keyed on that, a bulk sync warns once *per
                article* — each one claiming the report is one-shot — at the
                site with the worst measured failure rate (Tier 0's PDF
                locations are the same arbitrary-repository population
                Unpaywall draws from, measured at 64.3% failure). This
                parameter is written out at each call site so the value set is
                the enumeration above and cannot grow with the corpus. Do not
                "simplify" it back to ``result.source``; the source still
                appears in the message text, so the first report still names
                the specific venue.
        """
        if self.cache is None:
            # Reachable only when the default cache could not be created (#75).
            # DEBUG because the construction warning already named this exact
            # consequence — not because one line per paper would be noise,
            # which is equally true of the INFO branch below. What separates
            # them is that nobody warned at construction about an identifier
            # the caller had not passed yet.
            #
            # Not gated on convert_pdfs: the download is skipped either way, so
            # `file_path` is lost even for a caller who turned extraction off
            # precisely because they wanted the file. Nor does it borrow the
            # message below, which would assert a cause that is false.
            logger.debug(
                "The full-text cache could not be created, so %s is not downloaded — "
                "the URL is left on the result, and there is no file to extract "
                "text from",
                pdf_url,
            )
            return
        if not cache_id:
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
                self._report_pdf_download_failure(
                    result.source,
                    origin,
                    pdf_url,
                    f"HTTP {resp.status_code}",
                    f"http-{resp.status_code}",
                )
                return
            path, outcome = self._save_pdf_to_cache(self.cache, resp.content, cache_id)
            if outcome == "not-a-pdf":
                self._report_pdf_download_failure(
                    result.source, origin, pdf_url, "the response is not a PDF", "not-a-pdf"
                )
                return
            if path is None:
                # write-failed: _warn_cache_write_failed has already spoken, and
                # it names the right cause. Saying anything more here would
                # blame the publisher for a read-only directory.
                return
            result.file_path = path
            logger.info("PDF cached to %s", path)
            self._attach_pdf_text(path, result)
        except Exception as exc:
            # The environment, not the server. A lost network or a full disk
            # fails *every* article once it starts failing, so this is one-shot
            # per (origin, type) and needed no measurement to decide — unlike
            # the two server-side causes above. `origin` is the bounded tier
            # literal, never `result.source`, which a fetcher derives from
            # remote data; see the `origin` parameter's docstring above.
            #
            # Still not recorded on the exhaustion report: all three call sites
            # return the result immediately after this, so a failure noted
            # there could never reach the report that reads it (issue #68).
            self._warn_once(
                f"pdf-download:{origin}:{type(exc).__name__}",
                "Could not download a %s PDF (%s: %s). The URL is left on the "
                "result, but there is no file and no extracted text, and this "
                "will affect every article served this way. Further %s "
                "failures will not be repeated.",
                result.source,
                type(exc).__name__,
                exc,
                type(exc).__name__,
            )
            logger.debug("PDF download failed for %s", pdf_url, exc_info=True)

    def _report_pdf_download_failure(
        self, source: str, origin: str, pdf_url: str, reason: str, cause: str
    ) -> None:
        """Report a server-side PDF download failure (issue #68).

        Args:
            source: ``result.source`` — the specific venue or service, named in
                the message so the first report loses no detail.
            origin: The downloading tier, which is what the key is built from.
                See :meth:`_download_and_cache_pdf` for why ``source`` cannot
                be: it is remote-data-derived at Tier 0 and would make the
                keyspace unbounded, turning the one-shot into one per article.
            pdf_url: The URL that failed, quoted as "first seen at".
            reason: Human-readable cause, e.g. ``"HTTP 404"``.
            cause: Its stable key fragment, e.g. ``"http-404"``.

        Once per (origin, cause), with the per-article detail at DEBUG. The
        level was chosen from a measured rate rather than by taste, against a
        rule fixed before the numbers landed: under 5% of attempts a
        per-article WARNING is affordable, and at or above it a bulk run's log
        would be drowned at exactly the moment it mattered.

        Measured by ``scripts/sample_free_pdf_urls.py --target 150
        --per-host-interval 4.0``, one interpreter run, 2026-08-11:

        =========== ====== ======== ======= ================ ==========================
        Population   Probed Failed   Rate    95% CI            Cause breakdown
        =========== ====== ======== ======= ================ ==========================
        europepmc    150    1        0.7%    [0.1%, 3.7%]     exception-ConnectError: 1
        unpaywall    28     18       64.3%   [45.8%, 79.3%]   http-403: 4, not-a-pdf: 14
        biorxiv      150    1        0.7%    [0.1%, 3.7%]     exception-ReadTimeout: 1
        =========== ====== ======== ======= ================ ==========================

        The worst population is Unpaywall at 64.3%, whose CI lower bound
        (45.8%) is roughly nine times the 5% threshold — not a close call
        despite ``n=28`` being small. That selects this variant.

        Europe PMC and bioRxiv had **zero** server-side failures: each
        population's single failure was a transport exception, which takes
        the one-shot-per-exception-type path in :meth:`_download_and_cache_pdf`
        regardless of this rule. Every one of the 18 server-side failures
        counted above is Unpaywall's. 14 of those 28 were landing pages
        rather than PDFs (the magic-byte rejection, ``not-a-pdf``) rather than
        an HTTP failure — which is why the key is per ``(origin, cause)``
        rather than per origin: the rate is a property of the population
        Unpaywall draws from, not a property of bmlib. Re-run the sampler
        before revisiting this.

        The message says the report is one-shot; it does **not** say the
        failure is common. That distinction is load-bearing after issue #79.
        Widening Tier 1d's availability allow-list made ``europepmc_pdf`` the
        dominant caller of this line, and the table above records *zero*
        server-side failures for Europe PMC — so the earlier wording ("this is
        common enough that it is reported once") was measurably false for the
        source that now emits it most.
        """
        self._warn_once(
            f"pdf-download:{origin}:{cause}",
            "Could not download a %s PDF (%s; first seen at %s). The URL is "
            "left on the result, but there is no file and no extracted text. "
            "This is reported once — run with DEBUG logging to see every "
            "affected article.",
            source,
            reason,
            pdf_url,
        )
        logger.debug("PDF download failed (%s) for %s", cause, pdf_url)

    def _save_pdf_to_cache(
        self, cache: FullTextCache, data: bytes, cache_id: str
    ) -> tuple[str | None, str]:
        """Write a downloaded PDF to the disk cache, best-effort.

        Split out of the download so a failed *write* is reported like
        :meth:`_cache_html`'s. Left inside the download's own handler it was
        indistinguishable from a failed fetch, logged as "PDF download
        failed", and invisible above DEBUG.

        Args:
            cache: The cache to write to, known non-``None``. A parameter for
                the same reason as :meth:`_check_cache`'s.
            data: The downloaded PDF bytes.
            cache_id: Sanitised cache key.

        Returns:
            ``(path, outcome)``. ``outcome`` is ``"saved"``, ``"write-failed"``
            or ``"not-a-pdf"``. The two failures are told apart rather than
            both returning ``None``, because the caller reports them and
            blaming a read-only directory on the publisher's bytes is the
            mistake this method already avoids in its own logging.
        """
        try:
            path = cache.save_pdf(data, cache_id)
        except Exception as e:
            self._warn_cache_write_failed(e)
            logger.debug("Failed to cache PDF for %s", cache_id, exc_info=True)
            return None, "write-failed"
        if not path:
            logger.debug("PDF failed magic-byte validation for %s", cache_id)
            return None, "not-a-pdf"
        return path, "saved"

    def _attach_pdf_text(self, pdf_path: str, result: FullTextResult) -> None:
        """Extract a cached PDF's text into ``result.html``.

        A no-op when ``convert_pdfs`` is off or ``result.html`` is already
        populated — an earlier tier's text is never overwritten.

        Otherwise best-effort: a PDF backend that cannot be constructed (the
        ``bmlib[pdf]`` extra missing, or broken) or an unreadable PDF leaves
        the result untouched, so the caller still has the PDF itself.
        ``result.pdf_url`` and ``result.file_path`` are deliberately left in
        place — extracted text recovers the prose but not figures, tables or
        layout, so the original stays worth offering.

        Every way this can come up empty is logged at WARNING: a scanned PDF
        that yields nothing is invisible otherwise, and a partial extraction
        must not be mistaken for a whole article.
        """
        if not self.convert_pdfs or result.html:
            return
        try:
            converter = get_converter()
        except Exception as e:
            # Report what was actually raised rather than asserting the cause,
            # so a broken PyMuPDF install is not misreported as an uninstalled
            # one. Not narrowed to ImportError: get_converter() documents a
            # ValueError for an unknown backend name, and a third-party
            # backend's __init__ may raise anything at all. Narrowed, those
            # escaped this method entirely and came out in the handler that
            # wraps the _check_cache call, where a perfectly readable cached
            # PDF was blamed on an unreadable cache file and re-downloaded
            # into the identical deterministic fault.
            self._warn_once(
                f"pdf-backend:{type(e).__name__}",
                "convert_pdfs is enabled but no PDF backend is usable (%s: %s); "
                "PDFs will be returned as links only. Install bmlib[pdf] if the "
                "extra is missing.",
                type(e).__name__,
                e,
            )
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

        Raises:
            FullTextError: On a non-200 response other than 404.
            FullTextUnavailableError: On a 404. A fetcher's stored URL going
                stale is common, and counting it as a fault would inflate the
                one bucket the exhaustion report asks the operator to act on.
        """
        resp = self._http_get(url, headers={"Accept": "application/xml"})
        if resp.status_code == 404:
            raise FullTextUnavailableError(f"JATS XML not found: {url}")
        if resp.status_code != 200:
            raise FullTextError(f"JATS XML fetch failed: HTTP {resp.status_code}")
        article, html = JATSParser(resp.content).parse_with_html()
        return html, article.has_body

    def _resolve_pmc_id_and_pdf_url(
        self,
        *,
        doi: str | None = None,
        pmid: str = "",
        failures: _TierFailures,
    ) -> tuple[str | None, str | None]:
        """Search Europe PMC to discover a PMC ID and free PDF URL.

        Args:
            doi: Digital Object Identifier, preferred when present.
            pmid: PubMed ID, used when there is no DOI.
            failures: The caller's exhaustion report. A search that finds no
                record is noted on it as an absence.

        Returns:
            A tuple of (pmc_id, pdf_render_url). Either or both may be None.
            The PDF render URL comes from the ``fullTextUrlList`` in the
            search response and provides a free PDF when JATS XML is
            unavailable.

        Raises:
            FullTextError: On a non-200 response. Reported rather than
                returned as ``(None, None)``, which is also what an empty
                result set looks like: an unreachable Europe PMC then read as
                "this paper has no free full text", the misdiagnosis issue
                #67 exists to prevent. Both call sites catch it.
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
            raise FullTextError(f"Europe PMC search HTTP {resp.status_code}")

        data = resp.json()
        results = data.get("resultList", {}).get("result", [])
        if not results:
            failures.note_absence()
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
        failures: _TierFailures,
    ) -> str | None:
        """Resolve a PMC ID through NCBI's ID Converter.

        The second source for a PMC ID, consulted only when the Europe PMC
        search returned none. Europe PMC reports one only when it both indexed
        the paper and flagged its full text as available there; the converter
        depends on neither.

        Asked by PMID when there is one — an exact numeric key — and by DOI
        otherwise, since a DOI-formatting miss is one of the divergences this
        recovers.

        Args:
            doi: Digital Object Identifier, used when there is no PMID.
            pmid: PubMed ID, preferred when present.
            failures: The caller's exhaustion report. Required, like every
                other recorder here, even though this method has direct
                callers of its own in the tests: it has more recording sites
                than any other helper, and a future call site that omitted it
                would fail exactly the way issue #67 failed — silently, with
                the summary reading as an ordinary paywalled paper. A
                throwaway ``_TierFailures()`` costs a test one argument.

        Returns:
            The PMC ID, or ``None`` if the converter has no live record for the
            identifier, reports an error, answers with something unusable, or
            cannot be reached. It never raises: the caller has a free-PDF URL
            in hand by this point, and an exception would cost it. A converter
            that could not be reached is still recorded as a *fault* on
            ``failures``, and one that answered "no such record" as an
            absence — returning ``None`` for both is what let an outage read
            as an ordinary paywalled paper (issue #67).
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
                # Raised, not returned: the handler below files it as a fault
                # and still returns None, so the caller is unaffected while an
                # unreachable converter stops counting as an absence.
                raise FullTextError(f"ID Converter HTTP {resp.status_code} for {ids}")

            records = resp.json().get("records") or []
            if not records:
                failures.note_absence()
                return None

            record = records[0]
            if record.get("status") == "error":
                logger.debug("ID Converter has no record for %s: %s", ids, record.get("errmsg"))
                failures.note_absence()
                return None
            # Reported as the string "false" for a record PMC no longer serves.
            if str(record.get("live", "true")).lower() == "false":
                logger.debug("ID Converter record for %s is no longer live", ids)
                failures.note_absence()
                return None

            pmc_id = record.get("pmcid")
            if not isinstance(pmc_id, str) or not _PMC_ID_RE.fullmatch(pmc_id):
                if pmc_id:
                    logger.warning("ID Converter returned an unusable PMC ID: %r", pmc_id)
                    # A malformed id is the converter misbehaving, not an
                    # absence — the record exists and says something unusable.
                    failures.record(FullTextError(f"Unusable PMC ID: {pmc_id!r}"))
                else:
                    failures.note_absence()
                return None

            logger.info("PMC ID %s resolved via NCBI ID Converter for %s", pmc_id, ids)
            return pmc_id
        except Exception as exc:
            logger.debug("ID Converter lookup failed for %s", ids, exc_info=True)
            failures.record(exc)
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
            raise FullTextUnavailableError(f"No full text in Europe PMC for {normalized}")
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
            FullTextError: On a bad ID or a non-200 response.
            FullTextUnavailableError: On a reply carrying no article at all —
                efetch's answer for an article whose publisher does not
                release XML. It is HTTP 200 and parses cleanly into a
                document with no body *and* no abstract. Returned rather than
                raised, it would be promoted to the last-resort abstract and
                become near-empty HTML labelled as one. Raised as an absence
                rather than a fault because the source answered.
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
        if resp.status_code == 404:
            raise FullTextUnavailableError(f"NCBI PMC has no record for {normalized}")
        if resp.status_code != 200:
            raise FullTextError(f"NCBI PMC HTTP {resp.status_code}")

        article, html = JATSParser(resp.content, known_pmc_id=normalized).parse_with_html()
        if not article.has_body and not article.abstract_sections:
            raise FullTextUnavailableError(f"NCBI PMC returned no article content for {normalized}")
        return html, article.has_body

    def _fetch_unpaywall(self, doi: str) -> str:
        """Query Unpaywall for open-access PDF URL.

        Raises:
            FullTextError: On a non-200 response other than 404.
            FullTextUnavailableError: When Unpaywall has no record of the DOI, or
                holds one with no open-access location. Both are the service
                answering that there is nothing free — the ordinary outcome
                for most papers, and not something to act on.
        """
        encoded_doi = quote(doi, safe="")
        encoded_email = quote(self.email, safe="")
        url = f"{UNPAYWALL_BASE}/{encoded_doi}?email={encoded_email}"

        resp = self._http_get(url, headers={"Accept": "application/json"})
        if resp.status_code == 404:
            raise FullTextUnavailableError(f"DOI not found in Unpaywall: {doi}")
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

        raise FullTextUnavailableError(f"No open-access PDF found for DOI {doi}")
