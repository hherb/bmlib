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

//! The full-text service — the differential oracle and a scripted tier chain.
//!
//! The chain is driven over a [`HttpClient`] that records every request and
//! serves canned bodies, which is what makes "which URL was asked, in which
//! order" an assertion rather than a hope. `service_cases.json` diffs the
//! module's pure helpers and the JATS HTML rendering against Python.

use bmlib::fulltext::{
    entry_is_free, extract_free_pdf_url, normalise_pmc_id, pick_oa_pdf_url, plural, quote,
    render_jats_html, sanitize_identifier, ContentKind, FullTextCache, FullTextError,
    FullTextRequest, FullTextService, FullTextSourceEntry, JATSAbstractSection, JATSArticle,
    JATSAuthorInfo, JATSBodySection, JATSFigureInfo, JATSFundingAward, JATSFundingSource,
    JATSReferenceInfo, JATSTableInfo, PdfExtractError, PdfExtractor, PdfText, TierFailures,
    TierFault,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::{json, Map, Value};
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/// One scripted answer: a response, or the transport failing.
type Answer = Result<HttpResponse, FetchError>;

/// A transport that answers from a script and records what it was asked.
struct ScriptedClient {
    script: Mutex<VecDeque<Answer>>,
    repeat: Option<Answer>,
    requests: Mutex<Vec<String>>,
}

impl ScriptedClient {
    fn new(script: Vec<Answer>) -> Arc<Self> {
        Arc::new(ScriptedClient {
            script: Mutex::new(script.into()),
            repeat: None,
            requests: Mutex::new(Vec::new()),
        })
    }

    /// Answer every request with the same response.
    fn always(response: HttpResponse) -> Arc<Self> {
        Arc::new(ScriptedClient {
            script: Mutex::new(VecDeque::new()),
            repeat: Some(Ok(response)),
            requests: Mutex::new(Vec::new()),
        })
    }

    /// The URLs requested, in order.
    fn requests(&self) -> Vec<String> {
        self.requests.lock().expect("lock").clone()
    }

    fn call_count(&self) -> usize {
        self.requests.lock().expect("lock").len()
    }
}

impl HttpClient for ScriptedClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.requests.lock().expect("lock").push(url.to_string());
        if let Some(answer) = self.script.lock().expect("lock").pop_front() {
            return answer;
        }
        match &self.repeat {
            Some(answer) => answer.clone(),
            // Exhausting the script is a test error, not a quiet 404: it makes
            // an over-run a recorded transport fault.
            None => Err(FetchError::Transport(format!(
                "scripted client exhausted at {url}"
            ))),
        }
    }
}

/// A temporary directory that cleans up after itself.
struct TempDir(PathBuf);

impl TempDir {
    fn new(label: &str) -> Self {
        let unique = format!(
            "bmlib-service-{label}-{}-{:x}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or_default()
        );
        let path = std::env::temp_dir().join(unique);
        std::fs::create_dir_all(&path).expect("temp dir");
        TempDir(path)
    }

    fn cache(&self) -> FullTextCache {
        let cache =
            FullTextCache::new(Some(self.0.clone())).expect("an explicit directory needs no home");
        std::fs::create_dir_all(cache.html_dir()).expect("html dir");
        std::fs::create_dir_all(cache.pdf_dir()).expect("pdf dir");
        cache
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn ok(body: impl Into<String>) -> Answer {
    Ok(HttpResponse::ok_text(body))
}

/// A `200` carrying **bytes** — the shape a PDF arrives in.
fn ok_bytes(body: impl Into<Vec<u8>>) -> Answer {
    Ok(HttpResponse::ok(body))
}

fn status(code: u16) -> Answer {
    Ok(HttpResponse::from_bytes(code, Vec::new()))
}

fn body(code: u16, body: impl Into<String>) -> Answer {
    Ok(HttpResponse::from_bytes(code, body.into().into_bytes()))
}

fn json_ok(value: Value) -> Answer {
    Ok(HttpResponse::ok_text(value.to_string()))
}

fn transport(message: &str) -> Answer {
    Err(FetchError::Transport(message.to_string()))
}

fn service(client: Arc<ScriptedClient>) -> FullTextService {
    FullTextService::new(client, "test@example.com")
}

fn request() -> FullTextRequest {
    FullTextRequest::default()
}

/// A JATS document with a `<body>`.
const FULL_JATS: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article">
  <front>
    <article-meta>
      <title-group><article-title>An article with a body</article-title></title-group>
      <abstract><p>An abstract paragraph.</p></abstract>
    </article-meta>
  </front>
  <body><sec><title>Introduction</title><p>Body prose here.</p></sec></body>
</article>"#;

/// The body-less shape medRxiv serves for some preprints.
const ABSTRACT_ONLY_JATS: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <title-group><article-title>Why More Doctors May Not Mean More Physicians</article-title></title-group>
      <abstract><p>Only the abstract survives.</p></abstract>
    </article-meta>
  </front>
  <back><sec><title>Data Availability</title><p>On request.</p></sec></back>
</article>"#;

/// A second body-less rendering, distinguishable from [`ABSTRACT_ONLY_JATS`] so
/// the test that replaces one abstract with another can tell them apart.
const SUPERSEDING_ABSTRACT_JATS: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <title-group><article-title>A different article</article-title></title-group>
      <abstract><p>The superseding article has its own abstract.</p></abstract>
    </article-meta>
  </front>
</article>"#;

/// efetch's answer for an article whose publisher does not release XML.
const NCBI_STUB_JATS: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<pmc-articleset>
  <Reply>The publisher of this article does not allow downloading of the full text in XML form.</Reply>
</pmc-articleset>"#;

/// One Europe PMC search hit with an optional id and free PDF URL.
fn search_body(pmcid: Option<&str>, pdf_url: Option<&str>) -> Value {
    let mut hit = Map::new();
    if let Some(pmcid) = pmcid {
        hit.insert("pmcid".to_string(), json!(pmcid));
        hit.insert("inEPMC".to_string(), json!("Y"));
    }
    if let Some(pdf_url) = pdf_url {
        hit.insert(
            "fullTextUrlList".to_string(),
            json!({"fullTextUrl": [{
                "documentStyle": "pdf",
                "availability": "Open access",
                "availabilityCode": "OA",
                "url": pdf_url,
            }]}),
        );
    }
    if hit.is_empty() {
        json!({"resultList": {"result": []}})
    } else {
        json!({"resultList": {"result": [Value::Object(hit)]}})
    }
}

/// The fixtures really are the shapes the tests assume.
#[test]
fn the_jats_fixtures_parse_to_the_expected_body_state() {
    let full = bmlib::fulltext::parse(FULL_JATS).expect("full jats parses");
    assert!(full.has_body);
    let empty = bmlib::fulltext::parse(ABSTRACT_ONLY_JATS).expect("abstract-only parses");
    assert!(!empty.has_body, "the fixture must be body-less");
    assert_eq!(empty.abstract_sections.len(), 1);
    let stub = bmlib::fulltext::parse(NCBI_STUB_JATS).expect("stub parses");
    assert!(!stub.has_body);
    assert!(stub.abstract_sections.is_empty());
}

// ---------------------------------------------------------------------------
// The differential oracle
// ---------------------------------------------------------------------------

const CASES: &str = include_str!("data/service_cases.json");
const EXPECTED: &str = include_str!("data/service_expected.json");

fn string(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn strings(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// Build a [`JATSArticle`] from a case's JSON, with the same defaults the
/// Python dumper's builder uses — which is what makes the two sides the same
/// document rather than two documents that happen to look alike.
fn article_from(spec: &Value) -> JATSArticle {
    let authors = spec
        .get("authors")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|author| JATSAuthorInfo {
                    surname: string(author, "surname"),
                    given_names: string(author, "given_names"),
                    affiliations: strings(author, "affiliations"),
                    collab: string(author, "collab"),
                    string_name: string(author, "string_name"),
                })
                .collect()
        })
        .unwrap_or_default();

    let abstract_sections = spec
        .get("abstract_sections")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|section| JATSAbstractSection {
                    title: string(section, "title"),
                    content: string(section, "content"),
                })
                .collect()
        })
        .unwrap_or_default();

    fn body_from(value: &Value) -> JATSBodySection {
        JATSBodySection {
            title: string(value, "title"),
            paragraphs: strings(value, "paragraphs"),
            subsections: value
                .get("subsections")
                .and_then(Value::as_array)
                .map(|items| items.iter().map(body_from).collect())
                .unwrap_or_default(),
        }
    }
    let body_sections = spec
        .get("body_sections")
        .and_then(Value::as_array)
        .map(|items| items.iter().map(body_from).collect())
        .unwrap_or_default();

    let figures = spec
        .get("figures")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|figure| JATSFigureInfo {
                    id: string(figure, "id"),
                    label: string(figure, "label"),
                    caption: string(figure, "caption"),
                    graphic_url: figure
                        .get("graphic_url")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    footnotes: strings(figure, "footnotes"),
                })
                .collect()
        })
        .unwrap_or_default();

    let tables = spec
        .get("tables")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|table| JATSTableInfo {
                    id: string(table, "id"),
                    label: string(table, "label"),
                    caption: string(table, "caption"),
                    html_content: string(table, "html_content"),
                    graphic_url: table
                        .get("graphic_url")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    footnotes: strings(table, "footnotes"),
                })
                .collect()
        })
        .unwrap_or_default();

    let references = spec
        .get("references")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|reference| JATSReferenceInfo {
                    id: string(reference, "id"),
                    label: string(reference, "label"),
                    citation: string(reference, "citation"),
                    authors: strings(reference, "authors"),
                    article_title: string(reference, "article_title"),
                    source: string(reference, "source"),
                    year: string(reference, "year"),
                    volume: string(reference, "volume"),
                    issue: string(reference, "issue"),
                    first_page: string(reference, "first_page"),
                    last_page: string(reference, "last_page"),
                    doi: string(reference, "doi"),
                    pmid: string(reference, "pmid"),
                    elocation_id: string(reference, "elocation_id"),
                })
                .collect()
        })
        .unwrap_or_default();

    let mut article = JATSArticle::new(
        string(spec, "title"),
        authors,
        string(spec, "journal"),
        string(spec, "volume"),
        string(spec, "issue"),
        string(spec, "pages"),
        string(spec, "year"),
        string(spec, "doi"),
        string(spec, "pmc_id"),
        string(spec, "pmid"),
        abstract_sections,
        body_sections,
        figures,
        tables,
        references,
    );
    article.has_body = spec
        .get("has_body")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    article.suppressed_nested_articles = spec
        .get("suppressed_nested_articles")
        .and_then(Value::as_u64)
        .unwrap_or(0) as usize;
    article.elocation_id = string(spec, "elocation_id");
    article.funding_statements = strings(spec, "funding_statements");
    article.funding_awards = spec
        .get("funding_awards")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|award| JATSFundingAward {
                    sources: award
                        .get("sources")
                        .and_then(Value::as_array)
                        .map(|sources| {
                            sources
                                .iter()
                                .map(|source| JATSFundingSource {
                                    name: string(source, "name"),
                                    identifier: string(source, "identifier"),
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                    award_ids: strings(award, "award_ids"),
                })
                .collect()
        })
        .unwrap_or_default();
    article
}

/// One case's outcome, in the same shape the Python dumper emits.
fn outcome<T: serde::Serialize>(result: Result<T, String>) -> Value {
    match result {
        Ok(value) => json!({"ok": true, "value": value}),
        Err(error) => json!({"ok": false, "error": error}),
    }
}

fn run_case(case: &Value) -> Value {
    let args = &case["args"];
    let object = |key: &str| args[key].as_object().expect("an object argument");
    match case["fn"].as_str().unwrap_or_default() {
        "entry_is_free" => outcome(Ok(entry_is_free(object("entry")))),
        "extract_free_pdf_url" => outcome(Ok(extract_free_pdf_url(object("result")))),
        "pick_oa_pdf_url" => outcome(
            pick_oa_pdf_url(object("data"))
                .map_err(|fault: TierFault| format!("{}: {}", fault.name, fault.message)),
        ),
        "normalise_pmc_id" => outcome(
            normalise_pmc_id(args["value"].as_str().unwrap_or_default())
                .map_err(|error: FullTextError| format!("FullTextError: {error}")),
        ),
        "plural" => outcome(Ok(plural(
            args["n"].as_u64().expect("n") as usize,
            args["noun"].as_str().expect("noun"),
        ))),
        "tier_failures" => {
            let mut failures = TierFailures::unreported();
            for record in args["records"].as_array().expect("records") {
                match record["kind"].as_str().unwrap_or_default() {
                    "fault" => failures.record(&TierFault::fault(
                        match record["name"].as_str().unwrap_or_default() {
                            "OSError" => "OSError",
                            "TypeError" => "TypeError",
                            "AttributeError" => "AttributeError",
                            "NameError" => "NameError",
                            "KeyError" => "KeyError",
                            "IndexError" => "IndexError",
                            "ValueError" => "ValueError",
                            "RuntimeError" => "RuntimeError",
                            other => panic!("unknown fault name {other:?}"),
                        },
                        "boom",
                    )),
                    "unavailable" => failures.record(&TierFault::unavailable("none")),
                    "plain_error" => failures.record(&TierFault::fault("FullTextError", "failed")),
                    "absence" => failures.note_absence(),
                    other => panic!("unknown record kind {other:?}"),
                }
            }
            outcome(Ok(json!({
                "describe": failures.describe(),
                "faults": failures.faults(),
                "absences": failures.absences(),
            })))
        }
        "build_html" => outcome(Ok(render_jats_html(&article_from(&args["article"])))),
        other => panic!("unknown fn {other:?}"),
    }
}

/// The error's class name, before Python's `": message"`.
fn error_name(error: &str) -> &str {
    error.split(':').next().unwrap_or(error)
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");
    assert!(cases.len() >= 60, "the corpus shrank");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = want["name"].as_str().unwrap_or("<unnamed>");
        assert_eq!(
            case["name"], want["name"],
            "cases and expectations are misaligned"
        );
        let got = run_case(case);
        if want["ok"].as_bool().unwrap_or(false) {
            if got["ok"] != want["ok"] || got.get("value") != want.get("value") {
                failures.push(format!(
                    "{name}: got {} want {}",
                    got.get("value").cloned().unwrap_or(Value::Null),
                    want.get("value").cloned().unwrap_or(Value::Null)
                ));
            }
        } else {
            // The message text is Python's; the class name is the contract.
            let want_name = error_name(want["error"].as_str().unwrap_or_default()).to_string();
            let got_ok = got["ok"].as_bool().unwrap_or(false);
            let got_name = got["error"]
                .as_str()
                .map(|error| error_name(error).to_string())
                .unwrap_or_default();
            if got_ok || got_name != want_name {
                failures.push(format!("{name}: got {got:?} want {want_name}"));
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// The oracle is only a net if it actually reaches every branch it names.
#[test]
fn the_corpus_covers_every_function_and_both_outcomes() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("list");
    let expected = expected.as_array().expect("list");

    let mut functions: Vec<&str> = cases
        .iter()
        .filter_map(|case| case["fn"].as_str())
        .collect();
    functions.sort_unstable();
    functions.dedup();
    assert_eq!(
        functions,
        [
            "build_html",
            "entry_is_free",
            "extract_free_pdf_url",
            "normalise_pmc_id",
            "pick_oa_pdf_url",
            "plural",
            "tier_failures",
        ]
    );

    let raising = expected
        .iter()
        .filter(|want| !want["ok"].as_bool().unwrap_or(false))
        .count();
    assert!(
        raising >= 10,
        "the raising shapes are barely covered: {raising}"
    );
}

// ---------------------------------------------------------------------------
// Tier 0 — fetcher-supplied sources
// ---------------------------------------------------------------------------

#[test]
fn a_known_source_xml_wins_and_is_cached() {
    let dir = TempDir::new("known-xml");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![ok(FULL_JATS)]);
    let service = service(client.clone()).with_cache(Some(cache.clone()));

    let mut request = request();
    request.fulltext_sources = vec![FullTextSourceEntry {
        url: "https://medrxiv.org/paper.source.xml".to_string(),
        format: "xml".to_string(),
        source: "medrxiv".to_string(),
        open_access: true,
        version: None,
    }];
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "medrxiv");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert!(result.html.expect("html").contains("Body prose here."));
    assert_eq!(client.call_count(), 1);
    assert!(
        cache.get_html(&sanitize_identifier("10.1/test")).is_some(),
        "a body-carrying JATS render is cached"
    );
}

#[test]
fn known_sources_are_ordered_xml_then_pdf_then_html() {
    let client = ScriptedClient::new(vec![status(404), body(200, "%PDF-1.4 fake")]);
    let service = service(client.clone());
    let sources = vec![
        FullTextSourceEntry {
            url: "https://ex/page.html".to_string(),
            format: "html".to_string(),
            source: "publisher".to_string(),
            open_access: true,
            version: None,
        },
        FullTextSourceEntry {
            url: "https://ex/a.pdf".to_string(),
            format: "pdf".to_string(),
            source: "publisher".to_string(),
            open_access: true,
            version: None,
        },
        FullTextSourceEntry {
            url: "https://ex/a.xml".to_string(),
            format: "xml".to_string(),
            source: "publisher".to_string(),
            open_access: true,
            version: None,
        },
    ];
    let mut request = request();
    request.fulltext_sources = sources;

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "publisher");
    assert_eq!(result.pdf_url.as_deref(), Some("https://ex/a.pdf"));
    // The pdf entry is taken ahead of the html one even though the html entry
    // was given first, and the PDF is not downloaded with no cache to hold it.
    assert_eq!(client.requests(), vec!["https://ex/a.xml"]);
}

#[test]
fn a_body_less_known_source_is_held_back_and_the_pdf_wins() {
    let dir = TempDir::new("bodyless-pdf");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![ok(ABSTRACT_ONLY_JATS), status(404)]);
    let service = service(client.clone()).with_cache(Some(cache));

    let mut request = request();
    request.fulltext_sources = vec![
        FullTextSourceEntry {
            url: "https://medrxiv.org/paper.source.xml".to_string(),
            format: "xml".to_string(),
            source: "medrxiv".to_string(),
            open_access: true,
            version: None,
        },
        FullTextSourceEntry {
            url: "https://medrxiv.org/paper.full.pdf".to_string(),
            format: "pdf".to_string(),
            source: "medrxiv".to_string(),
            open_access: true,
            version: None,
        },
    ];
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(
        result.pdf_url.as_deref(),
        Some("https://medrxiv.org/paper.full.pdf")
    );
    // The PDF's download 404s, so the held-back abstract is paired with the link
    // rather than discarded — and it is never reported as full text.
    assert_eq!(result.content_kind, ContentKind::Abstract);
    assert!(result.html.expect("abstract").contains("Only the abstract"));
    assert_eq!(client.call_count(), 2);
}

#[test]
fn an_html_source_returns_a_web_url_without_fetching_it() {
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone());
    let mut request = request();
    request.fulltext_sources = vec![FullTextSourceEntry {
        url: "https://ex/page".to_string(),
        format: "html".to_string(),
        source: "publisher".to_string(),
        open_access: true,
        version: None,
    }];
    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.web_url.as_deref(), Some("https://ex/page"));
    assert_eq!(
        client.call_count(),
        0,
        "an HTML entry is a link, not a fetch"
    );
}

// ---------------------------------------------------------------------------
// The Europe PMC chain
// ---------------------------------------------------------------------------

#[test]
fn europe_pmc_serves_a_known_pmc_id() {
    let client = ScriptedClient::new(vec![ok(FULL_JATS)]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.pmid = "456".to_string();
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert_eq!(
        client.requests(),
        vec!["https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML"]
    );
}

#[test]
fn the_tier_order_is_epmc_then_ncbi_then_the_pdf_lookup_then_unpaywall_then_doi() {
    let client = ScriptedClient::new(vec![
        status(404),
        status(404),
        json_ok(search_body(None, None)),
        status(404),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "doi");
    assert_eq!(result.web_url.as_deref(), Some("https://doi.org/10.1/test"));

    let requests = client.requests();
    assert_eq!(requests.len(), 4, "{requests:?}");
    assert!(requests[0].contains("/PMC123/fullTextXML"), "{requests:?}");
    assert!(requests[1].contains("efetch.fcgi"), "{requests:?}");
    assert!(
        requests[2].contains("/search?query=DOI:10.1%2Ftest"),
        "{requests:?}"
    );
    assert!(requests[3].contains("api.unpaywall.org"), "{requests:?}");
}

#[test]
fn a_discovered_pmc_id_is_fetched_from_europe_pmc() {
    let client = ScriptedClient::new(vec![
        json_ok(search_body(Some("PMC999"), None)),
        ok(FULL_JATS),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert_eq!(client.call_count(), 2);
}

#[test]
fn the_id_converter_rescues_a_search_that_found_nothing() {
    let client = ScriptedClient::new(vec![
        json_ok(search_body(None, None)),
        json_ok(json!({"status": "ok", "records": [{"pmcid": "PMC999", "live": "true"}]})),
        ok(FULL_JATS),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert!(client.requests()[1].contains("idconv"));
}

#[test]
fn an_unreachable_tier_does_not_abort_the_chain() {
    let client = ScriptedClient::new(vec![
        transport("connection reset"),
        transport("connection reset"),
        json_ok(json!({"best_oa_location": {"url_for_pdf": "https://ex/oa.pdf"}})),
        body(200, "%PDF-1.4 fake"),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());
    request.pmid = "456".to_string();

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "unpaywall");
    assert_eq!(result.pdf_url.as_deref(), Some("https://ex/oa.pdf"));
}

#[test]
fn a_body_less_jats_document_is_held_back_and_never_cached() {
    let dir = TempDir::new("bodyless-epmc");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![
        ok(ABSTRACT_ONLY_JATS),
        status(404),
        json_ok(search_body(None, None)),
        status(404),
    ]);
    let service = service(client.clone()).with_cache(Some(cache.clone()));
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Abstract);
    assert!(result.html.expect("abstract").contains("Only the abstract"));
    assert_eq!(
        result.web_url.as_deref(),
        Some("https://doi.org/10.1/test"),
        "the held-back abstract carries the link it degraded to"
    );
    assert!(
        cache.get_html(&sanitize_identifier("10.1/test")).is_none(),
        "the body-less render must not become the permanent answer"
    );
}

#[test]
fn content_kind_is_abstract_when_only_an_abstract_was_found() {
    let client = ScriptedClient::new(vec![
        ok(ABSTRACT_ONLY_JATS),
        status(404),
        status(404),
        transport("down"),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.content_kind, ContentKind::Abstract);
    assert_ne!(result.content_kind, ContentKind::Fulltext);
    assert!(result.html.is_some());
}

#[test]
fn an_ncbi_stub_does_not_become_the_last_resort_abstract() {
    let client = ScriptedClient::new(vec![
        status(404),
        ok(NCBI_STUB_JATS),
        json_ok(search_body(None, None)),
        status(404),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "doi");
    assert_eq!(
        result.html, None,
        "the stub carries no text to label an abstract"
    );
}

#[test]
fn the_free_pdf_render_url_is_taken_from_the_search() {
    let client = ScriptedClient::new(vec![
        json_ok(search_body(None, Some("https://europepmc.org/x.pdf"))),
        body(200, "%PDF-1.4 fake"),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc_pdf");
    assert_eq!(
        result.pdf_url.as_deref(),
        Some("https://europepmc.org/x.pdf")
    );
}

// ---------------------------------------------------------------------------
// Exhaustion, and the report that explains it
// ---------------------------------------------------------------------------

#[test]
fn an_empty_call_raises_and_asks_nothing() {
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone());
    let error = service.fetch_fulltext(&request()).expect_err("raises");
    assert_eq!(
        error,
        FullTextError::Other("No identifiers provided".to_string())
    );
    assert_eq!(client.call_count(), 0);
}

#[test]
fn a_pmc_only_exhausted_chain_raises_with_the_report() {
    let client = ScriptedClient::new(vec![
        transport("network is down"),
        transport("network is down"),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());

    let error = service.fetch_fulltext(&request).expect_err("raises");
    let message = error.to_string();
    assert!(
        message.contains("no DOI or PMID to fall back on"),
        "{message}"
    );
    assert!(
        message.contains("2 attempts failed (TransportError)"),
        "{message}"
    );
}

#[test]
fn a_chain_where_every_attempt_failed_says_so() {
    let client = ScriptedClient::new(vec![
        transport("down"),
        transport("down"),
        transport("down"),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());
    request.pmid = "456".to_string();

    let result = service
        .fetch_fulltext(&request)
        .expect("a link is still a result");
    assert_eq!(result.source, "doi");
    assert_eq!(result.content_kind, ContentKind::None);
    let warnings = service.warnings();
    assert!(
        warnings
            .iter()
            .any(|line| line.contains("nothing was retrieved; 3 attempts failed (TransportError)")),
        "{warnings:?}"
    );
}

// ---------------------------------------------------------------------------
// The ID Converter's record, read through Python's `bool()` and `str()`
// ---------------------------------------------------------------------------

/// Run a DOI-only chain whose Europe PMC search finds nothing and whose ID
/// Converter answers `record`, then whatever `rest` scripts.
fn id_converter_chain(record: Value, rest: Vec<Answer>) -> (FullTextService, Arc<ScriptedClient>) {
    let mut script = vec![
        json_ok(search_body(None, None)),
        json_ok(json!({"status": "ok", "records": [record]})),
    ];
    script.extend(rest);
    let client = ScriptedClient::new(script);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());
    let _ = service.fetch_fulltext(&request);
    (service, client)
}

/// The failure summary the chain logs when nothing was retrieved.
fn summary(service: &FullTextService) -> String {
    service
        .warnings()
        .into_iter()
        .find(|line| line.contains("nothing was retrieved"))
        .expect("a summary")
}

/// **A record PMC no longer serves is an absence, however `live` is typed.**
/// The flag is read through Python's `str()` and lowercased, so a JSON `false`
/// (`"False"`) and the documented `"false"` agree; dropping the lowercasing
/// would fetch a withdrawn record for the first two.
#[test]
fn an_id_converter_record_that_is_not_live_is_an_absence() {
    for live in [json!(false), json!("False"), json!("false")] {
        let (service, client) =
            id_converter_chain(json!({"pmcid": "PMC999", "live": live}), vec![status(404)]);
        assert!(
            client.requests().iter().all(|url| !url.contains("PMC999")),
            "live={live}: a record that is not live is never fetched: {:?}",
            client.requests()
        );
        let summary = summary(&service);
        assert!(!summary.contains("failed"), "live={live}: {summary}");
    }
}

/// **Anything whose `str()` is not `false` means live** — `0`, `null` and `""`
/// included, since Python's `str()` gives `"0"`, `"None"` and `""` — as does an
/// absent flag, which Python defaults to `"true"`.
#[test]
fn an_id_converter_record_whose_flag_is_not_false_is_live() {
    for live in [
        Some(json!("true")),
        Some(json!(0)),
        Some(Value::Null),
        Some(json!("")),
        Some(json!(["false"])),
        None,
    ] {
        let mut record = json!({"pmcid": "PMC999"});
        if let Some(live) = &live {
            record["live"] = live.clone();
        }
        let (service, client) = id_converter_chain(record, vec![ok(FULL_JATS)]);
        assert!(
            client.requests()[2].contains("PMC999"),
            "live={live:?}: the resolved id is fetched: {:?}",
            client.requests()
        );
        assert!(
            service.warnings().is_empty(),
            "live={live:?}: {:?}",
            service.warnings()
        );
    }
}

/// **A falsy `pmcid` is an absence, a truthy unusable one is a fault** — the
/// line between them is Python's `bool()`, so `""`, `null`, `0` and `[]` mean
/// the converter had nothing, while `5`, `"garbage"` and `["x"]` mean it said
/// something unusable. The WARNING carries the value as Python's `%r` prints
/// it: a string quoted, a list as its `repr`.
#[test]
fn an_id_converter_pmcid_is_absent_when_falsy_and_a_fault_when_unusable() {
    for pmcid in [json!(""), Value::Null, json!(0), json!([])] {
        let (service, _) = id_converter_chain(json!({"pmcid": pmcid}), vec![status(404)]);
        let summary = summary(&service);
        assert!(!summary.contains("failed"), "pmcid={pmcid}: {summary}");
        assert!(
            !service
                .warnings()
                .iter()
                .any(|line| line.contains("unusable PMC ID")),
            "pmcid={pmcid}: {:?}",
            service.warnings()
        );
    }
    for (pmcid, shown) in [
        (json!(5), "5"),
        (json!("garbage"), "'garbage'"),
        (json!(["x"]), "['x']"),
    ] {
        let (service, _) = id_converter_chain(json!({"pmcid": pmcid}), vec![status(404)]);
        assert!(
            service.warnings().contains(&format!(
                "ID Converter returned an unusable PMC ID: {shown}"
            )),
            "pmcid={pmcid}: {:?}",
            service.warnings()
        );
        let summary = summary(&service);
        assert!(
            summary.contains("FullTextError"),
            "pmcid={pmcid}: {summary}"
        );
    }
}

/// **An error record is an absence, and its `errmsg` is logged in Python's
/// spelling** — a list as its `repr`, not its JSON text.
#[test]
fn an_id_converter_error_record_is_an_absence_logged_with_its_errmsg() {
    let (service, client) = id_converter_chain(
        json!({"status": "error", "errmsg": ["invalid id"]}),
        vec![status(404)],
    );
    assert!(
        service.log_lines().iter().any(|line| line
            .message
            .ends_with("ID Converter has no record for 10.1/test: ['invalid id']")),
        "{:?}",
        service.log_lines()
    );
    assert_eq!(client.call_count(), 3, "{:?}", client.requests());
    let summary = summary(&service);
    assert!(!summary.contains("failed"), "{summary}");
}

/// An error record carrying **no** `errmsg` logs `None`, which is Python's `%s`
/// of the absent key, not an empty string.
#[test]
fn an_id_converter_error_record_without_an_errmsg_logs_none() {
    let (service, _) = id_converter_chain(json!({"status": "error"}), vec![status(404)]);
    assert!(
        service.log_lines().iter().any(|line| line
            .message
            .ends_with("ID Converter has no record for 10.1/test: None")),
        "{:?}",
        service.log_lines()
    );
}

#[test]
fn a_chain_that_was_offered_nothing_reports_absences_not_failures() {
    let client = ScriptedClient::new(vec![
        json_ok(search_body(None, None)),
        json_ok(json!({"status": "ok", "records": []})),
        status(404),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());
    request.pmid = "456".to_string();

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "doi");
    let summary = service
        .warnings()
        .into_iter()
        .find(|line| line.contains("nothing was retrieved"))
        .expect("a summary");
    assert!(summary.contains("3 sources had nothing"), "{summary}");
    assert!(!summary.contains("failed"), "{summary}");
}

#[test]
fn a_successful_retrieval_reports_nothing() {
    let client = ScriptedClient::always(HttpResponse::ok(FULL_JATS));
    let service = service(client);
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert!(service.warnings().is_empty());
}

#[test]
fn a_failed_then_recovered_chain_stays_silent() {
    let client = ScriptedClient::new(vec![
        transport("transient"),
        json_ok(json!({"status": "ok", "records": [{"pmcid": "PMC123", "live": "true"}]})),
        ok(FULL_JATS),
    ]);
    let service = service(client);
    let mut request = request();
    request.doi = Some("10.1/test".to_string());
    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert!(service.warnings().is_empty(), "{:?}", service.warnings());
}

// ---------------------------------------------------------------------------
// Caching
// ---------------------------------------------------------------------------

#[test]
fn a_cache_hit_avoids_every_request() {
    let dir = TempDir::new("cache-hit");
    let cache = dir.cache();
    cache
        .save_html("<h1>Cached</h1>", &sanitize_identifier("10.1/test"))
        .expect("cache write");
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone()).with_cache(Some(cache));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.pmc_id = Some("PMC123".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "cached");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert_eq!(client.call_count(), 0);
}

/// A cached PDF's text is re-derived on the hit, so the hit carries what the
/// original retrieval carried rather than a bare file path.
#[test]
fn a_cached_pdf_with_extracted_text_is_a_hit_without_a_request() {
    let dir = TempDir::new("cache-pdf");
    let cache = dir.cache();
    cache
        .save_pdf(b"%PDF-1.4 fake", &sanitize_identifier("10.1/test"))
        .expect("cache write");
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone())
        .with_cache(Some(cache))
        .with_pdf_extractor(Arc::new(FakeExtractor));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "cached");
    assert_eq!(result.content_kind, ContentKind::Extracted);
    assert!(result.html.expect("html").contains("Extracted prose"));
    assert_eq!(client.call_count(), 0);
}

/// **Defect #305, as Python settled it.** A cached PDF whose text cannot be
/// extracted and which has **no** abstract beside it is still a *hit*: the file
/// path is what the caller gets, `content_kind` is unset, and the chain does not
/// re-run. The port used to treat it as a miss and re-fetch, which the
/// maintainer rejected — for a `convert_pdfs = false` caller every PDF hit
/// becomes a network request. The sidecar (tested above) is what recovers the
/// abstract for the case that had one.
#[test]
fn a_text_less_pdf_hit_without_an_abstract_is_still_a_hit() {
    let dir = TempDir::new("cache-pdf-notext");
    let cache = dir.cache();
    cache
        .save_pdf(b"%PDF-1.4 fake", &sanitize_identifier("10.1/test"))
        .expect("cache write");
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone()).with_cache(Some(cache));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "cached");
    assert_eq!(result.content_kind, ContentKind::None);
    assert!(
        result.file_path.is_some(),
        "the cached PDF path is what the caller gets"
    );
    assert_eq!(client.call_count(), 0, "a hit is not a re-fetch");
}

/// **Defect #305's other half.** Once a PDF is cached the retrieval chain never
/// runs again for that identifier, so the abstract the call that cached it held
/// back is written beside it — and a later hit whose PDF yields no text reads it
/// back rather than losing it and re-running the chain.
#[test]
fn a_text_less_pdf_hit_returns_the_cached_abstract() {
    let dir = TempDir::new("cached-abstract");
    let cache = dir.cache();
    let key = sanitize_identifier("10.1/test");
    cache.save_pdf(b"%PDF-1.4 fake", &key).expect("cache write");
    cache
        .save_abstract("<p>The abstract the first call held back</p>", &key)
        .expect("abstract write");

    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone()).with_cache(Some(cache));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "cached");
    assert_eq!(result.content_kind, ContentKind::Abstract);
    assert_eq!(
        result.html.as_deref(),
        Some("<p>The abstract the first call held back</p>")
    );
    assert_eq!(client.call_count(), 0, "the chain must not re-run");
}

/// A PDF hit that **does** yield text keeps the extracted text; the sidecar is
/// consulted only where the PDF yielded none, so an abstract can never displace
/// what the PDF gave.
#[test]
fn a_pdf_hit_with_text_ignores_the_abstract_sidecar() {
    let dir = TempDir::new("pdf-text-wins");
    let cache = dir.cache();
    let key = sanitize_identifier("10.1/test");
    cache.save_pdf(b"%PDF-1.4 fake", &key).expect("cache write");
    cache
        .save_abstract("<p>the held-back abstract</p>", &key)
        .expect("abstract write");

    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone())
        .with_cache(Some(cache))
        .with_pdf_extractor(Arc::new(FakeExtractor));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "cached");
    assert_eq!(result.content_kind, ContentKind::Extracted);
    assert!(
        result
            .html
            .as_deref()
            .is_some_and(|h| h.contains("Extracted prose")),
        "the extracted text must win: {:?}",
        result.html
    );
    assert_eq!(client.call_count(), 0);
}

/// An **undecodable** abstract on a PDF hit heals like the other entries: the
/// whole cache read is abandoned, the entry is moved aside, and the chain runs —
/// Python's `get_abstract` raises and the same guard catches it (#305).
#[test]
fn an_undecodable_abstract_on_a_pdf_hit_is_quarantined_and_the_chain_runs() {
    let dir = TempDir::new("corrupt-abstract");
    let cache = dir.cache();
    let key = sanitize_identifier("10.1/test");
    cache.save_pdf(b"%PDF-1.4 fake", &key).expect("cache write");
    let path = cache.abstract_dir().join(format!("{key}.html"));
    cache
        .save_abstract("<p>x</p>", &key)
        .expect("abstract write");
    std::fs::write(&path, [0xff, 0xfe]).expect("corrupt it");

    let client = ScriptedClient::new(vec![ok(FULL_JATS)]);
    let service = service(client.clone()).with_cache(Some(cache.clone()));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.pmc_id = Some("PMC123".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc", "the cache read was abandoned");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert_eq!(client.call_count(), 1, "the chain ran");
    assert!(
        service
            .warnings()
            .iter()
            .any(|line| line.contains("Could not read the cached full text for")),
        "{:?}",
        service.warnings()
    );
    assert_eq!(
        cache.get_abstract("10.1/test"),
        None,
        "it left the lookup path"
    );
}

/// **Nothing is kept when no PDF was cached.** The sidecar's gate is
/// `result.file_path` — a PDF whose download failed leaves the abstract on the
/// result and nothing on disk, exactly as Python's `_with_abstract_fallback`
/// does.
#[test]
fn no_abstract_sidecar_is_written_when_no_pdf_was_cached() {
    let dir = TempDir::new("no-pdf-sidecar");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![
        ok(ABSTRACT_ONLY_JATS), // Europe PMC, caller's id
        status(404),            // NCBI, caller's id
        json_ok(search_body(None, Some("https://europepmc.org/x.pdf"))),
        status(500), // the PDF download fails
    ]);
    let service = service(client).with_cache(Some(cache.clone()));
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc_pdf");
    assert_eq!(result.content_kind, ContentKind::Abstract);
    assert!(result.html.is_some(), "the abstract is still on the result");
    assert!(
        result.file_path.is_none(),
        "nothing was cached, so there is no path"
    );
    assert_eq!(
        cache.get_abstract(&sanitize_identifier("10.1/test")),
        None,
        "and so nowhere to keep the abstract"
    );
}

/// An abstract **with no PDF beside it** is never a hit on its own: it may belong
/// to a paper a later retrieval still finds the whole article for, which is why
/// `check_cache` consults `abstracts/` only inside the PDF branch.
#[test]
fn an_abstract_without_a_pdf_is_not_a_cache_hit() {
    let dir = TempDir::new("abstract-alone");
    let cache = dir.cache();
    cache
        .save_abstract("<p>held back</p>", &sanitize_identifier("10.1/test"))
        .expect("abstract write");
    let client = ScriptedClient::new(vec![
        json_ok(search_body(None, None)),
        json_ok(json!({"status": "ok", "records": []})),
        status(404),
    ]);
    let service = service(client.clone()).with_cache(Some(cache));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "doi");
    assert!(client.call_count() > 0, "the chain ran past the sidecar");
}

/// The sidecar is written **whenever a PDF is cached with an abstract held
/// back, whether or not the PDF yielded text this time** — a later call may have
/// a different `convert_pdfs` or a different PDF backend.
#[test]
fn the_abstract_is_written_beside_a_pdf_even_when_the_pdf_yields_text() {
    let dir = TempDir::new("abstract-sidecar");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![
        ok(ABSTRACT_ONLY_JATS), // Europe PMC, caller's id
        status(404),            // NCBI, caller's id
        json_ok(search_body(None, Some("https://europepmc.org/x.pdf"))),
        body(200, "%PDF-1.4 fake"),
    ]);
    let service = service(client)
        .with_cache(Some(cache.clone()))
        .with_pdf_extractor(Arc::new(FakeExtractor));
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());
    request.identifier = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc_pdf");
    assert_eq!(
        result.content_kind,
        ContentKind::Extracted,
        "the PDF yielded text, so the abstract is not merged in"
    );
    assert!(
        cache
            .get_abstract(&sanitize_identifier("10.1/test"))
            .is_some_and(|html| html.contains("Only the abstract survives")),
        "the held-back abstract is cached beside the PDF regardless"
    );
}

/// A failed sidecar write has its **own** one-shot key: the PDF *was* cached, so
/// "nothing is being cached" would be false, and spending the cache-write key
/// here would silence a later directory-wide fault of the same type.
#[test]
fn a_failed_abstract_write_warns_once_with_its_own_key() {
    let dir = TempDir::new("abstract-write-fail");
    let cache = dir.cache();
    // A **file** where `abstracts/` must be makes the directory uncreatable while
    // `pdfs/` keeps working — the read-only-older-cache shape the Python names.
    std::fs::write(cache.abstract_dir(), b"not a directory").expect("block abstracts/");

    let answer = || {
        vec![
            ok(ABSTRACT_ONLY_JATS),
            status(404),
            json_ok(search_body(None, Some("https://europepmc.org/x.pdf"))),
            body(200, "%PDF-1.4 fake"),
        ]
    };
    let mut script = answer();
    script.extend(answer());
    let client = ScriptedClient::new(script);
    let service = service(client).with_cache(Some(cache));
    for identifier in ["10.1/one", "10.1/two"] {
        let mut request = request();
        request.pmc_id = Some("PMC123".to_string());
        request.doi = Some("10.1/test".to_string());
        request.identifier = Some(identifier.to_string());
        let result = service.fetch_fulltext(&request).expect("retrieved");
        assert_eq!(result.source, "europepmc_pdf");
    }

    let warnings = service.warnings();
    assert_eq!(
        warnings
            .iter()
            .filter(|line| line.contains("Could not cache the abstract beside a cached PDF"))
            .count(),
        1,
        "once per cause, not per article: {warnings:?}"
    );
    assert!(
        !warnings
            .iter()
            .any(|line| line.contains("nothing is being cached")),
        "the cache-write warning would be false and would spend its own key: {warnings:?}"
    );
}

#[test]
fn an_unreadable_cache_entry_falls_through_to_the_network_and_is_quarantined() {
    let dir = TempDir::new("corrupt-cache");
    let cache = dir.cache();
    let name = sanitize_identifier("10.1/test");
    let path = cache.html_dir().join(format!("{name}.html"));
    std::fs::write(&path, [0x3c, 0xff, 0xfe, 0x3e]).expect("write bad html");

    let client = ScriptedClient::new(vec![ok(FULL_JATS)]);
    let service = service(client.clone()).with_cache(Some(cache.clone()));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.pmc_id = Some("PMC123".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert!(
        service
            .warnings()
            .iter()
            .any(|line| line.contains("Could not read the cached full text for")),
        "{:?}",
        service.warnings()
    );
    assert!(
        cache
            .html_dir()
            .join(format!("{name}.html.corrupt"))
            .exists(),
        "the bytes are kept beside the cache, not deleted"
    );
}

/// **Defect #309's correction at the service level.** A PDF entry that is a
/// directory rather than a file used to be a cache hit for ever — the conversion
/// failure was swallowed, `content_kind` stayed `none`, and every later run
/// repeated it. `get_pdf` now refuses it and the service quarantines it, so the
/// entry leaves the lookup path and the chain re-fetches.
#[test]
fn a_pdf_entry_that_is_a_directory_falls_through_to_the_network_and_is_quarantined() {
    let dir = TempDir::new("corrupt-pdf-cache");
    let cache = dir.cache();
    let name = sanitize_identifier("10.1/test");
    std::fs::create_dir_all(cache.pdf_dir()).expect("pdf dir");
    let path = cache.pdf_dir().join(format!("{name}.pdf"));
    std::fs::create_dir(&path).expect("mkdir at the pdf entry");

    let client = ScriptedClient::new(vec![ok(FULL_JATS)]);
    let service = service(client.clone()).with_cache(Some(cache.clone()));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.pmc_id = Some("PMC123".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    assert!(
        service
            .warnings()
            .iter()
            .any(|line| line.contains("Could not read the cached PDF for")),
        "{:?}",
        service.warnings()
    );
    assert!(
        cache.pdf_dir().join(format!("{name}.pdf.corrupt")).exists(),
        "the unreadable entry left the lookup path"
    );
}

#[test]
fn no_identifier_means_no_cache_lookup_and_no_download() {
    let dir = TempDir::new("no-identifier");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![
        json_ok(search_body(None, Some("https://europepmc.org/x.pdf"))),
        json_ok(json!({"status": "ok", "records": []})),
    ]);
    let service = service(client.clone()).with_cache(Some(cache));
    let mut request = request();
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc_pdf");
    // The render URL is looked up (the search, then the converter that the
    // search's missing id sends it to); the PDF itself is not downloaded
    // without an identifier to key the cache by.
    assert_eq!(client.call_count(), 2);
    assert!(result.file_path.is_none());
}

// ---------------------------------------------------------------------------
// Defect #304 and the PMC-ID identity rules
// ---------------------------------------------------------------------------

/// **Defect #304's correction.** Tier 1b was gated on `pmc_id` being *empty*
/// rather than *usable*, so a malformed caller id suppressed the
/// DOI-discovered PMC fetch — supplying an id returned strictly less than
/// omitting it.
#[test]
fn a_malformed_caller_pmc_id_does_not_suppress_the_discovered_fetch() {
    let client = ScriptedClient::new(vec![
        json_ok(search_body(Some("PMC1"), None)),
        ok(FULL_JATS),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMCabc".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    let requests = client.requests();
    assert_eq!(requests.len(), 2, "{requests:?}");
    assert!(
        requests[0].contains("/search?query=DOI:10.1%2Ftest"),
        "{requests:?}"
    );
    assert!(requests[1].contains("/PMC1/fullTextXML"), "{requests:?}");
}

/// **Defect #304's stale-ID half, which the port used to take the other side
/// of.** A well-formed caller PMC ID that gives no full text at either source is
/// **superseded by the Europe PMC search hit's ID** — the recovery step this
/// replaced already trusted that search hit as the article, taking its free PDF
/// whenever it offered one, so fetching the same hit's XML makes no new identity
/// claim. The ID Converter is **not** asked: it was never trusted over a
/// well-formed caller ID.
#[test]
fn a_well_formed_caller_pmc_id_is_superseded_by_the_search_hit() {
    let client = ScriptedClient::new(vec![
        status(404),                                // Europe PMC, caller's id
        status(404),                                // NCBI, caller's id
        json_ok(search_body(Some("PMC777"), None)), // the search hit
        ok(FULL_JATS),                              // Europe PMC, discovered id
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC999".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    let requests = client.requests();
    assert_eq!(requests.len(), 4, "{requests:?}");
    assert!(requests[0].contains("/PMC999/fullTextXML"), "{requests:?}");
    assert!(requests[1].contains("efetch.fcgi"), "{requests:?}");
    assert!(
        requests[2].contains("/search?query=DOI:10.1%2Ftest"),
        "the search runs even though the caller gave a well-formed id: {requests:?}"
    );
    assert!(requests[3].contains("/PMC777/fullTextXML"), "{requests:?}");
    assert!(
        !requests.iter().any(|url| url.contains("idconv")),
        "a well-formed caller id keeps the converter out: {requests:?}"
    );
    assert!(
        service.log_lines().iter().any(|line| line
            .message
            .contains("PMC999 gave no full text and is superseded by PMC777")),
        "the supersession is named: {:?}",
        service.log_lines()
    );
}

/// A **stale caller ID still finds the free PDF**: the search runs once anyway,
/// and the `fullTextUrlList` hit it returns is used exactly as the recovery block
/// this replaced used it — what that block was *for* survives its replacement.
#[test]
fn a_stale_caller_id_still_finds_the_free_pdf() {
    let client = ScriptedClient::new(vec![
        status(404), // Europe PMC, caller's PMC999
        status(404), // NCBI, caller's PMC999
        json_ok(search_body(None, Some("https://europepmc.org/x.pdf"))),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC999".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc_pdf");
    assert_eq!(
        result.pdf_url.as_deref(),
        Some("https://europepmc.org/x.pdf")
    );
    let requests = client.requests();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert!(
        requests[2].contains("/search?query=DOI:10.1%2Ftest"),
        "the search ran exactly once: {requests:?}"
    );
}

/// A superseded ID's own abstract **gives way** to the superseding one's: the
/// search hit the free PDF comes from is the article, so the two are paired.
#[test]
fn a_superseding_ids_abstract_replaces_the_caller_ids() {
    let client = ScriptedClient::new(vec![
        ok(ABSTRACT_ONLY_JATS),                     // Europe PMC, caller's id
        status(404),                                // NCBI, caller's id
        json_ok(search_body(Some("PMC777"), None)), // the search hit
        ok(SUPERSEDING_ABSTRACT_JATS),              // Europe PMC, discovered id
        status(404),                                // NCBI, discovered id
        status(404),                                // Unpaywall
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC999".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.content_kind, ContentKind::Abstract);
    let html = result.html.expect("an abstract");
    assert!(
        html.contains("The superseding article"),
        "the discovered id's abstract must replace the caller's: {html}"
    );
    assert!(!html.contains("Only the abstract survives"), "{html}");
}

/// An **earlier tier's** abstract keeps first-wins, even when a later discovered
/// ID also brings one — the replace rule is for the superseded ID's own abstract
/// and nothing else.
#[test]
fn an_earlier_tiers_abstract_keeps_first_wins() {
    let client = ScriptedClient::new(vec![
        // Tier 0: a body-less known source, held back.
        ok(ABSTRACT_ONLY_JATS),
        // The search then discovers an id whose own rendering is body-less too.
        json_ok(search_body(Some("PMC777"), None)),
        ok(SUPERSEDING_ABSTRACT_JATS),
        status(404),
        status(404),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.fulltext_sources = vec![FullTextSourceEntry {
        url: "https://example.org/paper.xml".to_string(),
        format: "xml".to_string(),
        source: "medrxiv".to_string(),
        open_access: true,
        version: None,
    }];
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    let html = result.html.expect("an abstract");
    assert!(
        html.contains("Only the abstract survives"),
        "Tier 0's abstract came first and must keep: {html}"
    );
}

/// An **unusable `pmcid` in the search response** is validated where it is read:
/// a WARNING, a recorded fault, and `None` handed on so the converter is asked
/// instead. Returned raw, it reached both fetch helpers and cost two faults
/// while the converter was never asked (PR #355's review).
#[test]
fn an_unusable_search_pmcid_is_a_warning_and_the_converter_is_asked() {
    let hit = json!({
        "resultList": {"result": [{"inEPMC": "Y", "pmcid": "abc"}]}
    });
    let client = ScriptedClient::new(vec![
        json_ok(hit),
        json_ok(json!({"status": "ok", "records": [{"pmcid": "PMC1", "live": "true"}]})),
        ok(FULL_JATS),
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "europepmc");
    let requests = client.requests();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert!(
        requests.iter().any(|url| url.contains("idconv")),
        "the converter is asked for the id the search could not supply: {requests:?}"
    );
    assert!(
        service
            .warnings()
            .iter()
            .any(|line| line.contains("Europe PMC search returned an unusable PMC ID: 'abc'")),
        "{:?}",
        service.warnings()
    );
}

/// A search hit's **falsy** `pmcid` is not a fault at all: Python's `if found:`
/// guard reads `""`, `null`, `0`, `[]` and `{}` as "the search found no id", so
/// the converter is asked and nothing is warned. A guard on `is_some()` instead
/// of Python's truthiness would turn every one of them into a fault.
#[test]
fn a_falsy_search_pmcid_is_not_a_fault() {
    for pmcid in [json!(""), Value::Null, json!(0), json!([]), json!({})] {
        let hit = json!({
            "resultList": {"result": [{"inEPMC": "Y", "pmcid": pmcid}]}
        });
        let client = ScriptedClient::new(vec![
            json_ok(hit),
            json_ok(json!({"status": "ok", "records": [{"pmcid": "PMC1", "live": "true"}]})),
            ok(FULL_JATS),
        ]);
        let service = service(client);
        let mut request = request();
        request.doi = Some("10.1/test".to_string());

        let result = service.fetch_fulltext(&request).expect("retrieved");
        assert_eq!(result.source, "europepmc", "pmcid={pmcid}");
        assert!(
            !service
                .warnings()
                .iter()
                .any(|line| line.contains("unusable PMC ID")),
            "pmcid={pmcid}: a falsy value is not a fault: {:?}",
            service.warnings()
        );
    }
}

/// A search hit's `pmcid` of the **wrong JSON type** is refused the same way —
/// it is the reason Python's `_normalise_pmc_id` takes `object`: the comparison
/// that decides a supersession ran outside every tier's `except`, so
/// `{"pmcid": 12345}` escaped as `AttributeError` there. Rust cannot raise it,
/// but the value must still be a recorded fault and `None`, not a fetch.
#[test]
fn a_non_string_search_pmcid_is_a_fault_and_not_a_fetch() {
    for pmcid in [
        json!(12345),
        json!(true),
        json!({"id": "PMC1"}),
        json!(["PMC1"]),
    ] {
        let hit = json!({
            "resultList": {"result": [{"inEPMC": "Y", "pmcid": pmcid}]}
        });
        let client = ScriptedClient::new(vec![
            json_ok(hit),
            json_ok(json!({"status": "ok", "records": [{"pmcid": "PMC1", "live": "true"}]})),
            ok(FULL_JATS),
        ]);
        let service = service(client.clone());
        let mut request = request();
        request.doi = Some("10.1/test".to_string());

        let result = service.fetch_fulltext(&request).expect("retrieved");
        assert_eq!(result.source, "europepmc", "pmcid={pmcid}");
        let requests = client.requests();
        assert_eq!(requests.len(), 3, "pmcid={pmcid}: {requests:?}");
        assert!(
            requests[1].contains("idconv"),
            "pmcid={pmcid}: the converter must be asked: {requests:?}"
        );
        assert!(
            service
                .warnings()
                .iter()
                .any(|line| line.contains("Europe PMC search returned an unusable PMC ID")),
            "pmcid={pmcid}: {:?}",
            service.warnings()
        );
    }
}

/// A search hit's **bare numeric** id is normalised, so it compares equal to the
/// caller's prefixed one and does not trigger a supersede fetch of the same
/// article.
#[test]
fn a_search_hits_bare_id_compares_equal_to_the_callers_prefixed_one() {
    let client = ScriptedClient::new(vec![
        status(404), // Europe PMC, caller's PMC123
        status(404), // NCBI, caller's PMC123
        json_ok(json!({"resultList": {"result": [{"inEPMC": "Y", "pmcid": "123"}]}})),
        status(404), // Unpaywall
    ]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "doi");
    let requests = client.requests();
    let epmc_fetches = requests
        .iter()
        .filter(|url| url.contains("/fullTextXML"))
        .count();
    assert_eq!(
        epmc_fetches, 1,
        "the same article must not be fetched twice: {requests:?}"
    );
    assert!(
        !service
            .log_lines()
            .iter()
            .any(|line| line.message.contains("is superseded by")),
        "'123' and 'PMC123' are one id: {:?}",
        service.log_lines()
    );
}

/// A **malformed** caller PMC ID makes no identity claim at all, so discovery
/// runs in full — **converter included** — exactly as with no ID, and the
/// refusal is recorded on the exhaustion report rather than swallowed.
#[test]
fn a_malformed_caller_pmc_id_is_recorded_and_asks_the_converter() {
    let client = ScriptedClient::new(vec![
        json_ok(search_body(None, None)), // search
        json_ok(json!({"status": "ok", "records": [{"pmcid": "PMC1", "live": "true"}]})), // idconv
        ok(FULL_JATS),                    // PMC1
    ]);
    let result = {
        let service = service(client.clone());
        let mut request = request();
        request.pmc_id = Some("PMCabc".to_string());
        request.doi = Some("10.1/test".to_string());
        service.fetch_fulltext(&request).expect("retrieved")
    };
    assert_eq!(result.source, "europepmc");
    let requests = client.requests();
    assert_eq!(requests.len(), 3, "{requests:?}");
    assert!(
        requests.iter().any(|url| url.contains("idconv")),
        "a malformed id is treated as absent, so the converter may run: {requests:?}"
    );

    // With no DOI or PMID to discover from, the recorded fault is the whole of
    // what the chain did, and it is named in the report.
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMCabc".to_string());
    let error = service.fetch_fulltext(&request).expect_err("exhausted");
    assert!(
        error
            .to_string()
            .contains("1 attempt failed (FullTextError)"),
        "{error}"
    );
    assert_eq!(client.call_count(), 0, "no request may carry the bad id");
}

#[test]
fn a_malformed_pmc_id_never_reaches_a_url() {
    let client = ScriptedClient::new(vec![]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("../../etc/passwd".to_string());

    let error = service.fetch_fulltext(&request).expect_err("exhausted");
    assert!(error.to_string().contains("no DOI or PMID to fall back on"));
    assert_eq!(client.call_count(), 0, "no request may carry the bad id");
}

#[test]
fn the_ncbi_requests_carry_the_identification_and_the_key() {
    let client = ScriptedClient::new(vec![status(404), status(404)]);
    let service = service(client.clone()).with_ncbi_api_key(Some("secret".to_string()));
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());

    let _ = service.fetch_fulltext(&request);
    let requests = client.requests();
    let efetch = &requests[1];
    assert!(efetch.contains("efetch.fcgi"), "{efetch}");
    assert!(efetch.contains("db=pmc"), "{efetch}");
    assert!(efetch.contains("id=123"), "{efetch}");
    assert!(efetch.contains("retmode=xml"), "{efetch}");
    assert!(efetch.contains("tool=bmlib"), "{efetch}");
    assert!(efetch.contains("email=test%40example.com"), "{efetch}");
    assert!(efetch.contains("api_key=secret"), "{efetch}");
}

// ---------------------------------------------------------------------------
// Warnings and the once-per-service rule
// ---------------------------------------------------------------------------

#[test]
fn a_warning_is_suppressed_only_for_its_own_key() {
    let client = ScriptedClient::new(vec![]);
    let service = service(client);
    service.warn_once("k", "something went wrong");
    service.warn_once("k", "something went wrong");
    service.warn_once("other", "a second fault");
    let warnings = service.warnings();
    assert_eq!(
        warnings
            .iter()
            .filter(|line| line.contains("something went wrong"))
            .count(),
        1
    );
    assert!(warnings.iter().any(|line| line.contains("a second fault")));
}

#[test]
fn two_services_do_not_share_suppression() {
    let one = service(ScriptedClient::new(vec![]));
    let two = service(ScriptedClient::new(vec![]));
    one.warn_once("k", "the fault");
    two.warn_once("k", "the fault");
    assert_eq!(one.warnings().len(), 1);
    assert_eq!(two.warnings().len(), 1);
}

#[test]
fn a_defect_shaped_failure_is_reported_even_though_a_later_tier_succeeds() {
    let client = ScriptedClient::new(vec![
        // A body whose `resultList` is not an object is the AttributeError shape
        // the Python raises through `data.get("resultList", {}).get(...)`.
        json_ok(json!({"resultList": 7})),
        // The converter the missing id sends the search to.
        json_ok(json!({"status": "ok", "records": []})),
        json_ok(json!({"best_oa_location": {"url_for_pdf": "https://ex/a.pdf"}})),
        body(200, "%PDF-1.4 fake"),
    ]);
    let service = service(client);
    let mut request = request();
    request.doi = Some("10.1/test".to_string());

    let result = service
        .fetch_fulltext(&request)
        .expect("a later tier still succeeds");
    assert_eq!(result.source, "unpaywall");
    let warnings = service.warnings();
    assert!(
        warnings
            .iter()
            .any(|line| line.contains("AttributeError") && line.contains("defect")),
        "{warnings:?}"
    );
}

// ---------------------------------------------------------------------------
// Small units
// ---------------------------------------------------------------------------

#[test]
fn text_extraction_is_best_effort_and_never_costs_the_pdf() {
    struct Failing;
    impl PdfExtractor for Failing {
        fn extract(&self, _path: &Path) -> Result<PdfText, PdfExtractError> {
            Err(PdfExtractError::Conversion("backend exploded".to_string()))
        }
    }

    let dir = TempDir::new("extract-fail");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![body(200, "%PDF-1.4 fake")]);
    let service = service(client)
        .with_cache(Some(cache))
        .with_pdf_extractor(Arc::new(Failing));
    let mut request = request();
    request.identifier = Some("10.1/test".to_string());
    request.fulltext_sources = vec![FullTextSourceEntry {
        url: "https://ex/a.pdf".to_string(),
        format: "pdf".to_string(),
        source: "repo".to_string(),
        open_access: true,
        version: None,
    }];

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert!(result.file_path.is_some(), "the cached PDF survives");
    assert_eq!(result.pdf_url.as_deref(), Some("https://ex/a.pdf"));
    assert_eq!(result.content_kind, ContentKind::None);
    assert!(service
        .warnings()
        .iter()
        .any(|line| line.contains("PDF text extraction failed")));
}

/// **A binary PDF reaches the cache byte for byte** — the end-to-end form of
/// issue #316.
///
/// The cache validates the `%PDF` prefix and nothing else, so a lossy decode on
/// the download path wrote a file whose every non-UTF-8 byte had become U+FFFD
/// and whose prefix still matched: a silently corrupt cache entry nothing could
/// detect. The served body here carries every byte value, and the assertion is
/// on the file on disk.
#[test]
fn a_binary_pdf_reaches_the_cache_byte_for_byte() {
    let dir = TempDir::new("pdf-binary");
    let cache = dir.cache();
    let mut pdf = b"%PDF-1.4\n".to_vec();
    pdf.extend(0..=255u8);

    let client = ScriptedClient::new(vec![ok_bytes(pdf.clone())]);
    let service = service(client).with_cache(Some(cache.clone()));
    let mut request = request();
    request.identifier = Some("10.1/binary".to_string());
    request.fulltext_sources = vec![FullTextSourceEntry {
        url: "https://ex/binary.pdf".to_string(),
        format: "pdf".to_string(),
        source: "repo".to_string(),
        open_access: true,
        version: None,
    }];

    let result = service.fetch_fulltext(&request).expect("retrieved");
    let path = result.file_path.expect("the PDF was cached");
    let on_disk = std::fs::read(&path).expect("read back");
    assert_eq!(
        on_disk, pdf,
        "the cache must hold the bytes served, not a decoded substitute"
    );
    assert_eq!(
        cache.get_pdf(&sanitize_identifier("10.1/binary")),
        Some(PathBuf::from(&path))
    );
}

/// The HTML the service caches is the rendering, and the URL-building rules are
/// the ones a downstream reads it with.
#[test]
fn the_rendered_html_carries_the_article_and_not_an_invented_number() {
    let article = article_from(&json!({
        "title": "A title",
        "authors": [{"surname": "Smith", "given_names": "J", "affiliations": [],
                     "collab": "", "string_name": ""}],
        "journal": "J Test", "volume": "1", "issue": "2", "pages": "3-4", "year": "2025",
        "abstract_sections": [{"title": "Background", "content": "An abstract."}],
        "body_sections": [{"title": "Methods", "paragraphs": ["A paragraph."], "subsections": []}],
        "figures": [{"id": "", "label": "", "caption": "", "graphic_url": "g", "footnotes": []}],
        "has_body": true,
    }));
    let html = render_jats_html(&article);
    assert!(html.starts_with("<h1>A title</h1>"), "{html}");
    assert!(html.contains("<h2>Abstract</h2>"), "{html}");
    assert!(html.contains("<h2>Methods</h2>"), "{html}");
    assert!(html.contains("<p>A paragraph.</p>"), "{html}");
    assert!(
        !html.contains("Figure 1"),
        "no exhibit number is invented: {html}"
    );
    assert!(html.contains("src=\"g\""), "{html}");
}

#[test]
fn quote_leaves_the_unreserved_set_and_encodes_the_rest() {
    assert_eq!(quote("10.1/test", ""), "10.1%2Ftest");
    assert_eq!(quote("a:b c", ":"), "a:b%20c");
    assert_eq!(quote("test@example.com", ""), "test%40example.com");
    assert_eq!(quote("Az0_.-~", ""), "Az0_.-~");
    assert_eq!(quote("\u{e9}", ""), "%C3%A9");
}

#[test]
fn ncbi_full_text_beats_the_free_pdf_beneath_it() {
    let client = ScriptedClient::new(vec![status(404), ok(FULL_JATS)]);
    let service = service(client.clone());
    let mut request = request();
    request.pmc_id = Some("PMC123".to_string());
    request.doi = Some("10.1/test".to_string());

    let result = service.fetch_fulltext(&request).expect("retrieved");
    assert_eq!(result.source, "ncbi_pmc");
    assert_eq!(result.content_kind, ContentKind::Fulltext);
    // The PDF-recovery search was never reached: NCBI answered first.
    assert_eq!(client.call_count(), 2);
}

// ---------------------------------------------------------------------------
// The download-failure keyspace
// ---------------------------------------------------------------------------

/// One Tier 0 PDF entry, so the download's `origin` is `known_source` while
/// `result.source` is whatever the fetcher named.
fn pdf_source(name: &str) -> FullTextSourceEntry {
    FullTextSourceEntry {
        url: format!("https://ex/{name}.pdf"),
        format: "pdf".to_string(),
        source: name.to_string(),
        open_access: true,
        version: None,
    }
}

/// The failure key is bounded by `(origin, cause)`, never by `result.source`:
/// Tier 0's source is remote-data-derived (OpenAlex builds it from a venue
/// display name), so keying on it would warn once per article while claiming to
/// be one-shot. The venue still appears in the message, so the first report
/// loses no detail.
#[test]
fn two_venues_failing_the_same_way_are_reported_once() {
    let dir = TempDir::new("keyspace");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![status(404), status(404)]);
    let service = service(client.clone()).with_cache(Some(cache));

    let mut first = request();
    first.fulltext_sources = vec![pdf_source("The Lancet")];
    first.identifier = Some("10.1/a".to_string());
    let mut second = request();
    second.fulltext_sources = vec![pdf_source("Zenodo")];
    second.identifier = Some("10.1/b".to_string());

    let one = service.fetch_fulltext(&first).expect("retrieved");
    let two = service.fetch_fulltext(&second).expect("retrieved");
    assert_eq!(one.pdf_url.as_deref(), Some("https://ex/The Lancet.pdf"));
    assert_eq!(two.pdf_url.as_deref(), Some("https://ex/Zenodo.pdf"));

    let warnings = service.warnings();
    assert_eq!(
        warnings
            .iter()
            .filter(|line| line.contains("Could not download"))
            .count(),
        1,
        "{warnings:?}"
    );
    assert!(warnings.iter().any(|line| line.contains("The Lancet")));
}

/// The other half of a one-shot: suppression must not become silence, so a
/// second *different* cause from the same tier is still reported.
#[test]
fn two_different_causes_from_one_tier_are_both_reported() {
    let dir = TempDir::new("keyspace-cause");
    let cache = dir.cache();
    let client = ScriptedClient::new(vec![
        status(404),
        body(200, "<!DOCTYPE html><html>not a pdf</html>"),
    ]);
    let service = service(client.clone()).with_cache(Some(cache));

    let mut first = request();
    first.fulltext_sources = vec![pdf_source("repo")];
    first.identifier = Some("10.1/a".to_string());
    let mut second = request();
    second.fulltext_sources = vec![pdf_source("repo")];
    second.identifier = Some("10.1/b".to_string());

    service.fetch_fulltext(&first).expect("retrieved");
    service.fetch_fulltext(&second).expect("retrieved");

    let warnings = service.warnings();
    assert!(
        warnings.iter().any(|line| line.contains("HTTP 404")),
        "{warnings:?}"
    );
    assert!(
        warnings.iter().any(|line| line.contains("not a PDF")),
        "{warnings:?}"
    );
}

/// An extractor that always succeeds, for the cache-hit path.
struct FakeExtractor;

impl PdfExtractor for FakeExtractor {
    fn extract(&self, _pdf_path: &Path) -> Result<PdfText, PdfExtractError> {
        let text = "Extracted prose";
        Ok(PdfText {
            html: format!("<p>{text}</p>"),
            success: true,
            error_message: None,
            page_count: 1,
            converted_pages: 1,
            char_count: text.len(),
            warnings: Vec::new(),
        })
    }
}

/// **The HTML reference list reproduces two open Python defects**: a fourth
/// author's doubled period (#385) and the empty `<li>` where a citation no field
/// reads renders nothing (#393). Both reach the HTML `FullTextService` caches,
/// and the service corpus's `build_html/ref_four_authors_double_the_period` and
/// `build_html/ref_no_field_reads_renders_an_empty_li` cases pin them against
/// the live library.
#[test]
fn the_html_reference_list_reproduces_the_open_defects() {
    let article = article_from(&json!({
        "title": "Refs",
        "has_body": true,
        "references": [
            {
                "id": "r1", "label": "", "citation": "",
                "authors": ["M Ahamed", "M Karns", "M Goodson", "J Rowe"],
                "article_title": "DNA damage response",
                "source": "Toxicol Appl Pharmacol", "year": "2008"
            },
            {"id": "r2", "label": "", "citation": ""}
        ]
    }));
    let html = render_jats_html(&article);
    assert!(
        html.contains(
            "<li id=\"ref-r1\">M Ahamed, M Karns, et al.. DNA damage response. \
             <em>Toxicol Appl Pharmacol</em>. (2008)</li>"
        ),
        "{html}"
    );
    assert!(html.contains("<li id=\"ref-r2\"></li>"), "{html}");
}

/// **The HTML reference list shows two more open defects**: a `<mixed-citation>`
/// whose deposit glues a name's parts (#314) and the empty `<li>` a `<ref>` of
/// only a `<note>` renders (#150). Both reach the HTML `FullTextService` caches;
/// the service corpus's `build_html/ref_a_glued_mixed_citation` and
/// `build_html/ref_a_note_only_reference_renders_an_empty_li` pin them against
/// the live library.
#[test]
fn the_html_reference_list_reproduces_the_glue_and_the_note() {
    let article = article_from(&json!({
        "title": "Refs",
        "has_body": true,
        "references": [
            {
                "id": "r1", "label": "",
                "citation": "KalahastyR, MotatiL. Strokesight: a novel system. arXiv 2022",
                "authors": ["R Kalahasty", "L Motati"]
            },
            {"id": "cit20", "label": "", "citation": ""}
        ]
    }));
    let html = render_jats_html(&article);
    assert!(
        html.contains(
            "<li id=\"ref-r1\">KalahastyR, MotatiL. Strokesight: a novel system. arXiv 2022</li>"
        ),
        "{html}"
    );
    assert!(html.contains("<li id=\"ref-cit20\"></li>"), "{html}");
}
