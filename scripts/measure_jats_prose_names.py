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

"""Count the ``<name>`` elements a JATS document prints but the reader drops.

Issue #382: ``<surname>`` and ``<given-names>`` each accumulate their own text,
and the arms that read it fire only inside a citation's ``<person-group>`` or a
``<contrib>`` that *owns* the name. Everywhere else the buffered text is
discarded, so a name printed in prose is cut out of the sentence. #382 names one
instance in a contributor's ``<bio>`` (made visible by #258) and a synthetic one
in body prose, and asks for the population **before** a fix is chosen:

    A walk over both named artifacts should count ``<name>`` elements that are
    neither inside a ``<contrib>`` nor inside a citation.

This is that walk, and it classifies every ``<name>`` by the context the reader
actually decides on, because "outside a ``<contrib>`` and outside a citation" is
not one population:

- ``citation-author`` — inside a ``<person-group>`` of a citation. Read.
- ``contributor-own`` — a ``<contrib>``'s own name, with no ``<bio>``,
  ``<author-comment>`` or ``<p>`` between it and the ``<contrib>`` (Python's
  ``_contrib_owns_name``). Read.
- ``citation-inline`` — inside a citation but **not** a ``<person-group>``: its
  parts are not dropped, they are glued into one word (``JonesBob``), which is
  #314's population and not this one.
- ``contributor-prose`` — inside a ``<contrib>`` but behind one of the three
  prose containers: **dropped**, #382's ``<bio>`` form.
- ``other`` — everywhere else, including body prose (``<p>`` outside a
  ``<contrib>``): **dropped**, #382's body form. Reported by ancestor path,
  because some of these sit in containers the reader declines on purpose — a
  funding ``<principal-award-recipient>`` (#288) or a related work's
  ``<product>`` (#270) — and a count that pools them with body prose would
  overstate the defect.

``contributor-prose + other`` is the number #382 asks for. It is a count of
``<name>`` elements, not of the documents that carry one; both are reported.

Each artifact is either a ``.tar.gz`` of one XML document per member (a PMC
archive) or a gzipped ``<articles>`` concatenation (a Europe PMC served
bundle), the same two shapes ``diff_jats_parser.py`` reads.

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
from pathlib import Path

# A citation context: any ancestor that files the ``<name>`` under a reference.
CITATION = frozenset({"mixed-citation", "element-citation", "citation", "ref", "nlm-citation"})

# What a ``<contrib>`` holds *about* its contributor rather than naming them
# (Python's ``_CONTRIBUTOR_PROSE``). A name behind one of these is prose.
CONTRIBUTOR_PROSE = frozenset({"bio", "author-comment", "p"})

# The context names this walk reports, in the order it prints them.
CONTEXTS = (
    "citation-author",
    "contributor-own",
    "citation-inline",
    "contributor-prose",
    "other",
)

# The two contexts whose ``<name>`` text never reaches any field.
DROPPED = ("contributor-prose", "other")


def strip_namespace(tag: str) -> str:
    """``{ns}name`` -> ``name``."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def articles(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, bytes)`` for every article in an artifact."""
    if str(path).endswith((".tar.gz", ".tgz")):
        with tarfile.open(path) as tar:
            for member in tar:
                if member.isfile() and member.name.endswith(".xml"):
                    handle = tar.extractfile(member)
                    if handle is not None:
                        yield member.name, handle.read()
        return
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    for index, match in enumerate(re.finditer(r"<article\b.*?</article>", text, re.S)):
        yield f"article_{index}", match.group(0).encode()


def classify(ancestors: list[str]) -> str:
    """Which of the reader's arms reads this ``<name>``, given its ancestors.

    Mirrors ``_JATSHandler._contrib_owns_name``: walk outward and the first of a
    ``<contrib>`` or a prose container decides. A ``<person-group>`` inside a
    citation is the reference-author arm, which is its own answer.
    """
    if "person-group" in ancestors and any(a in CITATION for a in ancestors):
        return "citation-author"
    if any(a in CITATION for a in ancestors):
        return "citation-inline"
    if "contrib" in ancestors:
        for ancestor in reversed(ancestors):
            if ancestor == "contrib":
                return "contributor-own"
            if ancestor in CONTRIBUTOR_PROSE:
                return "contributor-prose"
    return "other"


def walk(element: ET.Element, ancestors: list[str], out: list[tuple[str, tuple[str, ...]]]) -> None:
    """Collect ``(context, ancestor path)`` for every ``<name>`` in the tree."""
    tag = strip_namespace(element.tag)
    if tag == "name":
        out.append((classify(ancestors), tuple(ancestors)))
    for child in element:
        ancestors.append(tag)
        walk(child, ancestors, out)
        ancestors.pop()


def scan(path: Path) -> None:
    """Print the ``<name>`` population of one artifact."""
    counts: Counter[str] = Counter()
    documents: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    total = unparsed = dropped_documents = 0
    for _, data in articles(path):
        total += 1
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            unparsed += 1
            continue
        found: list[tuple[str, tuple[str, ...]]] = []
        walk(root, [], found)
        present = {context for context, _ in found}
        for context in present:
            documents[context] += 1
        if present & set(DROPPED):
            dropped_documents += 1
        for context, ancestors in found:
            counts[context] += 1
            if context in DROPPED:
                paths["/".join(ancestors)] += 1
    dropped = sum(counts[context] for context in DROPPED)
    print(f"{path.name}: {total} documents, {unparsed} unparsed")
    for context in CONTEXTS:
        print(f"  {context:18} {counts[context]:9} <name>  in {documents[context]:6} documents")
    print(f"  {'DROPPED (#382)':18} {dropped:9} <name>  in {dropped_documents:6} documents")
    if paths:
        print("  ancestor paths of the dropped <name> elements:")
        for ancestor_path, count in paths.most_common(10):
            print(f"    {count:9}  {ancestor_path}")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    for artifact in args.artifacts:
        scan(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
