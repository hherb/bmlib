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

"""Sync orchestrator — fetches publications from multiple sources and stores them.

Coordinates fetchers, deduplication, and download-day tracking across all
configured publication sources (PubMed, bioRxiv, medRxiv, OpenAlex).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from bmlib.db import execute, fetch_all
from bmlib.publications.fetchers import ALL_SOURCES
from bmlib.publications.models import (
    FullTextSource,
    Publication,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import store_publication

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_fetchers() -> dict[str, Callable]:
    """Lazily load real fetcher functions to avoid import-time side effects."""
    from bmlib.publications.fetchers.biorxiv import fetch_biorxiv
    from bmlib.publications.fetchers.openalex import fetch_openalex
    from bmlib.publications.fetchers.pubmed import fetch_pubmed

    return {
        "pubmed": fetch_pubmed,
        "biorxiv": lambda client, d, **kw: fetch_biorxiv(client, d, server="biorxiv", **kw),
        "medrxiv": lambda client, d, **kw: fetch_biorxiv(client, d, server="medrxiv", **kw),
        "openalex": fetch_openalex,
    }


def _days_needing_fetch(
    conn: Any,
    source: str,
    *,
    date_from: date,
    date_to: date,
    recheck_days: int = 0,
) -> list[date]:
    """Determine which days need fetching for a source.

    Rules:
    1. If day == today: ALWAYS include (re-fetch for latest additions).
    2. If no completed row exists (or status="failed"): include.
    3. If recheck_days > 0 and last_verified_at is older than recheck_days: include.

    Parameters
    ----------
    conn:
        A DB-API connection with the publications schema.
    source:
        The source name (e.g. "pubmed", "biorxiv").
    date_from:
        Start of the date range (inclusive).
    date_to:
        End of the date range (inclusive).
    recheck_days:
        If > 0, re-fetch days whose last_verified_at is older than this many days.

    Returns
    -------
    list[date]
        Dates that need fetching, sorted ascending.
    """
    today = date.today()

    # Query all completed download_days rows for this source in range
    rows = fetch_all(
        conn,
        "SELECT date, status, last_verified_at FROM download_days"
        " WHERE source = ? AND date >= ? AND date <= ?",
        (source, date_from.isoformat(), date_to.isoformat()),
    )

    completed: dict[str, Any] = {}
    for row in rows:
        completed[row["date"]] = row

    needed: list[date] = []
    current = date_from
    while current <= date_to:
        if current == today:
            # Today is always re-fetched
            needed.append(current)
        elif current.isoformat() not in completed:
            # No row at all — needs fetch
            needed.append(current)
        else:
            entry = completed[current.isoformat()]
            if entry["status"] == "failed":
                # Failed days should be retried
                needed.append(current)
            elif recheck_days > 0 and entry["last_verified_at"] is not None:
                from datetime import datetime

                last_verified = datetime.fromisoformat(entry["last_verified_at"]).date()
                cutoff = today - timedelta(days=recheck_days)
                if last_verified < cutoff:
                    needed.append(current)
            elif recheck_days > 0 and entry["last_verified_at"] is None:
                # No last_verified_at set — treat as needing recheck
                needed.append(current)
        current += timedelta(days=1)

    return needed


def _raw_to_publication(raw: dict[str, Any], source: str) -> Publication:
    """Convert a raw fetcher dict to a Publication dataclass."""
    return Publication(
        title=raw.get("title", ""),
        doi=raw.get("doi"),
        pmid=raw.get("pmid"),
        abstract=raw.get("abstract"),
        authors=raw.get("authors", []),
        journal=raw.get("journal"),
        publication_date=raw.get("publication_date"),
        publication_types=raw.get("publication_types", []),
        keywords=raw.get("keywords", []),
        is_open_access=raw.get("is_open_access", False),
        license=raw.get("license"),
        sources=[source],
        first_seen_source=source,
    )


def _raw_to_fulltext_sources(raw: dict[str, Any]) -> list[FullTextSource] | None:
    """Extract FullTextSource objects from a raw fetcher dict, if any."""
    fts_list = raw.get("fulltext_sources")
    if not fts_list:
        return None
    result = []
    for fts in fts_list:
        result.append(
            FullTextSource(
                publication_id=0,  # will be set by store_publication
                source=fts.get("source", "unknown"),
                url=fts["url"],
                format=fts.get("format", "html"),
                version=fts.get("version"),
            )
        )
    return result if result else None


def _upsert_download_day(
    conn: Any,
    source: str,
    day: date,
    status: str,
    record_count: int,
) -> None:
    """Atomically insert or update a download_days row."""
    day_str = day.isoformat()
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC).isoformat()
    execute(
        conn,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at,"
        " last_verified_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (source, date) DO UPDATE SET"
        "   status = excluded.status,"
        "   record_count = excluded.record_count,"
        "   downloaded_at = excluded.downloaded_at,"
        "   last_verified_at = excluded.last_verified_at",
        (source, day_str, status, record_count, now, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sync(
    conn: Any,
    *,
    sources: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email: str,
    api_keys: dict[str, str] | None = None,
    on_record: Callable[[dict], None] | None = None,
    on_progress: Callable[[SyncProgress], None] | None = None,
    recheck_days: int = 0,
    _fetcher_override: dict[str, Callable] | None = None,
) -> SyncReport:
    """Orchestrate syncing publications from multiple sources.

    Parameters
    ----------
    conn:
        A DB-API connection.
    sources:
        Source names to sync.  Defaults to :data:`ALL_SOURCES`.
    date_from:
        Start date (inclusive).  Defaults to yesterday.
    date_to:
        End date (inclusive).  Defaults to today.
    email:
        Contact email for polite API access (required by OpenAlex).
    api_keys:
        Optional dict mapping source names to API keys.
    on_record:
        Optional callback invoked with each raw record dict.
    on_progress:
        Optional callback invoked with progress updates.
    recheck_days:
        If > 0, re-fetch completed days older than this many days.
    _fetcher_override:
        Dict mapping source names to callable fetchers (for testing).

    Returns
    -------
    SyncReport
        Summary of the sync operation.
    """
    ensure_schema(conn)

    today = date.today()
    if sources is None:
        sources = list(ALL_SOURCES)
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = today - timedelta(days=1)
    if api_keys is None:
        api_keys = {}

    fetchers = _fetcher_override if _fetcher_override is not None else _get_fetchers()

    # Create HTTP client only if using real fetchers
    client: Any = None
    if _fetcher_override is None:
        import httpx

        client = httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/0.1 (mailto:{email})"},
        )

    total_added = 0
    total_merged = 0
    total_failed = 0
    total_days = 0
    errors: list[str] = []
    sources_synced: list[str] = []

    try:
        for source in sources:
            fetcher = fetchers.get(source)
            if fetcher is None:
                errors.append(f"No fetcher found for source: {source}")
                continue

            days = _days_needing_fetch(
                conn,
                source,
                date_from=date_from,
                date_to=date_to,
                recheck_days=recheck_days,
            )

            if not days:
                sources_synced.append(source)
                continue

            for day in days:
                day_added = 0
                day_merged = 0
                day_failed = 0

                def handle_record(raw: dict[str, Any]) -> None:
                    nonlocal day_added, day_merged, day_failed
                    try:
                        pub = _raw_to_publication(raw, source)
                        fts = _raw_to_fulltext_sources(raw)
                        result = store_publication(conn, pub, fulltext_sources=fts)
                        if result == "added":
                            day_added += 1
                        elif result == "merged":
                            day_merged += 1
                    except Exception as exc:
                        day_failed += 1
                        logger.error("Failed to store record from %s: %s", source, exc)

                    if on_record is not None:
                        on_record(raw)

                # Build kwargs for the fetcher
                fetcher_kwargs: dict[str, Any] = {
                    "on_record": handle_record,
                    "on_progress": on_progress,
                }

                # Pass api_key if available for this source
                api_key = api_keys.get(source)
                if api_key is not None:
                    fetcher_kwargs["api_key"] = api_key

                # Pass email for openalex
                if source == "openalex":
                    fetcher_kwargs["email"] = email

                fetch_result = fetcher(client, day, **fetcher_kwargs)

                # All fetchers use "completed" or "failed" as status strings
                status = fetch_result.status if fetch_result.status == "failed" else "completed"
                record_count = day_added + day_merged

                _upsert_download_day(conn, source, day, status, record_count)

                total_added += day_added
                total_merged += day_merged
                total_failed += day_failed
                total_days += 1

                if fetch_result.error:
                    errors.append(f"{source}/{day.isoformat()}: {fetch_result.error}")

            sources_synced.append(source)
    finally:
        if client is not None and _fetcher_override is None:
            client.close()

    return SyncReport(
        sources_synced=sources_synced,
        days_processed=total_days,
        records_added=total_added,
        records_merged=total_merged,
        records_failed=total_failed,
        errors=errors,
    )
