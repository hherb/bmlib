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

//! The shared outcome-reply contract, `tests/fixtures/outcome_replies.json`.
//!
//! The same file Python's `tests/test_outcome_replies_contract.py` asserts
//! against: the prompts, and for each reply either the proposal read from it
//! or the message it is refused with.

use bmlib::quality::outcome_agent::{
    read_outcome_reply, OutcomeProposal, OUTCOME_CONDENSE_QUERY, OUTCOME_PROMPT_VERSION,
    OUTCOME_SYSTEM_PROMPT, OUTCOME_TASK_PROMPT,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/outcome_replies.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

#[test]
fn the_prompts_are_the_contracts() {
    let fixture = fixture();
    assert_eq!(u64::from(OUTCOME_PROMPT_VERSION), fixture["prompt_version"]);
    assert_eq!(OUTCOME_SYSTEM_PROMPT, fixture["system_prompt"]);
    assert_eq!(OUTCOME_TASK_PROMPT, fixture["task_prompt"]);
    assert_eq!(OUTCOME_CONDENSE_QUERY, fixture["condense_query"]);
}

#[test]
fn every_reply_reads_as_expected_or_is_refused_as_expected() {
    let fixture = fixture();
    let text = fixture["text"].as_str().expect("text");
    let similarity = fixture["min_similarity"].as_f64().expect("min_similarity");
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = &case["name"];
        let read = read_outcome_reply(&case["reply"], text, similarity);
        match case.get("error") {
            Some(error) => assert_eq!(
                read.expect_err(&name.to_string()),
                error.as_str().expect("error"),
                "{name}"
            ),
            None => {
                let proposal = read.unwrap_or_else(|e| panic!("{name}: {e}"));
                assert_eq!(proposal.to_json(), case["expected"], "{name}");
                assert_eq!(
                    OutcomeProposal::from_json(&case["expected"]),
                    Ok(proposal),
                    "{name}"
                );
            }
        }
    }
}
