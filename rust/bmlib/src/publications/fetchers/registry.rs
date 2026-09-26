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

//! Source fetcher registry.
//!
//! A port of `bmlib/publications/fetchers/registry.py`.
//!
//! Fetchers are registered by source name; new ones can be registered at
//! runtime. All share a uniform calling convention, which in Python is a
//! documented keyword protocol and here is a trait.
//!
//! # The resume-keyword check is the type system's job
//!
//! Python's `_check_accepts_resume_keywords` introspects a fetcher's signature
//! and refuses a `resumable=True` registration whose callable accepts neither
//! `**kwargs` nor the three resume keywords. The reason is concrete: `sync`
//! reads the *descriptor* to decide whether to pass those keywords, so a
//! descriptor declaring more than its fetcher accepts raises `TypeError` inside
//! the per-day handler — recording that day `failed`, **on every day of the
//! range, on every later run**. Loud, but once per day rather than once per
//! mistake, and at a place that names the day instead of the registration.
//!
//! In Rust that failure cannot be expressed. [`FetchRequest`] carries the resume
//! state as an `Option`, and the descriptor's `resumable` flag says whether the
//! *sync loop* fills it in. A fetcher that ignores it is a fetcher that ignores
//! an `Option` — not one that raises on an unexpected keyword. So the check has
//! no counterpart, and the guarantee is stronger: the mistake is unrepresentable
//! rather than caught.
//!
//! What the flag still decides is behaviour, so it is not decoration: a
//! `resumable` source is handed `completed_parts` and may answer a part with
//! [`PartDisposition::Skipped`], which is what makes an interrupted partitioned
//! day resume rather than restart.

use std::collections::BTreeMap;

use chrono::NaiveDate;

use crate::publications::models::{
    FetchResult, FetchedRecord, PartCheckpoint, SourceDescriptor, SourceParam,
};
use crate::publications::sync::LoadPartsError;

/// The resume state a partitioned day may carry.
///
/// Present only for a source whose descriptor says `resumable`; absent
/// otherwise, which is what stops a third-party fetcher written against an
/// earlier bmlib from being handed something it does not expect.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ResumeState {
    /// Parts a previous run finished, keyed by part key.
    pub completed_parts: BTreeMap<String, PartCheckpoint>,
}

/// What a fetcher reported about one part.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PartDisposition {
    /// The part was walked and its records delivered.
    Completed {
        /// The checkpoint describing the part.
        checkpoint: PartCheckpoint,
    },
    /// The part matched a stored checkpoint and was skipped.
    Skipped {
        /// The part key that matched.
        part_key: String,
    },
}

/// Something a fetcher reports back while it runs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Progress {
    /// A page was fetched.
    Page {
        /// How many records the walk has delivered so far.
        delivered: i64,
        /// The count the source promised, when it named one.
        promised: Option<i64>,
    },
    /// A part finished.
    PartFinished(PartDisposition),
}

/// Everything a fetcher is given for one day.
#[derive(Debug, Clone)]
pub struct FetchRequest {
    /// The day being fetched.
    pub date: NaiveDate,
    /// The source's own configuration, as the caller supplied it.
    pub config: BTreeMap<String, String>,
    /// Resume state, for a resumable source that has one.
    pub resume: Option<ResumeState>,
}

impl FetchRequest {
    /// A request for a day with no resume state.
    #[must_use]
    pub fn new(date: NaiveDate) -> Self {
        FetchRequest {
            date,
            config: BTreeMap::new(),
            resume: None,
        }
    }

    /// Read a configuration value.
    #[must_use]
    pub fn config_value(&self, key: &str) -> Option<&str> {
        self.config.get(key).map(String::as_str)
    }
}

/// What a fetcher produced for one day.
#[derive(Debug, Clone, PartialEq)]
pub struct FetchOutcome {
    /// The records the source delivered, in order.
    ///
    /// **Every** record the server handed over, not only the ones the day's
    /// store accepts: reconciliation compares this against the promise, and
    /// counting parsed records instead would report a phantom shortfall on
    /// every day carrying a PubMed book chapter.
    pub records: Vec<FetchedRecord>,
    /// The fetcher's own status word.
    pub status: String,
    /// The count the source promised, when it named one.
    pub promised: Option<i64>,
    /// Whether a page delivered nothing while the promise was unmet.
    pub stalled: bool,
    /// An error message, when the walk failed outright.
    pub error: Option<String>,
    /// Something the caller should know about a day that still completed.
    pub note: Option<String>,
    /// How many parts the walk completed or skipped, in order.
    pub parts: Vec<PartDisposition>,
}

impl FetchOutcome {
    /// A completed walk that promised nothing.
    #[must_use]
    pub fn completed(records: Vec<FetchedRecord>) -> Self {
        FetchOutcome {
            records,
            status: "completed".to_string(),
            promised: None,
            stalled: false,
            error: None,
            note: None,
            parts: Vec::new(),
        }
    }

    /// A failed walk.
    #[must_use]
    pub fn failed(message: impl Into<String>) -> Self {
        FetchOutcome {
            records: Vec::new(),
            status: "failed".to_string(),
            promised: None,
            stalled: false,
            error: Some(message.into()),
            note: None,
            parts: Vec::new(),
        }
    }

    /// This outcome as a [`FetchResult`] for the day record.
    #[must_use]
    pub fn to_fetch_result(&self, source: &str, date: NaiveDate) -> FetchResult {
        FetchResult {
            source: source.to_string(),
            date: date.format("%Y-%m-%d").to_string(),
            record_count: self.records.len() as i64,
            status: self.status.clone(),
            error: self.error.clone(),
            note: self.note.clone(),
        }
    }
}

/// Why a fetcher could not run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FetchError {
    /// The transport failed.
    Transport(String),
    /// The response could not be read as the shape the source documents.
    Malformed(String),
    /// The source's own configuration is incomplete or wrong.
    Config(String),
    /// A stored checkpoint could not be read, so the day cannot be resumed.
    ResumeUnreadable(String),
}

impl std::fmt::Display for FetchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FetchError::Transport(m) => write!(f, "{m}"),
            FetchError::Malformed(m) => write!(f, "{m}"),
            FetchError::Config(m) => write!(f, "{m}"),
            FetchError::ResumeUnreadable(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for FetchError {}

impl From<LoadPartsError> for FetchError {
    fn from(e: LoadPartsError) -> Self {
        FetchError::ResumeUnreadable(e.to_string())
    }
}

/// The HTTP transport a walker needs.
///
/// One method, and a **response object** rather than a string, because the
/// status code is what separates "the source answered, and the answer is not
/// what we wanted" from "the request never arrived" — and the three source
/// walkers all branch on it.
///
/// This is the boundary a test replaces. The Python fetchers take any object
/// with `get`/`raise_for_status`/`json`, and their tests pass a fake; a trait is
/// the same seam with the shape written down.
pub trait HttpClient {
    /// Issue a GET request.
    ///
    /// # Errors
    ///
    /// [`FetchError::Transport`] when the request never completed. A response
    /// with a non-success status is **not** an error here — it is a
    /// [`HttpResponse`], because what a walker does with a 404 or a 429 differs
    /// from what it does with no connection at all.
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError>;

    /// Issue a JSON POST.
    ///
    /// The default **refuses**, naming the method, rather than silently issuing a
    /// GET: a client that only serves the read-only fetchers should say so, and a
    /// GET to an endpoint expecting a POST returns a confusing 405 or 404 that
    /// reads as a wrong URL.
    ///
    /// # Errors
    ///
    /// [`FetchError::Transport`] when the request never completed — the same
    /// contract as [`HttpClient::get`], and for the same reason: a non-success
    /// status is a [`HttpResponse`] for the caller to read, not an error here.
    fn post_json(
        &self,
        url: &str,
        _body: &serde_json::Value,
        _headers: &std::collections::BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(format!(
            "this HttpClient does not implement post_json (POST {url})"
        )))
    }
}

/// A response body and its status.
///
/// # Why the body is bytes
///
/// **A `String` cannot carry a PDF.** This type used to hold a body already
/// decoded through a lossy UTF-8 read, so the PDFs the full-text service
/// downloads (tiers 1d and 2) reached the disk cache with every non-UTF-8 byte
/// replaced by U+FFFD — and nothing detected it. U+FFFD is valid UTF-8, a PDF's
/// `%PDF` magic prefix is ASCII and survives, and the cache's only check was
/// that prefix, so a corrupt file was written and read back as a healthy one.
/// The arithmetic is exact: the 273 bytes of `%PDF-1.4\n` followed by `0..=255`
/// become 529, and are not byte-identical to what the server sent.
///
/// Holding the bytes the server sent makes that corruption unrepresentable.
/// A **lossy** read is exactly what this type exists to stop happening a second
/// time, so [`HttpResponse::text`] is strict and refuses a body that is not
/// valid UTF-8; a caller that genuinely does not care uses
/// [`HttpResponse::text_or_empty`].
#[derive(Debug, Clone, PartialEq)]
pub struct HttpResponse {
    /// The HTTP status code.
    pub status: u16,
    /// The body, exactly as the server sent it.
    pub body: Vec<u8>,
}

impl HttpResponse {
    /// A `200 OK` carrying `body` as raw bytes.
    #[must_use]
    pub fn ok(body: impl Into<Vec<u8>>) -> Self {
        HttpResponse {
            status: 200,
            body: body.into(),
        }
    }

    /// A `200 OK` carrying a text body.
    ///
    /// [`HttpResponse::ok`] already accepts a `&str` or a `String`, but a text
    /// caller reading as a text caller is worth the second name: the byte
    /// constructor is the one a PDF takes.
    #[must_use]
    pub fn ok_text(body: impl Into<String>) -> Self {
        HttpResponse {
            status: 200,
            body: body.into().into_bytes(),
        }
    }

    /// A response with an explicit status and raw bytes.
    #[must_use]
    pub fn from_bytes(status: u16, body: Vec<u8>) -> Self {
        HttpResponse { status, body }
    }

    /// The body as **strict** UTF-8.
    ///
    /// # Errors
    ///
    /// [`FetchError::Transport`] when the body is not valid UTF-8. Strict and
    /// not lossy, deliberately: a lossy read is the silent corruption the byte
    /// body exists to prevent, and a caller cannot tell a server's real U+FFFD
    /// from a substituted one.
    pub fn text(&self) -> Result<&str, FetchError> {
        std::str::from_utf8(&self.body)
            .map_err(|_| FetchError::Transport("the body is not valid UTF-8".to_string()))
    }

    /// The body as text, or `""` when it is not valid UTF-8.
    ///
    /// For a caller that genuinely does not care — a human-readable diagnostic
    /// that is never parsed as data — and nowhere else. It is not a softer
    /// [`HttpResponse::text`]: an undecodable body reads as *empty*, which is a
    /// missing answer rather than a mangled one.
    #[must_use]
    pub fn text_or_empty(&self) -> &str {
        std::str::from_utf8(&self.body).unwrap_or("")
    }

    /// Whether the status is a success.
    #[must_use]
    pub fn is_success(&self) -> bool {
        (200..300).contains(&self.status)
    }

    /// The body parsed as JSON.
    ///
    /// # Errors
    ///
    /// [`FetchError::Transport`] when the body is not valid UTF-8 — JSON is
    /// required to be UTF-8, so such a body is not JSON either — and
    /// [`FetchError::Malformed`] naming the URL when it will not parse, because
    /// that is a claim about the source and not about the caller.
    pub fn json(&self, url: &str) -> Result<serde_json::Value, FetchError> {
        let text = self.text()?;
        serde_json::from_str(text).map_err(|e| {
            FetchError::Malformed(format!("{url} returned a body that is not JSON: {e}"))
        })
    }
}

/// A source fetcher.
///
/// One method, because that is the whole convention: fetch one day, report what
/// arrived, and report the promise so the walk can be reconciled against it.
pub trait Fetcher: Send + Sync {
    /// Fetch one day, reporting progress through `on_progress`.
    ///
    /// # Errors
    ///
    /// [`FetchError`] when the walk could not be performed. A walk that ran but
    /// delivered too little is **not** an error here — it is an
    /// [`FetchOutcome`] whose `status` is `failed`, because the day was
    /// genuinely attempted and the reconciliation is what judges it.
    /// Fetch one day.
    ///
    /// `on_progress` reports what the walk is doing, including the part
    /// boundaries a resumable source produces. The records come back in
    /// [`FetchOutcome::records`] rather than through a callback — see
    /// `sync_source`'s note on what that costs, and why it is deferred.
    ///
    /// # Errors
    ///
    /// When the source's answer cannot be used.
    fn fetch(
        &self,
        request: &FetchRequest,
        on_progress: &mut dyn FnMut(Progress),
    ) -> Result<FetchOutcome, FetchError>;
}

/// The registry's refusal to resolve a name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownSource {
    /// The name asked for.
    pub name: String,
    /// The names that are registered, sorted.
    pub available: Vec<String>,
}

impl std::fmt::Display for UnknownSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Unknown source {:?}. Available: {:?}",
            self.name, self.available
        )
    }
}

impl std::error::Error for UnknownSource {}

/// The source registry.
///
/// A struct rather than a module-level global, which is the one deliberate
/// shape change from Python. A process-wide mutable registry is a global that
/// tests must share and order around; here each caller owns one, and a test
/// states exactly what is registered.
#[derive(Default)]
pub struct Registry {
    entries: BTreeMap<String, Entry>,
}

struct Entry {
    descriptor: SourceDescriptor,
    fetcher: Box<dyn Fetcher>,
}

impl std::fmt::Debug for Registry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Registry")
            .field("sources", &self.source_names())
            .finish()
    }
}

impl Registry {
    /// An empty registry.
    #[must_use]
    pub fn new() -> Self {
        Registry::default()
    }

    /// Register a fetcher, replacing any entry under the same name.
    ///
    /// Replacing rather than refusing is the documented behaviour: registering
    /// under a built-in name overrides it.
    pub fn register(&mut self, descriptor: SourceDescriptor, fetcher: Box<dyn Fetcher>) {
        self.entries.insert(
            descriptor.name.clone(),
            Entry {
                descriptor,
                fetcher,
            },
        );
    }

    /// Descriptors for every registered source, in name order.
    #[must_use]
    pub fn list_sources(&self) -> Vec<SourceDescriptor> {
        self.entries
            .values()
            .map(|e| e.descriptor.clone())
            .collect()
    }

    /// Names of every registered source, in name order.
    #[must_use]
    pub fn source_names(&self) -> Vec<String> {
        self.entries.keys().cloned().collect()
    }

    /// The descriptor for a source.
    ///
    /// # Errors
    ///
    /// [`UnknownSource`], naming what is registered.
    pub fn descriptor(&self, name: &str) -> Result<&SourceDescriptor, UnknownSource> {
        self.entries
            .get(name)
            .map(|e| &e.descriptor)
            .ok_or_else(|| UnknownSource {
                name: name.to_string(),
                available: self.source_names(),
            })
    }

    /// The fetcher for a source.
    ///
    /// # Errors
    ///
    /// [`UnknownSource`], naming what is registered.
    pub fn fetcher(&self, name: &str) -> Result<&dyn Fetcher, UnknownSource> {
        self.entries
            .get(name)
            .map(|e| e.fetcher.as_ref())
            .ok_or_else(|| UnknownSource {
                name: name.to_string(),
                available: self.source_names(),
            })
    }

    /// Whether a source is registered.
    #[must_use]
    pub fn contains(&self, name: &str) -> bool {
        self.entries.contains_key(name)
    }

    /// Whether a source's descriptor declares it accepts resume state.
    ///
    /// An **unknown** source answers `false` rather than raising, matching
    /// Python: a source supplied through an override need not be registered, so
    /// `descriptor` would raise for a source that nonetheless has a working
    /// fetcher — and that raise would escape the per-day handler into a
    /// cleanup-only block and lose the whole multi-source run's report.
    #[must_use]
    pub fn is_resumable(&self, name: &str) -> bool {
        self.entries
            .get(name)
            .is_some_and(|e| e.descriptor.resumable)
    }
}

/// The descriptors for the built-in sources.
///
/// Kept separate from the fetchers so the metadata can be inspected — and
/// diffed against Python — without a network client in hand.
#[must_use]
pub fn builtin_descriptors() -> Vec<SourceDescriptor> {
    vec![
        SourceDescriptor {
            name: "pubmed".to_string(),
            display_name: "PubMed".to_string(),
            description: "NCBI PubMed biomedical literature database".to_string(),
            params: vec![SourceParam {
                name: "api_key".to_string(),
                description: "NCBI API key for higher rate limits".to_string(),
                required: false,
                default: None,
                secret: true,
            }],
            resumable: true,
        },
        SourceDescriptor {
            name: "biorxiv".to_string(),
            display_name: "bioRxiv".to_string(),
            description: "Preprint server for biology".to_string(),
            params: vec![SourceParam {
                name: "api_key".to_string(),
                description: "API key (reserved)".to_string(),
                required: false,
                default: None,
                secret: true,
            }],
            resumable: false,
        },
        SourceDescriptor {
            name: "medrxiv".to_string(),
            display_name: "medRxiv".to_string(),
            description: "Preprint server for health sciences".to_string(),
            params: vec![SourceParam {
                name: "api_key".to_string(),
                description: "API key (reserved)".to_string(),
                required: false,
                default: None,
                secret: true,
            }],
            resumable: false,
        },
        SourceDescriptor {
            name: "openalex".to_string(),
            display_name: "OpenAlex".to_string(),
            description: "Open catalog of scholarly works, authors, and institutions".to_string(),
            params: vec![
                SourceParam {
                    name: "email".to_string(),
                    description: "Contact email for polite API access".to_string(),
                    required: true,
                    default: None,
                    secret: false,
                },
                SourceParam {
                    name: "api_key".to_string(),
                    description: "OpenAlex API key for premium access".to_string(),
                    required: false,
                    default: None,
                    secret: true,
                },
            ],
            resumable: false,
        },
    ]
}

/// A registry with only the four built-in descriptors and no fetchers.
///
/// The fetchers land in the rounds that port them; until then a source is
/// *described* but not *fetchable*, which is the honest state and is what
/// [`Registry::descriptor`] can serve.
#[must_use]
pub fn descriptors_only() -> Vec<SourceDescriptor> {
    builtin_descriptors()
}
