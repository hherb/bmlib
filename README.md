# bmlib

[![CI](https://github.com/hherb/bmlib/actions/workflows/ci.yml/badge.svg)](https://github.com/hherb/bmlib/actions/workflows/ci.yml)

Shared Python library for biomedical literature tools — LLM abstraction, quality assessment, transparency analysis, full-text retrieval, publication ingestion, and database utilities.

**Version:** 0.10.0 | **License:** AGPL-3.0-or-later | **Python:** >=3.11

## Installation

```bash
# Core (only jinja2 dependency)
pip install bmlib

# Editable install with all extras
uv pip install -e ".[all,dev]"
```

### Optional dependency groups

| Group | Install command | Provides |
|-------|----------------|----------|
| `anthropic` | `pip install bmlib[anthropic]` | Anthropic Claude LLM provider |
| `ollama` | `pip install bmlib[ollama]` | Ollama local LLM provider |
| `openai` | `pip install bmlib[openai]` | OpenAI, DeepSeek, Mistral, Gemini, and OpenAI-compatible providers |
| `postgresql` | `pip install bmlib[postgresql]` | PostgreSQL database backend |
| `transparency` | `pip install bmlib[transparency]` | Transparency analysis (httpx) |
| `publications` | `pip install bmlib[publications]` | Publication ingestion and sync (httpx) |
| `fulltext` | `pip install bmlib[fulltext]` | `FullTextService` retrieval (httpx). The rest of `bmlib.fulltext` — JATS parser, models, `SectionSegmenter` — needs nothing beyond core |
| `pdf` | `pip install bmlib[pdf]` | PDF → text conversion (pymupdf) |
| `dev` | `pip install bmlib[dev]` | pytest, pytest-cov, ruff |
| `all` | `pip install bmlib[all]` | Every runtime extra above (**not** `dev`) |

## Modules

| Module | Description |
|--------|-------------|
| **bmlib.db** | Thin database abstraction (SQLite + PostgreSQL) with pure functions over DB-API connections |
| **bmlib.llm** | Unified LLM client with pluggable providers (Anthropic, OpenAI, Ollama, DeepSeek, Mistral, Gemini) — chat, tool calling, embeddings, JSON repair, and text chunking |
| **bmlib.templates** | Jinja2-based prompt template engine with user-override directory fallback |
| **bmlib.agents** | Base agent class for LLM-driven tasks with template rendering and JSON parsing |
| **bmlib.context_processor** | Hierarchical map-reduce over content that exceeds one LLM context window — batch, extract, consolidate recursively |
| **bmlib.quality** | 4-tier quality assessment pipeline for biomedical publications (metadata → LLM classifier → deep assessment → Cochrane nine-domain risk of bias), plus rule-based extractors |
| **bmlib.transparency** | Multi-API transparency and bias analysis (CrossRef, Europe PMC, OpenAlex, ClinicalTrials.gov) |
| **bmlib.publications** | Publication ingestion from PubMed, bioRxiv, medRxiv, and OpenAlex with deduplication and sync |
| **bmlib.fulltext** | Full-text retrieval (Europe PMC → Unpaywall → DOI), JATS XML parsing, PDF → text conversion, and disk-based caching |

## Quick Start

### Database

```python
from bmlib.db import connect_sqlite, execute, fetch_all, transaction

conn = connect_sqlite("~/.myapp/data.db")
with transaction(conn):
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "A paper"))
rows = fetch_all(conn, "SELECT * FROM papers")
```

### LLM

```python
from bmlib.llm import LLMClient, LLMMessage

client = LLMClient(default_provider="ollama")
response = client.chat(
    messages=[LLMMessage(role="user", content="Summarise this paper.")],
    model="ollama:medgemma4B_it_q8",
)
print(response.content)
```

Model strings use the format `"provider:model_name"`:

```
"anthropic:claude-sonnet-4-20250514"
"openai:gpt-4o"
"ollama:medgemma4B_it_q8"
"deepseek:deepseek-chat"
"mistral:mistral-large-latest"
"gemini:gemini-2.0-flash"
```

### Tool Calling

```python
from bmlib.llm import LLMClient, LLMMessage, LLMToolDefinition

search = LLMToolDefinition(
    name="search_pubmed",
    description="Search PubMed for articles matching a query.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)

client = LLMClient()
response = client.chat(
    messages=[LLMMessage(role="user", content="Find recent trials on statins.")],
    model="anthropic:claude-sonnet-4-20250514",
    tools=[search],
)

for call in response.tool_calls or []:
    print(call.name, call.arguments)  # arguments is already a parsed dict
```

To continue the conversation, append the assistant message (carrying
`tool_calls`) and one `role="tool"` message per call, each with the matching
`tool_call_id`, then send the whole list again.

### Long Documents

```python
from bmlib.llm import chunk_text, process_with_map_reduce

for chunk in chunk_text(paper_text, chunk_size=8000, overlap=200):
    print(chunk.chunk_index, chunk.size)

summary = process_with_map_reduce(
    paper_text,
    map_fn=lambda part: summarise(part),
    reduce_fn=lambda parts: summarise("\n".join(parts)),
)
```

### Publication Sync

```python
from datetime import date
from bmlib.db import connect_sqlite
from bmlib.publications import sync

conn = connect_sqlite("publications.db")
report = sync(
    conn,
    sources=["pubmed", "biorxiv"],
    # An ordinary week. A window containing a first-of-month meets a day of
    # tens of thousands of records — see docs/manual/publications.md.
    date_from=date(2025, 3, 3),
    date_to=date(2025, 3, 9),
    email="researcher@example.com",
)
print(f"Added: {report.records_added}, Merged: {report.records_merged}")
```

### Full-Text Retrieval

```python
from bmlib.fulltext import FullTextService

service = FullTextService(email="researcher@example.com")

# Passing identifier= enables the built-in disk cache (platform default dir).
result = service.fetch_fulltext(
    pmc_id="PMC7614751", doi="10.1234/example", identifier="PMC7614751"
)

if result.html:
    print(result.html[:200])
```

### Quality Assessment

```python
from bmlib.llm import LLMClient
from bmlib.quality import QualityManager

llm = LLMClient()
manager = QualityManager(
    llm=llm,
    classifier_model="anthropic:claude-3-haiku-20240307",
    assessor_model="anthropic:claude-sonnet-4-20250514",
)

assessment = manager.assess(
    title="A Randomized Controlled Trial of ...",
    abstract="We conducted a double-blind RCT ...",
    publication_types=["Randomized Controlled Trial"],
)
print(assessment.study_design, assessment.quality_tier)
```

### Transparency Analysis

```python
from bmlib.transparency import TransparencyAnalyzer

analyzer = TransparencyAnalyzer(email="researcher@example.com")
result = analyzer.analyze("doc-001", doi="10.1038/s41586-024-00001-0")
print(result.transparency_score, result.risk_level)
```

## Development

```bash
# Install with dev dependencies
uv pip install -e ".[all,dev]"

# Run tests
uv run pytest tests/ -v

# Lint and format
uv run ruff check .
uv run ruff format --check .

# Type-check. Scope and settings live in pyproject.toml, so this bare
# command is the one CI runs. It needs the extras installed above: all but
# psycopg2 ship their own py.typed (types-psycopg2 covers that one), and
# against a bare interpreter mypy reports them — and jinja2 — as missing
# stubs.
uv run mypy
```

## Documentation

Full API documentation is available in [docs/manual/](docs/manual/index.md).

## License

AGPL-3.0-or-later
