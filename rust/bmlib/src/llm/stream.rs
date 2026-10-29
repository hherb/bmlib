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

//! Reading a streamed chat answer, one event at a time.
//!
//! The port of `bmlib/llm/stream.py`. A streamed answer arrives as
//! server-sent events (Ollama's native API: one JSON object per line), each
//! carrying a JSON object whose shape depends on the protocol. [`SseEvents`]
//! splits the wire into those objects' text ([`ndjson_events`] does the same
//! for Ollama),
//! [`StreamState`] reads them in order and hands back the pieces of text and
//! reasoning each one carries ([`StreamDelta`]), and [`run_stream`] drives the
//! two and builds the final [`LLMResponse`]. The reducer is held to the shared
//! contract `tests/fixtures/chat_stream.json`, which the Python reads too.
//!
//! **A stream that ends before its end marker is an error**, never a short
//! answer: a dropped connection would otherwise read as a model that stopped
//! early.

use crate::llm::client::ChatError;
use crate::llm::data_types::LLMResponse;
use crate::llm::protocol::Protocol;
use serde_json::Value;
use std::io::BufRead;
use std::ops::ControlFlow;

/// The `stop_reason` of a stream the caller stopped from `on_delta`.
pub const CANCELLED_STOP_REASON: &str = "cancelled";

/// OpenAI-compatible providers asked to report usage on a stream.
///
/// An OpenAI-compatible server reports token usage on a stream only when the
/// request carries `stream_options: {"include_usage": true}`, and some servers
/// refuse that field as unknown. So it is sent only to the providers listed
/// here, whose OpenAI-compatible APIs document it. The Python keeps the same
/// list. Both languages speak Ollama's native API, whose stream reports usage
/// unasked, so the `ollama` entry has no effect in either.
pub const STREAM_USAGE_PROVIDERS: &[&str] = &["openai", "deepseek", "ollama"];

/// What joins two of Anthropic's reasoning blocks, as Python's `chat()` joins
/// them.
pub const THINKING_BLOCK_SEPARATOR: &str = "\n\n";

/// The data an OpenAI-compatible server sends as its stream's last event.
pub const SSE_DONE: &str = "[DONE]";

/// Why a stream carrying a tool call fails rather than dropping it: tools
/// can reach the server without [`ChatRequest::tools`](crate::llm::ChatRequest)
/// (through `extra_body`), and a dropped call would read as prose.
const TOOL_CALL_ERROR: &str = "the stream carried a tool call, which chat_stream does not read; \
                               use chat() for a request with tools";

/// One piece of a streamed answer, never empty.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StreamDelta {
    /// A piece of the answer itself.
    Text(String),
    /// A piece of the model's separated reasoning trace.
    Thinking(String),
}

impl StreamDelta {
    /// The piece's text, whichever kind it is.
    #[must_use]
    pub fn text(&self) -> &str {
        match self {
            StreamDelta::Text(text) | StreamDelta::Thinking(text) => text,
        }
    }
}

/// The answer so far, built from a stream's events in order.
///
/// Feed every event to [`StreamState::apply`], which returns the deltas it
/// carries, then call [`StreamState::finish`] for the response.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamState {
    protocol: Protocol,
    content: String,
    /// One entry per reasoning block (Anthropic), or a single running one.
    thinking_parts: Vec<String>,
    input_tokens: i64,
    output_tokens: i64,
    stop_reason: Option<String>,
    /// Whether the protocol's end marker has arrived.
    complete: bool,
}

/// `obj[key]` as a string, or `""` when it is absent, `null` or not a string.
fn text_of<'a>(obj: Option<&'a Value>, key: &str) -> &'a str {
    obj.and_then(|o| o.get(key))
        .and_then(Value::as_str)
        .unwrap_or_default()
}

/// `obj[key]` as a token count, when it is an integer.
fn count_of(obj: Option<&Value>, key: &str) -> Option<i64> {
    obj.and_then(|o| o.get(key)).and_then(Value::as_i64)
}

/// The message for an `error` event or object, quoting the server.
///
/// Anthropic and OpenAI send `{"message": ...}` inside `error`; Ollama sends
/// the message as the `error` string itself.
fn server_error(error: &Value) -> String {
    let message = error
        .as_str()
        .unwrap_or_else(|| text_of(Some(error), "message"));
    if message.is_empty() {
        "the stream failed".to_string()
    } else {
        format!("the stream failed (server said: {message})")
    }
}

/// The `error` field of an event, unless it is absent or `null`.
fn error_field(event: &Value) -> Option<&Value> {
    event.get("error").filter(|e| !e.is_null())
}

impl StreamState {
    /// An empty answer, for a stream in `protocol`.
    #[must_use]
    pub fn new(protocol: Protocol) -> Self {
        StreamState {
            protocol,
            content: String::new(),
            thinking_parts: Vec::new(),
            input_tokens: 0,
            output_tokens: 0,
            stop_reason: None,
            complete: false,
        }
    }

    /// Read one event and return the deltas it carries, in order.
    ///
    /// # Errors
    ///
    /// The event is the server reporting a failure (the message quotes it),
    /// is not a JSON object, or carries a tool call.
    pub fn apply(&mut self, event: &Value) -> Result<Vec<StreamDelta>, String> {
        if !event.is_object() {
            return Err(format!(
                "the stream sent an event that is not a JSON object: {event}"
            ));
        }
        match self.protocol {
            Protocol::Anthropic => self.apply_anthropic(event),
            Protocol::OpenAi => self.apply_openai(event),
            Protocol::Ollama => self.apply_ollama(event),
        }
    }

    /// Append `text` to the answer or the reasoning, and record the delta.
    /// An empty piece adds nothing and is not sent.
    fn add(&mut self, thinking: bool, text: &str, deltas: &mut Vec<StreamDelta>) {
        if text.is_empty() {
            return;
        }
        if !thinking {
            self.content.push_str(text);
            deltas.push(StreamDelta::Text(text.to_string()));
            return;
        }
        match self.thinking_parts.last_mut() {
            Some(part) => part.push_str(text),
            None => self.thinking_parts.push(text.to_string()),
        }
        deltas.push(StreamDelta::Thinking(text.to_string()));
    }

    /// Read one Anthropic Messages stream event.
    fn apply_anthropic(&mut self, event: &Value) -> Result<Vec<StreamDelta>, String> {
        let mut deltas = Vec::new();
        match event.get("type").and_then(Value::as_str) {
            Some("error") => {
                return Err(server_error(event.get("error").unwrap_or(&Value::Null)));
            }
            Some("message_start") => {
                let usage = event.get("message").and_then(|m| m.get("usage"));
                self.read_anthropic_usage(usage);
            }
            Some("content_block_start") => {
                let block = event.get("content_block");
                match block.and_then(|b| b.get("type")).and_then(Value::as_str) {
                    Some("thinking") => {
                        // A new block starts a new part, so two blocks stay
                        // separate.
                        self.thinking_parts.push(String::new());
                        self.add(true, text_of(block, "thinking"), &mut deltas);
                    }
                    Some("text") => self.add(false, text_of(block, "text"), &mut deltas),
                    Some(kind) if kind.ends_with("tool_use") => {
                        return Err(TOOL_CALL_ERROR.to_string());
                    }
                    // `redacted_thinking` is encrypted: nothing readable.
                    _ => {}
                }
            }
            Some("content_block_delta") => {
                let delta = event.get("delta");
                match delta.and_then(|d| d.get("type")).and_then(Value::as_str) {
                    Some("text_delta") => self.add(false, text_of(delta, "text"), &mut deltas),
                    Some("thinking_delta") => {
                        self.add(true, text_of(delta, "thinking"), &mut deltas);
                    }
                    // `signature_delta` verifies a reasoning block; not text.
                    _ => {}
                }
            }
            Some("message_delta") => {
                if let Some(stop) = event
                    .get("delta")
                    .and_then(|d| d.get("stop_reason"))
                    .and_then(Value::as_str)
                {
                    self.stop_reason = Some(stop.to_string());
                }
                self.read_anthropic_usage(event.get("usage"));
            }
            Some("message_stop") => self.complete = true,
            // `ping` and `content_block_stop` carry nothing to keep.
            _ => {}
        }
        Ok(deltas)
    }

    /// Take the token counts an Anthropic usage object carries. They are
    /// running totals, so a later one replaces an earlier one.
    fn read_anthropic_usage(&mut self, usage: Option<&Value>) {
        if let Some(input) = count_of(usage, "input_tokens") {
            self.input_tokens = input;
        }
        if let Some(output) = count_of(usage, "output_tokens") {
            self.output_tokens = output;
        }
    }

    /// Read one OpenAI-compatible `chat.completion.chunk`.
    fn apply_openai(&mut self, event: &Value) -> Result<Vec<StreamDelta>, String> {
        if let Some(error) = error_field(event) {
            return Err(server_error(error));
        }
        let mut deltas = Vec::new();
        // One answer is asked for, so only the first choice is read.
        if let Some(choice) = event
            .get("choices")
            .and_then(Value::as_array)
            .and_then(|c| c.first())
        {
            let delta = choice.get("delta");
            let tool_calls = delta.and_then(|d| d.get("tool_calls"));
            if tool_calls
                .and_then(Value::as_array)
                .is_some_and(|c| !c.is_empty())
            {
                return Err(TOOL_CALL_ERROR.to_string());
            }
            // DeepSeek and vLLM name the reasoning `reasoning_content`,
            // OpenRouter-style servers and Ollama `reasoning`. The first that
            // is set is read, as `chat()` reads it: a server may send both.
            let reasoning = match text_of(delta, "reasoning_content") {
                "" => text_of(delta, "reasoning"),
                text => text,
            };
            self.add(true, reasoning, &mut deltas);
            self.add(false, text_of(delta, "content"), &mut deltas);
            if let Some(finish) = choice.get("finish_reason").and_then(Value::as_str) {
                self.stop_reason = Some(finish.to_string());
                self.complete = true;
            }
        }
        let usage = event.get("usage");
        if let Some(prompt) = count_of(usage, "prompt_tokens") {
            self.input_tokens = prompt;
        }
        if let Some(completion) = count_of(usage, "completion_tokens") {
            self.output_tokens = completion;
        }
        Ok(deltas)
    }

    /// Read one chunk of Ollama's native `/api/chat` stream.
    fn apply_ollama(&mut self, event: &Value) -> Result<Vec<StreamDelta>, String> {
        if let Some(error) = error_field(event) {
            return Err(server_error(error));
        }
        let message = event.get("message");
        if message
            .and_then(|m| m.get("tool_calls"))
            .and_then(Value::as_array)
            .is_some_and(|c| !c.is_empty())
        {
            return Err(TOOL_CALL_ERROR.to_string());
        }
        let mut deltas = Vec::new();
        self.add(true, text_of(message, "thinking"), &mut deltas);
        self.add(false, text_of(message, "content"), &mut deltas);
        if event.get("done").and_then(Value::as_bool) == Some(true) {
            self.complete = true;
            // `chat()`'s mapping: only a truncation is told apart from a stop.
            let length = event.get("done_reason").and_then(Value::as_str) == Some("length");
            self.stop_reason = Some(if length { "length" } else { "stop" }.to_string());
            if let Some(prompt) = count_of(Some(event), "prompt_eval_count") {
                self.input_tokens = prompt;
            }
            if let Some(eval) = count_of(Some(event), "eval_count") {
                self.output_tokens = eval;
            }
        }
        Ok(deltas)
    }

    /// Build the response from everything read.
    ///
    /// A stream the caller stopped (`cancelled`) is not checked for its end
    /// marker, and its `stop_reason` is [`CANCELLED_STOP_REASON`], unless the
    /// end marker had already arrived, when the server's own reason is kept.
    ///
    /// # Errors
    ///
    /// The stream ended before its end marker.
    pub fn finish(self, model: &str, cancelled: bool) -> Result<LLMResponse, String> {
        if !cancelled && !self.complete {
            let (name, marker) = match self.protocol {
                Protocol::Anthropic => ("anthropic", "message_stop"),
                Protocol::OpenAi => ("openai", "a finish_reason"),
                Protocol::Ollama => ("ollama", "done"),
            };
            return Err(format!(
                "the {name} stream ended before {marker}; the answer is incomplete"
            ));
        }
        let thinking = self
            .thinking_parts
            .iter()
            .filter(|part| !part.is_empty())
            .map(String::as_str)
            .collect::<Vec<_>>()
            .join(THINKING_BLOCK_SEPARATOR);
        Ok(LLMResponse {
            content: self.content,
            model: model.to_string(),
            input_tokens: self.input_tokens,
            output_tokens: self.output_tokens,
            total_tokens: 0,
            stop_reason: if cancelled && !self.complete {
                Some(CANCELLED_STOP_REASON.to_string())
            } else {
                self.stop_reason
            },
            duration_seconds: 0.0,
            tool_calls: None,
            thinking: if thinking.is_empty() {
                None
            } else {
                Some(thinking)
            },
        }
        .with_derived_total())
    }
}

/// Read `events` to the end, calling `on_delta` for every delta.
///
/// `on_delta` returning [`ControlFlow::Break`] stops the stream: no further
/// event is read, and the response holds what arrived, with `stop_reason`
/// [`CANCELLED_STOP_REASON`] (or the server's own, when the end marker came in
/// the event that carried the last delta). Closing the connection is the
/// caller's job (dropping the reader closes it).
///
/// # Errors
///
/// An event the transport could not deliver (passed through as it came), the
/// server reporting a failure mid-stream, an event that is not a JSON object
/// or carries a tool call, or a stream ending before its end marker
/// ([`ChatError::Stream`]).
pub fn run_stream<I>(
    protocol: Protocol,
    events: I,
    model: &str,
    on_delta: &mut dyn FnMut(&StreamDelta) -> ControlFlow<()>,
) -> Result<LLMResponse, ChatError>
where
    I: IntoIterator<Item = Result<Value, ChatError>>,
{
    let mut state = StreamState::new(protocol);
    for event in events {
        for delta in state.apply(&event?).map_err(ChatError::Stream)? {
            if on_delta(&delta).is_break() {
                return state.finish(model, true).map_err(ChatError::Stream);
            }
        }
    }
    state.finish(model, false).map_err(ChatError::Stream)
}

/// The data of each server-sent event on a reader, in order.
///
/// Follows the SSE format: lines end in `\n` or `\r\n`; a `data:` line adds
/// its value (one leading space dropped) to the event, several joining with
/// `\n`; a blank line ends the event; comments (`:`) and other fields
/// (`event:`, `id:`) are skipped, since every protocol here repeats the event
/// type inside its JSON. An event left unfinished at the end of the stream is
/// discarded, as the format says, and [`StreamState::finish`] then reports the
/// answer as incomplete.
///
/// shortcut: a bare `\r` line ending is not recognised; no provider here sends
/// one. Upgrade if a server does.
pub struct SseEvents<R> {
    reader: R,
    finished: bool,
}

impl<R: BufRead> SseEvents<R> {
    /// The events on `reader`.
    pub fn new(reader: R) -> Self {
        SseEvents {
            reader,
            finished: false,
        }
    }
}

impl<R: BufRead> Iterator for SseEvents<R> {
    /// An event's data, or a read that broke off or a line that is not UTF-8
    /// ([`ChatError::Stream`]: the stream had opened, as Python's providers
    /// report either). Nothing follows an error.
    type Item = Result<String, ChatError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.finished {
            return None;
        }
        let mut data = String::new();
        let mut line = Vec::new();
        loop {
            line.clear();
            match self.reader.read_until(b'\n', &mut line) {
                Ok(0) => {
                    self.finished = true;
                    return None;
                }
                Ok(_) => {}
                Err(e) => {
                    self.finished = true;
                    return Some(Err(ChatError::Stream(format!("the stream failed: {e}"))));
                }
            }
            let Ok(text) = std::str::from_utf8(&line) else {
                self.finished = true;
                return Some(Err(ChatError::Stream(
                    "the stream sent a line that is not UTF-8".to_string(),
                )));
            };
            let text = text.strip_suffix('\n').unwrap_or(text);
            let text = text.strip_suffix('\r').unwrap_or(text);
            if text.is_empty() {
                // The format's rule: an event whose data is empty is not
                // dispatched.
                if let Some(event) = data.strip_suffix('\n').filter(|d| !d.is_empty()) {
                    return Some(Ok(event.to_string()));
                }
                data.clear();
                continue;
            }
            let (field, value) = match text.split_once(':') {
                Some((field, value)) => (field, value.strip_prefix(' ').unwrap_or(value)),
                None => (text, ""),
            };
            if field == "data" {
                data.push_str(value);
                data.push('\n');
            }
        }
    }
}

/// The JSON object on each line of an Ollama stream, in order.
///
/// Ollama's native API streams newline-delimited JSON, not SSE. A line with
/// only whitespace is skipped, a trailing `\r` is dropped.
///
/// # Errors
///
/// An item is [`ChatError::Stream`] when the read broke off, a line is not
/// UTF-8, or a line is not JSON; nothing follows an error.
pub fn ndjson_events<R: BufRead>(reader: R) -> impl Iterator<Item = Result<Value, ChatError>> {
    let mut lines = reader.split(b'\n');
    let mut failed = false;
    std::iter::from_fn(move || loop {
        if failed {
            return None;
        }
        let line = match lines.next()? {
            Ok(line) => line,
            Err(e) => {
                failed = true;
                return Some(Err(ChatError::Stream(format!("the stream failed: {e}"))));
            }
        };
        let parsed = std::str::from_utf8(&line)
            .map_err(|_| "the stream sent a line that is not UTF-8".to_string())
            .and_then(|text| {
                let text = text.trim();
                if text.is_empty() {
                    return Ok(None);
                }
                serde_json::from_str::<Value>(text)
                    .map(Some)
                    .map_err(|e| format!("the stream sent a line that is not JSON: {e}"))
            });
        match parsed {
            Ok(None) => {}
            Ok(Some(event)) => return Some(Ok(event)),
            Err(message) => {
                failed = true;
                return Some(Err(ChatError::Stream(message)));
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn events(wire: &str) -> Vec<Result<String, ChatError>> {
        SseEvents::new(wire.as_bytes()).collect()
    }

    #[test]
    fn events_are_split_at_blank_lines() {
        let got = events("data: {\"a\":1}\n\ndata: {\"b\":2}\r\n\r\n");
        assert_eq!(
            got,
            vec![Ok("{\"a\":1}".to_string()), Ok("{\"b\":2}".to_string())]
        );
    }

    #[test]
    fn several_data_lines_join_with_a_newline() {
        assert_eq!(
            events("data: one\ndata:two\n\n"),
            vec![Ok("one\ntwo".to_string())]
        );
    }

    #[test]
    fn comments_and_other_fields_are_skipped() {
        let wire = ": keep-alive\nevent: message_start\nid: 7\ndata: x\n\n";
        assert_eq!(events(wire), vec![Ok("x".to_string())]);
    }

    #[test]
    fn an_event_with_no_data_is_not_dispatched() {
        assert_eq!(
            events("event: ping\n\ndata:\n\ndata: y\n\n"),
            vec![Ok("y".to_string())]
        );
    }

    #[test]
    fn an_unfinished_event_at_the_end_is_discarded() {
        assert_eq!(events("data: x\n\ndata: half"), vec![Ok("x".to_string())]);
    }

    #[test]
    fn a_line_that_is_not_utf8_stops_the_stream() {
        let wire: &[u8] = b"data: \xff\n\ndata: x\n\n";
        let got: Vec<_> = SseEvents::new(wire).collect();
        assert_eq!(got.len(), 1);
        assert!(matches!(&got[0], Err(ChatError::Stream(m)) if m.contains("UTF-8")));
    }

    #[test]
    fn a_read_failure_is_a_stream_error_and_ends_the_events() {
        struct Broken;
        impl std::io::Read for Broken {
            fn read(&mut self, _buf: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("reset"))
            }
        }
        let got: Vec<_> = SseEvents::new(std::io::BufReader::new(Broken)).collect();
        assert_eq!(got.len(), 1);
        assert!(matches!(&got[0], Err(ChatError::Stream(m)) if m.contains("reset")));
    }

    #[test]
    fn a_transport_error_passes_through_run_stream() {
        let events = vec![Err(ChatError::Transport("gone".to_string()))];
        let got = run_stream(Protocol::OpenAi, events, "m", &mut |_| {
            ControlFlow::Continue(())
        });
        assert_eq!(got, Err(ChatError::Transport("gone".to_string())));
    }

    #[test]
    fn ndjson_skips_blank_lines_and_reads_crlf() {
        let wire = "{\"a\":1}\r\n\n  \n{\"b\":2}";
        let got: Vec<_> = ndjson_events(wire.as_bytes()).collect();
        assert_eq!(got, vec![Ok(json!({"a": 1})), Ok(json!({"b": 2}))]);
    }

    #[test]
    fn ndjson_stops_at_a_line_that_is_not_json() {
        let got: Vec<_> = ndjson_events("nope\n{\"a\":1}\n".as_bytes()).collect();
        assert_eq!(got.len(), 1);
        assert!(matches!(&got[0], Err(ChatError::Stream(m)) if m.contains("not JSON")));
    }

    #[test]
    fn ndjson_read_failure_is_a_stream_error() {
        struct Broken;
        impl std::io::Read for Broken {
            fn read(&mut self, _buf: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("reset"))
            }
        }
        let got: Vec<_> = ndjson_events(std::io::BufReader::new(Broken)).collect();
        assert_eq!(got.len(), 1);
        assert!(matches!(&got[0], Err(ChatError::Stream(m)) if m.contains("reset")));
    }

    #[test]
    fn a_delta_names_its_text() {
        assert_eq!(StreamDelta::Thinking("t".to_string()).text(), "t");
        assert_eq!(StreamDelta::Text("x".to_string()).text(), "x");
    }
}
