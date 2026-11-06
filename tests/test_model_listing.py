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

"""The shared model-listing contract, ``tests/fixtures/model_listing.json``
(the same file Rust's ``rust/bmlib/tests/llm_model_listing.rs`` reads, #680)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmlib.llm.model_listing import ModelListingError, ModelListingPage, read_model_listing

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "model_listing.json").read_text())


def test_fixture_has_cases() -> None:
    assert FIXTURE["listings"]


@pytest.mark.parametrize("case", FIXTURE["listings"], ids=lambda c: c["name"])
def test_listing_agrees_with_the_fixture(case: dict) -> None:
    expect = case["expect"]
    if expect.get("error") == "malformed":
        with pytest.raises(ModelListingError):
            read_model_listing(case["provider"], case["body"])
        return
    got = read_model_listing(case["provider"], case["body"])
    assert got == ModelListingPage(ids=expect["ids"], next_after=expect["next_after"])


def test_a_bool_id_is_not_an_id() -> None:
    # ``True`` is an ``int`` in Python; neither is a string id.
    with pytest.raises(ModelListingError):
        read_model_listing("openai", {"data": [{"id": True}]})


def test_a_truthy_has_more_that_is_not_true_does_not_page() -> None:
    # Rust reads ``has_more`` with ``as_bool``; ``1`` is no bool there.
    got = read_model_listing("anthropic", {"data": [], "has_more": 1, "last_id": "a"})
    assert got.next_after is None
