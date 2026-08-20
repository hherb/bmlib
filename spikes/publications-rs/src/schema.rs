// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! The subset of `publications/schema.py` this slice needs.
//!
//! SQLite only. The PostgreSQL DDL is not reproduced — see FINDINGS.md — but
//! every statement here is written in the intersection of the two dialects
//! except the `AUTOINCREMENT` and the partial unique indexes, which is exactly
//! the split the Python schema makes.

/// DDL for the publications table and the three child tables.
pub const SCHEMA_SQLITE: &str = "
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

CREATE TABLE IF NOT EXISTS fulltext_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    format          TEXT,
    version         TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (publication_id, url)
);

CREATE TABLE IF NOT EXISTS publication_grants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    agency          TEXT,
    grant_id        TEXT,
    country         TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publication_affiliations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    author          TEXT NOT NULL,
    affiliation     TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
";
