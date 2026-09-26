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
//! `quality_oracle` is the broad instrument (76 cases diffed against Python,
//! 13 marked as deliberate corrections). This file is the reasoned half: the
//! regression suites for #294, #297 and #298, and the properties a reader
//! needs stated.

use std::collections::BTreeMap;

use bmlib::quality::extractors::{
    extract_sample_size_dimension, extract_study_type, find_sample_size, get_extracted_sample_size,
    get_extracted_study_type, has_ci_reporting, has_power_calculation, is_negated,
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
// Issue #294 — a digit-grouped sample size
// ---------------------------------------------------------------------------

/// Python's patterns capture `(\d+)`, which cannot span a comma. Which
/// fragment you get depends on which side the pattern anchors:
///
/// ```text
/// "A total of 12,345 patients"  -> 345    (trailing-anchored: last run)
/// "n = 12,345"                  -> 12     (leading-anchored: first run)
/// ```
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

/// The severe half: a fragment below `min_n` reads as *absent*, so a
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

// ---------------------------------------------------------------------------
// Issue #297 — negation-blind bonuses
// ---------------------------------------------------------------------------

/// A denial is phrased with the negation before the keyword, after it, or
/// between the keyword and its verb. All three must be recognised.
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

/// The whole dimension, which is the defect's actual cost: Python scored this
/// 7.10 and **recorded the opposite of the source** in the audit trail.
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

/// Negation matching is whole-word, so a word merely *containing* a negation
/// token does not silence a real claim.
#[test]
fn a_word_containing_a_negation_token_is_not_a_negation() {
    assert!(!is_negated("nothing to report here", 20, 40));
    assert!(has_power_calculation(
        "Notably, a power calculation was done."
    ));
    assert!(has_ci_reporting(
        "Notably, the confidence interval was wide."
    ));
}

/// A word boundary matters: `CI` inside `acid` is not a confidence interval.
#[test]
fn the_abbreviation_needs_a_word_boundary() {
    assert!(!has_ci_reporting("The acid was strong."));
    assert!(!has_ci_reporting("Efficacy was measured."));
}

/// Integer bracket pairs and year ranges are not confidence intervals — the
/// decimal-point requirement is what keeps them out.
#[test]
fn a_bare_numeric_range_is_not_a_confidence_interval() {
    assert!(!has_ci_reporting("Effect 1.25 [12, 15]."));
    assert!(!has_ci_reporting("Published (2010-2015)."));
}

// ---------------------------------------------------------------------------
// Issue #298 — priority over evidence
// ---------------------------------------------------------------------------

/// `quasi_experimental` outranks `rct`, so a paper that describes itself as a
/// randomised controlled trial and *compares itself to* quasi-experimental
/// designs was classified as quasi-experimental: the contrastive mention won
/// because the higher-priority type was consulted first.
#[test]
fn a_contrastive_mention_does_not_outrank_the_papers_own_description() {
    for text in [
        "This was a randomized controlled trial. In contrast to quasi-experimental \
         designs, treatment was randomised.",
        "This randomized controlled trial, compared with quasi-experimental studies, \
         was larger.",
    ] {
        assert_eq!(study_of(text), "rct", "should be rct: {text:?}");
    }
}

/// A construction whose contrast subject is genuinely *indeterminate* is out
/// of scope, and saying so is more honest than widening a word list until it
/// agrees.
///
/// `"A randomized controlled trial, unlike quasi-experimental studies,
/// allocated treatment randomly."` is ungrammatical-by-omission: the commas
/// make it unclear whether the paper is an RCT contrasting itself with
/// quasi-experimental work, or a quasi-experimental study contrasting itself
/// with RCTs. Both readings are defensible, a keyword system cannot resolve
/// them, and the port does not claim to. What it does claim is the two forms
/// above, where the clause structure fixes the subject.
#[test]
fn a_contrast_with_an_indeterminate_subject_is_out_of_scope() {
    let got = study_of(
        "A randomized controlled trial, unlike quasi-experimental studies, \
         allocated treatment randomly.",
    );
    assert_ne!(
        got, "unknown",
        "it must still classify *something*, and it does: {got}"
    );
}

/// The contrastive marker may sit *after* the mention, which is why the scan
/// has to look forwards as well as backwards.
#[test]
fn the_contrastive_marker_is_found_on_either_side() {
    // Marker first.
    assert_eq!(
        study_of(
            "In contrast to quasi-experimental studies, this was a randomized controlled trial."
        ),
        "rct"
    );
    // Marker last.
    assert_eq!(
        study_of("This randomized controlled trial was larger, compared with quasi-experimental studies."),
        "rct"
    );
}

/// A genuine quasi-experimental study must still be classified as one — the
/// fix must not suppress the type.
#[test]
fn a_genuine_quasi_experimental_study_is_still_recognised() {
    for text in [
        "This was a quasi-experimental study.",
        "We conducted a quasi experimental study of 40 patients.",
        "A non-randomized trial was performed.",
    ] {
        assert_eq!(study_of(text), "quasi_experimental", "{text:?}");
    }
}

/// The RCT exclusion list must keep working: the reason `quasi_experimental`
/// precedes `rct` is so "non-randomized trial" is not read as randomised.
#[test]
fn a_negated_randomised_trial_is_not_read_as_an_rct() {
    assert_ne!(
        study_of("This was a non-randomized trial of 60 patients."),
        "rct"
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
