# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""An HTML page to canonical text: its main content, headings as sections.

The page is parsed by html5ever, through markupever here and scraper in the
Rust port, so a malformed page builds the tree html5ever builds in both
languages (html5ever 0.40 here and 0.39 there; the shared fixture is what
holds them together). Scripting is on and a leading byte-order mark is
discarded, html5ever's defaults in both. Every walk over the tree is iterative,
so nesting depth costs time, not stack. html5ever's own parse is quadratic in
nesting depth (#563), so the page is fed to it :data:`HTML_PARSE_CHUNK` code
points at a time, and a page still being parsed after
:data:`HTML_PARSE_SECONDS` is refused with :class:`HtmlParseTimeoutError`;
since one chunk's own time grows with the page's length, a page longer than
:data:`HTML_MAX_CHARS` is refused unparsed (:class:`HtmlPageTooLargeError`). The
leading byte-order mark is discarded here rather than by html5ever, which
discards one at the start of every chunk it is fed. Then, by fixed rules rather
than a score:

* **Pruned**: :data:`PRUNED` elements and any element with a ``hidden``
  attribute (but ``hidden="until-found"``, collapsed content a reader can still
  find) are dropped with everything inside them (text after them is kept).
  Attributes are read without a namespace, so MathML's ``xlink:role`` is not
  ``role``.
* **Root**: the ``<article>`` holding the most text (the first on a tie), if
  it holds at least half the text of the body (:data:`MIN_ARTICLE_SHARE`), so
  a related-article card does not displace the paper; else the first
  ``<main>`` or ``role="main"`` element, else ``<body>``, else (a frameset
  page, or a hidden body) the ``<html>`` element without its ``<head>``, which
  then holds no text. Text is measured in characters that are not white space.
* **Title**: the first ``<h1>`` with text the walk over the root reaches (not
  one inside a table row or another heading), which is then not a section;
  else ``<meta name="citation_title">``; else ``<title>``.
* **Sections**: ``<h1>`` to ``<h6>`` start a section at their level; content
  before the first heading is an untitled level-1 section.
* **Blocks**: a :data:`BLOCKS` element ends the running paragraph, inline
  elements and text run on, and ``<br>`` is a space. A ``<tr>`` is a table row
  of its ``<td>`` and ``<th>`` cells. The text of a heading or a cell puts a
  space where a block or a heading inside it begins and ends.

Whitespace is collapsed by :func:`assemble`, ``<pre>`` included. Decoding the
page's bytes is the caller's: this takes text, and refuses bytes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from time import monotonic
from typing import TYPE_CHECKING, Any

from bmlib.fulltext.canonical.model import (
    TOP_LEVEL,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
    is_white_space,
    normalise_whitespace,
)

if TYPE_CHECKING:
    from markupever.dom import Element

#: Bumped whenever this converter (or :func:`assemble`) can lay a page out
#: differently.
HTML_CANONICAL_VERSION = 1

#: The longest a page may take to parse, in seconds, before it is refused
#: (#563). html5ever is quadratic in nesting depth (measured with markupever:
#: ``<div>`` nested 40,000 deep, 429 KB, takes 2.6 s; a
#: 5 MB page nested that way about 5 minutes), while a 20 MB page shaped like
#: an article (60 sections, 2,400 paragraphs, tables, 15 MB of ``data:`` image)
#: parsed in 0.25 s, though :data:`HTML_MAX_CHARS` now refuses it (#656). The clock
#: is read between chunks, so a refusal comes one chunk late, and a chunk's own
#: time grows with the depth already open, which :data:`HTML_MAX_CHARS` bounds.
#: Wall-clock time, so a loaded machine can refuse a page an idle one accepts.
HTML_PARSE_SECONDS = 30.0

#: The longest page parsed, in code points; a longer one is refused unparsed
#: (#654). One chunk's time grows with the depth open before it, and a page can
#: open one element per three code points, so it grows with the page's length:
#: the worst measured, ``<i>`` or an unknown ``<x>`` nested to the page's
#: length and then a chunk of ``<hr>``, took about 8 s per million code points
#: here (markupever; 25 s at 4 million) and about twice that in the Rust port, a
#: loaded machine both. The cap is the slower language's: at this length a page
#: is converted or refused within about twice :data:`HTML_PARSE_SECONDS` there
#: (41 s and 60 s), and that chunk takes about 16 s here.
HTML_MAX_CHARS = 2_000_000

#: Code points fed to the parser between two readings of the clock. Smaller
#: chunks made markupever slower on the same page (4,096: 4.8 s against
#: 2.5 s at 16,384 for the 40,000-deep page, two runs that differ by load);
#: at 16,384 chunking costs nothing measurable (1.77 s fed in chunks and 1.77 s
#: in one piece, one later run), and the slowest chunk of that page takes 0.3 s.
HTML_PARSE_CHUNK = 16_384

#: The least share of the body's text the fullest ``<article>`` must hold to be
#: the root, as a fraction (``numerator / denominator``) so both languages
#: compare integers. Unmeasured: a starting point, like ``MIN_BODY_WORDS``.
MIN_ARTICLE_SHARE = (1, 2)

#: Elements dropped with their content: code, styling, navigation, forms,
#: embedded media and graphics, none of it the paper's prose. ``<noembed>``
#: and ``<noframes>`` are raw text to html5ever, so their content is markup.
PRUNED = frozenset(
    {
        "script",
        "style",
        "noscript",
        "noembed",
        "noframes",
        "template",
        "nav",
        "aside",
        "form",
        "button",
        "select",
        "textarea",
        "svg",
        "iframe",
        "object",
        "embed",
        "canvas",
        "audio",
        "video",
    }
)

#: Elements that end the running paragraph where they begin and end.
BLOCKS = frozenset(
    {
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
    }
)

#: Heading elements and the section level each starts.
HEADINGS = {f"h{level}": level for level in range(1, 7)}

#: A table row, and the cells it is read from.
_ROW = "tr"
_CELLS = frozenset({"td", "th"})

#: The meta tag a publisher names the article's title in (Highwire Press).
_CITATION_TITLE = "citation_title"


class HtmlPageTooLargeError(ValueError):
    """A page longer than :data:`HTML_MAX_CHARS` code points, refused unparsed.

    Attributes:
        length: the page's length in code points as passed, its leading
            byte-order mark included.
    """

    def __init__(self, length: int) -> None:
        super().__init__(
            f"the page is {length} characters long, more than the {HTML_MAX_CHARS} "
            "this converter parses"
        )
        self.length = length

    def __reduce__(self) -> tuple[Any, ...]:  # ``args`` holds the message, not the field
        return type(self), (self.length,), self.__dict__


class HtmlParseTimeoutError(ValueError):
    """A page html5ever was still parsing after :data:`HTML_PARSE_SECONDS`.

    Attributes:
        parsed: the code points fed to the parser before it was stopped.
        length: the page's length in code points after its leading byte-order
            mark is dropped.
    """

    def __init__(self, parsed: int, length: int) -> None:
        super().__init__(
            f"the page took longer than {HTML_PARSE_SECONDS:g} s to parse "
            f"(stopped after {parsed} of {length} characters); it may be nested too deeply"
        )
        self.parsed = parsed
        self.length = length

    def __reduce__(self) -> tuple[Any, ...]:  # ``args`` holds the message, not the fields
        return type(self), (self.parsed, self.length), self.__dict__


def canonical_from_html(html: str) -> CanonicalText:
    """Convert an HTML page to its canonical text (see the module docstring).

    Raises:
        TypeError: if *html* is not a ``str`` (decode the page's bytes first).
        ValueError: if *html* holds a lone surrogate, which no parser can read.
        HtmlPageTooLargeError: (a ``ValueError``) if *html* is longer than
            :data:`HTML_MAX_CHARS` code points.
        HtmlParseTimeoutError: (a ``ValueError``, so catch it first) if parsing
            the page takes longer than :data:`HTML_PARSE_SECONDS`. The clock is
            wall time, so the same page may parse on a less loaded machine.
        ImportError: if markupever, the ``html`` extra, is not installed.
    """
    if not isinstance(html, str):
        raise TypeError(f"canonical_from_html takes text, not {type(html).__name__}")
    if len(html) > HTML_MAX_CHARS:
        raise HtmlPageTooLargeError(len(html))
    try:
        html.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"the page holds a lone surrogate at index {e.start}") from e
    document = _parse(html)
    body = _first(document, lambda e: _name(e) == "body")
    walk = _Walk()
    sections = walk.read(_content_root(body if body is not None else document))
    return assemble(
        walk.title if walk.title is not None else _metadata_title(document),
        sections,
        SourceFormat.HTML,
        f"{SourceFormat.HTML.value}-{HTML_CANONICAL_VERSION}",
    )


def _parse(html: str) -> Element:
    """Parse *html* and return its ``<html>`` element.

    The page is fed in chunks of :data:`HTML_PARSE_CHUNK` code points, and
    before each chunk after the first the clock is read: past
    :data:`HTML_PARSE_SECONDS` the parse stops with :class:`HtmlParseTimeoutError`.

    Raises:
        HtmlParseTimeoutError: if the parse runs past :data:`HTML_PARSE_SECONDS`.
        ImportError: if markupever is not installed.
    """
    try:
        import markupever
        from markupever.dom import Element
    except ImportError as e:
        raise ImportError(
            f"markupever is required for HTML conversion ({e}). "
            "Install with: pip install bmlib[html]"
        ) from e
    # html5ever discards a U+FEFF at the start of every chunk, so it is told to
    # keep them and the page's own leading one is dropped here, once.
    html = html.removeprefix("\ufeff")
    parser = markupever.Parser(markupever.HtmlOptions(discard_bom=False))
    start = monotonic()
    for at in range(0, len(html), HTML_PARSE_CHUNK):
        if at and monotonic() - start > HTML_PARSE_SECONDS:
            raise HtmlParseTimeoutError(at, len(html))
        parser.process(html[at : at + HTML_PARSE_CHUNK])
    # html5ever always builds an <html> element, whatever the input.
    dom = parser.finish().into_dom()
    return next(n for n in dom.root().children() if isinstance(n, Element))


def _name(element: Element) -> str:
    """The element's local name, whatever its namespace (as scraper's ``name()``)."""
    return str(element.name.local)


def _attr(element: Element, name: str) -> str | None:
    """The value of *element*'s attribute *name* in no namespace (as scraper's ``attr``)."""
    return next((v for k, v in element.attrs.items() if k.local == name and not k.ns), None)


def _is_pruned(element: Element) -> bool:
    """Whether *element* and its content are dropped (:data:`PRUNED`, or ``hidden``)."""
    if _name(element) in PRUNED:
        return True
    hidden = _attr(element, "hidden")
    return hidden is not None and not (hidden.isascii() and hidden.lower() == "until-found")


#: What :func:`_edges` yields: an element opening or closing, or a text's content.
#: ``Any``, since mypy cannot tie *kind* to its payload once the pair is unpacked.
_OPEN, _CLOSE, _TEXT = "open", "close", "text"


def _edges(root: Element) -> Iterator[tuple[str, Any]]:
    """*root*'s content in document order, pruned subtrees and comments skipped.

    ``(_OPEN, element)`` and ``(_CLOSE, element)`` bracket each element below
    *root*, and ``(_TEXT, content)`` is a text. The tree is walked by its open
    and close edges (markupever's ``traverse``), never by recursion; ``depth``
    counts the edges open below *root*, so *root*'s own close ends the walk.
    """
    from markupever.dom import Element, Text

    edges = root.traverse()
    next(edges)  # root's own open edge
    depth = 0
    skipping = 0
    for edge in edges:
        if not edge.closed:
            depth += 1
        elif depth == 0:
            return
        else:
            depth -= 1
        node = edge.node
        if skipping:
            skipping += -1 if edge.closed else 1
        elif isinstance(node, Element):
            if not edge.closed and _is_pruned(node):
                skipping = 1
            else:
                yield (_CLOSE if edge.closed else _OPEN, node)
        elif isinstance(node, Text) and not edge.closed:
            yield (_TEXT, node.content)


def _elements(root: Element) -> Iterator[Element]:
    """*root*'s descendant elements in document order, pruned subtrees skipped."""
    return (node for kind, node in _edges(root) if kind == _OPEN)


def _first(root: Element, wanted: Callable[[Element], bool]) -> Element | None:
    """The first of :func:`_elements` for which *wanted* is true."""
    return next((e for e in _elements(root) if wanted(e)), None)


def _spaces_out(name: str) -> bool:
    """Whether a heading's or cell's text puts a space where element *name* begins and ends."""
    return name in BLOCKS or name in HEADINGS


def _raw_text(element: Element) -> str:
    """The text *element* holds, a space at each inner block's start and end and at ``<br>``."""
    parts: list[str] = []
    for kind, node in _edges(element):
        if kind == _TEXT:
            parts.append(node)
        elif (kind == _OPEN and _name(node) == "br") or _spaces_out(_name(node)):
            parts.append(" ")
    return "".join(parts)


def _text_of(element: Element) -> str:
    """:func:`_raw_text` with its whitespace collapsed."""
    return normalise_whitespace(_raw_text(element))


def _visible_length(text: str) -> int:
    """The characters of *text* that are not :func:`is_white_space`."""
    return sum(1 for ch in text if not is_white_space(ch))


def _content_root(body: Element) -> Element:
    """The element the paper is read from: the fullest article, else main, else *body*.

    One walk measures every article: a text counts towards the innermost open
    one, and an article's length towards the one enclosing it when it closes,
    so nested articles cost time linear in the page, not in their depth.
    """
    articles: list[Element] = []  # in document order
    lengths: list[int] = []
    open_articles: list[int] = []
    total = 0
    for kind, node in _edges(body):
        if kind == _TEXT:
            length = _visible_length(node)
            total += length
            if open_articles:
                lengths[open_articles[-1]] += length
        elif _name(node) == "article":
            if kind == _OPEN:
                open_articles.append(len(articles))
                articles.append(node)
                lengths.append(0)
            else:
                length = lengths[open_articles.pop()]
                if open_articles:
                    lengths[open_articles[-1]] += length
    if articles:
        # index(max) is the first of equal lengths, so the first article wins a tie.
        best = lengths.index(max(lengths))
        numerator, denominator = MIN_ARTICLE_SHARE
        if denominator * lengths[best] >= numerator * total:
            return articles[best]
    main = _first(body, lambda e: _name(e) == "main" or _attr(e, "role") == "main")
    return main if main is not None else body


def _metadata_title(document: Element) -> str:
    """The ``citation_title`` meta tag's content, else the ``<title>``'s text, else ``""``."""
    for element in _elements(document):
        if _name(element) == "meta" and (_attr(element, "name") or "").lower() == _CITATION_TITLE:
            content = normalise_whitespace(_attr(element, "content") or "")
            if content:
                return content
    title = _first(document, lambda e: _name(e) == "title")
    return _text_of(title) if title is not None else ""


class _Walk:
    """The section drafts a walk over the root builds, and its running paragraph."""

    def __init__(self) -> None:
        """Start with one untitled section and no title."""
        self.title: str | None = None
        self.sections = [SectionDraft("", TOP_LEVEL)]
        self.buffer: list[str] = []

    def flush(self) -> None:
        """End the running paragraph (``assemble`` drops it if it is blank)."""
        if self.buffer:
            self.sections[-1].blocks.append(BlockDraft.paragraph("".join(self.buffer)))
            self.buffer = []

    def read(self, root: Element) -> list[SectionDraft]:
        """Read *root*'s content into section drafts, in document order.

        A heading or a row is read whole where it opens (its own text, its
        cells), and the walk passes over its content to where it closes. The
        first ``<h1>`` with text it reaches is :attr:`title`, not a section.
        ``<head>``, reached only when the root is the ``<html>`` element, is
        passed over whole: its ``<title>`` is metadata, read only as the title.
        """
        inside = 0
        for kind, node in _edges(root):
            if inside:
                inside += {_OPEN: 1, _CLOSE: -1}.get(kind, 0)
                continue
            if kind == _TEXT:
                self.buffer.append(node)
                continue
            name = _name(node)
            if kind == _OPEN and name == "head":
                inside = 1
            elif kind == _OPEN and name == "br":
                self.buffer.append(" ")
            elif kind == _OPEN and name in HEADINGS:
                self.flush()
                heading = _text_of(node)
                if name == "h1" and self.title is None and heading:
                    self.title = heading
                else:
                    self.sections.append(SectionDraft(heading, HEADINGS[name]))
                inside = 1
            elif kind == _OPEN and name == _ROW:
                self.flush()
                cells = [_raw_text(c) for c in _cells(node)]
                self.sections[-1].blocks.append(BlockDraft.row(cells))
                inside = 1
            elif name in BLOCKS:
                self.flush()
        self.flush()
        return self.sections


def _cells(row: Element) -> list[Element]:
    """*row*'s ``<td>`` and ``<th>`` children that are not pruned."""
    from markupever.dom import Element

    return [
        c
        for c in row.children()
        if isinstance(c, Element) and _name(c) in _CELLS and not _is_pruned(c)
    ]
