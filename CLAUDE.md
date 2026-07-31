# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**bmlib** (v0.6.0) is a shared Python library for biomedical literature tools, licensed under AGPL-3.0-or-later. It provides LLM abstraction, quality assessment, transparency analysis, full-text retrieval, database utilities, and publication ingestion/sync.

## Development Setup

```bash
uv pip install -e ".[all,dev]"
```

- **Python:** >=3.11
- **Packaging:** pyproject.toml with setuptools
- **Testing:** pytest (`uv run pytest tests/ -v`)
- **Linting/Formatting:** ruff (`uv run ruff check .` / `uv run ruff format .`)
- **Core dependency:** jinja2 only. Everything else is optional.
- **Use `uv`, never bare pip.**

### Optional dependency groups

| Group          | Packages              | Purpose                                |
|----------------|-----------------------|----------------------------------------|
| anthropic      | anthropic>=0.30       | Anthropic Claude LLM provider          |
| ollama         | ollama>=0.3           | Ollama local LLM provider              |
| openai         | openai>=1.0           | OpenAI, DeepSeek, Mistral, Gemini, and OpenAI-compatible providers |
| postgresql     | psycopg2-binary>=2.9  | PostgreSQL database backend            |
| transparency   | httpx>=0.25           | Transparency analysis API calls        |
| publications   | httpx>=0.25           | Publication fetcher API calls           |
| pdf            | pymupdf>=1.23         | PDF → text conversion in `fulltext/`   |
| dev            | pytest>=7.0, pytest-cov, ruff | Development and testing tools  |
| all            | All runtime extras    | Everything except `dev`                |

## Architecture

### Directory structure

```
bmlib/
├── __init__.py              # Package root, exports __version__
├── agents/                  # LLM-driven task base class and per-agent metrics
│   ├── base.py              # BaseAgent — chat/chat_json, embeddings, JSON parsing
│   └── metrics.py           # PerformanceMetrics — thread-safe per-agent call accounting
├── db/                      # Database abstraction (SQLite + PostgreSQL)
│   ├── backend.py           # is_sqlite(), placeholder(), placeholders() — dialect detection
│   ├── connection.py        # connect_sqlite(), connect_postgresql()
│   ├── operations.py        # execute, fetch_one, fetch_all, fetch_scalar, table_exists, create_tables
│   ├── transactions.py      # transaction() context manager, transaction_depth(), owns_commit()
│   └── migrations.py        # Migration dataclass, run_migrations()
├── fulltext/                # Full-text retrieval, JATS XML parsing, PDF conversion
│   ├── cache.py             # Disk-based FullTextCache, sanitize_identifier()
│   ├── jats_parser.py       # JATS XML → structured data
│   ├── models.py            # FullTextResult, FullTextSourceEntry, JATSArticle, etc.
│   ├── pdf_converter.py     # Pluggable PDF → text (PDFConverter ABC, PyMuPDF backend)
│   └── service.py           # Tiered FullTextService (known sources → EuropePMC → Unpaywall → DOI)
├── llm/                     # Unified LLM client with pluggable providers
│   ├── client.py            # LLMClient router, get_llm_client() singleton
│   ├── data_types.py        # LLMMessage, LLMResponse, LLMToolDefinition, LLMToolCall, EmbeddingResponse
│   ├── json_repair.py       # Repair malformed LLM JSON (repair_json, safe_json_loads, ...)
│   ├── text_utils.py        # TextChunker, map-reduce / rolling-summary long-document helpers
│   ├── token_tracker.py     # Thread-safe TokenTracker
│   ├── utils.py             # extract_json()
│   └── providers/           # Provider implementations
│       ├── __init__.py      # Registry: register_provider, get_provider, list_providers
│       ├── base.py          # BaseProvider ABC, ModelMetadata, ModelPricing
│       ├── anthropic.py     # Anthropic Claude
│       ├── ollama.py        # Ollama (local)
│       ├── openai_provider.py # OpenAI
│       ├── openai_compat.py # OpenAI-compatible API servers
│       ├── deepseek.py      # DeepSeek
│       ├── mistral.py       # Mistral
│       └── gemini.py        # Google Gemini
├── publications/            # Publication ingestion, deduplication, and sync
│   ├── models.py            # Publication, FullTextSource, FetchedRecord, SyncReport, SourceDescriptor
│   ├── schema.py            # SQL schema (publications, fulltext_sources, download_days)
│   ├── storage.py           # Upsert with dedup by DOI/PMID, merge logic
│   ├── sync.py              # Multi-source sync orchestrator
│   └── fetchers/            # Source fetcher plugins
│       ├── registry.py      # register_source, get_source, get_fetcher, list_sources
│       ├── pubmed.py        # PubMed E-utilities (esearch + efetch)
│       ├── biorxiv.py       # bioRxiv / medRxiv
│       └── openalex.py      # OpenAlex
├── quality/                 # 3-tier quality assessment pipeline + standalone assessment tools
│   ├── data_models.py       # StudyDesign enum, QualityTier, BiasRisk, QualityAssessment, QualityFilter
│   ├── manager.py           # QualityManager orchestrator
│   ├── metadata_filter.py   # Tier 1: PubMed metadata → StudyDesign (free)
│   ├── scoring_models.py    # DimensionScore audit-trail models
│   ├── study_classifier.py  # Tier 2: LLM study-design classifier (cheap)
│   ├── quality_agent.py     # Tier 3: deep assessment agent (capable model)
│   ├── cochrane_models.py   # Cochrane 9-domain Risk-of-Bias + study-characteristics models
│   ├── cochrane_formatter.py # Markdown / HTML renderers for the Cochrane tables
│   ├── extractors.py        # Rule-based (LLM-free) study-type and sample-size extraction
│   └── scoring_models.py    # DimensionScore / AssessmentDetail audit-trail models
├── templates/engine.py      # Jinja2 TemplateEngine with user/default dir fallback
└── transparency/            # Multi-API transparency analysis
    ├── analyzer.py          # TransparencyAnalyzer (CrossRef, EuropePMC, OpenAlex, ClinicalTrials.gov)
    └── models.py            # TransparencyResult, TransparencyRisk enum, TransparencySettings
```

### Module descriptions

- **`db/`** — Thin database abstraction via pure functions over DB-API connections. Supports SQLite (built-in) and PostgreSQL (optional). No ORM; all SQL is explicit, so any module serving both backends gets its parameter placeholder from `placeholder(conn)` / `placeholders(conn, n)` rather than hard-coding `?`.
- **`llm/`** — Unified LLM client with a pluggable provider registry. Built-in providers: Anthropic, OpenAI, Ollama, DeepSeek, Mistral, Gemini. Model strings use `"provider:model_name"` format (e.g. `"anthropic:claude-sonnet-4-20250514"`). Providers are lazily registered on first access, and a provider whose SDK is not installed is silently skipped — so `list_providers()` reflects what is installed, not what exists. Beyond chat, the package covers embeddings (`LLMClient.embed()` / batch `embed_batch()`, Ollama only, both via `/api/embed`), tool calling (`tools`/`tool_choice` on `chat()`), thinking/reasoning (`think=` kwarg on `chat()` → `LLMResponse.thinking`), JSON repair, and text chunking. Model listing never fans out per model: the Anthropic and OpenAI-compatible providers each issue a single source-level `models.list()` call (the SDK may paginate underneath), and Ollama defers its per-model context-window lookup (see "Lazy model metadata" below).
- **`templates/`** — Jinja2-based prompt template engine with user directory override and default directory fallback.
- **`agents/`** — `BaseAgent` class for LLM-driven tasks. Provides `chat()`, `chat_json()` (retry with backoff, truncation-aware, `retry_context` label folded into every log line), `render_template()`, `parse_json()`, and message helpers. `embed()` / `embed_batch()` wrap the client's embedding calls (via the `embedding_model` constructor parameter, declared last for positional stability) and are deliberately excluded from the metrics below; `test_connection()` reports provider reachability only, not whether a given model is installed. `agents/metrics.py` provides `PerformanceMetrics`, thread-safe per-agent call accounting (tokens, requests, retries, wall time) surfaced via `BaseAgent.metrics` / `reset_metrics()` / `start_metrics()` / `stop_metrics()` / `format_metrics_report()` — independent of the process-wide `llm.TokenTracker`, since it answers "what did this agent do" rather than "what has this process spent".
- **`quality/`** — 3-tier quality assessment: (1) free metadata classification, (2) cheap LLM classifier, (3) deep LLM assessment. Uses CEBM evidence hierarchy for quality tiers. The Cochrane models/formatter and the rule-based extractors are **standalone**: nothing in the tiered pipeline imports them, and there is no conversion between `BiasRisk` and `CochraneRiskOfBias`, or between `DimensionScore` and `QualityAssessment`. Wiring them together is open work — see ROADMAP.md.
- **`transparency/`** — Queries CrossRef, Europe PMC (search + full text), PubMed, OpenAlex, and ClinicalTrials.gov to compute a transparency score (0-100) covering funding, COI, data availability, trial registration, and open access. The PubMed step is one `efetch` per analysis, skipped without a PMID (taken from the caller or from the Europe PMC record already fetched); it supplies structured `<CoiStatement>`, `<DataBankList>` and `<GrantList>` signals that Europe PMC cannot give for a closed-access paper, and `pubmed_api_key` rides on it. `<DataBankList>` is read twice over: a trial-registry name establishes registration, and any other name (GENBANK, PDB, SRA, Dryad) is a data deposition that sets `data_availability_level` to `full_open`. That field therefore has two producers, so it is written through `_Analysis.note_data_availability()` — strongest evidence of openness wins whichever arrives first, the superseded level's credit is taken back rather than added to, and `unknown` never displaces a finding. When no API is reachable the result is `UNKNOWN` at score 0, so an unreachable network does not masquerade as a HIGH-risk paper; `TransparencyResult.unknown_reason` says which of the three `UNKNOWN` cases it was, set if and only if `risk_level` is `UNKNOWN`.
- **`publications/`** — Publication ingestion from multiple sources (PubMed, bioRxiv, medRxiv, OpenAlex) with deduplication by DOI/PMID, merge-on-upsert, and date-range sync tracking. Runs on both backends `db/` supports: placeholders come from `db.placeholder()`, `ensure_schema()` picks the matching DDL, and the one irreducibly dialect-specific need — reading back an inserted row's id — is `cur.lastrowid` on SQLite and `RETURNING id` on PostgreSQL. Everything else is written in the intersection of the two dialects. `tests/test_backends.py` runs each test against both.
- **`fulltext/`** — Tiered full-text retrieval (caller-supplied sources → Europe PMC XML → Europe PMC PDF → Unpaywall → DOI/PubMed URL) with JATS XML parsing and disk-based caching. PDF→text conversion lives here too, and `FullTextService` calls it: a retrieved PDF is extracted into `FullTextResult.html` (opt out with `FullTextService(convert_pdfs=False)`, needs `bmlib[pdf]`). Extraction only runs once the PDF is cached, so it needs an `identifier`. A body-less JATS document — `<front>`+`<back>` with no article prose, which medRxiv serves for some preprints — is detected via `JATSArticle.has_body`, never cached, and held back as a last resort so the chain keeps looking for the real article. `FullTextResult.content_kind` tells the caller which of `fulltext` / `abstract` / `extracted` it actually got, so an abstract is not analysed as if it were an article.

## Coding Conventions

- **Pure functions in reusable modules.** Database operations take a DB-API connection as first argument. Avoid classes where a function suffices. State lives in the caller, not the library.
- **Docstrings required** on all public functions, classes, and modules. Use Google-style or reStructuredText format consistently within a module.
- **Type hints required** on all function signatures (parameters and return types).
- **Unit tests required** for new functionality. Tests go in `tests/` and use pytest. Follow existing test patterns (in-memory SQLite for DB tests, mocked HTTP for API tests).
- **AGPL-3 license header** required at the top of every source file. Copy from any existing file. The header format is:

```python
# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# ...
```

- **Dataclass models** with `to_dict()` / `from_dict()` for serialisation. Use `field(default_factory=...)` for mutable defaults.
- **No ORM.** Write explicit SQL. Use `bmlib.db` helpers (`execute`, `fetch_one`, `fetch_all`, `fetch_scalar`, `table_exists`, `create_tables`, `transaction`).
- **Optional dependencies** guarded by `try: import ... except ImportError: raise ImportError("Install with: pip install bmlib[group]")`.
- **ruff** for linting and formatting: line-length=100, target Python 3.11+. Lint rules: E, F, I, N, W, UP.

## Key Design Patterns

### Registry pattern (lazy loading)
Both LLM providers and publication fetchers use a module-level `_REGISTRY` dict with `_ensure_builtins()` for lazy registration. New providers/fetchers can be added at runtime via `register_provider()` / `register_source()`.

### Singleton globals with reset
`LLMClient` and `TokenTracker` use module-level singletons (`get_llm_client()`, `get_token_tracker()`) with corresponding `reset_*()` functions for testing.

### DB-API connection threading
All database functions take a connection as the first argument. The `transaction(conn)` context manager handles commit/rollback. No hidden state.

### Composable transactions via savepoints
`transaction(conn)` entered while another `transaction()` block is open joins it with a `SAVEPOINT` instead of committing — the outermost block owns the commit, and an inner failure rolls back to its own savepoint without losing the batch. This is what lets a bulk loop wrap many `transaction()`-using calls in one outer block and pay a single commit; `publications.sync()` depends on it for its one-commit-per-day batching.

Both backends nest, but they must answer "is a block already open?" differently. SQLite auto-begins only before DML, so `conn.in_transaction` means what it says. psycopg2 begins a transaction on the first statement of *any* kind — a bare `SELECT` leaves the connection INTRANS — so reading the driver's status would classify an ordinary un-nested block as nested and silently skip its commit, breaking every write. PostgreSQL therefore counts bmlib's own open blocks (`transaction_depth()`). Anything that commits conditionally (`create_tables()`) must ask `owns_commit()`, never the driver.

That count is keyed by *(thread, `id(conn)`)*. The thread is part of the key because nesting is a property of one call stack — keyed by connection alone, a block held open on one thread makes an unrelated outermost block on another thread look nested, so it opens a savepoint, never commits, and loses its write with nothing raised. `id(conn)` is used because psycopg2's connection is a C type that rejects attribute assignment and `sqlite3.Connection` supports neither weak references nor useful equality; the entry holds a strong reference to the connection, which is what stops the id being recycled onto a different one while the entry lives. `tests/test_backends.py::test_a_block_on_another_thread_does_not_look_like_nesting` is the regression guard.

### Optional dependencies guarded at the call site
Optional imports are deferred to the constructor or function that needs them, not the module top level, so importing a module never drags in an extra. `PyMuPDFConverter.__init__` and `TransparencyAnalyzer.analyze()` both follow this pattern.

### Lazy model metadata (Ollama)
`OllamaProvider.list_models()` costs one HTTP request regardless of how many
models are installed. It reads `/api/tags` as raw JSON rather than through
the `ollama` SDK, whose Pydantic model silently drops the per-model
`capabilities` array and `details.context_length`. Most models report their
context length there, so their metadata is complete immediately. For the
rest, `context_window` — and `capabilities.max_context_window` — fetch via a
memoised `show()` call only when read. `__repr__` on those subclasses renders
`<unresolved>` rather than fetching, so logging a model list stays free.
This is the only place in bmlib where attribute access performs I/O. The
returned objects degrade to plain `ModelMetadata` when copied or pickled.
The capability flags (`supports_function_calling`, `supports_vision`) on
`list_models()` results come from `/api/tags` and are a lower bound for
those two flags — `/api/show`, reached via `get_model_metadata()`, reports
a superset (zero violations across 137 comparable models; this is not a
claim about capabilities in general — e.g. `nemotron3:33b-q8` reports
`audio` in `/api/tags` and not in `/api/show`). Code filtering models by
capability should use `get_model_metadata()` when completeness matters —
but `get_model_metadata()` is authoritative only when its `show()` call
succeeds. For a cloud model on a server with cloud disabled, `show()`
returns 403, the error is swallowed, and `get_model_metadata()` falls back
to defaults *weaker* than the listing: every capability flag `False` and
an 8192 context window, versus e.g. `qwen3-next:80b-cloud`'s real
`ctx=262144, tools=True` from `list_models()`.

Bypassing the SDK means the raw path owes back the safety defaults `httpx`
supplied for free, so `_fetch_tags_payload()` builds its own opener rather
than calling `urlopen()`: `urllib` re-sends every header across a redirect,
including the `OLLAMA_API_KEY` bearer token, to any host. `_normalise_base_url()`
likewise restricts the scheme to HTTP(S) — `urlopen` would honour `file://`
and hand the bytes to `json.loads` — and treats `"<word>:<digits>"` as
host:port, since `OLLAMA_HOST` is conventionally scheme-less but `urlsplit`
reads `localhost:11434` as scheme `localhost`. Simplifying any of these back
to the obvious one-liner reintroduces a real defect; each has a regression
test naming it.

### Thread-safe token tracking
`TokenTracker` uses `threading.Lock()` for safe concurrent LLM usage accounting.

## Running Tests

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
```

All tests use in-memory SQLite (`connect_sqlite(":memory:")`) for database tests and mocked HTTP responses for API tests. No external services are required.

`tests/test_backends.py` additionally runs every one of its tests against PostgreSQL when a server is configured — it is the guard against `publications/` drifting back to SQLite-only SQL:

```bash
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/pgrun port=5432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py
```

The DSN must point at a database the tests may drop every table in. Unset, the PostgreSQL half of each test skips.

CI runs this against a `postgres:16` service on every matrix entry and also sets `BMLIB_REQUIRE_POSTGRESQL=1`, which turns that skip into a failure — a DSN that is missing or points at an unreachable server must not leave the PostgreSQL half unrun behind a green build.

## Test file mapping

| Module               | Test file(s)                                               |
|----------------------|------------------------------------------------------------|
| `db/`                | `test_db.py`, `test_migrations.py`, `test_backends.py`     |
| `llm/`               | `test_llm.py`, `test_openai_compat.py`, `test_llm_tools.py`, `test_llm_thinking.py`, `test_llm_embeddings.py`, `test_json_repair.py`, `test_text_utils.py`, `test_json_extraction.py` |
| `agents/`            | `test_agents.py`                                           |
| `quality/`           | `test_quality.py`, `test_cochrane.py`, `test_extractors.py` |
| `templates/`         | `test_templates.py`                                        |
| `transparency/`      | `test_transparency.py`                                     |
| `publications/`      | `test_publications.py`, `test_sync.py`, `test_backends.py`, `test_pubmed_fetcher.py`, `test_openalex_fetcher.py`, `test_registry.py` |
| `fulltext/`          | `test_fulltext_cache.py`, `test_fulltext_models.py`, `test_fulltext_service.py`, `test_jats_parser.py`, `test_pdf_converter.py` |

`scripts/smoke_test_tool_calling.py` is an end-to-end integration runner for tool calling. It hits live providers, so it is not part of the pytest suite — run it manually when changing provider tool-call code.
