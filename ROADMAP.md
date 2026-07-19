# Roadmap

This document tracks planned and implemented features for bmlib, grouped by
theme. Details of finished work stay brief — git history and `docs/plans/`
hold the full story.

| Status | Feature | Details |
|--------|---------|---------|
| **Database (`bmlib.db`)** | | |
| ✅ Done | SQLite + PostgreSQL connections | `connect_sqlite()` / `connect_postgresql()`; pure functions over DB-API connections, no ORM |
| ✅ Done | Operations helpers | `execute`, `fetch_one`, `fetch_all`, `fetch_scalar`, `table_exists`, `create_tables` |
| ✅ Done | Transaction context manager | `transaction(conn)`; joining an open SQLite transaction uses a savepoint and defers commit to the enclosing owner (0.4.0 breaking change) |
| ✅ Done | Migration runner | `Migration` dataclass + idempotent `run_migrations()` |
| **LLM (`bmlib.llm`)** | | |
| ✅ Done | Unified client with provider registry | `LLMClient` router, `"provider:model"` strings, lazy built-in registration, `register_provider()` for runtime extension |
| ✅ Done | Seven providers | Anthropic, OpenAI, Ollama, OpenAI-compatible servers, DeepSeek, Mistral, Gemini |
| ✅ Done | Thread-safe token tracking | `TokenTracker` singleton with `threading.Lock()`, `reset_*()` for tests |
| ✅ Done | Tool calling | `tools` / `tool_choice` on `chat()`; `LLMToolDefinition`, `LLMToolCall`, `LLMResponse.tool_calls`; Anthropic, Ollama, and OpenAI-compatible providers (0.4.0) |
| ✅ Done | Embeddings | `LLMClient.embed()` → `EmbeddingResponse`; Ollama only — every other provider raises `NotImplementedError` |
| ✅ Done | JSON repair | `json_repair.py` — single quotes, trailing/missing commas, control chars, truncation, unquoted keys; wired into `BaseAgent.parse_json()` |
| ✅ Done | Text chunking | `text_utils.py` — boundary-aware `TextChunker` plus map-reduce and rolling-summary helpers |
| ⬜ Planned | Embeddings beyond Ollama | Only Ollama implements `embed()`; add OpenAI and Gemini backends when a consumer needs them |
| ⬜ Planned | Defensive `list_models()` cache | Issue #12 — cached model list returned by reference; caller mutation corrupts the cache. Return a copy or store a tuple |
| ⬜ Planned | Consolidate JSON extraction | Issue #17 — `llm/utils.py::extract_json` and `llm/json_repair.py::extract_and_repair_json` duplicate span-location logic; unify behind one locator (fold into the Phase 1 BaseAgent work) |
| **Agents (`bmlib.agents`)** | | |
| ✅ Done | `BaseAgent` | `chat()`, `chat_json()` with retry and truncation fail-fast, template rendering, message helpers |
| **Templates (`bmlib.templates`)** | | |
| ✅ Done | Jinja2 prompt engine | User directory override with default-directory fallback |
| **Quality (`bmlib.quality`)** | | |
| ✅ Done | 3-tier assessment pipeline | Free metadata filter → cheap LLM study classifier → deep assessment agent; CEBM evidence hierarchy |
| ✅ Done | Cochrane risk-of-bias models | 9-domain RoB + study-characteristics table + Markdown/HTML renderers (0.4.0) |
| ✅ Done | Rule-based extractors | LLM-free study-type and sample-size scoring with `DimensionScore` audit trails (0.4.0) |
| ⬜ Planned | Wire the new quality tools into the pipeline | The Cochrane models and extractors are standalone — nothing in the tiered pipeline imports them, and no `BiasRisk` ↔ `CochraneRiskOfBias` or `DimensionScore` ↔ `QualityAssessment` conversion exists. Decide whether extractors become a free pre-filter ahead of Tier 1 and whether Tier 3 emits Cochrane domains |
| ⬜ Planned | PubMed fetcher must populate `publication_types` | Tier 1 classifies from that field, but `fetch_pubmed` never sets it, so synced PubMed records skip the free tier entirely — see HANDOVER.md |
| **Transparency (`bmlib.transparency`)** | | |
| ✅ Done | Multi-API analyzer | CrossRef, Europe PMC, OpenAlex, ClinicalTrials.gov → 0–100 score over funding, COI, data availability, trial registration, open access |
| ✅ Done | Industry-COI detection in full text | `_check_europepmc()` returns `industry_coi`; industry ties surfaced from COI statements |
| ✅ Done | Unreachable-API guard | No API reachable → `UNKNOWN` at score 0, so a dead network no longer reads as a HIGH-risk paper (0.4.0) |
| ⬜ Planned | Thread-safe analyzer | `_last_request` / `_api_reachable` are unsynchronised while `max_concurrent_analyses` invites concurrency — see HANDOVER.md |
| ⬜ Planned | Implement or drop the advisory settings | `enabled`, `filtering_enabled`, `max_concurrent_analyses`, `cache_results` are read by nothing; `outcome_switching_detected` is never assigned |
| ⬜ Planned | Structural COI detection | Issue #13 — a tagged COI section without a cue phrase must count as `coi_disclosed=True`; cue-phrase scan becomes the fallback |
| **Publications (`bmlib.publications`)** | | |
| ✅ Done | Multi-source sync | PubMed, bioRxiv/medRxiv, OpenAlex fetcher plugins with source registry; date-range sync tracking |
| ✅ Done | Dedup + merge-on-upsert | Deduplication by DOI/PMID with field-merge logic |
| ✅ Done | Batched sync commits | One commit per synced day; SQLite write lock no longer held across network I/O (0.4.0) |
| ⬜ Planned | PostgreSQL support for the storage layer | `storage.py` is SQLite-specific (`?` placeholders, `ON CONFLICT`, `cur.lastrowid`); port when a PostgreSQL consumer needs it |
| **Full text (`bmlib.fulltext`)** | | |
| ✅ Done | Tiered retrieval | Caller-supplied sources → Europe PMC XML → Europe PMC PDF → Unpaywall → DOI/PubMed URL, with disk-based caching |
| ✅ Done | JATS XML parser | JATS → structured `JATSArticle` data; external entity loading disabled |
| ✅ Done | PDF → text conversion | Pluggable `PDFConverter` with a PyMuPDF backend behind the optional `bmlib[pdf]` extra (0.4.0) |
| ⬜ Planned | Wire PDF conversion into `FullTextService` | The converter is standalone; the service downloads and caches PDF bytes but never converts them |
| ⬜ Planned | Rate limiting | The package throttles nothing — bulk callers must self-throttle against Europe PMC and Unpaywall |
| **Quality & maintenance** | | |
| ✅ Done | Test suite | 539 tests + 2 skipped; in-memory SQLite for DB tests, mocked HTTP for API tests, no external services |
| ✅ Done | Reference manual | `docs/manual/` — one page per module |
| ✅ Done | Documentation refresh for 0.4.0 | CHANGELOG, README, CLAUDE.md and all eight manual pages rewritten against the real source, signatures verified and examples executed |
| ✅ Done | Release 0.4.0 | Cut 2026-07-19; 0.3.0 was bumped in-tree but never released, so its changes ship inside 0.4.0 |
