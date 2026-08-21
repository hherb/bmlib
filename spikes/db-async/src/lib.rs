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

//! **Spike:** can `bmlib-db`'s `Db` trait be driven from a Tauri-style async
//! runtime, and does it survive connection pooling?
//!
//! `spikes/db-rs` established a *synchronous* design. The Tauri apps in the
//! pipeline are async (tokio), so the open question was whether
//! `transaction(db, |tx| ...)` still works from there — and if not, what the
//! alternative costs.
//!
//! Read `FINDINGS.md`. In short: the sync design is the one to keep, and the
//! bridge is a pool plus `spawn_blocking`, not an async trait.

#![warn(missing_docs)]

pub mod async_db;
pub mod pool;

pub use pool::{open_pool, with_conn, Pool, PooledDb};
