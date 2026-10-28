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

//! The canonical text of a paper: one string, and every piece of it as a span.
//!
//! The port of Python's `bmlib.fulltext.canonical.model`; the shared contract
//! `tests/fixtures/canonical_text.json` holds the two together. A paper's text
//! is converted, whatever its format, into a title and an ordered list of
//! sections, each with an optional heading and a list of blocks, laid out as
//! **one string**. The title, every heading and every block is a [`TextSpan`]
//! into it, so chunks, citations and highlights all point into one text.
//!
//! Spans count **Unicode code points** (`char`s), half-open `[start, end)`,
//! never bytes: Python counts code points, a byte offset differs from the
//! first non-ASCII character (`é`), and a UTF-16 one from the first character
//! outside the Basic Multilingual Plane (`𝛽`). Converting to the unit a
//! frontend counts happens at that boundary, never here.
//!
//! The layout is exact: the pieces are joined by [`BLOCK_SEPARATOR`] and
//! nothing else, every span is non-empty, and together the spans and
//! separators cover the whole text. [`CanonicalText::validate`] checks that,
//! and [`assemble`] validates what it returns.

use std::fmt;

use serde::{Deserialize, Serialize};

use crate::fulltext::jats_text::normalize_whitespace;

/// What separates two pieces (title, heading, block) of the canonical text.
/// It belongs to no span.
pub const BLOCK_SEPARATOR: &str = "\n\n";

/// What separates two cells of a table row. A tab is whitespace, so a quote
/// that separates the cells with spaces still matches the row.
pub const CELL_SEPARATOR: char = '\t';

/// The level of a top-level section.
pub const TOP_LEVEL: usize = 1;

/// A half-open range `[start, end)` of code points in the canonical text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TextSpan {
    /// The first code point covered.
    pub start: usize,
    /// One past the last code point covered.
    pub end: usize,
}

impl fmt::Display for TextSpan {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}, {})", self.start, self.end)
    }
}

/// What a block is: running prose, or one row of a table.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BlockKind {
    /// Running prose.
    Paragraph,
    /// One row of a table, its cells joined by [`CELL_SEPARATOR`].
    TableRow,
}

/// The format a canonical text was converted from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceFormat {
    /// JATS XML, through the JATS reader.
    Jats,
    /// Markdown.
    Markdown,
    /// Plain text.
    PlainText,
    /// A PDF, through the line extraction and the section segmenter.
    Pdf,
    /// An HTML page, through its main content.
    Html,
}

impl SourceFormat {
    /// The wire name, as Python's enum value spells it (`"plain_text"`).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            SourceFormat::Jats => "jats",
            SourceFormat::Markdown => "markdown",
            SourceFormat::PlainText => "plain_text",
            SourceFormat::Pdf => "pdf",
            SourceFormat::Html => "html",
        }
    }

    /// The converter version string for `version` of this format's converter:
    /// `"<format>-<version>"`, as `"plain_text-1"`.
    #[must_use]
    pub fn converter_version(self, version: u32) -> String {
        format!("{}-{version}", self.as_str())
    }
}

/// One block of a section: its kind and where its text lies.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonicalBlock {
    /// Prose or a table row.
    pub kind: BlockKind,
    /// Where the block's text lies.
    pub span: TextSpan,
}

/// One section: its level (1 is top), its heading if it has one, its blocks.
///
/// Sections form a flat list in document order; a section nests under the
/// nearest section before it with a lower `level`. Levels may skip (a Markdown
/// `#` followed by `###`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonicalSection {
    /// 1 for a top-level section.
    pub level: usize,
    /// Where the heading lies; `None` for an untitled section. The key is
    /// required in the JSON form, as Python requires it.
    #[serde(deserialize_with = "Option::deserialize")]
    pub heading_span: Option<TextSpan>,
    /// The section's blocks, in order.
    pub blocks: Vec<CanonicalBlock>,
}

/// A paper's canonical text and the spans of its title, headings and blocks.
///
/// `converter_version` names the converter that produced `text` (`"jats-1"`,
/// `"markdown-1"`, `"plain_text-1"`, `"pdf-1"`, `"html-1"`), so whatever points into it can tell when
/// a newer converter would lay the text out differently.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonicalText {
    /// The whole text.
    pub text: String,
    /// Where the title lies; `None` when there is no title. The key is
    /// required in the JSON form, as Python requires it.
    #[serde(deserialize_with = "Option::deserialize")]
    pub title_span: Option<TextSpan>,
    /// The sections, in document order.
    pub sections: Vec<CanonicalSection>,
    /// The format the text was converted from.
    pub source_format: SourceFormat,
    /// The converter and its version, as `"jats-1"`.
    pub converter_version: String,
}

/// A canonical text that is malformed or whose spans do not fit its text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CanonicalError {
    /// A span whose start lies after its end.
    Reversed(TextSpan),
    /// A span reaching outside a text of `length` code points.
    Outside {
        /// The span.
        span: TextSpan,
        /// The text's length in code points.
        length: usize,
    },
    /// A span that is empty, or does not start at `position`, where the
    /// layout puts the next piece (after a [`BLOCK_SEPARATOR`] unless it is
    /// the first).
    Misplaced {
        /// The span.
        span: TextSpan,
        /// Where the next piece belongs.
        position: usize,
    },
    /// Text past the last span (or any text, where there is no span).
    RunsOn {
        /// Where the last span ended.
        position: usize,
    },
    /// A section level below [`TOP_LEVEL`].
    Level(usize),
    /// A stored value that is not a canonical text at all.
    Malformed(String),
}

impl fmt::Display for CanonicalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CanonicalError::Reversed(span) => write!(f, "span {span} is reversed"),
            CanonicalError::Outside { span, length } => {
                write!(f, "span {span} lies outside a text of {length} code points")
            }
            CanonicalError::Misplaced { span, position } => write!(
                f,
                "span {span} is empty or does not start at {position}, where the layout puts \
                 the next piece"
            ),
            CanonicalError::RunsOn { position } => {
                write!(f, "the text runs on past the last span, from {position}")
            }
            CanonicalError::Level(level) => write!(
                f,
                "a section's level must be at least {TOP_LEVEL}, got {level}"
            ),
            CanonicalError::Malformed(why) => write!(f, "malformed canonical text: {why}"),
        }
    }
}

impl std::error::Error for CanonicalError {}

impl CanonicalText {
    /// The text `span` covers, or `None` if it is reversed or reaches outside
    /// the text.
    #[must_use]
    pub fn span_text(&self, span: TextSpan) -> Option<&str> {
        if span.start > span.end {
            return None;
        }
        // One walk to `span.end`, never over the whole text.
        let mut offsets = self
            .text
            .char_indices()
            .map(|(offset, _)| offset)
            .chain(std::iter::once(self.text.len()));
        let start = offsets.nth(span.start)?;
        let end = if span.end == span.start {
            start
        } else {
            offsets.nth(span.end - span.start - 1)?
        };
        Some(&self.text[start..end])
    }

    /// Every span, title first, in document order.
    #[must_use]
    pub fn spans(&self) -> Vec<TextSpan> {
        let mut out: Vec<TextSpan> = self.title_span.into_iter().collect();
        for section in &self.sections {
            out.extend(section.heading_span);
            out.extend(section.blocks.iter().map(|block| block.span));
        }
        out
    }

    /// Check the layout: the spans, in order and each non-empty, tile the text.
    ///
    /// The first span starts at 0, each next one starts right after a
    /// [`BLOCK_SEPARATOR`] following the one before, and the last ends at the
    /// end of the text; with no spans the text is empty.
    ///
    /// # Errors
    ///
    /// The first span or level that is wrong.
    pub fn validate(&self) -> Result<(), CanonicalError> {
        if let Some(section) = self.sections.iter().find(|s| s.level < TOP_LEVEL) {
            return Err(CanonicalError::Level(section.level));
        }
        let chars: Vec<char> = self.text.chars().collect();
        let separator: Vec<char> = BLOCK_SEPARATOR.chars().collect();
        let mut position = 0;
        for (index, span) in self.spans().into_iter().enumerate() {
            check_span(span, chars.len())?;
            if index > 0 {
                if chars.get(position..position + separator.len()) != Some(&separator[..]) {
                    return Err(CanonicalError::Misplaced { span, position });
                }
                position += separator.len();
            }
            if span.start != position || span.end == span.start {
                return Err(CanonicalError::Misplaced { span, position });
            }
            position = span.end;
        }
        if position != chars.len() {
            return Err(CanonicalError::RunsOn { position });
        }
        Ok(())
    }

    /// Rebuild and [`validate`](Self::validate) a canonical text from its JSON
    /// form, the shape Python's `CanonicalText.to_dict()` produces.
    ///
    /// # Errors
    ///
    /// [`CanonicalError::Malformed`] if a field is missing or of the wrong
    /// type, or the error `validate` reports.
    pub fn from_value(value: serde_json::Value) -> Result<Self, CanonicalError> {
        let text: CanonicalText = serde_json::from_value(value)
            .map_err(|error| CanonicalError::Malformed(error.to_string()))?;
        text.validate()?;
        Ok(text)
    }
}

/// A block before layout: its kind and its text, not yet normalised.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlockDraft {
    /// Prose or a table row.
    pub kind: BlockKind,
    /// The block's text; [`assemble`] normalises a paragraph's.
    pub text: String,
}

impl BlockDraft {
    /// A paragraph; [`assemble`] collapses its whitespace.
    #[must_use]
    pub fn paragraph(text: impl Into<String>) -> Self {
        BlockDraft {
            kind: BlockKind::Paragraph,
            text: text.into(),
        }
    }

    /// A table row: each cell normalised, joined by [`CELL_SEPARATOR`].
    ///
    /// An empty cell is kept (it holds a column's place); a row whose cells
    /// are all empty is an empty block, which [`assemble`] drops.
    #[must_use]
    pub fn row<S: AsRef<str>>(cells: &[S]) -> Self {
        let cells: Vec<String> = cells
            .iter()
            .map(|cell| normalize_whitespace(cell.as_ref()))
            .collect();
        BlockDraft {
            kind: BlockKind::TableRow,
            text: cells.join(&CELL_SEPARATOR.to_string()),
        }
    }
}

/// A section before layout: heading (`""` for none), level, blocks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SectionDraft {
    /// The heading; empty for an untitled section.
    pub heading: String,
    /// 1 for a top-level section.
    pub level: usize,
    /// The blocks, in order.
    pub blocks: Vec<BlockDraft>,
}

impl SectionDraft {
    /// A section draft from its parts.
    #[must_use]
    pub fn new(heading: impl Into<String>, level: usize, blocks: Vec<BlockDraft>) -> Self {
        SectionDraft {
            heading: heading.into(),
            level,
            blocks,
        }
    }
}

/// The text laid out so far and its length in code points.
#[derive(Default)]
struct Layout {
    text: String,
    length: usize,
}

impl Layout {
    /// Append `piece` (after a separator unless it is the first) and return its span.
    fn put(&mut self, piece: &str) -> TextSpan {
        if !self.text.is_empty() {
            self.text.push_str(BLOCK_SEPARATOR);
            self.length += BLOCK_SEPARATOR.chars().count();
        }
        let start = self.length;
        self.text.push_str(piece);
        self.length += piece.chars().count();
        TextSpan {
            start,
            end: self.length,
        }
    }
}

/// Lay `title` and `sections` out as one canonical text and record every span.
///
/// The title, headings and paragraphs have their whitespace collapsed; a table
/// row is kept as [`BlockDraft::row`] built it. An empty block is dropped, a
/// section with no heading and no blocks left is dropped, and a heading with no
/// blocks is kept. Pieces are joined by [`BLOCK_SEPARATOR`].
///
/// # Panics
///
/// If a section's level is below [`TOP_LEVEL`]: the result is validated before
/// it is returned, as Python's `assemble` raises `ValueError`.
#[must_use]
pub fn assemble(
    title: &str,
    sections: &[SectionDraft],
    source_format: SourceFormat,
    converter_version: &str,
) -> CanonicalText {
    let mut layout = Layout::default();
    let title = normalize_whitespace(title);
    let title_span = (!title.is_empty()).then(|| layout.put(&title));
    let mut laid_out = Vec::new();
    for draft in sections {
        let heading = normalize_whitespace(&draft.heading);
        let blocks: Vec<(BlockKind, String)> = draft
            .blocks
            .iter()
            .map(|block| (block.kind, block_text(block)))
            .filter(|(_, text)| !text.is_empty())
            .collect();
        if heading.is_empty() && blocks.is_empty() {
            continue;
        }
        let heading_span = (!heading.is_empty()).then(|| layout.put(&heading));
        let blocks = blocks
            .iter()
            .map(|(kind, text)| CanonicalBlock {
                kind: *kind,
                span: layout.put(text),
            })
            .collect();
        laid_out.push(CanonicalSection {
            level: draft.level,
            heading_span,
            blocks,
        });
    }
    let text = CanonicalText {
        text: layout.text,
        title_span,
        sections: laid_out,
        source_format,
        converter_version: converter_version.to_string(),
    };
    if let Err(error) = text.validate() {
        panic!("assemble laid out an invalid canonical text: {error}");
    }
    text
}

/// A block's laid-out text: each cell of a row normalised (empty if every cell
/// is), prose normalised as a whole.
fn block_text(block: &BlockDraft) -> String {
    match block.kind {
        BlockKind::TableRow => {
            let cells: Vec<String> = block
                .text
                .split(CELL_SEPARATOR)
                .map(normalize_whitespace)
                .collect();
            if cells.iter().all(String::is_empty) {
                String::new()
            } else {
                cells.join(&CELL_SEPARATOR.to_string())
            }
        }
        BlockKind::Paragraph => normalize_whitespace(&block.text),
    }
}

/// `Err` if `span` is reversed or reaches outside `[0, length]`.
fn check_span(span: TextSpan, length: usize) -> Result<(), CanonicalError> {
    if span.start > span.end {
        return Err(CanonicalError::Reversed(span));
    }
    if span.end > length {
        return Err(CanonicalError::Outside { span, length });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build(title: &str, sections: &[SectionDraft]) -> CanonicalText {
        assemble(title, sections, SourceFormat::PlainText, "test-1")
    }

    fn para(start: usize, end: usize) -> CanonicalBlock {
        CanonicalBlock {
            kind: BlockKind::Paragraph,
            span: TextSpan { start, end },
        }
    }

    fn doc_with(text: &str, level: usize, blocks: Vec<CanonicalBlock>) -> CanonicalText {
        CanonicalText {
            text: text.to_string(),
            title_span: None,
            sections: vec![CanonicalSection {
                level,
                heading_span: None,
                blocks,
            }],
            source_format: SourceFormat::PlainText,
            converter_version: "test-1".to_string(),
        }
    }

    #[test]
    fn pieces_are_joined_by_the_block_separator() {
        let doc = build(
            "T",
            &[SectionDraft::new("H", 1, vec![BlockDraft::paragraph("p")])],
        );
        assert_eq!(doc.text, "T\n\nH\n\np");
    }

    #[test]
    fn every_piece_is_a_span_into_the_text() {
        let doc = build(
            "Title",
            &[SectionDraft::new(
                "Head",
                2,
                vec![BlockDraft::paragraph("Body")],
            )],
        );
        assert_eq!(doc.span_text(doc.title_span.unwrap()), Some("Title"));
        let section = &doc.sections[0];
        assert_eq!(section.level, 2);
        assert_eq!(doc.span_text(section.heading_span.unwrap()), Some("Head"));
        assert_eq!(doc.span_text(section.blocks[0].span), Some("Body"));
    }

    #[test]
    fn an_empty_untitled_section_is_dropped_and_a_heading_only_one_kept() {
        let doc = build(
            "",
            &[
                SectionDraft::new("", 1, vec![BlockDraft::paragraph("  ")]),
                SectionDraft::new("Figures", 1, vec![]),
            ],
        );
        assert_eq!(doc.sections.len(), 1);
        assert!(doc.sections[0].blocks.is_empty());
        assert_eq!(doc.text, "Figures");
    }

    #[test]
    fn a_paragraph_collapses_its_whitespace_and_a_blank_title_has_no_span() {
        let doc = build(
            "  ",
            &[SectionDraft::new(
                "",
                1,
                vec![BlockDraft::paragraph(" a \n\n b\t c ")],
            )],
        );
        assert_eq!(doc.text, "a b c");
        assert_eq!(doc.title_span, None);
    }

    #[test]
    fn a_row_joins_its_normalised_cells_with_a_tab_and_keeps_empty_cells() {
        let row = BlockDraft::row(&["a", " b  c"]);
        assert_eq!(row.kind, BlockKind::TableRow);
        assert_eq!(row.text, "a\tb c");
        assert_eq!(BlockDraft::row(&["a", "", "c"]).text, "a\t\tc");
        assert_eq!(BlockDraft::row(&["a\tb"]).text, "a b");
    }

    #[test]
    fn a_row_of_empty_cells_is_dropped() {
        let doc = build(
            "",
            &[SectionDraft::new("", 1, vec![BlockDraft::row(&["", " "])])],
        );
        assert!(doc.sections.is_empty());
    }

    #[test]
    fn span_text_counts_code_points_and_refuses_bad_spans() {
        let doc = doc_with("\u{1d6fd}x", 1, vec![]);
        assert_eq!(doc.span_text(TextSpan { start: 1, end: 2 }), Some("x"));
        assert_eq!(
            doc.span_text(TextSpan { start: 0, end: 1 }),
            Some("\u{1d6fd}")
        );
        assert_eq!(doc.span_text(TextSpan { start: 2, end: 2 }), Some(""));
        assert_eq!(doc.span_text(TextSpan { start: 1, end: 3 }), None);
        assert_eq!(doc.span_text(TextSpan { start: 2, end: 1 }), None);
    }

    #[test]
    fn a_text_the_spans_tile_validates() {
        assert_eq!(
            doc_with("ab\n\ncd", 1, vec![para(0, 2), para(4, 6)]).validate(),
            Ok(())
        );
        assert_eq!(doc_with("", 1, vec![]).validate(), Ok(()));
    }

    #[test]
    fn spans_that_do_not_tile_the_text_are_refused() {
        for (text, blocks) in [
            ("ab\n\ncd", vec![para(0, 3), para(2, 5)]),
            ("ab\n\ncd", vec![para(0, 2), para(2, 6)]),
            ("ab\n\ncd", vec![para(0, 2), para(4, 4), para(4, 6)]),
            ("abcdef", vec![para(0, 2), para(4, 6)]),
        ] {
            assert!(
                matches!(
                    doc_with(text, 1, blocks.clone()).validate(),
                    Err(CanonicalError::Misplaced { .. })
                ),
                "{text:?} {blocks:?}"
            );
        }
        assert_eq!(
            doc_with("ab\n\ncd", 1, vec![para(0, 2)]).validate(),
            Err(CanonicalError::RunsOn { position: 2 })
        );
        assert_eq!(
            doc_with("ab", 1, vec![]).validate(),
            Err(CanonicalError::RunsOn { position: 0 })
        );
        assert!(matches!(
            doc_with("abcdef", 1, vec![para(0, 9)]).validate(),
            Err(CanonicalError::Outside { .. })
        ));
        assert!(matches!(
            doc_with("abcdef", 1, vec![para(3, 2)]).validate(),
            Err(CanonicalError::Reversed(_))
        ));
        assert_eq!(
            doc_with("a", 0, vec![para(0, 1)]).validate(),
            Err(CanonicalError::Level(0))
        );
    }

    #[test]
    #[should_panic(expected = "level")]
    fn assemble_validates_what_it_returns() {
        let _ = build("", &[SectionDraft::new("H", 0, vec![])]);
    }

    #[test]
    fn a_hand_built_row_has_its_cells_normalised() {
        let row = BlockDraft {
            kind: BlockKind::TableRow,
            text: "a\n\nb\t \t".to_string(),
        };
        assert_eq!(
            build("", &[SectionDraft::new("", 1, vec![row])]).text,
            "a b\t\t"
        );
        let blank = BlockDraft {
            kind: BlockKind::TableRow,
            text: "\t \t".to_string(),
        };
        assert_eq!(build("", &[SectionDraft::new("", 1, vec![blank])]).text, "");
    }

    #[test]
    fn from_value_refuses_an_unknown_kind_and_an_invalid_span() {
        let mut value = serde_json::to_value(doc_with("ab", 1, vec![para(0, 2)])).unwrap();
        value["sections"][0]["blocks"][0]["kind"] = "figure".into();
        assert!(matches!(
            CanonicalText::from_value(value.clone()),
            Err(CanonicalError::Malformed(why)) if why.contains("figure")
        ));
        value["sections"][0]["blocks"][0]["kind"] = "paragraph".into();
        value["sections"][0]["blocks"][0]["span"]["end"] = 99.into();
        assert!(matches!(
            CanonicalText::from_value(value),
            Err(CanonicalError::Outside { .. })
        ));
    }

    #[test]
    fn the_converter_version_names_the_format() {
        assert_eq!(SourceFormat::PlainText.converter_version(1), "plain_text-1");
    }
}
