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
//! for one consumer.
//!
//! **A field bmlib adds to a model fails to compile here.** Every `From` impl
//! destructures the bmlib value exhaustively — no `..` — so a new field is an
//! unbound-field error until it is either carried across or named with `_` as
//! deliberately left behind. Reading fields by name (`a.title`) would not do
//! this: the struct literal only forces every *FFI* field to be filled, so a
//! new bmlib field would compile and silently never reach the apps. Every bmlib
//! model here is exhaustive (none is `#[non_exhaustive]`), which is what makes
//! the destructuring a guarantee. The `tests` module holds the other direction:
//! a value that reaches the FFI record is the value bmlib produced.
//!
//! **A record's field order is part of the contract.** The generated Kotlin
//! data classes destructure by position and the Swift structs' memberwise
//! initialisers take fields in order, so a field is only ever *appended*; the
//! order matches bmlib's own model so the two read side by side.
//!
//! **An empty string means the document did not deposit the value.** Nothing
//! is invented for an absent value: no derived title, no numbered label, no
//! formatted citation. Only the hrefs (`graphic_url`) are `Option`s, following
//! bmlib's own model.

uniffi::setup_scaffolding!();

mod retraction;
pub use retraction::*;

#[cfg(feature = "http-sketch")]
mod http_sketch;
#[cfg(feature = "http-sketch")]
pub use http_sketch::*;

use bmlib::fulltext::models as m;

// ---------------------------------------------------------------------------
// JATS
// ---------------------------------------------------------------------------

/// One contributor. Every author a parse returns carries some spelling of a
/// name, so `full_name` is never blank.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsAuthor {
    /// The structured surname of a `<name>` deposit. Empty where the contributor
    /// is named only as a `collab` or a `string_name`, so sorting or indexing
    /// by it must handle the empty case.
    pub surname: String,
    /// The given names of a `<name>` deposit; empty where it gave none.
    pub given_names: String,
    /// **Always empty from this reader**: the Rust port has no `<aff>` handler
    /// yet (Python bmlib resolves affiliations since its issue #145). Do not
    /// read the empty list as "this contributor declared no affiliation".
    pub affiliations: Vec<String>,
    /// A collaboration's name (`<collab>`) — a group, not a person.
    pub collab: String,
    /// An undivided personal name, exactly as deposited (`<string-name>`).
    pub string_name: String,
    /// bmlib's `author_full_name`, precomputed so the apps do not re-derive the
    /// rule: a structured name wins over both undivided spellings, then
    /// `collab`, then `string_name`.
    pub full_name: String,
}

/// One abstract section.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsAbstractSection {
    /// The heading the abstract deposited; empty for an unstructured abstract.
    pub title: String,
    /// The section's prose.
    pub content: String,
}

/// One section, recursively: front matter, body and back matter, in document
/// order. [`JatsArticle::has_body`] — not this list being non-empty — says
/// whether the article has a body.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsBodySection {
    /// The heading the document deposited; empty for prose it heads with
    /// nothing. Two untitled sections may be adjacent.
    pub title: String,
    /// Paragraph texts. A paragraph may be the empty string.
    pub paragraphs: Vec<String>,
    /// Nested sections.
    pub subsections: Vec<JatsBodySection>,
}

/// A figure.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsFigure {
    /// `@id`.
    pub id: String,
    /// The figure's own `<label>`, as deposited; empty when it carries none —
    /// no number is invented from its position.
    pub label: String,
    /// Caption text.
    pub caption: String,
    /// The best-ranked `<graphic>` href, as deposited — usually relative (e.g.
    /// `pone.0123.g001`), so not a URL to open as it stands.
    pub graphic_url: Option<String>,
    /// The figure's notes, in document order. A marked note's marker is folded
    /// in as `"a — text"`; an attribution carries no marker. Splitting on
    /// `" — "` does **not** reliably recover the marker: a definition list in
    /// the notes uses the same separator and carries no marker at all.
    pub footnotes: Vec<String>,
}

/// A table. bmlib carries the table as HTML; the apps' markdown rendering is
/// **not** produced here (see bmlibrarian_lite's
/// `doc/cross_platform/bmlib_sync/uniffi_spike.md`).
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsTable {
    /// `@id`.
    pub id: String,
    /// The table's own `<label>`, as deposited; empty when it carries none.
    pub label: String,
    /// Caption text.
    pub caption: String,
    /// The table body as HTML; empty for a table deposited only as an image.
    pub html_content: String,
    /// The table's own `<graphic>` href, best-ranked. It may be set beside a
    /// non-empty `html_content` (a table deposited both ways), not only for a
    /// table deposited as an image.
    pub graphic_url: Option<String>,
    /// The table's notes; see [`JatsFigure::footnotes`] for the marker rule.
    pub footnotes: Vec<String>,
    /// The header rows as cell texts, as the reader reads them (see the crate's
    /// `JATSTableInfo::header_rows`); empty for an image-only table.
    pub header_rows: Vec<Vec<String>>,
    /// The body rows as cell texts, read as `header_rows` are; empty for an
    /// image-only table.
    pub body_rows: Vec<Vec<String>>,
}

/// One reference-list entry.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsReference {
    /// `@id`.
    pub id: String,
    /// The reference's own `<label>`; empty when it carries none.
    pub label: String,
    /// The text the deposit typeset — a `<mixed-citation>` or a typeset NLM
    /// `<citation>` — **not** a formatted reference. Empty for an element-only
    /// citation (`<element-citation>`), whose structured fields below are then
    /// all there is; read empty as "build the citation from the fields", not
    /// as "no citation".
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
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsFundingSource {
    /// Funder name.
    pub name: String,
    /// The funder's `<institution-id>`, verbatim: a Funder Registry DOI arrives
    /// both bare (`10.13039/…`) and wrapped in a `doi.org` URL, and sometimes
    /// as the literal `NA`. Fold the two spellings before matching.
    pub identifier: String,
}

/// One `<award-group>`.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsFundingAward {
    /// Funders.
    pub sources: Vec<JatsFundingSource>,
    /// Award numbers.
    pub award_ids: Vec<String>,
}

/// A parsed JATS article. Field order follows bmlib's `JATSArticle`.
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
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
    /// The page range as deposited: `fpage-lpage`, `fpage` alone, or empty —
    /// an article located by `elocation_id` usually has no pages.
    pub pages: String,
    /// Publication year.
    pub year: String,
    /// DOI.
    pub doi: String,
    /// PMC id. A `known_pmc_id` passed to [`parse_jats_audited`] is
    /// `PMC`-prefixed when given bare and wins over the document's own; a value
    /// read from the document is stored as deposited; empty when neither.
    pub pmc_id: String,
    /// PMID.
    pub pmid: String,
    /// Abstract sections.
    pub abstract_sections: Vec<JatsAbstractSection>,
    /// Front matter, body and back matter sections, in document order.
    pub body_sections: Vec<JatsBodySection>,
    /// Figures.
    pub figures: Vec<JatsFigure>,
    /// Tables.
    pub tables: Vec<JatsTable>,
    /// References.
    pub references: Vec<JatsReference>,
    /// Did body prose survive parsing — at least one non-empty `<p>` in
    /// `<body>`? **Not** "was there a `<body>`": a document of `<front>` and
    /// `<back>` only (medRxiv serves some) is `false` even though
    /// `body_sections` may carry its front and back matter. This is the field
    /// that says whether the article is full text.
    pub has_body: bool,
    /// How many `<sub-article>`/`<response>` regions were skipped (a nested one
    /// counted separately) — peer-review rounds, translations. Their prose is
    /// in no field; this is the one field that says they were there.
    pub suppressed_nested_articles: u64,
    /// The article's own `<elocation-id>`, the electronic locator deposited in
    /// place of a page range.
    pub elocation_id: String,
    /// `<funding-statement>` texts.
    pub funding_statements: Vec<String>,
    /// `<award-group>`s.
    pub funding_awards: Vec<JatsFundingAward>,
}

/// A parse with its audit: the counted publisher losses (`warnings`) and the
/// reader-defect diagnostics (`diagnostics`, empty on a correct reader).
#[derive(Debug, Clone, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct JatsParseReport {
    /// The article.
    pub article: JatsArticle,
    /// How the article names itself (its identifiers), for prefixing the lines
    /// below in an app's log — the lines themselves do not name the article.
    pub article_label: String,
    /// Reader-defect diagnostics: each is a claim that bmlib is wrong.
    pub diagnostics: Vec<String>,
    /// Counted losses that are the publisher's deposit.
    pub warnings: Vec<String>,
}

/// Why a parse failed.
#[derive(Debug, uniffi::Error)]
pub enum JatsError {
    /// The document is not well-formed XML.
    Xml {
        /// The XML reader's message. Not `message`: Kotlin exceptions inherit
        /// `Throwable.message`, and a field of that name does not compile.
        reason: String,
    },
    /// The document nests elements deeper than [`MAX_NESTING_DEPTH`] and was
    /// refused before parsing. The reader recurses once per level, and a stack
    /// overflow is not a panic — it would kill the app, which no error type can
    /// report.
    NestingTooDeep {
        /// The depth the pre-scan counted: exact for a document without an
        /// internal DTD subset, an upper bound for one declaring entities.
        depth: u64,
        /// The limit it exceeded.
        limit: u64,
    },
    /// The parse could not start: the system refused the thread it runs on.
    /// Nothing is wrong with the document; a later call may succeed.
    Resource {
        /// The system's message.
        reason: String,
    },
}

impl std::fmt::Display for JatsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JatsError::Xml { reason } => write!(f, "malformed JATS XML: {reason}"),
            JatsError::NestingTooDeep { depth, limit } => write!(
                f,
                "JATS document nests {depth} elements deep, beyond the {limit} this reader accepts"
            ),
            JatsError::Resource { reason } => {
                write!(f, "the JATS parse could not start: {reason}")
            }
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
        let full_name = bmlib::fulltext::author_full_name(a);
        let m::JATSAuthorInfo {
            surname,
            given_names,
            affiliations,
            collab,
            string_name,
        } = a;
        JatsAuthor {
            surname: surname.clone(),
            given_names: given_names.clone(),
            affiliations: affiliations.clone(),
            collab: collab.clone(),
            string_name: string_name.clone(),
            full_name,
        }
    }
}

impl From<m::JATSAbstractSection> for JatsAbstractSection {
    fn from(s: m::JATSAbstractSection) -> Self {
        let m::JATSAbstractSection { title, content } = s;
        JatsAbstractSection { title, content }
    }
}

impl From<m::JATSBodySection> for JatsBodySection {
    fn from(s: m::JATSBodySection) -> Self {
        let m::JATSBodySection {
            title,
            paragraphs,
            subsections,
        } = s;
        JatsBodySection {
            title,
            paragraphs,
            subsections: subsections.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<m::JATSFigureInfo> for JatsFigure {
    fn from(f: m::JATSFigureInfo) -> Self {
        let m::JATSFigureInfo {
            id,
            label,
            caption,
            graphic_url,
            footnotes,
        } = f;
        JatsFigure {
            id,
            label,
            caption,
            graphic_url,
            footnotes,
        }
    }
}

impl From<m::JATSTableInfo> for JatsTable {
    fn from(t: m::JATSTableInfo) -> Self {
        let m::JATSTableInfo {
            id,
            label,
            caption,
            html_content,
            graphic_url,
            footnotes,
            header_rows,
            body_rows,
        } = t;
        JatsTable {
            id,
            label,
            caption,
            html_content,
            graphic_url,
            footnotes,
            header_rows,
            body_rows,
        }
    }
}

impl From<m::JATSReferenceInfo> for JatsReference {
    fn from(r: m::JATSReferenceInfo) -> Self {
        let m::JATSReferenceInfo {
            id,
            label,
            citation,
            authors,
            article_title,
            source,
            year,
            volume,
            issue,
            first_page,
            last_page,
            doi,
            pmid,
            elocation_id,
        } = r;
        JatsReference {
            id,
            label,
            citation,
            authors,
            article_title,
            source,
            year,
            volume,
            issue,
            first_page,
            last_page,
            doi,
            pmid,
            elocation_id,
        }
    }
}

impl From<m::JATSFundingSource> for JatsFundingSource {
    fn from(s: m::JATSFundingSource) -> Self {
        let m::JATSFundingSource { name, identifier } = s;
        JatsFundingSource { name, identifier }
    }
}

impl From<m::JATSFundingAward> for JatsFundingAward {
    fn from(w: m::JATSFundingAward) -> Self {
        let m::JATSFundingAward { sources, award_ids } = w;
        JatsFundingAward {
            sources: sources.into_iter().map(Into::into).collect(),
            award_ids,
        }
    }
}

impl From<m::JATSArticle> for JatsArticle {
    fn from(a: m::JATSArticle) -> Self {
        let m::JATSArticle {
            title,
            authors,
            journal,
            volume,
            issue,
            pages,
            year,
            doi,
            pmc_id,
            pmid,
            abstract_sections,
            body_sections,
            figures,
            tables,
            references,
            has_body,
            suppressed_nested_articles,
            elocation_id,
            funding_statements,
            funding_awards,
        } = a;
        JatsArticle {
            title,
            authors: authors.iter().map(Into::into).collect(),
            journal,
            volume,
            issue,
            pages,
            year,
            doi,
            pmc_id,
            pmid,
            abstract_sections: abstract_sections.into_iter().map(Into::into).collect(),
            body_sections: body_sections.into_iter().map(Into::into).collect(),
            figures: figures.into_iter().map(Into::into).collect(),
            tables: tables.into_iter().map(Into::into).collect(),
            references: references.into_iter().map(Into::into).collect(),
            has_body,
            // usize → u64 widens on every target this ships to.
            suppressed_nested_articles: suppressed_nested_articles as u64,
            elocation_id,
            funding_statements,
            funding_awards: funding_awards.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<bmlib::fulltext::JatsReport> for JatsParseReport {
    fn from(r: bmlib::fulltext::JatsReport) -> Self {
        let bmlib::fulltext::JatsReport {
            article,
            // The structured imbalance counts. Their text is in `diagnostics`,
            // which is what an app can act on; the struct is bmlib's audit
            // internals.
            unwind: _,
            article_label,
            diagnostics,
            warnings,
        } = r;
        JatsParseReport {
            article: article.into(),
            article_label,
            diagnostics,
            warnings,
        }
    }
}

// ---------------------------------------------------------------------------
// The nesting bound
// ---------------------------------------------------------------------------

/// The deepest element nesting [`parse_jats`] accepts.
///
/// The XML reader (roxmltree) and bmlib's walk over its tree each recurse once
/// per element level, and a stack overflow aborts the process — UniFFI can
/// catch a panic, not that. Measured on a release (`opt-level = "s"`) build:
/// roxmltree alone overflows a 512 KiB stack (an iOS secondary thread's
/// default) before 1,000 levels, and bmlib's parse between 500 and 700. Real
/// articles are shallow: the deepest of the 8,118 articles in Europe PMC's
/// served bundle `PMC10030002_PMC10040000.xml.gz` nests 31 elements (the
/// bundle's own wrapper included), and of the 97,909 in PMC's
/// `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` package 43 (`PMC12162105`),
/// one article reaching 40 (measured 2026-10-18 by counting expat's start and
/// end events). The limit is six times the deepest article seen.
///
/// The guard is this crate's: bmlib's own `parse` entry points are unguarded
/// (issue #495), and this pre-scan is what would move there.
pub const MAX_NESTING_DEPTH: usize = 256;

/// The stack the parse runs on. With [`MAX_NESTING_DEPTH`] levels a release
/// build needs well under 1 MiB (8,000 levels fit 8 MiB); an unoptimised build
/// needs roughly ten times that per level — 1,000 levels overflowed 8 MiB and
/// fit 32 MiB — and is what `cargo test` runs. The stack is reserved address
/// space, touched only as deep as the parse goes.
const PARSE_STACK_BYTES: usize = if cfg!(debug_assertions) {
    64 << 20
} else {
    16 << 20
};

/// An upper bound on how deeply `xml` nests elements, computed without
/// recursion.
///
/// Exact for a well-formed document with no internal DTD subset. Every
/// element-start-like `<` inside an internal subset is added on top, because an
/// entity's replacement text can carry elements the body only references —
/// along any chain of entity references each entity's elements count once, so
/// their total bounds what expansion can add. Up to the first construct the
/// XML reader would reject, the count agrees with the depth the reader reaches;
/// past it the reader stops, so a malformed tail cannot make the bound too low
/// for what the reader actually does.
fn nesting_depth_bound(xml: &str) -> usize {
    let b = xml.as_bytes();
    let mut i = 0;
    let mut depth = 0usize;
    let mut deepest = 0usize;
    let mut entity_elements = 0usize;
    while let Some(off) = b[i..].iter().position(|&c| c == b'<') {
        i += off;
        let rest = &b[i..];
        if rest.starts_with(b"<!--") {
            i = skip_past(b, i + 4, b"-->");
        } else if rest.starts_with(b"<![CDATA[") {
            i = skip_past(b, i + 9, b"]]>");
        } else if rest.starts_with(b"<?") {
            i = skip_past(b, i + 2, b"?>");
        } else if rest.starts_with(b"<!") {
            let (end, elements) = skip_declaration(b, i + 2);
            entity_elements += elements;
            i = end;
        } else if rest.starts_with(b"</") {
            depth = depth.saturating_sub(1);
            i = skip_tag(b, i + 2).0;
        } else {
            let (end, self_closing) = skip_tag(b, i + 1);
            if !self_closing {
                depth += 1;
                deepest = deepest.max(depth);
            }
            i = end;
        }
    }
    deepest.saturating_add(entity_elements)
}

/// The index just past the next `terminator` at or after `from`, or the end.
fn skip_past(b: &[u8], from: usize, terminator: &[u8]) -> usize {
    b.get(from..)
        .and_then(|tail| tail.windows(terminator.len()).position(|w| w == terminator))
        .map_or(b.len(), |p| from + p + terminator.len())
}

/// Scan a tag from just after its `<` (or `</`): the index past its `>`, and
/// whether it closes itself (`/>`). Quoted attribute values may hold `>`.
fn skip_tag(b: &[u8], from: usize) -> (usize, bool) {
    let mut quote = None;
    let mut j = from;
    while j < b.len() {
        let c = b[j];
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if c == b'"' || c == b'\'' => quote = Some(c),
            None if c == b'>' => return (j + 1, j > from && b[j - 1] == b'/'),
            None => {}
        }
        j += 1;
    }
    (b.len(), false)
}

/// Scan a `<!…>` declaration (the DOCTYPE and its internal subset) from just
/// after its `<!`: the index past its closing `>`, and how many
/// element-start-like `<` it holds — in entity values, which are quoted, as
/// anywhere else. Comments and processing instructions inside the subset are
/// skipped as units, so a quote inside one cannot swallow the body.
fn skip_declaration(b: &[u8], from: usize) -> (usize, usize) {
    let mut quote = None;
    let mut brackets = 0usize;
    let mut elements = 0usize;
    let mut j = from;
    while j < b.len() {
        let c = b[j];
        if c == b'<' && b.get(j + 1).is_some_and(|&n| starts_a_name(n)) {
            elements += 1;
        }
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if b[j..].starts_with(b"<!--") => {
                j = skip_past(b, j + 4, b"-->");
                continue;
            }
            None if b[j..].starts_with(b"<?") => {
                j = skip_past(b, j + 2, b"?>");
                continue;
            }
            None if c == b'"' || c == b'\'' => quote = Some(c),
            None if c == b'[' => brackets += 1,
            None if c == b']' => brackets = brackets.saturating_sub(1),
            None if c == b'>' && brackets == 0 => return (j + 1, elements),
            None => {}
        }
        j += 1;
    }
    (b.len(), elements)
}

/// Can `c` begin an XML name? Any non-ASCII byte might (a UTF-8 lead byte), so
/// it counts — over-counting only tightens the bound.
fn starts_a_name(c: u8) -> bool {
    c.is_ascii_alphabetic() || c == b'_' || c == b':' || c >= 0x80
}

/// Refuse a document nesting past [`MAX_NESTING_DEPTH`], then run `parse` on a
/// thread with a stack sized for that depth, converting on that thread too
/// (the section conversion recurses as well).
///
/// A panic on the parse thread is re-raised on the calling one, so UniFFI's
/// boundary still turns it into a foreign error exactly as it would without
/// the thread.
fn parse_bounded<T, F>(xml: String, parse: F) -> Result<T, JatsError>
where
    T: Send + 'static,
    F: FnOnce(&str) -> Result<T, JatsError> + Send + 'static,
{
    let depth = nesting_depth_bound(&xml);
    if depth > MAX_NESTING_DEPTH {
        return Err(JatsError::NestingTooDeep {
            depth: depth as u64,
            limit: MAX_NESTING_DEPTH as u64,
        });
    }
    let handle = std::thread::Builder::new()
        .name("bmlib-jats-parse".to_string())
        .stack_size(PARSE_STACK_BYTES)
        .spawn(move || parse(&xml))
        .map_err(|e| JatsError::Resource {
            reason: e.to_string(),
        })?;
    match handle.join() {
        Ok(result) => result,
        Err(payload) => std::panic::resume_unwind(payload),
    }
}

// ---------------------------------------------------------------------------
// The exported functions
// ---------------------------------------------------------------------------

/// Parse a JATS document, **discarding its audit**.
///
/// bmlib counts what a parse loses (refused table spans, dropped formulas,
/// unfiled terms…) and checks itself for reader defects. This function returns
/// the article alone: those lines go only to the process's stderr, which an
/// iOS app does not see outside an attached debugger and Android discards. Use
/// [`parse_jats_audited`] to receive them.
///
/// # Errors
///
/// [`JatsError::Xml`] when the document is not well-formed,
/// [`JatsError::NestingTooDeep`] past [`MAX_NESTING_DEPTH`], and
/// [`JatsError::Resource`] when the parse thread cannot start.
#[uniffi::export]
pub fn parse_jats(xml: String) -> Result<JatsArticle, JatsError> {
    parse_bounded(xml, |xml| Ok(bmlib::fulltext::parse(xml)?.into()))
}

/// Parse a JATS document and return the audit with it.
///
/// `known_pmc_id` is the id the caller already holds, or `""` for none; a bare
/// number is `PMC`-prefixed, and it wins over an id the document deposits. The
/// audit's lines are also written to stderr, as for [`parse_jats`].
///
/// # Errors
///
/// As [`parse_jats`].
#[uniffi::export]
pub fn parse_jats_audited(xml: String, known_pmc_id: String) -> Result<JatsParseReport, JatsError> {
    parse_bounded(xml, move |xml| {
        Ok(bmlib::fulltext::parse_audited(xml, &known_pmc_id)?.into())
    })
}

/// The Rust workspace version these bindings were built from (`0.3.0` …),
/// which bmlib-ffi and the bmlib crate share. Not Python bmlib's version, and
/// the same for every commit of one version, so it cannot tell two builds
/// apart — the build's commit is issue #493.
#[uniffi::export]
pub fn bmlib_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[cfg(test)]
mod tests;
