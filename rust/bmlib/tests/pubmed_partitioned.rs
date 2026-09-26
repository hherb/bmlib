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

//! The partitioned-fetch loop, end to end over a scripted transport.
//!
//! This is where the parts composed in earlier rounds finally run together:
//! the EDAT ladder, the session walk, the skip/refetch decision and the
//! checkpoint condition. A scripted `Eutils` makes the whole day deterministic
//! without a socket.

use bmlib::publications::fetchers::pubmed::{
    fetch_partitioned, EFetchPage, ESearchResult, Eutils, PartCallbacks, PartitionRequest,
    EFETCH_MAX_RETRIEVABLE, EFETCH_PAGE_SIZE,
};
use bmlib::publications::models::{FetchedRecord, PartCheckpoint};
use chrono::NaiveDate;
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

/// A scripted transport, recording every call and serving counts/pages in order.
struct ScriptedEutils {
    /// Answers to plan-time searches, keyed by the term's EDAT range.
    counts: Mutex<BTreeMap<String, i64>>,
    /// Answers to session searches, keyed the same way.
    session_counts: Mutex<BTreeMap<String, i64>>,
    /// The page every fetch serves, and how many are left.
    ///
    /// A part's walk asks for one page per `EFETCH_PAGE_SIZE` records, so a
    /// scripted transport has to serve pages *per part*. `None` means "serve
    /// forever".
    page: Mutex<Option<EFetchPage>>,
    /// Fetches left before the transport refuses.
    pages_left: Mutex<Option<usize>>,
    /// Every term a plan-time search was asked for.
    plan_terms: Mutex<Vec<String>>,
    /// Every term a session search was asked for.
    session_terms: Mutex<Vec<String>>,
    /// Whether a session ESearch reports a WebEnv/QueryKey.
    session_available: bool,
}

impl ScriptedEutils {
    fn empty() -> Self {
        ScriptedEutils {
            counts: Mutex::new(BTreeMap::new()),
            session_counts: Mutex::new(BTreeMap::new()),
            page: Mutex::new(None),
            pages_left: Mutex::new(None),
            plan_terms: Mutex::new(Vec::new()),
            session_terms: Mutex::new(Vec::new()),
            session_available: true,
        }
    }

    /// A transport whose plan and session searches agree, over one page per part
    /// delivering `per_part` records.
    fn uniform(counts: BTreeMap<String, i64>, per_part: i64) -> Self {
        let mut transport = ScriptedEutils::empty();
        *transport.counts.lock().expect("lock") = counts.clone();
        *transport.session_counts.lock().expect("lock") = counts;
        transport.set_pages(per_part);
        transport
    }

    /// Give every future page a fixed delivery, served without limit.
    ///
    /// A **page** carries at most
    /// [`EFETCH_PAGE_SIZE`](bmlib::publications::fetchers::pubmed::EFETCH_PAGE_SIZE)
    /// records — the walk asks for `retmax=500`, so a transport that answered
    /// with a whole part's worth in one page would make the walk deliver many
    /// times the part's promise. A first cut did that, and the day-total
    /// reconcile then passed by accident.
    fn set_pages(&mut self, per_part_promise: i64) {
        let delivered = per_part_promise.min(EFETCH_PAGE_SIZE as i64);
        let page = EFetchPage {
            articles: (0..delivered)
                .map(|i| FetchedRecord::new(format!("r{i}"), "pubmed"))
                .collect(),
            delivered,
        };
        *self.page.lock().expect("lock") = Some(page);
        *self.pages_left.lock().expect("lock") = None;
    }

    /// Serve at most `n` pages in total, then refuse.
    fn limit_pages(&mut self, n: usize) {
        *self.pages_left.lock().expect("lock") = Some(n);
    }

    /// The key of an EDAT range term, for the count maps.
    fn range_key(lo: &str, hi: &str) -> String {
        format!("{lo}..{hi}")
    }

    /// The EDAT range a term restricts, as a `lo..hi` key.
    ///
    /// Only the dates **followed by `[EDAT]`** count: the day term carries its
    /// own `"YYYY/MM/DD"[Date - Publication]`, and taking every quoted date in
    /// the term yields three, which matches no key and silently reads as zero —
    /// so the ladder's root probe refuses a day that is perfectly fetchable. A
    /// first cut did exactly that.
    fn term_key(term: &str) -> String {
        // Each EDAT date is a quoted span whose closing quote is followed by
        // `[EDAT]`. Taking *every* quoted span instead picks up the day term's
        // own `"YYYY/MM/DD"[Date - Publication]`, which makes three dates, no
        // match, and a silent zero.
        let mut dates: Vec<String> = Vec::new();
        for (start, _) in term.match_indices('"') {
            let rest = &term[start + 1..];
            if let Some(end) = rest.find('"') {
                let candidate = &rest[..end];
                if candidate.len() == 10 && rest[end + 1..].starts_with("[EDAT]") {
                    dates.push(candidate.replace('/', "-"));
                }
            }
        }
        match dates.as_slice() {
            [lo, hi] => Self::range_key(lo, hi),
            _ => term.to_string(),
        }
    }
}

impl Eutils for ScriptedEutils {
    fn esearch(
        &self,
        term: &str,
        _api_key: Option<&str>,
        use_history: bool,
    ) -> Result<ESearchResult, String> {
        let key = Self::term_key(term);
        if use_history {
            self.session_terms.lock().expect("lock").push(key.clone());
            let counts = self.session_counts.lock().expect("lock");
            let count = counts.get(&key).copied().unwrap_or(0);
            return Ok(ESearchResult {
                count,
                web_env: self.session_available.then(|| "W".to_string()),
                query_key: self.session_available.then(|| "1".to_string()),
            });
        }
        self.plan_terms.lock().expect("lock").push(key.clone());
        let counts = self.counts.lock().expect("lock");
        Ok(ESearchResult {
            count: counts.get(&key).copied().unwrap_or(0),
            web_env: None,
            query_key: None,
        })
    }

    fn efetch(
        &self,
        _web_env: &str,
        _query_key: &str,
        _retstart: usize,
        _api_key: Option<&str>,
    ) -> Result<EFetchPage, String> {
        let mut left = self.pages_left.lock().expect("lock");
        if let Some(remaining) = *left {
            if remaining == 0 {
                return Err("no more pages scripted".to_string());
            }
            *left = Some(remaining - 1);
        }
        self.page
            .lock()
            .expect("lock")
            .clone()
            .ok_or_else(|| "no page scripted".to_string())
    }
}

fn day() -> NaiveDate {
    NaiveDate::from_ymd_opt(2024, 6, 10).expect("date")
}

const DAY_TERM: &str = "(\"2024/06/10\"[Date - Publication])";

/// Run the loop with no-op callbacks, returning `(result, records walked)`.
/// Run the loop over a **narrow ladder root**, so the split points are the ones
/// the test scripts. The production root is 1900..2100, wide enough for any
/// Entrez date; deriving its split points from a scripted transport would test
/// arithmetic rather than the loop.
fn run(
    transport: &Arc<ScriptedEutils>,
    day_count: i64,
    checkpoints: &BTreeMap<String, PartCheckpoint>,
) -> (
    bmlib::publications::fetchers::pubmed::PubMedResult,
    Vec<FetchedRecord>,
    Vec<Option<PartCheckpoint>>,
    Vec<String>,
) {
    run_over(
        transport,
        day_count,
        checkpoints,
        ("2024-06-10", "2024-06-10"),
    )
}

/// As [`run`], with the ladder root stated.
fn run_over(
    transport: &Arc<ScriptedEutils>,
    day_count: i64,
    checkpoints: &BTreeMap<String, PartCheckpoint>,
    root: (&str, &str),
) -> (
    bmlib::publications::fetchers::pubmed::PubMedResult,
    Vec<FetchedRecord>,
    Vec<Option<PartCheckpoint>>,
    Vec<String>,
) {
    let mut finished: Vec<Option<PartCheckpoint>> = Vec::new();
    let mut skipped: Vec<String> = Vec::new();
    let mut progress: Vec<(i64, i64)> = Vec::new();
    let mut records: Vec<FetchedRecord> = Vec::new();
    let mut on_part_finished = |c: Option<PartCheckpoint>| finished.push(c);
    let mut on_part_skipped = |k: &str| skipped.push(k.to_string());
    let mut on_progress =
        |processed: i64, total: i64, _key: &str| progress.push((processed, total));
    let mut callbacks = PartCallbacks {
        on_part_finished: &mut on_part_finished,
        on_part_skipped: &mut on_part_skipped,
        on_progress: &mut on_progress,
    };
    let request = PartitionRequest::over(
        day(),
        DAY_TERM,
        day_count,
        checkpoints,
        (
            NaiveDate::parse_from_str(root.0, "%Y-%m-%d").expect("root lo"),
            NaiveDate::parse_from_str(root.1, "%Y-%m-%d").expect("root hi"),
        ),
    );
    let result = fetch_partitioned(
        transport.as_ref(),
        &request,
        &mut callbacks,
        &mut |record| records.push(record),
    );
    (result, records, finished, skipped)
}

/// The day's single part: one date, with a count under the cap.
///
/// The ladder root is the day itself, so it never splits — which is what makes
/// these tests about the **loop** rather than about the split points, and keeps
/// every part's session count below what one session can serve. A fixture whose
/// part promised more than the cap would be re-planned, and a single date cannot
/// be split, so the day would refuse before the loop ran.
fn part_counts(count: i64) -> BTreeMap<String, i64> {
    BTreeMap::from([(ScriptedEutils::range_key("2024-06-10", "2024-06-10"), count)])
}

// ---------------------------------------------------------------------------
// A day that walks clean
// ---------------------------------------------------------------------------

/// An over-cap day splits, walks each part, and reports a checkpoint for every
/// clean one.
#[test]
fn a_clean_day_checkpoints_every_part() {
    let counts = part_counts(9_000);
    let transport = Arc::new(ScriptedEutils::uniform(counts, 9_000));
    let (result, records, finished, skipped) = run(&transport, 9_000, &BTreeMap::new());

    assert_eq!(result.status, "completed", "{:?}", result.error);
    assert!(skipped.is_empty(), "nothing was checkpointed to skip");
    assert_eq!(finished.len(), 1, "one report per part: {finished:?}");
    for checkpoint in &finished {
        assert!(
            checkpoint.is_some(),
            "a clean part carries a checkpoint: {finished:?}"
        );
    }
    assert_eq!(records.len(), 9_000, "every record was walked");
}

/// **A resumed day skips a checkpointed part and credits its promise**, which is
/// what keeps the day-total reconcile from failing.
///
/// A skipped part is credited to `delivered` but **not** to `processed`: the
/// returned count says what this run did, which is less than the day's size by
/// however many records the skipped parts hold.
#[test]
fn a_resumed_day_skips_and_credits_without_recounting() {
    let counts = part_counts(9_000);
    let transport = Arc::new(ScriptedEutils::uniform(counts, 9_000));
    // The first part was checkpointed by an earlier run, at the count this run's
    // plan also reports for it.
    let part_key = bmlib::publications::fetchers::pubmed::part_key(day(), day());
    let mut checkpoints = BTreeMap::new();
    checkpoints.insert(
        part_key.clone(),
        PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key: part_key.clone(),
            promised: 9_000,
            record_count: 9_000,
        },
    );

    let (result, records, finished, skipped) = run(&transport, 9_000, &checkpoints);

    assert_eq!(result.status, "completed", "{:?}", result.error);
    assert_eq!(skipped, vec![part_key], "the checkpointed part was skipped");
    assert!(
        finished.is_empty(),
        "a skipped part is not walked, so nothing is reported"
    );
    assert!(
        records.is_empty(),
        "a skipped part is not fetched, so no records arrive"
    );
    assert_eq!(
        result.processed, 0,
        "processed counts what this run walked, which is nothing"
    );
}

/// A skipped part's promise is **still credited to the day's delivery**, so the
/// day-total reconcile passes. Without the credit a resumed day would fail on a
/// shortfall it did not cause.
#[test]
fn a_skipped_part_is_credited_to_the_day_total() {
    let counts = part_counts(9_000);
    let transport = Arc::new(ScriptedEutils::uniform(counts, 9_000));
    let part_key = bmlib::publications::fetchers::pubmed::part_key(day(), day());
    let mut checkpoints = BTreeMap::new();
    checkpoints.insert(
        part_key,
        PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key: bmlib::publications::fetchers::pubmed::part_key(day(), day()),
            promised: 9_000,
            record_count: 9_000,
        },
    );
    let (result, _, _, skipped) = run(&transport, 9_000, &checkpoints);
    assert_eq!(skipped.len(), 1);
    assert_eq!(
        result.status, "completed",
        "the skipped part's promise must satisfy the day total: {:?}",
        result.error
    );
    assert_eq!(result.note, None, "and it must not read as a shortfall");
}

/// A checkpoint whose promise has **moved** forces a re-fetch rather than a skip.
///
/// Skipping on the key alone would permanently lose every record the part gained
/// since it was checkpointed.
#[test]
fn a_checkpoint_whose_count_moved_forces_a_refetch() {
    let counts = part_counts(9_000);
    let transport = Arc::new(ScriptedEutils::uniform(counts, 9_000));
    let part_key = bmlib::publications::fetchers::pubmed::part_key(day(), day());
    let mut checkpoints = BTreeMap::new();
    checkpoints.insert(
        part_key.clone(),
        PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key,
            // Stored as 5,000 where this run's plan says 9,000.
            promised: 5_000,
            record_count: 5_000,
        },
    );

    let (result, records, finished, skipped) = run(&transport, 9_000, &checkpoints);

    assert_eq!(result.status, "completed", "{:?}", result.error);
    assert!(
        skipped.is_empty(),
        "a moved count must not be skipped: {skipped:?}"
    );
    assert_eq!(finished.len(), 1, "the part was walked after all");
    assert_eq!(records.len(), 9_000);
}

// ---------------------------------------------------------------------------
// What fails the whole day
// ---------------------------------------------------------------------------

/// **Every failure path fails the whole day.** A day recorded `completed` is
/// never re-offered, so a part that could not be verified must not be left
/// looking whole.
#[test]
fn a_part_that_delivers_too_little_fails_the_day() {
    let counts = part_counts(9_000);
    let transport = Arc::new(ScriptedEutils::uniform(counts.clone(), 100));
    let (result, _, _, _) = run(&transport, 9_000, &BTreeMap::new());
    assert_eq!(result.status, "failed");
    let error = result.error.expect("a failure");
    assert!(error.contains("below the 50% floor"), "{error}");
}

/// A session that reports a count but **no WebEnv** cannot be walked, so the day
/// fails rather than walking the count in useless requests.
#[test]
fn a_part_without_a_session_fails_the_day() {
    let counts = part_counts(9_000);
    let mut transport = ScriptedEutils::empty();
    // The **planning** searches answer normally, so the ladder plans; only the
    // per-part session searches come back without a WebEnv.
    *transport.counts.lock().expect("lock") = counts.clone();
    *transport.session_counts.lock().expect("lock") = counts;
    transport.session_available = false;
    transport.set_pages(0);
    let transport = Arc::new(transport);

    let (result, _, _, _) = run(&transport, 9_000, &BTreeMap::new());
    assert_eq!(result.status, "failed");
    assert!(
        result
            .error
            .unwrap_or_default()
            .contains("without a history session"),
        "the message must say the session is what is missing"
    );
}

/// A page error stops the walk and fails the day, naming the part.
#[test]
fn a_page_error_fails_the_day_and_names_the_part() {
    let counts = part_counts(9_000);
    let mut scripted = ScriptedEutils::uniform(counts, 9_000);
    // Exhaust the scripted pages, so the first fetch finds none.
    scripted.limit_pages(0);
    let transport = Arc::new(scripted);
    let (result, _, _, _) = run(&transport, 9_000, &BTreeMap::new());
    assert_eq!(result.status, "failed");
    let error = result.error.unwrap_or_default();
    assert!(error.starts_with("part edat:"), "{error}");
}

/// A part that **grew** between planning and fetching is split again rather than
/// walked, because the last page of an over-cap session is silently clamped — so
/// walking would look like an ordinary short day.
///
/// The part here is a **single Entrez date**, so re-planning it necessarily
/// reaches `lo == hi` at the count that triggered the split and refuses with a
/// message that names a count an ESearch actually returned. That keeps the test
/// on the *branch* rather than on the ladder's arithmetic, which
/// `pubmed_walk` already pins.
///
/// A first cut used a multi-day part whose session count disagreed with the
/// children a re-plan gave it. Both describe the same range, so the disagreement
/// is not a fixture the source can produce — and the ladder walked it down a
/// structurally empty tail, which is the phantom the whole-range measurement
/// exists for. Testing the branch needs a case whose re-plan is determinate.
#[test]
fn a_part_that_grew_is_split_again() {
    let mut transport = ScriptedEutils::empty();
    // Planning sees one date, under the cap.
    transport
        .counts
        .lock()
        .expect("lock")
        .insert(ScriptedEutils::range_key("2024-06-10", "2024-06-10"), 5_000);
    // Its session reports 20,000: it grew between the two requests, and an
    // Entrez date cannot be split further.
    transport.session_counts.lock().expect("lock").insert(
        ScriptedEutils::range_key("2024-06-10", "2024-06-10"),
        20_000,
    );
    transport.set_pages(500);
    let transport = Arc::new(transport);

    let (result, _, finished, _) = run(&transport, 5_000, &BTreeMap::new());
    assert_eq!(result.status, "failed");
    let error = result.error.expect("a failure");
    assert!(
        error.contains("20,000") || error.contains("20000"),
        "the refusal must name the count the session returned: {error}"
    );
    assert!(
        error.contains("cannot be split further"),
        "and say why it cannot be split: {error}"
    );
    assert!(
        finished.is_empty(),
        "a refused part is not reported as finished: {finished:?}"
    );
}

/// **The re-plan branch is reachable only for a single-date part**, and that is
/// a property of the ladder rather than of this loop.
///
/// A part reaches the loop only when planning measured its range **at or under**
/// the cap, so a multi-date part cannot arrive over it — the ladder would have
/// split it. The only part that can therefore arrive over the cap is one the
/// ladder could not split, i.e. a single Entrez date. It is also the only case
/// where re-planning refuses, so the branch has one reachable input and one
/// outcome, and the test above pins both.
///
/// Stated here because a reader looking for "the healthy growth path" will not
/// find one: there is nothing to write.
#[test]
fn the_replan_branch_has_exactly_one_reachable_outcome() {
    // A single date above the cap is the only over-cap part planning can emit,
    // and it refuses rather than emitting one.
    let mut counter = |_: &str| Ok(EFETCH_MAX_RETRIEVABLE + 1);
    let outcome = bmlib::publications::fetchers::pubmed::plan_partitions(
        &mut counter,
        "T",
        EFETCH_MAX_RETRIEVABLE + 1,
        NaiveDate::from_ymd_opt(2024, 6, 10).expect("date"),
        NaiveDate::from_ymd_opt(2024, 6, 10).expect("date"),
        true,
        Some(EFETCH_MAX_RETRIEVABLE + 1),
    );
    assert!(
        matches!(
            outcome,
            Err(bmlib::publications::fetchers::pubmed::PlanError::Unsplittable { .. })
        ),
        "a single over-cap date must refuse: {outcome:?}"
    );
}

/// A day whose parts deliver exactly what was promised reconciles clean and
/// carries **no note**.
#[test]
fn a_clean_day_carries_no_note() {
    let counts = part_counts(9_000);
    let transport = Arc::new(ScriptedEutils::uniform(counts, 9_000));
    let (result, _, _, _) = run(&transport, 9_000, &BTreeMap::new());
    assert_eq!(result.note, None, "{:?}", result.note);
}

// ---------------------------------------------------------------------------
// The day's four arms
// ---------------------------------------------------------------------------

/// A scripted transport for the **day-level** arms, where the ladder does not
/// matter: the search answers once and the pages follow.
struct DayTransport {
    count: i64,
    session: bool,
    page_delivered: i64,
    /// How many pages to serve before refusing.
    pages: Mutex<usize>,
    /// Every search term asked for.
    terms: Mutex<Vec<String>>,
    /// How many page fetches were made. A partitioned day fetches **per part**;
    /// a single session fetches for the whole day.
    fetches: Mutex<usize>,
    /// Every session search's term, which names the range each part covered.
    session_terms: Mutex<Vec<String>>,
}

impl DayTransport {
    fn new(count: i64, session: bool, page_delivered: i64) -> Self {
        DayTransport {
            count,
            session,
            page_delivered,
            pages: Mutex::new(usize::MAX),
            terms: Mutex::new(Vec::new()),
            fetches: Mutex::new(0),
            session_terms: Mutex::new(Vec::new()),
        }
    }
}

impl Eutils for DayTransport {
    fn esearch(
        &self,
        term: &str,
        _api_key: Option<&str>,
        use_history: bool,
    ) -> Result<ESearchResult, String> {
        // The day-level search is sent `usehistory=y`, so it lands in
        // `session_terms` too; what distinguishes it is that it covers the whole
        // day rather than a part of it.
        if use_history {
            self.session_terms
                .lock()
                .expect("lock")
                .push(term.to_string());
        } else {
            self.terms.lock().expect("lock").push(term.to_string());
        }
        // A count **proportional to the range's span**, for the planning
        // searches: the production ladder's root is 1900..2100, so a fixed count
        // would either put every narrow range over the cap (unsplittable) or
        // leave the wide root under it (refusing the day on the root probe). The
        // day-level search returns the day's own count.
        // The day-level search restricts `[Date - Publication]`; a planning
        // search restricts `[EDAT]`. Distinguishing on the field, not on the
        // date, is what keeps a single-day planning range from being answered
        // with the day's whole count — which made the leftmost date of the
        // production root measure tens of thousands and refuse as unsplittable.
        let count = if !term.contains("[EDAT]") {
            self.count
        } else {
            let dates = edat_dates(term);
            match count_for_dates(&dates) {
                Some(dense) => dense,
                None => self.count,
            }
        };
        Ok(ESearchResult {
            count,
            web_env: self.session.then(|| "W".to_string()),
            query_key: self.session.then(|| "1".to_string()),
        })
    }

    fn efetch(
        &self,
        _web_env: &str,
        _query_key: &str,
        _retstart: usize,
        _api_key: Option<&str>,
    ) -> Result<EFetchPage, String> {
        *self.fetches.lock().expect("lock") += 1;
        let mut pages = self.pages.lock().expect("lock");
        if *pages == 0 {
            return Err("no more pages".to_string());
        }
        *pages -= 1;
        Ok(EFetchPage {
            articles: (0..self.page_delivered)
                .map(|i| FetchedRecord::new(format!("r{i}"), "pubmed"))
                .collect(),
            delivered: self.page_delivered,
        })
    }
}

/// The `(lo, hi)` EDAT dates a term restricts, as `NaiveDate`s.
fn edat_dates(term: &str) -> Vec<chrono::NaiveDate> {
    let mut dates = Vec::new();
    for (start, _) in term.match_indices('"') {
        let rest = &term[start + 1..];
        if let Some(end) = rest.find('"') {
            let candidate = &rest[..end];
            if candidate.len() == 10
                && candidate.contains('/')
                && rest[end + 1..].starts_with("[EDAT]")
            {
                if let Ok(d) = NaiveDate::parse_from_str(&candidate.replace('/', "-"), "%Y-%m-%d") {
                    dates.push(d);
                }
            }
        }
    }
    dates
}

/// A sparse density for a range, so the ladder's halves sum to their parent.
///
/// Returns `None` when the term carries no EDAT range, which is the day-level
/// search.
fn count_for_dates(dates: &[NaiveDate]) -> Option<i64> {
    let [lo, hi] = dates else {
        return None;
    };
    let span = (*hi - *lo).num_days() + 1;
    if span <= 0 {
        return None;
    }
    // 8 records per day, with a floor of 5 so no single day measures zero —
    // which would make the ladder treat it as an empty tail. The 1900..2100 root
    // is ~58,000 (way over the cap) and a single day is 8.
    Some((span * 8).max(5))
}

/// Run `fetch_pubmed` with no-op callbacks and an empty checkpoint map.
fn run_day(
    transport: &dyn Eutils,
    checkpoints: &BTreeMap<String, PartCheckpoint>,
) -> (bmlib::publications::fetchers::pubmed::PubMedResult, usize) {
    let mut records = 0usize;
    let mut on_record = |_: FetchedRecord| records += 1;
    let mut on_progress = |_: i64, _: i64, _: &str| {};
    let mut on_part_finished = |_: Option<PartCheckpoint>| {};
    let mut on_part_skipped = |_: &str| {};
    let mut callbacks = bmlib::publications::fetchers::pubmed::DayCallbacks {
        on_record: &mut on_record,
        on_progress: &mut on_progress,
        completed_parts: checkpoints,
        on_part_finished: &mut on_part_finished,
        on_part_skipped: &mut on_part_skipped,
    };
    let result =
        bmlib::publications::fetchers::pubmed::fetch_pubmed(transport, day(), None, &mut callbacks);
    (result, records)
}

/// **A quiet day completes with nothing to do**, and issues no fetch at all.
#[test]
fn a_quiet_day_completes_without_fetching() {
    let transport = DayTransport::new(0, true, 0);
    let (result, records) = run_day(&transport, &BTreeMap::new());
    assert_eq!(result.status, "completed");
    assert_eq!(result.processed, 0);
    assert_eq!(records, 0);
    assert_eq!(
        transport.session_terms.lock().expect("lock").len(),
        1,
        "one search, no pages"
    );
    assert_eq!(
        *transport.fetches.lock().expect("lock"),
        0,
        "a quiet day fetches nothing"
    );
}

/// **A zero count with checkpoints is refused**, and the refusal names the
/// records the checkpoints attest to.
#[test]
fn a_zero_with_checkpoints_is_refused() {
    let transport = DayTransport::new(0, true, 0);
    let part = bmlib::publications::fetchers::pubmed::part_key(day(), day());
    let mut checkpoints = BTreeMap::new();
    checkpoints.insert(
        part.clone(),
        PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key: part,
            promised: 9_000,
            record_count: 9_000,
        },
    );
    let (result, _) = run_day(&transport, &checkpoints);
    assert_eq!(result.status, "failed");
    let error = result.error.expect("a failure");
    assert!(error.contains("0 records"), "{error}");
    assert!(error.contains("9000 records"), "{error}");
    assert!(error.contains("download_day_parts"), "{error}");
}

/// **An under-cap day with no session is refused** rather than walked: without a
/// `WebEnv` each page asks for an empty one and returns a document holding no
/// articles, so an unguarded fetch walks the whole count in useless requests and
/// reports `completed` with nothing.
#[test]
fn a_day_without_a_session_is_refused() {
    let transport = DayTransport::new(100, false, 0);
    let (result, records) = run_day(&transport, &BTreeMap::new());
    assert_eq!(result.status, "failed");
    assert!(result
        .error
        .unwrap_or_default()
        .contains("without a history session"));
    assert_eq!(records, 0);
    assert_eq!(
        *transport.pages.lock().expect("lock"),
        usize::MAX,
        "no page may be fetched"
    );
}

/// An under-cap day with a session is walked in one go, and reconciles against
/// the day's own count.
#[test]
fn an_under_cap_day_is_walked_and_reconciled() {
    let transport = DayTransport::new(600, true, 300);
    let (result, records) = run_day(&transport, &BTreeMap::new());
    assert_eq!(result.status, "completed", "{:?}", result.error);
    assert_eq!(result.processed, 600);
    assert_eq!(records, 600);
}

/// An under-cap day that delivers too little fails on the **day's own** count.
#[test]
fn an_under_cap_day_that_delivers_too_little_fails() {
    let transport = DayTransport::new(1_000, true, 100);
    let (result, _) = run_day(&transport, &BTreeMap::new());
    assert_eq!(result.status, "failed");
    assert!(result
        .error
        .unwrap_or_default()
        .contains("below the 50% floor"));
}

/// **An over-cap day is partitioned even when the day-level search carried no
/// session**, because the session opened at day level is unused on that path —
/// each part opens its own.
///
/// The point is the *ordering*: the over-cap branch sits ahead of the session
/// guard, so the day is not refused for a missing day-level `WebEnv`. Each part
/// still needs its own session, so with none available the day fails **on a
/// part** rather than at the day level — which is what the error names.
#[test]
fn an_over_cap_day_partitions_without_a_day_level_session() {
    let transport = DayTransport::new(EFETCH_MAX_RETRIEVABLE + 1, false, 500);
    let (result, _) = run_day(&transport, &BTreeMap::new());

    let error = result.error.clone().unwrap_or_default();
    assert!(
        !error.contains("without a history session (WebEnv/QueryKey)"),
        "an over-cap day must not be refused at the day level: {error}"
    );
    assert!(
        error.starts_with("part edat:"),
        "it must have parted and failed there: {error}"
    );
    assert!(
        transport.session_terms.lock().expect("lock").len() > 1,
        "and it must have opened a session per part"
    );
}

/// An over-cap day **with** sessions is partitioned all the way through: every
/// part opens its own session, and the day completes.
#[test]
fn an_over_cap_day_partitions_and_completes() {
    // Unlimited: a page per 500 records across a 1900..2100 ladder is a great
    // many, and the walk refuses at zero.
    let transport = DayTransport::new(EFETCH_MAX_RETRIEVABLE + 1, true, 500);
    let (result, records) = run_day(&transport, &BTreeMap::new());
    assert_eq!(result.status, "completed", "{:?}", result.error);
    // One session for the day plus one per part.
    let sessions = transport.session_terms.lock().expect("lock").len();
    assert!(sessions > 2, "one per part: {sessions}");
    assert!(records > 0, "records were walked");
}
