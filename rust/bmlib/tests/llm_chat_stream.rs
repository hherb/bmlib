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
    LLMToolDefinition, LlmClient, StreamDelta, ERROR_BODY_LIMIT, RETRYABLE_STATUSES,
    STREAM_OPEN_ATTEMPTS,
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
    /// The request could not be made at all (`FetchError::Config`).
    CannotSend,
}

/// A transport that answers each stream with the next scripted answer and
/// records the body it was posted.
struct ScriptedStreams {
    answers: Mutex<VecDeque<Answer>>,
    posted: Mutex<Vec<(String, Value)>>,
    headers: Mutex<Vec<BTreeMap<String, String>>>,
}

impl ScriptedStreams {
    fn new(answers: Vec<Answer>) -> Arc<Self> {
        Arc::new(ScriptedStreams {
            answers: Mutex::new(answers.into()),
            posted: Mutex::new(Vec::new()),
            headers: Mutex::new(Vec::new()),
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
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        self.headers.lock().expect("lock").push(headers.clone());
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
            Some(Answer::CannotSend) => Err(FetchError::Config("cannot stream".to_string())),
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
fn an_event_that_is_not_json_is_a_stream_error() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, "data: {nope\n\n")]);
    let (_, result) = stream(&client(transport), &request("openai:gpt-x"));
    assert!(matches!(result, Err(ChatError::Stream(m)) if m.contains("not JSON")));
}

#[test]
fn a_line_that_is_not_utf8_is_a_stream_error() {
    struct NotUtf8;
    impl HttpClient for NotUtf8 {
        fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
            unreachable!()
        }
        fn post_json_stream(
            &self,
            _url: &str,
            _body: &Value,
            _headers: &BTreeMap<String, String>,
        ) -> Result<HttpStream, FetchError> {
            Ok(HttpStream {
                status: 200,
                body: Box::new(&b"data: \xff\n\n"[..]),
            })
        }
    }
    let mut client = LlmClient::new(Arc::new(NotUtf8));
    client.api_key = Some("k".to_string());
    let (_, result) = stream(&client, &request("openai:gpt-x"));
    assert!(matches!(result, Err(ChatError::Stream(m)) if m.contains("not UTF-8")));
}

#[test]
fn the_request_carries_the_providers_credentials() {
    let transport = ScriptedStreams::new(vec![
        Answer::Status(200, ANTHROPIC_SSE),
        Answer::Status(200, OPENAI_SSE),
    ]);
    let client = client(transport.clone());
    stream(&client, &request("anthropic:claude-x"))
        .1
        .expect("anthropic");
    stream(&client, &request("openai:gpt-x")).1.expect("openai");
    let headers = transport.headers.lock().expect("lock").clone();
    assert_eq!(headers[0].get("x-api-key").map(String::as_str), Some("k"));
    assert!(headers[0].contains_key("anthropic-version"));
    assert_eq!(
        headers[1].get("Authorization").map(String::as_str),
        Some("Bearer k")
    );
}

#[test]
fn nothing_is_retried_once_the_stream_has_opened() {
    const CUT: &str = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Yes\"}}]}\n\n";
    let transport = ScriptedStreams::new(vec![
        Answer::Status(200, CUT),
        Answer::Status(200, OPENAI_SSE),
    ]);
    let (seen, result) = stream(&client(transport.clone()), &request("openai:gpt-x"));
    assert_eq!(seen, [StreamDelta::Text("Yes".to_string())]);
    assert!(matches!(result, Err(ChatError::Stream(_))));
    assert_eq!(transport.posted().len(), 1);
}

#[test]
fn a_request_that_cannot_be_sent_is_not_retried() {
    let transport =
        ScriptedStreams::new(vec![Answer::CannotSend, Answer::Status(200, ANTHROPIC_SSE)]);
    let (_, result) = stream(&client(transport.clone()), &request("anthropic:claude-x"));
    assert!(matches!(&result, Err(ChatError::Transport(m)) if m.contains("cannot stream")));
    assert_eq!(transport.posted().len(), 1);
}

#[test]
fn a_refused_requests_body_is_read_only_so_far() {
    let message = "x".repeat(2 * ERROR_BODY_LIMIT as usize);
    let body: &'static str =
        Box::leak(format!("{{\"error\":{{\"message\":\"{message}\"}}}}").into_boxed_str());
    let transport = ScriptedStreams::new(vec![Answer::Status(400, body)]);
    let (_, result) = stream(&client(transport), &request("anthropic:claude-x"));
    // Cut short, the body is no longer JSON, so its first 200 characters are quoted.
    assert!(
        matches!(&result, Err(ChatError::Transport(m)) if m.len() < 1_000),
        "{result:?}"
    );
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
fn a_stopped_stream_records_the_usage_sent_so_far() {
    let model = "anthropic:claude-stream-stop-usage-test";
    let transport = ScriptedStreams::new(vec![Answer::Status(200, ANTHROPIC_SSE)]);
    let response = client(transport)
        .chat_stream(&request(model), &mut |_| ControlFlow::Break(()))
        .expect("a response");
    assert_eq!(response.stop_reason.as_deref(), Some("cancelled"));
    let usage = with_token_tracker(|tracker| tracker.get_summary());
    let row = usage
        .by_model
        .get(model)
        .expect("the model's usage is recorded");
    assert_eq!((row.input_tokens, row.output_tokens), (12, 1));
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

const OLLAMA_NDJSON: &str = "{\"message\":{\"role\":\"assistant\",\"content\":\"Blinded\"},\"done\":false}\n\
\n\
{\"message\":{\"role\":\"assistant\",\"content\":\", yes.\"},\"done\":false}\r\n\
{\"message\":{\"content\":\"\"},\"done\":true,\"done_reason\":\"stop\",\"prompt_eval_count\":11,\"eval_count\":4}\n";

#[test]
fn an_ollama_stream_is_read_as_ndjson_and_carries_num_ctx() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, OLLAMA_NDJSON)]);
    let mut request = request("ollama:llama-x");
    request.num_ctx = std::num::NonZeroU32::new(8192);
    let (seen, result) = stream(&client(transport.clone()), &request);
    let response = result.expect("a response");
    assert_eq!(
        seen,
        [
            StreamDelta::Text("Blinded".to_string()),
            StreamDelta::Text(", yes.".to_string())
        ]
    );
    assert_eq!(response.content, "Blinded, yes.");
    assert_eq!((response.input_tokens, response.output_tokens), (11, 4));
    assert_eq!(response.stop_reason.as_deref(), Some("stop"));
    let (url, body) = transport.posted().pop().expect("a post");
    assert!(url.ends_with("/api/chat"), "{url}");
    assert_eq!(body["stream"], json!(true));
    assert_eq!(body["options"]["num_ctx"], json!(8192));
}

#[test]
fn an_ollama_stream_without_done_is_an_error() {
    const CUT: &str = "{\"message\":{\"content\":\"Yes\"},\"done\":false}\n";
    let transport = ScriptedStreams::new(vec![Answer::Status(200, CUT)]);
    let (seen, result) = stream(&client(transport), &request("ollama:llama-x"));
    assert_eq!(seen.len(), 1);
    assert!(matches!(result, Err(ChatError::Stream(m)) if m.contains("ended before done")));
}

#[test]
fn an_ollama_line_that_is_not_json_is_a_stream_error() {
    let transport = ScriptedStreams::new(vec![Answer::Status(200, "not json\n")]);
    let (_, result) = stream(&client(transport), &request("ollama:llama-x"));
    assert!(matches!(result, Err(ChatError::Stream(m)) if m.contains("not JSON")));
}
