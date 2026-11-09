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

"""Write ``tests/fixtures/rob2_algorithm.json`` from the Python decision rules.

The shared RoB 2 contract, which Python and Rust both assert against. Sections:

- ``official``: cases listed by hand from the guidance's tables, each citing its
  table. They are **not** computed, so a rule transcribed wrongly in the code
  disagrees with them.
- ``exhaustive``: every walk of every domain over Y / N / NI (PY and PN judge
  as Y and N, which the unit tests check), with Python's judgement.
- ``applicable``, ``parse``, ``refusals``, ``overall``: the rest of the surface.

    python scripts/make_rob2_algorithm.py [tests/fixtures/rob2_algorithm.json]

``tests/test_rob2_algorithm_contract.py`` checks that this script still builds
the committed fixture, so add a case here and re-run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tests/fixtures/rob2_algorithm.json"

# (source, domain, answers, expected judgement); transcribed from the guidance.
OFFICIAL: list[tuple[str, str, dict[str, str], str]] = [
    ("Table 4", "D1", {"1.1": "Y", "1.2": "Y", "1.3": "N"}, "low"),
    ("Table 4", "D1", {"1.1": "NI", "1.2": "PY", "1.3": "NI"}, "low"),
    ("Table 4", "D1", {"1.1": "Y", "1.2": "Y", "1.3": "Y"}, "some_concerns"),
    ("Table 3", "D1", {"1.1": "N", "1.2": "Y", "1.3": "N"}, "some_concerns"),
    ("Table 4", "D1", {"1.1": "Y", "1.2": "NI", "1.3": "N"}, "some_concerns"),
    ("Table 4", "D1", {"1.1": "Y", "1.2": "NI", "1.3": "PY"}, "high"),
    ("Table 4", "D1", {"1.1": "Y", "1.2": "N", "1.3": "N"}, "high"),
    ("Table 6", "D2", {"2.1": "N", "2.2": "PN", "2.6": "Y"}, "low"),
    ("Table 6", "D2", {"2.1": "Y", "2.2": "N", "2.3": "N", "2.6": "Y"}, "low"),
    ("Table 6", "D2", {"2.1": "Y", "2.2": "N", "2.3": "NI", "2.6": "Y"}, "some_concerns"),
    (
        "Table 6",
        "D2",
        {"2.1": "Y", "2.2": "N", "2.3": "Y", "2.4": "N", "2.6": "Y"},
        "some_concerns",
    ),
    (
        "Table 6",
        "D2",
        {"2.1": "Y", "2.2": "N", "2.3": "Y", "2.4": "Y", "2.5": "Y", "2.6": "Y"},
        "some_concerns",
    ),
    (
        "Table 6",
        "D2",
        {"2.1": "Y", "2.2": "N", "2.3": "Y", "2.4": "NI", "2.5": "N", "2.6": "Y"},
        "high",
    ),
    ("Table 6", "D2", {"2.1": "N", "2.2": "N", "2.6": "N", "2.7": "N"}, "some_concerns"),
    ("Table 6", "D2", {"2.1": "N", "2.2": "N", "2.6": "NI", "2.7": "NI"}, "high"),
    ("Table 10", "D3", {"3.1": "Y"}, "low"),
    ("Table 10", "D3", {"3.1": "N", "3.2": "Y"}, "low"),
    ("Table 10", "D3", {"3.1": "N", "3.2": "N", "3.3": "N"}, "low"),
    ("Table 10", "D3", {"3.1": "NI", "3.2": "PN", "3.3": "NI", "3.4": "N"}, "some_concerns"),
    ("Table 10", "D3", {"3.1": "NI", "3.2": "PN", "3.3": "NI", "3.4": "NI"}, "high"),
    ("Table 12", "D4", {"4.1": "N", "4.2": "N", "4.3": "N"}, "low"),
    ("Table 12", "D4", {"4.1": "NI", "4.2": "N", "4.3": "Y", "4.4": "N"}, "low"),
    (
        "Table 12",
        "D4",
        {"4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "N"},
        "some_concerns",
    ),
    ("Table 12", "D4", {"4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "PY"}, "high"),
    ("Table 12", "D4", {"4.1": "N", "4.2": "NI", "4.3": "N"}, "some_concerns"),
    ("Table 12", "D4", {"4.1": "N", "4.2": "NI", "4.3": "Y", "4.4": "Y", "4.5": "Y"}, "high"),
    ("Table 12", "D4", {"4.1": "Y", "4.2": "N"}, "high"),
    ("Table 12", "D4", {"4.1": "N", "4.2": "PY"}, "high"),
    ("Table 14", "D5", {"5.1": "Y", "5.2": "N", "5.3": "N"}, "low"),
    ("Table 14", "D5", {"5.1": "NI", "5.2": "N", "5.3": "N"}, "some_concerns"),
    ("Table 14", "D5", {"5.1": "Y", "5.2": "N", "5.3": "NI"}, "some_concerns"),
    ("Table 14", "D5", {"5.1": "Y", "5.2": "NI", "5.3": "NI"}, "some_concerns"),
    ("Table 14", "D5", {"5.1": "Y", "5.2": "PY", "5.3": "N"}, "high"),
    ("Table 14", "D5", {"5.1": "N", "5.2": "N", "5.3": "Y"}, "high"),
]

# (name, domain, answers, error kind, question)
REFUSALS: list[tuple[str, str, dict[str, str], str, str]] = [
    ("3.2 does not allow NI", "D3", {"3.1": "N", "3.2": "NI"}, "NotAllowed", "3.2"),
    ("2.3 is applicable", "D2", {"2.1": "Y", "2.2": "N", "2.6": "Y"}, "MissingAnswer", "2.3"),
    ("4.2 is always asked", "D4", {"4.1": "Y"}, "MissingAnswer", "4.2"),
    (
        "9.9 is not in D1",
        "D1",
        {"1.1": "Y", "1.2": "Y", "1.3": "N", "9.9": "Y"},
        "UnknownQuestion",
        "9.9",
    ),
]

# (value, expected wire value or None for a refusal)
PARSES: list[tuple[str, str | None]] = [
    (" py ", "PY"),
    ("n", "N"),
    ("NI", "NI"),
    ("NA", None),
    ("Unclear", None),
    ("", None),
]

OVERALL: list[tuple[list[str], bool | None]] = [
    (["low"] * 5, None),
    (["low", "low", "some_concerns", "low", "low"], None),
    (["low", "some_concerns", "some_concerns", "low", "low"], None),
    (["low", "some_concerns", "some_concerns", "low", "low"], False),
    (["low", "some_concerns", "some_concerns", "low", "low"], True),
    (["low", "some_concerns", "high", "some_concerns", "low"], None),
    (["some_concerns", "some_concerns", "high", "low", "low"], False),
    (["high"] * 5, True),
]


def walks(domain: Domain) -> list[dict[str, str]]:
    """Every complete answer set over Y / N / NI along the applicable branch."""
    done: list[dict[str, str]] = []
    stack: list[dict[str, str]] = [{}]
    while stack:
        answers = stack.pop()
        parsed = {q: Answer(a) for q, a in answers.items()}
        pending = [q for q in applicable_questions(domain, parsed) if q not in answers]
        if not pending:
            done.append(answers)
            continue
        for answer in ("NI", "N", "Y"):
            if not (answer == "NI" and pending[0] == "3.2"):
                stack.append({**answers, pending[0]: answer})
    return sorted(done, key=lambda a: json.dumps(a))


def judge(domain: str, answers: dict[str, str]) -> str:
    """Python's judgement of the answers, as its wire value."""
    return domain_judgement(Domain(domain), {q: Answer(a) for q, a in answers.items()}).value


def build_contract() -> dict[str, Any]:
    """The whole contract, as the JSON-ready dict the fixture holds."""
    contract: dict[str, Any] = {"algorithm_version": ROB2_ALGORITHM_VERSION}
    contract["official"] = [
        {"source": s, "domain": d, "answers": a, "expected": e} for s, d, a, e in OFFICIAL
    ]
    contract["exhaustive"] = [
        {"domain": d.value, "answers": a, "expected": judge(d.value, a)}
        for d in Domain
        for a in walks(d)
    ]
    contract["applicable"] = [
        {
            "domain": "D2",
            "answers": a,
            "expected": applicable_questions(Domain.D2, {q: Answer(v) for q, v in a.items()}),
        }
        for a in ({"2.1": "N", "2.2": "N"}, {"2.1": "N", "2.2": "N", "2.6": "NI"})
    ]
    contract["parse"] = [{"value": v, "expected": e} for v, e in PARSES]
    contract["refusals"] = []
    for name, domain, answers, kind, question in REFUSALS:
        try:
            judge(domain, answers)
        except Rob2Error as error:
            assert (error.kind, error.question) == (kind, question), name
        else:
            raise AssertionError(f"{name} was accepted")
        contract["refusals"].append(
            {"name": name, "domain": domain, "answers": answers, "kind": kind, "question": question}
        )
    contract["overall"] = []
    for domains, escalate in OVERALL:
        judgements = [Judgement(d) for d in domains]
        case: dict[str, Any] = {
            "domains": domains,
            "escalate": escalate,
            "needs_escalation_decision": needs_escalation_decision(judgements),
        }
        try:
            case["expected"] = overall_judgement(judgements, escalate).value
        except Rob2Error as error:
            case["error"] = error.kind
        contract["overall"].append(case)
    return contract


def main() -> None:
    """Write the contract to the path given, or to the committed fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    text = json.dumps(build_contract(), ensure_ascii=False, indent=1) + "\n"
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
