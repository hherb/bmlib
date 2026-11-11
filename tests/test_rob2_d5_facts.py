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

"""The D5 facts reply, its reading and the rules that answer 5.1-5.3 from it."""

from __future__ import annotations

from typing import Any

import pytest

from bmlib.quality._full_text_agent import UnreadableReplyError
from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.rob2_agent import d5_assessment
from bmlib.quality.rob2_algorithm import Answer, Domain, Judgement
from bmlib.quality.rob2_d5_checks import D5_FACTS, D5Fact, FactVerdict
from bmlib.quality.rob2_d5_facts import (
    D5_FACTS_SYSTEM_PROMPT,
    D5_SECOND_LOOK_SYSTEM_PROMPT,
    D5Facts,
    d5_answers,
    facts_prompt,
    read_facts_reply,
    read_second_look,
    second_look_prompt,
)

PAPER = (
    "The trial was registered at ClinicalTrials.gov (NCT01234567) on 3 March 2015, before "
    "recruitment began. The prespecified primary outcome was pain on a 0-10 visual analogue "
    "scale at 6 and 12 weeks. Pain was analysed with a linear mixed model adjusted for baseline "
    "pain, as prespecified in the statistical analysis plan. Function was measured with the "
    "Oswestry index at 12 weeks. Results: at 6 weeks mean pain was 3.1 versus 4.2 (p=0.01). "
    "Function improved in both groups. Pain at twelve weeks is shown in Figure 2. The repeated "
    "measures ANOVA named in the protocol was replaced; P values come from a two-sample t test."
)
OUTCOME = "pain at 6 weeks"
SIM = 0.99

CITED = ["registered at ClinicalTrials.gov (NCT01234567)"]
DATED = ["on 3 March 2015, before recruitment began"]
MEASURE = ["The prespecified primary outcome was pain on a 0-10 visual analogue scale"]
TIMEPOINT = [
    "The prespecified primary outcome was pain on a 0-10 visual analogue scale at 6 and 12 weeks"
]
ANALYSIS = [
    "Pain was analysed with a linear mixed model adjusted for baseline pain, as prespecified"
]
FIXED = {"measure_fixed": MEASURE, "timepoint_fixed": TIMEPOINT, "analysis_fixed": ANALYSIS}


def reply(
    measurements: list[dict[str, Any]] | None = None,
    analysis: dict[str, Any] | None = None,
    **claims: list[str],
) -> dict[str, Any]:
    """A facts reply claiming each named fact with its quotes, every other fact absent."""
    return {
        "facts": {
            f.value: {"present": f.value in claims, "quotes": claims.get(f.value, [])}
            for f in D5_FACTS
        },
        "other_measurements": measurements or [],
        "main_analysis": analysis or {"differs": False, "planned": [], "reported": []},
    }


def read(data: dict[str, Any]) -> D5Facts:
    return read_facts_reply(data, PAPER, OUTCOME, SIM)


def answers(facts: D5Facts) -> dict[str, Answer]:
    return {q: a for q, (a, _deciding) in d5_answers(facts).items()}


def test_a_cited_plan_dated_before_the_data_that_fixes_everything_is_low():
    facts = read(reply(plan_cited=CITED, plan_dated_early=DATED, **FIXED))
    assert answers(facts) == {"5.1": Answer.Y, "5.2": Answer.N, "5.3": Answer.N}
    assessment = d5_assessment(facts)
    assert assessment.domain is Domain.D5 and assessment.judgement is Judgement.LOW
    assert assessment.facts == facts
    assert assessment.answers["5.2"].citations, (
        "the deciding facts' quotes are the answer's citations"
    )


def test_the_same_content_on_the_papers_word_is_one_rung_up():
    assert answers(read(reply(**FIXED))) == {"5.1": Answer.PY, "5.2": Answer.PN, "5.3": Answer.PN}


def test_a_claimed_fact_its_quote_cannot_show_is_absent_but_kept():
    # A registration number does not date the plan (Review Focus 2).
    facts = read(reply(plan_cited=CITED, plan_dated_early=CITED, **FIXED))
    report = facts.facts[D5Fact.PLAN_DATED_EARLY]
    assert report.claimed and report.citations
    assert report.verdict is FactVerdict.QUOTE_DOES_NOT_SUPPORT
    assert answers(facts) == {"5.1": Answer.PY, "5.2": Answer.PN, "5.3": Answer.PN}
    stored = facts.to_dict()["facts"]["plan_dated_early"]
    assert stored["claimed"] is True and stored["verdict"] == "quote_does_not_support"


def test_a_late_plan_or_anything_less_is_ni():
    late = read(reply(plan_cited=CITED, plan_dated_early=DATED, plan_late=DATED, **FIXED))
    assert answers(late) == {"5.1": Answer.NI, "5.2": Answer.NI, "5.3": Answer.NI}
    assert answers(read(reply())) == {"5.1": Answer.NI, "5.2": Answer.NI, "5.3": Answer.NI}


def test_a_switch_gives_y_unless_a_reason_independent_of_the_results_is_given():
    switched = ["The repeated measures ANOVA named in the protocol was replaced"]
    facts = read(reply(switched=switched, **FIXED))
    assert answers(facts)["5.1"] is Answer.N and answers(facts)["5.2"] is Answer.Y
    explained = read(reply(switched=switched, change_explained=DATED, **FIXED))
    assert answers(explained) == {"5.1": Answer.PY, "5.2": Answer.PN, "5.3": Answer.PN}


def item(label: str, named: list[str], result: list[str] | None = None) -> dict[str, Any]:
    return {"label": label, "named": named, "result": result or []}


def test_a_measurement_is_missing_only_when_both_looks_find_no_result():
    measured = [
        item("pain at 6 weeks", ["at 6 and 12 weeks"], ["at 6 weeks mean pain was 3.1 versus 4.2"]),
        item(
            "pain at 12 weeks",
            ["at 6 and 12 weeks"],
            ["Pain at twelve weeks is shown in Figure 2."],
        ),
        item("Oswestry", ["the Oswestry index at 12 weeks"], ["Function improved in both groups."]),
        item("pain at 24 weeks", ["at 24 weeks"]),
    ]
    facts = read(reply(measured, **FIXED))
    six, twelve, oswestry, unnamed = facts.measurements
    assert six.reported() and twelve.reported(), "a number, or a pointer to a figure"
    assert not oswestry.reported() and oswestry.needs_second_look() and not oswestry.missing()
    assert not unnamed.counts() and not unnamed.needs_second_look()
    assert answers(facts)["5.2"] is Answer.PN, "no second look made yet: nothing is missing"
    oswestry.second_look = []
    assert oswestry.missing()
    assert answers(facts)["5.2"] is Answer.PY
    assert d5_assessment(facts).judgement is Judgement.HIGH


def test_5_3_is_py_only_when_the_reported_analysis_differs_from_the_main_one_both_quoted():
    differs = {
        "differs": True,
        "planned": ["Pain was analysed with a linear mixed model"],
        "reported": ["P values come from a two-sample t test"],
    }
    facts = read(reply(analysis=differs, **FIXED))
    assert facts.analysis.counts()
    assert answers(facts)["5.3"] is Answer.PY
    differs["planned"] = ["The model was adjusted for age and sex."]
    assert not read(reply(analysis=differs, **FIXED)).analysis.counts()


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda r: r["facts"]["plan_cited"].update(present="yes"), "facts.plan_cited.present"),
        (lambda r: r["facts"].pop("switched"), "facts.switched"),
        (lambda r: r.pop("facts"), "facts"),
        (lambda r: r.pop("main_analysis"), "main_analysis"),
        (lambda r: r["main_analysis"].update(differs="no"), "main_analysis.differs"),
        (lambda r: r.update(other_measurements={}), "other_measurements"),
        (lambda r: r["other_measurements"].append({"named": []}), "other_measurements[0].label"),
    ],
)
def test_an_unreadable_reply_names_the_field(change: Any, field: str):
    data = reply()
    change(data)
    with pytest.raises(UnreadableReplyError, match=field.replace("[", r"\[").replace("]", r"\]")):
        read(data)


def test_the_second_look():
    assert read_second_look({"found": False, "quotes": []}, PAPER, SIM) == []
    found = read_second_look(
        {"found": True, "quotes": ["Function improved in both groups."]}, PAPER, SIM
    )
    assert [c.text for c in found] == ["Function improved in both groups."]
    with pytest.raises(UnreadableReplyError, match="found"):
        read_second_look({"found": "yes"}, PAPER, SIM)


def test_facts_round_trip_and_a_stored_verdict_is_checked():
    facts = read(
        reply(
            [item("Oswestry", ["the Oswestry index at 12 weeks"])],
            plan_cited=CITED,
            plan_dated_early=CITED,
            **FIXED,
        )
    )
    facts.measurements[0].second_look_error = "HTTP 503"
    assert D5Facts.from_dict(facts.to_dict()) == facts
    tampered = facts.to_dict()
    tampered["facts"]["plan_dated_early"]["verdict"] = "present"
    with pytest.raises(ValueError, match="plan_dated_early"):
        D5Facts.from_dict(tampered)


def test_the_prompts_carry_the_outcome_the_facts_and_the_paper():
    outcome = ProposedOutcome(name=OUTCOME, timepoint="6 weeks")
    prompt = facts_prompt("A trial", PAPER, outcome)
    assert "Outcome assessed: pain at 6 weeks" in prompt and "Timepoint: 6 weeks" in prompt
    for f in D5_FACTS:
        assert f'"{f.value}"' in prompt
    assert '"other_measurements"' in prompt and '"main_analysis"' in prompt
    assert prompt.endswith(f"Paper Title: A trial\n\nPaper Text:\n{PAPER}")
    facts = read(reply([item("Oswestry", ["the Oswestry index at 12 weeks"])]))
    look = second_look_prompt("A trial", PAPER, outcome, facts.measurements[0])
    assert '"Oswestry"' in look and "the Oswestry index at 12 weeks" in look
    assert "VERBATIM" in D5_FACTS_SYSTEM_PROMPT and "figure" in D5_SECOND_LOOK_SYSTEM_PROMPT
