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
//! ego-tree's open and close edges, so nesting depth costs time, not stack.
//! html5ever's own parse is quadratic in nesting depth (#563), so the page is
//! fed to it [`HTML_PARSE_CHUNK`] code points at a time, and a page still being
//! parsed after [`HTML_PARSE_SECONDS`] is refused ([`HtmlError::TimedOut`]);
//! since one chunk's own time grows with the page's length, a page longer than
//! [`HTML_MAX_CHARS`] is refused unparsed ([`HtmlError::TooLarge`]). A page
//! whose parse creates more than [`max_elements`] of its length is refused
//! part way ([`HtmlError::TooManyElements`]): html5ever rebuilds every
//! formatting element a closed block left open each time text follows, so a
//! page can make it create elements with the square of its length (#723).
//! The leading byte-order mark is discarded here rather than by html5ever,
//! which discards one at the start of every chunk it is fed. In short: [`PRUNED`] elements and any `hidden` one (but `hidden="until-found"`)
//! are dropped; the root is the fullest `<article>` if it holds at least
//! [`MIN_ARTICLE_SHARE`] of the body's text, else `<main>` (or `role="main"`),
//! else `<body>`, else the `<html>` element without its `<head>`; the title is the first `<h1>` with text the walk
//! reaches, else `<meta name="citation_title">`, else `<title>`; `<h1>`–`<h6>`
//! start sections; a [`BLOCKS`] element ends the running paragraph; a `<tr>` is
//! a table row. Behind the `html` feature.

use std::borrow::Cow;
use std::cell::Cell;
use std::fmt;
use std::rc::Rc;
use std::time::{Duration, Instant};

use ego_tree::iter::Edge;
use html5ever::driver::{self, ParseOpts};
use html5ever::tendril::{StrTendril, TendrilSink};
use html5ever::tokenizer::TokenizerOpts;
use html5ever::tree_builder::{ElementFlags, NodeOrText, QuirksMode, TreeSink};
use html5ever::{Attribute, QualName};
use scraper::{ElementRef, Html, HtmlTreeSink, Node};

use crate::fulltext::canonical::model::{
    assemble, BlockDraft, CanonicalText, SectionDraft, SourceFormat, TOP_LEVEL,
};
use crate::fulltext::jats_text::normalize_whitespace;

/// Bumped whenever this converter (or [`assemble`]) can lay a page out
/// differently.
pub const HTML_CANONICAL_VERSION: u32 = 1;

/// The longest a page may take to parse, in seconds, before it is refused
/// (#563). html5ever is quadratic in nesting depth. Measured in Python only
/// (markupever, html5ever 0.40), unmeasured here: `<div>` nested 40,000 deep,
/// 429 KB, at 2.6 s; a 5 MB page nested that way about 5
/// minutes; a 20 MB page shaped like an article parsed in 0.25 s, though
/// [`HTML_MAX_CHARS`] now refuses it (#656). The clock
/// is read between chunks, so a refusal comes one chunk late, and a chunk's own
/// time grows with the depth already open, which [`HTML_MAX_CHARS`] bounds.
/// Wall-clock time, so a loaded machine can refuse a page an idle one accepts.
pub const HTML_PARSE_SECONDS: u64 = 30;

/// The longest page parsed, in code points; a longer one is refused unparsed
/// (#654). One chunk's time grows with the depth open before it, and a page can
/// open one element per three code points, so it grows with the page's length:
/// the worst measured, `<i>` or an unknown `<x>` nested to the page's length
/// and then a chunk of `<hr>`, took about 16 s per million code points here (a
/// release build; 95 s at 4 million, never refused) and about half that in
/// Python, a loaded machine both. At this length a page is converted or refused
/// within about twice [`HTML_PARSE_SECONDS`] here (41 s and 60 s measured).
pub const HTML_MAX_CHARS: usize = 2_000_000;

/// Code points fed to the parser between two readings of the clock (Python's
/// measurement: smaller chunks made markupever slower on the same page).
pub const HTML_PARSE_CHUNK: usize = 16_384;

/// The elements a page's parse may create beyond one per code point of the
/// page (#723); past [`max_elements`] it is refused. An element's own start tag
/// takes at least three code points, and the elements html5ever implies
/// (`<html>`, `<head>`, `<body>`, a table's `<tbody>` and `<tr>`) are a few per
/// tag, so only a page that has html5ever rebuild the same formatting elements
/// over and over creates more. Unmeasured on real pages, which no draw has
/// sized: a starting point, like [`MIN_ARTICLE_SHARE`].
pub const HTML_ELEMENT_ALLOWANCE: usize = 1_000;

/// Code points fed to html5ever between two readings of the elements created
/// (#723). One rebuild creates as many elements as the page has formatting
/// elements open, so the count can only be read often: between two readings a
/// piece of 64 code points holds about five rebuilds on #723's page (12 code
/// points each, `<div>x</div>`) and about sixteen on `<p>x` (4 each). Measured on a
/// 486 KB page shaped like an article, a release build: 5.6 ms in pieces of 64
/// against 4.4 ms in pieces of 16,384 (and no difference at all on 40,000
/// nested `<div>`, 3.0 s either way).
const ELEMENT_COUNT_PIECE: usize = 64;

/// The most elements the parse of a page of `length` code points may create:
/// one per code point and [`HTML_ELEMENT_ALLOWANCE`] (#723).
#[must_use]
pub fn max_elements(length: usize) -> usize {
    length.saturating_add(HTML_ELEMENT_ALLOWANCE)
}

/// A page [`canonical_from_html`] refuses. Lengths are in code points.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum HtmlError {
    /// The page is longer than [`HTML_MAX_CHARS`], and was not parsed.
    TooLarge {
        /// The page's length as passed, its leading byte-order mark included.
        length: usize,
    },
    /// html5ever was still parsing the page after [`HTML_PARSE_SECONDS`].
    TimedOut {
        /// The code points fed to the parser before it was stopped.
        parsed: usize,
        /// The page's length after its leading byte-order mark is dropped.
        length: usize,
    },
    /// html5ever had created more than [`max_elements`] of the page's length
    /// (#723): it rebuilds every formatting element a closed block left open
    /// each time text follows, so a page can have it create elements with the
    /// square of its length.
    TooManyElements {
        /// The elements created when the parse was stopped.
        created: usize,
        /// The code points fed to the parser before it was stopped.
        parsed: usize,
        /// The page's length after its leading byte-order mark is dropped.
        length: usize,
    },
}

impl fmt::Display for HtmlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TooLarge { length } => write!(
                f,
                "the page is {length} characters long, more than the {HTML_MAX_CHARS} this \
                 converter parses"
            ),
            Self::TimedOut { parsed, length } => write!(
                f,
                "the page took longer than {HTML_PARSE_SECONDS} s to parse (stopped after \
                 {parsed} of {length} characters); it may be nested too deeply"
            ),
            Self::TooManyElements {
                created,
                parsed,
                length,
            } => write!(
                f,
                "the page made the parser create {created} elements, more than the {} its \
                 {length} characters allow (stopped after {parsed}); it may repeat misnested \
                 formatting elements",
                max_elements(*length)
            ),
        }
    }
}

impl std::error::Error for HtmlError {}

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
///
/// # Errors
///
/// [`HtmlError::TooLarge`] if the page is longer than [`HTML_MAX_CHARS`];
/// [`HtmlError::TimedOut`] if parsing it takes longer than
/// [`HTML_PARSE_SECONDS`]. The clock is wall time, so the same page may parse
/// on a less loaded machine. [`HtmlError::TooManyElements`] if the parse
/// creates more than [`max_elements`] of the page's length.
pub fn canonical_from_html(html: &str) -> Result<CanonicalText, HtmlError> {
    convert_within(html, Duration::from_secs(HTML_PARSE_SECONDS))
}

/// [`canonical_from_html`] with its parse given `budget`.
fn convert_within(html: &str, budget: Duration) -> Result<CanonicalText, HtmlError> {
    refuse_longer_than(html, HTML_MAX_CHARS)?;
    let start = Instant::now();
    let document = parse(html, HTML_PARSE_CHUNK, || start.elapsed() > budget)?;
    Ok(read(&document))
}

/// [`HtmlError::TooLarge`] if `html` is longer than `max` code points.
fn refuse_longer_than(html: &str, max: usize) -> Result<(), HtmlError> {
    // A page of at most `max` bytes holds at most `max` code points, uncounted.
    if html.len() <= max {
        return Ok(());
    }
    let length = html.chars().count();
    if length > max {
        return Err(HtmlError::TooLarge { length });
    }
    Ok(())
}

/// Parse `html`, read `chunk` code points at a time; before each chunk after
/// the first, `past_deadline` is asked, and `true` stops the parse. Each chunk
/// is fed in pieces of [`ELEMENT_COUNT_PIECE`], and after each the elements
/// created are counted: past [`max_elements`] the parse stops.
fn parse(html: &str, chunk: usize, past_deadline: impl FnMut() -> bool) -> Result<Html, HtmlError> {
    parse_counted(
        html,
        chunk,
        ELEMENT_COUNT_PIECE,
        max_elements,
        past_deadline,
    )
}

/// [`parse`], fed `piece` code points at a time, refusing more elements than
/// `max` of the page's length.
fn parse_counted(
    html: &str,
    chunk: usize,
    piece: usize,
    max: fn(usize) -> usize,
    mut past_deadline: impl FnMut() -> bool,
) -> Result<Html, HtmlError> {
    // html5ever discards a U+FEFF at the start of every chunk, so it is told to
    // keep them and the page's own leading one is dropped here, once.
    let html = html.strip_prefix('\u{feff}').unwrap_or(html);
    let opts = ParseOpts {
        tokenizer: TokenizerOpts {
            discard_bom: false,
            ..TokenizerOpts::default()
        },
        ..ParseOpts::default()
    };
    let created = Rc::new(Cell::new(0));
    let sink = CountingSink {
        inner: HtmlTreeSink::new(Html::new_document()),
        created: Rc::clone(&created),
    };
    let mut parser = driver::parse_document(sink, opts);
    let length = html.chars().count();
    let (mut rest, mut parsed) = (html, 0);
    while !rest.is_empty() {
        if parsed > 0 && parsed % chunk == 0 && past_deadline() {
            return Err(HtmlError::TimedOut { parsed, length });
        }
        // A piece never crosses a chunk's end, so the clock is read where it was.
        let take = piece.min(chunk - parsed % chunk);
        let cut = rest
            .char_indices()
            .nth(take)
            .map_or(rest.len(), |(at, _)| at);
        parser.process(StrTendril::from_slice(&rest[..cut]));
        // The last piece may be shorter than `take`.
        parsed = (parsed + take).min(length);
        rest = &rest[cut..];
        if created.get() > max(length) {
            let created = created.get();
            return Err(HtmlError::TooManyElements {
                created,
                parsed,
                length,
            });
        }
    }
    Ok(parser.finish())
}

/// scraper's tree sink, counting the elements html5ever creates (#723).
/// Every other call goes to scraper's sink as it is.
struct CountingSink {
    inner: HtmlTreeSink,
    created: Rc<Cell<usize>>,
}

impl TreeSink for CountingSink {
    type Handle = <HtmlTreeSink as TreeSink>::Handle;
    type Output = <HtmlTreeSink as TreeSink>::Output;
    type ElemName<'a>
        = <HtmlTreeSink as TreeSink>::ElemName<'a>
    where
        Self: 'a;

    fn finish(self) -> Self::Output {
        self.inner.finish()
    }
    fn parse_error(&self, msg: Cow<'static, str>) {
        self.inner.parse_error(msg);
    }
    fn get_document(&self) -> Self::Handle {
        self.inner.get_document()
    }
    fn elem_name<'a>(&'a self, target: &'a Self::Handle) -> Self::ElemName<'a> {
        self.inner.elem_name(target)
    }
    fn create_element(
        &self,
        name: QualName,
        attrs: Vec<Attribute>,
        flags: ElementFlags,
    ) -> Self::Handle {
        self.created.set(self.created.get() + 1);
        self.inner.create_element(name, attrs, flags)
    }
    fn create_comment(&self, text: StrTendril) -> Self::Handle {
        self.inner.create_comment(text)
    }
    fn create_pi(&self, target: StrTendril, data: StrTendril) -> Self::Handle {
        self.inner.create_pi(target, data)
    }
    fn append(&self, parent: &Self::Handle, child: NodeOrText<Self::Handle>) {
        self.inner.append(parent, child);
    }
    fn append_based_on_parent_node(
        &self,
        element: &Self::Handle,
        prev_element: &Self::Handle,
        child: NodeOrText<Self::Handle>,
    ) {
        self.inner
            .append_based_on_parent_node(element, prev_element, child);
    }
    fn append_doctype_to_document(
        &self,
        name: StrTendril,
        public_id: StrTendril,
        system_id: StrTendril,
    ) {
        self.inner
            .append_doctype_to_document(name, public_id, system_id);
    }
    fn mark_script_already_started(&self, node: &Self::Handle) {
        self.inner.mark_script_already_started(node);
    }
    fn pop(&self, node: &Self::Handle) {
        self.inner.pop(node);
    }
    fn get_template_contents(&self, target: &Self::Handle) -> Self::Handle {
        self.inner.get_template_contents(target)
    }
    fn same_node(&self, x: &Self::Handle, y: &Self::Handle) -> bool {
        self.inner.same_node(x, y)
    }
    fn set_quirks_mode(&self, mode: QuirksMode) {
        self.inner.set_quirks_mode(mode);
    }
    fn append_before_sibling(&self, sibling: &Self::Handle, new_node: NodeOrText<Self::Handle>) {
        self.inner.append_before_sibling(sibling, new_node);
    }
    fn add_attrs_if_missing(&self, target: &Self::Handle, attrs: Vec<Attribute>) {
        self.inner.add_attrs_if_missing(target, attrs);
    }
    fn associate_with_form(
        &self,
        target: &Self::Handle,
        form: &Self::Handle,
        nodes: (&Self::Handle, Option<&Self::Handle>),
    ) {
        self.inner.associate_with_form(target, form, nodes);
    }
    fn remove_from_parent(&self, target: &Self::Handle) {
        self.inner.remove_from_parent(target);
    }
    fn reparent_children(&self, node: &Self::Handle, new_parent: &Self::Handle) {
        self.inner.reparent_children(node, new_parent);
    }
    fn is_mathml_annotation_xml_integration_point(&self, handle: &Self::Handle) -> bool {
        self.inner
            .is_mathml_annotation_xml_integration_point(handle)
    }
    fn set_current_line(&self, line_number: u64) {
        self.inner.set_current_line(line_number);
    }
    fn allow_declarative_shadow_roots(&self, intended_parent: &Self::Handle) -> bool {
        self.inner.allow_declarative_shadow_roots(intended_parent)
    }
    fn attach_declarative_shadow(
        &self,
        location: &Self::Handle,
        template: &Self::Handle,
        attrs: &[Attribute],
    ) -> bool {
        self.inner
            .attach_declarative_shadow(location, template, attrs)
    }
    fn maybe_clone_an_option_into_selectedcontent(&self, option: &Self::Handle) {
        self.inner
            .maybe_clone_an_option_into_selectedcontent(option);
    }
}

/// The canonical text of a parsed page.
fn read(document: &Html) -> CanonicalText {
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

    /// The page's canonical text, parsed within the deadline.
    fn convert(html: &str) -> CanonicalText {
        canonical_from_html(html).expect("parsed in time")
    }

    /// A page with a split-prone mix: CRLF, entities, a comment, a table, a
    /// script, characters outside ASCII and the BMP, and two byte-order marks,
    /// a leading one (dropped) and one inside a paragraph (text): html5ever
    /// discards one at the start of every chunk it is fed.
    const PAGE: &str =
        "\u{feff}<html><head><title>T&amp;t</title></head><body>\r\n<h1>Caf\u{e9} \u{1f600}</h1>\
        <p>One\u{feff}\r\ntwo &lt;three&gt;</p><!-- a comment --><table><tr><td>a</td>\
        <td>b</td></tr></table><script>if (a < b) {}</script><p>end</p></body></html>";

    #[test]
    fn a_page_split_into_tiny_chunks_parses_as_one() {
        let whole = convert(PAGE);
        assert_eq!(
            whole.text.matches('\u{feff}').count(),
            1,
            "{:?}",
            whole.text
        );
        for chunk in [1, 2, 3, 7] {
            let document = parse(PAGE, chunk, || false).expect("no deadline");
            assert_eq!(read(&document), whole, "chunk {chunk}");
        }
    }

    #[test]
    fn a_parse_past_the_deadline_is_refused() {
        // Leading text outside ASCII, so a count of bytes is not one of code points.
        let page = format!("\u{e9}\u{e9}{PAGE}");
        let error = parse(&page, 10, || true).expect_err("past the deadline");
        assert_eq!(
            error,
            HtmlError::TimedOut {
                parsed: 10,
                length: page.chars().count()
            }
        );
        assert!(error.to_string().contains("longer than 30 s"), "{error}");
    }

    #[test]
    fn a_page_past_the_length_cap_is_refused() {
        // Code points outside ASCII, so a count of bytes is not one of code points.
        let page = "\u{e9}".repeat(5);
        assert_eq!(refuse_longer_than(&page, 5), Ok(()));
        let error = refuse_longer_than(&page, 4).expect_err("too long");
        assert_eq!(error, HtmlError::TooLarge { length: 5 });
        assert!(error.to_string().contains("is 5 characters"), "{error}");
    }

    #[test]
    fn a_page_past_the_cap_is_refused_unparsed() {
        // Unparsed, or this many `<i>` would take a minute; refused inside a zero budget.
        let page = "<i>".repeat(HTML_MAX_CHARS / 3 + 1);
        let error = convert_within(&page, Duration::ZERO).expect_err("too long");
        assert_eq!(error, HtmlError::TooLarge { length: page.len() });
    }

    #[test]
    fn a_page_of_one_chunk_never_asks_the_deadline() {
        let document = parse(PAGE, PAGE.len(), || panic!("asked")).expect("one chunk");
        assert_eq!(read(&document), convert(PAGE));
    }

    #[test]
    fn a_deeply_nested_page_is_stopped_by_the_real_clock() {
        // 40,000 nested <div> take seconds to parse whole; at 50 ms the parse
        // is stopped part way, so the work happens chunk by chunk.
        const DEPTH: usize = 40_000;
        let page = format!("{}x{}", "<div>".repeat(DEPTH), "</div>".repeat(DEPTH));
        let error = convert_within(&page, Duration::from_millis(50)).expect_err("stopped");
        let HtmlError::TimedOut { parsed, length } = error else {
            panic!("{error:?}");
        };
        assert!(parsed > 0 && parsed < length, "{parsed} of {length}");
    }

    /// #723's page: `n` formatting elements, each with its own attribute so
    /// html5ever's Noah's Ark clause matches none, left open in a block that
    /// closes; then `n` blocks, each of whose text has all `n` rebuilt.
    fn rebuilding(n: usize) -> String {
        let mut page = String::from("<div>");
        for k in 0..n {
            page.push_str(&format!("<b id={k}>"));
        }
        page.push_str("</div>");
        page.push_str(&"<div>x</div>".repeat(n));
        page
    }

    #[test]
    fn a_page_rebuilding_its_formatting_elements_is_stopped_by_its_count() {
        // Whole, this page creates about 16 million elements (2.8 GB) from 91 KB.
        let page = rebuilding(4_000);
        let error = convert_within(&page, Duration::from_secs(3600)).expect_err("refused");
        let HtmlError::TooManyElements {
            created,
            parsed,
            length,
        } = error
        else {
            panic!("{error:?}");
        };
        assert_eq!(length, page.chars().count());
        assert!(parsed < length, "{parsed} of {length}");
        // The count is read after every piece, and a piece of 64 code points
        // holds about five rebuilds of 4,000 elements each.
        let max = max_elements(length);
        assert!(
            created > max && created <= max + 6 * 4_000,
            "{created} of {max}"
        );
        assert!(
            error.to_string().contains("misnested formatting"),
            "{error}"
        );
    }

    #[test]
    fn the_element_count_refuses_one_past_the_budget() {
        // <html>, <head>, <body> and two <p>.
        const FIVE: &str = "<p>a<p>b";
        assert!(parse_counted(FIVE, 3, 1, |_| 5, || false).is_ok());
        let error = parse_counted(FIVE, 3, 1, |_| 4, || false).expect_err("over");
        assert_eq!(
            error,
            HtmlError::TooManyElements {
                created: 5,
                // The second <p> is created once its `>` is read.
                parsed: 7,
                length: 8
            }
        );
    }

    #[test]
    fn a_refusal_in_the_last_short_piece_counts_what_was_fed() {
        // Pieces "<p>", "a<p" and ">b": the second <p> is created in the last.
        let error = parse_counted("<p>a<p>b", 3, 3, |_| 4, || false).expect_err("over");
        assert_eq!(
            error,
            HtmlError::TooManyElements {
                created: 5,
                parsed: 8,
                length: 8
            }
        );
    }

    #[test]
    fn a_page_fed_in_any_pieces_parses_as_one() {
        let whole = convert(PAGE);
        for piece in [1, 2, 3, 7, 64] {
            let document =
                parse_counted(PAGE, HTML_PARSE_CHUNK, piece, max_elements, || false).expect("ok");
            assert_eq!(read(&document), whole, "piece {piece}");
        }
    }

    #[test]
    fn the_clock_is_read_at_each_chunks_end_and_not_between_pieces() {
        let length = PAGE.trim_start_matches('\u{feff}').chars().count();
        let mut asked = 0;
        parse_counted(PAGE, 10, 3, max_elements, || {
            asked += 1;
            false
        })
        .expect("no deadline");
        assert_eq!(asked, length.div_ceil(10) - 1);
    }

    #[test]
    fn the_element_budget_is_the_length_and_the_allowance() {
        assert_eq!(max_elements(0), HTML_ELEMENT_ALLOWANCE);
        assert_eq!(
            max_elements(HTML_MAX_CHARS),
            HTML_MAX_CHARS + HTML_ELEMENT_ALLOWANCE
        );
        assert_eq!(max_elements(usize::MAX), usize::MAX);
    }

    /// The text of every block, in order.
    fn blocks(html: &str) -> Vec<String> {
        let doc = convert(html);
        doc.sections
            .iter()
            .flat_map(|s| &s.blocks)
            .map(|b| doc.span_text(b.span).expect("a span").to_string())
            .collect()
    }

    /// The title, if there is one.
    fn title(html: &str) -> Option<String> {
        let doc = convert(html);
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
        let doc = convert("<h1><img src=x></h1><h1>Real</h1><p>x</p><h1>Later</h1>");
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
        let doc = convert("<p>lead</p><h2>Methods</h2><p>a</p><h4>Detail</h4>");
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
            .spawn(move || convert(&html).text)
            .expect("a thread")
            .join()
            .expect("no stack overflow");
        assert_eq!(text, "Deep\n\ntext");
    }

    #[test]
    fn the_version_and_format_are_stamped() {
        let doc = convert("");
        assert_eq!(
            (doc.source_format, doc.converter_version.as_str()),
            (SourceFormat::Html, "html-1")
        );
        assert!(doc.text.is_empty() && doc.sections.is_empty());
    }
}
