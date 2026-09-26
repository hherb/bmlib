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

//! Tier 2: LLM-based study-design classification.
//!
//! A port of `bmlib/quality/study_classifier.py`. Uses a cheap/fast model (e.g.
//! Haiku, or a local model via Ollama) to classify study design from title +
//! abstract. Cost: ~$0.001 per document.
//!
//! # What the port does differently, and why
//!
//! The Python is a `BaseAgent` subclass holding an `LLMClient`; here the client
//! is a [`JsonChat`] the caller supplies, so the tier runs against a scripted
//! source in a test. The reading of the model's answer is not restated either:
//! it is [`parse_classification`], the same pure function the Tier 2 oracle
//! pins — including its fix for
//! [#295](https://github.com/hherb/bmlib/issues/295), where a key present with
//! JSON `null` used to raise out of `_parse_data` and degrade the paper.

use crate::llm::LLMMessage;
use crate::quality::agent_chat::{format_template, JsonChat};
use crate::quality::data_models::QualityAssessment;
use crate::quality::llm_parsers::parse_classification;

/// Maximum abstract length sent to the classifier (characters).
pub const MAX_ABSTRACT_CHARS: usize = 3000;

/// The system prompt, copied byte-for-byte from the Python.
pub const CLASSIFIER_SYSTEM_PROMPT: &str = "\
You are a biomedical study design classifier.  Classify the paper's OWN
methodology — NOT the methodology of studies it references.

Focus on language like \"this study\", \"we conducted\", \"our analysis\".
Ignore phrases like \"previous studies have shown\" or \"a recent meta-analysis found\".

Return ONLY valid JSON, no explanation.";

/// The user template, copied byte-for-byte from the Python.
///
/// Braces are **doubled** as the Python spells them; `str.format` collapses them,
/// so the prompt the model receives has single braces. See
/// [`format_template`].
pub const CLASSIFIER_USER_TEMPLATE: &str = "\
Classify this paper's study design:

Title: {title}
Abstract: {abstract}

Return JSON:
{{
    \"study_design\": \"<see list below>\",
    \"confidence\": <0.0 to 1.0>,
    \"sample_size\": <number or null>,
    \"blinding\": \"none|single|double|triple|null\"
}}

Valid study_design values: systematic_review, meta_analysis, rct,
cohort_prospective, cohort_retrospective, case_control,
cross_sectional, case_series, case_report, editorial,
letter, guideline, other, unknown.";

/// The sampling defaults the Python's `StudyClassifier.__init__` carries.
///
/// They live here rather than at the call site so that they hold however the
/// classifier is constructed: a low temperature because this returns a fixed
/// JSON shape, and a budget well above the ~50 tokens that shape needs because
/// small local models preface it with commentary that would otherwise be
/// truncated along with the JSON.
pub const DEFAULT_TEMPERATURE: f64 = 0.1;

/// The default output ceiling — see [`DEFAULT_TEMPERATURE`].
pub const DEFAULT_MAX_TOKENS: i64 = 1024;

/// `BaseAgent.chat_json`'s own default, which the Python tiers do not override.
pub const DEFAULT_MAX_RETRIES: usize = 3;

/// Tier 2 study-design classifier using a cheap LLM.
///
/// The Python also takes a `TemplateEngine`; this classifier never consults it —
/// both prompts are module constants — so the port leaves it out rather than
/// carrying an inert parameter.
pub struct StudyClassifier<'a> {
    chat: &'a mut dyn JsonChat,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
}

impl<'a> StudyClassifier<'a> {
    /// A classifier over `chat`, with the Python's sampling defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        StudyClassifier {
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

    /// Classify study design from title and abstract.
    ///
    /// Either field may be `None` — sources omit abstracts often enough, and a
    /// nullable database column delivers the gap that way. Classifying from the
    /// title alone is weak but honest, and raising would abort the caller's whole
    /// batch. With **both** missing there is nothing to classify, so no LLM call
    /// is made.
    ///
    /// Returns a Tier 2 [`QualityAssessment`]. On failure, returns
    /// [`QualityAssessment::unclassified`].
    pub fn classify(
        &mut self,
        title: Option<&str>,
        abstract_text: Option<&str>,
    ) -> QualityAssessment {
        let title = title.unwrap_or("");
        let abstract_text = abstract_text.unwrap_or("");
        // // QUIRK: the emptiness guard **strips** and the prompt does not. A
        // title of `"  A trial  "` is sent to the model padded, exactly as the
        // Python sends it (`title = title or ""`, then `.strip()` only inside the
        // guard). Reproduced rather than tidied because the prompt is part of the
        // behaviour, and stripping here would put different bytes in front of the
        // model than the Python put there.
        if title.trim().is_empty() && abstract_text.trim().is_empty() {
            // An empty prompt does not yield an empty answer — the model invents
            // a plausible design, and nothing downstream can tell that apart from
            // a real classification. Refusing is the honest result.
            return QualityAssessment::unclassified();
        }

        // `abstract[:MAX_ABSTRACT_CHARS]` is a **character** slice, not a byte
        // one: a byte cut would split a multi-byte character and the port has no
        // way to represent the resulting string.
        let abstract_text: String = abstract_text.chars().take(MAX_ABSTRACT_CHARS).collect();
        let prompt = format_template(
            CLASSIFIER_USER_TEMPLATE,
            &[("title", title), ("abstract", &abstract_text)],
        );
        let messages = [
            LLMMessage::system(CLASSIFIER_SYSTEM_PROMPT),
            LLMMessage::user(prompt),
        ];

        match self.chat.chat_json(
            &messages,
            self.temperature,
            self.max_tokens,
            self.max_retries,
            // `_parse_data()` calls `.get()`: a top-level array would raise into
            // the handler below and degrade the paper to UNCLASSIFIED without
            // ever retrying, so the shape is demanded up front.
            true,
        ) {
            Ok(outcome) => parse_classification(&outcome.value),
            // The Python's `except Exception` — a parse failure or a transport
            // failure degrades the paper rather than raising. `llm_parsers`
            // already makes the null-field parse failures unreachable, so what
            // remains here is transport and truncation. `QualityManager` lets
            // Tier 2 replace Tier 1, so this is a real loss of a conclusive
            // metadata classification, and it is the Python's.
            Err(_) => QualityAssessment::unclassified(),
        }
    }
}
