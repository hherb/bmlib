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

"""The shared RoB 2 contract.

The same file Rust's ``rust/bmlib/tests/rob2_algorithm_contract.rs`` asserts
against: the hand-listed official cases, every exhaustive walk, the refusals,
the answer parser and the overall judgement. The fixture is written by
``scripts/make_rob2_algorithm.py``, which must still build it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.rob2_algorithm import (
    ROB2_ALGORITHM_VERSION,
    Answer,
    Domain,
    Judgement,
    Rob2Error,
    applicable_questions,
    domain_judgement,
    needs_escalation_decision,
    overall_judgement,
)

FIXTURE = Path(__file__).parent / "fixtures" / "rob2_algorithm.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_rob2_algorithm.py"


def _answers(raw: dict[str, str]) -> dict[str, Answer]:
    return {q: Answer(a) for q, a in raw.items()}


def test_the_version_is_the_contracts() -> None:
    assert ROB2_ALGORITHM_VERSION == CONTRACT["algorithm_version"]


@pytest.mark.parametrize("section", ["official", "exhaustive"])
def test_every_case_gives_the_expected_judgement(section: str) -> None:
    cases = CONTRACT[section]
    assert cases
    wrong = [
        c
        for c in cases
        if domain_judgement(Domain(c["domain"]), _answers(c["answers"])).value != c["expected"]
    ]
    assert not wrong, wrong[:3]


def test_applicable_questions_are_the_expected_ones() -> None:
    for c in CONTRACT["applicable"]:
        assert applicable_questions(Domain(c["domain"]), _answers(c["answers"])) == c["expected"]


def test_the_parser_reads_and_refuses_as_the_contract_says() -> None:
    for c in CONTRACT["parse"]:
        if c["expected"] is None:
            with pytest.raises(Rob2Error) as caught:
                Answer.parse(c["value"])
            assert caught.value.kind == "UnreadableAnswer"
        else:
            assert Answer.parse(c["value"]).value == c["expected"]


def test_every_refusal_is_refused_with_its_kind_and_question() -> None:
    for c in CONTRACT["refusals"]:
        with pytest.raises(Rob2Error) as caught:
            domain_judgement(Domain(c["domain"]), _answers(c["answers"]))
        assert (caught.value.kind, caught.value.question) == (c["kind"], c["question"]), c["name"]


def test_the_overall_judgement_is_the_expected_one() -> None:
    for c in CONTRACT["overall"]:
        domains = [Judgement(d) for d in c["domains"]]
        assert needs_escalation_decision(domains) == c["needs_escalation_decision"]
        if "error" in c:
            with pytest.raises(Rob2Error) as caught:
                overall_judgement(domains, c["escalate"])
            assert caught.value.kind == c["error"]
        else:
            assert overall_judgement(domains, c["escalate"]).value == c["expected"]


def test_the_generator_still_builds_the_fixture() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    spec = importlib.util.spec_from_file_location("bmlib_make_rob2_algorithm", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    assert make.build_contract() == CONTRACT
    expected = json.dumps(make.build_contract(), ensure_ascii=False, indent=1) + "\n"
    assert FIXTURE.read_text(encoding="utf-8") == expected
