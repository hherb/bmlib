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

"""Tests for bmlib.publications — models, schema, and storage."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from bmlib.db import connect_sqlite, execute, table_exists
from bmlib.publications.models import (
    DownloadDay,
    FetchResult,
    FullTextSource,
    Publication,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import (
    add_fulltext_source,
    get_publication_by_doi,
    get_publication_by_pmid,
    store_publication,
)

# ---------------------------------------------------------------------------
# Task 1: Data model tests
# ---------------------------------------------------------------------------


class TestPublication:
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
