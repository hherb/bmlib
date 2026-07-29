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
| ✅ Done | Consolidate JSON extraction | Issue #17 — one locator, `iter_json_spans()`, yields candidate spans in six priority stages without validating; `extract_json()` and `extract_and_repair_json()` are acceptance policies over it, dropping ~70 lines of duplicated fence and brace scanning. A fenced candidate outranks the object preference, and the repair path suppresses the nested-object stage so it cannot return a fragment of a span it rejected (unreleased) |
| ✅ Done | Field-level JSON salvage | `salvage_json_fields()` — when a long structured response is malformed in one place, recover the fields that are intact instead of losing all of it. Generalises biasbuster's `lenient_extract`; at most one repair pass per key, at the last match (unreleased) |
| **Agents (`bmlib.agents`)** | | |
| ✅ Done | `BaseAgent` | `chat()`, `chat_json()` with retry and truncation fail-fast, template rendering, message helpers |
| ✅ Done | Per-agent performance metrics | `PerformanceMetrics` — thread-safe token/request/retry/timing accounting, independent of the global `TokenTracker`; `agent.metrics` returns a snapshot. Upstream's model-inference timers are omitted: no provider reports them through bmlib, so they would be permanently zero (unreleased) |
| ✅ Done | Agent-level embeddings and connection test | `BaseAgent.embed()` / `embed_batch()` / `test_connection()` plus the `embedding_model` parameter, declared last so positional construction stays stable. Embeddings are excluded from the metrics — `tokens_per_second` is about generation (unreleased) |
| ✅ Done | Diagnosable JSON failures | `chat_json(retry_context=...)` labels every retry and failure message with the task; `parse_json()` warns when the repair stage was what rescued a response, since repair closes brackets and a truncated response can parse into a valid but incomplete object (unreleased) |
| ⬜ Planned | `parse_json`'s return contract | Issue #33 — **designed, not yet implemented**, see `docs/superpowers/specs/2026-07-29-json-parse-contract-design.md`. Widen to `dict \| list` with an opt-in `require_dict` that retries inside `chat_json()`, and demote `extract_json()`'s nested-object stage to a last resort — an unfenced `[{"a": 1}, {"b": 2}]` in prose currently returns `{"a": 1}` and drops the sibling with no error |
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
| ✅ Done | PubMed E-utilities step | Issue #18 — `pubmed_api_key` was accepted and never read. One `efetch` per analysis (skipped without a PMID, which it takes from the Europe PMC record already fetched) supplies `<CoiStatement>`, `<DataBankList>` registrations and `<GrantList>` funders — structured signals Europe PMC cannot give for a closed-access paper, and the first funder signal a PMID-only analysis has ever had. The key moves requests from NCBI's 3/s per-IP bucket to the key's 10/s one; it does not change bmlib's own pacing (unreleased) |
| ✅ Done | Structured `UNKNOWN` reason | Issue #21 — `TransparencyUnknownReason` (DISABLED / NO_IDENTIFIER / UNREACHABLE) on `TransparencyResult.unknown_reason`, set if and only if `risk_level` is UNKNOWN. Round-trips by value; a dict without the key loads as `None`. Declared last on the dataclass for positional stability (unreleased) |
| ⬜ Planned | Data deposition from `<DataBankList>` | PubMed also lists GENBANK/PDB/SRA/Dryad accessions — structured proof of data sharing, stronger than the current substring scan. Left out of the PubMed step to keep the data-availability scoring path untouched; `_parse_pubmed_signals()` already walks the databanks |
| ⬜ Planned | Word-boundary industry-funder matching | Issue #36 — `_INDUSTRY_KEYWORDS` is a substring test, so `"inc."` keeps its dot to avoid matching `"Lincoln"` and therefore misses `"Pfizer Inc"`. Always true of CrossRef names; the PubMed agency corpus makes it bite more often. A `\binc\b\.?`-style test fixes every case but shifts detection on the existing corpus, and `industry_funding_detected` feeds a HIGH-risk rule — needs measuring against both corpora |
| ⬜ Planned | Replace `analyze()`'s accumulator tuples | Issue #37 — eight accumulators are threaded through 4-to-6-element tuples where element order is the only thing binding a value to its name. Nothing is broken; a mutable `_Analysis` dataclass mutated in place would remove the arity. Worth doing before the next signal source lands |
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
| ✅ Done | Figure and table captions in every shape | Caption body (`<p>`) and lead (`<title>`) are routed on the enclosing `<caption>`, not on whichever `in_*` flag is set, so a `<fig>` inside a `<sec>` — the ordinary PMC layout — keeps its caption instead of blanking it, spilling it into the section's prose, and renaming the section. Figure and table internals no longer leak into `body_sections` or `has_body` (unreleased) |
| ⬜ Planned | Rate limiting | The package throttles nothing — bulk callers must self-throttle against Europe PMC and Unpaywall |
| **Quality (`bmlib.quality`) — robustness** | | |
| ✅ Done | Survive a missing abstract | A `None` abstract from a nullable column was sliced unguarded in both LLM tiers, so one record took the whole scoring batch down. With title *and* abstract missing the tiers return `unclassified()` without calling the model (unreleased) |
| ✅ Done | Tier 2 token budget is settable | `classify()` repeated `temperature`/`max_tokens` at the call site, silently overriding the constructor; the 256-token ceiling truncated small local models' preamble and lost the JSON with it (unreleased) |
| **Quality & maintenance** | | |
| ✅ Done | Test suite | 962 tests + 32 skipped; in-memory SQLite for DB tests, mocked HTTP for API tests, no external services. 30 skips are the PostgreSQL parameterisations of `test_backends.py`, which need `BMLIB_TEST_POSTGRESQL_DSN`; 2 need PyMuPDF |
| ✅ Done | Reference manual | `docs/manual/` — one page per module |
| ✅ Done | Documentation refresh for 0.4.0 | CHANGELOG, README, CLAUDE.md and all eight manual pages rewritten against the real source, signatures verified and examples executed |
| ✅ Done | Deduplicate `docs/manual/fulltext.md` | Issue #31 — the `## PDF Conversion` section appeared twice with overlapping, non-identical content; the two are merged, and the copy that wrongly called the converter standalone is gone (unreleased) |
| ✅ Done | Fix `docs/manual/transparency.md` self-contradiction | The constructor section said "do not share one analyzer across threads" — guidance from before 0.4.0 made it thread-safe — while the concurrency section recommended exactly that. Stale sentence gone; a twice-stated COI-window limitation merged (unreleased) |
| ✅ Done | Release 0.4.0 | Cut 2026-07-19; 0.3.0 was bumped in-tree but never released, so its changes ship inside 0.4.0 |
| ✅ Done | Release 0.5.0 | Cut 2026-07-20; batch embeddings and cross-provider thinking support. Carries one breaking change — Ollama embeddings moved to `/api/embed`, which returns L2-normalised vectors |
| ✅ Done | Release 0.5.1 | Cut 2026-07-21; single-request Ollama `list_models()`, entirely within `ollama.py` |
| ⬜ Planned | Cut the next release | `[Unreleased]` has accumulated three bodies of work — PostgreSQL `publications`, PDF→text wiring, body-less JATS. All additive, so a minor bump fits |
