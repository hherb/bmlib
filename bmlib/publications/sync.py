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
from bmlib.publications.fetchers.registry import get_fetcher, source_names
from bmlib.publications.models import (
    FetchedRecord,
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


def _get_fetcher_for_source(source: str) -> Callable | None:
    """Return the registered fetcher for a source, or None if unknown."""
    try:
        return get_fetcher(source)
    except ValueError:
        return None


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


def _record_to_publication(record: FetchedRecord) -> Publication:
    """Convert a :class:`FetchedRecord` to a :class:`Publication`."""
    return Publication(
        title=record.title,
        doi=record.doi,
        pmid=record.pmid,
        abstract=record.abstract,
        authors=record.authors,
        journal=record.journal,
        publication_date=record.publication_date,
        publication_types=record.publication_types,
        keywords=record.keywords,
        is_open_access=record.is_open_access,
        license=record.license,
        sources=[record.source],
        first_seen_source=record.source,
    )


def _record_to_fulltext_sources(record: FetchedRecord) -> list[FullTextSource] | None:
    """Extract :class:`FullTextSource` objects from a :class:`FetchedRecord`."""
    if not record.fulltext_sources:
        return None
    result = []
    for fts in record.fulltext_sources:
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


def _build_source_configs(
    source_configs: dict[str, dict[str, Any]] | None,
    email: str,
    api_keys: dict[str, str] | None,
) -> dict[str, dict[str, Any]]:
    """Merge legacy ``email``/``api_keys`` params into a ``source_configs`` dict."""
    if source_configs is not None:
        return source_configs

    configs: dict[str, dict[str, Any]] = {}
    if api_keys:
        for src, key in api_keys.items():
            configs.setdefault(src, {})["api_key"] = key
    if email:
        configs.setdefault("openalex", {})["email"] = email
    return configs


def sync(
    conn: Any,
    *,
    sources: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email: str = "",
    api_keys: dict[str, str] | None = None,
    source_configs: dict[str, dict[str, Any]] | None = None,
    on_record: Callable[[FetchedRecord], None] | None = None,
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
        Source names to sync.  Defaults to all registered sources.
    date_from:
        Start date (inclusive).  Defaults to yesterday.
    date_to:
        End date (inclusive).  Defaults to today.
    email:
        Contact email for polite API access (legacy; prefer *source_configs*).
    api_keys:
        Dict mapping source names to API keys (legacy; prefer *source_configs*).
    source_configs:
        Dict mapping source names to config dicts.  Each config dict is
        unpacked as ``**kwargs`` when calling the fetcher.  Supersedes
        *email* and *api_keys* when provided.
    on_record:
        Optional callback invoked with each :class:`FetchedRecord`.
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
        sources = list(source_names())
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = today - timedelta(days=1)

    resolved_configs = _build_source_configs(source_configs, email, api_keys)

    # Create HTTP client only if using real fetchers
    client: Any = None
    if _fetcher_override is None:
        import httpx

        user_agent_email = (
            resolved_configs.get("openalex", {}).get("email", email) or "unknown"
        )
        client = httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/0.1 (mailto:{user_agent_email})"},
        )

    total_added = 0
    total_merged = 0
    total_failed = 0
    total_days = 0
    errors: list[str] = []
    sources_synced: list[str] = []

    try:
        for source in sources:
            if _fetcher_override is not None:
                fetcher = _fetcher_override.get(source)
            else:
                fetcher = _get_fetcher_for_source(source)

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

            src_config = resolved_configs.get(source, {})

            for day in days:
                day_added = 0
                day_merged = 0
                day_failed = 0

                def handle_record(record: FetchedRecord) -> None:
                    nonlocal day_added, day_merged, day_failed
                    try:
                        pub = _record_to_publication(record)
                        fts = _record_to_fulltext_sources(record)
                        result = store_publication(conn, pub, fulltext_sources=fts)
                        if result == "added":
                            day_added += 1
                        elif result == "merged":
                            day_merged += 1
                    except Exception as exc:
                        day_failed += 1
                        logger.error("Failed to store record from %s: %s", source, exc)

                    if on_record is not None:
                        on_record(record)

                fetch_result = fetcher(
                    client, day,
                    on_record=handle_record,
                    on_progress=on_progress,
                    **src_config,
                )

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
