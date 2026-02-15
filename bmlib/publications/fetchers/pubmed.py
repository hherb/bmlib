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
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.publications.models import FetchResult, FetchedRecord, SyncProgress

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


def _text(el: ET.Element | None) -> str | None:
    """Extract text content from an XML element, or return None."""
    if el is None:
        return None
    return el.text


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

    # Convert text month to numeric
    month = _MONTH_MAP.get(month_text.lower().strip()[:3], month_text.zfill(2))

    day_text = _text(pubdate_el.find("Day"))
    if day_text is None:
        return f"{year}-{month}"

    return f"{year}-{month}-{day_text.zfill(2)}"


def _parse_article_xml(article_el: ET.Element) -> FetchedRecord:
    """Parse a PubmedArticle XML element into a :class:`FetchedRecord`."""
    medline = article_el.find("MedlineCitation")
    article = medline.find("Article") if medline is not None else None
    pubmed_data = article_el.find("PubmedData")

    # PMID
    pmid = _text(medline.find("PMID")) if medline is not None else None

    # Title
    title = _text(article.find("ArticleTitle")) if article is not None else None

    # Abstract — join multiple AbstractText parts
    abstract: str | None = None
    if article is not None:
        abstract_el = article.find("Abstract")
        if abstract_el is not None:
            parts = []
            for part in abstract_el.findall("AbstractText"):
                label = part.get("Label")
                # itertext() captures mixed content (text + child elements)
                text = "".join(part.itertext()).strip()
                if text:
                    if label:
                        parts.append(f"{label}: {text}")
                    else:
                        parts.append(text)
            if parts:
                abstract = "\n".join(parts)

    # Authors
    authors: list[str] = []
    if article is not None:
        author_list = article.find("AuthorList")
        if author_list is not None:
            for author_el in author_list.findall("Author"):
                last = _text(author_el.find("LastName"))
                fore = _text(author_el.find("ForeName"))
                if last and fore:
                    authors.append(f"{last}, {fore}")
                elif last:
                    authors.append(last)

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

    # Fulltext sources
    fulltext_sources: list[dict[str, str]] = []
    if pmc_id:
        fulltext_sources.append(
            {"url": f"{PMC_BASE_URL}{pmc_id}/", "source": "pmc", "format": "html"}
        )
    if doi:
        fulltext_sources.append(
            {"url": f"{DOI_BASE_URL}{doi}", "source": "publisher", "format": "html"}
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
        fulltext_sources=fulltext_sources,
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
    on_record: Callable[[dict], None],
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
        Callback invoked with each parsed article dict.
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
