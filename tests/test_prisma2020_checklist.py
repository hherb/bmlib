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

"""The PRISMA 2020 checklist table, against the shared fixture.

``tests/fixtures/prisma2020_checklist.json`` pins every field of every row;
Rust's ``rust/bmlib/tests/prisma2020_checklist_contract.rs`` reads the same
file, so the two tables cannot drift apart.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.prisma2020_checklist import (
    CHECKLIST,
    KEY_ITEMS,
    ChecklistItem,
    ChecklistSection,
    item,
)

FIXTURE = Path(__file__).parent / "fixtures" / "prisma2020_checklist.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_checklist_matches_fixture() -> None:
    rows = _fixture()["items"]
    names = [f.name for f in dataclasses.fields(ChecklistItem)]

    assert len(rows) == len(CHECKLIST)
    for row, entry in zip(rows, CHECKLIST, strict=True):
        assert sorted(row) == sorted(names), row["id"]
        assert {name: getattr(entry, name) for name in names} == row, row["id"]


def test_sections_are_their_wire_strings() -> None:
    assert [s.value for s in ChecklistSection] == [
        "title",
        "abstract",
        "introduction",
        "methods",
        "results",
        "discussion",
        "other_information",
    ]
    assert all(type(entry.section) is ChecklistSection for entry in CHECKLIST)


def test_ids_are_unique_and_ordered() -> None:
    ids = [entry.id for entry in CHECKLIST]

    assert len(set(ids)) == len(ids)

    def order(item_id: str) -> tuple[int, str]:
        number = item_id.rstrip("abcdef")
        return int(number), item_id[len(number) :]

    assert ids == sorted(ids, key=order)
    sections = list(ChecklistSection)
    positions = [sections.index(entry.section) for entry in CHECKLIST]
    assert positions == sorted(positions)


def test_key_items_equal_key_rows() -> None:
    assert set(KEY_ITEMS) == {entry.id for entry in CHECKLIST if entry.key}
    assert len(KEY_ITEMS) == len(set(KEY_ITEMS)) == 10
    assert list(KEY_ITEMS) == _fixture()["key_items"]


def test_forty_two_rows() -> None:
    assert len(CHECKLIST) == 42


def test_item_finds_a_row_by_id() -> None:
    found = item("13d")

    assert found is not None
    assert found.id == "13d"
    assert found.section is ChecklistSection.METHODS
    assert item("13g") is None
    assert item("") is None


def test_rows_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        CHECKLIST[0].key = True  # type: ignore[misc]
