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

//! One paper's record by identifier — port of `bmlib/publications/lookup.py`.
//!
//! [`lookup_record`] takes any of a PMID, a DOI, a PMC ID and a Europe PMC
//! preprint accession (`PPR` + digits) and returns the paper's
//! [`FetchedRecord`], ready to hand to `FullTextService::fetch_fulltext`:
//!
//! - **PubMed** (one efetch by PMID, the caller's or OpenAlex's) supplies the
//!   record when it holds the paper.
//! - **OpenAlex** is always asked, by the first of PMID, PMC ID and DOI: where
//!   PubMed has the paper it adds the identifiers PubMed's record lacks and its
//!   open-access locations; otherwise its work is the record.
//! - **Europe PMC** is asked only for a `PPR` accession; its record supplies the
//!   DOI the other two are asked by.
//! - **A preprint OpenAlex merged with its journal version** returns the
//!   preprint's own record, the journal version named in
//!   [`RecordLookup::published_version`] and not in the record. A DOI or PPR
//!   lookup PubMed did not confirm keeps OpenAlex's PMID and PMC ID out of the
//!   record, since for a merged preprint they are the journal version's.
//!
//! Each source's outcome is reported apart ([`SourceOutcome`]), so an outage
//! never reads as "no such paper". Nothing is retried. A body is read as UTF-8
//! whatever charset it declares. Held to the Python by
//! `tests/fixtures/record_lookup.json`.

use serde_json::Value;

use crate::publications::fetchers::europepmc::{self, EUROPEPMC_REST_BASE};
use crate::publications::fetchers::openalex;
use crate::publications::fetchers::pubmed::{self, EFETCH_URL};
use crate::publications::fetchers::registry::HttpClient;
use crate::publications::models::FetchedRecord;
use crate::publications::storage::normalize_doi;
use crate::pyvalue::{json_type_name, python_strip, repr_str};

/// OpenAlex's works endpoint; a work is `{OPENALEX_WORKS_URL}/{key}`.
pub const OPENALEX_WORKS_URL: &str = "https://api.openalex.org/works";

/// What one source made of the lookup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum SourceOutcome {
    /// The paper was found.
    Found,
    /// The source's own "not held": OpenAlex's 404, PubMed's empty set, Europe
    /// PMC's empty result list.
    NotFound,
    /// No answer: no connection, a timeout, any other non-200. Worth a retry.
    RequestFailed,
    /// A 200 whose body bmlib cannot read.
    Unreadable,
    /// Served, but not the paper asked about.
    OtherRecord,
    /// The PMID is a Bookshelf chapter, which the PubMed parser reads nothing from.
    BookRecord,
    /// Nothing to ask by: PubMed with no PMID, OpenAlex with no PMID, PMC ID or
    /// DOI, Europe PMC with no PPR accession.
    NotAsked,
}

impl SourceOutcome {
    /// The wire name, as Python's `SourceOutcome` value.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            SourceOutcome::Found => "found",
            SourceOutcome::NotFound => "not_found",
            SourceOutcome::RequestFailed => "request_failed",
            SourceOutcome::Unreadable => "unreadable",
            SourceOutcome::OtherRecord => "other_record",
            SourceOutcome::BookRecord => "book_record",
            SourceOutcome::NotAsked => "not_asked",
        }
    }
}

/// One source's outcome and a short message naming what happened.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceReport {
    /// What happened.
    pub outcome: SourceOutcome,
    /// The status, the parse failure or the identifier, in a few words.
    pub message: String,
}

/// The journal version of a preprint, named beside the preprint's record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishedVersion {
    /// Its PMID.
    pub pmid: String,
    /// Its DOI, as PubMed holds it.
    pub doi: String,
    /// Its PMC ID.
    pub pmc_id: Option<String>,
}

/// What [`lookup_record`] found, and what each source made of it.
#[derive(Debug, Clone, PartialEq)]
pub struct RecordLookup {
    /// The record, or `None` when no source has the paper.
    pub record: Option<FetchedRecord>,
    /// The journal version of a preprint OpenAlex merged with it.
    pub published_version: Option<PublishedVersion>,
    /// PubMed's outcome.
    pub pubmed: SourceReport,
    /// OpenAlex's outcome.
    pub openalex: SourceReport,
    /// Europe PMC's outcome.
    pub europepmc: SourceReport,
}

/// The caller's identifiers and keys. A blank identifier is none.
#[derive(Debug, Clone, Copy, Default)]
pub struct LookupRequest<'a> {
    /// A PMID: ASCII digits, not all zeros.
    pub pmid: Option<&'a str>,
    /// A DOI, a `doi:` / `https://doi.org/` prefix allowed; not shape-checked (#475).
    pub doi: Option<&'a str>,
    /// A PMC ID, its `PMC` prefix optional and case-folded.
    pub pmc_id: Option<&'a str>,
    /// A Europe PMC preprint accession (`PPR` + digits), case-folded.
    pub ppr_id: Option<&'a str>,
    /// OpenAlex's polite-pool contact (`mailto`).
    pub email: &'a str,
    /// Sent with the efetch when given.
    pub ncbi_api_key: Option<&'a str>,
    /// Sent to OpenAlex when given.
    pub openalex_api_key: Option<&'a str>,
}

/// Why a lookup was refused before any request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LookupError {
    /// No identifier was given (a blank one is none).
    NoIdentifier,
    /// An identifier is malformed.
    InvalidIdentifier {
        /// `pmid`, `pmc_id` or `ppr_id`.
        argument: &'static str,
        /// The value as given.
        value: String,
    },
}

impl std::fmt::Display for LookupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LookupError::NoIdentifier => {
                f.write_str("lookup_record needs a PMID, DOI, PMC ID or PPR accession")
            }
            LookupError::InvalidIdentifier { argument, value } => {
                let rule = match *argument {
                    "pmid" => "is not ASCII digits, or is all zeros",
                    "pmc_id" => "is not PMC followed by ASCII digits",
                    _ => "is not PPR followed by ASCII digits",
                };
                write!(f, "{argument} {} {rule}", repr_str(value))
            }
        }
    }
}

impl std::error::Error for LookupError {}

// ---------------------------------------------------------------------------
// The caller's identifiers
// ---------------------------------------------------------------------------

/// Python's `str.strip()`, `None` when nothing is left.
fn caller_text(value: Option<&str>) -> Option<&str> {
    value.map(python_strip).filter(|t| !t.is_empty())
}

fn ascii_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())
}

fn invalid(argument: &'static str, value: &str) -> LookupError {
    LookupError::InvalidIdentifier {
        argument,
        value: value.to_string(),
    }
}

fn lookup_pmid(pmid: Option<&str>) -> Result<Option<String>, LookupError> {
    let Some(text) = caller_text(pmid) else {
        return Ok(None);
    };
    let digits = text.trim_start_matches('0');
    if !ascii_digits(text) || digits.is_empty() {
        return Err(invalid("pmid", pmid.unwrap_or_default()));
    }
    Ok(Some(digits.to_string()))
}

/// `prefix` + ASCII digits, the prefix optional (`optional`) and case-folded.
fn accession(
    value: Option<&str>,
    prefix: &str,
    optional: bool,
    argument: &'static str,
) -> Result<Option<String>, LookupError> {
    let Some(text) = caller_text(value) else {
        return Ok(None);
    };
    let digits = match text.get(..prefix.len()) {
        Some(head) if head.eq_ignore_ascii_case(prefix) => &text[prefix.len()..],
        _ if optional => text,
        _ => "",
    };
    if !ascii_digits(digits) {
        return Err(invalid(argument, value.unwrap_or_default()));
    }
    Ok(Some(format!("{prefix}{digits}")))
}

// ---------------------------------------------------------------------------
// The three sources
// ---------------------------------------------------------------------------

/// The crate has no logging facade; `transparency::analyzer` writes its lines with
/// `eprintln!` and a level prefix, and this follows it — a source that could not
/// be used is a WARNING, as in the Python; the quieter outcomes are not written.
fn report(outcome: SourceOutcome, message: impl Into<String>) -> SourceReport {
    let message = message.into();
    if matches!(
        outcome,
        SourceOutcome::RequestFailed | SourceOutcome::Unreadable | SourceOutcome::OtherRecord
    ) {
        eprintln!(
            "bmlib.publications.lookup WARNING: lookup_record: {} ({message})",
            outcome.as_str()
        );
    }
    SourceReport { outcome, message }
}

/// Percent-encode as Python's `urllib.parse.quote(value, safe=safe)`.
fn quote(value: &str, safe: &[u8]) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || b"_.-~".contains(&byte) || safe.contains(&byte) {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

fn with_query(url: &str, params: &[(&str, &str)]) -> String {
    let query: Vec<String> = params
        .iter()
        .map(|(key, value)| format!("{key}={}", quote(value, b"")))
        .collect();
    format!("{url}?{}", query.join("&"))
}

/// The body of a 200, or the report for anything else — except a status in
/// `answers`, which is the source's own reply and is returned as `Ok(Err(status))`
/// for the caller to judge, with nothing logged (OpenAlex's 404 is "not held").
fn get_text_or_answer(
    http: &dyn HttpClient,
    url: &str,
    answers: &[u16],
) -> Result<Result<String, u16>, SourceReport> {
    let response = http
        .get(url)
        .map_err(|error| report(SourceOutcome::RequestFailed, error.to_string()))?;
    if answers.contains(&response.status) {
        return Ok(Err(response.status));
    }
    if response.status != 200 {
        return Err(report(
            SourceOutcome::RequestFailed,
            format!("HTTP {}", response.status),
        ));
    }
    String::from_utf8(response.body)
        .map(Ok)
        .map_err(|_| report(SourceOutcome::Unreadable, "the body is not UTF-8 text"))
}

/// The body of a 200, or the report for anything else.
fn get_text(http: &dyn HttpClient, url: &str) -> Result<String, SourceReport> {
    get_text_or_answer(http, url, &[]).map(|answer| answer.unwrap_or_default())
}

fn json_object(text: &str) -> Result<Value, SourceReport> {
    let body: Value = serde_json::from_str(text)
        .map_err(|_| report(SourceOutcome::Unreadable, "the body is not JSON"))?;
    if !body.is_object() {
        let kind = match json_type_name(&body) {
            "NoneType" => "null",
            "bool" => "boolean",
            "int" | "float" => "number",
            "str" => "string",
            _ => "list",
        };
        return Err(report(
            SourceOutcome::Unreadable,
            format!("the body is a {kind}, not a JSON object"),
        ));
    }
    Ok(body)
}

type Asked = (SourceReport, Option<FetchedRecord>);

fn ask_europepmc(http: &dyn HttpClient, ppr_id: &str) -> Asked {
    let query = format!("EXT_ID:{ppr_id} AND SRC:PPR");
    let url = with_query(
        &format!("{EUROPEPMC_REST_BASE}/search"),
        &[
            ("query", &query),
            ("resultType", "core"),
            ("format", "json"),
        ],
    );
    let body = match get_text(http, &url).and_then(|text| json_object(&text)) {
        Ok(body) => body,
        Err(failed) => return (failed, None),
    };
    let Some(results) = body
        .get("resultList")
        .and_then(|list| list.get("result"))
        .and_then(Value::as_array)
    else {
        let message = "the body carries no resultList.result list";
        return (report(SourceOutcome::Unreadable, message), None);
    };
    let Some(first) = results.first() else {
        let message = format!("Europe PMC holds no record for {ppr_id}");
        return (report(SourceOutcome::NotFound, message), None);
    };
    if !first.is_object() {
        let message = "the first result is not a JSON object";
        return (report(SourceOutcome::Unreadable, message), None);
    }
    if first.get("id").and_then(Value::as_str) != Some(ppr_id) {
        let id = first
            .get("id")
            .map_or_else(|| "None".to_string(), crate::pyvalue::python_repr);
        let message = format!("Europe PMC answered with {id}, not {ppr_id}");
        return (report(SourceOutcome::OtherRecord, message), None);
    }
    (
        report(SourceOutcome::Found, ""),
        europepmc::normalize(first),
    )
}

fn ask_openalex(
    http: &dyn HttpClient,
    kind: &str,
    value: &str,
    email: &str,
    api_key: Option<&str>,
) -> Asked {
    let key = if kind == "doi" {
        format!("{kind}:{}", quote(value, b"/:"))
    } else {
        format!("{kind}:{value}")
    };
    let mut params = vec![("mailto", email)];
    if let Some(api_key) = api_key.filter(|k| !k.is_empty()) {
        params.push(("api_key", api_key));
    }
    let url = with_query(&format!("{OPENALEX_WORKS_URL}/{key}"), &params);
    let text = match get_text_or_answer(http, &url, &[404]) {
        Ok(Ok(text)) => text,
        Ok(Err(_)) => {
            let message = format!("OpenAlex holds no work for {kind}:{value}");
            return (report(SourceOutcome::NotFound, message), None);
        }
        Err(failed) => return (failed, None),
    };
    let body = match json_object(&text) {
        Ok(body) => body,
        Err(failed) => return (failed, None),
    };
    let Some(record) = openalex::normalize(&body) else {
        return (
            report(SourceOutcome::Unreadable, "the body is not a work"),
            None,
        );
    };
    let carried = match kind {
        "pmid" => record.pmid.clone(),
        "pmcid" => record.pmc_id.clone(),
        _ => normalize_doi(record.doi.as_deref()),
    };
    if carried.as_deref() != Some(value) {
        let shown = carried
            .as_deref()
            .map_or_else(|| "None".to_string(), repr_str);
        let message = format!("OpenAlex's work for {kind}:{value} carries {kind} {shown}");
        return (report(SourceOutcome::OtherRecord, message), None);
    }
    (report(SourceOutcome::Found, ""), Some(record))
}

fn child_text<'a>(node: roxmltree::Node<'a, '_>, path: &[&str]) -> Option<&'a str> {
    let mut current = node;
    for name in path {
        current = current
            .children()
            .find(|c| c.is_element() && c.tag_name().name() == *name)?;
    }
    Some(current.text().unwrap_or(""))
}

fn ask_pubmed(http: &dyn HttpClient, pmid: &str, api_key: Option<&str>) -> Asked {
    let mut params = vec![("db", "pubmed"), ("id", pmid), ("retmode", "xml")];
    if let Some(api_key) = api_key.filter(|k| !k.is_empty()) {
        params.push(("api_key", api_key));
    }
    let text = match get_text(http, &with_query(EFETCH_URL, &params)) {
        Ok(text) => text,
        Err(failed) => return (failed, None),
    };
    let Ok(document) = pubmed::parse_ncbi_xml(&text) else {
        return (
            report(SourceOutcome::Unreadable, "the body is not XML"),
            None,
        );
    };
    let root = document.root_element();
    if root.tag_name().name() != "PubmedArticleSet" {
        let message = format!(
            "efetch answered with an <{}> root, not <PubmedArticleSet>",
            root.tag_name().name()
        );
        return (report(SourceOutcome::Unreadable, message), None);
    }
    let mut children = root
        .children()
        .filter(roxmltree::Node::is_element)
        .peekable();
    if children.peek().is_none() {
        let message = format!("PubMed holds no record for PMID {pmid}");
        return (report(SourceOutcome::NotFound, message), None);
    }
    for child in children {
        let name = child.tag_name().name();
        if name == "PubmedArticle" && child_text(child, &["MedlineCitation", "PMID"]) == Some(pmid)
        {
            return (
                report(SourceOutcome::Found, ""),
                Some(pubmed::parse_article(child)),
            );
        }
        if name == "PubmedBookArticle" && child_text(child, &["BookDocument", "PMID"]) == Some(pmid)
        {
            let message = format!("PMID {pmid} is a Bookshelf record");
            return (report(SourceOutcome::BookRecord, message), None);
        }
    }
    let message = format!("efetch served no record for PMID {pmid}");
    (report(SourceOutcome::OtherRecord, message), None)
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
enum Field {
    Pmid,
    Doi,
    Pmc,
}

const IDENTIFIERS: [Field; 3] = [Field::Pmid, Field::Doi, Field::Pmc];

fn slot(record: &mut FetchedRecord, id: Field) -> &mut Option<String> {
    match id {
        Field::Pmid => &mut record.pmid,
        Field::Doi => &mut record.doi,
        Field::Pmc => &mut record.pmc_id,
    }
}

fn same(id: Field, a: &str, b: &str) -> bool {
    match id {
        Field::Doi => normalize_doi(Some(a)) == normalize_doi(Some(b)),
        _ => a.eq_ignore_ascii_case(b),
    }
}

/// `base` with each empty identifier filled — the caller's first, then the
/// others' — and the others' full-text sources it lacks appended.
fn assemble(
    mut record: FetchedRecord,
    others: &[FetchedRecord],
    caller: [(Field, Option<&String>); 3],
    ppr_id: Option<&str>,
) -> FetchedRecord {
    for (id, given) in caller {
        let Some(given) = given else { continue };
        let held = slot(&mut record, id);
        match held {
            None => *held = Some(given.clone()),
            Some(held) if !same(id, held, given) => eprintln!(
                "bmlib.publications.lookup WARNING: lookup_record: the caller's {} {} differs from the record's {}; keeping the record's",
                match id {
                    Field::Pmid => "pmid",
                    Field::Doi => "doi",
                    Field::Pmc => "pmc_id",
                },
                repr_str(given),
                repr_str(held)
            ),
            Some(_) => {}
        }
    }
    let mut seen: Vec<String> = record
        .fulltext_sources
        .iter()
        .filter_map(|e| e.get("url").and_then(Value::as_str).map(str::to_string))
        .collect();
    for other in others {
        let mut other = other.clone();
        for id in IDENTIFIERS {
            let theirs = slot(&mut other, id).clone();
            let ours = slot(&mut record, id);
            if ours.is_none() && theirs.as_deref().is_some_and(|t| !t.is_empty()) {
                *ours = theirs;
            }
        }
        for entry in &other.fulltext_sources {
            let url = entry.get("url").and_then(Value::as_str).unwrap_or_default();
            if !seen.iter().any(|s| s == url) {
                seen.push(url.to_string());
                record.fulltext_sources.push(entry.clone());
            }
        }
        record.is_open_access = record.is_open_access || other.is_open_access;
    }
    if let Some(ppr_id) = ppr_id {
        record
            .extras
            .insert("ppr_id".to_string(), Value::String(ppr_id.to_string()));
    }
    record
}

/// Look up one paper's record by any of its identifiers.
///
/// The lookup's identity is the first given of PMID, PMC ID, PPR and DOI; the
/// caller's other identifiers fill only empty fields, a disagreement being
/// logged at WARNING and the record's kept. One source failing still returns
/// the others' record.
///
/// # Errors
///
/// [`LookupError`] before any request, for a malformed identifier or none.
pub fn lookup_record(
    http: &dyn HttpClient,
    request: &LookupRequest<'_>,
) -> Result<RecordLookup, LookupError> {
    let pmid = lookup_pmid(request.pmid)?;
    let doi = normalize_doi(caller_text(request.doi));
    let pmc_id = accession(request.pmc_id, "PMC", true, "pmc_id")?;
    let ppr_id = accession(request.ppr_id, "PPR", false, "ppr_id")?;
    if pmid.is_none() && doi.is_none() && pmc_id.is_none() && ppr_id.is_none() {
        return Err(LookupError::NoIdentifier);
    }

    let (epmc_report, epmc) = match &ppr_id {
        Some(ppr_id) => ask_europepmc(http, ppr_id),
        None => (report(SourceOutcome::NotAsked, "no PPR accession"), None),
    };

    let lookup_doi = doi
        .clone()
        .or_else(|| epmc.as_ref().and_then(|r| normalize_doi(r.doi.as_deref())));
    let key = match (&pmid, &pmc_id, &lookup_doi) {
        (Some(pmid), _, _) => Some(("pmid", pmid.as_str())),
        (None, Some(pmc_id), _) => Some(("pmcid", pmc_id.as_str())),
        (None, None, Some(doi)) => Some(("doi", doi.as_str())),
        _ => None,
    };
    let (oa_report, oa) = match key {
        Some((kind, value)) => {
            ask_openalex(http, kind, value, request.email, request.openalex_api_key)
        }
        None => (
            report(SourceOutcome::NotAsked, "no PMID, PMC ID or DOI"),
            None,
        ),
    };

    let oa_pmid = oa
        .as_ref()
        .and_then(|r| r.pmid.as_deref())
        .filter(|p| ascii_digits(p))
        .map(|p| p.trim_start_matches('0'))
        .filter(|p| !p.is_empty());
    let pubmed_pmid = pmid.as_deref().or(oa_pmid).map(str::to_string);
    let (mut pm_report, pm) = match &pubmed_pmid {
        Some(pubmed_pmid) => ask_pubmed(http, pubmed_pmid, request.ncbi_api_key),
        None => (report(SourceOutcome::NotAsked, "no PMID"), None),
    };

    let mut published_version = None;
    // PubMed's DOI, when it is not the one the lookup was made by.
    let published_doi = pm
        .as_ref()
        .and_then(|r| r.doi.clone())
        .filter(|d| normalize_doi(Some(d)) != lookup_doi);
    let is_published_version =
        pmid.is_none() && pmc_id.is_none() && lookup_doi.is_some() && oa.is_some();
    let published = (is_published_version, &pm, &oa, published_doi, &pubmed_pmid);
    let candidates: Vec<FetchedRecord> = match published {
        (true, Some(pm), Some(oa), Some(doi), Some(pubmed_pmid)) => {
            // `pubmed_pmid` is `pm`'s own PMID, #474's rule having matched it.
            pm_report = report(
                SourceOutcome::Found,
                format!(
                    "PMID {pubmed_pmid} is a published version (DOI {doi}), \
                     returned as published_version"
                ),
            );
            published_version = Some(PublishedVersion {
                pmid: pubmed_pmid.clone(),
                doi,
                pmc_id: pm.pmc_id.clone().or_else(|| oa.pmc_id.clone()),
            });
            let mut preprint = oa.clone();
            preprint.pmid = None;
            preprint.pmc_id = None;
            preprint
                .fulltext_sources
                .retain(|e| e.get("version").and_then(Value::as_str) == Some("preprint"));
            epmc.clone().into_iter().chain([preprint]).collect()
        }
        _ => {
            let mut oa = oa;
            if pm.is_none() && pmid.is_none() && pmc_id.is_none() {
                // Asked by a DOI, OpenAlex's PMID and PMC ID may be a merged
                // journal version's, and only PubMed's record — its DOI compared
                // above — can tell; unconfirmed, they stay out, so an outage
                // cannot decide which version the record names (PR review).
                if let Some(work) = oa.as_mut() {
                    work.pmid = None;
                    work.pmc_id = None;
                }
            }
            [pm, epmc, oa].into_iter().flatten().collect()
        }
    };
    let caller = [
        (Field::Pmid, pmid.as_ref()),
        (Field::Doi, doi.as_ref()),
        (Field::Pmc, pmc_id.as_ref()),
    ];
    let record = candidates
        .split_first()
        .map(|(base, others)| assemble(base.clone(), others, caller, ppr_id.as_deref()));

    Ok(RecordLookup {
        record,
        published_version,
        pubmed: pm_report,
        openalex: oa_report,
        europepmc: epmc_report,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::publications::fetchers::registry::{FetchError, HttpResponse};

    struct Answer(Result<HttpResponse, FetchError>);

    impl HttpClient for Answer {
        fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
            self.0.clone()
        }
    }

    fn doi_lookup(answer: Result<HttpResponse, FetchError>) -> SourceReport {
        let request = LookupRequest {
            doi: Some("10.1/x"),
            email: "a@b.c",
            ..LookupRequest::default()
        };
        lookup_record(&Answer(answer), &request)
            .expect("a lookup")
            .openalex
    }

    #[test]
    fn the_messages_are_the_pythons() {
        assert_eq!(
            doi_lookup(Ok(HttpResponse::from_bytes(503, Vec::new()))).message,
            "HTTP 503"
        );
        assert_eq!(
            doi_lookup(Ok(HttpResponse::from_bytes(404, Vec::new()))).message,
            "OpenAlex holds no work for doi:10.1/x"
        );
        assert_eq!(
            doi_lookup(Ok(HttpResponse::ok_text("[]"))).message,
            "the body is a list, not a JSON object"
        );
        assert_eq!(
            doi_lookup(Ok(HttpResponse::ok_text("<html>"))).message,
            "the body is not JSON"
        );
    }

    #[test]
    fn an_invalid_identifier_is_named() {
        let request = LookupRequest {
            pmc_id: Some("PMC١٢٣"),
            ..LookupRequest::default()
        };
        let error = lookup_record(&Answer(Err(FetchError::Transport("x".into()))), &request)
            .expect_err("refused");
        assert_eq!(
            error.to_string(),
            "pmc_id 'PMC١٢٣' is not PMC followed by ASCII digits"
        );
    }

    #[test]
    fn quote_is_pythons() {
        assert_eq!(quote("10.1/a?b#c d", b"/:"), "10.1/a%3Fb%23c%20d");
        assert_eq!(quote("10.1/é", b"/:"), "10.1/%C3%A9");
    }
}
