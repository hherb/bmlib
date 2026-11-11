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

"""The shared evidence-weight contract.

The same file Rust's ``rust/bmlib/tests/evidence_weight_contract.rs`` asserts
against: for each case the model's replies in order and the ``EvidenceWeight``
the pipeline returns, and for each refusal a change to the assessed case's wire
form and the message reading it gives.  The fixture is written by
``scripts/make_evidence_weight.py``, which must still build it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.llm.data_types import LLMResponse
from bmlib.quality.evidence_ladder import LadderSettings
from bmlib.quality.evidence_weight import assess_unattended, evidence_weight_from_dict

FIXTURE = Path(__file__).parent / "fixtures" / "evidence_weight.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_evidence_weight.py"


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bmlib_make_evidence_weight", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    return make


def test_every_status_and_every_ladder_error_is_covered() -> None:
    statuses = {c["expected"]["status"] for c in CONTRACT["cases"]}
    assert statuses == {"not_covered", "no_primary_outcome", "assessed"}
    errors = [a["ladder_error"] for a in CONTRACT["cases"][-1]["expected"]["assessed"]]
    assert any(e and e.startswith("the reported result was refused") for e in errors)
    assert any(e and e.startswith("the outcome's risk of bias was not assessed") for e in errors)
    assert None in errors


def test_every_case_runs_to_the_expected_result_and_reads_back() -> None:
    for case in CONTRACT["cases"]:
        llm = MagicMock()
        llm.chat = MagicMock(
            side_effect=[
                LLMResponse(json.dumps(r), "test:model", 1, 1, "stop") for r in case["replies"]
            ]
        )
        result = assess_unattended(
            llm,
            "test:model",
            settings=LadderSettings(),
            title=CONTRACT["title"],
            text=CONTRACT["text"],
            min_similarity=CONTRACT["min_similarity"],
        )
        assert result.to_dict() == case["expected"], case["name"]
        assert llm.chat.call_count == len(case["replies"]), case["name"]
        assert evidence_weight_from_dict(case["expected"]) == result, case["name"]


def test_every_refusal_names_what_does_not_add_up() -> None:
    apply_change = _generator().apply_change
    assert CONTRACT["refusals"]
    for case in CONTRACT["refusals"]:
        wire = copy.deepcopy(CONTRACT["cases"][-1]["expected"])
        apply_change(wire, case["change"])
        with pytest.raises(ValueError) as raised:
            evidence_weight_from_dict(wire)
        assert str(raised.value) == case["error"], case["name"]


def test_the_generator_still_builds_the_fixture() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    make = _generator()
    assert make.build_contract() == CONTRACT
    expected = json.dumps(make.build_contract(), ensure_ascii=False, indent=1) + "\n"
    assert FIXTURE.read_text(encoding="utf-8") == expected
