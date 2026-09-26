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

//! Data models for the quality assessment pipeline.
//!
//! A port of `bmlib/quality/data_models.py`: the [`StudyDesign`] and
//! [`QualityTier`] enums, the [`BiasRisk`] five-domain record, and the
//! [`QualityAssessment`] the tiers produce.
//!
//! # Two things worth knowing before reading the code
//!
//! **`QualityTier` is ordered, and its order is its evidence strength.**
//! Python gets this from `@total_ordering` over the enum's integer values;
//! Rust gets it from variant order, which is why the variants are declared
//! from `Unclassified` upward and carry explicit discriminants. A future
//! variant inserted in the wrong place would silently reorder the evidence
//! hierarchy, so [`QualityTier::ALL`] is asserted against the discriminants
//! in a test.
//!
//! **`cochrane_assessment` is a JSON value, not a typed field.** Python types
//! it `Any` to avoid `data_models` importing `cochrane_models`, which imports
//! `data_models` back for `BiasRisk`; the same cycle would bite here. Its
//! `to_dict` also tolerates a caller having assigned a plain dict, so the
//! field has to round-trip both shapes. `serde_json::Value` is that.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// A study design, as the pipeline classifies it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StudyDesign {
    /// A systematic review.
    SystematicReview,
    /// A meta-analysis.
    MetaAnalysis,
    /// A randomised controlled trial.
    Rct,
    /// A prospective cohort study.
    CohortProspective,
    /// A retrospective cohort study.
    CohortRetrospective,
    /// A case-control study.
    CaseControl,
    /// A cross-sectional study.
    CrossSectional,
    /// A case series.
    CaseSeries,
    /// A case report.
    CaseReport,
    /// A clinical guideline.
    Guideline,
    /// An editorial.
    Editorial,
    /// A letter.
    Letter,
    /// A comment.
    Comment,
    /// None of the above.
    Other,
    /// Not determined.
    Unknown,
}

impl StudyDesign {
    /// Every variant, in declaration order.
    pub const ALL: [StudyDesign; 15] = [
        StudyDesign::SystematicReview,
        StudyDesign::MetaAnalysis,
        StudyDesign::Rct,
        StudyDesign::CohortProspective,
        StudyDesign::CohortRetrospective,
        StudyDesign::CaseControl,
        StudyDesign::CrossSectional,
        StudyDesign::CaseSeries,
        StudyDesign::CaseReport,
        StudyDesign::Guideline,
        StudyDesign::Editorial,
        StudyDesign::Letter,
        StudyDesign::Comment,
        StudyDesign::Other,
        StudyDesign::Unknown,
    ];

    /// The enum **member name**, as Python's `StudyDesign.RCT.name` gives it.
    ///
    /// Distinct from [`Self::as_str`], which is the JSON name: the Python has
    /// both (`RCT.name` is `"RCT"`, and the serialised form is the lowercase
    /// key), and a caller comparing a design against a member name needs this
    /// one. Lowercasing it is not equivalent — `SYSTEMATIC_REVIEW` and
    /// `systematic_review` are the same design but not the same string, and an
    /// oracle that compared them would pass on a coincidence of `RCT` alone.
    #[must_use]
    pub fn member_name(self) -> &'static str {
        match self {
            StudyDesign::SystematicReview => "SYSTEMATIC_REVIEW",
            StudyDesign::MetaAnalysis => "META_ANALYSIS",
            StudyDesign::Rct => "RCT",
            StudyDesign::CohortProspective => "COHORT_PROSPECTIVE",
            StudyDesign::CohortRetrospective => "COHORT_RETROSPECTIVE",
            StudyDesign::CaseControl => "CASE_CONTROL",
            StudyDesign::CrossSectional => "CROSS_SECTIONAL",
            StudyDesign::CaseSeries => "CASE_SERIES",
            StudyDesign::CaseReport => "CASE_REPORT",
            StudyDesign::Guideline => "GUIDELINE",
            StudyDesign::Editorial => "EDITORIAL",
            StudyDesign::Letter => "LETTER",
            StudyDesign::Comment => "COMMENT",
            StudyDesign::Other => "OTHER",
            StudyDesign::Unknown => "UNKNOWN",
        }
    }

    /// The JSON name.
    /// The wire spelling, which is what `to_dict` writes.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            StudyDesign::SystematicReview => "systematic_review",
            StudyDesign::MetaAnalysis => "meta_analysis",
            StudyDesign::Rct => "rct",
            StudyDesign::CohortProspective => "cohort_prospective",
            StudyDesign::CohortRetrospective => "cohort_retrospective",
            StudyDesign::CaseControl => "case_control",
            StudyDesign::CrossSectional => "cross_sectional",
            StudyDesign::CaseSeries => "case_series",
            StudyDesign::CaseReport => "case_report",
            StudyDesign::Guideline => "guideline",
            StudyDesign::Editorial => "editorial",
            StudyDesign::Letter => "letter",
            StudyDesign::Comment => "comment",
            StudyDesign::Other => "other",
            StudyDesign::Unknown => "unknown",
        }
    }
}

impl std::fmt::Display for StudyDesign {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// String → design lookup, over the 27 spellings the mapping enumerates.
///
/// **This is an exact match, and it is worth stating because it looks like it
/// should not be.** `"RCT"` and `"Systematic Review"` both fall through to
/// [`StudyDesign::Unknown`], and a caller passing either gets a silent
/// "not determined" rather than an error — so the only place a mixed-case
/// value is corrected is `QualityAssessment::from_json`, which lowercases
/// before calling this. A caller reaching here directly must lowercase first.
///
/// A first cut of this port trimmed and lowercased inside the function, which
/// is friendlier and diverges from Python on exactly those three inputs; the
/// oracle caught it. Behaviour is matched rather than improved, because the
/// distinction is observable: a port that silently classifies where Python
/// declines would report a study design Python never claimed.
#[must_use]
pub fn study_design_from_str(raw: &str) -> StudyDesign {
    match raw {
        "systematic_review" | "systematic review" => StudyDesign::SystematicReview,
        "meta_analysis" | "meta-analysis" => StudyDesign::MetaAnalysis,
        "rct" | "randomized controlled trial" | "randomised controlled trial" => StudyDesign::Rct,
        "cohort_prospective" | "prospective cohort" | "cohort" => StudyDesign::CohortProspective,
        "cohort_retrospective" | "retrospective cohort" => StudyDesign::CohortRetrospective,
        "case_control" | "case-control" => StudyDesign::CaseControl,
        "cross_sectional" | "cross-sectional" => StudyDesign::CrossSectional,
        "case_series" | "case series" => StudyDesign::CaseSeries,
        "case_report" | "case report" => StudyDesign::CaseReport,
        "guideline" => StudyDesign::Guideline,
        "editorial" => StudyDesign::Editorial,
        "letter" => StudyDesign::Letter,
        "comment" => StudyDesign::Comment,
        "other" => StudyDesign::Other,
        _ => StudyDesign::Unknown,
    }
}

/// Evidence quality tier. **Higher value = stronger evidence.**
///
/// The variant order is the ordering, so it must not be rearranged: this is
/// what `QualityFilter::min_tier` compares against. Python gets the same
/// behaviour from `@total_ordering` over the enum's integer values.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub enum QualityTier {
    /// Not classified.
    Unclassified = 0,
    /// Case reports, editorials, letters.
    Tier1Anecdotal = 1,
    /// Cross-sectional, case-control.
    Tier2Observational = 2,
    /// Cohort studies.
    Tier3Controlled = 3,
    /// Randomised controlled trials.
    Tier4Experimental = 4,
    /// Systematic reviews, meta-analyses.
    Tier5Synthesis = 5,
}

impl QualityTier {
    /// Every variant, weakest first.
    pub const ALL: [QualityTier; 6] = [
        QualityTier::Unclassified,
        QualityTier::Tier1Anecdotal,
        QualityTier::Tier2Observational,
        QualityTier::Tier3Controlled,
        QualityTier::Tier4Experimental,
        QualityTier::Tier5Synthesis,
    ];

    /// The tier's integer value, which is what `to_dict` writes.
    #[must_use]
    pub fn value(self) -> i64 {
        self as i64
    }

    /// The tier for an integer, as `QualityTier(value)` behaves in Python.
    ///
    /// # Errors
    ///
    /// With the offending value if no tier carries it.
    pub fn from_value(value: i64) -> Result<Self, i64> {
        match value {
            0 => Ok(QualityTier::Unclassified),
            1 => Ok(QualityTier::Tier1Anecdotal),
            2 => Ok(QualityTier::Tier2Observational),
            3 => Ok(QualityTier::Tier3Controlled),
            4 => Ok(QualityTier::Tier4Experimental),
            5 => Ok(QualityTier::Tier5Synthesis),
            other => Err(other),
        }
    }
}

/// Every design's tier. Complete: [`StudyDesign::ALL`] has no unmapped member.
#[must_use]
pub fn design_to_tier(design: StudyDesign) -> QualityTier {
    match design {
        StudyDesign::SystematicReview | StudyDesign::MetaAnalysis | StudyDesign::Guideline => {
            QualityTier::Tier5Synthesis
        }
        StudyDesign::Rct => QualityTier::Tier4Experimental,
        StudyDesign::CohortProspective | StudyDesign::CohortRetrospective => {
            QualityTier::Tier3Controlled
        }
        StudyDesign::CaseControl | StudyDesign::CrossSectional => QualityTier::Tier2Observational,
        StudyDesign::CaseSeries
        | StudyDesign::CaseReport
        | StudyDesign::Editorial
        | StudyDesign::Letter
        | StudyDesign::Comment => QualityTier::Tier1Anecdotal,
        StudyDesign::Other | StudyDesign::Unknown => QualityTier::Unclassified,
    }
}

/// Design → randomisation status.
///
/// `Some(true)` for a design randomised by definition (RCT), `Some(false)` for
/// designs inherently non-randomised, and **`None`** where the design alone
/// does not determine it — a systematic review may synthesise RCTs or
/// observational studies. `None` is load-bearing: `require_randomization`
/// treats it as "not established", and filling these in with `Some(false)`
/// would make a review indistinguishable from a cohort study.
#[must_use]
pub fn design_to_randomized(design: StudyDesign) -> Option<bool> {
    match design {
        StudyDesign::Rct => Some(true),
        StudyDesign::CohortProspective
        | StudyDesign::CohortRetrospective
        | StudyDesign::CaseControl
        | StudyDesign::CrossSectional
        | StudyDesign::CaseSeries
        | StudyDesign::CaseReport
        | StudyDesign::Editorial
        | StudyDesign::Letter
        | StudyDesign::Comment => Some(false),
        // Deliberately not determined: a synthesis, a guideline, and the
        // catch-alls.
        StudyDesign::SystematicReview
        | StudyDesign::MetaAnalysis
        | StudyDesign::Guideline
        | StudyDesign::Other
        | StudyDesign::Unknown => None,
    }
}

/// Every design's default numeric score (0–10). Complete.
#[must_use]
pub fn design_to_score(design: StudyDesign) -> f64 {
    match design {
        StudyDesign::SystematicReview | StudyDesign::MetaAnalysis => 9.0,
        StudyDesign::Guideline => 8.5,
        StudyDesign::Rct => 8.0,
        StudyDesign::CohortProspective => 6.0,
        StudyDesign::CohortRetrospective => 5.0,
        StudyDesign::CaseControl => 4.5,
        StudyDesign::CrossSectional => 4.0,
        StudyDesign::CaseSeries => 3.0,
        StudyDesign::CaseReport => 2.0,
        StudyDesign::Editorial | StudyDesign::Letter => 1.5,
        StudyDesign::Comment => 1.0,
        StudyDesign::Other | StudyDesign::Unknown => 0.0,
    }
}

/// The five Cochrane Risk-of-Bias domains a [`QualityAssessment`] carries.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BiasRisk {
    /// Selection bias.
    pub selection: String,
    /// Performance bias.
    pub performance: String,
    /// Detection bias.
    pub detection: String,
    /// Attrition bias.
    pub attrition: String,
    /// Reporting bias.
    pub reporting: String,
}

/// The three values a bias domain may hold.
pub const VALID_BIAS_VALUES: [&str; 3] = ["low", "unclear", "high"];

impl Default for BiasRisk {
    fn default() -> Self {
        BiasRisk {
            selection: "unclear".to_string(),
            performance: "unclear".to_string(),
            detection: "unclear".to_string(),
            attrition: "unclear".to_string(),
            reporting: "unclear".to_string(),
        }
    }
}

impl BiasRisk {
    /// Read one domain, mapping anything unrecognised to `"unclear"`.
    ///
    /// Python's `v()` accepts a value only if it is in `("low", "unclear",
    /// "high")`, so a typo, a `null` or a number all become `"unclear"` rather
    /// than travelling on as a claim.
    #[must_use]
    pub fn domain_from_json(data: &serde_json::Value, key: &str) -> String {
        match data.get(key).and_then(serde_json::Value::as_str) {
            Some(v) if VALID_BIAS_VALUES.contains(&v) => v.to_string(),
            _ => "unclear".to_string(),
        }
    }

    /// Deserialise from [`Self::to_json`] output, or from anything dict-shaped.
    #[must_use]
    pub fn from_json(data: &serde_json::Value) -> Self {
        BiasRisk {
            selection: Self::domain_from_json(data, "selection"),
            performance: Self::domain_from_json(data, "performance"),
            detection: Self::domain_from_json(data, "detection"),
            attrition: Self::domain_from_json(data, "attrition"),
            reporting: Self::domain_from_json(data, "reporting"),
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "selection": self.selection,
            "performance": self.performance,
            "detection": self.detection,
            "attrition": self.attrition,
            "reporting": self.reporting,
        })
    }
}

/// Result from any tier of the quality pipeline.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QualityAssessment {
    /// 0=unclassified, 1=metadata, 2=LLM classifier, 3=deep, 4=Cochrane.
    pub assessment_tier: i64,
    /// Which tier produced it.
    pub extraction_method: String,
    /// The classified design.
    pub study_design: StudyDesign,
    /// The evidence tier.
    pub quality_tier: QualityTier,
    /// 0–10.
    pub quality_score: f64,
    /// Oxford CEBM level.
    pub evidence_level: Option<String>,
    /// Randomised, when the design determines it.
    pub is_randomized: Option<bool>,
    /// Controlled, when known.
    pub is_controlled: Option<bool>,
    /// `none` / `single` / `double` / `triple`.
    pub is_blinded: Option<String>,
    /// Prospective, when known.
    pub is_prospective: Option<bool>,
    /// Multicentre, when known.
    pub is_multicenter: Option<bool>,
    /// Number of participants, when known.
    pub sample_size: Option<i64>,
    /// 0–1 confidence in the assessment.
    pub confidence: f64,
    /// The five-domain bias record.
    pub bias_risk: Option<BiasRisk>,
    /// What the assessment found in the paper's favour.
    pub strengths: Vec<String>,
    /// What it found against.
    pub limitations: Vec<String>,
    /// Free-text notes per component.
    pub extraction_details: Vec<String>,
    /// Whether a transparency result adjusted this assessment.
    pub transparency_adjusted: bool,
    /// The attached Cochrane assessment, as JSON.
    ///
    /// Typed as a JSON value rather than a `CochraneStudyAssessment` for the
    /// reason the Python field is typed `Any`: naming the type would make this
    /// module depend on `cochrane_models`, which depends back on this one.
    pub cochrane_assessment: Option<serde_json::Value>,
}

impl Default for QualityAssessment {
    fn default() -> Self {
        QualityAssessment {
            assessment_tier: 0,
            extraction_method: "none".to_string(),
            study_design: StudyDesign::Unknown,
            quality_tier: QualityTier::Unclassified,
            quality_score: 0.0,
            evidence_level: None,
            is_randomized: None,
            is_controlled: None,
            is_blinded: None,
            is_prospective: None,
            is_multicenter: None,
            sample_size: None,
            confidence: 0.0,
            bias_risk: None,
            strengths: Vec::new(),
            limitations: Vec::new(),
            extraction_details: Vec::new(),
            transparency_adjusted: false,
            cochrane_assessment: None,
        }
    }
}

impl QualityAssessment {
    /// The unclassified result every tier falls back to.
    #[must_use]
    pub fn unclassified() -> Self {
        Self::default()
    }

    /// Tier 1: from PubMed metadata.
    #[must_use]
    pub fn from_metadata(design: StudyDesign, confidence: f64) -> Self {
        QualityAssessment {
            assessment_tier: 1,
            extraction_method: "pubmed_metadata".to_string(),
            study_design: design,
            quality_tier: design_to_tier(design),
            quality_score: design_to_score(design),
            is_randomized: design_to_randomized(design),
            confidence,
            ..Self::default()
        }
    }

    /// Tier 2: from the LLM classifier.
    #[must_use]
    pub fn from_classification(
        study_design: StudyDesign,
        confidence: f64,
        sample_size: Option<i64>,
        is_blinded: Option<String>,
    ) -> Self {
        QualityAssessment {
            assessment_tier: 2,
            extraction_method: "llm_classifier".to_string(),
            study_design,
            quality_tier: design_to_tier(study_design),
            quality_score: design_to_score(study_design),
            is_randomized: design_to_randomized(study_design),
            confidence,
            sample_size,
            is_blinded,
            ..Self::default()
        }
    }

    /// Whether this assessment satisfies `filter`.
    ///
    /// Each clause mirrors Python's, including the two asymmetries that are
    /// easy to get wrong: `min_sample_size` is skipped when the assessment
    /// records **no** sample size (an unknown size is not a small one), and
    /// `require_randomization` rejects `None` as well as `false` (an
    /// undetermined design has not shown itself to be randomised).
    #[must_use]
    pub fn passes_filter(&self, filter: &QualityFilter) -> bool {
        if let Some(min_tier) = filter.min_tier {
            if self.quality_tier < min_tier {
                return false;
            }
        }
        if filter.require_randomization && self.is_randomized != Some(true) {
            return false;
        }
        if filter.require_blinding && matches!(self.is_blinded.as_deref(), None | Some("none")) {
            return false;
        }
        if let (Some(min), Some(size)) = (filter.min_sample_size, self.sample_size) {
            if size < min {
                return false;
            }
        }
        true
    }

    /// Serialise to a plain JSON object, matching Python's `to_dict`.
    ///
    /// `original_quality_tier`, `transparency_result` and
    /// `extraction_details` are deliberately absent — Python omits all three,
    /// so a round trip through this drops them. `bias_risk` and
    /// `cochrane_assessment` appear only when set.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        let mut d = serde_json::Map::new();
        d.insert("assessment_tier".into(), self.assessment_tier.into());
        d.insert(
            "extraction_method".into(),
            self.extraction_method.clone().into(),
        );
        d.insert("study_design".into(), self.study_design.as_str().into());
        d.insert("quality_tier".into(), self.quality_tier.value().into());
        d.insert("quality_score".into(), self.quality_score.into());
        d.insert(
            "evidence_level".into(),
            self.evidence_level
                .clone()
                .map_or(serde_json::Value::Null, Into::into),
        );
        for (key, value) in [
            ("is_randomized", self.is_randomized),
            ("is_controlled", self.is_controlled),
            ("is_prospective", self.is_prospective),
            ("is_multicenter", self.is_multicenter),
        ] {
            d.insert(
                key.into(),
                value.map_or(serde_json::Value::Null, Into::into),
            );
        }
        d.insert(
            "is_blinded".into(),
            self.is_blinded
                .clone()
                .map_or(serde_json::Value::Null, Into::into),
        );
        d.insert(
            "sample_size".into(),
            self.sample_size.map_or(serde_json::Value::Null, Into::into),
        );
        d.insert("confidence".into(), self.confidence.into());
        d.insert("strengths".into(), serde_json::json!(self.strengths));
        d.insert("limitations".into(), serde_json::json!(self.limitations));
        d.insert(
            "transparency_adjusted".into(),
            self.transparency_adjusted.into(),
        );
        if let Some(bias) = &self.bias_risk {
            d.insert("bias_risk".into(), bias.to_json());
        }
        if let Some(cochrane) = &self.cochrane_assessment {
            // Python calls `.to_dict()` when the value has one and passes it
            // through otherwise — a caller who round-tripped through JSON may
            // have assigned the plain dict straight back. Here the field is
            // already JSON, so there is nothing to convert; the branch exists
            // so the key is present exactly when the value is.
            if !cochrane.is_null() {
                d.insert("cochrane_assessment".into(), cochrane.clone());
            }
        }
        serde_json::Value::Object(d)
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// With the offending value if `quality_tier` is not a known tier.
    pub fn from_json(data: &serde_json::Value) -> Result<Self, String> {
        let design = data
            .get("study_design")
            .and_then(serde_json::Value::as_str)
            .map_or(StudyDesign::Unknown, study_design_from_str);
        let tier_value = data
            .get("quality_tier")
            .and_then(serde_json::Value::as_i64)
            .unwrap_or(0);
        let quality_tier = QualityTier::from_value(tier_value)
            .map_err(|v| format!("no QualityTier with value {v}"))?;

        let bias_risk = data.get("bias_risk").map(BiasRisk::from_json);
        let cochrane_assessment = data
            .get("cochrane_assessment")
            .filter(|v| !v.is_null())
            .cloned();

        Ok(QualityAssessment {
            assessment_tier: data
                .get("assessment_tier")
                .and_then(serde_json::Value::as_i64)
                .unwrap_or(0),
            extraction_method: data
                .get("extraction_method")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("none")
                .to_string(),
            study_design: design,
            quality_tier,
            quality_score: data
                .get("quality_score")
                .and_then(serde_json::Value::as_f64)
                .unwrap_or(0.0),
            evidence_level: data
                .get("evidence_level")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
            is_randomized: data
                .get("is_randomized")
                .and_then(serde_json::Value::as_bool),
            is_controlled: data
                .get("is_controlled")
                .and_then(serde_json::Value::as_bool),
            is_blinded: data
                .get("is_blinded")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
            is_prospective: data
                .get("is_prospective")
                .and_then(serde_json::Value::as_bool),
            is_multicenter: data
                .get("is_multicenter")
                .and_then(serde_json::Value::as_bool),
            sample_size: data.get("sample_size").and_then(serde_json::Value::as_i64),
            confidence: data
                .get("confidence")
                .and_then(serde_json::Value::as_f64)
                .unwrap_or(0.0),
            bias_risk,
            strengths: string_list(data, "strengths"),
            limitations: string_list(data, "limitations"),
            extraction_details: string_list(data, "extraction_details"),
            transparency_adjusted: data
                .get("transparency_adjusted")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            cochrane_assessment,
        })
    }
}

fn string_list(data: &serde_json::Value, key: &str) -> Vec<String> {
    data.get(key)
        .and_then(serde_json::Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(serde_json::Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// User-configurable quality filter thresholds.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QualityFilter {
    /// Minimum acceptable evidence tier.
    pub min_tier: Option<QualityTier>,
    /// Demand a randomised design.
    pub require_randomization: bool,
    /// Demand blinding.
    pub require_blinding: bool,
    /// Minimum sample size.
    pub min_sample_size: Option<i64>,
    /// Run Tier 1 only.
    pub use_metadata_only: bool,
    /// Run Tier 2.
    pub use_llm_classification: bool,
    /// Run Tier 3.
    pub use_detailed_assessment: bool,
    /// Run Tier 4.
    pub use_cochrane_assessment: bool,
}

/// Python's field defaults, which are **not** all-`false`.
///
/// A derived `Default` set every flag to `false`, so `use_llm_classification`
/// was `false` where Python's constructor makes it `true` — and a caller who
/// wrote `QualityFilter::default()` silently got Tier 1 only, with no error and
/// no log line. That is the same shape as the defects this port has been fixing:
/// a plausible default that answers a different question than the caller asked.
impl Default for QualityFilter {
    fn default() -> Self {
        QualityFilter::defaults()
    }
}

impl QualityFilter {
    /// A default filter, which is **not** all-`false`.
    ///
    /// A derived `Default` gave `use_llm_classification: false` where Python's
    /// constructor makes it `true`, so `QualityFilter::default()` silently meant
    /// "Tier 1 only" — a plausible default answering a different question than
    /// the caller asked, which is the shape of the defects this port has been
    /// fixing. The `impl Default` above routes here, and a test pins that a
    /// default filter reaches Tier 2.
    ///
    /// A filter with the pipeline defaults.
    ///
    /// `use_llm_classification` is **true** by default and the other three
    /// stage flags false, which is Python's default and not obvious from the
    /// field list.
    #[must_use]
    pub fn defaults() -> Self {
        QualityFilter {
            min_tier: None,
            require_randomization: false,
            require_blinding: false,
            min_sample_size: None,
            use_metadata_only: false,
            use_llm_classification: true,
            use_detailed_assessment: false,
            use_cochrane_assessment: false,
        }
    }
}

/// A count of how many designs map into each tier, for callers that want it.
#[must_use]
pub fn designs_per_tier() -> BTreeMap<QualityTier, usize> {
    let mut counts: BTreeMap<QualityTier, usize> = BTreeMap::new();
    for design in StudyDesign::ALL {
        *counts.entry(design_to_tier(design)).or_insert(0) += 1;
    }
    counts
}
