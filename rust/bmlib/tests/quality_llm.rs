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

//! Reading an LLM's quality answer — the oracle and the named tests.
//!
//! The corpus (45 cases) diffs the two parsers against Python's. Seven carry a
//! `corrected` block: those are defect #295, where the Python raised on a key
//! present with `null` and this port reads "not answered".

use bmlib::quality::data_models::{design_to_tier, BiasRisk, QualityAssessment};
use bmlib::quality::llm_parsers::{parse_assessment, parse_classification};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/quality_llm_cases.json");
const EXPECTED: &str = include_str!("data/quality_llm_expected.json");

/// Render an assessment the way the oracle does, so the two are comparable.
fn render(assessment: &QualityAssessment) -> Value {
    json!({
        "assessment_tier": assessment.assessment_tier,
        "extraction_method": assessment.extraction_method,
        "study_design": assessment.study_design.member_name(),
        "quality_tier": assessment.quality_tier.value(),
        "quality_score": assessment.quality_score,
        "evidence_level": assessment.evidence_level,
        "is_randomized": assessment.is_randomized,
        "is_controlled": assessment.is_controlled,
        "is_blinded": assessment.is_blinded,
        "is_prospective": assessment.is_prospective,
        "is_multicenter": assessment.is_multicenter,
        "sample_size": assessment.sample_size,
        "confidence": assessment.confidence,
        "bias_risk": assessment.bias_risk.as_ref().map(|b| json!({
            "selection": b.selection,
            "performance": b.performance,
            "detection": b.detection,
            "attrition": b.attrition,
            "reporting": b.reporting,
        })),
        "strengths": assessment.strengths,
        "limitations": assessment.limitations,
        "extraction_details": assessment.extraction_details,
    })
}

fn run(case: &Value) -> Value {
    let data = &case["args"]["data"];
    match case["fn"].as_str().unwrap_or_default() {
        "parse_assessment" => render(&parse_assessment(data)),
        "parse_classification" => render(&parse_classification(data)),
        "bias_risk_from_dict" => {
            let bias = BiasRisk::from_json(data);
            json!({
                "selection": bias.selection, "performance": bias.performance,
                "detection": bias.detection, "attrition": bias.attrition,
                "reporting": bias.reporting,
            })
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        // A case may carry a `corrected` block — the payload this port is meant
        // to produce where the Python raised (#295) or where its value is out of
        // contract. The corpus holds both, with the reason on the case.
        let expected_value = match case.get("corrected") {
            Some(corrected) => corrected,
            None => {
                assert!(
                    want["ok"].as_bool().unwrap_or(false),
                    "{name}: {}",
                    want["error"]
                );
                &want["value"]
            }
        };
        let got = run(case);
        if &got != expected_value {
            failures.push(format!(
                "  {name}\n    expected: {}\n    rust:     {}",
                serde_json::to_string(expected_value).unwrap_or_default(),
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

// ---------------------------------------------------------------------------
// #295: a key present with null
// ---------------------------------------------------------------------------

/// **A key present with JSON `null` reads as "not answered", not as a crash.**
///
/// `dict.get(k, default)` returns its default only for an *absent* key, so the
/// Python reached `.get()`/`.lower()`/`float()` on `None` and raised.
#[test]
fn a_null_field_is_not_answered_rather_than_a_crash() {
    // design_characteristics: null
    let assessment = parse_assessment(&json!({
        "study_design": "rct", "quality_score": 8, "confidence": 0.9,
        "design_characteristics": null, "bias_risk": {}
    }));
    assert_eq!(assessment.study_design.member_name(), "RCT");
    assert_eq!(assessment.quality_score, 8.0);
    assert_eq!(assessment.is_randomized, None, "absent is not false");

    // bias_risk: null
    let assessment = parse_assessment(&json!({
        "study_design": "rct", "design_characteristics": {}, "bias_risk": null
    }));
    let bias = assessment.bias_risk.expect("a record");
    assert_eq!(bias.selection, "unclear");

    // study_design: null
    let assessment = parse_assessment(&json!({"study_design": null, "quality_score": 8}));
    assert_eq!(assessment.quality_score, 8.0, "the rest still reads");
}

/// **A `null` mapping is read as an empty one, and the difference is observable**
/// through a flag: `serde_json`'s `Value::get` on a `Null` returns `None` for
/// every key, so a coercion that merely passed the `null` through would still
/// answer every flag `None` — while a *non-null* non-object would not. The case
/// below asks for a flag **and** a blinding level under a `null` mapping, so a
/// coercion that stopped at `Object` still passes and one that reached for the
/// value does not.
#[test]
fn a_null_mapping_is_read_as_an_empty_one() {
    let assessment = parse_assessment(&json!({
        "study_design": "rct",
        "design_characteristics": null,
        // Read under the null mapping: both must be unstated, not denied.
        "is_randomized": true,
    }));
    assert_eq!(assessment.is_randomized, None);
    assert_eq!(assessment.is_controlled, None);
    assert_eq!(assessment.is_blinded, None);

    // And the empty-object form gives the identical assessment, which is the
    // claim: `null` and `{}` are the same statement.
    let empty = parse_assessment(&json!({
        "study_design": "rct", "design_characteristics": {}
    }));
    assert_eq!(assessment.is_randomized, empty.is_randomized);
    assert_eq!(assessment.is_blinded, empty.is_blinded);
}

/// The same coercion serves a **wrong-typed** container: a string where a mapping
/// belongs is "not answered" too, and raising on it is the same defect one step
/// out.
#[test]
fn a_wrong_typed_container_is_not_answered() {
    let assessment = parse_assessment(&json!({
        "study_design": "rct", "design_characteristics": "none",
        "bias_risk": [], "quality_score": 5
    }));
    assert_eq!(assessment.quality_score, 5.0);
    assert_eq!(assessment.is_randomized, None);
    assert_eq!(assessment.bias_risk.expect("a record").selection, "unclear");
}

/// A **null number** reads as the default rather than raising: `float(None)`
/// raised, and the prompt permits `null` for a field it did not determine.
#[test]
fn a_null_number_reads_as_the_default() {
    let assessment = parse_assessment(&json!({
        "study_design": "cohort", "quality_score": null, "confidence": null,
        "sample_size": null
    }));
    assert_eq!(assessment.quality_score, 0.0);
    assert_eq!(assessment.confidence, 0.5);
    assert_eq!(assessment.sample_size, None);

    // And on the classifier, where the same read exists.
    let classified = parse_classification(&json!({"study_design": null, "confidence": null}));
    assert_eq!(classified.study_design.member_name(), "UNKNOWN");
    assert_eq!(classified.confidence, 0.5);
}

/// **The consequence the defect had was worse than a degraded score.** The broad
/// `except` turned the crash into `unclassified()` *after* `chat_json` had
/// returned a parseable dict, so no retry ran — and `QualityManager` lets Tier 3
/// **replace** Tier 1. A paper the metadata classified conclusively as an RCT came
/// back `UNKNOWN` at score 0.
#[test]
fn a_null_does_not_cost_a_conclusive_metadata_classification() {
    let assessment = parse_assessment(&json!({
        "study_design": "rct", "quality_score": 8, "confidence": 0.9,
        "design_characteristics": null, "bias_risk": null
    }));
    // What Tier 1 would have said for an RCT, and what the caller must still get.
    assert_ne!(
        assessment.study_design.member_name(),
        "UNKNOWN",
        "Tier 1's conclusive RCT must survive Tier 3 answering null"
    );
    assert_eq!(assessment.assessment_tier, 3);
    assert_eq!(assessment.quality_score, 8.0);
    assert_eq!(
        assessment.quality_tier.value(),
        design_to_tier(assessment.study_design).value()
    );
}

// ---------------------------------------------------------------------------
// The reading rules
// ---------------------------------------------------------------------------

/// A design name is **trimmed and lowercased** before the mapping — the same rule
/// the extractors use — and an unrecognised one is `UNKNOWN` rather than an error,
/// because a model naming a design bmlib does not know has still answered.
#[test]
fn a_design_name_is_normalised_and_an_unknown_one_is_unknown() {
    for raw in ["RCT", "  rct  ", "rct"] {
        let assessment = parse_assessment(&json!({"study_design": raw}));
        assert_eq!(assessment.study_design.member_name(), "RCT", "{raw:?}");
    }
    for raw in ["not-a-design", "", "cohortish"] {
        let assessment = parse_assessment(&json!({"study_design": raw}));
        assert_eq!(
            assessment.study_design.member_name(),
            "UNKNOWN",
            "{raw:?} names no design bmlib knows"
        );
    }
}

/// Scores and confidences are **clamped**, and a value that is a numeric string
/// is read — a model that answers `"7"` has answered 7.
#[test]
fn scores_are_clamped_and_numeric_strings_are_read() {
    assert_eq!(
        parse_assessment(&json!({"quality_score": 12})).quality_score,
        10.0
    );
    assert_eq!(
        parse_assessment(&json!({"quality_score": -3})).quality_score,
        0.0
    );
    assert_eq!(
        parse_assessment(&json!({"quality_score": "7"})).quality_score,
        7.0
    );
    assert_eq!(
        parse_assessment(&json!({"confidence": 1.5})).confidence,
        1.0
    );
    assert_eq!(
        parse_assessment(&json!({"confidence": -0.5})).confidence,
        0.0
    );
    assert_eq!(
        parse_assessment(&json!({"confidence": "0.25"})).confidence,
        0.25
    );
}

/// A sample size is read as `int()` reads it: **a float truncates toward zero**,
/// because Python's `int(100.5)` is `100` and does not raise.
#[test]
fn a_sample_size_is_read_as_python_reads_it() {
    assert_eq!(
        parse_assessment(&json!({"sample_size": 100})).sample_size,
        Some(100)
    );
    assert_eq!(
        parse_assessment(&json!({"sample_size": "100"})).sample_size,
        Some(100)
    );
    assert_eq!(
        parse_assessment(&json!({"sample_size": 100.5})).sample_size,
        Some(100),
        "truncated, as int() does"
    );
    assert_eq!(
        parse_assessment(&json!({"sample_size": "many"})).sample_size,
        None
    );
    // A negative size is recorded: `int(-5)` succeeds, and refusing it here
    // would be a rule the Python does not have.
    assert_eq!(
        parse_assessment(&json!({"sample_size": -5})).sample_size,
        Some(-5)
    );
}

/// Only the four blinding words are accepted, and **case-sensitively** — the
/// Python tests the raw value, so `"Triple"` and `"Double"` are not recognised
/// and read as unstated.
///
/// A quirk rather than a rule, and reproduced: a port that folded case would
/// record a blinding the Python dropped, which is a **new claim about a study**
/// rather than a tidier spelling of an old one.
#[test]
fn only_the_four_blinding_words_are_accepted_case_sensitively() {
    for raw in ["double", "none"] {
        let assessment = parse_assessment(&json!({"design_characteristics": {"blinded": raw}}));
        assert_eq!(assessment.is_blinded.as_deref(), Some(raw), "{raw:?}");
    }
    for raw in ["Triple", "Double", "DOUBLE", "sometimes"] {
        let assessment = parse_assessment(&json!({"design_characteristics": {"blinded": raw}}));
        assert_eq!(
            assessment.is_blinded, None,
            "{raw:?} is not one of the four"
        );
    }
    let assessment = parse_assessment(&json!({"design_characteristics": {"blinded": null}}));
    assert_eq!(assessment.is_blinded, None);
}

/// A design flag is **`Option<bool>`** because the field is a *claim*: a model
/// that answered `null` has not said the study was randomised, and a `false`
/// there would put a denial in its mouth. A string `"true"` is not a boolean and
/// does not become one.
#[test]
fn a_design_flag_is_a_claim_not_a_default() {
    assert_eq!(
        parse_assessment(&json!({"design_characteristics": {"randomized": true}})).is_randomized,
        Some(true)
    );
    assert_eq!(
        parse_assessment(&json!({"design_characteristics": {"randomized": false}})).is_randomized,
        Some(false)
    );
    assert_eq!(
        parse_assessment(&json!({"design_characteristics": {"randomized": "true"}})).is_randomized,
        None,
        "a string is not a boolean — and the field is annotated `bool | None`, where \
         `is_randomized != Some(true)` is what the quality filter tests, so a string \
         would silently fail `require_randomization` while reading as an answer"
    );
    assert_eq!(
        parse_assessment(&json!({"design_characteristics": {"randomized": null}})).is_randomized,
        None
    );
}

/// A findings list keeps the **strings** and drops anything else: a number in
/// `strengths` is not a strength, and coercing it would put a claim in the
/// model's mouth.
#[test]
fn a_findings_list_keeps_only_strings() {
    let assessment = parse_assessment(&json!({
        "strengths": ["large sample", 42, null, "blinded"],
        "limitations": "not a list"
    }));
    assert_eq!(assessment.strengths, vec!["large sample", "blinded"]);
    assert!(assessment.limitations.is_empty());
}

/// `BiasRisk` maps every unrecognised domain to `"unclear"`, so a typo, a `null`
/// or a number all become unstated rather than travelling on as a claim.
#[test]
fn an_unrecognised_bias_domain_is_unclear() {
    let bias = BiasRisk::from_json(&json!({
        "selection": "excellent", "performance": null, "detection": 5,
        "attrition": "", "reporting": "low"
    }));
    assert_eq!(bias.selection, "unclear");
    assert_eq!(bias.performance, "unclear");
    assert_eq!(bias.detection, "unclear");
    assert_eq!(bias.attrition, "unclear");
    assert_eq!(bias.reporting, "low", "a recognised value travels");

    // An empty mapping is the five-unclear record, not `None`.
    let bias = BiasRisk::from_json(&json!({}));
    assert_eq!(bias.selection, "unclear");
    assert_eq!(bias.reporting, "unclear");
}
