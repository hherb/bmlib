# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**bmlib** is a shared Python library for biomedical literature tools, licensed under AGPL-3.0. It provides LLM abstraction, quality assessment, transparency analysis, database utilities, and publication ingestion/sync.

## Development Setup

```bash
uv pip install -e ".[all]"
```

- **Python:** >=3.11
- **Packaging:** pyproject.toml with setuptools
- **Testing:** pytest (`pytest tests/ -v`)
- **Linting/Formatting:** ruff (`ruff check .` / `ruff format .`)
- **Dependencies:** Core requires only jinja2. Optional groups: anthropic, ollama, postgresql, transparency, publications (httpx), dev (pytest, ruff)

## Architecture

Modules under `bmlib/`:
- `db/` — Thin database abstraction (SQLite + PostgreSQL) via pure functions over DB-API connections
- `llm/` — Unified LLM client with pluggable providers (Anthropic, Ollama)
- `templates/` — Jinja2-based prompt template engine
- `agents/` — Base class for LLM-driven tasks
- `quality/` — 3-tier quality assessment pipeline (metadata → LLM classifier → deep assessment)
- `transparency/` — Multi-API transparency analysis (CrossRef, EuropePMC, OpenAlex, ClinicalTrials.gov)
- `publications/` — Publication ingestion, deduplication, storage, and sync tracking

## Coding Conventions

- **Pure functions in reusable modules.** Database operations take a DB-API connection as first argument. Avoid classes where a function suffices. State lives in the caller, not the library.
- **Docstrings required** on all public functions, classes, and modules. Use Google-style or reStructuredText format consistently within a module.
- **Type hints required** on all function signatures (parameters and return types).
- **Unit tests required** for new functionality. Tests go in `tests/` and use pytest. Follow existing test patterns (in-memory SQLite for DB tests, mocked HTTP for API tests).
- **AGPL-3 license header** required at the top of every source file. Copy from any existing file.
- **Dataclass models** with `to_dict()` / `from_dict()` for serialisation. Use `field(default_factory=...)` for mutable defaults.
- **No ORM.** Write explicit SQL. Use `bmlib.db` helpers (`execute`, `fetch_one`, `fetch_all`, `transaction`).
- **Optional dependencies** guarded by `try: import ... except ImportError: raise ImportError("Install with: pip install bmlib[group]")`.
- **ruff** for linting and formatting: line-length=100, target Python 3.11+.

## Running Tests

```bash
pytest tests/ -v
ruff check .
ruff format --check .
```
