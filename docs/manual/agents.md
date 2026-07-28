# bmlib.agents — Agent Base Class

Base class for building LLM-powered agents. Provides shared infrastructure for agents that call LLMs: model/provider resolution, message helpers, template rendering, and resilient JSON response parsing.

Unlike a monolithic agent framework, `BaseAgent` does not read configuration from hardcoded paths. The calling application passes in the model string and LLM client explicitly.

The package is two files, `bmlib/agents/base.py` and `bmlib/agents/metrics.py`, exporting two names — `bmlib.agents.__all__`:

| Member | Kind | Purpose |
|--------|------|---------|
| `BaseAgent` | class | The agent base class. |
| `PerformanceMetrics` | class | Thread-safe per-agent call accounting, returned by `BaseAgent.metrics`. See [Performance Metrics](#performance-metrics). |
| `chat()` / `chat_json()` | methods | LLM interaction; `chat_json()` adds parsing, retry, and truncation handling. |
| `embed()` / `embed_batch()` | methods | Embedding calls, kept out of `PerformanceMetrics`. See [Embeddings](#embeddings). |
| `test_connection()` | method | Provider reachability check. See [Connectivity](#connectivity). |
| `system_msg()` / `user_msg()` / `assistant_msg()` | static methods | `LLMMessage` constructors. |
| `render_template()` | method | Prompt rendering via the injected `TemplateEngine`. |
| `parse_json()` | static method | Three-stage JSON extraction and repair. |

## Imports

```python
from bmlib.agents import BaseAgent, PerformanceMetrics
```

---

## BaseAgent

### Constructor

```python
class BaseAgent:
    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        embedding_model: str | None = None,
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `LLMClient` | *(required)* | The LLM client to use for chat requests. |
| `model` | `str` | *(required)* | Full model string (e.g. `"anthropic:claude-3-haiku-20240307"`). |
| `template_engine` | `TemplateEngine \| None` | `None` | Template engine for loading prompt files. Required if `render_template()` is called. |
| `temperature` | `float` | `0.3` | Default sampling temperature (lower = more deterministic). |
| `max_tokens` | `int` | `4096` | Default maximum tokens to generate. |
| `embedding_model` | `str \| None` | `None` | Default model string for [`embed()`](#embeddings) / [`embed_batch()`](#embeddings). `None` defers to the client's own default. Declared **last**, after `max_tokens`, so existing positional construction keeps working unchanged across versions. |

**Instance attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `self.llm` | `LLMClient` | The LLM client. |
| `self.model` | `str` | The model string. |
| `self.templates` | `TemplateEngine \| None` | The template engine. |
| `self.temperature` | `float` | Default temperature. |
| `self.max_tokens` | `int` | Default max tokens. |
| `self.embedding_model` | `str \| None` | Default embedding model. |

> **Note the name change across the boundary.** The constructor parameter is `template_engine`, but it is stored as **`self.templates`** — there is no `self.template_engine` attribute. Subclasses that touch the engine directly must use `self.templates`.

All six parameters are positional-or-keyword; there is no keyword-only marker in the signature. `model` is a full `"provider:model_name"` string (see [`bmlib.llm`](llm.md)), not a bare model name.

---

### Message Helpers

Static methods for creating `LLMMessage` instances:

#### `BaseAgent.system_msg`

```python
@staticmethod
def system_msg(content: str) -> LLMMessage
```

Create a system message.

#### `BaseAgent.user_msg`

```python
@staticmethod
def user_msg(content: str) -> LLMMessage
```

Create a user message.

#### `BaseAgent.assistant_msg`

```python
@staticmethod
def assistant_msg(content: str) -> LLMMessage
```

Create an assistant message.

---

### `BaseAgent.chat`

```python
def chat(
    self,
    messages: list[LLMMessage],
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    **kwargs: object,
) -> LLMResponse
```

Send a chat request through the LLM client using the agent's configured model and defaults.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `messages` | `list[LLMMessage]` | *(required)* | The conversation messages. |
| `json_mode` | `bool` | `False` | Request JSON-formatted output. |
| `temperature` | `float \| None` | `None` | Override the agent's default temperature. |
| `max_tokens` | `int \| None` | `None` | Override the agent's default max tokens. |
| `**kwargs` | `object` | | Forwarded to the provider (e.g. `think=True` for Ollama). |

**Returns:** `LLMResponse`

The per-call `temperature` and `max_tokens` substitute the agent's defaults only when they are `None`; passing `0` or `0.0` is honoured as an explicit value.

---

### `BaseAgent.chat_json`

```python
def chat_json(
    self,
    messages: list[LLMMessage],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_retries: int = 3,
    retry_context: str = "",
    **kwargs: object,
) -> dict
```

Send a chat request in JSON mode, parse the result, and retry on empty, unparseable, or truncated responses. This is the method agent subclasses should call — it combines [`chat()`](#baseagentchat) with [`parse_json()`](#baseagentparse_json) and exponential backoff.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `messages` | `list[LLMMessage]` | *(required)* | The conversation messages. |
| `temperature` | `float \| None` | `None` | Override the agent's default temperature. Also selects the truncation policy (see below). |
| `max_tokens` | `int \| None` | `None` | Override the agent's default max tokens. |
| `max_retries` | `int` | `3` | Total attempts, not extra attempts after the first. |
| `retry_context` | `str` | `""` | Label naming the task being attempted, folded into every retry, error, and failure message — `retry_context="quality classification"` turns `Retry 2/3 after 2s` into `Retry 2/3 for quality classification after 2s`. Also appears in the truncation `ValueError` and the final exhaustion message. Empty by default, in which case every message reads exactly as it did before this parameter existed. |
| `**kwargs` | `object` | | Forwarded to `chat()` and on to the provider. |

**Returns:** the parsed `dict`.

**Raises:** `ValueError` — immediately on truncation at temperature 0, or after all attempts are exhausted.

`json_mode=True` is always set; it is not a parameter.

**Backoff.** Attempts run as `for attempt in range(max_retries)`. Before every attempt after the first, the method logs a WARNING naming the previous error and sleeps `2 ** (attempt - 1)` seconds — 1s, 2s, 4s, …

---

#### Truncation handling

A response that stopped because it hit the token ceiling is reported as *truncation*, not as an "unparseable response". Truncation is detected from `LLMResponse.stop_reason` against a module constant:

```python
# bmlib/agents/base.py
_TRUNCATION_STOP_REASONS = ("max_tokens", "length")
```

Anthropic reports `"max_tokens"`; OpenAI-compatible providers report `"length"`.

The truncation check runs **first on every attempt** — before the empty-content check and before parsing. The four outcomes:

| Outcome | Condition | Behaviour |
|---------|-----------|-----------|
| **Complete despite truncation** | `stop_reason` is a truncation reason, but the content still parses | The parsed dict is **returned as-is**. A truncation `stop_reason` is not automatically a failure. |
| **Truncated at temperature 0** | Content does not parse and the effective temperature is exactly `0.0` | Raises `ValueError` **immediately — no retry**. Greedy sampling reproduces the identical truncation, so retrying only pays for it again. |
| **Truncated above temperature 0** | Content does not parse and the effective temperature is `> 0` | Logged at ERROR with the full response, then falls into the normal retry path — a resample may produce a shorter completion that fits. The truncation message is kept as `last_error`, so the final exhaustion error names truncation as the real cause. |
| **Exhaustion** | All `max_retries` attempts consumed | Raises `ValueError(f"Failed after {max_retries} attempts: {last_error}")`. |

The effective temperature is `temperature if temperature is not None else self.temperature` — an agent constructed with `temperature=0.0` gets the no-retry policy even when the call site passes nothing.

The truncation error message names the budget that was actually in force (`max_tokens` if given, otherwise `self.max_tokens`):

```
response truncated at max_tokens=256 (stop_reason='max_tokens') — raise max_tokens or request less output
```

**The other two failure modes** are treated as ordinary retryable errors:

| Failure | `last_error` | Log level |
|---------|--------------|-----------|
| Empty content after `.strip()` | `"empty response from model"` | WARNING — treated as a transport/model error. |
| `parse_json()` raises `ValueError` | `"unparseable response"` | ERROR, **with the full model output** for diagnosis. |

**Example — reacting to the two kinds of failure:**

```python
try:
    data = agent.chat_json(
        [agent.system_msg("Reply with JSON only."), agent.user_msg(prompt)],
        temperature=0.0,
        max_tokens=256,
    )
except ValueError as exc:
    if "truncated" in str(exc):
        # Deterministic: retrying is futile, so widen the budget instead.
        data = agent.chat_json(
            [agent.system_msg("Reply with JSON only."), agent.user_msg(prompt)],
            temperature=0.0,
            max_tokens=1024,
        )
    else:
        raise
```

---

### `BaseAgent.render_template`

```python
def render_template(self, template_name: str, **variables: Any) -> str
```

Render a prompt template using the configured template engine — delegates to `self.templates.render(template_name, **variables)`.

**Raises:** `RuntimeError` if `self.templates` is `None`:

```
No template engine configured — cannot render 'scoring.txt'
```

---

### `BaseAgent.parse_json`

```python
@staticmethod
def parse_json(text: str) -> dict
```

Extract and parse JSON from LLM response text, escalating through three stages and stopping at the first that succeeds:

1. **Direct parse** — `json.loads(text)`.
2. **Extraction** — [`extract_json()`](llm.md) from `bmlib.llm.utils`. Walks the candidate spans yielded by `iter_json_spans()` — fenced code blocks first, then balanced `{...}`/`[...]` spans — and returns the first candidate that parses **to a dict**, falling back to the first that parses at all if no candidate is an object. A greedy first-brace-to-last-brace match would swallow prose between two separate objects; walking balanced spans does not. If nothing parses, the extractor returns the input unchanged and this stage is skipped rather than re-parsed.
3. **Repair** — `extract_and_repair_json()` from `bmlib.llm.json_repair`. Walks the same candidate spans (with nested-object candidates suppressed, so it can never return an object nested inside a candidate it has already rejected) and returns the first that either validates as-is or repairs: single-quote string delimiters, trailing commas, missing commas, unquoted keys, unescaped newlines/tabs/control characters inside strings, and truncated output (missing closing brackets are appended). **A repaired candidate logs a WARNING** naming the response as possibly truncated — repair closes brackets, so a truncated response can parse into a valid but incomplete object.

**Raises:** `ValueError` if all three stages fail. The message embeds the first 200 characters of the input:

```
Could not parse JSON from LLM response: '<first 200 chars>'
```

**Example:**

```python
# All of these work:
BaseAgent.parse_json('{"score": 8}')                    # stage 1
BaseAgent.parse_json('```json\n{"score": 8}\n```')      # stage 2
BaseAgent.parse_json('The result is {"score": 8}.')     # stage 2
BaseAgent.parse_json("{'score': 8,}")                   # stage 3 — quotes + trailing comma
BaseAgent.parse_json('{"score": 8, "notes": "cut off')  # stage 3 — truncation repair
```

> **Stage 3 can rescue a truncated response.** That is why [`chat_json()`](#truncation-handling) attempts a parse *before* declaring a truncation stop reason fatal — a response that hit the ceiling mid-string may still yield a usable object. Repaired truncation means the tail of the JSON was invented by bracket-closing, so treat trailing fields as unreliable.

> **`salvage_json_fields()` is an opt-in last resort, not a fourth stage.** `parse_json()` never calls it automatically — silently returning partial data would turn a loud failure into a quiet wrong answer. When only a few known fields matter, catch the `ValueError` from `parse_json()` and call `salvage_json_fields(text, keys)` yourself; see [llm.md](llm.md#salvage_json_fields).

Internally, `chat_json()` uses the private classmethod `_try_parse(text)`, which is `parse_json()` returning `None` instead of raising (and `None` for empty input).

---

## Performance Metrics

`BaseAgent` accumulates call statistics into a private `PerformanceMetrics` instance (`self._metrics`), independent of the process-wide `TokenTracker` (`bmlib.llm`). `PerformanceMetrics` answers "what did this agent do"; `TokenTracker` answers "what has this process spent" — an agent's calls never write to global state, and nothing feeds a shared tracker automatically.

### `BaseAgent.metrics`

```python
@property
def metrics(self) -> PerformanceMetrics
```

An independent snapshot — a `PerformanceMetrics` copy taken under the internal lock. Reading `agent.metrics` while another thread is mid-call is safe; mutating the returned object has no effect on the agent.

### `BaseAgent.reset_metrics` / `start_metrics` / `stop_metrics`

```python
def reset_metrics(self) -> None
def start_metrics(self) -> None
def stop_metrics(self) -> None
```

`reset_metrics()` clears every counter and both time marks. `start_metrics()` / `stop_metrics()` set `start_time` / `end_time` (what `elapsed_time_seconds` measures against) — they do not gate whether calls are recorded; `chat()` records into the counters regardless of whether a collection period has been started.

### `BaseAgent.format_metrics_report`

```python
def format_metrics_report(self) -> str
```

Shorthand for `agent.metrics.format_report(title=type(agent).__name__)` — a human-readable report titled with the agent's class name.

### What gets recorded

`chat()` times each call with `time.monotonic()` and calls `PerformanceMetrics.add_request(prompt_tokens, completion_tokens, wall_time_seconds)` **only on success** — a raised call (network error, provider exception) records nothing, so a burst of failures does not silently deflate `tokens_per_second`. `chat_json()` additionally calls `add_retry()` before every attempt after the first, so `total_requests` counts attempts (each one a successful `chat()` call) and `total_retries` counts attempts beyond the first for the same logical request.

`embed()` / `embed_batch()` calls are **not** recorded — see [Embeddings](#embeddings).

### `PerformanceMetrics`

```python
from bmlib.agents import PerformanceMetrics
```

Thread-safe: an internal `threading.Lock` guards every mutation, so a `BaseAgent` shared across worker threads accumulates correctly.

**Fields:**

| Field | Type | Description |
|-------|------|--------------|
| `total_prompt_tokens` | `int` | Tokens sent to the model, summed across requests. |
| `total_completion_tokens` | `int` | Tokens generated by the model, summed across requests. |
| `total_tokens` | `int` | `total_prompt_tokens + total_completion_tokens`. |
| `total_requests` | `int` | Successful requests — every attempt that returned, not just the first per logical call. |
| `total_retries` | `int` | Attempts beyond the first, across all `chat_json()` calls. |
| `total_wall_time_seconds` | `float` | Wall-clock time inside successful requests only. |
| `start_time` | `float \| None` | `time.time()` at the last `mark_start()` / `start_metrics()`, or `None`. |
| `end_time` | `float \| None` | `time.time()` at the last `mark_end()` / `stop_metrics()`, or `None` while still running. |

**Methods:**

| Method | Description |
|--------|-------------|
| `add_request(prompt_tokens, completion_tokens, wall_time_seconds)` | Record one successful call. |
| `add_retry()` | Record one retry attempt. |
| `mark_start()` | Set `start_time` to now, clear `end_time`. |
| `mark_end()` | Set `end_time` to now. |
| `reset()` | Clear every counter and both time marks. |
| `snapshot()` | Return an independent copy, read under the lock. |
| `to_dict()` | Serialise to a plain dict, including the derived properties below (each rounded). |
| `from_dict(data)` | Classmethod; rebuild from `to_dict()` output. Derived values are ignored — they are recomputed from the raw fields. |
| `format_report(title=None)` | Render a human-readable, multi-line report. The heading line is omitted entirely when `title` is `None`. |

**Properties (derived, not stored):**

| Property | Formula | Notes |
|----------|---------|-------|
| `elapsed_time_seconds` | `end_time - start_time` (or `time.time() - start_time` if still running) | `0.0` if `start_time` is `None`. |
| `tokens_per_second` | `total_completion_tokens / total_wall_time_seconds` | Wall time, not model-inference time — no provider reports inference time through bmlib (`LLMResponse.duration_seconds` is declared but never populated), so an inference-time figure would be permanently zero. `0.0` if no wall time has accumulated. |
| `average_tokens_per_request` | `total_tokens / total_requests` | `0.0` if there have been no requests. |

**Example:**

```python
agent = ScoringAgent(llm=llm, model="anthropic:claude-3-haiku-20240307")
agent.start_metrics()
for title, abstract in papers:
    agent.score(title, abstract)
agent.stop_metrics()

print(agent.format_metrics_report())
# === ScoringAgent Performance Metrics ===
# Requests:     42 (3 retries)
# Tokens:       18,204 total (15,900 prompt + 2,304 completion)
# Time:         61.40s elapsed (58.12s in requests)
# Speed:        39.6 tokens/sec
# Avg/Request:  433 tokens

snap = agent.metrics    # an independent copy
agent.reset_metrics()   # snap is unaffected — it was already copied out
```

---

## Embeddings

### `BaseAgent.embed`

```python
def embed(self, text: str, model: str | None = None) -> list[float]
```

Embed *text* through `self.llm.embed()`, returning the raw vector.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | *(required)* | Text to embed. |
| `model` | `str \| None` | `None` | Overrides `self.embedding_model` for this call. `None` falls back to the agent's `embedding_model`, then to the client's own default. |

**Raises:** `ValueError` if the provider returns an empty vector.

### `BaseAgent.embed_batch`

```python
def embed_batch(
    self,
    texts: list[str],
    model: str | None = None,
    max_batch_size: int | None = None,
) -> list[list[float]]
```

Embed *texts* in as few provider requests as possible — several times faster than looping `embed()` over a bulk corpus.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `texts` | `list[str]` | *(required)* | Texts to embed. An empty list returns `[]` without contacting the provider. |
| `model` | `str \| None` | `None` | Overrides `self.embedding_model`. |
| `max_batch_size` | `int \| None` | `None` | Maximum texts per provider request; `None` lets the provider choose. |

**Returns:** one vector per input text, in input order.

**Raises:** `ValueError` if the provider returns a different number of vectors than texts given.

### `embedding_model`

Constructor parameter and instance attribute (`self.embedding_model`) — see the [constructor table](#constructor) above.

> **Embedding calls do not touch `PerformanceMetrics`.** Mixing embedding tokens into `tokens_per_second` — a figure about generation throughput — would distort it. Track embedding usage via `TokenTracker` or the raw `EmbeddingResponse` / `BatchEmbeddingResponse` if you need it.

---

## Connectivity

### `BaseAgent.test_connection`

```python
def test_connection(self) -> bool
```

Reports whether this agent's provider is reachable — nothing more. The provider is taken from the `"provider:model"` prefix of `self.model` when present, otherwise `self.llm.default_provider`.

This answers reachability only. Whether *this specific model* is installed or available is a separate question, answered by `llm.list_models(provider)` — a reachable Ollama server can still be missing the model `self.model` names.

---

## Creating Custom Agents

Subclass `BaseAgent` to build task-specific agents:

```python
from bmlib.agents import BaseAgent
from bmlib.llm import LLMClient
from bmlib.templates import TemplateEngine
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ScoringAgent(BaseAgent):
    """Scores paper relevance to user interests."""

    def score(self, title: str, abstract: str, interests: list[str]) -> dict:
        prompt = self.render_template(
            "scoring.txt",
            title=title,
            abstract=abstract,
            interests=interests,
        )
        try:
            return self.chat_json(
                messages=[
                    self.system_msg("You are a biomedical paper relevance scorer."),
                    self.user_msg(prompt),
                ],
                temperature=0.1,
                max_tokens=256,
            )
        except ValueError as exc:
            # Retries are already exhausted by chat_json; degrade rather than raise.
            logger.warning("Scoring failed: %s", exc)
            return {"score": 0, "reason": "unavailable"}


# Usage
llm = LLMClient()
engine = TemplateEngine(default_dir=Path("prompts/"))
agent = ScoringAgent(
    llm=llm,
    model="anthropic:claude-3-haiku-20240307",
    template_engine=engine,
    temperature=0.1,
    max_tokens=512,
)

result = agent.score(
    title="A Randomized Controlled Trial of ...",
    abstract="We conducted a double-blind RCT ...",
    interests=["oncology", "immunotherapy"],
)
print(f"Relevance score: {result['score']}/10")
```

### Design Pattern

The typical agent pattern is:

1. **Render a prompt** from a template with task-specific variables
2. **Build messages** with `system_msg()` + `user_msg()`
3. **Call `self.chat_json()`** for structured output — it sets `json_mode`, parses, and retries in one step
4. **Convert the dict** into a domain object (dataclass, dict, etc.)
5. **Catch the exception** and return a neutral value rather than propagating

This keeps agent logic focused on the domain while `BaseAgent` handles LLM plumbing.

> **Prefer `chat_json()` over `chat(json_mode=True)` + `parse_json()`.** The manual pairing gets no retries, no backoff, and no truncation diagnosis — a response that hit the token ceiling surfaces as a bare "Could not parse JSON" instead of naming the budget that caused it. Reach for `chat()` directly only when you want the raw `LLMResponse` (token counts, `stop_reason`, tool calls) or non-JSON output.

### In-tree examples

The two `BaseAgent` subclasses shipped with bmlib both follow this pattern — see [`bmlib.quality`](quality.md) for the full pipeline:

| Agent | Method | Call settings | On failure |
|-------|--------|---------------|------------|
| `StudyClassifier` (Tier 2) | `classify(title, abstract)` | constructor defaults `temperature=0.1, max_tokens=1024` | Logs a warning, returns `QualityAssessment.unclassified()` |
| `QualityAgent` (Tier 3) | `assess(title, abstract)` | constructor defaults `temperature=0.2, max_tokens=1024` | Logs a warning, returns `QualityAssessment.unclassified()` |

Neither passes sampling arguments to `chat_json()`, so the constructor's values apply — a call-site argument would silently override whatever the constructor was given, which is what once made the classifier's budget impossible to raise.

Both run at temperature `> 0`, so a truncated response consumes the full retry budget before failing. Neither propagates the `ValueError`: a failed assessment degrades one document rather than aborting a batch.
