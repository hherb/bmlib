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

//! Multi-source sync: which days to fetch, and what to record about them.
//!
//! A port of `bmlib/publications/sync.py`. This module holds the port's
//! densest invariants, and they all point the same way: **an uncertain day
//! costs a re-fetch, while a day wrongly called done is permanently missing.**
//! Every rule below fails closed for that reason.
//!
//! # The one rule to understand before the rest
//!
//! [`day_was_over_when_fetched`] decides whether a stored `completed` day is
//! *durable*. The boundary is **12:00 UTC on the following day**, and the hour
//! is not a safety margin. Day *D* finishes last in UTC-12, whose midnight is
//! noon UTC on *D+1*; equally, that instant is exactly the point beyond which
//! "now" can no longer fall inside day *D* anywhere on earth.
//!
//! Without it (#95): `sync`'s default window is `[yesterday, today]`, so a
//! 09:00 cron captured today as it stood at 09:00 and recorded it done.
//! Tomorrow that day is neither `today` nor `failed`, so at the documented
//! default `recheck_days=0` it was never offered again and the remaining 15
//! hours of indexing were permanently absent. Reconciliation cannot catch it:
//! the source's own count agreed at 09:00, because the walk really did deliver
//! everything that existed then.
//!
//! # `now` is a parameter, not a global
//!
//! Python reads the wall clock inside the rules and its tests monkeypatch
//! `fetch_all` to control it. Here the clock is threaded through as a
//! [`NaiveDate`] (or an explicit instant) so a test states the time it means.
//! That is a real improvement in testability and a deliberate one — the rules
//! are wall-clock-sensitive by nature, and a rule that reads a global clock is
//! a rule whose tests are about the clock.

use std::collections::BTreeSet;

use chrono::{DateTime, Duration, FixedOffset, NaiveDate, NaiveDateTime, Utc};

use crate::db::operations::{execute, fetch_all};
use crate::db::{Db, DbError, Row, Value};
use crate::publications::fetchers::{FetchRequest, FetchSink, Fetcher, PartDisposition, Progress};
use crate::publications::models::FullTextSource;
use crate::publications::models::{
    AuthorAffiliation, DownloadDay, FetchResult, FetchedRecord, Grant, PartCheckpoint, Publication,
    SyncReport,
};
use crate::publications::storage::store_publication;
use crate::pyvalue::repr_str;

/// The last day the window may name.
///
/// **Not** `NaiveDate::MAX`. Chrono's range is far wider than Python's
/// `date.max` (year 262143 against 9999), and `validate_window`'s message is
/// what a caller reads — so the bound is the one Python's own error text names
/// rather than whatever the date library happens to hold. Years beyond 9999 are
/// not a case this library has, and advertising them in an error message would
/// be a bound no caller recognises.
pub const LAST_SUPPORTED_DATE: &str = "9999-12-31";

/// The first day the window may reach back to — Python's `date.min`.
pub const FIRST_SUPPORTED_DATE: &str = "0001-01-01";

/// The hour on *D+1* at which day *D* is over in every timezone.
///
/// UTC-12 is the last zone to finish any calendar day, and its midnight is noon
/// UTC the following day.
pub const DAY_ENDS_EVERYWHERE_AT_UTC_HOUR: u32 = 12;

/// How far past "now" a stored `downloaded_at` may sit and still be believed.
///
/// A fetch cannot have happened in the future, so a timestamp beyond now is a
/// clock the rule cannot trust. The value is a fixed choice, not a measured
/// one — but the choice is bounded on both sides by an asymmetry rather than by
/// taste: too tight costs one merged re-fetch of a day that settles on the next
/// run, while too loose reads an impossible claim as durable and loses the day
/// permanently. Five minutes is generous against ordinary host skew and far
/// tighter than any of the failures this guards.
pub const CLOCK_SKEW_TOLERANCE_MINUTES: i64 = 5;

/// Why a sync argument was refused.
///
/// Every message names the parameter, because `sync`'s caller has no other way
/// to tell which one was wrong — and in Python these escaped the whole
/// multi-source run, losing the `SyncReport` with them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WindowError {
    /// A parameter was not a plain calendar date.
    NotADate {
        /// The parameter name.
        field: &'static str,
        /// What arrived instead, in Python's own type words.
        got: &'static str,
    },
    /// `recheck_days` was not a whole number.
    NotAWholeNumber {
        /// What arrived instead.
        got: &'static str,
    },
    /// `date_to` was the last representable day, so there is no day after it.
    NoDayAfter {
        /// The last representable date, ISO.
        last: String,
    },
    /// `recheck_days` was negative.
    NegativeRecheck {
        /// The value given.
        got: i64,
    },
    /// `recheck_days` reached back before the start of the calendar.
    RecheckBeforeCalendar {
        /// The value given.
        got: i64,
        /// How many days today is after the minimum date.
        days_since_min: i64,
        /// The minimum date, ISO.
        min: String,
    },
}

impl std::fmt::Display for WindowError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WindowError::NotADate { field, got } => write!(
                f,
                "{field} must be a datetime.date, got {got} — note that a datetime \
                 is not one for this purpose: it satisfies the annotation and then \
                 writes a download_days.date no lookup matches"
            ),
            WindowError::NotAWholeNumber { got } => write!(
                f,
                "recheck_days must be a whole number of days, got {got} — a float \
                 slips both range checks below, since every comparison against nan \
                 is False, and then silently disables rechecking"
            ),
            WindowError::NoDayAfter { last } => write!(
                f,
                "date_to must be earlier than {last}: day selection asks which day \
                 follows the last day of the window, and there is none"
            ),
            WindowError::NegativeRecheck { got } => {
                write!(f, "recheck_days must not be negative, got {got}")
            }
            WindowError::RecheckBeforeCalendar {
                got,
                days_since_min,
                min,
            } => write!(
                f,
                "recheck_days must not reach back before {min}: got {got}, and today \
                 is only {days_since_min} days after it"
            ),
        }
    }
}

impl std::error::Error for WindowError {}

// ---------------------------------------------------------------------------
// Timestamp reading
// ---------------------------------------------------------------------------

/// Read a timestamp as an **offset-aware instant**, or `None` if it is not one.
///
/// Separate from its caller so that "unusable" is one answer rather than three:
/// a non-string, an unparseable string and a naive timestamp all mean the same
/// thing to the durability rule. The naive case in particular must not reach a
/// comparison — comparing an aware instant against a naive one is an error in
/// Python (`TypeError`) and a compile error in Rust, and in Python it would
/// abort a whole sync from inside day selection.
///
/// Python's `datetime.fromisoformat` accepts a value with **no** offset, so the
/// distinction this makes is not "can it be parsed" but "does it say when".
#[must_use]
pub fn read_aware_timestamp(value: Option<&str>) -> Option<DateTime<FixedOffset>> {
    let text = value?;
    // Python writes `+00:00` and never `Z`; RFC 3339 accepts both, plus the
    // fractional seconds `datetime.now()` produces. The **offset is kept as
    // written** rather than normalised to UTC, because Python's
    // `datetime.isoformat()` echoes it and a caller comparing the two would see
    // `-05:00` become `+00:00`. Comparisons are still instant comparisons —
    // `DateTime<FixedOffset>` orders by instant.
    DateTime::parse_from_rfc3339(text).ok()
}

/// Whether an ISO string parses but carries **no** offset.
///
/// Used only to warn: the durability rule treats it as unusable, and a caller
/// reading the log needs to know which of the three it was.
#[must_use]
pub fn is_naive_timestamp(value: &str) -> bool {
    DateTime::parse_from_rfc3339(value).is_err()
        && NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f").is_ok()
}

/// Read `last_verified_at`'s calendar date, or `None` if it cannot be read.
///
/// The companion to [`read_aware_timestamp`] and deliberately **laxer**: only
/// the calendar date is used, so a naive value is perfectly usable here where
/// it is not for the durability rule. Routing this through the aware-only guard
/// would fail closed on every naive row and re-fetch the whole window on every
/// run for a `recheck_days` caller.
///
/// A stored `NULL` is **not** unusable: it is the documented "never verified"
/// state, which rule 4 of [`days_needing_fetch`] already answers by rechecking.
#[must_use]
pub fn read_verification_date(value: Option<&str>) -> Option<NaiveDate> {
    let text = value?;
    if let Ok(dt) = DateTime::parse_from_rfc3339(text) {
        return Some(dt.date_naive());
    }
    // A naive value: only the date part is wanted, so accept it.
    if let Ok(naive) = NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f") {
        return Some(naive.date());
    }
    NaiveDate::parse_from_str(text, "%Y-%m-%d").ok()
}

/// Whether `last_verified_at` was present but unreadable.
#[must_use]
pub fn verification_is_unreadable(value: Option<&str>) -> bool {
    value.is_some() && read_verification_date(value).is_none()
}

// ---------------------------------------------------------------------------
// The durability rule
// ---------------------------------------------------------------------------

/// Had `day` already ended everywhere on earth at `fetched_at`?
///
/// See the module docs for why the boundary is noon UTC on the following day.
///
/// Fails closed on an unreadable timestamp and on one that cannot be *true*: a
/// fetch cannot have happened in the future, so a value beyond `now` plus
/// [`CLOCK_SKEW_TOLERANCE_MINUTES`] is rejected rather than believed. Without
/// that bound the guard is loud about a value it cannot parse and silent about
/// one asserting the day was fetched tomorrow, which is #95's own failure mode:
/// permanent, invisible loss.
#[must_use]
pub fn day_was_over_when_fetched(
    day: NaiveDate,
    downloaded_at: Option<&str>,
    now: DateTime<Utc>,
    settle_days: u32,
) -> bool {
    let Some(fetched_at) = read_aware_timestamp(downloaded_at) else {
        return false;
    };
    if fetched_at > now + Duration::minutes(CLOCK_SKEW_TOLERANCE_MINUTES) {
        return false;
    }
    let Some(boundary) = day_over_everywhere(day) else {
        return false;
    };
    // **A difference, and not `boundary + Duration::days(settle_days)`.** Rule 5
    // reads rows of any date, so adding the period to a day near the calendar's
    // end overflows there — outside every per-day handler, where it costs the
    // whole run its report rather than one day's. A difference of two instants is
    // always representable.
    fetched_at.signed_duration_since(boundary) >= Duration::days(i64::from(settle_days))
}

/// The exact instant day `day` is over everywhere on earth.
#[must_use]
pub fn day_over_everywhere(day: NaiveDate) -> Option<DateTime<Utc>> {
    (day + Duration::days(1))
        .and_hms_opt(DAY_ENDS_EVERYWHERE_AT_UTC_HOUR, 0, 0)
        .map(|naive| naive.and_utc())
}

// ---------------------------------------------------------------------------
// Window validation
// ---------------------------------------------------------------------------

/// Refuse a window or recheck depth that day selection cannot walk.
///
/// Two kinds of rejection, and they fail differently. The **type** checks catch
/// a value that is not a day or a whole number of days at all; the **range**
/// checks catch one that is, but lies outside the calendar. Both reach date
/// arithmetic inside [`days_needing_fetch`], and what escapes from there takes
/// the whole multi-source run with it, because `sync`'s cleanup carries no
/// catch. The `SyncReport` is lost before a single record is fetched, for every
/// source rather than for one day — worse in kind than the per-day losses the
/// rest of this module guards against, because it is total (#99).
///
/// What is deliberately **not** rejected is an *empty* window
/// (`date_from > date_to`). That is what the ordinary incremental-sync idiom
/// produces once it has caught up, so raising would turn a caller that is
/// simply up to date into a crashing one. A window reaching into the *future*
/// is likewise accepted, and reported instead — see [`note_unreachable_days`].
///
/// The `datetime`-is-not-a-`date` check has no Rust analogue worth a separate
/// gate: the types differ, so the compiler is the guard. What *does* carry over
/// is the argument, and it is recorded on [`WindowError::NotADate`] for the
/// caller reading this port's docs.
///
/// # Errors
///
/// Naming the offending parameter.
pub fn validate_window(
    date_to: NaiveDate,
    recheck_days: i64,
    today: NaiveDate,
) -> Result<(), WindowError> {
    let last_supported =
        NaiveDate::parse_from_str(LAST_SUPPORTED_DATE, "%Y-%m-%d").expect("a literal date parses");
    if date_to >= last_supported {
        return Err(WindowError::NoDayAfter {
            last: LAST_SUPPORTED_DATE.to_string(),
        });
    }
    if recheck_days < 0 {
        return Err(WindowError::NegativeRecheck { got: recheck_days });
    }
    let first_supported =
        NaiveDate::parse_from_str(FIRST_SUPPORTED_DATE, "%Y-%m-%d").expect("a literal date parses");
    let days_since_date_min = (today - first_supported).num_days();
    if recheck_days > days_since_date_min {
        return Err(WindowError::RecheckBeforeCalendar {
            got: recheck_days,
            days_since_min: days_since_date_min,
            min: FIRST_SUPPORTED_DATE.to_string(),
        });
    }
    Ok(())
}

/// Report a window ending in the future, which can never complete.
///
/// [`day_was_over_when_fetched`] requires a fetch at or after 12:00 UTC on the
/// day *after* the day it describes, which for a day that has not happened is
/// unsatisfiable. So each future day is stored `completed` and re-offered on
/// every subsequent run, for the life of the installation — a permanent cost
/// that was reported at no log level and in no field of the `SyncReport`.
/// Invisible and permanent is the pair this module's other rules exist to break
/// up.
///
/// Rejecting the window was considered and refused: the past half of a window
/// ending tomorrow is perfectly fetchable, and raising would discard it along
/// with the unreachable half.
#[must_use]
pub fn note_unreachable_days(date_to: NaiveDate, today: NaiveDate) -> Option<String> {
    if date_to <= today {
        return None;
    }
    Some(format!(
        "date_to is {} day(s) in the future ({}); a day that has not ended cannot be \
         recorded durably, so those days will be re-fetched on every run until they \
         are past",
        (date_to - today).num_days(),
        date_to.format("%Y-%m-%d")
    ))
}

// ---------------------------------------------------------------------------
// Day selection
// ---------------------------------------------------------------------------

/// One stored `download_days` row, as day selection reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DayRow {
    /// The day, ISO.
    pub date: String,
    /// The stored status string.
    pub status: String,
    /// When the fetch wrote the row.
    pub downloaded_at: Option<String>,
    /// When the day was last verified.
    pub last_verified_at: Option<String>,
}

/// Why a day is being offered again.
///
/// Carried rather than discarded because the categories fail differently: three
/// of the four cost one merged re-fetch, and the fourth is a caller asking for
/// something. A caller diagnosing a slow sync needs to know which.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FetchReason {
    /// No row at all.
    NoRow,
    /// A row whose status is anything but `"completed"`.
    NotCompleted,
    /// A completed row whose fetch cannot be shown to have happened after the
    /// day was over everywhere.
    NotDurable,
    /// `recheck_days` is set and the row's verification is stale, absent or
    /// unreadable.
    RecheckDue,
    /// A row **outside** the caller's window that is not yet final: a status other
    /// than `"completed"`, or a completed day that has not settled.
    ///
    /// Offered whatever the window, because a source whose days are routinely
    /// filled late would otherwise leave the window unfinished and never be seen
    /// again. A *failed* row is included: a revisit that fails turns a completed
    /// row `failed`, so a rule offering only completed rows would drop the day
    /// after its first transient error.
    Unsettled,
}

/// A day that needs fetching, with why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DayToFetch {
    /// The day.
    pub day: NaiveDate,
    /// Why it is being offered.
    pub reason: FetchReason,
}

/// The last day Python's `date` can represent: `9999-12-31`.
///
/// **Not [`NaiveDate::MAX`]**, which is year 262143 — chrono's calendar is wider
/// than Python's, so a row carrying Python's `date.max` compares unequal to it and
/// would be re-offered on every run. The stored strings are Python's
/// `isoformat()`, so the bound that matters is Python's, and it is the bound
/// because `day + timedelta(days=1)` — which the durability rule needs — raises
/// there. `unsettled_days_outside` skips such a row rather than raising, since a
/// raise escapes day selection and loses the whole run's report.
fn python_date_max() -> NaiveDate {
    NaiveDate::from_ymd_opt(9999, 12, 31).expect("the last day Python's date can hold")
}

/// Determine which days need fetching for a source.
///
/// Rules, each failing closed — an uncertain day costs a re-fetch, which
/// `store_publication` merges, while a day wrongly called done is permanently
/// missing:
///
/// 1. No row at all: include.
/// 2. A row whose status is anything but `"completed"`: include. Read as a
///    denylist (`== "failed"`) this is the mirror of the write bug
///    [`resolve_day_status`] fixes: a status in any other spelling counted as
///    done, so a day that never succeeded was never offered again.
/// 3. A completed row whose fetch cannot be shown to have happened after the
///    day was over everywhere: include.
/// 4. If `recheck_days` > 0 and `last_verified_at` is older than that many
///    days, absent, or unreadable: include.
///
/// Rule 3 costs exactly one extra day-fetch per run under the default window
/// `[yesterday, today]` — two rather than one — because day *D* is offered once
/// more on *D+1*, which is the point. A caller passing a window of three days
/// or more, whose run happens before 12:00 UTC, pays one more again (three);
/// the cost does not grow with the window beyond that, and vanishes entirely
/// for a run at or after 12:00 UTC.
///
/// **Preconditions.** `date_from`, `date_to` and `recheck_days` are assumed
/// already validated by [`validate_window`]; this function does not re-check,
/// because catching an arithmetic failure here would convert a caller bug into
/// a day that quietly looks like it needs no fetch.
#[must_use]
pub fn days_needing_fetch(
    rows: &[DayRow],
    date_from: NaiveDate,
    date_to: NaiveDate,
    recheck_days: i64,
    now: DateTime<Utc>,
    settle_days: u32,
) -> Vec<DayToFetch> {
    let today = now.date_naive();
    let mut needed: Vec<DayToFetch> = Vec::new();
    let mut day = date_from;
    while day <= date_to {
        let key = day.format("%Y-%m-%d").to_string();
        let entry = rows.iter().find(|r| r.date == key);
        match entry {
            None => needed.push(DayToFetch {
                day,
                reason: FetchReason::NoRow,
            }),
            Some(row) => {
                if row.status != "completed" {
                    needed.push(DayToFetch {
                        day,
                        reason: FetchReason::NotCompleted,
                    });
                } else if !day_was_over_when_fetched(
                    day,
                    row.downloaded_at.as_deref(),
                    now,
                    settle_days,
                ) {
                    needed.push(DayToFetch {
                        day,
                        reason: FetchReason::NotDurable,
                    });
                } else if recheck_days > 0 {
                    let last_verified = read_verification_date(row.last_verified_at.as_deref());
                    let cutoff = today - Duration::days(recheck_days);
                    if last_verified.is_none_or(|d| d < cutoff) {
                        needed.push(DayToFetch {
                            day,
                            reason: FetchReason::RecheckDue,
                        });
                    }
                }
            }
        }
        day += Duration::days(1);
    }

    if settle_days > 0 {
        // Rule 5. Every row outside the window, of **any age**: a floor such as
        // "the last `settle_days` days" would strand a day fetched early by a run
        // that was then not repeated for longer than that, and the rows it would
        // skip are exactly the incomplete ones.
        //
        // A row whose date cannot be read, or is the last representable day, is
        // skipped rather than raised: a raise here escapes day selection and costs
        // the whole run its report. Python logs a WARNING on every run for such a
        // row, **and this does not** — the port has no logger in this module and
        // day selection has no report to write to, so the only difference is the
        // line, never which days are selected.
        for row in rows {
            let Ok(day) = NaiveDate::parse_from_str(&row.date, "%Y-%m-%d") else {
                continue;
            };
            if day == python_date_max() || (day >= date_from && day <= date_to) {
                continue;
            }
            if row.status != "completed"
                || !day_was_over_when_fetched(day, row.downloaded_at.as_deref(), now, settle_days)
            {
                needed.push(DayToFetch {
                    day,
                    reason: FetchReason::Unsettled,
                });
            }
        }
        // The window walk is ascending; the outside rows arrive in query order, so
        // the two together are not. Python sorts here and only here.
        needed.sort_by_key(|needed| needed.day);
    }

    needed
}

/// Read the stored day rows for a source inside a window.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn load_day_rows(db: &mut dyn Db, source: &str) -> Result<Vec<DayRow>, DbError> {
    // **Every row for the source, not the window's.** Python reads the window and
    // then, for a source with a settle period, reads everything outside it with a
    // second and deliberately unbounded query; the two together are this set, and
    // a source holds one row a day. Rule 5 of [`days_needing_fetch`] is what needs
    // the outside rows.
    let rows = fetch_all(
        db,
        "SELECT date, status, downloaded_at, last_verified_at FROM download_days \
         WHERE source = ?",
        &[Value::Text(source.to_string())],
    )?;
    Ok(rows
        .iter()
        .map(|r| DayRow {
            date: text(r, "date").unwrap_or_default(),
            status: text(r, "status").unwrap_or_default(),
            downloaded_at: text(r, "downloaded_at"),
            last_verified_at: text(r, "last_verified_at"),
        })
        .collect())
}

fn text(row: &Row, name: &str) -> Option<String> {
    row.get(name)
        .ok()
        .and_then(Value::as_str)
        .map(str::to_string)
}

// ---------------------------------------------------------------------------
// Day status
// ---------------------------------------------------------------------------

/// What to store for a day, and what to tell the caller about it.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct DayOutcome {
    /// `"completed"` or `"failed"`.
    pub status: String,
    /// Lines for `SyncReport`'s `errors` — days that will be retried.
    pub errors: Vec<String>,
    /// Lines for `SyncReport`'s `notes` — days that will not be.
    pub notes: Vec<String>,
}

impl DayOutcome {
    /// Whether the day is recorded as complete.
    #[must_use]
    pub fn is_completed(&self) -> bool {
        self.status == "completed"
    }
}

/// Decide what to store for a day, and what to tell the caller about it.
///
/// Both failure modes here used to be recorded as `completed`, which is
/// durable: [`days_needing_fetch`] does not offer a completed day again once it
/// is in the past *and was fetched after the day was over*, unless
/// `recheck_days` is set — so the records are, for the default configuration,
/// permanently absent.
///
/// **Unknown status.** The convention is `"completed"` or `"failed"`, but it
/// was enforced by a denylist (anything not exactly `"failed"` became
/// `"completed"`), so a fetcher reporting failure in any other spelling had
/// that failure converted into success. `register_source` is a documented
/// extension point, and a third-party fetcher is exactly the caller who will
/// not know the convention.
///
/// **Records that failed to store.** A day whose records raised on the way in
/// is missing them by name. `store_publication` merges, so re-fetching is
/// idempotent and the retry is cheap.
#[must_use]
pub fn resolve_day_status(
    source: &str,
    day: NaiveDate,
    fetch_result: &FetchResult,
    day_failed: i64,
) -> DayOutcome {
    let mut errors: Vec<String> = Vec::new();
    let mut notes: Vec<String> = Vec::new();
    let day_str = day.format("%Y-%m-%d");

    // Spelled as an allowlist rather than `not in (...)`, because a membership
    // test does not narrow a string to the vocabulary and so cannot be checked.
    let mut status = if fetch_result.status == "completed" {
        "completed".to_string()
    } else if fetch_result.status == "failed" {
        "failed".to_string()
    } else {
        errors.push(format!(
            "{source}/{day_str}: fetcher returned unknown status {}; recorded as failed",
            repr_str(&fetch_result.status)
        ));
        "failed".to_string()
    };

    if day_failed != 0 {
        errors.push(format!(
            "{source}/{day_str}: {day_failed} record(s) failed to store"
        ));
        status = "failed".to_string();
    }

    // Only meaningful on a day that completes: a note on a failed day describes
    // a walk that is about to be retried anyway.
    if let Some(note) = fetch_result.note.as_ref().filter(|_| status == "completed") {
        notes.push(format!("{source}/{day_str}: {note}"));
    }

    DayOutcome {
        status,
        errors,
        notes,
    }
}

// ---------------------------------------------------------------------------
// Storage helpers
// ---------------------------------------------------------------------------

/// Insert or update a `download_days` row.
///
/// Runs inside the caller's per-day transaction, so the day's status commits
/// atomically with the day's records.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn upsert_download_day(
    db: &mut dyn Db,
    source: &str,
    day: NaiveDate,
    status: &str,
    record_count: i64,
    now: &str,
) -> Result<(), DbError> {
    execute(
        db,
        "INSERT INTO download_days (source, date, status, record_count, downloaded_at, \
         last_verified_at) VALUES (?, ?, ?, ?, ?, ?) \
         ON CONFLICT (source, date) DO UPDATE SET \
           status = excluded.status, \
           record_count = excluded.record_count, \
           downloaded_at = excluded.downloaded_at, \
           last_verified_at = excluded.last_verified_at",
        &[
            Value::Text(source.to_string()),
            Value::Text(day.format("%Y-%m-%d").to_string()),
            Value::Text(status.to_string()),
            Value::Int(record_count),
            Value::Text(now.to_string()),
            Value::Text(now.to_string()),
        ],
    )?;
    Ok(())
}

/// The parts of `day` a previous run finished, keyed by part key.
///
/// # Errors
///
/// [`LoadPartsError`] when a stored part row cannot be read as a
/// [`PartCheckpoint`]. Raised rather than skipped so one handler records the
/// day, rather than a second copy of that block existing for this case.
pub fn load_day_parts(
    db: &mut dyn Db,
    source: &str,
    day: NaiveDate,
) -> Result<std::collections::BTreeMap<String, PartCheckpoint>, LoadPartsError> {
    let rows = fetch_all(
        db,
        "SELECT part_scheme, part_key, promised, record_count FROM download_day_parts \
         WHERE source = ? AND date = ?",
        &[
            Value::Text(source.to_string()),
            Value::Text(day.format("%Y-%m-%d").to_string()),
        ],
    )
    .map_err(LoadPartsError::Db)?;

    let mut parts = std::collections::BTreeMap::new();
    for row in &rows {
        // `DbValue` is this crate's own boundary type and deliberately not
        // `Serialize`, so the four columns are mapped explicitly rather than
        // handed to `json!`. A `NULL` stays `null`, which
        // `PartCheckpoint::from_json` then refuses by name — the behaviour the
        // strict reader is for.
        let as_json = |name: &str| match row.get(name) {
            Ok(Value::Text(s)) => serde_json::Value::String(s.clone()),
            Ok(Value::Int(i)) => serde_json::Value::from(*i),
            Ok(Value::Real(f)) => serde_json::Value::from(*f),
            _ => serde_json::Value::Null,
        };
        let json = serde_json::json!({
            "part_scheme": as_json("part_scheme"),
            "part_key": as_json("part_key"),
            "promised": as_json("promised"),
            "record_count": as_json("record_count"),
        });
        match PartCheckpoint::from_json(&json) {
            Ok(cp) => {
                parts.insert(cp.part_key.clone(), cp);
            }
            Err(e) => {
                return Err(LoadPartsError::Unreadable(LoadPartsUnreadable(format!(
                    "{source}/{}: a stored part row is unreadable: {e}",
                    day.format("%Y-%m-%d")
                ))))
            }
        }
    }
    Ok(parts)
}

/// Why a day's stored part rows could not be read.
#[derive(Debug)]
pub enum LoadPartsError {
    /// The driver failed.
    Db(DbError),
    /// A row is present but cannot be read as a checkpoint.
    Unreadable(LoadPartsUnreadable),
}

/// A day's part rows are present but unreadable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadPartsUnreadable(pub String);

impl std::fmt::Display for LoadPartsUnreadable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LoadPartsUnreadable {}

impl std::fmt::Display for LoadPartsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadPartsError::Db(e) => write!(f, "{e}"),
            LoadPartsError::Unreadable(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for LoadPartsError {}

/// Record one completed part.
///
/// Runs inside the caller's transaction, so the checkpoint commits atomically
/// with the records it attests to — a checkpoint that outlived a rolled-back
/// batch would make a re-run skip records that were never stored.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn record_day_part(
    db: &mut dyn Db,
    source: &str,
    day: NaiveDate,
    checkpoint: &PartCheckpoint,
    now: &str,
) -> Result<(), DbError> {
    execute(
        db,
        "INSERT INTO download_day_parts (source, date, part_scheme, part_key, promised, \
         record_count, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?) \
         ON CONFLICT (source, date, part_key) DO UPDATE SET \
           part_scheme = excluded.part_scheme, \
           promised = excluded.promised, \
           record_count = excluded.record_count, \
           completed_at = excluded.completed_at",
        &[
            Value::Text(source.to_string()),
            Value::Text(day.format("%Y-%m-%d").to_string()),
            Value::Text(checkpoint.part_scheme.clone()),
            Value::Text(checkpoint.part_key.clone()),
            Value::Int(checkpoint.promised),
            Value::Int(checkpoint.record_count),
            Value::Text(now.to_string()),
        ],
    )?;
    Ok(())
}

/// Drop `day`'s part rows.
///
/// Called when the day completes: the rows describe an unfinished day, so
/// keeping them would grow the table without bound and would make a
/// `recheck_days` re-fetch skip parts it was explicitly asked to redo.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn clear_day_parts(db: &mut dyn Db, source: &str, day: NaiveDate) -> Result<(), DbError> {
    execute(
        db,
        "DELETE FROM download_day_parts WHERE source = ? AND date = ?",
        &[
            Value::Text(source.to_string()),
            Value::Text(day.format("%Y-%m-%d").to_string()),
        ],
    )?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Record conversion
// ---------------------------------------------------------------------------

/// Convert a [`FetchedRecord`] to a [`Publication`].
#[must_use]
pub fn record_to_publication(record: &FetchedRecord) -> Publication {
    let mut pub_ = Publication::new(record.title.clone(), record.source.clone());
    pub_.doi = record.doi.clone();
    pub_.pmid = record.pmid.clone();
    pub_.pmcid = record.pmc_id.clone();
    pub_.abstract_text = record.abstract_text.clone();
    pub_.authors = record.authors.clone();
    pub_.journal = record.journal.clone();
    pub_.publication_date = record.publication_date.clone();
    pub_.publication_types = record.publication_types.clone();
    pub_.keywords = record.keywords.clone();
    pub_.is_open_access = record.is_open_access;
    pub_.license = record.license.clone();
    pub_.sources = vec![record.source.clone()];
    pub_.first_seen_source = record.source.clone();
    pub_
}

/// Return copies of `rows` whose `source` is the record's own.
///
/// Provenance is stamped here rather than in each fetcher because this is the
/// one place that authoritatively knows which source produced the record — a
/// fetcher can forget, and the cost of forgetting is silent: rows land in an
/// unnamed bucket and stop being scoped, which is the cross-source
/// flip-flopping that `source` exists to prevent. Whatever a fetcher may have
/// set is overwritten, which is correct: a row's provenance *is* the source
/// that reported the record carrying it.
#[must_use]
pub fn stamp_grant_source(rows: &[Grant], source: &str) -> Vec<Grant> {
    rows.iter()
        .map(|g| Grant {
            source: source.to_string(),
            ..g.clone()
        })
        .collect()
}

/// As [`stamp_grant_source`], for affiliations.
#[must_use]
pub fn stamp_affiliation_source(
    rows: &[AuthorAffiliation],
    source: &str,
) -> Vec<AuthorAffiliation> {
    rows.iter()
        .map(|a| AuthorAffiliation {
            source: source.to_string(),
            ..a.clone()
        })
        .collect()
}

/// Extract [`FullTextSource`] objects from a [`FetchedRecord`].
///
/// Returns `None` when the record carries none, so `store_publication` is
/// called with an empty slice and leaves the stored rows untouched — an absent
/// `<fullTextUrlList>` means the record did not carry the data, not that the
/// locations were withdrawn.
#[must_use]
pub fn record_to_fulltext_sources(record: &FetchedRecord) -> Option<Vec<FullTextSource>> {
    if record.fulltext_sources.is_empty() {
        return None;
    }
    let mut result = Vec::new();
    for fts in &record.fulltext_sources {
        let url = fts.get("url").and_then(serde_json::Value::as_str);
        let source = fts
            .get("source")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("unknown");
        let format = fts
            .get("format")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("html");
        let version = fts.get("version").and_then(serde_json::Value::as_str);
        let Some(url) = url else {
            continue;
        };
        result.push(FullTextSource::new(
            0, // set by store_publication
            source, url, format,
        ));
        if let Some(last) = result.last_mut() {
            last.version = version.map(str::to_string);
        }
    }
    if result.is_empty() {
        None
    } else {
        Some(result)
    }
}

/// Store `records`, returning `(added, merged, failed)`.
///
/// Runs inside the caller's transaction, so a record that fails rolls back to
/// its own savepoint without losing the batch. One bad record must not lose the
/// batch, so the per-record error is caught — which is exactly why the failure
/// count is reported rather than logged and dropped.
///
/// # Errors
///
/// Only a failure that is not per-record: the caller's own transaction.
pub fn store_records(
    db: &mut dyn Db,
    source: &str,
    records: &[FetchedRecord],
) -> Result<(i64, i64, i64), DbError> {
    let mut added = 0i64;
    let mut merged = 0i64;
    let mut failed = 0i64;
    for record in records {
        let mut pub_ = record_to_publication(record);
        let fts = record_to_fulltext_sources(record).unwrap_or_default();
        let grants = stamp_grant_source(&record.grants, source);
        let affiliations = stamp_affiliation_source(&record.author_affiliations, source);
        match store_publication(&mut *db, &mut pub_, &fts, &grants, &affiliations) {
            Ok(outcome) => match outcome.as_str() {
                "added" => added += 1,
                "merged" => merged += 1,
                _ => {}
            },
            Err(_) => failed += 1,
        }
    }
    Ok((added, merged, failed))
}

/// The default sync report for a run that fetched nothing.
#[must_use]
pub fn empty_report() -> SyncReport {
    SyncReport::default()
}

/// The current UTC instant, exposed so a caller can inject it.
#[must_use]
pub fn utc_now() -> DateTime<Utc> {
    Utc::now()
}

/// Build the day's `download_days` row into a [`DownloadDay`], for a caller
/// reading back what a run wrote.
#[must_use]
pub fn download_day_row(
    source: &str,
    day: NaiveDate,
    status: &str,
    record_count: i64,
    now: &str,
) -> DownloadDay {
    let mut row = DownloadDay::new(
        source,
        day.format("%Y-%m-%d").to_string(),
        status,
        record_count,
    );
    row.downloaded_at = now.to_string();
    row
}

// ---------------------------------------------------------------------------
// Credits and counts a finished day contributes
// ---------------------------------------------------------------------------

/// The records an earlier run stored for the parts this run skipped.
///
/// **Only the skipped ones are credited.** A prior part whose count moved is
/// re-walked, and its records are already in the totals — crediting it as well
/// would double them. And a re-walk that came up short is *not* checkpointed, so
/// "everything this run did not checkpoint" would catch it where this rule does
/// not.
///
/// The purpose is stated by the source: a day fetched across three runs must not
/// be recorded as holding only the last run's share.
///
/// # One cosmetic residue, and it is deliberate
///
/// The skip rule compares a part's *current* count against the stored `promised`,
/// so a part whose count moved **away and back again** is skipped and credited at
/// the `record_count` the earlier run stored — a number describing that range's
/// old contents rather than what is in `publications` now. It moves this row's
/// `record_count` only, which **no day-selection rule reads**, so it is recorded
/// here rather than re-discovered later as a bug.
#[must_use]
pub fn carried_credit(
    prior_parts: &std::collections::BTreeMap<String, PartCheckpoint>,
    skipped_keys: &std::collections::BTreeSet<String>,
) -> i64 {
    prior_parts
        .iter()
        .filter(|(key, _)| skipped_keys.contains(*key))
        .map(|(_, checkpoint)| checkpoint.record_count)
        .sum()
}

/// The `FetchResult` a **failed** day resolves through.
///
/// Python builds this in its `except` block, and its count is the expression that
/// block uses: the parts already flushed **plus** the records still buffered. It
/// is not what the day's row gets — [`day_record_count`] is — because Python
/// computes the row's count after its closing store has folded the buffer in.
#[must_use]
fn failed_fetch_result(
    source: &str,
    day: NaiveDate,
    flushed: (i64, i64, i64),
    buffered: usize,
    error: String,
) -> FetchResult {
    let (added, merged, failed) = flushed;
    FetchResult {
        source: source.to_string(),
        date: day.format("%Y-%m-%d").to_string(),
        record_count: failed_record_count(added, merged, failed, buffered),
        status: "failed".to_string(),
        error: Some(error),
        note: None,
    }
}

/// What to write into `download_days.record_count` for a finished day.
///
/// `added + merged + carried`: the records this run stored, plus the records an
/// earlier run stored for the parts this run skipped. Deliberately **not** the
/// day's own `promised` count, and deliberately not `added + merged` alone —
/// either would make a resumed day report a size it does not have.
#[must_use]
pub fn day_record_count(added: i64, merged: i64, carried: i64) -> i64 {
    added + merged + carried
}

/// The count a **failed** fetch reports.
///
/// The records already flushed by a finished part **plus** the ones still
/// buffered: the buffer alone stopped being the day's whole delivery when the
/// part flush moved to a per-part boundary (#105 review). Reporting the buffer
/// alone would understate a day that failed after several parts were stored.
#[must_use]
pub fn failed_record_count(added: i64, merged: i64, failed: i64, buffered: usize) -> i64 {
    added + merged + failed + buffered as i64
}

/// The error line a day's failure contributes to the report.
///
/// Built the same way everywhere so the two sources of a day's errors — the
/// fetch's own `error` and the day-status resolution's list — read alike.
#[must_use]
pub fn day_error_line(source: &str, date: &str, error: &str) -> String {
    format!("{source}/{date}: {error}")
}

/// The error line for a source with no fetcher.
///
/// A source is absent from `sources_synced` only when no fetcher was found for
/// it — which is different from a source whose days all failed, and the two must
/// read differently in the report.
#[must_use]
pub fn no_fetcher_line(source: &str) -> String {
    format!("No fetcher found for source: {source}")
}

// ---------------------------------------------------------------------------
// The sync loop
// ---------------------------------------------------------------------------

/// What one sync run was asked for.
pub struct SyncRequest<'a> {
    /// Source names to sync, in order.
    pub sources: Vec<String>,
    /// Window start, inclusive.
    pub date_from: NaiveDate,
    /// Window end, inclusive.
    pub date_to: NaiveDate,
    /// Re-fetch completed days older than this many days, when above zero.
    pub recheck_days: i64,
    /// Per-source configuration, passed to each fetcher.
    pub configs: &'a std::collections::BTreeMap<String, std::collections::BTreeMap<String, String>>,
}

/// Assemble the per-source configuration from the legacy parameters.
///
/// `source_configs` supersedes `email` and `api_keys` **when provided**: a caller
/// who supplies it has said what each source wants, and merging the legacy
/// parameters into it would let a stale `email` override their choice.
#[must_use]
pub fn build_source_configs(
    source_configs: Option<
        std::collections::BTreeMap<String, std::collections::BTreeMap<String, String>>,
    >,
    email: &str,
    api_keys: &std::collections::BTreeMap<String, String>,
) -> std::collections::BTreeMap<String, std::collections::BTreeMap<String, String>> {
    if let Some(configs) = source_configs {
        return configs;
    }
    let mut configs: std::collections::BTreeMap<
        String,
        std::collections::BTreeMap<String, String>,
    > = std::collections::BTreeMap::new();
    for (source, key) in api_keys {
        configs
            .entry(source.clone())
            .or_default()
            .insert("api_key".to_string(), key.clone());
    }
    if !email.is_empty() {
        configs
            .entry("openalex".to_string())
            .or_default()
            .insert("email".to_string(), email.to_string());
    }
    configs
}

/// What one sync run did.
///
/// **The source list is the report's** (`SyncReport::sources_synced`). This
/// carried a second `sources_synced` of its own until round 65 — documented as
/// "every source whose sync loop ran to completion" and **never written**, so it
/// read `[]` for every run and the test named for the no-fetcher case passed for
/// a reason that had nothing to do with its name. Two fields for one list is the
/// shape that drifts apart; there is one now, which is Python's.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SyncOutcome {
    /// The report, ready for a caller.
    pub report: SyncReport,
}

/// The day's records, buffered and drained at every part boundary.
///
/// Python's `handle_record` and `flush_part` as one object, because a flush needs
/// the buffer and the connection together — [`FetchSink`] is one `&mut`, which is
/// what lets it hold both.
///
/// **The buffer holds one part, and that is the whole point.** Python's peak on a
/// day too large for one history session is one part's records (#105's day
/// measured 242,216 and its part 500); before this existed the port buffered the
/// whole day, because [`Fetcher::fetch`] returned its records instead of handing
/// them over as they were read. A source with no parts drains once, at the day's
/// close, which is what Python does for it too.
///
/// **It is also where a part is checkpointed, and that was the larger defect.**
/// The port collected `Progress::PartFinished(Completed { checkpoint })` into a
/// `Vec` and dropped it, so **no `download_day_parts` row was ever written by a
/// sync**: an interrupted partitioned day could not resume, and `skipped_keys` —
/// with `carried_credit` behind it — could not fire outside a test. The plan's §9
/// row said the checkpoint still worked and only the memory bound was lost; the
/// checkpoint did not work either.
struct DayBuffer<'a> {
    db: &'a mut dyn Db,
    source: &'a str,
    day: NaiveDate,
    now: &'a str,
    buffered: Vec<FetchedRecord>,
    added: i64,
    merged: i64,
    failed: i64,
    skipped_keys: BTreeSet<String>,
    /// The first part that could not be stored, if any. The walk is not stopped
    /// by it — see [`DayBuffer::flush`] — so it is carried to the day's close,
    /// which is what records the day `failed`.
    flush_error: Option<DbError>,
}

/// What a finished walk left with the day's buffer.
///
/// Taken by [`DayBuffer::finish`] so the connection is released for the day's own
/// writes; the fields are the buffer's, moved out.
struct DayBufferState {
    buffered: Vec<FetchedRecord>,
    added: i64,
    merged: i64,
    failed: i64,
    skipped_keys: BTreeSet<String>,
    flush_error: Option<DbError>,
}

impl<'a> DayBuffer<'a> {
    /// A buffer for one day's walk.
    fn new(db: &'a mut dyn Db, source: &'a str, day: NaiveDate, now: &'a str) -> Self {
        DayBuffer {
            db,
            source,
            day,
            now,
            buffered: Vec::new(),
            added: 0,
            merged: 0,
            failed: 0,
            skipped_keys: BTreeSet::new(),
            flush_error: None,
        }
    }

    /// Store the buffer as one part, and checkpoint it if it earned one.
    ///
    /// **The records are stored whether or not the part earned a checkpoint.**
    /// This is the only thing that empties the buffer, so a version that stored
    /// nothing for a part the fetcher could not vouch for would hold the whole day
    /// in memory exactly when the source is degraded — the peak the flush exists
    /// to remove.
    ///
    /// **The checkpoint is what a part has to earn, in two independent ways**,
    /// both of them Python's: a part that reconciled short of its own promise
    /// arrives as `None`, which the fetcher decides, and a part holding a record
    /// that would not store is not checkpointed here. Both are one rule — the
    /// failure records the day `failed`, so the day is re-offered, and a
    /// checkpoint written beside the gap would make the retry skip the one part
    /// holding it, losing that record silently and permanently.
    ///
    /// A database failure is **remembered rather than propagated**, because a
    /// [`FetchSink`] method cannot return one and a walk must not be trusted to
    /// stop: the buffer keeps the part's records, no further part is flushed, and
    /// the day closes as `failed` — which is Python's outcome too, where the
    /// exception leaves `flush_part` through the fetcher into the per-day handler.
    /// One difference is stated rather than hidden: that exception *stops the
    /// fetch* there, and here the walk continues. The day is `failed` either way
    /// and the extra records are stored idempotently.
    fn flush(&mut self, checkpoint: Option<&PartCheckpoint>) {
        if self.flush_error.is_some() {
            return;
        }
        let records = std::mem::take(&mut self.buffered);
        match self.store_part(&records, checkpoint) {
            Ok((added, merged, failed)) => {
                self.added += added;
                self.merged += merged;
                self.failed += failed;
            }
            Err(error) => {
                // Put them back: the close stores them, and its failure is the
                // caller's own — which is where Python's would escape from too.
                self.buffered = records;
                self.flush_error = Some(error);
            }
        }
    }

    /// One transaction for a part's records **and** its checkpoint, so a
    /// checkpoint can never attest to records a rollback discarded.
    fn store_part(
        &mut self,
        records: &[FetchedRecord],
        checkpoint: Option<&PartCheckpoint>,
    ) -> Result<(i64, i64, i64), DbError> {
        let mut tx = self.db.begin()?;
        let counts = store_records(&mut *tx, self.source, records)?;
        // `counts.2` is the failed count: a part that lost a record is not
        // checkpointed even when the fetcher vouched for it.
        if let Some(checkpoint) = checkpoint.filter(|_| counts.2 == 0) {
            record_day_part(&mut *tx, self.source, self.day, checkpoint, self.now)?;
        }
        tx.commit()?;
        Ok(counts)
    }

    /// What the buffer holds now that the walk is over, releasing the connection.
    fn finish(self) -> DayBufferState {
        DayBufferState {
            buffered: self.buffered,
            added: self.added,
            merged: self.merged,
            failed: self.failed,
            skipped_keys: self.skipped_keys,
            flush_error: self.flush_error,
        }
    }
}

impl FetchSink for DayBuffer<'_> {
    fn record(&mut self, record: FetchedRecord) {
        self.buffered.push(record);
    }

    fn progress(&mut self, progress: Progress) {
        match progress {
            // Every part that finished, not only the ones that earned a
            // checkpoint: storing is what empties the buffer, and a part that came
            // up short still holds records the day must not keep in memory.
            Progress::PartFinished(PartDisposition::Completed { checkpoint }) => {
                self.flush(checkpoint.as_ref());
            }
            Progress::PartFinished(PartDisposition::Skipped { part_key }) => {
                self.skipped_keys.insert(part_key);
            }
            // A page boundary is not a part boundary: nothing is stored for it,
            // and the count it carries already reaches the caller.
            Progress::Page { .. } => {}
        }
    }
}

/// Sync one source's days, storing each day as it completes.
///
/// This is the per-source arm of `sync`. The caller owns the client and the
/// registry, because those are environment rather than policy.
///
/// # Errors
///
/// Only a failure that is **not** attributable to one day: the caller's own
/// database. A day that fails is recorded as a failed day and the loop moves on,
/// which is the whole point of the per-day transaction.
pub fn sync_source(
    db: &mut dyn Db,
    source: &str,
    fetcher: &dyn Fetcher,
    request: &SyncRequest<'_>,
    now: DateTime<Utc>,
    report: &mut SyncReport,
    settle_days: u32,
) -> Result<(), DbError> {
    let rows = load_day_rows(db, source)?;
    let days = days_needing_fetch(
        &rows,
        request.date_from,
        request.date_to,
        request.recheck_days,
        now,
        settle_days,
    );
    if days.is_empty() {
        report.sources_synced.push(source.to_string());
        return Ok(());
    }

    for needed in days {
        let day = needed.day;
        // Built once: it is the day row's `completed_at` and every part row's,
        // and the parts are written from inside the walk now.
        let now_str = now.to_string();
        // The totals the day's own store adds to; the parts' are the buffer's,
        // folded in once the walk returns.
        let mut day_added = 0i64;
        let mut day_merged = 0i64;
        let mut day_failed = 0i64;

        // Read this day's checkpoints before the fetch, guarded: anything raised
        // here would leave the caller without a report for every source, so it
        // fails the **day** instead. Fetching from scratch would be correct, but
        // recording the day `completed` on a run that could not read what an
        // earlier run stored is recording success over an unknown — and
        // `completed` is never re-offered.
        let prior_parts = match load_day_parts(db, source, day) {
            Ok(parts) => parts,
            Err(e) => {
                let message = format!("could not read download_day_parts for {source}/{day}: {e}");
                report
                    .errors
                    .push(day_error_line(source, &day.to_string(), &message));
                upsert_download_day(db, source, day, "failed", 0, &now_str)?;
                report.days_processed += 1;
                continue;
            }
        };

        let mut fetch_request = FetchRequest::new(day);
        if let Some(config) = request.configs.get(source) {
            fetch_request.config = config.clone();
        }
        if !prior_parts.is_empty() {
            fetch_request.resume = Some(crate::publications::fetchers::ResumeState {
                completed_parts: prior_parts.clone(),
            });
        }

        // The walk hands each record to the buffer as it is read, and a part
        // boundary drains it — so the peak is one part rather than the day, and a
        // part that finished is checkpointed in the same transaction as the
        // records it vouches for. Both are `DayBuffer`'s; see its doc for what
        // each of them used to be.
        let mut buffer = DayBuffer::new(&mut *db, source, day, &now_str);
        let outcome = fetcher.fetch(&fetch_request, &mut buffer);
        let DayBufferState {
            buffered,
            added,
            merged,
            failed,
            skipped_keys,
            flush_error,
        } = buffer.finish();
        day_added += added;
        day_merged += merged;
        day_failed += failed;
        // What the parts flushed, read **before** the day's own store below folds
        // the buffer in: a failed day's `FetchResult` carries the flushed count
        // plus the records still buffered, which is Python's expression and would
        // double-count after the fold.
        let flushed = (day_added, day_merged, day_failed);
        let buffered_len = buffered.len();
        // **One transaction for whatever the parts did not already put away**, so
        // the records and the day's status row commit together — which is what the
        // storage helpers' own docs mean by "the caller's per-day transaction",
        // and what Python's `with transaction(conn)` wraps. Nothing opened it
        // until now: each `store_publication` committed on its own, so a crash
        // between the records and the status row left the day unrecorded (harmless
        // — it is re-offered and merges — but not the atomicity the docs claimed).
        //
        // This runs on **every** path, including a walk that failed outright: the
        // records the walk delivered before it failed are the day's delivery, and
        // Python stores them in its closing block for the same reason.
        let mut tx = db.begin()?;
        let (added, merged, failed) = store_records(&mut *tx, source, &buffered)?;
        day_added += added;
        day_merged += merged;
        day_failed += failed;

        let carried = carried_credit(&prior_parts, &skipped_keys);

        // **Every path then resolves the day through one expression**, which is
        // Python's shape: its `except` builds a `FetchResult(status="failed")` and
        // the code after the handler calls `_resolve_day_status`, writes the row
        // and appends the fetch's own error — for a failure as much as for a
        // success. Two things follow that the port used to miss: a failed day's
        // row carries `added + merged + carried` rather than the failure's own
        // count, and a day whose records failed to store also carries the
        // `record(s) failed to store` line.
        //
        // A part that could not be stored is a failure of the same shape, reached
        // in Python by raising out of `flush_part` into the handler. Its message is
        // not: `{type}: {message}` needs the driver's exception class, and a
        // `DbError` does not carry one (§9).
        let fetch_result = match (flush_error, &outcome) {
            (Some(error), _) => {
                failed_fetch_result(source, day, flushed, buffered_len, error.to_string())
            }
            (None, Err(error)) => failed_fetch_result(
                source,
                day,
                flushed,
                buffered_len,
                format!("{}: {error}", error_type_name(error)),
            ),
            (None, Ok(fetch_outcome)) => fetch_outcome.to_fetch_result(source, day),
        };

        let resolved = resolve_day_status(source, day, &fetch_result, day_failed);
        let count = day_record_count(day_added, day_merged, carried);
        upsert_download_day(&mut *tx, source, day, &resolved.status, count, &now_str)?;
        if resolved.is_completed() {
            // The day is complete, so its part rows go with it: the same
            // transaction that loses any checkpoint the day no longer needs is
            // what makes a completed day never re-offered.
            clear_day_parts(&mut *tx, source, day)?;
        }
        tx.commit()?;

        report.days_processed += 1;
        report.records_added += day_added;
        report.records_merged += day_merged;
        report.records_failed += day_failed;
        if let Some(error) = fetch_result.error.as_ref() {
            report
                .errors
                .push(day_error_line(source, &day.to_string(), error));
        }
        report.errors.extend(resolved.errors);
        report.notes.extend(resolved.notes);
    }

    report.sources_synced.push(source.to_string());
    Ok(())
}

/// Run a sync over several sources.
///
/// The registry is what resolves a fetcher per source; a source with none
/// contributes an error and is **absent from `sources_synced`**, which is
/// different from a source whose days all failed.
///
/// # Errors
///
/// Only a database failure outside a day. A day's own failure is recorded and
/// the run continues.
pub fn sync(
    db: &mut dyn Db,
    registry: &crate::publications::fetchers::Registry,
    request: &SyncRequest<'_>,
    now: DateTime<Utc>,
) -> Result<SyncOutcome, DbError> {
    let mut outcome = SyncOutcome::default();

    // The future-window note comes first, because it describes the whole run
    // rather than any one day.
    if let Some(note) = note_unreachable_days(request.date_to, now.date_naive()) {
        outcome.report.notes.push(note);
    }

    for source in &request.sources {
        let Ok(fetcher) = registry.fetcher(source) else {
            outcome.report.errors.push(no_fetcher_line(source));
            continue;
        };
        // `0` for a source with no descriptor, which is Python's
        // `_source_settle_days` for a name reached through `_fetcher_override`. A
        // *declared* period that cannot be used skips the source with a line
        // rather than guessing one: outside every per-day handler, an escape here
        // would cost the whole run its report, and guessing would either lose the
        // period or invent one.
        let settle_days = match registry.descriptor(source) {
            Ok(descriptor) => match descriptor.check_settle_days() {
                Ok(days) => days,
                Err(error) => {
                    outcome
                        .report
                        .errors
                        .push(format!("{source}: no day selected: {error}"));
                    continue;
                }
            },
            Err(_) => 0,
        };
        sync_source(
            db,
            source,
            fetcher,
            request,
            now,
            &mut outcome.report,
            settle_days,
        )?;
    }

    Ok(outcome)
}

/// The Python exception name a fetcher error corresponds to.
///
/// [`crate::publications::fetchers::FetchError::Transport`] carries why the
/// *base* class answers for a transport failure (#361).
fn error_type_name(error: &crate::publications::fetchers::FetchError) -> &'static str {
    match error {
        crate::publications::fetchers::FetchError::Transport(_) => "TransportError",
        crate::publications::fetchers::FetchError::HttpStatus { .. } => "HTTPStatusError",
        crate::publications::fetchers::FetchError::Malformed(_) => "ValueError",
        crate::publications::fetchers::FetchError::Config(_) => "ValueError",
        crate::publications::fetchers::FetchError::ResumeUnreadable(_) => "ValueError",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{fetch_scalar, open_memory};
    use crate::publications::schema::ensure_schema;

    fn schema_db() -> Box<dyn Db> {
        let mut conn = open_memory().expect("in-memory sqlite");
        ensure_schema(&mut conn).expect("schema");
        Box::new(conn)
    }

    /// A count, so an assertion about what the day stored reads as one.
    fn count(db: &mut dyn Db, sql: &str) -> i64 {
        match fetch_scalar(db, sql, &[]).expect("scalar") {
            Some(Value::Int(i)) => i,
            other => panic!("expected an integer count, got {other:?}"),
        }
    }

    fn record(title: &str) -> FetchedRecord {
        FetchedRecord::new(title, "pubmed")
    }

    fn checkpoint(key: &str, records: usize) -> PartCheckpoint {
        PartCheckpoint {
            part_scheme: "edat-range".to_string(),
            part_key: key.to_string(),
            promised: records as i64,
            record_count: records as i64,
        }
    }

    fn day() -> NaiveDate {
        NaiveDate::from_ymd_opt(2024, 6, 10).expect("date")
    }

    fn finish(key: Option<&str>, records: usize) -> Progress {
        Progress::PartFinished(PartDisposition::Completed {
            checkpoint: key.map(|key| checkpoint(key, records)),
        })
    }

    /// **A part boundary stores the buffer and empties it**, which is the whole
    /// reason the sink exists: the peak is one part rather than the day.
    #[test]
    fn a_part_boundary_stores_the_buffer_and_empties_it() {
        let mut conn = schema_db();
        let mut buffer = DayBuffer::new(&mut *conn, "pubmed", day(), "2024-06-11T12:00:00Z");
        buffer.record(record("One"));
        buffer.record(record("Two"));
        assert_eq!(buffer.buffered.len(), 2, "buffered before the boundary");

        buffer.progress(finish(Some("a"), 2));

        assert!(
            buffer.buffered.is_empty(),
            "the part's records belong to the store now, not to the day's memory"
        );
        assert_eq!(buffer.added, 2);
        assert_eq!(
            count(&mut *conn, "SELECT COUNT(*) FROM publications"),
            2,
            "the part was stored"
        );
        assert_eq!(
            count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
            1,
            "and checkpointed in the same transaction"
        );
    }

    /// **A part that came up short is stored but not checkpointed.** The day is
    /// recorded `failed` and re-offered, and a checkpoint written beside the gap
    /// would make the retry skip the one part holding it.
    #[test]
    fn a_part_with_no_checkpoint_is_stored_but_not_checkpointed() {
        let mut conn = schema_db();
        let mut buffer = DayBuffer::new(&mut *conn, "pubmed", day(), "2024-06-11T12:00:00Z");
        buffer.record(record("One"));

        buffer.progress(finish(None, 1));

        assert!(buffer.buffered.is_empty(), "storing is what drains");
        assert_eq!(buffer.added, 1);
        assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 1);
        assert_eq!(
            count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
            0,
            "a part that did not reconcile earns no checkpoint"
        );
    }

    /// **And a part holding a record that would not store is not checkpointed
    /// either** — the second, independent way a part fails to earn one. The
    /// records are still stored: not draining here would hold the whole day in
    /// memory exactly when the source is degraded.
    #[test]
    fn a_part_holding_an_unstoreable_record_is_not_checkpointed() {
        let mut conn = schema_db();
        // Making every record fail to store, deterministically and without
        // breaking the connection: `store_records` catches a record's own failure,
        // which is exactly why the failure count has to be read back and acted on
        // here. `download_day_parts` survives, so "was a checkpoint written?" is
        // answerable.
        execute(&mut *conn, "DROP TABLE publications", &[]).expect("drop the table");
        let mut buffer = DayBuffer::new(&mut *conn, "pubmed", day(), "2024-06-11T12:00:00Z");
        buffer.record(record("One"));

        buffer.progress(finish(Some("a"), 1));

        assert!(buffer.buffered.is_empty(), "a failed part still drains");
        assert_eq!(buffer.added, 0);
        assert_eq!(buffer.failed, 1);
        assert_eq!(
            count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
            0,
            "a checkpoint beside a failed record would hide it on the retry"
        );
    }

    /// A skipped part is **remembered, not stored**: it carries credit, and its
    /// records were stored by the run that walked it.
    #[test]
    fn a_skipped_part_is_remembered_and_stores_nothing() {
        let mut conn = schema_db();
        let mut buffer = DayBuffer::new(&mut *conn, "pubmed", day(), "2024-06-11T12:00:00Z");
        buffer.progress(Progress::PartFinished(PartDisposition::Skipped {
            part_key: "a".to_string(),
        }));

        assert!(buffer.skipped_keys.contains("a"));
        assert_eq!(count(&mut *conn, "SELECT COUNT(*) FROM publications"), 0);
        assert_eq!(
            count(&mut *conn, "SELECT COUNT(*) FROM download_day_parts"),
            0
        );
    }

    /// The buffer survives the walk, so the day's own store gets the tail — and
    /// the parts' totals come back with it.
    #[test]
    fn the_tail_is_what_the_day_still_holds() {
        let mut conn = schema_db();
        let mut buffer = DayBuffer::new(&mut *conn, "pubmed", day(), "2024-06-11T12:00:00Z");
        buffer.record(record("One"));
        buffer.record(record("Two"));
        buffer.progress(finish(Some("a"), 1));
        buffer.progress(finish(Some("b"), 1));

        let state = buffer.finish();

        assert!(
            state.buffered.is_empty(),
            "both parts were drained; nothing is left for the day"
        );
        assert_eq!((state.added, state.merged, state.failed), (2, 0, 0));
        assert!(state.flush_error.is_none());
    }
}
