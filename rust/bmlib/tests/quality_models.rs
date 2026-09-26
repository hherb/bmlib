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

//! Quality data models — the named tests.
//!
//! `model_oracle` diffs 53 cases against Python. This file states the
//! properties those cases cannot: why the ordering matters, which omissions
//! are deliberate, and the two asymmetries in `passes_filter` that a
//! reasonable implementation gets wrong.

use bmlib::quality::data_models::{
    design_to_randomized, design_to_score, design_to_tier, study_design_from_str, BiasRisk,
    QualityAssessment, QualityFilter, QualityTier, StudyDesign,
};
use serde_json::json;

// ---------------------------------------------------------------------------
// The evidence order is the point of QualityTier
// ---------------------------------------------------------------------------

/// **Higher value = stronger evidence**, and the variant order *is* that
/// order. A variant inserted in the wrong place would silently reorder the
/// hierarchy that `min_tier` filters against.
#[test]
fn the_evidence_order_is_the_variant_order() {
    let by_value: Vec<i64> = QualityTier::ALL.iter().map(|t| t.value()).collect();
    assert_eq!(by_value, vec![0, 1, 2, 3, 4, 5]);

    let mut sorted = QualityTier::ALL.to_vec();
    sorted.sort();
    assert_eq!(
        sorted,
        QualityTier::ALL,
        "sorting must reproduce the declaration order"
    );

    assert!(QualityTier::Tier5Synthesis > QualityTier::Tier4Experimental);
    assert!(QualityTier::Tier4Experimental > QualityTier::Tier1Anecdotal);
    assert!(QualityTier::Unclassified < QualityTier::Tier1Anecdotal);
}

/// Round-tripping a tier through its integer value is lossless, and an
/// unknown integer is refused rather than defaulted — Python's
/// `QualityTier(value)` raises, and a port that defaulted would silently
/// downgrade a record.
#[test]
fn a_tier_round_trips_and_an_unknown_integer_is_refused() {
    for tier in QualityTier::ALL {
        assert_eq!(QualityTier::from_value(tier.value()), Ok(tier));
    }
    assert!(QualityTier::from_value(6).is_err());
    assert!(QualityTier::from_value(-1).is_err());
}

/// Every design maps — Python asserts this too, and a missing row would put a
/// study at the bottom of the hierarchy by accident.
#[test]
fn every_design_has_a_tier_and_a_score() {
    for design in StudyDesign::ALL {
        let _ = design_to_tier(design);
        let _ = design_to_score(design);
        // Only the randomized map is deliberately partial.
        let _ = design_to_randomized(design);
    }
}

// ---------------------------------------------------------------------------
// The randomization map is partial on purpose
// ---------------------------------------------------------------------------

/// `None` is a *third* answer, not a missing one: a systematic review may
/// synthesise randomised or observational work, so the design alone does not
/// determine randomisation. Filling these in with `false` would make a review
/// indistinguishable from a cohort study under `require_randomization`.
#[test]
fn a_synthesis_does_not_claim_to_be_non_randomized() {
    for design in [
        StudyDesign::SystematicReview,
        StudyDesign::MetaAnalysis,
        StudyDesign::Guideline,
    ] {
        assert_eq!(
            design_to_randomized(design),
            None,
            "{design} must be undetermined, not false"
        );
    }
    assert_eq!(design_to_randomized(StudyDesign::Rct), Some(true));
    assert_eq!(
        design_to_randomized(StudyDesign::CohortProspective),
        Some(false)
    );
}

// ---------------------------------------------------------------------------
// Design-name lookup is exact, and that is deliberate
// ---------------------------------------------------------------------------

/// Mixed-case and padded spellings fall through to `Unknown` rather than being
/// helpfully normalised. The oracle caught a first cut that trimmed and
/// lowercased here: it classified `"RCT"` where Python declines, which reports
/// a study design Python never claimed.
#[test]
fn the_design_lookup_is_exact_and_not_helpful() {
    assert_eq!(study_design_from_str("rct"), StudyDesign::Rct);
    assert_eq!(
        study_design_from_str("randomized controlled trial"),
        StudyDesign::Rct
    );
    for unhelpful in ["RCT", "Rct", "  rct  ", "Systematic Review", ""] {
        assert_eq!(
            study_design_from_str(unhelpful),
            StudyDesign::Unknown,
            "{unhelpful:?} must not be normalised"
        );
    }
}

// ---------------------------------------------------------------------------
// BiasRisk tolerates malformed input one domain at a time
// ---------------------------------------------------------------------------

/// A domain holds `low`/`unclear`/`high` or nothing. Anything else — a typo, a
/// `null`, a number, an empty string, a wrong case — becomes `unclear`, so a
/// malformed payload degrades one domain rather than the assessment.
#[test]
fn an_unrecognised_bias_domain_becomes_unclear() {
    let br = BiasRisk::from_json(&json!({
        "selection": "low",
        "performance": "invalid",
        "detection": null,
        "attrition": 7,
        "reporting": "HIGH",
    }));
    assert_eq!(br.selection, "low");
    assert_eq!(br.performance, "unclear");
    assert_eq!(br.detection, "unclear");
    assert_eq!(br.attrition, "unclear");
    assert_eq!(
        br.reporting, "unclear",
        "the domain values are case-sensitive"
    );
}

/// An empty object yields the all-`unclear` default, so a missing key and an
/// unreadable one agree.
#[test]
fn absent_domains_and_unreadable_ones_agree() {
    assert_eq!(BiasRisk::from_json(&json!({})), BiasRisk::default());
    assert_eq!(BiasRisk::default().selection, "unclear");
}

// ---------------------------------------------------------------------------
// passes_filter — the two asymmetries
// ---------------------------------------------------------------------------

/// **An unknown sample size is not a small one.** The clause is skipped when
/// the assessment records no size, so `min_sample_size` cannot reject a paper
/// whose size simply was not extracted.
#[test]
fn an_unknown_sample_size_does_not_fail_a_minimum() {
    let filter = QualityFilter {
        min_sample_size: Some(100),
        ..QualityFilter::defaults()
    };

    let mut unknown = QualityAssessment::from_metadata(StudyDesign::Rct, 0.9);
    unknown.sample_size = None;
    assert!(unknown.passes_filter(&filter));

    let mut small = unknown.clone();
    small.sample_size = Some(50);
    assert!(
        !small.passes_filter(&filter),
        "a known-small size must fail"
    );

    let mut big = unknown;
    big.sample_size = Some(150);
    assert!(big.passes_filter(&filter));
}

/// **An undetermined design has not shown itself randomised.** `require_randomization`
/// rejects `None` as well as `false`, so a systematic review does not pass a
/// randomisation filter just because its design does not rule randomisation out.
#[test]
fn an_undetermined_design_fails_a_randomisation_filter() {
    let filter = QualityFilter {
        require_randomization: true,
        ..QualityFilter::defaults()
    };
    assert!(QualityAssessment::from_metadata(StudyDesign::Rct, 0.9).passes_filter(&filter));
    assert!(
        !QualityAssessment::from_metadata(StudyDesign::CohortProspective, 0.9)
            .passes_filter(&filter)
    );
    assert!(
        !QualityAssessment::from_metadata(StudyDesign::SystematicReview, 0.9)
            .passes_filter(&filter),
        "None is not a pass"
    );
}

/// `require_blinding` rejects both "not recorded" and an explicit "none" —
/// the two readings whose consequence is the same.
#[test]
fn blinding_is_required_to_be_present_and_not_none() {
    let filter = QualityFilter {
        require_blinding: true,
        ..QualityFilter::defaults()
    };
    let mut a = QualityAssessment::from_metadata(StudyDesign::Rct, 0.9);

    a.is_blinded = None;
    assert!(!a.passes_filter(&filter));

    a.is_blinded = Some("none".to_string());
    assert!(!a.passes_filter(&filter));

    a.is_blinded = Some("double".to_string());
    assert!(a.passes_filter(&filter));
}

/// The pipeline default runs Tier 2 and nothing else — not obvious from the
/// field names, and a port that defaulted everything to `false` would run no
/// assessment at all.
#[test]
fn the_default_filter_runs_the_classifier_only() {
    let f = QualityFilter::defaults();
    assert!(f.use_llm_classification);
    assert!(!f.use_metadata_only);
    assert!(!f.use_detailed_assessment);
    assert!(!f.use_cochrane_assessment);
    assert_eq!(f.min_tier, None);
}

// ---------------------------------------------------------------------------
// Serialisation shape
// ---------------------------------------------------------------------------

/// `to_json` omits four fields Python also omits, and includes the two
/// conditional ones only when set. The omissions are load-bearing: a round
/// trip through JSON drops `extraction_details` and the original tier.
#[test]
fn to_json_matches_the_documented_shape() {
    let a = QualityAssessment::from_metadata(StudyDesign::Rct, 0.9);
    let d = a.to_json();
    let obj = d.as_object().expect("object");

    for absent in [
        "original_quality_tier",
        "transparency_result",
        "extraction_details",
        "cochrane_assessment",
        "bias_risk",
    ] {
        assert!(
            !obj.contains_key(absent),
            "{absent} must be omitted when unset"
        );
    }
    // The tier is written as its integer, the design as its string.
    assert_eq!(d["quality_tier"], json!(4));
    assert_eq!(d["study_design"], json!("rct"));
    assert_eq!(d["is_randomized"], json!(true));
    assert_eq!(d["evidence_level"], json!(null));
}

/// The `cochrane_assessment` field round-trips whatever shape it was given —
/// an object from `to_json`, or a plain dict a caller assigned back. Python
/// test `test_to_dict_does_not_crash_on_a_plain_dict_cochrane_assessment`
/// pins the second case.
#[test]
fn a_plain_dict_cochrane_assessment_passes_through() {
    let mut a = QualityAssessment::from_metadata(StudyDesign::Rct, 0.9);
    let plain = json!({"study_characteristics": {"study_id": "Andrei 2011"}});
    a.cochrane_assessment = Some(plain.clone());

    let d = a.to_json();
    assert_eq!(d["cochrane_assessment"], plain);

    let back = QualityAssessment::from_json(&d).expect("round trip");
    assert_eq!(back.cochrane_assessment, Some(plain));
}

/// A missing `cochrane_assessment` loads as `None` rather than raising on the
/// absent key — Python's own test checks both sides of this branch.
#[test]
fn an_absent_cochrane_assessment_loads_as_none() {
    let a = QualityAssessment::unclassified();
    assert!(!a
        .to_json()
        .as_object()
        .expect("object")
        .contains_key("cochrane_assessment"));
    let back = QualityAssessment::from_json(&a.to_json()).expect("round trip");
    assert_eq!(back.cochrane_assessment, None);
}

/// An unknown tier integer is refused on the way in, so a corrupt record
/// cannot be read as a weaker assessment than it was.
#[test]
fn an_unknown_tier_value_is_refused() {
    let bad = json!({"study_design": "rct", "quality_tier": 9});
    assert!(QualityAssessment::from_json(&bad).is_err());
}
