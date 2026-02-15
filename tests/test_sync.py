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

"""Tests for the sync orchestrator and public API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from bmlib.db import connect_sqlite, execute
from bmlib.publications.fetchers import ALL_SOURCES
from bmlib.publications.models import FetchedRecord, FetchResult
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import get_publication_by_doi
from bmlib.publications.sync import _days_needing_fetch, sync


def _fresh_conn():
    """Return an in-memory SQLite connection with the publications schema."""
    conn = connect_sqlite(":memory:")
    ensure_schema(conn)
    return conn


def _insert_download_day(conn, source, day, status="completed", last_verified_at=None):
    """Insert a download_days row for testing."""
    now = datetime.now(tz=UTC).isoformat()
    lv = last_verified_at if last_verified_at else now
    execute(
        conn,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at,"
        " last_verified_at) VALUES (?, ?, ?, ?, ?, ?)",
        (source, day.isoformat(), status, 10, now, lv),
    )
    conn.commit()


def _make_fake_fetcher(records):
    """Create a fake fetcher that calls on_record with the given FetchedRecords.

    Parameters
    ----------
    records:
        List of :class:`FetchedRecord` instances to pass to on_record.

    Returns
    -------
    callable
        A fake fetcher matching the real fetcher signature.
    """

    def fake_fetcher(client, target_date, *, on_record, on_progress=None, **kwargs):
        for rec in records:
            on_record(rec)
        return FetchResult(
            source=rec.source if records else "test",
            date=target_date.isoformat(),
            record_count=len(records),
            status="completed",
        )

    return fake_fetcher


def _sample_raw_record(doi="10.1234/test.001", title="Test Paper", source="pubmed"):
    """Return a sample :class:`FetchedRecord` as produced by a fetcher."""
    return FetchedRecord(
        doi=doi,
        title=title,
        authors=["Author A", "Author B"],
        abstract="A test abstract.",
        journal="Test Journal",
        publication_date="2024-06-15",
        publication_types=["journal-article"],
        keywords=["testing"],
        is_open_access=False,
        license=None,
        source=source,
        fulltext_sources=[
            {"url": f"https://example.com/{doi}.pdf", "format": "pdf", "source": source},
        ],
    )


# ---------------------------------------------------------------------------
# Task 7: _days_needing_fetch tests
# ---------------------------------------------------------------------------


class TestDaysNeedingFetch:
    def test_all_days_needed_when_empty(self):
        """All days in range should be returned when no download_days rows exist."""
        conn = _fresh_conn()
        # Use a date range that does NOT include today
        d_from = date(2024, 6, 10)
        d_to = date(2024, 6, 12)
        days = _days_needing_fetch(conn, "pubmed", date_from=d_from, date_to=d_to)
        assert days == [date(2024, 6, 10), date(2024, 6, 11), date(2024, 6, 12)]

    def test_skips_completed_days(self):
        """Completed days should be skipped (when not today)."""
        conn = _fresh_conn()
        d_from = date(2024, 6, 10)
        d_to = date(2024, 6, 12)

        # Mark the middle day as completed
        _insert_download_day(conn, "pubmed", date(2024, 6, 11))

        days = _days_needing_fetch(conn, "pubmed", date_from=d_from, date_to=d_to)
        assert date(2024, 6, 11) not in days
        assert date(2024, 6, 10) in days
        assert date(2024, 6, 12) in days

    def test_retries_failed_days(self):
        """Days with status='failed' should be included for retry."""
        conn = _fresh_conn()
        d_from = date(2024, 6, 10)
        d_to = date(2024, 6, 11)

        _insert_download_day(conn, "pubmed", date(2024, 6, 10), status="failed")
        _insert_download_day(conn, "pubmed", date(2024, 6, 11), status="completed")

        days = _days_needing_fetch(conn, "pubmed", date_from=d_from, date_to=d_to)
        assert date(2024, 6, 10) in days
        assert date(2024, 6, 11) not in days

    def test_today_always_included(self):
        """Today should always be included, even if already completed."""
        conn = _fresh_conn()
        today = date.today()

        _insert_download_day(conn, "pubmed", today)

        days = _days_needing_fetch(conn, "pubmed", date_from=today, date_to=today)
        assert today in days

    def test_recheck_old_days(self):
        """Days with old last_verified_at should be re-fetched when recheck_days > 0."""
        conn = _fresh_conn()
        d = date(2024, 6, 10)
        old_date = (datetime.now(tz=UTC) - timedelta(days=30)).isoformat()
        _insert_download_day(conn, "pubmed", d, last_verified_at=old_date)

        days = _days_needing_fetch(conn, "pubmed", date_from=d, date_to=d, recheck_days=7)
        assert d in days


# ---------------------------------------------------------------------------
# Task 7: sync tests
# ---------------------------------------------------------------------------


class TestSync:
    def test_sync_stores_records(self):
        """sync should store records and update download_days tracker."""
        conn = _fresh_conn()
        today = date.today()
        yesterday = today - timedelta(days=1)

        records = [
            _sample_raw_record(doi="10.1234/sync.001", title="Sync Paper 1"),
            _sample_raw_record(doi="10.1234/sync.002", title="Sync Paper 2"),
        ]
        fake_fetcher = _make_fake_fetcher(records)

        report = sync(
            conn,
            sources=["test_source"],
            date_from=yesterday,
            date_to=yesterday,
            email="test@example.com",
            _fetcher_override={"test_source": fake_fetcher},
        )

        assert report.records_added == 2
        assert report.records_merged == 0
        assert report.records_failed == 0
        assert report.days_processed == 1
        assert "test_source" in report.sources_synced

        # Verify records were stored
        pub1 = get_publication_by_doi(conn, "10.1234/sync.001")
        assert pub1 is not None
        assert pub1.title == "Sync Paper 1"

        pub2 = get_publication_by_doi(conn, "10.1234/sync.002")
        assert pub2 is not None
        assert pub2.title == "Sync Paper 2"

        # Verify download_days was updated
        from bmlib.db import fetch_one

        row = fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source = ? AND date = ?",
            ("test_source", yesterday.isoformat()),
        )
        assert row is not None
        assert row["status"] == "completed"
        assert row["record_count"] == 2

    def test_sync_skips_completed_days(self):
        """sync should skip completed days that are not today."""
        conn = _fresh_conn()
        past_day = date(2024, 6, 10)

        # Pre-mark the day as completed
        _insert_download_day(conn, "test_source", past_day)

        call_count = 0

        def counting_fetcher(client, target_date, *, on_record, **kwargs):
            nonlocal call_count
            call_count += 1
            return FetchResult(
                source="test_source",
                date=target_date.isoformat(),
                record_count=0,
                status="completed",
            )

        report = sync(
            conn,
            sources=["test_source"],
            date_from=past_day,
            date_to=past_day,
            email="test@example.com",
            _fetcher_override={"test_source": counting_fetcher},
        )

        assert call_count == 0
        assert report.days_processed == 0

    def test_sync_calls_on_record_callback(self):
        """sync should call on_record callback for each record."""
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)

        records = [
            _sample_raw_record(doi="10.1234/cb.001"),
            _sample_raw_record(doi="10.1234/cb.002"),
        ]
        fake_fetcher = _make_fake_fetcher(records)

        callback_records: list[FetchedRecord] = []
        sync(
            conn,
            sources=["test_source"],
            date_from=yesterday,
            date_to=yesterday,
            email="test@example.com",
            on_record=callback_records.append,
            _fetcher_override={"test_source": fake_fetcher},
        )

        assert len(callback_records) == 2
        assert callback_records[0].doi == "10.1234/cb.001"
        assert callback_records[1].doi == "10.1234/cb.002"

    def test_sync_handles_empty_fetcher(self):
        """sync should handle a fetcher that returns no records."""
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)

        # Empty fetcher that returns no records
        def empty_fetcher(client, target_date, *, on_record, **kwargs):
            return FetchResult(
                source="test_source",
                date=target_date.isoformat(),
                record_count=0,
                status="completed",
            )

        report = sync(
            conn,
            sources=["test_source"],
            date_from=yesterday,
            date_to=yesterday,
            email="test@example.com",
            _fetcher_override={"test_source": empty_fetcher},
        )

        assert report.records_added == 0
        assert report.records_merged == 0
        assert report.records_failed == 0
        assert report.days_processed == 1
        assert report.errors == []

    def test_sync_merges_duplicate_records(self):
        """Duplicate DOIs across fetcher calls should be merged, not duplicated."""
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)

        # First pass: add a record
        records1 = [_sample_raw_record(doi="10.1234/dup.001", source="source_a")]
        fake_a = _make_fake_fetcher(records1)

        # Second pass: same DOI from a different source
        records2 = [_sample_raw_record(doi="10.1234/dup.001", source="source_b")]
        fake_b = _make_fake_fetcher(records2)

        report = sync(
            conn,
            sources=["source_a", "source_b"],
            date_from=yesterday,
            date_to=yesterday,
            email="test@example.com",
            _fetcher_override={"source_a": fake_a, "source_b": fake_b},
        )

        assert report.records_added == 1
        assert report.records_merged == 1

    def test_sync_defaults(self):
        """sync with no sources/dates should use ALL_SOURCES and yesterday-today range."""
        conn = _fresh_conn()

        # Create a fake fetcher for all sources
        def noop_fetcher(client, target_date, *, on_record, **kwargs):
            return FetchResult(
                source="test",
                date=target_date.isoformat(),
                record_count=0,
                status="completed",
            )

        overrides = {s: noop_fetcher for s in ALL_SOURCES}

        report = sync(
            conn,
            email="test@example.com",
            _fetcher_override=overrides,
        )

        # Should process yesterday and today for each of the 4 sources
        assert report.days_processed == len(ALL_SOURCES) * 2
        assert set(report.sources_synced) == set(ALL_SOURCES)

    def test_sync_unknown_source_reports_error(self):
        """sync with an unknown source should report an error."""
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)

        report = sync(
            conn,
            sources=["nonexistent"],
            date_from=yesterday,
            date_to=yesterday,
            email="test@example.com",
            _fetcher_override={},
        )

        assert len(report.errors) == 1
        assert "nonexistent" in report.errors[0]


# ---------------------------------------------------------------------------
# Task 8: Public API tests
# ---------------------------------------------------------------------------


class TestPublicAPI:
    def test_imports(self):
        from bmlib.publications import (
            DownloadDay,
            FetchResult,
            FullTextSource,
            Publication,
            SyncProgress,
            SyncReport,
            add_fulltext_source,
            ensure_schema,
            get_publication_by_doi,
            get_publication_by_pmid,
            store_publication,
            sync,
        )

        assert sync is not None
        assert Publication is not None
        assert SyncReport is not None
        assert FullTextSource is not None
        assert DownloadDay is not None
        assert SyncProgress is not None
        assert FetchResult is not None
        assert store_publication is not None
        assert get_publication_by_doi is not None
        assert get_publication_by_pmid is not None
        assert add_fulltext_source is not None
        assert ensure_schema is not None
