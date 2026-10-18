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

//! Retraction status for the apps (bmlib#504): the rule, the Crossref request
//! and parse functions, and the Retraction Watch export index. The app brings
//! the transport and the storage.
//!
//! **Absent values are `Option`s here**, unlike the JATS records' empty
//! strings: an undated notice and a notice dated `""` are different claims, and
//! `export: None` ("not consulted") against `Some([])` ("consulted, nothing") is
//! the distinction the whole rule rests on.

use std::collections::HashMap;
use std::sync::Arc;

use bmlib::publications::retraction_status as rs;

/// Where a piece of evidence came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
#[cfg_attr(test, derive(serde::Serialize), serde(rename_all = "snake_case"))]
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

/// What a notice says about the paper.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
#[cfg_attr(test, derive(serde::Serialize), serde(rename_all = "snake_case"))]
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
    /// Reinstated.
    Reinstatement,
    /// Outside the vocabulary; `raw_kind` keeps the original. Decides nothing.
    Other,
}

/// The verdict. `Unchecked` is never a softer `Clear`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
#[cfg_attr(test, derive(serde::Serialize), serde(rename_all = "snake_case"))]
pub enum RetractionStatus {
    /// Retracted, and not reinstated since. Leave the paper out.
    Retracted,
    /// An expression of concern or a partial retraction stands. Warn.
    Concern,
    /// Every lookup answered and none found anything.
    Clear,
    /// Nothing found, but a lookup did not answer.
    Unchecked,
}

/// A lookup that has to answer before a paper can be called clear.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
#[cfg_attr(test, derive(serde::Serialize), serde(rename_all = "snake_case"))]
pub enum RetractionLookup {
    /// The Retraction Watch export was consulted.
    RetractionWatchExport,
    /// Crossref served this DOI.
    Crossref,
}

/// One source's statement about one paper.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct RetractionEvidence {
    /// Where it came from.
    pub source: RetractionSource,
    /// What it says.
    pub kind: RetractionKind,
    /// ISO date, as precise as deposited (`yyyy-mm-dd`, `yyyy-mm` or `yyyy`).
    pub date: Option<String>,
    /// The notice's DOI, lower-cased — show it beside a retraction.
    pub notice_doi: Option<String>,
    /// Retraction Watch's record ID.
    pub record_id: Option<String>,
    /// The source's own spelling of the kind.
    pub raw_kind: Option<String>,
}

/// What [`retraction_status`] decided, and on what.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
#[cfg_attr(test, derive(serde::Serialize))]
pub struct RetractionVerdict {
    /// The status.
    pub status: RetractionStatus,
    /// The newest retraction (`Retracted`), the newest concern (`Concern`),
    /// otherwise the reinstatement that undid a retraction, if any.
    pub deciding: Option<RetractionEvidence>,
    /// Every lookup that did not answer — beside any status, since a concern
    /// found while Crossref was down may yet be a retraction.
    pub unanswered: Vec<RetractionLookup>,
}

/// Crossref's answer about one DOI.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Enum)]
#[cfg_attr(
    test,
    derive(serde::Serialize),
    serde(tag = "outcome", rename_all = "snake_case")
)]
pub enum CrossrefResult {
    /// Crossref served the record. Pass it to [`retraction_status`].
    Served {
        /// Its `updated-by` relations.
        evidence: Vec<RetractionEvidence>,
        /// The DOIs its own retraction-family `update-to` names.
        retraction_targets: Vec<String>,
    },
    /// Absent from a successful answer: Crossref does not know this DOI, and
    /// asking again will not change it. The paper is unchecked, not clear.
    NotKnown,
    /// The batch failed, or this DOI's relations could not be read. Retry by
    /// your own pacing; never read it as "not known".
    Unreachable {
        /// Why, for a log line; the wording is not part of the contract.
        reason: String,
    },
}

/// One request URL and the DOIs it asks about.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct CrossrefBatch {
    /// The URL to GET (with your mailto User-Agent).
    pub url: String,
    /// The normalised DOIs to hand back to [`parse_crossref_retraction_response`].
    pub dois: Vec<String>,
}

/// The batches to send, and the DOIs no batch can carry.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct CrossrefRequests {
    /// The requests.
    pub batches: Vec<CrossrefBatch>,
    /// DOIs containing a comma, which a filter list cannot carry. Crossref has
    /// not answered for them: pass `None` as their Crossref result.
    pub unsendable: Vec<String>,
}

/// Why a Retraction Watch index could not be built or opened. Either way the
/// export was **not consulted**: pass `None` as `export`, never `[]`.
#[derive(Debug, uniffi::Error)]
pub enum RetractionIndexError {
    /// The CSV or the stored index is unusable.
    Unusable {
        /// Why. Not `message`, which collides with Kotlin's `Throwable.message`.
        reason: String,
    },
}

impl std::fmt::Display for RetractionIndexError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unusable { reason } => write!(f, "unusable Retraction Watch index: {reason}"),
        }
    }
}
impl std::error::Error for RetractionIndexError {}

/// The Retraction Watch export as one file the app stores.
#[derive(Debug, uniffi::Object)]
pub struct RetractionIndex(rs::RetractionIndex);

/// What [`build_retraction_index`] produced and what it left out.
#[derive(Debug, uniffi::Record)]
pub struct RetractionIndexBuild {
    /// The index. Store [`RetractionIndex::to_bytes`].
    pub index: Arc<RetractionIndex>,
    /// Rows skipped: no record ID, or no usable identifier for the paper.
    pub skipped_rows: u64,
    /// Nature strings this version could not map. Non-empty means Retraction
    /// Watch's vocabulary moved: report it, since those notices decide nothing.
    pub unknown_natures: Vec<String>,
}

// ---------------------------------------------------------------------------
// Functions
// ---------------------------------------------------------------------------

/// Decide one paper's retraction status. See bmlib's `retraction_status`.
///
/// `export`: `None` when the export was not consulted (no index, an unusable
/// one, or [`RetractionIndex::lookup`] returned `None`). `crossref`: `None`
/// when Crossref was not asked (no DOI, an unsendable one).
#[uniffi::export]
pub fn retraction_status(
    doi: Option<String>,
    export: Option<Vec<RetractionEvidence>>,
    crossref: Option<CrossrefResult>,
    publication_types: Vec<String>,
) -> RetractionVerdict {
    let export: Option<Vec<rs::RetractionEvidence>> =
        export.map(|e| e.into_iter().map(Into::into).collect());
    let crossref: Option<rs::CrossrefResult> = crossref.map(Into::into);
    rs::retraction_status(
        doi.as_deref(),
        export.as_deref(),
        crossref.as_ref(),
        &publication_types,
    )
    .into()
}

/// Build the batched Crossref requests for `dois`.
#[uniffi::export]
pub fn crossref_retraction_requests(dois: Vec<String>, mailto: Option<String>) -> CrossrefRequests {
    let rs::CrossrefRequests {
        batches,
        unsendable,
    } = rs::crossref_retraction_requests(&dois, mailto.as_deref());
    CrossrefRequests {
        batches: batches
            .into_iter()
            .map(|rs::CrossrefBatch { url, dois }| CrossrefBatch { url, dois })
            .collect(),
        unsendable,
    }
}

/// Read one batch's answer: a result per requested DOI, keyed by the
/// normalised DOI. A failed batch makes every DOI in it `Unreachable`.
#[uniffi::export]
pub fn parse_crossref_retraction_response(
    status: u16,
    body: Vec<u8>,
    requested_dois: Vec<String>,
) -> HashMap<String, CrossrefResult> {
    rs::parse_crossref_retraction_response(status, &body, &requested_dois)
        .into_iter()
        .map(|(doi, result)| (doi, result.into()))
        .collect()
}

/// Build an index from the export's CSV bytes. `data_as_of` is your date for
/// the copy (the day it was downloaded), returned by
/// [`RetractionIndex::data_as_of`] so a verdict can say how fresh it is.
///
/// # Errors
///
/// The CSV is not UTF-8 or is malformed, `data_as_of` is blank, or no row was
/// usable. Keep the previous index, if any.
#[uniffi::export]
pub fn build_retraction_index(
    csv: Vec<u8>,
    data_as_of: String,
) -> Result<RetractionIndexBuild, RetractionIndexError> {
    let rs::RetractionIndexBuild {
        index,
        skipped_rows,
        unknown_natures,
    } = rs::RetractionIndex::build(&csv, &data_as_of)
        .map_err(|reason| RetractionIndexError::Unusable { reason })?;
    Ok(RetractionIndexBuild {
        index: Arc::new(RetractionIndex(index)),
        skipped_rows: skipped_rows as u64,
        unknown_natures,
    })
}

#[uniffi::export]
impl RetractionIndex {
    /// Reopen a stored index.
    ///
    /// # Errors
    ///
    /// Not an index, another format version, or empty: rebuild it, and until
    /// then treat the export as not consulted.
    #[uniffi::constructor]
    pub fn from_bytes(bytes: Vec<u8>) -> Result<Arc<Self>, RetractionIndexError> {
        rs::RetractionIndex::from_bytes(&bytes)
            .map(|index| Arc::new(Self(index)))
            .map_err(|reason| RetractionIndexError::Unusable { reason })
    }

    /// The bytes to store.
    pub fn to_bytes(&self) -> Vec<u8> {
        self.0.to_bytes()
    }

    /// The date given when the index was built.
    pub fn data_as_of(&self) -> String {
        self.0.data_as_of().to_string()
    }

    /// The number of notices indexed.
    pub fn notice_count(&self) -> u64 {
        self.0.len() as u64
    }

    /// Every notice about one paper, by DOI **or** PMID. `None` when neither
    /// is usable: the export was then not consulted for this paper. Pass the
    /// result straight to [`retraction_status`]'s `export`.
    pub fn lookup(
        &self,
        doi: Option<String>,
        pmid: Option<String>,
    ) -> Option<Vec<RetractionEvidence>> {
        self.0
            .lookup(doi.as_deref(), pmid.as_deref())
            .map(|evidence| evidence.into_iter().map(Into::into).collect())
    }
}

// ---------------------------------------------------------------------------
// Conversions, exhaustive both ways
// ---------------------------------------------------------------------------

macro_rules! mirror_enum {
    ($ffi:ident, $core:path, [$($variant:ident),* $(,)?]) => {
        impl From<$core> for $ffi {
            fn from(value: $core) -> Self {
                use $core as C;
                match value { $(C::$variant => Self::$variant),* }
            }
        }
        impl From<$ffi> for $core {
            fn from(value: $ffi) -> Self {
                use $core as C;
                match value { $($ffi::$variant => C::$variant),* }
            }
        }
    };
}

mirror_enum!(
    RetractionSource,
    rs::RetractionSource,
    [
        RetractionWatchExport,
        CrossrefRetractionWatch,
        CrossrefPublisher,
        PubmedLabel
    ]
);
mirror_enum!(
    RetractionKind,
    rs::RetractionKind,
    [
        Retraction,
        Withdrawal,
        Removal,
        PartialRetraction,
        ExpressionOfConcern,
        Correction,
        Erratum,
        Reinstatement,
        Other
    ]
);
mirror_enum!(
    RetractionStatus,
    rs::RetractionStatus,
    [Retracted, Concern, Clear, Unchecked]
);
mirror_enum!(
    RetractionLookup,
    rs::RetractionLookup,
    [RetractionWatchExport, Crossref]
);

impl From<rs::RetractionEvidence> for RetractionEvidence {
    fn from(e: rs::RetractionEvidence) -> Self {
        let rs::RetractionEvidence {
            source,
            kind,
            date,
            notice_doi,
            record_id,
            raw_kind,
        } = e;
        Self {
            source: source.into(),
            kind: kind.into(),
            date,
            notice_doi,
            record_id,
            raw_kind,
        }
    }
}

impl From<RetractionEvidence> for rs::RetractionEvidence {
    fn from(e: RetractionEvidence) -> Self {
        let RetractionEvidence {
            source,
            kind,
            date,
            notice_doi,
            record_id,
            raw_kind,
        } = e;
        Self {
            source: source.into(),
            kind: kind.into(),
            date,
            notice_doi,
            record_id,
            raw_kind,
        }
    }
}

impl From<rs::RetractionVerdict> for RetractionVerdict {
    fn from(v: rs::RetractionVerdict) -> Self {
        let rs::RetractionVerdict {
            status,
            deciding,
            unanswered,
        } = v;
        Self {
            status: status.into(),
            deciding: deciding.map(Into::into),
            unanswered: unanswered.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<rs::CrossrefResult> for CrossrefResult {
    fn from(r: rs::CrossrefResult) -> Self {
        match r {
            rs::CrossrefResult::Served {
                evidence,
                retraction_targets,
            } => Self::Served {
                evidence: evidence.into_iter().map(Into::into).collect(),
                retraction_targets,
            },
            rs::CrossrefResult::NotKnown => Self::NotKnown,
            rs::CrossrefResult::Unreachable { reason } => Self::Unreachable { reason },
        }
    }
}

impl From<CrossrefResult> for rs::CrossrefResult {
    fn from(r: CrossrefResult) -> Self {
        match r {
            CrossrefResult::Served {
                evidence,
                retraction_targets,
            } => Self::Served {
                evidence: evidence.into_iter().map(Into::into).collect(),
                retraction_targets,
            },
            CrossrefResult::NotKnown => Self::NotKnown,
            CrossrefResult::Unreachable { reason } => Self::Unreachable { reason },
        }
    }
}
