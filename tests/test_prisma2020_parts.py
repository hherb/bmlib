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

"""Splitting the PRISMA 2020 checklist to a reply budget.

``tests/fixtures/prisma2020_parts.json`` holds the cases; Rust's
``rust/bmlib/tests/prisma2020_parts_contract.rs`` reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.prisma2020_checklist import CHECKLIST, item
from bmlib.quality.prisma2020_parts import (
    PART_OVERHEAD_CHARS,
    PartPlan,
    PartsError,
    part_query,
    plan_parts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "prisma2020_parts.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def rows(ids: list[str]) -> list:
    found = [item(i) for i in ids]
    assert all(row is not None for row in found)
    return found


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_cases(case: dict[str, Any]) -> None:
    items = rows(case["item_ids"])
    expected = case["expected"]
    if isinstance(expected, dict):
        with pytest.raises(PartsError) as err:
            plan_parts(items, case["reply_budget_chars"])
        assert err.value.kind == expected["error"]
        assert err.value.id == expected["id"]
    else:
        parts = plan_parts(items, case["reply_budget_chars"])
        assert [list(p.item_ids) for p in parts] == expected


def test_overhead_constant() -> None:
    assert PART_OVERHEAD_CHARS == 400


def test_large_budget_is_one_part() -> None:
    parts = plan_parts(CHECKLIST, 10**9)
    assert len(parts) == 1
    assert len(parts[0].item_ids) == 42


def test_budget_below_one_item_is_refused() -> None:
    with pytest.raises(PartsError) as err:
        plan_parts(CHECKLIST, PART_OVERHEAD_CHARS + 899)
    assert err.value.kind == "item_too_large"
    assert (err.value.id, err.value.needed, err.value.budget) == ("1", 1300, 1299)


def test_empty_items_give_no_parts() -> None:
    assert plan_parts([], 16000) == []


def test_order_is_checklist_order() -> None:
    parts = plan_parts(CHECKLIST, 16000)
    flat = [i for p in parts for i in p.item_ids]
    assert flat == [row.id for row in CHECKLIST]


def test_label_spans_sections() -> None:
    assert PartPlan(("5", "6", "13c")).label() == "Methods 5–13c"
    assert PartPlan(("13d", "17")).label() == "Methods 13d – Results 17"
    assert PartPlan(("13d",)).label() == "Methods 13d"
    assert PartPlan(("23d", "24a")).label() == "Discussion 23d – Other information 24a"


def test_part_query_names_every_hint() -> None:
    items = rows(["7", "13d"])
    query = part_query(items)
    assert query.startswith("Everything needed to judge these PRISMA 2020 items: ")
    assert query == (
        "Everything needed to judge these PRISMA 2020 items: "
        + "; ".join(row.condense_hint for row in items)
    )
