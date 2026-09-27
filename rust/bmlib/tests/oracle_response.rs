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

//! The corpus's shared response reader — `tests/common/oracle.rs`.
//!
//! Its own binary rather than a `#[cfg(test)]` module inside `common`, because
//! everything in `tests/common/` is compiled into **every** test binary that
//! declares `mod common;` (five of them): a test written there runs five times
//! while looking like one.

mod common;

use bmlib::publications::fetchers::FetchError;
use common::oracle::{response, Response};
use serde_json::json;

/// The three shapes the corpus writes, read as the three things they mean.
#[test]
fn the_three_shapes_are_read_as_what_they_mean() {
    assert!(matches!(
        response(&json!({"collection": [], "messages": []})),
        Response::Body(_)
    ));
    // A bare string is a body too: the walkers refuse it by shape, which is a
    // case of its own (`fetch/string-payload`).
    assert!(matches!(response(&json!("text")), Response::Body(_)));
    assert!(matches!(
        response(&json!({"http_status": 500, "body": {"collection": []}})),
        Response::Status(500)
    ));
    assert!(matches!(
        response(&json!({
            "transport_error": {"name": "ConnectError", "message": "connection refused"}
        })),
        Response::Transport(message) if message == "connection refused"
    ));
}

/// **A marker is recognised by its whole key set, not by one key.** An object
/// that merely *carries* `http_status`, or that pairs `transport_error` with
/// something else, is a body — so a real payload cannot be mistaken for a
/// marker, and a marker written in the wrong shape is not silently accepted
/// either.
#[test]
fn a_payload_that_merely_carries_a_marker_key_is_a_body() {
    assert!(matches!(
        response(&json!({"http_status": 500, "extra": 1})),
        Response::Body(_)
    ));
    assert!(matches!(
        response(&json!({"transport_error": "boom", "extra": 1})),
        Response::Body(_)
    ));
}

/// **Every marker fails closed.** A marker whose keys or types are wrong panics
/// rather than being read as a body, because reading a marker as a body is
/// exactly what made `fetch/http-error` vacuous for a release: both sides served
/// a *list*, agreed with each other, and passed (#349).
#[test]
#[should_panic(expected = "http_status is an integer")]
fn a_non_integer_status_fails_closed() {
    let _ = response(&json!({"http_status": "500"}));
}

#[test]
#[should_panic(expected = "transport_error carries an object")]
fn a_transport_marker_that_is_not_an_object_fails_closed() {
    let _ = response(&json!({"transport_error": "boom"}));
}

#[test]
#[should_panic(expected = "transport_error carries exactly name and message")]
fn a_transport_marker_missing_its_message_fails_closed() {
    let _ = response(&json!({"transport_error": {"name": "ConnectError"}}));
}

#[test]
#[should_panic(expected = "transport_error.name is a string")]
fn a_transport_name_that_is_not_a_string_fails_closed() {
    let _ = response(&json!({"transport_error": {"name": 7, "message": "refused"}}));
}

#[test]
#[should_panic(expected = "transport_error.name is Python's exception name")]
fn an_empty_transport_name_fails_closed() {
    let _ = response(&json!({"transport_error": {"name": "", "message": "refused"}}));
}

/// The fourth variant is the **harness's**, not the corpus's: a named test
/// injects a failure straight into the page source, and this is the conversion
/// it goes through.
#[test]
fn a_named_test_can_inject_a_failure_directly() {
    let injected: Response = Err(FetchError::Transport("connection reset".to_string())).into();
    assert!(matches!(
        injected,
        Response::Error(FetchError::Transport(message)) if message == "connection reset"
    ));
}
