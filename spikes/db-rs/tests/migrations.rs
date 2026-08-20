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

//! Migration runner, ported from `tests/test_migrations.py`.
//!
//! **SQLite only, unlike the other two files.** `ensure_version_table` picks
//! its DDL by dialect, and the PostgreSQL branch says `TIMESTAMP NOT NULL
//! DEFAULT NOW()`, which the SQLite the harness wraps cannot parse. That is
//! the harness working as documented: it simulates psycopg2's *driver*
//! semantics, not PostgreSQL's SQL. Shimming `NOW()` here would make the
//! coverage look wider while testing nothing extra.
//!
//! So the PostgreSQL DDL branch of `ensure_version_table` is **unverified**,
//! and stays that way until a real server is available. See FINDINGS.md,
//! "What this spike did not establish".

use std::collections::HashSet;

use bmlib_db::migrations::{get_applied_versions, run_migrations, Migration};
use bmlib_db::operations::{create_tables, fetch_scalar, table_exists};
use bmlib_db::{open_memory, params, Db, DbError, Value};

fn on_sqlite(body: impl FnOnce(&mut dyn Db)) {
    let mut conn = open_memory().expect("open in-memory sqlite");
    body(&mut conn);
}

fn sample() -> Vec<Migration> {
    vec![
        Migration::new(1, "create_t1", |db| {
            create_tables(db, "CREATE TABLE t1 (id INTEGER PRIMARY KEY);")
        }),
        Migration::new(2, "create_t2", |db| {
            create_tables(db, "CREATE TABLE t2 (id INTEGER PRIMARY KEY);")
        }),
    ]
}

#[test]
fn applies_all_on_fresh_db() {
    on_sqlite(|db| {
        assert_eq!(run_migrations(db, sample()).unwrap(), 2);
        assert!(table_exists(db, "t1").unwrap());
        assert!(table_exists(db, "t2").unwrap());
    });
}

#[test]
fn idempotent_second_call() {
    on_sqlite(|db| {
        run_migrations(db, sample()).unwrap();
        assert_eq!(run_migrations(db, sample()).unwrap(), 0);
    });
}

#[test]
fn applies_only_pending() {
    on_sqlite(|db| {
        run_migrations(
            db,
            vec![Migration::new(1, "create_t1", |db| {
                create_tables(db, "CREATE TABLE t1 (id INTEGER PRIMARY KEY);")
            })],
        )
        .unwrap();
        assert_eq!(run_migrations(db, sample()).unwrap(), 1);
        assert!(table_exists(db, "t2").unwrap());
    });
}

#[test]
fn empty_migration_list() {
    on_sqlite(|db| assert_eq!(run_migrations(db, vec![]).unwrap(), 0));
}

#[test]
fn empty_on_fresh_db() {
    on_sqlite(|db| assert_eq!(get_applied_versions(db).unwrap(), HashSet::new()));
}

#[test]
fn returns_applied_versions() {
    on_sqlite(|db| {
        run_migrations(db, sample()).unwrap();
        assert_eq!(
            get_applied_versions(db).unwrap(),
            HashSet::from([1i64, 2i64])
        );
    });
}

#[test]
fn out_of_order_list_still_applies_in_order() {
    on_sqlite(|db| {
        let mut reversed = sample();
        reversed.reverse();
        assert_eq!(run_migrations(db, reversed).unwrap(), 2);
        assert_eq!(
            get_applied_versions(db).unwrap(),
            HashSet::from([1i64, 2i64])
        );
    });
}

#[test]
fn version_names_recorded() {
    on_sqlite(|db| {
        run_migrations(db, sample()).unwrap();
        assert_eq!(
            fetch_scalar(
                db,
                "SELECT name FROM schema_version WHERE version = ?",
                &params![1]
            )
            .unwrap(),
            Some(Value::Text("create_t1".into()))
        );
    });
}

#[test]
fn failed_migration_rolls_back_ddl() {
    // The atomicity guarantee: a migration that fails after creating a table
    // must leave neither the table nor the version row behind.
    on_sqlite(|db| {
        let bad = Migration::new(1, "bad", |db| {
            create_tables(db, "CREATE TABLE t_bad (id INTEGER PRIMARY KEY);")?;
            Err(DbError::abort("boom after DDL"))
        });
        assert!(run_migrations(db, vec![bad]).is_err());
        assert!(
            !table_exists(db, "t_bad").unwrap(),
            "DDL inside a failed migration must roll back"
        );
        assert_eq!(get_applied_versions(db).unwrap(), HashSet::new());
    });
}
