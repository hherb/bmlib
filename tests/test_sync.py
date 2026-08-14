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
from bmlib.fulltext.models import FullTextSourceEntry
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
        from bmlib.db import fetch_one

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

        from bmlib.db import fetch_one

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

        from bmlib.db import fetch_one

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
