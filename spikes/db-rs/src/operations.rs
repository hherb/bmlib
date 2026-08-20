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

//! Pure-function query helpers.
//!
//! Same shape as the Python module: every function takes the connection as its
//! first argument and is transparent about the SQL. The one difference is that
//! callers always write `?`; the PostgreSQL numbered form is produced here,
//! not at the call site.

use crate::backend::{adapt_sql, Dialect};
use crate::db::Db;
use crate::error::Result;
use crate::split::split_sql_statements;
use crate::value::{Row, Value};

/// Run a statement, returning the number of rows affected.
pub fn execute(db: &mut dyn Db, sql: &str, params: &[Value]) -> Result<u64> {
    let sql = adapt_sql(sql, db.dialect());
    db.execute_raw(&sql, params)
}

/// Run a statement once per parameter set.
pub fn executemany(db: &mut dyn Db, sql: &str, rows: &[Vec<Value>]) -> Result<()> {
    let sql = adapt_sql(sql, db.dialect());
    for params in rows {
        db.execute_raw(&sql, params)?;
    }
    Ok(())
}

/// Run a query and return every row.
pub fn fetch_all(db: &mut dyn Db, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
    let sql = adapt_sql(sql, db.dialect());
    db.query_raw(&sql, params)
}

/// Run a query and return the first row, if any.
pub fn fetch_one(db: &mut dyn Db, sql: &str, params: &[Value]) -> Result<Option<Row>> {
    Ok(fetch_all(db, sql, params)?.into_iter().next())
}

/// Run a query and return the first column of the first row, if any.
///
/// Python needs a backend branch here, because `sqlite3.Row` indexes by
/// position while psycopg2's `RealDictRow` is a dict that raises `KeyError` on
/// `row[0]`, so "first column" means two different lookups. [`Row`] keeps
/// values in column order on both backends, so the branch is gone.
pub fn fetch_scalar(db: &mut dyn Db, sql: &str, params: &[Value]) -> Result<Option<Value>> {
    Ok(fetch_one(db, sql, params)?.and_then(|r| r.at(0).cloned()))
}

/// Whether a table exists, on either backend.
pub fn table_exists(db: &mut dyn Db, name: &str) -> Result<bool> {
    let sql = match db.dialect() {
        Dialect::Sqlite => "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        Dialect::Postgres => "SELECT 1 FROM information_schema.tables WHERE table_name=?",
    };
    Ok(fetch_one(db, sql, &[Value::Text(name.to_string())])?.is_some())
}

/// Execute a (possibly multi-statement) schema DDL string.
///
/// Statements are run one at a time. `rusqlite`'s `execute_batch` is avoided
/// for the same reason the Python port avoids `executescript()` — a batch
/// primitive that commits behind your back would break the atomicity of a
/// surrounding [`crate::transaction`]. Both backends support transactional
/// DDL, so a failure inside a block rolls the DDL back with everything else.
///
/// Unlike the Python version this needs no commit of its own: a connection is
/// in autocommit mode, and inside a block the opener owns the commit. That is
/// what retires the `owns_commit()` call the Python version had to make.
///
/// **Divergence from the Python port, deliberate:** Python splits for SQLite
/// only and hands psycopg2 the whole script, because psycopg2 accepts one. The
/// split is correct on both backends, so doing it unconditionally removes a
/// dialect branch — at the cost of one round trip per statement, which for
/// `publications/schema.py` is 25. Two things to check before adopting this in
/// earnest: measure that cost against a real server, and note that
/// [`crate::split::split_sql_statements`] does not know PostgreSQL's
/// dollar-quoted bodies (`$$ ... $$`), which bmlib's schema does not use but a
/// `CREATE FUNCTION` would.
pub fn create_tables(db: &mut dyn Db, schema_sql: &str) -> Result<()> {
    for stmt in split_sql_statements(schema_sql) {
        db.execute_raw(&stmt, &[])?;
    }
    Ok(())
}
