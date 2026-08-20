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

//! Shared harness: run one test body against both backends.
//!
//! The Rust equivalent of `tests/test_backends.py`'s `backend_conn` fixture,
//! which parametrises every test over SQLite and PostgreSQL.

#![allow(dead_code)]

use bmlib_db::pg_sim::PgSim;
use bmlib_db::{open_memory, Db};

/// Run `body` against a real SQLite connection.
pub fn on_sqlite(body: impl FnOnce(&mut dyn Db)) {
    let mut conn = open_memory().expect("open in-memory sqlite");
    body(&mut conn);
}

/// Run `body` against the PostgreSQL-semantics harness.
pub fn on_pg_semantics(body: impl FnOnce(&mut dyn Db)) {
    let mut conn = PgSim::new(open_memory().expect("open in-memory sqlite"));
    body(&mut conn);
}

/// Generate one test per backend from a single body.
macro_rules! both_backends {
    ($name:ident, $body:expr) => {
        mod $name {
            #[allow(unused_imports)]
            use super::*;

            #[test]
            fn sqlite() {
                crate::common::on_sqlite($body);
            }

            #[test]
            fn postgres_semantics() {
                crate::common::on_pg_semantics($body);
            }
        }
    };
}

pub(crate) use both_backends;
