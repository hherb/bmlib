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

use crate::llm::data_types::{LLMMessage, LLMResponse, LLMToolDefinition};
use crate::llm::protocol::{
    messages_to_anthropic, messages_to_openai, parse_anthropic_response, parse_openai_response,
    tool_choice_to_anthropic, tool_choice_to_openai, tool_def_to_anthropic, tool_def_to_openai,
    Protocol,
};
use crate::publications::fetchers::registry::HttpClient;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::sync::Arc;

/// The provider a model string with no prefix is routed to.
pub const DEFAULT_PROVIDER: &str = "anthropic";

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
            protocol: Protocol::OpenAi,
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
#[derive(Debug, Clone, PartialEq, Eq)]
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
/// `temperature`**, which OpenAI's o-series rejects rather than ignores.
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
            }
        }
    }

    // Merged **last**, so a caller can set a field this port does not know
    // about — and can override one it does, deliberately.
    if let Some(extra) = request.extra_body.as_ref().and_then(Value::as_object) {
        for (key, value) in extra {
            body.insert(key.clone(), value.clone());
        }
    }

    Value::Object(body)
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
pub fn parse_response(target: &Target, body: &Value) -> Result<LLMResponse, ChatError> {
    let parsed = match target.protocol {
        Protocol::OpenAi => parse_openai_response(body, &target.model),
        Protocol::Anthropic => parse_anthropic_response(body, &target.model),
    };
    parsed.map_err(ChatError::Malformed)
}

/// A chat client over an [`HttpClient`].
///
/// One client for every provider, because the protocol carries the differences:
/// the Python built one SDK per provider and each brought its own connection
/// pool, timeout behaviour and error taxonomy, so the same outage surfaced three
/// ways.
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
            // The server's own words, when it sent any: a bare status code makes
            // a bad model and a bad key read alike.
            //
            // **`text_or_empty` because this is a human-readable diagnostic and
            // never parsed as data.** An error body is quoted for an operator;
            // when it is not valid UTF-8 there is no text to quote, and a lossy
            // decode would put U+FFFD where the server's words should be.
            let text = response.text_or_empty();
            let detail = serde_json::from_str::<Value>(text)
                .ok()
                .and_then(|v| {
                    v.get("error")
                        .and_then(|e| e.get("message"))
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .unwrap_or_else(|| text.chars().take(200).collect());
            return Err(ChatError::Transport(format!(
                "{url} returned HTTP {}: {detail}",
                response.status
            )));
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
        parse_response(&target, &parsed)
    }
}
