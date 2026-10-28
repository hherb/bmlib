# Streaming chat in the LLM client (StudyAnalyzer item 6)

**Goal:** let a caller see a chat answer as the model writes it, for
Anthropic, the OpenAI-compatible providers and Ollama, in Python and Rust
alike, without going async.

**Architecture:** `chat_stream(request, on_delta)` beside `chat()`, in
`LLMClient` (Python) and `LlmClient` (Rust). It calls `on_delta` once per
piece of text or reasoning and returns the same `LLMResponse` `chat()` would.
The shared part is a pure reducer, `StreamState`, in `bmlib.llm.stream` /
`bmlib::llm::stream`: it reads one stream event (a JSON object) at a time and
returns the deltas it carries, then `finish()` builds the response. The
shared contract is `tests/fixtures/chat_stream.json`, driven through
`run_stream(protocol, events, model, on_delta)` in both languages.

**Spec:** StudyAnalyzer design §6.2 (answers stream in; citations attach once
the quotes are checked), §7 row 6 (callback per delta over the blocking
client, no async).

## Decisions (builder's calls)

- **Transport differs, the reducer does not.** Python reaches each provider
  through its SDK, as `chat()` does (`messages.create(stream=True)`,
  `chat.completions.create(stream=True)`, `ollama.Client.chat(stream=True)`)
  and feeds the SDK's events to the reducer as dicts (`to_dict()`,
  `model_dump()`). Rust posts through a new `HttpClient::post_json_stream`
  and reads server-sent events itself (`SseEvents`, Rust only, since the SDKs
  parse SSE for Python).
- **Ollama:** Python's `chat()` speaks Ollama's native `/api/chat`, Rust's
  speaks its OpenAI-compatible `/v1/chat/completions`. Each stream follows
  its own `chat()`, so the reducer has an `ollama` protocol in Python only,
  and the Rust contract test counts the fixture's `ollama` cases as skipped
  by name rather than silently.
- **A stream that ends early is an error, not a short answer** (house rule 6):
  Anthropic must send `message_stop`, OpenAI a `finish_reason`, Ollama
  `done: true`. An `error` event or object mid-stream is an error too.
  `StreamError` in Python, `ChatError::Stream` in Rust.
- **Stopping:** `on_delta` returning `False` (Python) or
  `ControlFlow::Break` (Rust) closes the stream and returns what arrived,
  with `stop_reason = "cancelled"`. A chat UI needs a Stop button, and adding
  one later would change the signature.
- **Usage:** Anthropic reports input tokens at `message_start` and output
  tokens at `message_delta`; Ollama on its last chunk. OpenAI-compatible
  servers report usage only when asked with `stream_options`, which some
  refuse as an unknown field, so it is sent only to `STREAM_USAGE_PROVIDERS`
  (`openai`, `deepseek`, `ollama`). Usage is read from any chunk that carries
  it; without it the counts are 0. Every completed or stopped stream is
  recorded in the token tracker, as `chat()` records.
- **Not in a stream:** tools (refused, so a call never drops them silently),
  `<think>`-tag splitting and JSON repair (the content is exactly what was
  streamed). Anthropic's reasoning blocks join with a blank line, as
  Python's `chat()` joins them.
- **Retry:** Python's Anthropic and OpenAI SDKs retry opening a stream
  themselves. Rust retries opening it (a transport failure, 429, 500, 502,
  503, 504, 529) up to `STREAM_OPEN_ATTEMPTS` times with exponential backoff
  from `LlmClient::retry_base_delay`, never after the first byte, since the
  caller has already shown those deltas. Rust's `chat()` retries nothing; that
  is filed as an issue, not widened here.
- **Timeouts (Rust):** `ureq` has no idle timeout. A stream gets
  `timeout_seconds` to connect and receive headers, then
  `stream_timeout_seconds` (default 600) for the whole body, not the 45 s
  global timeout that would cut a long answer off.

## Steps

1. Fixture `tests/fixtures/chat_stream.json`: cases per protocol (text,
   reasoning, usage, stop reasons, truncation, error events, a stop after N
   deltas).
2. Python `bmlib/llm/stream.py` (`StreamDelta`, `StreamState`, `StreamError`,
   `run_stream`) and its contract test.
3. Python providers: factor each `chat()`'s request building out, add
   `chat_stream`; `BaseProvider.chat_stream` refuses by default;
   `LLMClient.chat_stream` routes, refuses tools, records usage.
4. Rust `llm/stream.rs` (the same reducer, `run_stream`, `SseEvents`) and the
   contract test.
5. Rust transport: `HttpStream`, `HttpClient::post_json_stream` (refuses by
   default), `UreqClient`'s implementation, `LlmClient::chat_stream`.
6. CHANGELOG, HANDOVER, ROADMAP; gates in both languages; the Rust oracle
   rerun; review.
