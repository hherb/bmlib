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

The page is parsed by html5lib, a WHATWG parser, as the Rust port parses it
with html5ever, so a malformed page builds the same tree in both languages.
Scripting is on, as html5ever's default is, which decides how ``<noscript>``
parses. Then, by fixed rules rather than a score:

* **Pruned**: :data:`PRUNED` elements and any element with a ``hidden``
  attribute are dropped with everything inside them (text after them is kept).
* **Root**: the ``<article>`` holding the most text (the first on a tie), else
  the first ``<main>`` or ``role="main"`` element, else ``<body>``.
* **Title**: the root's first ``<h1>`` with text, which is then not a section;
  else ``<meta name="citation_title">``; else ``<title>``.
* **Sections**: ``<h1>`` to ``<h6>`` start a section at their level; content
  before the first heading is an untitled level-1 section.
* **Blocks**: a :data:`BLOCKS` element ends the running paragraph, inline
  elements and text run on, and ``<br>`` is a space. A ``<tr>`` is a table row
  of its ``<td>`` and ``<th>`` cells. The text of a heading or a cell puts a
  space where a block inside it begins and ends.

Whitespace is collapsed by :func:`assemble`, ``<pre>`` included. Decoding the
page's bytes is the caller's: this takes text.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from bmlib.fulltext.canonical.model import (
    TOP_LEVEL,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
    normalise_whitespace,
)

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

#: Bumped whenever this converter (or :func:`assemble`) can lay a page out
#: differently.
HTML_CANONICAL_VERSION = 1

#: Elements dropped with their content: code, styling, navigation, forms,
#: embedded media and graphics, none of it the paper's prose.
PRUNED = frozenset(
    {
        "script",
        "style",
        "noscript",
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


def canonical_from_html(html: str) -> CanonicalText:
    """Convert an HTML page to its canonical text (see the module docstring).

    Raises:
        ImportError: if html5lib, the ``html`` extra, is not installed.
    """
    document = _parse(html)
    body = _first(document, lambda e: _name(e) == "body")
    root = _content_root(body if body is not None else document)
    title_heading = _first(root, lambda e: _name(e) == "h1" and bool(_text_of(e)))
    title = _text_of(title_heading) if title_heading is not None else _metadata_title(document)

    walk = _Walk(skip=title_heading)
    walk.visit(root)
    walk.flush()
    return assemble(
        title,
        walk.sections,
        SourceFormat.HTML,
        f"{SourceFormat.HTML.value}-{HTML_CANONICAL_VERSION}",
    )


def _parse(html: str) -> Element:
    """Parse *html* into an ElementTree, HTML elements without a namespace."""
    try:
        import html5lib
    except ImportError as e:
        raise ImportError(
            f"html5lib is required for HTML conversion ({e}). Install with: pip install bmlib[html]"
        ) from e
    tree: Element = html5lib.parse(html, namespaceHTMLElements=False, scripting=True)
    return tree


def _name(element: Element) -> str:
    """The element's local name; ``""`` for a comment, whose tag is not a string.

    html5lib names a foreign (SVG, MathML) element ``{namespace}name``.
    """
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _is_pruned(element: Element) -> bool:
    """Whether *element* and its content are dropped (:data:`PRUNED`, or ``hidden``)."""
    return _name(element) in PRUNED or element.get("hidden") is not None


def _elements(root: Element) -> Iterator[Element]:
    """*root*'s descendant elements in document order, pruned subtrees skipped."""
    for child in root:
        if not _name(child) or _is_pruned(child):
            continue
        yield child
        yield from _elements(child)


def _first(root: Element, wanted: Callable[[Element], bool]) -> Element | None:
    """The first of :func:`_elements` for which *wanted* is true."""
    return next((e for e in _elements(root) if wanted(e)), None)


def _raw_text(element: Element) -> str:
    """The text *element* holds, a space at each inner block's start and end."""
    parts = [element.text or ""]
    for child in element:
        name = _name(child)
        if name and not _is_pruned(child):
            if name == "br":
                parts.append(" ")
            elif name in BLOCKS or name in HEADINGS:
                parts.extend((" ", _raw_text(child), " "))
            else:
                parts.append(_raw_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _text_of(element: Element) -> str:
    """:func:`_raw_text` with its whitespace collapsed."""
    return normalise_whitespace(_raw_text(element))


def _content_root(body: Element) -> Element:
    """The element the paper is read from: the fullest article, else main, else *body*."""
    best: Element | None = None
    best_length = -1
    for element in _elements(body):
        if _name(element) == "article":
            length = len(_text_of(element))
            if length > best_length:
                best, best_length = element, length
    if best is not None:
        return best
    main = _first(body, lambda e: _name(e) == "main" or e.get("role") == "main")
    return main if main is not None else body


def _metadata_title(document: Element) -> str:
    """The ``citation_title`` meta tag's content, else the ``<title>``'s text, else ``""``."""
    for element in _elements(document):
        if _name(element) == "meta" and (element.get("name") or "").lower() == _CITATION_TITLE:
            content = normalise_whitespace(element.get("content") or "")
            if content:
                return content
    title = _first(document, lambda e: _name(e) == "title")
    return _text_of(title) if title is not None else ""


class _Walk:
    """The section drafts a walk over the root builds, and its running paragraph."""

    def __init__(self, skip: Element | None) -> None:
        """Start with one untitled section; *skip* is the heading taken as the title."""
        self.skip = skip
        self.sections = [SectionDraft("", TOP_LEVEL)]
        self.buffer: list[str] = []

    def flush(self) -> None:
        """End the running paragraph (``assemble`` drops it if it is blank)."""
        if self.buffer:
            self.sections[-1].blocks.append(BlockDraft.paragraph("".join(self.buffer)))
            self.buffer = []

    def visit(self, element: Element) -> None:
        """Read *element*'s content into the drafts, in document order."""
        self.buffer.append(element.text or "")
        for child in element:
            name = _name(child)
            if name and not _is_pruned(child):
                self.child(child, name)
            self.buffer.append(child.tail or "")

    def child(self, element: Element, name: str) -> None:
        """Read one child element, by what it is."""
        if name == "br":
            self.buffer.append(" ")
        elif name in HEADINGS:
            self.flush()
            if element is not self.skip:
                self.sections.append(SectionDraft(_text_of(element), HEADINGS[name]))
        elif name == _ROW:
            self.flush()
            cells = [_raw_text(c) for c in element if _name(c) in _CELLS and not _is_pruned(c)]
            self.sections[-1].blocks.append(BlockDraft.row(cells))
        elif name in BLOCKS:
            self.flush()
            self.visit(element)
            self.flush()
        else:
            self.visit(element)
