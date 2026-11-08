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

use bmlib::llm::{
    anthropic_thinking, think_is_on, ChatError, ChatRequest, LLMMessage, LlmClient,
    CANCELLED_STOP_REASON,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse, HttpStream};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::ops::ControlFlow;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

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
    let fx = fixture();
    for case in fx["off"].as_array().unwrap() {
        let think = think_text(&case["think"]);
        assert!(!think_is_on(&think), "{}", case["name"]);
        // Off is no fields for any model, never thinking on or a refusal.
        for body in fx["models"].as_object().unwrap().values() {
            let fields = anthropic_thinking(&think, 4096, body).expect("off");
            assert!(fields.is_empty(), "{}: {fields:?}", case["name"]);
        }
    }
}

/// Each refusal says which rule it is: an adaptive model's unreported level
/// and a budget-only model's unknown one read differently.
#[test]
fn an_unsupported_effort_says_which_kind_of_model_refused_it() {
    let fx = fixture();
    let adaptive = anthropic_thinking("xhigh", 4096, &fx["models"]["both"]).unwrap_err();
    assert_eq!(
        adaptive.to_string(),
        "Anthropic model \"claude-opus-4-6\" does not report think effort level \"xhigh\""
    );
    let budget = anthropic_thinking("max", 4096, &fx["models"]["budget-only"]).unwrap_err();
    assert!(
        budget.to_string().contains("takes only a thinking budget"),
        "{budget}"
    );
    let err = ChatError::Thinking(budget.clone());
    assert_eq!(err.to_string(), budget.to_string());
}

type Seen = (String, String, Option<Value>);

/// One Anthropic stream answering "hello".
const SSE: &str = "event: message_start
data: {\"type\":\"message_start\",\"message\":{\"id\":\"m\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"m\",\"content\":[],\"usage\":{\"input_tokens\":1,\"output_tokens\":0}}}

event: content_block_start
data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}

event: content_block_delta
data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hello\"}}

event: content_block_stop
data: {\"type\":\"content_block_stop\",\"index\":0}

event: message_delta
data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":1}}

event: message_stop
data: {\"type\":\"message_stop\"}

";

/// Answers a GET with `model_body` (or `get_status`), a POST with a message
/// and a streamed POST with [`SSE`].
struct Fake {
    model_body: Value,
    get_status: u16,
    /// How many lookups answer 503 before `get_status` does.
    get_failures: Mutex<u32>,
    seen: Mutex<Vec<Seen>>,
}

impl Fake {
    fn new(model_body: Value, get_status: u16) -> Arc<Self> {
        Arc::new(Fake {
            model_body,
            get_status,
            get_failures: Mutex::new(0),
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
        let mut failures = self.get_failures.lock().unwrap();
        if *failures > 0 {
            *failures -= 1;
            return Ok(HttpResponse {
                status: 503,
                body: Vec::new(),
            });
        }
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

    fn post_json_stream(
        &self,
        url: &str,
        body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        self.seen
            .lock()
            .unwrap()
            .push(("POST".into(), url.into(), Some(body.clone())));
        Ok(HttpStream {
            status: 200,
            body: Box::new(SSE.as_bytes()),
        })
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
    assert_eq!(
        body["thinking"],
        json!({"type": "adaptive", "display": "summarized"})
    );
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
    for model in ["openai:gpt-x", "ollama:qwen3"] {
        let fake = Fake::new(json!({}), 200);
        // The fake's answer is Anthropic's, so the chat may not read; only
        // what was sent matters.
        let _ = client(&fake).chat(&request(model, Some("true")));
        let seen = fake.seen();
        assert_eq!(seen.len(), 1, "{model}: {seen:?}");
        assert_eq!(seen[0].0, "POST", "{model}");
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
    assert_eq!(
        body["thinking"],
        json!({"type": "adaptive", "display": "summarized"})
    );
}

#[test]
fn an_output_config_from_extra_body_keeps_its_keys_and_gains_the_effort() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    let mut req = request("anthropic:claude-opus-5-5", Some("high"));
    req.extra_body = Some(json!({"output_config": {"format": {"type": "json_schema"}}}));
    client(&fake).chat(&req).expect("chat");
    let body = fake.seen()[1].2.clone().unwrap();
    assert_eq!(
        body["output_config"],
        json!({"format": {"type": "json_schema"}, "effort": "high"})
    );
}

#[test]
fn a_thinking_from_extra_body_is_left_whole() {
    // Merged key by key, the looked-up budget would land beside `adaptive`.
    let fake = Fake::new(fixture()["models"]["both"].clone(), 200);
    let mut req = request("anthropic:claude-opus-4-6", Some("4096"));
    req.max_tokens = 16000;
    req.extra_body = Some(json!({"thinking": {"type": "adaptive"}}));
    client(&fake).chat(&req).expect("chat");
    let body = fake.seen()[1].2.clone().unwrap();
    assert_eq!(body["thinking"], json!({"type": "adaptive"}));
}

#[test]
fn a_lookup_body_that_is_not_json_says_why_and_sends_no_chat() {
    struct NotJson;
    impl HttpClient for NotJson {
        fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
            Ok(HttpResponse::ok(b"<html>".to_vec()))
        }
        fn get_with_headers(
            &self,
            url: &str,
            _headers: &BTreeMap<String, String>,
        ) -> Result<HttpResponse, FetchError> {
            self.get(url)
        }
        fn post_json(
            &self,
            url: &str,
            _body: &Value,
            _headers: &BTreeMap<String, String>,
        ) -> Result<HttpResponse, FetchError> {
            panic!("no chat may be sent: POST {url}")
        }
    }
    let mut client = LlmClient::new(Arc::new(NotJson));
    client.api_key = Some("k".to_string());
    match client.chat(&request("anthropic:claude-opus-5-5", Some("true"))) {
        Err(ChatError::Malformed(m)) => {
            assert!(m.contains("/v1/models/claude-opus-5-5"), "{m}");
            assert!(
                m.contains("returned a body that is not JSON: expected value"),
                "{m}"
            );
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn both_streams_send_the_looked_up_fields() {
    for cancellable in [false, true] {
        let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
        let req = request("anthropic:claude-opus-5-5", Some("high"));
        let mut on_delta = |_: &_| ControlFlow::Continue(());
        let response = if cancellable {
            client(&fake).chat_stream_with_cancel(&req, &AtomicBool::new(false), &mut on_delta)
        } else {
            client(&fake).chat_stream(&req, &mut on_delta)
        }
        .expect("stream");
        assert_eq!(response.content, "hello", "{cancellable}");
        let seen = fake.seen();
        assert_eq!(seen.len(), 2, "{cancellable}: {seen:?}");
        assert_eq!(seen[0].0, "GET");
        let body = seen[1].2.as_ref().unwrap();
        assert_eq!(
            body["thinking"],
            json!({"type": "adaptive", "display": "summarized"}),
            "{cancellable}"
        );
        assert_eq!(
            body["output_config"],
            json!({"effort": "high"}),
            "{cancellable}"
        );
    }
}

#[test]
fn a_cancel_already_set_makes_no_lookup() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    let response = client(&fake)
        .chat_stream_with_cancel(
            &request("anthropic:claude-opus-5-5", Some("true")),
            &AtomicBool::new(true),
            &mut |_| ControlFlow::Continue(()),
        )
        .expect("cancelled");
    assert_eq!(response.stop_reason.as_deref(), Some(CANCELLED_STOP_REASON));
    assert!(fake.seen().is_empty(), "{:?}", fake.seen());
}

/// A model lookup that does not answer until released.
struct StalledLookup {
    entered: Mutex<mpsc::Sender<()>>,
    release: Mutex<mpsc::Receiver<()>>,
    /// What the lookup answers once released: `None` is a transport failure.
    answer: Option<Value>,
    /// Told of every stream opened.
    opened: Mutex<mpsc::Sender<()>>,
}

impl HttpClient for StalledLookup {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.get_with_headers(url, &BTreeMap::new())
    }
    fn get_with_headers(
        &self,
        _url: &str,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let _ = self.entered.lock().unwrap().send(());
        let _ = self.release.lock().unwrap().recv();
        match &self.answer {
            Some(body) => Ok(HttpResponse::ok(body.to_string().into_bytes())),
            None => Err(FetchError::Transport("released".into())),
        }
    }
    fn post_json_stream(
        &self,
        url: &str,
        _body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        let _ = self.opened.lock().unwrap().send(());
        Err(FetchError::Transport(format!("opened {url}")))
    }
    fn post_json(
        &self,
        url: &str,
        _body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        panic!("no chat may be sent: POST {url}")
    }
}

#[test]
fn a_cancel_stops_a_stalled_lookup_at_once() {
    let (entered_tx, entered) = mpsc::channel();
    let (release, release_rx) = mpsc::channel::<()>();
    let (opened_tx, _opened) = mpsc::channel();
    let mut client = LlmClient::new(Arc::new(StalledLookup {
        entered: Mutex::new(entered_tx),
        release: Mutex::new(release_rx),
        answer: None,
        opened: Mutex::new(opened_tx),
    }));
    client.api_key = Some("k".to_string());
    let cancel = Arc::new(AtomicBool::new(false));
    let setter = {
        let cancel = Arc::clone(&cancel);
        std::thread::spawn(move || {
            entered.recv().expect("the lookup was made");
            cancel.store(true, Ordering::SeqCst);
        })
    };
    let started = Instant::now();
    let response = client
        .chat_stream_with_cancel(
            &request("anthropic:claude-opus-5-5", Some("true")),
            &cancel,
            &mut |_| ControlFlow::Continue(()),
        )
        .expect("cancelled");
    assert_eq!(response.stop_reason.as_deref(), Some(CANCELLED_STOP_REASON));
    assert!(
        started.elapsed() < Duration::from_secs(5),
        "{:?}",
        started.elapsed()
    );
    setter.join().unwrap();
    drop(release);
}

/// Both streams refuse before they open: a refusal, and a refused lookup,
/// are the caller's error and no stream is sent without the thinking.
#[test]
fn a_stream_asks_the_model_too() {
    for cancellable in [false, true] {
        for (body, status) in [
            (fixture()["models"]["adaptive-only"].clone(), 200),
            (json!({}), 401),
        ] {
            let fake = Fake::new(body, status);
            let req = request("anthropic:claude-opus-5-5", Some("4096"));
            let mut on_delta = |_: &_| ControlFlow::Continue(());
            let err = if cancellable {
                client(&fake).chat_stream_with_cancel(&req, &AtomicBool::new(false), &mut on_delta)
            } else {
                client(&fake).chat_stream(&req, &mut on_delta)
            }
            .expect_err("refused before the stream opens");
            match (&err, status) {
                (ChatError::Thinking(r), 200) => assert_eq!(r.reason(), "budget-not-supported"),
                (ChatError::Http { status: 401, .. }, 401) => {}
                other => panic!("{cancellable}: {other:?}"),
            }
            assert_eq!(
                fake.seen().len(),
                1,
                "{cancellable} {status}: {:?}",
                fake.seen()
            );
        }
    }
}

/// A lookup answering 503 is retried, as opening a stream is.
#[test]
fn a_lookup_answering_503_is_retried() {
    let fake = Fake::new(fixture()["models"]["adaptive-only"].clone(), 200);
    *fake.get_failures.lock().unwrap() = 1;
    client(&fake)
        .chat(&request("anthropic:claude-opus-5-5", Some("true")))
        .expect("chat");
    let seen = fake.seen();
    let methods: Vec<&str> = seen.iter().map(|s| s.0.as_str()).collect();
    assert_eq!(methods, ["GET", "GET", "POST"], "{seen:?}");
    assert_eq!(
        seen[2].2.as_ref().unwrap()["thinking"],
        json!({"type": "adaptive", "display": "summarized"})
    );
}

/// A lookup that fails names its URL, and only a transport failure is
/// retryable: a client that cannot make the lookup is a setup error.
#[test]
fn a_failed_lookup_names_its_url() {
    struct Fails(FetchError);
    impl HttpClient for Fails {
        fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
            Err(self.0.clone())
        }
        fn get_with_headers(
            &self,
            url: &str,
            _headers: &BTreeMap<String, String>,
        ) -> Result<HttpResponse, FetchError> {
            self.get(url)
        }
        fn post_json(
            &self,
            url: &str,
            _body: &Value,
            _headers: &BTreeMap<String, String>,
        ) -> Result<HttpResponse, FetchError> {
            panic!("no chat may be sent: POST {url}")
        }
    }
    let cases = [
        (FetchError::Transport("timed out".into()), true),
        (FetchError::Config("no GET with headers".into()), false),
        (
            FetchError::HttpStatus {
                url: "u".into(),
                status: 404,
            },
            false,
        ),
    ];
    for (error, retryable) in cases {
        let mut client = LlmClient::new(Arc::new(Fails(error.clone())));
        client.api_key = Some("k".to_string());
        client.retry_base_delay = Duration::ZERO;
        let err = client
            .chat(&request("anthropic:claude-opus-5-5", Some("true")))
            .expect_err("the lookup failed");
        assert!(
            err.to_string().contains("/v1/models/claude-opus-5-5"),
            "{err}"
        );
        assert_eq!(err.is_retryable(), retryable, "{error:?}: {err:?}");
        match (&error, &err) {
            (FetchError::Transport(_), ChatError::Transport(_))
            | (FetchError::Config(_), ChatError::Malformed(_))
            | (FetchError::HttpStatus { .. }, ChatError::Http { status: 404, .. }) => {}
            other => panic!("{other:?}"),
        }
    }
}

/// A budget is held below the `max_tokens` the body sends, `extra_body`'s.
#[test]
fn a_budget_stays_below_the_max_tokens_extra_body_sets() {
    let fake = Fake::new(fixture()["models"]["budget-only"].clone(), 200);
    let mut req = request("anthropic:claude-haiku-4-5", Some("16384"));
    req.max_tokens = 32000;
    req.extra_body = Some(json!({"max_tokens": 2000}));
    client(&fake).chat(&req).expect("chat");
    let body = fake.seen()[1].2.clone().unwrap();
    assert_eq!(body["max_tokens"], json!(2000));
    assert_eq!(
        body["thinking"],
        json!({"type": "enabled", "budget_tokens": 1999})
    );
}

/// A lookup body naming no model (a proxy's stub) is refused naming the
/// model asked for.
#[test]
fn a_refusal_names_the_model_asked_for_when_the_body_names_none() {
    let fake = Fake::new(json!({"type": "model"}), 200);
    let err = client(&fake)
        .chat(&request("anthropic:claude-opus-5-5", Some("true")))
        .expect_err("refused");
    assert!(err.to_string().contains("\"claude-opus-5-5\""), "{err}");
}

/// A cancel that arrives while the lookup is in flight opens no stream once
/// the lookup answers: the caller has gone, and the chat would be billed.
#[test]
fn a_lookup_that_answers_after_a_cancel_opens_no_stream() {
    let (entered_tx, entered) = mpsc::channel();
    let (release, release_rx) = mpsc::channel::<()>();
    let (opened_tx, opened) = mpsc::channel();
    let mut client = LlmClient::new(Arc::new(StalledLookup {
        entered: Mutex::new(entered_tx),
        release: Mutex::new(release_rx),
        answer: Some(fixture()["models"]["adaptive-only"].clone()),
        opened: Mutex::new(opened_tx),
    }));
    client.api_key = Some("k".to_string());
    let cancel = Arc::new(AtomicBool::new(false));
    let setter = {
        let cancel = Arc::clone(&cancel);
        std::thread::spawn(move || {
            entered.recv().expect("the lookup was made");
            cancel.store(true, Ordering::SeqCst);
        })
    };
    let response = client
        .chat_stream_with_cancel(
            &request("anthropic:claude-opus-5-5", Some("true")),
            &cancel,
            &mut |_| ControlFlow::Continue(()),
        )
        .expect("cancelled");
    assert_eq!(response.stop_reason.as_deref(), Some(CANCELLED_STOP_REASON));
    setter.join().unwrap();
    release.send(()).expect("the worker is waiting");
    assert!(
        opened.recv_timeout(Duration::from_secs(1)).is_err(),
        "a stream was opened after the cancel"
    );
}
