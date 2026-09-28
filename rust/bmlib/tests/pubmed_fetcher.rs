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

//! `PubMedFetcher` — `fetch_pubmed` behind the `Fetcher` trait.
//!
//! The walk, the ladder and the transport each have their own tests; what is
//! pinned here is the **layer between them and `sync`**, which nothing tested
//! before the fetcher existed:
//!
//! * every record reaches the caller's [`FetchSink`] as it is read, and the count
//!   the outcome reports is what the sink received;
//! * every part boundary reaches it too, as `Progress::PartFinished` — the event
//!   `sync` drains its buffer at and checkpoints on. A boundary that never arrives
//!   is a whole day held in memory and no checkpoint written, which is what the
//!   port did before round 52;
//! * the day's resume state reaches the walk, and a day whose parts are all
//!   checkpointed and empty fails rather than reading as a quiet day.

mod common;

use std::collections::BTreeMap;
use std::sync::Arc;

use bmlib::publications::fetchers::pubmed::{part_key, PubMedFetcher, EFETCH_PAGE_SIZE};
use bmlib::publications::fetchers::{
    FetchRequest, Fetcher, PartDisposition, Progress, ResumeState,
};
use bmlib::publications::models::PartCheckpoint;
use chrono::NaiveDate;
use common::pubmed_sim::ScriptedEutils;
use common::sink::RecordingSink;

/// The day the fake's terms are keyed for.
const DAY_TERM: &str = "(\"2024/06/10\"[Date - Publication])";

fn day() -> NaiveDate {
    NaiveDate::from_ymd_opt(2024, 6, 10).expect("date")
}

/// A transport whose *day* search answers `count` with a history session.
///
/// The day's own search is keyed by the whole day term rather than an EDAT range,
/// because that is the term `fetch_pubmed` sends: a map keyed by ranges answers it
/// with zero, which reads as a quiet day and skips whatever the test meant to walk.
fn day_transport(count: i64) -> ScriptedEutils {
    let transport = ScriptedEutils::empty();
    *transport.session_counts.lock().expect("lock") =
        BTreeMap::from([(DAY_TERM.to_string(), count)]);
    transport
}

fn request() -> FetchRequest {
    FetchRequest::new(day())
}

/// A day the source has nothing for is **completed with nothing delivered**, and
/// the sink hears about no boundary: a quiet day has no parts.
#[test]
fn a_quiet_day_is_completed_with_nothing_delivered() {
    let fetcher = PubMedFetcher::new(Arc::new(ScriptedEutils::empty()));
    let mut sink = RecordingSink::new();
    let outcome = fetcher
        .fetch(&request(), &mut sink)
        .expect("a fetcher reports a failed day in the outcome, not as an error");

    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.record_count, 0);
    assert!(outcome.error.is_none());
    assert!(sink.records.is_empty());
    assert!(
        sink.progress.is_empty(),
        "a quiet day walks no page and finishes no part: {:?}",
        sink.progress
    );
}

/// A session day delivers its records through the sink, and the count the outcome
/// reports is exactly what the sink received.
#[test]
fn a_session_day_delivers_its_records_through_the_sink() {
    let mut transport = day_transport(2);
    transport.set_pages(2);
    let fetcher = PubMedFetcher::new(Arc::new(transport));
    let mut sink = RecordingSink::new();
    let outcome = fetcher.fetch(&request(), &mut sink).expect("fetches");

    assert_eq!(outcome.status, "completed", "{:?}", outcome.error);
    assert_eq!(outcome.record_count, 2);
    assert_eq!(outcome.record_count, sink.records.len() as i64);
    assert_eq!(
        sink.records
            .iter()
            .map(|record| record.title.clone())
            .collect::<Vec<_>>(),
        vec!["r0".to_string(), "r1".to_string()]
    );
    assert_eq!(
        sink.pages(),
        vec![(2, Some(2))],
        "one page event, with the day's own count as the promise"
    );
    assert!(
        sink.progress
            .iter()
            .all(|event| !matches!(event, Progress::PartFinished(_))),
        "a day under the cap has one session and no parts"
    );
}

/// **A failed day is an outcome, not an `Err`** — `sync` reads the status and the
/// message, and the message must be Python's, name included (#354).
#[test]
fn a_failed_day_carries_pythons_message() {
    let transport = day_transport(2);
    transport
        .session_failures
        .lock()
        .expect("lock")
        .insert(DAY_TERM.to_string(), "ReadTimeout: timed out".to_string());
    let fetcher = PubMedFetcher::new(Arc::new(transport));
    let mut sink = RecordingSink::new();
    let outcome = fetcher.fetch(&request(), &mut sink).expect("fetches");

    assert_eq!(outcome.status, "failed");
    assert_eq!(outcome.error.as_deref(), Some("ReadTimeout: timed out"));
    assert_eq!(outcome.record_count, 0);
    assert!(sink.records.is_empty());
}

/// **The day's resume state reaches the walk**, which is what lets a partitioned
/// day skip the parts an earlier run finished. Observable as the refusal it
/// produces: a day whose parts are all checkpointed and whose search now answers
/// nothing is *not* a quiet day — the records those parts attest to are missing.
#[test]
fn a_days_resume_state_reaches_the_walk() {
    let checkpoints = BTreeMap::from([(
        part_key(day(), day()),
        PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key: part_key(day(), day()),
            promised: 9_000,
            record_count: 9_000,
        },
    )]);
    let mut request = request();
    request.resume = Some(ResumeState {
        completed_parts: checkpoints,
    });

    // The day's own search now answers nothing at all.
    let fetcher = PubMedFetcher::new(Arc::new(ScriptedEutils::empty()));
    let mut sink = RecordingSink::new();
    let outcome = fetcher.fetch(&request, &mut sink).expect("fetches");

    assert_eq!(
        outcome.status, "failed",
        "a checkpointed day that now reports nothing is not quiet"
    );
    let error = outcome.error.expect("a failed day names its failure");
    assert!(
        error.contains("checkpointed") && error.contains("9000"),
        "the message counts the checkpoints' records: {error}"
    );
    assert_eq!(outcome.record_count, 0);
    assert!(sink.records.is_empty());
}

/// A day whose records spread over a month of Entrez dates, so it **must**
/// partition: 30 days at 1,000 records is 30,000, and one session serves 9,999.
///
/// A real day looks like this — a publication date's EDATs are deposit dates — and
/// modelling it this way is what keeps these tests off the ladder's arithmetic:
/// nothing here scripts a split point, so a change to how the ladder divides a
/// range moves the parts without breaking the test.
fn dense_day() -> Arc<ScriptedEutils> {
    let mut transport = ScriptedEutils::empty();
    transport.dense = Some((
        NaiveDate::from_ymd_opt(2024, 6, 1).expect("date"),
        NaiveDate::from_ymd_opt(2024, 6, 30).expect("date"),
        1_000,
    ));
    transport.set_pages(EFETCH_PAGE_SIZE as i64);
    Arc::new(transport)
}

/// The checkpoints a walk reported, in order.
fn completed_checkpoints(sink: &RecordingSink) -> Vec<PartCheckpoint> {
    sink.progress
        .iter()
        .filter_map(|event| match event {
            Progress::PartFinished(PartDisposition::Completed { checkpoint }) => checkpoint.clone(),
            Progress::PartFinished(PartDisposition::Skipped { .. }) | Progress::Page { .. } => None,
        })
        .collect()
}

/// The part keys a walk reported as skipped, in order.
fn skipped_keys(sink: &RecordingSink) -> Vec<String> {
    sink.progress
        .iter()
        .filter_map(|event| match event {
            Progress::PartFinished(PartDisposition::Skipped { part_key }) => Some(part_key.clone()),
            Progress::PartFinished(PartDisposition::Completed { .. }) | Progress::Page { .. } => {
                None
            }
        })
        .collect()
}

/// **Every part boundary reaches the sink**, each with the checkpoint the walk
/// earned — the events `sync` drains its buffer at and checkpoints on. Before round
/// 52 the port collected these and dropped them, so a partitioned day was held
/// whole in memory and could not resume.
#[test]
fn a_partitioned_day_reports_its_boundaries_to_the_sink() {
    let transport = dense_day();
    let fetcher = PubMedFetcher::new(transport);
    let mut sink = RecordingSink::new();
    let outcome = fetcher.fetch(&request(), &mut sink).expect("fetches");

    assert_eq!(outcome.status, "completed", "{:?}", outcome.error);
    assert_eq!(outcome.record_count, sink.records.len() as i64);
    let checkpoints = completed_checkpoints(&sink);
    assert!(
        checkpoints.len() >= 2,
        "a day over the cap partitions into parts: {checkpoints:?}"
    );
    assert_eq!(
        checkpoints
            .iter()
            .map(|checkpoint| checkpoint.record_count)
            .sum::<i64>(),
        outcome.record_count,
        "the parts account for the whole day: {checkpoints:?}"
    );
    assert!(
        skipped_keys(&sink).is_empty(),
        "nothing was checkpointed before this run"
    );
    let keys: Vec<&str> = checkpoints
        .iter()
        .map(|checkpoint| checkpoint.part_key.as_str())
        .collect();
    let mut unique = keys.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), keys.len(), "one boundary per part: {keys:?}");
}

/// **And the next run skips exactly the parts the first one reported.** This is the
/// round trip `sync`'s resume depends on: what the fetcher hands over as a
/// checkpoint is what it accepts back as resume state, key for key. Nothing here
/// knows how the ladder divides the day — the checkpoints come out of the first
/// walk and go straight back in.
#[test]
fn a_later_run_skips_the_parts_the_first_one_reported() {
    let transport = dense_day();
    let fetcher = PubMedFetcher::new(transport);
    let mut sink = RecordingSink::new();
    let first = fetcher.fetch(&request(), &mut sink).expect("fetches");
    assert_eq!(first.status, "completed", "{:?}", first.error);
    let checkpoints = completed_checkpoints(&sink);
    assert!(!checkpoints.is_empty());

    let resumed = BTreeMap::from_iter(
        checkpoints
            .iter()
            .map(|checkpoint| (checkpoint.part_key.clone(), checkpoint.clone())),
    );
    let mut request = request();
    request.resume = Some(ResumeState {
        completed_parts: resumed.clone(),
    });

    let mut sink = RecordingSink::new();
    let second = fetcher.fetch(&request, &mut sink).expect("fetches");

    assert_eq!(second.status, "completed", "{:?}", second.error);
    assert_eq!(
        second.record_count, 0,
        "a skipped part's records were stored by the run that walked it"
    );
    assert!(sink.records.is_empty(), "nothing was walked");
    assert!(sink.pages().is_empty(), "and no page was asked for");
    let skipped = skipped_keys(&sink);
    assert_eq!(
        skipped.len(),
        resumed.len(),
        "every part the first run reported is skipped: {skipped:?}"
    );
    for key in &skipped {
        assert!(resumed.contains_key(key), "{key} was not a reported part");
    }
}
