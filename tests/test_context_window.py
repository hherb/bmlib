# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for the known context-window table, held to
``tests/fixtures/context_window.json`` (the Rust port reads the same file)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmlib.llm.context_window import KNOWN_CONTEXT_WINDOWS, known_context_window

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "context_window.json").read_text())


def test_table_matches_the_fixture():
    rows = {(r["provider"], r["model"], r["window"]) for r in FIXTURE["table"]}
    assert len(rows) == len(FIXTURE["table"])
    assert rows == {(p, m, w) for (p, m), w in KNOWN_CONTEXT_WINDOWS.items()}


@pytest.mark.parametrize("case", FIXTURE["table_lookups"], ids=lambda c: c["name"])
def test_table_lookups(case):
    expected = None if case["expect"].get("unknown") else case["expect"]["window"]
    assert known_context_window(case["provider"], case["model"]) == expected
