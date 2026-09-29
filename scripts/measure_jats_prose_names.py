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
  #149's first-wins and #270's related-work refusal), or a ``<contrib>``'s own
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
# The mixed-content ones, whose descendants merge into the citation's buffer
# (`_MIXED_CONTENT_CITATIONS`).
MIXED_CITATIONS = frozenset({"mixed-citation", "citation"})
# Another work described in place (`_RELATED_WORK_ELEMENTS`).
RELATED_WORK = frozenset({"related-article", "related-object", "product"})
# What a `<contrib>` holds about its contributor (`_CONTRIBUTOR_PROSE`).
CONTRIBUTOR_PROSE = frozenset({"bio", "author-comment", "p"})
# Cells `characters()` writes directly while a `<table-wrap>` is open.
TABLE_CELLS = frozenset({"td", "th"})

READ, KEPT, GLUED, DROPPED = "read", "kept", "glued", "dropped"


@dataclass(frozen=True)
class Context:
    """One place a name-part holder can sit, and what the parser does with it."""

    name: str
    fate: str
    note: str


CONTEXTS = (
    Context("citation-author", READ, "a <name>/<person-group> in a <ref>'s first citation"),
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
    that were not the first of their ``<ref>`` — the parser reads only the
    first (#149), and position among siblings is not in the path.
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


def walk(root: ET.Element) -> Walk:
    """Collect every name-part holder in document order, iteratively.

    Iterative so a deep document cannot raise ``RecursionError`` part-way
    through an artifact. A ``<sub-article>``/``<response>`` subtree is not
    entered; its holders are counted in ``suppressed``.
    """
    result = Walk()
    ancestors: list[str] = []
    later: set[int] = set()
    typeset: set[int] = set()
    ref_citations: list[int] = []
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:
        element, leaving = stack.pop()
        tag = strip_namespace(element.tag)
        if leaving:
            ancestors.pop()
            later.discard(len(ancestors))
            typeset.discard(len(ancestors))
            if tag == "ref":
                ref_citations.pop()
            continue
        if tag in NESTED_ARTICLES:
            result.suppressed += _count_holders(element)
            continue
        if tag in READ_CITATIONS and ref_citations:
            ref_citations[-1] += 1
            if ref_citations[-1] > 1:
                later.add(len(ancestors))
        if tag == "citation" and _carries_text_of_its_own(element):
            typeset.add(len(ancestors))
        if any(strip_namespace(child.tag) in NAME_PARTS for child in element):
            result.holders.append(
                Holder(tag, tuple(ancestors), frozenset(later), frozenset(typeset))
            )
        if tag == "ref":
            ref_citations.append(0)
        ancestors.append(tag)
        stack.append((element, True))
        stack.extend((child, False) for child in reversed(element))
    return result


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
    (PR #387): the part's parent is a ``<name>`` (or a ``<string-name>`` in a
    ``<mixed-citation>``, which its own arm reads verbatim), or an enclosing
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
    if ancestors[-1] == "string-name" and ancestors[nearest] in MIXED_CITATIONS:
        # The <string-name> arm reads its own buffer verbatim (`Tan J`), and in a
        # mixed-content citation the parts have merged into it; in an
        # element-only one they have not, and it reads nothing.
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
    mixed = _innermost(path, frozenset({"mixed-citation"}))
    if mixed >= 0:
        return "mixed-citation-glued" if "ref" in path[:mixed] else "citation-in-prose"
    element = _innermost(path, frozenset({"element-citation"}))
    if element >= 0:
        return "element-citation-unread" if "ref" in path[:element] else "citation-in-prose"
    nlm = _innermost(path, frozenset({"citation"}))
    if nlm >= 0:
        if "ref" not in path[:nlm]:
            if _in_routed_paragraph(path[:nlm]):
                return "nlm-citation-in-prose"
            return "citation-in-prose"
        return "nlm-citation-glued" if nlm in holder.typeset_citations else "nlm-citation-unread"
    structured = _innermost(path, frozenset({"nlm-citation"}))
    if structured >= 0:
        return "nlm-citation-unread" if "ref" in path[:structured] else "citation-in-prose"
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
