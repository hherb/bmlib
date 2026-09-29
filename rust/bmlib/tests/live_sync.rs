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

//! A live end-to-end `sync()`, through `builtin_registry`.
//!
//! **Nothing had ever run the whole pipeline against a real source.** Every half
//! was tested — `live_network.rs` reaches the real endpoints through the
//! transports, and the fetcher layer is driven over scripted ones — but a
//! scripted `HttpClient` is a claim about the remote written by whoever wrote the
//! parser, and the day bookkeeping is exercised only by fixtures that agree with
//! it. The handover named this the missing end-to-end check; this file is it.
//!
//! # Running it
//!
//! Gated on `BMLIB_LIVE_TESTS`, exactly as `live_network.rs` is, so the default
//! `cargo test` opens no socket:
//!
//! ```text
//! BMLIB_LIVE_TESTS=1 cargo test --test live_sync
//! ```
//!
//! It writes to an **in-memory** database that goes away with the process, so
//! there is no file to clean up and nothing to pollute. `--test-threads=1` is not
//! needed: one test, one day, one source.
//!
//! # What it asserts
//!
//! **Shape, not content.** A count would fail the first time the source revised
//! its data, so the assertions are the invariants the pipeline promises: the run
//! completes with no day errors, the day is recorded `completed` with a positive
//! record count, and every stored record carries the DOI #343 makes mandatory. A
//! source changing its *shape* — or the registry/fetcher/storage seam breaking —
//! is what it should catch.

#![allow(clippy::expect_used)]

use bmlib::db::{fetch_all, fetch_scalar, open_memory, Value};
use bmlib::http::UreqClient;
use bmlib::publications::fetchers::builtin_registry;
use bmlib::publications::schema::ensure_schema;
use bmlib::publications::sync::{build_source_configs, sync, SyncRequest};
use chrono::{NaiveDate, Utc};
use std::collections::BTreeMap;
use std::sync::Arc;

/// Whether the live tests were asked for.
fn live() -> bool {
    std::env::var("BMLIB_LIVE_TESTS").is_ok_and(|value| value == "1")
}

/// One settled bioRxiv day, end to end.
///
/// The day is **well past `BIORXIV_SETTLE_DAYS`** (90): `/pubs` fills each day in
/// weeks late, so a day inside the window is deliberately re-offered and a run
/// against one would measure the settle rule rather than the pipeline.
#[test]
fn a_live_biorxiv_day_syncs_end_to_end() {
    if !live() {
        eprintln!("skipped: set BMLIB_LIVE_TESTS=1 to run the live end-to-end sync");
        return;
    }

    let mut db = open_memory().expect("in-memory sqlite");
    ensure_schema(&mut db).expect("schema");
    let registry =
        builtin_registry(Arc::new(UreqClient::new())).expect("the built-in registry registers");
    let configs = build_source_configs(None, "test@example.com", &BTreeMap::new());
    let day = NaiveDate::from_ymd_opt(2024, 1, 15).expect("a real date");
    let request = SyncRequest {
        sources: vec!["biorxiv".to_string()],
        date_from: day,
        date_to: day,
        recheck_days: 0,
        configs: &configs,
    };

    let outcome = sync(&mut db, &registry, &request, Utc::now()).expect("the sync runs");

    assert!(
        outcome.report.errors.is_empty(),
        "a live day failed: {:?} (sources_synced={:?})",
        outcome.report.errors,
        outcome.report.sources_synced
    );
    assert_eq!(
        outcome.report.sources_synced,
        vec!["biorxiv".to_string()],
        "the registry found and ran the fetcher"
    );
    assert_eq!(outcome.report.days_processed, 1);
    assert!(
        outcome.report.records_added > 0,
        "a settled day served nothing: {:?}",
        outcome.report
    );

    // The day row says what happened, and agrees with the report. This is the
    // bookkeeping a scripted fetch never reaches: the row is the durable claim a
    // later run consults.
    let row = fetch_scalar(
        &mut db,
        "SELECT record_count FROM download_days WHERE source = 'biorxiv' AND date = ?",
        &[Value::Text("2024-01-15".to_string())],
    )
    .expect("the day row is readable");
    assert_eq!(
        row.as_ref().and_then(Value::as_i64),
        Some(outcome.report.records_added),
        "the stored day count must be the report's"
    );

    // #343 makes a DOI mandatory: a record without one fails its day, so nothing
    // without a DOI can have been kept.
    let rows = fetch_all(
        &mut db,
        "SELECT doi FROM publications WHERE first_seen_source = 'biorxiv'",
        &[],
    )
    .expect("the publications are readable");
    assert_eq!(
        rows.len() as i64,
        outcome.report.records_added,
        "every stored record is counted"
    );
    for row in rows {
        let doi = row
            .get("doi")
            .ok()
            .and_then(Value::as_str)
            .unwrap_or_default();
        assert!(
            !doi.is_empty(),
            "a stored record carries no DOI, which #343 makes mandatory: {row:?}"
        );
    }
}
