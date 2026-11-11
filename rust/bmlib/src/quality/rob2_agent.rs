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

//! The RoB 2 assessor: one call per domain, answers backed by verified quotes.
//!
//! The model answers RoB 2's signalling questions (wording in
//! [`crate::quality::rob2_questions`]), one domain per call: D1 once for the
//! trial, D2-D5 once for each primary outcome. Code does every rule:
//! [`read_domain_reply`] parses the answers (`NA` means "not answered"), checks
//! applicability and derives the judgement with
//! [`crate::quality::rob2_algorithm`], and locates every quote in the
//! **original** text. When the guidance leaves the overall judgement to the
//! assessor, one more call asks whether to escalate.
//!
//! A failure is never a score: a domain whose reply cannot be read or whose
//! request fails is [`DomainResult::Failed`] naming why, the other domains go
//! on, and every outcome it touches has `overall` `None` ("not assessed").
//! Only a stop or a refused input ends the run ([`Rob2RunError`]). A paper over
//! the window is condensed per domain, for that domain's query, once per run.
//!
//! A port of `bmlib/quality/rob2_agent.py`; the contracts are
//! `tests/fixtures/rob2_replies.json` and `tests/fixtures/rob2_run.json`.

use std::collections::BTreeMap;
use std::fmt;
use std::sync::atomic::AtomicBool;

use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::{json, Map, Value};

use crate::context_processor::ProcessingConfig;
use crate::pyvalue::{python_strip, repr_str};
use crate::quality::agent_chat::{JsonChat, STOPPED_MESSAGE};
use crate::quality::cochrane_assessor::{
    DEFAULT_CONDENSE_THRESHOLD_CHARS, DEFAULT_MAX_RETRIES, DEFAULT_TEMPERATURE,
};
use crate::quality::full_text_agent::DEFAULT_MAX_TOKENS;
use crate::quality::full_text_agent::{
    ask_until_readable, check_similarity, check_stop, condense_once, Failure, Sampling, LABEL_CHARS,
};
pub use crate::quality::full_text_agent::{LlmQueryCondenser, QueryCondenser};
use crate::quality::outcome_agent::ProposedOutcome;
use crate::quality::pico_agent::verify_quotes;
use crate::quality::pico_models::{PicoCitation, PicoContradictedQuote};
use crate::quality::rob2_algorithm::{
    applicable_questions, domain_judgement, needs_escalation_decision, overall_judgement,
    questions, Answer, Answers, Domain, Judgement, Rob2Error, ROB2_ALGORITHM_VERSION,
};
use crate::quality::rob2_d5_checks::{D5Fact, D5_FACTS};
use crate::quality::rob2_d5_facts::{d5_answers, D5Facts, DIFFERING_ANALYSIS, MISSING_MEASUREMENT};
use crate::quality::rob2_questions::{
    condense_query, domain_title, question_text, D5_OUTCOME_SWITCHING, ESCALATION_PROMPT,
    ROB2_PROMPT_VERSION,
};

/// The answer that means "this question does not apply"; read as unanswered.
pub const NOT_APPLICABLE: &str = "NA";

/// Every spelling read as [`NOT_APPLICABLE`], once whitespace is removed and
/// the label upper-cased ("n/a", "Not applicable").
const NOT_APPLICABLE_FORMS: [&str; 3] = [NOT_APPLICABLE, "N/A", "NOTAPPLICABLE"];

/// The answers a reply may give, as the refusal names them.
const ALLOWED: &str = "Y, PY, PN, N, NI, NA";

/// The wire values of [`Answer`], [`Domain`] and [`Judgement`], as a refusal
/// names them.
const ANSWERS: &str = "Y, PY, PN, N, NI";
const DOMAINS: &str = "D1, D2, D3, D4, D5";
const JUDGEMENTS: &str = "low, some_concerns, high";

/// A [`DomainResult`]'s `status` values.
const ASSESSED: &str = "assessed";
const FAILED: &str = "failed";

/// The system turn of every RoB 2 call. Byte-identical to Python's.
pub const ROB2_SYSTEM_PROMPT: &str = r##"You are an expert in clinical research methodology and in assessing the risk of bias of randomised trials.

CRITICAL RULES:
1. Base every answer ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume or fabricate any information
3. When the paper gives nothing to go on, answer NI (no information) rather than guess
4. Return ONLY valid JSON, no explanation"##;

/// The start of each domain's prompt, with the reply shape; the domain, the
/// outcome, the questions and the paper follow it. Byte-identical to Python's.
pub const ROB2_DOMAIN_PROMPT: &str = r##"Assess one domain of the risk of bias of the randomised trial in the research paper below, for the effect of assignment to the interventions (as an intention-to-treat analysis estimates it).

Answer every question listed below. For each give:
- answer: "Y" (yes), "PY" (probably yes), "PN" (probably no), "N" (no) or "NI" (no information), as the question describes; "NA" (not applicable) only for a question that says it applies only under a condition, when that condition clearly does not hold
- quotes: 1-3 passages copied VERBATIM from the paper that support the answer, or an empty list when the paper says nothing on it. Copy them character for character; do not paraphrase, shorten with ellipses, or join sentences.
- reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape, one entry per question id:

{
    "answers": {
        "1.1": {
            "answer": "PY",
            "quotes": ["Participants were randomly assigned using a computer-generated list."],
            "reasoning": "A computer-generated list is a random sequence."
        }
    }
}"##;

// --- Wire helpers (the messages are Python's) ---

/// The wire name of a unit enum (`"D1"`, `"NI"`, `"low"`).
fn wire_name(value: impl Serialize) -> String {
    match json!(value) {
        Value::String(name) => name,
        other => unreachable!("a unit enum serialises to its name, not {other}"),
    }
}

/// `value` as an object, or a message naming `what`.
pub(crate) fn object<'v>(value: &'v Value, what: &str) -> Result<&'v Map<String, Value>, String> {
    value
        .as_object()
        .ok_or_else(|| format!("{what} is not an object"))
}

/// `data[key]` as text.
pub(crate) fn text<'v>(data: &'v Map<String, Value>, key: &str) -> Result<&'v str, String> {
    data.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{key} is not text"))
}

/// `data[key]` as text, `None` when absent or null.
fn optional_text(data: &Map<String, Value>, key: &str) -> Result<Option<String>, String> {
    match data.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.clone())),
        Some(_) => Err(format!("{key} is not text")),
    }
}

/// The lowest and highest HTTP status a stored `status_code` may hold.
const HTTP_STATUS_RANGE: std::ops::RangeInclusive<u64> = 100..=599;

/// `data[key]`: required, an HTTP status or null.
fn status_code(data: &Map<String, Value>, key: &str) -> Result<Option<u16>, String> {
    match data.get(key) {
        None => Err(format!("{key} is absent")),
        Some(Value::Null) => Ok(None),
        Some(code) => code
            .as_u64()
            .filter(|code| HTTP_STATUS_RANGE.contains(code))
            .and_then(|code| u16::try_from(code).ok())
            .map(Some)
            .ok_or_else(|| format!("{key} is not null or an HTTP status")),
    }
}

/// `data[key]` as a boolean, the message saying it is not `what`.
fn boolean(data: &Map<String, Value>, key: &str, what: &str) -> Result<bool, String> {
    data.get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("{key} is not {what}"))
}

/// `data[key]` as a list.
pub(crate) fn list<'v>(data: &'v Map<String, Value>, key: &str) -> Result<&'v Vec<Value>, String> {
    data.get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{key} is not a list"))
}

/// An exact wire value of `T`, the message listing `allowed`.
fn wire_enum<T: DeserializeOwned>(
    value: Option<&Value>,
    key: &str,
    allowed: &str,
) -> Result<T, String> {
    let Some(Value::String(label)) = value else {
        return Err(format!("{key} is not text"));
    };
    serde_json::from_value(Value::String(label.clone()))
        .map_err(|_| format!("{key} \"{label}\" is not one of {allowed}"))
}

// --- The models ---

/// The model's answer to one signalling question, with its passages.
#[derive(Debug, Clone, PartialEq)]
pub struct MachineAnswer {
    /// The answer.
    pub answer: Answer,
    /// The paper's own spans for the quotes found, offsets in code points.
    pub citations: Vec<PicoCitation>,
    /// The quotes not located in the paper, verbatim; the answer stands
    /// without them.
    pub quotes_not_found: Vec<String>,
    /// The quotes the paper contradicts (a changed number, negation or
    /// direction), each with the reason; never also in `quotes_not_found`.
    pub contradicted: Vec<PicoContradictedQuote>,
    /// How many quote members were not usable text (blank, or not a string)
    /// and so were never searched for.
    pub quotes_dropped: usize,
    /// The model's explanation, `""` when absent.
    pub reasoning: String,
}

impl MachineAnswer {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "answer": self.answer,
            "citations": self.citations.iter().map(PicoCitation::to_json).collect::<Vec<_>>(),
            "quotes_not_found": self.quotes_not_found,
            "contradicted": self.contradicted.iter().map(PicoContradictedQuote::to_json).collect::<Vec<_>>(),
            "quotes_dropped": self.quotes_dropped,
            "reasoning": self.reasoning,
        })
    }

    /// Python's `from_dict()`; the answer must be an exact wire value.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent, of the wrong type or value.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the answer")?;
        let quotes_not_found = list(data, "quotes_not_found")?
            .iter()
            .map(|q| q.as_str().map(str::to_string))
            .collect::<Option<Vec<_>>>()
            .ok_or("quotes_not_found is not a list of text")?;
        let quotes_dropped = data
            .get("quotes_dropped")
            .and_then(Value::as_u64)
            .and_then(|n| usize::try_from(n).ok())
            .ok_or("quotes_dropped is not a count")?;
        Ok(MachineAnswer {
            answer: wire_enum(data.get("answer"), "answer", ANSWERS)?,
            citations: list(data, "citations")?
                .iter()
                .map(PicoCitation::from_json)
                .collect::<Result<_, _>>()?,
            quotes_not_found,
            contradicted: list(data, "contradicted")?
                .iter()
                .map(PicoContradictedQuote::from_json)
                .collect::<Result<_, _>>()?,
            quotes_dropped,
            reasoning: text(data, "reasoning")?.to_string(),
        })
    }
}

/// One domain, assessed: the applicable answers and the judgement they give.
#[derive(Debug, Clone, PartialEq)]
pub struct DomainAssessment {
    /// The domain.
    pub domain: Domain,
    /// The applicable questions' answers by id.
    pub answers: BTreeMap<String, MachineAnswer>,
    /// The judgement [`domain_judgement`] derives from `answers`.
    pub judgement: Judgement,
    /// Whether the model saw a digest instead of the whole paper.
    pub condensed: bool,
    /// Ids the model answered although they do not apply on the branch its
    /// answers took; listed rather than dropped silently.
    pub ignored: Vec<String>,
    /// D5's facts, from which its answers were derived ([`d5_assessment`]);
    /// `None` for every other domain.
    pub facts: Option<D5Facts>,
}

impl DomainAssessment {
    /// Python's `to_dict()`, tagged `"status": "assessed"`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        let answers: Map<String, Value> = self
            .answers
            .iter()
            .map(|(q, a)| (q.clone(), a.to_json()))
            .collect();
        let mut value = json!({
            "status": ASSESSED,
            "domain": self.domain,
            "answers": answers,
            "judgement": self.judgement,
            "condensed": self.condensed,
            "ignored": self.ignored,
        });
        if let Some(facts) = &self.facts {
            value["facts"] = facts.to_json();
        }
        value
    }

    /// Python's `from_dict()`: refuses what the reader refuses. The answers
    /// must give the stored judgement, every answer must apply, and `ignored`
    /// may list only questions that do not apply.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent, of the wrong type or value,
    /// or the answers that do not give the judgement.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the domain")?;
        let domain: Domain = wire_enum(data.get("domain"), "domain", DOMAINS)?;
        let raw = data
            .get("answers")
            .and_then(Value::as_object)
            .ok_or("answers is not an object")?;
        refuse_unknown(domain, raw)?;
        let mut answers = BTreeMap::new();
        for &question in questions(domain) {
            if let Some(item) = raw.get(question) {
                let answer = MachineAnswer::from_json(item)
                    .map_err(|e| format!("answers.{question}.{e}"))?;
                answers.insert(question.to_string(), answer);
            }
        }
        let judgement: Judgement = wire_enum(data.get("judgement"), "judgement", JUDGEMENTS)?;
        let given: Answers = answers.iter().map(|(q, a)| (q.clone(), a.answer)).collect();
        let derived = judge(domain, &given)?;
        let applicable = applicable_questions(domain, &given);
        if let Some(question) = answers.keys().find(|q| !applicable.contains(&q.as_str())) {
            return Err(format!("answers.{question} is not applicable"));
        }
        if derived != judgement {
            return Err(format!(
                "judgement {} does not follow from the answers",
                wire_name(judgement)
            ));
        }
        let mut ignored = Vec::new();
        for item in list(data, "ignored")? {
            match item.as_str() {
                Some(q) if questions(domain).contains(&q) && !applicable.contains(&q) => {
                    ignored.push(q.to_string());
                }
                other => {
                    let shown = other.map_or_else(|| item.to_string(), str::to_string);
                    return Err(format!(
                        "ignored lists {shown}, which is not an unasked question of {}",
                        wire_name(domain)
                    ));
                }
            }
        }
        let facts = match data.get("facts") {
            None | Some(Value::Null) => None,
            Some(_) if domain != Domain::D5 => {
                return Err(format!("facts are D5's, not {}'s", wire_name(domain)));
            }
            Some(facts) => Some(D5Facts::from_json(facts)?),
        };
        Ok(DomainAssessment {
            domain,
            answers,
            judgement,
            condensed: boolean(data, "condensed", "a boolean")?,
            ignored,
            facts,
        })
    }
}

/// A domain's result: assessed, or failed with its reason.
#[derive(Debug, Clone, PartialEq)]
pub enum DomainResult {
    /// The domain was assessed.
    Assessed(DomainAssessment),
    /// The domain could not be assessed.
    Failed {
        /// The domain.
        domain: Domain,
        /// Why: the unreadable field, the failed request or condensation.
        reason: String,
        /// Whether sending the failed request again may succeed; `false`
        /// where the failure was not a request.
        retryable: bool,
        /// The HTTP status the failed request was answered with (401 or 403
        /// is a refused key); `None` where no HTTP answer came or the failure
        /// was not a request.
        status: Option<u16>,
    },
}

impl DomainResult {
    /// The domain, assessed or not.
    #[must_use]
    pub fn domain(&self) -> Domain {
        match self {
            DomainResult::Assessed(assessment) => assessment.domain,
            DomainResult::Failed { domain, .. } => *domain,
        }
    }

    /// Python's `to_dict()` of either class, tagged by `status`; a failed
    /// domain's `status` is written as `status_code`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        match self {
            DomainResult::Assessed(assessment) => assessment.to_json(),
            DomainResult::Failed {
                domain,
                reason,
                retryable,
                status,
            } => json!({
                "status": FAILED,
                "domain": domain,
                "reason": reason,
                "retryable": retryable,
                "status_code": status,
            }),
        }
    }

    /// Python's `domain_result_from_dict()`, by `status`.
    ///
    /// # Errors
    ///
    /// A message when `status` is neither `assessed` nor `failed`, or the rest
    /// does not read.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the domain")?;
        match text(data, "status")? {
            ASSESSED => DomainAssessment::from_json(value).map(DomainResult::Assessed),
            FAILED => Ok(DomainResult::Failed {
                domain: wire_enum(data.get("domain"), "domain", DOMAINS)?,
                reason: text(data, "reason")?.to_string(),
                retryable: boolean(data, "retryable", "a boolean")?,
                status: status_code(data, "status_code")?,
            }),
            other => Err(format!("status \"{other}\" is not {ASSESSED} or {FAILED}")),
        }
    }
}

/// The model's answer to the escalation question.
#[derive(Debug, Clone, PartialEq)]
pub struct Escalation {
    /// Whether the combined concerns warrant high risk of bias.
    pub escalate: bool,
    /// The model's explanation, `""` when absent.
    pub reasoning: String,
}

impl Escalation {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({"escalate": self.escalate, "reasoning": self.reasoning})
    }

    /// Python's `from_dict()`.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent or of the wrong type.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "escalation")?;
        Ok(Escalation {
            escalate: boolean(data, "escalate", "true or false")?,
            reasoning: text(data, "reasoning")?.to_string(),
        })
    }
}

/// RoB 2 for one primary outcome: domains 2-5, and the overall judgement.
#[derive(Debug, Clone, PartialEq)]
pub struct OutcomeRob2 {
    /// The outcome's name, as the outcome proposal gave it.
    pub outcome: String,
    /// Domains D2, D3, D4 and D5, in that order.
    pub domains: Vec<DomainResult>,
    /// The model's escalation answer; `None` when none was needed or it could
    /// not be had (then see `escalation_error`).
    pub escalation: Option<Escalation>,
    /// Why a needed escalation could not be had; `None` otherwise.
    pub escalation_error: Option<String>,
    /// Whether sending the failed escalation request again may succeed;
    /// `false` unless `escalation_error` came from a request.
    pub escalation_retryable: bool,
    /// The HTTP status that request was answered with; `None` where no HTTP
    /// answer came or `escalation_error` is not from a request. Written as
    /// `escalation_status_code`.
    pub escalation_status: Option<u16>,
    /// The overall judgement; `None` ("not assessed") when any of D1-D5 failed
    /// or a needed escalation could not be had. Never a guessed judgement.
    pub overall: Option<Judgement>,
}

impl OutcomeRob2 {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "outcome": self.outcome,
            "domains": self.domains.iter().map(DomainResult::to_json).collect::<Vec<_>>(),
            "escalation": self.escalation.as_ref().map(Escalation::to_json),
            "escalation_error": self.escalation_error,
            "escalation_retryable": self.escalation_retryable,
            "escalation_status_code": self.escalation_status,
            "overall": self.overall,
        })
    }

    /// Python's `from_dict()`. Whether `overall` follows from the domains
    /// needs the study's D1, so [`Rob2Run::from_json`] checks it.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent or of the wrong type or
    /// value, domains that are not D2-D5 in order, or both `escalation` and
    /// `escalation_error` set.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the outcome")?;
        let domains = list(data, "domains")?
            .iter()
            .map(DomainResult::from_json)
            .collect::<Result<Vec<_>, _>>()?;
        if domains
            .iter()
            .map(DomainResult::domain)
            .ne(Domain::ALL[1..].iter().copied())
        {
            return Err("domains are not D2, D3, D4, D5 in order".to_string());
        }
        let escalation = match data.get("escalation") {
            None | Some(Value::Null) => None,
            Some(raw) => Some(Escalation::from_json(raw)?),
        };
        let escalation_error = optional_text(data, "escalation_error")?;
        if escalation.is_some() && escalation_error.is_some() {
            return Err("escalation and escalation_error are both set".to_string());
        }
        let escalation_retryable = boolean(data, "escalation_retryable", "a boolean")?;
        let escalation_status = status_code(data, "escalation_status_code")?;
        if escalation_error.is_none() && (escalation_retryable || escalation_status.is_some()) {
            return Err(
                "escalation_retryable or escalation_status_code given without an error".to_string(),
            );
        }
        let outcome = text(data, "outcome")?.to_string();
        let overall = match data.get("overall") {
            None | Some(Value::Null) => None,
            raw => Some(wire_enum(raw, "overall", JUDGEMENTS)?),
        };
        Ok(OutcomeRob2 {
            outcome,
            domains,
            escalation,
            escalation_error,
            escalation_retryable,
            escalation_status,
            overall,
        })
    }
}

/// One RoB 2 run over a paper: D1 for the trial, D2-D5 for each primary outcome.
#[derive(Debug, Clone, PartialEq)]
pub struct Rob2Run {
    /// The decision rules that judged it ([`ROB2_ALGORITHM_VERSION`]).
    pub algorithm_version: String,
    /// The wording that asked it ([`ROB2_PROMPT_VERSION`]).
    pub prompt_version: u32,
    /// Domain D1, judged once for the trial.
    pub study: DomainResult,
    /// One entry per primary outcome, in the order given.
    pub outcomes: Vec<OutcomeRob2>,
}

impl Rob2Run {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "algorithm_version": self.algorithm_version,
            "prompt_version": self.prompt_version,
            "study": self.study.to_json(),
            "outcomes": self.outcomes.iter().map(OutcomeRob2::to_json).collect::<Vec<_>>(),
        })
    }

    /// Python's `from_dict()`: refuses what the agent would not make. A run
    /// judged by other decision rules is refused, since its judgements cannot
    /// be checked against these.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent or of the wrong type or
    /// value, a study that is not D1, an escalation given where none is asked,
    /// or an outcome's `overall` that does not follow from its domains
    /// (`outcomes[1].`).
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the run")?;
        let algorithm_version = text(data, "algorithm_version")?;
        if algorithm_version != ROB2_ALGORITHM_VERSION {
            return Err(format!(
                "algorithm_version {algorithm_version} is not {ROB2_ALGORITHM_VERSION}"
            ));
        }
        let prompt_version = match data.get("prompt_version") {
            Some(Value::Number(n)) if n.is_u64() || n.is_i64() => n
                .as_u64()
                .and_then(|n| u32::try_from(n).ok())
                .ok_or("prompt_version is not a whole number that fits 32 bits")?,
            _ => return Err("prompt_version is not a whole number".to_string()),
        };
        let study = DomainResult::from_json(data.get("study").unwrap_or(&Value::Null))
            .map_err(|e| format!("study.{e}"))?;
        if study.domain() != Domain::D1 {
            return Err("study is not domain D1".to_string());
        }
        let mut outcomes = Vec::new();
        for (index, raw) in list(data, "outcomes")?.iter().enumerate() {
            let outcome = OutcomeRob2::from_json(raw)
                .and_then(|o| check_overall(&study, &o).map(|()| o))
                .map_err(|e| format!("outcomes[{index}].{e}"))?;
            outcomes.push(outcome);
        }
        Ok(Rob2Run {
            algorithm_version: algorithm_version.to_string(),
            prompt_version,
            study,
            outcomes,
        })
    }
}

/// Refuse an escalation nobody asked for and an `overall` the domains do not give.
fn check_overall(study: &DomainResult, outcome: &OutcomeRob2) -> Result<(), String> {
    let asked = judgements(study, &outcome.domains).is_some_and(|j| needs_escalation_decision(&j));
    if outcome.escalation.is_some() && !asked {
        return Err("escalation given where none is asked".to_string());
    }
    if outcome.escalation_error.is_some() && !asked {
        return Err("escalation_error given where no escalation is asked".to_string());
    }
    if outcome.overall != rob2_overall(study, &outcome.domains, outcome.escalation.as_ref()) {
        return Err("overall does not follow from the domains".to_string());
    }
    Ok(())
}

/// The five judgements, D1 first; `None` when any domain failed.
fn judgements(study: &DomainResult, domains: &[DomainResult]) -> Option<[Judgement; 5]> {
    let all: Option<Vec<Judgement>> = std::iter::once(study)
        .chain(domains)
        .map(|result| match result {
            DomainResult::Assessed(assessment) => Some(assessment.judgement),
            DomainResult::Failed { .. } => None,
        })
        .collect();
    all?.try_into().ok()
}

/// The overall judgement of one outcome, or `None` when it is not assessed.
/// Python's `rob2_overall`.
///
/// `None` when any domain failed, or an escalation is needed and `escalation`
/// is `None`; otherwise [`overall_judgement`].
#[must_use]
pub fn rob2_overall(
    study: &DomainResult,
    domains: &[DomainResult],
    escalation: Option<&Escalation>,
) -> Option<Judgement> {
    let judgements = judgements(study, domains)?;
    let escalate = escalation.map(|e| e.escalate);
    if needs_escalation_decision(&judgements) && escalate.is_none() {
        return None;
    }
    overall_judgement(&judgements, escalate).ok()
}

// --- Reading a reply ---

/// Refuse the first id (in sorted order) that is not one of `domain`'s questions.
fn refuse_unknown(domain: Domain, answers: &Map<String, Value>) -> Result<(), String> {
    let mut unknown: Vec<&String> = answers
        .keys()
        .filter(|q| !questions(domain).contains(&q.as_str()))
        .collect();
    unknown.sort();
    match unknown.first() {
        Some(question) => Err(format!(
            "answers.{question} is not a question of {}",
            wire_name(domain)
        )),
        None => Ok(()),
    }
}

/// [`domain_judgement`], its refusal named in wire terms (`answers.2.3`).
fn judge(domain: Domain, answers: &Answers) -> Result<Judgement, String> {
    domain_judgement(domain, answers).map_err(|error| match error {
        Rob2Error::MissingAnswer { question } => {
            format!("answers.{question} is applicable but not answered")
        }
        Rob2Error::UnknownQuestion { question } => {
            format!(
                "answers.{question} is not a question of {}",
                wire_name(domain)
            )
        }
        Rob2Error::NotAllowed { question, answer } => format!(
            "answers.{question}.answer {} is not allowed for this question",
            wire_name(answer)
        ),
        other => other.to_string(),
    })
}

/// Read the model's answers for one domain and verify their quotes against
/// `text` (the paper as passed, never a digest). Python's `read_domain_reply`.
///
/// An answer of `NA` (also `N/A` or `Not applicable`; any case, whitespace
/// ignored) counts as unanswered; an answer to a question that does not apply,
/// even one that cannot be read, is left out and its id listed in `ignored`.
/// `condensed` is false.
///
/// # Errors
///
/// A message when `min_similarity` is outside (0, 1]; the reply is not an
/// object; `answers` is not an object; an id is not one of the domain's
/// questions; an entry is not an object; an answer is not text or not one of
/// Y, PY, PN, N, NI, NA; reasoning is not text (each of these only for a
/// question that applies, decided from the answers that can be read); an
/// applicable question is unanswered (or `NA`); or an answer is not allowed for
/// its question (3.2 has no NI). The message names the field
/// (`answers.2.3.answer`).
pub fn read_domain_reply(
    domain: Domain,
    data: &Value,
    text: &str,
    min_similarity: f64,
) -> Result<DomainAssessment, String> {
    check_similarity(min_similarity)?;
    let data = data.as_object().ok_or("expected an object")?;
    let raw = data
        .get("answers")
        .and_then(Value::as_object)
        .ok_or("answers is not an object")?;
    refuse_unknown(domain, raw)?;

    let mut parsed: Vec<(&'static str, MachineAnswer)> = Vec::new();
    let mut unreadable: BTreeMap<&'static str, String> = BTreeMap::new();
    for &question in questions(domain) {
        let Some(item) = raw.get(question) else {
            continue;
        };
        match read_entry(item, &format!("answers.{question}"), text, min_similarity) {
            Ok(Some(answer)) => parsed.push((question, answer)),
            Ok(None) => {}
            Err(message) => {
                unreadable.insert(question, message);
            }
        }
    }

    let given: Answers = parsed
        .iter()
        .map(|(q, a)| ((*q).to_string(), a.answer))
        .collect();
    let applicable = applicable_questions(domain, &given);
    if let Some(message) = applicable.iter().find_map(|q| unreadable.get(q)) {
        return Err(message.clone());
    }
    let judgement = judge(domain, &given)?;
    let ignored = questions(domain)
        .iter()
        .filter(|q| {
            (parsed.iter().any(|(p, _)| p == *q) || unreadable.contains_key(*q))
                && !applicable.contains(q)
        })
        .map(|q| (*q).to_string())
        .collect();
    let answers = parsed
        .into_iter()
        .filter(|(q, _)| applicable.contains(q))
        .map(|(q, a)| (q.to_string(), a))
        .collect();
    Ok(DomainAssessment {
        domain,
        answers,
        judgement,
        condensed: false,
        ignored,
        facts: None,
    })
}

/// D5 assessed from `facts`: the answers by [`d5_answers`], the judgement by
/// RoB 2's algorithm, and the facts kept with it. Python's `d5_assessment`.
///
/// Each answer's citations are the passages that decided it: the deciding
/// facts' quotes, the naming passages of the measurements missing at both
/// looks, or both sides of a differing analysis. Its reasoning lists every
/// fact's verdict, so a reader sees why it came out.
///
/// # Panics
///
/// When `facts` lacks one of [`D5_FACTS`], which neither
/// [`read_facts_reply`](crate::quality::rob2_d5_facts::read_facts_reply) nor
/// [`D5Facts::from_json`] lets happen.
#[must_use]
pub fn d5_assessment(facts: D5Facts) -> DomainAssessment {
    let verdicts = D5_FACTS
        .iter()
        .map(|f| format!("{}: {}", f.as_str(), facts.facts[f].verdict.as_str()))
        .collect::<Vec<_>>()
        .join("; ");
    let missing: Vec<&str> = facts
        .measurements
        .iter()
        .filter(|m| m.missing())
        .map(|m| m.label.as_str())
        .collect();
    let reasoning = format!(
        "From the facts ({verdicts}); other measurements missing at both looks: {}; the \
         analysis reported differs from the main one: {}.",
        if missing.is_empty() {
            "none".to_string()
        } else {
            missing.join(", ")
        },
        if facts.analysis.counts() { "yes" } else { "no" }
    );
    let mut answers = BTreeMap::new();
    for (question, (answer, deciding)) in d5_answers(&facts) {
        let mut citations: Vec<PicoCitation> = Vec::new();
        for reason in &deciding {
            if reason == MISSING_MEASUREMENT {
                citations.extend(
                    facts
                        .measurements
                        .iter()
                        .filter(|m| m.missing())
                        .flat_map(|m| m.named.iter().cloned()),
                );
            } else if reason == DIFFERING_ANALYSIS {
                citations.extend(facts.analysis.planned.iter().cloned());
                citations.extend(facts.analysis.reported.iter().cloned());
            } else if let Some(fact) = D5Fact::parse(reason) {
                citations.extend(facts.facts[&fact].citations.iter().cloned());
            }
        }
        answers.insert(
            question,
            MachineAnswer {
                answer,
                citations,
                quotes_not_found: Vec::new(),
                contradicted: Vec::new(),
                quotes_dropped: 0,
                reasoning: reasoning.clone(),
            },
        );
    }
    let given: Answers = answers
        .iter()
        .map(|(q, a): (&String, &MachineAnswer)| (q.clone(), a.answer))
        .collect();
    let judgement = judge(Domain::D5, &given).expect("D5's three answers are always complete");
    DomainAssessment {
        domain: Domain::D5,
        answers,
        judgement,
        condensed: false,
        ignored: Vec::new(),
        facts: Some(facts),
    }
}

/// One question's entry as a [`MachineAnswer`], or `None` for `NA`. Python's
/// `_read_entry`.
///
/// `NA`, `N/A` and `Not applicable` (any case, whitespace ignored) read as `NA`.
///
/// # Errors
///
/// A message naming the field under `path` when the entry is not an object,
/// its answer is not text or not one of Y, PY, PN, N, NI, NA, or its reasoning
/// is not text.
fn read_entry(
    item: &Value,
    path: &str,
    text: &str,
    min_similarity: f64,
) -> Result<Option<MachineAnswer>, String> {
    let item = item
        .as_object()
        .ok_or_else(|| format!("{path} is not an object"))?;
    let label = item
        .get("answer")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{path}.answer is not text"))?;
    let folded: String = label.chars().filter(|c| !c.is_whitespace()).collect();
    if NOT_APPLICABLE_FORMS.contains(&folded.to_uppercase().as_str()) {
        return Ok(None);
    }
    let answer = Answer::parse(label)
        .map_err(|_| format!("{path}.answer \"{label}\" is not one of {ALLOWED}"))?;
    let reasoning = match item.get("reasoning") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(reasoning)) => reasoning.clone(),
        Some(_) => return Err(format!("{path}.reasoning is not text")),
    };
    let (citations, quotes_not_found, contradicted, quotes_dropped) =
        verify_quotes(item.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
    Ok(Some(MachineAnswer {
        answer,
        citations,
        quotes_not_found,
        contradicted,
        quotes_dropped,
        reasoning,
    }))
}

/// Read the model's escalation answer. Python's `read_escalation_reply`.
///
/// # Errors
///
/// A message when the reply is not an object, `escalate` is not `true` or
/// `false`, or `reasoning` is not text.
pub fn read_escalation_reply(data: &Value) -> Result<Escalation, String> {
    let data = data.as_object().ok_or("expected an object")?;
    let escalate = boolean(data, "escalate", "true or false")?;
    Ok(Escalation {
        escalate,
        reasoning: optional_text(data, "reasoning")?.unwrap_or_default(),
    })
}

// --- The prompts ---

/// The outcome as a prompt names it: name, timepoint, reported result.
/// Shared by the domain, escalation and D5 facts prompts.
pub(crate) fn outcome_block(outcome: &ProposedOutcome) -> String {
    // shortcut: a number under 1e-4 or over 1e16 prints its exponent as Rust
    // does (1e-5; Python 1e-05); pin such a case if one ever matters.
    let result = match &outcome.result {
        None => "none extracted".to_string(),
        Some(result) => serde_json::to_string(result).expect("a ReportedResult always serialises"),
    };
    let timepoint = outcome
        .timepoint
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or("not stated");
    format!(
        "Outcome assessed: {}\nTimepoint: {timepoint}\nReported result: {result}",
        outcome.name
    )
}

/// [`domain_prompt`] without the check that the outcome matches the domain.
fn render_domain_prompt(
    domain: Domain,
    title: &str,
    paper: &str,
    outcome: Option<&ProposedOutcome>,
) -> String {
    let mut parts = vec![
        ROB2_DOMAIN_PROMPT.to_string(),
        domain_title(domain).to_string(),
    ];
    if let Some(outcome) = outcome {
        parts.push(outcome_block(outcome));
    }
    if domain == Domain::D5 {
        parts.push(D5_OUTCOME_SWITCHING.to_string());
    }
    let listed: Vec<String> = questions(domain)
        .iter()
        .map(|q| format!("{q}: {}", question_text(q)))
        .collect();
    parts.push(format!("Questions:\n\n{}", listed.join("\n\n")));
    parts.push(format!("Paper Title: {title}"));
    parts.push(format!("Paper Text:\n{paper}"));
    parts.join("\n\n")
}

/// The user turn asking `domain`'s questions over `paper` (the whole paper or
/// its digest). `outcome` is `None` for D1 and required for D2-D5. Python's
/// `domain_prompt`.
///
/// # Errors
///
/// A message when an outcome is given for D1, or none for D2-D5.
pub fn domain_prompt(
    domain: Domain,
    title: &str,
    paper: &str,
    outcome: Option<&ProposedOutcome>,
) -> Result<String, String> {
    match (domain, outcome) {
        (Domain::D1, Some(_)) => {
            Err("D1 is judged once for the trial, without an outcome".to_string())
        }
        (Domain::D1, None) | (_, Some(_)) => {
            Ok(render_domain_prompt(domain, title, paper, outcome))
        }
        (_, None) => Err(format!(
            "{} is judged for an outcome, and none was given",
            wire_name(domain)
        )),
    }
}

/// The user turn of the escalation question: the outcome and all five domains,
/// each its title and judgement, then one line per answer with its reasoning.
/// Python's `escalation_message`.
#[must_use]
pub fn escalation_message(
    outcome: &ProposedOutcome,
    study: &DomainAssessment,
    domains: &[DomainAssessment],
) -> String {
    let mut blocks = vec![ESCALATION_PROMPT.to_string(), outcome_block(outcome)];
    for assessment in std::iter::once(study).chain(domains) {
        let mut lines = vec![format!(
            "{}: {}",
            domain_title(assessment.domain),
            wire_name(assessment.judgement)
        )];
        for (question, answer) in &assessment.answers {
            let line = format!("{question}: {}", wire_name(answer.answer));
            lines.push(if answer.reasoning.is_empty() {
                line
            } else {
                format!("{line} - {}", answer.reasoning)
            });
        }
        blocks.push(lines.join("\n"));
    }
    blocks.join("\n\n")
}

// --- The agent ---

/// Why a RoB 2 run ended without a result; a domain's own failure is not one
/// (it is [`DomainResult::Failed`]).
#[derive(Debug, Clone, PartialEq)]
pub enum Rob2RunError {
    /// The paper's text was empty; no request was made.
    EmptyInput,
    /// No primary outcome was given; no request was made.
    NoOutcomes,
    /// The caller's stop was set; no partial run is returned.
    Stopped,
    /// `min_similarity` was not a number in (0, 1]; no request was made.
    InvalidSimilarity,
}

impl fmt::Display for Rob2RunError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Rob2RunError::EmptyInput => "the paper's text is empty",
            Rob2RunError::NoOutcomes => "no primary outcome to assess",
            Rob2RunError::Stopped => STOPPED_MESSAGE,
            Rob2RunError::InvalidSimilarity => "min_similarity must be a number in (0, 1]",
        })
    }
}

impl std::error::Error for Rob2RunError {}

/// A call's failure: a stop ends the run, anything else is the reason its
/// domain (or escalation) failed.
fn split(failure: Failure) -> Result<(String, bool, Option<u16>), Rob2RunError> {
    match failure {
        Failure::Stopped => Err(Rob2RunError::Stopped),
        Failure::CondensationFailed(m) | Failure::Unreadable(m) => Ok((m, false, None)),
        Failure::RequestFailed {
            message,
            retryable,
            status,
        } => Ok((message, retryable, status)),
    }
}

/// The digests of one run, by domain: condensed once, reused for every outcome.
type Papers = BTreeMap<Domain, Result<String, Failure>>;

/// Assesses a paper with RoB 2, one call per domain.
///
/// Text over `condense_config.max_context_chars` is condensed per domain by
/// the configured [`QueryCondenser`] (see [`LlmQueryCondenser`]); with none
/// configured each domain fails as a condensation failure, never sent whole or
/// cut. A stop is read before each request and condensation.
pub struct Rob2Agent<'a> {
    chat: &'a mut dyn JsonChat,
    condenser: Option<&'a mut dyn QueryCondenser>,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    condense_config: ProcessingConfig,
    stop: Option<&'a AtomicBool>,
}

impl<'a> Rob2Agent<'a> {
    /// An agent asking through `chat`, with the Cochrane assessor's defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        Rob2Agent {
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

    /// End a run before its next request once `stop` is set.
    #[must_use]
    pub fn with_stop(mut self, stop: &'a AtomicBool) -> Self {
        self.stop = Some(stop);
        self
    }

    /// Condense an oversized paper through `condenser`.
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

    /// The budget beyond which text is condensed first.
    #[must_use]
    pub fn with_condense_config(mut self, condense_config: ProcessingConfig) -> Self {
        self.condense_config = condense_config;
        self
    }

    fn sampling(&self) -> Sampling {
        Sampling {
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            max_retries: self.max_retries,
        }
    }

    /// Assess the paper: D1 once, D2-D5 for each outcome, then any escalation.
    ///
    /// Citation offsets are code points into `text` exactly as passed.
    ///
    /// # Errors
    ///
    /// [`Rob2RunError`] when the input is refused before any request, or the
    /// stop is set. A domain that could not be assessed is a
    /// [`DomainResult::Failed`] in the run instead.
    pub fn assess(
        &mut self,
        title: &str,
        text: &str,
        outcomes: &[ProposedOutcome],
        min_similarity: f64,
    ) -> Result<Rob2Run, Rob2RunError> {
        check_similarity(min_similarity).map_err(|_| Rob2RunError::InvalidSimilarity)?;
        let title = python_strip(title);
        let stripped = python_strip(text);
        if stripped.is_empty() {
            return Err(Rob2RunError::EmptyInput);
        }
        if outcomes.is_empty() {
            return Err(Rob2RunError::NoOutcomes);
        }
        check_stop(self.stop).map_err(|_| Rob2RunError::Stopped)?;
        let paper = Paper {
            title,
            stripped,
            original: text,
            min_similarity,
        };
        let mut papers = Papers::new();
        let study = self.domain(Domain::D1, &paper, None, &mut papers)?;
        let mut results = Vec::with_capacity(outcomes.len());
        for outcome in outcomes {
            let mut domains = Vec::with_capacity(Domain::ALL.len() - 1);
            for &domain in &Domain::ALL[1..] {
                domains.push(self.domain(domain, &paper, Some(outcome), &mut papers)?);
            }
            results.push(self.outcome(title, outcome, &study, domains)?);
        }
        Ok(Rob2Run {
            algorithm_version: ROB2_ALGORITHM_VERSION.to_string(),
            prompt_version: ROB2_PROMPT_VERSION,
            study,
            outcomes: results,
        })
    }

    /// The text `domain`'s call sends and whether it is a digest; condensed
    /// once per run.
    fn paper_for(
        &mut self,
        domain: Domain,
        paper: &Paper<'_>,
        papers: &mut Papers,
    ) -> Result<(String, bool), Failure> {
        if paper.stripped.chars().count() <= self.condense_config.max_context_chars {
            return Ok((paper.stripped.to_string(), false));
        }
        if let Some(digest) = papers.get(&domain) {
            return digest.clone().map(|digest| (digest, true));
        }
        let stage = format!("the RoB 2 {} assessment", wire_name(domain));
        let digest = condense_once(
            match self.condenser.as_mut() {
                Some(condenser) => Some(&mut **condenser),
                None => None,
            },
            condense_query(domain),
            paper.title,
            paper.stripped,
            &self.condense_config,
            &stage,
            self.stop,
        );
        if digest != Err(Failure::Stopped) {
            papers.insert(domain, digest.clone());
        }
        digest.map(|digest| (digest, true))
    }

    /// Ask one domain's questions; a failed call comes back as
    /// [`DomainResult::Failed`].
    fn domain(
        &mut self,
        domain: Domain,
        paper: &Paper<'_>,
        outcome: Option<&ProposedOutcome>,
        papers: &mut Papers,
    ) -> Result<DomainResult, Rob2RunError> {
        let failed = |(reason, retryable, status)| DomainResult::Failed {
            domain,
            reason,
            retryable,
            status,
        };
        let (sent, condensed) = match self.paper_for(domain, paper, papers) {
            Ok(sent) => sent,
            Err(failure) => return split(failure).map(failed),
        };
        let label = format!(
            "RoB 2 {} of {}",
            wire_name(domain),
            repr_str(&paper.title.chars().take(LABEL_CHARS).collect::<String>())
        );
        let sampling = self.sampling();
        let asked = ask_until_readable(
            &mut *self.chat,
            ROB2_SYSTEM_PROMPT,
            &render_domain_prompt(domain, paper.title, &sent, outcome),
            sampling,
            self.stop,
            &label,
            |data| read_domain_reply(domain, data, paper.original, paper.min_similarity),
        );
        match asked {
            Ok(mut assessment) => {
                assessment.condensed = condensed;
                Ok(DomainResult::Assessed(assessment))
            }
            Err(failure) => split(failure).map(failed),
        }
    }

    /// Combine one outcome's domains, asking the escalation question when it
    /// is needed.
    fn outcome(
        &mut self,
        title: &str,
        outcome: &ProposedOutcome,
        study: &DomainResult,
        domains: Vec<DomainResult>,
    ) -> Result<OutcomeRob2, Rob2RunError> {
        let mut escalation = None;
        let mut escalation_error = None;
        let (mut escalation_retryable, mut escalation_status) = (false, None);
        let needed = judgements(study, &domains).is_some_and(|j| needs_escalation_decision(&j));
        if let (true, DomainResult::Assessed(first)) = (needed, study) {
            let assessed: Vec<DomainAssessment> = domains
                .iter()
                .filter_map(|d| match d {
                    DomainResult::Assessed(a) => Some(a.clone()),
                    DomainResult::Failed { .. } => None,
                })
                .collect();
            let label = format!(
                "RoB 2 escalation of {}",
                repr_str(&title.chars().take(LABEL_CHARS).collect::<String>())
            );
            let sampling = self.sampling();
            match ask_until_readable(
                &mut *self.chat,
                ROB2_SYSTEM_PROMPT,
                &escalation_message(outcome, first, &assessed),
                sampling,
                self.stop,
                &label,
                read_escalation_reply,
            ) {
                Ok(answer) => escalation = Some(answer),
                Err(failure) => {
                    let (message, retryable, status) = split(failure)?;
                    (escalation_error, escalation_retryable, escalation_status) =
                        (Some(message), retryable, status);
                }
            }
        }
        let overall = rob2_overall(study, &domains, escalation.as_ref());
        Ok(OutcomeRob2 {
            outcome: outcome.name.clone(),
            domains,
            escalation,
            escalation_error,
            escalation_retryable,
            escalation_status,
            overall,
        })
    }
}

/// The paper one run reads: what is sent and what quotes are checked against.
struct Paper<'p> {
    title: &'p str,
    stripped: &'p str,
    original: &'p str,
    min_similarity: f64,
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::agents::{ChatJsonError, ChatJsonOutcome};
    use crate::context_processor::{ProcessingResult, ProcessingStatus};
    use crate::llm::LLMMessage;
    use crate::quality::evidence_ladder::{EffectScale, ReportedResult};
    use crate::quality::rob2_questions::ROB2_CONDENSE_QUERIES;
    use std::sync::atomic::Ordering;

    const RANDOM: &str = "Participants were randomly assigned using a computer-generated list.";
    pub(crate) const TEXT: &str = "Participants were randomly assigned using a computer-generated list. Allocation was concealed by a central web-based service. Outcome data were available for 98% of participants.";
    pub(crate) const SIM: f64 = 0.9;

    fn entry(answer: &str) -> Value {
        json!({"answer": answer, "quotes": [], "reasoning": "Because."})
    }

    /// The answers that make `domain` low, with `changes` applied.
    pub(crate) fn reply(domain: Domain, changes: &[(&str, &str)]) -> Value {
        let mut answers: Map<String, Value> = match domain {
            Domain::D1 => [("1.1", "Y"), ("1.2", "Y"), ("1.3", "N")].as_slice(),
            Domain::D2 => [("2.1", "N"), ("2.2", "N"), ("2.6", "Y")].as_slice(),
            Domain::D3 => [("3.1", "Y")].as_slice(),
            Domain::D4 => [("4.1", "N"), ("4.2", "N"), ("4.3", "N")].as_slice(),
            Domain::D5 => [("5.1", "Y"), ("5.2", "N"), ("5.3", "N")].as_slice(),
        }
        .iter()
        .chain(changes)
        .map(|(q, a)| ((*q).to_string(), entry(a)))
        .collect();
        if domain == Domain::D1 {
            answers["1.1"]["quotes"] = json!([RANDOM]);
        }
        json!({ "answers": answers })
    }

    #[test]
    fn every_spelling_of_not_applicable_reads_as_na() {
        for label in ["NA", " na ", "N/A", "n/a", "n / a", "Not applicable"] {
            let found =
                read_domain_reply(Domain::D2, &reply(Domain::D2, &[("2.7", label)]), TEXT, SIM)
                    .expect("read");
            assert!(found.ignored.is_empty() && !found.answers.contains_key("2.7"));
            let mut omitted = reply(Domain::D2, &[]);
            omitted["answers"]["2.6"] = entry(label);
            assert_eq!(
                read_domain_reply(Domain::D2, &omitted, TEXT, SIM).expect_err(label),
                "answers.2.6 is applicable but not answered"
            );
        }
    }

    #[test]
    fn an_unreadable_entry_on_a_question_that_does_not_apply_is_ignored() {
        for item in [entry("Unclear"), json!({"answer": 3}), json!("Y")] {
            let mut data = reply(Domain::D2, &[]);
            data["answers"]["2.7"] = item;
            let found = read_domain_reply(Domain::D2, &data, TEXT, SIM).expect("read");
            assert_eq!(found.judgement, Judgement::Low);
            assert_eq!(found.ignored, vec!["2.7".to_string()]);
        }
        let asked = reply(Domain::D2, &[("2.6", "N"), ("2.7", "Unclear")]);
        assert_eq!(
            read_domain_reply(Domain::D2, &asked, TEXT, SIM).expect_err("refused"),
            "answers.2.7.answer \"Unclear\" is not one of Y, PY, PN, N, NI, NA"
        );
    }

    pub(crate) fn low_run(outcomes: usize) -> Vec<Value> {
        let mut replies = vec![reply(Domain::D1, &[])];
        for _ in 0..outcomes {
            replies.extend(Domain::ALL[1..].iter().map(|d| reply(*d, &[])));
        }
        replies
    }

    /// Replies with some concerns in D1 and D5, so an escalation is asked.
    fn concerns_run() -> Vec<Value> {
        let mut replies = vec![reply(Domain::D1, &[("1.2", "NI")])];
        replies.extend(Domain::ALL[1..4].iter().map(|d| reply(*d, &[])));
        replies.push(reply(Domain::D5, &[("5.1", "NI")]));
        replies
    }

    fn outcome(name: &str) -> ProposedOutcome {
        ProposedOutcome {
            name: name.to_string(),
            timepoint: Some("12 weeks".to_string()),
            result: Some(ReportedResult {
                measure: "mean difference".into(),
                scale: EffectScale::Difference,
                estimate: -4.2,
                ci_low: Some(-6.1),
                ci_high: Some(-2.3),
                ci_level: Some(0.95),
                n_analysed: Some(412),
            }),
            result_error: None,
            citations: Vec::new(),
            quotes_not_found: Vec::new(),
            contradicted: Vec::new(),
            quotes_dropped: 0,
        }
    }

    /// Answers each request with the next reply; sets `stop` at request `stop_at`.
    struct Scripted<'s> {
        replies: Vec<Result<Value, ChatJsonError>>,
        sent: Vec<String>,
        stop: Option<(&'s AtomicBool, usize)>,
    }

    impl<'s> Scripted<'s> {
        fn new(replies: Vec<Value>) -> Self {
            Scripted {
                replies: replies.into_iter().map(Ok).collect(),
                sent: Vec::new(),
                stop: None,
            }
        }
    }

    impl JsonChat for Scripted<'_> {
        fn chat_json(
            &mut self,
            messages: &[LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<ChatJsonOutcome, ChatJsonError> {
            assert_eq!(messages[0].content, ROB2_SYSTEM_PROMPT);
            self.sent.push(messages[messages.len() - 1].content.clone());
            if let Some((stop, at)) = self.stop {
                if self.sent.len() == at {
                    stop.store(true, Ordering::SeqCst);
                }
            }
            let reply = self.replies.get(self.sent.len() - 1).cloned();
            reply
                .expect("no more scripted replies")
                .map(|value| ChatJsonOutcome {
                    value,
                    attempts: 1,
                    repaired: false,
                })
        }
    }

    fn assessed(result: &DomainResult) -> &DomainAssessment {
        match result {
            DomainResult::Assessed(assessment) => assessment,
            DomainResult::Failed { domain, reason, .. } => panic!("{domain:?} failed: {reason}"),
        }
    }

    #[test]
    fn one_outcome_is_five_calls_in_domain_order() {
        let mut chat = Scripted::new(low_run(1));
        let run = Rob2Agent::new(&mut chat)
            .assess("A trial", TEXT, &[outcome("SBP")], SIM)
            .expect("run");
        assert_eq!(chat.sent.len(), 5);
        for (prompt, domain) in chat.sent.iter().zip(Domain::ALL) {
            assert!(prompt.contains(domain_title(domain)) && prompt.contains(TEXT));
        }
        assert!(!chat.sent[0].contains("Outcome assessed: SBP"));
        assert!(chat.sent[1..]
            .iter()
            .all(|p| p.contains("Outcome assessed: SBP") && p.contains("\"estimate\":-4.2")));
        assert!(chat.sent[4].contains(D5_OUTCOME_SWITCHING));
        assert!(!chat.sent[3].contains(D5_OUTCOME_SWITCHING));
        assert_eq!(run.algorithm_version, ROB2_ALGORITHM_VERSION);
        assert_eq!(run.prompt_version, ROB2_PROMPT_VERSION);
        let study = assessed(&run.study);
        assert_eq!(study.judgement, Judgement::Low);
        assert_eq!(study.answers["1.1"].citations[0].text, RANDOM);
        assert!(!study.condensed);
        let only = &run.outcomes[0];
        assert_eq!(
            only.domains
                .iter()
                .map(DomainResult::domain)
                .collect::<Vec<_>>(),
            Domain::ALL[1..].to_vec()
        );
        assert_eq!(
            (only.escalation.clone(), only.overall),
            (None, Some(Judgement::Low))
        );
    }

    #[test]
    fn two_outcomes_are_nine_calls() {
        let mut chat = Scripted::new(low_run(2));
        let run = Rob2Agent::new(&mut chat)
            .assess("A trial", TEXT, &[outcome("SBP"), outcome("DBP")], SIM)
            .expect("run");
        assert_eq!(chat.sent.len(), 9);
        assert!(chat.sent[5].contains("Outcome assessed: DBP"));
        assert!(run
            .outcomes
            .iter()
            .all(|o| o.overall == Some(Judgement::Low)));
    }

    #[test]
    fn two_some_concerns_ask_a_sixth_question() {
        for (escalate, overall) in [(true, Judgement::High), (false, Judgement::SomeConcerns)] {
            let mut replies = concerns_run();
            replies.push(json!({"escalate": escalate, "reasoning": "R."}));
            let mut chat = Scripted::new(replies);
            let run = Rob2Agent::new(&mut chat)
                .assess("A trial", TEXT, &[outcome("SBP")], SIM)
                .expect("run");
            assert_eq!(chat.sent.len(), 6);
            assert!(chat.sent[5].starts_with(ESCALATION_PROMPT));
            let only = &run.outcomes[0];
            assert_eq!(
                only.escalation,
                Some(Escalation {
                    escalate,
                    reasoning: "R.".into()
                })
            );
            assert_eq!(only.overall, Some(overall));
        }
    }

    #[test]
    fn an_unreadable_escalation_leaves_the_outcome_not_assessed() {
        let mut replies = concerns_run();
        replies.extend([json!({"escalate": "yes"}), json!({"escalate": "yes"})]);
        let mut chat = Scripted::new(replies);
        let run = Rob2Agent::new(&mut chat)
            .assess("A trial", TEXT, &[outcome("SBP")], SIM)
            .expect("run");
        assert_eq!(chat.sent.len(), 7);
        let only = &run.outcomes[0];
        assert_eq!((only.escalation.clone(), only.overall), (None, None));
        assert!(only
            .escalation_error
            .as_deref()
            .is_some_and(|e| e.contains("escalate is not true or false")));
        assert_eq!(
            (only.escalation_retryable, only.escalation_status),
            (false, None)
        );
        assert_eq!(Rob2Run::from_json(&run.to_json()), Ok(run));
    }

    #[test]
    fn a_failed_escalation_request_says_whether_to_retry_and_the_status() {
        for (retryable, status) in [(false, Some(401)), (true, Some(503)), (true, None)] {
            let mut replies = concerns_run();
            replies.push(json!({}));
            let mut chat = Scripted::new(replies);
            chat.replies[5] = Err(ChatJsonError::Transport {
                message: "down".into(),
                attempts: 1,
                retryable,
                status,
            });
            let run = Rob2Agent::new(&mut chat)
                .assess("A trial", TEXT, &[outcome("SBP")], SIM)
                .expect("run");
            let only = &run.outcomes[0];
            assert!(only.escalation_error.is_some());
            assert_eq!(
                (only.escalation_retryable, only.escalation_status),
                (retryable, status)
            );
        }
    }

    #[test]
    fn an_unreadable_domain_fails_alone() {
        let bad = json!({"answers": {"3.1": entry("Unclear")}});
        let mut replies = low_run(1);
        replies.splice(2..3, [bad.clone(), bad]);
        let mut chat = Scripted::new(replies);
        let run = Rob2Agent::new(&mut chat)
            .assess("A trial", TEXT, &[outcome("SBP")], SIM)
            .expect("run");
        assert_eq!(chat.sent.len(), 6);
        let only = &run.outcomes[0];
        let DomainResult::Failed {
            domain,
            reason,
            retryable,
            status,
        } = &only.domains[1]
        else {
            panic!("D3 should fail")
        };
        assert_eq!(*domain, Domain::D3);
        assert_eq!((*retryable, *status), (false, None));
        assert!(
            reason.contains("answers.3.1.answer \"Unclear\""),
            "{reason}"
        );
        for index in [0, 2, 3] {
            assessed(&only.domains[index]);
        }
        assert_eq!((only.overall, only.escalation.clone()), (None, None));
    }

    #[test]
    fn a_failed_domain_says_whether_to_retry_and_the_status() {
        for (retryable, status) in [(false, Some(401)), (true, Some(503)), (true, None)] {
            let mut chat = Scripted::new(low_run(1));
            chat.replies[1] = Err(ChatJsonError::Transport {
                message: "down".into(),
                attempts: 1,
                retryable,
                status,
            });
            let run = Rob2Agent::new(&mut chat)
                .assess("A trial", TEXT, &[outcome("SBP")], SIM)
                .expect("run");
            assert!(matches!(
                &run.outcomes[0].domains[0],
                DomainResult::Failed { retryable: r, status: s, .. }
                    if *r == retryable && *s == status
            ));
        }
    }

    #[test]
    fn a_stored_failure_with_a_bad_status_or_no_retryable_is_refused() {
        let good = DomainResult::Failed {
            domain: Domain::D2,
            reason: "x".into(),
            retryable: false,
            status: Some(401),
        };
        assert_eq!(DomainResult::from_json(&good.to_json()), Ok(good.clone()));
        for bad in [json!("401"), json!(99), json!(600), json!(true), json!(4.5)] {
            let mut stored = good.to_json();
            stored["status_code"] = bad;
            let error = DomainResult::from_json(&stored).expect_err("refused");
            assert!(error.contains("status_code"), "{error}");
        }
        let mut stored = good.to_json();
        let mut missing = good.to_json();
        missing
            .as_object_mut()
            .expect("object")
            .remove("status_code");
        assert!(DomainResult::from_json(&missing)
            .expect_err("refused")
            .contains("status_code"));
        stored.as_object_mut().expect("object").remove("retryable");
        assert!(DomainResult::from_json(&stored)
            .expect_err("refused")
            .contains("retryable"));
    }

    #[test]
    fn a_failed_request_fails_that_domain_alone() {
        let mut chat = Scripted::new(low_run(1));
        chat.replies[1] = Err(ChatJsonError::Transport {
            message: "down".into(),
            attempts: 1,
            retryable: true,
            status: None,
        });
        let run = Rob2Agent::new(&mut chat)
            .assess("A trial", TEXT, &[outcome("SBP")], SIM)
            .expect("run");
        let only = &run.outcomes[0];
        assert!(
            matches!(&only.domains[0], DomainResult::Failed { domain: Domain::D2, reason, .. } if reason.contains("down"))
        );
        assert_eq!(only.overall, None);
        assert_eq!(assessed(&only.domains[3]).judgement, Judgement::Low);
    }

    #[test]
    fn a_stop_between_domain_calls_ends_the_run() {
        let stop = AtomicBool::new(false);
        let mut chat = Scripted::new(low_run(1));
        chat.stop = Some((&stop, 2));
        let result = Rob2Agent::new(&mut chat).with_stop(&stop).assess(
            "A trial",
            TEXT,
            &[outcome("SBP")],
            SIM,
        );
        assert_eq!(result, Err(Rob2RunError::Stopped));
        assert_eq!(chat.sent.len(), 2);
    }

    #[test]
    fn a_refused_call_sends_nothing() {
        let mut chat = Scripted::new(low_run(1));
        let sbp = [outcome("SBP")];
        for (text, outcomes, similarity, error) in [
            (TEXT, &[][..], SIM, Rob2RunError::NoOutcomes),
            ("  ", &sbp[..], SIM, Rob2RunError::EmptyInput),
            (TEXT, &sbp[..], 0.0, Rob2RunError::InvalidSimilarity),
        ] {
            let result = Rob2Agent::new(&mut chat).assess("A trial", text, outcomes, similarity);
            assert_eq!(result, Err(error));
        }
        assert!(chat.sent.is_empty());
        assert_eq!(
            Rob2RunError::NoOutcomes.to_string(),
            "no primary outcome to assess"
        );
    }

    /// A condenser returning a fixed digest and recording its queries.
    pub(crate) struct Condenser(pub(crate) Vec<String>);

    impl QueryCondenser for Condenser {
        fn condense(
            &mut self,
            query: &str,
            _text: &str,
            _label: &str,
            _config: &ProcessingConfig,
        ) -> ProcessingResult {
            self.0.push(query.to_string());
            let mut result = ProcessingResult::failed("unused");
            result.status = ProcessingStatus::Completed;
            result.final_result.content = "A digest.".to_string();
            result
        }
    }

    #[test]
    fn a_paper_over_the_window_is_condensed_once_per_domain() {
        let text = format!("{TEXT} {}", "Filler sentence about nothing. ".repeat(20));
        let mut condenser = Condenser(Vec::new());
        let mut chat = Scripted::new(low_run(2));
        let run = Rob2Agent::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(200))
            .assess("A trial", &text, &[outcome("SBP"), outcome("DBP")], SIM)
            .expect("run");
        let queries: Vec<String> = ROB2_CONDENSE_QUERIES
            .iter()
            .map(|(_, q)| (*q).to_string())
            .collect();
        assert_eq!(condenser.0, queries);
        assert!(chat
            .sent
            .iter()
            .all(|p| p.contains("A digest.") && !p.contains("Filler sentence")));
        assert!(std::iter::once(&run.study)
            .chain(run.outcomes.iter().flat_map(|o| &o.domains))
            .all(|d| assessed(d).condensed));
        // Quotes are verified against the original text, not the digest.
        assert_eq!(
            assessed(&run.study).answers["1.1"].citations[0].text,
            RANDOM
        );
    }

    #[test]
    fn a_paper_over_the_window_without_a_condenser_fails_every_domain_visibly() {
        let mut chat = Scripted::new(low_run(1));
        let run = Rob2Agent::new(&mut chat)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(20))
            .assess("A trial", TEXT, &[outcome("SBP")], SIM)
            .expect("run");
        assert!(chat.sent.is_empty());
        assert!(
            matches!(&run.study, DomainResult::Failed { reason, .. } if reason.contains("condenser"))
        );
        assert_eq!(run.outcomes[0].overall, None);
    }

    #[test]
    fn from_json_refuses_what_the_agent_would_not_make() {
        let mut replies = concerns_run();
        replies.push(json!({"escalate": true, "reasoning": "R."}));
        let mut chat = Scripted::new(replies);
        let run = Rob2Agent::new(&mut chat)
            .assess("A trial", TEXT, &[outcome("SBP")], SIM)
            .expect("run");
        let good = run.to_json();
        assert_eq!(Rob2Run::from_json(&good), Ok(run));
        let refused = |change: &dyn Fn(&mut Value)| {
            let mut data = good.clone();
            change(&mut data);
            Rob2Run::from_json(&data).expect_err("refused")
        };
        for (change, word) in [
            (
                &(|d: &mut Value| d["algorithm_version"] = json!("other")) as &dyn Fn(&mut Value),
                "algorithm_version",
            ),
            (
                &|d: &mut Value| d["prompt_version"] = json!(true),
                "prompt_version",
            ),
            (
                &|d: &mut Value| d["prompt_version"] = json!(-1),
                "prompt_version",
            ),
            (
                &|d: &mut Value| d["study"]["status"] = json!("maybe"),
                "status",
            ),
            (
                &|d: &mut Value| d["study"]["judgement"] = json!("high"),
                "judgement",
            ),
            (
                &|d: &mut Value| d["study"]["condensed"] = json!("no"),
                "condensed",
            ),
            (
                &|d: &mut Value| d["study"]["answers"]["1.1"]["answer"] = json!("py"),
                "answer",
            ),
            (
                &|d: &mut Value| d["study"]["answers"]["1.1"]["quotes_dropped"] = json!(true),
                "quotes_dropped",
            ),
            (
                &|d: &mut Value| d["study"]["answers"]["1.9"] = json!({}),
                "1.9",
            ),
            (
                &|d: &mut Value| d["study"]["ignored"] = json!(["1.1"]),
                "ignored",
            ),
            (&|d: &mut Value| d["study"]["domain"] = json!("D2"), "study"),
            (
                &|d: &mut Value| d["outcomes"][0]["overall"] = json!("low"),
                "overall",
            ),
            (
                &|d: &mut Value| d["outcomes"][0]["escalation"] = Value::Null,
                "overall",
            ),
            (
                &|d: &mut Value| d["outcomes"][0]["escalation_error"] = json!("x"),
                "escalation_error",
            ),
            (
                &|d: &mut Value| {
                    d["outcomes"][0]
                        .as_object_mut()
                        .expect("object")
                        .remove("escalation_status_code");
                },
                "escalation_status_code",
            ),
            (
                &|d: &mut Value| d["outcomes"][0]["escalation_retryable"] = json!(true),
                "escalation_retryable",
            ),
            (
                &|d: &mut Value| d["outcomes"][0]["escalation_status_code"] = json!(99),
                "escalation_status_code",
            ),
            (
                &|d: &mut Value| d["outcomes"][0]["escalation"]["escalate"] = json!("yes"),
                "escalate",
            ),
            (
                &|d: &mut Value| {
                    d["outcomes"][0]["domains"]
                        .as_array_mut()
                        .expect("list")
                        .truncate(3)
                },
                "domains",
            ),
        ] {
            let message = refused(change);
            assert!(message.contains(word), "{word}: {message}");
        }
        let study = assessed(&Rob2Run::from_json(&good).expect("run").study).clone();
        let mut lone = good["outcomes"][0].clone();
        lone["domains"][1] = json!({"status": "failed", "domain": "D3", "reason": "r", "retryable": false, "status_code": null});
        let failed = OutcomeRob2::from_json(&lone).expect("reads alone");
        assert!(check_overall(&DomainResult::Assessed(study), &failed)
            .expect_err("an escalation with a failed domain")
            .contains("escalation"));
    }

    #[test]
    fn domain_prompt_refuses_an_outcome_on_the_wrong_domain() {
        let sbp = outcome("SBP");
        assert!(domain_prompt(Domain::D1, "t", "p", Some(&sbp))
            .expect_err("refused")
            .contains("D1"));
        assert!(domain_prompt(Domain::D2, "t", "p", None)
            .expect_err("refused")
            .contains("D2"));
        let mut bare = sbp;
        bare.timepoint = None;
        bare.result = None;
        let prompt = domain_prompt(Domain::D2, "t", "p", Some(&bare)).expect("prompt");
        assert!(prompt.contains("Timepoint: not stated\nReported result: none extracted"));
    }
}
