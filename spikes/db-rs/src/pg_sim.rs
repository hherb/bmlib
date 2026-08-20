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

//! A PostgreSQL *semantics* harness — not a PostgreSQL backend.
//!
//! No PostgreSQL server is available in this environment, and an untested
//! backend claiming to work would be worse than none. What this does provide
//! is the two psycopg2 behaviours the Python design was shaped by, so the same
//! test suite can be run twice the way `tests/test_backends.py` does:
//!
//! 1. **Numbered placeholders.** [`dialect`](Db::dialect) reports
//!    [`Dialect::Postgres`], so every statement arrives here already rewritten
//!    to `$1, $2, ...`. The harness records that form — which is what proves
//!    the rewriter fired — then restores `?` to run it on the wrapped SQLite
//!    connection.
//!
//! 2. **"Any statement opens a transaction."** psycopg2 leaves a connection
//!    INTRANS after a bare `SELECT`, which is precisely why the Python port
//!    could not read the driver's status to decide nesting and had to keep its
//!    own count. [`intrans`](PgSim::intrans) is set by every statement, and the
//!    test suite asserts that `owns_commit()` is unmoved by it.
//!
//! What it does *not* simulate: PostgreSQL's SQL dialect, its types, its
//! locking, or `RETURNING id`. It is a harness for one question.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::backend::Dialect;
use crate::db::Db;
use crate::error::Result;
use crate::value::{Row, Value};

/// Delegating implementation so a boxed block can itself be wrapped.
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

/// Wraps any [`Db`] and presents it with PostgreSQL's driver semantics.
pub struct PgSim<D: Db> {
    inner: D,
    intrans: Rc<Cell<bool>>,
    log: Rc<RefCell<Vec<String>>>,
}

impl<D: Db> PgSim<D> {
    /// Wrap `inner`.
    pub fn new(inner: D) -> Self {
        PgSim {
            inner,
            intrans: Rc::new(Cell::new(false)),
            log: Rc::new(RefCell::new(Vec::new())),
        }
    }

    /// Whether the driver would report this connection as INTRANS.
    ///
    /// Set by any statement, including a bare `SELECT` — the behaviour that
    /// makes driver status useless for deciding nesting.
    pub fn intrans(&self) -> bool {
        self.intrans.get()
    }

    /// Every statement as it arrived, in PostgreSQL's numbered form.
    pub fn log(&self) -> Vec<String> {
        self.log.borrow().clone()
    }
}

/// Restore `?` from `$n` so the wrapped SQLite connection can run it.
///
/// The inverse of [`crate::backend::rewrite_placeholders`], and subject to the
/// same rule: only outside string literals.
fn unnumber(sql: &str) -> String {
    let mut out = String::with_capacity(sql.len());
    let mut chars = sql.chars().peekable();
    let mut quote: Option<char> = None;
    while let Some(ch) = chars.next() {
        if let Some(q) = quote {
            out.push(ch);
            if ch == q {
                if chars.peek() == Some(&q) {
                    out.push(q);
                    chars.next();
                } else {
                    quote = None;
                }
            }
            continue;
        }
        match ch {
            '\'' | '"' => {
                quote = Some(ch);
                out.push(ch);
            }
            '$' if chars.peek().is_some_and(|c| c.is_ascii_digit()) => {
                while chars.peek().is_some_and(|c| c.is_ascii_digit()) {
                    chars.next();
                }
                out.push('?');
            }
            _ => out.push(ch),
        }
    }
    out
}

/// Answer PostgreSQL's catalog query out of SQLite's catalog.
///
/// `table_exists` sends `information_schema.tables` on the PostgreSQL branch,
/// which the wrapped SQLite connection has no such table for. The harness
/// simulates the *answer*, not the catalog. This is the one place it rewrites
/// a caller's SQL rather than only its placeholders, and it is why the harness
/// is documented as a harness: a real backend would need none of it.
fn catalog_shim(sql: &str) -> String {
    if sql.contains("information_schema.tables") {
        return "SELECT 1 FROM sqlite_master WHERE type='table' AND name=$1".to_string();
    }
    sql.to_string()
}

impl<D: Db> Db for PgSim<D> {
    fn dialect(&self) -> Dialect {
        Dialect::Postgres
    }

    fn execute_raw(&mut self, sql: &str, params: &[Value]) -> Result<u64> {
        self.log.borrow_mut().push(sql.to_string());
        self.intrans.set(true);
        self.inner.execute_raw(&unnumber(sql), params)
    }

    fn query_raw(&mut self, sql: &str, params: &[Value]) -> Result<Vec<Row>> {
        self.log.borrow_mut().push(sql.to_string());
        // Even a bare SELECT. This is the whole point of the harness.
        self.intrans.set(true);
        self.inner.query_raw(&unnumber(&catalog_shim(sql)), params)
    }

    fn last_insert_rowid(&self) -> Option<i64> {
        // PostgreSQL has no rowid; the Python port uses `RETURNING id`.
        None
    }

    fn begin(&mut self) -> Result<Box<dyn Db + '_>> {
        let inner = self.inner.begin()?;
        Ok(Box::new(PgSim {
            inner,
            intrans: self.intrans.clone(),
            log: self.log.clone(),
        }))
    }

    fn commit(self: Box<Self>) -> Result<()> {
        self.inner.commit_boxed()
    }

    fn rollback(self: Box<Self>) -> Result<()> {
        self.inner.rollback_boxed()
    }

    fn owns_commit(&self) -> bool {
        self.inner.owns_commit()
    }
}

/// `self: Box<Self>` cannot be called on a `D: Db` held by value, so the
/// wrapper needs a by-value route to the same two methods.
trait BoxedEnds {
    fn commit_boxed(self) -> Result<()>;
    fn rollback_boxed(self) -> Result<()>;
}

impl<D: Db> BoxedEnds for D {
    fn commit_boxed(self) -> Result<()> {
        Box::new(self).commit()
    }
    fn rollback_boxed(self) -> Result<()> {
        Box::new(self).rollback()
    }
}
