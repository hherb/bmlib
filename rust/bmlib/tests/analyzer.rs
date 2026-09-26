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

//! The transparency analyzer — the oracle from the pure half, and named tests
//! over a **scripted `HttpClient`**.
//!
//! Two halves, because the module has two halves:
//!
//! * the pure helpers (the funder matcher, the four JSON coercers, the Europe
//!   PMC record walk, the trial-id heuristic, the nested-article lexer, the COI
//!   text scans, the PubMed signal parser and the merge rules) are driven by 328
//!   committed corpus cases generated from Python by
//!   `rust/oracle/dump_analyzer.py`;
//! * `analyze()` and the five network steps, which need a client, are driven by
//!   a transport whose every answer is scripted and whose every request is
//!   recorded — so "which URL was asked for" is an assertion and not a hope.
//!
//! The corpus's HTTP half does not exist on the Python side: those tests need a
//! live or faked `httpx` client, so they are named tests here rather than
//! oracle cases.

use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::transparency::analyzer::{
    discloses_industry_ties, encode_query, epmc_records, extract_coi_text, extract_tagged_coi_text,
    find_trial_ids, full_text_provenance_indicator, is_industry_funder, json_bool, json_count,
    json_object, json_text, merge_pubmed_signals, note_full_text_provenance, parse_pubmed_signals,
    pmid_from_epmc, score_data_availability, strip_nested_articles, user_agent, Analysis,
    PubMedSignals, TransparencyAnalyzer, DATA_PATTERNS, INDICATOR_COI_IN_PUBMED,
    INDICATOR_COI_UNKNOWN, INDICATOR_FUNDERS_NOT_READABLE, INDICATOR_NO_COI_IN_FULLTEXT,
    INDICATOR_NO_FUNDER_INFO, INDICATOR_NO_POSTED_RESULTS, INDICATOR_RESULTS_NOT_CHECKABLE,
    MAX_TRANSPARENCY_SCORE, SCORE_FUNDER_INFO, SCORE_OPEN_ACCESS, SCORE_RESULTS_POSTED,
    SCORE_TRIAL_REGISTERED,
};
use bmlib::transparency::models::{
    FullTextStatus, TransparencyRisk, TransparencySettings, TransparencyUnknownReason,
    TrialResultsStatus,
};
use serde_json::{json, Value};
use std::sync::Mutex;
use std::time::Duration;

const CASES: &str = include_str!("data/analyzer_cases.json");
const EXPECTED: &str = include_str!("data/analyzer_expected.json");

// ---------------------------------------------------------------------------
// The oracle: the pure helpers
// ---------------------------------------------------------------------------

/// Rebuild the Python driver's `_analysis()` from a case's arguments.
fn analysis_from(args: &Value) -> Analysis {
    let mut analysis = Analysis::default();
    if let Some(indicators) = args.get("indicators").and_then(Value::as_array) {
        analysis.indicators = indicators
            .iter()
            .map(|value| value.as_str().unwrap_or_default().to_string())
            .collect();
    }
    if let Some(value) = args.get("score").and_then(Value::as_i64) {
        analysis.score = value;
    }
    if let Some(value) = args.get("industry_funding").and_then(Value::as_bool) {
        analysis.industry_funding = value;
    }
    if let Some(value) = args.get("industry_confidence").and_then(Value::as_f64) {
        analysis.industry_confidence = value;
    }
    if let Some(value) = args.get("data_level").and_then(Value::as_str) {
        analysis.data_level = value.to_string();
    }
    if let Some(value) = args.get("coi_disclosed") {
        analysis.coi_disclosed = value.as_bool();
    }
    for (key, field) in [
        ("trial_registered", &mut analysis.trial_registered),
        ("results_compliant", &mut analysis.results_compliant),
        ("full_text_analyzed", &mut analysis.full_text_analyzed),
        ("funder_info_scored", &mut analysis.funder_info_scored),
    ] {
        if let Some(value) = args.get(key).and_then(Value::as_bool) {
            *field = value;
        }
    }
    if let Some(value) = args.get("full_text_status").and_then(Value::as_str) {
        analysis.full_text_status = FullTextStatus::parse(value).expect("corpus status");
    }
    if let Some(value) = args.get("trial_results_status").and_then(Value::as_str) {
        analysis.trial_results_status = TrialResultsStatus::parse(value).expect("corpus status");
    }
    analysis
}

/// The Python driver's `_analysis_out()`.
fn analysis_out(analysis: &Analysis) -> Value {
    json!({
        "score": analysis.score,
        "indicators": analysis.indicators,
        "industry_funding": analysis.industry_funding,
        "industry_confidence": analysis.industry_confidence,
        "data_level": analysis.data_level,
        "coi_disclosed": analysis.coi_disclosed,
        "trial_registered": analysis.trial_registered,
        "results_compliant": analysis.results_compliant,
        "full_text_analyzed": analysis.full_text_analyzed,
        "funder_info_scored": analysis.funder_info_scored,
        "full_text_status": analysis.full_text_status.as_str(),
        "trial_results_status": analysis.trial_results_status.as_str(),
    })
}

fn signals_from(args: &Value) -> PubMedSignals {
    let list = |key: &str| -> Vec<String> {
        args.get(key)
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .map(|value| value.as_str().unwrap_or_default().to_string())
                    .collect()
            })
            .unwrap_or_default()
    };
    PubMedSignals {
        coi_statement: args
            .get("coi_statement")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        trial_accessions: list("trial_accessions"),
        registration_not_checkable: args
            .get("registration_not_checkable")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        funders: list("funders"),
        deposition_databanks: list("deposition_databanks"),
    }
}

fn signals_out(signals: &PubMedSignals) -> Value {
    json!({
        "coi_statement": signals.coi_statement,
        "trial_accessions": signals.trial_accessions,
        "registration_not_checkable": signals.registration_not_checkable,
        "funders": signals.funders,
        "deposition_databanks": signals.deposition_databanks,
    })
}

fn run_case(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let a = &case["args"];
    match fn_name {
        "is_industry_funder" => json!(is_industry_funder(a["name"].as_str().unwrap_or_default())),
        "json_object" => json!(json_object(&a["value"])),
        "json_text" => json!(json_text(&a["value"])),
        "json_count" => json!(json_count(&a["value"])),
        "json_bool" => json!(json_bool(&a["value"])),
        "epmc_records" => json!(epmc_records(&a["epmc"])),
        "find_trial_ids" => json!(find_trial_ids(a.get("epmc"))),
        "pmid_from_epmc" => json!(pmid_from_epmc(a.get("epmc"))),
        "strip_nested_articles" => {
            match strip_nested_articles(a["xml"].as_str().unwrap_or_default()) {
                Ok(Some(text)) => json!(text),
                Ok(None) => Value::Null,
                Err(error) => json!({ "refused": error.to_string() }),
            }
        }
        "extract_tagged_coi_text" => {
            json!(extract_tagged_coi_text(
                a["full_text"].as_str().unwrap_or_default()
            ))
        }
        "extract_coi_text" => json!(extract_coi_text(
            a["full_text"].as_str().unwrap_or_default(),
            None
        )),
        "discloses_industry_ties" => {
            json!(discloses_industry_ties(
                a["coi_text"].as_str().unwrap_or_default()
            ))
        }
        "parse_pubmed_signals" => signals_out(&parse_pubmed_signals(
            a["xml"].as_str().unwrap_or_default(),
            a["pmid"].as_str().unwrap_or_default(),
        )),
        "merge_pubmed_signals" => {
            let mut analysis = analysis_from(a.get("analysis").unwrap_or(&Value::Null));
            merge_pubmed_signals(&signals_from(&a["pubmed"]), &mut analysis);
            analysis_out(&analysis)
        }
        "note_full_text_provenance" => {
            let mut analysis = analysis_from(a);
            note_full_text_provenance(&mut analysis);
            analysis_out(&analysis)
        }
        "score_data_availability" => {
            let mut analysis = analysis_from(a);
            score_data_availability(&mut analysis);
            analysis_out(&analysis)
        }
        "note_data_level" => {
            let level = a["level"].as_str().unwrap_or_default();
            if bmlib::transparency::analyzer::data_level_rank(level).is_none() {
                // Python raises `KeyError: 'bogus'`; the panic path is exercised
                // here so the corpus still pins it.
                let panicked = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    analysis_from(a).note_data_level(level);
                }))
                .is_err();
                assert!(panicked, "an unknown level must panic");
                return json!({ "ok": false, "error": format!("KeyError: '{level}'") });
            }
            let mut analysis = analysis_from(a);
            analysis.note_data_level(level);
            json!(analysis.data_level)
        }
        "user_agent" => json!(user_agent(
            a["email"].as_str().unwrap_or_default(),
            a["version"].as_str().unwrap_or_default()
        )),
        "data_patterns" => json!(DATA_PATTERNS),
        other => panic!("unknown fn {other:?}"),
    }
}

/// The port agrees with Python on every committed case.
#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), wants.len(), "corpus sizes");
    assert!(cases.len() >= 300, "the corpus is the net: {}", cases.len());

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        let got = run_case(case);
        if want["ok"].as_bool().unwrap_or(false) {
            if got != want["value"] {
                failures.push(format!(
                    "  {name}\n    python: {}\n    rust:   {}",
                    serde_json::to_string(&want["value"]).unwrap_or_default(),
                    serde_json::to_string(&got).unwrap_or_default()
                ));
            }
        } else {
            // The one expected refusal: Python's `KeyError`, which the arm above
            // synthesises after proving the panic fires.
            if got["error"] != want["error"] {
                failures.push(format!(
                    "  {name}\n    python: {}\n    rust:   {}",
                    want["error"], got["error"]
                ));
            }
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

/// The lexer recognises exactly the restated element set, and nothing whose
/// name merely begins with one of its members.
///
/// The Python lexer builds its alternation *from* `_NESTED_ARTICLE_ELEMENTS`;
/// a hand-rolled scanner cannot, so this is the guard that the two copies have
/// not drifted — the rule is enforced by a test rather than by the shape of a
/// regex.
#[test]
fn the_lexer_recognises_exactly_the_restated_element_set() {
    for element in bmlib::transparency::analyzer::NESTED_ARTICLE_ELEMENTS {
        let xml = format!("<article>A<{element}>R</{element}>B</article>");
        assert_eq!(
            strip_nested_articles(&xml).expect("well formed"),
            Some("<article>AB</article>".to_string()),
            "{element} must be stripped"
        );
    }
    for near in [
        "sub-article-x",
        "response-note",
        "SUB-ARTICLE",
        "sub-articles",
    ] {
        let xml = format!("<article>A<{near}>R</{near}>B</article>");
        assert_eq!(
            strip_nested_articles(&xml).expect("well formed"),
            Some(xml.clone()),
            "{near} is not one of the two elements"
        );
    }
}

/// The provenance partition is total: every status either has a line or is
/// `ANALYZED`, and `ANALYZED` has none.
#[test]
fn every_status_says_what_happened() {
    for status in FullTextStatus::ALL {
        let line = full_text_provenance_indicator(*status);
        match status {
            FullTextStatus::Analyzed => assert!(line.is_none(), "{status:?} must stay silent"),
            other => assert!(line.is_some(), "{other:?} must say what happened"),
        }
        // The two collections are a partition: a member in neither is a defect.
        assert!(
            line.is_some()
                || bmlib::transparency::analyzer::STATUSES_WITH_NO_PROVENANCE_LINE.contains(status),
            "{status:?} is in neither collection"
        );
    }
}

/// The Python side files an absent `hasResults` as a *finding*; that is issue
/// #210 and it is reproduced rather than fixed, so it is pinned end to end: a
/// ClinicalTrials.gov body narrowing itself to `hasResults` and then omitting it
/// still stores "registered trial without posted results".
#[test]
fn an_absent_has_results_is_still_a_finding() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(None, "123", "N", "")]),
        )
        .route(
            &efetch_url("123"),
            200,
            &pubmed_databanks(&[("ClinicalTrials.gov", &["NCT12345678"])]),
        )
        // The request narrows itself to `hasResults` and the answer omits it.
        .route(&ct_url("NCT12345678"), 200, "{}");
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::NotPosted)
    );
    assert!(!result.trial_results_compliant);
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_NO_POSTED_RESULTS));
}

// ---------------------------------------------------------------------------
// The scripted transport
// ---------------------------------------------------------------------------

/// One scripted answer: a response, or a transport failure.
#[derive(Debug, Clone)]
enum Answer {
    Response(u16, String),
    Fail(String),
}

/// A transport whose every answer is scripted and whose every request is
/// recorded.
///
/// **It matches the whole URL, not a suffix.** The Python suite learned this the
/// hard way: a fake that accepted any `url.endswith("/fullTextXML")` confirmed
/// only that *something* was asked for, so issue #184 — an extra `{source}/`
/// segment that made every live fetch 404 — passed 236 of 236 tests. An
/// unmatched URL answers 404, which is not a 200 and so marks nothing reachable.
struct ScriptedClient {
    routes: Mutex<Vec<(String, Answer)>>,
    seen: Mutex<Vec<String>>,
}

impl ScriptedClient {
    fn new() -> Self {
        ScriptedClient {
            routes: Mutex::new(Vec::new()),
            seen: Mutex::new(Vec::new()),
        }
    }

    fn route(self, url: &str, status: u16, body: &str) -> Self {
        self.routes
            .lock()
            .expect("lock")
            .push((url.to_string(), Answer::Response(status, body.to_string())));
        self
    }

    fn route_fail(self, url: &str, message: &str) -> Self {
        self.routes
            .lock()
            .expect("lock")
            .push((url.to_string(), Answer::Fail(message.to_string())));
        self
    }

    fn urls(&self) -> Vec<String> {
        self.seen.lock().expect("lock").clone()
    }
}

impl HttpClient for ScriptedClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.seen.lock().expect("lock").push(url.to_string());
        let routes = self.routes.lock().expect("lock");
        for (pattern, answer) in routes.iter() {
            if pattern == url {
                return match answer {
                    Answer::Response(status, body) => Ok(HttpResponse {
                        status: *status,
                        body: body.clone().into_bytes(),
                    }),
                    Answer::Fail(message) => Err(FetchError::Transport(message.clone())),
                };
            }
        }
        Ok(HttpResponse {
            status: 404,
            body: Vec::new(),
        })
    }
}

const EMAIL: &str = "test@example.com";

fn crossref_url(doi: &str) -> String {
    format!("https://api.crossref.org/works/{doi}")
}

fn openalex_url(doi: &str) -> String {
    format!("https://api.openalex.org/works/doi:{doi}")
}

fn ct_url(nct: &str) -> String {
    format!("https://clinicaltrials.gov/api/v2/studies/{nct}?fields=hasResults")
}

fn fulltext_url(ext_id: &str) -> String {
    format!("https://www.ebi.ac.uk/europepmc/webservices/rest/{ext_id}/fullTextXML")
}

fn search_url(query: &str) -> String {
    format!(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?{}",
        encode_query(&[("query", query), ("format", "json"), ("resultType", "core")])
    )
}

fn efetch_url(pmid: &str) -> String {
    format!(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{}",
        encode_query(&[
            ("db", "pubmed"),
            ("id", pmid),
            ("retmode", "xml"),
            ("tool", "bmlib"),
            ("email", EMAIL),
        ])
    )
}

fn default_settings() -> TransparencySettings {
    TransparencySettings::default()
}

/// An analyzer that does not sleep between requests.
fn analyzer_with(settings: TransparencySettings) -> TransparencyAnalyzer {
    let mut analyzer = TransparencyAnalyzer::new(EMAIL, None, settings);
    analyzer.min_request_interval = Duration::ZERO;
    analyzer
}

fn harness(settings: TransparencySettings) -> (TransparencyAnalyzer, ScriptedClient) {
    (analyzer_with(settings), ScriptedClient::new())
}

/// A Europe PMC search body carrying the given records.
fn search_body(records: Vec<Value>) -> String {
    json!({ "resultList": { "result": records } }).to_string()
}

/// A Europe PMC record.
fn epmc_record(pmcid: Option<&str>, id: &str, in_epmc: &str, abstract_text: &str) -> Value {
    let mut record = json!({
        "source": "MED",
        "id": id,
        "inEPMC": in_epmc,
        "abstractText": abstract_text,
    });
    if let Some(pmcid) = pmcid {
        record["pmcid"] = json!(pmcid);
    }
    record
}

/// A full-text body that arrives whole and carries no COI cue.
fn whole_article(body: &str) -> String {
    format!("<article><body>{body}</body></article>")
}

// ---------------------------------------------------------------------------
// CrossRef
// ---------------------------------------------------------------------------

/// A well-formed CrossRef body names the funder, and an industry one is
/// recorded with the structured confidence.
#[test]
fn a_well_formed_crossref_body_names_the_funder() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &crossref_url("10.1/x"),
            200,
            &json!({"message": {"funder": [{"name": "Pfizer Inc"}]}}).to_string(),
        )
        .route(&search_url("DOI:\"10.1/x\""), 200, &search_body(vec![]));
    let result = analyzer.analyze(&client, "doc-1", None, Some("10.1/x"));

    assert!(result.industry_funding_detected);
    assert!(
        result
            .risk_indicators
            .iter()
            .any(|line| line == "Industry funder: Pfizer Inc"),
        "{:?}",
        result.risk_indicators
    );
    assert!(result.transparency_score >= SCORE_FUNDER_INFO);
    assert_eq!(result.validate(), Ok(()));
}

/// **Issue #307.** A body with no readable `message` must not be reported as a
/// record stating no funders.
#[test]
fn a_body_with_no_readable_message_does_not_claim_no_funders() {
    for body in [
        json!({"status": "ok"}).to_string(),
        json!({"message": null}).to_string(),
        json!({"message": "not an object"}).to_string(),
        json!({"message": []}).to_string(),
        json!({"message": 7}).to_string(),
    ] {
        let (analyzer, client) = harness(default_settings());
        let client = client.route(&crossref_url("10.1/x"), 200, &body).route(
            &search_url("DOI:\"10.1/x\""),
            200,
            &search_body(vec![]),
        );
        let result = analyzer.analyze(&client, "doc-1", None, Some("10.1/x"));

        assert!(
            result
                .risk_indicators
                .iter()
                .any(|line| line == INDICATOR_FUNDERS_NOT_READABLE),
            "{body}: {:?}",
            result.risk_indicators
        );
        assert!(
            !result
                .risk_indicators
                .iter()
                .any(|line| line == INDICATOR_NO_FUNDER_INFO),
            "{body}: the exchange was unreadable, not empty: {:?}",
            result.risk_indicators
        );
    }
}

/// The two shapes that *do* mean CrossRef holds nothing keep the old line.
#[test]
fn a_readable_message_with_no_funder_still_claims_no_funders() {
    for body in [
        json!({"message": {}}).to_string(),
        json!({"message": {"funder": null}}).to_string(),
        json!({"message": {"funder": []}}).to_string(),
    ] {
        let (analyzer, client) = harness(default_settings());
        let client = client.route(&crossref_url("10.1/x"), 200, &body).route(
            &search_url("DOI:\"10.1/x\""),
            200,
            &search_body(vec![]),
        );
        let result = analyzer.analyze(&client, "doc-1", None, Some("10.1/x"));
        assert!(
            result
                .risk_indicators
                .iter()
                .any(|line| line == INDICATOR_NO_FUNDER_INFO),
            "{body}: {:?}",
            result.risk_indicators
        );
    }
}

/// A `funder` that arrives as an object, a string or a number is not "no funder
/// information" either — the split-out line exists for exactly that shape.
#[test]
fn a_wrong_typed_funder_is_not_readable() {
    for funder in [
        json!({"name": "Acme Pharmaceuticals Inc"}),
        json!("Acme"),
        json!(7),
    ] {
        let (analyzer, client) = harness(default_settings());
        let client = client
            .route(
                &crossref_url("10.1/x"),
                200,
                &json!({"message": {"funder": funder}}).to_string(),
            )
            .route(&search_url("DOI:\"10.1/x\""), 200, &search_body(vec![]));
        let result = analyzer.analyze(&client, "doc-1", None, Some("10.1/x"));
        assert!(
            result
                .risk_indicators
                .iter()
                .any(|line| line == INDICATOR_FUNDERS_NOT_READABLE),
            "{:?}",
            result.risk_indicators
        );
        assert!(!result.industry_funding_detected);
    }
}

// ---------------------------------------------------------------------------
// UNKNOWN paths
// ---------------------------------------------------------------------------

/// **Issue #306.** Nothing was measured, so the COI status is `None` and not a
/// determinate `Some(true)`.
#[test]
fn coi_disclosed_is_none_when_nothing_was_measured() {
    let disabled = analyzer_with(TransparencySettings {
        enabled: false,
        ..TransparencySettings::default()
    });
    let empty = ScriptedClient::new();
    let result = disabled.analyze(&empty, "doc-1", Some("123"), Some("10.1/x"));
    assert_eq!(result.risk_level, TransparencyRisk::Unknown);
    assert_eq!(
        result.unknown_reason,
        Some(TransparencyUnknownReason::Disabled)
    );
    assert_eq!(
        result.coi_disclosed, None,
        "a determinate claim on a run that measured nothing"
    );
    assert_eq!(result.full_text_status, Some(FullTextStatus::NotAttempted));
    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::NotRegistered)
    );
    assert_eq!(result.validate(), Ok(()));

    let (identifierless, client) = harness(default_settings());
    let result = identifierless.analyze(&client, "doc-1", None, None);
    assert_eq!(
        result.unknown_reason,
        Some(TransparencyUnknownReason::NoIdentifier)
    );
    assert_eq!(result.coi_disclosed, None);
    assert!(client.urls().is_empty(), "no identifier, no request");
}

/// A total outage is `UNKNOWN`, and the reason says the APIs were unreachable.
#[test]
fn an_unreachable_api_yields_unknown_with_the_right_reason() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route_fail(&search_url("EXT_ID:123"), "connection reset")
        .route_fail(&efetch_url("123"), "connection reset");
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(result.risk_level, TransparencyRisk::Unknown);
    assert_eq!(
        result.unknown_reason,
        Some(TransparencyUnknownReason::Unreachable)
    );
    assert_eq!(result.coi_disclosed, None);
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line.contains("unreachable")));
    assert_eq!(result.validate(), Ok(()));
}

/// A failed *search* beside a reachable endpoint stores `SEARCH_FAILED`, not the
/// default `NOT_ATTEMPTED` — issue #193's whole point.
#[test]
fn a_search_failure_stores_search_failed() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route_fail(&search_url("EXT_ID:123"), "connection reset")
        .route(&efetch_url("123"), 200, "<PubmedArticleSet/>");
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(result.full_text_status, Some(FullTextStatus::SearchFailed));
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_COI_UNKNOWN));
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line.contains("the EuropePMC search produced no answer")));
}

// ---------------------------------------------------------------------------
// Full-text statuses
// ---------------------------------------------------------------------------

/// Every full-text refusal is a distinct stored status, driven through
/// `analyze()` over the scripted transport.
#[test]
fn the_full_text_statuses_are_distinct() {
    let cases: Vec<(&str, u16, &str, FullTextStatus)> = vec![
        ("404", 404, "", FullTextStatus::NotServed),
        ("429", 429, "", FullTextStatus::RequestFailed),
        ("503", 503, "", FullTextStatus::RequestFailed),
        ("empty-200", 200, "", FullTextStatus::RequestFailed),
        (
            "truncated",
            200,
            "<article><body>x</body>",
            FullTextStatus::Truncated,
        ),
        (
            "unclosed-region",
            200,
            "<article><body>x</body><sub-article><body>y</body></article>",
            FullTextStatus::UnclosedRegion,
        ),
        (
            "entirely-nested",
            200,
            "<sub-article><body>y</body></sub-article>",
            FullTextStatus::EntirelyNested,
        ),
        (
            "unterminated-markup",
            200,
            "<article></article><!--",
            FullTextStatus::UnterminatedMarkup,
        ),
    ];
    for (name, status, body, want) in cases {
        let (analyzer, client) = harness(default_settings());
        let client = client
            .route(
                &search_url("EXT_ID:123"),
                200,
                &search_body(vec![epmc_record(Some("PMC123"), "123", "Y", "")]),
            )
            .route(&fulltext_url("PMC123"), status, body);
        let result = analyzer.analyze(&client, "doc-1", Some("123"), None);
        assert_eq!(result.full_text_status, Some(want), "{name}");
        assert!(!result.full_text_analyzed, "{name}");
        assert_eq!(
            result.coi_disclosed, None,
            "{name}: no COI status was measured"
        );
        assert_eq!(result.validate(), Ok(()), "{name}");
    }
}

/// A served, whole, article-shaped body is scanned: `full_text_analyzed` is set
/// and a missing disclosure becomes an explicit `Some(false)`.
#[test]
fn a_missing_disclosure_in_scanned_full_text_is_false() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(Some("PMC123"), "123", "Y", "")]),
        )
        .route(
            &fulltext_url("PMC123"),
            200,
            &whole_article("no disclosures here"),
        );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(result.full_text_status, Some(FullTextStatus::Analyzed));
    assert!(result.full_text_analyzed);
    assert_eq!(result.coi_disclosed, Some(false));
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_NO_COI_IN_FULLTEXT));
}

/// **The URL carries the accession alone.** A `{source}/` segment was issue
/// #184: every live fetch 404'd and nothing said so.
#[test]
fn the_full_text_url_is_the_accession_alone() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(Some("PMC123"), "123", "Y", "")]),
        )
        .route(&fulltext_url("PMC123"), 200, &whole_article("x"));
    let _ = analyzer.analyze(&client, "doc-1", Some("123"), None);

    let urls = client.urls();
    assert!(
        urls.iter().any(|url| url == &fulltext_url("PMC123")),
        "the whole URL was not asked for: {urls:?}"
    );
    assert!(
        !urls.iter().any(|url| url.contains("/PMC/PMC123/")),
        "a source segment is back: {urls:?}"
    );
}

/// A record with no *accession* is not fetched by its bare id: the answer is
/// known before the request leaves.
#[test]
fn a_record_with_no_accession_is_not_fetched() {
    let (analyzer, client) = harness(default_settings());
    let client = client.route(
        &search_url("EXT_ID:123"),
        200,
        &search_body(vec![epmc_record(None, "12345678", "Y", "")]),
    );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(
        !client.urls().iter().any(|url| url.contains("fullTextXML")),
        "a request whose answer is known is not made: {:?}",
        client.urls()
    );
    assert_eq!(result.full_text_status, Some(FullTextStatus::NotAttempted));
}

/// A lowercase accession is still an address — case-insensitivity is measured,
/// not a courtesy.
#[test]
fn a_lowercase_accession_is_fetched() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(Some("pmc4154587"), "123", "Y", "")]),
        )
        .route(&fulltext_url("pmc4154587"), 200, &whole_article("x"));
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(
        client
            .urls()
            .iter()
            .any(|url| url == &fulltext_url("pmc4154587")),
        "{:?}",
        client.urls()
    );
    assert_eq!(result.full_text_status, Some(FullTextStatus::Analyzed));
}

// ---------------------------------------------------------------------------
// PubMed
// ---------------------------------------------------------------------------

/// A PubMed `<CoiStatement>` establishes the disclosure and retracts the
/// undeterminable line written before PubMed was consulted.
#[test]
fn a_pubmed_coi_statement_retracts_the_unknown_line() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(None, "123", "N", "")]),
        )
        .route(
            &efetch_url("123"),
            200,
            "<PubmedArticleSet><PubmedArticle><MedlineCitation>\
             <CoiStatement>Dr X is a consultant for Acme.</CoiStatement>\
             <Article/></MedlineCitation></PubmedArticle></PubmedArticleSet>",
        );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(result.coi_disclosed, Some(true));
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_COI_IN_PUBMED));
    assert!(
        !result
            .risk_indicators
            .iter()
            .any(|line| line == INDICATOR_COI_UNKNOWN),
        "the retraction set missed the line it exists for: {:?}",
        result.risk_indicators
    );
    assert_eq!(result.validate(), Ok(()));
}

/// The provenance line is **never retracted**: a truncated full text stays on
/// the record even when PubMed supplies the COI statement.
#[test]
fn the_provenance_line_is_never_retracted() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(Some("PMC123"), "123", "Y", "")]),
        )
        .route(&fulltext_url("PMC123"), 200, "<article><body>x</body>")
        .route(
            &efetch_url("123"),
            200,
            "<PubmedArticleSet><PubmedArticle><MedlineCitation>\
             <CoiStatement>Nothing to declare.</CoiStatement>\
             <Article/></MedlineCitation></PubmedArticle></PubmedArticleSet>",
        );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(result.full_text_status, Some(FullTextStatus::Truncated));
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_COI_IN_PUBMED));
    assert!(
        result
            .risk_indicators
            .iter()
            .any(|line| line.contains("did not arrive whole")),
        "the provenance line was retracted: {:?}",
        result.risk_indicators
    );
}

/// A DOI-only analysis reaches PubMed through the Europe PMC record's own PMID,
/// and a malformed record does not let a `KeyError` escape `analyze()`.
#[test]
fn a_doi_only_analysis_recovers_the_pmid() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("DOI:\"10.1/x\""),
            200,
            &search_body(vec![json!({
                "source": "MED", "id": "123", "inEPMC": "N", "abstractText": "", "pmid": "99887766"
            })]),
        )
        .route(
            &efetch_url("99887766"),
            200,
            "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article><GrantList>\
             <Grant><Agency>Pfizer Inc</Agency></Grant></GrantList></Article>\
             </MedlineCitation></PubmedArticle></PubmedArticleSet>",
        );
    let result = analyzer.analyze(&client, "doc-1", None, Some("10.1/x"));

    assert!(
        client
            .urls()
            .iter()
            .any(|url| url == &efetch_url("99887766")),
        "{:?}",
        client.urls()
    );
    assert!(result.industry_funding_detected);
    assert_eq!(result.validate(), Ok(()));

    // And the hostile shape: a `resultList` that is an array used to raise
    // `AttributeError` out of a public `analyze()` on exactly this path.
    let (analyzer, client) = harness(default_settings());
    let client = client.route(
        &search_url("DOI:\"10.1/x\""),
        200,
        &json!({"resultList": []}).to_string(),
    );
    let result = analyzer.analyze(&client, "doc-1", None, Some("10.1/x"));
    assert_eq!(result.validate(), Ok(()));
}

// ---------------------------------------------------------------------------
// ClinicalTrials.gov
// ---------------------------------------------------------------------------

/// A structured accession plus an answered "no results" is a finding, and the
/// status says which answer it was.
#[test]
fn a_registered_trial_without_posted_results() {
    let (analyzer, client) = harness(default_settings());
    let client = trial_client(client, &["NCT12345678"], &[("NCT12345678", "false")]);
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(result.trial_registered);
    assert!(!result.trial_results_compliant);
    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::NotPosted)
    );
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_NO_POSTED_RESULTS));
    assert!(result.transparency_score >= SCORE_TRIAL_REGISTERED);
}

/// Posted results are the one answer the walk stops on, and the flag agrees with
/// the status.
#[test]
fn a_registered_trial_with_posted_results() {
    let (analyzer, client) = harness(default_settings());
    let client = trial_client(client, &["NCT12345678"], &[("NCT12345678", "true")]);
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(result.trial_registered);
    assert!(result.trial_results_compliant);
    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::Posted)
    );
    assert!(result.transparency_score >= SCORE_TRIAL_REGISTERED + SCORE_RESULTS_POSTED);
    assert_eq!(result.validate(), Ok(()));
}

/// **A partly-answered check is not a finding** (issue #206): the cap drops one
/// accession nobody reached, so the walk has not earned the "no posted results"
/// claim.
#[test]
fn a_partly_answered_trial_check_is_not_a_finding() {
    let (analyzer, client) = harness(default_settings());
    let accessions = ["NCT11111111", "NCT22222222", "NCT33333333", "NCT44444444"];
    let client = trial_client(
        client,
        &accessions,
        &[
            ("NCT11111111", "false"),
            ("NCT22222222", "false"),
            ("NCT33333333", "false"),
        ],
    );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::PartlyAnswered)
    );
    assert!(!result.trial_results_status.expect("status").is_answered());
    assert!(!result.trial_results_compliant);
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_RESULTS_NOT_CHECKABLE));
    assert!(!result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_NO_POSTED_RESULTS));
    assert_eq!(result.validate(), Ok(()));
}

/// A wrong-typed `hasResults` **inverts** the answer if read with truthiness, so
/// the coercer must refuse it: `"no"` is not "results posted".
#[test]
fn a_wrong_typed_has_results_is_not_posted() {
    let (analyzer, client) = harness(default_settings());
    let client = trial_client(client, &["NCT12345678"], &[("NCT12345678", "\"no\"")]);
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(!result.trial_results_compliant);
    assert_ne!(
        result.trial_results_status,
        Some(TrialResultsStatus::Posted)
    );
    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::RequestFailed)
    );
    assert!(result.transparency_score < SCORE_TRIAL_REGISTERED + SCORE_RESULTS_POSTED);
    assert_eq!(result.validate(), Ok(()));
}

/// A registration in another registry establishes registration and makes no
/// claim about posted results either way.
#[test]
fn a_registration_elsewhere_makes_no_claim() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(None, "123", "N", "")]),
        )
        .route(
            &efetch_url("123"),
            200,
            &pubmed_databanks(&[("ISRCTN", &["ISRCTN123"])]),
        );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(result.trial_registered);
    assert_eq!(
        result.trial_results_status,
        Some(TrialResultsStatus::NotCheckable)
    );
    assert!(!result.trial_results_compliant);
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line == INDICATOR_RESULTS_NOT_CHECKABLE));
}

// ---------------------------------------------------------------------------
// OpenAlex
// ---------------------------------------------------------------------------

/// `{"is_oa": "false"}` is a truthy string and must not award the open-access
/// component; a true boolean must.
#[test]
fn a_wrong_typed_is_oa_awards_nothing() {
    let base = |body: &str| {
        let (analyzer, client) = harness(default_settings());
        let client = client
            .route(&search_url("DOI:\"10.1/x\""), 200, &search_body(vec![]))
            .route(
                &crossref_url("10.1/x"),
                200,
                &json!({"message": {}}).to_string(),
            )
            .route(&openalex_url("10.1/x"), 200, body);
        analyzer.analyze(&client, "doc-1", None, Some("10.1/x"))
    };

    let posted = base(&json!({"open_access": {"is_oa": true}}).to_string());
    let inverted = base(&json!({"open_access": {"is_oa": "false"}}).to_string());
    let absent = base(&json!({"open_access": {}}).to_string());

    assert!(posted.transparency_score >= SCORE_OPEN_ACCESS);
    assert!(inverted.transparency_score < SCORE_OPEN_ACCESS);
    assert!(absent.transparency_score < SCORE_OPEN_ACCESS);
}

// ---------------------------------------------------------------------------
// The malformed-body net
// ---------------------------------------------------------------------------

/// **Every JSON shape a remote can send must not escape `analyze()`.** Four
/// truthy non-object shapes plus a wrong-typed field inside an object, at all
/// five endpoints.
#[test]
fn no_malformed_body_escapes_analyze() {
    let hostile_bodies = [
        json!(null).to_string(),
        json!([]).to_string(),
        json!("string").to_string(),
        json!(true).to_string(),
        json!(42).to_string(),
        json!({"message": 5}).to_string(),
        json!({"message": [1]}).to_string(),
        json!({"resultList": 5}).to_string(),
        json!({"open_access": 5}).to_string(),
        json!({"hasResults": "no"}).to_string(),
        json!({"hasResults": {"a": 1}}).to_string(),
    ];
    for body in hostile_bodies {
        // A DOI and a PMID, so every step runs.
        let (analyzer, client) = harness(default_settings());
        let client = client
            .route(&crossref_url("10.1/x"), 200, &body)
            .route(&search_url("DOI:\"10.1/x\""), 200, &body)
            .route(&openalex_url("10.1/x"), 200, &body)
            .route(&ct_url("NCT12345678"), 200, &body)
            .route(&efetch_url("123"), 200, &body);
        let result = analyzer.analyze(&client, "doc-1", Some("123"), Some("10.1/x"));
        assert_eq!(result.validate(), Ok(()), "{body}");
        assert!(
            result.transparency_score <= MAX_TRANSPARENCY_SCORE,
            "{body}: {}",
            result.transparency_score
        );
    }
}

/// A well-formed overall score: every component spent once, capped at 100.
#[test]
fn a_fully_transparent_paper_reaches_the_cap() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &crossref_url("10.1/x"),
            200,
            &json!({"message": {"funder": [{"name": "Wellcome Trust"}]}}).to_string(),
        )
        .route(
            &search_url("DOI:\"10.1/x\""),
            200,
            &search_body(vec![epmc_record(
                Some("PMC123"),
                "123",
                "Y",
                "Registered NCT12345678",
            )]),
        )
        .route(
            &fulltext_url("PMC123"),
            200,
            "<article><body><sec sec-type=\"conflict\"><title>Conflicts</title>None.</sec>\
             Data are in figshare.</body></article>",
        )
        .route(
            &efetch_url("123"),
            200,
            &pubmed_databanks(&[("ClinicalTrials.gov", &["NCT12345678"])]),
        )
        .route(
            &ct_url("NCT12345678"),
            200,
            &json!({"hasResults": true}).to_string(),
        )
        .route(
            &openalex_url("10.1/x"),
            200,
            &json!({"open_access": {"is_oa": true}, "cited_by_count": 3}).to_string(),
        );
    let result = analyzer.analyze(&client, "doc-1", Some("123"), Some("10.1/x"));

    assert_eq!(result.coi_disclosed, Some(true));
    assert!(result.trial_registered && result.trial_results_compliant);
    assert!(result.transparency_score <= MAX_TRANSPARENCY_SCORE);
    assert!(
        result.transparency_score >= 100 - 1,
        "{}",
        result.transparency_score
    );
    assert_eq!(result.risk_level, TransparencyRisk::Low);
    assert_eq!(result.tier_downgrade_applied, 0);
    assert_eq!(result.validate(), Ok(()));
}

/// The provenance partition is asked of every status through the carrier, and a
/// served-and-refused document never stores the *nothing arrived* line.
#[test]
fn a_refusal_does_not_store_the_nothing_arrived_line() {
    let (analyzer, client) = harness(default_settings());
    let client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(Some("PMC123"), "123", "Y", "")]),
        )
        .route(&fulltext_url("PMC123"), 404, "");
    let result = analyzer.analyze(&client, "doc-1", Some("123"), None);

    assert!(!result.full_text_status.expect("status").is_refusal());
    assert!(result
        .risk_indicators
        .iter()
        .any(|line| line.contains("EuropePMC served none")));
}

// ---------------------------------------------------------------------------
// Test helpers that need a scripted result
// ---------------------------------------------------------------------------

/// A PubMed body carrying one `<DataBank>` per repository.
fn pubmed_databanks(databanks: &[(&str, &[&str])]) -> String {
    let mut list = String::new();
    for (name, accessions) in databanks {
        list.push_str("<DataBank><DataBankName>");
        list.push_str(name);
        list.push_str("</DataBankName><AccessionNumberList>");
        for accession in *accessions {
            list.push_str("<AccessionNumber>");
            list.push_str(accession);
            list.push_str("</AccessionNumber>");
        }
        list.push_str("</AccessionNumberList></DataBank>");
    }
    format!(
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article><DataBankList>\
         {list}</DataBankList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )
}

/// Route a PMID whose PubMed record names `accessions` and a ClinicalTrials.gov
/// answer for each. The two routes cannot drift from each other.
fn trial_client(
    client: ScriptedClient,
    accessions: &[&str],
    answers: &[(&str, &str)],
) -> ScriptedClient {
    let mut client = client
        .route(
            &search_url("EXT_ID:123"),
            200,
            &search_body(vec![epmc_record(None, "123", "N", "")]),
        )
        .route(
            &efetch_url("123"),
            200,
            &pubmed_databanks(&[("ClinicalTrials.gov", accessions)]),
        );
    for (nct, has_results) in answers {
        client = client.route(
            &ct_url(nct),
            200,
            &format!("{{\"hasResults\": {has_results}}}"),
        );
    }
    client
}
