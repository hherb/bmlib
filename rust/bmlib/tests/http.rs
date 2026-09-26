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
#[test]
fn an_undecodable_body_is_carried_and_refused_by_text() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let handle = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let _ = stream
            .write_all(b"HTTP/1.1 200 X\r\nContent-Length: 2\r\nConnection: close\r\n\r\n\xff\xfe");
        let _ = stream.flush();
    });
    let response = UreqClient::new()
        .get(&format!("http://127.0.0.1:{port}/"))
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
