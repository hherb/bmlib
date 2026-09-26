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

//! Which tier runs, and what a deeper one does to a shallower answer.
//!
//! The tiering rule is a **pure function** of the filter flags, the metadata
//! result and which deeper passes succeeded — so the pipeline's decisions are
//! testable without a model, and a caller can see why a paper got the tier it
//! did.
//!
//! # "Supersedes" means "runs instead of, when it works"
//!
//! Not "suppresses even on failure". A routine transport failure in the Cochrane
//! pass must not stop the Tier 3 assessment the caller explicitly enabled, so a
//! failed Tier 4 falls through to Tier 3 and then Tier 2 exactly as if
//! `use_cochrane_assessment` had not been set.

use crate::quality::cochrane_models::{collapse_risk_of_bias, CochraneStudyAssessment};
use crate::quality::data_models::{design_to_tier, QualityAssessment, QualityFilter};
use crate::quality::metadata_filter::classify_from_metadata;

/// The confidence at which Tier 1's answer is taken as conclusive.
///
/// Below it a deeper tier is consulted even when the metadata produced a design:
/// a guess with a low confidence is not a classification.
pub const METADATA_ACCEPTANCE_THRESHOLD: f64 = 0.9;

/// What the pipeline should do next.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TierStep {
    /// Tier 1's answer stands; no deeper pass is wanted.
    Metadata,
    /// Run the Cochrane pass (Tier 4) for `base`, then re-enter with its result.
    ///
    /// `base` is the assessment the Cochrane pass **enriches**, and which of the
    /// two it is depends on Tier 1:
    ///
    /// * Tier 1's result when the metadata was conclusive;
    /// * a **Tier 2 classification** when it was not and the classifier is
    ///   enabled, which is not an edge case — a bioRxiv or medRxiv record has no
    ///   PubMed publication types at all, so Tier 1 is inconclusive for exactly
    ///   the papers whose full text makes a Cochrane pass worth paying for.
    ///
    /// Tier 3 stays superseded regardless: it is the expensive tier whose work the
    /// Cochrane pass actually replaces.
    Cochrane {
        /// Whether the enricher is a Tier 2 classification rather than Tier 1.
        needs_classification: bool,
    },
    /// Run the deep assessment (Tier 3).
    DeepAssessment,
    /// Run the LLM classifier (Tier 2).
    Classification,
}

/// Whether Tier 1's answer counts as conclusive.
#[must_use]
pub fn metadata_is_confident(metadata: &QualityAssessment) -> bool {
    metadata.confidence >= METADATA_ACCEPTANCE_THRESHOLD && metadata.quality_tier.value() != 0
}

/// Decide the next tier.
///
/// # Errors
///
/// Never; the signature mirrors the caller's so a failure can be added without
/// changing every call site.
pub fn next_tier(
    filter: &QualityFilter,
    metadata: &QualityAssessment,
    cochrane_available: bool,
    cochrane_attempted: bool,
) -> TierStep {
    // A caller who asked for metadata only gets metadata only — before any
    // deeper question is asked, so the flag cannot be half-honoured.
    if filter.use_metadata_only {
        return TierStep::Metadata;
    }

    let confident = metadata_is_confident(metadata);

    if confident && !filter.use_detailed_assessment && !filter.use_cochrane_assessment {
        return TierStep::Metadata;
    }

    // Tier 4, deeper than Tier 3, so a **successful** pass supersedes it exactly
    // as Tier 3 supersedes Tier 2. A failed one falls through.
    if filter.use_cochrane_assessment && !cochrane_attempted {
        return TierStep::Cochrane {
            needs_classification: !confident && filter.use_llm_classification,
        };
    }
    // `cochrane_available` is read only when the pass has already been attempted,
    // so a successful one is never re-attempted and a failed one does not loop.
    let _ = cochrane_available;

    if filter.use_detailed_assessment {
        return TierStep::DeepAssessment;
    }

    if filter.use_llm_classification {
        return TierStep::Classification;
    }

    TierStep::Metadata
}

/// Fold a Cochrane assessment into the classification it enriches.
///
/// `base` supplies `study_design`, `quality_tier`, `quality_score` and
/// `confidence`, which a Cochrane assessment does not produce; the Cochrane pass
/// supplies the bias detail, which neither of the shallower tiers can see.
///
/// **Neither `evidence_level` nor `confidence` is copied across**, and both
/// omissions are deliberate:
///
/// * Cochrane's `evidence_level` is free-form model text where this one is
///   Oxford CEBM, so copying it would put a different scale in a field callers
///   compare;
/// * Cochrane's `overall_confidence` describes the model's certainty about the
///   nine bias domains, **not** about the `study_design` / `quality_tier` /
///   `quality_score` this function leaves untouched. Overwriting `confidence`
///   with it would let a caller's `if a.confidence >= t { trust
///   a.study_design }` discard a highly-confident classification because the
///   model was unsure about blinding.
///
/// Both values stay reachable on the attached Cochrane object.
#[must_use]
pub fn enrich_with_cochrane(
    base: &QualityAssessment,
    cochrane: &CochraneStudyAssessment,
) -> QualityAssessment {
    let mut enriched = base.clone();
    enriched.assessment_tier = 4;
    enriched.extraction_method = "llm_cochrane_assessment".to_string();
    // A failed collapse leaves the shallower tier's bias record in place rather
    // than erasing it: an unknown domain is not a reason to lose what was known.
    if let Ok(bias) = collapse_risk_of_bias(&cochrane.risk_of_bias) {
        enriched.bias_risk = Some(bias);
    }
    enriched.cochrane_assessment = Some(cochrane.to_json());
    // **Deliberately re-assigned from the base**, so the omission is a statement
    // in the code rather than an absence a later editor could fill in by
    // accident. `overall_confidence` describes the model's certainty about the
    // nine bias domains, not about the design and score above — see the doc
    // comment for the caller pattern this protects.
    enriched.confidence = base.confidence;
    enriched.evidence_level = base.evidence_level.clone();
    // `overall_quality_score` is likewise not copied: it is Cochrane's own
    // aggregate over its nine domains, while `quality_score` here is the
    // shallower tier's, and mixing the two scales in one field is what makes a
    // score uninterpretable.
    let _ = cochrane.overall_quality_score;
    let _ = cochrane.overall_confidence;
    // Re-listed rather than shared, so mutating either does not rewrite both —
    // the Python's `dataclasses.replace` is shallow and had to name them for the
    // same reason.
    enriched.strengths = base.strengths.clone();
    enriched.limitations = base.limitations.clone();
    enriched.extraction_details = base
        .extraction_details
        .iter()
        .cloned()
        .chain(std::iter::once("Cochrane assessment via LLM".to_string()))
        .collect();
    enriched
}

/// Run the pipeline over callbacks.
///
/// The tiers are injected rather than owned, so the pipeline has no client and a
/// test drives it with any answers it likes — including a failing Cochrane pass,
/// which is the case the fall-through exists for.
pub struct TierRunner<'a> {
    /// Tier 2, given `(title, abstract)`.
    pub classify: &'a mut dyn FnMut(&str, &str) -> QualityAssessment,
    /// Tier 3, given `(title, abstract)`.
    pub assess: &'a mut dyn FnMut(&str, &str) -> QualityAssessment,
    /// Tier 4, given `(title, text)`. `None` when it could not produce one.
    pub cochrane: &'a mut dyn FnMut(&str, &str) -> Option<CochraneStudyAssessment>,
}

/// Assess one paper through the tiered pipeline.
///
/// `abstract_text` rather than `abstract`, which is a Rust reserved word.
///
/// `full_text` falls back to `abstract`: an abstract yields a weak risk-of-bias
/// assessment, but a weak one beats none.
pub fn assess(
    runner: &mut TierRunner<'_>,
    title: &str,
    abstract_text: &str,
    publication_types: &[String],
    filter: &QualityFilter,
    full_text: Option<&str>,
) -> QualityAssessment {
    let metadata = classify_from_metadata(publication_types);

    if filter.use_metadata_only {
        return metadata;
    }

    let confident = metadata_is_confident(&metadata);

    if confident && !filter.use_detailed_assessment && !filter.use_cochrane_assessment {
        return metadata;
    }

    if filter.use_cochrane_assessment {
        let text = full_text.unwrap_or(abstract_text);
        if let Some(cochrane) = (runner.cochrane)(title, text) {
            // The design a Cochrane assessment does not carry has to come from
            // somewhere. Tier 1 supplies it when the metadata was conclusive;
            // when it was not, the cheap classifier does.
            let base = if confident || !filter.use_llm_classification {
                metadata.clone()
            } else {
                (runner.classify)(title, abstract_text)
            };
            return enrich_with_cochrane(&base, &cochrane);
        }
        // A routine transport failure must not stop the Tier 3 assessment the
        // caller explicitly enabled, so this falls through.
    }

    if filter.use_detailed_assessment {
        return (runner.assess)(title, abstract_text);
    }

    if filter.use_llm_classification {
        return (runner.classify)(title, abstract_text);
    }

    metadata
}

/// The description of which tier produced an assessment, for a log line.
#[must_use]
pub fn tier_label(assessment: &QualityAssessment) -> String {
    match assessment.assessment_tier {
        0 => "unclassified".to_string(),
        1 => "metadata".to_string(),
        2 => "llm_classifier".to_string(),
        3 => "llm_deep_assessment".to_string(),
        4 => "llm_cochrane_assessment".to_string(),
        other => format!("tier_{other}"),
    }
}

/// The tier that a design would have been recorded under, for a caller checking
/// that an enrichment left the design's own tier alone.
#[must_use]
pub fn tier_of_design(assessment: &QualityAssessment) -> i64 {
    design_to_tier(assessment.study_design).value()
}
