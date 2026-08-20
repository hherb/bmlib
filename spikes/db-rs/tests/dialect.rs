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

//! What the PostgreSQL side of the abstraction actually does.
//!
//! These have no Python counterpart: the questions they ask were answered in
//! Python by psycopg2 and by `is_sqlite(conn)`, neither of which exists here.

use bmlib_db::backend::{rewrite_placeholders, Dialect};
use bmlib_db::operations::{create_tables, execute, fetch_all};
use bmlib_db::pg_sim::PgSim;
use bmlib_db::{open_memory, owns_commit, params, transaction, Db};

#[test]
fn placeholders_are_numbered_for_postgres() {
    assert_eq!(
        rewrite_placeholders("INSERT INTO t (a, b, c) VALUES (?, ?, ?)"),
        "INSERT INTO t (a, b, c) VALUES ($1, $2, $3)"
    );
}

#[test]
fn a_question_mark_inside_a_literal_is_left_alone() {
    assert_eq!(
        rewrite_placeholders("SELECT 'why?' AS q WHERE x = ?"),
        "SELECT 'why?' AS q WHERE x = $1"
    );
    assert_eq!(
        rewrite_placeholders(r#"SELECT "od?d" FROM t WHERE v = ?"#),
        r#"SELECT "od?d" FROM t WHERE v = $1"#
    );
    assert_eq!(
        rewrite_placeholders("SELECT 'it''s? fine' AS v, ? AS p"),
        "SELECT 'it''s? fine' AS v, $1 AS p"
    );
}

#[test]
fn a_question_mark_inside_a_comment_is_left_alone() {
    assert_eq!(
        rewrite_placeholders("SELECT ? -- what? really\n, ?"),
        "SELECT $1 -- what? really\n, $2"
    );
    assert_eq!(
        rewrite_placeholders("SELECT /* huh? */ ?"),
        "SELECT /* huh? */ $1"
    );
}

#[test]
fn sqlite_reports_its_own_dialect() {
    let conn = open_memory().unwrap();
    assert_eq!(conn.dialect(), Dialect::Sqlite);
    assert_eq!(
        PgSim::new(open_memory().unwrap()).dialect(),
        Dialect::Postgres
    );
}

#[test]
fn the_postgres_backend_receives_numbered_sql() {
    // End-to-end: a caller writes `?`, the backend is handed `$n`.
    let mut db = PgSim::new(open_memory().unwrap());
    create_tables(&mut db, "CREATE TABLE t (a TEXT, b TEXT);").unwrap();
    execute(
        &mut db,
        "INSERT INTO t (a, b) VALUES (?, ?)",
        &params!["x", "y"],
    )
    .unwrap();

    let numbered = db
        .log()
        .into_iter()
        .find(|s| s.starts_with("INSERT"))
        .expect("the insert reached the backend");
    assert_eq!(numbered, "INSERT INTO t (a, b) VALUES ($1, $2)");
}

#[test]
fn the_driver_transaction_status_is_never_consulted() {
    // The regression this whole design is measured against. psycopg2 leaves a
    // connection INTRANS after a bare SELECT, so the Python port could not ask
    // the driver whether a block was open and had to keep its own count,
    // keyed by (thread, id(conn)).
    //
    // Here `owns_commit` is a constant per implementation, so INTRANS can be
    // true and the answer is still correct.
    let mut db = PgSim::new(open_memory().unwrap());
    create_tables(&mut db, "CREATE TABLE t (v TEXT);").unwrap();
    fetch_all(&mut db, "SELECT * FROM t", &[]).unwrap();

    assert!(
        db.intrans(),
        "the harness must reproduce psycopg2's INTRANS"
    );
    assert!(
        owns_commit(&db),
        "an unwrapped connection owns its commit however the driver feels"
    );

    transaction(&mut db, |tx| {
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["a"])?;
        Ok(())
    })
    .unwrap();

    assert_eq!(fetch_all(&mut db, "SELECT v FROM t", &[]).unwrap().len(), 1);
}

#[test]
fn postgres_has_no_last_insert_rowid() {
    // The one irreducibly dialect-specific need the Python port calls out:
    // `cur.lastrowid` on SQLite, `RETURNING id` on PostgreSQL.
    let mut sqlite = open_memory().unwrap();
    create_tables(
        &mut sqlite,
        "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);",
    )
    .unwrap();
    execute(&mut sqlite, "INSERT INTO t (v) VALUES (?)", &params!["a"]).unwrap();
    assert_eq!(Db::last_insert_rowid(&sqlite), Some(1));

    let pg = PgSim::new(open_memory().unwrap());
    assert_eq!(pg.last_insert_rowid(), None);
}
