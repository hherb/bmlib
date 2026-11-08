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

//! The shared contract for a cancelled stream's connection,
//! `tests/fixtures/stream_give_up.json` (#649, #652).
//!
//! Each case runs `LlmClient::chat_stream_with_cancel` over the real
//! `UreqClient` against a local server that stalls or refuses, cancels it,
//! and checks the server sees the connection close and no second request.
//! Python runs the same cases in `tests/test_stream_give_up_contract.py`.

use bmlib::http::UreqClient;
use bmlib::llm::{ChatRequest, LLMMessage, LlmClient, StreamDelta};
use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::ops::ControlFlow;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const FIXTURE: &str = include_str!("../../../tests/fixtures/stream_give_up.json");

/// How often a wait on the server's counters looks again.
const POLL: Duration = Duration::from_millis(10);

/// What the local server has seen, shared with its threads.
#[derive(Default)]
struct Seen {
    /// Requests read in full (and answered, for the first).
    requests: AtomicUsize,
    /// Connections accepted and not yet closed by the client.
    open: AtomicUsize,
}

/// Serve `case`'s behaviour on 127.0.0.1, returning its base URL and what
/// it sees. The threads run until the test process ends.
fn serve(case: &Value) -> (String, Arc<Seen>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let url = format!("http://{}", listener.local_addr().expect("addr"));
    let seen = Arc::new(Seen::default());
    let case = case.clone();
    let counters = Arc::clone(&seen);
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(stream) = stream else { return };
            counters.open.fetch_add(1, Ordering::SeqCst);
            let (case, counters) = (case.clone(), Arc::clone(&counters));
            std::thread::spawn(move || handle(stream, &case, &counters));
        }
    });
    (url, seen)
}

/// Read requests off one connection until the client closes it, answering
/// only the server's first request, as the case says.
fn handle(stream: TcpStream, case: &Value, seen: &Seen) {
    let mut writer = stream.try_clone().expect("clone");
    let mut reader = BufReader::new(stream);
    while read_request(&mut reader) {
        if seen.requests.load(Ordering::SeqCst) == 0 {
            let _ = writer.write_all(&first_answer(case));
            let _ = writer.flush();
        }
        seen.requests.fetch_add(1, Ordering::SeqCst);
    }
    seen.open.fetch_sub(1, Ordering::SeqCst);
}

/// Read one request, headers and body; false once the connection ends.
fn read_request(reader: &mut BufReader<TcpStream>) -> bool {
    let mut length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            return false;
        }
        if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            length = value.trim().parse().unwrap_or(0);
        }
        if line == "\r\n" {
            break;
        }
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body).is_ok()
}

/// What the server writes to the first request: nothing, one event, or 503.
fn first_answer(case: &Value) -> Vec<u8> {
    match case["server"].as_str().expect("server") {
        "stalls_before_headers" => Vec::new(),
        "stalls_after_first_event" => {
            let event = case["event"].as_str().expect("event");
            format!(
                "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{event}\r\n",
                case["content_type"].as_str().expect("content_type"),
                event.len()
            )
            .into_bytes()
        }
        "refuses_retryably" => {
            let body = r#"{"error": {"message": "busy"}}"#;
            format!(
                "HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nRetry-After: 1\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            )
            .into_bytes()
        }
        other => panic!("unknown server behaviour {other}"),
    }
}

/// Whether `done` becomes true within `limit`.
fn within(limit: Duration, done: impl Fn() -> bool) -> bool {
    let started = Instant::now();
    while started.elapsed() < limit {
        if done() {
            return true;
        }
        std::thread::sleep(POLL);
    }
    done()
}

#[test]
fn every_case_closes_the_connection_and_sends_no_retry() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("fixture");
    let limit = Duration::from_millis(fixture["closed_within_ms"].as_u64().expect("limit"));
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let (url, seen) = serve(case);
        let mut llm = LlmClient::new(Arc::new(UreqClient::default()));
        llm.base_url = Some(url);
        llm.api_key = Some("k".to_string());
        let provider = case["provider"].as_str().expect("provider");
        let request = ChatRequest::new(vec![LLMMessage::user("Was it blinded?")])
            .with_model(format!("{provider}:m"));
        let cancel = Arc::new(AtomicBool::new(false));
        match case["cancel"].as_str().expect("cancel") {
            "once_requested" => {
                let (cancel, seen) = (Arc::clone(&cancel), Arc::clone(&seen));
                std::thread::spawn(move || {
                    within(Duration::from_secs(10), || {
                        seen.requests.load(Ordering::SeqCst) > 0
                    });
                    cancel.store(true, Ordering::SeqCst);
                });
            }
            "on_first_delta" => {}
            other => panic!("unknown cancel {other}"),
        }
        let mut deltas = Vec::new();
        let outcome = llm.chat_stream_with_cancel(&request, &cancel, &mut |delta| {
            if let StreamDelta::Text(text) = delta {
                deltas.push(text.clone());
            }
            cancel.store(true, Ordering::SeqCst);
            ControlFlow::Continue(())
        });
        let refuses = case["server"] == "refuses_retryably";
        match outcome {
            Ok(response) => {
                assert_eq!(response.stop_reason.as_deref(), Some("cancelled"), "{name}");
                let expected: Vec<String> =
                    serde_json::from_value(case["deltas"].clone()).expect("deltas");
                assert_eq!(deltas, expected, "{name}");
            }
            Err(error) => assert!(refuses, "{name}: {error}"),
        }
        if refuses {
            // A retry would come after its backoff: wait the whole bound.
            std::thread::sleep(limit);
        }
        assert!(
            within(limit, || seen.open.load(Ordering::SeqCst) == 0),
            "{name}: {} connection(s) still open",
            seen.open.load(Ordering::SeqCst)
        );
        assert_eq!(
            seen.requests.load(Ordering::SeqCst),
            case["requests"].as_u64().expect("requests") as usize,
            "{name}"
        );
    }
}
