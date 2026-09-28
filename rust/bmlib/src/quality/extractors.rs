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

//! Rule-based (LLM-free) extractors for paper characteristics.
//!
//! A port of `bmlib/quality/extractors.py`. Pure functions that estimate study
//! characteristics with keyword heuristics: study-type detection with
//! exclusion-context guarding, sample-size extraction with logarithmic
//! scoring, and power-calculation / confidence-interval signals. They produce
//! [`DimensionScore`] objects with a full audit trail, and are a cheap
//! pre-filter or fallback for the LLM tiers.
//!
//! # The three corrections this module used to carry, and why it no longer does
//!
//! This port was written against a Python whose extractors had three defects,
//! and it corrected all three (#294, #297, #298). **Python then investigated
//! them on a 5,976-abstract Europe PMC draw and decided each one**, in the
//! extractor-audit batch that is now on `main`; this module follows the
//! decisions rather than the corrections. The history is kept here because a
//! reader who finds `#294`/`#297`/`#298` cited in the corpus deserves to know
//! which way each went.
//!
//! - **#294 — a digit-grouped sample size — Python adopted it.** Python's
//!   patterns captured `(\d+)`, so `"A total of 12,345 patients"` read as
//!   `345` and `"n = 12,345"` as `12`. [`COUNT`] is Python's `_COUNT` now,
//!   transcribed, and the corpus's six `294/*` cases diff strictly.
//! - **#297 — the power/CI signals were negation-blind — Python replaced the
//!   fix with a better one.** The port guarded a mention with a ±40-character
//!   window of negation words; on the draw that window refused **16 genuine
//!   confidence-interval reports and found no real denial**, because a CI is
//!   reported next to exactly that vocabulary (*"HR 0.96, 95% CI 0.46-1.49),
//!   with no difference"*). Python's [`is_denied`] is narrow on purpose — a
//!   negation at most three words before a mention with no preposition in
//!   between, or a negated verb of reporting straight after it — and
//!   [`has_ci_reporting`] never refuses a mention that states its own interval.
//!   The port's `is_negated`, `NEGATION_WORDS` and `NEGATION_CONTEXT_WINDOW`
//!   are gone with it.
//! - **#298 — priority over evidence — Python measured the port's fix and
//!   refused it.** The port vetoed a *higher*-priority study type whose mention
//!   sat in a contrastive clause, so an RCT that compared itself with
//!   quasi-experimental work classified as `rct`. On the draw the veto moved
//!   **55 study-type answers and none of them for the better** (29 of them
//!   [`has_exclusion_pattern`]'s window bug below), and the shape it was written
//!   for — an RCT abstract contrasting itself with quasi-experimental designs —
//!   occurs in **0 of 914** RCT abstracts. Python keeps the priority order and
//!   accepts that a contrastive mention of a higher-priority type wins; so does
//!   this module, and the two `298/*` corrections are retired. `rct`'s
//!   exclusion list holds `quasi-experimental` and `quasi experimental` again,
//!   as Python's does.
//!
//! **One defect the draw found in the port survived all three fixes**, and is
//! corrected here: [`has_exclusion_pattern`] scanned `text[start..keyword_pos]`,
//! which **ends before the keyword**, where Python scans
//! `text[start_pos : keyword_pos + len(keyword)]`, which **includes** it. That
//! difference is the whole of the rule for `"non-randomised controlled trial"`:
//! `iter_keyword_positions` finds `randomized controlled trial` *inside* it (the
//! hyphen is a word boundary), so the exclusion that has to fire is the one
//! containing the keyword itself. Measured on the draw, **27
//! `Controlled Clinical Trial` abstracts moved `unknown` → `rct`** — a
//! non-randomised trial scored as the design it explicitly says it is not,
//! which is worse than Python's `unknown`.
//!
//! # Why `fancy-regex`
//!
//! Python's rule tables use lookbehind, lookahead, scoped case folding
//! (`(?-i:CI)`) and possessive quantifiers. The port plan's §2 allows
//! `fancy-regex` for exactly these sites and refuses it for the ones reached
//! from the network: it adds backtracking, and the inputs here are an abstract
//! or a full text read from a database. **Python runs the same patterns on the
//! same bytes through `re`, also a backtracking engine**, so a transcription is
//! fidelity-preserving rather than a new worst case; the possessive quantifiers
//! Python uses as backtracking guards are transcribed as themselves, which
//! `fancy-regex` accepts.
//!
//! A transcription also inherits the engine's character classes, and **Rust's
//! are not Python's**: Rust's `\w` is
//! `[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]` where Python's is
//! `[\p{Alphabetic}\p{Nd}\p{Nl}\p{No}_]`, and Rust's `\s` is `\p{White_Space}`
//! where Python's `str.isspace()` also holds `U+001C`–`U+001F`. So a combining
//! mark (`Mn`) abuts a keyword for Python and not here, and a denial whose gap
//! is a file separator is read here and not there. Three corpus cases pin it —
//! `cw/combining-mark-before-keyword`, `cw/combining-mark-before-ci` and
//! `cw/file-separator-in-a-denial` — and the port plan's §9 carries the row,
//! with the reason the rewrite was declined: every `\b` would become a
//! four-branch lookaround alternation and every `[^\S\n]` a class difference,
//! over fifteen patterns, to reach characters no biomedical abstract carries,
//! and diffability against Python's source is what the transcription is for.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use fancy_regex::Regex;

use crate::quality::scoring_models::{
    DimensionScore, DIMENSION_SAMPLE_SIZE, DIMENSION_STUDY_DESIGN,
};

/// Priority order for study-type detection (highest evidence level first).
///
/// `quasi_experimental` precedes `rct` so that "non-randomized trial" does not
/// match RCT keywords like "randomized trial".
pub const STUDY_TYPE_PRIORITY: [&str; 12] = [
    "systematic_review",
    "meta_analysis",
    "quasi_experimental",
    "rct",
    "pilot_feasibility",
    "interventional_single_arm",
    "cohort_prospective",
    "cohort_retrospective",
    "case_control",
    "cross_sectional",
    "case_series",
    "case_report",
];

/// Default study-type keywords, as `(type, keywords)` pairs.
pub const DEFAULT_STUDY_TYPE_KEYWORDS: [(&str, &[&str]); 12] = [
    (
        "systematic_review",
        &["systematic review", "systematic literature review"],
    ),
    (
        "meta_analysis",
        &["meta-analysis", "meta analysis", "pooled analysis"],
    ),
    (
        "quasi_experimental",
        &[
            "non-randomized trial",
            "non-randomised trial",
            "nonrandomized trial",
            "nonrandomised trial",
            "quasi-experimental",
            "quasi experimental",
            "single-arm trial",
            "single arm trial",
            "open-label trial",
        ],
    ),
    (
        "rct",
        &[
            "randomized controlled trial",
            "randomised controlled trial",
            "RCT",
            "randomized trial",
            "randomised trial",
            "random allocation",
            "randomly assigned",
            "double-blind randomized",
            "double-blind randomised",
        ],
    ),
    (
        "pilot_feasibility",
        &[
            "pilot study",
            "pilot trial",
            "feasibility study",
            "feasibility trial",
            "proof-of-concept study",
            "proof of concept study",
        ],
    ),
    (
        "interventional_single_arm",
        &[
            "open-label",
            "open-labeled",
            "open label",
            "open labeled",
            "single-arm trial",
            "single-arm study",
            "single arm trial",
            "single arm study",
            "prospective protocol",
            "prospective intervention",
            "uncontrolled trial",
            "non-randomized trial",
            "non-randomised trial",
            "before-and-after study",
            "pre-post study",
            "pretest-posttest",
        ],
    ),
    (
        "cohort_prospective",
        &[
            "prospective cohort",
            "prospective study",
            "longitudinal cohort",
            "followed prospectively",
            "prospective follow-up",
            "prospective observation",
        ],
    ),
    (
        "cohort_retrospective",
        &["retrospective cohort", "retrospective study"],
    ),
    ("case_control", &["case-control", "case control study"]),
    (
        "cross_sectional",
        &[
            "cross-sectional",
            "cross sectional study",
            "prevalence study",
        ],
    ),
    ("case_series", &["case series", "case-series"]),
    ("case_report", &["case report", "case study"]),
];

/// Keywords that **exclude** a match for specific study types.
///
/// If any exclusion appears near the keyword, the match is rejected. `rct` is
/// the only type with a list, as in Python. **`quasi-experimental` and
/// `quasi experimental` are members**, which the port removed while it carried
/// its own #298 contrastive veto; they are back because Python's list has them
/// and the priority order is what keeps a genuinely quasi-experimental study
/// out of this branch.
pub const STUDY_TYPE_EXCLUSIONS: [(&str, &[&str]); 1] = [(
    "rct",
    &[
        "non-randomized",
        "non-randomised",
        "nonrandomized",
        "nonrandomised",
        "not randomized",
        "not randomised",
        "without randomization",
        "without randomisation",
        "quasi-experimental",
        "quasi experimental",
    ],
)];

/// How far before a keyword to search for an exclusion pattern (characters).
pub const EXCLUSION_CONTEXT_WINDOW: usize = 50;

/// Default study-type hierarchy scores.
pub const DEFAULT_STUDY_TYPE_HIERARCHY: [(&str, f64); 15] = [
    ("systematic_review", 10.0),
    ("meta_analysis", 10.0),
    ("rct", 8.0),
    ("quasi_experimental", 7.0),
    ("pilot_feasibility", 6.5),
    ("interventional_single_arm", 7.0),
    ("cohort_prospective", 6.0),
    ("cohort_retrospective", 5.0),
    ("case_control", 4.0),
    ("cross_sectional", 3.0),
    ("scoping_review", 3.0),
    ("narrative_review", 2.5),
    ("expert_opinion", 2.0),
    ("case_series", 2.0),
    ("case_report", 1.0),
];

/// Python's `_COUNT`: a count as a paper deposits it.
///
/// The patterns used to capture `(\d+)`, which cannot span a thousands
/// separator, so `"12,345 patients"` read as `345` and `"n = 12,345"` as `12`.
/// The separators are the ones a 5,976-abstract Europe PMC draw deposits beside
/// a count: a comma, and the space family — ASCII space, thin space, no-break
/// space, punctuation space, hair space, and the narrow no-break space added as
/// the last member. A period is not one of them: `"2.9 patients"` is a decimal,
/// and the one period-grouped count in the draw (`"35.020 patients"`) is
/// refused rather than guessed at. The lookarounds are what refuse it — a count
/// never starts inside a larger numeric token, nor ends before a decimal part,
/// a digit or a comma group. A comma followed by anything but three digits is a
/// list separator and ends the count (`"n=120,45% female"`). A period or comma
/// refuses a start only after a digit, where it is part of a number; after a
/// word it is prose punctuation (`"Of these,120 patients"`). The repeats are
/// bounded: unbounded, a long run of space-separated triples made every triple
/// a start scanning to the end (quadratic), and a long enough number reached
/// `int()`'s 4,300-digit limit and raised.
macro_rules! count_pattern {
    () => {
        r"(?<!\d)(?<!\d[.,])(\d{1,3}(?:,\d{3}){1,4}|\d{1,3}(?:[ \u{00a0}\u{2008}\u{2009}\u{200a}\u{202f}]\d{3}){1,4}|\d{1,15})(?!\.\d|,\d{3}|\d)"
    };
}

/// See [`COUNT`]'s documentation — this is that pattern as a value.
pub const COUNT: &str = count_pattern!();

/// Sample-size patterns, matched case-insensitively so `"N ="` counts.
///
/// Each captures one [`COUNT`]; [`find_sample_size`] strips the separators.
/// `n` is a whole word: without the boundary, `"mean = 118.45"` and
/// `"postintervention = 733.88"` each ended in `"n ="` (20 abstracts of 5,976,
/// and the returned size in 4).
pub const SAMPLE_SIZE_PATTERNS: [&str; 8] = [
    concat!(r"\bn\s*=\s*", count_pattern!()),
    concat!(count_pattern!(), r"\s+participants"),
    concat!(count_pattern!(), r"\s+subjects"),
    concat!(count_pattern!(), r"\s+patients"),
    concat!(r"sample\s+size\s+of\s+", count_pattern!()),
    concat!(
        r"total\s+of\s+",
        count_pattern!(),
        r"\s+(?:participants|subjects|patients)"
    ),
    concat!(
        r"enrolled\s+",
        count_pattern!(),
        r"\s+(?:participants|subjects|patients)"
    ),
    concat!(
        r"recruited\s+",
        count_pattern!(),
        r"\s+(?:participants|subjects|patients)"
    ),
];

/// Power-calculation keywords: phrases that name the calculation itself.
///
/// `"statistical power"` and `"power to detect"` were members and are not: of
/// 22 power-positive abstracts in the draw only 9 reported the paper's own
/// calculation, and most of the other 13 were these two phrases *discussing*
/// power — `"low statistical power"`, `"future studies with sufficient
/// statistical power"`, `"insufficient power to detect"`. A power the study
/// states as a number is the quantified pattern's.
pub const POWER_CALCULATION_KEYWORDS: [&str; 5] = [
    "power calculation",
    "power analysis",
    "power analyses",
    "sample size calculation",
    "calculated sample size",
];

/// The same claim in shapes a phrase list cannot hold.
///
/// The power-analysis program (`"calculated using G*Power"`, `"G*Power3.1"`),
/// and the phrase written the other way round (`"the sample size was
/// calculated"`). `"determined"` is not among the verbs — `"the sample size was
/// determined by the number of eligible patients"` is the opposite claim.
pub const POWER_CALCULATION_PATTERNS: [&str; 2] = [
    r"\bG\s*\*\s*Power(?![a-z])",
    r"\bsample\s+sizes?\s+(?:was|were|has\s+been|had\s+been)\s+(?:calculated|computed)\b",
];

/// A power the study states as a quantity: `"80% power"`, `"power of 0.80"`.
///
/// The quantity is range-checked by the study-power test — a calculation sets
/// power at 50% and below 100% — which is what keeps a cycling abstract's
/// `"mean power of 1.0%"` out. A test's `"predictive power of 88%"` is not a
/// study's power either, and nor is a laser's `"power of 0.6 mW"`: a fraction
/// followed by a unit of watts is refused. A table's `"Power 91.82%"` or a
/// laser `"operated at 80% power"` still reads as one; nothing in the text says
/// it is not. `\u{200b}` sits among the spaces because the draw deposits
/// `"80\u{a0}\u{200b}%"`. Every whitespace run is possessive: three adjacent
/// optional runs backtracked cubically, 51 s for 2,000 spaces after `"power"`.
const QUANTIFIED_POWER_PATTERN: &str = r"(?<![\d.])(?<pct>\d{1,3}(?:\.\d+)?)[\s\u{200b}]*+%[\s\u{200b}]*+(?:statistical\s++)?power\b|\bpower[\s\u{200b}]*+(?:(?:of|=|:|was|at)[\s\u{200b}]*+)?\(?[\s\u{200b}]*+(?:at\s++least[\s\u{200b}]++)?(?:(?<pct2>\d{1,3}(?:\.\d+)?)[\s\u{200b}]*+%|(?<frac>0?\.\d+)(?![\d%])(?![\s\u{200b}]*+[mk\u{3bc}\u{b5}]?W\b))";

/// The word before a quantified power that makes it a test's or a model's
/// rather than a study's: `"predictive power of 88%"`, `"diagnostic power"`.
const NOT_A_STUDY_POWER: &str =
    r"\b(?:predictive|discriminat\w*|diagnostic|explanatory|prognostic)[\s-]*$";

/// Markup a deposit may put between the parts of a CI report: `"95% <i>CI</i>"`,
/// `"CI<sub>95%</sub>"`, or bmlib's own Markdown — emphasis, and the `~95%~`
/// and `^95%^` its PubMed fetcher writes for `<sub>` and `<sup>`.
macro_rules! ci_markup {
    () => {
        r"(?:\s|<[^>]+>|[*_~^])*+"
    };
}

/// Confidence-interval patterns.
///
/// A bare `"CI"` token used to count on its own, and credited 16 abstracts in
/// the draw that report no interval: 11 are a cardiac index, a cochlear
/// implant, cognitive impairment or a chronicity index, the rest contrast-
/// induced AKI, a group label and the like. In full text it also credited
/// curies (`"Ci/mmol"`), chemical ionization, configuration interaction and a
/// drug-combination index. A `"CI"` now counts after a percentage (`"95% CI"`,
/// full-width `"95％CI"` too), before an interval or a percentage (`"CI
/// 1.1-2.0"`, `"CI: ±0.4%"`, `"CI 95%"`), or beside a bound (`"Lower CI"`, a
/// table's column header). A number alone is not an interval: `"CI-994"` is a
/// drug, `"CI of 2.4 L/min"` a cardiac index. The case keeps `"cis-9"` and
/// `"Ci/mmol"` out; after a percentage a lowercase `"ci"` is allowed, but not
/// `"cis"`. The bare-numeric bracket/range forms require a decimal point in
/// both numbers so integer citation markers like `"[12, 15]"` and year ranges
/// like `"(2010-2015)"` do not count as CI reporting.
pub const CI_PATTERNS: [&str; 6] = [
    r"confidence\s+intervals?",
    concat!(
        r"(?<![\d.])\d+(?:\.\d+)?\s*+[%\u{ff05}]\s*+-?",
        ci_markup!(),
        r"(?-i:CIs?|ci)(?![a-z])"
    ),
    concat!(
        r"(?<!\w)(?-i:CIs?)(?:\b|(?=\d{2}(?:\.\d+)?\s*+[%\u{ff05}]))",
        ci_markup!(),
        r"(?:of\s++)?[:=,\u{ff1a}]?\s*+(?:-?\d{2}(?:\.\d+)?\s*+[%\u{ff05}]|[\[(]?\s*+(?:\u{b1}\s*+\d|[-\u{2212}\u{2013}]?\d+(?:[.\u{b7}]\d+)?\s*+%?\s*+(?:[-\u{2010}\u{2212}\u{2013}~\u{ff5e},;]|to\b)\s*+[-\u{2212}\u{2013}]?\d))"
    ),
    r"\b(?:lower|upper)[\s-]++(?-i:CIs?)\b|(?<!\w)(?-i:CIs?)[\s-]++(?:lower|upper|limits?|bounds?)\b",
    r"\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\]",
    r"\(\s*\d+\.\d+\s*-\s*\d+\.\d+\s*\)",
];

/// How far either side of a mention [`is_denied`] reads.
///
/// It bounds the work, and it also bounds the reach: three long words can put a
/// negation beyond it (`"at most three words"` holds of words that fit), and a
/// window cut inside a word is trimmed to the next boundary so the cut does not
/// make a word of its own.
pub const DENIAL_LOOKAROUND: usize = 80;

/// Whitespace that may stand between the parts of a denial, blank lines
/// included.
macro_rules! denial_gap {
    () => {
        r"(?=\s)[^\S\n]*\n?[^\S\n]*"
    };
}

/// As [`denial_gap`], but the blank line is optional and it may be empty.
macro_rules! denial_space {
    () => {
        r"[^\S\n]*\n?[^\S\n]*"
    };
}

/// A denial that governs a mention: a negation at most three words before it.
///
/// With nothing but words in between, or a negated verb of reporting straight
/// after it. Narrow on purpose — a confidence interval is reported next to
/// exactly the vocabulary a wider window reads (`"HR 0.96, 95% CI 0.46-1.49),
/// with no difference"`), and a ±40-character window refused 16 genuine CI
/// reports in the draw while finding no real denial. A percentage counts as a
/// word between (`"did not report 95% confidence intervals"`). Two things end
/// the reach, each from a false denial in the served full text: a blank line,
/// which separates table cells and paragraphs, and a preposition attaching the
/// mention to the noun the negation governs (`"no overlap between the 95% CI"`).
/// `"by"`, `"on"` and `"using"` are not among them: `"not predetermined by a
/// power calculation"` is a denial. `"not only"` is not a negation.
macro_rules! denied_before {
    () => {
        concat!(
            r"\b(?:no|not(?![^\S\n]+only\b)|without|neither|nor|never|cannot)",
            r"(?:",
            denial_gap!(),
            r"(?:(?!(?:between|as|than|within|across|of|in|at|from|with|over|among)\b)[a-z-]+|\d+(?:\.\d+)?[^\S\n]*%)){0,3}",
            denial_gap!(),
            r"$"
        )
    };
}

/// A negation of reporting right after a mention: `"was not reported"`.
///
/// The blank-line rule holds on both sides, so a table's `"95% CI"` header does
/// not read the next cell's `"Not reported"`. The verbs include a target the
/// study says it missed (`"a power of 80% was not achieved"`), which reports no
/// calculation. A label's colon may precede it (`"Power calculation: not
/// performed"`).
macro_rules! denied_after {
    () => {
        concat!(
            r"^",
            denial_space!(),
            r"(?:\([^()]{1,20}\)",
            denial_space!(),
            r")?(?::",
            denial_space!(),
            r")?",
            r"(?:(?:was|were|is|are|has|have|had|been|be|could|would|can|may|might|will|should|did|does|do)",
            denial_gap!(),
            r")*",
            r"(?:not|never|cannot)",
            denial_gap!(),
            r"(?:(?:been|be)",
            denial_gap!(),
            r")?",
            r"(?:performed|reported|calculated|conducted|done|provided|given|stated|available|presented|described|carried\s+out|undertaken|achieved|reached|attained)\b"
        )
    };
}

/// A compiled pattern from a constant, with the flags Python gives it.
fn compiled(cell: &'static OnceLock<Regex>, pattern: &str, ignore_case: bool) -> &'static Regex {
    cell.get_or_init(|| {
        let body = if ignore_case {
            format!("(?i){pattern}")
        } else {
            pattern.to_string()
        };
        Regex::new(&body).expect("a fixed pattern")
    })
}

fn sample_size_res() -> &'static [Regex] {
    static P: OnceLock<Vec<Regex>> = OnceLock::new();
    P.get_or_init(|| {
        SAMPLE_SIZE_PATTERNS
            .iter()
            .map(|p| Regex::new(&format!("(?i){p}")).expect("a fixed pattern"))
            .collect()
    })
}

/// Python's `\b` + escaped words joined by `\s+` + `s?\b`, per keyword.
fn power_keyword_res() -> &'static [Regex] {
    static P: OnceLock<Vec<Regex>> = OnceLock::new();
    P.get_or_init(|| {
        POWER_CALCULATION_KEYWORDS
            .iter()
            .map(|keyword| {
                let body = keyword
                    .split_whitespace()
                    .map(regex::escape)
                    .collect::<Vec<_>>()
                    .join(r"\s+");
                Regex::new(&format!(r"(?i)\b{body}s?\b")).expect("a fixed pattern")
            })
            .collect()
    })
}

fn power_pattern_res() -> &'static [Regex] {
    static P: OnceLock<Vec<Regex>> = OnceLock::new();
    P.get_or_init(|| {
        POWER_CALCULATION_PATTERNS
            .iter()
            .map(|p| Regex::new(&format!("(?i){p}")).expect("a fixed pattern"))
            .collect()
    })
}

fn quantified_power_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    compiled(&R, QUANTIFIED_POWER_PATTERN, true)
}

fn not_a_study_power_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    compiled(&R, NOT_A_STUDY_POWER, true)
}

fn ci_res() -> &'static [Regex] {
    static P: OnceLock<Vec<Regex>> = OnceLock::new();
    P.get_or_init(|| {
        CI_PATTERNS
            .iter()
            .map(|p| Regex::new(&format!("(?i){p}")).expect("a fixed pattern"))
            .collect()
    })
}

fn ci_stated_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    compiled(&R, CI_PATTERNS[2], true)
}

fn denied_before_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    compiled(&R, denied_before!(), true)
}

fn denied_after_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    compiled(&R, denied_after!(), true)
}

fn digit_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    compiled(&R, r"\d", false)
}

/// Whether `c` is a word character, Python's `\w` for this module's use.
fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// The decimal value of a character Python's `\d` matches.
///
/// `char::to_digit` reads ASCII only; Python's `\d` is Unicode `Nd`, so a count
/// deposited in Arabic-Indic digits parses there and would not here. Each `Nd`
/// block's ten digits are consecutive, so the value is the distance back to the
/// first `\d` in the run.
fn decimal_value(c: char) -> Option<u32> {
    if let Some(d) = c.to_digit(10) {
        return Some(d);
    }
    let one = c.to_string();
    if !digit_re().is_match(&one).unwrap_or(false) {
        return None;
    }
    let mut value = 0u32;
    let mut current = c as u32;
    while value < 9 {
        let previous = char::from_u32(current.checked_sub(1)?)?;
        if !digit_re().is_match(&previous.to_string()).unwrap_or(false) {
            break;
        }
        current -= 1;
        value += 1;
    }
    Some(value)
}

/// Strip every non-digit from `raw` and parse, as Python's `int(re.sub(r"\D",
/// "", raw))` does.
///
/// Returns `None` where the result is empty or does not parse. A count grouped
/// with commas, spaces or a Unicode space separator reads as its digits, and a
/// count in a non-ASCII decimal script reads as itself.
#[must_use]
pub fn parse_number(raw: &str) -> Option<i64> {
    let mut digits = String::with_capacity(raw.len());
    let mut any = false;
    for c in raw.chars() {
        if let Some(value) = decimal_value(c) {
            digits.push(char::from_digit(value, 10)?);
            any = true;
        }
    }
    if !any {
        return None;
    }
    digits.parse::<i64>().ok()
}

/// Yield start offsets (in characters) of whole-word occurrences of `keyword`.
///
/// A match must start and end at a word boundary (an optional plural `s` is
/// tolerated), so the keyword `"rct"` matches `"RCTs"` but not `"infarct"`.
#[must_use]
pub fn iter_keyword_positions(text: &str, keyword: &str) -> Vec<usize> {
    let pattern = format!(r"(?i)(?<!\w){}s?(?!\w)", regex::escape(keyword));
    let Ok(re) = Regex::new(&pattern) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut byte_cursor = 0usize;
    let mut char_cursor = 0usize;
    for hit in re.find_iter(text).flatten() {
        char_cursor += text[byte_cursor..hit.start()].chars().count();
        byte_cursor = hit.start();
        out.push(char_cursor);
    }
    out
}

/// A snippet of `text` around an occurrence of `keyword`.
///
/// Uses the first occurrence unless `keyword_pos` gives the offset of a
/// specific one. Adds ellipses where the snippet is truncated. Returns `""` if
/// the keyword is not present.
#[must_use]
pub fn extract_text_context(
    text: &str,
    keyword: &str,
    context_chars: usize,
    keyword_pos: Option<usize>,
) -> String {
    let chars: Vec<char> = text.chars().collect();
    let pos = match keyword_pos {
        Some(p) => p,
        None => match text.find(keyword) {
            Some(byte) => text[..byte].chars().count(),
            None => return String::new(),
        },
    };
    if pos >= chars.len() {
        return String::new();
    }

    let start = pos.saturating_sub(context_chars);
    let end = (pos + keyword.chars().count() + context_chars).min(chars.len());

    let mut context: String = chars[start..end].iter().collect();
    if start > 0 {
        context = format!("...{context}");
    }
    if end < chars.len() {
        context.push_str("...");
    }
    context
}

/// Choose the best text from `document` for rule-based extraction.
///
/// Prefers a substantial `full_text` (longer than the abstract), otherwise
/// falls back to `abstract` + `methods_text`.
#[must_use]
pub fn prepare_extractor_search_text(document: &BTreeMap<String, String>) -> String {
    let get = |k: &str| document.get(k).cloned().unwrap_or_default();
    let full_text = get("full_text");
    let abstract_ = get("abstract");
    let methods = get("methods_text");

    if !full_text.is_empty() && full_text.chars().count() > abstract_.chars().count() {
        return full_text;
    }
    format!("{abstract_} {methods}")
}

/// Find the sample size in `text`, returning the largest plausible match.
#[must_use]
pub fn find_sample_size(text: &str, min_n: i64, max_n: i64) -> Option<i64> {
    let mut found: Vec<i64> = Vec::new();
    for re in sample_size_res() {
        for caps in re.captures_iter(text).flatten() {
            let Some(group) = caps.get(1) else { continue };
            if let Some(size) = parse_number(group.as_str()) {
                if size >= min_n && size <= max_n {
                    found.push(size);
                }
            }
        }
    }
    found.into_iter().max()
}

/// Score a sample size on a 0-10 scale as `log10(n) * log_multiplier`.
#[must_use]
pub fn calculate_sample_size_score(n: i64, log_multiplier: f64) -> f64 {
    if n <= 0 {
        return 0.0;
    }
    let score = (n as f64).log10() * log_multiplier;
    score.clamp(0.0, 10.0)
}

/// Whether the mention at `text[start..end]` is denied.
///
/// See the `denied_before` and `denied_after` macros, which hold the two shapes
/// and the measurement that narrowed them.
#[must_use]
pub fn is_denied(text: &str, start: usize, end: usize) -> bool {
    let chars: Vec<char> = text.chars().collect();
    let len = chars.len();
    let start = start.min(len);
    let end = end.min(len);

    let window_start = start.saturating_sub(DENIAL_LOOKAROUND);
    let mut before: String = chars[window_start..start].iter().collect();
    if window_start > 0 && is_word_char(chars[window_start - 1]) {
        // A window cut inside a word would make its tail a word of its own:
        // "casino" cut to "no". A cut on a word boundary keeps its first word.
        let keep_from = before
            .char_indices()
            .find(|(_, c)| !is_word_char(*c))
            .map_or(before.len(), |(i, _)| i);
        before = before[keep_from..].to_string();
    }
    let after: String = chars[end..(end + DENIAL_LOOKAROUND).min(len)]
        .iter()
        .collect();

    denied_before_re().is_match(&before).unwrap_or(false)
        || denied_after_re().is_match(&after).unwrap_or(false)
}

/// Whether a quantified-power match states a power a calculation would set.
fn is_a_study_power(text: &str, caps: &fancy_regex::Captures<'_>) -> bool {
    let Some(whole) = caps.get(0) else {
        return false;
    };
    let chars: Vec<char> = text.chars().collect();
    let start = text[..whole.start()].chars().count();
    let window: String = chars[start.saturating_sub(30)..start].iter().collect();
    if not_a_study_power_re().is_match(&window).unwrap_or(false) {
        return false;
    }
    let percent = caps
        .name("pct")
        .or_else(|| caps.name("pct2"))
        .map(|m| m.as_str().to_string());
    let value = match percent {
        Some(p) => p.parse::<f64>().ok().map(|v| v / 100.0),
        None => caps
            .name("frac")
            .and_then(|m| m.as_str().parse::<f64>().ok()),
    };
    match value {
        Some(v) => (0.5..1.0).contains(&v),
        None => false,
    }
}

/// A mention of a signal: its offset in characters and the text it matched.
type Mention = (usize, String);

/// The first power-calculation mention that is not denied, if any.
fn find_power_mention(text: &str) -> Option<Mention> {
    let mut candidates: Vec<(usize, usize)> = Vec::new();
    for re in power_keyword_res() {
        for hit in re.find_iter(text).flatten() {
            candidates.push((hit.start(), hit.end()));
        }
    }
    for re in power_pattern_res() {
        for hit in re.find_iter(text).flatten() {
            candidates.push((hit.start(), hit.end()));
        }
    }
    for caps in quantified_power_re().captures_iter(text).flatten() {
        let Some(whole) = caps.get(0) else { continue };
        if is_a_study_power(text, &caps) {
            candidates.push((whole.start(), whole.end()));
        }
    }
    candidates.sort_by_key(|&(start, _)| start);
    for (start, end) in candidates {
        if !is_denied(
            text,
            text[..start].chars().count(),
            text[..end].chars().count(),
        ) {
            return Some((text[..start].chars().count(), text[start..end].to_string()));
        }
    }
    None
}

/// Whether the CI match at `caps` carries the interval or percentage it reports.
///
/// `"No adverse events 95% CI 0.1-0.4"` and `"Never smokers 95% CI 1.0-1.4"`
/// are a table row's label beside a reported interval: nothing can deny a CI
/// that states its own bounds.
fn states_its_interval(text: &str, start: usize, end: usize) -> bool {
    let chars: Vec<char> = text.chars().collect();
    let len = chars.len();
    let pos = start.min(len);
    let endpos = (end + DENIAL_LOOKAROUND).min(len);
    if pos >= endpos {
        return false;
    }
    // Python's `_CI_STATED.search(text, pos, endpos)`: the lookbehind at the
    // search's own start may still see the character before it, so the slice
    // carries one character of context and a match beginning in it is refused.
    let slice_start = pos.saturating_sub(1);
    let slice: String = chars[slice_start..endpos].iter().collect();
    for hit in ci_stated_re().find_iter(&slice).flatten() {
        let hit_start = slice_start + slice[..hit.start()].chars().count();
        let hit_end = slice_start + slice[..hit.end()].chars().count();
        if hit_start >= pos && hit_end <= endpos {
            return hit_start < end;
        }
    }
    false
}

/// The first confidence-interval report that is not denied, if any.
fn find_ci_mention(text: &str) -> Option<Mention> {
    let chars: Vec<char> = text.chars().collect();
    let mut candidates: Vec<(usize, usize)> = Vec::new();
    for re in ci_res() {
        for hit in re.find_iter(text).flatten() {
            candidates.push((
                text[..hit.start()].chars().count(),
                text[..hit.end()].chars().count(),
            ));
        }
    }
    candidates.sort_by_key(|&(start, _)| start);
    for (start, end) in candidates {
        if states_its_interval(text, start, end) || !is_denied(text, start, end) {
            let matched: String = chars[start..end].iter().collect();
            return Some((start, matched));
        }
    }
    None
}

/// A snippet around `mention`, or `""` when there is none.
fn mention_context(text: &str, mention: &Option<Mention>) -> String {
    match mention {
        Some((start, matched)) => extract_text_context(text, matched, 50, Some(*start)),
        None => String::new(),
    }
}

/// Whether `text` reports the study's own power calculation.
///
/// Counts a calculation phrase ([`POWER_CALCULATION_KEYWORDS`],
/// [`POWER_CALCULATION_PATTERNS`]) or a power stated as a quantity from 50% up
/// to, not including, 100% (the quantified pattern), and refuses a mention a
/// denial governs ([`is_denied`]). A discussion of power — `"low statistical
/// power"` — is not a calculation.
#[must_use]
pub fn has_power_calculation(text: &str) -> bool {
    find_power_mention(text).is_some()
}

/// A snippet around the mention that earns the power bonus, or `""`.
///
/// It is the same mention [`has_power_calculation`] credits, so a bonus never
/// records empty evidence.
#[must_use]
pub fn find_power_calc_context(text: &str) -> String {
    mention_context(text, &find_power_mention(text))
}

/// Whether `text` reports confidence intervals.
///
/// Tests [`CI_PATTERNS`] and refuses a mention a denial governs ([`is_denied`]),
/// unless the mention states its interval or percentage (`"No difference 95% CI
/// 0.9-1.5"` is a report).
#[must_use]
pub fn has_ci_reporting(text: &str) -> bool {
    find_ci_mention(text).is_some()
}

/// A snippet around the mention that earns the CI bonus, or `""`.
#[must_use]
pub fn find_ci_context(text: &str) -> String {
    mention_context(text, &find_ci_mention(text))
}

/// Whether an exclusion pattern appears just before `keyword`.
///
/// Prevents false positives such as `"non-randomized trial"` matching as RCT
/// when searching for `"randomized trial"`. Checks the first occurrence unless
/// `keyword_pos` gives the offset of a specific one.
///
/// **The window ends *after* the keyword**, which is Python's
/// `text[start_pos : keyword_pos + len(keyword)]`. The port used to end it
/// *before* the keyword, and that is the difference #366 measured: for
/// `"non-randomised controlled trial"`, the keyword `randomized controlled
/// trial` is found *inside* the negation, and only a window that contains the
/// keyword contains the `"non-randomised"` that disqualifies it.
#[must_use]
pub fn has_exclusion_pattern(
    text: &str,
    keyword: &str,
    exclusion_patterns: &[&str],
    context_window: usize,
    keyword_pos: Option<usize>,
) -> bool {
    let chars: Vec<char> = text.chars().collect();
    let pos = match keyword_pos {
        Some(p) => p,
        None => match text.find(keyword) {
            Some(byte) => text[..byte].chars().count(),
            None => return false,
        },
    };
    if pos >= chars.len() {
        return false;
    }

    let start_pos = pos.saturating_sub(context_window);
    let end_pos = (pos + keyword.chars().count()).min(chars.len());
    let context_before: String = chars[start_pos..end_pos].iter().collect();

    exclusion_patterns
        .iter()
        .any(|e| context_before.contains(&e.to_lowercase()))
}

/// Look a study type up in a `(name, keywords)` table.
fn keywords_for<'a>(table: &'a [(&'a str, &'a [&'a str])], study_type: &str) -> &'a [&'a str] {
    table
        .iter()
        .find(|(name, _)| *name == study_type)
        .map_or(&[], |(_, kws)| *kws)
}

/// Look a study type's exclusions up in a `(name, exclusions)` table.
fn exclusions_for<'a>(table: &'a [(&'a str, &'a [&'a str])], study_type: &str) -> &'a [&'a str] {
    table
        .iter()
        .find(|(name, _)| *name == study_type)
        .map_or(&[], |(_, ex)| *ex)
}

/// Look a study type's score up in a `(name, score)` table.
fn hierarchy_score(table: &[(&str, f64)], study_type: &str) -> Option<f64> {
    table
        .iter()
        .find(|(name, _)| *name == study_type)
        .map(|(_, score)| *score)
}

/// Detect study type by keyword matching, with exclusion-context guarding.
///
/// Searches `full_text` when available (else abstract + methods) and tries each
/// type in priority order, rejecting matches whose exclusion patterns fire.
/// **The first type with any surviving match wins**: a lower-priority type is
/// never consulted once a higher one has matched, so a clean description of the
/// paper's own design loses to any unexcluded mention of a higher-priority
/// type, a contrastive one included (`"in contrast to quasi-experimental
/// designs"`). That shape measured 0 of 914 RCT abstracts, and reordering the
/// priority cost more than it recovered (#298). Keywords match whole words only
/// (with an optional plural `s`), so `"RCT"` matches `"RCTs"` but not
/// `"infarct"`; every occurrence of a keyword is tried, so one excluded mention
/// does not suppress a later clean one. Returns a [`DimensionScore`] for the
/// study-design dimension with an audit trail; defaults to `"unknown"` at a
/// neutral score when nothing matches.
#[must_use]
pub fn extract_study_type(document: &BTreeMap<String, String>) -> DimensionScore {
    let search_text = prepare_extractor_search_text(document).to_lowercase();

    for study_type in STUDY_TYPE_PRIORITY {
        let keywords = keywords_for(&DEFAULT_STUDY_TYPE_KEYWORDS, study_type);
        let exclusions = exclusions_for(&STUDY_TYPE_EXCLUSIONS, study_type);

        for keyword in keywords {
            let keyword_lower = keyword.to_lowercase();
            for keyword_pos in iter_keyword_positions(&search_text, &keyword_lower) {
                if !exclusions.is_empty()
                    && has_exclusion_pattern(
                        &search_text,
                        &keyword_lower,
                        exclusions,
                        EXCLUSION_CONTEXT_WINDOW,
                        Some(keyword_pos),
                    )
                {
                    continue;
                }

                let score =
                    hierarchy_score(&DEFAULT_STUDY_TYPE_HIERARCHY, study_type).unwrap_or(5.0);
                let mut dimension_score = DimensionScore::new(DIMENSION_STUDY_DESIGN, score);
                dimension_score.add_detail(
                    "study_type",
                    study_type,
                    score,
                    Some(extract_text_context(
                        &search_text,
                        &keyword_lower,
                        50,
                        Some(keyword_pos),
                    )),
                    Some(format!(
                        "Matched keyword '{keyword}' indicating {}",
                        study_type.replace('_', " ")
                    )),
                );
                return dimension_score;
            }
        }
    }

    let mut dimension_score = DimensionScore::new(DIMENSION_STUDY_DESIGN, 5.0);
    dimension_score.add_detail(
        "study_type",
        "unknown",
        5.0,
        None,
        Some("No study type keywords matched - assigned neutral score".to_string()),
    );
    dimension_score
}

/// Extract sample size and score it, with power/CI bonuses.
///
/// Applies logarithmic scoring to the extracted size, then adds bonuses when a
/// power calculation and/or confidence intervals are reported, capped at 10.
/// Returns a [`DimensionScore`] with an audit trail; a score of 0 when no
/// sample size is found.
///
/// **The search text is not lower-cased here**, where `extract_study_type`
/// lower-cases its own: Python passes `prepare_extractor_search_text(document)`
/// straight to the signal readers, so the audit trail's excerpts keep the
/// paper's capitalisation, and every pattern is case-insensitive anyway. The
/// port used to lower-case it, which moved the stored evidence text for every
/// abstract that reports a power calculation or a CI.
#[must_use]
pub fn extract_sample_size_dimension(document: &BTreeMap<String, String>) -> DimensionScore {
    let log_multiplier = 2.0;
    let power_bonus = 2.0;
    let ci_bonus = 0.5;

    let search_text = prepare_extractor_search_text(document);
    let Some(sample_size) = find_sample_size(&search_text, 5, 1_000_000) else {
        let mut dimension_score = DimensionScore::new(DIMENSION_SAMPLE_SIZE, 0.0);
        dimension_score.add_detail(
            "extracted_n",
            "not_found",
            0.0,
            None,
            Some("No sample size could be extracted from text".to_string()),
        );
        return dimension_score;
    };

    // Both the score and the detail's contribution are the **capped** value:
    // Python passes `base_score` (already clamped by
    // `calculate_sample_size_score`) to `add_detail`, so a million-patient
    // study records 10.0 in the audit trail, not 12.0. The reasoning string
    // reports the capped number too, which is why it reads `= 10.00`.
    let base_score = calculate_sample_size_score(sample_size, log_multiplier);
    let mut dimension_score = DimensionScore::new(DIMENSION_SAMPLE_SIZE, base_score);
    dimension_score.add_detail(
        "extracted_n",
        &sample_size.to_string(),
        base_score,
        None,
        Some(format!(
            "Log10({sample_size}) * {log_multiplier:.1} = {base_score:.2}"
        )),
    );

    if has_power_calculation(&search_text) {
        dimension_score.score = (dimension_score.score + power_bonus).min(10.0);
        dimension_score.add_detail(
            "power_calculation",
            "yes",
            power_bonus,
            Some(find_power_calc_context(&search_text)),
            Some(format!(
                "Power calculation mentioned, bonus +{power_bonus:.1}"
            )),
        );
    }

    if has_ci_reporting(&search_text) {
        dimension_score.score = (dimension_score.score + ci_bonus).min(10.0);
        dimension_score.add_detail(
            "ci_reporting",
            "yes",
            ci_bonus,
            Some(find_ci_context(&search_text)),
            Some(format!("Confidence intervals reported, bonus +{ci_bonus}")),
        );
    }

    dimension_score
}

/// The numeric sample size recorded in a sample-size dimension.
#[must_use]
pub fn get_extracted_sample_size(dimension_score: &DimensionScore) -> Option<i64> {
    let value = dimension_score.details.first()?.extracted_value.as_ref()?;
    value.parse::<i64>().ok()
}

/// The study-type string recorded in a study-design dimension.
#[must_use]
pub fn get_extracted_study_type(dimension_score: &DimensionScore) -> Option<String> {
    dimension_score.details.first()?.extracted_value.clone()
}
