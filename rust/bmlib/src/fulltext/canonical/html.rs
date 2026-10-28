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

//! An HTML page to canonical text: its main content, headings as sections.
//!
//! The port of Python's `bmlib.fulltext.canonical.html`, whose docstring states
//! the rules. The page is parsed by html5ever (through `scraper`), a WHATWG
//! parser, as Python parses it with html5lib, so a malformed page builds the
//! same tree in both languages; the shared fixture holds them together.
//! Scripting is on, html5ever's default, which Python asks html5lib for. In
//! short: [`PRUNED`] elements and any `hidden` one are dropped; the root is the
//! fullest `<article>`, else `<main>` (or `role="main"`), else `<body>`; the
//! title is the root's first `<h1>` with text, else `<meta
//! name="citation_title">`, else `<title>`; `<h1>`–`<h6>` start sections; a
//! [`BLOCKS`] element ends the running paragraph; a `<tr>` is a table row.
//! Behind the `html` feature.

use scraper::{ElementRef, Html, Node};

use crate::fulltext::canonical::model::{
    assemble, BlockDraft, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};
use crate::fulltext::jats_text::normalize_whitespace;

/// Bumped whenever this converter (or [`assemble`]) can lay a page out
/// differently.
pub const HTML_CANONICAL_VERSION: u32 = 1;

/// Elements dropped with their content: code, styling, navigation, forms,
/// embedded media and graphics, none of it the paper's prose.
pub const PRUNED: [&str; 17] = [
    "script", "style", "noscript", "template", "nav", "aside", "form", "button", "select",
    "textarea", "svg", "iframe", "object", "embed", "canvas", "audio", "video",
];

/// Elements that end the running paragraph where they begin and end.
pub const BLOCKS: [&str; 35] = [
    "address",
    "article",
    "blockquote",
    "body",
    "caption",
    "dd",
    "details",
    "dialog",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "header",
    "hgroup",
    "hr",
    "legend",
    "li",
    "main",
    "menu",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
];

/// A table row, and the cells it is read from.
const ROW: &str = "tr";
const CELLS: [&str; 2] = ["td", "th"];

/// The meta tag a publisher names the article's title in (Highwire Press).
const CITATION_TITLE: &str = "citation_title";

/// The section level a heading element starts (`h1` to `h6`), if it is one.
fn heading_level(name: &str) -> Option<usize> {
    match name {
        "h1" => Some(1),
        "h2" => Some(2),
        "h3" => Some(3),
        "h4" => Some(4),
        "h5" => Some(5),
        "h6" => Some(6),
        _ => None,
    }
}

/// Whether `element` and its content are dropped ([`PRUNED`], or `hidden`).
fn is_pruned(element: ElementRef<'_>) -> bool {
    PRUNED.contains(&element.value().name()) || element.value().attr("hidden").is_some()
}

/// `root`'s descendant elements in document order, pruned subtrees skipped.
fn elements<'a>(root: ElementRef<'a>, out: &mut Vec<ElementRef<'a>>) {
    for child in root.children().filter_map(ElementRef::wrap) {
        if !is_pruned(child) {
            out.push(child);
            elements(child, out);
        }
    }
}

/// The first of [`elements`] for which `wanted` is true.
fn first<'a>(
    root: ElementRef<'a>,
    wanted: impl Fn(ElementRef<'a>) -> bool,
) -> Option<ElementRef<'a>> {
    let mut all = Vec::new();
    elements(root, &mut all);
    all.into_iter().find(|&e| wanted(e))
}

/// The text `element` holds, a space at each inner block's start and end.
fn raw_text(element: ElementRef<'_>, out: &mut String) {
    for child in element.children() {
        match child.value() {
            Node::Text(text) => out.push_str(text),
            Node::Element(_) => {
                let child = ElementRef::wrap(child).expect("an element node");
                if is_pruned(child) {
                    continue;
                }
                let name = child.value().name();
                if name == "br" {
                    out.push(' ');
                } else if BLOCKS.contains(&name) || heading_level(name).is_some() {
                    out.push(' ');
                    raw_text(child, out);
                    out.push(' ');
                } else {
                    raw_text(child, out);
                }
            }
            _ => {}
        }
    }
}

/// [`raw_text`] with its whitespace collapsed.
fn text_of(element: ElementRef<'_>) -> String {
    let mut out = String::new();
    raw_text(element, &mut out);
    normalize_whitespace(&out)
}

/// The element the paper is read from: the fullest article, else main, else `body`.
fn content_root(body: ElementRef<'_>) -> ElementRef<'_> {
    let mut all = Vec::new();
    elements(body, &mut all);
    let mut best: Option<(ElementRef<'_>, usize)> = None;
    for element in all.iter().filter(|e| e.value().name() == "article") {
        let length = text_of(*element).chars().count();
        if best.is_none_or(|(_, longest)| length > longest) {
            best = Some((*element, length));
        }
    }
    if let Some((article, _)) = best {
        return article;
    }
    all.into_iter()
        .find(|e| e.value().name() == "main" || e.value().attr("role") == Some("main"))
        .unwrap_or(body)
}

/// The `citation_title` meta tag's content, else the `<title>`'s text, else `""`.
fn metadata_title(document: ElementRef<'_>) -> String {
    let mut all = Vec::new();
    elements(document, &mut all);
    for element in &all {
        let value = element.value();
        if value.name() == "meta"
            && value.attr("name").unwrap_or("").to_lowercase() == CITATION_TITLE
        {
            let content = normalize_whitespace(value.attr("content").unwrap_or(""));
            if !content.is_empty() {
                return content;
            }
        }
    }
    all.into_iter()
        .find(|e| e.value().name() == "title")
        .map(text_of)
        .unwrap_or_default()
}

/// The section drafts a walk over the root builds, and its running paragraph.
struct Walk<'a> {
    /// The heading taken as the title, which is not a section.
    skip: Option<ElementRef<'a>>,
    sections: Vec<SectionDraft>,
    buffer: String,
}

impl<'a> Walk<'a> {
    /// Start with one untitled section.
    fn new(skip: Option<ElementRef<'a>>) -> Self {
        Walk {
            skip,
            sections: vec![SectionDraft::new("", TOP_LEVEL, Vec::new())],
            buffer: String::new(),
        }
    }

    /// The section being filled.
    fn current(&mut self) -> &mut SectionDraft {
        self.sections
            .last_mut()
            .expect("the walk starts with a section")
    }

    /// End the running paragraph ([`assemble`] drops it if it is blank).
    fn flush(&mut self) {
        if !self.buffer.is_empty() {
            let text = std::mem::take(&mut self.buffer);
            self.current().blocks.push(BlockDraft::paragraph(text));
        }
    }

    /// Read `element`'s content into the drafts, in document order.
    fn visit(&mut self, element: ElementRef<'a>) {
        for child in element.children() {
            match child.value() {
                Node::Text(text) => self.buffer.push_str(text),
                Node::Element(_) => {
                    let child = ElementRef::wrap(child).expect("an element node");
                    if !is_pruned(child) {
                        self.child(child);
                    }
                }
                _ => {}
            }
        }
    }

    /// Read one child element, by what it is.
    fn child(&mut self, element: ElementRef<'a>) {
        let name = element.value().name();
        if name == "br" {
            self.buffer.push(' ');
        } else if let Some(level) = heading_level(name) {
            self.flush();
            if self.skip != Some(element) {
                self.sections
                    .push(SectionDraft::new(text_of(element), level, Vec::new()));
            }
        } else if name == ROW {
            self.flush();
            let cells: Vec<String> = element
                .children()
                .filter_map(ElementRef::wrap)
                .filter(|c| CELLS.contains(&c.value().name()) && !is_pruned(*c))
                .map(|c| {
                    let mut text = String::new();
                    raw_text(c, &mut text);
                    text
                })
                .collect();
            self.current().blocks.push(BlockDraft::row(&cells));
        } else if BLOCKS.contains(&name) {
            self.flush();
            self.visit(element);
            self.flush();
        } else {
            self.visit(element);
        }
    }
}

/// Convert an HTML page to its canonical text (the rules are in the module
/// docs).
#[must_use]
pub fn canonical_from_html(html: &str) -> CanonicalText {
    let document = Html::parse_document(html);
    let root_element = document.root_element();
    let body = first(root_element, |e| e.value().name() == "body").unwrap_or(root_element);
    let root = content_root(body);
    let title_heading = first(root, |e| e.value().name() == "h1" && !text_of(e).is_empty());
    let title = title_heading.map_or_else(|| metadata_title(root_element), text_of);

    let mut walk = Walk::new(title_heading);
    walk.visit(root);
    walk.flush();
    assemble(
        &title,
        &walk.sections,
        SourceFormat::Html,
        &SourceFormat::Html.converter_version(HTML_CANONICAL_VERSION),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The text of every block, in order.
    fn blocks(html: &str) -> Vec<String> {
        let doc = canonical_from_html(html);
        doc.sections
            .iter()
            .flat_map(|s| &s.blocks)
            .map(|b| doc.span_text(b.span).expect("a span").to_string())
            .collect()
    }

    /// The title, if there is one.
    fn title(html: &str) -> Option<String> {
        let doc = canonical_from_html(html);
        doc.title_span
            .map(|span| doc.span_text(span).expect("a span").to_string())
    }

    #[test]
    fn a_pruned_or_hidden_element_is_dropped_and_the_text_after_it_kept() {
        for element in PRUNED
            .iter()
            // `<embed>` is void, so "junk" would be its sibling.
            .filter(|e| **e != "embed")
        {
            let html = format!("<div>before <{element}>junk</{element}> after</div>");
            assert_eq!(blocks(&html), ["before after"], "{element}");
        }
        assert_eq!(blocks("<p>shown</p><div hidden><p>no</p></div>"), ["shown"]);
    }

    #[test]
    fn the_root_is_the_fullest_article_else_main_else_body() {
        assert_eq!(
            blocks(
                "<p>blurb</p><article><p>teaser</p></article><article><p>the paper</p></article>"
            ),
            ["the paper"]
        );
        assert_eq!(
            blocks("<article><p>one</p></article><article><p>two</p></article>"),
            ["one"]
        );
        assert_eq!(
            blocks("<div>banner</div><main><p>content</p></main>"),
            ["content"]
        );
        assert_eq!(
            blocks("<div>banner</div><div role=\"main\"><p>c</p></div>"),
            ["c"]
        );
        assert_eq!(blocks("<div>a</div><p>b</p>"), ["a", "b"]);
    }

    #[test]
    fn the_title_is_the_first_h1_else_citation_title_else_title() {
        let doc = canonical_from_html("<h1><img src=x></h1><h1>Real</h1><p>x</p><h1>Later</h1>");
        assert_eq!(doc.text, "Real\n\nx\n\nLater");
        assert_eq!(
            title("<title>Junk</title><meta name=\"Citation_Title\" content=\" Meta \"><p>x</p>"),
            Some("Meta".to_string())
        );
        assert_eq!(
            title("<title>Page</title><p>x</p>"),
            Some("Page".to_string())
        );
        assert_eq!(title("<p>x</p>"), None);
    }

    #[test]
    fn a_heading_starts_a_section_at_its_level() {
        let doc = canonical_from_html("<p>lead</p><h2>Methods</h2><p>a</p><h4>Detail</h4>");
        let levels: Vec<_> = doc
            .sections
            .iter()
            .map(|s| (s.level, s.heading_span.is_some()))
            .collect();
        assert_eq!(levels, [(1, false), (2, true), (4, true)]);
    }

    #[test]
    fn blocks_end_the_paragraph_and_inline_runs_on() {
        assert_eq!(
            blocks("<div>one <b>bold</b><i>it</i><p>two</p>three<br>four</div>"),
            ["one boldit", "two", "three four"]
        );
    }

    #[test]
    fn a_row_is_its_cells_and_a_cells_blocks_are_spaced() {
        assert_eq!(
            blocks("<table><tr><td><p>a</p><p>b</p></td><td></td><td>c</td></tr></table>"),
            ["a b\t\tc"]
        );
    }

    #[test]
    fn the_version_and_format_are_stamped() {
        let doc = canonical_from_html("");
        assert_eq!(
            (doc.source_format, doc.converter_version.as_str()),
            (SourceFormat::Html, "html-1")
        );
        assert!(doc.text.is_empty() && doc.sections.is_empty());
    }
}
