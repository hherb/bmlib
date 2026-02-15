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

"""Fetcher for bioRxiv and medRxiv preprint records.

Uses the bioRxiv API (https://api.biorxiv.org) to retrieve preprint metadata
for a given date.  The same endpoint serves both bioRxiv and medRxiv data,
controlled by the ``server`` parameter.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.models import FetchResult, FetchedRecord, SyncProgress

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://api.biorxiv.org/details"
PAGE_SIZE = 100
RATE_LIMIT_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalize(raw: dict[str, Any], server: str) -> FetchedRecord:
    """Convert a raw bioRxiv/medRxiv API record to a :class:`FetchedRecord`."""
    doi = raw.get("doi", "")
    authors_raw = raw.get("authors", "")
    authors = [a.strip() for a in authors_raw.split(";") if a.strip()] if authors_raw else []

    # Build full-text sources
    fulltext_sources: list[FullTextSourceEntry] = []

    # PDF URL derived from DOI
    if doi:
        pdf_url = f"https://www.{server}.org/content/{doi}v1.full.pdf"
        fulltext_sources.append(FullTextSourceEntry(
            url=pdf_url, format="pdf", source=server, open_access=True,
        ))

    # JATS XML URL from the record
    jatsxml = raw.get("jatsxml", "")
    if jatsxml:
        fulltext_sources.append(FullTextSourceEntry(
            url=jatsxml, format="xml", source=server, open_access=True,
        ))

    return FetchedRecord(
        title=raw.get("title", ""),
        source=server,
        doi=doi or None,
        abstract=raw.get("abstract", ""),
        authors=authors,
        publication_date=raw.get("date", ""),
        is_open_access=True,
        fulltext_sources=fulltext_sources,
        extras={
            "category": raw.get("category", ""),
            "published": raw.get("published", ""),
            "server": raw.get("server", server),
        },
    )


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


def fetch_biorxiv(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    server: str = "biorxiv",
    api_key: str | None = None,
) -> FetchResult:
    """Fetch preprint records from the bioRxiv/medRxiv API for a single date.

    Parameters
    ----------
    client:
        An HTTP client with a ``get(url)`` method that returns a response
        object with ``.status_code`` (int), ``.json()`` (dict), and
        ``.raise_for_status()`` methods (e.g. ``httpx.Client``).
    target_date:
        The date to fetch records for.
    on_record:
        Callback invoked with each normalised record dict.
    on_progress:
        Optional callback invoked after each page to report progress.
    server:
        ``"biorxiv"`` (default) or ``"medrxiv"``.
    api_key:
        Unused; reserved for future API authentication.

    Returns
    -------
    FetchResult
        Summary of the fetch operation.
    """
    date_str = target_date.isoformat()
    cursor = 0
    total_fetched = 0
    records_total: int | None = None

    try:
        while True:
            url = f"{BASE_URL}/{server}/{date_str}/{date_str}/{cursor}"
            response = client.get(url)
            response.raise_for_status()

            data = response.json()
            collection = data.get("collection", [])

            # Extract total from first page's messages
            if records_total is None:
                messages = data.get("messages", [])
                if messages:
                    records_total = int(messages[0].get("total", 0))

            if not collection:
                break

            for raw_record in collection:
                normalized = _normalize(raw_record, server)
                on_record(normalized)
                total_fetched += 1

            # Report progress after each page
            if on_progress is not None:
                on_progress(
                    SyncProgress(
                        source=server,
                        date=date_str,
                        records_processed=total_fetched,
                        records_total=records_total or total_fetched,
                        status="in_progress",
                    )
                )

            # Stop if this was the last page
            if len(collection) < PAGE_SIZE:
                break

            cursor += PAGE_SIZE
            time.sleep(RATE_LIMIT_SECONDS)

    except Exception as exc:
        return FetchResult(
            source=server,
            date=date_str,
            record_count=total_fetched,
            status="failed",
            error=str(exc),
        )

    return FetchResult(
        source=server,
        date=date_str,
        record_count=total_fetched,
        status="completed",
    )
