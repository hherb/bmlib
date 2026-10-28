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

//! One canonical text per paper, from JATS, Markdown, plain text, a PDF or
//! HTML (with the `html` feature), and its chunks.
//!
//! The port of Python's `bmlib.fulltext.canonical`; see [`model`] for the
//! layout and the offset unit.

pub mod assess;
pub mod chunk;
#[cfg(feature = "html")]
pub mod html;
pub mod jats;
pub mod markdown;
pub mod model;
pub mod pdf;
pub mod plain;

pub use assess::{assess_full_text, count_words, FullTextAssessment, MIN_BODY_WORDS};
pub use chunk::{
    chunk_canonical, is_abbreviation, sentence_spans, Chunk, ChunkError, ABBREVIATIONS,
    CHUNKER_VERSION, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS,
};
#[cfg(feature = "html")]
pub use html::{canonical_from_html, HTML_CANONICAL_VERSION};
pub use jats::{canonical_from_jats, strip_links, JATS_CANONICAL_VERSION};
pub use markdown::{canonical_from_markdown, strip_inline, MARKDOWN_VERSION};
pub use model::{
    assemble, BlockDraft, BlockKind, CanonicalBlock, CanonicalError, CanonicalSection,
    CanonicalText, SectionDraft, SourceFormat, TextSpan, BLOCK_SEPARATOR, CELL_SEPARATOR,
    TOP_LEVEL,
};
pub use pdf::{canonical_from_segmented, PDF_CANONICAL_VERSION};
pub use plain::{canonical_from_plain_text, split_lines, split_paragraphs, PLAIN_TEXT_VERSION};
