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

"""PubMed E-utilities fetcher for biomedical publication records.

Uses the NCBI E-utilities API (esearch + efetch) to retrieve PubMed article
metadata for a given publication date.  Parses PubmedArticle XML elements into
plain dictionaries suitable for downstream storage.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, NamedTuple

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.fetchers._reconcile import reconcile_delivery
from bmlib.publications.models import (
    AuthorAffiliation,
    FetchedRecord,
    FetchResult,
    Grant,
    SyncProgress,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

EFETCH_PAGE_SIZE = 500

# NCBI's search backend serves only the first 9,999 records of a history
# session and refuses the rest, whatever `retmax` asks for. Measured live on
# 2026-08-20 (`docs/DECISIONS.md`, "publications — how far a PubMed session
# can be walked"): `retstart=9999` is HTTP 400 — *"'retstart' cannot be larger
# than 9998. For PubMed, ESearch can only retrieve the first 9,999 records
# matching the query. To obtain more than 9,999 PubMed records, consider using
# EDirect…"* — and, more quietly, a page whose window crosses the boundary is
# clamped to it: `retstart=9500&retmax=500` returned 499 records at HTTP 200
# with no notice. A day larger than this cannot be completed through this
# route at all, so `fetch_pubmed` refuses it on ESearch's count rather than
# walking into the wall. Raising `EFETCH_PAGE_SIZE` does not raise this.
#
# This is a **record count**; the largest legal `retstart` is one less, which
# is what NCBI's own error names. The guard below is `>` rather than `>=` for
# that reason, and a day of exactly this many records is fetchable.
#
# The guard protects the walk against the cap it *knows*. It does not protect
# it against a cap NCBI silently **lowers**: the walk only meets the 400 when
# it requests a page starting past the new limit, so for counts between the
# lowered cap and the next page boundary the straddling page is clamped
# instead, the walk ends naturally, and the shortfall is at most
# `EFETCH_PAGE_SIZE - 1` — under the failure floor, so the day completes on a
# note. `scripts/sample_efetch_paging.py` is what detects a moved cap, in
# either direction; see `docs/DECISIONS.md`.
EFETCH_MAX_RETRIEVABLE = 9999

# The ladder's root. Wide enough that no record of any publication day falls
# outside it — verified per day by the root probe rather than assumed, since a
# record indexed outside it would be in no part's promise and every part would
# then reconcile perfectly while the day was silently short.
EDAT_ROOT_LO = date(1900, 1, 1)
EDAT_ROOT_HI = date(2100, 12, 31)

# Names the partitioning scheme in `download_day_parts.part_scheme`. A stored
# key is compared as a string, so a scheme that changes without this changing
# would match nothing and silently re-fetch every unfinished day.
PART_SCHEME = "edat-range"

RATE_LIMIT_WITH_KEY = 0.1  # seconds between requests with API key
RATE_LIMIT_WITHOUT_KEY = 0.34  # seconds between requests without API key

PMC_BASE_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/"
DOI_BASE_URL = "https://doi.org/"

# Month abbreviation mapping for PubDate parsing
_MONTH_MAP: dict[str, str] = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------


# Inline markup PubMed carries inside titles and abstracts, and the Markdown
# each maps to. Scientific prose depends on these: without ``sub``/``sup`` a
# chemical formula and an exponent both flatten into an ambiguous "CO2" / "m2".
#
# ``u``/``underline`` are deliberately absent, so they fall through to the
# undecorated path below. Markdown has no underline: ``__x__`` is *strong*
# emphasis, so mapping ``<u>`` to it renders underlined text identically to
# ``<b>`` — the same collapse this table exists to prevent for ``sub``/``sup``,
# except that it also asserts something false about the source. Underline is
# presentational, unlike a subscript, so dropping it loses nothing a reader
# needs; claiming it was bold does.
_INLINE_MARKUP: dict[str, tuple[str, str]] = {
    "b": ("**", "**"),
    "bold": ("**", "**"),
    "i": ("*", "*"),
    "italic": ("*", "*"),
    "sup": ("^", "^"),
    "sub": ("~", "~"),
}

# Characters escaped in prose taken from PubMed, so a value that is *declared*
# Markdown cannot be re-read as markup it never carried.
#
# Measured against 3,403 real titles and abstract sections (1,000 records over
# four days): this set alters 12 of them — 0.35% — and removes every construct
# a CommonMark parser found in the unescaped text. The obvious extra
# candidates buy nothing and cost a great deal. Intraword ``_`` is inert in
# CommonMark, so gene names like ``TP53_R175H`` are already safe, and a bare
# ``[...]`` is not a link without a following ``(...)``; escaping both churned
# 4.3% of fields and fixed nothing further.
#
# ``~`` and ``^`` are here because *this module* made them meaningful. A
# literal tilde is the commonest hazard of the three (8 fields to the
# asterisk's 3): "AUC ~ 0.80", "(~88%)", "2.68 ~ 5.42" are ordinary scientific
# prose, and against a Pandoc renderer — the one that reads the ``~2~`` this
# module emits — an unescaped pair silently subscripts everything between
# them. The measured asterisk case is the pharmacogenomic star allele:
# ``CYP2C19 (*1, *2, *3, *17 alleles)`` renders as ``(<em>1, </em>2, ...)``.
_MARKDOWN_SPECIALS = re.compile(r"([\\`*~^])")

# NlmCategory values that mean "this section has no label". Rendering them as
# headings would put the word UNASSIGNED in front of the prose.
_UNLABELLED_CATEGORIES = frozenset({"UNASSIGNED", "UNLABELLED"})


def _text(el: ET.Element | None) -> str | None:
    """Extract text content from an XML element, or return None."""
    if el is None:
        return None
    return el.text


def _text_with_formatting(el: ET.Element | None) -> str:
    """Extract an element's full text, mapping inline markup to Markdown.

    Walks mixed content — an element's own text, each child's text, and the
    tail text following each child — so nothing is lost. Recognised inline tags
    (see :data:`_INLINE_MARKUP`) are wrapped in their Markdown markers;
    an unrecognised tag contributes its text undecorated.

    The result is Markdown, so the prose it is built from is escaped on the way
    in (see :func:`_escape_markdown`) — otherwise declaring the field Markdown
    would itself corrupt values that were fine before, such as the star alleles
    in ``CYP2C19 (*1, *2, *3)``.

    Note this is *not* interchangeable with :func:`_text`, which reads only
    ``el.text``: for any element holding markup that is the text before the
    first child, which truncates the value silently.

    Whitespace at the edge of a formatted run is emitted *outside* that run's
    markers, and stripped only once overall, by the outermost call. Both halves
    matter, and upstream got the first wrong in a way that produced broken
    Markdown rather than merely ugly text: it stripped at every recursion
    level, so the space belonging to ``<b>Randomised </b><b>trial</b>``
    vanished entirely and the runs welded into ``**Randomised****trial**``.
    Keeping the space where it sat is no better — CommonMark requires an
    emphasis delimiter to be adjacent to non-whitespace, so ``**Randomised **``
    does not emphasise either. Moving it out yields
    ``**Randomised** **trial**``.

    Args:
        el: The element to read, or ``None``.

    Returns:
        The element's text with inline markup rendered as Markdown; ``""``
        when *el* is ``None`` or holds no text.
    """
    return _walk_formatting(el).strip()


def _escape_markdown(text: str) -> str:
    """Escape the Markdown-active characters in a run of PubMed prose.

    Applied to text taken from the document, never to the markers this module
    emits — see :data:`_MARKDOWN_SPECIALS` for the set and the measurement
    behind it. Whitespace is untouched, so the caller's lead/trail bookkeeping
    is unaffected.
    """
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)


def _walk_formatting(el: ET.Element | None) -> str:
    """Recursive, non-stripping worker for :func:`_text_with_formatting`.

    Every text node is visited exactly once — an element's own text, then each
    child's subtree, then that child's tail — and escaped as it is read, so the
    markers added around a run are the only unescaped Markdown in the result.
    """
    if el is None:
        return ""

    parts: list[str] = [_escape_markdown(el.text or "")]
    for child in el:
        text = _walk_formatting(child)
        prefix, suffix = _INLINE_MARKUP.get(child.tag.lower(), ("", ""))
        core = text.strip()
        if core:
            # Re-emit the run's edge whitespace outside its markers, so the
            # delimiters stay adjacent to non-whitespace (see the caller's
            # docstring). A run of pure whitespace, or one with no markup,
            # passes through untouched.
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()) :]
            parts.append(f"{lead}{prefix}{core}{suffix}{trail}")
        else:
            # An empty run would otherwise render as stray markers ("****").
            parts.append(text)
        parts.append(_escape_markdown(child.tail or ""))

    return "".join(parts)


def _format_abstract_markdown(abstract_el: ET.Element | None) -> str | None:
    """Render an ``Abstract`` element as Markdown, or return None.

    Each ``AbstractText`` becomes one section. A section's label comes from the
    ``Label`` attribute, falling back to ``NlmCategory`` — PubMed uses either,
    and reading only ``Label`` drops the heading from every section labelled
    the other way, running it into its neighbour. Labels render as
    ``**HEADING:** text``; sections are separated by a blank line so the
    structure survives into Markdown. Inline markup is preserved throughout
    (see :func:`_text_with_formatting`).

    Args:
        abstract_el: The ``Abstract`` element, or ``None``.

    Returns:
        The formatted abstract, or ``None`` when there is no text at all —
        ``FetchedRecord.abstract`` is ``str | None`` and an empty string would
        read as "this paper has a blank abstract" rather than "none was given".
    """
    if abstract_el is None:
        return None

    sections: list[str] = []
    for part in abstract_el.findall("AbstractText"):
        text = _text_with_formatting(part)
        if not text:
            continue

        label = (part.get("Label") or "").strip()
        if not label:
            category = (part.get("NlmCategory") or "").strip()
            if category and category.upper() not in _UNLABELLED_CATEGORIES:
                label = category

        # The label is document text too, so it is escaped like any other run;
        # *text* arrives already escaped from _text_with_formatting.
        sections.append(f"**{_escape_markdown(label.upper())}:** {text}" if label else text)

    return "\n\n".join(sections) or None


def _parse_pubdate(pubdate_el: ET.Element | None) -> str | None:
    """Parse a PubDate element into a YYYY-MM-DD (or partial) date string.

    Handles both numeric months and text abbreviations (e.g. "Jan").
    Returns None if the element is missing or has no Year.
    """
    if pubdate_el is None:
        return None

    year = _text(pubdate_el.find("Year"))
    if year is None:
        # Try MedlineDate as fallback (e.g. "2024 Jan-Feb")
        medline_date = _text(pubdate_el.find("MedlineDate"))
        if medline_date and len(medline_date) >= 4:
            return medline_date[:4]
        return None

    month_el = pubdate_el.find("Month")
    month_text = _text(month_el)
    if month_text is None:
        return year

    # Convert text month to numeric. Accept known abbreviations or numeric
    # months; for anything else (e.g. a season like "Winter"), drop the month
    # rather than emit an invalid date such as "2024-Winter".
    month_key = month_text.lower().strip()[:3]
    stripped = month_text.strip()
    if month_key in _MONTH_MAP:
        month = _MONTH_MAP[month_key]
    elif stripped.isdigit():
        month = stripped.zfill(2)
    else:
        return year

    day_text = _text(pubdate_el.find("Day"))
    if day_text is None:
        return f"{year}-{month}"

    return f"{year}-{month}-{day_text.zfill(2)}"


def _author_name(author_el: ET.Element) -> str | None:
    """Format one ``<Author>`` as ``"Last, Fore"``, or return None.

    Returns ``None`` for an author with no ``LastName`` — a ``<CollectiveName>``
    consortium, which has no personal name to render.
    """
    last = _text(author_el.find("LastName"))
    if not last:
        return None
    fore = _text(author_el.find("ForeName"))
    return f"{last}, {fore}" if fore else last


def _parse_grants(article_el: ET.Element) -> list[Grant]:
    """Extract funding awards from an ``<Article>``'s ``<GrantList>``.

    A grant naming neither an agency nor an award id is skipped: it identifies
    no award, and storing it would put an empty row in front of anyone counting
    a paper's funders.

    Exact repeats are collapsed, keeping first-occurrence order. PubMed really
    does repeat a `<Grant>` block verbatim — 31 of 575 entries across 200
    NIH-funded records, affecting 14 of them — and stored as separate rows
    those inflate every count of a paper's funders, with no way for a reader to
    tell PubMed's repetition from a genuine second award. Two grants differing
    in any field are two grants.

    ``<Acronym>`` — NIH's institute code, e.g. "HL" — is read by neither the
    key nor the row. It is an abbreviation of ``<Agency>`` for one funder
    rather than an independent fact, so two entries alike in agency, id and
    country but differing in acronym are the same award; keeping it out of the
    key is what lets those collapse.
    """
    grants: list[Grant] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for grant_el in article_el.findall("GrantList/Grant"):
        agency = _text(grant_el.find("Agency"))
        grant_id = _text(grant_el.find("GrantID"))
        if not agency and not grant_id:
            continue
        key = (agency, grant_id, _text(grant_el.find("Country")))
        if key in seen:
            continue
        seen.add(key)
        grants.append(Grant(agency=key[0], grant_id=key[1], country=key[2]))
    return grants


def _parse_article_xml(article_el: ET.Element) -> FetchedRecord:
    """Parse a PubmedArticle XML element into a :class:`FetchedRecord`."""
    medline = article_el.find("MedlineCitation")
    article = medline.find("Article") if medline is not None else None
    pubmed_data = article_el.find("PubmedData")

    # PMID
    pmid = _text(medline.find("PMID")) if medline is not None else None

    # Title — read with _text_with_formatting, not _text: a title holding
    # markup (a chemical formula, an italicised species name) is truncated at
    # its first child element by a bare ``el.text`` read.
    title = _text_with_formatting(article.find("ArticleTitle")) if article is not None else None

    # Abstract — Markdown, preserving section labels and inline markup
    abstract: str | None = None
    if article is not None:
        abstract = _format_abstract_markdown(article.find("Abstract"))

    # Authors, and the affiliations they state. Both come from one pass over
    # <AuthorList> so an affiliation's author name is formatted by the same
    # code that builds ``authors`` — the two are meant to be matched, and
    # upstream's separate formatting made that guesswork.
    authors: list[str] = []
    author_affiliations: list[AuthorAffiliation] = []
    if article is not None:
        author_list = article.find("AuthorList")
        if author_list is not None:
            for position, author_el in enumerate(author_list.findall("Author")):
                name = _author_name(author_el)
                if name is None:
                    # A <CollectiveName> consortium. Its affiliations are
                    # dropped with it, deliberately: AuthorAffiliation.author
                    # is contracted to match a name in ``authors``, which this
                    # entry is absent from, so storing it would put a row in
                    # the table that no join by author name can ever reach.
                    continue
                authors.append(name)
                # Deduplicated per author, for the same reason grants are (see
                # _parse_grants) — two authors at one institution each keep it.
                seen_affiliations: set[str] = set()
                for aff_el in author_el.findall("AffiliationInfo/Affiliation"):
                    # Read with the walker, not ``.text``: NLM declares
                    # ``<Affiliation>`` with the same ``(%text;)*`` content
                    # model as ``<ArticleTitle>``, so a superscript footnote
                    # marker truncates the institution — and a *leading* one
                    # makes ``.text`` None, which the guard below then drops.
                    affiliation = _text_with_formatting(aff_el)
                    if affiliation and affiliation not in seen_affiliations:
                        seen_affiliations.add(affiliation)
                        author_affiliations.append(
                            AuthorAffiliation(
                                author=name,
                                affiliation=affiliation,
                                position=position,
                            )
                        )

    # Journal
    journal: str | None = None
    if article is not None:
        journal = _text(article.find("Journal/Title"))

    # Publication date
    pubdate_el = article.find("Journal/JournalIssue/PubDate") if article is not None else None
    publication_date = _parse_pubdate(pubdate_el)

    # DOI and PMC ID from ArticleIdList
    doi: str | None = None
    pmc_id: str | None = None
    if pubmed_data is not None:
        for aid in pubmed_data.findall("ArticleIdList/ArticleId"):
            id_type = aid.get("IdType", "")
            if id_type == "doi":
                doi = aid.text
            elif id_type == "pmc":
                pmc_id = aid.text

    # Keywords from MeSH headings
    keywords: list[str] = []
    if medline is not None:
        for mesh in medline.findall("MeshHeadingList/MeshHeading/DescriptorName"):
            text = mesh.text
            if text:
                keywords.append(text)

    # Publication types — the free Tier 1 quality filter classifies study
    # design from these, so a record without them skips straight to the paid
    # LLM tiers (see bmlib.quality.metadata_filter).
    publication_types: list[str] = []
    if article is not None:
        for ptype in article.findall("PublicationTypeList/PublicationType"):
            text = ptype.text
            if text and text.strip():
                publication_types.append(text.strip())

    # Fulltext sources
    fulltext_sources: list[FullTextSourceEntry] = []
    if pmc_id:
        fulltext_sources.append(
            FullTextSourceEntry(
                url=f"{PMC_BASE_URL}{pmc_id}/",
                source="pmc",
                format="html",
                open_access=True,
            )
        )
    if doi:
        fulltext_sources.append(
            FullTextSourceEntry(
                url=f"{DOI_BASE_URL}{doi}",
                source="publisher",
                format="html",
                open_access=False,
            )
        )

    return FetchedRecord(
        title=title or "",
        source="pubmed",
        doi=doi,
        pmid=pmid,
        pmc_id=pmc_id,
        abstract=abstract,
        authors=authors,
        journal=journal,
        publication_date=publication_date,
        keywords=keywords,
        publication_types=publication_types,
        fulltext_sources=fulltext_sources,
        grants=_parse_grants(article) if article is not None else [],
        author_affiliations=author_affiliations,
    )


# ---------------------------------------------------------------------------
# E-utilities helpers
# ---------------------------------------------------------------------------


def _day_term(target_date: date) -> str:
    """Return the ESearch term for one publication day.

    ``[Date - Publication]`` rather than ``[EDAT]`` is deliberate and load-bearing:
    it is the field bmlib syncs by, and the two disagree by orders of magnitude on
    exactly the days this module has to handle (see ``docs/DECISIONS.md``).
    """
    return f'("{target_date:%Y/%m/%d}"[Date - Publication])'


def _esearch(
    client: Any,
    term: str,
    api_key: str | None,
    *,
    usehistory: bool = True,
) -> tuple[int, str | None, str | None]:
    """Run an ESearch query for *term* and return (count, web_env, query_key).

    *usehistory* is ``False`` for the ladder's counting probes, which need a
    number and not a session; opening one per probe would leave dozens of
    unused sessions on NCBI's server for every over-cap day.

    Returns (0, None, None) when the search yields no results.

    Raises:
        ValueError: If the response carries no usable ``<Count>``. NCBI
            answers a bad request — an unknown db, an invalid term, a
            throttled key — with HTTP 200 and an ``<ERROR>`` document that
            has no ``<Count>`` at all, so treating an absent element as
            zero would report a rejected search as a day with no
            publications. Raised rather than returned so the caller's
            existing handler turns it into a ``failed`` fetch.
    """
    params: dict[str, str | int] = {"db": "pubmed", "term": term, "retmax": 0}
    if usehistory:
        params["usehistory"] = "y"
    if api_key:
        params["api_key"] = api_key

    response = client.get(ESEARCH_URL, params=params)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    # `_text()` returns None both for an absent element and for an empty one,
    # so `or "0"` would collapse "NCBI rejected the search" into "the day was
    # quiet" — the same silent failure the WebEnv/QueryKey guard below exists
    # to prevent, reached one step earlier and past that guard, since an
    # <ERROR> document carries no session either.
    raw_count = _text(root.find("Count"))
    if raw_count is None or not raw_count.strip().isdigit():
        error = _text(root.find("ERROR")) or _text(root.find("ErrorList"))
        raise ValueError(
            f"esearch returned no usable <Count>{f' (NCBI said: {error})' if error else ''}"
        )
    count = int(raw_count)
    web_env = _text(root.find("WebEnv"))
    query_key = _text(root.find("QueryKey"))

    return count, web_env, query_key


class _EFetchPage(NamedTuple):
    """One efetch page: what will be parsed, and what was delivered.

    A plain ``tuple[list[ET.Element], int]`` invites the one misreading this
    pair exists to prevent — that the count is the list's length. It is not,
    and the gap between them is the whole point.
    """

    articles: list[ET.Element]
    """``<PubmedArticle>`` elements, the only kind the fetcher parses."""

    delivered: int
    """Record elements the server handed over — never ``len(articles)``."""


def _efetch_page(
    client: Any,
    web_env: str,
    query_key: str,
    retstart: int,
    api_key: str | None,
) -> _EFetchPage:
    """Fetch one page from EFetch and return its articles and delivery count.

    *delivered* counts every record element the server handed over, which is
    not the same as the length of the returned list: a ``<PubmedBookArticle>``
    is delivered and deliberately not parsed. The caller reconciles delivery
    against esearch's count, and counting parsed records there would report a
    phantom shortfall for every day carrying a book chapter.

    It counts the two record elements by name rather than taking every child
    of the set. ``<DeleteCitation>`` is also a legal child, and counting it as
    a delivery is wrong in the expensive direction twice over: it inflates
    delivery so a real shortfall clears the floor, and — because the caller's
    stall rule is ``delivered == 0`` — a page carrying nothing but one of them
    stops looking like the stall it is.

    Raises:
        ValueError: If the response is not a ``PubmedArticleSet``. NCBI answers
            an expired or evicted history session with
            ``<eFetchResult><ERROR>…</ERROR></eFetchResult>`` at **HTTP 200**,
            so ``raise_for_status()`` never fires and ``findall`` returns an
            empty list — a rejected page wearing the shape of an exhausted one.
            Refused here for the reason ``_esearch`` refuses a response with no
            ``<Count>``, and raised so the caller's handler marks the day failed.
    """
    params: dict[str, str | int] = {
        "db": "pubmed",
        "query_key": query_key,
        "WebEnv": web_env,
        "retstart": retstart,
        "retmax": EFETCH_PAGE_SIZE,
        "retmode": "xml",
    }
    if api_key:
        params["api_key"] = api_key

    response = client.get(EFETCH_URL, params=params)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    if root.tag != "PubmedArticleSet":
        error = _text(root.find("ERROR")) or _text(root.find(".//ERROR"))
        raise ValueError(
            f"efetch returned <{root.tag}> rather than <PubmedArticleSet>"
            f"{f' (NCBI said: {error})' if error else ''}"
        )
    articles = list(root.findall("PubmedArticle"))
    return _EFetchPage(
        articles=articles,
        delivered=len(articles) + len(root.findall("PubmedBookArticle")),
    )


class _WalkOutcome(NamedTuple):
    """What one history session's page walk produced."""

    processed: int
    """Records parsed and handed to ``on_record``."""

    delivered: int
    """Record elements the server handed over — never ``processed``."""

    stalled: bool
    """A page delivered nothing while *promised* was still unmet."""

    error: str | None
    """Set when a page raised; the walk stops and the caller fails the day."""


def _walk_session(
    client: Any,
    web_env: str,
    query_key: str,
    promised: int,
    *,
    on_record: Callable[[FetchedRecord], None],
    api_key: str | None,
    rate_limit: float,
    on_page: Callable[[int], None] | None = None,
) -> _WalkOutcome:
    """Walk one history session's pages, parsing every article into *on_record*.

    ``retstart`` indexes the *session's UID list*, not the records delivered so
    far: page k covers the UIDs at [k·EFETCH_PAGE_SIZE, (k+1)·EFETCH_PAGE_SIZE)
    whether or not every one of them yields a record — named for the constant
    that actually strides, since the claim holds only while the walk's step
    and `_efetch_page`'s `retmax` stay equal. Measured 2026-08-20 (issue #96): a
    page's record elements are exactly that slice of esearch's own
    `IdList`, in order, `<PubmedBookArticle>` entries included. So the
    stride stays `EFETCH_PAGE_SIZE`. Advancing by what arrived — the fix
    #96 proposed — would re-request the tail of every short page, deliver
    those records twice, and count the duplicates as delivery, which is
    precisely what would hide a real shortfall from `reconcile_delivery`.

    *on_page* is called with the running processed count after each page, for
    progress reporting; it is not called after a stalled or failed page.
    """
    processed = 0
    delivered = 0
    for retstart in range(0, promised, EFETCH_PAGE_SIZE):
        try:
            page = _efetch_page(client, web_env, query_key, retstart, api_key)
        except Exception as exc:
            logger.error("efetch failed at retstart=%d: %s: %s", retstart, type(exc).__name__, exc)
            return _WalkOutcome(processed, delivered, False, f"{type(exc).__name__}: {exc}")

        delivered += page.delivered
        for article_el in page.articles:
            on_record(_parse_article_xml(article_el))
            processed += 1

        if page.delivered == 0:
            # The session holds `promised` UIDs, so an empty page before the
            # walk is done means it stopped serving them. Paging on costs a
            # request per remaining page and returns nothing — up to 9 of
            # them on the 5,000-record day measured for #88, which is 10
            # pages of 500.
            return _WalkOutcome(processed, delivered, True, None)

        if on_page is not None:
            on_page(processed)

        if retstart + EFETCH_PAGE_SIZE < promised:
            time.sleep(rate_limit)

    return _WalkOutcome(processed, delivered, False, None)


# ---------------------------------------------------------------------------
# The EDAT ladder: planning an over-cap day as Entrez-date ranges (#105)
# ---------------------------------------------------------------------------


class _Partition(NamedTuple):
    """One Entrez-date range of a day, small enough to fetch in one session."""

    lo: date
    hi: date
    promised: int

    @property
    def key(self) -> str:
        """This part's identity in ``download_day_parts``."""
        return _part_key(self.lo, self.hi)


class _UnsplittableDay(Exception):  # noqa: N818 — name is a fixed cross-task interface, not public
    """A single Entrez day holds more records than one session can serve."""

    def __init__(self, edat_day: date, count: int) -> None:
        self.edat_day = edat_day
        self.count = count
        super().__init__(
            f"{count} records share the Entrez date {edat_day.isoformat()}, above the"
            f" {EFETCH_MAX_RETRIEVABLE} a history session serves, and an Entrez date"
            " cannot be split further; refusing the day"
        )


class _RootNotCovering(Exception):  # noqa: N818 — name is a fixed cross-task interface, not public
    """Records of this day lie outside the ladder's root range."""


def _part_key(lo: date, hi: date) -> str:
    """Return the stored identity of the partition spanning *lo* to *hi*.

    The one constructor, because the resume skip rule compares this string:
    a second spelling of the same range matches no checkpoint, so resume
    degrades to a full re-fetch with nothing raised. Pinned by a test.

    Args:
        lo: The range's first (inclusive) Entrez date.
        hi: The range's last (inclusive) Entrez date.

    Returns:
        The ``download_day_parts.part_scheme``-scoped key for this range.
    """
    return f"edat:{lo.isoformat()}:{hi.isoformat()}"


def _edat_range_term(day_term: str, lo: date, hi: date) -> str:
    """Restrict *day_term* to records indexed between *lo* and *hi* inclusive.

    Args:
        day_term: The day's own ``[Date - Publication]`` search term, as built
            by :func:`_day_term`.
        lo: The range's first (inclusive) Entrez date.
        hi: The range's last (inclusive) Entrez date.

    Returns:
        The combined ESearch term.
    """
    return f'{day_term} AND ("{lo:%Y/%m/%d}"[EDAT] : "{hi:%Y/%m/%d}"[EDAT])'


def _plan_partitions(
    count_fn: Callable[[str], int],
    day_term: str,
    day_count: int,
    *,
    lo: date = EDAT_ROOT_LO,
    hi: date = EDAT_ROOT_HI,
    probe_root: bool = True,
) -> list[_Partition]:
    """Split a day into Entrez-date ranges that each fit in one session.

    ``[lo, mid]`` and ``[mid+1, hi]`` tile ``[lo, hi]`` as arithmetic and every
    record carries exactly one Entrez date, so the parts are disjoint and
    covering by construction — below the root. At the root that is an empirical
    claim, so *probe_root* verifies it.

    Only the left child is counted; the right is the parent's count minus it,
    which the tiling makes exact and which halves the ladder's cost.

    Args:
        count_fn: Returns the record count for an arbitrary ESearch term —
            injected so this is unit-testable without HTTP.
        day_term: The day's own ``[Date - Publication]`` search term.
        day_count: The day's own record count, from the caller's day-level
            ESearch. Used only to validate the root probe.
        lo: The ladder's root range start. Defaults to :data:`EDAT_ROOT_LO`.
        hi: The ladder's root range end. Defaults to :data:`EDAT_ROOT_HI`.
        probe_root: Whether to verify the root range covers *day_count*
            before descending. Tests that construct an already-narrow root
            pass ``False`` to skip the probe.

    Returns:
        The day's partitions, each with ``promised <= EFETCH_MAX_RETRIEVABLE``
        and holding at least one record, sorted by construction into disjoint,
        covering ranges.

    Raises:
        _RootNotCovering: The root range holds fewer records than the day does,
            so some record of the day is indexed outside it. Coming up *long*
            is benign — the two counts are two requests at two instants, and a
            record indexed between them lands at EDAT=today, inside the range.
        _UnsplittableDay: A single Entrez date exceeds the cap.
    """
    root_count = count_fn(_edat_range_term(day_term, lo, hi))
    if probe_root and root_count < day_count:
        raise _RootNotCovering(
            f"the Entrez-date range {lo.isoformat()}..{hi.isoformat()} holds {root_count}"
            f" of this day's {day_count} records, so {day_count - root_count} of them lie"
            " outside the ladder and would be silently absent; refusing the day"
        )

    parts: list[_Partition] = []

    def descend(lo_: date, hi_: date, n: int) -> None:
        if n <= 0:
            return
        if n <= EFETCH_MAX_RETRIEVABLE:
            parts.append(_Partition(lo_, hi_, n))
            return
        if lo_ == hi_:
            raise _UnsplittableDay(lo_, n)
        mid = lo_ + (hi_ - lo_) // 2
        left = count_fn(_edat_range_term(day_term, lo_, mid))
        descend(lo_, mid, left)
        descend(mid + timedelta(days=1), hi_, n - left)

    descend(lo, hi, root_count)
    return parts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_pubmed(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    api_key: str | None = None,
) -> FetchResult:
    """Fetch all PubMed articles published on *target_date*.

    Parameters
    ----------
    client:
        An httpx-compatible HTTP client (must support ``client.get(url, params=...)``).
    target_date:
        The publication date to query for.
    on_record:
        Callback invoked with each parsed :class:`FetchedRecord`.
    on_progress:
        Optional callback invoked after each page with a :class:`SyncProgress`.
    api_key:
        Optional NCBI API key for higher rate limits.

    Returns
    -------
    FetchResult
        Summary of the fetch operation.
    """
    date_str = target_date.isoformat()
    rate_limit = RATE_LIMIT_WITH_KEY if api_key else RATE_LIMIT_WITHOUT_KEY

    day_term = _day_term(target_date)
    try:
        count, web_env, query_key = _esearch(client, day_term, api_key)
    except Exception as exc:
        logger.error("esearch failed for %s: %s", date_str, exc)
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=0,
            status="failed",
            error=str(exc),
        )

    if count == 0:
        logger.info("No PubMed records for %s", date_str)
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=0,
            status="completed",
        )

    if web_env is None or query_key is None:
        # ESearch is sent `usehistory=y` and every efetch page reads the
        # session back. Without it each page asks NCBI for `WebEnv=` (httpx
        # encodes `None` as an empty parameter) and gets a document holding
        # no `PubmedArticle`, so an unguarded fetch walks the entire count
        # in useless requests — 10 of them for a 5,000-record day — and then
        # reports `completed` with 0 records: a broken fetch wearing the
        # shape of a quiet day.
        message = f"esearch returned count={count} without a history session (WebEnv/QueryKey)"
        logger.error("%s for %s", message, date_str)
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=0,
            status="failed",
            error=message,
        )

    if count > EFETCH_MAX_RETRIEVABLE:
        # The day cannot be completed through this route, so it is refused
        # before a single record is fetched rather than after the reachable
        # ones are. Both readings end with the day recorded `failed` and
        # re-offered on every later run — it can never be `completed`, since
        # that would durably lose the remainder — so the only question is what
        # the doomed run costs. Walking first would buy the first
        # `EFETCH_MAX_RETRIEVABLE` records once and then re-fetch them on every
        # run for the life of the installation: measured against real days,
        # every first-of-month is over the cap (49,543-90,571 records, since a
        # record carrying only a year and month is indexed at day 1) and every
        # 1 January is 212,439-315,282, so a six-year backfill carries some 72
        # such days and would re-download ~3 GB per run to store nothing new.
        # Refusing costs one esearch instead. The records are not written off:
        # issue #105 partitions an over-cap day into sub-queries that each fit,
        # which is what makes "no publication is missed" true again.
        #
        # "a history session serves" rather than "efetch serves": the limit is
        # the search backend's, which is how NCBI's own 400 names it and how
        # every doc describing this refers to it. An operator grepping the
        # docs for the phrase in their log has to find it.
        message = (
            f"PubMed reported {count} records for {date_str}, but a history session serves"
            f" only its first {EFETCH_MAX_RETRIEVABLE} records, so"
            f" {count - EFETCH_MAX_RETRIEVABLE} of them cannot be reached; refusing the day"
            " rather than storing part of it as though it were whole. This day is refused"
            " on every run until issue #105 lands; no operator action is available"
        )
        logger.error("%s", message)
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=0,
            status="failed",
            error=message,
        )

    logger.info("PubMed esearch: %d records for %s", count, date_str)

    def _report(processed: int) -> None:
        if on_progress is not None:
            on_progress(
                SyncProgress(
                    source="pubmed",
                    date=date_str,
                    records_processed=processed,
                    records_total=count,
                    status="in_progress",
                    message=f"Fetched {processed}/{count} records",
                )
            )

    outcome = _walk_session(
        client,
        web_env,
        query_key,
        count,
        on_record=on_record,
        api_key=api_key,
        rate_limit=rate_limit,
        on_page=_report,
    )
    records_processed = outcome.processed
    if outcome.error is not None:
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=records_processed,
            status="failed",
            error=outcome.error,
        )

    verdict = reconcile_delivery(
        "pubmed",
        date_str,
        delivered=outcome.delivered,
        promised=count,
        stalled=outcome.stalled,
    )
    if verdict.failure is not None:
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=records_processed,
            status="failed",
            error=verdict.failure,
        )

    return FetchResult(
        source="pubmed",
        date=date_str,
        record_count=records_processed,
        status="completed",
        note=verdict.note,
    )
