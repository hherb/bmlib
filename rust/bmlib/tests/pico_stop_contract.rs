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

//! The PICO agent's `num_ctx` and stop, against the shared fixture
//! `tests/fixtures/pico_stop_num_ctx.json` (#640, #641, #642).
//!
//! Python's `TestTheStopAndNumCtxContract` runs the same cases. Each runs the
//! real agent, the real map-reduce and a real [`LlmClient`] over a fake Ollama
//! transport, so what is pinned is what reaches the wire: the `num_ctx` in
//! every request body and how many requests a stop lets through.

use std::collections::BTreeMap;
use std::num::NonZeroU32;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use bmlib::context_processor::ProcessingConfig;
use bmlib::llm::LlmClient;
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::quality::pico_agent::PicoAgent;
use bmlib::quality::{LlmChat, LlmContextModel, PicoError};
use serde_json::{json, Value};

const FIXTURE: &str = include_str!("../../../tests/fixtures/pico_stop_num_ctx.json");

/// An Ollama server that answers JSON mode with the fixture's reply and plain
/// requests with its digest, and sets the stop as the case's request arrives.
struct FakeOllama {
    json_reply: String,
    digest: String,
    stop: Arc<AtomicBool>,
    stop_at: Option<usize>,
    fail_at_stop: bool,
    fail_at: Vec<usize>,
    malformed_at: Option<usize>,
    /// Each request's `options.num_ctx`, `None` where the key is absent.
    num_ctx_sent: Mutex<Vec<Option<Value>>>,
}

impl HttpClient for FakeOllama {
    fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport("this fake only serves POST".into()))
    }

    fn post_json(
        &self,
        _url: &str,
        body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let index = {
            let mut sent = self.num_ctx_sent.lock().expect("lock");
            sent.push(body["options"].get("num_ctx").cloned());
            sent.len() - 1
        };
        if self.fail_at.contains(&index) {
            return Err(FetchError::Transport("the connection was reset".into()));
        }
        if self.malformed_at == Some(index) {
            return Ok(HttpResponse::from_bytes(200, b"not json".to_vec()));
        }
        if self.stop_at == Some(index) {
            self.stop.store(true, Ordering::SeqCst);
            if self.fail_at_stop {
                return Err(FetchError::Transport("the server went away".into()));
            }
        }
        let content = if body["format"] == json!("json") {
            &self.json_reply
        } else {
            &self.digest
        };
        let answer = json!({
            "message": {"role": "assistant", "content": content},
            "done": true,
            "done_reason": "stop",
        });
        Ok(HttpResponse::from_bytes(
            200,
            answer.to_string().into_bytes(),
        ))
    }
}

/// Run one case; the `num_ctx` of each request and the error kind.
fn run(fixture: &Value, case: &Value) -> (Vec<Option<Value>>, Option<&'static str>) {
    let stop = Arc::new(AtomicBool::new(false));
    let transport = Arc::new(FakeOllama {
        json_reply: fixture["json_reply"].to_string(),
        digest: fixture["digest"].as_str().expect("digest").to_string(),
        stop: stop.clone(),
        stop_at: case["stop_at_request"].as_u64().map(|k| k as usize),
        fail_at_stop: case["request_at_stop"] == json!("fails"),
        fail_at: case["fail_requests"]
            .as_array()
            .map(|ks| {
                ks.iter()
                    .map(|k| k.as_u64().expect("an index") as usize)
                    .collect()
            })
            .unwrap_or_default(),
        malformed_at: case["malformed_request"].as_u64().map(|k| k as usize),
        num_ctx_sent: Mutex::new(Vec::new()),
    });
    let mut client = LlmClient::new(transport.clone());
    client.retry_base_delay = Duration::ZERO;
    let model = fixture["model"].as_str().expect("model");
    let num_ctx = case["num_ctx"]
        .as_u64()
        .and_then(|n| NonZeroU32::new(u32::try_from(n).expect("a u32")));

    let mut chat = LlmChat::new(&client, model)
        .with_num_ctx(num_ctx)
        .with_stop(&stop);
    let condenser_model = LlmContextModel::new(&client, model)
        .with_num_ctx(num_ctx)
        .with_stop(&stop);
    let mut condenser =
        PicoAgent::pico_condenser(&condenser_model, 0.1, 4096).expect("the PICO prompts");
    let budget = fixture["max_context_chars"].as_u64().expect("budget") as usize;
    let mut agent = PicoAgent::new(&mut chat)
        .with_condenser(&mut condenser)
        .with_condense_config(ProcessingConfig::default().with_max_context_chars(budget))
        .with_stop(&stop);
    if case["stop_before"] == json!(true) {
        stop.store(true, Ordering::SeqCst);
    }

    let title = fixture["title"].as_str().expect("title");
    let text = fixture["text_unit"]
        .as_str()
        .expect("text_unit")
        .repeat(case["text_repeat"].as_u64().expect("text_repeat") as usize);
    let similarity = fixture["min_similarity"].as_f64().expect("similarity");
    let outcome: Result<(), PicoError> = match case["fn"].as_str().expect("fn") {
        "extract" => agent.extract(title, &text, similarity).map(drop),
        "suitability" => agent.check_suitability(title, &text).map(drop),
        "prepare" => agent.prepare(title, &text).map(drop),
        "prepared" => agent.prepare(title, &text).and_then(|prepared| {
            assert!(prepared.digest().is_some(), "a prepared case condenses");
            agent.check_suitability_prepared(&prepared)?;
            agent.extract_prepared(&prepared, similarity).map(drop)
        }),
        other => panic!("unknown fn {other}"),
    };
    drop(agent);
    let sent = transport.num_ctx_sent.lock().expect("lock").clone();
    (sent, outcome.err().map(|error| error.kind()))
}

#[test]
fn the_stopped_kind_is_the_fixtures() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture parses");
    assert_eq!(
        PicoError::Stopped(None).kind(),
        fixture["stopped_kind"].as_str().expect("stopped_kind")
    );
}

/// The retry cases' counts assume this many tries, as Python's do.
#[test]
fn the_attempts_are_the_fixtures() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture parses");
    assert_eq!(
        u64::from(bmlib::quality::CONTEXT_MODEL_ATTEMPTS),
        fixture["context_model_attempts"]
            .as_u64()
            .expect("context_model_attempts")
    );
}

#[test]
fn every_case_agrees_with_the_fixture() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture parses");
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let (sent, error) = run(&fixture, case);
        let expected = &case["expected"];
        assert_eq!(error, expected["error"].as_str(), "{name}: the error kind");
        assert_eq!(
            sent.len() as u64,
            expected["requests"].as_u64().expect("requests"),
            "{name}: the requests sent"
        );
        // A null `num_ctx` sends no key at all.
        let expected_num_ctx = Some(case["num_ctx"].clone()).filter(|n| !n.is_null());
        for num_ctx in &sent {
            assert_eq!(num_ctx, &expected_num_ctx, "{name}: a request's num_ctx");
        }
    }
}

// ---------------------------------------------------------------------------
// LlmContextModel's own rules (#641)
// ---------------------------------------------------------------------------

/// One scripted answer per request: `Ok((status, Ollama content, done_reason))`
/// or a transport fault; the last repeats.
struct Scripted {
    answers: Vec<Result<(u16, String, String), String>>,
    requests: Mutex<Vec<Value>>,
    stop_on_request: Option<Arc<AtomicBool>>,
}

impl Scripted {
    fn new(answers: Vec<Result<(u16, &str, &str), &str>>) -> Arc<Self> {
        Arc::new(Scripted {
            answers: answers
                .into_iter()
                .map(|a| {
                    a.map(|(s, c, d)| (s, c.to_string(), d.to_string()))
                        .map_err(str::to_string)
                })
                .collect(),
            requests: Mutex::new(Vec::new()),
            stop_on_request: None,
        })
    }

    fn requests(&self) -> usize {
        self.requests.lock().expect("lock").len()
    }
}

impl HttpClient for Scripted {
    fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport("this fake only serves POST".into()))
    }

    fn post_json(
        &self,
        _url: &str,
        body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let index = {
            let mut requests = self.requests.lock().expect("lock");
            requests.push(body.clone());
            requests.len() - 1
        };
        if let Some(stop) = &self.stop_on_request {
            stop.store(true, Ordering::SeqCst);
        }
        let answer = &self.answers[index.min(self.answers.len() - 1)];
        match answer {
            Err(fault) => Err(FetchError::Transport(fault.clone())),
            // `RAW` sends `content` as the whole body.
            Ok((status, content, done_reason)) if done_reason == "RAW" => Ok(
                HttpResponse::from_bytes(*status, content.clone().into_bytes()),
            ),
            Ok((status, content, done_reason)) => {
                let body = json!({
                    "message": {"role": "assistant", "content": content},
                    "done": true,
                    "done_reason": done_reason,
                });
                Ok(HttpResponse::from_bytes(
                    *status,
                    body.to_string().into_bytes(),
                ))
            }
        }
    }
}

fn scripted_client(transport: Arc<Scripted>) -> LlmClient {
    let mut client = LlmClient::new(transport);
    client.retry_base_delay = Duration::ZERO;
    client
}

use bmlib::context_processor::llm_processor::ContextModel;
use bmlib::quality::CONTEXT_MODEL_ATTEMPTS;

#[test]
fn context_model_sends_one_user_message_with_its_settings() {
    let transport = Scripted::new(vec![Ok((200, "the digest", "stop"))]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3").with_num_ctx(NonZeroU32::new(16384));

    assert_eq!(
        model.complete("a prompt", 0.2, 512),
        Ok("the digest".into())
    );
    let body = transport.requests.lock().expect("lock")[0].clone();
    assert_eq!(
        body["messages"],
        json!([{"role": "user", "content": "a prompt"}])
    );
    assert_eq!(body["options"]["num_ctx"], json!(16384));
    assert_eq!(body["options"]["temperature"], json!(0.2));
    assert_eq!(body["options"]["num_predict"], json!(512));
    assert!(
        body.get("format").is_none(),
        "a plain completion is not JSON mode"
    );
}

#[test]
fn context_model_ceiling_is_error() {
    // Ollama's one ceiling reason, which the client reads as "length".
    let transport = Scripted::new(vec![Ok((200, "half a pass", "length"))]);
    let client = scripted_client(transport.clone());
    let error = LlmContextModel::new(&client, "ollama:qwen3")
        .complete("p", 0.0, 10)
        .expect_err("a cut-off completion");
    assert!(error.contains("truncated"), "{error}");
    assert_eq!(transport.requests(), 1, "a truncation is not retried");
}

#[test]
fn context_model_json_is_an_object() {
    let transport = Scripted::new(vec![Ok((200, r#"{"extracted_content": "x"}"#, "stop"))]);
    let client = scripted_client(transport.clone());
    let value = LlmContextModel::new(&client, "ollama:qwen3")
        .complete_json("p", 0.0, 10)
        .expect("an object");
    assert_eq!(value, json!({"extracted_content": "x"}));
    let body = transport.requests.lock().expect("lock")[0].clone();
    assert_eq!(body["format"], json!("json"));
}

#[test]
fn context_model_non_object_is_error() {
    let transport = Scripted::new(vec![Ok((200, "[1, 2]", "stop"))]);
    let client = scripted_client(transport.clone());
    let error = LlmContextModel::new(&client, "ollama:qwen3")
        .complete_json("p", 0.0, 10)
        .expect_err("an array is no object");
    assert!(!error.is_empty());
}

#[test]
fn context_model_retries_transport() {
    // A fault, then an answer: one result, two requests.
    let transport = Scripted::new(vec![Err("reset"), Ok((200, "the digest", "stop"))]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3");
    assert_eq!(model.complete("p", 0.0, 10), Ok("the digest".into()));
    assert_eq!(transport.requests(), 2);

    // An HTTP error status is a transport failure too.
    let transport = Scripted::new(vec![Ok((503, "busy", "stop")), Ok((200, "{}", "stop"))]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3");
    assert_eq!(model.complete_json("p", 0.0, 10), Ok(json!({})));
    assert_eq!(transport.requests(), 2);

    // Failures past the attempts: an error after every attempt, saying so.
    let transport = Scripted::new(vec![Err("reset")]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3");
    let error = model
        .complete("p", 0.0, 10)
        .expect_err("every attempt fails");
    assert!(
        error.starts_with(&format!("after {CONTEXT_MODEL_ATTEMPTS} attempts: ")),
        "{error}"
    );
    assert!(error.contains("reset"), "{error}");
    assert_eq!(transport.requests(), CONTEXT_MODEL_ATTEMPTS as usize);
}

#[test]
fn context_model_malformed_answer_is_not_retried() {
    let transport = Scripted::new(vec![Ok((200, "not json", "RAW"))]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3");
    assert!(model.complete("p", 0.0, 10).is_err());
    assert_eq!(transport.requests(), 1);
    // The structured path too: `chat_json` reports it as its `Transport`.
    let transport = Scripted::new(vec![Ok((200, "not json", "RAW"))]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3");
    assert!(model.complete_json("p", 0.0, 10).is_err());
    assert_eq!(transport.requests(), 1);
}

/// Refused before anything is sent, so this pins the error, not the retry rule
/// (`context_model_malformed_answer_is_not_retried` pins that).
#[test]
fn context_model_unknown_provider_sends_nothing() {
    let transport = Scripted::new(vec![Ok((200, "x", "stop"))]);
    let client = scripted_client(transport.clone());
    let error = LlmContextModel::new(&client, "nosuch:model")
        .complete("p", 0.0, 10)
        .expect_err("no such provider");
    assert!(error.contains("nosuch"), "{error}");
    assert_eq!(transport.requests(), 0);
}

#[test]
fn context_model_stop_sends_nothing_and_is_not_retried() {
    let stop = AtomicBool::new(true);
    let transport = Scripted::new(vec![Ok((200, "x", "stop"))]);
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3").with_stop(&stop);
    assert_eq!(
        model.complete("p", 0.0, 10),
        Err(bmlib::quality::STOPPED_MESSAGE.to_string())
    );
    assert!(model.complete_json("p", 0.0, 10).is_err());
    assert_eq!(transport.requests(), 0);

    // A fault while the stop is set is not retried.
    let stop = Arc::new(AtomicBool::new(false));
    let mut transport = Scripted::new(vec![Err("reset")]);
    Arc::get_mut(&mut transport)
        .expect("unshared")
        .stop_on_request = Some(stop.clone());
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3").with_stop(&stop);
    // The fault itself, returned at once rather than after a backoff wait.
    let error = model.complete("p", 0.0, 10).expect_err("a fault");
    assert!(error.contains("reset"), "{error}");
    assert_eq!(transport.requests(), 1);

    // The structured path too, where only the stop keeps a transport fault
    // from its retry.
    let stop = Arc::new(AtomicBool::new(false));
    let mut transport = Scripted::new(vec![Err("reset")]);
    Arc::get_mut(&mut transport)
        .expect("unshared")
        .stop_on_request = Some(stop.clone());
    let client = scripted_client(transport.clone());
    let model = LlmContextModel::new(&client, "ollama:qwen3").with_stop(&stop);
    let error = model.complete_json("p", 0.0, 10).expect_err("a fault");
    assert!(error.contains("reset"), "{error}");
    assert!(!error.contains(bmlib::quality::STOPPED_MESSAGE), "{error}");
    assert_eq!(transport.requests(), 1);
}

#[test]
fn llm_chat_stop_sends_nothing() {
    use bmlib::quality::JsonChat;
    let stop = AtomicBool::new(true);
    let transport = Scripted::new(vec![Ok((200, "{}", "stop"))]);
    let client = scripted_client(transport.clone());
    let mut chat = LlmChat::new(&client, "ollama:qwen3").with_stop(&stop);
    let messages = [bmlib::llm::LLMMessage::user("hi")];
    assert!(chat.chat_json(&messages, 0.0, 10, 3, true).is_err());
    assert_eq!(transport.requests(), 0);
}

#[test]
fn stopped_says_why() {
    assert_eq!(
        PicoError::Stopped(None).to_string(),
        bmlib::quality::STOPPED_MESSAGE
    );
    // The failure the stop is reported over is kept, not discarded.
    assert_eq!(
        PicoError::Stopped(Some("missing API key".into())).to_string(),
        format!(
            "{}; the request under way failed: missing API key",
            bmlib::quality::STOPPED_MESSAGE
        )
    );
}

/// A failure the stop is reported over is kept on the error; the stop's own
/// refusal of a re-ask is not repeated as one.
#[test]
fn stopped_keeps_the_failure_it_replaces() {
    let stop = Arc::new(AtomicBool::new(false));
    let mut transport = Scripted::new(vec![Err("the server went away")]);
    Arc::get_mut(&mut transport)
        .expect("unshared")
        .stop_on_request = Some(stop.clone());
    let client = scripted_client(transport.clone());
    let mut chat = LlmChat::new(&client, "ollama:qwen3").with_stop(&stop);
    let mut agent = PicoAgent::new(&mut chat).with_stop(&stop);
    match agent.extract("A trial", "Adults were enrolled.", 0.9) {
        Err(PicoError::Stopped(Some(failure))) => {
            assert!(failure.contains("the server went away"), "{failure}");
        }
        other => panic!("{other:?}"),
    }

    // An unusable reply, then `chat_json`'s re-ask refused by the stop.
    let stop = Arc::new(AtomicBool::new(false));
    let mut transport = Scripted::new(vec![Ok((200, "not json", "stop"))]);
    Arc::get_mut(&mut transport)
        .expect("unshared")
        .stop_on_request = Some(stop.clone());
    let client = scripted_client(transport.clone());
    let mut chat = LlmChat::new(&client, "ollama:qwen3").with_stop(&stop);
    let mut agent = PicoAgent::new(&mut chat).with_stop(&stop);
    assert_eq!(
        agent.extract("A trial", "Adults were enrolled.", 0.9),
        Err(PicoError::Stopped(None))
    );
    assert_eq!(transport.requests(), 1);
}

/// The agent's own stop, over a chat that has none: no call is made.
#[test]
fn the_agents_stop_holds_over_any_chat() {
    let stop = AtomicBool::new(true);
    let transport = Scripted::new(vec![Ok((200, "{}", "stop"))]);
    let client = scripted_client(transport.clone());
    // No `with_stop` on the chat: only the agent knows.
    let mut chat = LlmChat::new(&client, "ollama:qwen3");
    let mut agent = PicoAgent::new(&mut chat).with_stop(&stop);

    assert_eq!(
        agent.extract("A trial", "Adults were enrolled.", 0.9),
        Err(PicoError::Stopped(None))
    );
    assert_eq!(
        agent.check_suitability("A trial", "Adults were enrolled."),
        Err(PicoError::Stopped(None))
    );
    assert_eq!(transport.requests(), 0);

    // Nor is a long paper condensed by a condenser model that has no stop.
    let condenser_model = LlmContextModel::new(&client, "ollama:qwen3");
    let mut condenser =
        PicoAgent::pico_condenser(&condenser_model, 0.1, 4096).expect("the PICO prompts");
    let mut chat = LlmChat::new(&client, "ollama:qwen3");
    let mut agent = PicoAgent::new(&mut chat)
        .with_condenser(&mut condenser)
        .with_condense_config(ProcessingConfig::default().with_max_context_chars(1000))
        .with_stop(&stop);
    let long = "Adults were enrolled. ".repeat(200);
    assert_eq!(
        agent.prepare("A trial", &long),
        Err(PicoError::Stopped(None))
    );
    assert_eq!(transport.requests(), 0);
}
