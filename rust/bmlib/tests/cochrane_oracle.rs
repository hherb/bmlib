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

//! The differential oracle: Rust versus Python, over the Cochrane models.
//!
//! No corrections — these models carry none of the sixteen defects — so every
//! case is diffed strictly.

use bmlib::quality::cochrane_models::{
    collapse_risk_of_bias, create_default_cochrane_risk_of_bias, create_default_risk_of_bias_item,
    CochraneInterventions, CochraneNotes, CochraneOutcomes, CochraneParticipants,
    CochraneRiskOfBias, CochraneStudyAssessment, CochraneStudyCharacteristics, RiskOfBiasItem,
    RiskOfBiasJudgement,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/cochrane_cases.json");
const EXPECTED: &str = include_str!("data/cochrane_expected.json");

/// Apply a per-domain judgement map to a default nine-domain assessment.
fn rob_from_spec(spec: Option<&Value>) -> CochraneRiskOfBias {
    let mut rob = create_default_cochrane_risk_of_bias();
    let Some(map) = spec.and_then(Value::as_object) else {
        return rob;
    };
    let items: [&mut RiskOfBiasItem; 9] = [
        &mut rob.random_sequence_generation,
        &mut rob.allocation_concealment,
        &mut rob.baseline_outcome_measurements,
        &mut rob.baseline_characteristics,
        &mut rob.blinding_participants_personnel,
        &mut rob.blinding_outcome_assessment_subjective,
        &mut rob.blinding_outcome_assessment_objective,
        &mut rob.incomplete_outcome_data,
        &mut rob.selective_reporting,
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
    rob
}

fn opt_str(spec: &Value, key: &str) -> Option<String> {
    spec.get(key).and_then(Value::as_str).map(str::to_string)
}

fn string_vec(spec: &Value, key: &str) -> Option<Vec<String>> {
    spec.get(key).and_then(Value::as_array).map(|a| {
        a.iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect()
    })
}

fn study_chars(overrides: Option<&Value>) -> CochraneStudyCharacteristics {
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
    participants.baseline_characteristics_reported = o
        .get("baseline_characteristics_reported")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let mut interventions =
        CochraneInterventions::new(s("intervention_description", "Hospital at home"));
    interventions.intervention_groups = string_vec(&o, "intervention_groups");
    interventions.control_description = opt_str(&o, "control_description");
    interventions.duration = opt_str(&o, "duration");
    interventions.setting = opt_str(&o, "intervention_setting");

    let mut outcomes = CochraneOutcomes::new(s("outcomes_description", "Mortality, cost"));
    outcomes.primary_outcomes = string_vec(&o, "primary_outcomes");
    outcomes.secondary_outcomes = string_vec(&o, "secondary_outcomes");
    outcomes.outcome_timepoints = string_vec(&o, "outcome_timepoints");
    outcomes.outcome_assessment_methods = string_vec(&o, "outcome_assessment_methods");

    let notes = CochraneNotes {
        follow_up_periods: string_vec(&o, "follow_up_periods"),
        funding_source: opt_str(&o, "funding_source"),
        conflicts_of_interest: opt_str(&o, "conflicts_of_interest"),
        ethical_approval: opt_str(&o, "ethical_approval"),
        trial_registration: opt_str(&o, "trial_registration"),
        publication_status: opt_str(&o, "publication_status"),
        additional_notes: string_vec(&o, "additional_notes"),
    };

    let mut ch = CochraneStudyCharacteristics::new(
        s("study_id", "Andrei 2011"),
        s("methods", "Parallel randomised trial"),
        participants,
        interventions,
        outcomes,
        notes,
    );
    ch.document_id = o.get("document_id").and_then(Value::as_i64);
    ch.document_title = opt_str(&o, "document_title");
    ch.pmid = opt_str(&o, "pmid");
    ch.doi = opt_str(&o, "doi");
    // The oracle pins `created_at` to `None` so the comparison is
    // deterministic; the field is a wall-clock stamp on both sides.
    ch.created_at = None;
    ch
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    let overrides = args.get("overrides");

    match fn_name {
        "judgement_from_string" => json!(RiskOfBiasJudgement::from_string(
            args["value"].as_str().unwrap_or_default()
        )
        .as_str()),
        "default_item" => json!(create_default_risk_of_bias_item(
            args.get("domain").and_then(Value::as_str).unwrap_or("D"),
            args.get("bias_type")
                .and_then(Value::as_str)
                .unwrap_or("selection bias"),
            args.get("outcome_type").and_then(Value::as_str),
        )
        .to_json()),
        "default_rob" => json!(create_default_cochrane_risk_of_bias().to_json()),
        "rob_to_list" => json!(rob_from_spec(args.get("judgements"))
            .to_list()
            .iter()
            .map(|i| i.to_json())
            .collect::<Vec<_>>()),
        "rob_summary_counts" => json!(rob_from_spec(args.get("judgements")).summary_counts()),
        "rob_roundtrip" => json!(create_default_cochrane_risk_of_bias().to_json()),
        "item_to_dict_omits_empty_outcome_type" => json!(RiskOfBiasItem::new(
            "D",
            "selection bias",
            "Low risk",
            "s",
            args.get("outcome_type")
                .and_then(Value::as_str)
                .map(str::to_string),
        )
        .to_json()),
        "participants_format" => json!(study_chars(overrides).participants.format_for_table()),
        "notes_format" => json!(study_chars(overrides).notes.format_for_table()),
        "characteristics_to_dict" => json!(study_chars(overrides).to_json()),
        "characteristics_roundtrip" => {
            let ch = study_chars(overrides);
            let parsed =
                CochraneStudyCharacteristics::from_json(&ch.to_json()).expect("round trip");
            let mut out = parsed.to_json();
            // The oracle drops `created_at` for these cases: Python's
            // `from_dict` stamps a live clock when the input's was null, so
            // comparing two readings from different processes would fail for a
            // reason unrelated to the port. (`from_json` here preserves
            // whatever was there, which is the more useful behaviour.)
            out.as_object_mut().expect("object").remove("created_at");
            out
        }
        "assessment_to_dict" => {
            let mut a = CochraneStudyAssessment::new(
                study_chars(overrides),
                rob_from_spec(args.get("judgements")),
            );
            a.overall_quality_score = args.get("overall_quality_score").and_then(Value::as_f64);
            a.overall_confidence = args.get("overall_confidence").and_then(Value::as_f64);
            a.evidence_level = args
                .get("evidence_level")
                .and_then(Value::as_str)
                .map(str::to_string);
            a.assessment_notes = string_vec(args, "assessment_notes");
            a.condensed_from_chars = args.get("condensed_from_chars").and_then(Value::as_i64);
            a.condensation_status = args
                .get("condensation_status")
                .and_then(Value::as_str)
                .map(str::to_string);
            json!(a.to_json())
        }
        "assessment_study_id" => {
            let a = CochraneStudyAssessment::new(
                study_chars(overrides),
                rob_from_spec(args.get("judgements")),
            );
            json!({"study_id": a.study_id(), "document_id": a.document_id()})
        }
        "collapse" => match collapse_risk_of_bias(&rob_from_spec(args.get("judgements"))) {
            Ok(bias) => json!({"ok": true, "value": bias.to_json()}),
            Err(e) => json!({"ok": false, "error": e.to_string()}),
        },
        "collapse_custom_types" => {
            let mut rob = create_default_cochrane_risk_of_bias();
            let items: [&mut RiskOfBiasItem; 9] = [
                &mut rob.random_sequence_generation,
                &mut rob.allocation_concealment,
                &mut rob.baseline_outcome_measurements,
                &mut rob.baseline_characteristics,
                &mut rob.blinding_participants_personnel,
                &mut rob.blinding_outcome_assessment_subjective,
                &mut rob.blinding_outcome_assessment_objective,
                &mut rob.incomplete_outcome_data,
                &mut rob.selective_reporting,
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
            if let Some(map) = args.get("bias_types").and_then(Value::as_object) {
                for (slot, name) in items.into_iter().zip(names) {
                    if let Some(bt) = map.get(name).and_then(Value::as_str) {
                        slot.bias_type = bt.to_string();
                    }
                }
            }
            match collapse_risk_of_bias(&rob) {
                Ok(bias) => json!({"ok": true, "value": bias.to_json()}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
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
