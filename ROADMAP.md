# Roadmap

This document tracks planned and implemented features for bmlib, grouped by
theme. Details of finished work stay brief — git history and `docs/plans/`
hold the full story.

| Status | Feature | Details |
|--------|---------|---------|
| **Database (`bmlib.db`)** | | |
| ✅ Done | SQLite + PostgreSQL connections | `connect_sqlite()` / `connect_postgresql()`; pure functions over DB-API connections, no ORM |
| ✅ Done | Operations helpers | `execute`, `fetch_one`, `fetch_all`, `fetch_scalar`, `table_exists`, `create_tables` |
| ✅ Done | Transaction context manager | `transaction(conn)`; joining an open transaction uses a savepoint and defers commit to the enclosing owner (0.4.0 breaking change on SQLite; extended to PostgreSQL, unreleased) |
| ✅ Done | Migration runner | `Migration` dataclass + idempotent `run_migrations()` |
| ✅ Done | Public backend detection | `is_sqlite()`, `placeholder()`, `placeholders()` promoted out of `db/migrations.py`; plus `transaction_depth()` / `owns_commit()` for code that commits conditionally (unreleased) |
| **LLM (`bmlib.llm`)** | | |
| ✅ Done | Unified client with provider registry | `LLMClient` router, `"provider:model"` strings, lazy built-in registration, `register_provider()` for runtime extension |
| ✅ Done | Seven providers | Anthropic, OpenAI, Ollama, OpenAI-compatible servers, DeepSeek, Mistral, Gemini |
| ✅ Done | Thread-safe token tracking | `TokenTracker` singleton with `threading.Lock()`, `reset_*()` for tests |
| ✅ Done | Tool calling | `tools` / `tool_choice` on `chat()`; `LLMToolDefinition`, `LLMToolCall`, `LLMResponse.tool_calls`; Anthropic, Ollama, and OpenAI-compatible providers (0.4.0) |
| ✅ Done | Embeddings | `LLMClient.embed()` → `EmbeddingResponse`; Ollama only — every other provider raises `NotImplementedError` |
| ✅ Done | Batch embeddings | `LLMClient.embed_batch()` → `BatchEmbeddingResponse`; bounded batching (`max_batch_size`, Ollama default 256) for ~7.6× on bulk corpora. Moved Ollama embeddings onto `/api/embed`, which returns L2-normalised vectors — breaking for non-cosine distance metrics (0.5.0) |
| ✅ Done | JSON repair | `json_repair.py` — single quotes, trailing/missing commas, control chars, truncation, unquoted keys; wired into `BaseAgent.parse_json()` |
| ✅ Done | Text chunking | `text_utils.py` — boundary-aware `TextChunker` plus map-reduce and rolling-summary helpers |
| ✅ Done | Defensive `list_models()` cache | Issue #12 — Anthropic and OpenAI-compatible providers return a copy of the cached model list; caller mutation can no longer corrupt the cache |
| ✅ Done | Single-request Ollama `list_models()` | Reads `/api/tags` as raw JSON — the SDK's Pydantic model silently drops the `capabilities` array and `details.context_length` — so 139 models cost one request (~3.6 s → ~46 ms) and capability flags populate for the first time. Context windows not carried in the listing resolve lazily behind a memoised `show()`. Adds the TTL cache + `force_refresh` Ollama alone lacked. The raw path re-implements the SDK's HTTP safety defaults: HTTP(S)-only scheme, and the bearer token stripped across cross-origin redirects (0.5.1) |
| ✅ Done | Thinking/reasoning support | Cross-provider `think` kwarg on `chat()` (`bool` / effort string / `int` budget) mapped to each provider's native parameter; trace returned in `LLMResponse.thinking` (0.5.0) |
| ⬜ Planned | Round-trip Anthropic thinking blocks in tool loops | Extended thinking + multi-turn tool use fails on the follow-up request: the API requires the original `thinking` blocks (with signatures) re-sent in the assistant turn, but the message converter drops them and `LLMResponse.thinking` is a plain string. Needs signature-preserving block storage |
| ⬜ Planned | Embeddings beyond Ollama | Only Ollama implements `embed()`; add OpenAI and Gemini backends when a consumer needs them |
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
| **Transparency (`bmlib.transparency`)** | | |
| ✅ Done | Multi-API analyzer | CrossRef, Europe PMC, OpenAlex, ClinicalTrials.gov → 0–100 score over funding, COI, data availability, trial registration, open access |
| ✅ Done | Industry-COI detection in full text | `_check_europepmc()` returns `industry_coi`; industry ties surfaced from COI statements |
| ✅ Done | Unreachable-API guard | No API reachable → `UNKNOWN` at score 0, so a dead network no longer reads as a HIGH-risk paper (0.4.0) |
| ✅ Done | Thread-safe analyzer | Mutex-guarded rate limiting (shared, throttles a shared API) and thread-local reachability (per-analysis), so one instance can be shared across workers (0.4.0) |
| ✅ Done | Settings ownership made explicit | `enabled` is now honoured (short-circuits before the `httpx` import); `filtering_enabled`, `max_concurrent_analyses` and `cache_results` are documented as caller-owned orchestration hints (0.4.0) |
| ✅ Done | Structural COI detection | Issue #13 — a non-blank JATS-tagged COI section counts as `coi_disclosed=True` even without a cue phrase; the cue-phrase scan is the fallback for untagged text |
| ⬜ Planned | Outcome-switching detection | `outcome_switching_detected` is reserved and always `False`. Requires comparing a trial's pre-registered primary outcomes against those reported |
| ⬜ Planned | Use or remove `pubmed_api_key` | Issue #18 — `TransparencyAnalyzer` accepts the parameter but never queries NCBI; remove (breaking) or wire it up when a PubMed check is added. The manual documents it as accepted-but-unused |
| ⬜ Planned | Structured `UNKNOWN` reason | Issue #21 — disabled / no-identifier / unreachable are only distinguishable by `risk_indicators` string matching; add an `unknown_reason` enum when a consumer needs to branch on the cause |
| **Publications (`bmlib.publications`)** | | |
| ✅ Done | Multi-source sync | PubMed, bioRxiv/medRxiv, OpenAlex fetcher plugins with source registry; date-range sync tracking. `register_source()` can override a built-in name (0.4.0) |
| ✅ Done | Dedup + merge-on-upsert | Deduplication by DOI/PMID with field-merge logic |
| ✅ Done | PubMed fetcher populates `publication_types` | Parsed from `PublicationTypeList`, so synced PubMed records reach the free Tier 1 filter instead of falling through to the paid LLM classifier (0.4.0) |
| ✅ Done | Batched sync commits | One commit per synced day; SQLite write lock no longer held across network I/O (0.4.0) |
| ✅ Done | PostgreSQL support for the storage layer | `schema.py`, `storage.py` and `sync.py` are dual-dialect; `ensure_schema()` picks the matching DDL. `tests/test_backends.py` runs each test against both backends, and CI fails rather than skips when the DSN is missing (unreleased) |
| ✅ Done | `publications.pmcid` | A fetcher's PMC id was dropped on store, so full-text retrieval could not use it. Declared last on the dataclass to keep positional construction stable (unreleased) |
| **Full text (`bmlib.fulltext`)** | | |
| ✅ Done | Tiered retrieval | Caller-supplied sources → Europe PMC XML → Europe PMC PDF → Unpaywall → DOI/PubMed URL, with disk-based caching |
| ✅ Done | JATS XML parser | JATS → structured `JATSArticle` data; external entity loading disabled. `parse_with_html()` gets both in one SAX pass |
| ✅ Done | PDF → text conversion | Pluggable `PDFConverter` with a PyMuPDF backend behind the optional `bmlib[pdf]` extra (0.4.0) |
| ✅ Done | Wire PDF conversion into `FullTextService` | A retrieved PDF is extracted into `FullTextResult.html` via `render_html()`; `convert_pdfs=False` opts out (unreleased) |
| ✅ Done | Body-less JATS detection | medRxiv serves `<front>`+`<back>` with no prose for some preprints; detected via `JATSArticle.has_body`, never cached, held back as a last resort so the chain keeps looking. `FullTextResult.content_kind` tells the caller what it got (unreleased) |
| ✅ Done | Parse an unsectioned `<body>` | Issue #30 — `<sec>` is optional in JATS, so loose `<p>` prose now becomes a titleless `JATSBodySection`, flushed at each `<sec>` boundary to keep document order and stop real sections nesting inside it. It counts towards `has_body`, ending the permanent cache miss (unreleased) |
| ⬜ Planned | Rate limiting | The package throttles nothing — bulk callers must self-throttle against Europe PMC and Unpaywall |
| **Quality (`bmlib.quality`) — robustness** | | |
| ✅ Done | Survive a missing abstract | A `None` abstract from a nullable column was sliced unguarded in both LLM tiers, so one record took the whole scoring batch down. With title *and* abstract missing the tiers return `unclassified()` without calling the model (unreleased) |
| ✅ Done | Tier 2 token budget is settable | `classify()` repeated `temperature`/`max_tokens` at the call site, silently overriding the constructor; the 256-token ceiling truncated small local models' preamble and lost the JSON with it (unreleased) |
| **Quality & maintenance** | | |
| ✅ Done | Test suite | 818 tests + 31 skipped; in-memory SQLite for DB tests, mocked HTTP for API tests, no external services. The skips are the PostgreSQL parameterisations of `test_backends.py`, which need `BMLIB_TEST_POSTGRESQL_DSN` |
| ✅ Done | Reference manual | `docs/manual/` — one page per module |
| ✅ Done | Documentation refresh for 0.4.0 | CHANGELOG, README, CLAUDE.md and all eight manual pages rewritten against the real source, signatures verified and examples executed |
| ✅ Done | Deduplicate `docs/manual/fulltext.md` | Issue #31 — the `## PDF Conversion` section appeared twice with overlapping, non-identical content; the two are merged, and the copy that wrongly called the converter standalone is gone (unreleased) |
| ✅ Done | Release 0.4.0 | Cut 2026-07-19; 0.3.0 was bumped in-tree but never released, so its changes ship inside 0.4.0 |
| ✅ Done | Release 0.5.0 | Cut 2026-07-20; batch embeddings and cross-provider thinking support. Carries one breaking change — Ollama embeddings moved to `/api/embed`, which returns L2-normalised vectors |
| ✅ Done | Release 0.5.1 | Cut 2026-07-21; single-request Ollama `list_models()`, entirely within `ollama.py` |
| ⬜ Planned | Cut the next release | `[Unreleased]` has accumulated three bodies of work — PostgreSQL `publications`, PDF→text wiring, body-less JATS. All additive, so a minor bump fits |
