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

"""The request's identifiers as library identifiers, from
``tests/fixtures/fulltext_library_ids.json`` — the file Rust's
``tests/library_store.rs`` asserts against."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.library_store import library_ids

_FIXTURE = Path(__file__).parent / "fixtures" / "fulltext_library_ids.json"
_CASES: list[dict[str, Any]] = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_library_ids_match_the_fixture(case: dict[str, Any]) -> None:
    ids, dropped = library_ids(case["pmid"], case["doi"], case["pmc_id"])
    assert {"pmid": ids.pmid, "doi": ids.doi, "pmcid": ids.pmcid} == case["ids"]
    assert dropped == case["dropped"]
