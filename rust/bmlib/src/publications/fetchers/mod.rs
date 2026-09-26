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

//! Source fetchers: one per publication source, plus the shared rules.
//!
//! | Python | Rust | Status |
//! |---|---|---|
//! | `_reconcile.py` | [`reconcile`] | ported |
//! | `registry.py` | [`registry`] | ported |
//! | `biorxiv.py` | [`biorxiv`] | ported |
//! | `openalex.py` | [`openalex`] | ported |
//! | `pubmed.py` | [`pubmed`] | in progress (XML reader) |

pub mod biorxiv;
pub mod openalex;
pub mod pubmed;
pub mod reconcile;
pub mod registry;

pub use biorxiv::{
    fetch_biorxiv, normalize as normalize_biorxiv, page_url, pdf_url, read_page_body,
    walk as walk_biorxiv, BiorxivFetcher, PageSource, BASE_URL as BIORXIV_BASE_URL,
    PAGE_SIZE as BIORXIV_PAGE_SIZE,
};
pub use openalex::{
    reconstruct_abstract, walk as walk_openalex, CursorPages, OpenAlexFetcher,
    API_URL as OPENALEX_API_URL, PER_PAGE as OPENALEX_PER_PAGE,
};
pub use pubmed::{
    may_checkpoint, part_step, plan_partitions, walk_session, EFetchPage, PartCredit, PartRefusal,
    PartStep, Partition, PlanError, WalkOutcome,
};
pub use reconcile::{reconcile_delivery, Reconciliation, SHORTFALL_FAILURE_RATIO};
pub use registry::{
    builtin_descriptors, FetchError, FetchOutcome, FetchRequest, Fetcher, HttpClient, HttpResponse,
    PartDisposition, Progress, Registry, ResumeState, UnknownSource,
};
