# bmlib API Manual

**Version 0.1.0** | **License: AGPL-3.0-or-later** | **Python >=3.11**

bmlib is a shared Python library for biomedical literature tools. It provides LLM abstraction, quality assessment, transparency analysis, database utilities, and publication ingestion/sync.

## Installation

```bash
# Core (only jinja2 dependency)
pip install bmlib

# Editable install with all extras
pip install -e ".[all]"
```

### Optional dependency groups

| Group            | Install command                    | Provides                                  |
|------------------|------------------------------------|--------------------------------------------|
| `anthropic`      | `pip install bmlib[anthropic]`     | Anthropic Claude API provider              |
| `ollama`         | `pip install bmlib[ollama]`        | Ollama local model provider                |
| `postgresql`     | `pip install bmlib[postgresql]`    | PostgreSQL database backend                |
| `transparency`   | `pip install bmlib[transparency]`  | Transparency analysis (httpx)              |
| `publications`   | `pip install bmlib[publications]`  | Publication ingestion and sync (httpx)     |
| `dev`            | `pip install bmlib[dev]`           | pytest, pytest-cov, ruff                   |
| `all`            | `pip install bmlib[all]`           | All of the above                           |

## Module Overview

bmlib is organised into eight modules, each with a focused responsibility:

| Module | Description | Documentation |
|--------|-------------|---------------|
| [`bmlib.db`](database.md) | Thin database abstraction over DB-API connections (SQLite + PostgreSQL) | [database.md](database.md) |
| [`bmlib.llm`](llm.md) | Unified LLM client with pluggable providers (Anthropic, Ollama) | [llm.md](llm.md) |
| [`bmlib.templates`](templates.md) | Jinja2-based prompt template engine with directory fallback | [templates.md](templates.md) |
| [`bmlib.agents`](agents.md) | Base class for LLM-driven tasks | [agents.md](agents.md) |
| [`bmlib.quality`](quality.md) | 3-tier quality assessment pipeline for biomedical publications | [quality.md](quality.md) |
| [`bmlib.transparency`](transparency.md) | Multi-API transparency analysis (CrossRef, EuropePMC, OpenAlex, ClinicalTrials.gov) | [transparency.md](transparency.md) |
| [`bmlib.publications`](publications.md) | Publication ingestion, deduplication, storage, and multi-source sync | [publications.md](publications.md) |
| [`bmlib.fulltext`](fulltext.md) | Full-text retrieval (Europe PMC, Unpaywall, DOI), JATS XML parsing, disk caching | [fulltext.md](fulltext.md) |

## Architecture Principles

- **Pure functions in reusable modules.** Database operations take a DB-API connection as the first argument. State lives in the caller, not the library.
- **No ORM.** Explicit SQL via `bmlib.db` helpers.
- **Dataclass models** with `to_dict()` / `from_dict()` for serialisation.
- **Optional dependencies** guarded by `ImportError` with helpful install instructions.
- **Provider-agnostic LLM layer.** Model strings use the format `"provider:model_name"` for routing.

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
    json_mode=True,
)
print(response.content)
```

### Quality Assessment

```python
from bmlib.llm import LLMClient
from bmlib.quality import QualityManager, QualityFilter

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

### Publication Sync

```python
from datetime import date
from bmlib.db import connect_sqlite
from bmlib.publications import sync

conn = connect_sqlite("publications.db")
report = sync(
    conn,
    sources=["pubmed", "biorxiv"],
    date_from=date(2025, 1, 1),
    date_to=date(2025, 1, 7),
    email="researcher@example.com",
)
print(f"Added: {report.records_added}, Merged: {report.records_merged}")
```

### Full-Text Retrieval

```python
from bmlib.fulltext import FullTextService, FullTextCache

service = FullTextService(email="researcher@example.com")
result = service.fetch_fulltext(pmc_id="PMC7614751", doi="10.1234/example")

if result.source == "europepmc" and result.html:
    cache = FullTextCache()  # uses platform default directory
    cache.save_html(result.html, "PMC7614751")
    print(result.html[:200])
```

### Transparency Analysis

```python
from bmlib.transparency import TransparencyAnalyzer

analyzer = TransparencyAnalyzer(email="researcher@example.com")
result = analyzer.analyze("doc-001", doi="10.1038/s41586-024-00001-0")
print(result.transparency_score, result.risk_level)
```
