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

//! bioRxiv and medRxiv preprint fetcher.
//!
//! A port of `bmlib/publications/fetchers/biorxiv.py`. One endpoint serves both
//! servers, controlled by a parameter.
//!
//! # Three guards that all exist for the same failure
//!
//! A day stored `completed` is never offered again once it is in the past, so
//! **anything that turns an unreadable or truncated answer into a quiet
//! success loses those records permanently**. Each guard below refuses a
//! response rather than reading it as an empty day, and each was added because
//! the alternative did exactly that:
//!
//! - A **non-object payload** read through `.get(..., [])` is indistinguishable
//!   from a day with no preprints (#88).
//! - A body carrying **neither `collection` nor `messages`** makes no claim
//!   about the day at all, so it cannot be accepted. The test is "carries no
//!   evidence either way" rather than "carries a collection", and the
//!   difference is deliberate: bioRxiv's quiet day is *known* to omit `total`
//!   (`DECISIONS.md`), whether it also omits `collection` is **not measured**,
//!   and requiring a key the API may not send on a quiet day would fail that
//!   day on every run for the life of the installation — the runaway-retry cost
//!   the reconciliation rules exist to avoid. #94 is the live sampler that
//!   would let this be tightened.
//! - An **absent `total`** stays `None` rather than becoming `0`. Flattening
//!   the two makes "the source said this day is empty" and "the source said
//!   nothing" identical, and the second silently switches off both
//!   reconciliation rules — a walk that then stops early completes with no
//!   shortfall and no stall detected.

use chrono::NaiveDate;

use crate::publications::fetchers::reconcile::reconcile_delivery;
use crate::publications::fetchers::registry::{
    FetchError, FetchOutcome, FetchRequest, Fetcher, HttpClient, PartDisposition, Progress,
};
use crate::publications::models::FetchedRecord;

/// The bioRxiv details endpoint.
pub const BASE_URL: &str = "https://api.biorxiv.org/details";

/// How many records a full page carries.
pub const PAGE_SIZE: usize = 100;

/// The pause between pages, in seconds.
pub const RATE_LIMIT_SECONDS: f64 = 0.5;

/// Build the URL for one page.
#[must_use]
pub fn page_url(server: &str, date: &str, cursor: usize) -> String {
    format!("{BASE_URL}/{server}/{date}/{date}/{cursor}")
}

/// The PDF URL for a preprint, from its DOI and version.
///
/// The **record's own version**, not a hard-coded `v1`: a v2+ preprint's `v1`
/// URL 404s or points at the wrong revision.
#[must_use]
pub fn pdf_url(server: &str, doi: &str, version: Option<&serde_json::Value>) -> String {
    let version = match version {
        None | Some(serde_json::Value::Null) => "1".to_string(),
        Some(v) => {
            let text = match v {
                serde_json::Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            let trimmed = text.trim();
            if trimmed.is_empty() {
                "1".to_string()
            } else {
                trimmed.to_string()
            }
        }
    };
    format!("https://www.{server}.org/content/{doi}v{version}.full.pdf")
}

/// A JSON string field, or `None` for an absent, null or empty value.
fn text(raw: &serde_json::Value, key: &str) -> Option<String> {
    raw.get(key)
        .and_then(serde_json::Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// Convert a raw bioRxiv/medRxiv API record to a [`FetchedRecord`].
///
/// # Why absent optionals become `None` and not `""`
///
/// An empty string is **not SQL NULL**, so the storage layer's `COALESCE`-based
/// merge could never fill the field in from another source later — the empty
/// string would win for ever.
#[must_use]
pub fn normalize(raw: &serde_json::Value, server: &str) -> FetchedRecord {
    let doi = text(raw, "doi");
    let authors: Vec<String> = raw
        .get("authors")
        .and_then(serde_json::Value::as_str)
        .map(|s| {
            s.split(';')
                .map(str::trim)
                .filter(|a| !a.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();

    let mut fulltext_sources: Vec<serde_json::Value> = Vec::new();
    if let Some(doi) = doi.as_deref() {
        fulltext_sources.push(serde_json::json!({
            "url": pdf_url(server, doi, raw.get("version")),
            "format": "pdf",
            "source": server,
            "open_access": true,
        }));
    }
    if let Some(jatsxml) = text(raw, "jatsxml") {
        fulltext_sources.push(serde_json::json!({
            "url": jatsxml,
            "format": "xml",
            "source": server,
            "open_access": true,
        }));
    }

    let mut record = FetchedRecord::new(text(raw, "title").unwrap_or_default(), server);
    record.doi = doi;
    record.abstract_text = text(raw, "abstract");
    record.authors = authors;
    record.publication_date = text(raw, "date");
    record.is_open_access = true;
    record.fulltext_sources = fulltext_sources;
    record.extras.insert(
        "category".to_string(),
        serde_json::json!(text(raw, "category").unwrap_or_default()),
    );
    record.extras.insert(
        "published".to_string(),
        serde_json::json!(text(raw, "published").unwrap_or_default()),
    );
    record.extras.insert(
        "server".to_string(),
        serde_json::json!(text(raw, "server").unwrap_or_else(|| server.to_string())),
    );
    record
}

/// What one response body claims about the day.
#[derive(Debug, Clone, PartialEq)]
pub struct PageBody {
    /// The records the page carries.
    pub collection: Vec<serde_json::Value>,
    /// The total the source named, if it named one.
    pub total: Option<i64>,
}

/// Read one response body, refusing a shape that makes no claim about the day.
///
/// # Errors
///
/// [`FetchError::Malformed`] for a non-object payload, a body carrying neither
/// `collection` nor `messages`, a `collection` that is not a list, or a
/// non-numeric `total`.
pub fn read_page_body(
    data: &serde_json::Value,
    server: &str,
    date_str: &str,
) -> Result<PageBody, FetchError> {
    // Checked rather than defaulted (#88): read through `.get(..., [])`, an
    // HTTP-200 error body is indistinguishable from a day with no preprints.
    if !data.is_object() {
        return Err(FetchError::Malformed(format!(
            "{server} returned a {} payload, not an object",
            json_type_name(data)
        )));
    }

    let messages: Vec<&serde_json::Value> = data
        .get("messages")
        .and_then(serde_json::Value::as_array)
        .map(|a| a.iter().collect())
        .unwrap_or_default();

    // "Carries no evidence either way", not "carries a collection" — see the
    // module docs for why requiring a non-empty `collection` would fail
    // bioRxiv's quiet day on every run.
    //
    // The test for `messages` is on its **contents and not its key**: Python
    // computes `messages = data.get("messages")` and checks the resulting
    // list's truthiness, so `{"messages": []}` is no claim at all. Testing
    // `contains_key` instead accepts that body and diverges.
    if !data
        .as_object()
        .is_some_and(|o| o.contains_key("collection"))
        && messages.is_empty()
    {
        return Err(FetchError::Malformed(format!(
            "{server} returned an object carrying neither a collection nor messages, \
             so it makes no claim about the day"
        )));
    }

    let collection = match data.get("collection") {
        None | Some(serde_json::Value::Null) => Vec::new(),
        Some(serde_json::Value::Array(items)) => items.clone(),
        Some(_) => {
            return Err(FetchError::Malformed(format!(
                "{server} returned a collection that is not a list"
            )))
        }
    };

    // Absent `total` stays `None` rather than 0 (#88) — see the module docs.
    let mut total = None;
    if let Some(first) = messages.first() {
        if let Some(raw_total) = first.get("total").filter(|v| !v.is_null()) {
            let parsed = match raw_total {
                serde_json::Value::Number(n) => n.as_i64(),
                serde_json::Value::String(s) => s.trim().parse::<i64>().ok(),
                _ => None,
            };
            // Named, because the day retries on every run until the cause is
            // fixed and a bare parse error says neither which source nor which
            // field is at fault.
            total = Some(parsed.ok_or_else(|| {
                FetchError::Malformed(format!(
                    "{server} reported a non-numeric total {} for {date_str}",
                    python_repr(raw_total)
                ))
            })?);
        }
    }

    Ok(PageBody { collection, total })
}

fn json_type_name(value: &serde_json::Value) -> &'static str {
    match value {
        serde_json::Value::Null => "NoneType",
        serde_json::Value::Bool(_) => "bool",
        serde_json::Value::Number(n) if n.is_f64() => "float",
        serde_json::Value::Number(_) => "int",
        serde_json::Value::String(_) => "str",
        serde_json::Value::Array(_) => "list",
        serde_json::Value::Object(_) => "dict",
    }
}

fn python_repr(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Null => "None".to_string(),
        serde_json::Value::Bool(true) => "True".to_string(),
        serde_json::Value::Bool(false) => "False".to_string(),
        serde_json::Value::String(s) => format!("'{s}'"),
        other => other.to_string(),
    }
}

/// One page the walk fetched, so the walk itself is testable without a socket.
pub trait PageSource {
    /// Fetch one page by cursor.
    ///
    /// # Errors
    ///
    /// [`FetchError::Transport`] when the request never completed.
    fn page(
        &self,
        server: &str,
        date: &str,
        cursor: usize,
    ) -> Result<serde_json::Value, FetchError>;
}

/// Walk every page for one day.
///
/// The loop is separated from the transport so that a test can present a
/// sequence of bodies — including the shapes that must be refused — without a
/// network, and so the cursor arithmetic is exercised directly.
///
/// # Errors
///
/// [`FetchError`] for a refused response or a transport failure. A walk that
/// ran but came up short is **not** an error: it is an outcome whose status the
/// reconciliation decides.
pub fn walk(
    source: &dyn PageSource,
    server: &str,
    date: NaiveDate,
    on_progress: &mut dyn FnMut(Progress),
) -> Result<FetchOutcome, FetchError> {
    let date_str = date.format("%Y-%m-%d").to_string();
    let mut cursor = 0usize;
    let mut delivered = 0i64;
    let mut promised: Option<i64> = None;
    let mut records: Vec<FetchedRecord> = Vec::new();
    let mut stalled = false;

    loop {
        let data = source.page(server, &date_str, cursor)?;
        let body = read_page_body(&data, server, &date_str)?;

        // The first page that names a total fixes it; a later page does not
        // override it.
        if promised.is_none() {
            promised = body.total;
        }

        if body.collection.is_empty() {
            // An empty page while the source's own total says records remain is
            // a walk that stopped serving them, not the end.
            stalled = promised.is_some_and(|total| delivered < total);
            break;
        }

        for raw in &body.collection {
            records.push(normalize(raw, server));
            delivered += 1;
        }

        on_progress(Progress::Page {
            delivered,
            promised,
        });

        // A short page is the last page.
        if body.collection.len() < PAGE_SIZE {
            break;
        }

        cursor += PAGE_SIZE;
    }

    let verdict = reconcile_delivery(server, &date_str, delivered, promised, stalled);
    Ok(FetchOutcome {
        records,
        status: if verdict.is_failure() {
            "failed".to_string()
        } else {
            "completed".to_string()
        },
        promised,
        stalled,
        error: verdict.failure,
        note: verdict.note,
        parts: Vec::<PartDisposition>::new(),
    })
}

/// The **verbatim** fetch, returning an outcome for every input.
///
/// The difference from [`walk`] is Python's `except Exception` block, and it is
/// not decoration: an error surfaces as a **`failed` day** whose message is
/// preceded by the exception's type name, because
/// "`ValueError: biorxiv returned a list payload`" and
/// "`RemoteProtocolError: connection closed`" call for opposite responses and
/// read identically without it.
///
/// [`walk`] keeps the strict form, which is what a caller that wants to handle
/// the error itself — or a test that wants to see it — should use.
pub fn fetch_biorxiv(
    source: &dyn PageSource,
    server: &str,
    date: NaiveDate,
    on_progress: &mut dyn FnMut(Progress),
) -> FetchOutcome {
    // The progress fallback Python applies (`records_total or total_fetched`) is
    // a *caller-side* mapping and lives in the progress consumer, not here.
    match walk(source, server, date, on_progress) {
        Ok(outcome) => outcome,
        Err(error) => FetchOutcome {
            records: Vec::new(),
            status: "failed".to_string(),
            promised: None,
            stalled: false,
            error: Some(format!("{}: {error}", error_type_name(&error))),
            note: None,
            parts: Vec::new(),
        },
    }
}

/// The Python exception name a [`FetchError`] corresponds to.
///
/// Named rather than printed as a Rust variant because the message is what a
/// caller reads beside the Python implementation's, and the two must say the
/// same thing.
fn error_type_name(error: &FetchError) -> &'static str {
    match error {
        FetchError::Transport(_) => "RemoteProtocolError",
        FetchError::Malformed(_) => "ValueError",
        FetchError::Config(_) => "ValueError",
        FetchError::ResumeUnreadable(_) => "ValueError",
    }
}

/// The bioRxiv/medRxiv fetcher, over an injected page source and transport.
pub struct BiorxivFetcher {
    /// The HTTP client.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
    /// `"biorxiv"` or `"medrxiv"`.
    pub server: String,
}

impl BiorxivFetcher {
    /// A fetcher for one of the two servers.
    #[must_use]
    pub fn new(client: std::sync::Arc<dyn HttpClient + Send + Sync>, server: &str) -> Self {
        BiorxivFetcher {
            client,
            server: server.to_string(),
        }
    }
}

/// A [`PageSource`] over an [`HttpClient`].
pub struct HttpPageSource {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
}

impl PageSource for HttpPageSource {
    fn page(
        &self,
        server: &str,
        date: &str,
        cursor: usize,
    ) -> Result<serde_json::Value, FetchError> {
        let url = page_url(server, date, cursor);
        let response = self.client.get(&url)?;
        // A non-success status is a transport-shaped failure for this walker:
        // nothing arrived that could be read as a claim about the day.
        if !response.is_success() {
            return Err(FetchError::Transport(format!(
                "{url} returned HTTP {}",
                response.status
            )));
        }
        response.json(&url)
    }
}

impl Fetcher for BiorxivFetcher {
    fn fetch(
        &self,
        request: &FetchRequest,
        on_progress: &mut dyn FnMut(Progress),
    ) -> Result<FetchOutcome, FetchError> {
        let source = HttpPageSource {
            client: self.client.clone(),
        };
        Ok(fetch_biorxiv(
            &source,
            &self.server,
            request.date,
            on_progress,
        ))
    }
}
