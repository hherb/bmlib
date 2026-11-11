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

"""Checks in code that a quote found in the paper can support its D5 fact.

RoB 2's domain 5 (selection of the reported result) is assessed from facts
the model reports about the plan behind an outcome, each with quotes copied
from the paper (:mod:`bmlib.quality.rob2_d5_facts`).  A quote found in the
paper by :func:`~bmlib.citations.find_passage` can still fail to show its
fact: a bare registration number "dating" a plan, a sentence that reports
deaths "showing" a switched outcome, the primary outcome's measure quoted for
a secondary one.  These checks ask of a quote the least its fact needs; a
fact none of whose found quotes passes does not count.

The **planning check** asks more of what a plan fixes (the measure, the time
point, the analysis): the paper must say it was set in advance (prespecified,
planned, in the protocol or registration, a primary or secondary outcome or
analysis).  A methods description of what was done is not a plan.  The
maintainer's call, from the RoB 2 wording smoke test (StudyAnalyzer
``docs/smokescreens/2026-11-10-rob2-wording.md``).

Every check is case-insensitive and uses no look-around, so Python's ``re``
and Rust's ``regex`` crate agree; a digit is ASCII ``0-9`` in both languages.
``tests/fixtures/rob2_d5_checks.json`` pins every verdict across the two.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import Enum

__all__ = [
    "D5_FACTS",
    "D5Fact",
    "FIXED_FACTS",
    "FactVerdict",
    "fact_verdict",
    "names_outcome",
    "shows_result",
    "states_a_plan",
    "supports",
]


class D5Fact(Enum):
    """A fact about the plan behind an outcome, by its wire name."""

    PLAN_CITED = "plan_cited"
    PLAN_DATED_EARLY = "plan_dated_early"
    PLAN_LATE = "plan_late"
    MEASURE_FIXED = "measure_fixed"
    TIMEPOINT_FIXED = "timepoint_fixed"
    ANALYSIS_FIXED = "analysis_fixed"
    SWITCHED = "switched"
    CHANGE_EXPLAINED = "change_explained"


#: Every fact, in the order the model is asked them and the wire lists them.
D5_FACTS: tuple[D5Fact, ...] = tuple(D5Fact)

#: The facts that say what a plan fixes for the outcome; the planning check
#: applies to them.
FIXED_FACTS: tuple[D5Fact, ...] = (
    D5Fact.MEASURE_FIXED,
    D5Fact.TIMEPOINT_FIXED,
    D5Fact.ANALYSIS_FIXED,
)


class FactVerdict(Enum):
    """Whether a fact counts, and if not, why."""

    #: Claimed, a quote found, and the checks passed.
    PRESENT = "present"
    #: The model did not claim it.
    NOT_CLAIMED = "not_claimed"
    #: Claimed, but none of its quotes was found in the paper.
    NO_QUOTE_FOUND = "no_quote_found"
    #: Claimed and quoted, but no found quote can show it.
    QUOTE_DOES_NOT_SUPPORT = "quote_does_not_support"
    #: A measure, time point or analysis quoted, but not stated as set in advance.
    NO_PLAN_STATED = "no_plan_stated"


#: A date: a year (alone or in an 8-digit date), a month name, or a numeric
#: day/month/year.
DATE = re.compile(
    r"\b(19|20)[0-9]{2}([0-9]{4})?\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s"
    r"|\b[0-9]{1,2}[/.\-][0-9]{1,2}[/.\-][0-9]{2,4}\b",
    re.IGNORECASE,
)

#: A phrase placing a plan before the data: prospective, or before / prior to
#: a stage of the trial within 40 characters, in the same sentence.
BEFORE = re.compile(
    r"prospective"
    r"|\b(before|prior to)\b[^.]{0,40}\b(enrol|recruit|randomi|inclusion"
    r"|first (patient|participant)|start|unblind|database|lock|analys|data)",
    re.IGNORECASE,
)

#: The language of a change to an outcome.  "Difference" in a results
#: sentence is not one, so "differ" counts only as "differs from".
CHANGE = re.compile(
    r"\b(chang|differ(s|ed)? from|different from|amend|modif|replac|instead|original"
    r"|initial|added|add\b|remov|dropp|switch|deviat|no longer|revis|substitut|redefin"
    r"|post hoc|post-hoc)",
    re.IGNORECASE,
)

#: A time expression: a digit, or a word for a time or a visit.
TIME = re.compile(
    r"[0-9]|\b(day|week|month|year|hour|baseline|follow|end of|discharge|visit"
    r"|post-?operative|post-?treatment|admission)",
    re.IGNORECASE,
)

#: Language saying something was set in advance, not only described.
PLANNING = re.compile(
    r"pre-?specif|pre-?defin|\bplanned\b|a priori|protocol|regist|analysis plan"
    r"|\b(primary|secondary|main|principal) (efficacy |safety )?"
    r"(outcome|end ?-?point|objective|analys[ie]s|analysis model)",
    re.IGNORECASE,
)

#: A passage pointing to where a result is shown outside the running text,
#: which the text cannot show.
POINTER = re.compile(
    r"\b(fig(ure)?s?|tables?|supplement[a-z]*|appendix|appendices)\b", re.IGNORECASE
)

#: A word of an outcome's name or a quote: an ASCII letter, then three or more
#: letters or hyphens.
WORD = re.compile(r"[A-Za-z][A-Za-z\-]{3,}")

#: How many leading letters of a word stand for it, so "death" matches "deaths".
STEM_LETTERS = 5

#: Words of an outcome's name that say nothing about which outcome it is.
GENERIC_WORDS = frozenset(
    {
        "assessed", "using", "measured", "measure", "outcome", "outcomes", "score",
        "scores", "change", "changes", "from", "baseline", "after", "with", "total",
        "rate", "rates", "number", "percentage", "mean", "days", "weeks", "months",
        "years", "follow", "time", "point", "until", "through", "during", "level",
        "levels", "proportion", "patients", "participants", "children", "adults",
        "between", "groups", "group", "daily", "minutes",
    }
)  # fmt: skip


def _stems(text: str) -> set[str]:
    """The stems of *text*'s words."""
    return {w.lower()[:STEM_LETTERS] for w in WORD.findall(text)}


def names_outcome(quote: str, outcome: str) -> bool:
    """Whether *quote* shares a distinctive word with the outcome's name.

    Words are compared by their first :data:`STEM_LETTERS` letters, lower-cased.
    An outcome name with no distinctive word cannot be checked, and every
    quote passes.
    """
    wanted = {
        w.lower()[:STEM_LETTERS] for w in WORD.findall(outcome) if w.lower() not in GENERIC_WORDS
    }
    return not wanted or bool(wanted & _stems(quote))


def supports(fact: D5Fact, quote: str, outcome: str) -> bool:
    """Whether *quote* can support *fact* for the outcome named *outcome*.

    A dated plan needs a date or a phrase placing it before the data; a switch
    needs the language of a change; a measure must name the outcome; a time
    point needs a time.  Every other fact passes.
    """
    if fact is D5Fact.PLAN_DATED_EARLY:
        return bool(DATE.search(quote) or BEFORE.search(quote))
    if fact is D5Fact.SWITCHED:
        return bool(CHANGE.search(quote))
    if fact is D5Fact.MEASURE_FIXED:
        return names_outcome(quote, outcome)
    if fact is D5Fact.TIMEPOINT_FIXED:
        return bool(TIME.search(quote))
    return True


def states_a_plan(quotes: Sequence[str]) -> bool:
    """Whether any of *quotes* says something was set in advance."""
    return any(PLANNING.search(q) for q in quotes)


def fact_verdict(fact: D5Fact, claimed: bool, found: Sequence[str], outcome: str) -> FactVerdict:
    """Whether *fact* counts, given the model's claim and its quotes found in the paper.

    Args:
        fact: The fact.
        claimed: Whether the model said it is present.
        found: The paper's own wording of its quotes that were found.
        outcome: The name of the outcome assessed.

    Returns:
        :attr:`FactVerdict.PRESENT` when claimed, some quote found and some
        found quote :func:`supports` it, and, for a fact of
        :data:`FIXED_FACTS`, some found quote states a plan (it may be another
        quote of the same fact than the one naming the outcome).  Otherwise
        the first reason it fails, in that order.
    """
    if not claimed:
        return FactVerdict.NOT_CLAIMED
    if not found:
        return FactVerdict.NO_QUOTE_FOUND
    if not any(supports(fact, q, outcome) for q in found):
        return FactVerdict.QUOTE_DOES_NOT_SUPPORT
    if fact in FIXED_FACTS and not states_a_plan(found):
        return FactVerdict.NO_PLAN_STATED
    return FactVerdict.PRESENT


def shows_result(passages: Sequence[str]) -> bool:
    """Whether any passage gives a result: an ASCII digit, or a pointer to a
    figure, table, supplement or appendix, whose content the text cannot show."""
    return any(re.search(r"[0-9]", p) or POINTER.search(p) for p in passages)
