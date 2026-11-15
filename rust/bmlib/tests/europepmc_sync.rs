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

//! Europe PMC day-by-day walker — the oracle, and the cases Python and Rust
//! word differently (a transport error's name), tested here alone.
//!
//! `rust/oracle/dump_europepmc_sync.py` writes `data/europepmc_sync_expected.json`
//! from `rust/oracle/europepmc_sync_cases.json`; regenerate it after changing
//! either side.

use std::sync::Arc;

use bmlib::publications::fetchers::europepmc::{
    page_params, walk, EuropePmcFetcher, SearchPages, SEARCH_URL,
};
use bmlib::publications::fetchers::registry::{builtin_descriptors, FetchRequest, Fetcher};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use chrono::NaiveDate;
use serde_json::{json, Value};

mod common;

use common::oracle::Response as Scripted;
use common::sink::RecordingSink;

const CASES: &str = include_str!("data/europepmc_sync_cases.json");
const EXPECTED: &str = include_str!("data/europepmc_sync_expected.json");

/// Answers scripted pages in order and records every request's parameters.
struct ScriptedPages {
    payloads: std::cell::RefCell<Vec<Scripted>>,
    calls: std::cell::RefCell<Vec<Vec<(String, String)>>>,
}

impl SearchPages for ScriptedPages {
    fn page(&self, params: &[(String, String)]) -> Result<Value, FetchError> {
        self.calls.borrow_mut().push(params.to_vec());
        let mut payloads = self.payloads.borrow_mut();
        if payloads.is_empty() {
            return Ok(json!({"hitCount": 0, "resultList": {"result": []}}));
        }
        match payloads.remove(0) {
            Scripted::Body(value) => Ok(value),
            Scripted::Status(status) => Err(FetchError::HttpStatus {
                url: SEARCH_URL.to_string(),
                status,
            }),
            Scripted::Transport(message) => Err(FetchError::Transport(message)),
            Scripted::Error(error) => Err(error),
        }
    }
}

fn scripted(pages: Vec<Scripted>) -> ScriptedPages {
    ScriptedPages {
        payloads: std::cell::RefCell::new(pages),
        calls: std::cell::RefCell::new(Vec::new()),
    }
}

fn day() -> NaiveDate {
    NaiveDate::from_ymd_opt(2024, 6, 10).expect("date")
}

/// The parameters as Python's dict reports them (`pageSize` is an integer).
fn params_object(pairs: &[(String, String)]) -> Value {
    let mut object = serde_json::Map::new();
    for (k, v) in pairs {
        if k == "pageSize" {
            object.insert(k.clone(), json!(v.parse::<i64>().unwrap_or(0)));
        } else {
            object.insert(k.clone(), json!(v));
        }
    }
    Value::Object(object)
}

fn run(case: &Value) -> Value {
    let args = &case["args"];
    assert_eq!(case["fn"], "fetch", "unknown fn");
    let pages: Vec<Scripted> = args["payloads"]
        .as_array()
        .map(|a| a.iter().map(common::oracle::response).collect())
        .unwrap_or_default();
    let query = args["query"].as_str().expect("query");
    let email = args.get("email").and_then(Value::as_str);
    let source = scripted(pages);
    let mut sink = RecordingSink::new();
    let outcome = walk(&source, day(), query, email, &mut sink);
    let calls = source.calls.borrow().clone();
    json!({
        "status": outcome.status,
        "error": outcome.error,
        "note": outcome.note,
        "record_count": outcome.record_count,
        "urls": calls.iter().map(|_| SEARCH_URL).collect::<Vec<_>>(),
        "params": calls.iter().map(|c| params_object(c)).collect::<Vec<_>>(),
        "records": sink.records.iter().map(|r| r.title.clone()).collect::<Vec<_>>(),
        "fulltext_urls": sink
            .records
            .iter()
            .map(|r| r.fulltext_sources.iter().map(|f| f.url.clone()).collect::<Vec<_>>())
            .collect::<Vec<_>>(),
        "progress": sink
            .pages()
            .into_iter()
            .map(|(delivered, promised)| json!([delivered, promised, "in_progress"]))
            .collect::<Vec<_>>(),
    })
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");
    let mut failures = Vec::new();
    for (case, want) in cases.iter().zip(expected) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!("{name}:\n  got  {got}\n  want {}", want["value"]));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn a_transport_error_fails_the_day_naming_the_error() {
    let source = scripted(vec![Scripted::Transport("network is unreachable".into())]);
    let mut sink = RecordingSink::new();
    let outcome = walk(&source, day(), "OPEN_ACCESS:Y", None, &mut sink);
    assert_eq!(outcome.status, "failed");
    let error = outcome.error.expect("an error");
    assert!(error.contains("network is unreachable"), "{error}");
}

#[test]
fn page_params_carry_the_day_clause() {
    let params = page_params("2024-06-10", "*", "OPEN_ACCESS:Y", None);
    let query = params.iter().find(|(k, _)| k == "query").expect("query");
    assert_eq!(
        query.1,
        "(OPEN_ACCESS:Y) AND FIRST_PDATE:[2024-06-10 TO 2024-06-10]"
    );
}

/// A client that records the URL it was asked for and answers one empty day.
struct RecordingClient {
    urls: std::sync::Mutex<Vec<String>>,
}

impl HttpClient for RecordingClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.urls.lock().expect("lock").push(url.to_string());
        Ok(HttpResponse::ok_text(
            r#"{"hitCount":0,"nextCursorMark":"*","resultList":{"result":[]}}"#,
        ))
    }
}

#[test]
fn the_fetcher_encodes_the_query_into_the_url() {
    let client = Arc::new(RecordingClient {
        urls: std::sync::Mutex::new(Vec::new()),
    });
    let fetcher = EuropePmcFetcher {
        client: client.clone(),
    };
    let mut request = FetchRequest::new(day());
    request.config.insert(
        "query".into(),
        r#"TITLE:"depression" AND OPEN_ACCESS:Y"#.into(),
    );
    let mut sink = RecordingSink::new();
    let outcome = fetcher.fetch(&request, &mut sink).expect("a walk");
    assert_eq!(outcome.status, "completed");
    let urls = client.urls.lock().expect("lock");
    let url = &urls[0];
    assert!(url.starts_with(&format!("{SEARCH_URL}?")), "{url}");
    assert!(!url.contains(' ') && !url.contains('"'), "unencoded: {url}");
    assert!(url.contains("cursorMark=%2A"), "{url}");
}

#[test]
fn the_fetcher_without_a_query_fails_the_day_unsent() {
    let client = Arc::new(RecordingClient {
        urls: std::sync::Mutex::new(Vec::new()),
    });
    let fetcher = EuropePmcFetcher {
        client: client.clone(),
    };
    let mut sink = RecordingSink::new();
    let outcome = fetcher
        .fetch(&FetchRequest::new(day()), &mut sink)
        .expect("an outcome");
    assert_eq!(outcome.status, "failed");
    assert!(client.urls.lock().expect("lock").is_empty());
}

#[test]
fn europepmc_is_a_builtin_source_with_a_required_query() {
    let descriptor = builtin_descriptors()
        .into_iter()
        .find(|d| d.name == "europepmc")
        .expect("registered");
    assert_eq!(descriptor.display_name, "Europe PMC");
    assert!(descriptor
        .params
        .iter()
        .any(|p| p.name == "query" && p.required));
}
