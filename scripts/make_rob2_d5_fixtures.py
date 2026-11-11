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

"""Write ``tests/fixtures/rob2_d5_checks.json``, the D5 quote checks' shared contract.

Built from the Python implementation; ``tests/test_rob2_d5_contract.py`` and
``rust/bmlib/tests/rob2_d5_contract.rs`` assert every case, and the Python
test checks that this script still builds the committed file, so add a case
here and re-run it:

    python scripts/make_rob2_d5_fixtures.py [output-directory]

The cases are the D5 checks' own, the quotes the RoB 2 wording smoke test
(StudyAnalyzer ``docs/smokescreens``) found a model backing a fact with
wrongly, and non-ASCII text, on which ``re`` and the ``regex`` crate must
agree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bmlib.quality.rob2_d5_checks import (
    D5Fact,
    fact_verdict,
    names_outcome,
    shows_result,
    states_a_plan,
    supports,
)

DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CHECKS_NAME = "rob2_d5_checks.json"

#: (fact, quote, outcome) for `supports`.
SUPPORTS: list[tuple[D5Fact, str, str]] = [
    (
        D5Fact.PLAN_DATED_EARLY,
        "This study is registered with ClinicalTrials.gov, NCT04546581.",
        "x",
    ),
    (D5Fact.PLAN_DATED_EARLY, "ClinicalTrials.gov number, NCT04960202.", "x"),
    (
        D5Fact.PLAN_DATED_EARLY,
        (
            "The primary analysis was planned after approximately 24 "
            "primary end-point events had been confirmed"
        ),
        "x",
    ),
    (
        D5Fact.PLAN_DATED_EARLY,
        (
            "On the basis of a group sequential design utilizing a Lan–DeMets "
            "alpha-spending function with an O’Brien–Fleming boundary"
        ),
        "x",
    ),
    (D5Fact.PLAN_DATED_EARLY, "registered (identifier 113021; November 4, 2012)", "x"),
    (D5Fact.PLAN_DATED_EARLY, "The trial was prospectively registered (ISRCTN123).", "x"),
    (D5Fact.PLAN_DATED_EARLY, "registered on 03/04/2015", "x"),
    (D5Fact.PLAN_DATED_EARLY, "The analysis plan was finalised before unblinding.", "x"),
    (D5Fact.PLAN_DATED_EARLY, "approved (8883-12, 20121212)", "x"),
    (D5Fact.PLAN_DATED_EARLY, "enregistré le 3 mars 2015 – avant l’inclusion", "x"),
    (D5Fact.PLAN_DATED_EARLY, "registered prospectively – before enrolment", "x"),
    (
        D5Fact.SWITCHED,
        (
            "There was one death ‏in the control group ‏and none in the case "
            "group; this difference was insignificant (P = 0.495)."
        ),
        "x",
    ),
    (D5Fact.SWITCHED, "The reported outcome differs from the registered one.", "x"),
    (D5Fact.SWITCHED, "The primary outcome was changed from pain to function after review.", "x"),
    (D5Fact.SWITCHED, "Originally registered as a secondary outcome", "x"),
    (D5Fact.SWITCHED, "The χ2 test was used instead of the planned ANCOVA.", "x"),
    (
        D5Fact.MEASURE_FIXED,
        (
            "The primary endpoint was an ordinal outcome based on "
            "the patient's clinical status on day 7"
        ),
        "deaths up to day 28",
    ),
    (
        D5Fact.MEASURE_FIXED,
        (
            "The composite safety outcome at day 28 included all of the "
            "outcomes used in the day 7 safety outcome except gr"
        ),
        "deaths up to day 28",
    ),
    (
        D5Fact.MEASURE_FIXED,
        (
            "Children underwent a 28–45 min in person assessment (including "
            "the 24-h dietary recall and placement of an acc"
        ),
        "daily minutes of moderate-to-vigorous physical activity",
    ),
    (
        D5Fact.MEASURE_FIXED,
        "Secondary outcomes included death from any cause by day 28.",
        "deaths up to day 28",
    ),
    (D5Fact.MEASURE_FIXED, "Échelle de qualité de vie (EQ-5D) ≤ 3", "qualité de vie"),
    (
        D5Fact.TIMEPOINT_FIXED,
        "These scales were completed at baseline and at the end of the program.",
        "x",
    ),
    (D5Fact.TIMEPOINT_FIXED, "The primary outcome was quality of life.", "x"),
    (D5Fact.TIMEPOINT_FIXED, "à l’inclusion et à six mois", "x"),
    (D5Fact.PLAN_CITED, "anything", "x"),
    (D5Fact.ANALYSIS_FIXED, "anything", "x"),
]

#: (quote, outcome) for `names_outcome`.
NAMES_OUTCOME: list[tuple[str, str]] = [
    (
        "all-cause mortality at the end of follow-up was recorded",
        "all-cause mortality at the end of follow-up",
    ),
    ("anything", "score at 6 months"),
    ("The KDIGO stage was recorded", "Kidney injury (KDIGO)"),
    (
        (
            "The primary endpoint was an ordinal outcome based on "
            "the patient's clinical status on day 7"
        ),
        "deaths up to day 28",
    ),
]

#: Quote lists for `states_a_plan`.
STATES_A_PLAN: list[list[str]] = [
    ["Statistical analysis was performed by the IBM SPSS Statistics for Windows version 17.0."],
    ["Assessments were performed at baseline, 7- and 12-weeks follow-up."],
    ["The secondary efficacy endpoints as prespecified in the statistical analysis plan were:"],
    ["A Poisson regression with robust variance was used as the primary efficacy analysis model."],
    ["The primary analysis compared proportions of patients in the two groups."],
    ["Le critère principal était pré-spécifié."],
    ["Efficacy outcomes included the following self-report scales"],
]

#: Passage lists for `shows_result`.
SHOWS_RESULT: list[list[str]] = [
    ["p = 0.04"],
    ["Pain at twelve weeks is shown in Figure 2."],
    ["(Table S4)"],
    ["see the Supplementary Appendix"],
    ["Function improved in both groups."],
    [],
    ["fell by ٣ points"],
    ["p ≤ 0·05"],
]

#: An outcome, and its measure named apart from the sentence that says it was prespecified.
IOP = "change of mean diurnal intraocular pressure (IOP)"
IOP_PLANNED = "The secondary endpoints as prespecified were:"
IOP_MEASURE = "Mean change in mean diurnal IOP at day 8"
#: Methods text that describes what was done, not a plan.
SPSS = "Statistical analysis was performed by the IBM SPSS Statistics."

#: (fact, claimed, found, outcome) for `fact_verdict`.
FACT_VERDICT: list[tuple[D5Fact, bool, list[str], str]] = [
    (D5Fact.PLAN_DATED_EARLY, True, ["registered with ClinicalTrials.gov, NCT04546581."], "x"),
    (D5Fact.MEASURE_FIXED, True, ["Pain was rated on a VAS."], "pain"),
    (D5Fact.SWITCHED, True, [], "x"),
    (D5Fact.PLAN_CITED, False, ["Registered as NCT01."], "x"),
    (D5Fact.PLAN_CITED, True, ["Registered as NCT01."], "x"),
    (D5Fact.MEASURE_FIXED, True, [IOP_PLANNED, IOP_MEASURE], IOP),
    (D5Fact.MEASURE_FIXED, True, [IOP_MEASURE], IOP),
    (D5Fact.ANALYSIS_FIXED, True, [SPSS], "x"),
    (D5Fact.TIMEPOINT_FIXED, True, ["The primary outcome was measured at 12 weeks."], "x"),
]  # fmt: skip


def build_checks() -> dict[str, Any]:
    """Every check case with the verdict the Python implementation gives."""
    return {
        "supports": [
            {"fact": f.value, "quote": q, "outcome": o, "expected": supports(f, q, o)}
            for f, q, o in SUPPORTS
        ],
        "names_outcome": [
            {"quote": q, "outcome": o, "expected": names_outcome(q, o)} for q, o in NAMES_OUTCOME
        ],
        "states_a_plan": [{"quotes": q, "expected": states_a_plan(q)} for q in STATES_A_PLAN],
        "shows_result": [{"passages": p, "expected": shows_result(p)} for p in SHOWS_RESULT],
        "fact_verdict": [
            {
                "fact": f.value,
                "claimed": c,
                "found": found,
                "outcome": o,
                "expected": fact_verdict(f, c, found, o).value,
            }
            for f, c, found, o in FACT_VERDICT
        ],
    }


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    """Write the contract to the directory given, or to the committed fixtures."""
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIRECTORY
    (directory / CHECKS_NAME).write_text(_dump(build_checks()), encoding="utf-8")


if __name__ == "__main__":
    main()
