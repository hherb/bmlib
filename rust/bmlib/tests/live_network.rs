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

//! Live requests against the real registries.
//!
//! **The rest of the suite makes no network request.** Every fetcher, the LLM
//! transport and the transparency analyzer are driven by scripted clients, which
//! is what makes them testable — but it also means nothing has ever confirmed that
//! a real registry's *response shape* is the one the parsers expect. A scripted
//! fixture is a claim about the remote written by the person who wrote the parser,
//! so it cannot catch a parser that agrees only with its own fixtures. This file is
//! the counterweight.
//!
//! # Running it
//!
//! Gated on `BMLIB_LIVE_TESTS`, so the default `cargo test` never opens a socket:
//!
//! ```text
//! BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1
//! ```
//!
//! `--test-threads=1` is not optional in spirit. **NCBI rate-limits by source
//! address** (3 requests/second, 10 with a key), and the tests share one process
//! and therefore one egress address; running them concurrently draws 429s that
//! read as parse failures. The tests also take a process-wide lock so that even a
//! parallel run serialises its requests rather than relying on the operator to
//! remember.
//!
//! # What these assert
//!
//! **Shape, not content.** A test that pinned a DOI or a count would fail the first
//! time a registry revised its data, which teaches nothing about the port. So these
//! assert the invariants the parsers depend on: a count parses and is positive, a
//! record carries a title where one is required, a cursor advances, a truncated
//! body is refused. A registry changing its *shape* is exactly what they should
//! catch.
//!
//! # What they cannot do
//!
//! They are not hermetic and cannot be a blocking gate: a registry outage, an
//! egress block or a rate-limit would redden CI for a reason that is not this
//! code. Treat them as an instrument you run deliberately — before a release, when
//! a parser's fixture is revised, or when a fetcher's transport changes — and read
//! a failure as *something changed at the far end*, which is the question.
//!
//! No API key is required: every endpoint here is open, and `NCBI_API_KEY` is used
//! only if the environment already provides one, for the higher rate limit.

#![allow(clippy::expect_used)]

use bmlib::http::UreqClient;
use bmlib::publications::fetchers::openalex::{CursorPages, HttpCursorPages};
use bmlib::publications::fetchers::pubmed::{Eutils, HttpEutils};
use bmlib::publications::fetchers::HttpClient;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

/// The process-wide request lock, so a parallel run still serialises its calls.
fn request_lock() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Whether the live tests are enabled. Absent means **skip**, and the skip says so.
fn enabled() -> bool {
    match std::env::var("BMLIB_LIVE_TESTS") {
        Ok(value) if !value.is_empty() && value != "0" => true,
        _ => {
            eprintln!(
                "SKIPPING a live test: set BMLIB_LIVE_TESTS=1 to make real requests \
                 (and use --test-threads=1; NCBI rate-limits by source address)"
            );
            false
        }
    }
}

/// A real client, under the shared lock.
fn client() -> (MutexGuard<'static, ()>, Arc<dyn HttpClient + Send + Sync>) {
    let guard = request_lock();
    (guard, Arc::new(UreqClient::new()))
}

/// The NCBI key, when the environment already has one.
fn api_key() -> Option<String> {
    std::env::var("NCBI_API_KEY").ok().filter(|k| !k.is_empty())
}

// ---------------------------------------------------------------------------
// The transport itself
// ---------------------------------------------------------------------------

/// The real client reaches a real host, and a **404 is a response and not an
/// error** — the contract every tier chain rests on. A registry that answers
/// "asked and not served" differently from "nobody answered" is what makes
/// `NOT_SERVED` distinguishable from `REQUEST_FAILED`.
#[test]
fn the_real_client_gets_a_body_and_a_status() {
    if !enabled() {
        return;
    }
    let (_guard, client) = client();

    let ok = client
        .get("https://api.openalex.org/works?per-page=1")
        .expect("OpenAlex answers");
    assert_eq!(ok.status, 200, "OpenAlex is open and needs no key");
    assert!(!ok.body.is_empty());
    let text = ok.text().expect("a JSON body is UTF-8");
    assert!(
        text.contains("\"meta\""),
        "an OpenAlex envelope: {text:.200}"
    );
    assert!(text.contains("\"results\""));

    // A path that cannot exist is a **response**, which is the whole point: a
    // walker distinguishes "not served" from "not reached".
    let missing = client
        .get("https://api.openalex.org/works/this-is-not-an-id")
        .expect("a response, not a transport failure");
    assert!(
        (400..500).contains(&missing.status),
        "expected a 4xx, got {}",
        missing.status
    );
}

/// An unreachable host is a **transport failure**, the other half of the contract.
#[test]
fn an_unreachable_host_is_a_transport_error() {
    if !enabled() {
        return;
    }
    let (_guard, client) = client();
    // RFC 5737 reserves this range for documentation, so nothing answers it.
    let error = client
        .get("https://192.0.2.1/")
        .expect_err("nothing is listening on a documentation address");
    assert!(!error.to_string().is_empty(), "the error names its cause");
}

// ---------------------------------------------------------------------------
// OpenAlex
// ---------------------------------------------------------------------------

/// A real OpenAlex cursor page parses, and the **cursor advances** — the property
/// the walker depends on and the one a fixture can never establish, since a
/// fixture carries the cursor its author chose.
#[test]
fn a_real_openalex_page_parses_and_the_cursor_advances() {
    if !enabled() {
        return;
    }
    let (_guard, client) = client();
    let pages = HttpCursorPages { client };
    let email = "bmlib-port@example.org";

    let first = pages
        .page("2024-01-15", "*", email, None)
        .expect("OpenAlex answers a date-filtered page");
    let first_cursor = first["meta"]["next_cursor"]
        .as_str()
        .expect("a next_cursor")
        .to_string();
    assert_ne!(first_cursor, "*", "the cursor advanced past the first page");
    let results = first["results"].as_array().expect("a results array");
    assert!(!results.is_empty(), "a 2024 date has works");
    // The fields the record reader takes are present and typed as it expects.
    let work = &results[0];
    assert!(work.get("id").is_some(), "a work carries an id");
    assert!(
        work.get("display_name").is_some() || work.get("title").is_some(),
        "a work carries a title: {work:.200}"
    );

    let second = pages
        .page("2024-01-15", &first_cursor, email, None)
        .expect("the second page answers");
    let second_cursor = second["meta"]["next_cursor"].as_str().unwrap_or("*");
    assert_ne!(
        second_cursor, first_cursor,
        "the second page's cursor differs from the first's"
    );
}

// ---------------------------------------------------------------------------
// bioRxiv
// ---------------------------------------------------------------------------

/// **bioRxiv's `/details/` endpoint serves an empty body, and that is what this
/// asserts.**
///
/// Measured 2026-09-26: `GET /details/biorxiv/{date}/{date}/0` returns **HTTP 200
/// with zero bytes** for every date tried — 2024-01-15, 2024-01-16, 2023-06-01,
/// both servers — while `/pubs/` on the same host serves the same days normally
/// (34 records for 2024-01-15). Python's own `httpx` gets the identical empty body,
/// so this is **not** a difference between the two ports: `biorxiv.py` and
/// `biorxiv.rs` share `https://api.biorxiv.org/details`, and both now read a
/// silent zero-row day.
///
/// The port matches the Python here, deliberately — the brief is equivalence, and
/// changing the URL is a correction outside the enumerated list. So this test
/// **pins the observed behaviour** rather than asserting a successful fetch, and
/// fails loudly if bioRxiv starts serving data again, which is the signal that the
/// endpoint question needs revisiting. Filed as an issue.
#[test]
fn the_biorxiv_details_endpoint_currently_serves_nothing() {
    if !enabled() {
        return;
    }
    let (_guard, client) = client();

    let details = client
        .get("https://api.biorxiv.org/details/biorxiv/2024-01-15/2024-01-15/0")
        .expect("bioRxiv answers");
    assert_eq!(details.status, 200, "it answers 200, not a 404");
    assert!(
        details.body.is_empty(),
        "if this is no longer empty, bioRxiv has restored /details and the \
         fetcher's URL should be revisited: {} bytes",
        details.body.len()
    );

    // The host is alive and serving the same day under a different path, which is
    // what makes the empty body an endpoint change rather than an outage.
    let pubs = client
        .get("https://api.biorxiv.org/pubs/biorxiv/2024-01-15/2024-01-15/0")
        .expect("the host answers");
    assert_eq!(pubs.status, 200);
    let value: serde_json::Value = serde_json::from_slice(&pubs.body).expect("and it is JSON");
    let messages = value["messages"]
        .as_array()
        .expect("a messages array, which the reconciler reads");
    assert!(!messages.is_empty(), "a message per request");
    let collection = value["collection"].as_array().expect("a collection array");
    assert!(!collection.is_empty(), "the same day has records here");
    // **The field names differ from `/details/`**, which is the second half of the
    // change: `/details/` names them `doi`/`title`, `/pubs/` prefixes them
    // `preprint_`/`published_`. A reader switched between the two without renaming
    // its fields would find every value absent.
    let record = &collection[0];
    assert!(
        record.get("preprint_doi").is_some(),
        "a /pubs/ record carries preprint_doi: {record:.200}"
    );
    assert!(
        record.get("doi").is_none(),
        "and NOT the /details/ spelling, which is the trap"
    );
}

// ---------------------------------------------------------------------------
// PubMed E-utilities
// ---------------------------------------------------------------------------

/// A real ESearch parses and yields a usable history session, then a real EFetch
/// over it returns articles the record reader can read.
///
/// The two are one test because the **session is the thing being tested**: ESearch
/// returns a `WebEnv`/`QueryKey` pair, and EFetch is addressed by it. A test that
/// only ran ESearch would never prove the pair is the one EFetch accepts.
#[test]
fn a_real_pubmed_history_session_round_trips() {
    if !enabled() {
        return;
    }
    let (_guard, client) = client();
    let eutils = HttpEutils { client };
    let key = api_key();

    // A nonsense term is a **quiet** search, not an error: NCBI answers a
    // well-formed document with a count of zero, which is the case a fetcher must
    // not mistake for a rejection.
    //
    // Measured: the count is not asserted to be exactly zero. A first cut asserted
    // `== 0` for an invented phrase and got 1 — a real article's title carries the
    // words, which is a fact about PubMed's corpus and not about this code. The
    // property worth pinning is that a term matching nothing yields a *parsed*
    // zero rather than an unreadable body.
    let quiet = eutils
        .esearch("zzqqjjxx nonexistent phrase[Title]", key.as_deref(), true)
        .expect("NCBI answers a quiet search with a well-formed document");
    assert!(quiet.count >= 0, "a count parses: {}", quiet.count);

    // A term that does match, to prove the session and the fetch work.
    let search = eutils
        .esearch("biomedical[Title] AND 2024[dp]", key.as_deref(), true)
        .expect("NCBI answers a productive ESearch");
    assert!(search.count > 0, "the term matches published articles");
    let web_env = search.web_env.clone().expect("a history session");
    let query_key = search.query_key.clone().expect("a query key");

    let page = eutils
        .efetch(&web_env, &query_key, 0, key.as_deref())
        .expect("EFetch accepts the session ESearch returned");
    assert!(!page.articles.is_empty(), "the page carries articles");
    let article = &page.articles[0];
    assert!(
        article.pmid.is_some(),
        "an article carries a PMID, which is its identity: {article:?}"
    );
    // The delivery is reconciled against the count the page reports.
    assert!(page.delivered > 0, "the page reports what it delivered");
}

/// NCBI answers a **bad request with HTTP 200 and an `<ERROR>` document that has
/// no `<Count>`**, so a reader that treated an absent element as zero would report
/// a rejected search as a day with no publications. This is that shape, live.
///
/// **The trigger is an empty term, which was measured rather than guessed.** A
/// first cut of this test used a malformed field tag (`"cancer["`) on the theory
/// that NCBI refuses it; it does not. Probed 2026-09-26: `cancer[` serves
/// **5,709,791** results and `zzz[NotAField]` serves 404 — a stray bracket is
/// simply ignored, and an unknown tag matches free text. Only the empty term
/// produces `<ERROR>` with no `<Count>` (5 of 6 probes), so that is what is used,
/// and the assertion is that the refusal is an `Err` rather than a quiet day.
#[test]
fn a_rejected_pubmed_search_is_an_error_not_a_quiet_day() {
    if !enabled() {
        return;
    }
    let (_guard, client) = client();
    let eutils = HttpEutils { client };
    let error = eutils
        .esearch("", None, true)
        .expect_err("an empty term is refused rather than read as an empty day");
    assert!(!error.is_empty(), "the refusal names its cause: {error}");
    // And the message says what went wrong rather than reporting a parse artefact.
    assert!(
        !error.contains("XML with DTD"),
        "the DTD is handled, so no refusal should mention it: {error}"
    );
}
