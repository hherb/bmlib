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

//! Data models for the PICO agent ([`crate::quality::pico_agent`]).
//!
//! A port of `bmlib/quality/pico_models.py`. A PICO breakdown names a study's
//! Population, Intervention, Comparison and Outcome. Each of the four is a
//! [`PicoPart`] carrying the model's description and the passages of the paper
//! that back it — located by [`crate::citations::find_passage`], so a citation
//! is the paper's own span and wording, never the model's copy.
//!
//! [`to_json`](PicoExtraction::to_json) gives Python's `to_dict()` key for key;
//! the oracle (`tests/pico_oracle.rs`) holds the two to each other.

use serde_json::{json, Value};

/// What the paper was found to say about one PICO part.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PicoPartState {
    /// At least one of the model's quotes was found in the paper.
    Verified,
    /// The model described the part, but none of its quotes was found (or it
    /// gave none). The description is kept for a reader to judge.
    Unverified,
    /// The model says the paper does not state this part. An honest absence,
    /// which needs no quote.
    NotReported,
}

impl PicoPartState {
    /// The wire name, as Python's enum value.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            PicoPartState::Verified => "verified",
            PicoPartState::Unverified => "unverified",
            PicoPartState::NotReported => "not_reported",
        }
    }
}

/// Why a PICO call failed: each calls for a different remedy.
///
/// Python raises `PicoError` carrying a `PicoErrorKind`; Rust returns this.
/// `InvalidSimilarity` is the one Python raises as a plain `ValueError` — a
/// caller's mistake, refused before any request.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum PicoError {
    /// The title and text were both empty, so no model call was made.
    EmptyInput,
    /// The provider was unreachable or refused the request; retrying may help.
    RequestFailed(String),
    /// The reply hit the output ceiling; raise `max_tokens`.
    Truncated(String),
    /// The reply was empty, did not parse, was not a JSON object, or lacked a
    /// required field; another model may do better.
    UnusableReply(String),
    /// The text was too long for one context and the map-reduce condensing it
    /// produced no digest that fits (or no condenser was configured). The
    /// message says why: a failed run (a provider outage during it included,
    /// so retrying may help), an empty digest, or one still over the budget.
    CondensationFailed(String),
    /// `min_similarity` was not a number in (0, 1].
    InvalidSimilarity(f64),
}

impl PicoError {
    /// Python's `PicoErrorKind` value; `"invalid_similarity"` for the one
    /// Python raises as a `ValueError`.
    #[must_use]
    pub fn kind(&self) -> &'static str {
        match self {
            PicoError::EmptyInput => "empty_input",
            PicoError::RequestFailed(_) => "request_failed",
            PicoError::Truncated(_) => "truncated",
            PicoError::UnusableReply(_) => "unusable_reply",
            PicoError::CondensationFailed(_) => "condensation_failed",
            PicoError::InvalidSimilarity(_) => "invalid_similarity",
        }
    }
}

impl std::fmt::Display for PicoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PicoError::EmptyInput => write!(f, "both the title and the text are empty"),
            PicoError::RequestFailed(message)
            | PicoError::Truncated(message)
            | PicoError::UnusableReply(message)
            | PicoError::CondensationFailed(message) => write!(f, "{message}"),
            PicoError::InvalidSimilarity(value) => {
                write!(f, "min_similarity must be a number in (0, 1], got {value}")
            }
        }
    }
}

impl std::error::Error for PicoError {}

/// A passage of the paper backing a PICO part.
///
/// Offsets are `char` indices (code points) into the `text` the caller passed,
/// as Python's `str` indices are; `text` is the paper's own wording of the span.
#[derive(Debug, Clone, PartialEq)]
pub struct PicoCitation {
    /// Start, in code points.
    pub start: usize,
    /// End (exclusive), in code points.
    pub end: usize,
    /// The paper's wording of the span.
    pub text: String,
    /// `find_passage`'s similarity.
    pub similarity: f64,
    /// Whether the match was exact after normalisation.
    pub exact: bool,
}

impl PicoCitation {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "similarity": self.similarity,
            "exact": self.exact,
        })
    }
}

/// One of the four PICO parts.
#[derive(Debug, Clone, PartialEq)]
pub struct PicoPart {
    /// The model's description, kept whatever the state.
    pub description: String,
    /// Whether the paper was found to back it.
    pub state: PicoPartState,
    /// The model's confidence, clamped to 0–1; `None` when unreadable.
    pub confidence: Option<f64>,
    /// The quotes found in the paper, as the paper's spans.
    pub citations: Vec<PicoCitation>,
    /// The model's quotes that were not found, verbatim.
    pub quotes_not_found: Vec<String>,
    /// Quotes that were not usable text at all.
    pub quotes_dropped: usize,
}

impl PicoPart {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "description": self.description,
            "state": self.state.as_str(),
            "confidence": self.confidence,
            "citations": self.citations.iter().map(PicoCitation::to_json).collect::<Vec<_>>(),
            "quotes_not_found": self.quotes_not_found,
            "quotes_dropped": self.quotes_dropped,
        })
    }
}

/// A study's PICO breakdown.
#[derive(Debug, Clone, PartialEq)]
pub struct PicoExtraction {
    /// Who was studied.
    pub population: PicoPart,
    /// What was done to them.
    pub intervention: PicoPart,
    /// What it was compared against.
    pub comparison: PicoPart,
    /// What was measured.
    pub outcome: PicoPart,
    /// The design, as the model named it; not verified.
    pub study_type: Option<String>,
    /// The number of participants, as the model gave it; not verified.
    pub sample_size: Option<String>,
    /// The mean confidence of the reported parts carrying one.
    pub overall_confidence: Option<f64>,
    /// The stripped text's length in characters when it was condensed first.
    pub condensed_from_chars: Option<i64>,
    /// The condensation's `ProcessingStatus` value, when condensed.
    pub condensation_status: Option<String>,
    /// Notes on how the extraction was made.
    pub notes: Vec<String>,
}

impl PicoExtraction {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "population": self.population.to_json(),
            "intervention": self.intervention.to_json(),
            "comparison": self.comparison.to_json(),
            "outcome": self.outcome.to_json(),
            "study_type": self.study_type,
            "sample_size": self.sample_size,
            "overall_confidence": self.overall_confidence,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": self.notes,
        })
    }
}

/// Whether a paper is an intervention study with a PICO to extract.
///
/// Only `is_suitable` is required of the model; every other field is `None`
/// when it gave none that could be read.
#[derive(Debug, Clone, PartialEq)]
pub struct PicoSuitability {
    /// Whether a PICO extraction applies.
    pub is_suitable: bool,
    /// Whether the study tests an intervention.
    pub is_intervention_study: Option<bool>,
    /// Whether it has a control or comparison group.
    pub has_comparison: Option<bool>,
    /// The design, as the model named it.
    pub study_type: Option<String>,
    /// The model's confidence, clamped to 0–1.
    pub confidence: Option<f64>,
    /// The model's explanation.
    pub rationale: Option<String>,
    /// As for [`PicoExtraction`].
    pub condensed_from_chars: Option<i64>,
    /// As for [`PicoExtraction`].
    pub condensation_status: Option<String>,
    /// As for [`PicoExtraction`].
    pub notes: Vec<String>,
}

impl PicoSuitability {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "is_suitable": self.is_suitable,
            "is_intervention_study": self.is_intervention_study,
            "has_comparison": self.has_comparison,
            "study_type": self.study_type,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": self.notes,
        })
    }
}
