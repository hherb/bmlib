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
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, TypeVar

from bmlib import __version__
from bmlib.db import execute, fetch_all, placeholder, transaction
from bmlib.publications.fetchers.registry import get_fetcher, source_names
from bmlib.publications.models import (
    AuthorAffiliation,
    FetchedRecord,
    FetchResult,
    FullTextSource,
    Grant,
    Publication,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import store_publication

logger = logging.getLogger(__name__)

# Both carry ``source`` and are dataclasses, which is all _stamp_source needs.
# Bound rather than bare, so ``dataclasses.replace`` — which accepts only a
# dataclass instance — type-checks, and so the return type stays the caller's
# own type rather than widening to the union.
_ChildRow = TypeVar("_ChildRow", Grant, AuthorAffiliation)

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
    ph = placeholder(conn)

    # Query all completed download_days rows for this source in range
    rows = fetch_all(
        conn,
        "SELECT date, status, last_verified_at FROM download_days"
        f" WHERE source = {ph} AND date >= {ph} AND date <= {ph}",
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
        pmcid=record.pmc_id,
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


def _stamp_source(rows: Sequence[_ChildRow], source: str) -> list[_ChildRow]:
    """Return copies of *rows* whose ``source`` is the record's own.

    Provenance is stamped here rather than in each fetcher because this is the
    one place that authoritatively knows which source produced the record — a
    fetcher can forget, and the cost of forgetting is silent: rows land in an
    unnamed bucket and stop being scoped, which is the cross-source
    flip-flopping that ``source`` exists to prevent. Whatever a fetcher may
    have set is overwritten, which is correct: a row's provenance *is* the
    source that reported the record carrying it.
    """
    return [replace(row, source=source) for row in rows]


def _record_to_fulltext_sources(record: FetchedRecord) -> list[FullTextSource] | None:
    """Extract :class:`FullTextSource` objects from a :class:`FetchedRecord`."""
    if not record.fulltext_sources:
        return None
    result = []
    for fts in record.fulltext_sources:
        # Fetchers populate ``fulltext_sources`` with ``FullTextSourceEntry``
        # dataclass instances; accept plain dicts too for robustness.
        if isinstance(fts, dict):
            url = fts.get("url")
            source = fts.get("source", "unknown")
            fmt = fts.get("format", "html")
            version = fts.get("version")
        else:
            url = getattr(fts, "url", None)
            source = getattr(fts, "source", "unknown")
            fmt = getattr(fts, "format", "html")
            version = getattr(fts, "version", None)
        if not url:
            continue
        result.append(
            FullTextSource(
                publication_id=0,  # will be set by store_publication
                source=source,
                url=url,
                format=fmt,
                version=version,
            )
        )
    return result if result else None


def _resolve_day_status(
    source: str,
    day: date,
    fetch_result: FetchResult,
    day_failed: int,
) -> tuple[str, list[str]]:
    """Decide what to store for a day, and what to tell the caller about it.

    Returns the ``download_days`` status and any lines to append to
    :class:`SyncReport`'s ``errors``. Both failure modes here used to be
    recorded as ``completed``, which is durable: ``_days_needing_fetch()``
    never offers a completed day again, so the records are permanently absent.

    Two rules, both failing closed:

    *Unknown status* — the convention is ``"completed"`` or ``"failed"``, but
    it was enforced by a denylist (anything not exactly ``"failed"`` became
    ``completed``), so a fetcher reporting failure in any other spelling had
    that failure converted into success. ``register_source()`` is a documented
    extension point, and a third-party fetcher is exactly the caller who will
    not know the convention.

    *Records that failed to store* — a day whose records raised on the way in
    is missing them by name. ``store_publication`` merges, so re-fetching is
    idempotent and the retry is cheap.
    """
    notes: list[str] = []
    status = fetch_result.status

    if status not in ("completed", "failed"):
        logger.error(
            "Fetcher for %s/%s returned unknown status %r; recording the day as failed",
            source,
            day.isoformat(),
            status,
        )
        notes.append(
            f"{source}/{day.isoformat()}: fetcher returned unknown status {status!r};"
            " recorded as failed"
        )
        status = "failed"

    if day_failed:
        notes.append(f"{source}/{day.isoformat()}: {day_failed} record(s) failed to store")
        status = "failed"

    return status, notes


def _upsert_download_day(
    conn: Any,
    source: str,
    day: date,
    status: str,
    record_count: int,
) -> None:
    """Insert or update a download_days row.

    Runs inside the caller's per-day transaction (see :func:`sync`), so the
    day's status commits atomically with the day's records; commits itself
    only when called with no transaction open.
    """
    day_str = day.isoformat()
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC).isoformat()
    ph = placeholder(conn)
    with transaction(conn):
        execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at,"
            f" last_verified_at) VALUES ({', '.join([ph] * 6)})"
            " ON CONFLICT (source, date) DO UPDATE SET"
            "   status = excluded.status,"
            "   record_count = excluded.record_count,"
            "   downloaded_at = excluded.downloaded_at,"
            "   last_verified_at = excluded.last_verified_at",
            (source, day_str, status, record_count, now, now),
        )


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
        Optional callback invoked with each :class:`FetchedRecord` as the
        fetcher streams it — *before* the record is stored, so the callback
        must not expect to read the record back from the database. Records
        are stored in one batch per day after the fetch completes.
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

        user_agent_email = resolved_configs.get("openalex", {}).get("email", email) or "unknown"
        client = httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": f"bmlib/{__version__} (mailto:{user_agent_email})"},
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
                day_records: list[FetchedRecord] = []

                def handle_record(record: FetchedRecord) -> None:
                    # Buffer only — the store happens after the fetch so the
                    # day's write transaction never spans network I/O. The
                    # whole day is held in memory (typically a few thousand
                    # records, tens of MB with abstracts); if a source ever
                    # delivers far larger days, flush in chunks here.
                    day_records.append(record)
                    if on_record is not None:
                        on_record(record)

                try:
                    fetch_result = fetcher(
                        client,
                        day,
                        on_record=handle_record,
                        on_progress=on_progress,
                        **src_config,
                    )
                except Exception as exc:
                    # A misconfigured source (e.g. a required kwarg like
                    # OpenAlex's ``email`` not supplied) or a bug inside a
                    # fetcher must not abort the whole multi-source run and
                    # discard the report. Record it as a failed day and move on.
                    logger.error("Fetcher for %s/%s raised: %s", source, day.isoformat(), exc)
                    fetch_result = FetchResult(
                        source=source,
                        date=day.isoformat(),
                        record_count=len(day_records),
                        status="failed",
                        error=str(exc),
                    )

                # One transaction per day: each store_publication call joins
                # it (savepoint) instead of committing, so a day of thousands
                # of records costs a single commit/fsync rather than one per
                # statement — and because records were buffered during the
                # fetch, SQLite's write lock is held only for the store loop,
                # not for the network-bound fetch. A record that fails to
                # store rolls back to its own savepoint without losing the
                # batch, and the day-status row commits atomically with the
                # records. Should writing the day-status row itself fail, the
                # whole day rolls back and the error propagates — the day is
                # left unrecorded and simply retried on the next run.
                with transaction(conn):
                    for record in day_records:
                        try:
                            pub = _record_to_publication(record)
                            fts = _record_to_fulltext_sources(record)
                            result = store_publication(
                                conn,
                                pub,
                                fulltext_sources=fts,
                                grants=_stamp_source(record.grants, record.source),
                                affiliations=_stamp_source(
                                    record.author_affiliations, record.source
                                ),
                            )
                            if result == "added":
                                day_added += 1
                            elif result == "merged":
                                day_merged += 1
                        except Exception as exc:
                            # Broad on purpose — one bad record must not lose
                            # the batch — so the exception *type* is logged
                            # too: a TypeError here is a bmlib defect
                            # affecting every record, not bad data from the
                            # source, and the two read identically without it.
                            day_failed += 1
                            logger.error(
                                "Failed to store record from %s/%s: %s: %s",
                                source,
                                day.isoformat(),
                                type(exc).__name__,
                                exc,
                            )

                    status, day_notes = _resolve_day_status(source, day, fetch_result, day_failed)
                    record_count = day_added + day_merged

                    _upsert_download_day(conn, source, day, status, record_count)

                total_added += day_added
                total_merged += day_merged
                total_failed += day_failed
                total_days += 1

                if fetch_result.error:
                    errors.append(f"{source}/{day.isoformat()}: {fetch_result.error}")
                errors.extend(day_notes)

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
