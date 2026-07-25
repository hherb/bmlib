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

"""Database schema for the publications module.

Creates tables for publications, full-text sources, and download tracking on
either backend.  The two DDL strings differ only where the dialects do:
surrogate keys (``AUTOINCREMENT`` vs ``SERIAL``) and booleans (SQLite has
none).  Everything the storage layer leans on — the partial unique indexes on
``doi``/``pmid`` that make cross-source deduplication work, and the ``UNIQUE``
constraints backing ``ON CONFLICT`` — exists in both.
"""

from __future__ import annotations

from typing import Any

from bmlib.db import create_tables, execute, fetch_all, is_sqlite, transaction

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS publications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doi             TEXT,
    pmid            TEXT,
    pmcid           TEXT,
    title           TEXT NOT NULL,
    abstract        TEXT,
    authors         TEXT DEFAULT '[]',
    journal         TEXT,
    publication_date TEXT,
    publication_types TEXT DEFAULT '[]',
    keywords        TEXT DEFAULT '[]',
    is_open_access  INTEGER DEFAULT 0,
    license         TEXT,
    sources         TEXT NOT NULL DEFAULT '[]',
    first_seen_source TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_doi
    ON publications (doi) WHERE doi IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_pmid
    ON publications (pmid) WHERE pmid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_publications_publication_date
    ON publications (publication_date);

CREATE TABLE IF NOT EXISTS fulltext_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    format          TEXT NOT NULL,
    version         TEXT,
    retrieved_at    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(publication_id, url)
);

CREATE TABLE IF NOT EXISTS download_days (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    date            TEXT NOT NULL,
    status          TEXT NOT NULL,
    record_count    INTEGER DEFAULT 0,
    downloaded_at   TEXT NOT NULL,
    last_verified_at TEXT,
    UNIQUE(source, date)
);
"""

SCHEMA_SQL_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS publications (
    id              SERIAL PRIMARY KEY,
    doi             TEXT,
    pmid            TEXT,
    pmcid           TEXT,
    title           TEXT NOT NULL,
    abstract        TEXT,
    authors         TEXT DEFAULT '[]',
    journal         TEXT,
    publication_date TEXT,
    publication_types TEXT DEFAULT '[]',
    keywords        TEXT DEFAULT '[]',
    is_open_access  BOOLEAN DEFAULT FALSE,
    license         TEXT,
    sources         TEXT NOT NULL DEFAULT '[]',
    first_seen_source TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_doi
    ON publications (doi) WHERE doi IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_pmid
    ON publications (pmid) WHERE pmid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_publications_publication_date
    ON publications (publication_date);

CREATE TABLE IF NOT EXISTS fulltext_sources (
    id              SERIAL PRIMARY KEY,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    format          TEXT NOT NULL,
    version         TEXT,
    retrieved_at    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(publication_id, url)
);

CREATE TABLE IF NOT EXISTS download_days (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    date            TEXT NOT NULL,
    status          TEXT NOT NULL,
    record_count    INTEGER DEFAULT 0,
    downloaded_at   TEXT NOT NULL,
    last_verified_at TEXT,
    UNIQUE(source, date)
);
"""

# Columns added after a table's first release. ``CREATE TABLE IF NOT EXISTS``
# is a no-op against a database an earlier bmlib already created, so a new
# column has to be added explicitly or it silently never appears there.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "publications": [("pmcid", "TEXT")],
}


def _existing_columns(conn: Any, table: str) -> set[str]:
    """Return the column names currently present on *table*."""
    if is_sqlite(conn):
        return {row["name"] for row in fetch_all(conn, f"PRAGMA table_info({table})")}
    rows = fetch_all(
        conn,
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    return {row["column_name"] for row in rows}


def _ensure_columns(conn: Any) -> None:
    """Add any post-release columns missing from an existing database.

    Wrapped in a transaction so the ALTERs are committed when
    :func:`ensure_schema` is called standalone — PostgreSQL would otherwise
    leave them pending and lose them when the connection closes — while still
    joining a caller's enclosing block.
    """
    missing = [
        (table, name, col_type)
        for table, columns in _ADDED_COLUMNS.items()
        for name, col_type in columns
        if name not in _existing_columns(conn, table)
    ]
    if not missing:
        return
    with transaction(conn):
        for table, name, col_type in missing:
            execute(conn, f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


def ensure_schema(conn: Any) -> None:
    """Create all publications tables if they do not exist.

    Safe to call repeatedly, and safe to call against a database created by an
    older bmlib: columns added since then are filled in by
    :func:`_ensure_columns`.
    """
    create_tables(conn, SCHEMA_SQL if is_sqlite(conn) else SCHEMA_SQL_POSTGRESQL)
    _ensure_columns(conn)
