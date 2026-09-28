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

//! The differential corpus for `db/` — the one package that had none.
//!
//! What is diffed is what a *caller* can see: the statements the splitter
//! produces, the dialect's placeholder spellings, the value shapes a fetch
//! returns, which tables exist, and — the part that matters most — what a nested
//! `transaction` block commits when it succeeds and rolls back when it raises.
//! Python's *mechanism* has no counterpart: `_depths` and `_depth_key` are
//! replaced by the type of the value in hand, so a `Connection::begin()` opens a
//! transaction and a `Transaction::begin()` opens a savepoint. The outcomes are
//! what has to agree, and these cases are how that is measured rather than
//! asserted.
//!
//! Cases are step scripts (`rust/oracle/dump_db.py` runs the same ones against
//! Python), so the vocabulary is deliberately small and total: an unknown op is a
//! panic rather than a silently-skipped step, and the trace records every step's
//! outcome so a divergence names the step it happened on.

use std::collections::BTreeMap;

use bmlib::db::migrations::{get_applied_versions, run_migrations, Migration};
use bmlib::db::operations::{
    create_tables, execute, executemany, fetch_all, fetch_one, fetch_scalar, table_exists,
};
use bmlib::db::split::split_sql_statements;
use bmlib::db::{open_memory, placeholders, transaction, Db, DbError, Value as DbValue};
use serde_json::{json, Map, Value};

const CASES: &str = include_str!("data/db_cases.json");
const EXPECTED: &str = include_str!("data/db_expected.json");

/// The message Python's scripted failure raises, which the trace carries.
///
/// Both sides script the same failure by *raising*, and Python's trace records
/// `f"{type(exc).__name__}: {exc}"`; here the error travels as a `DbError` because
/// `transaction` is typed, so the string is reproduced rather than derived.
const SCRIPTED_FAILURE: &str = "RuntimeError: scripted failure";

fn case_value(value: &DbValue) -> Value {
    match value {
        DbValue::Null => Value::Null,
        DbValue::Int(i) => json!(i),
        DbValue::Real(f) => json!(f),
        DbValue::Text(s) => json!(s),
        DbValue::Blob(bytes) => json!({"blob": hex(bytes)}),
    }
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// A scripted JSON parameter as a database value — the inverse of [`case_value`].
fn param(value: &Value) -> DbValue {
    match value {
        Value::Null => DbValue::Null,
        Value::Number(number) => match number.as_i64() {
            Some(i) => DbValue::Int(i),
            None => DbValue::Real(number.as_f64().expect("a scripted number")),
        },
        Value::String(text) => DbValue::Text(text.clone()),
        Value::Object(object) if object.len() == 1 && object.contains_key("blob") => {
            let text = object["blob"].as_str().expect("a hex string");
            DbValue::Blob(
                (0..text.len())
                    .step_by(2)
                    .map(|i| u8::from_str_radix(&text[i..i + 2], 16).expect("hex"))
                    .collect(),
            )
        }
        other => panic!("no database value for the scripted parameter {other}"),
    }
}

fn params(step: &Value) -> Vec<DbValue> {
    step.get("params")
        .and_then(Value::as_array)
        .map(|items| items.iter().map(param).collect())
        .unwrap_or_default()
}

/// A row as JSON, in column order — Python's row factory is a tuple, so the two
/// sides read the same way.
fn row_value(row: &bmlib::db::Row) -> Value {
    let mut values = Vec::new();
    let mut index = 0;
    while let Some(value) = row.at(index) {
        values.push(case_value(value));
        index += 1;
    }
    Value::Array(values)
}

fn rows_value(rows: &[bmlib::db::Row]) -> Value {
    Value::Array(rows.iter().map(row_value).collect())
}

/// Every table that exists, and its rows — read through the same helpers a caller
/// has, so the snapshot cannot see more than the layer under test does.
fn state(db: &mut dyn Db) -> Result<Value, DbError> {
    let names = fetch_all(
        db,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' \
         ORDER BY name",
        &[],
    )?;
    let mut tables = Map::new();
    for row in &names {
        let name = match row.at(0) {
            Some(DbValue::Text(name)) => name.clone(),
            other => panic!("a table name, not {other:?}"),
        };
        // The version table's `applied_at` defaults to the wall clock, so diffing
        // it would fail on every run for no reason; which versions were recorded,
        // under which names, is what a migration run is judged by.
        let query = if name == "schema_version" {
            "SELECT version, name FROM schema_version ORDER BY version".to_string()
        } else {
            format!("SELECT * FROM \"{name}\"")
        };
        let rows = fetch_all(db, &query, &[])?;
        tables.insert(name, rows_value(&rows));
    }
    Ok(Value::Object(tables))
}

/// Run one scripted step, returning what a caller can see of it.
fn run_step(db: &mut dyn Db, step: &Value) -> Result<Value, DbError> {
    let op = step["op"].as_str().unwrap_or_else(|| panic!("a step op"));
    let sql = step["sql"].as_str().unwrap_or_default();
    match op {
        "execute" => Ok(json!({ "rowcount": execute(db, sql, &params(step))? })),
        "executemany" => {
            let rows: Vec<Vec<DbValue>> = step["rows"]
                .as_array()
                .expect("scripted rows")
                .iter()
                .map(|row| {
                    row.as_array()
                        .expect("a scripted row")
                        .iter()
                        .map(param)
                        .collect()
                })
                .collect();
            executemany(db, sql, &rows)?;
            Ok(Value::Null)
        }
        "fetch_one" => Ok(match fetch_one(db, sql, &params(step))? {
            Some(row) => row_value(&row),
            None => Value::Null,
        }),
        "fetch_all" => Ok(rows_value(&fetch_all(db, sql, &params(step))?)),
        "fetch_scalar" => Ok(match fetch_scalar(db, sql, &params(step))? {
            Some(value) => case_value(&value),
            None => Value::Null,
        }),
        "table_exists" => Ok(json!(table_exists(
            db,
            step["name"].as_str().expect("a table name")
        )?)),
        "create_tables" => {
            create_tables(db, sql)?;
            Ok(Value::Null)
        }
        "owns_commit" => Ok(json!(bmlib::db::owns_commit(db))),
        "transaction" => run_transaction(db, step),
        other => panic!("no step op {other:?}"),
    }
}

/// Run a scripted block, nested as the script says, and report its trace.
///
/// `fail` makes the block raise **after** its steps have run, which is the shape a
/// rollback is decided by: on the outermost block it rolls the whole thing back,
/// and inside another one it rolls back to the savepoint and leaves the outer
/// block's writes pending.
fn run_transaction(db: &mut dyn Db, step: &Value) -> Result<Value, DbError> {
    let inner = step
        .get("steps")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let fail = step.get("fail").and_then(Value::as_bool).unwrap_or(false);
    let mut trace: Vec<Value> = Vec::new();
    let outcome = transaction(db, |tx| -> Result<(), DbError> {
        for inner_step in &inner {
            trace.push(run_step(tx, inner_step)?);
        }
        if fail {
            return Err(DbError::abort(SCRIPTED_FAILURE));
        }
        Ok(())
    });
    if outcome.is_err() {
        trace.push(json!({ "raised": SCRIPTED_FAILURE }));
    }
    Ok(Value::Array(trace))
}

fn run_case(case: &Value) -> Value {
    let args = &case["args"];
    match case["fn"].as_str().expect("a case fn") {
        "split" => {
            let statements = split_sql_statements(args["script"].as_str().expect("a script"));
            json!(statements)
        }
        "dialect" => {
            let counts: Vec<usize> = args["counts"]
                .as_array()
                .expect("counts")
                .iter()
                .map(|n| n.as_u64().expect("a count") as usize)
                .collect();
            json!({
                "is_sqlite": true,
                "placeholder": bmlib::db::placeholder(),
                "placeholders": counts.iter().map(|n| placeholders(*n)).collect::<Vec<_>>(),
            })
        }
        "script" => {
            let mut conn = open_memory().expect("in-memory sqlite");
            let steps = args["steps"].as_array().expect("steps");
            let mut trace = Vec::new();
            for step in steps {
                trace.push(run_step(&mut conn, step).expect("a step"));
            }
            json!({ "trace": trace, "tables": state(&mut conn).expect("state") })
        }
        "migrations" => {
            let runs = args.get("runs").and_then(Value::as_u64).unwrap_or(1);
            let mut conn = open_memory().expect("in-memory sqlite");
            let mut run_counts = Vec::new();
            for _ in 0..runs {
                run_counts.push(
                    run_migrations(&mut conn, scripted_migrations(args)).expect("migrations"),
                );
            }
            let mut versions: Vec<i64> = get_applied_versions(&mut conn)
                .expect("versions")
                .into_iter()
                .collect();
            versions.sort_unstable();
            json!({
                "runs": run_counts,
                "versions": versions,
                "tables": state(&mut conn).expect("state"),
            })
        }
        other => panic!("no case fn {other:?}"),
    }
}

/// The case's migrations, as closures running their scripts.
///
/// Rebuilt per run because a `Migration` owns its closure; Python's dumper builds
/// its own the same way, over the same `_split_sql_statements`.
fn scripted_migrations(args: &Value) -> Vec<Migration> {
    args["migrations"]
        .as_array()
        .expect("migrations")
        .iter()
        .map(|migration| {
            let sql = migration["sql"]
                .as_str()
                .expect("migration sql")
                .to_string();
            Migration::new(
                migration["version"].as_i64().expect("a version"),
                migration["name"].as_str().expect("a name"),
                move |db| {
                    for statement in split_sql_statements(&sql) {
                        execute(db, &statement, &[])?;
                    }
                    Ok(())
                },
            )
        })
        .collect()
}

/// **The corrected cases are the ones the register names.**
///
/// A corpus edit cannot quietly attach a correction to another input or drop one:
/// the case carrying a `corrected` block is compared against a table, and its
/// reason is asserted non-empty. Each one is also asserted against Python inside
/// `the_port_agrees_with_python_on_every_case`.
#[test]
fn the_corrected_cases_are_the_ones_the_register_names() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let named: Vec<(&str, &str)> = cases
        .as_array()
        .expect("cases is a list")
        .iter()
        .filter(|case| case.get("corrected").is_some())
        .map(|case| {
            (
                case["name"].as_str().unwrap_or_default(),
                case["corrected"]["why"].as_str().unwrap_or_default(),
            )
        })
        .collect();

    // Case, and the §9 row that owns its divergence.
    let owned: [&str; 1] = ["script/a-ddl-rowcount-is-not-negatives"];
    let found: Vec<&str> = named.iter().map(|(name, _)| *name).collect();
    assert_eq!(found, owned.to_vec(), "the corrected cases");

    for (name, why) in &named {
        assert!(
            !why.is_empty(),
            "{name}: a correction without a reason is a tolerance"
        );
    }
}

/// Every case agrees with Python's committed answer.
#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parses");
    let expected: BTreeMap<String, Value> = expected
        .as_array()
        .expect("expected is a list")
        .iter()
        .map(|want| {
            (
                want["name"].as_str().expect("a name").to_string(),
                want.clone(),
            )
        })
        .collect();

    let mut checked = 0;
    let mut mismatches: Vec<String> = Vec::new();
    for case in cases.as_array().expect("cases is a list") {
        let name = case["name"].as_str().expect("a name");
        let want = expected
            .get(name)
            .unwrap_or_else(|| panic!("{name}: no expectation"));
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: the dumper failed, so this case pins nothing: {}",
            want["error"]
        );
        let got = run_case(case);
        // A case whose value the port **corrects** carries what the port should
        // answer instead, and is judged against that — with Python's own answer
        // asserted to differ, so a correction cannot quietly become a tautology.
        let target = match case.get("corrected") {
            Some(corrected) => {
                assert!(
                    want["value"] != corrected["value"],
                    "{name}: the correction is Python's own answer, so it pins nothing"
                );
                &corrected["value"]
            }
            None => &want["value"],
        };
        if got != *target {
            // **Every** divergence, not the first: a corpus run is a survey, and
            // stopping at the first disagreement hides how many there are and
            // whether they are one mistake or several.
            mismatches.push(format!(
                "{name}\n  port:     {got}\n  expected: {target}\n  python:   {}",
                want["value"]
            ));
        }
        checked += 1;
    }
    assert_eq!(
        checked,
        expected.len(),
        "every expectation is reached by a case"
    );
    assert!(
        mismatches.is_empty(),
        "{} of {checked} cases disagree with Python:\n{}",
        mismatches.len(),
        mismatches.join("\n")
    );
}
