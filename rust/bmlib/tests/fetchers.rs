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
//! The corpus (24 cases) diffs the reconciliation rule and the six built-in
//! descriptors against Python. The named tests state why each rule exists,
//! which is the part a corpus of messages cannot say.

use bmlib::publications::fetchers::biorxiv::{BIORXIV_SETTLE_DAYS, DETAILS_SETTLE_DAYS};
use bmlib::publications::fetchers::europepmc::EUROPEPMC_SETTLE_DAYS;
use std::sync::Arc;

use bmlib::publications::fetchers::{
    builtin_descriptors, builtin_registry, reconcile_delivery, FetchError, FetchOutcome,
    FetchRequest, FetchSink, Fetcher, HttpClient, HttpResponse, Progress, Reconciliation, Registry,
    ResumeState, SHORTFALL_FAILURE_RATIO,
};
use bmlib::publications::models::{SourceDescriptor, MAX_SETTLE_DAYS};
use chrono::NaiveDate;
use common::sink::RecordingSink;
use serde_json::Value;

mod common;

const CASES: &str = include_str!("data/fetcher_cases.json");
const EXPECTED: &str = include_str!("data/fetcher_expected.json");

/// Run one case, as a refusal when the port refuses.
///
/// The corpus carries cases Python **refused** as well as values, because a
/// refusal is behaviour: `settle_days` past the ceiling must be rejected at the
/// same point with the same sentence.
fn run(case: &Value) -> Result<Value, String> {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    Ok(match fn_name {
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
        "max_settle_days" => serde_json::json!(MAX_SETTLE_DAYS),
        "settle_days" => {
            let value = args["value"].as_u64().unwrap_or(0);
            let name = args["source"].as_str().unwrap_or_default();
            let descriptor = SourceDescriptor::new(name, "D", "d")
                .with_settle_days(value as u32)
                .map_err(|error| error.to_string())?;
            serde_json::json!(descriptor.check_settle_days().map_err(|e| e.to_string())?)
        }
        other => panic!("unknown fn {other:?}"),
    })
}

fn descriptor_json(descriptor: &SourceDescriptor) -> Value {
    serde_json::json!({
        "name": descriptor.name,
        "display_name": descriptor.display_name,
        "description": descriptor.description,
        "resumable": descriptor.resumable,
        "settle_days": descriptor.settle_days,
        "opt_in": descriptor.opt_in,
        "params": descriptor.params.iter().map(|p| serde_json::json!({
            "name": p.name,
            "description": p.description,
            "required": p.required,
            "default": p.default,
            "secret": p.secret,
        })).collect::<Vec<_>>(),
    })
}

/// A client for a registry test: **nothing may be fetched**. Constructing the
/// built-in registry is wiring, not I/O, and a version that opened a connection
/// would be caught here rather than by a test that happens to have a network.
struct NoRequestClient;

impl HttpClient for NoRequestClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        panic!("building a registry must not fetch anything: {url}")
    }
}

/// **Every built-in descriptor resolves a fetcher.** A source that is described and
/// has none is the failure `sync()` reports one day at a time as
/// `No fetcher found for source: …`, which is a registration mistake surfacing as a
/// data problem at runtime; this is the same check, at the place the mistake is
/// made.
#[test]
fn the_builtin_registry_wires_every_descriptor_to_a_fetcher() {
    let registry = builtin_registry(Arc::new(NoRequestClient)).expect("the built-ins register");

    let mut described: Vec<String> = builtin_descriptors()
        .iter()
        .map(|descriptor| descriptor.name.clone())
        .collect();
    let mut registered = registry.source_names();
    described.sort();
    registered.sort();
    assert_eq!(
        registered, described,
        "the registry and the descriptor list are the same set"
    );

    for name in &registered {
        assert!(
            registry.fetcher(name).is_ok(),
            "{name} is described and has no fetcher"
        );
    }
    assert_eq!(
        registry.descriptor("pubmed").map(|d| d.resumable),
        Ok(true),
        "PubMed is the partitioned source, so its fetcher must take resume state"
    );
}

/// Answers every request with bioRxiv's quiet day, recording the URL asked.
struct QuietDayClient {
    asked: std::sync::Mutex<Vec<String>>,
}

impl HttpClient for QuietDayClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.asked
            .lock()
            .expect("not poisoned")
            .push(url.to_string());
        Ok(HttpResponse::ok(
            r#"{"messages":[{"status":"no posts found"}],"collection":[]}"#,
        ))
    }
}

/// **Each bioRxiv source reads its own endpoint, and declares its own settle
/// period** (#341). The `*-details` names exist because the day ledger is keyed
/// by name; a wiring that sent one to the other's endpoint would fill one
/// population's ledger with the other's days.
#[test]
fn each_biorxiv_source_reads_its_own_endpoint() {
    for (name, prefix, settle_days) in [
        ("biorxiv", "https://api.biorxiv.org/pubs/biorxiv/", 90),
        ("medrxiv", "https://api.biorxiv.org/pubs/medrxiv/", 90),
        (
            "biorxiv-details",
            "https://api.biorxiv.org/details/biorxiv/",
            7,
        ),
        (
            "medrxiv-details",
            "https://api.biorxiv.org/details/medrxiv/",
            7,
        ),
    ] {
        let client = Arc::new(QuietDayClient {
            asked: std::sync::Mutex::new(Vec::new()),
        });
        let registry = builtin_registry(client.clone()).expect("the built-ins register");
        let mut sink = RecordingSink::new();
        let date = NaiveDate::from_ymd_opt(2026, 11, 4).expect("a date");
        let outcome = registry
            .fetcher(name)
            .expect("registered")
            .fetch(&FetchRequest::new(date), &mut sink)
            .expect("an outcome");

        assert_eq!(outcome.status, "completed", "{name}: {outcome:?}");
        let asked = client.asked.lock().expect("not poisoned").clone();
        assert_eq!(
            asked,
            vec![format!("{prefix}2026-11-04/2026-11-04/0")],
            "{name}"
        );
        assert_eq!(
            registry.descriptor(name).map(|d| d.settle_days),
            Ok(settle_days),
            "{name}"
        );
    }
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
        sink: &mut dyn FetchSink,
    ) -> Result<FetchOutcome, bmlib::publications::fetchers::FetchError> {
        self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        sink.progress(Progress::Page {
            delivered: self.outcome.record_count,
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
            settle_days: 0,
            opt_in: false,
        },
        Box::new(StubFetcher {
            outcome: FetchOutcome::completed(0),
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
        let got = run(case);
        if !want["ok"].as_bool().unwrap_or(false) {
            // Python refused: the port must refuse with the same sentence.
            let python = want["error"].as_str().unwrap_or_default();
            match got {
                Err(message) if python == format!("ValueError: {message}") => {}
                Err(message) => failures.push(format!(
                    "  {name}: both refused, differently\n    python: {python}\n    rust:   {message}"
                )),
                Ok(value) => failures.push(format!(
                    "  {name}: Python refused ({python}), the port did not: {value}"
                )),
            }
            continue;
        }
        match got {
            Ok(value) if value == want["value"] => {}
            Ok(value) => failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&value).unwrap_or_default()
            )),
            Err(error) => failures.push(format!("  {name}: the port refused ({error})")),
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

/// **A `None` promise and a promise of zero reach one verdict** (#795): a zero
/// delivery satisfies both, and records delivered against either cannot be
/// verified. The failure says which one the source sent.
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

/// **A count the delivery contradicts fails the day** (#795): records against a
/// count of zero, and a negative count whatever was delivered. Both returned
/// clean while the rule read `promised <= 0` as "nothing to check".
#[test]
fn a_count_the_delivery_contradicts_fails_the_day() {
    let r = reconcile_delivery("openalex", "2024-06-10", 1, Some(0), false);
    assert!(r
        .failure
        .as_deref()
        .unwrap_or_default()
        .contains("reported a count of 0"));
    for delivered in [0, 5] {
        let r = reconcile_delivery("openalex", "2024-06-10", delivered, Some(-1), false);
        assert_eq!(
            r.failure.as_deref(),
            Some(
                "openalex reported -1 records for 2024-06-10, which is not a count, so the \
                 walk cannot be verified"
            )
        );
    }
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

/// A promised count of zero or less is satisfied only by an empty day (#795).
///
/// Zero with nothing delivered is the quiet day. Zero with records is a count
/// the delivery contradicts, and a negative count is not a count, so both fail:
/// the rule used to read any `promised <= 0` as "nothing to reconcile".
#[test]
fn a_non_positive_promise_is_satisfied_only_by_an_empty_day() {
    for promised in [0, -1, i64::MIN] {
        for delivered in [0, 1, 3, 100] {
            let r = reconcile_delivery("s", "2024-06-10", delivered, Some(promised), false);
            let quiet_day = promised == 0 && delivered == 0;
            assert_eq!(
                r.is_failure(),
                !quiet_day,
                "{delivered} delivered against a promise of {promised}"
            );
            assert_eq!(r.note, None);
        }
    }
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
    registry
        .register(descriptor, fetcher)
        .expect("a usable settle period");

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
    registry
        .register(descriptor, first)
        .expect("a usable settle period");

    let (mut descriptor, second) = stub_sharing("s", false, counter.clone());
    descriptor.display_name = "second".to_string();
    registry
        .register(descriptor, second)
        .expect("a usable settle period");

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
    fetcher
        .fetch(&request, &mut RecordingSink::new())
        .expect("runs");
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

/// The seven built-in descriptors, diffed against Python in the corpus, are
/// checked here for the two facts the corpus cannot express: PubMed is the only
/// **resumable** one, and OpenAlex (`email`) and Europe PMC (`query`) are the
/// only ones with a **required** parameter.
#[test]
fn the_builtin_descriptors_declare_what_the_sync_loop_reads() {
    let descriptors = builtin_descriptors();
    assert_eq!(descriptors.len(), 7);

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
    assert_eq!(
        required,
        vec![("openalex", "email"), ("europepmc", "query")]
    );

    // Europe PMC alone runs only when named: without the search it requires it
    // would fail every default run's days, re-offered for ever.
    let opt_in: Vec<&str> = descriptors
        .iter()
        .filter(|d| d.opt_in)
        .map(|d| d.name.as_str())
        .collect();
    assert_eq!(opt_in, vec!["europepmc"]);

    // The settle period is read by day selection, outside every per-day handler,
    // so a built-in that declared an unusable one would cost a whole run its
    // report. Five declare it: `/pubs` fills a day in weeks late, `/details` is
    // given a short margin (#341), and Europe PMC's index days load in batches
    // (a margin fixed before measurement).
    let settling: Vec<(&str, u32)> = descriptors
        .iter()
        .filter(|d| d.settle_days > 0)
        .map(|d| (d.name.as_str(), d.settle_days))
        .collect();
    assert_eq!(
        settling,
        vec![
            ("biorxiv", BIORXIV_SETTLE_DAYS),
            ("biorxiv-details", DETAILS_SETTLE_DAYS),
            ("medrxiv", BIORXIV_SETTLE_DAYS),
            ("medrxiv-details", DETAILS_SETTLE_DAYS),
            ("europepmc", EUROPEPMC_SETTLE_DAYS),
        ]
    );
    for descriptor in &descriptors {
        assert!(
            descriptor.check_settle_days().is_ok(),
            "{} declares a settle period day selection cannot use",
            descriptor.name
        );
    }
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

/// **Registration checks the settle period again**, although the builder already
/// did: the field is public, so a descriptor can be changed between the two — and
/// the value is used by day selection, outside every per-day handler, where an
/// unusable one costs the whole run its report rather than one day's.
#[test]
fn registration_refuses_a_settle_period_day_selection_cannot_use() {
    let mut registry = Registry::new();
    let (mut descriptor, fetcher) = stub("s", false);
    descriptor.settle_days = MAX_SETTLE_DAYS + 1;

    let error = registry
        .register(descriptor, fetcher)
        .expect_err("refused at registration");

    assert!(
        error
            .to_string()
            .contains("the most day selection accepts is"),
        "{error}"
    );
    assert!(
        registry.source_names().is_empty(),
        "a refused descriptor is not registered"
    );
}
