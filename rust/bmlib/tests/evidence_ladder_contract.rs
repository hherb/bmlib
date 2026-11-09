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
//! The shared evidence-ladder contract, `tests/fixtures/evidence_ladder.json`.
//!
//! The same file Python's `tests/test_evidence_ladder_contract.py` asserts
//! against. Floats compare exactly: the weights are table values.

use bmlib::quality::data_models::StudyDesign;
use bmlib::quality::evidence_ladder::{
    build_ladder, paper_weight, start_level, EvidenceLadder, LadderSettings, ReportedResult,
    LADDER_VERSION,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/evidence_ladder.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn wire<T: serde::de::DeserializeOwned>(value: &Value) -> T {
    serde_json::from_value(value.clone()).unwrap_or_else(|error| panic!("{value}: {error}"))
}

fn to_value<T: serde::Serialize>(value: &T) -> Value {
    serde_json::to_value(value).expect("wire")
}

#[test]
fn the_version_is_the_contracts() {
    assert_eq!(LADDER_VERSION, fixture()["ladder_version"]);
}

#[test]
fn the_default_settings_are_the_contracts() {
    assert_eq!(
        to_value(&LadderSettings::default()),
        fixture()["default_settings"]
    );
}

#[test]
fn every_case_builds_the_expected_ladder() {
    let fixture = fixture();
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let result: Option<ReportedResult> = wire(&case["result"]);
        let settings: LadderSettings = wire(&case["settings"]);
        let ladder = build_ladder(
            wire(&case["start"]),
            wire(&case["rob_overall"]),
            result.as_ref(),
            &settings,
        )
        .expect("a valid result");
        assert_eq!(to_value(&ladder), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn start_levels() {
    for case in fixture()["start_levels"].as_array().expect("cases") {
        let design: StudyDesign = wire(&case["design"]);
        assert_eq!(to_value(&start_level(design)), case["expected"]);
    }
}

#[test]
fn every_refusal_is_refused() {
    let fixture = fixture();
    let refusals = fixture["refusals"].as_array().expect("cases");
    assert!(!refusals.is_empty());
    for case in refusals {
        let result: ReportedResult = wire(&case["result"]);
        assert!(result.validate().is_err(), "{} accepted", case["name"]);
        let settings = LadderSettings::default();
        assert!(
            build_ladder(
                bmlib::quality::evidence_ladder::Level::High,
                bmlib::quality::rob2_algorithm::Judgement::Low,
                Some(&result),
                &settings
            )
            .is_err(),
            "{} built a ladder",
            case["name"]
        );
    }
}

#[test]
fn paper_weights() {
    for case in fixture()["paper_weights"].as_array().expect("cases") {
        let ladders: Vec<EvidenceLadder> = wire(&case["ladders"]);
        assert_eq!(to_value(&paper_weight(&ladders)), case["expected"]);
    }
}
