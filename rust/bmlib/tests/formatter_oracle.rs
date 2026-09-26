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

//! The differential oracle: Rust versus Python, over the Cochrane renderers.
//!
//! These compare **exact strings**, unlike every other corpus. A renderer's
//! output *is* the artefact, so there is no semantic field to compare instead:
//! one character of whitespace is a difference a consumer sees.

use bmlib::quality::cochrane_formatter::{
    format_complete_assessment_markdown, format_multiple_assessments_markdown,
    format_risk_of_bias_html, format_risk_of_bias_markdown, format_risk_of_bias_summary_markdown,
    format_study_characteristics_html, format_study_characteristics_markdown, get_cochrane_css,
    COCHRANE_CSS,
};
use bmlib::quality::cochrane_models::{
    create_default_cochrane_risk_of_bias, CochraneInterventions, CochraneNotes, CochraneOutcomes,
    CochraneParticipants, CochraneRiskOfBias, CochraneStudyAssessment,
    CochraneStudyCharacteristics,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/formatter_cases.json");
const EXPECTED: &str = include_str!("data/formatter_expected.json");

fn rob(judgements: Option<&Value>) -> CochraneRiskOfBias {
    let mut r = create_default_cochrane_risk_of_bias();
    let Some(map) = judgements.and_then(Value::as_object) else {
        return r;
    };
    let items: [&mut bmlib::quality::cochrane_models::RiskOfBiasItem; 9] = [
        &mut r.random_sequence_generation,
        &mut r.allocation_concealment,
        &mut r.baseline_outcome_measurements,
        &mut r.baseline_characteristics,
        &mut r.blinding_participants_personnel,
        &mut r.blinding_outcome_assessment_subjective,
        &mut r.blinding_outcome_assessment_objective,
        &mut r.incomplete_outcome_data,
        &mut r.selective_reporting,
    ];
    let names = [
        "random_sequence_generation",
        "allocation_concealment",
        "baseline_outcome_measurements",
        "baseline_characteristics",
        "blinding_participants_personnel",
        "blinding_outcome_assessment_subjective",
        "blinding_outcome_assessment_objective",
        "incomplete_outcome_data",
        "selective_reporting",
    ];
    for (slot, name) in items.into_iter().zip(names) {
        if let Some(j) = map.get(name).and_then(Value::as_str) {
            slot.judgement = j.to_string();
        }
    }
    r
}

fn chars(overrides: Option<&Value>) -> CochraneStudyCharacteristics {
    let o = overrides.cloned().unwrap_or_else(|| json!({}));
    let s = |key: &str, default: &str| -> String {
        o.get(key)
            .and_then(Value::as_str)
            .unwrap_or(default)
            .to_string()
    };
    let mut participants = CochraneParticipants::new(
        s("setting", "Romania"),
        s("population", "Chronic heart failure"),
    );
    participants.total_participants = o.get("total_participants").and_then(Value::as_i64);
    participants.group_sizes = o.get("group_sizes").filter(|v| !v.is_null()).cloned();

    let notes = CochraneNotes {
        funding_source: o
            .get("funding_source")
            .and_then(Value::as_str)
            .map(str::to_string),
        additional_notes: o
            .get("additional_notes")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            }),
        ..CochraneNotes::default()
    };

    let mut ch = CochraneStudyCharacteristics::new(
        s("study_id", "Andrei 2011"),
        s("methods", "Parallel randomised trial"),
        participants,
        CochraneInterventions::new(s("intervention_description", "Hospital at home")),
        CochraneOutcomes::new(s("outcomes_description", "Mortality, cost")),
        notes,
    );
    ch.created_at = None;
    ch
}

fn assessment(
    overrides: Option<&Value>,
    judgements: Option<&Value>,
    kwargs: Option<&Value>,
) -> CochraneStudyAssessment {
    let mut a = CochraneStudyAssessment::new(chars(overrides), rob(judgements));
    if let Some(k) = kwargs {
        a.overall_quality_score = k.get("overall_quality_score").and_then(Value::as_f64);
        a.overall_confidence = k.get("overall_confidence").and_then(Value::as_f64);
        a.evidence_level = k
            .get("evidence_level")
            .and_then(Value::as_str)
            .map(str::to_string);
        a.assessment_notes = k
            .get("assessment_notes")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            });
    }
    a
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "chars_markdown" => json!(format_study_characteristics_markdown(&chars(
            args.get("overrides")
        ))),
        "chars_html" => json!(format_study_characteristics_html(&chars(
            args.get("overrides")
        ))),
        "rob_markdown" => json!(format_risk_of_bias_markdown(&rob(args.get("judgements")))),
        "rob_html" => json!(format_risk_of_bias_html(&rob(args.get("judgements")))),
        "complete_markdown" => json!(format_complete_assessment_markdown(&assessment(
            args.get("overrides"),
            args.get("judgements"),
            args.get("kwargs")
        ))),
        "multiple_markdown" => {
            let n = args.get("count").and_then(Value::as_u64).unwrap_or(2) as usize;
            let items: Vec<CochraneStudyAssessment> = (0..n)
                .map(|i| {
                    assessment(
                        Some(&json!({"study_id": format!("Study {}", i + 1)})),
                        None,
                        None,
                    )
                })
                .collect();
            json!(format_multiple_assessments_markdown(
                &items,
                args.get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("Characteristics of included studies")
            ))
        }
        "summary_markdown" => {
            let n = args.get("count").and_then(Value::as_u64).unwrap_or(2) as usize;
            let items: Vec<CochraneStudyAssessment> = (0..n)
                .map(|i| {
                    assessment(
                        Some(&json!({"study_id": format!("Study {}", i + 1)})),
                        None,
                        None,
                    )
                })
                .collect();
            json!(format_risk_of_bias_summary_markdown(&items))
        }
        "summary_empty" => json!(format_risk_of_bias_summary_markdown(&[])),
        "css" => json!(get_cochrane_css()),
        "css_len" => json!({
            "len": COCHRANE_CSS.len(),
            "lines": COCHRANE_CSS.matches('\n').count(),
        }),
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_renders_byte_identically_to_python() {
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
        // A `corrected` block means the port deliberately diverges here: the
        // harness asserts Python still produces the recorded thing, that Rust
        // produces the corrected value, and that the two differ. Only one case
        // carries one — the summary guard that drops a lone confidence — and
        // it is the only renderer defect this port fixes.
        let expected_value = match case.get("corrected") {
            Some(corrected) => {
                let python_says = &want["value"];
                assert_ne!(
                    python_says, &corrected["value"],
                    "{name}: the correction is not a difference, so Python has changed"
                );
                &corrected["value"]
            }
            None => &want["value"],
        };

        let got = run(case);
        if got != *expected_value {
            // A long markdown blob is unreadable as one line, so the first
            // differing line is named — that is what a renderer divergence
            // actually is.
            let want_text = expected_value.as_str().unwrap_or_default();
            let got_text = got.as_str().unwrap_or_default();
            let detail = if want_text.is_empty() || got_text.is_empty() {
                format!(
                    "\n    expected: {}\n    rust:     {}",
                    serde_json::to_string(expected_value).unwrap_or_default(),
                    serde_json::to_string(&got).unwrap_or_default()
                )
            } else {
                let first_diff = want_text
                    .lines()
                    .zip(got_text.lines())
                    .position(|(a, b)| a != b);
                match first_diff {
                    Some(i) => format!(
                        "\n    first differing line {i}:\n      python: {:?}\n      rust:   {:?}",
                        want_text.lines().nth(i).unwrap_or_default(),
                        got_text.lines().nth(i).unwrap_or_default()
                    ),
                    None => format!(
                        "\n    identical lines but different length: python {} chars, rust {} chars",
                        want_text.chars().count(),
                        got_text.chars().count()
                    ),
                }
            };
            failures.push(format!("  {name}{detail}"));
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
