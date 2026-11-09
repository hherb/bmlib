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

"""Write ``tests/fixtures/outcome_replies.json`` from the Python reader.

The shared contract Python and Rust both assert against: the prompts, and for
each reply either the proposal read from it or the message it is refused with.

    python scripts/make_outcome_replies.py [tests/fixtures/outcome_replies.json]

``tests/test_outcome_replies_contract.py`` checks that this script still builds
the committed fixture, so add a case here and re-run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bmlib.quality.outcome_agent import (
    OUTCOME_CONDENSE_QUERY,
    OUTCOME_PROMPT_VERSION,
    OUTCOME_SYSTEM_PROMPT,
    OUTCOME_TASK_PROMPT,
    read_outcome_reply,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tests/fixtures/outcome_replies.json"

TEXT = (
    "The primary outcome was all-cause mortality at 28 days. "
    "The risk ratio was 0.70 (95% CI 0.55 to 0.88), n=1200. "
    "A co-primary outcome was hospital admission."
)
MORTALITY = "The primary outcome was all-cause mortality at 28 days."
ADMISSION = "A co-primary outcome was hospital admission."
MIN_SIMILARITY = 0.9


def _result(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "measure": "risk ratio",
        "scale": "ratio",
        "estimate": 0.7,
        "ci_low": 0.55,
        "ci_high": 0.88,
        "ci_level": 0.95,
        "n_analysed": 1200,
    }
    value.update(overrides)
    return value


def _outcome(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "All-cause mortality",
        "timepoint": "28 days",
        "quotes": [MORTALITY],
        "result": _result(),
    }
    value.update(overrides)
    return value


def _reply(*outcomes: Any) -> dict[str, Any]:
    return {"primary_outcomes": list(outcomes)}


_DIFFERENCE = _result(
    scale=" Difference ", measure="mean difference", estimate=-2.0, ci_low=-3.1, ci_high=-0.9
)
_BARE = {"measure": "risk ratio", "scale": "ratio", "estimate": 0.7}
_QUOTES = [
    "Patients were followed for a year.",
    "The primary outcome was all-cause mortality at 90 days.",
    MORTALITY,
    "  ",
    7,
    None,
]

# (name, reply); the expected side is whatever the Python reader makes of it.
CASES: list[tuple[str, Any]] = [
    ("one ratio result", _reply(_outcome())),
    (
        "two co-primary outcomes",
        _reply(
            _outcome(),
            _outcome(name="Hospital admission", timepoint=None, quotes=[ADMISSION], result=None),
        ),
    ),
    ("none named", _reply()),
    ("difference result, scale folded", _reply(_outcome(result=_DIFFERENCE))),
    ("interval and level absent", _reply(_outcome(result=_BARE))),
    ("blank timepoint", _reply(_outcome(timepoint="  "))),
    ("bounds the wrong way round", _reply(_outcome(result=_result(ci_low=0.9, ci_high=0.5)))),
    ("ratio bound zero", _reply(_outcome(result=_result(ci_low=0.0, estimate=0.4)))),
    ("one bound only", _reply(_outcome(result=_result(ci_high=None)))),
    ("estimate outside interval", _reply(_outcome(result=_result(estimate=0.2)))),
    ("level as printed, a percentage", _reply(_outcome(result=_result(ci_level=95)))),
    ("level as printed, 99.9", _reply(_outcome(result=_result(ci_level=99.9)))),
    ("level of 100", _reply(_outcome(result=_result(ci_level=100)))),
    ("level of 1", _reply(_outcome(result=_result(ci_level=1)))),
    ("level of 0", _reply(_outcome(result=_result(ci_level=0)))),
    ("level text", _reply(_outcome(result=_result(ci_level="95%")))),
    (
        "an unreadable result beside a good one",
        _reply(
            _outcome(result=_result(ci_low="NR")),
            _outcome(name="Hospital admission", quotes=[ADMISSION]),
        ),
    ),
    ("negative sample", _reply(_outcome(result=_result(n_analysed=-3)))),
    ("sample over 32 bits", _reply(_outcome(result=_result(n_analysed=2**32)))),
    ("quotes lost, changed and unusable", _reply(_outcome(quotes=_QUOTES))),
    ("scale percent", _reply(_outcome(result=_result(scale="percent")))),
    ("scale not text", _reply(_outcome(result=_result(scale=1)))),
    ("measure not text", _reply(_outcome(result=_result(measure=None)))),
    ("estimate missing", _reply(_outcome(result=_result(estimate=None)))),
    ("estimate a bool", _reply(_outcome(result=_result(estimate=True)))),
    ("estimate text", _reply(_outcome(result=_result(estimate="0.7")))),
    ("bound text", _reply(_outcome(result=_result(ci_low="low")))),
    ("sample a float", _reply(_outcome(result=_result(n_analysed=1200.5)))),
    ("result text", _reply(_outcome(result="risk ratio 0.7"))),
    ("result missing", _reply({"name": "x", "quotes": []})),
    ("name blank", _reply(_outcome(name="  "))),
    ("name missing", _reply(_outcome(name=None))),
    ("timepoint a number", _reply(_outcome(timepoint=3))),
    ("outcome not an object", _reply("mortality")),
    ("list not a list", {"primary_outcomes": "none"}),
    ("list missing", {}),
    ("reply not an object", ["mortality"]),
]


def build_contract() -> dict[str, Any]:
    """Build the whole contract from the Python implementation."""
    cases = []
    for name, reply in CASES:
        case: dict[str, Any] = {"name": name, "reply": reply}
        try:
            case["expected"] = read_outcome_reply(reply, TEXT, MIN_SIMILARITY).to_dict()
        except ValueError as exc:
            case["error"] = str(exc)
        cases.append(case)
    return {
        "prompt_version": OUTCOME_PROMPT_VERSION,
        "system_prompt": OUTCOME_SYSTEM_PROMPT,
        "task_prompt": OUTCOME_TASK_PROMPT,
        "condense_query": OUTCOME_CONDENSE_QUERY,
        "text": TEXT,
        "min_similarity": MIN_SIMILARITY,
        "cases": cases,
    }


def main() -> None:
    """Write the contract to the path given, or to the committed fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    text = json.dumps(build_contract(), ensure_ascii=False, indent=1) + "\n"
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
