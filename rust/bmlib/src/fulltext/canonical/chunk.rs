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

//! Sentence-aware chunks of a canonical text, for retrieval by embedding.
//!
//! The port of Python's `bmlib.fulltext.canonical.chunk`; the shared contract
//! `tests/fixtures/canonical_chunks.json` holds the two together.
//! [`chunk_canonical`] splits each block of a [`CanonicalText`] into *units*
//! (sentences, or pieces of a sentence longer than a chunk) and packs a
//! section's units, in order, into chunks of at most `max_chars` code points.
//! A chunk is a [`TextSpan`] into the canonical text plus the index of the
//! section it lies in; it never crosses a section, and it may cross the
//! blocks of one. Consecutive chunks of a section share whole units, up to
//! `overlap_chars`.
//!
//! The sizes follow bmlibrarian's `embeddings/chunk_embedder.py` (1,000 and
//! 100), and the sentence rule its `adaptive_chunker_optimized.py`: a `.`,
//! `!` or `?` followed by whitespace ends a sentence unless the token before
//! it is an abbreviation ([`ABBREVIATIONS`]). Unlike bmlibrarian, `max_chars`
//! is a hard cap: a longer unit is cut at whitespace, or at the cap where it
//! has none. Nothing is dropped: every non-whitespace code point of every
//! block lies in some chunk.

use std::fmt;

use serde::{Deserialize, Serialize};

use super::model::{CanonicalError, CanonicalText, TextSpan};

/// Bumped whenever a change can move the chunks of any canonical text, so a
/// stored chunk (and its embedding) can tell it is stale. Must equal Python's
/// `CHUNKER_VERSION`; the shared contract checks both.
pub const CHUNKER_VERSION: u32 = 1;

/// The longest chunk, in code points (bmlibrarian's `DEFAULT_CHUNK_SIZE`).
pub const DEFAULT_MAX_CHARS: usize = 1000;

/// The most two consecutive chunks of a section share, in code points
/// (bmlibrarian's `DEFAULT_CHUNK_OVERLAP`).
pub const DEFAULT_OVERLAP_CHARS: usize = 100;

/// The marks that end a sentence when whitespace follows.
pub const SENTENCE_ENDINGS: [char; 3] = ['.', '!', '?'];

/// Tokens that a full stop does not end a sentence after, compared lower-case.
/// bmlibrarian's fourteen, plus `figs`, `e.g`, `i.e`, `cf` and `approx`.
pub const ABBREVIATIONS: [&str; 19] = [
    "dr", "mr", "ms", "mrs", "fig", "tab", "vol", "no", "pp", "al", "jr", "sr", "vs", "ca", "figs",
    "e.g", "i.e", "cf", "approx",
];

/// Characters stripped from the front of a token before it is compared with
/// [`ABBREVIATIONS`], so `(Fig. 2)` reads `fig`.
pub const OPENING_MARKS: [char; 7] = ['(', '[', '{', '"', '\'', '“', '‘'];

/// One chunk: the index of the section it lies in, and its span.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Chunk {
    /// Index into [`CanonicalText::sections`].
    pub section_index: usize,
    /// Where the chunk lies in the canonical text, in code points.
    pub span: TextSpan,
}

/// Why [`chunk_canonical`] refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChunkError {
    /// `max_chars` below 1, or `overlap_chars` not below `max_chars`.
    Sizes {
        /// The cap asked for.
        max_chars: usize,
        /// The overlap asked for.
        overlap_chars: usize,
    },
    /// The canonical text fails [`CanonicalText::validate`].
    Canonical(CanonicalError),
}

impl fmt::Display for ChunkError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ChunkError::Sizes {
                max_chars,
                overlap_chars,
            } => write!(
                f,
                "need max_chars >= 1 and 0 <= overlap_chars < max_chars, got {max_chars} and \
                 {overlap_chars}"
            ),
            ChunkError::Canonical(error) => write!(f, "{error}"),
        }
    }
}

impl std::error::Error for ChunkError {}

/// Split `canonical` into chunks, section by section, in document order.
///
/// Each chunk's span starts and ends on non-whitespace, is at most
/// `max_chars` code points long, and lies within one section's blocks. The
/// title and the headings are not chunked; a section's heading is reached by
/// its index.
///
/// # Errors
///
/// [`ChunkError::Sizes`] if `max_chars` is 0 or `overlap_chars` is not below
/// it; [`ChunkError::Canonical`] if `canonical` fails validation.
pub fn chunk_canonical(
    canonical: &CanonicalText,
    max_chars: usize,
    overlap_chars: usize,
) -> Result<Vec<Chunk>, ChunkError> {
    if max_chars < 1 || overlap_chars >= max_chars {
        return Err(ChunkError::Sizes {
            max_chars,
            overlap_chars,
        });
    }
    canonical.validate().map_err(ChunkError::Canonical)?;
    let text: Vec<char> = canonical.text.chars().collect();
    let mut chunks = Vec::new();
    for (section_index, section) in canonical.sections.iter().enumerate() {
        let units: Vec<TextSpan> = section
            .blocks
            .iter()
            .flat_map(|block| sentence_spans(&text, block.span))
            .flat_map(|unit| cut(&text, unit, max_chars))
            .collect();
        chunks.extend(
            pack(&units, max_chars, overlap_chars)
                .into_iter()
                .map(|span| Chunk {
                    section_index,
                    span,
                }),
        );
    }
    Ok(chunks)
}

/// The sentences of `text[span.start..span.end]`, as spans into `text` (a
/// text's code points).
///
/// Each starts and ends on non-whitespace. A sentence ends at a mark of
/// [`SENTENCE_ENDINGS`] followed by whitespace, unless the token before the
/// mark is an abbreviation; the span's end always ends one. A span of
/// whitespace alone has none. The span must lie within `text`.
#[must_use]
pub fn sentence_spans(text: &[char], span: TextSpan) -> Vec<TextSpan> {
    let mut out = Vec::new();
    let first = skip_white_space(text, span.start, span.end);
    let mut start = first;
    for i in first..span.end.saturating_sub(1) {
        if SENTENCE_ENDINGS.contains(&text[i])
            && text[i + 1].is_whitespace()
            && !is_abbreviation(text, start, i)
        {
            out.push(TextSpan { start, end: i + 1 });
            start = skip_white_space(text, i + 1, span.end);
        }
    }
    let end = trim_white_space(text, start, span.end);
    if start < end {
        out.push(TextSpan { start, end });
    }
    out
}

/// Whether the token before `text[mark]`, not reaching back past `start`, is
/// one of [`ABBREVIATIONS`].
///
/// The token is the run of non-whitespace before the mark, its leading
/// [`OPENING_MARKS`] stripped. Only an ASCII token can match:
/// `to_ascii_lowercase` leaves a non-ASCII character as it is (where Python's
/// `str.lower` maps the Kelvin sign to `k`, which its `isascii` guard refuses).
#[must_use]
pub fn is_abbreviation(text: &[char], start: usize, mark: usize) -> bool {
    let mut begin = mark;
    while begin > start && !text[begin - 1].is_whitespace() {
        begin -= 1;
    }
    while begin < mark && OPENING_MARKS.contains(&text[begin]) {
        begin += 1;
    }
    let token = &text[begin..mark];
    ABBREVIATIONS.contains(
        &token
            .iter()
            .map(char::to_ascii_lowercase)
            .collect::<String>()
            .as_str(),
    )
}

/// `unit` in pieces of at most `max_chars`, each cut at the last whitespace
/// that keeps it within the cap, or at the cap where the piece has none.
fn cut(text: &[char], unit: TextSpan, max_chars: usize) -> Vec<TextSpan> {
    let mut pieces = Vec::new();
    let mut start = unit.start;
    while unit.end - start > max_chars {
        let mut at = start + max_chars;
        while at > start && !text[at].is_whitespace() {
            at -= 1;
        }
        let end = if at > start {
            trim_white_space(text, start, at)
        } else {
            start + max_chars
        };
        pieces.push(TextSpan { start, end });
        start = skip_white_space(text, end, unit.end);
    }
    pieces.push(TextSpan {
        start,
        end: unit.end,
    });
    pieces
}

/// Pack `units` (each within the cap, in order) greedily into chunk spans.
///
/// After a chunk, the next restarts at the first of its units that begins
/// within `overlap_chars` of its end, unless there is none or those units and
/// the next new one would together pass `max_chars`.
fn pack(units: &[TextSpan], max_chars: usize, overlap_chars: usize) -> Vec<TextSpan> {
    let mut chunks = Vec::new();
    let mut first = 0;
    while first < units.len() {
        let mut last = first;
        while last + 1 < units.len() && units[last + 1].end - units[first].start <= max_chars {
            last += 1;
        }
        let end = units[last].end;
        chunks.push(TextSpan {
            start: units[first].start,
            end,
        });
        if last + 1 == units.len() {
            break;
        }
        let mut carried = (first + 1..=last)
            .find(|&k| units[k].start + overlap_chars >= end)
            .unwrap_or(last + 1);
        if units[last + 1].end - units[carried].start > max_chars {
            carried = last + 1;
        }
        first = carried;
    }
    chunks
}

/// The first index from `start` (before `end`) that is not whitespace, else `end`.
fn skip_white_space(text: &[char], mut start: usize, end: usize) -> usize {
    while start < end && text[start].is_whitespace() {
        start += 1;
    }
    start
}

/// `end` moved back over trailing whitespace, not past `start`.
fn trim_white_space(text: &[char], start: usize, mut end: usize) -> usize {
    while end > start && text[end - 1].is_whitespace() {
        end -= 1;
    }
    end
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fulltext::canonical::{assemble, BlockDraft, SectionDraft, SourceFormat};

    fn build(title: &str, sections: Vec<SectionDraft>) -> CanonicalText {
        assemble(title, &sections, SourceFormat::PlainText, "test-1")
    }

    fn paragraphs(heading: &str, texts: &[&str]) -> SectionDraft {
        SectionDraft::new(
            heading,
            1,
            texts.iter().map(|t| BlockDraft::paragraph(*t)).collect(),
        )
    }

    fn texts(doc: &CanonicalText, chunks: &[Chunk]) -> Vec<String> {
        chunks
            .iter()
            .map(|c| doc.span_text(c.span).expect("inside").to_string())
            .collect()
    }

    fn sentences(text: &str) -> Vec<String> {
        let chars: Vec<char> = text.chars().collect();
        sentence_spans(
            &chars,
            TextSpan {
                start: 0,
                end: chars.len(),
            },
        )
        .into_iter()
        .map(|s| chars[s.start..s.end].iter().collect())
        .collect()
    }

    #[test]
    fn a_mark_followed_by_whitespace_ends_a_sentence() {
        assert_eq!(
            sentences("One. Two! Three? Four"),
            ["One.", "Two!", "Three?", "Four"]
        );
        assert_eq!(
            sentences("p = 0.05 and 1.5-fold."),
            ["p = 0.05 and 1.5-fold."]
        );
    }

    #[test]
    fn an_abbreviation_does_not_end_a_sentence() {
        for text in [
            "As (Fig. 2) shows.",
            "Smith et al. found it.",
            "Drugs, e.g. aspirin, help.",
            "See FIG. 3 here.",
        ] {
            assert_eq!(sentences(text), [text]);
        }
    }

    #[test]
    fn whitespace_alone_has_no_sentence() {
        assert!(sentences(" \t ").is_empty());
        assert_eq!(sentences("\tA. B.\t"), ["A.", "B."]);
    }

    #[test]
    fn a_non_ascii_token_never_matches() {
        let kelvin: Vec<char> = "\u{212A}g.".chars().collect();
        assert!(!is_abbreviation(&kelvin, 0, 2));
        let fig: Vec<char> = "fig.".chars().collect();
        assert!(is_abbreviation(&fig, 0, 3));
        assert!(!is_abbreviation(&fig, 1, 3));
    }

    #[test]
    fn chunks_cross_blocks_but_not_sections() {
        let doc = build(
            "T",
            vec![
                paragraphs("H", &["A.", "B."]),
                SectionDraft::new("Empty", 1, vec![]),
                paragraphs("", &["C."]),
            ],
        );
        let chunks = chunk_canonical(&doc, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS).unwrap();
        assert_eq!(texts(&doc, &chunks), ["A.\n\nB.", "C."]);
        assert_eq!(
            chunks.iter().map(|c| c.section_index).collect::<Vec<_>>(),
            [0, 2]
        );
    }

    #[test]
    fn overlap_carries_whole_sentences_that_fit() {
        let doc = build("", vec![paragraphs("", &["Aaaa. Bbbb. Cccc. Dddd."])]);
        let chunks = chunk_canonical(&doc, 11, 5).unwrap();
        assert_eq!(
            texts(&doc, &chunks),
            ["Aaaa. Bbbb.", "Bbbb. Cccc.", "Cccc. Dddd."]
        );
        let doc = build("", vec![paragraphs("", &["Aaaa. Bbbb. Ccccccccc."])]);
        let chunks = chunk_canonical(&doc, 11, 5).unwrap();
        assert_eq!(texts(&doc, &chunks), ["Aaaa. Bbbb.", "Ccccccccc."]);
    }

    #[test]
    fn an_over_long_unit_is_cut_at_whitespace_or_the_cap() {
        let doc = build("", vec![paragraphs("", &["one two three four"])]);
        let chunks = chunk_canonical(&doc, 9, 0).unwrap();
        assert_eq!(texts(&doc, &chunks), ["one two", "three", "four"]);
        let doc = build("", vec![paragraphs("", &["abcdefgh ij"])]);
        let chunks = chunk_canonical(&doc, 3, 0).unwrap();
        assert_eq!(texts(&doc, &chunks), ["abc", "def", "gh", "ij"]);
    }

    #[test]
    fn spans_count_code_points() {
        let doc = build("", vec![paragraphs("", &["𝛽 é. Next."])]);
        let chunks = chunk_canonical(&doc, 5, 0).unwrap();
        assert_eq!(chunks[0].span, TextSpan { start: 0, end: 4 });
        assert_eq!(texts(&doc, &chunks), ["𝛽 é.", "Next."]);
    }

    #[test]
    fn bad_sizes_and_texts_are_refused() {
        let doc = build("", vec![paragraphs("", &["x"])]);
        for (max_chars, overlap_chars) in [(0, 0), (5, 5), (5, 6)] {
            assert_eq!(
                chunk_canonical(&doc, max_chars, overlap_chars),
                Err(ChunkError::Sizes {
                    max_chars,
                    overlap_chars
                })
            );
        }
        let mut broken = doc.clone();
        broken.text.push('y');
        assert!(matches!(
            chunk_canonical(&broken, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS),
            Err(ChunkError::Canonical(CanonicalError::RunsOn { .. }))
        ));
        assert!(ChunkError::Sizes {
            max_chars: 0,
            overlap_chars: 0
        }
        .to_string()
        .contains("max_chars"));
    }

    #[test]
    fn an_empty_text_has_no_chunks() {
        let doc = build("", vec![]);
        assert!(
            chunk_canonical(&doc, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS)
                .unwrap()
                .is_empty()
        );
    }
}
