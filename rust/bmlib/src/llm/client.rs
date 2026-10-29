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

//! Routing a `"provider:model"` string to a protocol and a base URL.
//!
//! The Python's `LLMClient` carries seven provider *classes*, each wrapping an
//! SDK. Here a provider is a **record** — a name, a protocol, a base URL, a
//! default model and the environment variable its key lives in — so adding one
//! is adding a row rather than a module, and routing is a lookup.
//!
//! The registry is deliberately the Python's: the same names, so a caller's
//! `"deepseek:deepseek-chat"` keeps working, and the same defaults, so a caller
//! who names no model gets what they got before.

use crate::llm::context_window::{known_context_window, read_context_window, ContextWindowError};
use crate::llm::data_types::{LLMMessage, LLMResponse, LLMToolDefinition};
use crate::llm::protocol::{
    messages_to_anthropic, messages_to_ollama, messages_to_openai, parse_anthropic_response,
    parse_ollama_response, parse_openai_response, think_to_ollama, tool_choice_to_anthropic,
    tool_choice_to_openai, tool_def_to_anthropic, tool_def_to_ollama, tool_def_to_openai, Protocol,
};
use crate::llm::stream::{run_stream, SseEvents, StreamDelta, SSE_DONE, STREAM_USAGE_PROVIDERS};
use crate::publications::fetchers::registry::{FetchError, HttpClient, HttpStream};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::io::Read;
use std::num::NonZeroU32;
use std::ops::ControlFlow;
use std::sync::Arc;
use std::time::Duration;

/// The provider a model string with no prefix is routed to.
pub const DEFAULT_PROVIDER: &str = "anthropic";

/// How many times [`LlmClient::chat_stream`] tries to open a stream.
///
/// As many tries as Python's Anthropic and OpenAI SDKs make by default (one
/// try and two retries; ollama-python retries nothing); which failures are
/// retried, and the backoff, are this port's own (the SDKs also retry 408 and
/// 409 and honour `Retry-After`). Only opening is retried: once a delta has
/// reached the caller, a retry would show them a second answer after the
/// first. Every [`FetchError::Transport`] is retried, since it does not say
/// which step failed; a timeout waiting for the response headers is one, so a
/// request the server was already answering can be sent again, as the SDKs
/// send it again. Any other `FetchError` (a client that cannot stream, a body
/// that cannot be encoded) would fail the same way again, so it is not.
pub const STREAM_OPEN_ATTEMPTS: u32 = 3;

/// The statuses on which opening a stream is retried: rate limiting, server
/// failures, and Anthropic's 529 "overloaded".
pub const RETRYABLE_STATUSES: &[u16] = &[429, 500, 502, 503, 504, 529];

/// How much of a refused stream's body is read to quote in the error.
pub const ERROR_BODY_LIMIT: u64 = 64 * 1024;

/// The wait before the first retry; each later one doubles it.
pub const DEFAULT_RETRY_BASE_DELAY: Duration = Duration::from_secs(1);

/// Providers whose chat path accepts OpenAI-style tool calling.
///
/// A **static allowlist** rather than a per-model capability query: some
/// providers report capabilities per model, and asking about every model to
/// answer "does this provider support tools?" is wasteful. Passing `tools` to a
/// provider outside this set is refused rather than silently ignored, because a
/// model given no tools answers as though none were needed — which reads as a
/// model that chose not to use them.
pub const TOOL_CAPABLE_PROVIDERS: &[&str] = &[
    "anthropic",
    "openai",
    "deepseek",
    "mistral",
    "gemini",
    "ollama",
];

/// Whether a provider supports tool calling.
///
/// Accepts a bare name (`"anthropic"`) or a full `"provider:model"` string,
/// case-insensitively — the same shape the Python's `supports_tools` takes, so a
/// caller's probe keeps working.
#[must_use]
pub fn supports_tools(provider_name: &str) -> bool {
    let head = provider_name
        .split(':')
        .next()
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    TOOL_CAPABLE_PROVIDERS.contains(&head.as_str())
}

/// Split `"provider:model"` into its parts.
///
/// A model string with no colon takes [`DEFAULT_PROVIDER`] and the whole string
/// as the model. The provider is **lowercased**: a mixed-case `"Anthropic"` must
/// route to the same registry row as `"anthropic"`, or a caller's working
/// configuration breaks on capitalisation.
///
/// Only the **first** colon splits, because a model name may contain one —
/// Ollama tags are `name:tag`, so `"ollama:medgemma:q8"` is the Ollama model
/// `medgemma:q8` and not a malformed string.
#[must_use]
pub fn parse_model_string(model: Option<&str>, default_provider: &str) -> (String, String) {
    match model {
        Some(model) if model.contains(':') => {
            let (provider, name) = model.split_once(':').expect("contains a colon");
            (provider.to_lowercase(), name.to_string())
        }
        other => (
            default_provider.to_lowercase(),
            other.unwrap_or_default().to_string(),
        ),
    }
}

/// One provider, as data.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderSpec {
    /// The registry key.
    pub name: &'static str,
    /// The wire format it speaks.
    pub protocol: Protocol,
    /// The base URL, without a trailing slash.
    pub base_url: &'static str,
    /// The model used when the caller names none.
    pub default_model: &'static str,
    /// The environment variable its credential is read from, if any.
    pub api_key_env: Option<&'static str>,
    /// Whether it is a local server.
    pub is_local: bool,
}

impl ProviderSpec {
    /// The credential for this provider, from the explicit key or the
    /// environment.
    ///
    /// An explicit key wins, so a caller can override a stale environment
    /// variable without unsetting it.
    #[must_use]
    pub fn resolve_api_key(&self, explicit: Option<&str>) -> Option<String> {
        if let Some(key) = explicit.filter(|k| !k.is_empty()) {
            return Some(key.to_string());
        }
        self.api_key_env
            .and_then(|var| std::env::var(var).ok())
            .filter(|key| !key.is_empty())
    }

    /// Whether this provider needs a credential to be usable.
    #[must_use]
    pub fn requires_api_key(&self) -> bool {
        !self.is_local && self.api_key_env.is_some()
    }
}

/// Every provider the port knows, keyed by name.
///
/// The rows are the Python's providers, and the base URLs are **the same
/// literals** — a wrong URL is a 404 that reads as a bad key.
#[must_use]
pub fn provider_specs() -> BTreeMap<&'static str, ProviderSpec> {
    let rows = [
        ProviderSpec {
            name: "anthropic",
            protocol: Protocol::Anthropic,
            base_url: "https://api.anthropic.com",
            default_model: "claude-sonnet-4-20250514",
            api_key_env: Some("ANTHROPIC_API_KEY"),
            is_local: false,
        },
        ProviderSpec {
            name: "openai",
            protocol: Protocol::OpenAi,
            base_url: "https://api.openai.com",
            default_model: "gpt-4o-mini",
            api_key_env: Some("OPENAI_API_KEY"),
            is_local: false,
        },
        ProviderSpec {
            name: "deepseek",
            protocol: Protocol::OpenAi,
            base_url: "https://api.deepseek.com",
            default_model: "deepseek-chat",
            api_key_env: Some("DEEPSEEK_API_KEY"),
            is_local: false,
        },
        ProviderSpec {
            name: "mistral",
            protocol: Protocol::OpenAi,
            base_url: "https://api.mistral.ai",
            default_model: "mistral-large-latest",
            api_key_env: Some("MISTRAL_API_KEY"),
            is_local: false,
        },
        ProviderSpec {
            name: "gemini",
            protocol: Protocol::OpenAi,
            // Gemini's OpenAI-compatibility endpoint, not its native API: the
            // shell `generativelanguage.googleapis.com` is the native one, and a
            // request there with OpenAI's body is a 400.
            base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
            default_model: "gemini-2.0-flash",
            api_key_env: Some("GEMINI_API_KEY"),
            is_local: false,
        },
        ProviderSpec {
            name: "ollama",
            protocol: Protocol::Ollama,
            base_url: "http://localhost:11434",
            default_model: "llama3.2",
            api_key_env: None,
            is_local: true,
        },
    ];
    rows.into_iter().map(|row| (row.name, row)).collect()
}

/// What a chat request asks for.
#[derive(Debug, Clone, PartialEq)]
pub struct ChatRequest {
    /// The conversation.
    pub messages: Vec<LLMMessage>,
    /// The provider:model string, or a bare model for the default provider.
    pub model: Option<String>,
    /// Sampling temperature.
    pub temperature: f64,
    /// The output cap. Sent as `max_completion_tokens` for a reasoning model.
    pub max_tokens: i64,
    /// Nucleus sampling, when set.
    pub top_p: Option<f64>,
    /// Ask the server for a JSON object.
    pub json_mode: bool,
    /// Tools the model may call.
    pub tools: Option<Vec<LLMToolDefinition>>,
    /// The canonical choice among them.
    pub tool_choice: String,
    /// Ask for a separated reasoning trace.
    pub think: Option<String>,
    /// Extra top-level body fields, merged last so a caller can set anything a
    /// server accepts without this port knowing the name.
    pub extra_body: Option<Value>,
    /// The context window, in tokens, to load an Ollama model with, sent as
    /// `options.num_ctx`. `None` sends nothing, leaving the server's default,
    /// which is often far smaller than the model's window and silently drops
    /// the start of a long prompt. **Ignored by the OpenAI and Anthropic
    /// protocols**: a cloud model's window is the provider's, and the setting
    /// only lowers what the caller plans with there.
    pub num_ctx: Option<NonZeroU32>,
}

impl Default for ChatRequest {
    fn default() -> Self {
        ChatRequest {
            messages: Vec::new(),
            model: None,
            temperature: 0.7,
            max_tokens: 4096,
            top_p: None,
            json_mode: false,
            tools: None,
            tool_choice: "auto".to_string(),
            think: None,
            extra_body: None,
            num_ctx: None,
        }
    }
}

impl ChatRequest {
    /// A request for one user turn.
    #[must_use]
    pub fn new(messages: Vec<LLMMessage>) -> Self {
        ChatRequest {
            messages,
            ..Default::default()
        }
    }

    /// Set the model string.
    #[must_use]
    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = Some(model.into());
        self
    }
}

/// What a chat call refused.
///
/// `#[non_exhaustive]`, so a variant added later is not a breaking change; a
/// `match` on it needs a wildcard arm.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ChatError {
    /// The named provider is not in the registry.
    UnknownProvider(String),
    /// The provider needs a credential and none was available.
    MissingApiKey {
        /// The provider that needs one.
        provider: String,
        /// The environment variable that would have supplied it.
        env_var: String,
    },
    /// Tools were passed to a provider that does not support them.
    ///
    /// **Unreachable with today's registry, and deliberately kept.** Every row in
    /// [`provider_specs`] is in [`TOOL_CAPABLE_PROVIDERS`], so no input reaches
    /// this — which is why a mutant deleting the check survives the file. It
    /// exists for the row someone adds later: a provider without tool support
    /// would otherwise accept `tools`, drop them, and return prose, which the
    /// caller reads as *the model chose not to call anything* rather than *the
    /// tools were never sent*. Recorded here rather than left as a
    /// re-discovery, and the check is one condition rather than a redesign.
    ToolsUnsupported(String),
    /// The transport failed.
    Transport(String),
    /// The server's answer could not be used.
    Malformed(String),
    /// A stream failed after it opened or ended before the answer was
    /// complete: an `error` event (the server's message is quoted), a read
    /// that broke off, an event that is not UTF-8, not JSON or not an object,
    /// a tool call, or a missing end marker. The deltas already delivered are
    /// what arrived of an incomplete answer.
    Stream(String),
    /// Tools were passed to [`LlmClient::chat_stream`], which does not carry
    /// tool calls; refused rather than sent without them.
    StreamingToolsUnsupported,
}

impl std::fmt::Display for ChatError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ChatError::UnknownProvider(name) => write!(f, "unknown provider {name:?}"),
            ChatError::MissingApiKey { provider, env_var } => write!(
                f,
                "{provider} requires an API key; set {env_var} or pass one explicitly"
            ),
            ChatError::ToolsUnsupported(provider) => {
                write!(f, "{provider} does not support tool calling")
            }
            ChatError::Transport(message) => write!(f, "{message}"),
            ChatError::Malformed(message) => write!(f, "{message}"),
            ChatError::Stream(message) => write!(f, "{message}"),
            ChatError::StreamingToolsUnsupported => write!(
                f,
                "chat_stream does not carry tool calls; use chat() for a request with tools"
            ),
        }
    }
}

impl std::error::Error for ChatError {}

/// A resolved target for one chat call.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    /// The registry name.
    pub provider: String,
    /// The model to ask for.
    pub model: String,
    /// The wire format.
    pub protocol: Protocol,
    /// The base URL, without a trailing slash.
    pub base_url: String,
    /// The credential, when one is needed and available.
    pub api_key: Option<String>,
}

/// Resolve a chat request to a target.
///
/// # Errors
///
/// An unknown provider, a missing credential, or tools on a provider that
/// cannot call them.
pub fn resolve_target(
    request: &ChatRequest,
    default_provider: &str,
    base_url_override: Option<&str>,
    api_key_override: Option<&str>,
) -> Result<Target, ChatError> {
    let specs = provider_specs();
    let (mut provider, mut named_model) =
        parse_model_string(request.model.as_deref(), default_provider);

    // A bare **known provider name** names that provider, not a model: a caller
    // writing `model="anthropic"` means "Anthropic's default model", and
    // treating it as a model name sends that string to whichever provider was
    // the default, as a model that does not exist there.
    if !request.model.as_deref().unwrap_or_default().contains(':') {
        let bare = request
            .model
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        if specs.contains_key(bare.as_str()) {
            provider = bare;
            named_model = String::new();
        }
    }

    let spec = specs
        .get(provider.as_str())
        .ok_or_else(|| ChatError::UnknownProvider(provider.clone()))?;

    if request.tools.is_some() && !supports_tools(&provider) {
        return Err(ChatError::ToolsUnsupported(provider));
    }

    let api_key = spec.resolve_api_key(api_key_override);
    if spec.requires_api_key() && api_key.is_none() {
        return Err(ChatError::MissingApiKey {
            provider: provider.clone(),
            env_var: spec.api_key_env.unwrap_or_default().to_string(),
        });
    }

    Ok(Target {
        provider,
        model: if named_model.is_empty() {
            spec.default_model.to_string()
        } else {
            named_model
        },
        protocol: spec.protocol,
        base_url: base_url_override
            .unwrap_or(spec.base_url)
            .trim_end_matches('/')
            .to_string(),
        api_key,
    })
}

/// Build the JSON body for a chat call.
///
/// `is_reasoning` switches `max_tokens` to `max_completion_tokens` and **drops
/// `temperature`**, which OpenAI's o-series rejects rather than ignores. It is
/// OpenAI's alone; the other protocols ignore it.
///
/// An Ollama body carries `stream: false`, set last so `extra_body` cannot turn
/// it on (the native API streams by default, and [`LlmClient::chat`] reads one
/// answer). Its `options` from `extra_body` merge field by field, and the
/// request's `num_ctx` is set after them, so it cannot be silently replaced.
#[must_use]
pub fn chat_body(request: &ChatRequest, target: &Target, is_reasoning: bool) -> Value {
    let mut body = Map::new();
    body.insert("model".to_string(), json!(target.model));

    match target.protocol {
        Protocol::OpenAi => {
            body.insert(
                "messages".to_string(),
                json!(messages_to_openai(&request.messages)),
            );
            if is_reasoning {
                body.insert(
                    "max_completion_tokens".to_string(),
                    json!(request.max_tokens),
                );
            } else {
                body.insert("max_tokens".to_string(), json!(request.max_tokens));
                body.insert("temperature".to_string(), json!(request.temperature));
                if let Some(top_p) = request.top_p {
                    body.insert("top_p".to_string(), json!(top_p));
                }
            }
            if request.json_mode {
                body.insert(
                    "response_format".to_string(),
                    json!({"type": "json_object"}),
                );
            }
            if let Some(tools) = request.tools.as_ref() {
                body.insert(
                    "tools".to_string(),
                    Value::Array(tools.iter().map(tool_def_to_openai).collect()),
                );
                body.insert(
                    "tool_choice".to_string(),
                    tool_choice_to_openai(&request.tool_choice),
                );
            }
            // `reasoning_effort` only on a reasoning model: a non-reasoning
            // server returns 400 for an unknown parameter where it would
            // otherwise ignore one.
            if let Some(effort) = request.think.as_ref().filter(|_| is_reasoning) {
                body.insert("reasoning_effort".to_string(), json!(effort));
            }
        }
        Protocol::Anthropic => {
            let (system, messages) = messages_to_anthropic(&request.messages);
            // Omitted rather than sent as `""`, which Anthropic reads as a
            // system prompt that is the empty string.
            if !system.is_empty() {
                body.insert("system".to_string(), json!(system));
            }
            body.insert("messages".to_string(), Value::Array(messages));
            body.insert("max_tokens".to_string(), json!(request.max_tokens));
            body.insert("temperature".to_string(), json!(request.temperature));
            if let Some(top_p) = request.top_p {
                body.insert("top_p".to_string(), json!(top_p));
            }
            if let Some(tools) = request.tools.as_ref() {
                body.insert(
                    "tools".to_string(),
                    Value::Array(tools.iter().map(tool_def_to_anthropic).collect()),
                );
                // Anthropic's default is `auto`, so the parameter is omitted
                // rather than sent — see `tool_choice_to_anthropic`.
                if let Some(choice) = tool_choice_to_anthropic(&request.tool_choice) {
                    body.insert("tool_choice".to_string(), choice);
                }
            }
            // Anthropic's reasoning is requested with a `thinking` object, whose
            // budget must be a positive number of tokens below the output cap.
            if let Some(budget) = request.think.as_deref().and_then(|t| t.parse::<i64>().ok()) {
                body.insert(
                    "thinking".to_string(),
                    json!({"type": "enabled", "budget_tokens": budget}),
                );
                // The API rejects non-default sampling parameters while
                // thinking is enabled, so they are omitted, as the Python's
                // provider omits them.
                body.remove("temperature");
                body.remove("top_p");
            }
        }
        Protocol::Ollama => {
            body.insert(
                "messages".to_string(),
                json!(messages_to_ollama(&request.messages)),
            );
            let mut options = Map::new();
            options.insert("temperature".to_string(), json!(request.temperature));
            options.insert("num_predict".to_string(), json!(request.max_tokens));
            if let Some(top_p) = request.top_p {
                options.insert("top_p".to_string(), json!(top_p));
            }
            body.insert("options".to_string(), Value::Object(options));
            if request.json_mode {
                body.insert("format".to_string(), json!("json"));
            }
            if let Some(think) = request.think.as_deref() {
                body.insert("think".to_string(), think_to_ollama(think));
            }
            // No `tool_choice`: the native API has none, and the model decides.
            if let Some(tools) = request.tools.as_ref() {
                body.insert(
                    "tools".to_string(),
                    Value::Array(tools.iter().map(tool_def_to_ollama).collect()),
                );
            }
        }
    }

    // Merged **last**, so a caller can set a field this port does not know
    // about — and can override one it does, deliberately.
    if let Some(extra) = request.extra_body.as_ref().and_then(Value::as_object) {
        for (key, value) in extra {
            match (body.get_mut(key), value) {
                (Some(Value::Object(ours)), Value::Object(theirs))
                    if target.protocol == Protocol::Ollama && key == "options" =>
                {
                    ours.extend(theirs.clone());
                }
                _ => {
                    body.insert(key.clone(), value.clone());
                }
            }
        }
    }

    if target.protocol == Protocol::Ollama {
        if let Some(num_ctx) = request.num_ctx {
            if let Some(options) = body.get_mut("options").and_then(Value::as_object_mut) {
                options.insert("num_ctx".to_string(), json!(num_ctx.get()));
            }
        }
        body.insert("stream".to_string(), json!(false));
    }

    Value::Object(body)
}

/// Build the JSON body for a streamed chat call.
///
/// [`chat_body`]'s body with `stream: true`, set after the caller's
/// `extra_body` so it cannot be turned off, and `stream_options` asking for
/// usage when the provider is in [`STREAM_USAGE_PROVIDERS`].
#[must_use]
pub fn chat_stream_body(request: &ChatRequest, target: &Target, is_reasoning: bool) -> Value {
    let mut body = chat_body(request, target, is_reasoning);
    if let Some(fields) = body.as_object_mut() {
        fields.insert("stream".to_string(), json!(true));
        if target.protocol == Protocol::OpenAi
            && STREAM_USAGE_PROVIDERS.contains(&target.provider.as_str())
        {
            fields.insert("stream_options".to_string(), json!({"include_usage": true}));
        }
    }
    body
}

/// The error for a rejected chat call, quoting the server when it said why.
///
/// A bare status code makes a bad model and a bad key read alike, so the
/// server's `error.message` is used, else up to 200 characters of its body.
/// **The body is a human-readable diagnostic and never parsed as data**, so a
/// body that is not UTF-8 is quoted as nothing rather than lossily.
fn http_error(url: &str, status: u16, text: &str) -> ChatError {
    let detail = serde_json::from_str::<Value>(text)
        .ok()
        .and_then(|v| {
            v.get("error")
                .and_then(|e| e.get("message"))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_else(|| text.chars().take(200).collect());
    ChatError::Transport(format!("{url} returned HTTP {status}: {detail}"))
}

/// Record a finished call's tokens and cost in the process's token tracker.
///
/// The model is recorded as `provider:model`, which is the Python's own
/// spelling and what makes a summary attributable to a provider. The cost is
/// this port's `calculate_cost`, which prices an unknown model at its
/// provider's fallback rather than at zero.
fn record_usage(target: &Target, response: &LLMResponse) {
    let cost = crate::llm::pricing::calculate_cost(
        &target.provider,
        &target.model,
        response.input_tokens,
        response.output_tokens,
    );
    crate::llm::token_tracker::with_token_tracker(|tracker| {
        tracker.record_usage(
            &format!("{}:{}", target.provider, target.model),
            response.input_tokens,
            response.output_tokens,
            cost,
        );
    });
}

/// Build the URL a chat call is posted to.
#[must_use]
pub fn chat_url(target: &Target) -> String {
    format!("{}{}", target.base_url, target.protocol.chat_path())
}

/// Build the headers a chat call carries.
#[must_use]
pub fn chat_headers(target: &Target) -> BTreeMap<String, String> {
    let mut headers = BTreeMap::new();
    headers.insert("content-type".to_string(), "application/json".to_string());
    if let Some(key) = target.api_key.as_ref() {
        let (name, value) = target.protocol.auth_header(key);
        headers.insert(name, value);
    }
    for (name, value) in target.protocol.extra_headers() {
        headers.insert(name.to_string(), value.to_string());
    }
    headers
}

/// Parse a chat response body for a target's protocol.
///
/// # Errors
///
/// A body the protocol cannot read — including one carrying an error object,
/// which many servers return at HTTP 200.
///
/// `messages` are the request's: Ollama estimates a token count it did not
/// report from them.
pub fn parse_response(
    target: &Target,
    body: &Value,
    messages: &[LLMMessage],
) -> Result<LLMResponse, ChatError> {
    match target.protocol {
        Protocol::OpenAi => {
            parse_openai_response(body, &target.model).map_err(ChatError::Malformed)
        }
        Protocol::Anthropic => {
            parse_anthropic_response(body, &target.model).map_err(ChatError::Malformed)
        }
        Protocol::Ollama => parse_ollama_response(body, &target.model, messages)
            .map_err(|e| ChatError::Malformed(e.to_string())),
    }
}

/// A chat client over an [`HttpClient`].
///
/// One client for every provider, because the protocol carries the differences:
/// the Python built one SDK per provider and each brought its own connection
/// pool, timeout behaviour and error taxonomy, so the same outage surfaced three
/// ways.
///
/// # There is no process-wide singleton, deliberately
///
/// The Python exposes `get_llm_client()` / `reset_llm_client()` over a
/// `threading.Lock`, and **this port has no counterpart**. The reason is that the
/// singleton existed to make construction cheap: `LLMClient.__init__` imported an
/// SDK, read credentials from the environment and built a connection pool, so
/// paying that once per process was worth a lock and a global.
///
/// [`LlmClient::new`] does none of it. It stores an `Arc` and a few fields, reads
/// nothing from the environment (the key is passed in or set by the caller), and
/// creates no connection — the [`HttpClient`] is injected and shared by the caller,
/// who decides its lifetime and pooling. A process-wide global here would add a
/// lock, a lifetime question and a test-ordering hazard in exchange for caching an
/// allocation.
///
/// **What this costs a caller** is one line: keep the `LlmClient` you built. What
/// it buys is that two callers in one process can hold different clients — a
/// different key, base URL or transport — which the singleton cannot express.
///
/// This is recorded rather than left implicit because the two places this port
/// *does* keep a global ([`crate::llm::token_tracker`] and
/// [`crate::agents::metrics`]) make the opposite choice, and the difference looks
/// like an oversight until the reason is written down: those hold *accumulated
/// state* a caller wants once per process, and this holds none.
pub struct LlmClient {
    /// The transport.
    pub client: Arc<dyn HttpClient + Send + Sync>,
    /// The provider a bare model name routes to.
    pub default_provider: String,
    /// A base URL overriding the registry's, when a caller runs a proxy.
    pub base_url: Option<String>,
    /// A credential overriding the environment's.
    pub api_key: Option<String>,
    /// The output cap at which a model is treated as a reasoning model.
    pub reasoning_model_prefixes: Vec<String>,
    /// The wait before the first retry of opening a stream; each later one
    /// doubles it. Zero in a test, so the backoff is not spent.
    pub retry_base_delay: Duration,
}

impl LlmClient {
    /// A client over `client`.
    #[must_use]
    pub fn new(client: Arc<dyn HttpClient + Send + Sync>) -> Self {
        LlmClient {
            client,
            default_provider: DEFAULT_PROVIDER.to_string(),
            base_url: None,
            api_key: None,
            // OpenAI's reasoning models, the family that rejects `temperature`
            // and `max_tokens`. Named rather than sniffed, because "is this
            // model a reasoning model" is not derivable from a model string and
            // guessing wrong sends a 400.
            reasoning_model_prefixes: vec!["o1".to_string(), "o3".to_string(), "o4".to_string()],
            retry_base_delay: DEFAULT_RETRY_BASE_DELAY,
        }
    }

    /// Whether a model is one of the reasoning family.
    #[must_use]
    pub fn is_reasoning_model(&self, model: &str) -> bool {
        let bare = model.split(':').next_back().unwrap_or(model);
        self.reasoning_model_prefixes
            .iter()
            .any(|prefix| bare.starts_with(prefix.as_str()))
    }

    /// Run a chat call.
    ///
    /// # Errors
    ///
    /// A routing failure, a transport failure, or a body the protocol cannot
    /// read.
    pub fn chat(&self, request: &ChatRequest) -> Result<LLMResponse, ChatError> {
        let target = resolve_target(
            request,
            &self.default_provider,
            self.base_url.as_deref(),
            self.api_key.as_deref(),
        )?;
        let body = chat_body(request, &target, self.is_reasoning_model(&target.model));
        let url = chat_url(&target);
        let headers = chat_headers(&target);

        let response = self
            .client
            .post_json(&url, &body, &headers)
            .map_err(|e| ChatError::Transport(e.to_string()))?;

        if !response.is_success() {
            // **`text_or_empty` because this is a human-readable diagnostic and
            // never parsed as data.** When it is not valid UTF-8 there is no
            // text to quote, and a lossy decode would put U+FFFD where the
            // server's words should be.
            return Err(http_error(&url, response.status, response.text_or_empty()));
        }

        // A JSON body must be UTF-8; one that is not is the malformed answer
        // this branch already reports, so the strict read is mapped onto the
        // existing error rather than escaping as a transport failure.
        let text = response.text().map_err(|e| {
            ChatError::Malformed(format!("{url} returned a body that is not JSON: {e}"))
        })?;
        let parsed: Value = serde_json::from_str(text).map_err(|e| {
            ChatError::Malformed(format!("{url} returned a body that is not JSON: {e}"))
        })?;
        let response = parse_response(&target, &parsed, &request.messages)?;

        // **Every successful call is recorded**, which is what makes the tracker's
        // figures mean anything. It was ported, tested and then left unwired: the
        // library could report no usage or cost at all, and a tracker nothing
        // feeds is indistinguishable from no tracker.
        //
        // Recorded **only on success**: a call that failed a status check or a
        // parse returned above, and the Python records after the response is
        // parsed for the same reason — a rejected request consumed no output
        // tokens, and counting it would inflate the totals with calls that
        // produced nothing.
        //
        record_usage(&target, &response);
        Ok(response)
    }

    /// Run a chat call and stream the answer as the model writes it.
    ///
    /// Routes and builds the request as [`LlmClient::chat`] does, then calls
    /// `on_delta` once per [`StreamDelta`], in order, and returns the same
    /// [`LLMResponse`] once the stream ends, recording its usage.
    ///
    /// `on_delta` returning [`ControlFlow::Break`] stops the stream: the
    /// connection is closed and the response holds what arrived, with
    /// `stop_reason` `"cancelled"` (or the server's own, when its end marker
    /// came with the last delta).
    ///
    /// What differs from `chat`: tools are refused, and a tool call that
    /// arrives anyway (tools sent through `extra_body`) fails the stream; the
    /// content is exactly what was streamed; and an OpenAI-compatible server
    /// reports usage only when asked, which only [`STREAM_USAGE_PROVIDERS`]
    /// are, so elsewhere the counts are 0 unless the server sends them
    /// unasked. A stopped stream reports the counts the server had sent when
    /// it stopped (Anthropic's input count is exact and its output count
    /// partial; an OpenAI-compatible server usually sends usage in its last
    /// chunk, so usually none). A failed stream records no usage, as a failed
    /// `chat` records none. Python logs usage asked for and never sent; this
    /// port has no logger, so it is 0 here without a line.
    ///
    /// Opening the stream is retried on a [`FetchError::Transport`] or a
    /// [`RETRYABLE_STATUSES`] status, up to [`STREAM_OPEN_ATTEMPTS`] tries, with
    /// a backoff from [`LlmClient::retry_base_delay`] doubling each time.
    ///
    /// # Errors
    ///
    /// A routing failure, tools ([`ChatError::StreamingToolsUnsupported`]), a
    /// transport failure (a client that cannot stream among them), a rejected
    /// request, or a stream that failed or ended early ([`ChatError::Stream`]).
    pub fn chat_stream(
        &self,
        request: &ChatRequest,
        on_delta: &mut dyn FnMut(&StreamDelta) -> ControlFlow<()>,
    ) -> Result<LLMResponse, ChatError> {
        if request.tools.is_some() {
            return Err(ChatError::StreamingToolsUnsupported);
        }
        let target = resolve_target(
            request,
            &self.default_provider,
            self.base_url.as_deref(),
            self.api_key.as_deref(),
        )?;
        let body = chat_stream_body(request, &target, self.is_reasoning_model(&target.model));
        let url = chat_url(&target);
        let stream = self.open_stream(&url, &body, &chat_headers(&target))?;

        let events = SseEvents::new(std::io::BufReader::new(stream.body))
            .take_while(|data| data.as_deref() != Ok(SSE_DONE))
            .map(|data| {
                data.and_then(|d| {
                    serde_json::from_str::<Value>(&d).map_err(|e| {
                        ChatError::Stream(format!("{url} streamed an event that is not JSON: {e}"))
                    })
                })
            });
        let response = run_stream(target.protocol, events, &target.model, on_delta)?;
        // Recorded for a stopped stream too, with the counts the server had
        // sent by then, which understate what it spent.
        record_usage(&target, &response);
        Ok(response)
    }

    /// Run `send` until it answers, retrying a transport failure or a
    /// [`RETRYABLE_STATUSES`] status up to [`STREAM_OPEN_ATTEMPTS`] tries, with
    /// a backoff from [`LlmClient::retry_base_delay`] doubling each time.
    ///
    /// Returns the last outcome, retried or not, for the caller to judge:
    /// shared by opening a stream and by a context-window lookup.
    /// `status_of` reads the status out of a successful send.
    fn send_with_retries<T>(
        &self,
        mut send: impl FnMut() -> Result<T, FetchError>,
        status_of: impl Fn(&T) -> u16,
    ) -> Result<T, FetchError> {
        let mut attempt = 1;
        loop {
            let outcome = send();
            let retryable = match &outcome {
                Err(FetchError::Transport(_)) => true,
                Err(_) => false,
                Ok(answer) => RETRYABLE_STATUSES.contains(&status_of(answer)),
            };
            if !retryable || attempt >= STREAM_OPEN_ATTEMPTS {
                return outcome;
            }
            std::thread::sleep(self.retry_base_delay * 2u32.pow(attempt - 1));
            attempt += 1;
        }
    }

    /// Open a stream, retrying a transport failure or a retryable status.
    ///
    /// The body of a refused request is read up to [`ERROR_BODY_LIMIT`]
    /// bytes: it only explains the status.
    fn open_stream(
        &self,
        url: &str,
        body: &Value,
        headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, ChatError> {
        let mut stream = self
            .send_with_retries(
                || self.client.post_json_stream(url, body, headers),
                |stream| stream.status,
            )
            .map_err(|e| ChatError::Transport(e.to_string()))?;
        if !stream.is_success() {
            let mut bytes = Vec::new();
            // A body that cannot be read is quoted as nothing: the
            // status is the error, and the body only explains it.
            let _ = (&mut stream.body)
                .take(ERROR_BODY_LIMIT)
                .read_to_end(&mut bytes);
            let text = std::str::from_utf8(&bytes).unwrap_or_default();
            return Err(http_error(url, stream.status, text));
        }
        Ok(stream)
    }

    /// The context window of `model`, in tokens, from the provider.
    ///
    /// `model` is parsed as [`LlmClient::chat`]'s is: `"provider:model"`, or a
    /// bare provider name for its default model. Read the number as input and
    /// answer **together**: a prompt plus the room left for the reply must fit
    /// inside it. OpenAI publishes no metadata endpoint, so it answers from
    /// [`known_context_window`] with no key and no request. Every other
    /// provider is asked, and the answer is read by [`read_context_window`].
    /// A transport failure or a [`RETRYABLE_STATUSES`] status is retried like
    /// opening a stream; a 404 is [`ContextWindowError::NoSuchModel`] at once.
    ///
    /// `Ok(None)` means the provider answered but states no usable window.
    ///
    /// # Errors
    ///
    /// An unknown provider or a missing key (before any request), a model the
    /// provider does not have, a transport failure or other refused status, or
    /// a body that is not JSON ([`ContextWindowError::Malformed`]).
    pub fn context_window(&self, model: &str) -> Result<Option<u64>, ContextWindowError> {
        let request = ChatRequest::new(Vec::new()).with_model(model);
        // Route once with a stand-in key, only to learn the provider: OpenAI
        // needs no key here, so a missing one must not stop it.
        let routed = resolve_target(
            &request,
            &self.default_provider,
            self.base_url.as_deref(),
            Some(ROUTING_ONLY_KEY),
        )
        .map_err(context_window_routing_error)?;
        if routed.provider == "openai" {
            return Ok(known_context_window(&routed.provider, &routed.model));
        }
        let target = resolve_target(
            &request,
            &self.default_provider,
            self.base_url.as_deref(),
            self.api_key.as_deref(),
        )
        .map_err(context_window_routing_error)?;
        let (url, post_body) = context_window_request(&target);
        let headers = context_window_headers(&target);
        let response = self
            .send_with_retries(
                || match &post_body {
                    Some(body) => self.client.post_json(&url, body, &headers),
                    None => self.client.get_with_headers(&url, &headers),
                },
                |response| response.status,
            )
            .map_err(|e| ContextWindowError::Transport(e.to_string()))?;
        let text = String::from_utf8_lossy(&response.body);
        if response.status == HTTP_NOT_FOUND {
            return Err(ContextWindowError::NoSuchModel {
                provider: target.provider,
                model: target.model,
            });
        }
        if !(200..300).contains(&response.status) {
            return Err(ContextWindowError::Transport(
                http_error(&url, response.status, &text).to_string(),
            ));
        }
        let body: Value = serde_json::from_str(&text).map_err(|e| {
            ContextWindowError::Malformed(format!("{url} returned a body that is not JSON: {e}"))
        })?;
        read_context_window(&target.provider, &target.model, &body)
    }
}

/// A placeholder credential for routing a model string when the answer needs
/// no key; never sent anywhere.
const ROUTING_ONLY_KEY: &str = "unused";

/// Gemini's native API reads its key from this header, not `Authorization`.
const GEMINI_KEY_HEADER: &str = "x-goog-api-key";

/// A routing failure from `resolve_target`, as a [`ContextWindowError`].
fn context_window_routing_error(error: ChatError) -> ContextWindowError {
    match error {
        ChatError::MissingApiKey { provider, env_var } => {
            ContextWindowError::MissingApiKey { provider, env_var }
        }
        ChatError::UnknownProvider(name) => ContextWindowError::UnknownProvider(name),
        other => ContextWindowError::Transport(other.to_string()),
    }
}

/// The headers a metadata lookup carries: a chat call's, except that Gemini's
/// native API takes its key in [`GEMINI_KEY_HEADER`].
fn context_window_headers(target: &Target) -> BTreeMap<String, String> {
    let mut headers = chat_headers(target);
    if target.provider == "gemini" {
        if let Some(key) = headers.remove("Authorization") {
            headers.insert(
                GEMINI_KEY_HEADER.to_string(),
                key.trim_start_matches("Bearer ").to_string(),
            );
        }
    }
    headers
}

/// The status that means the provider has no such model.
const HTTP_NOT_FOUND: u16 = 404;

/// Characters that stay as they are in a URL path segment: RFC 3986's
/// unreserved set. Everything else, `/` included, is percent-encoded so a
/// model id can never add or end a path segment.
fn is_unreserved(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~')
}

/// `text` percent-encoded for use as one URL path segment.
fn percent_encode_segment(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        if is_unreserved(byte) {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// The URL, and the JSON body when the lookup is a POST, for asking a
/// provider about `target.model`. Per-provider because the metadata endpoints
/// share nothing: the paths differ, Gemini's lives beside its OpenAI-compatible
/// base rather than under it, and Ollama takes the id in a POST body.
fn context_window_request(target: &Target) -> (String, Option<Value>) {
    let base = &target.base_url;
    let id = percent_encode_segment(&target.model);
    match target.provider.as_str() {
        "deepseek" => (format!("{base}/models"), None),
        "gemini" => {
            let native = base.strip_suffix("/openai").unwrap_or(base);
            (format!("{native}/models/{id}"), None)
        }
        "ollama" => (
            format!("{base}/api/show"),
            Some(json!({ "model": target.model })),
        ),
        // anthropic and mistral
        _ => (format!("{base}/v1/models/{id}"), None),
    }
}
