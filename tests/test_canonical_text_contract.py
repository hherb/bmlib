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
against: every converter turns each case's input into exactly the expected
``CanonicalText``, spans counted in code points, and the expected value
round-trips through ``from_dict``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.canonical import CanonicalText, canonical_from_jats, canonical_from_plain_text
from bmlib.fulltext.jats_parser import JATSParser

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_text.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def convert(case: dict[str, Any]) -> CanonicalText:
    """Run the converter the case names on its input."""
    fmt = case["format"]
    if fmt == "plain_text":
        return canonical_from_plain_text(case["input"], case.get("title", ""))
    if fmt == "jats":
        return canonical_from_jats(JATSParser(case["input"].encode()).parse())
    raise AssertionError(f"unknown format {fmt!r} in {case['name']}")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_converter_output_is_the_expected_canonical_text(case: dict[str, Any]) -> None:
    assert convert(case).to_dict() == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_expected_value_round_trips(case: dict[str, Any]) -> None:
    assert CanonicalText.from_dict(case["expected"]).to_dict() == case["expected"]


def test_the_contract_covers_every_format_it_names() -> None:
    assert {c["format"] for c in CASES} >= {"plain_text", "jats"}
