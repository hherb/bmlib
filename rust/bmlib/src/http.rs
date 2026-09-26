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

use crate::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse};
use std::collections::BTreeMap;

/// How long a request may take before it is abandoned.
///
/// A default, because the alternative is a walk that hangs for ever on a server
/// holding the connection open — and the fetchers' loops are long enough that one
/// stall costs the whole day's work.
pub const DEFAULT_TIMEOUT_SECONDS: u64 = 45;

/// A blocking HTTP client over `ureq`.
#[derive(Debug, Clone)]
pub struct UreqClient {
    /// The user agent every request carries.
    ///
    /// Several of the endpoints this library reads **ask a caller to identify
    /// itself**, and one of them refuses a request that does not: a bare
    /// client-library token is answered with a 403 by at least one registry.
    /// Naming bmlib and a contact address is the polite form those services
    /// document.
    pub user_agent: String,
    /// Seconds before a request is abandoned.
    pub timeout_seconds: u64,
}

impl Default for UreqClient {
    fn default() -> Self {
        UreqClient {
            user_agent: default_user_agent(),
            timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
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

    /// The blocking agent a request runs on.
    fn agent(&self) -> Result<ureq::Agent, FetchError> {
        let config = ureq::Agent::config_builder()
            .timeout_global(Some(std::time::Duration::from_secs(self.timeout_seconds)))
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
}

/// Read a `ureq` response into a [`HttpResponse`], whatever its status.
///
/// **A non-success status is a response, not an error** — the trait's contract,
/// and for good reason: what a walker does with a 404 or a 429 differs from what
/// it does with no connection at all.
fn read_response(
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
        Err(other) => return Err(FetchError::Transport(other.to_string())),
    };
    let status = response.status().as_u16();
    // **Bytes, never a string.** `read_to_string` rejects a body that is not
    // valid UTF-8, and what a caller does about that differs: a PDF is a
    // perfectly good body that must not be decoded at all. Reading the wire form
    // here is what lets `HttpResponse::text` decide, strictly, per caller.
    let body = response
        .into_body()
        .read_to_vec()
        .map_err(|e| FetchError::Transport(e.to_string()))?;
    Ok(HttpResponse::from_bytes(status, body))
}

impl HttpClient for UreqClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        let agent = self.agent()?;
        read_response(agent.get(url).call())
    }

    fn post_json(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        let agent = self.agent()?;
        let mut request = agent.post(url);
        for (name, value) in headers {
            request = request.header(name, value);
        }
        // A `ureq` send error and a JSON-encoding error are both transport
        // failures: neither means the remote answered.
        let payload = serde_json::to_string(body).map_err(|e| {
            FetchError::Transport(format!("could not encode the request body: {e}"))
        })?;
        read_response(request.send(payload))
    }
}
