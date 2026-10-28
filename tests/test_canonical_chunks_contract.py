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

"""The shared chunker contract.

The same file Rust's ``rust/bmlib/tests/canonical_chunks_contract.rs`` asserts
against: each case's canonical text, chunked with its sizes, gives exactly the
expected chunks; the chunker's version and defaults are the fixture's; and each
of its ``refusals`` (sizes) is refused by both languages alike.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.canonical import (
    CHUNKER_VERSION,
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    CanonicalText,
    chunk_canonical,
)

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_chunks.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = CONTRACT["cases"]
REFUSALS: list[dict[str, Any]] = CONTRACT["refusals"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_chunks_are_the_expected_ones(case: dict[str, Any]) -> None:
    canonical = CanonicalText.from_dict(case["canonical"])
    chunks = chunk_canonical(canonical, case["max_chars"], case["overlap_chars"])
    assert [chunk.to_dict() for chunk in chunks] == case["expected"]


def test_the_version_and_defaults_are_the_contracts() -> None:
    assert (CHUNKER_VERSION, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS) == (
        CONTRACT["chunker_version"],
        CONTRACT["default_max_chars"],
        CONTRACT["default_overlap_chars"],
    )


@pytest.mark.parametrize("refusal", REFUSALS, ids=[r["name"] for r in REFUSALS])
def test_bad_sizes_are_refused_with_a_value_error(refusal: dict[str, Any]) -> None:
    canonical = CanonicalText.from_dict(CASES[0]["canonical"])
    with pytest.raises(ValueError):
        chunk_canonical(canonical, refusal["max_chars"], refusal["overlap_chars"])
