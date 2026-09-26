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

//! One part's itinerary — the oracle and the named tests.
//!
//! The corpus (21 cases) diffs the three pure decisions `_fetch_partitioned`'s
//! loop makes. The named tests state why each exists, which is the part the loop
//! itself cannot say.

use bmlib::publications::fetchers::pubmed::{may_checkpoint, part_step, PartCredit, PartStep};
use bmlib::publications::fetchers::reconcile_delivery;
use serde_json::{json, Value};

const CASES: &str = include_str!("data/pubmed_part_cases.json");
const EXPECTED: &str = include_str!("data/pubmed_part_expected.json");

/// The label `_fetch_partitioned` builds for a part's own reconcile.
const PART_LABEL: &str = "2024-06-10 part edat:x:y";

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "part_step" => {
            let planned = args["planned"].as_i64().unwrap_or(0);
            let checkpoint = args.get("checkpoint").and_then(Value::as_i64);
            match part_step(planned, checkpoint) {
                PartStep::Skip { credited } => json!({"step": "skip", "credited": credited}),
                PartStep::RefetchBecauseCountMoved { was, now } => {
                    json!({"step": "refetch", "was": was, "now": now})
                }
                PartStep::Walk => json!({"step": "walk"}),
            }
        }
        "plan_verdict" => {
            let verdict = reconcile_delivery(
                "pubmed",
                PART_LABEL,
                args["part_count"].as_i64().unwrap_or(0),
                Some(args["planned"].as_i64().unwrap_or(0)),
                false,
            );
            json!({"failure": verdict.failure, "note": verdict.note})
        }
        "may_checkpoint" => {
            let plan_noted = args["plan_noted"].as_bool().unwrap_or(false);
            let walk_noted = args["walk_noted"].as_bool().unwrap_or(false);
            json!(may_checkpoint(plan_noted, walk_noted))
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
// Skip versus refetch
// ---------------------------------------------------------------------------

/// **The promise is compared, not only the key.** Skipping on the key alone
/// would permanently lose every record a part gained since it was checkpointed,
/// so a count that has moved forces a re-fetch.
#[test]
fn a_moved_promise_forces_a_refetch() {
    assert_eq!(
        part_step(120, Some(100)),
        PartStep::RefetchBecauseCountMoved { was: 100, now: 120 }
    );
    // A promise that *shrank* also forces one: the stored count no longer
    // describes this range either.
    assert_eq!(
        part_step(100, Some(120)),
        PartStep::RefetchBecauseCountMoved { was: 120, now: 100 }
    );
    // A single record's drift is enough.
    assert_eq!(
        part_step(5001, Some(5000)),
        PartStep::RefetchBecauseCountMoved {
            was: 5000,
            now: 5001
        }
    );
}

/// A matching promise **skips**, and the credit is the stored promise — not the
/// plan's, and not zero.
#[test]
fn a_matching_promise_skips_and_is_credited() {
    match part_step(100, Some(100)) {
        PartStep::Skip { credited } => assert_eq!(credited, 100),
        other => panic!("expected Skip, got {other:?}"),
    }
    assert_eq!(part_step(100, None), PartStep::Walk);
}

/// A skipped part is credited to **delivered** and **never** to processed.
///
/// The day-total reconciliation judges every part's delivery against the day's
/// own count, and a resumed run never issues the skipped part's own EFetch — so
/// without the credit every resumed day would fail. But those records were not
/// walked by this run, so the returned record count and the progress total stay
/// honest about what this run did.
#[test]
fn a_skipped_part_is_credited_to_delivery_and_not_to_progress() {
    let credit = PartCredit::skipped(4321);
    assert_eq!(credit.delivered, 4321);
    assert_eq!(
        credit.processed, 0,
        "a skipped part contributes no records this run walked"
    );
}

// ---------------------------------------------------------------------------
// The part's own reconcile
// ---------------------------------------------------------------------------

/// **The asymmetry is the tell.** A part that *delivers* 1 of 5,000 fails the
/// day, so a part that *claims* 1 having been measured at 5,000 thirty seconds
/// ago cannot pass either.
///
/// Left unchecked this is silent: the part then walks its own count, reconciles
/// that count against itself — which always passes — and is checkpointed as
/// clean, so the loss reaches only the day total, where enough collapsed parts
/// still clear the day-level floor. The day would be `completed`, never
/// re-offered, and most of a structural day permanently absent behind a single
/// shortfall note.
#[test]
fn a_part_claiming_far_less_than_it_was_measured_at_fails_the_day() {
    let verdict = reconcile_delivery("pubmed", PART_LABEL, 1, Some(5000), false);
    assert!(verdict.is_failure());
    let message = verdict.failure.expect("a failure");
    assert!(message.contains("1 of 5000"), "{message}");

    // A part that collapsed to zero always fails, since no planned part promises
    // fewer than one record.
    let verdict = reconcile_delivery("pubmed", PART_LABEL, 0, Some(5000), false);
    assert!(verdict.is_failure());
}

/// The floor is the **same** one every other comparison here uses rather than a
/// new constant: equality would fail a day for the one-record drift two requests
/// at two instants routinely show, and a day recorded `failed` is re-fetched on
/// every later run for the life of the installation.
#[test]
fn a_one_record_drift_is_a_note_not_a_failure() {
    for claimed in [5000, 4999] {
        let verdict = reconcile_delivery("pubmed", PART_LABEL, claimed, Some(5000), false);
        assert!(!verdict.is_failure(), "{claimed} must not fail the day");
    }
    // Exactly at the floor passes; below it fails.
    assert!(!reconcile_delivery("pubmed", PART_LABEL, 2500, Some(5000), false).is_failure());
    assert!(reconcile_delivery("pubmed", PART_LABEL, 2499, Some(5000), false).is_failure());
}

// ---------------------------------------------------------------------------
// Checkpointing
// ---------------------------------------------------------------------------

/// Flushing and checkpointing are **deliberately different questions**, and
/// collapsing them was a real defect (#105 review, F1).
///
/// A noted part must **not** be checkpointed: skipping it on a later resumed run
/// would credit it at its full `promised` and manufacture the very records the
/// note is reporting missing — and that run's result would carry no note at all,
/// since a note dies with the run that produced it.
#[test]
fn a_noted_part_is_never_checkpointed() {
    assert!(
        !may_checkpoint(true, false),
        "a plan note blocks the checkpoint"
    );
    assert!(
        !may_checkpoint(false, true),
        "a walk note blocks the checkpoint"
    );
    assert!(!may_checkpoint(true, true));
    assert!(
        may_checkpoint(false, false),
        "only a clean part is checkpointed"
    );
}

/// Both reconciles have to be clean, for the one reason: **a note dies with the
/// run that produced it.** A checkpoint written over a note lets a later run skip
/// the part and report no shortfall at all, so the deficiency stops being
/// answerable from a return value — which is the whole reason `FetchResult.note`
/// exists.
#[test]
fn a_note_that_is_not_checkpointed_survives_into_the_next_run() {
    // The condition is not "did the walk note it" but "did *either* reconcile".
    // A part clean on delivery and noted on its claim is still uncheckpointed,
    // so the next run re-walks it and carries the note.
    assert!(!may_checkpoint(true, false));
}
