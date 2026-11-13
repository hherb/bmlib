# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**bmlib** (v0.13.0) is a shared Python library for biomedical literature tools, licensed under AGPL-3.0-or-later. It provides LLM abstraction, quality assessment, transparency analysis, full-text retrieval, database utilities, and publication ingestion/sync.

**Before "fixing" anything that looks wrong, check [`docs/DECISIONS.md`](docs/DECISIONS.md).** It is the register of deliberate non-fixes — choices that read as bugs, oddities or missed simplifications but were investigated and closed as correct, each with the test that pins it. Reopening one wastes a session. **Before measuring anything, writing an instrument or arguing about a log level, read [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)** — the standing rules a session gets wrong again, each earned by a review. `HANDOVER.md` covers what is still open; `ROADMAP.md` tracks planned work.

**This file is the map and the rules in their shortest form. The full argument behind each one — measurements, populations, review history — is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**, under the same heading. Read the matching section there before changing a module's behaviour. A comment or test that cites "CLAUDE.md" for a figure or an argument means that file (the prose was moved there word for word on 2026-11-09). Put new rationale there, and add a line here only if a session would get something wrong without it.

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
| anthropic      | anthropic>=0.41       | Anthropic Claude LLM provider          |
| ollama         | ollama>=0.5           | Ollama local LLM provider              |
| openai         | openai>=1.0           | OpenAI, DeepSeek, Mistral, Gemini, and OpenAI-compatible providers |
| postgresql     | psycopg2-binary>=2.9  | PostgreSQL database backend            |
| transparency   | httpx>=0.25           | Transparency analysis API calls        |
| publications   | httpx>=0.25           | Publication fetcher API calls           |
| fulltext       | httpx>=0.25           | `FullTextService` retrieval; nothing else in `fulltext/` needs it |
| pdf            | pymupdf>=1.28.2       | PDF → text conversion in `fulltext/`   |
| html           | markupever>=0.4.0, psutil>=5.9 | `canonical_from_html` (html5ever, the parser the Rust port's scraper uses; psutil watches the child process it parses in, #723) |
| embed          | llama-cpp-python>=0.3.36 | `bmlib.embed.Embedder` (arctic-embed-l-v2.0 GGUF, in process); compiles llama.cpp, so not in `all` |
| secrets        | keyring>=25           | `bmlib.secrets.KeyringVault`, API keys in the OS keychain |
| dev            | pytest>=7.0, pytest-cov, ruff, mypy, types-psycopg2, types-psutil | Development and testing tools  |
| all            | All runtime extras but `embed` | Everything except `dev` and `embed` (ask for `.[all,embed]`) |

## Shell & Editing Environment

- This is macOS: `sed` is BSD sed. It does NOT support `\b`, and `-i` needs `-i ''`. `grep` may be aliased to ugrep. For regex renames or multi-file substitutions, use `perl -pi -e` or a small Python script. Do not use sed.
- After any scripted bulk edit, run `git diff --stat` and spot-check the diff. A substitution can half-apply or silently match nothing (for example, indentation mismatches in KDoc).

## Architecture

### Directory structure

```
bmlib/
├── __init__.py              # Package root, exports __version__
├── _atomic.py               # atomic_write() — publish a file so no partial version is visible (private, stdlib only)
├── _strict_json.py          # strict_json_loads() — JSON as serde_json reads it, NaN/1e999/lone surrogates/depth >127 refused (private, stdlib only)
├── _user_agent.py           # user_agent() — the one User-Agent every HTTP request carries (private, stdlib only)
├── agents/                  # LLM-driven task base class and per-agent metrics
│   ├── base.py              # BaseAgent — chat/chat_json, embeddings, JSON parsing
│   └── metrics.py           # PerformanceMetrics — thread-safe per-agent call accounting
├── citations/               # Citation markers, styles, and reference lists (pure stdlib)
│   ├── models.py            # CitationStyle, Citation, DocumentMetadata, FormattedReference
│   ├── parser.py            # [@id:N:Label] marker parsing/replacement as pure functions
│   ├── formatter.py         # Vancouver/APA/Harvard/Chicago + CitationFormatter facade
│   ├── builder.py           # build_references, format_document, find_missing_documents
│   └── passage_match.py     # find_passage — locate an LLM's near-verbatim quote, return the source's own span
├── embed/                   # In-process embedder: constants, vector rules, verified model download, Embedder, chunk store
│   ├── vectors.py           # EMBEDDING_DIM, truncate_and_normalize, query_text, check_texts
│   ├── model.py             # ModelFile, pinned arctic-embed-l-v2.0 GGUFs, verify_model, download_model (cancellable)
│   ├── embedder.py          # Embedder, EmbedError — llama-cpp-python, imported lazily (`embed` extra); Embedder.from_model carries the ModelFile
│   └── store.py             # A paper's chunks + embeddings in bmlib.db (both backends), brute-force cosine, a stale set refused; not re-exported
├── context_processor/       # Hierarchical map-reduce over oversized content
│   ├── base.py              # IterativeContextProcessor ABC — batching, recursion, consolidation
│   ├── data_types.py        # ProcessingConfig, ExtractionResult, ConsolidatedItem, Batch, ProcessingResult, ProgressInfo, strategy enums
│   └── llm_processor.py     # LLMChunkProcessor — extraction via BaseAgent
├── db/                      # Database abstraction (SQLite + PostgreSQL)
│   ├── backend.py           # is_sqlite(), placeholder(), placeholders() — dialect detection
│   ├── connection.py        # connect_sqlite(), connect_postgresql()
│   ├── operations.py        # execute, fetch_one, fetch_all, fetch_scalar, table_exists, create_tables
│   ├── transactions.py      # transaction() context manager, transaction_depth(), owns_commit(), require_transaction_control(), TransactionModeError
│   └── migrations.py        # Migration dataclass, run_migrations()
├── fulltext/                # Full-text retrieval, JATS XML parsing, PDF conversion
│   ├── cache.py             # Disk-based FullTextCache, sanitize_identifier(), renderer-stamped HTML entries, served JATS XML
│   ├── canonical/           # One canonical text per paper (one string, every piece a code-point span), held to Rust by tests/fixtures/canonical_text.json
│   │   ├── model.py         # CanonicalText, TextSpan, assemble() — the one layout every converter hands its drafts to
│   │   ├── jats.py          # JATSArticle → canonical text (what to_html() shows, metadata lines aside)
│   │   ├── markdown.py      # A hand-written Markdown subset → canonical text
│   │   ├── plain.py         # Plain text → canonical text
│   │   ├── pdf.py           # The segmenter's SegmentedDocument → canonical text (a line-end soft hyphen joined)
│   │   ├── html.py          # An HTML page's main content → canonical text (html5ever via markupever, extra `html`), parsed in a child process held to `HTML_PARSE_MAX_BYTES` (#723); Rust counts the elements it creates
│   │   ├── chunk.py         # Sentence-aware retrieval chunks of a canonical text
│   │   └── assess.py        # Does a canonical text look like a whole paper? (heuristic, unmeasured)
│   ├── _parse_audit.py      # Did the JATS parse unwind cleanly? (private, stdlib only)
│   ├── jats_parser.py       # JATS XML → structured data
│   ├── models.py            # FullTextResult, FullTextSourceEntry, JATSArticle, etc.
│   ├── pdf_converter.py     # Pluggable PDF → text (PDFConverter ABC, PyMuPDF backend)
│   ├── segmenter.py         # Heading-driven section segmentation of PDF text lines, page furniture set aside first; match_section_type()
│   ├── _titles.py           # Is the PDF's metadata title the article's title? (private)
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
│   ├── models.py            # Publication, FullTextSource, FetchedRecord, SyncReport, SourceDescriptor, RetractionNature, RetractionNotice, Grant, AuthorAffiliation
│   ├── schema.py            # SQL schema (publications, fulltext_sources, download_days, download_day_parts, retraction_notices, publication_grants, publication_affiliations)
│   ├── storage.py           # Upsert with dedup by DOI/PMID, merge logic
│   ├── sync.py              # Multi-source sync orchestrator
│   ├── retractions.py       # Retraction Watch: parse_retraction_watch_csv, store_retraction_notices, import_retraction_watch_export, lookup_retractions, is_retracted
│   ├── retraction_status.py # One verdict (retracted/concern/clear/unchecked) from the export, Crossref updated-by and the PubMed label; Crossref URL builder + parser, no transport (#504)
│   ├── lookup.py            # lookup_record — one paper's FetchedRecord by PMID/DOI/PMC ID/PPR: PubMed first, OpenAlex always, Europe PMC for a PPR
│   └── fetchers/            # Source fetcher plugins
│       ├── _reconcile.py    # reconcile_delivery — did the walk deliver what the source promised?
│       ├── registry.py      # register_source, get_source, get_fetcher, list_sources
│       ├── pubmed.py        # PubMed E-utilities (esearch + efetch)
│       ├── biorxiv.py       # bioRxiv / medRxiv
│       ├── europepmc.py     # One Europe PMC search hit as a FetchedRecord (lookup_record's, not a sync source)
│       └── openalex.py      # OpenAlex
├── library/                 # A shared folder of research papers several apps read and write (stdlib only; Rust `bmlib::library`, fixtures/library_cases.json)
│   ├── constants.py         # Layout names and limits (marker, papers/, records/, 150-byte folder name)
│   ├── errors.py            # LibraryError and one subclass per Rust variant
│   ├── ids.py               # SourceKind, PaperIds, PaperInfo, Installation, canonical_ids
│   ├── names.py             # folder_name, folder_name_numbered (length fit, then the reserved-device-name prefix)
│   ├── records.py           # parse_record, render_record — one installation's record, versioned
│   ├── files.py             # Every file-system call of the module, and sha256_hex
│   ├── scan.py              # scan_library, find, find_digest, group, read_file; Problem, Scan, PaperFolder, FileRef
│   ├── save.py              # save_file (write-once, then the record), release_file, remove_*; claiming a folder
│   └── library.py           # Library and open_library — the opened folder, delegating to the above
├── quality/                 # tiered quality assessment pipeline (incl. Tier 4 Cochrane) + standalone extractors
│   ├── data_models.py       # StudyDesign enum, QualityTier, BiasRisk, QualityAssessment, QualityFilter
│   ├── manager.py           # QualityManager orchestrator
│   ├── metadata_filter.py   # Tier 1: PubMed metadata → StudyDesign (free)
│   ├── scoring_models.py    # DimensionScore audit-trail models
│   ├── study_classifier.py  # Tier 2: LLM study-design classifier (cheap)
│   ├── quality_agent.py     # Tier 3: deep assessment agent (capable model)
│   ├── cochrane_models.py   # Cochrane 9-domain Risk-of-Bias + study-characteristics models
│   ├── cochrane_formatter.py # Markdown / HTML renderers for the Cochrane tables
│   ├── cochrane_assessor.py   # Cochrane-aligned assessment agent (Tier 4)
│   ├── _json_fields.py      # Read a model's JSON value as its annotated type or as unstated (private)
│   ├── _condense.py         # condense_text — the Cochrane map-reduce over any prompts (private)
│   ├── pico_models.py       # PicoExtraction, PicoPart, PicoSuitability, PicoPrepared, PicoError, part states
│   ├── pico_agent.py        # PicoAgent — suitability check and PICO extraction, quotes verified by find_passage
│   ├── rob2_algorithm.py    # RoB 2 decision rules (2019 guidance tables), pure; Rust rob2_algorithm.rs, contract fixtures/rob2_algorithm.json
│   ├── evidence_ladder.py   # Evidence ladder (RoB + imprecision to a level and weight), pure; Rust evidence_ladder.rs, contract fixtures/evidence_ladder.json
│   ├── design_agent.py      # Design proposal (study design + RCT layout, quotes verified), QueryCondenser seam; Rust design_agent.rs, contract fixtures/design_replies.json
│   ├── outcome_agent.py     # Primary-outcome proposal (outcomes the paper calls primary + reported result, quotes verified); Rust outcome_agent.rs, contract fixtures/outcome_replies.json (scripts/make_outcome_replies.py)
│   ├── rob2_questions.py    # RoB 2 questions in bmlib's own wording (22 ids), per-domain condense queries, escalation question; Rust rob2_questions.rs
│   ├── rob2_agent.py        # RoB 2 assessor (one call per domain, D1 per trial, D2-D4 per outcome, quotes verified, escalation; D5 from facts); Rust rob2_agent.rs, contracts fixtures/rob2_replies.json + rob2_run.json (scripts/make_rob2_fixtures.py)
│   ├── rob2_d5_checks.py    # D5 quote checks: can a found quote show its fact (dated plan, switch, measure, time point), the planning check, shows_result; Rust rob2_d5_checks.rs, contract fixtures/rob2_d5_checks.json
│   ├── rob2_d5_facts.py     # D5 from facts: facts and second-look prompts, readers, D5Facts, d5_answers (the evidence ladder for 5.1-5.3); Rust rob2_d5_facts.rs, contract fixtures/rob2_d5_facts.json (scripts/make_rob2_d5_fixtures.py)
│   ├── evidence_weight.py   # Unattended pipeline (design → outcomes → RoB 2 → ladder → paper weight), `assess_unattended`; Rust evidence_weight.rs, contract fixtures/evidence_weight.json (scripts/make_evidence_weight.py)
│   ├── _full_text_agent.py  # Shared base of the full-text agents (stop, num_ctx, condense-once, ask-until-readable, QueryCondenser); Rust full_text_agent.rs
│   ├── extractors.py        # Rule-based (LLM-free) study-type and sample-size extraction
│   └── scoring_models.py    # DimensionScore / AssessmentDetail audit-trail models
├── secrets.py               # API keys by provider in one OS keychain item (KeyBundle, KeyringVault — keyring on macOS, win32cred / secretstorage on Windows / Linux, extra `secrets`); Rust's twin by tests/fixtures/secret_key_list.json
├── templates/engine.py      # Jinja2 TemplateEngine with user/default dir fallback
└── transparency/            # Multi-API transparency analysis
    ├── analyzer.py          # TransparencyAnalyzer (CrossRef, EuropePMC, OpenAlex, ClinicalTrials.gov)
    └── models.py            # TransparencyResult, TransparencyRisk enum, TransparencySettings
```

### Module notes

The rules a change must not break, per module. The full text, with every measurement behind it, is [`docs/ARCHITECTURE.md` § Module descriptions](docs/ARCHITECTURE.md#module-descriptions).

- **`db/`** — Pure functions over DB-API connections, SQLite (built in) and PostgreSQL (optional), no ORM, explicit SQL. Any module serving both backends gets its placeholder from `placeholder(conn)` / `placeholders(conn, n)`, never a hard-coded `?`.
- **`llm/`** — `LLMClient` router over a lazily registered provider registry; model strings are `"provider:model_name"`. A provider whose SDK is not installed is skipped (probed with `find_spec`, because the SDKs are imported lazily, #303), so `list_providers()` reflects what is installed, and `get_provider()` names the missing extra. Every entry point folds a provider name to lowercase (#302). Embeddings (`embed()` / `embed_batch()`) are Ollama only, via `/api/embed`. Tool calling (`tools` / `tool_choice`), thinking (`think=` → `LLMResponse.thinking`), JSON repair and text chunking live here too. Model listing never fans out per model (see "Lazy model metadata").
- **`templates/`** — **bmlib ships no templates of its own** (no `templates/defaults/`; `package-data` is `py.typed` alone), so `default_dir` is always the caller's directory and its suffix tuple and line endings are a contract. `install_defaults()` copies each file through `atomic_write`, byte for byte, in sorted order. The `if not dest.exists()` skip is correct only *because* the write is atomic. It skips a **dangling symlink** rather than publishing over it.
- **`_user_agent.py`** — The one `User-Agent`, shared by `transparency` (through `_user_agent`, which the sampler and the suite pin) and `publications/sync.py` (#196). Its `python-httpx` token was required by ClinicalTrials.gov (#194) and is harmless since (#606). Only the live sampler checks the header.
- **`_atomic.py`** — `atomic_write()`, private and stdlib only. "Atomic" is about *visibility*, not crash durability: the data is fsync'd, the directory is not.
- **`_strict_json.py`** — Reads only what the Rust port's `serde_json` 1.0.151 reads (#572), and every refusal is a `json.JSONDecodeError`. `watch_refusals()` stops `parse_json`'s fallbacks and `json_mode_reply()` from answering with the text beside a refused value. It lives outside `llm/` because `retraction_status` must not import `bmlib.llm`.
- **`agents/`** — `BaseAgent`: `chat()`, `chat_json()` (retries with backoff, truncation-aware, `retry_context` in every log line; a retry after an unparseable reply carries `JSON_CORRECTION_NOTE`, #717), `render_template()`, `parse_json()`. `embed()` / `embed_batch()` (the `embedding_model` parameter is declared last, for positional stability) are left out of the metrics on purpose. `test_connection()` reports reachability only. `PerformanceMetrics` answers "what did this agent do", independently of the process-wide `TokenTracker`.
- **`citations/`** — Pure stdlib. `[@id:12345:Smith2023]` markers. `build_references()` numbers documents by first appearance, formats Vancouver/APA/Harvard/Chicago (Vancouver runs combined to `[1-3]`), and shows a missing document as a visible placeholder. Metadata is injected as `Mapping[int, DocumentMetadata]`. `find_passage()` returns the source's own span. An exact match never starts or ends inside a word or a source character (#527). A fuzzy match that changes a number, a negation or a direction is rejected with `contradiction` set (#632). Rust is held to it by `tests/fixtures/passage_match.json`.
- **`context_processor/`** — Hierarchical map-reduce. The harness has **no LLM dependency** (that is why the package is top-level). `LLMChunkProcessor` is resolved through a PEP 562 `__getattr__`. The guarantee: no batch handed to `extract_from_batch()` exceeds `max_context_chars` (see "Measured, not assumed"). `process()` holds no per-run state on the instance.
- **`embed/`** — snowflake-arctic-embed-l-v2.0 from bmlib's own GGUFs, held to Rust by `tests/fixtures/embedding_*.json`. `vectors.py` and `model.py` are stdlib only. `Embedder` needs `bmlib[embed]`, which is not in `all`. Deliberate differences from the general rules:
  - `download_model` streams, so it reproduces `atomic_write`'s rules itself: a unique temp name, opened exclusively, fsync'd, renamed only after size and sha256 match. Never use a fixed `.part`.
  - The import guard raises `ModelLoadError`. Every failure is an `EmbedError` subclass mirroring Rust's variants, and all of them pickle.
  - The download sends `bmlib/{version}`, not `user_agent()`.
  - `ModelFile` refuses a non-commit revision, a non-bare file name and a sha256 that is not lowercase hex.
  - `store.py` is imported as `bmlib.embed.store` and never re-exported. It labels every stored set and refuses a stale one. **Bump `EMBEDDING_RECIPE_VERSION` with any change that can move a vector.** Scores are summed left to right by hand, never with `sum()`. Build the embedder with `Embedder.from_model`, because the store refuses one without a `model`.
- **`quality/`** — Tiers: (1) metadata, (2) cheap LLM classifier, (3) deep LLM assessment, (4) Cochrane (`QualityFilter(use_cochrane_assessment=True)`), which enriches Tier 1's or Tier 2's classification and never replaces it. `collapse_risk_of_bias()` maps 9 domains onto 5. **The rule-based extractors and `cochrane_formatter` are standalone**; wiring them in is ROADMAP work. `PicoAgent` sits outside the tiers. Quotes are located by `find_passage`; parts are `UNVERIFIED` or `CONTRADICTED` (#647) and kept; nothing is discarded for low confidence; `prepare()` condenses once (#638). Mean confidence is a hand-rolled left-to-right sum, because `sum()` differs between 3.11 and 3.12+.
- **`transparency/`** — Scores 0-100 from CrossRef, Europe PMC, PubMed, OpenAlex and ClinicalTrials.gov. The rules most often re-broken:
  - **Full text is addressed by the Europe PMC accession alone**, `{EUROPEPMC_REST_BASE}/{accession}/fullTextXML` (#184). The accession is **not a PMCID and must not be normalised into one** (preprints are `PPR…`). Only an id matching `(?:PMC|PPR)\d+`, case-insensitively and with `re.ASCII`, is fetched (#188).
  - The module regexes **raw XML** and never sees `JATSParser` output. `_fetch_europepmc_fulltext` strips `<sub-article>`/`<response>` regions using a stack of names (#119, #160). Every lexer branch opens with a literal `<` *outside* the group, for performance. An unclosed region returns `None` (fall back to the abstract). An unterminated construct raises. A body without `</article>` is refused (presence, not position, #183), and that check runs last.
  - **Every outcome is stored.** `FullTextStatus` (`is_refusal`), `TrialResultsStatus` (`is_answered`) and `PubMedStatus` each split into named sets, and a new member must choose a side (a test asserts the split). `None` means *not recorded*, and no current path writes it. `NOT_SERVED` means only Europe PMC's own "not served": a 404, or a 500 for a record with `isOpenAccess: N` (#457). Any other unserved result is `REQUEST_FAILED`. An undecodable 200 is `UNDECODABLE` (#463). A failed search is `SEARCH_FAILED`, tested with `is None`, not falsiness.
  - **Levels:** DEBUG for the 404 and the closed-access 500, WARNING otherwise, ERROR only for a `_BUG_TYPES` member (bmlib is wrong). Nothing re-raises: a dead API costs one component, never the analysis. The `_ORDINARY_STATUSES` sets are empty *as a measurement*.
  - One `_request` → `_request_json` (returns `dict | None` and WARNs on a non-object) / `_request_text`, and one `_report_swallowed_exception`. A value inside a body goes through `_json_object` / `_json_text` / `_json_count` (which excludes `bool`) / `_json_bool` (a truthy string must not invert an answer). Wrong-typed values are tallied once per analysis. **Only `_epmc_records` walks `resultList`** (an AST test enforces it), and it truncates, never filters, because the index is a rank.
  - COI provenance lines are keyed on `FullTextStatus` and appended after every step, outside `_INDICATORS_RETRACTED_BY_PUBMED_COI`. Each line makes one claim (#203).
  - PubMed: a PMID is sent as canonical ASCII digits (#470; a bad caller PMID raises `ValueError` before any request). A DOI is stripped but not shape-checked (#475). bmlib reads the `<PubmedArticle>` whose own PMID matches, else stores `OTHER_RECORD` (#474). A set of only book records logs DEBUG, an empty set WARNING (#218). A PMID-only lookup is `EXT_ID:{pmid} AND SRC:MED`.
  - Trials: a capped or partly answered check is `PARTLY_ANSWERED` (#206). Accessions are deduplicated at the parser.
  - Industry-funder tokens follow four rules, and rule 4 vetoes the other three. `TestTheStatedCountsAreWhatTheCorpusHolds` re-derives every row in `analyzer.py` and `docs/manual/transparency.md`.
  - URLs and headers are module constants that the API sampler imports. With no API reachable the result is `UNKNOWN` at score 0, and `unknown_reason` says which case.
- **`publications/`** — Ingestion with DOI/PMID dedup, merge-on-upsert and date-range sync. **Read "A completed day is a durable claim" before touching a fetcher's page loop or `sync()`'s status handling.** Both backends. The dialect differences are limited to the DDL, `lastrowid` vs `RETURNING id`, column listing and the day lock (#447). An oversized PubMed day is partitioned into `[EDAT]` ranges and checkpointed into `download_day_parts` (`SourceDescriptor.resumable`). bioRxiv/medRxiv read `/pubs` (#325) and declare `settle_days`. `retractions.py` is standalone: the import refuses a removal above 1%, short rows and missing columns (#509, #510), and only a Retraction or a Reinstatement decides. `retraction_status.py` (#504) returns `clear` only when the export was consulted **and** Crossref served the DOI. Grants and affiliations are child rows; titles and abstracts are Markdown.
- **`library/`** — Plain files, no database. Source files are write-once and named for their digest; each installation writes only its own record (`records/{app}-{id}.json`), so a sync service has nothing to merge. **Nothing is ever deleted automatically**: `release` drops one installation's use of a file, and only `remove_file` / `remove_folder`, on the user's request, delete (removing an absent target is `Missing`). `find` returns the whole linked group. A new folder's name clash is judged by the code under Unicode case folding, not the file system; a numbered name is fitted to 150 bytes; a reserved Windows device name is prefixed after the fit. A folder whose records are all `Newer` fails the save with `NewerRecord`, never a second folder. An iCloud placeholder counts as a user file when opening. Record updates hold a process-wide lock; two processes of one installation are out of scope. A scan keeps every oddity as a `Problem`. The reasoning is in `docs/DECISIONS.md`; Rust is held to Python by `tests/fixtures/library_cases.json` and `tests/fixtures/library/sample/`.
- **`fulltext/`** — Tiered retrieval: caller sources → Europe PMC XML → Europe PMC PDF → Unpaywall → DOI. PDFs are extracted to text unless `convert_pdfs=False` (needs `bmlib[pdf]` and an `identifier`). Only `FullTextService` needs httpx; the package `__init__` resolves it lazily. The rules most often re-broken:
  - **Bump `jats_parser.RENDERER_VERSION` with any change that can move `to_html()`.** `test_renderer_version.py` fails until it is re-pinned. A stale or unstamped cache entry reads as absent; re-fetch first, and keep the stale entry if the re-fetch returns less (#172).
  - A body-less JATS document (`has_body` False) is never cached as full text. `content_kind` says fulltext / abstract / extracted. `jats_xml` returns the bytes as served.
  - Tier 1d takes free PDFs by `availabilityCode` `OA`/`F` (#79), type-checks both fields, and takes `site == Europe_PMC` only (#494).
  - A PDF's metadata title is believed only where page 1 prints it (#56).
  - A caller PMID is canonicalised, and an unusable one is set aside, not raised (unlike `transparency`; see DECISIONS). The cache key is not stripped (#485).
  - **`jats_parser` routes by owner or parent, never by ambient flags or enumerated lists.** Identifiers by declared type (#397). Article metadata at its owner path (#254). `<sub-article>`/`<response>` suppressed structurally (#110), Europe PMC's `associated-data` sec too (#497). Exhibits, contributors, captions and headings are stacks. Labels, graphics, titles and captions are routed by owner (#116, #117, #123-#130). A formula emits one rendition (#147). Unsectioned prose in body/back/front goes to `body_sections`, except `<ref-list>` apparatus (#224, #230). Container headings are recovered lazily (#231). Definitions and footnote markers are folded in with " — " (#228, #124). Cells are isolated at the buffer (#243). Object metadata is declined and `<attrib>` is routed (#241, #248). A citation's text is built from its descendants (#146, #390, #407, #414). Prose citations stay in the sentence (#391). `colspan` is bounded (#129). Affiliations are linked by IDREF, else inherited (#145).
  - **Every drop the parser argues for is counted** and reported once per article at WARNING. **ERROR means only "bmlib is wrong"** (`_parse_audit.py`). **Add a stack or flag to the handler, add it to the audit** (`TestTheAuditNetIsComplete`). Only `_TEXT_ACCUMULATING` elements read the popped buffer (`TestOnlyAnAccumulatingElementReadsTheBuffer`).

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

The full argument for each is in [`docs/ARCHITECTURE.md` § Key design patterns](docs/ARCHITECTURE.md#key-design-patterns), under the same heading.

### Registry pattern (lazy loading)
Both LLM providers and publication fetchers use a module-level `_REGISTRY` dict with `_ensure_builtins()` for lazy registration. New providers/fetchers can be added at runtime via `register_provider()` / `register_source()`.

### Singleton globals with reset
`LLMClient` and `TokenTracker` use module-level singletons (`get_llm_client()`, `get_token_tracker()`) with corresponding `reset_*()` functions for testing.

### DB-API connection threading
All database functions take a connection as the first argument. The `transaction(conn)` context manager handles commit/rollback. No hidden state.

### Composable transactions via savepoints
- A `transaction()` nested inside another joins it with a `SAVEPOINT`. The outermost block owns the commit. `publications.sync()` relies on this for one commit per day.
- PostgreSQL counts bmlib's own open blocks (`transaction_depth()`, keyed by *(thread, `id(conn)`)*) because psycopg2 is INTRANS after a bare `SELECT`. **Anything that commits conditionally asks `owns_commit()`, never the driver.**
- `transaction()` refuses any non-default transaction mode with `TransactionModeError` (#449, `require_transaction_control()`), at entry and again on the outermost exit. **Anything that decides a commit by the nesting rule must refuse the same connections** (`create_tables()` does). `connect_sqlite()` asks for legacy control by name. `sync()` checks at its entry.

### Replace-per-source child rows
- `publication_grants` / `publication_affiliations` carry a `source` column. `_replace_child_rows()` scopes every delete to that source. `sync._stamp_source()` fills the column; fetchers do not.
- A row with no source (including `""`) raises `ValueError`.
- **No UNIQUE constraint on the natural key, on purpose**: NULLs are distinct in a unique index.
- `_consolidate_rows()` relocates **every** child row, per source, before deleting the dropped publication (foreign keys are on).

### A completed day is a durable claim
A `completed` day in `download_days` is not offered again, so a false success loses the day's records for good. Every rule here **fails closed**:
- **A day is over at 12:00 UTC the next day** (#95). Do not simplify this to a date comparison. Judge each day against its own boundary. A `downloaded_at` that is unreadable or in the future fails closed.
- **Reconcile the walk** (`fetchers/_reconcile.py`). A stalled walk is broken outright, and every fetcher must compute it and pass it in. Records delivered against no count cannot complete. A shortfall is judged against `SHORTFALL_FAILURE_RATIO` (0.5, **fixed before measurement**, #92; do not cite it as measured). Never flatten `promised=None` to `0`. A tolerated shortfall goes to `SyncReport.notes`.
- **Count what the server delivered**, by record element name: `<PubmedArticle>` and `<PubmedBookArticle>`, never `<DeleteCitation>`.
- **Check the envelope.** Do not read a body through `.get(..., [])` defaults. bioRxiv refuses only a body with neither `collection` nor messages. Do not "simplify" that to `isinstance(data.get("collection"), list)` (#94).
- `sync()` uses allowlists: an unknown fetcher status is recorded as failed, and anything not `"completed"` is re-offered (`DayStatus`). A record that fails to store fails its day.
- A PubMed day over the 9,999-record session cap is **partitioned into `[EDAT]` ranges** (#96, #105), planned in full, and checkpointed per part in the same transaction as the part's records. Flushing and checkpointing are separate decisions: every part flushes, and only a part that reconciled clean is checkpointed. Of two of bmlib's own counts, the weaker never wins. A single Entrez date over the cap is still refused.
- A late-filling source (`settle_days`, bioRxiv/medRxiv 90) has its non-final rows re-offered whatever the window (rule 5, #325).
- A re-fetch is held to `peak_count` inside the refusal window (#346). Every transaction that writes a day takes `_lock_day` first (#447).
- `DownloadDay.from_dict()` and `PartCheckpoint.from_dict()` refuse to guess and raise a `ValueError` naming the field (#98, #99). `sync()` validates `date_from` / `date_to` / `recheck_days` at its entry, including the type: a `datetime` is a `date`. An empty window or a future window gets a note, not an error.

### Markdown, measured against the markup
`fetchers/pubmed.py` reads titles and abstracts with `_text_with_formatting()`, never `_text()` (which truncates at the first child). Strip once, at the outermost call. A run's edge whitespace is re-emitted outside its markers. An abstract section's label comes from `Label` **or** `NlmCategory`. `_escape_markdown()` escapes ``\ ` * ~ ^`` only (not `_` or `[]`). `<u>` is not mapped.

### Optional dependencies guarded at the call site
- Import an optional dependency in the constructor or function that needs it, never at module top level. No top-level optional import remains.
- **The rule has to hold for the package**: an extra-bearing export in an `__init__.py` goes through a PEP 562 `__getattr__` (`fulltext`, `context_processor`). That `__getattr__` binds what it resolves into `globals()`, and `__dir__` returns `sorted(set(__all__) | set(globals()))`.
- Measure importability with **one fresh interpreter per module** (#64).
- **Return the module from the guard; do not store it on the instance** (pickling). **Report what was raised; do not assert the cause**: interpolate the caught `ImportError` into the message.

### Measured, not assumed, in the batcher
An oversized item is split against a budget derived from the **measured** overflow of a trial split. `TRUNCATE` output is wrapped in `_Preformatted`. Each item is measured at the index it will occupy, so `Batch.total_chars == len(_format_batch_content(batch, config))`. There is no `estimate_item_size()` hook, on purpose. The guarantee is asserted inside `extract_from_batch` (`TestTheContextLimitIsNeverExceeded`).

### A file bmlib writes for a user is published, never written in place
- **A new writer of user-visible files uses `bmlib/_atomic.py`'s `atomic_write()`**: temp file beside the target, `fsync`, `os.replace` (#70, #73). Do not re-derive it.
- **Test a new call site for the publish**: hook `os.replace` and assert the target is still absent at that moment. An error injection alone proves nothing.
- It detects no existing corruption and swallows no `OSError`.
- It replaces a symlink rather than writing through it, so check for one where a symlink is deliberate (`install_defaults()` does; the cache deliberately does not). It re-points `OSError.filename` at the target.

### Lazy model metadata (Ollama)
- `OllamaProvider.list_models()` is one raw `/api/tags` request; the SDK drops `capabilities` and `context_length`. A missing `context_window` is fetched by a memoised `show()` only when read, and `__repr__` shows `<unresolved>`. This is the only attribute access in bmlib that does I/O.
- Listing flags are a lower bound; use `get_model_metadata()` when completeness matters, but it falls back to weak defaults when `show()` fails (e.g. 403 for a cloud model).
- `_fetch_tags_payload()` builds its own opener (no auth header across redirects). `_normalise_base_url()` allows only HTTP(S) and reads `"<word>:<digits>"` as host:port. Each has a regression test; do not simplify back to `urlopen()`.

### Thread-safe token tracking
`TokenTracker` uses `threading.Lock()` for safe concurrent LLM usage accounting.

## Running Tests

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/rerun_rust_oracle.py   # CI runs it: a behaviour change can stale the Rust corpora
```

**`mypy` takes no arguments here — and must not be given any.** Scope and settings live in `pyproject.toml`, so the bare command is what CI runs. **Run it in the dev venv** (`uv pip install -e ".[all,dev]"`). Without the extras installed it reports phantom missing-stub errors, `jinja2` included. Anything deliberately unchecked is an inline `# type: ignore[code]` with its reason, never a per-module `ignore_missing_imports` override (`warn_unused_ignores` can report only the inline form). Full text: [`docs/ARCHITECTURE.md` § mypy](docs/ARCHITECTURE.md#mypy).

All tests use in-memory SQLite (`connect_sqlite(":memory:")`) for database tests and mocked HTTP responses for API tests. No external services are required.

`tests/test_backends.py` additionally runs every one of its tests against PostgreSQL when a server is configured — it is the guard against `publications/` drifting back to SQLite-only SQL:

```bash
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/pgrun port=5432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py
```

The DSN must point at a database the tests may drop every table in. Unset, the PostgreSQL half of each test skips.

CI runs this against a `postgres:18` service on every matrix entry and also sets `BMLIB_REQUIRE_POSTGRESQL=1`, which turns that skip into a failure — a DSN that is missing or points at an unreachable server must not leave the PostgreSQL half unrun behind a green build.

**Lint with the CI-pinned ruff, not the one in `.venv`.** CI pins **0.15.20** (`.github/workflows/ci.yml`); a stale local ruff false-flags rules newer versions removed:

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

`main` carries a `protect_main` ruleset: no deletion, no non-fast-forward push, and **CodeQL code scanning plus code quality required** at the `errors` / `high_or_higher` thresholds. CodeQL runs from GitHub's *default setup*, so there is no workflow file in `.github/workflows/` to read or edit — and its generated workflow does not listen for a pull request's `reopened` action, so a PR that predates the setup needs a fresh commit, not a close/reopen, to get its first analysis. The ruleset says nothing about which merge strategy is used, and neither does anything else — squash, rebase and merge commits are all enabled and all fine. The release recipe no longer depends on which one you press: it tags `main`'s tip after pulling, which is on `main`'s first-parent line under every strategy (issue #78, closed).

## Test file mapping

| Module               | Test file(s)                                               |
|----------------------|------------------------------------------------------------|
| `db/`                | `test_db.py`, `test_migrations.py`, `test_backends.py`     |
| `llm/`               | `test_llm.py`, `test_openai_compat.py`, `test_llm_tools.py`, `test_llm_thinking.py`, `test_anthropic_thinking_contract.py` (the shared `anthropic_thinking.json`, which Rust asserts too), `test_llm_embeddings.py`, `test_json_repair.py`, `test_text_utils.py`, `test_json_extraction.py` |
| `agents/`            | `test_agents.py`                                           |
| `citations/`         | `test_citations_parser.py`, `test_citations_formatter.py`, `test_citations_builder.py`, `test_passage_match.py`, `test_passage_match_contract.py` |
| `embed/`             | `test_embed_vectors.py` (shared `embedding_contract.json`), `test_embed_model.py`, `test_embed_live.py` (the live tests need `BMLIB_EMBED_MODEL`, else skipped; the `Embedder`'s own rules run model-free against a fake llama object), `test_embed_store.py` (both backends, a fake embedder), `test_vector_store_contract.py` (shared `vector_store.json`, written by `scripts/make_vector_store_fixture.py`) |
| `scripts/convert_embedding_model.py` | `test_convert_embedding_model.py` |
| `context_processor/` | `test_context_processor.py`, `test_llm_chunk_processor.py` |
| `quality/`           | `test_quality.py`, `test_cochrane.py`, `test_cochrane_assessor.py`, `test_extractors.py`, `test_quality_narrowing.py` (the `_json_fields` rule and every reader that uses it), `test_pico_agent.py`, `test_extractor_sampler.py` (fails on any edit to `extractors.py` until the committed corpora are re-measured, #368) |
| `library/`           | `test_library_files.py`, `test_library_scan.py`, `test_library_save.py`, `test_library_contract.py` (the shared `library_cases.json` and `library/sample/`; Rust: `tests/library_contract.rs`, `tests/library_save.rs`) |
| `secrets.py`         | `test_secrets.py` (the shared `secret_key_list.json`, and each platform's store faked), `test_secrets_interop.py` (Rust and Python on one real item; CI's `keychain-interop` job, else skipped) |
| `templates/`         | `test_templates.py`                                        |
| `_atomic.py`         | `test_atomic.py` — only what belongs to the helper itself (the 38-char temp-name overhead `fulltext.cache`'s filename cap is arithmetic over, and the exception the caller gets back). The five load-bearing details are pinned at the call sites, where the behaviour is delivered: `test_templates.py::TestInstallingDefaultsIsAtomic` and `test_fulltext_cache.py::TestWritesAreAtomic` |
| `_user_agent.py`     | Pinned at its two call sites, where the header is delivered: `test_transparency.py::test_analyze_sends_it` and `test_sync.py::test_the_client_carries_the_shared_user_agent` |
| `transparency/`      | `test_transparency.py`, `test_funder_matching.py`          |
| `publications/`      | `test_publications.py`, `test_sync.py`, `test_backends.py`, `test_pubmed_fetcher.py`, `test_openalex_fetcher.py`, `test_registry.py`, `test_retractions.py`, `test_retraction_status.py` (the shared contract `tests/fixtures/retraction_status.json`, which the Rust port asserts against too and bmlibrarian_lite is to), `test_fetch_reconciliation.py`, `test_record_lookup.py`, `test_record_lookup_contract.py` (`tests/fixtures/record_lookup.json`, written by `scripts/make_record_lookup_fixture.py`, replayed by Rust), `test_europepmc_normalizer.py` |
| `fulltext/`          | `test_fulltext_cache.py`, `test_fulltext_models.py`, `test_fulltext_service.py`, `test_jats_parser.py`, `test_pdf_converter.py`, `test_segmenter.py`, `test_fulltext_titles.py`, `test_pdf_metadata_titles.py`, `test_renderer_version.py` (pins `RENDERER_VERSION` to a digest of the renderer's code, #172, and each canonical converter's version to its own), `test_canonical_model.py`, `test_canonical_jats.py`, `test_canonical_markdown.py`, `test_canonical_text_contract.py` (the shared `tests/fixtures/canonical_text.json`, cases, refusals and the HTML element lists, which the Rust port asserts too), `test_canonical_html.py`, `test_canonical_assess.py`, `test_pdf_extraction_contract.py` (the shared `tests/fixtures/pdf_extraction.{pdf,json}`, from a PDF's bytes to the blocks and the canonical text, which the Rust port's MuPDF backend asserts too; `make_pdf_extraction_fixture.py` still writes the PDF), `test_pdf_warnings_contract.py` (the shared `tests/fixtures/pdf_warnings.json` and its two damaged PDFs, MuPDF's messages per PDF, which Rust's `pdf_warnings_contract.rs` asserts too; `make_pdf_warnings_fixture.py` must still build them byte for byte), `test_full_text_assessment_contract.py` (the shared `tests/fixtures/full_text_assessment.json`, which the Rust port asserts too, and `make_full_text_assessment.py` still writes it) |
| `fulltext/_parse_audit.py` | `test_parse_audit.py` — the pure half only: what an imbalance *reads as*, given a state. That the state is captured correctly, and that every entry point reaches it, is pinned at the call site in `test_jats_parser.py`, where the behaviour is delivered — a struct agreeing with a capture agreeing with nothing would pass `test_parse_audit.py` in full |
| `scripts/`           | `test_api_failure_sampler.py` (`sample_api_failures.py` only), `test_databank_sampler.py` (`sample_databank_names.py` only), `test_free_pdf_sampler.py` (`sample_free_pdf_urls.py` only), `test_pdf_title_sampler.py` (`sample_pdf_metadata_titles.py` only), `test_efetch_paging_sampler.py` (`sample_efetch_paging.py` only), `test_rerun_rust_oracle.py` (`rerun_rust_oracle.py` only), `test_canonical_chunks_contract.py` (`make_canonical_chunks.py` still builds the chunk fixture), `test_jats_exhibit_sampler.py` (`sample_jats_exhibits.py` only), `test_prose_name_sampler.py` (`measure_jats_prose_names.py` only — each context's fate held to the real parser), `test_extractor_sampler.py` (`sample_extractor_signals.py` only, and the pin holding its three committed corpora to the current `extractors.py`), `test_html_page_sampler.py` (`sample_html_pages.py` only, and its two committed corpora's summaries held to their rows), `test_sampling_helpers.py` (`_sampling.py`) |


## Live samplers

The `scripts/sample_*.py` runners hit live APIs and are the evidence behind curated lists, thresholds and log levels. **Run the matching sampler before changing what it measures.** Each one's full description is in [`docs/ARCHITECTURE.md` § Live samplers](docs/ARCHITECTURE.md#live-samplers). `scripts/` is not a package; tests load each sampler by path and cover it offline through stubbed HTTP.

| Script | Evidence for — run before changing |
|---|---|
| `smoke_test_tool_calling.py` | provider tool-call code (end-to-end, not in pytest) |
| `sample_databank_names.py` | `_TRIAL_REGISTRY_NAMES`, `_DEPOSITION_DATABANK_LEVELS` |
| `sample_free_pdf_urls.py` | `_FREE_PDF_AVAILABILITY_CODES`, PDF-download log levels |
| `sample_pdf_metadata_titles.py` | `looks_like_junk`'s reject-list (`tests/data/pdf_metadata_titles.json`); bump `ROW_DEFINITIONS_VERSION` when a population changes meaning |
| `sample_efetch_paging.py` | `EFETCH_MAX_RETRIEVABLE`, the fixed stride, the partition ladder (`--partition`) |
| `sample_jats_exhibits.py` | `_ARCHIVAL_MIME_SUBTYPES`, `_ARCHIVAL_EXTENSIONS`, `_GRAPHIC_TRANSPARENT_WRAPPERS`, the `<label>` parent test; bump `_COUNTER_DEFINITIONS_VERSION` when a counter is redefined; add every new counter to a generation |
| `sample_api_failures.py` | the `_ORDINARY_STATUSES` sets, the `User-Agent`, #457's 500 reading |
| `sample_extractor_signals.py` | `quality/extractors.py`, run before **and after** a change; `test_extractor_sampler.py` fails until the committed corpora are re-measured (#368) |
| `sample_html_pages.py` | `HTML_ELEMENT_ALLOWANCE` and `ELEMENTS_PER_TAG` (Rust), `HTML_MAX_TAGS`, `HTML_PARSE_MAX_BYTES` — real pages' elements, sizes, `<` counts and the parse child's peak memory, fetched (`tests/data/html_page_shapes.json`, #726) and saved with SingleFile (`--saved-dir`, `tests/data/html_saved_page_shapes.json`, #656) |

Rules the samplers share. These are the ones a new instrument gets wrong:
- A probe that could not be made never prints as a finding. A throttled or failed probe enters no denominator. A population past `UNMEASURED_SHARE_ERROR_THRESHOLD` reports ERROR, is written to `*.unreportable.json`, and makes the exit code non-zero.
- **A sampler does not import the judgement predicate it measures** (a corpus labelled by the rule under test can only confirm it). The one inverse case is `sample_api_failures.py`, which *must* import the analyzer's URLs and headers, because its subject is the request itself. `sample_jats_exhibits.py`'s `_TRANSPARENT_WRAPPERS` is a scope and is pinned as *identical* to the parser's.
- Live Europe PMC draws are stratified (source × year, or by month). A committed corpus records how to re-derive it and which **rendition** it measured.
- `scripts/_sampling.py` holds the shared pacer, the two-ended `Retry-After` clamp, `wilson()` and `is_probeable()`, tested in `tests/test_sampling_helpers.py`. A helper moved there brings its tests with it.
