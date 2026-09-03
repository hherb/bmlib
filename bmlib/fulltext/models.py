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
    """Parsed author information from a JATS article.

    JATS names a contributor with ``(name | string-name | collab | ...)``, and
    only the first of those divides into parts. The other two give **one
    undivided string**, so each has a field of its own rather than being
    folded into ``surname``:

    - ``collab`` — a collaboration, consortium or group (*"the INHERIT Trial
      Group"*). Not a person at all. In the only draw that has measured it —
      1,025 open-access articles, from the PR #118 review — a collaboration
      was always credited *beside* a structured name, so no article lost all
      its contributors to this spelling (issue #120).
    - ``string_name`` — a person whose name the depositor did not split
      (*"Jane Q Smith"*). A ``<string-name>`` **may** carry ``<surname>`` and
      ``<given-names>`` children, and where it does those fill the structured
      fields instead; this one holds the undivided case (issue #140).

    **Both are held verbatim and are never split.** Deriving a surname from
    *"Ahmed Al-Rashid"* means deciding about particles, multi-word surnames
    and name order — assumed rather than measured, and wrong in a way the
    caller cannot detect. A consumer that needs *"Smith J"* has the string and
    can make that decision itself, knowing that it is making one.

    Keeping them out of ``surname`` is what lets that consumer tell them
    apart: ``surname`` is what downstream code sorts and de-duplicates on, and
    an organisation silently sitting in it is indistinguishable from a person.
    Emptiness is the predicate — ``bool(collab)`` asks "is this an
    organisation?" and ``bool(string_name)`` asks "is this name undivided, so
    must not be treated as a surname?" — so there is no flag that can disagree
    with the string it describes.

    **A contributor may now carry an empty ``surname``.** Before #120 and #140
    a collaboration produced no entry at all, so code reading ``surname``
    unconditionally never saw one — ``sorted(authors, key=...surname)`` now
    front-loads consortia and ``a.surname[0]`` now raises. Read
    :attr:`full_name`, or branch on ``collab`` / ``string_name``.
    """

    surname: str = ""
    given_names: str = ""
    #: Reserved: nothing populates this today — bmlib's JATS parser has no
    #: ``<aff>`` handler, so it is always empty — but a parser that can fill it
    #: should not need a schema change. Do not read it as "this contributor
    #: declared no affiliation".
    affiliations: list[str] = field(default_factory=list)
    #: A collaboration's name, where this contributor is one (issue #120).
    collab: str = ""
    #: An undivided personal name, exactly as deposited (issue #140).
    string_name: str = ""

    @property
    def full_name(self) -> str:
        """The name to display, preferring whichever spelling the deposit gave.

        A structured name wins over both undivided forms, because a
        ``<contrib>`` carrying a ``<name>`` *and* a ``<collab>`` is *"Smith, on
        behalf of the Y Group"* — the person is the contributor and the
        collaboration is an attribution attached to them.

        The order between the two undivided forms is **arbitrary**. No deposit
        carrying both has been measured, and the principle above does not
        settle it: a ``string_name`` *is* a person, so "the person is the
        contributor" would argue for the opposite order. It is fixed only so
        the rule is deterministic, and pinned so the code keeps applying
        whichever rule this docstring states.
        """
        if self.surname or self.given_names:
            return f"{self.given_names} {self.surname}".strip()
        return self.collab or self.string_name

    @property
    def is_named(self) -> bool:
        """Did any spelling of a name arrive?

        A question, not a guarantee: a ``<contrib>`` naming nobody —
        ``<anonymous/>``, or one carrying only an ``<xref>`` — is well-formed
        JATS, so an unnamed contributor is a document's answer and not an
        error. The parser's builder gates on this rather than repeating the
        four-way test, so "named" has one definition, on the public type, for
        the downstream that has to make the same judgement.

        Reads through :attr:`full_name`, so a field holding only whitespace
        counts as unnamed — ``bmlib.citations`` already treats a blank string
        as no author, and the parser strips before assigning either way.
        """
        return bool(self.full_name.strip())


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
    """Parsed table metadata with pre-rendered HTML content.

    ``graphic_url`` is the table's own ``<graphic>`` deposit, filled the way a
    figure's is and by the same ranking. A ``<table-wrap>`` whose only content
    is an image — a scanned or typographically complex table — otherwise
    carries an id, a label and a caption over nothing, which is
    indistinguishable from an empty one (issue #127). A ``<table-wrap>`` may
    carry both a ``<table>`` and a ``<graphic>``, so both fields may be set;
    which one to show is the renderer's choice, and ``to_html()`` shows the
    markup. A caller that wants the facsimile — because the markup lost a
    merged cell, or because it is showing the page as published — reads this
    field directly, and it is the only way to get at it: ``FullTextService``
    discards the ``JATSArticle`` and caches the rendered HTML alone, so for a
    service consumer that renderer choice is permanent. Both populations are
    measured over the two committed draws (1,997 articles, 2,448
    ``<table-wrap>``, every one of them in the recent window): the image is
    the *only* rendition for **8**, and sits beside a ``<table>`` for **84**.

    Those figures replace the pre-#138 draw's — 600 articles, 755 tables, 11
    image-only (all back-filled) and 5 carrying both (all recent) — which
    survived that redraw here alone: the reconciliation walked
    ``jats_parser.py``, ``CLAUDE.md``, ``ROADMAP.md``, ``CHANGELOG.md`` and
    ``docs/manual/``, and not this docstring. It had come to say the opposite
    of the evidence in both directions, since the back-filled window holds
    **no** ``<table-wrap>`` at all and so can supply no image-only table,
    while "both" is the commoner rendition rather than the rarer.
    ``TestTheCitedPopulationsAreWhatTheCorporaHold`` asserts the corpus
    against literals in the test, so it cannot catch prose drifting away from
    it — a figure has to be corrected everywhere it is read (#112).

    ``graphic_url`` is ``str | None`` while ``html_content`` beside it is
    ``str``, which is deliberate on both counts: ``html_content`` is rendered
    output, where empty and absent are the same state and ``""`` is the
    natural bottom, whereas ``graphic_url`` is a value the document either
    deposited or did not, and ``""`` is not a valid href. Matching
    :class:`JATSFigureInfo`'s already-shipped ``str | None`` matters more than
    matching the neighbouring field, since a caller writing ``if
    x.graphic_url`` over both exhibits should not have to know which class it
    is holding.
    """

    id: str
    label: str
    caption: str
    html_content: str = ""
    graphic_url: str | None = None


@dataclass
class JATSReferenceInfo:
    """Parsed reference/citation information."""

    id: str
    label: str
    #: Every descendant's text of a ``<mixed-citation>``, in document order —
    #: the marked-up parts with whatever character data the depositor put
    #: between them (issue #146; before it, a child that took a text buffer
    #: without merging it back deleted itself, leaving the punctuation alone:
    #: ``'. . . ;():-. doi: .'``).
    #:
    #: Deliberately *not* described as "the reference as the publisher typeset
    #: it". A separator is often in the publisher's rendering stylesheet rather
    #: than the deposit, so adjacent elements with nothing between them
    #: concatenate — ``<surname>``/``<given-names>`` and repeated ``<pub-id>``
    #: most often, measured at 13.2% of 3,798 citations carrying at least one
    #: such pair. That is faithful to what the document contains and is a large
    #: improvement on the punctuation alone, but it is not a typeset string,
    #: and a caller wanting one should read :attr:`formatted_citation`, which
    #: assembles from the structured fields with a separator of its own. Prefer
    #: this field where the publisher's own wording matters, and
    #: :attr:`formatted_citation` where consistent presentation does.
    #:
    #: An ``<element-citation>`` deposit leaves this **empty**, and that is not
    #: a gap: its content model is element-only, so the depositor authored no
    #: string and the whitespace between the children is insignificant. The
    #: parser enforces that rather than inheriting it — an element-only deposit
    #: still leaks the text of children this module does not accumulate
    #: (``<edition>``, ``<publisher-name>``, ``<comment>``), which read as a
    #: run-together word, so only a ``<mixed-citation>`` writes this field.
    #: Where a ``<ref>`` carries both spellings, the ``<mixed-citation>`` wins
    #: regardless of deposit order.
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
    # It tracks what survived parsing rather than what the XML contained, and
    # the default is False, so a hand-built JATSArticle reports "no body"
    # unless it says otherwise. Unsectioned prose does count: a <p> sitting
    # directly in <body> with no enclosing <sec> is collected into an
    # untitled section, so an article of that shape is not mistaken for an
    # abstract-only one.
    has_body: bool = False
    # How many <sub-article>/<response> elements were skipped, counting a
    # nested one separately. Nothing inside them is this article's, so they
    # contribute nothing to the fields above — but they can hold most of a
    # document's prose (a peer-review history, or the alternative-language
    # full text SciELO deposits as article-type="translation"), and dropping
    # that changes neither has_body nor FullTextResult.content_kind, which
    # between them report only *total* loss. This is the one field that says
    # a nested article was there at all.
    suppressed_nested_articles: int = 0


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
