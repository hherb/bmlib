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

//! The transparency model rules — the oracle and the named tests.
//!
//! The corpus carries Python's **tables as data** and 43 rule cases. The named
//! tests state what the oracle cannot: why the tri-state COI matters, and why both
//! sides of each partition are named.

use bmlib::transparency::{
    calculate_risk_level, FullTextStatus, TransparencyRisk, TransparencySettings,
    TransparencyUnknownReason, TrialResultsStatus, ANSWERED_TRIAL_RESULTS_STATUSES,
    MEDIUM_RISK_SCORE_THRESHOLD, NOT_ANSWERED_TRIAL_RESULTS_STATUSES,
    NOT_REFUSED_FULL_TEXT_STATUSES, REFUSED_FULL_TEXT_STATUSES,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/transparency_cases.json");
const EXPECTED: &str = include_str!("data/transparency_expected.json");

fn run(case: &Value) -> Value {
    let a = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "risk_level" => {
            let mut settings = TransparencySettings::default();
            if let Some(over) = a.get("settings").and_then(Value::as_object) {
                for (key, value) in over {
                    match key.as_str() {
                        "enabled" => settings.enabled = value.as_bool().unwrap_or(true),
                        "score_threshold" => {
                            settings.score_threshold = value.as_i64().unwrap_or(40)
                        }
                        "industry_funding_triggers_downgrade" => {
                            settings.industry_funding_triggers_downgrade =
                                value.as_bool().unwrap_or(true)
                        }
                        "missing_coi_triggers_downgrade" => {
                            settings.missing_coi_triggers_downgrade =
                                value.as_bool().unwrap_or(true)
                        }
                        "tier_downgrade_amount" => {
                            settings.tier_downgrade_amount = value.as_i64().unwrap_or(1)
                        }
                        "filtering_enabled" => {
                            settings.filtering_enabled = value.as_bool().unwrap_or(false)
                        }
                        "max_concurrent_analyses" => {
                            settings.max_concurrent_analyses = value.as_i64().unwrap_or(3)
                        }
                        "cache_results" => settings.cache_results = value.as_bool().unwrap_or(true),
                        other => panic!("unknown setting {other:?}"),
                    }
                }
            }
            json!(calculate_risk_level(
                a["score"].as_i64().unwrap_or(0),
                a["industry_funding"].as_bool().unwrap_or(false),
                a["data_availability"].as_str().unwrap_or_default(),
                a.get("coi_disclosed").and_then(Value::as_bool),
                &settings,
            )
            .as_str())
        }
        "full_text_is_refusal" => json!(FullTextStatus::parse(
            a["status"].as_str().unwrap_or_default()
        )
        .expect("corpus status")
        .is_refusal()),
        "trial_is_answered" => json!(TrialResultsStatus::parse(
            a["status"].as_str().unwrap_or_default()
        )
        .expect("corpus status")
        .is_answered()),
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected["cases"].as_array().expect("cases");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
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

/// **The port's enum members are Python's members**, compared as data in both
/// directions — an extra member is invisible to a one-way check, and a member
/// Python does not have is a value the analyzer could store and nothing
/// downstream expects.
#[test]
fn the_enum_members_are_pythons() {
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected");
    let tables = &expected["tables"];

    let check = |key: &str, ours: &[&str]| {
        let want: Vec<String> = tables[key]
            .as_array()
            .expect(key)
            .iter()
            .map(|v| v.as_str().expect("string").to_string())
            .collect();
        assert_eq!(ours, want.as_slice(), "{key} must match Python's members");
    };

    let risk: Vec<&str> = TransparencyRisk::ALL.iter().map(|v| v.as_str()).collect();
    check("risk", &risk);
    let reason: Vec<&str> = TransparencyUnknownReason::ALL
        .iter()
        .map(|v| v.as_str())
        .collect();
    check("unknown_reason", &reason);
    let full: Vec<&str> = FullTextStatus::ALL.iter().map(|v| v.as_str()).collect();
    check("full_text_status", &full);
    let trial: Vec<&str> = TrialResultsStatus::ALL.iter().map(|v| v.as_str()).collect();
    check("trial_results_status", &trial);

    assert_eq!(
        MEDIUM_RISK_SCORE_THRESHOLD,
        tables["medium_risk_score_threshold"]
            .as_i64()
            .expect("threshold")
    );
    // Every member round-trips through its wire name.
    for status in FullTextStatus::ALL {
        assert_eq!(
            FullTextStatus::parse(status.as_str()).expect("round-trips"),
            *status
        );
    }
    for status in TrialResultsStatus::ALL {
        assert_eq!(
            TrialResultsStatus::parse(status.as_str()).expect("round-trips"),
            *status
        );
    }
}

/// **Both sides of each partition are named, and a test asserts the partition.**
///
/// Membership of the refused set alone would leave the rule enforced by prose: a
/// member added later and omitted from it simply reads as `is_refusal` false —
/// so the **silent default runs the wrong way**, reporting a served-and-refused
/// document as one that never arrived.
#[test]
fn the_partitions_are_complete_and_disjoint() {
    // Every member is on exactly one side.
    for status in FullTextStatus::ALL {
        let refused = REFUSED_FULL_TEXT_STATUSES.contains(status);
        let not_refused = NOT_REFUSED_FULL_TEXT_STATUSES.contains(status);
        assert!(
            refused ^ not_refused,
            "{} must be on exactly one side (refused={refused}, not={not_refused})",
            status.as_str()
        );
        assert_eq!(
            status.is_refusal(),
            refused,
            "{} disagrees with the named set",
            status.as_str()
        );
    }
    assert_eq!(
        REFUSED_FULL_TEXT_STATUSES.len() + NOT_REFUSED_FULL_TEXT_STATUSES.len(),
        FullTextStatus::ALL.len(),
        "the two named sets must cover every member"
    );

    for status in TrialResultsStatus::ALL {
        let answered = ANSWERED_TRIAL_RESULTS_STATUSES.contains(status);
        let not_answered = NOT_ANSWERED_TRIAL_RESULTS_STATUSES.contains(status);
        assert!(
            answered ^ not_answered,
            "{} must be on exactly one side",
            status.as_str()
        );
        assert_eq!(status.is_answered(), answered, "{}", status.as_str());
    }
    assert_eq!(
        ANSWERED_TRIAL_RESULTS_STATUSES.len() + NOT_ANSWERED_TRIAL_RESULTS_STATUSES.len(),
        TrialResultsStatus::ALL.len()
    );

    // And the sets agree with Python's, as data.
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected");
    let refused: Vec<&str> = {
        let mut v: Vec<&str> = REFUSED_FULL_TEXT_STATUSES
            .iter()
            .map(|s| s.as_str())
            .collect();
        v.sort_unstable();
        v
    };
    let want: Vec<&str> = expected["tables"]["refused_full_text"]
        .as_array()
        .expect("refused")
        .iter()
        .map(|v| v.as_str().expect("string"))
        .collect();
    assert_eq!(refused, want);
}

/// **`NOT_SERVED` is the 404 and nothing else.** Three other outcomes used to
/// reach it, putting a claim in the remote's mouth that only the 404 makes: a
/// bmlib defect on the request line, a 429/503/403, and an empty HTTP 200 body.
/// They are `REQUEST_FAILED` now, and **`is_refusal` is false for it** — nothing
/// was served, so there is nothing to have refused.
#[test]
fn nothing_served_is_not_a_refusal() {
    for status in [
        FullTextStatus::NotAttempted,
        FullTextStatus::SearchFailed,
        FullTextStatus::NotServed,
        FullTextStatus::RequestFailed,
    ] {
        assert!(
            !status.is_refusal(),
            "{}: nothing was served",
            status.as_str()
        );
    }
    // A served-but-unscannable document is, whichever way it was unusable.
    for status in [
        FullTextStatus::Truncated,
        FullTextStatus::UnterminatedMarkup,
        FullTextStatus::UnclosedRegion,
        FullTextStatus::EntirelyNested,
    ] {
        assert!(
            status.is_refusal(),
            "{}: served and refused",
            status.as_str()
        );
    }
    assert!(!FullTextStatus::Analyzed.is_refusal());
}

/// **A partly-answered trial check is not a finding**, and it sits on the
/// unanswered side because the compatibility flag is what both downstreams
/// render — `false` under `is_answered()` true reads as *"the trial fell short"*.
#[test]
fn a_partly_answered_check_is_not_an_answer() {
    assert!(TrialResultsStatus::Posted.is_answered());
    assert!(TrialResultsStatus::NotPosted.is_answered());
    for status in [
        TrialResultsStatus::NotRegistered,
        TrialResultsStatus::PartlyAnswered,
        TrialResultsStatus::RequestFailed,
        TrialResultsStatus::NotCheckable,
    ] {
        assert!(!status.is_answered(), "{}", status.as_str());
    }
}

// ---------------------------------------------------------------------------
// The tri-state COI
// ---------------------------------------------------------------------------

/// **Only an explicit `false` triggers the missing-COI downgrade.** `None` means
/// the status could not be determined — full text unavailable, say — and
/// downgrading it would penalise a paper for bmlib's own inability to look.
///
/// Collapsing the two would downgrade every closed-access paper, which is exactly
/// the class the full-text statuses exist to report honestly.
#[test]
fn an_unknown_coi_does_not_downgrade_but_a_missing_one_does() {
    let settings = TransparencySettings::default();
    // A paper that is otherwise transparent.
    assert_eq!(
        calculate_risk_level(80, false, "open", Some(true), &settings),
        TransparencyRisk::Low
    );
    assert_eq!(
        calculate_risk_level(80, false, "open", None, &settings),
        TransparencyRisk::Low,
        "an unknown COI is not a missing one"
    );
    assert_eq!(
        calculate_risk_level(80, false, "open", Some(false), &settings),
        TransparencyRisk::High,
        "an inspected-and-absent statement does downgrade"
    );
    // And the rule can be switched off.
    let off = TransparencySettings {
        missing_coi_triggers_downgrade: false,
        ..TransparencySettings::default()
    };
    assert_eq!(
        calculate_risk_level(80, false, "open", Some(false), &off),
        TransparencyRisk::Low
    );
}

/// **Industry funding alone is not a downgrade to HIGH** — it needs restricted
/// data beside it. On its own it reaches `MEDIUM`.
#[test]
fn industry_funding_needs_restricted_data_for_high() {
    let settings = TransparencySettings::default();
    for restricted in ["restricted", "not_available", "not_stated"] {
        assert_eq!(
            calculate_risk_level(80, true, restricted, Some(true), &settings),
            TransparencyRisk::High,
            "{restricted} plus industry funding"
        );
    }
    // An availability the rule does not name is not restricted.
    for open in ["open", "unknown", ""] {
        assert_eq!(
            calculate_risk_level(80, true, open, Some(true), &settings),
            TransparencyRisk::Medium,
            "{open:?} plus industry funding is only medium"
        );
    }
    // And without industry funding, restricted data alone is not HIGH.
    assert_eq!(
        calculate_risk_level(80, false, "restricted", Some(true), &settings),
        TransparencyRisk::Low
    );
    // Both rules can be switched off together.
    let off = TransparencySettings {
        industry_funding_triggers_downgrade: false,
        ..TransparencySettings::default()
    };
    assert_eq!(
        calculate_risk_level(80, true, "restricted", Some(true), &off),
        TransparencyRisk::Medium
    );
}

/// The **score** decides `HIGH` below the caller's threshold and `MEDIUM` at or
/// below the fixed medium threshold; the threshold is the caller's, the medium
/// bound is not.
#[test]
fn the_thresholds_are_the_callers_and_the_constants() {
    let settings = TransparencySettings::default();
    assert_eq!(
        calculate_risk_level(39, false, "open", Some(true), &settings),
        TransparencyRisk::High,
        "below the caller's threshold"
    );
    assert_eq!(
        calculate_risk_level(40, false, "open", Some(true), &settings),
        TransparencyRisk::Medium,
        "at it, and at or below 70"
    );
    assert_eq!(
        calculate_risk_level(
            MEDIUM_RISK_SCORE_THRESHOLD,
            false,
            "open",
            Some(true),
            &settings
        ),
        TransparencyRisk::Medium
    );
    assert_eq!(
        calculate_risk_level(
            MEDIUM_RISK_SCORE_THRESHOLD + 1,
            false,
            "open",
            Some(true),
            &settings
        ),
        TransparencyRisk::Low
    );

    // The caller's threshold moves the HIGH boundary.
    let strict = TransparencySettings {
        score_threshold: 60,
        ..TransparencySettings::default()
    };
    assert_eq!(
        calculate_risk_level(50, false, "open", Some(true), &strict),
        TransparencyRisk::High
    );
    let lenient = TransparencySettings {
        score_threshold: 10,
        ..TransparencySettings::default()
    };
    assert_eq!(
        calculate_risk_level(50, false, "open", Some(true), &lenient),
        TransparencyRisk::Medium
    );
}

/// The **defaults** are the Python's, and two of them are not the zero value.
#[test]
fn the_settings_defaults_are_pythons() {
    let settings = TransparencySettings::default();
    assert!(settings.enabled);
    assert_eq!(settings.score_threshold, 40);
    assert!(settings.industry_funding_triggers_downgrade);
    assert!(settings.missing_coi_triggers_downgrade);
    assert_eq!(settings.tier_downgrade_amount, 1);
    assert!(!settings.filtering_enabled);
    assert_eq!(settings.max_concurrent_analyses, 3);
    assert!(settings.cache_results);
}
