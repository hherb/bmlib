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

//! The real HTTP client, behind [`HttpClient`].
//!
//! The trait is what every fetcher, the LLM transport and the transparency
//! analyzer are written against — which is what makes each of them testable
//! without a network. This is the one implementation that actually goes out, and
//! it is deliberately the only place in the crate that knows how.

use crate::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse, HttpStream};
use std::collections::BTreeMap;

/// How long a GET may take before it is abandoned, and each step of sending
/// a POST ([`DEFAULT_STREAM_TIMEOUT_SECONDS`] covers a POST's response).
///
/// A default, because the alternative is a walk that hangs for ever on a server
/// holding the connection open — and the fetchers' loops are long enough that one
/// stall costs the whole day's work.
pub const DEFAULT_TIMEOUT_SECONDS: u64 = 45;

/// How long a POST's response may take: its headers, then its body.
///
/// [`DEFAULT_TIMEOUT_SECONDS`] covers a whole GET, and a model's answer
/// outlasts it: a model writing a few thousand tokens takes minutes, streamed
/// or not (#702: a thinking model's large `max_tokens` timed out a
/// non-streamed chat at 45 s instead of stopping at its length). `ureq` has no
/// idle timeout, so a POST ([`HttpClient::post_json`] and
/// [`HttpClient::post_json_stream`]) gets the ordinary timeout for each step of
/// sending the request, then this for the response headers (a local server may
/// load the model before it answers) and this again for the body. Python's
/// Anthropic and OpenAI SDKs read for 600 s by default, so the two agree.
pub const DEFAULT_STREAM_TIMEOUT_SECONDS: u64 = 600;

/// A blocking HTTP client over `ureq`.
#[derive(Debug, Clone)]
pub struct UreqClient {
    /// The user agent every request carries.
    ///
    /// Several of the endpoints this library reads **ask a caller to identify
    /// itself**; naming bmlib and a contact address is the polite form those
    /// services document. ClinicalTrials.gov refused every shape not
    /// carrying `python-httpx` with a 403 on 2026-09-06 and served the default
    /// on 2026-11-07 (issue #606); `tests/live_network.rs` checks it.
    pub user_agent: String,
    /// Seconds before a GET is abandoned. For a POST, the limit on each step
    /// of sending it: looking up the host, connecting, then the request
    /// headers, then the request body.
    pub timeout_seconds: u64,
    /// Seconds a POST's response headers may take, and then its body: a
    /// non-streamed chat ([`HttpClient::post_json`]) as well as a stream
    /// ([`HttpClient::post_json_stream`]), since both wait on a model writing.
    pub stream_timeout_seconds: u64,
}

impl Default for UreqClient {
    fn default() -> Self {
        UreqClient {
            user_agent: default_user_agent(),
            timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
            stream_timeout_seconds: DEFAULT_STREAM_TIMEOUT_SECONDS,
        }
    }
}

/// The user agent bmlib sends when a caller names none.
///
/// Carries no contact address, because inventing one would be a false claim about
/// who is calling. A caller reading an API's terms should set their own through
/// [`UreqClient::user_agent`].
#[must_use]
pub fn default_user_agent() -> String {
    format!("bmlib/{}", env!("CARGO_PKG_VERSION"))
}

impl UreqClient {
    /// A client with the default user agent and timeout.
    #[must_use]
    pub fn new() -> Self {
        UreqClient::default()
    }

    /// The blocking agent a GET runs on: [`UreqClient::timeout_seconds`] for
    /// the whole request.
    fn agent(&self) -> Result<ureq::Agent, FetchError> {
        self.agent_with(
            ureq::Agent::config_builder()
                .timeout_global(Some(std::time::Duration::from_secs(self.timeout_seconds))),
        )
    }

    /// The blocking agent a POST runs on: [`UreqClient::timeout_seconds`] for
    /// each step of sending (the host lookup included: with no whole-request
    /// limit, `ureq` would otherwise wait on DNS as long as the system does),
    /// then [`UreqClient::stream_timeout_seconds`] for the response headers and
    /// again for the whole response body.
    fn post_agent(&self) -> Result<ureq::Agent, FetchError> {
        let step = Some(std::time::Duration::from_secs(self.timeout_seconds));
        let stream = Some(std::time::Duration::from_secs(self.stream_timeout_seconds));
        self.agent_with(
            ureq::Agent::config_builder()
                .timeout_resolve(step)
                .timeout_connect(step)
                .timeout_send_request(step)
                .timeout_send_body(step)
                .timeout_recv_response(stream)
                .timeout_recv_body(stream),
        )
    }

    /// Finish an agent's configuration with what every request shares.
    fn agent_with(
        &self,
        builder: ureq::config::ConfigBuilder<ureq::typestate::AgentScope>,
    ) -> Result<ureq::Agent, FetchError> {
        let config = builder
            .user_agent(self.user_agent.clone())
            // **Set explicitly rather than relied on.** `ureq`'s default is
            // already `false`, but the trait's contract is that a 404 is a
            // *response* and not an error, and a default is a thing that changes
            // between versions. With this set, a rejected request arrives with
            // its status **and its body** — which is where an API puts the message
            // that distinguishes a bad key from a bad model.
            .http_status_as_error(false)
            .build();
        Ok(ureq::Agent::new_with_config(config))
    }

    /// A `ureq` failure as a transport failure, naming the limit when it is a
    /// timeout, so a caller sees "timed out after 600 s waiting for the
    /// response headers" rather than a bare "timeout: receive response".
    fn transport_error(&self, error: &ureq::Error) -> FetchError {
        match error {
            ureq::Error::Timeout(kind) => FetchError::Transport(self.timeout_message(*kind)),
            other => FetchError::Transport(other.to_string()),
        }
    }

    /// What a timeout of `kind` says: the limit that ran out, in seconds, and
    /// what was being waited for, such as "timed out after 600 s waiting for
    /// the response headers (receive response)".
    fn timeout_message(&self, kind: ureq::Timeout) -> String {
        let (seconds, waiting) = match kind {
            ureq::Timeout::RecvResponse => (
                self.stream_timeout_seconds,
                "waiting for the response headers",
            ),
            ureq::Timeout::RecvBody => (self.stream_timeout_seconds, "reading the response body"),
            ureq::Timeout::Resolve => (self.timeout_seconds, "looking up the host"),
            ureq::Timeout::Connect => (self.timeout_seconds, "connecting"),
            ureq::Timeout::SendRequest | ureq::Timeout::SendBody => {
                (self.timeout_seconds, "sending the request")
            }
            // `Global` is a GET's only limit; the others are not set here.
            _ => (self.timeout_seconds, "on the whole request"),
        };
        format!("timed out after {seconds} s {waiting} ({kind})")
    }
}

/// A stream's body that names the limit when a read times out, as
/// [`UreqClient::transport_error`] does for everything before the body.
///
/// `ureq` reports a timeout while the body is read as an `io::Error` wrapping
/// its own error, which would read "timeout: receive body"; this one reads
/// "timed out after 600 s reading the response body (receive body)". Any
/// other read error passes through unchanged.
struct NamedTimeouts {
    /// The response body, as `ureq` hands it out.
    body: ureq::BodyReader<'static>,
    /// The client the stream was opened with, for its limits.
    client: UreqClient,
}

impl std::io::Read for NamedTimeouts {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        self.body.read(buf).map_err(|e| {
            let kind = match e
                .get_ref()
                .and_then(|inner| inner.downcast_ref::<ureq::Error>())
            {
                Some(ureq::Error::Timeout(kind)) => *kind,
                _ => return e,
            };
            std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                self.client.timeout_message(kind),
            )
        })
    }
}

/// Read a `ureq` response into a [`HttpResponse`], whatever its status.
///
/// **A non-success status is a response, not an error** — the trait's contract,
/// and for good reason: what a walker does with a 404 or a 429 differs from what
/// it does with no connection at all.
fn read_response(
    client: &UreqClient,
    result: Result<ureq::http::Response<ureq::Body>, ureq::Error>,
) -> Result<HttpResponse, FetchError> {
    let response = match result {
        Ok(response) => response,
        // Reachable only if the agent is configured to raise on status. Kept
        // because the status is an answer and losing the *body* is what makes
        // "HTTP 401" read the same as "HTTP 403"; the body is unavailable on this
        // path, which is a reason to keep the configuration above rather than to
        // drop this arm.
        Err(ureq::Error::StatusCode(code)) => {
            return Ok(HttpResponse::from_bytes(code, Vec::new()));
        }
        Err(other) => return Err(client.transport_error(&other)),
    };
    let status = response.status().as_u16();
    // **Bytes, never a string.** `read_to_string` rejects a body that is not
    // valid UTF-8, and what a caller does about that differs: a PDF is a
    // perfectly good body that must not be decoded at all. Reading the wire form
    // here is what lets `HttpResponse::text` decide, strictly, per caller.
    let body = response
        .into_body()
        .read_to_vec()
        .map_err(|e| client.transport_error(&e))?;
    Ok(HttpResponse::from_bytes(status, body))
}

impl HttpClient for UreqClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        let agent = self.agent()?;
        read_response(self, agent.get(url).call())
    }

    fn get_with_headers(
        &self,
        url: &str,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let agent = self.agent()?;
        let mut request = agent.get(url);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        read_response(self, request.call())
    }

    fn post_json(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let agent = self.post_agent()?;
        let mut request = agent.post(url);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        // A `ureq` send error and a JSON-encoding error are both transport
        // failures: neither means the remote answered.
        let payload = serde_json::to_string(body).map_err(|e| {
            FetchError::Transport(format!("could not encode the request body: {e}"))
        })?;
        read_response(self, request.send(payload))
    }

    fn post_json_stream(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        let agent = self.post_agent()?;
        let mut request = agent.post(url);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        // `Config`, not `Transport`: sending it again would fail the same way.
        let payload = serde_json::to_string(body)
            .map_err(|e| FetchError::Config(format!("could not encode the request body: {e}")))?;
        let response = match request.send(payload) {
            Ok(response) => response,
            // As in `read_response`: unreachable with `http_status_as_error`
            // off, and the status is still an answer.
            Err(ureq::Error::StatusCode(code)) => {
                return Ok(HttpStream {
                    status: code,
                    body: Box::new(std::io::empty()),
                });
            }
            Err(other) => return Err(self.transport_error(&other)),
        };
        Ok(HttpStream {
            status: response.status().as_u16(),
            // `into_reader` has no size cap, unlike `read_to_vec`: a stream's
            // length is bounded by `stream_timeout_seconds` instead.
            body: Box::new(NamedTimeouts {
                body: response.into_body().into_reader(),
                client: self.clone(),
            }),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A client whose two limits differ, so a message shows which it named.
    fn client() -> UreqClient {
        UreqClient {
            timeout_seconds: 7,
            stream_timeout_seconds: 99,
            ..UreqClient::default()
        }
    }

    /// Every timeout this client sets names its own limit; `Global` is a GET's.
    #[test]
    fn each_timeout_names_its_limit() {
        let cases = [
            (
                ureq::Timeout::Resolve,
                "timed out after 7 s looking up the host (resolve)",
            ),
            (
                ureq::Timeout::Connect,
                "timed out after 7 s connecting (connect)",
            ),
            (
                ureq::Timeout::SendRequest,
                "timed out after 7 s sending the request (send request)",
            ),
            (
                ureq::Timeout::SendBody,
                "timed out after 7 s sending the request (send body)",
            ),
            (
                ureq::Timeout::RecvResponse,
                "timed out after 99 s waiting for the response headers (receive response)",
            ),
            (
                ureq::Timeout::RecvBody,
                "timed out after 99 s reading the response body (receive body)",
            ),
            (
                ureq::Timeout::Global,
                "timed out after 7 s on the whole request (global)",
            ),
        ];
        for (kind, expected) in cases {
            let error = client().transport_error(&ureq::Error::Timeout(kind));
            assert_eq!(
                error,
                FetchError::Transport(expected.to_string()),
                "{kind:?}"
            );
        }
    }

    /// A POST's agent bounds every step, the host lookup included, and sets
    /// no whole-request limit (#706); a GET's has that limit alone. Read off
    /// the agent, since no test can make DNS stall.
    #[test]
    fn each_agent_sets_its_limits() {
        let s = |n| Some(std::time::Duration::from_secs(n));
        let post = client().post_agent().unwrap().config().timeouts();
        assert_eq!(post.global, None);
        assert_eq!(post.per_call, None);
        assert_eq!(post.resolve, s(7));
        assert_eq!(post.connect, s(7));
        assert_eq!(post.send_request, s(7));
        assert_eq!(post.send_body, s(7));
        assert_eq!(post.recv_response, s(99));
        assert_eq!(post.recv_body, s(99));
        let get = client().agent().unwrap().config().timeouts();
        assert_eq!(get.global, s(7));
        assert_eq!(get.resolve, None);
        assert_eq!(get.recv_body, None);
    }

    /// Anything that is not a timeout keeps `ureq`'s own words.
    #[test]
    fn other_errors_keep_their_text() {
        let error = ureq::Error::Io(std::io::Error::other("boom"));
        let expected = FetchError::Transport(error.to_string());
        assert_eq!(client().transport_error(&error), expected);
    }
}
