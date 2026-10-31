# bmlib::http — The HTTP Client

Every module that goes out to the network — the publication fetchers, the
record lookup, the LLM client, the transparency analyzer, the full-text
service — is written against one trait, `HttpClient`: the fetchers, the
lookup and the analyzer take `&dyn HttpClient`, and `LlmClient::new` and
`FullTextService::new`, which keep it, `Arc<dyn HttpClient + Send + Sync>`.
`UreqClient` is the one implementation that makes real
requests. Python has no page for this: its modules build an `httpx` client
internally, and its tests patch it. In Rust the client is always an argument,
so a test or a host app passes its own.

| Need | Feature |
|---|---|
| `UreqClient`, `default_user_agent`, the timeout constants | `http-ureq` (default) |
| `HttpClient`, `HttpResponse`, `HttpStream`, `FetchError` | none |

The trait and its types live in `bmlib::publications::fetchers`, not in
`bmlib::http`, so a build without `http-ureq` still has them.

## The real client

```rust
use bmlib::http::{UreqClient, DEFAULT_STREAM_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS};

let mut client = UreqClient::new();
assert!(client.user_agent.starts_with("bmlib/"));
assert_eq!(client.timeout_seconds, DEFAULT_TIMEOUT_SECONDS);        // 45
assert_eq!(client.stream_timeout_seconds, DEFAULT_STREAM_TIMEOUT_SECONDS); // 600

// Several APIs ask a caller to name itself; the default names bmlib and no contact.
client.user_agent = "myapp/1.0 (mailto:me@example.org)".to_string();
```

`timeout_seconds` bounds a whole request. A streamed POST
(`post_json_stream`, used for LLM streaming) gets `timeout_seconds` for each
step of sending, then `stream_timeout_seconds` for the response headers and
again for the body. `UreqClient` implements all four trait methods.

**A non-success status is a response, not an error.** `get` returns
`Ok(HttpResponse)` for a 404 or a 429, with its body; `Err(FetchError::Transport)`
means the request never completed. The body is raw bytes: `text()` decodes it
as strict UTF-8 (an `Err` otherwise), `text_or_empty()` gives `""` instead,
`json(url)` parses it, and `is_success()` tests for 2xx.

## Injecting your own client

A stub needs only `get`; the other three methods have defaults that refuse,
naming the method, rather than sending something else. Here a stub answers
every request with a 404 and records what was asked, and `lookup_record` runs
against it offline:

```rust
use std::cell::RefCell;
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::publications::lookup::{lookup_record, LookupRequest};

#[derive(Default)]
struct NotFound {
    asked: RefCell<Vec<String>>,
}

impl HttpClient for NotFound {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.asked.borrow_mut().push(url.to_string());
        Ok(HttpResponse::from_bytes(404, Vec::new()))
    }
}

let http = NotFound::default();
let request = LookupRequest {
    doi: Some("10.1000/example"),
    email: "me@example.org",
    ..LookupRequest::default()
};
let found = lookup_record(&http, &request)?;
assert!(found.record.is_none());
assert!(http.asked.borrow().iter().any(|url| url.contains("openalex")));
# Ok::<(), bmlib::publications::lookup::LookupError>(())
```

`HttpResponse::ok(bytes)` and `HttpResponse::ok_text(text)` build a 200. A host
app with its own HTTP stack (a proxy, retries, a shared connection pool)
implements the trait the same way and implements `get_with_headers`,
`post_json` and `post_json_stream` as well if it serves the LLM client.
