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

from datetime import date

import pytest

from bmlib.db import (
    create_tables,
    execute,
    fetch_all,
    fetch_scalar,
    placeholder,
    table_exists,
    transaction,
)
from bmlib.db.transactions import transaction_depth
from bmlib.publications.models import FetchedRecord, FetchResult, FullTextSource, Publication
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

        for table in ("publications", "fulltext_sources", "download_days"):
            assert table_exists(backend_conn, table)

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
