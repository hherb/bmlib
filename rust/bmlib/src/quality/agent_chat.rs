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

//! The seam between the three LLM quality tiers and a model.
//!
//! Each Python tier is a `BaseAgent` subclass holding an `LLMClient`, and its
//! `classify`/`assess` method calls `self.chat_json(...)`. The port does not
//! carry the client: a tier takes a [`JsonChat`], exactly as
//! [`crate::agents::ChatSource`] abstracts the retry loop's transport, so a test
//! drives a tier with a scripted source and no network.
//!
//! # Why not `ChatSource` itself
//!
//! [`crate::agents::ChatSource::chat`] takes an attempt number and nothing else:
//! it is the *bound* half of the loop, for a source that already knows what to
//! send. A tier composes its messages per call — the title and abstract are the
//! arguments — so the seam has to carry them. [`JsonChat`] is that seam, and it
//! delegates the retry, truncation and shape rules to
//! [`crate::agents::chat_json`] rather than restating any of them.
//!
//! # Why the sampling parameters are arguments
//!
//! The Python's sampling defaults live on the agent, not the client —
//! `StudyClassifier`'s docstring says so outright — and `chat_json` reads them
//! off `self`. So the tier passes them through on every call, and a tier's
//! constructor is where they are set.
//!
//! # The window and the stop
//!
//! [`LlmChat`] and [`LlmContextModel`] (the condenser's model) carry Ollama's
//! `num_ctx` (#640) and a stop read before every request (#642). A set stop
//! refuses the request with [`STOPPED_MESSAGE`]; the agent turns that, and
//! any other error while the stop is set, into its own "stopped" error, since
//! [`ChatJsonError`] and a [`ContextModel`]'s `String` carry no stop.

use std::num::NonZeroU32;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, PoisonError};

use serde_json::Value;

use crate::agents::{
    chat_json, ChatJsonError, ChatJsonOutcome, ChatSource, TRUNCATION_STOP_REASONS,
};
use crate::context_processor::llm_processor::{ContextModel, RequestFailure};
use crate::llm::{ChatError, ChatRequest, LLMMessage, LLMResponse, LlmClient};

/// What a request refused by a set stop reports.
pub const STOPPED_MESSAGE: &str = "stopped before the next request was sent";

/// Tries per [`LlmContextModel`] call: one and two retries, as many as
/// Python's Anthropic and OpenAI SDKs make by default and as Python's
/// `LLMChunkProcessor` makes (`CONTEXT_MODEL_ATTEMPTS`). [`LlmClient::chat`]
/// retries nothing (#561), so without this one failed request in a long
/// map-reduce drops its batch's passages, or fails the condensation when no
/// batch succeeds (#641).
pub const CONTEXT_MODEL_ATTEMPTS: u32 = 3;

/// `chat_json`'s attempts per structured [`LlmContextModel`] call, Python's
/// `BaseAgent.chat_json` default, which its `_extract_structured` leaves.
pub const CONTEXT_MODEL_JSON_RETRIES: usize = 3;

/// Asking a model for a JSON answer, with the messages under the caller's control.
///
/// The four diagnoses [`ChatJsonError`] carries — truncated, wrong shape, empty,
/// unparseable, and no answer at all — are the ones a tier degrades on, so a
/// scripted implementation can exercise every degradation a real transport can
/// produce.
pub trait JsonChat {
    /// Send `messages` and read the answer as JSON.
    ///
    /// # Errors
    ///
    /// [`ChatJsonError`] naming why the model's answer was unusable, or that the
    /// request itself failed.
    fn chat_json(
        &mut self,
        messages: &[LLMMessage],
        temperature: f64,
        max_tokens: i64,
        max_retries: usize,
        require_dict: bool,
    ) -> Result<ChatJsonOutcome, ChatJsonError>;
}

/// A [`JsonChat`] over a real [`LlmClient`].
///
/// The one production implementation. It builds the [`ChatRequest`] the Python's
/// `BaseAgent.chat` builds — `json_mode` on, because `chat_json` always asks for
/// a JSON object — and hands it to
/// [`crate::agents::chat_json`] through a [`ChatSource`], so the retry,
/// truncation and shape rules are the library's and not a second copy.
///
/// `#[non_exhaustive]`: built with [`LlmChat::new`] and the `with_` methods,
/// so a later field breaks no caller.
#[non_exhaustive]
pub struct LlmChat<'a> {
    /// The client the request is sent through.
    pub client: &'a LlmClient,
    /// The `"provider:model_name"` string, as the Python's agent holds it.
    pub model: String,
    /// The window Ollama loads the model with, in tokens; `None` sends none,
    /// leaving Ollama's own, often much smaller, default. Other providers
    /// ignore it.
    pub num_ctx: Option<NonZeroU32>,
    /// Read before every request, `chat_json`'s re-asks included: once set,
    /// nothing more is sent and the call fails with [`STOPPED_MESSAGE`].
    pub stop: Option<&'a AtomicBool>,
}

impl<'a> LlmChat<'a> {
    /// A chat seam over `client`, sending to `model`, with no window and no stop.
    #[must_use]
    pub fn new(client: &'a LlmClient, model: impl Into<String>) -> Self {
        LlmChat {
            client,
            model: model.into(),
            num_ctx: None,
            stop: None,
        }
    }

    /// Send `num_ctx` with every request (`None` sends none).
    #[must_use]
    pub fn with_num_ctx(mut self, num_ctx: Option<NonZeroU32>) -> Self {
        self.num_ctx = num_ctx;
        self
    }

    /// Refuse every request once `stop` is set.
    #[must_use]
    pub fn with_stop(mut self, stop: &'a AtomicBool) -> Self {
        self.stop = Some(stop);
        self
    }
}

impl JsonChat for LlmChat<'_> {
    fn chat_json(
        &mut self,
        messages: &[LLMMessage],
        temperature: f64,
        max_tokens: i64,
        max_retries: usize,
        require_dict: bool,
    ) -> Result<ChatJsonOutcome, ChatJsonError> {
        let request = ChatRequest {
            messages: messages.to_vec(),
            model: Some(self.model.clone()),
            temperature,
            max_tokens,
            // Python's `chat_json` calls `self.chat(..., json_mode=True)`.
            json_mode: true,
            num_ctx: self.num_ctx,
            ..ChatRequest::default()
        };
        let mut source = ClientSource {
            client: self.client,
            request,
            stop: self.stop,
            failure: None,
        };
        chat_json(
            &mut source,
            max_retries,
            temperature,
            Some(max_tokens),
            max_tokens,
            require_dict,
        )
    }
}

/// A [`ChatSource`] over one already-built request.
///
/// The request is immutable, so every attempt sends the same bytes — which is
/// what makes "at temperature 0 a retry is provably futile" a statement about
/// the request rather than about the transport.
struct ClientSource<'a> {
    client: &'a LlmClient,
    request: ChatRequest,
    stop: Option<&'a AtomicBool>,
    /// The last attempt's failure: [`ChatSource::chat`]'s `String` loses its
    /// kind, so [`ChatSource::last_failure_retryable`] (#659) and
    /// [`ChatSource::last_failure_status`] (#662) read it here.
    failure: Option<ChatError>,
}

impl ChatSource for ClientSource<'_> {
    fn chat(&mut self, _attempt: usize) -> Result<LLMResponse, String> {
        if is_set(self.stop) {
            return Err(STOPPED_MESSAGE.to_string());
        }
        self.client.chat(&self.request).map_err(|error| {
            let message = error.to_string();
            self.failure = Some(error);
            message
        })
    }

    fn last_failure_retryable(&self) -> bool {
        self.failure.as_ref().is_some_and(ChatError::is_retryable)
    }

    fn last_failure_status(&self) -> Option<u16> {
        self.failure.as_ref().and_then(ChatError::http_status)
    }

    fn backoff(&mut self, attempt: usize) {
        // The next attempt is refused anyway; waiting for it gains nothing.
        if is_set(self.stop) {
            return;
        }
        // Python's `2 ** (attempt - 1)`: 1s, 2s, 4s …
        let seconds = 1u64 << attempt.saturating_sub(1).min(63);
        std::thread::sleep(std::time::Duration::from_secs(seconds));
    }
}

/// Whether an optional stop is set.
fn is_set(stop: Option<&AtomicBool>) -> bool {
    stop.is_some_and(|stop| stop.load(Ordering::SeqCst))
}

/// A [`ContextModel`] over a real [`LlmClient`]: the condenser's model (#641).
///
/// Python's `LLMChunkProcessor` runs over the agent itself; this is the same
/// two calls over the client. [`complete`](ContextModel::complete) is a plain
/// chat whose output-ceiling stop reason is an error, as the trait requires;
/// [`complete_json`](ContextModel::complete_json) goes through
/// [`crate::agents::chat_json`] (Python's `_extract_structured` calls
/// `agent.chat_json`), so a non-object is an error after its re-asks.
///
/// Each call retries what a retry may cure ([`ChatError::is_retryable`]: a
/// transport failure or a [`crate::llm::RETRYABLE_STATUSES`] status, never a
/// 401, 404 or 400: #646) up to [`CONTEXT_MODEL_ATTEMPTS`] tries, waiting
/// [`LlmClient::retry_base_delay`] and doubling, and never once the stop is
/// set; a set stop sends nothing. A call whose tries ran out says how many
/// were made. `#[non_exhaustive]`, as [`LlmChat`] is.
#[non_exhaustive]
pub struct LlmContextModel<'a> {
    /// The client the requests are sent through.
    pub client: &'a LlmClient,
    /// The `"provider:model_name"` string.
    pub model: String,
    /// As [`LlmChat::num_ctx`].
    pub num_ctx: Option<NonZeroU32>,
    /// As [`LlmChat::stop`].
    pub stop: Option<&'a AtomicBool>,
    /// The last call's request failure, for
    /// [`ContextModel::last_request_failure`] (#616). shortcut: one slot, so a
    /// model shared by two threads reports whichever call ended last; upgrade
    /// to a failure returned with each call once a model must be shared.
    last_failure: Mutex<Option<RequestFailure>>,
}

impl<'a> LlmContextModel<'a> {
    /// A model over `client`, sending to `model`, with no window and no stop.
    #[must_use]
    pub fn new(client: &'a LlmClient, model: impl Into<String>) -> Self {
        LlmContextModel {
            client,
            model: model.into(),
            num_ctx: None,
            stop: None,
            last_failure: Mutex::new(None),
        }
    }

    /// Send `num_ctx` with every request (`None` sends none).
    #[must_use]
    pub fn with_num_ctx(mut self, num_ctx: Option<NonZeroU32>) -> Self {
        self.num_ctx = num_ctx;
        self
    }

    /// Refuse every request once `stop` is set.
    #[must_use]
    pub fn with_stop(mut self, stop: &'a AtomicBool) -> Self {
        self.stop = Some(stop);
        self
    }

    /// The one-message request both calls send, as Python's processor sends
    /// `[agent.user_msg(prompt)]`.
    fn request(
        &self,
        prompt: &str,
        temperature: f64,
        max_tokens: i64,
        json_mode: bool,
    ) -> ChatRequest {
        ChatRequest {
            messages: vec![LLMMessage::user(prompt)],
            model: Some(self.model.clone()),
            temperature,
            max_tokens,
            json_mode,
            num_ctx: self.num_ctx,
            ..ChatRequest::default()
        }
    }

    /// Run `call` until it succeeds, retrying what `request` says a retry may
    /// cure, up to [`CONTEXT_MODEL_ATTEMPTS`] tries, never once stopped.
    /// `request` reads a failure as its request's (`None` when it is no
    /// request's), and the last one is kept for
    /// [`ContextModel::last_request_failure`].
    fn with_retries<T, E>(
        &self,
        mut call: impl FnMut() -> Result<T, E>,
        request: impl Fn(&E) -> Option<RequestFailure>,
    ) -> Result<T, String>
    where
        E: std::fmt::Display,
    {
        self.record(None);
        let mut attempt = 1;
        loop {
            if is_set(self.stop) {
                self.record(None);
                return Err(STOPPED_MESSAGE.to_string());
            }
            match call() {
                Ok(value) => {
                    self.record(None);
                    return Ok(value);
                }
                Err(error) => {
                    let failure = request(&error);
                    self.record(failure);
                    if !failure.is_some_and(|f| f.retryable) || is_set(self.stop) {
                        return Err(error.to_string());
                    }
                    if attempt >= CONTEXT_MODEL_ATTEMPTS {
                        return Err(format!("after {attempt} attempts: {error}"));
                    }
                }
            }
            std::thread::sleep(self.client.retry_base_delay * 2u32.pow(attempt - 1));
            attempt += 1;
        }
    }
}

impl LlmContextModel<'_> {
    /// Keep `failure` as the last call's.
    fn record(&self, failure: Option<RequestFailure>) {
        *self
            .last_failure
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = failure;
    }
}

impl ContextModel for LlmContextModel<'_> {
    fn complete(&self, prompt: &str, temperature: f64, max_tokens: i64) -> Result<String, String> {
        let request = self.request(prompt, temperature, max_tokens, false);
        // Every `ChatError` is the request's, a malformed answer included.
        let response = self.with_retries(
            || self.client.chat(&request),
            |error: &ChatError| {
                Some(RequestFailure {
                    retryable: error.is_retryable(),
                    status: error.http_status(),
                })
            },
        )?;
        match response.stop_reason.as_deref() {
            // A completion cut at the output ceiling has lost passages.
            Some(reason) if TRUNCATION_STOP_REASONS.contains(&reason) => Err(format!(
                "the response was truncated (stop_reason={reason:?})"
            )),
            _ => Ok(response.content),
        }
    }

    fn complete_json(
        &self,
        prompt: &str,
        temperature: f64,
        max_tokens: i64,
    ) -> Result<Value, String> {
        let request = self.request(prompt, temperature, max_tokens, true);
        let outcome = self.with_retries(
            || {
                let mut source = ClientSource {
                    client: self.client,
                    request: request.clone(),
                    stop: self.stop,
                    failure: None,
                };
                chat_json(
                    &mut source,
                    CONTEXT_MODEL_JSON_RETRIES,
                    temperature,
                    Some(max_tokens),
                    max_tokens,
                    true,
                )
            },
            // Only a request a retry may cure, as `complete` retries: an
            // unknown provider, a missing key, an HTTP refusal (#646) or a
            // malformed answer would fail again,
            // and `chat_json` has re-asked for a bad reply.
            |error| match error {
                ChatJsonError::Transport {
                    retryable, status, ..
                } => Some(RequestFailure {
                    retryable: *retryable,
                    status: *status,
                }),
                _ => None,
            },
        )?;
        Ok(outcome.value)
    }

    fn last_request_failure(&self) -> Option<RequestFailure> {
        *self
            .last_failure
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    fn stop_requested(&self) -> bool {
        is_set(self.stop)
    }
}

/// Python's `str.format`, for the subset the quality prompts use.
///
/// The prompt constants are copied byte-for-byte from the Python, braces
/// included, and Python's `str.format` **collapses `{{` to `{` and `}}` to `}`**
/// — so a template that spells its JSON example with doubled braces sends the
/// model single ones. Rendering by a naive `replace("{title}", …)` would leave
/// the doubled braces in the prompt and send the model a different document.
///
/// Substitution is **single-pass**: a title that happens to contain the text
/// `{abstract}` is not re-scanned and replaced again, because Python's
/// `str.format` substitutes from the template and never from a value.
///
/// A `{name}` no argument matches is left **verbatim** rather than raising
/// Python's `KeyError`: every template here names only arguments its caller
/// supplies, and a renderer that cannot fail is one a caller need not wrap.
///
/// // QUIRK: Python's `str.format` raises `KeyError` on an unmatched name and
/// `ValueError` on a single `{`; this returns the text unchanged. No template in
/// the library can reach either, so the divergence is unreachable rather than
/// observable — and the alternative would be a `Result` every caller must unwrap
/// for a case that cannot happen.
#[must_use]
pub fn format_template(template: &str, args: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut chars = template.chars().peekable();

    while let Some(character) = chars.next() {
        match character {
            '{' => {
                if chars.peek() == Some(&'{') {
                    chars.next();
                    out.push('{');
                    continue;
                }
                let mut name = String::new();
                let mut closed = false;
                for character in chars.by_ref() {
                    if character == '}' {
                        closed = true;
                        break;
                    }
                    name.push(character);
                }
                match args.iter().find(|(key, _)| *key == name) {
                    Some((_, value)) => out.push_str(value),
                    // An unmatched name, or an unclosed brace, travels through
                    // as written.
                    None => {
                        out.push('{');
                        out.push_str(&name);
                        if closed {
                            out.push('}');
                        }
                    }
                }
            }
            '}' => {
                if chars.peek() == Some(&'}') {
                    chars.next();
                }
                out.push('}');
            }
            other => out.push(other),
        }
    }

    out
}
