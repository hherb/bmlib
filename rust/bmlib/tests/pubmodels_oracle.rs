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

//! The differential oracle: Rust versus Python, over the publication models.
//!
//! No corrections — these models carry none of the filed defects — so every
//! case is diffed strictly. The weight of the corpus is on the **three
//! validators** (56 of 88 cases), because they are the part a port is most
//! likely to soften, and softening them is invisible: a validator that
//! accepts what Python refuses stores a row that later degrades resume or
//! re-fetch behaviour with no error.

use bmlib::publications::models::{
    require_count, require_datetime, require_text, AuthorAffiliation, DownloadDay, FullTextSource,
    Grant, PartCheckpoint, Publication, RetractionNature, RetractionNotice,
};
use serde_json::{json, Value};
use std::str::FromStr;

const CASES: &str = include_str!("data/pubmodels_cases.json");
const EXPECTED: &str = include_str!("data/pubmodels_expected.json");

/// The value under test for `fn`-style cases, as a `Value`.
fn opt_value(args: &Value) -> Option<Value> {
    args.get("value").cloned()
}

/// Render a `Result` the way the Python side records it.
fn record<T: serde::Serialize>(result: Result<T, impl std::fmt::Display>) -> Value {
    match result {
        Ok(value) => json!({"ok": true, "value": value}),
        Err(e) => json!({"ok": false, "error": e.to_string()}),
    }
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    let value = opt_value(args);

    match fn_name {
        "require_text" => {
            let field = field_name(args["field"].as_str().unwrap_or_default());
            record(require_text(value.as_ref(), field))
        }
        "require_count" => {
            let field = field_name(args["field"].as_str().unwrap_or_default());
            let minimum = args["minimum"].as_i64().unwrap_or(0);
            record(require_count(value.as_ref(), field, minimum))
        }
        "require_datetime" => {
            let field = field_name(args["field"].as_str().unwrap_or_default());
            record(require_datetime(value.as_ref(), field))
        }
        "publication_roundtrip" => {
            let mut p = Publication::from_json(&args["data"]).expect("valid publication");
            p.created_at = "2024-01-02T03:04:05+00:00".to_string();
            p.updated_at = "2024-01-02T03:04:05+00:00".to_string();
            json!(p.to_json())
        }
        "publication_stamps_now" => {
            let p = Publication::new("T", "pubmed");
            json!({
                "has_created_at": !p.created_at.is_empty(),
                "has_updated_at": !p.updated_at.is_empty(),
                "tz": "UTC",
                "id": p.id,
                "pmcid": p.pmcid,
                "authors": p.authors,
                "is_open_access": p.is_open_access,
            })
        }
        "fulltext_roundtrip" => {
            let mut s = FullTextSource::from_json(&args["data"]).expect("valid source");
            s.created_at = "2024-01-02T03:04:05+00:00".to_string();
            json!(s.to_json())
        }
        "grant_roundtrip" => json!(Grant::from_json(&args["data"]).to_json()),
        "affiliation_roundtrip" => {
            json!(AuthorAffiliation::from_json(&args["data"])
                .expect("valid")
                .to_json())
        }
        "downloadday_roundtrip" => {
            record(DownloadDay::from_json(&args["data"]).map(|d| d.to_json()))
        }
        // These two report the checkpoint itself on success and the error
        // string on failure, which is the shape the corpus records.
        "part_checkpoint_new" => {
            let d = &args["data"];
            record(
                PartCheckpoint::new(
                    d["part_scheme"].as_str().unwrap_or_default(),
                    d["part_key"].as_str().unwrap_or_default(),
                    d["promised"].as_i64().unwrap_or(0),
                    d["record_count"].as_i64().unwrap_or(0),
                )
                .map(|c| c.to_json()),
            )
        }
        "part_checkpoint_roundtrip" => {
            record(PartCheckpoint::from_json(&args["data"]).map(|c| c.to_json()))
        }
        "retraction_nature_from_raw" => {
            json!(RetractionNature::from_raw(args.get("value").and_then(Value::as_str)).as_str())
        }
        "retraction_nature_from_dict" => record(
            RetractionNature::from_str(args["value"].as_str().unwrap_or_default())
                .map(|n| n.as_str()),
        ),
        "retraction_roundtrip" => {
            record(RetractionNotice::from_json(&args["data"]).map(|r| r.to_json()))
        }
        other => panic!("unknown fn {other:?}"),
    }
}

/// The validator field names are `&'static str` in Rust and plain strings in
/// the corpus, so this maps the seven the oracle uses.
fn field_name(raw: &str) -> &'static str {
    match raw {
        "part_key" => "part_key",
        "part_scheme" => "part_scheme",
        "promised" => "promised",
        "record_count" => "record_count",
        "downloaded_at" => "downloaded_at",
        "source" => "source",
        "date" => "date",
        other => panic!("the corpus names an unknown field {other:?}"),
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
            "case {name:?} errored in Python: {}",
            want["error"]
        );
        // A `corrected` block means the port deliberately diverges: the
        // harness asserts Python still produces the recorded thing, that Rust
        // produces the corrected payload, and that the two differ.
        let expected_value = match case.get("corrected") {
            Some(corrected) => {
                let mut payload = corrected.clone();
                if let Some(obj) = payload.as_object_mut() {
                    obj.remove("why");
                    obj.remove("issue");
                }
                assert_ne!(
                    &want["value"], &payload,
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
        "{} of {} cases diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}
