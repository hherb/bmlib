# bmlib.agents — Agent Base Class

Base class for building LLM-powered agents. Provides shared infrastructure for agents that call LLMs: model/provider resolution, message helpers, template rendering, and resilient JSON response parsing.

Unlike a monolithic agent framework, `BaseAgent` does not read configuration from hardcoded paths. The calling application passes in the model string and LLM client explicitly.

The module is a single file, `bmlib/agents/base.py`, exporting one name:

| Member | Kind | Purpose |
|--------|------|---------|
| `BaseAgent` | class | The agent base class — the whole of `bmlib.agents.__all__`. |
| `chat()` / `chat_json()` | methods | LLM interaction; `chat_json()` adds parsing, retry, and truncation handling. |
| `system_msg()` / `user_msg()` / `assistant_msg()` | static methods | `LLMMessage` constructors. |
| `render_template()` | method | Prompt rendering via the injected `TemplateEngine`. |
| `parse_json()` | static method | Three-stage JSON extraction and repair. |

## Imports

```python
from bmlib.agents import BaseAgent
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
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `LLMClient` | *(required)* | The LLM client to use for chat requests. |
| `model` | `str` | *(required)* | Full model string (e.g. `"anthropic:claude-3-haiku-20240307"`). |
| `template_engine` | `TemplateEngine \| None` | `None` | Template engine for loading prompt files. Required if `render_template()` is called. |
| `temperature` | `float` | `0.3` | Default sampling temperature (lower = more deterministic). |
| `max_tokens` | `int` | `4096` | Default maximum tokens to generate. |

**Instance attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `self.llm` | `LLMClient` | The LLM client. |
| `self.model` | `str` | The model string. |
| `self.templates` | `TemplateEngine \| None` | The template engine. |
| `self.temperature` | `float` | Default temperature. |
| `self.max_tokens` | `int` | Default max tokens. |

> **Note the name change across the boundary.** The constructor parameter is `template_engine`, but it is stored as **`self.templates`** — there is no `self.template_engine` attribute. Subclasses that touch the engine directly must use `self.templates`.

All five parameters are positional-or-keyword; there is no keyword-only marker in the signature. `model` is a full `"provider:model_name"` string (see [`bmlib.llm`](llm.md)), not a bare model name.

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
2. **Extraction** — [`extract_json()`](llm.md) from `bmlib.llm.utils`. Code-block aware (` ```json ... ``` `), falling back to a balanced-brace scan that returns the first `{...}` span that actually parses. A greedy first-brace-to-last-brace match would swallow prose between two separate objects; the balanced scan does not. If the extractor returns the input unchanged (nothing found), this stage is skipped rather than re-parsed.
3. **Repair** — `extract_and_repair_json()` from `bmlib.llm.json_repair`. Extracts the JSON span, then repairs common LLM defects: single-quote string delimiters, trailing commas, missing commas, unquoted keys, unescaped newlines/tabs/control characters inside strings, and truncated output (missing closing brackets are appended).

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

Internally, `chat_json()` uses the private classmethod `_try_parse(text)`, which is `parse_json()` returning `None` instead of raising (and `None` for empty input).

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
| `StudyClassifier` (Tier 2) | `classify(title, abstract)` | `chat_json(temperature=0.1, max_tokens=256)` | Logs a warning, returns `QualityAssessment.unclassified()` |
| `QualityAgent` (Tier 3) | `assess(title, abstract)` | `chat_json(temperature=0.2, max_tokens=1024)` | Logs a warning, returns `QualityAssessment.unclassified()` |

Both run at temperature `> 0`, so a truncated response consumes the full retry budget before failing — the cheap classifier's 256-token ceiling makes that a real possibility on verbose models. Neither propagates the `ValueError`: a failed assessment degrades one document rather than aborting a batch.
