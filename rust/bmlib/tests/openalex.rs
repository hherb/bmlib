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
//! The corpus (57 cases) diffs normalisation, the abstract rebuild and the
//! whole cursor walk against Python, with **one corrected case**: a boolean
//! `meta.count` (#313). The named tests state why each guard exists.

use bmlib::publications::fetchers::openalex::{
    page_params, reconstruct_abstract, version_map, walk, CursorPages, PER_PAGE,
};
use bmlib::publications::fetchers::{FetchError, Progress};
use chrono::NaiveDate;
use serde_json::{json, Value};

const CASES: &str = include_str!("data/openalex_cases.json");
const EXPECTED: &str = include_str!("data/openalex_expected.json");

struct ScriptedPages {
    payloads: std::cell::RefCell<Vec<Result<Value, FetchError>>>,
    cursors: std::cell::RefCell<Vec<String>>,
}

impl CursorPages for ScriptedPages {
    fn page(
        &self,
        _date: &str,
        cursor: &str,
        _email: &str,
        _api_key: Option<&str>,
    ) -> Result<Value, FetchError> {
        self.cursors.borrow_mut().push(cursor.to_string());
        let mut payloads = self.payloads.borrow_mut();
        if payloads.is_empty() {
            return Ok(json!({"results": [], "meta": {"count": 0, "next_cursor": null}}));
        }
        payloads.remove(0)
    }
}

fn run_walk(payloads: Vec<Result<Value, FetchError>>, email: &str, api_key: Option<&str>) -> Value {
    let source = ScriptedPages {
        payloads: std::cell::RefCell::new(payloads),
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
            let payloads: Vec<Result<Value, FetchError>> = args["payloads"]
                .as_array()
                .map(|a| a.iter().map(|p| Ok(p.clone())).collect())
                .unwrap_or_default();
            run_walk(
                payloads,
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
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );

        let expected_value = match case.get("corrected") {
            Some(corrected) => {
                // The corpus records the corrected result in the same
                // `{ok, value, why, issue}` envelope the harness uses, so the
                // payload is the `value` with the annotations gone.
                let mut payload = corrected
                    .get("value")
                    .cloned()
                    .unwrap_or_else(|| corrected.clone());
                if let Some(obj) = payload.as_object_mut() {
                    obj.remove("why");
                    obj.remove("issue");
                    obj.remove("ok");
                }
                assert_ne!(
                    want["value"], payload,
                    "{name}: the correction is not a difference, so Python has changed"
                );
                payload
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

/// **A boolean is not a count** — corrected from Python (#313), where
/// `isinstance(True, int)` accepts it and the literal `True` becomes the
/// promised count, reaching a caller-facing message. The comment beside Python's
/// check states the rule the check fails to enforce, and
/// `transparency.analyzer._json_count` excludes `bool` by name.
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
