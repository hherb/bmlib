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

//! The PRISMA 2020 agent: is the paper a systematic review, and how fully does
//! it report each checklist item of a part?
//!
//! Three calls, which the caller drives so it can store each part as it
//! finishes (StudyAnalyzer's PRISMA tab, design
//! `2026-11-18-prisma2020-tab-design.md`):
//!
//! * [`Prisma2020Agent::check_suitability`]: a systematic review or
//!   meta-analysis (a variant included) is suitable. The caller passes the
//!   abstract or the whole paper; nothing is cut (bmlibrarian's
//!   `full_text[:2000]` is not ported).
//! * [`Prisma2020Agent::prepare_part`]: the paper for one part, whole when it
//!   fits, else condensed with [`part_query`] of the part's items.
//! * [`Prisma2020Agent::assess_part`]: the model judges each item and quotes
//!   the paper; [`read_part_reply`] locates every quote in the **paper**,
//!   never the digest, and shows only what a quote backs.
//!
//! A condensation runs with `continue_on_error(false)`, so a failed batch
//! fails it, and a digest whose run did not complete is refused: no call is
//! ever judged from a partial digest. The prompts are byte-identical to
//! Python's; `rust/oracle/dump_prisma2020_agent.py` pins them.
//!
//! The checklist wording the prompts carry is the PRISMA 2020 checklist's
//! (Page MJ et al., *BMJ* 2021;372:n71, CC BY 4.0).
//!
//! A port of `bmlib/quality/prisma2020_agent.py`.

use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use serde_json::Value;

use crate::context_processor::llm_processor::{ContextModel, PromptTemplates};
use crate::context_processor::{
    ProcessingConfig, ProcessingResult, ProcessingStatus, ProgressInfo,
};
use crate::quality::agent_chat::JsonChat;
use crate::quality::cochrane_assessor::{
    Condenser, LlmCondenser, DEFAULT_CONDENSE_THRESHOLD_CHARS, DEFAULT_MAX_RETRIES,
    DEFAULT_TEMPERATURE,
};
use crate::quality::full_text_agent::{Bound, QueryCondenser, Sampling, DEFAULT_MAX_TOKENS};
use crate::quality::pico_agent::{
    ask_json, check_similarity, prepare_text, repr_prefix, sent_within, wrapped_prompt,
};
use crate::quality::pico_models::{PicoError, PicoPrepared};
use crate::quality::prisma2020_checklist::{item, ChecklistItem};
use crate::quality::prisma2020_models::{
    read_part_reply, read_suitability, PartAssessment, Suitability,
};
use crate::quality::prisma2020_parts::{part_query, PartPlan, PartsError};

// --- Prompts: byte-identical to Python's; the oracle pins them ---

/// The system prompt of every PRISMA 2020 call.
pub const PRISMA_SYSTEM_PROMPT: &str = r##"You are an expert in systematic review methodology and the PRISMA 2020 reporting guideline.

CRITICAL RULES:
1. Judge ONLY what is ACTUALLY PRESENT in the text
2. DO NOT assume, infer, or fabricate anything the text does not state
3. Judge THIS review's own reporting, not the studies it includes
4. Return ONLY valid JSON, no explanation; use null, never NaN or Infinity, for a number you cannot give"##;

/// The suitability check's task.
pub const SUITABILITY_TASK_PROMPT: &str = r##"Determine whether the document below is a SYSTEMATIC REVIEW or META-ANALYSIS, which makes it suitable for assessment against the PRISMA 2020 reporting checklist.

A document is SUITABLE if it is:
- A systematic review (a systematic search, with stated methods to identify, select and appraise studies)
- A meta-analysis (a statistical synthesis of the results of several studies)
- A systematic review with a meta-analysis
- One of these kinds of systematic review: a scoping review, a network meta-analysis, an umbrella review (a review of reviews), a rapid review or a living review

A document is NOT SUITABLE if it is:
- Primary research (an RCT, a cohort study, a case-control study, ...)
- A narrative review or literature review without systematic methods
- A protocol for a review, with no results yet
- A clinical guideline
- An editorial, commentary, or opinion piece
- A case report or case series

KEY INDICATORS of a systematic review:
- It describes a systematic search (databases, search terms, dates)
- It states criteria for selecting studies
- It assesses the risk of bias or quality of the included studies
- It may include a PRISMA flow diagram or cite PRISMA
- Its title often says "systematic review" or "meta-analysis"

Assess:
1. is_systematic_review: is this a systematic review (any of the kinds above)?
2. is_meta_analysis: does it include a statistical meta-analysis?
3. document_type: the type of document (e.g. "systematic review with meta-analysis", "scoping review", "narrative review", "RCT")
4. variant: "scoping_review", "network_meta_analysis", "umbrella_review", "rapid_review" or "living_review" when it is one of these kinds, otherwise null
5. rationale: a brief explanation (2-3 sentences)
6. confidence: how confident you are in this assessment (0.0-1.0)

Do not take a document for a systematic review just because it reviews literature: look for the methods above. If uncertain, lean towards NOT SUITABLE and say why in the rationale."##;

/// The suitability reply's shape.
pub const SUITABILITY_RESPONSE_FORMAT: &str = r##"Respond with JSON in exactly this shape:

{
    "is_systematic_review": true,
    "is_meta_analysis": true,
    "document_type": "systematic review with meta-analysis",
    "variant": null,
    "rationale": "The review searched MEDLINE and Embase, stated its inclusion criteria and pooled 12 trials in a random-effects meta-analysis.",
    "confidence": 0.9
}"##;

/// A part's task; `{items}` is replaced with each item's id, name, wording
/// and guidance.
pub const PART_TASK_PROMPT: &str = r##"Judge how fully the systematic review below reports each of these PRISMA 2020 checklist items.

{items}

For EACH item give:
- id: the item's id exactly as listed (for example "13d")
- judgement: one of "reported", "partly_reported", "not_reported", "reported_elsewhere" or "not_applicable", as the item's guidance defines them
- explanation: 1-3 sentences saying what the paper reports for the item, or what is missing
- quotes: 1-3 passages copied VERBATIM from the paper that show what you judged. Copy them character for character; do not paraphrase, shorten with ellipses, or join sentences.

STRICT EVIDENCE RULES:
- Score only what is actually present in the paper; do not infer what it does not say
- Every judgement but "not_reported" needs a quote; one with no quote found in the paper is shown as "not_reported"
- "reported_elsewhere": quote the paper's pointer to where the item is reported (a supplement, a protocol, a registry record)
- "not_applicable": only where the item's guidance allows it, quoting the passage that shows why
- Be strict: an item reported only in part is "partly_reported", not "reported""##;

/// A part's reply shape.
pub const PART_RESPONSE_FORMAT: &str = r##"Respond with JSON in exactly this shape, one entry for each item listed:

{
    "items": [
        {
            "id": "7",
            "judgement": "partly_reported",
            "explanation": "The MEDLINE strategy is given in full; the strategies for the other databases are not.",
            "quotes": ["a passage copied verbatim from the paper"]
        }
    ]
}"##;

/// What a condensation for the suitability check keeps.
pub const SUITABILITY_CONDENSE_QUERY: &str = r##"Everything needed to tell whether the document is a systematic review or meta-analysis: its title and stated design; how studies were searched for (databases, search terms, dates); the criteria for selecting studies; any assessment of the included studies' risk of bias or quality; any statistical pooling of results; and any mention of PRISMA, a protocol or a registration."##;

/// The map prompt of a PRISMA condensation (`{query}`, `{content}`).
pub const PRISMA_CONDENSE_EXTRACTION_PROMPT: &str = r##"Extract VERBATIM every passage of this paper that bears on the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Copy the passages character for character; do not paraphrase or shorten them, since each will be checked against the paper word for word
- Keep numbers, dates, database names, search strings, registration numbers and pointers to supplements exactly
- Keep a passage that says something was not done, or is reported elsewhere
- Say nothing about what the text does not report
- Return plain text

Extracted passages:"##;

/// The reduce prompt of a PRISMA condensation (`{query}`, `{content}`).
pub const PRISMA_CONDENSE_CONSOLIDATION_PROMPT: &str = r##"Merge these extracted passages into one collection of passages.

Needed: {query}

Extracted passages:
{content}

INSTRUCTIONS:
- Keep every passage VERBATIM, character for character; drop only exact duplicates
- Preserve numbers, dates, database names, search strings, registration numbers and pointers to supplements exactly
- Return plain text

Consolidated passages:"##;

/// Names the suitability check in a degraded condensation's note.
const SUITABILITY_STAGE: &str = "the suitability check";

/// Names a part's assessment in a degraded condensation's note.
const PART_STAGE: &str = "the assessment of PRISMA 2020 items";

/// [`PART_TASK_PROMPT`] with its `{items}` filled in.
fn part_task(rows: &[&'static ChecklistItem]) -> String {
    let items: Vec<String> = rows
        .iter()
        .map(|row| {
            format!(
                "Item {}: {}\nChecklist wording: {}\nGuidance:\n{}",
                row.id, row.name, row.wording, row.guidance
            )
        })
        .collect();
    // `replace`, not a template: guidance text is data.
    PART_TASK_PROMPT.replace("{items}", &items.join("\n\n"))
}

/// The characters of the longest prompt a part of `item_ids` sends, without
/// the paper and its title: the system prompt and the longest of the part's
/// question (as it is wrapped) and its two condensation prompts. A caller
/// subtracts it from the window when sizing the paper's budget. Ids not on
/// the checklist add nothing.
#[must_use]
pub fn part_prompt_chars(item_ids: &[&'static str]) -> usize {
    let rows: Vec<&'static ChecklistItem> = item_ids.iter().filter_map(|id| item(id)).collect();
    longest_prompt_chars(&part_task(&rows), PART_RESPONSE_FORMAT, &part_query(&rows))
}

/// The characters of the longest prompt the suitability check sends, without
/// the paper and its title, measured as [`part_prompt_chars`] measures a
/// part's. The check may send the whole paper, so a caller sizing one budget
/// for a whole run takes the larger of this and its parts' measure.
#[must_use]
pub fn suitability_prompt_chars() -> usize {
    longest_prompt_chars(
        SUITABILITY_TASK_PROMPT,
        SUITABILITY_RESPONSE_FORMAT,
        SUITABILITY_CONDENSE_QUERY,
    )
}

/// The system prompt's characters plus the longest of `task`'s question (as
/// it is wrapped, with an empty title and paper) and the two condensation
/// prompts for `query`.
fn longest_prompt_chars(task: &str, response_format: &str, query: &str) -> usize {
    let condense = |template: &str| template.replace("{query}", query).replace("{content}", "");
    let longest = [
        wrapped_prompt(task, response_format, "", ""),
        condense(PRISMA_CONDENSE_EXTRACTION_PROMPT),
        condense(PRISMA_CONDENSE_CONSOLIDATION_PROMPT),
    ]
    .iter()
    .map(|prompt| prompt.chars().count())
    .max()
    .unwrap_or(0);
    PRISMA_SYSTEM_PROMPT.chars().count() + longest
}

/// Why a PRISMA 2020 call made no result: [`PicoError`]'s variants and kind
/// strings, plus the two a part brings.
#[derive(Debug, Clone, PartialEq)]
pub enum PrismaError {
    /// The title and text were both empty, so no model call was made.
    EmptyInput,
    /// The provider was unreachable or refused the request, a condensation's
    /// included (#616); as [`PicoError::RequestFailed`].
    RequestFailed {
        /// What failed, in the transport's or the server's words.
        message: String,
        /// Whether sending the request again may succeed; a 401 or a 404 is
        /// `false`.
        retryable: bool,
        /// The HTTP status the server answered with, where one came.
        status: Option<u16>,
    },
    /// The reply hit the output ceiling: split the part, or raise
    /// `max_tokens`.
    Truncated(String),
    /// Every reply was empty, did not parse, or could not be read.
    UnusableReply(String),
    /// The paper did not fit and no digest that fits came of condensing it
    /// (no condenser, a failed run, an empty or oversized digest, or a run
    /// that did not complete), though no request failed it.
    CondensationFailed(String),
    /// `min_similarity` was not a number in (0, 1].
    InvalidSimilarity(f64),
    /// A [`PrismaPrepared`] sends more than this agent's budget.
    PreparedOverBudget {
        /// The characters the prepared paper sends.
        chars: usize,
        /// This agent's `max_context_chars`.
        budget: usize,
    },
    /// A [`PrismaPrepared`] was condensed, though its text fits this agent's
    /// budget whole.
    PreparedUnderBudget {
        /// The stripped text's characters.
        chars: usize,
        /// This agent's `max_context_chars`.
        budget: usize,
    },
    /// The stop was set: the call ended before its next request; carries the
    /// failure of a request under way, where there was one.
    Stopped(Option<String>),
    /// The part's item ids were none, repeated, or not on the checklist: a
    /// caller's mistake, refused before any request (Python's `ValueError`).
    InvalidItems(String),
    /// Splitting the checklist failed ([`crate::quality::plan_parts`]), so a
    /// caller can report the split and the calls as one error.
    Parts(PartsError),
}

impl PrismaError {
    /// The error's kind string: [`PicoError::kind`]'s for the shared variants,
    /// `"invalid_items"` and `"parts"` for the two of its own.
    #[must_use]
    pub fn kind(&self) -> &'static str {
        match self {
            PrismaError::EmptyInput => "empty_input",
            PrismaError::RequestFailed { .. } => "request_failed",
            PrismaError::Truncated(_) => "truncated",
            PrismaError::UnusableReply(_) => "unusable_reply",
            PrismaError::CondensationFailed(_) => "condensation_failed",
            PrismaError::InvalidSimilarity(_) => "invalid_similarity",
            PrismaError::PreparedOverBudget { .. } => "prepared_over_budget",
            PrismaError::PreparedUnderBudget { .. } => "prepared_under_budget",
            PrismaError::Stopped(_) => "stopped",
            PrismaError::InvalidItems(_) => "invalid_items",
            PrismaError::Parts(_) => "parts",
        }
    }
}

impl From<PicoError> for PrismaError {
    fn from(error: PicoError) -> Self {
        match error {
            PicoError::EmptyInput => PrismaError::EmptyInput,
            PicoError::RequestFailed {
                message,
                retryable,
                status,
            } => PrismaError::RequestFailed {
                message,
                retryable,
                status,
            },
            PicoError::Truncated(message) => PrismaError::Truncated(message),
            PicoError::UnusableReply(message) => PrismaError::UnusableReply(message),
            PicoError::CondensationFailed(message) => PrismaError::CondensationFailed(message),
            PicoError::InvalidSimilarity(value) => PrismaError::InvalidSimilarity(value),
            PicoError::PreparedOverBudget { chars, budget } => {
                PrismaError::PreparedOverBudget { chars, budget }
            }
            PicoError::PreparedUnderBudget { chars, budget } => {
                PrismaError::PreparedUnderBudget { chars, budget }
            }
            PicoError::Stopped(failure) => PrismaError::Stopped(failure),
        }
    }
}

impl From<PartsError> for PrismaError {
    fn from(error: PartsError) -> Self {
        PrismaError::Parts(error)
    }
}

impl std::fmt::Display for PrismaError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PrismaError::EmptyInput => write!(f, "{}", PicoError::EmptyInput),
            PrismaError::RequestFailed { message, .. }
            | PrismaError::Truncated(message)
            | PrismaError::UnusableReply(message)
            | PrismaError::CondensationFailed(message)
            | PrismaError::InvalidItems(message) => write!(f, "{message}"),
            PrismaError::InvalidSimilarity(value) => {
                write!(f, "{}", PicoError::InvalidSimilarity(*value))
            }
            PrismaError::PreparedOverBudget { chars, budget } => write!(
                f,
                "{}",
                PicoError::PreparedOverBudget {
                    chars: *chars,
                    budget: *budget
                }
            ),
            PrismaError::PreparedUnderBudget { chars, budget } => write!(
                f,
                "{}",
                PicoError::PreparedUnderBudget {
                    chars: *chars,
                    budget: *budget
                }
            ),
            PrismaError::Stopped(failure) => {
                write!(f, "{}", PicoError::Stopped(failure.clone()))
            }
            PrismaError::Parts(error) => write!(f, "{error}"),
        }
    }
}

impl std::error::Error for PrismaError {}

/// A paper made ready for one call: the text whole, or its digest.
///
/// Built by [`Prisma2020Agent::prepare_part`] for one part's items. It keeps
/// the paper as passed, since quotes are located in the paper and never the
/// digest. Hand it to [`Prisma2020Agent::assess_part`] with the same item ids,
/// and to each retry, so a long paper is condensed once per part. A digest
/// records the items it was condensed for, and `assess_part` refuses it for
/// any others; the whole paper serves any items.
#[derive(Debug, Clone, PartialEq)]
pub struct PrismaPrepared {
    paper: PicoPrepared,
    /// The item ids the digest was condensed for; empty when the text is sent
    /// whole, or the digest is the suitability check's.
    condensed_for: Vec<&'static str>,
}

impl PrismaPrepared {
    /// The title, stripped.
    #[must_use]
    pub fn title(&self) -> &str {
        self.paper.title()
    }

    /// The paper as passed, unstripped: citation offsets index it.
    #[must_use]
    pub fn text(&self) -> &str {
        self.paper.text()
    }

    /// What the model is sent: the digest, or the stripped text.
    #[must_use]
    pub fn sent(&self) -> &str {
        self.paper.sent()
    }

    /// The stripped text's length in characters, when it was condensed.
    #[must_use]
    pub fn condensed_from_chars(&self) -> Option<usize> {
        self.paper
            .condensed_from_chars()
            .and_then(|chars| usize::try_from(chars).ok())
    }

    /// The condensation's `ProcessingStatus` value (always `"completed"`),
    /// when condensed.
    #[must_use]
    pub fn condensation_status(&self) -> Option<&str> {
        self.paper.condensation_status()
    }

    /// The item ids the digest was condensed for, in the order given; empty
    /// when the text is sent whole or the digest is the suitability check's.
    #[must_use]
    pub fn condensed_for(&self) -> &[&'static str] {
        &self.condensed_for
    }

    /// Notes on the condensation, carried into the result.
    #[must_use]
    pub fn notes(&self) -> &[String] {
        self.paper.notes()
    }
}

/// The production [`QueryCondenser`] for the PRISMA agent: [`LlmCondenser`]
/// over the PRISMA condensation prompts, built afresh for each query.
pub struct PrismaCondenser<'a> {
    model: &'a dyn ContextModel,
    temperature: f64,
    max_tokens: i64,
    /// Shared, as each condensation builds a condenser of its own.
    progress: Option<Arc<crate::context_processor::ProgressCallback>>,
}

impl<'a> PrismaCondenser<'a> {
    /// Condense through `model`, sampling as the agent it serves would.
    #[must_use]
    pub fn new(model: &'a dyn ContextModel, temperature: f64, max_tokens: i64) -> Self {
        PrismaCondenser {
            model,
            temperature,
            max_tokens,
            progress: None,
        }
    }

    /// Hand every [`ProgressInfo`] a condensation reports to `callback`
    /// (#609); as [`LlmCondenser::with_progress`].
    #[must_use]
    pub fn with_progress(
        mut self,
        callback: impl Fn(&ProgressInfo) + Send + Sync + 'static,
    ) -> Self {
        self.progress = Some(Arc::new(Box::new(callback)));
        self
    }
}

impl QueryCondenser for PrismaCondenser<'_> {
    fn condense(
        &mut self,
        query: &str,
        text: &str,
        label: &str,
        config: &ProcessingConfig,
    ) -> ProcessingResult {
        let templates = PromptTemplates {
            extraction_prompt: PRISMA_CONDENSE_EXTRACTION_PROMPT.to_string(),
            consolidation_prompt: PRISMA_CONDENSE_CONSOLIDATION_PROMPT.to_string(),
        };
        let condenser = match LlmCondenser::with_prompts(
            self.model,
            templates,
            query,
            self.temperature,
            self.max_tokens,
        ) {
            Ok(condenser) => condenser,
            Err(error) => return ProcessingResult::failed(error),
        };
        let mut condenser = match &self.progress {
            Some(progress) => {
                let progress = Arc::clone(progress);
                condenser.with_progress(move |info| progress(info))
            }
            None => condenser,
        };
        condenser.condense(text, label, config)
    }
}

/// The checklist rows of `item_ids`, refusing none, a repeat, or an unknown id.
fn checklist_rows(item_ids: &[&'static str]) -> Result<Vec<&'static ChecklistItem>, PrismaError> {
    if item_ids.is_empty() {
        return Err(PrismaError::InvalidItems(
            "a part needs at least one PRISMA 2020 item".to_string(),
        ));
    }
    let mut rows: Vec<&'static ChecklistItem> = Vec::with_capacity(item_ids.len());
    for &id in item_ids {
        let row = item(id).ok_or_else(|| {
            PrismaError::InvalidItems(format!("item {id:?} is not on the PRISMA 2020 checklist"))
        })?;
        if rows.iter().any(|seen| seen.id == row.id) {
            return Err(PrismaError::InvalidItems(format!(
                "item {id:?} is asked twice"
            )));
        }
        rows.push(row);
    }
    Ok(rows)
}

/// Judges a systematic review against the PRISMA 2020 checklist, a part at a
/// time, every judgement backed by passages of the paper.
///
/// `PicoAgent`'s builder shape. Text over `condense_config.max_context_chars`
/// is condensed by the configured [`QueryCondenser`] (see
/// [`PrismaCondenser`]); with none it is refused as
/// [`PrismaError::CondensationFailed`] rather than sent whole or cut. A stop
/// ([`with_stop`](Self::with_stop)) is read before each model call and each
/// condensation; once set the call returns [`PrismaError::Stopped`], also when
/// it was set during a call that failed.
pub struct Prisma2020Agent<'a> {
    chat: &'a mut dyn JsonChat,
    condenser: Option<&'a mut dyn QueryCondenser>,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    condense_config: ProcessingConfig,
    stop: Option<&'a AtomicBool>,
}

impl<'a> Prisma2020Agent<'a> {
    /// An agent asking through `chat`, with the full-text agents' reply
    /// ceiling ([`DEFAULT_MAX_TOKENS`]: a part's reply is long) and the
    /// Cochrane assessor's other defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        Prisma2020Agent {
            chat,
            condenser: None,
            temperature: DEFAULT_TEMPERATURE,
            max_tokens: DEFAULT_MAX_TOKENS,
            max_retries: DEFAULT_MAX_RETRIES,
            condense_config: ProcessingConfig::default()
                .with_max_context_chars(DEFAULT_CONDENSE_THRESHOLD_CHARS),
            stop: None,
        }
    }

    /// End a call before its next request once `stop` is set (#642); give
    /// the chat and the condenser's model the same stop.
    #[must_use]
    pub fn with_stop(mut self, stop: &'a AtomicBool) -> Self {
        self.stop = Some(stop);
        self
    }

    /// Condense oversized text through `condenser`.
    #[must_use]
    pub fn with_condenser(mut self, condenser: &'a mut dyn QueryCondenser) -> Self {
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

    /// The budget beyond which text is condensed first. Its
    /// `continue_on_error` is overridden to `false` for every condensation.
    #[must_use]
    pub fn with_condense_config(mut self, condense_config: ProcessingConfig) -> Self {
        self.condense_config = condense_config;
        self
    }

    /// Say whether the paper is a systematic review PRISMA 2020 applies to.
    ///
    /// `text` is the abstract or the whole paper; over the budget it is
    /// condensed with [`SUITABILITY_CONDENSE_QUERY`], never cut.
    ///
    /// # Errors
    ///
    /// [`PrismaError`] naming why no answer could be made.
    pub fn check_suitability(
        &mut self,
        title: &str,
        text: &str,
    ) -> Result<Suitability, PrismaError> {
        let prepared = self.prepare(
            title,
            text,
            SUITABILITY_CONDENSE_QUERY,
            SUITABILITY_STAGE,
            &[],
        )?;
        let label = format!(
            "PRISMA 2020 suitability check of {}",
            repr_prefix(prepared.title())
        );
        let mut suitability = self.ask(
            SUITABILITY_TASK_PROMPT,
            SUITABILITY_RESPONSE_FORMAT,
            &prepared,
            read_suitability,
            &label,
        )?;
        suitability.condensed_from_chars = prepared.condensed_from_chars();
        suitability.condensation_status = prepared.condensation_status().map(str::to_string);
        suitability.notes = prepared.notes().to_vec();
        Ok(suitability)
    }

    /// Make the paper ready for the part of `item_ids`: the whole text when it
    /// fits `max_context_chars`, else a digest made with [`part_query`] of
    /// those items. Never cut.
    ///
    /// # Errors
    ///
    /// [`PrismaError::InvalidItems`] before anything else;
    /// [`PrismaError::EmptyInput`]; [`PrismaError::CondensationFailed`] (a
    /// digest whose run did not complete included);
    /// [`PrismaError::RequestFailed`] (a condensation request, #616); or
    /// [`PrismaError::Stopped`].
    pub fn prepare_part(
        &mut self,
        title: &str,
        text: &str,
        item_ids: &[&'static str],
    ) -> Result<PrismaPrepared, PrismaError> {
        let query = part_query(&checklist_rows(item_ids)?);
        self.prepare(title, text, &query, PART_STAGE, item_ids)
    }

    /// Judge the part of `item_ids` over `prepared`, locating every quote in
    /// [`PrismaPrepared::text`], the paper as passed, never the digest.
    ///
    /// Citation offsets are `char` indices into that text. An item the reply
    /// leaves out is listed in [`PartAssessment::missing`], for the caller to
    /// ask again.
    ///
    /// # Errors
    ///
    /// [`PrismaError::InvalidSimilarity`], [`PrismaError::InvalidItems`] (a
    /// digest condensed for other items included, design §6.5),
    /// [`PrismaError::PreparedOverBudget`] or
    /// [`PrismaError::PreparedUnderBudget`], before any request; otherwise
    /// [`PrismaError`] naming why no assessment could be made.
    pub fn assess_part(
        &mut self,
        prepared: &PrismaPrepared,
        item_ids: &[&'static str],
        min_similarity: f64,
    ) -> Result<PartAssessment, PrismaError> {
        check_similarity(min_similarity)?;
        let rows = checklist_rows(item_ids)?;
        // A digest gathered for other items may lack what these need, so a
        // re-check of one item must not reuse its part's (design §6.5).
        if prepared.paper.digest().is_some() && prepared.condensed_for != item_ids {
            return Err(PrismaError::InvalidItems(format!(
                "the prepared paper was condensed for items {:?}, not {item_ids:?}: \
                 prepare it for these items",
                prepared.condensed_for
            )));
        }
        let label = format!(
            "PRISMA 2020 part {} of {}",
            PartPlan {
                item_ids: item_ids.to_vec()
            }
            .label(),
            repr_prefix(prepared.title())
        );
        let mut part = self.ask(
            &part_task(&rows),
            PART_RESPONSE_FORMAT,
            prepared,
            |data| read_part_reply(data, item_ids, prepared.text(), min_similarity),
            &label,
        )?;
        part.condensed_from_chars = prepared.condensed_from_chars();
        part.condensation_status = prepared.condensation_status().map(str::to_string);
        part.notes = prepared.notes().to_vec();
        Ok(part)
    }

    /// Strip, refuse an empty input, and condense an oversized one for
    /// `query`, refusing a digest whose run did not complete; a digest
    /// records `condensed_for`.
    fn prepare(
        &mut self,
        title: &str,
        text: &str,
        query: &str,
        stage: &str,
        condensed_for: &[&'static str],
    ) -> Result<PrismaPrepared, PrismaError> {
        // A failed batch fails the condensation (design §6.3).
        let config = self.condense_config.clone().with_continue_on_error(false);
        let mut bound = self.condenser.as_mut().map(|condenser| Bound {
            inner: &mut **condenser,
            query,
        });
        let prepared = prepare_text(
            bound.as_mut().map(|bound| bound as &mut dyn Condenser),
            title,
            text,
            &config,
            stage,
            self.stop,
        )?;
        if let Some(status) = prepared.condensation_status() {
            if status != ProcessingStatus::Completed.as_str() {
                return Err(PrismaError::CondensationFailed(format!(
                    "a {}-character text was condensed, but the condensation finished \
                     with status {status}, so the digest may be incomplete; nothing is \
                     judged from it",
                    prepared.condensed_from_chars().unwrap_or_default()
                )));
            }
        }
        let condensed_for = if prepared.digest().is_some() {
            condensed_for.to_vec()
        } else {
            Vec::new()
        };
        Ok(PrismaPrepared {
            paper: prepared,
            condensed_for,
        })
    }

    /// Ask `task` over what `prepared` sends, refusing a paper prepared for
    /// another budget before any request.
    fn ask<T>(
        &mut self,
        task: &str,
        response_format: &str,
        prepared: &PrismaPrepared,
        read: impl Fn(&Value) -> Result<T, String>,
        label: &str,
    ) -> Result<T, PrismaError> {
        let sent = sent_within(&prepared.paper, self.condense_config.max_context_chars)?;
        let sampling = Sampling {
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            max_retries: self.max_retries,
        };
        Ok(ask_json(
            &mut *self.chat,
            PRISMA_SYSTEM_PROMPT,
            &wrapped_prompt(task, response_format, prepared.title(), sent),
            sampling,
            self.stop,
            label,
            read,
        )?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::quality::prisma2020_checklist::CHECKLIST;

    #[test]
    fn bad_item_ids_are_refused() {
        for bad in [&[][..], &["99"][..], &["7", "7"][..]] {
            let error = checklist_rows(bad).expect_err("refused");
            assert_eq!(error.kind(), "invalid_items", "{bad:?}");
        }
        assert_eq!(checklist_rows(&["7", "13d"]).expect("rows").len(), 2);
    }

    #[test]
    fn part_prompt_chars_grows_with_the_items() {
        let one = part_prompt_chars(&["7"]);
        let two = part_prompt_chars(&["7", "13d"]);
        assert!(one > PRISMA_SYSTEM_PROMPT.chars().count());
        assert!(two > one);
        let all: Vec<&'static str> = CHECKLIST.iter().map(|row| row.id).collect();
        assert!(part_prompt_chars(&all) > two);
    }

    /// A chat recording the characters each request sends, answering with a
    /// reply no reader accepts.
    struct Measuring(Vec<usize>);

    impl JsonChat for Measuring {
        fn chat_json(
            &mut self,
            messages: &[crate::llm::LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<crate::agents::ChatJsonOutcome, crate::agents::ChatJsonError> {
            self.0
                .push(messages.iter().map(|m| m.content.chars().count()).sum());
            Ok(crate::agents::ChatJsonOutcome {
                value: serde_json::json!({}),
                attempts: 1,
                repaired: false,
            })
        }
    }

    #[test]
    fn suitability_prompt_chars_holds_the_check_prompt() {
        let mut chat = Measuring(Vec::new());
        let _ = Prisma2020Agent::new(&mut chat).check_suitability("", "x");
        assert!(!chat.0.is_empty());
        for sent in chat.0 {
            assert!(sent - 1 <= suitability_prompt_chars(), "{sent}");
        }
        assert!(suitability_prompt_chars() > PRISMA_SYSTEM_PROMPT.chars().count());
    }

    #[test]
    fn part_task_carries_each_item() {
        let rows = checklist_rows(&["7", "13d"]).expect("rows");
        let task = part_task(&rows);
        for row in rows {
            assert!(task.contains(row.wording) && task.contains(row.guidance));
        }
        assert!(!task.contains("{items}"));
    }

    /// A chat that must not be called.
    struct Silent;

    impl JsonChat for Silent {
        fn chat_json(
            &mut self,
            _messages: &[crate::llm::LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<crate::agents::ChatJsonOutcome, crate::agents::ChatJsonError> {
            panic!("no request is expected");
        }
    }

    /// A condenser recording whether it was told to carry on past a failed batch.
    struct Recording(Vec<bool>);

    impl QueryCondenser for Recording {
        fn condense(
            &mut self,
            _query: &str,
            _text: &str,
            _label: &str,
            config: &ProcessingConfig,
        ) -> ProcessingResult {
            self.0.push(config.continue_on_error);
            ProcessingResult::failed("refused")
        }
    }

    #[test]
    fn a_failed_batch_fails_the_condensation() {
        let mut chat = Silent;
        let mut condenser = Recording(Vec::new());
        let error = Prisma2020Agent::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_condense_config(
                ProcessingConfig::default()
                    .with_max_context_chars(10)
                    .with_continue_on_error(true),
            )
            .prepare_part("A review", "a paper longer than ten characters", &["7"])
            .expect_err("refused");
        assert_eq!(error.kind(), "condensation_failed");
        assert_eq!(condenser.0, [false]);
    }

    /// A condenser answering every query with one completed digest.
    struct Completed;

    impl QueryCondenser for Completed {
        fn condense(
            &mut self,
            _query: &str,
            _text: &str,
            _label: &str,
            _config: &ProcessingConfig,
        ) -> ProcessingResult {
            let mut result = ProcessingResult::failed("");
            result.status = ProcessingStatus::Completed;
            result.error_message = None;
            result.final_result = crate::context_processor::ExtractionResult::new("a digest");
            result
        }
    }

    const LONG: &str = "a paper longer than its budget of ten characters";

    #[test]
    fn a_digest_for_other_items_is_refused_before_any_request() {
        // design §6.5: a key item re-checked alone needs its own digest.
        let mut chat = Silent;
        let mut condenser = Completed;
        let mut agent = Prisma2020Agent::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(10));
        let prepared = agent
            .prepare_part("A review", LONG, &["7", "8"])
            .expect("condensed");
        assert_eq!(prepared.condensed_for(), ["7", "8"]);
        for other in [&["7"][..], &["8", "7"][..], &["7", "8", "9"][..]] {
            let error = agent
                .assess_part(&prepared, other, 0.9)
                .expect_err("refused");
            assert_eq!(error.kind(), "invalid_items", "{other:?}");
        }
        let suitability = agent
            .prepare(
                "A review",
                LONG,
                SUITABILITY_CONDENSE_QUERY,
                SUITABILITY_STAGE,
                &[],
            )
            .expect("condensed");
        let error = agent
            .assess_part(&suitability, &["7"], 0.9)
            .expect_err("refused");
        assert_eq!(error.kind(), "invalid_items");
    }

    /// A chat answering every request with an empty part, counting requests.
    struct EmptyPart(usize);

    impl JsonChat for EmptyPart {
        fn chat_json(
            &mut self,
            _messages: &[crate::llm::LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<crate::agents::ChatJsonOutcome, crate::agents::ChatJsonError> {
            self.0 += 1;
            Ok(crate::agents::ChatJsonOutcome {
                value: serde_json::json!({"items": []}),
                attempts: 1,
                repaired: false,
            })
        }
    }

    #[test]
    fn a_whole_paper_serves_any_items() {
        let mut chat = EmptyPart(0);
        let mut agent = Prisma2020Agent::new(&mut chat);
        let prepared = agent
            .prepare_part("A review", LONG, &["7", "8"])
            .expect("whole");
        assert!(prepared.condensed_for().is_empty());
        for subset in [&["7"][..], &["8"][..], &["13d", "7"][..]] {
            let part = agent.assess_part(&prepared, subset, 0.9).expect("assessed");
            assert_eq!(part.missing, subset);
        }
        drop(agent);
        assert_eq!(chat.0, 3);
    }

    #[test]
    fn parts_and_pico_errors_convert() {
        let error = PrismaError::from(PartsError::ItemTooLarge {
            id: "7",
            needed: 2,
            budget: 1,
        });
        assert_eq!(error.kind(), "parts");
        assert_eq!(
            PrismaError::from(PicoError::Stopped(None)).kind(),
            "stopped"
        );
    }
}
