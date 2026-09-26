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

//! Thin database abstraction over SQLite and PostgreSQL.
//!
//! A port of `bmlib/db/` (787 Python lines across five files). The mapping:
//!
//! | Python              | Here                        |
//! |---------------------|-----------------------------|
//! | `db/backend.py`     | [`backend`]                 |
//! | `db/connection.py`  | [`sqlite`]                  |
//! | `db/operations.py`  | [`operations`], [`split`]   |
//! | `db/transactions.py`| [`transactions`]            |
//! | `db/migrations.py`  | [`migrations`]              |
//! | *(no equivalent)*   | [`value`], [`error`], [`traits`] |
//!
//! # What changed in the port, and why
//!
//! **Nesting is the type of the value in hand.** Python kept a side table of
//! open blocks keyed by `(thread, id(conn))`, plus `_is_nested()` and
//! `transaction_depth()`. Here a [`Db::begin`] on a connection opens a real
//! transaction and on a transaction or savepoint opens a savepoint, so the
//! bookkeeping is gone — and with it three classes of silent-write-loss bug,
//! since reaching around an open block no longer compiles.
//! `transaction_depth()` has no counterpart because nothing needs a count;
//! `owns_commit()` survives as a constant per implementation.
//!
//! **One [`Row`] type on both backends.** Python's `fetch_scalar` needed a
//! backend branch — `row[0]` on `sqlite3.Row`, `list(row.values())[0]` on
//! psycopg2's `RealDictRow` — because the two row types disagree about
//! indexing. A row that is ordered *and* named removes the branch.
//!
//! **Dialect detection is the implementation's own answer.** `is_sqlite(conn)`
//! sniffs the driver's module name; here `Db::dialect()` reports it.
//!
//! **Placeholders are renumbered inside the backend.** PostgreSQL wants `$1`
//! where SQLite wants `?`, so Python's `", ".join([placeholder] * n)` idiom
//! cannot carry over. Callers keep writing `?` and [`backend::adapt_sql`]
//! renumbers where needed — [`backend::placeholder`] is kept only so a
//! mechanical port compiles unchanged.
//!
//! # Not yet ported
//!
//! There is no PostgreSQL backend: [`Dialect::Postgres`] exists, the numbered
//! placeholder rewriting is exercised by tests, and `Transaction`-equivalent
//! behaviour is unverified against a real server. `spikes/db-rs/FINDINGS.md`
//! records what that does and does not establish.

pub mod backend;
pub mod error;
pub mod migrations;
pub mod operations;
pub mod split;
pub mod sqlite;
pub mod traits;
pub mod transactions;
pub mod value;

pub use backend::{adapt_sql, placeholder, placeholders, rewrite_placeholders, Dialect};
pub use error::{DbError, Result};
pub use migrations::{get_applied_versions, run_migrations, Migration, MigrationFn};
pub use operations::{
    create_tables, execute, executemany, fetch_all, fetch_one, fetch_scalar, table_exists,
};
pub use sqlite::{open_memory, open_path};
pub use traits::Db;
pub use transactions::{owns_commit, transaction, transaction_with};
pub use value::{Row, Value};
