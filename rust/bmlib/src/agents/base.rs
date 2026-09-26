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

//! Asking a model for JSON, with retries, and *knowing* when the answer was cut
//! off.
//!
//! # The defect this fixes
//!
//! `chat_json`'s truncation path tried to parse the response before declaring
//! truncation, on the assumption that a parseable response is a complete one. But
//! the parse runs the **repair** stage, which closes brackets and strings the
//! model never closed — so `parsed is not None` cannot tell a clean parse from a
//! fabricated one, and truncation was returned as success:
//!
//! | content | the Python returned |
//! |---|---|
//! | `{"n": 12` | `{"n": 12}` |
//! | `{"summary": "The study found that metformin` | `{"summary": "The study found that metformin"}` |
//! | `{"a": 1, "b": [1, 2` | `{"a": 1, "b": [1, 2]}` |
//!
//! The second row is the clearest: a value cut mid-sentence returned as a
//! complete string field. The first fabricates the number `12` from a stream
//! that stopped mid-token.
//!
//! The repair stage logged a warning — but the caller of `chat_json` never saw
//! it, and the branch returned before the truncation path could run. Filed as
//! [#300](https://github.com/hherb/bmlib/issues/300).
//!
//! **This port distinguishes the two.** [`JsonAttempt`] carries whether the parse
//! needed repair, and a truncated response that needed it is treated as
//! truncated. Only a response that parses **strictly** — no repair — is used
//! as-is, which is the case the Python's own test pinned (`{"ok": true}`) and the
//! only one the shortcut was ever right about.

use crate::llm::{safe_json_loads, LLMResponse};
use serde_json::Value;

/// Stop reasons that mean the model hit its output ceiling.
///
/// `max_tokens` is OpenAI-compatible servers; `length` is Anthropic's and
/// Ollama's word for the same thing.
pub const TRUNCATION_STOP_REASONS: &[&str] = &["max_tokens", "length"];

/// Why a response stopped, in the terms this module reasons about.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopReasonClass {
    /// It hit the output ceiling.
    Truncated,
    /// It finished, or gave no reason at all.
    Finished,
}

/// Classify a provider's stop reason.
///
/// An **absent** reason is `Finished`: a provider that reports none has not said
/// the output was cut off, and treating silence as truncation would refuse every
/// answer from a server that omits the field.
#[must_use]
pub fn classify_stop_reason(stop_reason: Option<&str>) -> StopReasonClass {
    match stop_reason {
        Some(reason) if TRUNCATION_STOP_REASONS.contains(&reason) => StopReasonClass::Truncated,
        _ => StopReasonClass::Finished,
    }
}

/// One attempt's parse, and whether it was the model's JSON or the repairer's.
#[derive(Debug, Clone, PartialEq)]
pub enum JsonAttempt {
    /// It parsed **strictly** — the bytes are the model's own. Usable as-is.
    Clean(Value),
    /// It parsed only after repair, so brackets or strings were fabricated.
    ///
    /// **Never usable from a truncated response**: the value may contain fields
    /// the model never emitted, and is at best incomplete.
    Repaired(Value),
    /// It did not parse even after repair.
    Unparseable,
}

impl JsonAttempt {
    /// Parse `content`, saying whether repair was needed.
    ///
    /// The distinction is `safe_json_loads(content, false, _)`: it succeeds only
    /// on a strict parse, because with repair disabled it returns the decoder's
    /// error rather than mending anything.
    #[must_use]
    pub fn parse(content: &str, max_attempts: usize) -> Self {
        if let Ok(value) = safe_json_loads(content, false, max_attempts) {
            return JsonAttempt::Clean(value);
        }
        match safe_json_loads(content, true, max_attempts) {
            Ok(value) => JsonAttempt::Repaired(value),
            Err(_) => JsonAttempt::Unparseable,
        }
    }

    /// The value, whatever its provenance.
    #[must_use]
    pub fn value(&self) -> Option<&Value> {
        match self {
            JsonAttempt::Clean(value) | JsonAttempt::Repaired(value) => Some(value),
            JsonAttempt::Unparseable => None,
        }
    }
}

/// What a successful `chat_json` produced.
#[derive(Debug, Clone, PartialEq)]
pub struct ChatJsonOutcome {
    /// The parsed value.
    pub value: Value,
    /// The attempts it took, counting the first as one.
    ///
    /// Carried so a caller can tell a clean answer from one that needed three
    /// tries — the same distinction the retry log lines made, in the return
    /// value rather than in a log.
    pub attempts: usize,
    /// Whether the **last** attempt's parse needed repair.
    ///
    /// Always `false` on a truncated response, since repair there is refused —
    /// so a caller who wants to know "was this answer whole" reads this and does
    /// not have to re-derive it.
    pub repaired: bool,
}

/// Why a `chat_json` gave up.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChatJsonError {
    /// The model hit its output ceiling.
    ///
    /// Reports the stop reason **as the provider worded it**, not the canonical
    /// `max_tokens`: an operator grepping logs knows what their server says.
    Truncated {
        /// The provider's own word.
        stop_reason: String,
        /// The ceiling that was hit.
        budget: i64,
        /// The attempts made.
        attempts: usize,
    },
    /// The answer parsed, but was not the shape asked for.
    WrongShape {
        /// What arrived, named as the caller would: `"array"`, `"string"`, …
        got: String,
        /// The attempts made.
        attempts: usize,
    },
    /// The model returned nothing.
    Empty {
        /// The attempts made.
        attempts: usize,
    },
    /// Every attempt's answer failed to parse.
    Unparseable {
        /// The attempts made.
        attempts: usize,
    },
    /// The request itself failed.
    ///
    /// **Its own variant** rather than folded into `Empty`: the four diagnoses
    /// above are about an *answer*, and this one is about no answer arriving.
    /// Reporting a refused connection as "empty response from model" names the
    /// wrong cause and points the operator at the model.
    Transport {
        /// The transport's own message.
        message: String,
        /// The attempts made.
        attempts: usize,
    },
}

impl std::fmt::Display for ChatJsonError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ChatJsonError::Truncated {
                stop_reason,
                budget,
                attempts,
            } => write!(
                f,
                "response truncated at max_tokens={budget} (stop_reason={stop_reason:?}) \
                 after {attempts} attempt(s) — raise max_tokens or request less output"
            ),
            ChatJsonError::WrongShape { got, attempts } => write!(
                f,
                "expected a JSON object, got {got} after {attempts} attempt(s)"
            ),
            ChatJsonError::Empty { attempts } => {
                write!(
                    f,
                    "failed after {attempts} attempt(s): empty response from model"
                )
            }
            ChatJsonError::Unparseable { attempts } => write!(
                f,
                "failed after {attempts} attempt(s): unparseable response"
            ),
            ChatJsonError::Transport { message, attempts } => {
                write!(f, "failed after {attempts} attempt(s): {message}")
            }
        }
    }
}

impl std::error::Error for ChatJsonError {}

/// The JSON type name a caller would use, matching Python's `type(x).__name__`.
#[must_use]
pub fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_i64() || n.is_u64() => "int",
        Value::Number(_) => "float",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// How a `chat_json` call gets its responses.
///
/// A trait rather than a client, so the loop is testable without a model — the
/// retry and truncation rules are the subject, and a real call would make them
/// both slow and unrepeatable.
pub trait ChatSource {
    /// One call.
    ///
    /// # Errors
    ///
    /// The transport's own failure, surfaced rather than retried: a retry is for
    /// a bad *answer*, and repeating a request that never arrived is the
    /// transport's business.
    fn chat(&mut self, attempt: usize) -> Result<LLMResponse, String>;

    /// Sleep before a retry.
    ///
    /// Injected so a test does not spend the backoff: the schedule is 1s, 2s,
    /// 4s …, which is three seconds for a three-attempt failure.
    fn backoff(&mut self, attempt: usize);
}

/// The retry-and-classify loop.
///
/// Each attempt is classified, and the classification decides whether a retry
/// could help:
///
/// * **Truncated** — the model hit the ceiling. At `temperature == 0.0` greedy
///   sampling reproduces the identical truncation, so retrying pays for it again
///   and the call fails at once; above zero a retry may sample a shorter
///   completion that fits, so it is retried.
/// * **Wrong shape under `require_dict`** — the same argument: the same messages
///   at temperature 0 return the same array.
/// * **Empty** or **unparseable** — always retried: both can be sampling noise.
///
/// # Errors
///
/// [`ChatJsonError`] naming the real cause and the attempt count.
pub fn chat_json(
    source: &mut dyn ChatSource,
    max_retries: usize,
    temperature: f64,
    max_tokens: Option<i64>,
    default_max_tokens: i64,
    require_dict: bool,
) -> Result<ChatJsonOutcome, ChatJsonError> {
    let attempts_allowed = max_retries.max(1);
    let mut attempts = 0usize;
    let mut last: Option<ChatJsonError> = None;

    for attempt in 0..attempts_allowed {
        if attempt > 0 {
            source.backoff(attempt);
        }
        attempts = attempt + 1;

        let response = match source.chat(attempt) {
            Ok(response) => response,
            Err(message) => {
                return Err(ChatJsonError::Transport { message, attempts });
            }
        };

        let content = response.content.trim();
        let budget = max_tokens.unwrap_or(default_max_tokens);

        if classify_stop_reason(response.stop_reason.as_deref()) == StopReasonClass::Truncated {
            match JsonAttempt::parse(content, 1) {
                // **Strictly parseable**: the JSON is complete despite hitting
                // the ceiling, and it is the model's own bytes. Usable as-is.
                JsonAttempt::Clean(parsed) => {
                    if !require_dict || parsed.is_object() {
                        return Ok(ChatJsonOutcome {
                            value: parsed,
                            attempts,
                            repaired: false,
                        });
                    }
                    let error = ChatJsonError::WrongShape {
                        got: json_type_name(&parsed).to_string(),
                        attempts,
                    };
                    if temperature == 0.0 {
                        return Err(error);
                    }
                    last = Some(error);
                }
                // **Repaired**: the brackets or strings this value has were
                // fabricated by the repairer, so the response is truncated and
                // says so.
                //
                // The Python used this value, which is #300: a value cut
                // mid-sentence came back as a complete string field, and a
                // number was fabricated from a stream that stopped mid-token.
                // The repairer's own warning said as much and the caller never
                // saw it.
                JsonAttempt::Repaired(_) | JsonAttempt::Unparseable => {
                    let error = ChatJsonError::Truncated {
                        // The **provider's** word, not the canonical one.
                        stop_reason: response.stop_reason.clone().unwrap_or_default(),
                        budget,
                        attempts,
                    };
                    if temperature == 0.0 {
                        return Err(error);
                    }
                    last = Some(error);
                }
            }
            continue;
        }

        if content.is_empty() {
            last = Some(ChatJsonError::Empty { attempts });
            continue;
        }

        match JsonAttempt::parse(content, 1) {
            JsonAttempt::Unparseable => {
                last = Some(ChatJsonError::Unparseable { attempts });
            }
            JsonAttempt::Clean(parsed) => {
                if require_dict && !parsed.is_object() {
                    let error = ChatJsonError::WrongShape {
                        got: json_type_name(&parsed).to_string(),
                        attempts,
                    };
                    if temperature == 0.0 {
                        return Err(error);
                    }
                    last = Some(error);
                    continue;
                }
                return Ok(ChatJsonOutcome {
                    value: parsed,
                    attempts,
                    repaired: false,
                });
            }
            JsonAttempt::Repaired(parsed) => {
                // A response that stopped **normally** but needed repair is a
                // different case from a truncated one: the model finished, and
                // its JSON was simply malformed. Repair is what the retry
                // machinery is for, and the result is usable — but it is
                // reported as repaired, so a caller is not told a mended
                // document was whole.
                if require_dict && !parsed.is_object() {
                    let error = ChatJsonError::WrongShape {
                        got: json_type_name(&parsed).to_string(),
                        attempts,
                    };
                    if temperature == 0.0 {
                        return Err(error);
                    }
                    last = Some(error);
                    continue;
                }
                return Ok(ChatJsonOutcome {
                    value: parsed,
                    attempts,
                    repaired: true,
                });
            }
        }
    }

    Err(last.unwrap_or(ChatJsonError::Unparseable { attempts }))
}
