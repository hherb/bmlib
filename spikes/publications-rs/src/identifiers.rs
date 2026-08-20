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

//! Identifier normalisation — a direct port.

/// Prefixes sources sometimes prepend to a DOI.
const DOI_PREFIXES: [&str; 5] = [
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
];

/// Return a canonical, case-folded DOI, or `None`.
///
/// DOIs are case-insensitive per the DOI handbook, but sources disagree:
/// PubMed preserves the registered form, OpenAlex lower-cases. Storing one
/// canonical form is what makes cross-source deduplication work.
pub fn normalize_doi(doi: Option<&str>) -> Option<String> {
    let d = doi?.trim();
    if d.is_empty() {
        return None;
    }
    let lowered = d.to_lowercase();
    let stripped = DOI_PREFIXES
        .iter()
        .find(|p| lowered.starts_with(*p))
        .map_or(d, |p| &d[p.len()..]);
    let out = stripped.trim().to_lowercase();
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// Return a whitespace-stripped PMID, or `None` for an empty value.
pub fn normalize_pmid(pmid: Option<&str>) -> Option<String> {
    let p = pmid?.trim();
    if p.is_empty() {
        None
    } else {
        Some(p.to_string())
    }
}
