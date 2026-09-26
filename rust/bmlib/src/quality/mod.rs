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

//! Tiered quality assessment.
//!
//! A port of `bmlib/quality/` (3,768 Python lines).
//!
//! | Python | Here | Status |
//! |---|---|---|
//! | `quality/scoring_models.py` | [`scoring_models`] | ported |
//! | `quality/data_models.py` | [`data_models`] | ported |
//! | `quality/cochrane_models.py` | [`cochrane_models`] | ported |
//! | `quality/cochrane_formatter.py` | [`cochrane_formatter`] | ported |
//! | `quality/extractors.py` | [`extractors`] | ported (fixes #294, #297, #298) |
//! | `quality/manager.py` | [`manager`] | ported |
//! | `quality/metadata_filter.py` | [`metadata_filter`] | ported (Tier 1) |
//! | the two `_parse_data` halves | [`llm_parsers`] | ported (fixes #295) |
//! | `quality/study_classifier.py` | [`study_classifier`] | ported (Tier 2) |
//! | `quality/quality_agent.py` | [`quality_agent`] | ported (Tier 3) |
//! | `quality/cochrane_assessor.py` | [`cochrane_assessor`] | ported (Tier 4) |
//!
//! The three LLM tiers do not own a client. Each takes a
//! [`agent_chat::JsonChat`] — the message-carrying form of
//! [`crate::agents::ChatSource`] — so the retry, truncation and
//! `require_dict` rules stay in [`crate::agents::chat_json`] and the tiers are
//! driven by a scripted source in a test. Their answer **readers** are pure
//! functions ([`llm_parsers`] for tiers 2 and 3,
//! [`cochrane_assessor::parse_cochrane_assessment`] for Tier 4), which is what
//! the differential oracle pins.
//!
//! The rule-based extractors and the Cochrane formatters are **standalone** in
//! the Python library too: nothing in the tiered pipeline imports them, and
//! there is no conversion between [`scoring_models::DimensionScore`] and
//! `QualityAssessment`. Wiring the extractors in as a free pre-filter ahead of
//! Tier 1 is open work in the Python repo; this port mirrors the current state
//! rather than pre-empting that decision.
//!
//! `cochrane_assessor._condense`'s map-reduce half is not ported, because the
//! `LLMChunkProcessor` it runs is not; the assessor takes a
//! [`cochrane_assessor::Condenser`] and keeps the rules the Python applies to
//! its output.

pub mod agent_chat;
pub mod cochrane_assessor;
pub mod cochrane_formatter;
pub mod cochrane_models;
pub mod data_models;
pub mod extractors;
pub mod llm_parsers;
pub mod manager;
pub mod metadata_filter;
pub mod quality_agent;
pub mod scoring_models;
pub mod study_classifier;

pub use agent_chat::{format_template, JsonChat, LlmChat};
pub use cochrane_assessor::{
    parse_cochrane_assessment, render_condense_consolidation, render_condense_extraction,
    AssessOptions, CochraneAssessor, CochraneStats, Condenser, StudyInput,
    COCHRANE_RESPONSE_FORMAT, COCHRANE_SYSTEM_PROMPT, COCHRANE_TASK_PROMPT,
    CONDENSE_CONSOLIDATION_PROMPT, CONDENSE_EXTRACTION_PROMPT, CONDENSE_QUERY,
    DEFAULT_CONDENSE_THRESHOLD_CHARS,
};
pub use cochrane_formatter::{
    domain_label, escape_html, format_complete_assessment_markdown,
    format_multiple_assessments_markdown, format_risk_of_bias_html, format_risk_of_bias_markdown,
    format_risk_of_bias_summary_markdown, format_study_characteristics_html,
    format_study_characteristics_markdown, get_cochrane_css, judgement_css_class, judgement_symbol,
    COCHRANE_CSS, MD_BOLD_END, MD_BOLD_START, MD_ITALIC_END, MD_ITALIC_START,
};
pub use cochrane_models::{
    bias_type_to_field, collapse_risk_of_bias, create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item, judgement_to_bias_risk, CochraneInterventions, CochraneNotes,
    CochraneOutcomes, CochraneParticipants, CochraneRiskOfBias, CochraneStudyAssessment,
    CochraneStudyCharacteristics, RiskOfBiasItem, RiskOfBiasJudgement, UnknownBiasType,
    ASSESSMENT_VERSION, ROB_DOMAINS, ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_LOW, ROB_JUDGEMENT_UNCLEAR,
    SEVERITY_ORDER, VALID_ROB_JUDGEMENTS,
};
pub use data_models::{
    design_to_randomized, design_to_score, design_to_tier, designs_per_tier, study_design_from_str,
    BiasRisk, QualityAssessment, QualityFilter, QualityTier, StudyDesign, VALID_BIAS_VALUES,
};
pub use extractors::{
    calculate_sample_size_score, extract_sample_size_dimension, extract_study_type,
    extract_text_context, find_power_calc_context, find_sample_size, get_extracted_sample_size,
    get_extracted_study_type, has_ci_reporting, has_exclusion_pattern, has_power_calculation,
    is_negated, iter_keyword_positions, parse_number, prepare_extractor_search_text,
    DEFAULT_STUDY_TYPE_HIERARCHY, DEFAULT_STUDY_TYPE_KEYWORDS, EXCLUSION_CONTEXT_WINDOW,
    NEGATION_CONTEXT_WINDOW, NEGATION_WORDS, NUMBER, SAMPLE_SIZE_PATTERNS, STUDY_TYPE_EXCLUSIONS,
    STUDY_TYPE_PRIORITY,
};
pub use quality_agent::{QualityAgent, ASSESSMENT_SYSTEM_PROMPT, ASSESSMENT_USER_TEMPLATE};
pub use scoring_models::{
    AssessmentDetail, DimensionScore, ALL_DIMENSIONS, DIMENSION_METHODOLOGICAL_QUALITY,
    DIMENSION_REPLICATION_STATUS, DIMENSION_RISK_OF_BIAS, DIMENSION_SAMPLE_SIZE,
    DIMENSION_STUDY_DESIGN,
};
pub use study_classifier::{StudyClassifier, CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_TEMPLATE};
