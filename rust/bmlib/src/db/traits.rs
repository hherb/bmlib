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

//! The `Db` trait — the Rust stand-in for Python's `conn: Any` first argument.
//!
//! Every helper in `bmlib.db` takes a DB-API connection as its first argument
//! and works whether or not a `transaction()` block is already open. That
//! convention is what `publications/` is written against, so preserving it is
//! the whole point.
//!
//! It survives as `&mut dyn Db`. A connection, a transaction and a savepoint
//! all implement `Db`, so a helper written against `&mut dyn Db` is callable
//! in any of the three positions — exactly as the Python helper is.
//!
//! # Why dynamic dispatch and not a generic `impl Db`
//!
//! The alternative is a generic trait with an associated type for the nested
//! block (`type Tx<'a>: Db`). Two things decide against it, and a third
//! commonly-cited reason turned out **not** to apply — recorded because it is
//! the sort of thing that gets asserted rather than measured.
//!
//! 1. **It infects every signature.** `fn store(db: &mut dyn Db)` is one
//!    function whatever it is called with. `fn store<D: Db>(db: &mut D)` is
//!    generic, and so is every helper it calls, all the way down — turning the
//!    whole of `publications/` generic to serve `db/`.
//! 2. **A GAT trait is not dyn-compatible**, so a connection could never be
//!    stored as a `Box<dyn Db>`, which a runtime backend choice, a registry,
//!    or a GUI app-state handle all need.
//! 3. **Monomorphisation recursion is *not* the reason.** The obvious worry is
//!    that a cyclic call graph (`store` → `consolidate` → `store`, which
//!    `publications/` has) instantiates infinitely. Measured: it compiles
//!    cleanly, because `rusqlite`'s savepoint type is its own parent type —
//!    `Savepoint::savepoint()` returns `Savepoint` — so the instantiation
//!    reaches a fixed point. It fails (`E0275`) only for a backend whose
//!    savepoint type *wraps* its parent. That is a property of the backend,
//!    not of the design.
//!
//! One `Box` per transaction is nothing beside a round trip.

use crate::db::backend::Dialect;
use crate::db::error::Result;
use crate::db::value::{Row, Value};

/// A connection, transaction or savepoint that statements can run on.
///
/// The trait is deliberately dyn-compatible: [`Db::begin`] returns a boxed
/// trait object rather than an associated type, and the consuming methods take
/// `self: Box<Self>`.
pub trait Db {
    /// Which SQL dialect this connection speaks.
    fn dialect(&self) -> Dialect;

    /// Run a statement, returning the number of rows affected.
    ///
    /// Callers write `?` regardless of backend; the implementation renumbers
    /// for PostgreSQL.
    ///
    /// # Errors
    ///
    /// Whatever the driver reports.
    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64>;

    /// Run a query, returning every row.
    ///
    /// # Errors
    ///
    /// Whatever the driver reports.
    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>>;

    /// The rowid of the last insert, where the backend offers one.
    ///
    /// `None` on PostgreSQL — the one irreducibly dialect-specific need the
    /// Python port also calls out, where it uses `RETURNING id` instead.
    fn last_insert_rowid(&self) -> Option<i64>;

    /// Open a nested block: a real transaction at the top, a savepoint inside.
    ///
    /// Which one it is, is decided by the implementation — a `Connection`
    /// begins, a `Transaction` or `Savepoint` opens a savepoint. **No side
    /// table and no driver status is consulted.** That is the whole of what
    /// `transactions._depths`, `_depth_key`, `_is_nested` and the
    /// `(thread, id(conn))` keying existed to compute in Python.
    ///
    /// # Errors
    ///
    /// If the backend cannot open the block.
    fn begin(&mut self) -> Result<Box<dyn Db + '_>>;

    /// Commit this block. A no-op on a connection, which is autocommit.
    ///
    /// # Errors
    ///
    /// If the commit fails. Reported rather than swallowed — the reason this
    /// is a closure API and not an RAII guard, since `Drop` cannot return a
    /// `Result`.
    fn commit(self: Box<Self>) -> Result<()>;

    /// Roll this block back.
    ///
    /// # Errors
    ///
    /// If the rollback fails.
    fn rollback(self: Box<Self>) -> Result<()>;

    /// True if a write right now would need its own commit.
    ///
    /// Constant per implementation: true for a connection, false for a
    /// transaction or savepoint. In Python this had to be *computed*, because
    /// the same `conn` object is passed down into helpers whether or not a
    /// block is open, so only a side count could tell the two apart. Here the
    /// borrow checker makes the ambiguous call impossible — see
    /// [`crate::db::transaction`]'s `compile_fail` doc-test.
    fn owns_commit(&self) -> bool;
}

/// A boxed block is itself a block.
///
/// Needed because a *test harness* that wraps a [`Db`] cannot implement [`Db`]
/// for `Box<dyn Db>` itself — the orphan rule forbids it in another crate, and
/// `PgSim`-style wrappers in `tests/` are exactly that case. Living here, it
/// costs nothing and lets a harness delegate to a boxed inner connection.
impl Db for Box<dyn Db + '_> {
    fn dialect(&self) -> Dialect {
        (**self).dialect()
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        (**self).execute_raw(sql, params)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        (**self).query_raw(sql, params)
    }

    fn last_insert_rowid(&self) -> Option<i64> {
        (**self).last_insert_rowid()
    }

    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        (**self).begin()
    }

    fn commit(self: Box<Self>) -> Result<()> {
        (*self).commit()
    }

    fn rollback(self: Box<Self>) -> Result<()> {
        (*self).rollback()
    }

    fn owns_commit(&self) -> bool {
        (**self).owns_commit()
    }
}
