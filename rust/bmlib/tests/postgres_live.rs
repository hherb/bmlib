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

//! The PostgreSQL backend, against a **real server**.
//!
//! `tests/dialect.rs` runs the dialect rules through a simulated connection
//! because, when it was written, no PostgreSQL server was reachable. That
//! simulation cannot answer the questions this file exists for: whether a
//! neutral [`Value`] encodes into a real `BOOLEAN`, whether `SERIAL` plus
//! `RETURNING id` yields a usable id, whether a nested block is really a
//! savepoint, and whether the PostgreSQL DDL applies at all.
//!
//! # Gating
//!
//! **The default `cargo test` opens no socket.** The suite runs only when
//! `BMLIB_PG_TESTS=1`, and only in a build with `--features postgres`; without
//! the variable each test returns immediately, so the binary still reports the
//! same count (a suite that silently disappeared would be worse than one that
//! skips). The connection comes from:
//!
//! | Variable | Default |
//! |---|---|
//! | `BMLIB_PG_TESTS` | unset — set to `1` to run |
//! | `BMLIB_PG_HOST` | `127.0.0.1` |
//! | `BMLIB_PG_PORT` | `5432` |
//! | `BMLIB_PG_USER` | `$USER` |
//! | `BMLIB_PG_PASSWORD` | empty (left unset) |
//! | `BMLIB_PG_ADMIN_DB` | `postgres` |
//!
//! # Isolation
//!
//! Each test creates its **own database** and drops it on the way out. A schema
//! per test would be cheaper, but `table_exists` asks `information_schema`
//! without a schema filter — deliberately, because that is what the Python
//! original does — so two tests sharing a server would see each other's tables.
//! A database each is the smallest isolation that keeps that behaviour honest.
//!
//! Run it with:
//!
//! ```text
//! BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live
//! ```

#![cfg(feature = "postgres")]

use std::sync::atomic::{AtomicUsize, Ordering};

use postgres::{Client, NoTls};

use bmlib::db::{
    connect_postgresql_params, create_tables, execute, fetch_all, fetch_one, fetch_scalar,
    run_migrations, table_exists, transaction, Db, DbError, Dialect, Migration, Value,
};
use bmlib::params;
use bmlib::publications::models::{FullTextSource, Grant, Publication};
use bmlib::publications::schema::{ensure_schema, existing_columns};
use bmlib::publications::storage::{
    add_fulltext_source, get_publication_by_doi, insert_publication, store_publication,
    StoreOutcome,
};

/// Distinguishes databases created by concurrently running tests.
static NEXT_DB: AtomicUsize = AtomicUsize::new(0);

/// Where and as whom to connect.
struct PgConfig {
    host: String,
    port: u16,
    user: String,
    password: String,
    admin_db: String,
}

/// The connection settings, or `None` when the suite is not enabled.
fn pg_config() -> Option<PgConfig> {
    if std::env::var("BMLIB_PG_TESTS").ok().as_deref() != Some("1") {
        return None;
    }
    Some(PgConfig {
        host: std::env::var("BMLIB_PG_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
        port: std::env::var("BMLIB_PG_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(5432),
        user: std::env::var("BMLIB_PG_USER")
            .ok()
            .or_else(|| std::env::var("USER").ok())
            .unwrap_or_else(|| "postgres".to_string()),
        password: std::env::var("BMLIB_PG_PASSWORD").unwrap_or_default(),
        admin_db: std::env::var("BMLIB_PG_ADMIN_DB").unwrap_or_else(|_| "postgres".to_string()),
    })
}

/// A private database, dropped when the test finishes.
struct TestDb {
    client: Option<Box<dyn Db>>,
    admin: Client,
    name: String,
}

impl TestDb {
    /// Create the database and connect [`Db`] to it, or `None` when gated off.
    fn new() -> Option<TestDb> {
        let config = pg_config()?;
        let mut admin_config = postgres::Config::new();
        admin_config
            .host(&config.host)
            .port(config.port)
            .dbname(&config.admin_db)
            .user(&config.user);
        if !config.password.is_empty() {
            admin_config.password(&config.password);
        }
        let mut admin = admin_config
            .connect(NoTls)
            .expect("admin connection to PostgreSQL");

        let name = format!(
            "bmlib_rs_{}_{}",
            std::process::id(),
            NEXT_DB.fetch_add(1, Ordering::SeqCst)
        );
        // A previous run that panicked may have left the name behind.
        admin
            .batch_execute(&format!("DROP DATABASE IF EXISTS \"{name}\""))
            .expect("drop a stale test database");
        admin
            .batch_execute(&format!("CREATE DATABASE \"{name}\""))
            .expect("create the test database");

        let client = connect_postgresql_params(
            &config.host,
            config.port,
            &name,
            &config.user,
            &config.password,
        )
        .expect("bmlib's own connector");

        Some(TestDb {
            client: Some(Box::new(client)),
            admin,
            name,
        })
    }

    /// The connection under test.
    fn db(&mut self) -> &mut dyn Db {
        &mut **self.client.as_mut().expect("the test connection")
    }
}

impl Drop for TestDb {
    fn drop(&mut self) {
        // Close our own connection first, or the DROP finds it open.
        self.client.take();
        let _ = self.admin.batch_execute(&format!(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity \
             WHERE datname = '{}' AND pid <> pg_backend_pid()",
            self.name
        ));
        let _ = self
            .admin
            .batch_execute(&format!("DROP DATABASE IF EXISTS \"{}\"", self.name));
    }
}

/// One scalar integer, or a panic naming what arrived instead.
fn count(db: &mut dyn Db, sql: &str) -> i64 {
    match fetch_scalar(db, sql, &[]).expect("scalar") {
        Some(Value::Int(i)) => i,
        other => panic!("expected an integer, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// The connection
// ---------------------------------------------------------------------------

/// The connector opens a real connection and the trait reports the dialect the
/// placeholder rewriter and the DDL both key on.
#[test]
fn a_real_connection_reports_the_postgres_dialect() {
    let Some(mut t) = TestDb::new() else { return };
    assert_eq!(t.db().dialect(), Dialect::Postgres);
    assert_eq!(
        fetch_scalar(t.db(), "SELECT 1", &[]).expect("scalar"),
        Some(Value::Int(1))
    );
}

// ---------------------------------------------------------------------------
// Operations
// ---------------------------------------------------------------------------

/// `?` placeholders are rewritten to `$n` and the round trip survives a real
/// server: text, integer, boolean, blob and NULL.
#[test]
fn parameters_round_trip_through_the_rewriter() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    create_tables(
        db,
        "CREATE TABLE probe (id SERIAL PRIMARY KEY, name TEXT, n INTEGER, flag BOOLEAN, data BYTEA);",
    )
    .expect("create");
    execute(
        db,
        "INSERT INTO probe (name, n, flag, data) VALUES (?, ?, ?, ?)",
        &params!["first", 42_i64, true, vec![1u8, 2, 3]],
    )
    .expect("insert");
    execute(
        db,
        "INSERT INTO probe (name, n, flag, data) VALUES (?, ?, ?, ?)",
        &[Value::Null, Value::Int(0), Value::from(false), Value::Null],
    )
    .expect("insert nulls");

    let rows =
        fetch_all(db, "SELECT name, n, flag, data FROM probe ORDER BY id", &[]).expect("select");
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].get_str("name").expect("text"), "first");
    assert_eq!(rows[0].get_i64("n").expect("int"), 42);
    assert_eq!(rows[0].get_i64("flag").expect("bool as 0/1"), 1);
    assert_eq!(
        rows[0].get("data").expect("blob").as_blob(),
        Some(&[1u8, 2, 3][..])
    );
    assert!(rows[1].get("name").expect("column").is_null());
    assert_eq!(rows[1].get_i64("flag").expect("false"), 0);
    assert!(rows[1].get("data").expect("column").is_null());

    // The id is `SERIAL` (`int4`), so `fetch_scalar` must narrow it for us.
    assert_eq!(count(db, "SELECT COUNT(*) FROM probe"), 2);
}

/// A `SERIAL` primary key is readable and monotonic, which is what
/// `insert_publication`'s `RETURNING id` arm depends on.
#[test]
fn returning_id_yields_the_new_row() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    create_tables(db, "CREATE TABLE t (id SERIAL PRIMARY KEY, v TEXT);").expect("create");
    let first = fetch_one(
        db,
        "INSERT INTO t (v) VALUES (?) RETURNING id",
        &params!["a"],
    )
    .expect("returning")
    .expect("a row");
    let second = fetch_one(
        db,
        "INSERT INTO t (v) VALUES (?) RETURNING id",
        &params!["b"],
    )
    .expect("returning")
    .expect("a row");
    assert_eq!(first.get_i64("id").expect("id"), 1);
    assert_eq!(second.get_i64("id").expect("id"), 2);
}

/// The catalog query is scoped to `current_schema()`: a table of the same name
/// in another schema is not this schema's table.
#[test]
fn existing_columns_ignores_another_schema() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    execute(db, "CREATE SCHEMA elsewhere", &[]).expect("schema");
    execute(
        db,
        "CREATE TABLE elsewhere.publications (id INTEGER, only_there TEXT)",
        &[],
    )
    .expect("other table");
    assert!(
        existing_columns(db, "publications")
            .expect("columns")
            .is_empty(),
        "a table in another schema was read as this one's"
    );
    execute(db, "CREATE TABLE publications (id INTEGER, here TEXT)", &[]).expect("own table");
    let mut columns = existing_columns(db, "publications").expect("columns");
    columns.sort();
    assert_eq!(columns, vec!["here".to_string(), "id".to_string()]);
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

/// A successful block commits, and a failing one leaves nothing behind.
#[test]
fn a_transaction_commits_and_a_failure_rolls_back() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    create_tables(db, "CREATE TABLE t (v TEXT);").expect("create");

    transaction(db, |tx| {
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["kept"])?;
        Ok(())
    })
    .expect("commit");
    assert_eq!(count(db, "SELECT COUNT(*) FROM t"), 1);

    let failed: Result<(), DbError> = transaction(db, |tx| {
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["discarded"])?;
        Err(DbError::abort(std::io::Error::other("boom")))
    });
    assert!(failed.is_err());
    assert_eq!(count(db, "SELECT COUNT(*) FROM t"), 1);
    assert_eq!(
        fetch_scalar(db, "SELECT v FROM t", &[]).expect("scalar"),
        Some(Value::Text("kept".to_string()))
    );
}

/// A nested block is a savepoint: its failure rolls back only its own writes
/// and leaves the outer block usable.
#[test]
fn a_failed_inner_block_rolls_back_only_itself() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    create_tables(db, "CREATE TABLE t (v TEXT);").expect("create");

    transaction(db, |tx| {
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["outer"])?;
        let inner: Result<(), DbError> = transaction(tx, |inner| {
            execute(inner, "INSERT INTO t (v) VALUES (?)", &params!["inner"])?;
            Err(DbError::abort(std::io::Error::other("boom")))
        });
        assert!(inner.is_err(), "the inner block must report its failure");
        // The outer block is still usable after the savepoint rolled back.
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["after"])?;
        Ok(())
    })
    .expect("outer commit");

    let rows = fetch_all(db, "SELECT v FROM t ORDER BY v", &[]).expect("select");
    let seen: Vec<&str> = rows.iter().filter_map(|r| r.get_str("v").ok()).collect();
    assert_eq!(seen, vec!["after", "outer"]);
}

// ---------------------------------------------------------------------------
// Migrations
// ---------------------------------------------------------------------------

/// The runner creates its version table with PostgreSQL's own `NOW()` default,
/// applies a migration once, and records it.
#[test]
fn migrations_apply_once_on_postgres() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();

    let migrations = || {
        vec![Migration::new(1, "create mig_test", |tx| {
            create_tables(
                tx,
                "CREATE TABLE mig_test (id SERIAL PRIMARY KEY, note TEXT);",
            )
        })]
    };
    assert_eq!(run_migrations(db, migrations()).expect("first run"), 1);
    assert_eq!(run_migrations(db, migrations()).expect("second run"), 0);
    assert!(table_exists(db, "mig_test").expect("exists"));

    // `applied_at` is a real PostgreSQL TIMESTAMP with a server-side default;
    // nothing reads it, but the insert must not need it either.
    assert_eq!(count(db, "SELECT COUNT(*) FROM schema_version"), 1);
}

// ---------------------------------------------------------------------------
// The publications store
// ---------------------------------------------------------------------------

/// The full schema applies, a record stores, and the second sight of the same
/// DOI merges rather than duplicating — with the `BOOLEAN` open-access flag
/// latching on through the merge.
#[test]
fn the_publications_schema_stores_and_merges() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    ensure_schema(db).expect("schema");
    ensure_schema(db).expect("schema is idempotent");

    let mut first = Publication::new("First title", "pubmed");
    first.doi = Some("10.1000/XYZ".to_string());
    first.is_open_access = false;
    assert_eq!(
        store_publication(db, &mut first, &[], &[], &[]).expect("store"),
        StoreOutcome::Added
    );

    let mut second = Publication::new("Second title", "openalex");
    second.doi = Some("10.1000/xyz".to_string());
    second.is_open_access = true;
    assert_eq!(
        store_publication(db, &mut second, &[], &[], &[]).expect("store"),
        StoreOutcome::Merged
    );

    assert_eq!(count(db, "SELECT COUNT(*) FROM publications"), 1);
    let stored = get_publication_by_doi(db, "10.1000/XYZ")
        .expect("read")
        .expect("a row");
    assert!(stored.is_open_access, "the flag must latch on, not reset");
    assert!(stored.sources.contains(&"pubmed".to_string()));
    assert!(stored.sources.contains(&"openalex".to_string()));
}

/// A repeated `(publication_id, url)` is skipped by `ON CONFLICT … DO NOTHING`
/// rather than duplicated — the insert form that lost its spaces in the port.
#[test]
fn a_repeated_fulltext_url_is_not_duplicated() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    ensure_schema(db).expect("schema");
    let publication = Publication::new("A paper", "pubmed");
    let id = insert_publication(db, &publication, "2026-01-01T00:00:00+00:00").expect("insert");

    assert!(
        add_fulltext_source(db, id, "pubmed", "https://example.test/x", "xml", None)
            .expect("first insert")
    );
    assert!(
        !add_fulltext_source(db, id, "pubmed", "https://example.test/x", "xml", None)
            .expect("second insert"),
        "the same (publication_id, url) must not insert twice"
    );
    assert_eq!(count(db, "SELECT COUNT(*) FROM fulltext_sources"), 1);
}

/// A split identity — one row carrying the DOI, another the PMID — consolidates
/// when a record carries both, moving the drop row's children onto the keep row.
///
/// This is the path whose SQL was mangled by lost line-continuation spaces: the
/// `DELETE … ?AND …` and `UPDATE … ?WHERE …` statements only run here, so only
/// a live server could show it.
#[test]
fn a_split_identity_consolidates_its_children() {
    let Some(mut t) = TestDb::new() else { return };
    let db = t.db();
    ensure_schema(db).expect("schema");

    let mut doi_row = Publication::new("DOI row", "pubmed");
    doi_row.doi = Some("10.1000/split".to_string());
    let doi_source = FullTextSource::new(0, "pubmed", "https://example.test/doi", "xml");
    let doi_grant = Grant {
        id: None,
        publication_id: 0,
        source: "pubmed".to_string(),
        agency: Some("Agency A".to_string()),
        grant_id: None,
        country: None,
    };
    store_publication(db, &mut doi_row, &[doi_source], &[doi_grant], &[]).expect("store DOI row");

    let mut pmid_row = Publication::new("PMID row", "pubmed");
    pmid_row.pmid = Some("12345678".to_string());
    let pmid_source = FullTextSource::new(0, "pubmed", "https://example.test/pmid", "pdf");
    let pmid_grant = Grant {
        id: None,
        publication_id: 0,
        source: "pubmed".to_string(),
        agency: Some("Agency B".to_string()),
        grant_id: None,
        country: None,
    };
    store_publication(db, &mut pmid_row, &[pmid_source], &[pmid_grant], &[])
        .expect("store PMID row");
    assert_eq!(count(db, "SELECT COUNT(*) FROM publications"), 2);

    // A record carrying both identifiers joins the two rows.
    let mut both = Publication::new("Both identifiers", "openalex");
    both.doi = Some("10.1000/split".to_string());
    both.pmid = Some("12345678".to_string());
    assert_eq!(
        store_publication(db, &mut both, &[], &[], &[]).expect("consolidate"),
        StoreOutcome::Merged
    );

    assert_eq!(count(db, "SELECT COUNT(*) FROM publications"), 1);
    // Both URLs moved onto the surviving row.
    assert_eq!(count(db, "SELECT COUNT(*) FROM fulltext_sources"), 2);
    assert_eq!(
        count(
            db,
            "SELECT COUNT(DISTINCT publication_id) FROM fulltext_sources"
        ),
        1
    );
    // The two grants share a source, so the drop row's was discarded rather than
    // duplicated — the branch the lost space would have broken.
    assert_eq!(count(db, "SELECT COUNT(*) FROM publication_grants"), 1);
}
