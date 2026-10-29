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

"""Tests for the known context-window table, held to
``tests/fixtures/context_window.json`` (the Rust port reads the same file)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmlib.llm.context_window import (
    KNOWN_CONTEXT_WINDOWS,
    NoSuchModelError,
    known_context_window,
    read_context_window,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "context_window.json").read_text())


def test_table_matches_the_fixture():
    rows = {(r["provider"], r["model"], r["window"]) for r in FIXTURE["table"]}
    assert len(rows) == len(FIXTURE["table"])
    assert rows == {(p, m, w) for (p, m), w in KNOWN_CONTEXT_WINDOWS.items()}


@pytest.mark.parametrize("case", FIXTURE["table_lookups"], ids=lambda c: c["name"])
def test_table_lookups(case):
    expected = None if case["expect"].get("unknown") else case["expect"]["window"]
    assert known_context_window(case["provider"], case["model"]) == expected


@pytest.mark.parametrize("case", FIXTURE["metadata"], ids=lambda c: c["name"])
def test_metadata_cases(case):
    expect = case["expect"]
    args = (case["provider"], case["model"], case["body"])
    if expect.get("error") == "no_such_model":
        with pytest.raises(NoSuchModelError) as exc:
            read_context_window(*args)
        assert exc.value.provider == case["provider"]
        assert exc.value.model == case["model"]
    else:
        want = None if expect.get("unknown") else expect["window"]
        assert read_context_window(*args) == want


def test_provider_without_metadata_is_a_value_error():
    with pytest.raises(ValueError):
        read_context_window("openai", "gpt-4o-mini", {})
