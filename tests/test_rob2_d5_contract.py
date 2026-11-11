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

"""The shared D5 contracts: ``rob2_d5_checks.json``.

The same file Rust's ``rust/bmlib/tests/rob2_d5_contract.rs`` asserts against.
Written by ``scripts/make_rob2_d5_fixtures.py``, which must still build it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from bmlib.quality.rob2_d5_checks import (
    D5Fact,
    fact_verdict,
    names_outcome,
    shows_result,
    states_a_plan,
    supports,
)

FIXTURES = Path(__file__).parent / "fixtures"
CHECKS: dict[str, Any] = json.loads((FIXTURES / "rob2_d5_checks.json").read_text("utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_rob2_d5_fixtures.py"


def test_every_check_case() -> None:
    for case in CHECKS["supports"]:
        assert supports(D5Fact(case["fact"]), case["quote"], case["outcome"]) is case["expected"], (
            case
        )
    for case in CHECKS["names_outcome"]:
        assert names_outcome(case["quote"], case["outcome"]) is case["expected"], case
    for case in CHECKS["states_a_plan"]:
        assert states_a_plan(case["quotes"]) is case["expected"], case
    for case in CHECKS["shows_result"]:
        assert shows_result(case["passages"]) is case["expected"], case
    for case in CHECKS["fact_verdict"]:
        verdict = fact_verdict(
            D5Fact(case["fact"]), case["claimed"], case["found"], case["outcome"]
        )
        assert verdict.value == case["expected"], case


def test_the_cases_cover_both_outcomes_of_every_check() -> None:
    for section in ("supports", "names_outcome", "states_a_plan", "shows_result"):
        assert {c["expected"] for c in CHECKS[section]} == {True, False}, section
    assert {c["expected"] for c in CHECKS["fact_verdict"]} == {
        "present",
        "not_claimed",
        "no_quote_found",
        "quote_does_not_support",
        "no_plan_stated",
    }


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("bmlib_make_rob2_d5_fixtures", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    return make


def test_the_generator_still_builds_the_fixtures() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    assert _generator().build_checks() == CHECKS
