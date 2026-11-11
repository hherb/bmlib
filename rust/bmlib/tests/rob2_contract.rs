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

//! The shared RoB 2 contracts, `tests/fixtures/rob2_replies.json` and
//! `tests/fixtures/rob2_run.json`.
//!
//! The same files Python's `tests/test_rob2_contract.py` asserts against: the
//! wording and its renderings, each domain and escalation reply's reading or
//! refusal, and a whole run that must round-trip unchanged.

use bmlib::quality::outcome_agent::ProposedOutcome;
use bmlib::quality::rob2_agent::{
    domain_prompt, escalation_message, read_domain_reply, read_escalation_reply, DomainAssessment,
    Escalation, Rob2Run, ROB2_DOMAIN_PROMPT, ROB2_SYSTEM_PROMPT,
};
use bmlib::quality::rob2_algorithm::{questions, Domain};
use bmlib::quality::rob2_questions::{
    domain_title, question_text, D5_OUTCOME_SWITCHING, ESCALATION_PROMPT, ROB2_CONDENSE_QUERIES,
    ROB2_PROMPT_VERSION,
};
use serde_json::{json, Value};

const REPLIES: &str = include_str!("../../../tests/fixtures/rob2_replies.json");
const RUN: &str = include_str!("../../../tests/fixtures/rob2_run.json");

fn replies() -> Value {
    serde_json::from_str(REPLIES).expect("the fixture is JSON")
}

fn domain(value: &Value) -> Domain {
    serde_json::from_value(value.clone()).expect("a domain")
}

#[test]
fn the_wording_is_the_contracts() {
    let fixture = replies();
    assert_eq!(u64::from(ROB2_PROMPT_VERSION), fixture["prompt_version"]);
    assert_eq!(ROB2_SYSTEM_PROMPT, fixture["system_prompt"]);
    assert_eq!(ROB2_DOMAIN_PROMPT, fixture["domain_prompt"]);
    assert_eq!(D5_OUTCOME_SWITCHING, fixture["d5_outcome_switching"]);
    assert_eq!(ESCALATION_PROMPT, fixture["escalation_prompt"]);
    let texts = fixture["question_texts"]
        .as_object()
        .expect("question_texts");
    let ids: Vec<&str> = Domain::ALL
        .iter()
        .flat_map(|d| questions(*d).iter().copied())
        .collect();
    assert_eq!(texts.len(), ids.len());
    for id in ids {
        assert_eq!(question_text(id), texts[id], "{id}");
    }
    for d in Domain::ALL {
        assert_eq!(domain_title(d), fixture["domain_titles"][format!("{d:?}")]);
    }
    let queries: Vec<Value> = ROB2_CONDENSE_QUERIES
        .iter()
        .map(|(d, q)| json!([d, q]))
        .collect();
    assert_eq!(Value::Array(queries), fixture["condense_queries"]);
}

#[test]
fn the_prompts_render_as_the_contract_says() {
    let fixture = replies();
    let rendered = &fixture["rendered"];
    let title = fixture["title"].as_str().expect("title");
    let text = fixture["text"].as_str().expect("text");
    let outcome = ProposedOutcome::from_json(&fixture["outcome"]).expect("outcome");
    let second = ProposedOutcome::from_json(&fixture["outcome_without_result"]).expect("outcome");
    assert_eq!(
        domain_prompt(Domain::D1, title, text, None).expect("D1"),
        rendered["D1"]
    );
    assert_eq!(
        domain_prompt(Domain::D2, title, text, Some(&second)).expect("D2"),
        rendered["D2 without a result"]
    );
    assert_eq!(
        domain_prompt(Domain::D5, title, text, Some(&outcome)).expect("D5"),
        rendered["D5"]
    );
    let study = DomainAssessment::from_json(&rendered["escalation_study"]).expect("study");
    let domains: Vec<DomainAssessment> = rendered["escalation_domains"]
        .as_array()
        .expect("domains")
        .iter()
        .map(|d| DomainAssessment::from_json(d).expect("domain"))
        .collect();
    assert_eq!(
        escalation_message(&outcome, &study, &domains),
        rendered["escalation"]
    );
}

#[test]
fn every_domain_reply_reads_as_expected_or_is_refused_as_expected() {
    let fixture = replies();
    let text = fixture["text"].as_str().expect("text");
    let similarity = fixture["min_similarity"].as_f64().expect("min_similarity");
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = &case["name"];
        let read = read_domain_reply(domain(&case["domain"]), &case["reply"], text, similarity);
        match case.get("error") {
            Some(error) => assert_eq!(
                read.expect_err(&name.to_string()),
                error.as_str().expect("error"),
                "{name}"
            ),
            None => {
                let assessment = read.unwrap_or_else(|e| panic!("{name}: {e}"));
                assert_eq!(assessment.to_json(), case["expected"], "{name}");
                assert_eq!(
                    DomainAssessment::from_json(&case["expected"]),
                    Ok(assessment),
                    "{name}"
                );
            }
        }
    }
}

#[test]
fn every_escalation_reply_reads_as_expected_or_is_refused_as_expected() {
    let fixture = replies();
    for case in fixture["escalation_cases"].as_array().expect("cases") {
        let name = &case["name"];
        let read = read_escalation_reply(&case["reply"]);
        match case.get("error") {
            Some(error) => assert_eq!(
                read.expect_err(&name.to_string()),
                error.as_str().expect("error"),
                "{name}"
            ),
            None => {
                let escalation = read.unwrap_or_else(|e| panic!("{name}: {e}"));
                assert_eq!(escalation.to_json(), case["expected"], "{name}");
                assert_eq!(
                    Escalation::from_json(&case["expected"]),
                    Ok(escalation),
                    "{name}"
                );
            }
        }
    }
}

#[test]
fn the_run_round_trips_unchanged() {
    let fixture: Value = serde_json::from_str(RUN).expect("the fixture is JSON");
    let run = Rob2Run::from_json(&fixture).expect("the run reads");
    assert_eq!(run.to_json(), fixture);
    let overall: Vec<Option<_>> = run.outcomes.iter().map(|o| o.overall).collect();
    assert_eq!(
        serde_json::to_value(overall).expect("json"),
        json!(["high", null, null])
    );
}
