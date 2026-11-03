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
//! bold or italic as the rescue for body-sized headings — and classified against an
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

/// A line naming a section exactly is a heading when it is larger than the
/// body at all, or bold or italic down to this fraction of the body size
/// (#628): Cell sets 8.97 pt headings over an 8 pt body, others bold 9.5 pt
/// under 10 pt.
pub const EXACT_HEADING_FLOOR_RATIO: f64 = 0.9;

/// Font sizes are compared with this much slack, so equal sizes compare equal.
pub const FONT_SIZE_TOLERANCE: f64 = 1e-6;

/// The title fallback must exceed the body median by this factor before the
/// largest first-page line is believed to be the title.
pub const TITLE_SIZE_RATIO: f64 = 1.5;

/// A vertical gap larger than this multiple of the line height separates
/// paragraphs; the leading **within** a paragraph is smaller.
pub const PARAGRAPH_GAP_RATIO: f64 = 1.5;

/// A line set in from the line above by at least this many ems (its own font
/// size), and at most [`PARAGRAPH_INDENT_MAX_EM`], starts a paragraph when the
/// line after it returns to the left edge: a first-line indent, the only
/// paragraph signal in a journal set without space between paragraphs.
pub const PARAGRAPH_INDENT_MIN_EM: f64 = 0.5;

/// Further in than this is another column or a centred line, not an indent.
pub const PARAGRAPH_INDENT_MAX_EM: f64 = 4.0;

/// How far, in ems, two lines' left edges may differ and still be one edge.
pub const LEFT_EDGE_TOLERANCE_EM: f64 = 0.25;

/// The most lines one heading may wrap over (`"3.2. Metformin modulated ..."` /
/// `"function in mice ..."` / `"mellitus"`).
pub const MAX_HEADING_LINES: usize = 3;

/// Page furniture (#591): a line whose text, digits aside, recurs at one height
/// on at least this share of the document's pages — and on at least
/// [`FURNITURE_MIN_PAGES`] of them — is a running head, a footer, a page number
/// or a licence stamp, if it also sits at its page's top or bottom edge (see
/// [`page_furniture`]).
///
/// Measured over 231 PDFs (bioRxiv/medRxiv and Unpaywall journal PDFs): the
/// furniture found recurs on 0.35 to 1.0 of the pages (an odd/even running head
/// at 0.43-0.46; below 0.4 it is kept, e.g. bioRxiv's stamp on a document's
/// landscape pages), content repeating at one height on at most 0.27 (list
/// numbering, "Figure 2", table cells), and the threshold moves the lines set
/// aside by 2% between 0.3 and 0.6.
pub const FURNITURE_PAGE_RATIO: f64 = 0.4;

/// The fewest pages a line must recur on to be furniture, whatever the share.
pub const FURNITURE_MIN_PAGES: usize = 3;

/// How far apart, in points, two lines' tops may be and still be one height.
pub const FURNITURE_Y_TOLERANCE: f64 = 2.0;

/// More furniture lines than this at one edge of one page are the page's own
/// content (a table filling it), not furniture: the corpus's longest edge run of
/// furniture is 13 lines, bioRxiv's stamp.
pub const FURNITURE_MAX_EDGE_LINES: usize = 20;

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
/// A line is a candidate when its text with every run of ASCII digits replaced
/// by `#` (so "Page 3 of 12" and "Page 4 of 12" agree) recurs on at least
/// `max(FURNITURE_MIN_PAGES, ceil(FURNITURE_PAGE_RATIO * pages))` pages at one
/// height: lines with the same masked text are taken in order of their `y`, a
/// run of them whose tops lie within [`FURNITURE_Y_TOLERANCE`] of the previous
/// one is one height, and every line in a run reaching that many pages is a
/// candidate. The pages are `max(page_num) + 1`. A run of bare numbers is a
/// candidate only if each number is its page's index plus one constant, which
/// is what a page number is and a manuscript's line numbers and a table's
/// values are not. A run can drift: each top is compared with the previous one,
/// not with the first.
///
/// A candidate is furniture only at its page's edge: the page's lines in order
/// of `y`, the candidates from the top down to the first other line and from
/// the bottom up to the last, so a table whose cells repeat at one height
/// across pages ("0.12" and "3.4" both read `#.#`) is kept wherever it has a
/// line above and below it. More than [`FURNITURE_MAX_EDGE_LINES`] at one edge
/// are the page's content and are kept. A line whose `y` is not finite is never
/// furniture and is passed over.
#[must_use]
pub fn page_furniture(blocks: &[TextBlock]) -> std::collections::BTreeSet<usize> {
    use std::collections::{BTreeMap, BTreeSet};
    let lines: Vec<usize> = (0..blocks.len())
        .filter(|&i| blocks[i].y.is_finite())
        .collect();
    let mut furniture = BTreeSet::new();
    let Some(last_page) = lines.iter().map(|&i| blocks[i].page_num).max() else {
        return furniture;
    };
    let pages = last_page as f64 + 1.0;
    let needed = FURNITURE_MIN_PAGES.max((FURNITURE_PAGE_RATIO * pages).ceil() as usize);
    // Finite `y` only, so `partial_cmp` is total and -0.0 ties with 0.0, as in
    // Python's sort.
    let by_y = |&a: &usize, &b: &usize| {
        blocks[a]
            .y
            .partial_cmp(&blocks[b].y)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.cmp(&b))
    };
    let mut by_text: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    let mut by_page: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    for &i in &lines {
        by_text
            .entry(mask_digits(&blocks[i].text))
            .or_default()
            .push(i);
        by_page.entry(blocks[i].page_num).or_default().push(i);
    }
    let mut candidates = BTreeSet::new();
    for mut members in by_text.into_values() {
        members.sort_by(by_y);
        let mut run: Vec<usize> = Vec::new();
        for i in members {
            if let Some(&previous) = run.last() {
                if blocks[i].y - blocks[previous].y > FURNITURE_Y_TOLERANCE {
                    candidates.extend(furniture_run(blocks, &run, needed));
                    run.clear();
                }
            }
            run.push(i);
        }
        candidates.extend(furniture_run(blocks, &run, needed));
    }
    for mut on_page in by_page.into_values() {
        on_page.sort_by(by_y);
        let top: Vec<usize> = on_page
            .iter()
            .copied()
            .take_while(|i| candidates.contains(i))
            .collect();
        let bottom: Vec<usize> = on_page
            .iter()
            .rev()
            .copied()
            .take_while(|i| candidates.contains(i))
            .collect();
        for edge in [top, bottom] {
            if edge.len() <= FURNITURE_MAX_EDGE_LINES {
                furniture.extend(edge);
            }
        }
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
        let offsets: std::collections::BTreeSet<Option<i128>> = run
            .iter()
            .zip(&numbers)
            // Widened, so no page number can overflow the subtraction.
            .map(|(&i, n)| n.map(|n| i128::from(n) - i128::from(blocks[i].page_num)))
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

/// Median font size of the characters of `blocks`.
///
/// The **median, not the mean**, so headings and footnotes cannot drag the
/// body-text estimate. Each line counts by its characters (code points), not
/// once: a table set small, one short cell per line, can outnumber the body's
/// lines while holding a fraction of its text, and a line median then reads
/// the table's size as the body's, so every body line looks heading-sized.
/// Where the characters split exactly in half, the two sizes either side are
/// averaged, so lines of one length give the plain median. A line with no text
/// or a non-positive size does not count; returns [`DEFAULT_FONT_SIZE`] when no
/// block carries both a usable size and some text.
#[must_use]
pub fn median_font_size(blocks: &[TextBlock]) -> f64 {
    let mut weighted: Vec<(f64, usize)> = blocks
        .iter()
        .filter(|b| b.font_size > 0.0 && !b.text.is_empty())
        .map(|b| (b.font_size, b.text.chars().count()))
        .collect();
    weighted.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let total: usize = weighted.iter().map(|(_, w)| w).sum();
    let mut seen = 0;
    for (i, (size, weight)) in weighted.iter().enumerate() {
        seen += weight;
        if 2 * seen > total {
            return *size;
        }
        if 2 * seen == total {
            return (size + weighted[i + 1].0) / 2.0;
        }
    }
    DEFAULT_FONT_SIZE
}

/// Whether `blocks[i]` is a paragraph's indented first line.
///
/// It sits below the line before it, set in from that line's left edge by
/// [`PARAGRAPH_INDENT_MIN_EM`] to [`PARAGRAPH_INDENT_MAX_EM`] ems; the line before
/// it ends short of its own right edge, as a paragraph's last line does; and the
/// line after it, if any, returns to the left edge. The last two keep a
/// reference list's hanging indent from reading as paragraphs: a reference's
/// wrapped first line runs to the margin, and its further wrapped lines are all
/// set in.
fn starts_indented(blocks: &[TextBlock], i: usize) -> bool {
    if i == 0 {
        return false;
    }
    let (previous, block) = (&blocks[i - 1], &blocks[i]);
    let em = block.font_size;
    let indent = block.x - previous.x;
    if block.y <= previous.y
        || !(PARAGRAPH_INDENT_MIN_EM * em <= indent && indent <= PARAGRAPH_INDENT_MAX_EM * em)
    {
        return false;
    }
    let tolerance = LEFT_EDGE_TOLERANCE_EM * em;
    if previous.x + previous.width >= block.x + block.width - tolerance {
        return false;
    }
    blocks
        .get(i + 1)
        .is_none_or(|next| (next.x - previous.x).abs() <= tolerance)
}

/// Join lines, inserting a blank line at each paragraph boundary.
///
/// A vertical gap larger than 1.5x the line's height is a paragraph boundary —
/// the leading within a paragraph is smaller. So is a first-line indent
/// (`starts_indented`), which journals set without space between paragraphs
/// use instead. A column or page boundary sends the gap **negative**, so no
/// break is inserted there: a paragraph continuing across the boundary stays one
/// paragraph, and a PDF gives no signal that would distinguish it from one that
/// ends at it.
#[must_use]
pub fn join_blocks(blocks: &[TextBlock]) -> String {
    let mut lines: Vec<&str> = Vec::with_capacity(blocks.len());
    let mut previous_bottom: Option<f64> = None;
    for (i, block) in blocks.iter().enumerate() {
        // A degenerate bbox (`height == 0`) makes the threshold 0, so any
        // positive gap ahead of this block reads as a paragraph break. Left
        // as-is: a degenerate bbox gives no usable leading to derive a real
        // threshold from, and guessing a floor would be an assumed size — against
        // this module's measured-not-assumed rule.
        let gap_threshold = block.height * PARAGRAPH_GAP_RATIO;
        let gap = previous_bottom.is_some_and(|bottom| block.y - bottom > gap_threshold);
        if gap || starts_indented(blocks, i) {
            lines.push("");
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
            // Elsevier's wording: "CRediT authorship contribution statement".
            "^credit\\s+authorship\\s+contributions?(?:\\s+statement)?$",
            "^authorship\\s+contributions?$",
        ],
    ),
];

/// The leading numbering a heading may carry: `"1.2  Introduction"`.
static LEADING_NUMBERING: OnceLock<Regex> = OnceLock::new();
/// A section number opening a heading: `"2."`, `"2.1."` or `"2.1.3"` and then a
/// letter. Each part has at most two digits, so a year (`"2025 Elsevier"`) is
/// not one, and the first does not start with 0, so a quantity (`"0.5 mM"`) is
/// not. Group 1 is the number, whose parts give the heading's depth; the letter
/// is Unicode category L, as Python's check of the character after the number.
static HEADING_NUMBER: OnceLock<Regex> = OnceLock::new();
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

/// The section number opening a heading (`"2.1"` for `"2.1. Animals"`), else
/// `None`.
#[must_use]
pub fn heading_number(text: &str) -> Option<&str> {
    let number = HEADING_NUMBER.get_or_init(|| {
        Regex::new(r"^([1-9][0-9]?(?:\.[0-9]{1,2})*)\.?\s+\p{L}").expect("a fixed pattern")
    });
    number
        .captures(text)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str())
}

/// How deep a numbered heading sits (`"2."` is 1, `"2.1."` is 2), else `None`.
#[must_use]
pub fn heading_depth(text: &str) -> Option<usize> {
    heading_number(text).map(|n| n.matches('.').count() + 1)
}

/// Whether a heading titled `title` numbers from a parent numbered
/// `parent_number`: an unnumbered parent (`None`) contradicts no number.
fn numbers_from(title: &str, parent_number: Option<&str>) -> bool {
    parent_number.is_none_or(|n| {
        title
            .strip_prefix(n)
            .is_some_and(|rest| rest.starts_with('.'))
    })
}

/// Whether `line`'s first word would have fitted at the end of `above`.
///
/// A wrapped line holds what did not fit on the line before, so its first word
/// and a space added to `above` reach past the column; `line` is at most the
/// column's width, so if they do not reach past `line`'s own width the word
/// fitted and `line` is not a wrap. The word's width is `line`'s width shared
/// out per character. A line with no width (a degenerate bbox) gives no
/// evidence either way, so the wrap is not refused for it. Python's
/// `_first_word_fits`.
fn first_word_fits(above: &TextBlock, line: &TextBlock) -> bool {
    let chars = line.text.chars().count();
    if chars == 0 || above.width <= 0.0 || line.width <= 0.0 {
        return false;
    }
    let word = line
        .text
        .split(' ')
        .next()
        .unwrap_or_default()
        .chars()
        .count()
        + 1;
    #[allow(clippy::cast_precision_loss)] // a line's length, far below 2^52
    let share = word as f64 / chars as f64;
    above.width + line.width * share <= line.width
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
/// Heading-sized (or body-sized but **bold or italic**), short, and carrying at
/// least one alphabetic character — a bare `"3."` is numbering, not a heading.
/// The floor is `min_heading_size` or the body size, whichever is smaller: a
/// journal set in 8 pt sets its headings in 8 pt bold.
///
/// A line naming a section exactly (`"Results"`, `"INTRODUCTION"`) needs less
/// (#628): any size above the body's, or bold or italic down to
/// [`EXACT_HEADING_FLOOR_RATIO`] of it. With the body size measured by
/// characters, headings set just over the body, or bold just under it, would
/// miss the ratio or the floor; a partial match still needs both, since that
/// is how a body line naming a section became a heading.
#[must_use]
pub fn is_potential_header(
    block: &TextBlock,
    median: f64,
    font_size_threshold: f64,
    min_heading_size: f64,
) -> bool {
    // Characters, not bytes: a multi-byte heading is not "long" because its
    // UTF-8 encoding is.
    if block.text.chars().count() > MAX_HEADING_CHARS {
        return false;
    }
    if !block.text.chars().any(char::is_alphabetic) {
        return false;
    }
    let styled = block.is_bold || block.is_italic;
    if block.font_size >= min_heading_size.min(median)
        && (styled || block.font_size >= median * font_size_threshold)
    {
        return true;
    }
    if match_section_type(&block.text).1 < 1.0 {
        return false;
    }
    if block.font_size > median + FONT_SIZE_TOLERANCE {
        return true;
    }
    styled && block.font_size >= median * EXACT_HEADING_FLOOR_RATIO - FONT_SIZE_TOLERANCE
}

/// The faces of the lines that are headings naming a section exactly (#628).
///
/// A line is bold or italic only when MuPDF flags its font so or the font's
/// name says so, and some journals set their headings in a face of their own
/// that does neither: Frontiers' numbered subsections at about the body size,
/// for one. A line in one of these faces is judged as a bold one is. The body's
/// own face, the one holding the most characters (the first seen on a tie), is
/// never one, or one body line naming a section would make every body line
/// bold; nor is a face with no name.
fn heading_faces(
    blocks: &[TextBlock],
    median: f64,
    font_size_threshold: f64,
    min_heading_size: f64,
) -> Vec<&str> {
    // Characters per face, in the order each face is first seen.
    let mut chars: Vec<(&str, usize)> = Vec::new();
    for b in blocks {
        let n = b.text.chars().count();
        match chars.iter_mut().find(|(face, _)| *face == b.font_name) {
            Some((_, count)) => *count += n,
            None => chars.push((&b.font_name, n)),
        }
    }
    let mut body: Option<(&str, usize)> = None;
    for &(face, count) in &chars {
        if body.is_none_or(|(_, most)| count > most) {
            body = Some((face, count));
        }
    }
    let body = body.map(|(face, _)| face);
    let mut faces: Vec<&str> = Vec::new();
    for b in blocks {
        if !b.font_name.is_empty()
            && Some(b.font_name.as_str()) != body
            && !faces.contains(&b.font_name.as_str())
            && match_section_type(&b.text).1 == 1.0
            && is_potential_header(b, median, font_size_threshold, min_heading_size)
        {
            faces.push(&b.font_name);
        }
    }
    faces
}

/// One heading found in the block list.
#[derive(Debug, Clone, PartialEq)]
pub struct Marker {
    /// The block index of the heading's first line.
    pub index: usize,
    /// One past the index of its last line (a heading may wrap).
    pub end: usize,
    /// What it was classified as.
    pub section_type: SectionType,
    /// The heading's own text, its lines joined by a space.
    pub title: String,
    /// How sure the classification is.
    pub confidence: f64,
    /// 1 at top level, else its section number's depth (`"2.1."` is 2).
    pub depth: usize,
}

/// Find the headings, in order, each with the lines it wraps over and its depth.
///
/// A heading-sized line is classified by the anchored patterns, then the
/// word-bounded search. A body-sized one, set apart only by bold or italic, must
/// match a pattern exactly or carry a section number: the search would read a
/// bold credit line, `"Methodology. A. Smith: ..."`, as Methods. A numbered
/// heading's depth is its number's; an unnumbered one is top level. A heading
/// that names no section is kept as a subsection under an earlier, shallower
/// heading, whose type and confidence it takes, unless that heading's own
/// number does not open this one's: under a heading it does not number from,
/// the heading it does belong to was not found. At top level there is no type
/// to give it, so a numbered one (`"3. Metformin lowers glucose"`) is held and
/// kept as [`SectionType::Other`] only if the next heading found numbers from it
/// (`"3.1."`), which then takes `Other` too if it names no section either
/// (#628). A line that is dropped does not end the hold; a heading found, or a
/// later line held, does. An unnumbered one is dropped, and so is one
/// repeating the number of the outermost heading still open (a figure's
/// `"2 Theta degrees"` axis label under `"2. Results"`). A line in a face the
/// paper's exact-match headings are set in counts as bold for the heading test
/// alone (#628). A bold quantity numbered like a subsection (`"2.5 mg/kg"` under
/// `"2."`, `"1.5 h"` under an unnumbered `"Results"`) still reads as one, and
/// so does a line of prose opening with one in a heading face (#623).
#[must_use]
pub fn identify_section_markers(
    blocks: &[TextBlock],
    median: f64,
    font_size_threshold: f64,
    min_heading_size: f64,
) -> Vec<Marker> {
    let mut markers: Vec<Marker> = Vec::new();
    // The headings enclosing the current one, outermost first, as indices
    // into `markers`.
    let mut enclosing: Vec<usize> = Vec::new();
    // An unnamed numbered top-level heading, kept if the next marker numbers
    // from it.
    let mut pending: Option<Marker> = None;
    let faces = heading_faces(blocks, median, font_size_threshold, min_heading_size);
    let mut index = 0;
    while index < blocks.len() {
        let block = &blocks[index];
        let as_bold;
        let styled = if faces.contains(&block.font_name.as_str()) {
            as_bold = TextBlock {
                is_bold: true,
                ..block.clone()
            };
            &as_bold
        } else {
            block
        };
        if !is_potential_header(styled, median, font_size_threshold, min_heading_size) {
            index += 1;
            continue;
        }
        let end = heading_end(blocks, index);
        let title = blocks[index..end]
            .iter()
            .map(|b| b.text.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        let numbered = heading_depth(&title);
        let depth = numbered.unwrap_or(1);
        let (mut section_type, mut confidence) = match_section_type(&title);
        let heading_sized = block.font_size >= median * font_size_threshold;
        if !heading_sized && confidence < 1.0 && numbered.is_none() {
            section_type = SectionType::Unknown;
        }
        if let Some(held) =
            pending.take_if(|held| depth > 1 && numbers_from(&title, heading_number(&held.title)))
        {
            markers.push(held);
            enclosing = vec![markers.len() - 1];
        }
        // Left as is until a marker is made: a line that is not one closes
        // nothing.
        let parents: Vec<usize> = enclosing
            .iter()
            .copied()
            .filter(|&m| markers[m].depth < depth)
            .collect();
        if section_type == SectionType::Unknown {
            let Some(&parent) = parents
                .last()
                .filter(|&&p| depth > 1 && numbers_from(&title, heading_number(&markers[p].title)))
            else {
                let open_number = enclosing
                    .first()
                    .and_then(|&m| heading_number(&markers[m].title));
                if depth == 1 && numbered.is_some() && heading_number(&title) != open_number {
                    pending = Some(Marker {
                        index,
                        end,
                        section_type: SectionType::Other,
                        title,
                        confidence,
                        depth: 1,
                    });
                }
                index += 1;
                continue;
            };
            section_type = markers[parent].section_type;
            confidence = markers[parent].confidence;
        }
        markers.push(Marker {
            index,
            end,
            section_type,
            title,
            confidence,
            depth,
        });
        enclosing = parents;
        enclosing.push(markers.len() - 1);
        pending = None;
        index = end;
    }
    markers
}

/// One past the last line of the heading that starts at `blocks[i]`.
///
/// A heading wraps onto the lines after it that share its font, size, weight,
/// slant and page, start below it by at most 1.5 times its height (top to top),
/// on its left edge, carry no section number of their own, and open with a word
/// that would not have fitted on the line above — at most [`MAX_HEADING_LINES`]
/// lines in all. The last test keeps a short italic subheading
/// (`"2.1. Hypothesis"`) from taking the italic body lines under it. A first
/// line that names a section exactly is a whole heading, so `"Methods"` over a
/// same-styled line is titled `"Methods"` alone.
fn heading_end(blocks: &[TextBlock], i: usize) -> usize {
    let first = &blocks[i];
    if match_section_type(&first.text).1 == 1.0 {
        return i + 1;
    }
    let same_style = |line: &TextBlock| {
        line.font_name == first.font_name
            && line.font_size == first.font_size
            && line.is_bold == first.is_bold
            && line.is_italic == first.is_italic
            && line.page_num == first.page_num
    };
    let mut end = i + 1;
    while end < blocks.len() && end - i < MAX_HEADING_LINES {
        let (line, above) = (&blocks[end], &blocks[end - 1]);
        let step = line.y - above.y;
        if !same_style(line)
            || !(0.0 < step && step <= above.height * PARAGRAPH_GAP_RATIO)
            || (line.x - first.x).abs() > LEFT_EDGE_TOLERANCE_EM * first.font_size
            || heading_depth(&line.text).is_some()
            || first_word_fits(above, line)
        {
            break;
        }
        end += 1;
    }
    end
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

    let mut flat = Vec::with_capacity(markers.len());
    for (position, marker) in markers.iter().enumerate() {
        let end_index = markers
            .get(position + 1)
            .map_or(blocks.len(), |next| next.index);
        let section_blocks = &blocks[marker.end..end_index];
        let heading = &blocks[marker.index];
        // A section whose heading is the last block carries **no** content, and
        // its pages are the heading's own rather than nothing.
        flat.push((
            marker.depth,
            Section {
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
            },
        ));
    }
    sections.extend(nest(&mut flat.into_iter().peekable(), 1));
    sections
}

/// The sections at `depth` or deeper from `items`, each with the deeper ones
/// after it as its subsections: each section goes under the nearest earlier one
/// that is shallower. Stops at the first item shallower than `depth`, which
/// belongs to an enclosing call.
fn nest(
    items: &mut std::iter::Peekable<impl Iterator<Item = (usize, Section)>>,
    depth: usize,
) -> Vec<Section> {
    let mut out = Vec::new();
    while let Some((own, mut section)) = items.next_if(|(d, _)| *d >= depth) {
        section.subsections = nest(items, own + 1);
        out.push(section);
    }
    out
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
    /// being bold or italic.
    pub font_size_threshold: f64,
    /// Font-size floor for headings, capped at the body text's median size, so
    /// a paper set small still has its bold body-size headings.
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
