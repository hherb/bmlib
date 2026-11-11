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

"""The shared evidence-ladder contract.

The same file Rust's ``rust/bmlib/tests/evidence_ladder_contract.rs`` asserts
against. The fixture is written by ``scripts/make_evidence_ladder.py``, which
must still build it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bmlib.quality.data_models import StudyDesign
from bmlib.quality.evidence_ladder import (
    LADDER_VERSION,
    EffectScale,
    LadderSettings,
    Level,
    ReportedResult,
    build_ladder,
    settings_from_wire,
    settings_to_wire,
    start_level,
)
from bmlib.quality.rob2_algorithm import Judgement

FIXTURE = Path(__file__).parent / "fixtures" / "evidence_ladder.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_evidence_ladder.py"


def _result(raw: dict[str, Any] | None) -> ReportedResult | None:
    if raw is None:
        return None
    return ReportedResult(**{**raw, "scale": EffectScale(raw["scale"])})


def test_the_version_is_the_contracts() -> None:
    assert LADDER_VERSION == CONTRACT["ladder_version"]


def test_the_default_settings_are_the_contracts() -> None:
    assert settings_to_wire(LadderSettings()) == CONTRACT["default_settings"]


def test_every_case_builds_the_expected_ladder() -> None:
    assert CONTRACT["cases"]
    for c in CONTRACT["cases"]:
        lad = build_ladder(
            Level(c["start"]),
            Judgement(c["rob_overall"]),
            _result(c["result"]),
            LadderSettings(**c["settings"]),
        )
        assert lad.to_wire() == c["expected"], c["name"]


def test_start_levels() -> None:
    for c in CONTRACT["start_levels"]:
        got = start_level(StudyDesign(c["design"]))
        assert (got.value if got else None) == c["expected"]


def test_every_refusal_is_refused() -> None:
    assert CONTRACT["refusals"]
    for c in CONTRACT["refusals"]:
        with pytest.raises(ValueError):
            _result(c["result"]).validate()  # type: ignore[union-attr]


def test_every_settings_refusal_names_its_field() -> None:
    assert CONTRACT["settings_refusals"]
    for c in CONTRACT["settings_refusals"]:
        with pytest.raises(ValueError) as raised:
            settings_from_wire(c["settings"])
        assert str(raised.value) == c["error"], c["name"]


def test_settings_read_from_the_wire_take_defaults_for_what_is_absent() -> None:
    assert CONTRACT["settings_read"]
    for c in CONTRACT["settings_read"]:
        assert settings_to_wire(settings_from_wire(c["settings"])) == c["expected"], c["name"]


def test_the_generator_still_builds_the_fixture() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    spec = importlib.util.spec_from_file_location("bmlib_make_evidence_ladder", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    assert make.build_contract() == CONTRACT
    expected = json.dumps(make.build_contract(), ensure_ascii=False, indent=1) + "\n"
    assert FIXTURE.read_text(encoding="utf-8") == expected
