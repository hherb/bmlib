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

Creates tables for publications, full-text sources, and download tracking.
"""

from __future__ import annotations

from typing import Any

from bmlib.db import create_tables

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS publications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doi             TEXT,
    pmid            TEXT,
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

CREATE INDEX IF NOT EXISTS idx_publications_pmid
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


def ensure_schema(conn: Any) -> None:
    """Create all publications tables if they do not exist."""
    create_tables(conn, SCHEMA_SQL)
