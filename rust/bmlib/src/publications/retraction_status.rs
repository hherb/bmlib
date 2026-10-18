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

//! A paper's retraction status from three sources, under one rule (#504).
//!
//! A port of `bmlib/publications/retraction_status.py`, held to it by the
//! shared contract `tests/fixtures/retraction_status.json`, which
//! bmlibrarian_lite's three platforms are to assert against too. Evidence comes from
//! the Retraction Watch export (here through [`RetractionIndex`], which a phone
//! app keeps as one file), Crossref's `updated-by` relations
//! ([`crossref_retraction_requests`], [`parse_crossref_retraction_response`];
//! the caller owns the transport), and PubMed's `Retracted Publication` label.
//! [`retraction_status`] decides. **"Could not check" is never "clear".**
//!
//! [`RetractionIndex`] exists only here: the desktop keeps the export in
//! Python's `retraction_notices` table, while the apps build no SQLite.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::fulltext::service::quote;
use crate::publications::models::{RetractionNature, RetractionNotice};
use crate::publications::retractions::{
    canonical_pmid, clean_identifier, parse_retraction_watch_csv, unusable_pmid,
};
use crate::publications::storage::normalize_doi;

/// Where a piece of evidence came from. Crossref's relations carry their own
/// `source`, and only `publisher` relations, which publishers deposit both
/// ways round, are ever discarded by the notice guard.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetractionSource {
    /// The Retraction Watch export.
    RetractionWatchExport,
    /// A Crossref relation whose `source` is `retraction-watch`.
    CrossrefRetractionWatch,
    /// A Crossref relation whose `source` is `publisher`.
    CrossrefPublisher,
    /// PubMed's `Retracted Publication` publication type.
    PubmedLabel,
}

impl RetractionSource {
    /// Which copy of one retraction is shown on equal dates: Retraction Watch's
    /// record is curated.
    fn preference(self) -> u8 {
        match self {
            Self::RetractionWatchExport => 3,
            Self::CrossrefRetractionWatch => 2,
            Self::CrossrefPublisher => 1,
            Self::PubmedLabel => 0,
        }
    }
}

/// What a notice says about the paper: Crossref's `type` vocabulary (the twelve
/// its deposit schema `common5.3.1.xsd` enumerates, and Retraction Watch's
/// `reinstatement`), which the export's natures map onto. Anything else is
/// [`Self::Other`]: a notice bmlib cannot read, so the lookup that returned it
/// has not answered.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetractionKind {
    /// Retracted.
    Retraction,
    /// Withdrawn (retracts).
    Withdrawal,
    /// Removed (retracts).
    Removal,
    /// Partly retracted (a concern).
    PartialRetraction,
    /// An expression of concern.
    ExpressionOfConcern,
    /// A correction (evidence of nothing).
    Correction,
    /// An erratum (evidence of nothing).
    Erratum,
    /// A corrigendum (evidence of nothing).
    Corrigendum,
    /// An addendum (evidence of nothing).
    Addendum,
    /// A clarification (evidence of nothing).
    Clarification,
    /// A new version (evidence of nothing).
    NewVersion,
    /// A new edition (evidence of nothing).
    NewEdition,
    /// Reinstated: undoes an earlier retraction, lifts an earlier concern.
    Reinstatement,
    /// A value outside the vocabulary.
    Other,
}

/// What a kind does to the verdict. [`RetractionKind::role`] is one exhaustive
/// match, so a kind added later has to choose.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KindRole {
    /// Retracts the paper.
    Retracts,
    /// Raises a concern.
    Concerns,
    /// Undoes a retraction or lifts a concern.
    Reinstates,
    /// Read, and evidence of nothing.
    DecidesNothing,
    /// Not read: the lookup that returned it has not answered.
    Unread,
}

impl RetractionKind {
    /// Read a Crossref `type`, case-insensitively on a trimmed value.
    #[must_use]
    pub fn from_raw(raw: &str) -> Self {
        match raw.trim().to_lowercase().as_str() {
            "retraction" => Self::Retraction,
            "withdrawal" => Self::Withdrawal,
            "removal" => Self::Removal,
            "partial_retraction" => Self::PartialRetraction,
            "expression_of_concern" => Self::ExpressionOfConcern,
            "correction" => Self::Correction,
            "erratum" => Self::Erratum,
            "corrigendum" => Self::Corrigendum,
            "addendum" => Self::Addendum,
            "clarification" => Self::Clarification,
            "new_version" => Self::NewVersion,
            "new_edition" => Self::NewEdition,
            "reinstatement" => Self::Reinstatement,
            _ => Self::Other,
        }
    }

    /// What this kind does to the verdict.
    #[must_use]
    pub fn role(self) -> KindRole {
        match self {
            Self::Retraction | Self::Withdrawal | Self::Removal => KindRole::Retracts,
            Self::ExpressionOfConcern | Self::PartialRetraction => KindRole::Concerns,
            Self::Reinstatement => KindRole::Reinstates,
            Self::Correction
            | Self::Erratum
            | Self::Corrigendum
            | Self::Addendum
            | Self::Clarification
            | Self::NewVersion
            | Self::NewEdition => KindRole::DecidesNothing,
            Self::Other => KindRole::Unread,
        }
    }

    /// A retraction, a withdrawal or a removal.
    #[must_use]
    pub fn retracts(self) -> bool {
        self.role() == KindRole::Retracts
    }

    /// An expression of concern or a partial retraction.
    #[must_use]
    pub fn concerns(self) -> bool {
        self.role() == KindRole::Concerns
    }

    fn from_nature(nature: RetractionNature) -> Self {
        match nature {
            RetractionNature::Retraction => Self::Retraction,
            RetractionNature::Correction => Self::Correction,
            RetractionNature::ExpressionOfConcern => Self::ExpressionOfConcern,
            RetractionNature::Reinstatement => Self::Reinstatement,
            RetractionNature::Other => Self::Other,
        }
    }
}

/// The verdict. [`Self::Unchecked`] is never a softer [`Self::Clear`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetractionStatus {
    /// Retracted, and not reinstated since.
    Retracted,
    /// An expression of concern or a partial retraction stands.
    Concern,
    /// Every lookup answered and no retraction or concern stands.
    Clear,
    /// No retraction or concern stands, but a lookup did not answer.
    Unchecked,
}

/// A lookup that has to answer before a paper can be called clear. The PubMed
/// label is not one: a record restored without its publication types is still
/// checkable against these two.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetractionLookup {
    /// The Retraction Watch export lookup.
    RetractionWatchExport,
    /// Crossref's answer for this DOI.
    Crossref,
}

/// One source's statement about one paper, in a source-neutral form.
///
/// `date` is ISO — `yyyy-mm-dd`, or `yyyy-mm` / `yyyy` where Crossref deposited
/// only that much; `""` is read as undated. `notice_doi` is normalised (doi.org
/// prefix stripped, lower-cased). `raw_kind` is the source's own spelling of
/// the kind.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RetractionEvidence {
    /// Where it came from.
    pub source: RetractionSource,
    /// What it says.
    pub kind: RetractionKind,
    /// When, if known.
    pub date: Option<String>,
    /// The notice's DOI.
    pub notice_doi: Option<String>,
    /// Retraction Watch's record ID.
    pub record_id: Option<String>,
    /// The source's own spelling of the kind.
    pub raw_kind: Option<String>,
}

/// What [`retraction_status`] decided, and on what.
///
/// `deciding` is the newest retraction for `Retracted`, the newest concern for
/// `Concern`, and otherwise the newest reinstatement, if any. `unanswered`
/// lists every lookup that did not answer, in declaration order, beside any
/// status. Serialised only: it is never read back, so no contradictory verdict
/// can be deserialised.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RetractionVerdict {
    /// The status.
    pub status: RetractionStatus,
    /// The evidence it rests on.
    pub deciding: Option<RetractionEvidence>,
    /// The lookups that did not answer.
    pub unanswered: Vec<RetractionLookup>,
}

/// Crossref's answer about one DOI.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum CrossrefResult {
    /// Crossref served the record.
    Served {
        /// The record's own DOI, normalised. [`retraction_status`] counts the
        /// result as an answer only for the paper with this DOI (#508).
        doi: String,
        /// Its `updated-by` relations, in document order.
        evidence: Vec<RetractionEvidence>,
        /// The DOIs its own retraction-family `update-to` names.
        retraction_targets: Vec<String>,
    },
    /// Absent from a successful answer: Crossref does not know this DOI.
    NotKnown,
    /// The batch failed, or this DOI's relations could not be read. Never read
    /// as "not known". The wording of `reason` is not part of the contract.
    Unreachable {
        /// Why.
        reason: String,
    },
}

/// One request URL and the normalised DOIs it asks about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CrossrefBatch {
    /// The URL to GET.
    pub url: String,
    /// The DOIs to hand back to [`parse_crossref_retraction_response`].
    pub dois: Vec<String>,
}

/// The batches to send, and the DOIs no batch can carry (a DOI containing a
/// comma cannot sit in a `filter=doi:a,doi:b` list; Crossref has then not
/// answered for it, so the paper cannot be clear).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CrossrefRequests {
    /// The requests.
    pub batches: Vec<CrossrefBatch>,
    /// DOIs never asked.
    pub unsendable: Vec<String>,
}

/// Crossref's works endpoint.
pub const CROSSREF_WORKS_URL: &str = "https://api.crossref.org/works";
/// DOIs per request: batches of up to 200 were all served on 2026-10-18; 50 is
/// lite's planned rate (one request per 50 papers).
pub const CROSSREF_BATCH_MAX_DOIS: usize = 50;
/// A URL is cut before it passes this length (a 6,002-character URL was
/// served on 2026-10-18).
pub const CROSSREF_URL_MAX_CHARS: usize = 4000;
/// PubMed's publication type, by name and by MeSH UI, case-folded.
pub const PUBMED_RETRACTED_LABELS: [&str; 2] = ["retracted publication", "d016441"];

// ---------------------------------------------------------------------------
// The export
// ---------------------------------------------------------------------------

/// Turn Retraction Watch notices (one paper's) into evidence.
#[must_use]
pub fn evidence_from_notices(notices: &[RetractionNotice]) -> Vec<RetractionEvidence> {
    notices
        .iter()
        .map(|notice| RetractionEvidence {
            source: RetractionSource::RetractionWatchExport,
            kind: RetractionKind::from_nature(notice.nature),
            date: notice.retraction_date.clone(),
            notice_doi: normalize_doi(notice.notice_doi.as_deref()),
            record_id: Some(notice.record_id.clone()),
            raw_kind: notice.raw_nature.clone(),
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Crossref
// ---------------------------------------------------------------------------

fn crossref_url(dois: &[String], mailto: Option<&str>) -> String {
    let filters: Vec<String> = dois
        .iter()
        .map(|doi| format!("doi:{}", quote(doi, "/()")))
        .collect();
    let mut url = format!(
        "{CROSSREF_WORKS_URL}?filter={}&select=DOI,update-to,updated-by&rows={}",
        filters.join(","),
        dois.len()
    );
    if let Some(mailto) = mailto.filter(|m| !m.is_empty()) {
        url.push_str("&mailto=");
        url.push_str(&quote(mailto, "@"));
    }
    url
}

/// Build the Crossref requests that ask about `dois`.
///
/// DOIs are normalised and de-duplicated, then packed in order into batches of
/// at most [`CROSSREF_BATCH_MAX_DOIS`] whose URL stays within
/// [`CROSSREF_URL_MAX_CHARS`] (a single longer DOI still gets its own batch).
#[must_use]
pub fn crossref_retraction_requests<S: AsRef<str>>(
    dois: &[S],
    mailto: Option<&str>,
) -> CrossrefRequests {
    let mut requests = CrossrefRequests::default();
    let mut current: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for raw in dois {
        let Some(doi) = normalize_doi(Some(raw.as_ref())) else {
            continue;
        };
        if !seen.insert(doi.clone()) {
            continue;
        }
        if doi.contains(',') {
            requests.unsendable.push(doi);
            continue;
        }
        let mut candidate = current.clone();
        candidate.push(doi.clone());
        if !current.is_empty()
            && (candidate.len() > CROSSREF_BATCH_MAX_DOIS
                || crossref_url(&candidate, mailto).len() > CROSSREF_URL_MAX_CHARS)
        {
            requests.batches.push(CrossrefBatch {
                url: crossref_url(&current, mailto),
                dois: std::mem::take(&mut current),
            });
            candidate = vec![doi];
        }
        current = candidate;
    }
    if !current.is_empty() {
        requests.batches.push(CrossrefBatch {
            url: crossref_url(&current, mailto),
            dois: current,
        });
    }
    requests
}

/// An ISO date from `updated.date-parts`, as precise as deposited.
fn crossref_date(entry: &serde_json::Map<String, serde_json::Value>) -> Option<String> {
    let first = entry
        .get("updated")?
        .as_object()?
        .get("date-parts")?
        .as_array()?
        .first()?
        .as_array()?;
    if first.is_empty() {
        return None;
    }
    let values: Vec<i64> = first
        .iter()
        .take(3)
        .map(serde_json::Value::as_i64)
        .collect::<Option<_>>()?;
    let year = values[0];
    if !(1000..=9999).contains(&year) {
        return None;
    }
    let Some(&month) = values.get(1) else {
        return Some(format!("{year:04}"));
    };
    if !(1..=12).contains(&month) {
        return None;
    }
    let Some(&day) = values.get(2) else {
        return Some(format!("{year:04}-{month:02}"));
    };
    let (year, month) = (u32::try_from(year).ok()?, u32::try_from(month).ok()?);
    crate::publications::retractions::iso(year, month, u32::try_from(day).ok()?)
}

type JsonObject = serde_json::Map<String, serde_json::Value>;

fn relations<'a>(item: &'a JsonObject, key: &str) -> Result<Vec<&'a JsonObject>, String> {
    let Some(value) = item.get(key) else {
        return Ok(Vec::new());
    };
    let unreadable = || format!("{key} is not a list of typed relations");
    value
        .as_array()
        .ok_or_else(unreadable)?
        .iter()
        .map(|entry| {
            entry
                .as_object()
                .filter(|e| e.get("type").is_some_and(serde_json::Value::is_string))
                .ok_or_else(unreadable)
        })
        .collect()
}

fn optional_text(entry: &JsonObject, key: &str) -> Result<Option<String>, String> {
    match entry.get(key) {
        None | Some(serde_json::Value::Null) => Ok(None),
        Some(serde_json::Value::String(text)) => Ok(Some(text.clone())),
        Some(serde_json::Value::Number(n)) if n.is_i64() || n.is_u64() => Ok(Some(n.to_string())),
        Some(_) => Err(format!("a relation's {key} is not text")),
    }
}

fn kind(raw: &str, unknown_kinds: &mut HashSet<String>) -> RetractionKind {
    let kind = RetractionKind::from_raw(raw);
    let key = raw.trim().to_lowercase();
    if kind == RetractionKind::Other && key != "other" && unknown_kinds.insert(key) {
        eprintln!(
            "bmlib.publications.retraction_status WARNING: Unrecognised Crossref relation type \
             {raw:?}; kept as \"other\" with the original in raw_kind. Crossref's answer for a \
             paper whose updated-by carries it counts as not given, so the paper reads \
             unchecked, not clear."
        );
    }
    kind
}

fn read_item(doi: &str, item: &JsonObject, unknown_kinds: &mut HashSet<String>) -> CrossrefResult {
    read_relations(doi, item, unknown_kinds)
        .unwrap_or_else(|reason| CrossrefResult::Unreachable { reason })
}

fn read_relations(
    doi: &str,
    item: &JsonObject,
    unknown_kinds: &mut HashSet<String>,
) -> Result<CrossrefResult, String> {
    let mut evidence = Vec::new();
    for entry in relations(item, "updated-by")? {
        let raw_type = entry["type"].as_str().unwrap_or_default();
        // Not guessed: the source decides whether the notice guard in
        // `retraction_status` may discard the relation.
        let source = match entry.get("source").and_then(serde_json::Value::as_str) {
            Some("retraction-watch") => RetractionSource::CrossrefRetractionWatch,
            Some("publisher") => RetractionSource::CrossrefPublisher,
            other => return Err(format!("unknown relation source {other:?}")),
        };
        evidence.push(RetractionEvidence {
            source,
            kind: kind(raw_type, unknown_kinds),
            date: crossref_date(entry),
            notice_doi: normalize_doi(optional_text(entry, "DOI")?.as_deref()),
            record_id: optional_text(entry, "record-id")?,
            raw_kind: Some(raw_type.to_string()),
        });
    }
    let mut targets: Vec<String> = Vec::new();
    for entry in relations(item, "update-to")? {
        let raw_type = entry["type"].as_str().unwrap_or_default();
        if let Some(target) = normalize_doi(optional_text(entry, "DOI")?.as_deref()) {
            if kind(raw_type, unknown_kinds).retracts() && !targets.contains(&target) {
                targets.push(target);
            }
        }
    }
    Ok(CrossrefResult::Served {
        doi: doi.to_string(),
        evidence,
        retraction_targets: targets,
    })
}

/// Read one batch's answer into a result per requested DOI, keyed by the
/// normalised DOI.
///
/// **A failed batch makes every DOI in it unreachable**, never "not known": a
/// non-200 (429 and 5xx included — the caller's pacing decides the retry), a
/// body that is not UTF-8 JSON, one with no list of `items`, an item with no
/// `DOI` (which leaves absence meaning nothing), or two items for one DOI
/// (which leaves the answer ambiguous). A DOI whose own relations cannot be
/// read is unreachable alone. A DOI absent from a well-formed answer is
/// [`CrossrefResult::NotKnown`].
#[must_use]
pub fn parse_crossref_retraction_response<S: AsRef<str>>(
    status: u16,
    body: &[u8],
    requested_dois: &[S],
) -> BTreeMap<String, CrossrefResult> {
    let requested: Vec<String> = requested_dois
        .iter()
        .filter_map(|doi| normalize_doi(Some(doi.as_ref())))
        .collect();
    let failed = |reason: String| -> BTreeMap<String, CrossrefResult> {
        requested
            .iter()
            .map(|doi| {
                let reason = reason.clone();
                (doi.clone(), CrossrefResult::Unreachable { reason })
            })
            .collect()
    };
    if status != 200 {
        return failed(format!("HTTP {status}"));
    }
    let data: serde_json::Value = match serde_json::from_slice(body) {
        Ok(data) => data,
        Err(error) => return failed(format!("body is not JSON: {error}")),
    };
    let Some(items) = data
        .get("message")
        .and_then(|message| message.get("items"))
        .and_then(serde_json::Value::as_array)
    else {
        return failed("body carries no list of items".to_string());
    };
    let mut by_doi: HashMap<String, &JsonObject> = HashMap::new();
    for item in items {
        let object = item.as_object();
        let doi = object
            .and_then(|o| o.get("DOI"))
            .and_then(serde_json::Value::as_str)
            .and_then(|doi| normalize_doi(Some(doi)));
        match (object, doi) {
            (Some(object), Some(doi)) => {
                if by_doi.contains_key(&doi) {
                    return failed(format!("two items for {doi}"));
                }
                by_doi.insert(doi, object);
            }
            _ => return failed("an item carries no DOI".to_string()),
        }
    }
    let mut unknown_kinds = HashSet::new();
    requested
        .iter()
        .map(|doi| {
            let result = match by_doi.get(doi) {
                Some(item) => read_item(doi, item, &mut unknown_kinds),
                None => CrossrefResult::NotKnown,
            };
            (doi.clone(), result)
        })
        .collect()
}

// ---------------------------------------------------------------------------
// The rule
// ---------------------------------------------------------------------------

fn label_evidence(publication_types: &[String]) -> Option<RetractionEvidence> {
    publication_types
        .iter()
        .find(|raw| PUBMED_RETRACTED_LABELS.contains(&raw.trim().to_lowercase().as_str()))
        .map(|raw| RetractionEvidence {
            source: RetractionSource::PubmedLabel,
            kind: RetractionKind::Retraction,
            date: None,
            notice_doi: None,
            record_id: None,
            raw_kind: Some(raw.clone()),
        })
}

/// Retraction Watch's curated copy first; past that the notice itself, so the
/// deciding entry does not depend on the order the evidence arrived in.
fn tie_break(e: &RetractionEvidence) -> (u8, &str, &str) {
    (
        e.source.preference(),
        e.notice_doi.as_deref().unwrap_or(""),
        e.record_id.as_deref().unwrap_or(""),
    )
}

/// The date as compared: ISO text, undated (`None` or `""`) being `""`.
fn date(e: &RetractionEvidence) -> &str {
    e.date.as_deref().unwrap_or("")
}

fn concern_key(e: &RetractionEvidence) -> (&str, (u8, &str, &str)) {
    (date(e), tie_break(e))
}

/// Newest first; undated is `""` and so oldest. On equal dates the label beats
/// a reinstatement (only a *later* one undoes it) and a reinstatement beats a
/// retraction (it can only follow it).
fn decisive_key(e: &RetractionEvidence) -> (&str, u8, (u8, &str, &str)) {
    let rank = if e.source == RetractionSource::PubmedLabel {
        2
    } else if e.kind == RetractionKind::Reinstatement {
        1
    } else {
        0
    };
    (date(e), rank, tie_break(e))
}

fn unread(evidence: &[RetractionEvidence]) -> bool {
    evidence.iter().any(|e| e.kind.role() == KindRole::Unread)
}

/// Decide one paper's retraction status from everything known about it.
///
/// * `doi` — without one Crossref cannot have answered, so the paper is never
///   clear.
/// * `export` — the paper's Retraction Watch evidence, `Some(&[])` when the
///   export was consulted and holds nothing, `None` when it was not consulted
///   ([`RetractionIndex::lookup`] returns exactly this).
/// * `crossref` — this DOI's [`parse_crossref_retraction_response`] result, or
///   `None` when Crossref was not asked.
/// * `publication_types` — the record's PubMed / Europe PMC types, if held.
///
/// The rules are `retraction_status.py`'s, each a case of the shared fixture:
/// newest first over retractions, withdrawals, removals, reinstatements and the
/// label, the first decides (dates compare as ISO text, so `"2021"` sorts
/// before `"2021-01-05"`); a concern stands until a *dated* reinstatement on or
/// after its date; clear needs the export consulted and Crossref to have served
/// this DOI (a DOI it does not know is unchecked), and a lookup that returned a
/// notice of a kind bmlib cannot read ([`KindRole::Unread`]) has not answered;
/// and a `publisher` relation naming a DOI the paper's own retraction
/// `update-to` names — not the paper itself — is ignored, since publishers
/// deposit those both ways round. Retraction Watch's relations are never
/// ignored: the Surgisphere Lancet paper lists its own notices in its
/// `update-to`.
#[must_use]
pub fn retraction_status(
    doi: Option<&str>,
    export: Option<&[RetractionEvidence]>,
    crossref: Option<&CrossrefResult>,
    publication_types: &[String],
) -> RetractionVerdict {
    let own = normalize_doi(doi);
    // A result served for another DOI is no answer about this paper (#508).
    let served = match crossref {
        Some(CrossrefResult::Served { doi, .. }) => {
            own.is_some() && normalize_doi(Some(doi)) == own
        }
        _ => false,
    };
    // Both sides normalised here, not trusted from the caller: an app may
    // rebuild a result from its own storage.
    let (served_evidence, targets): (&[RetractionEvidence], HashSet<String>) = match crossref {
        Some(CrossrefResult::Served {
            evidence,
            retraction_targets,
            ..
        }) if served => (
            evidence,
            retraction_targets
                .iter()
                .filter_map(|target| normalize_doi(Some(target)))
                .filter(|target| Some(target) != own.as_ref())
                .collect(),
        ),
        _ => (&[], HashSet::new()),
    };

    let mut evidence: Vec<&RetractionEvidence> = export
        .unwrap_or_default()
        .iter()
        .chain(served_evidence)
        .filter(|e| {
            !(e.source == RetractionSource::CrossrefPublisher
                && e.kind.retracts()
                && normalize_doi(e.notice_doi.as_deref()).is_some_and(|d| targets.contains(&d)))
        })
        .collect();
    let label = label_evidence(publication_types);
    evidence.extend(label.as_ref());

    let mut unanswered = Vec::new();
    if export.is_none_or(unread) {
        unanswered.push(RetractionLookup::RetractionWatchExport);
    }
    if !served || unread(served_evidence) {
        unanswered.push(RetractionLookup::Crossref);
    }

    let mut decisive: Vec<&RetractionEvidence> = evidence
        .iter()
        .copied()
        .filter(|e| e.kind.retracts() || e.kind == RetractionKind::Reinstatement)
        .collect();
    decisive.sort_by(|a, b| decisive_key(b).cmp(&decisive_key(a)));
    let first = decisive.first().copied();
    if let Some(first) = first.filter(|e| e.kind != RetractionKind::Reinstatement) {
        return RetractionVerdict {
            status: RetractionStatus::Retracted,
            deciding: Some(first.clone()),
            unanswered,
        };
    }

    let reinstated = evidence
        .iter()
        .filter(|e| e.kind == RetractionKind::Reinstatement)
        .map(|e| date(e))
        .filter(|d| !d.is_empty())
        .max();
    let mut concerns: Vec<&RetractionEvidence> = evidence
        .iter()
        .copied()
        .filter(|e| e.kind.concerns() && !reinstated.is_some_and(|r| date(e) <= r))
        .collect();
    concerns.sort_by(|a, b| concern_key(b).cmp(&concern_key(a)));
    if let Some(concern) = concerns.first() {
        return RetractionVerdict {
            status: RetractionStatus::Concern,
            deciding: Some((*concern).clone()),
            unanswered,
        };
    }
    RetractionVerdict {
        status: if unanswered.is_empty() {
            RetractionStatus::Clear
        } else {
            RetractionStatus::Unchecked
        },
        deciding: first.cloned(),
        unanswered,
    }
}

// ---------------------------------------------------------------------------
// The export, on a phone
// ---------------------------------------------------------------------------

/// The `format` tag of a serialised [`RetractionIndex`].
pub const RETRACTION_INDEX_FORMAT: &str = "bmlib-retraction-watch-index";
/// The version of the serialised form this build reads and writes. Bump it
/// whenever an entry's shape changes; an index of another version is refused,
/// which a caller reads as "the export was not consulted" and rebuilds.
pub const RETRACTION_INDEX_VERSION: u32 = 1;

/// One notice: `(doi, pmid, kind, raw_kind, date, notice_doi, record_id)`.
/// Positional to keep the file small; reordering it is a format change, so
/// bump [`RETRACTION_INDEX_VERSION`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct IndexEntry(
    Option<String>,
    Option<String>,
    RetractionKind,
    Option<String>,
    Option<String>,
    Option<String>,
    String,
);

#[derive(Serialize, Deserialize)]
struct IndexFile {
    format: String,
    version: u32,
    data_as_of: String,
    entries: Vec<IndexEntry>,
}

/// The Retraction Watch export as a lookup a phone app keeps in one file.
///
/// Built from the CSV by the oracle-checked [`parse_retraction_watch_csv`],
/// serialised with [`Self::to_bytes`], reopened with [`Self::from_bytes`]. A
/// missing or unreadable index is "the export was not consulted", never "no
/// notices".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetractionIndex {
    data_as_of: String,
    entries: Vec<IndexEntry>,
    by_doi: HashMap<String, Vec<usize>>,
    by_pmid: HashMap<String, Vec<usize>>,
}

/// What [`RetractionIndex::build`] produced and what it left out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetractionIndexBuild {
    /// The index.
    pub index: RetractionIndex,
    /// Rows skipped: malformed, no record ID, or no usable identifier for
    /// the paper (a PMID that is not ASCII digits, with no DOI, included).
    pub skipped_rows: usize,
    /// Distinct nature strings this version could not map (indexed as
    /// `other`, which decides nothing). Non-empty means Retraction Watch's
    /// vocabulary moved, and must be reported.
    pub unknown_natures: Vec<String>,
}

fn lookup_doi(doi: &str) -> Option<String> {
    clean_identifier(normalize_doi(Some(doi)).as_deref())
}

impl RetractionIndex {
    /// Build an index from the export's CSV bytes (UTF-8, a BOM allowed).
    ///
    /// `data_as_of` is the caller's date for the copy (the day it was
    /// downloaded), so a verdict can say "Retraction Watch data as of …".
    ///
    /// # Errors
    ///
    /// The bytes are not UTF-8 (a corrupt download — Python falls back to
    /// cp1252 there, this refuses), the CSV is malformed, `data_as_of` is
    /// blank, or no row was usable: an empty index would answer "no notices"
    /// for every paper. Also when no notice carries a paper DOI, or none a
    /// PMID: a renamed column, which would answer "no notices" for every
    /// lookup by the other identifier (63,373 and 32,208 of the 2026-03
    /// copy's 69,318 rows carry one). A truncated download that ends on a row
    /// boundary cannot be told from a smaller export: compare
    /// [`RetractionIndex::len`] with the index it replaces.
    pub fn build(csv: &[u8], data_as_of: &str) -> Result<RetractionIndexBuild, String> {
        let csv = csv.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(csv);
        std::str::from_utf8(csv).map_err(|e| format!("the export is not UTF-8: {e}"))?;
        let outcome = parse_retraction_watch_csv(csv).map_err(|e| e.to_string())?;
        // A row whose only identifier is not a PMID (non-digits) passed the
        // parser but can never be looked up: skipped and counted, so an export
        // of nothing else is refused as empty rather than answering "no
        // notices" for every paper.
        let mut unindexable = 0usize;
        let entries = outcome
            .notices
            .iter()
            .filter_map(|notice| {
                let doi = notice.doi.as_deref().and_then(lookup_doi);
                let pmid = notice.pmid.as_deref().and_then(canonical_pmid);
                if doi.is_none() && pmid.is_none() {
                    unindexable += 1;
                    return None;
                }
                Some(IndexEntry(
                    doi,
                    pmid,
                    RetractionKind::from_nature(notice.nature),
                    notice.raw_nature.clone(),
                    notice.retraction_date.clone(),
                    normalize_doi(notice.notice_doi.as_deref()),
                    notice.record_id.clone(),
                ))
            })
            .collect::<Vec<_>>();
        if !entries.is_empty() {
            for (column, carried) in [
                ("paper DOI", entries.iter().any(|e| e.0.is_some())),
                ("PMID", entries.iter().any(|e| e.1.is_some())),
            ] {
                if !carried {
                    return Err(format!("no notice carries a {column}: a renamed column?"));
                }
            }
        }
        Ok(RetractionIndexBuild {
            index: Self::from_entries(data_as_of, entries)?,
            skipped_rows: outcome.skipped.len() + unindexable,
            unknown_natures: outcome.unknown_natures.into_iter().map(|u| u.raw).collect(),
        })
    }

    fn from_entries(data_as_of: &str, entries: Vec<IndexEntry>) -> Result<Self, String> {
        if data_as_of.trim().is_empty() {
            return Err("the index needs a data-as-of date".to_string());
        }
        if entries.is_empty() {
            return Err("the export holds no usable notice".to_string());
        }
        let mut by_doi: HashMap<String, Vec<usize>> = HashMap::new();
        let mut by_pmid: HashMap<String, Vec<usize>> = HashMap::new();
        for (position, entry) in entries.iter().enumerate() {
            // `build` writes keys in exactly this form; a stored entry in any
            // other could never be found, so the file is refused, not trusted.
            let indexable = entry.0.is_some() || entry.1.is_some();
            if !indexable
                || entry.0.as_deref().is_some_and(|d| lookup_doi(d) != entry.0)
                || entry
                    .1
                    .as_deref()
                    .is_some_and(|p| canonical_pmid(p) != entry.1)
            {
                return Err(format!(
                    "entry {position} has no lookup key in canonical form"
                ));
            }
            if let Some(doi) = &entry.0 {
                by_doi.entry(doi.clone()).or_default().push(position);
            }
            if let Some(pmid) = &entry.1 {
                by_pmid.entry(pmid.clone()).or_default().push(position);
            }
        }
        Ok(Self {
            data_as_of: data_as_of.to_string(),
            entries,
            by_doi,
            by_pmid,
        })
    }

    /// Serialise for storage.
    #[must_use]
    pub fn to_bytes(&self) -> Vec<u8> {
        let file = IndexFile {
            format: RETRACTION_INDEX_FORMAT.to_string(),
            version: RETRACTION_INDEX_VERSION,
            data_as_of: self.data_as_of.clone(),
            entries: self.entries.clone(),
        };
        // Serialising strings, options and unit enums cannot fail.
        serde_json::to_vec(&file).expect("an index serialises")
    }

    /// Reopen a stored index.
    ///
    /// # Errors
    ///
    /// Not an index, another format version, an empty one, or one holding an
    /// entry no lookup could find. Read any error as "the export was not
    /// consulted".
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        let file: IndexFile =
            serde_json::from_slice(bytes).map_err(|e| format!("not a retraction index: {e}"))?;
        if file.format != RETRACTION_INDEX_FORMAT {
            return Err(format!("not a retraction index: format {:?}", file.format));
        }
        if file.version != RETRACTION_INDEX_VERSION {
            return Err(format!(
                "retraction index version {} is not {RETRACTION_INDEX_VERSION}; rebuild it",
                file.version
            ));
        }
        Self::from_entries(&file.data_as_of, file.entries)
    }

    /// The date the caller gave the copy this index was built from.
    #[must_use]
    pub fn data_as_of(&self) -> &str {
        &self.data_as_of
    }

    /// The number of notices indexed.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Never true: an empty index is refused.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Every notice about one paper, matched on its DOI **or** its PMID.
    ///
    /// `None` when neither identifier is usable — blank, a sentinel (`0`,
    /// `unavailable`) — or when a PMID is given that is not ASCII digits,
    /// whatever the DOI (a DOI-only lookup misses a notice held under the PMID
    /// alone), because the export was then not consulted for this paper; pass
    /// the result straight to [`retraction_status`]'s `export`.
    /// `Some(vec![])` is "consulted, nothing".
    #[must_use]
    pub fn lookup(&self, doi: Option<&str>, pmid: Option<&str>) -> Option<Vec<RetractionEvidence>> {
        if unusable_pmid(pmid) {
            return None;
        }
        let doi = doi.and_then(lookup_doi);
        let pmid = pmid
            .and_then(|p| clean_identifier(Some(p)))
            .and_then(|p| canonical_pmid(&p));
        if doi.is_none() && pmid.is_none() {
            return None;
        }
        let mut positions: Vec<usize> = doi
            .and_then(|d| self.by_doi.get(&d))
            .into_iter()
            .chain(pmid.and_then(|p| self.by_pmid.get(&p)))
            .flatten()
            .copied()
            .collect();
        positions.sort_unstable();
        positions.dedup();
        Some(
            positions
                .into_iter()
                .map(|position| {
                    let IndexEntry(_, _, kind, raw_kind, date, notice_doi, record_id) =
                        &self.entries[position];
                    RetractionEvidence {
                        source: RetractionSource::RetractionWatchExport,
                        kind: *kind,
                        date: date.clone(),
                        notice_doi: notice_doi.clone(),
                        record_id: Some(record_id.clone()),
                        raw_kind: raw_kind.clone(),
                    }
                })
                .collect(),
        )
    }
}
