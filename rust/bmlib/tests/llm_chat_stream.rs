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

//! `LlmClient::chat_stream` over a scripted streaming transport.
//!
//! The reducer is the shared contract's subject (`llm_stream_contract.rs`);
//! this is the client around it: the request it posts, the SSE it reads, the
//! retry of opening, the refusals, and the usage it records.

use bmlib::llm::{
    chat_stream_body, resolve_target, with_token_tracker, ChatError, ChatRequest, LLMMessage,
    LLMToolDefinition, LlmClient, StreamDelta, RETRYABLE_STATUSES, STREAM_OPEN_ATTEMPTS,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse, HttpStream};
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::ops::ControlFlow;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// One scripted answer to opening a stream.
enum Answer {
    Status(u16, &'static str),
    Unreachable,
}

/// A transport that answers each stream with the next scripted answer and
/// records the body it was posted.
struct ScriptedStreams {
    answers: Mutex<VecDeque<Answer>>,
    posted: Mutex<Vec<(String, Value)>>,
}

impl ScriptedStreams {
    fn new(answers: Vec<Answer>) -> Arc<Self> {
        Arc::new(ScriptedStreams {
            answers: Mutex::new(answers.into()),
            posted: Mutex::new(Vec::new()),
        })
    }

    fn posted(&self) -> Vec<(String, Value)> {
        self.posted.lock().expect("lock").clone()
    }
}

impl HttpClient for ScriptedStreams {
    fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
        unreachable!("chat_stream never GETs")
    }

    fn post_json_stream(
        &self,
        url: &str,
        body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        self.posted
            .lock()
            .expect("lock")
            .push((url.to_string(), body.clone()));
        match self.answers.lock().expect("lock").pop_front() {
            Some(Answer::Status(status, body)) => Ok(HttpStream {
                status,
                body: Box::new(body.as_bytes()),
            }),
            Some(Answer::Unreachable) | None => {
                Err(FetchError::Transport("connection refused".to_string()))
            }
        }
    }
}

fn client(transport: Arc<ScriptedStreams>) -> LlmClient {
    let mut client = LlmClient::new(transport);
    client.api_key = Some("k".to_string());
    client.retry_base_delay = Duration::ZERO;
    client
}

fn request(model: &str) -> ChatRequest {
    ChatRequest::new(vec![LLMMessage::user("Was it blinded?")]).with_model(model)
}

/// Run a stream, collecting its deltas.
fn stream(
    client: &LlmClient,
    request: &ChatRequest,
) -> (Vec<StreamDelta>, Result<bmlib::llm::LLMResponse, ChatError>) {
    let mut seen = Vec::new();
    let result = client.chat_stream(request, &mut |delta| {
        seen.push(delta.clone());
        ControlFlow::Continue(())
    });
    (seen, result)
}

const ANTHROPIC_SSE: &str = "event: message_start\n\
data: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":12,\"output_tokens\":1}}}\n\n\
event: content_block_start\n\
data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n\
event: ping\ndata: {\"type\":\"ping\"}\n\n\
event: content_block_delta\n\
data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Blinded\"}}\n\n\
event: content_block_delta\n\
data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\", yes.\"}}\n\n\
event: message_delta\n\
data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":7}}\n\n\
event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n";

const OPENAI_SSE: &str =
    "data: {\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"\"}}]}\n\n\
data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Yes\"}}]}\n\n\
data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n\
data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":1}}\n\n\
data: [DONE]\n\n";

#[test]
fn an_anthropic_stream_is_read_off_the_wire() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, ANTHROPIC_SSE)]);
    let (seen, result) = stream(&client(transport.clone()), &request("anthropic:claude-x"));
    let response = result.expect("a response");
    assert_eq!(
        seen,
        [
            StreamDelta::Text("Blinded".to_string()),
            StreamDelta::Text(", yes.".to_string())
        ]
    );
    assert_eq!(response.content, "Blinded, yes.");
    assert_eq!((response.input_tokens, response.output_tokens), (12, 7));
    assert_eq!(response.stop_reason.as_deref(), Some("end_turn"));
    let (url, body) = transport.posted().pop().expect("a post");
    assert_eq!(url, "https://api.anthropic.com/v1/messages");
    assert_eq!(body["stream"], json!(true));
    assert!(body.get("stream_options").is_none());
}

#[test]
fn an_openai_stream_ends_at_done_and_asks_for_usage() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, OPENAI_SSE)]);
    let (seen, result) = stream(&client(transport.clone()), &request("openai:gpt-x"));
    let response = result.expect("a response");
    assert_eq!(seen, [StreamDelta::Text("Yes".to_string())]);
    assert_eq!((response.input_tokens, response.output_tokens), (9, 1));
    let (_, body) = transport.posted().pop().expect("a post");
    assert_eq!(body["stream"], json!(true));
    assert_eq!(body["stream_options"], json!({"include_usage": true}));
}

#[test]
fn stream_options_go_only_to_the_providers_listed() {
    let request = request("mistral:m");
    let target = resolve_target(&request, "anthropic", None, Some("k")).expect("a target");
    let body = chat_stream_body(&request, &target, false);
    assert_eq!(body["stream"], json!(true));
    assert!(body.get("stream_options").is_none());
}

#[test]
fn extra_body_cannot_turn_streaming_off() {
    let mut request = request("openai:gpt-x");
    request.extra_body = Some(json!({"stream": false, "seed": 3}));
    let target = resolve_target(&request, "anthropic", None, Some("k")).expect("a target");
    let body = chat_stream_body(&request, &target, false);
    assert_eq!(body["stream"], json!(true));
    assert_eq!(body["seed"], json!(3));
}

#[test]
fn a_stream_cut_off_before_its_end_marker_is_an_error() {
    // `OPENAI_SSE` up to, not including, the chunk carrying `finish_reason`.
    const CUT: &str = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Yes\"}}]}\n\n";
    let transport = ScriptedStreams::new(vec![Answer::Status(200, CUT)]);
    let (seen, result) = stream(&client(transport), &request("openai:gpt-x"));
    assert_eq!(seen.len(), 1);
    assert!(
        matches!(result, Err(ChatError::Stream(m)) if m.contains("ended before a finish_reason"))
    );
}

#[test]
fn an_event_that_is_not_json_is_malformed() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, "data: {nope\n\n")]);
    let (_, result) = stream(&client(transport), &request("openai:gpt-x"));
    assert!(matches!(result, Err(ChatError::Malformed(m)) if m.contains("not JSON")));
}

#[test]
fn the_caller_can_stop_the_stream() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, ANTHROPIC_SSE)]);
    let result = client(transport).chat_stream(&request("anthropic:claude-x"), &mut |_| {
        ControlFlow::Break(())
    });
    let response = result.expect("a response");
    assert_eq!(response.content, "Blinded");
    assert_eq!(response.stop_reason.as_deref(), Some("cancelled"));
}

#[test]
fn opening_is_retried_on_a_retryable_status_and_a_transport_failure() {
    let transport = ScriptedStreams::new(vec![
        Answer::Status(529, "{\"error\":{\"message\":\"Overloaded\"}}"),
        Answer::Unreachable,
        Answer::Status(200, ANTHROPIC_SSE),
    ]);
    let (_, result) = stream(&client(transport.clone()), &request("anthropic:claude-x"));
    assert_eq!(result.expect("a response").content, "Blinded, yes.");
    assert_eq!(transport.posted().len(), 3);
    assert!(RETRYABLE_STATUSES.contains(&529));
}

#[test]
fn opening_gives_up_after_the_last_attempt_and_quotes_the_server() {
    let answers = (0..STREAM_OPEN_ATTEMPTS)
        .map(|_| Answer::Status(503, "{\"error\":{\"message\":\"try later\"}}"))
        .collect();
    let transport = ScriptedStreams::new(answers);
    let (_, result) = stream(&client(transport.clone()), &request("anthropic:claude-x"));
    assert!(matches!(&result, Err(ChatError::Transport(m)) if m.contains("HTTP 503: try later")));
    assert_eq!(transport.posted().len(), STREAM_OPEN_ATTEMPTS as usize);
}

#[test]
fn a_rejected_request_is_not_retried() {
    let transport = ScriptedStreams::new(vec![Answer::Status(
        401,
        "{\"error\":{\"message\":\"invalid x-api-key\"}}",
    )]);
    let (_, result) = stream(&client(transport.clone()), &request("anthropic:claude-x"));
    assert!(matches!(&result, Err(ChatError::Transport(m)) if m.contains("invalid x-api-key")));
    assert_eq!(transport.posted().len(), 1);
}

#[test]
fn tools_are_refused_before_anything_is_sent() {
    let transport = ScriptedStreams::new(vec![]);
    let mut request = request("anthropic:claude-x");
    request.tools = Some(vec![LLMToolDefinition {
        name: "f".to_string(),
        description: String::new(),
        parameters: json!({}),
    }]);
    let (_, result) = stream(&client(transport.clone()), &request);
    assert_eq!(result, Err(ChatError::StreamingToolsUnsupported));
    assert!(transport.posted().is_empty());
}

#[test]
fn a_client_that_cannot_stream_says_so() {
    struct GetOnly;
    impl HttpClient for GetOnly {
        fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
            unreachable!()
        }
    }
    let mut client = LlmClient::new(Arc::new(GetOnly));
    client.api_key = Some("k".to_string());
    client.retry_base_delay = Duration::ZERO;
    let (_, result) = stream(&client, &request("anthropic:claude-x"));
    assert!(
        matches!(&result, Err(ChatError::Transport(m)) if m.contains("does not implement post_json_stream"))
    );
}

#[test]
fn a_finished_stream_records_its_usage() {
    let model = "anthropic:claude-stream-usage-test";
    let transport = ScriptedStreams::new(vec![Answer::Status(200, ANTHROPIC_SSE)]);
    let (_, result) = stream(&client(transport), &request(model));
    result.expect("a response");
    let usage = with_token_tracker(|tracker| tracker.get_summary());
    let row = usage
        .by_model
        .get(model)
        .expect("the model's usage is recorded");
    assert_eq!((row.input_tokens, row.output_tokens), (12, 7));
}
