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

//! The shared D5 contracts Python's `tests/test_rob2_d5_contract.py` asserts
//! too: `rob2_d5_checks.json` and `rob2_d5_facts.json`, written by
//! `scripts/make_rob2_d5_fixtures.py`.

use bmlib::quality::outcome_agent::ProposedOutcome;
use bmlib::quality::rob2_agent::{d5_assessment, DomainAssessment};
use bmlib::quality::rob2_d5_checks::{
    fact_verdict, names_outcome, shows_result, states_a_plan, supports, D5Fact, D5_FACTS,
};
use bmlib::quality::rob2_d5_facts::{
    facts_prompt, read_facts_reply, read_second_look, second_look_prompt, D5Facts,
    D5_FACTS_SYSTEM_PROMPT, D5_SECOND_LOOK_SYSTEM_PROMPT,
};
use serde_json::{json, Value};

const CHECKS: &str = include_str!("../../../tests/fixtures/rob2_d5_checks.json");
const FACTS: &str = include_str!("../../../tests/fixtures/rob2_d5_facts.json");

fn facts_fixture() -> Value {
    serde_json::from_str(FACTS).expect("the fixture is JSON")
}

fn answers_of(assessment: &DomainAssessment) -> Value {
    let answers: serde_json::Map<String, Value> = assessment
        .answers
        .iter()
        .map(|(q, a)| (q.clone(), json!(a.answer)))
        .collect();
    Value::Object(answers)
}

#[test]
fn the_prompts_are_the_contracts() {
    let data = facts_fixture();
    let outcome = ProposedOutcome::from_json(&data["outcome"]).expect("outcome");
    let (title, paper) = (text(&data, "title"), text(&data, "paper"));
    assert_eq!(D5_FACTS_SYSTEM_PROMPT, text(&data, "system_prompt"));
    assert_eq!(
        D5_SECOND_LOOK_SYSTEM_PROMPT,
        text(&data, "second_look_system_prompt")
    );
    assert_eq!(
        facts_prompt(title, paper, &outcome),
        text(&data, "facts_prompt")
    );
    let mut stored = data["looked"]["facts"].clone();
    stored["other_measurements"] = json!([data["second_look_item"].clone()]);
    let facts = D5Facts::from_json(&stored).expect("stored facts");
    assert_eq!(
        second_look_prompt(title, paper, &outcome, &facts.measurements[0]),
        text(&data, "second_look_prompt")
    );
}

#[test]
fn every_facts_reply_reads_as_the_contract_says() {
    let data = facts_fixture();
    let (paper, outcome) = (text(&data, "paper"), text(&data["outcome"], "name"));
    let similarity = data["min_similarity"].as_f64().expect("a similarity");
    for case in cases(&data, "replies") {
        let read = read_facts_reply(&case["reply"], paper, outcome, similarity);
        if let Some(error) = case["error"].as_str() {
            assert_eq!(read.err().as_deref(), Some(error), "{}", case["name"]);
            continue;
        }
        let facts = read.expect("a readable reply");
        assert_eq!(facts.to_json(), case["facts"], "{}", case["name"]);
        let assessment = d5_assessment(facts);
        assert_eq!(answers_of(&assessment), case["answers"], "{}", case["name"]);
        assert_eq!(
            json!(assessment.judgement),
            case["judgement"],
            "{}",
            case["name"]
        );
    }
}

#[test]
fn every_second_look_reads_as_the_contract_says() {
    let data = facts_fixture();
    let paper = text(&data, "paper");
    let similarity = data["min_similarity"].as_f64().expect("a similarity");
    for case in cases(&data, "second_looks") {
        let read = read_second_look(&case["reply"], paper, similarity);
        match case["error"].as_str() {
            Some(error) => assert_eq!(read.err().as_deref(), Some(error), "{case}"),
            None => {
                let found: Vec<Value> = read
                    .expect("passages")
                    .iter()
                    .map(|c| c.to_json())
                    .collect();
                assert_eq!(Value::Array(found), case["found"], "{case}");
            }
        }
    }
}

#[test]
fn stored_facts_give_the_contracts_answers_and_round_trip() {
    let data = facts_fixture();
    let looked = &data["looked"];
    let facts = D5Facts::from_json(&looked["facts"]).expect("stored facts");
    assert_eq!(facts.to_json(), looked["facts"]);
    let assessment = d5_assessment(facts);
    assert_eq!(answers_of(&assessment), looked["answers"]);
    assert_eq!(json!(assessment.judgement), looked["judgement"]);
    assert_eq!(assessment.to_json(), looked["assessment"]);
    let back = DomainAssessment::from_json(&looked["assessment"]).expect("a D5 assessment");
    assert_eq!(back.to_json(), looked["assessment"]);
}

#[test]
fn a_stored_verdict_the_checks_do_not_give_is_refused() {
    let data = facts_fixture();
    let mut tampered = data["looked"]["facts"].clone();
    assert_eq!(tampered["facts"]["measure_fixed"]["verdict"], "present");
    tampered["facts"]["measure_fixed"]["verdict"] = json!("not_claimed");
    let error = D5Facts::from_json(&tampered).expect_err("a wrong verdict");
    assert!(error.contains("measure_fixed"), "{error}");
}

fn checks() -> Value {
    serde_json::from_str(CHECKS).expect("the fixture is JSON")
}

fn cases<'v>(data: &'v Value, section: &str) -> &'v Vec<Value> {
    data[section].as_array().expect("a list of cases")
}

fn fact(case: &Value) -> D5Fact {
    D5Fact::parse(case["fact"].as_str().expect("a fact")).expect("a known fact")
}

fn strings(value: &Value) -> Vec<&str> {
    value
        .as_array()
        .expect("a list")
        .iter()
        .map(|v| v.as_str().expect("text"))
        .collect()
}

fn text<'v>(case: &'v Value, key: &str) -> &'v str {
    case[key].as_str().expect("text")
}

#[test]
fn the_facts_are_the_contracts() {
    let names: Vec<&str> = D5_FACTS.iter().map(|f| f.as_str()).collect();
    assert_eq!(
        names,
        [
            "plan_cited",
            "plan_dated_early",
            "plan_late",
            "measure_fixed",
            "timepoint_fixed",
            "analysis_fixed",
            "switched",
            "change_explained"
        ]
    );
    assert_eq!(D5Fact::parse("dated"), None);
}

#[test]
fn every_check_case() {
    let data = checks();
    for case in cases(&data, "supports") {
        let got = supports(fact(case), text(case, "quote"), text(case, "outcome"));
        assert_eq!(Some(got), case["expected"].as_bool(), "{case}");
    }
    for case in cases(&data, "names_outcome") {
        let got = names_outcome(text(case, "quote"), text(case, "outcome"));
        assert_eq!(Some(got), case["expected"].as_bool(), "{case}");
    }
    for case in cases(&data, "states_a_plan") {
        assert_eq!(
            Some(states_a_plan(&strings(&case["quotes"]))),
            case["expected"].as_bool(),
            "{case}"
        );
    }
    for case in cases(&data, "shows_result") {
        assert_eq!(
            Some(shows_result(&strings(&case["passages"]))),
            case["expected"].as_bool(),
            "{case}"
        );
    }
    for case in cases(&data, "fact_verdict") {
        let got = fact_verdict(
            fact(case),
            case["claimed"].as_bool().expect("a flag"),
            &strings(&case["found"]),
            text(case, "outcome"),
        );
        assert_eq!(got.as_str(), text(case, "expected"), "{case}");
    }
}
