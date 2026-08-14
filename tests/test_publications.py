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

"""Tests for bmlib.publications — models, schema, storage, and fetchers."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from bmlib.db import connect_sqlite, execute, fetch_one, table_exists
from bmlib.publications.fetchers.biorxiv import PAGE_SIZE, _normalize, fetch_biorxiv
from bmlib.publications.models import (
    AuthorAffiliation,
    DownloadDay,
    FetchedRecord,
    FetchResult,
    FullTextSource,
    Grant,
    Publication,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import (
    add_fulltext_source,
    get_author_affiliations,
    get_grants,
    get_publication_by_doi,
    get_publication_by_pmid,
    store_publication,
)
from bmlib.publications.sync import _record_to_fulltext_sources

# ---------------------------------------------------------------------------
# Task 1: Data model tests
# ---------------------------------------------------------------------------


class TestPublication:
    def test_positional_construction_is_stable_across_versions(self):
        """Regression guard: new fields go last, so old call sites keep working.

        Downstream projects construct ``Publication`` positionally. Adding a
        field anywhere but the end silently shifts every later argument — an
        abstract passed in position six would land in the new field, with no
        error at any layer. This pins the order every released bmlib has had.
        """
        pub = Publication(
            "Title",
            ["pubmed"],
            "pubmed",
            "10.1234/test",
            "12345678",
            "An abstract.",
            ["Author A"],
            "Test Journal",
            "2024-01-15",
        )

        assert pub.title == "Title"
        assert pub.sources == ["pubmed"]
        assert pub.first_seen_source == "pubmed"
        assert pub.doi == "10.1234/test"
        assert pub.pmid == "12345678"
        assert pub.abstract == "An abstract."
        assert pub.authors == ["Author A"]
        assert pub.journal == "Test Journal"
        assert pub.publication_date == "2024-01-15"
        assert pub.pmcid is None

    def test_pmcid_is_optional_in_from_dict(self):
        """A dict serialised by an older bmlib has no ``pmcid`` key."""
        legacy = {
            "title": "Title",
            "sources": ["pubmed"],
            "first_seen_source": "pubmed",
            "doi": "10.1234/test",
        }

        assert Publication.from_dict(legacy).pmcid is None

    def test_roundtrip(self):
        pub = Publication(
            title="Test Publication",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/test",
            pmid="12345678",
            abstract="An abstract.",
            authors=["Author A", "Author B"],
            journal="Test Journal",
            publication_date="2024-01-15",
            publication_types=["journal-article"],
            keywords=["test", "example"],
            is_open_access=True,
            license="CC-BY-4.0",
        )
        d = pub.to_dict()
        pub2 = Publication.from_dict(d)

        assert pub2.title == "Test Publication"
        assert pub2.doi == "10.1234/test"
        assert pub2.pmid == "12345678"
        assert pub2.abstract == "An abstract."
        assert pub2.authors == ["Author A", "Author B"]
        assert pub2.journal == "Test Journal"
        assert pub2.publication_date == "2024-01-15"
        assert pub2.publication_types == ["journal-article"]
        assert pub2.keywords == ["test", "example"]
        assert pub2.is_open_access is True
        assert pub2.license == "CC-BY-4.0"
        assert pub2.sources == ["pubmed"]
        assert pub2.first_seen_source == "pubmed"
        assert isinstance(pub2.created_at, datetime)
        assert isinstance(pub2.updated_at, datetime)

    def test_defaults(self):
        pub = Publication(
            title="Minimal",
            sources=["biorxiv"],
            first_seen_source="biorxiv",
        )
        assert pub.doi is None
        assert pub.pmid is None
        assert pub.abstract is None
        assert pub.authors == []
        assert pub.journal is None
        assert pub.publication_date is None
        assert pub.publication_types == []
        assert pub.keywords == []
        assert pub.is_open_access is False
        assert pub.license is None
        assert pub.id is None
        assert isinstance(pub.created_at, datetime)
        assert isinstance(pub.updated_at, datetime)


class TestFullTextSource:
    def test_roundtrip(self):
        fts = FullTextSource(
            publication_id=1,
            source="pmc",
            url="https://pmc.example.com/article/123",
            format="xml",
            version="1.0",
        )
        d = fts.to_dict()
        fts2 = FullTextSource.from_dict(d)

        assert fts2.publication_id == 1
        assert fts2.source == "pmc"
        assert fts2.url == "https://pmc.example.com/article/123"
        assert fts2.format == "xml"
        assert fts2.version == "1.0"
        assert isinstance(fts2.created_at, datetime)


class TestDownloadDay:
    def test_roundtrip(self):
        dd = DownloadDay(
            source="pubmed",
            date="2024-06-15",
            status="completed",
            record_count=150,
        )
        d = dd.to_dict()
        dd2 = DownloadDay.from_dict(d)

        assert dd2.source == "pubmed"
        assert dd2.date == "2024-06-15"
        assert dd2.status == "completed"
        assert dd2.record_count == 150
        assert isinstance(dd2.downloaded_at, datetime)


class TestFetchResult:
    def test_basic(self):
        fr = FetchResult(
            source="pubmed",
            date="2024-06-15",
            record_count=100,
            status="ok",
        )
        assert fr.source == "pubmed"
        assert fr.date == "2024-06-15"
        assert fr.record_count == 100
        assert fr.status == "ok"
        assert fr.error is None

    def test_with_error(self):
        fr = FetchResult(
            source="biorxiv",
            date="2024-06-15",
            record_count=0,
            status="error",
            error="Connection timeout",
        )
        assert fr.error == "Connection timeout"


class TestSyncProgress:
    def test_basic(self):
        sp = SyncProgress(
            source="pubmed",
            date="2024-06-15",
            records_processed=50,
            records_total=200,
            status="in_progress",
        )
        assert sp.source == "pubmed"
        assert sp.records_processed == 50
        assert sp.records_total == 200
        assert sp.status == "in_progress"
        assert sp.message is None

    def test_with_message(self):
        sp = SyncProgress(
            source="pubmed",
            date="2024-06-15",
            records_processed=200,
            records_total=200,
            status="completed",
            message="All records synced successfully",
        )
        assert sp.message == "All records synced successfully"


class TestSyncReport:
    def test_basic(self):
        sr = SyncReport(
            sources_synced=["pubmed", "biorxiv"],
            days_processed=30,
            records_added=500,
            records_merged=50,
            records_failed=2,
        )
        assert sr.sources_synced == ["pubmed", "biorxiv"]
        assert sr.days_processed == 30
        assert sr.records_added == 500
        assert sr.records_merged == 50
        assert sr.records_failed == 2
        assert sr.errors == []

    def test_with_errors(self):
        sr = SyncReport(
            sources_synced=["pubmed"],
            days_processed=5,
            records_added=100,
            records_merged=10,
            records_failed=3,
            errors=["Failed to parse record X", "Duplicate DOI Y"],
        )
        assert len(sr.errors) == 2


# ---------------------------------------------------------------------------
# Task 2: Schema tests
# ---------------------------------------------------------------------------


def _schema_conn():
    """Create an in-memory SQLite connection with schema applied."""
    conn = connect_sqlite(":memory:")
    ensure_schema(conn)
    return conn


class TestSchema:
    def test_ensure_schema_creates_tables(self):
        conn = _schema_conn()
        assert table_exists(conn, "publications")
        assert table_exists(conn, "fulltext_sources")
        assert table_exists(conn, "download_days")
        assert table_exists(conn, "publication_grants")
        assert table_exists(conn, "publication_affiliations")

    def test_ensure_schema_idempotent(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        ensure_schema(conn)  # should not raise
        assert table_exists(conn, "publications")

    def test_doi_unique_index_enforced(self):
        conn = _schema_conn()
        now = datetime.now().isoformat()
        sql = (
            "INSERT INTO publications"
            " (doi, title, sources, first_seen_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
        )
        execute(conn, sql, ("10.1234/test", "Paper A", "[]", "pubmed", now, now))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            execute(conn, sql, ("10.1234/test", "Paper B", "[]", "pubmed", now, now))

    def test_null_doi_allows_multiples(self):
        conn = _schema_conn()
        now = datetime.now().isoformat()
        for title in ("No DOI A", "No DOI B", "No DOI C"):
            execute(
                conn,
                "INSERT INTO publications"
                " (doi, title, sources, first_seen_source, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (None, title, "[]", "pubmed", now, now),
            )
        conn.commit()
        # All three should be present
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM publications WHERE doi IS NULL")
        assert cur.fetchone()[0] == 3

    def test_download_days_unique_constraint(self):
        conn = _schema_conn()
        now = datetime.now().isoformat()
        execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("pubmed", "2024-06-15", "completed", 100, now),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            execute(
                conn,
                "INSERT INTO download_days (source, date, status, record_count, downloaded_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("pubmed", "2024-06-15", "completed", 200, now),
            )

    def test_fulltext_sources_unique_by_pub_url(self):
        conn = _schema_conn()
        now = datetime.now().isoformat()
        # Create a publication first
        cur = execute(
            conn,
            "INSERT INTO publications (title, sources, first_seen_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("Test Paper", "[]", "pubmed", now, now),
        )
        pub_id = cur.lastrowid
        conn.commit()

        execute(
            conn,
            "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pub_id, "pmc", "https://pmc.example.com/1", "xml", now),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            execute(
                conn,
                "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (pub_id, "pmc", "https://pmc.example.com/1", "xml", now),
            )

    def test_different_urls_same_pub_allowed(self):
        conn = _schema_conn()
        now = datetime.now().isoformat()
        cur = execute(
            conn,
            "INSERT INTO publications (title, sources, first_seen_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("Test Paper", "[]", "pubmed", now, now),
        )
        pub_id = cur.lastrowid
        conn.commit()

        execute(
            conn,
            "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pub_id, "pmc", "https://pmc.example.com/1", "xml", now),
        )
        execute(
            conn,
            "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pub_id, "publisher", "https://publisher.example.com/1.pdf", "pdf", now),
        )
        conn.commit()
        cur2 = conn.cursor()
        cur2.execute("SELECT COUNT(*) FROM fulltext_sources WHERE publication_id=?", (pub_id,))
        assert cur2.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# Task 3: Storage tests
# ---------------------------------------------------------------------------


class TestStorage:
    def test_store_new_publication(self):
        conn = _schema_conn()
        pub = Publication(
            title="New Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/new",
            pmid="11111111",
            abstract="Abstract text.",
        )
        result = store_publication(conn, pub)
        assert result == "added"

        found = get_publication_by_doi(conn, "10.1234/new")
        assert found is not None
        assert found.title == "New Paper"
        assert found.pmid == "11111111"
        assert found.abstract == "Abstract text."
        assert found.id is not None

    def test_duplicate_doi_merges(self):
        conn = _schema_conn()
        pub1 = Publication(
            title="First Version",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/dup",
            abstract="Original abstract.",
        )
        result1 = store_publication(conn, pub1)
        assert result1 == "added"

        pub2 = Publication(
            title="Second Version",
            sources=["biorxiv"],
            first_seen_source="biorxiv",
            doi="10.1234/dup",
            pmid="99999999",
            abstract="New abstract.",
        )
        result2 = store_publication(conn, pub2)
        assert result2 == "merged"

        merged = get_publication_by_doi(conn, "10.1234/dup")
        assert merged is not None
        # Title is kept from first insert (non-NULL not overwritten)
        assert merged.title == "First Version"
        # pmid was NULL, so it gets filled from incoming
        assert merged.pmid == "99999999"
        # abstract was non-NULL, so it stays
        assert merged.abstract == "Original abstract."
        # Sources should be merged
        assert "pubmed" in merged.sources
        assert "biorxiv" in merged.sources

    def test_duplicate_pmid_merges(self):
        conn = _schema_conn()
        pub1 = Publication(
            title="PMID Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            pmid="22222222",
        )
        store_publication(conn, pub1)

        pub2 = Publication(
            title="PMID Paper Updated",
            sources=["biorxiv"],
            first_seen_source="biorxiv",
            pmid="22222222",
            doi="10.1234/pmid-merge",
            abstract="Now has abstract.",
        )
        result = store_publication(conn, pub2)
        assert result == "merged"

        merged = get_publication_by_pmid(conn, "22222222")
        assert merged is not None
        assert merged.title == "PMID Paper"  # kept from first
        assert merged.doi == "10.1234/pmid-merge"  # filled NULL
        assert merged.abstract == "Now has abstract."  # filled NULL

    def test_no_identifiers_inserts(self):
        conn = _schema_conn()
        pub1 = Publication(
            title="No ID Paper 1",
            sources=["manual"],
            first_seen_source="manual",
        )
        pub2 = Publication(
            title="No ID Paper 2",
            sources=["manual"],
            first_seen_source="manual",
        )
        assert store_publication(conn, pub1) == "added"
        assert store_publication(conn, pub2) == "added"

    def test_get_not_found_returns_none(self):
        conn = _schema_conn()
        assert get_publication_by_doi(conn, "10.9999/nonexistent") is None
        assert get_publication_by_pmid(conn, "00000000") is None

    def test_store_publication_commits_once(self):
        # Bulk ingestion performance: one record (including its full-text
        # sources) must cost one commit, not one commit per statement.
        conn = _schema_conn()
        pub = Publication(
            title="Batch Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/batch",
        )
        sources = [
            FullTextSource(0, "pmc", "https://pmc.example.com/b.xml", "xml"),
            FullTextSource(0, "publisher", "https://pub.example.com/b.pdf", "pdf"),
        ]
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            assert store_publication(conn, pub, fulltext_sources=sources) == "added"
        finally:
            conn.set_trace_callback(None)

        commits = [s for s in statements if s.strip().upper().startswith("COMMIT")]
        assert len(commits) == 1
        assert not conn.in_transaction  # standalone call still persists

    def test_store_publication_joins_caller_transaction(self):
        # Inside a caller-managed transaction (the sync batcher), a store
        # must not commit — the caller owns the batch boundary.
        from bmlib.db import transaction

        conn = _schema_conn()
        pub = Publication(
            title="Batched Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/batched",
        )
        with transaction(conn):
            store_publication(conn, pub)
            assert conn.in_transaction  # still uncommitted inside the batch
        assert get_publication_by_doi(conn, "10.1234/batched") is not None

    def test_add_fulltext_source_works(self):
        conn = _schema_conn()
        pub = Publication(
            title="FTS Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/fts",
        )
        store_publication(conn, pub)
        found = get_publication_by_doi(conn, "10.1234/fts")

        inserted = add_fulltext_source(conn, found.id, "pmc", "https://pmc.example.com/fts", "xml")
        assert inserted is True

    def test_add_fulltext_source_rejects_duplicate_url(self):
        conn = _schema_conn()
        pub = Publication(
            title="FTS Dup Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/fts-dup",
        )
        store_publication(conn, pub)
        found = get_publication_by_doi(conn, "10.1234/fts-dup")
        url = "https://pmc.example.com/fts-dup"

        assert add_fulltext_source(conn, found.id, "pmc", url, "xml") is True
        assert add_fulltext_source(conn, found.id, "pmc", url, "xml") is False

    def test_merge_is_open_access_upgrades_false_to_true(self):
        """When existing is_open_access is False and incoming is True, it should upgrade."""
        conn = _schema_conn()
        pub1 = Publication(
            title="Closed Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/oa-merge",
            is_open_access=False,
        )
        store_publication(conn, pub1)

        pub2 = Publication(
            title="Closed Paper",
            sources=["openalex"],
            first_seen_source="openalex",
            doi="10.1234/oa-merge",
            is_open_access=True,
        )
        result = store_publication(conn, pub2)
        assert result == "merged"

        merged = get_publication_by_doi(conn, "10.1234/oa-merge")
        assert merged.is_open_access is True

    def test_merge_is_open_access_keeps_true(self):
        """When existing is_open_access is True and incoming is False, it stays True."""
        conn = _schema_conn()
        pub1 = Publication(
            title="Open Paper",
            sources=["openalex"],
            first_seen_source="openalex",
            doi="10.1234/oa-keep",
            is_open_access=True,
        )
        store_publication(conn, pub1)

        pub2 = Publication(
            title="Open Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/oa-keep",
            is_open_access=False,
        )
        store_publication(conn, pub2)

        merged = get_publication_by_doi(conn, "10.1234/oa-keep")
        assert merged.is_open_access is True

    def test_store_publication_with_fulltext_sources(self):
        conn = _schema_conn()
        pub = Publication(
            title="With FTS",
            sources=["pubmed"],
            first_seen_source="pubmed",
            doi="10.1234/with-fts",
        )
        fts_list = [
            FullTextSource(
                publication_id=0,  # will be set by store
                source="pmc",
                url="https://pmc.example.com/with-fts",
                format="xml",
            ),
            FullTextSource(
                publication_id=0,
                source="publisher",
                url="https://publisher.example.com/with-fts.pdf",
                format="pdf",
            ),
        ]
        result = store_publication(conn, pub, fulltext_sources=fts_list)
        assert result == "added"

        found = get_publication_by_doi(conn, "10.1234/with-fts")
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM fulltext_sources WHERE publication_id=?",
            (found.id,),
        )
        assert cur.fetchone()[0] == 2


class TestStorageDeduplication:
    """Regression tests for cross-source deduplication (DOI/PMID normalization)."""

    def _count(self, conn):
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM publications")
        return cur.fetchone()[0]

    def test_doi_case_insensitive_dedup(self):
        # PubMed keeps the registered DOI case; OpenAlex lower-cases it. Both
        # must resolve to a single row.
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(
                title="Lancet",
                doi="10.1016/S0140-6736(24)00001-1",
                sources=["pubmed"],
                first_seen_source="pubmed",
            ),
        )
        result = store_publication(
            conn,
            Publication(
                title="Lancet",
                doi="10.1016/s0140-6736(24)00001-1",
                sources=["openalex"],
                first_seen_source="openalex",
            ),
        )
        assert result == "merged"
        assert self._count(conn) == 1

    def test_doi_prefix_stripped_for_dedup(self):
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(
                title="P", doi="10.1234/abc", sources=["pubmed"], first_seen_source="pubmed"
            ),
        )
        result = store_publication(
            conn,
            Publication(
                title="P",
                doi="https://doi.org/10.1234/ABC",
                sources=["openalex"],
                first_seen_source="openalex",
            ),
        )
        assert result == "merged"
        assert self._count(conn) == 1

    def test_doi_lookup_normalizes_query(self):
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(
                title="P", doi="10.1234/XyZ", sources=["pubmed"], first_seen_source="pubmed"
            ),
        )
        assert get_publication_by_doi(conn, "10.1234/xyz") is not None
        assert get_publication_by_doi(conn, "HTTPS://doi.org/10.1234/XYZ") is not None

    def test_split_identity_consolidates_without_crash(self):
        # Row A: pmid only. Row B: doi only. A later record carrying both must
        # consolidate the two rows instead of raising a UNIQUE violation.
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(
                title="Paper", pmid="12345678", sources=["pubmed"], first_seen_source="pubmed"
            ),
        )
        store_publication(
            conn,
            Publication(
                title="Paper",
                doi="10.1234/xyz",
                abstract="Full abstract.",
                sources=["openalex"],
                first_seen_source="openalex",
            ),
        )
        assert self._count(conn) == 2

        result = store_publication(
            conn,
            Publication(
                title="Paper",
                pmid="12345678",
                doi="10.1234/xyz",
                sources=["openalex"],
                first_seen_source="openalex",
            ),
        )
        assert result == "merged"
        assert self._count(conn) == 1

        row = get_publication_by_doi(conn, "10.1234/xyz")
        assert row.pmid == "12345678"
        assert row.abstract == "Full abstract."
        assert "pubmed" in row.sources
        assert "openalex" in row.sources

    def test_split_identity_moves_fulltext_sources(self):
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(title="P", pmid="22222222", sources=["pubmed"], first_seen_source="pubmed"),
        )
        store_publication(
            conn,
            Publication(
                title="P", doi="10.1234/q", sources=["openalex"], first_seen_source="openalex"
            ),
        )
        pmid_row = get_publication_by_pmid(conn, "22222222")
        add_fulltext_source(conn, pmid_row.id, "pmc", "https://pmc.example/q", "xml")

        store_publication(
            conn,
            Publication(
                title="P",
                pmid="22222222",
                doi="10.1234/q",
                sources=["openalex"],
                first_seen_source="openalex",
            ),
        )
        keep = get_publication_by_doi(conn, "10.1234/q")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM fulltext_sources WHERE publication_id=?", (keep.id,))
        assert cur.fetchone()[0] == 1

    def test_split_identity_consolidation_failure_loses_nothing(self, monkeypatch):
        # Consolidation must not commit between deleting the drop row and
        # merging its data: if the merge step fails, a rollback must restore
        # both original rows instead of having durably lost the drop row.
        import bmlib.publications.storage as storage_mod

        conn = _schema_conn()
        store_publication(
            conn,
            Publication(
                title="Paper", pmid="12345678", sources=["pubmed"], first_seen_source="pubmed"
            ),
        )
        store_publication(
            conn,
            Publication(
                title="Paper", doi="10.1234/xyz", sources=["openalex"], first_seen_source="openalex"
            ),
        )

        def boom(*args, **kwargs):
            raise RuntimeError("merge failed")

        monkeypatch.setattr(storage_mod, "_merge_publication", boom)
        with pytest.raises(RuntimeError):
            store_publication(
                conn,
                Publication(
                    title="Paper",
                    pmid="12345678",
                    doi="10.1234/xyz",
                    sources=["openalex"],
                    first_seen_source="openalex",
                ),
            )
        conn.rollback()

        assert self._count(conn) == 2
        assert get_publication_by_pmid(conn, "12345678") is not None
        assert get_publication_by_doi(conn, "10.1234/xyz") is not None


# ---------------------------------------------------------------------------
# Task 4: bioRxiv/medRxiv fetcher tests
# ---------------------------------------------------------------------------


def _make_api_response(collection, total=None):
    """Build a mock httpx response for the bioRxiv API."""
    if total is None:
        total = len(collection)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "messages": [{"total": str(total), "count": str(len(collection))}],
        "collection": collection,
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _sample_record(doi="10.1101/2024.01.01.000001", title="Sample Preprint"):
    """Return a sample raw bioRxiv API record."""
    return {
        "doi": doi,
        "title": title,
        "authors": "Smith, J.; Doe, A.; Lee, B.",
        "date": "2024-06-15",
        "category": "neuroscience",
        "abstract": "This is a sample abstract.",
        "jatsxml": f"https://www.biorxiv.org/content/{doi}v1.source.xml",
        "published": "NA",
        "server": "biorxiv",
    }


class TestBiorxivNormalize:
    def test_normalize_splits_authors(self):
        raw = _sample_record()
        result = _normalize(raw, "biorxiv")
        assert result.authors == ["Smith, J.", "Doe, A.", "Lee, B."]

    def test_normalize_builds_fulltext_sources(self):
        raw = _sample_record(doi="10.1101/2024.01.01.000001")
        result = _normalize(raw, "biorxiv")
        sources = result.fulltext_sources
        assert len(sources) == 2
        pdf = sources[0]
        assert pdf.format == "pdf"
        assert pdf.url == "https://www.biorxiv.org/content/10.1101/2024.01.01.000001v1.full.pdf"
        assert pdf.source == "biorxiv"
        xml = sources[1]
        assert xml.format == "xml"
        assert "source.xml" in xml.url

    def test_normalize_uses_record_version_for_pdf_url(self):
        # A revised preprint (v2) must produce a v2 PDF URL, not a stale v1.
        raw = _sample_record(doi="10.1101/2024.01.01.000001")
        raw["version"] = 2
        result = _normalize(raw, "biorxiv")
        pdf = result.fulltext_sources[0]
        assert pdf.url == ("https://www.biorxiv.org/content/10.1101/2024.01.01.000001v2.full.pdf")

    def test_normalize_sets_source(self):
        raw = _sample_record()
        result = _normalize(raw, "medrxiv")
        assert result.source == "medrxiv"

    def test_normalize_open_access(self):
        raw = _sample_record()
        result = _normalize(raw, "biorxiv")
        assert result.is_open_access is True

    def test_normalize_absent_abstract_is_none_not_empty_string(self):
        # An absent/empty abstract must be None so the storage layer's
        # COALESCE merge can fill it from another source later.
        raw = _sample_record()
        raw["abstract"] = ""
        del raw["date"]
        result = _normalize(raw, "biorxiv")
        assert result.abstract is None
        assert result.publication_date is None


class TestFetchBiorxiv:
    def test_fetches_records_correctly(self):
        """Mock 2 records and verify normalised output."""
        records = [
            _sample_record(doi="10.1101/2024.01.01.000001", title="Paper A"),
            _sample_record(doi="10.1101/2024.01.01.000002", title="Paper B"),
        ]
        mock_resp = _make_api_response(records, total=2)
        client = MagicMock()
        client.get.return_value = mock_resp

        collected = []
        result = fetch_biorxiv(
            client,
            date(2024, 6, 15),
            on_record=collected.append,
        )

        assert result.status == "completed"
        assert result.record_count == 2
        assert result.source == "biorxiv"
        assert result.date == "2024-06-15"
        assert result.error is None

        assert len(collected) == 2
        assert collected[0].title == "Paper A"
        assert collected[1].title == "Paper B"

        # Verify normalisation
        rec = collected[0]
        assert isinstance(rec, FetchedRecord)
        assert isinstance(rec.authors, list)
        assert len(rec.authors) == 3
        assert len(rec.fulltext_sources) == 2
        assert rec.source == "biorxiv"
        assert rec.is_open_access is True

    def test_medrxiv_server_parameter(self):
        """URL should contain 'medrxiv' and source field should be 'medrxiv'."""
        records = [_sample_record()]
        mock_resp = _make_api_response(records)
        client = MagicMock()
        client.get.return_value = mock_resp

        collected = []
        result = fetch_biorxiv(
            client,
            date(2024, 6, 15),
            on_record=collected.append,
            server="medrxiv",
        )

        assert result.source == "medrxiv"
        assert result.status == "completed"

        # Verify the URL used contains "medrxiv"
        call_url = client.get.call_args[0][0]
        assert "medrxiv" in call_url

        # Verify source in normalised record
        assert collected[0].source == "medrxiv"

        # Verify PDF URL uses medrxiv domain
        pdf_source = collected[0].fulltext_sources[0]
        assert "medrxiv.org" in pdf_source.url

    def test_http_error_returns_failed(self):
        """HTTP error should return FetchResult with status='failed'."""
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
        client.get.return_value = mock_resp

        collected = []
        result = fetch_biorxiv(
            client,
            date(2024, 6, 15),
            on_record=collected.append,
        )

        assert result.status == "failed"
        assert result.record_count == 0
        assert result.error is not None
        assert "503" in result.error
        assert len(collected) == 0

    def test_empty_collection_returns_complete_zero(self):
        """Empty collection should return completed with 0 records."""
        mock_resp = _make_api_response([], total=0)
        client = MagicMock()
        client.get.return_value = mock_resp

        collected = []
        result = fetch_biorxiv(
            client,
            date(2024, 6, 15),
            on_record=collected.append,
        )

        assert result.status == "completed"
        assert result.record_count == 0
        assert len(collected) == 0

    def test_progress_callback_fires(self):
        """Progress callback should be called after each page."""
        records = [_sample_record(doi=f"10.1101/2024.01.01.{i:06d}") for i in range(3)]
        mock_resp = _make_api_response(records, total=3)
        client = MagicMock()
        client.get.return_value = mock_resp

        progress_reports = []
        result = fetch_biorxiv(
            client,
            date(2024, 6, 15),
            on_record=lambda r: None,
            on_progress=progress_reports.append,
        )

        assert result.status == "completed"
        assert len(progress_reports) >= 1

        progress = progress_reports[0]
        assert isinstance(progress, SyncProgress)
        assert progress.source == "biorxiv"
        assert progress.date == "2024-06-15"
        assert progress.records_processed == 3
        assert progress.status == "in_progress"

    def test_multi_page_pagination(self):
        """When a page has PAGE_SIZE records, fetch continues to the next page."""
        # Build a full page (PAGE_SIZE records) then a partial second page
        page1_records = [
            _sample_record(doi=f"10.1101/2024.01.01.{i:06d}") for i in range(PAGE_SIZE)
        ]
        page2_records = [
            _sample_record(doi=f"10.1101/2024.01.01.{PAGE_SIZE + i:06d}") for i in range(3)
        ]

        page1_resp = _make_api_response(page1_records, total=PAGE_SIZE + 3)
        page2_resp = _make_api_response(page2_records, total=PAGE_SIZE + 3)

        client = MagicMock()
        client.get.side_effect = [page1_resp, page2_resp]

        collected = []
        from unittest.mock import patch

        with patch("bmlib.publications.fetchers.biorxiv.time.sleep") as mock_sleep:
            result = fetch_biorxiv(
                client,
                date(2024, 6, 15),
                on_record=collected.append,
            )

        assert result.status == "completed"
        assert result.record_count == PAGE_SIZE + 3
        assert len(collected) == PAGE_SIZE + 3
        assert client.get.call_count == 2

        # Verify rate-limiting sleep was called between pages
        mock_sleep.assert_called_once_with(0.5)

        # Verify second page URL has offset
        second_url = client.get.call_args_list[1][0][0]
        assert f"/{PAGE_SIZE}" in second_url


class TestBiorxivWalkIsReconciledAgainstTheTotal:
    """A walk that stopped short must not report as a quiet day (issue #88).

    ``sync()`` stores a ``completed`` day and ``_days_needing_fetch()`` never
    offers it again, so a short walk that reports success loses those records
    permanently.
    """

    def test_a_walk_delivering_almost_none_of_the_total_fails(self):
        mock_resp = _make_api_response([_sample_record()], total=250)
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        assert "delivered 1 of 250" in result.error
        assert result.record_count == 1

    def test_a_small_shortfall_still_completes(self):
        """A preprint withdrawn between the count and the page is benign."""
        mock_resp = _make_api_response([_sample_record()], total=2)
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "completed"
        assert result.error is None

    def test_an_empty_page_with_records_outstanding_fails(self):
        mock_resp = _make_api_response([], total=250)
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        assert "empty page" in result.error

    def test_an_empty_page_mid_walk_fails(self):
        page1 = [_sample_record(doi=f"10.1101/2024.01.01.{i:06d}") for i in range(PAGE_SIZE)]
        client = MagicMock()
        client.get.side_effect = [
            _make_api_response(page1, total=250),
            _make_api_response([], total=250),
        ]

        from unittest.mock import patch

        with patch("bmlib.publications.fetchers.biorxiv.time.sleep"):
            result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.record_count == PAGE_SIZE

    def test_a_quiet_day_still_completes(self):
        """bioRxiv reports a day with no posts by omitting the total entirely."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "messages": [{"status": "no posts found"}],
            "collection": [],
        }
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "completed"
        assert result.record_count == 0
        assert result.error is None

    def test_a_payload_that_is_not_an_object_fails(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = ["not", "an", "object"]
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None

    def test_a_collection_that_is_not_a_list_fails(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"messages": [{"total": "5"}], "collection": None}
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None
        assert "collection" in result.error

    def test_a_non_numeric_total_fails_rather_than_raising(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"messages": [{"total": "many"}], "collection": []}
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get.return_value = mock_resp

        result = fetch_biorxiv(client, date(2024, 6, 15), on_record=MagicMock())

        assert result.status == "failed"
        assert result.error is not None


# ---------------------------------------------------------------------------
# Test _raw_to_fulltext_sources
# ---------------------------------------------------------------------------


class TestRecordToFulltextSources:
    def test_none_when_no_fulltext_sources(self):
        """Returns None when record has no fulltext_sources."""
        record = FetchedRecord(title="Test", source="test")
        assert _record_to_fulltext_sources(record) is None

    def test_none_when_empty_list(self):
        """Returns None when fulltext_sources is an empty list."""
        record = FetchedRecord(title="Test", source="test", fulltext_sources=[])
        assert _record_to_fulltext_sources(record) is None

    def test_extracts_sources_correctly(self):
        """Correctly converts fulltext source dicts to FullTextSource objects."""
        record = FetchedRecord(
            title="Test",
            source="test",
            fulltext_sources=[
                {
                    "source": "pmc",
                    "url": "https://pmc.example.com/1",
                    "format": "xml",
                    "version": "1.0",
                },
                {"source": "publisher", "url": "https://pub.example.com/1.pdf", "format": "pdf"},
            ],
        )
        result = _record_to_fulltext_sources(record)
        assert result is not None
        assert len(result) == 2
        assert result[0].source == "pmc"
        assert result[0].url == "https://pmc.example.com/1"
        assert result[0].format == "xml"
        assert result[0].version == "1.0"
        assert result[1].source == "publisher"
        assert result[1].format == "pdf"
        assert result[1].version is None

    def test_defaults_for_missing_keys(self):
        """Uses defaults when optional keys are missing from fulltext source dict."""
        record = FetchedRecord(
            title="Test",
            source="test",
            fulltext_sources=[
                {"url": "https://example.com/paper"},
            ],
        )
        result = _record_to_fulltext_sources(record)
        assert result is not None
        assert len(result) == 1
        assert result[0].source == "unknown"
        assert result[0].format == "html"
        assert result[0].version is None


# ---------------------------------------------------------------------------
# Grants and author affiliations
# ---------------------------------------------------------------------------


class TestGrantAndAffiliationModels:
    def test_grant_round_trips_through_a_dict(self):
        grant = Grant(agency="NHLBI", grant_id="R01", country="United States", publication_id=7)
        assert Grant.from_dict(grant.to_dict()) == grant

    def test_an_empty_grant_dict_loads_with_defaults(self):
        assert Grant.from_dict({}) == Grant()

    def test_affiliation_round_trips_through_a_dict(self):
        aff = AuthorAffiliation(
            author="Smith, John", affiliation="St Elsewhere", position=2, publication_id=7
        )
        assert AuthorAffiliation.from_dict(aff.to_dict()) == aff


class TestGrantAndAffiliationStorage:
    def test_grants_round_trip(self):
        conn = _schema_conn()
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(
            conn,
            pub,
            grants=[
                Grant(agency="NHLBI", grant_id="R01", country="United States", source="pubmed")
            ],
        )

        pub_id = get_publication_by_pmid(conn, "1").id
        stored = get_grants(conn, pub_id)
        assert len(stored) == 1
        assert stored[0].agency == "NHLBI"
        assert stored[0].grant_id == "R01"
        assert stored[0].country == "United States"
        assert stored[0].publication_id == pub_id
        assert stored[0].id is not None

    def test_a_grant_with_null_fields_round_trips(self):
        conn = _schema_conn()
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(conn, pub, grants=[Grant(agency="Wellcome Trust", source="pubmed")])

        stored = get_grants(conn, get_publication_by_pmid(conn, "1").id)
        assert stored[0].agency == "Wellcome Trust"
        assert stored[0].grant_id is None
        assert stored[0].country is None

    def test_affiliations_round_trip_in_position_order(self):
        conn = _schema_conn()
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(
            conn,
            pub,
            affiliations=[
                AuthorAffiliation(
                    author="Brown", affiliation="Pfizer Inc", position=2, source="pubmed"
                ),
                AuthorAffiliation(
                    author="Smith, J", affiliation="St Elsewhere", position=0, source="pubmed"
                ),
            ],
        )

        pub_id = get_publication_by_pmid(conn, "1").id
        stored = get_author_affiliations(conn, pub_id)
        assert [a.position for a in stored] == [0, 2]
        assert [a.author for a in stored] == ["Smith, J", "Brown"]
        assert stored[0].affiliation == "St Elsewhere"

    def test_a_publication_with_none_reads_back_empty(self):
        conn = _schema_conn()
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(conn, pub)

        pub_id = get_publication_by_pmid(conn, "1").id
        assert get_grants(conn, pub_id) == []
        assert get_author_affiliations(conn, pub_id) == []

    def test_re_storing_the_same_record_does_not_duplicate(self):
        """Re-syncing a day is idempotent.

        There is no UNIQUE constraint to lean on — the natural key is entirely
        nullable, and both backends treat NULL as distinct in a unique index, so
        such a constraint would silently protect nothing. Idempotency is the
        storage layer's job instead.
        """
        conn = _schema_conn()
        grants = [
            Grant(agency="NHLBI", grant_id="R01", source="pubmed"),
            Grant(agency="Wellcome Trust", source="pubmed"),
        ]
        affiliations = [
            AuthorAffiliation(author="Smith, J", affiliation="St Elsewhere", source="pubmed")
        ]

        for _ in range(3):
            pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
            store_publication(conn, pub, grants=grants, affiliations=affiliations)

        pub_id = get_publication_by_pmid(conn, "1").id
        assert len(get_grants(conn, pub_id)) == 2
        assert len(get_author_affiliations(conn, pub_id)) == 1

    def test_a_record_carrying_grants_replaces_the_stored_set(self):
        """A corrected record supersedes the stale one rather than adding to it."""
        conn = _schema_conn()
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(conn, pub, grants=[Grant(agency="Typo Foundation", source="pubmed")])

        pub2 = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(
            conn, pub2, grants=[Grant(agency="NHLBI", grant_id="R01", source="pubmed")]
        )

        stored = get_grants(conn, get_publication_by_pmid(conn, "1").id)
        assert [g.agency for g in stored] == ["NHLBI"]

    def test_a_record_carrying_none_does_not_erase_stored_grants(self):
        """A source with no funding data must not wipe a source that had it.

        bioRxiv and OpenAlex records merging into a PubMed row carry no grants;
        treating that as "this paper has no funders" would destroy the data on
        the next sync of any other source.
        """
        conn = _schema_conn()
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(
            conn,
            pub,
            grants=[Grant(agency="NHLBI", source="pubmed")],
            affiliations=[
                AuthorAffiliation(author="Smith, J", affiliation="St Elsewhere", source="pubmed")
            ],
        )

        merged = Publication(title="P", sources=["biorxiv"], first_seen_source="biorxiv", pmid="1")
        store_publication(conn, merged)

        pub_id = get_publication_by_pmid(conn, "1").id
        assert len(get_grants(conn, pub_id)) == 1
        assert len(get_author_affiliations(conn, pub_id)) == 1


class TestConsolidationRelocatesChildRows:
    """A split-identity consolidation must not orphan grant/affiliation rows.

    ``_consolidate_rows`` deletes the dropped publication row. Both backends
    enforce foreign keys, so a grant still pointing at that id makes the DELETE
    raise and aborts the entire store.
    """

    def test_a_split_identity_merge_relocates_grants(self):
        conn = _schema_conn()
        # Two rows for one work: one known by DOI, one by PMID.
        by_doi = Publication(
            title="P", sources=["openalex"], first_seen_source="openalex", doi="10.1/x"
        )
        store_publication(conn, by_doi)
        by_pmid = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(
            conn,
            by_pmid,
            grants=[Grant(agency="NHLBI", source="pubmed")],
            affiliations=[
                AuthorAffiliation(author="Smith, J", affiliation="St Elsewhere", source="pubmed")
            ],
        )

        # A record carrying both identifiers consolidates them.
        both = Publication(
            title="P", sources=["pubmed"], first_seen_source="pubmed", doi="10.1/x", pmid="1"
        )
        store_publication(conn, both)

        assert fetch_one(conn, "SELECT COUNT(*) AS n FROM publications")["n"] == 1
        kept = get_publication_by_doi(conn, "10.1/x")
        assert [g.agency for g in get_grants(conn, kept.id)] == ["NHLBI"]
        assert [a.author for a in get_author_affiliations(conn, kept.id)] == ["Smith, J"]

    def test_the_kept_rows_own_children_survive_a_merge(self):
        """The keep row's data wins; the drop row's is not layered on top.

        Both grants name the *same* source, which is the case this pins: two
        accounts of what PubMed said, of which only one can be right. Merging
        them would yield a funder set PubMed never asserted, so the keep row's
        wins outright. The cross-source case is the opposite and is covered by
        ``test_consolidation_keeps_each_source_at_most_once``.
        """
        conn = _schema_conn()
        by_doi = Publication(
            title="P", sources=["openalex"], first_seen_source="openalex", doi="10.1/x"
        )
        store_publication(conn, by_doi, grants=[Grant(agency="Keep Foundation", source="pubmed")])
        by_pmid = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")
        store_publication(conn, by_pmid, grants=[Grant(agency="Drop Foundation", source="pubmed")])

        both = Publication(
            title="P", sources=["pubmed"], first_seen_source="pubmed", doi="10.1/x", pmid="1"
        )
        store_publication(conn, both)

        kept = get_publication_by_doi(conn, "10.1/x")
        assert [g.agency for g in get_grants(conn, kept.id)] == ["Keep Foundation"]
        # And nothing is stranded pointing at the deleted row.
        assert fetch_one(conn, "SELECT COUNT(*) AS n FROM publication_grants")["n"] == 1

    def test_the_caller_s_objects_are_not_mutated(self):
        """`publication_id` is ignored on the way in and not written back.

        ``store_publication`` *does* mutate its ``pub`` argument in place, and
        says so prominently, which gives a reader every reason to expect the
        same of these. It does not — and the failure would be silent, since
        ``publication_id`` reads back as ``0``, a plausible-looking id rather
        than an obvious sentinel. Read the stored form back instead.
        """
        conn = _schema_conn()
        grant = Grant(agency="NHLBI", source="pubmed")
        affiliation = AuthorAffiliation(
            author="Smith, J", affiliation="St Elsewhere", source="pubmed"
        )
        pub = Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1")

        store_publication(conn, pub, grants=[grant], affiliations=[affiliation])

        assert grant.publication_id == 0
        assert grant.id is None
        assert affiliation.publication_id == 0

        # The stored rows carry the real ids.
        pub_id = get_publication_by_pmid(conn, "1").id
        assert get_grants(conn, pub_id)[0].publication_id == pub_id
        assert get_grants(conn, pub_id)[0].id is not None


class TestChildRowsAreScopedBySource:
    """Two sources' grants must coexist, not alternate.

    Without a ``source`` column, replace-on-store made the stored set depend
    entirely on which source synced last — PubMed's grants, then OpenAlex's,
    then PubMed's again, flip-flopping forever with no error and no warning.
    The sibling table ``fulltext_sources`` has always carried ``source``;
    these two were the exception.
    """

    def test_a_second_source_does_not_displace_the_first(self):
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1"),
            grants=[Grant(agency="NHLBI", source="pubmed")],
        )
        store_publication(
            conn,
            Publication(title="P", sources=["openalex"], first_seen_source="openalex", pmid="1"),
            grants=[Grant(agency="Wellcome Trust", source="openalex")],
        )

        pub_id = get_publication_by_pmid(conn, "1").id
        assert sorted(g.agency for g in get_grants(conn, pub_id)) == [
            "NHLBI",
            "Wellcome Trust",
        ]

    def test_re_syncing_one_source_replaces_only_its_own_rows(self):
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(title="P", sources=["openalex"], first_seen_source="openalex", pmid="1"),
            grants=[Grant(agency="Wellcome Trust", source="openalex")],
        )
        for agency in ("Typo Foundation", "NHLBI"):
            store_publication(
                conn,
                Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1"),
                grants=[Grant(agency=agency, source="pubmed")],
            )

        pub_id = get_publication_by_pmid(conn, "1").id
        stored = get_grants(conn, pub_id)
        # PubMed's correction superseded its own stale row; OpenAlex untouched.
        assert sorted(g.agency for g in stored) == ["NHLBI", "Wellcome Trust"]
        assert {g.source for g in stored} == {"pubmed", "openalex"}

    def test_the_stored_row_reports_which_source_asserted_it(self):
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1"),
            grants=[Grant(agency="NHLBI", source="pubmed")],
            affiliations=[
                AuthorAffiliation(author="Smith, J", affiliation="St Elsewhere", source="pubmed")
            ],
        )

        pub_id = get_publication_by_pmid(conn, "1").id
        assert get_grants(conn, pub_id)[0].source == "pubmed"
        assert get_author_affiliations(conn, pub_id)[0].source == "pubmed"

    def test_consolidation_keeps_each_source_at_most_once(self):
        """A split-identity merge must not layer one source's rows onto itself.

        The keep row already has PubMed grants; the drop row's PubMed grants
        are dropped rather than added, while a source the keep row lacks moves
        across.
        """
        conn = _schema_conn()
        store_publication(
            conn,
            Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", doi="10.1/x"),
            grants=[Grant(agency="Keep NHLBI", source="pubmed")],
        )
        store_publication(
            conn,
            Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1"),
            grants=[
                Grant(agency="Drop NHLBI", source="pubmed"),
                Grant(agency="Wellcome Trust", source="openalex"),
            ],
        )

        store_publication(
            conn,
            Publication(
                title="P", sources=["pubmed"], first_seen_source="pubmed", doi="10.1/x", pmid="1"
            ),
        )

        kept = get_publication_by_doi(conn, "10.1/x")
        stored = get_grants(conn, kept.id)
        assert sorted(g.agency for g in stored) == ["Keep NHLBI", "Wellcome Trust"]
        assert fetch_one(conn, "SELECT COUNT(*) AS n FROM publication_grants")["n"] == 2

    def test_a_row_naming_no_source_is_rejected(self):
        """A source-less row must fail loudly, whichever way it is missing.

        Scoping is the whole mechanism, so an unnamed row is not merely
        unlabelled — it is unreachable. No later sync can name it, so it can
        never be replaced, and every subsequent sync stacks a correctly
        labelled duplicate beside it.

        Both spellings of "missing" are checked because they used to fail
        differently. ``None`` hit the NOT NULL column; ``""`` — which is what
        the dataclass *defaults to*, so it is the one a caller actually
        reaches — sailed through and was stored. That is why the check is in
        the storage layer rather than left to the column.
        """
        conn = _schema_conn()
        for missing in (None, ""):
            with pytest.raises(ValueError, match="must name the source"):
                store_publication(
                    conn,
                    Publication(
                        title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1"
                    ),
                    grants=[Grant(agency="NHLBI", source=missing)],
                )

    def test_a_source_less_affiliation_is_rejected_too(self):
        """The guard covers both child tables, not just grants."""
        conn = _schema_conn()
        with pytest.raises(ValueError, match="publication_affiliations"):
            store_publication(
                conn,
                Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="2"),
                affiliations=[AuthorAffiliation(author="Smith, J", affiliation="St E")],
            )

    def test_the_rejection_leaves_nothing_behind(self):
        """The raise happens inside the store's transaction, so it rolls back.

        Otherwise a rejected batch could still leave the publication row — and
        any earlier grant group — committed, which is a worse state than either
        storing or refusing cleanly.
        """
        conn = _schema_conn()
        with pytest.raises(ValueError):
            store_publication(
                conn,
                Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="3"),
                grants=[Grant(agency="NHLBI", source="pubmed"), Grant(agency="Wellcome")],
            )

        assert get_publication_by_pmid(conn, "3") is None
        assert fetch_one(conn, "SELECT COUNT(*) AS n FROM publication_grants")["n"] == 0

    def test_relocating_a_row_onto_itself_is_a_no_op(self):
        """Guards the one assumption the relocation rests on.

        `_relocate_child_rows` deletes the drop row's rows for sources the keep
        row has, then moves the rest. Those two sets are disjoint only because
        the ids differ. With equal ids the subquery would match every row the
        DELETE is about to remove, wiping the publication's whole set and
        leaving the UPDATE nothing to move — total loss, silently. The caller
        cannot currently pass equal ids; this pins that it would be harmless if
        it ever could.
        """
        from bmlib.publications.storage import _relocate_child_rows

        conn = _schema_conn()
        store_publication(
            conn,
            Publication(title="P", sources=["pubmed"], first_seen_source="pubmed", pmid="1"),
            grants=[Grant(agency="NHLBI", source="pubmed")],
        )
        pub_id = get_publication_by_pmid(conn, "1").id

        _relocate_child_rows(conn, "publication_grants", pub_id, pub_id)

        assert [g.agency for g in get_grants(conn, pub_id)] == ["NHLBI"]
