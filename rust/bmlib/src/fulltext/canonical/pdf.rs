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

//! A segmented PDF to canonical text: the segmenter's sections, as extracted.
//!
//! The port of Python's `bmlib.fulltext.canonical.pdf`. The input is
//! [`SectionSegmenter::segment_document`]'s output, so this module reads no PDF
//! itself and is compiled without the `pdf` feature. The text is taken as the
//! extraction gives it: a table's cells may run together, and a hyphen at a line
//! break is kept, the lines joined by a space (`dose- response`), since `dose-` /
//! `response` must keep it and nothing in a PDF tells it from `random-` / `ised`.
//!
//! Python extracts lines with PyMuPDF and Rust with PDFium, so the two can
//! segment the same PDF differently; the shared contract starts at the
//! [`SegmentedDocument`].
//!
//! [`SectionSegmenter::segment_document`]: crate::fulltext::segmenter::SectionSegmenter::segment_document

use crate::fulltext::canonical::model::{
    assemble, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};
use crate::fulltext::canonical::plain::split_paragraphs;
use crate::fulltext::models::{Section, SectionType, SegmentedDocument};

/// Bumped whenever this converter, [`assemble`], or the segmenter and
/// extraction feeding it can lay a PDF's text out differently. Python pins it
/// against a digest of its own converter, segmenter and extraction; a change to
/// the Rust segmenter alone is held only by the shared contract and the
/// segmenter's own oracle.
pub const PDF_CANONICAL_VERSION: u32 = 1;

/// Whether `kind` is one of the segmenter's containers, whose headings ("Front
/// Matter", "Full Text") bmlib makes up rather than reads from the page.
fn heading_is_unprinted(kind: SectionType) -> bool {
    matches!(kind, SectionType::FrontMatter | SectionType::Unknown)
}

/// Push `section` at `level`, then each of its subsections one level deeper.
fn add(section: &Section, level: usize, drafts: &mut Vec<SectionDraft>) {
    let heading = if heading_is_unprinted(section.section_type) {
        ""
    } else {
        section.title.as_str()
    };
    drafts.push(SectionDraft::new(
        heading,
        level,
        split_paragraphs(&section.content),
    ));
    for subsection in &section.subsections {
        add(subsection, level + 1, drafts);
    }
}

/// Convert a segmented PDF to its canonical text.
///
/// The title is `document.title` (pass a known title to `segment_document()`
/// as its `metadata_title`, which it checks against page 1). Each section
/// becomes a section at [`TOP_LEVEL`] and each of its `subsections` follows it
/// one level deeper. A section's heading is its `title`, except that front
/// matter and the no-headings fallback are untitled, since their headings are
/// not the paper's. That is keyed on `section_type`, so any `FrontMatter` or
/// `Unknown` section is untitled, a hand-built one too; the segmenter makes
/// `Unknown` only for the fallback. Paragraphs are the content's
/// blank-line-separated runs ([`split_paragraphs`]).
#[must_use]
pub fn canonical_from_segmented(document: &SegmentedDocument) -> CanonicalText {
    let mut drafts = Vec::new();
    for section in &document.sections {
        add(section, TOP_LEVEL, &mut drafts);
    }
    assemble(
        document.title.as_deref().unwrap_or(""),
        &drafts,
        SourceFormat::Pdf,
        &SourceFormat::Pdf.converter_version(PDF_CANONICAL_VERSION),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fulltext::models::TextBlock;
    use crate::fulltext::segmenter::SectionSegmenter;

    fn section(kind: SectionType, title: &str, content: &str) -> Section {
        Section {
            section_type: kind,
            title: title.to_string(),
            content: content.to_string(),
            page_start: 0,
            page_end: 0,
            confidence: 1.0,
            subsections: Vec::new(),
        }
    }

    #[test]
    fn the_containers_made_up_headings_are_not_the_papers() {
        let document = SegmentedDocument {
            sections: vec![
                section(SectionType::FrontMatter, "Front Matter", "By A. Author"),
                section(SectionType::Unknown, "Full Text", "Body."),
                section(SectionType::Methods, "2. Methods", "We did."),
            ],
            ..SegmentedDocument::default()
        };
        let doc = canonical_from_segmented(&document);
        assert_eq!(doc.text, "By A. Author\n\nBody.\n\n2. Methods\n\nWe did.");
        assert_eq!(doc.converter_version, "pdf-1");
    }

    /// A first-page line at height `y`, 12 points tall.
    fn line(text: &str, y: f64, font_size: f64, is_bold: bool) -> TextBlock {
        TextBlock {
            text: text.to_string(),
            page_num: 0,
            font_size,
            font_name: "Times".to_string(),
            is_bold,
            is_italic: false,
            x: 50.0,
            y,
            width: 400.0,
            height: 12.0,
        }
    }

    #[test]
    fn the_segmenters_output_converts_end_to_end() {
        let blocks = [
            line("A randomised trial", 50.0, 20.0, false),
            line("We randomised 120", 100.0, 10.0, false),
            line("patients.", 112.0, 10.0, false),
            line("Methods", 150.0, 14.0, true),
            line("Dosing was fixed.", 170.0, 10.0, false),
            line("Follow-up was a year.", 210.0, 10.0, false),
        ];
        let document =
            SectionSegmenter::default().segment_document(&blocks, "", None, serde_json::json!({}));
        assert_eq!(
            canonical_from_segmented(&document).text,
            "A randomised trial\n\nA randomised trial\n\nWe randomised 120 patients.\n\n\
             Methods\n\nDosing was fixed.\n\nFollow-up was a year."
        );
    }

    #[test]
    fn a_subsection_follows_its_parent_one_level_deeper() {
        let mut parent = section(SectionType::Methods, "Methods", "");
        parent
            .subsections
            .push(section(SectionType::Methods, "Participants", "Adults."));
        let document = SegmentedDocument {
            sections: vec![parent],
            ..SegmentedDocument::default()
        };
        let levels: Vec<usize> = canonical_from_segmented(&document)
            .sections
            .iter()
            .map(|s| s.level)
            .collect();
        assert_eq!(levels, [1, 2]);
    }
}
