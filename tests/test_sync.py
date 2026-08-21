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

import sys
from datetime import UTC, date, datetime, timedelta

import pytest

from bmlib.db import connect_sqlite, execute, fetch_all, fetch_one
from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.fetchers import ALL_SOURCES
from bmlib.publications.models import FetchedRecord, FetchResult, PartCheckpoint
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import get_publication_by_doi
from bmlib.publications.sync import (
    _clear_day_parts,
    _day_was_over_when_fetched,
    _days_needing_fetch,
    _load_day_parts,
    _record_day_part,
    sync,
)


def _fresh_conn():
    """Return an in-memory SQLite connection with the publications schema."""
    conn = connect_sqlite(":memory:")
    ensure_schema(conn)
    return conn


def _insert_download_day(
    conn, source, day, status="completed", last_verified_at=None, downloaded_at=None
):
    """Insert a download_days row for testing.

    *downloaded_at* defaults to now, which is what a fetch of a past day
    records; the tests for issue #95 pass it explicitly, since the whole
    question there is whether the fetch happened before the day was over.
    """
    now = datetime.now(tz=UTC).isoformat()
    dl = downloaded_at if downloaded_at is not None else now
    lv = last_verified_at if last_verified_at else now
    execute(
        conn,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at,"
        " last_verified_at) VALUES (?, ?, ?, ?, ?, ?)",
        (source, day.isoformat(), status, 10, dl, lv),
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
            FullTextSourceEntry(url=f"https://example.com/{doi}.pdf", format="pdf", source=source),
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

    def test_today_is_included_even_when_already_completed(self):
        """Today is offered again — but by the durability rule, not a special case.

        This used to be named for, and assert, the ``if current == today``
        branch #95 removed. It still passes, for a different reason: a row
        written during today is always earlier than its own 12:00-UTC-tomorrow
        boundary. The argument is in
        ``TestACompletedDayIsDurableOnlyOnceTheDayIsOver``; this is the older
        suite's coverage of the same contract.
        """
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

        # Verify the full-text source (a FullTextSourceEntry dataclass) was
        # extracted and stored, not silently dropped.
        from bmlib.db import fetch_all

        fts_rows = fetch_all(
            conn,
            "SELECT * FROM fulltext_sources WHERE publication_id = ?",
            (pub1.id,),
        )
        assert len(fts_rows) == 1
        assert fts_rows[0]["url"] == "https://example.com/10.1234/sync.001.pdf"
        assert fts_rows[0]["format"] == "pdf"

        # Verify download_days was updated
        row = fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source = ? AND date = ?",
            ("test_source", yesterday.isoformat()),
        )
        assert row is not None
        assert row["status"] == "completed"
        assert row["record_count"] == 2

    def test_sync_commits_once_per_day(self):
        """A day's records must be written in one batch commit, not one per record."""
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)

        records = [_sample_raw_record(doi=f"10.1234/batch.{i:03d}") for i in range(5)]
        fake_fetcher = _make_fake_fetcher(records)

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            report = sync(
                conn,
                sources=["test_source"],
                date_from=yesterday,
                date_to=yesterday,
                email="test@example.com",
                _fetcher_override={"test_source": fake_fetcher},
            )
        finally:
            conn.set_trace_callback(None)

        assert report.records_added == 5
        commits = [s for s in statements if s.strip().upper().startswith("COMMIT")]
        assert len(commits) == 1  # records + download_days row, one commit per day

    def test_sync_holds_no_transaction_during_fetch(self):
        """The day's write transaction must not span the network-bound fetch.

        Records are buffered while the fetcher streams and stored afterwards
        in one short transaction, so SQLite's write lock is never held across
        network I/O and rate-limit sleeps.
        """
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)
        records = [_sample_raw_record(doi=f"10.1234/lock.{i:03d}") for i in range(3)]

        in_txn_during_fetch: list[bool] = []

        def probing_fetcher(client, target_date, *, on_record, on_progress=None, **kwargs):
            for rec in records:
                on_record(rec)
                in_txn_during_fetch.append(conn.in_transaction)
            return FetchResult(
                source="test_source",
                date=target_date.isoformat(),
                record_count=len(records),
                status="completed",
            )

        report = sync(
            conn,
            sources=["test_source"],
            date_from=yesterday,
            date_to=yesterday,
            email="test@example.com",
            _fetcher_override={"test_source": probing_fetcher},
        )

        assert report.records_added == 3
        assert not any(in_txn_during_fetch)

    def test_sync_failing_record_does_not_corrupt_batch(self):
        """A record that fails to store must not take the rest of the day with it."""
        conn = _fresh_conn()
        yesterday = date.today() - timedelta(days=1)

        bad = _sample_raw_record(doi="10.1234/fail.bad")
        bad.title = None  # violates publications.title NOT NULL
        records = [
            _sample_raw_record(doi="10.1234/fail.001"),
            bad,
            _sample_raw_record(doi="10.1234/fail.002"),
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
        assert report.records_failed == 1
        assert get_publication_by_doi(conn, "10.1234/fail.001") is not None
        assert get_publication_by_doi(conn, "10.1234/fail.002") is not None
        assert get_publication_by_doi(conn, "10.1234/fail.bad") is None

        row = fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source = ? AND date = ?",
            ("test_source", yesterday.isoformat()),
        )
        assert row is not None
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


class TestSyncPersistsGrantsAndAffiliations:
    """A fetcher's grants and affiliations must survive the whole sync path.

    They are new fields on ``FetchedRecord``, and the fields that are *not*
    persisted (``pmc_id``'s predecessor, ``extras``) show how quietly that can
    go wrong: the fetcher populates them, sync drops them, and nothing raises.
    """

    def test_grants_and_affiliations_reach_the_database(self):
        from bmlib.publications.models import AuthorAffiliation, Grant
        from bmlib.publications.storage import get_author_affiliations, get_grants

        conn = _fresh_conn()
        day = date(2024, 6, 11)

        record = _sample_raw_record(
            doi="10.1234/funded", title="A funded paper", source="test_source"
        )
        record.grants = [Grant(agency="NHLBI", grant_id="R01", country="United States")]
        record.author_affiliations = [
            AuthorAffiliation(author="Smith, J", affiliation="St Elsewhere", position=0),
            AuthorAffiliation(author="Brown, A", affiliation="Pfizer Inc", position=1),
        ]

        sync(
            conn,
            sources=["test_source"],
            date_from=day,
            date_to=day,
            email="test@example.com",
            _fetcher_override={"test_source": _make_fake_fetcher([record])},
        )

        pub = get_publication_by_doi(conn, "10.1234/funded")
        assert pub is not None

        grants = get_grants(conn, pub.id)
        assert [(g.agency, g.grant_id, g.country) for g in grants] == [
            ("NHLBI", "R01", "United States")
        ]

        affiliations = get_author_affiliations(conn, pub.id)
        assert [(a.author, a.affiliation, a.position) for a in affiliations] == [
            ("Smith, J", "St Elsewhere", 0),
            ("Brown, A", "Pfizer Inc", 1),
        ]

        # sync() stamps the provenance the fetcher left blank. Without these
        # two assertions the whole stamping step can be deleted and every test
        # still passes: unstamped rows land in the "" bucket, satisfy NOT NULL,
        # and read back byte-identical on the columns above.
        assert {g.source for g in grants} == {"test_source"}
        assert {a.source for a in affiliations} == {"test_source"}

    def test_a_record_without_them_stores_cleanly(self):
        """The common case — every non-PubMed fetcher supplies neither."""
        from bmlib.publications.storage import get_author_affiliations, get_grants

        conn = _fresh_conn()
        day = date(2024, 6, 11)

        sync(
            conn,
            sources=["test_source"],
            date_from=day,
            date_to=day,
            email="test@example.com",
            _fetcher_override={"test_source": _make_fake_fetcher([_sample_raw_record()])},
        )

        pub = get_publication_by_doi(conn, "10.1234/test.001")
        assert get_grants(conn, pub.id) == []
        assert get_author_affiliations(conn, pub.id) == []

    def test_two_sources_coexist_through_sync(self):
        """The flip-flop bug, guarded where it would actually happen.

        Every other cross-source test reaches ``store_publication`` directly
        and sets ``source`` by hand, so none of them exercises the stamping
        that makes the scoping work in production. Here neither fetcher sets
        ``source`` — exactly as the real ones do not — and the rows must still
        land in separate buckets and stay there across repeated syncs.
        """
        from bmlib.publications.models import Grant
        from bmlib.publications.storage import get_grants

        conn = _fresh_conn()
        day = date(2024, 6, 11)

        def fetcher_for(source: str, agency: str):
            def fetch(client, target_date, *, on_record, on_progress=None, **kwargs):
                record = _sample_raw_record(doi="10.1234/funded", source=source)
                record.grants = [Grant(agency=agency)]  # deliberately unstamped
                on_record(record)
                return FetchResult(
                    source=source,
                    date=target_date.isoformat(),
                    record_count=1,
                    status="completed",
                )

            return fetch

        # Sync each source twice, interleaved — the shape that used to leave
        # whichever source ran last holding the only surviving grant.
        for source, agency in [
            ("pubmed", "NHLBI"),
            ("openalex", "Wellcome Trust"),
            ("pubmed", "NHLBI"),
            ("openalex", "Wellcome Trust"),
        ]:
            sync(
                conn,
                sources=[source],
                date_from=day,
                date_to=day,
                email="test@example.com",
                _fetcher_override={source: fetcher_for(source, agency)},
            )

        pub = get_publication_by_doi(conn, "10.1234/funded")
        assert sorted((g.source, g.agency) for g in get_grants(conn, pub.id)) == [
            ("openalex", "Wellcome Trust"),
            ("pubmed", "NHLBI"),
        ]


class TestTheDayStatusReflectsWhatActuallyHappened:
    """Issues #89 and #90 — a failure must not be stored as a completed day.

    ``download_days`` is what ``_days_needing_fetch()`` consults on the next
    run, so a day wrongly stored as ``completed`` is never offered again and
    its records are permanently absent. The transient ``SyncReport`` is
    discarded; the row is not.
    """

    _DAY = date(2024, 6, 15)

    def _day_row(self, conn, source="test_source"):
        from bmlib.db import fetch_one

        return fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source = ? AND date = ?",
            (source, self._DAY.isoformat()),
        )

    def _fetcher_returning(self, status, error=None, records=()):
        def fetch(client, target_date, *, on_record, on_progress=None, **kwargs):
            for rec in records:
                on_record(rec)
            return FetchResult(
                source="test_source",
                date=target_date.isoformat(),
                record_count=len(records),
                status=status,
                error=error,
            )

        return fetch

    def _sync(self, conn, fetcher):
        return sync(
            conn,
            sources=["test_source"],
            date_from=self._DAY,
            date_to=self._DAY,
            email="test@example.com",
            _fetcher_override={"test_source": fetcher},
        )

    # -- #89: the status allowlist -----------------------------------------

    def test_an_unknown_status_is_recorded_as_failed(self):
        """ "partial" is not "failed", and was therefore stored as success."""
        conn = _fresh_conn()

        self._sync(conn, self._fetcher_returning("partial", error="half the pages timed out"))

        assert self._day_row(conn)["status"] == "failed"

    def test_an_unknown_status_leaves_the_day_to_be_retried(self):
        conn = _fresh_conn()

        self._sync(conn, self._fetcher_returning("error", error="boom"))

        assert _days_needing_fetch(conn, "test_source", date_from=self._DAY, date_to=self._DAY) == [
            self._DAY
        ]

    def test_an_unknown_status_is_named_in_the_report(self):
        """register_source() is public, so a third-party spelling must be visible."""
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher_returning("partial"))

        assert any("partial" in e for e in report.errors)

    def test_a_completed_status_is_still_completed(self):
        """Negative control: failing closed must not fail an ordinary day."""
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher_returning("completed"))

        assert self._day_row(conn)["status"] == "completed"
        assert report.errors == []

    # -- #90: records that failed to store ---------------------------------

    def test_a_day_whose_records_all_failed_to_store_is_recorded_failed(self):
        conn = _fresh_conn()
        bad = _sample_raw_record(doi="10.1234/store.bad")
        bad.title = None  # violates publications.title NOT NULL

        self._sync(conn, self._fetcher_returning("completed", records=[bad]))

        row = self._day_row(conn)
        assert row["status"] == "failed"
        assert row["record_count"] == 0

    def test_a_partial_store_failure_also_fails_the_day(self):
        """A day missing one record by name is missing it durably."""
        conn = _fresh_conn()
        bad = _sample_raw_record(doi="10.1234/store.bad")
        bad.title = None
        good = _sample_raw_record(doi="10.1234/store.good")

        self._sync(conn, self._fetcher_returning("completed", records=[good, bad]))

        row = self._day_row(conn)
        assert row["status"] == "failed"
        assert row["record_count"] == 1  # the good one was still stored

    def test_a_store_failure_reaches_the_report_errors(self):
        """It used to leave only per-record logs and a source-less counter."""
        conn = _fresh_conn()
        bad = _sample_raw_record(doi="10.1234/store.bad")
        bad.title = None

        report = self._sync(conn, self._fetcher_returning("completed", records=[bad]))

        assert len(report.errors) == 1
        assert "test_source/2024-06-15" in report.errors[0]
        assert report.records_failed == 1

    def test_a_failed_store_leaves_the_day_to_be_retried(self):
        """store_publication merges, so re-fetching the day is idempotent."""
        conn = _fresh_conn()
        bad = _sample_raw_record(doi="10.1234/store.bad")
        bad.title = None

        self._sync(conn, self._fetcher_returning("completed", records=[bad]))

        assert _days_needing_fetch(conn, "test_source", date_from=self._DAY, date_to=self._DAY) == [
            self._DAY
        ]

    def test_a_clean_day_is_not_offered_again(self):
        """Negative control for the retry: a good day still completes."""
        conn = _fresh_conn()
        good = _sample_raw_record(doi="10.1234/store.good")

        self._sync(conn, self._fetcher_returning("completed", records=[good]))

        assert (
            _days_needing_fetch(conn, "test_source", date_from=self._DAY, date_to=self._DAY) == []
        )

    def test_the_store_failure_log_names_the_exception_type(self, caplog):
        """A TypeError here is a bmlib defect, not bad data from the source.

        The handler stays broad — one bad record must not lose the batch — so
        the type is what tells the two apart in a log.
        """
        import logging
        from unittest.mock import patch

        conn = _fresh_conn()
        good = _sample_raw_record(doi="10.1234/store.good")

        with patch(
            "bmlib.publications.sync.store_publication",
            side_effect=TypeError("unexpected keyword argument"),
        ):
            with caplog.at_level(logging.ERROR, logger="bmlib.publications.sync"):
                self._sync(conn, self._fetcher_returning("completed", records=[good]))

        assert "TypeError" in caplog.text
        assert "test_source/2024-06-15" in caplog.text


class TestWhatTheCallerIsToldAboutAnImperfectDay:
    """A day that completes imperfectly, and a failure with nothing to say.

    Both used to leave the caller with a clean-looking report: a shortfall
    below the failure floor reached only a log line, and an error whose
    message was empty was dropped from ``errors`` by a truthiness test.
    """

    _DAY = date(2024, 6, 15)

    def _fetcher(self, *, status="completed", error=None, note=None):
        def fetch(client, target_date, *, on_record, on_progress=None, **kwargs):
            return FetchResult(
                source="test_source",
                date=target_date.isoformat(),
                record_count=0,
                status=status,
                error=error,
                note=note,
            )

        return fetch

    def _sync(self, conn, fetcher):
        return sync(
            conn,
            sources=["test_source"],
            date_from=self._DAY,
            date_to=self._DAY,
            email="test@example.com",
            _fetcher_override={"test_source": fetcher},
        )

    def test_a_short_day_that_completes_is_reported(self):
        """Up to half a day's records can go missing on this path.

        The day is stored ``completed`` and never re-offered, so unless the
        shortfall is returned there is no surface on which an operator could
        later ask which completed days came up short.
        """
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher(note="delivered 600 of 1000 records"))

        assert any("delivered 600 of 1000" in n for n in report.notes)
        assert report.errors == []

    def test_a_note_is_kept_apart_from_the_errors(self):
        """The two call for different responses: a retry, versus no retry."""
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher(note="delivered 600 of 1000 records"))

        assert report.notes != []
        assert not any("600" in e for e in report.errors)

    def test_an_ordinary_day_carries_no_note(self):
        """Negative control: the notes channel must not fill up on every day."""
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher())

        assert report.notes == []

    def test_a_note_on_a_failed_day_is_not_reported_as_a_note(self):
        """A failed day is retried, so a shortfall on it is not news."""
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher(status="failed", error="boom", note="short"))

        assert report.notes == []

    def test_an_error_with_an_empty_message_still_reaches_the_report(self):
        """``str(OSError())`` is ``""``, which a truthiness test drops.

        The day is retried either way, so nothing is lost — but if the cause
        is deterministic the day fails on every run while the report shows no
        errors at all, and the operator has nothing to work from.
        """
        conn = _fresh_conn()

        report = self._sync(conn, self._fetcher(status="failed", error=""))

        assert len(report.errors) == 1
        assert "test_source/2024-06-15" in report.errors[0]


class TestTheReadSideAgreesWithTheWriteSide:
    """``_days_needing_fetch`` must not treat an unrecognised status as done."""

    _DAY = date(2024, 6, 15)

    def test_a_day_stored_with_an_unrecognised_status_is_offered_again(self):
        """The mirror of #89, on the read side.

        ``_resolve_day_status`` now refuses to *write* anything but
        ``completed``/``failed``. Read through ``== "failed"``, any other
        value already in the table — written by an older bmlib, a third-party
        writer, or a future ``partial`` status — counts as done and the day is
        never fetched again.
        """
        conn = _fresh_conn()
        _insert_download_day(conn, "test_source", self._DAY, status="in_progress")

        needed = _days_needing_fetch(conn, "test_source", date_from=self._DAY, date_to=self._DAY)

        assert needed == [self._DAY]

    def test_a_completed_day_is_still_not_offered_again(self):
        """Negative control: the allowlist must not re-offer every day."""
        conn = _fresh_conn()
        _insert_download_day(conn, "test_source", self._DAY, status="completed")

        needed = _days_needing_fetch(conn, "test_source", date_from=self._DAY, date_to=self._DAY)

        assert needed == []


class TestTheFetcherAndTheDurableRowMeet:
    """The seam issue #88 is actually about, which neither half tested.

    Every fetcher test stops at ``FetchResult``; every sync test injects a
    hand-made one. Nothing ran a real reconciliation through to the row that
    decides whether the day is ever fetched again.
    """

    _DAY = date(2024, 6, 15)

    def _sync_with_biorxiv_serving(self, conn, payload):
        from unittest.mock import MagicMock

        from bmlib.publications.fetchers.biorxiv import fetch_biorxiv

        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status = MagicMock()
        http = MagicMock()
        http.get.return_value = response

        def fetch(client, target_date, *, on_record, on_progress=None, **kwargs):
            return fetch_biorxiv(http, target_date, on_record=on_record)

        return sync(
            conn,
            sources=["biorxiv"],
            date_from=self._DAY,
            date_to=self._DAY,
            email="test@example.com",
            _fetcher_override={"biorxiv": fetch},
        )

    def test_a_short_walk_fails_its_day_and_the_day_comes_back(self):
        """One preprint against a promised 250: stored failed, offered again."""
        payload = {
            "messages": [{"total": "250", "count": "1"}],
            "collection": [
                {
                    "doi": "10.1101/2024.06.15.000001",
                    "title": "A preprint",
                    "date": "2024-06-15",
                    "version": "1",
                    "category": "genomics",
                }
            ],
        }
        conn = _fresh_conn()

        report = self._sync_with_biorxiv_serving(conn, payload)

        row = fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source = ? AND date = ?",
            ("biorxiv", self._DAY.isoformat()),
        )
        assert row["status"] == "failed"
        assert any("delivered 1 of 250" in e for e in report.errors)
        assert _days_needing_fetch(conn, "biorxiv", date_from=self._DAY, date_to=self._DAY) == [
            self._DAY
        ]

    def test_a_whole_walk_completes_and_the_day_stays_done(self):
        """Negative control across the same seam."""
        payload = {
            "messages": [{"total": "1", "count": "1"}],
            "collection": [
                {
                    "doi": "10.1101/2024.06.15.000002",
                    "title": "A preprint",
                    "date": "2024-06-15",
                    "version": "1",
                    "category": "genomics",
                }
            ],
        }
        conn = _fresh_conn()

        report = self._sync_with_biorxiv_serving(conn, payload)

        assert report.errors == []
        assert _days_needing_fetch(conn, "biorxiv", date_from=self._DAY, date_to=self._DAY) == []


class TestACompletedDayIsDurableOnlyOnceTheDayIsOver:
    """Issue #95 — a day fetched *as* today was stored done and never revisited.

    ``sync()``'s default window is ``[yesterday, today]``, so a 09:00 cron
    captured today as it stood at 09:00 and recorded it ``completed``.
    Tomorrow that day is neither ``today`` nor ``failed``, so with the
    documented default ``recheck_days=0`` it was never offered again and
    everything indexed over the remaining 15 hours was permanently absent.
    No rule in ``_reconcile.py`` can see it: the source's own count agreed at
    09:00, because the walk really did deliver everything that existed then.

    The rule that replaces the old ``if current == today`` special case: a
    completed day is durable only once it was fetched after that day had
    ended **in every timezone**, which is 12:00 UTC the following day.
    """

    _DAY = date(2024, 6, 15)

    def _needed(self, conn, source="test_source"):
        return _days_needing_fetch(conn, source, date_from=self._DAY, date_to=self._DAY)

    def test_a_day_fetched_while_it_was_still_running_is_offered_again(self):
        """The issue, reproduced: a morning fetch of a day still in progress."""
        conn = _fresh_conn()
        _insert_download_day(
            conn, "test_source", self._DAY, downloaded_at="2024-06-15T09:00:00+00:00"
        )

        assert self._needed(conn) == [self._DAY]

    def test_a_day_fetched_after_it_ended_everywhere_is_not_offered_again(self):
        """Negative control: the rule must not re-offer every completed day.

        Without this, "always re-offer" passes the test above and costs every
        installation a permanent re-fetch of its whole date range.

        Deliberately *not* at the boundary instant — it sits days past it, so
        that this and ``test_noon_utc_the_next_day_is_late_enough`` are two
        data points rather than one. They were the same timestamp until
        review, which made the boundary test's claim below false and left the
        control unable to fail for its own reason alone.
        """
        conn = _fresh_conn()
        _insert_download_day(
            conn, "test_source", self._DAY, downloaded_at="2024-06-20T00:00:00+00:00"
        )

        assert self._needed(conn) == []

    def test_noon_utc_the_next_day_is_late_enough(self):
        """The boundary is inclusive, and which side it falls on is load-bearing.

        12:00 UTC on D+1 is midnight in UTC-12, the last zone on earth to
        finish day D. A fetch at exactly that instant saw the whole day
        everywhere, so it is durable — and ``<`` versus ``<=`` here is a
        one-character edit no other test in this class notices.
        """
        conn = _fresh_conn()
        _insert_download_day(
            conn, "test_source", self._DAY, downloaded_at="2024-06-16T12:00:00+00:00"
        )

        assert self._needed(conn) == []

    def test_a_second_before_noon_utc_the_next_day_is_not(self):
        """The other side of the same boundary.

        At 11:59:59 UTC on D+1 it is still 23:59:59 on day D in UTC-12, so a
        source keeping that calendar has not finished the day. All three
        built-in sources are US-based (UTC-5 to UTC-8), which is why the
        obvious rule — "the UTC *date* is past the day" — is not safe: it
        would call a fetch at 00:30 UTC on D+1 durable while PubMed's own
        day D still had five hours to run.
        """
        conn = _fresh_conn()
        _insert_download_day(
            conn, "test_source", self._DAY, downloaded_at="2024-06-16T11:59:59+00:00"
        )

        assert self._needed(conn) == [self._DAY]

    def test_an_offset_timestamp_is_compared_as_an_instant_not_as_a_wall_clock(self):
        """13:00+02:00 on D+1 is 11:00 UTC — still inside day D somewhere.

        Reading the date off the string, or dropping the offset, would call
        this durable. The comparison is between instants.
        """
        conn = _fresh_conn()
        _insert_download_day(
            conn, "test_source", self._DAY, downloaded_at="2024-06-16T13:00:00+02:00"
        )

        assert self._needed(conn) == [self._DAY]

    def test_a_timestamp_that_cannot_be_read_is_not_read_as_durable(self, caplog):
        """Fail closed, and say so.

        ``downloaded_at`` is ``NOT NULL TEXT`` and bmlib has always written an
        aware UTC ISO timestamp, so an unreadable value came from somewhere
        else. Treating it as durable would lose the day permanently; the
        re-fetch it triggers rewrites the column, so it heals itself.
        """
        conn = _fresh_conn()
        _insert_download_day(conn, "test_source", self._DAY, downloaded_at="not a timestamp")

        with caplog.at_level("WARNING", logger="bmlib.publications.sync"):
            assert self._needed(conn) == [self._DAY]

        # All three halves matter: the phrase is unique to this line, the
        # value is what the operator has to recognise, and `(source, date)` is
        # the row's actual key — without it the operator is told a row is bad
        # but not which one. Asserting only the phrase and the value let two
        # mutations of the identifying arguments survive.
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "unusable downloaded_at" in m
            and "'not a timestamp'" in m
            and f"test_source/{self._DAY.isoformat()}" in m
            for m in warnings
        ), warnings

    def test_a_naive_timestamp_is_not_read_as_durable(self):
        """A naive value must fail closed rather than raise.

        Comparing a naive datetime with an aware one raises ``TypeError``,
        which would abort the whole sync from inside day selection — before a
        single record is fetched — rather than cost one merge-idempotent
        re-fetch.
        """
        conn = _fresh_conn()
        _insert_download_day(conn, "test_source", self._DAY, downloaded_at="2024-06-20T12:00:00")

        assert self._needed(conn) == [self._DAY]

    def test_a_timestamp_that_is_not_a_string_is_not_read_as_durable(self):
        """The shape a schema change would produce, not a hypothetical one.

        ``downloaded_at`` is ``TEXT`` in both DDLs. Were the PostgreSQL side
        ever given a real timestamp type, psycopg2 would hand back a
        ``datetime`` — already aware, already correct, and not a string.
        Failing closed costs a re-fetch of every completed day; reading it
        through ``fromisoformat`` unguarded would raise ``TypeError`` from
        inside day selection and abort the sync instead.
        """
        assert not _day_was_over_when_fetched(
            "test_source", self._DAY, datetime(2024, 6, 20, 12, tzinfo=UTC)
        )

    def test_a_day_that_has_not_happened_yet_is_offered_again(self):
        """The same defect one step further out, and the rule covers it too.

        A caller whose ``date_to`` is in the future had those days stored
        ``completed`` with no records and never revisited. Under the old
        special case only ``today`` was re-offered.
        """
        conn = _fresh_conn()
        future = date.today() + timedelta(days=3)
        _insert_download_day(conn, "test_source", future)

        assert _days_needing_fetch(conn, "test_source", date_from=future, date_to=future) == [
            future
        ]

    def test_today_is_still_offered_although_the_special_case_is_gone(self):
        """The claim that made removing ``if current == today`` safe.

        12:00 UTC on D+1 is not merely late enough to be safe — it is exactly
        the instant beyond which "now" can no longer fall inside day D
        anywhere on earth. So a row written during today is always earlier
        than its own boundary, in every timezone, and the timestamp rule
        subsumes the special case rather than approximating it.
        """
        conn = _fresh_conn()
        today = date.today()
        _insert_download_day(conn, "test_source", today)

        assert _days_needing_fetch(conn, "test_source", date_from=today, date_to=today) == [today]

    def test_a_day_synced_as_today_is_not_stored_as_durable(self):
        """The seam: what ``sync()`` actually writes, read by the actual rule.

        The rule can only be exercised against a *past* day, and the clock
        cannot be advanced, so this asserts on the stored value directly —
        the day ``sync()`` has just recorded ``completed`` is one the
        durability rule will offer again tomorrow.
        """
        conn = _fresh_conn()
        today = date.today()
        records = [_sample_raw_record(source="test_source")]

        sync(
            conn,
            sources=["test_source"],
            date_from=today,
            date_to=today,
            _fetcher_override={"test_source": _make_fake_fetcher(records)},
        )

        row = fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source = ? AND date = ?",
            ("test_source", today.isoformat()),
        )
        assert row["status"] == "completed"
        assert not _day_was_over_when_fetched("test_source", today, row["downloaded_at"])

    def test_each_day_in_a_window_is_judged_against_its_own_boundary(self):
        """Every other test in this class uses a one-day window, and that hid a bug.

        Review found that passing ``date_from`` where the loop passes
        ``current`` — judging every day in the window against the *first*
        day's boundary — survived the entire suite, because with
        ``date_from == date_to`` the two are the same object. Under that
        mutant a 14:00 UTC cron on the default two-day window stores **today**
        as durable and loses the rest of the day: #95 exactly, reintroduced
        silently.

        So the window here spans two days whose answers differ, which is also
        the shape ``sync()`` actually runs with.
        """
        conn = _fresh_conn()
        first, second = date(2024, 6, 15), date(2024, 6, 16)
        # Both timestamps sit past `first`'s boundary (2024-06-16T12:00Z) and
        # before `second`'s (2024-06-17T12:00Z), which is what makes the two
        # days' answers differ. Deliberately clear of the boundary instant so
        # this stays a test about *which day* is judged and leaves `>=` versus
        # `>` to `test_noon_utc_the_next_day_is_late_enough` alone.
        _insert_download_day(conn, "test_source", first, downloaded_at="2024-06-16T18:00:00+00:00")
        _insert_download_day(conn, "test_source", second, downloaded_at="2024-06-16T20:00:00+00:00")

        assert _days_needing_fetch(conn, "test_source", date_from=first, date_to=second) == [second]

    def test_a_timestamp_from_the_future_is_not_read_as_durable(self, caplog):
        """The one shape that read cleanly and still could not be true.

        The guard was loud about a value it could not *parse* and silent about
        one asserting the day was fetched tomorrow. A restored backup, a host
        resuming with a bad RTC, or an external writer puts a past-the-boundary
        timestamp on a day that is still running; every such day then reads
        durable forever at ``recheck_days=0``. That is #95's own failure mode —
        permanent, invisible loss — so it fails closed and warns like the rest.
        """
        conn = _fresh_conn()
        today = date.today()
        _insert_download_day(conn, "test_source", today, downloaded_at="9999-12-31T00:00:00+00:00")

        with caplog.at_level("WARNING", logger="bmlib.publications.sync"):
            assert _days_needing_fetch(conn, "test_source", date_from=today, date_to=today) == [
                today
            ]

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "downloaded_at in the future" in m and f"test_source/{today.isoformat()}" in m
            for m in warnings
        ), warnings

    def test_a_clock_a_few_minutes_fast_is_still_believed(self):
        """Negative control for the bound above: ordinary skew is not a defect.

        Without a tolerance the future check fires on hosts whose clocks
        differ by seconds, costing a re-fetch every run. The asymmetry decides
        the size — too tight costs one merged day, too loose loses one
        permanently — so the tolerance is small, not generous.
        """
        conn = _fresh_conn()
        soon = (datetime.now(tz=UTC) + timedelta(minutes=1)).isoformat()
        _insert_download_day(conn, "test_source", self._DAY, downloaded_at=soon)

        assert self._needed(conn) == []

    def test_a_non_string_timestamp_fails_closed_through_day_selection_too(self, monkeypatch):
        """The non-string path, exercised where it would actually be hit.

        ``test_a_timestamp_that_is_not_a_string_is_not_read_as_durable`` calls
        the rule directly, so its claim about aborting *day selection* is
        argued but never run — and the column is ``TEXT``, so no insert can
        produce the shape. Faking the row is the only way to cover the path a
        PostgreSQL DDL change would create.
        """
        # Via `sys.modules`, not `import bmlib.publications.sync as ...`: the
        # package exports a `sync` *function*, which shadows the submodule of
        # the same name on the parent, so the plain import binds the function.
        sync_module = sys.modules["bmlib.publications.sync"]

        row = {
            "date": self._DAY.isoformat(),
            "status": "completed",
            "downloaded_at": datetime(2024, 6, 20, 12, tzinfo=UTC),
            "last_verified_at": None,
        }
        monkeypatch.setattr(sync_module, "fetch_all", lambda *a, **kw: [row])

        assert self._needed(_fresh_conn()) == [self._DAY]

    def test_an_unusable_last_verified_at_rechecks_rather_than_raising(self, caplog):
        """The sibling column, which was still being read raw.

        ``datetime.fromisoformat`` on a corrupt ``last_verified_at`` raised
        ``ValueError`` out of day selection, escaping ``sync()`` — whose
        ``try`` carries only a ``finally`` — and killing the whole
        multi-source run before a single record was fetched, ``SyncReport``
        and all. Reachable only when ``downloaded_at`` is *good* and this one
        is not, since a bad ``downloaded_at`` short-circuits above it.
        """
        conn = _fresh_conn()
        _insert_download_day(
            conn,
            "test_source",
            self._DAY,
            downloaded_at="2024-06-20T00:00:00+00:00",
            last_verified_at="not a timestamp",
        )

        with caplog.at_level("WARNING", logger="bmlib.publications.sync"):
            assert _days_needing_fetch(
                conn, "test_source", date_from=self._DAY, date_to=self._DAY, recheck_days=7
            ) == [self._DAY]

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "unusable last_verified_at" in m and f"test_source/{self._DAY.isoformat()}" in m
            for m in warnings
        ), warnings

    def test_a_non_string_last_verified_at_rechecks_rather_than_raising(self, monkeypatch):
        """The non-string shape on the sibling column, which is not the same path.

        ``downloaded_at`` short-circuits before rule 4, so a DDL change that
        moved *both* columns to a real timestamp type never reaches this one.
        Only a change to `last_verified_at` alone does — but the ``isinstance``
        guard exists either way, and without a test removing it survives the
        whole suite while turning a recheck into a ``TypeError`` that aborts
        the run.
        """
        sync_module = sys.modules["bmlib.publications.sync"]
        row = {
            "date": self._DAY.isoformat(),
            "status": "completed",
            "downloaded_at": "2024-06-20T00:00:00+00:00",
            "last_verified_at": datetime(2024, 6, 20, tzinfo=UTC),
        }
        monkeypatch.setattr(sync_module, "fetch_all", lambda *a, **kw: [row])

        assert _days_needing_fetch(
            _fresh_conn(), "test_source", date_from=self._DAY, date_to=self._DAY, recheck_days=7
        ) == [self._DAY]

    def test_a_null_last_verified_at_rechecks_without_warning(self, caplog):
        """Negative control: ``NULL`` is the documented state, not a defect.

        Rule 4 already answers "never verified" by rechecking. Warning on it
        would fire for every row of a fresh install with ``recheck_days`` set,
        which is how a real warning gets tuned out.
        """
        conn = _fresh_conn()
        _insert_download_day(
            conn, "test_source", self._DAY, downloaded_at="2024-06-20T00:00:00+00:00"
        )
        # The helper substitutes now for a falsy `last_verified_at`, so the
        # NULL this test is about has to be written separately.
        execute(
            conn,
            "UPDATE download_days SET last_verified_at = NULL WHERE source = ? AND date = ?",
            ("test_source", self._DAY.isoformat()),
        )
        conn.commit()

        with caplog.at_level("WARNING", logger="bmlib.publications.sync"):
            assert _days_needing_fetch(
                conn, "test_source", date_from=self._DAY, date_to=self._DAY, recheck_days=7
            ) == [self._DAY]

        assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []


class TestSyncRefusesAWindowItCannotWalk:
    """Issue #99 — a caller bug must not escape as ``OverflowError``.

    ``sync()``'s ``try`` carries only a ``finally``, so an exception raised
    from inside day selection kills the whole multi-source run and loses the
    ``SyncReport`` with it — before a single record is fetched. That is worse
    than the per-day losses the rest of this module guards against, because it
    is total rather than per-day.

    The fix is validation at ``sync()``'s entry, **not** an
    ``except OverflowError`` at the helpers: swallowing it there would convert
    a caller bug into a silent re-fetch, which is the failure mode the whole
    #88–#95 family exists to remove.
    """

    @staticmethod
    def _fetcher():
        return _make_fake_fetcher([])

    def test_a_window_ending_on_the_last_representable_date_is_rejected(self):
        """Day selection asks what day follows its last one, so ``date.max`` cannot be it.

        This drives the empty-database path, where the loop's own
        ``current += timedelta(days=1)`` is the operation that would overflow.
        The durability rule's ``day + timedelta(days=1)`` is a *second*
        overflow source on the same input and is unreachable from here, since
        it needs a stored completed row;
        :meth:`test_both_overflow_sources_are_real_without_the_guard` is what
        reproduces each of them directly.
        """
        conn = _fresh_conn()

        with pytest.raises(ValueError, match="date_to"):
            sync(
                conn,
                sources=["test_source"],
                date_from=date.max,
                date_to=date.max,
                _fetcher_override={"test_source": self._fetcher()},
            )

    def test_both_overflow_sources_are_real_without_the_guard(self):
        """The precondition ``_validate_window`` exists to guarantee, reproduced.

        Called directly, so the entry-point guard is bypassed and the two
        distinct overflows are visible one at a time — which is what makes
        the rejection above a fix rather than a ritual. Rule 1 (no row at
        all) reaches the loop's own increment; a stored completed row reaches
        the durability rule's ``day + timedelta(days=1)`` first, before the
        loop ever increments.
        """
        with pytest.raises(OverflowError):
            _days_needing_fetch(_fresh_conn(), "test_source", date_from=date.max, date_to=date.max)

        conn = _fresh_conn()
        _insert_download_day(conn, "test_source", date.max)

        with pytest.raises(OverflowError):
            _days_needing_fetch(conn, "test_source", date_from=date.max, date_to=date.max)

    def test_a_window_ending_one_day_earlier_still_runs(self):
        """Positive control at the boundary: ``date.max - 1 day`` is legal end to end.

        Without this, widening the rejection — to ``>= date.max - 30 days``,
        say — passes the whole suite. The stored completed row matters: it is
        what makes the durability rule's own ``day + timedelta(days=1)`` run
        on the largest day the guard accepts, landing exactly on ``date.max``.
        """
        conn = _fresh_conn()
        last = date.max - timedelta(days=1)
        _insert_download_day(conn, "test_source", last)

        report = sync(
            conn,
            sources=["test_source"],
            date_from=last,
            date_to=last,
            _fetcher_override={"test_source": self._fetcher()},
        )

        assert report.errors == []
        assert "test_source" in report.sources_synced

    def test_a_negative_recheck_days_is_rejected(self):
        """Silently ignored today: ``recheck_days > 0`` is simply False.

        A caller passing a negative window has misunderstood the parameter,
        and the current answer — recheck nothing — is the opposite of what
        they asked for, delivered without a word.
        """
        conn = _fresh_conn()

        with pytest.raises(ValueError, match="recheck_days"):
            sync(
                conn,
                sources=["test_source"],
                date_from=self._day(),
                date_to=self._day(),
                recheck_days=-1,
                _fetcher_override={"test_source": self._fetcher()},
            )

    def test_a_recheck_window_reaching_before_the_calendar_is_rejected(self):
        """``today - timedelta(days=recheck_days)`` has to land on a real date.

        No stored row is set up, deliberately: validation raises before day
        selection issues a single query, so a row here would read as though
        the recheck path were being exercised when nothing ever looks at it.
        """
        conn = _fresh_conn()

        with pytest.raises(ValueError, match="recheck_days"):
            sync(
                conn,
                sources=["test_source"],
                date_from=self._day(),
                date_to=self._day(),
                recheck_days=10**9,
                _fetcher_override={"test_source": self._fetcher()},
            )

    def test_an_ordinary_window_and_recheck_window_still_run(self):
        """Negative control: the guard must reject only what it names.

        The stored row is what makes this cover the arithmetic #99 is about.
        Against an empty database, day selection takes the "no row at all"
        branch and ``today - timedelta(days=recheck_days)`` is never
        evaluated, so an accepted ``recheck_days`` proves only that the guard
        let it past. A completed row last verified long ago forces rule 4,
        and the day comes back because the recheck window really was walked.
        """
        conn = _fresh_conn()
        day = self._day()
        _insert_download_day(
            conn,
            "test_source",
            day,
            last_verified_at=(datetime.now(tz=UTC) - timedelta(days=30)).isoformat(),
        )

        report = sync(
            conn,
            sources=["test_source"],
            date_from=day,
            date_to=day,
            recheck_days=7,
            _fetcher_override={"test_source": self._fetcher()},
        )

        assert report.days_processed == 1
        assert report.errors == []

    def test_the_deepest_recheck_the_calendar_allows_is_accepted(self):
        """Boundary. ``today - timedelta(days=(today - date.min).days)`` is ``date.min``.

        That is a real date, so the value is legal and must not be rejected —
        which is what pins the bound as ``>`` rather than ``>=``. The day is
        *not* re-offered, because a recheck window reaching back to the start
        of the calendar cannot make a row verified today look stale.
        """
        conn = _fresh_conn()
        day = self._day()
        _insert_download_day(conn, "test_source", day)

        report = sync(
            conn,
            sources=["test_source"],
            date_from=day,
            date_to=day,
            recheck_days=(date.today() - date.min).days,
            _fetcher_override={"test_source": self._fetcher()},
        )

        assert report.days_processed == 0
        assert report.errors == []

    def test_one_day_deeper_than_the_calendar_is_rejected(self):
        """The other side of the same boundary, so no looser bound passes.

        ``10**9`` alone leaves every bound between here and a billion
        indistinguishable — including one that would accept a value which
        really does overflow.
        """
        conn = _fresh_conn()

        with pytest.raises(ValueError, match="recheck_days"):
            sync(
                conn,
                sources=["test_source"],
                date_from=self._day(),
                date_to=self._day(),
                recheck_days=(date.today() - date.min).days + 1,
                _fetcher_override={"test_source": self._fetcher()},
            )

    def test_an_empty_window_is_still_the_ordinary_way_to_ask_for_nothing(self):
        """Deliberately **not** rejected, and pinned so it is not "tidied" into a raise.

        ``date_from > date_to`` is what the natural incremental-sync idiom
        produces once it has caught up — ``date_from = last_synced + 1 day``,
        ``date_to = today`` — so rejecting it would turn a caller that is up to
        date into a crashing one. It writes nothing and claims no day, which
        is why it is unlike the caller bugs above.
        """
        conn = _fresh_conn()
        today = date.today()

        report = sync(
            conn,
            sources=["test_source"],
            date_from=today,
            date_to=today - timedelta(days=1),
            _fetcher_override={"test_source": self._fetcher()},
        )

        assert report.days_processed == 0
        assert report.errors == []
        assert "test_source" in report.sources_synced

    def test_the_window_is_refused_before_an_http_client_is_built(self, monkeypatch):
        """The one claim in the fix that no other test reaches.

        ``sync()`` builds one ``httpx.Client`` *outside* the ``try`` whose
        ``finally`` closes it, so a validation raised after that point escapes
        with the connection pool still open. Every other test here passes
        ``_fetcher_override``, which skips the client entirely — so without
        this, moving the guard below the client build passes the whole suite.
        """
        httpx = pytest.importorskip("httpx")
        built: list[object] = []
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: built.append(object()))

        with pytest.raises(ValueError, match="date_to"):
            sync(_fresh_conn(), sources=["pubmed"], date_from=date.max, date_to=date.max)

        assert built == []

    def test_a_datetime_is_refused_where_a_date_is_required(self):
        """``datetime`` subclasses ``date``, so this is the one caller bug mypy allows.

        ``datetime.now()`` for ``date.today()`` is a far likelier slip than
        ``date.max``, and it defeats every value-based guard: ``datetime.max
        == date.max`` is ``False``, so nothing here fired before the type
        check existed.
        """
        conn = _fresh_conn()

        with pytest.raises(ValueError, match="date_to"):
            sync(
                conn,
                sources=["test_source"],
                date_from=self._day(),
                date_to=datetime.now(),
                _fetcher_override={"test_source": self._fetcher()},
            )

        with pytest.raises(ValueError, match="date_from"):
            sync(
                conn,
                sources=["test_source"],
                date_from=datetime.now(),
                date_to=date.today(),
                _fetcher_override={"test_source": self._fetcher()},
            )

    def test_a_datetime_window_never_reaches_the_download_days_table(self):
        """The reason the type check is a fix and not tidiness.

        A ``datetime`` on *both* ends does not raise at all: the comparison
        and the increment both work, and the run reports success while
        writing ``download_days.date`` values carrying a time component. No
        later date-keyed lookup can ever match such a row, so the day is
        re-fetched forever and the table accumulates rows nothing reads.
        """
        conn = _fresh_conn()
        moment = datetime.now() - timedelta(days=2)

        with pytest.raises(ValueError):
            sync(
                conn,
                sources=["test_source"],
                date_from=moment,
                date_to=moment,
                _fetcher_override={"test_source": self._fetcher()},
            )

        assert fetch_all(conn, "SELECT date FROM download_days") == []

    def test_a_date_shaped_string_is_refused(self):
        """``DownloadDay.date`` and ``FetchResult.date`` are both ``str`` in this module.

        So a caller reading a date back out of the model and handing it
        straight to ``sync()`` is a natural mistake, and it used to escape as
        ``AttributeError`` from inside day selection — losing the report.
        """
        conn = _fresh_conn()

        with pytest.raises(ValueError, match="date_from"):
            sync(
                conn,
                sources=["test_source"],
                date_from=self._day().isoformat(),
                date_to=date.today(),
                _fetcher_override={"test_source": self._fetcher()},
            )

    def test_a_recheck_days_that_is_not_a_whole_number_is_refused(self):
        """``nan`` slipped both value guards, one comparison away from the negative case.

        ``nan < 0`` and ``nan > bound`` are both ``False``, so it passed
        validation and then silently disabled rechecking at
        ``recheck_days > 0`` — the exact silent path this issue closed for
        negative values.
        """
        conn = _fresh_conn()

        for bad in (float("nan"), 1.5, "7", None):
            with pytest.raises(ValueError, match="recheck_days"):
                sync(
                    conn,
                    sources=["test_source"],
                    date_from=self._day(),
                    date_to=self._day(),
                    recheck_days=bad,
                    _fetcher_override={"test_source": self._fetcher()},
                )

    @staticmethod
    def _day():
        return date.today() - timedelta(days=5)


class TestAFetcherThatBreaksItsContractFailsOnlyItsDay:
    """A registered fetcher must not be able to take the whole run down.

    ``register_source()`` is public, so a third-party fetcher is exactly the
    caller that will get the contract wrong. The ``except Exception`` around
    the call already absorbs a fetcher that *raises*; what escaped was a
    fetcher that *returns* — successfully — something without a ``.status``.
    The ``AttributeError`` came from ``_resolve_day_status``, outside that
    handler's reach, and propagated through the ``finally`` and out of
    ``sync()``, losing the ``SyncReport`` for every source.
    """

    @staticmethod
    def _day():
        return date.today() - timedelta(days=3)

    def test_a_fetcher_that_forgets_to_return_fails_its_day_not_the_run(self, caplog):
        """The commonest shape of the bug, and the one that lost the report."""
        conn = _fresh_conn()

        def forgot_the_return(client, day, **kwargs):
            return None

        with caplog.at_level("ERROR"):
            report = sync(
                conn,
                sources=["good", "bad"],
                date_from=self._day(),
                date_to=self._day(),
                _fetcher_override={"good": _make_fake_fetcher([]), "bad": forgot_the_return},
            )

        assert "good" in report.sources_synced
        assert any("bad" in error for error in report.errors)
        assert (
            fetch_one(
                conn,
                "SELECT status FROM download_days WHERE source = ?",
                ("bad",),
            )["status"]
            == "failed"
        )

    def test_the_offending_type_is_named(self, caplog):
        """A bare message cannot tell a broken fetcher from a broken source."""
        conn = _fresh_conn()

        with caplog.at_level("ERROR"):
            sync(
                conn,
                sources=["bad"],
                date_from=self._day(),
                date_to=self._day(),
                _fetcher_override={"bad": lambda client, day, **kw: {"status": "completed"}},
            )

        assert any("dict" in record.getMessage() for record in caplog.records)

    def test_a_well_behaved_fetcher_is_untouched(self):
        """Negative control: the guard rejects only what is not a FetchResult."""
        conn = _fresh_conn()

        report = sync(
            conn,
            sources=["good"],
            date_from=self._day(),
            date_to=self._day(),
            _fetcher_override={"good": _make_fake_fetcher([])},
        )

        assert report.errors == []
        assert report.days_processed == 1


class TestAWindowReachingIntoTheFutureSaysSo:
    """A day that has not happened cannot ever be recorded durably.

    ``_day_was_over_when_fetched`` compares against 12:00 UTC on the day
    after, which for a future day is unsatisfiable — so the row is written
    ``completed`` and re-offered on every run for the life of the
    installation. That is the permanent, invisible cost this module's other
    rules exist to keep out, and it was reported at no log level and in no
    field of the ``SyncReport``.

    Rejecting the window was considered and refused: the past half of a
    window ending tomorrow is perfectly fetchable, and raising would throw it
    away too. Making the cost answerable from the return value is what
    ``SyncReport.notes`` is already for.
    """

    def test_a_future_window_is_reported_as_a_note(self, caplog):
        """Visible in the return value, not only in a log line."""
        conn = _fresh_conn()
        ahead = date.today() + timedelta(days=3)

        with caplog.at_level("WARNING"):
            report = sync(
                conn,
                sources=["test_source"],
                date_from=date.today(),
                date_to=ahead,
                _fetcher_override={"test_source": _make_fake_fetcher([])},
            )

        assert any("future" in note for note in report.notes)
        assert any("future" in record.getMessage() for record in caplog.records)

    def test_a_window_ending_today_says_nothing(self):
        """Negative control: the ordinary default window must stay quiet."""
        conn = _fresh_conn()

        report = sync(
            conn,
            sources=["test_source"],
            date_from=date.today() - timedelta(days=1),
            date_to=date.today(),
            _fetcher_override={"test_source": _make_fake_fetcher([])},
        )

        assert report.notes == []


class TestDayPartCheckpoints:
    """Checkpoints are what makes a very large day resumable."""

    def test_a_checkpoint_round_trips(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        cp = PartCheckpoint(
            part_scheme="edat-range",
            part_key="edat:2023-04-10:2023-08-31",
            promised=9375,
            record_count=9375,
        )

        _record_day_part(conn, "pubmed", date(2024, 1, 1), cp)

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 1)) == {cp.part_key: cp}

    def test_recording_the_same_part_twice_updates_rather_than_duplicates(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        key = "edat:2023-04-10:2023-08-31"
        _record_day_part(
            conn, "pubmed", date(2024, 1, 1), PartCheckpoint("edat-range", key, 10, 10)
        )
        _record_day_part(
            conn, "pubmed", date(2024, 1, 1), PartCheckpoint("edat-range", key, 12, 12)
        )

        stored = _load_day_parts(conn, "pubmed", date(2024, 1, 1))

        assert len(stored) == 1
        assert stored[key].promised == 12

    def test_parts_are_scoped_to_their_source_and_day(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        cp = PartCheckpoint("edat-range", "edat:2023-04-10:2023-08-31", 1, 1)
        _record_day_part(conn, "pubmed", date(2024, 1, 1), cp)

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 2)) == {}
        assert _load_day_parts(conn, "biorxiv", date(2024, 1, 1)) == {}

    def test_clearing_removes_only_that_day(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        cp = PartCheckpoint("edat-range", "edat:2023-04-10:2023-08-31", 1, 1)
        _record_day_part(conn, "pubmed", date(2024, 1, 1), cp)
        _record_day_part(conn, "pubmed", date(2024, 1, 2), cp)

        _clear_day_parts(conn, "pubmed", date(2024, 1, 1))

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 1)) == {}
        assert _load_day_parts(conn, "pubmed", date(2024, 1, 2)) == {cp.part_key: cp}
