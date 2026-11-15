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

//! One Europe PMC search record as a [`FetchedRecord`] — port of
//! `bmlib/publications/fetchers/europepmc.py`.
//!
//! Not a sync fetcher: [`crate::publications::lookup::lookup_record`] asks Europe
//! PMC for a preprint accession (`PPR` + digits), which neither PubMed nor
//! OpenAlex knows, and this reads the `resultType=core` hit it gets back. Every
//! field is read type-checked, so a value of the wrong type costs that field and
//! not the record.

use std::sync::LazyLock;

use regex::{Captures, Regex};
use serde_json::{json, Value};

use chrono::NaiveDate;

use crate::publications::fetchers::pubmed::escape_markdown;
use crate::publications::fetchers::reconcile::reconcile_delivery;
use crate::publications::fetchers::registry::{
    CountingSink, FetchError, FetchOutcome, FetchRequest, FetchSink, Fetcher, HttpClient, Progress,
};
use crate::publications::models::{FetchedRecord, FullTextSourceEntry};
use crate::pyvalue::json_type_name;
use crate::transparency::analyzer::encode_query;

/// Restated, as `transparency` and `fulltext` each keep their own literal.
pub const EUROPEPMC_REST_BASE: &str = "https://www.ebi.ac.uk/europepmc/webservices/rest";

// `fullTextXML` addresses a PMC or PPR accession, never a PMID (#188).
static PPR_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^PPR[0-9]+$").expect("valid"));
static PMC_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^PMC[0-9]+$").expect("valid"));

// The abstract is JATS-like markup that is not XML — the measured record
// carries a raw `&` — so tags are stripped by rule rather than parsed.
static BLOCK_TAG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"</?(?:title|p)\b[^>]*>").expect("valid"));
static ANY_TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]*>").expect("valid"));
static ENTITY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"&(lt|gt|amp|quot|apos|#[0-9]+|#x[0-9A-Fa-f]+);").expect("valid"));
// ASCII whitespace only, as the Python's: its `\s` and `str.strip` also take
// U+001C-U+001F, which Rust's do not (#557's divergence).
const ASCII_SPACE: &[char] = &[' ', '\t', '\n', '\r', '\x0C', '\x0B'];
static SPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t\n\r\x0C\x0B]+").expect("valid"));

fn text(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn object<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.get(key).filter(|v| v.is_object())
}

fn list<'a>(value: Option<&'a Value>, key: &str) -> &'a [Value] {
    value
        .and_then(|v| v.get(key))
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

/// One XML entity or character reference, decoded; an unusable one kept.
fn entity(caps: &Captures<'_>) -> String {
    let name = &caps[1];
    let named = match name {
        "lt" => Some('<'),
        "gt" => Some('>'),
        "amp" => Some('&'),
        "quot" => Some('"'),
        "apos" => Some('\''),
        _ => None,
    };
    let decoded = named.or_else(|| {
        let code = match name.strip_prefix("#x") {
            Some(hex) => u32::from_str_radix(hex, 16).ok(),
            None => name[1..].parse::<u32>().ok(),
        };
        // A surrogate or a value past U+10FFFF names no character.
        code.and_then(char::from_u32)
    });
    decoded.map_or_else(|| caps[0].to_string(), String::from)
}

/// Europe PMC's `abstractText` as Markdown paragraphs, or `None`: each
/// `<title>` and `<p>` a paragraph, every other tag removed, the five XML
/// entities and numeric references decoded (any other entity kept), whitespace
/// collapsed and the prose escaped as the PubMed parser escapes it.
fn abstract_text(raw: &str) -> Option<String> {
    let blocks: Vec<String> = BLOCK_TAG
        .split(raw)
        .filter_map(|block| {
            let untagged = ANY_TAG.replace_all(block, "");
            let decoded = ENTITY.replace_all(&untagged, |caps: &Captures<'_>| entity(caps));
            let collapsed = SPACE.replace_all(&decoded, " ");
            let plain = collapsed.trim_matches(ASCII_SPACE);
            (!plain.is_empty()).then(|| escape_markdown(plain))
        })
        .collect();
    (!blocks.is_empty()).then(|| blocks.join("\n\n"))
}

/// `"Last, First"` as the PubMed parser writes it, else the full name.
fn author(value: &Value) -> Option<String> {
    let last = text(value, "lastName");
    match (last, text(value, "firstName")) {
        (Some(last), Some(first)) => Some(format!("{last}, {first}")),
        (last, _) => text(value, "fullName").or(last),
    }
}

/// Convert one Europe PMC `resultType=core` search hit to a record, or `None`
/// when the hit is not a JSON object.
///
/// `source="europepmc"`. A `PPR` accession is kept in `extras["ppr_id"]`, and a
/// record Europe PMC holds the text of (`inEPMC` `"Y"`) gains an `xml` source
/// for its `fullTextXML`, addressed by its PMC ID, else its `PPR` accession —
/// the only way `FullTextService` reaches a preprint's text.
#[must_use]
pub fn normalize(raw: &Value) -> Option<FetchedRecord> {
    raw.as_object()?;
    let ppr_id = text(raw, "id").filter(|id| PPR_ID.is_match(id));
    let pmc_id = text(raw, "pmcid").filter(|id| PMC_ID.is_match(id));
    let is_open_access = text(raw, "isOpenAccess").as_deref() == Some("Y");

    let mut record = FetchedRecord::new(text(raw, "title").unwrap_or_default(), "europepmc");
    if let Some(address) = pmc_id.as_ref().or(ppr_id.as_ref()) {
        if text(raw, "inEPMC").as_deref() == Some("Y") {
            record.fulltext_sources.push(FullTextSourceEntry {
                url: format!("{EUROPEPMC_REST_BASE}/{address}/fullTextXML"),
                format: "xml".to_string(),
                source: "europepmc".to_string(),
                open_access: is_open_access,
                version: None,
            });
        }
    }
    record.authors = list(object(raw, "authorList"), "author")
        .iter()
        .filter_map(author)
        .collect();
    record.journal = object(raw, "journalInfo")
        .and_then(|info| object(info, "journal"))
        .and_then(|journal| text(journal, "title"))
        .or_else(|| object(raw, "bookOrReportDetails").and_then(|d| text(d, "publisher")));
    record.publication_types = list(object(raw, "pubTypeList"), "pubType")
        .iter()
        .filter_map(Value::as_str)
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect();
    record.doi = text(raw, "doi");
    // Held to a caller's PMID rule (#470): it enters a lookup's record
    // whenever PubMed supplied none.
    record.pmid = text(raw, "pmid")
        .filter(|p| p.bytes().all(|b| b.is_ascii_digit()))
        .map(|p| p.trim_start_matches('0').to_string())
        .filter(|p| !p.is_empty());
    record.pmc_id = pmc_id;
    record.abstract_text = raw
        .get("abstractText")
        .and_then(Value::as_str)
        .and_then(abstract_text);
    record.publication_date = text(raw, "firstPublicationDate");
    record.is_open_access = is_open_access;
    record.license = text(raw, "license");
    if let Some(ppr_id) = ppr_id {
        record.extras.insert("ppr_id".to_string(), json!(ppr_id));
    }
    Some(record)
}

// ---------------------------------------------------------------------------
// Day-by-day sync source — port of `fetch_europepmc`
// ---------------------------------------------------------------------------

/// The search endpoint (`{EUROPEPMC_REST_BASE}/search`).
pub const SEARCH_URL: &str = "https://www.ebi.ac.uk/europepmc/webservices/rest/search";

/// The API's largest page.
pub const PAGE_SIZE: usize = 1000;

/// The pause between requests, in seconds. Europe PMC publishes no hard limit;
/// the caller's transport spaces requests (bmlib's fetchers do not sleep).
pub const RATE_LIMIT_SECONDS: f64 = 1.0;

/// Europe PMC adds records for past dates late, so recent days are re-fetched.
pub const EUROPEPMC_SETTLE_DAYS: u32 = 14;

/// The cursor that starts a walk.
pub const FIRST_CURSOR: &str = "*";

/// The query parameters for one page of `query` on `date_str` (Python's order).
#[must_use]
pub fn page_params(
    date_str: &str,
    cursor: &str,
    query: &str,
    email: Option<&str>,
) -> Vec<(String, String)> {
    let mut params = vec![
        (
            "query".to_string(),
            format!("({query}) AND FIRST_PDATE:[{date_str} TO {date_str}]"),
        ),
        ("resultType".to_string(), "core".to_string()),
        ("format".to_string(), "json".to_string()),
        ("pageSize".to_string(), PAGE_SIZE.to_string()),
        ("cursorMark".to_string(), cursor.to_string()),
    ];
    if let Some(email) = email.filter(|e| !e.is_empty()) {
        params.push(("email".to_string(), email.to_string()));
    }
    params
}

/// One page of a search, as the walk asks for it; the seam the tests script.
pub trait SearchPages {
    /// The decoded JSON body of the page `params` names.
    ///
    /// # Errors
    ///
    /// [`FetchError`] when the request failed or the body is not JSON.
    fn page(&self, params: &[(String, String)]) -> Result<Value, FetchError>;
}

fn failed_outcome(message: String, record_count: i64) -> FetchOutcome {
    FetchOutcome {
        record_count,
        status: "failed".to_string(),
        promised: None,
        stalled: false,
        error: Some(message),
        note: None,
        parts: Vec::new(),
    }
}

/// Python's exception text for a failed page request.
fn error_text(error: &FetchError) -> String {
    match error {
        // Deterministic, as the Python oracle's fake response raises it.
        FetchError::HttpStatus { status, .. } => format!("HTTPStatusError: HTTP {status}"),
        FetchError::Transport(message) => format!("TransportError: {message}"),
        other => format!("ValueError: {other}"),
    }
}

/// Walk every record matching `query` first published on `date`, handing each
/// to `sink` (Python's `fetch_europepmc`).
///
/// The promise is the first page's `hitCount`; the walk follows `cursorMark`
/// until the cursor stops moving or a page is empty, and
/// [`reconcile_delivery`] judges what arrived against the promise.
#[must_use]
pub fn walk(
    source: &dyn SearchPages,
    date: NaiveDate,
    query: &str,
    email: Option<&str>,
    sink: &mut dyn FetchSink,
) -> FetchOutcome {
    let mut sink = CountingSink::new(sink);
    let date_str = date.format("%Y-%m-%d").to_string();
    if query.trim_matches(ASCII_SPACE).is_empty() {
        return failed_outcome(
            "Europe PMC needs a search: the query parameter is empty".to_string(),
            0,
        );
    }
    let mut cursor = FIRST_CURSOR.to_string();
    let mut promised = 0i64;
    let mut is_first_page = true;
    let mut stalled = false;

    loop {
        let data = match source.page(&page_params(&date_str, &cursor, query, email)) {
            Ok(data) => data,
            Err(error) => return failed_outcome(error_text(&error), sink.delivered()),
        };
        if !data.is_object() {
            return failed_outcome(
                format!(
                    "Europe PMC returned a {} payload, not an object, for {date_str}",
                    json_type_name(&data)
                ),
                sink.delivered(),
            );
        }
        let Some(results) = data
            .get("resultList")
            .and_then(|r| r.get("result"))
            .and_then(Value::as_array)
        else {
            return failed_outcome(
                format!("Europe PMC returned a page carrying no result list for {date_str}"),
                sink.delivered(),
            );
        };
        if is_first_page {
            let Some(count) = data
                .get("hitCount")
                .filter(|v| !v.is_boolean())
                .and_then(Value::as_i64)
            else {
                return failed_outcome(
                    format!("Europe PMC returned a page with no numeric hitCount for {date_str}"),
                    sink.delivered(),
                );
            };
            promised = count;
            is_first_page = false;
        }
        for raw in results {
            let Some(record) = normalize(raw) else {
                return failed_outcome(
                    format!(
                        "Europe PMC returned a page bmlib could not normalise for {date_str}: \
                         TypeError: a result that is a {}, not an object",
                        json_type_name(raw)
                    ),
                    sink.delivered(),
                );
            };
            sink.record(record);
        }
        if results.is_empty() {
            stalled = sink.delivered() < promised;
            break;
        }
        sink.progress(Progress::Page {
            delivered: sink.delivered(),
            promised: Some(promised),
        });
        match data.get("nextCursorMark").and_then(Value::as_str) {
            // Europe PMC repeats the cursor it was sent on the last page.
            Some(next) if !next.is_empty() && next != cursor => cursor = next.to_string(),
            _ => break,
        }
    }

    let verdict = reconcile_delivery(
        "europepmc",
        &date_str,
        sink.delivered(),
        Some(promised),
        stalled,
    );
    FetchOutcome {
        record_count: sink.delivered(),
        status: if verdict.is_failure() {
            "failed".to_string()
        } else {
            "completed".to_string()
        },
        promised: Some(promised),
        stalled,
        error: verdict.failure,
        note: verdict.note,
        parts: Vec::new(),
    }
}

/// [`SearchPages`] over an [`HttpClient`], with the parameters percent-encoded.
pub struct HttpSearchPages {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
}

impl SearchPages for HttpSearchPages {
    fn page(&self, params: &[(String, String)]) -> Result<Value, FetchError> {
        let pairs: Vec<(&str, &str)> = params
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        let url = format!("{SEARCH_URL}?{}", encode_query(&pairs));
        let response = self.client.get(&url)?;
        if !response.is_success() {
            return Err(FetchError::HttpStatus {
                url,
                status: response.status,
            });
        }
        response.json(&url)
    }
}

/// The `europepmc` source: a Europe PMC search (config `query`, optional
/// `email`) walked one first-publication day at a time.
pub struct EuropePmcFetcher {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
}

impl Fetcher for EuropePmcFetcher {
    fn fetch(
        &self,
        request: &FetchRequest,
        sink: &mut dyn FetchSink,
    ) -> Result<FetchOutcome, FetchError> {
        let source = HttpSearchPages {
            client: self.client.clone(),
        };
        Ok(walk(
            &source,
            request.date,
            request.config_value("query").unwrap_or_default(),
            request.config_value("email"),
            sink,
        ))
    }
}
