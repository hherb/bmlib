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

"""The D5 quote checks: whether a found quote can support its fact."""

from __future__ import annotations

from bmlib.quality.rob2_d5_checks import (
    D5_FACTS,
    D5Fact,
    FactVerdict,
    fact_verdict,
    names_outcome,
    shows_result,
    states_a_plan,
    supports,
)


def test_the_facts_in_order():
    assert [f.value for f in D5_FACTS] == [
        "plan_cited",
        "plan_dated_early",
        "plan_late",
        "measure_fixed",
        "timepoint_fixed",
        "analysis_fixed",
        "switched",
        "change_explained",
    ]


def test_a_plan_is_dated_by_a_date_or_a_before_phrase_not_by_a_registration_number():
    dated = D5Fact.PLAN_DATED_EARLY
    assert not supports(
        dated, "This study is registered with ClinicalTrials.gov, NCT04546581.", "x"
    )
    assert not supports(
        dated,
        "The primary analysis was planned after approximately 24 primary end-point events",
        "x",
    )
    assert supports(dated, "registered (identifier 113021; November 4, 2012)", "x")
    assert supports(dated, "The trial was prospectively registered (ISRCTN123).", "x")
    assert supports(dated, "registered on 03/04/2015", "x")
    assert supports(dated, "The analysis plan was finalised before unblinding.", "x")
    assert supports(dated, "approved (8883-12, 20121212)", "x")


def test_a_switch_needs_the_language_of_change():
    switched = D5Fact.SWITCHED
    assert not supports(
        switched, "There was one death in the control group and none in the case group", "x"
    )
    assert not supports(
        switched,
        "one death in the control group; this difference was insignificant (P = 0.495).",
        "x",
    )
    assert supports(switched, "The reported outcome differs from the registered one.", "x")
    assert supports(
        switched, "The primary outcome was changed from pain to function after review.", "x"
    )
    assert supports(switched, "Originally registered as a secondary outcome", "x")


def test_a_measure_must_name_the_outcome_assessed():
    primary = (
        "The primary endpoint was an ordinal outcome based on "
        "the patient's clinical status on day 7"
    )
    assert not names_outcome(primary, "deaths up to day 28")
    assert names_outcome(
        "Secondary outcomes included death from any cause by day 28.", "deaths up to day 28"
    )
    assert names_outcome(
        "all-cause mortality at the end of follow-up was recorded",
        "all-cause mortality at the end of follow-up",
    )
    # A name with no distinctive word cannot be checked, and passes.
    assert names_outcome("anything", "score at 6 months")
    assert not supports(D5Fact.MEASURE_FIXED, primary, "deaths up to day 28")


def test_a_time_point_needs_a_time():
    assert supports(
        D5Fact.TIMEPOINT_FIXED,
        "These scales were completed at baseline and at the end of the program.",
        "x",
    )
    assert not supports(D5Fact.TIMEPOINT_FIXED, "The primary outcome was quality of life.", "x")


def test_facts_without_a_check_pass():
    for fact in (
        D5Fact.PLAN_CITED,
        D5Fact.PLAN_LATE,
        D5Fact.ANALYSIS_FIXED,
        D5Fact.CHANGE_EXPLAINED,
    ):
        assert supports(fact, "anything", "x")


def test_the_planning_check_wants_a_plan_not_a_description():
    assert not states_a_plan(["Statistical analysis was performed by the IBM SPSS Statistics."])
    assert not states_a_plan(["Assessments were performed at baseline, 7- and 12-weeks follow-up."])
    assert states_a_plan(
        ["The secondary efficacy endpoints as prespecified in the statistical analysis plan were:"]
    )
    assert states_a_plan(["A Poisson regression was used as the primary efficacy analysis model."])
    assert states_a_plan(["The primary analysis compared proportions of patients."])


def test_a_verdict_says_why_a_claimed_fact_does_not_count():
    assert (
        fact_verdict(
            D5Fact.PLAN_DATED_EARLY, True, ["registered with ClinicalTrials.gov, NCT04546581."], "x"
        )
        is FactVerdict.QUOTE_DOES_NOT_SUPPORT
    )
    assert (
        fact_verdict(D5Fact.MEASURE_FIXED, True, ["Pain was rated on a VAS."], "pain")
        is FactVerdict.NO_PLAN_STATED
    )
    assert fact_verdict(D5Fact.SWITCHED, True, [], "x") is FactVerdict.NO_QUOTE_FOUND
    assert (
        fact_verdict(D5Fact.PLAN_CITED, False, ["Registered as NCT01."], "x")
        is FactVerdict.NOT_CLAIMED
    )
    assert (
        fact_verdict(D5Fact.PLAN_CITED, True, ["Registered as NCT01."], "x") is FactVerdict.PRESENT
    )
    assert FactVerdict.QUOTE_DOES_NOT_SUPPORT.value == "quote_does_not_support"


def test_the_plan_may_be_stated_in_another_quote_of_the_same_fact():
    outcome = "change of mean diurnal intraocular pressure (IOP)"
    quotes = [
        "The secondary endpoints as prespecified were:",
        "Mean change in mean diurnal IOP at day 8",
    ]
    assert fact_verdict(D5Fact.MEASURE_FIXED, True, quotes, outcome) is FactVerdict.PRESENT
    assert (
        fact_verdict(D5Fact.MEASURE_FIXED, True, quotes[1:], outcome) is FactVerdict.NO_PLAN_STATED
    )


def test_a_result_shows_a_number_or_points_to_where_it_is_shown():
    assert shows_result(["p = 0.04"])
    assert shows_result(["Pain at twelve weeks is shown in Figure 2."])
    assert shows_result(["(Table S4)"])
    assert shows_result(["see the Supplementary Appendix"])
    assert not shows_result(["Function improved in both groups."])
    assert not shows_result([])
    # A digit is ASCII 0-9 only, in both languages.
    assert not shows_result(["fell by ٣ points"])
