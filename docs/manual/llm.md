# bmlib.llm — LLM Abstraction Layer

Unified interface for interacting with large language models across multiple providers. Routes requests to the appropriate provider based on model strings of the form `"provider:model_name"`.

## Installation

```bash
# Anthropic Claude
pip install bmlib[anthropic]

# Ollama (local models)
pip install bmlib[ollama]

# OpenAI (also enables DeepSeek, Mistral, Gemini via OpenAI-compatible base)
pip install bmlib[openai]

# Multiple providers
pip install bmlib[anthropic,ollama,openai]

# Everything
pip install bmlib[all]
```

## Imports

```python
from bmlib.llm import (
    # Client and singletons
    LLMClient,
    get_llm_client,
    reset_llm_client,
    # Data types
    EmbeddingResponse,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    # Token tracking
    TokenTracker,
    get_token_tracker,
    reset_token_tracker,
    # JSON repair
    JSONRepairError,
    extract_and_repair_json,
    repair_json,
    safe_json_loads,
    # Text utilities
    TextChunk,
    TextChunker,
    chunk_text,
    combine_title_and_text,
    get_text_with_priority,
    process_with_map_reduce,
    process_with_rolling_summary,
)
```

---

## Data Types

### `LLMMessage`

```python
@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[LLMToolCall] | None = None
```

A message in an LLM conversation.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `role` | `Literal["system", "user", "assistant", "tool"]` | *(required)* | The role of the message sender. `"tool"` is used to send a tool-call result back to the model. |
| `content` | `str` | *(required)* | The text content of the message. For `role="tool"` messages, a JSON-encoded string of the tool output. |
| `tool_call_id` | `str \| None` | `None` | For `role="tool"` messages, the id of the tool call this message answers. Ignored for other roles. |
| `tool_calls` | `list[LLMToolCall] \| None` | `None` | For `role="assistant"` messages that re-send a previous turn in which the model invoked tools. Ignored for other roles. |

**Example:**

```python
system = LLMMessage(role="system", content="You are a research assistant.")
user = LLMMessage(role="user", content="Summarise this paper.")
```

---

### `LLMResponse`

```python
@dataclass
class LLMResponse:
    content: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    stop_reason: str | None = None
    duration_seconds: float = 0.0
    tool_calls: list[LLMToolCall] | None = None
```

Response from an LLM request.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | `str` | *(required)* | The text response from the model. May be empty when the model emits only tool calls. |
| `model` | `str` | `""` | The model that generated the response. |
| `input_tokens` | `int` | `0` | Number of input tokens consumed. |
| `output_tokens` | `int` | `0` | Number of output tokens generated. |
| `total_tokens` | `int` | `0` | Total tokens used. Auto-computed as `input_tokens + output_tokens` if not set. |
| `stop_reason` | `str \| None` | `None` | Why the model stopped generating (e.g. `"stop"`, `"max_tokens"`; `"tool_use"` for Anthropic or `"tool_calls"` for OpenAI/Ollama when stopped by tool invocation). |
| `duration_seconds` | `float` | `0.0` | Wall-clock time spent in the request. |
| `tool_calls` | `list[LLMToolCall] \| None` | `None` | Tool invocations the model emitted, or `None` if it called no tool. |

---

### `LLMToolDefinition`

```python
@dataclass
class LLMToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
```

Definition of a tool the model can call. Follows the OpenAI function-calling JSON Schema format; providers that need a different shape (e.g. Anthropic's `input_schema`) convert internally.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | *(required)* | Canonical tool name. Must be unique within a tool list and match `[a-zA-Z0-9_-]{1,64}`. |
| `description` | `str` | *(required)* | What the tool does. The model reads this to decide when to call it. |
| `parameters` | `dict[str, Any]` | `{}` | JSON Schema describing the tool's parameters (`{"type": "object", "properties": {...}, "required": [...]}`). |

---

### `LLMToolCall`

```python
@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
```

A tool invocation emitted by the model, returned in `LLMResponse.tool_calls`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | *(required)* | Provider-assigned unique id. Echo it back as `tool_call_id` in the subsequent `role="tool"` message. |
| `name` | `str` | *(required)* | Name of the tool being invoked; matches one of the names passed in `tools=`. |
| `arguments` | `dict[str, Any]` | `{}` | Arguments parsed from the model's JSON. Validate against the tool's schema before executing. |

---

### `EmbeddingResponse`

```python
@dataclass
class EmbeddingResponse:
    embedding: list[float]
    model: str = ""
    dimensions: int = 0
    input_tokens: int = 0
```

Response from an embedding request.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `embedding` | `list[float]` | *(required)* | The embedding vector. |
| `model` | `str` | `""` | The model that generated the embedding. |
| `dimensions` | `int` | `0` | Number of dimensions in the vector. |
| `input_tokens` | `int` | `0` | Number of input tokens processed. |

---

## LLMClient

The central class for all LLM interactions. Automatically routes requests to the correct provider based on the model string.

### Constructor

```python
class LLMClient:
    def __init__(
        self,
        default_provider: str = "anthropic",
        ollama_host: str | None = None,
        anthropic_api_key: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `default_provider` | `str` | `"anthropic"` | Provider to use when no `"provider:"` prefix is in the model string. |
| `ollama_host` | `str \| None` | `None` | Ollama server URL. Defaults to `OLLAMA_HOST` env var or `http://localhost:11434`. |
| `anthropic_api_key` | `str \| None` | `None` | Anthropic API key. Defaults to `ANTHROPIC_API_KEY` env var. |
| `api_key` | `str \| None` | `None` | Generic API key used by OpenAI-compatible providers (OpenAI, DeepSeek, Mistral, Gemini). Each provider also checks its own env var (e.g. `OPENAI_API_KEY`). |
| `base_url` | `str \| None` | `None` | Override the base URL for OpenAI-compatible providers. Each provider has its own default. |

---

### Model String Format

Model strings use the format `"provider:model_name"`:

```
"anthropic:claude-sonnet-4-20250514"
"openai:gpt-4o"
"ollama:medgemma4B_it_q8"
"deepseek:deepseek-chat"
"mistral:mistral-large-latest"
"gemini:gemini-2.0-flash"
```

If no provider prefix is given, `default_provider` is used. If no model is specified at all, the provider's default model is used.

---

### `LLMClient.chat`

```python
def chat(
    self,
    messages: list[LLMMessage],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    top_p: float | None = None,
    json_mode: bool = False,
    tools: list[LLMToolDefinition] | None = None,
    tool_choice: str = "auto",
    **kwargs: object,
) -> LLMResponse
```

Send a chat request, routing to the appropriate provider. Token usage is automatically tracked via the global `TokenTracker`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `messages` | `list[LLMMessage]` | *(required)* | The conversation messages. |
| `model` | `str \| None` | `None` | Model string (e.g. `"ollama:medgemma4B_it_q8"`). Uses default if `None`. |
| `temperature` | `float` | `0.7` | Sampling temperature (0.0 = deterministic, 1.0 = creative). |
| `max_tokens` | `int` | `4096` | Maximum tokens to generate. |
| `top_p` | `float \| None` | `None` | Nucleus sampling parameter. |
| `json_mode` | `bool` | `False` | Request JSON output. For Anthropic, extracts JSON from code blocks if needed. For Ollama, uses native `format="json"`. |
| `tools` | `list[LLMToolDefinition] \| None` | `None` | Tool definitions the model may call. See [Tool Calling](#tool-calling). |
| `tool_choice` | `str` | `"auto"` | Tool selection strategy: `"auto"` (model decides), `"required"` / `"any"` (must call at least one tool), `"none"` (disable tools for this turn). |
| `**kwargs` | `object` | | Provider-specific options. Ollama supports `think=True` for thinking mode. |

**Returns:** `LLMResponse` with the model's response content and token usage.

**Raises:** `NotImplementedError` if `tools` is provided but the resolved provider does not support tool calling.

**Example:**

```python
client = LLMClient(default_provider="ollama")

# Simple chat
response = client.chat(
    messages=[LLMMessage(role="user", content="What is apoptosis?")],
    model="ollama:medgemma4B_it_q8",
)
print(response.content)

# JSON mode with Anthropic
response = client.chat(
    messages=[
        LLMMessage(role="system", content="Classify the study design. Return JSON."),
        LLMMessage(role="user", content="Title: A Randomized Controlled Trial of..."),
    ],
    model="anthropic:claude-3-haiku-20240307",
    json_mode=True,
    temperature=0.1,
)

# Ollama with thinking mode
response = client.chat(
    messages=[LLMMessage(role="user", content="Complex reasoning task...")],
    model="ollama:deepseek-r1:8b",
    think=True,
)
```

---

### Tool Calling

Pass `tools=` to `chat()` to let the model invoke functions you define. When the model decides to call a tool, `LLMResponse.tool_calls` contains parsed `LLMToolCall` objects (and `stop_reason` is `"tool_use"` / `"tool_calls"` depending on provider). To continue the conversation, append the assistant turn (with its `tool_calls`) and a `role="tool"` message carrying the JSON-encoded result, then re-send the message list.

Supported by the built-in providers `anthropic`, `openai`, `deepseek`, `mistral`, `gemini`, and `ollama` (via an internal allowlist). Passing `tools` to any other provider — including custom OpenAI-compatible providers registered under other names — raises `NotImplementedError` before any network call.

**Example:**

```python
import json

tools = [
    LLMToolDefinition(
        name="add",
        description="Add two integers and return the sum",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
]

messages = [LLMMessage(role="user", content="What is 2 + 3?")]
response = client.chat(messages=messages, model="openai:gpt-4o", tools=tools)

if response.tool_calls:
    call = response.tool_calls[0]
    result = call.arguments["a"] + call.arguments["b"]
    messages.append(
        LLMMessage(role="assistant", content=response.content, tool_calls=response.tool_calls)
    )
    messages.append(
        LLMMessage(role="tool", content=json.dumps({"sum": result}), tool_call_id=call.id)
    )
    final = client.chat(messages=messages, model="openai:gpt-4o", tools=tools)
    print(final.content)
```

---

### `LLMClient.generate`

```python
def generate(
    self,
    prompt: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    **kwargs: object,
) -> LLMResponse
```

Convenience wrapper: wraps `prompt` as a single user message and delegates to `chat()`.

---

### `LLMClient.embed`

```python
def embed(
    self,
    text: str,
    model: str | None = None,
    **kwargs: object,
) -> EmbeddingResponse
```

Generate an embedding vector for `text`, routing to the appropriate provider based on the model string.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | *(required)* | The text to embed. |
| `model` | `str \| None` | `None` | Model string (`"provider:model_name"` format). Defaults to the default provider's default model — pass an embedding-specific model explicitly. |
| `**kwargs` | `object` | | Extra provider-specific arguments. |

**Returns:** `EmbeddingResponse` with the vector, model, dimensions, and input token count.

**Raises:** `NotImplementedError` for providers without embedding support. Of the built-in providers, only Ollama implements `embed()`.

**Example:**

```python
client = LLMClient(default_provider="ollama")
resp = client.embed("Myocardial infarction", model="ollama:nomic-embed-text")
print(resp.dimensions, resp.embedding[:3])
```

---

### `LLMClient.test_connection`

```python
def test_connection(
    self, provider: str | None = None,
) -> bool | dict[str, tuple[bool, str]]
```

Test connectivity to one or all providers.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str \| None` | `None` | Test a specific provider. If `None`, tests all registered providers. |

**Returns:**
- If `provider` is given: `bool` (True if connected).
- If `provider` is `None`: `dict[str, tuple[bool, str]]` mapping provider names to `(success, message)` tuples.

**Example:**

```python
# Test one provider
if client.test_connection("ollama"):
    print("Ollama is available")

# Test all providers
results = client.test_connection()
for name, (ok, msg) in results.items():
    print(f"{name}: {'OK' if ok else 'FAILED'} — {msg}")
```

---

### `LLMClient.list_models`

```python
def list_models(
    self, provider: str | None = None,
) -> list[str] | list[ModelMetadata]
```

List available models for one or all providers.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | `str \| None` | `None` | List models for a specific provider. If `None`, lists all. |

**Returns:**
- If `provider` is given: `list[str]` of model IDs.
- If `provider` is `None`: `list[ModelMetadata]` with full metadata.

---

### `LLMClient.get_model_metadata`

```python
def get_model_metadata(
    self, model: str, provider: str | None = None,
) -> ModelMetadata | None
```

Return metadata for a specific model, or `None` if unavailable.

---

### `LLMClient.get_provider_info`

```python
def get_provider_info(self, provider: str) -> dict[str, object]
```

Return a dictionary of provider metadata including name, URLs, and configuration.

**Returned keys:** `name`, `display_name`, `description`, `website_url`, `setup_instructions`, `is_local`, `is_free`, `requires_api_key`, `api_key_env_var`, `default_base_url`, `default_model`.

---

## Global Singleton

### `get_llm_client`

```python
def get_llm_client() -> LLMClient
```

Return the global `LLMClient` singleton, created on first call with default settings.

### `reset_llm_client`

```python
def reset_llm_client() -> None
```

Discard the global singleton so it is re-created on next use.

---

## Token Tracking

### `TokenTracker`

Thread-safe tracker that records token usage and estimated costs across all LLM calls.

#### `TokenTracker.record_usage`

```python
def record_usage(
    self,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost: float = 0.0,
) -> None
```

Record token usage for a single LLM call. Called automatically by `LLMClient.chat()`.

#### `TokenTracker.get_summary`

```python
def get_summary(self) -> TokenUsageSummary
```

Return an aggregate summary of all recorded usage.

**`TokenUsageSummary` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `total_input_tokens` | `int` | Total input tokens across all calls. |
| `total_output_tokens` | `int` | Total output tokens across all calls. |
| `total_tokens` | `int` | Sum of input + output. |
| `total_cost_usd` | `float` | Estimated total cost in USD. |
| `call_count` | `int` | Number of LLM calls made. |
| `by_model` | `dict[str, dict]` | Per-model breakdown with keys: `input_tokens`, `output_tokens`, `cost_usd`, `calls`. |

#### `TokenTracker.reset`

```python
def reset(self) -> None
```

Clear all recorded usage.

#### `TokenTracker.get_recent_records`

```python
def get_recent_records(self, count: int = 10) -> list[TokenUsageRecord]
```

Return the most recent usage records.

**`TokenUsageRecord` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `model` | `str` | Model string (e.g. `"anthropic:claude-3-haiku-20240307"`). |
| `input_tokens` | `int` | Input tokens for this call. |
| `output_tokens` | `int` | Output tokens for this call. |
| `timestamp` | `datetime` | When the call was made (UTC). |
| `cost_usd` | `float` | Estimated cost for this call. |

### `get_token_tracker`

```python
def get_token_tracker() -> TokenTracker
```

Return the global `TokenTracker` singleton.

### `reset_token_tracker`

```python
def reset_token_tracker() -> None
```

Replace the global `TokenTracker` with a fresh instance.

**Example:**

```python
from bmlib.llm import get_token_tracker

tracker = get_token_tracker()
summary = tracker.get_summary()
print(f"Total calls: {summary.call_count}")
print(f"Total cost: ${summary.total_cost_usd:.4f}")
for model, stats in summary.by_model.items():
    print(f"  {model}: {stats['calls']} calls, ${stats['cost_usd']:.4f}")
```

---

## JSON Repair

`bmlib.llm.json_repair` fixes the predictable syntax errors in LLM-emitted JSON before parsing: single quotes instead of double quotes, trailing commas, missing commas between elements, unescaped newlines/tabs/control characters inside strings, truncated output (missing closing brackets), and unquoted JavaScript-style keys. `BaseAgent.parse_json()` uses it as a last-resort fallback after direct parsing and extraction fail.

Module constants: `MAX_REPAIR_ATTEMPTS = 3`, `MAX_JSON_LENGTH = 1_000_000` (1 MB input limit).

### `repair_json`

```python
def repair_json(json_str: str, max_attempts: int = MAX_REPAIR_ATTEMPTS) -> str
```

Repair malformed JSON, returning a valid JSON string. Valid input is returned unchanged.

**Raises:** `JSONRepairError` if the JSON cannot be repaired after `max_attempts` iterations; `ValueError` if the input is empty or exceeds `MAX_JSON_LENGTH`.

### `safe_json_loads`

```python
def safe_json_loads(
    json_str: str, repair: bool = True, max_attempts: int = MAX_REPAIR_ATTEMPTS
) -> Any
```

Parse JSON, attempting repair on parse failure when `repair` is true. Returns the parsed data (dict, list, or primitive).

**Raises:** `ValueError` if the JSON cannot be parsed even after repair.

### `extract_and_repair_json`

```python
def extract_and_repair_json(response: str, repair: bool = True) -> tuple[str, bool]
```

Extract a JSON string from a raw LLM response — pure JSON, JSON in markdown code blocks, or JSON embedded in prose — and optionally repair it. Returns `(extracted_json_string, was_repaired)`.

**Raises:** `ValueError` if no JSON can be found in `response` or the extracted JSON cannot be parsed.

**Example:**

```python
from bmlib.llm import safe_json_loads

data = safe_json_loads("{'design': 'RCT', 'blinded': true,}")
# {'design': 'RCT', 'blinded': True}
```

---

## Text Utilities

`bmlib.llm.text_utils` splits documents that exceed a model's context window into overlapping chunks and drives map-reduce / rolling-summary processing over them. The chunker is boundary-aware by default: it prefers to end chunks on paragraph or sentence breaks and never discards text — every character of the input appears in at least one chunk.

Module constants (characters): `DEFAULT_CHUNK_SIZE = 10000`, `DEFAULT_CHUNK_OVERLAP = 250`, `DEFAULT_MIN_CHUNK_SIZE = 500`.

### `TextChunk`

```python
@dataclass
class TextChunk:
    content: str
    start_pos: int
    end_pos: int
    chunk_index: int
    total_chunks: int
```

A chunk of text with positional metadata. `start_pos` is inclusive, `end_pos` exclusive, both offsets into the source text. The `size` property returns `len(content)`.

### `TextChunker`

```python
class TextChunker:
    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
        *,
        boundary_aware: bool = True,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
    ) -> None
```

Sliding-window chunker with optional boundary awareness.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chunk_size` | `int` | `10000` | Maximum size of each chunk in characters. |
| `overlap` | `int` | `250` | Characters of overlap between consecutive chunks. |
| `boundary_aware` | `bool` | `True` | Prefer paragraph/sentence breaks over hard cuts. |
| `min_chunk_size` | `int` | `500` | Minimum offset a boundary break may occur at, to avoid tiny leading chunks. |

**Raises:** `ValueError` if `chunk_size <= 0`, `overlap < 0`, or `overlap >= chunk_size`.

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `chunk_text(text)` | `list[TextChunk]` | Split `text` into overlapping chunks. |
| `get_chunk_info(text)` | `dict[str, Any]` | Chunk `text` and summarise the result (`text_length`, `num_chunks`, `chunk_size`, `overlap`, `avg_chunk_size`, `last_chunk_size`). |

### `chunk_text`

```python
def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    *,
    boundary_aware: bool = True,
    min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
) -> list[TextChunk]
```

Convenience wrapper: chunk `text` using a one-off `TextChunker`.

### `process_with_map_reduce`

```python
def process_with_map_reduce(
    text: str,
    map_fn: Callable[[str], Any],
    reduce_fn: Callable[[list[Any]], Any],
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Any
```

Map each chunk to an intermediate result with `map_fn`, then combine the list of intermediates with `reduce_fn`. Text shorter than `max_chunk_size` bypasses chunking and is passed straight to `map_fn`.

### `process_with_rolling_summary`

```python
def process_with_rolling_summary(
    text: str,
    process_fn: Callable[[str, str | None], tuple[Any, str]],
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
    summary_max_length: int = 500,
) -> Any
```

Process each chunk with the previous chunk's summary as context. `process_fn` has the shape `(chunk, previous_summary) -> (result, new_summary)`; the carried summary is truncated to `summary_max_length`. Returns the result from the final chunk. Short text bypasses chunking (called with `None` context).

### `get_text_with_priority`

```python
def get_text_with_priority(
    document: dict[str, Any],
    prefer_full_text: bool = True,
) -> tuple[str, str]
```

Return the best available text from a document mapping and the field it came from. Checks `full_text`, `abstract`, `content`, and `text` in priority order; never truncates. Returns `("", "none")` if no text is found.

### `combine_title_and_text`

```python
def combine_title_and_text(title: str, text: str, max_title_length: int = 500) -> str
```

Prefix `text` with `title` for analysis: `"Title: <title>\n\n<text>"`, or whichever part is present. The title is truncated at `max_title_length`.

**Example:**

```python
from bmlib.llm import chunk_text, process_with_map_reduce

# Boundary-aware chunking
for chunk in chunk_text(long_document, chunk_size=10000, overlap=250):
    print(chunk.chunk_index, chunk.total_chunks, chunk.size)

# Map-reduce summarisation
summary = process_with_map_reduce(
    long_document,
    map_fn=lambda chunk: summarise(chunk),
    reduce_fn=lambda parts: combine(parts),
)
```

---

## Provider Details

### Anthropic

| Property | Value |
|----------|-------|
| Provider name | `anthropic` |
| Default model | `claude-sonnet-4-20250514` |
| API key env var | `ANTHROPIC_API_KEY` |
| Base URL | `https://api.anthropic.com` |
| Is local | No |
| Is free | No |
| System messages | Separated per Anthropic API requirement |

**Known model pricing (per million tokens):**

| Model | Input | Output | Context |
|-------|-------|--------|---------|
| `claude-opus-4-20250514` | $15.00 | $75.00 | 200k |
| `claude-sonnet-4-20250514` | $3.00 | $15.00 | 200k |
| `claude-sonnet-4-5-20250929` | $3.00 | $15.00 | 200k |
| `claude-3-5-haiku-20241022` | $1.00 | $5.00 | 200k |
| `claude-3-haiku-20240307` | $0.25 | $1.25 | 200k |

### OpenAI

| Property | Value |
|----------|-------|
| Provider name | `openai` |
| Default model | `gpt-4o` |
| API key env var | `OPENAI_API_KEY` |
| Base URL | `https://api.openai.com/v1` |
| Is local | No |
| Is free | No |

**Known model pricing (per million tokens):**

| Model | Input | Output | Context |
|-------|-------|--------|---------|
| `gpt-4o` | $2.50 | $10.00 | 128k |
| `gpt-4o-mini` | $0.15 | $0.60 | 128k |
| `gpt-4-turbo` | $10.00 | $30.00 | 128k |
| `o1` | $15.00 | $60.00 | 200k |
| `o1-mini` | $3.00 | $12.00 | 128k |
| `o3-mini` | $1.10 | $4.40 | 200k |

### DeepSeek

| Property | Value |
|----------|-------|
| Provider name | `deepseek` |
| Default model | `deepseek-chat` |
| API key env var | `DEEPSEEK_API_KEY` |
| Base URL | `https://api.deepseek.com` |
| Is local | No |
| Is free | No |

**Known model pricing (per million tokens):**

| Model | Input | Output | Context |
|-------|-------|--------|---------|
| `deepseek-chat` (V3) | $0.27 | $1.10 | 64k |
| `deepseek-reasoner` (R1) | $0.55 | $2.19 | 64k |

### Mistral

| Property | Value |
|----------|-------|
| Provider name | `mistral` |
| Default model | `mistral-large-latest` |
| API key env var | `MISTRAL_API_KEY` |
| Base URL | `https://api.mistral.ai/v1` |
| Is local | No |
| Is free | No |

**Known model pricing (per million tokens):**

| Model | Input | Output | Context |
|-------|-------|--------|---------|
| `mistral-large-latest` | $2.00 | $6.00 | 128k |
| `mistral-small-latest` | $0.10 | $0.30 | 128k |
| `codestral-latest` | $0.30 | $0.90 | 256k |
| `ministral-8b-latest` | $0.10 | $0.10 | 128k |
| `pixtral-large-latest` | $2.00 | $6.00 | 128k |

### Gemini

| Property | Value |
|----------|-------|
| Provider name | `gemini` |
| Default model | `gemini-2.0-flash` |
| API key env var | `GEMINI_API_KEY` |
| Base URL | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Is local | No |
| Is free | No |

**Known model pricing (per million tokens):**

| Model | Input | Output | Context |
|-------|-------|--------|---------|
| `gemini-2.5-pro-preview-05-06` | $1.25 | $10.00 | 1M |
| `gemini-2.5-flash-preview-05-20` | $0.15 | $0.60 | 1M |
| `gemini-2.0-flash` | $0.10 | $0.40 | 1M |
| `gemini-2.0-flash-lite` | $0.00 | $0.00 | 1M |
| `gemini-1.5-pro` | $1.25 | $5.00 | 2M |
| `gemini-1.5-flash` | $0.075 | $0.30 | 1M |

### Ollama

| Property | Value |
|----------|-------|
| Provider name | `ollama` |
| Default model | `medgemma4B_it_q8` |
| Host env var | `OLLAMA_HOST` |
| Default URL | `http://localhost:11434` |
| Is local | Yes |
| Is free | Yes |
| Extra kwargs | `think=True` for thinking mode |
| Embeddings | Yes — the only built-in provider implementing `embed()` |

---

## Custom Providers

New providers can be registered at runtime:

```python
from bmlib.llm.providers import register_provider, BaseProvider

class MyProvider(BaseProvider):
    PROVIDER_NAME = "mycloud"
    # ... implement abstract methods ...

register_provider("mycloud", MyProvider)
```

### `BaseProvider` Abstract Interface

All providers must implement:

| Method / Property | Type | Description |
|-------------------|------|-------------|
| `is_local` | `property -> bool` | Whether the provider runs locally. |
| `is_free` | `property -> bool` | Whether usage is free. |
| `requires_api_key` | `property -> bool` | Whether an API key is needed. |
| `default_base_url` | `property -> str` | Default API URL. |
| `default_model` | `property -> str` | Default model ID. |
| `chat(messages, model, temperature, max_tokens, **kwargs)` | `method -> LLMResponse` | Send a chat request. |
| `list_models(force_refresh=False)` | `method -> list[ModelMetadata]` | List available models. |
| `test_connection()` | `method -> tuple[bool, str]` | Test connectivity. |
| `count_tokens(text, model)` | `method -> int` | Count tokens in text. |

Optional overrides (non-abstract, with base implementations):

| Method | Type | Description |
|--------|------|-------------|
| `embed(text, model, **kwargs)` | `method -> EmbeddingResponse` | Generate an embedding. The base implementation raises `NotImplementedError`. |
| `get_model_pricing(model)` | `method -> ModelPricing` | Pricing lookup used by `calculate_cost()`. Base returns zero cost. |

### `ModelMetadata`

```python
@dataclass
class ModelMetadata:
    model_id: str
    display_name: str
    context_window: int
    pricing: ModelPricing
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    is_deprecated: bool = False
```

### `ModelPricing`

```python
@dataclass
class ModelPricing:
    input_cost: float = 0.0   # USD per million tokens
    output_cost: float = 0.0  # USD per million tokens
```

### `ProviderCapabilities`

```python
@dataclass
class ProviderCapabilities:
    supports_streaming: bool = False
    supports_function_calling: bool = False
    supports_vision: bool = False
    supports_system_messages: bool = True
    max_context_window: int = 128_000
```
