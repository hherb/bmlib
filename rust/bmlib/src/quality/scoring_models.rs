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

//! Audit-trail models for multi-dimensional quality scoring.
//!
//! A port of `bmlib/quality/scoring_models.py`. A [`DimensionScore`] holds one
//! dimension's score plus [`AssessmentDetail`] entries recording *what* was
//! extracted, *how much* it contributed, and *why*. Rule-based extractors and
//! LLM assessors both populate these, giving a reproducible per-component
//! audit trail.

use serde::{Deserialize, Serialize};

/// The study-design dimension name.
pub const DIMENSION_STUDY_DESIGN: &str = "study_design";
/// The sample-size dimension name.
pub const DIMENSION_SAMPLE_SIZE: &str = "sample_size";
/// The methodological-quality dimension name.
pub const DIMENSION_METHODOLOGICAL_QUALITY: &str = "methodological_quality";
/// The risk-of-bias dimension name.
pub const DIMENSION_RISK_OF_BIAS: &str = "risk_of_bias";
/// The replication-status dimension name.
pub const DIMENSION_REPLICATION_STATUS: &str = "replication_status";

/// Every dimension name, in Python's order.
pub const ALL_DIMENSIONS: [&str; 5] = [
    DIMENSION_STUDY_DESIGN,
    DIMENSION_SAMPLE_SIZE,
    DIMENSION_METHODOLOGICAL_QUALITY,
    DIMENSION_RISK_OF_BIAS,
    DIMENSION_REPLICATION_STATUS,
];

/// One audit-trail entry for a scored component.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AssessmentDetail {
    /// Dimension name (e.g. `"study_design"`, `"sample_size"`).
    pub dimension: String,
    /// Specific component assessed (e.g. `"randomization"`).
    pub component: String,
    /// Value found in the paper (e.g. `"double-blind"`, `"450"`).
    pub extracted_value: Option<String>,
    /// Points contributed to the dimension score.
    pub score_contribution: f64,
    /// Relevant excerpt from the paper, if any.
    #[serde(default)]
    pub evidence_text: Option<String>,
    /// Explanation for the score, if any.
    #[serde(default)]
    pub reasoning: Option<String>,
}

/// A single dimension's score with its contributing audit-trail entries.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DimensionScore {
    /// Name of this dimension.
    pub dimension_name: String,
    /// Final score for this dimension (typically 0-10).
    pub score: f64,
    /// Component assessments that contributed to the score.
    #[serde(default)]
    pub details: Vec<AssessmentDetail>,
}

impl DimensionScore {
    /// A score with no detail entries yet.
    #[must_use]
    pub fn new(dimension_name: impl Into<String>, score: f64) -> Self {
        DimensionScore {
            dimension_name: dimension_name.into(),
            score,
            details: Vec::new(),
        }
    }

    /// Append an audit-trail entry for a component of this dimension.
    pub fn add_detail(
        &mut self,
        component: &str,
        value: &str,
        contribution: f64,
        evidence: Option<String>,
        reasoning: Option<String>,
    ) {
        self.details.push(AssessmentDetail {
            dimension: self.dimension_name.clone(),
            component: component.to_string(),
            extracted_value: Some(value.to_string()),
            score_contribution: contribution,
            evidence_text: evidence,
            reasoning,
        });
    }
}
