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

//! Shared library for biomedical literature tools — the Rust port of
//! [`bmlib`](https://github.com/hherb/bmlib).
//!
//! # What this port is
//!
//! Functionally equivalent to the Python library, **and equivalent to a
//! corrected version of it**: where the Python library is wrong, this
//! implements the intended behaviour rather than reproducing the defect. The
//! sixteen defects found while planning the port are enumerated in the port
//! plan, and each Rust module that fixes one says so where it does.
//!
//! What is deliberately **kept** is `docs/DECISIONS.md` — the Python
//! repository's register of investigated non-fixes. Those are the
//! specification, not a defect list. When a clean-up here would change one of
//! them, it does not happen.
//!
//! # The plan
//!
//! `docs/plans/2026-09-26-rust-port-roadblocks.md` in the Python repository
//! carries the analysis, the dependency policy and the phase order. The short
//! version:
//!
//! | Phase | Contents | Status |
//! |-------|----------|--------|
//! | 1 | [`db`], then `citations`, `context_processor`, `quality` extractors, `llm` text utilities, models | **in progress** |
//! | 2 | `publications` fetchers and `sync` | not started |
//! | 3 | the two LLM protocols, `agents`, the LLM quality tiers | not started |
//! | 4 | `jats_parser`, `transparency`, `fulltext` (PDFium behind a wrapper) | not started |
//!
//! # Dependency policy
//!
//! Link a native library where a solved general problem has one; hand-roll
//! what is bmlib's own decision. So: `rusqlite` and `serde` for the plumbing,
//! hand-written code for the routing, invariants and heuristics that are the
//! library's actual value.

#![warn(missing_docs)]
#![warn(clippy::all)]

pub mod agents;
pub mod atomic;
pub mod citations;
pub mod context_processor;
pub mod db;
pub mod fulltext;
pub mod http;
pub mod llm;
pub mod publications;
pub mod quality;
pub mod templates;
pub mod transparency;

pub use db::{Db, DbError, Row, Value};
