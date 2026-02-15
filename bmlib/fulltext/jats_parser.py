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

"""JATS XML parser — Python port of the Swift JATSXMLParser.

Uses xml.sax (event-driven SAX), same pattern as Swift's XMLParserDelegate.
Handles article metadata, structured abstracts, body sections with nesting,
figures, tables, references, and inline formatting.
"""

from __future__ import annotations

import re
import xml.sax
import xml.sax.handler
from dataclasses import dataclass, field
from html import escape as html_escape
from io import BytesIO

from bmlib.fulltext.models import (
    JATSAbstractSection,
    JATSArticle,
    JATSAuthorInfo,
    JATSBodySection,
    JATSFigureInfo,
    JATSReferenceInfo,
    JATSTableInfo,
)

MAX_HEADING_LEVEL = 6


# ---------------------------------------------------------------------------
# Builder helpers (internal, mirror Swift builders)
# ---------------------------------------------------------------------------


@dataclass
class _AuthorBuilder:
    surname: str = ""
    given_names: str = ""
    affiliations: list[str] = field(default_factory=list)

    def build(self) -> JATSAuthorInfo | None:
        if not self.surname:
            return None
        return JATSAuthorInfo(
            surname=self.surname,
            given_names=self.given_names,
            affiliations=list(self.affiliations),
        )


@dataclass
class _SectionBuilder:
    title: str = ""
    paragraphs: list[str] = field(default_factory=list)
    subsections: list[JATSBodySection] = field(default_factory=list)

    def build(self) -> JATSBodySection:
        return JATSBodySection(
            title=self.title,
            paragraphs=list(self.paragraphs),
            subsections=list(self.subsections),
        )


@dataclass
class _FigureBuilder:
    id: str = ""
    label: str = ""
    caption: str = ""
    graphic_href: str = ""

    def build(self) -> JATSFigureInfo:
        return JATSFigureInfo(
            id=self.id,
            label=self.label,
            caption=self.caption,
            graphic_url=self.graphic_href or None,
        )


@dataclass
class _TableBuilder:
    id: str = ""
    label: str = ""
    caption: str = ""
    header_rows: list[list[str]] = field(default_factory=list)
    body_rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] = field(default_factory=list)
    current_cell_text: str = ""
    in_header: bool = False
    in_body: bool = False
    in_row: bool = False
    in_cell: bool = False
    current_row_has_header_cells: bool = False
    current_colspan: int = 1

    def start_header(self) -> None:
        self.in_header = True
        self.in_body = False

    def end_header(self) -> None:
        self.in_header = False

    def start_body(self) -> None:
        self.in_body = True
        self.in_header = False

    def end_body(self) -> None:
        self.in_body = False

    def start_row(self) -> None:
        self.in_row = True
        self.current_row = []
        self.current_row_has_header_cells = False

    def end_row(self) -> None:
        if self.in_row and self.current_row:
            if self.in_header or (
                self.current_row_has_header_cells
                and not self.in_body
                and not self.header_rows
            ):
                self.header_rows.append(self.current_row)
            else:
                self.body_rows.append(self.current_row)
        self.in_row = False
        self.current_row = []
        self.current_row_has_header_cells = False

    def start_cell(self, is_header: bool = False, colspan: int = 1) -> None:
        self.in_cell = True
        self.current_cell_text = ""
        self.current_colspan = max(1, colspan)
        if is_header or self.in_header:
            self.current_row_has_header_cells = True

    def end_cell(self) -> None:
        if self.in_cell:
            normalized = _normalize_whitespace(self.current_cell_text)
            self.current_row.append(normalized)
            for _ in range(1, self.current_colspan):
                self.current_row.append("")
        self.in_cell = False
        self.current_cell_text = ""
        self.current_colspan = 1

    def append_cell_text(self, text: str) -> None:
        if self.in_cell:
            self.current_cell_text += text.replace("\n", " ").replace("\r", " ")

    def build(self) -> JATSTableInfo:
        return JATSTableInfo(
            id=self.id,
            label=self.label,
            caption=self.caption,
            html_content=self._build_html_table(),
        )

    def _build_html_table(self) -> str:
        if not self.header_rows and not self.body_rows:
            return ""
        col_count = max(
            len(self.header_rows[0]) if self.header_rows else 0,
            len(self.body_rows[0]) if self.body_rows else 0,
        )
        if col_count == 0:
            return ""
        parts: list[str] = ["<table>"]
        if self.header_rows:
            parts.append("  <thead>")
            for row in self.header_rows:
                parts.append("    <tr>")
                for cell in _pad_row(row, col_count):
                    parts.append(f"      <th>{html_escape(cell)}</th>")
                parts.append("    </tr>")
            parts.append("  </thead>")
        parts.append("  <tbody>")
        for row in self.body_rows:
            parts.append("    <tr>")
            for cell in _pad_row(row, col_count):
                parts.append(f"      <td>{html_escape(cell)}</td>")
            parts.append("    </tr>")
        parts.append("  </tbody>")
        parts.append("</table>")
        return "\n".join(parts)


@dataclass
class _ReferenceBuilder:
    id: str = ""
    label: str = ""
    citation: str = ""
    authors: list[str] = field(default_factory=list)
    current_author_surname: str = ""
    current_author_given_names: str = ""
    article_title: str = ""
    source: str = ""
    year: str = ""
    volume: str = ""
    issue: str = ""
    first_page: str = ""
    last_page: str = ""
    doi: str = ""
    pmid: str = ""

    def finish_current_author(self) -> None:
        if self.current_author_surname:
            name = self.current_author_surname
            if self.current_author_given_names:
                name = f"{self.current_author_given_names} {name}"
            self.authors.append(name)
            self.current_author_surname = ""
            self.current_author_given_names = ""

    def build(self) -> JATSReferenceInfo:
        return JATSReferenceInfo(
            id=self.id,
            label=self.label,
            citation=self.citation,
            authors=list(self.authors),
            article_title=self.article_title,
            source=self.source,
            year=self.year,
            volume=self.volume,
            issue=self.issue,
            first_page=self.first_page,
            last_page=self.last_page,
            doi=self.doi,
            pmid=self.pmid,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _pad_row(row: list[str], count: int) -> list[str]:
    if len(row) >= count:
        return row[:count]
    return row + [""] * (count - len(row))


# Elements that accumulate their own text content (push a new text buffer).
_TEXT_ACCUMULATING = frozenset(
    {
        "p",
        "title",
        "article-title",
        "abstract",
        "sec",
        "surname",
        "given-names",
        "journal-title",
        "volume",
        "issue",
        "fpage",
        "lpage",
        "year",
        "article-id",
        "label",
        "mixed-citation",
        "element-citation",
        "caption",
        "bold",
        "b",
        "italic",
        "i",
        "sub",
        "sup",
        "monospace",
        "code",
        "xref",
        "ext-link",
        "uri",
        "email",
        "named-content",
        "list-item",
        "def",
        "term",
        "kwd",
        "alt-title",
        "inline-formula",
        "disp-formula",
        "tex-math",
        "source",
        "person-group",
        "pub-id",
        "collab",
    }
)

_INLINE_ELEMENTS = frozenset(
    {
        "bold",
        "b",
        "italic",
        "i",
        "sub",
        "sup",
        "monospace",
        "code",
        "xref",
        "ext-link",
        "uri",
        "email",
        "named-content",
        "inline-formula",
    }
)


# ---------------------------------------------------------------------------
# SAX Handler
# ---------------------------------------------------------------------------


class _JATSHandler(xml.sax.handler.ContentHandler):
    """SAX content handler that mirrors the Swift XMLParserDelegate logic."""

    def __init__(self, known_pmc_id: str = "") -> None:
        super().__init__()

        # Parsed content
        self.title = ""
        self.authors: list[JATSAuthorInfo] = []
        self.journal = ""
        self.volume = ""
        self.issue = ""
        self.pages = ""
        self.year = ""
        self.doi = ""
        self.pmc_id = known_pmc_id
        self.pmid = ""
        self.abstract_sections: list[JATSAbstractSection] = []
        self.body_sections: list[JATSBodySection] = []
        self.figures: list[JATSFigureInfo] = []
        self.tables: list[JATSTableInfo] = []
        self.references: list[JATSReferenceInfo] = []

        # Parsing state
        self.element_stack: list[str] = []
        self.text_stack: list[str] = [""]

        # Article metadata state
        self.in_front = False
        self.in_article_meta = False
        self.in_contrib_group = False
        self.in_contrib = False
        self.current_article_id_type: str | None = None
        self.current_author: _AuthorBuilder | None = None

        # Abstract state
        self.in_abstract = False
        self.current_abstract_title = ""
        self.current_abstract_text: list[str] = []

        # Body / back state
        self.in_body = False
        self.in_back = False
        self.section_stack: list[_SectionBuilder] = []

        # Figure / table state
        self.in_figure = False
        self.in_table_wrap = False
        self.current_figure: _FigureBuilder | None = None
        self.current_table: _TableBuilder | None = None

        # Reference state
        self.in_ref_list = False
        self.in_ref = False
        self.in_ref_citation = False
        self.in_ref_person_group = False
        self.current_reference: _ReferenceBuilder | None = None

        # Cross-reference state
        self.current_xref_type: str | None = None
        self.current_xref_rid: str | None = None

    # -- Text stack helpers --------------------------------------------------

    @property
    def current_text(self) -> str:
        return self.text_stack[-1] if self.text_stack else ""

    def _append_text(self, text: str) -> None:
        if self.text_stack:
            self.text_stack[-1] += text

    def _push_text_buffer(self) -> None:
        self.text_stack.append("")

    def _pop_text_buffer(self, merge_with_parent: bool = False) -> str:
        if len(self.text_stack) <= 1:
            text = self.text_stack[0] if self.text_stack else ""
            if self.text_stack:
                self.text_stack[0] = ""
            return text
        text = self.text_stack.pop()
        if merge_with_parent and text and self.text_stack:
            self.text_stack[-1] += text
        return text

    # -- SAX events ----------------------------------------------------------

    def startElement(self, name: str, attrs: xml.sax.xmlreader.AttributesImpl) -> None:
        self.element_stack.append(name)

        if name in _TEXT_ACCUMULATING:
            self._push_text_buffer()

        if name == "front":
            self.in_front = True
        elif name == "article-meta":
            self.in_article_meta = True
        elif name == "contrib-group":
            self.in_contrib_group = True
        elif name == "contrib":
            if attrs.get("contrib-type") == "author":
                self.in_contrib = True
                self.current_author = _AuthorBuilder()
        elif name == "abstract":
            self.in_abstract = True
            self.current_abstract_title = ""
            self.current_abstract_text = []
        elif name == "body":
            self.in_body = True
        elif name == "back":
            self.in_back = True
        elif name == "sec":
            if not self.in_abstract:
                self.section_stack.append(_SectionBuilder())
        elif name == "fig":
            self.in_figure = True
            self.current_figure = _FigureBuilder(id=attrs.get("id", ""))
        elif name == "graphic":
            if self.in_figure and self.current_figure is not None:
                href = (
                    attrs.get("xlink:href")
                    or attrs.get("href")
                    or attrs.get("xlink-href")
                    or ""
                )
                if href:
                    self.current_figure.graphic_href = href
        elif name == "table-wrap":
            self.in_table_wrap = True
            self.current_table = _TableBuilder(id=attrs.get("id", ""))
        elif name == "thead":
            if self.in_table_wrap and self.current_table:
                self.current_table.start_header()
        elif name == "tbody":
            if self.in_table_wrap and self.current_table:
                self.current_table.start_body()
        elif name == "tr":
            if self.in_table_wrap and self.current_table:
                self.current_table.start_row()
        elif name == "th":
            if self.in_table_wrap and self.current_table:
                colspan = int(attrs.get("colspan", "1") or "1")
                self.current_table.start_cell(is_header=True, colspan=colspan)
        elif name == "td":
            if self.in_table_wrap and self.current_table:
                colspan = int(attrs.get("colspan", "1") or "1")
                self.current_table.start_cell(is_header=False, colspan=colspan)
        elif name == "ref-list":
            self.in_ref_list = True
        elif name == "ref":
            self.in_ref = True
            self.current_reference = _ReferenceBuilder(id=attrs.get("id", ""))
        elif name in ("mixed-citation", "element-citation"):
            if self.in_ref:
                self.in_ref_citation = True
        elif name == "person-group":
            if self.in_ref_citation:
                self.in_ref_person_group = True
        elif name == "article-id":
            self.current_article_id_type = attrs.get("pub-id-type")
        elif name == "xref":
            self.current_xref_type = attrs.get("ref-type")
            self.current_xref_rid = attrs.get("rid")

    def characters(self, content: str) -> None:
        self._append_text(content)
        if self.in_table_wrap and self.current_table:
            self.current_table.append_cell_text(content)

    def endElement(self, name: str) -> None:
        # Pop text buffer
        if name in _TEXT_ACCUMULATING:
            is_inline = name in _INLINE_ELEMENTS
            is_fig_table_xref = name == "xref" and self.current_xref_type in (
                "fig",
                "figure",
                "table",
                "table-wrap",
            )
            element_text = self._pop_text_buffer(
                merge_with_parent=is_inline and not is_fig_table_xref
            )
        else:
            element_text = self.current_text

        text = element_text.strip()
        normalized_text = _normalize_whitespace(element_text)

        # --- Handle element end ---

        if name == "front":
            self.in_front = False
        elif name == "article-meta":
            self.in_article_meta = False
        elif name == "contrib-group":
            self.in_contrib_group = False
        elif name == "contrib":
            if self.in_contrib and self.current_author:
                author = self.current_author.build()
                if author:
                    self.authors.append(author)
            self.in_contrib = False
            self.current_author = None

        elif name == "journal-title":
            if self.in_front:
                self.journal = text
        elif name == "article-id":
            parent = self.element_stack[-2] if len(self.element_stack) >= 2 else ""
            if parent == "article-meta" or self.in_front:
                if self.current_article_id_type:
                    id_type = self.current_article_id_type.lower()
                    if id_type == "doi":
                        self.doi = text
                    elif id_type in ("pmc", "pmcid"):
                        self.pmc_id = text
                    elif id_type in ("pmid", "pubmed"):
                        self.pmid = text
                    else:
                        self._classify_article_id(text)
                else:
                    self._classify_article_id(text)
                self.current_article_id_type = None

        elif name == "abstract":
            if self.current_abstract_text:
                content = " ".join(self.current_abstract_text)
                self.abstract_sections.append(
                    JATSAbstractSection(title=self.current_abstract_title, content=content)
                )
            self.in_abstract = False
        elif name == "title":
            if self.in_abstract:
                if self.current_abstract_text:
                    content = " ".join(self.current_abstract_text)
                    self.abstract_sections.append(
                        JATSAbstractSection(
                            title=self.current_abstract_title, content=content
                        )
                    )
                    self.current_abstract_text = []
                self.current_abstract_title = text
            elif self.section_stack:
                self.section_stack[-1].title = normalized_text
        elif name == "p":
            if self.in_abstract:
                if normalized_text:
                    self.current_abstract_text.append(normalized_text)
            elif (self.in_body or self.in_back) and self.section_stack:
                self.section_stack[-1].paragraphs.append(normalized_text)
            elif self.in_figure and self.current_figure:
                self.current_figure.caption += normalized_text
            elif self.in_table_wrap and self.current_table:
                self.current_table.caption += normalized_text

        elif name == "body":
            self.in_body = False
        elif name == "back":
            self.in_back = False
        elif name == "sec":
            if not self.in_abstract and self.section_stack:
                builder = self.section_stack.pop()
                section = builder.build()
                if self.section_stack:
                    self.section_stack[-1].subsections.append(section)
                else:
                    self.body_sections.append(section)

        elif name == "fig":
            if self.current_figure:
                self.figures.append(self.current_figure.build())
            self.in_figure = False
            self.current_figure = None
        elif name == "label":
            if self.in_figure and self.current_figure:
                self.current_figure.label = text
            elif self.in_table_wrap and self.current_table:
                self.current_table.label = text
            elif self.in_ref and self.current_reference:
                self.current_reference.label = text

        elif name == "thead":
            if self.in_table_wrap and self.current_table:
                self.current_table.end_header()
        elif name == "tbody":
            if self.in_table_wrap and self.current_table:
                self.current_table.end_body()
        elif name == "tr":
            if self.in_table_wrap and self.current_table:
                self.current_table.end_row()
        elif name in ("th", "td"):
            if self.in_table_wrap and self.current_table:
                self.current_table.end_cell()
        elif name == "table-wrap":
            if self.current_table:
                self.tables.append(self.current_table.build())
            self.in_table_wrap = False
            self.current_table = None

        elif name == "ref-list":
            self.in_ref_list = False
        elif name == "ref":
            if self.current_reference:
                self.current_reference.finish_current_author()
                self.references.append(self.current_reference.build())
            self.in_ref = False
            self.in_ref_citation = False
            self.in_ref_person_group = False
            self.current_reference = None
        elif name in ("mixed-citation", "element-citation"):
            if self.in_ref and self.current_reference:
                self.current_reference.citation = normalized_text
                self.in_ref_citation = False
        elif name == "person-group":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.finish_current_author()
                self.in_ref_person_group = False
        elif name == "surname":
            if self.in_ref_person_group and self.current_reference:
                self.current_reference.current_author_surname = text
            elif self.in_contrib and self.current_author:
                self.current_author.surname = text
        elif name == "given-names":
            if self.in_ref_person_group and self.current_reference:
                self.current_reference.current_author_given_names = text
            elif self.in_contrib and self.current_author:
                self.current_author.given_names = text
        elif name == "name":
            if self.in_ref_person_group and self.current_reference:
                self.current_reference.finish_current_author()
        elif name == "collab":
            if self.in_ref_citation and self.current_reference and text:
                self.current_reference.authors.append(text)
        elif name == "article-title":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.article_title = normalized_text
            elif self.in_front and self.in_article_meta:
                self.title = normalized_text
        elif name == "source":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.source = text
        elif name == "year":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.year = text
            elif self.in_front and self.in_article_meta and not self.year:
                self.year = text
        elif name == "volume":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.volume = text
            elif self.in_front and self.in_article_meta:
                self.volume = text
        elif name == "issue":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.issue = text
            elif self.in_front and self.in_article_meta:
                self.issue = text
        elif name == "fpage":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.first_page = text
            elif self.in_front and self.in_article_meta and not self.pages:
                self.pages = text
        elif name == "lpage":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.last_page = text
            elif self.in_front and self.in_article_meta and self.pages and text:
                self.pages += f"-{text}"
        elif name == "pub-id":
            if self.in_ref_citation and self.current_reference:
                if text.startswith("10."):
                    self.current_reference.doi = text
                elif text.isdigit() and len(text) >= 7:
                    self.current_reference.pmid = text

        elif name == "xref":
            if self.current_xref_type and self.current_xref_rid:
                if self.current_xref_type in ("fig", "figure"):
                    link_text = text or "Figure"
                    self._append_text(f"[{link_text}](#{self.current_xref_rid})")
                elif self.current_xref_type in ("table", "table-wrap"):
                    link_text = text or "Table"
                    self._append_text(f"[{link_text}](#{self.current_xref_rid})")
            self.current_xref_type = None
            self.current_xref_rid = None

        # Pop element stack
        if self.element_stack:
            self.element_stack.pop()

    def _classify_article_id(self, text: str) -> None:
        if text.startswith("10."):
            self.doi = text
        elif text.startswith("PMC"):
            self.pmc_id = text
        elif text.isdigit() and len(text) >= 7:
            if not self.pmid:
                self.pmid = text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class JATSParser:
    """Parse JATS XML to structured data or HTML.

    Usage::

        parser = JATSParser(xml_bytes)
        article = parser.parse()       # -> JATSArticle
        html     = parser.to_html()    # -> str
    """

    def __init__(self, data: bytes, known_pmc_id: str = "") -> None:
        self._data = data
        pmc_id = known_pmc_id
        if pmc_id and not pmc_id.startswith("PMC"):
            pmc_id = f"PMC{pmc_id}"
        self._known_pmc_id = pmc_id

    def _run_parser(self) -> _JATSHandler:
        handler = _JATSHandler(known_pmc_id=self._known_pmc_id)
        parser = xml.sax.make_parser()
        parser.setContentHandler(handler)
        # Disable external entity loading for security
        parser.setFeature(xml.sax.handler.feature_external_ges, False)
        parser.setFeature(xml.sax.handler.feature_external_pes, False)
        parser.parse(BytesIO(self._data))
        return handler

    def parse(self) -> JATSArticle:
        """Parse XML and return structured article data."""
        h = self._run_parser()
        return JATSArticle(
            title=h.title,
            authors=h.authors,
            journal=h.journal,
            volume=h.volume,
            issue=h.issue,
            pages=h.pages,
            year=h.year,
            doi=h.doi,
            pmc_id=h.pmc_id,
            pmid=h.pmid,
            abstract_sections=h.abstract_sections,
            body_sections=h.body_sections,
            figures=h.figures,
            tables=h.tables,
            references=h.references,
        )

    def to_html(self) -> str:
        """Parse XML and return HTML string."""
        h = self._run_parser()
        return _build_html(h)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------


def _build_html(h: _JATSHandler) -> str:
    parts: list[str] = []

    # Title
    if h.title:
        parts.append(f"<h1>{html_escape(h.title)}</h1>")

    # Authors
    if h.authors:
        names = [a.full_name for a in h.authors]
        if len(names) <= 5:
            author_str = ", ".join(names)
        else:
            author_str = ", ".join(names[:5]) + " et al."
        parts.append(f'<p class="authors"><strong>Authors:</strong> {html_escape(author_str)}</p>')

    # Journal info
    journal_html = _format_journal_html(h)
    if journal_html:
        parts.append(f'<p class="journal-info">{journal_html}</p>')

    # Identifiers
    ids_html = _format_identifiers_html(h)
    if ids_html:
        parts.append(f'<p class="identifiers">{ids_html}</p>')

    # Abstract
    if h.abstract_sections:
        parts.append("<h2>Abstract</h2>")
        for sec in h.abstract_sections:
            if sec.title:
                parts.append(
                    f"<p><strong>{html_escape(sec.title)}:</strong> {html_escape(sec.content)}</p>"
                )
            else:
                parts.append(f"<p>{html_escape(sec.content)}</p>")

    # Body sections
    for sec in h.body_sections:
        parts.extend(_format_body_section_html(sec, level=2))

    # Figures
    if h.figures:
        parts.append("<h2>Figures</h2>")
        for i, fig in enumerate(h.figures):
            fig_num = fig.label or f"Figure {i + 1}"
            anchor_id = fig.id or f"fig{i + 1}"
            parts.append(f'<figure id="{html_escape(anchor_id)}">')
            if fig.graphic_url:
                full_url = _build_figure_url(fig.graphic_url, h.pmc_id)
                parts.append(
                    f'  <img src="{html_escape(full_url)}" '
                    f'alt="{html_escape(fig_num)}" loading="lazy">'
                )
            parts.append("  <figcaption>")
            parts.append(f"    <strong>{html_escape(fig_num)}</strong>")
            if fig.caption:
                parts.append(f"    <p>{html_escape(fig.caption)}</p>")
            parts.append("  </figcaption>")
            parts.append("</figure>")

    # Tables
    if h.tables:
        parts.append("<h2>Tables</h2>")
        for i, tbl in enumerate(h.tables):
            tbl_num = tbl.label or f"Table {i + 1}"
            anchor_id = tbl.id or f"table{i + 1}"
            parts.append(f'<div class="table-container" id="{html_escape(anchor_id)}">')
            parts.append(f"  <h3>{html_escape(tbl_num)}</h3>")
            if tbl.caption:
                parts.append(f'  <p class="table-caption">{html_escape(tbl.caption)}</p>')
            if tbl.html_content:
                parts.append(tbl.html_content)
            parts.append("</div>")

    # References
    if h.references:
        parts.append("<h2>References</h2>")
        parts.append('<ol class="references">')
        for ref in h.references:
            parts.append(f'  <li id="ref-{html_escape(ref.id)}">{_format_ref_html(ref)}</li>')
        parts.append("</ol>")

    return "\n".join(parts)


def _format_journal_html(h: _JATSHandler) -> str:
    parts: list[str] = []
    if h.journal:
        parts.append(f"<em>{html_escape(h.journal)}</em>")
    vol_parts: list[str] = []
    if h.volume:
        vol_parts.append(h.volume)
    if h.issue:
        vol_parts.append(f"({h.issue})")
    if h.pages:
        vol_parts.append(f": {h.pages}")
    if vol_parts:
        parts.append(html_escape("".join(vol_parts)))
    if h.year:
        parts.append(f"({html_escape(h.year)})")
    return " ".join(parts)


def _format_identifiers_html(h: _JATSHandler) -> str:
    ids: list[str] = []
    if h.doi:
        ids.append(
            f'DOI: <a href="https://doi.org/{html_escape(h.doi)}">{html_escape(h.doi)}</a>'
        )
    if h.pmc_id:
        pmc_num = h.pmc_id[3:] if h.pmc_id.startswith("PMC") else h.pmc_id
        ids.append(
            f'PMC: <a href="https://europepmc.org/article/PMC/{html_escape(pmc_num)}">'
            f"{html_escape(h.pmc_id)}</a>"
        )
    if h.pmid:
        ids.append(
            f'PMID: <a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(h.pmid)}/">'
            f"{html_escape(h.pmid)}</a>"
        )
    return " | ".join(ids)


def _format_body_section_html(section: JATSBodySection, level: int) -> list[str]:
    parts: list[str] = []
    heading = min(level, MAX_HEADING_LEVEL)
    if section.title:
        parts.append(f"<h{heading}>{html_escape(section.title)}</h{heading}>")
    for para in section.paragraphs:
        if para:
            html_para = _convert_inline_links(para)
            parts.append(f"<p>{html_para}</p>")
    for sub in section.subsections:
        parts.extend(_format_body_section_html(sub, level + 1))
    return parts


def _build_figure_url(path: str, pmc_id: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    has_ext = any(path.lower().endswith(ext) for ext in (".gif", ".jpg", ".jpeg", ".png", ".svg"))
    if pmc_id:
        normalized = pmc_id if pmc_id.startswith("PMC") else f"PMC{pmc_id}"
        base = f"https://europepmc.org/articles/{normalized}/bin/{path}"
        return base if has_ext else base + ".jpg"
    return path


def _format_ref_html(ref: JATSReferenceInfo) -> str:
    parts: list[str] = []
    if ref.authors:
        if len(ref.authors) <= 3:
            parts.append(html_escape(", ".join(ref.authors)))
        else:
            parts.append(html_escape(f"{ref.authors[0]}, {ref.authors[1]}, et al."))
    if ref.article_title:
        parts.append(html_escape(ref.article_title))
    if ref.source:
        parts.append(f"<em>{html_escape(ref.source)}</em>")
    if ref.year:
        parts.append(f"({html_escape(ref.year)})")
    vol = ""
    if ref.volume:
        vol = ref.volume
        if ref.issue:
            vol += f"({ref.issue})"
    if ref.first_page:
        if vol:
            vol += ":"
        vol += ref.first_page
        if ref.last_page:
            vol += f"-{ref.last_page}"
    if vol:
        parts.append(html_escape(vol))
    if ref.doi:
        parts.append(
            f'<a href="https://doi.org/{html_escape(ref.doi)}">doi:{html_escape(ref.doi)}</a>'
        )
    if not parts:
        return html_escape(ref.citation)
    return ". ".join(parts)


_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _convert_inline_links(text: str) -> str:
    """Convert markdown-style [text](#anchor) to HTML <a> tags, escaping the rest."""

    result: list[str] = []
    last_end = 0
    for m in _LINK_RE.finditer(text):
        result.append(html_escape(text[last_end : m.start()]))
        link_text = m.group(1)
        href = m.group(2)
        result.append(f'<a href="{html_escape(href)}">{html_escape(link_text)}</a>')
        last_end = m.end()
    result.append(html_escape(text[last_end:]))
    return "".join(result)
