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

use bmlib::db::{execute, fetch_scalar, open_memory, Db, Value};
use bmlib::publications::fetchers::{
    FetchError, FetchOutcome, FetchRequest, FetchSink, Fetcher, PartDisposition, Progress, Registry,
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
    /// What the walk delivers through the sink before it returns its outcome.
    /// Separate from `outcome` because the outcome carries only the count now.
    records: Vec<FetchedRecord>,
    /// The part boundaries the walk reports, in order. Empty for a source with no
    /// parts — which is every source but a partitioned PubMed day.
    boundaries: Vec<PartEvent>,
}

/// One part boundary a scripted walk reports.
enum PartEvent {
    /// A part that finished: the checkpoint it earned (`None` for one that
    /// reconciled short of its own promise) and how many records it holds.
    Finished(Option<PartCheckpoint>, usize),
    /// A part the caller's resume state already described.
    Skipped(&'static str),
}

impl ScriptedFetcher {
    fn records(n: usize) -> Self {
        ScriptedFetcher {
            outcome: std::sync::Mutex::new(Some(Ok(FetchOutcome {
                record_count: n as i64,
                status: "completed".to_string(),
                promised: Some(n as i64),
                stalled: false,
                error: None,
                note: None,
                parts: Vec::new(),
            }))),
            calls: std::sync::Mutex::new(0),
            seen_resume: std::sync::Mutex::new(None),
            records: (0..n)
                .map(|i| FetchedRecord::new(format!("Record {i}"), "pubmed"))
                .collect(),
            boundaries: Vec::new(),
        }
    }

    /// A walk over `records` that reports `boundaries` in order, then returns
    /// `outcome` — the shape a partitioned source produces.
    fn scripted(
        records: Vec<FetchedRecord>,
        boundaries: Vec<PartEvent>,
        outcome: Result<FetchOutcome, FetchError>,
    ) -> Self {
        ScriptedFetcher {
            outcome: std::sync::Mutex::new(Some(outcome)),
            calls: std::sync::Mutex::new(0),
            seen_resume: std::sync::Mutex::new(None),
            records,
            boundaries,
        }
    }

    /// A completed day whose parts were all skipped: what a resuming run sees
    /// when every part an earlier run finished is still described by a checkpoint.
    fn skipping(keys: &[&'static str]) -> Self {
        ScriptedFetcher::scripted(
            Vec::new(),
            keys.iter().map(|key| PartEvent::Skipped(key)).collect(),
            Ok(FetchOutcome::completed(0)),
        )
    }

    fn failing(message: &str) -> Self {
        ScriptedFetcher::failing_with(FetchError::Transport(message.to_string()))
    }

    /// A fetcher that fails with a chosen [`FetchError`], so the *name* the sync
    /// layer writes for each variant can be told apart. `FetchError::Transport`
    /// is the only one a built-in walker returns here — they catch a status
    /// failure into the outcome — but the layer's name table is what a
    /// third-party fetcher's `Err` reaches, and a status is Python's
    /// `HTTPStatusError` and not a transport fault (#349).
    fn failing_with(error: FetchError) -> Self {
        ScriptedFetcher {
            outcome: std::sync::Mutex::new(Some(Err(error))),
            calls: std::sync::Mutex::new(0),
            seen_resume: std::sync::Mutex::new(None),
            records: Vec::new(),
            boundaries: Vec::new(),
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
        sink: &mut dyn FetchSink,
    ) -> Result<FetchOutcome, FetchError> {
        *self.calls.lock().expect("lock") += 1;
        *self.seen_resume.lock().expect("lock") = request
            .resume
            .as_ref()
            .map(|r| r.completed_parts.keys().cloned().collect());
        // The records first, then a page event, then the parts in order: the
        // shape a real walk produces, and the shape the flush depends on.
        for record in &self.records {
            sink.record(record.clone());
        }
        sink.progress(Progress::Page {
            delivered: self.records.len() as i64,
            promised: None,
        });
        for boundary in &self.boundaries {
            match boundary {
                PartEvent::Finished(checkpoint, _records) => {
                    sink.progress(Progress::PartFinished(PartDisposition::Completed {
                        checkpoint: checkpoint.clone(),
                    }));
                }
                PartEvent::Skipped(key) => {
                    sink.progress(Progress::PartFinished(PartDisposition::Skipped {
                        part_key: (*key).to_string(),
                    }));
                }
            }
        }
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
        0,
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
        0,
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
        0,
    )
    .expect("syncs");

    assert_eq!(report.days_processed, 1);
    assert_eq!(report.errors.len(), 1);
    let error = &report.errors[0];
    assert!(error.starts_with("pubmed/2024-06-10: "), "{error}");
    // The Python exception name, so a bare transport error is not an empty tail.
    assert!(error.contains("TransportError"), "{error}");
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
        0,
    )
    .expect("syncs");
    assert_eq!(retry.calls(), 1, "a failed day is offered again");
}

/// **A status failure is named `HTTPStatusError`, not `RemoteProtocolError`.**
///
/// The sync layer writes the day's error line from a fetcher's `Err`, and the
/// name it puts there is Python's exception name — the same table
/// `biorxiv.rs`/`openalex.rs` keep for the failures their walkers catch
/// internally. A status is the source answering, not a protocol violation
/// (#349).
#[test]
fn a_status_failure_is_named_a_status_error_on_the_error_line() {
    let mut conn = db();
    let fetcher = ScriptedFetcher::failing_with(FetchError::HttpStatus {
        url: "https://api.openalex.org/works?cursor=*".to_string(),
        status: 429,
    });
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "openalex",
        &fetcher,
        &request(&["openalex"]),
        now(),
        &mut report,
        0,
    )
    .expect("syncs");

    assert_eq!(report.errors.len(), 1);
    let error = &report.errors[0];
    assert!(error.starts_with("openalex/2024-06-10: "), "{error}");
    assert_eq!(
        error,
        "openalex/2024-06-10: HTTPStatusError: \
         https://api.openalex.org/works?cursor=* returned HTTP 429"
    );
    assert!(
        !error.contains("TransportError"),
        "a status is not a protocol violation: {error}"
    );
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

/// **A failed day's row counts what it holds — once.** Python builds a
/// `FetchResult` for the failure and then writes the row from
/// `added + merged + carried`, so a day that fails after delivering records
/// reports the delivery. The port had two ways to get this wrong at once: a hard
/// `Err` threw the delivered records away (nothing was stored, and Python stores
/// them), and the row was written from the *failure's* count, which counts the
/// buffer a second time once the closing store has folded it in.
#[test]
fn a_failed_day_reports_the_records_it_holds() {
    let mut conn = db();
    let fetcher = ScriptedFetcher::scripted(
        (0..3)
            .map(|i| FetchedRecord::new(format!("Record {i}"), "pubmed"))
            .collect(),
        Vec::new(),
        Err(FetchError::Transport("connection refused".to_string())),
    );
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
        0,
    )
    .expect("the day's own failure does not escape the run");

    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM publications"),
        3,
        "the records delivered before the failure are the day's delivery"
    );
    assert_eq!(
        text(
            &mut *conn,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        )
        .as_deref(),
        Some("failed")
    );
    assert_eq!(
        count(
            &mut *conn,
            "SELECT record_count FROM download_days WHERE source = 'pubmed'",
        ),
        3,
        "and the row counts them once, not twice"
    );
    assert_eq!(
        report.errors,
        vec!["pubmed/2024-06-10: TransportError: connection refused".to_string()],
        "one line, naming the failure the fetcher reported"
    );
}

/// **The day's records and its status row commit together.** Python wraps both in
/// one `with transaction(conn)`, and the storage helpers' docs say "the caller's
/// per-day transaction" — but nothing opened one, so each record committed on its
/// own and a failure writing the status row left the day holding records it never
/// recorded. Harmless (the day is re-offered and merges) and not what the docs
/// claimed, which is why it is pinned here rather than asserted in prose.
#[test]
fn the_days_records_and_its_status_row_commit_together() {
    let mut conn = db();
    // Readable but not writable, so day selection works and the day's own row
    // cannot be written.
    execute(&mut *conn, "DROP TABLE download_days", &[]).expect("drop the table");
    execute(
        &mut *conn,
        "CREATE VIEW download_days AS SELECT \
           '' AS source, '' AS date, '' AS status, 0 AS record_count, \
           '' AS downloaded_at, '' AS last_verified_at WHERE 0",
        &[],
    )
    .expect("a read-only stand-in");

    let fetcher = ScriptedFetcher::records(3);
    let mut report = bmlib::publications::models::SyncReport::default();
    let error = bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
        0,
    )
    .expect_err("a failure outside the per-day handler is the caller's");

    assert!(
        error.to_string().contains("download_days"),
        "the failure names what could not be written: {error}"
    );
    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM publications"),
        0,
        "the records rolled back with the status row they belong to"
    );
}

/// **A finished part is checkpointed, so a day interrupted after it resumes.**
///
/// This is the half the plan's §9 row said still worked, and it did not: the port
/// collected every part boundary into a `Vec` and dropped it, so no sync ever
/// wrote a `download_day_parts` row — `record_day_part`'s only caller was a test —
/// and the carried credit below could never fire. Nothing could resume.
#[test]
fn a_finished_part_is_checkpointed_and_a_later_run_skips_it() {
    let mut conn = db();
    let first = ScriptedFetcher::scripted(
        (0..5)
            .map(|i| FetchedRecord::new(format!("Record {i}"), "pubmed"))
            .collect(),
        vec![
            PartEvent::Finished(Some(part("a", 2)), 2),
            PartEvent::Finished(Some(part("b", 3)), 3),
        ],
        Err(FetchError::Transport("connection refused".to_string())),
    );
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &first,
        &request(&["pubmed"]),
        now(),
        &mut report,
        0,
    )
    .expect("syncs");

    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
        2,
        "every finished part is checkpointed"
    );
    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM publications"),
        5,
        "and the records it delivered are stored, in its own transaction"
    );
    assert_eq!(
        text(
            &mut *conn,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        )
        .as_deref(),
        Some("failed"),
        "the walk failed, so the day is re-offered"
    );

    // A resuming run: both parts are described by checkpoints, so the fetcher
    // skips them, and their counts are credited rather than re-walked.
    let second = ScriptedFetcher::skipping(&["a", "b"]);
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &second,
        &request(&["pubmed"]),
        now(),
        &mut report,
        0,
    )
    .expect("syncs");

    assert_eq!(
        second.seen_resume.lock().expect("lock").clone(),
        Some(vec!["a".to_string(), "b".to_string()]),
        "the checkpoints the first run wrote are handed back"
    );
    assert_eq!(
        count(
            &mut *conn,
            "SELECT record_count FROM download_days WHERE source = 'pubmed'",
        ),
        5,
        "a day fetched across two runs holds both runs' records"
    );
    assert_eq!(
        text(
            &mut *conn,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        )
        .as_deref(),
        Some("completed")
    );
    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM publications"),
        5,
        "and nothing was stored twice"
    );
}

/// **A part that cannot be stored fails the day, and its records are not lost.**
///
/// The flush has nowhere to return an error to — a `FetchSink` method is
/// infallible and a walk must not be trusted to stop — so it remembers the
/// failure, keeps the part's records, and the day closes as `failed`, which is
/// Python's outcome too. The closing store is what puts those records away, and
/// it runs whether or not the fetch reported a failure.
#[test]
fn a_part_that_cannot_be_stored_fails_the_day() {
    let mut conn = db();
    // Readable but not writable: `load_day_parts` succeeds (the day is walked),
    // and the checkpoint insert fails — which is where Python's flush would raise.
    execute(&mut *conn, "DROP TABLE download_day_parts", &[]).expect("drop the table");
    execute(
        &mut *conn,
        "CREATE VIEW download_day_parts AS SELECT \
           '' AS source, '' AS date, '' AS part_scheme, '' AS part_key, \
           0 AS promised, 0 AS record_count, '' AS completed_at WHERE 0",
        &[],
    )
    .expect("a read-only stand-in");
    let fetcher = ScriptedFetcher::scripted(
        (0..2)
            .map(|i| FetchedRecord::new(format!("Record {i}"), "pubmed"))
            .collect(),
        vec![PartEvent::Finished(Some(part("a", 2)), 2)],
        Ok(FetchOutcome::completed(2)),
    );
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        now(),
        &mut report,
        0,
    )
    .expect("the day's own failure does not escape the run");

    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM publications"),
        2,
        "the records the failed part held are stored by the day's close"
    );
    assert_eq!(
        text(
            &mut *conn,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        )
        .as_deref(),
        Some("failed"),
        "a part that could not be stored fails the day"
    );
    assert_eq!(
        count(
            &mut *conn,
            "SELECT record_count FROM download_days WHERE source = 'pubmed'",
        ),
        2,
        "and the day reports what it holds"
    );
    assert_eq!(report.errors.len(), 1, "{:?}", report.errors);
    assert!(
        report.errors[0].contains("download_day_parts"),
        "the day's error names what failed: {:?}",
        report.errors
    );
}

/// A checkpoint for one part of the test's day.
fn part(key: &str, records: i64) -> PartCheckpoint {
    PartCheckpoint {
        part_scheme: "edat-range".to_string(),
        part_key: key.to_string(),
        promised: records,
        record_count: records,
    }
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
        0,
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
        0,
    )
    .expect("syncs");

    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
        0,
        "a completed day keeps no part rows: {:?}",
        report.errors
    );
}
