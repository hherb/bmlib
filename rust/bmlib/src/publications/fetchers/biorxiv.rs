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
//! A port of `bmlib/publications/fetchers/biorxiv.py`. One API serves both
//! servers, controlled by a parameter, through two [`Endpoint`]s that list
//! different populations (#341): [`Endpoint::Pubs`] (the default, registered as
//! `biorxiv` and `medrxiv`) lists preprints by the day their journal version
//! appeared, and [`Endpoint::Details`] (registered as `biorxiv-details` and
//! `medrxiv-details`) lists every version by the day it was posted. They are
//! separate registered sources rather than one source with a setting because
//! `download_days` is keyed by the source's name: a day completed under `/pubs`
//! must not read as completed for `/details`.
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
    CountingSink, FetchError, FetchOutcome, FetchRequest, FetchSink, Fetcher, HttpClient,
    PartDisposition, Progress,
};
use crate::publications::models::{FetchedRecord, FullTextSourceEntry};
use crate::pyvalue::truthy;
use crate::pyvalue::{json_type_name, python_repr, python_str, python_strip};

/// The bioRxiv endpoint the fetcher reads.
///
/// **`/pubs/`, and it was `/details/`.** On 2026-09-26 `/details/` answered
/// **HTTP 200 with a zero-byte body** for every date and server tried — eight of
/// eight combinations — while still sending `content-type: application/json`, so
/// the fetcher's JSON read failed and **every bioRxiv day errored** (#325).
/// `/pubs/` on the same host serves the same days normally and is the endpoint
/// bioRxiv's own documentation describes as *"Preprint published article detail"*.
///
/// **`/details/` came back on 2026-09-27**, found by the gated live suite
/// (`tests/live_network.rs`), which had been asserting the endpoint was dead: a run
/// earlier that day still saw it empty, and the next read 64,657 bytes of JSON.
/// Re-probed 2026-09-29, eight of eight combinations served JSON again — bioRxiv
/// and medRxiv, 2024-01-15 and 2025-06-01, twice each. So these are two
/// endpoints that both answer, and both are read: `/details/` through
/// [`Endpoint::Details`] (#341).
///
/// # This is a **population change**, not a path change, and it is a real cost
///
/// `/details/` serves the preprints **posted** on a day; `/pubs/` serves the
/// records that **pair a preprint with a publication**. Every one of the 34 records
/// `/pubs/` serves for 2024-01-15 carries a `published_doi`, and the count is the
/// published subset rather than the day's postings: measured side by side on
/// 2026-09-27, `/details/` declared **207** for that day against `/pubs/`' 34
/// (`tests/live_network.rs` records three more server-day pairs). So a
/// preprint posted today and published in six months appears under its
/// **publication** window, and a preprint that is never published may never appear
/// at all.
///
/// That is a genuine narrowing of what a sync collects, which is why `/details/`
/// is read too, through [`DETAILS_BASE_URL`] (#341). The gated live suite
/// (`BMLIB_LIVE_TESTS=1`, which no CI job sets) fails if `/details/` stops
/// declaring at least as many records as `/pubs/` for 2024-01-15, so a
/// maintainer's live run notices a third party changing the answer; a default
/// `cargo test` does not.
pub const BASE_URL: &str = "https://api.biorxiv.org/pubs";

/// bioRxiv's *"content detail"* endpoint: the versions **posted** on a day (#341).
///
/// One record per posted version, each carrying `version`, `date` (the posting
/// date), `jatsxml` and `published` (the journal DOI, or `"NA"`). Measured
/// 2026-11-13 against `/pubs`: medRxiv 2024-01-15 declares 28 against 10,
/// bioRxiv 207 against 34, and recent weekdays 55-99 medRxiv and 190-440
/// bioRxiv postings a day.
pub const DETAILS_BASE_URL: &str = "https://api.biorxiv.org/details";

/// How many days after a day has ended bioRxiv and medRxiv may still add to it.
///
/// `/pubs` pairs a preprint with its publication and learns of the publication
/// **weeks** after it appears, so a day fetched the morning after it ended is
/// nearly empty and fills in over the following weeks. Ninety days is a bound
/// rather than a measurement: longer than any observed lag, and short enough that
/// day selection's re-offering stays bounded.
pub const BIORXIV_SETTLE_DAYS: u32 = 90;

/// How many days after a day has ended `/details` may still add to it.
///
/// **A bound, not a measurement.** `/details` files a version under the date it
/// was posted, so a day should be whole soon after it ends, and the old days
/// probed hold their counts (2024-01-15 and 2025-06-01, the same on 2026-09-27
/// and 2026-11-13). How soon a day is whole was not measured (#759); a day
/// recorded too early is never offered again, and each day of margin costs one
/// re-fetch of a day per run, so the bound is generous.
pub const DETAILS_SETTLE_DAYS: u32 = 7;

/// How many records a full `/pubs` page carries.
pub const PAGE_SIZE: usize = 100;

/// How many records a full `/details` page carries: **30, not `/pubs`' 100**.
///
/// Measured 2026-11-13: bioRxiv 2026-11-04 declared 378, pages at cursors 0 to
/// 330 held 30 each and cursor 360 held 18, all distinct; cursor 100 served
/// records 100-129, so the cursor is a record offset. Taking 100 as the page
/// size reads a 30-record first page as the last, and reconciliation lets that
/// complete, with only a WARNING and a note, for any day of 60 or fewer. A page
/// longer than the size fails its day, because the next page would overlap it. `tests/live_network.rs` asserts it.
pub const DETAILS_PAGE_SIZE: usize = 30;

/// Which bioRxiv endpoint a walk reads (#341).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Endpoint {
    /// `/pubs/`: preprints by the day their journal version appeared.
    #[default]
    Pubs,
    /// `/details/`: every version, by the day it was posted.
    Details,
}

impl Endpoint {
    /// The endpoint's base URL.
    #[must_use]
    pub const fn base_url(self) -> &'static str {
        match self {
            Endpoint::Pubs => BASE_URL,
            Endpoint::Details => DETAILS_BASE_URL,
        }
    }

    /// How many records a full page of this endpoint carries.
    #[must_use]
    pub const fn page_size(self) -> usize {
        match self {
            Endpoint::Pubs => PAGE_SIZE,
            Endpoint::Details => DETAILS_PAGE_SIZE,
        }
    }

    /// The URL of one page of this endpoint.
    #[must_use]
    pub fn page_url(self, server: &str, date: &str, cursor: usize) -> String {
        format!("{}/{server}/{date}/{date}/{cursor}", self.base_url())
    }
}

/// The pause between pages, in seconds.
pub const RATE_LIMIT_SECONDS: f64 = 0.5;

/// Build the URL for one `/pubs` page; [`Endpoint::page_url`] for either.
#[must_use]
pub fn page_url(server: &str, date: &str, cursor: usize) -> String {
    Endpoint::Pubs.page_url(server, date, cursor)
}

/// The record's own version, as Python's `str(raw.get("version") or "").strip()
/// or None` reads it: a falsy value names none, and anything else is its Python
/// `str()`, stripped.
fn record_version(version: Option<&serde_json::Value>) -> Option<String> {
    let value = version.filter(|value| truthy(value))?;
    let text = python_str(value);
    let stripped = python_strip(&text);
    (!stripped.is_empty()).then(|| stripped.to_string())
}

/// The PDF URL for a preprint, from its DOI and version.
///
/// The **record's own version**, not a hard-coded `v1`: a v2+ preprint's `v1`
/// URL 404s or points at the wrong revision. A record naming none (`/pubs`, or a
/// falsy value) gets `v1`.
#[must_use]
pub fn pdf_url(server: &str, doi: &str, version: Option<&serde_json::Value>) -> String {
    let version = record_version(version).unwrap_or_else(|| "1".to_string());
    format!("https://www.{server}.org/content/{doi}v{version}.full.pdf")
}

/// Python's `raw.get(key, default)`: the value **as it stands**, a present `null`
/// included.
///
/// A key present with `null`, `""` or a non-string is not an absent key, and
/// Python's two-argument `get` returns what was sent. The port used to read
/// `text(raw, key).unwrap_or(default)`, which replaced all three with `default` —
/// storing `"biorxiv"` for a record whose `server` the source had sent as `null`.
fn get_or(raw: &serde_json::Value, key: &str, default: serde_json::Value) -> serde_json::Value {
    raw.get(key).cloned().unwrap_or(default)
}

/// Python's `raw.get(key) or ""`: a **falsy** value becomes `""`, and anything
/// else is carried **unchanged** — including a non-string, which `or` passes
/// through.
fn get_or_empty(raw: &serde_json::Value, key: &str) -> serde_json::Value {
    match raw.get(key) {
        Some(value) if truthy(value) => value.clone(),
        _ => serde_json::Value::String(String::new()),
    }
}

/// Python's `_field(raw, pubs_name, details_name)`: the `/pubs` spelling when it is
/// **truthy**, otherwise the `/details` one, and `""` when neither is present.
///
/// The truthiness is load-bearing twice over. A present but *empty* `/pubs` value
/// falls through to the `/details` name; and a present but *non-string* one is
/// returned **as it stands**, where a reader that insisted on a string would fall
/// through and answer `""` — the absent-value answer for a value that was sent.
fn field_value(raw: &serde_json::Value, pubs_name: &str, details_name: &str) -> serde_json::Value {
    match raw.get(pubs_name) {
        Some(value) if truthy(value) => value.clone(),
        _ => get_or(raw, details_name, serde_json::Value::String(String::new())),
    }
}

/// A JSON string field, or `None` for an absent, null or empty value.
fn text(raw: &serde_json::Value, key: &str) -> Option<String> {
    raw.get(key)
        .and_then(serde_json::Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// The first of `names` that holds a non-empty string.
///
/// **Two spellings because the endpoint changed**, not because either is optional:
/// `/pubs/` prefixes its preprint fields (`preprint_doi`, `preprint_title`) where
/// `/details/` did not, and the committed corpus is written in the older spelling.
/// One reader accepting both keeps that corpus meaningful.
///
/// It insists on a string, which Python's `_field` does not: a truthy non-string
/// `/pubs` value falls through here where Python keeps it. The record's own fields
/// are typed `String`, so a non-string cannot be carried there anyway; the
/// `extras`, which are JSON, go through [`field_value`] instead, which does carry
/// it.
#[must_use]
fn text_any(raw: &serde_json::Value, names: &[&str]) -> Option<String> {
    names.iter().find_map(|name| text(raw, name))
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
    let doi = text_any(raw, &["preprint_doi", "doi"]);
    let authors: Vec<String> = raw
        .get("preprint_authors")
        .or_else(|| raw.get("authors"))
        .and_then(serde_json::Value::as_str)
        .map(|s| {
            s.split(';')
                .map(str::trim)
                .filter(|a| !a.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();

    // The record's own version on both sources, so a v2 posting stored over a v1
    // row is told apart by its sources (each version's URLs differ). `None` for
    // a `/pubs` record, which names no version.
    let version = record_version(raw.get("version"));
    let mut fulltext_sources: Vec<FullTextSourceEntry> = Vec::new();
    if let Some(doi) = doi.as_deref() {
        fulltext_sources.push(FullTextSourceEntry {
            url: pdf_url(server, doi, raw.get("version")),
            format: "pdf".to_string(),
            source: server.to_string(),
            open_access: true,
            version: version.clone(),
        });
    }
    if let Some(jatsxml) = text(raw, "jatsxml") {
        fulltext_sources.push(FullTextSourceEntry {
            url: jatsxml,
            format: "xml".to_string(),
            source: server.to_string(),
            open_access: true,
            version,
        });
    }

    let mut record = FetchedRecord::new(
        text_any(raw, &["preprint_title", "title"]).unwrap_or_default(),
        server,
    );
    record.doi = doi;
    record.abstract_text = text_any(raw, &["preprint_abstract", "abstract"]);
    record.authors = authors;
    record.publication_date = text_any(raw, &["preprint_date", "date"]);
    record.is_open_access = true;
    record.fulltext_sources = fulltext_sources;
    // The five extras are ported expression for expression: `_field` for
    // `category` and `published`, `.get(k) or ""` for the two `published_*`
    // fields, and `.get(k, default)` for `server`. Each carries a `Value` rather
    // than a `String` because each of the three expressions passes a value that
    // is not always a string.
    record.extras.insert(
        "category".to_string(),
        field_value(raw, "preprint_category", "category"),
    );
    // `/pubs/` names the publication's DOI `published_doi`; `/details/` uses a bare
    // `published`. The narrower name is read first, and neither is invented when
    // both are absent — an unrecognised publication is not a publication.
    // `/details/` sends `"NA"` for a preprint with none (#341), which is no DOI,
    // so it is stored as the field's empty value.
    let published = field_value(raw, "published_doi", "published");
    record.extras.insert(
        "published".to_string(),
        if published == "NA" {
            serde_json::Value::String(String::new())
        } else {
            published
        },
    );
    // `/pubs/` files a record under the date its *publication* appeared, and names
    // the journal it appeared in. Both are read under one name, `or ""` rather
    // than `_field`, because `/details` never carried either and there is no
    // second spelling to fall back to. A truthy non-string is carried as it
    // stands, as Python's `or` carries it.
    record.extras.insert(
        "published_journal".to_string(),
        get_or_empty(raw, "published_journal"),
    );
    record.extras.insert(
        "published_date".to_string(),
        get_or_empty(raw, "published_date"),
    );
    record.extras.insert(
        "server".to_string(),
        get_or(raw, "server", serde_json::json!(server)),
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

    /// The endpoint the pages come from, which fixes the page size the walk
    /// reads a short page against. `/pubs` unless a source says otherwise.
    fn endpoint(&self) -> Endpoint {
        Endpoint::Pubs
    }
}

/// Walk every page for one day, handing each record to `sink` as it is read.
///
/// The loop is separated from the transport so that a test can present a
/// sequence of bodies — including the shapes that must be refused — without a
/// network, and so the cursor arithmetic is exercised directly.
///
/// **A record is the caller's as soon as it is read**, so a walk that fails on
/// its tenth page has already delivered nine pages and the count the caller
/// received says so; this form reports the failure as `Err`, and
/// [`fetch_biorxiv`] is the one that turns it into a failed outcome. That is the
/// whole difference between the two now — a buffer inside `Ok` used to be what
/// kept a failed day's records, and the sink makes that structural.
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
    sink: &mut dyn FetchSink,
) -> Result<FetchOutcome, FetchError> {
    let mut counted = CountingSink::new(sink);
    walk_counted(source, server, date, &mut counted)
}

/// [`walk`] over a sink that counts, so the caller of either entry point can
/// read the delivered count on the failure path too.
fn walk_counted(
    source: &dyn PageSource,
    server: &str,
    date: NaiveDate,
    sink: &mut CountingSink<'_>,
) -> Result<FetchOutcome, FetchError> {
    let date_str = date.format("%Y-%m-%d").to_string();
    let endpoint = source.endpoint();
    let page_size = endpoint.page_size();
    // The registered source's name, for the reconciliation's messages: `sync()`
    // walks `medrxiv` and `medrxiv-details` for the same day. Records keep the
    // server as their source.
    let name = match endpoint {
        Endpoint::Pubs => server.to_string(),
        Endpoint::Details => format!("{server}-details"),
    };
    let mut cursor = 0usize;
    let mut promised: Option<i64> = None;
    let mut stalled = false;

    loop {
        let data = source.page(server, &date_str, cursor)?;
        let body = read_page_body(&data, server, &date_str)?;

        // The first page that names a total fixes it; a later page does not
        // override it.
        if promised.is_none() {
            promised = body.total;
        }

        // The cursor is a record offset, so a page longer than the step overlaps
        // the next one, and every repeat would count as delivered: a stall would
        // then read as a whole day.
        if body.collection.len() > page_size {
            let endpoint_name = match endpoint {
                Endpoint::Pubs => "pubs",
                Endpoint::Details => "details",
            };
            return Err(FetchError::Malformed(format!(
                "{server} served {} records on one {endpoint_name} page for {date_str}, more \
                 than the {page_size} the walk steps by, so the next page would overlap it",
                body.collection.len()
            )));
        }

        if body.collection.is_empty() {
            // An empty page while the source's own total says records remain is
            // a walk that stopped serving them, not the end.
            stalled = promised.is_some_and(|total| sink.delivered() < total);
            break;
        }

        for raw in &body.collection {
            let record = normalize(raw, server);
            if record.doi.is_none() {
                // Every bioRxiv and medRxiv preprint has a DOI, so a record
                // without one under either spelling means the endpoint's shape
                // changed (a renamed `preprint_doi`) — and stored, it has no
                // identity to deduplicate on, so each revisit of the day would
                // insert it again. Failing the day is loud and retried; storing it
                // is neither.
                //
                // Raised **before** this record is kept, so it is neither stored
                // nor counted; the records that preceded it on the day are kept
                // (see `walk_into`), as Python has already stored them.
                return Err(FetchError::Malformed(format!(
                    "{server} served a record for {date_str} carrying no DOI under either \
                     spelling (preprint_doi, doi)"
                )));
            }
            if endpoint == Endpoint::Details && record_version(raw.get("version")).is_none() {
                // Every posting names its version. One that does not takes v1's
                // URLs, which a stored v1 row already holds, so its sources would
                // be dropped as duplicates.
                return Err(FetchError::Malformed(format!(
                    "{server} served a details record for {date_str} ({}) carrying no version",
                    record.doi.as_deref().unwrap_or_default()
                )));
            }
            sink.record(record);
        }

        sink.progress(Progress::Page {
            delivered: sink.delivered(),
            promised,
        });

        // A short page is the last page.
        if body.collection.len() < page_size {
            break;
        }

        cursor += page_size;
    }

    let verdict = reconcile_delivery(&name, &date_str, sink.delivered(), promised, stalled);
    Ok(FetchOutcome {
        record_count: sink.delivered(),
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
/// "`TransportError: connection closed`" call for opposite responses and
/// read identically without it.
///
/// [`walk`] keeps the strict form, which is what a caller that wants to handle
/// the error itself — or a test that wants to see it — should use.
pub fn fetch_biorxiv(
    source: &dyn PageSource,
    server: &str,
    date: NaiveDate,
    sink: &mut dyn FetchSink,
) -> FetchOutcome {
    // The progress fallback Python applies (`records_total or total_fetched`) is
    // a *caller-side* mapping and lives in the progress consumer, not here.
    let mut counted = CountingSink::new(sink);
    match walk_counted(source, server, date, &mut counted) {
        Ok(outcome) => outcome,
        // The records that arrived before the error are the day's delivery and
        // are already with the caller, and `sync()` stores a failed day's buffer
        // as Python stores its `on_record` calls — so a failure on page 2 does
        // not un-deliver page 1. `record_count` is that delivery, read from the
        // counter `walk_counted` was walking through.
        Err(error) => FetchOutcome {
            record_count: counted.delivered(),
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
/// same thing. [`FetchError::Transport`] carries why the *base* class is the
/// answer for a transport failure (#361).
fn error_type_name(error: &FetchError) -> &'static str {
    match error {
        FetchError::Transport(_) => "TransportError",
        FetchError::HttpStatus { .. } => "HTTPStatusError",
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
    /// The endpoint read.
    pub endpoint: Endpoint,
}

impl BiorxivFetcher {
    /// A fetcher for one of the two servers, reading `/pubs`.
    #[must_use]
    pub fn new(client: std::sync::Arc<dyn HttpClient + Send + Sync>, server: &str) -> Self {
        BiorxivFetcher {
            client,
            server: server.to_string(),
            endpoint: Endpoint::Pubs,
        }
    }

    /// The same fetcher reading `endpoint` instead.
    #[must_use]
    pub fn with_endpoint(mut self, endpoint: Endpoint) -> Self {
        self.endpoint = endpoint;
        self
    }
}

/// A [`PageSource`] over an [`HttpClient`].
pub struct HttpPageSource {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
    /// The endpoint the pages are asked of.
    pub endpoint: Endpoint,
}

impl PageSource for HttpPageSource {
    fn page(
        &self,
        server: &str,
        date: &str,
        cursor: usize,
    ) -> Result<serde_json::Value, FetchError> {
        let url = self.endpoint.page_url(server, date, cursor);
        let response = self.client.get(&url)?;
        // A non-success status is the answer the source gave, not a failed
        // request, so it is refused as `HttpStatus` — Python's
        // `HTTPStatusError` — and never as `Transport`.
        if !response.is_success() {
            return Err(FetchError::HttpStatus {
                url,
                status: response.status,
            });
        }
        response.json(&url)
    }

    fn endpoint(&self) -> Endpoint {
        self.endpoint
    }
}

impl Fetcher for BiorxivFetcher {
    fn fetch(
        &self,
        request: &FetchRequest,
        sink: &mut dyn FetchSink,
    ) -> Result<FetchOutcome, FetchError> {
        let source = HttpPageSource {
            client: self.client.clone(),
            endpoint: self.endpoint,
        };
        Ok(fetch_biorxiv(&source, &self.server, request.date, sink))
    }
}
