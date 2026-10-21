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

"""The shared find_passage contract: spans are code-point indices (see the fixture)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.citations import find_passage

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "passage_match.json").read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = _FIXTURE["cases"]
INVALID: list[float] = _FIXTURE["invalid_thresholds"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_contract_case(case: dict[str, Any]) -> None:
    match = find_passage(case["passage"], case["text"], case["min_similarity"])
    expected = case["expected"]
    assert match.matched is expected["matched"]
    assert match.exact is expected["exact"]
    assert (match.start, match.end, match.text) == (
        expected["start"],
        expected["end"],
        expected["text"],
    )
    if "similarity" in expected:
        assert match.similarity == expected["similarity"]
    if "min_similarity_at_least" in expected:
        assert match.similarity >= expected["min_similarity_at_least"]


@pytest.mark.parametrize("threshold", INVALID, ids=str)
def test_contract_invalid_threshold(threshold: float) -> None:
    with pytest.raises(ValueError):
        find_passage("a", "a", threshold)
