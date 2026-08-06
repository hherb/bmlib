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
from datetime import date
from typing import Any

from bmlib.fulltext.models import FullTextSourceEntry
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


def _esearch(
    client: Any,
    target_date: date,
    api_key: str | None,
) -> tuple[int, str | None, str | None]:
    """Run an ESearch query and return (count, web_env, query_key).

    Returns (0, None, None) when the search yields no results.
    """
    date_str = target_date.strftime("%Y/%m/%d")
    params: dict[str, str | int] = {
        "db": "pubmed",
        "term": f'("{date_str}"[Date - Publication])',
        "retmax": 0,
        "usehistory": "y",
    }
    if api_key:
        params["api_key"] = api_key

    response = client.get(ESEARCH_URL, params=params)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    count = int(_text(root.find("Count")) or "0")
    web_env = _text(root.find("WebEnv"))
    query_key = _text(root.find("QueryKey"))

    return count, web_env, query_key


def _efetch_page(
    client: Any,
    web_env: str,
    query_key: str,
    retstart: int,
    api_key: str | None,
) -> list[ET.Element]:
    """Fetch one page of PubmedArticle XML elements from EFetch."""
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
    return list(root.findall("PubmedArticle"))


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

    try:
        count, web_env, query_key = _esearch(client, target_date, api_key)
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

    logger.info("PubMed esearch: %d records for %s", count, date_str)

    records_processed = 0
    for retstart in range(0, count, EFETCH_PAGE_SIZE):
        try:
            articles = _efetch_page(client, web_env, query_key, retstart, api_key)
        except Exception as exc:
            logger.error("efetch failed at retstart=%d: %s", retstart, exc)
            return FetchResult(
                source="pubmed",
                date=date_str,
                record_count=records_processed,
                status="failed",
                error=str(exc),
            )

        for article_el in articles:
            record = _parse_article_xml(article_el)
            on_record(record)
            records_processed += 1

        if on_progress is not None:
            on_progress(
                SyncProgress(
                    source="pubmed",
                    date=date_str,
                    records_processed=records_processed,
                    records_total=count,
                    status="in_progress",
                    message=f"Fetched {records_processed}/{count} records",
                )
            )

        # Rate-limit between pages (skip after the last page)
        if retstart + EFETCH_PAGE_SIZE < count:
            time.sleep(rate_limit)

    return FetchResult(
        source="pubmed",
        date=date_str,
        record_count=records_processed,
        status="completed",
    )
