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

"""Backend-parity tests — every one runs on SQLite and on PostgreSQL.

The publications layer used to be SQLite-only. These tests pin the behaviour
that must hold identically on both backends, plus the commit semantics other
projects depend on: a bare :func:`bmlib.db.transaction` block always commits,
and only nesting inside another block defers the commit.

PostgreSQL runs only when ``BMLIB_TEST_POSTGRESQL_DSN`` is set (see
``conftest.py``); otherwise the PostgreSQL half of each test skips.
"""

from __future__ import annotations

import threading
from datetime import date

import pytest

from bmlib.db import (
    create_tables,
    execute,
    fetch_all,
    fetch_scalar,
    is_sqlite,
    owns_commit,
    placeholder,
    table_exists,
    transaction,
    transaction_depth,
)
from bmlib.publications.models import (
    FetchedRecord,
    FetchResult,
    FullTextSource,
    Publication,
    RetractionNature,
    RetractionNotice,
)
from bmlib.publications.retractions import (
    is_retracted,
    lookup_retractions,
    store_retraction_notices,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import (
    add_fulltext_source,
    get_publication_by_doi,
    get_publication_by_pmid,
    store_publication,
)
from bmlib.publications.sync import sync


def _pub(**kwargs) -> Publication:
    """Build a Publication with sensible defaults for the required fields."""
    kwargs.setdefault("title", "A paper")
    kwargs.setdefault("sources", ["pubmed"])
    kwargs.setdefault("first_seen_source", "pubmed")
    return Publication(**kwargs)


def _count(conn, table: str) -> int:
    """Return the row count of *table*."""
    return fetch_scalar(conn, f"SELECT COUNT(*) FROM {table}")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_ensure_schema_creates_all_tables(self, backend_conn):
        ensure_schema(backend_conn)

        for table in ("publications", "fulltext_sources", "download_days", "retraction_notices"):
            assert table_exists(backend_conn, table)

    def test_the_record_id_is_unique(self, backend_conn):
        # A full UNIQUE constraint, not the partial index publications uses
        # for doi/pmid: ON CONFLICT cannot infer a partial index without
        # repeating its predicate, and a retraction notice always has a
        # Record ID so there is no reason to accept a null.
        ensure_schema(backend_conn)
        ph = placeholder(backend_conn)
        columns = "(record_id, nature, reasons, created_at, updated_at)"
        values = f"({', '.join([ph] * 5)})"

        execute(
            backend_conn,
            f"INSERT INTO retraction_notices {columns} VALUES {values}",
            ("rw-1", "retraction", "[]", "2026-01-01", "2026-01-01"),
        )

        # The two drivers raise unrelated IntegrityError classes with no
        # shared base, so the expected class is selected per backend rather
        # than weakened to a bare Exception.
        if is_sqlite(backend_conn):
            import sqlite3

            expected: type[Exception] = sqlite3.IntegrityError
        else:
            import psycopg2

            expected = psycopg2.IntegrityError

        with pytest.raises(expected):
            execute(
                backend_conn,
                f"INSERT INTO retraction_notices {columns} VALUES {values}",
                ("rw-1", "retraction", "[]", "2026-01-01", "2026-01-01"),
            )

        # PostgreSQL leaves the transaction aborted after an integrity error,
        # so every later statement on this connection -- including the
        # fixture's teardown -- fails until it is rolled back.
        backend_conn.rollback()

    def test_ensure_schema_is_idempotent(self, backend_conn):
        ensure_schema(backend_conn)
        store_publication(backend_conn, _pub(doi="10.1234/a"))

        ensure_schema(backend_conn)

        assert _count(backend_conn, "publications") == 1

    def test_ensure_schema_adds_pmcid_to_an_older_database(self, backend_conn):
        """A database created before ``pmcid`` existed gains the column."""
        ensure_schema(backend_conn)
        with transaction(backend_conn):
            execute(backend_conn, "ALTER TABLE publications DROP COLUMN pmcid")

        ensure_schema(backend_conn)

        # The added column must be committed, not left pending on the
        # connection — otherwise it vanishes when the caller disconnects.
        backend_conn.rollback()
        store_publication(backend_conn, _pub(doi="10.1234/a", pmcid="PMC1"))
        assert get_publication_by_doi(backend_conn, "10.1234/a").pmcid == "PMC1"

    def test_reads_survive_a_database_that_has_not_been_upgraded_yet(self, backend_conn):
        """Rows still load from a database missing a post-release column.

        Someone can upgrade bmlib and read before calling ``ensure_schema()``.
        Writes cannot survive that — the INSERT names every column — but reads
        must, or the upgrade breaks the consumer at import-and-query time.
        """
        ensure_schema(backend_conn)
        store_publication(backend_conn, _pub(doi="10.1234/a", pmcid="PMC1"))
        with transaction(backend_conn):
            execute(backend_conn, "ALTER TABLE publications DROP COLUMN pmcid")

        pub = get_publication_by_doi(backend_conn, "10.1234/a")

        assert pub is not None
        assert pub.title == "A paper"
        assert pub.pmcid is None

    def test_a_same_named_table_in_another_schema_is_not_mistaken_for_ours(self, backend_conn):
        """Regression guard: the column check must be schema-qualified.

        ``information_schema.columns`` spans every schema the user can see, so
        an unqualified lookup answers about another consumer's ``publications``
        table. One that already has ``pmcid`` would make ours look up-to-date,
        the ALTER would be skipped, and the next write would fail.
        """
        if is_sqlite(backend_conn):
            pytest.skip("SQLite has no schemas; PRAGMA is scoped to the database")

        ensure_schema(backend_conn)
        with transaction(backend_conn):
            execute(backend_conn, "ALTER TABLE publications DROP COLUMN pmcid")
            execute(backend_conn, "CREATE SCHEMA decoy")
            execute(backend_conn, "CREATE TABLE decoy.publications (id SERIAL, pmcid TEXT)")

        try:
            ensure_schema(backend_conn)
            store_publication(backend_conn, _pub(doi="10.1234/a", pmcid="PMC1"))
            assert get_publication_by_doi(backend_conn, "10.1234/a").pmcid == "PMC1"
        finally:
            with transaction(backend_conn):
                execute(backend_conn, "DROP SCHEMA IF EXISTS decoy CASCADE")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TestStorage:
    @pytest.fixture(autouse=True)
    def _schema(self, backend_conn):
        ensure_schema(backend_conn)

    def test_insert_then_lookup(self, backend_conn):
        result = store_publication(
            backend_conn,
            _pub(doi="10.1234/abc", pmid="111", pmcid="PMC9", abstract="Text."),
        )

        assert result == "added"
        pub = get_publication_by_doi(backend_conn, "10.1234/abc")
        assert pub.pmid == "111"
        assert pub.pmcid == "PMC9"
        assert pub.abstract == "Text."
        assert pub.id is not None

    def test_doi_is_normalised_on_store_and_lookup(self, backend_conn):
        store_publication(backend_conn, _pub(doi="https://doi.org/10.1234/AbC"))

        assert get_publication_by_doi(backend_conn, "10.1234/abc") is not None
        assert _count(backend_conn, "publications") == 1

    def test_second_source_merges_rather_than_duplicates(self, backend_conn):
        store_publication(backend_conn, _pub(doi="10.1234/abc", sources=["pubmed"]))

        result = store_publication(
            backend_conn,
            _pub(doi="10.1234/abc", sources=["openalex"], first_seen_source="openalex"),
        )

        assert result == "merged"
        assert _count(backend_conn, "publications") == 1
        assert get_publication_by_doi(backend_conn, "10.1234/abc").sources == [
            "pubmed",
            "openalex",
        ]

    def test_merge_fills_nulls_without_overwriting(self, backend_conn):
        store_publication(backend_conn, _pub(doi="10.1234/abc", title="First", abstract=None))

        store_publication(
            backend_conn,
            _pub(doi="10.1234/abc", title="Second", abstract="Filled in", journal="Nature"),
        )

        pub = get_publication_by_doi(backend_conn, "10.1234/abc")
        assert pub.title == "First"
        assert pub.abstract == "Filled in"
        assert pub.journal == "Nature"

    def test_open_access_latches_on(self, backend_conn):
        """Once any source reports open access, a later record cannot unset it."""
        store_publication(backend_conn, _pub(doi="10.1234/abc", is_open_access=True))

        store_publication(backend_conn, _pub(doi="10.1234/abc", is_open_access=False))

        assert get_publication_by_doi(backend_conn, "10.1234/abc").is_open_access is True

    def test_open_access_can_be_set_by_a_later_record(self, backend_conn):
        store_publication(backend_conn, _pub(doi="10.1234/abc", is_open_access=False))

        store_publication(backend_conn, _pub(doi="10.1234/abc", is_open_access=True))

        assert get_publication_by_doi(backend_conn, "10.1234/abc").is_open_access is True

    def test_split_identity_is_consolidated(self, backend_conn):
        """A DOI row and a PMID row for one work collapse when a record links them."""
        store_publication(backend_conn, _pub(doi="10.1234/abc", title="By DOI"))
        store_publication(backend_conn, _pub(pmid="999", title="By PMID", journal="Cell"))
        assert _count(backend_conn, "publications") == 2

        store_publication(backend_conn, _pub(doi="10.1234/abc", pmid="999"))

        assert _count(backend_conn, "publications") == 1
        pub = get_publication_by_doi(backend_conn, "10.1234/abc")
        assert pub.pmid == "999"
        assert pub.title == "By DOI"
        assert pub.journal == "Cell"
        assert get_publication_by_pmid(backend_conn, "999").id == pub.id

    def test_consolidation_moves_fulltext_sources_and_drops_duplicates(self, backend_conn):
        store_publication(backend_conn, _pub(doi="10.1234/abc"))
        store_publication(backend_conn, _pub(pmid="999"))
        keep_id = get_publication_by_doi(backend_conn, "10.1234/abc").id
        drop_id = get_publication_by_pmid(backend_conn, "999").id
        add_fulltext_source(backend_conn, keep_id, "epmc", "http://x/shared", "xml")
        add_fulltext_source(backend_conn, drop_id, "epmc", "http://x/shared", "xml")
        add_fulltext_source(backend_conn, drop_id, "epmc", "http://x/only-on-drop", "pdf")

        store_publication(backend_conn, _pub(doi="10.1234/abc", pmid="999"))

        ph = placeholder(backend_conn)
        urls = {
            row["url"]
            for row in fetch_all(
                backend_conn,
                f"SELECT url FROM fulltext_sources WHERE publication_id = {ph}",
                (keep_id,),
            )
        }
        assert urls == {"http://x/shared", "http://x/only-on-drop"}
        assert _count(backend_conn, "fulltext_sources") == 2

    def test_add_fulltext_source_reports_whether_it_inserted(self, backend_conn):
        store_publication(backend_conn, _pub(doi="10.1234/abc"))
        pub_id = get_publication_by_doi(backend_conn, "10.1234/abc").id

        assert add_fulltext_source(backend_conn, pub_id, "epmc", "http://x/1", "xml") is True
        assert add_fulltext_source(backend_conn, pub_id, "epmc", "http://x/1", "xml") is False

    def test_store_publication_persists_fulltext_sources(self, backend_conn):
        store_publication(
            backend_conn,
            _pub(doi="10.1234/abc"),
            fulltext_sources=[
                FullTextSource(publication_id=0, source="epmc", url="http://x/1", format="xml")
            ],
        )

        assert _count(backend_conn, "fulltext_sources") == 1


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TestTransactions:
    def test_standalone_block_commits(self, backend_conn):
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)

        with transaction(backend_conn):
            execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("a",))

        backend_conn.rollback()  # a no-op if the commit really happened
        assert _count(backend_conn, "t") == 1

    def test_block_after_a_bare_query_still_commits(self, backend_conn):
        """Regression guard: a prior SELECT must not be mistaken for nesting.

        psycopg2 opens a transaction on the first statement of any kind, so
        reading the driver's transaction status here would classify this block
        as nested and silently skip its commit.
        """
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)
        fetch_all(backend_conn, "SELECT * FROM t")

        with transaction(backend_conn):
            execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("a",))

        backend_conn.rollback()
        assert _count(backend_conn, "t") == 1

    def test_failure_rolls_back(self, backend_conn):
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)

        with pytest.raises(RuntimeError):
            with transaction(backend_conn):
                execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("a",))
                raise RuntimeError("boom")

        assert _count(backend_conn, "t") == 0

    def test_nested_block_defers_the_commit_to_the_outer_one(self, backend_conn):
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)

        with pytest.raises(RuntimeError):
            with transaction(backend_conn):
                with transaction(backend_conn):
                    execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("a",))
                raise RuntimeError("outer fails after the inner block finished")

        assert _count(backend_conn, "t") == 0

    def test_inner_failure_rolls_back_only_the_inner_writes(self, backend_conn):
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)

        with transaction(backend_conn):
            execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("outer",))
            with pytest.raises(RuntimeError):
                with transaction(backend_conn):
                    execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("inner",))
                    raise RuntimeError("boom")

        backend_conn.rollback()
        rows = [r["v"] for r in fetch_all(backend_conn, "SELECT v FROM t")]
        assert rows == ["outer"]

    def test_depth_returns_to_zero(self, backend_conn):
        assert transaction_depth(backend_conn) == 0

        with transaction(backend_conn):
            assert transaction_depth(backend_conn) == 1
            with transaction(backend_conn):
                assert transaction_depth(backend_conn) == 2

        assert transaction_depth(backend_conn) == 0

    def test_depth_returns_to_zero_after_failure(self, backend_conn):
        with pytest.raises(RuntimeError):
            with transaction(backend_conn):
                raise RuntimeError("boom")

        assert transaction_depth(backend_conn) == 0

    def test_a_block_on_another_thread_does_not_look_like_nesting(self, backend_conn):
        """Regression guard: nesting is per call stack, not per connection.

        Counting opens by connection alone let one thread's open block make an
        unrelated outermost block on a second thread look nested — so the
        second thread opened a savepoint, never committed, and its write was
        lost with no error raised.
        """
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)
        started = threading.Event()
        release = threading.Event()

        def hold_a_block():
            with transaction(backend_conn):
                started.set()
                release.wait(timeout=5)

        holder = threading.Thread(target=hold_a_block)
        holder.start()
        try:
            started.wait(timeout=5)
            # This thread has no block of its own open, so it owns its commit.
            assert transaction_depth(backend_conn) == 0
            assert owns_commit(backend_conn) is True
            with transaction(backend_conn):
                execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("committed",))
        finally:
            release.set()
            holder.join(timeout=5)

        backend_conn.rollback()
        assert [r["v"] for r in fetch_all(backend_conn, "SELECT v FROM t")] == ["committed"]

    def test_owns_commit_tracks_the_block_depth(self, backend_conn):
        assert owns_commit(backend_conn) is True
        with transaction(backend_conn):
            assert owns_commit(backend_conn) is False
        assert owns_commit(backend_conn) is True

    def test_fetch_scalar_returns_the_first_column(self, backend_conn):
        create_tables(backend_conn, "CREATE TABLE t (v TEXT)")
        ph = placeholder(backend_conn)
        with transaction(backend_conn):
            execute(backend_conn, f"INSERT INTO t (v) VALUES ({ph})", ("a",))

        assert fetch_scalar(backend_conn, "SELECT v FROM t") == "a"
        assert fetch_scalar(backend_conn, "SELECT v FROM t WHERE v = 'nope'") is None


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _fetcher_returning(records: list[FetchedRecord], status: str = "completed"):
    """Build a fetcher stub that emits *records* for whatever day it is given."""

    def fetcher(client, day, *, on_record, on_progress=None, **config):
        for record in records:
            on_record(record)
        return FetchResult(
            source="testsource",
            date=day.isoformat(),
            record_count=len(records),
            status=status,
            error=None if status == "completed" else "fetch failed",
        )

    return fetcher


class TestSync:
    def test_sync_stores_records_and_tracks_the_day(self, backend_conn):
        day = date(2026, 1, 15)
        records = [
            FetchedRecord(title="One", source="testsource", doi="10.1/one", pmc_id="PMC1"),
            FetchedRecord(title="Two", source="testsource", pmid="222"),
        ]

        report = sync(
            backend_conn,
            sources=["testsource"],
            date_from=day,
            date_to=day,
            _fetcher_override={"testsource": _fetcher_returning(records)},
        )

        assert report.records_added == 2
        assert report.records_failed == 0
        assert report.errors == []
        assert _count(backend_conn, "publications") == 2
        # A PMID-only record is stored, not dropped.
        assert get_publication_by_pmid(backend_conn, "222") is not None
        # pmc_id survives the FetchedRecord → Publication conversion.
        assert get_publication_by_doi(backend_conn, "10.1/one").pmcid == "PMC1"

        days = fetch_all(
            backend_conn, "SELECT source, date, status, record_count FROM download_days"
        )
        assert len(days) == 1
        assert days[0]["status"] == "completed"
        assert days[0]["record_count"] == 2

    def test_a_completed_day_is_not_refetched(self, backend_conn):
        day = date(2026, 1, 15)
        records = [FetchedRecord(title="One", source="testsource", doi="10.1/one")]
        override = {"testsource": _fetcher_returning(records)}
        sync(
            backend_conn,
            sources=["testsource"],
            date_from=day,
            date_to=day,
            _fetcher_override=override,
        )

        report = sync(
            backend_conn,
            sources=["testsource"],
            date_from=day,
            date_to=day,
            _fetcher_override=override,
        )

        assert report.days_processed == 0
        assert report.records_added == 0

    def test_a_failed_day_is_recorded_and_refetched(self, backend_conn):
        day = date(2026, 1, 15)
        records = [FetchedRecord(title="One", source="testsource", doi="10.1/one")]
        sync(
            backend_conn,
            sources=["testsource"],
            date_from=day,
            date_to=day,
            _fetcher_override={"testsource": _fetcher_returning(records, status="failed")},
        )
        assert fetch_scalar(backend_conn, "SELECT status FROM download_days") == "failed"

        report = sync(
            backend_conn,
            sources=["testsource"],
            date_from=day,
            date_to=day,
            _fetcher_override={"testsource": _fetcher_returning(records)},
        )

        assert report.days_processed == 1
        assert fetch_scalar(backend_conn, "SELECT status FROM download_days") == "completed"

    def test_a_days_records_and_its_status_row_commit_together(self, backend_conn):
        """The whole day is one transaction, so a re-run sees all of it or none."""
        day = date(2026, 1, 15)
        records = [
            FetchedRecord(title=f"P{i}", source="testsource", doi=f"10.1/{i}") for i in range(5)
        ]

        sync(
            backend_conn,
            sources=["testsource"],
            date_from=day,
            date_to=day,
            _fetcher_override={"testsource": _fetcher_returning(records)},
        )

        backend_conn.rollback()
        assert _count(backend_conn, "publications") == 5
        assert _count(backend_conn, "download_days") == 1


# ---------------------------------------------------------------------------
# Retraction notices
# ---------------------------------------------------------------------------


def _notice(record_id, nature, date, doi="10.1/paper", pmid=None):
    """Build a RetractionNotice with the fields these tests care about."""
    return RetractionNotice(
        record_id=record_id,
        nature=nature,
        doi=doi,
        pmid=pmid,
        retraction_date=date,
        reasons=["Rogue Editor"],
    )


class TestRetractionStorage:
    def test_a_notice_round_trips(self, backend_conn):
        # Every field gets its own, distinct, recognisable value -- two
        # fields sharing a value cannot expose a transposition between them.
        # This test is mutation-tested: swapping notice_doi<->notice_pmid or
        # title<->journal in store_retraction_notices()'s values tuple must
        # make it fail (see CLAUDE.md / the retraction-watch design doc).
        ensure_schema(backend_conn)
        stored = RetractionNotice(
            record_id="rw-1",
            nature=RetractionNature.RETRACTION,
            doi="10.1111/original-paper-doi",
            pmid="10001001",
            notice_doi="10.2222/Notice-DOI",
            notice_pmid="20002002",
            title="Original Paper Title",
            journal="Original Paper Journal",
            retraction_date="2020-05-01",
            original_paper_date="2019-01-15",
            reasons=["Rogue Editor"],
            raw_nature="Retraction",
        )

        assert store_retraction_notices(backend_conn, [stored]) == 1

        (loaded,) = lookup_retractions(backend_conn, doi="10.1111/original-paper-doi")
        assert loaded.record_id == "rw-1"
        assert loaded.nature is RetractionNature.RETRACTION
        assert loaded.doi == "10.1111/original-paper-doi"
        assert loaded.pmid == "10001001"
        # _normalize_doi lower-cases, so notice_doi comes back lower-cased --
        # that transformation is intended, not a bug in this assertion.
        assert loaded.notice_doi == "10.2222/notice-doi"
        assert loaded.notice_pmid == "20002002"
        assert loaded.title == "Original Paper Title"
        assert loaded.journal == "Original Paper Journal"
        assert loaded.retraction_date == "2020-05-01"
        assert loaded.original_paper_date == "2019-01-15"
        assert loaded.reasons == ["Rogue Editor"]
        assert loaded.raw_nature == "Retraction"

    def test_reimporting_the_same_file_does_not_duplicate_notices(self, backend_conn):
        ensure_schema(backend_conn)
        notices = [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01")]

        store_retraction_notices(backend_conn, notices)
        store_retraction_notices(backend_conn, notices)

        assert _count(backend_conn, "retraction_notices") == 1

    def test_a_reimport_refreshes_a_changed_notice(self, backend_conn):
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn, [_notice("rw-1", RetractionNature.EXPRESSION_OF_CONCERN, "2020-05-01")]
        )

        store_retraction_notices(
            backend_conn, [_notice("rw-1", RetractionNature.RETRACTION, "2021-06-02")]
        )

        (loaded,) = lookup_retractions(backend_conn, doi="10.1/paper")
        assert loaded.nature is RetractionNature.RETRACTION
        assert loaded.retraction_date == "2021-06-02"

    def test_a_prefixed_uppercase_doi_matches_a_stored_notice(self, backend_conn):
        # Stored and looked up through the same normalisers store_publication
        # uses; a second normaliser that drifts is a lookup that silently
        # misses a paper that is in fact retracted.
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01", doi="10.1016/J.ABC")],
        )

        found = lookup_retractions(backend_conn, doi="https://doi.org/10.1016/j.abc")

        assert len(found) == 1

    def test_notices_come_back_newest_first(self, backend_conn):
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [
                _notice("rw-old", RetractionNature.RETRACTION, "2011-09-08"),
                _notice("rw-new", RetractionNature.CORRECTION, "2017-12-14"),
            ],
        )

        found = lookup_retractions(backend_conn, doi="10.1/paper")

        assert [n.record_id for n in found] == ["rw-new", "rw-old"]
        assert is_retracted(found) is True

    def test_an_undated_notice_sorts_after_a_dated_one_on_both_backends(self, backend_conn):
        # SQLite and PostgreSQL disagree about where NULLs land in a DESC
        # sort, so the ORDER BY has to say explicitly. Without that, the
        # "newest" notice differs by backend and is_retracted() follows it.
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [
                _notice("rw-dated", RetractionNature.RETRACTION, "2020-01-01"),
                _notice("rw-undated", RetractionNature.REINSTATEMENT, None),
            ],
        )

        found = lookup_retractions(backend_conn, doi="10.1/paper")

        assert found[0].record_id == "rw-dated"
        assert is_retracted(found) is True

    def test_a_lookup_by_pmid_finds_a_notice_stored_without_a_doi(self, backend_conn):
        ensure_schema(backend_conn)
        store_retraction_notices(
            backend_conn,
            [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01", doi=None, pmid="99")],
        )

        found = lookup_retractions(backend_conn, pmid="99")

        assert [n.record_id for n in found] == ["rw-1"]

    def test_a_lookup_with_no_identifier_is_a_programming_error(self, backend_conn):
        ensure_schema(backend_conn)

        with pytest.raises(ValueError):
            lookup_retractions(backend_conn)

    def test_a_lookup_with_only_unusable_identifiers_is_also_a_programming_error(
        self, backend_conn
    ):
        # doi="" / "   " / "https://doi.org/" and pmid="" all normalise away
        # to nothing -- the same "no usable identifier" situation as passing
        # None, and must raise rather than silently returning []. A caller
        # reading a TEXT column that stores "" instead of NULL hits exactly
        # this, and a silent [] there reads a retracted paper as clean.
        ensure_schema(backend_conn)

        with pytest.raises(ValueError):
            lookup_retractions(backend_conn, doi="")
        with pytest.raises(ValueError):
            lookup_retractions(backend_conn, doi="   ")
        with pytest.raises(ValueError):
            lookup_retractions(backend_conn, doi="https://doi.org/")
        with pytest.raises(ValueError):
            lookup_retractions(backend_conn, pmid="")

    def test_an_unknown_paper_has_no_notices(self, backend_conn):
        ensure_schema(backend_conn)

        assert lookup_retractions(backend_conn, doi="10.1/never-retracted") == []

    def test_a_caller_transaction_owns_the_commit(self, backend_conn):
        ensure_schema(backend_conn)

        with transaction(backend_conn):
            store_retraction_notices(
                backend_conn, [_notice("rw-1", RetractionNature.RETRACTION, "2020-05-01")]
            )
            assert not owns_commit(backend_conn)

        assert _count(backend_conn, "retraction_notices") == 1
