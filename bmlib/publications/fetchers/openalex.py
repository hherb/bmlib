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

"""OpenAlex fetcher — retrieves publication records from the OpenAlex API.

Uses cursor-based pagination to walk through all works published on a given
date.  Each raw record is normalised into the common publication dict format
before being handed to the *on_record* callback.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.publications.models import FetchResult, SyncProgress

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_URL = "https://api.openalex.org/works"
_PER_PAGE = 200
_RATE_LIMIT_SECONDS = 0.1
_DOI_PREFIX = "https://doi.org/"
_PMID_PREFIX = "https://pubmed.ncbi.nlm.nih.gov/"

_VERSION_MAP: dict[str, str] = {
    "publishedVersion": "published",
    "acceptedVersion": "accepted",
    "submittedVersion": "preprint",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct an abstract from OpenAlex's inverted-index representation.

    OpenAlex stores abstracts as ``{"word": [pos, ...], ...}``.  We flatten
    this into a list of ``(position, word)`` pairs, sort by position, and join.

    Returns ``None`` when *inverted_index* is ``None`` or empty.
    """
    if not inverted_index:
        return None

    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pairs.append((pos, word))

    if not pairs:
        return None

    pairs.sort(key=lambda p: p[0])
    return " ".join(word for _, word in pairs)


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw OpenAlex work record to the common publication format."""
    # DOI — strip prefix
    doi_raw = raw.get("doi") or ""
    doi = doi_raw.removeprefix(_DOI_PREFIX) if doi_raw else None
    if doi == "":
        doi = None

    # PMID — extract from ids dict
    pmid = None
    ids = raw.get("ids") or {}
    pmid_raw = ids.get("pmid") or ""
    if pmid_raw:
        pmid = pmid_raw.removeprefix(_PMID_PREFIX)
        if pmid == "":
            pmid = None

    # Authors
    authors = []
    for authorship in raw.get("authorships") or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)

    # Journal
    journal = None
    primary_location = raw.get("primary_location") or {}
    source = primary_location.get("source") or {}
    journal = source.get("display_name")

    # Abstract
    abstract = _reconstruct_abstract(raw.get("abstract_inverted_index"))

    # Keywords — primary topic display name
    keywords: list[str] = []
    primary_topic = raw.get("primary_topic") or {}
    topic_name = primary_topic.get("display_name")
    if topic_name:
        keywords.append(topic_name)

    # Open access
    oa_info = raw.get("open_access") or {}
    is_open_access = bool(oa_info.get("is_oa", False))

    # License
    license_value = raw.get("license")

    # Publication types
    publication_types: list[str] = []
    work_type = raw.get("type")
    if work_type:
        publication_types.append(work_type)

    # Fulltext sources from locations
    fulltext_sources: list[dict[str, Any]] = []
    for location in raw.get("locations") or []:
        loc_source = (location.get("source") or {}).get("display_name") or "unknown"
        version_raw = location.get("version") or ""
        version = _VERSION_MAP.get(version_raw, version_raw) if version_raw else None

        landing_url = location.get("landing_page_url")
        if landing_url:
            fulltext_sources.append(
                {
                    "source": loc_source,
                    "url": landing_url,
                    "format": "html",
                    "version": version,
                }
            )

        pdf_url = location.get("pdf_url")
        if pdf_url:
            fulltext_sources.append(
                {
                    "source": loc_source,
                    "url": pdf_url,
                    "format": "pdf",
                    "version": version,
                }
            )

    return {
        "title": raw.get("title") or "",
        "doi": doi,
        "pmid": pmid,
        "authors": authors,
        "journal": journal,
        "abstract": abstract,
        "publication_date": raw.get("publication_date"),
        "keywords": keywords,
        "is_open_access": is_open_access,
        "license": license_value,
        "publication_types": publication_types,
        "fulltext_sources": fulltext_sources,
        "source": "openalex",
    }


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------


def fetch_openalex(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    email: str = "user@example.com",
    api_key: str | None = None,
) -> FetchResult:
    """Fetch all OpenAlex works published on *target_date*.

    Parameters
    ----------
    client:
        An httpx-compatible client with a ``.get()`` method.
    target_date:
        The publication date to query.
    on_record:
        Called once per normalised record dict.
    on_progress:
        Optional callback receiving :class:`SyncProgress` updates.
    email:
        Polite-pool email sent as ``mailto`` parameter.
    api_key:
        Optional OpenAlex API key for premium access.

    Returns
    -------
    FetchResult
        Summary of the fetch operation.
    """
    date_str = target_date.isoformat()
    cursor = "*"
    records_processed = 0
    records_total = 0
    is_first_page = True

    while cursor is not None:
        params: dict[str, Any] = {
            "filter": f"from_publication_date:{date_str},to_publication_date:{date_str}",
            "per_page": _PER_PAGE,
            "cursor": cursor,
            "mailto": email,
        }
        if api_key:
            params["api_key"] = api_key

        try:
            response = client.get(_API_URL, params=params)
            response.raise_for_status()
        except Exception as exc:
            return FetchResult(
                source="openalex",
                date=date_str,
                record_count=records_processed,
                status="failed",
                error=str(exc),
            )

        data = response.json()

        if is_first_page:
            records_total = (data.get("meta") or {}).get("count", 0)
            is_first_page = False

        results = data.get("results") or []
        for raw in results:
            normalised = _normalize(raw)
            on_record(normalised)
            records_processed += 1

        if on_progress is not None:
            on_progress(
                SyncProgress(
                    source="openalex",
                    date=date_str,
                    records_processed=records_processed,
                    records_total=records_total,
                    status="in_progress",
                )
            )

        cursor = data.get("meta", {}).get("next_cursor")

        # Respect rate limit between paginated requests
        if cursor is not None:
            time.sleep(_RATE_LIMIT_SECONDS)

    return FetchResult(
        source="openalex",
        date=date_str,
        record_count=records_processed,
        status="ok",
    )
