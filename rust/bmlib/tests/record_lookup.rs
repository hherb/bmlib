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

//! `lookup_record` held to the Python by the shared fixture
//! `tests/fixtures/record_lookup.json`, written by
//! `scripts/make_record_lookup_fixture.py`: every case's requests, in order, and
//! its result, each report's message aside (a transport failure's text is the
//! HTTP library's). The Rust messages are pinned by the unit tests in
//! `src/publications/lookup.rs`.

use std::collections::{BTreeMap, VecDeque};
use std::sync::Mutex;

use bmlib::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse};
use bmlib::publications::lookup::{
    lookup_record, LookupError, LookupRequest, RecordLookup, SourceReport,
};
use bmlib::publications::models::FetchedRecord;
use serde_json::{json, Value};

const FIXTURE: &str = include_str!("../../../tests/fixtures/record_lookup.json");

struct Replay {
    answers: Mutex<VecDeque<Value>>,
    requests: Mutex<Vec<String>>,
}

impl HttpClient for Replay {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.requests.lock().expect("lock").push(url.to_string());
        let answer = self
            .answers
            .lock()
            .expect("lock")
            .pop_front()
            .unwrap_or_else(|| panic!("no scripted answer for {url}"));
        if answer.get("raises").is_some() {
            return Err(FetchError::Transport("scripted transport failure".into()));
        }
        Ok(HttpResponse::from_bytes(
            u16::try_from(answer["status"].as_u64().expect("status")).expect("u16"),
            answer["body"].as_str().expect("body").as_bytes().to_vec(),
        ))
    }
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).expect("ascii");
            out.push(u8::from_str_radix(hex, 16).expect("hex"));
            i += 3;
        } else {
            out.push(if bytes[i] == b'+' { b' ' } else { bytes[i] });
            i += 1;
        }
    }
    String::from_utf8(out).expect("utf-8")
}

/// A URL as the fixture writes a request: its base, and its decoded parameters.
fn request(url: &str) -> Value {
    let (base, query) = url.split_once('?').unwrap_or((url, ""));
    let params: BTreeMap<String, String> = query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (key, value) = pair.split_once('=').expect("key=value");
            (percent_decode(key), percent_decode(value))
        })
        .collect();
    json!({"url": base, "params": params})
}

/// `FetchedRecord.to_dict()`'s shape.
fn record_json(record: &FetchedRecord) -> Value {
    json!({
        "title": record.title,
        "source": record.source,
        "doi": record.doi,
        "pmid": record.pmid,
        "pmc_id": record.pmc_id,
        "abstract": record.abstract_text,
        "authors": record.authors,
        "journal": record.journal,
        "publication_date": record.publication_date,
        "keywords": record.keywords,
        "publication_types": record.publication_types,
        "is_open_access": record.is_open_access,
        "license": record.license,
        "fulltext_sources": record.fulltext_sources,
        "extras": record.extras,
        "grants": record.grants.iter().map(|g| json!({
            "id": g.id, "publication_id": g.publication_id, "source": g.source,
            "agency": g.agency, "grant_id": g.grant_id, "country": g.country,
        })).collect::<Vec<_>>(),
        "author_affiliations": record.author_affiliations.iter().map(|a| json!({
            "id": a.id, "publication_id": a.publication_id, "source": a.source,
            "author": a.author, "affiliation": a.affiliation, "position": a.position,
        })).collect::<Vec<_>>(),
    })
}

fn outcome(report: &SourceReport) -> Value {
    json!({"outcome": report.outcome.as_str()})
}

fn result_json(result: &RecordLookup) -> Value {
    json!({
        "record": result.record.as_ref().map(record_json),
        "published_version": result.published_version.as_ref().map(|p| json!({
            "pmid": p.pmid, "doi": p.doi, "pmc_id": p.pmc_id,
        })),
        "pubmed": outcome(&result.pubmed),
        "openalex": outcome(&result.openalex),
        "europepmc": outcome(&result.europepmc),
    })
}

#[test]
fn every_fixture_case() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture is JSON");
    let email = fixture["email"].as_str().expect("email");
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(cases.len() >= 40, "the fixture lost its cases");

    let mut diverging = Vec::new();
    for case in cases {
        let input = &case["input"];
        let arg = |key: &str| input.get(key).and_then(Value::as_str);
        let client = Replay {
            answers: Mutex::new(
                case["responses"]
                    .as_array()
                    .expect("responses")
                    .iter()
                    .cloned()
                    .collect(),
            ),
            requests: Mutex::new(Vec::new()),
        };
        let lookup = LookupRequest {
            pmid: arg("pmid"),
            doi: arg("doi"),
            pmc_id: arg("pmc_id"),
            ppr_id: arg("ppr_id"),
            email,
            ncbi_api_key: arg("ncbi_api_key"),
            openalex_api_key: arg("openalex_api_key"),
        };
        let outcome = lookup_record(&client, &lookup);
        let requests: Vec<Value> = client
            .requests
            .lock()
            .expect("lock")
            .iter()
            .map(|url| request(url))
            .collect();
        let mut got = json!({"requests": requests});
        match outcome {
            Ok(result) => got["result"] = result_json(&result),
            Err(LookupError::NoIdentifier) => got["error"] = json!("no_identifier"),
            Err(LookupError::InvalidIdentifier { argument, .. }) => {
                got["error"] = json!("invalid");
                got["argument"] = json!(argument);
            }
        }
        if got != case["expected"] {
            diverging.push(format!(
                "{}\n    expected: {}\n    rust:     {got}",
                case["name"], case["expected"]
            ));
        }
        assert!(
            client.answers.lock().expect("lock").is_empty(),
            "{}: scripted answers never asked for",
            case["name"]
        );
    }
    assert!(
        diverging.is_empty(),
        "{} of {} diverge:\n{}",
        diverging.len(),
        cases.len(),
        diverging.join("\n")
    );
}
