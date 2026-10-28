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
//! the rules. The page is parsed by html5ever, through `scraper` here and
//! markupever in Python, so both languages build html5ever's tree (0.39 here,
//! 0.40 there; the shared fixture is what holds them together), scripting on
//! and a leading byte-order mark discarded. Every walk is iterative, over
//! ego-tree's open and close edges, so nesting depth costs time, not stack. In
//! short: [`PRUNED`] elements and any `hidden` one (but `hidden="until-found"`)
//! are dropped; the root is the fullest `<article>` if it holds at least
//! [`MIN_ARTICLE_SHARE`] of the body's text, else `<main>` (or `role="main"`),
//! else `<body>`, else the `<html>` element without its `<head>`; the title is the first `<h1>` with text the walk
//! reaches, else `<meta name="citation_title">`, else `<title>`; `<h1>`–`<h6>`
//! start sections; a [`BLOCKS`] element ends the running paragraph; a `<tr>` is
//! a table row. Behind the `html` feature.

use ego_tree::iter::Edge;
use scraper::{ElementRef, Html, Node};

use crate::fulltext::canonical::model::{
    assemble, BlockDraft, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};
use crate::fulltext::jats_text::normalize_whitespace;

/// Bumped whenever this converter (or [`assemble`]) can lay a page out
/// differently.
pub const HTML_CANONICAL_VERSION: u32 = 1;

/// The least share of the body's text, in characters that are not white space,
/// the fullest `<article>` must hold to be the root, as `(numerator,
/// denominator)`. Unmeasured: a starting point, like [`MIN_BODY_WORDS`].
///
/// [`MIN_BODY_WORDS`]: crate::fulltext::canonical::MIN_BODY_WORDS
pub const MIN_ARTICLE_SHARE: (usize, usize) = (1, 2);

/// Elements dropped with their content: code, styling, navigation, forms,
/// embedded media and graphics, none of it the paper's prose. `<noembed>` and
/// `<noframes>` are raw text to html5ever, so their content is markup.
pub const PRUNED: [&str; 19] = [
    "script", "style", "noscript", "noembed", "noframes", "template", "nav", "aside", "form",
    "button", "select", "textarea", "svg", "iframe", "object", "embed", "canvas", "audio", "video",
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

/// Whether `element` and its content are dropped ([`PRUNED`], or `hidden` but
/// not `hidden="until-found"`). `attr` reads an attribute in no namespace.
fn is_pruned(element: ElementRef<'_>) -> bool {
    PRUNED.contains(&element.value().name())
        || element
            .value()
            .attr("hidden")
            .is_some_and(|hidden| !hidden.eq_ignore_ascii_case("until-found"))
}

/// One step of [`edges`]: an element opening or closing, or a text.
#[derive(Clone, Copy)]
enum Step<'a> {
    Open(ElementRef<'a>),
    Close(ElementRef<'a>),
    Text(&'a str),
}

/// `root`'s content in document order, pruned subtrees and comments skipped.
///
/// Each element below `root` is bracketed by an [`Step::Open`] and a
/// [`Step::Close`]. The tree is walked by ego-tree's open and close edges,
/// never by recursion; `root`'s own two edges are not steps.
fn edges<'a>(root: ElementRef<'a>) -> impl Iterator<Item = Step<'a>> {
    let mut skipping = 0usize;
    root.traverse().filter_map(move |edge| {
        let (node, open) = match edge {
            Edge::Open(node) => (node, true),
            Edge::Close(node) => (node, false),
        };
        if node.id() == root.id() {
            return None;
        }
        if skipping > 0 {
            if open {
                skipping += 1;
            } else {
                skipping -= 1;
            }
            return None;
        }
        match node.value() {
            Node::Element(_) => {
                let element = ElementRef::wrap(node).expect("an element node");
                if open && is_pruned(element) {
                    skipping = 1;
                    None
                } else if open {
                    Some(Step::Open(element))
                } else {
                    Some(Step::Close(element))
                }
            }
            Node::Text(text) if open => Some(Step::Text(text)),
            _ => None,
        }
    })
}

/// `root`'s descendant elements in document order, pruned subtrees skipped.
fn elements<'a>(root: ElementRef<'a>) -> impl Iterator<Item = ElementRef<'a>> {
    edges(root).filter_map(|step| match step {
        Step::Open(element) => Some(element),
        _ => None,
    })
}

/// The first of [`elements`] for which `wanted` is true.
fn first<'a>(
    root: ElementRef<'a>,
    wanted: impl Fn(ElementRef<'a>) -> bool,
) -> Option<ElementRef<'a>> {
    elements(root).find(|&e| wanted(e))
}

/// Whether a heading's or cell's text puts a space where element `name`
/// begins and ends.
fn spaces_out(name: &str) -> bool {
    BLOCKS.contains(&name) || heading_level(name).is_some()
}

/// The text `element` holds, a space at each inner block's start and end and
/// at `<br>`.
fn raw_text(element: ElementRef<'_>) -> String {
    let mut out = String::new();
    for step in edges(element) {
        match step {
            Step::Text(text) => out.push_str(text),
            Step::Open(e) if e.value().name() == "br" || spaces_out(e.value().name()) => {
                out.push(' ');
            }
            Step::Close(e) if spaces_out(e.value().name()) => out.push(' '),
            _ => {}
        }
    }
    out
}

/// [`raw_text`] with its whitespace collapsed.
fn text_of(element: ElementRef<'_>) -> String {
    normalize_whitespace(&raw_text(element))
}

/// The element the paper is read from: the fullest article, else main, else `body`.
///
/// One walk measures every article: a text counts towards the innermost open
/// one, and an article's length towards the one enclosing it when it closes,
/// so nested articles cost time linear in the page, not in their depth.
fn content_root(body: ElementRef<'_>) -> ElementRef<'_> {
    let mut articles: Vec<(ElementRef<'_>, usize)> = Vec::new(); // in document order
    let mut open: Vec<usize> = Vec::new();
    let mut total = 0usize;
    for step in edges(body) {
        match step {
            Step::Text(text) => {
                let length = text.chars().filter(|c| !c.is_whitespace()).count();
                total += length;
                if let Some(&index) = open.last() {
                    articles[index].1 += length;
                }
            }
            Step::Open(e) if e.value().name() == "article" => {
                open.push(articles.len());
                articles.push((e, 0));
            }
            Step::Close(e) if e.value().name() == "article" => {
                let length = open.pop().map_or(0, |index| articles[index].1);
                if let Some(&index) = open.last() {
                    articles[index].1 += length;
                }
            }
            _ => {}
        }
    }
    let mut best: Option<(ElementRef<'_>, usize)> = None;
    for &(article, length) in &articles {
        // A strict `>`, so the first article wins a tie.
        if best.is_none_or(|(_, longest)| length > longest) {
            best = Some((article, length));
        }
    }
    let (numerator, denominator) = MIN_ARTICLE_SHARE;
    if let Some((article, length)) = best {
        if denominator * length >= numerator * total {
            return article;
        }
    }
    first(body, |e| {
        e.value().name() == "main" || e.value().attr("role") == Some("main")
    })
    .unwrap_or(body)
}

/// The `citation_title` meta tag's content, else the `<title>`'s text, else `""`.
fn metadata_title(document: ElementRef<'_>) -> String {
    for element in elements(document) {
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
    first(document, |e| e.value().name() == "title")
        .map(text_of)
        .unwrap_or_default()
}

/// `row`'s `<td>` and `<th>` children that are not pruned.
fn cells(row: ElementRef<'_>) -> Vec<String> {
    row.children()
        .filter_map(ElementRef::wrap)
        .filter(|c| CELLS.contains(&c.value().name()) && !is_pruned(*c))
        .map(raw_text)
        .collect()
}

/// The section drafts a walk over the root builds, its title and its running
/// paragraph.
struct Walk {
    /// The first `<h1>` with text the walk reached, which is not a section.
    title: Option<String>,
    sections: Vec<SectionDraft>,
    buffer: String,
}

impl Walk {
    /// Start with one untitled section and no title.
    fn new() -> Self {
        Walk {
            title: None,
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

    /// Read `root`'s content into section drafts, in document order.
    ///
    /// A heading or a row is read whole where it opens (its own text, its
    /// cells), and the walk passes over its content to where it closes.
    /// `<head>`, reached only when the root is the `<html>` element, is passed
    /// over whole: its `<title>` is metadata, read only as the title.
    fn read(&mut self, root: ElementRef<'_>) {
        let mut inside = 0usize;
        for step in edges(root) {
            if inside > 0 {
                match step {
                    Step::Open(_) => inside += 1,
                    Step::Close(_) => inside -= 1,
                    Step::Text(_) => {}
                }
                continue;
            }
            let (element, open) = match step {
                Step::Text(text) => {
                    self.buffer.push_str(text);
                    continue;
                }
                Step::Open(element) => (element, true),
                Step::Close(element) => (element, false),
            };
            let name = element.value().name();
            if open && name == "head" {
                inside = 1;
            } else if open && name == "br" {
                self.buffer.push(' ');
            } else if let (true, Some(level)) = (open, heading_level(name)) {
                self.flush();
                let heading = text_of(element);
                if level == 1 && self.title.is_none() && !heading.is_empty() {
                    self.title = Some(heading);
                } else {
                    self.sections
                        .push(SectionDraft::new(heading, level, Vec::new()));
                }
                inside = 1;
            } else if open && name == ROW {
                self.flush();
                let cells = cells(element);
                self.current().blocks.push(BlockDraft::row(&cells));
                inside = 1;
            } else if BLOCKS.contains(&name) {
                self.flush();
            }
        }
        self.flush();
    }
}

/// Convert an HTML page to its canonical text (the rules are in the module
/// docs).
#[must_use]
pub fn canonical_from_html(html: &str) -> CanonicalText {
    let document = Html::parse_document(html);
    let root_element = document.root_element();
    let body = first(root_element, |e| e.value().name() == "body").unwrap_or(root_element);
    let mut walk = Walk::new();
    walk.read(content_root(body));
    let title = walk
        .title
        .take()
        .unwrap_or_else(|| metadata_title(root_element));
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
            blocks("<p>b</p><article><p>teaser</p></article><article><p>the paper</p></article>"),
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
    fn every_block_element_ends_the_paragraph() {
        // html5ever drops the table's own elements outside a table (the row
        // tests read them), merges `<body>` into the page's, and `<hr>` is
        // void. The `<main>` keeps an `<article>` or `<main>` from being the root.
        const TABLE_PARTS: [&str; 8] = [
            "caption", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
        ];
        for element in BLOCKS
            .iter()
            .filter(|e| !TABLE_PARTS.contains(e) && !["body", "hr"].contains(e))
        {
            let html = format!("<main><div>a<{element}>b</{element}>c</div></main>");
            assert_eq!(blocks(&html), ["a", "b", "c"], "{element}");
        }
        assert_eq!(blocks("<div>a<hr>b</div>"), ["a", "b"]);
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
    fn deep_nesting_is_walked_without_recursion() {
        // A 256 KiB stack, an eighth of a tokio worker's: every walk is
        // iterative, so depth costs time and never stack.
        const DEPTH: usize = 5000;
        let html = format!(
            "{}<h2>Deep</h2>text{}",
            "<div>".repeat(DEPTH),
            "</div>".repeat(DEPTH)
        );
        let text = std::thread::Builder::new()
            .stack_size(256 * 1024)
            .spawn(move || canonical_from_html(&html).text)
            .expect("a thread")
            .join()
            .expect("no stack overflow");
        assert_eq!(text, "Deep\n\ntext");
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
