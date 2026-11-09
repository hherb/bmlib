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

"""Write ``tests/fixtures/evidence_ladder.json`` from the Python ladder.

The shared contract Python and Rust both assert against: ``ladder_version``,
``default_settings``, ``cases`` (inputs with the expected ladder as a dict),
``start_levels``, ``paper_weights`` and ``refusals`` (results that must fail
``validate``).

    python scripts/make_evidence_ladder.py [tests/fixtures/evidence_ladder.json]

``tests/test_evidence_ladder_contract.py`` checks that this script still builds
the committed fixture, so add a case here and re-run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bmlib.quality.data_models import StudyDesign
from bmlib.quality.evidence_ladder import (
    LADDER_VERSION,
    EffectScale,
    LadderSettings,
    Level,
    ReportedResult,
    build_ladder,
    paper_weight,
    settings_to_wire,
    start_level,
)
from bmlib.quality.rob2_algorithm import Judgement

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tests/fixtures/evidence_ladder.json"

_R = EffectScale.RATIO
_D = EffectScale.DIFFERENCE

# (name, rob overall, result or None, settings overrides)
CASES: list[tuple[str, Judgement, ReportedResult | None, dict[str, Any]]] = [
    ("clean", Judgement.LOW, ReportedResult("risk ratio", _R, 0.7, 0.55, 0.88, 0.95, 1200), {}),
    (
        "some concerns, crossing",
        Judgement.SOME_CONCERNS,
        ReportedResult("risk ratio", _R, 0.8, 0.6, 1.02, 0.95, 1200),
        {},
    ),
    (
        "high, crossing, small (floor)",
        Judgement.HIGH,
        ReportedResult("risk ratio", _R, 0.9, 0.5, 1.6, 0.95, 80),
        {},
    ),
    (
        "difference, not crossing",
        Judgement.LOW,
        ReportedResult("mean difference", _D, -2.0, -3.1, -0.9, 0.95, 500),
        {},
    ),
    (
        "touching no effect",
        Judgement.LOW,
        ReportedResult("risk ratio", _R, 0.9, 0.8, 1.0, 0.95, 500),
        {},
    ),
    ("no result", Judgement.LOW, None, {}),
    (
        "no interval, small",
        Judgement.LOW,
        ReportedResult("risk ratio", _R, 0.9, None, None, None, 80),
        {},
    ),
    (
        "sample size unknown",
        Judgement.LOW,
        ReportedResult("risk ratio", _R, 0.7, 0.5, 0.9, 0.95, None),
        {},
    ),
    (
        "cap trims two steps",
        Judgement.LOW,
        ReportedResult("risk ratio", _R, 0.9, 0.5, 1.6, 0.95, 80),
        {"crosses_no_effect_steps": 2, "small_trial_steps": 2, "max_imprecision_steps": 1},
    ),
    (
        "interval without a stated level",
        Judgement.SOME_CONCERNS,
        ReportedResult("risk ratio", _R, 0.8, 0.6, 1.02, None, 1200),
        {},
    ),
    (
        "custom weight",
        Judgement.SOME_CONCERNS,
        ReportedResult("risk ratio", _R, 0.8, 0.6, 1.02, 0.95, 1200),
        {"weight_low": 0.5},
    ),
]

# (name, result); each must fail ``validate``.
REFUSALS: list[tuple[str, ReportedResult]] = [
    ("one bound only", ReportedResult("risk ratio", _R, 0.7, 0.5, None, 0.95, 10)),
    ("ratio bound zero", ReportedResult("risk ratio", _R, 0.7, 0.0, 0.9, 0.95, 10)),
    ("estimate outside", ReportedResult("risk ratio", _R, 0.7, 0.8, 0.9, 0.95, 10)),
    ("level of one", ReportedResult("risk ratio", _R, 0.7, 0.5, 0.9, 1.0, 10)),
    ("level of zero", ReportedResult("risk ratio", _R, 0.7, 0.5, 0.9, 0.0, 10)),
]


def _result_wire(result: ReportedResult | None) -> dict[str, Any] | None:
    """A result as the JSON object serde reads."""
    if result is None:
        return None
    return {
        "measure": result.measure,
        "scale": result.scale.value,
        "estimate": result.estimate,
        "ci_low": result.ci_low,
        "ci_high": result.ci_high,
        "ci_level": result.ci_level,
        "n_analysed": result.n_analysed,
    }


def build_contract() -> dict[str, Any]:
    """Build the whole contract from the Python implementation."""
    cases = []
    for name, rob, result, overrides in CASES:
        settings = LadderSettings(**overrides)
        ladder = build_ladder(Level.HIGH, rob, result, settings)
        cases.append(
            {
                "name": name,
                "start": "high",
                "rob_overall": rob.value,
                "result": _result_wire(result),
                "settings": settings_to_wire(settings),
                "expected": ladder.to_wire(),
            }
        )
    refusals = []
    for name, result in REFUSALS:
        try:
            result.validate()
        except ValueError:
            pass
        else:
            raise AssertionError(f"{name} was accepted")
        refusals.append({"name": name, "result": _result_wire(result)})
    low = build_ladder(Level.HIGH, Judgement.HIGH, None, LadderSettings())
    high = build_ladder(
        Level.HIGH,
        Judgement.LOW,
        ReportedResult("r", _R, 0.7, 0.5, 0.9, 0.95, 900),
        LadderSettings(),
    )
    return {
        "ladder_version": LADDER_VERSION,
        "default_settings": settings_to_wire(LadderSettings()),
        "cases": cases,
        "start_levels": [
            {"design": d.value, "expected": s.value if (s := start_level(d)) else None}
            for d in (StudyDesign.RCT, StudyDesign.COHORT_PROSPECTIVE)
        ],
        "paper_weights": [
            {"ladders": [low.to_wire(), high.to_wire()], "expected": paper_weight([low, high])},
            {"ladders": [], "expected": None},
        ],
        "refusals": refusals,
    }


def main() -> None:
    """Write the contract to the path given, or to the committed fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    text = json.dumps(build_contract(), ensure_ascii=False, indent=1) + "\n"
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
