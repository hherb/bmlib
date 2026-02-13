# bmlib

Shared library for biomedical literature tools — LLM abstraction, quality assessment, transparency analysis, and database utilities.

## Installation

```bash
uv pip install -e ".[all]"
```

## Modules

- **bmlib.llm** — LLM provider abstraction (Ollama native, Anthropic)
- **bmlib.db** — Thin database abstraction (SQLite, PostgreSQL) with pure functions
- **bmlib.templates** — Jinja2 template engine with file-based prompt templates
- **bmlib.agents** — Base agent class for LLM-driven tasks
- **bmlib.quality** — 3-tier quality assessment pipeline for biomedical literature
- **bmlib.transparency** — Multi-API transparency and bias analysis

## License

AGPL-3.0-or-later
