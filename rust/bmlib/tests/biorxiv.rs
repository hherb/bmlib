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

//! bioRxiv/medRxiv walker — the oracle and the named tests.
//!
//! The corpus (65 cases) diffs normalisation and the whole page walk against
//! Python, including the shapes that must be **refused** rather than read as a
//! quiet day. The named tests state why each refusal exists, which is what a
//! corpus of messages cannot say.
//!
//! **Two cases are `corrected` blocks** (#349): a non-2xx now actually reaches
//! the walk, and the message the port writes for it is its own wording where
//! Python's is httpx's. `the_corrected_cases_are_the_ones_the_register_names`
//! pins that list, and the oracle test asserts both directions — Python still
//! says what the corpus records, and Rust produces the corrected value.

use bmlib::publications::fetchers::biorxiv::{
    fetch_biorxiv, normalize, page_url, pdf_url, read_page_body, HttpPageSource, PageSource,
    PAGE_SIZE,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse, Progress};
use chrono::NaiveDate;
use serde_json::{json, Value};

mod common;

const CASES: &str = include_str!("data/biorxiv_cases.json");
const EXPECTED: &str = include_str!("data/biorxiv_expected.json");

// The corpus's response vocabulary — a body, a status, a request that never
// arrived — lives in one place, read by every harness that scripts a transport.
// `Scripted` is the local name it has always had here; the shared module is
// `tests/common/oracle.rs`, and `rust/oracle/_oracle.py` is the Python half of
// the same contract.
use common::oracle::Response as Scripted;

/// A page source that serves a fixed sequence of pages, recording each URL.
struct ScriptedPages {
    payloads: std::cell::RefCell<Vec<Scripted>>,
    urls: std::cell::RefCell<Vec<String>>,
}

impl PageSource for ScriptedPages {
    fn page(&self, server: &str, date: &str, cursor: usize) -> Result<Value, FetchError> {
        let url = page_url(server, date, cursor);
        self.urls.borrow_mut().push(url.clone());
        let mut payloads = self.payloads.borrow_mut();
        if payloads.is_empty() {
            return Ok(json!({"collection": [], "messages": []}));
        }
        match payloads.remove(0) {
            Scripted::Body(value) => Ok(value),
            // The failures are built here rather than by the corpus because
            // **the URL belongs to the source**: the corpus names what went
            // wrong, and the URL this page actually asked for is filled in —
            // which is what the real `HttpPageSource` does.
            Scripted::Status(status) => Err(FetchError::HttpStatus { url, status }),
            Scripted::Transport(message) => Err(FetchError::Transport(message)),
            Scripted::Error(error) => Err(error),
        }
    }
}

fn run_scripted(pages: Vec<Scripted>, server: &str, day: &str) -> Value {
    let source = ScriptedPages {
        payloads: std::cell::RefCell::new(pages),
        urls: std::cell::RefCell::new(Vec::new()),
    };
    let date = NaiveDate::parse_from_str(day, "%Y-%m-%d").expect("date");
    let mut progress: Vec<Value> = Vec::new();
    // The corpus diffs Python's `fetch_biorxiv`, which reports the running
    // delivered count as the total when the source named none.
    let mut last_delivered = 0i64;
    let mut observe = |p: Progress| {
        if let Progress::Page { delivered, .. } = p {
            last_delivered = delivered;
        }
        if let Progress::Page {
            delivered,
            promised,
        } = p
        {
            progress.push(json!([
                delivered,
                promised.unwrap_or(last_delivered),
                "in_progress"
            ]));
        }
    };
    let outcome = fetch_biorxiv(&source, server, date, &mut observe);

    json!({
        "status": outcome.status,
        "error": outcome.error,
        "note": outcome.note,
        "record_count": outcome.records.len(),
        "urls": source.urls.borrow().clone(),
        "records": outcome.records.iter().map(|r| r.title.clone()).collect::<Vec<_>>(),
        "progress": progress,
    })
}

/// The named tests' entry point: pages given as bodies, or as an injected
/// failure.
fn run_walk(payloads: Vec<Result<Value, FetchError>>, server: &str, day: &str) -> Value {
    run_scripted(
        payloads.into_iter().map(Scripted::from).collect(),
        server,
        day,
    )
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "normalize" => {
            let server = args
                .get("server")
                .and_then(Value::as_str)
                .unwrap_or("biorxiv");
            let record = normalize(&args["raw"], server);
            json!({
                "title": record.title,
                "source": record.source,
                "doi": record.doi,
                "abstract": record.abstract_text,
                "authors": record.authors,
                "publication_date": record.publication_date,
                "is_open_access": record.is_open_access,
                "fulltext_sources": record.fulltext_sources,
                "extras": record.extras,
            })
        }
        "fetch" => {
            let server = args
                .get("server")
                .and_then(Value::as_str)
                .unwrap_or("biorxiv");
            let day = args
                .get("day")
                .and_then(Value::as_str)
                .unwrap_or("2024-06-10");
            let pages: Vec<Scripted> = args["payloads"]
                .as_array()
                .map(|a| a.iter().map(common::oracle::response).collect())
                .unwrap_or_default();
            run_scripted(pages, server, day)
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    let mut corrected_seen: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        // A case may carry the value the port is *required* to produce where it
        // deliberately differs from Python (§9). Three things are asserted: the
        // committed expectation is Python's answer, Rust produces the corrected
        // value, and the two genuinely differ — so a correction Python has since
        // adopted is reported rather than silently passing.
        let expected_value: Value = match case.get("corrected") {
            Some(corrected) => {
                let corrected_value = corrected
                    .get("value")
                    .unwrap_or_else(|| panic!("{name}: corrected block carries no `value`"));
                assert_ne!(
                    &want["value"], corrected_value,
                    "  {name}: marked corrected but Python already returns the corrected \
                     value — the correction is stale, remove it"
                );
                corrected_seen.push(name.to_string());
                corrected_value.clone()
            }
            None => want["value"].clone(),
        };
        if got != expected_value {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&expected_value).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
    let declared = cases
        .iter()
        .filter(|case| case.get("corrected").is_some())
        .count();
    assert_eq!(
        corrected_seen.len(),
        declared,
        "a corrected case did not reach the comparison"
    );
}

/// **The corrected blocks are the §9 divergences, and they stay named.**
///
/// A corpus edit cannot quietly attach a block to another input, drop one, or
/// strip its reason: each case's issue is compared against the table below as a
/// whole, and the presence of a `why` is asserted. Every one of them is also
/// asserted against Python inside `the_port_agrees_with_python_on_every_case`.
///
/// **And each is asserted to have reached the rule it is named for**, on the
/// Python side too. That is not the same check as "Python differs from the
/// corrected value": a dumper that stopped honouring a marker would serve a list
/// payload, Python would answer `ValueError: … a list payload …`, and that still
/// differs from the corrected value — so the case would stay green while testing
/// the refusal it used to duplicate. The assertion is on the committed
/// expectation, which is why regenerating it is what makes this fire.
#[test]
fn the_corrected_cases_are_the_ones_the_register_names() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");

    // Case, the issue that owns its divergence, and the prefix Python's own
    // committed error must carry — the last is what proves the case reached the
    // rule it is named for rather than a refusal it duplicates.
    let owned: [(&str, i64, &str); 3] = [
        ("fetch/http-error", 349, "HTTPStatusError: "),
        (
            "fetch/http-error-keeps-the-records",
            349,
            "HTTPStatusError: ",
        ),
        ("fetch/transport-error", 361, "ConnectError: "),
    ];

    let corrected: Vec<&Value> = cases
        .as_array()
        .expect("cases is a list")
        .iter()
        .filter(|case| case.get("corrected").is_some())
        .collect();
    let found: Vec<(&str, i64)> = corrected
        .iter()
        .map(|case| {
            (
                case["name"].as_str().unwrap_or_default(),
                case["corrected"]["issue"].as_i64().unwrap_or_default(),
            )
        })
        .collect();
    assert_eq!(
        found,
        owned
            .iter()
            .map(|(name, issue, _)| (*name, *issue))
            .collect::<Vec<_>>(),
        "the corrected cases, and the issue each cites"
    );

    for case in &corrected {
        let name = case["name"].as_str().unwrap_or_default();
        let (_, _, python_error) = owned
            .iter()
            .find(|(owned_name, _, _)| *owned_name == name)
            .unwrap_or_else(|| panic!("{name}: no entry in the table above"));
        assert!(
            case["corrected"]["why"]
                .as_str()
                .is_some_and(|why| !why.is_empty()),
            "{name}: a correction without a reason is a tolerance"
        );

        let python = &expected
            .as_array()
            .expect("expected is a list")
            .iter()
            .find(|want| want["name"] == case["name"])
            .unwrap_or_else(|| panic!("{name}: no expectation"))["value"];
        let error = python["error"].as_str().unwrap_or_default();
        assert!(
            error.starts_with(python_error),
            "{name}: Python must reach {python_error:?}, not a payload refusal: {python}"
        );
        if name.ends_with("keeps-the-records") {
            assert!(
                python["record_count"].as_i64().unwrap_or(0) > 0,
                "{name}: the first page must have delivered records before the failure: {python}"
            );
        }
    }
}

// ---------------------------------------------------------------------------
// The five refusals
// ---------------------------------------------------------------------------

/// A **non-object payload** read through `.get(..., [])` is indistinguishable
/// from a day with no preprints — and a day stored `completed` is never offered
/// again (#88).
#[test]
fn a_non_object_payload_is_refused() {
    for payload in [json!([1, 2, 3]), json!("text"), json!(5), json!(null)] {
        let err = read_page_body(&payload, "biorxiv", "2024-06-10").expect_err("refused");
        assert!(
            err.to_string().contains("not an object"),
            "{payload} gave {err}"
        );
    }
}

/// A body carrying **neither `collection` nor `messages`** makes no claim about
/// the day, so it cannot be accepted. The test is "carries no evidence either
/// way" and not "carries a collection": bioRxiv's quiet day is *known* to omit
/// `total`, and requiring `collection` on a quiet day would fail that day on
/// every run for the life of the installation — the runaway-retry cost the
/// reconciliation rules exist to avoid.
#[test]
fn a_body_making_no_claim_is_refused() {
    let err = read_page_body(&json!({}), "biorxiv", "2024-06-10").expect_err("refused");
    assert!(
        err.to_string()
            .contains("neither a collection nor messages"),
        "{err}"
    );

    // The `collection` **key** alone is a claim, even empty — that is the
    // quiet-day shape DECISIONS.md records.
    assert!(read_page_body(&json!({"collection": []}), "biorxiv", "2024-06-10").is_ok());
    // An empty `messages` list is *not*: Python tests the list's truthiness,
    // not whether the key is present, so `{"messages": []}` carries nothing.
    assert!(read_page_body(&json!({"messages": []}), "biorxiv", "2024-06-10").is_err());
    // A populated `messages` list is a claim.
    assert!(read_page_body(
        &json!({"messages": [{"total": 0}]}),
        "biorxiv",
        "2024-06-10"
    )
    .is_ok());
    // And an unrelated key is not a claim at all.
    assert!(read_page_body(&json!({"status": "ok"}), "biorxiv", "2024-06-10").is_err());
}

/// A **`collection` that is not a list** is refused rather than iterated.
#[test]
fn a_non_list_collection_is_refused() {
    let err = read_page_body(
        &json!({"collection": {"a": 1}, "messages": [{"total": 1}]}),
        "biorxiv",
        "2024-06-10",
    )
    .expect_err("refused");
    assert!(err.to_string().contains("not a list"), "{err}");
}

/// A **non-numeric `total`** is refused *by name*: the day retries on every run
/// until the cause is fixed, and a bare parse error says neither which source
/// nor which field is at fault.
#[test]
fn a_non_numeric_total_is_named() {
    let err = read_page_body(
        &json!({"collection": [{"title": "R"}], "messages": [{"total": "lots"}]}),
        "biorxiv",
        "2024-06-10",
    )
    .expect_err("refused");
    let message = err.to_string();
    assert!(message.contains("non-numeric total"), "{message}");
    assert!(
        message.contains("'lots'"),
        "the offending value must be quoted: {message}"
    );
    assert!(
        message.contains("2024-06-10"),
        "the day must be named: {message}"
    );
}

/// An **absent `total`** stays `None` rather than becoming zero. Flattening the
/// two makes "the source said this day is empty" and "the source said nothing"
/// identical, and the second silently switches off both reconciliation rules.
#[test]
fn an_absent_total_stays_none() {
    for messages in [json!([]), json!([{}]), json!([{"total": null}])] {
        let body = read_page_body(
            &json!({"collection": [{"title": "R"}], "messages": messages}),
            "biorxiv",
            "2024-06-10",
        )
        .expect("accepted");
        assert_eq!(body.total, None, "{messages} must not become a total");
    }
    // A numeric string is read, and zero really is zero.
    let body = read_page_body(
        &json!({"collection": [], "messages": [{"total": "0"}]}),
        "biorxiv",
        "2024-06-10",
    )
    .expect("accepted");
    assert_eq!(body.total, Some(0));
}

// ---------------------------------------------------------------------------
// The walk
// ---------------------------------------------------------------------------

/// A page of `count` well-formed records.
///
/// Every record carries a DOI because the walk **refuses a day whose record has
/// none** — see `a_record_without_a_doi_fails_the_day` — so a fixture without one
/// is no longer an example of a normal record.
fn full_page(count: usize) -> Vec<Value> {
    (0..count)
        .map(|i| json!({"title": format!("R{i}"), "preprint_doi": format!("10.1101/{i}")}))
        .collect()
}

/// A **short page ends the walk**, so a stall needs a *full* first page. This
/// is the shape an expiring history session leaves on the last page of a long
/// walk, and the rule catches it **however small the gap** — here, one record.
#[test]
fn a_stall_after_a_full_page_fails_however_small_the_gap() {
    for total in [200, 101] {
        let pages = vec![
            Ok(json!({"collection": full_page(100), "messages": [{"total": total}]})),
            Ok(json!({"collection": [], "messages": [{"total": total}]})),
        ];
        let result = run_walk(pages, "biorxiv", "2024-06-10");
        assert_eq!(result["status"], "failed", "total {total}");
        assert!(
            result["error"]
                .as_str()
                .unwrap_or_default()
                .contains("empty page"),
            "{result}"
        );
    }
}

/// **A record with no DOI fails the day**, naming the day, the source and both
/// spellings.
///
/// `/pubs` renamed the record's `doi` to `preprint_doi`, so a reader that was
/// only re-pointed finds **every** DOI absent. A stored record has no identity to
/// deduplicate on, so each revisit of the day would insert it again. Failing the
/// day is loud and retried; storing it is neither.
///
/// Raised **before** the bad record is kept, so it is not counted; the records
/// that preceded it are, which
/// `an_error_keeps_the_records_that_arrived_before_it` pins.
#[test]
fn a_record_without_a_doi_fails_the_day() {
    let pages = vec![Ok(
        json!({"collection": [{"title": "no doi here"}], "messages": [{"total": 1}]}),
    )];
    let result = run_walk(pages, "biorxiv", "2024-06-10");

    assert_eq!(result["status"], "failed", "{result}");
    let error = result["error"].as_str().unwrap_or_default();
    for needle in [
        "biorxiv",
        "2024-06-10",
        "carrying no DOI",
        "preprint_doi, doi",
    ] {
        assert!(error.contains(needle), "{needle:?} missing from {error:?}");
    }
    assert_eq!(
        result["record_count"], 0,
        "the bad record is refused before it is kept: {result}"
    );
}

/// **An error keeps the records that arrived before it**, as Python has already
/// handed them to `on_record` and counts them in `record_count`.
///
/// The port used to discard its whole buffer on any `Err`, so a day failing on
/// page 2 reported — and `sync()` stored — nothing from page 1: 0 where Python
/// says 100. Both a refused record mid-page and a transport failure on a later
/// page are checked, the second being a shape the JSON corpus cannot express.
#[test]
fn an_error_keeps_the_records_that_arrived_before_it() {
    let mid_page = vec![Ok(json!({
        "collection": [
            {"preprint_doi": "10.1101/a", "title": "A"},
            {"title": "no doi"},
            {"preprint_doi": "10.1101/c", "title": "C"},
        ],
        "messages": [{"total": 3}],
    }))];
    let result = run_walk(mid_page, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed", "{result}");
    assert_eq!(result["records"], json!(["A"]), "{result}");
    assert_eq!(result["record_count"], 1, "{result}");

    let later_page = vec![
        Ok(json!({"collection": full_page(PAGE_SIZE), "messages": [{"total": 150}]})),
        Err(FetchError::Transport("connection reset".to_string())),
    ];
    let result = run_walk(later_page, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed", "{result}");
    assert_eq!(result["record_count"], PAGE_SIZE, "{result}");
    assert!(
        result["error"]
            .as_str()
            .is_some_and(|e| e.starts_with("TransportError: ")),
        "{result}"
    );
}

/// **A non-success status is Python's `HTTPStatusError`, not a transport
/// fault.** Python's `response.raise_for_status()` raises
/// `httpx.HTTPStatusError` for a 4xx/5xx and a `httpx.TransportError` subclass
/// when no request arrived; the walker catches both and stores
/// `f"{type(exc).__name__}: {exc}"`. One Rust variant for both reported a 500 as
/// a `RemoteProtocolError` — a protocol violation, which is not what the source
/// did — and no corpus case could see it, because `fetch/http-error` served a
/// *list payload* on both sides and never made a request that carried a status
/// (#349).
#[test]
fn a_non_success_status_is_a_status_error_not_a_transport_error() {
    let result = run_scripted(vec![Scripted::Status(500)], "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed", "{result}");
    assert_eq!(result["record_count"], 0, "{result}");
    assert_eq!(
        result["error"],
        json!(format!(
            "HTTPStatusError: {} returned HTTP 500",
            page_url("biorxiv", "2024-06-10", 0)
        )),
        "{result}"
    );
    // The status is the *source's answer*, so the message names the URL it
    // answered — which is the one the walker asked for.
    assert_eq!(
        result["urls"],
        json!([page_url("biorxiv", "2024-06-10", 0)]),
        "{result}"
    );

    // A client error and a server error are the same refusal here; 429 and 503
    // are what a rate limit and an outage produce, and neither is a quiet day.
    for status in [403u16, 404, 429, 503] {
        let result = run_scripted(vec![Scripted::Status(status)], "biorxiv", "2024-06-10");
        let error = result["error"].as_str().unwrap_or_default();
        assert!(
            error.starts_with("HTTPStatusError: "),
            "{status} gave {result}"
        );
        assert!(
            error.ends_with(&format!("returned HTTP {status}")),
            "{status} gave {result}"
        );
    }
}

/// A status failure **keeps the records the walk already delivered**, for the
/// reason a transport failure does: they were handed to the caller and will be
/// stored, and the day is retried whatever this run concluded. The first page
/// is full, so the walk really did ask for a second one.
#[test]
fn a_status_failure_keeps_the_records_already_delivered() {
    let pages = vec![
        Scripted::Body(json!({
            "collection": full_page(PAGE_SIZE),
            "messages": [{"total": 150}]
        })),
        Scripted::Status(500),
    ];
    let result = run_scripted(pages, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed", "{result}");
    assert_eq!(result["record_count"], PAGE_SIZE, "{result}");
    assert_eq!(result["progress"], json!([[PAGE_SIZE, 150, "in_progress"]]));
    let error = result["error"].as_str().unwrap_or_default();
    assert!(
        error.ends_with("returned HTTP 500"),
        "the status must reach the message: {error}"
    );
    assert_eq!(
        result["urls"],
        json!([
            page_url("biorxiv", "2024-06-10", 0),
            page_url("biorxiv", "2024-06-10", PAGE_SIZE)
        ]),
        "the failure is on the second page, not the first"
    );
}

/// A transport that answers with one fixed status, so the **real**
/// [`HttpPageSource`] is what the assertions below are about.
///
/// The scripted `PageSource` above stands in for the walker's page seam and
/// builds the same `HttpStatus` the library does — which is exactly why this
/// test exists: a corpus case cannot see a change in `HttpPageSource` itself,
/// and before this nothing but the gated live suite touched it.
struct OneStatus {
    status: u16,
    asked: std::sync::Mutex<Vec<String>>,
}

impl HttpClient for OneStatus {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        // A `Mutex` rather than a `RefCell` because [`HttpClient`] is required to
        // be `Send + Sync`; nothing here contends for it.
        self.asked
            .lock()
            .expect("not poisoned")
            .push(url.to_string());
        Ok(HttpResponse::from_bytes(self.status, b"{}".to_vec()))
    }
}

/// The **real** page source refuses a non-success status as a status error, and
/// names the URL it asked for — the property the corpus's marker is a model of.
#[test]
fn the_real_page_source_refuses_a_status_as_a_status_error() {
    let client = std::sync::Arc::new(OneStatus {
        status: 429,
        asked: std::sync::Mutex::new(Vec::new()),
    });
    let source = HttpPageSource {
        client: client.clone(),
    };
    let asked = page_url("biorxiv", "2024-06-10", 0);
    let error = source
        .page("biorxiv", "2024-06-10", 0)
        .expect_err("a 429 is refused");
    assert_eq!(
        error,
        FetchError::HttpStatus {
            url: asked.clone(),
            status: 429
        }
    );
    // Scoped, because the guard would otherwise still be held when the walk
    // below asks the same client for another page.
    {
        let asked_urls = client.asked.lock().expect("not poisoned");
        assert_eq!(asked_urls.len(), 1);
        assert_eq!(asked_urls[0], asked);
    }

    // And the walk turns it into Python's name and message.
    let date = NaiveDate::from_ymd_opt(2024, 6, 10).expect("date");
    let outcome = fetch_biorxiv(&source, "biorxiv", date, &mut |_| {});
    assert_eq!(outcome.status, "failed");
    let expected = format!("HTTPStatusError: {asked} returned HTTP 429");
    assert_eq!(outcome.error.as_deref(), Some(expected.as_str()));
    assert!(outcome.records.is_empty());
}

/// The walk **stops at a short page** rather than asking for the next one, which
/// is why the stall fixture above must use a full page to reach the empty one.
#[test]
fn a_short_page_ends_the_walk() {
    let pages = vec![Ok(
        json!({"collection": full_page(2), "messages": [{"total": 2}]}),
    )];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "completed");
    assert_eq!(result["urls"].as_array().expect("urls").len(), 1);
}

/// A full page continues, and the cursor advances by the page size.
#[test]
fn a_full_page_advances_the_cursor() {
    let pages = vec![
        Ok(json!({"collection": full_page(100), "messages": [{"total": 103}]})),
        Ok(json!({"collection": full_page(3), "messages": [{"total": 103}]})),
    ];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "completed");
    assert_eq!(result["record_count"], 103);
    let urls = result["urls"].as_array().expect("urls");
    assert_eq!(urls.len(), 2);
    assert!(urls[0].as_str().unwrap_or_default().ends_with("/0"));
    assert!(urls[1]
        .as_str()
        .unwrap_or_default()
        .ends_with(&format!("/{PAGE_SIZE}")));
}

/// **Records delivered without a count cannot be verified**, so the day cannot
/// be claimed complete — an unverifiable success is the failure the
/// reconciliation exists to prevent.
#[test]
fn records_without_a_count_cannot_be_confirmed() {
    let pages = vec![Ok(json!({"collection": full_page(1)}))];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed");
    assert!(result["error"]
        .as_str()
        .unwrap_or_default()
        .contains("no count"));
}

/// A quiet day that **omits both keys** is refused rather than stored as
/// complete — the refusal that keeps a 200-with-an-error-body from looking like
/// an empty day.
#[test]
fn a_quiet_day_that_omits_both_keys_is_refused() {
    let pages = vec![Ok(json!({"messages": []}))];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed");
    assert!(result["error"]
        .as_str()
        .unwrap_or_default()
        .contains("neither a collection nor messages"));
}

/// A transport failure is a failure, and **the day is not silently completed**.
#[test]
fn a_transport_failure_fails_the_day() {
    let pages = vec![Err(FetchError::Transport("connection refused".to_string()))];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    assert_eq!(result["status"], "failed");
    let error = result["error"].as_str().unwrap_or_default();
    // The type name is what separates a bmlib defect from a bad response.
    assert!(error.starts_with("TransportError:"), "{error}");
    assert!(error.contains("connection refused"), "{error}");

    // A malformed body is a `ValueError`, and the two read identically without
    // the names.
    let pages = vec![Ok(json!([1, 2, 3]))];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    let error = result["error"].as_str().unwrap_or_default();
    assert!(error.starts_with("ValueError:"), "{error}");
    assert!(error.contains("not an object"), "{error}");
}

/// Progress is reported **after each page**, with the running total — it is
/// what a caller displays while a long walk runs.
#[test]
fn progress_is_reported_per_page() {
    let pages = vec![
        Ok(json!({"collection": full_page(100), "messages": [{"total": 103}]})),
        Ok(json!({"collection": full_page(3), "messages": [{"total": 103}]})),
    ];
    let result = run_walk(pages, "biorxiv", "2024-06-10");
    assert_eq!(
        result["progress"],
        json!([[100, 103, "in_progress"], [103, 103, "in_progress"]])
    );
}

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

/// The PDF URL uses the **record's own version**, not a hard-coded `v1`: a v2+
/// preprint's `v1` URL 404s or points at the wrong revision.
#[test]
fn the_pdf_url_uses_the_records_own_version() {
    assert_eq!(
        pdf_url("biorxiv", "10.1/x", Some(&json!("2"))),
        "https://www.biorxiv.org/content/10.1/xv2.full.pdf"
    );
    // An absent, null, empty or whitespace version means the first revision.
    for absent in [None, Some(json!(null)), Some(json!("")), Some(json!("   "))] {
        assert_eq!(
            pdf_url("biorxiv", "10.1/x", absent.as_ref()),
            "https://www.biorxiv.org/content/10.1/xv1.full.pdf",
            "{absent:?}"
        );
    }
    // The server is the record's, so medRxiv links point at medRxiv.
    assert_eq!(
        pdf_url("medrxiv", "10.1/x", Some(&json!("1"))),
        "https://www.medrxiv.org/content/10.1/xv1.full.pdf"
    );
}

/// **An absent optional becomes `None`, not `""`.** An empty string is not SQL
/// NULL, so the storage layer's `COALESCE` merge could never fill the field in
/// from another source later — the empty string would win for ever.
#[test]
fn an_absent_optional_is_none_not_an_empty_string() {
    let record = normalize(&json!({"title": "T"}), "biorxiv");
    assert_eq!(record.abstract_text, None);
    assert_eq!(record.publication_date, None);
    assert_eq!(record.doi, None);
    // But an explicit empty string is also absent, not stored as "".
    let record = normalize(
        &json!({"title": "T", "abstract": "", "date": ""}),
        "biorxiv",
    );
    assert_eq!(record.abstract_text, None);
    assert_eq!(record.publication_date, None);
}

/// Authors are semicolon-separated and blank parts are dropped, including a
/// trailing separator — the export's own spelling.
#[test]
fn authors_split_on_semicolons_and_drop_blanks() {
    let record = normalize(&json!({"title": "T", "authors": "A, X; B, Y;"}), "biorxiv");
    assert_eq!(record.authors, vec!["A, X", "B, Y"]);

    let record = normalize(
        &json!({"title": "T", "authors": "A, X;;   ;B, Y"}),
        "biorxiv",
    );
    assert_eq!(record.authors, vec!["A, X", "B, Y"]);

    for absent in [
        json!({"title": "T"}),
        json!({"title": "T", "authors": ""}),
        json!({"title": "T", "authors": null}),
    ] {
        assert!(normalize(&absent, "biorxiv").authors.is_empty(), "{absent}");
    }
}

/// A preprint is open access by definition, and both full-text locations are
/// marked so — a caller filtering for open access must not have to guess.
#[test]
fn both_fulltext_locations_are_marked_open_access() {
    let record = normalize(
        &json!({"title": "T", "doi": "10.1/x", "jatsxml": "https://x/jats"}),
        "biorxiv",
    );
    assert_eq!(record.fulltext_sources.len(), 2);
    assert_eq!(record.fulltext_sources[0]["format"], "pdf");
    assert_eq!(record.fulltext_sources[1]["format"], "xml");
    for entry in &record.fulltext_sources {
        assert_eq!(entry["open_access"], true);
        assert_eq!(entry["source"], "biorxiv");
    }
    assert!(record.is_open_access, "a preprint is open access");
}

/// A record with no DOI has no PDF location — the URL is derived from the DOI,
/// so inventing one would produce a link that cannot work.
#[test]
fn a_record_without_a_doi_has_no_pdf_location() {
    let record = normalize(&json!({"title": "T"}), "biorxiv");
    assert!(record.fulltext_sources.is_empty());
}

/// The source-specific extras are always present, so a caller reading them does
/// not have to distinguish absent from empty.
#[test]
fn the_extras_are_always_present() {
    let record = normalize(&json!({"title": "T"}), "biorxiv");
    assert_eq!(record.extras["category"], "");
    assert_eq!(record.extras["published"], "");
    assert_eq!(record.extras["server"], "biorxiv");

    // The record's own `server` field wins over the parameter.
    let record = normalize(&json!({"title": "T", "server": "medrxiv"}), "biorxiv");
    assert_eq!(record.extras["server"], "medrxiv");
}
