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

//! Whether a canonical text looks like a whole paper rather than its abstract.
//!
//! The port of Python's `bmlib.fulltext.canonical.assess`. A publisher's landing
//! page converts as cleanly as its full-text page, so a converter cannot tell
//! the two apart; this check reads the result, from any format. It is a
//! heuristic and **unmeasured**: [`MIN_BODY_WORDS`] and the heading rule are a
//! starting point without a corpus behind them, and a case report or letter
//! with no IMRaD headings fails it.
//!
//! Each heading is classified by the PDF segmenter's pattern table
//! ([`match_section_type`]). A section under an *Abstract* or *References*
//! heading (at a deeper level, up to the next section at that heading's level
//! or above) belongs to it, so a structured abstract's own *Methods* and
//! *Results* are not the paper's.

use serde::Serialize;

use crate::fulltext::canonical::model::{CanonicalError, CanonicalText};
use crate::fulltext::models::SectionType;
use crate::fulltext::segmenter::match_section_type;

/// The fewest words a paper's body (its blocks outside the abstract and the
/// references) must hold. Unmeasured: a structured abstract runs to some 400
/// words and a research letter to about 1,000; a longer abstract page or a
/// shorter paper is misjudged.
pub const MIN_BODY_WORDS: usize = 1000;

/// Headings whose sections, and those nested under them, are not the body.
const NOT_BODY: [SectionType; 2] = [SectionType::Abstract, SectionType::References];

/// The section that must be found, and the ones of which one must be too.
const REQUIRED: SectionType = SectionType::Methods;
const ONE_OF: [SectionType; 2] = [SectionType::Results, SectionType::Discussion];

/// What [`assess_full_text`] found, and its verdict.
///
/// `section_types` are the classified headings outside the abstract and the
/// references (those two themselves included), each once, in document order;
/// `body_words` counts the words of the blocks outside them. Serialises to the
/// shape the shared contract pins, as Python's `to_dict()`. Built only by
/// [`assess_full_text`] (`#[non_exhaustive]`), so `is_full_text` always agrees
/// with the other two fields.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[non_exhaustive]
pub struct FullTextAssessment {
    /// Whether the text looks like a whole paper.
    pub is_full_text: bool,
    /// The section types its headings were classified as.
    pub section_types: Vec<SectionType>,
    /// The words in the body's blocks.
    pub body_words: usize,
}

/// The number of runs of characters that are not Unicode `White_Space`.
#[must_use]
pub fn count_words(text: &str) -> usize {
    text.split(char::is_whitespace)
        .filter(|word| !word.is_empty())
        .count()
}

/// Judge whether `text` is a whole paper (the rule is in the module docs).
///
/// It is when a *Methods* heading and a *Results* or *Discussion* one lie
/// outside the abstract and the references, and the body holds at least
/// [`MIN_BODY_WORDS`].
///
/// # Errors
///
/// [`CanonicalError`] if `text` fails [`CanonicalText::validate`] (a text
/// deserialised from storage, say), where Python's `span_text` raises.
pub fn assess_full_text(text: &CanonicalText) -> Result<FullTextAssessment, CanonicalError> {
    text.validate()?;
    let span_text = |span| text.span_text(span).expect("a validated text's own span");
    let mut found: Vec<SectionType> = Vec::new();
    let mut body_words = 0;
    let mut excluded_level: Option<usize> = None;
    for section in &text.sections {
        if excluded_level.is_some_and(|level| section.level > level) {
            continue;
        }
        excluded_level = None;
        let kind = section.heading_span.map_or(SectionType::Unknown, |span| {
            match_section_type(span_text(span)).0
        });
        if kind != SectionType::Unknown && !found.contains(&kind) {
            found.push(kind);
        }
        if NOT_BODY.contains(&kind) {
            excluded_level = Some(section.level);
            continue;
        }
        body_words += section
            .blocks
            .iter()
            .map(|block| count_words(span_text(block.span)))
            .sum::<usize>();
    }
    let is_full_text = found.contains(&REQUIRED)
        && found.iter().any(|kind| ONE_OF.contains(kind))
        && body_words >= MIN_BODY_WORDS;
    Ok(FullTextAssessment {
        is_full_text,
        section_types: found,
        body_words,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fulltext::canonical::markdown::canonical_from_markdown;

    #[test]
    fn an_information_separator_is_not_white_space() {
        assert_eq!(count_words(" a  b\tc\n"), 3);
        assert_eq!(count_words("a\u{1c}b"), 1);
    }

    #[test]
    fn a_structured_abstracts_headings_are_the_abstracts() {
        let body = vec!["word"; MIN_BODY_WORDS].join(" ");
        let text = canonical_from_markdown(&format!(
            "## Abstract\n\n### Methods\n\n{body}\n\n### Results\n\n## Introduction\n\ny"
        ));
        let result = assess_full_text(&text).expect("a valid text");
        assert_eq!(
            result.section_types,
            vec![SectionType::Abstract, SectionType::Introduction]
        );
        assert_eq!((result.is_full_text, result.body_words), (false, 1));
    }

    #[test]
    fn a_text_that_fails_validation_is_an_error_not_a_panic() {
        let mut text = canonical_from_markdown("## Methods\n\nword");
        text.sections[0].blocks[0].span.end = 99;
        assert!(matches!(
            assess_full_text(&text),
            Err(CanonicalError::Outside { .. })
        ));
    }
}
