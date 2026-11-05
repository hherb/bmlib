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

//! Markdown to canonical text: headings become sections, inline markup is
//! stripped.
//!
//! The port of Python's `bmlib.fulltext.canonical.markdown`, whose docstring
//! states the subset; both are hand-written so the two cannot drift apart
//! through two different parsers, and the shared fixture holds them together.
//! Text is read as a `Vec<char>`, so every index is a code point, as in Python.
//! Whitespace is Unicode `White_Space` in both. A letter or digit is
//! `char::is_alphanumeric` here and `str.isalnum` there, which differ for
//! characters Unicode calls alphabetic without being letters (Thai and Indic
//! vowel signs, circled letters such as `Ⓐ`) and wherever the two languages'
//! Unicode versions differ, so emphasis next to those may be read differently.

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;

use crate::fulltext::canonical::model::{
    assemble, BlockDraft, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};
use crate::fulltext::canonical::plain::split_lines;

/// Bumped whenever this converter (or [`assemble`]) can lay a text out
/// differently.
pub const MARKDOWN_VERSION: u32 = 2;

/// The ASCII punctuation a backslash escapes (CommonMark's set).
const ESCAPABLE: &str = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";
/// The schemes an autolink is recognised by.
const AUTOLINK_SCHEMES: [&str; 3] = ["http://", "https://", "mailto:"];
/// The longest delimiter run read as emphasis (strong).
const MAX_EMPHASIS_RUN: usize = 2;
/// The fewest marks a thematic break holds.
const MIN_BREAK_RUN: usize = 3;
/// The characters that make a thematic break.
const BREAK_CHARS: &str = "-*_";
/// What a table's delimiter row is made of.
const DELIMITER_ROW_CHARS: &str = "|:- \t";

/// A compiled pattern, built once.
fn pattern(cell: &'static OnceLock<Regex>, source: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(source).expect("a fixed pattern"))
}

/// An ATX heading: up to three spaces, one to six `#`, then a space or the end.
fn atx() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    pattern(&CELL, r"^ {0,3}(#{1,6})(?:[ \t](.*))?$")
}

/// The closing `#` run an ATX heading may end in.
fn atx_close() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    pattern(&CELL, r"(?:^|[ \t]+)#+[ \t]*$")
}

/// A setext underline: `=` for level 1, `-` for level 2.
fn setext() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    pattern(&CELL, r"^ {0,3}(=+|-+)[ \t]*$")
}

/// A list item: a bullet (group 1) or a number (group 2), then its text (group 3).
fn list_item() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    pattern(&CELL, r"^[ \t]*(?:([-*+])|([0-9]{1,9})[.)])[ \t]+(.*)$")
}

/// The number a numbered item must start at to interrupt a paragraph.
const INTERRUPTING_NUMBER: u32 = 1;

/// A code fence: three or more backticks or tildes, then an info string.
fn fence() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    pattern(&CELL, r"^ {0,3}(`{3,}|~{3,})")
}

/// A blockquote marker and the one space that may follow it.
fn quote() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    pattern(&CELL, r"^ {0,3}> ?")
}

/// `text` with its inline Markdown markup removed (see the module docs).
#[must_use]
pub fn strip_inline(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    strip_chars(&chars)
}

/// [`strip_inline`] over code points.
fn strip_chars(text: &[char]) -> String {
    let n = text.len();
    let mut out = String::new();
    // Emphasis openers already known to have no closer, by delimiter and run (#549).
    let mut unclosed: Vec<(char, usize)> = Vec::new();
    let mut index: Option<InlineIndex> = None;
    let mut i = 0;
    while i < n {
        let ch = text[i];
        if ch == '\\' && i + 1 < n && ESCAPABLE.contains(text[i + 1]) {
            out.push(text[i + 1]);
            i += 2;
            continue;
        }
        if ch == '`' {
            let run = run_length(text, i);
            let index = index.get_or_insert_with(|| InlineIndex::new(text));
            match index
                .runs
                .get(&run)
                .and_then(|starts| first_at(starts, i + run))
            {
                Some(close) => {
                    out.extend(&text[i + run..close]);
                    i = close + run;
                }
                None => {
                    out.extend(&text[i..i + run]);
                    i += run;
                }
            }
            continue;
        }
        if ch == '[' || (ch == '!' && text.get(i + 1) == Some(&'[')) {
            let start = if ch == '!' { i + 1 } else { i };
            let index = index.get_or_insert_with(|| InlineIndex::new(text));
            if let Some((label, end)) = link_at(text, start, index) {
                out.push_str(&strip_chars(label));
                i = end;
                continue;
            }
        }
        if ch == '<'
            && AUTOLINK_SCHEMES
                .iter()
                .any(|s| s.chars().eq(text[i + 1..].iter().copied().take(s.len())))
        {
            let index = index.get_or_insert_with(|| InlineIndex::new(text));
            // The address runs to the next `>` and may hold no whitespace.
            if let Some(close) = first_at(&index.angles, i + 1) {
                if !first_at(&index.spaces, i + 1).is_some_and(|space| space < close) {
                    out.extend(&text[i + 1..close]);
                    i = close + 1;
                    continue;
                }
            }
        }
        if ch == '*' || ch == '_' {
            if let Some((inner, end)) = emphasis_at(text, i, &mut unclosed) {
                out.push_str(&strip_chars(inner));
                i = end;
                continue;
            }
            let run = run_length(text, i);
            out.extend(&text[i..i + run]);
            i += run;
            continue;
        }
        out.push(ch);
        i += 1;
    }
    out
}

/// What one [`strip_inline`] call needs to find code spans, links and
/// autolinks, read once so no scan repeats (#549).
struct InlineIndex {
    /// Every unescaped `]`, in order.
    closes: Vec<usize>,
    /// Each unescaped `(`'s balancing `)`.
    parens: HashMap<usize, usize>,
    /// Every whitespace index, in order.
    spaces: Vec<usize>,
    /// Every other index, in order.
    solid: Vec<usize>,
    /// Every `>`, escaped or not (an autolink ignores escapes), in order.
    angles: Vec<usize>,
    /// Where each whole backtick run starts, by its length, in order. A code
    /// span's closer search starts after its opener's whole run, so it meets
    /// only whole runs, and backslashes do not escape inside it.
    runs: HashMap<usize, Vec<usize>>,
}

impl InlineIndex {
    /// The index of `text`.
    ///
    /// A backslash escapes the character after it. A link's scans start just
    /// after a `[` or a `]`, and neither is a backslash, so the character a scan
    /// starts at is never the one an escape takes: one pass from the start
    /// reads every escape as each scan would.
    fn new(text: &[char]) -> Self {
        let mut closes = Vec::new();
        let mut parens = HashMap::new();
        let mut opens = Vec::new();
        let mut i = 0;
        while i < text.len() {
            match text[i] {
                '\\' => {
                    i += 2;
                    continue;
                }
                ']' => closes.push(i),
                '(' => opens.push(i),
                ')' => {
                    if let Some(open) = opens.pop() {
                        parens.insert(open, i);
                    }
                }
                _ => {}
            }
            i += 1;
        }
        let (spaces, solid) = (0..text.len()).partition(|&at| text[at].is_whitespace());
        let angles = (0..text.len()).filter(|&at| text[at] == '>').collect();
        let mut runs: HashMap<usize, Vec<usize>> = HashMap::new();
        for at in 0..text.len() {
            if text[at] == '`' && (at == 0 || text[at - 1] != '`') {
                runs.entry(run_length(text, at)).or_default().push(at);
            }
        }
        InlineIndex {
            closes,
            parens,
            spaces,
            solid,
            angles,
            runs,
        }
    }
}

/// The first of the ordered `indices` at or after `from`.
fn first_at(indices: &[usize], from: usize) -> Option<usize> {
    indices
        .get(indices.partition_point(|&at| at < from))
        .copied()
}

/// Whether `text[start..end]` reads as a link destination: no whitespace, or
/// `url "title"`.
fn is_link_target(index: &InlineIndex, text: &[char], start: usize, end: usize) -> bool {
    let Some(first) = first_at(&index.solid, start).filter(|&at| at < end) else {
        return true;
    };
    let last = index.solid[index.solid.partition_point(|&at| at < end) - 1];
    let Some(gap) = first_at(&index.spaces, first).filter(|&at| at < last) else {
        return true;
    };
    let title = first_at(&index.solid, gap).expect("`last` follows the gap");
    title < last && (text[title] == '"' || text[title] == '\'') && text[last] == text[title]
}

/// The label of a `[label](target)` starting at `start`, and the index after it.
///
/// The label ends at the first unescaped `]`; the target at the `)` that
/// balances its opening one, skipping escaped parentheses, and must pass
/// [`is_link_target`]. `index` is `text`'s [`InlineIndex`].
fn link_at<'a>(text: &'a [char], start: usize, index: &InlineIndex) -> Option<(&'a [char], usize)> {
    let close = first_at(&index.closes, start + 1)?;
    let end = *index.parens.get(&(close + 1))?;
    if !is_link_target(index, text, close + 2, end) {
        return None;
    }
    Some((&text[start + 1..close], end + 1))
}

/// How many copies of `text[start]` follow from `start`.
fn run_length(text: &[char], start: usize) -> usize {
    text[start..]
        .iter()
        .take_while(|&&c| c == text[start])
        .count()
}

/// The content of an emphasis opening at `start`, and the index after it.
///
/// The opener is a run of one or two delimiters not preceded by a letter or
/// digit and followed by a non-space; the closer is the first run of the same
/// length after it that follows a non-space and is not followed by a letter or
/// digit. Whether a run closes depends on the run alone, so once an opener
/// finds none, no later opener of its delimiter and length can: it joins
/// `unclosed`, which the caller keeps for one left-to-right walk of `text`.
fn emphasis_at<'a>(
    text: &'a [char],
    start: usize,
    unclosed: &mut Vec<(char, usize)>,
) -> Option<(&'a [char], usize)> {
    let delimiter = text[start];
    let run = run_length(text, start);
    let after = start + run;
    if run > MAX_EMPHASIS_RUN || after >= text.len() || text[after].is_whitespace() {
        return None;
    }
    if start > 0 && text[start - 1].is_alphanumeric() {
        return None;
    }
    if unclosed.contains(&(delimiter, run)) {
        return None;
    }
    let mut i = after + 1;
    while i < text.len() {
        if text[i] != delimiter {
            i += 1;
            continue;
        }
        let length = run_length(text, i);
        let end = i + length;
        if length == run
            && !text[i - 1].is_whitespace()
            && !text.get(end).is_some_and(|c| c.is_alphanumeric())
        {
            return Some((&text[after..i], end));
        }
        i = end;
    }
    unclosed.push((delimiter, run));
    None
}

/// The cells of a table row, split on unescaped pipes, inline markup stripped.
fn table_cells(line: &str) -> Vec<String> {
    let trimmed: Vec<char> = line.trim().chars().collect();
    let mut body = &trimmed[1..];
    if body.last() == Some(&'|') && !body.ends_with(&['\\', '|']) {
        body = &body[..body.len() - 1];
    }
    let mut cells = Vec::new();
    let mut current: Vec<char> = Vec::new();
    let mut i = 0;
    while i < body.len() {
        if body[i] == '\\' && body.get(i + 1) == Some(&'|') {
            current.extend(['\\', '|']);
            i += 2;
            continue;
        }
        if body[i] == '|' {
            cells.push(strip_chars(&current));
            current.clear();
        } else {
            current.push(body[i]);
        }
        i += 1;
    }
    cells.push(strip_chars(&current));
    cells
}

/// Whether `line` is a table's `|---|:--:|` row.
fn is_delimiter_row(line: &str) -> bool {
    line.contains('-') && line.trim().chars().all(|c| DELIMITER_ROW_CHARS.contains(c))
}

/// Whether `line` is `---`, `***` or `___` (spaces allowed between).
fn is_thematic_break(line: &str) -> bool {
    let marks: Vec<char> = line.chars().filter(|&c| c != ' ' && c != '\t').collect();
    marks.len() >= MIN_BREAK_RUN
        && BREAK_CHARS.contains(marks[0])
        && marks.iter().all(|&c| c == marks[0])
}

/// The marker of an open list item.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Marker {
    Bullet,
    Number,
}

/// The line-by-line state of one conversion.
struct Converter {
    title: String,
    title_taken: bool,
    sections: Vec<SectionDraft>,
    paragraph: Vec<String>,
    item: Option<Marker>,
    table_rows: usize,
    seen_content: bool,
    /// The open fence's marker run and the blockquote depth it sits at.
    fence: Option<(String, usize)>,
    fence_lines: Vec<String>,
}

impl Converter {
    fn new() -> Self {
        Converter {
            title: String::new(),
            title_taken: false,
            sections: vec![SectionDraft::new("", TOP_LEVEL, vec![])],
            paragraph: Vec::new(),
            item: None,
            table_rows: 0,
            seen_content: false,
            fence: None,
            fence_lines: Vec::new(),
        }
    }

    /// Add `draft` to the current section.
    fn block(&mut self, draft: BlockDraft) {
        self.sections
            .last_mut()
            .expect("there is always a section")
            .blocks
            .push(draft);
        self.seen_content = true;
    }

    /// End the paragraph (or list item) being collected.
    fn flush(&mut self) {
        if !self.paragraph.is_empty() {
            let text = strip_inline(&self.paragraph.join(" "));
            self.paragraph.clear();
            self.block(BlockDraft::paragraph(text));
        }
        self.item = None;
    }

    /// Start a section, or take the title for a leading level-1 heading.
    fn heading(&mut self, level: usize, text: &str) {
        self.flush();
        if level == TOP_LEVEL && !self.seen_content && !self.title_taken {
            self.title = strip_inline(text);
            self.title_taken = true;
            return;
        }
        self.sections
            .push(SectionDraft::new(strip_inline(text), level, vec![]));
        self.seen_content = true;
    }

    /// End the fenced block; its inline markup is not stripped.
    fn close_fence(&mut self) {
        let text = self.fence_lines.join("\n");
        self.fence = None;
        self.fence_lines.clear();
        self.block(BlockDraft::paragraph(text));
    }

    /// Read one line.
    fn line(&mut self, line: &str) {
        let mut line = line.to_string();
        let mut quotes = 0;
        if let Some((open, depth)) = &self.fence {
            let depth = *depth;
            while quotes < depth {
                let Some(marker) = quote().find(&line) else {
                    break;
                };
                line = line[marker.end()..].to_string();
                quotes += 1;
            }
            if quotes == depth {
                let stripped = line.trim();
                let mark = open.chars().next().expect("a fence is never empty");
                if !stripped.is_empty()
                    && stripped.chars().all(|c| c == mark)
                    && stripped.chars().count() >= open.chars().count()
                {
                    self.close_fence();
                } else {
                    self.fence_lines.push(line);
                }
                return;
            }
            // The blockquote holding the fence ended, and the fence with it.
            self.close_fence();
        }
        while let Some(marker) = quote().find(&line) {
            line = line[marker.end()..].to_string();
            quotes += 1;
        }
        let is_table_line = line.trim().starts_with('|');
        if !is_table_line {
            self.table_rows = 0;
        }
        if let Some(open) = fence().captures(&line) {
            // A backtick fence's info string may not hold a backtick (CommonMark).
            let whole = open.get(0).expect("group 0 is the match");
            if !(open[1].starts_with('`') && line[whole.end()..].contains('`')) {
                self.flush();
                self.fence = Some((open[1].to_string(), quotes));
                return;
            }
        }
        if line.chars().all(char::is_whitespace) {
            self.flush();
            return;
        }
        if let Some(heading) = atx().captures(&line) {
            let text = heading.get(2).map_or("", |m| m.as_str());
            let text = atx_close().replace(text, "").into_owned();
            self.heading(heading[1].len(), &text);
            return;
        }
        if !self.paragraph.is_empty() && self.item.is_none() {
            if let Some(underline) = setext().captures(&line) {
                let level = if underline[1].starts_with('=') { 1 } else { 2 };
                let text = self.paragraph.join(" ");
                self.paragraph.clear();
                self.heading(level, &text);
                return;
            }
        }
        if is_thematic_break(&line) {
            self.flush();
            return;
        }
        if is_table_line {
            self.flush();
            self.table_rows += 1;
            // Only the row right after a table's first is its delimiter row.
            if !(self.table_rows == 2 && is_delimiter_row(&line)) {
                self.block(BlockDraft::row(&table_cells(&line)));
            }
            return;
        }
        if let Some(item) = list_item().captures(&line) {
            let bullet = item.get(1).is_some();
            let starts_at_one = item
                .get(2)
                .and_then(|number| number.as_str().parse::<u32>().ok())
                == Some(INTERRUPTING_NUMBER);
            if bullet
                || self.paragraph.is_empty()
                || self.item == Some(Marker::Number)
                || starts_at_one
            {
                self.flush();
                let text = if bullet { &item[3] } else { line.trim() };
                self.paragraph = vec![text.to_string()];
                self.item = Some(if bullet {
                    Marker::Bullet
                } else {
                    Marker::Number
                });
                return;
            }
        }
        self.paragraph.push(line.trim().to_string());
    }

    /// End the document and lay it out.
    fn finish(mut self) -> CanonicalText {
        if self.fence.is_some() {
            self.close_fence();
        }
        self.flush();
        assemble(
            &self.title,
            &self.sections,
            SourceFormat::Markdown,
            &SourceFormat::Markdown.converter_version(MARKDOWN_VERSION),
        )
    }
}

/// Convert Markdown `text` to its canonical text (the subset is in the module
/// docs).
#[must_use]
pub fn canonical_from_markdown(text: &str) -> CanonicalText {
    let mut converter = Converter::new();
    for line in split_lines(text) {
        converter.line(&line);
    }
    converter.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_inline_leaves_what_is_not_markup() {
        for (source, expected) in [
            ("**unclosed bold", "**unclosed bold"),
            ("[no target]", "[no target]"),
            ("[label] (space)", "[label] (space)"),
            ("<not a link>", "<not a link>"),
            ("`unclosed code", "`unclosed code"),
            ("***three***", "***three***"),
            ("a \\_b\\_ c", "a _b_ c"),
            ("[**bold** label](u)", "bold label"),
            ("*a*b*", "a*b"),
            ("trailing \\", "trailing \\"),
            ("β *γ* δ", "β γ δ"),
        ] {
            assert_eq!(strip_inline(source), expected, "{source:?}");
        }
    }

    fn texts(markdown: &str) -> Vec<String> {
        let doc = canonical_from_markdown(markdown);
        doc.sections
            .iter()
            .flat_map(|section| &section.blocks)
            .map(|block| doc.span_text(block.span).expect("a valid span").to_string())
            .collect()
    }

    #[test]
    fn block_rules_beyond_the_contract() {
        assert_eq!(texts("~~~\nkept *as is*\n"), ["kept *as is*"]);
        assert_eq!(texts("#5 is a tag"), ["#5 is a tag"]);
        assert_eq!(texts("a\n\n---\n\nb"), ["a", "b"]);
        let doc = canonical_from_markdown("Lead.\n\n##\n\nBody.");
        let headings: Vec<_> = doc.sections.iter().map(|s| s.heading_span).collect();
        assert_eq!(headings, [None, None]);
        let levels: Vec<_> = doc.sections.iter().map(|s| s.level).collect();
        assert_eq!(levels, [1, 2]);
        assert_eq!(doc.converter_version, "markdown-2");
    }

    #[test]
    fn fences_and_quotes() {
        // #548: a quoted fence kept its markers and never closed.
        assert_eq!(
            texts("> ```\n> code *x*\n> ```\n\n# Heading\n\nText"),
            ["code *x*", "Text"]
        );
        assert_eq!(
            texts("> > ```\n> > deep\n> shallow *y*"),
            ["deep", "shallow y"]
        );
        assert_eq!(texts("```\n> kept\n```"), ["> kept"]);
        assert_eq!(texts("```code``` here\n\n*Text*"), ["code here", "Text"]);
        assert_eq!(texts("~~~ `tilde` info\nkept *z*\n~~~"), ["kept *z*"]);
    }

    #[test]
    fn unclosed_openers_cost_linear_time() {
        // #549: each opener rescanned the paragraph. Linear, these take
        // milliseconds even unoptimised; quadratic, minutes.
        let nested = "[a](".repeat(10_000) + "x" + &" ".repeat(10_000) + "y" + &")".repeat(10_000);
        let runs = (1..400)
            .map(|k| "`".repeat(k))
            .collect::<Vec<_>>()
            .join(" ");
        for text in [
            "(_a ".repeat(20_000),
            "[a ".repeat(20_000),
            "[".repeat(60_000) + "]",
            "[a](".repeat(20_000),
            nested,
            "<".repeat(60_000) + ">",
            "<http://a".repeat(9_000) + " >",
            runs,
        ] {
            let started = std::time::Instant::now();
            let _ = strip_inline(&text);
            assert!(started.elapsed().as_secs_f64() < 5.0, "{:?}", &text[..12]);
        }
    }
}
