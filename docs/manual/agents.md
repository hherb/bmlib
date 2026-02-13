# bmlib.agents — Agent Base Class

Base class for building LLM-powered agents. Provides shared infrastructure for agents that call LLMs: model/provider resolution, message helpers, template rendering, and JSON response parsing.

Unlike a monolithic agent framework, `BaseAgent` does not read configuration from hardcoded paths. The calling application passes in the model string and LLM client explicitly.

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

---

### `BaseAgent.render_template`

```python
def render_template(self, template_name: str, **variables: Any) -> str
```

Render a prompt template using the configured template engine.

**Raises:** `RuntimeError` if no template engine was configured.

---

### `BaseAgent.parse_json`

```python
@staticmethod
def parse_json(text: str) -> dict
```

Extract and parse JSON from LLM response text. Handles three cases:

1. Direct JSON parse
2. JSON embedded in markdown code blocks (` ```json ... ``` `)
3. Bare `{...}` object within surrounding text

**Raises:** `ValueError` if no valid JSON can be extracted.

**Example:**

```python
# All of these work:
BaseAgent.parse_json('{"score": 8}')
BaseAgent.parse_json('```json\n{"score": 8}\n```')
BaseAgent.parse_json('The result is {"score": 8}.')
```

---

## Creating Custom Agents

Subclass `BaseAgent` to build task-specific agents:

```python
from bmlib.agents import BaseAgent
from bmlib.llm import LLMClient, LLMMessage
from bmlib.templates import TemplateEngine
from pathlib import Path


class ScoringAgent(BaseAgent):
    """Scores paper relevance to user interests."""

    def score(self, title: str, abstract: str, interests: list[str]) -> dict:
        prompt = self.render_template(
            "scoring.txt",
            title=title,
            abstract=abstract,
            interests=interests,
        )
        response = self.chat(
            messages=[
                self.system_msg("You are a biomedical paper relevance scorer."),
                self.user_msg(prompt),
            ],
            json_mode=True,
            temperature=0.1,
        )
        return self.parse_json(response.content)


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
3. **Call `self.chat()`** with `json_mode=True` for structured output
4. **Parse the response** with `self.parse_json()`
5. **Return a domain object** (dataclass, dict, etc.)

This keeps agent logic focused on the domain while `BaseAgent` handles LLM plumbing.
