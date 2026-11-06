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

//! The shared model-listing contract, `tests/fixtures/model_listing.json`
//! (the same file Python's `tests/test_model_listing.py` reads), and
//! `LlmClient::list_models` over a scripted transport (#680).

use bmlib::llm::{
    read_model_listing, ListModelsError, LlmClient, ModelListingPage, MAX_LISTING_PAGES,
    STREAM_OPEN_ATTEMPTS,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

const FIXTURE: &str = include_str!("../../../tests/fixtures/model_listing.json");

#[test]
fn listings_agree_with_the_fixture() {
    let fx: Value = serde_json::from_str(FIXTURE).expect("fixture is JSON");
    let cases = fx["listings"].as_array().expect("listings");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let got = read_model_listing(case["provider"].as_str().expect("provider"), &case["body"]);
        let expect = &case["expect"];
        if expect["error"] == "malformed" {
            assert!(
                matches!(got, Err(ListModelsError::Malformed(_))),
                "{name}: expected malformed, got {got:?}"
            );
            continue;
        }
        let ids = expect["ids"]
            .as_array()
            .expect("ids")
            .iter()
            .map(|id| id.as_str().expect("id").to_string())
            .collect();
        let next_after = expect["next_after"].as_str().map(str::to_string);
        assert_eq!(got, Ok(ModelListingPage { ids, next_after }), "{name}");
    }
}

/// Answers GETs from a script (the last answer repeats) and records each URL
/// and its headers.
struct Scripted {
    script: Mutex<VecDeque<Result<(u16, String), String>>>,
    requests: Mutex<Vec<(String, BTreeMap<String, String>)>>,
}

impl Scripted {
    fn new(script: Vec<Result<(u16, String), String>>) -> Arc<Self> {
        Arc::new(Scripted {
            script: Mutex::new(script.into()),
            requests: Mutex::new(Vec::new()),
        })
    }

    fn ok(bodies: &[Value]) -> Arc<Self> {
        Scripted::new(bodies.iter().map(|b| Ok((200, b.to_string()))).collect())
    }

    fn urls(&self) -> Vec<String> {
        self.requests
            .lock()
            .unwrap()
            .iter()
            .map(|(u, _)| u.clone())
            .collect()
    }

    fn headers(&self) -> BTreeMap<String, String> {
        self.requests.lock().unwrap().last().cloned().unwrap().1
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
            .push((url.to_string(), headers.clone()));
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
}

fn client(transport: Arc<Scripted>, key: Option<&str>) -> LlmClient {
    let mut client = LlmClient::new(transport);
    client.api_key = key.map(str::to_string);
    client.retry_base_delay = Duration::ZERO;
    client
}

#[test]
fn ollama_lists_its_tags_sorted_without_a_key() {
    let t = Scripted::ok(&[json!({"models": [{"model": "b:1"}, {"model": "a:2"}]})]);
    let got = client(t.clone(), None).list_models("ollama");
    assert_eq!(got, Ok(vec!["a:2".to_string(), "b:1".to_string()]));
    assert_eq!(t.urls(), ["http://localhost:11434/api/tags"]);
    assert!(!t.headers().contains_key("Authorization"));
}

#[test]
fn anthropic_follows_every_page_and_merges_them() {
    let t = Scripted::ok(&[
        json!({"data": [{"id": "c"}, {"id": "a"}], "has_more": true, "last_id": "a b"}),
        json!({"data": [{"id": "b"}, {"id": "a"}], "has_more": false, "last_id": "a"}),
    ]);
    let got = client(t.clone(), Some("k")).list_models("anthropic");
    assert_eq!(got, Ok(vec!["a".into(), "b".into(), "c".into()]));
    assert_eq!(
        t.urls(),
        [
            "https://api.anthropic.com/v1/models?limit=1000",
            "https://api.anthropic.com/v1/models?limit=1000&after_id=a%20b",
        ]
    );
    let headers = t.headers();
    assert_eq!(headers["x-api-key"], "k");
    assert_eq!(headers["anthropic-version"], "2023-06-01");
}

#[test]
fn each_provider_lists_at_its_own_url() {
    for (provider, url) in [
        ("openai", "https://api.openai.com/v1/models"),
        ("mistral", "https://api.mistral.ai/v1/models"),
        ("deepseek", "https://api.deepseek.com/models"),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/models",
        ),
    ] {
        // Each provider's own reading: only Gemini's drops the prefix.
        let t = Scripted::ok(&[json!({"data": [{"id": "models/m"}]})]);
        let want = if provider == "gemini" {
            "m"
        } else {
            "models/m"
        };
        assert_eq!(
            client(t.clone(), Some("k")).list_models(provider),
            Ok(vec![want.to_string()])
        );
        assert_eq!(t.urls(), [url], "{provider}");
        assert_eq!(t.headers()["Authorization"], "Bearer k", "{provider}");
    }
}

#[test]
fn a_provider_name_is_read_trimmed_and_in_any_case() {
    let t = Scripted::ok(&[json!({"models": []})]);
    assert_eq!(client(t, None).list_models(" Ollama "), Ok(vec![]));
}

#[test]
fn a_base_url_override_is_listed_under() {
    let t = Scripted::ok(&[json!({"data": []})]);
    let mut c = client(t.clone(), Some("k"));
    c.base_url = Some("http://h:8080/".into());
    assert_eq!(c.list_models("openai"), Ok(vec![]));
    assert_eq!(t.urls(), ["http://h:8080/v1/models"]);
}

#[test]
fn an_empty_provider_name_is_unknown() {
    let t = Scripted::ok(&[json!({"models": []})]);
    assert_eq!(
        client(t.clone(), None).list_models(" "),
        Err(ListModelsError::UnknownProvider(" ".into()))
    );
    assert!(t.urls().is_empty());
}

#[test]
fn a_missing_key_is_reported_before_any_request() {
    std::env::remove_var("OPENAI_API_KEY");
    let t = Scripted::ok(&[json!({"data": []})]);
    let got = client(t.clone(), None).list_models("openai");
    assert!(
        matches!(got, Err(ListModelsError::MissingApiKey { ref provider, .. }) if provider == "openai")
    );
    assert!(t.urls().is_empty());
}

#[test]
fn an_unknown_provider_is_reported_not_routed_to_the_default() {
    let t = Scripted::ok(&[json!({"models": []})]);
    let got = client(t.clone(), Some("k")).list_models("llama-cloud");
    assert_eq!(
        got,
        Err(ListModelsError::UnknownProvider("llama-cloud".into()))
    );
    assert!(t.urls().is_empty());
}

#[test]
fn a_refused_key_carries_its_status_after_one_call() {
    let t = Scripted::new(vec![Ok((
        401,
        r#"{"error":{"message":"invalid x-api-key"}}"#.into(),
    ))]);
    let got = client(t.clone(), Some("k")).list_models("anthropic");
    match got {
        Err(ListModelsError::Transport { status, message }) => {
            assert_eq!(status, Some(401));
            assert!(message.contains("invalid x-api-key"), "{message}");
        }
        other => panic!("expected a transport error, got {other:?}"),
    }
    assert_eq!(t.urls().len(), 1);
}

#[test]
fn a_retryable_status_is_retried_then_succeeds() {
    let t = Scripted::new(vec![
        Ok((503, "busy".into())),
        Ok((200, json!({"models": [{"model": "m"}]}).to_string())),
    ]);
    assert_eq!(
        client(t.clone(), None).list_models("ollama"),
        Ok(vec!["m".into()])
    );
    assert_eq!(t.urls().len(), 2);
}

#[test]
fn a_transport_failure_is_retried_then_reported_without_a_status() {
    let t = Scripted::new(vec![Err("connection refused".into())]);
    let got = client(t.clone(), None).list_models("ollama");
    assert!(
        matches!(got, Err(ListModelsError::Transport { status: None, .. })),
        "{got:?}"
    );
    assert_eq!(t.urls().len(), STREAM_OPEN_ATTEMPTS as usize);
}

#[test]
fn a_body_that_is_not_json_is_malformed() {
    let t = Scripted::new(vec![Ok((200, "<html>".into()))]);
    let got = client(t, None).list_models("ollama");
    assert!(matches!(got, Err(ListModelsError::Malformed(_))), "{got:?}");
}

#[test]
fn paging_that_never_ends_is_refused_not_cut_short() {
    let t = Scripted::ok(&[json!({"data": [{"id": "a"}], "has_more": true, "last_id": "a"})]);
    let got = client(t.clone(), Some("k")).list_models("anthropic");
    assert!(
        matches!(&got, Err(ListModelsError::Malformed(m)) if m.contains("pages")),
        "{got:?}"
    );
    assert_eq!(t.urls().len(), MAX_LISTING_PAGES);
}
