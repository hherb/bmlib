# A model's context window, and Ollama at a chosen size — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `context_window(model)` in Python and Rust (the model's maximum, or
unknown), a `num_ctx` running size that Ollama receives, and Rust speaking
Ollama's native `/api/chat` so it can send it (StudyAnalyzer item 11).

**Architecture:** A pure reader per provider turns a raw metadata body into
`int | None` (or "no such model"); a thin transport per provider fetches that
body, retrying with backoff. Both languages hold the same known table in code,
pinned to the shared fixture. Rust gains `Protocol::Ollama` with Python's
Ollama provider as the oracle for the request body, the response mapping and
the stream.

**Tech Stack:** Python 3 (anthropic, openai, ollama SDKs, httpx), Rust
(`serde_json`, bmlib's `HttpClient` over ureq).

**Spec:** `docs/superpowers/specs/2026-10-29-context-window-design.md`. Read
it first; its *My call* items are settled.

## Global Constraints

- Python and Rust land in **one PR**, each answer held to a shared fixture.
- **Never guess a window**: no default, fallback constant or prefix match turns
  "nobody said" into a number. Unknown is `None`.
- A count is a JSON integer > 0 that is not a boolean; anything else is
  unknown, logged at WARNING (Python; Rust has no logger, so says nothing).
- A model the provider does not know is "no such model" (Python
  `NoSuchModelError`, Rust `ContextWindowError::NoSuchModel`), never retried.
- A failed lookup is an error after retries, never `None`.
- `num_ctx` reaches Ollama only, as `options.num_ctx`; other providers ignore
  it. `None` sends nothing.
- Gates: from `~/src/bmlib`, `uv run pytest tests/ -v`, `ruff check .`,
  `ruff format --check .`; from `~/src/bmlib/rust`, `cargo test`,
  `cargo clippy --all-targets --all-features -- -D warnings`,
  `cargo fmt --check`, `RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --all-features`.
  Before the PR, `scripts/rerun_rust_oracle.py` (CI runs it).

## Review Focus

1. **A dated or unlisted OpenAI id** (`gpt-6.1-sol-2026-09-01`) is unknown,
   not the base model's window: Task 1's table cases include one.
2. **An Ollama model whose `model_info` has `*.context_length` under another
   architecture's prefix, or only `rope.scaling.original_context_length`** is
   unknown: Task 2's fixture cases.
3. **A Rust caller passing `extra_body: {"options": {"num_ctx": 2048}}` beside
   `num_ctx: 32768`** sends 32768 and keeps the other option fields: Task 5.
4. **An Ollama stream that ends without `done: true`** is an error in Rust as
   in Python, not a short answer: Task 6, via the `chat_stream.json` cases.
5. **An unreachable Ollama or a 500 from Anthropic** comes back as an error
   after the retries, not unknown: Task 3's and Task 4's transport tests.

---

### Task 1: The known table, in both languages

**Files:**
- Create: `bmlib/llm/context_window.py`
- Create: `rust/bmlib/src/llm/context_window.rs` (add `pub mod context_window;`
  and re-exports in `rust/bmlib/src/llm/mod.rs`)
- Create: `tests/fixtures/context_window.json`
- Test: `tests/test_context_window.py`, `rust/bmlib/tests/llm_context_window.rs`

**Interfaces:**
- Produces (Python): `KNOWN_CONTEXT_WINDOWS: Mapping[tuple[str, str], int]`
  keyed `(provider, model)`; `known_context_window(provider: str, model: str) -> int | None`.
- Produces (Rust): `KNOWN_CONTEXT_WINDOWS: &[(&str, &str, u64)]`;
  `pub fn known_context_window(provider: &str, model: &str) -> Option<u64>`.
- Fixture shape (later tasks add to it):

```json
{
  "table": [{"provider": "openai", "model": "gpt-6.1-sol", "window": 1050000,
             "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol", "read": "2026-10-29"}],
  "table_lookups": [{"name": "...", "provider": "openai", "model": "...", "expect": {"window": 1050000} | {"unknown": true}}],
  "metadata": []
}
```

- [ ] **Step 1: Source the rows.** One row per OpenAI model on
  `https://developers.openai.com/api/docs/models` that shows a context window,
  plus the Rust registry's default `gpt-4o-mini` if its page still exists.
  Fetch each model's page for the exact count (the index rounds: "1.05M" is
  1050000). Record `source` and `read`. A model without an exact figure is
  left out.
- [ ] **Step 2: Write the failing tests.** In both languages:
  `test_table_matches_the_fixture` (the code table equals the fixture's
  `table` rows, both ways: same set of `(provider, model, window)`) and
  `test_table_lookups` driven by `table_lookups`, which must include: each
  row's exact id → its window; the same id with a date suffix → unknown; the
  id upper-cased → unknown; an Anthropic id → unknown (the table holds OpenAI
  only).
- [ ] **Step 3: Run them; expect failures** (module missing).
- [ ] **Step 4: Implement** the table and `known_context_window` (exact key
  match, nothing else) with doc comments citing the fixture as the record of
  sources.
- [ ] **Step 5: Run both suites; expect PASS.** Commit:
  `feat(llm): the known context-window table, held to a shared fixture`.

### Task 2: Reading a window out of a provider's metadata (pure)

**Files:**
- Modify: `bmlib/llm/context_window.py`, `rust/bmlib/src/llm/context_window.rs`
- Modify: `tests/fixtures/context_window.json` (`metadata` cases)
- Test: the Task 1 test files

**Interfaces:**
- Produces (Python): `class ContextWindowError(RuntimeError)` with
  `provider: str`, `model: str`; `class NoSuchModelError(ContextWindowError)`;
  `read_context_window(provider: str, model: str, body: Mapping[str, Any]) -> int | None`
  (raises `NoSuchModelError`; `ValueError` for a provider without metadata).
- Produces (Rust):

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ContextWindowError {
    UnknownProvider(String),
    MissingApiKey { provider: String, env_var: String },
    NoSuchModel { provider: String, model: String },
    Transport(String),
    Malformed(String),
}
pub fn read_context_window(provider: &str, model: &str, body: &Value)
    -> Result<Option<u64>, ContextWindowError>;
```

Rules (one per provider; `body` is what Task 3's/4's transport fetched):

| provider | body | window |
|---|---|---|
| anthropic | model object | `max_input_tokens` |
| gemini | model object | `inputTokenLimit` |
| mistral | model object | `max_context_length` |
| deepseek | list `{"data": [...]}` | the entry whose `id == model`, its `context_window`; no entry → no such model |
| ollama | `/api/show` body | `model_info[model_info["general.architecture"] + ".context_length"]`; missing architecture or key → unknown |

A missing field is unknown (Python logs INFO with the reason); a present field
that is not a count is unknown (WARNING).

- [ ] **Step 1: Write the fixture cases** (`metadata`: `name`, `provider`,
  `model`, `body`, `expect` one of `{"window": n}`, `{"unknown": true}`,
  `{"error": "no_such_model"}`), at least: each provider's happy path
  (anthropic 1000000, gemini, mistral, deepseek, ollama `llama` 131072); each
  field missing; each field `true`, `0`, `-1`, `"131072"`, `131072.5`;
  deepseek id absent; ollama with `general.architecture` absent; ollama with
  only `mistral3.rope.scaling.original_context_length`; ollama whose
  architecture is `qwen3` but the key present is `llama.context_length`;
  ollama with both the right key and an `original_` key (the right key wins).
- [ ] **Step 2: Write `test_metadata_cases`** in both languages, driving every
  case through `read_context_window`; run, expect FAIL.
- [ ] **Step 3: Implement** `read_context_window` and the count check (Python
  `isinstance(v, int) and not isinstance(v, bool) and v > 0`; Rust
  `Value::as_u64` with `> 0`, which refuses booleans, floats and negatives).
- [ ] **Step 4: Run; expect PASS.** Commit:
  `feat(llm): read a context window from each provider's metadata`.

### Task 3: Python lookup, `num_ctx`, and the client method

**Files:**
- Modify: `bmlib/llm/context_window.py` (retry helper and constants)
- Modify: `bmlib/llm/providers/base.py`, `anthropic.py`, `openai_compat.py`,
  `gemini.py`, `mistral.py`, `deepseek.py`, `ollama.py`, `bmlib/llm/client.py`
- Test: `tests/test_context_window.py`, `tests/test_ollama_provider.py` (or the
  existing Ollama test module)

**Interfaces:**
- Consumes: `read_context_window`, `known_context_window`, the errors (Tasks 1–2).
- Produces: `BaseProvider.context_window(model: str) -> int | None` (default:
  `known_context_window(self.PROVIDER_NAME, model)`, no request);
  `LLMClient.context_window(model: str, provider: str | None = None) -> int | None`
  (routes `"provider:model"` as `get_model_metadata` does, but **propagates**
  errors; its docstring, and Rust's in Task 4, says every number is to be
  read as input and answer together, per the design); constants
  `CONTEXT_LOOKUP_ATTEMPTS = 3`,
  `CONTEXT_LOOKUP_BASE_DELAY_SECONDS = 1.0`.

Transports (each feeds `read_context_window`):
- anthropic: `client.models.retrieve(model)` → dict (`to_dict()`, or
  `model_dump()`); `anthropic.NotFoundError` → `NoSuchModelError`. The SDK
  retries itself. Raise the `anthropic` floor in `pyproject.toml` if the
  pinned one lacks `models.retrieve`; read `max_input_tokens` from the dict,
  not a typed attribute.
- mistral: openai SDK `client.models.retrieve(model).to_dict()`; deepseek:
  `{"data": [m.to_dict() for m in client.models.list()]}`; `openai.NotFoundError`
  → `NoSuchModelError`.
- gemini: `httpx.get(f"{native_base}/models/{model}", headers={"x-goog-api-key": key})`
  where `native_base` is the provider's base URL without its trailing
  `/openai`; 404 → `NoSuchModelError`; retried with the helper.
- ollama: `client.show(model)` → dict; `ollama.ResponseError` with
  `status_code == 404` → `NoSuchModelError`; retried with the helper.
- Any other failure, after retries: `ContextWindowError` naming provider and
  model, chained (`raise … from e`).

`num_ctx`: Ollama's `_request_kwargs` reads `kwargs.get("num_ctx")`; `None`
adds nothing; an `int` > 0 (not `bool`) sets `options["num_ctx"]`; anything
else raises `ValueError`. Document `num_ctx` beside `think` in
`LLMClient.chat`/`chat_stream` and the Ollama `chat` docstrings; other
providers already ignore unknown kwargs (add one test proving it for openai).

- [ ] **Step 1: Write the failing tests** (fakes for the SDK clients, as the
  existing provider tests do): `test_client_context_window_routes_provider_prefix`;
  per provider, a fake body → the reader's answer; a not-found → 
  `NoSuchModelError`; Ollama `ConnectionError` three times → `ContextWindowError`
  after `CONTEXT_LOOKUP_ATTEMPTS` calls (sleep patched); Ollama fails twice then
  answers → the window; `test_openai_context_window_needs_no_request`;
  `test_ollama_num_ctx_reaches_options` (chat and chat_stream);
  `test_ollama_num_ctx_refuses_bool_and_zero`;
  `test_openai_ignores_num_ctx`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the helper `_with_backoff(call, retry_on: tuple[type[BaseException], ...])`
  (delay `base * 2**attempt`), the overrides, `LLMClient.context_window`,
  and `num_ctx`.
- [ ] **Step 4: Run the full Python gate; expect PASS.** Commit:
  `feat(llm): Python context_window() per provider, and Ollama's num_ctx`.

### Task 4: Rust lookup and the client method

**Files:**
- Modify: `rust/bmlib/src/publications/fetchers/registry.rs` (trait method),
  `rust/bmlib/src/http.rs` (`UreqClient`), `rust/bmlib/src/llm/context_window.rs`,
  `rust/bmlib/src/llm/client.rs`
- Test: `rust/bmlib/tests/llm_context_window.rs`

**Interfaces:**
- Produces: `HttpClient::get_with_headers(&self, url: &str, headers: &BTreeMap<String, String>) -> Result<HttpResponse, FetchError>`,
  default refusing with `FetchError::Config` naming the method (as
  `post_json_stream`'s does); implemented on `UreqClient`.
- Produces: `LlmClient::context_window(&self, model: &str) -> Result<Option<u64>, ContextWindowError>`.

Behaviour: parse the model string as `chat` does (a bare provider name means
its default model). `openai` answers from the table with no key and no
request. Otherwise resolve the target (`MissingApiKey`/`UnknownProvider` map
across) and fetch:
- anthropic: GET `{base}/v1/models/{model}`, `chat_headers`'s auth and version;
- mistral: GET `{base}/v1/models/{model}`, Bearer;
- deepseek: GET `{base}/models`, Bearer;
- gemini: GET `{base without /openai}/models/{model}`, header `x-goog-api-key`;
- ollama: POST `{base}/api/show` with `{"model": model}`.
404 → `NoSuchModel`, not retried. A `FetchError::Transport` or a
`RETRYABLE_STATUSES` status is retried up to `STREAM_OPEN_ATTEMPTS` with
`retry_base_delay` doubling (share the loop with `open_stream` rather than
copy it). Another status → `Transport` quoting the server as `http_error`
does. A body that is not JSON → `Malformed`. Then `read_context_window`.
The model id goes into the URL path percent-encoded.

- [ ] **Step 1: Write the failing tests** with a recording fake `HttpClient`:
  each provider's URL, method and headers; 404 → `NoSuchModel` after one call;
  503 twice then 200 → the window after three calls; transport failure every
  time → `Transport` after `STREAM_OPEN_ATTEMPTS` calls; openai → table with
  zero calls and no key; anthropic without a key → `MissingApiKey`; a model id
  containing `/` reaches the path encoded.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the Rust gate; expect PASS.** Commit:
  `feat(llm): Rust context_window() per provider`.

### Task 5: Rust speaks Ollama's native `/api/chat` (non-streamed)

**Files:**
- Modify: `rust/bmlib/src/llm/protocol.rs`, `rust/bmlib/src/llm/client.rs`
- Create: `tests/fixtures/ollama_chat.json`
- Test: `rust/bmlib/tests/llm_ollama_contract.rs`, `tests/test_ollama_contract.py`

**Interfaces:**
- Produces: `Protocol::Ollama` (`chat_path` `/api/chat`, `models_path`
  `/api/tags`, Bearer auth, no extra headers); `provider_specs()`'s `ollama`
  row uses it. `ChatRequest.num_ctx: Option<NonZeroU32>` (default `None`).
  `pub fn messages_to_ollama(messages: &[LLMMessage]) -> Vec<Value>`;
  `pub fn tool_def_to_ollama(tool: &LLMToolDefinition) -> Value`;
  `pub fn think_to_ollama(think: &str) -> Value`;
  `pub fn parse_ollama_response(body: &Value, model: &str, messages: &[LLMMessage]) -> Result<LLMResponse, ProtocolError>`.
  `parse_response` gains a `messages: &[LLMMessage]` parameter (Ollama's
  estimate needs them).

Oracle: `bmlib/llm/providers/ollama.py` — `_request_kwargs`,
`_convert_messages_to_ollama`, `_convert_tool_def_to_ollama`, `chat()`'s
mapping, `_estimate_tokens` (count **code points**: `chars().count()`).
`think`: an integer string → `bool(n != 0)`; `"true"`/`"false"` → that bool;
any other string as is. The body sends `"stream": false` (the native API
streams by default). `extra_body` merges last, except `options`, merged field
by field, after which `num_ctx` is set.

- [ ] **Step 1: Write the fixture** `ollama_chat.json`: `requests` (a request
  in neutral form — messages, temperature, max_tokens, top_p, json_mode,
  tools, think, num_ctx — and the expected body **without** `stream`) and
  `responses` (a native body, the request's messages, and the expected
  content, thinking, tool calls, token counts, stop reason). Cover: plain
  chat; `num_ctx` set and absent; json mode; tools and a tool-call turn;
  arguments as an object and as a JSON string and as non-JSON (`{"_raw": …}`);
  `done_reason: "length"`; counts absent (estimated) and `0` (kept); thinking.
- [ ] **Step 2: Write the tests**: Python drives `_request_kwargs` and
  `chat()` (fake client) through the fixture; Rust drives `chat_body` (assert
  `stream == false`, then compare without it) and `parse_ollama_response`.
  Add the Review Focus 3 case (Rust only: `extra_body` options merged, `num_ctx`
  wins). Run; Rust FAILS, Python passes (it is the oracle).
- [ ] **Step 3: Implement** the Rust side; update `llm_client.rs` tests that
  assumed Ollama on `/v1/chat/completions`.
- [ ] **Step 4: Run both gates; expect PASS.** Commit:
  `feat(llm): Rust chats with Ollama over its native API, with num_ctx`.

### Task 6: Rust streams from Ollama's native API

**Files:**
- Modify: `rust/bmlib/src/llm/stream.rs`, `rust/bmlib/src/llm/client.rs`,
  `rust/bmlib/tests/llm_stream_contract.rs`, `rust/bmlib/tests/llm_chat_stream.rs`

**Interfaces:**
- Consumes: `Protocol::Ollama`, `chat_body` (Task 5).
- Produces: `StreamState`'s Ollama arm (port of Python's `_apply_ollama`; end
  marker `"done"`); `pub fn ndjson_events<R: BufRead>(reader: R) -> impl Iterator<Item = Result<Value, ChatError>>`
  (blank lines skipped; a line that is not JSON is `ChatError::Stream`).
  `chat_stream` reads NDJSON for `Protocol::Ollama`, SSE otherwise.

- [ ] **Step 1: Un-skip** the `ollama` cases in `llm_stream_contract.rs` and
  delete the comment saying they are Python's alone (also in `stream.rs`'s
  module doc and `STREAM_USAGE_PROVIDERS`' doc). Add an `llm_chat_stream.rs`
  test: a fake server streaming three NDJSON lines ending `done: true` →
  deltas and the response; one ending without `done` → `ChatError::Stream`;
  `num_ctx` in the posted body.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run both gates and `scripts/rerun_rust_oracle.py`; expect
  PASS.** Commit: `feat(llm): Rust streams from Ollama's native API`.

### Task 7: Records

**Files:** `CHANGELOG.md`, `ROADMAP.md`, `HANDOVER.md`, `README.md` (the LLM
section, if it lists capabilities).

- [ ] **Step 1:** CHANGELOG under *Unreleased*: *Added* (`context_window`,
  `num_ctx`, `get_with_headers`), *Changed — breaking* (Rust: `ChatRequest`
  field, `Protocol::Ollama`, `parse_response`'s parameter; Ollama now on
  `/api/chat`), and the design's argument in brief.
- [ ] **Step 2:** File the bmlib issue: Python `get_model_metadata()`'s
  fallbacks (Anthropic 200k for every model, now stale; 8192 for Ollama;
  128k default) report guesses as windows. Link it from the CHANGELOG entry.
- [ ] **Step 3:** ROADMAP row and HANDOVER session section per bmlib's
  conventions. Commit: `docs: item 11, context windows`.
