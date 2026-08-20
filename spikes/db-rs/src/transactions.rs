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

//! Composable transactions via savepoints.
//!
//! The Python contract this reproduces, verbatim from `bmlib/db/transactions.py`:
//! entering a block while another is open runs the inner one inside a
//! `SAVEPOINT`; on success the savepoint is released and **no commit is
//! issued**, because whoever opened the outermost block owns the commit; on
//! failure only the inner block's writes are rolled back.
//!
//! What is *not* reproduced is the machinery that decided "am I nested?" —
//! `_depths`, `_depth_key`, `_depths_lock`, `_is_nested`, `transaction_depth`
//! and the `(thread, id(conn))` keying, about 60 lines of the Python module
//! and the subject of its longest comment. Here the answer is the type of the
//! thing you were handed, so there is nothing to look up and no lock to take.

use crate::db::Db;
use crate::error::Result;

/// Run `f` in a transaction, committing on `Ok` and rolling back on `Err`.
///
/// Nested calls open a savepoint instead, exactly as the Python original does.
///
/// # Why a closure and not a guard
///
/// `Drop` cannot return a `Result` and cannot see whether the block succeeded,
/// so an RAII guard can only auto-*rollback*; auto-commit-on-success needs the
/// success/failure signal a closure's return value carries. This is the one
/// place the Rust API reads differently from `with transaction(conn):`, and it
/// is strictly stronger: a failing commit is reported rather than swallowed.
///
/// # Example
///
/// A helper written against `&mut dyn Db` composes in either position — the
/// property `publications.sync()`'s one-commit-per-day batching depends on:
///
/// ```
/// use bmlib_db::{open_memory, transaction, params, operations::{create_tables, execute}};
///
/// fn store(db: &mut dyn bmlib_db::Db, v: &str) -> bmlib_db::Result<()> {
///     transaction(db, |tx| {
///         execute(tx, "INSERT INTO t (v) VALUES (?)", &params![v])?;
///         Ok(())
///     })
/// }
///
/// let mut conn = open_memory().unwrap();
/// create_tables(&mut conn, "CREATE TABLE t (v TEXT);").unwrap();
///
/// store(&mut conn, "standalone").unwrap();              // a real transaction
/// transaction(&mut conn, |tx| {
///     store(tx, "batched")?;                            // a savepoint
///     store(tx, "batched too")?;                        // another savepoint
///     Ok(())
/// }).unwrap();                                          // one commit for both
/// ```
///
/// # The hazard the borrow checker removes
///
/// Python's depth table exists because the *same* `conn` object is passed into
/// helpers whether or not a block is open, so only a side count can tell an
/// outermost block from a nested one — and getting the key wrong (by
/// connection alone rather than by thread *and* connection) silently stopped
/// committing. Here, reaching around an open block to the connection it
/// borrows does not compile:
///
/// ```compile_fail
/// use bmlib_db::{open_memory, transaction, params, operations::execute};
///
/// let mut conn = open_memory().unwrap();
/// transaction(&mut conn, |_tx| {
///     // error: cannot borrow `conn` as mutable more than once at a time
///     execute(&mut conn, "INSERT INTO t (v) VALUES (?)", &params!["x"])?;
///     Ok(())
/// }).unwrap();
/// ```
pub fn transaction<T, F>(db: &mut dyn Db, f: F) -> Result<T>
where
    F: FnOnce(&mut dyn Db) -> Result<T>,
{
    let mut block = db.begin()?;
    match f(&mut *block) {
        Ok(value) => {
            block.commit()?;
            Ok(value)
        }
        Err(err) => {
            // The caller's error is what matters; a rollback that also fails
            // must not mask it. Python has the same precedence — `raise` in
            // the `except` clause runs after `conn.rollback()`.
            let _ = block.rollback();
            Err(err)
        }
    }
}

/// True if a write on `db` right now would need its own commit.
///
/// Kept because it is public API in the Python original. Its only consumer
/// there — `create_tables` — no longer needs it: a `Connection` is autocommit
/// and a `Transaction` is owned by its opener, so neither has a decision to
/// make. See FINDINGS.md, "What disappears".
pub fn owns_commit(db: &dyn Db) -> bool {
    db.owns_commit()
}
