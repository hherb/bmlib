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
    """Parsed body section with nested subsections.

    ``title`` is a heading the document deposited, never one this library
    derived: a ``<sec>``'s own ``<title>``, or — for unsectioned prose in
    ``<body>``, ``<back>`` and ``<front>`` (issues #224, #230) — the heading
    the element holding that prose deposited, an ``<ack>``'s
    *Acknowledgements*, a ``<glossary>``'s *Abbreviations* or an unsectioned
    body's ``<def-list>`` heading (issue #231). Prose the document heads with
    nothing keeps the empty string, because nothing is invented for it
    (issues #116, #162), so a caller rendering these must handle an untitled
    section rather than substituting a name of its own — and two untitled
    sections may be adjacent, loose ``<body>`` prose and loose ``<back>``
    prose being two sections with nothing to head either.

    The list is not only the ``<body>``'s. Back matter follows the body and
    front matter precedes it, both in document order; ``JATSArticle.has_body``
    is the field that answers whether there is a body at all.
    """

    title: str
    paragraphs: list[str] = field(default_factory=list)
    subsections: list[JATSBodySection] = field(default_factory=list)


@dataclass
class JATSFigureInfo:
    """Parsed figure metadata.

    ``footnotes`` holds the notes deposited inside the ``<fig>`` — JATS admits
    ``<fn>`` there directly, with no wrapper — each with its own marker folded
    into it (issue #124), and the figure's own ``<attrib>`` or its image's —
    ``"Source: Authors' elaboration."``, an abbreviation list — in document
    order (issues #241, #248). See :class:`JATSTableInfo`, which carries the
    argument. **The two halves are measured far apart**: a marked note is
    deposited almost never on a figure in the rendition this parser is fed (2
    across 8,118 served articles, against 16,933 on the table side), while an
    attribution is the figure side's larger population by far (125 served, 677
    across 97,909 archive articles). An attribution used to weld into the
    sentence around a figure deposited in a ``<p>``, or reach nothing where
    the figure stood in a section. The field exists on both exhibits because
    one shared holder in the parser is what stops the two drifting apart.
    """

    id: str
    label: str
    caption: str
    graphic_url: str | None = None
    footnotes: list[str] = field(default_factory=list)


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

    ``footnotes`` holds the table's own notes — a ``<table-wrap-foot>``'s
    ``<fn>`` prose, an ``<fn-group>`` (which JATS admits and neither measured
    artifact deposits inside an exhibit — see ``_EXHIBIT_FOOTNOTE_CONTAINERS``),
    the
    general note deposited as a loose ``<p>`` after the last marked one, and
    the table's own ``<attrib>`` or one inside its ``<table-wrap-foot>``
    (issues #241, #248: 21 served and 192 archive attributions) — in document
    order, with each marked note's marker folded into its own string, ``"a —
    Adjusted for age."`` (issue #124). An attribution carries no marker, and an
    image credit inside a marked note is filed ahead of that note's prose
    unless the credit is all the note deposits, when it takes the marker. The
    counts below are #124's and predate attributions being filed here. Before
    it the prose reached nothing at all: the ``<p>``
    handler drops exhibit internals so that a cell is not printed twice, which
    is right for a cell and wrong for a note. **The marker is folded rather
    than modelled** because ``<sup>`` is an inline element flattened into the
    surrounding cell, so the rendered body still reads ``12.3a`` and a
    reference to nothing is worse than a blank — the rule issue #116
    established for a swallowed label, and the shape issue #228 settled one
    container over for a definition's ``<term>``. The separator was chosen by
    measuring the deposit: **2 of 16,947** footnote paragraphs in the served
    artifact contain ``" — "`` against 47 containing a spaced hyphen and 4,133
    a colon, so the em dash collides an order of magnitude less often than
    either alternative and is not a free choice.

    **But splitting on it does not recover the marker, and an earlier draft
    said it did.** ``" — "`` is also ``_DEFINITION_SEPARATOR``: issue #228
    folds a ``<def-list>``'s ``<term>`` into its definition with the same
    string, and that fold runs *before* this one, so an abbreviations list
    deposited in a ``<table-wrap-foot>`` emits ``"BMI — body mass index"``
    carrying no marker at all. Measured on what the parser *emits* rather than
    on the deposit — which is the population the claim is about — **68 of the
    16,935 notes, in 10 of the 8,118 served articles**, carry the separator
    with no marker folded; the archive side is the 926 notes
    ``definition_terms_dropped`` shed when #124 landed. A consumer splitting
    unguarded reads ``BMI`` as a footnote marker. Split only where the prefix
    is marker-shaped, or read the deposit. The three-way case, a marked note
    whose prose is itself a folded definition, measures **0 of 16,935** and
    would be ambiguous either way (PR #237's review).

    The population is the largest this parser has recovered since issue #224:
    **16,935 paragraphs in 3,707 of 8,118 served articles (45.7%)**, 2.37 MB of
    prose, over Europe PMC's ``PMC10030002_PMC10040000.xml.gz`` — the routing
    tally, not the markup survey's 16,947, which over-counts by 12 where a
    note deposits a ``<def-list>`` inside a ``<p>``. Table
    footnotes carry the abbreviation expansions without which the cells are
    unreadable, and the per-table funding and disclosure notes
    ``bmlib.transparency`` scans for — that module reads the raw XML itself
    (issue #119), so this costs it nothing and would have cost any other
    consumer of :class:`JATSArticle` everything.

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
    footnotes: list[str] = field(default_factory=list)


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
    #: assembles from the structured fields with a separator of its own — where
    #: at least two of them would print, or where one would and this field is
    #: empty; with one component and a deposited string it returns *this* field
    #: instead (issue #268). Prefer this field where the publisher's own wording
    #: matters, and :attr:`formatted_citation` where consistent presentation
    #: does.
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
    #: The cited work's ``<elocation-id>``, an electronic locator such as
    #: ``e0230000`` (issue #265). Declared last so positional construction
    #: written before it keeps working. Kept apart from ``first_page``, which a
    #: caller reads as a page. The renderers print it only where there is no
    #: ``first_page``, which keeps every reference depositing both rendered as
    #: it was: there neither element is reliably the locator — the
    #: ``<elocation-id>`` is, among other shapes, the ``<fpage>``'s own value, a
    #: DOI or PII, an issue number or supplement suffix beside a range, or the
    #: true article number beside an issue deposited as ``<fpage>``. Nor do they
    #: print it *alone* in place of a deposited ``citation`` — but that is no
    #: longer a rule about locators: it is :meth:`_defers_to_the_deposit`, which
    #: prints the deposit wherever fewer than two components would print at all
    #: **and there is a deposit to print** (issue #268); an
    #: ``<element-citation>`` leaves ``citation`` empty, and there a lone
    #: locator is still printed. A ``volume`` or a ``first_page`` beside it does
    #: not lift it out of that rule: the volume prefixes the locator and a page
    #: range replaces it, so all three are inside the one ``_volume_info`` run
    #: rather than beside it and the reference has one printed component — so
    #: such a reference prints its deposit *now*, where it printed ``15:e7`` or
    #: ``5`` before #268.
    elocation_id: str = ""

    def _defers_to_the_deposit(self, printed_part_count: int) -> bool:
        """Would a rendering of that many components print ``citation`` instead?

        Package-internal, and the one statement of the rule: read by
        :attr:`formatted_citation` and by ``jats_parser._format_ref_html``,
        each passing the length of the parts list it has just built.

        **One component is never a citation.** A ``<mixed-citation>`` tagging
        just one of its structured children rendered that child *in place of*
        the whole deposited string — a bare ``(2023)`` for an IRENA report, an
        author list for a work the rendering then never names, a journal name
        for a citation carrying a URL (issue #268). Where one component is all
        a renderer would print and there is a deposited string, both print the
        deposit: 828 of the served artifact's 174,458 references that carry a
        deposited string and render structured (346 of 8,118 articles) and
        15,748 of 2,975,128 in the archive one (5,573 of 97,909) — the
        denominator is that wider population, not the one-component one, of
        which this moves essentially all. Issue #265 made this rule for a lone
        ``<elocation-id>`` and #268 generalised it; the residual — a *pair*
        naming no work, such as ``authors`` and ``year`` — is filed as #276
        rather than taken, since two components carry shapes that read as
        citations (``authors``+``article_title``, ``authors``+``doi``) and
        shapes that do not.

        **The argument is about what a renderer prints, not about which fields
        are populated**, so the count comes from the renderer rather than from
        a list here. Its ancestor was such a list and drifted in the commit
        that wrote it, listing ``issue`` — which neither renderer prints
        without a ``volume`` — so a reference tagging an issue beside a locator
        printed the bare locator (PR #269's review). ``volume`` and
        ``first_page`` are the same trap from the other side: two populated
        fields, one printed run (``15:123``), and a locator with no work
        attached is what the rule refuses.

        With **no** parts at all the deposit is printed whether or not there is
        one, which is what the renderers did before either issue: an
        ``<element-citation>`` leaves ``citation`` empty and a reference
        tagging nothing has nothing else to say. So the method answers *True*
        there even with nothing to defer to, and the name overstates that one
        case; the alternative is a renderer returning a value no caller asked
        for.
        """
        # The first arm is **prospective and behaviourally equivalent today**:
        # both call sites join their parts with ``". ".join``, which is ``""``
        # for an empty list, so at zero parts they print the same empty string
        # whichever arm decides — ``printed_part_count < 2 and
        # bool(self.citation)`` passes the whole suite (4,237 tests, measured
        # in PR #277's review, so it is an equivalent mutant and not an
        # unobserved one). It is kept because it states the zero-part rule
        # where a reader looks for it, and because a later renderer whose
        # zero-part output is not the empty string would need it.
        return not printed_part_count or (printed_part_count == 1 and bool(self.citation))

    @property
    def _volume_info(self) -> str:
        """The ``volume(issue):locator`` run, unescaped, as both renderers print it.

        Package-internal, shared by :attr:`formatted_citation` and
        ``jats_parser._format_ref_html`` so the locator rule is stated once: the
        page range where there is one, else ``elocation_id`` (see that field
        for why the range wins). An issue is printed only after a volume.
        """
        volume_info = ""
        if self.volume:
            volume_info = self.volume
            if self.issue:
                volume_info += f"({self.issue})"
        page_range = self.first_page
        if page_range and self.last_page:
            page_range += f"-{self.last_page}"
        locator = page_range or self.elocation_id
        if locator:
            volume_info = f"{volume_info}:{locator}" if volume_info else locator
        return volume_info

    @property
    def formatted_citation(self) -> str:
        """The reference as one plain string, assembled from its structured fields.

        Authors (the first two and ``et al.`` beyond three), title, source,
        ``(year)``, :attr:`_volume_info` and ``doi:``, joined with ``". "``.
        The deposited :attr:`citation` is printed instead where fewer than two
        of those would print at all — see :meth:`_defers_to_the_deposit`. An
        ``<element-citation>`` leaves ``citation`` empty, so a lone component
        is printed there, being all there is.
        """
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
        volume_info = self._volume_info
        if volume_info:
            parts.append(volume_info)
        if self.doi:
            parts.append(f"doi:{self.doi}")
        if self._defers_to_the_deposit(len(parts)):
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
    # cleanly but holds no article prose — its body_sections may still carry
    # front and back matter (author notes, acknowledgements; issues #224,
    # #230) — so callers must not mistake it for full text.
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
    # The article's own <elocation-id>: the electronic locator JATS deposits
    # *in place of* a page range, so in valid JATS, and in every article of the
    # four artifacts issue #265 measured, `pages` is blank where this is set.
    # The parser does not enforce that: an invalid deposit carrying both keeps
    # both, and the rendered journal line prints `pages`. An article paginated
    # that way used to store no locator at all: 4,869 of the 8,118 served
    # articles of Europe PMC's `PMC10030002_PMC10040000.xml.gz`, and 81,934 of
    # the 97,909 of PMC's `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`.
    # A field of its own rather than folded into `pages`, which a downstream
    # reads and formats as a page range, and `e0123456` is not one. Declared
    # last so a construction written before it keeps working.
    elocation_id: str = ""
    # The article's own <funding-statement>s, in document order and
    # whitespace-normalised (issue #257). A funding disclosure reached no
    # field before: 1,337 of the 8,118 served articles of
    # `PMC10030002_PMC10040000.xml.gz` and 41,260 of the 97,909 of
    # `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` carried one that
    # reached nothing. Only the statement: the structured <award-group>
    # (funder, award id) is not modelled (#284). Where the publisher repeats
    # the sentence in the article's prose as well — mostly a back-matter
    # *Funding* note or section — it is here *and* in `body_sections`, as
    # deposited. A <p> inside a statement (invalid in JATS 1.3; 1 archive
    # statement, whose funders sit in list items) still routes as front prose,
    # so that one statement is split between this field and `body_sections`.
    funding_statements: list[str] = field(default_factory=list)


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
