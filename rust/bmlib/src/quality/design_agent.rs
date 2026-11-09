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

//! The design proposal: what kind of study this paper reports.
//!
//! A port of `bmlib/quality/design_agent.py`; the contract is
//! `tests/fixtures/design_replies.json`.

use std::fmt;
use std::sync::atomic::AtomicBool;

use serde_json::{json, Value};

use crate::context_processor::ProcessingConfig;
use crate::pyvalue::python_strip;
use crate::quality::agent_chat::{JsonChat, STOPPED_MESSAGE};
use crate::quality::cochrane_assessor::{
    DEFAULT_CONDENSE_THRESHOLD_CHARS, DEFAULT_MAX_RETRIES, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE,
};
use crate::quality::data_models::{study_design_from_str, RctLayout, StudyDesign};
use crate::quality::full_text_agent::{
    ask_until_readable, check_similarity, check_stop, condense_once, Failure, Sampling, LABEL_CHARS,
};
pub use crate::quality::full_text_agent::{LlmQueryCondenser, QueryCondenser};
use crate::quality::pico_agent::verify_quotes;
use crate::quality::pico_models::{PicoCitation, PicoContradictedQuote};

/// What a condensation's note calls the call it is for.
const CONDENSE_STAGE: &str = "the design proposal";

/// Bumped whenever the prompts below change, so a stored proposal says which
/// wording produced it. Python's `DESIGN_PROMPT_VERSION`.
pub const DESIGN_PROMPT_VERSION: u32 = 1;

/// The system turn. Byte-identical to Python's; the contract pins it.
pub const DESIGN_SYSTEM_PROMPT: &str = r##"You are an expert in clinical research methodology and evidence-based medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. Classify THIS study's own design, not the designs of studies it cites or reviews
4. Return ONLY valid JSON, no explanation"##;

/// The question, with the reply shape; the paper follows it. Byte-identical to
/// Python's; the contract pins it.
pub const DESIGN_TASK_PROMPT: &str = r##"Classify the study design of the research paper below.

Give the design of the study THIS paper reports. A paper that cites randomised trials, cohort studies or other designs is not itself one of them, unless it is a systematic review or meta-analysis of them.

study_design must be exactly one of:
- "systematic_review", "meta_analysis"
- "rct" (a randomised controlled trial)
- "cohort_prospective", "cohort_retrospective", "case_control", "cross_sectional"
- "case_series", "case_report"
- "guideline", "editorial", "letter", "comment"
- "other" (a design not listed)

rct_layout: required when study_design is "rct", and null for every other design. Exactly one of:
- "parallel": participants are randomised individually to parallel groups
- "cluster": groups of participants (clinics, schools, villages) are randomised
- "crossover": each participant receives the arms in turn
- "other": any other randomised layout

quotes: 1-3 passages copied VERBATIM from the paper that show the design (for example the sentence describing how participants were allocated). Copy them character for character; do not paraphrase, shorten with ellipses, or join sentences.

reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape:

{
    "study_design": "rct",
    "rct_layout": "parallel",
    "quotes": ["Participants were randomly assigned 1:1 to metformin or placebo."],
    "reasoning": "The authors randomised individual participants to two parallel arms."
}"##;

/// What the condensation gathers when the paper does not fit the window.
pub const DESIGN_CONDENSE_QUERY: &str = r##"Everything that shows the study's own design: how participants were recruited and allocated (randomisation method, unit of randomisation, crossover or cluster allocation), whether the study followed people forward or looked back, whether it compared groups, and how the authors themselves describe the study type."##;

// --- Errors ---

/// Why a proposal (this one's, or a later piece's) could not be made.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum ProposalError {
    /// The paper's text was empty; no request was made.
    EmptyInput,
    /// The paper did not fit and could not be condensed to fit.
    CondensationFailed(String),
    /// The provider failed or refused a request.
    RequestFailed(String),
    /// Every reply was unusable or cut off, or named something outside the
    /// allowed set.
    Unreadable(String),
    /// The caller's stop was set.
    Stopped,
    /// `min_similarity` was not a number in (0, 1]; no request was made.
    InvalidSimilarity,
}

impl fmt::Display for ProposalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ProposalError::EmptyInput => f.write_str("the paper's text is empty"),
            ProposalError::CondensationFailed(m)
            | ProposalError::RequestFailed(m)
            | ProposalError::Unreadable(m) => f.write_str(m),
            ProposalError::Stopped => f.write_str(STOPPED_MESSAGE),
            ProposalError::InvalidSimilarity => {
                f.write_str("min_similarity must be a number in (0, 1]")
            }
        }
    }
}

impl std::error::Error for ProposalError {}

impl From<Failure> for ProposalError {
    fn from(failure: Failure) -> Self {
        match failure {
            Failure::Stopped => ProposalError::Stopped,
            Failure::CondensationFailed(m) => ProposalError::CondensationFailed(m),
            Failure::RequestFailed(m) => ProposalError::RequestFailed(m),
            Failure::Unreadable(m) => ProposalError::Unreadable(m),
        }
    }
}

// --- Reading a reply ---

/// The model's reading of the paper's own design, backed by its passages.
#[derive(Debug, Clone, PartialEq)]
pub struct DesignProposal {
    /// The study design; never [`StudyDesign::Unknown`].
    pub design: StudyDesign,
    /// Set exactly when `design` is [`StudyDesign::Rct`].
    pub rct_layout: Option<RctLayout>,
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
    /// The model's explanation, `""` when absent.
    pub reasoning: String,
    /// Whether the model saw a digest instead of the whole paper.
    pub condensed: bool,
}

impl DesignProposal {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "design": self.design.as_str(),
            "rct_layout": self.rct_layout.map(RctLayout::as_str),
            "citations": self.citations.iter().map(PicoCitation::to_json).collect::<Vec<_>>(),
            "quotes_not_found": self.quotes_not_found,
            "contradicted": self.contradicted.iter().map(PicoContradictedQuote::to_json).collect::<Vec<_>>(),
            "quotes_dropped": self.quotes_dropped,
            "reasoning": self.reasoning,
            "condensed": self.condensed,
        })
    }

    /// Python's `from_dict()`.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent, of the wrong type or value.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let design_wire = field(value, "design")?
            .as_str()
            .ok_or("design is not text")?;
        let design = study_design_from_str(design_wire);
        if design.as_str() != design_wire {
            return Err(format!("design \"{design_wire}\" is not a design"));
        }
        let rct_layout = match field(value, "rct_layout")? {
            Value::Null => None,
            Value::String(wire) => Some(
                RctLayout::from_wire(wire)
                    .ok_or_else(|| format!("rct_layout \"{wire}\" is not a layout"))?,
            ),
            _ => return Err("rct_layout is not text".to_string()),
        };
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
        Ok(DesignProposal {
            design,
            rct_layout,
            citations,
            quotes_not_found,
            contradicted,
            quotes_dropped,
            reasoning: field(value, "reasoning")?
                .as_str()
                .ok_or("reasoning is not text")?
                .to_string(),
            condensed: field(value, "condensed")?
                .as_bool()
                .ok_or("condensed is not a boolean")?,
        })
    }
}

/// `value[key]`, or a message naming the absent key.
fn field<'v>(value: &'v Value, key: &str) -> Result<&'v Value, String> {
    value.get(key).ok_or_else(|| format!("{key} is missing"))
}

/// Read the model's reply and verify its quotes against `text`. Python's
/// `read_design_reply`.
///
/// # Errors
///
/// A message when `min_similarity` is outside (0, 1], the reply is not an
/// object, the design is not a label the study-design lookup knows (or is
/// `unknown`), the layout is missing for an RCT, given for another design or
/// not a layout, or `reasoning` is not text.
pub fn read_design_reply(
    data: &Value,
    text: &str,
    min_similarity: f64,
) -> Result<DesignProposal, String> {
    check_similarity(min_similarity)?;
    let Some(data) = data.as_object() else {
        return Err("expected an object".to_string());
    };

    let label = data
        .get("study_design")
        .and_then(Value::as_str)
        .ok_or("study_design is not text")?;
    let design = study_design_from_str(python_strip(&label.to_lowercase()));
    if design == StudyDesign::Unknown {
        return Err(format!("study_design \"{label}\" is not a design label"));
    }

    let raw_layout = data.get("rct_layout").filter(|v| !v.is_null());
    let rct_layout = match (design == StudyDesign::Rct, raw_layout) {
        (false, Some(_)) => return Err("rct_layout given for a non-RCT".to_string()),
        (false, None) => None,
        (true, None) => return Err("rct_layout missing".to_string()),
        (true, Some(raw)) => {
            let raw = raw.as_str().ok_or("rct_layout is not text")?;
            Some(
                RctLayout::from_wire(python_strip(&raw.to_lowercase()))
                    .ok_or_else(|| format!("rct_layout \"{raw}\" is not a layout"))?,
            )
        }
    };

    let reasoning = match data.get("reasoning") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(text)) => text.clone(),
        Some(_) => return Err("reasoning is not text".to_string()),
    };

    let (citations, quotes_not_found, contradicted, quotes_dropped) =
        verify_quotes(data.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
    Ok(DesignProposal {
        design,
        rct_layout,
        citations,
        quotes_not_found,
        contradicted,
        quotes_dropped,
        reasoning,
        condensed: false,
    })
}

// --- The agent ---

/// Proposes the paper's own study design, backed by passages of the paper.
///
/// Text over `condense_config.max_context_chars` is condensed once by the
/// configured [`QueryCondenser`] (see [`LlmQueryCondenser`]); with none
/// configured it is refused as [`ProposalError::CondensationFailed`], never
/// sent whole or cut. A stop is read before each request and the condensation.
pub struct DesignAgent<'a> {
    chat: &'a mut dyn JsonChat,
    condenser: Option<&'a mut dyn QueryCondenser>,
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    condense_config: ProcessingConfig,
    stop: Option<&'a AtomicBool>,
}

impl<'a> DesignAgent<'a> {
    /// An agent asking through `chat`, with the Cochrane assessor's defaults.
    #[must_use]
    pub fn new(chat: &'a mut dyn JsonChat) -> Self {
        DesignAgent {
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

    /// Propose the paper's own design.
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
    ) -> Result<DesignProposal, ProposalError> {
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
                DESIGN_CONDENSE_QUERY,
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

        let prompt = format!("{DESIGN_TASK_PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{sent}");
        let label = format!(
            "design proposal of {}",
            crate::pyvalue::repr_str(&title.chars().take(LABEL_CHARS).collect::<String>())
        );
        let sampling = Sampling {
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            max_retries: self.max_retries,
        };
        let mut proposal = ask_until_readable(
            &mut *self.chat,
            DESIGN_SYSTEM_PROMPT,
            &prompt,
            sampling,
            self.stop,
            &label,
            |data| read_design_reply(data, text, min_similarity),
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

    const TEXT: &str =
        "Participants were randomly assigned 1:1 to metformin or placebo. Follow-up was 12 weeks.";
    const QUOTE: &str = "Participants were randomly assigned 1:1 to metformin or placebo.";
    const SIM: f64 = 0.9;

    fn reply(overrides: Value) -> Value {
        let mut value = json!({
            "study_design": "rct",
            "rct_layout": "parallel",
            "quotes": [QUOTE],
            "reasoning": "Individual randomisation to two arms.",
        });
        for (key, item) in overrides.as_object().expect("an object") {
            value[key] = item.clone();
        }
        value
    }

    fn err_of(data: &Value) -> String {
        read_design_reply(data, TEXT, SIM).expect_err("a refusal")
    }

    #[test]
    fn a_parallel_rct_with_a_found_quote() {
        let proposal = read_design_reply(&reply(json!({})), TEXT, SIM).expect("read");
        assert_eq!(proposal.design, StudyDesign::Rct);
        assert_eq!(proposal.rct_layout, Some(RctLayout::Parallel));
        assert_eq!(proposal.citations.len(), 1);
        assert_eq!(proposal.citations[0].text, QUOTE);
        assert_eq!(proposal.citations[0].start, 0);
        assert_eq!(proposal.citations[0].end, QUOTE.chars().count());
        assert!(proposal.quotes_not_found.is_empty());
        assert!(!proposal.condensed);
    }

    #[test]
    fn layout_rules() {
        assert_eq!(
            err_of(&reply(json!({"rct_layout": null}))),
            "rct_layout missing"
        );
        let mut absent = reply(json!({}));
        absent.as_object_mut().expect("object").remove("rct_layout");
        assert_eq!(err_of(&absent), "rct_layout missing");
        assert_eq!(
            err_of(&reply(json!({"study_design": "cohort_prospective"}))),
            "rct_layout given for a non-RCT"
        );
        let cohort = reply(json!({"study_design": "cohort_prospective", "rct_layout": null}));
        assert_eq!(
            read_design_reply(&cohort, TEXT, SIM)
                .expect("read")
                .rct_layout,
            None
        );
        assert!(err_of(&reply(json!({"rct_layout": "banana"}))).starts_with("rct_layout"));
        assert_eq!(
            err_of(&reply(json!({"rct_layout": 1}))),
            "rct_layout is not text"
        );
        let folded = reply(json!({"rct_layout": " Cluster "}));
        assert_eq!(
            read_design_reply(&folded, TEXT, SIM)
                .expect("read")
                .rct_layout,
            Some(RctLayout::Cluster)
        );
    }

    #[test]
    fn designs_are_looked_up_tolerantly_and_never_unknown() {
        let alias = reply(json!({"study_design": "  Randomised Controlled Trial "}));
        assert_eq!(
            read_design_reply(&alias, TEXT, SIM).expect("read").design,
            StudyDesign::Rct
        );
        for bad in [
            json!("banana"),
            json!("unknown"),
            json!(""),
            json!(3),
            json!(null),
        ] {
            assert!(
                err_of(&reply(json!({"study_design": bad}))).starts_with("study_design"),
                "{bad}"
            );
        }
    }

    #[test]
    fn lost_contradicted_and_dropped_quotes_each_have_their_place() {
        let lost = "Patients were allocated by minimisation.";
        let changed = "Participants were randomly assigned 1:2 to metformin or placebo.";
        let quotes = json!({"quotes": [lost, changed, QUOTE, "  ", 7, null]});
        let proposal = read_design_reply(&reply(quotes), TEXT, SIM).expect("read");
        assert_eq!(proposal.citations.len(), 1);
        assert_eq!(proposal.quotes_not_found, vec![lost]);
        assert_eq!(
            proposal.contradicted,
            vec![PicoContradictedQuote {
                quote: changed.to_string(),
                contradiction: crate::citations::PassageContradiction::Number,
            }]
        );
        assert_eq!(proposal.quotes_dropped, 3);
        let none = read_design_reply(&reply(json!({"quotes": []})), TEXT, SIM).expect("read");
        assert!(none.citations.is_empty() && none.quotes_dropped == 0);
    }

    #[test]
    fn reasoning_and_shape_and_similarity() {
        let mut data = reply(json!({}));
        data.as_object_mut().expect("object").remove("reasoning");
        assert_eq!(
            read_design_reply(&data, TEXT, SIM).expect("read").reasoning,
            ""
        );
        assert_eq!(
            err_of(&reply(json!({"reasoning": 5}))),
            "reasoning is not text"
        );
        assert!(err_of(&json!(["rct"])).contains("object"));
        for bad in [0.0, 1.5, -1.0, f64::NAN] {
            let message = read_design_reply(&reply(json!({})), TEXT, bad).expect_err("refused");
            assert!(message.contains("min_similarity"), "{message}");
        }
    }

    #[test]
    fn the_proposal_round_trips() {
        let proposal = read_design_reply(
            &reply(json!({"quotes": [
                QUOTE,
                "lost words here",
                "Participants were randomly assigned 1:2 to metformin or placebo.",
                " "
            ]})),
            TEXT,
            SIM,
        )
        .expect("read");
        assert!(!proposal.contradicted.is_empty() && proposal.quotes_dropped == 1);
        assert_eq!(DesignProposal::from_json(&proposal.to_json()), Ok(proposal));
        let good = read_design_reply(&reply(json!({})), TEXT, SIM)
            .expect("read")
            .to_json();
        for (key, bad) in [
            ("design", json!("banana")),
            ("rct_layout", json!("banana")),
            ("condensed", json!("yes")),
            ("reasoning", json!(3)),
            ("quotes_not_found", json!("x")),
            ("citations", json!({})),
            ("contradicted", json!("x")),
            ("quotes_dropped", json!(-1)),
            ("quotes_dropped", json!(true)),
        ] {
            let mut value = good.clone();
            value[key] = bad;
            let message = DesignProposal::from_json(&value).expect_err("refused");
            assert!(message.contains(key), "{key}: {message}");
        }
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
        let mut chat = Canned::of(reply(json!({})));
        let proposal = DesignAgent::new(&mut chat)
            .propose("A trial", TEXT, SIM)
            .expect("read");
        assert_eq!(proposal.design, StudyDesign::Rct);
        assert!(!proposal.condensed);
        assert_eq!(chat.system[0], DESIGN_SYSTEM_PROMPT);
        assert!(chat.sent[0].starts_with(DESIGN_TASK_PROMPT));
        assert!(chat.sent[0].contains(TEXT));
    }

    #[test]
    fn empty_text_and_bad_similarity_send_nothing() {
        let mut chat = Canned::of(reply(json!({})));
        assert_eq!(
            DesignAgent::new(&mut chat).propose("A trial", "  ", SIM),
            Err(ProposalError::EmptyInput)
        );
        assert_eq!(
            DesignAgent::new(&mut chat).propose("A trial", TEXT, 0.0),
            Err(ProposalError::InvalidSimilarity)
        );
        assert!(chat.sent.is_empty());
    }

    #[test]
    fn a_stop_set_before_the_call_sends_nothing() {
        let stop = AtomicBool::new(true);
        let mut chat = Canned::of(reply(json!({})));
        let result = DesignAgent::new(&mut chat)
            .with_stop(&stop)
            .propose("A trial", TEXT, SIM);
        assert_eq!(result, Err(ProposalError::Stopped));
        assert!(chat.sent.is_empty());
    }

    #[test]
    fn an_unreadable_reply_is_asked_twice_then_refused() {
        let mut chat = Canned::of(reply(json!({"study_design": "banana"})));
        let result = DesignAgent::new(&mut chat).propose("A trial", TEXT, SIM);
        assert!(matches!(&result, Err(ProposalError::Unreadable(m)) if m.contains("study_design")));
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
            DesignAgent::new(&mut chat).propose("A trial", TEXT, SIM),
            Err(ProposalError::RequestFailed(_))
        ));
        let cut = ChatJsonError::Truncated {
            stop_reason: "length".into(),
            budget: 10,
            attempts: 1,
        };
        let mut chat = Canned::new(vec![Err(cut)]);
        assert!(matches!(
            DesignAgent::new(&mut chat).propose("A trial", TEXT, SIM),
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
        let mut chat = Canned::of(reply(json!({})));
        let proposal = DesignAgent::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(200))
            .propose("A trial", &text, SIM)
            .expect("read");
        assert!(proposal.condensed);
        assert_eq!(
            condenser.calls,
            vec![(DESIGN_CONDENSE_QUERY.to_string(), text.trim().to_string())]
        );
        assert!(chat.sent[0].contains("A digest without the quote."));
        assert!(!chat.sent[0].contains("Filler sentence"));
        assert_eq!(proposal.citations[0].text, QUOTE);
        assert_eq!(proposal.citations[0].start, 0);
    }

    #[test]
    fn an_oversized_paper_without_a_condenser_fails_visibly() {
        let mut chat = Canned::of(reply(json!({})));
        let result = DesignAgent::new(&mut chat)
            .with_condense_config(ProcessingConfig::default().with_max_context_chars(20))
            .propose("A trial", TEXT, SIM);
        assert!(matches!(result, Err(ProposalError::CondensationFailed(_))));
        assert!(chat.sent.is_empty());
    }

    #[test]
    fn the_layout_and_constants() {
        assert_eq!(DESIGN_PROMPT_VERSION, 1);
        assert!(RctLayout::Parallel.is_covered());
        assert!(!RctLayout::Cluster.is_covered());
        assert_eq!(
            RctLayout::from_wire("crossover"),
            Some(RctLayout::Crossover)
        );
        assert_eq!(RctLayout::from_wire("Parallel"), None);
    }
}
