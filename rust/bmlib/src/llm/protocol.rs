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

//! The wire formats, as pure value transforms.
//!
//! bmlib reaches OpenAI-compatible and Anthropic-compatible servers, and Ollama
//! over its native API — the Python's seven providers were seven SDKs over
//! three protocols. So
//! the protocol is the unit to port, and it is **pure**: a request body is a
//! value, and a response body is parsed into one. A provider is then a base URL,
//! a model name and a protocol, with no transport of its own.
//!
//! Every function here is total and testable without a socket, which is the
//! point: the Python's equivalents are reachable only through an SDK and a live
//! call, so its conversion rules were only ever exercised by whatever a mocked
//! client returned.

use crate::llm::data_types::{LLMMessage, LLMToolCall, LLMToolDefinition, Role};
use serde_json::{json, Map, Value};

/// Which wire format a provider speaks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum Protocol {
    /// `/v1/chat/completions` — OpenAI, DeepSeek, Mistral, Gemini's
    /// compatibility endpoint, and every local server that imitates them.
    #[default]
    OpenAi,
    /// `/v1/messages` — Anthropic.
    Anthropic,
    /// `/api/chat` — Ollama's native API. Its OpenAI-compatible endpoint drops
    /// `options`, so a request there cannot set `num_ctx`, and Ollama's small
    /// default window silently cuts the start of a long prompt.
    Ollama,
}

impl Protocol {
    /// The path a chat request is posted to.
    #[must_use]
    pub fn chat_path(self) -> &'static str {
        match self {
            Protocol::OpenAi => "/v1/chat/completions",
            Protocol::Anthropic => "/v1/messages",
            Protocol::Ollama => "/api/chat",
        }
    }

    /// The path a model listing is read from.
    #[must_use]
    pub fn models_path(self) -> &'static str {
        match self {
            Protocol::OpenAi => "/v1/models",
            Protocol::Anthropic => "/v1/models",
            Protocol::Ollama => "/api/tags",
        }
    }

    /// The header a credential is sent in.
    ///
    /// Anthropic uses `x-api-key`, and OpenAI and Ollama (a local server needs
    /// none, a proxy in front of one may) `Authorization: Bearer`; a provider
    /// carrying the wrong one gets a 401 that reads like a bad key.
    #[must_use]
    pub fn auth_header(self, api_key: &str) -> (String, String) {
        match self {
            Protocol::OpenAi | Protocol::Ollama => {
                ("Authorization".to_string(), format!("Bearer {api_key}"))
            }
            Protocol::Anthropic => ("x-api-key".to_string(), api_key.to_string()),
        }
    }

    /// The version header a request needs, when the protocol requires one.
    ///
    /// Anthropic rejects a request without it, so it is part of the protocol
    /// rather than an optional extra.
    #[must_use]
    pub fn extra_headers(self) -> Vec<(&'static str, &'static str)> {
        match self {
            Protocol::OpenAi | Protocol::Ollama => Vec::new(),
            Protocol::Anthropic => vec![("anthropic-version", ANTHROPIC_VERSION)],
        }
    }
}

/// The Anthropic API version this port speaks.
pub const ANTHROPIC_VERSION: &str = "2023-06-01";

/// What a protocol transform refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtocolError {
    /// A tool call's arguments were not valid JSON, or were not an object.
    ///
    /// The raw text is kept, because it is the only thing that says what the
    /// model actually emitted.
    ToolArguments {
        /// The tool name, so the message names what went wrong.
        tool: String,
        /// The text that would not parse.
        raw: String,
    },
    /// The body was not an answer: a server's error object (Ollama reports a
    /// rejected request as `{"error": "..."}`, sometimes at HTTP 200) or not a
    /// JSON object at all. The message quotes the server when it said why.
    Malformed(String),
}

impl std::fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProtocolError::ToolArguments { tool, raw } => {
                write!(f, "tool '{tool}' arguments were not a JSON object: {raw}")
            }
            ProtocolError::Malformed(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for ProtocolError {}

// ---------------------------------------------------------------------------
// OpenAI-compatible
// ---------------------------------------------------------------------------

/// Convert messages to OpenAI's shape.
///
/// Three cases carry weight:
///
/// * a `tool` message carries `tool_call_id`, which OpenAI **requires** so the
///   model can correlate the result to the call it answers;
/// * an assistant message that emitted tool calls is re-emitted with them, and
///   its `content` becomes **`null` rather than `""`** when empty — the wire
///   distinguishes "no text" from "empty text";
/// * a tool call's arguments are serialised back to a **JSON string**, which is
///   the form OpenAI both sends and expects.
#[must_use]
pub fn messages_to_openai(messages: &[LLMMessage]) -> Vec<Value> {
    let mut out = Vec::with_capacity(messages.len());
    for message in messages {
        if message.role == Role::Tool {
            let mut entry = Map::new();
            entry.insert("role".to_string(), json!("tool"));
            entry.insert("content".to_string(), json!(message.content));
            if let Some(id) = message.tool_call_id.as_ref() {
                entry.insert("tool_call_id".to_string(), json!(id));
            }
            out.push(Value::Object(entry));
            continue;
        }

        if message.role == Role::Assistant {
            if let Some(calls) = message.tool_calls.as_ref().filter(|c| !c.is_empty()) {
                let mut entry = Map::new();
                entry.insert("role".to_string(), json!("assistant"));
                entry.insert(
                    "content".to_string(),
                    if message.content.is_empty() {
                        Value::Null
                    } else {
                        json!(message.content)
                    },
                );
                entry.insert(
                    "tool_calls".to_string(),
                    Value::Array(calls.iter().map(tool_call_to_openai).collect()),
                );
                out.push(Value::Object(entry));
                continue;
            }
        }

        out.push(json!({
            "role": message.role.as_str(),
            "content": message.content,
        }));
    }
    out
}

/// One tool call in OpenAI's shape, with its arguments as a JSON **string**.
#[must_use]
pub fn tool_call_to_openai(call: &LLMToolCall) -> Value {
    json!({
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            // A JSON string, not an object: OpenAI sends and expects the
            // string form, and an object here is a 400 on most servers. Written
            // with Python's spacing, because the oracle compares the string
            // `json.dumps` produces.
            "arguments": json_dumps_python(&call.arguments),
        },
    })
}

/// Serialise a value the way Python's `json.dumps` does by default.
///
/// `serde_json::to_string` writes compact JSON — `{"a":1}` — where
/// `json.dumps` writes `{"a": 1}`, with a space after each `,` and `:`. The
/// difference is invisible to a server, which parses either, but it is visible
/// in a log, in a stored transcript, and to the oracle that compares this port
/// against the Python byte for byte.
///
/// Reproduced rather than tolerated because this string is **sent to a model**:
/// a transcript replayed to a provider should be the transcript the Python sent,
/// so that a difference in behaviour cannot come from a difference in
/// whitespace. Non-ASCII is left unescaped, which is `ensure_ascii=False` —
/// `serde_json`'s behaviour, and the one that keeps a tool argument legible.
#[must_use]
pub fn json_dumps_python(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => serde_json::to_string(s).unwrap_or_else(|_| "\"\"".to_string()),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(json_dumps_python).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, v)| {
                    let key = serde_json::to_string(k).unwrap_or_else(|_| "\"\"".to_string());
                    format!("{key}: {}", json_dumps_python(v))
                })
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// One tool definition in OpenAI's nested `function` shape.
///
/// bmlib's `parameters` is already JSON Schema, so this is a near-identity wrap.
/// An **absent** schema becomes an empty object schema rather than `null`: a
/// server handed `"parameters": null` rejects the request.
#[must_use]
pub fn tool_def_to_openai(tool: &LLMToolDefinition) -> Value {
    // The Python tests **truthiness**, so an empty object is as absent as a
    // null one and gets the default schema; a server handed `{}` for a tool's
    // parameters rejects the request.
    let parameters = match &tool.parameters {
        Value::Null => json!({"type": "object", "properties": {}}),
        Value::Object(map) if map.is_empty() => json!({"type": "object", "properties": {}}),
        other => other.clone(),
    };
    json!({
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
        },
    })
}

/// `tool_choice` in OpenAI's shape.
///
/// bmlib's `"any"` is an alias for `"required"`, and anything else is taken as a
/// **specific tool name** to force.
#[must_use]
pub fn tool_choice_to_openai(tool_choice: &str) -> Value {
    match tool_choice {
        "required" | "any" => json!("required"),
        "auto" | "none" => json!(tool_choice),
        "" => json!("auto"),
        named => json!({"type": "function", "function": {"name": named}}),
    }
}

/// Split a leading `<think>…</think>` block off `content`.
///
/// Some local OpenAI-compatible servers (llama.cpp, LM Studio) emit reasoning
/// inline rather than in a separate field. Returns `(thinking, remainder)`, and
/// an **empty** block yields `None` for the thinking — an empty trace is not a
/// trace.
#[must_use]
pub fn split_think_tags(content: &str) -> (Option<String>, String) {
    let trimmed = content.trim_start();
    let Some(rest) = trimmed.strip_prefix("<think>") else {
        return (None, content.to_string());
    };
    let Some(end) = rest.find("</think>") else {
        return (None, content.to_string());
    };
    let thinking = rest[..end].trim();
    // The pattern's own `\s*` after the closing tag is part of the match, so
    // exactly that whitespace is consumed — and no more. Trimming further would
    // eat an answer's own leading blank line.
    let remainder = rest[end + "</think>".len()..].trim_start_matches(char::is_whitespace);
    (
        if thinking.is_empty() {
            None
        } else {
            Some(thinking.to_string())
        },
        remainder.to_string(),
    )
}

/// Read a tool call's arguments, which OpenAI sends as a JSON **string**.
///
/// An object is taken as-is (some servers send one), a parseable object string is
/// parsed, and **anything else is kept under `_raw`** rather than dropped: a
/// model that emitted malformed arguments is a fact the caller needs, and
/// discarding it would turn a broken call into a call with no arguments.
#[must_use]
pub fn tool_arguments_from_openai(raw: &Value) -> Value {
    match raw {
        Value::Object(_) => raw.clone(),
        Value::String(text) if text.is_empty() => json!({}),
        Value::String(text) => match serde_json::from_str::<Value>(text) {
            // A parseable object is the arguments.
            Ok(Value::Object(map)) => Value::Object(map),
            // A string that **will not parse** keeps its text, because that is
            // the only record of what the model emitted.
            Err(_) => json!({"_raw": text}),
            // A parseable *non-object* — `"42"`, `"[1,2]"` — is not arguments,
            // and an empty set is what the provider reads it as.
            Ok(_) => json!({}),
        },
        Value::Null => json!({}),
        other => json!({"_raw": other.to_string()}),
    }
}

/// The reasoning field a response carries, if any.
///
/// DeepSeek and vLLM use `reasoning_content`, OpenRouter-style servers
/// `reasoning`. An **empty** string is not a trace.
#[must_use]
pub fn reasoning_from_openai(message: &Value) -> Option<String> {
    for key in ["reasoning_content", "reasoning"] {
        if let Some(text) = message.get(key).and_then(Value::as_str) {
            if !text.is_empty() {
                return Some(text.to_string());
            }
        }
    }
    None
}

/// Parse one OpenAI chat-completion body into a response.
///
/// # Errors
///
/// A body with no usable `choices` entry. Returning an empty response instead
/// would report a rejected request as a model that said nothing — the same
/// silent failure `read_esearch` refuses.
pub fn parse_openai_response(
    body: &Value,
    model: &str,
) -> Result<crate::llm::data_types::LLMResponse, String> {
    let choice = body
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|c| c.first())
        .ok_or_else(|| {
            // NCBI's counterpart lesson: many servers answer a bad request at
            // HTTP 200 with an `error` object and no choices.
            let detail = body
                .get("error")
                .and_then(|e| e.get("message"))
                .and_then(Value::as_str)
                .map(|m| format!(" (server said: {m})"))
                .unwrap_or_default();
            format!("no choices in the response{detail}")
        })?;

    let message = choice.get("message").cloned().unwrap_or(Value::Null);
    let content = message
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let thinking = reasoning_from_openai(&message);

    let tool_calls: Vec<LLMToolCall> = message
        .get("tool_calls")
        .and_then(Value::as_array)
        .map(|calls| {
            calls
                .iter()
                .filter_map(|raw| {
                    let function = raw.get("function")?;
                    let name = function.get("name").and_then(Value::as_str)?.to_string();
                    let arguments = function
                        .get("arguments")
                        .map(tool_arguments_from_openai)
                        .unwrap_or_else(|| json!({}));
                    Some(LLMToolCall {
                        id: raw
                            .get("id")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        name,
                        arguments,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let usage = body.get("usage");
    let input_tokens = usage
        .and_then(|u| u.get("prompt_tokens"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let output_tokens = usage
        .and_then(|u| u.get("completion_tokens"))
        .and_then(Value::as_i64)
        .unwrap_or(0);

    Ok(crate::llm::data_types::LLMResponse {
        content,
        model: model.to_string(),
        input_tokens,
        output_tokens,
        total_tokens: 0,
        stop_reason: choice
            .get("finish_reason")
            .and_then(Value::as_str)
            .map(str::to_string),
        duration_seconds: 0.0,
        tool_calls: if tool_calls.is_empty() {
            None
        } else {
            Some(tool_calls)
        },
        thinking,
    }
    .with_derived_total())
}

// ---------------------------------------------------------------------------
// Anthropic
// ---------------------------------------------------------------------------

/// Convert messages to Anthropic's shape.
///
/// Returns `(system, messages)`: Anthropic takes the system prompt as a
/// **separate parameter**, not as a message.
///
/// Three shapes differ from OpenAI's and each is load-bearing:
///
/// * a tool result is a **`user` message carrying a `tool_result` block**, not a
///   message with `role: "tool"` — Anthropic has no such role;
/// * an assistant turn that emitted tool calls is re-emitted with `tool_use`
///   blocks so the model can correlate the next turn's results to them;
/// * **consecutive tool results merge into one user turn**, which is Anthropic's
///   preferred shape when answering several parallel tool calls.
#[must_use]
pub fn messages_to_anthropic(messages: &[LLMMessage]) -> (String, Vec<Value>) {
    let mut system = String::new();
    let mut out: Vec<Value> = Vec::new();

    for message in messages {
        match message.role {
            Role::System => {
                // **Every** system message is kept, joined by a blank line.
                //
                // The Python used to assign (`system_content = msg.content`), so
                // a conversation with two system turns silently kept only the
                // last — and a caller who prepends a task instruction and then a
                // safety instruction lost the first with no error. Filed as
                // issue #315, and **fixed in Python** in `e9db0f9`; both
                // implementations join now, because the caller sent both and
                // both must reach the model.
                if !system.is_empty() {
                    system.push_str("\n\n");
                }
                system.push_str(&message.content);
                continue;
            }
            Role::Tool => {
                let block = json!({
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id.clone().unwrap_or_default(),
                    "content": message.content,
                });
                // Merge into the previous turn when it is already nothing but
                // tool results.
                //
                // **The `all` is structural, not observable**: the only array
                // content this function emits is a merged tool-result turn, so
                // relaxing it to a bare "content is a list" check passes every
                // test — including one written for it. It is kept because it is
                // the correct rule, and named here so the surviving mutant is a
                // recorded fact rather than a re-discovery.
                let merged = match out.last_mut() {
                    Some(Value::Object(previous))
                        if previous.get("role").and_then(Value::as_str) == Some("user") =>
                    {
                        match previous.get_mut("content") {
                            Some(Value::Array(blocks))
                                if !blocks.is_empty()
                                    && blocks.iter().all(|b| {
                                        b.get("type").and_then(Value::as_str) == Some("tool_result")
                                    }) =>
                            {
                                blocks.push(block.clone());
                                true
                            }
                            _ => false,
                        }
                    }
                    _ => false,
                };
                if !merged {
                    out.push(json!({"role": "user", "content": [block]}));
                }
                continue;
            }
            Role::Assistant => {
                if let Some(calls) = message.tool_calls.as_ref().filter(|c| !c.is_empty()) {
                    let mut blocks: Vec<Value> = Vec::new();
                    if !message.content.is_empty() {
                        blocks.push(json!({"type": "text", "text": message.content}));
                    }
                    for call in calls {
                        blocks.push(json!({
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            // Anthropic calls it `input`, and it is an object
                            // rather than OpenAI's JSON string.
                            "input": call.arguments,
                        }));
                    }
                    out.push(json!({"role": "assistant", "content": blocks}));
                    continue;
                }
            }
            Role::User => {}
        }

        out.push(json!({"role": message.role.as_str(), "content": message.content}));
    }

    (system, out)
}

/// One tool definition in Anthropic's shape.
///
/// OpenAI's `parameters` becomes `input_schema`; the rest passes through. An
/// absent **or empty** schema gets the default object schema, as on the OpenAI
/// side.
#[must_use]
pub fn tool_def_to_anthropic(tool: &LLMToolDefinition) -> Value {
    let input_schema = match &tool.parameters {
        Value::Null => json!({"type": "object", "properties": {}}),
        Value::Object(map) if map.is_empty() => json!({"type": "object", "properties": {}}),
        other => other.clone(),
    };
    json!({
        "name": tool.name,
        "description": tool.description,
        "input_schema": input_schema,
    })
}

/// `tool_choice` in Anthropic's shape, or `None` to omit the parameter.
///
/// **`"auto"` is `None`, not `{"type": "auto"}`**: it is Anthropic's default, so
/// omitting it is equivalent and a slightly smaller request. `"required"` and
/// `"any"` become `{"type": "any"}`; `"none"` becomes `{"type": "none"}`; and
/// anything else is a tool name to force.
#[must_use]
pub fn tool_choice_to_anthropic(tool_choice: &str) -> Option<Value> {
    match tool_choice {
        "auto" | "" => None,
        "required" | "any" => Some(json!({"type": "any"})),
        "none" => Some(json!({"type": "none"})),
        named => Some(json!({"type": "tool", "name": named})),
    }
}

/// Parse one Anthropic messages body into a shared response.
///
/// The content is a **list of blocks**: the text is the concatenation of the
/// `text` blocks, `thinking` blocks are the reasoning trace, and `tool_use`
/// blocks are the calls.
///
/// # Errors
///
/// A body with no `content` array. As on the OpenAI side, a rejected request
/// comes back at HTTP 200 with an `error` object and no content, and reading
/// that as an empty answer would report a rejected call as a silent model.
pub fn parse_anthropic_response(
    body: &Value,
    model: &str,
) -> Result<crate::llm::data_types::LLMResponse, String> {
    let blocks = body
        .get("content")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            let detail = body
                .get("error")
                .and_then(|e| e.get("message"))
                .and_then(Value::as_str)
                .map(|m| format!(" (server said: {m})"))
                .unwrap_or_default();
            format!("no content blocks in the response{detail}")
        })?;

    let mut text = String::new();
    let mut thinking_parts: Vec<&str> = Vec::new();
    let mut tool_calls: Vec<LLMToolCall> = Vec::new();

    for block in blocks {
        match block.get("type").and_then(Value::as_str) {
            Some("text") => {
                if let Some(part) = block.get("text").and_then(Value::as_str) {
                    text.push_str(part);
                }
            }
            Some("thinking") => {
                // An **empty** trace is not a trace. Several blocks join with a
                // blank line, as the Python's provider and the stream join them.
                if let Some(trace) = block.get("thinking").and_then(Value::as_str) {
                    if !trace.is_empty() {
                        thinking_parts.push(trace);
                    }
                }
            }
            Some("tool_use") => {
                if let Some(name) = block.get("name").and_then(Value::as_str) {
                    tool_calls.push(LLMToolCall {
                        id: block
                            .get("id")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        name: name.to_string(),
                        arguments: block.get("input").cloned().unwrap_or_else(|| json!({})),
                    });
                }
            }
            _ => {}
        }
    }

    let usage = body.get("usage");
    let input_tokens = usage
        .and_then(|u| u.get("input_tokens"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let output_tokens = usage
        .and_then(|u| u.get("output_tokens"))
        .and_then(Value::as_i64)
        .unwrap_or(0);

    Ok(crate::llm::data_types::LLMResponse {
        content: text,
        model: model.to_string(),
        input_tokens,
        output_tokens,
        total_tokens: 0,
        stop_reason: body
            .get("stop_reason")
            .and_then(Value::as_str)
            .map(str::to_string),
        duration_seconds: 0.0,
        tool_calls: if tool_calls.is_empty() {
            None
        } else {
            Some(tool_calls)
        },
        thinking: if thinking_parts.is_empty() {
            None
        } else {
            Some(thinking_parts.join(crate::llm::stream::THINKING_BLOCK_SEPARATOR))
        },
    }
    .with_derived_total())
}

// ---------------------------------------------------------------------------
// Ollama (native `/api/chat`)
// ---------------------------------------------------------------------------

/// Characters per token in Ollama's estimate of a count it did not report:
/// Python's `CHARS_PER_TOKEN_ESTIMATE`, counted in code points as Python's
/// `len` counts them.
pub const OLLAMA_CHARS_PER_TOKEN: usize = 4;

/// Convert messages to Ollama's native shape.
///
/// Close to OpenAI's, with two differences that follow Python's provider:
///
/// * an assistant turn's tool calls carry their arguments as an **object**, the
///   form Ollama both sends and expects, not OpenAI's JSON string; and its empty
///   content stays `""` rather than becoming `null`;
/// * a tool result carries `tool_call_id` only when it has a non-empty one.
#[must_use]
pub fn messages_to_ollama(messages: &[LLMMessage]) -> Vec<Value> {
    messages
        .iter()
        .map(|message| {
            let mut entry = Map::new();
            entry.insert("role".to_string(), json!(message.role.as_str()));
            entry.insert("content".to_string(), json!(message.content));
            match message.role {
                Role::Tool => {
                    if let Some(id) = message.tool_call_id.as_ref().filter(|id| !id.is_empty()) {
                        entry.insert("tool_call_id".to_string(), json!(id));
                    }
                }
                Role::Assistant => {
                    if let Some(calls) = message.tool_calls.as_ref().filter(|c| !c.is_empty()) {
                        let calls: Vec<Value> = calls
                            .iter()
                            .map(|call| {
                                json!({
                                    "id": call.id,
                                    "type": "function",
                                    "function": {"name": call.name, "arguments": call.arguments},
                                })
                            })
                            .collect();
                        entry.insert("tool_calls".to_string(), Value::Array(calls));
                    }
                }
                Role::System | Role::User => {}
            }
            Value::Object(entry)
        })
        .collect()
}

/// One tool definition in Ollama's shape, which is OpenAI's nested `function`
/// form, empty schema default included (see [`tool_def_to_openai`]).
#[must_use]
pub fn tool_def_to_ollama(tool: &LLMToolDefinition) -> Value {
    tool_def_to_openai(tool)
}

/// The `think` field Ollama is sent for a request's `think` text.
///
/// Ollama takes a bool, or an effort level (`"low"`, `"medium"`, `"high"`, for
/// gpt-oss models). Rust carries `think` as text, where Python's caller passes a
/// bool, a string or an int, so the text is read back as Python would have sent
/// it: `"true"` and `"false"` are that bool; an integer is a token budget
/// (Anthropic's form), which Ollama has no notion of, so it is on unless it is
/// 0 (Python's `bool(n)`); any other text is an effort level, sent as is.
#[must_use]
pub fn think_to_ollama(think: &str) -> Value {
    match think {
        "true" => json!(true),
        "false" => json!(false),
        other => match other.parse::<i64>() {
            Ok(budget) => json!(budget != 0),
            Err(_) => json!(other),
        },
    }
}

/// Read a native tool call's arguments as Python's provider does.
///
/// An object is the arguments, and a string is parsed (some models send JSON
/// text) with non-JSON kept under `_raw`, as [`tool_arguments_from_openai`]
/// reads them. Anything else is no arguments.
fn tool_arguments_from_ollama(raw: Option<&Value>) -> Value {
    match raw {
        Some(value @ (Value::Object(_) | Value::String(_))) => tool_arguments_from_openai(value),
        _ => json!({}),
    }
}

/// Parse one native `/api/chat` body (with `stream: false`) into a response.
///
/// Follows Python's provider, the oracle:
///
/// * `message.thinking` is the trace, and an empty one is none;
/// * a tool call without an id gets `call_{index}`, so the caller can still
///   correlate the result it sends back;
/// * `prompt_eval_count` and `eval_count` are the counts, and **only an absent
///   one is estimated** ([`OLLAMA_CHARS_PER_TOKEN`] code points a token, over
///   `messages` for the input and the answer for the output): a reported 0, a
///   fully cached prompt, is a real count;
/// * the stop reason is `"tool_calls"` when there are any, else `"length"` when
///   `done_reason` says the output cap was hit, else `"stop"`.
///
/// # Errors
///
/// [`ProtocolError::Malformed`] for a body that is not a JSON object or that
/// carries an `error`: Ollama reports a rejected request that way, and reading
/// it as an empty answer would report it as a model that said nothing.
pub fn parse_ollama_response(
    body: &Value,
    model: &str,
    messages: &[LLMMessage],
) -> Result<crate::llm::data_types::LLMResponse, ProtocolError> {
    if let Some(error) = body.get("error").filter(|e| !e.is_null()) {
        let detail = error
            .as_str()
            .map_or_else(|| error.to_string(), str::to_string);
        return Err(ProtocolError::Malformed(format!(
            "the server refused the request (server said: {detail})"
        )));
    }
    if !body.is_object() {
        return Err(ProtocolError::Malformed(format!(
            "the response is not a JSON object: {body}"
        )));
    }

    let message = body.get("message").unwrap_or(&Value::Null);
    let content = message
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let thinking = message
        .get("thinking")
        .and_then(Value::as_str)
        .filter(|t| !t.is_empty())
        .map(str::to_string);

    let tool_calls: Vec<LLMToolCall> = message
        .get("tool_calls")
        .and_then(Value::as_array)
        .map(|calls| {
            calls
                .iter()
                .enumerate()
                .map(|(index, raw)| {
                    let function = raw.get("function").filter(|f| f.is_object()).unwrap_or(raw);
                    LLMToolCall {
                        id: raw
                            .get("id")
                            .and_then(Value::as_str)
                            .filter(|id| !id.is_empty())
                            .map_or_else(|| format!("call_{index}"), str::to_string),
                        name: function
                            .get("name")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        arguments: tool_arguments_from_ollama(function.get("arguments")),
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    let estimate = |chars: usize| i64::try_from(chars / OLLAMA_CHARS_PER_TOKEN).unwrap_or(i64::MAX);
    let input_tokens = body
        .get("prompt_eval_count")
        .and_then(Value::as_i64)
        .unwrap_or_else(|| estimate(messages.iter().map(|m| m.content.chars().count()).sum()));
    let output_tokens = body
        .get("eval_count")
        .and_then(Value::as_i64)
        .unwrap_or_else(|| estimate(content.chars().count()));

    let stop_reason = if !tool_calls.is_empty() {
        "tool_calls"
    } else if body.get("done_reason").and_then(Value::as_str) == Some("length") {
        "length"
    } else {
        "stop"
    };

    Ok(crate::llm::data_types::LLMResponse {
        content,
        model: model.to_string(),
        input_tokens,
        output_tokens,
        total_tokens: 0,
        stop_reason: Some(stop_reason.to_string()),
        duration_seconds: 0.0,
        tool_calls: if tool_calls.is_empty() {
            None
        } else {
            Some(tool_calls)
        },
        thinking,
    }
    .with_derived_total())
}
