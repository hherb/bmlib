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

//! Plain text to canonical text: one untitled section, paragraphs by blank lines.
//!
//! The port of Python's `bmlib.fulltext.canonical.plain`.

use crate::fulltext::canonical::model::{
    assemble, BlockDraft, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};

/// Bumped whenever the plain-text converter (or [`assemble`]) can lay a text
/// out differently.
pub const PLAIN_TEXT_VERSION: u32 = 1;

/// `text`'s lines, with `\r\n` and a lone `\r` read as `\n`.
#[must_use]
pub fn split_lines(text: &str) -> Vec<String> {
    text.replace("\r\n", "\n")
        .replace('\r', "\n")
        .split('\n')
        .map(str::to_string)
        .collect()
}

/// Convert plain `text`: paragraphs are separated by blank lines.
///
/// A line holding only whitespace counts as blank. `title` (the paper's title,
/// known from elsewhere) becomes the title when it is not blank. Everything
/// else is one section, level 1, with no heading.
#[must_use]
pub fn canonical_from_plain_text(text: &str, title: &str) -> CanonicalText {
    let mut paragraphs = Vec::new();
    let mut current: Vec<String> = Vec::new();
    for line in split_lines(text) {
        if line.chars().any(|ch| !ch.is_whitespace()) {
            current.push(line);
        } else if !current.is_empty() {
            paragraphs.push(BlockDraft::paragraph(current.join(" ")));
            current.clear();
        }
    }
    if !current.is_empty() {
        paragraphs.push(BlockDraft::paragraph(current.join(" ")));
    }
    assemble(
        title,
        &[SectionDraft::new("", TOP_LEVEL, paragraphs)],
        SourceFormat::PlainText,
        &SourceFormat::PlainText.converter_version(PLAIN_TEXT_VERSION),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_line_ending_splits_alike() {
        assert_eq!(split_lines("a\r\nb\rc\nd"), ["a", "b", "c", "d"]);
    }

    #[test]
    fn blank_lines_separate_paragraphs() {
        let doc = canonical_from_plain_text("a\nb\n\n \nc", "");
        assert_eq!(doc.text, "a b\n\nc");
        assert_eq!(doc.converter_version, "plain_text-1");
    }
}
