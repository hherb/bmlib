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

"""The shared Anthropic thinking contract, ``tests/fixtures/anthropic_thinking.json`` (#707).

Rust's ``rust/bmlib/tests/llm_anthropic_thinking.rs`` reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmlib.llm.providers.anthropic import ThinkingRefusedError, resolve_thinking

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "anthropic_thinking.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_case(case):
    body = FIXTURE["models"][case["model"]]
    expect = case["expect"]
    if "refusal" in expect:
        with pytest.raises(ThinkingRefusedError) as raised:
            resolve_thinking(case["think"], case["max_tokens"], body)
        assert raised.value.reason == expect["refusal"]
    else:
        assert resolve_thinking(case["think"], case["max_tokens"], body) == expect["fields"]


def test_every_refusal_reason_is_exercised():
    reasons = {c["expect"]["refusal"] for c in FIXTURE["cases"] if "refusal" in c["expect"]}
    assert reasons == {
        "no-capabilities",
        "not-supported",
        "budget-not-supported",
        "effort-not-supported",
        "negative-budget",
        "max-tokens-too-small",
    }


def test_every_model_body_is_used():
    assert {c["model"] for c in FIXTURE["cases"]} == set(FIXTURE["models"])


@pytest.mark.parametrize("case", FIXTURE["off"], ids=lambda c: c["name"])
def test_off_is_falsy(case):
    # Python's truthiness, which Rust's ``think_is_on`` follows; that the
    # provider makes no lookup for these is
    # ``test_llm_thinking.py::test_no_think_makes_no_lookup``.
    assert not case["think"]
