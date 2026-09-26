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

//! The differential oracle: Rust versus Python, over the quality data models.
//!
//! No corrections here — the data models carry none of the sixteen defects —
//! so every case is diffed strictly. The corpus is what pins the three
//! mapping tables, the evidence ordering, and the two asymmetries in
//! `passes_filter` that are easy to get subtly wrong.

use bmlib::quality::data_models::{
    design_to_randomized, design_to_score, design_to_tier, study_design_from_str, BiasRisk,
    QualityAssessment, QualityFilter, QualityTier, StudyDesign,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/model_cases.json");
const EXPECTED: &str = include_str!("data/model_expected.json");

fn design_of(raw: &str) -> StudyDesign {
    StudyDesign::ALL
        .into_iter()
        .find(|d| d.as_str() == raw)
        .unwrap_or(StudyDesign::Unknown)
}

fn tier_of(value: i64) -> QualityTier {
    QualityTier::from_value(value).expect("known tier")
}

fn filter_of(spec: &Value) -> QualityFilter {
    let mut f = QualityFilter::defaults();
    if let Some(v) = spec.get("min_tier").and_then(Value::as_i64) {
        f.min_tier = Some(tier_of(v));
    }
    if let Some(v) = spec.get("require_randomization").and_then(Value::as_bool) {
        f.require_randomization = v;
    }
    if let Some(v) = spec.get("require_blinding").and_then(Value::as_bool) {
        f.require_blinding = v;
    }
    if let Some(v) = spec.get("min_sample_size").and_then(Value::as_i64) {
        f.min_sample_size = Some(v);
    }
    f
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "design_mappings" => {
            let mut tier = serde_json::Map::new();
            let mut score = serde_json::Map::new();
            let mut randomized = serde_json::Map::new();
            for d in StudyDesign::ALL {
                tier.insert(d.as_str().into(), design_to_tier(d).value().into());
                score.insert(d.as_str().into(), design_to_score(d).into());
                randomized.insert(
                    d.as_str().into(),
                    design_to_randomized(d).map_or(Value::Null, Into::into),
                );
            }
            json!({
                "tier": Value::Object(tier),
                "score": Value::Object(score),
                "randomized": Value::Object(randomized),
                "design_count": StudyDesign::ALL.len(),
                "tier_count": QualityTier::ALL.len(),
            })
        }
        "design_from_str" => {
            json!(study_design_from_str(args["raw"].as_str().unwrap_or_default()).as_str())
        }
        "tier_ordering" => json!(QualityTier::ALL
            .iter()
            .map(|t| t.value())
            .collect::<Vec<_>>()),
        "bias_roundtrip" => json!(BiasRisk::from_json(&args["data"]).to_json()),
        "assessment_unclassified" => json!(QualityAssessment::unclassified().to_json()),
        "assessment_from_metadata" => {
            let d = design_of(args["design"].as_str().unwrap_or_default());
            json!(QualityAssessment::from_metadata(d, 0.9).to_json())
        }
        "assessment_from_classification" => {
            let d = design_of(args["design"].as_str().unwrap_or_default());
            let confidence = args
                .get("confidence")
                .and_then(Value::as_f64)
                .unwrap_or(0.7);
            let sample_size = args.get("sample_size").and_then(Value::as_i64);
            let is_blinded = args
                .get("is_blinded")
                .and_then(Value::as_str)
                .map(str::to_string);
            json!(
                QualityAssessment::from_classification(d, confidence, sample_size, is_blinded)
                    .to_json()
            )
        }
        "assessment_roundtrip" => {
            let a = QualityAssessment::from_json(&args["data"]).expect("valid assessment");
            json!(a.to_json())
        }
        "assessment_to_dict" => {
            // Built directly rather than through `from_json`, so a plain-dict
            // `cochrane_assessment` (the shape a caller gets from a JSON round
            // trip) reaches `to_json` as-is.
            let mut a = QualityAssessment::default();
            if let Some(v) = args["data"].get("assessment_tier").and_then(Value::as_i64) {
                a.assessment_tier = v;
            }
            if let Some(v) = args["data"].get("study_design").and_then(Value::as_str) {
                a.study_design = study_design_from_str(v);
            }
            if let Some(v) = args["data"].get("quality_tier").and_then(Value::as_i64) {
                a.quality_tier = tier_of(v);
            }
            if let Some(v) = args["data"].get("cochrane_assessment") {
                a.cochrane_assessment = Some(v.clone());
            }
            json!(a.to_json())
        }
        "filter_defaults" => {
            let f = QualityFilter::defaults();
            json!({
                "min_tier": f.min_tier.map(QualityTier::value),
                "require_randomization": f.require_randomization,
                "require_blinding": f.require_blinding,
                "min_sample_size": f.min_sample_size,
                "use_metadata_only": f.use_metadata_only,
                "use_llm_classification": f.use_llm_classification,
                "use_detailed_assessment": f.use_detailed_assessment,
                "use_cochrane_assessment": f.use_cochrane_assessment,
            })
        }
        "passes_filter" => {
            let a = QualityAssessment::from_json(&args["assessment"]).expect("valid assessment");
            json!(a.passes_filter(&filter_of(&args["filter"])))
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
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "case {name:?} errored in Python: {}",
            want["error"]
        );
        let got = run(case);
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
        "{} of {} cases diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}
