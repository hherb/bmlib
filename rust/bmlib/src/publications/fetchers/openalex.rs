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

//! OpenAlex fetcher — publication records from the OpenAlex API.
//!
//! A port of `bmlib/publications/fetchers/openalex.py`. Cursor-based pagination
//! over all works published on a given date.
//!
//! # Where the failures are placed matters as much as that they are caught
//!
//! Three guards here are about **which layer reports the failure**, and getting
//! it wrong sends an operator to the wrong place:
//!
//! - A **malformed body** is a failure of *this HTTP call*. Decoding it outside
//!   the guard let the exception escape the fetcher, and `sync` logged it as
//!   "Fetcher raised" — pointing at the fetcher's contract rather than at the
//!   response (#91).
//! - A **results list whose members are not work objects** is *this page*
//!   failing, for the same reason and the same wrong layer.
//! - OpenAlex answers an **invalid query with an `{"error": ...}` body at HTTP
//!   200.** Read through `.get()` defaults, such a body is indistinguishable
//!   from a day with no works — which is how a rejected query came to be stored
//!   as a completed day (#88). The envelope is checked rather than defaulted, in
//!   the order that lets a page's valid records still be emitted before the page
//!   is refused.
//!
//! # And one that the shortfall floor cannot catch
//!
//! An **empty page while `meta.count` says works remain** is a walk that stopped
//! serving them — the late-page death the floor misses, since 600 of 1,000
//! clears it. Without the `stalled` flag OpenAlex reached `reconcile_delivery`
//! judged by the floor alone. Breaking on an empty page also *bounds the loop*:
//! a page carrying no results and a non-null `next_cursor` otherwise repeats for
//! ever.

use std::collections::BTreeMap;

use chrono::NaiveDate;
use serde_json::Value;

use crate::publications::fetchers::reconcile::reconcile_delivery;
use crate::publications::fetchers::registry::{
    FetchError, FetchOutcome, FetchRequest, Fetcher, HttpClient, Progress,
};
use crate::publications::models::FetchedRecord;

/// The OpenAlex works endpoint.
pub const API_URL: &str = "https://api.openalex.org/works";

/// How many works a page carries.
pub const PER_PAGE: usize = 200;

/// The pause between pages, in seconds.
pub const RATE_LIMIT_SECONDS: f64 = 0.1;

/// The DOI URL prefix OpenAlex writes.
pub const DOI_PREFIX: &str = "https://doi.org/";

/// The PubMed URL prefix OpenAlex writes.
pub const PMID_PREFIX: &str = "https://pubmed.ncbi.nlm.nih.gov/";

/// OpenAlex's location-version vocabulary → the port's own.
///
/// A value outside the map is passed through unchanged rather than dropped: the
/// vocabulary belongs to OpenAlex, and a version this port does not know is
/// still information a caller can read.
#[must_use]
pub fn version_map() -> BTreeMap<&'static str, &'static str> {
    BTreeMap::from([
        ("publishedVersion", "published"),
        ("acceptedVersion", "accepted"),
        ("submittedVersion", "preprint"),
    ])
}

/// Reconstruct an abstract from OpenAlex's inverted-index representation.
///
/// OpenAlex stores abstracts as `{"word": [pos, ...], ...}`. This flattens into
/// `(position, word)` pairs, sorts by position, and joins.
///
/// Returns `None` when the index is absent or empty — **not** an empty string,
/// so the storage layer's `COALESCE` merge can still fill the field from another
/// source later.
#[must_use]
pub fn reconstruct_abstract(inverted_index: Option<&Value>) -> Option<String> {
    let index = inverted_index?.as_object()?;
    if index.is_empty() {
        return None;
    }

    let mut pairs: Vec<(i64, &str)> = Vec::new();
    for (word, positions) in index {
        let Some(positions) = positions.as_array() else {
            continue;
        };
        for pos in positions {
            if let Some(p) = pos.as_i64() {
                pairs.push((p, word.as_str()));
            }
        }
    }
    if pairs.is_empty() {
        return None;
    }

    // Python's `sort` on `(position, word)` tuples is by position then word, and
    // a *stable* sort by position alone would keep the dict's order for a tie.
    // Sorting on both reproduces Python's ordering exactly.
    pairs.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(b.1)));
    Some(
        pairs
            .into_iter()
            .map(|(_, word)| word)
            .collect::<Vec<_>>()
            .join(" "),
    )
}

/// A JSON string field, or `None` for absent, null or empty.
fn text(raw: &Value, key: &str) -> Option<String> {
    raw.get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn nested<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.get(key).filter(|v| !v.is_null())
}

/// Convert a raw OpenAlex work record to a [`FetchedRecord`].
///
/// Returns `None` when the record is not an object — Python catches the
/// resulting `AttributeError` at the page level and reports the whole page as
/// unnormalisable, which is the layer that owns the failure.
#[must_use]
pub fn normalize(raw: &Value) -> Option<FetchedRecord> {
    let object = raw.as_object()?;

    // DOI — strip the prefix.
    let doi = text(raw, "doi")
        .map(|d| d.strip_prefix(DOI_PREFIX).unwrap_or(&d).to_string())
        .filter(|d| !d.is_empty());

    // PMID — extract from the ids object.
    let pmid = nested(raw, "ids")
        .and_then(|ids| text(ids, "pmid"))
        .map(|p| p.strip_prefix(PMID_PREFIX).unwrap_or(&p).to_string())
        .filter(|p| !p.is_empty());

    // Authors, in order.
    let mut authors: Vec<String> = Vec::new();
    if let Some(authorships) = raw.get("authorships").and_then(Value::as_array) {
        for authorship in authorships {
            if let Some(name) = nested(authorship, "author").and_then(|a| text(a, "display_name")) {
                authors.push(name);
            }
        }
    }

    // Journal, from the primary location's source.
    let journal = nested(raw, "primary_location")
        .and_then(|loc| nested(loc, "source"))
        .and_then(|source| text(source, "display_name"));

    let abstract_text = reconstruct_abstract(raw.get("abstract_inverted_index"));

    // Keywords — the primary topic's display name.
    let mut keywords: Vec<String> = Vec::new();
    if let Some(topic) = nested(raw, "primary_topic").and_then(|t| text(t, "display_name")) {
        keywords.push(topic);
    }

    let is_open_access = nested(raw, "open_access")
        .and_then(|oa| oa.get("is_oa"))
        .and_then(Value::as_bool)
        .unwrap_or(false);

    // A plain `.get()`, not `text()`: Python reads `raw.get("license")`, so a
    // **present empty string survives as `""`** where absent is `None`. The
    // usual empty-means-absent rule does not apply to this field, and folding
    // it in diverges on `{"license": ""}`.
    let license = raw
        .get("license")
        .and_then(Value::as_str)
        .map(str::to_string);

    let publication_types: Vec<String> = text(raw, "type").into_iter().collect();

    // Full-text sources, one or two per location.
    let mut fulltext_sources: Vec<Value> = Vec::new();
    if let Some(locations) = raw.get("locations").and_then(Value::as_array) {
        for location in locations {
            let loc_source = nested(location, "source")
                .and_then(|s| text(s, "display_name"))
                .unwrap_or_else(|| "unknown".to_string());
            let version_raw = text(location, "version");
            let version = version_raw.map(|v| {
                version_map()
                    .get(v.as_str())
                    .map_or(v.clone(), |mapped| (*mapped).to_string())
            });
            let loc_is_oa = location
                .get("is_oa")
                .and_then(Value::as_bool)
                .unwrap_or(false);

            let mut entry = |url: String, format: &str| {
                let mut source = serde_json::json!({
                    "url": url,
                    "format": format,
                    "source": loc_source,
                    "open_access": loc_is_oa,
                });
                // Python's `FullTextSourceEntry.to_dict` omits `version` when it
                // is unset, so an absent version is an absent key.
                if let Some(version) = version.as_ref() {
                    source["version"] = serde_json::json!(version);
                }
                fulltext_sources.push(source);
            };

            if let Some(landing) = text(location, "landing_page_url") {
                entry(landing, "html");
            }
            if let Some(pdf) = text(location, "pdf_url") {
                entry(pdf, "pdf");
            }
        }
    }

    // `object` was only needed for the is-object check; the rest reads through
    // `raw`, which is the same value.
    let _ = object;

    let mut record = FetchedRecord::new(text(raw, "title").unwrap_or_default(), "openalex");
    record.doi = doi;
    record.pmid = pmid;
    record.abstract_text = abstract_text;
    record.authors = authors;
    record.journal = journal;
    record.publication_date = text(raw, "publication_date");
    record.keywords = keywords;
    record.publication_types = publication_types;
    record.is_open_access = is_open_access;
    record.license = license;
    record.fulltext_sources = fulltext_sources;
    Some(record)
}

/// The query parameters for one page.
#[must_use]
pub fn page_params(
    date_str: &str,
    cursor: &str,
    email: &str,
    api_key: Option<&str>,
) -> Vec<(String, String)> {
    let mut params = vec![
        (
            "filter".to_string(),
            format!("from_publication_date:{date_str},to_publication_date:{date_str}"),
        ),
        ("per_page".to_string(), PER_PAGE.to_string()),
        ("cursor".to_string(), cursor.to_string()),
        ("mailto".to_string(), email.to_string()),
    ];
    if let Some(key) = api_key {
        params.push(("api_key".to_string(), key.to_string()));
    }
    params
}

/// One page of the walk, so the walk is testable without a socket.
pub trait CursorPages {
    /// Fetch one page.
    ///
    /// # Errors
    ///
    /// [`FetchError::Transport`] when the request never completed.
    fn page(
        &self,
        date: &str,
        cursor: &str,
        email: &str,
        api_key: Option<&str>,
    ) -> Result<Value, FetchError>;
}

/// A failed outcome that keeps whatever the walk delivered.
///
/// The count is `records.len()` and not zero: those records were already handed
/// to the caller and will be stored, and the day is retried regardless. Carrying
/// the records rather than a separate count is what makes the two impossible to
/// disagree.
fn failed(message: String, records: Vec<FetchedRecord>) -> FetchOutcome {
    FetchOutcome {
        records,
        status: "failed".to_string(),
        promised: None,
        stalled: false,
        error: Some(message),
        note: None,
        parts: Vec::new(),
    }
}

/// Walk every page for one day.
///
/// `email` is the polite-pool contact; `api_key` is optional premium access.
/// `on_progress` is called after each page that carried results.
#[must_use]
pub fn walk(
    source: &dyn CursorPages,
    date: NaiveDate,
    email: &str,
    api_key: Option<&str>,
    on_progress: &mut dyn FnMut(Progress),
) -> FetchOutcome {
    let date_str = date.format("%Y-%m-%d").to_string();
    // `"*"` only seeds the first page; the loop exits on the `None` the last
    // page's `next_cursor` returns.
    let mut cursor: Option<String> = Some("*".to_string());
    let mut delivered = 0i64;
    let mut promised = 0i64;
    let mut is_first_page = true;
    let mut stalled = false;
    let mut records: Vec<FetchedRecord> = Vec::new();

    while let Some(current) = cursor {
        let data = match source.page(&date_str, &current, email, api_key) {
            Ok(data) => data,
            Err(error) => {
                // `str(OSError())` is the empty string, which reads downstream as
                // "no error" and is dropped from the report entirely — so a day
                // that keeps failing does so with nothing said about why.
                return failed(format!("{}: {error}", error_type_name(&error)), records);
            }
        };

        if !data.is_object() {
            return failed(
                format!(
                    "OpenAlex returned a {} payload, not an object, for {date_str}",
                    json_type_name(&data)
                ),
                records,
            );
        }

        let Some(results) = data.get("results").and_then(Value::as_array) else {
            return failed(
                format!("OpenAlex returned a page carrying no results list for {date_str}"),
                records,
            );
        };

        // A results list whose members are not work objects is *this page*
        // failing, reported here rather than escaping as "Fetcher raised".
        for raw in results {
            let Some(normalised) = normalize(raw) else {
                return failed(
                    // Python's message is whatever `_normalize` raised, and for
                    // a non-object member that is an `AttributeError` naming
                    // the receiver — `'int' object has no attribute 'get'`.
                    // Reproduced because it is read beside the Python
                    // implementation's, and the type is what separates a
                    // malformed payload from a bmlib defect.
                    format!(
                        "OpenAlex returned a page bmlib could not normalise for {date_str}: \
                         AttributeError: '{}' object has no attribute 'get'",
                        json_type_name(raw)
                    ),
                    records,
                );
            };
            records.push(normalised);
            delivered += 1;
        }

        let Some(meta) = data.get("meta").filter(|m| m.is_object()) else {
            return failed(
                format!("OpenAlex returned a page carrying no meta object for {date_str}"),
                records,
            );
        };

        if is_first_page {
            // A `bool` is an `int` in Python, but a count is never sent as one.
            //
            // **Corrected from Python** (#313), which reaches for
            // `isinstance(meta.get("count"), int)` — and a `bool` passes that,
            // so `True` was accepted as a count and carried into the
            // reconciliation as `promised`, reaching a caller-facing message as
            // the literal `True`. The comment beside it already stated the rule
            // the check failed to enforce.
            //
            // The library has the right reader next door:
            // `transparency.analyzer._json_count` excludes `bool` **by name**
            // ("`bool` is excluded although it is an `int` in Python"), and
            // `CHANGELOG.md` records that as a fixed defect. This port applies
            // the same rule here so the two count readers agree.
            let Some(count) = meta
                .get("count")
                .filter(|v| !v.is_boolean())
                .and_then(Value::as_i64)
            else {
                return failed(
                    format!(
                        "OpenAlex returned a page whose meta carries no numeric count \
                         for {date_str}"
                    ),
                    records,
                );
            };
            promised = count;
            is_first_page = false;
        }

        if results.is_empty() {
            // An empty page while `meta.count` says works remain is a walk that
            // stopped serving them — the late-page death the shortfall floor
            // cannot catch. Breaking here also bounds the loop: no results and a
            // non-null `next_cursor` would otherwise repeat for ever.
            stalled = delivered < promised;
            break;
        }

        on_progress(Progress::Page {
            delivered,
            promised: Some(promised),
        });

        cursor = meta
            .get("next_cursor")
            .filter(|v| !v.is_null())
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            });
    }

    // `delivered` is what the server handed over. That is the record count only
    // because `normalize` skips nothing — the moment this loop grows a "skip
    // this kind of work" branch, it must count list members instead, or it
    // reports PubMed's phantom shortfall.
    let verdict = reconcile_delivery("openalex", &date_str, delivered, Some(promised), stalled);
    FetchOutcome {
        records,
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

fn error_type_name(error: &FetchError) -> &'static str {
    match error {
        FetchError::Transport(_) => "RemoteProtocolError",
        FetchError::Malformed(_) => "ValueError",
        FetchError::Config(_) => "ValueError",
        FetchError::ResumeUnreadable(_) => "ValueError",
    }
}

fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// A [`CursorPages`] over an [`HttpClient`].
pub struct HttpCursorPages {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
}

impl CursorPages for HttpCursorPages {
    fn page(
        &self,
        date: &str,
        cursor: &str,
        email: &str,
        api_key: Option<&str>,
    ) -> Result<Value, FetchError> {
        let query: Vec<String> = page_params(date, cursor, email, api_key)
            .into_iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect();
        let url = format!("{API_URL}?{}", query.join("&"));
        let response = self.client.get(&url)?;
        if !response.is_success() {
            return Err(FetchError::Transport(format!(
                "{url} returned HTTP {}",
                response.status
            )));
        }
        response.json(&url)
    }
}

/// The OpenAlex fetcher.
pub struct OpenAlexFetcher {
    /// The transport.
    pub client: std::sync::Arc<dyn HttpClient + Send + Sync>,
}

impl Fetcher for OpenAlexFetcher {
    fn fetch(
        &self,
        request: &FetchRequest,
        on_progress: &mut dyn FnMut(Progress),
    ) -> Result<FetchOutcome, FetchError> {
        let email = request.config_value("email").unwrap_or_default();
        if email.is_empty() {
            return Err(FetchError::Config(
                "OpenAlex requires an email for polite API access".to_string(),
            ));
        }
        let source = HttpCursorPages {
            client: self.client.clone(),
        };
        Ok(walk(
            &source,
            request.date,
            email,
            request.config_value("api_key"),
            on_progress,
        ))
    }
}
