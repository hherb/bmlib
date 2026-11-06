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

//! Reading a provider's model listing (#680).
//!
//! [`read_model_listing`] is pure and shared with Python's
//! `bmlib.llm.model_listing.read_model_listing` through
//! `tests/fixtures/model_listing.json`. [`crate::llm::LlmClient::list_models`]
//! fetches the pages and reads each with it.

use serde_json::Value;

/// How many models Anthropic is asked for per page: its maximum (the default
/// is 20, so a listing without paging would be cut short).
pub const ANTHROPIC_LISTING_PAGE_SIZE: u32 = 1000;

/// The most pages one listing follows before it is refused as runaway paging.
/// At [`ANTHROPIC_LISTING_PAGE_SIZE`] a page, that is 100,000 models; a
/// listing past it is an error, never a list cut short.
pub const MAX_LISTING_PAGES: usize = 100;

/// The prefix Gemini's native API puts on a model id (`models/gemini-…`).
/// Stripped from a listed id, since a chat names the model without it and
/// `context_window` puts the id into a URL path.
pub const GEMINI_MODEL_PREFIX: &str = "models/";

/// One page of a provider's model listing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelListingPage {
    /// The model ids on this page, in the provider's order.
    pub ids: Vec<String>,
    /// The cursor of the next page (Anthropic's `last_id` while `has_more`),
    /// or `None` when this page is the last.
    pub next_after: Option<String>,
}

/// Why a model listing could not be had.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ListModelsError {
    /// bmlib has no provider of this name.
    UnknownProvider(String),
    /// The provider needs an API key and none was found.
    MissingApiKey {
        /// The registry name.
        provider: String,
        /// The environment variable that was checked.
        env_var: String,
    },
    /// The request failed (network, status, timeout).
    Transport {
        /// What went wrong, for the user.
        message: String,
        /// The HTTP status the provider answered with (a 401 or 403 is a
        /// refused key), or `None` where no HTTP answer came.
        status: Option<u16>,
    },
    /// The response was not a model listing.
    Malformed(String),
}

impl std::fmt::Display for ListModelsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownProvider(name) => write!(f, "{name:?} is not a known provider"),
            Self::MissingApiKey { provider, env_var } => write!(
                f,
                "{provider} requires an API key; set {env_var} or pass one explicitly"
            ),
            Self::Transport { message, .. } | Self::Malformed(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for ListModelsError {}

/// Read one page of `provider`'s model listing out of the JSON it returned.
///
/// Ollama's `/api/tags` lists `models[].model`; every other provider lists
/// `data[].id` (OpenAI, DeepSeek, Mistral and Gemini's OpenAI-compatible
/// endpoint, and Anthropic, which alone pages: `has_more` and `last_id`).
/// A Gemini id loses a leading [`GEMINI_MODEL_PREFIX`].
///
/// # Errors
///
/// [`ListModelsError::Malformed`] when the body is not of that shape: not an
/// object, no list, an entry without a non-empty string id, or Anthropic's
/// `has_more` without a `last_id` to continue from. Never an empty list in
/// place of a body it could not read.
pub fn read_model_listing(
    provider: &str,
    body: &Value,
) -> Result<ModelListingPage, ListModelsError> {
    let malformed =
        |what: &str| ListModelsError::Malformed(format!("{provider}'s model listing {what}"));
    let (list_key, id_key) = if provider == "ollama" {
        ("models", "model")
    } else {
        ("data", "id")
    };
    let entries = body
        .get(list_key)
        .and_then(Value::as_array)
        .ok_or_else(|| malformed(&format!("has no {list_key:?} list")))?;
    let ids = entries
        .iter()
        .map(|entry| {
            entry
                .get(id_key)
                .and_then(Value::as_str)
                .map(|id| {
                    if provider == "gemini" {
                        id.strip_prefix(GEMINI_MODEL_PREFIX).unwrap_or(id)
                    } else {
                        id
                    }
                })
                .filter(|id| !id.is_empty())
                .map(str::to_string)
                .ok_or_else(|| malformed(&format!("has an entry without a {id_key:?}")))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let next_after =
        if provider == "anthropic" && body.get("has_more").and_then(Value::as_bool) == Some(true) {
            let last = body
                .get("last_id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
                .ok_or_else(|| malformed("says it has more but gives no \"last_id\""))?;
            Some(last.to_string())
        } else {
            None
        };
    Ok(ModelListingPage { ids, next_after })
}
