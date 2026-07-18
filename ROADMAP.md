# Roadmap

This document tracks planned and implemented features for bmlib, grouped by
theme. Details of finished work stay brief — git history and `docs/plans/`
hold the full story.

| Status | Feature | Details |
|--------|---------|---------|
| **Database (`bmlib.db`)** | | |
| ✅ Done | SQLite + PostgreSQL connections | `connect_sqlite()` / `connect_postgresql()`; pure functions over DB-API connections, no ORM |
| ✅ Done | Operations helpers | `execute`, `fetch_one`, `fetch_all`, `fetch_scalar`, `table_exists`, `create_tables` |
| ✅ Done | Transaction context manager | `transaction(conn)`; joining an open SQLite transaction uses a savepoint and defers commit to the enclosing owner (0.3.0 breaking change) |
| ✅ Done | Migration runner | `Migration` dataclass + idempotent `run_migrations()` |
| **LLM (`bmlib.llm`)** | | |
| ✅ Done | Unified client with provider registry | `LLMClient` router, `"provider:model"` strings, lazy built-in registration, `register_provider()` for runtime extension |
| ✅ Done | Seven providers | Anthropic, OpenAI, Ollama, OpenAI-compatible servers, DeepSeek, Mistral, Gemini |
| ✅ Done | Thread-safe token tracking | `TokenTracker` singleton with `threading.Lock()`, `reset_*()` for tests |
| ✅ Done | Defensive `list_models()` cache | Issue #12 — Anthropic and OpenAI-compatible providers return a copy of the cached model list; caller mutation can no longer corrupt the cache |
| ⬜ Planned | Consolidate JSON extraction | Issue #17 — `llm/utils.py::extract_json` and `llm/json_repair.py::extract_and_repair_json` duplicate span-location logic; unify behind one locator (fold into the Phase 1 BaseAgent work) |
| **Agents (`bmlib.agents`)** | | |
| ✅ Done | `BaseAgent` | `chat()`, `chat_json()` with retry and truncation fail-fast, template rendering, message helpers |
| **Templates (`bmlib.templates`)** | | |
| ✅ Done | Jinja2 prompt engine | User directory override with default-directory fallback |
| **Quality (`bmlib.quality`)** | | |
| ✅ Done | 3-tier assessment pipeline | Free metadata filter → cheap LLM study classifier → deep assessment agent; CEBM evidence hierarchy |
| **Transparency (`bmlib.transparency`)** | | |
| ✅ Done | Multi-API analyzer | CrossRef, EuropePMC, OpenAlex, ClinicalTrials.gov → 0–100 score over funding, COI, data availability, trial registration, outcome switching |
| ✅ Done | Industry-COI detection in full text | `_check_europepmc()` returns `industry_coi`; industry ties surfaced from COI statements |
| ✅ Done | Structural COI detection | Issue #13 — a non-blank JATS-tagged COI section counts as `coi_disclosed=True`; the cue-phrase scan is the fallback for untagged text |
| ⬜ Planned | Use or remove `pubmed_api_key` | Issue #18 — `TransparencyAnalyzer` accepts the parameter but never queries NCBI; remove (breaking) or wire it up when a PubMed check is added |
| **Publications (`bmlib.publications`)** | | |
| ✅ Done | Multi-source sync | PubMed, bioRxiv/medRxiv, OpenAlex fetcher plugins with source registry; date-range sync tracking |
| ✅ Done | Dedup + merge-on-upsert | Deduplication by DOI/PMID with field-merge logic |
| ✅ Done | Batched sync commits | One commit per synced day; SQLite write lock no longer held across network I/O (0.3.0) |
| ⬜ Planned | PostgreSQL support for the storage layer | `storage.py` is SQLite-specific (`?` placeholders, `ON CONFLICT`, `cur.lastrowid`); port when a PostgreSQL consumer needs it |
| **Full text (`bmlib.fulltext`)** | | |
| ✅ Done | Multi-tier retrieval | Disk cache → fetcher-provided sources → Europe PMC XML → Unpaywall → DOI resolution |
| ✅ Done | JATS XML parser | JATS → structured `JATSArticle` data |
| **Quality & maintenance** | | |
| ✅ Done | Test suite | 548 tests; in-memory SQLite for DB tests, mocked HTTP for API tests, no external services |
| ✅ Done | Reference manual | `docs/manual/` — one page per module |
| ✅ Done | Documentation refresh for 0.3.0 | README/manual version strings, `transaction()` + migrations, sync write-batching, full-text COI pipeline, new Phase 0 module sections, CLAUDE.md sweep; doc examples executed against the code |
| ⬜ Planned | Release 0.3.0 | CHANGELOG `Unreleased` and documentation are now in sync — tag and package once the docs/fixes PR lands |
