# Publications Module Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `bmlib/publications/` module that downloads, stores, deduplicates, and sync-tracks biomedical publications from PubMed, bioRxiv, medRxiv, and OpenAlex.

**Architecture:** New package under `bmlib/publications/` following existing patterns — dataclass models with `to_dict`/`from_dict`, pure-function database operations using `bmlib.db`, optional `httpx` dependency for HTTP fetching. Source fetchers stream records via callbacks; a sync orchestrator tracks which (source, day) pairs have been completed.

**Tech Stack:** Python 3.11+, `bmlib.db` (SQLite/PostgreSQL), `httpx` (HTTP), `xml.etree.ElementTree` (PubMed XML parsing), `json` (list field serialization), `dataclasses`, `pytest`.

---

### Task 1: Data Models (`models.py`)

**Files:**
- Create: `bmlib/publications/__init__.py` (empty for now)
- Create: `bmlib/publications/models.py`
- Create: `tests/test_publications.py`

**Step 1: Write the failing tests**

Create `tests/test_publications.py`:

```python
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

"""Tests for bmlib.publications — models, schema, storage, and sync."""

from __future__ import annotations

from datetime import UTC, date, datetime

from bmlib.publications.models import (
    DownloadDay,
    FetchResult,
    FullTextSource,
    Publication,
    SyncProgress,
    SyncReport,
)


class TestPublication:
    def test_roundtrip(self):
        pub = Publication(
            doi="10.1234/test",
            pmid="12345678",
            title="Test Paper",
            abstract="An abstract.",
            authors=["Smith, John", "Doe, Jane"],
            journal="Test Journal",
            publication_date=date(2024, 1, 15),
            publication_types=["Journal Article"],
            keywords=["test"],
            is_open_access=True,
            license="CC-BY-4.0",
            sources=["pubmed"],
            first_seen_source="pubmed",
            created_at=datetime(2024, 1, 20, tzinfo=UTC),
            updated_at=datetime(2024, 1, 20, tzinfo=UTC),
        )
        d = pub.to_dict()
        assert d["doi"] == "10.1234/test"
        assert d["authors"] == ["Smith, John", "Doe, Jane"]
        assert d["publication_date"] == "2024-01-15"

        pub2 = Publication.from_dict(d)
        assert pub2.doi == pub.doi
        assert pub2.authors == pub.authors
        assert pub2.publication_date == pub.publication_date
        assert pub2.is_open_access is True

    def test_defaults(self):
        pub = Publication(
            title="Minimal",
            sources=["openalex"],
            first_seen_source="openalex",
        )
        assert pub.doi is None
        assert pub.pmid is None
        assert pub.authors == []
        assert pub.keywords == []
        assert pub.is_open_access is False


class TestFullTextSource:
    def test_roundtrip(self):
        fts = FullTextSource(
            publication_id=1,
            source="biorxiv",
            url="https://biorxiv.org/content/10.1101/2024.01.01.pdf",
            format="pdf",
            version="preprint",
        )
        d = fts.to_dict()
        assert d["source"] == "biorxiv"
        assert d["format"] == "pdf"

        fts2 = FullTextSource.from_dict(d)
        assert fts2.url == fts.url
        assert fts2.version == "preprint"
        assert fts2.retrieved_at is None


class TestDownloadDay:
    def test_roundtrip(self):
        dd = DownloadDay(
            source="pubmed",
            date=date(2024, 6, 15),
            status="complete",
            record_count=42,
            downloaded_at=datetime(2024, 6, 15, 12, 0, tzinfo=UTC),
        )
        d = dd.to_dict()
        assert d["source"] == "pubmed"
        assert d["record_count"] == 42
        assert d["last_verified_at"] is None

        dd2 = DownloadDay.from_dict(d)
        assert dd2.date == date(2024, 6, 15)
        assert dd2.status == "complete"


class TestFetchResult:
    def test_basic(self):
        fr = FetchResult(
            source="biorxiv", date=date(2024, 1, 1),
            record_count=100, status="complete",
        )
        assert fr.error is None


class TestSyncProgress:
    def test_basic(self):
        sp = SyncProgress(
            source="pubmed", date=date(2024, 1, 1),
            records_processed=50, records_total=200,
            status="fetching", message="Page 2/8",
        )
        assert sp.records_total == 200


class TestSyncReport:
    def test_basic(self):
        sr = SyncReport(
            sources_synced=["pubmed", "biorxiv"],
            days_processed=5,
            records_added=100,
            records_merged=20,
            records_failed=2,
            errors=["timeout on page 3"],
        )
        assert sr.days_processed == 5
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bmlib.publications'`

**Step 3: Create empty package and implement models**

Create `bmlib/publications/__init__.py` (minimal, just to make it a package):

```python
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

"""Publication ingestion, storage, deduplication, and sync tracking."""
```

Create `bmlib/publications/models.py`:

```python
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

"""Data models for the publications module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any


@dataclass
class Publication:
    """Canonical record for a single publication."""

    title: str
    sources: list[str]
    first_seen_source: str

    doi: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_date: date | None = None
    publication_types: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    is_open_access: bool = False
    license: str | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    # Database row id (None until stored)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "doi": self.doi,
            "pmid": self.pmid,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "journal": self.journal,
            "publication_date": (
                self.publication_date.isoformat() if self.publication_date else None
            ),
            "publication_types": self.publication_types,
            "keywords": self.keywords,
            "is_open_access": self.is_open_access,
            "license": self.license,
            "sources": self.sources,
            "first_seen_source": self.first_seen_source,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Publication:
        pub_date_raw = data.get("publication_date")
        if pub_date_raw and isinstance(pub_date_raw, str):
            pub_date = date.fromisoformat(pub_date_raw)
        elif isinstance(pub_date_raw, date):
            pub_date = pub_date_raw
        else:
            pub_date = None

        created_raw = data.get("created_at")
        created = (
            datetime.fromisoformat(created_raw)
            if created_raw
            else datetime.now(tz=UTC)
        )
        updated_raw = data.get("updated_at")
        updated = (
            datetime.fromisoformat(updated_raw)
            if updated_raw
            else datetime.now(tz=UTC)
        )

        return cls(
            id=data.get("id"),
            doi=data.get("doi"),
            pmid=data.get("pmid"),
            title=data["title"],
            abstract=data.get("abstract"),
            authors=data.get("authors", []),
            journal=data.get("journal"),
            publication_date=pub_date,
            publication_types=data.get("publication_types", []),
            keywords=data.get("keywords", []),
            is_open_access=data.get("is_open_access", False),
            license=data.get("license"),
            sources=data.get("sources", []),
            first_seen_source=data.get("first_seen_source", "unknown"),
            created_at=created,
            updated_at=updated,
        )


@dataclass
class FullTextSource:
    """A single full-text access point for a publication."""

    publication_id: int
    source: str
    url: str
    format: str

    version: str | None = None
    retrieved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "url": self.url,
            "format": self.format,
            "version": self.version,
            "retrieved_at": (
                self.retrieved_at.isoformat() if self.retrieved_at else None
            ),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullTextSource:
        retrieved_raw = data.get("retrieved_at")
        retrieved = (
            datetime.fromisoformat(retrieved_raw) if retrieved_raw else None
        )
        created_raw = data.get("created_at")
        created = (
            datetime.fromisoformat(created_raw)
            if created_raw
            else datetime.now(tz=UTC)
        )
        return cls(
            id=data.get("id"),
            publication_id=data["publication_id"],
            source=data["source"],
            url=data["url"],
            format=data["format"],
            version=data.get("version"),
            retrieved_at=retrieved,
            created_at=created,
        )


@dataclass
class DownloadDay:
    """Tracks sync completion for a single source and calendar day."""

    source: str
    date: date
    status: str
    record_count: int

    downloaded_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    last_verified_at: datetime | None = None

    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "date": self.date.isoformat(),
            "status": self.status,
            "record_count": self.record_count,
            "downloaded_at": self.downloaded_at.isoformat(),
            "last_verified_at": (
                self.last_verified_at.isoformat()
                if self.last_verified_at
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DownloadDay:
        date_raw = data["date"]
        d = date.fromisoformat(date_raw) if isinstance(date_raw, str) else date_raw

        dl_raw = data.get("downloaded_at")
        downloaded = (
            datetime.fromisoformat(dl_raw) if dl_raw else datetime.now(tz=UTC)
        )
        lv_raw = data.get("last_verified_at")
        last_verified = datetime.fromisoformat(lv_raw) if lv_raw else None

        return cls(
            id=data.get("id"),
            source=data["source"],
            date=d,
            status=data["status"],
            record_count=data.get("record_count", 0),
            downloaded_at=downloaded,
            last_verified_at=last_verified,
        )


@dataclass
class FetchResult:
    """Result returned by a source fetcher for a single day."""

    source: str
    date: date
    record_count: int
    status: str
    error: str | None = None


@dataclass
class SyncProgress:
    """Progress callback payload for UI reporting."""

    source: str
    date: date
    records_processed: int
    records_total: int | None
    status: str
    message: str | None = None


@dataclass
class SyncReport:
    """Summary of a sync() run."""

    sources_synced: list[str]
    days_processed: int
    records_added: int
    records_merged: int
    records_failed: int
    errors: list[str] = field(default_factory=list)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/__init__.py bmlib/publications/models.py tests/test_publications.py
git commit -m "feat(publications): add data models for publications module"
```

---

### Task 2: Database Schema (`schema.py`)

**Files:**
- Create: `bmlib/publications/schema.py`
- Modify: `tests/test_publications.py` (add schema tests)

**Step 1: Write the failing tests**

Append to `tests/test_publications.py`:

```python
from bmlib.db import connect_sqlite, fetch_all, fetch_one, table_exists
from bmlib.publications.schema import SCHEMA_SQL, ensure_schema


class TestSchema:
    def _conn(self):
        return connect_sqlite(":memory:")

    def test_ensure_schema_creates_tables(self):
        conn = self._conn()
        ensure_schema(conn)
        assert table_exists(conn, "publications")
        assert table_exists(conn, "fulltext_sources")
        assert table_exists(conn, "download_days")

    def test_ensure_schema_idempotent(self):
        conn = self._conn()
        ensure_schema(conn)
        ensure_schema(conn)  # second call should not raise
        assert table_exists(conn, "publications")

    def test_doi_unique_index(self):
        """Two publications with the same DOI should violate the unique index."""
        import sqlite3
        import pytest
        conn = self._conn()
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO publications (doi, title, sources, first_seen_source, created_at, updated_at) "
            "VALUES ('10.1234/a', 'Paper A', '[]', 'pubmed', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO publications (doi, title, sources, first_seen_source, created_at, updated_at) "
                "VALUES ('10.1234/a', 'Paper B', '[]', 'biorxiv', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
            )

    def test_null_doi_allowed_multiple(self):
        """Multiple publications with NULL doi should be fine."""
        conn = self._conn()
        ensure_schema(conn)
        for i in range(3):
            conn.execute(
                "INSERT INTO publications (title, sources, first_seen_source, created_at, updated_at) "
                f"VALUES ('Paper {i}', '[]', 'pubmed', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
            )
        conn.commit()
        rows = fetch_all(conn, "SELECT * FROM publications")
        assert len(rows) == 3

    def test_download_days_unique_constraint(self):
        """Same (source, date) should violate unique constraint."""
        import sqlite3
        import pytest
        conn = self._conn()
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at) "
            "VALUES ('pubmed', '2024-01-01', 'complete', 10, '2024-01-01T12:00:00')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO download_days (source, date, status, record_count, downloaded_at) "
                "VALUES ('pubmed', '2024-01-01', 'complete', 20, '2024-01-01T13:00:00')"
            )

    def test_fulltext_sources_unique_constraint(self):
        """Same (publication_id, url) should violate unique constraint."""
        import sqlite3
        import pytest
        conn = self._conn()
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO publications (title, sources, first_seen_source, created_at, updated_at) "
            "VALUES ('Paper', '[]', 'pubmed', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at) "
            "VALUES (1, 'biorxiv', 'https://example.com/paper.pdf', 'pdf', '2024-01-01T00:00:00')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at) "
                "VALUES (1, 'publisher', 'https://example.com/paper.pdf', 'pdf', '2024-01-01T00:00:00')"
            )

    def test_fulltext_different_urls_same_pub(self):
        """Same publication, different URLs should be fine."""
        conn = self._conn()
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO publications (title, sources, first_seen_source, created_at, updated_at) "
            "VALUES ('Paper', '[]', 'pubmed', '2024-01-01T00:00:00', '2024-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at) "
            "VALUES (1, 'biorxiv', 'https://biorxiv.org/paper.pdf', 'pdf', '2024-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO fulltext_sources (publication_id, source, url, format, created_at) "
            "VALUES (1, 'publisher', 'https://publisher.com/paper.pdf', 'pdf', '2024-01-01T00:00:00')"
        )
        conn.commit()
        rows = fetch_all(
            conn, "SELECT * FROM fulltext_sources WHERE publication_id=1"
        )
        assert len(rows) == 2
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py::TestSchema -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bmlib.publications.schema'`

**Step 3: Implement schema.py**

Create `bmlib/publications/schema.py`:

```python
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

"""Database schema for the publications module."""

from __future__ import annotations

from typing import Any

from bmlib.db import create_tables

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    pmid TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT DEFAULT '[]',
    journal TEXT,
    publication_date TEXT,
    publication_types TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    is_open_access INTEGER DEFAULT 0,
    license TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    first_seen_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_publications_doi
    ON publications(doi) WHERE doi IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publications_pmid
    ON publications(pmid) WHERE pmid IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_publications_date
    ON publications(publication_date);

CREATE TABLE IF NOT EXISTS fulltext_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id INTEGER NOT NULL REFERENCES publications(id),
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    format TEXT NOT NULL,
    version TEXT,
    retrieved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(publication_id, url)
);

CREATE TABLE IF NOT EXISTS download_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL,
    record_count INTEGER DEFAULT 0,
    downloaded_at TEXT NOT NULL,
    last_verified_at TEXT,
    UNIQUE(source, date)
);
"""


def ensure_schema(conn: Any) -> None:
    """Create publications tables if they don't exist."""
    create_tables(conn, SCHEMA_SQL)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/schema.py tests/test_publications.py
git commit -m "feat(publications): add database schema with dedup indexes"
```

---

### Task 3: Storage Layer (`storage.py`)

**Files:**
- Create: `bmlib/publications/storage.py`
- Modify: `tests/test_publications.py` (add storage tests)

**Step 1: Write the failing tests**

Append to `tests/test_publications.py`:

```python
from bmlib.db import transaction
from bmlib.publications.models import FullTextSource, Publication
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import (
    add_fulltext_source,
    get_publication_by_doi,
    get_publication_by_pmid,
    store_publication,
)


class TestStorage:
    def _conn(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        return conn

    def test_store_new_publication(self):
        conn = self._conn()
        pub = Publication(
            doi="10.1234/test",
            pmid="12345678",
            title="Test Paper",
            authors=["Smith, John"],
            sources=["pubmed"],
            first_seen_source="pubmed",
        )
        result = store_publication(conn, pub)
        assert result == "added"
        stored = get_publication_by_doi(conn, "10.1234/test")
        assert stored is not None
        assert stored.title == "Test Paper"
        assert stored.id is not None

    def test_store_duplicate_doi_merges(self):
        conn = self._conn()
        pub1 = Publication(
            doi="10.1234/test",
            title="Paper from PubMed",
            authors=["Smith, John"],
            sources=["pubmed"],
            first_seen_source="pubmed",
        )
        store_publication(conn, pub1)

        pub2 = Publication(
            doi="10.1234/test",
            pmid="99999999",
            title="Paper from OpenAlex",
            abstract="An abstract from OpenAlex",
            authors=["Smith, John", "Doe, Jane"],
            sources=["openalex"],
            first_seen_source="openalex",
        )
        result = store_publication(conn, pub2)
        assert result == "merged"

        stored = get_publication_by_doi(conn, "10.1234/test")
        assert stored is not None
        # Title kept from first source (never overwrite non-NULL)
        assert stored.title == "Paper from PubMed"
        # PMID filled in from second source (was NULL)
        assert stored.pmid == "99999999"
        # Abstract filled in from second source
        assert stored.abstract == "An abstract from OpenAlex"
        # Sources merged
        assert "pubmed" in stored.sources
        assert "openalex" in stored.sources

    def test_store_duplicate_pmid_merges(self):
        conn = self._conn()
        pub1 = Publication(
            pmid="12345678",
            title="PubMed Paper",
            sources=["pubmed"],
            first_seen_source="pubmed",
        )
        store_publication(conn, pub1)

        pub2 = Publication(
            pmid="12345678",
            doi="10.1234/found-later",
            title="Same paper from bioRxiv",
            sources=["biorxiv"],
            first_seen_source="biorxiv",
        )
        result = store_publication(conn, pub2)
        assert result == "merged"

        stored = get_publication_by_pmid(conn, "12345678")
        assert stored is not None
        assert stored.doi == "10.1234/found-later"
        assert "biorxiv" in stored.sources

    def test_store_no_identifiers_inserts(self):
        conn = self._conn()
        pub = Publication(
            title="No DOI or PMID",
            sources=["openalex"],
            first_seen_source="openalex",
        )
        result = store_publication(conn, pub)
        assert result == "added"

    def test_get_publication_by_doi_not_found(self):
        conn = self._conn()
        assert get_publication_by_doi(conn, "10.9999/nonexistent") is None

    def test_get_publication_by_pmid_not_found(self):
        conn = self._conn()
        assert get_publication_by_pmid(conn, "00000000") is None

    def test_add_fulltext_source(self):
        conn = self._conn()
        pub = Publication(
            doi="10.1234/test",
            title="Test",
            sources=["pubmed"],
            first_seen_source="pubmed",
        )
        store_publication(conn, pub)
        stored = get_publication_by_doi(conn, "10.1234/test")

        added = add_fulltext_source(
            conn,
            publication_id=stored.id,
            source="biorxiv",
            url="https://biorxiv.org/paper.pdf",
            format="pdf",
            version="preprint",
        )
        assert added is True

        # Same URL should not be added again
        added2 = add_fulltext_source(
            conn,
            publication_id=stored.id,
            source="publisher",
            url="https://biorxiv.org/paper.pdf",
            format="pdf",
        )
        assert added2 is False

    def test_store_with_fulltext_sources(self):
        conn = self._conn()
        pub = Publication(
            doi="10.1234/test",
            title="Test",
            sources=["biorxiv"],
            first_seen_source="biorxiv",
        )
        fulltext = [
            {"source": "biorxiv", "url": "https://biorxiv.org/paper.pdf",
             "format": "pdf", "version": "preprint"},
        ]
        store_publication(conn, pub, fulltext_sources=fulltext)

        rows = fetch_all(conn, "SELECT * FROM fulltext_sources")
        assert len(rows) == 1
        assert rows[0]["source"] == "biorxiv"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py::TestStorage -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bmlib.publications.storage'`

**Step 3: Implement storage.py**

Create `bmlib/publications/storage.py`:

```python
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

"""Publication storage with deduplication.

All functions take a DB-API connection as first argument, following the
bmlib.db convention.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from bmlib.db import execute, fetch_one

from bmlib.publications.models import Publication

logger = logging.getLogger(__name__)


def store_publication(
    conn: Any,
    pub: Publication,
    fulltext_sources: list[dict[str, Any]] | None = None,
) -> str:
    """Store a publication, deduplicating by DOI then PMID.

    Returns:
        "added" if a new row was inserted, "merged" if an existing row
        was updated with data from *pub*.
    """
    existing = None

    # Dedup: check DOI first
    if pub.doi:
        existing = get_publication_by_doi(conn, pub.doi)

    # Dedup: check PMID if no DOI match
    if existing is None and pub.pmid:
        existing = get_publication_by_pmid(conn, pub.pmid)

    now = datetime.now(tz=UTC).isoformat()

    if existing is not None:
        _merge_publication(conn, existing, pub, now)
        pub_id = existing.id
        result = "merged"
    else:
        pub_id = _insert_publication(conn, pub, now)
        result = "added"

    # Add any fulltext sources
    if fulltext_sources:
        for fts in fulltext_sources:
            add_fulltext_source(
                conn,
                publication_id=pub_id,
                source=fts["source"],
                url=fts["url"],
                format=fts["format"],
                version=fts.get("version"),
            )

    conn.commit()
    return result


def get_publication_by_doi(conn: Any, doi: str) -> Publication | None:
    """Look up a publication by DOI."""
    row = fetch_one(conn, "SELECT * FROM publications WHERE doi = ?", (doi,))
    return _row_to_publication(row) if row else None


def get_publication_by_pmid(conn: Any, pmid: str) -> Publication | None:
    """Look up a publication by PubMed ID."""
    row = fetch_one(conn, "SELECT * FROM publications WHERE pmid = ?", (pmid,))
    return _row_to_publication(row) if row else None


def add_fulltext_source(
    conn: Any,
    publication_id: int,
    source: str,
    url: str,
    format: str,
    version: str | None = None,
) -> bool:
    """Add a full-text source for a publication.

    Returns True if inserted, False if the (publication_id, url) already exists.
    """
    existing = fetch_one(
        conn,
        "SELECT 1 FROM fulltext_sources WHERE publication_id = ? AND url = ?",
        (publication_id, url),
    )
    if existing:
        return False

    now = datetime.now(tz=UTC).isoformat()
    execute(
        conn,
        "INSERT INTO fulltext_sources "
        "(publication_id, source, url, format, version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (publication_id, source, url, format, version, now),
    )
    conn.commit()
    return True


# --- Internal helpers ---


def _insert_publication(conn: Any, pub: Publication, now: str) -> int:
    """Insert a new publication row and return its id."""
    cur = execute(
        conn,
        "INSERT INTO publications "
        "(doi, pmid, title, abstract, authors, journal, publication_date, "
        "publication_types, keywords, is_open_access, license, "
        "sources, first_seen_source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pub.doi,
            pub.pmid,
            pub.title,
            pub.abstract,
            json.dumps(pub.authors),
            pub.journal,
            pub.publication_date.isoformat() if pub.publication_date else None,
            json.dumps(pub.publication_types),
            json.dumps(pub.keywords),
            int(pub.is_open_access),
            pub.license,
            json.dumps(pub.sources),
            pub.first_seen_source,
            now,
            now,
        ),
    )
    return cur.lastrowid


def _merge_publication(
    conn: Any, existing: Publication, incoming: Publication, now: str,
) -> None:
    """Merge incoming data into an existing publication row.

    Rules:
    - Fill NULL fields from incoming (never overwrite non-NULL)
    - Merge sources lists
    - Update updated_at
    """
    # Merge sources
    merged_sources = list(existing.sources)
    for src in incoming.sources:
        if src not in merged_sources:
            merged_sources.append(src)

    execute(
        conn,
        "UPDATE publications SET "
        "doi = COALESCE(doi, ?), "
        "pmid = COALESCE(pmid, ?), "
        "abstract = COALESCE(abstract, ?), "
        "authors = CASE WHEN authors = '[]' THEN ? ELSE authors END, "
        "journal = COALESCE(journal, ?), "
        "publication_date = COALESCE(publication_date, ?), "
        "publication_types = CASE WHEN publication_types = '[]' THEN ? ELSE publication_types END, "
        "keywords = CASE WHEN keywords = '[]' THEN ? ELSE keywords END, "
        "is_open_access = CASE WHEN is_open_access = 0 THEN ? ELSE is_open_access END, "
        "license = COALESCE(license, ?), "
        "sources = ?, "
        "updated_at = ? "
        "WHERE id = ?",
        (
            incoming.doi,
            incoming.pmid,
            incoming.abstract,
            json.dumps(incoming.authors),
            incoming.journal,
            incoming.publication_date.isoformat() if incoming.publication_date else None,
            json.dumps(incoming.publication_types),
            json.dumps(incoming.keywords),
            int(incoming.is_open_access),
            incoming.license,
            json.dumps(merged_sources),
            now,
            existing.id,
        ),
    )


def _row_to_publication(row: Any) -> Publication:
    """Convert a database row to a Publication dataclass."""
    return Publication(
        id=row["id"],
        doi=row["doi"],
        pmid=row["pmid"],
        title=row["title"],
        abstract=row["abstract"],
        authors=json.loads(row["authors"]) if row["authors"] else [],
        journal=row["journal"],
        publication_date=(
            __import__("datetime").date.fromisoformat(row["publication_date"])
            if row["publication_date"]
            else None
        ),
        publication_types=(
            json.loads(row["publication_types"]) if row["publication_types"] else []
        ),
        keywords=json.loads(row["keywords"]) if row["keywords"] else [],
        is_open_access=bool(row["is_open_access"]),
        license=row["license"],
        sources=json.loads(row["sources"]) if row["sources"] else [],
        first_seen_source=row["first_seen_source"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
```

Note: the `_row_to_publication` helper uses an inline `__import__("datetime").date.fromisoformat` — replace that during implementation with a proper import from `datetime` at the top of the file (`from datetime import date as date_type` or similar; avoid shadowing the `date` function). The plan code is illustrative.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/storage.py tests/test_publications.py
git commit -m "feat(publications): add storage layer with dedup and merge logic"
```

---

### Task 4: Fetcher Base + bioRxiv Fetcher

**Files:**
- Create: `bmlib/publications/fetchers/__init__.py`
- Create: `bmlib/publications/fetchers/biorxiv.py`
- Modify: `tests/test_publications.py` (add fetcher tests with mocked HTTP)

**Step 1: Write the failing tests**

The bioRxiv API returns JSON like:
```json
{
  "messages": [{"status": "ok", "count": 2, "total": 2}],
  "collection": [
    {
      "doi": "10.1101/2024.01.15.575533",
      "title": "A Paper Title",
      "authors": "Smith, J.; Doe, J.",
      "author_corresponding": "Smith, J.",
      "date": "2024-01-15",
      "category": "neuroscience",
      "abstract": "Abstract text here.",
      "jatsxml": "https://biorxiv.org/content/...",
      "published": "NA",
      "server": "biorxiv"
    }
  ]
}
```

Append to `tests/test_publications.py`:

```python
from unittest.mock import MagicMock, patch
from bmlib.publications.fetchers.biorxiv import fetch_biorxiv


BIORXIV_RESPONSE = {
    "messages": [{"status": "ok", "count": 2, "total": 2}],
    "collection": [
        {
            "doi": "10.1101/2024.01.15.575533",
            "title": "Paper One",
            "authors": "Smith, J.; Doe, J.",
            "author_corresponding": "Smith, J.",
            "date": "2024-01-15",
            "category": "neuroscience",
            "abstract": "Abstract one.",
            "jatsxml": "https://www.biorxiv.org/content/early/2024/01/15/2024.01.15.575533.source.xml",
            "published": "NA",
            "server": "biorxiv",
        },
        {
            "doi": "10.1101/2024.01.15.575534",
            "title": "Paper Two",
            "authors": "Jones, A.",
            "author_corresponding": "Jones, A.",
            "date": "2024-01-15",
            "category": "genetics",
            "abstract": "Abstract two.",
            "jatsxml": "https://www.biorxiv.org/content/early/2024/01/15/2024.01.15.575534.source.xml",
            "published": "NA",
            "server": "biorxiv",
        },
    ],
}

BIORXIV_EMPTY = {
    "messages": [{"status": "ok", "count": 0, "total": 0}],
    "collection": [],
}


class TestBiorxivFetcher:
    def test_fetches_records(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = BIORXIV_RESPONSE

        mock_resp_empty = MagicMock()
        mock_resp_empty.status_code = 200
        mock_resp_empty.json.return_value = BIORXIV_EMPTY

        mock_client = MagicMock()
        mock_client.get.side_effect = [mock_resp, mock_resp_empty]

        records = []
        progress_events = []

        result = fetch_biorxiv(
            mock_client,
            date(2024, 1, 15),
            on_record=records.append,
            on_progress=progress_events.append,
            server="biorxiv",
        )

        assert result.status == "complete"
        assert result.record_count == 2
        assert len(records) == 2
        assert records[0]["doi"] == "10.1101/2024.01.15.575533"
        assert records[0]["title"] == "Paper One"
        assert records[0]["source"] == "biorxiv"
        assert records[0]["authors"] == ["Smith, J.", "Doe, J."]
        assert len(records[0]["fulltext_sources"]) >= 1
        assert len(progress_events) > 0

    def test_medrxiv_server(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = BIORXIV_EMPTY

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        records = []
        result = fetch_biorxiv(
            mock_client,
            date(2024, 1, 15),
            on_record=records.append,
            server="medrxiv",
        )

        assert result.source == "medrxiv"
        # Verify the URL used contains "medrxiv"
        call_url = mock_client.get.call_args[0][0]
        assert "medrxiv" in call_url

    def test_http_error_returns_failed(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        records = []
        result = fetch_biorxiv(
            mock_client,
            date(2024, 1, 15),
            on_record=records.append,
        )

        assert result.status == "failed"
        assert result.error is not None
        assert len(records) == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py::TestBiorxivFetcher -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement fetchers**

Create `bmlib/publications/fetchers/__init__.py`:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
# ... (AGPL-3 header) ...

"""Source fetchers for publication data."""

ALL_SOURCES = ["pubmed", "biorxiv", "medrxiv", "openalex"]
```

Create `bmlib/publications/fetchers/biorxiv.py`:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
# ... (AGPL-3 header) ...

"""bioRxiv and medRxiv fetcher.

Uses the bioRxiv API: https://api.biorxiv.org
Both servers share the same API, selected via the `server` parameter.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.publications.models import FetchResult, SyncProgress

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.biorxiv.org/details"
_PAGE_SIZE = 100
_MIN_REQUEST_INTERVAL = 0.5


def fetch_biorxiv(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    server: str = "biorxiv",
    api_key: str | None = None,
) -> FetchResult:
    """Fetch all preprints posted on *target_date* from bioRxiv or medRxiv."""
    date_str = target_date.isoformat()
    cursor = 0
    total_fetched = 0
    last_request = 0.0

    while True:
        # Rate limiting
        elapsed = time.time() - last_request
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        url = f"{_BASE_URL}/{server}/{date_str}/{date_str}/{cursor}"
        last_request = time.time()

        try:
            resp = client.get(url)
        except Exception as e:
            logger.error("bioRxiv request failed: %s", e)
            return FetchResult(
                source=server, date=target_date, record_count=total_fetched,
                status="failed", error=str(e),
            )

        if resp.status_code != 200:
            return FetchResult(
                source=server, date=target_date, record_count=total_fetched,
                status="failed",
                error=f"HTTP {resp.status_code}: {getattr(resp, 'text', '')}",
            )

        data = resp.json()
        messages = data.get("messages", [{}])
        total = messages[0].get("total", 0) if messages else 0
        collection = data.get("collection", [])

        if not collection:
            break

        for raw in collection:
            record = _normalize(raw, server)
            on_record(record)
            total_fetched += 1

        if on_progress:
            on_progress(SyncProgress(
                source=server, date=target_date,
                records_processed=total_fetched,
                records_total=total if total else None,
                status="fetching",
                message=f"Page {cursor // _PAGE_SIZE + 1}",
            ))

        if len(collection) < _PAGE_SIZE:
            break

        cursor += _PAGE_SIZE

    if on_progress:
        on_progress(SyncProgress(
            source=server, date=target_date,
            records_processed=total_fetched, records_total=total_fetched,
            status="complete", message=None,
        ))

    return FetchResult(
        source=server, date=target_date,
        record_count=total_fetched, status="complete",
    )


def _normalize(raw: dict, server: str) -> dict:
    """Convert a bioRxiv API record to the common intermediate dict format."""
    authors_str = raw.get("authors", "")
    authors = [a.strip() for a in authors_str.split(";") if a.strip()]

    doi = raw.get("doi", "")

    fulltext_sources = []
    if doi:
        pdf_url = f"https://www.biorxiv.org/content/{doi}v1.full.pdf"
        fulltext_sources.append({
            "source": server,
            "url": pdf_url,
            "format": "pdf",
            "version": "preprint",
        })
    jatsxml = raw.get("jatsxml")
    if jatsxml:
        fulltext_sources.append({
            "source": server,
            "url": jatsxml,
            "format": "xml",
            "version": "preprint",
        })

    return {
        "doi": doi or None,
        "pmid": None,
        "title": raw.get("title", ""),
        "abstract": raw.get("abstract"),
        "authors": authors,
        "journal": None,
        "publication_date": raw.get("date"),
        "publication_types": [raw.get("category", "preprint")],
        "keywords": [],
        "is_open_access": True,
        "license": None,
        "source": server,
        "fulltext_sources": fulltext_sources,
    }
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/fetchers/ tests/test_publications.py
git commit -m "feat(publications): add bioRxiv/medRxiv fetcher"
```

---

### Task 5: PubMed Fetcher

**Files:**
- Create: `bmlib/publications/fetchers/pubmed.py`
- Modify: `tests/test_publications.py` (add PubMed fetcher tests)

**Step 1: Write the failing tests**

PubMed E-utilities: `esearch` returns XML with IDs, `efetch` returns XML with full records. Key XML structure:

```xml
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article>
        <ArticleTitle>Title</ArticleTitle>
        <Abstract><AbstractText>Abstract</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>John</ForeName></Author>
        </AuthorList>
        <Journal><Title>Journal Name</Title></Journal>
        <ArticleIdList>
          <ArticleId IdType="doi">10.1234/test</ArticleId>
        </ArticleIdList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Keyword</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1234/test</ArticleId>
        <ArticleId IdType="pmc">PMC1234567</ArticleId>
      </ArticleIdList>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
```

Append to `tests/test_publications.py`:

```python
from bmlib.publications.fetchers.pubmed import fetch_pubmed, _parse_article_xml

PUBMED_ESEARCH_RESPONSE = """<?xml version="1.0"?>
<eSearchResult>
  <Count>2</Count>
  <RetMax>2</RetMax>
  <RetStart>0</RetStart>
  <IdList>
    <Id>11111111</Id>
    <Id>22222222</Id>
  </IdList>
</eSearchResult>"""

PUBMED_EFETCH_RESPONSE = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">11111111</PMID>
      <Article PubModel="Print">
        <Journal>
          <Title>Test Journal</Title>
          <JournalIssue>
            <PubDate><Year>2024</Year><Month>01</Month><Day>15</Day></PubDate>
          </JournalIssue>
        </Journal>
        <ArticleTitle>PubMed Paper One</ArticleTitle>
        <Abstract><AbstractText>Abstract one.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>John</ForeName></Author>
          <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
        </AuthorList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Neuroscience</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1234/pm-one</ArticleId>
        <ArticleId IdType="pmc">PMC1111111</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">22222222</PMID>
      <Article PubModel="Print">
        <Journal><Title>Another Journal</Title></Journal>
        <ArticleTitle>PubMed Paper Two</ArticleTitle>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList/>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""

PUBMED_ESEARCH_EMPTY = """<?xml version="1.0"?>
<eSearchResult>
  <Count>0</Count><RetMax>0</RetMax><RetStart>0</RetStart><IdList/>
</eSearchResult>"""


class TestPubmedFetcher:
    def test_parse_article_xml(self):
        """Test XML parsing of a single PubMed article."""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(PUBMED_EFETCH_RESPONSE)
        articles = root.findall(".//PubmedArticle")
        record = _parse_article_xml(articles[0])
        assert record["pmid"] == "11111111"
        assert record["doi"] == "10.1234/pm-one"
        assert record["title"] == "PubMed Paper One"
        assert record["abstract"] == "Abstract one."
        assert "Smith, John" in record["authors"]
        assert record["journal"] == "Test Journal"
        assert record["source"] == "pubmed"
        assert len(record["keywords"]) > 0
        # Should have PMC fulltext source
        assert any(
            "pmc" in fts["source"] for fts in record["fulltext_sources"]
        )

    def test_parse_minimal_article(self):
        """Article with no DOI, no abstract, no authors."""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(PUBMED_EFETCH_RESPONSE)
        articles = root.findall(".//PubmedArticle")
        record = _parse_article_xml(articles[1])
        assert record["pmid"] == "22222222"
        assert record["doi"] is None
        assert record["abstract"] is None

    def test_fetch_pubmed_mocked(self):
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.text = PUBMED_ESEARCH_RESPONSE

        fetch_resp = MagicMock()
        fetch_resp.status_code = 200
        fetch_resp.text = PUBMED_EFETCH_RESPONSE

        mock_client = MagicMock()
        mock_client.get.side_effect = [search_resp, fetch_resp]

        records = []
        result = fetch_pubmed(
            mock_client, date(2024, 1, 15),
            on_record=records.append,
        )

        assert result.status == "complete"
        assert result.record_count == 2
        assert len(records) == 2

    def test_fetch_pubmed_empty_day(self):
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.text = PUBMED_ESEARCH_EMPTY

        mock_client = MagicMock()
        mock_client.get.return_value = search_resp

        records = []
        result = fetch_pubmed(
            mock_client, date(2024, 1, 15),
            on_record=records.append,
        )

        assert result.status == "complete"
        assert result.record_count == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py::TestPubmedFetcher -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement PubMed fetcher**

Create `bmlib/publications/fetchers/pubmed.py`:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
# ... (AGPL-3 header) ...

"""PubMed fetcher via NCBI E-utilities.

Uses esearch to find IDs, then efetch to retrieve full records as XML.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.publications.models import FetchResult, SyncProgress

logger = logging.getLogger(__name__)

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_PAGE_SIZE = 500
_MIN_REQUEST_INTERVAL_NO_KEY = 0.34   # ~3 req/sec
_MIN_REQUEST_INTERVAL_WITH_KEY = 0.1  # ~10 req/sec


def fetch_pubmed(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    api_key: str | None = None,
) -> FetchResult:
    """Fetch all PubMed articles published on *target_date*."""
    min_interval = (
        _MIN_REQUEST_INTERVAL_WITH_KEY if api_key
        else _MIN_REQUEST_INTERVAL_NO_KEY
    )
    last_request = 0.0
    date_str = target_date.strftime("%Y/%m/%d")
    total_fetched = 0

    # Step 1: esearch to get total count and IDs
    def _rate_limit():
        nonlocal last_request
        elapsed = time.time() - last_request
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        last_request = time.time()

    _rate_limit()
    search_params = {
        "db": "pubmed",
        "term": f'("{date_str}"[Date - Publication])',
        "retmax": "0",
        "usehistory": "y",
    }
    if api_key:
        search_params["api_key"] = api_key

    try:
        search_resp = client.get(_ESEARCH_URL, params=search_params)
    except Exception as e:
        return FetchResult(
            source="pubmed", date=target_date, record_count=0,
            status="failed", error=str(e),
        )

    if search_resp.status_code != 200:
        return FetchResult(
            source="pubmed", date=target_date, record_count=0,
            status="failed", error=f"esearch HTTP {search_resp.status_code}",
        )

    search_root = ET.fromstring(search_resp.text)
    total_count = int(search_root.findtext("Count", "0"))

    if total_count == 0:
        return FetchResult(
            source="pubmed", date=target_date, record_count=0, status="complete",
        )

    webenv = search_root.findtext("WebEnv", "")
    query_key = search_root.findtext("QueryKey", "1")

    # Step 2: efetch in pages using history server
    retstart = 0
    while retstart < total_count:
        _rate_limit()
        fetch_params = {
            "db": "pubmed",
            "query_key": query_key,
            "WebEnv": webenv,
            "retstart": str(retstart),
            "retmax": str(_PAGE_SIZE),
            "retmode": "xml",
        }
        if api_key:
            fetch_params["api_key"] = api_key

        try:
            fetch_resp = client.get(_EFETCH_URL, params=fetch_params)
        except Exception as e:
            logger.error("efetch failed at offset %d: %s", retstart, e)
            return FetchResult(
                source="pubmed", date=target_date, record_count=total_fetched,
                status="partial", error=str(e),
            )

        if fetch_resp.status_code != 200:
            return FetchResult(
                source="pubmed", date=target_date, record_count=total_fetched,
                status="partial",
                error=f"efetch HTTP {fetch_resp.status_code} at offset {retstart}",
            )

        root = ET.fromstring(fetch_resp.text)
        articles = root.findall(".//PubmedArticle")

        for article_el in articles:
            record = _parse_article_xml(article_el)
            on_record(record)
            total_fetched += 1

        if on_progress:
            on_progress(SyncProgress(
                source="pubmed", date=target_date,
                records_processed=total_fetched,
                records_total=total_count,
                status="fetching",
                message=f"Page {retstart // _PAGE_SIZE + 1}/{(total_count + _PAGE_SIZE - 1) // _PAGE_SIZE}",
            ))

        retstart += _PAGE_SIZE

    if on_progress:
        on_progress(SyncProgress(
            source="pubmed", date=target_date,
            records_processed=total_fetched, records_total=total_fetched,
            status="complete", message=None,
        ))

    return FetchResult(
        source="pubmed", date=target_date,
        record_count=total_fetched, status="complete",
    )


def _parse_article_xml(article_el: ET.Element) -> dict:
    """Parse a <PubmedArticle> element into the common intermediate dict."""
    citation = article_el.find("MedlineCitation")
    pmid = citation.findtext("PMID", "")

    article = citation.find("Article")
    title = article.findtext("ArticleTitle", "") if article is not None else ""

    # Abstract
    abstract_el = article.find("Abstract") if article is not None else None
    abstract = None
    if abstract_el is not None:
        parts = [at.text or "" for at in abstract_el.findall("AbstractText")]
        abstract = " ".join(parts).strip() or None

    # Authors
    authors = []
    author_list = article.find("AuthorList") if article is not None else None
    if author_list is not None:
        for author_el in author_list.findall("Author"):
            last = author_el.findtext("LastName", "")
            first = author_el.findtext("ForeName", "")
            if last:
                authors.append(f"{last}, {first}" if first else last)

    # Journal
    journal_el = article.find("Journal") if article is not None else None
    journal = journal_el.findtext("Title", "") if journal_el is not None else None

    # Publication date
    pub_date_str = None
    if journal_el is not None:
        ji = journal_el.find("JournalIssue")
        if ji is not None:
            pd = ji.find("PubDate")
            if pd is not None:
                year = pd.findtext("Year", "")
                month = pd.findtext("Month", "01")
                day = pd.findtext("Day", "01")
                if year:
                    # Month may be text like "Jan" — handle both
                    month_map = {
                        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
                        "may": "05", "jun": "06", "jul": "07", "aug": "08",
                        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
                    }
                    m = month_map.get(month.lower()[:3], month.zfill(2))
                    pub_date_str = f"{year}-{m}-{day.zfill(2)}"

    # DOI from PubmedData/ArticleIdList
    doi = None
    pmc_id = None
    pubmed_data = article_el.find("PubmedData")
    if pubmed_data is not None:
        for aid in pubmed_data.findall(".//ArticleId"):
            if aid.get("IdType") == "doi":
                doi = (aid.text or "").strip()
            elif aid.get("IdType") == "pmc":
                pmc_id = (aid.text or "").strip()

    # Keywords from MeSH
    keywords = []
    mesh_list = citation.find("MeshHeadingList")
    if mesh_list is not None:
        for mh in mesh_list.findall("MeshHeading"):
            desc = mh.findtext("DescriptorName", "")
            if desc:
                keywords.append(desc)

    # Full text sources
    fulltext_sources = []
    if pmc_id:
        fulltext_sources.append({
            "source": "pmc",
            "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/",
            "format": "html",
            "version": "published",
        })
    if doi:
        fulltext_sources.append({
            "source": "publisher",
            "url": f"https://doi.org/{doi}",
            "format": "html",
            "version": "published",
        })

    return {
        "doi": doi or None,
        "pmid": pmid or None,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "journal": journal,
        "publication_date": pub_date_str,
        "publication_types": [],
        "keywords": keywords,
        "is_open_access": False,
        "license": None,
        "source": "pubmed",
        "fulltext_sources": fulltext_sources,
    }
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/fetchers/pubmed.py tests/test_publications.py
git commit -m "feat(publications): add PubMed fetcher with XML parsing"
```

---

### Task 6: OpenAlex Fetcher

**Files:**
- Create: `bmlib/publications/fetchers/openalex.py`
- Modify: `tests/test_publications.py` (add OpenAlex fetcher tests)

**Step 1: Write the failing tests**

OpenAlex returns JSON. Key fields: `id`, `doi`, `title`, `authorships`, `primary_location`, `locations`, `open_access`, `abstract_inverted_index`, `ids` (contains `pmid`).

Append to `tests/test_publications.py`:

```python
from bmlib.publications.fetchers.openalex import fetch_openalex, _reconstruct_abstract


OPENALEX_RESPONSE = {
    "meta": {"count": 1, "per_page": 25},
    "results": [
        {
            "id": "https://openalex.org/W1234567",
            "doi": "https://doi.org/10.1234/oa-one",
            "title": "OpenAlex Paper",
            "ids": {
                "openalex": "https://openalex.org/W1234567",
                "doi": "https://doi.org/10.1234/oa-one",
                "pmid": "https://pubmed.ncbi.nlm.nih.gov/33333333",
            },
            "publication_date": "2024-01-15",
            "primary_location": {
                "source": {"display_name": "Nature"},
            },
            "authorships": [
                {"author": {"display_name": "Alice Jones"}},
                {"author": {"display_name": "Bob Smith"}},
            ],
            "abstract_inverted_index": {
                "This": [0],
                "is": [1],
                "an": [2],
                "abstract.": [3],
            },
            "open_access": {"is_oa": True, "oa_url": "https://example.com/oa.pdf"},
            "primary_topic": {"display_name": "Neuroscience"},
            "locations": [
                {
                    "source": {"display_name": "Nature"},
                    "landing_page_url": "https://nature.com/article",
                    "pdf_url": "https://nature.com/article.pdf",
                    "version": "publishedVersion",
                },
                {
                    "source": {"display_name": "PubMed Central"},
                    "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/article",
                    "pdf_url": None,
                    "version": "publishedVersion",
                },
            ],
            "license": "cc-by",
            "type": "journal-article",
        },
    ],
    "next_cursor": None,
}

OPENALEX_EMPTY = {
    "meta": {"count": 0, "per_page": 25},
    "results": [],
    "next_cursor": None,
}


class TestOpenalexFetcher:
    def test_reconstruct_abstract(self):
        inverted = {"Hello": [0], "world": [1], "foo": [2]}
        assert _reconstruct_abstract(inverted) == "Hello world foo"

    def test_reconstruct_abstract_none(self):
        assert _reconstruct_abstract(None) is None

    def test_fetch_records(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = OPENALEX_RESPONSE

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        records = []
        result = fetch_openalex(
            mock_client, date(2024, 1, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert result.status == "complete"
        assert result.record_count == 1
        assert len(records) == 1

        rec = records[0]
        assert rec["doi"] == "10.1234/oa-one"
        assert rec["pmid"] == "33333333"
        assert rec["title"] == "OpenAlex Paper"
        assert rec["abstract"] == "This is an abstract."
        assert rec["is_open_access"] is True
        assert rec["source"] == "openalex"
        # Should have multiple fulltext sources from locations
        assert len(rec["fulltext_sources"]) >= 2

    def test_fetch_empty(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = OPENALEX_EMPTY

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        records = []
        result = fetch_openalex(
            mock_client, date(2024, 1, 15),
            on_record=records.append,
            email="test@example.com",
        )

        assert result.status == "complete"
        assert result.record_count == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py::TestOpenalexFetcher -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement OpenAlex fetcher**

Create `bmlib/publications/fetchers/openalex.py`:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
# ... (AGPL-3 header) ...

"""OpenAlex fetcher.

Uses the OpenAlex API: https://api.openalex.org
Cursor-based pagination, polite pool via email header.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from bmlib.publications.models import FetchResult, SyncProgress

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.openalex.org/works"
_PAGE_SIZE = 200
_MIN_REQUEST_INTERVAL = 0.1


def fetch_openalex(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    email: str = "user@example.com",
    api_key: str | None = None,
) -> FetchResult:
    """Fetch all works published on *target_date* from OpenAlex."""
    date_str = target_date.isoformat()
    cursor = "*"
    total_fetched = 0
    total_count = None
    last_request = 0.0

    while cursor:
        elapsed = time.time() - last_request
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        params = {
            "filter": f"from_publication_date:{date_str},to_publication_date:{date_str}",
            "per_page": str(_PAGE_SIZE),
            "cursor": cursor,
            "mailto": email,
        }
        if api_key:
            params["api_key"] = api_key

        last_request = time.time()

        try:
            resp = client.get(_BASE_URL, params=params)
        except Exception as e:
            return FetchResult(
                source="openalex", date=target_date, record_count=total_fetched,
                status="failed", error=str(e),
            )

        if resp.status_code != 200:
            return FetchResult(
                source="openalex", date=target_date, record_count=total_fetched,
                status="failed",
                error=f"HTTP {resp.status_code}",
            )

        data = resp.json()
        if total_count is None:
            total_count = data.get("meta", {}).get("count", 0)

        results = data.get("results", [])
        if not results:
            break

        for raw in results:
            record = _normalize(raw)
            on_record(record)
            total_fetched += 1

        if on_progress:
            on_progress(SyncProgress(
                source="openalex", date=target_date,
                records_processed=total_fetched,
                records_total=total_count,
                status="fetching",
                message=None,
            ))

        cursor = data.get("next_cursor")

    if on_progress:
        on_progress(SyncProgress(
            source="openalex", date=target_date,
            records_processed=total_fetched, records_total=total_fetched,
            status="complete", message=None,
        ))

    return FetchResult(
        source="openalex", date=target_date,
        record_count=total_fetched, status="complete",
    )


def _reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """Reconstruct abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return None
    words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort()
    return " ".join(w for _, w in words) if words else None


def _normalize(raw: dict) -> dict:
    """Convert an OpenAlex work record to the common intermediate dict."""
    # DOI: strip "https://doi.org/" prefix
    raw_doi = raw.get("doi") or ""
    doi = raw_doi.replace("https://doi.org/", "").strip() or None

    # PMID: extract from ids
    pmid = None
    ids = raw.get("ids", {})
    raw_pmid = ids.get("pmid", "")
    if raw_pmid:
        pmid = raw_pmid.replace("https://pubmed.ncbi.nlm.nih.gov/", "").strip() or None

    # Authors
    authors = []
    for authorship in raw.get("authorships", []):
        name = authorship.get("author", {}).get("display_name", "")
        if name:
            authors.append(name)

    # Journal
    primary_loc = raw.get("primary_location") or {}
    source_info = primary_loc.get("source") or {}
    journal = source_info.get("display_name")

    # Abstract
    abstract = _reconstruct_abstract(raw.get("abstract_inverted_index"))

    # Keywords
    keywords = []
    topic = raw.get("primary_topic")
    if topic and topic.get("display_name"):
        keywords.append(topic["display_name"])

    # Full text sources from locations
    fulltext_sources = []
    for loc in raw.get("locations", []):
        loc_source = (loc.get("source") or {}).get("display_name", "openalex")
        version = _map_version(loc.get("version"))

        landing = loc.get("landing_page_url")
        if landing:
            fulltext_sources.append({
                "source": loc_source,
                "url": landing,
                "format": "html",
                "version": version,
            })
        pdf = loc.get("pdf_url")
        if pdf:
            fulltext_sources.append({
                "source": loc_source,
                "url": pdf,
                "format": "pdf",
                "version": version,
            })

    return {
        "doi": doi,
        "pmid": pmid,
        "title": raw.get("title", ""),
        "abstract": abstract,
        "authors": authors,
        "journal": journal,
        "publication_date": raw.get("publication_date"),
        "publication_types": [raw.get("type", "")] if raw.get("type") else [],
        "keywords": keywords,
        "is_open_access": raw.get("open_access", {}).get("is_oa", False),
        "license": raw.get("license"),
        "source": "openalex",
        "fulltext_sources": fulltext_sources,
    }


def _map_version(oa_version: str | None) -> str | None:
    """Map OpenAlex version strings to our version vocabulary."""
    if not oa_version:
        return None
    mapping = {
        "publishedVersion": "published",
        "acceptedVersion": "accepted",
        "submittedVersion": "preprint",
    }
    return mapping.get(oa_version, oa_version)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/fetchers/openalex.py tests/test_publications.py
git commit -m "feat(publications): add OpenAlex fetcher with abstract reconstruction"
```

---

### Task 7: Sync Orchestrator (`sync.py`)

**Files:**
- Create: `bmlib/publications/sync.py`
- Modify: `tests/test_publications.py` (add sync tests)

**Step 1: Write the failing tests**

Append to `tests/test_publications.py`:

```python
from bmlib.publications.sync import sync, _days_needing_fetch
from bmlib.db import execute as db_execute


class TestDaysNeeding:
    def _conn(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        return conn

    def test_all_days_needed_when_empty(self):
        conn = self._conn()
        days = _days_needing_fetch(
            conn, "pubmed",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 3),
        )
        assert days == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

    def test_skips_completed_days(self):
        conn = self._conn()
        db_execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pubmed", "2024-01-02", "complete", 10, "2024-01-02T12:00:00"),
        )
        conn.commit()
        days = _days_needing_fetch(
            conn, "pubmed",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 3),
        )
        assert date(2024, 1, 2) not in days
        assert date(2024, 1, 1) in days
        assert date(2024, 1, 3) in days

    def test_retries_failed_days(self):
        conn = self._conn()
        db_execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pubmed", "2024-01-02", "failed", 0, "2024-01-02T12:00:00"),
        )
        conn.commit()
        days = _days_needing_fetch(
            conn, "pubmed",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 3),
        )
        assert date(2024, 1, 2) in days

    def test_today_always_included(self):
        from datetime import date as d
        today = d.today()
        conn = self._conn()
        db_execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pubmed", today.isoformat(), "complete", 50, "2024-01-02T12:00:00"),
        )
        conn.commit()
        days = _days_needing_fetch(
            conn, "pubmed",
            date_from=today,
            date_to=today,
        )
        assert today in days


class TestSync:
    def _conn(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        return conn

    def test_sync_with_mocked_fetcher(self):
        """Test sync orchestrator with a fake fetcher."""
        conn = self._conn()

        # We'll test sync by mocking the fetcher registry
        fake_records = [
            {
                "doi": "10.1234/sync-test",
                "pmid": "44444444",
                "title": "Sync Test Paper",
                "abstract": "An abstract.",
                "authors": ["Test, Author"],
                "journal": "Test Journal",
                "publication_date": "2024-01-15",
                "publication_types": ["Journal Article"],
                "keywords": [],
                "is_open_access": False,
                "license": None,
                "source": "pubmed",
                "fulltext_sources": [],
            },
        ]

        report = sync(
            conn,
            sources=["pubmed"],
            date_from=date(2024, 1, 15),
            date_to=date(2024, 1, 15),
            email="test@example.com",
            _fetcher_override={
                "pubmed": lambda client, d, **kw: _fake_fetch(
                    "pubmed", d, fake_records, kw.get("on_record"), kw.get("on_progress"),
                ),
            },
        )

        assert report.records_added == 1
        assert report.days_processed == 1
        assert "pubmed" in report.sources_synced

        # Verify stored in DB
        stored = get_publication_by_doi(conn, "10.1234/sync-test")
        assert stored is not None
        assert stored.title == "Sync Test Paper"

        # Verify download_days tracker
        row = fetch_one(
            conn,
            "SELECT * FROM download_days WHERE source='pubmed' AND date='2024-01-15'",
        )
        assert row is not None
        assert row["status"] == "complete"

    def test_sync_skips_completed_days(self):
        conn = self._conn()

        # Mark day as complete
        db_execute(
            conn,
            "INSERT INTO download_days (source, date, status, record_count, downloaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pubmed", "2024-01-15", "complete", 5, "2024-01-15T12:00:00"),
        )
        conn.commit()

        call_count = [0]

        def counting_fetcher(client, d, **kw):
            call_count[0] += 1
            return FetchResult(source="pubmed", date=d, record_count=0, status="complete")

        report = sync(
            conn,
            sources=["pubmed"],
            date_from=date(2024, 1, 15),
            date_to=date(2024, 1, 15),
            email="test@example.com",
            _fetcher_override={"pubmed": counting_fetcher},
        )

        # Should not have called the fetcher (day already complete, and not today)
        # Unless today IS 2024-01-15, in which case it would re-fetch
        from datetime import date as d
        if d.today() != date(2024, 1, 15):
            assert call_count[0] == 0
            assert report.days_processed == 0


def _fake_fetch(source, target_date, records, on_record, on_progress):
    """Helper for testing sync with fake data."""
    from bmlib.publications.models import FetchResult
    for rec in records:
        if on_record:
            on_record(rec)
    return FetchResult(
        source=source, date=target_date,
        record_count=len(records), status="complete",
    )
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_publications.py::TestSync -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement sync.py**

Create `bmlib/publications/sync.py`:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
# ... (AGPL-3 header) ...

"""Sync orchestrator — coordinates fetchers, dedup, and day tracking."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, UTC
from typing import Any

from bmlib.db import execute, fetch_all, fetch_one, transaction
from bmlib.publications.fetchers import ALL_SOURCES
from bmlib.publications.models import (
    FetchResult,
    Publication,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import add_fulltext_source, store_publication

logger = logging.getLogger(__name__)


def sync(
    conn: Any,
    *,
    sources: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email: str = "user@example.com",
    api_keys: dict[str, str] | None = None,
    on_record: Callable[[dict], None] | None = None,
    on_progress: Callable[[SyncProgress], None] | None = None,
    recheck_days: int = 0,
    _fetcher_override: dict[str, Callable] | None = None,
) -> SyncReport:
    """Synchronise publications from remote sources into the database.

    Args:
        conn: DB-API connection.
        sources: Which sources to sync (default: all).
        date_from: Earliest date to sync (default: yesterday).
        date_to: Latest date to sync (default: today).
        email: Contact email for API politeness headers.
        api_keys: Per-source API keys, e.g. ``{"pubmed": "..."}``
        on_record: Called after each record is stored/merged.
        on_progress: Called with progress updates for UI.
        recheck_days: Re-verify completed days older than this (0=disabled).
        _fetcher_override: For testing — override fetcher functions by source.
    """
    ensure_schema(conn)

    if sources is None:
        sources = list(ALL_SOURCES)
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=1)

    api_keys = api_keys or {}
    fetchers = _fetcher_override or _get_fetchers()

    total_added = 0
    total_merged = 0
    total_failed = 0
    total_days = 0
    errors: list[str] = []
    synced_sources: list[str] = []

    for source in sources:
        fetcher = fetchers.get(source)
        if fetcher is None:
            errors.append(f"No fetcher for source: {source}")
            continue

        days = _days_needing_fetch(
            conn, source,
            date_from=date_from, date_to=date_to,
            recheck_days=recheck_days,
        )

        if not days:
            continue

        synced_sources.append(source)

        try:
            import httpx
            client = httpx.Client(
                timeout=30.0,
                headers={"User-Agent": f"bmlib/0.1 (mailto:{email})"},
            )
        except ImportError:
            raise ImportError(
                "httpx is required for publication sync. "
                "Install with: pip install bmlib[publications]"
            )

        try:
            for day in days:
                added, merged, failed, day_errors = _sync_one_day(
                    conn, client, fetcher, source, day,
                    api_key=api_keys.get(source),
                    email=email,
                    on_record=on_record,
                    on_progress=on_progress,
                )
                total_added += added
                total_merged += merged
                total_failed += failed
                total_days += 1
                errors.extend(day_errors)
        finally:
            if hasattr(client, "close"):
                client.close()

    return SyncReport(
        sources_synced=synced_sources,
        days_processed=total_days,
        records_added=total_added,
        records_merged=total_merged,
        records_failed=total_failed,
        errors=errors,
    )


def _days_needing_fetch(
    conn: Any,
    source: str,
    *,
    date_from: date,
    date_to: date,
    recheck_days: int = 0,
) -> list[date]:
    """Determine which calendar days need fetching for a given source."""
    today = date.today()

    # Get all completed days in range
    rows = fetch_all(
        conn,
        "SELECT date, status, last_verified_at FROM download_days "
        "WHERE source = ? AND date >= ? AND date <= ?",
        (source, date_from.isoformat(), date_to.isoformat()),
    )

    completed: dict[str, Any] = {}
    for row in rows:
        if row["status"] == "complete":
            completed[row["date"]] = row

    days = []
    current = date_from
    while current <= date_to:
        date_str = current.isoformat()

        if current == today:
            # Today is always re-fetched
            days.append(current)
        elif date_str not in completed:
            # Never completed or failed — needs fetch
            days.append(current)
        elif recheck_days > 0:
            # Check retention policy
            row = completed[date_str]
            lv = row["last_verified_at"]
            if lv:
                last_verified = datetime.fromisoformat(lv)
                age = (datetime.now(tz=UTC) - last_verified).days
                if age >= recheck_days:
                    days.append(current)
            else:
                # Never verified — recheck
                days.append(current)

        current += timedelta(days=1)

    return days


def _sync_one_day(
    conn: Any,
    client: Any,
    fetcher: Callable,
    source: str,
    target_date: date,
    *,
    api_key: str | None,
    email: str,
    on_record: Callable[[dict], None] | None,
    on_progress: Callable[[SyncProgress], None] | None,
) -> tuple[int, int, int, list[str]]:
    """Sync a single (source, day) pair. Returns (added, merged, failed, errors)."""
    added = 0
    merged = 0
    failed = 0
    errors: list[str] = []

    def handle_record(raw: dict):
        nonlocal added, merged, failed
        try:
            pub_date_raw = raw.get("publication_date")
            pub_date = None
            if pub_date_raw and isinstance(pub_date_raw, str):
                pub_date = date.fromisoformat(pub_date_raw)

            pub = Publication(
                doi=raw.get("doi"),
                pmid=raw.get("pmid"),
                title=raw.get("title", ""),
                abstract=raw.get("abstract"),
                authors=raw.get("authors", []),
                journal=raw.get("journal"),
                publication_date=pub_date,
                publication_types=raw.get("publication_types", []),
                keywords=raw.get("keywords", []),
                is_open_access=raw.get("is_open_access", False),
                license=raw.get("license"),
                sources=[raw.get("source", source)],
                first_seen_source=raw.get("source", source),
            )

            result = store_publication(
                conn, pub,
                fulltext_sources=raw.get("fulltext_sources"),
            )

            if result == "added":
                added += 1
            else:
                merged += 1

            if on_record:
                on_record(raw)

        except Exception as e:
            failed += 1
            logger.error("Failed to store record: %s", e)
            errors.append(f"Record error: {e}")

    now = datetime.now(tz=UTC).isoformat()

    # Build fetcher kwargs
    fetcher_kwargs: dict[str, Any] = {
        "on_record": handle_record,
        "on_progress": on_progress,
    }
    if api_key:
        fetcher_kwargs["api_key"] = api_key
    if source == "openalex":
        fetcher_kwargs["email"] = email

    fetch_result = fetcher(client, target_date, **fetcher_kwargs)

    # Update download_days tracker
    # Delete existing row for this (source, day) then insert
    execute(
        conn,
        "DELETE FROM download_days WHERE source = ? AND date = ?",
        (source, target_date.isoformat()),
    )
    execute(
        conn,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at, last_verified_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            source,
            target_date.isoformat(),
            fetch_result.status,
            fetch_result.record_count,
            now,
            now,
        ),
    )
    conn.commit()

    return added, merged, failed, errors


def _get_fetchers() -> dict[str, Callable]:
    """Return the real fetcher functions keyed by source name."""
    from bmlib.publications.fetchers.biorxiv import fetch_biorxiv
    from bmlib.publications.fetchers.openalex import fetch_openalex
    from bmlib.publications.fetchers.pubmed import fetch_pubmed

    def _biorxiv(client, d, **kw):
        return fetch_biorxiv(client, d, server="biorxiv", **kw)

    def _medrxiv(client, d, **kw):
        return fetch_biorxiv(client, d, server="medrxiv", **kw)

    return {
        "pubmed": fetch_pubmed,
        "biorxiv": _biorxiv,
        "medrxiv": _medrxiv,
        "openalex": fetch_openalex,
    }
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publications.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/sync.py tests/test_publications.py
git commit -m "feat(publications): add sync orchestrator with day tracking"
```

---

### Task 8: Public API (`__init__.py`) and pyproject.toml

**Files:**
- Modify: `bmlib/publications/__init__.py` (add exports)
- Modify: `pyproject.toml` (add `publications` optional dependency group)

**Step 1: Write the failing test**

Append to `tests/test_publications.py`:

```python
class TestPublicAPI:
    def test_imports(self):
        """All public API symbols should be importable from bmlib.publications."""
        from bmlib.publications import (
            sync,
            SyncReport,
            Publication,
            FullTextSource,
            DownloadDay,
            SyncProgress,
            FetchResult,
            store_publication,
            get_publication_by_doi,
            get_publication_by_pmid,
            add_fulltext_source,
            ensure_schema,
        )
        # Just verify they imported successfully
        assert sync is not None
        assert Publication is not None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_publications.py::TestPublicAPI -v`
Expected: FAIL with `ImportError`

**Step 3: Update __init__.py and pyproject.toml**

Update `bmlib/publications/__init__.py`:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
# ... (AGPL-3 header) ...

"""Publication ingestion, storage, deduplication, and sync tracking.

Usage::

    from bmlib.db import connect_sqlite
    from bmlib.publications import sync, ensure_schema

    conn = connect_sqlite("~/.myapp/publications.db")
    report = sync(conn, email="user@example.com")
    print(f"Added {report.records_added}, merged {report.records_merged}")
"""

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
from bmlib.publications.sync import sync

__all__ = [
    "sync",
    "SyncReport",
    "Publication",
    "FullTextSource",
    "DownloadDay",
    "SyncProgress",
    "FetchResult",
    "store_publication",
    "get_publication_by_doi",
    "get_publication_by_pmid",
    "add_fulltext_source",
    "ensure_schema",
]
```

Add to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
publications = ["httpx>=0.25"]
```

And update the `all` group:

```toml
all = ["bmlib[anthropic,ollama,postgresql,transparency,publications,dev]"]
```

**Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add bmlib/publications/__init__.py pyproject.toml tests/test_publications.py
git commit -m "feat(publications): finalize public API and add publications dependency group"
```

---

### Task 9: Run full test suite and lint

**Step 1: Run ruff**

Run: `ruff check bmlib/publications/ tests/test_publications.py`
Expected: No errors (fix any that appear)

**Step 2: Run ruff format check**

Run: `ruff format --check bmlib/publications/ tests/test_publications.py`
Expected: No formatting issues (fix any that appear)

**Step 3: Run full pytest**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS, no regressions in existing modules

**Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix: lint and formatting fixes for publications module"
```

(Only if there were fixes needed)
