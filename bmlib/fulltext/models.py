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

"""Data models for full-text retrieval, JATS XML parsing, and PDF section
segmentation.

The full-text and JATS types mirror the Swift BioMedLit library's
JATSModels and FullTextResult types. The PDF section-segmentation types —
``SectionType``, ``TextBlock``, ``Section``, ``SegmentedDocument`` — are new
to this port and mirror nothing in Swift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# What a :attr:`FullTextResult.html` payload actually is. See the field's
# comment on :class:`FullTextResult` for what each value promises.
ContentKind = Literal["none", "abstract", "extracted", "fulltext"]


@dataclass
class JATSAuthorInfo:
    """Parsed author information from a JATS article."""

    surname: str
    given_names: str = ""
    affiliations: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        if not self.given_names:
            return self.surname
        return f"{self.given_names} {self.surname}"


@dataclass
class JATSAbstractSection:
    """Parsed abstract section (e.g. Background, Methods)."""

    title: str
    content: str


@dataclass
class JATSBodySection:
    """Parsed body section with nested subsections."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    subsections: list[JATSBodySection] = field(default_factory=list)


@dataclass
class JATSFigureInfo:
    """Parsed figure metadata."""

    id: str
    label: str
    caption: str
    graphic_url: str | None = None


@dataclass
class JATSTableInfo:
    """Parsed table metadata with pre-rendered HTML content."""

    id: str
    label: str
    caption: str
    html_content: str = ""


@dataclass
class JATSReferenceInfo:
    """Parsed reference/citation information."""

    id: str
    label: str
    citation: str
    authors: list[str] = field(default_factory=list)
    article_title: str = ""
    source: str = ""
    year: str = ""
    volume: str = ""
    issue: str = ""
    first_page: str = ""
    last_page: str = ""
    doi: str = ""
    pmid: str = ""

    @property
    def formatted_citation(self) -> str:
        parts: list[str] = []
        if self.authors:
            if len(self.authors) <= 3:
                parts.append(", ".join(self.authors))
            else:
                parts.append(f"{self.authors[0]}, {self.authors[1]}, et al.")
        if self.article_title:
            parts.append(self.article_title)
        if self.source:
            parts.append(self.source)
        if self.year:
            parts.append(f"({self.year})")
        volume_info = ""
        if self.volume:
            volume_info = self.volume
            if self.issue:
                volume_info += f"({self.issue})"
        if self.first_page:
            if volume_info:
                volume_info += ":"
            volume_info += self.first_page
            if self.last_page:
                volume_info += f"-{self.last_page}"
        if volume_info:
            parts.append(volume_info)
        if self.doi:
            parts.append(f"doi:{self.doi}")
        if not parts:
            return self.citation
        return ". ".join(parts)


@dataclass
class JATSArticle:
    """Complete parsed JATS article data."""

    title: str
    authors: list[JATSAuthorInfo]
    journal: str
    volume: str
    issue: str
    pages: str
    year: str
    doi: str
    pmc_id: str
    pmid: str
    abstract_sections: list[JATSAbstractSection]
    body_sections: list[JATSBodySection]
    figures: list[JATSFigureInfo]
    tables: list[JATSTableInfo]
    references: list[JATSReferenceInfo]
    # True when <body> held at least one non-empty <p> inside a <sec> — that
    # is, body prose that survived parsing. Some publishers (medRxiv among
    # them) serve a JATS document made of <front> and <back> only; it parses
    # cleanly but holds nothing beyond the abstract, so callers must not
    # mistake it for full text.
    #
    # Two consequences of tracking what survived parsing rather than what the
    # XML contained. A <p> sitting directly in <body> with no enclosing <sec>
    # is dropped by the handler and so does not count — consistent with the
    # rendered HTML, which has no body prose either, but it means a valid
    # article of that shape reads as abstract-only. And the default is False,
    # so a hand-built JATSArticle reports "no body" unless it says otherwise.
    has_body: bool = False


@dataclass
class FullTextSourceEntry:
    """A known full-text source URL discovered by a fetcher.

    Produced by publication fetchers, consumed by :class:`FullTextService`.
    """

    url: str
    format: str  # "pdf", "xml", "html"
    source: str  # e.g. "biorxiv", "medrxiv", "pmc", "publisher"
    open_access: bool = True
    version: str | None = None  # e.g. "preprint", "accepted", "published"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "url": self.url,
            "format": self.format,
            "source": self.source,
            "open_access": self.open_access,
        }
        if self.version:
            d["version"] = self.version
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullTextSourceEntry:
        return cls(
            url=data["url"],
            format=data["format"],
            source=data["source"],
            open_access=data.get("open_access", True),
            version=data.get("version"),
        )


@dataclass
class FullTextResult:
    """Result of a full-text retrieval attempt."""

    # "europepmc", "europepmc_pdf", "unpaywall", "doi", "pubmed", "cached",
    # or a fetcher source name (e.g. "biorxiv") for known full-text URLs
    source: str
    html: str | None = None
    pdf_url: str | None = None
    web_url: str | None = None
    file_path: str | None = None
    # What ``html`` actually holds. The service can tell an article body from
    # an abstract and from PDF-extracted prose, so it says which rather than
    # leaving every case looking alike:
    #
    #   "fulltext"  — a JATS document that had a <body>
    #   "abstract"  — a body-less JATS rendering, returned only as a last
    #                 resort; there is no article text in it
    #   "extracted" — text recovered from a PDF. Prose only: no figures,
    #                 tables or layout, and possibly not every page, so
    #                 ``pdf_url``/``file_path`` stay worth offering
    #   "none"      — ``html`` is None
    #
    # Callers that must not analyse an abstract as if it were an article
    # should branch on this rather than on ``html`` being set.
    content_kind: ContentKind = "none"


class SectionType(Enum):
    """Standard sections of a biomedical publication.

    ``TITLE`` is reserved: the segmenter carries the document title on
    :attr:`SegmentedDocument.title` and never emits a ``TITLE`` section, but
    the member stays as the name a caller building one by hand would reach
    for, and :meth:`Section.to_markdown` renders it at heading level one.
    ``FRONT_MATTER`` and ``UNKNOWN`` are containers, not classifications —
    what precedes the first detected heading, and text no heading claimed.
    Every other member has at least one heading pattern in
    :data:`bmlib.fulltext.segmenter.SectionSegmenter.SECTION_PATTERNS`.
    """

    TITLE = "title"
    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    BACKGROUND = "background"
    METHODS = "methods"
    RESULTS = "results"
    DISCUSSION = "discussion"
    CONCLUSION = "conclusion"
    ACKNOWLEDGMENTS = "acknowledgments"
    REFERENCES = "references"
    SUPPLEMENTARY = "supplementary"
    APPENDIX = "appendix"
    FUNDING = "funding"
    CONFLICTS = "conflicts"
    DATA_AVAILABILITY = "data_availability"
    AUTHOR_CONTRIBUTIONS = "author_contributions"
    FRONT_MATTER = "front_matter"
    UNKNOWN = "unknown"


@dataclass
class TextBlock:
    """One text line of a PDF with its layout and font attributes.

    A line, not a span: PyMuPDF starts a new span at every font change, so a
    heading numbered in a different weight or a sentence holding an italic
    gene name would shatter into fragments no anchored heading pattern can
    match. Font attributes are those of the line's dominant span — see
    ``_line_to_block()`` in :mod:`bmlib.fulltext.pdf_converter`.
    """

    text: str
    page_num: int  # 0-indexed
    font_size: float
    font_name: str
    is_bold: bool
    is_italic: bool
    x: float
    y: float
    width: float
    height: float

    def __str__(self) -> str:
        """Return a short summary that does not dump the text."""
        return (
            f"TextBlock(page={self.page_num}, font={self.font_size:.1f}, text={self.text[:50]!r})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of all fields."""
        return {
            "text": self.text,
            "page_num": self.page_num,
            "font_size": self.font_size,
            "font_name": self.font_name,
            "is_bold": self.is_bold,
            "is_italic": self.is_italic,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextBlock:
        """Rebuild a block from :meth:`to_dict` output. All fields required."""
        return cls(
            text=data["text"],
            page_num=data["page_num"],
            font_size=data["font_size"],
            font_name=data["font_name"],
            is_bold=data["is_bold"],
            is_italic=data["is_italic"],
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"],
        )


@dataclass
class Section:
    """A typed, titled span of a segmented document.

    ``page_start`` / ``page_end`` are 0-indexed and cover the section's
    content blocks; for a heading with no body they are the heading's page.
    ``confidence`` is 1.0 for an exact heading match, 0.7 for a partial one,
    and 0.5 for the two container sections (front matter, the no-headings
    fallback). ``subsections`` is carried for callers but never populated by
    the segmenter, which emits a flat list.
    """

    section_type: SectionType
    title: str
    content: str
    page_start: int
    page_end: int
    confidence: float = 1.0
    subsections: list[Section] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render as markdown — ``#`` for a TITLE section, ``##`` otherwise."""
        level = "#" if self.section_type is SectionType.TITLE else "##"
        md = f"{level} {self.title}\n\n{self.content}\n"
        for subsection in self.subsections:
            md += f"\n### {subsection.title}\n\n{subsection.content}\n"
        return md

    def __str__(self) -> str:
        """Return a short summary that does not dump the content."""
        return (
            f"Section({self.section_type.value}, pages={self.page_start}-{self.page_end}, "
            f"{len(self.content)} chars)"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict; the enum becomes its value."""
        return {
            "section_type": self.section_type.value,
            "title": self.title,
            "content": self.content,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "confidence": self.confidence,
            "subsections": [s.to_dict() for s in self.subsections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Section:
        """Rebuild a section from :meth:`to_dict` output.

        ``confidence`` and ``subsections`` default as on the dataclass;
        everything else is required.
        """
        return cls(
            section_type=SectionType(data["section_type"]),
            title=data["title"],
            content=data["content"],
            page_start=data["page_start"],
            page_end=data["page_end"],
            confidence=data.get("confidence", 1.0),
            subsections=[cls.from_dict(s) for s in data.get("subsections", [])],
        )


@dataclass
class SegmentedDocument:
    """A publication segmented into typed sections.

    ``authors`` is reserved: nothing populates it today — author extraction
    from PDF front matter is its own heuristic problem — but a parser that
    can fill it should not need a schema change. ``metadata`` is whatever
    the caller passed to ``segment_document()``, stored as-is.
    """

    file_path: str = ""
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_section(self, section_type: SectionType) -> Section | None:
        """Return the first section of *section_type*, or None."""
        for section in self.sections:
            if section.section_type is section_type:
                return section
        return None

    def to_markdown(self) -> str:
        """Render the whole document as markdown."""
        md_parts: list[str] = []
        if self.title:
            md_parts.append(f"# {self.title}\n")
        if self.authors:
            md_parts.append(f"**Authors:** {', '.join(self.authors)}\n")
        for section in self.sections:
            md_parts.append("\n---\n")
            md_parts.append(f"**{section.title.upper()}**")
            md_parts.append("\n---\n\n")
            md_parts.append(section.to_markdown())
        return "\n".join(md_parts)

    def __str__(self) -> str:
        """Return a short summary that does not dump the sections."""
        return f"SegmentedDocument({self.file_path or '<no path>'}, {len(self.sections)} sections)"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict.

        ``metadata`` is included as-is — it is JSON-safe only if what the
        caller passed to ``segment_document()`` was.
        """
        return {
            "file_path": self.file_path,
            "title": self.title,
            "authors": list(self.authors),
            "sections": [s.to_dict() for s in self.sections],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SegmentedDocument:
        """Rebuild a document from :meth:`to_dict` output. Every field defaults."""
        return cls(
            file_path=data.get("file_path", ""),
            title=data.get("title"),
            authors=list(data.get("authors", [])),
            sections=[Section.from_dict(s) for s in data.get("sections", [])],
            metadata=data.get("metadata", {}),
        )
