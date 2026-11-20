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

"""Splitting the PRISMA 2020 checklist into parts a model's reply can hold.

A run asks the model to judge a part of the checklist at a time, so that one
reply (an explanation and up to three quotes per item) fits the model's output
budget.  :func:`plan_parts` packs items greedily, in the order given.  Nothing
is cut: an item that cannot fit a part even alone is refused with
:class:`PartsError`.

Rust's ``rust/bmlib/src/quality/prisma2020_parts.rs`` is the same;
``tests/fixtures/prisma2020_parts.json`` pins both.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bmlib.quality.prisma2020_checklist import ChecklistItem, ChecklistSection, item

__all__ = [
    "PART_OVERHEAD_CHARS",
    "PartPlan",
    "PartsError",
    "part_query",
    "plan_parts",
]

#: The characters of a reply's JSON frame, counted once per part.
PART_OVERHEAD_CHARS = 400

_QUERY_PREFIX = "Everything needed to judge these PRISMA 2020 items: "

_SECTION_LABELS: dict[ChecklistSection, str] = {
    ChecklistSection.TITLE: "Title",
    ChecklistSection.ABSTRACT: "Abstract",
    ChecklistSection.INTRODUCTION: "Introduction",
    ChecklistSection.METHODS: "Methods",
    ChecklistSection.RESULTS: "Results",
    ChecklistSection.DISCUSSION: "Discussion",
    ChecklistSection.OTHER_INFORMATION: "Other information",
}


class PartsError(Exception):
    """A split that failed.

    Attributes:
        kind: The error's kind string: ``"item_too_large"``.
        id: The item that did not fit.
        needed: The characters the item needs: its ``reply_chars`` plus
            :data:`PART_OVERHEAD_CHARS`.
        budget: The reply budget given.
    """

    def __init__(self, kind: str, id: str, needed: int, budget: int) -> None:  # noqa: A002
        super().__init__(
            f"PRISMA item {id} needs {needed} reply characters, over the budget of {budget}"
        )
        self.kind = kind
        self.id = id
        self.needed = needed
        self.budget = budget


@dataclass(frozen=True)
class PartPlan:
    """One part of a split: the ids of its items, in the order given."""

    item_ids: tuple[str, ...]

    def label(self) -> str:
        """A label for the part: ``"Methods 5–13c"`` within a section, or
        ``"Methods 13d – Results 17"`` across sections (an en dash in both).
        A one-item part is ``"Methods 13d"``.
        """
        if not self.item_ids:
            return ""
        first, last = self.item_ids[0], self.item_ids[-1]
        first_section = _section_label(first)
        last_section = _section_label(last)
        if first == last:
            return f"{first_section} {first}"
        if first_section == last_section:
            return f"{first_section} {first}–{last}"
        return f"{first_section} {first} – {last_section} {last}"


def _section_label(item_id: str) -> str:
    row = item(item_id)
    return _SECTION_LABELS[row.section] if row else ""


def plan_parts(items: Sequence[ChecklistItem], reply_budget_chars: int) -> list[PartPlan]:
    """Split ``items`` into parts whose replies fit ``reply_budget_chars``.

    Greedy, in the order given: a part takes the next item while
    :data:`PART_OVERHEAD_CHARS` plus the sum of the items' ``reply_chars`` is
    at most the budget.  Callers pass the whole checklist for a run, or a
    subset for a re-check or a split part.

    Raises:
        PartsError: ``item_too_large``, for the first item that does not fit a
            part alone, before any part is returned.
    """
    parts: list[list[str]] = []
    used = 0
    for row in items:
        alone = PART_OVERHEAD_CHARS + row.reply_chars
        if alone > reply_budget_chars:
            raise PartsError("item_too_large", row.id, alone, reply_budget_chars)
        if parts and used + row.reply_chars <= reply_budget_chars:
            parts[-1].append(row.id)
            used += row.reply_chars
        else:
            parts.append([row.id])
            used = alone
    return [PartPlan(tuple(ids)) for ids in parts]


def part_query(items: Sequence[ChecklistItem]) -> str:
    """The condensation query for a part: the items' ``condense_hint``s joined
    with ``"; "``, after "Everything needed to judge these PRISMA 2020 items: ".
    """
    return _QUERY_PREFIX + "; ".join(row.condense_hint for row in items)
