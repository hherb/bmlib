# bmlib.llm — LLM Abstraction Layer

Unified interface for interacting with large language models across multiple providers. Routes requests to the appropriate provider based on model strings of the form `"provider:model_name"`.

## Installation

```bash
# Anthropic Claude
pip install bmlib[anthropic]

# Ollama (local models)
pip install bmlib[ollama]

# Both
pip install bmlib[anthropic,ollama]
```

## Imports

```python
from bmlib.llm import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    TokenTracker,
    get_llm_client,
    get_token_tracker,
    reset_llm_client,
    reset_token_tracker,
)
```

---

## Data Types

### `LLMMessage`

```python
@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str
```

A message in an LLM conversation.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["system", "user", "assistant"]` | The role of the message sender. |
| `content` | `str` | The text content of the message. |

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
```

Response from an LLM request.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | `str` | *(required)* | The text response from the model. |
| `model` | `str` | `""` | The model that generated the response. |
| `input_tokens` | `int` | `0` | Number of input tokens consumed. |
| `output_tokens` | `int` | `0` | Number of output tokens generated. |
| `total_tokens` | `int` | `0` | Total tokens used. Auto-computed as `input_tokens + output_tokens` if not set. |
| `stop_reason` | `str \| None` | `None` | Why the model stopped generating (e.g. `"stop"`, `"max_tokens"`). |

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
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `default_provider` | `str` | `"anthropic"` | Provider to use when no `"provider:"` prefix is in the model string. |
| `ollama_host` | `str \| None` | `None` | Ollama server URL. Defaults to `OLLAMA_HOST` env var or `http://localhost:11434`. |
| `anthropic_api_key` | `str \| None` | `None` | Anthropic API key. Defaults to `ANTHROPIC_API_KEY` env var. |

---

### Model String Format

Model strings use the format `"provider:model_name"`:

```
"anthropic:claude-sonnet-4-20250514"
"ollama:medgemma4B_it_q8"
"ollama:llama3.1:8b"
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
| `**kwargs` | `object` | | Provider-specific options. Ollama supports `think=True` for thinking mode. |

**Returns:** `LLMResponse` with the model's response content and token usage.

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

Return a dictionary of provider metadata including name, URLs, capabilities, and configuration.

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

## Provider Details

### Anthropic

| Property | Value |
|----------|-------|
| Provider name | `anthropic` |
| Default model | `claude-sonnet-4-20250514` |
| API key env var | `ANTHROPIC_API_KEY` |
| Is local | No |
| Is free | No |
| System messages | Separated per Anthropic API requirement |

**Known model pricing (per million tokens):**

| Model | Input | Output |
|-------|-------|--------|
| `claude-opus-4-20250514` | $15.00 | $75.00 |
| `claude-sonnet-4-20250514` | $3.00 | $15.00 |
| `claude-sonnet-4-5-20250929` | $3.00 | $15.00 |
| `claude-3-5-haiku-20241022` | $1.00 | $5.00 |
| `claude-3-haiku-20240307` | $0.25 | $1.25 |

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
| `list_models()` | `method -> list[ModelMetadata]` | List available models. |
| `test_connection()` | `method -> tuple[bool, str]` | Test connectivity. |
| `count_tokens(text, model)` | `method -> int` | Count tokens in text. |

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
