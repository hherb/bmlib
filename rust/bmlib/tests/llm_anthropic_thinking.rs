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

//! The shared Anthropic thinking contract, `tests/fixtures/anthropic_thinking.json`
//! (the file Python's `tests/test_anthropic_thinking_contract.py` reads), and
//! `LlmClient`'s lookup of a model's metadata before a thinking chat (#707).

use bmlib::llm::{anthropic_thinking, think_is_on, ChatError, ChatRequest, LLMMessage, LlmClient};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

const FIXTURE: &str = include_str!("../../../tests/fixtures/anthropic_thinking.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("fixture is JSON")
}

/// Python's `think` value as the text Rust carries it in.
fn think_text(think: &Value) -> String {
    match think {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

#[test]
fn every_case() {
    let fx = fixture();
    for case in fx["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let body = &fx["models"][case["model"].as_str().unwrap()];
        let think = think_text(&case["think"]);
        assert!(think_is_on(&think), "{name}");
        let got = anthropic_thinking(&think, case["max_tokens"].as_i64().unwrap(), body);
        match case["expect"].get("refusal") {
            Some(reason) => {
                let refusal = got.expect_err(name);
                assert_eq!(refusal.reason(), reason.as_str().unwrap(), "{name}");
            }
            None => assert_eq!(
                Value::Object(got.expect(name)),
                case["expect"]["fields"],
                "{name}"
            ),
        }
    }
}

#[test]
fn off_is_off() {
    for case in fixture()["off"].as_array().unwrap() {
        assert!(
            !think_is_on(&think_text(&case["think"])),
            "{}",
            case["name"]
        );
    }
}

type Seen = (String, String, Option<Value>);

/// Answers a GET with `model_body` (or `get_status`) and a POST with a message.
struct Fake {
    model_body: Value,
    get_status: u16,
    seen: Mutex<Vec<Seen>>,
}

impl Fake {
    fn new(model_body: Value, get_status: u16) -> Arc<Self> {
        Arc::new(Fake {
            model_body,
            get_status,
            seen: Mutex::new(Vec::new()),
        })
    }

    fn seen(&self) -> Vec<Seen> {
        self.seen.lock().unwrap().clone()
    }
}

impl HttpClient for Fake {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.get_with_headers(url, &BTreeMap::new())
    }

    fn get_with_headers(
        &self,
        url: &str,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.seen
            .lock()
            .unwrap()
            .push(("GET".into(), url.into(), None));
        Ok(HttpResponse {
            status: self.get_status,
            body: self.model_body.to_string().into_bytes(),
        })
    }

    fn post_json(
        &self,
        url: &str,
        body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.seen
            .lock()
            .unwrap()
            .push(("POST".into(), url.into(), Some(body.clone())));
        Ok(HttpResponse::ok(
            json!({
                "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                "content": [{"type": "text", "text": "hello"}],
                "stop_reason": "end_turn", "stop_sequence": null,
                "usage": {"input_tokens": 3, "output_tokens": 1}
            })
            .to_string()
            .into_bytes(),
        ))
    }
}

fn client(fake: &Arc<Fake>) -> LlmClient {
    let mut client = LlmClient::new(fake.clone());
    client.api_key = Some("k".to_string());
    client.retry_base_delay = Duration::ZERO;
    client
}

fn request(model: &str, think: Option<&str>) -> ChatRequest {
    let mut request = ChatRequest::new(vec![LLMMessage::user("hi")]).with_model(model);
    request.think = think.map(str::to_string);
    request
}

#[test]
fn the_model_is_asked_before_the_chat_and_its_shape_is_sent() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    let response = client(&fake)
        .chat(&request("anthropic:claude-opus-5-5", Some("xhigh")))
        .expect("chat");
    assert_eq!(response.content, "hello");
    let seen = fake.seen();
    assert_eq!(seen.len(), 2, "{seen:?}");
    assert_eq!(seen[0].0, "GET");
    assert!(
        seen[0].1.ends_with("/v1/models/claude-opus-5-5"),
        "{}",
        seen[0].1
    );
    let body = seen[1].2.as_ref().unwrap();
    assert_eq!(body["thinking"], json!({"type": "adaptive"}));
    assert_eq!(body["output_config"], json!({"effort": "xhigh"}));
}

#[test]
fn a_budget_only_model_gets_a_budget() {
    let fake = Fake::new(fixture()["models"]["budget-only"].clone(), 200);
    let mut req = request("anthropic:claude-haiku-4-5", Some("true"));
    req.max_tokens = 16000;
    client(&fake).chat(&req).expect("chat");
    let body = fake.seen()[1].2.clone().unwrap();
    assert_eq!(
        body["thinking"],
        json!({"type": "enabled", "budget_tokens": 8192})
    );
    assert!(body.get("output_config").is_none(), "{body:?}");
}

#[test]
fn a_refusal_sends_no_chat() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    let err = client(&fake)
        .chat(&request("anthropic:claude-opus-5-5", Some("4096")))
        .expect_err("refused");
    match &err {
        ChatError::Thinking(refusal) => assert_eq!(refusal.reason(), "budget-not-supported"),
        other => panic!("{other:?}"),
    }
    assert!(!err.is_retryable());
    assert_eq!(fake.seen().len(), 1, "only the lookup");
}

#[test]
fn a_refused_lookup_is_the_http_error_and_sends_no_chat() {
    let fake = Fake::new(json!({"error": {"message": "invalid x-api-key"}}), 401);
    let err = client(&fake)
        .chat(&request("anthropic:claude-opus-5-5", Some("true")))
        .expect_err("refused");
    match err {
        ChatError::Http { status, message } => {
            assert_eq!(status, 401);
            assert!(message.contains("invalid x-api-key"), "{message}");
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(fake.seen().len(), 1);
}

#[test]
fn no_lookup_when_think_is_off_or_the_protocol_is_not_anthropic() {
    for think in [None, Some(""), Some("false"), Some("0")] {
        let fake = Fake::new(json!({}), 200);
        client(&fake)
            .chat(&request("anthropic:claude-opus-5-5", think))
            .expect("chat");
        let seen = fake.seen();
        assert_eq!(seen.len(), 1, "{think:?}: {seen:?}");
        assert!(seen[0].2.as_ref().unwrap().get("thinking").is_none());
    }
}

#[test]
fn extra_body_wins_over_the_looked_up_fields() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    let mut req = request("anthropic:claude-opus-5-5", Some("high"));
    req.extra_body = Some(json!({"output_config": {"effort": "low"}}));
    client(&fake).chat(&req).expect("chat");
    let body = fake.seen()[1].2.clone().unwrap();
    assert_eq!(body["output_config"], json!({"effort": "low"}));
    assert_eq!(body["thinking"], json!({"type": "adaptive"}));
}

#[test]
fn a_stream_asks_the_model_too() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    let err = client(&fake)
        .chat_stream(
            &request("anthropic:claude-opus-5-5", Some("4096")),
            &mut |_| std::ops::ControlFlow::Continue(()),
        )
        .expect_err("refused before the stream opens");
    assert!(matches!(err, ChatError::Thinking(_)), "{err:?}");
    assert_eq!(fake.seen().len(), 1);
}
