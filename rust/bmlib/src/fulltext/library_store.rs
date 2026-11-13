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

//! The shared library as the full-text service's second store: the request's
//! identifiers as library identifiers, and the store itself.
//!
//! The library is keyed by the request's `pmid`, `doi` and `pmc_id`. Each is
//! made canonical on its own, so one bad value costs only that identifier, not
//! the whole key.

use crate::library::{Installation, Library, PaperIds};

/// The request's identifiers as the library's canonical ones, and the names of
/// the fields that were refused.
///
/// Each of `pmid`, `doi` and `pmc_id` goes alone through
/// [`PaperIds::canonical`] (a blank one is simply absent). A field it refuses
/// is left out of the result and its name (`"pmid"`, `"doi"` or `"pmc_id"`,
/// the request's parameter names) is returned in the second part, in that
/// order. This function only reports; the caller logs the warning.
#[must_use]
pub fn library_ids(
    pmid: &str,
    doi: Option<&str>,
    pmc_id: Option<&str>,
) -> (PaperIds, Vec<&'static str>) {
    let mut ids = PaperIds::default();
    let mut dropped = Vec::new();
    match PaperIds::canonical(Some(pmid), None, None) {
        Ok(one) => ids.pmid = one.pmid,
        Err(_) => dropped.push("pmid"),
    }
    match PaperIds::canonical(None, doi, None) {
        Ok(one) => ids.doi = one.doi,
        Err(_) => dropped.push("doi"),
    }
    match PaperIds::canonical(None, None, pmc_id) {
        Ok(one) => ids.pmcid = one.pmcid,
        Err(_) => dropped.push("pmc_id"),
    }
    (ids, dropped)
}

/// The shared library and the installation that writes to it: what the
/// full-text service needs to read and save papers there.
pub struct LibraryStore {
    /// The library folder.
    pub library: Library,
    /// The installation whose record the service's saves go in.
    pub who: Installation,
}

impl LibraryStore {
    /// A store over `library`, saving as `who`.
    #[must_use]
    pub fn new(library: Library, who: Installation) -> Self {
        Self { library, who }
    }
}
