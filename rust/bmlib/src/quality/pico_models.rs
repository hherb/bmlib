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

use crate::pyvalue::{python_strip, repr_str};

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
    /// Unlike Python, every `ChatError` lands here, an unknown provider and a
    /// missing key included, which no retry cures (see `docs/DECISIONS.md`).
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
    /// A [`PicoPrepared`] sends more characters than this agent's budget: it
    /// was prepared by an agent with a larger one. Python's `ValueError`.
    PreparedOverBudget {
        /// The characters the prepared paper sends.
        chars: usize,
        /// This agent's `max_context_chars`.
        budget: usize,
    },
}

impl PicoError {
    /// Python's `PicoErrorKind` value; `"invalid_similarity"` and
    /// `"prepared_over_budget"` for the two Python raises as a `ValueError`.
    #[must_use]
    pub fn kind(&self) -> &'static str {
        match self {
            PicoError::EmptyInput => "empty_input",
            PicoError::RequestFailed(_) => "request_failed",
            PicoError::Truncated(_) => "truncated",
            PicoError::UnusableReply(_) => "unusable_reply",
            PicoError::CondensationFailed(_) => "condensation_failed",
            PicoError::InvalidSimilarity(_) => "invalid_similarity",
            PicoError::PreparedOverBudget { .. } => "prepared_over_budget",
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
            PicoError::PreparedOverBudget { chars, budget } => write!(
                f,
                "the prepared text is {chars} characters, over this agent's \
                 {budget}-character budget: prepare it with this agent"
            ),
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

/// The `ProcessingStatus` values a condensation that produced a digest can
/// report: all but `"failed"`, which produces none.
const DIGEST_STATUSES: [&str; 5] = [
    "pending",
    "in_progress",
    "completed",
    "truncated",
    "partial",
];

/// A paper made ready for the PICO calls, condensed at most once (#638).
///
/// A port of Python's `PicoPrepared`. [`PicoAgent::prepare`] builds it, and
/// [`PicoAgent::check_suitability_prepared`] and
/// [`PicoAgent::extract_prepared`] take it, so a long paper's map-reduce runs
/// once for both calls and every retry. [`to_json`](Self::to_json) and
/// [`from_json`](Self::from_json) let it be stored; it holds the paper, since
/// quotes are verified against the paper and never the digest.
///
/// [`PicoAgent::prepare`]: crate::quality::pico_agent::PicoAgent::prepare
/// [`PicoAgent::check_suitability_prepared`]: crate::quality::pico_agent::PicoAgent::check_suitability_prepared
/// [`PicoAgent::extract_prepared`]: crate::quality::pico_agent::PicoAgent::extract_prepared
#[derive(Debug, Clone, PartialEq)]
pub struct PicoPrepared {
    title: String,
    text: String,
    digest: Option<String>,
    condensation_status: Option<String>,
    notes: Vec<String>,
}

impl PicoPrepared {
    /// Build one, refusing what `prepare` cannot make — Python's
    /// `__post_init__`.
    pub(crate) fn new(
        title: String,
        text: String,
        digest: Option<String>,
        condensation_status: Option<String>,
        notes: Vec<String>,
    ) -> Result<Self, String> {
        if title.is_empty() && python_strip(&text).is_empty() {
            return Err("both the title and the text are empty".to_string());
        }
        match &digest {
            None => {
                if condensation_status.is_some() || !notes.is_empty() {
                    return Err(
                        "a text sent whole carries no condensation status or notes".to_string()
                    );
                }
            }
            Some(digest) if python_strip(digest).is_empty() => {
                return Err("the digest is blank".to_string());
            }
            Some(_) => {
                if !condensation_status
                    .as_deref()
                    .is_some_and(|status| DIGEST_STATUSES.contains(&status))
                {
                    return Err(format!(
                        "condensation_status {} is not one a digest finishes with \
                         (completed, in_progress, partial, pending, truncated)",
                        condensation_status
                            .as_deref()
                            .map_or_else(|| "None".to_string(), repr_str)
                    ));
                }
            }
        }
        Ok(PicoPrepared {
            title,
            text,
            digest,
            condensation_status,
            notes,
        })
    }

    /// The title, stripped.
    #[must_use]
    pub fn title(&self) -> &str {
        &self.title
    }

    /// The text as passed, unstripped: citation offsets index it.
    #[must_use]
    pub fn text(&self) -> &str {
        &self.text
    }

    /// What is sent in place of the text when it was condensed.
    #[must_use]
    pub fn digest(&self) -> Option<&str> {
        self.digest.as_deref()
    }

    /// The condensation's `ProcessingStatus` value, when condensed.
    #[must_use]
    pub fn condensation_status(&self) -> Option<&str> {
        self.condensation_status.as_deref()
    }

    /// Notes on the condensation, carried into each result.
    #[must_use]
    pub fn notes(&self) -> &[String] {
        &self.notes
    }

    /// What the model is sent: the digest, or the stripped text.
    #[must_use]
    pub fn sent(&self) -> &str {
        self.digest
            .as_deref()
            .unwrap_or_else(|| python_strip(&self.text))
    }

    /// The stripped text's length in characters when it was condensed.
    #[must_use]
    pub fn condensed_from_chars(&self) -> Option<i64> {
        self.digest
            .as_ref()
            .map(|_| python_strip(&self.text).chars().count() as i64)
    }

    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "title": self.title,
            "text": self.text,
            "digest": self.digest,
            "condensation_status": self.condensation_status,
            "notes": self.notes,
        })
    }

    /// Python's `from_dict()`.
    ///
    /// # Errors
    ///
    /// A field absent or of the wrong type, naming it, or fields breaking the
    /// rules `prepare` keeps.
    pub fn from_json(data: &Value) -> Result<Self, String> {
        let text = |key: &str| match data.get(key) {
            Some(Value::String(value)) => Ok(value.clone()),
            other => Err(format!("'{key}' is a {}, not str", type_name(other))),
        };
        let optional = |key: &str| match data.get(key) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::String(value)) => Ok(Some(value.clone())),
            other => Err(format!("'{key}' is a {}, not str", type_name(other))),
        };
        if !data.is_object() {
            return Err(format!(
                "expected an object holding 'title', got {}",
                type_name(Some(data))
            ));
        }
        // Read in Python's order, so the first bad field is the one named.
        let title = text("title")?;
        let body = text("text")?;
        let digest = optional("digest")?;
        let condensation_status = optional("condensation_status")?;
        let notes = match data.get("notes") {
            Some(Value::Array(items)) => items
                .iter()
                .map(|item| {
                    item.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| "'notes' holds a member that is not text".to_string())
                })
                .collect::<Result<Vec<_>, _>>()?,
            other => return Err(format!("'notes' is a {}, not list", type_name(other))),
        };
        PicoPrepared::new(title, body, digest, condensation_status, notes)
    }
}

/// Python's `type(value).__name__` for a JSON value, for a refusal's message.
fn type_name(value: Option<&Value>) -> &'static str {
    match value {
        None | Some(Value::Null) => "NoneType",
        Some(Value::Bool(_)) => "bool",
        Some(Value::Number(number)) if number.is_f64() => "float",
        Some(Value::Number(_)) => "int",
        Some(Value::String(_)) => "str",
        Some(Value::Array(_)) => "list",
        Some(Value::Object(_)) => "dict",
    }
}
