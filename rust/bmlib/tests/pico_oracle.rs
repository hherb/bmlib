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

//! Differential oracle: the Rust PICO agent against the Python one.
//!
//! `rust/oracle/dump_pico_agent.py` runs Python's `PicoAgent` over each case's
//! scripted model replies; this runs the Rust agent over the same replies and
//! asserts the same result (or error kind) **and the same messages sent**, so
//! a prompt that drifts in one language fails here.

mod common;

use std::collections::BTreeMap;

use bmlib::context_processor::{
    ExtractionResult, ProcessingConfig, ProcessingResult, ProcessingStatus,
};
use bmlib::llm::LLMResponse;
use bmlib::quality::cochrane_assessor::Condenser;
use bmlib::quality::pico_agent::{
    PicoAgent, EXTRACTION_RESPONSE_FORMAT, EXTRACTION_TASK_PROMPT,
    PICO_CONDENSE_CONSOLIDATION_PROMPT, PICO_CONDENSE_EXTRACTION_PROMPT, PICO_CONDENSE_QUERY,
    PICO_SYSTEM_PROMPT, SUITABILITY_RESPONSE_FORMAT, SUITABILITY_TASK_PROMPT,
};
use common::scripted_chat::{Script, ScriptedChat};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/pico_agent_cases.json");
const EXPECTED: &str = include_str!("data/pico_agent_expected.json");

/// A condenser that returns a fixed digest, as the dumper patches Python's
/// map-reduce to.
struct FixedDigest {
    content: String,
    status: ProcessingStatus,
}

impl Condenser for FixedDigest {
    fn condense(
        &mut self,
        _text: &str,
        _label: &str,
        _config: &ProcessingConfig,
    ) -> ProcessingResult {
        ProcessingResult {
            final_result: ExtractionResult::new(self.content.clone()),
            status: self.status,
            total_items_processed: 1,
            batches_created: 1,
            recursion_levels_used: 0,
            intermediate_results: None,
            error_message: None,
            processing_stats: BTreeMap::new(),
            failed_batches: Vec::new(),
            skipped_items: Vec::new(),
            successful_batches: 1,
        }
    }
}

fn status(name: &str) -> ProcessingStatus {
    match name {
        "completed" => ProcessingStatus::Completed,
        "partial" => ProcessingStatus::Partial,
        "truncated" => ProcessingStatus::Truncated,
        "failed" => ProcessingStatus::Failed,
        other => panic!("unknown status {other}"),
    }
}

fn run(case: &Value) -> Value {
    let args = &case["args"];
    let stop_reasons = args["stop_reasons"].as_array().cloned().unwrap_or_default();
    let responses = args["replies"]
        .as_array()
        .expect("replies")
        .iter()
        .enumerate()
        .map(|(i, reply)| {
            Ok(LLMResponse {
                content: reply.as_str().expect("a reply string").to_string(),
                stop_reason: Some(
                    stop_reasons
                        .get(i)
                        .and_then(Value::as_str)
                        .unwrap_or("stop")
                        .to_string(),
                ),
                ..Default::default()
            })
        })
        .collect();
    let mut chat = ScriptedChat::new(Script::new(responses));
    let mut condenser = FixedDigest {
        content: args["digest"].as_str().unwrap_or("").to_string(),
        status: status(args["digest_status"].as_str().unwrap_or("completed")),
    };
    let title = args["title"].as_str().expect("title");
    let text = args["text"].as_str().expect("text");

    let outcome = {
        let mut agent = PicoAgent::new(&mut chat).with_condenser(&mut condenser);
        if let Some(temperature) = args["temperature"].as_f64() {
            agent = agent.with_temperature(temperature);
        }
        if let Some(chars) = args["max_context_chars"].as_u64() {
            agent = agent.with_condense_config(
                ProcessingConfig::default().with_max_context_chars(chars as usize),
            );
        }
        match case["fn"].as_str().expect("fn") {
            "extract" => agent
                .extract(
                    title,
                    text,
                    args["min_similarity"].as_f64().expect("similarity"),
                )
                .map(|r| r.to_json()),
            "suitability" => agent.check_suitability(title, text).map(|r| r.to_json()),
            other => panic!("unknown fn {other}"),
        }
    };

    let mut value = match outcome {
        Ok(result) => json!({ "result": result }),
        Err(error) => json!({ "error": error.kind() }),
    };
    // Python records one entry per model call; Rust's `seen` holds one per
    // `chat_json` call, which retries inside. Every call in one run sends the
    // same messages, so the run is compared as those messages and a count.
    let mut sent: Vec<Value> = chat
        .seen
        .iter()
        .map(|messages| {
            Value::Array(
                messages
                    .iter()
                    .map(|m| json!({ "role": m.role.as_str(), "content": m.content }))
                    .collect(),
            )
        })
        .collect();
    sent.dedup();
    value["messages"] = Value::Array(sent);
    value["model_calls"] = json!(chat.source.calls);
    value
}

#[test]
fn the_rust_agent_agrees_with_the_python_one() {
    let cases: Vec<Value> = serde_json::from_str(CASES).expect("cases");
    let expected: Vec<Value> = serde_json::from_str(EXPECTED).expect("expected");
    assert_eq!(cases.len(), expected.len());
    assert!(
        cases.len() >= 37,
        "the corpus has shrunk below its 34 cases"
    );

    for (case, want) in cases.iter().zip(&expected) {
        assert_eq!(case["name"], want["name"]);
        assert_eq!(
            want["ok"], true,
            "{}: the dumper failed: {}",
            want["name"], want["error"]
        );
        if case["fn"] == "prompts" {
            // The three condensation prompts reach no run (the map-reduce is
            // faked on both sides), so they are compared here, with the rest.
            assert_eq!(
                want["value"],
                json!({
                    "PICO_SYSTEM_PROMPT": PICO_SYSTEM_PROMPT,
                    "SUITABILITY_TASK_PROMPT": SUITABILITY_TASK_PROMPT,
                    "SUITABILITY_RESPONSE_FORMAT": SUITABILITY_RESPONSE_FORMAT,
                    "EXTRACTION_TASK_PROMPT": EXTRACTION_TASK_PROMPT,
                    "EXTRACTION_RESPONSE_FORMAT": EXTRACTION_RESPONSE_FORMAT,
                    "PICO_CONDENSE_QUERY": PICO_CONDENSE_QUERY,
                    "PICO_CONDENSE_EXTRACTION_PROMPT": PICO_CONDENSE_EXTRACTION_PROMPT,
                    "PICO_CONDENSE_CONSOLIDATION_PROMPT": PICO_CONDENSE_CONSOLIDATION_PROMPT,
                }),
                "the prompts differ from Python's"
            );
            continue;
        }
        let got = run(case);
        assert_eq!(
            got["result"], want["value"]["result"],
            "{}: result",
            case["name"]
        );
        assert_eq!(
            got["error"], want["value"]["error"],
            "{}: error",
            case["name"]
        );

        let mut python_sent = want["value"]["messages"]
            .as_array()
            .expect("messages")
            .clone();
        assert_eq!(
            got["model_calls"],
            json!(python_sent.len()),
            "{}: model calls",
            case["name"]
        );
        python_sent.dedup();
        assert_eq!(
            got["messages"],
            Value::Array(python_sent),
            "{}: messages",
            case["name"]
        );
    }
}
