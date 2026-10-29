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

//! The shared context-window contract, `tests/fixtures/context_window.json`
//! (the same file Python's `tests/test_context_window.py` reads).

use bmlib::llm::{
    known_context_window, read_context_window, ContextWindowError, KNOWN_CONTEXT_WINDOWS,
};
use bmlib::llm::{LlmClient, STREAM_OPEN_ATTEMPTS};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

const FIXTURE: &str = include_str!("../../../tests/fixtures/context_window.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("fixture is JSON")
}

#[test]
fn table_matches_the_fixture() {
    let fx = fixture();
    let rows = fx["table"].as_array().unwrap();
    let from_fixture: HashSet<(String, String, u64)> = rows
        .iter()
        .map(|r| {
            (
                r["provider"].as_str().unwrap().to_string(),
                r["model"].as_str().unwrap().to_string(),
                r["window"].as_u64().unwrap(),
            )
        })
        .collect();
    assert_eq!(from_fixture.len(), rows.len(), "duplicate fixture rows");
    let from_code: HashSet<(String, String, u64)> = KNOWN_CONTEXT_WINDOWS
        .iter()
        .map(|(p, m, w)| (p.to_string(), m.to_string(), *w))
        .collect();
    assert_eq!(
        KNOWN_CONTEXT_WINDOWS.len(),
        from_code.len(),
        "duplicate code rows"
    );
    assert_eq!(from_fixture, from_code);
}

#[test]
fn table_lookups() {
    let fx = fixture();
    for case in fx["table_lookups"].as_array().unwrap() {
        let expected = case["expect"]["window"].as_u64();
        let got = known_context_window(
            case["provider"].as_str().unwrap(),
            case["model"].as_str().unwrap(),
        );
        assert_eq!(got, expected, "{}", case["name"]);
    }
}

#[test]
fn metadata_cases() {
    let fx = fixture();
    for case in fx["metadata"].as_array().unwrap() {
        let name = &case["name"];
        let provider = case["provider"].as_str().unwrap();
        let model = case["model"].as_str().unwrap();
        let got = read_context_window(provider, model, &case["body"]);
        if case["expect"]["error"] == "no_such_model" {
            assert_eq!(
                got,
                Err(ContextWindowError::NoSuchModel {
                    provider: provider.to_string(),
                    model: model.to_string()
                }),
                "{name}"
            );
        } else {
            assert_eq!(got, Ok(case["expect"]["window"].as_u64()), "{name}");
        }
    }
}

#[test]
fn provider_without_metadata_is_unknown() {
    assert_eq!(
        read_context_window("openai", "gpt-4o-mini", &Value::Null),
        Err(ContextWindowError::UnknownProvider("openai".to_string()))
    );
    let shown = |e: ContextWindowError| e.to_string();
    let msg = shown(ContextWindowError::NoSuchModel {
        provider: "deepseek".into(),
        model: "zz-model-9".into(),
    });
    assert!(msg.contains("deepseek") && msg.contains("zz-model-9"));
    assert!(shown(ContextWindowError::UnknownProvider("openai".into())).contains("openai"));
    let msg = shown(ContextWindowError::MissingApiKey {
        provider: "mistral".into(),
        env_var: "MISTRAL_KEY_X".into(),
    });
    assert!(msg.contains("mistral") && msg.contains("MISTRAL_KEY_X"));
}

// ---------------------------------------------------------------------------
// LlmClient::context_window, over a scripted transport
// ---------------------------------------------------------------------------

/// One recorded request: method, URL, body (POST only), headers.
type Recorded = (String, String, Option<Value>, BTreeMap<String, String>);

/// Answers from a script (the last answer repeats) and records every request.
struct Scripted {
    script: Mutex<VecDeque<Result<(u16, String), String>>>,
    requests: Mutex<Vec<Recorded>>,
}

impl Scripted {
    fn new(script: Vec<Result<(u16, String), String>>) -> Arc<Self> {
        Arc::new(Scripted {
            script: Mutex::new(script.into()),
            requests: Mutex::new(Vec::new()),
        })
    }

    fn ok(body: Value) -> Arc<Self> {
        Scripted::new(vec![Ok((200, body.to_string()))])
    }

    fn answer(&self) -> Result<HttpResponse, FetchError> {
        let mut script = self.script.lock().unwrap();
        let next = if script.len() > 1 {
            script.pop_front().unwrap()
        } else {
            script.front().cloned().unwrap()
        };
        match next {
            Ok((status, body)) => Ok(HttpResponse::from_bytes(status, body.into_bytes())),
            Err(message) => Err(FetchError::Transport(message)),
        }
    }

    fn calls(&self) -> usize {
        self.requests.lock().unwrap().len()
    }

    fn last(&self) -> Recorded {
        self.requests.lock().unwrap().last().cloned().unwrap()
    }
}

impl HttpClient for Scripted {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.get_with_headers(url, &BTreeMap::new())
    }

    fn get_with_headers(
        &self,
        url: &str,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.requests
            .lock()
            .unwrap()
            .push(("GET".into(), url.into(), None, headers.clone()));
        self.answer()
    }

    fn post_json(
        &self,
        url: &str,
        body: &Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.requests.lock().unwrap().push((
            "POST".into(),
            url.into(),
            Some(body.clone()),
            headers.clone(),
        ));
        self.answer()
    }
}

fn client(transport: Arc<Scripted>) -> LlmClient {
    let mut client = LlmClient::new(transport);
    client.api_key = Some("k".to_string());
    client.retry_base_delay = Duration::ZERO;
    client
}

#[test]
fn anthropic_gets_the_model_with_its_headers() {
    let t = Scripted::ok(json!({"max_input_tokens": 200000}));
    let got = client(t.clone()).context_window("anthropic:claude-x");
    assert_eq!(got, Ok(Some(200_000)));
    let (method, url, _, headers) = t.last();
    assert_eq!(method, "GET");
    assert_eq!(url, "https://api.anthropic.com/v1/models/claude-x");
    assert_eq!(headers["x-api-key"], "k");
    assert_eq!(headers["anthropic-version"], "2023-06-01");
}

#[test]
fn mistral_gets_the_model_with_bearer() {
    let t = Scripted::ok(json!({"max_context_length": 32000}));
    assert_eq!(
        client(t.clone()).context_window("mistral:m-1"),
        Ok(Some(32_000))
    );
    let (_, url, _, headers) = t.last();
    assert_eq!(url, "https://api.mistral.ai/v1/models/m-1");
    assert_eq!(headers["Authorization"], "Bearer k");
}

#[test]
fn deepseek_gets_the_list() {
    let t = Scripted::ok(json!({"data": [{"id": "deepseek-chat", "context_window": 64000}]}));
    assert_eq!(
        client(t.clone()).context_window("deepseek:deepseek-chat"),
        Ok(Some(64_000))
    );
    assert_eq!(t.last().1, "https://api.deepseek.com/models");
    assert_eq!(t.last().3["Authorization"], "Bearer k");
}

#[test]
fn gemini_uses_the_native_base_and_key_header() {
    let t = Scripted::ok(json!({"inputTokenLimit": 1048576}));
    assert_eq!(
        client(t.clone()).context_window("gemini:gemini-2.0-flash"),
        Ok(Some(1_048_576))
    );
    let (_, url, _, headers) = t.last();
    assert_eq!(
        url,
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash"
    );
    assert_eq!(headers["x-goog-api-key"], "k");
}

#[test]
fn ollama_posts_show_with_the_model_in_the_body() {
    let t = Scripted::ok(json!({
        "model_info": {"general.architecture": "llama", "llama.context_length": 131072}
    }));
    assert_eq!(
        client(t.clone()).context_window("ollama:llama3.2:8b"),
        Ok(Some(131_072))
    );
    let (method, url, body, _) = t.last();
    assert_eq!(method, "POST");
    assert_eq!(url, "http://localhost:11434/api/show");
    assert_eq!(body, Some(json!({"model": "llama3.2:8b"})));
}

#[test]
fn a_bare_provider_name_means_its_default_model() {
    let t = Scripted::ok(json!({"max_input_tokens": 1000}));
    client(t.clone()).context_window("anthropic").unwrap();
    assert!(t.last().1.ends_with("/v1/models/claude-sonnet-4-20250514"));
}

#[test]
fn openai_answers_from_the_table_without_a_key_or_a_request() {
    std::env::remove_var("OPENAI_API_KEY");
    let t = Scripted::ok(json!({}));
    let mut c = LlmClient::new(t.clone());
    c.api_key = None;
    assert_eq!(
        c.context_window("openai:gpt-4o-mini"),
        Ok(known_context_window("openai", "gpt-4o-mini"))
    );
    assert_eq!(t.calls(), 0);
}

#[test]
fn a_missing_key_is_reported_before_any_request() {
    std::env::remove_var("ANTHROPIC_API_KEY");
    let t = Scripted::ok(json!({}));
    let mut c = LlmClient::new(t.clone());
    c.api_key = None;
    assert!(matches!(
        c.context_window("anthropic:claude-x"),
        Err(ContextWindowError::MissingApiKey { .. })
    ));
    assert_eq!(t.calls(), 0);
}

#[test]
fn an_unknown_provider_is_reported() {
    let t = Scripted::ok(json!({}));
    assert_eq!(
        client(t.clone()).context_window("nope:m"),
        Err(ContextWindowError::UnknownProvider("nope".to_string()))
    );
}

#[test]
fn a_404_is_no_such_model_after_one_call() {
    let t = Scripted::new(vec![Ok((404, "{}".into()))]);
    assert_eq!(
        client(t.clone()).context_window("mistral:gone"),
        Err(ContextWindowError::NoSuchModel {
            provider: "mistral".into(),
            model: "gone".into()
        })
    );
    assert_eq!(t.calls(), 1);
}

#[test]
fn a_deepseek_404_is_a_transport_error() {
    let t = Scripted::new(vec![Ok((404, "{}".into()))]);
    assert!(matches!(
        client(t.clone()).context_window("deepseek:gone"),
        Err(ContextWindowError::Transport(_))
    ));
    assert_eq!(t.calls(), 1);
}

#[test]
fn an_ollama_404_saying_not_found_is_no_such_model() {
    let t = Scripted::new(vec![Ok((
        404,
        json!({"error": "model 'x' not found"}).to_string(),
    ))]);
    assert_eq!(
        client(t).context_window("ollama:x"),
        Err(ContextWindowError::NoSuchModel {
            provider: "ollama".into(),
            model: "x".into()
        })
    );
}

#[test]
fn an_ollama_404_with_an_html_body_is_a_transport_error() {
    let t = Scripted::new(vec![Ok((404, "<html>404 page not found</html>".into()))]);
    assert!(matches!(
        client(t).context_window("ollama:x"),
        Err(ContextWindowError::Transport(_))
    ));
}

#[test]
fn a_retryable_status_is_retried_then_succeeds() {
    let t = Scripted::new(vec![
        Ok((503, "busy".into())),
        Ok((503, "busy".into())),
        Ok((200, json!({"max_input_tokens": 5000}).to_string())),
    ]);
    assert_eq!(
        client(t.clone()).context_window("anthropic:c"),
        Ok(Some(5000))
    );
    assert_eq!(t.calls(), 3);
}

#[test]
fn transport_failure_is_retried_then_reported() {
    let t = Scripted::new(vec![Err("no route".into())]);
    let got = client(t.clone()).context_window("anthropic:c");
    assert!(matches!(got, Err(ContextWindowError::Transport(m)) if m.contains("no route")));
    assert_eq!(t.calls(), STREAM_OPEN_ATTEMPTS as usize);
}

#[test]
fn another_status_quotes_the_server() {
    let t = Scripted::new(vec![Ok((
        401,
        json!({"error": {"message": "bad key"}}).to_string(),
    ))]);
    let got = client(t.clone()).context_window("anthropic:c");
    assert!(
        matches!(got, Err(ContextWindowError::Transport(m)) if m.contains("401") && m.contains("bad key"))
    );
    assert_eq!(t.calls(), 1);
}

#[test]
fn a_body_that_is_not_json_is_malformed() {
    let t = Scripted::new(vec![Ok((200, "<html>".into()))]);
    assert!(matches!(
        client(t).context_window("anthropic:c"),
        Err(ContextWindowError::Malformed(_))
    ));
}

#[test]
fn a_model_id_is_percent_encoded_in_the_path() {
    let t = Scripted::ok(json!({"max_input_tokens": 1}));
    client(t.clone())
        .context_window("mistral:org/m 1~x")
        .unwrap();
    assert_eq!(t.last().1, "https://api.mistral.ai/v1/models/org%2Fm%201~x");
}

#[test]
fn a_client_without_get_with_headers_refuses_by_name() {
    struct Bare;
    impl HttpClient for Bare {
        fn get(&self, _: &str) -> Result<HttpResponse, FetchError> {
            unreachable!()
        }
    }
    let err = Bare
        .get_with_headers("http://x", &BTreeMap::new())
        .unwrap_err();
    assert!(matches!(&err, FetchError::Config(m) if m.contains("get_with_headers")));
}
