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

//! SQLite backend, over `rusqlite`.
//!
//! Three implementations of [`Db`]: the connection, a transaction, and a
//! savepoint. `rusqlite`'s own types already model the distinction Python had
//! to track in a side table, so each one only has to say which it is.

use std::path::Path;
use std::sync::Arc;

use rusqlite::types::{ToSqlOutput, ValueRef};
use rusqlite::{Connection, Savepoint, Transaction};

use crate::backend::Dialect;
use crate::db::Db;
use crate::error::Result;
use crate::value::{Row, Value};

impl rusqlite::types::ToSql for Value {
    fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
        Ok(match self {
            Value::Null => ToSqlOutput::Borrowed(ValueRef::Null),
            Value::Int(i) => ToSqlOutput::Borrowed(ValueRef::Integer(*i)),
            Value::Real(r) => ToSqlOutput::Borrowed(ValueRef::Real(*r)),
            Value::Text(s) => ToSqlOutput::Borrowed(ValueRef::Text(s.as_bytes())),
            Value::Blob(b) => ToSqlOutput::Borrowed(ValueRef::Blob(b)),
        })
    }
}

fn from_ref(v: ValueRef<'_>) -> Value {
    match v {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(i) => Value::Int(i),
        ValueRef::Real(r) => Value::Real(r),
        ValueRef::Text(t) => Value::Text(String::from_utf8_lossy(t).into_owned()),
        ValueRef::Blob(b) => Value::Blob(b.to_vec()),
    }
}

// `rusqlite`'s statement methods take `&self` (interior mutability), and both
// `Transaction` and `Savepoint` deref to `Connection`, so all three
// implementations share these two functions rather than repeating themselves.

pub(crate) fn conn_execute(c: &Connection, sql: &str, params: &[Value]) -> Result<u64> {
    Ok(c.execute(sql, rusqlite::params_from_iter(params.iter()))? as u64)
}

pub(crate) fn conn_query(c: &Connection, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
    let mut stmt = c.prepare(sql)?;
    let columns: Arc<Vec<String>> =
        Arc::new(stmt.column_names().iter().map(|s| s.to_string()).collect());
    let ncols = columns.len();
    let mut rows = stmt.query(rusqlite::params_from_iter(params.iter()))?;
    let mut out = Vec::new();
    while let Some(r) = rows.next()? {
        let mut values = Vec::with_capacity(ncols);
        for i in 0..ncols {
            values.push(from_ref(r.get_ref(i)?));
        }
        out.push(Row::new(columns.clone(), values));
    }
    Ok(out)
}

/// Open an in-memory SQLite database, foreign keys on.
///
/// The equivalent of `connect_sqlite(":memory:")`.
pub fn open_memory() -> Result<Connection> {
    let conn = Connection::open_in_memory()?;
    conn.execute_batch("PRAGMA foreign_keys=ON;")?;
    Ok(conn)
}

/// Open (or create) a SQLite database file.
///
/// Mirrors `connect_sqlite`: WAL and foreign keys default on, and WAL is
/// skipped for an in-memory database.
pub fn open_path(path: &Path, wal_mode: bool, foreign_keys: bool) -> Result<Connection> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|e| crate::error::DbError::Backend(e.to_string()))?;
        }
    }
    let conn = Connection::open(path)?;
    if wal_mode {
        conn.pragma_update(None, "journal_mode", "WAL")?;
    }
    if foreign_keys {
        conn.execute_batch("PRAGMA foreign_keys=ON;")?;
    }
    Ok(conn)
}

impl Db for Connection {
    fn dialect(&self) -> Dialect {
        Dialect::Sqlite
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        conn_execute(self, sql, params)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        conn_query(self, sql, params)
    }

    fn last_insert_rowid(&self) -> Option<i64> {
        Some(Connection::last_insert_rowid(self))
    }

    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        Ok(Box::new(Connection::transaction(self)?))
    }

    /// A connection is in autocommit mode, so there is nothing to commit.
    ///
    /// This is the first place the port diverges from Python's `sqlite3`,
    /// which auto-begins before DML and leaves a write pending until an
    /// explicit `commit()`. See FINDINGS.md, "Two hazards that cannot occur".
    fn commit(self: Box<Self>) -> Result<()> {
        Ok(())
    }

    fn rollback(self: Box<Self>) -> Result<()> {
        Ok(())
    }

    fn owns_commit(&self) -> bool {
        true
    }
}

impl Db for Transaction<'_> {
    fn dialect(&self) -> Dialect {
        Dialect::Sqlite
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        conn_execute(self, sql, params)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        conn_query(self, sql, params)
    }

    fn last_insert_rowid(&self) -> Option<i64> {
        Some(Connection::last_insert_rowid(self))
    }

    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        Ok(Box::new(Transaction::savepoint(self)?))
    }

    fn commit(self: Box<Self>) -> Result<()> {
        Transaction::commit(*self)?;
        Ok(())
    }

    fn rollback(self: Box<Self>) -> Result<()> {
        Transaction::rollback(*self)?;
        Ok(())
    }

    fn owns_commit(&self) -> bool {
        false
    }
}

impl Db for Savepoint<'_> {
    fn dialect(&self) -> Dialect {
        Dialect::Sqlite
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        conn_execute(self, sql, params)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        conn_query(self, sql, params)
    }

    fn last_insert_rowid(&self) -> Option<i64> {
        Some(Connection::last_insert_rowid(self))
    }

    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        Ok(Box::new(Savepoint::savepoint(self)?))
    }

    /// `RELEASE SAVEPOINT`, and deliberately **not** a commit.
    ///
    /// The Python original's rule — "whoever opened the outermost block owns
    /// the commit" — is what makes a batch loop able to wrap many
    /// `transaction()`-using calls in one outer block and pay a single commit.
    fn commit(self: Box<Self>) -> Result<()> {
        Savepoint::commit(*self)?;
        Ok(())
    }

    /// `ROLLBACK TO SAVEPOINT` then `RELEASE`, so only this block's writes go.
    fn rollback(self: Box<Self>) -> Result<()> {
        let mut sp = *self;
        sp.rollback()?;
        sp.finish()?;
        Ok(())
    }

    fn owns_commit(&self) -> bool {
        false
    }
}
