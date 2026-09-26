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

//! Tier 1 and the tiering rule — the oracle and the named tests.
//!
//! The corpus carries Python's **tables as data**, so the port's transcription is
//! compared key by key rather than trusted, and 27 classification cases diff the
//! priority walk. The named tests state why the omissions and the order matter.

use bmlib::quality::manager::{
    enrich_with_cochrane, metadata_is_confident, next_tier, TierStep, METADATA_ACCEPTANCE_THRESHOLD,
};
use bmlib::quality::metadata_filter::{
    classify_from_metadata, design_for_type, normalize_type, METADATA_HIGH_CONFIDENCE,
    PUBMED_TYPE_TO_DESIGN, TYPE_PRIORITY,
};
use bmlib::quality::{QualityAssessment, QualityFilter, StudyDesign};
use serde_json::Value;

const EXPECTED: &str = include_str!("data/tiering_expected.json");

/// A nine-domain all-low risk-of-bias record, as a model would return one.
///
/// Every domain is required by `CochraneRiskOfBias::from_json`, so the fixture
/// states all nine rather than an empty object — which that reader refuses.
fn all_low_risk() -> serde_json::Value {
    serde_json::from_str(
        r#"{"random_sequence_generation": {"domain": "Random sequence generation", "bias_type": "selection bias", "judgement": "Low risk", "support_for_judgement": "x"}, "allocation_concealment": {"domain": "Allocation concealment", "bias_type": "selection bias", "judgement": "Low risk", "support_for_judgement": "x"}, "baseline_outcome_measurements": {"domain": "Baseline outcome measurements", "bias_type": "other bias", "judgement": "Low risk", "support_for_judgement": "x"}, "baseline_characteristics": {"domain": "Baseline characteristics", "bias_type": "other bias", "judgement": "Low risk", "support_for_judgement": "x"}, "blinding_participants_personnel": {"domain": "Blinding of participants and personnel", "bias_type": "performance bias", "judgement": "Low risk", "support_for_judgement": "x"}, "blinding_outcome_assessment_subjective": {"domain": "Blinding of outcome assessment (subjective)", "bias_type": "detection bias", "judgement": "Low risk", "support_for_judgement": "x"}, "blinding_outcome_assessment_objective": {"domain": "Blinding of outcome assessment (objective)", "bias_type": "detection bias", "judgement": "Low risk", "support_for_judgement": "x"}, "incomplete_outcome_data": {"domain": "Incomplete outcome data", "bias_type": "attrition bias", "judgement": "Low risk", "support_for_judgement": "x"}, "selective_reporting": {"domain": "Selective reporting", "bias_type": "reporting bias", "judgement": "Low risk", "support_for_judgement": "x"}}"#,
    )
    .expect("the fixture parses")
}

fn expectations() -> Value {
    serde_json::from_str(EXPECTED).expect("expected parse")
}

fn classify(types: &[&str]) -> QualityAssessment {
    let owned: Vec<String> = types.iter().map(|s| (*s).to_string()).collect();
    classify_from_metadata(&owned)
}

/// Render an assessment the way the oracle does.
fn render(assessment: &QualityAssessment) -> Value {
    serde_json::json!({
        "study_design": assessment.study_design.member_name(),
        "quality_tier": assessment.quality_tier.value(),
        "quality_score": assessment.quality_score,
        "confidence": assessment.confidence,
        "assessment_tier": assessment.assessment_tier,
        "extraction_method": assessment.extraction_method,
        "is_randomized": assessment.is_randomized,
    })
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let expected = expectations();
    let cases: Value =
        serde_json::from_str(include_str!("data/tiering_cases.json")).expect("cases parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected["cases"].as_array().expect("cases");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let types: Vec<&str> = case["args"]["publication_types"]
            .as_array()
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let got = render(&classify(&types));
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// **The port's tables are Python's tables**, compared as data: every key, every
/// design and the priority order. A hand-transcribed table drifts, and a drifted
/// table classifies papers differently while every case still passes.
#[test]
fn the_tables_are_pythons_tables() {
    let expected = expectations();
    let tables = &expected["tables"];

    for (key, design) in tables["type_to_design"]
        .as_array()
        .expect("rows")
        .iter()
        .map(|row| {
            (
                row[0].as_str().expect("key"),
                row[1].as_str().expect("design"),
            )
        })
    {
        let ours = design_for_type(key)
            .unwrap_or_else(|| panic!("the port is missing {key:?}"))
            .member_name();
        assert_eq!(ours, design, "design for {key:?}");
    }
    assert_eq!(
        PUBMED_TYPE_TO_DESIGN.len(),
        tables["type_to_design"].as_array().expect("rows").len(),
        "the port must not carry an extra row"
    );

    let priority: Vec<&str> = tables["type_priority"]
        .as_array()
        .expect("priority")
        .iter()
        .map(|v| v.as_str().expect("entry"))
        .collect();
    assert_eq!(
        TYPE_PRIORITY,
        priority.as_slice(),
        "the priority order is load-bearing, not incidental"
    );

    // And the confidence constants.
    assert_eq!(
        METADATA_HIGH_CONFIDENCE,
        tables["constants"]["METADATA_HIGH_CONFIDENCE"]
            .as_f64()
            .expect("confidence")
    );
    assert_eq!(
        METADATA_ACCEPTANCE_THRESHOLD,
        tables["constants"]["METADATA_ACCEPTANCE_THRESHOLD"]
            .as_f64()
            .expect("threshold")
    );
}

// ---------------------------------------------------------------------------
// The mapping's omissions
// ---------------------------------------------------------------------------

/// **Three types are deliberately absent**, and their absence is the rule:
///
/// * `Multicenter Study` and `Comparative Study` are organisational or generic
///   attributes, not designs;
/// * `Observational Study` is PubMed's catch-all, so mapping it to a specific
///   design would assert a prospectivity and a tier the evidence does not
///   support — **at high confidence**.
///
/// A record carrying only such tags falls through to a deeper tier, which is the
/// point: an unclassified answer is honest, and a fabricated design at 0.9
/// confidence is not.
#[test]
fn the_unmapped_types_stay_unmapped() {
    for unmapped in [
        "Observational Study",
        "Multicenter Study",
        "Comparative Study",
    ] {
        let assessment = classify(&[unmapped]);
        assert_eq!(
            assessment.assessment_tier, 0,
            "{unmapped:?} must not be classified"
        );
        assert_eq!(assessment.confidence, 0.0);
        assert!(design_for_type(unmapped).is_none(), "{unmapped:?}");
    }
    // But a mapped tag beside an unmapped one still classifies.
    let assessment = classify(&["Multicenter Study", "Cohort Study"]);
    assert_eq!(assessment.study_design, StudyDesign::CohortProspective);
    assert_eq!(assessment.confidence, METADATA_HIGH_CONFIDENCE);
}

// ---------------------------------------------------------------------------
// The priority walk
// ---------------------------------------------------------------------------

/// **The priority walk resolves to the strongest design, not the first input.**
/// A paper tagged `Cohort Study` *and* `Case-Control Study` is a cohort, whichever
/// order the tags arrive in — cohort designs outrank case-control in the evidence
/// hierarchy.
#[test]
fn the_priority_walk_ignores_input_order() {
    for types in [
        ["Case-Control Study", "Cohort Study"],
        ["Cohort Study", "Case-Control Study"],
    ] {
        let assessment = classify(&types);
        assert_eq!(
            assessment.study_design,
            StudyDesign::CohortProspective,
            "{types:?}"
        );
    }
    // A systematic review outranks the meta-analysis beside it.
    assert_eq!(
        classify(&["Meta-Analysis", "Systematic Review"]).study_design,
        StudyDesign::SystematicReview
    );
    // **A priority-listed tag outranks a table-only one**, which is the walk
    // doing its job rather than reading the input in order: `Editorial` is in the
    // priority list and `Review` is not, so `Editorial` wins even though
    // `Review` is the broader design. `Review` alone is `Other`, at the lower
    // confidence.
    assert_eq!(
        classify(&["Editorial", "Review"]).study_design,
        StudyDesign::Editorial
    );
    assert_eq!(classify(&["Review"]).study_design, StudyDesign::Other);
    assert_eq!(
        classify(&["Clinical Trial", "Clinical Trial, Phase III"]).study_design,
        StudyDesign::Rct
    );
}

/// **A match outside the priority list is found too, at the lower confidence.**
/// The list is ordered by specificity, so a tag that had to fall through to the
/// general table was matched less precisely — and the port records that rather
/// than claiming the same certainty.
#[test]
fn a_non_priority_match_carries_the_lower_confidence() {
    // `Systematic Review` is in the priority list, so it takes the high
    // confidence. `Review` and `Retracted Publication` are in the table only.
    let high = classify(&["Systematic Review"]);
    assert_eq!(high.confidence, METADATA_HIGH_CONFIDENCE);
    assert_eq!(
        classify(&["Review"]).confidence,
        METADATA_HIGH_CONFIDENCE * 0.8,
        "a table-only match is weaker evidence than a priority one"
    );

    let low = classify(&["Retracted Publication"]);
    assert_eq!(low.study_design, StudyDesign::Other);
    assert_eq!(
        low.confidence,
        METADATA_HIGH_CONFIDENCE * 0.8,
        "a fall-through match is weaker evidence than a priority one"
    );
    assert!(low.confidence < high.confidence);
}

/// Matching is **case-insensitive, with hyphens and underscores folded to
/// spaces** — so `"systematic review"`, `"Systematic Review"` and
/// `"systematic-review"` all match, and so does a padded string.
#[test]
fn matching_folds_case_hyphens_and_underscores() {
    for raw in [
        "Randomized Controlled Trial",
        "randomized controlled trial",
        "RANDOMIZED CONTROLLED TRIAL",
        "  Randomized Controlled Trial  ",
    ] {
        assert_eq!(classify(&[raw]).study_design, StudyDesign::Rct, "{raw:?}");
    }
    assert_eq!(
        classify(&["meta analysis"]).study_design,
        StudyDesign::MetaAnalysis
    );
    assert_eq!(
        classify(&["systematic_review"]).study_design,
        StudyDesign::SystematicReview
    );
    assert_eq!(normalize_type("Meta-Analysis"), "meta analysis");
    assert_eq!(normalize_type("  A_B  "), "a b");
}

// ---------------------------------------------------------------------------
// The tiering rule
// ---------------------------------------------------------------------------

/// **Metadata-only is honoured before any deeper question is asked**, so the flag
/// cannot be half-honoured by a filter that also enables a deeper tier.
#[test]
fn metadata_only_short_circuits_everything() {
    let filter = QualityFilter {
        use_metadata_only: true,
        use_detailed_assessment: true,
        use_cochrane_assessment: true,
        use_llm_classification: true,
        ..QualityFilter::default()
    };
    assert_eq!(
        next_tier(&filter, &classify(&["Cohort Study"]), true, false),
        TierStep::Metadata
    );
}

/// A **confident** Tier 1 answer stands when no deeper tier was asked for — and
/// the confidence test is the whole of it, since a design was produced.
#[test]
fn a_confident_metadata_answer_stands_when_nothing_deeper_is_wanted() {
    let filter = QualityFilter::default();
    let confident = classify(&["Cohort Study"]);
    assert!(metadata_is_confident(&confident));
    assert_eq!(
        next_tier(&filter, &confident, false, false),
        TierStep::Metadata
    );

    // An unclassified Tier 1 does **not** stand: the caller left the classifier
    // enabled and got nothing from the metadata.
    let unclassified = QualityAssessment::unclassified();
    assert!(!metadata_is_confident(&unclassified));
    assert_eq!(
        next_tier(&filter, &unclassified, false, false),
        TierStep::Classification
    );
}

/// **"Supersedes" means "runs instead of, when it works"** — a failed Cochrane
/// pass falls through to the Tier 3 assessment the caller explicitly enabled,
/// rather than suppressing it.
#[test]
fn a_failed_cochrane_pass_falls_through_to_the_deep_assessment() {
    let filter = QualityFilter {
        use_cochrane_assessment: true,
        use_detailed_assessment: true,
        ..QualityFilter::default()
    };
    let confident = classify(&["Cohort Study"]);

    // Not yet attempted: the Cochrane pass is what runs.
    assert_eq!(
        next_tier(&filter, &confident, false, false),
        TierStep::Cochrane {
            needs_classification: false
        },
        "a confident Tier 1 needs no classification for the enricher"
    );
    // Attempted and unavailable: Tier 3, not silence.
    assert_eq!(
        next_tier(&filter, &confident, false, true),
        TierStep::DeepAssessment
    );
}

/// **An unconfident Tier 1 needs the cheap classifier for the Cochrane pass**,
/// and that is not an edge: a bioRxiv or medRxiv record has no PubMed publication
/// types at all, so Tier 1 is inconclusive for exactly the papers whose full text
/// makes a Cochrane pass worth paying for.
///
/// Enriching the unclassified Tier 1 result instead returned `UNKNOWN` at score
/// 0.0 and confidence 0.0 — **worse than the Tier 2 answer the caller had left
/// enabled** — with a full nine-domain bias table attached to it.
#[test]
fn an_unconfident_tier_one_asks_for_a_classification_first() {
    let filter = QualityFilter {
        use_cochrane_assessment: true,
        ..QualityFilter::default()
    };
    let unclassified = QualityAssessment::unclassified();

    assert_eq!(
        next_tier(&filter, &unclassified, false, false),
        TierStep::Cochrane {
            needs_classification: true
        }
    );

    // With the classifier disabled there is nothing to ask, so the enricher is
    // Tier 1's own answer — the caller's choice, not an accident.
    let no_classifier = QualityFilter {
        use_cochrane_assessment: true,
        use_llm_classification: false,
        ..QualityFilter::default()
    };
    assert_eq!(
        next_tier(&no_classifier, &unclassified, false, false),
        TierStep::Cochrane {
            needs_classification: false
        }
    );
}

/// **The enrichment leaves `evidence_level` and `confidence` alone**, and both
/// omissions are deliberate:
///
/// * Cochrane's `evidence_level` is free-form model text where this one is Oxford
///   CEBM, so copying it would put a different scale in a field callers compare;
/// * Cochrane's `overall_confidence` describes the model's certainty about the
///   nine bias domains, **not** about the design and score this function leaves
///   untouched — so overwriting `confidence` would let a caller's
///   `if a.confidence >= t { trust a.study_design }` discard a highly-confident
///   classification because the model was unsure about blinding.
#[test]
fn the_enrichment_leaves_the_design_and_its_confidence_alone() {
    let base = classify(&["Systematic Review"]);
    // Built from the models' own deserialisers rather than a `Default`, which
    // neither has: an assessment's parts are required, and a blank one would be
    // a shape the source cannot produce.
    let cochrane = bmlib::quality::CochraneStudyAssessment::new(
        bmlib::quality::CochraneStudyCharacteristics::new(
            "S1",
            "methods",
            bmlib::quality::CochraneParticipants::from_json(&serde_json::json!({})),
            bmlib::quality::CochraneInterventions::from_json(&serde_json::json!({})),
            bmlib::quality::CochraneOutcomes::from_json(&serde_json::json!({})),
            bmlib::quality::CochraneNotes::from_json(&serde_json::json!({})),
        ),
        // Every domain is required, so an empty object is refused — the
        // fixture states all nine, as the source can produce.
        bmlib::quality::cochrane_models::CochraneRiskOfBias::from_json(&all_low_risk())
            .expect("a full nine-domain record"),
    );
    // A **low** overall confidence and a free-form evidence level: writing either
    // into the enriched result is what the test below must catch. Set directly
    // because the model has no builder, and both fields are optional in the
    // source.
    let mut cochrane = cochrane;
    cochrane.overall_confidence = Some(0.2);
    cochrane.evidence_level = Some("moderate certainty".to_string());
    cochrane.overall_quality_score = Some(3.0);
    let enriched = enrich_with_cochrane(&base, &cochrane);

    assert_eq!(enriched.assessment_tier, 4);
    assert_eq!(enriched.extraction_method, "llm_cochrane_assessment");
    assert_eq!(
        enriched.study_design, base.study_design,
        "a Cochrane assessment carries no design, so the base's stands"
    );
    assert_eq!(enriched.quality_score, base.quality_score);
    assert_eq!(enriched.quality_tier, base.quality_tier);
    assert_eq!(
        enriched.confidence, base.confidence,
        "the model's certainty about the **bias domains** (0.2) must not replace \
         the classification's own confidence ({}) — a caller's `confidence >= t` \
         guard would then discard a confident classification because the model \
         was unsure about blinding",
        base.confidence
    );
    assert_eq!(enriched.confidence, METADATA_HIGH_CONFIDENCE);
    assert_eq!(
        enriched.evidence_level, base.evidence_level,
        "Cochrane's free-form evidence level must not replace the Oxford CEBM one"
    );
    assert_eq!(
        enriched.quality_score, base.quality_score,
        "Cochrane's own 0–10 aggregate is a different scale"
    );
    assert!(enriched.cochrane_assessment.is_some());
    // The detail line is appended, not replaced.
    assert!(enriched
        .extraction_details
        .iter()
        .any(|d| d.contains("Cochrane")));
    // And the base is untouched.
    assert_eq!(base.assessment_tier, 1);
    assert!(base.cochrane_assessment.is_none());
}

/// **A default filter reaches Tier 2**, which a derived `Default` did not: it set
/// every flag `false`, so `QualityFilter::default()` meant "Tier 1 only" while
/// reading as "the defaults" — a plausible default answering a different question
/// than the caller asked.
#[test]
fn a_default_filter_reaches_tier_two() {
    let filter = QualityFilter::default();
    assert!(
        filter.use_llm_classification,
        "Python's constructor makes this true, and a caller relying on the \
         defaults must get the classifier"
    );
    assert!(!filter.use_metadata_only);
    assert!(!filter.use_detailed_assessment);
    assert!(!filter.use_cochrane_assessment);

    // The observable consequence: an unclassified paper is classified rather
    // than returned as Tier 1's empty answer.
    assert_eq!(
        next_tier(&filter, &QualityAssessment::unclassified(), false, false),
        TierStep::Classification
    );
}
