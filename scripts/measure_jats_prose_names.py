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

- ``read`` — a field reads the parts: a ``<name>`` or a ``<person-group>`` in
  the *first* citation element of a ``<ref>`` (PR #387's two positions, with
  #149's first-wins and #270's related-work refusal) — or in a later
  alternative of the ``<citation-alternatives>`` group holding it, while the
  reference has stored no author yet (#407, #417) — or a ``<contrib>``'s own
  name (``_contrib_owns_name``; a
  non-author contributor's name is declined rather than read, and is not prose
  either way).
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
the real parser and holds each fate to what the parse kept — the parser is
imported by the *test*, never by this script, which restates the sets it needs,
as every sampler here does.

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
from dataclasses import dataclass, field
from pathlib import Path

# The name parts whose buffers #382 is about.
NAME_PARTS = frozenset({"surname", "given-names"})

# Restated from the parser rather than imported; see the module docstring.
# Regions the parser suppresses whole (`_NESTED_ARTICLE_ELEMENTS`).
NESTED_ARTICLES = frozenset({"sub-article", "response"})
# The citation elements the parser reads in a `<ref>` (`_CITATION_ELEMENTS`),
# NLM 2.x's `<citation>` and NLM 3.0's `<nlm-citation>` included since #390.
READ_CITATIONS = frozenset({"mixed-citation", "element-citation", "citation", "nlm-citation"})
# Another work described in place (`_RELATED_WORK_ELEMENTS`).
RELATED_WORK = frozenset({"related-article", "related-object", "product"})
# What a `<contrib>` holds about its contributor (`_CONTRIBUTOR_PROSE`).
CONTRIBUTOR_PROSE = frozenset({"bio", "author-comment", "p"})
# Cells `characters()` writes directly while a `<table-wrap>` is open.
TABLE_CELLS = frozenset({"td", "th"})
# What a name arm cuts out of a cited `<collab>` or `<string-name>`
# (`_without_notes(name=True)`: the note spans of `_NOTE_ELEMENTS` and
# `_NOTE_XREF_TYPES`, and the name spans of every `<xref>` and `<contrib-group>`,
# #423, #425, #429). Only whether any text is left is asked of it.
CUT_FROM_NAMES = frozenset({"fn", "xref", "contrib-group"})

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
        "a <name>/<person-group> in a <ref>'s first citation, or in a later"
        " alternative of its group filling an empty author list (#407)",
    ),
    Context("contributor-own", READ, "a <contrib>'s own name (a non-author's is declined)"),
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
        "citation-cell",
        DROPPED,
        "a <td>/<th> in a <ref>'s citation, outside any <table-wrap>: isolated (#243), no table",
    ),
    Context("citation-in-prose", DROPPED, "a citation element outside any <ref>"),
    Context("related-work-metadata", DROPPED, "a related work outside prose (<article-meta>)"),
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
            for member in tar:
                if not member.isfile():
                    continue
                handle = tar.extractfile(member)
                if handle is None or not member.name.endswith(suffixes):
                    stats.skipped_members += 1
                    continue
                yield member.name, handle.read()
        return
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        text = stream.read()
    stats.openers = len(_OPENER.findall(text))
    stats.closers = text.count("</article>")
    for index, match in enumerate(_ARTICLE.finditer(text)):
        yield f"article_{index}", match.group(0).encode()


@dataclass(frozen=True)
class Holder:
    """One name-part holder: its own tag, the path above it, and where it sits.

    ``later_citations`` holds the indices in ``ancestors`` of citation elements
    whose names no field reads: one that was not the first of its ``<ref>``
    (#149) — unless it is a later alternative in the ``<citation-alternatives>``
    group holding the first, opened while the reference had stored no author,
    whose names the parser reads and stores (#407, #417) — and one nested in
    another, which cites another work (#414). Position among siblings, and
    what the siblings stored, are not in the path.
    ``typeset_citations`` holds those of ``<citation>`` elements carrying
    character data of their own, which is what decides whether the parser
    writes that one's string (#390) — also not in the path.
    """

    tag: str
    ancestors: tuple[str, ...]
    later_citations: frozenset[int] = frozenset()
    typeset_citations: frozenset[int] = frozenset()


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
class _RefState:
    """What the walk has seen of one open ``<ref>``, counted as the parser's
    ``_ReferenceBuilder`` counts it (issue #417).

    ``parts`` counts the citation elements opened, and
    ``groups_opened``/``open_groups``/``first_group`` are the
    ``<citation-alternatives>`` numbering of #407. The parser does not count a
    citation nested in another's note (#414), and this does: a nested one is
    never a direct child of the first's group — its parent lies inside the
    outer citation, and a group nested there is numbered afresh — so it is
    marked in ``later`` all the same, and only ``parts`` differs, which is
    read for its first value alone. ``has_author`` says the
    reference's author list is non-empty, which is what decides whether a later
    alternative's names fill it (``fill_empty_fields_from`` takes the list whole
    and only into an empty one).
    """

    parts: int = 0
    groups_opened: int = 0
    open_groups: list[int] = field(default_factory=list)
    first_group: int = 0
    has_author: bool = False


def walk(root: ET.Element) -> Walk:
    """Collect every name-part holder in document order, iteratively.

    Iterative so a deep document cannot raise ``RecursionError`` part-way
    through an artifact. A ``<sub-article>``/``<response>`` subtree is not
    entered; its holders are counted in ``suppressed``.

    A ``<ref>``'s citation elements are numbered as the parser numbers them
    (``_CITATION_ELEMENTS``' open arm): the first is read; a later one is read
    only as an alternative in the first's ``<citation-alternatives>`` group —
    its direct child, the group being the innermost open one — and then only
    while the reference has no author, since the parser fills the author list
    from an alternative only where it is empty (#407). Reading every later
    alternative as read, the remedy #417 itself proposed, would file as read
    the names of a later rendition the parser discards beside the first's.
    """
    result = Walk()
    ancestors: list[str] = []
    later: set[int] = set()
    typeset: set[int] = set()
    refs: list[_RefState] = []
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:
        element, leaving = stack.pop()
        tag = strip_namespace(element.tag)
        if leaving:
            ancestors.pop()
            later.discard(len(ancestors))
            typeset.discard(len(ancestors))
            if refs and tag == "citation-alternatives":
                refs[-1].open_groups.pop()
            elif tag == "ref":
                refs.pop()
            continue
        if tag in NESTED_ARTICLES:
            result.suppressed += _count_holders(element)
            continue
        if refs and tag in READ_CITATIONS:
            _open_citation(refs[-1], len(ancestors), ancestors, later)
        elif refs and tag == "citation-alternatives":
            refs[-1].groups_opened += 1
            refs[-1].open_groups.append(refs[-1].groups_opened)
        if tag == "citation" and _carries_text_of_its_own(element):
            typeset.add(len(ancestors))
        path = (*ancestors, tag)
        if any(strip_namespace(child.tag) in NAME_PARTS for child in element):
            result.holders.append(
                Holder(tag, tuple(ancestors), frozenset(later), frozenset(typeset))
            )
            if refs and _reads_as_citation_author(path, frozenset(later)):
                refs[-1].has_author = refs[-1].has_author or _parts_carry_text(element)
        elif refs and tag in {"collab", "string-name"}:
            if _reads_as_cited_verbatim_name(path, frozenset(later)):
                refs[-1].has_author = refs[-1].has_author or bool(_name_text(element).strip())
        if tag == "ref":
            refs.append(_RefState())
        ancestors.append(tag)
        stack.append((element, True))
        stack.extend((child, False) for child in reversed(element))
    return result


def _open_citation(state: _RefState, depth: int, ancestors: list[str], later: set[int]) -> None:
    """Number one citation element of a ``<ref>``, marking it in ``later`` where
    its names are not the reference's (see :func:`walk`)."""
    state.parts += 1
    in_group = bool(ancestors) and ancestors[-1] == "citation-alternatives"
    group = state.open_groups[-1] if in_group and state.open_groups else 0
    if state.parts == 1:
        state.first_group = group
    elif not (group and group == state.first_group and not state.has_author):
        later.add(depth)


def _parts_carry_text(holder: ET.Element) -> bool:
    """Does a holder's ``<surname>``/``<given-names>`` carry text, so the
    parser's ``finish_current_author`` appends a name?"""
    return any(
        "".join(child.itertext()).strip()
        for child in holder
        if strip_namespace(child.tag) in NAME_PARTS
    )


def _reads_as_cited_verbatim_name(path: tuple[str, ...], later: frozenset[int]) -> bool:
    """Mirror the cited ``<collab>`` and undivided ``<string-name>`` arms, where
    ``path`` ends with the element: ``_cited_reference()`` must name the
    reference, the nearest citation element or related work above being a
    citation element whose names the reference reads."""
    nearest = _innermost(path[:-1], READ_CITATIONS | RELATED_WORK)
    if nearest < 0 or path[nearest] in RELATED_WORK:
        return False
    return nearest not in later  # :func:`walk` asks only with a <ref> open.


def _name_text(element: ET.Element) -> str:
    """The text a name arm keeps of ``element``: everything but what
    ``CUT_FROM_NAMES`` holds. Iterative, as :func:`walk` is."""
    pieces: list[str] = []
    pending: list[ET.Element | str] = [element]
    while pending:
        node = pending.pop()
        if isinstance(node, str):
            pieces.append(node)
            continue
        pieces.append(node.text or "")
        for child in reversed(node):
            pending.append(child.tail or "")
            if strip_namespace(child.tag) not in CUT_FROM_NAMES:
                pending.append(child)
    return "".join(pieces)


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
    """Is a prose ``<citation>`` above these ancestors merged into output?

    The parser merges it back into the buffer around it (``is_prose_citation``),
    so it survives where that buffer is a paragraph's — not one standing in a
    ``<sec>`` itself, whose buffer nothing reads, nor a ``<ref-list>``'s own
    ``<p>``, which #224 refuses as bibliography apparatus. A ``<ref-list>``
    under an open ``<sec>`` keeps its apparatus in the parser and is read as
    refused here: 0 served and 1 archive article carry one (#224).
    """
    return "p" in ancestors and "ref-list" not in ancestors


def _reads_as_citation_author(ancestors: tuple[str, ...], later: frozenset[int]) -> bool:
    """Mirror ``_cited_name_part_reference``, where ``ancestors`` ends with the holder.

    ``_cited_reference()`` first: walking up from the part, the nearest citation
    element or related work must be a citation element, the first of its
    ``<ref>`` (#270's refusal, #149's first-wins). Then one of two positions
    (PR #387): the part's parent is a ``<name>`` or a ``<string-name>`` (read
    verbatim in a mixed-content citation, as a ``<name>`` in an element-only
    one since #415), or an enclosing
    ``<person-group>`` set ``in_ref_person_group`` — itself asked through
    ``_cited_reference()`` at the group's open, so from the group upward.
    """
    nearest = _innermost(ancestors, READ_CITATIONS | RELATED_WORK)
    if nearest < 0 or ancestors[nearest] in RELATED_WORK:
        return False
    if nearest in later or "ref" not in ancestors[:nearest]:
        return False
    if ancestors[-1] == "name":
        return True
    if ancestors[-1] == "string-name":
        # In a mixed-content citation the <string-name> arm reads its own buffer
        # verbatim (`Tan J`), the parts having merged into it; in an
        # element-only one they have not, so since #415 the parts are read as
        # a <name>'s are. Read either way.
        return True
    for index in range(len(ancestors) - 1, nearest, -1):
        if ancestors[index] != "person-group":
            continue
        above = _innermost(ancestors[:index], READ_CITATIONS | RELATED_WORK)
        if above == nearest:
            return True
    return False


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

    ``ancestors`` here is the path above the *parts*, which includes the
    holder's own tag, because that is the stack the parser's arms read.
    """
    path = (*holder.ancestors, holder.tag)
    later = holder.later_citations
    if _reads_as_citation_author(path, later):
        return "citation-author"
    if "contrib" in path and _contrib_owns(path):
        return "contributor-own"
    cell = _innermost(path, TABLE_CELLS)
    if cell >= 0 and "table-wrap" in path[:cell]:
        return "table-cell"
    part = _reference_part(path)
    if part >= 0 and cell > part:
        # A cell of the citation's <alternatives> table: its text is isolated
        # from the citation's string (#243) and no table builder takes it.
        return "citation-cell"
    if part >= 0:
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
        return "related-work-in-prose" if "p" in path[:related] else "related-work-metadata"
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
