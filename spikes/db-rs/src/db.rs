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
//! the point of this spike.
//!
//! It survives as `&mut dyn Db`. A connection, a transaction and a savepoint
//! all implement `Db`, so a helper written against `&mut dyn Db` is callable
//! in any of the three positions — exactly as the Python helper is.
//!
//! # Why this is dynamic dispatch and not a generic `impl Db`
//!
//! The alternative is a generic trait with an associated type for the nested
//! block (`type Tx<'a>: Db`). It was tried; two things decided against it, and
//! a third commonly-cited reason turned out **not** to apply — recorded here
//! because it is the sort of thing that gets asserted rather than measured.
//!
//! 1. **It infects every signature.** `fn store(db: &mut dyn Db)` is one
//!    function whatever it is called with. `fn store<D: Db>(db: &mut D)` is
//!    generic, and so is every helper it calls, all the way down — turning the
//!    whole of `publications/` generic to serve `db/`.
//! 2. **A GAT trait is not dyn-compatible**, so a connection could never be
//!    stored as a `Box<dyn Db>` — which a runtime backend choice, a registry,
//!    or a Tauri app-state handle all need.
//! 3. **Monomorphisation recursion is *not* the reason.** The obvious worry is
//!    that a cyclic call graph (`store` → `consolidate` → `store`, which
//!    `publications/` has) instantiates infinitely. Measured: it compiles
//!    cleanly, because `rusqlite`'s savepoint type is its own parent type —
//!    `Savepoint::savepoint()` returns `Savepoint` — so the instantiation
//!    reaches a fixed point. It fails (`E0275`, overflow evaluating the
//!    requirement) only for a backend whose savepoint type *wraps* its parent.
//!    That is a property of the backend, not of the design.
//!
//! One `Box` per transaction is nothing beside a round trip.

use crate::backend::Dialect;
use crate::error::Result;
use crate::value::{Row, Value};

/// A connection, transaction or savepoint that statements can run on.
///
/// The trait is deliberately dyn-compatible: `begin` returns a boxed trait
/// object rather than an associated type, and the consuming methods take
/// `self: Box<Self>`.
pub trait Db {
    /// Which SQL dialect this connection speaks.
    fn dialect(&self) -> Dialect;

    /// Run a statement, returning the number of rows affected.
    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64>;

    /// Run a query, returning every row.
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
    /// `(thread, id(conn))` keying existed to compute.
    fn begin(&mut self) -> Result<Box<dyn Db + '_>>;

    /// Commit this block. A no-op on a connection, which is autocommit.
    fn commit(self: Box<Self>) -> Result<()>;

    /// Roll this block back.
    fn rollback(self: Box<Self>) -> Result<()>;

    /// True if a write right now would need its own commit.
    ///
    /// Constant per implementation: true for a connection, false for a
    /// transaction or savepoint. In Python this had to be computed, because
    /// the same `conn` object is passed down into helpers whether or not a
    /// block is open, so only a side count could tell the two apart. Here the
    /// borrow checker makes the ambiguous call impossible — see
    /// [`crate::transaction`].
    fn owns_commit(&self) -> bool;
}
