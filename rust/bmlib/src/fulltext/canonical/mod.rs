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

//! One canonical text per paper, built from JATS, Markdown or plain text.
//!
//! The port of Python's `bmlib.fulltext.canonical`; see [`model`] for the
//! layout and the offset unit.

pub mod jats;
pub mod model;
pub mod plain;

pub use jats::{canonical_from_jats, strip_links, JATS_CANONICAL_VERSION};
pub use model::{
    assemble, BlockDraft, BlockKind, CanonicalBlock, CanonicalError, CanonicalSection,
    CanonicalText, SectionDraft, SourceFormat, TextSpan, BLOCK_SEPARATOR, CELL_SEPARATOR,
    TOP_LEVEL,
};
pub use plain::{canonical_from_plain_text, split_lines, PLAIN_TEXT_VERSION};
