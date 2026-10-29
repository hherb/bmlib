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

//! The shared streaming-chat contract, `tests/fixtures/chat_stream.json`.
//!
//! The same file Python's `tests/test_llm_stream.py` asserts against: each
//! case's events, run through `run_stream`, give exactly its deltas and its
//! response, or an error containing its `error`.

use bmlib::llm::{run_stream, ChatError, Protocol, StreamDelta};
use serde_json::{json, Value};
use std::ops::ControlFlow;

const FIXTURE: &str = include_str!("../../../tests/fixtures/chat_stream.json");

/// The protocols this port's client reads, by the fixture's name for them.
fn protocol_of(name: &str) -> Option<Protocol> {
    match name {
        "anthropic" => Some(Protocol::Anthropic),
        "openai" => Some(Protocol::OpenAi),
        "ollama" => Some(Protocol::Ollama),
        _ => None,
    }
}

fn delta_json(delta: &StreamDelta) -> Value {
    match delta {
        StreamDelta::Text(text) => json!({"kind": "text", "text": text}),
        StreamDelta::Thinking(text) => json!({"kind": "thinking", "text": text}),
    }
}

#[test]
fn every_case_gives_its_deltas_and_response() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture is JSON");
    let mut failures = Vec::new();
    let mut run = 0;
    for case in fixture["cases"].as_array().expect("a list of cases") {
        let name = case["name"].as_str().expect("a name");
        let protocol_name = case["protocol"].as_str().expect("a protocol");
        let Some(protocol) = protocol_of(protocol_name) else {
            panic!("{name}: a protocol nobody reads: {protocol_name}");
        };
        run += 1;
        let stop_after = case.get("stop_after").and_then(Value::as_u64);
        let mut seen = Vec::new();
        let events = case["events"]
            .as_array()
            .expect("events")
            .iter()
            .cloned()
            .map(Ok);
        let result = run_stream(protocol, events, "model-x", &mut |delta| {
            seen.push(delta_json(delta));
            if stop_after.is_some_and(|n| seen.len() as u64 >= n) {
                ControlFlow::Break(())
            } else {
                ControlFlow::Continue(())
            }
        });
        if Value::Array(seen.clone()) != case["deltas"] {
            failures.push(format!("{name}: deltas {seen:?}"));
        }
        match (result, case.get("error").and_then(Value::as_str)) {
            (Err(ChatError::Stream(message)), Some(want)) if message.contains(want) => {}
            (Ok(response), None) => {
                let got = json!({
                    "content": response.content,
                    "thinking": response.thinking,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "stop_reason": response.stop_reason,
                });
                if got != case["response"] {
                    failures.push(format!("{name}: response {got}"));
                }
                if response.model != "model-x"
                    || response.tool_calls.is_some()
                    || response.total_tokens != response.input_tokens + response.output_tokens
                {
                    failures.push(format!("{name}: {response:?}"));
                }
            }
            (other, want) => failures.push(format!("{name}: got {other:?}, wanted {want:?}")),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
    // No case is skipped: every protocol in the fixture is read.
    assert_eq!(run, 24, "cases run");
}
