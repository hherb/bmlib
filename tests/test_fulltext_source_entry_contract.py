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

"""The shared ``FullTextSourceEntry`` JSON contract.

The same file Rust's ``rust/bmlib/tests/fulltext_source_entry_contract.rs``
asserts against: an entry read from JSON writes back the same object in both
languages, and one without ``url``, ``format`` or ``source`` is refused (#624).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.models import FullTextSourceEntry

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "fulltext_source_entry.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_an_entry_writes_back_as_the_contract_says(case: dict[str, Any]) -> None:
    """``from_dict`` then ``to_dict`` gives the fixture's ``output``."""
    assert FullTextSourceEntry.from_dict(case["input"]).to_dict() == case["output"]


@pytest.mark.parametrize("case", CONTRACT["refused"], ids=lambda c: c["name"])
def test_an_entry_without_a_required_field_is_refused(case: dict[str, Any]) -> None:
    """An entry missing ``url``, ``format`` or ``source`` does not read."""
    with pytest.raises(KeyError):
        FullTextSourceEntry.from_dict(case["input"])
