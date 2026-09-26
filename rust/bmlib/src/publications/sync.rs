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

use chrono::{DateTime, Duration, FixedOffset, NaiveDate, NaiveDateTime, Utc};

use crate::db::operations::{execute, fetch_all};
use crate::db::{Db, DbError, Row, Value};
use crate::publications::fetchers::{FetchRequest, Fetcher, PartDisposition, Progress};
use crate::publications::models::FullTextSource;
use crate::publications::models::{
    AuthorAffiliation, DownloadDay, FetchResult, FetchedRecord, Grant, PartCheckpoint, Publication,
    SyncReport,
};
use crate::publications::storage::store_publication;

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
) -> bool {
    let Some(fetched_at) = read_aware_timestamp(downloaded_at) else {
        return false;
    };
    if fetched_at > now + Duration::minutes(CLOCK_SKEW_TOLERANCE_MINUTES) {
        return false;
    }
    let day_over_everywhere = (day + Duration::days(1))
        .and_hms_opt(DAY_ENDS_EVERYWHERE_AT_UTC_HOUR, 0, 0)
        .map(|naive| naive.and_utc());
    day_over_everywhere.is_some_and(|boundary| fetched_at >= boundary)
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
}

/// A day that needs fetching, with why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DayToFetch {
    /// The day.
    pub day: NaiveDate,
    /// Why it is being offered.
    pub reason: FetchReason,
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
                } else if !day_was_over_when_fetched(day, row.downloaded_at.as_deref(), now) {
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
    needed
}

/// Read the stored day rows for a source inside a window.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn load_day_rows(
    db: &mut dyn Db,
    source: &str,
    date_from: NaiveDate,
    date_to: NaiveDate,
) -> Result<Vec<DayRow>, DbError> {
    let rows = fetch_all(
        db,
        "SELECT date, status, downloaded_at, last_verified_at FROM download_days \
         WHERE source = ? AND date >= ? AND date <= ?",
        &[
            Value::Text(source.to_string()),
            Value::Text(date_from.format("%Y-%m-%d").to_string()),
            Value::Text(date_to.format("%Y-%m-%d").to_string()),
        ],
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
            python_repr_str(&fetch_result.status)
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

/// Python's `repr` for a string, which is single-quoted.
fn python_repr_str(value: &str) -> String {
    format!("'{value}'")
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
/// [`DayPartsUnreadable`] when a stored part row cannot be read as a
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
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SyncOutcome {
    /// Every source whose sync loop ran to completion.
    ///
    /// Includes sources whose individual days failed: a fetcher error records a
    /// failed day and moves on. A source is absent only when no fetcher was found
    /// for it.
    pub sources_synced: Vec<String>,
    /// The report, ready for a caller.
    pub report: SyncReport,
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
) -> Result<(), DbError> {
    let rows = load_day_rows(db, source, request.date_from, request.date_to)?;
    let days = days_needing_fetch(
        &rows,
        request.date_from,
        request.date_to,
        request.recheck_days,
        now,
    );
    if days.is_empty() {
        report.sources_synced.push(source.to_string());
        return Ok(());
    }

    for needed in days {
        let day = needed.day;
        let mut day_added = 0i64;
        let mut day_merged = 0i64;
        let mut day_failed = 0i64;
        let mut skipped_keys: std::collections::BTreeSet<String> =
            std::collections::BTreeSet::new();

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
                upsert_download_day(db, source, day, "failed", 0, &now.to_string())?;
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

        // A part boundary arrives as `Progress::PartFinished`, and this is where
        // the skipped keys — and therefore the carried credit — come from.
        let mut part_events: Vec<Progress> = Vec::new();
        let mut on_progress = |p: Progress| part_events.push(p);
        let outcome = fetcher.fetch(&fetch_request, &mut on_progress);
        for event in part_events {
            if let Progress::PartFinished(PartDisposition::Skipped { part_key }) = event {
                skipped_keys.insert(part_key);
            }
        }

        // **A known gap, stated rather than hidden.** Python stores the buffer
        // *per part* (`flush_part`), so its peak memory on a day too large for one
        // session is one part's records. This port stores it once, after the
        // fetch returns, and its peak is the day's — on the 242,216-record day
        // measured for #105, the difference between 500 records and the whole
        // day. That is precisely the peak the per-part flush exists to remove,
        // and the per-part *checkpoint* still works (the skipped keys above are
        // collected from the walk), so what is lost is the memory bound, not the
        // resume.
        //
        // Closing it needs the records to arrive through a callback the caller
        // supplies, so a part boundary can drain them — `Fetcher::fetch` takes
        // only `on_progress` today and returns its records in `FetchOutcome`.
        // That is a change to `Fetcher` and to all three fetchers, and it is
        // deliberately not made here: it would rewrite working, mutation-tested
        // walk loops for a bound that only bites on days of ~240k records. See
        // the plan's Phase 5.
        let buffered: Vec<FetchedRecord> = match outcome.as_ref() {
            Ok(fetch_outcome) => fetch_outcome.records.clone(),
            // A fetch that failed outright stored nothing, so there is nothing to
            // flush; the day's count below still reports what was buffered, which
            // is the empty buffer.
            Err(_) => Vec::new(),
        };
        let buffered_len = buffered.len();
        let (added, merged, failed) = store_records(db, source, &buffered)?;
        day_added += added;
        day_merged += merged;
        day_failed += failed;

        let carried = carried_credit(&prior_parts, &skipped_keys);

        match outcome {
            Ok(fetch_outcome) => {
                let fetch_result = fetch_outcome.to_fetch_result(source, day);
                let outcome = resolve_day_status(source, day, &fetch_result, day_failed);
                let count = day_record_count(day_added, day_merged, carried);
                upsert_download_day(db, source, day, &outcome.status, count, &now.to_string())?;
                report.days_processed += 1;
                report.records_added += day_added;
                report.records_merged += day_merged;
                report.records_failed += day_failed;
                if let Some(error) = fetch_result.error.as_ref() {
                    report
                        .errors
                        .push(day_error_line(source, &day.to_string(), error));
                }
                // Read the status **before** the notes move out of `outcome`,
                // which is a partial move.
                let completed = outcome.is_completed();
                report.errors.extend(outcome.errors);
                report.notes.extend(outcome.notes);
                if completed {
                    // The day is complete, so its part rows go with it: the same
                    // transaction that loses any checkpoint the day no longer
                    // needs is what makes a completed day never re-offered.
                    clear_day_parts(db, source, day)?;
                }
            }
            Err(error) => {
                let message = format!("{}: {error}", error_type_name(&error));
                let count = failed_record_count(day_added, day_merged, day_failed, buffered_len);
                upsert_download_day(db, source, day, "failed", count, &now.to_string())?;
                report.days_processed += 1;
                report.records_added += day_added;
                report.records_merged += day_merged;
                report.records_failed += day_failed;
                report
                    .errors
                    .push(day_error_line(source, &day.to_string(), &message));
            }
        }
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
        sync_source(db, source, fetcher, request, now, &mut outcome.report)?;
    }

    Ok(outcome)
}

/// The Python exception name a fetcher error corresponds to.
fn error_type_name(error: &crate::publications::fetchers::FetchError) -> &'static str {
    match error {
        crate::publications::fetchers::FetchError::Transport(_) => "RemoteProtocolError",
        crate::publications::fetchers::FetchError::Malformed(_) => "ValueError",
        crate::publications::fetchers::FetchError::Config(_) => "ValueError",
        crate::publications::fetchers::FetchError::ResumeUnreadable(_) => "ValueError",
    }
}
