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

//! The corpus's response vocabulary, read the same way by every harness that
//! scripts a fetcher's transport.
//!
//! One home because the copies had already drifted: the marker reader was
//! written into `tests/biorxiv.rs` and `tests/openalex.rs` for #349, and the
//! transport marker would have made a third. The Python dumpers read the same
//! three shapes through `rust/oracle/_oracle.py` — that file and this one are
//! the two halves of one contract, and a change to the corpus's shape belongs in
//! both (#361).

use bmlib::publications::fetchers::FetchError;
use serde_json::Value;

/// One scripted response.
///
/// Three of the four variants are the **corpus's** vocabulary; the fourth is the
/// harness's, for a named test that injects a failure directly rather than
/// through JSON.
pub enum Response {
    /// A bare body, HTTP 200.
    Body(Value),
    /// The source answered with a non-success status.
    Status(u16),
    /// The request never arrived, so there is no response at all.
    ///
    /// The message is the transport's. Python's httpx *subclass* name
    /// (`ConnectError`, `ReadTimeout`, `ReadError`) is deliberately **not**
    /// carried: the port has one [`FetchError::Transport`] for all of them and
    /// names the base class, which is the §9 divergence each case's `corrected`
    /// block records (#361).
    Transport(String),
    /// A failure a named test injects straight into the page source.
    Error(FetchError),
}

impl From<Result<Value, FetchError>> for Response {
    fn from(result: Result<Value, FetchError>) -> Self {
        match result {
            Ok(value) => Response::Body(value),
            Err(error) => Response::Error(error),
        }
    }
}

/// Read one corpus payload.
///
/// Three shapes: a bare value (the body), `{"http_status": N, "body": B}` for a
/// status the source answered with, and
/// `{"transport_error": {"name": N, "message": M}}` for a request that never
/// arrived.
///
/// **Every marker fails closed.** A payload carrying a marker with the wrong
/// keys or types panics rather than being read as a body, because reading a
/// marker as a body is exactly what made `fetch/http-error` vacuous for a
/// release: both sides served a *list* and agreed with each other (#349).
#[must_use]
pub fn response(payload: &Value) -> Response {
    if let Some(object) = payload.as_object() {
        if object.len() == 1 {
            if let Some(marker) = object.get("transport_error") {
                let marker = marker.as_object().unwrap_or_else(|| {
                    panic!("transport_error carries an object with name and message: {payload}")
                });
                assert!(
                    marker.len() == 2
                        && marker.contains_key("name")
                        && marker.contains_key("message"),
                    "transport_error carries exactly name and message: {payload}"
                );
                let name = marker["name"]
                    .as_str()
                    .unwrap_or_else(|| panic!("transport_error.name is a string: {payload}"));
                assert!(
                    !name.is_empty(),
                    "transport_error.name is Python's exception name: {payload}"
                );
                let message = marker["message"]
                    .as_str()
                    .unwrap_or_else(|| panic!("transport_error.message is a string: {payload}"));
                return Response::Transport(message.to_string());
            }
        }
        let is_marker = object.contains_key("http_status")
            && object
                .keys()
                .all(|key| key == "http_status" || key == "body");
        if is_marker {
            let status = object["http_status"]
                .as_u64()
                .unwrap_or_else(|| panic!("http_status is an integer: {payload}"));
            return Response::Status(status as u16);
        }
    }
    Response::Body(payload.clone())
}
