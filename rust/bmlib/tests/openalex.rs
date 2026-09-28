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

//! OpenAlex walker — the oracle and the named tests.
//!
//! The corpus (58 cases) diffs normalisation, the abstract rebuild and the
//! whole cursor walk against Python. One correction — a boolean `meta.count`
//! (#313) — was retired when Python adopted the same refusal, so that case
//! diffs strictly now; **two more are `corrected` blocks** (#349): a non-2xx now
//! actually reaches the walk, and the message the port writes for it is its own
//! wording where Python's is httpx's.
//! `the_corrected_cases_are_the_ones_the_register_names` pins that list, and the
//! oracle test asserts both directions — Python still says what the corpus
//! records, and Rust produces the corrected value. The named tests state why
//! each guard exists.

use bmlib::publications::fetchers::openalex::{
    page_params, reconstruct_abstract, version_map, walk, CursorPages, HttpCursorPages, API_URL,
    PER_PAGE,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse, Progress};
use chrono::NaiveDate;
use serde_json::{json, Value};

const CASES: &str = include_str!("data/openalex_cases.json");
const EXPECTED: &str = include_str!("data/openalex_expected.json");

/// One scripted page: the body the source serves, the status it answers with,
/// or a failure a named test injects.
///
/// The status is a variant of its own rather than an `Err` built by the caller
/// because **the URL belongs to the source, not to the corpus**: the corpus
/// names a status, and [`ScriptedPages::page`] fills in the URL it actually
/// asked for — which is what the real `HttpCursorPages` does.
enum Scripted {
    Body(Value),
    Status(u16),
    Error(FetchError),
}

impl From<Result<Value, FetchError>> for Scripted {
    fn from(result: Result<Value, FetchError>) -> Self {
        match result {
            Ok(value) => Scripted::Body(value),
            Err(error) => Scripted::Error(error),
        }
    }
}

/// Read one corpus payload as a scripted page.
///
/// A response is either a bare body (HTTP 200) or an object
/// `{"http_status": N, "body": B}` — the marker the Python dumper reads too.
/// An object carrying any other key is a body, so a payload is only ever read
/// as a status when it says so under that one name.
fn scripted(payload: &Value) -> Scripted {
    if let Some(object) = payload.as_object() {
        let is_marker = object.contains_key("http_status")
            && object
                .keys()
                .all(|key| key == "http_status" || key == "body");
        if is_marker {
            let status = object["http_status"]
                .as_u64()
                .unwrap_or_else(|| panic!("http_status is an integer: {payload}"));
            return Scripted::Status(status as u16);
        }
    }
    Scripted::Body(payload.clone())
}

/// The URL `HttpCursorPages` asks for, built through the library's own
/// [`page_params`].
///
/// Not a second copy of the query: the harness needs the URL only to name it in
/// a status refusal, and building it from the same source is what keeps the
/// message identical to the one the real page source produces.
fn page_url(date: &str, cursor: &str, email: &str, api_key: Option<&str>) -> String {
    let query: Vec<String> = page_params(date, cursor, email, api_key)
        .into_iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect();
    format!("{API_URL}?{}", query.join("&"))
}

struct ScriptedPages {
    payloads: std::cell::RefCell<Vec<Scripted>>,
    cursors: std::cell::RefCell<Vec<String>>,
}

impl CursorPages for ScriptedPages {
    fn page(
        &self,
        date: &str,
        cursor: &str,
        email: &str,
        api_key: Option<&str>,
    ) -> Result<Value, FetchError> {
        self.cursors.borrow_mut().push(cursor.to_string());
        let mut payloads = self.payloads.borrow_mut();
        if payloads.is_empty() {
            return Ok(json!({"results": [], "meta": {"count": 0, "next_cursor": null}}));
        }
        match payloads.remove(0) {
            Scripted::Body(value) => Ok(value),
            Scripted::Status(status) => Err(FetchError::HttpStatus {
                url: page_url(date, cursor, email, api_key),
                status,
            }),
            Scripted::Error(error) => Err(error),
        }
    }
}

fn run_scripted(pages: Vec<Scripted>, email: &str, api_key: Option<&str>) -> Value {
    let source = ScriptedPages {
        payloads: std::cell::RefCell::new(pages),
        cursors: std::cell::RefCell::new(Vec::new()),
    };
    let date = NaiveDate::from_ymd_opt(2024, 6, 10).expect("date");
    let mut progress: Vec<Value> = Vec::new();
    let outcome = walk(&source, date, email, api_key, &mut |p| {
        if let Progress::Page {
            delivered,
            promised,
        } = p
        {
            progress.push(json!([delivered, promised, "in_progress"]));
        }
    });
    let cursors = source.cursors.borrow().clone();
    // The corpus reports the query each page was asked for, which the *walker*
    // does not build — `page_params` does. Reconstructing it here keeps the
    // comparison honest about which layer owns the URL.
    let params: Vec<Value> = cursors
        .iter()
        .map(|c| {
            let pairs = page_params("2024-06-10", c, email, api_key);
            let mut object = serde_json::Map::new();
            for (k, v) in pairs {
                // `per_page` is a number in the corpus's dict, not a string.
                if k == "per_page" {
                    object.insert(k, json!(v.parse::<i64>().unwrap_or(0)));
                } else {
                    object.insert(k, json!(v));
                }
            }
            Value::Object(object)
        })
        .collect();
    json!({
        "status": outcome.status,
        "error": outcome.error,
        "note": outcome.note,
        "record_count": outcome.records.len(),
        "cursors": cursors,
        "params": params,
        "records": outcome.records.iter().map(|r| r.title.clone()).collect::<Vec<_>>(),
        "progress": progress,
    })
}

/// The named tests' entry point: pages given as bodies, or as an injected
/// failure.
fn run_walk(payloads: Vec<Result<Value, FetchError>>, email: &str, api_key: Option<&str>) -> Value {
    run_scripted(
        payloads.into_iter().map(Scripted::from).collect(),
        email,
        api_key,
    )
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "abstract" => match reconstruct_abstract(args.get("index")) {
            Some(text) => json!(text),
            None => Value::Null,
        },
        "normalize" => {
            let record = bmlib::publications::fetchers::openalex::normalize(&args["raw"])
                .expect("a work object");
            json!({
                "title": record.title,
                "source": record.source,
                "doi": record.doi,
                "pmid": record.pmid,
                "abstract": record.abstract_text,
                "authors": record.authors,
                "journal": record.journal,
                "publication_date": record.publication_date,
                "keywords": record.keywords,
                "publication_types": record.publication_types,
                "is_open_access": record.is_open_access,
                "license": record.license,
                "fulltext_sources": record.fulltext_sources,
            })
        }
        "fetch" => {
            let pages: Vec<Scripted> = args["payloads"]
                .as_array()
                .map(|a| a.iter().map(scripted).collect())
                .unwrap_or_default();
            run_scripted(
                pages,
                args.get("email").and_then(Value::as_str).unwrap_or("a@b.c"),
                args.get("api_key").and_then(Value::as_str),
            )
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

        let got = run(case);
        if got != expected_value {
            failures.push(format!(
                "  {name}\n    expected: {}\n    rust:     {}",
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
/// Both are #349's — a non-2xx now actually reaches the walk — and a corpus edit
/// cannot quietly attach a block to another input, drop one, or strip its
/// reason: the names, the issue each cites and the presence of a `why` are all
/// asserted. Every one of them is also asserted against Python inside
/// `the_port_agrees_with_python_on_every_case`.
///
/// **And each is asserted to have reached the rule it is named for**, on the
/// Python side too. That is not the same check as "Python differs from the
/// corrected value": a dumper that stopped honouring the `http_status` marker
/// would serve a list payload, Python would answer `ValueError: … a list
/// payload …`, and that still differs from the corrected value — so the case
/// would stay green while testing the refusal it used to duplicate. The
/// assertion is on the committed expectation, which is why regenerating it is
/// what makes this fire.
#[test]
fn the_corrected_cases_are_the_ones_the_register_names() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let corrected: Vec<&Value> = cases
        .as_array()
        .expect("cases is a list")
        .iter()
        .filter(|case| case.get("corrected").is_some())
        .collect();
    let names: Vec<&str> = corrected
        .iter()
        .map(|case| case["name"].as_str().unwrap_or_default())
        .collect();
    assert_eq!(
        names,
        vec!["fetch/http-error", "fetch/http-error-keeps-the-records"],
        "this corpus carries exactly #349's two divergences"
    );
    for case in &corrected {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(
            case["corrected"]["issue"],
            json!(349),
            "{name}: the block must cite the issue that owns it"
        );
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
            error.starts_with("HTTPStatusError: "),
            "{name}: Python must reach the status path, not a payload refusal: {python}"
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
// The abstract rebuild
// ---------------------------------------------------------------------------

/// OpenAlex stores an abstract as a word → positions index, so the word order
/// comes only from the positions. A rebuild that used the dict's order would
/// produce a different sentence from the same paper.
#[test]
fn the_abstract_is_ordered_by_position_not_by_key() {
    let index = json!({"world": [1], "hello": [0]});
    assert_eq!(
        reconstruct_abstract(Some(&index)),
        Some("hello world".to_string())
    );
}

/// A word occurring more than once contributes at each of its positions.
#[test]
fn a_repeated_word_lands_at_every_position() {
    let index = json!({"the": [0, 2], "cat": [1]});
    assert_eq!(
        reconstruct_abstract(Some(&index)),
        Some("the cat the".to_string())
    );
}

/// An absent or empty index is `None`, **not** an empty string — an empty
/// string is not SQL NULL, so the storage layer's `COALESCE` merge could never
/// fill the abstract in from another source later.
#[test]
fn an_absent_or_empty_index_is_none_not_an_empty_string() {
    assert_eq!(reconstruct_abstract(None), None);
    assert_eq!(reconstruct_abstract(Some(&json!({}))), None);
    assert_eq!(reconstruct_abstract(Some(&json!({"a": []}))), None);
    assert_eq!(reconstruct_abstract(Some(&json!(null))), None);
}

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

/// The DOI and PMID arrive as URLs and are **stripped**: the same paper fetched
/// from OpenAlex and from PubMed must dedup on one key.
#[test]
fn the_doi_and_pmid_url_prefixes_are_stripped() {
    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "doi": "https://doi.org/10.1/x",
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123"},
    }))
    .expect("a work object");
    assert_eq!(record.doi.as_deref(), Some("10.1/x"));
    assert_eq!(record.pmid.as_deref(), Some("123"));

    // A value that is *only* the prefix strips to nothing and becomes `None`
    // rather than an empty identifier no lookup can match.
    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "doi": "https://doi.org/",
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/"},
    }))
    .expect("a work object");
    assert_eq!(record.doi, None);
    assert_eq!(record.pmid, None);
}

/// An unrecognised location version is **passed through**, not dropped: the
/// vocabulary belongs to OpenAlex, and a version this port does not know is
/// still information a caller can read.
#[test]
fn an_unknown_location_version_is_passed_through() {
    assert_eq!(version_map().get("publishedVersion"), Some(&"published"));
    assert_eq!(version_map().get("acceptedVersion"), Some(&"accepted"));
    assert_eq!(version_map().get("submittedVersion"), Some(&"preprint"));

    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "locations": [{"version": "novelVersion", "landing_page_url": "u"}],
    }))
    .expect("a work object");
    assert_eq!(record.fulltext_sources[0]["version"], "novelVersion");

    // A mapped version is translated.
    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "locations": [{"version": "publishedVersion", "landing_page_url": "u"}],
    }))
    .expect("a work object");
    assert_eq!(record.fulltext_sources[0]["version"], "published");

    // An absent version is an absent key, not a null one.
    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "locations": [{"landing_page_url": "u"}],
    }))
    .expect("a work object");
    assert!(
        record.fulltext_sources[0].get("version").is_none(),
        "{}",
        record.fulltext_sources[0]
    );
}

/// Each location contributes up to two sources — a landing page and a PDF —
/// each carrying **its own** open-access flag, not the work's.
#[test]
fn each_location_contributes_its_own_sources_and_flag() {
    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "open_access": {"is_oa": true},
        "locations": [
            {"source": {"display_name": "S"}, "is_oa": false,
             "landing_page_url": "http://l", "pdf_url": "http://p"},
            {"source": {"display_name": "Other"}, "is_oa": true, "landing_page_url": "http://l2"},
        ],
    }))
    .expect("a work object");
    assert_eq!(record.fulltext_sources.len(), 3);
    assert_eq!(record.fulltext_sources[0]["format"], "html");
    assert_eq!(record.fulltext_sources[0]["open_access"], false);
    assert_eq!(record.fulltext_sources[1]["format"], "pdf");
    assert_eq!(record.fulltext_sources[1]["open_access"], false);
    assert_eq!(record.fulltext_sources[2]["source"], "Other");
    assert_eq!(record.fulltext_sources[2]["open_access"], true);
    assert!(record.is_open_access, "the work's own flag is separate");
}

/// A location naming no source gets `"unknown"` rather than an absent field, so
/// a caller rendering sources never has to handle a missing name.
#[test]
fn a_location_without_a_source_names_it_unknown() {
    let record = bmlib::publications::fetchers::openalex::normalize(&json!({
        "title": "T",
        "locations": [{"landing_page_url": "u"}],
    }))
    .expect("a work object");
    assert_eq!(record.fulltext_sources[0]["source"], "unknown");
}

// ---------------------------------------------------------------------------
// The walk
// ---------------------------------------------------------------------------

fn page(results: Vec<Value>, count: Value, next: Value) -> Value {
    json!({"results": results, "meta": {"count": count, "next_cursor": next}})
}

fn work(title: &str) -> Value {
    json!({"title": title})
}

/// The cursor is seeded with `"*"` and the loop **exits on the `None` the last
/// page returns** — so a walk that never sees a null cursor would not stop.
#[test]
fn the_cursor_seeds_with_star_and_ends_on_null() {
    let result = run_walk(
        vec![
            Ok(page(vec![work("A")], json!(2), json!("c1"))),
            Ok(page(vec![work("B")], json!(2), json!(null))),
        ],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "completed");
    assert_eq!(result["cursors"], json!(["*", "c1"]));
    assert_eq!(result["record_count"], 2);
}

/// **An empty page while `meta.count` says works remain** is a walk that stopped
/// serving them — the late-page death the shortfall floor cannot catch, since
/// 600 of 1,000 clears it. Without the stall flag OpenAlex reached the
/// reconciliation judged by the floor alone.
#[test]
fn an_empty_late_page_stalls_however_large_the_walk() {
    // A walk that delivered plenty and still stopped short: the floor would
    // pass this, and only the stall flag catches it.
    let many: Vec<Value> = (0..600).map(|i| work(&format!("W{i}"))).collect();
    let result = run_walk(
        vec![
            Ok(page(many, json!(1000), json!("c1"))),
            Ok(page(vec![], json!(1000), json!("c2"))),
        ],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "failed");
    assert!(result["error"]
        .as_str()
        .unwrap_or_default()
        .contains("empty page"));
}

/// An empty page that has **met** the promise completes: the reconciliation
/// returns early once `delivered >= promised` and never consults the stall flag.
#[test]
fn an_empty_page_that_met_the_promise_completes() {
    let result = run_walk(
        vec![
            Ok(page(
                vec![work("A"), work("B"), work("C")],
                json!(3),
                json!("c1"),
            )),
            Ok(page(vec![], json!(3), json!(null))),
        ],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "completed");
    assert_eq!(result["record_count"], 3);
}

/// Breaking on an empty page also **bounds the loop**: no results and a
/// non-null `next_cursor` would otherwise repeat for ever. This test would hang
/// rather than fail if the break were removed.
#[test]
fn an_empty_page_with_a_cursor_still_ends_the_walk() {
    let result = run_walk(
        vec![Ok(page(vec![], json!(0), json!("always-more")))],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "completed");
    assert_eq!(result["cursors"], json!(["*"]));
}

/// A **non-object payload** read through `.get()` defaults is indistinguishable
/// from a day with no works — which is how a rejected query came to be stored as
/// a completed day (#88).
#[test]
fn a_non_object_payload_is_refused() {
    let result = run_walk(vec![Ok(json!([1, 2]))], "a@b.c", None);
    assert_eq!(result["status"], "failed");
    assert!(result["error"]
        .as_str()
        .unwrap_or_default()
        .contains("not an object"));
}

/// The promised count is read on the **first page only**, and a page that
/// carries no numeric count is refused — the check exists for the error bodies
/// that send none.
#[test]
fn a_first_page_without_a_numeric_count_is_refused() {
    for count in [json!("many"), json!(null)] {
        let result = run_walk(vec![Ok(page(vec![], count, json!(null)))], "a@b.c", None);
        assert_eq!(result["status"], "failed");
        assert!(
            result["error"]
                .as_str()
                .unwrap_or_default()
                .contains("no numeric count"),
            "{result}"
        );
    }
}

/// **A boolean is not a count.** `isinstance(True, int)` accepts it in Python,
/// and the literal `True` then becomes the promised count and reaches a
/// caller-facing message; the comment beside Python's check already stated the
/// rule the check failed to enforce, and `transparency.analyzer._json_count`
/// excluded `bool` by name.
///
/// This was the port's one corrected case (#313) until Python adopted the same
/// refusal, at which point the correction was retired and the oracle case diffs
/// strictly.
#[test]
fn a_boolean_count_is_refused() {
    for count in [json!(true), json!(false)] {
        let result = run_walk(
            vec![Ok(page(vec![], count.clone(), json!(null)))],
            "a@b.c",
            None,
        );
        assert_eq!(result["status"], "failed", "{count}");
        assert!(
            result["error"]
                .as_str()
                .unwrap_or_default()
                .contains("no numeric count"),
            "{count} gave {result}"
        );
        assert!(
            !result["error"]
                .as_str()
                .unwrap_or_default()
                .contains("True"),
            "the boolean must not reach the message as a number: {result}"
        );
    }
}

/// A results list whose members are **not work objects** is *this page*
/// failing, reported here rather than escaping as "Fetcher raised" — the wrong
/// layer for a malformed payload (#91).
#[test]
fn a_results_member_that_is_not_a_work_is_refused_at_the_page() {
    let result = run_walk(
        vec![Ok(page(vec![json!(1)], json!(1), json!(null)))],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "failed");
    assert!(result["error"]
        .as_str()
        .unwrap_or_default()
        .contains("could not normalise"));
}

/// A failure **keeps what the walk delivered**: those records were already
/// handed to the caller and will be stored, and the day is retried regardless.
#[test]
fn a_failure_keeps_the_records_already_delivered() {
    let result = run_walk(
        vec![
            Ok(page(vec![work("A"), work("B")], json!(10), json!("c1"))),
            Ok(json!([1, 2])),
        ],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "failed");
    assert_eq!(result["record_count"], 2);
    assert_eq!(result["records"], json!(["A", "B"]));
}

/// Progress is reported after each page that carried results, and not for the
/// empty page that ended the walk.
#[test]
fn progress_is_reported_per_page_with_results() {
    let result = run_walk(
        vec![
            Ok(page(vec![work("A")], json!(2), json!("c1"))),
            Ok(page(vec![work("B")], json!(2), json!(null))),
        ],
        "a@b.c",
        None,
    );
    assert_eq!(
        result["progress"],
        json!([[1, 2, "in_progress"], [2, 2, "in_progress"]])
    );
}

/// A transport failure is a failure with the type named: `str(OSError())` is the
/// empty string, which reads downstream as "no error" and is dropped from the
/// report entirely.
#[test]
fn a_transport_failure_names_its_type() {
    let result = run_walk(
        vec![Err(FetchError::Transport("timed out".to_string()))],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "failed");
    let error = result["error"].as_str().unwrap_or_default();
    assert!(error.starts_with("RemoteProtocolError:"), "{error}");
    assert!(error.contains("timed out"), "{error}");
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
    let result = run_scripted(vec![Scripted::Status(500)], "a@b.c", None);
    assert_eq!(result["status"], "failed", "{result}");
    assert_eq!(result["record_count"], 0, "{result}");
    assert_eq!(result["cursors"], json!(["*"]), "{result}");
    assert_eq!(
        result["error"],
        json!(format!(
            "HTTPStatusError: {} returned HTTP 500",
            page_url("2024-06-10", "*", "a@b.c", None)
        )),
        "{result}"
    );

    // A client error and a server error are the same refusal here; 429 and 503
    // are what a rate limit and an outage produce, and neither is a quiet day.
    for status in [403u16, 404, 429, 503] {
        let result = run_scripted(vec![Scripted::Status(status)], "a@b.c", None);
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
/// stored, and the day is retried whatever this run concluded. The failing
/// request is the one for the cursor the first page named.
#[test]
fn a_status_failure_keeps_the_records_already_delivered() {
    let result = run_scripted(
        vec![
            Scripted::Body(page(vec![work("A")], json!(2), json!("c1"))),
            Scripted::Status(500),
        ],
        "a@b.c",
        None,
    );
    assert_eq!(result["status"], "failed", "{result}");
    assert_eq!(result["record_count"], 1, "{result}");
    assert_eq!(result["records"], json!(["A"]), "{result}");
    assert_eq!(result["progress"], json!([[1, 2, "in_progress"]]));
    assert_eq!(result["cursors"], json!(["*", "c1"]), "{result}");
    let error = result["error"].as_str().unwrap_or_default();
    assert!(
        error.ends_with("returned HTTP 500"),
        "the status must reach the message: {error}"
    );
    assert!(
        error.contains("cursor=c1"),
        "the failure names the page it happened on, not the first: {error}"
    );
}

/// A transport that answers with one fixed status, so the **real**
/// [`HttpCursorPages`] is what the assertions below are about.
///
/// The scripted `CursorPages` above stands in for the walker's page seam and
/// builds the same `HttpStatus` the library does — which is exactly why this
/// test exists: a corpus case cannot see a change in `HttpCursorPages` itself,
/// and before this only the gated live suite touched it.
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

/// The **real** cursor pages refuse a non-success status as a status error, and
/// name the URL the walker asked for — the property the corpus's marker is a
/// model of.
#[test]
fn the_real_cursor_pages_refuse_a_status_as_a_status_error() {
    let client = std::sync::Arc::new(OneStatus {
        status: 503,
        asked: std::sync::Mutex::new(Vec::new()),
    });
    let source = HttpCursorPages {
        client: client.clone(),
    };
    let asked = page_url("2024-06-10", "*", "a@b.c", None);
    let error = source
        .page("2024-06-10", "*", "a@b.c", None)
        .expect_err("a 503 is refused");
    assert_eq!(
        error,
        FetchError::HttpStatus {
            url: asked.clone(),
            status: 503
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
    let outcome = walk(&source, date, "a@b.c", None, &mut |_| {});
    assert_eq!(outcome.status, "failed");
    let expected = format!("HTTPStatusError: {asked} returned HTTP 503");
    assert_eq!(outcome.error.as_deref(), Some(expected.as_str()));
    assert!(outcome.records.is_empty());
}

// ---------------------------------------------------------------------------
// Query construction
// ---------------------------------------------------------------------------

/// The filter pins **both** ends of the day, so a work dated outside it cannot
/// arrive from a date-filtered query.
#[test]
fn the_query_pins_both_ends_of_the_day() {
    let params = page_params("2024-06-10", "*", "a@b.c", None);
    let filter = params
        .iter()
        .find(|(k, _)| k == "filter")
        .map(|(_, v)| v.clone())
        .expect("a filter");
    assert_eq!(
        filter,
        "from_publication_date:2024-06-10,to_publication_date:2024-06-10"
    );
    assert!(params
        .iter()
        .any(|(k, v)| k == "per_page" && v == &PER_PAGE.to_string()));
    assert!(params.iter().any(|(k, v)| k == "mailto" && v == "a@b.c"));
    assert!(
        !params.iter().any(|(k, _)| k == "api_key"),
        "no key means no parameter"
    );
}

/// The API key is sent **only when supplied** — an empty string is a key the
/// caller did not give.
#[test]
fn the_api_key_is_sent_only_when_supplied() {
    let params = page_params("2024-06-10", "*", "a@b.c", Some("KEY"));
    assert!(params.iter().any(|(k, v)| k == "api_key" && v == "KEY"));
}
