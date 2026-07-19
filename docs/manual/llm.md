# bmlib.llm — LLM Abstraction Layer

Unified interface for interacting with large language models across multiple providers. Routes requests to the appropriate provider based on model strings of the form `"provider:model_name"`.

Beyond plain chat, the module provides tool calling, embeddings, token accounting, JSON repair for malformed model output, and text chunking for documents that exceed a context window.

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
    EmbeddingResponse,
    JSONRepairError,
    LLMClient,
    get_llm_client,
    reset_llm_client,
    # Data types
    EmbeddingResponse,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    TextChunk,
    TextChunker,
    TokenTracker,
    chunk_text,
    combine_title_and_text,
    extract_and_repair_json,
    get_llm_client,
    get_text_with_priority,
    get_token_tracker,
    process_with_map_reduce,
    process_with_rolling_summary,
    repair_json,
    reset_llm_client,
    reset_token_tracker,
    safe_json_loads,
)
```

Provider-level types are **not** re-exported from `bmlib.llm`; import them from the provider subpackage:

```python
from bmlib.llm.providers import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
    get_provider,
    list_providers,
    register_provider,
)
```

`TokenUsageRecord` and `TokenUsageSummary` live in `bmlib.llm.token_tracker`, and `extract_json` in `bmlib.llm.utils`.

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
| `role` | `Literal["system", "user", "assistant", "tool"]` | *(required)* | The role of the message sender. `"tool"` carries a tool-call result back to the model. |
| `content` | `str` | *(required)* | The text content of the message. For `role="tool"` messages this should be a JSON-encoded string of the tool output. May be empty for assistant messages that consist solely of tool calls. |
| `tool_call_id` | `str \| None` | `None` | For `role="tool"` messages, the id of the tool call being answered. Ignored for other roles. |
| `tool_calls` | `list[LLMToolCall] \| None` | `None` | For `role="assistant"` messages that replay a turn in which the model invoked tools. Ignored for other roles. |

**Example:**

```python
system = LLMMessage(role="system", content="You are a research assistant.")
user = LLMMessage(role="user", content="Summarise this paper.")
```

---

### `LLMToolDefinition`

```python
@dataclass
class LLMToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
```

Definition of a tool the model may call. Follows the OpenAI function-calling JSON Schema format; providers needing a different shape (e.g. Anthropic's `input_schema`) convert internally.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | *(required)* | Canonical tool name. Unique within a tool list and matching `[a-zA-Z0-9_-]{1,64}`. |
| `description` | `str` | *(required)* | What the tool does. The model reads this to decide when to call it, so clarity matters. |
| `parameters` | `dict[str, Any]` | `{}` | JSON Schema describing the tool's parameters. |

**Example:**

```python
add_tool = LLMToolDefinition(
    name="add",
    description="Add two integers and return the sum",
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
    },
)
```

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
| `id` | `str` | *(required)* | Provider-assigned id for this call. Echo it back in the following `role="tool"` message's `tool_call_id`. |
| `name` | `str` | *(required)* | Name of the tool being invoked. Matches one of the names passed in `tools=`. |
| `arguments` | `dict[str, Any]` | `{}` | Arguments the model passes to the tool, **already parsed** into a plain dict — do not call `json.loads()` on it. Validate against the tool's schema before executing. |

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
| `total_tokens` | `int` | `0` | Total tokens used. Auto-computed as `input_tokens + output_tokens` if left at `0`. |
| `stop_reason` | `str \| None` | `None` | Why the model stopped generating (e.g. `"stop"`, `"max_tokens"`). Tool-capable providers report `"tool_use"` (Anthropic) or `"tool_calls"` (OpenAI/Ollama) when a tool call caused the stop. |
| `duration_seconds` | `float` | `0.0` | Wall-clock time spent in the request. |
| `tool_calls` | `list[LLMToolCall] \| None` | `None` | Tool invocations the model emitted, or `None` if it called no tool. |

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
| `default_provider` | `str` | `"anthropic"` | Provider to use when no `"provider:"` prefix is in the model string. Normalised to lowercase. |
| `ollama_host` | `str \| None` | `None` | Ollama server URL. Defaults to `OLLAMA_HOST` env var or `http://localhost:11434`. |
| `anthropic_api_key` | `str \| None` | `None` | Anthropic API key. Defaults to `ANTHROPIC_API_KEY` env var. |
| `api_key` | `str \| None` | `None` | Generic API key used by OpenAI-compatible providers (OpenAI, DeepSeek, Mistral, Gemini). Each provider also checks its own env var (e.g. `OPENAI_API_KEY`). |
| `base_url` | `str \| None` | `None` | Override the base URL for OpenAI-compatible providers. Each provider has its own default. |

Provider instances are created lazily on first use and cached for the lifetime of the client.

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
| `json_mode` | `bool` | `False` | Request JSON output. For Anthropic, extracts JSON from code blocks if needed. For Ollama, uses native `format="json"`. For OpenAI-compatible providers, sets `response_format={"type": "json_object"}`. |
| `tools` | `list[LLMToolDefinition] \| None` | `None` | Tool definitions the model may call. See [Tool Calling](#tool-calling). |
| `tool_choice` | `str` | `"auto"` | Tool selection strategy. Ignored when `tools` is `None`. |
| `**kwargs` | `object` | | Provider-specific options. Ollama supports `think=True` for thinking mode. |

**Returns:** `LLMResponse` with the model's response content, any tool calls, and token usage.

**Raises:** `NotImplementedError` if `tools` is not `None` and the resolved provider is not tool-capable. The check happens before any network call.

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

> **Note:** There is no `LLMClient.chat_json()`. JSON-with-retry convenience lives on `BaseAgent.chat_json()` — see [agents.md](agents.md). For raw responses, combine `json_mode=True` with `safe_json_loads()` or `extract_and_repair_json()`.

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

Convenience wrapper that wraps *prompt* as a single user message and delegates to `chat()`.

**Example:**

```python
response = client.generate(
    "List three biomarkers for sepsis.",
    model="anthropic:claude-3-5-haiku-20241022",
)
print(response.content)
```

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

Generate an embedding vector for *text*, routing on the *model* string. See [Embeddings](#embeddings) for provider support and caveats.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | *(required)* | The text to embed. |
| `model` | `str \| None` | `None` | Model string (`"provider:model_name"`). Falls back to the default provider's **chat** default model — almost always wrong for embeddings, so pass an explicit model. |
| `**kwargs` | `object` | | Extra provider-specific arguments. |

**Raises:** `NotImplementedError` for providers that do not implement embeddings (everything except Ollama).

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

Provider `list_models()` results are cached with a TTL, and the Anthropic and OpenAI-compatible providers return a **copy** of the cached list at both the cache-hit and cache-store paths. Mutating what you get back cannot corrupt the cache for later callers, so sorting or filtering the result in place is safe.

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

Return a dictionary of provider metadata: identification, URLs, and configuration.

**Returned keys:** `name`, `display_name`, `description`, `website_url`, `setup_instructions`, `is_local`, `is_free`, `requires_api_key`, `api_key_env_var`, `default_base_url`, `default_model`.

> **Note:** The returned dict contains no `capabilities` key. To inspect model capabilities, use `get_model_metadata(model).capabilities`. To test tool support ahead of time, see [Detecting tool support](#detecting-tool-support).

---

## Tool Calling

Pass `tools` to `chat()` to let the model invoke functions you define. Each `LLMToolDefinition` is translated into the provider's native format. When the model chooses to call a tool, the response carries parsed `LLMToolCall` objects in `LLMResponse.tool_calls`.

### `tool_choice` values

| Value | Behaviour |
|-------|-----------|
| `"auto"` | *(default)* The model decides whether to call a tool. |
| `"required"` / `"any"` | The model must call at least one tool. |
| `"none"` | Tool calling is disabled for this turn. |
| *any other string* | Interpreted as a specific tool name the model is forced to call. |

### Provider support

| Provider | Tool calling | Notes |
|----------|--------------|-------|
| `anthropic` | Yes | `tool_choice="auto"` omits the parameter (Anthropic's own default). |
| `openai` | Yes | |
| `deepseek` | Yes | OpenAI-compatible path. |
| `mistral` | Yes | OpenAI-compatible path. |
| `gemini` | Yes | OpenAI-compatible path. |
| `ollama` | Yes | **`tool_choice` is accepted but discarded** — Ollama's native API has no equivalent parameter, so the model always decides. Ollama does not always assign call ids; bmlib synthesises `call_0`, `call_1`, … from position. |

Passing `tools` to any other provider raises `NotImplementedError` at the client level, before any network round-trip.

### Detecting tool support

Support is gated by a provider-name allowlist in `bmlib.llm.client` (`anthropic`, `openai`, `deepseek`, `mistral`, `gemini`, `ollama`) — **not** by `ProviderCapabilities.supports_function_calling`, which is populated per model and unreliable for this purpose. To probe without raising, use the public `supports_tools()` helper, which checks the same allowlist that gates `chat()`:

```python
from bmlib.llm import supports_tools

supports_tools("ollama")                              # True
supports_tools("anthropic:claude-sonnet-4-20250514")  # full model strings work too
supports_tools("some_custom_provider")                # False
```

### Single-turn example

```python
from bmlib.llm import LLMClient, LLMMessage, LLMToolDefinition

client = LLMClient()

weather_tool = LLMToolDefinition(
    name="get_weather",
    description="Return the current temperature in Celsius for a city",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)

response = client.chat(
    messages=[LLMMessage(role="user", content="What's the weather in Oslo?")],
    model="anthropic:claude-sonnet-4-20250514",
    tools=[weather_tool],
)

for call in response.tool_calls or []:
    print(call.name, call.arguments)   # arguments is already a dict
```

### Multi-turn example

After the model emits tool calls, execute them, then re-send the **whole** conversation with the assistant turn plus one `role="tool"` message per call:

```python
import json

from bmlib.llm import LLMClient, LLMMessage, LLMToolDefinition

client = LLMClient()

add_tool = LLMToolDefinition(
    name="add",
    description="Add two integers and return the sum",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)

TOOLS = {"add": lambda a, b: a + b}

messages = [LLMMessage(role="user", content="What is 17 plus 25?")]

while True:
    response = client.chat(
        messages=messages,
        model="anthropic:claude-sonnet-4-20250514",
        tools=[add_tool],
    )

    if not response.tool_calls:
        print(response.content)
        break

    # 1. Replay the assistant turn, including the tool calls it emitted.
    messages.append(
        LLMMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )
    )

    # 2. Append one tool-result message per call, echoing the call id.
    for call in response.tool_calls:
        result = TOOLS[call.name](**call.arguments)
        messages.append(
            LLMMessage(
                role="tool",
                content=json.dumps({"result": result}),
                tool_call_id=call.id,
            )
        )

    # 3. Loop: the full message list is re-sent on the next iteration.
```

Guard the loop with an iteration cap in production code — a model can keep requesting tools indefinitely.

---

## Embeddings

`LLMClient.embed()` returns an `EmbeddingResponse` for a single string.

> **Warning — provider support is narrow.** `BaseProvider.embed()` is a concrete method that raises `NotImplementedError(f"{PROVIDER_NAME} does not support embeddings")`. **Only Ollama overrides it.** Anthropic, OpenAI, DeepSeek, Mistral, and Gemini all raise.

> **Warning — pass an explicit model.** When `model` is omitted, the provider's *chat* default model is used. For Ollama that is `medgemma4B_it_q8`, a chat model, not an embedding model. Always name an embedding model explicitly.

**Example:**

```python
from bmlib.llm import LLMClient

client = LLMClient(default_provider="ollama")

result = client.embed(
    text="Metformin reduces HbA1c in type 2 diabetes.",
    model="ollama:nomic-embed-text",
)
print(result.dimensions, len(result.embedding))
print(result.input_tokens)
```

Ollama raises `ConnectionError("Ollama embedding failed: ...")` if the server call fails.

> **Note:** Unlike `chat()`, the `embed()` path does **not** record usage with the global `TokenTracker`, even though `EmbeddingResponse.input_tokens` is populated. Track embedding cost yourself if you need it.

---

## Global Singleton

### `get_llm_client`

```python
def get_llm_client() -> LLMClient
```

Return the global `LLMClient` singleton, created on first call with default settings. Thread-safe: concurrent first calls create exactly one client.

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

Record token usage for a single LLM call. Called automatically by `LLMClient.chat()` (and therefore by `generate()`), but not by `embed()`.

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

`bmlib.llm.json_repair` fixes the syntax errors LLMs habitually produce: single-quoted strings, unescaped newlines/tabs/control characters inside strings, trailing commas, missing commas between values, truncated output (unclosed brackets), and unquoted JavaScript-style keys.

**Module constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_REPAIR_ATTEMPTS` | `3` | Default number of repair iterations. |
| `MAX_JSON_LENGTH` | `1_000_000` | Maximum input size in characters (1 MB). |

### `JSONRepairError`

```python
class JSONRepairError(Exception)
```

Raised by `repair_json()` when the input cannot be repaired.

### `repair_json`

```python
def repair_json(json_str: str, max_attempts: int = MAX_REPAIR_ATTEMPTS) -> str
```

Repair a malformed JSON string and return the repaired text. Valid input is returned unchanged.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `json_str` | `str` | *(required)* | Potentially malformed JSON. |
| `max_attempts` | `int` | `3` | Maximum repair iterations; each feeds its output into the next. |

**Raises:** `JSONRepairError` if unrepairable; `ValueError` if the input is empty or exceeds `MAX_JSON_LENGTH`.

### `safe_json_loads`

```python
def safe_json_loads(
    json_str: str,
    repair: bool = True,
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
) -> Any
```

Parse JSON, repairing first if the initial parse fails.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `json_str` | `str` | *(required)* | JSON string to parse. |
| `repair` | `bool` | `True` | Attempt repair on parse failure. |
| `max_attempts` | `int` | `3` | Maximum repair attempts when `repair` is true. |

**Returns:** the parsed data (dict, list, or primitive).

**Raises:** `ValueError` if the JSON cannot be parsed even after repair — `JSONRepairError` is converted into `ValueError`, so callers only need to catch the one exception.

### `extract_and_repair_json`

```python
def extract_and_repair_json(response: str, repair: bool = True) -> tuple[str, bool]
```

Locate JSON inside a raw LLM response and repair it. Extraction tries, in order: a ` ```json ` fenced block, a bare ` ``` ` fenced block whose content starts with `{` or `[`, then the first balanced object or array embedded in prose. If a run of text opens with `{` or `[` but never balances (truncated output) and `repair` is true, everything from the opener onwards is taken and closed by `repair_json()`.

**Returns:** `(json_string, was_repaired)`.

**Raises:** `ValueError` if no JSON can be found, or if the extracted JSON cannot be parsed after repair.

**Example:**

```python
from bmlib.llm import extract_and_repair_json, safe_json_loads

# A fenced block containing single quotes and a trailing comma.
raw = (
    "Here is the classification:\n\n"
    "```json\n"
    "{'design': 'RCT', 'confidence': 0.9,}\n"
    "```\n"
)

json_str, was_repaired = extract_and_repair_json(raw)
data = safe_json_loads(json_str)
print(data["design"], was_repaired)   # RCT True
```

### `bmlib.llm.utils.extract_json`

```python
def extract_json(text: str) -> str
```

The older, narrower extractor. Not exported from `bmlib.llm` — import it from `bmlib.llm.utils`.

| | `extract_json` | `extract_and_repair_json` |
|---|---|---|
| Top-level arrays | Not found | Found |
| Repairs malformed JSON | No | Yes (unless `repair=False`) |
| On failure | Returns the input unchanged | Raises `ValueError` |
| Return value | `str` | `tuple[str, bool]` |

Prefer `extract_and_repair_json()` for new code.

---

## Text Utilities

`bmlib.llm.text_utils` splits documents that exceed a context window and drives processing over the pieces. The chunker is **boundary-aware** by default: chunk ends are pulled back to the nearest paragraph or sentence break, and no character of the input is ever discarded.

> **All sizes are measured in characters, not tokens.**

**Module constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_CHUNK_SIZE` | `10000` | Default maximum chunk size in characters. |
| `DEFAULT_CHUNK_OVERLAP` | `250` | Default overlap between consecutive chunks, in characters. |
| `DEFAULT_MIN_CHUNK_SIZE` | `500` | Minimum offset at which a boundary break may occur. |

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

| Field | Type | Description |
|-------|------|-------------|
| `content` | `str` | The chunk text. |
| `start_pos` | `int` | Start offset (inclusive) in the source text. |
| `end_pos` | `int` | End offset (exclusive) in the source text. |
| `chunk_index` | `int` | Zero-based index of this chunk. |
| `total_chunks` | `int` | Total number of chunks the source was split into. |

**Property:**

| Property | Type | Description |
|----------|------|-------------|
| `size` | `int` | Length of `content` in characters. |

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

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chunk_size` | `int` | `10000` | Maximum size of each chunk in characters. |
| `overlap` | `int` | `250` | Characters of overlap between consecutive chunks. |
| `boundary_aware` | `bool` | `True` | Keyword-only. Prefer paragraph/sentence breaks over hard cuts. |
| `min_chunk_size` | `int` | `500` | Keyword-only. Minimum offset a boundary break may occur at, avoiding tiny leading chunks. |

**Raises:** `ValueError` if `chunk_size <= 0`, `overlap < 0`, or `overlap >= chunk_size`.

#### `TextChunker.chunk_text`

```python
def chunk_text(self, text: str) -> list[TextChunk]
```

Split *text* into overlapping chunks. Returns `[]` for empty input, and a single chunk when the text fits within `chunk_size`.

#### `TextChunker.get_chunk_info`

```python
def get_chunk_info(self, text: str) -> dict[str, Any]
```

Chunk *text* and summarise the result without returning the chunks themselves.

**Returned keys:** `text_length`, `num_chunks`, `chunk_size`, `overlap`, `avg_chunk_size`, `last_chunk_size`.

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

Convenience wrapper that builds a one-off `TextChunker`.

**Example:**

```python
from bmlib.llm import chunk_text

for chunk in chunk_text(long_document, chunk_size=8000, overlap=200):
    print(f"[{chunk.chunk_index + 1}/{chunk.total_chunks}] {chunk.size} chars")
```

### `process_with_map_reduce`

```python
def process_with_map_reduce(
    text: str,
    map_fn: Callable[[str], Any],
    reduce_fn: Callable[[list[Any]], Any],
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Any
```

Map each chunk to an intermediate result, then reduce the list to a final result.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | *(required)* | Full text to process. |
| `map_fn` | `Callable[[str], Any]` | *(required)* | Maps one chunk's content to an intermediate result. |
| `reduce_fn` | `Callable[[list[Any]], Any]` | *(required)* | Combines the intermediate results. |
| `max_chunk_size` | `int` | `10000` | Maximum chunk size in characters. |

Text no longer than `max_chunk_size` bypasses chunking entirely and returns `map_fn(text)` — `reduce_fn` is never called in that case.

**Example:**

```python
from bmlib.llm import LLMMessage, get_llm_client, process_with_map_reduce

client = get_llm_client()

def summarise(chunk: str) -> str:
    return client.chat(
        messages=[LLMMessage(role="user", content=f"Summarise in one sentence:\n\n{chunk}")],
        model="anthropic:claude-3-5-haiku-20241022",
    ).content

def merge(parts: list[str]) -> str:
    return client.chat(
        messages=[
            LLMMessage(role="user", content="Merge these summaries:\n\n" + "\n".join(parts))
        ],
        model="anthropic:claude-sonnet-4-20250514",
    ).content

overall = process_with_map_reduce(long_document, summarise, merge)
```

### `process_with_rolling_summary`

```python
def process_with_rolling_summary(
    text: str,
    process_fn: Callable[[str, str | None], tuple[Any, str]],
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
    summary_max_length: int = 500,
) -> Any
```

Process chunks in order, carrying a summary of what came before into each call.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | *(required)* | Full text to process. |
| `process_fn` | `Callable[[str, str \| None], tuple[Any, str]]` | *(required)* | `(chunk, previous_summary) -> (result, new_summary)`. Receives `None` as the summary for the first chunk. |
| `max_chunk_size` | `int` | `10000` | Maximum chunk size in characters. |
| `summary_max_length` | `int` | `500` | The carried summary is truncated to this length (plus `"..."`). |

**Returns:** the result from the **final** chunk only — intermediate results are discarded. Accumulate them inside `process_fn` if you need them all. Short text bypasses chunking and is called once with `None`.

**Example:**

```python
from bmlib.llm import process_with_rolling_summary

findings: list[str] = []

def extract(chunk: str, previous: str | None) -> tuple[list[str], str]:
    context = f"Context so far: {previous}\n\n" if previous else ""
    text = client.chat(
        messages=[LLMMessage(role="user", content=f"{context}List findings in:\n\n{chunk}")],
        model="anthropic:claude-3-5-haiku-20241022",
    ).content
    findings.append(text)
    return findings, text[:400]

process_with_rolling_summary(long_document, extract)
print(len(findings))
```

### `get_text_with_priority`

```python
def get_text_with_priority(
    document: dict[str, Any],
    prefer_full_text: bool = True,
) -> tuple[str, str]
```

Return the best available text from a document mapping, along with the name of the field it came from. Never truncates.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `document` | `dict[str, Any]` | *(required)* | Mapping with candidate `full_text`, `abstract`, `content`, `text` fields. |
| `prefer_full_text` | `bool` | `True` | Prefer `full_text` over `abstract` when both are present. |

**Resolution order:**

| `prefer_full_text` | Order |
|--------------------|-------|
| `True` | `full_text` → `abstract` → `content` → `text` |
| `False` | `abstract` → `full_text` → `content` → `text` |

**Returns:** `(text, source_field_name)`, or `("", "none")` if every candidate field is empty.

> **Note:** `prefer_full_text=False` does not disable full text — it merely demotes it below the abstract. There is no way to exclude `full_text` from consideration.

### `combine_title_and_text`

```python
def combine_title_and_text(title: str, text: str, max_title_length: int = 500) -> str
```

Prefix a document body with its title for analysis.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str` | *(required)* | Document title, truncated at `max_title_length` as a guard. |
| `text` | `str` | *(required)* | Document body (abstract or full text). |
| `max_title_length` | `int` | `500` | Maximum retained title length. |

**Returns:** `"Title: {title}\n\n{text}"`; `"Title: {title}"` when `text` is empty; the bare `text` when `title` is empty.

**Example:**

```python
from bmlib.llm import combine_title_and_text, get_text_with_priority

body, source = get_text_with_priority(document)
prompt_text = combine_title_and_text(document["title"], body)
print(source)   # "full_text" | "abstract" | "content" | "text" | "none"
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
| Tool calling | Yes |
| Embeddings | No |
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
| Tool calling | Yes |
| Embeddings | No |

Reasoning models (`o1`, `o1-mini`, `o3-mini`) are sent `max_completion_tokens` instead of `max_tokens`, and `temperature` / `top_p` are omitted, as those models reject them.

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
| Tool calling | Yes |
| Embeddings | No |

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
| Tool calling | Yes |
| Embeddings | No |

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
| Tool calling | Yes |
| Embeddings | No |

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
| Tool calling | Yes (`tool_choice` ignored) |
| Embeddings | Yes (the only provider that implements them) |
| Extra kwargs | `think=True` for thinking mode |
| Embeddings | Yes — the only built-in provider implementing `embed()` |

---

## Provider Registry

Providers live in a module-level registry keyed by lowercase name. Built-ins are registered lazily on first lookup.

```python
from bmlib.llm.providers import get_provider, list_providers, register_provider
```

### `register_provider`

```python
def register_provider(name: str, cls: type[BaseProvider]) -> None
```

Register a provider class under *name*. Registering an existing name replaces it.

### `list_providers`

```python
def list_providers() -> list[str]
```

Return the names of all registered providers, registering the built-ins first if that has not yet happened.

> **Note:** Built-in registration **silently skips** any provider whose SDK is not installed. With only `bmlib[ollama]` installed, `list_providers()` returns `["ollama"]` — the absence of a name means "SDK missing", not "unsupported". `LLMClient.test_connection()` and `list_models()` iterate this list, so they too only see installed providers.

### `get_provider`

```python
def get_provider(name: str, **kwargs: object) -> BaseProvider
```

Instantiate and return a provider by name, forwarding `**kwargs` to its constructor.

**Raises:** `ValueError` if *name* is not registered (the message lists what is available).

**Example:**

```python
from bmlib.llm.providers import get_provider, list_providers

print(list_providers())            # e.g. ['anthropic', 'ollama']
provider = get_provider("ollama", base_url="http://gpu-box:11434")
ok, message = provider.test_connection()
```

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

Once registered, the provider is reachable through the normal model-string routing:

```python
from bmlib.llm import LLMClient, LLMMessage

client = LLMClient()
response = client.chat(
    messages=[LLMMessage(role="user", content="Hello")],
    model="mycloud:my-model-v1",
)
```

> **Note:** Tool calling for custom providers is blocked by the client-level allowlist described under [Detecting tool support](#detecting-tool-support). Passing `tools=` to a custom provider raises `NotImplementedError` regardless of what the class implements.

### `BaseProvider` Abstract Interface

**Class attributes to override:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `PROVIDER_NAME` | `str` | Short identifier (e.g. `"anthropic"`). |
| `DISPLAY_NAME` | `str` | Human-readable label. |
| `DESCRIPTION` | `str` | One-line description. |
| `WEBSITE_URL` | `str` | The provider's website. |
| `SETUP_INSTRUCTIONS` | `str` | How to get started. |

**Constructor:**

```python
def __init__(
    self,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: object,
) -> None
```

`base_url` falls back to `default_base_url`; leftover `**kwargs` are stored as extra config.

**Must be implemented:**

| Method / Property | Type | Description |
|-------------------|------|-------------|
| `is_local` | `property -> bool` | Whether the provider runs locally. |
| `is_free` | `property -> bool` | Whether usage is free. |
| `requires_api_key` | `property -> bool` | Whether an API key is needed. |
| `default_base_url` | `property -> str` | Default API URL. |
| `default_model` | `property -> str` | Default model ID. |
| `chat(messages, model, temperature, max_tokens, **kwargs)` | `method -> LLMResponse` | Send a chat request. `top_p`, `json_mode`, `tools`, and `tool_choice` arrive via `**kwargs`. |
| `list_models(force_refresh=False)` | `method -> list[ModelMetadata]` | List available models. |
| `test_connection()` | `method -> tuple[bool, str]` | Test connectivity. |
| `count_tokens(text, model=None)` | `method -> int` | Count tokens in text. |

**Provided with usable defaults (override as needed):**

| Method / Property | Type | Default behaviour |
|-------------------|------|-------------------|
| `api_key_env_var` | `property -> str` | Returns `""`. |
| `embed(text, model=None, **kwargs)` | `method -> EmbeddingResponse` | Raises `NotImplementedError`. |
| `get_model_pricing(model)` | `method -> ModelPricing` | Returns zero-cost pricing. |
| `calculate_cost(model, input_tokens, output_tokens)` | `method -> float` | Applies `get_model_pricing()` per million tokens. |
| `get_model_metadata(model)` | `method -> ModelMetadata \| None` | Linear search over `list_models()`. |
| `validate_model(model)` | `method -> bool` | Membership test over `list_models()`. |
| `format_model_string(model)` | `method -> str` | Returns `f"{PROVIDER_NAME}:{model}"`. |

**Worked example:**

```python
from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
    register_provider,
)


class EchoProvider(BaseProvider):
    """Trivial provider that echoes the last user message."""

    PROVIDER_NAME = "echo"
    DISPLAY_NAME = "Echo"
    DESCRIPTION = "Test provider that echoes input"
    WEBSITE_URL = ""
    SETUP_INSTRUCTIONS = "No setup required."

    @property
    def is_local(self) -> bool:
        return True

    @property
    def is_free(self) -> bool:
        return True

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def default_base_url(self) -> str:
        return ""

    @property
    def default_model(self) -> str:
        return "echo-1"

    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        last = messages[-1].content if messages else ""
        return LLMResponse(
            content=last,
            model=model or self.default_model,
            input_tokens=self.count_tokens(last),
            output_tokens=self.count_tokens(last),
            stop_reason="stop",
        )

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        return [
            ModelMetadata(
                model_id="echo-1",
                display_name="Echo 1",
                context_window=8192,
                pricing=ModelPricing(),
                capabilities=ProviderCapabilities(max_context_window=8192),
            )
        ]

    def test_connection(self) -> tuple[bool, str]:
        return True, "Echo provider is always available"

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // 4


register_provider("echo", EchoProvider)
```

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

> **Note:** `supports_function_calling` is descriptive metadata only. `LLMClient.chat()` does not consult it when deciding whether to accept `tools=` — see [Detecting tool support](#detecting-tool-support).
