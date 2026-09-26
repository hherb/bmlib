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

//! Full-text retrieval service with a multi-tier fallback chain.
//!
//! Tier 1a: Europe PMC XML -> JATS parser -> HTML
//! Tier 1b: Discover PMC ID via Europe PMC search, then Europe PMC XML
//! Tier 1b': Discover PMC ID via NCBI's ID Converter when the search found none
//! Tier 1c: NCBI PMC efetch for whichever PMC ID was resolved
//! Tier 1d: Europe PMC PDF render URL (when XML is unavailable but free PDF exists)
//! Tier 2:  Unpaywall -> open-access PDF URL
//! Tier 3:  DOI resolution -> publisher website URL
//!
//! A port of `bmlib/fulltext/service.py`. Every network call goes through the
//! injected [`HttpClient`] the `publications/fetchers` walkers already use, so
//! the whole chain is drivable from a scripted transport with no network.
//!
//! # The order is load-bearing
//!
//! The chain is tried in exactly the order above, and each tier exists because
//! the one before it can answer with less. Reordering it changes what a caller
//! *gets*, not merely how many requests are spent:
//!
//! * **Tier 0 before everything** — a fetcher-supplied URL is an address for
//!   *this* paper, where every later tier resolves one from an identifier. Put
//!   below Tier 1 and a bioRxiv source would be shadowed by whatever Europe PMC
//!   happens to hold.
//! * **1a before 1b** — a caller-supplied PMC ID is a stronger identity claim
//!   than a search hit; searching first could fetch a different article.
//! * **1b' after 1b, never before** — the Europe PMC search returns the PMC ID
//!   *and* the free-PDF URL Tier 1d needs in one request, so the converter's
//!   extra request is spent only when the search came back without an id.
//! * **1c after 1a/1b, before 1d** — structured JATS beats a PDF that needs
//!   `bmlib[pdf]` to read at all. NCBI serves PMC itself where Europe PMC
//!   serves the corpus its `inEPMC` flag describes.
//! * **1d before 2** — a Europe PMC render URL is free and known to be free;
//!   Unpaywall costs a request and answers with whatever repository it likes.
//! * **3 last** — it is the degradation: a link, with no text.
//!
//! # Content kind is honest
//!
//! A body-less JATS document (medRxiv serves one for some preprints) renders to
//! little more than the abstract. It is **never cached** — a later fetch may
//! find a populated document, and caching this would make the abstract
//! permanent for the identifier — and it is held back as a last resort while
//! every remaining tier looks for the real article. Where it is finally
//! returned, [`ContentKind::Abstract`] says so, so a caller that must not
//! analyse an abstract as an article can branch on it rather than on the
//! presence of `html`.
//!
//! # Defects the port fixes rather than reproduces
//!
//! The port implements the *corrected* behaviour of two filed defects, both
//! enumerated in the port plan's Appendix. Each site says so in a
//! `DEFECT-FIX` comment and is otherwise faithful:
//!
//! * **#304** — Tier 1b was gated on `pmc_id` being *empty* rather than
//!   *usable*, so a malformed caller id suppressed the DOI-discovered PMC
//!   fetch, and supplying an id returned strictly less than omitting it. Only
//!   the unambiguous malformed case is corrected: a well-formed but unserved
//!   caller id is still a stronger claim than a search hit and still suppresses
//!   the discovery search (the stale case is a design question, not a bug fix).
//! * **#305** — a cached-PDF hit returned `content_kind = none` with no
//!   abstract where call 1 returned `abstract`, and issued no request, so the
//!   chain that produced the abstract never ran again — permanently. A cached
//!   PDF whose text cannot be extracted is now a miss for `content_kind`
//!   purposes while its `file_path` is carried onto whatever the chain returns.

use crate::fulltext::cache::{safe_filename, sanitize_identifier, FullTextCache};
use crate::fulltext::jats_reader::{author_full_name, parse_with_pmc_id};
use crate::fulltext::models::{
    ContentKind, FullTextResult, FullTextSourceEntry, JATSArticle, JATSBodySection,
    JATSFundingAward, JATSReferenceInfo,
};
use crate::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse};
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

/// Europe PMC's REST base, for the search and for `fullTextXML`.
pub const EUROPE_PMC_BASE: &str = "https://www.ebi.ac.uk/europepmc/webservices/rest";

/// Unpaywall's API base.
pub const UNPAYWALL_BASE: &str = "https://api.unpaywall.org/v2";

/// The DOI resolver base, for the final link-only fallback.
pub const DOI_BASE: &str = "https://doi.org";

/// PubMed's web base, for the fallback when there is a PMID and no DOI.
pub const PUBMED_BASE: &str = "https://pubmed.ncbi.nlm.nih.gov";

/// NCBI's ID Converter, the second source for a PMC ID.
pub const NCBI_IDCONV_URL: &str = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/";

/// E-utilities `efetch`, which serves PMC itself.
pub const EUTILS_EFETCH_URL: &str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi";

/// The `tool` NCBI asks every caller to name.
pub const EUTILS_TOOL_NAME: &str = "bmlib";

/// The per-request timeout the Python applies.
///
/// Retained for API fidelity only: the injected [`HttpClient`] owns its own
/// timeout and redirect policy, and the trait exposes neither, so this value
/// reaches no request. See [`FullTextService::timeout`].
pub const TIMEOUT: f64 = 30.0;

/// The access codes Europe PMC labels a free `fullTextUrl` entry with.
///
/// Measured over 600 recent MEDLINE records — all 1,263 `fullTextUrl` entries,
/// of which 326 were `documentStyle=pdf`: `OA`/`Open access` 312 (95.7%),
/// `F`/`Free` 14 (4.3%), `S`/`Subscription required` 0. There was no fourth
/// value and every entry carried a code. Accepting only `"Free"` — which is
/// what the Python did until issue #79 — therefore discarded 95.7% of the free
/// PDFs Tier 1d exists to find, silently.
pub const FREE_PDF_AVAILABILITY_CODES: [&str; 2] = ["OA", "F"];

/// The display labels Europe PMC uses for the same two access states.
pub const FREE_PDF_AVAILABILITY_LABELS: [&str; 2] = ["Open access", "Free"];

/// The Python exception names that can only mean a bmlib defect.
///
/// A deny-list, not an allow-list: the legitimate failures are varied —
/// transport errors, `JSONDecodeError`, XML parse errors, `OSError` — while
/// the set that always means a defect is small and stable. What is *excluded*
/// is the load-bearing part: `JSONDecodeError` **is** a `ValueError` and XML
/// parse errors **are** `SyntaxError`s, so neither may appear here, and
/// `RuntimeError` (which carries `RecursionError`) and `OSError` are
/// environment. `AttributeError` is knowingly imperfect in the other
/// direction — a malformed Europe PMC body reaches it, and that is a bmlib
/// defect too: a missing shape check.
pub const BUG_TYPE_NAMES: [&str; 5] = [
    "TypeError",
    "AttributeError",
    "NameError",
    "KeyError",
    "IndexError",
];

/// A full-text retrieval failure.
///
/// Python has a two-member hierarchy — `FullTextUnavailableError` under
/// `FullTextError` — and the distinction is the whole reason it exists: the
/// exhaustion report (issue #67) is built on telling a broken chain from an
/// ordinary paywalled paper. `Unpaywall HTTP 503` and `DOI not found in
/// Unpaywall` were the same type once, so a total outage and a paper nobody
/// serves for free produced byte-identical summaries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FullTextError {
    /// A source answered, and it has no free full text for this article.
    ///
    /// Raised where a source replied and had nothing. A transport or protocol
    /// fault — a 5xx, a timeout, unparseable JSON — is [`FullTextError::Other`].
    Unavailable(String),
    /// Any other retrieval failure: a transport fault, a malformed body, or an
    /// exhausted chain with nowhere left to degrade to.
    Other(String),
}

impl std::fmt::Display for FullTextError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FullTextError::Unavailable(message) | FullTextError::Other(message) => {
                write!(f, "{message}")
            }
        }
    }
}

impl std::error::Error for FullTextError {}

/// Render `n` with its noun, pluralised the naive way.
#[must_use]
pub fn plural(n: usize, noun: &str) -> String {
    if n == 1 {
        format!("1 {noun}")
    } else {
        format!("{n} {noun}s")
    }
}

/// What one failure means, mirroring Python's exception classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FaultKind {
    /// Python's `FullTextUnavailableError`: a source answered, and has nothing.
    Unavailable,
    /// Python's `FullTextError`, or a transport/protocol exception.
    Fault,
    /// A [`BUG_TYPE_NAMES`] member. It can only mean bmlib is wrong, so it is
    /// reported the moment it is swallowed rather than at an exit.
    Defect,
}

/// One tier failure, classified the way Python's `except Exception` plus
/// `_BUG_TYPES` classified it.
///
/// [`name`](TierFault::name) is the Python exception's `__name__`, because that
/// is what the exhaustion report renders and what keys the once-per-service
/// defect warning. A transport failure is named for the [`FetchError`] variant
/// it arrived as, which is the one place the two languages' names necessarily
/// differ: Python names `httpx.ConnectError` where the port names the transport
/// seam.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TierFault {
    /// The Python exception class name, or this port's name for a transport
    /// failure.
    pub name: &'static str,
    /// What happened, for the DEBUG-level and per-article reports.
    pub message: String,
    /// What the failure means.
    pub kind: FaultKind,
}

impl TierFault {
    /// A source that answered and has nothing.
    #[must_use]
    pub fn unavailable(message: impl Into<String>) -> Self {
        TierFault {
            name: "FullTextUnavailableError",
            message: message.into(),
            kind: FaultKind::Unavailable,
        }
    }

    /// A transport or protocol fault.
    #[must_use]
    pub fn fault(name: &'static str, message: impl Into<String>) -> Self {
        TierFault {
            name,
            message: message.into(),
            kind: FaultKind::Fault,
        }
    }

    /// A failure that can only mean bmlib is wrong.
    #[must_use]
    pub fn defect(name: &'static str, message: impl Into<String>) -> Self {
        debug_assert!(
            BUG_TYPE_NAMES.contains(&name),
            "{name} is not a defect-shaped name"
        );
        TierFault {
            name,
            message: message.into(),
            kind: FaultKind::Defect,
        }
    }
}

impl From<FullTextError> for TierFault {
    fn from(error: FullTextError) -> Self {
        match error {
            FullTextError::Unavailable(message) => TierFault::unavailable(message),
            FullTextError::Other(message) => TierFault::fault("FullTextError", message),
        }
    }
}

/// Why one `fetch_fulltext` call came up empty.
///
/// Every tier that makes a request catches its own failure, reports it and
/// moves on. That is correct — an unreachable Unpaywall must not cost the DOI
/// fallback — but it left a chain that failed *everywhere* indistinguishable
/// from one that was simply offered nothing, and silent at any level a caller
/// normally runs at (issue #67).
///
/// Faults and absences are kept apart rather than counted together, because the
/// only question an operator can act on is whether anything went *wrong*. A
/// source replying "no free full text" is the ordinary outcome for most papers;
/// a connection failure across a corpus is a lost network, and a `TypeError` is
/// a bug.
///
/// Faults are a list because their type *names* are rendered; absences are a
/// count because only their number is. Type names, not messages: a message
/// carries the URL and the identifier, so nine of them would be as long as the
/// DEBUG log this summary exists to replace.
pub struct TierFailures<'a> {
    faults: Vec<&'static str>,
    absences: usize,
    /// Called at the moment a defect-shaped failure is swallowed, not at an
    /// exit. Every alternative reads this record at some exit, which is exactly
    /// issue #72: `describe()` is already consulted at one exit, and that is why
    /// the bug was invisible. Mandatory — opting out is spelled
    /// [`TierFailures::unreported`] — because an unwired callback is not a
    /// quieter channel but total silence.
    on_bug: Option<&'a dyn Fn(&TierFault)>,
}

impl<'a> TierFailures<'a> {
    /// A record whose defects are reported to `on_bug`.
    #[must_use]
    pub fn reported(on_bug: &'a dyn Fn(&TierFault)) -> Self {
        TierFailures {
            faults: Vec::new(),
            absences: 0,
            on_bug: Some(on_bug),
        }
    }

    /// A record nobody is listening to — direct helper calls and tests.
    #[must_use]
    pub fn unreported() -> Self {
        TierFailures {
            faults: Vec::new(),
            absences: 0,
            on_bug: None,
        }
    }

    /// Note one swallowed failure, filed by what it means.
    pub fn record(&mut self, fault: &TierFault) {
        if fault.kind == FaultKind::Unavailable {
            self.absences += 1;
            return;
        }
        self.faults.push(fault.name);
        if fault.kind == FaultKind::Defect {
            if let Some(on_bug) = self.on_bug {
                on_bug(fault);
            }
        }
    }

    /// Note a source that reported an absence by returning, not raising.
    pub fn note_absence(&mut self) {
        self.absences += 1;
    }

    /// The fault type names recorded, in the order they happened.
    #[must_use]
    pub fn faults(&self) -> &[&'static str] {
        &self.faults
    }

    /// How many sources answered that they had nothing.
    #[must_use]
    pub fn absences(&self) -> usize {
        self.absences
    }

    /// Summarise the attempts for a log line.
    ///
    /// Worded as *attempts*, never tiers: Tier 0 records once per
    /// fetcher-supplied source, so the number is not bounded by the chain's
    /// eight tiers and "9 tiers raised" was emittable from a run that attempted
    /// four.
    #[must_use]
    pub fn describe(&self) -> String {
        let mut parts: Vec<String> = Vec::new();
        if !self.faults.is_empty() {
            let mut kinds: Vec<&str> = self.faults.to_vec();
            kinds.sort_unstable();
            kinds.dedup();
            parts.push(format!(
                "{} failed ({})",
                plural(self.faults.len(), "attempt"),
                kinds.join(", ")
            ));
        }
        if self.absences > 0 {
            parts.push(format!("{} had nothing", plural(self.absences, "source")));
        }
        if parts.is_empty() {
            "no attempt reported a failure".to_string()
        } else {
            parts.join("; ")
        }
    }
}

/// The one regex that gates a PMC ID, validated where it becomes a URL path.
///
/// `\z` and not `$`: `$` also matches before a trailing newline, so an anchored
/// match would accept `"PMC123\n"` — the same reason Python's `fullmatch` is
/// used there rather than `match`.
fn pmc_id_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"^PMC\d+\z").expect("a fixed pattern"))
}

/// Whether an entry is one `fullTextUrl` entry bmlib may download.
///
/// Both access fields are read: the code (`availabilityCode`) decides when
/// present, and the display string (`availability`) is the fallback for an
/// entry carrying none. An allow-list, never a deny-list on "Subscription
/// required": an unknown future value must under-credit, costing one retrieval,
/// rather than send bmlib to download a paywalled PDF.
///
/// A code that *is* present but unrecognised returns `false` **without**
/// consulting the string: falling back there would let a future code bmlib has
/// never evaluated through on the strength of a label, which is the opposite of
/// the under-credit rule the allow-list exists to keep.
///
/// Both values are type-checked before they are compared, and a code that is
/// not a string is treated as no code at all rather than as an unrecognised
/// one — it carries no access claim to under-credit. Python's `x in frozenset`
/// *hashes* `x`, so an `availability` arriving as a JSON object would raise
/// `TypeError: unhashable type` — a defect-shaped name — and a malformed remote
/// payload would be reported as a bmlib defect and would spend the one-shot
/// `bug:TypeError` slot a later real defect needs.
#[must_use]
pub fn entry_is_free(entry: &Map<String, Value>) -> bool {
    match entry.get("availabilityCode") {
        Some(Value::String(code)) if !code.is_empty() => {
            FREE_PDF_AVAILABILITY_CODES.contains(&code.as_str())
        }
        _ => match entry.get("availability") {
            Some(Value::String(availability)) => {
                FREE_PDF_AVAILABILITY_LABELS.contains(&availability.as_str())
            }
            _ => false,
        },
    }
}

/// Extract a free PDF URL from Europe PMC's `fullTextUrlList`.
///
/// The search API includes `fullTextUrlList` with `?pdf=render` entries for
/// PDFs it serves itself, even when JATS XML is unavailable — which is exactly
/// when Tier 1d needs one.
///
/// Both container shapes are checked, for the reason [`entry_is_free`] checks
/// its two values: `.get(k, [])` returns `None`, not `[]`, for a key present
/// with a JSON null, and iterating that raises `TypeError` — a defect-shaped
/// name. A malformed payload would then be reported as a bmlib defect *and*
/// spend the one-shot `bug:TypeError` slot.
#[must_use]
pub fn extract_free_pdf_url(result: &Map<String, Value>) -> Option<String> {
    let url_list = match result.get("fullTextUrlList") {
        Some(Value::Object(url_list)) => url_list,
        _ => return None,
    };
    let entries = match url_list.get("fullTextUrl") {
        Some(Value::Array(entries)) => entries,
        _ => return None,
    };
    for entry in entries {
        let Value::Object(entry) = entry else {
            continue;
        };
        let is_pdf =
            matches!(entry.get("documentStyle"), Some(Value::String(style)) if style == "pdf");
        if !is_pdf || !entry_is_free(entry) {
            continue;
        }
        if let Some(Value::String(url)) = entry.get("url") {
            return Some(url.clone());
        }
    }
    None
}

/// Pick an Unpaywall record's best PDF URL, or `None` if it has none.
///
/// A module-level function rather than part of the Unpaywall tier so a sampler
/// script can measure the population bmlib actually downloads: measured against
/// a hand-copy, the number a log level was set from would drift from the code it
/// describes the moment either changed.
///
/// # Errors
///
/// A [`FaultKind::Defect`] where the record's shape is one Python would raise
/// through — a truthy non-object `best_oa_location`, a truthy non-iterable
/// `oa_locations`, or a non-object location. There is deliberately **no**
/// `TypeError` for a truthy non-string URL: the Python's truthiness test admits
/// one and it fails later inside the transport. The port reads strings only and
/// skips anything else (recorded in the port report as a divergence).
pub fn pick_oa_pdf_url(data: &Map<String, Value>) -> Result<Option<String>, TierFault> {
    let best = match data.get("best_oa_location") {
        Some(value) if truthy(value) => value,
        _ => &Value::Object(Map::new()),
    };
    let best = match best {
        Value::Object(best) => best,
        _ => {
            return Err(TierFault::defect(
                "AttributeError",
                "'best_oa_location' is not an object",
            ))
        }
    };
    if let Some(url) = first_url(best) {
        return Ok(Some(url));
    }

    let locations = match data.get("oa_locations") {
        Some(value) if truthy(value) => value,
        _ => return Ok(None),
    };
    let Value::Array(locations) = locations else {
        // A truthy dict or string iterates into its keys/characters and the
        // `.get` then raises AttributeError; a number or boolean is not
        // iterable at all and raises TypeError. Both are Python's, not ours.
        let name = match locations {
            Value::String(_) | Value::Object(_) => "AttributeError",
            _ => "TypeError",
        };
        return Err(TierFault::defect(
            name,
            "'oa_locations' is not iterable as a list of objects",
        ));
    };
    for location in locations {
        let Value::Object(location) = location else {
            return Err(TierFault::defect(
                "AttributeError",
                "an 'oa_locations' entry is not an object",
            ));
        };
        if let Some(url) = first_url(location) {
            return Ok(Some(url));
        }
    }
    Ok(None)
}

/// `url_for_pdf or url` from one Unpaywall location object.
fn first_url(location: &Map<String, Value>) -> Option<String> {
    for key in ["url_for_pdf", "url"] {
        if let Some(Value::String(url)) = location.get(key) {
            if !url.is_empty() {
                return Some(url.clone());
            }
        }
    }
    None
}

/// Prefix a bare numeric PMC ID and validate the result.
///
/// A PMC ID is interpolated into a URL path by two fetch helpers, and reaches
/// them from three places: the caller, Europe PMC's search response and NCBI's
/// ID Converter. Only the first is under bmlib's control, so the check lives at
/// the point of use and covers all three. Every tier catches the error and
/// moves on, so a malformed ID costs a log line rather than a request.
///
/// # Errors
///
/// [`FullTextError::Other`] if the value is not `PMC` followed by digits.
pub fn normalise_pmc_id(pmc_id: &str) -> Result<String, FullTextError> {
    let normalized = if pmc_id.starts_with("PMC") {
        pmc_id.to_string()
    } else {
        format!("PMC{pmc_id}")
    };
    if !pmc_id_pattern().is_match(&normalized) {
        // Python's `!r` quotes with single quotes where Rust's `{:?}` uses
        // double; the message is otherwise the same.
        return Err(FullTextError::Other(format!(
            "Not a usable PMC ID: '{pmc_id}'"
        )));
    }
    Ok(normalized)
}

/// Whether a caller-supplied PMC ID is one that a fetch could use.
fn pmc_id_is_usable(pmc_id: Option<&str>) -> bool {
    match pmc_id {
        None => false,
        Some(id) => !id.is_empty() && normalise_pmc_id(id).is_ok(),
    }
}

/// Python's truthiness for a decoded JSON value.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

/// Python's `str()` for a decoded JSON value, as the ID Converter's `live`
/// flag is read through it.
fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(value) => value.clone(),
        // Python renders a list or dict as its `repr`; JSON text is the port's
        // nearest equivalent and differs only inside a nested string's quoting.
        other => other.to_string(),
    }
}

/// Python's `urllib.parse.quote(s, safe=...)`.
///
/// The always-safe set is `A-Za-z0-9_.-~`; every other byte of the UTF-8
/// encoding is percent-encoded with uppercase hex. Needed because the injected
/// [`HttpClient`] takes a whole URL and no query parameters.
#[must_use]
pub fn quote(value: &str, safe: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.as_bytes() {
        let character = *byte as char;
        let unreserved = character.is_ascii_alphanumeric() || "_.-~".contains(character);
        if unreserved || safe.contains(character) {
            out.push(character);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// The name a [`FetchError`] contributes to the exhaustion report.
fn fetch_error_name(error: &FetchError) -> &'static str {
    match error {
        FetchError::Transport(_) => "TransportError",
        FetchError::Malformed(_) => "MalformedError",
        FetchError::Config(_) => "ConfigError",
        FetchError::ResumeUnreadable(_) => "ResumeUnreadableError",
    }
}

/// One response body read as **strict** UTF-8, for the text tiers.
///
/// The counterpart of `FullTextService::http_get` for the callers that read
/// JATS XML. A body that is not valid UTF-8 is a fault about the fetch rather
/// than something to decode lossily — the PDF tiers never come through here,
/// which is exactly why they keep the raw bytes.
fn response_text(response: &HttpResponse) -> Result<&str, TierFault> {
    response
        .text()
        .map_err(|error| TierFault::fault(fetch_error_name(&error), error.to_string()))
}

// ---------------------------------------------------------------------------
// The JATS HTML rendering
// ---------------------------------------------------------------------------

/// The heading level a body section never exceeds.
const MAX_HEADING_LEVEL: usize = 6;

/// Render a parsed JATS article as the HTML full text the service caches.
///
/// This is a port of Python's `jats_parser._build_html`, placed here because
/// [`crate::fulltext::jats_reader`] deliberately stops at structured data and
/// the service is the rendering's only consumer: `FullTextResult.html` must
/// carry article text for [`ContentKind`] to be honest, and a body-less render
/// must still produce the abstract it is held back for.
///
/// The rules it keeps, each measured in the Python:
///
/// * **No exhibit number is invented.** `fig.label or f"Figure {i + 1}"` stated
///   a number the document does not carry; 7,058 exhibits in the committed
///   corpus carry 6,937 direct-child `<label>` elements, so 121 of them in 83 of
///   997 articles were given one — and the invented number is the *index*, so it
///   collides with a real one. An unlabelled `<figcaption>` is emitted only
///   where the deposit gives it something to hold.
/// * **An exhibit's footnotes are a block inside its own container**, after the
///   caption — where the publisher prints them, and what keeps caption and note
///   distinguishable in the string the service caches, which for a service
///   consumer is the only place either is ever seen.
/// * **The locator is one run**: `volume(issue):pages`, `elocation_id` where
///   there is no page range, and separated only from something it follows.
/// * **A reference with fewer than two printable components prints its
///   deposited `citation` instead** where there is one; a lone `(2023)` in place
///   of a whole citation is issue #268's defect.
#[must_use]
pub fn render_jats_html(article: &JATSArticle) -> String {
    let mut parts: Vec<String> = Vec::new();

    if !article.title.is_empty() {
        parts.push(format!("<h1>{}</h1>", html_escape(&article.title)));
    }

    if !article.authors.is_empty() {
        let names: Vec<String> = article.authors.iter().map(author_full_name).collect();
        let author_str = if names.len() <= 5 {
            names.join(", ")
        } else {
            format!("{} et al.", names[..5].join(", "))
        };
        parts.push(format!(
            "<p class=\"authors\"><strong>Authors:</strong> {}</p>",
            html_escape(&author_str)
        ));
    }

    let journal_html = format_journal_html(article);
    if !journal_html.is_empty() {
        parts.push(format!("<p class=\"journal-info\">{journal_html}</p>"));
    }

    let ids_html = format_identifiers_html(article);
    if !ids_html.is_empty() {
        parts.push(format!("<p class=\"identifiers\">{ids_html}</p>"));
    }

    if !article.abstract_sections.is_empty() {
        parts.push("<h2>Abstract</h2>".to_string());
        for section in &article.abstract_sections {
            if section.title.is_empty() {
                parts.push(format!("<p>{}</p>", html_escape(&section.content)));
            } else {
                parts.push(format!(
                    "<p><strong>{}:</strong> {}</p>",
                    html_escape(&section.title),
                    html_escape(&section.content)
                ));
            }
        }
    }

    for section in &article.body_sections {
        parts.extend(format_body_section_html(section, 2));
    }

    if !article.funding_statements.is_empty() || !article.funding_awards.is_empty() {
        parts.push("<section class=\"funding\">".to_string());
        parts.push("<h2>Funding</h2>".to_string());
        for statement in &article.funding_statements {
            parts.push(format!("<p>{}</p>", html_escape(statement)));
        }
        for award in &article.funding_awards {
            parts.push(format!("<p>{}</p>", html_escape(&format_award(award))));
        }
        parts.push("</section>".to_string());
    }

    if !article.figures.is_empty() {
        parts.push("<h2>Figures</h2>".to_string());
        for (index, figure) in article.figures.iter().enumerate() {
            // The anchor id keeps its fallback: it is a link target this
            // renderer owns, never a claim about what the document says.
            let anchor_id = if figure.id.is_empty() {
                format!("fig{}", index + 1)
            } else {
                figure.id.clone()
            };
            parts.push(format!("<figure id=\"{}\">", html_escape(&anchor_id)));
            if let Some(graphic) = &figure.graphic_url {
                let full_url = build_exhibit_url(graphic, &article.pmc_id);
                let alt = if figure.label.is_empty() {
                    figure.caption.clone()
                } else {
                    figure.label.clone()
                };
                parts.push(format!(
                    "  <img src=\"{}\" alt=\"{}\" loading=\"lazy\">",
                    html_escape(&full_url),
                    html_escape(&alt)
                ));
            }
            if !figure.label.is_empty() || !figure.caption.is_empty() {
                parts.push("  <figcaption>".to_string());
                if !figure.label.is_empty() {
                    parts.push(format!(
                        "    <strong>{}</strong>",
                        html_escape(&figure.label)
                    ));
                }
                if !figure.caption.is_empty() {
                    parts.push(format!("    <p>{}</p>", html_escape(&figure.caption)));
                }
                parts.push("  </figcaption>".to_string());
            }
            parts.extend(format_exhibit_footnotes_html(&figure.footnotes));
            parts.push("</figure>".to_string());
        }
    }

    if !article.tables.is_empty() {
        parts.push("<h2>Tables</h2>".to_string());
        for (index, table) in article.tables.iter().enumerate() {
            let anchor_id = if table.id.is_empty() {
                format!("table{}", index + 1)
            } else {
                table.id.clone()
            };
            parts.push(format!(
                "<div class=\"table-container\" id=\"{}\">",
                html_escape(&anchor_id)
            ));
            if !table.label.is_empty() {
                parts.push(format!("  <h3>{}</h3>", html_escape(&table.label)));
            }
            if !table.caption.is_empty() {
                parts.push(format!(
                    "  <p class=\"table-caption\">{}</p>",
                    html_escape(&table.caption)
                ));
            }
            if !table.html_content.is_empty() {
                parts.push(table.html_content.clone());
            } else if let Some(graphic) = &table.graphic_url {
                // Only where there is no markup: a `<table-wrap>` may carry
                // both, and where it does the `<table>` is the better rendition.
                let full_url = build_exhibit_url(graphic, &article.pmc_id);
                let alt = if table.label.is_empty() {
                    table.caption.clone()
                } else {
                    table.label.clone()
                };
                parts.push(format!(
                    "  <img src=\"{}\" alt=\"{}\" loading=\"lazy\">",
                    html_escape(&full_url),
                    html_escape(&alt)
                ));
            }
            parts.extend(format_exhibit_footnotes_html(&table.footnotes));
            parts.push("</div>".to_string());
        }
    }

    if !article.references.is_empty() {
        parts.push("<h2>References</h2>".to_string());
        parts.push("<ol class=\"references\">".to_string());
        for reference in &article.references {
            parts.push(format!(
                "  <li id=\"ref-{}\">{}</li>",
                html_escape(&reference.id),
                format_ref_html(reference)
            ));
        }
        parts.push("</ol>".to_string());
    }

    parts.join("\n")
}

/// An exhibit's footnotes as the block a publisher prints.
fn format_exhibit_footnotes_html(footnotes: &[String]) -> Vec<String> {
    if footnotes.is_empty() {
        return Vec::new();
    }
    let mut parts = vec!["  <div class=\"fn-group\">".to_string()];
    parts.extend(
        footnotes
            .iter()
            .map(|note| format!("    <p>{}</p>", html_escape(note))),
    );
    parts.push("  </div>".to_string());
    parts
}

/// `journal (volume(issue):locator) (year)`, escaped as one run.
fn format_journal_html(article: &JATSArticle) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !article.journal.is_empty() {
        parts.push(format!("<em>{}</em>", html_escape(&article.journal)));
    }
    let mut volume_parts: Vec<String> = Vec::new();
    if !article.volume.is_empty() {
        volume_parts.push(article.volume.clone());
    }
    if !article.issue.is_empty() {
        volume_parts.push(format!("({})", article.issue));
    }
    // One locator, the page range where there is one, and separated only from
    // something it follows: an article carrying no volume or issue rendered
    // `: 100-101` (issue #265).
    let locator = if article.pages.is_empty() {
        article.elocation_id.clone()
    } else {
        article.pages.clone()
    };
    if !locator.is_empty() {
        if volume_parts.is_empty() {
            volume_parts.push(locator);
        } else {
            volume_parts.push(format!(": {locator}"));
        }
    }
    if !volume_parts.is_empty() {
        parts.push(html_escape(&volume_parts.join("")));
    }
    if !article.year.is_empty() {
        parts.push(format!("({})", html_escape(&article.year)));
    }
    parts.join(" ")
}

/// The DOI/PMC/PMID identifiers, each an anchor.
fn format_identifiers_html(article: &JATSArticle) -> String {
    let mut ids: Vec<String> = Vec::new();
    if !article.doi.is_empty() {
        let doi = html_escape(&article.doi);
        ids.push(format!("DOI: <a href=\"https://doi.org/{doi}\">{doi}</a>"));
    }
    if !article.pmc_id.is_empty() {
        let pmc_num = article
            .pmc_id
            .strip_prefix("PMC")
            .unwrap_or(&article.pmc_id);
        ids.push(format!(
            "PMC: <a href=\"https://europepmc.org/article/PMC/{}\">{}</a>",
            html_escape(pmc_num),
            html_escape(&article.pmc_id)
        ));
    }
    if !article.pmid.is_empty() {
        let pmid = html_escape(&article.pmid);
        ids.push(format!(
            "PMID: <a href=\"https://pubmed.ncbi.nlm.nih.gov/{pmid}/\">{pmid}</a>"
        ));
    }
    ids.join(" | ")
}

/// Render one `<award-group>` as a line (issue #284).
///
/// Each part is printed only where the document deposited it: an award naming
/// no funder prints its number alone, and a funder named with no number prints
/// alone too — the second being the commonest shape there is. Nothing is
/// invented: no `"Grant:"` label, no placeholder for an absent id.
fn format_award(award: &JATSFundingAward) -> String {
    let funders = award
        .sources
        .iter()
        .map(|source| {
            if !source.name.is_empty() && !source.identifier.is_empty() {
                format!("{} ({})", source.name, source.identifier)
            } else if source.name.is_empty() {
                source.identifier.clone()
            } else {
                source.name.clone()
            }
        })
        .collect::<Vec<_>>()
        .join("; ");
    let numbers = award.award_ids.join(", ");
    match (funders.is_empty(), numbers.is_empty()) {
        (false, false) => format!("{funders}: {numbers}"),
        (true, _) => numbers,
        (false, true) => funders,
    }
}

/// A body section's heading and paragraphs, recursing into subsections.
fn format_body_section_html(section: &JATSBodySection, level: usize) -> Vec<String> {
    let mut parts: Vec<String> = Vec::new();
    let heading = level.min(MAX_HEADING_LEVEL);
    if !section.title.is_empty() {
        parts.push(format!(
            "<h{heading}>{}</h{heading}>",
            html_escape(&section.title)
        ));
    }
    for paragraph in &section.paragraphs {
        if !paragraph.is_empty() {
            parts.push(format!("<p>{}</p>", convert_inline_links(paragraph)));
        }
    }
    for subsection in &section.subsections {
        parts.extend(format_body_section_html(subsection, level + 1));
    }
    parts
}

/// Resolve an exhibit's `<graphic>` href for the rendered HTML.
///
/// A relative href is resolved against Europe PMC's per-article `bin/`
/// directory, and one carrying no image extension is given `.jpg`, which is
/// what that service serves.
fn build_exhibit_url(path: &str, pmc_id: &str) -> String {
    if path.starts_with("http://") || path.starts_with("https://") {
        return path.to_string();
    }
    let lower = path.to_lowercase();
    let has_extension = [".gif", ".jpg", ".jpeg", ".png", ".svg"]
        .iter()
        .any(|extension| lower.ends_with(extension));
    if !pmc_id.is_empty() {
        let normalized = if pmc_id.starts_with("PMC") {
            pmc_id.to_string()
        } else {
            format!("PMC{pmc_id}")
        };
        let base = format!("https://europepmc.org/articles/{normalized}/bin/{path}");
        return if has_extension {
            base
        } else {
            format!("{base}.jpg")
        };
    }
    path.to_string()
}

/// Render one reference, deferring to the deposited citation where the
/// structured rendering would print fewer than two components.
fn format_ref_html(reference: &JATSReferenceInfo) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !reference.authors.is_empty() {
        if reference.authors.len() <= 3 {
            parts.push(html_escape(&reference.authors.join(", ")));
        } else {
            parts.push(html_escape(&format!(
                "{}, {}, et al.",
                reference.authors[0], reference.authors[1]
            )));
        }
    }
    if !reference.article_title.is_empty() {
        parts.push(html_escape(&reference.article_title));
    }
    if !reference.source.is_empty() {
        parts.push(format!("<em>{}</em>", html_escape(&reference.source)));
    }
    if !reference.year.is_empty() {
        parts.push(format!("({})", html_escape(&reference.year)));
    }
    let volume_info = reference_volume_info(reference);
    if !volume_info.is_empty() {
        parts.push(html_escape(&volume_info));
    }
    if !reference.doi.is_empty() {
        let doi = html_escape(&reference.doi);
        parts.push(format!("<a href=\"https://doi.org/{doi}\">doi:{doi}</a>"));
    }
    if defers_to_the_deposit(parts.len(), &reference.citation) {
        return html_escape(&reference.citation);
    }
    parts.join(". ")
}

/// The `volume(issue):locator` run, as both Python renderers print it.
fn reference_volume_info(reference: &JATSReferenceInfo) -> String {
    let mut volume_info = String::new();
    if !reference.volume.is_empty() {
        volume_info = reference.volume.clone();
        if !reference.issue.is_empty() {
            volume_info.push_str(&format!("({})", reference.issue));
        }
    }
    let mut page_range = reference.first_page.clone();
    if !page_range.is_empty() && !reference.last_page.is_empty() {
        page_range.push_str(&format!("-{}", reference.last_page));
    }
    let locator = if page_range.is_empty() {
        reference.elocation_id.clone()
    } else {
        page_range
    };
    if !locator.is_empty() {
        volume_info = if volume_info.is_empty() {
            locator
        } else {
            format!("{volume_info}:{locator}")
        };
    }
    volume_info
}

/// Would a rendering of that many components print `citation` instead?
///
/// **One component is never a citation**: a `<mixed-citation>` tagging just one
/// of its structured children rendered that child *in place of* the whole
/// deposited string — a bare `(2023)` for a report, an author list for a work
/// the rendering then never names. 828 of the served artifact's 174,458
/// references that carry a deposited string and render structured (346 of 8,118
/// articles) were moved by this rule.
fn defers_to_the_deposit(printed_part_count: usize, citation: &str) -> bool {
    printed_part_count == 0 || (printed_part_count == 1 && !citation.is_empty())
}

/// Python's `html.escape(text)` — with `quote=True`, so both quote characters
/// are escaped, and `&` first.
#[must_use]
pub fn html_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Convert markdown-style `[text](#anchor)` to anchors, escaping the rest.
fn convert_inline_links(text: &str) -> String {
    static LINK: OnceLock<Regex> = OnceLock::new();
    let pattern =
        LINK.get_or_init(|| Regex::new(r"\[([^\]]+)\]\(([^)]+)\)").expect("a fixed pattern"));

    let mut result = String::new();
    let mut last_end = 0;
    for capture in pattern.captures_iter(text) {
        let whole = capture.get(0).expect("group 0");
        result.push_str(&html_escape(&text[last_end..whole.start()]));
        let link_text = capture.get(1).expect("group 1").as_str();
        let href = capture.get(2).expect("group 2").as_str();
        result.push_str(&format!(
            "<a href=\"{}\">{}</a>",
            html_escape(href),
            html_escape(link_text)
        ));
        last_end = whole.end();
    }
    result.push_str(&html_escape(&text[last_end..]));
    result
}

// ---------------------------------------------------------------------------
// PDF text extraction
// ---------------------------------------------------------------------------

/// What one PDF text extraction produced.
///
/// The fields `_attach_pdf_text` reads from Python's `ConversionResult`;
/// [`PdfText::is_complete`] is a method rather than a field because Python
/// computes it (`success and page_count == converted_pages and char_count > 0`)
/// rather than accepting it, and a caller-supplied flag could disagree with the
/// numbers beside it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PdfText {
    /// The extracted prose rendered as an HTML fragment; empty where nothing
    /// could be extracted.
    pub html: String,
    /// Whether the backend considers the conversion successful.
    pub success: bool,
    /// The backend's message where [`PdfText::success`] is false.
    pub error_message: Option<String>,
    /// How many pages the document has.
    pub page_count: usize,
    /// How many pages produced text.
    pub converted_pages: usize,
    /// How many characters were extracted.
    pub char_count: usize,
    /// Per-page warnings the backend reported.
    pub warnings: Vec<String>,
}

impl PdfText {
    /// Whether every page was converted and some text was extracted.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.success && self.page_count == self.converted_pages && self.char_count > 0
    }
}

/// Why a PDF backend could not be used at all.
///
/// The two arms are Python's two separate handlers in `_attach_pdf_text`:
/// `Backend` is `get_converter()` failing (the extra is missing, or the backend
/// is broken), reported once per cause; `Conversion` is `convert`/`render_html`
/// raising, reported per PDF.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PdfExtractError {
    /// No backend could be constructed.
    Backend {
        /// A name for the cause, used as the once-per-service key.
        name: String,
        /// What happened.
        message: String,
    },
    /// The backend raised while converting.
    Conversion(String),
}

/// A pluggable PDF-to-text backend.
///
/// The Rust stand-in for `bmlib.fulltext.pdf_converter`'s `PDFConverter` ABC
/// plus `get_converter` and `render_html`. A service with **no** extractor
/// configured reproduces the Python with `bmlib[pdf]` not installed: the PDF is
/// still cached and its URL still reported, and the warning says so once.
pub trait PdfExtractor {
    /// Extract a cached PDF's text.
    ///
    /// # Errors
    ///
    /// [`PdfExtractError`] as described in its two arms.
    fn extract(&self, pdf_path: &Path) -> Result<PdfText, PdfExtractError>;
}

/// Which tier is downloading a PDF.
///
/// A bounded enumeration, not a `String`, because the value keys the one-shot
/// download-failure warnings and [`FullTextService::warn_once`] is only
/// one-shot if its keyspace cannot grow with the corpus. Tier 0's
/// `result.source` — the obvious wrong answer, in scope at all three call
/// sites — is remote-data-derived there, so it is a type rather than a
/// paragraph asking a reader not to write it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PdfOrigin {
    /// Tier 1d, Europe PMC's PDF render URL.
    EuropepmcPdf,
    /// Tier 2, Unpaywall.
    Unpaywall,
    /// Tier 0, a fetcher-supplied source.
    KnownSource,
}

impl PdfOrigin {
    /// The stable string the warning key is built from.
    fn as_str(self) -> &'static str {
        match self {
            PdfOrigin::EuropepmcPdf => "europepmc_pdf",
            PdfOrigin::Unpaywall => "unpaywall",
            PdfOrigin::KnownSource => "known_source",
        }
    }
}

/// What [`FullTextService::save_pdf_to_cache`] did with the bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PdfSaveOutcome {
    /// Written to the cache.
    Saved,
    /// The write failed; the cache-write warning has already spoken.
    WriteFailed,
    /// The magic-byte check rejected the data.
    NotAPdf,
}

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

/// One `fetch_fulltext` call's arguments.
///
/// Python's keyword-only parameters, as one value. `pmc_id`, `doi` and
/// `identifier` are `Option` because the Python's are; an empty string is
/// treated exactly as `None` was (Python's truthiness), which matters for
/// `pmc_id` because a caller passing `""` must not be sent to
/// `_normalise_pmc_id` and faulted.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct FullTextRequest {
    /// Known source URLs from the fetcher.
    pub fulltext_sources: Vec<FullTextSourceEntry>,
    /// PubMed Central ID if known.
    pub pmc_id: Option<String>,
    /// Digital Object Identifier.
    pub doi: Option<String>,
    /// PubMed ID.
    pub pmid: String,
    /// Cache key (typically DOI). When provided, enables disk caching of
    /// retrieved content.
    pub identifier: Option<String>,
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

/// The subset of Python's log levels this module emits.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum LogLevel {
    /// Per-article detail, and the traceback a WARNING promises.
    Debug,
    /// What the chain did.
    Info,
    /// A fault a caller or operator must see.
    Warning,
}

/// One emitted log line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LogLine {
    /// The level.
    pub level: LogLevel,
    /// The formatted message.
    pub message: String,
}

impl LogLine {
    /// Whether the line is a WARNING.
    #[must_use]
    pub fn is_warning(&self) -> bool {
        self.level == LogLevel::Warning
    }
}

// ---------------------------------------------------------------------------
// The service
// ---------------------------------------------------------------------------

/// Retrieves full text from multiple sources with fallback.
///
/// # What the port changes about construction
///
/// Python's `__init__` imports `httpx` (raising `ImportError` naming the
/// `bmlib[fulltext]` extra) and builds a default [`FullTextCache`] on disk
/// before anything else. The Rust port takes its transport as an argument, so
/// there is no import to fail, and it **does not touch the filesystem** on
/// construction: [`FullTextService::new`] has no cache, and
/// [`FullTextService::with_default_cache`] is the explicit request for the
/// Python's default — which degrades to no cache, with the same warning, where
/// the directory cannot be created.
///
/// [`FullTextService::timeout`] is retained for API fidelity and reaches no
/// request: the injected transport owns its timeout and its redirect policy,
/// and [`HttpClient`] exposes neither.
pub struct FullTextService {
    client: Arc<dyn HttpClient + Send + Sync>,
    email: String,
    timeout: f64,
    cache: Option<FullTextCache>,
    convert_pdfs: bool,
    ncbi_api_key: Option<String>,
    pdf_extractor: Option<Arc<dyn PdfExtractor + Send + Sync>>,
    /// Faults that are a property of the environment or of bmlib itself rather
    /// than of one article, warned once each keyed by cause, so a second
    /// distinct fault is still reported instead of hiding behind the first.
    /// Per service, not process-wide — a caller that builds a fresh service has
    /// a fresh environment to learn about.
    warned: Mutex<HashSet<String>>,
    log: Mutex<Vec<LogLine>>,
}

impl FullTextService {
    /// A service over `client`, contacting Unpaywall as `email`.
    ///
    /// Starts with **no** cache and no PDF extractor; add either with the
    /// builder methods. Checked before anything else happens in Python so a
    /// failed construction leaves no cache directory behind — a property the
    /// port keeps by construction, since nothing here creates a directory.
    #[must_use]
    pub fn new(client: Arc<dyn HttpClient + Send + Sync>, email: impl Into<String>) -> Self {
        FullTextService {
            client,
            email: email.into(),
            timeout: TIMEOUT,
            cache: None,
            convert_pdfs: true,
            ncbi_api_key: None,
            pdf_extractor: None,
            warned: Mutex::new(HashSet::new()),
            log: Mutex::new(Vec::new()),
        }
    }

    /// Use `cache` — `None` means caching is off.
    #[must_use]
    pub fn with_cache(mut self, cache: Option<FullTextCache>) -> Self {
        self.cache = cache;
        self
    }

    /// Build Python's default cache, degrading to none if it cannot be made.
    #[must_use]
    pub fn with_default_cache(mut self) -> Self {
        self.cache = default_cache();
        self
    }

    /// Set the per-request timeout (see the type's note: it reaches no request).
    #[must_use]
    pub fn with_timeout(mut self, timeout: f64) -> Self {
        self.timeout = timeout;
        self
    }

    /// Whether a cached PDF's text is extracted into `result.html`.
    #[must_use]
    pub fn with_convert_pdfs(mut self, convert_pdfs: bool) -> Self {
        self.convert_pdfs = convert_pdfs;
        self
    }

    /// Send `api_key` with the NCBI requests, moving them into the key's
    /// 10 requests/second allowance instead of the 3 shared by the IP.
    #[must_use]
    pub fn with_ncbi_api_key(mut self, api_key: Option<String>) -> Self {
        self.ncbi_api_key = api_key;
        self
    }

    /// Use `extractor` to turn a cached PDF into text.
    #[must_use]
    pub fn with_pdf_extractor(mut self, extractor: Arc<dyn PdfExtractor + Send + Sync>) -> Self {
        self.pdf_extractor = Some(extractor);
        self
    }

    /// The configured cache, if any.
    #[must_use]
    pub fn cache(&self) -> Option<&FullTextCache> {
        self.cache.as_ref()
    }

    /// The contact address sent to Unpaywall and NCBI.
    #[must_use]
    pub fn email(&self) -> &str {
        &self.email
    }

    /// The configured timeout, which reaches no request.
    #[must_use]
    pub fn timeout(&self) -> f64 {
        self.timeout
    }

    /// Whether PDF text extraction is enabled.
    #[must_use]
    pub fn convert_pdfs(&self) -> bool {
        self.convert_pdfs
    }

    /// The NCBI API key, if configured.
    #[must_use]
    pub fn ncbi_api_key(&self) -> Option<&str> {
        self.ncbi_api_key.as_deref()
    }

    /// Every log line emitted so far, in order.
    #[must_use]
    pub fn log_lines(&self) -> Vec<LogLine> {
        self.log
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone()
    }

    /// The WARNING messages emitted so far.
    #[must_use]
    pub fn warnings(&self) -> Vec<String> {
        self.log_lines()
            .into_iter()
            .filter(LogLine::is_warning)
            .map(|line| line.message)
            .collect()
    }

    /// Forget every log line, so a test can look at one call's output.
    pub fn clear_log(&self) {
        self.log
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clear();
    }

    /// Emit a WARNING the first time `key` is seen on this service.
    ///
    /// The key must name the *cause*, not just the site, so two different
    /// faults at one site are both reported.
    pub fn warn_once(&self, key: &str, message: impl Into<String>) {
        {
            let mut warned = self
                .warned
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            if !warned.insert(key.to_string()) {
                return;
            }
        }
        self.push_log(LogLevel::Warning, message);
    }

    /// Emit a WARNING every time.
    pub fn warn(&self, message: impl Into<String>) {
        self.push_log(LogLevel::Warning, message);
    }

    /// Emit an INFO line.
    pub fn info(&self, message: impl Into<String>) {
        self.push_log(LogLevel::Info, message);
    }

    /// Emit a DEBUG line.
    pub fn debug(&self, message: impl Into<String>) {
        self.push_log(LogLevel::Debug, message);
    }

    fn push_log(&self, level: LogLevel, message: impl Into<String>) {
        let message = message.into();
        let label = match level {
            LogLevel::Debug => "DEBUG",
            LogLevel::Info => "INFO",
            LogLevel::Warning => "WARNING",
        };
        eprintln!("{label}: {message}");
        self.log
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .push(LogLine { level, message });
    }

    /// Fetch full text using known sources and the three-tier fallback chain.
    ///
    /// # Errors
    ///
    /// [`FullTextError::Other`] when no identifiers at all were given, and when
    /// the chain exhausted with no link to degrade to. A tier failing never
    /// aborts the chain; it is recorded on the exhaustion report instead.
    pub fn fetch_fulltext(
        &self,
        request: &FullTextRequest,
    ) -> Result<FullTextResult, FullTextError> {
        let cache_id = request
            .identifier
            .as_deref()
            .filter(|identifier| !identifier.is_empty())
            .map(sanitize_identifier);

        // Cache check — return immediately if content is already on disk.
        //
        // A cache *read* is best-effort exactly as a cache write is: an entry
        // truncated by a killed process or a filesystem fault broke the
        // documented FullTextError-only contract and was a hard stop where
        // re-fetching over the network was available, so one bad file made a
        // paper permanently unfetchable and took a bulk sync down with it.
        let mut cached_pdf_path: Option<String> = None;
        if let (Some(id), Some(cache)) = (cache_id.as_deref(), self.cache.as_ref()) {
            match self.check_cache(cache, id) {
                // DEFECT-FIX (#305). A PDF entry whose text could not be
                // extracted is no longer returned as a hit: it carried
                // `content_kind = none` with no abstract where call 1 returned
                // the abstract, and because the rendered abstract is
                // deliberately never cached, the chain that produced it could
                // never run again for the identifier. The file path is kept and
                // merged into whatever the chain returns, so the cached PDF is
                // still offered on the result.
                Some(hit) if hit.html.is_some() => return Ok(hit),
                Some(hit) => cached_pdf_path = hit.file_path,
                None => {}
            }
        }

        let mut result = self.retrieve(request, cache_id.as_deref())?;
        if result.file_path.is_none() {
            result.file_path = cached_pdf_path;
        }
        Ok(result)
    }

    /// The tier chain. Every return is an exit a caller can observe.
    fn retrieve(
        &self,
        request: &FullTextRequest,
        cache_id: Option<&str>,
    ) -> Result<FullTextResult, FullTextError> {
        // Python's truthiness, once, so every gate below reads what it read.
        let pmc_id = request
            .pmc_id
            .as_deref()
            .filter(|id| !id.is_empty())
            .map(str::to_string);
        let doi = request.doi.as_deref().filter(|doi| !doi.is_empty());
        let pmid = request.pmid.as_str();
        let has_pmid = !pmid.is_empty();
        let has_fallback_identifier = doi.is_some() || has_pmid;

        // A body-less JATS rendering picked up along the way. Held back as a
        // last resort rather than returned, since it carries only the abstract
        // while a later tier may still find the whole article.
        let mut abstract_only: Option<FullTextResult> = None;

        // Every tier below swallows its own failure so the next one still
        // runs; this is what remembers that they did.
        let on_bug = |fault: &TierFault| self.warn_swallowed_bug(fault);
        let mut failures = TierFailures::reported(&on_bug);

        // Tier 0: fetcher-provided sources.
        if !request.fulltext_sources.is_empty() {
            let (result, held_back) =
                self.try_known_sources(&request.fulltext_sources, cache_id, &mut failures);
            abstract_only = held_back;
            if let Some(result) = result {
                return Ok(self.with_abstract_fallback(result, abstract_only.as_ref()));
            }
        }

        // Tier 1a: Europe PMC with a known PMC ID.
        let mut xml_failed = false;
        // Whichever PMC ID we end up holding — the caller's or a resolved one.
        // NCBI's tier below spends it, so it is set before the fetch that may
        // fail, not after.
        let mut resolved_pmc_id: Option<String> = pmc_id.clone();
        if let Some(id) = pmc_id.as_deref() {
            match self.fetch_europepmc(id) {
                Ok((html, true)) => {
                    self.info(format!("Full text retrieved from Europe PMC for {id}"));
                    self.cache_html(&html, cache_id);
                    return Ok(fulltext_result("europepmc", html));
                }
                Ok((html, false)) => {
                    self.info(format!(
                        "Europe PMC XML for {id} has no body — looking further"
                    ));
                    if abstract_only.is_none() {
                        abstract_only = Some(abstract_result("europepmc", html));
                    }
                    // Treated as a failure so the free-PDF lookup below runs.
                    xml_failed = true;
                }
                Err(fault) => {
                    self.debug(format!("Europe PMC failed for {id}: {}", fault.message));
                    failures.record(&fault);
                    xml_failed = true;
                }
            }
        }

        // Tier 1b: discover a PMC ID via the Europe PMC search, then fetch.
        let mut pdf_render_url: Option<String> = None;
        // DEFECT-FIX (#304). The gate was `if not pmc_id`, so a caller-supplied
        // id that Europe PMC could not serve — or could not even parse —
        // suppressed the tier that would have found the right one: supplying an
        // unusable id returned strictly less than omitting it. Gating on
        // *usability* fixes the unambiguous malformed case without letting a
        // discovered id override a well-formed caller id, which is a design
        // question rather than this defect.
        if !pmc_id_is_usable(pmc_id.as_deref()) && has_fallback_identifier {
            let mut discovered_pmc_id: Option<String> = None;
            match self.resolve_pmc_id_and_pdf_url(doi, pmid, &mut failures) {
                Ok((id, url)) => {
                    discovered_pmc_id = id;
                    pdf_render_url = url;
                }
                Err(fault) => {
                    self.debug(format!(
                        "Europe PMC search failed for doi={doi:?} pmid={pmid}: {}",
                        fault.message
                    ));
                    failures.record(&fault);
                }
            }

            // Tier 1b'. The search reports a PMC ID only for what Europe PMC
            // both indexed and holds; NCBI's converter depends on neither, and
            // is asked second because that one search also returned the
            // free-PDF URL Tier 1d needs. It sits outside the search's handler
            // deliberately: a search that failed is precisely when a second,
            // independent resolver is worth having.
            if discovered_pmc_id.is_none() {
                discovered_pmc_id = self.resolve_pmc_id_via_idconv(doi, pmid, &mut failures);
            }

            if let Some(id) = discovered_pmc_id {
                resolved_pmc_id = Some(id.clone());
                match self.fetch_europepmc(&id) {
                    Ok((html, true)) => {
                        self.info(format!(
                            "Full text retrieved from Europe PMC via discovered {id}"
                        ));
                        self.cache_html(&html, cache_id);
                        return Ok(fulltext_result("europepmc", html));
                    }
                    Ok((html, false)) => {
                        self.info(format!(
                            "Europe PMC XML for discovered {id} has no body — looking further"
                        ));
                        if abstract_only.is_none() {
                            abstract_only = Some(abstract_result("europepmc", html));
                        }
                    }
                    Err(fault) => {
                        self.debug(format!(
                            "Europe PMC fetch failed for discovered {id}: {}",
                            fault.message
                        ));
                        failures.record(&fault);
                    }
                }
            }
        }

        // Tier 1c: NCBI's own copy, for whichever PMC ID we hold. Reaching here
        // means Europe PMC gave no body for it — it serves the corpus its
        // inEPMC flag describes, and NCBI serves PMC itself. Ahead of the PDF
        // tier because structured JATS beats a PDF that needs `bmlib[pdf]` to
        // read at all.
        if let Some(id) = resolved_pmc_id.as_deref() {
            match self.fetch_ncbi_pmc(id) {
                Ok((html, true)) => {
                    self.info(format!("Full text retrieved from NCBI PMC for {id}"));
                    self.cache_html(&html, cache_id);
                    return Ok(fulltext_result("ncbi_pmc", html));
                }
                Ok((html, false)) => {
                    self.info(format!(
                        "NCBI PMC XML for {id} has no body — looking further"
                    ));
                    if abstract_only.is_none() {
                        abstract_only = Some(abstract_result("ncbi_pmc", html));
                    }
                }
                Err(fault) => {
                    self.debug(format!("NCBI PMC failed for {id}: {}", fault.message));
                    failures.record(&fault);
                }
            }
        }

        // When XML failed with a known PMC ID, search for a PDF render URL.
        if xml_failed && pdf_render_url.is_none() && has_fallback_identifier {
            match self.resolve_pmc_id_and_pdf_url(doi, pmid, &mut failures) {
                Ok((_id, url)) => pdf_render_url = url,
                Err(fault) => {
                    self.debug(format!("PDF URL resolution failed: {}", fault.message));
                    failures.record(&fault);
                }
            }
        }

        // Tier 1d: Europe PMC PDF render (free PDF when XML is unavailable).
        if let Some(url) = pdf_render_url {
            self.info(format!("PDF available from Europe PMC render: {url}"));
            let mut result = FullTextResult {
                pdf_url: Some(url.clone()),
                ..empty_result("europepmc_pdf")
            };
            self.download_and_cache_pdf(&url, cache_id, &mut result, PdfOrigin::EuropepmcPdf);
            return Ok(self.with_abstract_fallback(result, abstract_only.as_ref()));
        }

        // Tier 2: Unpaywall.
        if let Some(doi) = doi {
            match self.fetch_unpaywall(doi) {
                Ok(pdf_url) => {
                    self.info(format!("PDF URL found via Unpaywall for DOI {doi}"));
                    let mut result = FullTextResult {
                        pdf_url: Some(pdf_url.clone()),
                        ..empty_result("unpaywall")
                    };
                    self.download_and_cache_pdf(
                        &pdf_url,
                        cache_id,
                        &mut result,
                        PdfOrigin::Unpaywall,
                    );
                    return Ok(self.with_abstract_fallback(result, abstract_only.as_ref()));
                }
                Err(fault) => {
                    self.debug(format!("Unpaywall failed for DOI {doi}: {}", fault.message));
                    failures.record(&fault);
                }
            }
        }

        // Tier 3: DOI / PubMed fallback. When a body-less JATS was seen
        // earlier, keep its abstract and hang the link off it — the reader gets
        // both, rather than a bare link.
        let mut web_url: Option<String> = None;
        if let Some(doi) = doi {
            self.info(format!("Falling back to DOI URL for {doi}"));
            // QUIRK: the DOI is interpolated raw here while Unpaywall's URL
            // percent-encodes it (`quote(doi, safe="")`). A DOI carrying a
            // URL-significant character — `#`, `?` — therefore produces a
            // different, and wrong, address in this tier. Reproduced rather
            // than reconciled: the link is what a caller would have pasted, and
            // the Python prints it that way.
            web_url = Some(format!("{DOI_BASE}/{doi}"));
        } else if has_pmid {
            self.info(format!("Falling back to PubMed URL for PMID {pmid}"));
            web_url = Some(format!("{PUBMED_BASE}/{pmid}/"));
        }

        // An empty call is not an exhausted chain: nothing was asked of any
        // source, so there is no failure to summarise and the report below
        // would claim otherwise. Raised ahead of it for that reason.
        if request.fulltext_sources.is_empty() && pmc_id.is_none() && doi.is_none() && !has_pmid {
            return Err(FullTextError::Other("No identifiers provided".to_string()));
        }

        // One report for every empty-handed exit — the two returns below and
        // the raise. Keeping it inside the abstract branch made the *more*
        // complete failure the quieter one: a caller whose every attempt failed
        // got a result shaped exactly like a paper that genuinely has no free
        // full text, and nothing above DEBUG to tell them apart (issue #67).
        let outcome = if abstract_only.is_some() {
            "returning the abstract only"
        } else if web_url.is_some() {
            "nothing was retrieved"
        } else {
            "nothing was retrieved and there is no link to fall back on"
        };
        self.warn(format!(
            "No full text found for doi={doi:?} pmid={pmid} — {outcome}; {}",
            failures.describe()
        ));

        if let Some(mut held_back) = abstract_only {
            if let Some(url) = web_url {
                held_back.web_url = Some(url);
            }
            return Ok(held_back);
        }

        match web_url {
            // Identifiers were given — the empty call raised above — so this is
            // an exhausted chain with no link to degrade to. Saying "no
            // identifiers provided" here sent the reader looking in the wrong
            // place.
            None => Err(FullTextError::Other(format!(
                "Nothing retrieved and no DOI or PMID to fall back on — {}",
                failures.describe()
            ))),
            Some(url) => Ok(FullTextResult {
                web_url: Some(url),
                ..empty_result(if doi.is_some() { "doi" } else { "pubmed" })
            }),
        }
    }

    /// Carry a held-back abstract onto a result that has no text of its own.
    ///
    /// A PDF tier counts as a success as soon as it has a URL — the download
    /// may have failed, or there may have been no cache to extract from.
    /// Returning that alone would discard an abstract already in hand and leave
    /// the reader a bare link, which is the outcome the whole fallback exists to
    /// prevent. The link stays on the result either way.
    fn with_abstract_fallback(
        &self,
        mut result: FullTextResult,
        abstract_only: Option<&FullTextResult>,
    ) -> FullTextResult {
        let has_text = result.html.as_deref().is_some_and(|html| !html.is_empty());
        let Some(abstract_only) = abstract_only else {
            return result;
        };
        if has_text {
            return result;
        }
        result.html = abstract_only.html.clone();
        result.content_kind = ContentKind::Abstract;
        self.info("PDF yielded no text — pairing the link with the abstract-only rendering");
        result
    }

    /// Try fetcher-provided full-text sources in priority order.
    ///
    /// Priority: xml (JATS) > pdf > html, and the sort is stable so two entries
    /// of one format keep the order the fetcher gave them.
    ///
    /// Returns `(result, abstract_only)`. `result` is the best source that
    /// worked — JATS full text, a PDF, or a link — or `None` when every entry
    /// failed; only a [`ContentKind::Fulltext`] means article text was actually
    /// retrieved. `abstract_only` holds a body-less JATS rendering if one was
    /// seen. It is never worth stopping on, because a publisher that serves an
    /// abstract-only JATS (medRxiv does) generally serves the complete article
    /// as a PDF alongside it.
    fn try_known_sources(
        &self,
        sources: &[FullTextSourceEntry],
        cache_id: Option<&str>,
        failures: &mut TierFailures<'_>,
    ) -> (Option<FullTextResult>, Option<FullTextResult>) {
        let priority = |format: &str| match format {
            "xml" => 0,
            "pdf" => 1,
            "html" => 2,
            _ => 99,
        };
        let mut sorted: Vec<&FullTextSourceEntry> = sources.iter().collect();
        sorted.sort_by_key(|entry| priority(&entry.format));

        let mut abstract_only: Option<FullTextResult> = None;
        for entry in sorted {
            if entry.format == "xml" {
                match self.fetch_jats_xml(&entry.url) {
                    Ok((html, false)) => {
                        // Not cached: a later fetch may find a populated
                        // document, and caching this would make the abstract
                        // permanent.
                        self.info(format!(
                            "JATS XML from {} has no body — keeping it only as a fallback and looking for the full article",
                            entry.source
                        ));
                        if abstract_only.is_none() {
                            abstract_only = Some(abstract_result(&entry.source, html));
                        }
                        continue;
                    }
                    Ok((html, true)) => {
                        self.info(format!("Full text from JATS XML ({})", entry.source));
                        self.cache_html(&html, cache_id);
                        return (Some(fulltext_result(&entry.source, html)), abstract_only);
                    }
                    Err(fault) => {
                        self.debug(format!(
                            "Known source {} ({}) failed: {}",
                            entry.source, entry.url, fault.message
                        ));
                        failures.record(&fault);
                        continue;
                    }
                }
            } else if entry.format == "pdf" {
                self.info(format!("PDF available from {}", entry.source));
                let mut result = FullTextResult {
                    pdf_url: Some(entry.url.clone()),
                    ..empty_result(&entry.source)
                };
                self.download_and_cache_pdf(
                    &entry.url,
                    cache_id,
                    &mut result,
                    PdfOrigin::KnownSource,
                );
                return (Some(result), abstract_only);
            } else if entry.format == "html" {
                self.info(format!("HTML source from {}", entry.source));
                return (
                    Some(FullTextResult {
                        web_url: Some(entry.url.clone()),
                        ..empty_result(&entry.source)
                    }),
                    abstract_only,
                );
            }
            // QUIRK: an entry whose `format` is not one of the three matches no
            // branch, so the loop moves on with nothing logged and nothing
            // recorded on `failures` — the entry is *silently* skipped, and an
            // exhaustion report describing a run of such entries reads as "no
            // attempt reported a failure". Reproduced from the Python, where a
            // typo in a fetcher's `format` is invisible.
            // An unknown format matches no branch and the loop continues.
        }

        (None, abstract_only)
    }

    // --- Cache helpers ------------------------------------------------------

    /// Return a cached result, or `None` for a miss.
    ///
    /// Only HTML that came from a JATS `<body>` is ever written to the cache,
    /// so a cached HTML hit is always full text. Text extracted from a PDF is
    /// not cached — it is re-derived here from the cached PDF, so a cache hit
    /// carries the same `html` and `content_kind` as the original retrieval
    /// instead of silently dropping to a bare file path.
    ///
    /// The PDF branch's result may carry **no** text: that is the signal
    /// [`FullTextService::fetch_fulltext`] uses to treat the entry as a miss for
    /// `content_kind` purposes while keeping `file_path` (defect #305).
    fn check_cache(&self, cache: &FullTextCache, cache_id: &str) -> Option<FullTextResult> {
        let html_path = cache
            .html_dir()
            .join(format!("{}.html", safe_filename(cache_id)));
        let html = cache.get_html(cache_id);
        // Python's `get_html` raises for an entry it cannot decode, which is
        // what the best-effort handler caught. Rust's returns `None` for both
        // an absent and an unreadable entry, so presence is what tells them
        // apart — and an entry that is a directory fails the read the same way
        // a truncated one does.
        if html.is_none() && html_path.exists() {
            self.warn(format!(
                "Could not read the cached full text for {cache_id} (Unreadable: the entry could not be read back); re-fetching."
            ));
            self.debug(format!("Cache read failed for {cache_id}"));
            // Moved aside, not deleted: an undecodable HTML entry is consulted
            // ahead of the PDF entry, so left where it is it hides a good PDF
            // behind it and the same warning and fetch repeat on every run for
            // ever.
            self.quarantine_cache_entry(cache, cache_id);
            // The whole cache check is abandoned, not merely the HTML lookup:
            // in the Python the read *raised*, so the PDF branch below was
            // never reached and the run went to the network. Reproduced, and it
            // is what makes the next run the one that finds the PDF the
            // quarantine just unhid.
            return None;
        }
        // QUIRK: Python's test is `if html:`, so a cached entry holding the
        // **empty string** is a miss, not a hit — the lookup falls through to
        // the PDF entry and then to the network, and a PDF tier can overwrite
        // the very file that was read. Reproduced: `Some("")` is not a hit.
        if let Some(html) = html {
            if !html.is_empty() {
                self.info(format!("Cache hit (HTML) for {cache_id}"));
                return Some(fulltext_result("cached", html));
            }
        }
        if let Some(pdf_path) = cache.get_pdf(cache_id) {
            self.info(format!("Cache hit (PDF) for {cache_id}"));
            let mut result = empty_result("cached");
            result.file_path = Some(pdf_path.display().to_string());
            self.attach_pdf_text(&pdf_path, &mut result);
            return Some(result);
        }
        None
    }

    /// Move an unreadable cache entry aside, never raising.
    ///
    /// `FullTextCache::quarantine` is already best-effort in Python and
    /// infallible in Rust — it reports what it moved rather than raising — so
    /// the Python's once-per-exception-type warning for a cache that cannot be
    /// tidied has no reachable cause here.
    fn quarantine_cache_entry(&self, cache: &FullTextCache, cache_id: &str) {
        let _ = cache.quarantine(cache_id);
    }

    /// Report an unwritable cache, once per (site, cause).
    ///
    /// Best-effort: the content is already in hand, so a write that fails costs
    /// nothing this call. It costs every *later* call — a read-only cache
    /// directory or a full disk means the whole corpus is re-fetched over the
    /// network on every run, permanently. Keyed by cause and not by the site
    /// alone, so a transient permission error early in a run does not
    /// permanently silence a genuine bmlib defect inside `save_pdf`.
    fn warn_cache_write_failed(&self, kind: &str, message: &str) {
        self.warn_once(
            &format!("cache-write:{kind}"),
            format!(
                "Could not write to the full-text cache ({kind}: {message}); retrieval still works, but nothing is being cached, so every run re-fetches."
            ),
        );
    }

    /// Report a tier failure that can only mean a bmlib defect.
    ///
    /// Once per cause per service: a defect that hits one tier hits it for every
    /// article, so a line per article would be unreadable at exactly the moment
    /// it mattered — but a *second*, different defect must still be reported
    /// rather than hidden by the first. This must not raise: it runs inside a
    /// tier's failure handling.
    fn warn_swallowed_bug(&self, fault: &TierFault) {
        let name = fault.name;
        self.warn_once(
            &format!("bug:{name}"),
            format!(
                "A full-text tier failed with {name} ({}), which bmlib does not raise deliberately — this is a defect, possibly provoked by an unexpected API response. Full text may be silently degraded for every article in this run while later tiers keep succeeding. Run with DEBUG logging for the traceback and please report it; further {name} failures will not be repeated.",
                fault.message
            ),
        );
    }

    /// Save HTML to the disk cache if caching is enabled.
    ///
    /// Failing to write costs only a re-fetch next run — the HTML is already in
    /// hand and is returned either way — so no cache means nothing to say here
    /// beyond the warning construction has already emitted.
    fn cache_html(&self, html: &str, cache_id: Option<&str>) {
        let (Some(cache_id), Some(cache)) = (cache_id, self.cache.as_ref()) else {
            return;
        };
        if cache_id.is_empty() {
            return;
        }
        if let Err(error) = cache.save_html(html, cache_id) {
            self.warn_cache_write_failed(&format!("{:?}", error.kind()), &error.to_string());
            self.debug(format!("Failed to cache HTML for {cache_id}"));
        }
    }

    /// Download a PDF and save it to the disk cache.
    ///
    /// On success, sets `result.file_path` to the cached file and — when
    /// `convert_pdfs` is on and a backend is available — `result.html` and
    /// `result.content_kind` from the PDF's extracted text. On failure
    /// (transport failure, non-200, invalid PDF) leaves the result unchanged so
    /// the caller can still use `result.pdf_url` as a fallback.
    ///
    /// Returns without downloading at all when there is nowhere to put the
    /// file: no cache, or no `identifier` to key one by. The URL stays on the
    /// result in both cases. The no-cache case is DEBUG because the
    /// construction warning already named that consequence, and it is *not*
    /// gated on `convert_pdfs`: the download is skipped either way, so
    /// `file_path` is lost even for a caller who turned extraction off
    /// precisely because they wanted the file.
    ///
    /// The three failure causes are reported once per `(origin, cause)` rather
    /// than per article, and the level was chosen from a measured rate against
    /// a rule fixed before the numbers landed: under 5% of attempts a
    /// per-article WARNING is affordable, and at or above it a bulk run's log
    /// would be drowned. Measured 2026-08-11 with
    /// `scripts/sample_free_pdf_urls.py --target 150 --per-host-interval 4.0`:
    /// europepmc 150 probed / 1 failed / 0.7%; unpaywall 28 / 18 / **64.3%**
    /// (http-403: 4, not-a-pdf: 14); biorxiv 150 / 1 / 0.7%. The worst
    /// population's CI lower bound (45.8%) is roughly nine times the 5%
    /// threshold, so this variant was selected; the key is per cause because 14
    /// of Unpaywall's 18 were landing pages rather than HTTP failures.
    fn download_and_cache_pdf(
        &self,
        pdf_url: &str,
        cache_id: Option<&str>,
        result: &mut FullTextResult,
        origin: PdfOrigin,
    ) {
        let Some(cache) = self.cache.as_ref() else {
            self.debug(format!(
                "The full-text cache could not be created, so {pdf_url} is not downloaded — the URL is left on the result, and there is no file to extract text from"
            ));
            return;
        };
        let Some(cache_id) = cache_id else {
            if self.convert_pdfs {
                self.info(format!(
                    "convert_pdfs is on but no identifier was given — a PDF is only extracted once cached, so {pdf_url} is left as a URL"
                ));
            }
            return;
        };

        let response = match self.http_get(pdf_url) {
            Ok(response) => response,
            Err(fault) => {
                self.report_pdf_download_exception(&result.source, origin, pdf_url, &fault);
                return;
            }
        };
        if response.status != 200 {
            self.report_pdf_download_failure(
                &result.source,
                origin,
                pdf_url,
                &format!("HTTP {}", response.status),
                &format!("http-{}", response.status),
            );
            return;
        }

        // **The bytes, not a decoded string.** The cache validates the `%PDF`
        // prefix and nothing else, so a lossy decode on this path is what wrote a
        // silently corrupted file: every non-UTF-8 byte became U+FFFD and the
        // prefix still matched.
        let (path, outcome) = self.save_pdf_to_cache(cache, &response.body, cache_id);
        let Some(path) = path else {
            if outcome == PdfSaveOutcome::NotAPdf {
                self.report_pdf_download_failure(
                    &result.source,
                    origin,
                    pdf_url,
                    "the response is not a PDF",
                    "not-a-pdf",
                );
            }
            // Otherwise "write-failed": warn_cache_write_failed has already
            // spoken, and it names the right cause. Saying anything more here
            // would blame the publisher for a read-only directory.
            return;
        };
        result.file_path = Some(path.display().to_string());
        self.info(format!("PDF cached to {}", path.display()));
        // Deliberately not under the download's handler. Extraction runs after
        // `file_path` is set, so an exception escaping it there was reported as
        // a download failure — "there is no file and no extracted text" about an
        // article whose file is cached and on the result. Caught rather than
        // allowed to propagate, because the download did succeed: letting it
        // reach the tier handler would lose a perfectly good cached PDF over a
        // failure to extract text from it.
        self.attach_pdf_text(&path, result);
    }

    /// Report a PDF download that failed before answering.
    ///
    /// The environment, not the server: a lost network or a full disk fails
    /// *every* article once it starts failing, so this is one-shot per
    /// `(origin, cause)` and needed no measurement to decide. The wording says
    /// the fault "will affect every article served this way", which is an
    /// assertion about the environment and would be measurably false said of a
    /// publisher's 404.
    fn report_pdf_download_exception(
        &self,
        source: &str,
        origin: PdfOrigin,
        pdf_url: &str,
        fault: &TierFault,
    ) {
        let name = fault.name;
        self.warn_once(
            &format!("pdf-download:{}:{name}", origin.as_str()),
            format!(
                "Could not download a {source} PDF ({name}: {}). The URL is left on the result, but there is no file and no extracted text, and this will affect every article served this way. Further {name} failures will not be repeated.",
                fault.message
            ),
        );
        self.debug(format!(
            "PDF download failed for {pdf_url}: {}",
            fault.message
        ));
    }

    /// Report a server-side PDF download failure.
    ///
    /// Once per `(origin, cause)`, with the per-article detail at DEBUG. The
    /// message says the report is one-shot; it does **not** say the failure is
    /// common — the earlier wording ("this is common enough that it is reported
    /// once") was measurably false for Europe PMC, which the table above records
    /// with zero server-side failures and which became this line's dominant
    /// caller once Tier 1d's availability allow-list was widened.
    fn report_pdf_download_failure(
        &self,
        source: &str,
        origin: PdfOrigin,
        pdf_url: &str,
        reason: &str,
        cause: &str,
    ) {
        self.warn_once(
            &format!("pdf-download:{}:{cause}", origin.as_str()),
            format!(
                "Could not download a {source} PDF ({reason}; first seen at {pdf_url}). The URL is left on the result, but there is no file and no extracted text. This is reported once — run with DEBUG logging to see every affected article."
            ),
        );
        self.debug(format!("PDF download failed ({cause}) for {pdf_url}"));
    }

    /// Write a downloaded PDF to the disk cache, best-effort.
    ///
    /// Split out of the download so a failed *write* is reported like
    /// `cache_html`'s. Left inside the download's own handler it was
    /// indistinguishable from a failed fetch, logged as "PDF download failed",
    /// and invisible above DEBUG. The two failures are told apart rather than
    /// both returning nothing, because the caller reports them and blaming a
    /// read-only directory on the publisher's bytes is the mistake this method
    /// exists to avoid.
    fn save_pdf_to_cache(
        &self,
        cache: &FullTextCache,
        data: &[u8],
        cache_id: &str,
    ) -> (Option<PathBuf>, PdfSaveOutcome) {
        match cache.save_pdf(data, cache_id) {
            Err(error) => {
                self.warn_cache_write_failed(&format!("{:?}", error.kind()), &error.to_string());
                self.debug(format!("Failed to cache PDF for {cache_id}"));
                (None, PdfSaveOutcome::WriteFailed)
            }
            Ok(None) => {
                self.debug(format!("PDF failed magic-byte validation for {cache_id}"));
                (None, PdfSaveOutcome::NotAPdf)
            }
            Ok(Some(path)) => (Some(path), PdfSaveOutcome::Saved),
        }
    }

    /// Extract a cached PDF's text into `result.html`.
    ///
    /// A no-op when `convert_pdfs` is off or `result.html` is already
    /// populated — an earlier tier's text is never overwritten. Otherwise
    /// best-effort: a backend that cannot be constructed (the `bmlib[pdf]`
    /// extra missing, or broken) or an unreadable PDF leaves the result
    /// untouched, so the caller still has the PDF itself. `result.pdf_url` and
    /// `result.file_path` are deliberately left in place — extracted text
    /// recovers the prose but not figures, tables or layout.
    ///
    /// Every way this can come up empty is logged at WARNING: a scanned PDF
    /// that yields nothing is invisible otherwise, and a partial extraction must
    /// not be mistaken for a whole article.
    fn attach_pdf_text(&self, pdf_path: &Path, result: &mut FullTextResult) {
        let has_html = result.html.as_deref().is_some_and(|html| !html.is_empty());
        if !self.convert_pdfs || has_html {
            return;
        }
        let Some(extractor) = self.pdf_extractor.as_ref() else {
            self.warn_once(
                "pdf-backend:NoBackend",
                "convert_pdfs is enabled but no PDF backend is usable (NoBackend: none is configured); PDFs will be returned as links only. Install bmlib[pdf] if the extra is missing.",
            );
            return;
        };

        match extractor.extract(pdf_path) {
            Err(PdfExtractError::Backend { name, message }) => {
                // Report what was actually raised rather than asserting the
                // cause, so a broken backend install is not misreported as an
                // uninstalled one.
                self.warn_once(
                    &format!("pdf-backend:{name}"),
                    format!(
                        "convert_pdfs is enabled but no PDF backend is usable ({name}: {message}); PDFs will be returned as links only. Install bmlib[pdf] if the extra is missing."
                    ),
                );
            }
            Err(PdfExtractError::Conversion(message)) => {
                self.warn(format!(
                    "PDF text extraction failed for {}: {message}",
                    pdf_path.display()
                ));
            }
            Ok(conversion) => {
                if !conversion.success {
                    self.warn(format!(
                        "PDF text extraction failed for {}: {}",
                        pdf_path.display(),
                        conversion.error_message.unwrap_or_default()
                    ));
                    return;
                }
                if conversion.html.is_empty() {
                    let warnings = if conversion.warnings.is_empty() {
                        "no warnings reported".to_string()
                    } else {
                        conversion
                            .warnings
                            .iter()
                            .take(3)
                            .cloned()
                            .collect::<Vec<_>>()
                            .join("; ")
                    };
                    self.warn(format!(
                        "PDF {} yielded no extractable text over {} page(s) — likely a scan; {warnings}",
                        pdf_path.display(),
                        conversion.page_count
                    ));
                    return;
                }
                if !conversion.is_complete() {
                    self.warn(format!(
                        "PDF {} extracted only {} of {} pages — the attached text is incomplete",
                        pdf_path.display(),
                        conversion.converted_pages,
                        conversion.page_count
                    ));
                }
                result.html = Some(conversion.html);
                result.content_kind = ContentKind::Extracted;
                self.info(format!(
                    "Extracted {} chars of text from PDF {}",
                    conversion.char_count,
                    pdf_path.display()
                ));
            }
        }
    }

    // --- Fetch helpers ------------------------------------------------------

    /// Fetch JATS XML from an arbitrary URL and parse to HTML.
    ///
    /// Returns the rendered HTML and whether the document actually had a body.
    /// A body-less document renders to little more than the abstract, so the
    /// caller must keep looking for the real full text.
    ///
    /// # Errors
    ///
    /// [`TierFault::unavailable`] on a 404 — a fetcher's stored URL going stale
    /// is common, and counting it as a fault would inflate the one bucket the
    /// exhaustion report asks the operator to act on.
    fn fetch_jats_xml(&self, url: &str) -> Result<(String, bool), TierFault> {
        let response = self.http_get(url)?;
        if response.status == 404 {
            return Err(TierFault::unavailable(format!("JATS XML not found: {url}")));
        }
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("JATS XML fetch failed: HTTP {}", response.status),
            ));
        }
        let article = parse_with_pmc_id(response_text(&response)?, "")
            .map_err(|error| TierFault::fault("SAXParseException", error.to_string()))?;
        Ok((render_jats_html(&article), article.has_body))
    }

    /// Search Europe PMC to discover a PMC ID and a free PDF URL.
    ///
    /// The DOI is preferred when present; the PMID is used when there is no
    /// DOI. A search that finds no record is noted on `failures` as an absence.
    ///
    /// # Errors
    ///
    /// [`TierFault::fault`] on a non-200 response. Reported rather than returned
    /// as `(None, None)`, which is also what an empty result set looks like: an
    /// unreachable Europe PMC then read as "this paper has no free full text",
    /// the misdiagnosis issue #67 exists to prevent.
    fn resolve_pmc_id_and_pdf_url(
        &self,
        doi: Option<&str>,
        pmid: &str,
        failures: &mut TierFailures<'_>,
    ) -> Result<(Option<String>, Option<String>), TierFault> {
        let query = if let Some(doi) = doi {
            format!("DOI:{doi}")
        } else if !pmid.is_empty() {
            format!("EXT_ID:{pmid}")
        } else {
            return Ok((None, None));
        };

        let url = format!(
            "{EUROPE_PMC_BASE}/search?query={}&format=json&resultType=core&pageSize=1",
            quote(&query, ":")
        );
        let response = self.http_get(&url)?;
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("Europe PMC search HTTP {}", response.status),
            ));
        }

        let data = self.json_body(&response)?;
        let Some(results) = epmc_search_records(&data)? else {
            failures.note_absence();
            return Ok((None, None));
        };
        let hit = &results[0];
        let hit = match hit {
            Value::Object(hit) => hit,
            _ => {
                return Err(TierFault::defect(
                    "AttributeError",
                    "a Europe PMC search result is not an object",
                ))
            }
        };

        let pmc_id = if matches!(hit.get("inEPMC"), Some(Value::String(value)) if value == "Y") {
            match hit.get("pmcid") {
                None | Some(Value::Null) => None,
                Some(Value::String(pmcid)) => Some(pmcid.clone()),
                Some(_) => {
                    return Err(TierFault::defect(
                        "AttributeError",
                        "a Europe PMC search result's 'pmcid' is not a string",
                    ))
                }
            }
        } else {
            None
        };

        Ok((pmc_id, extract_free_pdf_url(hit)))
    }

    /// An NCBI query string, with the identification NCBI asks of every caller.
    ///
    /// `tool` and `email` identify bmlib; `api_key` is sent only when
    /// configured, and moves the request into the key's allowance rather than
    /// the 3 requests/second shared by everything on the IP.
    ///
    /// `base` is a parameter because the ID Converter and `efetch` are two
    /// different endpoints that share this identification — a single hard-coded
    /// base sent every `efetch` to the converter's path.
    fn ncbi_url(&self, base: &str, params: &[(&str, &str)]) -> String {
        let mut pairs: Vec<(String, String)> = params
            .iter()
            .map(|(key, value)| (quote(key, ""), quote(value, "")))
            .collect();
        pairs.push(("tool".to_string(), quote(EUTILS_TOOL_NAME, "")));
        pairs.push(("email".to_string(), quote(&self.email, "")));
        if let Some(api_key) = &self.ncbi_api_key {
            pairs.push(("api_key".to_string(), quote(api_key, "")));
        }
        let query = pairs
            .into_iter()
            .map(|(key, value)| format!("{key}={value}"))
            .collect::<Vec<_>>()
            .join("&");
        format!("{base}?{query}")
    }

    /// Resolve a PMC ID through NCBI's ID Converter.
    ///
    /// The second source for a PMC ID, consulted only when the Europe PMC
    /// search returned none. Europe PMC reports one only when it both indexed
    /// the paper and flagged its full text as available there; the converter
    /// depends on neither. Asked by PMID when there is one — an exact numeric
    /// key — and by DOI otherwise, since a DOI-formatting miss is one of the
    /// divergences this recovers.
    ///
    /// It never fails the call: the caller has a free-PDF URL in hand by this
    /// point, and a failure would cost it. A converter that could not be
    /// reached is still recorded as a *fault* on `failures`, and one that
    /// answered "no such record" as an absence — returning nothing for both let
    /// an outage read as an ordinary paywalled paper (issue #67).
    fn resolve_pmc_id_via_idconv(
        &self,
        doi: Option<&str>,
        pmid: &str,
        failures: &mut TierFailures<'_>,
    ) -> Option<String> {
        let ids = if !pmid.is_empty() {
            pmid.to_string()
        } else {
            doi?.to_string()
        };

        let url = self.ncbi_url(
            NCBI_IDCONV_URL,
            &[("ids", ids.as_str()), ("format", "json")],
        );
        let response = match self.http_get(&url) {
            Ok(response) => response,
            Err(fault) => {
                self.debug(format!(
                    "ID Converter lookup failed for {ids}: {}",
                    fault.message
                ));
                failures.record(&fault);
                return None;
            }
        };
        if response.status != 200 {
            // Raised, not returned: the handler files it as a fault and still
            // returns nothing, so the caller is unaffected while an unreachable
            // converter stops counting as an absence.
            failures.record(&TierFault::fault(
                "FullTextError",
                format!("ID Converter HTTP {} for {ids}", response.status),
            ));
            return None;
        }
        let body = match self.json_body(&response) {
            Ok(body) => body,
            Err(fault) => {
                failures.record(&fault);
                return None;
            }
        };
        let Value::Object(body) = &body else {
            failures.record(&TierFault::defect(
                "AttributeError",
                "the ID Converter returned a body that is not an object",
            ));
            return None;
        };
        let Some(records) = body.get("records") else {
            failures.note_absence();
            return None;
        };
        if !truthy(records) {
            failures.note_absence();
            return None;
        }
        // `records = resp.json().get("records") or []`, then `records[0]`.
        // Several shapes reach that subscript and each raises its own Python
        // exception: a dict raises KeyError on key `0` (JSON keys are strings),
        // a string yields a character whose `.get` raises AttributeError, and a
        // number or boolean is not subscriptable at all. The names matter —
        // three of the five are `_BUG_TYPES` members and the exhaustion report
        // prints them.
        let records = match records {
            Value::Array(records) => records,
            Value::Object(_) => {
                failures.record(&TierFault::defect(
                    "KeyError",
                    "the ID Converter's 'records' is an object, and records[0] is its key 0",
                ));
                return None;
            }
            Value::String(_) => {
                failures.record(&TierFault::defect(
                    "AttributeError",
                    "the ID Converter's 'records' is a string, and records[0] has no .get",
                ));
                return None;
            }
            _ => {
                failures.record(&TierFault::defect(
                    "TypeError",
                    "the ID Converter's 'records' is not subscriptable",
                ));
                return None;
            }
        };
        let record = match records.first() {
            Some(Value::Object(record)) => record,
            Some(_) => {
                failures.record(&TierFault::defect(
                    "AttributeError",
                    "an ID Converter record is not an object",
                ));
                return None;
            }
            None => {
                failures.note_absence();
                return None;
            }
        };

        if matches!(record.get("status"), Some(Value::String(status)) if status == "error") {
            let errmsg = record.get("errmsg").map(python_str).unwrap_or_default();
            self.debug(format!("ID Converter has no record for {ids}: {errmsg}"));
            failures.note_absence();
            return None;
        }
        // QUIRK: the flag is read through Python's `str()`, so the answer
        // depends on how the JSON happened to be typed. `"false"`, `"False"`
        // and `false` all mean not-live, while `0`, `"0"`, `null` and `""` all
        // mean live — `str(None)` is `"None"`, which is not `"false"`.
        // Reproduced rather than normalised: the converter documents the field
        // as a string, so anything else is its own misdeposit and the port
        // records what the Python recorded.
        // Reported as the string "false" for a record PMC no longer serves —
        // and a JSON `false` stringifies to "False", which lowercases to the
        // same answer.
        let live = record.get("live").map_or("true".to_string(), python_str);
        if live.to_lowercase() == "false" {
            self.debug(format!("ID Converter record for {ids} is no longer live"));
            failures.note_absence();
            return None;
        }

        match record.get("pmcid") {
            Some(Value::String(pmcid)) if pmc_id_pattern().is_match(pmcid) => {
                self.info(format!(
                    "PMC ID {pmcid} resolved via NCBI ID Converter for {ids}"
                ));
                Some(pmcid.clone())
            }
            other => {
                if other.is_some_and(truthy) {
                    self.warn(format!(
                        "ID Converter returned an unusable PMC ID: {}",
                        other.map(python_str).unwrap_or_default()
                    ));
                    // A malformed id is the converter misbehaving, not an
                    // absence — the record exists and says something unusable.
                    failures.record(&TierFault::fault(
                        "FullTextError",
                        format!(
                            "Unusable PMC ID: {}",
                            other.map(python_str).unwrap_or_default()
                        ),
                    ));
                } else {
                    failures.note_absence();
                }
                None
            }
        }
    }

    /// Fetch JATS XML from Europe PMC and parse to HTML.
    ///
    /// Returns the rendered HTML and whether the document had a body.
    ///
    /// # Errors
    ///
    /// [`TierFault`] for a bad id, a 404 (as an absence) or a non-200.
    fn fetch_europepmc(&self, pmc_id: &str) -> Result<(String, bool), TierFault> {
        let normalized = normalise_pmc_id(pmc_id).map_err(TierFault::from)?;
        let url = format!("{EUROPE_PMC_BASE}/{normalized}/fullTextXML");

        let response = self.http_get(&url)?;
        if response.status == 404 {
            return Err(TierFault::unavailable(format!(
                "No full text in Europe PMC for {normalized}"
            )));
        }
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("Europe PMC HTTP {}", response.status),
            ));
        }

        let article = parse_with_pmc_id(response_text(&response)?, &normalized)
            .map_err(|error| TierFault::fault("SAXParseException", error.to_string()))?;
        Ok((render_jats_html(&article), article.has_body))
    }

    /// Fetch a PMC article from NCBI's own copy via E-utilities `efetch`.
    ///
    /// Europe PMC's `fullTextXML` serves the corpus its `inEPMC` flag
    /// describes; NCBI serves PMC itself. For an article PMC holds and Europe
    /// PMC does not, this is the only source that answers.
    ///
    /// # Errors
    ///
    /// [`TierFault::fault`] on a bad id or a non-200 response.
    /// [`TierFault::unavailable`] on a reply carrying no article at all —
    /// efetch's answer for an article whose publisher does not release XML. It
    /// is HTTP 200 and parses cleanly into a document with no body *and* no
    /// abstract. Returned rather than refused, it would be promoted to the
    /// last-resort abstract and become near-empty HTML labelled as one. Raised
    /// as an absence rather than a fault because the source answered.
    fn fetch_ncbi_pmc(&self, pmc_id: &str) -> Result<(String, bool), TierFault> {
        let normalized = normalise_pmc_id(pmc_id).map_err(TierFault::from)?;
        let numeric = normalized.strip_prefix("PMC").unwrap_or(&normalized);
        let url = self.ncbi_url(
            EUTILS_EFETCH_URL,
            &[("db", "pmc"), ("id", numeric), ("retmode", "xml")],
        );

        let response = self.http_get(&url)?;
        if response.status == 404 {
            return Err(TierFault::unavailable(format!(
                "NCBI PMC has no record for {normalized}"
            )));
        }
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("NCBI PMC HTTP {}", response.status),
            ));
        }

        let article = parse_with_pmc_id(response_text(&response)?, &normalized)
            .map_err(|error| TierFault::fault("SAXParseException", error.to_string()))?;
        if !article.has_body && article.abstract_sections.is_empty() {
            return Err(TierFault::unavailable(format!(
                "NCBI PMC returned no article content for {normalized}"
            )));
        }
        Ok((render_jats_html(&article), article.has_body))
    }

    /// Query Unpaywall for an open-access PDF URL.
    ///
    /// # Errors
    ///
    /// [`TierFault::fault`] on a non-200 response other than 404.
    /// [`TierFault::unavailable`] when Unpaywall has no record of the DOI, or
    /// holds one with no open-access location. Both are the service answering
    /// that there is nothing free — the ordinary outcome for most papers, and
    /// not something to act on.
    fn fetch_unpaywall(&self, doi: &str) -> Result<String, TierFault> {
        let url = format!(
            "{UNPAYWALL_BASE}/{}?email={}",
            quote(doi, ""),
            quote(&self.email, "")
        );
        let response = self.http_get(&url)?;
        if response.status == 404 {
            return Err(TierFault::unavailable(format!(
                "DOI not found in Unpaywall: {doi}"
            )));
        }
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("Unpaywall HTTP {}", response.status),
            ));
        }

        let data = self.json_body(&response)?;
        let Value::Object(data) = &data else {
            return Err(TierFault::defect(
                "AttributeError",
                "Unpaywall returned a body that is not an object",
            ));
        };
        match pick_oa_pdf_url(data)? {
            Some(pdf_url) => Ok(pdf_url),
            None => Err(TierFault::unavailable(format!(
                "No open-access PDF found for DOI {doi}"
            ))),
        }
    }

    /// One GET through the injected transport.
    fn http_get(&self, url: &str) -> Result<HttpResponse, TierFault> {
        self.client
            .get(url)
            .map_err(|error| TierFault::fault(fetch_error_name(&error), error.to_string()))
    }

    /// One response body decoded as JSON.
    fn json_body(&self, response: &HttpResponse) -> Result<Value, TierFault> {
        serde_json::from_str(response_text(response)?)
            .map_err(|error| TierFault::fault("JSONDecodeError", error.to_string()))
    }
}

/// A result with nothing on it yet.
fn empty_result(source: impl Into<String>) -> FullTextResult {
    FullTextResult {
        source: source.into(),
        html: None,
        pdf_url: None,
        web_url: None,
        file_path: None,
        content_kind: ContentKind::None,
    }
}

/// A result carrying a JATS body.
fn fulltext_result(source: &str, html: String) -> FullTextResult {
    FullTextResult {
        html: Some(html),
        content_kind: ContentKind::Fulltext,
        ..empty_result(source)
    }
}

/// A result carrying a body-less JATS rendering — an abstract, never full text.
fn abstract_result(source: &str, html: String) -> FullTextResult {
    FullTextResult {
        html: Some(html),
        content_kind: ContentKind::Abstract,
        ..empty_result(source)
    }
}

/// The `resultList.result` array from a decoded Europe PMC search body.
///
/// Reproduces Python's `data.get("resultList", {}).get("result", [])` walk and
/// classifies every shape that raises through it as the defect shape it is:
/// a body that is not an object, a present-but-non-object `resultList`, and a
/// truthy `result` that is not a list (a dict raises `KeyError` on `results[0]`,
/// a string raises `AttributeError` on the `hit.get` that follows, and a number
/// or boolean raises `TypeError`). `Ok(None)` is the empty result set — which
/// is an *absence*, not a fault.
fn epmc_search_records(data: &Value) -> Result<Option<&Vec<Value>>, TierFault> {
    let Value::Object(data) = data else {
        return Err(TierFault::defect(
            "AttributeError",
            "Europe PMC returned a body that is not an object",
        ));
    };
    let records = match data.get("resultList") {
        // `.get(k, {})`: absent means an empty object.
        None => None,
        Some(Value::Object(result_list)) => result_list.get("result"),
        // `.get(k, {})` returns the present null, and the next `.get` raises.
        Some(_) => {
            return Err(TierFault::defect(
                "AttributeError",
                "Europe PMC's 'resultList' is not an object",
            ))
        }
    };
    let Some(records) = records else {
        return Ok(None);
    };
    if !truthy(records) {
        return Ok(None);
    }
    match records {
        Value::Array(records) => Ok(Some(records)),
        Value::Object(_) => Err(TierFault::defect(
            "KeyError",
            "Europe PMC's 'result' is an object, and results[0] is its key 0",
        )),
        Value::String(_) => Err(TierFault::defect(
            "AttributeError",
            "Europe PMC's 'result' is a string, and results[0] has no .get",
        )),
        _ => Err(TierFault::defect(
            "TypeError",
            "Europe PMC's 'result' is not subscriptable",
        )),
    }
}

/// Construct the default disk cache, or degrade to no caching.
///
/// The cache is best-effort everywhere else in this module — a failed write
/// warns once and retrieval continues, a failed read falls through to the
/// network — and construction was the last place an environment fault about the
/// *cache* could abort a run that had every chance of succeeding without one.
///
/// Taking **no parameters** is what makes that asymmetry structural: there is no
/// way to route a caller-supplied `cache_dir` through the degrading path, so the
/// only caller who can reach it is one who expressed no preference.
///
/// The fault surfaces here rather than in [`FullTextCache::new`] because the
/// Rust cache does not create its directories; Python's constructor does, inside
/// the guard. The warning names what the degraded run costs, not just that it is
/// degraded: a PDF is fetched *into* the cache, so with no cache there is no
/// download at all and a PDF-only article comes back as a bare URL. That is lost
/// content, not merely repeated network traffic.
#[must_use]
pub fn default_cache() -> Option<FullTextCache> {
    let cache = FullTextCache::default();
    for directory in [cache.cache_dir.clone(), cache.pdf_dir(), cache.html_dir()] {
        if let Err(error) = std::fs::create_dir_all(&directory) {
            eprintln!(
                "WARNING: Could not create the full-text cache directory ({:?}: {error}); retrieval still works and full text still parses, but nothing will be cached, so every run re-fetches — and a PDF-only article comes back as a bare URL, since a PDF is downloaded into the cache and extracted only once cached. Pass cache=FullTextCache(cache_dir=...) to use a writable location.",
                error.kind()
            );
            return None;
        }
    }
    Some(cache)
}
