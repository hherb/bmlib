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

"""Unit tests for :func:`canonical_from_jats` beyond the shared contract."""

from __future__ import annotations

import json
from pathlib import Path

from bmlib.citations import find_passage
from bmlib.fulltext.canonical import BlockKind, CanonicalText, canonical_from_jats
from bmlib.fulltext.jats_parser import JATSParser

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_text.json"

#: The acceptance threshold the tests locate quotes at.
MIN_SIMILARITY = 0.9


def case_text(name: str) -> CanonicalText:
    """The canonical text of the shared fixture's JATS case *name*."""
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    (case,) = [c for c in cases if c["name"] == name]
    return canonical_from_jats(JATSParser(case["input"].encode()).parse())


def blocks(doc: CanonicalText, kind: BlockKind) -> list[tuple[int, int]]:
    """The ``(start, end)`` of every block of *kind*."""
    return [(b.span.start, b.span.end) for s in doc.sections for b in s.blocks if b.kind is kind]


def test_a_quote_of_a_table_row_is_found_at_the_row() -> None:
    doc = case_text("jats/full")
    match = find_passage("A 12", doc.text, MIN_SIMILARITY)

    assert (match.start, match.end) == blocks(doc, BlockKind.TABLE_ROW)[1]


def test_a_quote_of_a_link_stripped_sentence_is_found_at_its_block() -> None:
    doc = case_text("jats/inline-links")
    quote = "As shown in Fig 1 and before 2, the effect [95% CI] held."
    match = find_passage(quote, doc.text, MIN_SIMILARITY)

    assert match.exact
    assert (match.start, match.end) == blocks(doc, BlockKind.PARAGRAPH)[0]


def test_a_graphic_only_table_is_a_heading_only_section() -> None:
    xml = (
        '<?xml version="1.0"?><article xmlns:xlink="http://www.w3.org/1999/xlink">'
        "<front><article-meta><article-id pub-id-type='pmc'>PMC1</article-id>"
        "</article-meta></front><body><sec><title>S</title><p>x</p>"
        '<table-wrap id="t1"><label>Table 1</label><graphic xlink:href="t1.jpg"/></table-wrap>'
        "</sec></body></article>"
    )
    doc = canonical_from_jats(JATSParser(xml.encode()).parse())
    table = doc.sections[-1]

    assert table.heading_span is not None
    assert doc.span_text(table.heading_span) == "Table 1"
    assert table.blocks == ()


def test_the_converter_version_is_jats_1() -> None:
    assert case_text("jats/front-only").converter_version == "jats-1"
