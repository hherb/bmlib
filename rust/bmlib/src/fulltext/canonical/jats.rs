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

//! A parsed JATS article to canonical text.
//!
//! The port of Python's `bmlib.fulltext.canonical.jats`. Everything the HTML
//! rendering shows, apart from the author, journal and identifier lines, in
//! the same order: the abstract, the body with its back matter, funding,
//! figure captions, tables as plain rows, and references. The headings
//! `Abstract`, `Funding`, `Figures`, `Tables` and `References` are the labels
//! the rendering already prints for those modelled fields; no other heading is
//! invented. Exhibits come after the body, where the rendering puts them: the
//! article keeps them in lists of their own, not where the body cites them.

use crate::fulltext::canonical::model::{
    assemble, BlockDraft, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};
use crate::fulltext::models::{JATSArticle, JATSBodySection};
use crate::fulltext::service::{format_award, inline_link_pattern};

/// Bumped whenever this converter, [`assemble`] or the JATS reader can lay an
/// article out differently; Python pins it beside `RENDERER_VERSION`.
pub const JATS_CANONICAL_VERSION: u32 = 1;

/// The label for the abstract's section.
pub const ABSTRACT_HEADING: &str = "Abstract";
/// The label for the funding section.
pub const FUNDING_HEADING: &str = "Funding";
/// The label for the figures section.
pub const FIGURES_HEADING: &str = "Figures";
/// The label for the tables section.
pub const TABLES_HEADING: &str = "Tables";
/// The label for the references section.
pub const REFERENCES_HEADING: &str = "References";

/// `text` with each `[text](target)` link the reader wrote reduced to its text.
#[must_use]
pub fn strip_links(text: &str) -> String {
    inline_link_pattern().replace_all(text, "$1").into_owned()
}

/// Convert a parsed JATS `article` to its canonical text.
///
/// Order: title; `Abstract` with each abstract section beneath it; the body
/// sections, nested by level; `Funding` (statements, then awards); `Figures`
/// and `Tables`, each exhibit a section headed by its label; `References`, one
/// block per reference.
#[must_use]
pub fn canonical_from_jats(article: &JATSArticle) -> CanonicalText {
    let sub_level = TOP_LEVEL + 1;
    let mut sections: Vec<SectionDraft> = Vec::new();
    if !article.abstract_sections.is_empty() {
        sections.push(SectionDraft::new(ABSTRACT_HEADING, TOP_LEVEL, vec![]));
        sections.extend(article.abstract_sections.iter().map(|part| {
            SectionDraft::new(
                part.title.clone(),
                sub_level,
                vec![BlockDraft::paragraph(part.content.clone())],
            )
        }));
    }
    for body in &article.body_sections {
        push_body_sections(&mut sections, body, TOP_LEVEL);
    }
    let funding: Vec<BlockDraft> = article
        .funding_statements
        .iter()
        .map(|statement| BlockDraft::paragraph(statement.clone()))
        .chain(
            article
                .funding_awards
                .iter()
                .map(|award| BlockDraft::paragraph(format_award(award))),
        )
        .collect();
    if !funding.is_empty() {
        sections.push(SectionDraft::new(FUNDING_HEADING, TOP_LEVEL, funding));
    }
    if !article.figures.is_empty() {
        sections.push(SectionDraft::new(FIGURES_HEADING, TOP_LEVEL, vec![]));
        for figure in &article.figures {
            let blocks = std::iter::once(&figure.caption)
                .chain(&figure.footnotes)
                .map(|text| BlockDraft::paragraph(text.clone()))
                .collect();
            sections.push(SectionDraft::new(figure.label.clone(), sub_level, blocks));
        }
    }
    if !article.tables.is_empty() {
        sections.push(SectionDraft::new(TABLES_HEADING, TOP_LEVEL, vec![]));
        for table in &article.tables {
            let mut blocks = vec![BlockDraft::paragraph(table.caption.clone())];
            blocks.extend(
                table
                    .header_rows
                    .iter()
                    .chain(&table.body_rows)
                    .map(|row| BlockDraft::row(row)),
            );
            blocks.extend(
                table
                    .footnotes
                    .iter()
                    .map(|note| BlockDraft::paragraph(note.clone())),
            );
            sections.push(SectionDraft::new(table.label.clone(), sub_level, blocks));
        }
    }
    if !article.references.is_empty() {
        let blocks = article
            .references
            .iter()
            .map(|reference| BlockDraft::paragraph(reference.formatted_citation()))
            .collect();
        sections.push(SectionDraft::new(REFERENCES_HEADING, TOP_LEVEL, blocks));
    }
    assemble(
        &strip_links(&article.title),
        &sections,
        SourceFormat::Jats,
        &SourceFormat::Jats.converter_version(JATS_CANONICAL_VERSION),
    )
}

/// Push `section` and its subsections, depth first, links stripped.
fn push_body_sections(out: &mut Vec<SectionDraft>, section: &JATSBodySection, level: usize) {
    let blocks = section
        .paragraphs
        .iter()
        .map(|paragraph| BlockDraft::paragraph(strip_links(paragraph)))
        .collect();
    out.push(SectionDraft::new(
        strip_links(&section.title),
        level,
        blocks,
    ));
    for sub in &section.subsections {
        push_body_sections(out, sub, level + 1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::citations::find_passage;
    use crate::fulltext::canonical::model::BlockKind;
    use crate::fulltext::jats_reader::parse;

    /// The acceptance threshold the tests locate quotes at.
    const MIN_SIMILARITY: f64 = 0.9;

    const FIXTURE: &str = include_str!("../../../../../tests/fixtures/canonical_text.json");

    fn case_text(name: &str) -> CanonicalText {
        let fixture: serde_json::Value = serde_json::from_str(FIXTURE).expect("JSON");
        let case = fixture["cases"]
            .as_array()
            .expect("cases")
            .iter()
            .find(|case| case["name"] == name)
            .expect("the case exists");
        canonical_from_jats(&parse(case["input"].as_str().expect("input")).expect("parses"))
    }

    fn blocks(doc: &CanonicalText, kind: BlockKind) -> Vec<(usize, usize)> {
        doc.sections
            .iter()
            .flat_map(|section| &section.blocks)
            .filter(|block| block.kind == kind)
            .map(|block| (block.span.start, block.span.end))
            .collect()
    }

    fn found_at(quote: &str, doc: &CanonicalText) -> (usize, usize) {
        let found = find_passage(quote, &doc.text, MIN_SIMILARITY).expect("a valid threshold");
        let span = found.span.expect("the quote is found");
        (span.start, span.end)
    }

    #[test]
    fn a_quote_of_a_table_row_is_found_at_the_row() {
        let doc = case_text("jats/full");
        assert_eq!(found_at("A 12", &doc), blocks(&doc, BlockKind::TableRow)[1]);
    }

    #[test]
    fn a_quote_of_a_link_stripped_sentence_is_found_at_its_block() {
        let doc = case_text("jats/inline-links");
        let quote = "As shown in Fig 1 and before 2, the effect [95% CI] held.";
        assert_eq!(found_at(quote, &doc), blocks(&doc, BlockKind::Paragraph)[0]);
    }

    #[test]
    fn a_graphic_only_table_is_a_heading_only_section() {
        let xml = "<?xml version=\"1.0\"?><article xmlns:xlink=\"http://www.w3.org/1999/xlink\">\
                   <front><article-meta><article-id pub-id-type=\"pmc\">PMC1</article-id>\
                   </article-meta></front><body><sec><title>S</title><p>x</p>\
                   <table-wrap id=\"t1\"><label>Table 1</label><graphic xlink:href=\"t1.jpg\"/>\
                   </table-wrap></sec></body></article>";
        let doc = canonical_from_jats(&parse(xml).expect("parses"));
        let table = doc.sections.last().expect("a table section");
        assert_eq!(
            doc.span_text(table.heading_span.expect("a heading")),
            Some("Table 1")
        );
        assert!(table.blocks.is_empty());
    }

    #[test]
    fn strip_links_keeps_brackets_that_are_not_links() {
        assert_eq!(
            strip_links("see [Fig 1](#f1) [95% CI]"),
            "see Fig 1 [95% CI]"
        );
    }
}
