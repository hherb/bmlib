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

//! Sync day rules — the oracle and the named tests.
//!
//! The corpus (75 cases) diffs every rule against Python at a **stated**
//! instant, which is the one structural difference from the Python module: its
//! tests control the clock by monkeypatching, and this port takes the instant
//! as a parameter. A rule that reads a global clock is a rule whose tests are
//! about the clock.
//!
//! Every rule here fails closed, and the reason is the same for all of them: an
//! uncertain day costs one merged re-fetch, while a day wrongly called done is
//! **permanently missing**.

use bmlib::publications::models::FetchResult;
use bmlib::publications::sync::{
    day_over_everywhere, day_was_over_when_fetched, days_needing_fetch, note_unreachable_days,
    read_aware_timestamp, read_verification_date, resolve_day_status, validate_window, DayRow,
    FetchReason, WindowError, CLOCK_SKEW_TOLERANCE_MINUTES, DAY_ENDS_EVERYWHERE_AT_UTC_HOUR,
};
use chrono::{DateTime, FixedOffset, NaiveDate, SecondsFormat, Utc};
use serde_json::Value;

const CASES: &str = include_str!("data/sync_cases.json");
const EXPECTED: &str = include_str!("data/sync_expected.json");

fn now_of(raw: &str) -> DateTime<Utc> {
    DateTime::parse_from_rfc3339(raw)
        .expect("corpus instant")
        .with_timezone(&Utc)
}

/// The instant as Python's `datetime.isoformat()` writes it — offset preserved.
fn iso_offset(dt: DateTime<FixedOffset>) -> String {
    dt.to_rfc3339_opts(SecondsFormat::AutoSi, false)
}

fn day_of(raw: &str) -> NaiveDate {
    NaiveDate::parse_from_str(raw, "%Y-%m-%d").expect("corpus date")
}

/// The instant as Python's `datetime.isoformat()` writes it.
fn iso(dt: DateTime<Utc>) -> String {
    dt.to_rfc3339_opts(SecondsFormat::AutoSi, false)
}

fn rows_of(spec: &Value) -> Vec<DayRow> {
    spec.as_array()
        .map(|a| {
            a.iter()
                .map(|r| DayRow {
                    date: r["date"].as_str().unwrap_or_default().to_string(),
                    status: r["status"].as_str().unwrap_or_default().to_string(),
                    downloaded_at: r
                        .get("downloaded_at")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    last_verified_at: r
                        .get("last_verified_at")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                })
                .collect()
        })
        .unwrap_or_default()
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    // Only the clock-reading cases carry `now`, so it is resolved where it is
    // used rather than up front.
    let now = || now_of(args["now"].as_str().unwrap_or_default());

    match fn_name {
        "day_was_over" => {
            let day = day_of(args["day"].as_str().unwrap_or_default());
            let at = args.get("downloaded_at").and_then(Value::as_str);
            serde_json::json!(day_was_over_when_fetched(day, at, now()))
        }
        "read_aware" => match read_aware_timestamp(args.get("value").and_then(Value::as_str)) {
            Some(dt) => serde_json::json!(iso_offset(dt)),
            None => Value::Null,
        },
        "read_verification" => {
            match read_verification_date(args.get("value").and_then(Value::as_str)) {
                Some(d) => serde_json::json!(d.format("%Y-%m-%d").to_string()),
                None => Value::Null,
            }
        }
        "note_unreachable" => {
            let date_to = day_of(args["date_to"].as_str().unwrap_or_default());
            match note_unreachable_days(date_to, now().date_naive()) {
                Some(note) => serde_json::json!(note),
                None => Value::Null,
            }
        }
        "validate_window" => {
            let date_to = day_of(args["date_to"].as_str().unwrap_or_default());
            let recheck = args["recheck_days"].as_i64().unwrap_or(0);
            // The **corpus's** today, not the wall clock. This rule's only use
            // of the clock is to bound `recheck_days` against the start of the
            // calendar, and "how many days since 0001-01-01" is a property of
            // when the test runs unless it is pinned. Python's
            // `_validate_window` reads `date.today()` and the dumper swaps in a
            // frozen `date`, so both sides read the case's `now` — a real
            // `Utc::now()` here would compare two different facts and, worse,
            // expire the committed expectation at the next midnight.
            let today = now().date_naive();
            match validate_window(date_to, recheck, today) {
                Ok(()) => serde_json::json!({"ok": true}),
                Err(e) => serde_json::json!({"ok": false, "error": e.to_string()}),
            }
        }
        "resolve_status" => {
            let mut result = FetchResult {
                source: "s".to_string(),
                date: "2024-01-02".to_string(),
                record_count: 0,
                status: args["status"].as_str().unwrap_or_default().to_string(),
                error: None,
                note: args.get("note").and_then(Value::as_str).map(str::to_string),
            };
            if result.note.is_none() {
                result.note = None;
            }
            let day = day_of("2024-01-02");
            let outcome =
                resolve_day_status("s", day, &result, args["day_failed"].as_i64().unwrap_or(0));
            serde_json::json!({
                "status": outcome.status,
                "errors": outcome.errors,
                "notes": outcome.notes,
            })
        }
        "days_needing" => {
            let rows = rows_of(&args["rows"]);
            let needed = days_needing_fetch(
                &rows,
                day_of(args["date_from"].as_str().unwrap_or_default()),
                day_of(args["date_to"].as_str().unwrap_or_default()),
                args["recheck_days"].as_i64().unwrap_or(0),
                now(),
            );
            serde_json::json!(needed
                .iter()
                .map(|d| d.day.format("%Y-%m-%d").to_string())
                .collect::<Vec<_>>())
        }
        other => panic!("unknown fn {other:?}"),
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
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
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

// ---------------------------------------------------------------------------
// The durability boundary
// ---------------------------------------------------------------------------

/// **The boundary is 12:00 UTC on the day after the day described**, and the
/// hour is not a safety margin: day *D* finishes last in UTC-12, whose midnight
/// is noon UTC on *D+1*.
#[test]
fn noon_utc_on_the_following_day_is_the_boundary() {
    let day = day_of("2024-06-10");
    let now = now_of("2024-06-15T12:00:00+00:00");

    assert!(day_was_over_when_fetched(
        day,
        Some("2024-06-11T12:00:00+00:00"),
        now
    ));
    assert!(!day_was_over_when_fetched(
        day,
        Some("2024-06-11T11:59:59+00:00"),
        now
    ));
    // A fetch during the day itself is never durable, whatever the hour.
    assert!(!day_was_over_when_fetched(
        day,
        Some("2024-06-10T23:59:59+00:00"),
        now
    ));
}

/// The boundary is the same instant whatever offset the timestamp carries, so
/// an eastern offset that lands before noon UTC is **not** durable. Comparing
/// local wall-clock text instead of instants would call it done.
#[test]
fn the_boundary_is_an_instant_not_a_wall_clock() {
    let day = day_of("2024-06-10");
    let now = now_of("2024-06-15T12:00:00+00:00");
    // 12:00+13:00 is 23:00 UTC on the *10th* — before the boundary.
    assert!(!day_was_over_when_fetched(
        day,
        Some("2024-06-11T12:00:00+13:00"),
        now
    ));
    // 07:00-05:00 is 12:00 UTC on the 11th — exactly on it.
    assert!(day_was_over_when_fetched(
        day,
        Some("2024-06-11T07:00:00-05:00"),
        now
    ));
}

/// A timestamp that **cannot be read** fails closed. `downloaded_at` is
/// `NOT NULL` and bmlib has only ever written an aware UTC ISO value, so
/// anything else came from elsewhere; reading it as durable would lose the day
/// permanently, while the re-fetch it costs is merged and rewrites the column.
#[test]
fn an_unreadable_timestamp_fails_closed() {
    let day = day_of("2024-06-10");
    let now = now_of("2024-06-15T12:00:00+00:00");
    for bad in [
        None,
        Some(""),
        Some("not-a-time"),
        Some("2024/06/11 12:00"),
        // Readable, but says nothing about *when* — the case Python's
        // `fromisoformat` accepts and the aware-only guard must refuse.
        Some("2024-06-11T12:00:00"),
        Some("2024-06-11"),
    ] {
        assert!(
            !day_was_over_when_fetched(day, bad, now),
            "{bad:?} must not read as durable"
        );
    }
}

/// A timestamp beyond `now` cannot be *true*, so it fails closed too. Without
/// this bound the guard is loud about an unparseable value and silent about one
/// asserting the day was fetched tomorrow — which is #95's own failure mode:
/// permanent, invisible loss.
#[test]
fn a_timestamp_in_the_future_fails_closed() {
    let day = day_of("2024-06-14");
    let now = now_of("2024-06-15T12:00:00+00:00");

    assert_eq!(CLOCK_SKEW_TOLERANCE_MINUTES, 5);
    // Within the tolerance is believed.
    assert!(day_was_over_when_fetched(
        day,
        Some("2024-06-15T12:05:00+00:00"),
        now
    ));
    // One second past it is not.
    assert!(!day_was_over_when_fetched(
        day,
        Some("2024-06-15T12:05:01+00:00"),
        now
    ));
    assert!(!day_was_over_when_fetched(
        day,
        Some("2024-06-15T13:00:00+00:00"),
        now
    ));
}

/// The exact boundary instant, exposed for a caller that wants it.
#[test]
fn the_boundary_instant_is_available() {
    let boundary = day_over_everywhere(day_of("2024-06-10")).expect("in range");
    assert_eq!(iso(boundary), "2024-06-11T12:00:00+00:00");
    assert_eq!(DAY_ENDS_EVERYWHERE_AT_UTC_HOUR, 12);
}

// ---------------------------------------------------------------------------
// The two timestamp readers differ on purpose
// ---------------------------------------------------------------------------

/// A **naive** timestamp is unusable for the durability rule and perfectly
/// usable for verification. The two readers exist because conflating them
/// either loses the day or re-fetches the whole window on every run.
#[test]
fn a_naive_timestamp_is_unusable_for_durability_but_readable_for_verification() {
    assert_eq!(read_aware_timestamp(Some("2024-06-11T12:00:00")), None);
    assert_eq!(
        read_verification_date(Some("2024-06-11T12:00:00")),
        Some(day_of("2024-06-11")),
        "only the calendar date is wanted here"
    );
}

/// An offset-carrying timestamp normalises to UTC for the aware reader.
#[test]
fn an_offset_timestamp_normalises_to_utc() {
    let dt = read_aware_timestamp(Some("2024-06-11T07:00:00-05:00")).expect("readable");
    assert_eq!(dt, now_of("2024-06-11T12:00:00+00:00"));
}

/// A stored `NULL` verification is **not** unusable — it is the documented
/// "never verified" state, which day selection already answers by rechecking.
#[test]
fn a_null_verification_is_a_known_state_not_an_unreadable_one() {
    assert_eq!(read_verification_date(None), None);
    assert_eq!(read_verification_date(Some("nonsense")), None);
}

// ---------------------------------------------------------------------------
// Day selection
// ---------------------------------------------------------------------------

fn row(day: &str, status: &str, downloaded_at: Option<&str>, verified: Option<&str>) -> DayRow {
    DayRow {
        date: day.to_string(),
        status: status.to_string(),
        downloaded_at: downloaded_at.map(str::to_string),
        last_verified_at: verified.map(str::to_string),
    }
}

/// **Rule 2 is an allowlist, not a denylist.** Anything that is not exactly
/// `"completed"` is offered again. Read as `== "failed"` this is the mirror of
/// the write bug `resolve_day_status` fixes: a status in any other spelling
/// counted as done, so a day that never succeeded was never offered again.
#[test]
fn an_unrecognised_status_is_offered_again() {
    let now = now_of("2024-06-15T12:00:00+00:00");
    let day = "2024-06-10";
    for status in ["failed", "done", "", "Completed", "COMPLETED", "partial"] {
        let rows = vec![row(day, status, Some("2024-06-11T12:00:00+00:00"), None)];
        let needed = days_needing_fetch(&rows, day_of(day), day_of(day), 0, now);
        assert_eq!(needed.len(), 1, "{status:?} must not count as completed");
        assert_eq!(needed[0].reason, FetchReason::NotCompleted);
    }
    // And the one accepted spelling really is accepted.
    let rows = vec![row(
        day,
        "completed",
        Some("2024-06-11T12:00:00+00:00"),
        None,
    )];
    assert!(days_needing_fetch(&rows, day_of(day), day_of(day), 0, now).is_empty());
}

/// Rule 3's cost, stated as the source states it: under the default window
/// `[yesterday, today]` a run before noon UTC offers **two** days rather than
/// one, because day *D* is offered once more on *D+1* — which is the point.
/// After noon UTC it offers **none**.
#[test]
fn rule_three_costs_exactly_one_extra_day_before_noon() {
    let yesterday = "2024-06-14";
    let today = "2024-06-15";
    let rows = vec![
        row(
            yesterday,
            "completed",
            Some("2024-06-15T12:00:00+00:00"),
            None,
        ),
        row(today, "completed", Some("2024-06-15T11:00:00+00:00"), None),
    ];

    let before_noon = days_needing_fetch(
        &rows,
        day_of(yesterday),
        day_of(today),
        0,
        now_of("2024-06-15T09:00:00+00:00"),
    );
    assert_eq!(
        before_noon
            .iter()
            .map(|d| d.day.format("%Y-%m-%d").to_string())
            .collect::<Vec<_>>(),
        vec![yesterday.to_string(), today.to_string()],
        "yesterday is not yet durable and today never is"
    );

    let after_noon = days_needing_fetch(
        &rows,
        day_of(yesterday),
        day_of(today),
        0,
        now_of("2024-06-15T13:00:00+00:00"),
    );
    assert_eq!(
        after_noon
            .iter()
            .map(|d| d.day.format("%Y-%m-%d").to_string())
            .collect::<Vec<_>>(),
        vec![today.to_string()],
        "yesterday is now durable; today still is not"
    );
}

/// An **empty** window is not an error: it is what the ordinary
/// incremental-sync idiom produces once it has caught up.
#[test]
fn an_empty_window_selects_nothing_and_is_not_an_error() {
    let now = now_of("2024-06-15T12:00:00+00:00");
    let needed = days_needing_fetch(&[], day_of("2024-06-15"), day_of("2024-06-14"), 0, now);
    assert!(needed.is_empty());
}

/// Rule 4: rechecking is only consulted for a day that is already durable, and
/// absent, unreadable and stale are **one answer** — none of them shows the day
/// was verified inside the window.
#[test]
fn rechecking_treats_absent_unreadable_and_stale_alike() {
    let now = now_of("2024-06-15T12:00:00+00:00");
    let day = "2024-06-10";
    let at = Some("2024-06-11T12:00:00+00:00");
    for (label, verified) in [
        ("absent", None),
        ("unreadable", Some("nonsense")),
        ("stale", Some("2024-01-01T00:00:00+00:00")),
    ] {
        let rows = vec![row(day, "completed", at, verified)];
        let needed = days_needing_fetch(&rows, day_of(day), day_of(day), 7, now);
        assert_eq!(needed.len(), 1, "{label} must be rechecked");
        assert_eq!(needed[0].reason, FetchReason::RecheckDue);
    }
    // Fresh enough is left alone.
    let rows = vec![row(day, "completed", at, Some("2024-06-14T00:00:00+00:00"))];
    assert!(days_needing_fetch(&rows, day_of(day), day_of(day), 7, now).is_empty());
}

/// A day that is **not durable** is offered for that reason and not for the
/// recheck — the categories are recorded, because a caller diagnosing a slow
/// sync needs to know which.
#[test]
fn a_non_durable_day_is_offered_before_rechecking_is_considered() {
    let now = now_of("2024-06-15T12:00:00+00:00");
    let rows = vec![row(
        "2024-06-10",
        "completed",
        Some("2024-06-10T09:00:00+00:00"),
        Some("2024-06-14T00:00:00+00:00"),
    )];
    let needed = days_needing_fetch(&rows, day_of("2024-06-10"), day_of("2024-06-10"), 7, now);
    assert_eq!(needed.len(), 1);
    assert_eq!(needed[0].reason, FetchReason::NotDurable);
}

/// With `recheck_days` unset, a durable day is left alone even with no
/// verification recorded — the documented default, and the reason a missing
/// `last_verified_at` is not by itself a re-fetch.
#[test]
fn rechecking_is_off_by_default() {
    let now = now_of("2024-06-15T12:00:00+00:00");
    let rows = vec![row(
        "2024-06-10",
        "completed",
        Some("2024-06-11T12:00:00+00:00"),
        None,
    )];
    assert!(
        days_needing_fetch(&rows, day_of("2024-06-10"), day_of("2024-06-10"), 0, now).is_empty()
    );
}

// ---------------------------------------------------------------------------
// Window validation
// ---------------------------------------------------------------------------

/// `date_to == date.max` is refused: day selection asks which day follows the
/// last day of the window, and there is none.
#[test]
fn the_last_representable_day_cannot_be_a_window_end() {
    let err = validate_window(NaiveDate::MAX, 0, day_of("2024-06-15")).expect_err("refused");
    assert!(matches!(err, WindowError::NoDayAfter { .. }));
    assert!(err.to_string().contains("9999-12-31"), "{err}");
}

/// A **negative** `recheck_days` walked fine in Python and was swallowed by
/// `recheck_days > 0`, delivering "recheck nothing" to a caller who asked for
/// the opposite, without a word.
#[test]
fn a_negative_recheck_depth_is_refused_rather_than_ignored() {
    let err = validate_window(day_of("2024-06-15"), -1, day_of("2024-06-15")).expect_err("refused");
    assert_eq!(err, WindowError::NegativeRecheck { got: -1 });
    assert_eq!(err.to_string(), "recheck_days must not be negative, got -1");
}

/// A `recheck_days` reaching back before the start of the calendar would take
/// the date arithmetic out of range.
#[test]
fn a_recheck_depth_reaching_before_the_calendar_is_refused() {
    let err = validate_window(day_of("2024-06-15"), 10_i64.pow(9), day_of("2024-06-15"))
        .expect_err("refused");
    assert!(matches!(err, WindowError::RecheckBeforeCalendar { .. }));
    assert!(err.to_string().contains("0001-01-01"));
}

/// Every rejection names the parameter, because `sync`'s caller has no other
/// way to tell which one was wrong.
#[test]
fn every_window_rejection_names_its_parameter() {
    for (err, field) in [
        (
            validate_window(NaiveDate::MAX, 0, day_of("2024-06-15")).unwrap_err(),
            "date_to",
        ),
        (
            validate_window(day_of("2024-06-15"), -5, day_of("2024-06-15")).unwrap_err(),
            "recheck_days",
        ),
    ] {
        assert!(
            err.to_string().starts_with(field),
            "{err} must name {field}"
        );
    }
}

// ---------------------------------------------------------------------------
// The future window
// ---------------------------------------------------------------------------

/// A window ending in the future can never complete — the durability boundary
/// is unsatisfiable for a day that has not happened — so each future day is
/// stored `completed` and re-offered on every run for the life of the
/// installation. Reported rather than rejected: the past half of the window is
/// perfectly fetchable.
#[test]
fn a_window_ending_in_the_future_is_reported_not_rejected() {
    let today = day_of("2024-06-15");
    assert_eq!(note_unreachable_days(today, today), None);
    assert_eq!(note_unreachable_days(day_of("2024-06-14"), today), None);

    let note = note_unreachable_days(day_of("2024-06-18"), today).expect("a note");
    assert!(note.contains("3 day(s) in the future"), "{note}");
    assert!(note.contains("re-fetched on every run"), "{note}");
}

// ---------------------------------------------------------------------------
// Day status
// ---------------------------------------------------------------------------

fn fetch(status: &str, note: Option<&str>) -> FetchResult {
    FetchResult {
        source: "s".to_string(),
        date: "2024-06-10".to_string(),
        record_count: 0,
        status: status.to_string(),
        error: None,
        note: note.map(str::to_string),
    }
}

/// A day whose records failed to store is recorded **failed**, not completed:
/// `completed` is durable, so the records would be permanently absent.
#[test]
fn store_failures_make_the_day_fail() {
    let outcome = resolve_day_status("s", day_of("2024-06-10"), &fetch("completed", None), 3);
    assert_eq!(outcome.status, "failed");
    assert_eq!(outcome.errors.len(), 1);
    assert!(outcome.errors[0].contains("3 record(s) failed to store"));
    assert!(outcome.notes.is_empty(), "a failed day gets no note");
}

/// The convention is an **allowlist**: an unrecognised status is recorded as
/// failed and reported, because a third-party fetcher is exactly the caller who
/// will not know the convention, and the old denylist converted their failure
/// into success.
#[test]
fn an_unknown_status_is_recorded_failed_and_reported() {
    let outcome = resolve_day_status("s", day_of("2024-06-10"), &fetch("done", None), 0);
    assert_eq!(outcome.status, "failed");
    assert_eq!(outcome.errors.len(), 1);
    assert!(
        outcome.errors[0].contains("unknown status 'done'"),
        "{:?}",
        outcome.errors
    );
}

/// A deleted note ("the day came up short but completed") is reported on the
/// **notes** list and not the errors list, because the two call for different
/// responses: an error names a day that will be retried, a note names a day
/// that will not be.
#[test]
fn a_note_lands_on_notes_and_not_errors() {
    let outcome = resolve_day_status(
        "s",
        day_of("2024-06-10"),
        &fetch("completed", Some("came up short")),
        0,
    );
    assert_eq!(outcome.status, "completed");
    assert!(outcome.errors.is_empty());
    assert_eq!(outcome.notes.len(), 1);
    assert!(outcome.notes[0].contains("came up short"));
}

/// A note on a **failed** day is dropped: it describes a walk that is about to
/// be retried anyway.
#[test]
fn a_note_on_a_failed_day_is_dropped() {
    let outcome = resolve_day_status(
        "s",
        day_of("2024-06-10"),
        &fetch("failed", Some("short")),
        0,
    );
    assert_eq!(outcome.status, "failed");
    assert!(outcome.notes.is_empty());
}
