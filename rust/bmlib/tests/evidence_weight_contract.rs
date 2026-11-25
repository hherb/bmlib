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

//! The shared evidence-weight contract, `tests/fixtures/evidence_weight.json`.
//!
//! The same file Python's `tests/test_evidence_weight_contract.py` asserts
//! against: for each case the model's replies in order and the
//! `EvidenceWeight` the pipeline returns, and for each refusal a change to the
//! assessed case's wire form and the message reading it gives.

use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::agent_chat::JsonChat;
use bmlib::quality::evidence_ladder::{paper_weight, start_level, LadderSettings};
use bmlib::quality::evidence_weight::{
    assess_outcome, assess_unattended, AgentOptions, EvidenceWeight, EvidenceWeightError,
};
use bmlib::quality::rob2_algorithm::ROB2_ALGORITHM_VERSION;
use bmlib::quality::rob2_questions::ROB2_PROMPT_VERSION;
use bmlib::quality::{
    combine_outcome, plan_rob2, DesignAgent, DomainResult, OutcomeAgent, OutcomeRob2, Rob2Agent,
    Rob2Digests, Rob2Run, Rob2Step,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/evidence_weight.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

/// Answers each request with the next reply.
struct Scripted {
    replies: Vec<Value>,
    sent: usize,
}

impl JsonChat for Scripted {
    fn chat_json(
        &mut self,
        _messages: &[LLMMessage],
        _temperature: f64,
        _max_tokens: i64,
        _max_retries: usize,
        _require_dict: bool,
    ) -> Result<ChatJsonOutcome, ChatJsonError> {
        self.sent += 1;
        Ok(ChatJsonOutcome {
            value: self.replies[self.sent - 1].clone(),
            attempts: 1,
            repaired: false,
        })
    }
}

/// Python's `apply_change` in `scripts/make_evidence_weight.py`.
fn apply_change(wire: &mut Value, change: &Value) {
    let path = change["path"].as_array().expect("path");
    let (last, parents) = path.split_last().expect("a path");
    let mut target = wire;
    for key in parents {
        target = step(target, key);
    }
    if let Some(value) = change.get("set") {
        *step(target, last) = value.clone();
    } else if change.get("remove").is_some() {
        if let Some(list) = step(target, last).as_array_mut() {
            list.pop();
        } else {
            target
                .as_object_mut()
                .expect("an object")
                .remove(last.as_str().expect("a key"));
        }
    } else {
        step(target, last).as_array_mut().expect("a list").reverse();
    }
}

/// `value[key]`, by index for a number and by name for text, created if absent.
fn step<'v>(value: &'v mut Value, key: &Value) -> &'v mut Value {
    match key.as_u64() {
        Some(index) => &mut value[usize::try_from(index).expect("an index")],
        None => &mut value[key.as_str().expect("a key")],
    }
}

#[test]
fn every_case_runs_to_the_expected_result_and_reads_back() {
    let fixture = fixture();
    let cases = fixture["cases"].as_array().expect("cases");
    assert_eq!(cases.len(), 4);
    for case in cases {
        let replies = case["replies"].as_array().expect("replies").clone();
        let count = replies.len();
        let mut chat = Scripted { replies, sent: 0 };
        let result = assess_unattended(
            &mut chat,
            None,
            None,
            &LadderSettings::default(),
            &AgentOptions::default(),
            fixture["title"].as_str().expect("title"),
            fixture["text"].as_str().expect("text"),
            fixture["min_similarity"].as_f64().expect("similarity"),
        )
        .expect("a result");
        assert_eq!(result.to_json(), case["expected"], "{}", case["name"]);
        assert_eq!(chat.sent, count, "{}", case["name"]);
        assert_eq!(
            EvidenceWeight::from_json(&case["expected"]).expect("reads back"),
            result,
            "{}",
            case["name"]
        );
    }
}

#[test]
fn every_refusal_names_what_does_not_add_up() {
    let fixture = fixture();
    let refusals = fixture["refusals"].as_array().expect("refusals");
    assert!(!refusals.is_empty());
    for case in refusals {
        let mut wire = fixture["cases"][3]["expected"].clone();
        apply_change(&mut wire, &case["change"]);
        let error = EvidenceWeight::from_json(&wire).expect_err("refused");
        assert_eq!(
            error,
            case["error"].as_str().expect("text"),
            "{}",
            case["name"]
        );
    }
}

/// #813: a `ladder_error` stored where the inputs build a ladder is refused
/// (two changes, so not one of the fixture's refusals; Python's unit test has it).
#[test]
fn a_ladder_error_where_a_ladder_can_be_built_is_refused() {
    let fixture = fixture();
    let mut wire = fixture["cases"][3]["expected"].clone();
    wire["assessed"][0]["ladder"] = Value::Null;
    wire["assessed"][0]["ladder_error"] = Value::from("x");
    assert_eq!(
        EvidenceWeight::from_json(&wire).expect_err("refused"),
        "assessed[0]'s ladder is not the one its design, \
         RoB 2 result, reported result and settings give"
    );
}

/// #813: a ladder stored where the inputs build none is refused (two changes;
/// Python's unit test has it too).
#[test]
fn a_ladder_where_none_can_be_built_is_refused() {
    let fixture = fixture();
    let mut wire = fixture["cases"][3]["expected"].clone();
    assert!(wire["assessed"][1]["ladder"].is_null());
    wire["assessed"][1]["ladder"] = wire["assessed"][0]["ladder"].clone();
    wire["assessed"][1]["ladder_error"] = Value::Null;
    assert_eq!(
        EvidenceWeight::from_json(&wire).expect_err("refused"),
        "assessed[1]'s ladder is not the one its design, \
         RoB 2 result, reported result and settings give"
    );
}

/// An `assessed` result with no outcomes is one `assess_unattended` never
/// writes (it returns `no_primary_outcome`); three changes, so a unit test.
#[test]
fn an_assessed_result_with_no_outcomes_is_refused() {
    let fixture = fixture();
    let mut wire = fixture["cases"][3]["expected"].clone();
    wire["outcomes"]["outcomes"] = Value::Array(vec![]);
    wire["run"]["outcomes"] = Value::Array(vec![]);
    wire["assessed"] = Value::Array(vec![]);
    wire["paper_weight"] = Value::Null;
    assert_eq!(
        EvidenceWeight::from_json(&wire).expect_err("refused"),
        "an assessed result has no primary outcomes"
    );
}

/// The assessed case under settings other than the defaults reads back to
/// itself: every settings field survives the wire, so the rebuild on read
/// (#813) gives the stored ladders.
#[test]
fn a_result_under_other_settings_reads_back() {
    let fixture = fixture();
    let case = &fixture["cases"][3];
    let settings = LadderSettings {
        some_concerns_steps: 2,
        small_trial_n: 5000,
        max_imprecision_steps: 1,
        weight_high: 0.95,
        weight_moderate: 0.65,
        weight_low: 0.35,
        weight_very_low: 0.05,
        ..LadderSettings::default()
    };
    let mut chat = Scripted {
        replies: case["replies"].as_array().expect("replies").clone(),
        sent: 0,
    };
    let result = assess_unattended(
        &mut chat,
        None,
        None,
        &settings,
        &AgentOptions::default(),
        fixture["title"].as_str().expect("title"),
        fixture["text"].as_str().expect("text"),
        fixture["min_similarity"].as_f64().expect("similarity"),
    )
    .expect("a result");
    let wire = result.to_json();
    assert_ne!(wire["assessed"], case["expected"]["assessed"]);
    assert_eq!(
        EvidenceWeight::from_json(&wire).expect("reads back"),
        result
    );
}

/// The assessed case driven one step at a time (design, outcomes, then
/// `plan_rob2`'s steps with one set of digests, then `assess_outcome` for each
/// outcome) assembles the same `Rob2Run`, assessed outcomes and paper weight
/// the fixture records for `assess_unattended`.
#[test]
fn the_assessed_case_step_by_step() {
    let fixture = fixture();
    let case = &fixture["cases"][3];
    let (title, text) = (
        fixture["title"].as_str().expect("title"),
        fixture["text"].as_str().expect("text"),
    );
    let similarity = fixture["min_similarity"].as_f64().expect("similarity");
    let replies = case["replies"].as_array().expect("replies").clone();
    let count = replies.len();
    let mut chat = Scripted { replies, sent: 0 };
    let options = AgentOptions::default();

    let design = DesignAgent::new(&mut chat)
        .with_temperature(options.temperature)
        .with_max_tokens(options.max_tokens)
        .with_max_retries(options.max_retries)
        .propose(title, text, similarity)
        .expect("a design");
    let proposal = OutcomeAgent::new(&mut chat)
        .with_temperature(options.temperature)
        .with_max_tokens(options.max_tokens)
        .with_max_retries(options.max_retries)
        .propose(title, text, similarity)
        .expect("outcomes");

    let mut agent = Rob2Agent::new(&mut chat)
        .with_temperature(options.temperature)
        .with_max_tokens(options.max_tokens)
        .with_max_retries(options.max_retries);
    let mut digests = Rob2Digests::default();
    let mut study: Option<DomainResult> = None;
    let mut domains = Vec::new();
    let mut results = Vec::new();
    for step in plan_rob2(proposal.outcomes.len()) {
        match step {
            Rob2Step::Domain { domain, outcome } => {
                let result = agent
                    .assess_domain(
                        domain,
                        title,
                        text,
                        outcome.map(|i| &proposal.outcomes[i]),
                        similarity,
                        &mut digests,
                    )
                    .expect("a domain");
                if outcome.is_none() {
                    study = Some(result);
                } else {
                    domains.push(result);
                }
            }
            Rob2Step::Escalation { outcome } => {
                let proposed = &proposal.outcomes[outcome];
                let study = study.as_ref().expect("D1 first");
                let escalation = agent
                    .escalate_outcome(title, proposed, study, &domains)
                    .expect("an escalation");
                let combined =
                    combine_outcome(proposed, study, std::mem::take(&mut domains), escalation)
                        .expect("the steps fit");
                results.push(combined);
            }
        }
    }
    let run = Rob2Run {
        algorithm_version: ROB2_ALGORITHM_VERSION.to_string(),
        prompt_version: ROB2_PROMPT_VERSION,
        study: study.expect("D1"),
        outcomes: results,
    };
    assert_eq!(run.to_json(), case["expected"]["run"]);
    assert_eq!(chat.sent, count);

    // #816: the ladder step, outcome by outcome, gives the pipeline's assessed and weight.
    let start = start_level(design.design).expect("an RCT");
    let settings = LadderSettings::default();
    let assessed: Vec<_> = proposal
        .outcomes
        .iter()
        .zip(&run.outcomes)
        .map(|(proposed, rob2)| assess_outcome(start, &run.study, proposed, rob2, &settings))
        .collect::<Result<_, _>>()
        .expect("valid settings");
    let wire: Vec<Value> = assessed.iter().map(|a| a.to_json()).collect();
    assert_eq!(Value::Array(wire), case["expected"]["assessed"]);
    let ladders: Vec<_> = assessed.iter().filter_map(|a| a.ladder.clone()).collect();
    assert_eq!(
        serde_json::to_value(paper_weight(&ladders)).expect("a number or null"),
        case["expected"]["paper_weight"]
    );

    // The two ladder errors the fixture's outcomes do not reach, worded as Python's test has them.
    let (proposed, rob2) = (&proposal.outcomes[0], &run.outcomes[0]);
    let failed = OutcomeRob2 {
        overall: None,
        escalation_error: Some("HTTP 503: overloaded".to_string()),
        ..rob2.clone()
    };
    assert_eq!(
        assess_outcome(start, &run.study, proposed, &failed, &settings)
            .expect("valid settings")
            .ladder_error,
        Some(
            "the outcome's risk of bias was not assessed: \
             the escalation question failed (HTTP 503: overloaded)"
                .to_string()
        )
    );
    let mut unladdered = proposed.clone();
    unladdered.result.as_mut().expect("a result").ci_low = Some(0.75);
    let one = assess_outcome(start, &run.study, &unladdered, rob2, &settings).expect("valid");
    assert!(one.ladder.is_none());
    assert_eq!(
        one.ladder_error.as_deref(),
        Some("ci_low <= estimate <= ci_high does not hold")
    );

    // #818: invalid settings are the call's error, never the outcome's ladder_error.
    let invalid = LadderSettings {
        weight_high: 0.2,
        weight_low: 0.9,
        ..LadderSettings::default()
    };
    let reason = invalid.validate().expect_err("invalid");
    assert_eq!(
        assess_outcome(start, &run.study, proposed, rob2, &invalid),
        Err(EvidenceWeightError::InvalidSettings(reason))
    );
}
