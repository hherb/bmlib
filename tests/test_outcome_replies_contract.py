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

"""The shared outcome-reply contract.

The same file Rust's ``rust/bmlib/tests/outcome_replies_contract.rs`` asserts
against: the prompts, and for each reply either the proposal read from it or
the message it is refused with.  The fixture is written by
``scripts/make_outcome_replies.py``, which must still build it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.outcome_agent import (
    OUTCOME_CONDENSE_QUERY,
    OUTCOME_PROMPT_VERSION,
    OUTCOME_SYSTEM_PROMPT,
    OUTCOME_TASK_PROMPT,
    OutcomeProposal,
    read_outcome_reply,
)

FIXTURE = Path(__file__).parent / "fixtures" / "outcome_replies.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_outcome_replies.py"


def test_the_prompts_are_the_contracts() -> None:
    assert OUTCOME_PROMPT_VERSION == CONTRACT["prompt_version"]
    assert OUTCOME_SYSTEM_PROMPT == CONTRACT["system_prompt"]
    assert OUTCOME_TASK_PROMPT == CONTRACT["task_prompt"]
    assert OUTCOME_CONDENSE_QUERY == CONTRACT["condense_query"]


def test_every_reply_reads_as_expected_or_is_refused_as_expected() -> None:
    assert CONTRACT["cases"]
    for case in CONTRACT["cases"]:
        args = (case["reply"], CONTRACT["text"], CONTRACT["min_similarity"])
        if "error" in case:
            with pytest.raises(ValueError) as raised:
                read_outcome_reply(*args)
            assert str(raised.value) == case["error"], case["name"]
        else:
            proposal = read_outcome_reply(*args)
            assert proposal.to_dict() == case["expected"], case["name"]
            assert OutcomeProposal.from_dict(case["expected"]) == proposal, case["name"]


def test_the_contract_covers_each_kind_of_outcome() -> None:
    cases = {c["name"]: c for c in CONTRACT["cases"]}
    assert cases["none named"]["expected"]["outcomes"] == []
    refused = cases["bounds the wrong way round"]["expected"]["outcomes"][0]
    assert refused["result"] is None and refused["result_error"]
    assert "error" in cases["scale percent"]


def test_the_generator_still_builds_the_fixture() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    spec = importlib.util.spec_from_file_location("bmlib_make_outcome_replies", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    expected = json.dumps(make.build_contract(), ensure_ascii=False, indent=1) + "\n"
    assert make.build_contract() == CONTRACT
    assert FIXTURE.read_text(encoding="utf-8") == expected
