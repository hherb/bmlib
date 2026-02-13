# bmlib.templates — Prompt Template Engine

Jinja2-based template engine for loading and rendering prompt files from disk. Supports a user-override directory with fallback to package-shipped defaults, enabling prompt customisation without modifying installed code.

## Installation

No extra dependencies — Jinja2 is a core dependency of bmlib.

## Imports

```python
from bmlib.templates import TemplateEngine
```

---

## TemplateEngine

### Constructor

```python
class TemplateEngine:
    def __init__(
        self,
        user_dir: Path | None = None,
        default_dir: Path | None = None,
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_dir` | `Path \| None` | `None` | User override directory (checked first). Paths are expanded via `expanduser()`. |
| `default_dir` | `Path \| None` | `None` | Package default directory (fallback). |

**Resolution order** when rendering a template named `"scoring.txt"`:

1. `<user_dir>/scoring.txt` — user's customised version
2. `<default_dir>/scoring.txt` — package-shipped default

This lets users override any prompt without touching installed code.

**Example:**

```python
from pathlib import Path
from bmlib.templates import TemplateEngine

engine = TemplateEngine(
    user_dir=Path("~/.myapp/prompts"),
    default_dir=Path(__file__).parent / "defaults",
)
```

---

### `TemplateEngine.render`

```python
def render(self, template_name: str, **variables: Any) -> str
```

Render a template file with the given variables. Supports full Jinja2 syntax including conditionals, loops, and filters.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `template_name` | `str` | *(required)* | Filename of the template (e.g. `"scoring.txt"`). |
| `**variables` | `Any` | | Template variables passed to Jinja2's `render()`. |

**Returns:** The rendered template string.

**Raises:** `jinja2.TemplateNotFound` if the template does not exist in either directory.

**Example:**

```python
rendered = engine.render(
    "scoring.txt",
    title="A Randomized Controlled Trial of ...",
    abstract="We conducted a double-blind RCT ...",
    interests=["oncology", "immunotherapy"],
)
```

Template file (`scoring.txt`):

```
Score the relevance of this paper to the user's interests.

Title: {{ title }}
Abstract: {{ abstract }}

Interests:
{% for interest in interests %}
- {{ interest }}
{% endfor %}

Return a JSON object with "score" (0-10) and "reasoning".
```

---

### `TemplateEngine.has_template`

```python
def has_template(self, template_name: str) -> bool
```

Check whether a template exists in either directory.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `template_name` | `str` | *(required)* | Filename to check. |

**Returns:** `True` if the template exists, `False` otherwise.

**Example:**

```python
if engine.has_template("scoring.txt"):
    prompt = engine.render("scoring.txt", title="...", abstract="...")
else:
    prompt = f"Score this paper: ..."
```

---

### `TemplateEngine.install_defaults`

```python
def install_defaults(self) -> None
```

Copy all default templates to the user directory. Skips templates that already exist in the user directory (never overwrites). Only copies files with extensions `.txt`, `.j2`, or `.jinja2`.

Requires both `user_dir` and `default_dir` to be set. Creates `user_dir` if it doesn't exist.

**Example:**

```python
engine = TemplateEngine(
    user_dir=Path("~/.myapp/prompts"),
    default_dir=Path("/path/to/package/defaults"),
)
engine.install_defaults()
# All default templates are now available in ~/.myapp/prompts/
# Users can edit them without affecting the package originals.
```

---

## Template Syntax

Templates use full [Jinja2 syntax](https://jinja.palletsprojects.com/). Common patterns for LLM prompts:

### Variables

```
Title: {{ title }}
Abstract: {{ abstract }}
```

### Conditionals

```
{% if publication_types %}
Publication types: {{ publication_types | join(", ") }}
{% endif %}
```

### Loops

```
{% for author in authors %}
- {{ author }}
{% endfor %}
```

### Filters

```
Abstract (truncated): {{ abstract | truncate(500) }}
Keywords: {{ keywords | join(", ") | upper }}
```

### Defaults

```
Journal: {{ journal | default("Unknown") }}
```

**Note:** Autoescape is disabled (templates are plain-text prompts, not HTML). Trailing newlines are preserved.
