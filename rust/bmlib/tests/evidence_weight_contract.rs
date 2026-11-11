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
use bmlib::quality::evidence_ladder::LadderSettings;
use bmlib::quality::evidence_weight::{assess_unattended, AgentOptions, EvidenceWeight};
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
