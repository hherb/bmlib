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

//! PDF text assembly: spans into lines, lines into paragraphs, paragraphs into
//! HTML.
//!
//! This is `pdf_converter.py`'s **pure half** — everything that does not need a
//! PDF library. The backend is a trait ([`PdfTextExtractor`]), so the assembly
//! rules are testable and a real binding is a separate integration rather than a
//! rewrite.
//!
//! # Why the layout rules live here rather than in the backend
//!
//! A PDF carries no paragraph marks and no headings: text wraps at the column
//! edge, and a reader reports one *line* per visual line, split again at every
//! font change. Recovering prose from that is this module's job, and it is the
//! part whose quality is measured rather than asserted — so it is the part worth
//! pinning with a corpus.

use crate::fulltext::models::{ContentKind, TextBlock};
use serde_json::Value;

/// The ratio of pages on which a line must appear to count as furniture.
pub const REPEATED_LINE_RATIO: f64 = 0.6;

/// The fewest pages a document needs before furniture is looked for at all.
///
/// Below this every line is plausibly content, and a two-page document's repeated
/// header is one of only a handful of lines.
pub const REPEATED_LINE_MIN_PAGES: usize = 3;

/// How far short of the column width a line may fall and still be mid-paragraph.
pub const PARAGRAPH_BREAK_RATIO: f64 = 0.85;

/// The fewest lines a document needs before the column width is estimated from
/// the tenth-widest rather than the widest.
pub const PARAGRAPH_WIDTH_MIN_LINES: usize = 10;

/// PyMuPDF's **bold** span-flag bit.
pub const SPAN_BOLD_FLAG: i32 = 1 << 4;

/// PyMuPDF's *italic* span-flag bit.
pub const SPAN_ITALIC_FLAG: i32 = 1 << 1;

/// Collapse runs of whitespace so lines compare on their words alone.
#[must_use]
pub fn normalize_line(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    let mut in_run = false;
    for ch in line.chars() {
        if ch.is_whitespace() {
            if !in_run {
                out.push(' ');
                in_run = true;
            }
        } else {
            out.push(ch);
            in_run = false;
        }
    }
    out.trim().to_string()
}

/// Non-whitespace characters a span contributes to its line.
#[must_use]
pub fn span_text_weight(span: &Value) -> usize {
    span.get("text")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .filter(|c| !c.is_whitespace())
        .count()
}

/// Collapse one reader line into a [`TextBlock`].
///
/// **One block per _line_, not per span**: a reader starts a new span at every
/// font change, so a heading numbered in a different weight (`"2."` +
/// `"Materials and Methods"`) or a sentence holding an italic gene name would
/// otherwise shatter into fragments no anchored heading pattern can match.
///
/// Span text is **concatenated, not joined with spaces** — spans carry their own
/// trailing spaces, and joining would double them. Font attributes come from the
/// **dominant** span (most non-whitespace characters, ties to the first), so a
/// superscript reference marker or an inline formula cannot restyle the line.
///
/// Returns `None` for a line with no non-whitespace text.
#[must_use]
pub fn line_to_block(raw_line: &Value, page_num: i64) -> Option<TextBlock> {
    let spans = raw_line.get("spans").and_then(Value::as_array);
    let joined: String = spans
        .map(|spans| {
            spans
                .iter()
                .map(|s| s.get("text").and_then(Value::as_str).unwrap_or_default())
                .collect()
        })
        .unwrap_or_default();
    let text = normalize_line(&joined);
    if text.is_empty() {
        return None;
    }

    // The dominant span, ties to the first — `max_by_key` returns the **last**
    // maximum, so the comparison is strict-greater to keep the first.
    let dominant = spans.and_then(|spans| {
        spans.iter().fold(None::<&Value>, |best, span| match best {
            Some(previous) if span_text_weight(span) <= span_text_weight(previous) => {
                Some(previous)
            }
            _ => Some(span),
        })
    });

    let flags = dominant
        .and_then(|s| s.get("flags"))
        .and_then(Value::as_i64)
        .unwrap_or(0) as i32;
    let bbox = raw_line.get("bbox").and_then(Value::as_array);
    let coordinate = |index: usize| -> f64 {
        bbox.and_then(|b| b.get(index))
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
    };
    let (x0, y0, x1, y1) = (coordinate(0), coordinate(1), coordinate(2), coordinate(3));

    Some(TextBlock {
        text,
        page_num,
        font_size: dominant
            .and_then(|s| s.get("size"))
            .and_then(Value::as_f64)
            .unwrap_or(12.0),
        font_name: dominant
            .and_then(|s| s.get("font"))
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_default(),
        is_bold: flags & SPAN_BOLD_FLAG != 0,
        is_italic: flags & SPAN_ITALIC_FLAG != 0,
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
    })
}

/// The normalised lines that recur across **most pages**.
///
/// These are running heads, footers and watermarks — layout furniture that reads
/// as noise once the pages are concatenated.
///
/// **Each line is counted at most once per page**, so a phrase that merely repeats
/// within one page is not mistaken for furniture.
#[must_use]
pub fn repeated_lines(page_texts: &[String]) -> std::collections::BTreeSet<String> {
    let page_count = page_texts.len();
    if page_count < REPEATED_LINE_MIN_PAGES {
        return std::collections::BTreeSet::new();
    }

    let mut counts: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for text in page_texts {
        let mut seen_here: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        for line in text.lines() {
            if line.trim().is_empty() {
                continue;
            }
            seen_here.insert(normalize_line(line));
        }
        for line in seen_here {
            *counts.entry(line).or_insert(0) += 1;
        }
    }

    let ratio_threshold = (page_count as f64 * REPEATED_LINE_RATIO).ceil() as usize;
    let threshold = REPEATED_LINE_MIN_PAGES.max(ratio_threshold);
    counts
        .into_iter()
        .filter(|(_, seen_on)| *seen_on >= threshold)
        .map(|(line, _)| line)
        .collect()
}

/// Join consecutive lines, ending a paragraph after each **short** line.
#[must_use]
pub fn split_on_short_lines(lines: &[String], break_below: f64) -> Vec<String> {
    let mut paragraphs = Vec::new();
    let mut current: Vec<&str> = Vec::new();
    for line in lines {
        current.push(line);
        if (line.chars().count() as f64) < break_below {
            paragraphs.push(current.join(" "));
            current.clear();
        }
    }
    if !current.is_empty() {
        paragraphs.push(current.join(" "));
    }
    paragraphs
}

/// Join hard-wrapped lines back into paragraphs.
///
/// A PDF carries no paragraph marks: text wraps at the column edge, so every line
/// but the last of a paragraph runs nearly the full width. A line falling well
/// short of that width is therefore where the paragraph ended.
///
/// The width is estimated from the **tenth-widest** line once the document is
/// long enough, so headings and stubs do not drag the estimate down; the retry
/// against the widest is what recovers a document whose estimate collapsed to one
/// paragraph.
#[must_use]
pub fn group_paragraphs(lines: &[String]) -> Vec<String> {
    if lines.is_empty() {
        return Vec::new();
    }

    let mut widths: Vec<usize> = lines.iter().map(|l| l.chars().count()).collect();
    widths.sort_unstable_by(|a, b| b.cmp(a));
    let estimate = if widths.len() >= PARAGRAPH_WIDTH_MIN_LINES {
        widths[widths.len() / 10]
    } else {
        widths[0]
    };
    let paragraphs = split_on_short_lines(lines, estimate as f64 * PARAGRAPH_BREAK_RATIO);

    if paragraphs.len() == 1 && (estimate as f64) < widths[0] as f64 {
        return split_on_short_lines(lines, widths[0] as f64 * PARAGRAPH_BREAK_RATIO);
    }
    paragraphs
}

/// Escape the five characters that would otherwise be markup.
///
/// **One implementation for the whole crate**, re-exported from
/// [`crate::fulltext::service`] rather than repeated: `render_jats_html` escapes
/// article text and this escapes extracted PDF text, and two spellings of an
/// escaping rule is the classic way one of them ends up missing a character.
pub use crate::fulltext::service::html_escape;

/// Render extracted PDF text as readable HTML.
///
/// Strips repeated page furniture, reflows hard-wrapped lines into paragraphs and
/// escapes the text. **It recovers the prose, not the layout**, so a caller should
/// still offer the original PDF for figures and tables.
///
/// Returns an HTML fragment of `<p>` elements, or an empty string when the
/// conversion failed or yielded no text.
#[must_use]
pub fn render_html(success: bool, text: &str, page_texts: &[String]) -> String {
    if !success || text.trim().is_empty() {
        return String::new();
    }
    let pages: Vec<String> = if page_texts.is_empty() {
        vec![text.to_string()]
    } else {
        page_texts.to_vec()
    };
    let furniture = repeated_lines(&pages);

    let lines: Vec<String> = pages
        .iter()
        .flat_map(|page| page.lines())
        .map(normalize_line)
        .filter(|line| !line.is_empty() && !furniture.contains(line))
        .collect();

    group_paragraphs(&lines)
        .into_iter()
        .filter(|p| !p.is_empty())
        .map(|p| format!("<p>{}</p>", html_escape(&p)))
        .collect::<Vec<_>>()
        .join("\n")
}

/// What a PDF backend must do.
///
/// A trait, so the assembly rules above are testable without a PDF, and so the
/// binding — which on this platform means linking a native library — is a separate
/// integration rather than the shape of the module.
pub trait PdfTextExtractor {
    /// The backend's name, for a caller's log line.
    fn name(&self) -> &'static str;

    /// Extract one text block per **visual line**, in reading order.
    ///
    /// # Errors
    ///
    /// When the document cannot be read or the text cannot be extracted.
    fn extract_blocks(&self, path: &std::path::Path) -> Result<Vec<TextBlock>, String>;

    /// The page texts, for the furniture rule.
    ///
    /// # Errors
    ///
    /// As [`PdfTextExtractor::extract_blocks`].
    fn page_texts(&self, path: &std::path::Path) -> Result<Vec<String>, String>;
}

/// What a conversion produced.
///
/// The fields are the Python dataclass's, in its order, and `title` is declared
/// **last** there so positional construction stays stable through a change — kept
/// here for the same reason.
#[derive(Debug, Clone, PartialEq)]
pub struct ConversionResult {
    /// Whether the conversion ran at all.
    pub success: bool,
    /// The extracted text, pages joined.
    pub text: String,
    /// `"plaintext"` or `"markdown"`.
    pub format: String,
    /// How many pages the document has.
    pub page_count: usize,
    /// How many of them were converted.
    pub converted_pages: usize,
    /// How many characters the text holds.
    pub char_count: usize,
    /// Anything the backend wanted to report without failing.
    pub warnings: Vec<String>,
    /// The backend's name.
    pub converter_name: String,
    /// The backend's version.
    pub converter_version: String,
    /// The document's own metadata, **unaltered** — a record of what the PDF says,
    /// which a caller debugging provenance needs junk and all.
    pub metadata: serde_json::Value,
    /// Why it failed, when it did.
    pub error_message: Option<String>,
    /// Each page's own text, when the backend reported it.
    pub page_texts: Vec<String>,
    /// The document's title, when the PDF states one.
    pub title: Option<String>,
}

impl Default for ConversionResult {
    fn default() -> Self {
        ConversionResult {
            success: false,
            text: String::new(),
            format: "plaintext".to_string(),
            page_count: 0,
            converted_pages: 0,
            char_count: 0,
            warnings: Vec::new(),
            converter_name: String::new(),
            converter_version: String::new(),
            metadata: serde_json::json!({}),
            error_message: None,
            page_texts: Vec::new(),
            title: None,
        }
    }
}

impl ConversionResult {
    /// A failed conversion naming its cause.
    #[must_use]
    pub fn failure(error: impl Into<String>) -> Self {
        ConversionResult {
            error_message: Some(error.into()),
            ..ConversionResult::default()
        }
    }

    /// Whether **every page** was converted and some text was extracted.
    ///
    /// Note the three clauses, not one: a run that succeeded on three of ten pages
    /// is not a complete conversion, and `success` alone would call it one.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.success && self.page_count == self.converted_pages && self.char_count > 0
    }

    /// Ratio of converted pages to total pages.
    ///
    /// **`0.0` when the document has no pages**, not `1.0`: a caller filtering on
    /// a ratio is asking what fraction came through, and nothing came through. The
    /// first cut of this port returned `1.0` for an empty document — reasoning
    /// that nothing was *expected* — which is a different question and would have
    /// let an unreadable PDF pass a completeness filter.
    #[must_use]
    pub fn completion_ratio(&self) -> f64 {
        if self.page_count == 0 {
            return 0.0;
        }
        self.converted_pages as f64 / self.page_count as f64
    }
}

impl std::fmt::Display for ConversionResult {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let status = if self.success { "SUCCESS" } else { "FAILED" };
        let completeness = if self.is_complete() {
            "complete"
        } else {
            "incomplete"
        };
        write!(
            f,
            "ConversionResult({status}, {completeness}, {}/{} pages, {} chars, converter={})",
            self.converted_pages, self.page_count, self.char_count, self.converter_name
        )
    }
}

/// The content kind a converted PDF reports, so a caller does not claim it
/// retrieved an article it merely extracted text from.
#[must_use]
pub fn converted_content_kind() -> ContentKind {
    ContentKind::Extracted
}
