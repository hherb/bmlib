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

//! Publications schema — the DDL oracle and its named tests.
//!
//! The DDL is compared byte for byte against Python's, because a
//! `CREATE TABLE IF NOT EXISTS` statement is an artefact already written into
//! every database in the field rather than an implementation detail. A reflow
//! or a reordered column produces a schema that looks equivalent, is not
//! comparable, and would pass any behavioural test.

use bmlib::db::{open_memory, Db};
use bmlib::publications::schema::{
    ensure_schema, existing_columns, ADDED_COLUMNS, SCHEMA_SQL, SCHEMA_SQL_POSTGRESQL,
};
use serde_json::Value;

const EXPECTED: &str = include_str!("data/schema_expected.json");

fn expected() -> Value {
    serde_json::from_str(EXPECTED).expect("schema expectations parse")
}

#[test]
fn the_ddl_matches_python_byte_for_byte() {
    let want = expected();
    assert_eq!(
        SCHEMA_SQL,
        want["sqlite"].as_str().expect("sqlite ddl"),
        "the SQLite DDL diverges from Python's"
    );
    assert_eq!(
        SCHEMA_SQL_POSTGRESQL,
        want["postgresql"].as_str().expect("postgresql ddl"),
        "the PostgreSQL DDL diverges from Python's"
    );
}

#[test]
fn the_post_release_column_list_matches_python() {
    let want = expected();
    let theirs: Vec<(String, String, String)> = want["added_columns"]
        .as_array()
        .expect("list")
        .iter()
        .map(|row| {
            let a = row.as_array().expect("triple");
            (
                a[0].as_str().unwrap_or_default().to_string(),
                a[1].as_str().unwrap_or_default().to_string(),
                a[2].as_str().unwrap_or_default().to_string(),
            )
        })
        .collect();
    let ours: Vec<(String, String, String)> = ADDED_COLUMNS
        .iter()
        .map(|(t, n, c)| ((*t).to_string(), (*n).to_string(), (*c).to_string()))
        .collect();
    assert_eq!(ours, theirs);
}

/// The two dialects must declare the same tables and indexes: they differ only
/// where the dialects do, and a table added to one and not the other would make
/// a backend silently unusable.
#[test]
fn both_dialects_create_the_same_objects() {
    fn objects(ddl: &str) -> Vec<String> {
        ddl.lines()
            .filter_map(|l| {
                let l = l.trim();
                for kw in [
                    "CREATE TABLE IF NOT EXISTS ",
                    "CREATE UNIQUE INDEX IF NOT EXISTS ",
                    "CREATE INDEX IF NOT EXISTS ",
                ] {
                    if let Some(rest) = l.strip_prefix(kw) {
                        return Some(
                            rest.split_whitespace()
                                .next()
                                .unwrap_or_default()
                                .to_string(),
                        );
                    }
                }
                None
            })
            .collect()
    }
    assert_eq!(objects(SCHEMA_SQL), objects(SCHEMA_SQL_POSTGRESQL));
    // And there are fourteen of them, which is what Python's `;` count says.
    assert_eq!(objects(SCHEMA_SQL).len(), 14);
}

/// The unique indexes on `doi` and `pmid` are **partial** (`WHERE … IS NOT
/// NULL`) and that is load-bearing: cross-source deduplication depends on them,
/// while a non-partial unique index would refuse the second NULL — SQLite and
/// PostgreSQL both treat NULLs as distinct, so a plain UNIQUE would be
/// harmless there, but the partial form is what the schema states and what the
/// lookup expects.
#[test]
fn the_deduplication_indexes_are_partial_and_unique() {
    for ddl in [SCHEMA_SQL, SCHEMA_SQL_POSTGRESQL] {
        assert!(ddl.contains("CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_doi"));
        assert!(ddl.contains("CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_pmid"));
        assert!(ddl.contains("ON publications (doi) WHERE doi IS NOT NULL"));
        assert!(ddl.contains("ON publications (pmid) WHERE pmid IS NOT NULL"));
    }
}

/// `ensure_schema` creates every table against a real database, and is safe to
/// run repeatedly — which is what makes it the documented upgrade step.
#[test]
fn ensure_schema_is_idempotent() {
    let mut conn = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut conn).expect("first run");
    ensure_schema(&mut conn).expect("second run");

    for table in [
        "publications",
        "fulltext_sources",
        "download_days",
        "download_day_parts",
        "retraction_notices",
        "publication_grants",
        "publication_affiliations",
    ] {
        let columns = existing_columns(&mut conn, table).expect("catalog read");
        assert!(!columns.is_empty(), "{table} was not created");
    }
}

/// The `UNIQUE` constraints the storage layer leans on are really there, not
/// merely written in the DDL string. `fulltext_sources` dedups on
/// `(publication_id, url)`, which is what makes re-storing a source's URLs
/// idempotent.
#[test]
fn the_unique_constraints_are_enforced_by_the_database() {
    use bmlib::db::execute;

    let mut conn = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut conn).expect("schema");

    execute(
        &mut conn,
        "INSERT INTO publications (title, sources, first_seen_source, created_at, updated_at) \
         VALUES ('T', '[]', 's', 'now', 'now')",
        &[],
    )
    .expect("publication");

    let insert_source = "INSERT INTO fulltext_sources \
         (publication_id, source, url, format, created_at) VALUES (1, 's', 'u', 'xml', 'now')";
    execute(&mut conn, insert_source, &[]).expect("first insert");
    assert!(
        execute(&mut conn, insert_source, &[]).is_err(),
        "the (publication_id, url) UNIQUE must reject the duplicate"
    );

    // `download_days` dedups on (source, date).
    let insert_day = "INSERT INTO download_days \
         (source, date, status, downloaded_at) VALUES ('s', '2024-01-02', 'completed', 'now')";
    execute(&mut conn, insert_day, &[]).expect("first day");
    assert!(execute(&mut conn, insert_day, &[]).is_err());
}

/// The schema's `NOT NULL` columns are enforced — the validators in
/// `publications::models` exist to produce good errors *before* this, but the
/// database is the backstop and a port that dropped a constraint would lose it.
#[test]
fn not_null_columns_are_enforced() {
    use bmlib::db::execute;

    let mut conn = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut conn).expect("schema");

    assert!(
        execute(
            &mut conn,
            "INSERT INTO publications (sources, first_seen_source, created_at, updated_at) \
             VALUES ('[]', 's', 'now', 'now')",
            &[],
        )
        .is_err(),
        "title is NOT NULL"
    );
    assert!(
        execute(
            &mut conn,
            "INSERT INTO publication_grants (publication_id, agency, created_at) \
             VALUES (1, 'A', 'now')",
            &[],
        )
        .is_err(),
        "publication_grants.source is NOT NULL"
    );
}

/// `pmcid` is the one post-release column, so it must be present on a database
/// created from the current DDL — and `ensure_columns` must not try to add it
/// again, which would be an error rather than a no-op on both backends.
#[test]
fn the_post_release_column_is_present_and_not_re_added() {
    let mut conn = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut conn).expect("schema");

    let columns = existing_columns(&mut conn, "publications").expect("catalog read");
    assert!(columns.contains(&"pmcid".to_string()), "{columns:?}");

    // The reconciliation is what a caller's second call runs, and it must find
    // nothing to do.
    bmlib::publications::schema::ensure_columns(&mut conn).expect("no-op");
}

/// The dialect is the connection's own answer, so `ensure_schema` picks the
/// right DDL rather than being told — which is what keeps a caller from writing
/// SQLite DDL into PostgreSQL.
#[test]
fn ensure_schema_reads_the_dialect_from_the_connection() {
    use bmlib::db::backend::Dialect;
    let conn = open_memory().expect("in-memory sqlite");
    assert_eq!(conn.dialect(), Dialect::Sqlite);
}
