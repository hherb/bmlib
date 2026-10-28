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

"""The shared full-text-check contract, ``tests/fixtures/full_text_assessment.json``.

The same file Rust's ``rust/bmlib/tests/full_text_assessment_contract.rs``
asserts against: each case's Markdown, converted and assessed, gives exactly
the expected assessment, and the threshold is the fixture's. The fixture is
written by ``scripts/make_full_text_assessment.py``, which must still build it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.canonical import MIN_BODY_WORDS, assess_full_text, canonical_from_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "full_text_assessment.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = CONTRACT["cases"]
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_full_text_assessment.py"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_assessment_is_the_expected_one(case: dict[str, Any]) -> None:
    result = assess_full_text(canonical_from_markdown(case["markdown"]))
    assert result.to_dict() == case["expected"]


def test_the_threshold_is_the_contracts() -> None:
    assert MIN_BODY_WORDS == CONTRACT["min_body_words"]


def test_both_verdicts_are_on_contract() -> None:
    assert {c["expected"]["is_full_text"] for c in CASES} == {True, False}


def test_the_generator_still_builds_the_fixture() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    spec = importlib.util.spec_from_file_location("bmlib_make_full_text_assessment", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    assert make.build_contract() == CONTRACT
