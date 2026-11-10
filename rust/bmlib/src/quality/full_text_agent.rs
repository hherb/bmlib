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

//! What the agents that ask one question over a paper's full text share.
//!
//! The design proposal, the primary-outcome extraction and the RoB 2 agent
//! each need the same plumbing: refuse a bad window up front, condense a paper
//! that does not fit once for a query of their own, ask until the reply reads,
//! and send nothing once the caller's stop is set. It lives here once, as
//! plain functions over the agent's pieces; each agent maps [`Failure`] onto
//! its own error type. Python's `quality/_full_text_agent.py`.
//! [`PicoAgent`](crate::quality::pico_agent::PicoAgent) predates this and keeps
//! its own copy of the same rules.

use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::Value;

use crate::agents::ChatJsonError;
use crate::context_processor::llm_processor::{ContextModel, PromptTemplates};
use crate::context_processor::{ProcessingConfig, ProcessingResult};
use crate::llm::LLMMessage;
use crate::quality::agent_chat::JsonChat;
use crate::quality::cochrane_assessor::{condense_checked, Condenser, LlmCondenser};
use crate::quality::pico_agent::{
    PICO_CONDENSE_CONSOLIDATION_PROMPT, PICO_CONDENSE_EXTRACTION_PROMPT, READ_ATTEMPTS,
};

/// How much of the title names the paper in a log line or a condensation label.
pub const LABEL_CHARS: usize = 60;

/// The reply's output ceiling for every full-text agent (design, outcome, RoB 2
/// and the unattended pipeline). Python's `_full_text_agent.DEFAULT_MAX_TOKENS`.
///
/// A reasoning model's thinking counts against `max_tokens`: one RoB 2 domain
/// took 8,651 output tokens, mostly thinking, so 4096 or even 8192 cut replies
/// short. It is a ceiling, not a cost: a model stops when its reply is done. A
/// caller whose model allows less clips it to `LlmClient::model_limits`.
/// [`PicoAgent`](crate::quality::pico_agent::PicoAgent) keeps the Cochrane
/// assessor's 4096.
pub const DEFAULT_MAX_TOKENS: i64 = 16384;

/// Condenses an oversized text for one stated need.
///
/// [`Condenser`] with the query a parameter, so one condenser serves agents
/// that gather different evidence. The *rules* applied to its result are
/// `condense_checked`'s, as for every condenser.
pub trait QueryCondenser {
    /// Condense `text` (the paper named by `label`) for `query`, to the budget
    /// in `config`.
    fn condense(
        &mut self,
        query: &str,
        text: &str,
        label: &str,
        config: &ProcessingConfig,
    ) -> ProcessingResult;
}

/// The production [`QueryCondenser`]: [`LlmCondenser`] over the PICO
/// verbatim-passage prompts, built afresh for each call's query.
pub struct LlmQueryCondenser<'a> {
    model: &'a dyn ContextModel,
    temperature: f64,
    max_tokens: i64,
}

impl<'a> LlmQueryCondenser<'a> {
    /// Condense through `model`, sampling as the agent this serves would.
    #[must_use]
    pub fn new(model: &'a dyn ContextModel, temperature: f64, max_tokens: i64) -> Self {
        LlmQueryCondenser {
            model,
            temperature,
            max_tokens,
        }
    }
}

impl QueryCondenser for LlmQueryCondenser<'_> {
    fn condense(
        &mut self,
        query: &str,
        text: &str,
        label: &str,
        config: &ProcessingConfig,
    ) -> ProcessingResult {
        let templates = PromptTemplates {
            extraction_prompt: PICO_CONDENSE_EXTRACTION_PROMPT.to_string(),
            consolidation_prompt: PICO_CONDENSE_CONSOLIDATION_PROMPT.to_string(),
        };
        match LlmCondenser::with_prompts(
            self.model,
            templates,
            query,
            self.temperature,
            self.max_tokens,
        ) {
            Ok(mut condenser) => condenser.condense(text, label, config),
            Err(error) => ProcessingResult::failed(error),
        }
    }
}

/// A [`QueryCondenser`] pinned to one query, as the plain [`Condenser`]
/// `condense_checked` takes.
struct Bound<'c> {
    inner: &'c mut dyn QueryCondenser,
    query: &'c str,
}

impl Condenser for Bound<'_> {
    fn condense(&mut self, text: &str, label: &str, config: &ProcessingConfig) -> ProcessingResult {
        self.inner.condense(self.query, text, label, config)
    }
}

// --- The shared steps ---

/// Why a shared step failed, before an agent names it in its own error type.
#[derive(Debug, Clone, PartialEq)]
pub enum Failure {
    /// The caller's stop was set.
    Stopped,
    /// The paper did not fit and could not be condensed to fit.
    CondensationFailed(String),
    /// The provider failed or refused a request.
    RequestFailed {
        /// What failed, in the transport's or the server's words.
        message: String,
        /// Whether sending the request again may succeed
        /// ([`crate::llm::ChatError::is_retryable`]); a 401 is `false`.
        retryable: bool,
        /// The HTTP status the server answered with (401 or 403 is a refused
        /// key); `None` where no HTTP answer came.
        status: Option<u16>,
    },
    /// Every reply was unusable or cut off, or named something outside the
    /// allowed set.
    Unreadable(String),
}

/// How an agent samples: what `chat_json` is asked for.
#[derive(Debug, Clone, Copy)]
pub struct Sampling {
    /// Sampling temperature.
    pub temperature: f64,
    /// The reply's output ceiling.
    pub max_tokens: i64,
    /// `chat_json`'s attempts per question.
    pub max_retries: usize,
}

/// Whether an optional stop is set.
#[must_use]
pub fn stopped(stop: Option<&AtomicBool>) -> bool {
    stop.is_some_and(|stop| stop.load(Ordering::SeqCst))
}

/// [`Failure::Stopped`] once the stop is set.
///
/// # Errors
///
/// [`Failure::Stopped`].
pub fn check_stop(stop: Option<&AtomicBool>) -> Result<(), Failure> {
    if stopped(stop) {
        Err(Failure::Stopped)
    } else {
        Ok(())
    }
}

/// Refuse a `min_similarity` outside (0, 1]; NaN fails both comparisons.
///
/// # Errors
///
/// A message naming `min_similarity` and the value.
pub fn check_similarity(min_similarity: f64) -> Result<(), String> {
    if min_similarity > 0.0 && min_similarity <= 1.0 {
        Ok(())
    } else {
        Err(format!(
            "min_similarity must be a number in (0, 1], got {min_similarity}"
        ))
    }
}

/// Condense `text` once for `query` and return the digest.
///
/// Refused rather than sent whole or cut: no condenser, a failed run, an empty
/// or oversized digest (`condense_checked`'s rules), and a stop set while it
/// ran (its digest may be partial).
///
/// # Errors
///
/// [`Failure::Stopped`], [`Failure::RequestFailed`] when a request failed the
/// map-reduce, or [`Failure::CondensationFailed`].
pub fn condense_once(
    condenser: Option<&mut dyn QueryCondenser>,
    query: &str,
    title: &str,
    text: &str,
    config: &ProcessingConfig,
    stage: &str,
    stop: Option<&AtomicBool>,
) -> Result<String, Failure> {
    let Some(condenser) = condenser else {
        return Err(Failure::CondensationFailed(
            "no condenser configured".to_string(),
        ));
    };
    let label: String = title.chars().take(LABEL_CHARS).collect();
    let mut bound = Bound {
        inner: condenser,
        query,
    };
    let condensed = condense_checked(&mut bound, text, &label, config, stage);
    check_stop(stop)?;
    let (digest, _notes, _status) = condensed.map_err(|refusal| {
        let message = format!(
            "a {}-character text could not be condensed to fit {} characters: {}",
            text.chars().count(),
            config.max_context_chars,
            refusal.reason
        );
        match refusal.request {
            Some(request) => Failure::RequestFailed {
                message,
                retryable: request.retryable,
                status: request.status,
            },
            None => Failure::CondensationFailed(message),
        }
    })?;
    Ok(digest)
}

/// Send the question and read the reply, asking again once if `read` refuses it.
///
/// `read` returns a message for a reply it cannot use. The stop is read before
/// each request, and wins over whatever a request failed with.
///
/// # Errors
///
/// [`Failure::Stopped`]; [`Failure::RequestFailed`] for a transport failure;
/// [`Failure::Unreadable`] for a cut-off or unparseable reply, or when every
/// reply was refused by `read` (the last message is carried).
pub fn ask_until_readable<T>(
    chat: &mut dyn JsonChat,
    system_prompt: &str,
    prompt: &str,
    sampling: Sampling,
    stop: Option<&AtomicBool>,
    label: &str,
    read: impl Fn(&Value) -> Result<T, String>,
) -> Result<T, Failure> {
    let messages = vec![LLMMessage::system(system_prompt), LLMMessage::user(prompt)];
    let mut problem = String::new();
    for _attempt in 0..READ_ATTEMPTS {
        check_stop(stop)?;
        let data = match chat.chat_json(
            &messages,
            sampling.temperature,
            sampling.max_tokens,
            sampling.max_retries,
            true,
        ) {
            Ok(outcome) => outcome.value,
            Err(_) if stopped(stop) => return Err(Failure::Stopped),
            Err(
                error @ ChatJsonError::Transport {
                    retryable, status, ..
                },
            ) => {
                return Err(Failure::RequestFailed {
                    message: error.to_string(),
                    retryable,
                    status,
                })
            }
            Err(error) => return Err(Failure::Unreadable(error.to_string())),
        };
        match read(&data) {
            Ok(result) => return Ok(result),
            Err(message) => problem = message,
        }
    }
    Err(Failure::Unreadable(format!(
        "{label}: {READ_ATTEMPTS} replies could not be read: {problem}"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::ChatJsonOutcome;
    use serde_json::json;

    /// Answers each request with the next canned reply, repeating the last.
    struct Canned(Vec<Result<Value, ChatJsonError>>, usize);

    impl JsonChat for Canned {
        fn chat_json(
            &mut self,
            _messages: &[LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<ChatJsonOutcome, ChatJsonError> {
            let index = self.1.min(self.0.len() - 1);
            self.1 += 1;
            self.0[index].clone().map(|value| ChatJsonOutcome {
                value,
                attempts: 1,
                repaired: false,
            })
        }
    }

    const SAMPLING: Sampling = Sampling {
        temperature: 0.1,
        max_tokens: 100,
        max_retries: 1,
    };

    fn ask(chat: &mut Canned, stop: Option<&AtomicBool>) -> Result<i64, Failure> {
        ask_until_readable(chat, "sys", "q", SAMPLING, stop, "label", |v| {
            v["n"]
                .as_i64()
                .ok_or_else(|| "n is not a number".to_string())
        })
    }

    #[test]
    fn a_readable_reply_is_returned_and_an_unreadable_one_is_asked_again() {
        let mut chat = Canned(vec![Ok(json!({})), Ok(json!({"n": 4}))], 0);
        assert_eq!(ask(&mut chat, None), Ok(4));
        assert_eq!(chat.1, 2);
        let mut chat = Canned(vec![Ok(json!({}))], 0);
        let Err(Failure::Unreadable(message)) = ask(&mut chat, None) else {
            panic!("refused")
        };
        assert!(message.contains("n is not a number") && message.starts_with("label:"));
        assert_eq!(chat.1, READ_ATTEMPTS);
    }

    #[test]
    fn failures_are_told_apart_and_a_stop_wins() {
        let down = ChatJsonError::Transport {
            message: "down".into(),
            attempts: 1,
            retryable: true,
            status: None,
        };
        let mut chat = Canned(vec![Err(down.clone())], 0);
        assert!(matches!(
            ask(&mut chat, None),
            Err(Failure::RequestFailed {
                retryable: true,
                status: None,
                ..
            })
        ));
        for (status, retryable) in [(401, false), (503, true)] {
            let answered = ChatJsonError::Transport {
                message: format!("HTTP {status}"),
                attempts: 1,
                retryable,
                status: Some(status),
            };
            let mut chat = Canned(vec![Err(answered)], 0);
            assert!(matches!(
                ask(&mut chat, None),
                Err(Failure::RequestFailed { retryable: r, status: Some(s), .. })
                    if r == retryable && s == status
            ));
        }
        let cut = ChatJsonError::Truncated {
            stop_reason: "length".into(),
            budget: 1,
            attempts: 1,
        };
        let mut chat = Canned(vec![Err(cut)], 0);
        assert!(matches!(ask(&mut chat, None), Err(Failure::Unreadable(_))));

        let stop = AtomicBool::new(true);
        let mut chat = Canned(vec![Ok(json!({"n": 1}))], 0);
        assert_eq!(ask(&mut chat, Some(&stop)), Err(Failure::Stopped));
        assert_eq!(chat.1, 0);
    }

    #[test]
    fn a_request_that_failed_the_condensation_carries_its_status() {
        use crate::context_processor::llm_processor::{ModelCallError, RequestFailure};

        struct Refused(RequestFailure);
        impl QueryCondenser for Refused {
            fn condense(
                &mut self,
                _q: &str,
                _t: &str,
                _l: &str,
                _c: &ProcessingConfig,
            ) -> ProcessingResult {
                let mut result = ProcessingResult::failed("boom");
                result.error = Some(std::sync::Arc::new(ModelCallError {
                    message: "boom".to_string(),
                    request: self.0,
                }));
                result
            }
        }
        let config = ProcessingConfig::default().with_max_context_chars(10);
        for (retryable, status) in [(false, Some(401)), (true, Some(503)), (true, None)] {
            let mut refused = Refused(RequestFailure { retryable, status });
            let failure = condense_once(Some(&mut refused), "q", "t", "text", &config, "s", None);
            assert!(matches!(
                failure,
                Err(Failure::RequestFailed { retryable: r, status: s, .. })
                    if r == retryable && s == status
            ));
        }
    }

    #[test]
    fn similarity_and_stop_helpers() {
        assert!(check_similarity(0.5).is_ok() && check_similarity(1.0).is_ok());
        for bad in [0.0, 1.1, -1.0, f64::NAN] {
            assert!(check_similarity(bad).is_err(), "{bad}");
        }
        assert!(!stopped(None));
        assert_eq!(
            check_stop(Some(&AtomicBool::new(true))),
            Err(Failure::Stopped)
        );
    }

    #[test]
    fn condensing_without_a_condenser_fails_and_a_stop_wins() {
        let config = ProcessingConfig::default().with_max_context_chars(10);
        let none = condense_once(None, "q", "t", &"x".repeat(50), &config, "s", None);
        assert!(matches!(none, Err(Failure::CondensationFailed(_))));

        struct Fixed;
        impl QueryCondenser for Fixed {
            fn condense(
                &mut self,
                _q: &str,
                _t: &str,
                _l: &str,
                _c: &ProcessingConfig,
            ) -> ProcessingResult {
                let mut result = ProcessingResult::failed("unused");
                result.status = crate::context_processor::ProcessingStatus::Completed;
                result.final_result.content = "digest".to_string();
                result
            }
        }
        let ok = condense_once(Some(&mut Fixed), "q", "t", "text", &config, "s", None);
        assert_eq!(ok.as_deref(), Ok("digest"));
        let stop = AtomicBool::new(true);
        let stopped = condense_once(
            Some(&mut Fixed),
            "q",
            "t",
            "text",
            &config,
            "s",
            Some(&stop),
        );
        assert_eq!(stopped, Err(Failure::Stopped));
    }
}
