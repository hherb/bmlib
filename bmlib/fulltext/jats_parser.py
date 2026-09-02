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
from typing import ClassVar, Generic, TypeVar

from bmlib.fulltext._parse_audit import ParseUnwindState, unwind_diagnostics
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
    """One ``<contrib>`` being read, in whichever spelling it names its contributor.

    JATS models that name as ``(name | string-name | collab | ...)``. All three
    are collected, because refusing the two undivided ones dropped a
    contributor from 34 of the 1,025 open-access articles drawn in the PR #118
    review (3.3%, not reproducible from a committed corpus), and *every* author
    from an article deposited with ``<string-name>`` (#140) — each as a
    well-formed shorter list rather than as an error. That draw counted
    ``<contrib>`` elements carrying no ``<surname>``, which is a set the two
    spellings share, so it is not a rate for either one of them.
    """

    surname: str = ""
    given_names: str = ""
    affiliations: list[str] = field(default_factory=list)
    collab: str = ""
    string_name: str = ""

    def build(self) -> JATSAuthorInfo | None:
        """The contributor, or ``None`` where the ``<contrib>`` named nobody.

        ``None`` now means what it says — no spelling of a name arrived —
        rather than "no ``<surname>``", which was true of every collaboration.
        The call site counts it, since dropping a contributor in silence is
        what kept both spellings invisible for as long as they were.

        The predicate is :attr:`JATSAuthorInfo.is_named`, asked of the built
        contributor rather than repeated over the builder's own fields: one
        definition of "named", on the public type. Constructing first and
        discarding is deliberate — a raising ``__post_init__`` would be the
        cheaper-looking guard and is exactly #129, an exception thrown from
        inside a SAX callback into ``service.py``'s tier-level
        ``except Exception``, costing the whole article.
        """
        info = JATSAuthorInfo(
            surname=self.surname,
            given_names=self.given_names,
            affiliations=list(self.affiliations),
            collab=self.collab,
            string_name=self.string_name,
        )
        return info if info.is_named else None


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

# BOTH SETS ARE DEFENSIVE, AND THE MEASUREMENT SAYS SO. Measured by
# `scripts/sample_jats_exhibits.py` (issues #131, #138) over the two committed
# draws — 997 articles each, drawn deterministically from a named PMC OA
# baseline package and measured on the rendition FullTextService feeds this
# parser, Europe PMC's `fullTextXML` rather than the package's own archive
# bytes: **7,055 <graphic> sit inside an <alternatives>** (6,503 recent, 552
# back-filled) and of those **zero declare a mime-subtype at all** and **zero
# are archival by either test**. So neither tier fires, and the ARCHIVAL rank
# is unreached.
#
# The 276-article draw this used to be quoted from (912 figures, 1,819
# members, the same answer) is not in the repo and is superseded by these —
# issue #132 is why a figure nobody can re-derive was worth re-taking even
# where it agreed.
#
# Extensions are counted over every deposit rather than over <alternatives>
# members alone — the sampler holds the two in separate counters and never
# cross-tabulates them, so no extension figure scoped to the members is
# derivable from either corpus. At that wider scope, across all 13,617:
# .jpg and .gif in both windows and .png in the back-filled one, with **no
# deposit in either window whose href carries no extension**. That is a
# property of the *served* rendition and not of publishers, and
# `tests/data/jats_exhibits.rendition.json` is where the difference is
# recorded: `graphic_extensions` disagrees in 272 of 300 compared articles,
# and on the archive side of those 272 it records 1,262 extensionless hrefs
# of 2,046 deposits, in 241 articles. Scope it there and no further — the
# artifact records a field only where the renditions disagree, so it says
# nothing about the 28 that agree. So on the bytes this parser is handed
# _ARCHIVAL_EXTENSIONS always has something to read, where on archive bytes
# it frequently would not.
#
# They are kept rather than deleted because the failure they prevent is silent
# and permanent: an undeclared master deposited first ranks FULL, wins under
# the strictly-better rule, and leaves the figure pointing at something no
# browser renders. "No instance in 1,994 articles" is not "cannot happen", and
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

    **THE TABLE SIDE MEASURES EMPTY, and this says so rather than implying a
    population** (issue #135, now answered). Across the two committed draws —
    2,448 ``<table-wrap>``, all of them in the recent window — **92 carry a
    ``<graphic>`` of their own and not one carries two**. So the ranking below
    is *unexercised* on tables rather than confirmed there: with a single
    deposit it and plain first-wins agree, and nothing is contradicted.
    Sharing the rule is still right, for the reason above; what would be wrong
    is restating it as publisher behaviour, which an earlier draft of this
    docstring did.

    The back-filled window contributes **no denominator at all** — 0
    ``<table-wrap>`` in 997 articles. So the answer above rests entirely on
    the recent window, and the redrawn back-filled one can no longer
    corroborate or contradict it. (*That the 1996-1998 ``oa_comm`` material is
    scanned page images with no tabular markup is an inference*, from 0 tables
    beside 627 figures and 3,873 ``.png`` deposits; no counter measures it.)

    The instrument had to be corrected before that number meant anything. The
    sampler counted a table's deposits with a whole-subtree walk while the
    parser routes a ``<graphic>`` by its **owner**, and the first live run
    made the difference real: unscoped, four of ten recent-window tables
    "carried several deposits", which were the ``<td>`` cell images of two
    articles. Scoped to what the parser would route, the count is zero.

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

        **The two committed draws are the evidence, and they are what a
        reader can re-derive** (``scripts/sample_jats_exhibits.py``, issue
        #138 — 997 articles per window, drawn from a named PMC OA baseline
        package and measured on Europe PMC's ``fullTextXML``): of **4,602**
        recent figures carrying a ``<graphic>``, **58.1%** [56.7-59.6] carry
        more than one and **57.3%** [55.9-58.8] end on a thumbnail; of **627**
        back-filled ones, **44.0%** [40.2-47.9] on both counts. **0%** deposit
        a thumbnail *first* in either — so the convention that motivates
        ranking over plain first-wins appears in neither window, and ranking
        earns its place on the other number: it is what stops half of all
        figures resolving to a preview.

        Two earlier figures are superseded and neither is re-derivable: the
        58.0% / 52.9% above, from the 225-article survey, and **49.9% /
        49.5%** from a 276-article draw that is not in the repo (issue #132).
        The share sits between them depending on the window; the shape of the
        finding — around half of all figures, and never a thumbnail first — is
        the part that reproduces across every draw taken.

        **This population is rendition-dependent, which is what #138 found.**
        Measured on the same identifiers' *archive* bytes,
        ``last_is_thumb`` **differs in 156 of 300 compared articles, and where
        it differs the archive measures 0 against 781 served**. Scope it that
        way and no further: ``tests/data/jats_exhibits.rendition.json``
        records a field only where the two renditions disagree, so an
        agreeing article contributes to neither side and the archive's total
        over all 300 is not derivable from it. That is this module's own rule
        about a count being of what was looked for, applied to the artifact
        that establishes the rule.

        **Do not attach a mechanism to it.** An early draft said the archive
        deposits one bare ``<graphic xlink:href="…-g001">`` per figure where
        Europe PMC synthesises an image/thumb pair; that is true of a
        spot-checked article and false in general — ``PMC12169732`` deposits
        its own four thumbnails as ``specific-use="thumbnail"`` where Europe
        PMC re-labels them ``content-type="thumb"``, and both renditions
        measure four. That article was drawn *out* of the held sample by the
        redraw, so it is now a **live spot-check** (re-run 2026-09-02) and not
        a row of the committed artifact — which is exactly why the caveat
        above is structural rather than resting on it: the artifact records
        disagreements alone, so no archive total can be read off it whatever
        one article does. The finding survives either way, and it is
        decisive:
        these percentages describe the bytes ``FullTextService`` hands this
        parser, and a draw measured from a baseline package would read the
        whole ranking rule as unreached.

        *Strictly* better is what makes the first deposit win among equals.

        **Every percentage above is measured over figures.** Tables reach this
        method too since issue #127, and no draw has found one carrying a
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
class _ContribFrame:
    """One open ``<contrib>`` bmlib is collecting as an author.

    A stack of these, for the reason :class:`_ExhibitFrame` is one: ``<collab>``
    may carry a ``<contrib-group>`` of the collaboration's own members, so a
    ``<contrib>`` can open inside another. Held as a single slot — as the first
    cut of this fix was, no released version having had a ``collab`` field to
    lose — each member would overwrite the consortium's builder and its close
    would clear the flag, so ``</collab>`` is reached with nothing to write the
    collaboration's name into and the outer ``</contrib>`` finds nothing to
    build (issue #120).

    ``slot`` is the index reserved in ``author_slots`` when the ``<contrib>``
    opened. A contributor is *built* at its end tag and has to be *listed* at
    its start: appending at the close puts a consortium behind the members it
    encloses, which is not the order the document gave.
    """

    slot: int
    builder: _AuthorBuilder


@dataclass
class _FormulaFrame:
    """One open ``<inline-formula>`` or ``<disp-formula>`` (issue #147).

    A formula holds its expression in one of several *encodings* of the same
    thing, and the rule is to emit exactly one of them. Merging every child —
    what ``_INLINE_ELEMENTS`` would have done — prints the formula twice
    wherever both are deposited: 1,087 formulas in the committed recent corpus
    and 188,473 across the ``PMC012xxxxxx`` baseline package carry a LaTeX and
    a MathML encoding of one expression.

    Only ``<tex-math>`` needs a field. MathML accumulates no buffer, so its
    leaf text is already sitting in the formula's own — which is why
    :func:`_render_formula` takes that buffer as its fallback rather than this
    frame carrying a second list, and why a MathML deposit binding the
    namespace to something other than ``mml`` keeps exactly today's behaviour
    instead of depending on a literal prefix match the way issue #128 does.

    A stack of these, for the reason :class:`_ExhibitFrame` is one: formulas
    nest. 21 ``<inline-formula>`` in the 880-article served draw sit inside a
    ``<disp-formula>``, where the inner emission lands in the outer's buffer —
    and the outer emits it as its own text *where the outer has no LaTeX of
    its own*, which is the condition the buffer fallback carries and this
    sentence used to state unconditionally. An outer carrying a ``<tex-math>``
    renders that and the inner rendition is dropped, which is the encoding
    choice working rather than a loss: both describe the same expression.

    ``label`` is the equation number — 1,459 of the committed corpus's 1,915
    display formulas carry one, so the ``(1)`` that body prose cross-references
    is the common case and not the exception. It is read from a ``<label>``
    whose *parent* is this formula, the rule issue #116 established. Whether it
    is *printed* is the caller's decision and not this frame's; see
    :func:`_render_formula` and ``_TABLE_CELL_ELEMENTS``.
    """

    display: bool
    label: str = ""
    #: Every ``<tex-math>`` this formula has closed, in document order. A list
    #: because the choice cannot be made while the encodings are still
    #: arriving: a streaming "first wins" rule would pick the wrong encoding
    #: wherever the MathML is deposited first.
    #:
    #: It is **not** a list so that several deposits can all be emitted.
    #: ``<alternatives>`` may hold more than one ``<tex-math>``, but those are
    #: alternative encodings of one expression, so :func:`_render_formula`
    #: takes the first that renders to anything and drops the rest — joining
    #: them printed the expression twice, which is the outcome
    #: ``_FORMULA_ELEMENTS`` says the design exists to prevent. That shape
    #: measures **0** across both committed corpora and 0 of 501,132 formulas
    #: scanned in the ``PMC012xxxxxx`` package, so the rule is unexercised
    #: rather than confirmed, and is stated here so a later reader does not
    #: re-derive the joining version from this field's type.
    #:
    #: **Say which population that is.** 4,377 of the package's 188,473
    #: both-encoding formulas are MathML-first — 2.3% of *formulas*, but they
    #: sit in **37 of its 97,909 articles**, ~118 apiece. So it is one
    #: publisher's house style rather than a rate: a 997-article draw expects
    #: none, and a random 4,000-article one measured 2. The rule stands on the
    #: content model, which admits either order, and not on the count; the
    #: count is here so that a later reader meeting a zero does not conclude
    #: the order never varies.
    latex: list[str] = field(default_factory=list)


@dataclass
class _ReferenceBuilder:
    id: str = ""
    label: str = ""
    #: One entry per ``<mixed-citation>`` in this ``<ref>``, holding that
    #: element's **raw** text. A ``<ref>`` may carry several — JATS admits it,
    #: and 216 references in 21 of 880 local PMC articles do — so this is a
    #: list and not a slot, which is what an unconditional assignment made it
    #: (issue #149: every part but the last was discarded).
    #:
    #: Raw rather than normalised, and joined with **nothing** between them,
    #: because that is what the deposit holds: the character data between
    #: consecutive citation elements is empty in 586 of 586 occurrences, and
    #: the visual separation lives inside the parts — RSC's ``<label> (b) </label>``
    #: carries its own leading space. Normalising each part would eat it and
    #: run ``(a)`` into ``(b)``; normalising once in :meth:`build` keeps it,
    #: while still not inventing a space in front of a tail that opens with
    #: punctuation. That is the module's "strip once, at the outermost call"
    #: rule, already written down for ``_text_with_formatting``.
    citation_parts: list[str] = field(default_factory=list)
    #: How many citation elements this ``<ref>`` has opened, counting both
    #: spellings. Only the first fills the structured fields; see the
    #: ``<mixed-citation>`` arm of ``startElement``.
    citation_element_count: int = 0
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
            citation=_normalize_whitespace("".join(self.citation_parts)),
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


#: The delimiter pairs a depositor may already have written around a LaTeX
#: expression, tested in this order. ``$$`` precedes ``$`` because the shorter
#: one is a prefix of the longer.
_LATEX_DELIMITERS: tuple[tuple[str, str], ...] = (
    ("$$", "$$"),
    ("\\[", "\\]"),
    ("\\(", "\\)"),
    ("$", "$"),
)

#: The members of :data:`_LATEX_DELIMITERS` that put a renderer into *display*
#: mode, which breaks the line. Inside a sentence that is wrong markup rather
#: than mere under-styling, which is what makes the re-delimiting rule in
#: :func:`_latex_expression` one-directional.
_DISPLAY_LATEX_DELIMITERS = frozenset({("$$", "$$"), ("\\[", "\\]")})


def _delimiter_pair(body: str) -> tuple[str, str] | None:
    """The delimiter pair the depositor wrote around ``body``, if any.

    A pair counts only when the body has room for both halves — ``"$"`` opens
    and closes with the same character and is not a delimited body.

    Args:
        body: The deposit's text, already whitespace-normalised.

    Returns:
        The matching member of :data:`_LATEX_DELIMITERS`, or ``None``.
    """
    for opening, closing in _LATEX_DELIMITERS:
        if (
            body.startswith(opening)
            and body.endswith(closing)
            and len(body) >= len(opening) + len(closing)
        ):
            return opening, closing
    return None


def _latex_expression(deposit: str, display: bool) -> str:
    """Render one ``<tex-math>`` deposit as an expression fit for prose.

    A ``<tex-math>`` does not hold an expression. 99.9% of 4,422 deposits
    sampled from the ``PMC012xxxxxx`` baseline package are a whole LaTeX
    *document* — ``\\documentclass[12pt]{minimal}``, a run of ``\\usepackage``
    lines, then ``\\begin{document}`` — so merging the element's text as it
    stands injects some 300 characters of preamble per formula, which is worse
    than the drop it replaces. **Say which population that is**: of the 7,769
    document-wrapped deposits sampled, every one carries exactly *one*
    ``\\begin{document}``/``\\end{document}`` pair, so the split below can take
    the first and last marker without choosing between several; and the same
    holds for 147 of 147 in two articles fetched live from Europe PMC
    (PMC12000231 and PMC12044768, 2026-09-02) — the rendition the parser is
    actually fed, which is the half issue #138 had to learn separately. That
    is a count of *wrapped* deposits and not of all of them, the 3 bare
    expressions among the 4,422 carrying no pair at all.

    **The two markers are read independently, because a deposit carrying one
    of them fails closed.** Requiring both let a truncated deposit fall
    through to the bare-expression path, which then delimited the preamble and
    merged it into the prose — ``$$\\documentclass…\\begin{document}$$E=mc^2$$``,
    the exact outcome this function exists to prevent, *plus* the doubled pair
    the delimiter rule below exists to prevent. Splitting on whichever marker
    is present recovers the expression instead. The population measures 0
    unpaired deposits in both corpora, so this is severity and not frequency:
    it is silent, and it lands in the HTML ``FullTextService`` caches.

    **The depositor's own delimiters are kept, except where they would put a
    sentence into display mode.** 96.0% of the bodies are already wrapped in
    ``$$…$$`` and 3.7% in ``$…$``, so adding a pair unconditionally gives
    ``$$$$…$$$$``. But that ``$$`` is not a claim about the deposit's context:
    measured over one Europe PMC package, **98.6% of 20,251 inline
    ``<tex-math>`` bodies carry ``$$…$$``** (19,962; 86 carry ``$…$``, 203
    none), and inline formulas cannot genuinely be 98.6% display math — the
    ``minimal``-documentclass converter emits that wrapper for both contexts.
    Left verbatim it rendered ``'×'`` as ``'$$\\times$$'`` inside a figure
    caption. So a *display* pair on an inline formula is re-spelled ``$…$``.

    The rule is deliberately **one-directional**: an inline pair on a display
    formula is left alone, because the two errors do not cost the same. A
    display delimiter inside a sentence breaks the line — wrong markup — while
    an inline delimiter on a formula that stands alone merely under-styles it,
    and re-spelling that way would be inventing a claim rather than reading
    one. A body carrying several delimited runs (``$a$ + $b$``) is left alone
    too: its outer characters are not one pair around one expression, and
    stripping them would corrupt it.

    A body opening an environment (``\\begin{aligned}``, 0.2%) is left alone
    for the same reason: the environment establishes its own math mode, and
    ``$$\\begin{equation}…`` is not valid LaTeX.

    Args:
        deposit: The ``<tex-math>`` element's text.
        display: Whether the formula is a ``<disp-formula>``.

    Returns:
        The expression, delimited, or ``""`` if the deposit held nothing.
    """
    body = deposit
    if "\\begin{document}" in body:
        body = body.split("\\begin{document}", 1)[1]
    if "\\end{document}" in body:
        body = body.rsplit("\\end{document}", 1)[0]
    body = _normalize_whitespace(body)
    if not body:
        return ""
    if body.startswith("\\begin{"):
        return body
    pair = _delimiter_pair(body)
    if pair is None:
        return f"$${body}$$" if display else f"${body}$"
    if display or pair not in _DISPLAY_LATEX_DELIMITERS:
        return body
    opening, closing = pair
    inner = body[len(opening) : len(body) - len(closing)].strip()
    if not inner or opening in inner or closing in inner:
        # Not one delimited expression but several runs, or an empty pair.
        return body
    return f"${inner}$"


def _pad_as_deposited(rendered: str, buffered: str, display: bool) -> str:
    """Space a merged formula the way the deposit spaced it.

    Two rules, and the second is the module's own, already written down for
    ``_text_with_formatting``: **a run's edge whitespace is re-emitted outside
    its markers**. :func:`_render_formula` normalises, so an inline formula
    whose deposit reads ``<inline-formula> k </inline-formula>mer`` would
    otherwise lose the separation the publisher put *inside* the element —
    measured over the 880-article local corpus, that welded ``'EndMatrix
    represents'`` into one word and ``'−minus 0.505'`` into another. Issue
    #147 is about formulas that were **dropped**; re-spacing text that already
    reached the prose is collateral, so the inline path keeps the deposit's
    spacing exactly.

    The first rule is the display one, and there the deposit has no spacing to
    keep: a ``<disp-formula>`` is a block, rendered on a line of its own, so
    the markup puts nothing between it and the text either side. Merged
    verbatim it welds — the same corpus ran ``'following reactions:'`` straight
    into the first equation, and consecutive equations into each other. One
    space either side is the least that can be invented and still not join two
    expressions into one; the paragraph normalises the doubles away.

    Args:
        rendered: The formula's chosen rendition, already normalised.
        buffered: The formula's own text buffer, whose *edges* are read here.
        display: Whether this is a ``<disp-formula>``.

    Returns:
        The rendition with whatever spacing it merges with.
    """
    if display:
        return f" {rendered} "
    lead = " " if buffered[:1].isspace() else ""
    trail = " " if buffered[-1:].isspace() else ""
    return f"{lead}{rendered}{trail}"


def _render_formula(frame: _FormulaFrame, buffered: str, *, numbered: bool) -> str:
    """The one rendition a formula contributes to the text around it.

    LaTeX wins wherever a ``<tex-math>`` arrived, because it is the deposit's
    exact expression where the alternative is a flattening. The buffer serves
    otherwise, and *otherwise* is the common case rather than a fallback: it
    carries the leaf text of a MathML encoding (which outnumbers LaTeX 10,202
    to 1,398 in the committed recent corpus), a formula deposited as ordinary
    ``<italic>``/``<sub>``/``<sup>`` markup (71 of the 141 encoding-less
    display formulas in the 880-article served draw), and a MathML deposit
    whose namespace prefix is not ``mml``.

    **The first deposit that renders to anything wins, and the buffer is
    reached whenever none does.** Both halves were defects. Joining every
    ``<tex-math>`` printed one expression twice wherever an ``<alternatives>``
    holds two LaTeX encodings of it — the outcome ``_FORMULA_ELEMENTS`` says
    the whole design exists to prevent, contradicted three comments away — and
    testing ``frame.latex`` for *presence* rather than for a rendition let an
    empty or preamble-only ``<tex-math>`` suppress a perfectly good MathML
    flattening sitting in the formula's own buffer, so ``'Before Vmax
    after.'`` became ``'Before after.'``. Both populations measure **0** in
    both corpora, which is why they are stated rather than assumed: the
    ``<alternatives>``-holds-two shape is what the ``latex`` field's own
    docstring cites as its reason for being a list.

    A formula holding nothing renders as nothing — 140 of the committed
    corpus's 1,915 display formulas hold nothing but a ``<graphic>`` once a
    ``<label>`` is set aside, and no text-taking rule recovers those. Emitting
    the label alone would be issue #162's defect: a number standing for
    content that is not there.

    **The equation number is printed only where a number is what the reader
    would read**, and that is a measured rule rather than a taste. Merged into
    a sentence it is not: over the 880-article local corpus that produced
    ``'as shown in eqn (2):2 τ = kn'``, where the label reads as a
    coefficient, and — two formulas running on — ``'NH3 + H2O → NH4+ + OH−2
    Al3+ + 3OH− → Al(OH)33 Al(OH)3'``, where each number welds onto the
    previous formula's tail and changes the chemistry. 21 insertions across
    that corpus opened with such a number. A corruption is worse than a blank
    (issues #116 and #162), and the prose introducing a merged equation names
    its number in nearly every case anyway. The caller decides; see
    ``_TABLE_CELL_ELEMENTS`` for the cell, which is the case that is *not* a
    sentence.

    Args:
        frame: The formula that is closing.
        buffered: Its own text buffer, whatever reached it that no arm took.
        numbered: Whether this formula's ``<label>`` is printed in front of
            the expression.

    Returns:
        The rendered formula, or ``""`` if it held no text at all.
    """
    body = next(
        (
            rendered
            for rendered in (_latex_expression(deposit, frame.display) for deposit in frame.latex)
            if rendered
        ),
        "",
    )
    if not body:
        body = _normalize_whitespace(buffered)
    if not body:
        return ""
    if numbered and frame.label:
        return f"{frame.label} {body}"
    return body


def _pad_row(row: list[str], count: int) -> list[str]:
    if len(row) >= count:
        return row[:count]
    return row + [""] * (count - len(row))


#: The widest ``colspan`` this module will honour. ``colspan`` is CDATA, so a
#: publisher may deposit any string of digits, and :meth:`_TableBuilder.end_cell`
#: materialises ``colspan - 1`` empty strings per cell — a 305-byte document
#: declaring ``colspan="20000000"`` rendered a 320 MB ``html_content`` at ~2.1 GB
#: peak RSS, which ``FullTextService`` then wrote to its disk cache; a larger
#: value raises ``MemoryError`` out of the SAX callback, which is #129's failure
#: verbatim — and ``MemoryError`` is not a ``_BUG_TYPES`` member, so the tier
#: chain reports the article as unavailable and says nothing. No real table is a
#: thousand columns wide, so the bound costs nothing a document plausibly meant.
_MAX_COLSPAN = 1000


def _read_span(attrs: xml.sax.xmlreader.AttributesImpl) -> tuple[int, str | None]:
    """Read a cell's ``colspan``, rejecting a value this module will not honour.

    ``colspan`` is CDATA in JATS, so ``"two"``, ``"1.5"``, a whitespace-only
    value and ``"20000000"`` are all well-formed markup. A bare ``int()`` raised
    a ``ValueError`` from inside the SAX callback, which propagated out of
    :meth:`JATSParser.parse` — and every call site in ``fulltext/service.py``
    sits under a tier-level ``except Exception`` logging at DEBUG, so one
    malformed attribute on one cell lost the whole article and the chain then
    reported it as unavailable from that source (issue #129).

    **Both ends are bounded, and for the same reason.** The low end needs no
    guard — :meth:`_TableBuilder.start_cell` clamps with ``max(1, …)`` — but the
    high end is what reintroduces #129: see :data:`_MAX_COLSPAN`. Bounding only
    the value ``int()`` refuses, and leaving the value it accepts unbounded, is
    the shape the original fix shipped with.

    A rejected span is **not** cosmetic, which is why this returns the raw value
    rather than swallowing it. :meth:`_build_html_table` fixes the column count
    from the first row and :func:`_pad_row` pads at the *end*, so a span rendered
    as 1 instead of 2 does not blank a cell — it slides every later cell in that
    row one column left. A results row reading ``Mean=42, SD=7.1`` renders as
    ``n=42, Mean=7.1, SD=''``: wrong numbers under the right headings, with no
    visual tell. The caller counts these so :func:`_audit_parse` can report them
    once per article at WARNING.

    ``rowspan`` needs no companion — this module never reads it.

    Args:
        attrs: The cell element's attributes.

    Returns:
        ``(span, rejected)``. ``rejected`` is ``None`` where the declaration was
        honoured, and otherwise the raw value, for the caller to count. An
        absent or empty ``colspan`` is neither honoured nor rejected: it is a
        missing value, not a malformed one, so it yields ``(1, None)``.
    """
    raw = attrs.get("colspan", "1") or "1"
    try:
        span = int(raw)
    except ValueError:
        logger.debug("Unparseable colspan=%r; treating the cell as one column", raw)
        return 1, raw
    if span > _MAX_COLSPAN:
        logger.debug(
            "colspan=%r exceeds the %d-column bound; treating the cell as one column",
            raw,
            _MAX_COLSPAN,
        )
        return 1, raw
    return span, None


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
        # The two undivided spellings of a contributor's name. Accumulating so
        # that the close reads its own text rather than whatever the ancestor's
        # buffer happened to hold, and *inline* (below) so that text goes back
        # to the parent where the name is not a contributor's. See
        # `_UNDIVIDED_NAME_ELEMENTS` for why "where the name is not a
        # contributor's" is a condition rather than a blanket merge.
        #
        # THE REASON THEY WERE MADE INLINE IS NOW DELIVERED ELSEWHERE, and the
        # membership is kept knowing that. #120/#140 added them here so that a
        # <mixed-citation> printing either keeps the name in the citation
        # string it renders; #146's `_inside_mixed_citation()` merges every
        # descendant of a citation regardless of membership, which subsumes
        # that case entirely. Measured: on the commit before #146, deleting
        # both entries fails two tests in `TestAnUndividedContributorName`;
        # after it, the identical deletion passes the whole suite and changes
        # the rendered HTML of none of 880 local PMC articles. What the
        # membership still stands for is a name printed somewhere *other* than
        # a citation — body prose, a section title — where nothing else merges
        # it back and the name would be deleted from the surrounding text. That
        # population is **unmeasured**: JATS's parent lists for these two
        # elements are contributor and citation contexts, so the prose case may
        # not be a shape publishers deposit at all.
        # `TestAnUndividedNameInProseStaysInTheProse` pins the rule rather than
        # the population, so the entries cannot go quietly vacuous again.
        "collab",
        "string-name",
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
# inside those journals is far above the general one.
#
# HOW OFTEN AN ARTICLE **CARRIES** A REGION, which is the only population
# re-derivable from this repo and is the *bound* on how often one loses
# content to it (#158): **29 of 997 recent committed-corpus articles, 2.9%
# [2.0-4.1], 145 regions in all**, and 0 of 997 in the back-filled window
# (`scripts/sample_jats_exhibits.py`). `bmlib.transparency` counts the same
# population over the same PMC `oa_comm` baseline package PMC012xxxxxx at
# 3,382 of 97,909 (3.45%) — a far larger draw whose interval overlaps this
# one — but **the two read different renditions**, transparency the archive
# bytes and this the `fullTextXML` the parser is fed, and the renditions do
# not agree here: `tests/data/jats_exhibits.rendition.json` records Europe
# PMC *adding* regions in 5 of 300 articles (27 archive against 32 served).
# The added element is the injected `associated-data` block named below —
# **spot-checked live in three of those five, not read off the artifact**,
# which records counts alone and no `article-type`: each of the three gains
# exactly one `<sub-article article-type="associated-data">` between the
# archive copy and the served one. So the two corroborate each other across a
# known difference, which is worth stating rather than calling them one
# source.
#
# Two older figures are cited elsewhere and are **not** of this population:
# 4 of 249 (1.6%) counted peer-review deposits specifically, and 288 of 1,022
# (28.2%) counted articles that *lose body text*, on a draw that is in no
# commit. An article can only lose content to a region it **carries**, so on
# one draw the losing count cannot exceed the carrying count — a bound
# against the carrier figure only, never against the peer-review one, since
# a translation <sub-article> costs an article its prose while depositing no
# review round at all. That the 28.2% exceeds the 3.45% is a fact about two
# unrelated samples, not a contradiction to reconcile arithmetically.
#
# Peer review is not the only use: <sub-article> also carries the
# alternative-language full text (SciELO's article-type="translation"),
# meeting abstracts, and Europe PMC's own injected "associated-data" block,
# which is absent from PMC's copy of the same record. Which is why the
# suppression is structural:
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
# THIS ONE IS NOT DEFENSIVE, AND THE FIGURE MOVED TWICE. The vanished
# 276-article draw found exactly one <graphic> owned by a non-exhibit inside
# an exhibit, which read as a population of one. The two committed draws find
# **153, in 12 of 997 recent articles** — and 0 of 997 back-filled — spread
# over three owners, none of which this comment could have enumerated in
# advance: <td> 82 (8 articles), <inline-formula> 69 (3),
# <disp-formula> 2 (1). The cell images are what make it
# consequential rather than merely more numerous: since #127 gave
# JATSTableInfo a `graphic_url`, relaxing ownership lands a <td>'s
# decoration in it as though it were the table's own rendition, and the
# strictly-better rule then makes that permanent. So this rule is measured as
# load-bearing on 153 deposits, not carried against a hypothetical — and the
# owner spread is the argument for keeping the *listed* side short and
# everything else opaque, rather than trying to enumerate the owners: the
# previous draw of this same window found <chem-struct> and <th> too, which
# this one does not, so the set of owners is drawn from rather than fixed.
#
# The rest is still what the archival tiers are: what it prevents is silent. A
# nested <table-wrap>/<fn>/<supplementary-material> inside a <fig> hands over
# its image, and the strictly-better rule makes that permanent where "keep the
# last" used to overwrite it. The <p> member is not defensive at all — JATS
# admits <p> inside <fig>, and without it a figure whose graphic is wrapped in
# prose flow loses its image outright.
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
        "collab",
        "string-name",
    }
)

# The two formula elements, whose text is emitted by their own arm and never
# by the buffer pop (issue #147).
#
# Both used to lose content, in the two ways a text-accumulating element can.
# <inline-formula> is inline, so its buffer merged into the sentence — but
# <tex-math> beneath it accumulates and is *not* inline, so the buffer it
# merged was empty and the sentence rendered with a hole in it.
# <disp-formula> accumulates with no handler at all, so a display equation was
# popped and discarded whole: its LaTeX, its MathML, and the "(1)" that body
# prose goes on to cross-reference.
#
# The rule is to CHOOSE ONE RENDITION, AT THE FORMULA ELEMENT. It cannot be
# expressed by adding <tex-math> to _INLINE_ELEMENTS, and the reason is
# measured rather than hypothetical: 1,087 formulas in the committed recent
# corpus, and 188,473 across the PMC012xxxxxx baseline package, carry a LaTeX
# *and* a MathML encoding of the same expression, so a rule that merged every
# child would print each of them twice.
_FORMULA_ELEMENTS = frozenset({"inline-formula", "disp-formula"})

# The elements whose text a formula's own arm delivers, so the pop must never
# merge them. <tex-math> is here because its text is rendered before it is
# merged — a raw merge is worse than the drop it replaces, since 99.9% of
# 4,422 sampled deposits are a whole LaTeX document, preamble and all — and
# the formula elements are here because their arm decides where their one
# rendition goes. Kept as a set beside _INLINE_ELEMENTS rather than removing
# <inline-formula> from it: that membership still states the true thing, that
# an inline formula's text belongs to the prose around it. What changed is who
# delivers it.
_FORMULA_PARTS = _FORMULA_ELEMENTS | {"tex-math"}

# The two elements whose content is a table cell.
#
# A cell is a slot, not a sentence, and that difference decides whether a
# merged display formula prints its equation number. `_render_formula` argues
# at length that a number merged into prose reads as a coefficient — but a cell
# has no surrounding sentence for it to weld into, and the number there is the
# column's own datum: measured over the 40 labelled display formulas that sit
# in a cell (8 of the package's 97,909 articles), *every one* is a cell whose
# entire content is the number and the equation. PMC12164272's Table 2 is a
# reaction-number column — `<td>1 S1CV2+ + O32- → …` for rows 1-9, each number
# cross-referenced from the body prose — and PMC12120668's tables 4, 6, 7 and 8
# carry 18 equation numbers the same way. Withholding it there was a
# regression: characters() used to deliver the label to the cell, so this
# change cost the column its identity while removing the LaTeX preamble beside
# it. Kept as a named set because _DISPLAY_FORMULA_MERGE_PARENTS reads it too,
# and two spellings of "a cell" are two things to keep in step.
_TABLE_CELL_ELEMENTS = frozenset({"td", "th"})

# Parents a <disp-formula> merges into rather than standing beside as its own
# paragraph.
#
# 116,623 of the package's 150,598 display formulas (77.4%) sit inside a <p>,
# and 201 of the 880-article served draw's 654. Emitted as its own paragraph,
# every one of those would be appended *ahead* of the paragraph it interrupts,
# because the enclosing <p> has not closed yet. The rest are block children —
# 33,270 directly in a <sec>, and <app>, <boxed-text>, <disp-formula-group>,
# <body> and <disp-quote> beyond that — where there is no open prose to join
# and a paragraph of its own is the only way the equation reaches the article.
#
# AN ALLOW-LIST, AND IT FAILS TOWARD THE PARAGRAPH *IN FLOWING PROSE ONLY*. A
# <sec> accumulates a buffer like a <p> does, but nothing ever reads it, so
# merging into an unlisted parent is a silent loss where emitting a paragraph
# is at worst an ordering surprise. The listed members are the ones this module
# actually reads back: <p> and the two cell elements, whose text reaches the
# rendered table, plus _INLINE_ELEMENTS, each of which merges onward into one
# of them.
#
# THE FAILURE DIRECTION REVERSES INSIDE A FLOAT, and the first cut of this
# comment did not say so. `_append_prose` tests `in_figure`/`in_table_wrap`
# before every prose branch, so a formula the allow-list sends to the paragraph
# path from inside a <fig> or <table-wrap> reaches `_append_caption_text`, which
# drops it when no <caption> is open — while `characters()` has already withheld
# it from the cell. A <disp-formula> under a <disp-formula-group> or a
# <boxed-text> in a <td>, all legal cell content, is then lost outright rather
# than misplaced. The population measures 0 in both corpora (all 385
# disp-formula-in-cell in the package are direct <td>/<th>/<p> children), so it
# is latent — but `formulas_dropped` counts it and `_audit_parse` reports it,
# because a silent loss with no counter is the failure this module keeps being
# caught by, and a comment asserting the wrong direction is how it stays silent.
_DISPLAY_FORMULA_MERGE_PARENTS = _INLINE_ELEMENTS | {"p"} | _TABLE_CELL_ELEMENTS


# The two spellings that give a contributor's name as one undivided string.
#
# Both are inline, so their text goes back to the parent — which is what keeps
# a name a `<mixed-citation>` prints inline inside the citation string it
# renders, and a name printed in body prose inside that paragraph. Inside a
# `<contrib>` the merge is destructive instead: the nearest accumulating
# ancestor of a roster member is the enclosing `<collab>`, so the member's name
# was appended to the consortium's own — *"The INHERIT Trial GroupJane Q
# SmithAhmed Al-Rashid"*, silently, in the very shape #120 exists to collect.
#
# So the merge is refused while any `<contrib>` is open. That is the module's
# owner test in its usual form: the `<contrib>` owns the name, and no enclosing
# buffer has a claim on it. A depth would do as well as a stack here, but the
# stack is already kept and reading it costs nothing.
_UNDIVIDED_NAME_ELEMENTS = frozenset({"collab", "string-name"})


# ---------------------------------------------------------------------------
# SAX Handler
# ---------------------------------------------------------------------------


class _JATSHandler(xml.sax.handler.ContentHandler):
    """SAX content handler that mirrors the Swift XMLParserDelegate logic."""

    def __init__(self, known_pmc_id: str = "") -> None:
        super().__init__()

        # Parsed content
        self.title = ""
        # One entry per <contrib> collected as an author, reserved when the
        # element opened and filled when it closed; `build_authors()` renders
        # them. See `_ContribFrame` for why the reservation is what keeps a
        # collaboration ahead of its own member roster.
        self.author_slots: list[JATSAuthorInfo | None] = []
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
        # One entry per open <contrib>, innermost last, holding the builder it
        # is being read into — or None where `_is_author_contrib` said this
        # contributor is not one of the article's authors. The None entries are
        # what keep the pushes and pops paired, so an editor nested inside an
        # author's <collab> roster cannot pop the author's own frame.
        #
        # `in_contrib` and `current_author` are *derived* from this stack
        # rather than stored beside it: a stored flag cleared by the inner
        # close is exactly what #115 was, one element family over.
        self.contrib_stack: list[_ContribFrame | None] = []
        # Contributor names seen anywhere inside <front>, whether or not the
        # contributor carrying one was collected. What tells a genuinely
        # author-less article from a parse that looked in the wrong place
        # (issue #121), and gated on `in_front` — a structural fact — rather
        # than on `in_contrib`, which is set only once `_is_author_contrib`
        # has said yes. Keyed on that, the counter would go to zero in
        # exactly the situation it exists to detect: #111 dropped every
        # author from 57% of open-access articles by answering that question
        # wrongly, and a counter sharing the answer would have reported every
        # one of them as author-less.
        #
        # `<back>` is excluded for the opposite reason — a bibliography is
        # full of surnames and none of them is a contributor, so counted
        # document-wide every author-less article with references would read
        # as a defect. A suppressed <sub-article>'s <front> never sets the
        # flag, so nested contributors are excluded for free.
        #
        # **All three JATS spellings count, not just <surname>.** A <contrib>
        # names its contributor with `(name | string-name | collab | …)`, and
        # bmlib reads only <name>. Counting surnames alone, a <contrib-group>
        # built from <string-name> (#140, and 100% of the authors lost) or
        # from <collab> (#120, some of them) reached the quiet branch and was
        # reported as *genuinely* author-less — a positive claim the evidence
        # never supported, and exactly the silence #121 exists to end.
        # Counting is not parsing: extracting either spelling is its own
        # issue, but the detector must not certify an article as author-less
        # because it looked for one spelling of a name and found none.
        self.front_contributor_name_count = 0
        # Cells whose declared `colspan` this module refused to honour, counted
        # so `_audit_parse` can report them once per article rather than once
        # per cell. Not a cosmetic tally: a refused span slides every later cell
        # in its row one column left, so the table renders wrong numbers under
        # the right headings — see `_read_span`.
        self.rejected_spans = 0
        # <contrib> elements collected as an author from which no name could be
        # read, counted so `_audit_parse` reports them once per article at
        # WARNING — the level and the granularity `rejected_spans` above
        # settled for the same reasons (issue #129). A per-<contrib> DEBUG line
        # was both too quiet to be the answer to #120's other half and, on an
        # author list of 200 <xref>-only contribs, 200 identical lines; and
        # emitted from `endElement` it named an article whose <article-id> had
        # not been read yet.
        self.contribs_naming_nobody = 0
        # Display formulas this parser rendered and then had nowhere to file,
        # counted so `_audit_parse` reports them once per article at WARNING
        # for the two reasons above. `_append_prose` has four branches and no
        # fallthrough, so a standalone <disp-formula> in <back> with no <sec>
        # on the stack — 192 in 23 of the PMC012xxxxxx package's 97,909
        # articles — is built and dropped, as is one the merge allow-list
        # sends to the paragraph path from inside a float with no <caption>
        # open. Neither is a regression: `main` discarded the whole element.
        # That is exactly why it is counted rather than left — the parser now
        # builds the string, so losing it silently is a new kind of quiet.
        # Routing them is issue #177.
        self.formulas_dropped = 0
        self.current_article_id_type: str | None = None

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
        # 19.6% of articles; a later 276-article draw re-measured it at 0.7%
        # (2 articles, both eLife, losing 6 of 12 and 5 of 11 figures).
        # **Neither draw is in the repo, and the committed corpora put the
        # rate lower still**: 7 nested <fig> and 0 nested <table-wrap> across
        # 1,994 articles, all seven in **one** article — eLife's PMC12143881,
        # 7 of its 19 figures (`scripts/sample_jats_exhibits.py`, issue #138).
        # So one article in 1,994 is the whole population, and it is the
        # publisher the shape was always attributed to: a house style costing
        # about a third of *its* figures, not a general convention. eLife's
        # PMC8754430, where the issue came from, is the same shape outside any
        # committed corpus. Read the 1-in-1,994 as a property of which
        # publishers a draw happens to catch, never as a rate. And
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
        # </caption> would clear it, so a <media> legend inside a figure's
        # caption would truncate that caption at the point the legend ended
        # and drop every word after it.
        #
        # The **owner** because a depth counter only fixes that half. The
        # legend's owner is not an exhibit bmlib models, so counted rather
        # than named it would still land on the enclosing figure — and the
        # case a depth cannot reach at all needs no nesting, since JATS admits
        # a <caption> on <boxed-text>, <media> and <supplementary-material>,
        # any of which may sit inside a <fig> beside the figure's own.
        #
        # Both paragraphs are in the subjunctive on purpose: they describe
        # what the retired boolean would do, not what a draw caught it doing.
        #
        # NEITHER POPULATION MEASURES EMPTY ANY LONGER, AND THIS SAYS SO.
        # Over the two committed draws (1,994 articles,
        # `scripts/sample_jats_exhibits.py`, issue #138): **6 <caption> of
        # 8,111 recent nest inside another**, and **6 <caption> inside an
        # exhibit are owned by a <supplementary-material>** rather than by the
        # exhibit enclosing them. Both counts are one article — eLife's
        # PMC12143881, which also carries every nested <fig> in the window —
        # so this is a per-publisher deposit property in the way #115's
        # nesting is, not a general rate. It is also the exact shape earlier
        # drafts of this comment *asserted* and the two previous draws could
        # not find: a figure supplement deposited as a captioned
        # <supplementary-material> inside its <fig>. The stack and the owner
        # test are what keep those six legends off the enclosing figure.
        # The back-filled window contributes nothing either way: it holds
        # **0 <caption>** — inferred, not counted, to be scanned page
        # images — so its
        # zeroes are an absent denominator and not a second measurement. The
        # seven-article corpus in the sibling Swift repository, eLife's
        # PMC8754430 included, deposits its figure supplements as nested <fig>
        # instead, so one publisher uses both shapes.
        #
        # THE PREMISE IT RESTS ON MEASURES FULL, which is the half that could
        # have lost content: 6,938 / 6,938 recent exhibits carry a direct-child
        # <caption> and carry one anywhere, so no exhibit is captioned only
        # indirectly and the parent can never come up empty where the old rule
        # found something. Unlike the <label> premise one handler down, which
        # the same redraw broke — so this is a measured result and not a
        # symmetry that can be assumed.
        #
        # Naming the owner is also what retired `_innermost_exhibit()`: a
        # <caption> is a direct child of what it describes, so its parent
        # answers exactly, where "the innermost exhibit open anywhere above"
        # was merely usually right.
        self.caption_stack: list[_FigureBuilder | _TableBuilder | None] = []

        # Formula state. A stack because formulas nest, and holding only what
        # the encoding *choice* needs: see `_FormulaFrame` (issue #147).
        self.formula_stack: list[_FormulaFrame] = []

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

    def _cell_span(self, attrs: xml.sax.xmlreader.AttributesImpl) -> int:
        """Read a cell's ``colspan``, counting one this module would not honour.

        The counting half of :func:`_read_span`, kept here so the predicate
        stays a pure function and both cell branches share one line.
        """
        span, rejected = _read_span(attrs)
        if rejected is not None:
            self.rejected_spans += 1
        return span

    # -- End-of-parse audit --------------------------------------------------

    #: Routing state that is a bare flag or a single slot rather than a stack.
    #: Reported as one grouped diagnostic — they all fail the same way, and an
    #: operator reads them as a set. **Add a flag to the handler, add it
    #: here**: an incomplete net is what lets the next one hide.
    #:
    #: Two fields are deliberately absent, and for one reason: `</abstract>`
    #: flushes without clearing, and only a *subsequent* `<abstract>` open
    #: clears (the suppressed open is the one that does *not*, returning above
    #: the clear). So `current_abstract_text` **and `current_abstract_title`**
    #: are both non-empty at the end of every article carrying a titled
    #: abstract, and auditing either would fire on almost every real document.
    #: Both are named here because the rule above says "add a flag, add it
    #: here", and a maintainer following it would otherwise add the second.
    #: `tests/test_jats_parser.py`'s `parser_log` fixture is what caught the
    #: first, and is why every fixture in that module is a false-positive
    #: check; `test_the_audit_covers_every_routing_flag` is what stops the net
    #: silently acquiring a third omission.
    _ROUTING_FLAGS: ClassVar[tuple[str, ...]] = (
        "in_front",
        "in_article_meta",
        "in_abstract",
        "in_body",
        "in_back",
        "in_ref_list",
        "in_ref",
        "in_ref_citation",
        "in_ref_person_group",
        "current_reference",
        "current_article_id_type",
        "current_xref_type",
        "current_xref_rid",
        # Single-slot: unsectioned `<body>` prose accumulates here and is
        # flushed at `</body>`. Left stranded, the article loses that prose
        # outright and `has_body` stays True, because `body_paragraph_count`
        # already counted it — a silent loss of a whole body in the shape this
        # audit exists to catch. Covered today only because `in_body` is
        # cleared on the adjacent line, which is an accident of layout rather
        # than anything asserted.
        "implicit_body_section",
    )

    def unwind_state(self) -> ParseUnwindState:
        """Snapshot the routing state this parse ended with.

        The handler-coupled half of the audit; :mod:`bmlib.fulltext._parse_audit`
        holds the pure half that reads the snapshot. Split that way so the
        predicates can be handed the residue a defect would leave without
        having to build a handler and reach into every one of its stacks and
        flags.

        ``excess_text_buffers`` subtracts the one buffer ``text_stack`` always
        holds, so a clean parse maps to the struct's defaults exactly.

        Returns:
            The state, all-default where the parse unwound cleanly.
        """
        return ParseUnwindState(
            nested_article_depth=self.nested_article_depth,
            open_sections=len(self.section_stack),
            open_figures=len(self.figure_stack),
            open_tables=len(self.table_stack),
            open_captions=len(self.caption_stack),
            open_formulas=len(self.formula_stack),
            open_contrib_groups=len(self.contrib_group_stack),
            open_contribs=len(self.contrib_stack),
            unfilled_author_slots=sum(slot is None for slot in self.author_slots),
            unfilled_figure_slots=sum(slot is None for slot in self.figure_slots),
            unfilled_table_slots=sum(slot is None for slot in self.table_slots),
            excess_text_buffers=max(0, len(self.text_stack) - 1),
            open_elements=tuple(self.element_stack),
            stuck_flags=tuple(name for name in self._ROUTING_FLAGS if getattr(self, name)),
        )

    def describe_article(self) -> str:
        """Name the article this parse was of, for a diagnostic line.

        An ERROR carrying no identity is unactionable in a bulk sync, where
        the parse that produced it is one of thousands. Falls back through the
        identifiers in the order a reader can act on them, then to the title,
        and finally — for a document carrying neither, which is the parse most
        likely to be broken — to a fixed string saying so, since a line naming
        nothing is still better than no line.
        """
        for identifier in (self.pmc_id, self.doi, self.pmid):
            if identifier:
                return identifier
        if self.title:
            return f"'{self.title[:60]}'"
        return "an article carrying no identifier or title"

    @property
    def current_author(self) -> _AuthorBuilder | None:
        """The builder for the innermost ``<contrib>`` being collected, if any.

        Derived from ``contrib_stack`` rather than stored, so the inner close
        of a nested ``<contrib>`` restores the enclosing one instead of
        clearing it. ``None`` while the innermost open ``<contrib>`` is not an
        author's, which is what stops an editor listed inside a collaboration's
        roster from writing into the collaboration's builder.
        """
        frame = self.contrib_stack[-1] if self.contrib_stack else None
        return frame.builder if frame is not None else None

    @property
    def in_contrib(self) -> bool:
        """Is the *innermost* open ``<contrib>`` one bmlib collects as an author?

        False while a non-author ``<contrib>`` is nested inside an author's —
        an editor listed in a collaboration's roster — which is what routes
        that editor's ``<surname>`` away from the consortium enclosing them.
        Not "is any author ``<contrib>`` open", which is a different question
        and not the one any call site asks.
        """
        return self.current_author is not None

    def build_authors(self) -> list[JATSAuthorInfo]:
        """Build the authors, in the order their ``<contrib>`` elements *opened*.

        A method rather than an attribute for the reason
        :meth:`build_figures` is one. The filter drops a slot reserved by a
        ``<contrib>`` that never closed, which ``xml.sax`` cannot deliver; it
        only keeps the reservation from being able to put a hole in the result,
        and ``unfilled_author_slots`` reports it if it ever happens.
        """
        return [author for author in self.author_slots if author is not None]

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

    def _inside_mixed_citation(self) -> bool:
        """Is the element now closing a *descendant* of a ``<mixed-citation>``?

        ``<mixed-citation>`` is JATS's mixed-content citation: the publisher
        deposits the reference as they typeset it, with their own punctuation
        between the marked-up parts. So every descendant's text is *also* the
        citation's, and a child that took a buffer without merging it back
        deleted itself from the string — ``<person-group>``,
        ``<article-title>``, ``<source>``, ``<year>``, ``<volume>``,
        ``<issue>``, ``<fpage>``, ``<lpage>`` and ``<pub-id>`` all do, which is
        the whole of a standard NLM deposit, so it rendered as
        ``'. . . ;():-. doi: .'`` (issue #146).

        **The rule is a property of the context, not of the element**, which is
        why membership of ``_INLINE_ELEMENTS`` — the instrument #120 and #140
        reached for, correctly, because ``<collab>`` and ``<string-name>``
        carry a name wherever they appear — cannot serve here. These elements
        carry text that must *not* merge outside a citation: an
        ``<article-title>`` in ``<article-meta>`` is the article's own title
        and would be appended to whatever buffer is open, and a ``<source>`` or
        ``<year>`` there is a metadata field, not prose.

        It is an *ancestor* test and not the parent test the module usually
        makes (``<label>``, ``<caption>``, ``<article-id>``), because mixed
        content is inherited down the whole subtree: a ``<surname>`` sits
        inside ``<name>`` inside ``<person-group>``, and each merge composes
        into the one above it. ``<graphic>`` is deliberately *not* in that
        list: ``_graphic_owner`` walks up past the transparent wrappers, so it
        is neither a parent test nor this one, and citing it here put the
        contrast's own counter-example on the wrong side of it.

        The slice is what excludes the ``<mixed-citation>`` element itself,
        whose own close *reads* the buffer rather than merging it — and that
        half is **prospective, so do not read it as load-bearing**. Merging it
        too would push the whole citation into the enclosing buffer, but at
        this revision nothing reads that buffer: no handler in ``endElement``
        takes ``text`` for an element outside ``_TEXT_ACCUMULATING``, so the
        base buffer is written and never consulted. Dropping the slice
        survives the full suite, and three document shapes — a ``<ref-list>``
        in ``<back>`` followed by a ``<floats-group>``, a ``<ref-list>`` inside
        a body ``<sec>`` whose buffer *is* open, and two refs followed by an
        ``<fn-group>`` — parse identically with and without it. It is kept
        because a buffer that escapes is the shape this module has been caught
        by repeatedly, and because the alternative asserts that the citation's
        text belongs to an ancestor that has no claim on it.

        ``<element-citation>`` is deliberately *not* included. Its content
        model is element-only, so whitespace between children is insignificant
        and there is no authored string to recover — concatenating gives either
        a run-together word or the depositor's indentation as a separator.
        Assembling a reference from the structured fields is a citation-style
        decision, and :attr:`JATSReferenceInfo.formatted_citation` is where
        this library makes it.

        **Excluding it here was necessary and not sufficient**, and the review
        of #146 is what established the difference. Suppressing the merge stops
        an accumulating child donating its text, but a child this module does
        *not* accumulate never took a buffer in the first place: its characters
        go straight to whatever is open, which inside an ``<element-citation>``
        is the citation's own buffer. A routine book deposit carrying
        ``<edition>``, ``<publisher-loc>`` and ``<publisher-name>`` therefore
        produced ``'3rd edAmsterdamElsevier'`` — precisely the run-together
        word this paragraph gives as the reason for the exclusion, and the
        opposite of the empty string it was documented to leave. So the close
        arm writes :attr:`~JATSReferenceInfo.citation` for ``<mixed-citation>``
        only; see the comment there.

        The prospective half above is **mechanised**, not left to this
        paragraph: ``TestOnlyAnAccumulatingElementReadsTheBuffer`` walks every
        arm of :meth:`endElement` and fails on one that reads the buffer for
        an element outside ``_TEXT_ACCUMULATING`` (issue #151). The rule was
        true when it was written and nothing tied the two together, which is
        the ``TestTheAuditNetIsComplete`` situation one module over.

        Read *"reads the buffer"* as this paragraph means it — consults it at
        all — and not as three local names. The walk is keyed on
        ``self.current_text`` and ``self._pop_text_buffer()`` as well as on
        ``text``/``normalized_text``/``element_text``, because for a
        non-accumulating element ``element_text = self.current_text`` makes the
        first two the same value; keyed on the locals alone it passed an arm
        reading ``self.current_text`` for ``<institution>``, which is #142 in
        the spelling an implementer is as likely to write.

        Returns:
            ``True`` when a ``<mixed-citation>`` is open strictly above the
            element being closed.
        """
        # A *strict*-ancestor slice only because `element_stack.pop()` sits at
        # the very end of `endElement`: the element now closing is still on
        # the stack, so `[:-1]` drops it and leaves its ancestors. Move that
        # pop above the `_pop_text_buffer()` call at the top of the method —
        # which is where this predicate is evaluated, so nothing short of that
        # reaches it — and the slice silently becomes "excludes the parent",
        # reading a `<mixed-citation>`'s own children as outside it. Pinned by
        # seven tests across two classes — three in
        # `TestAMixedCitationKeepsTheTextItPrints` (of its six) and four in
        # `TestARefCarryingSeveralCitationsKeepsThemAll` — all of which stay
        # green for a pop moved anywhere below that call. The set is the
        # measured difference between the two placements below, not a reading
        # of the test names: the second class holds the majority of the guard
        # and an earlier draft of this comment omitted it, which would have
        # told a maintainer rewriting #149's tests that nothing was at stake.
        # See the comment at the pop itself for what else moves with it.
        return "mixed-citation" in self.element_stack[:-1]

    # -- Section and caption helpers -----------------------------------------

    def _caption_owner(self, parent: str) -> _FigureBuilder | _TableBuilder | None:
        """The builder a ``<caption>`` just opened under ``parent`` belongs to.

        ``<caption>`` is a direct child of the element it describes, so the
        parent decides outright — the ``<label>`` idiom, one element away. It
        is exact where "the innermost exhibit open anywhere above" was only
        usually right: ``<boxed-text>``, ``<media>``, ``<supplementary-material>``
        and ``<fig-group>`` all admit a ``<caption>`` too, and inside a
        ``<fig>`` each of them would donate its legend to the figure. No draw
        has found one doing so — see ``caption_stack``, which records that
        population as empty rather than claiming it.

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

    def _parent_element(self) -> str:
        """The element enclosing the one currently closing.

        ``element_stack[-1]`` is the closing element itself — ``pop()`` sits at
        the end of ``endElement`` — so ``[-2]`` is its parent. One spelling of
        the module's most heavily argued rule, which had grown five identical
        copies (``<caption>``, ``<title>``, ``<article-id>``, ``<label>`` and
        issue #147's ``<disp-formula>``): the routing decisions those arms make
        differ, but *"which element owns this one"* is a single question and
        two copies of it are two things to keep in step.

        Returns:
            The parent element's name, or ``""`` at the document root.
        """
        return self.element_stack[-2] if len(self.element_stack) >= 2 else ""

    def _prose_reaches_output(self) -> bool:
        """Whether :meth:`_append_prose` would file this text anywhere.

        The branches below mirror that method's, and are a *predicate* rather
        than a second copy of the routing: it answers where the text goes,
        this answers only whether anywhere. Kept beside it so the two are read
        together — the failure it exists to detect is a branch added to one
        and not the other, which would report a loss that did not happen or,
        worse, stay quiet about one that did.

        Returns:
            ``True`` if the text would be kept.
        """
        if self.in_figure or self.in_table_wrap:
            # `_append_caption_text` keeps text only for an open <caption>
            # whose owner this module models.
            return bool(self.caption_stack) and self.caption_stack[-1] is not None
        if self.in_abstract:
            return True
        if (self.in_body or self.in_back) and self.section_stack:
            return True
        return self.in_body

    def _append_prose(self, text: str, *, keep_empty: bool) -> None:
        """Route one run of prose to whatever the parse currently has open.

        Extracted from the ``<p>`` arm when ``<disp-formula>`` gained one
        (issue #147): a display equation standing between two paragraphs *is*
        a paragraph of the section, and the routing it needs — caption before
        section, abstract before body, sectioned before unsectioned — is the
        same routing, argued in the same order and for the same reasons. Two
        copies of it would be two things to keep in step, which is the shape
        this module keeps being caught by.

        The ``<p>`` caller asks for ``keep_empty`` because an empty paragraph
        inside a section is still a paragraph the document deposited, and
        several tests pin the resulting empty string. A formula holding
        nothing is not: it renders as ``""`` and must add no paragraph at all,
        for the reason ``to_html`` invents no number for an unlabelled exhibit
        (issue #162).

        Args:
            text: The prose, already whitespace-normalised.
            keep_empty: Whether an empty ``text`` still appends inside a
                section. Never opens an implicit body section either way.
        """
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
            self._append_caption_text(text)
        elif self.in_abstract:
            if text:
                self.current_abstract_text.append(text)
        elif (self.in_body or self.in_back) and self.section_stack:
            if not text and not keep_empty:
                return
            if self.in_body and text:
                self.body_paragraph_count += 1
            self.section_stack[-1].paragraphs.append(text)
        elif self.in_body and text:
            # An unsectioned <body> child. Empty paragraphs are dropped
            # rather than opening a section, so a <body> holding nothing
            # but whitespace stays body-less.
            if self.implicit_body_section is None:
                self.implicit_body_section = _SectionBuilder()
            self.body_paragraph_count += 1
            self.implicit_body_section.paragraphs.append(text)

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

        Dropped *where this is reached at all*, which is not everywhere a
        <caption> is. The ``<p>`` caller sits behind ``in_figure or
        in_table_wrap``, so a <caption> at section level — issue #130's own
        ``<boxed-text>`` shape — never enters it: only its ``<title>`` is
        dropped, while its ``<p>`` children still fall through to the
        section's prose. Better than before, which took the heading too, but
        the two halves of one caption now go different ways. Issue #137.

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
            # A frame either way: a non-author <contrib> pushes None so that
            # its own close pops its own entry rather than the enclosing
            # author's. Reserving the slot here is what lists a collaboration
            # ahead of the members its <collab> encloses.
            if self._is_author_contrib(attrs.get("contrib-type")):
                self.author_slots.append(None)
                self.contrib_stack.append(
                    _ContribFrame(slot=len(self.author_slots) - 1, builder=_AuthorBuilder())
                )
            else:
                self.contrib_stack.append(None)
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
        elif name in _FORMULA_ELEMENTS:
            # A frame per formula, and a stack because formulas nest — see
            # `_FormulaFrame`. What it collects is decided at the close, which
            # is what makes the encoding choice independent of the order the
            # encodings were deposited in (issue #147).
            self.formula_stack.append(_FormulaFrame(display=name == "disp-formula"))
        elif name == "caption":
            # `element_stack[-1]` is this <caption>, as at <article-id> above.
            parent = self._parent_element()
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
            # committed corpus (13,617 deposits, every extension unpadded) —
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
                current_table.start_cell(is_header=True, colspan=self._cell_span(attrs))
        elif name == "td":
            current_table = self.current_table
            if current_table is not None:
                current_table.start_cell(is_header=False, colspan=self._cell_span(attrs))
        elif name == "ref-list":
            self.in_ref_list = True
        elif name == "ref":
            self.in_ref = True
            self.current_reference = _ReferenceBuilder(id=attrs.get("id", ""))
        elif name in ("mixed-citation", "element-citation"):
            if self.in_ref and self.current_reference:
                # Only the FIRST citation element of a <ref> fills the
                # structured fields. A <ref> may carry several — 216 references
                # in 21 of 880 local PMC articles do — and every field arm is
                # gated on `in_ref_citation`, so leaving it False for the rest
                # is the whole of first-wins: scalars stop being last-wins and
                # `authors` stops *accumulating*, which was welding a byline
                # out of several different works (issue #149 — one reference
                # reported 40 authors, and rendered two people from two
                # different papers as though they were one paper's). The
                # deposit is not lost: every part's text still reaches
                # `citation_parts` at the close, which is gated on `in_ref`.
                self.current_reference.citation_element_count += 1
                if self.current_reference.citation_element_count == 1:
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
        if self.formula_stack:
            # A cell collects its text here rather than from a buffer, so a
            # formula inside one has to be held back the same way it is held
            # back from prose: its arm appends the one rendition it chose.
            # Without this the LaTeX reaches the rendered table raw, preamble
            # and all — 24,476 <tex-math> in 856 of the PMC012xxxxxx package's
            # 97,909 articles sit inside a <td> or a <th>, and every one of
            # them pasted some 300 characters of \usepackage lines into the
            # cell, in HTML `FullTextService` then caches (issue #147).
            return
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
            # An undivided name inside a <contrib> belongs to that contributor
            # and is not merged back; see `_UNDIVIDED_NAME_ELEMENTS`.
            is_owned_name = name in _UNDIVIDED_NAME_ELEMENTS and bool(self.contrib_stack)
            # A formula and its LaTeX are emitted by the formula arm, which
            # renders one chosen encoding — so neither may merge here, inside
            # a <mixed-citation> included. Merging <tex-math> would put a
            # whole LaTeX document into the prose, and merging both encodings
            # of one expression would print it twice (issue #147).
            is_formula_part = name in _FORMULA_PARTS
            element_text = self._pop_text_buffer(
                merge_with_parent=(is_inline or self._inside_mixed_citation())
                and not is_fig_table_xref
                and not is_owned_name
                and not is_formula_part
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
            # Guarded because a close with nothing open would otherwise raise
            # on malformed input; SAX makes that unreachable today, the way it
            # does for every other stack here. Unlike the <contrib-group> guard
            # above, the audit does *not* cover this one's false branch: the
            # slot is reserved by the same handler that pushes the frame, so no
            # frame means no slot and `unfilled_author_slots` has nothing to
            # count. Were it ever reachable, a built contributor would go
            # missing silently.
            if self.contrib_stack:
                contrib_frame = self.contrib_stack.pop()
                if contrib_frame is not None:
                    author = contrib_frame.builder.build()
                    if author is not None:
                        self.author_slots[contrib_frame.slot] = author
                    else:
                        # Give the reservation back, so an unfilled slot keeps
                        # meaning "a <contrib> that never closed" and the audit
                        # can report it as the defect it would be. A <contrib>
                        # naming nobody — `<anonymous/>`, or one carrying only
                        # an <xref> — is well-formed JATS, so a slot left
                        # standing here would make the audit ERROR on a
                        # document bmlib had read correctly.
                        #
                        # Safe because the stack is LIFO: every frame with a
                        # higher slot index opened inside this <contrib> and
                        # has therefore already been popped and *resolved* —
                        # filled, or given back by this same branch — so the
                        # deletion shifts only entries no live frame indexes,
                        # and no live frame's index goes stale.
                        del self.author_slots[contrib_frame.slot]
                        # #120's other half. The contributor is still dropped —
                        # nothing can be built from a <contrib> that names
                        # nobody — but counting it is what stops the next
                        # unhandled spelling of a name from being invisible for
                        # as long as <collab> and <string-name> were.
                        # `_audit_parse` reports the tally; see the counter's
                        # own comment for why it is not logged from here.
                        self.contribs_naming_nobody += 1

        elif name == "journal-title":
            if self.in_front:
                self.journal = text
        elif name == "article-id":
            parent = self._parent_element()
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
            # Cleared for *every* <article-id>, not only one this branch
            # consumed. The open sets it unconditionally, so an <article-id>
            # outside <article-meta>/<front> — JATS-invalid, but this parser is
            # deliberately lenient about invalid markup — used to strand it and
            # make the audit report a correctly-parsed article as a bmlib
            # defect. The value is read only above this line, so clearing it
            # here changes no parse result.
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
            # the same argument the <label> rule turns on, and the draws
            # settle it rather than merely illustrating it: the two issues
            # name <fn-group> and <boxed-text>, and every redraw has turned up
            # an owner neither of them mentions. The draw that has since been
            # replaced offered a **<list>**; the two committed now offer a
            # **<def-list>**, which no enumeration written from #125 and #130
            # would have held either.
            #
            # MEASURED, and this half is not a small population. Over the two
            # committed draws (`scripts/sample_jats_exhibits.py`, issue #138 —
            # 997 articles per window, drawn from a named PMC OA baseline
            # package and measured on Europe PMC's fullTextXML), counting only
            # a <title> that a <sec> was open for and that no exhibit already
            # excluded: **411 titles in 104 of 997 recent articles (10.4%
            # [8.7-12.5])**, owned by a <caption> (387), a <def-list> (12) and
            # an <fn-group> (12). What owns
            # that <caption> is *not* recorded — the sampler counts the
            # <title>'s immediate parent alone — so a <boxed-text> or <media>
            # legend at section level is the likely reading rather than a
            # measured one. The back-filled window carries **none**, holding
            # no <caption> at all.
            #
            # Issue #125's own <fn-group> shape is in this draw, where it was
            # in neither of the last two, and it also reproduces on eLife's
            # PMC8754430, which loses its *Additional information* heading
            # twice over. The rate is still a floor rather than a rate for
            # that shape: 12 titles in 3 articles is the whole of it here.
            #
            # Both shapes were checked against the real deposits, old parser
            # against new: PMC8754430's section reads "Author contributions"
            # before and "Additional information" after, and PMC12755737's
            # reads a <supplementary-material> caption's lead before and
            # "Supporting information" after.
            parent = self._parent_element()
            if parent == "caption":
                self._append_caption_text(normalized_text)
            elif self.in_abstract and not (self.in_figure or self.in_table_wrap):
                # The exhibit test is what the parent rule replaced on the
                # section branch, and it has to stay on this one. JATS admits
                # a <fig> and a <table-wrap> in an <abstract> — a graphical
                # abstract — and the old `if self.in_figure or
                # self.in_table_wrap:` opening this whole branch swallowed
                # every <title> inside one. Without it a <table-wrap-foot>
                # <fn-group><title> in an abstract flushes the pending section
                # and installs itself as the next heading, splitting the
                # abstract and re-attributing the prose after it: exactly the
                # heading-the-publisher-never-wrote failure this rule exists
                # to remove, one branch over. It is also the worse half of it,
                # because `abstract_sections` is rendered into the HTML that
                # `FullTextService` caches while `body_sections` reaches no
                # bmlib path at all.
                #
                # THE POPULATION WAS MEASURED EMPTY AND IS NO LONGER
                # RE-DERIVABLE, which is worth saying plainly. Over the two
                # 300-article draws committed before issue #138, 44 <fig> or
                # <table-wrap> sat inside an <abstract> and **none carried a
                # <title>** (0 of 44). Those draws have been replaced, and
                # `scripts/sample_jats_exhibits.py` carries no counter for
                # this — it was an ad-hoc walk — so nothing in the repo
                # re-derives the 44 and the next reader must re-measure rather
                # than trust it. The guard is kept for the reason the
                # <alternatives> archival tiers are: what it prevents is
                # silent and, through the cache, permanent, and an empty
                # population is not an impossible one.
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
            self._append_prose(normalized_text, keep_empty=True)
        elif name == "tex-math":
            # Stashed, never merged: the choice between this and the MathML
            # beside it belongs to the formula, which has not closed yet, and
            # 4,377 of the package's 188,473 both-encoding formulas deposit
            # the MathML first (issue #147).
            #
            # With no formula open the expression is merged where it was
            # deposited — 5 <tex-math> in that package sit directly in a <p>.
            # Rendered rather than raw, because what the element holds is a
            # whole LaTeX document; `display=False` since a formula standing
            # in prose with no <disp-formula> around it is not a display one.
            if self.formula_stack:
                self.formula_stack[-1].latex.append(element_text)
            else:
                self._append_text(_latex_expression(element_text, display=False))
        elif name in _FORMULA_ELEMENTS:
            if self.formula_stack:
                # Guarded for the reason </fig> is: SAX makes a close with
                # nothing open unreachable, and a suppression region guarded
                # on startElement alone is how that stops being true.
                formula = self.formula_stack.pop()
                parent = self._parent_element()
                standalone = formula.display and parent not in _DISPLAY_FORMULA_MERGE_PARENTS
                # A number is printed where a number is what the reader reads:
                # standing apart, or filling a cell, which is not a sentence.
                # See `_TABLE_CELL_ELEMENTS` for the measurement.
                numbered = standalone or (formula.display and parent in _TABLE_CELL_ELEMENTS)
                rendered = _render_formula(formula, element_text, numbered=numbered)
                if standalone:
                    # A block-level equation between two paragraphs is a
                    # paragraph of the section — routed exactly as one, since
                    # it is one. `keep_empty=False` is the whole of the rule
                    # that a formula holding nothing adds no paragraph: an
                    # image-only <disp-formula> renders as "" and must not
                    # open one, and stating that twice would leave two
                    # spellings of one rule to keep in step.
                    if rendered and not self._prose_reaches_output():
                        # The rendition was built and has nowhere to go: no
                        # <caption> open inside a float, or <back> prose with
                        # no section on the stack. Counted rather than dropped
                        # in silence, the rule `rejected_spans` settled for
                        # #129 — a formula this parser rendered and then lost
                        # is exactly the event no reader could otherwise see.
                        self.formulas_dropped += 1
                    self._append_prose(rendered, keep_empty=False)
                else:
                    # Inside flowing text, which is where an inline formula
                    # always is and 77.4% of display formulas are.
                    # `_append_text` reaches the buffer this formula's own pop
                    # restored, so the rendition lands where the element was
                    # deposited.
                    if rendered:
                        rendered = _pad_as_deposited(rendered, element_text, formula.display)
                    self._append_text(rendered)
                    current_table = self.current_table
                    if current_table is not None and not self.formula_stack:
                        # characters() held this formula's text back from the
                        # cell so the LaTeX could be rendered first; this is
                        # where the cell gets its one rendition. Guarded on
                        # the innermost frame alone: a nested formula's
                        # emission is already inside the outer one's buffer,
                        # so offering it to the cell as well prints it twice.
                        current_table.append_cell_text(rendered)

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
            # same. A swallowed label is not a blank either: the marker that
            # overwrote it is rendered as the exhibit's own number, so the
            # symptom is a *wrong* number rather than a missing one. That is
            # what still makes this routing load-bearing now that an exhibit
            # carrying no label of its own is rendered without one (#162) —
            # the renderer no longer substitutes anything, so a mis-routed
            # marker is the only way an invented number can still appear.
            #
            # Asking the parent needs no enumeration of the containers that
            # may carry a <label>, which is what a depth counter needed and
            # what could not be completed by inspection. It is also exact
            # where a depth is merely close: an exhibit opened *inside* a
            # footnote still gets its own label, since its <label>'s parent is
            # the exhibit either way.
            #
            # THE PREMISE IS NOT REFUTED BY THE COMMITTED CORPUS, AND IT IS
            # NOT CONFIRMED BY IT EITHER (issue #162). This comment used to
            # say it was VIOLATED, on 6,937 exhibits carrying a direct-child
            # <label> against 6,944 "carrying one anywhere". The second figure
            # is `exhibits_with_descendant_label`, which counts an exhibit
            # holding **any** <label> in its subtree — so the difference is
            # the set a descendant-search fallback would *fire* on, and says
            # nothing about where an exhibit's own label sits. It was read as
            # the premise, which is this repo's standing lesson (a count is of
            # what you looked for) inside the instrument built to check it.
            #
            # Fetched from Europe PMC on 2026-09-02, all seven of those
            # articles' exhibits (PMC12011025, PMC12111618, PMC12115352,
            # PMC12149983, PMC12154067, PMC12159547, PMC12177175) are a
            # <table-wrap> carrying no <label> and no <caption>, and every
            # label below them is a <table-wrap-foot><fn> marker (`*`, `**`,
            # the empty string) or a <list-item> bullet inside a cell (`1.`,
            # `-`, `•`). Those are the two containers #116 was about, so a
            # descendant search would have corrupted 7 of 7 — which is why the
            # fallback #162 proposed is refused rather than deferred. Four are
            # deposited under ids their publisher reserves for an unnumbered
            # table (`array1`, `array2`, `utbl0001`), so the missing label is
            # the deposit's intent.
            #
            # Deciding the premise would need a rule for which of an exhibit's
            # descendant labels *would* have been its own, and that rule is
            # this one. So it stands on the argument below rather than on a
            # measurement, and the honest population beside it is a different
            # one: 121 exhibits of 7,058, in 83 of 997 recent articles, carry
            # no <label> of their own. `to_html` used to give each of those an
            # invented `Figure {i + 1}` / `Table {i + 1}`; it no longer does.
            #
            # The rule is also much the better of the two on the one
            # comparison the corpus does support. A depth counter would
            # *mis-assign* 561 labels in 95 of these 997 articles — <fn>
            # (330), <list-item> (225) and <supplementary-material> (6), the
            # last a container no enumeration written for #116 named. A
            # corruption is worse than a blank, and the parent test needs no
            # list to avoid it.
            #
            # The back-filled window numbers every exhibit it deposits: 627 of
            # 627, all <fig>, and no <table-wrap> at all — so it contributes
            # nothing to either population above, rather than corroborating
            # one of them.
            #
            # Of the 7,498 labels inside a recent exhibit,
            # 92.5% are the exhibit's own. The six that a
            # <supplementary-material> owns are one eLife article's figure
            # supplements, the same article the caption stack above is
            # exercised by — so that container is now measured rather than
            # named as absent, and a single deposit convention is what put it
            # in both windows' worth of evidence at once.
            parent = self._parent_element()
            if parent in _FORMULA_ELEMENTS and self.formula_stack:
                # An equation number, and the same parent test: a
                # <disp-formula>'s "(1)" is one of the four labels the
                # retired depth counter mis-assigned to the exhibit around it
                # (issue #116), so routing it here rather than by an ambient
                # flag is what keeps it off the figure that encloses it.
                self.formula_stack[-1].label = text
            elif parent == "fig" and self.current_figure is not None:
                self.current_figure.label = text
            elif parent == "table-wrap" and self.current_table is not None:
                self.current_table.label = text
            elif parent == "ref" and self.current_reference:
                # The same parent test, and it was missing here alone: this
                # branch was gated on the ambient `in_ref`, which is the very
                # routing #116 established is wrong, one element family over.
                # A <ref> may hold several citation elements, and RSC gives
                # each its own <label> — "(a)", "(b)", "(c)" — so the ambient
                # flag let the last part's marker become the reference's
                # number. Measured over 880 local PMC articles: 158 references
                # in 14 of them, and **nought** where a real reference label
                # was overwritten — so the whole population is a number the
                # publisher never wrote, on a reference that has none. Which
                # is #116's own symptom: a swallowed label is not a blank, it
                # is an invented value. The markers are not lost either way;
                # they sit in `citation`, where the deposit puts them.
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
                # Only <mixed-citation> writes the string, and the asymmetry is
                # load-bearing twice over. An <element-citation>'s content model
                # is element-only, so its buffer holds whatever text arrived
                # from children this module does not accumulate — a book's
                # <edition>/<publisher-loc>/<publisher-name> gave
                # "3rd edAmsterdamElsevier", which is the run-together word the
                # exclusion exists to avoid, not the empty string it was
                # documented to leave. And a <ref> may carry both spellings —
                # JATS admits them as siblings and inside <citation-alternatives>
                # — so an unconditional assignment is last-writer-wins: an
                # <element-citation> deposited second wiped a <mixed-citation>
                # the publisher did typeset. Appending on one branch only makes
                # the documented "empty" true and states the precedence.
                #
                # Appended raw, and appended rather than assigned: see
                # `_ReferenceBuilder.citation_parts` for why a <ref> is a list
                # of parts and why they are joined without a separator.
                if name == "mixed-citation":
                    self.current_reference.citation_parts.append(element_text)
                self.in_ref_citation = False
        elif name == "person-group":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.finish_current_author()
                self.in_ref_person_group = False
        elif name == "surname":
            if self.in_front:
                self.front_contributor_name_count += 1
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
            if self.in_front:
                self.front_contributor_name_count += 1
            if self.in_ref_citation and self.current_reference and text:
                # Normalised, not merely stripped; see the <string-name> arm.
                self.current_reference.authors.append(normalized_text)
            elif self.in_contrib and self.current_author and text:
                # A collaboration is not a person and gets a field of its own;
                # see JATSAuthorInfo for why it is not folded into `surname`.
                self.current_author.collab = text
        elif name == "on-behalf-of":
            # Counted, not extracted. A fourth spelling: JATS 1.2 admits
            # <on-behalf-of> as a <contrib>'s name, and an article naming its
            # only contributor that way parses to no authors and then reached
            # the *quiet* branch of the zero-author detector — certified as
            # naming nobody, which is #120 and #140 verbatim one element
            # further out. Counting it here is what makes that branch loud;
            # extracting it is its own issue.
            if self.in_front:
                self.front_contributor_name_count += 1
        elif name == "string-name":
            if self.in_front:
                self.front_contributor_name_count += 1
            if self.in_ref_citation and self.current_reference:
                # Gated exactly as the <collab> branch above is, on the whole
                # citation rather than on `in_ref_person_group`: JATS admits
                # either spelling as a direct child of <mixed-citation> and
                # <element-citation>, and the narrower gate dropped a cited
                # name that was sitting in the markup — the failure direction
                # #120 and #140 are about, one element family over.
                if (
                    self.current_reference.current_author_surname
                    or self.current_reference.current_author_given_names
                ):
                    # A <string-name> that *divided*. Its <surname> and
                    # <given-names> children have already routed through the
                    # arms above, so this element's own buffer holds nothing
                    # but the punctuation between them — appending it put a
                    # bare "," in the author list, ahead of the name itself.
                    # Flushing here rather than appending is also what stops
                    # two divided siblings collapsing onto the last of them:
                    # only </name> and </person-group> flush, and neither
                    # closes between two adjacent <string-name>.
                    self.current_reference.finish_current_author()
                elif text:
                    # **Normalised, not merely stripped.** `text` is
                    # end-stripped only, and since #146 this buffer holds the
                    # merged text of the element's children rather than the
                    # whitespace between them — so a Wiley deposit spelling a
                    # cited name `<string-name><given-names>J.</given-names>
                    # <surname>Tan</surname></string-name>` outside any
                    # `<person-group>` (where neither child's arm fires, both
                    # being gated on `in_ref_person_group`) put the literal
                    # `"J.\nTan"` into `references[].authors` and thence into
                    # the HTML `FullTextService` caches, as a line break
                    # mid-name. Every other author reaching this list is built
                    # by `finish_current_author()`, which joins its parts with
                    # a single space; this is the one arm that appends a raw
                    # buffer, so it is the one arm that has to normalise.
                    self.current_reference.authors.append(normalized_text)
            elif self.in_contrib and self.current_author and text:
                # Only where no structured name arrived. JATS permits
                # <string-name> to carry <surname> and <given-names> children,
                # and those already routed through the arms above — so this
                # element's own buffer then holds nothing but the punctuation
                # between them, which is not a name.
                if not (self.current_author.surname or self.current_author.given_names):
                    self.current_author.string_name = text
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

        # Pop element stack. Last, and load-bearingly so: every handler above
        # that asks what encloses the element now closing reads the stack with
        # that element still on it. `_inside_mixed_citation`'s
        # `element_stack[:-1]` is a *strict*-ancestor slice for that reason
        # alone, and the `element_stack[-2]` parent tests for `<title>` and
        # `<label>` name the owner for the same one. Moving this up shifts
        # them one element outwards, and the cost is measured rather than
        # asserted: 58 tests in `test_jats_parser.py` redden for a pop placed
        # just before the handler arms, and 65 for one placed above the buffer
        # pop at the top of the method. Only the second reaches the citation
        # slice, because `_inside_mixed_citation` is called from inside
        # `_pop_text_buffer`'s own argument — which is why "move the pop up"
        # has to name *how far* up to mean anything.
        #
        # Two neighbours are deliberately not on that list. The `<caption>`
        # parent test is made in `startElement`, where the *push* is what
        # places it. And `<article-id>`'s is pinned by nothing here: it is
        # disjoined as `parent == "article-meta" or self.in_front`, so the id
        # is admitted whichever element a shifted index names.
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


def _audit_parse(handler: _JATSHandler) -> None:
    """Report what this parse left behind: an imbalance, or no authors at all.

    An unbalanced stack or counter is issue #134 and logs at ERROR here; the
    zero-author case is issue #121 and picks its own level in
    :func:`_report_zero_authors`; a refused ``colspan`` is #129's other half
    and logs at WARNING. All three live at this one call site because it is
    the only place every entry point can hear them.

    Called from :meth:`JATSParser._run_parser`, which is the one place
    :meth:`~JATSParser.parse`, :meth:`~JATSParser.to_html` and
    :meth:`~JATSParser.parse_with_html` all funnel through — so every entry
    point is covered without any of them having to remember.

    ERROR, and not a raised exception: a partial article reported loudly beats
    no article, which is #129's mistake in the other direction. And ERROR
    rather than WARNING because ``expat`` rejects an unbalanced *document*, so
    nothing a publisher deposits can reach the audit's predicates — every
    line they produce is a claim that bmlib itself is wrong. Keeping that
    meaning exact is why the zero-author case, which *can* fire on a document
    bmlib parsed correctly, is a WARNING instead.

    Args:
        handler: The handler the parse just finished with.
    """
    article = handler.describe_article()
    for message in unwind_diagnostics(handler.unwind_state()):
        logger.error("JATS parse of %s: %s", article, message)

    if handler.rejected_spans:
        # WARNING, not ERROR: unlike the audit above, a publisher's deposit
        # reaches this one, so reporting it at ERROR would spend the "an ERROR
        # here means bmlib is wrong" contract the audit depends on. And once
        # per article rather than once per cell, which a 40-cell table made
        # unreadable.
        logger.warning(
            "JATS parse of %s: %d table cell(s) declared a colspan this parser "
            "would not honour and were rendered as one column — every later cell "
            "in those rows sits one column left of where the document put it",
            article,
            handler.rejected_spans,
        )

    if handler.contribs_naming_nobody:
        # WARNING for `rejected_spans`' reason — a publisher's deposit reaches
        # it — and phrased as evidence rather than as a conclusion, for
        # `_report_zero_authors`' reason: "carried no name" would be a claim
        # about the document, and the spellings bmlib reads are exactly what
        # #120 and #140 proved incomplete. What is certain is that bmlib read
        # none, which is the fact worth reporting either way.
        logger.warning(
            "JATS parse of %s: %d <contrib>(s) collected as an author yielded no "
            "name bmlib could read, so those contributors are missing from the "
            "author list",
            article,
            handler.contribs_naming_nobody,
        )

    if handler.formulas_dropped:
        # WARNING for `rejected_spans`' reason: a publisher's deposit reaches
        # it — a <disp-formula> in an unsectioned <back>, or under a wrapper
        # inside a float — so it cannot mean "bmlib is wrong" the way the
        # audit above does. Phrased as what happened rather than as a
        # conclusion about the document: the equation was in the deposit and
        # this parser rendered it, which is what makes the loss reportable.
        logger.warning(
            "JATS parse of %s: %d display formula(s) were rendered but reached no "
            "section, caption or cell, so their equations are missing from the "
            "article (issue #177)",
            article,
            handler.formulas_dropped,
        )

    if not handler.build_authors():
        _report_zero_authors(handler, article)


def _report_zero_authors(handler: _JATSHandler, article: str) -> None:
    """Say whether a parse that produced no authors looked in the wrong place.

    An article that parses to zero authors renders HTML byte-identical to one
    that genuinely lists none, and ``FullTextService`` caches that HTML — so
    the correct answer and the catastrophic one persist to disk the same way.
    Issue #111 dropped every author from 57% of open-access articles and
    survived undetected until it was found from outside bmlib, while porting
    the parser to Swift. This is the detector that was missing throughout
    (issue #121).

    ``front_contributor_name_count`` is what separates the two: a document
    naming contributors in its ``<front>`` and yielding no authors was most
    likely mis-routed, while one naming none is simply author-less.

    **It counts every JATS spelling of a contributor's name**, not just
    ``<surname>``. Counting surnames alone, the spellings bmlib did not then
    extract — ``<string-name>``, which loses 100% of an article's authors, and
    ``<collab>`` (issue #120), which loses some — both landed in the quiet
    branch below and were certified *genuinely author-less*, which is a
    positive claim their evidence never supported. Both are extracted now, and
    ``<on-behalf-of>`` is counted for the same reason while it is not: counting
    is not parsing, extracting a spelling remains its own issue, and the quiet
    branch says what it actually checked rather than what it concluded.

    **WARNING and not ERROR**, unlike the audit above it. That distinction can
    fire on a well-formed document bmlib parsed correctly — #121's measurement
    (1,025 articles, drawn during the Swift port; not reproducible from a
    committed corpus) names ``PMC12803704``, an ``article-type="correction"``
    that is genuinely author-less and still carries ``<front>`` surnames — so
    it is a "look at this", where ERROR here means only "bmlib is wrong".

    Args:
        handler: The handler the parse just finished with.
        article: How to name the article in the message.
    """
    if handler.front_contributor_name_count:
        logger.warning(
            "JATS parse of %s produced no authors, but its <front> named "
            "%d contributor(s): they were most likely routed elsewhere",
            article,
            handler.front_contributor_name_count,
        )
    else:
        # Reports its evidence, not a conclusion. "No <surname>, <string-name>,
        # <collab> or <on-behalf-of> in <front>" is what was checked;
        # "genuinely author-less" is an inference, and it was wrong for every
        # spelling this counter did not yet cover.
        logger.debug(
            "JATS parse of %s produced no authors, and its <front> named no "
            "contributor via <surname>, <string-name>, <collab> or <on-behalf-of>",
            article,
        )


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
        _audit_parse(handler)
        return handler

    def parse(self) -> JATSArticle:
        """Parse XML and return structured article data."""
        h = self._run_parser()
        return JATSArticle(
            title=h.title,
            authors=h.build_authors(),
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
            # NO NUMBER IS INVENTED FOR AN EXHIBIT THE PUBLISHER DID NOT NUMBER
            # (issue #162). `fig.label or f"Figure {i + 1}"` stated a number the
            # document does not carry, which is #116's own symptom — a swallowed
            # label is not a blank — reached from the other side, and it is a
            # measured population rather than a hypothetical: 7,058 exhibits in
            # the committed recent corpus carry 6,937 direct-child <label>
            # elements, so 121 of them, in 83 of 997 articles (1.7% and 8.3%),
            # were given one. The back-filled window measures 0 of 627.
            #
            # It is worse than a blank for the reason #116 was. The invented
            # number is the *index*, so it does not merely add a number — it
            # collides with a real one, and a paper whose first figure is an
            # unnumbered schematic rendered two exhibits as "Figure 1". Four of
            # the seven inspected deposits carry ids their publisher reserves
            # for an unnumbered table (`array1`, `array2`, `utbl0001`), so the
            # absent label is the deposit's intent and not an omission.
            #
            # This rule was already bmlib's, one branch over: an unsectioned
            # `<body>`'s prose becomes a `JATSBodySection` with an empty title
            # and `to_html` renders it with no heading, because no heading is
            # invented (#30). Stated on one branch and not applied on the next
            # is the shape this module keeps being caught by.
            #
            # The anchor id keeps its fallback: `fig{i + 1}` is a link target
            # this renderer owns, never a claim about what the document says.
            anchor_id = fig.id or f"fig{i + 1}"
            parts.append(f'<figure id="{html_escape(anchor_id)}">')
            if fig.graphic_url:
                full_url = _build_exhibit_url(fig.graphic_url, h.pmc_id)
                # `alt` carried the same invented number, where a screen reader
                # reads it out as the document's own. It falls back to the
                # caption — text the deposit does carry — and then to the empty
                # string, which asserts nothing rather than asserting a number.
                alt = fig.label or fig.caption
                parts.append(
                    f'  <img src="{html_escape(full_url)}" alt="{html_escape(alt)}" loading="lazy">'
                )
            # Emitted only where the deposit gives it something to hold. It
            # used to be unconditional because the invented number always
            # filled it, so dropping that would otherwise leave an empty
            # <figcaption> on every unlabelled, uncaptioned figure.
            if fig.label or fig.caption:
                parts.append("  <figcaption>")
                if fig.label:
                    parts.append(f"    <strong>{html_escape(fig.label)}</strong>")
                if fig.caption:
                    parts.append(f"    <p>{html_escape(fig.caption)}</p>")
                parts.append("  </figcaption>")
            parts.append("</figure>")

    # Tables
    if h.tables:
        parts.append("<h2>Tables</h2>")
        for i, tbl in enumerate(h.tables):
            # The figure branch above carries the argument; this is the same
            # rule, and the table side is where issue #162's whole inspected
            # population sits. All seven of the exhibits it names are a
            # <table-wrap> carrying neither a <label> nor a <caption>, so this
            # heading was the only text rendered for them and every word of it
            # was bmlib's.
            anchor_id = tbl.id or f"table{i + 1}"
            parts.append(f'<div class="table-container" id="{html_escape(anchor_id)}">')
            if tbl.label:
                parts.append(f"  <h3>{html_escape(tbl.label)}</h3>")
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
                alt = tbl.label or tbl.caption
                parts.append(
                    f'  <img src="{html_escape(full_url)}" alt="{html_escape(alt)}" loading="lazy">'
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
