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
//! Tier 1a: Europe PMC XML -> JATS parser -> HTML, for a usable caller PMC ID
//! Tier 1c: NCBI PMC efetch for that ID, when Europe PMC gave no body
//! Tier 1b: Discover PMC ID via search, then 1a and 1c for it — also when the
//!          caller's ID gave no full text, which the search hit then supersedes
//! Tier 1b': Discover PMC ID via NCBI's ID Converter when the search found none
//!           and there is no usable caller ID
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
//!   happens to hold. A Tier 0 `pdf` entry is fetched first but ranks below
//!   Tiers 1a to 1c on purpose: JATS found later is returned with the PDF on it
//!   (#739).
//! * **1a/1c before 1b** — the caller-supplied PMC ID is tried in full first,
//!   because it is a stronger identity claim than a search hit. Only when it
//!   gives no full text at *either* source does the search hit supersede it; an
//!   ID Converter answer never does, because the recovery this replaced trusted
//!   that search hit's free PDF and never asked the converter.
//! * **1b' after 1b, never before** — the Europe PMC search returns the PMC ID
//!   *and* the free-PDF URL Tier 1d needs in one request, so the converter's
//!   extra request is spent only when the search came back without an id — and
//!   never once a usable caller ID has failed, since the converter would then be
//!   making an identity claim the supersession decision did not license.
//! * **1c after 1a, before 1d** — structured JATS beats a PDF that needs a PDF
//!   backend to read at all. NCBI serves PMC itself where Europe PMC serves the
//!   corpus its `inEPMC` flag describes, and it is asked only when Europe PMC
//!   gave no body for that same id.
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
//! # Defect fixes the port and the Python now share
//!
//! Three sites once carried the *pre-fix* Python behaviour, each marked with a
//! `DEFECT-FIX` comment. Python's PR #355 settled all three — #309 part 2 on the
//! side the port had already taken, #304 and #305 on the side it has now adopted
//! — so they are agreements rather than divergences:
//!
//! * **#304** — a caller PMC ID is validated before it is used. A malformed one
//!   is recorded and treated as absent, so discovery runs in full, the ID
//!   Converter included; and a well-formed one that no source serves is
//!   **superseded by the Europe PMC search hit's id**, which is the hit the
//!   free-PDF tier already trusted as the article. A remote `pmcid` is validated
//!   where it is read, so a bare numeric one compares equal to the caller's
//!   prefixed spelling and a malformed one sends the chain to the converter.
//! * **#305** — the abstract a cached PDF was returned with is kept in an
//!   `abstracts/` sidecar: written whenever a PDF is cached with an abstract held
//!   back, and read only on a PDF hit that yields no text.
//! * **#309** — the cache key's pass-through bound is the longest key
//!   `sanitize_identifier` returns (171), so the service's key is never hashed
//!   twice, and `get_pdf` refuses an unreadable entry so the service quarantines
//!   it.

use crate::fulltext::cache::{safe_filename, sanitize_identifier, FullTextCache, PDF_MAGIC_BYTES};
use crate::fulltext::jats_reader::{author_full_name, parse_with_pmc_id};
use crate::fulltext::library_store::{contradiction, library_ids, paper_info, Held, LibraryStore};
use crate::fulltext::models::{
    join_citation_parts, ContentKind, FullTextResult, FullTextSourceEntry, JATSArticle,
    JATSBodySection, JATSFundingAward, JATSReferenceInfo, PmcOpenDataRecord, PDF_CHECKSUM_MISMATCH,
    PDF_SEARCH_FAILED,
};
use crate::fulltext::pdf_text::{PdfError, PdfErrorKind};
use crate::library::{FileRef, Installation, Library, PaperIds, PaperInfo, SourceKind};
use crate::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse};
use crate::pyvalue::{python_repr, python_str, python_strip, truthy};
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

/// Europe PMC's REST base, for the search and for `fullTextXML`.
pub const EUROPE_PMC_BASE: &str = "https://www.ebi.ac.uk/europepmc/webservices/rest";

/// Unpaywall's API base.
pub const UNPAYWALL_BASE: &str = "https://api.unpaywall.org/v2";

/// The DOI resolver base, for the final link-only fallback.
pub const DOI_BASE: &str = "https://doi.org";

/// PMC's article datasets on AWS Open Data (#748): a public bucket NLM
/// provides for programmatic access, read where bot screens refuse the
/// publisher's and Europe PMC's PDF links to a client that is not a browser.
pub const PMC_OPEN_DATA_BASE: &str = "https://pmc-oa-opendata.s3.amazonaws.com";

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

/// How many times in all a PDF sought beside JATS (#739) is tried when its
/// download fails for a cause that may pass
/// ([`FullTextResult::pdf_failure_may_pass`]; #744).
pub const PDF_BESIDE_JATS_ATTEMPTS: u32 = 3;

/// The wait before the second try of a PDF beside JATS; each later wait is
/// twice the one before: 1 s, then 2 s (#744).
pub const PDF_RETRY_DELAY: Duration = Duration::from_secs(1);

/// How many later cache hits seek a PDF the cache marks as owed again before
/// the mark is dropped (#744), so a cause that never passes (a bot wall's
/// 503, a refused Unpaywall email) costs a bounded number of lookups. A hit
/// tries each candidate once: the mark is the retry across lookups.
pub const PDF_SEEK_AGAIN_LIMIT: u32 = 3;

/// The [`FullTextResult::source`] of a result served from the shared library
/// (design §4.5).
pub const LIBRARY_SOURCE: &str = "library";

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
    /// Python's `FullTextUnavailableError`. Inside the chain a tier records an
    /// absence as a [`TierFault`] directly, so `fetch_fulltext` never returns
    /// this.
    Unavailable(String),
    /// A PMC ID that is not `PMC` and digits ([`normalise_pmc_id`]), or a call
    /// naming no article (`"No identifiers provided"`). Inside the chain a
    /// transport fault or a malformed body is a [`TierFault`], never this.
    Other(String),
    /// The chain retrieved nothing and has no link to fall back on (#565).
    ///
    /// Python's `FullTextExhaustedError`. A caller tells "something failed, a
    /// retry may succeed" from "nothing failed" by `faults` alone, without
    /// parsing `message`.
    Exhausted {
        /// The exhaustion report, as it was worded before #565.
        message: String,
        /// The fault type names the tiers swallowed, in order — what
        /// [`FullTextResult::tier_faults`] carries on a returned result. Empty
        /// when nothing the chain asked failed, so a retry now would get the
        /// same answers: every source asked had nothing (`absences > 0`), or
        /// none could be asked (`absences == 0`, the only identifier given
        /// having been refused). A caller's refused identifier is not a fault
        /// (#603). A transport failure is named `TransportError` here and by
        /// httpx's class (`ConnectError`, `ReadTimeout`) in Python.
        faults: Vec<String>,
        /// How many sources answered and had nothing.
        absences: usize,
    },
}

impl std::fmt::Display for FullTextError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FullTextError::Unavailable(message)
            | FullTextError::Other(message)
            | FullTextError::Exhausted { message, .. } => write!(f, "{message}"),
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
            FullTextError::Exhausted { message, .. } => {
                TierFault::fault("FullTextExhaustedError", message)
            }
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
///
/// A caller's identifier set aside before any request is *refused*, kept apart
/// from the faults met while fetching: the log line counts both as failed
/// attempts, while [`FullTextResult::tier_faults`] lists only the faults, since
/// a retry cannot change the caller's own input (#603).
pub struct TierFailures<'a> {
    faults: Vec<&'static str>,
    absences: usize,
    refused: Vec<&'static str>,
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
            refused: Vec::new(),
            on_bug: Some(on_bug),
        }
    }

    /// A record nobody is listening to — direct helper calls and tests.
    #[must_use]
    pub fn unreported() -> Self {
        TierFailures {
            faults: Vec::new(),
            absences: 0,
            refused: Vec::new(),
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

    /// Note a caller's identifier set aside before any request was made.
    pub fn refuse(&mut self, fault: &TierFault) {
        self.refused.push(fault.name);
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
        let failed: Vec<&str> = self.refused.iter().chain(&self.faults).copied().collect();
        if !failed.is_empty() {
            let mut kinds = failed.clone();
            kinds.sort_unstable();
            kinds.dedup();
            parts.push(format!(
                "{} failed ({})",
                plural(failed.len(), "attempt"),
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
/// used there rather than `match`. `[0-9]` and not `\d`, which the `regex` crate
/// makes every Unicode decimal digit where Python's pattern is `re.ASCII` (#564).
fn pmc_id_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"^PMC[0-9]+\z").expect("a fixed pattern"))
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

/// What the Europe PMC search found: `(pmc_id, pdf_render_url, hit_doi)`.
type SearchFindings = (Option<String>, Option<String>, Option<String>);

/// The `site` of a `fullTextUrl` entry Europe PMC serves itself (issue #494).
///
/// The list also carries free PDFs on `Unpaywall` (a publisher's or
/// repository's host) and `PubMedCentral` (NCBI's), listed *ahead* of Europe
/// PMC's own: in a live draw of 1,400 records stratified by source and year
/// (2026-10-18), every one of the 440 carrying both had the other site's first
/// — PubMedCentral in 386, Unpaywall in 54. Taken first-wins, Tier 1d fetched
/// another host's PDF under the `europepmc_pdf` label.
const EUROPE_PMC_OWN_SITE: &str = "Europe_PMC";

/// Extract Europe PMC's own free PDF URL from its `fullTextUrlList`.
///
/// The search API includes `fullTextUrlList` with `?pdf=render` entries
/// (`fulltextRepo` ones for a preprint) for PDFs it serves itself, even when
/// JATS XML is unavailable — which is exactly when Tier 1d needs one.
///
/// Only an entry whose `site` is `"Europe_PMC"` is taken; an entry naming no
/// site is refused, since it may be another host's. Another site's PDF
/// is left to Tier 2, which reaches it through the article's DOI — the search
/// hit's when the caller gave none. A blank `url` is skipped for a later entry.
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
        let is_own =
            matches!(entry.get("site"), Some(Value::String(site)) if site == EUROPE_PMC_OWN_SITE);
        let is_pdf =
            matches!(entry.get("documentStyle"), Some(Value::String(style)) if style == "pdf");
        if !is_own || !is_pdf || !entry_is_free(entry) {
            continue;
        }
        if let Some(Value::String(url)) = entry.get("url") {
            if !python_strip(url).is_empty() {
                return Some(url.clone());
            }
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
/// [`FullTextError::Other`] if the value is not `PMC` followed by ASCII digits.
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

/// [`normalise_pmc_id`] for a decoded JSON value.
///
/// Typed `str` on the Python side, but a search response is JSON and a caller may
/// be untyped, so any other type is refused here as unusable rather than raising
/// `AttributeError` from a comparison outside every tier's guard — the review
/// finding that a malformed remote `pmcid` escaped `fetch_fulltext`. The message
/// is Python's `f"Not a usable PMC ID: {value!r}"`.
fn normalise_pmc_value(value: &Value) -> Result<String, FullTextError> {
    match value {
        Value::String(raw) => normalise_pmc_id(raw),
        other => Err(FullTextError::Other(format!(
            "Not a usable PMC ID: {}",
            python_repr(other)
        ))),
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
///
/// `HttpStatus` has no in-module producer — every status this module meets is
/// checked where it is read, so the tier names it itself — but the arm states
/// the name rather than defaulting, so a caller that hands one in is reported
/// as Python's `HTTPStatusError` and not as a transport fault.
///
/// **The names are Python's**, which is what the exhaustion report renders
/// (`f"{type(exc).__name__}: {exc}"`), and three arms used to invent classes
/// Python does not have — `MalformedError`, `ConfigError`,
/// `ResumeUnreadableError`. Nothing in this module can produce them
/// ([`HttpClient`] has one method, `get`, and this module reads JSON itself),
/// so nothing called the invented names out; they are `ValueError` now, which is
/// what the fetcher layer measures for the same errors and what the corpus's
/// `fetch/http-error` case pins for a malformed body.
/// What the PDF engine said before a read failed, as a clause to end the
/// failure's log line with (#619); empty when it said nothing.
fn engine_said(engine_messages: &[String]) -> String {
    if engine_messages.is_empty() {
        return String::new();
    }
    format!(
        " (the PDF engine reported {} message(s): {})",
        engine_messages.len(),
        engine_messages.join("; ")
    )
}

fn fetch_error_name(error: &FetchError) -> &'static str {
    match error {
        FetchError::Transport(_) => "TransportError",
        FetchError::HttpStatus { .. } => "HTTPStatusError",
        FetchError::Malformed(_) => "ValueError",
        FetchError::Config(_) => "ValueError",
        FetchError::ResumeUnreadable(_) => "ValueError",
    }
}

/// The highest version of `pmc_id` a PMC Open Data listing names (#748).
///
/// `listing` is the body of `?list-type=2&prefix=<pmc_id>.&delimiter=/`, whose
/// `<Prefix>` elements name one `<pmc_id>.<version>/` each; `pmc_id` is
/// canonical, `PMC` and ASCII digits. The versions are compared as numbers (a
/// listing sorts `.10/` before `.2/`); `None` when the bucket holds no version.
fn latest_open_data_version(listing: &str, pmc_id: &str) -> Option<u64> {
    let pattern = Regex::new(&format!(
        r"<Prefix>{}\.([0-9]+)/</Prefix>",
        regex::escape(pmc_id)
    ))
    .expect("an escaped PMC ID makes a valid pattern");
    pattern
        .captures_iter(listing)
        .filter_map(|found| found[1].parse::<u64>().ok())
        .max()
}

/// A fault refusing a PMC Open Data record not of the shape read (#750).
fn open_data_refusal(stem: &str, what: impl std::fmt::Display) -> TierFault {
    TierFault::fault(
        "FullTextError",
        format!("PMC Open Data record {stem}: {what}"),
    )
}

/// The md5 a PMC Open Data record gives for the version's own `suffix` file (#750).
///
/// The record names each file as
/// `s3://pmc-oa-opendata/<stem>/<stem><suffix>?md5=<hex>`, or `null` for one
/// the version lacks. The bucket writes every key, so a missing one is a
/// record of another shape, not a file the version lacks. Anything else, a
/// file of another name or folder included, is a record not of the shape
/// read: refused, not guessed at.
fn open_data_file_md5(
    data: &Map<String, Value>,
    field: &str,
    stem: &str,
    suffix: &str,
) -> Result<Option<String>, TierFault> {
    let value = match data.get(field) {
        None => return Err(open_data_refusal(stem, format!("no {field}"))),
        Some(Value::Null) => return Ok(None),
        Some(value) => value,
    };
    let prefix = format!("s3://pmc-oa-opendata/{stem}/{stem}{suffix}?md5=");
    let md5 = value.as_str().and_then(|text| text.strip_prefix(&prefix));
    match md5 {
        Some(md5)
            if md5.len() == 32 && md5.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) =>
        {
            Ok(Some(md5.to_string()))
        }
        _ => Err(open_data_refusal(
            stem,
            format!("unexpected {field} {value}"),
        )),
    }
}

/// Read `pmc_id`'s record at `version` from its parsed `.json` (#750).
///
/// A field must be present and have the JSON type the bucket gives it, `null`
/// only where the bucket leaves it so (it writes every key); the record must
/// name the article and version asked.
fn open_data_record(
    data: &Value,
    pmc_id: &str,
    version: u64,
) -> Result<PmcOpenDataRecord, TierFault> {
    let stem = format!("{pmc_id}.{version}");
    let Some(data) = data.as_object() else {
        return Err(TierFault::fault(
            "FullTextError",
            format!("PMC Open Data record {stem} is not an object"),
        ));
    };
    let unexpected = |name: &str| {
        let value = data.get(name).unwrap_or(&Value::Null);
        open_data_refusal(&stem, format!("unexpected {name} {value}"))
    };
    // The bucket writes every key, `null` where it has no value (#752).
    let present = |name: &str| {
        data.get(name)
            .ok_or_else(|| open_data_refusal(&stem, format!("no {name}")))
    };
    let text = |name: &str| match present(name)? {
        Value::Null => Ok(None),
        Value::String(value) => Ok(Some(value.clone())),
        _ => Err(unexpected(name)),
    };
    let flag = |name: &str| match present(name)? {
        Value::Bool(value) => Ok(*value),
        _ => Err(unexpected(name)),
    };
    let named_pmc_id = text("pmcid")?.ok_or_else(|| unexpected("pmcid"))?;
    let named_version = match present("version")? {
        Value::Number(number) if number.is_i64() || number.is_u64() => number.as_u64(),
        _ => return Err(unexpected("version")),
    };
    if named_pmc_id != pmc_id || named_version != Some(version) {
        return Err(TierFault::fault(
            "FullTextError",
            format!("PMC Open Data record {stem} names another article or version"),
        ));
    }
    let pmid = match present("pmid")? {
        Value::Null => None,
        // A PMID is positive, and one past u64 reads as a float (#752 review).
        Value::Number(number) => match number.as_u64() {
            Some(pmid) if pmid > 0 => Some(pmid.to_string()),
            _ => return Err(unexpected("pmid")),
        },
        _ => return Err(unexpected("pmid")),
    };
    Ok(PmcOpenDataRecord {
        pmc_id: pmc_id.to_string(),
        version,
        pmid,
        doi: text("doi")?,
        is_retracted: flag("is_retracted")?,
        is_pmc_openaccess: flag("is_pmc_openaccess")?,
        license_code: text("license_code")?,
        xml_md5: open_data_file_md5(data, "xml_url", &stem, ".xml")?,
        pdf_md5: open_data_file_md5(data, "pdf_url", &stem, ".pdf")?,
    })
}

/// `content`'s md5 as lowercase hex: a checksum the bucket gives, not security.
fn md5_hex(content: &[u8]) -> String {
    use md5::{Digest, Md5};
    Md5::digest(content)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
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
pub(crate) fn format_award(award: &JATSFundingAward) -> String {
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
    let authors = reference.author_text();
    if !authors.is_empty() {
        // `et al.` ends in a period, which `join_citation_parts` does not
        // double (#385).
        parts.push(html_escape(&authors));
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
    let volume_info = reference.volume_info();
    if !volume_info.is_empty() {
        parts.push(html_escape(&volume_info));
    }
    if !reference.doi.is_empty() {
        let doi = html_escape(&reference.doi);
        parts.push(format!("<a href=\"https://doi.org/{doi}\">doi:{doi}</a>"));
    }
    if reference.defers_to_the_deposit(parts.len()) {
        return html_escape(&reference.citation);
    }
    join_citation_parts(&parts, true)
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
    /// What the backend's engine reported while reading (#599): MuPDF's
    /// messages, the path replaced by "the file", for `MupdfPdfExtractor`.
    pub engine_messages: Vec<String>,
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
/// raising, reported per PDF, its kind reaching
/// [`FullTextResult::pdf_extraction_failure`] (#604).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PdfExtractError {
    /// No backend could be constructed.
    Backend {
        /// A name for the cause, used as the once-per-service key.
        name: String,
        /// What happened.
        message: String,
    },
    /// The backend raised while converting: which side the failure is on, and
    /// what happened.
    Conversion(PdfError),
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

/// Where JATS full text was found, for [`FullTextService::pdf_for_jats`].
struct JatsFound<'a> {
    /// The caller's request: its `fulltext_sources` and PMID.
    request: &'a FullTextRequest,
    /// The caller's DOI, if non-empty.
    doi: Option<&'a str>,
    /// The PMC ID the JATS came from; `None` for a Tier 0 xml entry, whose
    /// article no PMC ID names, so Europe PMC's PDF is not asked for it.
    pmc_id: Option<&'a str>,
    /// What Tier 1b's search itself returned when it answered; `None` runs it in
    /// `pdf_for_jats` when `pmc_id` is set. Its PMC ID is the search's alone,
    /// never the ID Converter's, so a PDF is taken only from a hit that named
    /// the JATS's article.
    search: Option<SearchFindings>,
    /// How many cache hits, this one included, have sought the PDF again
    /// (#744); 0 in the tier chain. A hit tries each candidate once, the chain
    /// up to [`PDF_BESIDE_JATS_ATTEMPTS`] times. When no PDF is saved and any
    /// failure may pass, the cache marks the JATS with this count, until it
    /// reaches [`PDF_SEEK_AGAIN_LIMIT`]; a saved PDF, or failures that are all
    /// final, unmark it, and a run that made no request leaves the mark as it
    /// is.
    seeks: u32,
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
    /// Beside JATS, PMC's AWS Open Data bucket (#748).
    PmcOpenData,
}

impl PdfOrigin {
    /// The stable string the warning key is built from.
    fn as_str(self) -> &'static str {
        match self {
            PdfOrigin::EuropepmcPdf => "europepmc_pdf",
            PdfOrigin::Unpaywall => "unpaywall",
            PdfOrigin::KnownSource => "known_source",
            PdfOrigin::PmcOpenData => "pmc_open_data",
        }
    }
}

/// The shared library's part in one lookup: what it gave before the cache and
/// the chain ([`FullTextService::read_library`]), and what the chain keeps in
/// it and takes from it after.
///
/// One value per `fetch_fulltext` call, passed down the chain. The library is
/// used only while `ids` is not empty and `failure` is `None`
/// ([`FullTextService::keeper`]), so the first library error ends its use for
/// the rest of the call.
#[derive(Default)]
struct FromLibrary {
    /// The library's JATS, rendered: it ends the lookup.
    served: Option<FullTextResult>,
    /// The library's PDF when no JATS was served, held for the chain, which
    /// takes it in place of a download.
    pdf: Option<Held>,
    /// The library error that stopped its use in this lookup, for the result's
    /// [`FullTextResult::library_failure`].
    failure: Option<String>,
    /// The request's identifiers in the library's form; empty when the lookup
    /// does not use the library.
    ids: PaperIds,
    /// The library files this lookup used or saved, in order, for the result's
    /// [`FullTextResult::library_files`].
    files: Vec<FileRef>,
    /// The paper as a JATS used or saved in this lookup describes it, so a PDF
    /// saved later in the call is filed with its title, year and author.
    paper: Option<PaperInfo>,
    /// Whether this lookup first noted the served JATS in this installation's
    /// record: the lookup a first fetch would have sought its PDF in, so
    /// [`FullTextService::seek_pdf_again`] seeks it once without a mark.
    jats_first_noted: bool,
}

impl FromLibrary {
    /// `result` with this lookup's library files and library failure on it.
    fn finish(self, mut result: FullTextResult) -> FullTextResult {
        result.library_files = self.files;
        result.library_failure = self.failure;
        result
    }

    /// The paper to file a PDF under: the JATS's when this lookup used or saved
    /// one, else the request's identifiers alone.
    fn pdf_paper(&self) -> PaperInfo {
        self.paper
            .clone()
            .unwrap_or_else(|| paper_info(self.ids.clone(), None))
    }
}

/// What [`FullTextService::usable_library_jats`] made of the library's JATS.
enum LibraryJats {
    /// It may be served.
    Usable(Box<JATSArticle>),
    /// One of its own identifiers contradicts the request's: another paper.
    /// The message names the file and both values, for the lookup's
    /// [`FullTextResult::library_failure`].
    WrongPaper(String),
    /// It does not parse or has no body.
    Unusable,
}

/// The request's `pmid`, `doi` and `pmc_id` as Python's `fetch_fulltext` has
/// read them when it looks the paper up in the library, so both languages make
/// the same lookup and log the same library warnings.
///
/// The PMID is stripped and kept only when it is ASCII digits, not all zeros
/// (Python's `_normalise_pmid`; it refuses anything else, with its own
/// warning). A PMC ID given as the PMID (`PMC` and ASCII digits, #478) is used
/// as the `pmc_id` when none was given. The DOI is stripped (`_normalise_doi`);
/// the PMC ID is passed as given.
fn library_request_ids(request: &FullTextRequest) -> (&str, Option<&str>, Option<&str>) {
    let pmid = python_strip(&request.pmid);
    let usable_pmid = !pmid.is_empty()
        && pmid.bytes().all(|b| b.is_ascii_digit())
        && pmid.bytes().any(|b| b != b'0');
    let given_pmc_id = request.pmc_id.as_deref().filter(|id| !id.is_empty());
    let pmc_id =
        given_pmc_id.or_else(|| (!usable_pmid && pmc_id_pattern().is_match(pmid)).then_some(pmid));
    let doi = request
        .doi
        .as_deref()
        .map(python_strip)
        .filter(|doi| !doi.is_empty());
    (if usable_pmid { pmid } else { "" }, doi, pmc_id)
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
    fetch_pdf_with_jats: bool,
    pdf_retry_delay: Duration,
    pdf_extractor: Option<Arc<dyn PdfExtractor + Send + Sync>>,
    /// The shared library, read before the cache (design §4.5); `None` runs the
    /// service as it ran before there was one.
    library: Option<LibraryStore>,
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
            fetch_pdf_with_jats: false,
            pdf_retry_delay: PDF_RETRY_DELAY,
            pdf_extractor: None,
            library: None,
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

    /// Whether to look for a PDF once JATS full text is found, for an app that
    /// processes the JATS and shows the PDF (#739). Off by default, so JATS
    /// ends the search; a PDF the caller's `fulltext_sources` gave before the
    /// JATS rides on it either way. When on, the caller's `pdf` entries not yet
    /// tried are asked, then Europe PMC's free PDF (only when its search
    /// resolves to the PMC ID the JATS came from, so the PDF is the same
    /// article's), then Unpaywall; the first PDF saved goes on the JATS result,
    /// else the first failure that may pass, else a failed search's
    /// [`PDF_SEARCH_FAILED`], else the first failure (#746). A download that
    /// fails for a cause that may pass is tried again with backoff
    /// ([`PDF_BESIDE_JATS_ATTEMPTS`]), and when no PDF is saved and any failure
    /// may pass the cache marks the JATS, so a later hit seeks the PDF again
    /// (#744); any other cached JATS hit is served as it is.
    #[must_use]
    pub fn with_fetch_pdf_with_jats(mut self, fetch_pdf_with_jats: bool) -> Self {
        self.fetch_pdf_with_jats = fetch_pdf_with_jats;
        self
    }

    /// Wait `delay` before a PDF beside JATS is tried again, and twice as long
    /// before each later try ([`PDF_RETRY_DELAY`] by default; #744). Zero in a
    /// test, so the backoff is not spent.
    #[must_use]
    pub fn with_pdf_retry_delay(mut self, delay: Duration) -> Self {
        self.pdf_retry_delay = delay;
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

    /// Read papers from `library` before the cache, as the installation `who`
    /// (design §4.5).
    ///
    /// A lookup with a valid `pmid`, `doi` or `pmc_id` then asks the network
    /// nothing when the library holds the paper's JATS (with a body, and no
    /// identifier of its own contradicting the request's): the result's
    /// `source` is [`LIBRARY_SOURCE`], it lists the files it used in
    /// [`FullTextResult::library_files`], and the library's PDF, if any, is its
    /// `file_path`. Each file used is noted in `who`'s record. Otherwise the
    /// chain runs, and the JATS and PDFs it downloads are kept in the library
    /// rather than the cache (a PDF is then downloaded even without an
    /// `identifier`), while the library's PDF, when it holds one, is taken in
    /// place of a download. The cache keeps only the *PDF still owed* mark and
    /// an abstract kept with a PDF. A library that cannot be read or written
    /// does not stop the lookup: it is warned of, named in
    /// [`FullTextResult::library_failure`], and the rest of the lookup runs as
    /// without a library.
    #[must_use]
    pub fn with_library(mut self, library: Library, who: Installation) -> Self {
        self.library = Some(LibraryStore::new(library, who));
        self
    }

    /// The configured cache, if any.
    #[must_use]
    pub fn cache(&self) -> Option<&FullTextCache> {
        self.cache.as_ref()
    }

    /// The shared library and the installation reading it, if one is set
    /// ([`FullTextService::with_library`]).
    #[must_use]
    pub fn library(&self) -> Option<&LibraryStore> {
        self.library.as_ref()
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

    /// Whether a PDF is sought once JATS full text is found
    /// ([`FullTextService::with_fetch_pdf_with_jats`]).
    #[must_use]
    pub fn fetch_pdf_with_jats(&self) -> bool {
        self.fetch_pdf_with_jats
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
        let record_level = match level {
            LogLevel::Debug => log::Level::Debug,
            LogLevel::Info => log::Level::Info,
            LogLevel::Warning => log::Level::Warn,
        };
        log::log!(target: "bmlib.fulltext.service", record_level, "{message}");
        self.log
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .push(LogLine { level, message });
    }

    /// Fetch full text using known sources and the three-tier fallback chain.
    ///
    /// # Errors
    ///
    /// [`FullTextError::Other`] when no identifiers at all were given, and
    /// [`FullTextError::Exhausted`] when the chain exhausted with no link to
    /// degrade to. A tier failing never aborts the chain; it is recorded on the
    /// exhaustion report instead. A library failure met before the chain ends in
    /// an error is logged at WARNING, but no result carries it as
    /// [`FullTextResult::library_failure`].
    pub fn fetch_fulltext(
        &self,
        request: &FullTextRequest,
    ) -> Result<FullTextResult, FullTextError> {
        let cache_id = request
            .identifier
            .as_deref()
            // A blank key is no key (#485): it would be shared by every call
            // spelling it the same way. A padded key stays the caller's own.
            .filter(|identifier| !python_strip(identifier).is_empty())
            .map(sanitize_identifier);

        // The library first (design §4.5): its JATS ends the lookup. Its
        // files and failure are carried onto whatever the cache or the chain
        // returns.
        let mut library = self.read_library(request);
        if let Some(hit) = library.served.take() {
            // No PDF in the library: the cache's PDF mark (#744) still applies.
            let hit = match (cache_id.as_deref(), self.cache.as_ref()) {
                (Some(id), Some(cache)) => {
                    self.seek_pdf_again(hit, cache, id, request, &mut library)
                }
                _ => hit,
            };
            return Ok(library.finish(hit));
        }

        // Cache check — return immediately if content is already on disk.
        //
        // A cache *read* is best-effort exactly as a cache write is: an entry
        // truncated by a killed process or a filesystem fault broke the
        // documented FullTextError-only contract and was a hard stop where
        // re-fetching over the network was available, so one bad file made a
        // paper permanently unfetchable and took a bulk sync down with it.
        //
        // A readable PDF entry is a **hit** even when its text cannot be
        // extracted (#305). The port used to treat it as a miss and re-run the
        // chain, which the maintainer rejected: for a `convert_pdfs = false`
        // caller every PDF hit became a network re-fetch. Python's fix is the
        // `abstracts/` sidecar instead — `check_cache` reads it back — and the
        // result carries `content_kind = none` with `file_path` when there is no
        // abstract to pair it with.
        if let (Some(id), Some(cache)) = (cache_id.as_deref(), self.cache.as_ref()) {
            if let Some(hit) = self.check_cache(cache, id) {
                let hit = self.seek_pdf_again(hit, cache, id, request, &mut library);
                return Ok(library.finish(hit));
            }
        }

        // Every tier below swallows its own failure so the next one still
        // runs; this is what remembers that they did, whichever tier returns.
        let on_bug = |fault: &TierFault| self.warn_swallowed_bug(fault);
        let mut failures = TierFailures::reported(&on_bug);
        let mut result =
            self.retrieve(request, cache_id.as_deref(), &mut failures, &mut library)?;
        result.tier_faults = failures.faults().iter().map(ToString::to_string).collect();
        Ok(library.finish(result))
    }

    /// What the library holds for `request`, read before the cache (design
    /// §4.5).
    ///
    /// Keyed by the request's `pmid`, `doi` and `pmc_id` ([`library_ids`]); a
    /// field the library refuses is warned of and left out, and a request with
    /// none left skips the library without a failure. The paper's JATS is served
    /// when it parses, has a body, and none of its own identifiers contradicts
    /// the request's (an identifier absent on either side agrees); the
    /// library's PDF goes beside it, and each file used is noted in this
    /// installation's record. A JATS that has no body or does not parse is
    /// warned of and not served, and the library's PDF is held for the chain
    /// instead. A JATS that is the wrong paper is not served and is the
    /// lookup's library failure (warned of once): no PDF of its folder is
    /// held, and nothing is kept in the library, since the request's
    /// identifiers would file it in that folder. The first library error is
    /// warned of and kept as the failure, and the rest of the call uses no
    /// library.
    fn read_library(&self, request: &FullTextRequest) -> FromLibrary {
        let mut out = FromLibrary::default();
        let Some(store) = self.library.as_ref() else {
            return out;
        };
        let (pmid, doi, pmc_id) = library_request_ids(request);
        let (ids, dropped) = library_ids(pmid, doi, pmc_id);
        for field in dropped {
            self.warn(format!(
                "The request's {field} is not an identifier the library accepts; looking the paper up in the library without it."
            ));
        }
        if ids.is_empty() {
            return out;
        }
        out.ids = ids.clone();
        // One scan serves both lookups and the notes below.
        let found = store.scan(&ids).and_then(|scan| {
            let jats = store.find_source_in(&scan, &ids, SourceKind::Jats)?;
            Ok((scan, jats))
        });
        let (scan, jats) = match found {
            Ok(found) => found,
            Err(error) => {
                self.library_failed(&mut out, &error);
                return out;
            }
        };
        let article = match jats
            .as_ref()
            .map(|held| self.usable_library_jats(held, &ids))
        {
            // The folder's record claimed a paper its JATS is not (design §4.4: a
            // record is a claim, not proof), so its PDF is not trusted either, and
            // nothing is kept: the request's identifiers would file it there.
            Some(LibraryJats::WrongPaper(message)) => {
                self.library_failed(&mut out, &message);
                return out;
            }
            Some(LibraryJats::Usable(article)) => Some(*article),
            Some(LibraryJats::Unusable) | None => None,
        };
        let pdf = match store.find_source_in(&scan, &ids, SourceKind::Pdf) {
            Ok(pdf) => pdf,
            Err(error) => {
                self.library_failed(&mut out, &error);
                None
            }
        };
        let (Some(jats), Some(article)) = (jats, article) else {
            if let Some(pdf) = &pdf {
                self.info(format!(
                    "Holding the library's PDF {}/{} for the chain",
                    pdf.file.folder, pdf.file.name
                ));
            }
            out.pdf = pdf;
            return out;
        };
        self.info(format!(
            "Full text from the library's JATS ({}/{})",
            jats.file.folder, jats.file.name
        ));
        let mut result =
            fulltext_result(LIBRARY_SOURCE, render_jats_html(&article), Some(jats.bytes));
        out.files.push(jats.file);
        if let Some(pdf) = pdf {
            match store.path_of(&pdf.file) {
                Ok(path) => {
                    result.file_path = Some(path.display().to_string());
                    out.files.push(pdf.file);
                }
                Err(error) => self.library_failed(&mut out, &error),
            }
        }
        let paper = paper_info(ids, Some(&article));
        if out.failure.is_none() {
            for file in &out.files {
                match store.note_use_in(&scan, &paper, file) {
                    Ok(wrote) => out.jats_first_noted |= wrote && file.kind == SourceKind::Jats,
                    Err(error) => {
                        self.library_failed(&mut out, &error);
                        break;
                    }
                }
            }
        }
        out.paper = Some(paper);
        out.served = Some(result);
        out
    }

    /// Record `error` as the library's failure in this lookup, warned of; the
    /// rest of the lookup uses no library.
    fn library_failed(&self, out: &mut FromLibrary, error: &str) {
        self.warn(format!(
            "Could not use the library ({error}); going on without it."
        ));
        out.failure = Some(error.to_string());
    }

    /// The library, when this lookup may still use it: one is set, the request
    /// gave it identifiers, and no library error has been met.
    fn keeper(&self, library: &FromLibrary) -> Option<&LibraryStore> {
        self.library
            .as_ref()
            .filter(|_| library.failure.is_none() && !library.ids.is_empty())
    }

    /// JATS full text the chain's tier `source` found: kept in the library
    /// when this lookup uses one, else cached as [`FullTextService::cache_html`]
    /// caches it.
    ///
    /// The paper is filed under the request's identifiers, with the title,
    /// year, first author and missing identifiers read from `xml` (parsed as
    /// the tier parsed it, with `known_pmc_id`; if that parse fails it is warned
    /// of and the identifiers alone are used). The saved file goes on
    /// `library.files`. A library error is warned of and recorded, and the
    /// JATS is cached instead.
    fn keep_jats(
        &self,
        html: &str,
        xml: &[u8],
        source: &str,
        known_pmc_id: &str,
        cache_id: Option<&str>,
        library: &mut FromLibrary,
    ) {
        if let Some(store) = self.keeper(library) {
            let parsed = std::str::from_utf8(xml)
                .map_err(|error| error.to_string())
                .and_then(|text| {
                    parse_with_pmc_id(text, known_pmc_id).map_err(|error| error.to_string())
                });
            let article = match parsed {
                Ok(article) => Some(article),
                Err(error) => {
                    self.warn(format!(
                        "Could not read the JATS from {source} again to file it in the library ({error}); filing it under the request's identifiers alone."
                    ));
                    None
                }
            };
            let paper = paper_info(library.ids.clone(), article.as_ref());
            match store.keep(&paper, xml, SourceKind::Jats, source) {
                Ok(file) => {
                    self.info(format!(
                        "JATS from {source} kept in the library ({}/{})",
                        file.folder, file.name
                    ));
                    library.files.push(file);
                    library.paper = Some(paper);
                    return;
                }
                Err(error) => self.library_failed(library, &error),
            }
        }
        self.cache_html(html, xml, cache_id);
    }

    /// Keep downloaded PDF `bytes` from `url` in the library, filed under
    /// [`FromLibrary::pdf_paper`]. Returns the file's path in the library, the
    /// file added to `library.files`; `None` after a library error, warned of
    /// and recorded. The caller has checked the bytes are a PDF.
    fn keep_pdf(
        &self,
        store: &LibraryStore,
        bytes: &[u8],
        url: &str,
        library: &mut FromLibrary,
    ) -> Option<PathBuf> {
        // The file goes on `files` once kept, even if its path then fails.
        let path = store
            .keep(&library.pdf_paper(), bytes, SourceKind::Pdf, url)
            .and_then(|file| {
                let path = store.path_of(&file);
                library.files.push(file);
                path
            });
        match path {
            Ok(path) => Some(path),
            Err(error) => {
                self.library_failed(library, &error);
                None
            }
        }
    }

    /// The library's PDF held for this lookup, in place of a download: its
    /// path, added to `library.files`, once noted in this installation's
    /// record. `None` when none is held, the library is no longer used, or its
    /// path fails (warned of and recorded). A note that fails is warned of and
    /// recorded too, which ends the library's use for the call, but the PDF is
    /// still served: its bytes passed the digest check, and a read-only library
    /// must not cost a download of a PDF it holds.
    fn use_library_pdf(&self, library: &mut FromLibrary) -> Option<PathBuf> {
        let store = self.keeper(library)?;
        let held = library.pdf.take()?;
        let path = match store.path_of(&held.file) {
            Ok(path) => path,
            Err(error) => {
                self.library_failed(library, &error);
                return None;
            }
        };
        let noted = store.note_use(&library.pdf_paper(), &held.file);
        self.info(format!(
            "Using the library's PDF {}/{} in place of a download",
            held.file.folder, held.file.name
        ));
        library.files.push(held.file);
        if let Err(error) = noted {
            self.library_failed(library, &error);
        }
        Some(path)
    }

    /// The library's JATS `held`, parsed, when it may be served for `ids`: it
    /// parses, has a body, and its own identifiers agree with `ids`. A file
    /// that is only unusable is warned of here; a file of another paper is
    /// returned with a message naming both values of the contradicting
    /// identifier, which the caller warns of once as the lookup's failure.
    fn usable_library_jats(&self, held: &Held, ids: &PaperIds) -> LibraryJats {
        let at = format!("{}/{}", held.file.folder, held.file.name);
        let parsed = std::str::from_utf8(&held.bytes)
            .map_err(|error| error.to_string())
            .and_then(|xml| parse_with_pmc_id(xml, "").map_err(|error| error.to_string()));
        let article = match parsed {
            Ok(article) => article,
            Err(error) => {
                self.warn(format!(
                    "Not serving the library's JATS {at}: it does not parse ({error}); looking further."
                ));
                return LibraryJats::Unusable;
            }
        };
        let (own, _) = library_ids(&article.pmid, Some(&article.doi), Some(&article.pmc_id));
        if let Some((field, theirs, ours)) = contradiction(&own, ids) {
            return LibraryJats::WrongPaper(format!(
                "the library's JATS {at} is another paper's: its own {field} {theirs} is not the request's {ours}, so nothing is kept in the library in this lookup"
            ));
        }
        if !article.has_body {
            self.warn(format!(
                "Not serving the library's JATS {at}: it has no body; looking further."
            ));
            return LibraryJats::Unusable;
        }
        LibraryJats::Usable(Box::new(article))
    }

    /// The tier chain. Every return is an exit a caller can observe.
    ///
    /// `library` is the lookup's part of the shared library (design §4.5): JATS
    /// and PDFs the chain finds are kept in it rather than in the cache, and
    /// the library's PDF it holds is taken in place of a download, beside
    /// JATS or, with no JATS, ahead of Tier 1d.
    fn retrieve(
        &self,
        request: &FullTextRequest,
        cache_id: Option<&str>,
        failures: &mut TierFailures<'_>,
        library: &mut FromLibrary,
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

        // Tier 0: fetcher-provided sources.
        let mut held_link: Option<FullTextSourceEntry> = None;
        let mut pdf: Option<FullTextResult> = None;
        if !request.fulltext_sources.is_empty() {
            let (result, held_back, link, known_pdf) =
                self.try_known_sources(&request.fulltext_sources, cache_id, failures, library);
            abstract_only = held_back;
            held_link = link;
            pdf = known_pdf;
            if let Some(result) = result {
                let found = JatsFound {
                    request,
                    doi,
                    pmc_id: None,
                    search: None,
                    seeks: 0,
                };
                let result =
                    self.pdf_for_jats(result, pdf.as_ref(), &found, cache_id, failures, library);
                return Ok(self.with_abstract_fallback(result, abstract_only.as_ref(), cache_id));
            }
        }

        // Tiers 1a and 1c for the caller's PMC ID. A malformed one makes no
        // identity claim at all, so it is recorded once and the chain goes on as
        // though none had been given (#304). It used to be refused by each tier
        // in turn and, being non-empty, to switch off the discovery below — so
        // supplying it returned strictly less than omitting it.
        let mut caller_pmc_id: Option<String> = None;
        if let Some(raw) = pmc_id.as_deref() {
            match normalise_pmc_id(raw) {
                Ok(id) => caller_pmc_id = Some(id),
                Err(error) => {
                    self.debug(format!("Caller-supplied PMC ID {raw:?} is not usable"));
                    failures.refuse(&TierFault::from(error));
                }
            }
        }
        // Python holds on to the object the caller's ID produced, so that when a
        // discovered ID also brings an abstract *that* one may replace it while an
        // earlier tier's keeps first-wins. Nothing between the caller's tier and
        // the discovered one touches `abstract_only`, so a flag says it exactly.
        let mut abstract_came_from_caller = false;
        if let Some(id) = caller_pmc_id.as_deref() {
            let held_before = abstract_only.is_some();
            let (result, held_back) =
                self.try_pmc_id(id, cache_id, failures, abstract_only.take(), library);
            abstract_only = held_back;
            if let Some(result) = result {
                let found = JatsFound {
                    request,
                    doi,
                    pmc_id: Some(id),
                    search: None,
                    seeks: 0,
                };
                return Ok(self.pdf_for_jats(
                    result,
                    pdf.as_ref(),
                    &found,
                    cache_id,
                    failures,
                    library,
                ));
            }
            if !held_before && abstract_only.is_some() {
                abstract_came_from_caller = true;
            }
        }

        // Tiers 1b and 1b′: resolve a PMC ID from the DOI or PMID, then 1a and 1c
        // for it. Reached with a caller ID only once that ID has given no full
        // text at any source, and then only the *search* may supersede it —
        // the maintainer's decision on #304's stale-ID half, on the ground that
        // the recovery step this replaced already trusted that search hit as the
        // article, taking its free PDF whenever it offered one. Fetching the same
        // hit's XML makes no new identity claim; an ID Converter answer would,
        // since that recovery never asked it, so a well-formed caller ID keeps
        // the converter out.
        let mut pdf_render_url: Option<String> = None;
        let mut hit_doi: Option<String> = None;
        if has_fallback_identifier {
            let (discovered_pmc_id, search_for_pdf) =
                self.discover_pmc_id(doi, pmid, caller_pmc_id.is_none(), failures);
            // A search that failed is `None` here: pdf_for_jats asks it again
            // then (#744), and only then, so a hit that answered is not re-sent.
            if let Some(search) = &search_for_pdf {
                pdf_render_url.clone_from(&search.1);
                hit_doi.clone_from(&search.2);
            }
            if let Some(id) = discovered_pmc_id {
                if Some(id.as_str()) != caller_pmc_id.as_deref() {
                    if let Some(caller) = caller_pmc_id.as_deref() {
                        self.info(format!(
                            "PMC ID {caller} gave no full text and is superseded by {id}, which doi={doi:?} pmid={pmid} resolves to"
                        ));
                    }
                    let (result, discovered_abstract) =
                        self.try_pmc_id(&id, cache_id, failures, None, library);
                    if let Some(result) = result {
                        let found = JatsFound {
                            request,
                            doi,
                            pmc_id: Some(&id),
                            search: search_for_pdf,
                            seeks: 0,
                        };
                        return Ok(self.pdf_for_jats(
                            result,
                            pdf.as_ref(),
                            &found,
                            cache_id,
                            failures,
                            library,
                        ));
                    }
                    // A superseded ID's own abstract gives way to the superseding
                    // one's: Tier 1d's PDF comes from the same search hit, so the
                    // two are paired, and #305's sidecar would otherwise cache a
                    // stale article's abstract beside it for good. An earlier
                    // tier's abstract keeps first-wins.
                    if discovered_abstract.is_some()
                        && (abstract_only.is_none() || abstract_came_from_caller)
                    {
                        abstract_only = discovered_abstract;
                    }
                }
            }
        }

        // No JATS anywhere: a caller's PDF in hand is the answer, and the PDF
        // tiers below would only fetch another (#739).
        if let Some(saved) = pdf.take_if(|held| held.pdf_download_failure.is_none()) {
            return Ok(self.with_abstract_fallback(saved, abstract_only.as_ref(), cache_id));
        }

        // The library's PDF in place of the PDF tiers below (design §4.5).
        if let Some(path) = self.use_library_pdf(library) {
            let mut result = FullTextResult {
                file_path: Some(path.display().to_string()),
                ..empty_result(LIBRARY_SOURCE)
            };
            self.attach_pdf_text(&path, &mut result);
            return Ok(self.with_abstract_fallback(result, abstract_only.as_ref(), cache_id));
        }

        // Tier 1d: Europe PMC PDF render (free PDF when XML is unavailable).
        if let Some(url) = pdf_render_url {
            self.info(format!("PDF available from Europe PMC render: {url}"));
            let mut result = FullTextResult {
                pdf_url: Some(url.clone()),
                ..empty_result("europepmc_pdf")
            };
            self.download_and_cache_pdf(
                &url,
                cache_id,
                &mut result,
                PdfOrigin::EuropepmcPdf,
                None,
                library,
            );
            // A failed one is no better than the caller's held failure, and
            // would replace its address and cause (#739).
            if result.pdf_download_failure.is_none() || pdf.is_none() {
                return Ok(self.with_abstract_fallback(result, abstract_only.as_ref(), cache_id));
            }
        }

        // Tier 2: Unpaywall, by the search hit's DOI when the caller gave none
        // (#494): Tier 1d leaves a free PDF Europe PMC lists on another site to
        // this tier, so without it a PMID-only caller would lose that PDF. Tier
        // 3's link below stays the caller's own.
        if let (None, Some(found)) = (doi, hit_doi.as_deref()) {
            self.debug(format!(
                "Asking Unpaywall by the DOI {found} the search hit for PMID {pmid} carries"
            ));
        }
        if let Some(doi) = doi.or(hit_doi.as_deref()) {
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
                        None,
                        library,
                    );
                    if result.pdf_download_failure.is_none() || pdf.is_none() {
                        // As Tier 1d's.
                        return Ok(self.with_abstract_fallback(
                            result,
                            abstract_only.as_ref(),
                            cache_id,
                        ));
                    }
                }
                Err(fault) => {
                    self.debug(format!("Unpaywall failed for DOI {doi}: {}", fault.message));
                    failures.record(&fault);
                }
            }
        }

        // A caller's free PDF that failed to download, now that no tier did
        // better (#739): its address and cause beat a bare link. Every tier
        // came back empty-handed, so #67's summary is owed here as below.
        if let Some(failed) = pdf {
            let outcome = format!(
                "returning the caller's PDF link, whose download failed ({})",
                failed.pdf_download_failure.as_deref().unwrap_or_default()
            );
            self.report_exhausted(doi, pmid, hit_doi.as_deref(), &outcome, failures);
            return Ok(self.with_abstract_fallback(failed, abstract_only.as_ref(), cache_id));
        }

        // Tier 3: DOI / PubMed fallback. When a body-less JATS was seen
        // earlier, keep its abstract and hang the link off it — the reader gets
        // both, rather than a bare link.
        let mut web_url: Option<String> = None;
        let mut link_source = if doi.is_some() { "doi" } else { "pubmed" };
        if let Some(link) = &held_link {
            // The caller's link ahead of bmlib's guess at one (#579).
            self.info(format!(
                "Falling back to the caller's link from {}",
                link.source
            ));
            web_url = Some(link.url.clone());
            link_source = &link.source;
        } else if let Some(doi) = doi {
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

        // One report for every empty-handed exit — the held PDF's above, the
        // two returns below and the raise. Keeping it inside the abstract branch made the *more*
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
        self.report_exhausted(doi, pmid, hit_doi.as_deref(), outcome, failures);

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
            None => Err(FullTextError::Exhausted {
                message: format!(
                    "Nothing retrieved and no DOI or PMID to fall back on — {}",
                    failures.describe()
                ),
                faults: failures.faults().iter().map(ToString::to_string).collect(),
                absences: failures.absences(),
            }),
            Some(url) => Ok(FullTextResult {
                web_url: Some(url),
                ..empty_result(link_source)
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
    ///
    /// Where the PDF was cached, the abstract is cached beside it (#305),
    /// **whether or not the PDF yielded text this time**. A hit on that PDF
    /// short-circuits the whole chain for good, so it is the only route by which
    /// a later call can return the abstract this one did; and whether the PDF
    /// yields text depends on `convert_pdfs` and the PDF backend, which can
    /// differ between the call that caches and the call that hits.
    fn with_abstract_fallback(
        &self,
        mut result: FullTextResult,
        abstract_only: Option<&FullTextResult>,
        cache_id: Option<&str>,
    ) -> FullTextResult {
        let Some(abstract_only) = abstract_only else {
            return result;
        };
        let abstract_html = abstract_only
            .html
            .as_deref()
            .filter(|html| !html.is_empty());
        let has_file = result
            .file_path
            .as_deref()
            .is_some_and(|path| !path.is_empty());
        if let (Some(abstract_html), true, Some(cache_id), Some(cache)) =
            (abstract_html, has_file, cache_id, self.cache.as_ref())
        {
            // The XML first, as for an HTML entry, so the abstract is cached
            // only once its XML is. Not where an HTML entry is present, so its
            // XML is never replaced by an abstract's (Python serves a stale one
            // over a re-fetch that returned less, #172).
            let saved = match abstract_only.jats_xml.as_deref() {
                Some(xml) if !cache.has_html(cache_id) => {
                    cache.save_jats(xml, cache_id).map(|_| ())
                }
                _ => Ok(()),
            }
            .and_then(|()| cache.save_abstract(abstract_html, cache_id).map(|_| ()));
            if let Err(error) = saved {
                // Not `warn_cache_write_failed`: the PDF *was* cached, so "nothing
                // is being cached" would be false — an older cache whose root is
                // read-only cannot create `abstracts/` while `pdfs/` writes fine
                // — and spending that warning's one-shot key here would silence a
                // later directory-wide fault of the same type.
                let kind = format!("{:?}", error.kind());
                self.warn_once(
                    &format!("abstract-write:{kind}"),
                    format!(
                        "Could not cache the abstract, or the JATS XML it was rendered from, beside a cached PDF ({kind}: {error}); the PDF is cached, but a later hit on it that yields no text will return no abstract. Further {kind} failures will not be repeated."
                    ),
                );
                self.debug(format!("Failed to cache the abstract for {cache_id}"));
            }
        }
        let has_text = result.html.as_deref().is_some_and(|html| !html.is_empty());
        if has_text {
            return result;
        }
        result.html = abstract_only.html.clone();
        result.content_kind = ContentKind::Abstract;
        result.jats_xml = abstract_only.jats_xml.clone();
        self.info("PDF yielded no text — pairing the link with the abstract-only rendering");
        result
    }

    /// Log #67's summary for a chain that came back empty-handed.
    ///
    /// `outcome` says what the caller is given instead of full text, and
    /// `hit_doi` is the search hit's DOI Unpaywall was asked by, if it was.
    fn report_exhausted(
        &self,
        doi: Option<&str>,
        pmid: &str,
        hit_doi: Option<&str>,
        outcome: &str,
        failures: &TierFailures<'_>,
    ) {
        // A borrowed DOI is named here, or the failure it caused is reported
        // against `doi=None` with the DOI itself at DEBUG alone (#494).
        let borrowed = hit_doi
            .map(|hit_doi| format!(" (Unpaywall asked by the search hit's DOI {hit_doi})"))
            .unwrap_or_default();
        self.warn(format!(
            "No full text found for doi={doi:?} pmid={pmid}{borrowed} — {outcome}; {}",
            failures.describe()
        ));
    }

    /// `result`, JATS full text, with a PDF on it for display (#739).
    ///
    /// The caller's PDF from Tier 0 rides on it as is unless
    /// [`FullTextService::with_fetch_pdf_with_jats`] is on and that PDF is
    /// missing or failed. Then a PDF is sought: the caller's `pdf` entries when
    /// Tier 0 tried none (its xml entry won first), the PMC ID's in PMC's AWS
    /// Open Data bucket (#748), Europe PMC's free PDF when its search resolves
    /// to the PMC ID the JATS came from, and Unpaywall by the caller's DOI or,
    /// failing that, that same hit's DOI. The first saved goes on `result`; if
    /// none is, the first failure that may pass does, else a failed search's
    /// [`PDF_SEARCH_FAILED`] (a failed Open Data listing or Unpaywall lookup
    /// counts as one), else the first failure (#746). A failed search, Open
    /// Data listing or Unpaywall lookup is recorded on `failures`.
    ///
    /// Short of a saved caller's PDF, the library's PDF held for `library`
    /// goes on `result` in place of any download, whether or not
    /// `fetch_pdf_with_jats` is on (a later lookup serves the two together
    /// anyway), and the cache's mark is removed as for a saved download.
    fn pdf_for_jats(
        &self,
        result: FullTextResult,
        pdf: Option<&FullTextResult>,
        found: &JatsFound<'_>,
        cache_id: Option<&str>,
        failures: &mut TierFailures<'_>,
        library: &mut FromLibrary,
    ) -> FullTextResult {
        let pdf_saved = pdf.is_some_and(|held| held.pdf_download_failure.is_none());
        if !pdf_saved {
            if let Some(path) = self.use_library_pdf(library) {
                self.mark_pdf_retry(cache_id, found.pmc_id, None);
                let held = FullTextResult {
                    file_path: Some(path.display().to_string()),
                    ..empty_result(LIBRARY_SOURCE)
                };
                return with_pdf(result, Some(&held));
            }
        }
        if !self.fetch_pdf_with_jats || pdf_saved {
            return with_pdf(result, pdf);
        }
        let mut search_failed = false;
        // The search was asked here, so a request was made.
        let mut searched = false;
        let mut candidates: Vec<(String, String, PdfOrigin)> = Vec::new();
        if pdf.is_none() {
            candidates.extend(
                found
                    .request
                    .fulltext_sources
                    .iter()
                    .filter(|entry| entry.format == "pdf")
                    .map(|entry| {
                        (
                            entry.source.clone(),
                            entry.url.clone(),
                            PdfOrigin::KnownSource,
                        )
                    }),
            );
        }
        let pmid = found.request.pmid.as_str();
        let mut hit_doi: Option<String> = None;
        let mut europepmc_render: Option<String> = None;
        if let Some(pmc_id) = found.pmc_id {
            if found.doi.is_some() || !pmid.is_empty() {
                let (found_id, render_url, found_doi) = match &found.search {
                    Some(search) => search.clone(),
                    None => {
                        let search = self.discover_pmc_id(found.doi, pmid, false, failures).1;
                        searched = true;
                        search_failed = search.is_none();
                        search.unwrap_or_default()
                    }
                };
                if found_id.as_deref() == Some(pmc_id) {
                    // The same article's PDF only.
                    hit_doi = found_doi;
                    europepmc_render = render_url;
                }
            }
            // The JATS's own article, so no search need name it (#748).
            candidates.push((
                "pmc_open_data".into(),
                pmc_id.to_string(),
                PdfOrigin::PmcOpenData,
            ));
        }
        if let Some(url) = europepmc_render {
            candidates.push(("europepmc_pdf".into(), url, PdfOrigin::EuropepmcPdf));
        }
        if let Some(doi) = found.doi.map(str::to_string).or(hit_doi) {
            candidates.push(("unpaywall".into(), doi, PdfOrigin::Unpaywall));
        }

        let mut first_failure = pdf.cloned();
        // The first failure a later lookup may cure (#744), the one reported
        // when there is one, so the mark is kept exactly when the result's
        // failure may pass, bar the seek limit (#746).
        let mut passing = pdf.filter(|held| held.pdf_failure_may_pass()).cloned();
        let tried_any = searched || !candidates.is_empty();
        for (source, target, origin) in candidates {
            // Only the bucket's record gives an md5 (#750).
            let resolved = match origin {
                PdfOrigin::Unpaywall => Some(self.fetch_unpaywall(&target).map(|url| (url, None))),
                PdfOrigin::PmcOpenData => Some(
                    self.fetch_pmc_open_data_pdf(&target)
                        .map(|(url, md5)| (url, Some(md5))),
                ),
                PdfOrigin::EuropepmcPdf | PdfOrigin::KnownSource => None,
            };
            let (url, md5) = match resolved {
                None => (target, None),
                Some(Ok(found)) => found,
                Some(Err(fault)) => {
                    self.debug(format!("{source} failed for {target}: {}", fault.message));
                    failures.record(&fault);
                    // Answering it has no free PDF is no failure.
                    search_failed |= fault.kind != FaultKind::Unavailable;
                    continue;
                }
            };
            let attempts = if found.seeks > 0 {
                1
            } else {
                PDF_BESIDE_JATS_ATTEMPTS
            };
            let candidate = self.download_beside_jats(
                &url,
                cache_id,
                &source,
                origin,
                attempts,
                md5.as_deref(),
                library,
            );
            if candidate.pdf_download_failure.is_none() {
                if candidate.file_path.is_some() {
                    self.info(format!("PDF from {source} saved beside the JATS full text"));
                } else {
                    // No cache or identifier: left a URL, as every PDF tier leaves it.
                    self.info(format!(
                        "PDF from {source} put on the JATS full text, unsaved"
                    ));
                }
                self.mark_pdf_retry(cache_id, found.pmc_id, None);
                return with_pdf(result, Some(&candidate));
            }
            if passing.is_none() && candidate.pdf_failure_may_pass() {
                passing = Some(candidate.clone());
            }
            if first_failure.is_none() {
                first_failure = Some(candidate);
            }
        }
        if passing.is_none() && search_failed {
            // The search for a PDF failed and no download that failed may pass
            // (#744, #746): said on the result, so it does not read as "no PDF
            // exists", nor a final download failure as the last word. That
            // download's address stays on the result, and its cause in the log.
            let outranked = first_failure.as_ref().map_or_else(String::new, |failed| {
                format!(
                    "; the PDF at {} failed finally ({})",
                    failed.pdf_url.as_deref().unwrap_or_default(),
                    failed.pdf_download_failure.as_deref().unwrap_or_default()
                )
            });
            self.info(format!(
                "The search for a PDF beside the JATS full text of {} failed{outranked}",
                cache_id.unwrap_or_default()
            ));
            passing = Some(FullTextResult {
                pdf_url: first_failure
                    .as_ref()
                    .and_then(|failed| failed.pdf_url.clone()),
                pdf_download_failure: Some(PDF_SEARCH_FAILED.to_string()),
                ..empty_result(&result.source)
            });
        }
        let retry = passing.is_some() && found.seeks < PDF_SEEK_AGAIN_LIMIT;
        let reported = passing.or(first_failure);
        if tried_any || reported.is_some() {
            // Else no request was made, and the mark stays as it is.
            self.mark_pdf_retry(cache_id, found.pmc_id, retry.then_some(found.seeks));
        }
        with_pdf(result, reported.as_ref())
    }

    /// Download the PDF at `url` for a JATS result, retrying with backoff
    /// (#744).
    ///
    /// A failure that may pass ([`FullTextResult::pdf_failure_may_pass`]) is
    /// tried again, up to `attempts` tries in all, waiting the retry delay before
    /// the second try, each later wait twice the one before. Any other
    /// outcome is returned at once. Returns the last try's result: saved, or
    /// carrying its `pdf_download_failure`.
    #[allow(clippy::too_many_arguments)]
    fn download_beside_jats(
        &self,
        url: &str,
        cache_id: Option<&str>,
        source: &str,
        origin: PdfOrigin,
        attempts: u32,
        md5: Option<&str>,
        library: &mut FromLibrary,
    ) -> FullTextResult {
        let mut attempt = 1;
        loop {
            let mut found = FullTextResult {
                pdf_url: Some(url.to_string()),
                ..empty_result(source)
            };
            self.download_and_cache_pdf(url, cache_id, &mut found, origin, md5, library);
            if attempt >= attempts || !found.pdf_failure_may_pass() {
                return found;
            }
            let delay = self.pdf_retry_delay * 2u32.pow(attempt - 1);
            self.info(format!(
                "The PDF from {source} failed ({}); trying again in {} s",
                found.pdf_download_failure.as_deref().unwrap_or_default(),
                delay.as_secs_f64()
            ));
            std::thread::sleep(delay);
            attempt += 1;
        }
    }

    /// Mark the cached JATS as owing a PDF a later hit seeks again, keeping
    /// the PMC ID it came from and `seeks`, the hits that have sought it again
    /// so far; `None` unmarks it (#744). Returns whether the mark was written
    /// or removed; `true` with no cache.
    ///
    /// Best effort, as every cache write here: a mark that cannot be saved
    /// costs only the later look, and one that cannot be removed only a
    /// bounded number of them, since a hit counts itself before it seeks
    /// ([`FullTextService::seek_pdf_again`]). Warned once per cause under its
    /// own key, since the cache's other entries may still be written.
    fn mark_pdf_retry(
        &self,
        cache_id: Option<&str>,
        pmc_id: Option<&str>,
        seeks: Option<u32>,
    ) -> bool {
        let (Some(cache), Some(cache_id)) = (self.cache.as_ref(), cache_id) else {
            return true;
        };
        match seeks {
            Some(seeks) => match cache.save_pdf_retry(cache_id, pmc_id, seeks) {
                Ok(_) => true,
                Err(error) => {
                    let kind = format!("{:?}", error.kind());
                    self.warn_once(
                        &format!("pdf-mark-write:{kind}"),
                        format!(
                            "Could not write the PDF mark in the full-text cache ({kind}: {error}); a later lookup may not seek the PDF beside the JATS again."
                        ),
                    );
                    false
                }
            },
            None => {
                let removed = cache.delete_pdf_retry(cache_id);
                if !removed {
                    self.warn_once(
                        "pdf-mark-delete",
                        "Could not remove the PDF mark from the full-text cache; it stays in place.",
                    );
                }
                removed
            }
        }
    }

    /// `hit`, with the PDF beside it sought again if an earlier try may pass
    /// (#744).
    ///
    /// Only a JATS hit with no PDF beside it, and only when
    /// `fetch_pdf_with_jats` is on and the cache marks it
    /// ([`FullTextCache::save_pdf_retry`]): the PDF is then sought as
    /// [`FullTextService::pdf_for_jats`] seeks it, by the PMC ID the mark
    /// names, one try per candidate, at most [`PDF_SEEK_AGAIN_LIMIT`] hits in
    /// all. The hit counts itself in the mark *before* it seeks, and seeks
    /// only if that write succeeds, so a mark that later cannot be updated
    /// still bounds the requests. Any other hit is returned as it is, so a
    /// paper with no PDF anywhere costs no request, and so is a hit with
    /// nothing to try, which leaves the mark as it is. The search's and
    /// Unpaywall's faults go on `tier_faults`. A PDF found goes to the
    /// library when `library` is in use, as in the chain.
    ///
    /// A library JATS hit with no mark is sought once, as a first fetch seeks
    /// it (by the paper's PMC ID, with retries), when this lookup first noted
    /// the JATS in this installation's record
    /// ([`FromLibrary::jats_first_noted`]); a failure that may pass marks it as
    /// above. Its record then lists the JATS, so a later hit without a mark
    /// makes no request: the record is the "sought once" state, and no cache
    /// state is added. A lookup whose note failed seeks nothing.
    fn seek_pdf_again(
        &self,
        hit: FullTextResult,
        cache: &FullTextCache,
        cache_id: &str,
        request: &FullTextRequest,
        library: &mut FromLibrary,
    ) -> FullTextResult {
        if !(self.fetch_pdf_with_jats
            && hit.content_kind == ContentKind::Fulltext
            && hit.file_path.is_none())
        {
            return hit;
        }
        let (marked, seeks, first) = match cache.get_pdf_retry(cache_id) {
            Ok(Some((marked, seeks))) => (marked, seeks, false),
            Ok(None) if library.jats_first_noted => {
                let paper = library.paper.as_ref().and_then(|p| p.ids.pmcid.clone());
                (paper.unwrap_or_default(), 0, true)
            }
            Ok(None) => return hit,
            Err(error) => {
                self.warn(format!(
                    "Could not read the PDF mark cached beside {cache_id} ({:?}: {error}); removing it and serving the cached full text without seeking its PDF.",
                    error.kind()
                ));
                self.mark_pdf_retry(Some(cache_id), None, None);
                return hit;
            }
        };
        let doi = request.doi.as_deref().filter(|doi| !doi.is_empty());
        let pmc_id = Some(marked.as_str()).filter(|id| !id.is_empty());
        // What pdf_for_jats could ask: a pdf source, Unpaywall by DOI, or PMC
        // Open Data and the search by the PMC ID (#748).
        let can_try = request
            .fulltext_sources
            .iter()
            .any(|entry| entry.format == "pdf")
            || doi.is_some()
            || pmc_id.is_some();
        if !can_try {
            return hit;
        }
        let seeks = if first {
            self.info(format!(
                "Seeking the PDF beside the library's full text of {cache_id}, new to this installation"
            ));
            0
        } else {
            let seeks = seeks.saturating_add(1);
            let counted = (seeks < PDF_SEEK_AGAIN_LIMIT).then_some(seeks);
            if !self.mark_pdf_retry(Some(cache_id), pmc_id, counted) {
                return hit; // an uncounted seek could repeat on every hit
            }
            self.info(format!(
                "Seeking again the PDF beside the cached full text of {cache_id}"
            ));
            seeks
        };
        let on_bug = |fault: &TierFault| self.warn_swallowed_bug(fault);
        let mut failures = TierFailures::reported(&on_bug);
        let found = JatsFound {
            request,
            doi,
            pmc_id,
            search: None,
            seeks,
        };
        let mut result =
            self.pdf_for_jats(hit, None, &found, Some(cache_id), &mut failures, library);
        result.tier_faults = failures.faults().iter().map(ToString::to_string).collect();
        result
    }

    /// Try fetcher-provided full-text sources in priority order.
    ///
    /// Priority: xml (JATS) > pdf, and the sort is stable so two entries of one
    /// format keep the order the fetcher gave them; a pdf entry and an html
    /// entry are held, so the JATS tiers after this one still run (#579, #739).
    ///
    /// Returns `(result, abstract_only, held_link, pdf)`. `result` is JATS full
    /// text, or `None` when no xml entry gave it. `held_link` is the first html
    /// entry: a link and not text, so it ends nothing here, and the caller uses
    /// it only once every tier that can fetch text has failed (#579). Returned
    /// at once, it put a publisher link ahead of Europe PMC's full text for
    /// every PubMed record carrying a DOI. `abstract_only` holds a body-less JATS rendering if one was
    /// seen. It is never worth stopping on, because a publisher that serves an
    /// abstract-only JATS (medRxiv does) generally serves the complete article
    /// as a PDF alongside it. `pdf` is the first pdf entry that was saved, else
    /// the first whose download failed (`pdf_download_failure` set). It ends
    /// nothing either (#739): JATS is better to process than a PDF's text, and a
    /// PDF better to show, so the caller keeps looking for JATS and puts that
    /// PDF, saved or failed, on any JATS it finds. While `library` holds the
    /// paper's PDF no pdf entry is downloaded: the library's serves in its
    /// place.
    fn try_known_sources(
        &self,
        sources: &[FullTextSourceEntry],
        cache_id: Option<&str>,
        failures: &mut TierFailures<'_>,
        library: &mut FromLibrary,
    ) -> (
        Option<FullTextResult>,
        Option<FullTextResult>,
        Option<FullTextSourceEntry>,
        Option<FullTextResult>,
    ) {
        let priority = |format: &str| match format {
            "xml" => 0,
            "pdf" => 1,
            "html" => 2,
            _ => 99,
        };
        let mut sorted: Vec<&FullTextSourceEntry> = sources.iter().collect();
        sorted.sort_by_key(|entry| priority(&entry.format));

        let mut abstract_only: Option<FullTextResult> = None;
        let mut held_link: Option<FullTextSourceEntry> = None;
        let mut pdf: Option<FullTextResult> = None;
        // The library's PDF serves in place of the caller's (design §4.5).
        let library_pdf = library.pdf.is_some() && self.keeper(library).is_some();
        if library_pdf && sorted.iter().any(|entry| entry.format == "pdf") {
            self.info(
                "The library holds the paper's PDF, so the caller's PDF sources are not downloaded",
            );
        }
        for entry in sorted {
            if entry.format == "xml" {
                match self.fetch_jats_xml(&entry.url) {
                    Ok((html, false, xml)) => {
                        // Not cached: a later fetch may find a populated
                        // document, and caching this would make the abstract
                        // permanent.
                        self.info(format!(
                            "JATS XML from {} has no body — keeping it only as a fallback and looking for the full article",
                            entry.source
                        ));
                        if abstract_only.is_none() {
                            abstract_only = Some(abstract_result(&entry.source, html, Some(xml)));
                        }
                        continue;
                    }
                    Ok((html, true, xml)) => {
                        self.info(format!("Full text from JATS XML ({})", entry.source));
                        self.keep_jats(&html, &xml, &entry.source, "", cache_id, library);
                        return (
                            Some(fulltext_result(&entry.source, html, Some(xml))),
                            abstract_only,
                            held_link,
                            pdf,
                        );
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
                if library_pdf
                    || pdf
                        .as_ref()
                        .is_some_and(|held| held.pdf_download_failure.is_none())
                {
                    continue; // one PDF in hand is enough
                }
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
                    None,
                    library,
                );
                // A PDF ends nothing (#739): JATS reads better than a PDF's
                // text, and a publisher's 403 used to hide the JATS a later
                // tier serves. A saved PDF replaces a failed one.
                if pdf.is_none() || result.pdf_download_failure.is_none() {
                    pdf = Some(result);
                }
            } else if entry.format == "html" {
                self.info(format!(
                    "HTML source from {}, held as a last resort",
                    entry.source
                ));
                if held_link.is_none() {
                    held_link = Some(entry.clone());
                }
            }
            // QUIRK: an entry whose `format` is not one of the three matches no
            // branch, so the loop moves on with nothing logged and nothing
            // recorded on `failures` — the entry is *silently* skipped, and an
            // exhaustion report describing a run of such entries reads as "no
            // attempt reported a failure". Reproduced from the Python, where a
            // typo in a fetcher's `format` is invisible.
            // An unknown format matches no branch and the loop continues.
        }

        (None, abstract_only, held_link, pdf)
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
    /// Where the PDF yields no text, the abstract the retrieval paired it with is
    /// read from beside it (#305), which makes that promise hold for this case
    /// too. The PDF branch's result may still carry **no** text and no abstract:
    /// it is a hit either way, with `content_kind` unset and `file_path` set.
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
                let jats_xml = self.read_cached_jats(cache, cache_id);
                self.info(format!("Cache hit (HTML) for {cache_id}"));
                let mut hit = fulltext_result("cached", html, jats_xml);
                // The PDF saved beside the JATS, for display (#739);
                // fetch_fulltext seeks it again when the cache marks it (#744).
                // shortcut: a JATS hit cached without a mark, as one cached while
                // fetch_pdf_with_jats was off, seeks no PDF; mark such entries if
                // apps turn the option on after caching.
                hit.file_path = self.cached_pdf_beside(cache, cache_id);
                return Some(hit);
            }
        }
        // The PDF entry is consulted the same way, and **presence without a hit
        // is an unreadable entry** (defect #309). Python's `get_pdf` tested only
        // `exists()`, so a directory — or any file that cannot be opened — was
        // served as a cached PDF, the swallowed conversion failure left
        // `html = None`, and the same bogus hit came back on every later run.
        // `get_pdf` now refuses it; quarantining it here is what keeps it from
        // hiding the entry a re-fetch is about to write.
        let pdf_path = cache
            .pdf_dir()
            .join(format!("{}.pdf", safe_filename(cache_id)));
        let pdf = cache.get_pdf(cache_id);
        if pdf.is_none() && pdf_path.exists() {
            self.warn(format!(
                "Could not read the cached PDF for {cache_id} (Unreadable: the entry could not be read back); re-fetching."
            ));
            self.debug(format!("Cache read failed for {cache_id}"));
            self.quarantine_cache_entry(cache, cache_id);
            return None;
        }
        if let Some(pdf_path) = pdf {
            self.info(format!("Cache hit (PDF) for {cache_id}"));
            let mut result = empty_result("cached");
            result.file_path = Some(pdf_path.display().to_string());
            self.attach_pdf_text(&pdf_path, &mut result);
            // #305: where the PDF yields no text, the abstract the retrieval
            // paired it with is read from beside it, which is what makes the
            // "same html and content_kind as the original retrieval" promise hold
            // for that case too. It is consulted only here — alone, without a
            // PDF, it is never a hit, since a later retrieval may still find the
            // whole article.
            if result.html.as_deref().is_none_or(str::is_empty) {
                let abstract_path = cache
                    .abstract_dir()
                    .join(format!("{}.html", safe_filename(cache_id)));
                let abstract_html = cache.get_abstract(cache_id);
                if abstract_html.is_none() && abstract_path.exists() {
                    // Present and unreadable. Python's `get_abstract` raises, so
                    // the raise abandons the *whole* cache read — warned,
                    // quarantined, and the chain re-fetched — rather than
                    // returning a hit with no text.
                    self.warn(format!(
                        "Could not read the cached full text for {cache_id} (Unreadable: the entry could not be read back); re-fetching."
                    ));
                    self.debug(format!("Cache read failed for {cache_id}"));
                    self.quarantine_cache_entry(cache, cache_id);
                    return None;
                }
                // QUIRK: Python's test is `if abstract:`, so an entry holding the
                // empty string does not become the abstract. Reproduced.
                if let Some(abstract_html) = abstract_html {
                    if !abstract_html.is_empty() {
                        result.html = Some(abstract_html);
                        result.content_kind = ContentKind::Abstract;
                        result.jats_xml = self.read_cached_jats(cache, cache_id);
                    }
                }
            }
            return Some(result);
        }
        None
    }

    /// The path of a PDF cached beside a JATS hit, or `None`.
    ///
    /// The hit is the JATS; the PDF only rides on it for display (#739), so
    /// one that is present but cannot be opened is reported, moved aside as
    /// [`FullTextService::read_cached_jats`] moves its own, and the hit served
    /// without it, as Python does for the `OSError` its `get_pdf` raises. Left
    /// in place, it was warned about on every lookup for good.
    fn cached_pdf_beside(&self, cache: &FullTextCache, cache_id: &str) -> Option<String> {
        if let Some(path) = cache.get_pdf(cache_id) {
            return Some(path.display().to_string());
        }
        let path = cache
            .pdf_dir()
            .join(format!("{}.pdf", safe_filename(cache_id)));
        if path.exists() {
            self.warn(format!(
                "Could not open the PDF cached beside {cache_id} (Unreadable: the entry could not be read back); serving the cached full text without it."
            ));
            self.debug(format!("PDF cache read failed for {cache_id}"));
            self.quarantine_cache_entry(cache, cache_id);
        }
        None
    }

    /// The JATS XML cached beside a hit, or `None`.
    ///
    /// An unreadable one is reported and moved aside, and the hit is served
    /// without it (the maintainer's call, PR #545's review): the rendering
    /// beside it reads fine, and abandoning the hit for it turned a cache used
    /// offline into a failure, or a lower-ranked PDF, for that lookup. Later
    /// lookups serve the rendering with `None`, as for an entry an earlier bmlib
    /// wrote.
    fn read_cached_jats(&self, cache: &FullTextCache, cache_id: &str) -> Option<Vec<u8>> {
        match cache.get_jats(cache_id) {
            Ok(jats_xml) => jats_xml,
            Err(error) => {
                self.warn(format!(
                    "Could not read the cached JATS XML for {cache_id} ({:?}: {error}); serving the cached rendering without it.",
                    error.kind()
                ));
                self.debug(format!("JATS cache read failed for {cache_id}"));
                self.quarantine_cache_entry(cache, cache_id);
                None
            }
        }
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

    /// Save HTML, and the JATS XML it was rendered from, to the disk cache if
    /// caching is enabled.
    ///
    /// The XML is written first and the HTML only once it is on disk.
    ///
    /// Failing to write costs only a re-fetch next run — the HTML is already in
    /// hand and is returned either way — so no cache means nothing to say here
    /// beyond the warning construction has already emitted.
    fn cache_html(&self, html: &str, xml: &[u8], cache_id: Option<&str>) {
        let (Some(cache_id), Some(cache)) = (cache_id, self.cache.as_ref()) else {
            return;
        };
        if cache_id.is_empty() {
            return;
        }
        if let Err(error) = cache.save_jats(xml, cache_id) {
            // Not `warn_cache_write_failed`: `jats/` is created on first save, so
            // an older cache whose root is read-only fails here while `pdfs/`
            // writes fine, and "nothing is being cached" would be false.
            let kind = format!("{:?}", error.kind());
            self.warn_once(
                &format!("jats-write:{kind}"),
                format!(
                    "Could not cache JATS XML ({kind}: {error}), so the HTML rendered from it is not cached either and that article is re-fetched at its next lookup; PDFs are cached as before. Further {kind} failures will not be repeated."
                ),
            );
            self.debug(format!("Failed to cache the JATS XML for {cache_id}"));
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
    /// When `library` is in use the PDF is downloaded with or without a cache
    /// or an identifier, and its bytes, once they pass the md5 and `%PDF`
    /// checks, are kept in the library ([`FullTextService::keep_pdf`]):
    /// `file_path` is then the library's file. If the library fails, the PDF
    /// goes to the cache as without one, or, with no cache or identifier,
    /// only its URL stays on the result, as without a cache.
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
        md5: Option<&str>,
        library: &mut FromLibrary,
    ) {
        let keeper = self.keeper(library);
        if keeper.is_none() {
            if self.cache.is_none() {
                self.debug(format!(
                    "The full-text cache could not be created, so {pdf_url} is not downloaded — the URL is left on the result, and there is no file to extract text from"
                ));
                return;
            }
            if cache_id.is_none() {
                if self.convert_pdfs {
                    self.info(format!(
                        "convert_pdfs is on but no identifier was given — a PDF is only extracted once cached, so {pdf_url} is left as a URL"
                    ));
                }
                return;
            }
        }

        let response = match self.http_get(pdf_url) {
            Ok(response) => response,
            Err(fault) => {
                self.report_pdf_download_exception(&result.source, origin, pdf_url, &fault);
                result.pdf_download_failure = Some("request-failed".to_string());
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
            result.pdf_download_failure = Some(format!("http-{}", response.status));
            return;
        }
        if md5.is_some_and(|md5| md5_hex(&response.body) != md5) {
            self.report_pdf_download_failure(
                &result.source,
                origin,
                pdf_url,
                "the PDF does not match the md5 its record gives",
                PDF_CHECKSUM_MISMATCH,
            );
            result.pdf_download_failure = Some(PDF_CHECKSUM_MISMATCH.to_string());
            return;
        }

        if let Some(store) = keeper {
            // The cache's own check, made before the library keeps the bytes.
            if !response.body.starts_with(PDF_MAGIC_BYTES) {
                self.report_pdf_download_failure(
                    &result.source,
                    origin,
                    pdf_url,
                    "the response is not a PDF",
                    "not-a-pdf",
                );
                result.pdf_download_failure = Some("not-a-pdf".to_string());
                return;
            }
            if let Some(path) = self.keep_pdf(store, &response.body, pdf_url, library) {
                result.file_path = Some(path.display().to_string());
                self.info(format!("PDF kept in the library at {}", path.display()));
                self.attach_pdf_text(&path, result);
                return;
            }
        }
        // Without the library, or after it failed: the cache, else the URL alone.
        let (Some(cache), Some(cache_id)) = (self.cache.as_ref(), cache_id) else {
            return;
        };

        // **The bytes, not a decoded string.** The cache validates the `%PDF`
        // prefix and nothing else, so a lossy decode on this path is what wrote a
        // silently corrupted file: every non-UTF-8 byte became U+FFFD and the
        // prefix still matched.
        let (path, outcome) = self.save_pdf_to_cache(cache, &response.body, cache_id);
        let Some(path) = path else {
            let cause = if outcome == PdfSaveOutcome::NotAPdf {
                self.report_pdf_download_failure(
                    &result.source,
                    origin,
                    pdf_url,
                    "the response is not a PDF",
                    "not-a-pdf",
                );
                "not-a-pdf"
            } else {
                // "write-failed": warn_cache_write_failed has already spoken,
                // and it names the right cause. Saying anything more here would
                // blame the publisher for a read-only directory.
                "write-failed"
            };
            result.pdf_download_failure = Some(cause.to_string());
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
    /// extra missing, or broken) leaves the result untouched, and an unreadable
    /// PDF leaves it untouched but for `pdf_extraction_failure`, which names
    /// the side the failure is on (#604), so the caller still has the PDF
    /// itself. `result.pdf_url` and
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
            Err(PdfExtractError::Conversion(error)) => {
                self.warn(format!(
                    "PDF text extraction failed for {}: {}{}",
                    pdf_path.display(),
                    error.message,
                    engine_said(&error.engine_messages)
                ));
                result.pdf_extraction_failure = Some(error.kind.as_str().to_string());
            }
            Ok(conversion) => {
                if !conversion.success {
                    // A `PdfText` names no kind, so the failure is unplaced: `Engine`.
                    result.pdf_extraction_failure = Some(PdfErrorKind::Engine.as_str().to_string());
                    self.warn(format!(
                        "PDF text extraction failed for {}: {}",
                        pdf_path.display(),
                        conversion.error_message.unwrap_or_default()
                    ));
                    return;
                }
                if conversion.html.is_empty() {
                    // Not "likely a scan" when the engine said why: a page cut
                    // short after too many syntax errors also yields nothing,
                    // and its last message is the one that says so (#599).
                    let cause = if conversion.engine_messages.is_empty() {
                        "likely a scan; no warnings reported".to_string()
                    } else {
                        format!(
                            "the PDF engine reported {} message(s): {}",
                            conversion.engine_messages.len(),
                            conversion.engine_messages.join("; ")
                        )
                    };
                    self.warn(format!(
                        "PDF {} yielded no extractable text over {} page(s) — {cause}",
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
                // Python's converter logs each of these itself (#599).
                if !conversion.engine_messages.is_empty() {
                    self.warn(format!(
                        "PDF {} read with {} message(s) from the PDF engine, so text may be missing: {}",
                        pdf_path.display(),
                        conversion.engine_messages.len(),
                        conversion.engine_messages.join("; ")
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
    /// Returns the rendered HTML, whether the document actually had a body, and
    /// the XML as served. A body-less document renders to little more than the abstract, so the
    /// caller must keep looking for the real full text.
    ///
    /// # Errors
    ///
    /// [`TierFault::unavailable`] on a 404 — a fetcher's stored URL going stale
    /// is common, and counting it as a fault would inflate the one bucket the
    /// exhaustion report asks the operator to act on.
    fn fetch_jats_xml(&self, url: &str) -> Result<(String, bool, Vec<u8>), TierFault> {
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
        Ok((
            render_jats_html(&article),
            article.has_body,
            response.body.clone(),
        ))
    }

    /// Tiers 1a and 1c for **one** PMC ID: Europe PMC's XML, then NCBI's, then
    /// the bucket's.
    ///
    /// NCBI is asked only when Europe PMC gave no body — it serves the corpus its
    /// `inEPMC` flag describes, and NCBI serves PMC itself — and PMC's Open Data
    /// bucket only when neither did (#751): the same PMC rendition as NCBI's,
    /// served where NCBI fails or throttles. All ahead of the PDF tiers,
    /// because structured JATS beats a PDF that needs a PDF backend to read at
    /// all. One method for the caller's ID and a discovered one, since the two
    /// were separate copies of the same sequence.
    ///
    /// Returns `(result, abstract_only)`: the full text, or `None` when no
    /// source had a body, and the held-back rendering — **the first one seen**,
    /// earlier tiers' included, which is why the caller's own rendering is passed
    /// in.
    fn try_pmc_id(
        &self,
        pmc_id: &str,
        cache_id: Option<&str>,
        failures: &mut TierFailures<'_>,
        mut abstract_only: Option<FullTextResult>,
        library: &mut FromLibrary,
    ) -> (Option<FullTextResult>, Option<FullTextResult>) {
        type Fetch<'s> = fn(&FullTextService, &str) -> Result<(String, bool, Vec<u8>), TierFault>;
        let fetchers: [(&str, &str, Fetch<'_>); 3] = [
            ("europepmc", "Europe PMC", Self::fetch_europepmc),
            ("ncbi_pmc", "NCBI PMC", Self::fetch_ncbi_pmc),
            (
                "pmc_open_data",
                "PMC Open Data",
                Self::fetch_pmc_open_data_xml,
            ),
        ];
        for (source, name, fetch) in fetchers {
            match fetch(self, pmc_id) {
                Ok((html, true, xml)) => {
                    self.info(format!("Full text retrieved from {name} for {pmc_id}"));
                    self.keep_jats(&html, &xml, source, pmc_id, cache_id, library);
                    return (
                        Some(fulltext_result(source, html, Some(xml))),
                        abstract_only,
                    );
                }
                Ok((html, false, xml)) => {
                    self.info(format!(
                        "{name} XML for {pmc_id} has no body — looking further"
                    ));
                    if abstract_only.is_none() {
                        abstract_only = Some(abstract_result(source, html, Some(xml)));
                    }
                }
                Err(fault) => {
                    self.debug(format!("{name} failed for {pmc_id}: {}", fault.message));
                    failures.record(&fault);
                }
            }
        }
        (None, abstract_only)
    }

    /// Tiers 1b and 1b′: resolve a PMC ID, Europe PMC's free-PDF URL and the hit's DOI.
    ///
    /// Europe PMC's search is asked first because it returns the PMC ID and the
    /// free-PDF URL Tier 1d needs in one request. NCBI's ID Converter is asked
    /// when the search reported no ID — **including when the search itself
    /// failed**, since a second, independent resolver is worth most exactly then.
    ///
    /// `use_converter` is `false` when a usable caller ID has already failed:
    /// what supersedes it has to be the search hit the free PDF comes from, never
    /// a second resolver's answer (see [`FullTextService::retrieve`]).
    ///
    /// The search's ID arrives validated, so a malformed one is a recorded fault
    /// and the converter is asked instead.
    ///
    /// Returns `(pmc_id, Some((search_pmc_id, pdf_render_url, hit_doi)))`.
    /// `pmc_id` is the search's or, failing it, the ID Converter's; the inner
    /// tuple is the search's alone, so the free PDF is paired with the ID only
    /// the search gave it (#739). It is `None` when the search failed (#744): a
    /// fault it recorded while answering, such as an unusable field, is not a
    /// failed search.
    fn discover_pmc_id(
        &self,
        doi: Option<&str>,
        pmid: &str,
        use_converter: bool,
        failures: &mut TierFailures<'_>,
    ) -> (Option<String>, Option<SearchFindings>) {
        let search = match self.resolve_pmc_id_and_pdf_url(doi, pmid, failures) {
            Ok(found) => Some(found),
            Err(fault) => {
                self.debug(format!(
                    "Europe PMC search failed for doi={doi:?} pmid={pmid}: {}",
                    fault.message
                ));
                failures.record(&fault);
                None
            }
        };
        let mut resolved = search.as_ref().and_then(|found| found.0.clone());
        if resolved.is_none() && use_converter {
            resolved = self.resolve_pmc_id_via_idconv(doi, pmid, failures);
        }
        (resolved, search)
    }

    /// Search Europe PMC to discover a PMC ID, a free PDF URL and a DOI.
    ///
    /// The DOI is preferred when present; the PMID is used when there is no
    /// DOI. A search that finds no record is noted on `failures` as an absence.
    /// The hit's own DOI, stripped, is returned only for a search by PMID: Tier
    /// 2 needs a DOI, and a free PDF Europe PMC lists on another site is left
    /// to Tier 2 (#494), so a PMID-only caller would otherwise lose it.
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
    ) -> Result<SearchFindings, TierFault> {
        let query = if let Some(doi) = doi {
            format!("DOI:{doi}")
        } else if !pmid.is_empty() {
            // Within `SRC:MED`, Python's #474 rule: since #494 the hit's DOI is
            // sent to Unpaywall, so a colliding thesis first would fetch another
            // work's PDF. The rest of #474 (record check, canonical PMID) is #476.
            format!("EXT_ID:{pmid} AND SRC:MED")
        } else {
            return Ok((None, None, None));
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
            return Ok((None, None, None));
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

        let found = if matches!(hit.get("inEPMC"), Some(Value::String(value)) if value == "Y") {
            hit.get("pmcid")
        } else {
            None
        };
        // Validated **where it is read**, as the ID Converter's own answer is: a
        // malformed or non-string id is a WARNING and a recorded fault, and the
        // answer handed on is `None` so the converter is asked instead. Returned
        // raw, a bare numeric id did not compare equal to the caller's prefixed
        // one and a malformed one reached `fetch_europepmc` and `fetch_ncbi_pmc`
        // in turn — two faults and a converter never asked.
        let mut pmc_id: Option<String> = None;
        if let Some(found) = found.filter(|value| truthy(value)) {
            match normalise_pmc_value(found) {
                Ok(id) => pmc_id = Some(id),
                Err(error) => {
                    self.warn(format!(
                        "Europe PMC search returned an unusable PMC ID: {}",
                        python_repr(found)
                    ));
                    failures.record(&TierFault::from(error));
                }
            }
        }

        // Read only for a caller with no DOI, by `_normalise_doi`'s rule (#475).
        // A present, non-string one is the `pmcid` sibling's WARNING and fault:
        // dropped quietly, a PMID-only caller lost Tier 2 behind "no failure".
        let mut hit_doi: Option<String> = None;
        if let (None, Some(found)) = (doi, hit.get("doi").filter(|value| truthy(value))) {
            if let Value::String(found) = found {
                hit_doi = Some(python_strip(found))
                    .filter(|found| !found.is_empty())
                    .map(str::to_owned);
            } else {
                let message = format!(
                    "Europe PMC search returned an unusable DOI: {}",
                    python_repr(found)
                );
                self.warn(message.clone());
                failures.record(&TierFault::fault("FullTextError", message));
            }
        }
        Ok((pmc_id, extract_free_pdf_url(hit), hit_doi))
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
            // Python's `%s` of `record.get("errmsg")`, so an absent key is `None`.
            let errmsg = python_str(record.get("errmsg").unwrap_or(&Value::Null));
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
                    // Python formats the id with `%r`, so a string arrives quoted.
                    self.warn(format!(
                        "ID Converter returned an unusable PMC ID: {}",
                        other.map(python_repr).unwrap_or_default()
                    ));
                    // A malformed id is the converter misbehaving, not an
                    // absence — the record exists and says something unusable.
                    failures.record(&TierFault::fault(
                        "FullTextError",
                        format!(
                            "Unusable PMC ID: {}",
                            other.map(python_repr).unwrap_or_default()
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
    /// Returns the rendered HTML, whether the document had a body, and the XML
    /// as served.
    ///
    /// # Errors
    ///
    /// [`TierFault`] for a bad id, a 404 (as an absence) or a non-200.
    fn fetch_europepmc(&self, pmc_id: &str) -> Result<(String, bool, Vec<u8>), TierFault> {
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
        Ok((
            render_jats_html(&article),
            article.has_body,
            response.body.clone(),
        ))
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
    fn fetch_ncbi_pmc(&self, pmc_id: &str) -> Result<(String, bool, Vec<u8>), TierFault> {
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
        Ok((
            render_jats_html(&article),
            article.has_body,
            response.body.clone(),
        ))
    }

    /// `pmc_id`'s latest version's record in PMC's AWS Open Data bucket (#750).
    ///
    /// Two requests: the listing of the article's versions, then the latest
    /// one's `PMC<n>.<v>.json`. The record says whether PMC lists the article
    /// as retracted, its licence, and which files the version holds. `None`
    /// when the bucket holds no version of the article or no record for its
    /// latest: the bucket answering that it has none, as most articles outside
    /// PMC's open-access subset are. Python's `pmc_open_data_record`.
    ///
    /// # Errors
    ///
    /// [`FullTextError::Other`] on a malformed PMC ID, a failed request, a
    /// listing or record answered other than 200 (a missing record's 404
    /// aside), and a record that is not valid JSON or not of the shape read.
    pub fn pmc_open_data_record(
        &self,
        pmc_id: &str,
    ) -> Result<Option<PmcOpenDataRecord>, FullTextError> {
        self.open_data_record(pmc_id)
            .map_err(|fault| FullTextError::Other(fault.message))
    }

    /// [`FullTextService::pmc_open_data_record`], failing as a tier fails.
    fn open_data_record(&self, pmc_id: &str) -> Result<Option<PmcOpenDataRecord>, TierFault> {
        let pmc_id = normalise_pmc_id(pmc_id).map_err(TierFault::from)?;
        let listing = self.open_data_listing(&format!(
            "prefix={}&delimiter=/",
            quote(&format!("{pmc_id}."), "/")
        ))?;
        let Some(version) = latest_open_data_version(&listing, &pmc_id) else {
            return Ok(None);
        };
        let stem = format!("{pmc_id}.{version}");
        let response = self.http_get(&format!("{PMC_OPEN_DATA_BASE}/{stem}/{stem}.json"))?;
        if response.status == 404 {
            return Ok(None);
        }
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("PMC Open Data HTTP {}", response.status),
            ));
        }
        let data: Value = serde_json::from_slice(&response.body).map_err(|_| {
            TierFault::fault(
                "FullTextError",
                format!("PMC Open Data record {stem} is not JSON"),
            )
        })?;
        open_data_record(&data, &pmc_id, version).map(Some)
    }

    /// Fetch `pmc_id`'s JATS from PMC's AWS Open Data bucket (#751).
    ///
    /// The latest version's XML, as its record names it, checked against the
    /// record's md5. Returns the rendered HTML, whether the document had a
    /// body, and the XML as served, as [`FullTextService::fetch_ncbi_pmc`].
    ///
    /// # Errors
    ///
    /// [`TierFault::fault`] as [`FullTextService::pmc_open_data_record`]; on the
    /// XML answered other than 200, which the record says is there; on bytes
    /// that do not match its md5; and on XML that cannot be read or parsed
    /// (`SAXParseException`, as in Python). [`TierFault::unavailable`] when the
    /// bucket holds no record of the article, its latest version no XML, or
    /// the XML no article content.
    fn fetch_pmc_open_data_xml(&self, pmc_id: &str) -> Result<(String, bool, Vec<u8>), TierFault> {
        let Some(record) = self.open_data_record(pmc_id)? else {
            return Err(TierFault::unavailable(format!(
                "{pmc_id} is not in PMC's Open Data bucket"
            )));
        };
        let stem = record.stem();
        let Some(xml_md5) = record.xml_md5.as_deref() else {
            return Err(TierFault::unavailable(format!(
                "{stem} has no JATS in PMC's Open Data"
            )));
        };
        let response = self.http_get(&format!("{PMC_OPEN_DATA_BASE}/{stem}/{stem}.xml"))?;
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("PMC Open Data HTTP {}", response.status),
            ));
        }
        if md5_hex(&response.body) != xml_md5 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("{stem}.xml does not match the md5 its record gives"),
            ));
        }
        let article = parse_with_pmc_id(response_text(&response)?, &record.pmc_id)
            .map_err(|error| TierFault::fault("SAXParseException", error.to_string()))?;
        if !article.has_body && article.abstract_sections.is_empty() {
            return Err(TierFault::unavailable(format!(
                "{stem}.xml holds no article content"
            )));
        }
        Ok((
            render_jats_html(&article),
            article.has_body,
            response.body.clone(),
        ))
    }

    /// The URL and md5 of `pmc_id`'s PDF in PMC's AWS Open Data bucket (#748, #750).
    ///
    /// Read from the latest version's record
    /// ([`FullTextService::pmc_open_data_record`]), so a missing PDF is an
    /// answer rather than a failed download. Only the latest version is taken,
    /// the one Europe PMC's JATS is of.
    ///
    /// # Errors
    ///
    /// [`TierFault::fault`] as [`FullTextService::pmc_open_data_record`].
    /// [`TierFault::unavailable`] when the bucket holds no record of the
    /// article, or its latest version holds no PDF.
    fn fetch_pmc_open_data_pdf(&self, pmc_id: &str) -> Result<(String, String), TierFault> {
        let Some(record) = self.open_data_record(pmc_id)? else {
            return Err(TierFault::unavailable(format!(
                "{pmc_id} is not in PMC's Open Data bucket"
            )));
        };
        let stem = record.stem();
        match record.pdf_md5 {
            Some(md5) => Ok((format!("{PMC_OPEN_DATA_BASE}/{stem}/{stem}.pdf"), md5)),
            None => Err(TierFault::unavailable(format!(
                "{stem} has no PDF in PMC's Open Data"
            ))),
        }
    }

    /// One `ListObjectsV2` page of PMC's Open Data bucket, its body as text.
    ///
    /// Read lossily: only the ASCII keys are read, so a stray byte costs
    /// nothing.
    ///
    /// # Errors
    ///
    /// [`TierFault::fault`] on a response other than 200, or one that is not a
    /// listing (a proxy's page, say), which would otherwise read as naming no
    /// version.
    fn open_data_listing(&self, query: &str) -> Result<String, TierFault> {
        let response = self.http_get(&format!("{PMC_OPEN_DATA_BASE}/?list-type=2&{query}"))?;
        if response.status != 200 {
            return Err(TierFault::fault(
                "FullTextError",
                format!("PMC Open Data HTTP {}", response.status),
            ));
        }
        let listing = String::from_utf8_lossy(&response.body).into_owned();
        // Read as a listing, a proxy's page would name no version: an absence.
        if !listing.contains("<ListBucketResult") {
            return Err(TierFault::fault(
                "FullTextError",
                "PMC Open Data listing is not a ListBucketResult",
            ));
        }
        Ok(listing)
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
        jats_xml: None,
        pdf_download_failure: None,
        pdf_extraction_failure: None,
        tier_faults: Vec::new(),
        library_files: Vec::new(),
        library_failure: None,
    }
}

/// A result carrying a JATS body, and the XML it was rendered from where known.
fn fulltext_result(source: &str, html: String, jats_xml: Option<Vec<u8>>) -> FullTextResult {
    FullTextResult {
        html: Some(html),
        content_kind: ContentKind::Fulltext,
        jats_xml,
        ..empty_result(source)
    }
}

/// `result`, JATS full text, carrying a PDF's address, saved file and any failure.
///
/// JATS is what gets processed and the PDF what gets shown (#739), so the PDF's
/// address, saved file and any download failure go on the JATS result. Text
/// extracted from the PDF is dropped: the JATS body is the better text.
fn with_pdf(mut result: FullTextResult, pdf: Option<&FullTextResult>) -> FullTextResult {
    if let Some(pdf) = pdf {
        result.pdf_url.clone_from(&pdf.pdf_url);
        result.file_path.clone_from(&pdf.file_path);
        result
            .pdf_download_failure
            .clone_from(&pdf.pdf_download_failure);
    }
    result
}

/// A result carrying a body-less JATS rendering — an abstract, never full text.
fn abstract_result(source: &str, html: String, jats_xml: Option<Vec<u8>>) -> FullTextResult {
    FullTextResult {
        html: Some(html),
        content_kind: ContentKind::Abstract,
        jats_xml,
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
///
/// **Two causes, two sentences**, because they call for different actions:
/// Python's guard catches `RuntimeError` from `Path.home()` as well as the
/// directory errors, and a caller who cannot determine a home directory is not
/// helped by being told to pick a writable location they never chose.
#[must_use]
pub fn default_cache() -> Option<FullTextCache> {
    default_cache_at(FullTextCache::new(None))
}

/// The whole of [`default_cache`] bar the two lookups it starts from.
///
/// Split out so **both** arms are reachable from a test without mutating the
/// process environment: `default_cache` reads the home directory and the
/// platform default once and hands the result here, so a test hands it a
/// temporary directory or a `None`.
///
/// It is private on purpose. [`default_cache`]'s taking no parameters is what
/// makes its asymmetry structural — there is no way for a caller to route a
/// `cache_dir` of their own through the degrading path — and a helper no caller
/// can name does not open one.
fn default_cache_at(default: Option<FullTextCache>) -> Option<FullTextCache> {
    let Some(cache) = default else {
        log::warn!(
            target: "bmlib.fulltext.service",
            "Could not determine a home directory for the full-text cache (HOME is unset and this platform's own fallback found nothing); retrieval still works and full text still parses, but nothing will be cached, so every run re-fetches — and a PDF-only article comes back as a bare URL, since a PDF is downloaded into the cache and extracted only once cached. Pass cache=FullTextCache(cache_dir=...) to name a location."
        );
        return None;
    };
    for directory in [cache.cache_dir.clone(), cache.pdf_dir(), cache.html_dir()] {
        if let Err(error) = std::fs::create_dir_all(&directory) {
            log::warn!(
                target: "bmlib.fulltext.service",
                "Could not create the full-text cache directory ({:?}: {error}); retrieval still works and full text still parses, but nothing will be cached, so every run re-fetches — and a PDF-only article comes back as a bare URL, since a PDF is downloaded into the cache and extracted only once cached. Pass cache=FullTextCache(cache_dir=...) to use a writable location.",
                error.kind()
            );
            return None;
        }
    }
    Some(cache)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A `delimiter=/` listing for `PMC123.`, naming `prefixes` (#748).
    fn open_data_listing(prefixes: &[&str]) -> String {
        let common: String = prefixes
            .iter()
            .map(|prefix| format!("<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>"))
            .collect();
        format!("<ListBucketResult><Prefix>PMC123.</Prefix>{common}</ListBucketResult>")
    }

    #[test]
    fn the_latest_open_data_version_is_the_largest_by_number() {
        let listing = open_data_listing(&["PMC123.1/", "PMC123.10/", "PMC123.2/"]);
        assert_eq!(latest_open_data_version(&listing, "PMC123"), Some(10));
    }

    #[test]
    fn an_open_data_article_whose_id_extends_this_one_is_not_read() {
        let listing = open_data_listing(&["PMC1231.4/"]);
        assert_eq!(latest_open_data_version(&listing, "PMC123"), None);
    }

    #[test]
    fn an_empty_open_data_listing_names_no_version() {
        // Its own <Prefix>PMC123.</Prefix> echo is no version.
        assert_eq!(
            latest_open_data_version(&open_data_listing(&[]), "PMC123"),
            None
        );
    }

    /// The shared fixture's record, as `pmc_open_data_record` reads it (#750).
    fn open_data_record_fixture() -> Value {
        serde_json::from_str(include_str!(
            "../../../../tests/fixtures/pmc_open_data_record.json"
        ))
        .expect("the fixture is JSON")
    }

    #[test]
    fn an_open_data_record_is_read_as_python_reads_it() {
        let record = open_data_record(&open_data_record_fixture(), "PMC123", 10).expect("read");
        assert_eq!(
            record,
            PmcOpenDataRecord {
                pmc_id: "PMC123".into(),
                version: 10,
                pmid: Some("456".into()),
                doi: Some("10.1/open".into()),
                is_retracted: false,
                is_pmc_openaccess: true,
                license_code: Some("CC BY".into()),
                xml_md5: Some("c2c199fb4ed79ff17438764763676f71".into()),
                pdf_md5: Some("abe57a6643460397192ff69ffce75752".into()),
            }
        );
    }

    #[test]
    fn an_open_data_records_absent_files_and_null_fields_are_none() {
        let mut data = open_data_record_fixture();
        for name in ["pmid", "doi", "license_code", "pdf_url", "xml_url"] {
            data[name] = Value::Null;
        }
        data["is_retracted"] = Value::Bool(true);
        let record = open_data_record(&data, "PMC123", 10).expect("read");
        assert_eq!(
            (record.pmid, record.doi, record.license_code),
            (None, None, None)
        );
        assert_eq!((record.pdf_md5, record.xml_md5), (None, None));
        assert!(record.is_retracted);
    }

    /// Python's `test_a_record_not_of_the_shape_read_is_refused`, case for case,
    /// each refused for the reason Python gives.
    #[test]
    fn an_open_data_record_not_of_the_shape_read_is_refused() {
        let md5 = |n: usize, c: char| c.to_string().repeat(n);
        let url = "s3://pmc-oa-opendata/PMC123.10/PMC123.10.pdf?md5=";
        let another = " names another article or version";
        let past_u64: Value = serde_json::from_str("18446744073709551616").expect("JSON");
        let mut cases: Vec<(&str, Option<Value>, String)> = vec![
            ("pmcid", Some(json!("PMC124")), another.into()),
            ("version", Some(json!(9)), another.into()),
            ("version", Some(json!(11)), another.into()),
            ("version", Some(json!(-1)), another.into()),
            ("version", Some(json!(true)), ": unexpected version".into()),
            (
                "is_retracted",
                Some(Value::Null),
                ": unexpected is_retracted".into(),
            ),
            (
                "is_retracted",
                Some(json!("false")),
                ": unexpected is_retracted".into(),
            ),
            (
                "is_pmc_openaccess",
                Some(json!(1)),
                ": unexpected is_pmc_openaccess".into(),
            ),
            ("pmid", Some(json!("456")), ": unexpected pmid".into()),
            ("pmid", Some(json!(true)), ": unexpected pmid".into()),
            ("pmid", Some(json!(0)), ": unexpected pmid".into()),
            ("pmid", Some(json!(-5)), ": unexpected pmid".into()),
            ("pmid", Some(past_u64), ": unexpected pmid".into()),
            ("doi", Some(json!(10)), ": unexpected doi".into()),
            (
                "license_code",
                Some(json!(["CC BY"])),
                ": unexpected license_code".into(),
            ),
            (
                "pdf_url",
                Some(json!(format!(
                    "s3://pmc-oa-opendata/PMC123.9/PMC123.9.pdf?md5={}",
                    md5(32, 'a')
                ))),
                ": unexpected pdf_url".into(),
            ),
            (
                "pdf_url",
                Some(json!(format!(
                    "s3://pmc-oa-opendata/PMC123.10/other.pdf?md5={}",
                    md5(32, 'a')
                ))),
                ": unexpected pdf_url".into(),
            ),
            // An md5 alone names no file.
            (
                "pdf_url",
                Some(json!(md5(32, 'a'))),
                ": unexpected pdf_url".into(),
            ),
            (
                "pdf_url",
                Some(json!(format!("{url}{}", md5(32, 'A')))),
                ": unexpected pdf_url".into(),
            ),
            (
                "pdf_url",
                Some(json!(format!("{url}{}", md5(31, 'a')))),
                ": unexpected pdf_url".into(),
            ),
            (
                "pdf_url",
                Some(json!(format!("{url}{}", md5(33, 'a')))),
                ": unexpected pdf_url".into(),
            ),
            (
                "pdf_url",
                Some(json!(format!("{url}{}&x=1", md5(32, 'a')))),
                ": unexpected pdf_url".into(),
            ),
            (
                "pdf_url",
                Some(json!(url.strip_suffix("?md5=").expect("a query"))),
                ": unexpected pdf_url".into(),
            ),
            (
                "xml_url",
                Some(json!(format!("{url}{}", md5(32, 'a')))),
                ": unexpected xml_url".into(),
            ),
            ("xml_url", Some(json!(5)), ": unexpected xml_url".into()),
        ];
        for name in [
            "pmcid",
            "version",
            "pmid",
            "doi",
            "is_retracted",
            "is_pmc_openaccess",
            "license_code",
            "xml_url",
            "pdf_url",
        ] {
            cases.push((name, None, format!(": no {name}")));
        }
        for (name, value, reason) in cases {
            let mut data = open_data_record_fixture();
            match &value {
                Some(value) => data[name] = value.clone(),
                None => {
                    data.as_object_mut().expect("an object").remove(name);
                }
            }
            let refused = open_data_record(&data, "PMC123", 10).expect_err("refused");
            assert_eq!(refused.name, "FullTextError", "{name}: {value:?}");
            assert!(
                refused
                    .message
                    .starts_with(&format!("PMC Open Data record PMC123.10{reason}")),
                "{name}: {value:?}: {}",
                refused.message
            );
        }
        let refused = open_data_record(&json!([]), "PMC123", 10).expect_err("refused");
        assert!(
            refused.message.ends_with("is not an object"),
            "{}",
            refused.message
        );
    }

    #[test]
    fn md5_hex_is_lowercase_hex() {
        assert_eq!(md5_hex(b""), "d41d8cd98f00b204e9800998ecf8427e");
    }

    /// **Every arm of [`fetch_error_name`] is Python's exception name.**
    ///
    /// The arms are not reachable through HTTP from this module — its client has
    /// one method and the JSON is read here — so no behaviour test can pin them,
    /// and three of them carried invented classes (`MalformedError`,
    /// `ConfigError`, `ResumeUnreadableError`) that no Python `type(exc).__name__`
    /// produces. This states the table against the names the fetcher layer
    /// measures: `TransportError` and `HTTPStatusError` for the two the corpus
    /// pins, and `ValueError` for the three #349's split shares.
    #[test]
    fn every_fetch_error_name_is_pythons() {
        let cases: [(FetchError, &str); 5] = [
            (
                FetchError::Transport("refused".to_string()),
                "TransportError",
            ),
            (
                FetchError::HttpStatus {
                    url: "https://example.org".to_string(),
                    status: 500,
                },
                "HTTPStatusError",
            ),
            (FetchError::Malformed("not JSON".to_string()), "ValueError"),
            (FetchError::Config("no key".to_string()), "ValueError"),
            (
                FetchError::ResumeUnreadable("not a checkpoint".to_string()),
                "ValueError",
            ),
        ];
        for (error, name) in cases {
            assert_eq!(
                fetch_error_name(&error),
                name,
                "{error:?} must be reported under Python's name"
            );
        }
    }

    /// A directory under the platform temp dir, unique to one test.
    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("bmlib-default-cache-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    /// The happy path: all three directories exist afterwards, and they are the
    /// three the cache reads.
    #[test]
    fn a_writable_default_creates_all_three_directories() {
        let root = scratch("writable");
        let cache = default_cache_at(Some(FullTextCache {
            cache_dir: root.clone(),
        }))
        .expect("a writable location");
        assert_eq!(cache.cache_dir, root);
        for directory in [&root, &root.join("pdfs"), &root.join("html")] {
            assert!(
                directory.is_dir(),
                "{} was not created",
                directory.display()
            );
        }
        let _ = std::fs::remove_dir_all(&root);
    }

    /// The `OSError` arm: a file standing where a directory should be. Nothing is
    /// written beside it — this is `docs/DECISIONS.md`'s *"no writability probe"*,
    /// which would litter the operator's directory with a file that is not an
    /// article.
    #[test]
    fn a_blocked_directory_degrades_to_no_cache_and_writes_nothing() {
        let root = scratch("blocked");
        std::fs::create_dir_all(&root).expect("the parent");
        let blocker = root.join("blocker");
        std::fs::write(&blocker, b"in the way").expect("the blocking file");

        let outcome = default_cache_at(Some(FullTextCache {
            cache_dir: blocker.clone(),
        }));

        assert!(outcome.is_none(), "a file in the way is not a cache");
        assert_eq!(std::fs::read(&blocker).expect("still there"), b"in the way");
        assert_eq!(std::fs::read_dir(&root).expect("readable").count(), 1);
        let _ = std::fs::remove_dir_all(&root);
    }

    /// The `RuntimeError` arm, which is the one the port used to answer by
    /// caching into the current working directory: Python's `Path.home()`
    /// raises, `FullTextService` catches it, and no cache is made.
    #[test]
    fn no_home_directory_degrades_to_no_cache() {
        assert!(default_cache_at(None).is_none());
    }
}
