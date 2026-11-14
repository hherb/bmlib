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

use crate::llm::CANCEL_POLL_INTERVAL;
use crate::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse, HttpStream};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use ureq::unversioned::resolver::DefaultResolver;
use ureq::unversioned::transport::{
    Buffers, ConnectionDetails, Connector, DefaultConnector, NextTimeout, Transport,
};

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
    ///
    /// With `abandoned`, its connections give up once the flag is set
    /// ([`GiveUp`], #649).
    fn post_agent(&self, abandoned: Option<Arc<AtomicBool>>) -> Result<ureq::Agent, FetchError> {
        let step = Some(std::time::Duration::from_secs(self.timeout_seconds));
        let stream = Some(std::time::Duration::from_secs(self.stream_timeout_seconds));
        let config = self.config_with(
            ureq::Agent::config_builder()
                .timeout_resolve(step)
                .timeout_connect(step)
                .timeout_send_request(step)
                .timeout_send_body(step)
                .timeout_recv_response(stream)
                .timeout_recv_body(stream),
        );
        Ok(match abandoned {
            None => ureq::Agent::new_with_config(config),
            Some(abandoned) => ureq::Agent::with_parts(
                config,
                DefaultConnector::new().chain(GiveUp { abandoned }),
                DefaultResolver::default(),
            ),
        })
    }

    /// Finish an agent's configuration with what every request shares.
    fn agent_with(
        &self,
        builder: ureq::config::ConfigBuilder<ureq::typestate::AgentScope>,
    ) -> Result<ureq::Agent, FetchError> {
        Ok(ureq::Agent::new_with_config(self.config_with(builder)))
    }

    /// Finish a configuration with what every request shares.
    fn config_with(
        &self,
        builder: ureq::config::ConfigBuilder<ureq::typestate::AgentScope>,
    ) -> ureq::config::Config {
        builder
            .user_agent(self.user_agent.clone())
            // **Set explicitly rather than relied on.** `ureq`'s default is
            // already `false`, but the trait's contract is that a 404 is a
            // *response* and not an error, and a default is a thing that changes
            // between versions. With this set, a rejected request arrives with
            // its status **and its body** — which is where an API puts the message
            // that distinguishes a bad key from a bad model.
            .http_status_as_error(false)
            .build()
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
            // A streamed GET's idle limit (`get_with_headers_stream`).
            ureq::Timeout::PerRead => (self.timeout_seconds, "with nothing received"),
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

    /// Every step up to the response headers within `timeout_seconds`, then
    /// each read of the body within it too (`ureq`'s per-read limit, an idle
    /// limit): a large body is never cut off for taking long, only for
    /// stalling.
    fn get_with_headers_stream(
        &self,
        url: &str,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        let step = Some(std::time::Duration::from_secs(self.timeout_seconds));
        let agent = ureq::Agent::new_with_config(
            self.config_with(
                ureq::Agent::config_builder()
                    .timeout_resolve(step)
                    .timeout_connect(step)
                    .timeout_send_request(step)
                    .timeout_recv_response(step)
                    .timeout_per_read(step)
                    // No redirect is followed: one would carry the signed
                    // request's other headers (a session token) to a new host
                    // without its signature. A 3xx is answered as a response.
                    .max_redirects(0),
            ),
        );
        let mut request = agent.get(url);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        let response = match request.call() {
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
            // `into_reader` has no size cap, unlike `read_to_vec`.
            body: Box::new(NamedTimeouts {
                body: response.into_body().into_reader(),
                client: self.clone(),
            }),
        })
    }

    fn post_json(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let agent = self.post_agent(None)?;
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
        self.open_stream(url, body, headers, None)
    }

    /// A wait for the server fails within [`CANCEL_POLL_INTERVAL`] of
    /// `abandoned` being set, and nothing is written after it, so the server
    /// sees the connection close. A write or a connect already under way runs
    /// to its own limit (`timeout_seconds`) first.
    fn post_json_stream_with_cancel(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &BTreeMap<String, String>,
        abandoned: Arc<AtomicBool>,
    ) -> Result<HttpStream, FetchError> {
        self.open_stream(url, body, headers, Some(abandoned))
    }
}

impl UreqClient {
    /// [`HttpClient::post_json_stream`], given up once `abandoned` is set
    /// when there is one.
    fn open_stream(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &BTreeMap<String, String>,
        abandoned: Option<Arc<AtomicBool>>,
    ) -> Result<HttpStream, FetchError> {
        let agent = self.post_agent(abandoned)?;
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

/// What a connection given up by its caller fails with.
///
/// `Other`, because the kinds a reader might pick mean something else to
/// `ureq` or `std`: `ureq` reads `UnexpectedEof`, `ConnectionAborted` and
/// `ConnectionReset` as the server closing, which ends a body delimited by
/// the close as if it were complete, and `std`'s readers retry an
/// `Interrupted` read at once, which would spin.
fn given_up() -> ureq::Error {
    ureq::Error::Io(std::io::Error::other(
        "the request was given up by its caller",
    ))
}

/// The last link of a stream's connector chain: wraps each connection
/// `ureq`'s default chain opens (TCP, then TLS) in [`GivesUp`], and fails
/// a connection opened once `abandoned` is set, before any request is sent
/// on it (#649).
///
/// Built on `ureq`'s `unversioned` transport API, which may change in a
/// minor release; the tests in this module drive it over real sockets.
#[derive(Debug)]
struct GiveUp {
    /// Set by the caller when it stops waiting for the stream.
    abandoned: Arc<AtomicBool>,
}

impl Connector<Box<dyn Transport>> for GiveUp {
    type Out = Box<dyn Transport>;

    fn connect(
        &self,
        _details: &ConnectionDetails,
        chained: Option<Box<dyn Transport>>,
    ) -> Result<Option<Self::Out>, ureq::Error> {
        // Checked once the connection is open, since the chain before this
        // opens it: a stream given up while connecting sends nothing.
        if self.abandoned.load(Ordering::SeqCst) {
            return Err(given_up());
        }
        Ok(chained.map(|inner| {
            Box::new(GivesUp {
                inner,
                abandoned: Arc::clone(&self.abandoned),
            }) as Box<dyn Transport>
        }))
    }
}

/// A connection that fails its wait for the server once `abandoned` is set.
///
/// A blocked socket read cannot be woken from another thread, and `ureq`
/// hands out no socket to shut down. So a wait for input is cut into
/// slices of [`CANCEL_POLL_INTERVAL`], with `abandoned` looked at between
/// them. A slice that times out has read nothing, so the next slice reads
/// on as the whole wait would have, and the wait's own limit is kept: once
/// it is reached, the timeout `ureq` names is the one it set. Once
/// `abandoned` is set, the wait fails and `ureq` drops the connection,
/// which closes it; nothing more is written to it either. A write already
/// under way is not sliced (a write cut short would lose its place), so it
/// runs to its own limit.
#[derive(Debug)]
struct GivesUp {
    /// The connection `ureq`'s default chain opened.
    inner: Box<dyn Transport>,
    /// Set by the caller when it stops waiting for the stream.
    abandoned: Arc<AtomicBool>,
}

impl GivesUp {
    /// [`given_up`] once the caller has stopped waiting.
    fn check(&self) -> Result<(), ureq::Error> {
        if self.abandoned.load(Ordering::SeqCst) {
            Err(given_up())
        } else {
            Ok(())
        }
    }
}

impl Transport for GivesUp {
    fn buffers(&mut self) -> &mut dyn Buffers {
        self.inner.buffers()
    }

    fn transmit_output(&mut self, amount: usize, timeout: NextTimeout) -> Result<(), ureq::Error> {
        self.check()?;
        self.inner.transmit_output(amount, timeout)
    }

    fn await_input(&mut self, timeout: NextTimeout) -> Result<bool, ureq::Error> {
        // A wait already out of time fails at once, as `ureq`'s own
        // transports do (`not_zero` would grant it a second).
        if timeout.after.is_zero() {
            return Err(ureq::Error::Timeout(timeout.reason));
        }
        let started = std::time::Instant::now();
        // `None`: the wait has no limit of its own.
        let limit = timeout.not_zero().map(|d| *d);
        loop {
            self.check()?;
            let left = limit.map(|l| l.saturating_sub(started.elapsed()));
            if left.is_some_and(|l| l.is_zero()) {
                return Err(ureq::Error::Timeout(timeout.reason));
            }
            let slice = left.map_or(CANCEL_POLL_INTERVAL, |l| l.min(CANCEL_POLL_INTERVAL));
            let sliced = NextTimeout {
                after: slice.into(),
                ..timeout
            };
            match self.inner.await_input(sliced) {
                // The slice ran out, not the wait: read on. shortcut: `ureq`
                // 3.5's per-read limit, longer than a slice, never fires here
                // (each slice is shorter); bmlib sets none, so honour it if a
                // client ever does.
                Err(ureq::Error::Timeout(reason))
                    if reason == timeout.reason && left.is_none_or(|l| l > slice) => {}
                other => return other,
            }
        }
    }

    fn is_open(&mut self) -> bool {
        !self.abandoned.load(Ordering::SeqCst) && self.inner.is_open()
    }

    fn is_tls(&self) -> bool {
        self.inner.is_tls()
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
        // A stream that may be given up (#649) keeps the same limits.
        let abandonable = Some(Arc::new(AtomicBool::new(false)));
        for abandoned in [None, abandonable] {
            let post = client().post_agent(abandoned).unwrap().config().timeouts();
            assert_eq!(post.global, None);
            assert_eq!(post.per_call, None);
            assert_eq!(post.resolve, s(7));
            assert_eq!(post.connect, s(7));
            assert_eq!(post.send_request, s(7));
            assert_eq!(post.send_body, s(7));
            assert_eq!(post.recv_response, s(99));
            assert_eq!(post.recv_body, s(99));
        }
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
