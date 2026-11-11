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

"""The shared RoB 2 contracts, ``rob2_replies.json`` and ``rob2_run.json``.

The same files Rust's ``rust/bmlib/tests/rob2_contract.rs`` asserts against:
the wording and its renderings, each domain and escalation reply's reading or
refusal, and a whole run that must round-trip unchanged.  Both are written by
``scripts/make_rob2_fixtures.py``, which must still build them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.rob2_agent import (
    ROB2_DOMAIN_PROMPT,
    ROB2_SYSTEM_PROMPT,
    DomainAssessment,
    Escalation,
    Rob2Run,
    domain_prompt,
    escalation_message,
    read_domain_reply,
    read_escalation_reply,
)
from bmlib.quality.rob2_algorithm import Domain, questions
from bmlib.quality.rob2_questions import (
    D5_OUTCOME_SWITCHING,
    ESCALATION_PROMPT,
    ROB2_CONDENSE_QUERIES,
    ROB2_PROMPT_VERSION,
    domain_title,
    question_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPLIES: dict[str, Any] = json.loads((FIXTURES / "rob2_replies.json").read_text("utf-8"))
RUN: dict[str, Any] = json.loads((FIXTURES / "rob2_run.json").read_text("utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_rob2_fixtures.py"


def test_the_wording_is_the_contracts() -> None:
    assert ROB2_PROMPT_VERSION == REPLIES["prompt_version"]
    assert ROB2_SYSTEM_PROMPT == REPLIES["system_prompt"]
    assert ROB2_DOMAIN_PROMPT == REPLIES["domain_prompt"]
    assert D5_OUTCOME_SWITCHING == REPLIES["d5_outcome_switching"]
    assert ESCALATION_PROMPT == REPLIES["escalation_prompt"]
    ids = [q for d in Domain for q in questions(d)]
    assert list(REPLIES["question_texts"]) == ids
    assert all(question_text(q) == REPLIES["question_texts"][q] for q in ids)
    assert {d.value: domain_title(d) for d in Domain} == REPLIES["domain_titles"]
    assert [[d.value, q] for d, q in ROB2_CONDENSE_QUERIES] == REPLIES["condense_queries"]


def test_the_prompts_render_as_the_contract_says() -> None:
    rendered = REPLIES["rendered"]
    title, text = REPLIES["title"], REPLIES["text"]
    outcome = ProposedOutcome.from_dict(REPLIES["outcome"])
    second = ProposedOutcome.from_dict(REPLIES["outcome_without_result"])
    assert domain_prompt(Domain.D1, title, text, None) == rendered["D1"]
    assert domain_prompt(Domain.D2, title, text, second) == rendered["D2 without a result"]
    assert domain_prompt(Domain.D5, title, text, outcome) == rendered["D5"]
    study = DomainAssessment.from_dict(rendered["escalation_study"])
    domains = [DomainAssessment.from_dict(d) for d in rendered["escalation_domains"]]
    assert escalation_message(outcome, study, domains) == rendered["escalation"]


def test_every_domain_reply_reads_as_expected_or_is_refused_as_expected() -> None:
    assert REPLIES["cases"]
    for case in REPLIES["cases"]:
        args = (Domain(case["domain"]), case["reply"], REPLIES["text"], REPLIES["min_similarity"])
        if "error" in case:
            with pytest.raises(ValueError) as raised:
                read_domain_reply(*args)
            assert str(raised.value) == case["error"], case["name"]
        else:
            assessment = read_domain_reply(*args)
            assert assessment.to_dict() == case["expected"], case["name"]
            assert DomainAssessment.from_dict(case["expected"]) == assessment, case["name"]


def test_every_escalation_reply_reads_as_expected_or_is_refused_as_expected() -> None:
    for case in REPLIES["escalation_cases"]:
        if "error" in case:
            with pytest.raises(ValueError) as raised:
                read_escalation_reply(case["reply"])
            assert str(raised.value) == case["error"], case["name"]
        else:
            escalation = read_escalation_reply(case["reply"])
            assert escalation.to_dict() == case["expected"], case["name"]
            assert Escalation.from_dict(case["expected"]) == escalation, case["name"]


def test_the_contract_covers_the_review_focus() -> None:
    cases = {c["name"]: c for c in REPLIES["cases"]}
    assert "2.3" in cases["D2 NA for an applicable question"]["error"]
    assert "3.2" in cases["D3 NI where none is allowed"]["error"]
    unreported = cases["D3 amount missing unreported, 3.3 still answered"]["expected"]
    assert list(unreported["answers"]) == ["3.1", "3.2", "3.3", "3.4"]
    assert unreported["judgement"] == "some_concerns"
    assert "error" in cases["answer outside the set"]
    assert cases["answer case and space tolerated"]["expected"]["answers"]["1.1"]["answer"] == "PY"
    assert cases["D2 answer to an unasked question is ignored"]["expected"]["ignored"] == ["2.7"]
    lost = cases["D3 quotes lost, contradicted and unusable"]["expected"]["answers"]["3.1"]
    assert lost["quotes_not_found"] and lost["contradicted"] and lost["quotes_dropped"] == 2


def test_the_run_round_trips_unchanged() -> None:
    run = Rob2Run.from_dict(RUN)
    assert run.to_dict() == RUN
    overall = [o["overall"] for o in RUN["outcomes"]]
    assert overall == ["high", None, None]


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("bmlib_make_rob2_fixtures", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    return make


def test_the_generator_still_builds_the_fixtures() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    make = _generator()
    for name, build in (
        ("rob2_replies.json", make.build_replies),
        ("rob2_run.json", make.build_run),
    ):
        expected = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
        assert (FIXTURES / name).read_text(encoding="utf-8") == expected, name
