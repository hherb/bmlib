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

//! The three dataclasses this slice needs.
//!
//! `serde` replaces `to_dict()` / `from_dict()` outright — which matters
//! beyond this crate, since those methods are also the sidecar boundary.
//!
//! Two frictions worth naming. `abstract` is a **Rust keyword**, so the field
//! is `r#abstract`; it is one of bmlib's most-used field names and every port
//! will meet it. And Python's `_pub(**kwargs)` test helper — build one with
//! defaults, override two fields — becomes `..Default::default()`, which turns
//! out to be a close enough substitute that the ported tests read almost the
//! same.

use serde::{Deserialize, Serialize};

/// A publication record.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Publication {
    /// Row id, once stored.
    pub id: Option<i64>,
    /// Canonical DOI.
    pub doi: Option<String>,
    /// PubMed identifier.
    pub pmid: Option<String>,
    /// PubMed Central identifier.
    pub pmcid: Option<String>,
    /// Article title.
    pub title: String,
    /// Abstract text.
    pub r#abstract: Option<String>,
    /// Author names.
    pub authors: Vec<String>,
    /// Journal name.
    pub journal: Option<String>,
    /// Publication date, ISO.
    pub publication_date: Option<String>,
    /// PubMed publication types.
    pub publication_types: Vec<String>,
    /// Keywords.
    pub keywords: Vec<String>,
    /// Whether any source has reported open access.
    pub is_open_access: bool,
    /// Licence identifier.
    pub license: Option<String>,
    /// Every source that has reported this work.
    pub sources: Vec<String>,
    /// The source that first reported it.
    pub first_seen_source: String,
    /// Row creation timestamp.
    pub created_at: Option<String>,
    /// Row update timestamp.
    pub updated_at: Option<String>,
}

/// A funding award, as asserted by one source.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Grant {
    /// Row id, once stored.
    pub id: Option<i64>,
    /// Owning publication.
    pub publication_id: Option<i64>,
    /// The source that asserted this row. Never empty — see
    /// [`crate::storage::replace_child_rows`].
    pub source: String,
    /// Funding agency.
    pub agency: Option<String>,
    /// Award identifier.
    pub grant_id: Option<String>,
    /// Agency country.
    pub country: Option<String>,
}

/// One author's affiliation, as asserted by one source.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct AuthorAffiliation {
    /// Row id, once stored.
    pub id: Option<i64>,
    /// Owning publication.
    pub publication_id: Option<i64>,
    /// The source that asserted this row.
    pub source: String,
    /// Author name.
    pub author: String,
    /// Affiliation text.
    pub affiliation: String,
    /// Author position in the byline.
    pub position: i64,
}
