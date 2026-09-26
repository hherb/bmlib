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

//! Database schema for the publications module.
//!
//! A port of `bmlib/publications/schema.py`. Creates the tables for
//! publications, full-text sources, download tracking, grants, affiliations and
//! retraction notices on either backend.
//!
//! The two DDL strings differ only where the dialects do: surrogate keys
//! (`AUTOINCREMENT` vs `SERIAL`) and booleans (SQLite has none). Everything the
//! storage layer leans on — the partial unique indexes on `doi`/`pmid` that
//! make cross-source deduplication work, and the `UNIQUE` constraints backing
//! `ON CONFLICT` — exists in both.
//!
//! # Why the DDL is reproduced byte for byte
//!
//! A `CREATE TABLE IF NOT EXISTS` statement is an **artefact**, not an
//! implementation: it is what is already written into every database in the
//! field. A port that reformats the DDL, reorders the columns or "tidies" the
//! whitespace produces a schema that *looks* equivalent and is not comparable
//! — and nothing would catch the difference, because both would create working
//! tables. The oracle compares these strings exactly, so a well-meant reflow is
//! a test failure rather than a silent divergence.

use crate::db::backend::Dialect;
use crate::db::operations::{create_tables, execute, fetch_all};
use crate::db::{Db, DbError, Value};

/// The SQLite DDL.
pub const SCHEMA_SQL: &str = r"
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

CREATE TABLE IF NOT EXISTS download_day_parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    date            TEXT NOT NULL,
    part_scheme     TEXT NOT NULL,
    part_key        TEXT NOT NULL,
    promised        INTEGER NOT NULL,
    record_count    INTEGER NOT NULL,
    completed_at    TEXT NOT NULL,
    UNIQUE(source, date, part_key)
);

CREATE TABLE IF NOT EXISTS retraction_notices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       TEXT NOT NULL UNIQUE,
    doi             TEXT,
    pmid            TEXT,
    notice_doi      TEXT,
    notice_pmid     TEXT,
    nature          TEXT NOT NULL,
    raw_nature      TEXT,
    title           TEXT,
    journal         TEXT,
    retraction_date TEXT,
    original_paper_date TEXT,
    reasons         TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_doi
    ON retraction_notices (doi);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_pmid
    ON retraction_notices (pmid);

CREATE TABLE IF NOT EXISTS publication_grants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    agency          TEXT,
    grant_id        TEXT,
    country         TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publication_grants_publication_id
    ON publication_grants (publication_id);

CREATE TABLE IF NOT EXISTS publication_affiliations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    author          TEXT NOT NULL,
    affiliation     TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publication_affiliations_publication_id
    ON publication_affiliations (publication_id);
";

/// The PostgreSQL DDL.
///
/// `SERIAL` rather than the newer `GENERATED BY DEFAULT AS IDENTITY`, on
/// purpose. The two are equivalent for everything this schema does, and
/// `CREATE TABLE IF NOT EXISTS` never rewrites a table that already exists — so
/// switching would not migrate one existing database, it would only make new
/// ones differ from every database already in the field. Divergence with no
/// behavioural gain is the wrong trade for a library several projects depend
/// on.
pub const SCHEMA_SQL_POSTGRESQL: &str = r"
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

CREATE TABLE IF NOT EXISTS download_day_parts (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    date            TEXT NOT NULL,
    part_scheme     TEXT NOT NULL,
    part_key        TEXT NOT NULL,
    promised        INTEGER NOT NULL,
    record_count    INTEGER NOT NULL,
    completed_at    TEXT NOT NULL,
    UNIQUE(source, date, part_key)
);

CREATE TABLE IF NOT EXISTS retraction_notices (
    id              SERIAL PRIMARY KEY,
    record_id       TEXT NOT NULL UNIQUE,
    doi             TEXT,
    pmid            TEXT,
    notice_doi      TEXT,
    notice_pmid     TEXT,
    nature          TEXT NOT NULL,
    raw_nature      TEXT,
    title           TEXT,
    journal         TEXT,
    retraction_date TEXT,
    original_paper_date TEXT,
    reasons         TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_doi
    ON retraction_notices (doi);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_pmid
    ON retraction_notices (pmid);

CREATE TABLE IF NOT EXISTS publication_grants (
    id              SERIAL PRIMARY KEY,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    agency          TEXT,
    grant_id        TEXT,
    country         TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publication_grants_publication_id
    ON publication_grants (publication_id);

CREATE TABLE IF NOT EXISTS publication_affiliations (
    id              SERIAL PRIMARY KEY,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    source          TEXT NOT NULL,
    author          TEXT NOT NULL,
    affiliation     TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publication_affiliations_publication_id
    ON publication_affiliations (publication_id);
";

/// Columns added after a table's first release.
///
/// `CREATE TABLE IF NOT EXISTS` is a no-op against a database an earlier bmlib
/// already created, so a new column has to be added explicitly or it silently
/// never appears there.
///
/// Deliberately **not** the general migration runner. That one records applied
/// versions in a `schema_version` table, which databases created by an earlier
/// bmlib do not have and cannot be assumed to own — several consumers point
/// bmlib at a database they also use for their own tables, and claiming that
/// table name retroactively would be a breaking change. Reconciling against the
/// live column list needs no bookkeeping, is idempotent, and cannot disagree
/// with the database's actual state.
pub const ADDED_COLUMNS: [(&str, &str, &str); 1] = [("publications", "pmcid", "TEXT")];

/// The column names currently present on `table`.
///
/// The PostgreSQL lookup is restricted to `current_schema()` — the schema
/// `CREATE TABLE` just wrote to. `information_schema.columns` spans every
/// schema the connected user can see, so an unqualified query would answer
/// about some *other* database's `publications` table: a second consumer in its
/// own schema, upgraded at a different time, is enough to report a column as
/// present that this schema does not have. The `ALTER` would then be skipped
/// and the next write would fail on the missing column.
///
/// # Errors
///
/// Propagates whatever the catalog query raises.
pub fn existing_columns(db: &mut dyn Db, table: &str) -> Result<Vec<String>, DbError> {
    if db.dialect() == Dialect::Sqlite {
        let rows = fetch_all(db, &format!("PRAGMA table_info({table})"), &[])?;
        return Ok(rows
            .iter()
            .filter_map(|r| {
                r.get("name")
                    .ok()
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .collect());
    }
    let rows = fetch_all(
        db,
        // `concat!` rather than a `\` continuation: a trailing backslash eats
        // the next line's leading whitespace, so the two fragments would meet
        // as `columnsWHERE`. Python's adjacent literals keep the space, and
        // this keeps the boundary visible. Nothing caught it until the
        // PostgreSQL branch ran against a real server — the SQLite branch
        // returns before this line, and the simulated connection's catalog
        // shim only intercepts `information_schema.tables`.
        concat!(
            "SELECT column_name FROM information_schema.columns",
            " WHERE table_name = ? AND table_schema = current_schema()"
        ),
        &[Value::Text(table.to_string())],
    )?;
    Ok(rows
        .iter()
        .filter_map(|r| {
            r.get("column_name")
                .ok()
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect())
}

/// Add any post-release columns missing from an existing database.
///
/// Wrapped in a transaction so the `ALTER`s are committed when
/// [`ensure_schema`] is called standalone — PostgreSQL would otherwise leave
/// them pending and lose them when the connection closes — while still joining
/// a caller's enclosing block.
///
/// # Errors
///
/// Propagates the catalog query or the `ALTER`.
pub fn ensure_columns(db: &mut dyn Db) -> Result<(), DbError> {
    let mut missing: Vec<(&str, &str, &str)> = Vec::new();
    for (table, name, col_type) in ADDED_COLUMNS {
        let present = existing_columns(db, table)?;
        if !present.iter().any(|c| c == name) {
            missing.push((table, name, col_type));
        }
    }
    if missing.is_empty() {
        return Ok(());
    }
    let mut tx = db.begin()?;
    for (table, name, col_type) in missing {
        execute(
            &mut *tx,
            &format!("ALTER TABLE {table} ADD COLUMN {name} {col_type}"),
            &[],
        )?;
    }
    tx.commit()
}

/// Create all publications tables if they do not exist.
///
/// Safe to call repeatedly, and safe to call against a database created by an
/// older bmlib: columns added since then are filled in by [`ensure_columns`].
///
/// **Call this once after upgrading bmlib.** Reads tolerate a database that has
/// not been through it — [`crate::publications::storage`] treats a post-release
/// column as absent rather than raising — but writes do not:
/// `store_publication` names every column in its `INSERT` and will fail on one
/// the database lacks.
///
/// # Errors
///
/// Propagates the DDL or the column reconciliation.
pub fn ensure_schema(db: &mut dyn Db) -> Result<(), DbError> {
    let ddl = if db.dialect() == Dialect::Sqlite {
        SCHEMA_SQL
    } else {
        SCHEMA_SQL_POSTGRESQL
    };
    create_tables(db, ddl)?;
    ensure_columns(db)
}
