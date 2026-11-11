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
//! too: `rob2_d5_checks.json`, written by `scripts/make_rob2_d5_fixtures.py`.

use bmlib::quality::rob2_d5_checks::{
    fact_verdict, names_outcome, shows_result, states_a_plan, supports, D5Fact, D5_FACTS,
};
use serde_json::Value;

const CHECKS: &str = include_str!("../../../tests/fixtures/rob2_d5_checks.json");

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
