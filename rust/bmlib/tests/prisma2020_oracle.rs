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

//! Differential oracle: the Rust PRISMA 2020 agent against the Python one.
//!
//! `rust/oracle/dump_prisma2020_agent.py` runs Python's `Prisma2020Agent` over
//! each case's scripted model replies; this runs the Rust agent over the same
//! replies and asserts the same result (or error kind), **the same messages
//! sent** and the same condensation queries, so a prompt that drifts in one
//! language fails here.

mod common;

use std::collections::BTreeMap;
use std::sync::atomic::AtomicBool;

use bmlib::context_processor::{
    ExtractionResult, ProcessingConfig, ProcessingResult, ProcessingStatus,
};
use bmlib::llm::LLMResponse;
use bmlib::quality::prisma2020_agent::{
    part_prompt_chars, Prisma2020Agent, PART_RESPONSE_FORMAT, PART_TASK_PROMPT,
    PRISMA_CONDENSE_CONSOLIDATION_PROMPT, PRISMA_CONDENSE_EXTRACTION_PROMPT, PRISMA_SYSTEM_PROMPT,
    SUITABILITY_CONDENSE_QUERY, SUITABILITY_RESPONSE_FORMAT, SUITABILITY_TASK_PROMPT,
};
use bmlib::quality::QueryCondenser;
use common::scripted_chat::{Script, ScriptedChat};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/prisma2020_agent_cases.json");
const EXPECTED: &str = include_str!("data/prisma2020_agent_expected.json");

/// A condenser that returns a fixed digest, as the dumper patches Python's
/// map-reduce to, and records each query it was given.
struct FixedDigest {
    content: String,
    status: ProcessingStatus,
    queries: Vec<String>,
}

impl QueryCondenser for FixedDigest {
    fn condense(
        &mut self,
        query: &str,
        _text: &str,
        _label: &str,
        _config: &ProcessingConfig,
    ) -> ProcessingResult {
        self.queries.push(query.to_string());
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
            batch_errors: Vec::new(),
            batch_exceptions: Vec::new(),
            error: None,
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

/// The checklist ids a case names, as the `&'static str` the agent takes.
fn item_ids(args: &Value) -> Vec<&'static str> {
    args["item_ids"]
        .as_array()
        .expect("item_ids")
        .iter()
        .map(|id| {
            let id = id.as_str().expect("an id string");
            bmlib::quality::prisma2020_checklist::item(id)
                .expect("an id on the checklist")
                .id
        })
        .collect()
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
        queries: Vec::new(),
    };
    let stop = AtomicBool::new(args["stop"].as_bool().unwrap_or(false));
    let title = args["title"].as_str().expect("title");
    let text = args["text"].as_str().expect("text");

    let outcome = {
        let mut agent = Prisma2020Agent::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_stop(&stop);
        if let Some(chars) = args["max_context_chars"].as_u64() {
            agent = agent.with_condense_config(
                ProcessingConfig::default().with_max_context_chars(chars as usize),
            );
        }
        match case["fn"].as_str().expect("fn") {
            "part" => {
                let ids = item_ids(args);
                let similarity = args["min_similarity"].as_f64().expect("similarity");
                agent
                    .prepare_part(title, text, &ids)
                    .and_then(|prepared| agent.assess_part(&prepared, &ids, similarity))
                    .map(|r| r.to_json())
            }
            "suitability" => agent.check_suitability(title, text).map(|r| r.to_json()),
            other => panic!("unknown fn {other}"),
        }
    };

    let mut value = match outcome {
        Ok(result) => json!({ "result": result }),
        Err(error) => json!({ "error": error.kind() }),
    };
    // One entry per model call, as Python records them: a retry after an
    // unparseable reply sends the corrected request (#717).
    let sent: Vec<Value> = chat
        .sent
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
    value["messages"] = Value::Array(sent);
    value["model_calls"] = json!(chat.source.calls);
    value["queries"] = json!(condenser.queries);
    value
}

#[test]
fn the_rust_agent_agrees_with_the_python_one() {
    let cases: Vec<Value> = serde_json::from_str(CASES).expect("cases");
    let expected: Vec<Value> = serde_json::from_str(EXPECTED).expect("expected");
    assert_eq!(cases.len(), expected.len());
    assert!(
        cases.len() >= 18,
        "the corpus has shrunk below its 18 cases"
    );

    for (case, want) in cases.iter().zip(&expected) {
        assert_eq!(case["name"], want["name"]);
        assert_eq!(
            want["ok"], true,
            "{}: the dumper failed: {}",
            want["name"], want["error"]
        );
        match case["fn"].as_str().expect("fn") {
            "prompts" => {
                // The condensation prompts reach no run (the map-reduce is
                // faked on both sides), so they are compared here, with the rest.
                assert_eq!(
                    want["value"],
                    json!({
                        "PRISMA_SYSTEM_PROMPT": PRISMA_SYSTEM_PROMPT,
                        "SUITABILITY_TASK_PROMPT": SUITABILITY_TASK_PROMPT,
                        "SUITABILITY_RESPONSE_FORMAT": SUITABILITY_RESPONSE_FORMAT,
                        "PART_TASK_PROMPT": PART_TASK_PROMPT,
                        "PART_RESPONSE_FORMAT": PART_RESPONSE_FORMAT,
                        "SUITABILITY_CONDENSE_QUERY": SUITABILITY_CONDENSE_QUERY,
                        "PRISMA_CONDENSE_EXTRACTION_PROMPT": PRISMA_CONDENSE_EXTRACTION_PROMPT,
                        "PRISMA_CONDENSE_CONSOLIDATION_PROMPT": PRISMA_CONDENSE_CONSOLIDATION_PROMPT,
                    }),
                    "the prompts differ from Python's"
                );
                continue;
            }
            "part_prompt_chars" => {
                assert_eq!(
                    json!({ "result": part_prompt_chars(&item_ids(&case["args"])) }),
                    want["value"],
                    "{}: part_prompt_chars",
                    case["name"]
                );
                continue;
            }
            _ => {}
        }
        let got = run(case);
        for field in ["result", "error", "queries"] {
            assert_eq!(
                got[field], want["value"][field],
                "{}: {field}",
                case["name"]
            );
        }
        let python_sent = want["value"]["messages"]
            .as_array()
            .expect("messages")
            .clone();
        assert_eq!(
            got["model_calls"],
            json!(python_sent.len()),
            "{}: model calls",
            case["name"]
        );
        assert_eq!(
            got["messages"],
            Value::Array(python_sent),
            "{}: messages",
            case["name"]
        );
    }
}
