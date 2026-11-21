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

"""Reading a PRISMA 2020 part's reply under the strict-evidence rule."""

from __future__ import annotations

import logging

import pytest

from bmlib.quality.prisma2020_models import (
    Judgement,
    read_part_reply,
    read_suitability,
    shown_judgement,
)

TEXT = "Methods. We searched MEDLINE and Embase from inception to March 2020."
QUOTE = "We searched MEDLINE and Embase from inception to March 2020"


def _reply(*items: dict) -> dict:
    return {"items": list(items)}


def _entry(item_id: str, judgement: str, quotes: list[str] | None = None) -> dict:
    return {"id": item_id, "judgement": judgement, "explanation": "why", "quotes": quotes or []}


@pytest.mark.parametrize(
    "claim",
    [
        Judgement.REPORTED,
        Judgement.PARTLY_REPORTED,
        Judgement.REPORTED_ELSEWHERE,
        Judgement.NOT_APPLICABLE,
    ],
)
def test_unbacked_claim_is_not_reported(claim: Judgement) -> None:
    assert shown_judgement(claim, 0, False) == (Judgement.NOT_REPORTED, True)


def test_backed_claim_stands() -> None:
    assert shown_judgement(Judgement.PARTLY_REPORTED, 1, False) == (
        Judgement.PARTLY_REPORTED,
        False,
    )


def test_not_applicable_needs_the_flag() -> None:
    assert shown_judgement(Judgement.NOT_APPLICABLE, 1, False) == (Judgement.NOT_REPORTED, True)
    assert shown_judgement(Judgement.NOT_APPLICABLE, 1, True) == (Judgement.NOT_APPLICABLE, False)


def test_not_reported_is_never_upgraded() -> None:
    part = read_part_reply(_reply(_entry("7", "not_reported", [QUOTE])), ["7"], TEXT, 0.85)
    row = part.items[0]
    assert row.model_judgement is Judgement.NOT_REPORTED
    assert row.judgement is Judgement.NOT_REPORTED
    assert row.unbacked is False
    assert len(row.citations) == 1


def test_backed_item_is_read_with_its_citation() -> None:
    part = read_part_reply(_reply(_entry("7", "reported", [QUOTE])), ["7"], TEXT, 0.85)
    row = part.items[0]
    assert row.id == "7"
    assert (row.judgement, row.unbacked) == (Judgement.REPORTED, False)
    assert row.to_dict()["judgement"] == "reported"


def test_missing_item_is_listed() -> None:
    part = read_part_reply(_reply(_entry("7", "reported", [QUOTE])), ["7", "8"], TEXT, 0.85)
    assert part.missing == ["8"]
    assert [row.id for row in part.items] == ["7"]


def test_extra_item_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    reply = _reply(_entry("7", "reported", [QUOTE]), _entry("99", "reported"))
    with caplog.at_level(logging.WARNING, logger="bmlib.quality.prisma2020_models"):
        part = read_part_reply(reply, ["7"], TEXT, 0.85)
    assert [row.id for row in part.items] == ["7"]
    assert any(r.levelno == logging.WARNING and "99" in r.getMessage() for r in caplog.records)


def test_unknown_judgement_is_unreadable() -> None:
    with pytest.raises(ValueError, match="maybe"):
        read_part_reply(_reply(_entry("7", "maybe")), ["7"], TEXT, 0.85)


def test_duplicate_id_is_unreadable() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        read_part_reply(_reply(_entry("7", "reported"), _entry("7", "reported")), ["7"], TEXT, 0.85)


def test_integer_id_is_read_as_its_digits() -> None:
    # Ruling 8: ``"id": 7`` is the likeliest model slip; a re-ask would cost a part.
    entry = {**_entry("7", "reported", [QUOTE]), "id": 7}
    part = read_part_reply(_reply(entry), ["7"], TEXT, 0.85)
    assert [row.id for row in part.items] == ["7"]
    assert part.missing == []


@pytest.mark.parametrize("bad", [True, 7.0, None, ["7"]])
def test_other_id_types_are_unreadable(bad: object) -> None:
    with pytest.raises(ValueError, match="id"):
        read_part_reply(_reply({**_entry("7", "reported"), "id": bad}), ["7"], TEXT, 0.85)


def test_items_must_be_a_list() -> None:
    with pytest.raises(ValueError, match="items"):
        read_part_reply({"items": "no"}, ["7"], TEXT, 0.85)


@pytest.mark.parametrize(
    ("sr", "ma", "suitable"), [(True, False, True), (False, True, True), (False, False, False)]
)
def test_suitable_is_sr_or_ma(sr: bool, ma: bool, suitable: bool) -> None:
    result = read_suitability(
        {
            "is_systematic_review": sr,
            "is_meta_analysis": ma,
            "document_type": "x",
            "variant": None,
            "rationale": "r",
            "confidence": 0.9,
        }
    )
    assert result.is_suitable is suitable


def test_variant_kept_as_given() -> None:
    reply = {
        "is_systematic_review": True,
        "is_meta_analysis": False,
        "document_type": "SR",
        "variant": "Living SR (odd)",
        "rationale": "r",
        "confidence": 3,
    }
    result = read_suitability(reply)
    assert result.variant == "Living SR (odd)"
    assert result.confidence == 1.0
    assert result.to_dict()["variant"] == "Living SR (odd)"


def test_suitability_flag_must_be_boolean() -> None:
    with pytest.raises(ValueError, match="is_systematic_review"):
        read_suitability({"is_systematic_review": "yes", "is_meta_analysis": False})
