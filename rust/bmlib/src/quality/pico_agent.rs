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

//! PICO extraction with quotes verified against the paper.
//!
//! A port of `bmlib/quality/pico_agent.py` (StudyAnalyzer item 7; the design
//! is `docs/superpowers/specs/2026-10-30-pico-agent-design.md`).

use serde_json::Value;

use crate::agents::ChatJsonError;
use crate::citations::{find_passage, InvalidSimilarity};
use crate::context_processor::llm_processor::{ContextModel, PromptTemplates};
use crate::context_processor::ProcessingConfig;
use crate::llm::LLMMessage;
use crate::pyvalue::python_strip;
use crate::quality::agent_chat::JsonChat;
use crate::quality::cochrane_assessor::{
    clamped_confidence, condense_checked, Condenser, LlmCondenser,
    DEFAULT_CONDENSE_THRESHOLD_CHARS, DEFAULT_MAX_RETRIES, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE,
};
use crate::quality::pico_models::{
    PicoCitation, PicoError, PicoExtraction, PicoPart, PicoPartState, PicoSuitability,
};

/// How many times a question is asked when its reply parses but cannot be read
/// (so it is asked once more) — the Cochrane assessor's `ASSESSMENT_ATTEMPTS`.
const READ_ATTEMPTS: usize = 2;

/// The four parts, in the order they are read, reported and averaged.
pub const PICO_PARTS: [&str; 4] = ["population", "intervention", "comparison", "outcome"];

// --- Prompts: byte-identical to Python's; the oracle pins them ---

/// Python's `PICO_SYSTEM_PROMPT`.
pub const PICO_SYSTEM_PROMPT: &str = r##"You are an expert in clinical research methodology and evidence-based medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. Describe THIS study, not studies it references
4. Return ONLY valid JSON, no explanation"##;

/// Python's `SUITABILITY_TASK_PROMPT`.
pub const SUITABILITY_TASK_PROMPT: &str = r##"Determine whether the document below is an INTERVENTION STUDY suitable for PICO (Population, Intervention, Comparison, Outcome) extraction.

A document is SUITABLE if it is:
- A randomized controlled trial (RCT)
- A clinical trial with an intervention
- A cohort study with an intervention or exposure
- A case-control study
- A comparative effectiveness study
- Any primary research testing an intervention, treatment, or exposure

A document is NOT SUITABLE if it is:
- A systematic review or meta-analysis (it reviews other studies and does not test an intervention itself)
- A narrative review or literature review
- An editorial, commentary, or opinion piece
- A case report or case series (no comparison group)
- A cross-sectional survey (no intervention)
- A basic science or laboratory study (not clinical)
- A qualitative study without quantifiable outcomes

KEY INDICATORS of an intervention study:
- It tests a specific treatment, drug, procedure, or intervention
- It has a defined patient population or study participants
- It includes a control or comparison group (or a baseline comparison)
- It measures specific outcomes or endpoints
- It reports results from actual participants, not a review of literature

Assess:
1. is_intervention_study: does this study test an intervention, treatment or exposure on participants?
2. has_comparison: does it have a control or comparison group?
3. is_suitable: should PICO components be extracted? (true for an intervention study)
4. confidence: how confident you are in this assessment (0.0-1.0)
5. rationale: a brief explanation (2-3 sentences)
6. study_type: the type of study (e.g. "RCT", "cohort study", "systematic review", "case-control study")

Do not confuse a systematic review (which reviews other studies) with primary research. Look for evidence of actual participant enrollment and intervention. If uncertain, lean towards NOT SUITABLE and say why in the rationale."##;

/// Python's `SUITABILITY_RESPONSE_FORMAT`.
pub const SUITABILITY_RESPONSE_FORMAT: &str = r##"Respond with JSON in exactly this shape:

{
    "is_intervention_study": true,
    "has_comparison": true,
    "is_suitable": true,
    "confidence": 0.9,
    "rationale": "This is an RCT testing intervention X in population Y with control group Z.",
    "study_type": "randomized controlled trial"
}"##;

/// Python's `EXTRACTION_TASK_PROMPT`.
pub const EXTRACTION_TASK_PROMPT: &str = r##"Extract the PICO components (Population, Intervention, Comparison, Outcome) from the research paper below.

For EACH of the four components give:
- description: what the paper says, specific and close to its wording
- reported: false ONLY when the paper says nothing about this component; otherwise true
- confidence: 0.0-1.0, using the scale below
- quotes: 1-3 passages copied VERBATIM from the paper that support the description. Copy them character for character; do not paraphrase, shorten with ellipses, or join sentences. Give no quotes when reported is false.

1. Population (P): who was studied — demographics (age, sex, condition), inclusion and exclusion criteria, setting.
   Example: "Adults aged 40-65 with type 2 diabetes and HbA1c > 7%, recruited from primary care clinics"

2. Intervention (I): what was done to the study population — the treatment, procedure or exposure, with dose, frequency and duration where stated.
   Example: "Metformin 1000mg twice daily for 12 weeks"

3. Comparison (C): what the intervention was compared against — placebo, standard care, no treatment, or an alternative intervention.
   Example: "Placebo tablets twice daily for 12 weeks"
   A study the paper describes as single-arm or uncontrolled HAS a reported comparison: say there was no comparison group, set reported to true, and quote the passage that says so.

4. Outcome (O): what was measured — primary and secondary outcomes, how they were measured, and when.
   Example: "Change in HbA1c from baseline to 12 weeks (primary); fasting glucose and body weight (secondary)"

Also give:
- study_type: the study design if clear (RCT, cohort study, case-control, cross-sectional, ...), or null
- sample_size: the number of participants if stated (e.g. "N=150"), or null

Confidence scale:
- 1.0 = explicitly stated, no ambiguity
- 0.8 = clearly stated but some details missing
- 0.6 = can be inferred but not explicitly stated
- 0.4 = partially mentioned, significant uncertainty
- 0.2 = barely mentioned, high uncertainty"##;

/// Python's `EXTRACTION_RESPONSE_FORMAT`.
pub const EXTRACTION_RESPONSE_FORMAT: &str = r##"Respond with JSON in exactly this shape:

{
    "population": {
        "description": "who was studied",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "intervention": {
        "description": "what was done",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "comparison": {
        "description": "what it was compared against",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "outcome": {
        "description": "what was measured",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "study_type": "randomized controlled trial",
    "sample_size": "N=150"
}"##;

/// Python's `PICO_CONDENSE_QUERY`.
pub const PICO_CONDENSE_QUERY: &str = r##"Everything needed to identify the study's PICO: the study design; who was studied (age, sex, condition, inclusion and exclusion criteria, setting) and how many; the intervention (treatment, dose, frequency, duration); what it was compared against, or that there was no comparison group; and the outcomes measured, how, and when."##;

/// Python's `PICO_CONDENSE_EXTRACTION_PROMPT`.
pub const PICO_CONDENSE_EXTRACTION_PROMPT: &str = r##"Extract VERBATIM every passage of this paper that bears on the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Copy the passages character for character; do not paraphrase or shorten them, since each will be checked against the paper word for word
- Keep numbers, doses, group sizes and timepoints exactly
- Say nothing about what the text does not report
- Return plain text

Extracted passages:"##;

/// Python's `PICO_CONDENSE_CONSOLIDATION_PROMPT`.
pub const PICO_CONDENSE_CONSOLIDATION_PROMPT: &str = r##"Merge these extracted passages into one collection of passages.

Needed: {query}

Extracted passages:
{content}

INSTRUCTIONS:
- Keep every passage VERBATIM, character for character; drop only exact duplicates
- Preserve numbers, doses, group sizes and timepoints exactly
- Return plain text

Consolidated passages:"##;

/// The quotes found, the quotes lost, and how many were not usable text.
pub type QuoteCheck = (Vec<PicoCitation>, Vec<String>, usize);

/// Locate the model's quotes in `text`. Python's `verify_quotes`.
///
/// # Errors
///
/// `min_similarity` outside (0, 1], as [`find_passage`] refuses it.
pub fn verify_quotes(
    quotes: Option<&Value>,
    text: &str,
    min_similarity: f64,
) -> Result<QuoteCheck, InvalidSimilarity> {
    let Some(quotes) = quotes.filter(|value| !value.is_null()) else {
        return Ok((Vec::new(), Vec::new(), 0));
    };
    let Some(quotes) = quotes.as_array() else {
        return Ok((Vec::new(), Vec::new(), 1));
    };

    let mut citations: Vec<PicoCitation> = Vec::new();
    let mut not_found: Vec<String> = Vec::new();
    let mut dropped = 0usize;
    for quote in quotes {
        let Some(quote) = quote.as_str().filter(|q| !python_strip(q).is_empty()) else {
            dropped += 1;
            continue;
        };
        let found = find_passage(quote, text, min_similarity)?;
        let Some(span) = found.span else {
            not_found.push(quote.to_string());
            continue;
        };
        // Each span once, in the order the quotes came.
        if citations
            .iter()
            .any(|c| c.start == span.start && c.end == span.end)
        {
            continue;
        }
        citations.push(PicoCitation {
            start: span.start,
            end: span.end,
            text: span.text,
            similarity: found.similarity,
            exact: found.exact,
        });
    }
    Ok((citations, not_found, dropped))
}

/// Read one PICO part and verify its quotes. Python's `read_part`.
///
/// # Errors
///
/// A message when `value` is not an object carrying a non-blank string
/// `description`.
pub fn read_part(
    value: Option<&Value>,
    text: &str,
    min_similarity: f64,
) -> Result<PicoPart, String> {
    let Some(object) = value.and_then(Value::as_object) else {
        return Err(format!(
            "expected an object, got {}",
            value.map_or("NoneType", crate::agents::json_type_name)
        ));
    };
    let Some(description) = object.get("description").and_then(Value::as_str) else {
        return Err(format!(
            "description is a {}, not text",
            object
                .get("description")
                .map_or("NoneType", crate::agents::json_type_name)
        ));
    };
    // A blank description shows nothing, as `null` does, so it is asked again (#578).
    if python_strip(description).is_empty() {
        return Err("description is blank".to_string());
    }
    let description = description.to_string();
    let confidence = clamped_confidence(object.get("confidence"));
    // Only an explicit `false` is an absence: a garbled flag must not hide a
    // part the model described.
    if object.get("reported").and_then(Value::as_bool) == Some(false) {
        return Ok(PicoPart {
            description,
            state: PicoPartState::NotReported,
            confidence,
            citations: Vec::new(),
            quotes_not_found: Vec::new(),
            quotes_dropped: 0,
        });
    }

    let (citations, quotes_not_found, quotes_dropped) =
        verify_quotes(object.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
    let state = if citations.is_empty() {
        PicoPartState::Unverified
    } else {
        PicoPartState::Verified
    };
    Ok(PicoPart {
        description,
        state,
        confidence,
        citations,
        quotes_not_found,
        quotes_dropped,
    })
}

/// Read an extraction reply. Python's `read_extraction`.
///
/// # Errors
///
/// A message naming the first part that is missing or unusable.
pub fn read_extraction(
    data: &Value,
    text: &str,
    min_similarity: f64,
) -> Result<PicoExtraction, String> {
    let mut parts: Vec<PicoPart> = Vec::with_capacity(PICO_PARTS.len());
    for name in PICO_PARTS {
        let part = read_part(data.get(name), text, min_similarity)
            .map_err(|error| format!("{name}: {error}"))?;
        parts.push(part);
    }

    // A correctly identified absence must not pull the mean down. Summed left
    // to right in PICO order, as Python's hand-rolled loop is (Python avoids
    // `sum()`, which compensates its rounding on 3.12+), so the two agree bit
    // for bit.
    let values: Vec<f64> = parts
        .iter()
        .filter(|part| part.state != PicoPartState::NotReported)
        .filter_map(|part| part.confidence)
        .collect();
    let overall_confidence = if values.is_empty() {
        None
    } else {
        Some(values.iter().fold(0.0, |sum, value| sum + value) / values.len() as f64)
    };

    let mut parts = parts.into_iter();
    let mut next = || parts.next().expect("four parts were read");
    Ok(PicoExtraction {
        population: next(),
        intervention: next(),
        comparison: next(),
        outcome: next(),
        study_type: as_text(data.get("study_type")),
        sample_size: sample_size(data.get("sample_size")),
        overall_confidence,
        condensed_from_chars: None,
        condensation_status: None,
        notes: Vec::new(),
    })
}

/// Read a suitability reply. Python's `read_suitability`.
///
/// # Errors
///
/// A message when `is_suitable` is not a boolean.
pub fn read_suitability(data: &Value) -> Result<PicoSuitability, String> {
    let Some(is_suitable) = data.get("is_suitable").and_then(Value::as_bool) else {
        return Err(format!(
            "is_suitable is a {}, not a boolean",
            data.get("is_suitable")
                .map_or("NoneType", crate::agents::json_type_name)
        ));
    };
    Ok(PicoSuitability {
        is_suitable,
        is_intervention_study: data.get("is_intervention_study").and_then(Value::as_bool),
        has_comparison: data.get("has_comparison").and_then(Value::as_bool),
        study_type: as_text(data.get("study_type")),
        confidence: clamped_confidence(data.get("confidence")),
        rationale: as_text(data.get("rationale")),
        condensed_from_chars: None,
        condensation_status: None,
        notes: Vec::new(),
    })
}

/// Extracts a study's PICO, every part backed by passages of the paper.
///
/// A port of Python's `PicoAgent`. Two independent calls:
/// [`check_suitability`](Self::check_suitability) says whether the paper has a
/// PICO at all, and [`extract`](Self::extract) extracts it; a caller may run
/// the extraction whatever the check answered. Each returns its result or a
/// [`PicoError`] naming why not.
///
/// Text over `condense_config.max_context_chars` is condensed by the
/// configured [`Condenser`] (see [`PicoAgent::pico_condenser`]); with none
/// configured it is refused as [`PicoError::CondensationFailed`] rather than
/// sent whole or cut.
pub struct PicoAgent<'a> {
    chat: &'a mut dyn JsonChat,
    condenser: Option<&'a mut dyn Condenser>,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    condense_config: ProcessingConfig,
}

/// The input after stripping and any condensation: `(title, text_to_send,
/// notes, condensed_from_chars, condensation_status)`.
type Prepared = (String, String, Vec<String>, Option<i64>, Option<String>);

impl<'a> PicoAgent<'a> {
    /// An agent asking through `chat`, with the Cochrane assessor's defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        PicoAgent {
            chat,
            condenser: None,
            temperature: DEFAULT_TEMPERATURE,
            max_tokens: DEFAULT_MAX_TOKENS,
            max_retries: DEFAULT_MAX_RETRIES,
            condense_config: ProcessingConfig::default()
                .with_max_context_chars(DEFAULT_CONDENSE_THRESHOLD_CHARS),
        }
    }

    /// Condense oversized text through `condenser`.
    #[must_use]
    pub fn with_condenser(mut self, condenser: &'a mut dyn Condenser) -> Self {
        self.condenser = Some(condenser);
        self
    }

    /// Sampling temperature.
    #[must_use]
    pub fn with_temperature(mut self, temperature: f64) -> Self {
        self.temperature = temperature;
        self
    }

    /// The reply's output ceiling.
    #[must_use]
    pub fn with_max_tokens(mut self, max_tokens: i64) -> Self {
        self.max_tokens = max_tokens;
        self
    }

    /// `chat_json`'s attempts per question.
    #[must_use]
    pub fn with_max_retries(mut self, max_retries: usize) -> Self {
        self.max_retries = max_retries;
        self
    }

    /// The budget beyond which text is condensed first.
    #[must_use]
    pub fn with_condense_config(mut self, condense_config: ProcessingConfig) -> Self {
        self.condense_config = condense_config;
        self
    }

    /// The map-reduce over the PICO condensation prompts, for
    /// [`with_condenser`](Self::with_condenser).
    ///
    /// # Errors
    ///
    /// A condensation prompt lacking a placeholder (they are constants, so
    /// this is a defect in this module).
    pub fn pico_condenser(
        model: &'a dyn ContextModel,
        temperature: f64,
        max_tokens: i64,
    ) -> Result<LlmCondenser<'a>, String> {
        LlmCondenser::with_prompts(
            model,
            PromptTemplates {
                extraction_prompt: PICO_CONDENSE_EXTRACTION_PROMPT.to_string(),
                consolidation_prompt: PICO_CONDENSE_CONSOLIDATION_PROMPT.to_string(),
            },
            PICO_CONDENSE_QUERY,
            temperature,
            max_tokens,
        )
    }

    /// Say whether the paper is an intervention study with a PICO.
    ///
    /// # Errors
    ///
    /// [`PicoError`] naming why no answer could be made.
    pub fn check_suitability(
        &mut self,
        title: &str,
        text: &str,
    ) -> Result<PicoSuitability, PicoError> {
        let (title, sent, notes, condensed_from, status) =
            self.prepare(title, text, "the suitability check")?;
        let label = format!("PICO suitability check of {}", repr_prefix(&title));
        let mut suitability = self.ask(
            SUITABILITY_TASK_PROMPT,
            SUITABILITY_RESPONSE_FORMAT,
            &title,
            &sent,
            read_suitability,
            &label,
        )?;
        suitability.condensed_from_chars = condensed_from;
        suitability.condensation_status = status;
        suitability.notes = notes;
        Ok(suitability)
    }

    /// Extract the paper's PICO and verify each part's quotes against `text`.
    ///
    /// Citation offsets are `char` indices into `text` exactly as passed.
    ///
    /// # Errors
    ///
    /// [`PicoError::InvalidSimilarity`] for a `min_similarity` outside (0, 1],
    /// before any request; otherwise [`PicoError`] naming why no breakdown
    /// could be made.
    pub fn extract(
        &mut self,
        title: &str,
        text: &str,
        min_similarity: f64,
    ) -> Result<PicoExtraction, PicoError> {
        // Negated so NaN is refused too.
        if !(min_similarity > 0.0 && min_similarity <= 1.0) {
            return Err(PicoError::InvalidSimilarity(min_similarity));
        }
        // Quotes are located in the text as passed, unstripped, so an offset
        // indexes the string the caller will highlight.
        let original = text;
        let (title, sent, notes, condensed_from, status) =
            self.prepare(title, text, "extraction")?;
        let label = format!("PICO extraction of {}", repr_prefix(&title));
        // Quotes are verified against the paper, never the digest.
        let mut extraction = self.ask(
            EXTRACTION_TASK_PROMPT,
            EXTRACTION_RESPONSE_FORMAT,
            &title,
            &sent,
            |data| read_extraction(data, original, min_similarity),
            &label,
        )?;
        extraction.condensed_from_chars = condensed_from;
        extraction.condensation_status = status;
        extraction.notes = notes;
        Ok(extraction)
    }

    fn prepare(&mut self, title: &str, text: &str, stage: &str) -> Result<Prepared, PicoError> {
        let title = python_strip(title).to_string();
        let text = python_strip(text).to_string();
        if title.is_empty() && text.is_empty() {
            return Err(PicoError::EmptyInput);
        }
        // `len(text)` is a character count, not a byte count.
        let length = text.chars().count();
        if length <= self.condense_config.max_context_chars {
            return Ok((title, text, Vec::new(), None, None));
        }

        let label: String = title.chars().take(60).collect();
        let Some(condenser) = self.condenser.as_mut() else {
            return Err(PicoError::CondensationFailed(
                "no condenser configured".to_string(),
            ));
        };
        let (digest, notes, status) = condense_checked(
            &mut **condenser,
            &text,
            &label,
            &self.condense_config,
            stage,
        )
        .map_err(|reason| {
            PicoError::CondensationFailed(format!(
                "a {length}-character text could not be condensed to fit {} characters: {reason}",
                self.condense_config.max_context_chars
            ))
        })?;
        Ok((
            title,
            digest,
            notes,
            Some(length as i64),
            Some(status.as_str().to_string()),
        ))
    }

    fn ask<T>(
        &mut self,
        task: &str,
        response_format: &str,
        title: &str,
        text: &str,
        read: impl Fn(&Value) -> Result<T, String>,
        label: &str,
    ) -> Result<T, PicoError> {
        let prompt =
            format!("{task}\n\nPaper Title: {title}\n\nPaper Text:\n{text}\n\n{response_format}");
        let messages = vec![
            LLMMessage::system(PICO_SYSTEM_PROMPT),
            LLMMessage::user(prompt),
        ];

        let mut problem = String::new();
        for _attempt in 0..READ_ATTEMPTS {
            let data = match self.chat.chat_json(
                &messages,
                self.temperature,
                self.max_tokens,
                self.max_retries,
                true,
            ) {
                Ok(outcome) => outcome.value,
                Err(error @ ChatJsonError::Truncated { .. }) => {
                    return Err(PicoError::Truncated(error.to_string()))
                }
                Err(error @ ChatJsonError::Transport { .. }) => {
                    return Err(PicoError::RequestFailed(error.to_string()))
                }
                Err(error) => return Err(PicoError::UnusableReply(error.to_string())),
            };
            match read(&data) {
                Ok(result) => return Ok(result),
                Err(message) => problem = message,
            }
        }
        Err(PicoError::UnusableReply(format!(
            "{label}: {READ_ATTEMPTS} replies could not be read: {problem}"
        )))
    }
}

/// Python's `repr(title[:60])`, for a log label.
fn repr_prefix(title: &str) -> String {
    crate::pyvalue::repr_str(&title.chars().take(60).collect::<String>())
}

/// `_json_fields.as_text`: a string, verbatim; anything else is unstated.
fn as_text(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).map(str::to_string)
}

/// Python's `_sample_size`: a string verbatim, an integer as its digits (#578).
///
/// A float is refused, as in Python, whose `str(150.0)` is `"150.0"`. An
/// integer past 64 bits arrives here as a float and is refused, where Python
/// keeps it; no participant count is that big.
fn sample_size(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::Number(number)) if number.is_i64() || number.is_u64() => {
            Some(number.to_string())
        }
        other => as_text(other),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const TEXT: &str = "Adults aged 40-65 were randomised to metformin or placebo.";
    const SIM: f64 = 0.9;

    fn part(overrides: Value) -> Value {
        let mut value = json!({
            "description": "Adults aged 40 to 65",
            "reported": true,
            "confidence": 0.9,
            "quotes": ["Adults aged 40-65"],
        });
        for (key, item) in overrides.as_object().expect("an object") {
            value[key] = item.clone();
        }
        value
    }

    #[test]
    fn a_found_quote_is_the_papers_span() {
        let result = read_part(
            Some(&part(json!({"quotes": ["adults aged 40–65"]}))),
            TEXT,
            SIM,
        )
        .expect("a part");
        assert_eq!(result.state, PicoPartState::Verified);
        assert_eq!(result.citations[0].text, "Adults aged 40-65");
        assert_eq!(result.citations[0].start, 0);
    }

    #[test]
    fn quotes_as_a_bare_string() {
        let result = read_part(
            Some(&part(json!({"quotes": "Adults aged 40-65"}))),
            TEXT,
            SIM,
        )
        .expect("a part");
        assert_eq!(result.state, PicoPartState::Unverified);
        assert_eq!(result.quotes_dropped, 1);
    }

    #[test]
    fn overall_confidence_skips_unreported_parts() {
        let data = json!({
            "population": part(json!({"confidence": 0.9})),
            "intervention": part(json!({"confidence": 0.7})),
            "comparison": part(json!({"reported": false, "confidence": 0.1})),
            "outcome": part(json!({"confidence": null})),
        });
        let extraction = read_extraction(&data, TEXT, SIM).expect("an extraction");
        assert_eq!(extraction.overall_confidence, Some((0.9 + 0.7) / 2.0));
    }

    #[test]
    fn a_missing_part_names_itself() {
        let data = json!({
            "population": part(json!({})),
            "intervention": part(json!({})),
            "outcome": part(json!({})),
        });
        let error = read_extraction(&data, TEXT, SIM).expect_err("comparison is missing");
        assert!(error.starts_with("comparison: "), "{error}");
    }

    #[test]
    fn a_blank_description_is_refused_as_null_is() {
        // "\x1c" is blank to Python's `str.strip` and not to `trim`.
        for blank in ["", "   ", "\n\t", "\x1c"] {
            for reported in [true, false] {
                let overrides = json!({"description": blank, "reported": reported});
                let error =
                    read_part(Some(&part(overrides)), TEXT, SIM).expect_err("a blank description");
                assert_eq!(error, "description is blank");
            }
        }
    }

    #[test]
    fn an_integer_sample_size_is_kept_as_its_digits() {
        for (value, expected) in [
            (json!(150), Some("150")),
            (json!(0), Some("0")),
            (json!(150.0), None),
            (json!(true), None),
            (json!("N=150"), Some("N=150")),
            (Value::Null, None),
        ] {
            let mut data = json!({
                "population": part(json!({})),
                "intervention": part(json!({})),
                "comparison": part(json!({})),
                "outcome": part(json!({})),
            });
            data["sample_size"] = value.clone();
            let extraction = read_extraction(&data, TEXT, SIM).expect("an extraction");
            assert_eq!(extraction.sample_size.as_deref(), expected, "{value}");
        }
    }

    #[test]
    fn suitability_requires_a_bool_is_suitable() {
        assert!(read_suitability(&json!({"is_suitable": "yes"})).is_err());
    }
}
