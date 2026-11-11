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

"""The shared D5 contracts: ``rob2_d5_checks.json`` and ``rob2_d5_facts.json``.

The same file Rust's ``rust/bmlib/tests/rob2_d5_contract.rs`` asserts against.
Written by ``scripts/make_rob2_d5_fixtures.py``, which must still build it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.rob2_agent import DomainAssessment, d5_assessment
from bmlib.quality.rob2_d5_checks import (
    D5Fact,
    fact_verdict,
    names_outcome,
    shows_result,
    states_a_plan,
    supports,
)
from bmlib.quality.rob2_d5_facts import (
    D5_FACTS_SYSTEM_PROMPT,
    D5_SECOND_LOOK_SYSTEM_PROMPT,
    D5Facts,
    OtherMeasurement,
    facts_prompt,
    read_facts_reply,
    read_second_look,
    second_look_prompt,
)

FIXTURES = Path(__file__).parent / "fixtures"
CHECKS: dict[str, Any] = json.loads((FIXTURES / "rob2_d5_checks.json").read_text("utf-8"))
FACTS: dict[str, Any] = json.loads((FIXTURES / "rob2_d5_facts.json").read_text("utf-8"))
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


def test_the_prompts_are_the_contracts() -> None:
    outcome = ProposedOutcome.from_dict(FACTS["outcome"])
    assert D5_FACTS_SYSTEM_PROMPT == FACTS["system_prompt"]
    assert D5_SECOND_LOOK_SYSTEM_PROMPT == FACTS["second_look_system_prompt"]
    assert facts_prompt(FACTS["title"], FACTS["paper"], outcome) == FACTS["facts_prompt"]
    item = D5Facts.from_dict(
        {**FACTS["looked"]["facts"], "other_measurements": [FACTS["second_look_item"]]}
    ).measurements[0]
    assert isinstance(item, OtherMeasurement)
    look = second_look_prompt(FACTS["title"], FACTS["paper"], outcome, item)
    assert look == FACTS["second_look_prompt"]


def test_every_facts_reply_reads_as_the_contract_says() -> None:
    names = set()
    for case in FACTS["replies"]:
        names.add(case["name"])
        if "error" in case:
            try:
                read_facts_reply(
                    case["reply"], FACTS["paper"], FACTS["outcome"]["name"], FACTS["min_similarity"]
                )
            except ValueError as exc:
                assert str(exc) == case["error"], case["name"]
            else:
                raise AssertionError(f"{case['name']} was read")
            continue
        facts = read_facts_reply(
            case["reply"], FACTS["paper"], FACTS["outcome"]["name"], FACTS["min_similarity"]
        )
        assert facts.to_dict() == case["facts"], case["name"]
        assessment = d5_assessment(facts)
        assert {q: a.answer.value for q, a in assessment.answers.items()} == case["answers"]
        assert assessment.judgement.value == case["judgement"], case["name"]
    assert len(names) == len(FACTS["replies"])


def test_every_second_look_reads_as_the_contract_says() -> None:
    for case in FACTS["second_looks"]:
        if "error" in case:
            try:
                read_second_look(case["reply"], FACTS["paper"], FACTS["min_similarity"])
            except ValueError as exc:
                assert str(exc) == case["error"]
            else:
                raise AssertionError(f"{case} was read")
        else:
            found = read_second_look(case["reply"], FACTS["paper"], FACTS["min_similarity"])
            assert [c.to_dict() for c in found] == case["found"]


def test_stored_facts_give_the_contracts_answers_and_round_trip() -> None:
    looked = FACTS["looked"]
    facts = D5Facts.from_dict(looked["facts"])
    assert facts.to_dict() == looked["facts"]
    assessment = d5_assessment(facts)
    assert {q: a.answer.value for q, a in assessment.answers.items()} == looked["answers"]
    assert assessment.judgement.value == looked["judgement"]
    assert assessment.to_dict() == looked["assessment"]
    assert DomainAssessment.from_dict(looked["assessment"]).to_dict() == looked["assessment"]


def test_the_generator_still_builds_the_fixtures() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    make = _generator()
    assert make.build_checks() == CHECKS
    assert make.build_facts() == FACTS
