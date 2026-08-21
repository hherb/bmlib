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

//! Error type for the spike.
//!
//! Python signals failure by raising; every `bmlib.db` helper simply lets the
//! driver's exception propagate, and `transaction()` catches `Exception` to
//! decide rollback. Rust needs one concrete error type that can carry both a
//! driver failure and a *caller's* failure, because the caller's `Err` is what
//! tells `transaction()` to roll back — the role `raise RuntimeError("boom")`
//! plays in the Python tests.

use std::fmt;

/// Result alias used throughout the crate.
pub type Result<T> = std::result::Result<T, DbError>;

/// Anything that can go wrong in a database call.
#[derive(Debug)]
pub enum DbError {
    /// The driver rejected the statement or the connection failed.
    Backend(String),
    /// A row did not have the column asked for, or it held the wrong type.
    Column(String),
    /// The caller's own failure, propagated so `transaction()` rolls back.
    ///
    /// This is the stand-in for an arbitrary Python exception crossing a
    /// `with transaction(conn):` block.
    Abort(Box<dyn std::error::Error + Send + Sync>),
}

impl DbError {
    /// Wrap a caller-side failure so it can travel through `transaction()`.
    pub fn abort<E>(err: E) -> Self
    where
        E: Into<Box<dyn std::error::Error + Send + Sync>>,
    {
        DbError::Abort(err.into())
    }
}

impl fmt::Display for DbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DbError::Backend(m) => write!(f, "database error: {m}"),
            DbError::Column(m) => write!(f, "column error: {m}"),
            DbError::Abort(e) => write!(f, "aborted by caller: {e}"),
        }
    }
}

impl std::error::Error for DbError {}

impl From<rusqlite::Error> for DbError {
    fn from(e: rusqlite::Error) -> Self {
        DbError::Backend(e.to_string())
    }
}
