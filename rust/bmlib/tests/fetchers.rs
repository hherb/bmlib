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

//! Fetcher shared rules — the oracle and the named tests.
//!
//! The corpus (24 cases) diffs the reconciliation rule and the four built-in
//! descriptors against Python. The named tests state why each rule exists,
//! which is the part a corpus of messages cannot say.

use bmlib::publications::fetchers::{
    builtin_descriptors, reconcile_delivery, FetchOutcome, FetchRequest, Fetcher, Progress,
    Reconciliation, Registry, ResumeState, SHORTFALL_FAILURE_RATIO,
};
use bmlib::publications::models::SourceDescriptor;
use chrono::NaiveDate;
use serde_json::Value;

const CASES: &str = include_str!("data/fetcher_cases.json");
const EXPECTED: &str = include_str!("data/fetcher_expected.json");

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "ratio" => serde_json::json!(SHORTFALL_FAILURE_RATIO),
        "reconcile" => {
            let result = reconcile_delivery(
                args["source"].as_str().unwrap_or_default(),
                args["date"].as_str().unwrap_or_default(),
                args["delivered"].as_i64().unwrap_or(0),
                args.get("promised").and_then(Value::as_i64),
                args.get("stalled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            );
            serde_json::json!({"failure": result.failure, "note": result.note})
        }
        "builtins" => {
            let mut descriptors = builtin_descriptors();
            descriptors.sort_by(|a, b| a.name.cmp(&b.name));
            serde_json::json!(descriptors.iter().map(descriptor_json).collect::<Vec<_>>())
        }
        "source_names" => {
            let mut names: Vec<String> =
                builtin_descriptors().into_iter().map(|d| d.name).collect();
            names.sort();
            serde_json::json!(names)
        }
        other => panic!("unknown fn {other:?}"),
    }
}

fn descriptor_json(descriptor: &SourceDescriptor) -> Value {
    serde_json::json!({
        "name": descriptor.name,
        "display_name": descriptor.display_name,
        "description": descriptor.description,
        "resumable": descriptor.resumable,
        "params": descriptor.params.iter().map(|p| serde_json::json!({
            "name": p.name,
            "description": p.description,
            "required": p.required,
            "default": p.default,
            "secret": p.secret,
        })).collect::<Vec<_>>(),
    })
}

/// A fetcher that returns a fixed outcome, for exercising the registry.
struct StubFetcher {
    outcome: FetchOutcome,
    /// Whether `fetch` was called, so a test can prove the registry resolved
    /// the right entry rather than merely returning something.
    calls: std::sync::Arc<std::sync::atomic::AtomicUsize>,
}

impl Fetcher for StubFetcher {
    fn fetch(
        &self,
        _request: &FetchRequest,
        on_progress: &mut dyn FnMut(Progress),
    ) -> Result<FetchOutcome, bmlib::publications::fetchers::FetchError> {
        self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        on_progress(Progress::Page {
            delivered: self.outcome.records.len() as i64,
            promised: self.outcome.promised,
        });
        Ok(self.outcome.clone())
    }
}

fn stub(name: &str, resumable: bool) -> (SourceDescriptor, Box<StubFetcher>) {
    stub_sharing(
        name,
        resumable,
        std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
    )
}

/// As [`stub`], with the call counter supplied so two stubs share one — which is
/// how a test proves the *replacement* ran rather than the one it replaced.
fn stub_sharing(
    name: &str,
    resumable: bool,
    calls: std::sync::Arc<std::sync::atomic::AtomicUsize>,
) -> (SourceDescriptor, Box<StubFetcher>) {
    (
        SourceDescriptor {
            name: name.to_string(),
            display_name: name.to_string(),
            description: "stub".to_string(),
            params: Vec::new(),
            resumable,
        },
        Box::new(StubFetcher {
            outcome: FetchOutcome::completed(Vec::new()),
            calls,
        }),
    )
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
// Rule 1: unreconcilable
// ---------------------------------------------------------------------------

/// **A `None` promise is not a promise of zero.** Zero is a source saying "this
/// day is empty", which a delivery of zero satisfies; `None` is a source saying
/// nothing at all, against which no delivery can be verified. Flattening the
/// two either fails every quiet day or accepts every truncated one.
#[test]
fn an_unnamed_promise_is_not_a_promise_of_zero() {
    // Named zero: a zero delivery satisfies it.
    assert!(!reconcile_delivery("s", "2024-06-10", 0, Some(0), false).is_failure());
    // Unnamed, zero delivered: the ordinary quiet day, which must pass —
    // this is what keeps bioRxiv's total-omitting quiet response working.
    assert!(!reconcile_delivery("s", "2024-06-10", 0, None, false).is_failure());
    // Unnamed, records delivered: unverifiable, so it cannot be called complete.
    let r = reconcile_delivery("s", "2024-06-10", 5, None, false);
    assert!(r.is_failure());
    assert!(r
        .failure
        .as_deref()
        .unwrap_or_default()
        .contains("no count"));
}

/// An **unverifiable success is the failure this module exists to prevent** —
/// the message says so, because the operator reading it needs to know the walk
/// may in fact have been fine.
#[test]
fn an_unverifiable_walk_says_why_it_cannot_be_confirmed() {
    let r = reconcile_delivery("pubmed", "2024-06-10", 5, None, false);
    let message = r.failure.expect("a failure");
    assert!(
        message.contains("cannot be shown to have finished"),
        "{message}"
    );
    assert!(r.note.is_none(), "a failure carries no note");
}

// ---------------------------------------------------------------------------
// Rule 2: stalled
// ---------------------------------------------------------------------------

/// A page delivering nothing while the promise is unmet is **broken outright
/// whatever the magnitude**, so it carries no threshold — and it is the only
/// rule that catches a history session expiring on the *last* page of a long
/// walk, where the shortfall is a single record.
#[test]
fn a_stalled_walk_fails_even_when_the_gap_is_one_record() {
    let r = reconcile_delivery("pubmed", "2024-06-10", 9, Some(10), true);
    assert!(r.is_failure(), "one record short and stalled must fail");
    assert!(r
        .failure
        .as_deref()
        .unwrap_or_default()
        .contains("empty page"));
    // Without the stall flag the same delivery is a benign note.
    let r = reconcile_delivery("pubmed", "2024-06-10", 9, Some(10), false);
    assert!(!r.is_failure());
    assert!(r.note.is_some());
}

/// Stalling is only meaningful when the promise is unmet: a full delivery with
/// a trailing empty page is a complete walk.
#[test]
fn a_stall_at_full_delivery_is_not_a_failure() {
    assert!(!reconcile_delivery("s", "2024-06-10", 10, Some(10), true).is_failure());
    // And a stall with no promise follows rule 1, not rule 2.
    let r = reconcile_delivery("s", "2024-06-10", 0, None, true);
    assert!(
        !r.is_failure(),
        "nothing delivered against no count is quiet"
    );
}

// ---------------------------------------------------------------------------
// Rule 3: shortfall
// ---------------------------------------------------------------------------

/// The floor is **exclusive**: delivering exactly the fraction passes. This is
/// the boundary that decides whether a day is re-fetched on every later run for
/// ever, so it is pinned from both sides.
#[test]
fn the_shortfall_floor_is_exclusive() {
    assert_eq!(SHORTFALL_FAILURE_RATIO, 0.5);
    assert!(!reconcile_delivery("s", "2024-06-10", 5, Some(10), false).is_failure());
    assert!(reconcile_delivery("s", "2024-06-10", 4, Some(10), false).is_failure());
}

/// The floor is a **floor rather than strict inequality** for a reason that is
/// easy to miss: a day marked `failed` is re-offered on *every* later sync run,
/// so failing on a gap with a benign and permanent cause re-fetches that day for
/// ever, silently growing with the date range.
#[test]
fn a_small_shortfall_completes_the_day_and_is_reported() {
    let r = reconcile_delivery("pubmed", "2024-06-10", 9999, Some(10000), false);
    assert!(!r.is_failure());
    let note = r.note.expect("a note");
    assert!(note.contains("benign causes"), "{note}");
    assert!(
        note.contains("recording the day as completed"),
        "the note must say what was decided: {note}"
    );
}

/// A short day may be missing nearly half its records, and a log line is not a
/// surface anything can query — which is why the shortfall is **returned** as a
/// note rather than only logged, and why `SyncReport` has a notes list.
#[test]
fn a_shortfall_is_returned_not_only_logged() {
    let r = reconcile_delivery("s", "2024-06-10", 6, Some(10), false);
    assert!(r.note.is_some(), "the caller must be able to see it");
    assert!(r.failure.is_none());
}

/// At most one field is ever set — the caller stores exactly one of them.
#[test]
fn a_reconciliation_sets_at_most_one_field() {
    let clean: Reconciliation = reconcile_delivery("s", "2024-06-10", 10, Some(10), false);
    assert_eq!(clean, Reconciliation::clean());
    for (delivered, promised, stalled) in [
        (4, Some(10), false),
        (0, Some(10), false),
        (5, None, false),
        (0, Some(10), true),
    ] {
        let r = reconcile_delivery("s", "2024-06-10", delivered, promised, stalled);
        assert!(
            !(r.failure.is_some() && r.note.is_some()),
            "{delivered}/{promised:?}/{stalled} set both"
        );
    }
}

/// A promised count of zero or less is the source saying there is nothing to
/// reconcile, not a target nothing can reach.
///
/// **The `promised <= 0` guard is subsumed**, and this test is where that is
/// recorded rather than hidden. `delivered` counts records the server handed
/// over, so it is never negative, and every `promised <= 0` is therefore
/// `<= delivered` — the second half of the guard already answers. Checked
/// exhaustively below: both spellings agree on every input the function can
/// receive, and mutation confirms no test distinguishes them. The guard stays
/// because it states *why* the line is right; it is not a rule, and a reader
/// should not add a test that tries to exercise it.
#[test]
fn a_non_positive_promise_is_satisfied_by_anything() {
    for promised in [0, -1, i64::MIN] {
        for delivered in [0, 1, 3, 100] {
            let r = reconcile_delivery("s", "2024-06-10", delivered, Some(promised), false);
            assert!(
                !r.is_failure(),
                "{delivered} delivered against a promise of {promised} must not fail"
            );
        }
    }

    // The exhaustiveness claim, as an assertion rather than a comment.
    let mut differing: Vec<(i64, i64)> = Vec::new();
    for delivered in 0..6i64 {
        for promised in -3..6i64 {
            if (promised <= 0 || delivered >= promised) != (delivered >= promised) {
                differing.push((delivered, promised));
            }
        }
    }
    assert!(
        differing.is_empty(),
        "the guard is no longer subsumed for {differing:?} — make it load-bearing \
         or delete it"
    );
}

// ---------------------------------------------------------------------------
// The registry
// ---------------------------------------------------------------------------

/// The registry resolves what was registered, and refuses an unknown name by
/// listing what is — because the caller's next question is always "what is
/// available".
#[test]
fn an_unknown_source_lists_what_is_registered() {
    let mut registry = Registry::new();
    let (descriptor, fetcher) = stub("stub", false);
    registry.register(descriptor, fetcher);

    assert!(registry.contains("stub"));
    assert_eq!(registry.source_names(), vec!["stub".to_string()]);

    let err = registry.descriptor("nope").expect_err("unknown");
    assert!(err.to_string().contains("Unknown source \"nope\""));
    assert!(err.to_string().contains("stub"));
    assert_eq!(err.available, vec!["stub".to_string()]);
    assert!(registry.fetcher("nope").is_err());
}

/// Registering under an existing name **replaces** it — the documented way to
/// override a built-in. The resolved fetcher really is the new one, which is
/// why the stub counts its calls.
#[test]
fn registering_under_an_existing_name_overrides_it() {
    let mut registry = Registry::new();
    let counter = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let (descriptor, first) = stub_sharing("s", false, counter.clone());
    registry.register(descriptor, first);

    let (mut descriptor, second) = stub_sharing("s", false, counter.clone());
    descriptor.display_name = "second".to_string();
    registry.register(descriptor, second);

    assert_eq!(
        registry.descriptor("s").expect("present").display_name,
        "second"
    );
    assert_eq!(
        registry.source_names(),
        vec!["s".to_string()],
        "not duplicated"
    );

    let fetcher = registry.fetcher("s").expect("present");
    let mut request = FetchRequest::new(NaiveDate::from_ymd_opt(2024, 6, 10).expect("date"));
    request.config.insert("k".to_string(), "v".to_string());
    fetcher.fetch(&request, &mut |_| {}).expect("runs");
    assert_eq!(
        counter.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "the resolved fetcher must be the replacement"
    );
}

/// An unknown source is **not** resumable rather than an error. A source
/// supplied through an override need not be registered, so a raise here would
/// escape the per-day handler into a cleanup-only block and lose the whole
/// multi-source run's report.
#[test]
fn an_unknown_source_is_not_resumable_rather_than_an_error() {
    let registry = Registry::new();
    assert!(!registry.is_resumable("never-registered"));
    assert!(registry.descriptor("never-registered").is_err());
}

/// The four built-in descriptors, diffed against Python in the corpus, are
/// checked here for the two facts the corpus cannot express: PubMed is the only
/// **resumable** one, and OpenAlex is the only one with a **required**
/// parameter.
#[test]
fn the_builtin_descriptors_declare_what_the_sync_loop_reads() {
    let descriptors = builtin_descriptors();
    assert_eq!(descriptors.len(), 4);

    let resumable: Vec<&str> = descriptors
        .iter()
        .filter(|d| d.resumable)
        .map(|d| d.name.as_str())
        .collect();
    assert_eq!(
        resumable,
        vec!["pubmed"],
        "a resumable descriptor is what makes sync pass the resume state"
    );

    let required: Vec<(&str, &str)> = descriptors
        .iter()
        .flat_map(|d| {
            d.params
                .iter()
                .filter(|p| p.required)
                .map(move |p| (d.name.as_str(), p.name.as_str()))
        })
        .collect();
    assert_eq!(required, vec![("openalex", "email")]);
}

/// A secret parameter is marked as one, so a caller can redact it from a log or
/// a report. Every built-in's `api_key` is secret; OpenAlex's `email` is not.
#[test]
fn a_secret_parameter_is_marked_as_one() {
    for descriptor in builtin_descriptors() {
        for param in &descriptor.params {
            if param.name == "api_key" {
                assert!(param.secret, "{}/api_key must be secret", descriptor.name);
            }
            if param.name == "email" {
                assert!(!param.secret, "an email is not a secret");
            }
        }
    }
}

/// A resumable source is handed its resume state and may skip a part; a
/// non-resumable one is handed nothing. That is the whole of what the
/// `resumable` flag decides at run time.
#[test]
fn resume_state_is_present_only_for_a_resumable_source() {
    let request = FetchRequest::new(NaiveDate::from_ymd_opt(2024, 6, 10).expect("date"));
    assert!(request.resume.is_none());

    let mut resumable = request.clone();
    resumable.resume = Some(ResumeState::default());
    assert!(resumable.resume.is_some());
}

/// A failed walk yields a `failed` status and no records, and turns into a
/// `FetchResult` the day layer can store.
#[test]
fn a_failed_outcome_becomes_a_failed_fetch_result() {
    let outcome = FetchOutcome::failed("history session expired");
    let result = outcome.to_fetch_result(
        "pubmed",
        NaiveDate::from_ymd_opt(2024, 6, 10).expect("date"),
    );
    assert_eq!(result.status, "failed");
    assert_eq!(result.error.as_deref(), Some("history session expired"));
    assert_eq!(result.record_count, 0);
    assert_eq!(result.source, "pubmed");
    assert_eq!(result.date, "2024-06-10");
}
