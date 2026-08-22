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

import logging
import re
import xml.sax
import xml.sax.handler
from dataclasses import dataclass, field
from enum import IntEnum
from html import escape as html_escape
from io import BytesIO
from typing import Generic, TypeVar

from bmlib.fulltext.models import (
    JATSAbstractSection,
    JATSArticle,
    JATSAuthorInfo,
    JATSBodySection,
    JATSFigureInfo,
    JATSReferenceInfo,
    JATSTableInfo,
)

logger = logging.getLogger(__name__)

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


class _GraphicSuitability(IntEnum):
    """How well a ``<graphic>`` deposit serves as *the* image of its figure.

    Ordered worst to best, so the deposits can be ranked rather than chosen by
    position — see :func:`_graphic_suitability`.
    """

    ARCHIVAL = 1
    """A print master no browser renders: TIFF, EPS, PostScript."""

    THUMBNAIL = 2
    """A reduced preview. Renders, but is not the figure."""

    FULL = 3
    """Everything else — the ordinary case, and the one to keep."""


# JATS mime-subtypes of the archival masters deposited beside a web image,
# normally inside <alternatives>. None of `content-type`, `specific-use` or
# `mime-subtype` is case-controlled, so all three are lowercased before
# comparison.
_ARCHIVAL_MIME_SUBTYPES = frozenset({"tiff", "tif", "eps", "postscript"})

# The same masters as they appear in an href that declares no `mime-subtype`.
# See `_graphic_suitability` for why inferring *here* is safe where inferring a
# thumbnail from the extension is not.
_ARCHIVAL_EXTENSIONS = frozenset({".tif", ".tiff", ".eps", ".ps"})

# BOTH SETS ARE DEFENSIVE, AND THE MEASUREMENT SAYS SO. Over 276 open-access
# Europe PMC articles (`scripts/sample_jats_exhibits.py`, issue #131): 912
# figures carry an <alternatives> block and 1,819 <graphic> sit inside one, of
# which **zero declare a mime-subtype at all** and **zero are archival by
# either test** — every href in the sample is .jpg or .gif. So neither tier
# fires on that corpus, and the ARCHIVAL rank is unreached.
#
# They are kept rather than deleted because the failure they prevent is silent
# and permanent: an undeclared master deposited first ranks FULL, wins under
# the strictly-better rule, and leaves the figure pointing at something no
# browser renders. "No instance in 276 articles" is not "cannot happen", and
# the cost of carrying the tiers is one comparison. Re-run the sampler before
# concluding otherwise — that is what it is for.


def _has_archival_extension(href: str) -> bool:
    """Does ``href`` name a print master by its file extension?

    Args:
        href: The deposit's resolved href.

    Returns:
        ``True`` if the path ends in a known archival extension.
    """
    path = href.split("?", 1)[0].split("#", 1)[0].strip().lower()
    return any(path.endswith(extension) for extension in _ARCHIVAL_EXTENSIONS)


def _graphic_suitability(attrs: xml.sax.xmlreader.AttributesImpl, href: str) -> _GraphicSuitability:
    """Rank one ``<graphic>`` deposit by how well it serves as the figure.

    ``content-type`` and ``specific-use`` are both open-valued in JATS and
    neither is case-controlled, so "thumbnail" is matched as a lowercased
    substring — ``thumb`` and ``thumbnail`` are both current spellings and a
    third is possible.

    **A thumbnail is never inferred from the file extension.** Every thumbnail
    in the surveyed corpus is a ``.gif`` because PLOS and Springer both deposit
    that way, so an extension rule passes the corpus and then discards the only
    image a figure has wherever ``.gif`` *is* that image.

    **An archival master is**, and the asymmetry is deliberate rather than an
    exception to the rule above. A ``<graphic>`` in an ``<alternatives>`` block
    need not declare ``mime-subtype`` — and when it does not, an undeclared
    TIFF deposited first ranked ``FULL`` and, under
    :meth:`_GraphicHolder.offer_graphic`'s strictly-better rule, beat the web
    image that followed it. What makes inferring safe *here* is that a first
    deposit is accepted whatever its rank, so demoting can only ever break a
    tie against a real web image — it can never discard the only image a figure
    has, which is exactly the cost that rules the thumbnail half out.

    A deposit marked *both* — a TIFF thumbnail — is ranked ``THUMBNAIL``,
    since that predicate is tested first. Neither ranking serves it well
    because the deposit is a TIFF either way, so neither describes something a
    browser can show; the corpus carries no instance and no test pins the
    order. It is the reference implementation's, and is recorded here rather
    than asserted.

    Args:
        attrs: Attributes of the ``<graphic>`` start tag.
        href: The deposit's resolved href, read for its extension only.

    Returns:
        The deposit's suitability, worst to best.
    """
    content_type = (attrs.get("content-type") or "").lower()
    specific_use = (attrs.get("specific-use") or "").lower()
    if "thumb" in content_type or "thumb" in specific_use:
        return _GraphicSuitability.THUMBNAIL
    if (attrs.get("mime-subtype") or "").lower() in _ARCHIVAL_MIME_SUBTYPES:
        return _GraphicSuitability.ARCHIVAL
    if _has_archival_extension(href):
        return _GraphicSuitability.ARCHIVAL
    return _GraphicSuitability.FULL


@dataclass(kw_only=True)
class _GraphicHolder:
    """The half of an exhibit builder that chooses among ``<graphic>`` deposits.

    Shared by :class:`_FigureBuilder` and :class:`_TableBuilder` rather than
    written once each, because a ``<table-wrap>`` may be deposited as an image
    too (issue #127) — a scanned or typographically complex table. Two copies
    of a rule this heavily argued are two things to keep in step, and that is
    the whole of the argument for sharing it.

    **Whether a table is ever deposited with several ``<graphic>`` is not
    measured**, and the ranking below is therefore reasoned onto tables rather
    than observed on them. Both committed corpora say only that the question
    does not arise in them: every one of the 11 image-only tables in
    ``tests/data/jats_exhibits.backfill.json`` carries exactly one deposit,
    declaring no ``content-type``, no ``specific-use`` and no
    ``<alternatives>``, and the recent draw carries no image-only table at
    all. With one deposit, ranking and plain first-wins agree, so nothing
    here is contradicted — it is simply unexercised. ``sample_jats_exhibits``
    counts the table side as of issue #135 so a later draw can settle it;
    until one has, do not restate this as publisher behaviour.

    ``kw_only`` because these two fields are inherited and would otherwise
    lead both subclasses' generated ``__init__``, making ``_TableBuilder("t1")``
    set the href rather than the id — and leaving ``graphic_rank`` ``None``
    beside a set href, which is the one state the pairing below forbids.
    ``offer_graphic`` is the only writer that keeps them in step.
    """

    graphic_href: str = ""
    graphic_rank: _GraphicSuitability | None = None

    def offer_graphic(self, href: str, rank: _GraphicSuitability) -> None:
        """Keep ``href`` only if it is a strictly better deposit than the one held.

        A figure commonly deposits the same image more than once and only one
        href fits the model: 58.0% of the 959 surveyed figures that carry a
        ``<graphic>`` at all — from a 225-article survey — carry several.
        Position cannot decide between them. A thumbnail is deposited *last*
        (PLOS, Springer), so "keep the last" yields a thumbnail for 52.9% of
        figures. "Keep the first" was correct for every article measured, but
        it inverts wherever an ``<alternatives>`` archival master is deposited
        first — no corpus instance exists. Ranking settles both without caring
        which end it is.

        Re-measured independently on 276 open-access Europe PMC articles
        (``scripts/sample_jats_exhibits.py``): **49.9%** of the 1,833 figures
        carrying a ``<graphic>`` carry more than one and **49.5%** end on a
        thumbnail — a few points below the 58.0% / 52.9% above, which came
        from a different draw. **0%** deposit a thumbnail *first*, so the
        convention that motivates ranking over plain first-wins does not
        appear in that sample at all. Ranking still earns its place on the
        49.5%: it is what stops half of all figures resolving to a preview.

        *Strictly* better is what makes the first deposit win among equals.

        **Every percentage above is measured over figures.** Tables reach this
        method too since issue #127, and no draw has yet found one carrying a
        second deposit — see :class:`_GraphicHolder` for what that does and
        does not license.

        Args:
            href: The deposit's resolved href, already stripped by the caller;
                an empty one is ignored.
            rank: Its suitability, from :func:`_graphic_suitability`.
        """
        if not href:
            return
        if self.graphic_rank is None or rank > self.graphic_rank:
            self.graphic_href = href
            self.graphic_rank = rank


@dataclass
class _FigureBuilder(_GraphicHolder):
    id: str = ""
    label: str = ""
    caption: str = ""

    def build(self) -> JATSFigureInfo:
        return JATSFigureInfo(
            id=self.id,
            label=self.label,
            caption=self.caption,
            graphic_url=self.graphic_href or None,
        )


@dataclass
class _TableBuilder(_GraphicHolder):
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
    current_row_cell_count: int = 0
    current_row_header_cell_count: int = 0
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
        self.current_row_cell_count = 0
        self.current_row_header_cell_count = 0

    def end_row(self) -> None:
        if self.in_row and self.current_row:
            # A row is a header when it is inside an explicit <thead>, or — for
            # tables lacking <thead>/<tbody> wrappers — when it is the first row
            # AND *every* cell is a header cell. Requiring all cells to be header
            # cells avoids misclassifying a normal data row that merely starts
            # with a single <th> row-label.
            all_header_cells = (
                self.current_row_cell_count > 0
                and self.current_row_header_cell_count == self.current_row_cell_count
            )
            if self.in_header or (all_header_cells and not self.in_body and not self.header_rows):
                self.header_rows.append(self.current_row)
            else:
                self.body_rows.append(self.current_row)
        self.in_row = False
        self.current_row = []
        self.current_row_has_header_cells = False
        self.current_row_cell_count = 0
        self.current_row_header_cell_count = 0

    def start_cell(self, is_header: bool = False, colspan: int = 1) -> None:
        self.in_cell = True
        self.current_cell_text = ""
        self.current_colspan = max(1, colspan)
        self.current_row_cell_count += 1
        if is_header or self.in_header:
            self.current_row_has_header_cells = True
            self.current_row_header_cell_count += 1

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
            graphic_url=self.graphic_href or None,
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


_BuilderT = TypeVar("_BuilderT", _FigureBuilder, _TableBuilder)


@dataclass
class _ExhibitFrame(Generic[_BuilderT]):
    """One open ``<fig>`` or ``<table-wrap>``.

    Both are stacks rather than single slots because both nest: eLife wraps
    every figure supplement inside the figure it belongs to, and JATS lets a
    ``<table-wrap>`` open inside another's ``<table-wrap-foot>``. Held as one
    slot, the inner open overwrote the parent's builder, the inner close
    emitted the child and cleared the slot, and the parent's own end tag found
    nothing to build — losing the parent outright (issue #115).

    ``slot`` is the index reserved in the owning slot list when the element
    opened. An exhibit is *built* at its end tag but has to be *listed* at its
    start, so a plain pop-and-append emits every supplement ahead of the parent
    it belongs to; the reservation is what keeps the result in document order.

    There is no ordering field. One was carried until issue #123: caption text
    was routed to whichever exhibit had opened most recently, so a sequence
    number was needed to compare the two stacks. A ``<caption>`` is a direct
    child of the element it describes, so its parent now names the owner
    outright and the comparison has nothing left to break a tie for.
    """

    slot: int
    builder: _BuilderT


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

# A <sub-article> or <response> is a complete article of its own — its own
# <front>, its own <body>, its own back matter — nested inside this one.
# Nothing inside one is this article's, so no handler may fire there.
#
# The set is complete, and structurally so: of JATS's ~295 elements exactly
# three admit <front>/<front-stub> and <body>, and the third is <article>
# itself. Both have <article> and <sub-article> as their only parents, so
# neither can appear in flowing content.
#
# Peer review is the case that motivated this (issue #110) — PLOS was
# observed depositing each round as a <sub-article>, and PLOS, eLife, BMJ
# Open and F1000 publish review histories as a matter of policy, so the rate
# inside those journals is far above the 4-in-249 measured across PMC — but
# it is not the only one. <sub-article> also carries the alternative-language
# full text (SciELO's article-type="translation"), meeting abstracts, and
# Europe PMC's own injected "associated-data" block, which is absent from
# PMC's copy of the same record. Which is why the suppression is structural:
# @article-type is CDATA #IMPLIED, four published vocabularies for it
# disagree, and publishers deposit values in none of them (eLife's
# "decision-letter", the F1000 platform's "response"), so no allow-list of
# types could have decided this correctly.
_NESTED_ARTICLE_ELEMENTS = frozenset({"sub-article", "response"})


# Elements a <graphic> may sit inside without ceasing to be the enclosing
# exhibit's own image. <alternatives> is a "choose one of these" wrapper around
# several encodings of a single image, and <p> is prose flow that contains an
# image without owning it — JATS admits both inside <fig>, and reading either
# as the owner costs the figure its image.
#
# Every other container — <fn>, <supplementary-material>, <media>,
# <boxed-text>, and a nested <table-wrap> — owns the image it holds. That side
# needs no enumeration: anything not listed here is opaque, so a container this
# module has never heard of keeps its own image rather than donating it.
#
# Measured over the same 276 articles: exactly **one** <graphic> in the sample
# is owned by a non-exhibit inside an exhibit — an inline image in a <td> of
# PMC13047053's table — and it resolves identically either way, because no
# <fig> is open around it. Routing by owner therefore changed **no** figure's
# image across the corpus.
#
# Kept for the reason the archival tiers are: what it prevents is silent. A
# nested <table-wrap>/<fn>/<supplementary-material> inside a <fig> hands over
# its image, and the strictly-better rule then makes that permanent where
# "keep the last" used to overwrite it. The <p> member is not defensive at
# all — JATS admits <p> inside <fig>, and without it a figure whose graphic is
# wrapped in prose flow loses its image outright.
_GRAPHIC_TRANSPARENT_WRAPPERS = frozenset({"alternatives", "p"})


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
        # Set once an <article-id pub-id-type="doi"> has been read, which
        # locks the value against the shape-matching fallback below.
        self.doi_is_typed = False
        self.pmc_id = known_pmc_id
        self.pmid = ""
        self.abstract_sections: list[JATSAbstractSection] = []
        self.body_sections: list[JATSBodySection] = []
        self.references: list[JATSReferenceInfo] = []

        # Parsing state
        self.element_stack: list[str] = []
        self.text_stack: list[str] = [""]
        # How many <sub-article>/<response> elements are open. A depth and
        # not a flag: JATS permits a nested article inside a nested article,
        # and a flag cleared by the inner close re-admits the rest of the
        # outer one.
        self.nested_article_depth = 0
        # How many were skipped in total, a nested one counted separately.
        # Reported on JATSArticle because the suppression is otherwise
        # invisible: it changes neither has_body nor content_kind unless it
        # takes the whole body.
        self.suppressed_nested_articles = 0

        # Article metadata state
        self.in_front = False
        self.in_article_meta = False
        # The roles declared by the open <contrib-group> elements, innermost
        # last; a bare <contrib> inherits the innermost. Held rather than a
        # plain "are we in a group" boolean — that one was tracked and never
        # read — and a *stack* rather than one value, because <collab> may
        # contain a <contrib-group>: that is how a collaboration's member
        # roster is tagged, and a single value let the roster's close clear
        # the enclosing group's role, so an editor group's own members were
        # then collected as this article's authors.
        self.contrib_group_stack: list[str | None] = []
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
        # <sec> is optional inside <body>, so prose can arrive with an empty
        # section_stack. It is collected here and flushed to body_sections at
        # the next <sec> or at </body>, rather than pushed onto section_stack:
        # a real <sec> opening afterwards would otherwise nest inside it.
        self.implicit_body_section: _SectionBuilder | None = None
        # Prose found inside <body>. Counted separately from body_sections
        # because back-matter sections land there too, so a non-empty
        # body_sections does not by itself mean the article has a body.
        self.body_paragraph_count = 0

        # Figure / table state
        #
        # Both exhibits nest, so both are stacks and neither is a single slot.
        # A <fig> may contain another — eLife wraps every figure supplement
        # inside the figure it belongs to. The original survey put this at
        # 19.6% of articles; `scripts/sample_jats_exhibits.py` re-measures it
        # at 0.7% of a general open-access draw (2 of 276, both eLife; 0 of a
        # 300-article stratified draw), so it is one publisher's house style
        # costing about half of *its* figures, not a general convention. The
        # two articles lost 6 of 12 and 5 of 11 figures respectively. And
        # JATS lets a <table-wrap> open inside another's <table-wrap-foot>. As
        # one slot, the inner open overwrote the parent's builder, the inner
        # close emitted the child and cleared the slot, and the parent's own
        # end tag found nothing to build (issue #115).
        #
        # One slot per exhibit in *open* order, reserved when the element opens
        # and filled when it closes. Pop-and-append would restore the parent
        # but list it after its own supplement, since an exhibit is built at
        # its end tag and the child's arrives first.
        self.figure_slots: list[JATSFigureInfo | None] = []
        self.table_slots: list[JATSTableInfo | None] = []
        # The open exhibits, innermost last. `in_figure`, `current_figure`,
        # `in_table_wrap` and `current_table` are derived from these rather
        # than stored: a stored flag is what the inner close cleared while the
        # parent was still open, which read the rest of the parent as article
        # prose, and it would come back the moment someone added an early
        # return.
        self.figure_stack: list[_ExhibitFrame[_FigureBuilder]] = []
        self.table_stack: list[_ExhibitFrame[_TableBuilder]] = []
        # Caption text is carried in <p> and <title> — the same elements that
        # carry section prose and section headings — so routing it needs the
        # enclosing <caption> and not just "a figure is open somewhere above".
        #
        # One entry per open <caption>, innermost last, holding the builder
        # that <caption> belongs to — or None where its owner is an element
        # this module does not model. Both halves are load-bearing (#123):
        #
        # A **stack** because captions nest. Held as a boolean, the inner
        # </caption> cleared it, so a <media> legend inside a figure's caption
        # truncated that caption at the point the legend ended and dropped
        # every word after it.
        #
        # The **owner** because a depth counter only fixes that half. The
        # legend's owner is not an exhibit bmlib models, so counted rather
        # than named it still lands on the enclosing figure — and the case a
        # depth cannot reach at all needs no nesting: eLife deposits a
        # <supplementary-material> carrying a <caption> of its own beside the
        # figure's, inside the <fig>, and every word of *Figure 1—source data
        # 1* was appended to the figure's legend.
        #
        # Naming the owner is also what retired `_innermost_exhibit()`: a
        # <caption> is a direct child of what it describes, so its parent
        # answers exactly, where "the innermost exhibit open anywhere above"
        # was merely usually right.
        self.caption_stack: list[_FigureBuilder | _TableBuilder | None] = []

        # Reference state
        self.in_ref_list = False
        self.in_ref = False
        self.in_ref_citation = False
        self.in_ref_person_group = False
        self.current_reference: _ReferenceBuilder | None = None

        # Cross-reference state
        self.current_xref_type: str | None = None
        self.current_xref_rid: str | None = None

    # -- Exhibit stack helpers -----------------------------------------------

    @property
    def in_figure(self) -> bool:
        """Is a ``<fig>`` open? Derived, never stored — see ``figure_stack``."""
        return bool(self.figure_stack)

    @property
    def in_table_wrap(self) -> bool:
        """Is a ``<table-wrap>`` open? Derived, never stored."""
        return bool(self.table_stack)

    @property
    def current_figure(self) -> _FigureBuilder | None:
        """The innermost open ``<fig>``.

        Innermost rather than "whichever was opened most recently and not yet
        emitted": a ``<graphic>`` or ``<label>`` belongs to the figure that
        encloses it, and the parent becomes current again when its supplement
        closes.
        """
        return self.figure_stack[-1].builder if self.figure_stack else None

    @property
    def current_table(self) -> _TableBuilder | None:
        """The innermost open ``<table-wrap>``, for the same reason."""
        return self.table_stack[-1].builder if self.table_stack else None

    def build_figures(self) -> list[JATSFigureInfo]:
        """Build the figures, in the order their ``<fig>`` elements *opened*.

        A method rather than a property because each call renders a fresh
        list: as an attribute it read like the mutable list it replaced, so
        ``h.figures.append(...)`` would have become a silent no-op.

        A slot still holding ``None`` was reserved by a ``<fig>`` that never
        closed, which ``xml.sax`` cannot deliver — expat rejects an unbalanced
        document before :meth:`JATSParser.parse` returns, which
        ``test_an_unbalanced_document_is_refused_outright`` pins. The filter
        only keeps the reservation from being able to put a hole in the
        result.
        """
        return [figure for figure in self.figure_slots if figure is not None]

    def build_tables(self) -> list[JATSTableInfo]:
        """Build the tables, in the order their ``<table-wrap>`` elements *opened*.

        The filter is there for the reason :meth:`build_figures`' is, and is
        equally unreachable; both are kept so a future non-SAX feed cannot put
        a hole in the result.
        """
        return [table for table in self.table_slots if table is not None]

    def _graphic_owner(self) -> str:
        """The element a ``<graphic>`` currently being opened belongs to.

        ``element_stack[-1]`` is the ``<graphic>`` itself, so the walk starts
        one above it and skips only the wrappers that do not take ownership
        (:data:`_GRAPHIC_TRANSPARENT_WRAPPERS`).

        Returns:
            The owning element's name, or ``""`` if there is none.
        """
        for name in reversed(self.element_stack[:-1]):
            if name not in _GRAPHIC_TRANSPARENT_WRAPPERS:
                return name
        return ""

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

    # -- Section and caption helpers -----------------------------------------

    def _caption_owner(self, parent: str) -> _FigureBuilder | _TableBuilder | None:
        """The builder a ``<caption>`` just opened under ``parent`` belongs to.

        ``<caption>`` is a direct child of the element it describes, so the
        parent decides outright — the ``<label>`` idiom, one element away. It
        is exact where "the innermost exhibit open anywhere above" was only
        usually right: ``<boxed-text>``, ``<media>``, ``<supplementary-material>``
        and ``<fig-group>`` all admit a ``<caption>`` too, and inside a
        ``<fig>`` each of them was donating its legend to the figure.

        Args:
            parent: The element enclosing the ``<caption>``.

        Returns:
            The owning exhibit's builder, or ``None`` when the owner is an
            element this module does not model — whose caption is then held by
            nothing rather than by the wrong thing.
        """
        if parent == "fig":
            return self.current_figure
        if parent == "table-wrap":
            return self.current_table
        return None

    def _append_caption_text(self, text: str) -> None:
        """Append caption prose to the innermost open ``<caption>``'s owner.

        A ``<caption>`` carries a ``<title>`` lead and one or more ``<p>``
        elements, which arrive in document order, so they are joined with a
        single space into the one ``caption`` string the models expose.

        Text arriving with no caption open is furniture — a cell, a footnote —
        and is dropped, which is what keeps table internals out of the prose.
        Text whose innermost caption has no modelled owner is dropped for the
        same reason: it belongs to that element, not to the exhibit enclosing
        it.

        Args:
            text: Whitespace-normalised text of the caption child element.
        """
        if not self.caption_stack:
            return
        builder = self.caption_stack[-1]
        if builder is None or not text:
            return
        if builder.caption:
            builder.caption += " "
        builder.caption += text

    def _flush_implicit_body_section(self) -> None:
        """Emit any pending unsectioned ``<body>`` prose as a body section.

        Called when a real ``<sec>`` opens and again at ``</body>``, so loose
        paragraphs keep their position in document order. The section carries
        no title — JATS gave it none, and inventing one would put a heading in
        the rendered article that the publisher never wrote.
        """
        if self.implicit_body_section is None:
            return
        self.body_sections.append(self.implicit_body_section.build())
        self.implicit_body_section = None

    # -- SAX events ----------------------------------------------------------

    def startElement(self, name: str, attrs: xml.sax.xmlreader.AttributesImpl) -> None:
        self.element_stack.append(name)

        if name in _NESTED_ARTICLE_ELEMENTS:
            self.nested_article_depth += 1
            self.suppressed_nested_articles += 1
            # The declared type is logged rather than read: it is CDATA
            # #IMPLIED, the vocabularies that constrain it disagree, and
            # publishers deposit values in none of them. What is skipped is
            # decided structurally, by the element, never by its type. JATS
            # spells the attribute per element — <sub-article> carries
            # article-type, <response> carries response-type — so reading
            # only the first would report every <response> as untyped.
            type_attr = "response-type" if name == "response" else "article-type"
            logger.debug(
                "Skipping nested <%s %s=%r> at depth %d",
                name,
                type_attr,
                attrs.get(type_attr),
                self.nested_article_depth,
            )

        if name in _TEXT_ACCUMULATING:
            self._push_text_buffer()

        if self.nested_article_depth:
            # Inside a nested article. Suppressed on the *opening* tag too,
            # not only on the closes that write the outputs: an open leaves
            # state behind. Where the nested article precedes the article's
            # own <body> — out of order for JATS, which puts <sub-article>
            # last, but well-formed — a nested <sec> whose close never comes
            # pops nothing, so the article's own section is filed as a
            # subsection of a review round's and never reaches body_sections.
            # A float is worse than a section: <fig>/<table-wrap> set flags
            # that the suppressed close never clears, and the leftover flag
            # swallows the rest of the parse.
            #
            # The element and text stacks keep running, so the two stay
            # balanced across the skipped region. characters() is the third
            # thing that keeps running, and it is guarded separately — see
            # there, since neither of these two handlers delivers text.
            return

        if name == "front":
            self.in_front = True
        elif name == "article-meta":
            self.in_article_meta = True
        elif name == "contrib-group":
            self.contrib_group_stack.append(attrs.get("content-type"))
        elif name == "contrib":
            if self._is_author_contrib(attrs.get("contrib-type")):
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
                # Flush first, so prose that preceded this <sec> becomes its own
                # body section rather than being folded in as the <sec>'s parent.
                self._flush_implicit_body_section()
                self.section_stack.append(_SectionBuilder())
        elif name == "fig":
            # Reserve the slot now, fill it at </fig>: listed where it opened,
            # built where it closed.
            self.figure_slots.append(None)
            self.figure_stack.append(
                _ExhibitFrame(
                    slot=len(self.figure_slots) - 1,
                    builder=_FigureBuilder(id=attrs.get("id", "")),
                )
            )
        elif name == "caption":
            # `element_stack[-1]` is this <caption>, as at <article-id> above.
            parent = self.element_stack[-2] if len(self.element_stack) >= 2 else ""
            self.caption_stack.append(self._caption_owner(parent))
        elif name == "graphic":
            # Routed by its owner, like a <label> — not by "is a figure open
            # anywhere above?", which is what `current_figure` answers. A
            # <graphic> held by a nested <table-wrap>, <fn> or
            # <supplementary-material> was being offered to the figure
            # enclosing it, and since both rank FULL and `offer_graphic`
            # accepts only a strictly better deposit, that foreign href then
            # beat the figure's own for good.
            # Stripped because `offer_graphic`'s emptiness guard is falsiness
            # and whitespace is truthy: XML normalises a pretty-printed
            # attribute to spaces rather than collapsing it, so a wrapped
            # href would take the ranking slot, block the real deposit that
            # follows, and render as a broken src. No instance in either
            # committed corpus (2,346 deposits, every extension unpadded) —
            # this guards a population measured empty, not an observed one.
            href = (
                attrs.get("xlink:href") or attrs.get("href") or attrs.get("xlink-href") or ""
            ).strip()
            owner = self._graphic_owner()
            current_figure = self.current_figure
            current_table = self.current_table
            if owner == "fig" and current_figure is not None:
                current_figure.offer_graphic(href, _graphic_suitability(attrs, href))
            elif owner == "table-wrap" and current_table is not None:
                # A <table-wrap>'s own image — a scanned or typographically
                # complex table, which before issue #127 was dropped and left
                # the table an id, a label and a caption over nothing. Ranked
                # by the same rule a figure's deposits are, because it is the
                # same rule: see `_GraphicHolder`.
                current_table.offer_graphic(href, _graphic_suitability(attrs, href))
        elif name == "table-wrap":
            self.table_slots.append(None)
            self.table_stack.append(
                _ExhibitFrame(
                    slot=len(self.table_slots) - 1,
                    builder=_TableBuilder(id=attrs.get("id", "")),
                )
            )
        elif name == "thead":
            current_table = self.current_table
            if current_table is not None:
                current_table.start_header()
        elif name == "tbody":
            current_table = self.current_table
            if current_table is not None:
                current_table.start_body()
        elif name == "tr":
            current_table = self.current_table
            if current_table is not None:
                current_table.start_row()
        elif name == "th":
            current_table = self.current_table
            if current_table is not None:
                colspan = int(attrs.get("colspan", "1") or "1")
                current_table.start_cell(is_header=True, colspan=colspan)
        elif name == "td":
            current_table = self.current_table
            if current_table is not None:
                colspan = int(attrs.get("colspan", "1") or "1")
                current_table.start_cell(is_header=False, colspan=colspan)
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
        if self.nested_article_depth:
            # Character data is delivered by neither startElement nor
            # endElement, so the suppression there does not cover it. Text
            # sitting directly inside a nested article — not wrapped in a
            # child that pushes a buffer of its own — would otherwise land in
            # whichever buffer is open above, which is the article's own
            # paragraph. Discarding it needs no compensating pop: buffers are
            # pushed and popped by the element handlers, never here.
            return
        self._append_text(content)
        current_table = self.current_table
        if current_table is not None:
            current_table.append_cell_text(content)

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

        if name in _NESTED_ARTICLE_ELEMENTS and self.nested_article_depth:
            # The depth test is unreachable by construction — expat rejects a
            # close with no matching open, so no test can kill it — and is
            # kept only so a future non-SAX feed cannot drive the depth
            # negative and suppress the rest of the document.
            self.nested_article_depth -= 1

        text = element_text.strip()
        normalized_text = _normalize_whitespace(element_text)

        # --- Handle element end ---

        if self.nested_article_depth:
            # Still inside a nested article, so this close is not the
            # article's either. Tested before every handler rather than at
            # each one, because a handler added later would otherwise have to
            # remember to opt out.
            #
            # Most handlers are already inert here — they need in_front,
            # in_article_meta, in_body or a non-empty section_stack, none of
            # which the suppressed open ever set. Two are not, and they are
            # why this half is load-bearing on an *ordinarily* ordered
            # document rather than only an out-of-order one. </abstract>
            # flushes its buffer without clearing it, and only the opening tag
            # clears, so a nested one re-emits the article's own abstract a
            # second time. And <article-id> falls through to
            # _classify_article_id when its type is absent or unrecognised,
            # which would let a review round's identifier answer for the
            # article's.
            pass
        elif name == "front":
            self.in_front = False
        elif name == "article-meta":
            self.in_article_meta = False
        elif name == "contrib-group":
            # Popping restores the enclosing group's role, which is what a
            # nested roster inside <collab> needs. It also empties the stack
            # at the outermost close, and that half matters for a <contrib>
            # with no enclosing group at all — out of place for JATS, and so
            # exactly what a lenient parse must still answer for. Left on the
            # stack, a closed group's role would decide it: after an editor
            # group the stray contributor is dropped, after an author group
            # it is collected, and neither is an answer the document gave.
            # Guarded because a close with nothing open would otherwise raise
            # on malformed input; SAX makes that unreachable today.
            if self.contrib_group_stack:
                self.contrib_group_stack.pop()
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
                        # The document has declared this value the DOI, so no
                        # later untyped id may replace it on the strength of
                        # merely looking like one.
                        self.doi_is_typed = True
                    elif id_type in (
                        "pmc",
                        "pmcid",
                        "pmcid-ver",
                        "pmcaid",
                        "pmcaiid",
                    ):
                        # All PMC-related identifiers — store the canonical
                        # PMC ID only from "pmc" or "pmcid" variants (not
                        # versioned or internal PMC article IDs).
                        if id_type in ("pmc", "pmcid") and not self.pmc_id:
                            self.pmc_id = text
                    elif id_type in ("pmid", "pubmed"):
                        self.pmid = text
                    else:
                        self._classify_article_id(text)
                else:
                    self._classify_article_id(text)
                self.current_article_id_type = None

        elif name == "abstract":
            # Flush the final section when it has a title OR body text, so a
            # titled-but-empty trailing subsection (or a title-only abstract)
            # is not silently dropped.
            if self.current_abstract_text or self.current_abstract_title:
                content = " ".join(self.current_abstract_text)
                self.abstract_sections.append(
                    JATSAbstractSection(title=self.current_abstract_title, content=content)
                )
            self.in_abstract = False
        elif name == "title":
            # Routed by the element that owns it, like a <label> and a
            # <graphic>, and for the reason both are: <sec> is far from the
            # only JATS element carrying a <title>. <caption> does — it is the
            # caption's lead, not a heading — and so do <fn-group> (modelled
            # `(label?, title?, (fn|p)+)`), <ref-list>, <glossary>, <app> and
            # <boxed-text>. Asked only "is a section open?", any of them
            # renamed it: eLife's *Additional information* section holds an
            # <fn-group> per contribution type and the last one won
            # (PMC8754430, issue #125), and a <boxed-text><caption><title> at
            # section level did the same (issue #130). The result is not a
            # blank but a heading the publisher never wrote.
            #
            # The parent test needs no enumeration of the elements that carry
            # a <title>, which is what made this uncloseable by inspection —
            # the same argument the <label> rule turns on.
            parent = self.element_stack[-2] if len(self.element_stack) >= 2 else ""
            if parent == "caption":
                self._append_caption_text(normalized_text)
            elif self.in_abstract:
                if self.current_abstract_text or self.current_abstract_title:
                    content = " ".join(self.current_abstract_text)
                    self.abstract_sections.append(
                        JATSAbstractSection(title=self.current_abstract_title, content=content)
                    )
                    self.current_abstract_text = []
                self.current_abstract_title = text
            elif parent == "sec" and self.section_stack:
                # A structured abstract's <sec><title> has the same parent and
                # is answered by the branch above, not this one — but not
                # because of the order: <sec> inside an <abstract> pushes no
                # builder, so `section_stack` is what actually keeps them
                # apart, and swapping the two branches changes nothing today.
                # The order is kept as the cheaper guard of the two to reason
                # about, and is recorded here as not load-bearing so a later
                # reader does not take it for one.
                self.section_stack[-1].title = normalized_text
        elif name == "p":
            if self.in_figure or self.in_table_wrap:
                # Figure and table internals, tested before every prose branch
                # because a <fig> or <table-wrap> usually sits inside a <sec>:
                # asking about the section first would blank the caption and
                # reprint it as article prose. Only <caption> content is kept,
                # and `_append_caption_text` decides which caption's owner gets
                # it. Cell and footnote <p> is dropped — characters() already
                # collects cells into the rendered table, so letting it through
                # would duplicate furniture into the prose and count it towards
                # has_body.
                self._append_caption_text(normalized_text)
            elif self.in_abstract:
                if normalized_text:
                    self.current_abstract_text.append(normalized_text)
            elif (self.in_body or self.in_back) and self.section_stack:
                if self.in_body and normalized_text:
                    self.body_paragraph_count += 1
                self.section_stack[-1].paragraphs.append(normalized_text)
            elif self.in_body and normalized_text:
                # An unsectioned <body> child. Empty paragraphs are dropped
                # rather than opening a section, so a <body> holding nothing
                # but whitespace stays body-less.
                if self.implicit_body_section is None:
                    self.implicit_body_section = _SectionBuilder()
                self.body_paragraph_count += 1
                self.implicit_body_section.paragraphs.append(normalized_text)

        elif name == "body":
            self._flush_implicit_body_section()
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
            if self.figure_stack:
                # Guarded because a close with nothing open would otherwise
                # raise on malformed input; SAX makes that unreachable today.
                # The way it stops being unreachable is a suppression region
                # guarded on `startElement` alone — which is what
                # `_NESTED_ARTICLE_ELEMENTS` is, and why both of its halves
                # are guarded together.
                frame = self.figure_stack.pop()
                self.figure_slots[frame.slot] = frame.builder.build()
        elif name == "caption":
            # Popping restores the enclosing caption, which is the half a
            # boolean got wrong: cleared by the inner close, it truncated the
            # outer caption at the point the inner one ended. Guarded because
            # a close with nothing open would otherwise raise on malformed
            # input; SAX makes that unreachable today.
            if self.caption_stack:
                self.caption_stack.pop()
        elif name == "label":
            # A <label> belongs to the element that encloses it, and JATS
            # spells it as a direct child, so the parent decides outright
            # (`element_stack[-1]` is this <label>, as at <article-id> above).
            #
            # Routing on the ambient "is an exhibit open?" flags instead let
            # any labelled descendant overwrite the exhibit's number: a
            # <table-wrap-foot><fn>'s "a"/"b"/"*" marker did so for 12.0% of
            # the 225 surveyed articles (issue #116), and a <fn-group>'s
            # "Notes", a <disp-formula>'s "(1)" and eLife's
            # <supplementary-material> "Figure 1—source data 1" all did the
            # same. A swallowed label is not a blank either — the renderer
            # substitutes `Table {i + 1}` or `Figure {i + 1}`, so the symptom
            # is an invented number.
            #
            # Asking the parent needs no enumeration of the containers that
            # may carry a <label>, which is what a depth counter needed and
            # what could not be completed by inspection. It is also exact
            # where a depth is merely close: an exhibit opened *inside* a
            # footnote still gets its own label, since its <label>'s parent is
            # the exhibit either way.
            #
            # THE PREMISE IS MEASURED (`scripts/sample_jats_exhibits.py`,
            # issue #131). Over 276 open-access Europe PMC articles carrying
            # 2,067 exhibits: 2,033 exhibits carry a <label> as a direct child
            # and 2,033 carry one anywhere — the same number, so **no exhibit
            # in the sample carries its label only indirectly** and this rule
            # cannot lose one. Of 2,173 labels inside an exhibit, 93.6% are
            # the exhibit's own; the rest sit in <fn> (105), <list-item> (34)
            # and <supplementary-material> (1).
            #
            # The depth rule this replaced would still mis-assign the last two
            # groups — 35 labels in 2 of the 276 articles (0.7%). Small, but
            # both are corruptions rather than omissions: a <list-item>'s "•"
            # became PMC12996797's table number, and eLife's "Figure 3—source
            # data 1." became PMC12999171's figure number.
            parent = self.element_stack[-2] if len(self.element_stack) >= 2 else ""
            if parent == "fig" and self.current_figure is not None:
                self.current_figure.label = text
            elif parent == "table-wrap" and self.current_table is not None:
                self.current_table.label = text
            elif self.in_ref and self.current_reference:
                self.current_reference.label = text

        elif name == "thead":
            current_table = self.current_table
            if current_table is not None:
                current_table.end_header()
        elif name == "tbody":
            current_table = self.current_table
            if current_table is not None:
                current_table.end_body()
        elif name == "tr":
            current_table = self.current_table
            if current_table is not None:
                current_table.end_row()
        elif name in ("th", "td"):
            current_table = self.current_table
            if current_table is not None:
                current_table.end_cell()
        elif name == "table-wrap":
            if self.table_stack:
                # Guarded for the reason </fig> is; SAX makes it unreachable.
                table_frame = self.table_stack.pop()
                self.table_slots[table_frame.slot] = table_frame.builder.build()

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

    def _is_author_contrib(self, contrib_type: str | None) -> bool:
        """Is this ``<contrib>`` one of the article's authors?

        JATS spells the contributor role two ways, and the per-contrib one is
        the minority form in PMC: ``content-type="author"`` on the enclosing
        ``<contrib-group>``, with bare children, is the dominant form.
        Reading only ``contrib-type`` drops every author from roughly three
        open-access articles in five (issue #111: 45 of 79 sampled, 57.0%; a
        249-article sample put it at 60.6%).

        Five rules, of which #111's sample earns two. **Measured:** a
        contributor's own declaration decides on its own — it has to be able
        to say ``editor`` inside an author group, and 33 of the 79 articles
        rely on it; and a group naming any other role is taken at its word,
        since 23 carry an ``editor`` group beside the author group and
        reading its members as authors would be a new defect rather than a
        wider fix.

        **Not measured** — the sample contains no instance of any of them, so
        each rests on convention rather than on the corpus. A ``<contrib>``
        that declares nothing inherits the innermost enclosing group that
        does; a group declaring nothing inherits in turn, and at the outermost
        level that means authors. An empty attribute declares nothing rather
        than declaring "not an author": read as a declaration it drops the
        contributor, the same silent loss for a document whose only fault is
        a stray empty attribute. And the comparison folds case — which JATS
        itself asks for, on the Tag Library's own ``@article-type`` page:
        *"Upper/lower/mixed case in attribute values … is likely to be
        variable and thus unreliable for search/discovery. If possible, JATS
        recommends a case-insensitive search for such values."* That is
        written of ``@article-type`` rather than of these two attributes, so
        it is precedent and not a citation; it is also the module's own habit
        (``pub-id-type`` is folded too), and folding cannot cost anything,
        since a role that is not ``author`` in any casing is excluded either
        way while an unfolded ``Author`` drops a whole group.

        The role is read from a *stack* of open groups rather than one value,
        because ``<collab>`` may contain a ``<contrib-group>`` — a
        collaboration's member roster. Innermost *declared* wins, so a bare
        roster inside an ``editor`` group stays editors instead of resetting
        to the authors default.

        Args:
            contrib_type: The ``contrib-type`` attribute of this
                ``<contrib>``, or ``None`` where it carries none.

        Returns:
            ``True`` where the contributor is an author of this article.
        """
        if contrib_type:
            return contrib_type.lower() == "author"
        group_type = next((t for t in reversed(self.contrib_group_stack) if t), None)
        return not group_type or group_type.lower() == "author"

    def _classify_article_id(self, text: str) -> None:
        """Classify an article-id whose `pub-id-type` was absent or unknown.

        Shape is the only evidence here, so every branch defers to a value
        that arrived with a type declaring what it was — otherwise document
        order decides the answer, and a publisher's internal id that happens
        to resemble a DOI overwrites the DOI itself.
        """
        if text.startswith("10.") and "/" in text:
            # A DOI is a prefix and a suffix joined by a slash; the slash is
            # not optional.  SAGE stamps every article with a filename-form
            # copy of its DOI as `pub-id-type="publisher-id"`
            # (`10.1177_20552076251406653`), which reaches here through the
            # unknown-type fallthrough and clears a bare `10.` prefix test.
            if not self.doi_is_typed:
                self.doi = text
        elif text.startswith("PMC"):
            # No flag needed here, unlike the DOI: `pmc_id` is already
            # first-wins in the typed branch, and the caller's `known_pmc_id`
            # seeds it, so "already set" covers both — and used not to be
            # tested at all, letting an untyped id overwrite either one.
            if not self.pmc_id:
                self.pmc_id = text
        elif text.isdigit() and len(text) >= 7:
            # Bare numeric IDs without a recognised pub-id-type are
            # ambiguous — they could be PMC article IDs, publisher
            # internal IDs, etc.  Never guess; PMIDs will arrive via
            # the typed path (pub-id-type="pmid").
            logger.debug("Ignoring untyped numeric article-id: %s", text)


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
            figures=h.build_figures(),
            tables=h.build_tables(),
            references=h.references,
            has_body=h.body_paragraph_count > 0,
            suppressed_nested_articles=h.suppressed_nested_articles,
        )

    def to_html(self) -> str:
        """Parse XML and return HTML string."""
        return _build_html(self.parse())

    def parse_with_html(self) -> tuple[JATSArticle, str]:
        """Parse XML once and return both the article and its HTML rendering.

        Callers that need to inspect the article (to check
        :attr:`~bmlib.fulltext.models.JATSArticle.has_body`, say) and also
        render it should use this rather than calling :meth:`parse` and
        :meth:`to_html` in turn, which would parse the document twice.

        Returns:
            A tuple of the parsed article and its HTML.
        """
        article = self.parse()
        return article, _build_html(article)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------


def _build_html(h: JATSArticle) -> str:
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
        for abstract_sec in h.abstract_sections:
            if abstract_sec.title:
                parts.append(
                    f"<p><strong>{html_escape(abstract_sec.title)}:</strong> "
                    f"{html_escape(abstract_sec.content)}</p>"
                )
            else:
                parts.append(f"<p>{html_escape(abstract_sec.content)}</p>")

    # Body sections
    for body_sec in h.body_sections:
        parts.extend(_format_body_section_html(body_sec, level=2))

    # Figures
    if h.figures:
        parts.append("<h2>Figures</h2>")
        for i, fig in enumerate(h.figures):
            fig_num = fig.label or f"Figure {i + 1}"
            anchor_id = fig.id or f"fig{i + 1}"
            parts.append(f'<figure id="{html_escape(anchor_id)}">')
            if fig.graphic_url:
                full_url = _build_exhibit_url(fig.graphic_url, h.pmc_id)
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
            elif tbl.graphic_url:
                # Only where there is no markup. A <table-wrap> may carry both,
                # and where it does the <table> is the better rendition —
                # emitting both shows one table twice. `JATSTableInfo` holds
                # the href either way; choosing between them is the renderer's.
                full_url = _build_exhibit_url(tbl.graphic_url, h.pmc_id)
                parts.append(
                    f'  <img src="{html_escape(full_url)}" '
                    f'alt="{html_escape(tbl_num)}" loading="lazy">'
                )
            parts.append("</div>")

    # References
    if h.references:
        parts.append("<h2>References</h2>")
        parts.append('<ol class="references">')
        for ref in h.references:
            parts.append(f'  <li id="ref-{html_escape(ref.id)}">{_format_ref_html(ref)}</li>')
        parts.append("</ol>")

    return "\n".join(parts)


def _format_journal_html(h: JATSArticle) -> str:
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


def _format_identifiers_html(h: JATSArticle) -> str:
    ids: list[str] = []
    if h.doi:
        ids.append(f'DOI: <a href="https://doi.org/{html_escape(h.doi)}">{html_escape(h.doi)}</a>')
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


def _build_exhibit_url(path: str, pmc_id: str) -> str:
    """Resolve an exhibit's ``<graphic>`` href for the rendered HTML.

    Named for the exhibit rather than the figure since issue #127: a
    ``<table-wrap>`` deposits an image the same way and resolves it the same
    way. A relative href is resolved against Europe PMC's per-article ``bin/``
    directory, and one carrying no image extension is given ``.jpg``, which is
    what that service serves.

    Args:
        path: The href as deposited.
        pmc_id: The article's PMC identifier, with or without the prefix; an
            empty one leaves a relative href alone rather than guessing a host.

    Returns:
        An absolute URL, or *path* unchanged when there is nothing to resolve
        it against.
    """
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
