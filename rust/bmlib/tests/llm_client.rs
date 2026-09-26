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

//! The client router and a chat call over a scripted transport.
//!
//! Routing is pure, so most of this needs no transport at all. The calls that do
//! use a scripted `HttpClient` that records the request, which is what makes
//! "which URL was posted, with which headers" an assertion rather than a hope.

use bmlib::llm::{
    chat_body, chat_headers, chat_url, parse_model_string, resolve_target, supports_tools,
    ChatError, ChatRequest, LlmClient,
};
use bmlib::llm::{LLMMessage, LLMToolDefinition};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

/// One recorded request: the URL, the body, and the headers.
type Recorded = (String, Value, BTreeMap<String, String>);

/// A transport that answers with a scripted body and records what it was asked.
struct ScriptedClient {
    status: u16,
    body: String,
    requests: Mutex<Vec<Recorded>>,
}

impl ScriptedClient {
    fn ok(body: Value) -> Arc<Self> {
        Arc::new(ScriptedClient {
            status: 200,
            body: body.to_string(),
            requests: Mutex::new(Vec::new()),
        })
    }

    fn failing(status: u16, body: &str) -> Arc<Self> {
        Arc::new(ScriptedClient {
            status,
            body: body.to_string(),
            requests: Mutex::new(Vec::new()),
        })
    }

    fn last_request(&self) -> Recorded {
        self.requests
            .lock()
            .expect("lock")
            .last()
            .cloned()
            .expect("a request")
    }
}

impl HttpClient for ScriptedClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        Ok(HttpResponse::ok(format!("GET {url}")))
    }

    fn post_json(
        &self,
        url: &str,
        body: &Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.requests
            .lock()
            .expect("lock")
            .push((url.to_string(), body.clone(), headers.clone()));
        Ok(HttpResponse::from_bytes(
            self.status,
            self.body.clone().into_bytes(),
        ))
    }
}

fn client(transport: Arc<ScriptedClient>) -> LlmClient {
    let mut client = LlmClient::new(transport);
    // Explicit rather than from the environment: a test that reads a developer's
    // real key is a test whose result depends on their shell.
    client.api_key = Some("k".to_string());
    client
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

/// A `"provider:model"` string splits on the **first** colon only, because a
/// model name may contain one — Ollama tags are `name:tag`.
#[test]
fn a_model_string_splits_on_the_first_colon_only() {
    assert_eq!(
        parse_model_string(Some("anthropic:claude-sonnet-4"), "anthropic"),
        ("anthropic".to_string(), "claude-sonnet-4".to_string())
    );
    assert_eq!(
        parse_model_string(Some("ollama:medgemma:q8"), "anthropic"),
        ("ollama".to_string(), "medgemma:q8".to_string()),
        "an Ollama tag is part of the model name"
    );
    // No prefix: the default provider, and the whole string is the model.
    assert_eq!(
        parse_model_string(Some("gpt-4o"), "openai"),
        ("openai".to_string(), "gpt-4o".to_string())
    );
    assert_eq!(
        parse_model_string(None, "anthropic"),
        ("anthropic".to_string(), String::new())
    );
}

/// **The provider is lowercased.** A mixed-case `"Anthropic"` must route to the
/// same registry row as `"anthropic"`, or a caller's working configuration
/// breaks on capitalisation alone.
#[test]
fn a_mixed_case_provider_still_routes() {
    assert_eq!(
        parse_model_string(Some("Anthropic:claude-3"), "openai").0,
        "anthropic"
    );
    assert_eq!(
        parse_model_string(None, "OpenAI").0,
        "openai",
        "the default provider is lowercased too"
    );
}

/// An unknown provider is refused by name — not silently routed to the default,
/// which would send a request to the wrong company.
#[test]
fn an_unknown_provider_is_refused_by_name() {
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("ghost:model");
    let error = resolve_target(&request, "anthropic", None, Some("k")).expect_err("refused");
    match &error {
        ChatError::UnknownProvider(name) => assert_eq!(name, "ghost"),
        other => panic!("expected UnknownProvider, got {other:?}"),
    }
    assert!(error.to_string().contains("ghost"));
}

/// A provider that needs a credential and has none is refused **before** the
/// request goes out, naming the environment variable that would have supplied
/// it — a caller's next question is which variable to set.
#[test]
fn a_missing_credential_is_refused_before_the_request() {
    // A variable that certainly is not set.
    std::env::remove_var("ANTHROPIC_API_KEY");
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("anthropic:m");
    let error = resolve_target(&request, "anthropic", None, None).expect_err("refused");
    match &error {
        ChatError::MissingApiKey { provider, env_var } => {
            assert_eq!(provider, "anthropic");
            assert_eq!(env_var, "ANTHROPIC_API_KEY");
        }
        other => panic!("expected MissingApiKey, got {other:?}"),
    }
    let message = error.to_string();
    assert!(message.contains("ANTHROPIC_API_KEY"), "{message}");

    // An explicit key satisfies it.
    resolve_target(&request, "anthropic", None, Some("k")).expect("resolves");
    // And a local provider needs none at all.
    let local = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("ollama:llama3.2");
    resolve_target(&local, "anthropic", None, None).expect("a local provider needs no key");
}

/// **Tool calling is gated by a static allowlist**, and passing tools to a
/// provider outside it is refused rather than silently ignored: a model given no
/// tools answers as though none were needed, which reads as a model that chose
/// not to use them.
#[test]
fn tools_are_gated_by_the_allowlist() {
    assert!(supports_tools("anthropic"));
    assert!(supports_tools("Anthropic"));
    assert!(supports_tools("anthropic:claude-sonnet-4"));
    assert!(supports_tools("openai:gpt-4o"));
    assert!(supports_tools("ollama:llama3.2"));
    assert!(!supports_tools("some-local-server"));
    assert!(!supports_tools(""));

    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]);
    request.tools = Some(vec![LLMToolDefinition::new("add", "add", json!({}))]);
    request.model = Some("anthropic:m".to_string());
    resolve_target(&request, "anthropic", None, Some("k")).expect("anthropic supports tools");
}

/// An empty model name falls back to the provider's default, and a base URL
/// override is stripped of a trailing slash so the path does not double up.
#[test]
fn defaults_and_base_url_overrides_are_applied() {
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("anthropic");
    let target = resolve_target(&request, "anthropic", None, Some("k")).expect("resolves");
    assert_eq!(target.model, "claude-sonnet-4-20250514");

    let target = resolve_target(
        &request,
        "anthropic",
        Some("http://localhost:8080/"),
        Some("k"),
    )
    .expect("resolves");
    assert_eq!(target.base_url, "http://localhost:8080");
    assert_eq!(chat_url(&target), "http://localhost:8080/v1/messages");
}

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

/// **The credential goes where the protocol puts it**, and Anthropic carries its
/// version header: a provider with the wrong one gets a 401 that reads like a
/// bad key.
#[test]
fn headers_follow_the_protocol() {
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("anthropic:m");
    let target = resolve_target(&request, "anthropic", None, Some("secret")).expect("resolves");
    let headers = chat_headers(&target);
    assert_eq!(headers.get("x-api-key").map(String::as_str), Some("secret"));
    assert!(!headers.contains_key("Authorization"), "{headers:?}");
    assert!(headers.contains_key("anthropic-version"), "{headers:?}");

    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    let target = resolve_target(&request, "openai", None, Some("secret")).expect("resolves");
    let headers = chat_headers(&target);
    assert_eq!(
        headers.get("Authorization").map(String::as_str),
        Some("Bearer secret")
    );
    assert!(!headers.contains_key("x-api-key"), "{headers:?}");
    assert!(!headers.contains_key("anthropic-version"), "{headers:?}");
}

/// Anthropic takes the system prompt as a **separate parameter**, and an absent
/// one is omitted rather than sent as `""` — which Anthropic reads as a system
/// prompt that is the empty string.
#[test]
fn the_system_prompt_is_separate_on_anthropic() {
    let request = ChatRequest::new(vec![LLMMessage::system("be terse"), LLMMessage::user("hi")])
        .with_model("anthropic:m");
    let target = resolve_target(&request, "anthropic", None, Some("k")).expect("resolves");
    let body = chat_body(&request, &target, false);
    assert_eq!(body["system"], json!("be terse"));
    assert_eq!(body["messages"].as_array().expect("messages").len(), 1);
    assert_eq!(body["messages"][0]["role"], json!("user"));

    // No system message: no `system` key at all.
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("anthropic:m");
    let body = chat_body(&request, &target, false);
    assert!(body.get("system").is_none(), "{body:?}");
}

/// A reasoning model gets `max_completion_tokens` and **no `temperature`**,
/// which OpenAI's o-series rejects rather than ignores. On the OpenAI protocol
/// only — Anthropic has no such restriction and keeps its `temperature`.
#[test]
fn a_reasoning_model_drops_temperature_and_renames_the_cap() {
    let transport = ScriptedClient::ok(json!({"choices": [{"message": {"content": "x"}}]}));
    let client = client(transport);
    assert!(client.is_reasoning_model("o3-mini"));
    assert!(client.is_reasoning_model("openai:o1-preview"));
    assert!(!client.is_reasoning_model("gpt-4o"));

    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:o3-mini");
    let target = resolve_target(&request, "openai", None, Some("k")).expect("resolves");
    let body = chat_body(&request, &target, true);
    assert_eq!(body["max_completion_tokens"], json!(4096));
    assert!(body.get("max_tokens").is_none(), "{body:?}");
    assert!(body.get("temperature").is_none(), "{body:?}");

    // The same request on a non-reasoning model keeps both.
    let body = chat_body(&request, &target, false);
    assert_eq!(body["max_tokens"], json!(4096));
    assert_eq!(body["temperature"], json!(0.7));
}

/// `extra_body` is merged **last**, so a caller can set a field this port does
/// not know about — and can deliberately override one it does.
#[test]
fn extra_body_is_merged_last() {
    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    request.extra_body = Some(json!({"top_k": 5, "temperature": 0.1}));
    let target = resolve_target(&request, "openai", None, Some("k")).expect("resolves");
    let body = chat_body(&request, &target, false);
    assert_eq!(body["top_k"], json!(5), "an unknown field passes through");
    assert_eq!(
        body["temperature"],
        json!(0.1),
        "and a known one is overridden"
    );
}

/// Anthropic's `tool_choice` is **omitted** for the default rather than sent.
#[test]
fn anthropic_omits_the_default_tool_choice() {
    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("anthropic:m");
    request.tools = Some(vec![LLMToolDefinition::new("add", "add", json!({}))]);
    request.tool_choice = "auto".to_string();
    let target = resolve_target(&request, "anthropic", None, Some("k")).expect("resolves");
    let body = chat_body(&request, &target, false);
    assert!(body.get("tool_choice").is_none(), "{body:?}");
    assert_eq!(body["tools"][0]["input_schema"]["type"], json!("object"));

    request.tool_choice = "required".to_string();
    let body = chat_body(&request, &target, false);
    assert_eq!(body["tool_choice"], json!({"type": "any"}));

    // OpenAI keeps the parameter in every case.
    let mut openai = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    openai.tools = Some(vec![LLMToolDefinition::new("add", "add", json!({}))]);
    let target = resolve_target(&openai, "openai", None, Some("k")).expect("resolves");
    let body = chat_body(&openai, &target, false);
    assert_eq!(body["tool_choice"], json!("auto"));
    assert_eq!(body["tools"][0]["type"], json!("function"));
}

// ---------------------------------------------------------------------------
// The call
// ---------------------------------------------------------------------------

/// A call posts to the protocol's path, with the parsed response returned.
#[test]
fn a_call_posts_to_the_right_path_and_parses_the_answer() {
    let transport = ScriptedClient::ok(json!({
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2}
    }));
    let client = client(transport.clone());
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    let response = client.chat(&request).expect("answers");
    assert_eq!(response.content, "hello");
    assert_eq!(response.total_tokens, 5);

    let (url, body, headers) = transport.last_request();
    assert_eq!(url, "https://api.openai.com/v1/chat/completions");
    assert_eq!(body["model"], json!("gpt-4o"));
    assert_eq!(
        headers.get("Authorization").map(String::as_str),
        Some("Bearer k")
    );
}

/// A rejected request reports the server's own words, so a bad model and a bad
/// key do not read alike.
#[test]
fn a_rejected_request_quotes_the_server() {
    let transport = ScriptedClient::failing(401, r#"{"error": {"message": "invalid api key"}}"#);
    let client = client(transport);
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    let error = client.chat(&request).expect_err("refused");
    let message = error.to_string();
    assert!(message.contains("401"), "{message}");
    assert!(message.contains("invalid api key"), "{message}");
}

/// A 200 whose body is not JSON is reported as such, rather than as a model that
/// said nothing.
#[test]
fn a_non_json_body_is_reported() {
    let transport = ScriptedClient::failing(200, "<html>oops</html>");
    let client = client(transport);
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    let error = client.chat(&request).expect_err("refused");
    assert!(error.to_string().contains("not JSON"), "{error}");
}

/// A 200 carrying an error object and no choices is a **rejected request**, not
/// a silent model — caught one layer up when the server reports it that way.
#[test]
fn a_two_hundred_error_object_is_refused() {
    let transport = ScriptedClient::ok(json!({"error": {"message": "context length exceeded"}}));
    let client = client(transport);
    let request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model("openai:gpt-4o");
    let error = client.chat(&request).expect_err("refused");
    assert!(
        error.to_string().contains("context length exceeded"),
        "{error}"
    );
}
