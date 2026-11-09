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

"""The shared design-reply contract.

The same file Rust's ``rust/bmlib/tests/design_replies_contract.rs`` asserts
against: the prompts, and for each reply either the proposal read from it or
the message it is refused with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.design_agent import (
    DESIGN_CONDENSE_QUERY,
    DESIGN_PROMPT_VERSION,
    DESIGN_SYSTEM_PROMPT,
    DESIGN_TASK_PROMPT,
    DesignProposal,
    read_design_reply,
)

FIXTURE = Path(__file__).parent / "fixtures" / "design_replies.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_prompts_are_the_contracts() -> None:
    assert DESIGN_PROMPT_VERSION == CONTRACT["prompt_version"]
    assert DESIGN_SYSTEM_PROMPT == CONTRACT["system_prompt"]
    assert DESIGN_TASK_PROMPT == CONTRACT["task_prompt"]
    assert DESIGN_CONDENSE_QUERY == CONTRACT["condense_query"]


def test_every_reply_reads_as_expected_or_is_refused_as_expected() -> None:
    assert CONTRACT["cases"]
    for case in CONTRACT["cases"]:
        args = (case["reply"], CONTRACT["text"], CONTRACT["min_similarity"])
        if "error" in case:
            with pytest.raises(ValueError) as raised:
                read_design_reply(*args)
            assert str(raised.value) == case["error"], case["name"]
        else:
            proposal = read_design_reply(*args)
            assert proposal.to_dict() == case["expected"], case["name"]
            assert DesignProposal.from_dict(case["expected"]) == proposal, case["name"]
