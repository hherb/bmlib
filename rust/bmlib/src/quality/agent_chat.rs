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

use crate::agents::{chat_json, ChatJsonError, ChatJsonOutcome, ChatSource};
use crate::llm::{ChatRequest, LLMMessage, LLMResponse, LlmClient};

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
pub struct LlmChat<'a> {
    /// The client the request is sent through.
    pub client: &'a LlmClient,
    /// The `"provider:model_name"` string, as the Python's agent holds it.
    pub model: String,
}

impl<'a> LlmChat<'a> {
    /// A chat seam over `client`, sending to `model`.
    #[must_use]
    pub fn new(client: &'a LlmClient, model: impl Into<String>) -> Self {
        LlmChat {
            client,
            model: model.into(),
        }
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
            ..ChatRequest::default()
        };
        let mut source = ClientSource {
            client: self.client,
            request,
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
}

impl ChatSource for ClientSource<'_> {
    fn chat(&mut self, _attempt: usize) -> Result<LLMResponse, String> {
        self.client.chat(&self.request).map_err(|e| e.to_string())
    }

    fn backoff(&mut self, attempt: usize) {
        // Python's `2 ** (attempt - 1)`: 1s, 2s, 4s …
        let seconds = 1u64 << attempt.saturating_sub(1).min(63);
        std::thread::sleep(std::time::Duration::from_secs(seconds));
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
