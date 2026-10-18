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

//! The shared retraction-status contract (#504) — the same file Python and
//! bmlibrarian_lite's platforms assert against — and the export index.

use bmlib::publications::retraction_status::{
    crossref_retraction_requests, parse_crossref_retraction_response, retraction_status,
    CrossrefResult, RetractionEvidence, RetractionIndex, RetractionKind, RetractionLookup,
    RetractionSource, RetractionStatus, RETRACTION_INDEX_VERSION,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/retraction_status.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn body(case: &Value) -> Vec<u8> {
    match case.get("body_text") {
        Some(text) => text.as_str().unwrap().as_bytes().to_vec(),
        None => serde_json::to_vec(&case["body"]).unwrap(),
    }
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

fn without_reason(result: &CrossrefResult) -> Value {
    let mut value = serde_json::to_value(result).unwrap();
    if let CrossrefResult::Unreachable { reason } = result {
        assert!(!reason.is_empty(), "an unreachable result names why");
        value.as_object_mut().unwrap().remove("reason");
    }
    value
}

#[test]
fn every_rule_case() {
    let fixture = fixture();
    let cases = fixture["rule_cases"].as_array().unwrap();
    assert!(cases.len() >= 30);
    for case in cases {
        let doi = case["doi"].as_str();
        let crossref = (!case["crossref"].is_null()).then(|| {
            let results = parse_crossref_retraction_response(
                u16::try_from(case["crossref"]["status"].as_u64().unwrap()).unwrap(),
                &body(&case["crossref"]),
                &[doi.unwrap()],
            );
            assert_eq!(results.len(), 1, "{}", case["name"]);
            results.into_values().next().unwrap()
        });
        let export: Option<Vec<RetractionEvidence>> =
            serde_json::from_value(case["export"].clone()).unwrap();
        let verdict = retraction_status(
            doi,
            export.as_deref(),
            crossref.as_ref(),
            &strings(&case["publication_types"]),
        );
        assert_eq!(
            serde_json::to_value(&verdict).unwrap(),
            case["expected"],
            "{}",
            case["name"]
        );
    }
}

#[test]
fn every_parse_case() {
    let fixture = fixture();
    for case in fixture["parse_cases"].as_array().unwrap() {
        let results = parse_crossref_retraction_response(
            u16::try_from(case["status"].as_u64().unwrap()).unwrap(),
            &body(case),
            &strings(&case["requested"]),
        );
        let got: serde_json::Map<String, Value> = results
            .iter()
            .map(|(doi, result)| (doi.clone(), without_reason(result)))
            .collect();
        assert_eq!(Value::Object(got), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn every_request_case() {
    let fixture = fixture();
    for case in fixture["request_cases"].as_array().unwrap() {
        let requests =
            crossref_retraction_requests(&strings(&case["dois"]), case["mailto"].as_str());
        let batches: Vec<Value> = requests
            .batches
            .iter()
            .map(|b| serde_json::json!({"url": b.url, "dois": b.dois}))
            .collect();
        let got = serde_json::json!({"batches": batches, "unsendable": requests.unsendable});
        assert_eq!(got, case["expected"], "{}", case["name"]);
    }
}

#[test]
fn an_undecodable_body_is_unreachable() {
    let results = parse_crossref_retraction_response(200, b"\xff\xfe{", &["10.5555/a"]);
    assert!(matches!(
        results["10.5555/a"],
        CrossrefResult::Unreachable { .. }
    ));
}

// ---------------------------------------------------------------------------
// The export index
// ---------------------------------------------------------------------------

const HEADER: &str = "Record ID,Title,RetractionDate,RetractionDOI,OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature\n";

fn csv() -> String {
    format!(
        "\u{feff}{HEADER}\
         4036,Wakefield,2/6/2010 0:00,10.1016/S0140-6736(10)60175-4,10.1016/S0140-6736(97)11096-0,9500320,Retraction\n\
         17269,Wakefield,3/6/2004 0:00,10.1016/S0140-6736(04)15715-2,10.1016/S0140-6736(97)11096-0,9500320,Correction\n\
         69425,No DOI,7/31/2025 0:00,10.26355/eurrev_202507_37337,unavailable,18401974,Retraction\n\
         22696,No PMID,12/1/2018 0:00,unavailable,10.4314/jfas.v10i4s.222,0,Retraction\n\
         900001,Unknown,1/1/2020 0:00,,10.5555/odd,,Mystery\n\
         ,Empty row,,,,,\n"
    )
}

fn built() -> RetractionIndex {
    let build = RetractionIndex::build(csv().as_bytes(), "2026-10-18").unwrap();
    assert_eq!(build.skipped_rows, 1);
    assert_eq!(build.unknown_natures, vec!["Mystery".to_string()]);
    assert_eq!(build.index.len(), 5);
    build.index
}

#[test]
fn the_index_survives_a_round_trip() {
    let index = built();
    let reopened = RetractionIndex::from_bytes(&index.to_bytes()).unwrap();
    assert_eq!(reopened, index);
    assert_eq!(reopened.data_as_of(), "2026-10-18");
}

#[test]
fn a_lookup_matches_doi_or_pmid_once_each() {
    let index = built();
    let by_doi = index
        .lookup(Some("https://doi.org/10.1016/s0140-6736(97)11096-0"), None)
        .unwrap();
    let by_both = index
        .lookup(Some("10.1016/S0140-6736(97)11096-0"), Some("009500320"))
        .unwrap();
    assert_eq!(by_doi, by_both);
    assert_eq!(by_doi.len(), 2);
    assert_eq!(by_doi[0].record_id.as_deref(), Some("4036"));
    assert_eq!(by_doi[0].kind, RetractionKind::Retraction);
    assert_eq!(by_doi[0].date.as_deref(), Some("2010-02-06"));
    assert_eq!(
        by_doi[0].notice_doi.as_deref(),
        Some("10.1016/s0140-6736(10)60175-4")
    );
    assert_eq!(by_doi[0].source, RetractionSource::RetractionWatchExport);

    let no_doi = index.lookup(None, Some("18401974")).unwrap();
    assert_eq!(no_doi[0].record_id.as_deref(), Some("69425"));
    let odd = index.lookup(Some("10.5555/odd"), None).unwrap();
    assert_eq!(odd[0].kind, RetractionKind::Other);
    assert_eq!(odd[0].raw_kind.as_deref(), Some("Mystery"));
}

#[test]
fn a_lookup_with_nothing_usable_was_not_consulted() {
    let index = built();
    for (doi, pmid) in [
        (None, None),
        (Some("unavailable"), Some("0")),
        (Some("  "), Some("PMC123")),
        (Some("https://doi.org/"), Some("000")),
    ] {
        assert_eq!(index.lookup(doi, pmid), None, "{doi:?} {pmid:?}");
    }
    assert_eq!(index.lookup(Some("10.5555/nothing"), None), Some(vec![]));
    let verdict = retraction_status(None, index.lookup(None, Some("0")).as_deref(), None, &[]);
    assert_eq!(verdict.status, RetractionStatus::Unchecked);
    assert_eq!(
        verdict.unanswered,
        vec![RetractionLookup::RetractionWatchExport, RetractionLookup::Crossref]
    );
}

#[test]
fn an_unusable_index_is_refused() {
    let index = built();
    let mut wrong_version: Value = serde_json::from_slice(&index.to_bytes()).unwrap();
    wrong_version["version"] = (RETRACTION_INDEX_VERSION + 1).into();
    let mut empty = wrong_version.clone();
    empty["version"] = RETRACTION_INDEX_VERSION.into();
    empty["entries"] = Value::Array(vec![]);
    for bytes in [
        b"".to_vec(),
        b"{\"format\": \"something else\"}".to_vec(),
        serde_json::to_vec(&wrong_version).unwrap(),
        serde_json::to_vec(&empty).unwrap(),
    ] {
        assert!(RetractionIndex::from_bytes(&bytes).is_err());
    }
}

#[test]
fn a_build_that_would_say_no_notices_for_everything_is_refused() {
    assert!(RetractionIndex::build(HEADER.as_bytes(), "2026-10-18").is_err());
    assert!(RetractionIndex::build(b"<html>proxy error</html>", "2026-10-18").is_err());
    assert!(RetractionIndex::build(csv().as_bytes(), " ").is_err());
    let mut latin1 = csv().into_bytes();
    latin1.extend_from_slice(b"5,Caf\xe9,1/1/2020 0:00,,10.5555/cafe,,Retraction\n");
    assert!(RetractionIndex::build(&latin1, "2026-10-18").is_err());
}
