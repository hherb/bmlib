#!/usr/bin/env python3
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Count the structured names a JATS document prints, by where the parser sends them.

Issue #382: ``<surname>`` and ``<given-names>`` each accumulate their own text,
and the arms that read it fire only inside a reference's ``<person-group>`` or a
``<contrib>`` that *owns* the name. Everywhere else the buffered text survives
only if the pop merges it into the buffer around it, which it does inside a
mixed-content citation — a ``<mixed-citation>`` or, since #390, an NLM 2.x
``<citation>`` — or a related work, and nowhere else; a table cell keeps it
by a separate route, ``characters()`` writing the cell directly. So a name
printed in prose is cut out of the sentence. #382 asked for the population
before a fix is chosen, and this walk counts it.

**The unit is a name-part holder**: every element that carries a ``<surname>``
or ``<given-names>`` child, counted once — a ``<name>``, and also a
``<string-name>`` depositing its parts, which loses them by the same route
while the ``<string-name>``'s own loose text stays in the sentence. The holder's
tag is reported, so the two are never pooled unseen.

Each holder is put in exactly one **context**, and each context has one
**fate**, decided in the parser's own order (``bmlib/fulltext/jats_parser.py``,
the ``surname`` and ``given-names`` arms of ``endElement`` and the pop above
them):

- ``read`` — a field stores the name: an author of the reference, written by
  the *first* citation element of a ``<ref>`` (PR #387's positions, with #149's
  first-wins and #270's related-work refusal) or by a later alternative of the
  ``<citation-alternatives>`` group holding it, filling an author list still
  empty (#407, #417) — or an author ``<contrib>``'s own name
  (``_contrib_owns_name``). Which cited names reach the list is decided by the
  parser's *state*, so :func:`walk` runs its reference arms event for event
  rather than reading the path; a name an arm reads but never appends — given
  names awaiting a surname that never comes — falls to the context its
  citation element gives it. A non-author contributor's own name is declined
  rather than read (``contributor-declined``), and is not prose either way.
- ``kept`` — no field reads them, but the text reaches output: a table cell,
  or a related work sitting in a ``<p>``, whose parts merge into the sentence.
- ``glued`` — kept, with surname and given names welded into one word: a
  ``<mixed-citation>`` in a ``<ref>`` (#314), a ``<ref>``'s *typeset*
  ``<citation>`` (#390, which writes its string only then), or a
  ``<citation>`` printed in a paragraph, which merges back into the sentence.
- ``dropped`` — the text reaches nothing.

**The contexts deliberately do not follow the reader's arms one-to-one**, and
this is the correction PR #389's review made: its first cut filed every
``<citation>``, every ``<element-citation>`` and every citation outside a
``<ref>`` as read or glued, and 3.7 million names the parser dropped at the time
were reported as kept (PR #387 has since made the bare ``<name>`` in a citation
an author, which this walk follows). A routing-agreement test
(``tests/test_prose_name_sampler.py``) now parses one fixture per context with
the real parser and holds each fate to what the parse kept — stored as an
author for ``read``, printed elsewhere for ``kept`` and ``glued``, absent for
``dropped``. The parser is imported by the *test*, never by this script, which
restates the sets it needs, as every sampler here does; the same test holds
each restated routing set to the parser's own.

Suppressed regions (``<sub-article>``, ``<response>``) are skipped exactly as
the parser skips them, and counted separately.

Each artifact is a directory of ``.xml``/``.nxml`` files, a tar of them (a PMC
OA package), or a gzipped ``<articles>`` concatenation (a Europe PMC served
bundle). The exit status is non-zero when any artifact had a document that
would not parse, a bundle whose split disagrees with its own opener count, or no
document at all — a run that measured nothing must not print zeros at exit 0.

Usage (from the repository root)::

    .venv/bin/python scripts/measure_jats_prose_names.py \\
        ~/pmc_archive/packages/oa_comm_xml.PMC000xxxxxx.baseline.2025-06-26.tar.gz \\
        ~/europepmc/packages/PMC10030002_PMC10040000.xml.gz
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

# The name parts whose buffers #382 is about.
NAME_PARTS = frozenset({"surname", "given-names"})

# Restated from the parser rather than imported; see the module docstring.
# `tests/test_prose_name_sampler.py` holds every set below that the parser
# names to the parser's own, since a restated routing set that drifts
# measures a parser that no longer exists.
# Regions the parser suppresses whole (`_NESTED_ARTICLE_ELEMENTS`).
NESTED_ARTICLES = frozenset({"sub-article", "response"})
# The citation elements the parser reads in a `<ref>` (`_CITATION_ELEMENTS`),
# NLM 2.x's `<citation>` and NLM 3.0's `<nlm-citation>` included since #390.
READ_CITATIONS = frozenset({"mixed-citation", "element-citation", "citation", "nlm-citation"})
# The two whose descendants merge into their buffer (`_MIXED_CONTENT_CITATIONS`);
# the other two are element-only.
MIXED_CITATIONS = frozenset({"mixed-citation", "citation"})
# Another work described in place (`_RELATED_WORK_ELEMENTS`).
RELATED_WORK = frozenset({"related-article", "related-object", "product"})
# What a `<contrib>` holds about its contributor (`_CONTRIBUTOR_PROSE`).
CONTRIBUTOR_PROSE = frozenset({"bio", "author-comment", "p"})
# Cells `characters()` writes directly while a `<table-wrap>` is open
# (`_TABLE_CELL_ELEMENTS`).
TABLE_CELLS = frozenset({"td", "th"})
# Every element taking a text buffer of its own (`_TEXT_ACCUMULATING`).
TEXT_ACCUMULATING = frozenset(
    {
        "abstract", "alt-text", "alt-title", "article-id", "article-title", "attrib",
        "award-id", "b", "bold", "caption", "citation", "code", "collab", "contrib-group",
        "def", "disp-formula", "element-citation", "elocation-id", "email", "ext-link",
        "fpage", "funding-source", "funding-statement", "given-names", "i",
        "inline-formula", "institution-id", "issue", "italic", "journal-title", "kwd",
        "label", "list-item", "long-desc", "lpage", "mixed-citation", "monospace",
        "named-content", "nlm-citation", "object-id", "p", "permissions", "person-group",
        "pub-id", "sec", "source", "string-name", "sub", "sup", "support-source",
        "surname", "td", "term", "tex-math", "th", "title", "uri", "volume", "xref",
        "year",
    }
)  # fmt: skip
# Those whose buffer merges into the one around them wherever they stand
# (`_INLINE_ELEMENTS`).
INLINE_ELEMENTS = frozenset(
    {
        "award-id", "b", "bold", "code", "collab", "elocation-id", "email", "ext-link",
        "funding-source", "i", "inline-formula", "italic", "monospace", "named-content",
        "string-name", "sub", "sup", "support-source", "uri", "xref",
    }
)  # fmt: skip
# A formula's elements, which merge nothing (`_FORMULA_PARTS`): the formula arm
# appends the one rendition it chooses (#147).
FORMULA_PARTS = frozenset({"inline-formula", "disp-formula", "tex-math"})
# What a name arm cuts out of its buffer (`_without_notes(name=True)`): what
# merges out of an `<fn>` (`_NOTE_ELEMENTS`), and every `<xref>` and
# `<contrib-group>` (`_NOT_A_NAMES_TEXT`), #423, #425, #429. A note-type `<xref>`
# (`_NOTE_XREF_TYPES`) is an `<xref>` already and adds nothing to a name's cut.
NOTE_ELEMENTS = frozenset({"fn"})
NOT_A_NAMES_TEXT = frozenset({"xref", "contrib-group"})
# An `<xref>` whose text `endElement` replaces with a link rather than merging
# (its `is_fig_table_xref`, a literal there and so not pinned).
FIG_TABLE_XREF_TYPES = frozenset({"fig", "figure", "table", "table-wrap"})
# Where the article's own contributor list stands (`_ARTICLE_META`).
ARTICLE_META = ("front", "article-meta")

READ, KEPT, GLUED, DROPPED = "read", "kept", "glued", "dropped"


@dataclass(frozen=True)
class Context:
    """One place a name-part holder can sit, and what the parser does with it."""

    name: str
    fate: str
    note: str


CONTEXTS = (
    Context(
        "citation-author",
        READ,
        "a name a <ref>'s author list stores: its first citation's, or a later"
        " alternative's filling an empty list (#407)",
    ),
    Context("contributor-own", READ, "an author <contrib>'s own name"),
    Context(
        "contributor-overwritten",
        DROPPED,
        "an author's own name overwritten by a later one: <name-alternatives> (#143)",
    ),
    Context(
        "contributor-declined",
        DROPPED,
        "a non-author <contrib>'s own name, declined by design (#111, #266)",
    ),
    Context("table-cell", KEPT, "a <td>/<th> in a <table-wrap>: characters() writes the cell"),
    Context("related-work-in-prose", KEPT, "a related work in a <p> merges into the sentence"),
    Context("mixed-citation-glued", GLUED, "a <ref>'s <mixed-citation>, not an author (#314)"),
    Context("element-citation-unread", DROPPED, "a <ref>'s <element-citation>, not an author"),
    Context("nlm-citation-glued", GLUED, "a <ref>'s typeset <citation>, not an author (#390)"),
    Context(
        "nlm-citation-unread",
        DROPPED,
        "a <ref>'s element-only <citation> or <nlm-citation>, not an author (#390)",
    ),
    Context(
        "nlm-citation-in-prose",
        GLUED,
        "a <citation> in a <p> outside any <ref> merges into the sentence (#390)",
    ),
    Context(
        "isolated-cell",
        DROPPED,
        "a <td>/<th> outside any <table-wrap>: isolated (#243), and no table takes it",
    ),
    Context(
        "reference-discarded",
        DROPPED,
        "a <ref>'s citation whose reference the parser never builds: a <ref> nested in another",
    ),
    Context("citation-in-prose", DROPPED, "a citation element outside any <ref>"),
    Context(
        "related-work-metadata",
        DROPPED,
        "a related work outside routed prose (<article-meta>, a <ref-list>'s own <p>)",
    ),
    Context("contributor-prose", DROPPED, "a <contrib>'s <bio>/<author-comment>/<p> (#382)"),
    Context("other", DROPPED, "anywhere else (#382); reported by path"),
)
FATES = {context.name: context.fate for context in CONTEXTS}

# How many distinct ancestor paths to print per dropped context before the
# remainder line. Every path is counted; this only bounds the listing.
PATHS_SHOWN = 15


def strip_namespace(tag: str) -> str:
    """``{ns}name`` -> ``name``."""
    return tag.split("}", 1)[1] if "}" in tag else tag


@dataclass
class ReadStats:
    """What reading one artifact found beside the documents it yielded."""

    skipped_members: int = 0
    openers: int = 0
    closers: int = 0


_OPENER = re.compile(r"<article[\s>]")
_ARTICLE = re.compile(r"<article[\s>].*?</article>", re.S)


def articles(path: Path, stats: ReadStats) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, bytes)`` for every article in an artifact.

    A directory or a tar yields its ``.xml``/``.nxml`` files, counting every
    other regular file in ``stats.skipped_members``. Anything else is read as a
    gzipped concatenation, decoded strictly, and split on the root element —
    ``<article`` followed by whitespace or ``>``, since a bare word boundary also
    matches ``<article-meta``. ``stats.openers``/``stats.closers`` count the roots
    independently of the split, so an article missing its end tag (which the
    non-greedy match would weld onto the next) shows as a disagreement.
    """
    suffixes = (".xml", ".nxml")
    if path.is_dir():
        for member in sorted(path.rglob("*")):
            if member.is_file():
                if member.name.endswith(suffixes):
                    yield member.name, member.read_bytes()
                else:
                    stats.skipped_members += 1
        return
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as tar:
            for info in tar:
                if not info.isfile():
                    continue
                handle = tar.extractfile(info)
                if handle is None or not info.name.endswith(suffixes):
                    stats.skipped_members += 1
                    continue
                yield info.name, handle.read()
        return
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        text = stream.read()
    stats.openers = len(_OPENER.findall(text))
    stats.closers = text.count("</article>")
    for index, match in enumerate(_ARTICLE.finditer(text)):
        yield f"article_{index}", match.group(0).encode()


@dataclass(frozen=True)
class Holder:
    """One name-part holder: its own tag, the path above it, and what became of it.

    ``typeset_citations`` holds the indices in ``ancestors`` of ``<citation>``
    elements carrying character data of their own, which is what decides
    whether the parser writes that one's string (#390) — not in the path.

    The other three are what :func:`walk`'s mirror of the parser's state
    concluded, none of it in the path either. ``read_as_author``: the holder's
    parts, or its own text, reached an author the reference stores — the first
    citation element's, or a later alternative's filling an empty author list
    (#149, #407) — or it was read there and carries no text to lose. A holder
    is one unit, so one whose part reached an author is read whole; only a
    second surname in one ``<name>``, which JATS does not admit, loses a part
    of a name the parser stores.
    ``reference_built``: false under a ``<ref>`` whose reference the parser
    never builds, one ``<ref>`` nested in another replacing it.
    ``collected_contributor``: the innermost open ``<contrib>`` is one the
    parser collects as an author (#111, #266). ``contributor_overwritten``:
    its parts were read into that author and a later name overwrote every one
    — the first spelling of a ``<name-alternatives>`` (#143).
    """

    tag: str
    ancestors: tuple[str, ...]
    typeset_citations: frozenset[int] = frozenset()
    read_as_author: bool = False
    reference_built: bool = True
    collected_contributor: bool = False
    contributor_overwritten: bool = False


@dataclass
class Walk:
    """Every holder in one document, and how many the suppression removed."""

    holders: list[Holder] = field(default_factory=list)
    suppressed: int = 0


def _count_holders(element: ET.Element) -> int:
    return sum(
        1
        for node in element.iter()
        if any(strip_namespace(child.tag) in NAME_PARTS for child in node)
    )


@dataclass
class _Authors:
    """A ``_ReferenceBuilder``'s author list, as far as this walk needs it.

    The count, the two pending parts (``current_author_surname`` and
    ``current_author_given_names``, of which only emptiness decides
    anything), the holder each pending part came from, and ``stored``: the
    holders whose text reached an appended author.
    """

    count: int = 0
    surname: bool = False
    given: bool = False
    surname_owner: int | None = None
    given_owner: int | None = None
    stored: set[int] = field(default_factory=set)

    def set_part(self, part: str, present: bool, owner: int | None) -> None:
        """A closing ``<surname>`` or ``<given-names>``, which overwrites."""
        if part == "surname":
            self.surname, self.surname_owner = present, owner if present else None
        else:
            self.given, self.given_owner = present, owner if present else None

    def finish(self, *, closes_a_name: bool = False) -> None:
        """Mirror ``finish_current_author``: nothing happens without a surname
        unless a name closes, and the parts clear only when it does happen."""
        if not (self.surname or closes_a_name):
            return
        if self.surname or self.given:
            self.count += 1
            for owner in (self.surname_owner, self.given_owner):
                if owner is not None:
                    self.stored.add(owner)
        self.surname = self.given = False
        self.surname_owner = self.given_owner = None

    def append(self, owner: int | None) -> None:
        """A cited ``<collab>`` or undivided ``<string-name>`` appended whole."""
        self.count += 1
        if owner is not None:
            self.stored.add(owner)


@dataclass
class _Frame:
    """One open citation element of the reference (``_CitationFrame``).

    ``authors`` is where its names go: the reference's own list for the first
    citation element, a scratch list for a later alternative in the first's
    ``<citation-alternatives>`` group (#407), and nowhere for any other — a
    later part (#149), or one nested in another, which cites another work
    (``cites_another_work``, #414).
    """

    authors: _Authors | None
    alternative: bool = False


@dataclass
class _Reference:
    """The one reference the parser holds open (``current_reference``).

    Counted as ``_ReferenceBuilder`` counts it (issue #417): ``parts`` is
    ``citation_element_count``, which a nested citation does not raise — not
    observable here, a nested citation's parent lying inside the outer one and
    so never being the first's group, but kept as the parser keeps it — and
    ``groups_opened``/``open_groups``/``first_group`` are the
    ``<citation-alternatives>`` numbering of #407. ``holders`` lists every
    holder opened while this reference was the open one, and ``routed`` maps
    each whose parts or text the reference's arms read to whether it carries
    any text.
    """

    authors: _Authors = field(default_factory=_Authors)
    parts: int = 0
    groups_opened: int = 0
    open_groups: list[int] = field(default_factory=list)
    first_group: int = 0
    frames: list[_Frame] = field(default_factory=list)
    holders: list[int] = field(default_factory=list)
    routed: dict[int, bool] = field(default_factory=dict)


@dataclass
class _Contrib:
    """One open ``<contrib>`` (``_ContribFrame``): whether the parser collects
    it as an author, its author's two name parts — last writer wins, an empty
    part included, as ``_AuthorBuilder``'s are — and, for each holder whose
    parts it read, whether that holder prints anything."""

    collected: bool
    parts: _Authors = field(default_factory=_Authors)
    routed: dict[int, bool] = field(default_factory=dict)


@dataclass
class _ParserState:
    """The parser state the reference and contributor arms read.

    ``in_person_group`` is ``in_ref_person_group``, one flag as there.
    ``contrib_groups`` holds each open ``<contrib-group>``'s ``content-type``,
    and ``contribs`` each open ``<contrib>``.
    ``holder_index`` maps a holder element to its index, so a closing part
    names the holder it came from.
    """

    reference: _Reference | None = None
    in_person_group: bool = False
    contrib_groups: list[str | None] = field(default_factory=list)
    contribs: list[_Contrib] = field(default_factory=list)
    holder_index: dict[int, int] = field(default_factory=dict)

    def route(self, element: ET.Element) -> int | None:
        """Note that the reference's arms read ``element``, if it is a holder,
        and return its index. Noted at the read itself, under the state then
        open, because that is when the parser decides: a holder opening before
        the ``<person-group>`` that sets the flag is not yet read at its open.
        Whether the holder prints anything is what says whether a name that
        never reaches the list loses text."""
        index = self.holder_index.get(id(element))
        if index is not None and self.reference is not None:
            self.reference.routed[index] = bool("".join(element.itertext()).strip())
        return index


def walk(root: ET.Element) -> Walk:
    """Collect every name-part holder in document order, iteratively.

    Iterative so a deep document cannot raise ``RecursionError`` part-way
    through an artifact. A ``<sub-article>``/``<response>`` subtree is not
    entered; its holders are counted in ``suppressed``.

    What becomes of a cited name is decided by **state, not by the path**, so
    the walk runs the parser's reference arms event for event (issue #417):
    the one open reference, its citation frames and their numbering, the
    ``<citation-alternatives>`` groups, the person-group flag, every
    ``<surname>``, ``<given-names>``, ``<name>``, ``<person-group>``,
    ``<collab>`` and ``<string-name>`` close that writes the author list, the
    fill from an alternative at its close, and the flush at ``</ref>``. A path
    rule approximated this twice and was wrong both ways: a later alternative
    is read only while the list is empty when it closes (``fill_empty_fields_from``
    takes it whole), and a part reaches the list only where
    ``finish_current_author`` appends — given names in a ``<person-group>``
    wait for a surname and are lost at ``</ref>`` without one.
    """
    result = Walk()
    state = _ParserState()
    ancestors: list[str] = []
    elements: list[ET.Element] = []
    typeset: set[int] = set()
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:
        element, leaving = stack.pop()
        tag = strip_namespace(element.tag)
        if leaving:
            _close(state, result, elements, tuple(ancestors))
            ancestors.pop()
            elements.pop()
            typeset.discard(len(ancestors))
            continue
        if tag in NESTED_ARTICLES:
            result.suppressed += _count_holders(element)
            continue
        path = (*ancestors, tag)
        _open(state, result, element, path)
        if tag == "citation" and _carries_text_of_its_own(element):
            typeset.add(len(ancestors))
        if any(strip_namespace(child.tag) in NAME_PARTS for child in element):
            _record_holder(state, result, element, path, frozenset(typeset))
        ancestors.append(tag)
        elements.append(element)
        stack.append((element, True))
        stack.extend((child, False) for child in reversed(element))
    return result


def _open(state: _ParserState, result: Walk, element: ET.Element, path: tuple[str, ...]) -> None:
    """The parser's open arms that this walk mirrors, ``path`` ending with the element."""
    tag, above = path[-1], path[:-1]
    reference = state.reference
    if tag == "ref":
        if reference is not None:
            # One reference is held open, so this one replaces it, and the
            # one it replaces is never built.
            for index in reference.holders:
                result.holders[index] = replace(result.holders[index], reference_built=False)
        state.reference = _Reference()
    elif tag in READ_CITATIONS and reference is not None:
        if reference.frames:
            # In another's note, so it cites another work (#414).
            reference.frames.append(_Frame(None))
            return
        reference.parts += 1
        in_group = bool(above) and above[-1] == "citation-alternatives"
        group = reference.open_groups[-1] if in_group and reference.open_groups else 0
        if reference.parts == 1:
            reference.first_group = group
            reference.frames.append(_Frame(reference.authors))
        elif group and group == reference.first_group:
            reference.frames.append(_Frame(_Authors(), alternative=True))
        else:
            reference.frames.append(_Frame(None))
    elif tag == "citation-alternatives" and reference is not None:
        reference.groups_opened += 1
        reference.open_groups.append(reference.groups_opened)
    elif tag == "person-group":
        if _cited_authors(state, above) is not None:
            state.in_person_group = True
    elif tag == "contrib-group":
        state.contrib_groups.append(element.get("content-type"))
    elif tag == "contrib":
        state.contribs.append(
            _Contrib(_in_articles_contributor_list(path) and _is_author_contrib(state, element))
        )


def _record_holder(
    state: _ParserState,
    result: Walk,
    element: ET.Element,
    path: tuple[str, ...],
    typeset: frozenset[int],
) -> None:
    """Add one holder, listed under the reference open, if any."""
    tag, above = path[-1], path[:-1]
    index = len(result.holders)
    state.holder_index[id(element)] = index
    reference = state.reference
    result.holders.append(
        Holder(
            tag,
            above,
            typeset,
            reference_built=reference is not None or "ref" not in above,
            collected_contributor=bool(state.contribs) and state.contribs[-1].collected,
        )
    )
    if reference is not None:
        reference.holders.append(index)


def _close(
    state: _ParserState, result: Walk, elements: list[ET.Element], path: tuple[str, ...]
) -> None:
    """The parser's close arms that this walk mirrors, ``path`` ending with the element."""
    tag, above = path[-1], path[:-1]
    element = elements[-1]
    reference = state.reference
    if tag in NAME_PARTS:
        authors = _cited_authors(state, above)
        if authors is not None and _reads_cited_parts(state, above):
            present = bool(_buffer_text(element, above)[0].strip())
            authors.set_part(tag, present, state.route(elements[-2]))
        elif state.contribs and state.contribs[-1].collected and _contrib_owns(above):
            # The contributor's own name: `in_contrib`, `current_author` and
            # `_contrib_owns_name`, asked only once the reference has refused.
            # (`collected` mirrors `current_author` and is not observable here:
            # a declined contributor's holders are never read as overwritten.)
            contrib = state.contribs[-1]
            owner = state.holder_index.get(id(elements[-2]))
            if owner is not None:
                contrib.routed[owner] = bool("".join(elements[-2].itertext()).strip())
            contrib.parts.set_part(tag, bool(_buffer_text(element, above)[0].strip()), owner)
    elif tag == "name":
        if (authors := _cited_authors(state, above)) is not None:
            authors.finish(closes_a_name=True)
    elif tag == "person-group":
        if (authors := _cited_authors(state, above)) is not None:
            authors.finish()
            state.in_person_group = False
    elif tag == "collab":
        if (authors := _cited_authors(state, above)) is not None:
            owner = state.route(element)
            if _buffer_text(element, above)[1].strip():
                authors.append(owner)
    elif tag == "string-name":
        if (authors := _cited_authors(state, above)) is not None:
            owner = state.route(element)
            if authors.surname or authors.given:
                # A divided one, flushed as a <name> is under an element-only
                # citation outside a <person-group> (#415).
                authors.finish(
                    closes_a_name=not state.in_person_group and _in_element_only_citation(path)
                )
            elif _buffer_text(element, above)[1].strip():
                authors.append(owner)
    elif tag in READ_CITATIONS and reference is not None and reference.frames:
        frame = reference.frames.pop()
        if frame.alternative and frame.authors is not None:
            # `fill_empty_fields_from`: the list whole, and only into an empty one.
            if not reference.authors.count and frame.authors.count:
                reference.authors.count = frame.authors.count
                reference.authors.stored |= frame.authors.stored
    elif tag == "citation-alternatives" and reference is not None and reference.open_groups:
        reference.open_groups.pop()
    elif tag == "ref":
        if reference is not None:
            # Nothing is pending here in a well-formed document — every
            # position that reads a surname closes and flushes before </ref> —
            # but the parser flushes, and so does this.
            reference.authors.finish()
            for index, carries_text in reference.routed.items():
                read = index in reference.authors.stored or not carries_text
                result.holders[index] = replace(result.holders[index], read_as_author=read)
        state.reference = None
        state.in_person_group = False
    elif tag == "contrib-group" and state.contrib_groups:
        state.contrib_groups.pop()
    elif tag == "contrib" and state.contribs:
        contrib = state.contribs.pop()
        kept = {contrib.parts.surname_owner, contrib.parts.given_owner}
        for index, carries_text in contrib.routed.items():
            if carries_text and index not in kept:
                result.holders[index] = replace(result.holders[index], contributor_overwritten=True)


def _cited_authors(state: _ParserState, above: tuple[str, ...]) -> _Authors | None:
    """Mirror ``_cited_reference``: the author list an element whose strict
    ancestors are ``above`` writes to, if any.

    The innermost open citation frame's list — none for a later part or a
    nested citation — refused where a related work stands nearer than any
    citation element (#270).
    """
    reference = state.reference
    if reference is None or not reference.frames:
        return None
    frame = reference.frames[-1]
    if frame.authors is None:
        return None
    nearest = _innermost(above, READ_CITATIONS | RELATED_WORK)
    if nearest >= 0 and above[nearest] in RELATED_WORK:
        return None
    return frame.authors


def _reads_cited_parts(state: _ParserState, above: tuple[str, ...]) -> bool:
    """Mirror ``_cited_name_part_reference``'s positions, where ``above`` ends
    with the part's parent: in the reference's ``<person-group>``, in a
    ``<name>``, or in a ``<string-name>`` under an element-only citation (#415)
    — elsewhere a ``<string-name>`` is read verbatim by its own arm."""
    parent = above[-1] if above else ""
    return (
        state.in_person_group
        or parent == "name"
        or (parent == "string-name" and _in_element_only_citation(above))
    )


def _in_element_only_citation(path: tuple[str, ...]) -> bool:
    """Mirror ``_in_element_only_citation``: is the nearest citation element on
    ``path``, its last element included, element-only?"""
    nearest = _innermost(path, READ_CITATIONS)
    return nearest >= 0 and path[nearest] not in MIXED_CITATIONS


def _in_articles_contributor_list(path: tuple[str, ...]) -> bool:
    """Mirror ``_in_articles_contributor_list``, ``path`` ending with the
    ``<contrib>``: the outermost ``<contrib-group>`` — or, with none, the
    ``<contrib>`` itself — stands directly in ``front > article-meta``."""
    anchor = path.index("contrib-group") if "contrib-group" in path else len(path) - 1
    return path[max(anchor - len(ARTICLE_META), 0) : anchor] == ARTICLE_META


def _is_author_contrib(state: _ParserState, element: ET.Element) -> bool:
    """Mirror ``_is_author_contrib``: the contributor's own ``contrib-type``
    decides, else the innermost group declaring one, else authors."""
    contrib_type = element.get("contrib-type")
    if contrib_type:
        return contrib_type.lower() == "author"
    group_type = next((t for t in reversed(state.contrib_groups) if t), None)
    return not group_type or group_type.lower() == "author"


def _buffer_text(element: ET.Element, above: tuple[str, ...]) -> tuple[str, str]:
    """The text an accumulating element's buffer holds at its close, whole and
    with a name arm's cut made — ``(raw, cut)``. Iterative, as :func:`walk` is.

    Mirrors the merge at the top of ``endElement``. Character data directly in
    the element, or in a descendant taking no buffer, lands in it; an
    accumulating descendant's buffer merges only where ``endElement`` merges
    it (see :func:`_merges`). The cut removes what merged marked: anything
    merging out of an ``<fn>`` and every ``<xref>`` and ``<contrib-group>``,
    with whatever they hold — but not an ``<fn>``'s own character data, which
    lands unmarked, as in the parser. A part's value is the cut text or, cut
    to nothing, the raw (``_name_part``), so its emptiness is the raw's; a
    ``<collab>``'s or ``<string-name>``'s is the cut's.

    Two approximations, each about a shape no draw has shown: a formula
    contributes all its text where the formula arm appends one rendition of
    it, and a funder identifier's ``<named-content>`` merges as any other does.
    """
    raw: list[str] = []
    cut: list[str] = []
    # Pieces in reverse document order, popped from the end: a text piece
    # (node None) or a node, with the path above it, whether it merged
    # marked, and whether an <fn> stands between it and the buffer.
    pending: list[tuple[ET.Element | None, str, tuple[str, ...], bool, bool]] = []
    _push_content(pending, element, (*above, strip_namespace(element.tag)), False, False)
    while pending:
        node, text, node_above, marked, under_fn = pending.pop()
        if node is None:
            raw.append(text)
            if not marked:
                cut.append(text)
            continue
        tag = strip_namespace(node.tag)
        if tag in NESTED_ARTICLES:
            continue
        if tag in TEXT_ACCUMULATING:
            if tag == "xref" and node.get("ref-type") in FIG_TABLE_XREF_TYPES:
                raw.append("[link]")  # `_append_link`, always marked
                continue
            if tag in FORMULA_PARTS:
                rendition = "".join(node.itertext())
                raw.append(rendition)
                if not marked:
                    cut.append(rendition)
                continue
            if not _merges(tag, node_above):
                continue
            marked = marked or under_fn or tag in NOT_A_NAMES_TEXT
            under_fn = False
        elif tag in NOTE_ELEMENTS:
            under_fn = True
        _push_content(pending, node, (*node_above, tag), marked, under_fn)
    return "".join(raw), "".join(cut)


def _push_content(
    pending: list[tuple[ET.Element | None, str, tuple[str, ...], bool, bool]],
    node: ET.Element,
    path: tuple[str, ...],
    marked: bool,
    under_fn: bool,
) -> None:
    """Push ``node``'s text, children and their tails for :func:`_buffer_text`,
    so they pop in document order. Its own character data lands unmarked by an
    ``<fn>``; its children carry ``under_fn`` to the next buffer down."""
    pieces: list[tuple[ET.Element | None, str, tuple[str, ...], bool, bool]] = [
        (None, node.text or "", path, marked, False)
    ]
    for child in node:
        pieces.append((child, "", path, marked, under_fn))
        pieces.append((None, child.tail or "", path, marked, False))
    pending.extend(reversed(pieces))


def _merges(tag: str, above: tuple[str, ...]) -> bool:
    """Mirror ``endElement``'s ``merge_with_parent`` for an accumulating element
    whose strict ancestors are ``above``, under a cited name: inline, a
    ``<contrib-group>``, or anything below a mixed-content citation or a
    related work — never a formula part or a cell.

    Three more terms cannot arise under a name a reference's arm reads, and
    are left out: a prose ``<citation>`` (a ``<ref>`` is open), declined
    metadata below a claiming element (the claimers are an ``<xref>``, cut
    whole, and a mixed-content citation, whose descendants merge anyway), and
    a name a ``<contrib>`` owns (no ``<contrib>`` stands in a citation).
    """
    if tag in FORMULA_PARTS or tag in TABLE_CELLS:
        return False
    return (
        tag in INLINE_ELEMENTS
        or tag == "contrib-group"
        or any(ancestor in MIXED_CITATIONS for ancestor in above)
        or any(ancestor in RELATED_WORK for ancestor in above)
    )


def _carries_text_of_its_own(element: ET.Element) -> bool:
    """Mirror ``_ReferenceBuilder.citation_is_typeset``: character data that is
    not whitespace alone, directly in the element or in an ``<x>`` — JATS's
    generated punctuation — belonging to it rather than to a citation nested
    inside it (PR #394's review)."""
    if _own_character_data(element).strip():
        return True
    pending = list(element)
    while pending:
        node = pending.pop()
        tag = strip_namespace(node.tag)
        if tag in READ_CITATIONS:
            continue
        if tag == "x" and _own_character_data(node).strip():
            return True
        pending.extend(node)
    return False


def _own_character_data(element: ET.Element) -> str:
    """The text directly in ``element``, not in a child of it."""
    return (element.text or "") + "".join(child.tail or "" for child in element)


def _in_routed_paragraph(ancestors: tuple[str, ...]) -> bool:
    """Does text merged into a ``<p>`` above these ancestors reach output?

    A prose ``<citation>`` merges back into the buffer around it
    (``is_prose_citation``), and a related work's parts into the one it sits
    in, so either survives where that buffer is a paragraph's — not one
    standing in a ``<sec>`` itself, whose buffer nothing reads; nor a
    ``<ref-list>``'s own ``<p>``, which #224 refuses as bibliography
    apparatus; nor one in a ``<floats-group>``, which falls past every branch
    (#253). A ``<ref-list>`` under an open ``<sec>`` keeps its apparatus in
    the parser and is read as refused here: 0 served and 1 archive article
    carry one (#224).
    """
    return "p" in ancestors and "ref-list" not in ancestors and "floats-group" not in ancestors


def _contrib_owns(ancestors: tuple[str, ...]) -> bool:
    """Mirror ``_contrib_owns_name``: the nearest of a ``<contrib>`` and a prose
    container decides."""
    for ancestor in reversed(ancestors):
        if ancestor == "contrib":
            return True
        if ancestor in CONTRIBUTOR_PROSE:
            return False
    return False


def _innermost(ancestors: tuple[str, ...], tags: frozenset[str]) -> int:
    """Index of the innermost ancestor in ``tags``, or ``-1``."""
    for index in range(len(ancestors) - 1, -1, -1):
        if ancestors[index] in tags:
            return index
    return -1


def _reference_part(path: tuple[str, ...]) -> int:
    """Index of the ``<ref>``'s own citation element above the holder — the
    outermost below the innermost ``<ref>`` — or ``-1`` outside one."""
    ref = _innermost(path, frozenset({"ref"}))
    if ref < 0:
        return -1
    return next((i for i in range(ref + 1, len(path)) if path[i] in READ_CITATIONS), -1)


def classify(holder: Holder) -> str:
    """The context of one holder, decided in the parser's order.

    ``path`` here is the path above the *parts*, which includes the holder's
    own tag, because that is the stack the parser's arms read.
    """
    path = (*holder.ancestors, holder.tag)
    if holder.read_as_author:
        return "citation-author"
    if "contrib" in path and _contrib_owns(path):
        if not holder.collected_contributor:
            return "contributor-declined"
        return "contributor-overwritten" if holder.contributor_overwritten else "contributor-own"
    cell = _innermost(path, TABLE_CELLS)
    if cell >= 0 and "table-wrap" in path[:cell]:
        return "table-cell"
    if cell >= 0 and not {"p", "ref"} & set(path[cell + 1 :]):
        # No table builder is open for it, and its text is isolated from the
        # buffer around it (#243) — in a citation's <alternatives> table, a
        # prose citation's, or an <array>. A <p> below the cell routes its own
        # prose, and a <ref> below it builds its own reference.
        return "isolated-cell"
    part = _reference_part(path)
    if part >= 0:
        if not holder.reference_built:
            return "reference-discarded"
        # The <ref>'s own citation element decides, not the innermost: one
        # nested in its note cites another work and is printed only inside
        # the outer one's string, where the outer writes one (#414).
        if path[part] == "mixed-citation":
            return "mixed-citation-glued"
        if path[part] == "element-citation":
            return "element-citation-unread"
        if path[part] == "citation" and part in holder.typeset_citations:
            return "nlm-citation-glued"
        return "nlm-citation-unread"
    if _innermost(path, frozenset({"mixed-citation", "element-citation"})) >= 0:
        return "citation-in-prose"
    nlm = _innermost(path, frozenset({"citation"}))
    if nlm >= 0:
        return "nlm-citation-in-prose" if _in_routed_paragraph(path[:nlm]) else "citation-in-prose"
    if _innermost(path, frozenset({"nlm-citation"})) >= 0:
        return "citation-in-prose"
    related = next((i for i, tag in enumerate(path) if tag in RELATED_WORK), -1)
    if related >= 0:
        if _in_routed_paragraph(path[:related]):
            return "related-work-in-prose"
        return "related-work-metadata"
    if "contrib" in path:
        return "contributor-prose"
    return "other"


@dataclass
class ArtifactReport:
    """The counts for one artifact, and whether they may be read at all."""

    documents: int = 0
    unparsed: int = 0
    suppressed: int = 0
    holders: Counter[str] = field(default_factory=Counter)
    documents_by_context: Counter[str] = field(default_factory=Counter)
    holder_tags: Counter[tuple[str, str]] = field(default_factory=Counter)
    paths: dict[str, Counter[str]] = field(default_factory=dict)
    dropped_in_prose: int = 0
    dropped_in_prose_documents: int = 0
    stats: ReadStats = field(default_factory=ReadStats)

    @property
    def parsed(self) -> int:
        """Documents that entered the denominators."""
        return self.documents - self.unparsed

    @property
    def problems(self) -> list[str]:
        """Why this artifact's counts cannot be quoted, if they cannot."""
        found = []
        if self.documents == 0:
            found.append("no document was read")
        if self.unparsed:
            found.append(f"{self.unparsed} document(s) did not parse")
        if self.stats.openers != self.stats.closers:
            found.append(
                f"{self.stats.openers} <article> openers against {self.stats.closers} closers"
            )
        if self.stats.openers and self.stats.openers != self.documents:
            found.append(f"{self.stats.openers} openers but {self.documents} split out")
        return found


def measure(path: Path) -> ArtifactReport:
    """Walk every document of one artifact."""
    report = ArtifactReport()
    for _, data in articles(path, report.stats):
        report.documents += 1
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            report.unparsed += 1
            continue
        walked = walk(root)
        report.suppressed += walked.suppressed
        present: set[str] = set()
        in_prose = False
        for holder in walked.holders:
            context = classify(holder)
            present.add(context)
            report.holders[context] += 1
            report.holder_tags[(context, holder.tag)] += 1
            if FATES[context] == DROPPED:
                full = "/".join((*holder.ancestors, holder.tag))
                report.paths.setdefault(context, Counter())[full] += 1
                if "p" in holder.ancestors:
                    report.dropped_in_prose += 1
                    in_prose = True
        for context in present:
            report.documents_by_context[context] += 1
        report.dropped_in_prose_documents += in_prose
    return report


def print_report(path: Path, report: ArtifactReport) -> None:
    """Print one artifact's table, its dropped paths in full, and any problem."""
    print(
        f"{path.name}: {report.documents} documents, {report.parsed} parsed, "
        f"{report.unparsed} unparsed, {report.stats.skipped_members} non-XML members skipped, "
        f"{report.suppressed} holders in suppressed regions (not counted)"
    )
    for context in CONTEXTS:
        tags = ", ".join(
            f"{tag} {count}"
            for (name, tag), count in sorted(report.holder_tags.items())
            if name == context.name
        )
        print(
            f"  {context.name:24} {context.fate:8} {report.holders[context.name]:10} "
            f"in {report.documents_by_context[context.name]:6} docs  [{tags}]  {context.note}"
        )
    dropped = sum(report.holders[c.name] for c in CONTEXTS if c.fate == DROPPED)
    print(f"  {'DROPPED, all contexts':33} {dropped:10}")
    print(
        f"  {'DROPPED with a <p> ancestor':33} {report.dropped_in_prose:10} "
        f"in {report.dropped_in_prose_documents:6} docs"
    )
    for context in CONTEXTS:
        paths = report.paths.get(context.name)
        if not paths:
            continue
        print(f"  {context.name}: {len(paths)} distinct path(s)")
        for ancestor_path, count in paths.most_common(PATHS_SHOWN):
            print(f"    {count:10}  {ancestor_path}")
        rest = paths.most_common()[PATHS_SHOWN:]
        if rest:
            print(f"    {sum(c for _, c in rest):10}  in {len(rest)} further path(s) not shown")
    for problem in report.problems:
        print(f"  ERROR: {problem} — these counts are not reportable")
    sys.stdout.flush()


def main() -> int:
    """Measure every artifact named on the command line; 1 if any is not reportable."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for artifact in args.artifacts:
        report = measure(artifact)
        print_report(artifact, report)
        failed = failed or bool(report.problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
