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

//! **Spike:** a slice of `bmlib/publications/storage.py`, ported to exercise
//! two things `spikes/db-rs` could not: whether `Value`/`Row` holds up at
//! realistic scale, and whether the `DECISIONS.md` invariants survive a port.
//!
//! Read `FINDINGS.md`. This is exploratory code covering one slice of one
//! module; it is not a port of `publications/`.

#![warn(missing_docs)]

pub mod error;
pub mod identifiers;
pub mod models;
pub mod schema;
pub mod storage;

pub use error::{PublicationError, Result};
pub use models::{AuthorAffiliation, Grant, Publication};
pub use storage::{
    get_author_affiliations, get_grants, get_publication_by_doi, get_publication_by_pmid,
    store_publication, Stored,
};
