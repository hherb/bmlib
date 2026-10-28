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

"""The shared canonical-text contract.

The same file Rust's ``rust/bmlib/tests/canonical_text_contract.rs`` asserts
against: every converter turns each case's input (for a PDF, the segmenter's
``SegmentedDocument`` as ``to_dict()`` writes it) into exactly the expected
``CanonicalText``, spans counted in code points, and the expected value
round-trips through ``from_dict``. Each of its ``refusals`` (a variant of its
``valid`` value) is refused by both languages alike, and its ``html_elements``
lists are both languages' ``PRUNED`` and ``BLOCKS``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.canonical import (
    CanonicalText,
    canonical_from_html,
    canonical_from_jats,
    canonical_from_markdown,
    canonical_from_plain_text,
    canonical_from_segmented,
)
from bmlib.fulltext.canonical.html import BLOCKS, PRUNED
from bmlib.fulltext.jats_parser import JATSParser
from bmlib.fulltext.models import SegmentedDocument

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_text.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = CONTRACT["cases"]
REFUSALS: list[dict[str, Any]] = CONTRACT["refusals"]


def convert(case: dict[str, Any]) -> CanonicalText:
    """Run the converter the case names on its input."""
    fmt = case["format"]
    if fmt == "plain_text":
        return canonical_from_plain_text(case["input"], case.get("title", ""))
    if fmt == "markdown":
        return canonical_from_markdown(case["input"])
    if fmt == "jats":
        return canonical_from_jats(JATSParser(case["input"].encode()).parse())
    if fmt == "pdf":
        return canonical_from_segmented(SegmentedDocument.from_dict(case["input"]))
    if fmt == "html":
        return canonical_from_html(case["input"])
    raise AssertionError(f"unknown format {fmt!r} in {case['name']}")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_converter_output_is_the_expected_canonical_text(case: dict[str, Any]) -> None:
    assert convert(case).to_dict() == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_expected_value_round_trips(case: dict[str, Any]) -> None:
    assert CanonicalText.from_dict(case["expected"]).to_dict() == case["expected"]


def test_the_html_element_sets_are_the_contracts() -> None:
    # A member one port lacks is otherwise invisible to both: each language's
    # own tests iterate its own constant.
    elements = CONTRACT["html_elements"]
    assert (sorted(PRUNED), sorted(BLOCKS)) == (elements["pruned"], elements["blocks"])


def test_the_contract_covers_every_format_it_names() -> None:
    assert {c["format"] for c in CASES} == {"plain_text", "markdown", "jats", "pdf", "html"}


def test_the_value_the_refusals_vary_is_accepted() -> None:
    assert CanonicalText.from_dict(CONTRACT["valid"]).to_dict() == CONTRACT["valid"]


@pytest.mark.parametrize("refusal", REFUSALS, ids=[r["name"] for r in REFUSALS])
def test_a_malformed_value_is_refused_with_a_value_error(refusal: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CanonicalText.from_dict(refusal["value"])
