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

//! The shared Ollama chat contract, `tests/fixtures/ollama_chat.json`.
//!
//! The same file Python's `tests/test_ollama_contract.py` asserts against, with
//! Python's Ollama provider as the oracle: each neutral request, run through
//! `chat_body`, gives exactly its body (plus `stream: false`), and each native
//! response, run through `parse_ollama_response`, gives exactly its answer.

use bmlib::llm::{
    chat_body, parse_ollama_response, parse_response, resolve_target, ChatError, ChatRequest,
    LLMMessage, LLMToolDefinition, Protocol,
};
use serde_json::{json, Value};
use std::num::NonZeroU32;

const FIXTURE: &str = include_str!("../../../tests/fixtures/ollama_chat.json");

/// The shared fixture, parsed.
fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

/// The fixture's messages as Rust `LLMMessage`s, through their serde form.
fn messages(raw: &Value) -> Vec<LLMMessage> {
    serde_json::from_value(raw.clone()).expect("the fixture's messages")
}

/// A neutral request as a Rust caller builds it. `think` is a JSON bool,
/// string or number, as a Python caller passes it; Rust carries it as text.
fn chat_request(neutral: &Value) -> ChatRequest {
    let mut request = ChatRequest::new(messages(&neutral["messages"])).with_model(format!(
        "ollama:{}",
        neutral["model"].as_str().expect("a model")
    ));
    request.temperature = neutral["temperature"].as_f64().expect("a temperature");
    request.max_tokens = neutral["max_tokens"].as_i64().expect("max_tokens");
    request.top_p = neutral.get("top_p").and_then(Value::as_f64);
    request.json_mode = neutral.get("json_mode").and_then(Value::as_bool) == Some(true);
    request.tools = neutral
        .get("tools")
        .map(|t| serde_json::from_value::<Vec<LLMToolDefinition>>(t.clone()).expect("tools"));
    request.think = neutral.get("think").map(|t| match t {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    });
    request.num_ctx = neutral
        .get("num_ctx")
        .and_then(Value::as_u64)
        .map(|n| NonZeroU32::new(u32::try_from(n).expect("fits")).expect("positive"));
    request
}

/// The body `chat_body` builds for `request`, after checking it routes to
/// Ollama's native protocol (no key needed, as for a local server).
fn body_for(request: &ChatRequest) -> Value {
    let target = resolve_target(request, "anthropic", None, None).expect("routes to ollama");
    assert_eq!(target.protocol, Protocol::Ollama);
    chat_body(request, &target, false)
}

#[test]
fn every_request_builds_its_body() {
    let mut failures = Vec::new();
    for case in fixture()["requests"].as_array().expect("requests") {
        let name = case["name"].as_str().expect("a name");
        let mut body = body_for(&chat_request(&case["request"]));
        // The native API streams unless told not to.
        if body["stream"] != json!(false) {
            failures.push(format!("{name}: stream is {}", body["stream"]));
        }
        body.as_object_mut().expect("an object").remove("stream");
        if body != case["body"] {
            failures.push(format!("{name}: {body}"));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn every_response_reads_its_answer() {
    let mut failures = Vec::new();
    for case in fixture()["responses"].as_array().expect("responses") {
        let name = case["name"].as_str().expect("a name");
        let response =
            parse_ollama_response(&case["body"], "model-x", &messages(&case["messages"]))
                .expect("parses");
        let got = json!({
            "content": response.content,
            "thinking": response.thinking,
            "tool_calls": response.tool_calls,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "stop_reason": response.stop_reason,
        });
        if got != case["expected"] || response.model != "model-x" {
            failures.push(format!("{name}: {got}"));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// `extra_body`'s `options` is merged field by field, not swapped in whole, and
/// `num_ctx` is set after it, so a caller's options cannot silently replace the
/// window the request asked for. Nor can `extra_body` turn streaming on.
#[test]
fn extra_body_options_merge_and_num_ctx_wins() {
    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("ollama:qwen3");
    request.temperature = 0.2;
    request.max_tokens = 128;
    request.num_ctx = NonZeroU32::new(32768);
    request.extra_body = Some(json!({
        "options": {"num_ctx": 2048, "seed": 7},
        "keep_alive": "5m",
        "stream": true,
    }));
    let body = body_for(&request);
    assert_eq!(
        body["options"],
        json!({"temperature": 0.2, "num_predict": 128, "seed": 7, "num_ctx": 32768})
    );
    assert_eq!(body["keep_alive"], json!("5m"));
    assert_eq!(body["stream"], json!(false));
}

/// A non-object `extra_body.options` is ignored, so it cannot replace the
/// request's options wholesale and take `num_ctx` with them.
#[test]
fn a_non_object_extra_body_options_is_ignored() {
    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("ollama:qwen3");
    request.temperature = 0.2;
    request.max_tokens = 128;
    request.num_ctx = NonZeroU32::new(32768);
    for options in [Value::Null, json!("big")] {
        request.extra_body = Some(json!({ "options": options }));
        assert_eq!(
            body_for(&request)["options"],
            json!({"temperature": 0.2, "num_predict": 128, "num_ctx": 32768}),
            "extra_body options {options}"
        );
    }
}

/// Without `num_ctx`, a caller's own `options.num_ctx` stands.
#[test]
fn extra_body_num_ctx_stands_without_one_on_the_request() {
    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("ollama:qwen3");
    request.extra_body = Some(json!({"options": {"num_ctx": 2048}}));
    assert_eq!(body_for(&request)["options"]["num_ctx"], json!(2048));
}

/// `num_ctx` is Ollama's alone: the other protocols send nothing for it.
#[test]
fn other_protocols_ignore_num_ctx() {
    for model in ["openai:gpt-4o", "anthropic:claude-x"] {
        let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model(model);
        request.num_ctx = NonZeroU32::new(32768);
        let target = resolve_target(&request, "anthropic", None, Some("k")).expect("routes");
        let body = chat_body(&request, &target, false).to_string();
        assert!(!body.contains("num_ctx"), "{model}: {body}");
        assert!(!body.contains("32768"), "{model}: {body}");
    }
}

/// A native error body at HTTP 200 is a rejected request, not a silent model.
#[test]
fn an_error_body_is_malformed() {
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("ollama:nope");
    let target = resolve_target(&request, "anthropic", None, None).expect("routes");
    let error = parse_response(
        &target,
        &json!({"error": "model 'nope' not found"}),
        &request.messages,
    )
    .expect_err("refused");
    match error {
        ChatError::Malformed(message) => assert!(message.contains("not found"), "{message}"),
        other => panic!("expected Malformed, got {other:?}"),
    }
}
