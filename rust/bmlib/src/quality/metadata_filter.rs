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

//! Tier 1: PubMed publication types to a study design. **Free** — no API call.
//!
//! The mapping's **omissions** carry as much weight as its entries, and each was
//! argued rather than overlooked:
//!
//! * `Multicenter Study` and `Comparative Study` are **organisational or generic
//!   attributes, not designs**, so neither is mapped;
//! * `Observational Study` is PubMed's catch-all for non-experimental work whose
//!   subtype was not indexed, so mapping it to a specific design would assert a
//!   prospectivity and a tier the evidence does not support — at **high
//!   confidence**. Records carrying only such tags fall through to a deeper tier
//!   instead.
//!
//! The priority walk exists so a paper tagged with several types resolves to the
//! **strongest** rather than whichever came first in the input: a paper tagged
//! `Cohort Study` *and* `Case-Control Study` is a cohort, since cohort designs
//! outrank case-control in the evidence hierarchy.

use crate::quality::data_models::{QualityAssessment, StudyDesign};

/// Confidence when a type matched.
pub const METADATA_HIGH_CONFIDENCE: f64 = 0.9;

/// The confidence multiplier for a match **outside** the priority list.
///
/// A known-but-lower-priority tag is still a real signal, and a weaker one: the
/// priority list is ordered by specificity, so a match that had to fall through
/// to the general table was matched less precisely.
pub const METADATA_LOW_CONFIDENCE: f64 = METADATA_HIGH_CONFIDENCE * 0.8;

/// Publication type to design, in the source's own spelling.
pub const PUBMED_TYPE_TO_DESIGN: &[(&str, StudyDesign)] = &[
    ("Systematic Review", StudyDesign::SystematicReview),
    ("Meta-Analysis", StudyDesign::MetaAnalysis),
    ("Randomized Controlled Trial", StudyDesign::Rct),
    ("Controlled Clinical Trial", StudyDesign::Rct),
    ("Clinical Trial", StudyDesign::Rct),
    ("Clinical Trial, Phase I", StudyDesign::Rct),
    ("Clinical Trial, Phase II", StudyDesign::Rct),
    ("Clinical Trial, Phase III", StudyDesign::Rct),
    ("Clinical Trial, Phase IV", StudyDesign::Rct),
    ("Pragmatic Clinical Trial", StudyDesign::Rct),
    ("Equivalence Trial", StudyDesign::Rct),
    // Multicenter Study and Comparative Study are deliberately absent: they are
    // organisational and generic attributes, not designs. Observational Study is
    // absent for a different reason — it is PubMed's catch-all, so mapping it to
    // a specific design asserts prospectivity the evidence does not support.
    ("Cohort Study", StudyDesign::CohortProspective),
    ("Longitudinal Study", StudyDesign::CohortProspective),
    ("Prospective Study", StudyDesign::CohortProspective),
    ("Retrospective Study", StudyDesign::CohortRetrospective),
    ("Case-Control Study", StudyDesign::CaseControl),
    ("Cross-Sectional Study", StudyDesign::CrossSectional),
    ("Twin Study", StudyDesign::CrossSectional),
    ("Validation Study", StudyDesign::CrossSectional),
    ("Case Reports", StudyDesign::CaseReport),
    ("Practice Guideline", StudyDesign::Guideline),
    ("Guideline", StudyDesign::Guideline),
    ("Consensus Development Conference", StudyDesign::Guideline),
    ("Editorial", StudyDesign::Editorial),
    ("Letter", StudyDesign::Letter),
    ("Comment", StudyDesign::Comment),
    ("Review", StudyDesign::Other),
    ("Published Erratum", StudyDesign::Other),
    ("Retracted Publication", StudyDesign::Other),
];

/// The resolution order, most specific first.
pub const TYPE_PRIORITY: &[&str] = &[
    "Systematic Review",
    "Meta-Analysis",
    "Randomized Controlled Trial",
    "Controlled Clinical Trial",
    "Pragmatic Clinical Trial",
    "Clinical Trial, Phase III",
    "Clinical Trial, Phase IV",
    "Clinical Trial, Phase II",
    "Clinical Trial, Phase I",
    "Clinical Trial",
    // Cohort designs outrank case-control in the evidence hierarchy, so they
    // come first: a paper tagged with both resolves to the stronger design
    // rather than being downgraded.
    "Cohort Study",
    "Longitudinal Study",
    "Prospective Study",
    "Retrospective Study",
    "Case-Control Study",
    "Cross-Sectional Study",
    "Case Reports",
    "Practice Guideline",
    "Guideline",
    "Editorial",
    "Letter",
    "Comment",
];

/// Normalise a publication type for matching.
///
/// Case-folded, with hyphens and underscores become spaces, so
/// `"systematic review"`, `"Systematic Review"` and `"systematic-review"` all
/// match. The **order matters**: `Meta-Analysis` normalises to `meta analysis`,
/// which is why the table's keys are normalised the same way rather than compared
/// as written.
#[must_use]
pub fn normalize_type(raw: &str) -> String {
    raw.trim().to_lowercase().replace(['-', '_'], " ")
}

/// The design a canonical type maps to.
#[must_use]
pub fn design_for_type(canonical: &str) -> Option<StudyDesign> {
    PUBMED_TYPE_TO_DESIGN
        .iter()
        .find(|(name, _)| *name == canonical)
        .map(|(_, design)| *design)
}

/// Classify a study design from publication types. **Tier 1.**
///
/// Returns an unclassified assessment when nothing matches, which is what lets a
/// caller tell "the metadata said nothing" from "the metadata said something
/// weak" — the second is impossible here, since every match carries the same
/// high confidence and the priority list only orders *which* design wins.
#[must_use]
pub fn classify_from_metadata(publication_types: &[String]) -> QualityAssessment {
    if publication_types.is_empty() {
        return QualityAssessment::unclassified();
    }

    // Normalised to the **original** spelling, so a caller can be told which
    // input matched rather than which canonical key did.
    let normalized: Vec<(String, &String)> = publication_types
        .iter()
        .map(|raw| (normalize_type(raw), raw))
        .collect();

    // The priority walk: the first priority entry present wins.
    for candidate in TYPE_PRIORITY {
        let needle = normalize_type(candidate);
        if normalized.iter().any(|(norm, _)| *norm == needle) {
            if let Some(design) = design_for_type(candidate) {
                return QualityAssessment::from_metadata(design, METADATA_HIGH_CONFIDENCE);
            }
        }
    }

    // Then any other known type, at the lower confidence.
    for (norm, _) in &normalized {
        let canonical = PUBMED_TYPE_TO_DESIGN
            .iter()
            .find(|(name, _)| normalize_type(name) == *norm)
            .map(|(name, _)| *name);
        if let Some(canonical) = canonical {
            if let Some(design) = design_for_type(canonical) {
                return QualityAssessment::from_metadata(design, METADATA_LOW_CONFIDENCE);
            }
        }
    }

    QualityAssessment::unclassified()
}
