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

//! The apps keep their own HTTP transports: a **SKETCH** that compiles and is
//! not wired up, behind the non-default `http-sketch` feature so it is in no
//! binary or binding the apps ship (issue #492). Its open design questions are
//! listed on that issue.

use std::collections::HashMap;
use std::sync::Arc;

use bmlib::publications::fetchers::registry as reg;

/// A response from the app's transport.
#[derive(Debug, Clone, uniffi::Record)]
pub struct ForeignHttpResponse {
    /// HTTP status.
    pub status: u16,
    /// Raw body bytes (a PDF is not a string).
    pub body: Vec<u8>,
}

/// Why the app's transport produced no response. A non-2xx status is **not**
/// this — it is a [`ForeignHttpResponse`], as in bmlib.
#[derive(Debug, uniffi::Error)]
pub enum ForeignHttpError {
    /// The request never completed: no connection, TLS, a timeout.
    Transport {
        /// The transport's message (see `JatsError::Xml` on the name).
        reason: String,
    },
    /// The app's transport threw something other than a [`ForeignHttpError`]
    /// — a bug in the adapter, not the network. Kept apart from
    /// [`Transport`](ForeignHttpError::Transport) so it is never recorded as
    /// the source not answering.
    Unexpected {
        /// The foreign error's description.
        reason: String,
    },
}

impl std::fmt::Display for ForeignHttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ForeignHttpError::Transport { reason } => write!(f, "transport failed: {reason}"),
            ForeignHttpError::Unexpected { reason } => {
                write!(
                    f,
                    "the app's transport raised an unexpected error: {reason}"
                )
            }
        }
    }
}
impl std::error::Error for ForeignHttpError {}

/// Any Swift `Error` or Kotlin exception the callback throws that is not a
/// [`ForeignHttpError`] arrives here.
impl From<uniffi::UnexpectedUniFFICallbackError> for ForeignHttpError {
    fn from(e: uniffi::UnexpectedUniFFICallbackError) -> Self {
        ForeignHttpError::Unexpected { reason: e.reason }
    }
}

/// The app's HTTP transport, implemented in Swift (`URLSession`) or Kotlin
/// (`OkHttp`).
///
/// **The transport owns policy bmlib's own client sets**, and an
/// implementation must provide it:
/// - identify the caller in a `User-Agent` (bmlib's `UreqClient` sends
///   `bmlib/<version>`; some APIs ask callers to name themselves, and one
///   registry refuses a bare client-library token);
/// - apply a timeout (bmlib's client uses 45 s overall; the full-text service's
///   own timeout does not reach an injected transport);
/// - follow redirects (the DOI tiers depend on it);
/// - send `Content-Type: application/json` on [`post_json`](Self::post_json)
///   unless `headers` names one;
/// - return a non-2xx status as a response, never as an error;
/// - pacing, retries and 429 handling (bmlib's `HttpClient` does none).
///
/// **Blocking by design**: bmlib's `HttpClient` is synchronous, so the foreign
/// implementation blocks the thread that called into Rust. Call into Rust from
/// a dedicated thread or dispatch queue — never from the main thread, and
/// never from a Swift concurrency task, whose cooperative pool a blocked
/// thread can deadlock. There is no cancellation: an app-side cancel can only
/// surface as an error.
#[uniffi::export(with_foreign)]
pub trait ForeignHttpClient: Send + Sync {
    /// GET `url`.
    fn get(&self, url: String) -> Result<ForeignHttpResponse, ForeignHttpError>;
    /// POST a JSON body.
    fn post_json(
        &self,
        url: String,
        body_json: String,
        headers: HashMap<String, String>,
    ) -> Result<ForeignHttpResponse, ForeignHttpError>;
}

/// Adapts the foreign transport to bmlib's `HttpClient` trait.
struct ForeignTransport(Arc<dyn ForeignHttpClient>);

/// bmlib's `FetchError` has no variant for a defect on the caller's side, so an
/// [`ForeignHttpError::Unexpected`] cannot yet reach the tier chain as one; it
/// is carried as a transport failure whose text says what it is (issue #492).
fn fetch_error(e: ForeignHttpError) -> reg::FetchError {
    reg::FetchError::Transport(e.to_string())
}

impl reg::HttpClient for ForeignTransport {
    fn get(&self, url: &str) -> Result<reg::HttpResponse, reg::FetchError> {
        self.0
            .get(url.to_string())
            .map(|r| reg::HttpResponse::from_bytes(r.status, r.body))
            .map_err(fetch_error)
    }

    fn post_json(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &std::collections::BTreeMap<String, String>,
    ) -> Result<reg::HttpResponse, reg::FetchError> {
        self.0
            .post_json(
                url.to_string(),
                body.to_string(),
                headers
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            )
            .map(|r| reg::HttpResponse::from_bytes(r.status, r.body))
            .map_err(fetch_error)
    }
}

/// Constructs bmlib's `FullTextService` over the app's transport but exposes
/// none of it. `fetch_status` calls the app's transport directly, so an error
/// keeps its variant (bmlib's `FetchError` would flatten it); the
/// `ForeignTransport` adapter the tier chain would use is exercised by this
/// crate's tests. It shows the callback interface compiles and round-trips; it
/// is not a full-text API.
#[derive(uniffi::Object)]
pub struct BmlibFullTextService {
    client: Arc<dyn ForeignHttpClient>,
    #[allow(dead_code)]
    inner: bmlib::fulltext::FullTextService,
}

#[uniffi::export]
impl BmlibFullTextService {
    /// Build the service over the app's transport.
    #[uniffi::constructor]
    pub fn new(client: Arc<dyn ForeignHttpClient>, email: String) -> Arc<Self> {
        let transport: Arc<dyn reg::HttpClient + Send + Sync> =
            Arc::new(ForeignTransport(client.clone()));
        Arc::new(BmlibFullTextService {
            inner: bmlib::fulltext::FullTextService::new(transport, email),
            client,
        })
    }

    /// GET `url` through the app's transport and return the status: Rust →
    /// foreign transport → Rust. The foreign error comes back as it was thrown,
    /// variant and reason intact.
    pub fn fetch_status(&self, url: String) -> Result<u16, ForeignHttpError> {
        self.client.get(url).map(|r| r.status)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bmlib::publications::fetchers::registry::HttpClient as _;

    /// A transport answering from a fixed script.
    struct Scripted(Result<ForeignHttpResponse, fn() -> ForeignHttpError>);

    impl ForeignHttpClient for Scripted {
        fn get(&self, _url: String) -> Result<ForeignHttpResponse, ForeignHttpError> {
            self.0.clone().map_err(|make| make())
        }
        fn post_json(
            &self,
            _url: String,
            _body_json: String,
            _headers: HashMap<String, String>,
        ) -> Result<ForeignHttpResponse, ForeignHttpError> {
            self.0.clone().map_err(|make| make())
        }
    }

    fn adapter(script: Result<ForeignHttpResponse, fn() -> ForeignHttpError>) -> ForeignTransport {
        ForeignTransport(Arc::new(Scripted(script)))
    }

    #[test]
    fn a_body_that_is_not_utf8_passes_through_byte_for_byte() {
        let body = vec![0x25, 0x50, 0x44, 0x46, 0xff, 0xfe, 0x00];
        let response = adapter(Ok(ForeignHttpResponse {
            status: 200,
            body: body.clone(),
        }))
        .get("https://x")
        .expect("a response");
        assert_eq!(response.status, 200);
        assert_eq!(response.body, body);
    }

    #[test]
    fn a_non_2xx_status_is_a_response_not_an_error() {
        let response = adapter(Ok(ForeignHttpResponse {
            status: 404,
            body: vec![],
        }))
        .get("https://x")
        .expect("a 404 is an answer");
        assert_eq!(response.status, 404);
    }

    #[test]
    fn an_app_defect_says_so_in_what_reaches_bmlib() {
        let error = adapter(Err(|| ForeignHttpError::Unexpected {
            reason: "NullPointerException".into(),
        }))
        .get("https://x")
        .expect_err("an error");
        let text = error.to_string();
        assert!(
            text.contains("unexpected error: NullPointerException"),
            "{text}"
        );
    }

    #[test]
    fn a_transport_failure_is_reported_once_not_wrapped_twice() {
        let error = adapter(Err(|| ForeignHttpError::Transport {
            reason: "timed out".into(),
        }))
        .get("https://x")
        .expect_err("an error");
        assert_eq!(error.to_string(), "transport failed: timed out");
    }

    #[test]
    fn fetch_status_keeps_the_foreign_errors_variant() {
        let service = BmlibFullTextService::new(
            Arc::new(Scripted(Err(|| ForeignHttpError::Unexpected {
                reason: "bug".into(),
            }))),
            "a@b.c".into(),
        );
        match service.fetch_status("https://x".into()) {
            Err(ForeignHttpError::Unexpected { reason }) => assert_eq!(reason, "bug"),
            other => panic!("{other:?}"),
        }
    }
}
