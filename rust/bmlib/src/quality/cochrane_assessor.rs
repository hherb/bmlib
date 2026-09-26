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

//! Cochrane-aligned study assessment.
//!
//! A port of `bmlib/quality/cochrane_assessor.py`. Produces a
//! [`CochraneStudyAssessment`] — the Cochrane Handbook's study-characteristics
//! table plus the nine-domain Risk of Bias assessment — from a paper's title and
//! text.
//!
//! Text larger than one context is reduced to an evidence digest by
//! `bmlib.context_processor` first, so the nine-domain judgement is always made
//! once, over content that fits. Truncation is not an option here: allocation
//! concealment and blinding live in Methods and attrition in Results, so a
//! head-of-string cut drops exactly the evidence the domains are about. "Fits" is
//! checked by **measuring the digest**, not by trusting the harness's own
//! `ProcessingStatus` — a status of `TRUNCATED` names the harness's recursion
//! ceiling, not the size of what it produced, so a digest that is still
//! oversized after condensing is refused rather than judged.
//!
//! Reference: Cochrane Handbook for Systematic Reviews of Interventions
//! (<https://training.cochrane.org/handbook>).
//!
//! # The condensation seam
//!
//! The Python condenses by constructing an `LLMChunkProcessor` over itself.
//! `LLMChunkProcessor` is not ported (it follows the `llm` package), so the
//! map-reduce is a [`Condenser`] the caller supplies and this module keeps the
//! **rules** the Python applies to its result: a failed run, an empty digest and
//! an oversized digest are all refused, and a run that did not complete
//! cleanly attaches a note saying so. With no condenser configured an oversized
//! paper is refused rather than sent whole — see [`CochraneAssessor::assess`].

use crate::context_processor::{ProcessingConfig, ProcessingResult, ProcessingStatus};
use crate::llm::LLMMessage;
use crate::quality::agent_chat::{format_template, JsonChat};
use crate::quality::cochrane_models::{
    CochraneInterventions, CochraneNotes, CochraneOutcomes, CochraneParticipants,
    CochraneRiskOfBias, CochraneStudyAssessment, CochraneStudyCharacteristics, RiskOfBiasItem,
    RiskOfBiasJudgement, ROB_JUDGEMENT_UNCLEAR,
};
use crate::quality::llm_parsers::as_mapping;
use serde_json::Value;

/// Text longer than this is condensed before assessment.
///
/// Roughly 12k tokens, so a whole research paper usually passes through
/// uncondensed while still leaving room in a 32k-token window for the
/// ~4k-character prompt and a 4096-token answer. The context processor's own
/// 4000-character default would condense almost every full text and most long
/// abstracts.
pub const DEFAULT_CONDENSE_THRESHOLD_CHARS: usize = 48_000;

/// Whole assessment attempts before giving up.
///
/// `chat_json` retries transport and JSON-shape failures inside each one; this
/// outer bound covers a reply that **parses but carries no risk-of-bias
/// section**. Two, not three: a model that omits it twice has misread the
/// prompt, and the bound keeps the worst case at six model calls rather than
/// nine.
const ASSESSMENT_ATTEMPTS: usize = 2;

/// Stand-in support text for a domain the model did not report.
const NO_INFORMATION: &str = "Not reported or insufficient information to assess";

/// The default sampling temperature, as the Python's `__init__` sets it.
pub const DEFAULT_TEMPERATURE: f64 = 0.1;

/// The default output ceiling, as the Python's `__init__` sets it.
///
/// The reply carries nine judgements with their supporting text plus the
/// characteristics table, so it is substantially larger than Tier 3's.
pub const DEFAULT_MAX_TOKENS: i64 = 4096;

/// `BaseAgent.chat_json`'s own default, which the Python tiers do not override.
pub const DEFAULT_MAX_RETRIES: usize = 3;

/// The nine Cochrane domains: response key, domain name, bias type, outcome type.
///
/// One table rather than nine hand-written constructor calls, and the source of
/// the `bias_type` values [`crate::quality::collapse_risk_of_bias`] groups by.
pub const ROB_DOMAINS: [(&str, &str, &str, Option<&str>); 9] = [
    (
        "random_sequence_generation",
        "Random sequence generation",
        "selection bias",
        None,
    ),
    (
        "allocation_concealment",
        "Allocation concealment",
        "selection bias",
        None,
    ),
    (
        "baseline_outcome_measurements",
        "Baseline outcome measurements",
        "selection bias",
        None,
    ),
    (
        "baseline_characteristics",
        "Baseline characteristics",
        "selection bias",
        None,
    ),
    (
        "blinding_participants_personnel",
        "Blinding of participants and personnel",
        "performance bias",
        None,
    ),
    (
        "blinding_outcome_assessment_subjective",
        "Blinding of outcome assessment (subjective outcomes)",
        "detection bias",
        Some("subjective"),
    ),
    (
        "blinding_outcome_assessment_objective",
        "Blinding of outcome assessment (objective outcomes)",
        "detection bias",
        Some("objective"),
    ),
    (
        "incomplete_outcome_data",
        "Incomplete outcome data",
        "attrition bias",
        None,
    ),
    (
        "selective_reporting",
        "Selective reporting",
        "reporting bias",
        None,
    ),
];

/// The system prompt, copied byte-for-byte from the Python.
pub const COCHRANE_SYSTEM_PROMPT: &str = "\
You are a medical research methodologist specialising in systematic reviews \
and Cochrane methodology.

CRITICAL RULES:
1. Extract ONLY information that is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. For anything not reported, use \"Not reported\" or \"Details not reported\"
4. Assess THIS study's methodology, not studies it references
5. Return ONLY valid JSON, no explanation";

/// The task prompt, copied byte-for-byte from the Python.
pub const COCHRANE_TASK_PROMPT: &str = "\
Conduct a complete Cochrane-style assessment of the study below.

Extract the STUDY CHARACTERISTICS table: methods (the study design, e.g.
\"Parallel randomised trial\"); participants (setting, population, inclusion and
exclusion criteria, total participants, group sizes); interventions
(description, control, duration); outcomes (description, primary, secondary,
timepoints); and notes (follow-up periods, funding, conflicts of interest,
ethical approval, trial registration, publication status).

Then judge the NINE Cochrane RISK OF BIAS domains. For each, give a judgement
of exactly \"Low risk\", \"High risk\" or \"Unclear risk\", plus the text supporting
it:

a) Random sequence generation (selection bias) — low if adequate
   (computer-generated, random number table), high if inadequate (alternation,
   birth date), unclear if not reported.
b) Allocation concealment (selection bias) — low if adequate (central
   allocation, sealed opaque envelopes), high if open lists, unclear if not
   reported.
c) Baseline outcome measurements (selection bias) — low if similar at
   baseline, high if they differed materially, unclear if not reported.
d) Baseline characteristics (selection bias) — low if balanced, high if
   important imbalances, unclear if not reported.
e) Blinding of participants and personnel (performance bias) — low if blinded
   or the outcome is unlikely to be affected by its absence.
f) Blinding of outcome assessment, SUBJECTIVE outcomes (detection bias) —
   patient-reported measures, quality of life.
g) Blinding of outcome assessment, OBJECTIVE outcomes (detection bias) —
   mortality, laboratory values.
h) Incomplete outcome data (attrition bias) — low if dropout is low, balanced
   across groups and handled appropriately.
i) Selective reporting (reporting bias) — low if every pre-specified outcome
   is reported.";

/// The response-format prompt, copied byte-for-byte from the Python.
pub const COCHRANE_RESPONSE_FORMAT: &str = "\
Respond with JSON in exactly this shape:

{
    \"study_characteristics\": {
        \"methods\": \"study design description\",
        \"participants\": {
            \"setting\": \"location/country\",
            \"population\": \"description of participants\",
            \"inclusion_criteria\": [\"criterion 1\"],
            \"exclusion_criteria\": [\"criterion 1\"],
            \"total_participants\": 45,
            \"group_sizes\": {\"intervention\": 25, \"control\": 20},
            \"baseline_characteristics_reported\": true
        },
        \"interventions\": {
            \"description\": \"intervention description\",
            \"intervention_groups\": [\"group 1\"],
            \"control_description\": \"control description\",
            \"duration\": \"duration\"
        },
        \"outcomes\": {
            \"description\": \"outcomes measured\",
            \"primary_outcomes\": [\"outcome 1\"],
            \"secondary_outcomes\": [\"outcome 1\"],
            \"outcome_timepoints\": [\"1 month\", \"3 months\"]
        },
        \"notes\": {
            \"follow_up_periods\": [\"6 months\", \"12 months\"],
            \"funding_source\": \"funding info\",
            \"conflicts_of_interest\": \"conflicts\",
            \"ethical_approval\": \"approval status\",
            \"trial_registration\": \"registration id\",
            \"publication_status\": \"full publication\",
            \"additional_notes\": [\"note 1\"]
        }
    },
    \"risk_of_bias\": {
        \"random_sequence_generation\": {\"judgement\": \"Low risk\", \"support_for_judgement\": \"...\"},
        \"allocation_concealment\": {\"judgement\": \"Unclear risk\", \"support_for_judgement\": \"...\"},
        \"baseline_outcome_measurements\": {\"judgement\": \"Low risk\", \"support_for_judgement\": \"...\"},
        \"baseline_characteristics\": {\"judgement\": \"Low risk\", \"support_for_judgement\": \"...\"},
        \"blinding_participants_personnel\": {
            \"judgement\": \"High risk\", \"support_for_judgement\": \"...\"
        },
        \"blinding_outcome_assessment_subjective\": {
            \"judgement\": \"High risk\", \"support_for_judgement\": \"...\"
        },
        \"blinding_outcome_assessment_objective\": {
            \"judgement\": \"Low risk\", \"support_for_judgement\": \"...\"
        },
        \"incomplete_outcome_data\": {\"judgement\": \"Low risk\", \"support_for_judgement\": \"...\"},
        \"selective_reporting\": {\"judgement\": \"Unclear risk\", \"support_for_judgement\": \"...\"}
    },
    \"overall_confidence\": 0.7,
    \"evidence_level\": \"Level 2 (moderate-high)\",
    \"assessment_notes\": [\"note 1\"]
}

Use null for any field the text does not report. Every one of the nine
risk_of_bias domains must be present. Respond ONLY with valid JSON.";

/// What the digest must preserve.
///
/// A digest that drops these is a digest the assessment pass cannot judge from.
pub const CONDENSE_QUERY: &str = "Everything needed for a Cochrane assessment: the \
study design; the setting, population and group sizes; the interventions and \
controls; the outcomes measured and when; funding, conflicts of interest, \
ethical approval and trial registration; and the reported detail behind each \
risk of bias domain — how the randomisation sequence was generated, how \
allocation was concealed, whether groups were comparable at baseline, who was \
blinded to what, how much outcome data was missing and how it was handled, and \
whether every pre-specified outcome was reported.";

/// The map-stage prompt, copied byte-for-byte from the Python.
///
/// Rendered with [`render_condense_extraction`]; the placeholders are `{query}`
/// and `{content}`, as the Python names them.
pub const CONDENSE_EXTRACTION_PROMPT: &str = "\
Extract, verbatim where possible, every passage of this paper that bears on \
the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Quote or closely paraphrase what the text actually says
- Keep numbers, group sizes, timepoints and named funders exactly
- Say nothing about what the text does not report — omissions are recorded by
  the assessment step, not invented here
- Return plain text

Extracted evidence:";

/// The reduce-stage prompt, copied byte-for-byte from the Python.
///
/// Rendered with [`render_condense_consolidation`].
pub const CONDENSE_CONSOLIDATION_PROMPT: &str = "\
Merge these extracted passages into one evidence summary.

Needed: {query}

Extracted evidence:
{content}

INSTRUCTIONS:
- Merge overlapping passages, keeping every distinct detail
- Preserve numbers, group sizes, timepoints and named funders exactly
- Keep the methodological detail even where it seems minor: it is what the
  risk of bias judgements rest on
- Return plain text

Consolidated evidence:";

/// Render the map-stage prompt.
#[must_use]
pub fn render_condense_extraction(query: &str, content: &str) -> String {
    format_template(
        CONDENSE_EXTRACTION_PROMPT,
        &[("query", query), ("content", content)],
    )
}

/// Render the reduce-stage prompt.
#[must_use]
pub fn render_condense_consolidation(query: &str, content: &str) -> String {
    format_template(
        CONDENSE_CONSOLIDATION_PROMPT,
        &[("query", query), ("content", content)],
    )
}

/// Reduces oversized text to an evidence digest that fits one context.
///
/// The Python's `_condense` runs `LLMChunkProcessor` with the assessor as its
/// agent. That class is not ported, so a caller supplies the map-reduce and
/// [`CochraneAssessor`] applies the Python's checks to whatever it returns. The
/// two rendered prompts above are the ones the Python passes, so an
/// implementation that uses them is sending the same bytes.
pub trait Condenser {
    /// Condense `text` for the study named by `label`.
    ///
    /// `config` is the assessor's own [`ProcessingConfig`], so an implementation
    /// batches to the same budget the assessor measures the digest against.
    fn condense(&mut self, text: &str, label: &str, config: &ProcessingConfig) -> ProcessingResult;
}

/// The progress callback [`CochraneAssessor::assess_batch`] calls.
///
/// Called `(current, total, title)` before each study, as the Python's
/// `progress_callback` is. Named so the batch signature reads as one argument
/// rather than a nested type.
pub type ProgressCallback<'a> = &'a mut dyn FnMut(usize, usize, &str);

/// The counters [`CochraneAssessor::get_stats`] reports.
#[derive(Debug, Clone, PartialEq)]
pub struct CochraneStats {
    /// Every [`CochraneAssessor::assess`] call.
    pub total_assessments: u64,
    /// The calls that returned an assessment.
    pub successful_assessments: u64,
    /// The calls that did not.
    pub failed_assessments: u64,
    /// A **subset** of the failures: the ones that were an unusable reply
    /// rather than a transport error or a rejected confidence.
    pub parse_failures: u64,
    /// `successful_assessments / total_assessments`, or 0.0 when none were made.
    pub success_rate: f64,
}

/// The keyword arguments of [`CochraneAssessor::assess`].
#[derive(Debug, Clone, Default)]
pub struct AssessOptions<'a> {
    /// Cochrane's `"Author Year"` study label. Unset, it falls back to
    /// `"document {document_id}"` and then to the title; no surname is guessed
    /// from an author list.
    pub study_id: Option<&'a str>,
    /// PubMed id, recorded on the characteristics table.
    pub pmid: Option<&'a str>,
    /// DOI, recorded on the characteristics table.
    pub doi: Option<&'a str>,
    /// The caller's own row id.
    pub document_id: Option<i64>,
    /// Reject an assessment whose `overall_confidence` falls below this. Zero,
    /// the default, rejects nothing.
    pub min_confidence: f64,
}

/// One study for [`CochraneAssessor::assess_batch`].
///
/// The Python's batch helper takes dicts keyed by `assess`'s own parameter
/// names; this is the typed form of the same record.
#[derive(Debug, Clone, Default)]
pub struct StudyInput {
    /// The paper's title.
    pub title: String,
    /// The text to assess — full text or abstract.
    pub text: Option<String>,
    /// Cochrane's `"Author Year"` study label.
    pub study_id: Option<String>,
    /// PubMed id.
    pub pmid: Option<String>,
    /// DOI.
    pub doi: Option<String>,
    /// The caller's own row id.
    pub document_id: Option<i64>,
}

/// Produces Cochrane-aligned assessments of individual studies.
///
/// The Python's constructor takes an `LLMClient`, a model string, a
/// `TemplateEngine` and a `ProcessingConfig`. Here the client is the
/// [`JsonChat`] seam (the model string lives on the implementation, e.g.
/// [`crate::quality::agent_chat::LlmChat`]), the template engine is unused
/// because this agent's prompts are module constants, and the condensation
/// configuration is [`CochraneAssessor::with_condense_config`].
pub struct CochraneAssessor<'a> {
    chat: &'a mut dyn JsonChat,
    condenser: Option<&'a mut dyn Condenser>,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    condense_config: ProcessingConfig,
    total_assessments: u64,
    successful_assessments: u64,
    failed_assessments: u64,
    parse_failures: u64,
}

impl<'a> CochraneAssessor<'a> {
    /// An assessor over `chat`, with the Python's sampling defaults and
    /// condensation threshold.
    ///
    /// No [`Condenser`] is configured, so text longer than the threshold is
    /// refused until [`CochraneAssessor::with_condenser`] supplies one.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        CochraneAssessor {
            chat,
            condenser: None,
            temperature: DEFAULT_TEMPERATURE,
            max_tokens: DEFAULT_MAX_TOKENS,
            max_retries: DEFAULT_MAX_RETRIES,
            condense_config: ProcessingConfig::default()
                .with_max_context_chars(DEFAULT_CONDENSE_THRESHOLD_CHARS),
            total_assessments: 0,
            successful_assessments: 0,
            failed_assessments: 0,
            parse_failures: 0,
        }
    }

    /// Configure the map-reduce pass for oversized text.
    #[must_use]
    pub fn with_condenser(mut self, condenser: &'a mut dyn Condenser) -> Self {
        self.condenser = Some(condenser);
        self
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

    /// Override the condensation configuration.
    #[must_use]
    pub fn with_condense_config(mut self, condense_config: ProcessingConfig) -> Self {
        self.condense_config = condense_config;
        self
    }

    /// Assess one study against the Cochrane template.
    ///
    /// Either field may be `None`. With both missing there is nothing to assess
    /// and no model call is made — left to itself the model returns a
    /// fully-formed nine-domain judgement for a paper it was told nothing about.
    ///
    /// The caller chooses what *text* is: full text gives a real risk-of-bias
    /// assessment, an abstract gives a weak one. Text longer than the
    /// condensation threshold is condensed first, and the result says so through
    /// [`CochraneStudyAssessment::condensed_from_chars`] and
    /// [`CochraneStudyAssessment::condensation_status`]. A digest that still does
    /// not fit the budget after condensing is not judged, and neither is an
    /// oversized paper when no [`Condenser`] is configured.
    ///
    /// `min_confidence` rejects only a **reported** confidence below the bar: an
    /// assessment whose confidence could not be parsed (`overall_confidence` is
    /// `None`) is kept regardless of how high the bar is set. An unknown
    /// confidence is not a low one — the same rule keeps
    /// `transparency::models::calculate_risk_level` from treating an
    /// undetermined COI disclosure as a missing one.
    ///
    /// Returns the assessment, or `None` if it could not be made. `None` rather
    /// than an all-`"Unclear risk"` stand-in: that would be indistinguishable
    /// from a real assessment in which the model genuinely judged every domain
    /// unclear, and anything persisting results would store the fabrication
    /// permanently.
    pub fn assess(
        &mut self,
        title: Option<&str>,
        text: Option<&str>,
        options: AssessOptions<'_>,
    ) -> Option<CochraneStudyAssessment> {
        let title = title.unwrap_or("").trim();
        let text = text.unwrap_or("").trim();
        let label = options
            .study_id
            .filter(|id| !id.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| match options.document_id {
                Some(document_id) => format!("document {document_id}"),
                None => title.chars().take(60).collect(),
            });

        self.total_assessments += 1;

        if title.is_empty() && text.is_empty() {
            self.failed_assessments += 1;
            return None;
        }

        let mut notes: Vec<String> = Vec::new();
        let mut condensed_from: Option<i64> = None;
        let mut condensation_status: Option<String> = None;
        let mut digest: Option<String> = None;

        // `len(text)` is a character count, not a byte count.
        let text_length = text.chars().count();
        if text_length > self.condense_config.max_context_chars {
            match self.condense(text, &label) {
                Some((digest_text, digest_notes, status)) => {
                    notes = digest_notes;
                    condensation_status = Some(status.as_str().to_string());
                    condensed_from = Some(text_length as i64);
                    digest = Some(digest_text);
                }
                None => {
                    self.failed_assessments += 1;
                    return None;
                }
            }
        }

        let text_to_send: &str = digest.as_deref().unwrap_or(text);
        let mut assessment = self.attempt_assessment(
            title,
            text_to_send,
            &notes,
            condensed_from,
            condensation_status.as_deref(),
        )?;

        assessment.study_characteristics.study_id =
            resolve_study_id(options.study_id, options.document_id, title);
        assessment.study_characteristics.document_id = options.document_id;
        assessment.study_characteristics.document_title = if title.is_empty() {
            None
        } else {
            Some(title.to_string())
        };
        assessment.study_characteristics.pmid = options.pmid.map(str::to_string);
        assessment.study_characteristics.doi = options.doi.map(str::to_string);

        if let Some(confidence) = assessment.overall_confidence {
            if confidence < options.min_confidence {
                self.failed_assessments += 1;
                return None;
            }
        }

        self.successful_assessments += 1;
        Some(assessment)
    }

    /// Assess several studies, keeping the ones that succeeded.
    ///
    /// A convenience loop over [`CochraneAssessor::assess`]. `progress` is
    /// called `(current, total, title)` before each study. A study that could
    /// not be assessed is absent from the result; [`CochraneAssessor::get_stats`]
    /// counts it.
    pub fn assess_batch(
        &mut self,
        studies: &[StudyInput],
        min_confidence: f64,
        mut progress: Option<ProgressCallback<'_>>,
    ) -> Vec<CochraneStudyAssessment> {
        let total = studies.len();
        let mut assessments: Vec<CochraneStudyAssessment> = Vec::new();

        for (index, study) in studies.iter().enumerate() {
            if let Some(callback) = progress.as_deref_mut() {
                callback(index + 1, total, &study.title);
            }
            let options = AssessOptions {
                study_id: study.study_id.as_deref(),
                pmid: study.pmid.as_deref(),
                doi: study.doi.as_deref(),
                document_id: study.document_id,
                min_confidence,
            };
            if let Some(assessment) =
                self.assess(Some(&study.title), study.text.as_deref(), options)
            {
                assessments.push(assessment);
            }
        }

        assessments
    }

    /// Report what this assessor has done.
    ///
    /// `total_assessments` counts **every** [`CochraneAssessor::assess`] call, so
    /// `successful_assessments + failed_assessments == total_assessments` and
    /// `success_rate` can report a failure. (The Python's own comment records
    /// that upstream incremented the total only on the success path, after every
    /// failure had returned, so its `success_rate` could only ever be 1.0.)
    /// `parse_failures` is a subset of the failures, naming the ones that were an
    /// unusable reply rather than a transport error or a rejected confidence.
    #[must_use]
    pub fn get_stats(&self) -> CochraneStats {
        CochraneStats {
            total_assessments: self.total_assessments,
            successful_assessments: self.successful_assessments,
            failed_assessments: self.failed_assessments,
            parse_failures: self.parse_failures,
            success_rate: if self.total_assessments == 0 {
                0.0
            } else {
                self.successful_assessments as f64 / self.total_assessments as f64
            },
        }
    }

    /// Run the model and parse its reply, retrying a structural failure.
    ///
    /// Failure accounting lives in here, so a `None` return has always recorded
    /// exactly one outcome — the invariant `successful + failed == total`
    /// depends on it.
    ///
    /// The Python also takes the study's `label`, which it uses in log lines
    /// only; the port's tiers have no logger, so it is not carried.
    fn attempt_assessment(
        &mut self,
        title: &str,
        text: &str,
        notes: &[String],
        condensed_from: Option<i64>,
        condensation_status: Option<&str>,
    ) -> Option<CochraneStudyAssessment> {
        let prompt = format!(
            "{COCHRANE_TASK_PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{text}\n\n\
             {COCHRANE_RESPONSE_FORMAT}"
        );
        let messages = [
            LLMMessage::system(COCHRANE_SYSTEM_PROMPT),
            LLMMessage::user(prompt),
        ];

        for _attempt in 0..ASSESSMENT_ATTEMPTS {
            let data = match self.chat.chat_json(
                &messages,
                self.temperature,
                self.max_tokens,
                self.max_retries,
                true,
            ) {
                Ok(outcome) => outcome.value,
                Err(_) => {
                    // The Python logs and counts a failure; there is no second
                    // whole-assessment attempt, because `chat_json` has already
                    // spent its own retries on the transport.
                    self.failed_assessments += 1;
                    return None;
                }
            };

            // The Python catches `(ValueError, TypeError, AttributeError)` here
            // — not `Exception` — because the only documented failure is the
            // missing risk_of_bias section, and a `KeyError` escaping would be a
            // defect worth seeing. The port's parser reports its refusals as an
            // `Err`, so every refusal is retried and nothing escapes.
            //
            // // QUIRK: the narrow catch is reproduced as "any refusal retries"
            // rather than by classifying the parser's errors. `Exception` and
            // these three are the same set for every refusal the parser can
            // produce, so the port would need a classified error type to tell
            // them apart — and the one difference, an error type that ought to
            // escape, is unreachable from JSON.
            match parse_cochrane_assessment(&data, notes, condensed_from, condensation_status) {
                Ok(assessment) => return Some(assessment),
                Err(_) => continue,
            }
        }

        self.parse_failures += 1;
        self.failed_assessments += 1;
        None
    }

    /// Reduce oversized text to an evidence digest that fits one context.
    ///
    /// Returns `(digest, notes, status)`, or `None` when no condenser is
    /// configured, the run failed, produced nothing to judge, or produced a
    /// digest that still does not fit the budget.
    fn condense(
        &mut self,
        text: &str,
        label: &str,
    ) -> Option<(String, Vec<String>, ProcessingStatus)> {
        let condenser = self.condenser.as_mut()?;
        let result = condenser.condense(text, label, &self.condense_config);

        if result.status == ProcessingStatus::Failed {
            return None;
        }

        let digest = result.final_result.content.trim().to_string();
        if digest.is_empty() {
            // The Cochrane prompt over an empty string returns a confident
            // nine-domain assessment of no paper at all.
            return None;
        }

        // `result.status` says how the harness's own run ended — `truncated`
        // means the recursion ceiling was hit, `partial` means a batch failed —
        // it does not say whether the digest that came out the other end
        // actually fits. In particular `truncated` names the recursion ceiling,
        // not the content size: the harness returns whatever the last level held
        // once `max_recursion_depth` is reached, oversized or not. So this checks
        // the same thing `context_processor` checks about itself — the measured
        // length of what is about to be sent — rather than trusting the status to
        // imply it; a status check would not have caught a digest that overflows
        // the budget it was supposed to fit.
        if digest.chars().count() > self.condense_config.max_context_chars {
            return None;
        }

        let mut notes: Vec<String> = Vec::new();
        if result.status != ProcessingStatus::Completed {
            notes.push(format!(
                "Source text was condensed before assessment; the condensation finished with \
                 status {}, so the digest may be incomplete.",
                result.status.as_str()
            ));
        }
        Some((digest, notes, result.status))
    }
}

/// Pick the Cochrane study label, without guessing a surname.
///
/// // QUIRK: the fallbacks are Python's two different emptiness tests. An empty
/// `study_id` string falls back (`if study_id:` is truthiness), but a
/// `document_id` of **0** does not (`if document_id is not None` is presence),
/// so a caller whose row ids start at 0 gets `"Study 0"` rather than the title.
/// Reproduced because the two tests are what the Python wrote, and a port that
/// unified them would relabel a study.
fn resolve_study_id(study_id: Option<&str>, document_id: Option<i64>, title: &str) -> String {
    if let Some(study_id) = study_id.filter(|id| !id.is_empty()) {
        return study_id.to_string();
    }
    if let Some(document_id) = document_id {
        return format!("Study {document_id}");
    }
    if title.is_empty() {
        "Unknown study".to_string()
    } else {
        title.to_string()
    }
}

/// Build a [`CochraneStudyAssessment`] from the model's parsed reply.
///
/// Extracted as a pure function of the parsed JSON, the way
/// [`crate::quality::llm_parsers`] extracts the other two tiers' readers, so the
/// rules are testable — and differentially testable against Python — without a
/// model.
///
/// # Errors
///
/// If the reply carries no risk-of-bias section. Nine fabricated `"Unclear
/// risk"` defaults would be indistinguishable from a real assessment.
pub fn parse_cochrane_assessment(
    data: &Value,
    notes: &[String],
    condensed_from_chars: Option<i64>,
    condensation_status: Option<&str>,
) -> Result<CochraneStudyAssessment, String> {
    let rob_data = match data.get("risk_of_bias") {
        Some(Value::Object(map)) if !map.is_empty() => Value::Object(map.clone()),
        _ => return Err("the response carries no risk_of_bias section".to_string()),
    };

    // `_as_dict`'s rule, which is the one #295 generalises: a model that answers
    // `null` or a bare string for a whole section must not take the assessment
    // down with it. The section's own reader then supplies its `"Not reported"`
    // defaults. This is `llm_parsers::as_mapping`, not a second spelling of it.
    let sc_data = as_mapping(data.get("study_characteristics"));

    let characteristics = CochraneStudyCharacteristics::new(
        "", // replaced by the caller
        or_default_text(&sc_data, "methods", "Not reported"),
        CochraneParticipants::from_json(&as_mapping(sc_data.get("participants"))),
        CochraneInterventions::from_json(&as_mapping(sc_data.get("interventions"))),
        CochraneOutcomes::from_json(&as_mapping(sc_data.get("outcomes"))),
        CochraneNotes::from_json(&as_mapping(sc_data.get("notes"))),
    );

    // Python includes the model's list **as it stands** when it is a list and
    // ignores anything else; this keeps the strings and drops a non-string
    // member, the same narrowing `llm_parsers::as_string_list` makes and for the
    // same reason — a number in a list of notes is not a note.
    let model_notes: Vec<String> = match data.get("assessment_notes") {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect(),
        _ => Vec::new(),
    };
    let all_notes: Vec<String> = notes.iter().cloned().chain(model_notes).collect();

    let mut assessment =
        CochraneStudyAssessment::new(characteristics, parse_risk_of_bias(&rob_data));
    assessment.overall_confidence = clamped_confidence(data.get("overall_confidence"));
    // The field is annotated `str | None` in the Python and the prompt asks for
    // one of seven strings, so a number here is out of contract and reads as
    // unstated rather than being stringified into a level nobody named.
    assessment.evidence_level = data
        .get("evidence_level")
        .and_then(Value::as_str)
        .map(str::to_string);
    assessment.assessment_notes = if all_notes.is_empty() {
        None
    } else {
        Some(all_notes)
    };
    assessment.condensed_from_chars = condensed_from_chars;
    assessment.condensation_status = condensation_status.map(str::to_string);

    Ok(assessment)
}

/// Build the nine-domain assessment from the model's `risk_of_bias`.
///
/// Every judgement is normalised through [`RiskOfBiasJudgement::from_string`].
/// Writing the model's raw string through — as upstream did — stores an invalid
/// value for a model that answers `"low"` rather than `"Low risk"`, and
/// [`CochraneRiskOfBias::summary_counts`] then skips that domain entirely,
/// silently reporting eight of nine.
///
/// A domain the model omitted defaults to `"Unclear risk"`. That is honest
/// per-domain degradation of an otherwise good answer; a missing **section** is
/// rejected by the caller instead.
fn parse_risk_of_bias(rob_data: &Value) -> CochraneRiskOfBias {
    let mut items: Vec<RiskOfBiasItem> = Vec::with_capacity(ROB_DOMAINS.len());
    for (key, domain, bias_type, outcome_type) in ROB_DOMAINS {
        let raw = as_mapping(rob_data.get(key));
        let judgement = RiskOfBiasJudgement::from_string(&or_default_text(
            &raw,
            "judgement",
            ROB_JUDGEMENT_UNCLEAR,
        ))
        .as_str()
        .to_string();
        items.push(RiskOfBiasItem::new(
            domain,
            bias_type,
            judgement,
            or_default_text(&raw, "support_for_judgement", NO_INFORMATION),
            outcome_type.map(str::to_string),
        ));
    }

    let mut it = items.into_iter();
    CochraneRiskOfBias {
        random_sequence_generation: it.next().expect("nine domains"),
        allocation_concealment: it.next().expect("nine domains"),
        baseline_outcome_measurements: it.next().expect("nine domains"),
        baseline_characteristics: it.next().expect("nine domains"),
        blinding_participants_personnel: it.next().expect("nine domains"),
        blinding_outcome_assessment_subjective: it.next().expect("nine domains"),
        blinding_outcome_assessment_objective: it.next().expect("nine domains"),
        incomplete_outcome_data: it.next().expect("nine domains"),
        selective_reporting: it.next().expect("nine domains"),
    }
}

/// Read the model's confidence, clamped to 0.0–1.0.
///
/// A model reporting 1.4 would outrank every honest result and defeat
/// `min_confidence`. An unusable value becomes `None` rather than a fabricated
/// number.
///
/// The read is Python's `float(value)`, **booleans included**: `float(True)` is
/// `1.0` and `float(False)` is `0.0`, so a model answering `true` for its
/// confidence has answered 1.0 and is recorded as certain. Reproduced rather
/// than tidied, because a port that read a boolean as "not answered" would
/// silently *keep* an assessment that `min_confidence` was set to reject.
///
/// // QUIRK: `"overall_confidence": true` is read as **maximum** confidence —
/// `float(True) == 1.0` — where `false` is 0.0 and is rejected by any real bar.
/// The prompt asks for `<0.0 to 1.0>` and the field is annotated `float | None`,
/// so a boolean is out of contract; Python's `float()` accepts it anyway. Not
/// "not answered": the port records the value Python recorded.
fn clamped_confidence(value: Option<&Value>) -> Option<f64> {
    let value = value?;
    if value.is_null() {
        return None;
    }
    let raw = match value {
        Value::Number(number) => number.as_f64()?,
        Value::Bool(flag) => {
            if *flag {
                1.0
            } else {
                0.0
            }
        }
        Value::String(text) => text.trim().parse::<f64>().ok()?,
        // `float()` raises TypeError for a list or an object — which Python
        // catches into `None` and a warning.
        _ => return None,
    };
    // Python's `min(1.0, max(0.0, x))`, not `f64::clamp`: `clamp` propagates a
    // NaN where Python's two-argument `max` returns its first argument, so a
    // model reporting `NaN` is recorded as 0.0 by the Python.
    Some(py_min(1.0, py_max(0.0, raw)))
}

/// Python's two-argument `max` for one comparison: `b if b > a else a`.
///
/// The order matters only for NaN, which is exactly why it is spelled out.
///
/// // QUIRK: a `"overall_confidence": "nan"` is recorded as **0.0**, because
/// `max(0.0, nan)` keeps its first argument (every comparison against NaN is
/// false) and `min(1.0, 0.0)` is 0.0. `f64::clamp` would have propagated the NaN
/// and a `min`/`max` written in the other argument order would have returned 1.0
/// — certainty — for a value that is not a number at all.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's two-argument `min`: `b if b < a else a`.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python's `x or default` for a text field.
///
/// `or` is **truthiness**, not presence: an empty string, a `0`, a `false` and
/// an empty list all take the default, and anything else is rendered by
/// [`python_str`].
///
/// // QUIRK: a judgement of `5` becomes the **text** `"5"` and then, through
/// [`RiskOfBiasJudgement::from_string`], `"Unclear risk"`; a
/// `support_for_judgement` of `12` becomes the support text `"12"`, and one of
/// `true` becomes `"True"`. Python writes these through `str()`, and a port that
/// read a non-string as "not answered" would record a domain's support as the
/// "Not reported" default where the Python recorded the model's number as if it
/// were prose.
fn or_default_text(section: &Value, key: &str, default: &str) -> String {
    match section.get(key) {
        Some(value) if is_truthy(value) => python_str(value),
        _ => default.to_string(),
    }
}

/// Python's truthiness for a decoded JSON value.
fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_none_or(|f| f != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Python's `str()` for a decoded JSON value.
///
/// Reproduced because the Python writes several model fields through `str(...)`:
/// a number becomes `"45"` and a boolean `"True"`. A **list or object** is
/// approximated by its JSON text, which differs from Python's `repr` inside a
/// nested string's quoting — the same caveat the full-text service's copy
/// carries — and a float's exponent is spelled `1e100` where Python writes
/// `1e+100`.
///
/// // QUIRK: `str({"a": 1})` is Python's `"{'a': 1}"` (single quotes, spaces
/// after the colons) where this returns `{"a":1}`. The fields that reach it are
/// annotated as text in the reply contract, so a nested object is out of
/// contract; the approximation is recorded rather than reproduced because a
/// faithful `repr` for arbitrary nesting is a renderer of its own, and getting
/// it *nearly* right would be worse than a difference that is visible.
fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}
