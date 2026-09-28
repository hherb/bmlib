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

//! Rule-based quality extractors — the named tests.
//!
//! `quality_oracle` is the broad instrument (575 cases diffed against live
//! Python, all strict). This file is the reasoned half: the regression suites
//! for #294, #297 and #298 and for the window defect #366 found, plus the
//! properties a reader needs stated.
//!
//! **Every behaviour asserted here was measured against Python**, either by a
//! case that is in the oracle corpus or by a direct probe recorded in the
//! comment above the assertion. A named test that encoded a translator's
//! reading would be exactly the failure mode this file exists to avoid.

use std::collections::BTreeMap;

use bmlib::quality::extractors::{
    extract_sample_size_dimension, extract_study_type, find_sample_size, get_extracted_sample_size,
    get_extracted_study_type, has_ci_reporting, has_power_calculation, is_denied, parse_number,
};
use bmlib::quality::scoring_models::DIMENSION_SAMPLE_SIZE;

fn doc(text: &str) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    m.insert("full_text".to_string(), text.to_string());
    m
}

fn study_of(text: &str) -> String {
    get_extracted_study_type(&extract_study_type(&doc(text))).unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Issue #294 — a digit-grouped sample size (Python adopted the fix)
// ---------------------------------------------------------------------------

/// Python's patterns captured `(\d+)`, which cannot span a comma. Which
/// fragment you got depended on which side the pattern anchored:
///
/// ```text
/// "A total of 12,345 patients"  -> 345    (trailing-anchored: last run)
/// "n = 12,345"                  -> 12     (leading-anchored: first run)
/// ```
///
/// Python adopted the port's `_COUNT` in its extractor audit, and the six
/// `294/*` corpus cases now diff strictly.
#[test]
fn a_thousands_separator_does_not_split_the_number() {
    assert_eq!(
        find_sample_size("A total of 12,345 patients were enrolled.", 5, 1_000_000),
        Some(12_345)
    );
    assert_eq!(find_sample_size("n = 12,345", 5, 1_000_000), Some(12_345));
    assert_eq!(
        find_sample_size("1,234 participants", 5, 1_000_000),
        Some(1_234)
    );
    assert_eq!(
        find_sample_size("The study enrolled 1,234 patients.", 5, 1_000_000),
        Some(1_234)
    );
}

/// The severe half: a fragment below `min_n` read as *absent*, so a
/// million-patient study scored **0.0**.
#[test]
fn a_grouped_number_whose_fragment_is_too_small_is_still_found() {
    assert_eq!(
        find_sample_size("n = 1,000,000 participants", 5, 1_000_000),
        Some(1_000_000)
    );
    assert_eq!(
        find_sample_size("The trial recruited 10,000 subjects", 5, 1_000_000),
        Some(10_000)
    );

    let d = extract_sample_size_dimension(&doc("n = 1,000,000 participants"));
    assert_eq!(get_extracted_sample_size(&d), Some(1_000_000));
    assert_eq!(d.score, 10.0, "a million patients is the top of the scale");
    assert_eq!(d.dimension_name, DIMENSION_SAMPLE_SIZE);
}

/// The ungrouped forms must be untouched by the fix.
#[test]
fn an_ungrouped_number_still_reads_as_itself() {
    assert_eq!(
        find_sample_size("n = 12345 patients", 5, 1_000_000),
        Some(12_345)
    );
    assert_eq!(find_sample_size("n = 450", 5, 1_000_000), Some(450));
    // And a malformed grouping is not silently reinterpreted as a bigger
    // number: `1,23` is not `123`.
    assert_eq!(find_sample_size("n = 1,23", 5, 1_000_000), None);
}

/// The space family is a separator too, because that is how a deposit writes a
/// count — and a non-ASCII decimal script reads as itself, since Python's `\d`
/// is Unicode `Nd` and `int()` accepts it.
#[test]
fn the_separator_family_and_other_scripts_read_as_a_count() {
    assert_eq!(
        find_sample_size("n = 12 345 patients", 5, 1_000_000),
        Some(12_345)
    );
    assert_eq!(
        find_sample_size("n = 12\u{2009}345 patients", 5, 1_000_000),
        Some(12_345)
    );
    // Arabic-Indic ١٢٣٤٥. `char::to_digit` reads ASCII only, so the value comes
    // from the distance back to the first `Nd` in the run.
    assert_eq!(
        parse_number("\u{661}\u{662}\u{663}\u{664}\u{665}"),
        Some(12_345)
    );
    assert_eq!(
        find_sample_size(
            "n = \u{661}\u{662}\u{663}\u{664}\u{665} patients",
            5,
            1_000_000
        ),
        Some(12_345)
    );
}

// ---------------------------------------------------------------------------
// Issue #297 — the power/CI signals (Python replaced the port's fix)
// ---------------------------------------------------------------------------

/// A denial is phrased with the negation before the keyword, after it, or
/// between the keyword and its verb. All three must be recognised. This is
/// Python's narrow model, not the port's ±40-character window: the window
/// refused 16 genuine CI reports over the draw and found no real denial.
#[test]
fn a_denied_power_calculation_is_not_a_power_calculation() {
    for text in [
        "No power calculation was performed.",
        "A power calculation was not performed.",
        "There was no power analysis.",
        "The study was conducted without a power calculation.",
    ] {
        assert!(!has_power_calculation(text), "should be denied: {text:?}");
    }
    for text in [
        "A power calculation was performed.",
        "Power analysis indicated 80% power.",
        "Notably, a power calculation was done.",
    ] {
        assert!(has_power_calculation(text), "should count: {text:?}");
    }
}

#[test]
fn denied_confidence_intervals_are_not_reported() {
    for text in [
        "Confidence intervals were not reported.",
        "We did not report confidence intervals.",
        "No confidence interval is given.",
    ] {
        assert!(!has_ci_reporting(text), "should be denied: {text:?}");
    }
    for text in [
        "The confidence interval was wide.",
        "The 95% CI was wide.",
        "Effect 1.25 [1.05, 1.45].",
        "Effect 1.25 (1.05-1.45).",
    ] {
        assert!(has_ci_reporting(text), "should count: {text:?}");
    }
}

/// The whole dimension, which is the signal's actual cost: the port's fix
/// scored this 4.60 rather than Python's negation-blind 7.10, and Python now
/// scores it 4.60 as well.
#[test]
fn a_text_denying_both_bonuses_earns_neither() {
    let text = "We enrolled 200 patients. No power calculation was performed \
                and confidence intervals were not reported.";
    let d = extract_sample_size_dimension(&doc(text));

    let components: Vec<&str> = d.details.iter().map(|x| x.component.as_str()).collect();
    assert_eq!(
        components,
        vec!["extracted_n"],
        "neither bonus may be recorded, but got {components:?}"
    );
    assert!(
        (d.score - 4.602_059_991_327_962).abs() < 1e-9,
        "the truthful base score, not 7.10: {}",
        d.score
    );
    for detail in &d.details {
        let reasoning = detail.reasoning.clone().unwrap_or_default();
        assert!(
            !reasoning.contains("mentioned") && !reasoning.contains("reported"),
            "the audit trail must not assert what the text denies: {reasoning}"
        );
    }
}

/// **The denial's reach is bounded on purpose, and both bounds are measured.**
///
/// A blank line separates table cells, and a preposition attaches the mention
/// to the noun the negation governs — so `"95% CI"` with `"Not reported"` in
/// the next cell, and `"no overlap between the 95% CI"`, are *reports*, not
/// denials. `"by"`, `"on"` and `"using"` are **not** among the prepositions, so
/// `"not predetermined by a power calculation"` *is* a denial; and `"not only"`
/// is not a negation.
#[test]
fn the_denial_does_not_reach_across_a_blank_line_or_a_preposition() {
    assert!(has_ci_reporting("95% CI\n\nNot reported"));
    assert!(has_ci_reporting("There was no overlap between the 95% CI."));
    assert!(has_ci_reporting(
        "We not only reported confidence intervals."
    ));
    // "by" does not end the reach, which is Python's documented reading and
    // the opposite of what a widened preposition list would give.
    assert!(!has_power_calculation(
        "The sample size was not predetermined by a power calculation."
    ));
    assert!(!has_ci_reporting(
        "We did not report 95% confidence intervals."
    ));
}

/// A mention that states its own interval cannot be denied: a table row's
/// label beside a reported range reads as a denial otherwise.
#[test]
fn a_stated_interval_survives_a_denial() {
    assert!(has_ci_reporting("No difference 95% CI 0.9-1.5."));
}

/// Only a power a study would *set* counts, and only when the word before it
/// does not make it a test's or a laser's.
#[test]
fn a_quantified_power_must_be_a_study_power() {
    assert!(has_power_calculation("The study had 80% power."));
    assert!(has_power_calculation("A power of 0.80 was assumed."));
    assert!(has_power_calculation("Power 91.82%"));
    // Below 50% is not a calculation's target; 100% is not one either.
    assert!(!has_power_calculation("Mean power of 1.0% was observed."));
    assert!(!has_power_calculation("Power of 1.0 was assumed."));
    // A test's power, and a laser's.
    assert!(!has_power_calculation(
        "The predictive power of 88% was high."
    ));
    assert!(!has_power_calculation(
        "The laser power of 0.6 mW was measured."
    ));
    // The two phrases that *discuss* power are not the calculation itself.
    assert!(!has_power_calculation(
        "The study had low statistical power."
    ));
}

/// Negation matching is whole-word, so a word merely *containing* a negation
/// token does not silence a real claim: `not` inside `"notably"`, and the word
/// `"nothing"` cut by a window boundary.
#[test]
fn a_word_containing_a_negation_token_is_not_a_negation() {
    // "nothing" ends at the mention, and `_DENIED_BEFORE` anchors on `\bno`,
    // which "nothing" does not provide.
    assert!(!is_denied("nothing", 7, 7));
    assert!(has_power_calculation(
        "Notably, a power calculation was done."
    ));
    assert!(has_ci_reporting(
        "Notably, the confidence interval was wide."
    ));
}

/// A word boundary matters: `CI` inside `acid` is not a confidence interval,
/// and `"cis"` is not one either.
#[test]
fn the_abbreviation_needs_a_word_boundary() {
    assert!(!has_ci_reporting("The acid was strong."));
    assert!(!has_ci_reporting("Efficacy was measured."));
    assert!(!has_ci_reporting("The cis-9 fatty acid was measured."));
    assert!(!has_ci_reporting("Activity was 12 Ci/mmol."));
    // A bare number is not an interval: "CI-994" is a drug and "CI of 2.4
    // L/min" is a cardiac index.
    assert!(!has_ci_reporting("Patients received CI-994."));
    assert!(!has_ci_reporting("The CI of 2.4 L/min was low."));
}

/// Integer bracket pairs and year ranges are not confidence intervals — the
/// decimal-point requirement is what keeps them out.
#[test]
fn a_bare_numeric_range_is_not_a_confidence_interval() {
    assert!(!has_ci_reporting("Effect 1.25 [12, 15]."));
    assert!(!has_ci_reporting("Published (2010-2015)."));
}

// ---------------------------------------------------------------------------
// Issue #366 — the exclusion window ends after the keyword
// ---------------------------------------------------------------------------

/// **The defect the draw found in the port, and Python's window is the fix.**
///
/// `has_exclusion_pattern` scanned `text[start..keyword_pos]`, which *ends
/// before* the keyword, where Python scans `text[start_pos : keyword_pos +
/// len(keyword)]`, which *includes* it. `randomised controlled trial` is found
/// *inside* `non-randomised controlled trial` (the hyphen is a word boundary),
/// so the exclusion that has to fire is the one containing the keyword itself.
/// Over the draw, 27 `Controlled Clinical Trial` abstracts moved `unknown` →
/// `rct` — the design the paper says it is not.
#[test]
fn the_exclusion_window_contains_the_keyword() {
    for text in [
        "We performed a non-randomized controlled trial.",
        "A prospective, non-randomised controlled trial was conducted.",
    ] {
        assert_eq!(study_of(text), "unknown", "{text:?}");
    }
    // Without the hyphen there is no inner match at all, so this one was
    // already right — it pins the *boundary* rather than the window.
    assert_eq!(
        study_of("This was a nonrandomized controlled trial of 40 patients."),
        "unknown"
    );
}

// ---------------------------------------------------------------------------
// Issue #298 — priority over evidence (Python refused the port's fix)
// ---------------------------------------------------------------------------

/// **Python measured the port's contrastive veto and refused it.** The port
/// vetoed a higher-priority type's mention when it sat in a contrastive clause.
/// On the draw that moved 55 study-type answers and none for the better, and
/// the shape it was written for occurs in 0 of 914 RCT abstracts. Python keeps
/// the priority order and so does this module: a contrastive mention of a
/// higher-priority type wins.
#[test]
fn a_contrastive_mention_of_a_higher_priority_type_wins() {
    for text in [
        "This was a randomized controlled trial. In contrast to quasi-experimental \
         designs, treatment was randomised.",
        "This randomized controlled trial, compared with quasi-experimental studies, \
         was larger.",
    ] {
        assert_eq!(
            study_of(text),
            "quasi_experimental",
            "#298's own shape, and Python's measured answer: {text:?}"
        );
    }
}

/// A genuine quasi-experimental study must still be classified as one, and the
/// RCT exclusion list must keep working: the reason `quasi_experimental`
/// precedes `rct` is so "non-randomized trial" is not read as randomised.
#[test]
fn a_genuine_quasi_experimental_study_is_still_recognised() {
    for text in [
        "This was a quasi-experimental study.",
        "We conducted a quasi experimental study of 40 patients.",
        "A non-randomized trial was performed.",
    ] {
        assert_eq!(study_of(text), "quasi_experimental", "{text:?}");
    }
    assert_eq!(
        study_of("This was a non-randomised controlled trial."),
        "unknown"
    );
}

/// Priority order otherwise holds: a systematic review outranks the trials it
/// reviews.
#[test]
fn priority_order_still_holds_where_evidence_agrees() {
    assert_eq!(
        study_of("A systematic review of randomized controlled trials."),
        "systematic_review"
    );
    assert_eq!(study_of("A meta-analysis was performed."), "meta_analysis");
    assert_eq!(study_of("We present a case report."), "case_report");
    assert_eq!(study_of("We looked at some things."), "unknown");
}

/// Whole-word keyword matching: `RCT` matches `RCTs` but not `infarct`.
#[test]
fn keywords_match_on_word_boundaries() {
    assert_ne!(study_of("The infarct was large."), "rct");
    assert_eq!(study_of("Two RCTs were included."), "rct");
}

// ---------------------------------------------------------------------------
// The audit trail's own text
// ---------------------------------------------------------------------------

/// **The sample-size search text is not lower-cased**, where `extract_study_type`
/// lower-cases its own. Python passes `prepare_extractor_search_text(document)`
/// straight to the signal readers, so an excerpt keeps the paper's
/// capitalisation; the port lower-cased it, which moved the stored evidence for
/// every abstract that reports a power calculation or a CI.
#[test]
fn the_sample_size_evidence_keeps_the_papers_case() {
    let d = extract_sample_size_dimension(&doc(
        "The study enrolled 450 patients. A power calculation was done and the 95% CI was narrow.",
    ));
    let power = d
        .details
        .iter()
        .find(|x| x.component == "power_calculation")
        .expect("the power bonus is credited");
    let evidence = power.evidence_text.clone().unwrap_or_default();
    assert!(
        evidence.contains("The study enrolled"),
        "the excerpt must keep the paper's capitalisation: {evidence:?}"
    );

    let ci = d
        .details
        .iter()
        .find(|x| x.component == "ci_reporting")
        .expect("the CI bonus is credited");
    assert!(
        ci.evidence_text
            .as_deref()
            .unwrap_or_default()
            .contains("CI"),
        "the CI bonus records its own mention's excerpt: {:?}",
        ci.evidence_text
    );
}

/// The study-type excerpt *is* lower-cased, because Python lower-cases the
/// search text before it scans for keywords.
#[test]
fn the_study_type_evidence_is_lower_cased() {
    let d = extract_study_type(&doc("This was a RANDOMIZED CONTROLLED TRIAL."));
    let evidence = d.details[0].evidence_text.clone().unwrap_or_default();
    assert_eq!(evidence, "this was a randomized controlled trial.");
}
