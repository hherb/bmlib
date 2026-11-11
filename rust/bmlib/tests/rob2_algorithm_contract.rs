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
//! The shared RoB 2 contract, `tests/fixtures/rob2_algorithm.json`.
//!
//! The same file Python's `tests/test_rob2_algorithm_contract.py` asserts
//! against: the hand-listed official cases, every exhaustive walk, the
//! refusals, the answer parser and the overall judgement.

use bmlib::quality::rob2_algorithm::{
    applicable_questions, domain_judgement, needs_escalation_decision, overall_judgement, Answer,
    Answers, Domain, Judgement, Rob2Error, ROB2_ALGORITHM_VERSION,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/rob2_algorithm.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn wire<T: serde::de::DeserializeOwned>(value: &Value) -> T {
    serde_json::from_value(value.clone()).unwrap_or_else(|error| panic!("{value}: {error}"))
}

fn answers(value: &Value) -> Answers {
    value
        .as_object()
        .expect("an object of answers")
        .iter()
        .map(|(q, a)| {
            (
                q.clone(),
                Answer::parse(a.as_str().expect("text")).expect("an answer"),
            )
        })
        .collect()
}

fn domains(value: &Value) -> [Judgement; 5] {
    let list: Vec<Judgement> = wire(value);
    list.try_into().expect("five domains")
}

fn kind(error: &Rob2Error) -> &'static str {
    match error {
        Rob2Error::MissingAnswer { .. } => "MissingAnswer",
        Rob2Error::NotAllowed { .. } => "NotAllowed",
        Rob2Error::UnknownQuestion { .. } => "UnknownQuestion",
        Rob2Error::EscalationUndecided => "EscalationUndecided",
        Rob2Error::UnreadableAnswer { .. } => "UnreadableAnswer",
    }
}

#[test]
fn the_version_is_the_contracts() {
    assert_eq!(ROB2_ALGORITHM_VERSION, fixture()["algorithm_version"]);
}

#[test]
fn every_official_and_exhaustive_case_gives_the_expected_judgement() {
    let fixture = fixture();
    let mut failures = Vec::new();
    for section in ["official", "exhaustive"] {
        let cases = fixture[section].as_array().expect("a list of cases");
        assert!(!cases.is_empty(), "{section} is empty");
        for case in cases {
            let domain: Domain = wire(&case["domain"]);
            let expected: Judgement = wire(&case["expected"]);
            let actual = domain_judgement(domain, &answers(&case["answers"]));
            if actual != Ok(expected) {
                failures.push(format!("{section} {case}: got {actual:?}"));
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn applicable_questions_are_the_expected_ones() {
    for case in fixture()["applicable"].as_array().expect("cases") {
        let expected: Vec<String> = wire(&case["expected"]);
        assert_eq!(
            applicable_questions(wire(&case["domain"]), &answers(&case["answers"])),
            expected
        );
    }
}

#[test]
fn the_parser_reads_and_refuses_as_the_contract_says() {
    for case in fixture()["parse"].as_array().expect("cases") {
        let result = Answer::parse(case["value"].as_str().expect("text"));
        match case["expected"].as_str() {
            Some(expected) => assert_eq!(
                serde_json::to_value(result.expect("an answer")).expect("wire"),
                expected
            ),
            None => assert!(matches!(result, Err(Rob2Error::UnreadableAnswer { .. }))),
        }
    }
}

#[test]
fn every_refusal_is_refused_with_its_kind_and_question() {
    for case in fixture()["refusals"].as_array().expect("cases") {
        let error = domain_judgement(wire(&case["domain"]), &answers(&case["answers"]))
            .expect_err(&format!("{} accepted", case["name"]));
        assert_eq!(kind(&error), case["kind"], "{}", case["name"]);
        let question = match &error {
            Rob2Error::MissingAnswer { question }
            | Rob2Error::NotAllowed { question, .. }
            | Rob2Error::UnknownQuestion { question } => question.as_str(),
            other => panic!("{other:?} names no question"),
        };
        assert_eq!(question, case["question"], "{}", case["name"]);
    }
}

#[test]
fn the_overall_judgement_is_the_expected_one() {
    for case in fixture()["overall"].as_array().expect("cases") {
        let list = domains(&case["domains"]);
        let escalate = case["escalate"].as_bool();
        assert_eq!(
            needs_escalation_decision(&list),
            case["needs_escalation_decision"]
        );
        match overall_judgement(&list, escalate) {
            Ok(actual) => assert_eq!(
                serde_json::to_value(actual).expect("wire"),
                case["expected"]
            ),
            Err(error) => assert_eq!(kind(&error), case["error"]),
        }
    }
}
