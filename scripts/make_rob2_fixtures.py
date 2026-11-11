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

"""Write ``tests/fixtures/rob2_replies.json`` and ``tests/fixtures/rob2_run.json``.

The shared RoB 2 contracts Python and Rust both assert against, built from the
Python implementation:

- ``rob2_replies.json``: the wording (every question, the condensation
  queries, the prompts and a rendering of each), and for each domain reply
  either the assessment read from it or the message it is refused with; the
  same for escalation replies.
- ``rob2_run.json``: a whole run's dict, which both languages read and write
  back unchanged.

    python scripts/make_rob2_fixtures.py [output-directory]

``tests/test_rob2_contract.py`` checks that this script still builds the
committed fixtures, so add a case here and re-run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bmlib.quality.evidence_ladder import EffectScale, ReportedResult
from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.rob2_agent import (
    ROB2_DOMAIN_PROMPT,
    ROB2_SYSTEM_PROMPT,
    DomainAssessment,
    DomainFailed,
    Escalation,
    OutcomeRob2,
    Rob2Run,
    d5_assessment,
    domain_prompt,
    escalation_message,
    read_domain_reply,
    read_escalation_reply,
    rob2_overall,
)
from bmlib.quality.rob2_algorithm import ROB2_ALGORITHM_VERSION, Domain
from bmlib.quality.rob2_d5_checks import D5_FACTS, D5Fact
from bmlib.quality.rob2_d5_facts import read_facts_reply
from bmlib.quality.rob2_questions import (
    D5_OUTCOME_SWITCHING,
    ESCALATION_PROMPT,
    QUESTION_TEXTS,
    ROB2_CONDENSE_QUERIES,
    ROB2_PROMPT_VERSION,
    domain_title,
)

DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "tests/fixtures"
REPLIES_NAME = "rob2_replies.json"
RUN_NAME = "rob2_run.json"

RANDOM = "Participants were randomly assigned using a computer-generated list."
CENTRAL = "Allocation was concealed by a central web-based service."
COMPLETE = "Outcome data were available for 98% of participants."
TITLE = "A trial of a blood pressure drug"
TEXT = (
    f"{RANDOM} {CENTRAL} Baseline characteristics were similar between groups. "
    "Participants and clinicians were blinded with an identical placebo. "
    f"{COMPLETE} "
    "The analysis followed the registered protocol, NCT00000000, at 12 weeks."
)
MIN_SIMILARITY = 0.9

OUTCOME = ProposedOutcome(
    "Systolic blood pressure",
    "12 weeks",
    ReportedResult("mean difference", EffectScale.DIFFERENCE, -4.2, -6.1, -2.3, 0.95, 412),
)
SECOND = ProposedOutcome("Diastolic blood pressure", None, None)


def _entry(answer: Any, *quotes: Any, reasoning: Any = "Because.") -> dict[str, Any]:
    return {"answer": answer, "quotes": list(quotes), "reasoning": reasoning}


_LOW: dict[Domain, dict[str, Any]] = {
    Domain.D1: {"1.1": _entry("Y", RANDOM), "1.2": _entry("Y", CENTRAL), "1.3": _entry("N")},
    Domain.D2: {
        "2.1": _entry("N"),
        "2.2": _entry("N"),
        "2.3": _entry("NA"),
        "2.6": _entry("Y"),
        "2.7": _entry("NA"),
    },
    Domain.D3: {"3.1": _entry("Y", COMPLETE), "3.2": _entry("na")},
    Domain.D4: {"4.1": _entry("N"), "4.2": _entry("N"), "4.3": _entry("N")},
    Domain.D5: {"5.1": _entry("Y"), "5.2": _entry("N"), "5.3": _entry("N")},
}


def _reply(domain: Domain, **changes: Any) -> dict[str, Any]:
    """The low reply for *domain*; ``q2_3=...`` replaces ``"2.3"``, ``None`` removes it."""
    answers = dict(_LOW[domain])
    for key, value in changes.items():
        question = key[1:].replace("_", ".")
        if value is None:
            answers.pop(question, None)
        else:
            answers[question] = value
    return {"answers": answers}


_QUOTES = (
    "Participants were followed for a year.",
    "Outcome data were available for 90% of participants.",
    COMPLETE,
    "  ",
    7,
)

# (name, domain, reply); the expected side is whatever the Python reader makes of it.
CASES: list[tuple[str, Domain, Any]] = [
    ("D1 complete, low", Domain.D1, _reply(Domain.D1)),
    ("D1 NI without quotes is kept", Domain.D1, _reply(Domain.D1, q1_2=_entry("NI"))),
    ("D1 high", Domain.D1, _reply(Domain.D1, q1_2=_entry("N"))),
    ("answer case and space tolerated", Domain.D1, _reply(Domain.D1, q1_1=_entry(" py "))),
    ("reasoning absent", Domain.D1, _reply(Domain.D1, q1_3={"answer": "N"})),
    ("quotes a bare string", Domain.D1, _reply(Domain.D1, q1_1={"answer": "Y", "quotes": RANDOM})),
    ("D2 low, NA where it does not apply", Domain.D2, _reply(Domain.D2)),
    (
        "D2 answer to an unasked question is ignored",
        Domain.D2,
        _reply(Domain.D2, q2_7=_entry("Y")),
    ),
    (
        "D2 deviations unbalanced, high",
        Domain.D2,
        _reply(
            Domain.D2,
            q2_1=_entry("Y"),
            q2_2=_entry("PY"),
            q2_3=_entry("Y"),
            q2_4=_entry("Y"),
            q2_5=_entry("N"),
        ),
    ),
    ("D2 NA for an applicable question", Domain.D2, _reply(Domain.D2, q2_1=_entry("Y"))),
    (
        "D2 N/A and Not applicable read as NA",
        Domain.D2,
        _reply(Domain.D2, q2_3=_entry("n/a"), q2_7=_entry(" Not  Applicable ")),
    ),
    (
        "D2 Not applicable for an applicable question",
        Domain.D2,
        _reply(Domain.D2, q2_6=_entry("Not applicable")),
    ),
    (
        "D2 unreadable answer to an unasked question is ignored",
        Domain.D2,
        _reply(Domain.D2, q2_7=_entry("Unclear")),
    ),
    (
        "D2 malformed entry for an unasked question is ignored",
        Domain.D2,
        _reply(Domain.D2, q2_4="Y", q2_5=_entry(None)),
    ),
    (
        "D2 unreadable answer to an asked question",
        Domain.D2,
        _reply(Domain.D2, q2_6=_entry("N"), q2_7=_entry("Unclear")),
    ),
    ("D2 applicable question omitted", Domain.D2, _reply(Domain.D2, q2_6=None)),
    ("D2 unknown id", Domain.D2, _reply(Domain.D2, q2_9=_entry("Y"))),
    ("D3 low", Domain.D3, _reply(Domain.D3)),
    (
        "D3 missingness likely depends on the outcome, high",
        Domain.D3,
        _reply(
            Domain.D3,
            q3_1=_entry("N"),
            q3_2=_entry("N"),
            q3_3=_entry("PY"),
            q3_4=_entry("Y"),
        ),
    ),
    (
        "D3 amount missing unreported, 3.3 still answered",
        Domain.D3,
        _reply(
            Domain.D3,
            q3_1=_entry("NI"),
            q3_2=_entry("N"),
            q3_3=_entry("PY"),
            q3_4=_entry("PN"),
        ),
    ),
    (
        "D3 NI where none is allowed",
        Domain.D3,
        _reply(Domain.D3, q3_1=_entry("N"), q3_2=_entry("NI")),
    ),
    (
        "D3 quotes lost, contradicted and unusable",
        Domain.D3,
        _reply(Domain.D3, q3_1=_entry("Y", *_QUOTES)),
    ),
    ("D4 low", Domain.D4, _reply(Domain.D4)),
    (
        "D4 assessors aware, some concerns",
        Domain.D4,
        _reply(Domain.D4, q4_3=_entry("Y"), q4_4=_entry("PY"), q4_5=_entry("PN")),
    ),
    ("D5 some concerns", Domain.D5, _reply(Domain.D5, q5_1=_entry("NI"))),
    ("answer outside the set", Domain.D1, _reply(Domain.D1, q1_1=_entry("Unclear"))),
    ("answer not text", Domain.D1, _reply(Domain.D1, q1_1=_entry(1))),
    ("entry not an object", Domain.D1, _reply(Domain.D1, q1_1="Y")),
    ("reasoning not text", Domain.D1, _reply(Domain.D1, q1_1=_entry("Y", reasoning=3))),
    ("answers not an object", Domain.D1, {"answers": ["Y"]}),
    ("answers missing", Domain.D1, {}),
    ("reply not an object", Domain.D1, ["Y"]),
]

ESCALATION_CASES: list[tuple[str, Any]] = [
    ("escalate", {"escalate": True, "reasoning": "Several concerns compound."}),
    ("stay, reasoning absent", {"escalate": False}),
    ("escalate as text", {"escalate": "true"}),
    ("escalate as a number", {"escalate": 1}),
    ("escalate missing", {"reasoning": "x"}),
    ("reasoning not text", {"escalate": True, "reasoning": 2}),
    ("reply not an object", [True]),
]


def _read(domain: Domain, reply: Any) -> DomainAssessment:
    return read_domain_reply(domain, reply, TEXT, MIN_SIMILARITY)


#: A D5 facts reply citing the registered protocol and stating nothing else: 5.1-5.3 NI, so D5
#: has some concerns (design §4.6).
D5_CITED_ONLY: dict[str, Any] = {
    "facts": {
        f.value: {
            "present": f is D5Fact.PLAN_CITED,
            "quotes": ["registered protocol, NCT00000000"] if f is D5Fact.PLAN_CITED else [],
        }
        for f in D5_FACTS
    },
    "other_measurements": [],
    "main_analysis": {"differs": False, "planned": [], "reported": []},
}


def _concerns() -> tuple[DomainAssessment, list[DomainAssessment]]:
    """A study and outcome domains with some concerns in D1 and D5 (escalation needed)."""
    study = _read(Domain.D1, _reply(Domain.D1, q1_2=_entry("NI", reasoning="Not described.")))
    domains = [_read(d, _reply(d)) for d in (Domain.D2, Domain.D3, Domain.D4)]
    domains.append(
        d5_assessment(read_facts_reply(D5_CITED_ONLY, TEXT, OUTCOME.name, MIN_SIMILARITY))
    )
    return study, domains


def build_replies() -> dict[str, Any]:
    """Build the reply contract from the Python implementation."""
    cases = []
    for name, domain, reply in CASES:
        case: dict[str, Any] = {"name": name, "domain": domain.value, "reply": reply}
        try:
            case["expected"] = _read(domain, reply).to_dict()
        except ValueError as exc:
            case["error"] = str(exc)
        cases.append(case)
    escalations = []
    for name, reply in ESCALATION_CASES:
        case = {"name": name, "reply": reply}
        try:
            case["expected"] = read_escalation_reply(reply).to_dict()
        except ValueError as exc:
            case["error"] = str(exc)
        escalations.append(case)
    study, domains = _concerns()
    return {
        "prompt_version": ROB2_PROMPT_VERSION,
        "system_prompt": ROB2_SYSTEM_PROMPT,
        "domain_prompt": ROB2_DOMAIN_PROMPT,
        "d5_outcome_switching": D5_OUTCOME_SWITCHING,
        "escalation_prompt": ESCALATION_PROMPT,
        "question_texts": dict(QUESTION_TEXTS),
        "domain_titles": {d.value: domain_title(d) for d in Domain},
        "condense_queries": [[d.value, query] for d, query in ROB2_CONDENSE_QUERIES],
        "title": TITLE,
        "text": TEXT,
        "min_similarity": MIN_SIMILARITY,
        "outcome": OUTCOME.to_dict(),
        "outcome_without_result": SECOND.to_dict(),
        "rendered": {
            "D1": domain_prompt(Domain.D1, TITLE, TEXT, None),
            "D2 without a result": domain_prompt(Domain.D2, TITLE, TEXT, SECOND),
            "D5": domain_prompt(Domain.D5, TITLE, TEXT, OUTCOME),
            "escalation_study": study.to_dict(),
            "escalation_domains": [d.to_dict() for d in domains],
            "escalation": escalation_message(OUTCOME, study, domains),
        },
        "cases": cases,
        "escalation_cases": escalations,
    }


def build_run() -> dict[str, Any]:
    """A whole run: one outcome escalated, one with a failed domain; condensed in places."""
    study, domains = _concerns()
    study.condensed = True
    escalation = Escalation(True, "Concerns about concealment and the plan compound.")
    first = OutcomeRob2(
        OUTCOME.name, list(domains), escalation, rob2_overall(study, domains, escalation)
    )
    second_domains: list[DomainAssessment | DomainFailed] = [
        _read(Domain.D2, _reply(Domain.D2, q2_7=_entry("Y"))),
        DomainFailed(Domain.D3, "connection refused", True, None),
        DomainFailed(Domain.D4, "HTTP 401: invalid x-api-key", False, 401),
        DomainFailed(Domain.D5, "HTTP 503: overloaded", True, 503),
    ]
    second = OutcomeRob2(
        SECOND.name, second_domains, None, rob2_overall(study, second_domains, None)
    )
    third = OutcomeRob2(
        "Heart rate", list(domains), None, None, "RoB 2 escalation: HTTP 503", True, 503
    )
    run = Rob2Run(ROB2_ALGORITHM_VERSION, ROB2_PROMPT_VERSION, study, [first, second, third])
    return run.to_dict()


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    """Write both contracts to the directory given, or to the committed fixtures."""
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIRECTORY
    (directory / REPLIES_NAME).write_text(_dump(build_replies()), encoding="utf-8")
    (directory / RUN_NAME).write_text(_dump(build_run()), encoding="utf-8")


if __name__ == "__main__":
    main()
