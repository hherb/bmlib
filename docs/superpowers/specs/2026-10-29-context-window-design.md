# A model's context window, and running Ollama at a chosen size

_StudyAnalyzer's item 11 (its design §6.2). Python and Rust, same PR, shared
fixtures. Written 2026-10-29 with the maintainer._

## Why

StudyAnalyzer sends the whole paper to the model when the paper, the
conversation and room for the answer fit in the model's context window, and
retrieved passages when they do not. It needs two numbers per model:

- **the maximum**: the most the model allows;
- **the running size**: what a request actually gets.

For a cloud model the two are the same. For a local Ollama model they are
not: Ollama runs every request at a size the server chooses (its
`OLLAMA_CONTEXT_LENGTH`, often 4096 tokens) unless the request names one, and
**drops the start of a longer prompt without an error**. Rust reaches Ollama
through its OpenAI-compatible endpoint, which cannot name a size (Ollama's
docs: *"The OpenAI API does not have a way of setting the context size"*).
Planning with the model's advertised 128k would silently cut most of a
paper, which is the truncation the family's rules forbid.

The maintainer's decisions:

- **The app plans with the size the machine actually runs**, set by the user,
  capped at the model's maximum.
- **An unknown maximum plays safe**: the app uses retrieved passages until the
  user enters a number. So bmlib answers "unknown" and never guesses.

## Goals

1. **`context_window(model)`** on the LLM client in both languages: the
   model's maximum in tokens, or *unknown* (`None`). It is any other app's
   answer too.
2. **A chat request can name its running size** (`num_ctx`), and for Ollama
   it is sent, in `chat()` and `chat_stream()`, in both languages.
3. **Rust talks to Ollama through its native `/api/chat`**, as Python already
   does, because that is the only way to send the size. A side effect: the
   five Ollama cases in `tests/fixtures/chat_stream.json`, Python's alone
   today, run in Rust too.

## Non-goals

- Counting a paper's tokens, and the whole-paper or retrieval decision: both
  are StudyAnalyzer's (plan 2b).
- Clamping the running size to the maximum: the app does that (it holds the
  user's setting). bmlib sends what it is given.
- A Rust `list_models()`, or Rust model metadata beyond the window.
- Changing Python's existing `get_model_metadata()` / `list_models()`
  values. Other apps read them. Their fallbacks (8192 for Ollama, 128k by
  default, 200k for every Anthropic model, which is now stale since the
  Claude 5 family has 1M) are a separate issue to file, not this PR.

## Anti-goals (a failure even if it works)

- A guessed window. Any default, fallback constant or prefix match that turns
  "nobody said" into a number is a defect here.
- An unreachable provider reported as "unknown". Unknown means *nobody
  publishes it*; a failed lookup is an error the caller sees (rule 8).
- Rust and Python answering differently for the same provider response.

## Where the maximum comes from (verified 2026-10-29)

The provider's own model metadata where its API carries the number, else the
known table, else unknown. A failed request is an error; it does not fall
back to the table.

| Provider | Source | Field |
|---|---|---|
| anthropic | `GET /v1/models/{id}` | `max_input_tokens` (since March 2026) |
| gemini | native `GET /v1beta/models/{id}` (not the OpenAI-compatible base URL bmlib chats through) | `inputTokenLimit` |
| mistral | `GET /v1/models/{id}` | `max_context_length` |
| deepseek | `GET /models` (list; pick the id) | `context_window` |
| ollama | `POST /api/show` | `model_info["<arch>.context_length"]` (Python's `_context_from_model_info` already reads it) |
| openai | none: the model object has no window | the known table |

For Ollama the maximum is exactly `model_info[general.architecture +
".context_length"]`, otherwise unknown. *My call*, from "never guess":
Python's `_context_from_model_info` also takes any `*.context_length`, any
integer key containing "context", and `*.rope.scaling.original_context_length`.
Those loose readings stay in `get_model_metadata()` (a non-goal to change) and
are not used here. Never `parameters.num_ctx` or the Modelfile, which are a
default running size, and never Python's 8192 fallback.

**Every number is treated as a total** (input and answer together), though
Anthropic's and Gemini's count input only. Planning the answer's room inside
an input-only limit wastes a little and never overflows. One rule, no
per-provider flag. *My call.*

## The known table

- Only providers whose API does not publish a window: OpenAI today.
- **Exact model ids only.** No prefix or pattern matching: `gpt-4o` does not
  answer `gpt-4o-2024-08-06`. A dated id missing from the table is unknown,
  and the app plays safe. *My call*, from "never guess".
- Each row cites the provider page and the date it was read. A model whose
  window cannot be sourced is left out.
- One table for both languages, with a test in each that fails when the two
  differ (the `pricing.rs` precedent).

## The running size

- Rust `ChatRequest` gains `num_ctx: Option<u32>`; Python's `chat()` and
  `chat_stream()` take `num_ctx: int | None = None`.
- Ollama sends it as `options.num_ctx`. **Other providers ignore it**, as the
  design says the setting only lowers what the app plans with for a cloud
  model. *My call*: documented on the field, not an error.
- `None` sends nothing (the server's default), today's behaviour.
- Rust merges `extra_body` into the request; on the native Ollama path an
  `extra_body["options"]` is merged field by field and `num_ctx` is set last,
  so it cannot be silently replaced. *My call.*
- Adding a field to `ChatRequest` and a `Protocol` variant breaks Rust code
  that builds them by literal: say so in the CHANGELOG.

## Rust's native Ollama

- `chat()` posts to `{base}/api/chat` with `stream: false`; `chat_stream()`
  with `stream: true` and reads **newline-delimited JSON**, not SSE.
- The request body, the response mapping (content, thinking, tool calls,
  token counts, stop reason) and the stream events follow Python's Ollama
  provider (`_request_kwargs`, `chat()`, the stream path), which is the
  oracle. Tools keep working.
- The streamed events feed the existing `StreamState`; the five `ollama`
  cases of `chat_stream.json` stop being Python-only.
- The opening of a stream is retried as today; the body never.

## Errors and retries

- The lookup is a network call: retried with exponential backoff (rule 7),
  then an error that says which provider and model, never `None`.
- **A model the provider does not know is an error of its own kind** ("no
  such model"), not unknown and not retried: a 404 from Anthropic, Mistral or
  Gemini, Ollama's "model not found" from `/api/show`, a DeepSeek id absent
  from its list. A mistyped model should be told to the user at once, not
  quietly put the app in retrieval mode. *My call.*
- A provider that knows the model but omits the field (an Ollama model
  without its architecture's `context_length`) is unknown, logged at INFO
  with the reason.
- A field that is present but not a positive integer (`true`, `0`, a string)
  is refused as unknown and logged at WARNING. `bool` is not a count (the
  #313 lesson).

## Shared fixtures

- `tests/fixtures/context_window.json`: per provider, raw metadata responses
  and the answer both languages must give (a number, unknown, or an error
  kind), including the refused shapes above.
- The table itself, and the parity test.
- Ollama request bodies for `chat()` and `chat_stream()` with and without
  `num_ctx`, so the two languages build the same body.

## Left to the builder

- The names of the error types, and whether the lookup is memoised per
  client.
- How Python reads `max_input_tokens` when the pinned `anthropic` SDK
  predates the typed field (`model_extra` / `to_dict()`), and how Gemini's
  native endpoint is reached from each language.
- The NDJSON reader's place in Rust's `HttpClient` (`post_json_stream` reads
  SSE today).
- The table's file format and location, and its initial OpenAI rows.

## How StudyAnalyzer uses it (for the 2b plan, not built here)

The maximum caps the user's setting; for Ollama the setting is sent as
`num_ctx` on every request; the fit test uses `min(setting, maximum)`. An
unknown maximum means retrieval mode until the user enters a number, which
is then used as both the setting and the cap.
