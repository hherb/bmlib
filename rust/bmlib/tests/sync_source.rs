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

//! `sync_source` over a real database and a scripted fetcher.
//!
//! The rule functions are pinned by their own oracle; what this exercises is the
//! loop that composes them — a day fetched, stored, and recorded, with the
//! carried credit and the failure count landing in the row a caller reads.

use bmlib::db::{fetch_scalar, open_memory, Db, Value};
use bmlib::publications::fetchers::{
    FetchError, FetchOutcome, FetchRequest, Fetcher, Progress, Registry,
};
use bmlib::publications::models::{FetchedRecord, PartCheckpoint};
use bmlib::publications::schema::ensure_schema;
use bmlib::publications::sync::{sync, SyncRequest};
use chrono::{NaiveDate, Utc};

fn db() -> Box<dyn Db> {
    let mut conn = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut conn).expect("schema");
    Box::new(conn)
}

fn count(db: &mut dyn Db, sql: &str) -> i64 {
    match fetch_scalar(db, sql, &[]).expect("scalar") {
        Some(Value::Int(i)) => i,
        other => panic!("expected an integer count, got {other:?}"),
    }
}

fn text(db: &mut dyn Db, sql: &str) -> Option<String> {
    match fetch_scalar(db, sql, &[]).expect("scalar") {
        Some(Value::Text(s)) => Some(s),
        Some(Value::Null) | None => None,
        other => panic!("expected text, got {other:?}"),
    }
}

fn day() -> NaiveDate {
    NaiveDate::from_ymd_opt(2024, 6, 10).expect("date")
}

fn now() -> chrono::DateTime<Utc> {
    chrono::DateTime::parse_from_rfc3339("2024-06-11T12:00:00Z")
        .expect("instant")
        .with_timezone(&Utc)
}

/// A fetcher that returns a scripted outcome and records that it was called.
struct ScriptedFetcher {
    outcome: std::sync::Mutex<Option<Result<FetchOutcome, FetchError>>>,
    calls: std::sync::Mutex<usize>,
    /// The resume state the caller handed it, for the resume test.
    seen_resume: std::sync::Mutex<Option<Vec<String>>>,
}

impl ScriptedFetcher {
    fn records(n: usize) -> Self {
        ScriptedFetcher {
            outcome: std::sync::Mutex::new(Some(Ok(FetchOutcome {
                records: (0..n)
                    .map(|i| FetchedRecord::new(format!("Record {i}"), "pubmed"))
                    .collect(),
                status: "completed".to_string(),
                promised: Some(n as i64),
                stalled: false,
                error: None,
                note: None,
                parts: Vec::new(),
            }))),
            calls: std::sync::Mutex::new(0),
            seen_resume: std::sync::Mutex::new(None),
        }
    }

    fn failing(message: &str) -> Self {
        ScriptedFetcher {
            outcome: std::sync::Mutex::new(Some(Err(FetchError::Transport(message.to_string())))),
            calls: std::sync::Mutex::new(0),
            seen_resume: std::sync::Mutex::new(None),
        }
    }

    fn calls(&self) -> usize {
        *self.calls.lock().expect("lock")
    }
}

impl Fetcher for ScriptedFetcher {
    fn fetch(
        &self,
        request: &FetchRequest,
        on_progress: &mut dyn FnMut(Progress),
    ) -> Result<FetchOutcome, FetchError> {
        *self.calls.lock().expect("lock") += 1;
        *self.seen_resume.lock().expect("lock") = request
            .resume
            .as_ref()
            .map(|r| r.completed_parts.keys().cloned().collect());
        on_progress(Progress::Page {
            delivered: 1,
            promised: None,
        });
        self.outcome
            .lock()
            .expect("lock")
            .take()
            .expect("fetched twice")
    }
}

fn request(sources: &[&str]) -> SyncRequest<'static> {
    SyncRequest {
        sources: sources.iter().map(|s| (*s).to_string()).collect(),
        date_from: day(),
        date_to: day(),
        recheck_days: 0,
        configs: Box::leak(Box::new(std::collections::BTreeMap::new())),
    }
}

/// A source with no day needing a fetch is **synced** and its fetcher is never
/// called — the days already complete are not re-fetched.
#[test]
fn a_source_with_nothing_to_fetch_is_synced_without_fetching() {
    let mut conn = db();
    // The day is already complete from an earlier run.
    bmlib::publications::sync::upsert_download_day(
        &mut *conn,
        "pubmed",
        day(),
        "completed",
        5,
        &now().to_rfc3339(),
    )
    .expect("seed");

    let fetcher = ScriptedFetcher::records(5);
    let mut report = bmlib::publications::models::SyncReport::default();
    let mut req = request(&["pubmed"]);
    req.date_from = day();
    req.date_to = day();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &req,
        now(),
        &mut report,
    )
    .expect("syncs");

    assert_eq!(report.sources_synced, vec!["pubmed".to_string()]);
    assert_eq!(fetcher.calls(), 0, "a completed day is not re-fetched");
    assert!(report.errors.is_empty());
}

/// A day that needs fetching is fetched, its records stored, and the day
/// recorded `completed` with the count it holds.
#[test]
fn a_fetched_day_is_stored_and_recorded() {
    let mut conn = db();
    let fetcher = ScriptedFetcher::records(3);
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
    )
    .expect("syncs");

    assert_eq!(fetcher.calls(), 1);
    assert_eq!(report.days_processed, 1);
    assert_eq!(report.records_added, 3);
    assert!(report.errors.is_empty(), "{:?}", report.errors);

    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 3);
    assert_eq!(
        text(
            &mut *conn,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        )
        .as_deref(),
        Some("completed")
    );
    assert_eq!(
        count(
            &mut *conn,
            "SELECT record_count FROM download_days WHERE source = 'pubmed'",
        ),
        3,
        "the day records what it holds"
    );
}

/// A fetcher that **fails** records the day `failed` and contributes an error
/// line, and the run still returns a report — which is the point of the per-day
/// transaction.
#[test]
fn a_failed_fetch_records_the_day_and_the_run_continues() {
    let mut conn = db();
    let fetcher = ScriptedFetcher::failing("timed out");
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
    )
    .expect("syncs");

    assert_eq!(report.days_processed, 1);
    assert_eq!(report.errors.len(), 1);
    let error = &report.errors[0];
    assert!(error.starts_with("pubmed/2024-06-10: "), "{error}");
    // The Python exception name, so a bare transport error is not an empty tail.
    assert!(error.contains("RemoteProtocolError"), "{error}");
    assert_eq!(
        text(
            &mut *conn,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        )
        .as_deref(),
        Some("failed")
    );
    // A failed day is re-offered, which is the whole reason it is recorded so.
    let mut again = bmlib::publications::models::SyncReport::default();
    let retry = ScriptedFetcher::failing("timed out");
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &retry,
        &request(&["pubmed"]),
        now(),
        &mut again,
    )
    .expect("syncs");
    assert_eq!(retry.calls(), 1, "a failed day is offered again");
}

/// **A source with no fetcher is absent from `sources_synced`** — different from
/// one whose days all failed — and contributes its own error line.
#[test]
fn a_source_with_no_fetcher_is_not_synced() {
    let mut conn = db();
    let registry = Registry::new();
    let outcome = sync(&mut *conn, &registry, &request(&["ghost"]), now()).expect("runs");

    assert!(outcome.sources_synced.is_empty());
    assert_eq!(
        outcome.report.errors,
        vec!["No fetcher found for source: ghost".to_string()]
    );
    assert_eq!(outcome.report.days_processed, 0);
}

/// A checkpoint for the day is **handed to the fetcher** as resume state, which
/// is what lets a resumable source skip the parts an earlier run finished.
#[test]
fn a_days_checkpoints_are_handed_to_the_fetcher() {
    let mut conn = db();
    let part_key = bmlib::publications::fetchers::pubmed::part_key(day(), day());
    bmlib::publications::sync::record_day_part(
        &mut *conn,
        "pubmed",
        day(),
        &PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key: part_key.clone(),
            promised: 9_000,
            record_count: 9_000,
        },
        &now().to_rfc3339(),
    )
    .expect("seed a part");

    let fetcher = ScriptedFetcher::records(1);
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
    )
    .expect("syncs");

    assert_eq!(
        fetcher.seen_resume.lock().expect("lock").clone(),
        Some(vec![part_key]),
        "the day's checkpoints must reach the fetcher"
    );
}

/// A **completed** day's part rows are cleared, so a later run does not carry
/// checkpoints for a day it will never fetch again.
#[test]
fn a_completed_day_clears_its_part_rows() {
    let mut conn = db();
    let part_key = bmlib::publications::fetchers::pubmed::part_key(day(), day());
    bmlib::publications::sync::record_day_part(
        &mut *conn,
        "pubmed",
        day(),
        &PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key,
            promised: 9_000,
            record_count: 9_000,
        },
        &now().to_rfc3339(),
    )
    .expect("seed a part");
    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
        1
    );

    let fetcher = ScriptedFetcher::records(1);
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
    )
    .expect("syncs");

    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
        0,
        "a completed day keeps no part rows: {:?}",
        report.errors
    );
}
