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

//! The context windows bmlib knows by heart.
//!
//! [`KNOWN_CONTEXT_WINDOWS`] holds `(provider, model, window in tokens)`. Every
//! row is a figure read off the provider's own model page;
//! `tests/fixtures/context_window.json` is the record of each row's source and
//! read date, and this table and Python's `bmlib.llm.context_window` are both
//! tested equal to it. Keys match exactly: a dated snapshot id or a different
//! case is *unknown*, not guessed, because a wrong window silently truncates
//! or overflows a prompt.

use serde_json::Value;

/// `(provider, model, window in tokens)`, one row per known model.
pub const KNOWN_CONTEXT_WINDOWS: &[(&str, &str, u64)] = &[
    ("openai", "gpt-6-astra", 1_050_000),
    ("openai", "gpt-6.1-sol", 1_050_000),
    ("openai", "gpt-6.1-terra", 1_050_000),
    ("openai", "gpt-6-luna", 1_050_000),
    ("openai", "gpt-4o-mini", 128_000),
];

/// The context window (tokens) for an exact `provider` and `model`, or `None`
/// when the pair is not in [`KNOWN_CONTEXT_WINDOWS`]. There is no prefix,
/// case-folding or suffix matching.
pub fn known_context_window(provider: &str, model: &str) -> Option<u64> {
    KNOWN_CONTEXT_WINDOWS
        .iter()
        .find(|(p, m, _)| *p == provider && *m == model)
        .map(|(_, _, w)| *w)
}

/// Body field holding the window, per provider whose model object names it.
const FIELD_BY_PROVIDER: &[(&str, &str)] = &[
    ("anthropic", "max_input_tokens"),
    ("gemini", "inputTokenLimit"),
    ("mistral", "max_context_length"),
];

/// DeepSeek lists every model in one body; this is the per-model field.
const DEEPSEEK_WINDOW_FIELD: &str = "context_window";

/// Body field holding the most a reply may be (the largest `max_tokens` a
/// request may ask for), per provider whose model object names it.
const OUTPUT_FIELD_BY_PROVIDER: &[(&str, &str)] =
    &[("anthropic", "max_tokens"), ("gemini", "outputTokenLimit")];

/// DeepSeek's per-model field for the largest reply.
const DEEPSEEK_OUTPUT_FIELD: &str = "max_output_tokens";

/// Providers whose metadata states no output limit: reading one is unknown,
/// not an error.
const NO_OUTPUT_FIELD: &[&str] = &["mistral", "ollama"];

/// Ollama: the architecture names the prefix of the window's key.
const OLLAMA_ARCHITECTURE_KEY: &str = "general.architecture";
/// Ollama: appended to the architecture to form the window's key.
const OLLAMA_WINDOW_KEY_SUFFIX: &str = ".context_length";

/// Why a context window could not be fetched or read.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ContextWindowError {
    /// The provider has no metadata to read.
    UnknownProvider(String),
    /// The provider needs an API key and none was found.
    MissingApiKey {
        /// The registry name.
        provider: String,
        /// The environment variable that was checked.
        env_var: String,
    },
    /// The provider has no model with this name (as opposed to: no window).
    NoSuchModel {
        /// The registry name.
        provider: String,
        /// The model that was asked for.
        model: String,
    },
    /// The request failed (network, status, timeout).
    Transport {
        /// What went wrong, for the user.
        message: String,
        /// The HTTP status the provider answered with (a 401 or 403 is a
        /// refused key), or `None` where no HTTP answer came (#664).
        status: Option<u16>,
    },
    /// The response could not be understood.
    Malformed(String),
}

impl std::fmt::Display for ContextWindowError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownProvider(name) => {
                write!(
                    f,
                    "{name:?} has no model metadata to read a context window from"
                )
            }
            Self::MissingApiKey { provider, env_var } => write!(
                f,
                "{provider} requires an API key; set {env_var} or pass one explicitly"
            ),
            Self::NoSuchModel { provider, model } => {
                write!(f, "{provider} has no model {model:?}")
            }
            Self::Transport { message, .. } | Self::Malformed(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for ContextWindowError {}

/// `name` in `object` as a positive count. Absent, or present but not a count
/// (bool, float, negative, zero, string), is `None`: a wrong window silently
/// truncates or overflows a prompt, so "unknown" is the honest answer. (The
/// crate has no logger; Python logs the reason.)
fn field(object: &Value, name: &str) -> Option<u64> {
    object.get(name)?.as_u64().filter(|n| *n > 0)
}

/// Read `model`'s context window (tokens) out of a provider's metadata.
///
/// `body` is the JSON the provider returned: the model object (anthropic,
/// gemini, mistral), the model list (deepseek) or `/api/show` (ollama).
/// `Ok(None)` means the body does not state a usable window. A deepseek list
/// without `model` is [`ContextWindowError::NoSuchModel`]; a provider with no
/// metadata is [`ContextWindowError::UnknownProvider`].
pub fn read_context_window(
    provider: &str,
    model: &str,
    body: &Value,
) -> Result<Option<u64>, ContextWindowError> {
    if let Some((_, name)) = FIELD_BY_PROVIDER.iter().find(|(p, _)| *p == provider) {
        return Ok(field(body, name));
    }
    match provider {
        "deepseek" => Ok(deepseek_entry(provider, model, body)?
            .and_then(|entry| field(entry, DEEPSEEK_WINDOW_FIELD))),
        "ollama" => {
            // `modelinfo` is the spelling the ollama client library exposes.
            let info = ["model_info", "modelinfo"]
                .iter()
                .filter_map(|k| body.get(k))
                .find(|v| v.is_object());
            let Some(info) = info else {
                return Ok(None);
            };
            let Some(arch) = info[OLLAMA_ARCHITECTURE_KEY].as_str() else {
                return Ok(None);
            };
            Ok(field(info, &format!("{arch}{OLLAMA_WINDOW_KEY_SUFFIX}")))
        }
        other => Err(ContextWindowError::UnknownProvider(other.to_string())),
    }
}

/// What a provider states about a model: its context window and the largest
/// reply it may give (the most a request's `max_tokens` may ask for). Either
/// is `None` when the provider does not state it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ModelLimits {
    /// The context window in tokens, input and reply together (see
    /// [`read_context_window`]).
    pub window: Option<u64>,
    /// The largest reply in tokens (see [`read_output_limit`]).
    pub max_output: Option<u64>,
}

/// Read the largest reply `model` may give (tokens) out of a provider's
/// metadata: the most a request's `max_tokens` may ask for. A request above
/// it is refused by the provider, so a caller sizing `max_tokens` keeps under
/// it.
///
/// `body` is what [`read_context_window`] takes. Anthropic states it as
/// `max_tokens`, Gemini as `outputTokenLimit`, DeepSeek's list as
/// `max_output_tokens`; Mistral and Ollama state none, so they are `Ok(None)`.
/// A value that is not a positive count is `Ok(None)`, as for the window.
///
/// # Errors
///
/// A deepseek list without `model` is [`ContextWindowError::NoSuchModel`]; a
/// provider with no metadata at all is [`ContextWindowError::UnknownProvider`].
pub fn read_output_limit(
    provider: &str,
    model: &str,
    body: &Value,
) -> Result<Option<u64>, ContextWindowError> {
    if let Some((_, name)) = OUTPUT_FIELD_BY_PROVIDER
        .iter()
        .find(|(p, _)| *p == provider)
    {
        return Ok(field(body, name));
    }
    if NO_OUTPUT_FIELD.contains(&provider) {
        return Ok(None);
    }
    match provider {
        "deepseek" => Ok(deepseek_entry(provider, model, body)?
            .and_then(|entry| field(entry, DEEPSEEK_OUTPUT_FIELD))),
        other => Err(ContextWindowError::UnknownProvider(other.to_string())),
    }
}

/// Read both of `model`'s limits out of one metadata `body` (see
/// [`read_context_window`] and [`read_output_limit`]).
///
/// # Errors
///
/// As [`read_context_window`].
pub fn read_model_limits(
    provider: &str,
    model: &str,
    body: &Value,
) -> Result<ModelLimits, ContextWindowError> {
    Ok(ModelLimits {
        window: read_context_window(provider, model, body)?,
        max_output: read_output_limit(provider, model, body)?,
    })
}

/// DeepSeek's list entry for `model`: `Ok(None)` when `data` is not a list
/// (unknown, not "no such model"), [`ContextWindowError::NoSuchModel`] when no
/// entry names it. Non-object entries never match; with duplicate ids the
/// first wins.
fn deepseek_entry<'a>(
    provider: &str,
    model: &str,
    body: &'a Value,
) -> Result<Option<&'a Value>, ContextWindowError> {
    let Some(list) = body["data"].as_array() else {
        return Ok(None);
    };
    list.iter()
        .find(|e| e.is_object() && e["id"] == model)
        .map(Some)
        .ok_or_else(|| ContextWindowError::NoSuchModel {
            provider: provider.to_string(),
            model: model.to_string(),
        })
}
