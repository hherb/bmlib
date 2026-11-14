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

//! The real HTTP client, against a **local** server.
//!
//! No test here reaches the internet: the point is to exercise the production
//! path — a socket, a real request, a real status line — without depending on a
//! remote service being up or answering the same way twice.

use bmlib::http::{default_user_agent, UreqClient, DEFAULT_TIMEOUT_SECONDS};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};

/// A one-shot HTTP server that answers with `status` and `body` to any request.
///
/// Returns the URL to call and the thread's join handle. It reads the request
/// fully — including a body when `Content-Length` says so — so a POST does not
/// deadlock against a server that never reads it.
///
/// **Draining the request is not only about the POST deadlock.** A socket closed
/// with bytes still unread in its receive queue is answered with a reset rather
/// than a FIN, and a reset can beat the client's read of the response that was
/// already written — so the caller sees a transport error for a request that was
/// served. That is measured, not reasoned: the sibling test for an undecodable
/// body used to hand-roll a server that wrote its response and closed without
/// reading, and it failed **1 run in 200** of this ten-test binary while passing
/// every time alone. Every server in this file goes through this helper for that
/// reason; a hand-rolled one reintroduces the race.
///
/// **The body is bytes**, because a PDF is bytes and the whole point of the
/// response type is to carry them unchanged.
fn serve_once(status: u16, body: impl Into<Vec<u8>>) -> (String, std::thread::JoinHandle<String>) {
    let body = body.into();
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let handle = std::thread::spawn(move || {
        let (stream, _) = listener.accept().expect("accept");
        let request = read_request(&stream);
        let mut response = format!(
            "HTTP/1.1 {status} X\r\nContent-Length: {}\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n",
            body.len()
        )
        .into_bytes();
        response.extend_from_slice(&body);
        let mut stream = stream;
        let _ = stream.write_all(&response);
        let _ = stream.flush();
        request
    });
    (format!("http://127.0.0.1:{port}/path"), handle)
}

/// Read a whole request, headers and body, and return it as text.
fn read_request(stream: &TcpStream) -> String {
    let mut reader = BufReader::new(stream.try_clone().expect("clone"));
    let mut request = String::new();
    let mut length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            break;
        }
        if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            length = value.trim().parse().unwrap_or(0);
        }
        let done = line == "\r\n" || line == "\n";
        request.push_str(&line);
        if done {
            break;
        }
    }
    if length > 0 {
        let mut body = vec![0u8; length];
        let _ = std::io::Read::read_exact(&mut reader, &mut body);
        request.push_str(&String::from_utf8_lossy(&body));
    }
    request
}

/// A **200** is a body and a status, and the request carries the user agent.
#[test]
fn a_get_reads_a_body_and_sends_the_user_agent() {
    let (url, handle) = serve_once(200, "hello");
    let response = UreqClient::new().get(&url).expect("answers");
    assert_eq!(response.status, 200);
    assert_eq!(response.body.as_slice(), b"hello");

    let request = handle.join().expect("joined");
    assert!(request.starts_with("GET /path"), "{request}");
    assert!(
        request.to_ascii_lowercase().contains("user-agent:"),
        "every endpoint this library reads wants to know who is calling: {request}"
    );
}

/// **A non-success status is a response, not an error** — the trait's contract,
/// and the reason a 404 and a dead socket are different outcomes.
///
/// This is the property the whole tier chain rests on: Europe PMC's 404 means
/// *"asked and not served"*, while no connection means *"nobody answered"*, and
/// the two store different statuses.
#[test]
fn a_not_found_is_a_response_with_its_body() {
    let (url, handle) = serve_once(404, "not here");
    let response = UreqClient::new()
        .get(&url)
        .expect("a response, not an error");
    assert_eq!(response.status, 404);
    assert_eq!(
        response.body.as_slice(),
        b"not here",
        "a rejected body is where the reason lives"
    );
    let _ = handle.join();
}

/// A **429** is likewise a response, so the caller can decide whether to back off
/// rather than seeing it as a transport failure.
#[test]
fn a_rate_limit_is_a_response() {
    let (url, handle) = serve_once(429, "slow down");
    let response = UreqClient::new().get(&url).expect("a response");
    assert_eq!(response.status, 429);
    let _ = handle.join();
}

/// A POST sends its headers and a JSON body, and reads the answer.
#[test]
fn a_post_sends_headers_and_a_json_body() {
    let (url, handle) = serve_once(200, "{\"ok\":true}");
    let mut headers = BTreeMap::new();
    headers.insert("content-type".to_string(), "application/json".to_string());
    headers.insert("authorization".to_string(), "Bearer secret".to_string());
    let response = UreqClient::new()
        .post_json(&url, &serde_json::json!({"model": "m"}), &headers)
        .expect("answers");
    assert_eq!(response.status, 200);
    assert_eq!(response.body.as_slice(), b"{\"ok\":true}");

    let request = handle.join().expect("joined");
    assert!(request.starts_with("POST /path"), "{request}");
    let lower = request.to_ascii_lowercase();
    assert!(lower.contains("authorization: bearer secret"), "{request}");
    assert!(
        lower.contains("content-type: application/json"),
        "{request}"
    );
    assert!(
        request.contains("{\"model\":\"m\"}"),
        "the body was sent: {request}"
    );
}

/// **Nothing listening is a transport failure**, which is the other half of the
/// contract — and the half that must not be confused with a 404.
#[test]
fn a_refused_connection_is_a_transport_failure() {
    // Bind and immediately drop, so the port is almost certainly free.
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    drop(listener);
    let error = UreqClient::new()
        .get(&format!("http://127.0.0.1:{port}/"))
        .expect_err("no listener");
    let message = error.to_string();
    assert!(
        !message.is_empty(),
        "a bare transport error must still say something"
    );
}

/// The default user agent names the library and its version, and **carries no
/// invented contact address** — inventing one would be a false claim about who is
/// calling.
#[test]
fn the_default_user_agent_names_the_library_only() {
    let agent = default_user_agent();
    assert!(agent.starts_with("bmlib/"), "{agent}");
    assert!(!agent.contains('@'), "no invented contact address: {agent}");
    assert_eq!(DEFAULT_TIMEOUT_SECONDS, 45);
    assert!(UreqClient::new().user_agent.starts_with("bmlib/"));
}

/// A caller can set its own user agent, which an API's terms may ask it to.
#[test]
fn a_caller_can_name_itself() {
    let (url, handle) = serve_once(200, "ok");
    let mut client = UreqClient::new();
    client.user_agent = "my-app/1.0 (mailto:me@example.org)".to_string();
    client.get(&url).expect("answers");
    let request = handle.join().expect("joined");
    assert!(
        request.contains("my-app/1.0 (mailto:me@example.org)"),
        "{request}"
    );
}

/// A body that is not UTF-8 is **carried byte for byte**, and a text read of it
/// is refused rather than silently mangled.
///
/// The refusal belongs to the reader, not the transport: the fetchers read XML
/// and JSON, so a body they cannot decode is a body they cannot read — but the
/// PDF path must be able to carry the very same bytes to the cache, which is why
/// the response holds bytes and `text()` is strict.
///
/// This test used to spin its own server so it could write raw bytes as the body.
/// That server never read the request, which is the reset race `serve_once`
/// measures in its doc comment; it now goes through the shared helper, whose
/// `Content-Length` and body are exactly what this test was writing by hand.
#[test]
fn an_undecodable_body_is_carried_and_refused_by_text() {
    let (url, handle) = serve_once(200, vec![0xffu8, 0xfeu8]);
    let response = UreqClient::new()
        .get(&url)
        .expect("bytes are not a transport failure");
    assert_eq!(
        response.body,
        vec![0xff, 0xfe],
        "the bytes served must be the bytes carried"
    );
    let error = response.text().expect_err("not UTF-8");
    assert!(matches!(error, FetchError::Transport(_)), "{error:?}");
    assert!(error.to_string().contains("not valid UTF-8"), "{error}");
    assert_eq!(
        response.text_or_empty(),
        "",
        "no text is invented for a body that has none"
    );
    let _ = handle.join();
}

/// **Every byte value `0..=255` survives a real request byte for byte.**
///
/// This is the regression test for the defect the byte body exists to close: a
/// lossy decode turned the 273 bytes of a `%PDF-1.4\n` plus `0..=255` PDF into
/// 529 bytes full of U+FFFD, and the `%PDF` prefix — ASCII, and the only thing
/// the cache checked — still matched, so the corrupt file was written and read
/// back as a healthy one.
#[test]
fn every_byte_value_survives_a_real_response() {
    let payload: Vec<u8> = (0..=255u8).collect();
    let (url, handle) = serve_once(200, payload.clone());
    let response = UreqClient::new().get(&url).expect("answers");
    assert_eq!(response.status, 200);
    assert_eq!(
        response.body, payload,
        "the body must be the bytes served, not a decoded substitute"
    );
    assert!(
        response.text().is_err(),
        "these bytes are not valid UTF-8, so a text read must refuse them"
    );
    let _ = handle.join();
}

/// The `HttpResponse::ok` helper the fetcher tests use agrees with what a real
/// response looks like.
#[test]
fn the_test_helper_matches_the_real_shape() {
    let (url, handle) = serve_once(200, "body");
    let real = UreqClient::new().get(&url).expect("answers");
    let helper = HttpResponse::ok("body");
    assert_eq!(real, helper);
    let _ = handle.join();
}

/// A one-shot server that waits `head_pause` before its response headers and
/// `body_pause` before the body, as a local model server loads the model and
/// then writes.
/// It drains the request first, for `serve_once`'s reason.
fn serve_slowly(
    head_pause: std::time::Duration,
    body_pause: std::time::Duration,
    body: &'static str,
) -> (String, std::thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let handle = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let request = read_request(&stream);
        std::thread::sleep(head_pause);
        let head = format!(
            "HTTP/1.1 200 X\r\nContent-Length: {}\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n",
            body.len()
        );
        let _ = stream.write_all(head.as_bytes());
        let _ = stream.flush();
        std::thread::sleep(body_pause);
        let _ = stream.write_all(body.as_bytes());
        let _ = stream.flush();
        request
    });
    (format!("http://127.0.0.1:{port}/path"), handle)
}

/// `n` milliseconds.
fn ms(n: u64) -> std::time::Duration {
    std::time::Duration::from_millis(n)
}

fn read_all(stream: bmlib::publications::fetchers::HttpStream) -> std::io::Result<String> {
    let mut text = String::new();
    std::io::Read::read_to_string(&mut { stream.body }, &mut text)?;
    Ok(text)
}

/// A streamed POST carries its headers and body, and hands back the body.
#[test]
fn a_stream_post_sends_headers_and_hands_back_the_body() {
    let (url, handle) = serve_once(200, "data: a\n\n");
    let headers = BTreeMap::from([("x-api-key".to_string(), "k".to_string())]);
    let stream = UreqClient::new()
        .post_json_stream(&url, &serde_json::json!({"stream": true}), &headers)
        .expect("answers");
    assert_eq!(stream.status, 200);
    assert_eq!(read_all(stream).expect("the body"), "data: a\n\n");
    let request = handle.join().expect("server").to_ascii_lowercase();
    assert!(request.contains("x-api-key: k"), "{request}");
    assert!(request.contains("{\"stream\":true}"), "{request}");
}

/// A refused stream is a status whose body can be quoted, not an error.
#[test]
fn a_refused_stream_carries_its_status_and_body() {
    let (url, handle) = serve_once(401, "bad key");
    let stream = UreqClient::new()
        .post_json_stream(&url, &serde_json::json!({}), &BTreeMap::new())
        .expect("a response");
    assert_eq!(stream.status, 401);
    assert_eq!(read_all(stream).expect("the body"), "bad key");
    handle.join().expect("server");
}

/// A stream's response headers and body each get `stream_timeout_seconds`,
/// not the ordinary timeout a long answer would outlast.
#[test]
fn a_stream_may_outlast_the_ordinary_timeout() {
    let (url, handle) = serve_slowly(ms(1_500), ms(1_500), "data: a\n\n");
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let stream = client
        .post_json_stream(&url, &serde_json::json!({}), &BTreeMap::new())
        .expect("the headers within the stream timeout");
    assert_eq!(read_all(stream).expect("the body within it"), "data: a\n\n");
    handle.join().expect("server");
}

/// ...and that timeout still holds.
#[test]
fn a_stream_still_times_out() {
    let (url, _handle) = serve_slowly(ms(2_500), ms(2_500), "data: a\n\n");
    let client = UreqClient {
        timeout_seconds: 30,
        stream_timeout_seconds: 1,
        ..UreqClient::new()
    };
    let outcome = client.post_json_stream(&url, &serde_json::json!({}), &BTreeMap::new());
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m))
            if m == "timed out after 1 s waiting for the response headers (receive response)"),
        "{outcome:?}"
    );
}

/// A non-streamed POST (a chat) gets the stream's split too (#702): a model
/// writing a long answer outlasts the ordinary timeout...
#[test]
fn a_post_may_outlast_the_ordinary_timeout() {
    let (url, handle) = serve_slowly(ms(1_500), ms(1_500), "{}");
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let response = client
        .post_json(&url, &serde_json::json!({}), &BTreeMap::new())
        .expect("the answer within the stream timeout");
    assert_eq!(response.text().expect("UTF-8"), "{}");
    handle.join().expect("server");
}

/// ...and a timeout on its response names the limit that ran out.
#[test]
fn a_post_still_times_out_naming_the_limit() {
    let (url, _handle) = serve_slowly(ms(2_500), ms(2_500), "{}");
    let client = UreqClient {
        timeout_seconds: 30,
        stream_timeout_seconds: 1,
        ..UreqClient::new()
    };
    let outcome = client.post_json(&url, &serde_json::json!({}), &BTreeMap::new());
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m))
            if m == "timed out after 1 s waiting for the response headers (receive response)"),
        "{outcome:?}"
    );
}

/// A GET keeps one limit on the whole request, also named when it runs out.
#[test]
fn a_get_times_out_on_the_whole_request() {
    let (url, _handle) = serve_slowly(ms(2_500), ms(2_500), "{}");
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let outcome = client.get(&url);
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m))
            if m == "timed out after 1 s on the whole request (global)"),
        "{outcome:?}"
    );
}

/// A POST whose headers arrive in time but whose body stalls fails on the
/// body's own limit, named (#706's review: nothing tested that limit).
#[test]
fn a_post_body_times_out_naming_the_limit() {
    let (url, _handle) = serve_slowly(ms(0), ms(2_500), "{}");
    let client = UreqClient {
        timeout_seconds: 30,
        stream_timeout_seconds: 1,
        ..UreqClient::new()
    };
    let outcome = client.post_json(&url, &serde_json::json!({}), &BTreeMap::new());
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m))
            if m == "timed out after 1 s reading the response body (receive body)"),
        "{outcome:?}"
    );
}

/// A stream whose body stalls fails while it is read, naming the limit too.
#[test]
fn a_stream_body_times_out_naming_the_limit() {
    let (url, _handle) = serve_slowly(ms(0), ms(2_500), "data: a\n\n");
    let client = UreqClient {
        timeout_seconds: 30,
        stream_timeout_seconds: 1,
        ..UreqClient::new()
    };
    let stream = client
        .post_json_stream(&url, &serde_json::json!({}), &BTreeMap::new())
        .expect("the headers in time");
    let error = read_all(stream).expect_err("the body stalls");
    assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
    assert_eq!(
        error.to_string(),
        "timed out after 1 s reading the response body (receive body)"
    );
}

/// A stream's body that ends short is `ureq`'s own error, not a timeout:
/// only a timeout is renamed.
#[test]
fn a_stream_body_cut_short_keeps_its_own_error() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let handle = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        read_request(&stream);
        let _ = stream
            .write_all(b"HTTP/1.1 200 X\r\nContent-Length: 100\r\nConnection: close\r\n\r\ndata:");
    });
    let stream = UreqClient::new()
        .post_json_stream(
            &format!("http://127.0.0.1:{port}/path"),
            &serde_json::json!({}),
            &BTreeMap::new(),
        )
        .expect("the headers");
    handle.join().expect("server");
    let error = read_all(stream).expect_err("the body ends short");
    assert_eq!(error.kind(), std::io::ErrorKind::UnexpectedEof, "{error}");
}

/// A GET with headers (a model listing) keeps the whole-request limit too.
#[test]
fn a_get_with_headers_times_out_on_the_whole_request() {
    let (url, _handle) = serve_slowly(ms(2_500), ms(2_500), "{}");
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let outcome = client.get_with_headers(&url, &BTreeMap::new());
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m))
            if m == "timed out after 1 s on the whole request (global)"),
        "{outcome:?}"
    );
}

/// A stream that may be given up (#649) reads through pauses longer than
/// its wait slices, and its limits hold and are named as they are without.
#[test]
fn a_cancellable_stream_keeps_its_limits() {
    let flag = || std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let (url, handle) = serve_slowly(ms(1_500), ms(1_500), "data: a\n\n");
    let patient = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let stream = patient
        .post_json_stream_with_cancel(&url, &serde_json::json!({}), &BTreeMap::new(), flag())
        .expect("the headers within the stream timeout");
    assert_eq!(read_all(stream).expect("the body within it"), "data: a\n\n");
    handle.join().expect("server");

    let (url, _handle) = serve_slowly(ms(2_500), ms(2_500), "data: a\n\n");
    let hasty = UreqClient {
        timeout_seconds: 30,
        stream_timeout_seconds: 1,
        ..UreqClient::new()
    };
    let outcome =
        hasty.post_json_stream_with_cancel(&url, &serde_json::json!({}), &BTreeMap::new(), flag());
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m))
            if m == "timed out after 1 s waiting for the response headers (receive response)"),
        "{outcome:?}"
    );
}

/// A stream given up before it is sent opens no request: the server reads
/// no bytes, and the caller is told why.
#[test]
fn a_stream_given_up_before_sending_sends_nothing() {
    let (url, handle) = serve_once(200, "data: a\n\n");
    let given_up = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
    let outcome = UreqClient::new().post_json_stream_with_cancel(
        &url,
        &serde_json::json!({}),
        &BTreeMap::new(),
        given_up,
    );
    assert!(
        matches!(&outcome, Err(FetchError::Transport(m)) if m.contains("given up by its caller")),
        "{outcome:?}"
    );
    assert_eq!(handle.join().expect("server"), "");
}

/// A stream given up while its body is awaited fails the read at once,
/// long before the server's pause ends.
#[test]
fn a_stream_given_up_while_reading_fails_the_read() {
    let (url, _handle) = serve_slowly(ms(0), ms(5_000), "data: a\n\n");
    let given_up = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stream = UreqClient::new()
        .post_json_stream_with_cancel(
            &url,
            &serde_json::json!({}),
            &BTreeMap::new(),
            std::sync::Arc::clone(&given_up),
        )
        .expect("the headers");
    let started = std::time::Instant::now();
    let setter = std::sync::Arc::clone(&given_up);
    std::thread::spawn(move || {
        std::thread::sleep(ms(200));
        setter.store(true, std::sync::atomic::Ordering::SeqCst);
    });
    // Only the abandoned worker sees this error, and drops it.
    read_all(stream).expect_err("the read is given up");
    assert!(started.elapsed() < ms(2_000), "{:?}", started.elapsed());
}

/// A body delimited by the connection's close, given up mid-read, fails
/// rather than reading as complete: the give-up is not taken for the
/// server closing (the review of #649).
#[test]
fn a_close_delimited_body_given_up_is_not_complete() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let url = format!("http://{}/path", listener.local_addr().expect("addr"));
    std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        read_request(&stream);
        let _ = stream.write_all(b"HTTP/1.1 200 X\r\nConnection: close\r\n\r\ndata: a\n\n");
        let _ = stream.flush();
        std::thread::sleep(ms(5_000));
    });
    let given_up = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stream = UreqClient::new()
        .post_json_stream_with_cancel(
            &url,
            &serde_json::json!({}),
            &BTreeMap::new(),
            std::sync::Arc::clone(&given_up),
        )
        .expect("the headers");
    let setter = std::sync::Arc::clone(&given_up);
    std::thread::spawn(move || {
        std::thread::sleep(ms(300));
        setter.store(true, std::sync::atomic::Ordering::SeqCst);
    });
    let error = read_all(stream).expect_err("a given-up body is not a whole one");
    assert!(
        error.to_string().contains("given up by its caller"),
        "{error}"
    );
}

/// A one-shot server that sends its headers at once, then each of `pieces`
/// after `pause`, as a large download arrives.
fn serve_in_pieces(
    pieces: &'static [&'static str],
    pause: std::time::Duration,
) -> (String, std::thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let handle = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let request = read_request(&stream);
        let length: usize = pieces.iter().map(|p| p.len()).sum();
        let head =
            format!("HTTP/1.1 200 OK\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n");
        let _ = stream.write_all(head.as_bytes());
        let _ = stream.flush();
        for piece in pieces {
            std::thread::sleep(pause);
            let _ = stream.write_all(piece.as_bytes());
            let _ = stream.flush();
        }
        request
    });
    (format!("http://127.0.0.1:{port}/path"), handle)
}

/// A streamed GET sends its headers and may take longer than the timeout as
/// a whole, so long as no read waits longer than it.
#[test]
fn a_streamed_get_may_outlast_the_timeout_while_data_keeps_coming() {
    let (url, handle) = serve_in_pieces(&["aa", "bb", "cc", "dd"], ms(400));
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 1,
        ..UreqClient::new()
    };
    let headers = BTreeMap::from([("x-amz-request-payer".to_string(), "requester".to_string())]);
    let stream = client
        .get_with_headers_stream(&url, &headers)
        .expect("the headers at once");
    assert_eq!(stream.status, 200);
    assert_eq!(read_all(stream).expect("1.6 s of pieces"), "aabbccdd");
    let request = handle.join().expect("server").to_ascii_lowercase();
    assert!(request.starts_with("get /path"), "{request}");
    assert!(
        request.contains("x-amz-request-payer: requester"),
        "{request}"
    );
}

/// ...and a body that stalls fails on the idle limit, named.
#[test]
fn a_streamed_get_that_stalls_times_out_naming_the_idle_limit() {
    let (url, _handle) = serve_in_pieces(&["aa"], ms(2_500));
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let stream = client
        .get_with_headers_stream(&url, &BTreeMap::new())
        .expect("the headers at once");
    let error = read_all(stream).expect_err("a stall");
    assert_eq!(error.kind(), std::io::ErrorKind::TimedOut, "{error}");
    assert!(
        error
            .to_string()
            .starts_with("timed out after 1 s with nothing received"),
        "{error}"
    );
}

/// A refused streamed GET carries its status and body.
#[test]
fn a_refused_streamed_get_carries_its_status_and_body() {
    let (url, handle) = serve_once(403, "<Error><Code>AccessDenied</Code></Error>");
    let stream = UreqClient::new()
        .get_with_headers_stream(&url, &BTreeMap::new())
        .expect("a response");
    assert_eq!(stream.status, 403);
    assert_eq!(
        read_all(stream).expect("the body"),
        "<Error><Code>AccessDenied</Code></Error>"
    );
    handle.join().expect("server");
}

/// A one-shot server that answers with `response`, verbatim.
fn serve_raw(response: &'static str) -> (String, std::thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let handle = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let request = read_request(&stream);
        let _ = stream.write_all(response.as_bytes());
        let _ = stream.flush();
        request
    });
    (format!("http://127.0.0.1:{port}/path"), handle)
}

/// A streamed GET follows no redirect: the 3xx is the answer, so a signed
/// request's other headers never reach another host.
#[test]
fn a_streamed_get_follows_no_redirect() {
    let (url, handle) = serve_raw(
        "HTTP/1.1 307 Temporary Redirect\r\nLocation: http://127.0.0.1:9/elsewhere\r\n\
         Content-Length: 0\r\nConnection: close\r\n\r\n",
    );
    let stream = UreqClient::new()
        .get_with_headers_stream(&url, &BTreeMap::new())
        .expect("the redirect itself");
    assert_eq!(stream.status, 307);
    handle.join().expect("server");
}

/// A streamed GET whose headers never come names the limit it set
/// (`timeout_seconds`), not the POST streams' `stream_timeout_seconds`.
#[test]
fn a_streamed_get_without_headers_times_out_naming_its_own_limit() {
    let (url, _handle) = serve_slowly(ms(2_500), ms(0), "x");
    let client = UreqClient {
        timeout_seconds: 1,
        stream_timeout_seconds: 30,
        ..UreqClient::new()
    };
    let error = client
        .get_with_headers_stream(&url, &BTreeMap::new())
        .expect_err("no headers within 1 s");
    let message = error.to_string();
    assert!(
        message.starts_with("timed out after 1 s waiting for the response headers"),
        "{message}"
    );
}

/// A success whose body ureq leaves encoded is refused: its bytes are not the
/// stored object's, which the caller checks them against.
#[test]
fn a_streamed_get_refuses_a_body_left_encoded() {
    let (url, handle) = serve_raw(
        "HTTP/1.1 200 OK\r\nContent-Encoding: deflate\r\nContent-Length: 2\r\n\
         Connection: close\r\n\r\nxx",
    );
    match UreqClient::new().get_with_headers_stream(&url, &BTreeMap::new()) {
        Err(FetchError::Malformed(message)) => {
            assert!(
                message.contains("Content-Encoding \"deflate\""),
                "{message}"
            );
        }
        other => panic!("expected Malformed, got {other:?}"),
    }
    handle.join().expect("server");
}

/// ...but `identity` is no encoding at all.
#[test]
fn a_streamed_get_reads_an_identity_body() {
    let (url, handle) = serve_raw(
        "HTTP/1.1 200 OK\r\nContent-Encoding: identity\r\nContent-Length: 2\r\n\
         Connection: close\r\n\r\nxx",
    );
    let stream = UreqClient::new()
        .get_with_headers_stream(&url, &BTreeMap::new())
        .expect("an identity body");
    assert_eq!(read_all(stream).expect("the body"), "xx");
    handle.join().expect("server");
}
