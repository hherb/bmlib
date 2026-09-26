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

//! Cochrane-aligned data models for study assessment.
//!
//! A port of `bmlib/quality/cochrane_models.py`. Models the Cochrane Handbook's
//! requirements for systematic reviews:
//!
//! - a **study-characteristics table** — Methods, Participants, Interventions,
//!   Outcomes, Notes;
//! - a **risk-of-bias assessment** — the nine standard domains, each with a
//!   judgement and supporting text.
//!
//! A strict superset of [`crate::quality::data_models::BiasRisk`]: where that
//! records five domains as bare strings, this captures nine with rationale plus
//! the full characteristics table.
//!
//! # The severity order is load-bearing
//!
//! [`SEVERITY_ORDER`] is `low < unclear < high`, and `unclear` outranking `low`
//! is deliberate: an unreported domain is **not** a clean bill of health. You
//! cannot claim low selection-bias risk when allocation concealment was never
//! described. [`collapse_risk_of_bias`] takes the worst rank per target field,
//! so one unreported domain of four promotes a field to `"unclear"`.

use serde::Serialize;

use crate::quality::data_models::BiasRisk;

/// Cochrane risk-of-bias judgement: low.
pub const ROB_JUDGEMENT_LOW: &str = "Low risk";
/// Cochrane risk-of-bias judgement: high.
pub const ROB_JUDGEMENT_HIGH: &str = "High risk";
/// Cochrane risk-of-bias judgement: unclear.
pub const ROB_JUDGEMENT_UNCLEAR: &str = "Unclear risk";

/// The three valid judgement strings.
pub const VALID_ROB_JUDGEMENTS: [&str; 3] =
    [ROB_JUDGEMENT_LOW, ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_UNCLEAR];

/// `BiasRisk`'s vocabulary, weakest first — see the module docs.
pub const SEVERITY_ORDER: [&str; 3] = ["low", "unclear", "high"];

/// Cochrane judgement → the word [`BiasRisk`] uses for it.
#[must_use]
pub fn judgement_to_bias_risk(judgement: &str) -> &'static str {
    match judgement {
        ROB_JUDGEMENT_LOW => "low",
        ROB_JUDGEMENT_HIGH => "high",
        // Unclear, and anything unrecognised, which `RiskOfBiasJudgement`
        // has already mapped to unclear.
        _ => "unclear",
    }
}

/// `bias_type` → the [`BiasRisk`] field it feeds.
///
/// The 9→5 grouping is read off the items themselves rather than written out
/// per domain, so a tenth domain of an existing type collapses correctly
/// without this being touched.
#[must_use]
pub fn bias_type_to_field(bias_type: &str) -> Option<&'static str> {
    match bias_type.trim().to_lowercase().as_str() {
        "selection bias" => Some("selection"),
        "performance bias" => Some("performance"),
        "detection bias" => Some("detection"),
        "attrition bias" => Some("attrition"),
        "reporting bias" => Some("reporting"),
        _ => None,
    }
}

/// A Cochrane Risk-of-Bias judgement category.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RiskOfBiasJudgement {
    /// Low risk of bias.
    Low,
    /// High risk of bias.
    High,
    /// Unclear, or not reported.
    Unclear,
}

impl RiskOfBiasJudgement {
    /// The wire spelling, which is what `to_dict` writes.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            RiskOfBiasJudgement::Low => ROB_JUDGEMENT_LOW,
            RiskOfBiasJudgement::High => ROB_JUDGEMENT_HIGH,
            RiskOfBiasJudgement::Unclear => ROB_JUDGEMENT_UNCLEAR,
        }
    }

    /// Convert a string, tolerating case and the common variations.
    ///
    /// Anything unrecognised falls back to [`Self::Unclear`] rather than
    /// failing — Python warns and does the same. A judgement is a *reading of
    /// evidence*, and refusing to produce one because a model spelled it oddly
    /// would lose the domain entirely.
    #[must_use]
    pub fn from_string(value: &str) -> Self {
        match value.to_lowercase().trim() {
            "low" | "low risk" | "low_risk" => RiskOfBiasJudgement::Low,
            "high" | "high risk" | "high_risk" => RiskOfBiasJudgement::High,
            _ => RiskOfBiasJudgement::Unclear,
        }
    }
}

impl std::fmt::Display for RiskOfBiasJudgement {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A single risk-of-bias domain assessment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RiskOfBiasItem {
    /// Name of the bias domain (e.g. `"Random sequence generation"`).
    pub domain: String,
    /// Category of bias (e.g. `"selection bias"`).
    pub bias_type: String,
    /// One of the three judgement strings.
    pub judgement: String,
    /// Text explaining the basis for the judgement.
    pub support_for_judgement: String,
    /// For detection bias, `"subjective"` or `"objective"`.
    pub outcome_type: Option<String>,
}

impl RiskOfBiasItem {
    /// Build an item.
    #[must_use]
    pub fn new(
        domain: impl Into<String>,
        bias_type: impl Into<String>,
        judgement: impl Into<String>,
        support_for_judgement: impl Into<String>,
        outcome_type: Option<String>,
    ) -> Self {
        RiskOfBiasItem {
            domain: domain.into(),
            bias_type: bias_type.into(),
            judgement: judgement.into(),
            support_for_judgement: support_for_judgement.into(),
            outcome_type,
        }
    }

    /// Whether [`Self::judgement`] is one of the three valid strings.
    ///
    /// Python *warns* on an invalid judgement and keeps it, so this reports
    /// rather than corrects.
    #[must_use]
    pub fn judgement_is_valid(&self) -> bool {
        VALID_ROB_JUDGEMENTS.contains(&self.judgement.as_str())
    }

    /// Serialise to a plain JSON object, `outcome_type` omitted when unset.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        let mut d = serde_json::Map::new();
        d.insert("domain".into(), self.domain.clone().into());
        d.insert("bias_type".into(), self.bias_type.clone().into());
        d.insert("judgement".into(), self.judgement.clone().into());
        d.insert(
            "support_for_judgement".into(),
            self.support_for_judgement.clone().into(),
        );
        // Python: `if self.outcome_type:` — an empty string is falsy and so is
        // omitted. Matching on emptiness rather than on `Option::is_some`
        // keeps that.
        if let Some(outcome_type) = self.outcome_type.as_ref().filter(|s| !s.is_empty()) {
            d.insert("outcome_type".into(), outcome_type.clone().into());
        }
        serde_json::Value::Object(d)
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// If a required field is absent or not a string.
    pub fn from_json(data: &serde_json::Value) -> Result<Self, String> {
        let get = |key: &str| -> Result<String, String> {
            data.get(key)
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
                .ok_or_else(|| format!("RiskOfBiasItem: missing string field {key:?}"))
        };
        Ok(RiskOfBiasItem {
            domain: get("domain")?,
            bias_type: get("bias_type")?,
            judgement: get("judgement")?,
            support_for_judgement: get("support_for_judgement")?,
            outcome_type: data
                .get("outcome_type")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
        })
    }
}

/// The nine Cochrane Risk-of-Bias domains.
///
/// Selection bias (4): random sequence generation, allocation concealment,
/// baseline outcome measurements, baseline characteristics. Performance bias
/// (1): blinding of participants and personnel. Detection bias (2): blinding of
/// outcome assessment, split by subjective/objective outcomes. Attrition bias
/// (1): incomplete outcome data. Reporting bias (1): selective reporting.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CochraneRiskOfBias {
    /// Random sequence generation (selection).
    pub random_sequence_generation: RiskOfBiasItem,
    /// Allocation concealment (selection).
    pub allocation_concealment: RiskOfBiasItem,
    /// Baseline outcome measurements (selection).
    pub baseline_outcome_measurements: RiskOfBiasItem,
    /// Baseline characteristics (selection).
    pub baseline_characteristics: RiskOfBiasItem,
    /// Blinding of participants and personnel (performance).
    pub blinding_participants_personnel: RiskOfBiasItem,
    /// Blinding of outcome assessment, subjective outcomes (detection).
    pub blinding_outcome_assessment_subjective: RiskOfBiasItem,
    /// Blinding of outcome assessment, objective outcomes (detection).
    pub blinding_outcome_assessment_objective: RiskOfBiasItem,
    /// Incomplete outcome data (attrition).
    pub incomplete_outcome_data: RiskOfBiasItem,
    /// Selective reporting (reporting).
    pub selective_reporting: RiskOfBiasItem,
}

/// The nine field names, in Cochrane table order.
pub const ROB_DOMAINS: [&str; 9] = [
    "random_sequence_generation",
    "allocation_concealment",
    "baseline_outcome_measurements",
    "baseline_characteristics",
    "blinding_participants_personnel",
    "blinding_outcome_assessment_subjective",
    "blinding_outcome_assessment_objective",
    "incomplete_outcome_data",
    "selective_reporting",
];

impl CochraneRiskOfBias {
    /// The domains as a list, in Cochrane table order.
    #[must_use]
    pub fn to_list(&self) -> Vec<&RiskOfBiasItem> {
        vec![
            &self.random_sequence_generation,
            &self.allocation_concealment,
            &self.baseline_outcome_measurements,
            &self.baseline_characteristics,
            &self.blinding_participants_personnel,
            &self.blinding_outcome_assessment_subjective,
            &self.blinding_outcome_assessment_objective,
            &self.incomplete_outcome_data,
            &self.selective_reporting,
        ]
    }

    /// Serialise all nine domains to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        let mut d = serde_json::Map::new();
        for (name, item) in ROB_DOMAINS.iter().zip(self.to_list()) {
            d.insert((*name).into(), item.to_json());
        }
        serde_json::Value::Object(d)
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// Naming the first domain that is absent or malformed.
    pub fn from_json(data: &serde_json::Value) -> Result<Self, String> {
        let mut items: Vec<RiskOfBiasItem> = Vec::with_capacity(9);
        for name in ROB_DOMAINS {
            let item = data
                .get(name)
                .ok_or_else(|| format!("CochraneRiskOfBias: missing domain {name:?}"))?;
            items.push(RiskOfBiasItem::from_json(item)?);
        }
        let mut it = items.into_iter();
        Ok(CochraneRiskOfBias {
            random_sequence_generation: it.next().expect("9 items"),
            allocation_concealment: it.next().expect("9 items"),
            baseline_outcome_measurements: it.next().expect("9 items"),
            baseline_characteristics: it.next().expect("9 items"),
            blinding_participants_personnel: it.next().expect("9 items"),
            blinding_outcome_assessment_subjective: it.next().expect("9 items"),
            blinding_outcome_assessment_objective: it.next().expect("9 items"),
            incomplete_outcome_data: it.next().expect("9 items"),
            selective_reporting: it.next().expect("9 items"),
        })
    }

    /// Count the domains by judgement, as `{"Low risk": n, "High risk": n,
    /// "Unclear risk": n}`.
    ///
    /// **All three keys are always present**, including at zero — a caller
    /// rendering a summary needs the zeros. An item whose judgement is none of
    /// the three is **skipped**, not counted under its own key: Python's
    /// `if item.judgement in counts` guard is what makes a malformed judgement
    /// invisible here, and a port that grouped by the raw value would add a
    /// fourth key and quietly change what a total means.
    #[must_use]
    pub fn summary_counts(&self) -> std::collections::BTreeMap<String, usize> {
        let mut counts = std::collections::BTreeMap::new();
        for key in VALID_ROB_JUDGEMENTS {
            counts.insert(key.to_string(), 0);
        }
        for item in self.to_list() {
            if let Some(entry) = counts.get_mut(&item.judgement) {
                *entry += 1;
            }
        }
        counts
    }
}

/// Participants section of the Cochrane study-characteristics table.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CochraneParticipants {
    /// Where the study took place.
    pub setting: String,
    /// The population studied.
    pub population: String,
    /// Inclusion criteria.
    pub inclusion_criteria: Option<Vec<String>>,
    /// Exclusion criteria.
    pub exclusion_criteria: Option<Vec<String>>,
    /// Total participants, when reported.
    pub total_participants: Option<i64>,
    /// Participants per group, when reported.
    pub group_sizes: Option<serde_json::Value>,
    /// Whether baseline characteristics were reported.
    pub baseline_characteristics_reported: bool,
}

impl CochraneParticipants {
    /// Build a participants section.
    #[must_use]
    pub fn new(setting: impl Into<String>, population: impl Into<String>) -> Self {
        CochraneParticipants {
            setting: setting.into(),
            population: population.into(),
            inclusion_criteria: None,
            exclusion_criteria: None,
            total_participants: None,
            group_sizes: None,
            baseline_characteristics_reported: false,
        }
    }

    /// Format for the characteristics table.
    #[must_use]
    pub fn format_for_table(&self) -> String {
        let mut lines = vec![
            format!("Setting: {}", self.setting),
            String::new(),
            self.population.clone(),
        ];

        if let Some(total) = self.total_participants.filter(|n| *n != 0) {
            match &self.group_sizes {
                Some(serde_json::Value::Object(groups)) if !groups.is_empty() => {
                    let group_str = groups
                        .iter()
                        .map(|(k, v)| {
                            let rendered = match v {
                                serde_json::Value::String(s) => s.clone(),
                                other => other.to_string(),
                            };
                            format!("{k}: {rendered}")
                        })
                        .collect::<Vec<_>>()
                        .join(", ");
                    lines.push(format!("N={total} ({group_str})"));
                }
                _ => lines.push(format!("N={total}")),
            }
        }

        lines.join("\n")
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "setting": self.setting,
            "population": self.population,
            "inclusion_criteria": self.inclusion_criteria,
            "exclusion_criteria": self.exclusion_criteria,
            "total_participants": self.total_participants,
            "group_sizes": self.group_sizes,
            "baseline_characteristics_reported": self.baseline_characteristics_reported,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    #[must_use]
    pub fn from_json(data: &serde_json::Value) -> Self {
        CochraneParticipants {
            // Python defaults a missing setting or population to
            // "Not reported", not to the empty string.
            setting: string_or(data, "setting", "Not reported"),
            population: string_or(data, "population", "Not reported"),
            inclusion_criteria: string_vec(data, "inclusion_criteria"),
            exclusion_criteria: string_vec(data, "exclusion_criteria"),
            total_participants: data
                .get("total_participants")
                .and_then(serde_json::Value::as_i64),
            group_sizes: data.get("group_sizes").filter(|v| !v.is_null()).cloned(),
            baseline_characteristics_reported: data
                .get("baseline_characteristics_reported")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
        }
    }
}

/// Interventions section of the Cochrane study-characteristics table.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CochraneInterventions {
    /// Description of the intervention.
    pub description: String,
    /// The intervention arms.
    pub intervention_groups: Option<Vec<String>>,
    /// The control condition.
    pub control_description: Option<String>,
    /// How long the intervention ran.
    pub duration: Option<String>,
    /// Where it ran.
    pub setting: Option<String>,
}

impl CochraneInterventions {
    /// Build an interventions section from its description alone.
    #[must_use]
    pub fn new(description: impl Into<String>) -> Self {
        CochraneInterventions {
            description: description.into(),
            intervention_groups: None,
            control_description: None,
            duration: None,
            setting: None,
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "description": self.description,
            "intervention_groups": self.intervention_groups,
            "control_description": self.control_description,
            "duration": self.duration,
            "setting": self.setting,
        })
    }

    /// Deserialise from [`Self::to_json`] output, defaulting a missing
    /// description to `"Not reported"`.
    #[must_use]
    pub fn from_json(data: &serde_json::Value) -> Self {
        CochraneInterventions {
            description: string_or(data, "description", "Not reported"),
            intervention_groups: string_vec(data, "intervention_groups"),
            control_description: optional_string(data, "control_description"),
            duration: optional_string(data, "duration"),
            setting: optional_string(data, "setting"),
        }
    }
}

/// Outcomes section of the Cochrane study-characteristics table.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CochraneOutcomes {
    /// Description of the outcomes.
    pub description: String,
    /// Primary outcomes.
    pub primary_outcomes: Option<Vec<String>>,
    /// Secondary outcomes.
    pub secondary_outcomes: Option<Vec<String>>,
    /// When outcomes were measured.
    pub outcome_timepoints: Option<Vec<String>>,
    /// How outcomes were assessed.
    pub outcome_assessment_methods: Option<Vec<String>>,
}

impl CochraneOutcomes {
    /// Build an outcomes section from its description alone.
    #[must_use]
    pub fn new(description: impl Into<String>) -> Self {
        CochraneOutcomes {
            description: description.into(),
            primary_outcomes: None,
            secondary_outcomes: None,
            outcome_timepoints: None,
            outcome_assessment_methods: None,
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "description": self.description,
            "primary_outcomes": self.primary_outcomes,
            "secondary_outcomes": self.secondary_outcomes,
            "outcome_timepoints": self.outcome_timepoints,
            "outcome_assessment_methods": self.outcome_assessment_methods,
        })
    }

    /// Deserialise from [`Self::to_json`] output, defaulting a missing
    /// description to `"Not reported"`.
    #[must_use]
    pub fn from_json(data: &serde_json::Value) -> Self {
        CochraneOutcomes {
            description: string_or(data, "description", "Not reported"),
            primary_outcomes: string_vec(data, "primary_outcomes"),
            secondary_outcomes: string_vec(data, "secondary_outcomes"),
            outcome_timepoints: string_vec(data, "outcome_timepoints"),
            outcome_assessment_methods: string_vec(data, "outcome_assessment_methods"),
        }
    }
}

/// Notes section of the Cochrane study-characteristics table.
///
/// Captures follow-up, funding, conflicts of interest, ethics and trial
/// registration — the transparency-relevant metadata Cochrane requires.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct CochraneNotes {
    /// Follow-up periods.
    pub follow_up_periods: Option<Vec<String>>,
    /// Who funded it.
    pub funding_source: Option<String>,
    /// Declared conflicts.
    pub conflicts_of_interest: Option<String>,
    /// Ethical approval.
    pub ethical_approval: Option<String>,
    /// Trial registration.
    pub trial_registration: Option<String>,
    /// Publication status.
    pub publication_status: Option<String>,
    /// Anything else.
    pub additional_notes: Option<Vec<String>>,
}

impl CochraneNotes {
    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "follow_up_periods": self.follow_up_periods,
            "funding_source": self.funding_source,
            "conflicts_of_interest": self.conflicts_of_interest,
            "ethical_approval": self.ethical_approval,
            "trial_registration": self.trial_registration,
            "publication_status": self.publication_status,
            "additional_notes": self.additional_notes,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    #[must_use]
    pub fn from_json(data: &serde_json::Value) -> Self {
        CochraneNotes {
            follow_up_periods: string_vec(data, "follow_up_periods"),
            funding_source: optional_string(data, "funding_source"),
            conflicts_of_interest: optional_string(data, "conflicts_of_interest"),
            ethical_approval: optional_string(data, "ethical_approval"),
            trial_registration: optional_string(data, "trial_registration"),
            publication_status: optional_string(data, "publication_status"),
            additional_notes: string_vec(data, "additional_notes"),
        }
    }

    /// Format for the characteristics table.
    ///
    /// Blocks are joined with a **blank line** (`"\n\n"`) and the result is
    /// `"No additional notes"` when nothing is present — which is what the
    /// Markdown renderer's split-on-blank-line relies on.
    #[must_use]
    pub fn format_for_table(&self) -> String {
        let mut lines: Vec<String> = Vec::new();

        if let Some(periods) = self.follow_up_periods.as_ref().filter(|v| !v.is_empty()) {
            lines.push(format!("Follow-up at {}", periods.join(", ")));
        }
        for (label, value) in [
            ("Funding", &self.funding_source),
            ("Conflicts of interest", &self.conflicts_of_interest),
            ("Ethical approval", &self.ethical_approval),
            ("Trial registration", &self.trial_registration),
            ("Publication status", &self.publication_status),
        ] {
            if let Some(value) = value.as_ref().filter(|s| !s.is_empty()) {
                lines.push(format!("{label}: {value}"));
            }
        }
        if let Some(notes) = self.additional_notes.as_ref().filter(|v| !v.is_empty()) {
            lines.extend(notes.iter().cloned());
        }

        if lines.is_empty() {
            return "No additional notes".to_string();
        }
        lines.join("\n\n")
    }
}

/// The complete Cochrane study-characteristics table for one study.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CochraneStudyCharacteristics {
    /// The study identifier.
    pub study_id: String,
    /// Methods section.
    pub methods: String,
    /// Participants section.
    pub participants: CochraneParticipants,
    /// Interventions section.
    pub interventions: CochraneInterventions,
    /// Outcomes section.
    pub outcomes: CochraneOutcomes,
    /// Notes section.
    pub notes: CochraneNotes,
    /// The source document's id, when known.
    pub document_id: Option<i64>,
    /// The source document's title, when known.
    pub document_title: Option<String>,
    /// PubMed id, when known.
    pub pmid: Option<String>,
    /// DOI, when known.
    pub doi: Option<String>,
    /// When the record was created, ISO 8601.
    ///
    /// Python stamps `datetime.now(UTC)` in `__post_init__` when none was
    /// given. Kept as a string here rather than a typed instant: the field's
    /// only job is to round-trip through `to_dict`/`from_dict`, and a string
    /// does that without pinning the crate to a timestamp library.
    pub created_at: Option<String>,
}

impl CochraneStudyCharacteristics {
    /// Build from the five sections.
    ///
    /// `created_at` is left `None`, **not** stamped with the current time as
    /// Python's `__post_init__` does. Two reasons, and the honest one is the
    /// second: this port has no clock dependency, and a value whose only
    /// consumer is `isoformat()` is not worth one; and a wall-clock default
    /// makes every `to_json` comparison in the oracle nondeterministic, which
    /// would hide real divergences behind a timestamp nobody reads. A caller
    /// that wants the stamp sets it.
    #[must_use]
    pub fn new(
        study_id: impl Into<String>,
        methods: impl Into<String>,
        participants: CochraneParticipants,
        interventions: CochraneInterventions,
        outcomes: CochraneOutcomes,
        notes: CochraneNotes,
    ) -> Self {
        CochraneStudyCharacteristics {
            study_id: study_id.into(),
            methods: methods.into(),
            participants,
            interventions,
            outcomes,
            notes,
            document_id: None,
            document_title: None,
            pmid: None,
            doi: None,
            created_at: None,
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "study_id": self.study_id,
            "methods": self.methods,
            "participants": self.participants.to_json(),
            "interventions": self.interventions.to_json(),
            "outcomes": self.outcomes.to_json(),
            "notes": self.notes.to_json(),
            "document_id": self.document_id,
            "document_title": self.document_title,
            "pmid": self.pmid,
            "doi": self.doi,
            "created_at": self.created_at,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// **DEFECT-FIX (#310) — lenient reads.** Python reads `study_id`,
    /// `methods` and the four sections by direct index while the five optional
    /// fields beside them use `.get()` — in one method, over one dict, with no
    /// rule separating them. That makes a `cochrane_assessment` written as a
    /// partial dict unreadable, even though `QualityAssessment.cochrane_assessment`
    /// is typed `Any` and its `to_dict` goes out of its way to tolerate exactly
    /// that shape. All six now default the way their siblings do: an absent text
    /// field reads `"Not reported"`, and an absent section reads as its own empty
    /// default (which `CochraneParticipants::from_json` and friends already give
    /// a missing key). Infallible, because no field is required any more.
    #[must_use]
    pub fn from_json(data: &serde_json::Value) -> Self {
        let null = serde_json::Value::Null;
        let section = |key: &str| data.get(key).unwrap_or(&null);
        CochraneStudyCharacteristics {
            study_id: string_or(data, "study_id", "Not reported"),
            methods: string_or(data, "methods", "Not reported"),
            participants: CochraneParticipants::from_json(section("participants")),
            interventions: CochraneInterventions::from_json(section("interventions")),
            outcomes: CochraneOutcomes::from_json(section("outcomes")),
            notes: CochraneNotes::from_json(section("notes")),
            document_id: data.get("document_id").and_then(serde_json::Value::as_i64),
            document_title: optional_string(data, "document_title"),
            pmid: optional_string(data, "pmid"),
            doi: optional_string(data, "doi"),
            created_at: optional_string(data, "created_at"),
        }
    }
}

/// The assessment version this port writes.
pub const ASSESSMENT_VERSION: &str = "2.0.0";

/// A complete Cochrane-aligned study assessment.
///
/// `PartialEq` but not `Eq`: it carries two `f64` scores.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CochraneStudyAssessment {
    /// The characteristics table.
    pub study_characteristics: CochraneStudyCharacteristics,
    /// The nine-domain risk of bias.
    pub risk_of_bias: CochraneRiskOfBias,
    /// 0–10, when scored.
    pub overall_quality_score: Option<f64>,
    /// 0–1, when stated.
    pub overall_confidence: Option<f64>,
    /// e.g. `"Level 2 (moderate-high)"`.
    pub evidence_level: Option<String>,
    /// Free-text notes.
    pub assessment_notes: Option<Vec<String>>,
    /// The assessment schema version.
    pub assessment_version: String,
    /// The original character count when the text was condensed before
    /// assessment; `None` when the paper went to the model whole.
    ///
    /// A judgement made over an LLM-condensed digest is weaker evidence than
    /// one made over the paper, so it says so rather than leaving the caller to
    /// infer it.
    pub condensed_from_chars: Option<i64>,
    /// The `ProcessingStatus` value the condensation pass finished with, when
    /// the text was condensed; `None` when it was not.
    ///
    /// A `"partial"` condensation means one or more extraction batches failed
    /// after retries, so whole sections of the paper are absent from the digest
    /// the nine-domain judgement ran over — a fact the model's own
    /// `overall_confidence` cannot know, since it is a property of the
    /// pipeline, not of the paper.
    pub condensation_status: Option<String>,
}

impl CochraneStudyAssessment {
    /// Build an assessment from its two required parts.
    #[must_use]
    pub fn new(
        study_characteristics: CochraneStudyCharacteristics,
        risk_of_bias: CochraneRiskOfBias,
    ) -> Self {
        CochraneStudyAssessment {
            study_characteristics,
            risk_of_bias,
            overall_quality_score: None,
            overall_confidence: None,
            evidence_level: None,
            assessment_notes: None,
            assessment_version: ASSESSMENT_VERSION.to_string(),
            condensed_from_chars: None,
            condensation_status: None,
        }
    }

    /// The study identifier, from the characteristics table.
    #[must_use]
    pub fn study_id(&self) -> &str {
        &self.study_characteristics.study_id
    }

    /// The document id, from the characteristics table, if any.
    #[must_use]
    pub fn document_id(&self) -> Option<i64> {
        self.study_characteristics.document_id
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "study_characteristics": self.study_characteristics.to_json(),
            "risk_of_bias": self.risk_of_bias.to_json(),
            "overall_quality_score": self.overall_quality_score,
            "overall_confidence": self.overall_confidence,
            "evidence_level": self.evidence_level,
            "assessment_notes": self.assessment_notes,
            "assessment_version": self.assessment_version,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// **DEFECT-FIX (#310) at this level** — both section keys are read leniently:
    /// Python indexes `study_characteristics` and `risk_of_bias`
    /// directly, where every other field beside them uses `.get()`. An absent
    /// characteristics section reads as an empty one and an absent risk-of-bias
    /// section as the all-`"Unclear risk"` default.
    ///
    /// # Errors
    ///
    /// Naming the first risk-of-bias domain that is absent or malformed, for a
    /// `risk_of_bias` that is present but not a complete nine-domain object.
    pub fn from_json(data: &serde_json::Value) -> Result<Self, String> {
        let characteristics = data
            .get("study_characteristics")
            .cloned()
            .unwrap_or(serde_json::Value::Null);
        let risk_of_bias = match data.get("risk_of_bias") {
            Some(value) => CochraneRiskOfBias::from_json(value)?,
            None => create_default_cochrane_risk_of_bias(),
        };
        Ok(CochraneStudyAssessment {
            study_characteristics: CochraneStudyCharacteristics::from_json(&characteristics),
            risk_of_bias,
            overall_quality_score: data
                .get("overall_quality_score")
                .and_then(serde_json::Value::as_f64),
            overall_confidence: data
                .get("overall_confidence")
                .and_then(serde_json::Value::as_f64),
            evidence_level: optional_string(data, "evidence_level"),
            assessment_notes: string_vec(data, "assessment_notes"),
            assessment_version: string_or(data, "assessment_version", ASSESSMENT_VERSION),
            condensed_from_chars: data
                .get("condensed_from_chars")
                .and_then(serde_json::Value::as_i64),
            condensation_status: optional_string(data, "condensation_status"),
        })
    }
}

/// An `"Unclear risk"` item, for when information is unavailable.
#[must_use]
pub fn create_default_risk_of_bias_item(
    domain: &str,
    bias_type: &str,
    outcome_type: Option<&str>,
) -> RiskOfBiasItem {
    RiskOfBiasItem::new(
        domain,
        bias_type,
        ROB_JUDGEMENT_UNCLEAR,
        "Not reported or insufficient information to assess",
        outcome_type.map(str::to_string),
    )
}

/// A RoB assessment with all nine domains set to `"Unclear risk"`.
#[must_use]
pub fn create_default_cochrane_risk_of_bias() -> CochraneRiskOfBias {
    CochraneRiskOfBias {
        random_sequence_generation: create_default_risk_of_bias_item(
            "Random sequence generation",
            "selection bias",
            None,
        ),
        allocation_concealment: create_default_risk_of_bias_item(
            "Allocation concealment",
            "selection bias",
            None,
        ),
        baseline_outcome_measurements: create_default_risk_of_bias_item(
            "Baseline outcome measurements",
            "selection bias",
            None,
        ),
        baseline_characteristics: create_default_risk_of_bias_item(
            "Baseline characteristics",
            "selection bias",
            None,
        ),
        blinding_participants_personnel: create_default_risk_of_bias_item(
            "Blinding of participants and personnel",
            "performance bias",
            None,
        ),
        blinding_outcome_assessment_subjective: create_default_risk_of_bias_item(
            "Blinding of outcome assessment (subjective outcomes)",
            "detection bias",
            Some("subjective"),
        ),
        blinding_outcome_assessment_objective: create_default_risk_of_bias_item(
            "Blinding of outcome assessment (objective outcomes)",
            "detection bias",
            Some("objective"),
        ),
        incomplete_outcome_data: create_default_risk_of_bias_item(
            "Incomplete outcome data",
            "attrition bias",
            None,
        ),
        selective_reporting: create_default_risk_of_bias_item(
            "Selective reporting",
            "reporting bias",
            None,
        ),
    }
}

/// Why a nine-domain assessment could not be collapsed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownBiasType {
    /// The domain whose item could not be placed.
    pub domain: String,
    /// The unrecognised `bias_type`.
    pub bias_type: String,
}

impl std::fmt::Display for UnknownBiasType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // `{!r}` on a Python str renders with single quotes where Rust's `{:?}`
        // renders with double, so the quoting is written out rather than
        // derived — the two messages are compared verbatim by the oracle.
        // The category list is in Python's `sorted()` order for the same
        // reason.
        write!(
            f,
            "Cannot collapse domain '{}': unknown bias_type '{}'. Expected one of \
             ['attrition bias', 'detection bias', 'performance bias', \
             'reporting bias', 'selection bias'].",
            self.domain, self.bias_type
        )
    }
}

impl std::error::Error for UnknownBiasType {}

/// Reduce the nine Cochrane domains to the five [`BiasRisk`] domains.
///
/// Each [`RiskOfBiasItem`] already names its target through `bias_type`, so the
/// grouping is derived rather than hard-coded: four selection domains collapse
/// onto one field, two detection domains onto another, and a field left unset
/// keeps [`BiasRisk`]'s `"unclear"` default.
///
/// The worst rank per field wins. `unclear` outranks `low`, so one unreported
/// selection domain promotes the field to `"unclear"` even when the other three
/// are `"low"` — an unreported domain is not a clean bill of health.
///
/// # Errors
///
/// When an item's `bias_type` is not one of the five categories. Silently
/// dropping it would return a [`BiasRisk`] that looks complete and is not.
pub fn collapse_risk_of_bias(rob: &CochraneRiskOfBias) -> Result<BiasRisk, UnknownBiasType> {
    let mut worst: std::collections::BTreeMap<&'static str, usize> =
        std::collections::BTreeMap::new();

    for item in rob.to_list() {
        let target = bias_type_to_field(&item.bias_type).ok_or_else(|| UnknownBiasType {
            domain: item.domain.clone(),
            bias_type: item.bias_type.clone(),
        })?;
        let judgement = RiskOfBiasJudgement::from_string(&item.judgement).as_str();
        let word = judgement_to_bias_risk(judgement);
        let rank = SEVERITY_ORDER
            .iter()
            .position(|s| *s == word)
            .expect("every judgement maps to a severity word");

        let entry = worst.entry(target).or_insert(0);
        if rank > *entry {
            *entry = rank;
        }
    }

    let mut bias = BiasRisk::default();
    for (target, rank) in worst {
        let word = SEVERITY_ORDER[rank].to_string();
        match target {
            "selection" => bias.selection = word,
            "performance" => bias.performance = word,
            "detection" => bias.detection = word,
            "attrition" => bias.attrition = word,
            "reporting" => bias.reporting = word,
            _ => unreachable!("bias_type_to_field returns only the five fields"),
        }
    }
    Ok(bias)
}

// --- small JSON helpers, kept here so the six models read alike ------------

fn optional_string(data: &serde_json::Value, key: &str) -> Option<String> {
    data.get(key)
        .and_then(serde_json::Value::as_str)
        .map(str::to_string)
}

fn string_or(data: &serde_json::Value, key: &str, default: &str) -> String {
    optional_string(data, key).unwrap_or_else(|| default.to_string())
}

fn string_vec(data: &serde_json::Value, key: &str) -> Option<Vec<String>> {
    data.get(key)
        .and_then(serde_json::Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(serde_json::Value::as_str)
                .map(str::to_string)
                .collect()
        })
}
