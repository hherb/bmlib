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
use bmlib::publications::models::{FetchedRecord, PartCheckpoint, SourceDescriptor};
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
        &now,
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
        &now,
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

/// A day completed after it was over is durable: the next run does not fetch it
/// again. `sync_source` stored `downloaded_at` in chrono's `Display` form, which
/// `read_aware_timestamp` cannot read, so every completed day was re-fetched.
#[test]
fn a_completed_day_is_not_fetched_again() {
    let mut conn = db();
    for (run, expected_calls) in [(0, 1), (1, 0)] {
        let fetcher = ScriptedFetcher::records(3);
        let mut report = bmlib::publications::models::SyncReport::default();
        bmlib::publications::sync::sync_source(
            &mut *conn,
            "pubmed",
            &fetcher,
            &request(&["pubmed"]),
            &now,
            &mut report,
            0,
        )
        .expect("syncs");
        assert_eq!(fetcher.calls(), expected_calls, "run {run}");
    }
    let stored = text(
        &mut *conn,
        "SELECT downloaded_at FROM download_days WHERE source = 'pubmed'",
    );
    assert_eq!(stored.as_deref(), Some("2024-06-11T12:00:00.000000+00:00"));
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
        &now,
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
        &now,
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
        &now,
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
///
/// Until round 65 this asserted `outcome.sources_synced`, a second list on a
/// `SyncOutcome` wrapper that **nothing ever wrote**: the assertion held for
/// every input, so it pinned nothing. The wrapper is gone and `sync` returns the
/// report, so this list is the one a caller reads; the other half is
/// `a_source_with_a_fetcher_is_named_in_the_source_list`.
#[test]
fn a_source_with_no_fetcher_is_not_synced() {
    let mut conn = db();
    let registry = Registry::new();
    let outcome = sync(&mut *conn, &registry, &request(&["ghost"]), &now).expect("runs");

    assert!(outcome.sources_synced.is_empty());
    assert_eq!(
        outcome.errors,
        vec!["No fetcher found for source: ghost".to_string()]
    );
    assert_eq!(outcome.days_processed, 0);
}

/// **A source whose fetcher is found is named in the report's source list** — the
/// other half of `sources_synced`, which the port had no `sync()`-level test for.
#[test]
fn a_source_with_a_fetcher_is_named_in_the_source_list() {
    let mut conn = db();
    let mut registry = Registry::new();
    registry
        .register(
            SourceDescriptor::new("scripted", "Scripted", "a scripted source"),
            Box::new(ScriptedFetcher::records(2)),
        )
        .expect("a non-resumable source with no settle period registers");

    let outcome = sync(&mut *conn, &registry, &request(&["scripted"]), &now).expect("runs");

    assert_eq!(
        outcome.sources_synced,
        vec!["scripted".to_string()],
        "a source whose loop ran is in the report"
    );
    assert!(outcome.errors.is_empty(), "{:?}", outcome.errors);
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
        &now,
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
    // Readable but refusing an insert, so day selection works, the day's lock (a
    // no-op `UPDATE`) is taken, and the day's own row cannot be written.
    execute(
        &mut *conn,
        "CREATE TRIGGER refuse_days BEFORE INSERT ON download_days \
         BEGIN SELECT RAISE(ABORT, 'download_days refuses this row'); END",
        &[],
    )
    .expect("a refusing trigger");

    let fetcher = ScriptedFetcher::records(3);
    let mut report = bmlib::publications::models::SyncReport::default();
    let error = bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        &now,
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
        &now,
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
        &now,
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
        &now,
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
        &now,
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
        &now,
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

/// **The three `ValueError`-shaped fetcher failures are named `ValueError`.**
///
/// Python's fetchers raise a plain `ValueError` for a body that is not the shape
/// the source documents, for a configuration that is incomplete or wrong, and for
/// a stored checkpoint that cannot be read; `sync` stores
/// `f"{type(exc).__name__}: {exc}"`, so all three reach a caller as `ValueError`.
/// `error_type_name` maps them so — and until this test **none of the three was
/// asserted anywhere**: the only names the suite pinned were `TransportError` and
/// `HTTPStatusError`, so a mutant could rename any of these and stay green.
#[test]
fn the_value_error_shaped_failures_are_named_value_error() {
    for (error, message) in [
        (
            FetchError::Malformed("expected an object".to_string()),
            "expected an object",
        ),
        (
            FetchError::Config("no email for unpaywall".to_string()),
            "no email for unpaywall",
        ),
        (
            FetchError::ResumeUnreadable("checkpoint is not JSON".to_string()),
            "checkpoint is not JSON",
        ),
    ] {
        let mut conn = db();
        let fetcher = ScriptedFetcher::failing_with(error);
        let mut report = bmlib::publications::models::SyncReport::default();
        bmlib::publications::sync::sync_source(
            &mut *conn,
            "pubmed",
            &fetcher,
            &request(&["pubmed"]),
            &now,
            &mut report,
            0,
        )
        .expect("syncs");

        assert_eq!(report.errors.len(), 1, "{message}");
        assert_eq!(
            report.errors[0],
            format!("pubmed/2024-06-10: ValueError: {message}"),
            "the name is Python's exception class"
        );
    }
}

// ---------------------------------------------------------------------------
// The entry, the day's lock and the peak (#607, #444)
// ---------------------------------------------------------------------------

/// A `download_days` row as an earlier run might have left it.
fn seed_day(db: &mut dyn Db, status: &str, record_count: i64, peak: Value) {
    execute(
        db,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at, \
         last_verified_at, peak_count) VALUES ('pubmed', '2024-06-10', ?, ?, \
         '2024-06-11T12:30:00+00:00', '2024-06-11T12:30:00+00:00', ?)",
        &[
            Value::Text(status.to_string()),
            Value::Int(record_count),
            peak,
        ],
    )
    .expect("seed the day");
}

fn stored_day(db: &mut dyn Db) -> (Option<String>, i64, i64) {
    (
        text(
            db,
            "SELECT status FROM download_days WHERE source = 'pubmed'",
        ),
        count(
            db,
            "SELECT record_count FROM download_days WHERE source = 'pubmed'",
        ),
        count(
            db,
            "SELECT peak_count FROM download_days WHERE source = 'pubmed'",
        ),
    )
}

/// **`sync()` refuses a window it cannot walk at its entry** (#607), as Python's
/// does, rather than leaving the check to its caller: nothing is fetched and no
/// row is written.
#[test]
fn sync_refuses_a_window_it_cannot_walk_before_fetching() {
    let mut conn = db();
    let mut registry = Registry::new();
    registry
        .register(
            SourceDescriptor::new("scripted", "Scripted", "a stand-in"),
            Box::new(ScriptedFetcher::records(2)),
        )
        .expect("register");
    let mut bad = request(&["scripted"]);
    bad.recheck_days = -1;

    let error = sync(&mut *conn, &registry, &bad, &now).expect_err("refused");

    assert!(
        matches!(
            error,
            bmlib::publications::sync::SyncError::Window(
                bmlib::publications::sync::WindowError::NegativeRecheck { got: -1 }
            )
        ),
        "{error:?}"
    );
    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM download_days"), 0);
    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 0);
}

/// **A day's row carries its peak**, which is what the next fetch is held to; a
/// row the port wrote before #444 carried none.
#[test]
fn a_fetched_day_records_its_peak() {
    let mut conn = db();
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &ScriptedFetcher::records(3),
        &request(&["pubmed"]),
        &now,
        &mut report,
        0,
    )
    .expect("runs");

    assert_eq!(stored_day(&mut *conn), (Some("completed".into()), 3, 3));
}

/// **A completed re-fetch storing under half the peak of an unfinished day is
/// recorded failed** (#346 through `sync_source`), the peak kept so the next
/// fetch is held to it too — the settling preprint day `/pubs`' quiet-day body
/// would otherwise make durable at 0.
///
/// The run is half a day after the seeded fetch, which was itself after the day
/// was over, so the row is unfinished only because the source settles over 90
/// days: a close that dropped `settle_days` would read it final and complete.
#[test]
fn a_re_fetch_below_half_an_unfinished_days_peak_is_refused() {
    let mut conn = db();
    seed_day(&mut *conn, "completed", 105, Value::Int(105));
    let later = || now() + chrono::Duration::hours(12);
    let mut report = bmlib::publications::models::SyncReport::default();
    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &ScriptedFetcher::records(0),
        &request(&["pubmed"]),
        &later,
        &mut report,
        90,
    )
    .expect("runs");

    assert_eq!(stored_day(&mut *conn), (Some("failed".into()), 0, 105));
    assert_eq!(report.errors.len(), 1, "{:?}", report.errors);
    assert!(
        report.errors[0].contains("an earlier fetch of this unfinished day stored 105"),
        "{:?}",
        report.errors
    );
}

/// **A day whose lock cannot be had costs that day a line, not the run** (#447's
/// review): the lock is the closing block's first statement, so nothing is
/// written and the row stays as an earlier run left it.
#[test]
fn a_day_lock_that_cannot_be_had_costs_the_day_a_line() {
    let mut conn = db();
    seed_day(&mut *conn, "failed", 0, Value::Int(0));
    // The lock is a no-op UPDATE of the day's row on SQLite; refusing it stands
    // in for a busy timeout.
    execute(
        &mut *conn,
        "CREATE TRIGGER busy BEFORE UPDATE ON download_days \
         BEGIN SELECT RAISE(ABORT, 'database is locked'); END",
        &[],
    )
    .expect("a refusing trigger");
    let mut report = bmlib::publications::models::SyncReport::default();

    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &ScriptedFetcher::records(2),
        &request(&["pubmed"]),
        &now,
        &mut report,
        0,
    )
    .expect("the run goes on");

    assert_eq!(report.days_processed, 1);
    assert_eq!(report.errors.len(), 1, "{:?}", report.errors);
    assert!(
        report.errors[0].starts_with("pubmed/2024-06-10: the day could not be locked")
            && report.errors[0].contains("database is locked")
            && report.errors[0].contains("its 2 unflushed record(s) were not stored"),
        "{:?}",
        report.errors
    );
    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 0);
    assert_eq!(stored_day(&mut *conn), (Some("failed".into()), 0, 0));
}

/// **A part's flush takes the day's lock too**, so a part that cannot have it is
/// not stored and fails the day, as a part that cannot be stored does.
#[test]
fn a_parts_flush_takes_the_days_lock() {
    let mut conn = db();
    seed_day(&mut *conn, "failed", 0, Value::Int(0));
    execute(
        &mut *conn,
        "CREATE TRIGGER busy BEFORE UPDATE ON download_days \
         BEGIN SELECT RAISE(ABORT, 'database is locked'); END",
        &[],
    )
    .expect("a refusing trigger");
    let fetcher = ScriptedFetcher::scripted(
        vec![FetchedRecord::new("Record", "pubmed")],
        vec![PartEvent::Finished(Some(part("a", 1)), 1)],
        Ok(FetchOutcome::completed(1)),
    );
    let mut report = bmlib::publications::models::SyncReport::default();

    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        &now,
        &mut report,
        0,
    )
    .expect("the run goes on");

    assert_eq!(
        count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
        0
    );
    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 0);
    // Two lines: the flush's own failure, which fails the day, and then the
    // close's, which the same trigger refuses. A flush that took no lock would
    // store the part and leave only the second.
    assert_eq!(report.errors.len(), 2, "{:?}", report.errors);
    assert!(
        report.errors[0].starts_with("pubmed/2024-06-10: ")
            && report.errors[0].contains("database is locked")
            && !report.errors[0].contains("could not be locked"),
        "{:?}",
        report.errors
    );
    assert!(
        report.errors[1].starts_with("pubmed/2024-06-10: the day could not be locked")
            && report.errors[1].contains("its 1 unflushed record(s) were not stored"),
        "{:?}",
        report.errors
    );
}

/// **A day's close judges the stored row at the time of the close** (#444's
/// review). Another sync may complete the day during this one's fetch; read
/// against this run's start its fresh timestamp is a fetch in the future, the row
/// looks unfinished, and a lower count would overwrite the completed day as
/// failed. Python reads the wall clock at the close.
#[test]
fn a_days_close_judges_the_stored_row_when_it_closes() {
    let mut conn = db();
    // Completed, peak 100, by a sync that finished 20 minutes into this run.
    execute(
        &mut *conn,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at, \
         last_verified_at, peak_count) VALUES ('pubmed', '2024-06-10', 'completed', 100, \
         '2024-06-20T00:20:00+00:00', '2024-06-20T00:20:00+00:00', 100)",
        &[],
    )
    .expect("seed");
    let start = chrono::DateTime::parse_from_rfc3339("2024-06-20T00:00:00Z")
        .expect("instant")
        .with_timezone(&Utc);
    let calls = std::cell::Cell::new(0);
    // The run's start first, then 30 minutes later for every later reading.
    let clock = || {
        calls.set(calls.get() + 1);
        if calls.get() == 1 {
            start
        } else {
            start + chrono::Duration::minutes(30)
        }
    };
    let mut report = bmlib::publications::models::SyncReport::default();

    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &ScriptedFetcher::records(10),
        &request(&["pubmed"]),
        &clock,
        &mut report,
        0,
    )
    .expect("runs");

    assert_eq!(
        report.days_processed, 1,
        "the row read as a future fetch at the start"
    );
    assert!(report.errors.is_empty(), "{:?}", report.errors);
    assert_eq!(
        report.notes,
        [
            "pubmed/2024-06-10: stored 10 record(s) where an earlier fetch stored 100; recording \
          the lower count, the day being already final"
        ]
    );
    assert_eq!(
        text(&mut *conn, "SELECT status FROM download_days"),
        Some("completed".into())
    );
    // Stamped when it was written, as Python's `_upsert_download_day` stamps it,
    // and not with the run's start.
    assert_eq!(
        text(&mut *conn, "SELECT downloaded_at FROM download_days"),
        Some("2024-06-20T00:30:00.000000+00:00".into())
    );
}

/// **`sync_source` refuses a window it cannot walk**, as `sync()` does: it is
/// public, and day selection does date arithmetic on `recheck_days`.
#[test]
fn sync_source_refuses_a_window_it_cannot_walk() {
    let mut conn = db();
    let fetcher = ScriptedFetcher::records(2);
    let mut bad = request(&["pubmed"]);
    bad.recheck_days = i64::MAX;
    let mut report = bmlib::publications::models::SyncReport::default();

    let error = bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &bad,
        &now,
        &mut report,
        0,
    )
    .expect_err("refused");

    assert!(
        matches!(error, bmlib::publications::sync::SyncError::Window(_)),
        "{error:?}"
    );
    assert_eq!(fetcher.calls(), 0);
    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM download_days"), 0);
}

/// **A day whose checkpoints cannot be read is failed without fetching**, through
/// the same close as any failed fetch: the row records `failed`, its peak is kept,
/// and the line names the table. Fetching from scratch and recording `completed`
/// would record success over an unknown.
#[test]
fn a_day_whose_checkpoints_cannot_be_read_fails_without_fetching() {
    let mut conn = db();
    seed_day(&mut *conn, "failed", 7, Value::Int(7));
    execute(
        &mut *conn,
        "INSERT INTO download_day_parts (source, date, part_scheme, part_key, promised, \
         record_count, completed_at) VALUES ('pubmed', '2024-06-10', 'edat-range', 'a', \
         'many', 3, '2024-06-11T12:30:00+00:00')",
        &[],
    )
    .expect("an unreadable checkpoint");
    let fetcher = ScriptedFetcher::records(3);
    let mut report = bmlib::publications::models::SyncReport::default();

    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        &now,
        &mut report,
        0,
    )
    .expect("the day's own failure does not escape the run");

    assert_eq!(fetcher.calls(), 0, "nothing is fetched over an unknown");
    assert_eq!(stored_day(&mut *conn), (Some("failed".into()), 0, 7));
    assert_eq!(report.days_processed, 1);
    assert_eq!(report.errors.len(), 1, "{:?}", report.errors);
    assert!(
        report.errors[0].starts_with(
            "pubmed/2024-06-10: could not read download_day_parts for pubmed/2024-06-10: "
        ),
        "{:?}",
        report.errors
    );
}

/// **A close that cannot be locked still reports the fetch's own failure and the
/// records its parts already stored**: the lock line is added to what the day
/// did, not substituted for it. The trigger lets the part's flush lock the day
/// and refuses the close's.
#[test]
fn a_close_that_cannot_be_locked_keeps_the_fetch_error_and_the_flushed_totals() {
    let mut conn = db();
    seed_day(&mut *conn, "failed", 0, Value::Int(0));
    // A lock is a no-op UPDATE of the day's row; each one is counted, and the
    // second is refused. The refused one's insert goes with it, so the count is
    // of the locks that were had.
    execute(&mut *conn, "CREATE TABLE locks_had (n INTEGER)", &[]).expect("a counter");
    execute(
        &mut *conn,
        "CREATE TRIGGER busy BEFORE UPDATE ON download_days BEGIN \
           SELECT RAISE(ABORT, 'database is locked') \
             WHERE (SELECT COUNT(*) FROM locks_had) >= 1; \
           INSERT INTO locks_had VALUES (1); \
         END",
        &[],
    )
    .expect("a trigger refusing the second lock");
    let fetcher = ScriptedFetcher::scripted(
        (0..2)
            .map(|i| FetchedRecord::new(format!("Record {i}"), "pubmed"))
            .collect(),
        vec![PartEvent::Finished(Some(part("a", 2)), 2)],
        Err(FetchError::Transport("connection reset".to_string())),
    );
    // A minute later for every reading, so the checkpoint's stamp says which
    // reading it was.
    let readings = std::cell::Cell::new(0);
    let clock = || {
        readings.set(readings.get() + 1);
        now() + chrono::Duration::minutes(readings.get() - 1)
    };
    let mut report = bmlib::publications::models::SyncReport::default();

    bmlib::publications::sync::sync_source(
        &mut *conn,
        "pubmed",
        &fetcher,
        &request(&["pubmed"]),
        &clock,
        &mut report,
        0,
    )
    .expect("the run goes on");

    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM locks_had"), 1);
    assert_eq!(report.days_processed, 1);
    assert_eq!(report.records_added, 2, "the part's records were stored");
    assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 2);
    assert_eq!(report.errors.len(), 2, "{:?}", report.errors);
    assert_eq!(
        report.errors[0],
        "pubmed/2024-06-10: TransportError: connection reset"
    );
    assert!(
        report.errors[1].starts_with("pubmed/2024-06-10: the day could not be locked")
            && report.errors[1].contains("its 0 unflushed record(s) were not stored"),
        "{:?}",
        report.errors
    );
    assert_eq!(stored_day(&mut *conn), (Some("failed".into()), 0, 0));
    // The checkpoint was stamped when the part was written (the second reading),
    // as Python's `_record_day_part` stamps it.
    assert_eq!(
        text(&mut *conn, "SELECT completed_at FROM download_day_parts"),
        Some("2024-06-11T12:01:00.000000+00:00".into())
    );
}
