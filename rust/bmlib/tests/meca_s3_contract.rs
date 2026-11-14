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

//! The shared S3 contracts, the same files Python's
//! `tests/test_meca_s3_contract.py` asserts: `s3_sigv4.json` (AWS's published
//! examples and Python's signed requests) and `s3_responses.json` (S3 replies
//! and their readings).

use bmlib::fulltext::meca::s3::{
    classify_error, list_objects_request, object_request, parse_list_objects_v2,
};
use bmlib::fulltext::meca::sigv4::{sign_get, S3Credentials, SignError, SignedRequest};
use chrono::{DateTime, Utc};
use serde_json::{json, Value};
use std::collections::BTreeMap;

const SIGV4: &str = include_str!("../../../tests/fixtures/s3_sigv4.json");
const RESPONSES: &str = include_str!("../../../tests/fixtures/s3_responses.json");

fn load(text: &str) -> Value {
    serde_json::from_str(text).expect("the fixture is JSON")
}

/// A case's body as bytes: `body` (text) or `body_hex` (bytes that are not UTF-8).
fn body(case: &Value) -> Vec<u8> {
    if let Some(hex) = case.get("body_hex").and_then(Value::as_str) {
        return (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect();
    }
    case["body"].as_str().unwrap().as_bytes().to_vec()
}

fn credentials(raw: &Value) -> Result<S3Credentials, SignError> {
    S3Credentials::new(
        raw["access_key_id"].as_str().unwrap(),
        raw["secret_access_key"].as_str().unwrap(),
        raw.get("session_token").and_then(Value::as_str),
    )
}

fn now(raw: &Value) -> DateTime<Utc> {
    DateTime::parse_from_rfc3339(raw.as_str().unwrap())
        .unwrap()
        .with_timezone(&Utc)
}

fn pairs(raw: &Value) -> Vec<(String, String)> {
    match raw {
        Value::Array(items) => items
            .iter()
            .map(|p| {
                (
                    p[0].as_str().unwrap().to_string(),
                    p[1].as_str().unwrap().to_string(),
                )
            })
            .collect(),
        Value::Object(map) => map
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect(),
        _ => panic!("pairs: {raw}"),
    }
}

fn run(case: &Value, key: &S3Credentials, at: DateTime<Utc>) -> Result<SignedRequest, SignError> {
    let args = &case["args"];
    let s = |name: &str| args[name].as_str().unwrap();
    match case["call"].as_str().unwrap() {
        "sign_get" => sign_get(
            s("host"),
            s("path"),
            &pairs(&args["query"]),
            key,
            s("region"),
            at,
            &pairs(&args["extra_headers"]),
        ),
        "list_objects_request" => list_objects_request(
            key,
            s("bucket"),
            s("region"),
            at,
            s("prefix"),
            args["continuation_token"].as_str(),
        ),
        "object_request" => object_request(key, s("bucket"), s("region"), s("key"), at),
        other => panic!("unknown call {other}"),
    }
}

#[test]
fn every_signing_case_matches() {
    let fixture = load(SIGV4);
    let cases = fixture["cases"].as_array().unwrap();
    assert!(cases.len() >= 7);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let key = credentials(&case["credentials"]).unwrap();
        let signed = run(case, &key, now(&case["now"])).unwrap_or_else(|e| panic!("{name}: {e}"));
        let expected = &case["expected"];
        assert_eq!(
            signed.canonical_request, expected["canonical_request"],
            "{name}"
        );
        assert_eq!(signed.string_to_sign, expected["string_to_sign"], "{name}");
        assert_eq!(signed.signature, expected["signature"], "{name}");
        assert_eq!(signed.url, expected["url"], "{name}");
        let headers: BTreeMap<String, String> =
            serde_json::from_value(expected["headers"].clone()).unwrap();
        assert_eq!(signed.headers, headers, "{name}");
    }
}

#[test]
fn every_refusal_is_refused() {
    let fixture = load(SIGV4);
    let key = credentials(&fixture["refused_credentials"]).unwrap();
    let at = now(&fixture["refused_now"]);
    for case in fixture["refused"].as_array().unwrap() {
        let mut call = case.clone();
        call["call"] = json!("sign_get");
        assert!(run(&call, &key, at).is_err(), "{}", case["name"]);
    }
    for case in fixture["refused_keys"].as_array().unwrap() {
        assert!(
            credentials(&case["credentials"]).is_err(),
            "{}",
            case["name"]
        );
    }
    let object_keys = fixture["refused_object_keys"].as_array().unwrap();
    assert!(!object_keys.is_empty());
    for raw in object_keys {
        let object = raw.as_str().unwrap();
        let refused = object_request(&key, "medrxiv-src-monthly", "us-east-1", object, at);
        assert!(refused.is_err(), "{object}");
    }
}

#[test]
fn every_listing_reads_as_python_reads_it() {
    let fixture = load(RESPONSES);
    for case in fixture["list_pages"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let got = match parse_list_objects_v2(&body(case)) {
            Ok(page) => json!({
                "objects": page.objects.iter().map(|o| json!({
                    "key": o.key, "size": o.size, "etag": o.etag, "last_modified": o.last_modified,
                })).collect::<Vec<_>>(),
                "is_truncated": page.is_truncated,
                "next_continuation_token": page.next_continuation_token,
            }),
            Err(e) => json!({ "error": e.kind.as_str() }),
        };
        assert_eq!(got, case["expected"], "{name}");
    }
}

#[test]
fn every_error_reads_as_python_reads_it() {
    let fixture = load(RESPONSES);
    for case in fixture["errors"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let status = u16::try_from(case["status"].as_u64().unwrap()).unwrap();
        let error = classify_error(status, &body(case));
        let got = json!({
            "kind": error.kind.as_str(),
            "code": error.code,
            "detail": error.detail,
            "retryable": error.retryable(),
            "reason": error.reason(),
        });
        assert_eq!(got, case["expected"], "{name}");
    }
}
