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
    #: The container heading an *implicit* section was opened under (#231), or
    #: ``None`` for one opened under no heading. Compared by **identity** in
    #: :meth:`_JATSHandler._implicit_section_for_prose`, which is what makes a
    #: builder accept prose only while the frame it was opened under is still
    #: the innermost one. A ``<sec>``'s builder never consults it.
    heading: _HeadingFrame | None = None

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
# draws — 1,000 articles each, 997 of the recent window served, drawn
# deterministically from a named PMC OA
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
# derivable from either corpus. At that wider scope, across all 13,624:
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
# browser renders. "No instance in 1,997 articles" is not "cannot happen", and
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
    ``<table-wrap>`` in 1,000 articles. So the answer above rests entirely on
    the recent window, and the redrawn back-filled one can no longer
    corroborate or contradict it. (*That the 1996-1998 ``oa_comm`` material is
    scanned page images with no tabular markup is an inference*, from 0 tables
    beside 627 figures and 3,880 ``.png`` deposits; no counter measures it.)

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
        #138 — a 1,000-article draw per window, 997 of the recent one and
        all 1,000 of the back-filled one served, drawn from a named PMC OA
        baseline package and measured on Europe PMC's ``fullTextXML``): of
        **4,602**
        recent figures carrying a ``<graphic>``, **57.8%** [56.3-59.2] carry
        more than one and **57.3%** [55.9-58.8] end on a thumbnail; of **627**
        back-filled ones, **44.0%** [40.2-47.9] on both counts. **0%** deposit
        a thumbnail *first* in either — so the convention that motivates
        ranking over plain first-wins appears in neither window, and ranking
        earns its place on the other number: it is what stops half of all
        figures resolving to a preview.

        **These are what the parser routes, not what a subtree holds** (issue
        #164). The sampler counted every ``<graphic>`` anywhere below a
        ``<fig>`` until then, so a ``<td>``'s cell image and a nested figure
        supplement's deposit counted as the enclosing figure's. Scoping it to
        the owner test this module actually uses moves **one** of the four
        counts, by **18 figures**: 2,676 to 2,658, 58.1% to the 57.8% above,
        each inside the other's interval. Nothing else moves, in either
        window — the thumbnail-position counts are identical — and the
        corpus keeps both readings per row, so the correction is derivable
        from one file rather than from a diff against another draw.

        The size of that is worth stating because #164 expected otherwise. Its
        spot check over the same articles' *archive* bytes moved the
        multi-graphic count 77 to 58 and read as "large enough to matter" —
        an ad-hoc measurement over package bytes, in the repo nowhere and
        re-derivable from neither corpus, so it is quoted the way the
        225-article survey and the vanished 276-article draw are. The
        absolute correction is almost identical on the two renditions — 19
        figures there, 18 here — but the archive holds 77 multi-graphic
        figures against the served rendition's 2,676, so the same 18-or-so
        figures are a quarter of one population and two thirds of one percent
        of the other. **A share is of a denominator, and the rendition
        chooses the denominator.**

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


@dataclass(kw_only=True)
class _FootnoteHolder:
    """The half of an exhibit builder that collects its footnotes (issue #124).

    Shared by :class:`_FigureBuilder` and :class:`_TableBuilder` for
    :class:`_GraphicHolder`'s reason and one more. The first is that two copies
    of a rule are two things to keep in step. The second is that the two sides
    were measured wildly apart when #124 landed — of the 16,935 notes it routed
    across the 8,118 served articles of ``PMC10030002_PMC10040000.xml.gz``,
    **2** are a figure's, and 277 of 190,198 across the 97,909 archive articles
    of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`` — so a per-exhibit
    implementation would have left the figure half effectively untested by any
    corpus, and one holder is what makes the table side's exercise the figure
    side's too. Issues #241 and #248 changed the figure side's standing: the
    holder also files an exhibit's ``<attrib>``, and figures carry most of
    those — 125 served and 677 archive, against 21 and 192 for tables.

    ``pending_footnote_label`` is the marker a ``<label>`` read for the ``<fn>``
    now open, held until the first paragraph of that same note spends it. It is
    a single slot rather than a stack because a ``<fn>`` does not nest inside a
    ``<fn>``, and because an exhibit *opened* inside a footnote gets its own
    holder — the owner walk in
    :meth:`~bmlib.fulltext.jats_parser._JATSHandler._owning_exhibit_footnote`
    ends at the first exhibit, so the inner one never reaches this one.

    **Left pending it would be spent on another note's prose**, which is a
    wrong value where the alternative is a blank, so ``</fn>`` takes it back
    and counts it. That is #228's own hazard one container over, and the
    population is 1 of 10,763 served ``<fn>`` — a direction, not a rate.

    ``kw_only`` for :class:`_GraphicHolder`'s reason: these fields are
    inherited and would otherwise lead both subclasses' generated ``__init__``.
    """

    footnotes: list[str] = field(default_factory=list)
    pending_footnote_label: str = ""
    #: Where the first unmarked credit of the ``<fn>`` now open was filed, while
    #: a marker was pending — ``None`` otherwise. A credit leaves the marker for
    #: the note's own prose (``fold_marker=False``), but a note may deposit no
    #: prose beside the image at all, and then the credit *is* the note: giving
    #: the marker back at ``</fn>`` left ``['Photo: Getty.']`` against a body
    #: reading ``12.3b`` and a WARNING saying bmlib filed no prose for a note
    #: it had just filed (PR #250's review). ``main`` stored ``'b — Photo:
    #: Getty.'``. So :meth:`take_pending_footnote_label` folds the marker into
    #: that credit instead. 0 such notes in either artifact.
    unmarked_credit_slot: int | None = None

    def append_footnote(self, text: str, *, fold_marker: bool = True) -> None:
        """File ``text`` as a note of this exhibit, folding in a held marker.

        The marker is spent by the *first* paragraph of its own ``<fn>``, so a
        note deposited as several paragraphs is marked once and its
        continuations arrive unmarked — which is where a reader expects the
        marker to be, and matches the sibling Swift port's
        ``append_footnote``.

        Args:
            text: The note's prose, already whitespace-normalised. An empty
                string files nothing and leaves the marker pending, so an
                empty ``<p>`` ahead of a note's real prose does not consume
                it.
            fold_marker: ``False`` for text that is filed as a note but is not
                the note's own prose — an ``<attrib>`` crediting an image
                inside it (issues #241, #248) — which leaves the marker pending
                for the prose it labels. Folded into the credit, the marker
                would point the body's ``12.3a`` at the wrong sentence. Where
                the note deposits no prose, ``</fn>`` folds it into the credit
                after all; see :attr:`unmarked_credit_slot`. The ``<attrib>``
                arm also passes ``False`` for an exhibit's own attribution,
                where no marker can be pending and it states the rule.
        """
        if not text:
            return
        if self.pending_footnote_label:
            if fold_marker:
                text = f"{self.pending_footnote_label} — {text}"
                self.pending_footnote_label = ""
                self.unmarked_credit_slot = None
            elif self.unmarked_credit_slot is None:
                self.unmarked_credit_slot = len(self.footnotes)
        self.footnotes.append(text)

    def hold_footnote_label(self, marker: str) -> str:
        """Hold ``marker`` for the ``<fn>`` now open, and say what it displaced.

        The class is the sole writer of its own slot, so every way a marker can
        leave without reaching a note goes through one of these three methods
        and is answerable to the caller. Assigning the field directly was the
        ``<term>`` arm's own defect one container over: JATS models ``<fn>``
        as ``(label?, …)``, so a second ``<label>`` is invalid and *not*
        ill-formed, expat does not validate a content model, and a bare
        last-wins put the second marker on the first note's prose with nothing
        counted — *"a rule resting on a remembered content model is the rule
        this module keeps being caught by"*.

        An **empty** ``marker`` displaces just as a different one does. ``""``
        is this field's absent spelling, so an empty ``<label>`` would
        otherwise erase a good marker *and* leave ``</fn>`` nothing to give
        back, which is the one route by which a marker could still vanish with
        no line at all — a note rendering unmarked against a body that still
        reads ``12.3a``, which is the dangling reference the whole feature
        exists to prevent.

        Measured 0 of 8,118 served and 0 of 97,909 archive articles deposit a
        second ``<label>`` in one ``<fn>``, so this pins a direction rather
        than a population — the standing this module gives the nesting rules
        beside it. An empty ``<label>`` alone *is* deposited (3 served, 11
        archive) and costs nothing, there being no marker to displace.

        Args:
            marker: The marker just read, or ``""``.

        Returns:
            The marker this one displaced, or ``""`` when the slot was free.
        """
        displaced, self.pending_footnote_label = self.pending_footnote_label, marker
        return displaced

    def take_pending_footnote_label(self) -> str:
        """Settle an unspent marker at ``</fn>``, and say what was lost.

        A marker no paragraph of the note claimed is folded into the note's
        first image credit where one was filed — the credit being then the
        whole of what the note deposited — and given back otherwise.

        Returns:
            The marker that reached nothing in the output, or ``""``.
        """
        marker, self.pending_footnote_label = self.pending_footnote_label, ""
        slot, self.unmarked_credit_slot = self.unmarked_credit_slot, None
        if marker and slot is not None:
            self.footnotes[slot] = f"{marker} — {self.footnotes[slot]}"
            return ""
        return marker


@dataclass
class _FigureBuilder(_GraphicHolder, _FootnoteHolder):
    id: str = ""
    label: str = ""
    caption: str = ""

    def build(self) -> JATSFigureInfo:
        return JATSFigureInfo(
            id=self.id,
            label=self.label,
            caption=self.caption,
            graphic_url=self.graphic_href or None,
            footnotes=list(self.footnotes),
        )


@dataclass
class _TableBuilder(_GraphicHolder, _FootnoteHolder):
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
            footnotes=list(self.footnotes),
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
    #: The text alternative of an image this formula holds — the rendition of
    #: last resort, used only where no ``<tex-math>`` renders *and* the buffer
    #: is empty (issues #241, #248). An ``<alt-text>`` is declined metadata and
    #: no longer reaches the buffer, so without this an image-only formula
    #: whose deposit spelled it out lost it: ``'where alpha is the rate.'``
    #: became ``'where is the rate.'``, and a labelled ``<disp-formula>``
    #: rendered as nothing, taking the ``(1)`` the body prose cross-references
    #: with it (PR #250's review). A field rather than a merge back into the
    #: buffer, because a MathML formula carrying an image's ``<alt-text>`` as
    #: well would then print one expression twice — the outcome
    #: ``_FORMULA_ELEMENTS`` exists to prevent, and a weld ``main`` produced.
    #: First deposit wins, for ``latex``'s reason. 0 ``<alt-text>`` with text
    #: sits in a formula across 7,836 parseable served articles and all 97,909
    #: archive ones, so this pins a direction.
    alt_text: str = ""


@dataclass
class _DefinitionFrame:
    """One open ``<def-item>``: the word it defines, and where it opened.

    A stack of these, for the reason :class:`_ExhibitFrame` is one: a ``<def>``
    admits a ``<def-list>``, so definition items nest. Held as one slot the
    inner item's term overwrote the outer one's and the inner close cleared
    it, which is issue #115 one element family over — and the outer item's own
    definition, arriving after the nested list, would then take the inner
    term or none at all.

    ``term`` is the word this item defines, or ``None`` once it has been folded
    into its definition's prose or where none has arrived. **The two are one
    state and the write site is what keeps them one**: a ``<term>`` that
    normalises to the empty string is stored as ``None`` rather than ``""``, so
    every reader can ask ``if frame.term`` and ``if frame.term is None`` and
    get the same answer. Carrying the third state let one reader's spelling
    disagree with another's while both passed (PR #236's review).

    ``exhibit_depth`` is ``len(figure_stack) + len(table_stack)`` at the moment
    this item opened — the :class:`_ExhibitFrame` idiom of capturing at the
    open the value a later decision needs. The fold spends the term on the
    next prose to reach output, and JATS admits a ``<fig>`` or ``<table-wrap>``
    inside a ``<def>``, whose ``<caption>`` is prose that reaches output while
    this item is open: without the capture the term was folded onto the
    *exhibit's caption*, a wrong value in a public field that ``to_html``
    renders, while the definition itself lost the word (PR #236's review).
    Comparing against the depth at the open — rather than scanning
    ``element_stack`` for element names — keeps the test derived from the same
    two stacks ``in_figure`` and ``in_table_wrap`` derive from, so it cannot
    drift from the routing it is guarding.

    It is a *depth* and not a flag because exhibits nest, and it is compared
    against rather than stored as a boolean because a ``<def-list>`` sitting
    **inside** a caption is legitimate and common: there the exhibit opened
    before this item, the depth is unchanged, and the fold must proceed.

    **The population is empty on both committed artifacts** — 0 of 14,186
    ``<def-item>`` in the 8,118 served articles of
    ``PMC10030002_PMC10040000.xml.gz`` and 0 of 153,395 in the 97,909 archive
    articles of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` hold a
    float inside their ``<def>``. Those are **whole-document** walks, so the
    archive denominator is the unscoped 153,395 rather than the 153,256 this
    parser sees; a zero over the wider set is a zero over the subset, which is
    why the looser walk is quoted rather than corrected. So this pins a
    direction and not a population, the standing the ``<term>`` parent test
    one arm over is given. "No instance" is not "cannot happen", and what it
    prevents is silent, permanent and a corruption rather than a blank.
    """

    term: str | None = None
    exhibit_depth: int = 0


@dataclass(frozen=True, eq=False)
class _HeadingFrame:
    """A heading a container deposited for its own unsectioned prose (#231).

    Issues #224 and #230 route unsectioned ``<back>`` and ``<front>`` prose
    into ``body_sections``, and every container's run arrived **untitled**:
    an ``<ack>``, an ``<fn-group>`` and a ``<glossary>`` concatenated with no
    heading between them and none above them. The heading was there — the
    publisher deposited ``<title>Acknowledgements</title>`` — and the
    ``<title>`` owner rule (#125, #130) dropped it, because an ``<ack>`` is
    not a ``<sec>`` and must never *rename* a section. Refusing the rename and
    keeping the heading are different questions, and only the first was
    answered.

    **The frame is live exactly as long as the element that deposited the
    heading**, which is what ``owner_depth`` records: ``len(element_stack)``
    at which that element is the innermost open one, captured when the
    heading is read — the :class:`_ExhibitFrame` idiom of capturing at the
    open what a later decision needs. An implicit section is opened under the
    innermost live frame and accepts prose only while that same frame is
    still innermost (:meth:`_JATSHandler._implicit_section_for_prose`), so
    without the boundary the next container's prose would inherit the
    heading and an untitled ``<fn-group>``'s competing-interest note would
    render under *Acknowledgements*: a **wrong** heading where the
    alternative is none, which is what #116 and #162 each refused from the
    other side.

    **Identity, not value** (``eq=False``): two sibling ``<notes>`` each
    depositing *Notes* are two frames and two sections, where comparing by
    value would merge them — and a frame is compared at every unsectioned
    run, so a value comparison is the one that would be written by accident.
    **Frozen**, because nothing updates a frame in place: an element
    depositing a second ``<title>`` gets a *new* frame
    (:meth:`_JATSHandler._recover_container_heading`), so the prose after the
    second heading opens a section of its own rather than joining one titled
    with the first. **No defaults**, because every field is captured at the
    read and a defaulted ``owner_depth`` of ``0`` is the one value that could
    never pop — ``len(element_stack)`` is at least ``1`` at every close — so
    it is not merely unreachable but uniquely fatal, and a constructor that
    cannot produce it is cheaper than a comment saying nobody does.

    **The title is never the empty string**: :meth:`_recover_container_heading`
    is the one writer and refuses it, so ``if frame.title`` and ``frame is
    None`` cannot disagree about whether a heading is open — the hazard
    :class:`_DefinitionFrame` collapsed ``""`` into ``None`` to remove, taken
    here at the write site for the same reason.

    **A stack, not a slot**, for :class:`_DefinitionFrame`'s reason one element
    family over: a ``<glossary>`` heading a ``<def-list>`` that heads itself
    nests, and held as one value the inner close would clear the outer
    container's heading for the prose still to come under it.

    It carries no slot, and since the flush became lazy it needs none: a frame
    never flushes anything. The only flush it causes runs from
    :meth:`_append_prose`, which has just chosen its slot from ``in_body`` /
    ``in_back`` / ``in_front``, so the section it ends is always the one the
    run was about to join.
    """

    title: str
    owner_depth: int


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
    elocation_id: str = ""
    #: Whether the last element this ``<ref>`` closed was one of its own
    #: non-empty ``<elocation-id>`` parts, so the next may continue it. The
    #: close of any element but an ``<elocation-id>`` or one inside it clears
    #: it — the one signal an ``<element-citation>`` gives, since its buffer
    #: cannot show a child that keeps its text in a buffer of its own, such as
    #: a ``<source>`` (issue #265).
    elocation_may_continue: bool = False

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
            elocation_id=self.elocation_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _without_whitespace(text: str) -> str:
    """``text`` with every whitespace character removed."""
    return _WS_RE.sub("", text)


def _elocation_part_continues(buffer: str, joined: str, citation_element: str) -> bool:
    """Does a citation print its ``<elocation-id>`` parts ``joined`` as one run?

    Issue #265. ``buffer`` is the citation element's text buffer once the
    part now closing has merged into it, and ``joined`` the locator stored so
    far with that part appended, each part stripped of its own edge
    whitespace.

    Whitespace is judged by the spelling, because the two spellings mean
    different things by it (PR #269's review):

    - In a ``<mixed-citation>`` it is typeset text, so ``e1`` and ``e2``
      printed ``e1 e2`` are two locators and not ``e1e2``. The buffer, less the
      closing part's own trailing whitespace, must end with ``joined`` exactly
      — any whitespace or text printed between two parts, inside the elements
      or out, breaks the match, while a part's own inner whitespace
      (``quiz 380``) is on both sides of it.
    - An ``<element-citation>`` is element-only, so the whitespace between its
      children is insignificant indentation and cannot part them; there it is
      ignored on both sides. Only a close can part two parts in that spelling,
      which is why the caller tests one as well.

    All five split references measured (in the 97,909 archive articles) are
    ``<mixed-citation>`` deposits with nothing at all between the parts, so
    both readings join them.

    Args:
        buffer: The citation element's buffer, ending with the closing part.
        joined: The stored locator with the closing part appended.
        citation_element: ``"mixed-citation"`` or ``"element-citation"``.

    Returns:
        Whether the part continues the locator before it.
    """
    if citation_element == "mixed-citation":
        return buffer.rstrip().endswith(joined)
    return _without_whitespace(buffer).endswith(_without_whitespace(joined))


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
    content that is not there. An image carrying an ``<alt-text>`` is not
    nothing, and that text is the last rendition tried
    (:attr:`_FormulaFrame.alt_text`).

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
        body = frame.alt_text
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
        "elocation-id",
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
        # A CELL ACCUMULATES SO THAT ITS CHILDREN HAVE SOMEWHERE TO MERGE, AND
        # THE BUFFER IS THEN DISCARDED. A cell fills
        # `_TableBuilder.current_cell_text` from `characters()` directly, so
        # the buffer these take carries nothing any arm wants: it exists to be
        # what every child that *does* merge back merges into — an ordinary
        # `<p>` in a cell routes through its own arm, never through this — and
        # `</td>` pops it and drops it on the floor (issue #243). One arm does
        # consult it, and only for emptiness: the `</td>` arm tests it to
        # decide whether an unmodelled cell lost anything (`cell_text_dropped`,
        # issue #245). Its *content* is read nowhere.
        #
        # Accumulating in order to discard is not by itself unusual here —
        # `<sec>`, `<abstract>`, `<caption>`, `<def>`, `<list-item>`,
        # `<person-group>`, `<element-citation>`, `<alt-title>` and `<kwd>`
        # all take a buffer no arm consumes. What is particular to a cell is
        # *why*: those isolate prose their children have already routed
        # elsewhere, and so does this, but a cell's children route to a
        # builder rather than to the article.
        #
        # Without it a `<table-wrap>` deposited inside a `<p>` — legal JATS,
        # and 7,248 such deposits sit in 2,237 of the 8,118 served articles of
        # `PMC10030002_PMC10040000.xml.gz` — spliced every cell's text into
        # the sentence around it: `<p>Before<table-wrap>…12.3…</table-wrap>
        # after.</p>` stored `'Before12.3after.'` in `body_sections` and in
        # the HTML `FullTextService` caches, and an exhibit opened inside a
        # footnote's `<p>` gave the outer note `'a — See12.3'`. A *wrong*
        # value where a blank was the alternative.
        #
        # A hold inside `characters()` — the issue's own remedy, mirroring the
        # formula hold beside it — reaches two of the four routes found and
        # leaves two, the argument being about the fifth nobody has enumerated:
        # a `<xref>` *replaces* its text with a link built from the popped
        # buffer, so emptying that buffer yields `'[Figure](#f1)'` — the arm's
        # own `text or "Figure"` fallback, an *invented* label and so worse
        # than the blank it replaces, #162's own symptom — and the formula arm
        # appends its chosen rendition with its own `_append_text` that
        # `characters()` never sees. Enumerating the arms that merge is the
        # list #116 established cannot be completed by inspection; isolating
        # the buffer answers all four at once and answers an arm added later
        # too.
        #
        # Spelled literally because `_TABLE_CELL_ELEMENTS` — the named set the
        # two handler arms read — is defined below this one.
        "td",
        "th",
        # An object's non-prose metadata, isolated so that no child merging
        # back can reach the sentence the object stands in (issues #241,
        # #248). Spelled literally for the reason the cells are:
        # `_NON_PROSE_METADATA`, which states the rule and the measurement, is
        # defined below this set.
        "alt-text",
        "long-desc",
        "object-id",
        "permissions",
        # Printed content, isolated for the same reason and then *routed*,
        # which the metadata above never is: see the `</attrib>` arm.
        "attrib",
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
# [2.0-4.1], 145 regions in all**, and 0 of 1,000 in the back-filled window
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
# **153, in 12 of 997 recent articles** — and 0 of 1,000 back-filled — spread
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


# The two citation elements this module reads in a <ref>, JATS's element-only
# and mixed-content spellings of a reference.
_CITATION_ELEMENTS = frozenset({"mixed-citation", "element-citation"})


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
        # Accumulating so its arm reads its own text (issue #265), and inline
        # so that text still lands wherever it landed before the arm existed:
        # a <related-article> in a <p> or an <article-title> keeps its
        # locator in the sentence, and in an <element-citation> the parts reach
        # the buffer the arm's join reads. (In a <mixed-citation> it merges
        # through `_inside_mixed_citation` whether inline or not.)
        "elocation-id",
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

# The containers that make an exhibit's descendant prose that exhibit's
# *footnote* rather than its furniture (issue #124).
#
# Three, and they are NOT equally deposited — the comment says which, because
# "each is deposited" is what a first cut asserted and a draw refuted.
# <table-wrap-foot> holds them on the table side and is also the parent of the
# general note publishers put after the last marked one (5,901 loose <p> in
# 1,386 of the 8,118 served articles). <fn> is the note itself, and the table
# side reaches it through the foot wrapper while a <fig> deposits it bare.
#
# <fn-group> is **defensive and unexercised**: **0 of the 8,118 served
# articles and 0 of the 97,909 archive ones deposit an <fn-group> inside an
# exhibit at all**, by either parent. It is kept because removing it is not
# free — a loose <p> in such a group has no <fn> below it to be found by, and
# in a <fig> no <table-wrap-foot> above either, so nothing else in the walk's
# path answers — and because "no instance" is not "cannot happen", which is
# the standing this module gives the unreached ARCHIVAL rank two types over.
#
# **It is not claimed here that JATS admits the shape.** Five files asserted
# "JATS admits one in both exhibits" with no citation, in the same breath as
# calling <fn> the only one of the three a <fig> takes directly — two claims
# that cannot both hold, since the keep-argument above is entirely about
# <fig><fn-group><p> (PR #237's review). The content model was not resolvable
# offline, so the member rests on the walk's own shape and on a measured zero,
# which is the weaker and honest ground. It costs one frozenset entry.
#
# **And it is a deliberate divergence from the normative cross-platform
# spec**, which a porting reader in either direction has to be told about:
# `bmlibrarian_lite`'s `doc/cross_platform/jats_parsing.md` specifies
# `("table-wrap-foot", "fn")`, two elements, and the shipped Swift parser
# follows it. Neither side is wrong on any measured deposit — the population
# is 0 — so this is a note, not a defect to reconcile.
#
# **Membership is not the whole rule** — this set says only "footnote matter",
# and which exhibit it is filed on is `_owning_exhibit_footnote`'s ancestor
# walk. A <back><fn-group><fn> is a member of this set and belongs to no
# exhibit at all.
#
# <table-wrap-foot> is listed rather than left to the <fn> inside it because
# the loose <p> above has no <fn> to be found by.
_EXHIBIT_FOOTNOTE_CONTAINERS = frozenset({"table-wrap-foot", "fn", "fn-group"})

# The members of that set that carry a heading of their own (issue #238).
# JATS admits no <title> inside an <fn> — it is modelled `(label?, …)`, the
# spelling `hold_footnote_label` uses, and the ellipsis is a block class that
# does take a <graphic>, which the sibling arm counts — so the block's heading
# ("Note:", "Fontes:") has the <table-wrap-foot> or the <fn-group> as its
# parent. The <title> arm keys on that parent *and* on the owner walk finding
# an exhibit; membership here is half of the rule, and the set is pinned as a
# subset of `_EXHIBIT_FOOTNOTE_CONTAINERS`, since a member outside it could
# never set `saw_container` and the arm would silently go dead for it. The
# two sets are told apart only by an <fn><title>, which JATS does not admit,
# so the split is documentary rather than pinnable. Deposit survey over the
# archive artifact `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` (97,909
# articles), scoped as the parser routes — suppressed regions skipped, a cell
# ending the owner walk: **7 headings in 4 articles, every one a
# <table-wrap-foot>'s**, reading "Note" (4), "Note:" (2) and "Fontes:" (1),
# none empty; **0** in the 8,118 served articles of
# `PMC10030002_PMC10040000.xml.gz`. The issue's own 8 came from an unscoped
# whole-document walk, one wider than what the parser reaches. No <fn-group>
# heading is deposited inside an exhibit in either artifact, which follows
# from no <fn-group> being deposited there at all (see above); the member is
# kept for the same reason its parent set keeps it, and a fixture exercises
# it. The block's own <label>, modelled beside the <title>, is still dropped
# uncounted: issue #235's.
_EXHIBIT_FOOTNOTE_BLOCKS = frozenset({"table-wrap-foot", "fn-group"})

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
# it. Kept as a named set because it has four readers now —
# _DISPLAY_FORMULA_MERGE_PARENTS, the merge exclusion in endElement's pop
# preamble (#243) and the </td> arm that counts an unmodelled cell (#245) —
# and four spellings of "a cell" are four things to keep in step. The
# _TEXT_ACCUMULATING membership is the one place the pair is still written
# out, that set being defined above this one.
_TABLE_CELL_ELEMENTS = frozenset({"td", "th"})

# An object's metadata that is never a sentence of the article (issues #241,
# #248): its text alternative, its long description, its identifier, and its
# copyright and licence block. Nothing inside one reaches a paragraph, a
# caption, a footnote or a table cell.
#
# THESE ACCUMULATED NOWHERE AND HAD NO ARM, so `characters()` appended their
# text to whatever buffer was open above the object. For a `<fig>` or
# `<table-wrap>` deposited inside a `<p>` — Elsevier's house style, and 7,248
# `<table-wrap>` alone sit that way in the served artifact — that buffer is the
# sentence: `'BeforeTable 2after.'`, and PMC10030262 read `'…in Tables
# 2.Table 2Table 3'`. A *wrong* value where a blank is the alternative, which is
# the preference #116 and #162 settled. Measured over the 8,118 served articles
# of `PMC10030002_PMC10040000.xml.gz`, counting elements carrying any text and
# taking the buffer each would have written to as its nearest
# `_TEXT_ACCUMULATING` ancestor, suppressed regions skipped: 4,018 `<alt-text>`
# in 522 articles reached a `<p>`'s buffer and 67 (in 9) a table cell, and into
# a `<p>` beside them 13 `<object-id>`, 5 `<permissions>` and 2 `<long-desc>`.
# Over the 97,909 archive articles of
# `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`: 15,792 `<alt-text>` (in 2,160
# articles) into a `<p>` and 462 (in 49) into a cell, and in any buffer 322
# `<object-id>`, 164 `<permissions>` (111 of them Wiley's `© 2024 WILEY-VCH
# GmbH` on an author photo) and 10 `<long-desc>`. Issue #248's own 3,877 in 537
# articles counted six element types inside a <fig>/<table-wrap> inside a <p>,
# a different predicate. **The `<alt-text>` values are overwhelmingly
# placeholders** — `"Fig. 1"`, `"Table 2"`, `"Image 1"`, `"Multimedia component
# 1"` and a figure's DOI — **and that is not true of every member**: 5 of the 7
# served `<long-desc>` are genuine descriptions, and 5 served `<permissions>` on
# a figure inside a `<p>` are a stock-photo credit (`© 2023 Peter Cade/Getty
# Images`). They are declined all the same, because on `main` they welded into
# the sentence the figure interrupts and a wrong value is not the alternative
# to a blank; whether a figure's credit or description should be *routed* the
# way `<attrib>` is is issue #251.
# Where the object stood in a `<sec>` instead the text went to that section's
# unread buffer; it now goes to the member's own buffer and is discarded there,
# the same outcome, so the ordinary block deposit moves nothing.
#
# **THREE ROUTES REACH THE ARTICLE, AND MEMBERSHIP OF `_TEXT_ACCUMULATING`
# ANSWERS ONE OF THEM.** The buffer isolates every child that *merges* — raw
# character data and an inline run alike — which is #243's argument for a cell
# and answers an arm added later too. It cannot answer a child that *routes*: a
# `<p>` goes through its own arm to `_append_prose` whatever buffer surrounds
# it, and `<license>` was modelled `(p)+` before JATS spelled it
# `<license-p>`, so that method refuses prose under this metadata and
# `_prose_reaches_output` mirrors the refusal. And a table cell is filled from
# `characters()` and from the formula arm directly, bypassing every buffer, so
# both reach the cell through `_offer_cell_text`, which holds this text back.
# The formula half of the third route pins a direction, neither artifact
# depositing a formula in any member. **The second is a population since
# issue #230**: all 19 `<p>` inside a `<permissions>` in the archive artifact
# sit in `<article-meta>`, where the paragraph fell past every branch whatever
# this refusal said — and front matter routes now, so the refusal is what keeps
# each article's licence from being filed among its front-matter paragraphs. The
# `characters()` half of the third is a population too — the 67 served and 462
# archive cells above, which buffer membership does not reach.
#
# **Membership is by what the element *is*, and `<attrib>` is the neighbour it
# excludes.** An attribution is typeset — an interview quote's `"(P2, CP)"`, a
# figure's `"Source: Authors' elaboration."` — so it takes a buffer too but is
# routed rather
# than discarded; see the `</attrib>` arm. `<copyright-statement>`,
# `<copyright-year>`, `<copyright-holder>` and `<license>` are reached through
# the `<permissions>` that JATS requires around them, and no member of that
# family is deposited outside one in either artifact.
#
# **Two ancestors claim the text anyway; see `_TEXT_CLAIMING_ELEMENTS`.** Under
# either one a member's text is kept exactly as `main` kept it, on every route,
# and a formula's image text alternative is kept a third way, as that formula's
# rendition of last resort (see `_FormulaFrame.alt_text`).
_NON_PROSE_METADATA = frozenset({"alt-text", "long-desc", "object-id", "permissions"})

# Elements that own every descendant's text, an object's declined metadata
# included (issues #241, #248).
#
# A `<mixed-citation>` for #146's reason: every descendant is that citation's
# text, as typeset, so a member under one merges back exactly as `characters()`
# delivered it before. An `<object-id>` in a citation is arguably printed and
# an `<alt-text>` is not, and no draw decides between them — both artifacts
# deposit 0. An `<xref>` because it *replaces* its text with a link label: an
# image that is the reference would otherwise leave the label empty and fire
# the arm's `text or "Figure"` fallback, `[Figure 1](#f1)` becoming the
# invented `[Figure](#f1)` — #162's symptom. No member's text lands in an
# `<xref>` in either artifact; found by review, so this pins a direction.
#
# **The exception has to reach every route or it is not the exception.** It
# lived at the buffer pop alone at first, so a table cell — which
# `_offer_cell_text` fills with no buffer between — lost `See Figure 1` to `See`
# where `main` kept it, while four documents said the parse under an `<xref>`
# was exactly `main`'s (PR #250's review). One predicate,
# `_inside_declined_metadata`, now answers for the cell, the prose routes and
# the formula counter; the pop asks the element-local form of the same rule.
# `<attrib>` is claimed too, being routed rather than declined but otherwise the
# same kind of child: routed under an `<xref>` it brought back the invented
# label.
_TEXT_CLAIMING_ELEMENTS = frozenset({"xref", "mixed-citation"})

# The children whose own buffer a `_TEXT_CLAIMING_ELEMENTS` ancestor takes back
# at the pop, where outside one the buffer is discarded or routed.
_CLAIMABLE_ELEMENTS = _NON_PROSE_METADATA | {"attrib"}

# What separates a definition's term from the definition itself (issue #228).
#
# A `<def-list>` pairs a `<term>` with a `<def>`, and this module models no
# definition list: the `<def>`'s `<p>` routes as ordinary prose, exactly as a
# `<list-item>`'s does, while the `<term>`'s buffer was popped and discarded —
# so an abbreviations list arrived as definitions with no words defined. The
# term is folded into the definition's own paragraph rather than modelled,
# which is what this module already does with a `<list>` and the shape #124
# proposes for a footnote marker, so one answer serves three containers
# instead of three public fields.
#
# An em dash rather than a colon, and spaced. **Measured on the terms
# themselves** (whole-document walks of the two named artifacts, so unscoped
# and wider than what the parser sees): a term is free text — 13 of 14,177
# served and 240 of 153,388 archive terms run past ten words — and it carries
# its own punctuation, **174 served (1.2%) and 1,597 archive (1.0%) containing
# a colon, 164 and 1,401 ending in one**. So a colon separator cannot be told
# from the term's own text, which is the collision this choice avoids.
#
# The spaced em dash does not collide at all: **0 of 14,177 and 0 of 153,388
# terms contain `" — "`**. Five archive terms carry a bare em dash and none
# carries the spaced form, so the separator is recoverable by a reader who
# already knows the paragraph is a definition — which is the whole of what it
# promises; see `docs/manual/fulltext.md` on why the fold is one-way.
#
# A first cut argued this from `tests/data/funder_names.json`, a
# `transparency` corpus of *funder organisation names* — the wrong population
# in the position this module reserves for a rule's evidence, and #158's own
# complaint (PR #236's review). The two `<term>` corpora were already in hand.
_DEFINITION_SEPARATOR = " — "

# Parents a <disp-formula> merges into rather than standing beside as its own
# paragraph.
#
# A large minority of display formulas sit inside a <p>. Emitted as its own
# paragraph, every one of those would be appended *ahead* of the paragraph it
# interrupts, because the enclosing <p> has not closed yet. The rest are block
# children — chiefly a <sec> directly, and <app>, <boxed-text>,
# <disp-formula-group>, <body> and <disp-quote> beyond that — where there is no
# open prose to join and a paragraph of its own is the only way the equation
# reaches the article. Both parents are routed, so the rule does not turn on
# which is commoner; the share is recorded because it sizes what merging is for.
#
# HOW LARGE THAT MINORITY IS DEPENDS ON THE RENDITION, AND THE FIRST STATEMENT
# OF IT CITED THE WRONG ONE. Over the *archive* bytes of the whole
# `PMC012xxxxxx` package it is 116,623 of 150,598 (77.4%), with 33,270 directly
# in a <sec> — a majority, and it was written down here as though it described
# what this parser is fed. It does not: on Europe PMC's `fullTextXML`, which is
# the rendition `FullTextService` hands over, the committed recent corpus
# measures **714 of 1,915 (37.3%)** in a <p> against **1,199 in a <sec>**, and
# the 880-article served draw measures 201 of 654 (30.7%). The two served
# measurements agree with each other and the archive is the outlier, so a <p>
# is the *minority* parent on the bytes that reach this code. Measured with
# issue #164, whose sentinel fix is what let these counters reach a corpus at
# all — they had been registered as a generation and omitted from the loader,
# so both corpora read them as a measured zero.
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


# Where the article's own metadata is deposited, outermost first: the owner
# paths `_JATSHandler._owned_by` tests the metadata arms against (issues #254,
# #259, #152). Instrumented at the arms over both artifacts — the 8,118 served
# articles of `PMC10030002_PMC10040000.xml.gz` and the 97,909 of
# `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`, nested articles and
# references aside — every value that is the article's own arrives at one of
# these paths. What arrived anywhere else inside <article-meta> was another
# work's (a <related-article>, a <product>, a citation in abstract prose) or one
# of the article's own <history>/<pub-history> dates, which is not its
# publication year.
_ARTICLE_META = ("front", "article-meta")
_JOURNAL_META = ("front", "journal-meta")

# The wrappers the JATS 1.3 Tag Library places each value in inside its
# container, as paths below it ("May be contained in", checked element by
# element). A value is also admitted bare in the container: see
# `_JATSHandler._in_own_metadata`. `<fpage>`, `<lpage>` and `<article-id>` have
# no wrapper there, so they test the container alone. `<related-article>`,
# `<related-object>` and `<product>` hold `<article-title>`, `<year>`,
# `<volume>`, `<issue>`, `<fpage>` and `<lpage>` too (not `<article-id>` or
# `<journal-title>`), and are not wrappers of the article's own — they are the
# other works.
_TITLE_WRAPPERS = (("title-group",),)
# A <string-date> is legal inside <pub-date> and admits <year>.
_YEAR_WRAPPERS = (("pub-date",), ("pub-date", "string-date"))
# An article published across several issues groups each pair (JATS 1.1+).
_VOLUME_ISSUE_WRAPPERS = (("volume-issue-group",),)
_JOURNAL_TITLE_WRAPPERS = (("journal-title-group",),)

# The declared <pub-date> types that name no publication at all (issue #261).
# PMC deposits two: `nihms-submitted`, the day an author manuscript reached
# NIH, and `pmc-release`, the day PMC's embargo lifts — neither a date this
# article was published, and the year arm being first writer, either could be
# the stored year. It is a **suffix** and not those two names because the
# attribute is CDATA and the vocabulary is open: over the article's own
# <pub-date> elements in the four named artifacts the whole of it is `epub`,
# `collection`, `pmc-release`, `ppub`, `pub`, `nihms-submitted`, `epreprint`,
# `ecorrected`, `epub-ppub`, `preprint` and `update`, and those two are the
# only members the suffixes reach — so the rule is narrow by measurement as
# well as by intent.
#
# Everything else is kept, including `epreprint` and `update`: they name a
# publication of some kind, and *which* publication date the year should be —
# the electronic one or the issue's — is the question issue #261's decision
# (option 3, 2026-09-15) deliberately leaves open, filed as issue #273: the
# stored year is the issue's in almost every back-filled article (99.4% and
# 99.8%) and an electronic date in most recent ones (82.6% archive, 63.8%
# served), since document order is a deposit convention rather than a
# property of the field. Those four shares are **definition-dependent** and
# the definition is #273's: `ppub` and `collection` are the issue's date and
# every other member is electronic, `date-type="pub"` included whatever its
# `@publication-format` (11 served and 470 archive declare it `print`, which
# is why that cut has to be stated). Counting only the `epub` family instead
# gives 57.9% and 73.5% — a different question, not a correction.
_NON_PUBLICATION_DATE_SUFFIXES = ("-submitted", "-release")


def _names_a_publication_date(declared_type: str | None) -> bool:
    """Is a ``<pub-date>`` declaring ``declared_type`` a date of publication?

    ``None`` is a date that declared no type, which is not a refusal: the
    article deposited it as its publication date and named no other kind.

    Case is folded, as this module folds ``pub-id-type`` and ``contrib-type``
    and as the Tag Library recommends for ``@article-type`` — precedent here
    rather than citation, the recommendation being written of that attribute.
    An unfolded comparison costs the article a correct year, while no casing
    of an accepted type is ever refused by folding.

    Args:
        declared_type: The ``@pub-type`` or ``@date-type`` the date declared.

    Returns:
        True unless the type ends in one of
        :data:`_NON_PUBLICATION_DATE_SUFFIXES`.
    """
    if not declared_type:
        return True
    return not declared_type.strip().lower().endswith(_NON_PUBLICATION_DATE_SUFFIXES)


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
        self.elocation_id = ""
        # Does the article's page range still want its <lpage>? True from the
        # moment an <fpage> writes `pages` until an <lpage> completes it, so a
        # second <lpage> cannot append to a range that is already closed
        # (issue #272's review). Bookkeeping rather than routing state, and
        # legitimately True at the end of an article paginated by an <fpage>
        # with no <lpage> — so it is named in `TestTheAuditNetIsComplete`'s
        # `_NOT_ROUTING`, not in the audit.
        #
        # That shape is legal JATS and measures **0 articles** on all four
        # artifacts: every article depositing an <fpage> at the owner path
        # deposits an <lpage> too (served 3,235 of 3,235, archive 15,971 of
        # 15,971). So the exclusion pins a direction rather than sparing a
        # measured population, and the structural argument is what it rests
        # on — the empirical half read as a common shape until PR #274's
        # review measured it. The decision is still pinned: this class's own
        # <fpage>-repeat fixtures end with the flag True, so auditing it
        # reddens them through the autouse `parser_log` fixture.
        self.page_range_awaits_last_page = False
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
        # for the two reasons above. `_append_prose` has five branches and no
        # fallthrough, so a rendition reaching none of them is built and
        # dropped. Not a regression: `main` discarded the whole element. That
        # is exactly why it is counted rather than left — the parser now
        # builds the string, so losing it silently is a new kind of quiet.
        #
        # **This counts a routing gap, never a refusal.** The `<back>` shape
        # it used to name — 192 formulas in 23 of the PMC012xxxxxx package's
        # 97,909 articles — reaches the article as of issue #224 and is gone
        # from here. A formula refused as bibliography apparatus is a
        # *decision* and goes to `refused_apparatus_prose` instead, or the
        # WARNING would report a policy this module chose as a gap in it.
        #
        # **Two shapes are left, and naming only one understates what #177 is
        # sized by.** A formula inside a float with no <caption> open, 0
        # measured in both committed corpora; and one standing outside
        # `<front>`, `<body>` and `<back>` altogether — a `<floats-group>`'s
        # `<boxed-text>`, or a `<sec>` inside it (issue #253) — which is not
        # measured for formulas at all. Both are latent here rather than
        # confirmed. Front matter was a third until issue #230 routed it,
        # and routing it moved this counter in 0 of the 8,118 served and 0
        # of the 97,909 archive articles — so no standalone display formula
        # stood in front-matter prose outside an abstract or a float, the two
        # front positions whose answer routing front matter did not change
        # (an abstract's formula is filed on both sides, and a float's
        # reaches this counter on both where it has nowhere to go).
        #
        # **And it does not catch every rendered-then-lost formula**, which
        # the paragraph above would otherwise imply: a `<disp-formula>` whose
        # parent is in `_DISPLAY_FORMULA_MERGE_PARENTS` is merged into that
        # parent's buffer and never reaches the standalone arm, so a formula
        # inside a dropped `<p>` — a `<floats-group>`'s, or a float's with
        # no <caption> open and no footnote container above it — is lost by
        # the `<p>`'s own route and counted by neither counter. That is issue
        # #233, which named front matter among its (unmeasured) shapes until
        # #230 routed it.
        self.formulas_dropped = 0
        # Prose refused by the `<ref-list>` rule in
        # `_unsectioned_prose_is_the_articles`, counted so `_audit_parse`
        # reports it once per article at WARNING.
        #
        # **A deliberate refusal is exactly the drop no reader can otherwise
        # see.** On `main` this prose was incidental collateral of a branch
        # gated on `in_body`; here it is named and argued, which earns it a
        # line rather than excusing one — the rule `rejected_spans` settled
        # for #129 and `formulas_dropped` for #177, and issue #150 is the
        # downstream that cannot learn the content existed without it.
        # WARNING and not ERROR for `rejected_spans`' reason: a publisher's
        # deposit reaches it, so it cannot mean "bmlib is wrong".
        #
        # It counts both content kinds the refusal takes, and counts each of
        # them **once**: `_append_prose` is the only site that increments it,
        # reached by a `<p>` directly and by a `<disp-formula>` through the
        # standalone arm, which subtracts the refusal from `formulas_dropped`
        # rather than counting it again. A first cut incremented at both, so
        # one formula reported as two — the counter this change added to size
        # a loss, over-reporting it.
        self.refused_apparatus_prose = 0
        # The open <def-item> elements, innermost last (issue #228). Each
        # holds the <term> this parser has read and not yet filed, and the
        # exhibit depth at which the item opened; see `_DefinitionFrame` for
        # why it is a stack, why the term is never the empty string, and what
        # the depth is compared against.
        #
        # The frame is pushed after `startElement`'s suppression return and
        # popped under `endElement`'s matching guard, so the two stay balanced
        # across a nested article's region: unbalanced, a stranded term
        # prefixes the host article's next paragraph with a reviewer's word.
        # `open_definition_items` is the audit's field for exactly that.
        self.def_item_stack: list[_DefinitionFrame] = []
        # Headings a container deposited for its own unsectioned prose, each
        # holding the depth of the element that owns it (issue #231). See
        # `_HeadingFrame` for why it is a stack, and `_recover_container_heading`
        # for the rule. Pushed and popped under the same suppression guards
        # `def_item_stack` is, so a nested article's own <ack> heading cannot
        # strand a frame and title the host article's next run with it;
        # `open_container_headings` is the audit's field for that.
        self.heading_stack: list[_HeadingFrame] = []
        # Terms this parser read and could not file, counted so `_audit_parse`
        # reports them once per article at WARNING — the granularity and the
        # level `rejected_spans` settled for #129 and `refused_apparatus_prose`
        # for #224.
        #
        # Folding the term into its definition's paragraph files it wherever
        # that paragraph routes; where the paragraph routes *nowhere* the pair
        # is lost together, and the term's half is the one no reader could
        # otherwise see.
        #
        # 14,174 terms are folded over the 8,118 served articles and 153,226
        # over the 97,909 archive ones; 3 and 23 are dropped, in 1 and 7.
        # **The three counts close on both**: fold plus drop equals the terms
        # carrying a word, 14,186 − 9 empty served and 153,256 − 7 archive, so
        # this counter and the fold partition the population rather than
        # sampling it.
        #
        # Those are the **post-#230** figures, re-measured on this revision
        # rather than derived: front matter routes now, so a front-matter
        # definition list is folded where it used to be dropped. The split
        # moved twice and was measured each time — 12,667 / 1,510 served and
        # 142,855 / 10,394 archive before #124 made an exhibit's footnote a
        # destination, 12,733 / 1,444 and 143,781 / 9,468 between #124 and
        # #230. Deriving a new fold by adding the known move to the old one is
        # exactly what `docs/DECISIONS.md` tells a reader not to trust here —
        # at #124 it would have been wrong by 14, which is what the closure
        # caught (PR #237's review).
        #
        # **The partition is structural and not a property of the draw.**
        # Until PR #236's review it closed only because neither corpus
        # deposits a `<term>` outside a `<def-item>`: such a term reached
        # neither side and was discarded in silence, a third outcome the word
        # "partition" denied. The `<term>` arm counts it now, so every term
        # carrying a word is either folded or counted whatever its parent.
        #
        # **Measured at the drop rather than inferred from the markup**, since
        # a <front><abstract>'s definition list would be *folded* into the
        # abstract and a region walk cannot tell that from a drop. **Two rows
        # have left this counter, and what is left is one population.** The
        # 66 in a <body> float with no <caption> open, dropped as exhibit
        # furniture, are folded and filed since #124 made a footnote a
        # destination; the 1,441 served terms in <front> (9,445 archive) since
        # #230 routed front matter. The 3 served and 23 archive left are
        # **exactly the <def-item> carrying no <def>, per article** — counted
        # on the same parse, 0 articles disagreeing on either artifact — so
        # the identity an earlier draft of this comment could only call "a
        # coincidence of counts, not a checked identity" is now checked, on
        # these two artifacts. The shapes that would reach it with a <def>
        # present — a definition in a <floats-group>'s <boxed-text>, or in a
        # float with nowhere to put it — have no drop left over for them on
        # either artifact, which is why
        # `test_a_term_whose_definition_reaches_nothing_is_counted` pins the
        # first rather than leaving the counter's routing half untested.
        #
        # **Scoped to a <term>, and the shared label-or-term counter #228's
        # own comment proposes is refused on measurement.** An unfiled
        # <label> — one whose owner is not a formula, a <fig>, a <table-wrap>
        # or a <ref> — reaches 6,225 of the 8,118 served articles of
        # `PMC10030002_PMC10040000.xml.gz` (76.7%) and 86,516 of the 97,909
        # archive articles of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`
        # (88.4%), where each of this counter's three siblings fires on a
        # small minority. A line on three articles in four is noise, and the
        # owners are at least four separate questions: an <aff>'s marker
        # (23,077 served — 25,332 with <corresp>, which is the same row under
        # the wider scope and is how the docs state it; the two figures
        # differing by exactly <corresp>'s 2,255 was read as a contradiction
        # in PR #236's review, so both now name their element set), a numbered
        # <sec>'s own number (19,462), a <list-item>'s bullet (7,351) and a
        # footnote marker (5,891, which is #124's).
        #
        # **"At least" is meant literally**: those four leave ~4,190 of the
        # 62,226 unaccounted, the largest single remainder being a
        # <supplementary-material>'s own label — 2,998 served and 42,901
        # archive, half again the footnote count and comparable to <corresp>,
        # named by no issue (whole-document walks, so unscoped). Filed with
        # the owner table rather than pooled here.
        #
        # It counts a *word*, never an element: an empty <term> adds no prefix
        # wherever it lands, so counting one would report a loss the document
        # never deposited.
        self.definition_terms_dropped = 0
        # A footnote marker read for an <fn> that then deposited no prose to
        # fold it into (issue #124). Counted for `rejected_spans`' reason: the
        # marker is given back rather than carried, so nothing in the output
        # shows that the document numbered a note bmlib could not file.
        #
        # The population is small — 1 of the 10,763 <fn> inside an exhibit
        # across the 8,118 served articles of `PMC10030002_PMC10040000.xml.gz`,
        # and 11 of 137,735 in the 97,909 archive articles of
        # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` — so this is a
        # direction rather than a rate, which is the standing #235's
        # measurement denied a counter with the opposite shape.
        #
        # It counts a *marker*, never an <fn>: a note deposited with no label
        # at all is not a loss, and 7,661 of the 10,763 served <fn> are that.
        self.footnote_markers_dropped = 0
        # A footnote block's own <title>, and a <graphic> owned by an exhibit's
        # footnote matter (issue #238). Each is dropped by a rule this module
        # argued for — the <title> owner rule (#125, #130), bmlib modelling no
        # container that carries a heading, and `_graphic_owner`'s opacity
        # (#127), which is what stops a nested supplement's image being
        # donated to the figure enclosing it — and once #124 made the block a
        # destination they were the two things in it leaving no trace beside
        # its own <label>, which is #235's. Counted for
        # `refused_apparatus_prose`'s reason: a drop the module chose earns a
        # line rather than excusing one. Folding the heading in as the block's
        # lead is refused — `docs/DECISIONS.md` has the argument. An empty
        # deposit — `<title/>`, a `<graphic/>` with no href — costs nothing on
        # either, the rule every sibling counter makes: nothing was read, so
        # the line would state a loss that did not happen.
        #
        # The second counts *deposits*: an <alternatives> pair is one image in
        # two encodings and reads 2, the unit the deposit survey counts, and
        # its line says so.
        #
        # Both measure **0 over the 8,118 served articles** of
        # `PMC10030002_PMC10040000.xml.gz`, the rendition bmlib is fed, and a
        # handful over the 97,909 archive articles of
        # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` — the counters
        # themselves read 7 headings in 4 articles and 7 deposits in 4 — so
        # each pins a direction on the served bytes and a population on the
        # archive.
        self.footnote_headings_dropped = 0
        self.footnote_graphics_dropped = 0
        # A <td>/<th> whose text reached no table (issue #245). <array> is
        # JATS's *non-floating* tabular structure — tabular markup with no
        # <table-wrap> wrapping it — and bmlib models none of it, so no
        # `_TableBuilder` is open and `append_cell_text` has nowhere to put
        # the text. Defined by the wrapper's absence and not by the absence of
        # a <table>, which is what the arm actually tests: JATS admits a
        # <table> inside an <array>, and such a cell reaches this counter too.
        #
        # Until #243 that text still reached the buffer above: the enclosing
        # <sec>'s, where it was discarded, or the enclosing <p>'s, where it
        # was spliced into the sentence as a run-together string the publisher
        # never wrote. Isolating the cell's own buffer makes the loss total
        # for the second shape too, which is the right direction by this
        # module's standing preference — a blank beats a wrong value (#116,
        # #162) — and is what earns the counter: a drop this module argues for
        # gets a line rather than an excuse, `refused_apparatus_prose`'s rule.
        #
        # Measured over two named public artifacts: 355 cells in 8 of the
        # 8,118 served articles of `PMC10030002_PMC10040000.xml.gz` — 173 of
        # them in 3 articles inside a <p>, the only shape where the loss was
        # visible, and the other 182 in 5 articles inside a <glossary>, where
        # they were already being discarded — and 248,720 in 6,726 of the
        # 97,909 archive articles of
        # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`. Every one is an
        # <array>'s on both. So in five of those eight served articles the
        # counter reports a loss that is **pre-existing and was silent**.
        #
        # **It is keyed on no builder being open, which is narrower than "no
        # table received this cell".** An <array> deposited *inside* an open
        # <table-wrap> — in its <caption>, its <table-wrap-foot> or one of its
        # own cells — routes into that builder instead, splicing a phantom row
        # into a table the publisher never wrote that way and taking the
        # silent branch here. That is pre-existing and is filed rather than
        # fixed; it measures 0 of 8,118 served and 0 of 97,909 archive
        # articles, so the "every one is an <array>'s" above is a statement
        # about what this arm has seen and not about where an <array> can sit.
        #
        # The unit is the *cell*, never the character (PR #239's review's rule
        # for #238's image counter), and a cell that carried nothing costs
        # nothing, which is every sibling counter's rule.
        self.cell_text_dropped = 0
        # An <attrib> whose prose reached nothing (issues #241, #248). An
        # attribution is printed content and is routed where a <p> would be —
        # so it reaches nothing exactly where a <p> does: inside a float, owned
        # by an element this module does not model (a <supplementary-material>
        # or <boxed-text> in a <fig>), with no <caption> open and no footnote
        # container above it, and in a <floats-group>'s <boxed-text>, which
        # sits in none of <front>, <body> and <back> (issue #253). (Front matter was on this
        # list until issue #230 routed it, and an attribution there is now
        # filed as a <p> is.) Before #241 it welded
        # into the sentence around an inline float; it is a blank now, the
        # module's standing preference, and the blank is counted where the
        # <p> beside it is not — `formulas_dropped`'s precedent for a newly
        # routed kind of content (issue #177), since what was routed *for the
        # first time* is what nobody could otherwise see going. An attribution
        # claimed by an <xref> or a <mixed-citation>, declined with the
        # metadata around it, left in a cell, or refused as bibliography
        # apparatus is not a loss this counter owns. Measured by the counter
        # itself: 0 over the 8,118 served articles of
        # `PMC10030002_PMC10040000.xml.gz` and 0 over the 97,909 archive ones
        # of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`, so it is wholly
        # prospective.
        self.attributions_dropped = 0
        # A reference's own <elocation-id> part that did not continue the
        # locator before it, so `JATSReferenceInfo.elocation_id` keeps the
        # first (issue #265, PR #269's review). Its text is still in
        # `citation` for a <mixed-citation>, where the element is inline, but
        # an <element-citation> writes no `citation`, and there the part is in
        # no public field. Counted because it is the one drop in that arm the
        # module chose — `refused_apparatus_prose`'s rule — while a repeat of
        # the whole locator loses nothing and an empty part reads nothing, so
        # neither counts. The unit is the part. Measured 0 over the 8,118
        # served articles of `PMC10030002_PMC10040000.xml.gz` and the 97,909
        # archive ones of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`, so it
        # is wholly prospective.
        self.elocation_parts_dropped = 0
        # An <lpage> of the article's own that completed no open page range,
        # so its page number is in no public field (issue #272, PR #274's
        # review). Counted for `elocation_parts_dropped`'s reason one arm
        # over: the arm refuses a value the document deposited, the refusal
        # is one this module argued for, and <lpage> is not inline, so unlike
        # a refused locator part nothing else carries the text. Two shapes
        # reach it — a second <lpage> extending a range its predecessor
        # closed, and an <lpage> with no <fpage> before it at all — and the
        # line names neither, the arm seeing only that no range was open. The
        # unit is the element. An empty <lpage/> deposits no page number, so
        # it reads nothing and counts nothing, every sibling's rule. Measured
        # 0 over the 8,118 served articles of
        # `PMC10030002_PMC10040000.xml.gz` and 0 over the 97,909 archive ones
        # of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`, so it is wholly
        # prospective.
        self.last_pages_dropped = 0
        # A <year> the article deposited in a <pub-date> naming no publication
        # (issue #261). The whole arm short-circuits once a year is stored, so
        # this counts the refusals made **before one was found** and not every
        # refused date in the document — which is what the line below needs,
        # and is narrower than the attribute's name suggests. Counted at the
        # refusal and reported by `_audit_parse` **only where the article ends
        # with no year at all**, which is what the refusal can cost. A line
        # per refusal would instead fire wherever a refused date is merely
        # deposited *first* — 1,105 of the 8,118 served articles (13.6%,
        # about one in seven: 1,047 taking today's year from a `pmc-release`
        # date and 58 from a `nihms-submitted` one) — which is a line about
        # no loss at all, the reason the shared counter #228's comment
        # proposed was refused on measurement by #235. Measured 0 articles
        # losing their year over all four artifacts — the 8,118
        # served of `PMC10030002_PMC10040000.xml.gz`, the 97,909 of
        # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` and the 3,028 and
        # 27,515 of its `PMC000xxxxxx` and `PMC001xxxxxx` siblings, every one
        # of which deposits another dated <pub-date> — so it is wholly
        # prospective.
        self.non_publication_years_refused = 0
        self.current_article_id_type: str | None = None
        # The type the open <pub-date> declared, read at its start tag because
        # the <year> arm fires at the year's own close (issue #261). One slot
        # and not a stack: <pub-date> admits only date parts (JATS 1.3
        # `(day | era | month | season | year | string-date | x)*`), so the
        # content model does not let it nest, and the Tag Library contains it
        # in <article-meta>, <front-stub>, <event> and <event-desc> alone.
        # Cleared at `</pub-date>`, which is what stops a refused type judging
        # the dates after it.
        #
        # **Expat enforces well-formedness, not the content model**, so a
        # document nesting one anyway is delivered, and the inner close then
        # clears the outer's type and the refusal is defeated — the shape that
        # bit `<contrib>` (#120), `<caption>` (#130) and the exhibits (#115),
        # each of which nests *legally*. Accepted here rather than fixed with
        # a stack, on the precedent of `current_article_id_type` and
        # `current_xref_type` beside it, whose elements do not nest either;
        # the cost is one article's year, and no artifact deposits the shape
        # (0 nested <pub-date>, <article-id> or <xref> across all four).
        # Filed as issue #275 for the class of four rather than fixed for the
        # one this PR added, since fixing the newest alone would leave three
        # older slots with the same exposure. Stated so the hazard is on the
        # record rather than denied (PR #274's review).
        self.current_pub_date_type: str | None = None

        # Abstract state
        self.in_abstract = False
        self.current_abstract_title = ""
        self.current_abstract_text: list[str] = []

        # Body / back state
        self.in_body = False
        self.in_back = False
        self.section_stack: list[_SectionBuilder] = []
        # <sec> is optional inside <body> — and inside <back>, issue #224 —
        # so prose can arrive with an empty section_stack. It is collected
        # here and flushed to body_sections at the next <sec> or at the
        # container's own close, rather than pushed onto section_stack: a real
        # <sec> opening afterwards would otherwise nest inside it.
        #
        # **A slot per container, and the second one is what the audit needs.**
        # One slot would serve for output, `</body>` flushing before `<back>`
        # opens in any DTD-valid document — but it would also *launder* a
        # missing `</body>` flush: the slot survives into `<back>`, collects
        # acknowledgement prose behind the body's own, and `</back>` empties it,
        # so the article silently loses the boundary between the two and the
        # end-of-parse audit sees nothing stranded. That is a real narrowing,
        # since **at least** 73.8% of the served corpus carries a `<back>`:
        # that figure is the share *gaining prose* (see
        # `_unsectioned_prose_is_the_articles`), so a `<back>` holding only a
        # `<ref-list>` and sections is outside it and the true share is
        # higher — a lower bound, not the population, which is this repo's
        # own "a count is of what you looked for" one comment down. And it is
        # the shape this module keeps being caught by: a single slot where the
        # state is per-container. Two slots make the loss structural instead —
        # the body slot can only be emptied by `</body>`, so a defect there
        # strands it and `_ROUTING_FLAGS` reports it.
        #
        # **`<front>` has the third, for the same reason** (issue #230). Its
        # prose is the article's too — author notes, competing-interest
        # statements, data availability — and sharing the body's slot would
        # let a missing `</front>` flush ride into `<body>` and weld the two,
        # which is the laundering above one container further out.
        self.implicit_body_section: _SectionBuilder | None = None
        self.implicit_back_section: _SectionBuilder | None = None
        self.implicit_front_section: _SectionBuilder | None = None
        # Prose found inside <body>. Counted separately from body_sections
        # because back-matter and (since issue #230) front-matter sections land
        # there too, so a non-empty body_sections does not by itself mean the
        # article has a body.
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
        # 1,997 articles, all seven in **one** article — eLife's PMC12143881,
        # 7 of its 19 figures (`scripts/sample_jats_exhibits.py`, issue #138).
        # So one article in 1,997 is the whole population, and it is the
        # publisher the shape was always attributed to: a house style costing
        # about a third of *its* figures, not a general convention. eLife's
        # PMC8754430, where the issue came from, is the same shape outside any
        # committed corpus. Read the 1-in-1,997 as a property of which
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
        # Over the two committed draws (1,997 articles,
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
        "in_abstract",
        "in_body",
        "in_back",
        "in_ref_list",
        "in_ref",
        "in_ref_citation",
        "in_ref_person_group",
        "current_reference",
        "current_article_id_type",
        "current_pub_date_type",
        "current_xref_type",
        "current_xref_rid",
        # A single slot each: unsectioned `<body>` prose accumulates in the
        # first and unsectioned `<back>` prose in the second, each emptied by
        # its own container's close (issue #224).
        # Left stranded, the article loses that prose outright — and for
        # `<body>` prose `has_body` stays True, because
        # `body_paragraph_count` already counted it, so it is a silent loss of
        # a whole body in the shape this audit exists to catch.
        #
        # **Both are listed because a slot per container is what makes either
        # detectable.** Sharing one slot, `</back>`'s flush empties whatever
        # `</body>`'s failed to, so a stranded body slot unwinds clean and
        # this net says nothing — the audit narrowed by exactly the share of
        # documents that carry a `<back>`. See the slots' own comment.
        "implicit_body_section",
        "implicit_back_section",
        # Front matter's, from issue #230, emptied ahead of each front <sec> and
        # at `</front>`. Nothing after `</front>` flushes it, so stranded it is
        # prose lost with no other symptom — `has_body` never counted it.
        "implicit_front_section",
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
            open_definition_items=len(self.def_item_stack),
            open_container_headings=len(self.heading_stack),
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

    def _graphic_owner(self, *, closing_child: bool = False) -> str:
        """The element a ``<graphic>`` belongs to.

        The walk starts one above the ``<graphic>`` and skips only the wrappers
        that do not take ownership (:data:`_GRAPHIC_TRANSPARENT_WRAPPERS`).
        Asked from two positions: while the ``<graphic>`` is being opened,
        ``element_stack[-1]`` is the ``<graphic>`` itself; while a direct child
        of it is closing — the ``<attrib>`` crediting the image (issues #241,
        #248) — that child is ``[-1]`` and the ``<graphic>`` is ``[-2]``. One
        walk for both, since an image's credit belongs to whatever owns the
        image and two spellings of that rule would be two things to keep in
        step.

        Args:
            closing_child: ``True`` when the element atop the stack is a child
                of the ``<graphic>`` rather than the ``<graphic>`` itself.

        Returns:
            The owning element's name, or ``""`` if there is none.
        """
        above_graphic = self.element_stack[:-2] if closing_child else self.element_stack[:-1]
        for name in reversed(above_graphic):
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

    def _inside_declined_metadata(self) -> bool:
        """Is text arriving here an object's metadata that this module declines?

        An ancestor test that *includes* the element atop the stack, since its
        callers — ``_offer_cell_text``, ``_append_prose``,
        ``_prose_reaches_output``, the ``<disp-formula>`` counter and the
        ``<alt-text>`` and ``<attrib>`` arms — want the answer for text arriving
        inside that element: at ``characters()`` the innermost open element may
        be the ``<alt-text>`` itself, and at a ``<p>``'s close the ``<p>`` is
        atop the stack with the ``<license>`` and ``<permissions>`` above it.
        See :data:`_NON_PROSE_METADATA` for the three routes it answers two of.

        **Walked from the root, and the first element that decides wins.** A
        member of :data:`_TEXT_CLAIMING_ELEMENTS` met first means the metadata
        below it is that element's text, kept wherever it goes; a member of
        :data:`_NON_PROSE_METADATA` met first means everything below it is
        declined, an ``<xref>`` inside a licence included. Asking "is a member
        open?" and "is a claimer open?" as two ``any`` tests would get the
        second shape wrong, keeping a licence's cross-reference text because a
        claimer sat somewhere on the stack.

        Derived from ``element_stack`` rather than kept as a depth, so a
        stranded region is already visible to the audit as the unbalanced
        element stack it would be, and ``TestTheAuditNetIsComplete`` gains no
        field to cover — the choice #124 made for its footnote routing.

        Returns:
            ``True`` when a member of :data:`_NON_PROSE_METADATA` is open with
            no member of :data:`_TEXT_CLAIMING_ELEMENTS` above it.
        """
        for element in self.element_stack:
            if element in _TEXT_CLAIMING_ELEMENTS:
                return False
            if element in _NON_PROSE_METADATA:
                return True
        return False

    def _inside_text_claiming_element(self) -> bool:
        """Is the element now closing a descendant of an ``<xref>`` or a citation?

        The element-local half of :meth:`_inside_declined_metadata`, asked at
        the buffer pop and in the ``<attrib>`` arm, where the question is
        whether *this* element's text belongs to an ancestor that claims it —
        see :data:`_TEXT_CLAIMING_ELEMENTS`. A strict slice for
        :meth:`_inside_mixed_citation`'s reason: the closing element is still
        on the stack, and neither claimer is a member of the sets this is
        asked for, so the slice is prospective rather than load-bearing.

        Returns:
            ``True`` when a member of :data:`_TEXT_CLAIMING_ELEMENTS` is open
            strictly above the element being closed.
        """
        return any(element in _TEXT_CLAIMING_ELEMENTS for element in self.element_stack[:-1])

    def _inside_table_cell(self) -> bool:
        """Is the element now closing text that a table cell already holds?

        A cell's text is filled by ``characters()`` directly (issue #243), so
        an arm that routes its element's text elsewhere would print a cell's
        content twice — once in the rendered table and once where it was sent.

        **Walked outward and ended at the first cell or ``<table-wrap>``, and
        not at a ``<fig>``.** ``characters()`` offers text to the innermost open
        *table*, so everything inside a ``<td>`` reaches that cell — a
        ``<fig>`` deposited in the cell included, its caption and any
        attribution its image or an unmodelled child carries. Only a
        ``<table-wrap>`` opened inside the cell takes the text away, being the
        innermost table then. A first cut stopped at a ``<fig>`` too, which
        would have counted an attribution sitting in the cell as one that
        reached nothing (found by mutation). A ``<fig>``'s *own* attribution
        is asked before this, by the caller, and is filed as its note as well.

        An ``<array>``'s cell counts as a cell here although no builder is open
        for it (issue #245): its text is dropped and counted there, and
        routing an element out of it as a paragraph would make one cell's
        content half counted and half filed.

        Returns:
            ``True`` when a ``<td>`` or ``<th>`` is open above the element
            being closed with no ``<table-wrap>`` between.
        """
        for element in reversed(self.element_stack[:-1]):
            if element in _TABLE_CELL_ELEMENTS:
                return True
            if element == "table-wrap":
                return False
        return False

    def _offer_cell_text(self, text: str) -> None:
        """Deliver ``text`` to the open table cell, unless metadata holds it back.

        A cell is filled by two routes and no buffer sits on either:
        ``characters()`` for raw character data, and the formula arm for the
        one rendition it chose (#147). So a test in only one of them leaves the
        other writing an image's ``<alt-text>`` into the rendered table —
        ``'12.3Image 1'``, 67 served elements in 9 articles — and this is the one
        door both go through (issues #241, #248). The builder's own ``in_cell``
        test still decides whether a cell is open at all.

        The test is :meth:`_inside_declined_metadata` and not a bare "is a
        member open?", so an image-only ``<xref>`` in a cell keeps its label:
        the first cut held it back, and ``See Figure 1`` rendered as ``See``
        while the prose route kept ``[Figure 1](#f1)`` (PR #250's review).

        Scanned only once a table is known to be open, because ``characters()``
        is the hottest path in the parser and most of an article is not a
        table.

        Args:
            text: The text for the cell, raw as ``characters()`` delivers it or
                rendered as the formula arm emits it.
        """
        current_table = self.current_table
        if current_table is not None and not self._inside_declined_metadata():
            current_table.append_cell_text(text)

    def _owning_exhibit_footnote(
        self, *, including_self: bool = False
    ) -> _FigureBuilder | _TableBuilder | None:
        """The exhibit whose footnote the element atop the stack sits in, if any.

        A ``<table-wrap-foot>``'s prose is the table's own — the abbreviation
        expansions its cells are unreadable without, and its per-table funding
        and disclosure notes (issue #124). It reaches :meth:`_append_prose`
        through the same branch a cell does, so something has to tell the two
        apart, and this is it.

        **An ancestor walk, answering with whichever it meets first.** Both
        halves are load-bearing and each has its own failure:

        - Stopping at the exhibit keeps prose belonging to *no* exhibit out of
          one. A ``<back><fn-group><fn>`` is the article's competing-interest
          statement and #224 routes it to the article; met from inside a
          figure it would be filed as that figure's note instead.
        - Requiring the footnote container *before* the exhibit keeps an
          exhibit nested inside another's footnote from inheriting it. This is
          the one the sibling Swift port got wrong, and it is worth naming
          because the obvious instrument fails at exactly this shape: routed on
          a parser-wide footnote *depth*, the counter still stands at the outer
          table's depth while an inner ``<table-wrap>`` is being parsed, so the
          inner table's own cell ``<p>`` takes the footnote branch and is filed
          as a footnote — rendered twice, once in the cell and once below it
          (bmlibrarian_lite#173). A depth cannot answer a question about the
          *innermost* exhibit; the walk answers it structurally. That port has
          since **fixed** it — ``inInnermostExhibitFootnote`` is its shipped
          routing and the depth survives only for its unwind audit — so this
          names a defect it shipped once, not one it has (PR #237's review).
        - **A cell ends the walk**, so an ``<fn>`` deposited inside a
          ``<td>``/``<th>`` files nothing. JATS admits one there, and without
          this arm the walk sets ``saw_container`` on that ``<fn>`` and carries
          on outward past the cell to the ``<table-wrap>`` — while
          ``characters()`` has *already* delivered the same text to the cell
          through :meth:`_offer_cell_text`, which withholds only an object's
          declined metadata. The note
          would then be rendered twice, once in the cell and once in the
          footnote block: bmlibrarian_lite#173's own symptom reached by a
          different route, and the exact invariant the ``<p>`` branch at
          :meth:`_append_prose` exists to hold. The module already solves the
          same collision for a formula in a cell by *withholding* the cell
          text; a footnote has no such hold, so the walk refuses instead and
          the cell keeps the one rendition it always had.

        It is therefore neither the parent test this module usually makes
        (``<label>``, ``<caption>``, ``<article-id>``) nor a bare ancestor
        membership test like :meth:`_inside_mixed_citation`, but the shape
        :func:`_graphic_owner` already uses: walk outward and take the first
        element that decides. A ``<p>`` sits inside ``<fn>`` inside
        ``<table-wrap-foot>`` inside ``<table-wrap>``, so no single parent
        names the owner.

        **The nesting population measures 0** — no exhibit opens inside
        another's footnote across either artifact, the 8,118 served articles
        of ``PMC10030002_PMC10040000.xml.gz`` or the 97,909 archive ones of
        ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`` — so the second half pins a
        direction and not a population, which is the standing this module gives
        its other structural nesting rules. What it prevents is silent and
        permanent.

        **``including_self`` is which question is being asked, and both are
        asked.** ``element_stack.pop()`` sits at the very end of
        ``endElement``, so the element now closing is still on the stack —
        and ``element_stack.append(name)`` is the first statement of
        ``startElement``, so at the ``<graphic>`` arm (issue #238) the element
        just opened is on it too, and the strict slice asks the same question
        from the mirror-image end.
        Prose asks *"are my ancestors a footnote of an exhibit?"* and takes the
        strict slice — :meth:`_inside_mixed_citation`'s reason, and costing
        nothing today since none of ``<p>``, ``<disp-formula>`` and
        ``<attrib>`` is a member of either set. ``</fn>`` asks *"which exhibit
        is this footnote's?"*, where the closing element **is** the container:
        a ``<fig><fn>`` has no other, so the strict slice would answer ``None``
        for exactly the shape a figure deposits and the unspent marker would
        go uncounted there.

        Args:
            including_self: Whether the element atop the stack — the one now
                closing, or the one just opened — counts as a footnote
                container in its own right.

        Returns:
            The owning exhibit's builder, or ``None`` when this is not an
            exhibit's footnote matter.
        """
        elements = self.element_stack if including_self else self.element_stack[:-1]
        saw_container = False
        for element in reversed(elements):
            if element in _EXHIBIT_FOOTNOTE_CONTAINERS:
                saw_container = True
            elif element in _TABLE_CELL_ELEMENTS:
                return None
            elif element == "fig":
                return self.current_figure if saw_container else None
            elif element == "table-wrap":
                return self.current_table if saw_container else None
        return None

    # -- Section and caption helpers -----------------------------------------

    def _exhibit_named(self, parent: str) -> _FigureBuilder | _TableBuilder | None:
        """The builder a direct child of ``parent`` describes, if bmlib models it.

        Asked for a ``<caption>`` and for an ``<attrib>`` (issues #241, #248),
        each a direct child of the element it describes — for the image
        credit, of the element owning the image. The parent decides outright —
        the ``<label>`` idiom, one element away. For the caption it
        is exact where "the innermost exhibit open anywhere above" was only
        usually right: ``<boxed-text>``, ``<media>``, ``<supplementary-material>``
        and ``<fig-group>`` all admit a ``<caption>`` too, and inside a
        ``<fig>`` each of them would donate its legend to the figure. No draw
        has found one doing so — see ``caption_stack``, which records that
        population as empty rather than claiming it.

        Args:
            parent: The element enclosing the child.

        Returns:
            The owning exhibit's builder, or ``None`` when the owner is an
            element this module does not model — whose caption or attribution
            is then held by nothing rather than by the wrong thing.
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
        two copies of it are two things to keep in step. ``<article-id>`` has
        since moved to :meth:`_owned_by`, which asks the same question about
        the whole path (issue #152).

        Returns:
            The parent element's name, or ``""`` at the document root.
        """
        return self.element_stack[-2] if len(self.element_stack) >= 2 else ""

    def _owned_by(self, *path: str) -> bool:
        """Is the element now closing enclosed by exactly ``path``, innermost last?

        :meth:`_parent_element` extended outward, for the article's own
        metadata (issues #254, #259, #152). A value is the article's only at a
        fixed place in ``<front>``, and "somewhere inside ``<article-meta>``"
        is not that place: a ``<related-article>``, a ``<product>`` and a
        ``<mixed-citation>`` in abstract prose all nest there and carry the
        same child names, and each used to write its own title, volume, issue
        or pages onto the article. A suffix and not a root-anchored match, so
        a wrapper around ``<article>`` — NCBI efetch's ``<pmc-articleset>`` —
        changes nothing. A nested article's ``<front>`` matches the same
        suffix; its closes are kept off the article by the nested-article
        suppression in :meth:`endElement`, which is tested before any arm.

        Args:
            path: Ancestor names, outermost first, ending with the parent.

        Returns:
            Whether those are the closing element's nearest ancestors.
        """
        return tuple(self.element_stack[-len(path) - 1 : -1]) == path

    def _in_own_metadata(
        self, container: tuple[str, ...], wrappers: tuple[tuple[str, ...], ...]
    ) -> bool:
        """Is the closing element in one of ``wrappers``, or bare in ``container``?

        Each wrapper is one the JATS model places the value in — a title in
        ``<title-group>``, a year in ``<pub-date>`` or its ``<string-date>``,
        a volume in ``<volume-issue-group>``, a journal title in
        ``<journal-title-group>`` — and the value is accepted where the
        depositor omitted the wrapper too. For the volume and issue bare is
        the ordinary form and the group the exception; for the journal it is
        a real spelling (NLM 2.x, and the majority form in the oldest PMC
        back-files); for the title and year it is invalid markup no artifact
        measured holds, admitted because a bare child of the article's own
        ``<article-meta>`` has no other owner to belong to, so leniency there
        costs no wrong value. Every element that *does* belong to another work
        — a ``<related-article>``, a ``<related-object>``, a ``<product>``, a
        citation — is not in the wrapper list, and sits inside that work.

        Args:
            container: The owner path, outermost first (``_ARTICLE_META`` or
                ``_JOURNAL_META``).
            wrappers: Paths below ``container`` the JATS model places the
                value in, each outermost first.

        Returns:
            Whether the value is the article's own.
        """
        return self._owned_by(*container) or any(
            self._owned_by(*container, *wrapper) for wrapper in wrappers
        )

    def _prose_reaches_output(self) -> bool:
        """Whether :meth:`_append_prose` would file this text anywhere.

        The branches below mirror that method's, and are a *predicate* rather
        than a second copy of the routing: it answers where the text goes,
        this answers only whether anywhere. Kept beside it so the two are read
        together — the failure it exists to detect is a branch added to one
        and not the other, which would report a loss that did not happen or,
        worse, stay quiet about one that did.

        The mirror is of the branches that **file** text, so
        :meth:`_append_prose`'s ``<ref-list>`` refusal arm has no counterpart
        here: it counts and files nothing, which is what ``False`` already
        says. Its object-metadata refusal (issues #241, #248) *does* have one,
        the first test below, because it runs ahead of every branch. Which *kind*
        of not-filed a loss was is :meth:`_prose_is_refused_apparatus`'s
        question, asked separately by the callers that need it — the
        ``<disp-formula>`` arm, which reports, and
        :meth:`_prefix_pending_definition_term`, which needs *"filed or
        reported"* as one condition and so asks both.

        Returns:
            ``True`` if the text would be kept.
        """
        if self._inside_declined_metadata():
            # `_append_prose`'s first test, mirrored first so the predicate
            # does not answer True where nothing is filed. Behaviourally it is
            # an equivalent mutant on its own, and each consumer has a
            # different second protection: the definition fold is protected
            # by the refusal running ahead of it, and the <disp-formula>
            # counter subtracts the metadata explicitly. Remove it *and* move
            # the refusal below the fold, and a pending term is spent on a
            # licence paragraph the method then declines. The <attrib> arm
            # asks the declined test before this one for the same reason the
            # counter does.
            return False
        if self.in_figure or self.in_table_wrap:
            # Two destinations since issue #124, and this mirrors both — in
            # the same order, since the branches are asked in that order
            # there. Mirroring only the caption would report a
            # <disp-formula> in a table footnote as a formula that reached
            # nowhere, a line claiming a loss that did not happen.
            if self.caption_stack:
                # `_append_caption_text` keeps text only for an open <caption>
                # whose owner this module models.
                return self.caption_stack[-1] is not None
            return self._owning_exhibit_footnote() is not None
        if self.in_abstract:
            return True
        if (self.in_body or self.in_back or self.in_front) and self.section_stack:
            # `in_back` and `in_front` both decide here — a <ref-list> under a
            # back or front <sec> keeps its apparatus, where the line below
            # would refuse it, so without either flag a formula filed into the
            # section is reported dropped, and an <attrib> or a definition
            # term there is lost outright. Only `in_body` is answered by that
            # line as well, whether or not a section is open, so dropping it
            # alone is an equivalent mutant by construction; it stays so this
            # reads branch for branch against `_append_prose`. `in_front` was
            # equivalent too until the <ref-list> refusal reached <front>,
            # and was recorded as equivalent past that point (PR #256's
            # review), which is why both flags are now pinned:
            # `test_a_formula_under_a_sectioned_reference_list_is_not_reported_dropped`
            # and `test_prose_under_a_sectioned_reference_list_is_filed_whole`.
            return True
        return self._unsectioned_prose_is_the_articles()

    def _prose_is_refused_apparatus(self) -> bool:
        """Whether prose here is refused as bibliography apparatus.

        The ``<ref-list>`` half of :meth:`_unsectioned_prose_is_the_articles`
        asked from the outside, so a loss can be reported as the decision it
        is rather than as a routing gap. Four callers reach the same rule
        from different positions — :meth:`_append_prose`, where the branches
        above have already excluded every other case; the ``<disp-formula>``
        and ``<attrib>`` arms of :meth:`endElement`, where they have not (the
        second after its own exhibit, declined-metadata and cell tests); and
        :meth:`_prefix_pending_definition_term`, which runs ahead of all of
        :meth:`_append_prose`'s own branches but after its object-metadata
        refusal (issues #241, #248) — which is why the guards are
        restated here in full instead of left to the caller. That is a
        position and not an order: both arms ask *before* they call
        :meth:`_append_prose`, so the fold runs after those callers, and
        saying it "runs before any of them" had the sequence backwards
        (PR #236's review). This said three callers until PR #256's review;
        the ``<attrib>`` arm has asked since issue #241.

        It is deliberately narrower than "the prose was not filed". Prose in a
        ``<floats-group>``'s ``<boxed-text>``, which sits in none of
        ``<front>``, ``<body>`` and ``<back>``, and prose inside a float with no
        modelled ``<caption>`` open, are also dropped here and are **not** this
        refusal: the first is a routing gap nobody has decided (issue #253)
        and the second is issue #177's. Answering ``True`` for either would put a claim in
        this module's mouth that it made a choice it did not make. Front matter
        was the first of these, and the largest, until issue #230 routed it.

        **Only the float guard changes an answer, and the other two say what
        they are rather than implying they were measured.** ``in_abstract``
        cannot be the deciding guard: :meth:`_prose_reaches_output` answers
        ``True`` for it one branch earlier unless a float is open too, and
        then the float guard here answers first. ``section_stack`` *is*
        reachable non-empty, which a first draft of this comment denied — a
        ``<sec>`` inside a ``<floats-group>``'s ``<boxed-text>`` leaves it
        populated while ``in_front``, ``in_body`` and ``in_back`` are all
        ``False``, so that method's conjunction does not answer ``True`` and the
        ``<disp-formula>`` arm arrives here with the stack loaded. It costs
        nothing only because ``in_back`` and ``in_front`` are both ``False`` in
        that shape too, so the final line would answer ``False`` anyway. Both
        are kept because this predicate states a rule rather than a position,
        and a new caller would otherwise inherit guards nobody restated — but
        do not delete ``section_stack`` on the strength of an unreachability
        that shape refutes. The shape named here was a ``<sec>`` in ``<front><notes>``
        until issue #230 put ``in_front`` into the conjunction, which files
        that formula and no longer asks this predicate. The float guard is
        genuinely load-bearing and pinned: a formula inside a ``<fig>`` inside
        a refused ``<ref-list>`` is lost to the float branch whether or not the
        refusal exists, so it belongs to #177 and not here.

        Returns:
            ``True`` if this module refuses the text as a ``<ref-list>``'s.
        """
        if self.in_figure or self.in_table_wrap or self.in_abstract:
            return False
        if self.section_stack:
            return False
        return (self.in_back or self.in_front) and not self._unsectioned_prose_is_the_articles()

    def _unsectioned_prose_is_the_articles(self) -> bool:
        """Whether prose arriving with no section open belongs to the article.

        ``<sec>`` is optional in ``<back>`` as well as in ``<body>``, and the
        elements that hold a bare ``<p>`` there are not spare matter:
        ``<ack>``, ``<notes>``, ``<fn-group>``, ``<app>``, ``<glossary>`` and
        ``<bio>`` are where funding acknowledgements and competing-interest
        statements live. The branch was gated on ``in_body`` alone, so every
        one of them was dropped — issue #224, found by a JATS parity check
        against the Swift port, whose own comment names the same consequence.

        **The population is the largest this module has measured, and the
        table is a tally of what this method routes rather than of what a
        walk over the markup finds.** Instrumented at :meth:`_append_prose`
        over the 8,118 served articles of Europe PMC's named OA package
        ``PMC10030002_PMC10040000.xml.gz``, 5,990 (73.8%) gain at least one
        paragraph — 40,342 paragraphs and 5.91 MB of prose. By the ``<back>``
        child that owns them: ``<fn-group>`` 13,650 (in 3,925 articles),
        ``<glossary>`` 10,693 (723), ``<notes>`` 10,286 (2,241), ``<ack>``
        4,892 (4,359), ``<app-group>`` 618 (99), ``<bio>`` 203 (43). The same
        instrument over the 97,909 articles of PMC's
        ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` gives 82,058
        (83.8%) and 541,481 paragraphs — ``<notes>`` 192,002, ``<fn-group>``
        147,635, ``<glossary>`` 113,468, ``<ack>`` 61,319, ``<app-group>``
        24,741, ``<bio>`` 2,316.

        Both sets of rows sum to their own total exactly, which the first cut
        of this table did not: taken from a raw-XML walk it counted paragraphs
        this branch never reaches — whitespace-only ones, and ``<p>`` inside a
        back-matter float — so every row was overstated, the archive column
        was 136 short of the total printed beside it, and so was the refusal
        below. A count is of what you looked for, and here what to look for is
        the routing.

        **``<ref-list>`` is the one refusal, and it is a misfiling rule rather
        than a taste.** A ``<ref>``'s ``<note>`` and a ``<ref-list>``'s own
        ``<p>`` are bibliography apparatus: sampled from that package they
        read *"Faculty Opinions Recommendation"* ten times in one article,
        *"Papers of special note have been highlighted as: ..."*, and bare DOI
        fragments. Appended to ``body_sections`` they become paragraphs of an
        article that never carried them, which is the corruption this module
        prefers a blank to (#116, #162) — and issue #150, which puts a
        note-only ``<ref>`` where it belongs, would then be left with its
        content misfiled instead of missing, and its symptom invisible. It is
        163 paragraphs in 39 of the 8,118 served articles, 0.40% of the
        40,505 this branch is offered, and 1,311 in 293 of the 97,909 archive
        ones, 0.24% of 542,792 — so the refusal costs little; it is also the one
        place this module and the Swift port deliberately differ, so a later
        parity check must not "reconcile" them. It is **counted and reported
        once per article at WARNING** (``refused_apparatus_prose``): a drop
        this module argued for is more deserving of a line than the
        incidental one it replaced, not less.

        **The refusal is scoped to this branch, and that is worth stating
        because "the one refusal" reads wider than it is.** Prose under an
        open ``<sec>`` never reaches here at all, so a ``<ref-list>`` inside a
        ``<back>`` ``<sec>`` keeps its apparatus, and so does one in
        ``<body>``, where ``in_body`` answers first. Both are pre-existing and
        both measure near-empty — 0 apparatus paragraphs in 0 of the 8,118
        served articles, 1 in 1 of the 97,909 archive ones — so this is the
        scope of a rule rather than a hole in it.

        Nothing else is refused. Every other container here already routes
        this way *inside* ``<body>`` — a ``<def-list>``'s ``<def><p>`` in a
        body ``<sec>`` reaches that section today — so refusing one in
        ``<back>`` would make the same markup mean two different things
        depending on where the publisher put it. ``<glossary>`` is routed on
        exactly that argument even though it arrived without its ``<term>``
        (#228) and its section without the heading the publisher deposited
        (#231): those were its defects to fix rather than a reason to drop the
        definition too, and both are since answered — the second by
        :meth:`_recover_container_heading`, which is what puts the glossary's
        own *Abbreviations* over the definitions it heads.

        **``<front>`` is the third container, on the same argument** (issue
        #230). JAMA deposits *"Funding/Support"* and *"Role of the
        Funder/Sponsor"* as bare ``<author-notes><p>``, ``<fn
        fn-type="COI-statement">`` sits in ``<author-notes>``, and PLOS puts
        its data availability in ``<front><notes>`` — so the material this
        branch routes from ``<back>`` was dropped from ``<front>``, with no
        counter and no line, which made identical markup mean two things by
        position once more. Tallied the same way, and excluding a ``<p>`` in a
        table cell, which ``characters()`` has already filed: 9,328 runs in
        3,350 of the 8,118 served articles (41.3%, 1.08 million characters)
        and 114,519 in 46,737 of the 97,909 archive ones (47.7%, 12.1 million),
        ``<author-notes>`` the bulk of both (6,280 and 81,810). Routed with no
        special case — a
        ``<trans-abstract>`` included, being sometimes the only English
        abstract an article carries — and in document order, which puts front
        matter ahead of the body in ``body_sections`` and so just after the
        abstract in the rendered article. Editorial boilerplate is routed all
        the same — ``fn-type="edited-by"`` alone is 2,443 of the 6,280 served
        ``<author-notes>`` runs and 41,431 of the 81,810 archive ones — since
        refusing by an attribute vocabulary is what this module has declined
        everywhere else. **The ``<ref-list>`` refusal applies here as in
        ``<back>``**: ``<front>`` admits ``<notes>`` and ``<notes>`` admits a
        ``<ref-list>``. No artifact deposits one (0 of 8,118 and of 97,909),
        and a first cut that said JATS admits none filed its apparatus as
        article prose (PR review).

        An **ancestor** test on ``element_stack``, for ``_inside_mixed_citation``'s
        reason: the claim is inherited down the whole subtree, a ``<note>``
        sitting inside a ``<ref>`` inside the list. Read from the stack rather
        than from ``in_ref_list``, which is a bare boolean that a nested
        ``<ref-list>``'s close clears — JATS permits the nesting, and the flag
        would then re-admit the outer list's remaining apparatus, which is
        #115 one element family over.

        The slice excludes the element now closing, and that half is
        **prospective, so do not read it as load-bearing** —
        ``_inside_mixed_citation``'s own slice is the same shape and says the
        same thing. ``_append_prose`` is reached from three arms, ``<p>``,
        ``<disp-formula>`` and ``<attrib>`` (issues #241, #248), so the
        excluded element is never the ``<ref-list>`` being tested for:
        dropping the slice survives the whole suite (measured, and the one
        survivor of #224's eight-mutant sweep). It is kept because it makes the
        test say what it means, and because a caller added later would
        otherwise inherit a rule nobody restated.

        Returns:
            ``True`` if the prose should open or extend the implicit section.
        """
        if self.in_body:
            return True
        if self.in_back or self.in_front:
            return "ref-list" not in self.element_stack[:-1]
        return False

    def _heading_is_its_containers_own(self) -> bool:
        """Whether a ``<title>`` here heads unsectioned prose this module files.

        The gate on issue #231's recovery, asked from the ``<title>`` arm once
        every other owner has been offered it. It mirrors the guards
        :meth:`_append_prose` asks before its unsectioned branch, and reuses
        :meth:`_unsectioned_prose_is_the_articles` for the last of them, rather
        than listing container elements — so a heading is admitted exactly where
        the prose beneath it could open an implicit section.

        **One term decides and four are recorded equivalents**, and the flush
        being lazy is why. A frame admitted here does nothing until prose is
        routed to an implicit section while it is the innermost one (see
        :meth:`_implicit_section_for_prose`); a frame that titles nothing leaves
        no trace. So a term refusing a position where :meth:`_append_prose` can
        never open an implicit section decides nothing, and four of them are
        that:

        * the **float** guard — prose inside a ``<fig>`` or ``<table-wrap>``
          reaches a caption, a footnote or nothing, never an implicit section,
          and the frame's owner is inside the float so it pops first. What it
          keeps out is a ``<table-wrap-foot>``'s or exhibit ``<fn-group>``'s
          heading, which is #238's and counted there;
        * ``section_stack`` — prose under an open ``<sec>`` reaches that
          section, and a ``<title>`` read while one is open belongs to an
          element *inside* it, so its frame pops before the section closes. It
          states the scope that keeps issue #240 out: an ``<fn-group>``'s
          heading inside a ``<sec>`` must still not rename it (#125);
        * the **declined-metadata** guard — :meth:`_append_prose` refuses
          prose under an object's metadata before anything else;
        * the ``<ref-list>`` half of :meth:`_unsectioned_prose_is_the_articles`
          — that list's prose is refused as bibliography apparatus (#224), so
          its *References* heading would title nothing.

        Each was a live guard while the recovery flushed on reading a heading,
        since a heading admitted here then cut the surrounding prose in two;
        the four ``..._does_not_end_the_pending_section`` tests were written to
        separate them from their mutants under that design and now pin the
        behaviour rather than the term. They are kept because this predicate
        states a rule rather than a position — ``_prose_is_refused_apparatus``
        keeps its own unreachable ``section_stack`` term for the same reason —
        and because *an equivalence is a claim about the code around the flag*:
        a later commit teaching :meth:`_append_prose` to open an implicit
        section in any of those positions re-opens it.

        **The abstract term is the one that decides, and it is an ancestor test
        rather than the ``in_abstract`` flag.** The live-flag case never reaches
        here — the ``<title>`` arm's abstract branch takes it first — but the
        flag is one boolean over possibly-nested ``<abstract>`` elements, set
        at any open and cleared at any close, so an ``<abstract>`` inside a
        float within the article's own abstract (#249's shape) clears it while
        the outer abstract is still open. The abstract's remaining prose then
        falls through to the front implicit section, which is pre-existing and
        untitled; read from the flag, this gate would also admit the abstract's
        next section heading, putting it over abstract prose in the HTML
        ``FullTextService`` caches — the one position where this recovery could
        produce a *wrong* value. Measured on neither named artifact (0 of
        8,118 served and 0 of 97,909 archive articles), so it pins a direction
        rather than a population; the element stack cannot go stale, which is
        :meth:`_unsectioned_prose_is_the_articles`' own reason for reading it.

        Returns:
            ``True`` if this heading is an unsectioned container's own.
        """
        if self.in_figure or self.in_table_wrap:
            return False
        if "abstract" in self.element_stack[:-1]:
            return False
        if self.section_stack:
            return False
        if self._inside_declined_metadata():
            return False
        return self._unsectioned_prose_is_the_articles()

    def _recover_container_heading(self, title: str) -> None:
        """Take a container's own deposited heading for its unsectioned prose.

        Called from the ``<title>`` arm for a heading
        :meth:`_heading_is_its_containers_own` has accepted. It pushes a frame
        carrying the heading and the depth of the element that owns it, and
        **flushes nothing**: the section this heading titles is opened by the
        first prose routed under it (:meth:`_implicit_section_for_prose`),
        which also ends whatever section that prose would otherwise have
        joined. Flushing here instead cut the pending section on *reading* a
        heading whether or not anything followed — so a ``<kwd-group>``'s
        *Keywords*, which heads no routable prose, split a front-matter run in
        two, and a ``<supplementary-material>``'s own heading inside an
        ``<ack>`` rendered *Acknowledgements* twice with nothing between.

        **The one writer of a frame, and it refuses an empty heading.** A frame
        holding ``""`` would open an untitled section that is still a boundary,
        so ``if frame.title`` and ``frame is None`` would disagree about whether
        a heading is open — the hazard :class:`_DefinitionFrame` collapsed
        ``""`` into ``None`` to remove, refused here at the write site for the
        same reason. The ``<title>`` arm no longer tests the text itself, so
        this is the only protection and it is pinned
        (``test_an_empty_heading_does_not_end_the_pending_section``).

        **A second heading for the same element replaces the first with a new
        frame** rather than stacking or rewriting: the element has one close,
        so it can pop one frame, and a new frame — compared by identity — ends
        the section the first heading titled, so prose after the second heading
        is the second heading's, the reading the document's own order gives.
        Where no prose came between them the first heading titles nothing, and
        it joins the population of headings that title nothing, which is
        measured rather than counted — see the ``<title>`` arm. Illegal JATS,
        the Tag Library's content models giving each of these containers a
        single ``title?``, and **measured empty**: of the elements carrying a
        direct ``<title>`` outside ``<sub-article>``/``<response>`` regions —
        173,994 in the 8,118 served articles of
        ``PMC10030002_PMC10040000.xml.gz`` and 2,465,840 in the 97,909 archive
        ones of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` —
        **none carries two**. So it pins a direction rather than a population.

        Args:
            title: The heading, already whitespace-normalised.
        """
        if not title:
            return
        owner_depth = len(self.element_stack) - 1
        frame = _HeadingFrame(title=title, owner_depth=owner_depth)
        if self.heading_stack and self.heading_stack[-1].owner_depth == owner_depth:
            self.heading_stack[-1] = frame
            return
        self.heading_stack.append(frame)

    def _implicit_section_for_prose(self) -> _SectionBuilder:
        """The implicit section the next unsectioned run joins, opening one if needed.

        The one place an implicit section is opened, for the three slots
        :meth:`_append_prose`'s unsectioned branch fills — so "a section takes
        the heading it was opened under" is written once rather than per slot,
        and a mutant on one slot cannot survive while its siblings are pinned
        (which the per-slot copies allowed: the ``<body>`` copy was pinned by
        nothing, PR #280's review).

        **The flush is lazy, and it is keyed on the frame's identity.** A
        builder accepts prose only while the heading frame it was opened under
        — or ``None`` — is still the innermost live one. When it is not, the
        builder is flushed and a new one opened under the current frame, with
        that frame's heading. So a section ends where its heading's element
        ends, but only if prose arrives to show it: a heading that titles
        nothing ends nothing, and two untitled runs either side of it stay one
        section, as they were before #231. That is what removed three shapes
        the eager flush produced — a ``<kwd-group>``'s *Keywords* splitting a
        front-matter run, a heading on an empty container splitting untitled
        back matter, and a nested element's own heading inside an ``<ack>``
        rendering *Acknowledgements* twice — each of which moved
        ``body_sections`` without a heading the reader could see.

        The slot is chosen from ``in_body`` / ``in_back`` / ``in_front`` in the
        order :meth:`_flush_implicit_section` empties them, which is what makes
        the flush below end the section this run was about to join and no
        other, in DTD-invalid nestings included.

        Returns:
            The builder the run should be appended to.
        """
        heading = self.heading_stack[-1] if self.heading_stack else None
        if self.in_body:
            builder = self.implicit_body_section
        elif self.in_back:
            builder = self.implicit_back_section
        else:
            builder = self.implicit_front_section
        if builder is not None and builder.heading is heading:
            return builder
        if builder is not None:
            self._flush_implicit_section()
        builder = _SectionBuilder(title=heading.title if heading else "", heading=heading)
        if self.in_body:
            self.implicit_body_section = builder
        elif self.in_back:
            self.implicit_back_section = builder
        else:
            self.implicit_front_section = builder
        return builder

    def _prefix_pending_definition_term(self, text: str) -> str:
        """Fold the innermost open ``<def-item>``'s ``<term>`` into its definition.

        A ``<def-list>`` renders as the definitions alone unless the word each
        one defines is carried with it, and this module models no definition
        list — so the term joins the definition's own paragraph, which is what
        already happens to a ``<list-item>``'s prose one element family over
        (issue #228). See :data:`_DEFINITION_SEPARATOR` for the shape and why.

        **Called from :meth:`_append_prose` rather than from the ``<p>`` arm**,
        so one rule serves all five branches that method routes through — a
        caption, an abstract, a section, the unsectioned branch and the
        ``<ref-list>`` refusal, the last of which discards the prose and
        counts it rather than being a destination in any other sense.
        A ``<p>`` may carry a ``<def-list>``, so a
        figure legend can hold one, and stating the fold twice would leave two
        spellings of it to keep in step, which is why ``_append_prose`` exists
        at all (issue #147).

        **The term is spent only on a paragraph that is accounted for**, which
        is the whole of why this asks two predicates instead of prefixing
        unconditionally. Its first test declines an object's metadata before
        the fold is reached (issues #241, #248), and a caller passing
        ``spend_pending=False`` skips the fold too, so neither spends a term.
        Past those, ``_append_prose`` has three outcomes and not two: it
        files the prose, it refuses it as bibliography apparatus and counts
        that, or — in a ``<floats-group>``'s ``<boxed-text>``, which sits in
        none of the three containers — it falls past every branch with no
        counter and no line at all (issue #253). Consuming the term in that third case
        would hand it to a paragraph nobody ever sees and leave
        ``definition_terms_dropped`` reading zero over the population it
        exists to size; consuming it in the second keeps one loss to one
        count, which is the rule PR #232's review had to correct for a
        ``<disp-formula>`` reported as two. The third case was ``<front>``,
        where 1,441 of the counter's 1,444 served terms lived, until issue
        #230 routed front matter and made those terms folds.

        A ``<def-list>`` inside a float reaches the third case too **where the
        float gives it nowhere to go** — no ``<caption>`` open *and* not
        footnote matter — its definition being dropped as exhibit furniture,
        so the counter gives that shape its first line as well. The narrowing
        is issue #124's: an exhibit's footnote is now a destination, so a
        definition list deposited in a ``<table-wrap-foot>`` is folded and
        filed like any other, and that position left this counter's
        population.

        The innermost frame is the ``<p>``'s nearest ``<def-item>`` ancestor,
        frames being pushed and popped with the element, so a nested
        definition list's term goes to its own definition and the enclosing
        item keeps its own.

        Empty prose takes no term: ``keep_empty=True`` still appends an empty
        paragraph a document deposited, and prefixing a term onto it would
        spend the word on a paragraph that says nothing. The term stays
        pending for a later paragraph of the same definition, or is counted at
        ``</def-item>``.

        Args:
            text: The prose about to be routed, already whitespace-normalised.

        Returns:
            ``text``, with the pending term and separator ahead of it where
            there was one to fold in.
        """
        if not text or not self.def_item_stack:
            return text
        frame = self.def_item_stack[-1]
        if frame.term is None:
            return text
        if len(self.figure_stack) + len(self.table_stack) > frame.exhibit_depth:
            # A <fig> or <table-wrap> opened inside this <def-item>, so the
            # prose reaching output is that exhibit's caption and not this
            # definition. Folding here put the word into a public caption
            # field and left the definition without it — a wrong value where
            # the alternative is a blank, which is the preference #116 and
            # #162 both settled. The term stays pending for the definition's
            # own prose, and is counted at </def-item> if none arrives.
            return text
        if not (self._prose_reaches_output() or self._prose_is_refused_apparatus()):
            return text
        term = frame.term
        frame.term = None
        return f"{term}{_DEFINITION_SEPARATOR}{text}"

    def _append_prose(self, text: str, *, keep_empty: bool, spend_pending: bool = True) -> None:
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
            spend_pending: ``False`` for a run that is filed where prose is but
                is not the prose a pending word labels, which leaves a pending
                footnote marker and definition term for the prose that follows.
                The ``<attrib>`` arm passes it; see that arm for why.
        """
        if self._inside_declined_metadata():
            # A <p> inside an object's licence is not the article's prose, and
            # its own arm reaches here whatever buffer surrounds it — one of
            # the two routes membership of `_TEXT_ACCUMULATING` cannot close,
            # the direct cell write being the other (issues #241, #248). Asked
            # before the definition fold, so a pending term waits for its
            # definition's real prose, and uncounted: this is metadata this
            # module declines, not content it loses.
            return
        if spend_pending:
            text = self._prefix_pending_definition_term(text)
        if self.in_figure or self.in_table_wrap:
            # Figure and table internals, tested before every prose branch
            # because a <fig> or <table-wrap> usually sits inside a <sec>:
            # asking about the section first would blank the caption and
            # reprint it as article prose. Two destinations, and everything
            # else here is dropped.
            #
            # A cell's <p> is still dropped, and that is the invariant this
            # branch exists for: characters() already collects cells into the
            # rendered table, so letting one through would print the same text
            # twice and count furniture towards has_body.
            #
            # A footnote's is the table's own content and used to be dropped
            # with it (issue #124) — the abbreviation expansions the cells are
            # unreadable without, and the per-table funding and disclosure
            # notes.
            #
            # **THE CAPTION IS ASKED FIRST, AND THE ORDER IS A RULE RATHER
            # THAN A PREFERENCE.** Asking it first is what keeps
            # `_append_caption_text`'s own rule unconditional: text inside a
            # <caption> belongs to that caption's owner, and to *nobody* where
            # this module does not model the owner. The two questions overlap
            # in exactly one shape, because a <fig> or <table-wrap> opened
            # inside a footnote ends the owner walk on its own — so the
            # overlap needs a caption-carrying element the parser does not
            # model, a <supplementary-material> or a <media> inside an <fn>.
            # There the footnote-first order would file that element's legend
            # as the enclosing table's note: a *wrong* value where the
            # alternative is a blank, which is the preference #116 and #162
            # both settled.
            #
            # Measured 0 of the 8,118 served articles of
            # `PMC10030002_PMC10040000.xml.gz` and 0 of the 97,909 archive
            # ones, so the order pins a direction
            # and moves nothing stored — the standing #236 gave the fold's own
            # exhibit-depth scope, and for the same reason: what it prevents
            # is silent and permanent. A footnote's own <p> carries no
            # <caption> ancestor, so the ordinary deposit reaches the same
            # place either way.
            if self.caption_stack:
                # `_append_caption_text` decides which caption's owner gets it.
                self._append_caption_text(text)
            else:
                footnote_owner = self._owning_exhibit_footnote()
                if footnote_owner is not None:
                    footnote_owner.append_footnote(text, fold_marker=spend_pending)
        elif self.in_abstract:
            if text:
                self.current_abstract_text.append(text)
        elif (self.in_body or self.in_back or self.in_front) and self.section_stack:
            if not text and not keep_empty:
                return
            if self.in_body and text:
                self.body_paragraph_count += 1
            self.section_stack[-1].paragraphs.append(text)
        elif text and self._unsectioned_prose_is_the_articles():
            # An unsectioned <body>, <back> or <front> child — <sec> is
            # optional in all three, and the predicate says which back or
            # front matter is the article's (issues #224, #230). Empty
            # paragraphs are dropped rather than opening a section, so a <body> holding
            # nothing but whitespace stays body-less and a <back> or <front>
            # holding nothing but whitespace adds no untitled section to the
            # rendered article. The branches are asked body, back, front —
            # the order `_flush_implicit_section` empties the slots in, which
            # is what keeps a DTD-invalid nesting from filling one slot and
            # flushing another. `_implicit_section_for_prose` asks them in the
            # same order, and is also where the container's own deposited
            # heading is taken (issue #231) and where a section ends because a
            # different heading is now innermost.
            if self.in_body:
                # <body> alone, because `has_body` is what stops
                # `FullTextService` caching a body-less document and going no
                # further. An article that is front matter plus back matter is
                # not an article, however much acknowledgement prose it
                # carries — so the counter answers "is there a body?" while
                # `body_sections` answers "what did the document say?", which
                # is why the two were separated in the first place.
                self.body_paragraph_count += 1
            self._implicit_section_for_prose().paragraphs.append(text)
        elif text and self._prose_is_refused_apparatus():
            # The <ref-list> refusal. `self.in_back or self.in_front` alone
            # would do here, the branches above having excluded everything
            # else the predicate tests, but the formula arm one method over
            # reaches this rule from a different position and two spellings of
            # one refusal are two things to keep in step. (This said
            # `self.in_back` alone until PR #256's review: a front
            # <ref-list>'s unsectioned prose reaches this arm too.) What
            # neither may become is a bare `else`: a <p> in a
            # <floats-group>'s <boxed-text> also falls past the branch above,
            # belonging to none of the three containers, and nothing decided
            # that (issue #253), so pooling the two would report a refusal
            # this module made and one it never considered as one. The shape
            # named here was front matter until issue #230 routed it.
            self.refused_apparatus_prose += 1

    def _append_caption_text(self, text: str) -> None:
        """Append caption prose to the innermost open ``<caption>``'s owner.

        A ``<caption>`` carries a ``<title>`` lead and one or more ``<p>``
        elements, which arrive in document order, so they are joined with a
        single space into the one ``caption`` string the models expose.

        Text arriving with no caption open is furniture — a cell — and is
        dropped, which is what keeps table internals out of the prose. Text
        whose innermost caption has no modelled owner is dropped for the same
        reason: it belongs to that element, not to the exhibit enclosing it.

        **A footnote no longer reaches here at all** (issue #124). It used to,
        and was named beside the cell as furniture; the caller now asks
        :meth:`_owning_exhibit_footnote` first and files it on the exhibit,
        the notes being the exhibit's own content rather than a second
        rendition of something already printed. A cell is still furniture for
        the reason it always was: ``characters()`` has already written it into
        the rendered table.

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

    def _flush_implicit_section(self) -> None:
        """Emit the open container's pending unsectioned prose, if any.

        Called when a real ``<sec>`` opens and again at ``</body>``,
        ``</back>`` and ``</front>`` (issue #230), so loose paragraphs keep
        their position in document order — a document's acknowledgements land
        ahead of the appendix section that follows them, not after it. The
        section carries no title — JATS gave it none, and inventing one would
        put a heading in the rendered article that the publisher never wrote.

        **It empties one slot, chosen by the container that is open**, which
        is what makes the call sites' ordering load-bearing rather than
        decorative: each flush must precede its own ``in_body`` / ``in_back``
        / ``in_front`` clear, or it reads the wrong slot and empties nothing. A
        helper that emptied *whatever* was pending would let ``</back>`` clean
        up after a missing ``</body>`` flush, which is the laundering the three
        slots exist to prevent — see their comment in ``__init__``.

        **``<body>`` is tested first, and the shape that needs it is a
        ``<body>`` nested inside a ``<back>``, not the reverse.** An earlier
        comment named the reverse, which does not discriminate: with a
        ``<back>`` inside a ``<body>`` both flags are set but ``_append_prose``
        files everything in the body slot, so the back slot is empty and
        either order reads the same — swapping the branches survived the whole
        suite. Nested the other way the back slot fills *before* ``<body>``
        opens, so at ``</body>`` both flags are set and both slots hold prose,
        which is the only state where the order decides: testing ``in_back``
        first empties the back slot at ``</body>``, leaves ``</back>`` nothing
        to flush, and strands the body slot, losing that prose outright.
        Pinned by
        ``test_a_body_inside_a_back_does_not_let_the_back_branch_steal_the_flush``.
        Both are DTD-invalid, so this is about not compounding a malformed
        document. Neither open is the ordinary case of the article root, where
        nothing can be pending because nothing routes there.

        **``<front>`` is tested last, for the same reason one container
        further out**: :meth:`_append_prose` asks body, back, front, so a
        ``<body>`` or ``<back>`` nested inside a ``<front>`` fills its own slot
        after the front's has begun, and only the matching order empties the
        inner slot at the inner close. Each pairing is pinned by a
        ``..._inside_a_front_...`` test beside the one above.
        """
        if self.in_body:
            pending, self.implicit_body_section = self.implicit_body_section, None
        elif self.in_back:
            pending, self.implicit_back_section = self.implicit_back_section, None
        elif self.in_front:
            pending, self.implicit_front_section = self.implicit_front_section, None
        else:
            return
        if pending is not None:
            self.body_sections.append(pending.build())

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
                self._flush_implicit_section()
                self.section_stack.append(_SectionBuilder())
        elif name == "def-item":
            # One frame per open definition item, holding its <term> until the
            # definition's prose arrives to carry it, and the exhibit depth it
            # opened at so a float inside its own <def> cannot take that term
            # into a caption. See `_DefinitionFrame`.
            self.def_item_stack.append(
                _DefinitionFrame(exhibit_depth=len(self.figure_stack) + len(self.table_stack))
            )
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
            # `element_stack[-1]` is this <caption>: the push above precedes
            # every arm, as the pop follows every arm in `endElement`.
            parent = self._parent_element()
            self.caption_stack.append(self._exhibit_named(parent))
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
            # committed corpus (13,624 deposits, every extension unpadded) —
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
            elif (
                href
                and owner in _EXHIBIT_FOOTNOTE_CONTAINERS
                and self._owning_exhibit_footnote() is not None
            ):
                # An image the footnote matter itself owns — a note's, or
                # the loose general note's — dropped by the opacity rule
                # above and counted (issue #238). Scoped to that owner and
                # not to every <graphic> in footnote matter, on measurement
                # (a deposit survey scoped as the heading one is: owner as
                # `_graphic_owner` computes it, suppressed regions skipped,
                # a cell ending the walk): of the 329 such images in the
                # archive artifact, 319 (in 70 articles) are an
                # <inline-formula>'s, which is issue #175's population, and
                # 3 (in 2) a <boxed-text>'s — against 7 in 4 articles owned
                # by the <fn>. The served bundle's one such image is a
                # formula's. An ancestor test would have pooled all three.
                # Every owner outside the three sets is issue #244's
                # residual, uncounted here.
                #
                # `href` first: a <graphic/> referencing nothing is not an
                # image to lose, the rule `offer_graphic` makes for the same
                # deposit one branch up. And the unit is the *deposit* — an
                # <alternatives> pair, transparent to the owner walk, reaches
                # this arm twice and reads 2, which is what the survey counts
                # too; the audit line names that unit rather than claiming
                # two images. Its <alt-text>, if any, no longer welds into the
                # note's prose: membership of `_TEXT_ACCUMULATING` isolates it,
                # and outside a <mixed-citation> or an <xref> nothing merges
                # it back (issue #241).
                self.footnote_graphics_dropped += 1
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
        elif name in _CITATION_ELEMENTS:
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
        elif name == "pub-date":
            # Both spellings: JATS 1.1+ replaced `@pub-type` with `@date-type`
            # beside `@publication-format`, and the values are the same
            # vocabulary — 968 of the served artifact's 19,175 <pub-date> and
            # 22,021 of the archive's 224,634 declare their type the second
            # way. The two spellings' value sets overlap without matching —
            # the 1.1+ form moves the electronic/print distinction into
            # `@publication-format`, so `pub` appears only there and `epub`
            # only under `pub-type` — and a refused value could arrive in
            # either. None does: 0 in every artifact, and no <pub-date>
            # declares both attributes, so which is read first pins a
            # direction rather than a population (#261).
            self.current_pub_date_type = attrs.get("pub-type") or attrs.get("date-type")
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
        self._offer_cell_text(content)

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
            # A cell accumulates in order to discard (issue #243), so its
            # buffer may not merge here either — and the term is explicit for
            # the reason the two above it are. `td`/`th` are not in
            # `_INLINE_ELEMENTS`, so `_inside_mixed_citation()` is the only
            # path by which a cell's buffer can merge; left to that, the drop
            # would be guaranteed by the absence of a `<table-wrap>` or
            # `<array>` under a `<mixed-citation>` rather than structurally.
            # Where one appeared, the cell's text would reach
            # `JATSReferenceInfo.citation` *and* the cell — #243's own splice
            # in a public field, plus the doubled rendition `_FORMULA_PARTS`
            # exists to prevent — while `cell_text_dropped` reported a loss
            # that had not happened, the counter's own version of the audit's
            # rule that a line must mean only "bmlib is wrong"
            # (`current_article_id_type`, which accused a parse it had read
            # correctly). Measured 0 cells under a `<mixed-citation>` over
            # both named artifacts, so this pins a direction and not a
            # population, the standing the `<term>` parent test is given.
            is_cell = name in _TABLE_CELL_ELEMENTS
            # An object's declined metadata, and an attribution, merge into an
            # <xref> around them (issues #241, #248): an <xref> *replaces* its
            # text with a link label, and an empty label fires the arm's
            # `text or "Figure"` fallback, so isolating an <inline-graphic>'s
            # <alt-text> there turned `[Figure 1](#f1)` into the invented
            # `[Figure](#f1)` — #162's symptom. The weld comes with it
            # (`[Fig. 1icon](#f1)`), as on `main`. A <mixed-citation> claims
            # the same text through `_inside_mixed_citation` already; see
            # `_TEXT_CLAIMING_ELEMENTS`, and `_inside_declined_metadata` for
            # the same rule on the routes that bypass this buffer.
            is_claimed = name in _CLAIMABLE_ELEMENTS and self._inside_text_claiming_element()
            element_text = self._pop_text_buffer(
                merge_with_parent=(is_inline or self._inside_mixed_citation() or is_claimed)
                and not is_fig_table_xref
                and not is_owned_name
                and not is_formula_part
                and not is_cell
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

        if (
            name != "elocation-id"
            and self.current_reference is not None
            and "elocation-id" not in self.element_stack
        ):
            # Any other element closing parts two <elocation-id>s; see the
            # `<elocation-id>` arm. One closing *inside* a part does not — the
            # Tag Library models `<elocation-id>` as text only, so a child is
            # invalid, but a `<sup>` there would otherwise part the locator from
            # its own continuation. `element_stack` still holds the closing
            # element here, so a match is an ancestor.
            self.current_reference.elocation_may_continue = False

        # --- Handle element end ---

        if self.nested_article_depth:
            # Still inside a nested article, so this close is not the
            # article's either. Tested before every handler rather than at
            # each one, because a handler added later would otherwise have to
            # remember to opt out.
            #
            # Many handlers are already inert here — they need in_front,
            # in_body or a non-empty section_stack, none of which the
            # suppressed open ever set. Several are not, and they are why this
            # half is load-bearing on an *ordinarily* ordered document rather
            # than only an out-of-order one. </abstract> flushes its buffer
            # without clearing it, and only the opening tag clears, so a
            # nested one re-emits the article's own abstract a second time.
            # And the article-metadata arms test an owner *path* on
            # `element_stack` (issues #254, #259, #152), which keeps running
            # through the region: a review round deposited with a <front>
            # rather than a <front-stub> matches `front > article-meta` exactly
            # as the article does. The round's own text never reaches a buffer
            # (characters() is suppressed too), so what leaks without this
            # guard is an *empty* value blanking the article's last-writer
            # fields — title, volume, issue, pages and journal.
            #
            # Since #272 this guard is **alone** for only two of those five:
            # the <volume>, <issue> and <fpage> arms refuse an empty value in
            # their own right, so a suppressed round's closes now reach them
            # and are refused there too. `title` (normalized_text, written
            # unconditionally) and `journal` are the two this guard still
            # protects by itself — a second protection arriving next door does
            # not make this one redundant, but a comment that says "alone"
            # after one has is the drift PR #274's review found.
            pass
        elif name == "front":
            # Flush before the clear, for `</back>`'s reason below: the flush
            # picks its slot from this flag (issue #230).
            self._flush_implicit_section()
            self.in_front = False
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
            if self._in_own_metadata(_JOURNAL_META, _JOURNAL_TITLE_WRAPPERS):
                self.journal = text
        elif name == "article-id":
            # The owner path, like every article-metadata arm (issue #152).
            # This was `parent == "article-meta" or self.in_front`, neither half
            # pinned and the two not equivalent: the parent half admitted an
            # <article-meta> outside <front>, and the flag any <article-id>
            # anywhere in <front> — including one JATS 1.3 admits there, in a
            # <pub-history><event>, where it identifies another version (a
            # preprint's DOI) and would have replaced the article's typed DOI.
            # Both measure 0 in all four artifacts, so the rule is chosen for
            # agreeing with its neighbours, not by a draw.
            if self._owned_by(*_ARTICLE_META):
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
            # a 1,000-article draw per window, 997 of the recent one served,
            # drawn from a named PMC OA baseline
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
            elif (
                normalized_text
                and parent in _EXHIBIT_FOOTNOTE_BLOCKS
                and self._owning_exhibit_footnote() is not None
            ):
                # The block's own heading, dropped by the owner rule above
                # and counted (issue #238). Two guards, and each keeps a
                # different population out. The parent test keeps out a
                # <list><title> inside a note: that title is dropped by the
                # same rule wherever the list sits, so counting it only here
                # would key the counter on the wrong scope. The owner walk
                # keeps out every <fn-group> heading belonging to no
                # exhibit, and that is two populations, not one: an
                # unsectioned <back>'s is a *container's* and issue #231's,
                # differently caused and unmeasured here; a sectioned one —
                # `<sec><fn-group><title>Competing interests</title>` in
                # <body> or <back> — is #125's own residual, 12 titles in 3
                # of 997 served articles by that issue's survey, dropped with
                # no counter, and filed as issue #240. Pooling any of them
                # with this one would report a loss this counter sized and
                # one it never measured as one, the
                # `_prose_is_refused_apparatus` gate's argument. And an
                # empty <title/> costs nothing: nothing was read, so the
                # line would state a loss that did not happen.
                self.footnote_headings_dropped += 1
            elif self._heading_is_its_containers_own():
                # An unsectioned container's own heading, kept rather than
                # dropped (issue #231). Last of the arm's branches because
                # every owner that has a destination of its own has been
                # offered it first: this one is reached only where the prose
                # beneath the heading would open an *implicit* section, which
                # is precisely the prose the heading heads.
                #
                # It is the opposite of what #116 and #162 refused, and the
                # distinction is the whole of the argument: those two refused
                # to *derive* a heading — a figure number from a list index, a
                # section name from an element name — while this one is
                # deposited, and was being thrown away. Nothing is invented for
                # a container that deposits none.
                #
                # MEASURED on both named artifacts by an instrumented handler
                # recording every run taking `_append_prose`'s unsectioned
                # branch, joined per article by occurrence index to a walk of
                # the same bytes asking whether the block deposited a <title>.
                # Of the blocks contributing to an implicit section, served
                # (`PMC10030002_PMC10040000.xml.gz`, 8,118 articles) / archive
                # (`oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`,
                # 97,909): <back> 11,857 of 17,384 (68.2%) / 220,491 of 298,700
                # (73.8%), <front> 475 of 3,743 (12.7%) / 7,196 of 53,722
                # (13.4%), <body> 5 of 1,182 / 969 of 10,258 — 12,337 of
                # 22,309 (55.3%) / 228,656 of 362,680 (63.0%) overall.
                # *Acknowledgements*, *Funding*, *Declarations*, *Author
                # contributions*, *Data availability*, *Abbreviations* and
                # *Competing interests* are the commonest, which is the list of
                # disclosures a reader most needs told apart.
                #
                # **Front matter is the half this does not reach**:
                # <author-notes> deposits a heading in 25 of 2,444 served
                # appearances, so front prose still follows the abstract's
                # paragraphs under <h2>Abstract</h2> with no heading of its
                # own. That residual is issue #279 and wants a rendering
                # answer — there is no deposited heading there to recover.
                self._recover_container_heading(normalized_text)
        elif name == "p":
            self._append_prose(normalized_text, keep_empty=True)
        elif name == "attrib":
            # An attribution is printed content — an interview quote's
            # "(P2, CP)", a figure's "Source: Authors' elaboration.", a table's
            # abbreviation list — so it is routed, where an object's metadata
            # is declined (issues #241, #248). It accumulated nowhere and had
            # no arm, so its text reached whatever buffer was open above its
            # owner: where the owner stood in a <p> that was the sentence, and
            # where it stood in a <sec> it was the section's unread buffer,
            # *lost with no line*. Counted over the 97,909 archive articles of
            # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`, every
            # text-bearing attribution by its nearest accumulating ancestor: of
            # 5,266 <disp-quote> attributions 3,844 (in 217 articles) were lost
            # that way and 2 more with no buffer open, 1,331 (in 94) welded
            # into a sentence and 89 sat in a cell; over the 8,118 served
            # articles of `PMC10030002_PMC10040000.xml.gz`, 217, 1, 21 and 0
            # of 239. Discarding it — the issues' own remedy — would have made
            # the majority's silent loss total.
            #
            # **Routed as a <p> is, once what it credits has been asked.** JATS
            # spells <attrib> as a direct child of that object, so the parent
            # decides (#116's test) — walked past a <graphic> by
            # `_graphic_owner`, since an image's credit belongs to whatever
            # owns the image. An exhibit's attribution, or its image's, is
            # filed among that exhibit's footnotes, which render below it:
            # sent through `_append_prose` it would reach neither destination
            # that method offers inside a float — no <caption> is open around
            # it and no footnote container stands above it — and be dropped.
            # An ambient `current_figure` would instead give a nested
            # <table-wrap>'s attribution to the figure. Everything else goes
            # where a <p> would go, which files a quote's attribution as the
            # paragraph after the quote and one inside <table-wrap-foot> as a
            # table note through #124's owner walk.
            #
            # **Four positions take it before that, each keeping the parse
            # consistent with what already holds the text.** Under a
            # <mixed-citation> or an <xref> it merged into that element at the
            # pop (`_CLAIMABLE_ELEMENTS`), so filing it again would store it
            # twice or, under an <xref>, route it and leave the link label
            # empty for `"Figure"` to be invented. Under declined metadata it
            # is declined with it. In a cell it is the cell's text:
            # `characters()` delivered it to a modelled cell already, and for
            # an <array>'s it goes back to the buffer so `cell_text_dropped`
            # counts that cell as the loss it is (issue #245) — routed as a
            # paragraph, one cell's content was half counted and half filed
            # (PR #250's review). A <fig> opened inside a cell files its own
            # attribution, the exhibit being asked first.
            #
            # **Three further differences from a <p>.** `keep_empty=False`,
            # and an empty <attrib/> reaches no branch that files or counts.
            # It never spends a pending footnote marker or definition term
            # (`spend_pending=False`, `fold_marker=False`): an image credit
            # inside a note's or a definition's <p> closes before that <p>,
            # and would take the word meant for the paragraph — `'BMI —
            # Credit: X.'`. Where the note deposits no prose at all, `</fn>`
            # folds the marker into the credit after all (see
            # `_FootnoteHolder.unmarked_credit_slot`). The exhibit branch
            # passes `fold_marker=False` where no marker can be pending — an
            # <fn> between the attribution and the exhibit ends the walk first
            # — so there it states the rule rather than guarding a reachable
            # state, and mutating it survives. And prose that would reach
            # nothing is **counted** (`attributions_dropped`) rather than
            # dropped with no line: an attribution owned by an element bmlib
            # does not model inside a float — a <supplementary-material>, a
            # <boxed-text> — welded into the sentence on `main` and is a blank
            # here, and a blank this module argues for earns a line. The
            # shapes in this paragraph and the one above were found by review
            # at 0 measured population, so each pins a direction.
            #
            # `normalized_text` for the reason the <term> arm gives: the value
            # reaches a public field, and a depositor's line break must not.
            credited = self._parent_element()
            if credited == "graphic":
                credited = self._graphic_owner(closing_child=True)
            exhibit = self._exhibit_named(credited)
            if self._inside_text_claiming_element():
                pass  # merged into the citation or the link label at the pop
            elif exhibit is not None:
                exhibit.append_footnote(normalized_text, fold_marker=False)
            elif not normalized_text or self._inside_declined_metadata():
                pass
            elif self._inside_table_cell():
                self._append_text(element_text)
            elif self._prose_reaches_output() or self._prose_is_refused_apparatus():
                self._append_prose(normalized_text, keep_empty=False, spend_pending=False)
            else:
                self.attributions_dropped += 1
        elif name == "alt-text":
            # Declined metadata (issues #241, #248), with one reader: an image
            # inside a formula spells the formula out here, and it is that
            # formula's rendition of last resort — see `_FormulaFrame.alt_text`
            # for why a field and not a merge. A claimed one (under an <xref>
            # or a <mixed-citation>) is stored too and never read: it merged
            # into the formula's buffer at the pop, and the field is consulted
            # only when that buffer is empty — so asking whether it was
            # declined would be a test no input can separate from its absence.
            if self.formula_stack and normalized_text:
                enclosing_formula = self.formula_stack[-1]
                if not enclosing_formula.alt_text:
                    enclosing_formula.alt_text = normalized_text
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
                    if (
                        rendered
                        and not self._prose_reaches_output()
                        and not self._prose_is_refused_apparatus()
                        # An object's metadata is declined, not lost, and is
                        # counted nowhere (issues #241, #248). The predicate
                        # above answers False there because `_append_prose`
                        # files nothing there, so this is subtracted for the
                        # reason the refusal beside it is.
                        and not self._inside_declined_metadata()
                    ):
                        # The rendition was built and will not be filed.
                        # Counted rather than dropped in silence, the rule
                        # `rejected_spans` settled for #129 — a formula this
                        # parser rendered and then lost is exactly the event
                        # no reader could otherwise see.
                        #
                        # **This arm counts only the routing gap, and the
                        # refusal is subtracted rather than counted here.**
                        # Reported as "reached no section, caption or cell" a
                        # decision (issue #224) would send a reader after a
                        # gap this module chose not to have. But the refusal
                        # itself is counted by `_append_prose` one line down,
                        # which reaches its own refusal arm on exactly this
                        # state — so incrementing it here as well reported one
                        # formula as two, which is what a first cut did and
                        # what the count in
                        # `test_a_refused_formula_is_not_reported_as_a_routing_gap`
                        # now pins — the substring assertion it carried before
                        # passed either way. One rule, one counting site,
                        # asked from two positions.
                        self.formulas_dropped += 1
                    self._append_prose(rendered, keep_empty=False)
                else:
                    # Inside flowing text, which is where an inline formula
                    # always is and a large minority of display formulas are
                    # (37.3% on the served rendition; see the set's comment).
                    # `_append_text` reaches the buffer this formula's own pop
                    # restored, so the rendition lands where the element was
                    # deposited.
                    if rendered:
                        rendered = _pad_as_deposited(rendered, element_text, formula.display)
                    self._append_text(rendered)
                    if not self.formula_stack:
                        # characters() held this formula's text back from the
                        # cell so the LaTeX could be rendered first; this is
                        # where the cell gets its one rendition. Guarded on
                        # the innermost frame alone: a nested formula's
                        # emission is already inside the outer one's buffer,
                        # so offering it to the cell as well prints it twice.
                        # Through `_offer_cell_text`, the door `characters()`
                        # uses, so a formula inside an image's metadata is
                        # held back by the same test (issues #241, #248).
                        self._offer_cell_text(rendered)

        elif name == "body":
            self._flush_implicit_section()
            self.in_body = False
        elif name == "back":
            # Mirrors </body>, and the order is load-bearing at both arms:
            # `_flush_implicit_section` picks its slot from these very flags,
            # so a flush after the clear reads neither slot and strands the
            # prose — which the end-of-parse audit then reports as an ERROR,
            # both slots being in `_ROUTING_FLAGS`.
            #
            # **What kills the swap is every ordinary back-matter fixture**,
            # `TestJATSParserUnsectionedBackMatter`'s in particular, since
            # each loses the prose it asserts. Not
            # `test_back_matter_survives_its_own_flush_order`, which this
            # comment used to name: that test emulates the swap by clearing
            # the flag in a wrapper round `endElement`, so on the mutant the
            # emulation is idempotent and it passes either way. It documents
            # what the wrong order costs; the fixtures are the guard. A
            # comment naming a test that does not redden is the "a rule
            # enforced by prose is not enforced" failure one level down.
            self._flush_implicit_section()
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
            # (`element_stack[-1]` is this <label>, as at <title> above).
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
            elif parent == "fn":
                # A footnote's own marker — "a", "b", "*" — held until the
                # first paragraph of that same <fn> folds it in (issue #124).
                #
                # It was #116 that established this label is *not* the
                # exhibit's number, and it discarded it because there was
                # nowhere else for it to go. There is now, and keeping it is
                # what makes the note usable: <sup> is an inline element
                # flattened into the surrounding cell, so the rendered body
                # still reads `12.3a` and with two footnotes the mapping back
                # is otherwise unrecoverable — a reference to nothing, which is
                # #116's own "a swallowed marker is not a blank" one element
                # down.
                #
                # The same parent test, and it is what keeps a <fn-group>'s own
                # heading — "Notes", "Abbreviations" — out of the marker slot:
                # that <label>'s parent is the group, not the note. 3,102 of
                # the 10,763 <fn> inside an exhibit in the 8,118 served
                # articles carry one, and 39,349 of 137,735 in the archive.
                #
                # `including_self=False` is right here for once *and* for the
                # ordinary reason: the closing element is the <label>, so the
                # <fn> above it is an ancestor either way.
                #
                # The write goes through the holder so a marker it displaces is
                # counted rather than overwritten in silence — see
                # `hold_footnote_label`, which is the `<term>` arm's rule forty
                # lines down applied to the same shape one container over.
                owner = self._owning_exhibit_footnote()
                if owner is not None and owner.hold_footnote_label(text):
                    self.footnote_markers_dropped += 1

        elif name == "fn":
            # A marker no paragraph of this <fn> claimed. Left pending it would
            # be folded into whatever footnote prose arrived next — one note's
            # marker printed on another, silently — which is a *wrong* value
            # where the alternative is a blank, so it is taken back and
            # counted. #228's own hazard one container over, and the same
            # remedy: `_DefinitionFrame.term` is given back at `</def-item>`.
            #
            # `including_self=True` because the <fn> now closing is the
            # container: a <fig><fn> has no other, so the strict slice would
            # answer None for the figure side entirely.
            #
            # Measured 1 of 10,763 served <fn> and 11 of 137,735 archive ones,
            # so this pins a direction rather than a population — but the
            # direction is the one that matters, an unspent marker being
            # invisible in the output it corrupts.
            #
            # Not counted where the note's only filed text is an image credit:
            # the holder folds the marker into that credit, the credit being the
            # whole of the note (issues #241, #248; see
            # `_FootnoteHolder.unmarked_credit_slot`).
            owner = self._owning_exhibit_footnote(including_self=True)
            if owner is not None and owner.take_pending_footnote_label():
                self.footnote_markers_dropped += 1

        elif name == "term":
            # A <term> belongs to the <def-item> that encloses it, and JATS
            # spells it as a direct child, so the parent decides outright —
            # the <label> rule immediately above, for the same reason it was
            # written there (#116): read from an ambient "is a definition list
            # open?", a <term> deposited anywhere else in the list would
            # prefix the next paragraph to arrive with a word that defines
            # nothing in it. 0 of the 14,186 <term> in the 8,118 served
            # articles of `PMC10030002_PMC10040000.xml.gz` and 0 of the
            # 153,256 in the 97,909 archive articles of
            # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` have any
            # other parent, so this pins a direction and not a population.
            #
            # `normalized_text` rather than `text`: a term wraps across source
            # lines like anything else, and an end-stripped `'RT-PCR\n  assay'`
            # reached a public field once already (issue #146).
            if self._parent_element() == "def-item" and self.def_item_stack:
                def_frame = self.def_item_stack[-1]
                if def_frame.term is not None:
                    # A second <term> in one item. JATS's own content model
                    # admits one, and 0 of the 14,186 served items deposit
                    # two, as do none of the 153,256 archive ones — but bare
                    # last-wins with no line is the #116/#143 class of defect,
                    # and a rule resting on a remembered content model is the
                    # rule this module keeps being caught by. The displaced
                    # word is counted, not overwritten in silence.
                    self.definition_terms_dropped += 1
                # Normalised to `None` at the write site, never stored as the
                # empty string: `_DefinitionFrame.term` is a two-state field,
                # and letting `""` in is what left one reader's spelling free
                # to disagree with another's (PR #236's review).
                def_frame.term = normalized_text or None
            elif normalized_text:
                # A <term> that reached no frame: its parent is not a
                # <def-item>, or — unreachable under SAX — none is open. The
                # word is read and discarded either way, so it is counted for
                # the reason every other unfilable term is (PR #236's review).
                # Without this the fold/drop partition below held only because
                # the corpora deposit no such term, which is a property of the
                # draw and not of the code: the routing question and the
                # accounting question are separate, and `_report_zero_authors`
                # settled that counting is not parsing.
                #
                # It counts a word and never an element, as the arm above
                # does. `<index-term>` is the other JATS parent a `<term>` may
                # have and bmlib extracts none, so counting one is honest
                # rather than over-reporting — and neither corpus deposits it:
                # **0 of 14,186 served and 0 of 153,395 archive `<term>` have
                # any parent but `<def-item>`** (whole-document walks, so
                # unscoped and wider than what the parser sees).
                self.definition_terms_dropped += 1
        elif name == "def-item":
            if self.def_item_stack:
                # Guarded for the reason </fig> is: SAX makes a close with
                # nothing open unreachable, and a suppression region guarded
                # on startElement alone is how that stops being true.
                closed = self.def_item_stack.pop()
                if closed.term is not None:
                    # The item closed with its term still pending, so no
                    # paragraph of this definition reached the article to
                    # carry it — measured since issue #230 as exactly the
                    # items that deposit no <def> at all, 3 of 14,186 served
                    # and 23 of 153,256 archive, front matter having been
                    # almost all of it before. Counted rather than dropped in
                    # silence; see `definition_terms_dropped`.
                    self.definition_terms_dropped += 1

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
        elif name in _TABLE_CELL_ELEMENTS:
            current_table = self.current_table
            if current_table is not None:
                current_table.end_cell()
            elif text:
                # No builder ever opened, so nothing received this cell's
                # text — an <array>, in every one of the 355 served and
                # 248,720 archive cells this counter fires on. (The deposit
                # survey's wider 251,362 is the *markup* population; the
                # 2,642 between them are blank cells and cells whose text a
                # child's own arm took, neither of which reaches this arm.
                # Two numbers of two populations, so the site that increments
                # quotes the one it increments — #158's rule turned on the
                # module's own counters.) Counted at the drop rather than
                # inferred from the markup, the rule #228's split settled, and
                # `text` is the cell's own buffer, which `td`/`th`'s membership
                # of `_TEXT_ACCUMULATING` is what makes reachable here. See the
                # counter's own comment for why an <array>'s content was
                # *partly* surviving before #243 and is now wholly lost.
                self.cell_text_dropped += 1
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
        elif name in _CITATION_ELEMENTS:
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
        # The article's own metadata, each read only at its owner path (issues
        # #254, #259). These arms were gated on `in_front and in_article_meta`,
        # so every element nested in <article-meta> carrying the same child
        # names wrote the article's fields: a <related-article>'s title made a
        # correction or commentary read as the paper it corrects, a
        # <mixed-citation> in a retraction notice's abstract gave the notice
        # the retracted paper's title, volume and issue, and an <lpage> welded
        # a suffix onto the article's page range. A wrong value where the
        # alternative is a blank — so where the article carries no <fpage> of
        # its own, `pages` now stays blank rather than taking a citation's.
        elif name == "article-title":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.article_title = normalized_text
            elif self._in_own_metadata(_ARTICLE_META, _TITLE_WRAPPERS):
                self.title = normalized_text
        elif name == "source":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.source = text
        elif name == "year":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.year = text
            elif self._in_own_metadata(_ARTICLE_META, _YEAR_WRAPPERS) and not self.year:
                # First writer among the <pub-date>s **that name a
                # publication**: document order still picks, but a date
                # declaring a `*-submitted` or `*-release` type names no
                # publication and is passed over, so the next one decides
                # (issue #261, decided by the maintainer on 2026-09-15 —
                # option 3, which leaves the epub-versus-issue ordering open;
                # that half is issue #273).
                # Diffed against `main` over the four named artifacts it
                # moves the stored year in 183 of the 8,118 served articles
                # and 456 of the 97,909 archive ones, in 0 of the 3,028 and
                # 27,515 back-filled ones, and to blank in none of them; no
                # other field of `JATSArticle` moves anywhere.
                #
                # No other dated element stands in where no <pub-date> carries
                # a year — a <history> date is not the publication year, nor
                # is another work's — and every article in all four artifacts
                # carries one, so nothing is blanked by that either.
                if _names_a_publication_date(self.current_pub_date_type):
                    self.year = text
                elif text:
                    # The refusal's own cost, where this article deposits no
                    # other dated <pub-date>: `_audit_parse` reports it then
                    # and not per refusal. An empty <year> states no year, so
                    # refusing it loses nothing and counts nothing — the
                    # <elocation-id> arm's empty rule, one arm over.
                    self.non_publication_years_refused += 1
        elif name == "pub-date":
            # Cleared at the close, or a refused type would judge the dates
            # after it too and cost the article its year. See the slot.
            self.current_pub_date_type = None
        elif name == "volume":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.volume = text
            elif text and self._in_own_metadata(_ARTICLE_META, _VOLUME_ISSUE_WRAPPERS):
                # Last writer, but an empty element states no volume and so
                # does not blank the one before it (issue #272) — the guard
                # <lpage> has always had and <elocation-id> was given with
                # #265. <article-meta> admits one <volume>, so a second is
                # invalid markup: an *empty* one measured 0 over the served
                # and archive artifacts (#272's own tally — a non-empty
                # second one is measured nowhere), and the four-artifact diff
                # moves this field in 0 articles. A direction, not a
                # population.
                self.volume = text
        elif name == "issue":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.issue = text
            elif text and self._in_own_metadata(_ARTICLE_META, _VOLUME_ISSUE_WRAPPERS):
                # Its sibling's guard, for the same reason (issue #272).
                self.issue = text
        elif name == "fpage":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.first_page = text
            elif text and self._owned_by(*_ARTICLE_META):
                # Last writer, unlike the year. The ambient gate needed `and
                # not self.pages` to keep a later citation's page off the
                # article's; the owner path does that now, and what the guard
                # was left guarding is a second <fpage> of the article's own,
                # which the <article-meta> model does not admit and no article
                # in the four artifacts deposits. There it was worse than
                # nothing: `100-101` then `200-201` stored `100-101-201`, a
                # range no document states, where last writer stores `200-201`.
                # An *empty* second one is the shape last writer gets wrong in
                # the other direction — it blanked a good range, and the page
                # range and its <lpage> half with it — so it is refused here
                # as <lpage> and <elocation-id> refuse theirs (issue #272).
                self.pages = text
                # This <fpage> opens a range that its own <lpage> may close.
                self.page_range_awaits_last_page = True
        elif name == "lpage":
            if self.in_ref_citation and self.current_reference:
                self.current_reference.last_page = text
            elif text and self._owned_by(*_ARTICLE_META):
                if self.page_range_awaits_last_page:
                    # An <lpage> completes the range the <fpage> before it
                    # opened, and only that one — the flag says so, where
                    # `self.pages` alone says merely that *some* page value is
                    # stored. Two shapes turn on the difference, both invalid
                    # markup measured 0 on all four artifacts: a second
                    # <lpage> appended to a closed range (`100-101-201`,
                    # pre-existing), and the same value re-admitted by #272's
                    # own empty-<fpage> guard, which keeps `pages` non-empty
                    # where blanking it used to hide the defect. Both are the
                    # range `docs/DECISIONS.md` calls one no document states,
                    # and a corruption is worse than a blank.
                    self.pages += f"-{text}"
                    self.page_range_awaits_last_page = False
                else:
                    # Refused, and so counted: the document deposited a page
                    # number that reaches no field. <lpage> is not inline, so
                    # nothing else carries the text — the asymmetry with the
                    # refused <elocation-id> part its counter's comment names.
                    # Both shapes land here, including an <lpage> with no
                    # <fpage> at all, which `main` refused just as silently
                    # through the `self.pages` guard this flag replaced.
                    self.last_pages_dropped += 1
        elif name == "elocation-id":
            # The electronic locator JATS deposits in place of a page range
            # (issue #265; the populations it moved are in docs/DECISIONS.md).
            # It is inline (see `_INLINE_ELEMENTS`), so its text still lands
            # where it did before this arm existed; the arm only reads it.
            reference = self.current_reference
            if self.in_ref_citation and reference and self._parent_element() in _CITATION_ELEMENTS:
                # The reference's own, a direct child of its citation element
                # — true of every one of the 8,549 served and 406,553 archive
                # references whose first citation element carries one — so a
                # <related-object>'s or <related-article>'s locator nested in
                # the citation is not.
                #
                # Several are one locator only when each continues the last:
                # 6 of those 406,553 archive references deposit more than one,
                # five splitting a locator across adjacent elements (`e8` `1`
                # `72` `1` for `e81721`, which `citation` prints as one word)
                # and one repeating it. So a part is joined only where no other
                # element has closed since the last part — a child keeping its
                # text in a buffer of its own leaves no trace in the citation's
                # — *and* the citation prints the two as one run
                # (`_elocation_part_continues`, which is where whitespace is
                # judged per spelling). A repeat of the whole is
                # skipped; any other part leaves the first, as a <ref>'s first
                # citation part is kept (#149), and is counted. An empty part
                # is no locator and parts nothing.
                if text:
                    if not reference.elocation_id:
                        reference.elocation_id = text
                    elif text != reference.elocation_id:
                        joined = reference.elocation_id + text
                        if reference.elocation_may_continue and _elocation_part_continues(
                            self.current_text, joined, self._parent_element()
                        ):
                            reference.elocation_id = joined
                        else:
                            self.elocation_parts_dropped += 1
                    reference.elocation_may_continue = True
            elif text and self._owned_by(*_ARTICLE_META):
                # Last writer, as the <fpage> arm: <article-meta> admits one,
                # and no article in the four artifacts #265 measured deposits
                # two. As that arm and <lpage>, an empty one does not blank
                # the value before it, since an empty value states no locator
                # (PR #269's review). This arm and <lpage> had that guard
                # first; #272 gave it to <fpage>, <volume> and <issue>, so the
                # contrast the comment used to draw with <fpage> is gone.
                self.elocation_id = text
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
        # alone, and every `_parent_element` test in this method — `<title>`,
        # `<attrib>`, a formula's, `<label>` and `<term>` — names the owner for
        # the same one, as `_owned_by`'s owner paths do for every
        # article-metadata arm (issues #254, #259, #152). Moving this up shifts
        # them one element outwards, and the cost is measured rather than
        # asserted: 240 tests in `test_jats_parser.py` redden for a pop placed
        # just before the handler arms, and 252 for one placed above the buffer
        # pop at the top of the method — against 179 and 191 at `5424198`,
        # before the metadata arms read the stack, where this comment still
        # quoted the 58 and 65 of an earlier revision. Re-measure rather than
        # adjust. Only the second reaches the citation slice, because
        # `_inside_mixed_citation` is called from inside `_pop_text_buffer`'s
        # own argument — which is why "move the pop up" has to name *how far*
        # up to mean anything.
        #
        # One neighbour is deliberately not on that list: the `<caption>`
        # parent test is made in `startElement`, where the *push* is what
        # places it.
        if (
            self.heading_stack
            and self.heading_stack[-1].owner_depth == len(self.element_stack)
            and not self.nested_article_depth
        ):
            # A recovered heading stops being the innermost one where the
            # element that deposited it ends (issue #231). **This only pops:
            # it flushes nothing.** The section the heading titled ends lazily,
            # when the next unsectioned prose finds a different frame innermost
            # (`_implicit_section_for_prose`), or at the container's own close
            # or a `<sec>` opening, as every implicit section always has — so a
            # heading that titled nothing ends nothing. Flushing here was the
            # first design, and it split untitled runs around a `<kwd-group>`
            # and rendered a heading twice around a nested element's own
            # (PR #280's review).
            #
            # **Read before the element pop, like every other owner test in
            # this method**: `owner_depth` is `len(element_stack) - 1` taken at
            # the `</title>`, where the stack held the owner and the title, so
            # the owner's own close is the one place the two are equal — and
            # the first close at which `owner_depth >= len(element_stack)`, so
            # writing `>=` is an **equivalent mutant**, recorded rather than
            # counted as tested (PR #280's review).
            #
            # **After the name-keyed arms**, because the owner's own close is
            # still inside the owner: an arm that routed prose there would
            # need the frame live. None does today — the elements a `<title>`
            # may be contained in carry no arm that files prose at their close
            # — so the order decides nothing now, and it is the order that
            # stays right if one is added. `</back>` is the owner this is most
            # likely to meet: the JATS 1.3 Tag Library puts `<back>` among the
            # 31 elements `<title>` may be contained in, and `<body>` and
            # `<front>` are absent from that list, so the container-level
            # heading is reachable for exactly one of the three. Pinned by
            # `test_a_back_level_heading_covers_what_no_container_heads`.
            #
            # The suppression guard is the one `def_item_stack` carries: a
            # frame is only ever pushed outside a nested article's region —
            # the <title> arm is an `elif` of the same suppression test — so
            # popping inside one would unbalance the stack and strand the host
            # article's own heading.
            #
            # **It is an equivalent mutant, recorded rather than counted as
            # tested** (the #231 sweep). A live frame's owner encloses the
            # nested article, or the frame would already have popped; every
            # close inside that region is therefore deeper than the owner, so
            # `owner_depth == len(element_stack)` cannot hold there — and at
            # `</sub-article>` itself the depth was decremented at the top of
            # this method, so the clause could not protect that close even if
            # the depths coincided (PR #280's review). Kept for
            # `def_item_stack`'s reason and because the equivalence rests on
            # the *rest* of the method — on the push being suppressed, on the
            # decrement preceding this block, and on the depth test — any of
            # which a later commit may change.
            self.heading_stack.pop()

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
        # it — a <disp-formula> under an unlisted wrapper inside a float — so
        # it cannot mean "bmlib is wrong" the way the audit above does.
        # Phrased as what happened rather than as a conclusion about the
        # document: the equation was in the deposit and this parser rendered
        # it, which is what makes the loss reportable.
        #
        # **Four destinations, not three.** Issue #124 made an exhibit's
        # footnote one of them, so a line naming "section, caption or cell"
        # sent a reader hunting in three places for a rendition the fourth
        # would have kept — the misdescription the paragraph above forbids,
        # and the manual was corrected for it while this line was not
        # (PR #237's review).
        logger.warning(
            "JATS parse of %s: %d display formula(s) were rendered but reached no "
            "section, caption, cell or footnote, so their equations are missing "
            "from the article (issue #177)",
            article,
            handler.formulas_dropped,
        )

    if handler.refused_apparatus_prose:
        # The same rule and the same level, for a loss this module *chose*
        # (issue #224). Once per article rather than per paragraph, which is
        # what `contribs_naming_nobody` settled: one article's reference list
        # can carry ten of these, and ten identical lines are read as noise.
        #
        # It says what bmlib did and never what the document held. "Refused as
        # bibliography rather than article prose" is a claim about this
        # parser's rule; "the article had no acknowledgements" would be a
        # claim about the publisher, and the whole point of the refusal is
        # that the content *was* deposited.
        # "item(s)" rather than "paragraph(s)": the counter also takes a
        # <disp-formula>'s rendition, which is not a paragraph, and a line
        # that says what bmlib did must not misdescribe what it did it to.
        logger.warning(
            "JATS parse of %s: %d <ref-list> item(s) were refused as "
            "bibliography apparatus rather than article prose, so they are "
            "missing from the article (issue #224)",
            article,
            handler.refused_apparatus_prose,
        )

    if handler.definition_terms_dropped:
        # The same rule and the same level again, for the residue of issue
        # #228: a term is filed wherever its definition's paragraph routes, so
        # what is left is a definition that routed nowhere and took its term
        # with it. Once per article rather than per term, which
        # `contribs_naming_nobody` settled: one glossary can carry fifty.
        #
        # It says what bmlib read and lost, never what the document held —
        # `_report_zero_authors`' rule. And it names the element rather than
        # "abbreviation", because a `<def-list>` is a definition list of any
        # kind and a line that guesses the genre misdescribes most of them.
        #
        # **"from the article's prose", not "from the article".** A
        # `<def-list>` inside a `<td>`/`<th>` is written straight to the
        # rendered table by `characters()`, bypassing every routing rule, so
        # both the term and its definition reach `html_content` and the wider
        # claim would send a reader hunting for words already in front of
        # them — over-reporting of the kind PR #232's review corrected for a
        # `<disp-formula>`. Measured 0 of 1,444 served drops and 0 of 9,468
        # archive ones sit in a cell, so this narrows a claim rather than
        # describing a live population (PR #236's review; denominators
        # re-measured for #124 in PR #237's).
        logger.warning(
            "JATS parse of %s: %d <def-list> term(s) were read and reached no "
            "definition this parser could file, so those words are missing from "
            "the article's prose (issue #228)",
            article,
            handler.definition_terms_dropped,
        )

    if handler.footnote_markers_dropped:
        # The same rule and the same level once more, for the residue of issue
        # #124. Two causes reach it and the line covers both: a marker the
        # first paragraph of its own <fn> never spent, given back at `</fn>`,
        # and a marker displaced by a second <label> in the same note. Once per
        # article, `contribs_naming_nobody`'s granularity.
        #
        # **It says what bmlib filed, never what the document deposited.** An
        # earlier wording read "that deposited no prose", which is a claim
        # about the publisher this parser cannot make and is false for at least
        # three shapes the branch takes — a <label> arriving *after* the note's
        # own prose, a bare text node the <p> rule drops, and prose sitting in
        # a nested float the owner walk refuses. In each the document deposited
        # a note and bmlib is the one that filed none. What is certain, and all
        # that is claimed, is that a marker arrived and nothing in the output
        # carries it (PR #237's review).
        #
        # And it names the *marker* as what was lost rather than the note: the
        # note is either filed already or lost by a rule with its own counter,
        # so reporting a lost footnote would over-report in the shape PR #232's
        # review corrected for a `<disp-formula>`.
        logger.warning(
            "JATS parse of %s: %d footnote marker(s) were read for an exhibit "
            "footnote that bmlib filed no prose for, so those markers are "
            "missing from the article (issue #124)",
            article,
            handler.footnote_markers_dropped,
        )

    if handler.footnote_headings_dropped:
        # The same rule and the same level, for a drop this module chose
        # (issue #238): a footnote block's own <title>, refused by the owner
        # rule that keeps an <fn-group>'s heading from renaming a section.
        # Once per article, `contribs_naming_nobody`'s granularity, and it
        # says what bmlib did — "filed nowhere" — never what the document
        # held, since the whole point is that the heading *was* deposited.
        logger.warning(
            "JATS parse of %s: %d heading(s) of an exhibit's footnote block were read "
            "and filed nowhere, so those headings are missing from the article "
            "(issue #238)",
            article,
            handler.footnote_headings_dropped,
        )

    if handler.footnote_graphics_dropped:
        # Its sibling, for the block's image (issue #238). "In an exhibit's
        # footnote matter" and not "a footnote's image", because the loose
        # general note's <graphic> is owned by the <table-wrap-foot> and
        # reaches this counter too; and it names the image as what was lost,
        # the note's prose having been filed by the ordinary route. The count
        # is of *deposits*: an <alternatives> pair is one image reaching the
        # arm twice, so "2 images are missing" would over-report in the shape
        # PR #232's review corrected for a <disp-formula>, and the line says
        # what the counter counts (PR #239's review).
        logger.warning(
            "JATS parse of %s: %d graphic deposit(s) in an exhibit's footnote matter "
            "were read and filed nowhere, so the image(s) they encode are missing "
            "from the article (issue #238)",
            article,
            handler.footnote_graphics_dropped,
        )

    if handler.cell_text_dropped:
        # Issue #245, at the same level and the same once-per-article
        # granularity as its siblings. It names the *cell* as the unit and
        # says what bmlib did — no table opened to receive the text — rather
        # than what the document deposited, which is
        # `footnote_markers_dropped`'s own correction: the document deposited
        # a perfectly good <array>, and the reason nothing carries it is that
        # bmlib models no such element. <array> is named as the *measured*
        # cause and not as a claim about this document, since the arm sees
        # only that no builder was open — every one of the 355 served and
        # 248,720 archive cells is one, but a container nobody has met would
        # arrive here too.
        logger.warning(
            "JATS parse of %s: %d table cell(s) carried text that reached no table, "
            "no <table-wrap> having opened one (an <array> in every case measured), "
            "so that content is missing from the article (issue #245)",
            article,
            handler.cell_text_dropped,
        )

    if handler.attributions_dropped:
        # Issues #241 and #248, at the siblings' level and granularity. It
        # names the attribution as the unit and says what bmlib did — filed it
        # nowhere — rather than what owns it, the arm seeing only that no
        # destination was open.
        logger.warning(
            "JATS parse of %s: %d attribution(s) were read and filed nowhere, "
            "so those credits are missing from the article (issues #241, #248)",
            article,
            handler.attributions_dropped,
        )

    if handler.elocation_parts_dropped:
        # Issue #265, at the siblings' level and granularity. It says what
        # bmlib stored — the first part — and never that the text is missing
        # from the article: a <mixed-citation> keeps it in `citation`.
        logger.warning(
            "JATS parse of %s: %d <elocation-id> part(s) did not continue the "
            "reference's own locator and were not stored in its elocation_id, "
            "which keeps the first (issue #265)",
            article,
            handler.elocation_parts_dropped,
        )

    if handler.last_pages_dropped:
        # Issue #272, at the siblings' level and granularity. Unlike the
        # <elocation-id> line above it *does* say the page number is missing
        # from the article, because <lpage> is not inline and no other field
        # carries it. It says what bmlib did — completed no range — rather
        # than which shape the document held, the arm seeing only that none
        # was open.
        logger.warning(
            "JATS parse of %s: %d <lpage> value(s) completed no page range this "
            "parser had open, so those page numbers are missing from the article "
            "(issue #272)",
            article,
            handler.last_pages_dropped,
        )

    if handler.non_publication_years_refused and not handler.year:
        # Issue #261, at the siblings' level and granularity — but gated on
        # what the refusal *cost*, not on the refusal. A refused date is the
        # first one deposited in 1,105 of the 8,118 served articles (13.6%,
        # about one in seven: 1,047 of them a `pmc-release` date and 58 a
        # `nihms-submitted` one), so a line per refusal would fire on one
        # article in seven and say nothing about a loss — 1,105 is the
        # population this counter counts, and the 1,047 row alone is the
        # subset the first cut of this comment reasoned from (PR #274's
        # review); where the article deposits no other dated <pub-date>,
        # the year goes blank where it used to be stored, and that is a drop
        # this module chose. Measured 0 articles over the served, archive and
        # back-filled artifacts, so the line is wholly prospective.
        logger.warning(
            "JATS parse of %s: %d <pub-date> year(s) name no publication date "
            "(a *-submitted or *-release type) and no other <pub-date> supplied a "
            "year, so no year was stored (issue #261)",
            article,
            handler.non_publication_years_refused,
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
            elocation_id=h.elocation_id,
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
            parts.extend(_format_exhibit_footnotes_html(fig.footnotes))
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
            parts.extend(_format_exhibit_footnotes_html(tbl.footnotes))
            parts.append("</div>")

    # References
    if h.references:
        parts.append("<h2>References</h2>")
        parts.append('<ol class="references">')
        for ref in h.references:
            parts.append(f'  <li id="ref-{html_escape(ref.id)}">{_format_ref_html(ref)}</li>')
        parts.append("</ol>")

    return "\n".join(parts)


def _format_exhibit_footnotes_html(footnotes: list[str]) -> list[str]:
    """Render an exhibit's footnotes as the block a publisher prints (issue #124).

    Two things deposited in the same block still reach nothing and are
    counted by nothing — the block's own ``<title>`` and a ``<graphic>`` the
    ``<fn>`` owns — which is **#238**, filed rather than fixed here because
    each is consistent with a rule settled elsewhere (#125/#130 for the title,
    #127's opaque owner for the graphic) and undoing either moves stored
    values. Measured 0 served and 8 and 7 respectively in 4 archive articles.

    A block after the exhibit's own content and *inside* its container —
    after ``</figcaption>`` but before ``</figure>``, and inside the table's
    wrapper — rather than folded into the caption, on two grounds. ("After the
    exhibit" alone read as a sibling, which is what a consumer writing a
    selector would have built against; PR #237's review.) It is where the
    publisher prints them, and it keeps caption and
    footnote distinguishable in the string ``FullTextService`` caches — which
    for a service consumer is the only place either is ever seen, since the
    service discards the :class:`~bmlib.fulltext.models.JATSArticle`. Folded
    into ``caption`` instead, a per-table funding note would read as part of
    the legend to every downstream that prints one.

    Emits nothing for an exhibit carrying none, the ``<figcaption>`` rule one
    branch up: an empty container asserts that something was deposited there.

    Args:
        footnotes: The exhibit's notes, each with its own marker already
            folded in.

    Returns:
        The lines to append, empty when there is nothing to print.
    """
    if not footnotes:
        return []
    parts = ['  <div class="fn-group">']
    parts.extend(f"    <p>{html_escape(note)}</p>" for note in footnotes)
    parts.append("  </div>")
    return parts


def _format_journal_html(h: JATSArticle) -> str:
    parts: list[str] = []
    if h.journal:
        parts.append(f"<em>{html_escape(h.journal)}</em>")
    vol_parts: list[str] = []
    if h.volume:
        vol_parts.append(h.volume)
    if h.issue:
        vol_parts.append(f"({h.issue})")
    # One locator, the page range where there is one, and separated only from
    # something it follows: an article carrying no volume or issue rendered
    # `: 100-101` (issue #265).
    locator = h.pages or h.elocation_id
    if locator:
        vol_parts.append(f": {locator}" if vol_parts else locator)
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
    vol = ref._volume_info
    if vol:
        parts.append(html_escape(vol))
    if ref.doi:
        parts.append(
            f'<a href="https://doi.org/{html_escape(ref.doi)}">doi:{html_escape(ref.doi)}</a>'
        )
    if ref._defers_to_the_deposit(len(parts)):
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
