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

"""The shared contract for quotes split at their ellipses: spans are code-point indices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.citations import split_at_ellipses
from bmlib.citations.passage_match import ELLIPSIS_PIECE_MIN_WORDS
from bmlib.quality import verify_quotes

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "quote_pieces.json").read_text(encoding="utf-8")
)


def test_min_piece_words_matches_the_contract() -> None:
    assert _FIXTURE["min_piece_words"] == ELLIPSIS_PIECE_MIN_WORDS


@pytest.mark.parametrize("case", _FIXTURE["split_cases"], ids=lambda c: c["name"])
def test_split_case(case: dict[str, Any]) -> None:
    assert split_at_ellipses(case["quote"]) == case["pieces"]


@pytest.mark.parametrize("case", _FIXTURE["verify_cases"], ids=lambda c: c["name"])
def test_verify_case(case: dict[str, Any]) -> None:
    citations, not_found, contradicted, dropped = verify_quotes(
        case["quotes"], case["text"], case["min_similarity"]
    )
    expected = case["expected"]
    assert [{"start": c.start, "end": c.end, "text": c.text} for c in citations] == expected[
        "citations"
    ]
    assert not_found == expected["not_found"]
    assert [[c.quote, c.contradiction.value] for c in contradicted] == expected["contradicted"]
    assert dropped == expected["dropped"]
