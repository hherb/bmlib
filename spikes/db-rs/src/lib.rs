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

//! **Spike:** a Rust port of `bmlib.db`, written to answer one question —
//! does the composable-savepoint transaction design survive the borrow
//! checker, and what does the API cost?
//!
//! This is exploratory code. It is not published, not wired to anything, and
//! deliberately covers only `bmlib/db/`. Read `FINDINGS.md` for the answer;
//! read `README.md` for how to run it.
//!
//! The Python module it ports is 787 lines across five files. The mapping:
//!
//! | Python                     | Here            |
//! |----------------------------|-----------------|
//! | `db/backend.py`            | [`backend`]     |
//! | `db/connection.py`         | [`sqlite`]      |
//! | `db/operations.py`         | [`operations`], [`split`] |
//! | `db/transactions.py`       | [`transactions`] |
//! | `db/migrations.py`         | [`migrations`]  |
//! | *(no equivalent)*          | [`db`], [`value`], [`error`] |
//! | *(test fixture)*           | [`pg_sim`]      |

#![warn(missing_docs)]

pub mod backend;
pub mod db;
pub mod error;
pub mod migrations;
pub mod operations;
pub mod pg_sim;
pub mod split;
pub mod sqlite;
pub mod transactions;
pub mod value;

pub use backend::{placeholder, placeholders, Dialect};
pub use db::Db;
pub use error::{DbError, Result};
pub use sqlite::{open_memory, open_path};
pub use transactions::{owns_commit, transaction, transaction_with};
pub use value::{Row, Value};
