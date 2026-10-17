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

//! UniFFI bindings over [`bmlib`] for the Swift (iOS/macOS) and Kotlin
//! (Android) apps of bmlibrarian_lite. **SPIKE** — see `README.md`.
//!
//! The FFI surface is a set of *owned copies* of bmlib's models rather than the
//! models themselves: UniFFI records must derive `uniffi::Record`, and bmlib's
//! public structs should not grow an FFI derive (nor a dependency on uniffi)
//! for one consumer. The `From` impls below are the whole mapping, field for
//! field, so a field bmlib adds is a compile-visible omission here rather
//! than a silent one — every struct literal names every field.

use std::collections::HashMap;
use std::sync::Arc;

use bmlib::fulltext::models as m;
use bmlib::publications::fetchers::registry as reg;

uniffi::setup_scaffolding!();

// ---------------------------------------------------------------------------
// JATS
// ---------------------------------------------------------------------------

/// One contributor. `full_name` is bmlib's `author_full_name`, precomputed so
/// the apps do not re-derive the structured-name-wins rule.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsAuthor {
    /// `<surname>`.
    pub surname: String,
    /// `<given-names>`.
    pub given_names: String,
    /// Resolved affiliation texts.
    pub affiliations: Vec<String>,
    /// `<collab>` — a group author.
    pub collab: String,
    /// `<string-name>` — an undivided name.
    pub string_name: String,
    /// bmlib's `author_full_name`.
    pub full_name: String,
}

/// One abstract section.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsAbstractSection {
    /// Section title; empty for an unstructured abstract with no `<title>`.
    pub title: String,
    /// The section's text.
    pub content: String,
}

/// One body (or back-matter) section, recursively.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsBodySection {
    /// Section title; empty for untitled content.
    pub title: String,
    /// Paragraph texts.
    pub paragraphs: Vec<String>,
    /// Nested sections.
    pub subsections: Vec<JatsBodySection>,
}

/// A figure.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsFigure {
    /// `@id`.
    pub id: String,
    /// `<label>`.
    pub label: String,
    /// Caption text.
    pub caption: String,
    /// The best-ranked `<graphic>` href, if any.
    pub graphic_url: Option<String>,
    /// Footnotes, marker joined.
    pub footnotes: Vec<String>,
}

/// A table. bmlib carries the table as HTML; the apps' markdown rendering is
/// **not** produced here (see the spike record).
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsTable {
    /// `@id`.
    pub id: String,
    /// `<label>`.
    pub label: String,
    /// Caption text.
    pub caption: String,
    /// The table body as HTML.
    pub html_content: String,
    /// A table deposited as an image.
    pub graphic_url: Option<String>,
    /// Footnotes, marker joined.
    pub footnotes: Vec<String>,
}

/// One reference-list entry.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsReference {
    /// `@id`.
    pub id: String,
    /// `<label>`.
    pub label: String,
    /// The rendered citation text.
    pub citation: String,
    /// Author names.
    pub authors: Vec<String>,
    /// `<article-title>`.
    pub article_title: String,
    /// `<source>`.
    pub source: String,
    /// `<year>`.
    pub year: String,
    /// `<volume>`.
    pub volume: String,
    /// `<issue>`.
    pub issue: String,
    /// `<fpage>`.
    pub first_page: String,
    /// `<lpage>`.
    pub last_page: String,
    /// DOI.
    pub doi: String,
    /// PMID.
    pub pmid: String,
    /// `<elocation-id>`.
    pub elocation_id: String,
}

/// One funder named in an `<award-group>`.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsFundingSource {
    /// Funder name.
    pub name: String,
    /// Funder identifier (e.g. a FundRef DOI).
    pub identifier: String,
}

/// One `<award-group>`.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsFundingAward {
    /// Funders.
    pub sources: Vec<JatsFundingSource>,
    /// Award numbers.
    pub award_ids: Vec<String>,
}

/// A parsed JATS article.
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsArticle {
    /// Article title.
    pub title: String,
    /// Contributors.
    pub authors: Vec<JatsAuthor>,
    /// Journal title.
    pub journal: String,
    /// Volume.
    pub volume: String,
    /// Issue.
    pub issue: String,
    /// `fpage-lpage`.
    pub pages: String,
    /// Publication year.
    pub year: String,
    /// DOI.
    pub doi: String,
    /// PMC id, `PMC`-prefixed.
    pub pmc_id: String,
    /// PMID.
    pub pmid: String,
    /// `<elocation-id>`.
    pub elocation_id: String,
    /// Abstract sections.
    pub abstract_sections: Vec<JatsAbstractSection>,
    /// Body and back-matter sections.
    pub body_sections: Vec<JatsBodySection>,
    /// Figures.
    pub figures: Vec<JatsFigure>,
    /// Tables.
    pub tables: Vec<JatsTable>,
    /// References.
    pub references: Vec<JatsReference>,
    /// Did the article have a `<body>` at all?
    pub has_body: bool,
    /// Nested `<article>`s whose content was suppressed.
    pub suppressed_nested_articles: u64,
    /// `<funding-statement>` texts.
    pub funding_statements: Vec<String>,
    /// `<award-group>`s.
    pub funding_awards: Vec<JatsFundingAward>,
}

/// A parse with its audit: the counted publisher losses (`warnings`) and the
/// reader-defect diagnostics (`diagnostics`, empty on a correct reader).
#[derive(Debug, Clone, uniffi::Record)]
pub struct JatsParseReport {
    /// The article.
    pub article: JatsArticle,
    /// Counted losses that are the publisher's deposit.
    pub warnings: Vec<String>,
    /// Reader-defect diagnostics.
    pub diagnostics: Vec<String>,
}

/// Why a parse failed.
#[derive(Debug, uniffi::Error)]
pub enum JatsError {
    /// The bytes are not well-formed XML.
    Xml {
        /// The XML reader's message. Not `message`: Kotlin exceptions inherit
        /// `Throwable.message`, and a field of that name does not compile.
        reason: String,
    },
}

impl std::fmt::Display for JatsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JatsError::Xml { reason } => write!(f, "malformed JATS XML: {reason}"),
        }
    }
}
impl std::error::Error for JatsError {}

impl From<bmlib::fulltext::JatsError> for JatsError {
    fn from(e: bmlib::fulltext::JatsError) -> Self {
        match e {
            bmlib::fulltext::JatsError::Xml(reason) => JatsError::Xml { reason },
        }
    }
}

impl From<&m::JATSAuthorInfo> for JatsAuthor {
    fn from(a: &m::JATSAuthorInfo) -> Self {
        JatsAuthor {
            surname: a.surname.clone(),
            given_names: a.given_names.clone(),
            affiliations: a.affiliations.clone(),
            collab: a.collab.clone(),
            string_name: a.string_name.clone(),
            full_name: bmlib::fulltext::author_full_name(a),
        }
    }
}

impl From<m::JATSBodySection> for JatsBodySection {
    fn from(s: m::JATSBodySection) -> Self {
        JatsBodySection {
            title: s.title,
            paragraphs: s.paragraphs,
            subsections: s.subsections.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<m::JATSArticle> for JatsArticle {
    fn from(a: m::JATSArticle) -> Self {
        JatsArticle {
            authors: a.authors.iter().map(Into::into).collect(),
            title: a.title,
            journal: a.journal,
            volume: a.volume,
            issue: a.issue,
            pages: a.pages,
            year: a.year,
            doi: a.doi,
            pmc_id: a.pmc_id,
            pmid: a.pmid,
            elocation_id: a.elocation_id,
            abstract_sections: a
                .abstract_sections
                .into_iter()
                .map(|s| JatsAbstractSection {
                    title: s.title,
                    content: s.content,
                })
                .collect(),
            body_sections: a.body_sections.into_iter().map(Into::into).collect(),
            figures: a
                .figures
                .into_iter()
                .map(|f| JatsFigure {
                    id: f.id,
                    label: f.label,
                    caption: f.caption,
                    graphic_url: f.graphic_url,
                    footnotes: f.footnotes,
                })
                .collect(),
            tables: a
                .tables
                .into_iter()
                .map(|t| JatsTable {
                    id: t.id,
                    label: t.label,
                    caption: t.caption,
                    html_content: t.html_content,
                    graphic_url: t.graphic_url,
                    footnotes: t.footnotes,
                })
                .collect(),
            references: a
                .references
                .into_iter()
                .map(|r| JatsReference {
                    id: r.id,
                    label: r.label,
                    citation: r.citation,
                    authors: r.authors,
                    article_title: r.article_title,
                    source: r.source,
                    year: r.year,
                    volume: r.volume,
                    issue: r.issue,
                    first_page: r.first_page,
                    last_page: r.last_page,
                    doi: r.doi,
                    pmid: r.pmid,
                    elocation_id: r.elocation_id,
                })
                .collect(),
            has_body: a.has_body,
            suppressed_nested_articles: a.suppressed_nested_articles as u64,
            funding_statements: a.funding_statements,
            funding_awards: a
                .funding_awards
                .into_iter()
                .map(|w| JatsFundingAward {
                    sources: w
                        .sources
                        .into_iter()
                        .map(|s| JatsFundingSource {
                            name: s.name,
                            identifier: s.identifier,
                        })
                        .collect(),
                    award_ids: w.award_ids,
                })
                .collect(),
        }
    }
}

/// Parse a JATS document.
///
/// # Errors
///
/// [`JatsError::Xml`] when the document is not well-formed.
#[uniffi::export]
pub fn parse_jats(xml: String) -> Result<JatsArticle, JatsError> {
    Ok(bmlib::fulltext::parse(&xml)?.into())
}

/// Parse a JATS document with a known PMC id, returning the audit as well.
///
/// # Errors
///
/// [`JatsError::Xml`] when the document is not well-formed.
#[uniffi::export]
pub fn parse_jats_audited(xml: String, known_pmc_id: String) -> Result<JatsParseReport, JatsError> {
    let report = bmlib::fulltext::parse_audited(&xml, &known_pmc_id)?;
    Ok(JatsParseReport {
        article: report.article.into(),
        warnings: report.warnings,
        diagnostics: report.diagnostics,
    })
}

/// The bmlib crate version these bindings were built from — what an app pins.
#[uniffi::export]
pub fn bmlib_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

// ---------------------------------------------------------------------------
// HTTP: the apps keep their own transports (SKETCH — compiles, not wired up)
// ---------------------------------------------------------------------------

/// A response from the app's transport.
#[derive(Debug, Clone, uniffi::Record)]
pub struct ForeignHttpResponse {
    /// HTTP status.
    pub status: u16,
    /// Raw body bytes (a PDF is not a string).
    pub body: Vec<u8>,
}

/// A request that never completed (no connection, TLS, timeout). A non-2xx
/// status is **not** this — it is a [`ForeignHttpResponse`], as in bmlib.
#[derive(Debug, uniffi::Error)]
pub enum ForeignHttpError {
    /// The transport failed.
    Transport {
        /// The transport's message (see [`JatsError::Xml`] on the name).
        reason: String,
    },
}

impl std::fmt::Display for ForeignHttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ForeignHttpError::Transport { reason } => write!(f, "transport failed: {reason}"),
        }
    }
}
impl std::error::Error for ForeignHttpError {}

/// A foreign callback that throws something other than [`ForeignHttpError`] (a Swift
/// `fatalError` aside) arrives here; it is a transport failure to bmlib.
impl From<uniffi::UnexpectedUniFFICallbackError> for ForeignHttpError {
    fn from(e: uniffi::UnexpectedUniFFICallbackError) -> Self {
        ForeignHttpError::Transport { reason: e.reason }
    }
}

/// The app's HTTP transport, implemented in Swift (`URLSession`) or Kotlin
/// (`OkHttp`). bmlib's polite pacing, retries and the 429 handling the apps
/// already own stay on the app side.
///
/// Blocking by design: bmlib's `HttpClient` is synchronous, so the foreign
/// implementation is called on whatever thread called into Rust and must block
/// that thread (Swift: a semaphore around a `URLSession` task; Kotlin:
/// `OkHttp`'s `execute()`). Call into Rust off the main thread.
#[uniffi::export(with_foreign)]
pub trait ForeignHttpClient: Send + Sync {
    /// GET `url`.
    fn get(&self, url: String) -> Result<ForeignHttpResponse, ForeignHttpError>;
    /// POST a JSON body.
    fn post_json(
        &self,
        url: String,
        body_json: String,
        headers: HashMap<String, String>,
    ) -> Result<ForeignHttpResponse, ForeignHttpError>;
}

/// Adapts the foreign transport to bmlib's `HttpClient` trait.
struct ForeignTransport(Arc<dyn ForeignHttpClient>);

impl reg::HttpClient for ForeignTransport {
    fn get(&self, url: &str) -> Result<reg::HttpResponse, reg::FetchError> {
        self.0
            .get(url.to_string())
            .map(|r| reg::HttpResponse::from_bytes(r.status, r.body))
            .map_err(|e| reg::FetchError::Transport(e.to_string()))
    }

    fn post_json(
        &self,
        url: &str,
        body: &serde_json::Value,
        headers: &std::collections::BTreeMap<String, String>,
    ) -> Result<reg::HttpResponse, reg::FetchError> {
        self.0
            .post_json(
                url.to_string(),
                body.to_string(),
                headers
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            )
            .map(|r| reg::HttpResponse::from_bytes(r.status, r.body))
            .map_err(|e| reg::FetchError::Transport(e.to_string()))
    }
}

/// bmlib's full-text tier chain over the app's transport. SKETCH: only the
/// constructor and the cheap `fetch_status` probe are exposed, to prove the
/// callback interface crosses the boundary in both directions.
#[derive(uniffi::Object)]
pub struct BmlibFullTextService {
    transport: Arc<dyn reg::HttpClient + Send + Sync>,
    #[allow(dead_code)]
    inner: bmlib::fulltext::FullTextService,
}

#[uniffi::export]
impl BmlibFullTextService {
    /// Build the service over the app's transport.
    #[uniffi::constructor]
    pub fn new(client: Arc<dyn ForeignHttpClient>, email: String) -> Arc<Self> {
        let transport: Arc<dyn reg::HttpClient + Send + Sync> = Arc::new(ForeignTransport(client));
        Arc::new(BmlibFullTextService {
            inner: bmlib::fulltext::FullTextService::new(transport.clone(), email),
            transport,
        })
    }

    /// GET `url` through bmlib's side of the bridge and return the status:
    /// Rust → foreign transport → Rust. A probe for the round trip, nothing more.
    pub fn fetch_status(&self, url: String) -> Result<u16, ForeignHttpError> {
        self.transport
            .get(&url)
            .map(|r| r.status)
            .map_err(|e| ForeignHttpError::Transport {
                reason: format!("{e:?}"),
            })
    }
}
