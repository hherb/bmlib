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

//! `store_retraction_notices` and `lookup_retractions`, against Python on real SQLite.
//!
//! Both languages run the same `CREATE TABLE` and the same `INSERT … ON CONFLICT`,
//! so the oracle diffs the *result* rather than the SQL text. That matters for the
//! upsert: an `excluded` list that refreshed `created_at` would still insert and
//! update correctly while losing when a notice was first seen, and only a stored
//! value shows that.

use bmlib::db::{open_memory, Db};
use bmlib::publications::models::{RetractionNature, RetractionNotice};
use bmlib::publications::retractions::{lookup_retractions, store_retraction_notices};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/retraction_store_cases.json");
const EXPECTED: &str = include_str!("data/retraction_store_expected.json");

/// A fresh in-memory database with the schema applied.
fn database() -> rusqlite::Connection {
    let mut conn = open_memory().expect("in-memory sqlite");
    // **`ensure_schema` and not the DDL string directly**: the schema is 18
    // statements, and `execute_raw` sends one — the same call the migrations use
    // is what applies them all.
    bmlib::publications::schema::ensure_schema(&mut conn).expect("schema applies");
    conn
}

fn notice(spec: &Value) -> RetractionNotice {
    let field = |name: &str| spec.get(name).and_then(Value::as_str).map(str::to_string);
    let nature = match spec["nature"].as_str().unwrap_or("retraction") {
        "retraction" => RetractionNature::Retraction,
        "correction" => RetractionNature::Correction,
        "expression_of_concern" => RetractionNature::ExpressionOfConcern,
        "reinstatement" => RetractionNature::Reinstatement,
        _ => RetractionNature::Other,
    };
    RetractionNotice {
        record_id: spec["record_id"].as_str().unwrap_or_default().to_string(),
        nature,
        doi: field("doi"),
        pmid: field("pmid"),
        notice_doi: field("notice_doi"),
        notice_pmid: field("notice_pmid"),
        title: field("title"),
        journal: field("journal"),
        retraction_date: field("retraction_date"),
        original_paper_date: field("original_paper_date"),
        reasons: spec
            .get("reasons")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default(),
        raw_nature: field("raw_nature"),
    }
}

fn run(case: &Value) -> Result<Value, String> {
    let mut db = database();
    let mut out: Vec<Value> = Vec::new();
    for step in case["steps"].as_array().expect("steps") {
        match step["op"].as_str().expect("op") {
            "store" => {
                let notices: Vec<RetractionNotice> = step["notices"]
                    .as_array()
                    .expect("notices")
                    .iter()
                    .map(notice)
                    .collect();
                let processed = store_retraction_notices(&mut db, &notices)?;
                out.push(json!({"op": "store", "processed": processed}));
            }
            "lookup" => {
                let doi = step.get("doi").and_then(Value::as_str);
                let pmid = step.get("pmid").and_then(Value::as_str);
                let found = lookup_retractions(&mut db, doi, pmid)?;
                out.push(json!({
                    "op": "lookup",
                    "notices": found.iter().map(RetractionNotice::to_json).collect::<Vec<_>>(),
                }));
            }
            "count" => {
                let rows = db
                    .query_raw("SELECT COUNT(*) AS n FROM retraction_notices", &[])
                    .map_err(|e| e.to_string())?;
                let n = rows
                    .first()
                    .and_then(|row| row.get("n").ok())
                    .and_then(bmlib::db::Value::as_i64)
                    .unwrap_or(0);
                out.push(json!({"op": "count", "rows": n}));
            }
            other => return Err(format!("unknown op {other:?}")),
        }
    }
    Ok(Value::Array(out))
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

        let expects_error = case.get("expects_error").is_some_and(|v| v == &json!(true));
        let got = run(case);
        if expects_error {
            // The Python raises; the port must refuse too, and *not* return an
            // empty list — "no usable identifier" is a programming error, and an
            // empty result would read as "this paper is not retracted".
            if got.is_ok() {
                failures.push(format!("  {name}: python raised, rust returned a value"));
            }
            continue;
        }

        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        match got {
            Err(e) => failures.push(format!("  {name}: the port refused: {e}")),
            Ok(got) if got != want["value"] => failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            )),
            Ok(_) => {}
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

/// **Re-importing the export is idempotent**: `record_id` is Retraction Watch's own
/// key, so a second import updates rather than duplicates, and the count stays put.
#[test]
fn a_reimport_updates_rather_than_duplicates() {
    let mut db = database();
    let mut first = RetractionNotice::new("rw-1", RetractionNature::Retraction);
    first.title = Some("Old".to_string());
    assert_eq!(
        store_retraction_notices(&mut db, &[first]).expect("stores"),
        1
    );

    let mut second = RetractionNotice::new("rw-1", RetractionNature::Retraction);
    second.title = Some("New".to_string());
    assert_eq!(
        store_retraction_notices(&mut db, &[second]).expect("stores"),
        1
    );

    let rows = db
        .query_raw("SELECT COUNT(*) AS n FROM retraction_notices", &[])
        .expect("counts");
    assert_eq!(
        rows[0].get("n").ok().and_then(bmlib::db::Value::as_i64),
        Some(1)
    );
}

/// **`processed` counts what was handed over, not the rows left behind.** Two
/// notices sharing a `record_id` in one call are two processed and one row, which
/// is what makes an import's progress report meaningful.
#[test]
fn processed_counts_the_batch_not_the_rows() {
    let mut db = database();
    let a = RetractionNotice::new("rw-same", RetractionNature::Retraction);
    let b = RetractionNotice::new("rw-same", RetractionNature::Correction);
    assert_eq!(
        store_retraction_notices(&mut db, &[a, b]).expect("stores"),
        2,
        "two notices handed over"
    );
    let rows = db
        .query_raw("SELECT COUNT(*) AS n FROM retraction_notices", &[])
        .expect("counts");
    assert_eq!(
        rows[0].get("n").ok().and_then(bmlib::db::Value::as_i64),
        Some(1)
    );
}

/// **An unusable identifier is an error, not an empty result.** A caller who passes
/// `doi="0"` — one of the export's own "no identifier here" sentinels — has a bug,
/// and an empty list would read as *"this paper is not retracted"*.
#[test]
fn an_unusable_identifier_is_an_error() {
    let mut db = database();
    assert!(
        lookup_retractions(&mut db, None, None).is_err(),
        "neither given"
    );
    for sentinel in ["0", "unavailable", "Unavailable", "   ", ""] {
        assert!(
            lookup_retractions(&mut db, Some(sentinel), None).is_err(),
            "{sentinel:?} is not an identifier"
        );
    }
    // A bare DOI prefix normalises away to nothing, which is the same error.
    assert!(lookup_retractions(&mut db, Some("https://doi.org/"), None).is_err());
    // And a usable one is not an error.
    assert!(lookup_retractions(&mut db, Some("10.1/real"), None).is_ok());
}

/// **A stored `nature` this version does not know is an error**, where the CSV's is
/// forgiving. The asymmetry is the point: the CSV's vocabulary belongs to Retraction
/// Watch, so an unknown value must cost a row rather than the import — but this
/// column was written by this library, so an unknown value means the database came
/// from a version that knows a notice type this one does not. `Other` would make
/// `is_retracted` read it as evidence of nothing and answer "not retracted".
#[test]
fn an_unknown_stored_nature_is_an_error() {
    let mut db = database();
    let mut notice = RetractionNotice::new("rw-x", RetractionNature::Retraction);
    notice.doi = Some("10.1/x".to_string());
    store_retraction_notices(&mut db, &[notice]).expect("stores");

    // Rewrite the column the way a future version would.
    db.execute_raw(
        "UPDATE retraction_notices SET nature = 'rehabilitation' WHERE record_id = 'rw-x'",
        &[],
    )
    .expect("updates");

    let error = lookup_retractions(&mut db, Some("10.1/x"), None).expect_err("refuses");
    assert!(error.contains("rehabilitation"), "{error}");
}

/// The lookup matches on **either** identifier, and normalises both, so any case or
/// prefix variant of a stored DOI finds it.
#[test]
fn the_lookup_normalises_and_matches_either_identifier() {
    let mut db = database();
    let mut notice = RetractionNotice::new("rw-1", RetractionNature::Retraction);
    notice.doi = Some("https://doi.org/10.1234/AbC".to_string());
    notice.pmid = Some("  12345  ".to_string());
    store_retraction_notices(&mut db, &[notice]).expect("stores");

    for variant in ["10.1234/abc", "https://doi.org/10.1234/ABC", "10.1234/AbC"] {
        let found = lookup_retractions(&mut db, Some(variant), None).expect("finds");
        assert_eq!(found.len(), 1, "{variant} should find the notice");
    }
    assert_eq!(
        lookup_retractions(&mut db, None, Some("12345"))
            .expect("finds")
            .len(),
        1
    );
    // Either identifier, when both are given.
    assert_eq!(
        lookup_retractions(&mut db, Some("10.1234/abc"), Some("12345"))
            .expect("finds")
            .len(),
        1
    );
}

/// **Undated notices sort last**, not first: a bare `retraction_date DESC` puts a
/// NULL before every date in SQLite, which would report the least informative notice
/// as the newest.
#[test]
fn undated_notices_sort_last() {
    let mut db = database();
    let mut dated = RetractionNotice::new("rw-2020", RetractionNature::Retraction);
    dated.doi = Some("10.1/ord".to_string());
    dated.retraction_date = Some("2020-01-01".to_string());
    let mut newer = RetractionNotice::new("rw-2024", RetractionNature::Retraction);
    newer.doi = Some("10.1/ord".to_string());
    newer.retraction_date = Some("2024-01-01".to_string());
    let mut undated = RetractionNotice::new("rw-none", RetractionNature::Retraction);
    undated.doi = Some("10.1/ord".to_string());
    store_retraction_notices(&mut db, &[dated, newer, undated]).expect("stores");

    let found = lookup_retractions(&mut db, Some("10.1/ord"), None).expect("finds");
    let order: Vec<&str> = found.iter().map(|n| n.record_id.as_str()).collect();
    assert_eq!(
        order,
        vec!["rw-2024", "rw-2020", "rw-none"],
        "newest first, undated last"
    );
}

/// The batch is chunked, so an import larger than one chunk still completes and
/// every row lands — the streaming guarantee's observable half.
#[test]
fn a_multi_chunk_batch_stores_every_row() {
    let mut db = database();
    let notices: Vec<RetractionNotice> = (0..2500)
        .map(|i| {
            let mut notice = RetractionNotice::new(format!("rw-{i}"), RetractionNature::Retraction);
            notice.doi = Some(format!("10.1/{i}"));
            notice
        })
        .collect();
    assert_eq!(
        store_retraction_notices(&mut db, &notices).expect("stores"),
        2500
    );
    let rows = db
        .query_raw("SELECT COUNT(*) AS n FROM retraction_notices", &[])
        .expect("counts");
    assert_eq!(
        rows[0].get("n").ok().and_then(bmlib::db::Value::as_i64),
        Some(2500)
    );
    // The last row is addressable, so the chunk boundary lost nothing.
    assert_eq!(
        lookup_retractions(&mut db, Some("10.1/2499"), None)
            .expect("finds")
            .len(),
        1
    );
}

/// An empty batch is a successful no-op, not an error — a day with no notices is
/// ordinary.
#[test]
fn an_empty_batch_is_a_no_op() {
    let mut db = database();
    assert_eq!(store_retraction_notices(&mut db, &[]).expect("stores"), 0);
    let rows = db
        .query_raw("SELECT COUNT(*) AS n FROM retraction_notices", &[])
        .expect("counts");
    assert_eq!(
        rows[0].get("n").ok().and_then(bmlib::db::Value::as_i64),
        Some(0)
    );
}

/// Every nature round-trips through the database, including `Other`, so a stored
/// vocabulary is stable across an import and a lookup.
#[test]
fn every_nature_round_trips() {
    let mut db = database();
    let natures = [
        RetractionNature::Retraction,
        RetractionNature::Correction,
        RetractionNature::ExpressionOfConcern,
        RetractionNature::Reinstatement,
        RetractionNature::Other,
    ];
    let notices: Vec<RetractionNotice> = natures
        .iter()
        .enumerate()
        .map(|(i, nature)| {
            let mut notice = RetractionNotice::new(format!("rw-{i}"), *nature);
            notice.doi = Some(format!("10.1/n{i}"));
            notice
        })
        .collect();
    store_retraction_notices(&mut db, &notices).expect("stores");

    for (i, nature) in natures.iter().enumerate() {
        let found = lookup_retractions(&mut db, Some(&format!("10.1/n{i}")), None).expect("finds");
        assert_eq!(found.len(), 1);
        assert_eq!(
            found[0].nature, *nature,
            "nature {nature:?} did not round-trip"
        );
    }
}
