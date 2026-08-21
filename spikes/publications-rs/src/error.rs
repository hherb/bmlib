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

//! This crate's error type.
//!
//! A module with its own failure modes needs its own error, which is what a
//! real port would do throughout. It is also what turned up the one change
//! this spike made to `bmlib-db` — see FINDINGS.md, "What the slice changed
//! upstream".

use std::fmt;

use bmlib_db::DbError;

/// Result alias for this crate.
pub type Result<T> = std::result::Result<T, PublicationError>;

/// Anything that can go wrong storing or reading a publication.
#[derive(Debug)]
pub enum PublicationError {
    /// The database rejected something.
    Db(DbError),
    /// A stored value could not be read back into a model.
    Malformed(String),
    /// A child row named no source. See [`crate::storage::replace_child_rows`].
    UnnamedSource {
        /// The table the row was headed for.
        table: &'static str,
    },
}

impl fmt::Display for PublicationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PublicationError::Db(e) => write!(f, "{e}"),
            PublicationError::Malformed(m) => write!(f, "malformed stored value: {m}"),
            PublicationError::UnnamedSource { table } => write!(
                f,
                "{table}: every row must name the source that asserted it, got \"\".                  Set Grant.source / AuthorAffiliation.source, or let sync() stamp it."
            ),
        }
    }
}

impl std::error::Error for PublicationError {}

impl From<DbError> for PublicationError {
    fn from(e: DbError) -> Self {
        PublicationError::Db(e)
    }
}

impl From<serde_json::Error> for PublicationError {
    fn from(e: serde_json::Error) -> Self {
        PublicationError::Malformed(e.to_string())
    }
}
