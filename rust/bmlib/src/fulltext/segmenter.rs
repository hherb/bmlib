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

//! Heading-driven section segmentation of a PDF's text lines.
//!
//! Headings are detected by font size against the document's **median** — with
//! bold as the rescue for body-sized headings — and classified against an
//! anchored pattern table, with an unanchored word-bounded search as the
//! lower-confidence fallback.

use crate::fulltext::models::{Section, SectionType, SegmentedDocument, TextBlock};
use crate::fulltext::titles::accepted_metadata_title;
use regex::Regex;
use std::sync::OnceLock;

/// Confidence for sections that **contain rather than classify**: front matter,
/// and the whole-document fallback when no heading was detected.
///
/// If the first real heading was missed, the container has swallowed it.
pub const FALLBACK_CONFIDENCE: f64 = 0.5;

/// Confidence for a heading matched by an unanchored, word-bounded search rather
/// than the anchored pattern (*"Supplementary materials online"*).
pub const PARTIAL_MATCH_CONFIDENCE: f64 = 0.7;

/// Assumed body size when no block carries a positive font size.
pub const DEFAULT_FONT_SIZE: f64 = 12.0;

/// A heading is short; a line longer than this is prose whatever its font.
pub const MAX_HEADING_CHARS: usize = 100;

/// The title fallback must exceed the body median by this factor before the
/// largest first-page line is believed to be the title.
pub const TITLE_SIZE_RATIO: f64 = 1.5;

/// A vertical gap larger than this multiple of the line height separates
/// paragraphs; the leading **within** a paragraph is smaller.
pub const PARAGRAPH_GAP_RATIO: f64 = 1.5;

/// Page furniture (#591): a line whose text, digits aside, recurs at one height
/// on at least this share of the document's pages — and on at least
/// [`FURNITURE_MIN_PAGES`] of them — is a running head, a footer, a page number
/// or a licence stamp.
///
/// Measured over 231 PDFs (bioRxiv/medRxiv and Unpaywall journal PDFs): the
/// furniture found sits from 0.35 to 1.0 of the pages (an odd/even running head
/// at 0.43-0.46), content repeating at one height below 0.3 (list numbering,
/// "Figure 2", table cells, at most 0.27), and the threshold moves the lines
/// dropped by 2% between 0.3 and 0.6.
pub const FURNITURE_PAGE_RATIO: f64 = 0.4;

/// The fewest pages a line must recur on to be furniture, whatever the share.
pub const FURNITURE_MIN_PAGES: usize = 3;

/// How far apart, in points, two lines' tops may be and still be one height.
pub const FURNITURE_Y_TOLERANCE: f64 = 2.0;

/// A page number has at most this many digits.
const PAGE_NUMBER_MAX_DIGITS: usize = 6;

/// `text` with every run of ASCII digits replaced by `#`.
fn mask_digits(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_run = false;
    for ch in text.chars() {
        if ch.is_ascii_digit() {
            if !in_run {
                out.push('#');
            }
            in_run = true;
        } else {
            out.push(ch);
            in_run = false;
        }
    }
    out
}

/// A page number's value, if `text` is a bare number short enough to be one.
fn bare_number(text: &str) -> Option<i64> {
    if text.is_empty()
        || text.len() > PAGE_NUMBER_MAX_DIGITS
        || !text.bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    text.parse().ok()
}

/// The indexes of `blocks` that are page furniture.
///
/// A line is furniture when its text with every run of ASCII digits replaced by
/// `#` (so "Page 3 of 12" and "Page 4 of 12" agree) recurs on at least
/// `max(FURNITURE_MIN_PAGES, ceil(FURNITURE_PAGE_RATIO * pages))` pages at one
/// height: lines with the same masked text are taken in order of their `y`, a
/// run of them whose tops lie within [`FURNITURE_Y_TOLERANCE`] of the previous
/// one is one height, and every line in a run reaching that many pages is
/// furniture. The pages are `max(page_num) + 1`. A run of bare numbers is
/// furniture only if each number is its page's index plus one constant, which
/// is what a page number is and a manuscript's line numbers and a table's
/// values are not.
#[must_use]
pub fn page_furniture(blocks: &[TextBlock]) -> std::collections::BTreeSet<usize> {
    let mut furniture = std::collections::BTreeSet::new();
    let Some(last_page) = blocks.iter().map(|b| b.page_num).max() else {
        return furniture;
    };
    let pages = (last_page + 1) as f64;
    let needed = FURNITURE_MIN_PAGES.max((FURNITURE_PAGE_RATIO * pages).ceil() as usize);
    let mut by_text: std::collections::BTreeMap<String, Vec<usize>> =
        std::collections::BTreeMap::new();
    for (i, block) in blocks.iter().enumerate() {
        by_text.entry(mask_digits(&block.text)).or_default().push(i);
    }
    for mut members in by_text.into_values() {
        members.sort_by(|&a, &b| blocks[a].y.total_cmp(&blocks[b].y).then(a.cmp(&b)));
        let mut run: Vec<usize> = Vec::new();
        for i in members {
            if let Some(&previous) = run.last() {
                if blocks[i].y - blocks[previous].y > FURNITURE_Y_TOLERANCE {
                    furniture.extend(furniture_run(blocks, &run, needed));
                    run.clear();
                }
            }
            run.push(i);
        }
        furniture.extend(furniture_run(blocks, &run, needed));
    }
    furniture
}

/// `run` if it is furniture (see [`page_furniture`]), else nothing.
fn furniture_run(blocks: &[TextBlock], run: &[usize], needed: usize) -> Vec<usize> {
    let pages: std::collections::BTreeSet<i64> = run.iter().map(|&i| blocks[i].page_num).collect();
    if pages.len() < needed {
        return Vec::new();
    }
    let numbers: Vec<Option<i64>> = run.iter().map(|&i| bare_number(&blocks[i].text)).collect();
    if numbers.iter().any(Option::is_some) {
        let offsets: std::collections::BTreeSet<Option<i64>> = run
            .iter()
            .zip(&numbers)
            .map(|(&i, n)| n.map(|n| n - blocks[i].page_num))
            .collect();
        // A non-number in the run is a second offset (`None`) beside the numbers'.
        if offsets.len() != 1 {
            return Vec::new();
        }
    }
    run.to_vec()
}

/// `blocks` without their [`page_furniture`], in order.
#[must_use]
pub fn drop_page_furniture(blocks: &[TextBlock]) -> Vec<TextBlock> {
    let furniture = page_furniture(blocks);
    blocks
        .iter()
        .enumerate()
        .filter(|(i, _)| !furniture.contains(i))
        .map(|(_, b)| b.clone())
        .collect()
}

/// Median font size of `blocks`, ignoring non-positive sizes.
///
/// The **median, not the mean**, so headings and footnotes cannot drag the
/// body-text estimate. Returns [`DEFAULT_FONT_SIZE`] when no block carries a
/// usable size.
#[must_use]
pub fn median_font_size(blocks: &[TextBlock]) -> f64 {
    let mut sizes: Vec<f64> = blocks
        .iter()
        .filter(|b| b.font_size > 0.0)
        .map(|b| b.font_size)
        .collect();
    if sizes.is_empty() {
        return DEFAULT_FONT_SIZE;
    }
    sizes.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = sizes.len() / 2;
    if sizes.len() % 2 == 1 {
        sizes[mid]
    } else {
        (sizes[mid - 1] + sizes[mid]) / 2.0
    }
}

/// Join lines, inserting a blank line at each paragraph-sized gap.
///
/// A vertical gap larger than 1.5× the line's height is a paragraph boundary —
/// the leading within a paragraph is smaller. A column or page boundary sends the
/// gap **negative**, so no break is inserted there: a paragraph continuing across
/// the boundary stays one paragraph, and a PDF gives no signal that would
/// distinguish it from one that ends at it.
#[must_use]
pub fn join_blocks(blocks: &[TextBlock]) -> String {
    let mut lines: Vec<&str> = Vec::with_capacity(blocks.len());
    let mut previous_bottom: Option<f64> = None;
    for block in blocks {
        // A degenerate bbox (`height == 0`) makes the threshold 0, so any
        // positive gap ahead of this block reads as a paragraph break. Left
        // as-is: a degenerate bbox gives no usable leading to derive a real
        // threshold from, and guessing a floor would be an assumed size — against
        // this module's measured-not-assumed rule.
        let gap_threshold = block.height * PARAGRAPH_GAP_RATIO;
        if let Some(bottom) = previous_bottom {
            if block.y - bottom > gap_threshold {
                lines.push("");
            }
        }
        lines.push(&block.text);
        previous_bottom = Some(block.y + block.height);
    }
    lines.join("\n")
}

/// The heading pattern table, as `(section type, patterns)`.
///
/// Each pattern is anchored (`^…$`); the partial search strips the anchors and
/// adds word boundaries. Both are compiled once and shared.
pub const SECTION_PATTERNS: &[(SectionType, &[&str])] = &[
    (SectionType::Abstract, &["^abstract$", "^summary$"]),
    (
        SectionType::Introduction,
        &["^introduction$", "^background\\s+and\\s+introduction$"],
    ),
    (
        SectionType::Background,
        &["^background$", "^literature\\s+review$"],
    ),
    (
        SectionType::Methods,
        &[
            "^methods$",
            "^methodology$",
            "^materials\\s+and\\s+methods$",
            "^methods\\s+and\\s+materials$",
            "^experimental\\s+procedures?$",
            "^experimental\\s+methods$",
        ],
    ),
    (
        SectionType::Results,
        &["^results$", "^findings$", "^results\\s+and\\s+discussion$"],
    ),
    (
        SectionType::Discussion,
        &["^discussion$", "^discussion\\s+and\\s+conclusion$"],
    ),
    (
        SectionType::Conclusion,
        &[
            "^conclusion$",
            "^conclusions$",
            "^concluding\\s+remarks$",
            "^summary\\s+and\\s+conclusions?$",
        ],
    ),
    (
        SectionType::Acknowledgments,
        &["^acknowledgments?$", "^acknowledgements?$"],
    ),
    (
        SectionType::References,
        &[
            "^references$",
            "^bibliography$",
            "^literature\\s+cited$",
            "^works\\s+cited$",
        ],
    ),
    (
        SectionType::Supplementary,
        &[
            "^supplementary\\s+materials?$",
            "^supplementary\\s+information$",
            "^supporting\\s+information$",
        ],
    ),
    (SectionType::Appendix, &["^appendix$", "^appendices$"]),
    (
        SectionType::Funding,
        &[
            "^funding$",
            "^funding\\s+sources?$",
            "^financial\\s+support$",
            "^grant\\s+support$",
            "^funding\\s+and\\s+acknowledgments?$",
            "^funding\\s+and\\s+acknowledgements?$",
            "^funding\\s+information$",
            "^funding\\s+statement$",
            "^source\\s+of\\s+funding$",
            "^sources?\\s+of\\s+support$",
        ],
    ),
    (
        SectionType::Conflicts,
        &[
            "^conflicts?\\s+of\\s+interest$",
            "^competing\\s+interests?$",
            "^disclosures?$",
            "^declaration\\s+of\\s+interests?$",
            "^financial\\s+disclosures?$",
            "^conflict\\s+of\\s+interest\\s+statement$",
            "^declaration\\s+of\\s+competing\\s+interests?$",
            "^potential\\s+conflicts?\\s+of\\s+interest$",
        ],
    ),
    (
        SectionType::DataAvailability,
        &[
            "^data\\s+availability$",
            "^data\\s+sharing$",
            "^data\\s+access$",
            "^availability\\s+of\\s+data$",
            "^data\\s+availability\\s+statement$",
            "^data\\s+and\\s+materials?\\s+availability$",
            "^code\\s+and\\s+data\\s+availability$",
        ],
    ),
    (
        SectionType::AuthorContributions,
        &[
            "^authors?['\u{2019}]?s?\\s+contributions?$",
            "^contributors?$",
            "^credit\\s+authorship$",
            "^authorship\\s+contributions?$",
        ],
    ),
];

/// The leading numbering a heading may carry: `"1.2  Introduction"`.
static LEADING_NUMBERING: OnceLock<Regex> = OnceLock::new();
/// The punctuation a heading may trail: `"Discussion:"`.
static TRAILING_PUNCTUATION: OnceLock<Regex> = OnceLock::new();
/// The compiled tables: anchored patterns, then the partial-search ones.
type CompiledTables = (
    Vec<(SectionType, Vec<Regex>)>,
    Vec<(SectionType, Vec<Regex>)>,
);

/// Built once, on first use.
static COMPILED: OnceLock<CompiledTables> = OnceLock::new();

fn leading_numbering() -> &'static Regex {
    LEADING_NUMBERING.get_or_init(|| Regex::new(r"^[\d.\s)\]]+").expect("a fixed pattern"))
}

fn trailing_punctuation() -> &'static Regex {
    TRAILING_PUNCTUATION.get_or_init(|| Regex::new(r"[:.?!]+$").expect("a fixed pattern"))
}

fn compiled() -> &'static CompiledTables {
    COMPILED.get_or_init(|| {
        let mut exact = Vec::new();
        let mut partial = Vec::new();
        for (section_type, patterns) in SECTION_PATTERNS {
            let mut exact_set = Vec::new();
            let mut partial_set = Vec::new();
            for pattern in *patterns {
                exact_set.push(Regex::new(&format!("(?i){pattern}")).expect("a fixed pattern"));
                // The Python strips the anchors and wraps the rest in `\b(?:…)\b`.
                let body = pattern.trim_start_matches('^').trim_end_matches('$');
                partial_set
                    .push(Regex::new(&format!(r"(?i)\b(?:{body})\b")).expect("a fixed pattern"));
            }
            exact.push((*section_type, exact_set));
            partial.push((*section_type, partial_set));
        }
        (exact, partial)
    })
}

/// Classify a heading, returning `(section type, confidence)`.
///
/// Anchored matches win at `1.0`; an unanchored word-bounded search is the `0.7`
/// fallback; `(SectionType::Unknown, 0.0)` means no pattern claimed it.
#[must_use]
pub fn match_section_type(text: &str) -> (SectionType, f64) {
    let normalized = text.trim().to_lowercase();
    let normalized = leading_numbering().replace(&normalized, "");
    let normalized = trailing_punctuation().replace(&normalized, "");

    let (exact, partial) = compiled();
    for (section_type, patterns) in exact {
        for pattern in patterns {
            if pattern.is_match(&normalized) {
                return (*section_type, 1.0);
            }
        }
    }
    for (section_type, patterns) in partial {
        for pattern in patterns {
            if pattern.is_match(&normalized) {
                return (*section_type, PARTIAL_MATCH_CONFIDENCE);
            }
        }
    }
    (SectionType::Unknown, 0.0)
}

/// Whether `block` looks like a section heading.
///
/// Heading-sized (or body-sized but **bold**), short, and carrying at least one
/// alphabetic character — a bare `"3."` is numbering, not a heading.
#[must_use]
pub fn is_potential_header(
    block: &TextBlock,
    median: f64,
    font_size_threshold: f64,
    min_heading_size: f64,
) -> bool {
    if block.font_size < min_heading_size {
        return false;
    }
    if block.font_size < median * font_size_threshold && !block.is_bold {
        return false;
    }
    // Characters, not bytes: a multi-byte heading is not "long" because its
    // UTF-8 encoding is.
    if block.text.chars().count() > MAX_HEADING_CHARS {
        return false;
    }
    if !block.text.chars().any(char::is_alphabetic) {
        return false;
    }
    true
}

/// One heading found in the block list.
#[derive(Debug, Clone, PartialEq)]
pub struct Marker {
    /// The block index the heading sits at.
    pub index: usize,
    /// What it was classified as.
    pub section_type: SectionType,
    /// The heading's own text.
    pub title: String,
    /// How sure the classification is.
    pub confidence: f64,
}

/// Find the heading blocks, in order.
#[must_use]
pub fn identify_section_markers(
    blocks: &[TextBlock],
    median: f64,
    font_size_threshold: f64,
    min_heading_size: f64,
) -> Vec<Marker> {
    let mut markers = Vec::new();
    for (index, block) in blocks.iter().enumerate() {
        if !is_potential_header(block, median, font_size_threshold, min_heading_size) {
            continue;
        }
        let (section_type, confidence) = match_section_type(&block.text);
        if section_type != SectionType::Unknown {
            markers.push(Marker {
                index,
                section_type,
                title: block.text.clone(),
                confidence,
            });
        }
    }
    markers
}

/// Slice `blocks` into sections at the marker boundaries.
#[must_use]
pub fn extract_sections(blocks: &[TextBlock], markers: &[Marker]) -> Vec<Section> {
    if blocks.is_empty() {
        return Vec::new();
    }

    if markers.is_empty() {
        return vec![Section {
            section_type: SectionType::Unknown,
            title: "Full Text".to_string(),
            content: join_blocks(blocks),
            page_start: blocks[0].page_num,
            page_end: blocks[blocks.len() - 1].page_num,
            confidence: FALLBACK_CONFIDENCE,
            subsections: Vec::new(),
        }];
    }

    let mut sections: Vec<Section> = Vec::new();

    let front_blocks = &blocks[..markers[0].index];
    if !front_blocks.is_empty() {
        sections.push(Section {
            section_type: SectionType::FrontMatter,
            title: "Front Matter".to_string(),
            content: join_blocks(front_blocks),
            page_start: front_blocks[0].page_num,
            page_end: front_blocks[front_blocks.len() - 1].page_num,
            confidence: FALLBACK_CONFIDENCE,
            subsections: Vec::new(),
        });
    }

    for (position, marker) in markers.iter().enumerate() {
        let end_index = markers
            .get(position + 1)
            .map_or(blocks.len(), |next| next.index);
        let section_blocks = &blocks[marker.index + 1..end_index];
        let heading = &blocks[marker.index];
        // A section whose heading is the last block carries **no** content, and
        // its pages are the heading's own rather than nothing.
        sections.push(Section {
            section_type: marker.section_type,
            title: marker.title.clone(),
            content: join_blocks(section_blocks),
            page_start: section_blocks
                .first()
                .map_or(heading.page_num, |b| b.page_num),
            page_end: section_blocks
                .last()
                .map_or(heading.page_num, |b| b.page_num),
            confidence: marker.confidence,
            subsections: Vec::new(),
        });
    }
    sections
}

/// Document title from corroborated metadata, else the largest first-page line.
///
/// The metadata title is believed **only where page 1 prints it**. Real PDFs
/// carry filenames, `"untitled"` and typesetters' job numbers in `/Title`, and
/// such a value used to beat a perfectly good large-font line; a title the
/// document itself never states is not the document's title.
///
/// The fallback is believed only when it exceeds the body median **by half
/// again** — otherwise an ordinary line would become the title of every PDF whose
/// metadata is blank.
#[must_use]
pub fn extract_title(
    blocks: &[TextBlock],
    metadata_title: Option<&str>,
    median: f64,
) -> Option<String> {
    let first_page: Vec<&TextBlock> = blocks.iter().filter(|b| b.page_num == 0).collect();
    let page_text = first_page
        .iter()
        .map(|b| b.text.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    if let Some(title) = accepted_metadata_title(metadata_title, Some(&page_text)) {
        return Some(title);
    }
    if first_page.is_empty() {
        return None;
    }
    // `max` keeps the **first** of equal sizes, which is what Python's does.
    let candidate = first_page.iter().fold(first_page[0], |best, block| {
        if block.font_size > best.font_size {
            block
        } else {
            best
        }
    });
    if candidate.font_size > median * TITLE_SIZE_RATIO {
        return Some(candidate.text.clone());
    }
    None
}

/// Segment text lines into standard sections.
#[derive(Debug, Clone, PartialEq)]
pub struct SectionSegmenter {
    /// Multiplier over the median above which a line is heading-sized without
    /// being bold.
    pub font_size_threshold: f64,
    /// Absolute font-size floor for headings.
    pub min_heading_size: f64,
}

impl Default for SectionSegmenter {
    fn default() -> Self {
        SectionSegmenter {
            font_size_threshold: 1.2,
            min_heading_size: 10.0,
        }
    }
}

impl SectionSegmenter {
    /// A segmenter with the source's thresholds.
    #[must_use]
    pub fn new(font_size_threshold: f64, min_heading_size: f64) -> Self {
        SectionSegmenter {
            font_size_threshold,
            min_heading_size,
        }
    }

    /// Segment `blocks` into a [`SegmentedDocument`].
    ///
    /// With no blocks, a document with no sections; with blocks but no detected
    /// headings, **one `UNKNOWN` section titled `"Full Text"`** at 0.5
    /// confidence. Page furniture ([`page_furniture`]) is set aside first, so a
    /// running head or a page number is in no section and a paragraph a page
    /// break interrupted reads on.
    #[must_use]
    pub fn segment_document(
        &self,
        blocks: &[TextBlock],
        file_path: &str,
        metadata_title: Option<&str>,
        metadata: serde_json::Value,
    ) -> SegmentedDocument {
        let kept = drop_page_furniture(blocks);
        let blocks = kept.as_slice();
        let median = median_font_size(blocks);
        let markers = identify_section_markers(
            blocks,
            median,
            self.font_size_threshold,
            self.min_heading_size,
        );
        SegmentedDocument {
            file_path: file_path.to_string(),
            title: extract_title(blocks, metadata_title, median),
            authors: Vec::new(),
            sections: extract_sections(blocks, &markers),
            metadata,
        }
    }
}
