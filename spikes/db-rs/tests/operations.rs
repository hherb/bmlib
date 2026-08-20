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

//! Query helpers and the DDL splitter.
//!
//! Sources: `tests/test_db.py::TestOperations`, `::TestCreateTablesTriggers`
//! and `tests/test_migrations.py::TestSplitSqlStatements`.

mod common;

use bmlib_db::operations::{
    create_tables, execute, executemany, fetch_all, fetch_one, fetch_scalar, table_exists,
};
use bmlib_db::split::split_sql_statements;
use bmlib_db::{open_memory, params, placeholders, Db, Value};

use common::both_backends;

// --- test_db.py::TestOperations -------------------------------------------

both_backends!(create_and_query, |db: &mut dyn Db| {
    create_tables(db, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);").unwrap();
    assert!(table_exists(db, "t").unwrap());
    assert!(!table_exists(db, "nope").unwrap());
});

both_backends!(execute_insert_and_fetch, |db: &mut dyn Db| {
    create_tables(db, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);").unwrap();
    execute(db, "INSERT INTO t (v) VALUES (?)", &params!["a"]).unwrap();
    execute(db, "INSERT INTO t (v) VALUES (?)", &params!["b"]).unwrap();
    let rows = fetch_all(db, "SELECT v FROM t ORDER BY v", &[]).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].get_str("v").unwrap(), "a");
});

both_backends!(fetch_one_returns_none, |db: &mut dyn Db| {
    create_tables(db, "CREATE TABLE t (v TEXT);").unwrap();
    assert!(fetch_one(db, "SELECT * FROM t", &[]).unwrap().is_none());
    assert!(fetch_scalar(db, "SELECT v FROM t", &[]).unwrap().is_none());
});

both_backends!(executemany_inserts_each_row, |db: &mut dyn Db| {
    create_tables(db, "CREATE TABLE t (v TEXT);").unwrap();
    executemany(
        db,
        "INSERT INTO t (v) VALUES (?)",
        &[params!["a"], params!["b"], params!["c"]],
    )
    .unwrap();
    assert_eq!(
        fetch_scalar(db, "SELECT COUNT(*) FROM t", &[]).unwrap(),
        Some(Value::Int(3))
    );
});

both_backends!(nulls_round_trip, |db: &mut dyn Db| {
    // Every column of a grant proper is nullable in the real schema, so this
    // is not a corner case there.
    create_tables(db, "CREATE TABLE t (a TEXT, b INTEGER);").unwrap();
    let nothing: Option<&str> = None;
    execute(
        db,
        "INSERT INTO t (a, b) VALUES (?, ?)",
        &params![nothing, 7],
    )
    .unwrap();
    let row = fetch_one(db, "SELECT a, b FROM t", &[]).unwrap().unwrap();
    assert!(row.get("a").unwrap().is_null());
    assert_eq!(row.get_i64("b").unwrap(), 7);
});

both_backends!(
    an_in_clause_is_built_from_placeholders,
    |db: &mut dyn Db| {
        create_tables(db, "CREATE TABLE t (v TEXT);").unwrap();
        executemany(
            db,
            "INSERT INTO t (v) VALUES (?)",
            &[params!["a"], params!["b"], params!["c"]],
        )
        .unwrap();
        let wanted = ["a", "c"];
        let sql = format!(
            "SELECT v FROM t WHERE v IN ({}) ORDER BY v",
            placeholders(wanted.len())
        );
        let args: Vec<Value> = wanted.iter().map(|s| Value::from(*s)).collect();
        let got: Vec<String> = fetch_all(db, &sql, &args)
            .unwrap()
            .iter()
            .map(|r| r.get_str("v").unwrap().to_string())
            .collect();
        assert_eq!(got, ["a", "c"]);
    }
);

#[test]
fn placeholders_is_empty_for_zero() {
    // Same contract as Python: callers must skip the IN clause entirely,
    // because neither backend accepts an empty list.
    assert_eq!(placeholders(0), "");
    assert_eq!(placeholders(1), "?");
    assert_eq!(placeholders(3), "?, ?, ?");
}

// --- test_db.py::TestCreateTablesTriggers ---------------------------------
//
// SQLite only: `CREATE TRIGGER` bodies are what the splitter's nesting rules
// exist for, and the PostgreSQL-semantics harness runs on SQLite anyway, so a
// second run would assert nothing new.

#[test]
fn trigger_with_body_is_one_statement() {
    let mut conn = open_memory().unwrap();
    create_tables(
        &mut conn,
        "
        CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT, updated TEXT);
        CREATE TABLE audit (id INTEGER PRIMARY KEY, note TEXT);
        CREATE TRIGGER t_after_insert AFTER INSERT ON t
        BEGIN
            UPDATE t SET updated = 'yes' WHERE id = NEW.id;
            INSERT INTO audit (note) VALUES ('inserted');
        END;
        ",
    )
    .unwrap();
    execute(&mut conn, "INSERT INTO t (v) VALUES (?)", &params!["x"]).unwrap();
    assert_eq!(
        fetch_scalar(&mut conn, "SELECT updated FROM t", &[]).unwrap(),
        Some(Value::Text("yes".into()))
    );
    assert_eq!(
        fetch_scalar(&mut conn, "SELECT note FROM audit", &[]).unwrap(),
        Some(Value::Text("inserted".into()))
    );
}

#[test]
fn case_expression_inside_trigger_body() {
    let mut conn = open_memory().unwrap();
    create_tables(
        &mut conn,
        "
        CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER, label TEXT);
        CREATE TRIGGER t_label AFTER INSERT ON t
        BEGIN
            UPDATE t
            SET label = CASE WHEN NEW.n > 10 THEN 'big' ELSE 'small' END
            WHERE id = NEW.id;
        END;
        ",
    )
    .unwrap();
    execute(&mut conn, "INSERT INTO t (n) VALUES (?)", &params![42]).unwrap();
    assert_eq!(
        fetch_scalar(&mut conn, "SELECT label FROM t", &[]).unwrap(),
        Some(Value::Text("big".into()))
    );
}

#[test]
fn plain_schema_still_splits() {
    let mut conn = open_memory().unwrap();
    create_tables(
        &mut conn,
        "
        CREATE TABLE a (id INTEGER PRIMARY KEY);
        CREATE TABLE b (id INTEGER PRIMARY KEY);
        CREATE INDEX idx_b ON b (id);
        ",
    )
    .unwrap();
    assert!(table_exists(&mut conn, "a").unwrap());
    assert!(table_exists(&mut conn, "b").unwrap());
}

#[test]
fn bare_begin_outside_trigger_is_not_treated_as_a_body() {
    let stmts = split_sql_statements("CREATE TABLE a (id INT); BEGIN; CREATE TABLE b (id INT);");
    assert_eq!(stmts.len(), 3);
}

// --- test_migrations.py::TestSplitSqlStatements ---------------------------

#[test]
fn splits_respecting_comments_and_strings() {
    let script = "CREATE TABLE a (id INTEGER); -- trailing; comment\n\
                  CREATE INDEX i ON a(id);\n\
                  /* block; comment */ INSERT INTO a VALUES (1);\n\
                  SELECT ';' AS x;";
    assert_eq!(
        split_sql_statements(script),
        [
            "CREATE TABLE a (id INTEGER)",
            "CREATE INDEX i ON a(id)",
            "INSERT INTO a VALUES (1)",
            "SELECT ';' AS x",
        ]
    );
}

#[test]
fn handles_escaped_quotes() {
    assert_eq!(
        split_sql_statements("SELECT 'it''s; ok' AS v; SELECT 2;"),
        ["SELECT 'it''s; ok' AS v", "SELECT 2"]
    );
}

#[test]
fn non_ascii_in_a_script_does_not_panic() {
    // Not in the Python suite: there is nothing there to catch. Python indexes
    // strings by code point, so this is unremarkable; a port that indexed the
    // Rust `&str` by byte would panic on a non-boundary slice. Biomedical
    // metadata is full of these characters, so the guard is worth its line.
    let stmts = split_sql_statements(
        "CREATE TABLE µ (x TEXT DEFAULT 'β; γ'); -- ünicode; comment\nSELECT '—' AS dash;",
    );
    assert_eq!(stmts.len(), 2);
    assert!(stmts[0].contains("'β; γ'"));
    assert_eq!(stmts[1], "SELECT '—' AS dash");
}
