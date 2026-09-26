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

//! PostgreSQL, over the blocking `postgres` crate.
//!
//! The `Dialect::Postgres` half of the library — the numbered placeholder
//! rewriter, the `information_schema` catalog queries, the PostgreSQL DDL and
//! `RETURNING id` in `insert_publication` — was written long before this file
//! existed and is pinned by tests that run through a simulated connection. What
//! was missing is the thing that can actually open a socket, which is what this
//! module adds. It is behind the `postgres` feature, mirroring the Python
//! library's optional `psycopg2` extra: a caller who only ever opens SQLite
//! should not link a Postgres client and its async runtime.
//!
//! # Why the blocking wrapper, and not `tokio-postgres` directly
//!
//! [`Db`] is a synchronous trait and the whole library above it is
//! synchronous, so the choice is between an async runtime threaded through
//! every signature and a crate that drives the connection task on its own
//! thread. `postgres` is the latter: `Client::execute` blocks, which is exactly
//! the shape `rusqlite` already has. It is the same call `ureq` made over an
//! async HTTP client, and for the same reason.
//!
//! # Two implementations, not three
//!
//! `rusqlite` needs one [`Db`] impl per nesting level — connection, transaction
//! and savepoint are three distinct types — and `db/sqlite.rs` has all three.
//! The blocking Postgres crate has **two**: its `Transaction` covers both a real
//! transaction and a savepoint, because `Transaction::transaction()` opens a
//! nested one via `SAVEPOINT` and `commit`/`rollback` are `RELEASE`/`ROLLBACK
//! TO`. So a `Transaction` opening a block is the savepoint case, and the
//! connection is the only place a real `BEGIN` happens — which is the same
//! "outermost block owns the commit" rule the Python original kept a side table
//! to compute.
//!
//! # The two type mappings a caller can feel
//!
//! **Booleans.** SQLite has no boolean type, so [`Value`] carries `0`/`1` and
//! both the schema and the port's own code use that everywhere. PostgreSQL's
//! `is_open_access` is a real `BOOLEAN`, so [`Value::Int`] against a `BOOL`
//! parameter is encoded as `false`/`true` rather than failing. The reverse
//! mapping is deliberate too: a `BOOL` column reads back as [`Value::Int`], not
//! as some new boolean variant, so `get_i64("is_open_access")` — which is what
//! the SQLite path already does — keeps working unchanged.
//!
//! **Integers.** A PostgreSQL column's type decides the width: `INTEGER` is
//! `int4`, and an `i64` will not encode into it. `id` is `SERIAL` (`int4`), so
//! every row id in this schema is an `int4`, while the neutral [`Value::Int`] is
//! an `i64`. The encoding narrows to the column's own type and reports a value
//! that does not fit rather than truncating it.
//!
//! # What is not supported, and why that is loud
//!
//! The bmlib schema uses only `TEXT`, `INTEGER`, `SERIAL` and `BOOLEAN`, which
//! is the whole of the mapping below. Anything else — `NUMERIC`, `UUID`, an
//! array, a composite — is refused with a message naming the column and the
//! PostgreSQL type, rather than guessed at. A silent fallback (say, rendering
//! every unknown type as text) would make a wrong value look like a right one,
//! which is the failure mode this repository's differential corpora exist to
//! prevent.

use std::sync::Arc;

use bytes::BytesMut;
use postgres::types::{IsNull, ToSql, Type, WrongType};
use postgres::{Client, Column, NoTls, Transaction};

use crate::db::backend::Dialect;
use crate::db::error::{DbError, Result};
use crate::db::traits::Db;
use crate::db::value::{Row, Value};

/// Open a PostgreSQL connection from a libpq-style connection string.
///
/// The Rust counterpart of `connect_postgresql(dsn=...)`. TLS is not
/// configured: `bmlib` talks to the database a caller points it at, and the
/// Python original's `psycopg2.connect` does not turn TLS on unless the DSN
/// asks for it either. A caller who needs TLS should build a
/// [`postgres::Config`] and connect it themselves; every [`Db`] method below is
/// implemented on `postgres::Client`, so such a client works with the rest of
/// the library unchanged.
///
/// # Errors
///
/// If the connection cannot be established or the credentials are refused.
pub fn connect(dsn: &str) -> Result<Client> {
    Client::connect(dsn, NoTls).map_err(DbError::from)
}

/// Open a connection from individual parameters.
///
/// The counterpart of `connect_postgresql(host=…, port=…, database=…, user=…,
/// password=…)`, with the same defaults. An **empty** password is left unset,
/// so a server using trust or peer authentication is not sent an empty
/// password that overrides a `.pgpass` file or `PGPASSWORD`.
///
/// # Errors
///
/// If the connection cannot be established or the credentials are refused.
pub fn connect_params(
    host: &str,
    port: u16,
    database: &str,
    user: &str,
    password: &str,
) -> Result<Client> {
    let mut config = postgres::Config::new();
    config.host(host).port(port).dbname(database).user(user);
    if !password.is_empty() {
        config.password(password);
    }
    config.connect(NoTls).map_err(DbError::from)
}

/// The error for a value with no encoding into the column's PostgreSQL type.
fn unsupported(kind: &str, ty: &Type) -> Box<dyn std::error::Error + Sync + Send> {
    format!(
        "bmlib has no PostgreSQL encoding for a {kind} value as {}",
        ty.name()
    )
    .into()
}

/// The error for an integer too wide for the column's PostgreSQL type.
fn out_of_range(value: i64, ty: &Type) -> Box<dyn std::error::Error + Sync + Send> {
    format!(
        "integer {value} does not fit PostgreSQL's {} column type",
        ty.name()
    )
    .into()
}

/// Borrow every parameter as the driver's object-safe parameter type.
fn to_params(params: &[Value]) -> Vec<&(dyn ToSql + Sync)> {
    params.iter().map(|v| v as &(dyn ToSql + Sync)).collect()
}

/// Turn one driver row into the backend-neutral [`Row`].
fn row_to_row(row: &postgres::Row) -> Result<Row> {
    let mut names = Vec::with_capacity(row.columns().len());
    let mut values = Vec::with_capacity(row.columns().len());
    for (i, column) in row.columns().iter().enumerate() {
        names.push(column.name().to_string());
        values.push(pg_value(row, i, column)?);
    }
    Ok(Row::new(Arc::new(names), values))
}

/// Read one cell, deciding by the column's own PostgreSQL type.
///
/// Every read goes through `try_get`, not `get`: `get` panics when a value
/// cannot be converted, and a public library method may not turn a server's
/// unexpected answer into a panic a caller cannot catch.
fn pg_value(row: &postgres::Row, index: usize, column: &Column) -> Result<Value> {
    let ty = column.type_();
    let value = match *ty {
        Type::BOOL => opt(row.try_get::<usize, Option<bool>>(index)?, |b| {
            Value::Int(i64::from(b))
        }),
        Type::INT2 => opt(row.try_get::<usize, Option<i16>>(index)?, |v| {
            Value::Int(i64::from(v))
        }),
        Type::INT4 => opt(row.try_get::<usize, Option<i32>>(index)?, |v| {
            Value::Int(i64::from(v))
        }),
        Type::INT8 => opt(row.try_get::<usize, Option<i64>>(index)?, Value::Int),
        Type::OID => opt(row.try_get::<usize, Option<u32>>(index)?, |v| {
            Value::Int(i64::from(v))
        }),
        Type::FLOAT4 => opt(row.try_get::<usize, Option<f32>>(index)?, |v| {
            Value::Real(f64::from(v))
        }),
        Type::FLOAT8 => opt(row.try_get::<usize, Option<f64>>(index)?, Value::Real),
        Type::BYTEA => opt(row.try_get::<usize, Option<Vec<u8>>>(index)?, Value::Blob),
        Type::TEXT | Type::VARCHAR | Type::BPCHAR | Type::NAME | Type::UNKNOWN => {
            opt(row.try_get::<usize, Option<String>>(index)?, Value::Text)
        }
        _ => {
            return Err(DbError::Backend(format!(
                "unsupported PostgreSQL column type {} for column {:?}",
                ty.name(),
                column.name()
            )))
        }
    };
    Ok(value)
}

/// `Some(v)` through `f`, `None` to SQL NULL.
fn opt<T>(value: Option<T>, f: impl FnOnce(T) -> Value) -> Value {
    value.map_or(Value::Null, f)
}

/// Encode a neutral [`Value`] into the PostgreSQL binary protocol.
///
/// The expected type is the *column's*, inferred by the server from the
/// statement, so the value adapts to it rather than the other way round — which
/// is what lets one neutral type serve both `INTEGER` and `BOOLEAN` columns.
impl ToSql for Value {
    fn to_sql(
        &self,
        ty: &Type,
        out: &mut BytesMut,
    ) -> std::result::Result<IsNull, Box<dyn std::error::Error + Sync + Send>> {
        match self {
            Value::Null => Ok(IsNull::Yes),
            Value::Text(s) => match *ty {
                Type::TEXT
                | Type::VARCHAR
                | Type::BPCHAR
                | Type::NAME
                | Type::UNKNOWN
                | Type::JSON
                | Type::JSONB => s.to_sql(ty, out),
                _ => Err(unsupported("text", ty)),
            },
            Value::Int(i) => match *ty {
                // SQLite stores booleans as 0/1 and so does every caller above
                // `db/`; PostgreSQL stores a real BOOLEAN.
                Type::BOOL => (*i != 0).to_sql(ty, out),
                Type::INT2 => i16::try_from(*i)
                    .map_err(|_| out_of_range(*i, ty))?
                    .to_sql(ty, out),
                Type::INT4 => i32::try_from(*i)
                    .map_err(|_| out_of_range(*i, ty))?
                    .to_sql(ty, out),
                Type::INT8 => i.to_sql(ty, out),
                Type::OID => u32::try_from(*i)
                    .map_err(|_| out_of_range(*i, ty))?
                    .to_sql(ty, out),
                Type::FLOAT4 => (*i as f32).to_sql(ty, out),
                Type::FLOAT8 => (*i as f64).to_sql(ty, out),
                _ => Err(unsupported("integer", ty)),
            },
            Value::Real(r) => match *ty {
                Type::FLOAT4 => (*r as f32).to_sql(ty, out),
                Type::FLOAT8 => r.to_sql(ty, out),
                _ => Err(unsupported("real", ty)),
            },
            Value::Blob(b) => match *ty {
                Type::BYTEA => b.to_sql(ty, out),
                _ => Err(unsupported("blob", ty)),
            },
        }
    }

    fn accepts(ty: &Type) -> bool {
        matches!(
            *ty,
            Type::BOOL
                | Type::INT2
                | Type::INT4
                | Type::INT8
                | Type::OID
                | Type::FLOAT4
                | Type::FLOAT8
                | Type::TEXT
                | Type::VARCHAR
                | Type::BPCHAR
                | Type::NAME
                | Type::UNKNOWN
                | Type::BYTEA
                | Type::JSON
                | Type::JSONB
        )
    }

    /// Spelled out rather than expanded from `postgres_types::to_sql_checked!`,
    /// which is the same three lines but reaches for a crate this one depends on
    /// only transitively.
    fn to_sql_checked(
        &self,
        ty: &Type,
        out: &mut BytesMut,
    ) -> std::result::Result<IsNull, Box<dyn std::error::Error + Sync + Send>> {
        if !<Value as ToSql>::accepts(ty) {
            return Err(Box::new(WrongType::new::<Value>(ty.clone())));
        }
        self.to_sql(ty, out)
    }
}

impl Db for Client {
    fn dialect(&self) -> Dialect {
        Dialect::Postgres
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        let refs = to_params(params);
        Ok(Client::execute(self, sql, &refs)?)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        let refs = to_params(params);
        let rows = Client::query(self, sql, &refs)?;
        rows.iter().map(row_to_row).collect()
    }

    /// Always `None`: PostgreSQL has no `lastrowid`, which is why
    /// `publications::storage::insert_publication` asks with `RETURNING id` on
    /// this dialect. The branch is not dead — it is the whole reason that
    /// function has two arms.
    fn last_insert_rowid(&self) -> Option<i64> {
        None
    }

    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        Ok(Box::new(Client::transaction(self)?))
    }

    /// A connection outside an explicit block autocommits each statement, so
    /// there is nothing pending to commit — the same rule `db/sqlite.rs` states
    /// for `rusqlite::Connection`.
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
        Dialect::Postgres
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        let refs = to_params(params);
        Ok(Transaction::execute(self, sql, &refs)?)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        let refs = to_params(params);
        let rows = Transaction::query(self, sql, &refs)?;
        rows.iter().map(row_to_row).collect()
    }

    fn last_insert_rowid(&self) -> Option<i64> {
        None
    }

    /// A nested block is a `SAVEPOINT`, opened by the driver's own
    /// `Transaction::transaction()`.
    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        Ok(Box::new(Transaction::transaction(self)?))
    }

    /// `RELEASE SAVEPOINT` for a nested block, the real `COMMIT` for the
    /// outermost one — the driver decides by what it is.
    fn commit(self: Box<Self>) -> Result<()> {
        Transaction::commit(*self)?;
        Ok(())
    }

    /// `ROLLBACK TO SAVEPOINT` for a nested block, a full rollback for the
    /// outermost one.
    fn rollback(self: Box<Self>) -> Result<()> {
        Transaction::rollback(*self)?;
        Ok(())
    }

    fn owns_commit(&self) -> bool {
        false
    }
}
