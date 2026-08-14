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

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.fetchers._reconcile import reconcile_delivery
from bmlib.publications.models import FetchedRecord, FetchResult, SyncProgress

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


def _normalize(raw: dict[str, Any]) -> FetchedRecord:
    """Convert a raw OpenAlex work record to a :class:`FetchedRecord`."""
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
    fulltext_sources: list[FullTextSourceEntry] = []
    for location in raw.get("locations") or []:
        loc_source = (location.get("source") or {}).get("display_name") or "unknown"
        version_raw = location.get("version") or ""
        version = _VERSION_MAP.get(version_raw, version_raw) if version_raw else None
        loc_is_oa = bool(location.get("is_oa", False))

        landing_url = location.get("landing_page_url")
        if landing_url:
            fulltext_sources.append(
                FullTextSourceEntry(
                    url=landing_url,
                    format="html",
                    source=loc_source,
                    open_access=loc_is_oa,
                    version=version,
                )
            )

        pdf_url = location.get("pdf_url")
        if pdf_url:
            fulltext_sources.append(
                FullTextSourceEntry(
                    url=pdf_url,
                    format="pdf",
                    source=loc_source,
                    open_access=loc_is_oa,
                    version=version,
                )
            )

    return FetchedRecord(
        title=raw.get("title") or "",
        source="openalex",
        doi=doi,
        pmid=pmid,
        abstract=abstract,
        authors=authors,
        journal=journal,
        publication_date=raw.get("publication_date"),
        keywords=keywords,
        publication_types=publication_types,
        is_open_access=is_open_access,
        license=license_value,
        fulltext_sources=fulltext_sources,
    )


def _failed(date_str: str, records_processed: int, message: str) -> FetchResult:
    """Build a failed :class:`FetchResult`, keeping whatever the walk delivered.

    ``record_count`` is what arrived before the failure, not zero: those
    records were already handed to ``on_record`` and will be stored, and the
    day is retried regardless.
    """
    return FetchResult(
        source="openalex",
        date=date_str,
        record_count=records_processed,
        status="failed",
        error=message,
    )


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------


def fetch_openalex(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    email: str,
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
        Called once per normalised :class:`FetchedRecord`.
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
    # `str | None`, not `str`: "*" only seeds the first page, and the loop
    # below exits on the `None` the last page's `next_cursor` returns.
    cursor: str | None = "*"
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
            # Inside the guard, not after it (#91): a malformed body is a
            # failure of this HTTP call, and decoding it outside meant the
            # exception escaped the fetcher entirely and was logged by
            # ``sync()`` as "Fetcher raised" — pointing at the wrong layer.
            data = response.json()
        except Exception as exc:
            return _failed(date_str, records_processed, str(exc))

        # OpenAlex answers an invalid query with an ``{"error": ...}`` body at
        # HTTP 200. Read through ``.get()`` defaults, such a body is
        # indistinguishable from a day with no works — which is how a rejected
        # query came to be stored as a completed day (#88). The envelope is
        # therefore checked rather than defaulted, in the order that lets a
        # page's valid records still be emitted before the page is refused.
        if not isinstance(data, dict):
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a {type(data).__name__} payload, not an object, for {date_str}",
            )

        results = data.get("results")
        if not isinstance(results, list):
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a page carrying no results list for {date_str}",
            )

        for raw in results:
            normalised = _normalize(raw)
            on_record(normalised)
            records_processed += 1

        meta = data.get("meta")
        if not isinstance(meta, dict):
            return _failed(
                date_str,
                records_processed,
                f"OpenAlex returned a page carrying no meta object for {date_str}",
            )

        if is_first_page:
            # ``bool`` is an ``int``, but a count is never sent as one; the
            # check exists for the error bodies that send no count at all.
            if not isinstance(meta.get("count"), int):
                return _failed(
                    date_str,
                    records_processed,
                    f"OpenAlex returned a page whose meta carries no numeric count for {date_str}",
                )
            records_total = meta["count"]
            is_first_page = False

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

        cursor = meta.get("next_cursor")

        # Respect rate limit between paginated requests
        if cursor is not None:
            time.sleep(_RATE_LIMIT_SECONDS)

    shortfall = reconcile_delivery(
        "openalex",
        date_str,
        delivered=records_processed,
        promised=records_total,
    )
    if shortfall is not None:
        return _failed(date_str, records_processed, shortfall)

    return FetchResult(
        source="openalex",
        date=date_str,
        record_count=records_processed,
        status="completed",
    )
