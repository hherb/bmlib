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
//! # Three defects this port fixes rather than reproduces
//!
//! **#294 — a digit-grouped sample size.** Every pattern in Python captures
//! `(\d+)`, which cannot span a comma, so `12,345` reads as `345` or as `12`
//! depending on which side the pattern anchors:
//!
//! ```text
//! "A total of 12,345 patients"  -> 345     (last run)
//! "n = 12,345"                  -> 12      (first run)
//! "n = 1,000,000 participants"  -> None    (fragment below min_n)
//! ```
//!
//! A fragment below `min_n` reads as *absent*, so a million-patient study
//! scored 0.0. [`NUMBER`] accepts `,` thousands separators and
//! [`parse_number`] strips them.
//!
//! **#297 — the power/CI signals are negation-blind.** Python's
//! `has_power_calculation` is a bare substring test and `has_ci_reporting` a
//! bare pattern search, so a text saying *"No power calculation was performed
//! and confidence intervals were not reported"* is awarded **both** bonuses —
//! and the audit trail records the opposite of the source. [`is_negated`]
//! guards both, which is the machinery the module already applies to study
//! type.
//!
//! **#298 — priority over evidence.** `quasi_experimental` sits ahead of `rct`
//! in [`STUDY_TYPE_PRIORITY`], so a paper that describes itself as a
//! randomised controlled trial *and compares itself to* quasi-experimental
//! designs is classified as quasi-experimental. The contrastive construction
//! is now an exclusion for `quasi_experimental`, so the unguarded clean
//! higher-tier match wins.
//!
//! Each fix is pinned by a named test and by the differential oracle's
//! `corrected` mechanism.

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
/// If any exclusion appears near the keyword, the match is rejected. `rct`'s
/// list is Python's; `quasi_experimental`'s is this port's fix for #298.
pub const STUDY_TYPE_EXCLUSIONS: [(&str, &[&str]); 2] = [
    (
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
            // Deliberately NOT "quasi-experimental" / "quasi experimental",
            // which Python's list contains. Those words are already handled by
            // the *priority order*: `quasi_experimental` is consulted before
            // `rct`, so a paper that is genuinely quasi-experimental never
            // reaches this branch. Keeping them here instead lets the phrase
            // veto a real RCT from 39 characters away — "In contrast to
            // quasi-experimental studies, this was a randomized controlled
            // trial" matched RCT's keyword and was then disqualified by its own
            // contrast clause, returning `unknown` (#298).
        ],
    ),
    // `quasi_experimental`'s entry is deliberately empty: its fix for #298 is
    // the contrastive check in `has_exclusion_pattern`, not a word list. An
    // entry here would mean "these words disqualify the type outright", which
    // is how the first cut of the fix let an earlier "quasi-experimental"
    // veto a later genuine RCT.
    ("quasi_experimental", &[]),
];

/// How far before a keyword to search for an exclusion pattern (characters).
pub const EXCLUSION_CONTEXT_WINDOW: usize = 50;

/// How far before a keyword to look for a negation word (characters).
///
/// Wider than [`EXCLUSION_CONTEXT_WINDOW`] because a denial is usually phrased
/// across more words than a prefix ("no power calculation was performed"), and
/// narrower than a sentence because a denial in a *previous* sentence must not
/// silence this one.
pub const NEGATION_CONTEXT_WINDOW: usize = 40;

/// Words that negate a following claim, matched as whole words.
pub const NEGATION_WORDS: [&str; 14] = [
    "no", "not", "never", "without", "absent", "lacking", "lacked", "neither", "nor", "unable",
    "failed", "denied", "none", "cannot",
];

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

/// The numeric alternative every sample-size pattern uses.
///
/// **This is the fix for #294.** Python's patterns capture `(\d+)`, which
/// cannot span a thousands separator, so `12,345` yields a fragment. This
/// accepts `,`-separated groups and [`parse_number`] removes them.
pub const NUMBER: &str = r"\d{1,3}(?:,\d{3})+|\d+";

/// Sample-size patterns, matched case-insensitively.
pub const SAMPLE_SIZE_PATTERNS: [&str; 8] = [
    r"n\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)",
    r"(\d{1,3}(?:,\d{3})+|\d+)\s+participants",
    r"(\d{1,3}(?:,\d{3})+|\d+)\s+subjects",
    r"(\d{1,3}(?:,\d{3})+|\d+)\s+patients",
    r"sample\s+size\s+of\s+(\d{1,3}(?:,\d{3})+|\d+)",
    r"total\s+of\s+(\d{1,3}(?:,\d{3})+|\d+)\s+(?:participants|subjects|patients)",
    r"enrolled\s+(\d{1,3}(?:,\d{3})+|\d+)\s+(?:participants|subjects|patients)",
    r"recruited\s+(\d{1,3}(?:,\d{3})+|\d+)\s+(?:participants|subjects|patients)",
];

/// Power-calculation keywords.
pub const POWER_CALCULATION_KEYWORDS: [&str; 6] = [
    "power calculation",
    "power analysis",
    "sample size calculation",
    "calculated sample size",
    "statistical power",
    "power to detect",
];

/// Confidence-interval patterns.
///
/// The bare-numeric bracket/range forms require a decimal point in both
/// numbers so integer citation markers like `[12, 15]` and year ranges like
/// `(2010-2015)` do not count as CI reporting.
pub const CI_PATTERNS: [&str; 5] = [
    r"confidence interval",
    r"\bCI\b",
    r"95%\s*CI",
    r"\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\]",
    r"\(\s*\d+\.\d+\s*-\s*\d+\.\d+\s*\)",
];

/// Strip thousands separators and parse.
#[must_use]
pub fn parse_number(raw: &str) -> Option<i64> {
    raw.replace(',', "").parse::<i64>().ok()
}

fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Yield start offsets of whole-word occurrences of `keyword` in `text`.
///
/// A match must start and end at a word boundary, with an optional plural `s`
/// tolerated, so the keyword `rct` matches `RCTs` but not `infarct`. ASCII
/// case-insensitive, matching Python's `re.IGNORECASE` for these ASCII
/// keywords.
#[must_use]
pub fn iter_keyword_positions(text: &str, keyword: &str) -> Vec<usize> {
    let chars: Vec<char> = text.chars().collect();
    let needle: Vec<char> = keyword.to_lowercase().chars().collect();
    let mut out = Vec::new();
    if needle.is_empty() || needle.len() > chars.len() {
        return out;
    }
    let lower: Vec<char> = text.to_lowercase().chars().collect();
    let mut i = 0usize;
    while i + needle.len() <= lower.len() {
        if lower[i..i + needle.len()] == needle[..] {
            let char_before_ok = i == 0 || !is_word_char(lower[i - 1]);
            // An optional trailing `s`, then a boundary.
            let after = i + needle.len();
            let ends_ok = if after < lower.len() && lower[after] == 's' {
                after + 1 >= lower.len() || !is_word_char(lower[after + 1])
            } else {
                after >= lower.len() || !is_word_char(lower[after])
            };
            if char_before_ok && ends_ok {
                out.push(i);
            }
        }
        i += 1;
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
pub fn prepare_extractor_search_text(
    document: &std::collections::BTreeMap<String, String>,
) -> String {
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
///
/// # The fix for #294
///
/// See [`NUMBER`]. Python's fragment-producing behaviour is described in the
/// module docs; here `12,345` yields `12345`, `n = 1,000,000 participants`
/// yields `1000000`, and the returned value is the real count.
#[must_use]
pub fn find_sample_size(text: &str, min_n: i64, max_n: i64) -> Option<i64> {
    let lower = text.to_lowercase();
    let mut found: Vec<i64> = Vec::new();
    for pattern in SAMPLE_SIZE_PATTERNS {
        for capture in capture_group1_all(&lower, pattern) {
            if let Some(size) = parse_number(&capture) {
                if size >= min_n && size <= max_n {
                    found.push(size);
                }
            }
        }
    }
    found.into_iter().max()
}

/// Every match of a pattern's first capture group, in order.
///
/// Only the patterns in [`SAMPLE_SIZE_PATTERNS`] are needed, and they are a
/// small closed set of literal-plus-`NUMBER` shapes, so this is a hand-rolled
/// matcher rather than a regex dependency. It returns the captured text.
fn capture_group1_all(text: &str, pattern: &str) -> Vec<String> {
    let mut out = Vec::new();
    // Find the *capturing* group's parentheses: the first `(` that is not the
    // start of a `(?:` non-capturing group. Using `rfind(')')` here was a bug —
    // it landed on a trailing `(?:...)`'s close, so a pattern like
    // `(\d+)\s+(?:participants|subjects|patients)` captured the whole tail
    // rather than the digits, and no sample size was ever found.
    let chars_pat: Vec<char> = pattern.chars().collect();
    let mut open = None;
    let mut i = 0usize;
    while i < chars_pat.len() {
        if chars_pat[i] == '(' {
            let is_group_prefix = chars_pat.get(i + 1) == Some(&'?');
            let is_lookaround = chars_pat.get(i + 1) == Some(&'?')
                && matches!(chars_pat.get(i + 2), Some(':' | '=' | '!'));
            if !is_group_prefix {
                open = Some(i);
                break;
            }
            if is_lookaround && chars_pat.get(i + 2) == Some(&':') {
                // A non-capturing group: skip it and keep looking.
                i += 1;
                continue;
            }
            i += 1;
            continue;
        }
        i += 1;
    }
    let Some(open) = open else {
        return out;
    };
    // The matching close: scan forward with a depth counter starting at 1 for
    // the group's own `(`, so an inner `...` group balances out. Stopping at
    // the first `)` (as a first cut did) truncated a group like
    // `(\d+\.\d+\s*,\s*\d+\.\d+)` down to `\d+\.\d+`, so the CI
    // bracket pattern never matched its second number.
    let mut depth = 1i32;
    let mut close = None;
    let mut j = open + 1;
    while j < chars_pat.len() {
        match chars_pat[j] {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 {
                    close = Some(j);
                    break;
                }
            }
            _ => {}
        }
        j += 1;
    }
    let Some(close) = close else {
        return out;
    };
    let prefix: String = chars_pat[..open].iter().collect();
    let group: String = chars_pat[open + 1..close].iter().collect();
    let suffix: String = chars_pat[close + 1..].iter().collect();

    // Every capture group here is the `NUMBER` form, whose alternation is
    // `\d{1,3}(?:,\d{3})+|\d+`: the grouped reading is *tried first and
    // allowed to fail*, falling back to a plain run of digits. Forcing the
    // grouped reading (as a first cut did, by inspecting the group text)
    // made `200 patients` unmatchable, because there is no comma.
    let _ = &group;

    let chars: Vec<char> = text.chars().collect();
    let mut i = 0usize;
    while i < chars.len() {
        let Some(prefix_len) = match_literal_prefix(&chars, i, &prefix) else {
            i += 1;
            continue;
        };
        let cap_start = i + prefix_len;
        let captured = match_number(&chars, cap_start);
        let Some((cap_text, cap_len)) = captured else {
            i += 1;
            continue;
        };
        let after_cap = cap_start + cap_len;
        let Some(suffix_len) = match_alternation_then_literal(&chars, after_cap, &suffix) else {
            i += 1;
            continue;
        };
        out.push(cap_text);
        i = after_cap + suffix_len;
    }
    out
}

/// Match `suffix`, expanding any `(?:a|b|c)` non-capturing group.
///
/// The suffixes in [`SAMPLE_SIZE_PATTERNS`] are literal text with at most one
/// such group, which is what this handles.
fn match_alternation_then_literal(chars: &[char], start: usize, suffix: &str) -> Option<usize> {
    // Split on `(?:` ... `)`.
    if let Some(gopen) = suffix.find("(?:") {
        let gclose = suffix[gopen..].find(')')? + gopen;
        let before = &suffix[..gopen];
        let alternatives = &suffix[gopen + 3..gclose];
        let after = &suffix[gclose + 1..];

        let mut i = start;
        i += match_literal_prefix(chars, i, before)?;
        // Try each alternative; the first that matches wins (regex alternation
        // is ordered, and these alternatives cannot both match).
        let mut matched = None;
        for alt in alternatives.split('|') {
            if let Some(len) = match_literal_prefix(chars, i, alt) {
                matched = Some(len);
                break;
            }
        }
        let len = matched?;
        let after_alt = i + len;
        let tail = match_literal_prefix(chars, after_alt, after)?;
        return Some(after_alt + tail - start);
    }
    match_literal_prefix(chars, start, suffix)
}

/// Match a literal fragment, honouring the regex escapes these patterns use.
///
/// Returns the number of characters consumed, or `None`.
fn match_literal_prefix(chars: &[char], start: usize, literal: &str) -> Option<usize> {
    let lit: Vec<char> = literal.chars().collect();
    let mut i = start;
    let mut k = 0usize;

    while k < lit.len() {
        match lit[k] {
            '\\' => {
                let next = *lit.get(k + 1)?;
                // `\s*` / `\s+`: zero-or-more / one-or-more whitespace.
                if next == 's' {
                    let greedy = lit.get(k + 2);
                    if greedy == Some(&'*') || greedy == Some(&'+') {
                        let began = i;
                        while i < chars.len() && chars[i].is_whitespace() {
                            i += 1;
                        }
                        if greedy == Some(&'+') && i == began {
                            return None;
                        }
                        k += 3;
                        continue;
                    }
                    if i >= chars.len() || !chars[i].is_whitespace() {
                        return None;
                    }
                    i += 1;
                    k += 2;
                    continue;
                }
                // `\b`: a word-boundary assertion, consuming nothing.
                if next == 'b' {
                    let before_ok = i == 0 || !is_word_char(chars[i - 1]);
                    let after_ok = i >= chars.len() || !is_word_char(chars[i]);
                    if !(before_ok && after_ok) {
                        return None;
                    }
                    k += 2;
                    continue;
                }
                // Any other escape is the literal character it escapes.
                if i >= chars.len() || chars[i] != next {
                    return None;
                }
                i += 1;
                k += 2;
            }
            '[' => {
                // A character class: `[abc]` — all these patterns use simple
                // positive sets with no ranges or negation.
                let close = lit[k..].iter().position(|c| *c == ']')? + k;
                if i >= chars.len() {
                    return None;
                }
                let set = &lit[k + 1..close];
                if !set.contains(&chars[i]) {
                    return None;
                }
                i += 1;
                k = close + 1;
            }
            c => {
                if i >= chars.len() || chars[i] != c {
                    return None;
                }
                i += 1;
                k += 1;
            }
        }
    }
    Some(i - start)
}

/// Match a [`NUMBER`] at `start`, returning `(text, characters consumed)`.
fn match_number(chars: &[char], start: usize) -> Option<(String, usize)> {
    let mut i = start;
    // `\d{1,3}(?:,\d{3})+|\d+` — try the grouped alternative first, since it is
    // the more specific one and a regex engine would prefer it too.
    let mut grouped = 0usize;
    let mut j = i;
    while grouped < 3 && j < chars.len() && chars[j].is_ascii_digit() {
        grouped += 1;
        j += 1;
    }
    if grouped >= 1 {
        let mut k = j;
        let mut groups = 0usize;
        while k + 3 < chars.len() + 1 && chars.get(k) == Some(&',') {
            if k + 3 < chars.len() + 1
                && chars.get(k + 1).is_some_and(char::is_ascii_digit)
                && chars.get(k + 2).is_some_and(char::is_ascii_digit)
                && chars.get(k + 3).is_some_and(char::is_ascii_digit)
            {
                groups += 1;
                k += 4;
            } else {
                break;
            }
        }
        if groups > 0 {
            let text: String = chars[i..k].iter().collect();
            return Some((text, k - i));
        }
    }
    // Second alternative: a plain run of digits.
    let mut n = 0usize;
    while i < chars.len() && chars[i].is_ascii_digit() {
        n += 1;
        i += 1;
    }
    if n == 0 {
        return None;
    }
    let text: String = chars[start..start + n].iter().collect();
    Some((text, n))
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

/// Whether a keyword's mention is denied, by a negation word on either side.
///
/// **The fix for #297.** A denial is phrased two ways, and both are common:
///
/// | Form | Example |
/// |---|---|
/// | negation **before** | `"No power calculation was performed"` |
/// | negation **after** | `"A power calculation was not performed"` |
/// | negation **between** | `"Confidence intervals were not reported"` |
///
/// The keyword is followed by a copula, then the negation, then the verb — so
/// a backwards-only window misses the second and third rows, and a
/// forwards-only window misses the first. Both are scanned, each within
/// `window` characters, which is why the window is wider than
/// [`EXCLUSION_CONTEXT_WINDOW`]: a denial is spread over more words than a
/// prefix.
///
/// Matching is whole-word, so `"nothing"` does not read as `"no"` and
/// `"notably"` does not read as `"not"`.
///
/// Deliberately **not** applied to study-type keywords: *"no randomized
/// controlled trials were included"* is a legitimate sentence in a systematic
/// review, and suppressing the study-type signal there would lose real
/// evidence. #297 is about *bonuses* asserted on absent evidence.
#[must_use]
pub fn is_negated(text: &str, keyword_pos: usize, window: usize) -> bool {
    let chars: Vec<char> = text.chars().collect();
    if keyword_pos >= chars.len() {
        return false;
    }
    let before_start = keyword_pos.saturating_sub(window);
    let after_end = (keyword_pos + window).min(chars.len());

    let before: String = chars[before_start..keyword_pos].iter().collect();
    let after: String = chars[keyword_pos..after_end].iter().collect();

    contains_negation_word(&before) || contains_negation_word(&after)
}

/// Whether any whole word in `text` is one of [`NEGATION_WORDS`].
fn contains_negation_word(text: &str) -> bool {
    text.split(|c: char| !is_word_char(c))
        .filter(|w| !w.is_empty())
        .any(|w| NEGATION_WORDS.contains(&w.to_lowercase().as_str()))
}

/// Whether `text` mentions a power/sample-size calculation.
///
/// A mention preceded by a negation word does not count — see [`is_negated`].
#[must_use]
pub fn has_power_calculation(text: &str) -> bool {
    let lower = text.to_lowercase();
    for keyword in POWER_CALCULATION_KEYWORDS {
        let mut from = 0usize;
        while let Some(rel) = lower[from..].find(keyword) {
            let byte_pos = from + rel;
            let char_pos = lower[..byte_pos].chars().count();
            if !is_negated(&lower, char_pos, NEGATION_CONTEXT_WINDOW) {
                return true;
            }
            from = byte_pos + keyword.len();
            if from >= lower.len() {
                break;
            }
        }
    }
    false
}

/// A snippet around the first power-calculation mention, if any.
#[must_use]
pub fn find_power_calc_context(text: &str) -> String {
    let lower = text.to_lowercase();
    for keyword in &POWER_CALCULATION_KEYWORDS[..3] {
        if lower.contains(keyword) {
            return extract_text_context(&lower, keyword, 50, None);
        }
    }
    String::new()
}

/// Whether `text` reports confidence intervals.
///
/// A mention preceded by a negation word does not count — see [`is_negated`].
#[must_use]
pub fn has_ci_reporting(text: &str) -> bool {
    let lower = text.to_lowercase();
    for pattern in CI_PATTERNS {
        for (char_pos, _) in pattern_hits(&lower, pattern) {
            if !is_negated(&lower, char_pos, NEGATION_CONTEXT_WINDOW) {
                return true;
            }
        }
    }
    false
}

/// Every hit of one [`CI_PATTERNS`] entry, as `(character offset, length)`.
fn pattern_hits(text: &str, pattern: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    match pattern {
        r"confidence interval" => {
            push_all(&mut out, text, "confidence interval");
        }
        r"\bCI\b" => {
            let chars: Vec<char> = text.chars().collect();
            let lower: Vec<char> = text.to_lowercase().chars().collect();
            for i in 0..lower.len().saturating_sub(1) {
                if lower[i] == 'c' && lower[i + 1] == 'i' {
                    let before_ok = i == 0 || !is_word_char(chars[i - 1]);
                    let after_ok = i + 2 >= chars.len() || !is_word_char(chars[i + 2]);
                    if before_ok && after_ok {
                        out.push((i, 2));
                    }
                }
            }
        }
        r"95%\s*CI" => {
            let lower: Vec<char> = text.to_lowercase().chars().collect();
            let mut i = 0usize;
            while i + 4 <= lower.len() {
                if lower[i] == '9' && lower[i + 1] == '5' && lower[i + 2] == '%' {
                    let mut j = i + 3;
                    while j < lower.len() && lower[j].is_whitespace() {
                        j += 1;
                    }
                    if j + 1 < lower.len() && lower[j] == 'c' && lower[j + 1] == 'i' {
                        out.push((i, j + 2 - i));
                        i = j + 2;
                        continue;
                    }
                }
                i += 1;
            }
        }
        // `\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\]` — a bracketed decimal
        // pair; `\(\s*\d+\.\d+\s*-\s*\d+\.\d+\s*\)` — a parenthesised
        // decimal range. Both are matched directly: a bracketed decimal is a
        // *literal* shape, and routing it through the general capture-group
        // scanner (which is built for the `NUMBER` alternation) silently
        // matched nothing.
        r"\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\]" => {
            push_decimal_pair(&mut out, text, '[', ',', ']');
        }
        r"\(\s*\d+\.\d+\s*-\s*\d+\.\d+\s*\)" => {
            push_decimal_pair(&mut out, text, '(', '-', ')');
        }
        _ => {}
    }
    out
}

fn push_all(out: &mut Vec<(usize, usize)>, text: &str, needle: &str) {
    let lower = text.to_lowercase();
    let mut from = 0usize;
    while let Some(rel) = lower[from..].find(needle) {
        let byte = from + rel;
        out.push((lower[..byte].chars().count(), needle.chars().count()));
        from = byte + needle.len();
        if from >= lower.len() {
            break;
        }
    }
}

/// Find `open`, spaces, decimal, spaces, `sep`, spaces, decimal, spaces, `close`.
fn push_decimal_pair(
    out: &mut Vec<(usize, usize)>,
    text: &str,
    open: char,
    sep: char,
    close: char,
) {
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0usize;
    while i < chars.len() {
        if chars[i] != open {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < chars.len() && chars[j].is_whitespace() {
            j += 1;
        }
        // `match_decimal` returns `(text, LENGTH)`, not an end offset — using
        // the length as a position is what made this look for the separator
        // *inside* the first number.
        let Some((_, first_len)) = match_decimal(&chars, j) else {
            i += 1;
            continue;
        };
        let mut k = j + first_len;
        while k < chars.len() && chars[k].is_whitespace() {
            k += 1;
        }
        if k >= chars.len() || chars[k] != sep {
            i += 1;
            continue;
        }
        k += 1;
        while k < chars.len() && chars[k].is_whitespace() {
            k += 1;
        }
        let Some((_, second_len)) = match_decimal(&chars, k) else {
            i += 1;
            continue;
        };
        let mut m = k + second_len;
        while m < chars.len() && chars[m].is_whitespace() {
            m += 1;
        }
        if m < chars.len() && chars[m] == close {
            out.push((i, m + 1 - i));
            i = m + 1;
            continue;
        }
        i += 1;
    }
}

/// Match `\d+\.\d+` at `start`, returning `(text, characters consumed)`.
fn match_decimal(chars: &[char], start: usize) -> Option<(String, usize)> {
    let mut i = start;
    let mut int_digits = 0usize;
    while i < chars.len() && chars[i].is_ascii_digit() {
        int_digits += 1;
        i += 1;
    }
    if int_digits == 0 || i >= chars.len() || chars[i] != '.' {
        return None;
    }
    i += 1;
    let mut frac = 0usize;
    while i < chars.len() && chars[i].is_ascii_digit() {
        frac += 1;
        i += 1;
    }
    if frac == 0 {
        return None;
    }
    Some((chars[start..i].iter().collect(), i - start))
}

/// Constructions that mark a mention as *contrastive* rather than the paper's
/// own design.
///
/// These are checked **on both sides** of the keyword, which the general
/// exclusion window is not. `"in contrast to quasi-experimental designs"` puts
/// the marker first, but `"quasi-experimental studies, compared with …"` puts
/// it after — and a backwards-only window misses the second, which is how the
/// #298 fix first failed to fire.
pub const CONTRASTIVE_MARKERS: [&str; 10] = [
    "in contrast to",
    "in contrast with",
    "compared with",
    "compared to",
    "as opposed to",
    "rather than in",
    // A bare `unlike` is too common to disqualify a study type on its own:
    // "unlike the control group" says nothing about the paper's design. These
    // two require the plural noun that makes it a comparison *between study
    // designs*, which is the reading #298 is about.
    "unlike prior",
    "unlike previous",
    "unlike quasi",
    "unlike observational",
];

/// Characters that end a clause, so a contrastive marker in an *earlier*
/// clause does not reach a keyword in this one.
const CLAUSE_BOUNDARIES: [char; 4] = [',', ';', '.', '\n'];

/// Whether an exclusion pattern appears near `keyword`.
///
/// Prevents false positives such as "non-randomized trial" matching as RCT
/// when searching for "randomized trial".
///
/// The scan is **backwards only** by default, which is the published behaviour
/// and is what the RCT exclusions need: `"non-randomized"` and
/// `"quasi-experimental"` both precede the keyword they disqualify. Markers in
/// [`CONTRASTIVE_MARKERS`] are additionally checked forwards, because a
/// contrastive construction may follow the mention — see that constant.
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
    // The windowed scan, which is what the RCT exclusions need: "non-" and
    // "quasi-" sit immediately before the keyword, and a mention far earlier
    // in the document must not disqualify it.
    let start = pos.saturating_sub(context_window);
    let before_window: String = chars[start..pos].iter().collect();

    let window = before_window.to_lowercase();
    let kw_len = keyword.chars().count();

    // A contrastive marker frames a *clause*, not a document, so it is looked
    // for within this keyword's clause only. An unbounded search reaches any
    // marker anywhere earlier, and `"In contrast to quasi-experimental studies,
    // this was a randomized controlled trial"` then disqualifies the RCT on a
    // marker belonging to the clause before it — which is how an earlier cut of
    // this fix returned `unknown` for a perfectly plain RCT.
    //
    // The clause runs from the previous boundary to the next one. The marker
    // may sit on either side of the keyword because both readings occur:
    // `"In contrast to X studies, this was an RCT"` and `"X studies, compared
    // with …"`.
    let boundary_before = chars[..pos]
        .iter()
        .rposition(|c| CLAUSE_BOUNDARIES.contains(c))
        .map_or(0, |b| b + 1);
    let boundary_after = chars[pos + kw_len..]
        .iter()
        .position(|c| CLAUSE_BOUNDARIES.contains(c))
        .map_or(chars.len(), |b| pos + kw_len + b);
    let clause_before: String = chars[boundary_before..pos].iter().collect();
    let clause_after: String = chars[(pos + kw_len).min(chars.len())..boundary_after]
        .iter()
        .collect();
    let clause_before = clause_before.to_lowercase();
    let clause_after = clause_after.to_lowercase();

    // Checked **first and instead of** the word exclusions: a clause framed
    // against prior work is not a claim about this paper's design, and letting
    // the word `"quasi-experimental"` in that same clause disqualify the RCT is
    // the other half of the bug.
    if CONTRASTIVE_MARKERS
        .iter()
        .any(|m| clause_before.contains(m) || clause_after.contains(m))
    {
        return true;
    }

    // Otherwise the windowed word scan, which is what the RCT exclusions need
    // ("non-randomized", "quasi-experimental" as the *subject* of the phrase).
    exclusion_patterns
        .iter()
        .any(|e| window.contains(&e.to_lowercase()))
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
/// Searches `full_text` when available (else abstract + methods), tries each
/// type in priority order, and rejects matches whose exclusion patterns fire.
/// Keywords match whole words only, with an optional plural `s`, so `RCT`
/// matches `RCTs` but not `infarct`; every occurrence of a keyword is tried, so
/// one excluded mention does not suppress a later clean one. Returns a
/// [`DimensionScore`] for the study-design dimension; defaults to `"unknown"`
/// at a neutral score when nothing matches.
///
/// # The fix for #298
///
/// `quasi_experimental` outranks `rct`, so a paper describing itself as a
/// randomised controlled trial **and comparing itself to** quasi-experimental
/// designs was classified as quasi-experimental — the contrastive mention won
/// because the higher-priority type was consulted first. The contrastive
/// constructions are now exclusions for `quasi_experimental` (see
/// [`STUDY_TYPE_EXCLUSIONS`]), so the unguarded clean higher-tier match wins.
#[must_use]
pub fn extract_study_type(document: &std::collections::BTreeMap<String, String>) -> DimensionScore {
    let search_text = prepare_extractor_search_text(document).to_lowercase();

    for study_type in STUDY_TYPE_PRIORITY {
        let keywords = keywords_for(&DEFAULT_STUDY_TYPE_KEYWORDS, study_type);
        let exclusions = exclusions_for(&STUDY_TYPE_EXCLUSIONS, study_type);

        for keyword in keywords {
            let keyword_lower = keyword.to_lowercase();
            for keyword_pos in iter_keyword_positions(&search_text, &keyword_lower) {
                // Not gated on `exclusions` being non-empty: a type with an
                // empty word list still has to be disqualified when its keyword
                // appears inside a comparison.
                if has_exclusion_pattern(
                    &search_text,
                    &keyword_lower,
                    exclusions,
                    EXCLUSION_CONTEXT_WINDOW,
                    Some(keyword_pos),
                ) {
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
#[must_use]
pub fn extract_sample_size_dimension(
    document: &std::collections::BTreeMap<String, String>,
) -> DimensionScore {
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
            None,
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
