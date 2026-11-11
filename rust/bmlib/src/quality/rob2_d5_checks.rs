// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Checks in code that a quote found in the paper can support its D5 fact.
//!
//! RoB 2's domain 5 (selection of the reported result) is assessed from facts
//! the model reports about the plan behind an outcome, each with quotes copied
//! from the paper. A quote found in the paper by
//! [`find_passage`](crate::citations::find_passage) can still fail to show
//! its fact: a bare registration number "dating" a plan, a sentence that
//! reports deaths "showing" a switched outcome, the primary outcome's measure
//! quoted for a secondary one. These checks ask of a quote the least its fact
//! needs; a fact none of whose found quotes passes does not count.
//!
//! The **planning check** asks more of what a plan fixes (the measure, the
//! time point, the analysis): the paper must say it was set in advance
//! (prespecified, planned, in the protocol or registration, a primary or
//! secondary outcome or analysis). A methods description of what was done is
//! not a plan. The maintainer's call, from the RoB 2 wording smoke test
//! (StudyAnalyzer `docs/smokescreens/2026-11-10-rob2-wording.md`).
//!
//! Every check is case-insensitive and uses no look-around, so this module
//! and Python's `bmlib.quality.rob2_d5_checks` (on `re`) agree; a digit is
//! ASCII `0-9` in both languages. `tests/fixtures/rob2_d5_checks.json` pins
//! every verdict across the two.

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::LazyLock;

/// A fact about the plan behind an outcome, by its wire name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum D5Fact {
    /// The paper cites a registration, protocol or analysis plan.
    PlanCited,
    /// The paper places that plan before the outcome data by group were seen.
    PlanDatedEarly,
    /// The paper says the plan was made after recruitment began or the data
    /// were collected.
    PlanLate,
    /// The paper states the outcome's measure as set in advance.
    MeasureFixed,
    /// The paper states the outcome's time point as set in advance.
    TimepointFixed,
    /// The paper states the outcome's analysis as set in advance.
    AnalysisFixed,
    /// The outcome assessed itself differs from the registration or protocol.
    Switched,
    /// That change has a reason that does not depend on its results.
    ChangeExplained,
}

/// Every fact, in the order the model is asked them and the wire lists them.
pub const D5_FACTS: [D5Fact; 8] = [
    D5Fact::PlanCited,
    D5Fact::PlanDatedEarly,
    D5Fact::PlanLate,
    D5Fact::MeasureFixed,
    D5Fact::TimepointFixed,
    D5Fact::AnalysisFixed,
    D5Fact::Switched,
    D5Fact::ChangeExplained,
];

/// The facts that say what a plan fixes for the outcome; the planning check
/// applies to them.
pub const FIXED_FACTS: [D5Fact; 3] = [
    D5Fact::MeasureFixed,
    D5Fact::TimepointFixed,
    D5Fact::AnalysisFixed,
];

impl D5Fact {
    /// The fact's wire name (`plan_cited`, ...).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::PlanCited => "plan_cited",
            Self::PlanDatedEarly => "plan_dated_early",
            Self::PlanLate => "plan_late",
            Self::MeasureFixed => "measure_fixed",
            Self::TimepointFixed => "timepoint_fixed",
            Self::AnalysisFixed => "analysis_fixed",
            Self::Switched => "switched",
            Self::ChangeExplained => "change_explained",
        }
    }

    /// The fact with wire name `name`, or `None`.
    #[must_use]
    pub fn parse(name: &str) -> Option<Self> {
        D5_FACTS.into_iter().find(|f| f.as_str() == name)
    }
}

/// Whether a fact counts, and if not, why.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FactVerdict {
    /// Claimed, a quote found, and the checks passed.
    Present,
    /// The model did not claim it.
    NotClaimed,
    /// Claimed, but none of its quotes was found in the paper.
    NoQuoteFound,
    /// Claimed and quoted, but no found quote can show it.
    QuoteDoesNotSupport,
    /// A measure, time point or analysis quoted, but not stated as set in
    /// advance.
    NoPlanStated,
}

impl FactVerdict {
    /// Every verdict, for reading one back by its wire name.
    pub const ALL: [FactVerdict; 5] = [
        Self::Present,
        Self::NotClaimed,
        Self::NoQuoteFound,
        Self::QuoteDoesNotSupport,
        Self::NoPlanStated,
    ];

    /// The verdict's wire name (`present`, ...).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Present => "present",
            Self::NotClaimed => "not_claimed",
            Self::NoQuoteFound => "no_quote_found",
            Self::QuoteDoesNotSupport => "quote_does_not_support",
            Self::NoPlanStated => "no_plan_stated",
        }
    }

    /// The verdict with wire name `name`, or `None`.
    #[must_use]
    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|v| v.as_str() == name)
    }
}

/// Compiles one of this module's patterns, which are constants and known good.
fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a D5 check pattern compiles")
}

/// A date: a year (alone or in an 8-digit date), a month name, or a numeric
/// day/month/year.
pub static DATE: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)\b(19|20)[0-9]{2}([0-9]{4})?\b",
        r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s",
        r"|\b[0-9]{1,2}[/.\-][0-9]{1,2}[/.\-][0-9]{2,4}\b",
    ))
});

/// A phrase placing a plan before the data: prospective, or before / prior
/// to a stage of the trial within 40 characters, in the same sentence.
pub static BEFORE: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)prospective",
        r"|\b(before|prior to)\b[^.]{0,40}\b(enrol|recruit|randomi|inclusion",
        r"|first (patient|participant)|start|unblind|database|lock|analys|data)",
    ))
});

/// The language of a change to an outcome. "Difference" in a results sentence
/// is not one, so "differ" counts only as "differs from".
pub static CHANGE: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)\b(chang|differ(s|ed)? from|different from|amend|modif|replac|instead|original",
        r"|initial|added|add\b|remov|dropp|switch|deviat|no longer|revis|substitut|redefin",
        r"|post hoc|post-hoc)",
    ))
});

/// A time expression: a digit, or a word for a time or a visit.
pub static TIME: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)[0-9]|\b(day|week|month|year|hour|baseline|follow|end of|discharge|visit",
        r"|post-?operative|post-?treatment|admission)",
    ))
});

/// Language saying something was set in advance, not only described.
pub static PLANNING: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)pre-?specif|pre-?defin|\bplanned\b|a priori|protocol|regist|analysis plan",
        r"|\b(primary|secondary|main|principal) (efficacy |safety )?",
        r"(outcome|end ?-?point|objective|analys[ie]s|analysis model)",
    ))
});

/// A passage pointing to where a result is shown outside the running text,
/// which the text cannot show.
pub static POINTER: LazyLock<Regex> = LazyLock::new(|| {
    pattern(r"(?i)\b(fig(ure)?s?|tables?|supplement[a-z]*|appendix|appendices)\b")
});

/// A word of an outcome's name or a quote: an ASCII letter, then three or
/// more letters or hyphens.
pub static WORD: LazyLock<Regex> = LazyLock::new(|| pattern(r"[A-Za-z][A-Za-z\-]{3,}"));

/// How many leading letters of a word stand for it, so "death" matches
/// "deaths".
pub const STEM_LETTERS: usize = 5;

/// Words of an outcome's name that say nothing about which outcome it is.
pub const GENERIC_WORDS: [&str; 42] = [
    "assessed",
    "using",
    "measured",
    "measure",
    "outcome",
    "outcomes",
    "score",
    "scores",
    "change",
    "changes",
    "from",
    "baseline",
    "after",
    "with",
    "total",
    "rate",
    "rates",
    "number",
    "percentage",
    "mean",
    "days",
    "weeks",
    "months",
    "years",
    "follow",
    "time",
    "point",
    "until",
    "through",
    "during",
    "level",
    "levels",
    "proportion",
    "patients",
    "participants",
    "children",
    "adults",
    "between",
    "groups",
    "group",
    "daily",
    "minutes",
];

/// A word's stem: its first [`STEM_LETTERS`] letters, lower-cased.
fn stem(word: &str) -> String {
    word.to_lowercase().chars().take(STEM_LETTERS).collect()
}

/// Whether `quote` shares a distinctive word with the outcome's name.
///
/// Words are compared by their first [`STEM_LETTERS`] letters, lower-cased.
/// An outcome name with no distinctive word cannot be checked, and every
/// quote passes.
#[must_use]
pub fn names_outcome(quote: &str, outcome: &str) -> bool {
    let wanted: Vec<String> = WORD
        .find_iter(outcome)
        .map(|m| m.as_str())
        .filter(|w| !GENERIC_WORDS.contains(&w.to_lowercase().as_str()))
        .map(stem)
        .collect();
    wanted.is_empty()
        || WORD
            .find_iter(quote)
            .any(|m| wanted.contains(&stem(m.as_str())))
}

/// Whether `quote` can support `fact` for the outcome named `outcome`.
///
/// A dated plan needs a date or a phrase placing it before the data; a switch
/// needs the language of a change; a measure must name the outcome; a time
/// point needs a time. Every other fact passes.
#[must_use]
pub fn supports(fact: D5Fact, quote: &str, outcome: &str) -> bool {
    match fact {
        D5Fact::PlanDatedEarly => DATE.is_match(quote) || BEFORE.is_match(quote),
        D5Fact::Switched => CHANGE.is_match(quote),
        D5Fact::MeasureFixed => names_outcome(quote, outcome),
        D5Fact::TimepointFixed => TIME.is_match(quote),
        D5Fact::PlanCited | D5Fact::PlanLate | D5Fact::AnalysisFixed | D5Fact::ChangeExplained => {
            true
        }
    }
}

/// Whether any of `quotes` says something was set in advance.
#[must_use]
pub fn states_a_plan(quotes: &[&str]) -> bool {
    quotes.iter().any(|q| PLANNING.is_match(q))
}

/// Whether `fact` counts, given the model's claim and its quotes found in the
/// paper (`found`, the paper's own wording), for the outcome named `outcome`.
///
/// [`FactVerdict::Present`] when claimed, some quote found and some found
/// quote [`supports`] it, and, for a fact of [`FIXED_FACTS`], some found quote
/// states a plan (it may be another quote of the same fact than the one
/// naming the outcome). Otherwise the first reason it fails, in that order.
#[must_use]
pub fn fact_verdict(fact: D5Fact, claimed: bool, found: &[&str], outcome: &str) -> FactVerdict {
    if !claimed {
        FactVerdict::NotClaimed
    } else if found.is_empty() {
        FactVerdict::NoQuoteFound
    } else if !found.iter().any(|q| supports(fact, q, outcome)) {
        FactVerdict::QuoteDoesNotSupport
    } else if FIXED_FACTS.contains(&fact) && !states_a_plan(found) {
        FactVerdict::NoPlanStated
    } else {
        FactVerdict::Present
    }
}

/// Whether any passage gives a result: an ASCII digit, or a pointer to a
/// figure, table, supplement or appendix, whose content the text cannot show.
#[must_use]
pub fn shows_result(passages: &[&str]) -> bool {
    passages
        .iter()
        .any(|p| p.bytes().any(|b| b.is_ascii_digit()) || POINTER.is_match(p))
}
