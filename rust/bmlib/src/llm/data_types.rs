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

//! Messages and responses, shared by every provider.
//!
//! The types are the transport's **contract**: a provider that cannot fill a
//! field says so with `None` rather than by leaving a default that reads as a
//! real value. That is why `stop_reason` and `thinking` are `Option`, and why
//! `total_tokens` is derived rather than stored — see [`LLMResponse::new`].

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Who sent a message.
///
/// `Tool` carries the result of a tool call back to the model in a follow-up
/// turn, and must name the call it answers — see [`LLMMessage::tool_call_id`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// Instructions for the model.
    System,
    /// A human turn.
    User,
    /// The model's own turn.
    Assistant,
    /// The result of a tool the model invoked.
    Tool,
}

impl Role {
    /// The wire name.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Role::System => "system",
            Role::User => "user",
            Role::Assistant => "assistant",
            Role::Tool => "tool",
        }
    }

    /// Parse a wire name.
    ///
    /// # Errors
    ///
    /// An unrecognised role, named, so a malformed transcript says which member
    /// it was rather than defaulting to one that would send the model the wrong
    /// turn.
    pub fn parse(raw: &str) -> Result<Role, String> {
        match raw {
            "system" => Ok(Role::System),
            "user" => Ok(Role::User),
            "assistant" => Ok(Role::Assistant),
            "tool" => Ok(Role::Tool),
            other => Err(format!("unknown role {other:?}")),
        }
    }
}

/// One turn in a conversation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LLMMessage {
    /// Who sent it.
    pub role: Role,
    /// The text. For a tool result this is a JSON-encoded string; for an
    /// assistant turn consisting solely of tool calls it may be empty.
    pub content: String,
    /// For [`Role::Tool`], the id of the call this answers. Ignored otherwise.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub tool_call_id: Option<String>,
    /// For [`Role::Assistant`], the calls the model emitted in that turn, so a
    /// caller maintaining its own conversation state can re-send it. Ignored
    /// otherwise.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub tool_calls: Option<Vec<LLMToolCall>>,
}

impl LLMMessage {
    /// A plain message.
    #[must_use]
    pub fn new(role: Role, content: impl Into<String>) -> Self {
        LLMMessage {
            role,
            content: content.into(),
            tool_call_id: None,
            tool_calls: None,
        }
    }

    /// A system message.
    #[must_use]
    pub fn system(content: impl Into<String>) -> Self {
        LLMMessage::new(Role::System, content)
    }

    /// A user message.
    #[must_use]
    pub fn user(content: impl Into<String>) -> Self {
        LLMMessage::new(Role::User, content)
    }

    /// An assistant message.
    #[must_use]
    pub fn assistant(content: impl Into<String>) -> Self {
        LLMMessage::new(Role::Assistant, content)
    }

    /// A tool result answering `tool_call_id`.
    #[must_use]
    pub fn tool_result(tool_call_id: impl Into<String>, content: impl Into<String>) -> Self {
        LLMMessage {
            role: Role::Tool,
            content: content.into(),
            tool_call_id: Some(tool_call_id.into()),
            tool_calls: None,
        }
    }
}

/// A tool the model may call.
///
/// The shape is OpenAI's function-calling form, because it is the wider of the
/// two: Anthropic's `input_schema` form is a mechanical rename, so a provider
/// that needs it converts rather than every caller carrying two spellings.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LLMToolDefinition {
    /// The tool's canonical name.
    pub name: String,
    /// What the tool does. The model reads this to decide when to call it.
    pub description: String,
    /// A JSON Schema describing the parameters.
    #[serde(default)]
    pub parameters: Value,
}

impl LLMToolDefinition {
    /// A tool with an object-typed parameter schema.
    #[must_use]
    pub fn new(name: impl Into<String>, description: impl Into<String>, parameters: Value) -> Self {
        LLMToolDefinition {
            name: name.into(),
            description: description.into(),
            parameters,
        }
    }
}

/// A tool invocation the model emitted.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LLMToolCall {
    /// The provider's id, which must be echoed in the answering
    /// [`LLMMessage::tool_call_id`] so the model can correlate the result.
    pub id: String,
    /// The tool's name, which must match one passed in `tools`.
    pub name: String,
    /// The arguments, parsed into a value. Dispatch validates them against the
    /// tool's schema before running anything.
    #[serde(default)]
    pub arguments: Value,
}

/// What a model answered.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct LLMResponse {
    /// The text. Empty when the model emitted only tool calls.
    pub content: String,
    /// The model that answered.
    pub model: String,
    /// Input tokens used.
    pub input_tokens: i64,
    /// Output tokens generated.
    pub output_tokens: i64,
    /// Input plus output.
    ///
    /// Carried rather than derived on read, because a provider may report it
    /// directly and a caller comparing two providers wants what each said — see
    /// [`LLMResponse::new`] for the rule when it is not reported.
    pub total_tokens: i64,
    /// Why generation stopped. Providers that support tools use `"tool_use"`
    /// (Anthropic) or `"tool_calls"` (OpenAI/Ollama) when a tool caused it.
    pub stop_reason: Option<String>,
    /// Wall-clock seconds the request took.
    pub duration_seconds: f64,
    /// The calls the model emitted, or `None` when it called none.
    ///
    /// **`None` and `Some(vec![])` are different claims**: the first says the
    /// provider did not report tool calls at all, the second that it reported an
    /// empty set. A caller deciding whether tool calling is supported reads the
    /// first.
    pub tool_calls: Option<Vec<LLMToolCall>>,
    /// The model's separated reasoning trace, when the provider returns one.
    pub thinking: Option<String>,
}

impl LLMResponse {
    /// A response whose `total_tokens` is derived when the provider gave none.
    ///
    /// The Python's `__post_init__` fills `total_tokens` from the parts **only
    /// when it is zero**, so a provider that reports a total of zero for a
    /// request that used tokens keeps its own answer. This constructor states
    /// that rule in one place instead of leaving it to each call site.
    #[must_use]
    pub fn new(content: impl Into<String>) -> Self {
        LLMResponse {
            content: content.into(),
            ..Default::default()
        }
    }

    /// Fill `total_tokens` from the parts when the provider reported none.
    #[must_use]
    pub fn with_derived_total(mut self) -> Self {
        if self.total_tokens == 0 {
            self.total_tokens = self.input_tokens + self.output_tokens;
        }
        self
    }

    /// Whether the model asked to call any tool.
    ///
    /// `Some(vec![])` is **not** a call: a provider reporting an empty set has
    /// said the model called nothing.
    #[must_use]
    pub fn has_tool_calls(&self) -> bool {
        self.tool_calls
            .as_ref()
            .is_some_and(|calls| !calls.is_empty())
    }
}

/// What an embedding request answered.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct EmbeddingResponse {
    /// The vector.
    pub embedding: Vec<f64>,
    /// The model that produced it.
    pub model: String,
    /// The vector's length, as the provider reported it.
    ///
    /// Carried rather than read from `embedding.len()`: a provider reporting a
    /// dimension its vector does not have is a fact about that provider, and
    /// deriving it here would hide the disagreement.
    pub dimensions: usize,
    /// Input tokens processed.
    pub input_tokens: i64,
}

/// What a batch embedding request answered.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct BatchEmbeddingResponse {
    /// One vector per input text, in input order.
    pub embeddings: Vec<Vec<f64>>,
    /// The model that produced them.
    pub model: String,
    /// The length of each vector; `0` for an empty batch.
    pub dimensions: usize,
    /// Input tokens across the whole batch.
    pub input_tokens: i64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_role_round_trips_through_its_wire_name() {
        for role in [Role::System, Role::User, Role::Assistant, Role::Tool] {
            assert_eq!(Role::parse(role.as_str()).expect("parses"), role);
        }
        let err = Role::parse("human").expect_err("refused");
        assert!(err.contains("human"), "{err}");
    }

    #[test]
    fn a_total_is_derived_only_when_the_provider_reported_none() {
        let mut response = LLMResponse::new("hi");
        response.input_tokens = 3;
        response.output_tokens = 4;
        assert_eq!(response.with_derived_total().total_tokens, 7);

        // A provider-reported total stands, even when it disagrees with the
        // parts — the caller comparing two providers wants what each said.
        let mut reported = LLMResponse::new("hi");
        reported.input_tokens = 3;
        reported.output_tokens = 4;
        reported.total_tokens = 99;
        assert_eq!(reported.with_derived_total().total_tokens, 99);
    }

    #[test]
    fn an_empty_tool_call_list_is_not_a_call() {
        let mut response = LLMResponse::new("hi");
        assert!(!response.has_tool_calls(), "unreported is not a call");
        response.tool_calls = Some(Vec::new());
        assert!(!response.has_tool_calls(), "an empty set is not a call");
        response.tool_calls = Some(vec![LLMToolCall {
            id: "1".to_string(),
            name: "add".to_string(),
            arguments: Value::Null,
        }]);
        assert!(response.has_tool_calls());
    }

    #[test]
    fn a_tool_result_names_the_call_it_answers() {
        let message = LLMMessage::tool_result("call-1", "{\"sum\": 3}");
        assert_eq!(message.role, Role::Tool);
        assert_eq!(message.tool_call_id.as_deref(), Some("call-1"));
    }
}
