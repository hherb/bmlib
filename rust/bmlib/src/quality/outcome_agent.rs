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

//! The primary-outcome proposal: which outcomes the paper calls primary, and
//! the results it reports for them.
//!
//! A port of `bmlib/quality/outcome_agent.py`; the contract is
//! `tests/fixtures/outcome_replies.json`.

use std::sync::atomic::AtomicBool;

use serde_json::{json, Value};

use crate::context_processor::ProcessingConfig;
use crate::pyvalue::python_strip;
use crate::quality::agent_chat::JsonChat;
use crate::quality::cochrane_assessor::{
    DEFAULT_CONDENSE_THRESHOLD_CHARS, DEFAULT_MAX_RETRIES, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE,
};
pub use crate::quality::design_agent::ProposalError;
use crate::quality::evidence_ladder::{EffectScale, ReportedResult};
use crate::quality::full_text_agent::{
    ask_until_readable, check_similarity, check_stop, condense_once, Sampling, LABEL_CHARS,
};
pub use crate::quality::full_text_agent::{LlmQueryCondenser, QueryCondenser};
use crate::quality::pico_agent::verify_quotes;
use crate::quality::pico_models::{PicoCitation, PicoContradictedQuote};

/// What a condensation's note calls the call it is for.
const CONDENSE_STAGE: &str = "the outcome proposal";

/// Message for an `n_analysed` that is not a count that fits 32 bits (the same
/// words as Python's `ReportedResult.validate`).
const N_ANALYSED_RANGE: &str = "n_analysed must be a whole number that fits 32 bits";

/// Bumped whenever the prompts below change, so a stored proposal says which
/// wording produced it. Python's `OUTCOME_PROMPT_VERSION`.
pub const OUTCOME_PROMPT_VERSION: u32 = 1;

/// The system turn. Byte-identical to Python's; the contract pins it.
pub const OUTCOME_SYSTEM_PROMPT: &str = r##"You are an expert in clinical research methodology and evidence-based medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, calculate or fabricate any information or number
3. List only outcomes the paper itself calls primary; never choose one yourself
4. Return ONLY valid JSON, no explanation"##;

/// The question, with the reply shape; the paper follows it. Byte-identical to
/// Python's; the contract pins it.
pub const OUTCOME_TASK_PROMPT: &str = r##"Find the PRIMARY outcomes of the research paper below, and the result the paper reports for each.

primary_outcomes: only the outcomes the paper itself calls primary (or co-primary). Do not list secondary, exploratory or safety outcomes, and do not pick an outcome because it seems the most important. If the paper names no primary outcome, return an empty list.

For each primary outcome give:
- name: the outcome as the paper names it
- timepoint: when it was measured (for example "12 weeks"), or null if the paper does not say
- quotes: 1-3 passages copied VERBATIM from the paper that show the outcome is primary or give its result. Copy them character for character; do not paraphrase, shorten with ellipses, or join sentences.
- result: the effect the paper reports for the primary analysis of this outcome, or null if the paper reports no effect estimate for it:
  - measure: the name of the measure as the paper gives it (for example "risk ratio", "mean difference")
  - scale: "ratio" for a ratio (risk ratio, odds ratio, hazard ratio), "difference" for a difference (mean difference, risk difference)
  - estimate: the point estimate, as a number
  - ci_low, ci_high: the confidence interval bounds, or null if not reported
  - ci_level: the interval's level as printed (95 for a 95% interval), or null if not stated
  - n_analysed: the number of participants analysed for this result, or null if not reported
Copy numbers exactly as printed; do not calculate, convert or round.

Respond with JSON in exactly this shape:

{
    "primary_outcomes": [
        {
            "name": "Systolic blood pressure",
            "timepoint": "12 weeks",
            "quotes": ["The primary outcome was change in systolic blood pressure at 12 weeks."],
            "result": {
                "measure": "mean difference",
                "scale": "difference",
                "estimate": -4.2,
                "ci_low": -6.1,
                "ci_high": -2.3,
                "ci_level": 95,
                "n_analysed": 412
            }
        }
    ]
}"##;

/// What the condensation gathers when the paper does not fit the window.
pub const OUTCOME_CONDENSE_QUERY: &str = r##"Everything that names the study's primary outcome or outcomes (including co-primary outcomes) and when each was measured, and the results of the primary analysis: effect estimates with their confidence intervals and the number of participants analysed."##;

// --- Reading a reply ---

/// `value[key]` as text, or a message naming `path` + `key`.
fn text_field<'v>(value: &'v Value, key: &str, path: &str) -> Result<&'v str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{path}{key} is not text"))
}

/// `value` as a finite number; anything else is refused naming the field.
fn number(value: Option<&Value>, name: &str, path: &str) -> Result<f64, String> {
    value
        .and_then(Value::as_f64)
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("{path}{name} is not a finite number"))
}

/// A level printed as a percentage (`95` for "95% CI") is divided by this.
/// Python's `PERCENT`.
pub const PERCENT: f64 = 100.0;

/// Message for a `ci_level` that is neither a percentage nor a fraction.
const CI_LEVEL_RANGE: &str =
    "ci_level is neither a percentage in (1, 100) nor a fraction in (0, 1)";

/// Read the `result` of one outcome in a model's reply into
/// `(result, result_error)`. Python's `_read_reply_result`.
///
/// `ci_level` is read as printed: a value in (1, 100) is a percentage and is
/// divided by [`PERCENT`]; a value in (0, 1) is a fraction. Any field that
/// cannot be read (a number that is text, null or not finite, a scale other
/// than `ratio`/`difference`, a level that is neither) refuses this outcome's
/// result only, as `(None, Some(reason))` naming the field; the outcome
/// itself stays.
///
/// # Errors
///
/// A message when `raw` is neither null nor an object.
fn read_reply_result(
    raw: &Value,
    path: &str,
) -> Result<(Option<ReportedResult>, Option<String>), String> {
    let Some(object) = raw.as_object() else {
        if raw.is_null() {
            return Ok((None, None));
        }
        return Err(format!("{path}result is not an object"));
    };
    let mut object = object.clone();
    if let Some(level) = object.get("ci_level").and_then(Value::as_f64) {
        if 1.0 < level && level < PERCENT {
            object.insert("ci_level".to_string(), json!(level / PERCENT));
        } else if !(0.0 < level && level < 1.0) {
            return Ok((None, Some(format!("result.{CI_LEVEL_RANGE}"))));
        }
    }
    Ok(read_result(&Value::Object(object), "").unwrap_or_else(|reason| (None, Some(reason))))
}

/// Read a `result` in its wire form (`ci_level` a fraction) into
/// `(result, result_error)`: the strict reader behind
/// [`ProposedOutcome::from_json`], and behind `read_reply_result` once the
/// level is a fraction. Python's `_read_result`.
///
/// # Errors
///
/// A message when `raw` is neither null nor an object, the measure is not
/// text, the scale is not `ratio`/`difference`, or a number field is not a
/// finite number. A result that reads but is incoherent is not an error here:
/// it comes back as `(None, Some(reason))`.
fn read_result(
    raw: &Value,
    path: &str,
) -> Result<(Option<ReportedResult>, Option<String>), String> {
    if raw.is_null() {
        return Ok((None, None));
    }
    let path = format!("{path}result.");
    if !raw.is_object() {
        return Err(format!("{} is not an object", &path[..path.len() - 1]));
    }
    let measure = text_field(raw, "measure", &path)?.to_string();
    let label = text_field(raw, "scale", &path)?;
    let scale = match python_strip(&label.to_lowercase()) {
        "ratio" => EffectScale::Ratio,
        "difference" => EffectScale::Difference,
        _ => return Err(format!("{path}scale \"{label}\" is not a scale")),
    };
    let estimate = number(raw.get("estimate"), "estimate", &path)?;
    let optional = |name: &str| -> Result<Option<f64>, String> {
        match raw.get(name) {
            None | Some(Value::Null) => Ok(None),
            some => number(some, name, &path).map(Some),
        }
    };
    let (ci_low, ci_high, ci_level) = (
        optional("ci_low")?,
        optional("ci_high")?,
        optional("ci_level")?,
    );
    let n_analysed = match raw.get("n_analysed") {
        None | Some(Value::Null) => None,
        Some(Value::Number(n)) if n.is_i64() || n.is_u64() => {
            match n.as_u64().and_then(|u| u32::try_from(u).ok()) {
                Some(count) => Some(count),
                None => return Ok((None, Some(N_ANALYSED_RANGE.to_string()))),
            }
        }
        Some(_) => return Err(format!("{path}n_analysed is not a whole number")),
    };
    let result = ReportedResult {
        measure,
        scale,
        estimate,
        ci_low,
        ci_high,
        ci_level,
        n_analysed,
    };
    Ok(match result.validate() {
        Ok(()) => (Some(result), None),
        Err(reason) => (None, Some(reason)),
    })
}

/// One primary outcome the paper names, with its result and passages.
#[derive(Debug, Clone, PartialEq)]
pub struct ProposedOutcome {
    /// The outcome as the paper names it.
    pub name: String,
    /// When it was measured, `None` if not said.
    pub timepoint: Option<String>,
    /// The reported effect; `None` when the paper gives none, or when it was
    /// incoherent (then see `result_error`).
    pub result: Option<ReportedResult>,
    /// Why the reported effect was refused, naming the field; `None` when
    /// `result` is set or the paper gave none.
    pub result_error: Option<String>,
    /// The paper's own spans for the quotes found, offsets in code points.
    pub citations: Vec<PicoCitation>,
    /// The quotes not located in the paper, verbatim.
    pub quotes_not_found: Vec<String>,
    /// The quotes the paper contradicts (a changed number, negation or
    /// direction), each with the reason; never also in `quotes_not_found`.
    pub contradicted: Vec<PicoContradictedQuote>,
    /// How many quote members were not usable text (blank, or not a string)
    /// and so were never searched for.
    pub quotes_dropped: usize,
}

/// `value[key]`, or a message naming the absent key.
fn field<'v>(value: &'v Value, key: &str) -> Result<&'v Value, String> {
    value.get(key).ok_or_else(|| format!("{key} is missing"))
}

impl ProposedOutcome {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "timepoint": self.timepoint,
            "result": self.result,
            "result_error": self.result_error,
            "citations": self.citations.iter().map(PicoCitation::to_json).collect::<Vec<_>>(),
            "quotes_not_found": self.quotes_not_found,
            "contradicted": self.contradicted.iter().map(PicoContradictedQuote::to_json).collect::<Vec<_>>(),
            "quotes_dropped": self.quotes_dropped,
        })
    }

    /// Python's `from_dict()`: refuses what the reader refuses.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent, of the wrong type or value.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let timepoint = match value.get("timepoint") {
            None | Some(Value::Null) => None,
            Some(Value::String(text)) => Some(text.clone()),
            Some(_) => return Err("timepoint is not text".to_string()),
        };
        let result_error = match value.get("result_error") {
            None | Some(Value::Null) => None,
            Some(Value::String(text)) => Some(text.clone()),
            Some(_) => return Err("result_error is not text".to_string()),
        };
        let raw = value.get("result").ok_or("result is not an object")?;
        let (result, refusal) = read_result(raw, "")?;
        if let Some(refusal) = refusal {
            return Err(format!("result is incoherent: {refusal}"));
        }
        if result.is_some() && result_error.is_some() {
            return Err("result and result_error are both set".to_string());
        }
        let citations = field(value, "citations")?
            .as_array()
            .ok_or("citations is not a list")?
            .iter()
            .map(PicoCitation::from_json)
            .collect::<Result<Vec<_>, _>>()?;
        let quotes_not_found = field(value, "quotes_not_found")?
            .as_array()
            .ok_or("quotes_not_found is not a list")?
            .iter()
            .map(|q| q.as_str().map(str::to_string))
            .collect::<Option<Vec<_>>>()
            .ok_or("quotes_not_found is not a list of text")?;
        let contradicted = field(value, "contradicted")?
            .as_array()
            .ok_or("contradicted is not a list")?
            .iter()
            .map(PicoContradictedQuote::from_json)
            .collect::<Result<Vec<_>, _>>()?;
        let quotes_dropped = field(value, "quotes_dropped")?
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or("quotes_dropped is not a count")?;
        Ok(ProposedOutcome {
            name: field(value, "name")?
                .as_str()
                .ok_or("name is not text")?
                .to_string(),
            timepoint,
            result,
            result_error,
            citations,
            quotes_not_found,
            contradicted,
            quotes_dropped,
        })
    }
}

/// The primary outcomes the paper names; empty when it names none.
#[derive(Debug, Clone, PartialEq)]
pub struct OutcomeProposal {
    /// The proposed outcomes, in the order the model gave them.
    pub outcomes: Vec<ProposedOutcome>,
    /// Whether the model saw a digest instead of the whole paper.
    pub condensed: bool,
}

impl OutcomeProposal {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "outcomes": self.outcomes.iter().map(ProposedOutcome::to_json).collect::<Vec<_>>(),
            "condensed": self.condensed,
        })
    }

    /// Python's `from_dict()`.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent, of the wrong type or value.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        Ok(OutcomeProposal {
            outcomes: field(value, "outcomes")?
                .as_array()
                .ok_or("outcomes is not a list")?
                .iter()
                .map(ProposedOutcome::from_json)
                .collect::<Result<Vec<_>, _>>()?,
            condensed: field(value, "condensed")?
                .as_bool()
                .ok_or("condensed is not a boolean")?,
        })
    }
}

/// Read the model's reply and verify its quotes against `text`. Python's
/// `read_outcome_reply`.
///
/// # Errors
///
/// A message when `min_similarity` is outside (0, 1], the reply is not an
/// object, `primary_outcomes` is not a list, an outcome is not an object or
/// has no name, a timepoint is not text, or a result is present but not an
/// object. The message names the outcome (`primary_outcomes[1].`) and the
/// field. A field inside a result that cannot be read refuses that outcome's
/// result only (its `result_error`).
pub fn read_outcome_reply(
    data: &Value,
    text: &str,
    min_similarity: f64,
) -> Result<OutcomeProposal, String> {
    check_similarity(min_similarity)?;
    if !data.is_object() {
        return Err("expected an object".to_string());
    }
    let items = data
        .get("primary_outcomes")
        .and_then(Value::as_array)
        .ok_or("primary_outcomes is not a list")?;

    let mut outcomes = Vec::with_capacity(items.len());
    for (index, item) in items.iter().enumerate() {
        let path = format!("primary_outcomes[{index}].");
        if !item.is_object() {
            return Err(format!("{} is not an object", &path[..path.len() - 1]));
        }
        let name = python_strip(text_field(item, "name", &path)?);
        if name.is_empty() {
            return Err(format!("{path}name is empty"));
        }
        let timepoint = match item.get("timepoint") {
            None | Some(Value::Null) => None,
            Some(Value::String(point)) => Some(python_strip(point)).filter(|p| !p.is_empty()),
            Some(_) => return Err(format!("{path}timepoint is not text")),
        };
        let raw = item
            .get("result")
            .ok_or_else(|| format!("{path}result is missing"))?;
        let (result, result_error) = read_reply_result(raw, &path)?;
        let (citations, quotes_not_found, contradicted, quotes_dropped) =
            verify_quotes(item.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
        outcomes.push(ProposedOutcome {
            name: name.to_string(),
            timepoint: timepoint.map(str::to_string),
            result,
            result_error,
            citations,
            quotes_not_found,
            contradicted,
            quotes_dropped,
        });
    }
    Ok(OutcomeProposal {
        outcomes,
        condensed: false,
    })
}

// --- The agent ---

/// Proposes the paper's primary outcomes, backed by passages of the paper.
///
/// Text over `condense_config.max_context_chars` is condensed once by the
/// configured [`QueryCondenser`] (see [`LlmQueryCondenser`]); with none
/// configured it is refused as [`ProposalError::CondensationFailed`], never
/// sent whole or cut. A stop is read before each request and the condensation.
pub struct OutcomeAgent<'a> {
    chat: &'a mut dyn JsonChat,
    condenser: Option<&'a mut dyn QueryCondenser>,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    condense_config: ProcessingConfig,
    stop: Option<&'a AtomicBool>,
}

impl<'a> OutcomeAgent<'a> {
    /// An agent asking through `chat`, with the Cochrane assessor's defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        OutcomeAgent {
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

    /// End a call before its next request once `stop` is set.
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

    /// Propose the paper's primary outcomes.
    ///
    /// Citation offsets are code points into `text` exactly as passed.
    ///
    /// # Errors
    ///
    /// [`ProposalError`] naming why no proposal could be made.
    pub fn propose(
        &mut self,
        title: &str,
        text: &str,
        min_similarity: f64,
    ) -> Result<OutcomeProposal, ProposalError> {
        check_similarity(min_similarity).map_err(|_| ProposalError::InvalidSimilarity)?;
        let title = python_strip(title);
        let stripped = python_strip(text);
        if stripped.is_empty() {
            return Err(ProposalError::EmptyInput);
        }
        check_stop(self.stop)?;

        let condensed = stripped.chars().count() > self.condense_config.max_context_chars;
        let digest;
        let sent = if condensed {
            digest = condense_once(
                match self.condenser.as_mut() {
                    Some(condenser) => Some(&mut **condenser),
                    None => None,
                },
                OUTCOME_CONDENSE_QUERY,
                title,
                stripped,
                &self.condense_config,
                CONDENSE_STAGE,
                self.stop,
            )?;
            digest.as_str()
        } else {
            stripped
        };

        let prompt =
            format!("{OUTCOME_TASK_PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{sent}");
        let label = format!(
            "outcome proposal of {}",
            crate::pyvalue::repr_str(&title.chars().take(LABEL_CHARS).collect::<String>())
        );
        let sampling = Sampling {
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            max_retries: self.max_retries,
        };
        let mut proposal = ask_until_readable(
            &mut *self.chat,
            OUTCOME_SYSTEM_PROMPT,
            &prompt,
            sampling,
            self.stop,
            &label,
            |data| read_outcome_reply(data, text, min_similarity),
        )?;
        proposal.condensed = condensed;
        Ok(proposal)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::{ChatJsonError, ChatJsonOutcome};
    use crate::context_processor::ProcessingResult;
    use crate::llm::LLMMessage;
    use crate::quality::agent_chat::JsonChat;
    use serde_json::json;
    use std::sync::atomic::AtomicBool;

    const TEXT: &str = "The primary outcome was all-cause mortality at 28 days. The risk ratio was 0.70 (95% CI 0.55 to 0.88), n=1200.";
    const QUOTE: &str = "The primary outcome was all-cause mortality at 28 days.";
    const SIM: f64 = 0.9;

    fn merged(mut base: Value, overrides: Value) -> Value {
        for (key, item) in overrides.as_object().expect("an object") {
            base[key] = item.clone();
        }
        base
    }

    fn result(overrides: Value) -> Value {
        merged(
            json!({
                "measure": "risk ratio", "scale": "ratio", "estimate": 0.7,
                "ci_low": 0.55, "ci_high": 0.88, "ci_level": 0.95, "n_analysed": 1200,
            }),
            overrides,
        )
    }

    fn outcome(overrides: Value) -> Value {
        merged(
            json!({
                "name": "All-cause mortality", "timepoint": "28 days",
                "quotes": [QUOTE], "result": result(json!({})),
            }),
            overrides,
        )
    }

    fn reply(outcomes: Vec<Value>) -> Value {
        json!({ "primary_outcomes": outcomes })
    }

    fn err_of(data: &Value) -> String {
        read_outcome_reply(data, TEXT, SIM).expect_err("a refusal")
    }

    fn only(data: &Value) -> ProposedOutcome {
        let mut proposal = read_outcome_reply(data, TEXT, SIM).expect("read");
        assert_eq!(proposal.outcomes.len(), 1);
        proposal.outcomes.remove(0)
    }

    #[test]
    fn one_outcome_with_a_ratio_result_and_a_found_quote() {
        let found = only(&reply(vec![outcome(json!({}))]));
        assert_eq!(found.name, "All-cause mortality");
        assert_eq!(found.timepoint.as_deref(), Some("28 days"));
        assert_eq!(
            found.result,
            Some(ReportedResult {
                measure: "risk ratio".into(),
                scale: EffectScale::Ratio,
                estimate: 0.7,
                ci_low: Some(0.55),
                ci_high: Some(0.88),
                ci_level: Some(0.95),
                n_analysed: Some(1200),
            })
        );
        assert_eq!(found.result_error, None);
        assert_eq!(found.citations.len(), 1);
        assert_eq!(found.citations[0].text, QUOTE);
        assert_eq!(found.citations[0].end, QUOTE.chars().count());
    }

    #[test]
    fn co_primary_outcomes_keep_their_order_and_none_is_valid() {
        let second =
            outcome(json!({"name": "Admission", "timepoint": null, "quotes": [], "result": null}));
        let proposal =
            read_outcome_reply(&reply(vec![outcome(json!({})), second]), TEXT, SIM).expect("read");
        assert_eq!(proposal.outcomes[1].name, "Admission");
        assert_eq!(proposal.outcomes[1].timepoint, None);
        assert!(
            proposal.outcomes[1].result.is_none() && proposal.outcomes[1].result_error.is_none()
        );
        assert!(!proposal.condensed);
        let none = read_outcome_reply(&reply(vec![]), TEXT, SIM).expect("read");
        assert!(none.outcomes.is_empty());
    }

    #[test]
    fn an_incoherent_result_is_kept_out_with_its_reason() {
        for (overrides, reason) in [
            (
                json!({"ci_low": 0.9, "ci_high": 0.5}),
                "ci_low <= estimate <= ci_high does not hold",
            ),
            (
                json!({"ci_low": 0.0, "estimate": 0.4}),
                "ci_low must be above 0 on the ratio scale",
            ),
            (
                json!({"ci_high": null}),
                "ci_low and ci_high must be given together",
            ),
            (json!({"n_analysed": -3}), N_ANALYSED_RANGE),
            (json!({"n_analysed": 4_294_967_296_u64}), N_ANALYSED_RANGE),
        ] {
            let found = only(&reply(vec![outcome(json!({"result": result(overrides)}))]));
            assert_eq!(found.result, None);
            assert_eq!(found.result_error.as_deref(), Some(reason));
            assert_eq!(found.citations.len(), 1, "the outcome itself stands");
        }
    }

    #[test]
    fn optional_numbers_may_be_absent_and_a_scale_is_folded() {
        let bare = json!({"measure": "risk ratio", "scale": "ratio", "estimate": 0.7});
        let found = only(&reply(vec![outcome(json!({"result": bare}))]));
        let bare_result = found.result.expect("a result");
        assert_eq!((bare_result.ci_low, bare_result.n_analysed), (None, None));
        let folded =
            result(json!({"scale": " Difference ", "estimate": -2, "ci_low": -3, "ci_high": -1}));
        let found = only(&reply(vec![outcome(json!({"result": folded}))]));
        let folded = found.result.expect("a result");
        assert_eq!(folded.scale, EffectScale::Difference);
        assert_eq!(folded.estimate, -2.0);
    }

    #[test]
    fn an_unreadable_field_refuses_only_that_outcomes_result() {
        for (raw, message) in [
            (
                result(json!({"scale": "percent"})),
                "result.scale \"percent\" is not a scale",
            ),
            (result(json!({"scale": 1})), "result.scale is not text"),
            (
                result(json!({"measure": null})),
                "result.measure is not text",
            ),
            (
                result(json!({"estimate": null})),
                "result.estimate is not a finite number",
            ),
            (
                result(json!({"estimate": true})),
                "result.estimate is not a finite number",
            ),
            (
                result(json!({"estimate": "0.7"})),
                "result.estimate is not a finite number",
            ),
            (
                result(json!({"ci_low": "low"})),
                "result.ci_low is not a finite number",
            ),
            (
                result(json!({"n_analysed": 1200.5})),
                "result.n_analysed is not a whole number",
            ),
            (
                result(json!({"n_analysed": 1200.0})),
                "result.n_analysed is not a whole number",
            ),
            (
                result(json!({"n_analysed": true})),
                "result.n_analysed is not a whole number",
            ),
            (
                result(json!({"ci_low": "NR"})),
                "result.ci_low is not a finite number",
            ),
        ] {
            let second = outcome(json!({"name": "Hospitalisation"}));
            let proposal = read_outcome_reply(
                &reply(vec![outcome(json!({"result": raw})), second]),
                TEXT,
                SIM,
            )
            .expect("read");
            let (bad, good) = (&proposal.outcomes[0], &proposal.outcomes[1]);
            assert_eq!(bad.result, None);
            assert_eq!(bad.result_error.as_deref(), Some(message));
            assert_eq!(bad.citations.len(), 1, "the outcome itself stands");
            assert!(good.result.is_some() && good.result_error.is_none());
        }
    }

    #[test]
    fn a_result_that_is_not_an_object_refuses_the_reply() {
        assert_eq!(
            err_of(&reply(vec![outcome(json!({"result": "risk ratio 0.7"}))])),
            "primary_outcomes[0].result is not an object"
        );
    }

    #[test]
    fn the_level_is_read_as_printed() {
        for (printed, level) in [(json!(95), 0.95), (json!(90.0), 0.9), (json!(0.95), 0.95)] {
            let found = only(&reply(vec![outcome(
                json!({"result": result(json!({"ci_level": printed}))}),
            )]));
            assert_eq!(found.result.expect("a result").ci_level, Some(level));
        }
    }

    #[test]
    fn a_level_neither_percentage_nor_fraction_is_refused_naming_it() {
        for printed in [json!(0), json!(1), json!(100), json!(150), json!(-95)] {
            let found = only(&reply(vec![outcome(
                json!({"result": result(json!({"ci_level": printed}))}),
            )]));
            assert_eq!(found.result, None);
            assert_eq!(
                found.result_error.as_deref(),
                Some(&*format!("result.{CI_LEVEL_RANGE}"))
            );
        }
    }

    #[test]
    fn a_stored_level_is_a_fraction_and_is_not_converted() {
        let mut stored = only(&reply(vec![outcome(json!({}))])).to_json();
        stored["result"]["ci_level"] = json!(95);
        let refused = ProposedOutcome::from_json(&stored).expect_err("refused");
        assert!(
            refused.contains("ci_level must be above 0 and below 1"),
            "{refused}"
        );
    }

    #[test]
    fn a_malformed_reply_names_the_field() {
        let missing = json!({"name": "x", "quotes": []});
        for (bad, message) in [
            (
                json!({"primary_outcomes": "none"}),
                "primary_outcomes is not a list",
            ),
            (json!({}), "primary_outcomes is not a list"),
            (json!(["x"]), "expected an object"),
            (
                reply(vec![json!("x")]),
                "primary_outcomes[0] is not an object",
            ),
            (
                reply(vec![outcome(json!({"name": null}))]),
                "primary_outcomes[0].name is not text",
            ),
            (
                reply(vec![outcome(json!({"name": "  "}))]),
                "primary_outcomes[0].name is empty",
            ),
            (
                reply(vec![outcome(json!({"timepoint": 3}))]),
                "primary_outcomes[0].timepoint is not text",
            ),
            (
                reply(vec![missing]),
                "primary_outcomes[0].result is missing",
            ),
        ] {
            assert_eq!(err_of(&bad), message);
        }
        for bad in [0.0, 1.5, -1.0, f64::NAN] {
            let message = read_outcome_reply(&reply(vec![]), TEXT, bad).expect_err("refused");
            assert!(message.contains("min_similarity"), "{message}");
        }
    }

    #[test]
    fn lost_contradicted_and_dropped_quotes_each_have_their_place() {
        let lost = "Patients were followed for a year.";
        let changed = "The primary outcome was all-cause mortality at 90 days.";
        let quotes = json!({"quotes": [lost, changed, QUOTE, "  ", 7, null]});
        let found = only(&reply(vec![outcome(quotes)]));
        assert_eq!(found.citations.len(), 1);
        assert_eq!(found.quotes_not_found, vec![lost]);
        assert_eq!(
            found.contradicted,
            vec![PicoContradictedQuote {
                quote: changed.to_string(),
                contradiction: crate::citations::PassageContradiction::Number,
            }]
        );
        assert_eq!(found.quotes_dropped, 3);
        assert_eq!(
            only(&reply(vec![outcome(json!({"timepoint": "  "}))])).timepoint,
            None
        );
    }

    #[test]
    fn the_proposal_round_trips_and_from_json_refuses_what_the_reader_refuses() {
        let quotes = json!({"quotes": [QUOTE, "lost words here", "The primary outcome was all-cause mortality at 90 days."]});
        let proposal = read_outcome_reply(
            &reply(vec![
                outcome(quotes),
                outcome(json!({"name": "Second", "result": result(json!({"ci_low": 0.9, "ci_high": 0.5}))})),
                outcome(json!({"name": "Third", "result": null, "quotes": [" "]})),
            ]),
            TEXT,
            SIM,
        )
        .expect("read");
        assert!(!proposal.outcomes[0].contradicted.is_empty());
        assert!(proposal.outcomes[1].result_error.is_some());
        assert_eq!(proposal.outcomes[2].quotes_dropped, 1);
        assert_eq!(
            OutcomeProposal::from_json(&proposal.to_json()),
            Ok(proposal.clone())
        );

        let good = proposal.outcomes[0].to_json();
        let with = |key: &str, bad: Value| {
            let mut value = good.clone();
            value[key] = bad;
            ProposedOutcome::from_json(&value).expect_err("refused")
        };
        let good_result = good["result"].clone();
        for (key, bad) in [
            ("name", json!(3)),
            ("timepoint", json!(3)),
            ("result", json!("x")),
            (
                "result",
                merged(good_result.clone(), json!({"scale": "percent"})),
            ),
            (
                "result",
                merged(good_result.clone(), json!({"estimate": true})),
            ),
            (
                "result",
                merged(good_result, json!({"ci_low": 0.9, "ci_high": 0.5})),
            ),
            ("result_error", json!(3)),
            ("result_error", json!("also set")),
            ("quotes_not_found", json!("x")),
            ("citations", json!({})),
            ("contradicted", json!("x")),
            ("quotes_dropped", json!(-1)),
            ("quotes_dropped", json!(true)),
        ] {
            let message = with(key, bad);
            assert!(message.contains(key), "{key}: {message}");
        }
        let mut absent = good.clone();
        absent.as_object_mut().expect("object").remove("result");
        assert!(ProposedOutcome::from_json(&absent)
            .expect_err("refused")
            .contains("result"));
        let bad_flag = json!({"outcomes": [], "condensed": "yes"});
        assert!(OutcomeProposal::from_json(&bad_flag)
            .expect_err("refused")
            .contains("condensed"));
        let bad_list = json!({"outcomes": {}, "condensed": false});
        assert!(OutcomeProposal::from_json(&bad_list)
            .expect_err("refused")
            .contains("outcomes"));
    }

    // --- The agent ---

    /// Answers each request with the next canned reply, repeating the last.
    struct Canned {
        replies: Vec<Result<Value, ChatJsonError>>,
        sent: Vec<String>,
        system: Vec<String>,
    }

    impl Canned {
        fn new(replies: Vec<Result<Value, ChatJsonError>>) -> Self {
            Canned {
                replies,
                sent: Vec::new(),
                system: Vec::new(),
            }
        }
        fn of(value: Value) -> Self {
            Canned::new(vec![Ok(value)])
        }
    }

    impl JsonChat for Canned {
        fn chat_json(
            &mut self,
            messages: &[LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<ChatJsonOutcome, ChatJsonError> {
            self.system.push(messages[0].content.clone());
            self.sent.push(messages[messages.len() - 1].content.clone());
            let index = (self.sent.len() - 1).min(self.replies.len() - 1);
            self.replies[index].clone().map(|value| ChatJsonOutcome {
                value,
                attempts: 1,
                repaired: false,
            })
        }
    }

    /// A condenser returning a fixed digest and recording its query.
    struct Scripted {
        digest: &'static str,
        calls: Vec<(String, String)>,
    }

    impl QueryCondenser for Scripted {
        fn condense(
            &mut self,
            query: &str,
            text: &str,
            _label: &str,
            _config: &ProcessingConfig,
        ) -> ProcessingResult {
            self.calls.push((query.to_string(), text.to_string()));
            let mut result = ProcessingResult::failed("unused");
            result.status = crate::context_processor::ProcessingStatus::Completed;
            result.final_result.content = self.digest.to_string();
            result
        }
    }

    #[test]
    fn a_reply_is_read_by_the_agent() {
        let mut chat = Canned::of(reply(vec![outcome(json!({}))]));
        let proposal = OutcomeAgent::new(&mut chat)
            .propose("A trial", TEXT, SIM)
            .expect("read");
        assert_eq!(proposal.outcomes.len(), 1);
        assert!(!proposal.condensed);
        assert_eq!(chat.system[0], OUTCOME_SYSTEM_PROMPT);
        assert!(chat.sent[0].starts_with(OUTCOME_TASK_PROMPT));
        assert!(chat.sent[0].contains(TEXT));
    }

    #[test]
    fn empty_text_and_bad_similarity_send_nothing() {
        let mut chat = Canned::of(reply(vec![]));
        assert_eq!(
            OutcomeAgent::new(&mut chat).propose("A trial", "  ", SIM),
            Err(ProposalError::EmptyInput)
        );
        assert_eq!(
            OutcomeAgent::new(&mut chat).propose("A trial", TEXT, 0.0),
            Err(ProposalError::InvalidSimilarity)
        );
        assert!(chat.sent.is_empty());
    }

    #[test]
    fn a_stop_set_before_the_call_sends_nothing() {
        let stop = AtomicBool::new(true);
        let mut chat = Canned::of(reply(vec![]));
        let result = OutcomeAgent::new(&mut chat)
            .with_stop(&stop)
            .propose("A trial", TEXT, SIM);
        assert_eq!(result, Err(ProposalError::Stopped));
        assert!(chat.sent.is_empty());
    }

    #[test]
    fn an_unreadable_reply_is_asked_twice_then_refused() {
        let bad = reply(vec![outcome(json!({"result": "risk ratio 0.7"}))]);
        let mut chat = Canned::of(bad);
        let result = OutcomeAgent::new(&mut chat).propose("A trial", TEXT, SIM);
        assert!(
            matches!(&result, Err(ProposalError::Unreadable(m)) if m.contains("result is not an object"))
        );
        assert_eq!(chat.sent.len(), 2);
    }

    #[test]
    fn a_failed_request_and_a_cut_off_reply_are_told_apart() {
        let down = ChatJsonError::Transport {
            message: "down".into(),
            attempts: 1,
            retryable: true,
            status: None,
        };
        let mut chat = Canned::new(vec![Err(down)]);
        assert!(matches!(
            OutcomeAgent::new(&mut chat).propose("A trial", TEXT, SIM),
            Err(ProposalError::RequestFailed(_))
        ));
        let cut = ChatJsonError::Truncated {
            stop_reason: "length".into(),
            budget: 10,
            attempts: 1,
        };
        let mut chat = Canned::new(vec![Err(cut)]);
        assert!(matches!(
            OutcomeAgent::new(&mut chat).propose("A trial", TEXT, SIM),
            Err(ProposalError::Unreadable(_))
        ));
    }

    #[test]
    fn an_oversized_paper_is_condensed_and_verified_against_the_original() {
        let text = format!("{TEXT} {}", "Filler sentence about nothing. ".repeat(20));
        let mut condenser = Scripted {
            digest: "A digest without the quote.",
            calls: Vec::new(),
        };
        let mut chat = Canned::of(reply(vec![outcome(json!({}))]));
        let proposal = OutcomeAgent::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(200))
            .propose("A trial", &text, SIM)
            .expect("read");
        assert!(proposal.condensed);
        assert_eq!(
            condenser.calls,
            vec![(OUTCOME_CONDENSE_QUERY.to_string(), text.trim().to_string())]
        );
        assert!(chat.sent[0].contains("A digest without the quote."));
        assert!(!chat.sent[0].contains("Filler sentence"));
        assert_eq!(proposal.outcomes[0].citations[0].text, QUOTE);
        assert_eq!(proposal.outcomes[0].citations[0].start, 0);
    }

    #[test]
    fn an_oversized_paper_without_a_condenser_fails_visibly() {
        let mut chat = Canned::of(reply(vec![]));
        let result = OutcomeAgent::new(&mut chat)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(20))
            .propose("A trial", TEXT, SIM);
        assert!(matches!(result, Err(ProposalError::CondensationFailed(_))));
        assert!(chat.sent.is_empty());
    }

    #[test]
    fn the_constants() {
        assert_eq!(OUTCOME_PROMPT_VERSION, 1);
        assert!(OUTCOME_SYSTEM_PROMPT.contains("primary") && !OUTCOME_CONDENSE_QUERY.is_empty());
    }
}
