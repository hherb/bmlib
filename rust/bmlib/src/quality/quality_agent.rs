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

//! Tier 3: deep methodological quality assessment.
//!
//! A port of `bmlib/quality/quality_agent.py`. Uses a more capable model (e.g.
//! Sonnet) for comprehensive assessment including bias risk, strengths and
//! limitations.
//!
//! Cost: ~$0.003 per document. Use selectively — only when detailed assessment
//! is explicitly requested.
//!
//! As in [`crate::quality::study_classifier`], the client is a
//! [`JsonChat`] rather than a `BaseAgent`, and the reading of the answer is
//! [`parse_assessment`] — the same function the Tier 3 oracle pins, including
//! its three fixes for [#295](https://github.com/hherb/bmlib/issues/295).

use crate::llm::LLMMessage;
use crate::quality::agent_chat::{format_template, JsonChat};
use crate::quality::data_models::QualityAssessment;
use crate::quality::llm_parsers::parse_assessment;

/// Maximum abstract length sent for deep assessment (characters).
pub const MAX_ABSTRACT_CHARS: usize = 4000;

/// The system prompt, copied byte-for-byte from the Python.
pub const ASSESSMENT_SYSTEM_PROMPT: &str = "\
You are a research quality assessment expert.
Evaluate the methodological quality of biomedical research papers.

CRITICAL RULES:
1. Extract ONLY information that is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. If information is unclear or not mentioned, use null or \"unclear\"
4. Focus on THIS study's methodology, not studies it references
5. Return ONLY valid JSON, no explanation";

/// The user template, copied byte-for-byte from the Python.
///
/// Braces are **doubled** as the Python spells them; `str.format` collapses them,
/// so the prompt the model receives has single braces. See
/// [`format_template`].
pub const ASSESSMENT_USER_TEMPLATE: &str = "\
Assess this research paper's methodological quality:

Title: {title}
Abstract: {abstract}

Return JSON:
{{
    \"study_design\": \"<see list below>\",
    \"quality_score\": <1-10>,
    \"evidence_level\": \"1a|1b|2a|2b|3a|3b|4|5|null\",
    \"design_characteristics\": {{
        \"randomized\": true|false|null,
        \"controlled\": true|false|null,
        \"blinded\": \"none\"|\"single\"|\"double\"|\"triple\"|null,
        \"prospective\": true|false|null,
        \"multicenter\": true|false|null
    }},
    \"sample_size\": <number or null>,
    \"bias_risk\": {{
        \"selection\": \"low\"|\"unclear\"|\"high\",
        \"performance\": \"low\"|\"unclear\"|\"high\",
        \"detection\": \"low\"|\"unclear\"|\"high\",
        \"attrition\": \"low\"|\"unclear\"|\"high\",
        \"reporting\": \"low\"|\"unclear\"|\"high\"
    }},
    \"strengths\": [\"2-3 methodological strengths\"],
    \"limitations\": [\"2-3 methodological limitations\"],
    \"confidence\": <0.0 to 1.0>
}}

Valid study_design values: systematic_review, meta_analysis, rct,
cohort_prospective, cohort_retrospective, case_control,
cross_sectional, case_series, case_report, editorial,
letter, guideline, other.

Focus on THIS study's methodology, not studies it references.";

/// The default sampling temperature, as the Python's `__init__` sets it.
pub const DEFAULT_TEMPERATURE: f64 = 0.2;

/// The default output ceiling, as the Python's `__init__` sets it.
pub const DEFAULT_MAX_TOKENS: i64 = 1024;

/// `BaseAgent.chat_json`'s own default, which the Python tiers do not override.
pub const DEFAULT_MAX_RETRIES: usize = 3;

/// Tier 3 deep quality assessor.
///
/// As with [`crate::quality::study_classifier::StudyClassifier`], the sampling
/// defaults live here rather than at the call site so they hold however the
/// agent is constructed. The Python's `template_engine` parameter is left out:
/// both prompts are module constants and it is never consulted.
pub struct QualityAgent<'a> {
    chat: &'a mut dyn JsonChat,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
}

impl<'a> QualityAgent<'a> {
    /// An assessor over `chat`, with the Python's sampling defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        QualityAgent {
            chat,
            temperature: DEFAULT_TEMPERATURE,
            max_tokens: DEFAULT_MAX_TOKENS,
            max_retries: DEFAULT_MAX_RETRIES,
        }
    }

    /// Override the sampling temperature.
    #[must_use]
    pub fn with_temperature(mut self, temperature: f64) -> Self {
        self.temperature = temperature;
        self
    }

    /// Override the output ceiling.
    #[must_use]
    pub fn with_max_tokens(mut self, max_tokens: i64) -> Self {
        self.max_tokens = max_tokens;
        self
    }

    /// Override the retry budget.
    #[must_use]
    pub fn with_max_retries(mut self, max_retries: usize) -> Self {
        self.max_retries = max_retries;
        self
    }

    /// Perform detailed quality assessment.
    ///
    /// As in the Tier 2 classifier, either field may be `None`: a gap is
    /// something to work around, not a reason to abort the caller's batch. With
    /// both missing there is nothing to assess and no LLM call is made.
    ///
    /// Returns a Tier 3 [`QualityAssessment`]. On failure, returns
    /// [`QualityAssessment::unclassified`].
    pub fn assess(
        &mut self,
        title: Option<&str>,
        abstract_text: Option<&str>,
    ) -> QualityAssessment {
        let title = title.unwrap_or("");
        let abstract_text = abstract_text.unwrap_or("");
        // // QUIRK: as in Tier 2, the emptiness guard strips and the prompt does
        // not — a padded title reaches the model padded, which is what the Python
        // sends.
        if title.trim().is_empty() && abstract_text.trim().is_empty() {
            // Left to itself the model would return fully-formed strengths and
            // limitations for a paper it was told nothing about.
            return QualityAssessment::unclassified();
        }

        let abstract_text: String = abstract_text.chars().take(MAX_ABSTRACT_CHARS).collect();
        let prompt = format_template(
            ASSESSMENT_USER_TEMPLATE,
            &[("title", title), ("abstract", &abstract_text)],
        );
        let messages = [
            LLMMessage::system(ASSESSMENT_SYSTEM_PROMPT),
            LLMMessage::user(prompt),
        ];

        match self.chat.chat_json(
            &messages,
            self.temperature,
            self.max_tokens,
            self.max_retries,
            // `_parse_data()` calls `.get()`: a top-level array would raise into
            // the handler below and degrade the paper to UNCLASSIFIED without
            // ever retrying.
            true,
        ) {
            Ok(outcome) => parse_assessment(&outcome.value),
            // The Python's `except Exception`, and it costs more here than in
            // Tier 2: `QualityManager` lets Tier 3 **replace** Tier 1, so a
            // paper the metadata classified conclusively as an RCT comes back
            // UNKNOWN at score 0. That is the Python's behaviour and the port
            // keeps it; only the null-field crashes are repaired, in
            // `llm_parsers`.
            Err(_) => QualityAssessment::unclassified(),
        }
    }
}
