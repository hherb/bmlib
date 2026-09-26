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

//! Citation-marker parsing and reference building — pure functions, no I/O.
//!
//! A port of `bmlib/citations/` (1,129 Python lines across four files):
//!
//! | Python | Here |
//! |---|---|
//! | `citations/models.py` | [`models`] |
//! | `citations/parser.py` | [`parser`] |
//! | `citations/formatter.py` | [`formatter`] |
//! | `citations/builder.py` | [`builder`] |
//!
//! Text carries `[@id:12345:Smith2023]` markers. [`build_references`] numbers
//! the cited documents by order of first appearance, formats references in
//! Vancouver, APA, Harvard, or Chicago style, replaces markers with `[N]`
//! (Vancouver, adjacent runs combined to `[1-3]`) or the style's author–date
//! inline citation, and reports a missing document as a visible placeholder
//! rather than dropping it.
//!
//! Metadata is a `HashMap<i64, DocumentMetadata>`. The upstream DB fetch was
//! severed in the Python port, and stays severed here.
//!
//! # The only dependency
//!
//! `serde`/`serde_json`, for the `to_dict`/`from_dict` pairs that Python
//! writes by hand. The marker scanner is hand-rolled rather than a regex —
//! see [`parser`]'s module docs for why.
//!
//! # Kept, not tidied
//!
//! `docs/DECISIONS.md` records five upstream-faithful oddities this module
//! keeps deliberately, each pinned by a Python test naming it: per-style
//! empty-title rendering, the ambiguous bare inverted `authors` string,
//! `"\n---"` with no leading blank line, `"Smithn.d."`, and
//! `author_surname("Jan van der Berg") == "Berg"`. Three more are pinned by
//! tests rather than the register — APA's seven-author ellipsis with nothing
//! elided, a two-run rendering as `"1,2"` rather than a range, and Harvard's
//! `"and et al."`. **A port that "fixed" any of these would diverge.**

pub mod builder;
pub mod formatter;
pub mod models;
pub mod parser;

pub use builder::{
    build_references, build_references_default, find_missing_documents, format_document,
};
pub use formatter::{
    ApaFormatter, ChicagoFormatter, CitationFormatter, HarvardFormatter, StyleFormatter,
    VancouverFormatter, MAX_AUTHORS_BEFORE_ET_AL,
};
pub use models::{
    author_surname, Citation, CitationStyle, DocumentMetadata, FormattedReference,
    DEFAULT_CITATION_STYLE,
};
pub use parser::{
    citation_positions, citations_in_range, count_citations, count_unique_citations,
    create_citation_marker, extract_document_id_from_citation, extract_label_from_citation,
    find_adjacent_citations, format_citation_group, marker_spans, parse_citations,
    replace_all_citations_with_numbers, replace_citation_with_number, unique_document_ids,
    validate_marker, CITATION_PATTERN,
};
