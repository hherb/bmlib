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

//! The one part of `context_processor/` that talks to a model.
//!
//! The harness itself has **no LLM dependency** — that is why this is a separate
//! module and why the crate needs no async runtime for it. This file is the whole
//! of the coupling, and it takes the model as a trait so the harness stays
//! testable without one.

use crate::context_processor::base::{
    run_all, split_string, text_items, Item, ItemRef, IterativeContextProcessor, ProcessingCore,
    ProgressCallback, SplitError,
};
use crate::context_processor::data_types::{
    ConsolidatedItem, ExtractionResult, ProcessingConfig, ProcessingResult,
};
use regex::Regex;
use serde_json::Value;
use std::collections::BTreeMap;
use std::sync::OnceLock;

/// The confidence recorded when the model reported none.
pub const DEFAULT_EXTRACTION_CONFIDENCE: f64 = 0.9;

/// The placeholders a prompt template must carry.
pub const REQUIRED_PLACEHOLDERS: &[&str] = &["{query}", "{content}"];

fn placeholder_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"\{(query|content)\}").expect("a fixed pattern"))
}

/// Check a prompt template carries the placeholders that get filled.
///
/// # Errors
///
/// The missing placeholder and the parameter's name, so a caller with two
/// templates knows which one to fix.
pub fn validate_template(template: &str, name: &str) -> Result<(), String> {
    for placeholder in REQUIRED_PLACEHOLDERS {
        if !template.contains(placeholder) {
            return Err(format!("{name} must contain the {placeholder} placeholder"));
        }
    }
    Ok(())
}

/// Fill a prompt template.
///
/// **Substitution is by replacement, not by a format string**, so a template may
/// contain literal braces — a JSON example, a regex, a LaTeX fragment — without
/// doubling them.
///
/// **One pass, not two chained replacements**: a second pass runs over what the
/// first substituted, so a query containing the literal `{content}` would have the
/// whole batch spliced into it — doubling a prompt that was sized to fit exactly,
/// which is the context overflow this module exists to prevent.
#[must_use]
pub fn render_template(template: &str, query: &str, content: &str) -> String {
    placeholder_pattern()
        .replace_all(template, |caps: &regex::Captures<'_>| {
            match caps.get(1).map(|m| m.as_str()) {
                Some("query") => query.to_string(),
                Some("content") => content.to_string(),
                _ => String::new(),
            }
        })
        .into_owned()
}

/// Whether an item is a `(text, score)` pair.
///
/// **Python requires a `tuple`**, and a JSON array is not one — so the source's
/// predicate is `false` for every array, including `["text", 0.5]`. That is a
/// distinction JSON cannot carry, and the port has to choose a spelling. It uses
/// a **two-element array whose first member is a string and whose second is a
/// number**, which is the shape the Python *means*: a caller handing this port a
/// scored chunk could not produce a Python tuple anyway, and reading arrays as
/// unscored would silently drop every score.
///
/// The two rules the source states are kept: a JSON **boolean is rejected
/// although it is a number** (`("text", True)` is a caller's mistake, and
/// rendering it as `score 1.00` hides that), and a three-element array is not a
/// pair.
#[must_use]
pub fn is_scored_chunk(item: &Value) -> bool {
    match item {
        Value::Array(items) if items.len() == 2 => {
            items[0].is_string() && items[1].is_number() && !matches!(items[1], Value::Bool(_))
        }
        _ => false,
    }
}

/// Render a chunk, showing its search score when it has one.
#[must_use]
pub fn format_item(item: &Value, index: usize) -> String {
    if is_scored_chunk(item) {
        let text = item[0].as_str().unwrap_or_default();
        let score = item[1].as_f64().unwrap_or(0.0);
        return format!("[Chunk {}, score {score:.2}]\n{text}", index + 1);
    }
    let text = match item {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    };
    // The `[Item N]` header is used for a plain string **and** for an
    // unexpected type: the source logs a warning for the second and renders it
    // the same way, so the header alone does not distinguish them.
    format!("[Item {}]\n{text}", index + 1)
}

/// Render a summary from the level below, naming the level it came from.
#[must_use]
pub fn format_consolidated_item(item: &ConsolidatedItem, index: usize) -> String {
    // **The level is printed as the metadata spells it.** The source's f-string
    // coerces, so an integer `3` and the string `"3"` both render as `3`, and a
    // non-numeric value renders as itself rather than falling back to 0 — which a
    // numeric read here would do.
    let level = match item.metadata.get("recursion_level") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(other) => other.to_string(),
        None => "0".to_string(),
    };
    format!(
        "[Consolidated level {level}, item {}]\n{}",
        index + 1,
        item.content
    )
}

/// Split a chunk too long to fit, **keeping its score on every piece**.
///
/// A scored chunk loses its score if it is merely passed through the base
/// splitter, and the score is what the `WEIGHTED` consolidation strategy sorts on.
#[must_use]
pub fn split_oversized_item(item: &Value, max_chars: usize, overlap: usize) -> Vec<Value> {
    if is_scored_chunk(item) {
        let text = item[0].as_str().unwrap_or_default();
        let score = item[1].clone();
        return split_string(text, max_chars, overlap)
            .into_iter()
            .map(|piece| Value::Array(vec![Value::String(piece), score.clone()]))
            .collect();
    }
    if let Value::String(text) = item {
        return split_string(text, max_chars, overlap)
            .into_iter()
            .map(Value::String)
            .collect();
    }
    vec![item.clone()]
}

/// What the model is asked, and how.
#[derive(Debug, Clone, PartialEq)]
pub struct PromptTemplates {
    /// The level-0 extraction prompt. Must carry both placeholders.
    pub extraction_prompt: String,
    /// The consolidation prompt for every level above. Must carry both.
    pub consolidation_prompt: String,
}

impl PromptTemplates {
    /// Validate both templates.
    ///
    /// # Errors
    ///
    /// The name of whichever template is short a placeholder.
    pub fn validate(&self) -> Result<(), String> {
        validate_template(&self.extraction_prompt, "extraction_prompt")?;
        validate_template(&self.consolidation_prompt, "consolidation_prompt")
    }
}

/// One batch's answer from the model.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelAnswer {
    /// The extracted or summarised text.
    pub content: String,
    /// The model's own confidence, already clamped.
    pub confidence: f64,
    /// The findings it named, when it named any.
    pub findings: Vec<Value>,
}

impl ModelAnswer {
    /// A plain answer at the default confidence.
    #[must_use]
    pub fn plain(content: impl Into<String>) -> Self {
        ModelAnswer {
            content: content.into(),
            confidence: DEFAULT_EXTRACTION_CONFIDENCE,
            findings: Vec::new(),
        }
    }
}

/// How the processor reaches a model.
///
/// A trait rather than a concrete client, so the extraction rules are testable
/// without a network — and so this module does not depend on `llm/`'s transport.
pub trait ContextModel {
    /// A plain completion.
    ///
    /// # Errors
    ///
    /// Whatever the transport reports.
    fn complete(&self, prompt: &str, temperature: f64, max_tokens: i64) -> Result<String, String>;

    /// A JSON-mode completion, read as the structured extraction shape.
    ///
    /// # Errors
    ///
    /// Whatever the transport reports, or a body that is not an object.
    fn complete_json(
        &self,
        prompt: &str,
        temperature: f64,
        max_tokens: i64,
    ) -> Result<Value, String>;
}

/// Read the model's own confidence out of a structured answer.
///
/// **An unusable confidence falls back to the default rather than to zero**: a
/// model that reported `"high"` has not said it is unsure, and recording zero
/// would let `min_confidence_threshold` discard a good extraction for a type
/// error. The clamp is applied after, so a reported `1.5` becomes `1.0` rather
/// than being read as missing.
#[must_use]
pub fn read_confidence(parsed: &Value) -> f64 {
    let raw = parsed.get("confidence");
    let value = match raw {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.trim().parse::<f64>().ok(),
        _ => None,
    };
    match value {
        Some(value) => value.clamp(0.0, 1.0),
        None => DEFAULT_EXTRACTION_CONFIDENCE,
    }
}

/// Read the findings list, dropping anything that is not a list.
#[must_use]
pub fn read_findings(parsed: &Value) -> Vec<Value> {
    match parsed.get("key_findings") {
        Some(Value::Array(items)) => items.clone(),
        // A truthy non-list is **not** coerced into a one-element list: the
        // source's `list(findings)` over a string would split it into
        // characters, which is a shape no caller asked for.
        _ => Vec::new(),
    }
}

/// One batch to summarise.
///
/// A struct rather than seven positional parameters: the two that matter most —
/// the content and the query — are both `&str`, so a transposed pair would compile
/// and silently ask the wrong question of the wrong text.
#[derive(Debug)]
pub struct BatchRequest<'a> {
    /// The prompts, already validated.
    pub templates: &'a PromptTemplates,
    /// The batch's formatted content.
    pub batch_content: &'a str,
    /// The question guiding extraction.
    pub query: &'a str,
    /// Carries `recursion_level` and `batch_index`.
    pub batch_metadata: &'a BTreeMap<String, Value>,
    /// Sampling temperature.
    pub temperature: f64,
    /// Output cap.
    pub max_tokens: i64,
    /// Whether a level-0 batch asks for the structured shape.
    pub use_structured_output: bool,
}

/// Summarise one batch with the model.
///
/// **Level 0 uses the extraction prompt; every level above uses the
/// consolidation prompt**, which asks the model to merge rather than extract.
///
/// # Errors
///
/// A transport or parse failure, so the harness can record it against the batch
/// rather than losing the batch's index.
pub fn extract_from_batch(
    model: &dyn ContextModel,
    request: &BatchRequest<'_>,
) -> Result<ExtractionResult, String> {
    let BatchRequest {
        templates,
        batch_content,
        query,
        batch_metadata,
        temperature,
        max_tokens,
        use_structured_output,
    } = *request;
    let level = batch_metadata
        .get("recursion_level")
        .and_then(Value::as_u64)
        .unwrap_or(0) as usize;
    let structured = use_structured_output && level == 0;
    let template = if level == 0 {
        &templates.extraction_prompt
    } else {
        &templates.consolidation_prompt
    };
    let prompt = render_template(template, query, batch_content);

    let answer = if structured {
        let parsed = model.complete_json(&prompt, temperature, max_tokens)?;
        let content = parsed
            .get("extracted_content")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_default()
            .trim()
            .to_string();
        ModelAnswer {
            content,
            confidence: read_confidence(&parsed),
            findings: read_findings(&parsed),
        }
    } else {
        let content = model.complete(&prompt, temperature, max_tokens)?;
        ModelAnswer::plain(content.trim())
    };

    let mut metadata = batch_metadata.clone();
    if !answer.findings.is_empty() {
        metadata.insert("key_findings".to_string(), Value::Array(answer.findings));
    }

    let mut result = ExtractionResult::new(answer.content);
    result.metadata = metadata;
    result.confidence = answer.confidence;
    result.recursion_level = level;
    result.batch_index = batch_metadata
        .get("batch_index")
        .and_then(Value::as_u64)
        .map(|v| v as usize);
    Ok(result)
}

// ---------------------------------------------------------------------------
// The processor: the harness bound to a model
// ---------------------------------------------------------------------------

/// One chunk as [`LlmChunkProcessor`] renders it.
///
/// Python's `format_item` takes the caller's own value — a plain string or a
/// `(text, score)` tuple — and chooses the header from its *type*. Rust's
/// `&dyn Item` carries no type information for a hook to route on, so this port
/// puts the choice where the port already puts per-item rendering: in the item.
/// The bytes are Python's, which is the part that matters.
#[derive(Debug, Clone, PartialEq)]
pub struct ChunkItem {
    /// The chunk's text.
    pub text: String,
    /// The search score, when the caller had one — Python's `(text, score)`.
    pub score: Option<f64>,
}

impl ChunkItem {
    /// A plain chunk: `[Item N]`.
    #[must_use]
    pub fn new(text: impl Into<String>) -> Self {
        ChunkItem {
            text: text.into(),
            score: None,
        }
    }

    /// A scored chunk: `[Chunk N, score S]`.
    #[must_use]
    pub fn scored(text: impl Into<String>, score: f64) -> Self {
        ChunkItem {
            text: text.into(),
            score: Some(score),
        }
    }
}

impl Item for ChunkItem {
    fn render(&self, index: usize) -> String {
        match self.score {
            Some(score) => format!("[Chunk {}, score {score:.2}]\n{}", index + 1, self.text),
            None => format!("[Item {}]\n{}", index + 1, self.text),
        }
    }

    fn split(&self, max_chars: usize, overlap: usize) -> Result<Vec<Box<dyn Item>>, SplitError> {
        // **The score is kept on every piece**, which is the rule
        // `split_oversized_item` states and the `WEIGHTED` strategy sorts on.
        Ok(split_string(&self.text, max_chars, overlap)
            .into_iter()
            .map(|piece| {
                Box::new(ChunkItem {
                    text: piece,
                    score: self.score,
                }) as Box<dyn Item>
            })
            .collect())
    }
}

/// Python's `LLMChunkProcessor`: the iterative map-reduce bound to a model.
///
/// The harness is [`crate::context_processor::base`]'s, unchanged; what this
/// type adds is the three things the harness asks a processor for — how an item
/// renders, which prompt a level uses, and who answers.
///
/// **The port carried the rules of this class and not the class**, because
/// [`ContextModel`] is a trait and `LLMChunkProcessor` is a `BaseAgent`
/// subclass. That left the map-reduce reachable only by a caller willing to
/// write the binding themselves, and left `CochraneAssessor`'s condensation
/// running against a test stub — see [`LlmCondenser`](crate::quality::LlmCondenser).
///
/// Python's `temperature=None` / `max_tokens=None` mean *"use the agent's own"*;
/// a [`ContextModel`] has no defaults to read, so the caller supplies the
/// values its agent would have used. The Cochrane assessor passes its own.
pub struct LlmChunkProcessor<'a> {
    core: ProcessingCore,
    model: &'a dyn ContextModel,
    templates: PromptTemplates,
    use_structured_output: bool,
    temperature: f64,
    max_tokens: i64,
}

impl std::fmt::Debug for LlmChunkProcessor<'_> {
    /// The model is a trait object and does not print; the settings that decide
    /// what it is asked do.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LlmChunkProcessor")
            .field("max_context_chars", &self.core.config.max_context_chars)
            .field("use_structured_output", &self.use_structured_output)
            .field("temperature", &self.temperature)
            .field("max_tokens", &self.max_tokens)
            .finish_non_exhaustive()
    }
}

impl<'a> LlmChunkProcessor<'a> {
    /// Bind the harness to `model`.
    ///
    /// # Errors
    ///
    /// A template missing `{query}` or `{content}`, naming which parameter is
    /// short, or an invalid configuration — Python's `ValueError` for both.
    pub fn new(
        model: &'a dyn ContextModel,
        templates: PromptTemplates,
        config: ProcessingConfig,
        use_structured_output: bool,
        temperature: f64,
        max_tokens: i64,
    ) -> Result<Self, String> {
        templates.validate()?;
        let core = ProcessingCore::new(config, None).map_err(|error| error.to_string())?;
        Ok(LlmChunkProcessor {
            core,
            model,
            templates,
            use_structured_output,
            temperature,
            max_tokens,
        })
    }

    /// The prompts in force.
    #[must_use]
    pub fn templates(&self) -> &PromptTemplates {
        &self.templates
    }

    /// Condense one text, the way the Python's `_condense` calls `process`.
    #[must_use]
    pub fn process(&self, text: &str, query: &str) -> ProcessingResult {
        self.process_items(&text_items(&[text]), query)
    }

    /// Run the harness over items the caller built.
    ///
    /// `store_intermediate` is `false`, as Python's `_condense` leaves it: the
    /// per-level results are not wanted and holding them doubles peak memory on
    /// the oversized documents this exists for.
    #[must_use]
    pub fn process_items(&self, items: &[ItemRef], query: &str) -> ProcessingResult {
        run_all(self, &self.core, items, query, &self.core.config, false)
    }
}

impl IterativeContextProcessor for LlmChunkProcessor<'_> {
    fn config(&self) -> &ProcessingConfig {
        &self.core.config
    }

    /// The item renders itself: the header depends on whether the caller had a
    /// score, and a `&dyn Item` cannot be asked which it is.
    fn format_item(&self, item: &dyn Item, index: usize) -> String {
        item.render(index)
    }

    fn format_consolidated_item(&self, item: &ConsolidatedItem, index: usize) -> String {
        crate::context_processor::llm_processor::format_consolidated_item(item, index)
    }

    fn extract_from_batch(
        &self,
        batch_content: &str,
        query: &str,
        batch_metadata: &BTreeMap<String, Value>,
    ) -> Result<ExtractionResult, Box<dyn std::error::Error + Send + Sync>> {
        let request = BatchRequest {
            templates: &self.templates,
            batch_content,
            query,
            batch_metadata,
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            use_structured_output: self.use_structured_output,
        };
        extract_from_batch(self.model, &request).map_err(|error| {
            // Python wraps the failure so the harness's record names the stage:
            // `RuntimeError(f"LLM extraction failed: {exc}")`.
            Box::new(std::io::Error::other(format!(
                "LLM extraction failed: {error}"
            ))) as Box<dyn std::error::Error + Send + Sync>
        })
    }

    fn progress_callback(&self) -> Option<&ProgressCallback> {
        self.core.progress_callback.as_ref()
    }
}
