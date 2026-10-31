# bmlib::llm — LLM Client, Streaming and Helpers

One chat client for every provider, over three wire protocols, plus the pure
pieces that go with it: JSON repair, JSON extraction, text chunking, pricing
and token accounting. The Python page is
[llm.md](../../../../docs/manual/llm.md); this one describes the Rust API and
where it differs.

| Need | Feature |
|---|---|
| `LlmClient`, `ChatRequest`, the protocols, streaming, context windows | none (you supply the `HttpClient`) |
| A real `HttpClient` to hand it (`bmlib::http`) | `http-ureq` (default) |
| JSON repair, `extract_json`, `TextChunker`, pricing, `TokenTracker` | none |

There are no per-provider SDKs and no provider classes. A provider is a row in
`provider_specs()` — name, `Protocol`, base URL, default model, the
environment variable its key lives in — and every request goes through one
`LlmClient` over an injected
`HttpClient` ([http.md](http.md)). The rows are
Python's: `anthropic` (the default provider), `openai`, `deepseek`,
`mistral`, `gemini` (its OpenAI-compatible endpoint) and `ollama` (local, no
key). Embeddings are not ported: `EmbeddingResponse` exists as a data type,
but `LlmClient` has no `embed`. Use [`bmlib::embed`](embed.md) for in-process
embeddings.

## Chatting

`ChatRequest` carries the messages and the sampling settings; its defaults are
Python's (`temperature` 0.7, `max_tokens` 4096, `tool_choice` `"auto"`). The
model string is `"provider:model"`, a bare model for the default provider, or
a bare provider name for that provider's default model. Only the first colon
splits, so `"ollama:medgemma:q8"` is the Ollama model `medgemma:q8`.

Here the transport is a stub that answers every POST with a canned
OpenAI-style body and keeps what it was sent:

```rust
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use bmlib::llm::{ChatRequest, LLMMessage, LlmClient};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::Value;

struct Canned {
    body: &'static str,
    sent: Mutex<Vec<(String, Value)>>,
}

impl HttpClient for Canned {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(format!("no GET here: {url}")))
    }
    fn post_json(
        &self,
        url: &str,
        body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.sent.lock().unwrap().push((url.to_string(), body.clone()));
        Ok(HttpResponse::ok_text(self.body))
    }
}

let transport = Arc::new(Canned {
    body: r#"{"choices": [{"message": {"content": "Yes, double-blind."},
               "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 20, "completion_tokens": 5}}"#,
    sent: Mutex::new(Vec::new()),
});
let mut client = LlmClient::new(transport.clone());
client.api_key = Some("sk-test".into()); // else OPENAI_API_KEY is read

let request = ChatRequest {
    temperature: 0.0,
    max_tokens: 256,
    ..ChatRequest::new(vec![
        LLMMessage::system("Answer in one sentence."),
        LLMMessage::user("Was the trial blinded?"),
    ])
}
.with_model("openai:gpt-4o-mini");

let response = client.chat(&request)?;
assert_eq!(response.content, "Yes, double-blind.");
assert_eq!(response.stop_reason.as_deref(), Some("stop"));
assert_eq!(response.total_tokens, 25); // derived: the server sent no total

let (url, body) = transport.sent.lock().unwrap()[0].clone();
assert_eq!(url, "https://api.openai.com/v1/chat/completions");
assert_eq!(body["max_tokens"], 256);
# Ok::<(), bmlib::llm::ChatError>(())
```

`LlmClient`'s fields are public: `default_provider`, `base_url` (overrides the
registry's, for a proxy or a local OpenAI-compatible server), `api_key`
(overrides the environment), `reasoning_model_prefixes` (default `o1`, `o3`,
`o4`: such a model is sent `max_completion_tokens` and no `temperature`) and
`retry_base_delay`. **There is no `get_llm_client()` singleton**: building a
client reads nothing and opens nothing, so keep the one you built, and two
callers may hold clients with different keys or transports.

What a call can refuse is a `ChatError`: `UnknownProvider`, `MissingApiKey {
provider, env_var }` (before any request), `Transport` (no answer, or a
non-2xx status, quoting the server's `error.message` when it gave one),
`Malformed` (a body the protocol cannot read, including an `error` object at
HTTP 200), and for streams `Stream` and `StreamingToolsUnsupported`. It is
`#[non_exhaustive]`. `chat` itself does not retry; only opening a stream and a
context-window lookup do.

Other request fields: `json_mode` (OpenAI `response_format`, Ollama
`format: "json"`; Anthropic has none), `top_p`, `think`, `num_ctx`, and
`extra_body`, merged into the body last so you can send a field this crate
does not know. `chat_body`, `chat_url`, `chat_headers` and `resolve_target`
are public, so you can inspect exactly what would be sent without sending it.

`LLMResponse.duration_seconds` is always `0.0`, as in Python: nothing fills
it. Time the call yourself if you need it.

## Tools

Pass `tools: Some(vec![LLMToolDefinition::new(name, description, json_schema)])`
and a `tool_choice` of `"auto"`, `"none"`, `"required"` (or its alias
`"any"`), or a tool's name to force it. The answer's `tool_calls` holds
`LLMToolCall { id, name, arguments }`; `has_tool_calls()` treats an empty list
as no call. Arguments OpenAI sent as a string that will not parse are kept
under `"_raw"` rather than dropped. Send the result back with
`LLMMessage::tool_result(call_id, json_text)`. `supports_tools(provider)`
answers from `TOOL_CAPABLE_PROVIDERS`, which today is every registered row.

## Thinking

`think` is text, where Python takes a bool, a string or an int:

| Protocol | `think` | Sent as |
|---|---|---|
| OpenAI-compatible | an effort (`"low"`, …) | `reasoning_effort`, only to a reasoning model |
| Anthropic | an integer budget (`"2048"`) | `thinking: {type: enabled, budget_tokens}`; `temperature` and `top_p` dropped |
| Ollama | `"true"`, `"false"`, an integer, or an effort | `think` (an integer is on unless 0) |

The trace comes back in `LLMResponse.thinking`. A local OpenAI-compatible
server that writes a leading `<think>…</think>` block into the content is
handled by `split_think_tags`.

## Streaming

`chat_stream(&request, &mut on_delta)` calls `on_delta` once per
`StreamDelta::Text` or `StreamDelta::Thinking` piece, in order, and returns the
same `LLMResponse` `chat` would. The transport must implement
`HttpClient::post_json_stream` (the default refuses):

```rust
use std::collections::BTreeMap;
use std::ops::ControlFlow;
use std::sync::Arc;
use std::time::Duration;
use bmlib::llm::{ChatRequest, LLMMessage, LlmClient, StreamDelta};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse, HttpStream};
use serde_json::Value;

const SSE: &str = "\
data: {\"choices\":[{\"delta\":{\"content\":\"Blinded\"}}]}\n\n\
data: {\"choices\":[{\"delta\":{\"content\":\", yes.\"},\"finish_reason\":\"stop\"}]}\n\n\
data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":3}}\n\n\
data: [DONE]\n\n";

struct Streams;

impl HttpClient for Streams {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(format!("no GET here: {url}")))
    }
    fn post_json_stream(
        &self,
        _url: &str,
        _body: &Value,
        _headers: &BTreeMap<String, String>,
    ) -> Result<HttpStream, FetchError> {
        Ok(HttpStream { status: 200, body: Box::new(SSE.as_bytes()) })
    }
}

let mut client = LlmClient::new(Arc::new(Streams));
client.api_key = Some("sk-test".into());
client.retry_base_delay = Duration::ZERO; // a retried open would otherwise sleep

let request = ChatRequest::new(vec![LLMMessage::user("Was it blinded?")])
    .with_model("openai:gpt-4o-mini");
let mut shown = String::new();
let response = client.chat_stream(&request, &mut |delta| {
    if let StreamDelta::Text(piece) = delta {
        shown.push_str(piece); // e.g. print it as it arrives
    }
    ControlFlow::Continue(())
})?;
assert_eq!(shown, "Blinded, yes.");
assert_eq!(response.content, shown);
assert_eq!((response.input_tokens, response.output_tokens), (9, 3));
# Ok::<(), bmlib::llm::ChatError>(())
```

- Returning `ControlFlow::Break(())` closes the connection; the response holds
  what arrived, with `stop_reason` `"cancelled"` (`CANCELLED_STOP_REASON`).
- A stream that ends before its end marker (`[DONE]`, Anthropic's
  `message_stop`, Ollama's `done`) is a `ChatError::Stream`, never a short
  answer. So is a server `error` event and a tool call; tools are refused up
  front (`StreamingToolsUnsupported`).
- Opening is tried up to `STREAM_OPEN_ATTEMPTS` (3) times on a transport
  failure or a `RETRYABLE_STATUSES` status (429, 5xx, 529), the wait doubling
  from `retry_base_delay`. Nothing is retried once a delta has been delivered.
- An OpenAI-compatible server reports usage only when asked, and only the
  `STREAM_USAGE_PROVIDERS` are asked; elsewhere the counts are 0.

The pieces are public for a caller with its own transport: `SseEvents` and
`ndjson_events` split the wire, `StreamState` folds events into deltas, and
`run_stream` drives both.

## Context windows

`client.context_window("provider:model")` asks the provider (Anthropic,
Gemini, Mistral, DeepSeek, Ollama's `/api/show`) and reads the answer with
`read_context_window`. OpenAI has no metadata endpoint, so it answers from
`KNOWN_CONTEXT_WINDOWS` without a key or a request; an exact match only.

```rust
use std::sync::Arc;
use bmlib::llm::{known_context_window, LlmClient};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};

struct Offline;
impl HttpClient for Offline {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(format!("offline: {url}")))
    }
}

let client = LlmClient::new(Arc::new(Offline));
assert_eq!(client.context_window("openai:gpt-4o-mini")?, Some(128_000));
assert_eq!(known_context_window("openai", "gpt-4o-mini-2024-07-18"), None);
# Ok::<(), bmlib::llm::ContextWindowError>(())
```

`Ok(None)` means the provider answered and stated no usable window.
`ContextWindowError::NoSuchModel` is a 404 that names the model (not from
DeepSeek, and from Ollama only when its `error` says "not found"). Unlike
Python's `context_window(model, provider=None)`, a bare provider name asks
about that provider's default model.

The number is for input and output together. To use it with Ollama, send it:
`ChatRequest::num_ctx` (a `NonZeroU32`) becomes `options.num_ctx`, without
which Ollama loads the model with its own, often much smaller, window and
silently drops the start of a long prompt. The other protocols ignore it.

## What a call cost

Every successful `chat` and `chat_stream` is recorded in the process-wide
`TokenTracker` under `"provider:model"`, priced by
`llm::pricing::calculate_cost` (dollars per million tokens). An unknown hosted
model is billed at its provider's fallback rate, never at zero; Ollama is free.

```rust
use bmlib::llm::pricing::{calculate_cost, model_pricing};
use bmlib::llm::{with_token_tracker, TokenTracker};

let rate = model_pricing("openai", "gpt-4o-mini");
assert_eq!((rate.input_cost, rate.output_cost), (0.15, 0.6));
assert_eq!(calculate_cost("ollama", "llama3.2", 1_000_000, 1_000_000), 0.0);
assert!(calculate_cost("openai", "a-model-not-in-the-table", 1_000_000, 0) > 0.0);

// A tracker of your own, for one task:
let tracker = TokenTracker::new();
tracker.record_usage("openai:gpt-4o-mini", 1_000, 200, 0.00027);
tracker.record_usage("openai:gpt-4o-mini", 500, 100, 0.000135);
let summary = tracker.get_summary();
assert_eq!(summary.call_count, 2);
assert_eq!(summary.by_model["openai:gpt-4o-mini"].output_tokens, 300);
assert_eq!(tracker.get_recent_records(1)[0].input_tokens, 500);

// The process-wide one, which LlmClient feeds:
let calls = with_token_tracker(|t| t.get_summary().call_count);
# let _ = calls;
```

`with_token_tracker` takes a closure instead of handing out the tracker:
calling it again from inside the closure deadlocks. `reset_token_tracker()`
clears the global in place. The per-agent counterpart is
[`agents::PerformanceMetrics`](agents.md).

## Getting JSON out of a model's answer

`extract_json(text, allow_fragments)` finds the JSON in a reply, preferring a
` ```json ` fence, then an object over an array; it returns `text` unchanged
when nothing parses. `safe_json_loads(text, repair, max_attempts)` parses,
repairing first when asked. `repair_json` / `repair_json_default` return the
mended string. `extract_and_repair_json(text, repair)` does both and says
whether repair was needed.

```rust
use bmlib::llm::{
    extract_and_repair_json, extract_json, repair_json_default, safe_json_loads,
    salvage_json_fields, MAX_REPAIR_ATTEMPTS,
};
use serde_json::json;

let reply = "Here you go:\n```json\n{\"n\": 120}\n```";
assert_eq!(extract_json(reply, true), "{\"n\": 120}");

let sloppy = "{'design': 'RCT', 'arms': [1, 2,],}";
let value = safe_json_loads(sloppy, true, MAX_REPAIR_ATTEMPTS)?;
assert_eq!(value, json!({"design": "RCT", "arms": [1, 2]}));
assert!(safe_json_loads(sloppy, false, MAX_REPAIR_ATTEMPTS).is_err());

// A truncated array of objects closes in the order it opened: both survive.
let mended = repair_json_default(r#"[{"a": 1}, {"b": 2"#)?;
assert_eq!(serde_json::from_str::<serde_json::Value>(&mended).unwrap(),
           json!([{"a": 1}, {"b": 2}]));

let (text, repaired) = extract_and_repair_json("Result: {\"ok\": true,}", true)?;
assert!(repaired);
assert_eq!(serde_json::from_str::<serde_json::Value>(&text).unwrap(), json!({"ok": true}));

// Last resort, never applied automatically: pull named fields out of a wreck.
let fields = salvage_json_fields(
    r#"{"summary": "Good", "score": 8, "items": [1 2"#,
    &["summary".to_string(), "score".to_string()],
);
assert_eq!(fields["score"], json!(8));
# Ok::<(), bmlib::llm::RepairError>(())
```

Where Python appended every `]` and then every `}`, the Rust repairer closes
brackets in reverse order of opening, so an interleaved truncation is repaired
rather than handed to the fragment extractor (which kept only the first
object). Errors are `RepairError` (`EmptyRepair`, `EmptyParse`,
`EmptyExtract`, `TooLarge` past `MAX_JSON_LENGTH`, `Unrepairable`).
`iter_json_spans` exposes the candidate spans both extractors walk.

## Splitting long text

`TextChunker` cuts text into overlapping `TextChunk`s, pulling each cut back
to a paragraph or sentence break when `boundary_aware` is set. **Offsets are
character offsets**, as in Python, not byte offsets.

```rust
use bmlib::llm::{chunk_text, combine_title_and_text, process_with_map_reduce, TextChunker};

let chunker = TextChunker::new(60, 10, true, 20)?;
let text = "Methods were blinded. Allocation was concealed. \
            Outcomes were assessed at twelve weeks. Analysis was by intention to treat.";
let chunks = chunker.chunk_text(text);
assert!(chunks.len() > 1);
assert!(chunks.iter().all(|c| c.content.chars().count() <= 60));
assert_eq!(chunks[0].total_chunks, chunks.len());
assert_eq!(chunker.chunk_info(text).num_chunks, chunks.len());

assert!(TextChunker::new(100, 100, true, 10).is_err()); // overlap must be smaller
assert_eq!(chunk_text("", 100, 10, true, 10)?.len(), 0);

// One map per chunk, one reduce; short text skips chunking.
let words = process_with_map_reduce(
    text,
    |chunk| chunk.split_whitespace().count(),
    |counts| counts.into_iter().sum(),
    10_000,
)?;
assert_eq!(words, 18);

assert_eq!(combine_title_and_text("A trial", "Body.", 200), "Title: A trial\n\nBody.");
# Ok::<(), bmlib::llm::ChunkerError>(())
```

`process_with_rolling_summary` carries a summary from chunk to chunk, and
`get_text_with_priority` picks a document's best text field. For a list of
items, recursion and failure accounting, use
[`bmlib::context_processor`](context_processor.md).
