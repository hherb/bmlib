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
//! The corpus (43 cases) diffs normalisation and the whole page walk against
//! Python, including the five shapes that must be **refused** rather than read
//! as a quiet day. The named tests state why each refusal exists, which is what
//! a corpus of messages cannot say.

use bmlib::publications::fetchers::biorxiv::{
    fetch_biorxiv, normalize, page_url, pdf_url, read_page_body, PageSource, PAGE_SIZE,
};
use bmlib::publications::fetchers::{FetchError, Progress};
use chrono::NaiveDate;
use serde_json::{json, Value};

const CASES: &str = include_str!("data/biorxiv_cases.json");
const EXPECTED: &str = include_str!("data/biorxiv_expected.json");

/// A page source that serves a fixed sequence of payloads, recording each URL.
struct ScriptedPages {
    payloads: std::cell::RefCell<Vec<Result<Value, FetchError>>>,
    urls: std::cell::RefCell<Vec<String>>,
}

impl PageSource for ScriptedPages {
    fn page(&self, server: &str, date: &str, cursor: usize) -> Result<Value, FetchError> {
        self.urls.borrow_mut().push(page_url(server, date, cursor));
        let mut payloads = self.payloads.borrow_mut();
        if payloads.is_empty() {
            return Ok(json!({"collection": [], "messages": []}));
        }
        payloads.remove(0)
    }
}

fn run_walk(payloads: Vec<Result<Value, FetchError>>, server: &str, day: &str) -> Value {
    let source = ScriptedPages {
        payloads: std::cell::RefCell::new(payloads),
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
            let payloads: Vec<Result<Value, FetchError>> = args["payloads"]
                .as_array()
                .map(|a| a.iter().map(|p| Ok(p.clone())).collect())
                .unwrap_or_default();
            run_walk(payloads, server, day)
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
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
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

fn full_page(count: usize) -> Vec<Value> {
    (0..count)
        .map(|i| json!({"title": format!("R{i}")}))
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
    assert!(error.starts_with("RemoteProtocolError:"), "{error}");
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
