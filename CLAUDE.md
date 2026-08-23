# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**bmlib** (v0.10.0) is a shared Python library for biomedical literature tools, licensed under AGPL-3.0-or-later. It provides LLM abstraction, quality assessment, transparency analysis, full-text retrieval, database utilities, and publication ingestion/sync.

**Before "fixing" anything that looks wrong, check [`docs/DECISIONS.md`](docs/DECISIONS.md).** It is the register of deliberate non-fixes — choices that read as bugs, oddities or missed simplifications but were investigated and closed as correct, each with the test that pins it. Reopening one wastes a session. `HANDOVER.md` covers what is still open; `ROADMAP.md` tracks planned work.

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
| fulltext       | httpx>=0.25           | `FullTextService` retrieval; nothing else in `fulltext/` needs it |
| pdf            | pymupdf>=1.28.2       | PDF → text conversion in `fulltext/`   |
| dev            | pytest>=7.0, pytest-cov, ruff, mypy, types-psycopg2 | Development and testing tools  |
| all            | All runtime extras    | Everything except `dev`                |

## Architecture

### Directory structure

```
bmlib/
├── __init__.py              # Package root, exports __version__
├── _atomic.py               # atomic_write() — publish a file so no partial version is visible (private, stdlib only)
├── agents/                  # LLM-driven task base class and per-agent metrics
│   ├── base.py              # BaseAgent — chat/chat_json, embeddings, JSON parsing
│   └── metrics.py           # PerformanceMetrics — thread-safe per-agent call accounting
├── citations/               # Citation markers, styles, and reference lists (pure stdlib)
│   ├── models.py            # CitationStyle, Citation, DocumentMetadata, FormattedReference
│   ├── parser.py            # [@id:N:Label] marker parsing/replacement as pure functions
│   ├── formatter.py         # Vancouver/APA/Harvard/Chicago + CitationFormatter facade
│   └── builder.py           # build_references, format_document, find_missing_documents
├── context_processor/       # Hierarchical map-reduce over oversized content
│   ├── base.py              # IterativeContextProcessor ABC — batching, recursion, consolidation
│   ├── data_types.py        # ProcessingConfig, ExtractionResult, ConsolidatedItem, Batch, ProcessingResult, ProgressInfo, strategy enums
│   └── llm_processor.py     # LLMChunkProcessor — extraction via BaseAgent
├── db/                      # Database abstraction (SQLite + PostgreSQL)
│   ├── backend.py           # is_sqlite(), placeholder(), placeholders() — dialect detection
│   ├── connection.py        # connect_sqlite(), connect_postgresql()
│   ├── operations.py        # execute, fetch_one, fetch_all, fetch_scalar, table_exists, create_tables
│   ├── transactions.py      # transaction() context manager, transaction_depth(), owns_commit()
│   └── migrations.py        # Migration dataclass, run_migrations()
├── fulltext/                # Full-text retrieval, JATS XML parsing, PDF conversion
│   ├── cache.py             # Disk-based FullTextCache, sanitize_identifier()
│   ├── _parse_audit.py      # Did the JATS parse unwind cleanly? (private, stdlib only)
│   ├── jats_parser.py       # JATS XML → structured data
│   ├── models.py            # FullTextResult, FullTextSourceEntry, JATSArticle, etc.
│   ├── pdf_converter.py     # Pluggable PDF → text (PDFConverter ABC, PyMuPDF backend)
│   ├── segmenter.py         # Heading-driven section segmentation of PDF text lines
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
│   ├── retractions.py       # Retraction Watch: parse_retraction_watch_csv, store_retraction_notices, lookup_retractions, is_retracted
│   └── fetchers/            # Source fetcher plugins
│       ├── _reconcile.py    # reconcile_delivery — did the walk deliver what the source promised?
│       ├── registry.py      # register_source, get_source, get_fetcher, list_sources
│       ├── pubmed.py        # PubMed E-utilities (esearch + efetch)
│       ├── biorxiv.py       # bioRxiv / medRxiv
│       └── openalex.py      # OpenAlex
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
- **`templates/`** — Jinja2-based prompt template engine with user directory override and default directory fallback. **bmlib ships no templates of its own** — there is no `templates/defaults/`, and `package-data` is `py.typed` alone — so `default_dir` is always the caller's own prompt directory and its suffix tuple and line endings are a contract, not an internal detail. `install_defaults()` copies each one through `_atomic.atomic_write`, byte for byte, in sorted order — see "A file bmlib writes for a user is published, never written in place" below, and note that the `if not dest.exists()` skip is only correct *because* the write is atomic. It skips a **dangling symlink** rather than publishing over it: `exists()` follows symlinks, so one whose target is missing reads as absent, and `os.replace` replaces the link where the `write_text` it replaced wrote through it — the atomic publish is what created that hazard, not what fixed it.
- **`_atomic.py`** — one private, stdlib-only helper, `atomic_write()`, shared by `fulltext/cache.py` and `templates/engine.py`. Not part of the public API. "Atomic" is about *visibility*, not crash durability: the data is fsync'd before the rename is issued, but the containing directory is not, so a lost rename leaves the target absent — the direction both callers repair.
- **`agents/`** — `BaseAgent` class for LLM-driven tasks. Provides `chat()`, `chat_json()` (retry with backoff, truncation-aware, `retry_context` label folded into every log line), `render_template()`, `parse_json()`, and message helpers. `embed()` / `embed_batch()` wrap the client's embedding calls (via the `embedding_model` constructor parameter, declared last for positional stability) and are deliberately excluded from the metrics below; `test_connection()` reports provider reachability only, not whether a given model is installed. `agents/metrics.py` provides `PerformanceMetrics`, thread-safe per-agent call accounting (tokens, requests, retries, wall time) surfaced via `BaseAgent.metrics` / `reset_metrics()` / `start_metrics()` / `stop_metrics()` / `format_metrics_report()` — independent of the process-wide `llm.TokenTracker`, since it answers "what did this agent do" rather than "what has this process spent".
- **`citations/`** — Citation-marker parsing and reference building, pure
  stdlib. Text carries `[@id:12345:Smith2023]` markers; `build_references()`
  numbers the cited documents by order of first appearance, formats
  references in Vancouver, APA, Harvard, or Chicago style, replaces markers
  with `[N]` (Vancouver, adjacent runs combined to `[1-3]`) or the style's
  author–date inline citation, and reports a missing document as a visible
  placeholder rather than dropping it. Metadata is injected as
  `Mapping[int, DocumentMetadata]` — the upstream DB fetch was severed in
  the port.
- **`context_processor/`** — Hierarchical map-reduce for content that exceeds one context window: batch the items to fit, extract from each batch, feed the extractions back in as items, repeat until they fit. `IterativeContextProcessor` is the harness and has **no LLM dependency** — which is why it is a top-level package rather than living under `agents/`; only `LLMChunkProcessor` imports `BaseAgent`, and the package `__init__` resolves it through a PEP 562 `__getattr__` so that claim holds of the package and not merely of `base.py` (eager re-export pulled in `bmlib.templates` and jinja2, over half the import cost, for callers who only wanted the harness). `bmlib.llm.text_utils.process_with_map_reduce()` is the shallow case of the same idea (one map, one reduce, one string) and stays; this module uses that module's `TextChunker` when it splits an oversized item. `max_context_chars` is the guarantee the module makes — no batch handed to `extract_from_batch()` exceeds it — and the port from bmlibrarian fixed two separate ways upstream broke it (see "Measured, not assumed, in the batcher" below). `process()` holds no per-run state on the instance, so one processor can serve concurrent calls.
- **`quality/`** — Tiered quality assessment: (1) free metadata classification, (2) cheap LLM classifier, (3) deep LLM assessment, (4) Cochrane-aligned assessment. Uses CEBM evidence hierarchy for quality tiers. `CochraneAssessor` (Tier 4, behind `QualityFilter(use_cochrane_assessment=True)`) produces `cochrane_models`' nine-domain `CochraneRiskOfBias` and study-characteristics table from a title and text; `collapse_risk_of_bias()` bridges the nine domains onto the five-domain `BiasRisk`; and `QualityManager` reaches both of these behind that same flag, enriching a classification rather than replacing it — Tier 1's when the metadata was conclusive, Tier 2's when it was not, since a Cochrane assessment supplies no `study_design` of its own and a preprint carries no PubMed publication types to classify from. **The rule-based extractors and `cochrane_formatter` are still standalone**: nothing in the tiered pipeline imports them, and there is no conversion between `DimensionScore` and `QualityAssessment`. Wiring the extractors in as a free pre-filter ahead of Tier 1 is open work — see ROADMAP.md.
- **`transparency/`** — Queries CrossRef, Europe PMC (search + full text), PubMed, OpenAlex, and ClinicalTrials.gov to compute a transparency score (0-100) covering funding, COI, data availability, trial registration, and open access. The PubMed step is one `efetch` per analysis, skipped without a PMID (taken from the caller or from the Europe PMC record already fetched); it supplies structured `<CoiStatement>`, `<DataBankList>` and `<GrantList>` signals that Europe PMC cannot give for a closed-access paper, and `pubmed_api_key` rides on it. When no API is reachable the result is `UNKNOWN` at score 0, so an unreachable network does not masquerade as a HIGH-risk paper; `TransparencyResult.unknown_reason` says which of the three `UNKNOWN` cases it was, set if and only if `risk_level` is `UNKNOWN`.
- **`publications/`** — Publication ingestion from multiple sources (PubMed, bioRxiv, medRxiv, OpenAlex) with deduplication by DOI/PMID, merge-on-upsert, and date-range sync tracking. Every fetcher reconciles what its source delivered against the count that source promised (`fetchers/_reconcile.py`) and refuses a malformed envelope rather than reading it as an empty day — see "A completed day is a durable claim" below, which is the one place to read before touching a fetcher's page loop or `sync()`'s status handling. A PubMed day too large for one history session is not refused but **partitioned into Entrez-date ranges that each fit**, walked part by part and checkpointed into `download_day_parts` so an interrupted run resumes; `SourceDescriptor.resumable` (default `False`) is what lets `sync()` hand a fetcher the per-part keywords without breaking a third-party one. Runs on both backends `db/` supports: placeholders come from `db.placeholder()`, `ensure_schema()` picks the matching DDL, and the one irreducibly dialect-specific need — reading back an inserted row's id — is `cur.lastrowid` on SQLite and `RETURNING id` on PostgreSQL. Everything else is written in the intersection of the two dialects. `tests/test_backends.py` runs each test against both. `retractions.py` is a standalone module, not a fetcher: `parse_retraction_watch_csv()` streams the Crossref-distributed Retraction Watch export into `RetractionNotice` records, `store_retraction_notices()` upserts them idempotently on Retraction Watch's own `record_id`, and `lookup_retractions()` plus the pure `is_retracted()` answer "is this paper retracted?" — with only a Retraction or a Reinstatement deciding, since a later Correction does not undo an earlier Retraction. The PubMed fetcher also extracts `<GrantList>` grants and `<AffiliationInfo>` affiliations into `Grant` / `AuthorAffiliation` child rows (tables `publication_grants` / `publication_affiliations`, read back with `get_grants()` / `get_author_affiliations()`), and reads titles and abstracts as Markdown — see "Replace-if-nonempty child rows" and "Markdown, measured against the markup" below.
- **`fulltext/`** — Tiered full-text retrieval (caller-supplied sources → Europe PMC XML → Europe PMC PDF → Unpaywall → DOI/PubMed URL) with JATS XML parsing and disk-based caching. PDF→text conversion lives here too, and `FullTextService` calls it: a retrieved PDF is extracted into `FullTextResult.html` (opt out with `FullTextService(convert_pdfs=False)`, needs `bmlib[pdf]`). Extraction only runs once the PDF is cached, so it needs an `identifier`. A body-less JATS document — `<front>`+`<back>` with no article prose, which medRxiv serves for some preprints — is detected via `JATSArticle.has_body`, never cached, and held back as a last resort so the chain keeps looking for the real article. `FullTextResult.content_kind` tells the caller which of `fulltext` / `abstract` / `extracted` it actually got, so an abstract is not analysed as if it were an article. `SectionSegmenter` (in `segmenter.py`) segments the `TextBlock` lines from `PyMuPDFConverter.extract_blocks()` — an optional capability declared by the `LayoutExtractor` protocol, not by the `PDFConverter` ABC — into a `SegmentedDocument` of typed sections. One block per PDF *line* with dominant-span font attributes, because span-level extraction shattered mixed-font headings; front matter is kept as a section rather than dropped; standalone for now — nothing in `fulltext` or `quality` calls it yet. Only `FullTextService` needs an extra (`bmlib[fulltext]`, httpx); the package `__init__` resolves it through a PEP 562 `__getattr__` so the parser, the models and the segmenter import on core bmlib alone — see "Optional dependencies guarded at the call site". Tier 1d's free-PDF check (`_entry_is_free`) allow-lists Europe PMC's `fullTextUrlList` on `availabilityCode` (`OA`, `F`), falling back to the `availability` display string only for an entry carrying no code; a present-but-unknown code is rejected without consulting the label. Measured over 600 MEDLINE records, `"Open access"`/`OA` is 95.7% of free-PDF entries and `"Free"`/`F` is the other 4.3% — accepting only the `"Free"` label, as the code did before issue #79, silently discarded the large majority of the PDFs the tier exists to find. Both access fields are type-checked before being compared: `x in frozenset` *hashes* `x`, and the resulting `TypeError` on a JSON object is a `_BUG_TYPES` member, so a malformed payload would be reported as a bmlib defect rather than as an entry to skip — and would spend the one-shot `bug:TypeError` slot a later real defect needs. `_extract_free_pdf_url` checks the container one level up for the same reason: `.get("fullTextUrl", [])` returns `None`, not `[]`, for a key present with a null. A PDF's **metadata title is believed only where page 1 prints it** (`_titles.py`, issue #56): real `/Title` values are typesetter job numbers and source filenames, and one used to beat a perfectly good large-font line. `PyMuPDFConverter` puts the judged answer in `ConversionResult.title` and `SectionSegmenter._extract_title` prefers it over the font-size heuristic, while `metadata["title"]` stays verbatim. **Run `scripts/sample_pdf_metadata_titles.py` before changing the reject-list in `looks_like_junk`** — every member has to be earned from `tests/data/pdf_metadata_titles.json`, and the one member left no longer clears a row corroboration does not, so it is kept as defence-in-depth and says so. Containment is anchored to whole tokens: an unanchored substring test accepts a `/Title` truncated mid-word, which is both a false accept and worse than the fallback it beats. In `jats_parser`, **an identifier is read from the type the document declares, not from its shape**: `_classify_article_id` is the fallback for an `<article-id>` whose `pub-id-type` is absent or unrecognised, and every branch of it defers to a value that arrived typed — otherwise document order decides, and SAGE's `pub-id-type="publisher-id"` (the DOI with the slash replaced by an underscore, emitted *after* the real one on every article it publishes) overwrote the DOI. Two independent guards, since neither is sufficient alone: a typed DOI is authoritative and the fallback may not replace it — which is what settles a *well-formed* companion or collection DOI that no shape test could reject — and the fallback requires a `10.` prefix **and** a slash, so the underscore form fails on its own merits in a document carrying no typed DOI at all. The same "already set" test now guards `pmc_id`, which the fallback could overwrite despite the typed branch and `known_pmc_id` both being first-wins. In `jats_parser`, **a nested article is not this one and a contributor's role may be declared on the group**. `<sub-article>`/`<response>` open a suppressed region in which no handler fires (issue #110 — PLOS was observed depositing each peer-review round that way, and the last round's DOI, title and ~180 paragraphs of reviewer prose were the article's). The suppression is **structural, never by `article-type`**: that attribute is `CDATA #IMPLIED`, its four published vocabularies disagree, and publishers deposit values in none of them, so no allow-list could decide it — and peer review is not the only use, since `<sub-article>` also carries SciELO's `article-type="translation"` full text, meeting abstracts, and Europe PMC's own injected `associated-data` block (absent from PMC's copy of the same record). The set of two elements is complete for the same structural reason: exactly three JATS elements admit `<front>` and `<body>`, and the third is `<article>`. It is counted as a *depth* because JATS nests them, and applied to the opening tags as well as the closing ones, since an open leaves state behind — a nested `<sec>` that never closes cost an article its whole body, and a nested `<fig>`/`<table-wrap>` leaves a flag that swallows the rest of the parse. The **closing** half is load-bearing on an ordinarily-ordered document too: `</abstract>` flushes without clearing (only the suppressed open clears), so a nested one re-emits the article's own abstract twice, and `<article-id>` falls through to the shape-matching fallback. `characters()` is guarded in its own right, being delivered by neither handler. Because none of this changes `has_body` unless it takes the whole body, `JATSArticle.suppressed_nested_articles` reports the count — 288 of 1,022 sampled articles lose body text with `has_body` flipping on none of them. It does **not** protect `bmlib.transparency`, which regexes the raw XML itself; that is issue #119.

Beside it, `<contrib-group content-type="author">` with bare children is read as authors (issue #111 — the dominant form in PMC, so reading only `contrib-type` dropped every author from 57% of open-access articles). Five rules, of which the sample earns **two**: a contributor's own `contrib-type` decides on its own, and any other declared role is taken at its word. The other three rest on convention, #111's sample carrying no instance of any — a group declaring nothing inherits the group enclosing it (authors at the outermost level), an empty attribute counts as no declaration, and the comparison folds case, which the JATS Tag Library itself recommends for attribute values (written of `@article-type`, so precedent rather than citation), matches this module's folding of `pub-id-type`, and cannot cost anything since an unfolded `Author` drops a whole group while no casing of another role is ever accepted. The enclosing role is a **stack**, because `<collab>` may contain a `<contrib-group>` — a collaboration's member roster — and held as one value its close cleared the enclosing group's declaration, collecting an `editor` group's own members as this article's authors.

And **an exhibit is a stack, and its own content is routed by the element that owns it**. `<fig>` and `<table-wrap>` both nest — eLife wraps every figure supplement inside the figure it belongs to, PMC8754430 returned 9 of 12. The original survey put nesting at 19.6% of 225 articles; `scripts/sample_jats_exhibits.py` re-measured at **0.7%** of a general open-access draw (2 of 276 articles, and 0 of a 300-article stratified draw), both of them eLife — so nesting is an eLife house style affecting about half of *that publisher's* figures, not a 19.6% general convention — the two articles that do nest lost 6 of 12 and 5 of 11 figures — and JATS lets a `<table-wrap>` open inside another's `<table-wrap-foot>`, so each is a stack of `_ExhibitFrame` and never a single slot, which the inner open overwrote and the inner close emitted and cleared, losing the parent outright (issue #115). Two halves, and a test that only counts exhibits catches neither: `in_figure`/`current_figure`/`in_table_wrap`/`current_table` are **derived** from the stacks rather than stored, since the flag the inner close cleared is what read the rest of the parent as article prose; and the list entry is **reserved when the element opens and filled when it closes**, since an exhibit is built at its end tag but has to be listed at its start — pop-and-append restores the parent and still lists every supplement ahead of it. **Test the derived flags with prose *after* the exhibit closes**: every fixture stopped at the close, so deriving either flag from the *slot* list instead of the stack — a five-character edit — survived the whole suite while swallowing every later paragraph and section title. Nesting measures 0 in *both* committed 300-article draws, recent and back-filled, so the 0.7% rests on the vanished 276-article one alone. Caption text used to go to the **innermost** open exhibit — never "the figure if any is open, else the table", since exhibits nest both ways round and the ambient-flag order handed an inner table's legend to the figure enclosing it — but `_innermost_exhibit` and `_ExhibitFrame.open_seq` are gone as of #123: a `<caption>` is a direct child of what it describes, so its parent names the owner exactly where "innermost" was merely usually right, and caption routing was that helper's only caller. 

A `<label>` and a `<graphic>` are routed by their **owner**, not by the ambient flags, and the two rules replaced two separate enumerations that could not be completed by inspection. A `<label>` is a direct child of the exhibit it numbers, so `element_stack[-2]` decides outright (the `<article-id>` idiom, two handlers away). That began as a footnote-depth counter — a `<table-wrap-foot><fn>`'s `a`/`b`/`*` marker overwrote the number for 12.0% of the same 225 articles, so PMC12661592's one table was labelled `a` (issue #116) — but the depth needed `_FOOTNOTE_CONTAINERS` to list every container whose `<label>` is not the exhibit's, and an `<fn-group>` directly inside a `<fig>`, a `<disp-formula>`'s `(1)`, a `<media>`'s `Video 1` and eLife's `<supplementary-material>` `Figure 1-source data 1` each still overwrote it. The parent test needs no list, and is exact where the depth was merely close: an exhibit opened *inside* a footnote keeps its own label either way. A `<graphic>` is the same question one element wider — it may sit inside `<alternatives>` (several encodings of one image) or a `<p>` (prose flow that holds an image without owning it), so `_graphic_owner` walks up past those two and takes the first element it finds; everything else is opaque, so a container this module has never heard of keeps its own image rather than donating it. Asking `current_figure` instead donated a nested `<table-wrap>`/`<fn>`/`<supplementary-material>`'s image to the enclosing figure, which the ranking below then made permanent. A table's own `<graphic>` is kept in `JATSTableInfo.graphic_url` and ranked by the same rule a figure's is — one `_GraphicHolder`, not two copies, because two copies of a rule this heavily argued are two things to keep in step (issue #127). **The table side measures empty, and #135 is answered rather than open**: across the two committed draws — 755 `<table-wrap>` in 600 articles — 16 carry a `<graphic>` of their own and **not one carries two**, so ranking and plain first-wins agree there and the rule is *unexercised* on tables rather than confirmed. Do not restate it as publisher behaviour. Getting that number needed the instrument fixed first: the sampler counted a table's deposits by whole-subtree walk while the parser routes by **owner**, and unscoped, four of ten recent tables "carried several" — every one a `<td>` cell image from two articles. **That population is invisible in a recent draw and concentrated in an older one**: 0 of 662 tables published in the last two years against 11 of 93 from 1996-1998, and those 11 sit in 2 articles of 300, both from one journal, where they are *every* table the article has (6 of 6 and 5 of 5). So it is a per-deposit property like eLife's nesting rather than a rate — and where it fires the article loses its whole tabular content, which for a clinical paper is its data. Rendering is the one place the two exhibits differ: an `<img>` is emitted only where there is no `<table>`, since a `<table-wrap>` may carry both and the markup is then the better rendition — the model holds the href either way, though `FullTextService` discards the `JATSArticle` and caches only the rendered HTML, so for a service consumer that choice is permanent. How often a table carries both is now measured on the same two windows: 5 of 662 recent (0.8%) and 0 of 93 back-filled, against 0 and 11 image-only — so the discarded rendition and the kept one live in opposite windows. Footnote prose is #124. 

Among several `<graphic>`, the deposits are **ranked** (`ARCHIVAL < THUMBNAIL < FULL`) and one is accepted only when **strictly** better, so the first wins among equals: 58.0% of the 959 surveyed figures *that carry a `<graphic>` at all* carry more than one and 52.9% end on a thumbnail, so "keep the last" resolved most figures to a preview — while "keep the first", correct for every article measured, inverts wherever an `<alternatives>` master is deposited first (issue #117). `thumb` is matched as a lowercased substring of `content-type` **or** `specific-use`. **The two extension rules are deliberately asymmetric**: a thumbnail is never inferred from the extension, since every corpus thumbnail is a `.gif` only because PLOS and Springer both deposit that way and an extension rule would discard the only image a figure has elsewhere — but an archival master *is*, because `mime-subtype` is optional and an undeclared TIFF deposited first ranked `FULL` and beat the JPEG after it. What makes inferring safe there is that a first deposit is accepted whatever its rank, so the demotion can only break a tie, never empty a figure. `scripts/sample_jats_exhibits.py` is the evidence for all of it — **run it before changing any of these rules**.

One of them measures **empty**, and the comments say so rather than implying a population: of 1,819 `<graphic>` inside an `<alternatives>` over 276 open-access articles — and 1,329 more over the two committed draws — **none declares a `mime-subtype` and none is archival by either test**, so the whole `ARCHIVAL` rank is unreached. It is kept because what it prevents is silent and permanent, and "no instance" is not "cannot happen"; the `<p>` member of the transparent set is *not* defensive, since without it a figure whose graphic sits in prose flow loses it outright. **The second one moved, and matters more than it did.** The 276-article draw found exactly one `<graphic>` owned by a non-exhibit inside an exhibit, which read as a population of one; the recent committed draw finds **36, in 3 of 300 articles, every one a `<td>`** — and since #127 gave `JATSTableInfo` a `graphic_url`, relaxing ownership lands those cell decorations in it as if they were the table's own rendition. So that rule is measured as load-bearing, not carried against a hypothetical. By contrast the parent rule's premise measures **full** in all three draws: 2,033 / 2,033, 1,446 / 1,446 and 365 / 365 exhibits carry a direct-child `<label>` and carry one anywhere, so no exhibit carries its label only indirectly. The depth rule it replaced would still mis-assign 35 labels in 2 of the 276 articles, both as corruptions rather than blanks. Re-measuring #117 gives **49.9%** carrying several graphics and **49.5%** ending on a thumbnail on that sample, **52.8%** / **52.4%** on the recent draw and **60.9%** / **59.8%** on the back-filled one, against the 58.0% / 52.9% cited from the original — and **0%** depositing a thumbnail first in every one of them.

And **a `<title>` and a `<caption>` are routed by their owner too** (issues #125, #130, #123), which is the same rule one element further out. `<sec>` is far from the only element carrying a `<title>`: `<fn-group>` is modelled `(label?, title?, (fn|p)+)`, and `<ref-list>`, `<glossary>`, `<app>`, `<boxed-text>` and every `<caption>` carry one, so asking only "is a section open?" let any of them rename it — leaving not a blank but a heading the publisher never wrote, which is why it survived. **Measured, and not small**: 69 titles in 31 of 300 recent articles (10.3% [7.4-14.3]), every one owned by a `<caption>` — what owns that `<caption>` is not recorded, the sampler counting the `<title>`'s immediate parent alone — and 13 in 1 of 300 back-filled articles owned by a **`<list>`** — a container neither issue names and no enumeration written from them would have covered, which is the parent test's whole argument. #125's own `<fn-group>` shape is in neither window and reproduces on eLife's PMC8754430, so the rate is a floor. A `<caption>`'s prose is a **stack of owners**: held as a stored boolean, the inner `</caption>` truncated the enclosing caption at the point the inner one ended, and a depth fixes only that half, since the inner legend's owner is not an exhibit bmlib models. **Both of that half's populations measure empty** — no `<caption>` nests inside another (0 of 1,550 recent, 0 of 288 back-filled) and every `<caption>` inside an exhibit is owned by that exhibit, in both draws and in the seven-article Swift corpus — while the premise it rests on measures **full**, 1,413 / 1,413 and 288 / 288 carrying a direct-child `<caption>`. A usurping title is **dropped, not relocated**: it was never a heading, and bmlib models none of the containers that carry one — though only the `<title>`, since a section-level `<caption>`'s `<p>` children never enter the exhibit branch and still reach the section's prose (issue #137). The **exhibit test the parent rule replaced on the section branch has to stay on the abstract branch**: JATS admits a `<fig>` and a `<table-wrap>` in an `<abstract>`, and the old `if in_figure or in_table_wrap:` opening the whole `<title>` arm swallowed every title inside one, so without an explicit guard a `<table-wrap-foot><fn-group><title>` there flushes the pending abstract section and installs itself as the next heading — #125 one branch over, and the worse half of it, since `abstract_sections` is rendered into the HTML `FullTextService` caches while `body_sections` reaches no bmlib path at all. That population measures **empty**: 44 exhibits inside an `<abstract>` across the two draws, none carrying a `<title>`. The sampler's own walk descends into `<sub-article>`, which the parser suppresses, so every counter here is a whole-document count — measured for the cited population at 69 outside and 0 inside, and issue #138 is the scope-and-redraw.

Every rule above is a rule about *routing*, and each was found from outside bmlib after the fact. So **the parse is audited when it ends** (issue #134, `_parse_audit.py`): `_run_parser()` — the one place `parse`, `to_html` and `parse_with_html` funnel through — reads the handler's stacks, depths and flags into a frozen `ParseUnwindState` and logs one ERROR per imbalance, each naming what the imbalance *cost*. It is a **net, not an input check**: expat rejects an unbalanced *document*, so nothing a publisher deposits can reach these predicates and every line is a claim that bmlib itself is wrong — which is what fixes the level at ERROR. Nothing raises, since a partial article reported loudly beats no article. It is also **prospective, and the comments say so**: #115, #123 and #130 are stack-handling defects but would each have unwound *clean* and left no residue — which is precisely why all three went undetected until they were found from outside bmlib — so the one genuine precedent is the Swift port's stranded footnote counter. Do not restate those three as defects this net would have caught.

Four rules on it. **Every field defaults to its clean value**, which is what lets a test name only the imbalance it is about, and why `excess_text_buffers` counts the excess and not the depth — `text_stack` always holds one buffer, so the raw length would read every clean parse as broken (mutation-measured: 221 tests). **Add a stack or a flag to the handler, add it here**; an incomplete net is what lets the next one hide, which is why the audit is its own module rather than a helper inside a 2,000-line parser — and that rule is now *mechanised*, since prose alone had already let it slip: `implicit_body_section` shipped missing from `_ROUTING_FLAGS`, so a stranded unsectioned `<body>` lost its whole prose with `has_body` still True and the audit silent. `TestTheAuditNetIsComplete` walks the handler's own attributes and fails on any reaching neither the audit nor a *named* exclusion. And **a predicate must be measured against well-formed input before it ships**: `current_abstract_text` *and* `current_abstract_title` are both excluded because `</abstract>` flushes without clearing — only a *subsequent* `<abstract>` open clears, the suppressed one being the one that does not — so both are non-empty at the end of every article carrying a titled abstract. What caught the first is the autouse `parser_log` fixture in `tests/test_jats_parser.py`, which fails any test in that module whose parse emits an ERROR — making all 186 pre-existing fixtures a false-positive check without being written as one, and confirmed to have teeth by two named mutants costing 36 and 221 tests. The fourth rule is the same one from the other side: **ERROR must mean only "bmlib is wrong"**, so a predicate that fires on a correct parse is a defect in the audit. `current_article_id_type` was set unconditionally and cleared conditionally, so a JATS-invalid `<article-id>` outside `<article-meta>` made the audit accuse a perfectly-parsed article.

Beside it, **a parse that yields no authors reports which kind it is** (#121): `front_contributor_name_count` is gated on `in_front`, a structural fact, and never on `in_contrib`, which is set only once `_is_author_contrib` has said yes — the routing decision #111 got wrong, so a counter keyed on it goes to zero in exactly the situation it exists to detect. That one is **WARNING and not ERROR**, because it can fire on a document bmlib parsed correctly (`PMC12803704`, genuinely author-less and still carrying `<front>` surnames, 1 of 1,025 measured during the Swift port and not reproducible from a committed corpus), and the audit's net depends on ERROR meaning only "bmlib is wrong". **It counts every JATS spelling of a contributor's name** — `<surname>`, `<string-name>` and `<collab>` — because a `<contrib>` is named with `(name | string-name | collab | …)` while bmlib, when this counter was written, extracted only `<name>`: counting surnames alone, a `<string-name>`-only article (100% of its authors lost, #140) and a `<collab>`-only one (#120) were both certified *genuinely author-less*, which is the exact silence #121 exists to end. Both are extracted now (see the next paragraph), so neither reaches the detector by that route any longer — and the counter is deliberately **not** narrowed to match, since it counts the *spelling* and not the extraction, which is what keeps it able to report the next contributor bmlib fails to collect. Counting is not parsing, and the quiet branch reports the evidence it has rather than a conclusion.

And **a name that arrives undivided is kept undivided** (#120, #140). `<collab>` names a group and `<string-name>` a person the depositor did not split; each gets a field of its own on `JATSAuthorInfo` rather than being folded into `surname`, which is sorted and de-duplicated on and where an organisation is indistinguishable from a person. Verbatim in both cases, because splitting *"Ahmed Al-Rashid"* is a decision about particles, multi-word surnames and name order — assumed rather than measured, and undetectable by the caller once it is sitting in `surname`. `full_name` prefers a structured name over both, since a `<contrib>` carrying a `<name>` *and* a `<collab>` is *"Smith, on behalf of the Y Group"*. Four things the extraction needed beyond the two fields, three of them the same shapes this module has already been caught by. **`<contrib>` is a stack and `in_contrib`/`current_author` are derived from it**, because a `<collab>` may carry a `<contrib-group>` of the collaboration's own members: held as one slot each member overwrote the consortium's builder and its close cleared the flag, which is #115 one element family over — and a *non-author* `<contrib>` pushes a `None` frame, since skipping the push lets an editor's end tag pop the author's own, and reading the nearest builder rather than the top of the stack writes that editor's surname into the consortium. **A contributor is listed where its `<contrib>` opened**, the exhibits' reserve-and-fill, or a consortium is listed behind the members it encloses; the reservation is *given back* when the `<contrib>` names nobody, so an unfilled slot keeps meaning "never closed" and cannot make the audit ERROR on the well-formed `<anonymous/>`. **Both undivided spellings accumulate a buffer and merge it back, except inside a `<contrib>`** — accumulating so the close reads its own text and not the ancestor's, inline so a `<mixed-citation>` printing one keeps that name in the citation string it renders, and body prose keeps a name printed in it. But the nearest accumulating ancestor of a *roster member* is the enclosing `<collab>`, so an unconditional merge appended every member to the consortium's own name — *"the INHERIT Trial GroupJane Q SmithAhmed Al-Rashid"*, silently, in the exact shape #120 exists to collect. `_UNDIVIDED_NAME_ELEMENTS` refuses the merge while any `<contrib>` is open: the owner test in its usual form, the `<contrib>` owning the name. `collab` had to join `string-name` in `_INLINE_ELEMENTS` at the same time — it was accumulating and not inline, so every consortium-authored reference lost its author from the rendered citation, which is the defect the `string-name` entry beside it was added to avoid, one line up in the same set. **The `<contrib>` field is filled only when no structured name arrived**, since JATS lets `<string-name>` carry `<surname>` and `<given-names>` children and the buffer then holds only the punctuation between them; testing `surname` alone short-circuits, so the guard reads `given_names` too. **The reference branch is gated on the whole citation**, exactly as `<collab>`'s is: JATS admits either spelling as a direct child of `<mixed-citation>`, and the narrower `in_ref_person_group` dropped a cited name that was sitting in the markup. There the same divided shape needs a *flush* rather than a refusal — appending the buffer put a bare `","` in `references[].authors` ahead of the name, and only `</name>` and `</person-group>` finish a pending cited author, neither of which closes between two adjacent `<string-name>`, so the first of two collapsed onto the second. A `<contrib>` from which no name could be read is **counted and reported once per article at WARNING** from `_audit_parse`, which is where `rejected_spans` settled the same question (#129): a per-`<contrib>` DEBUG was too quiet to be #120's other half, was 200 identical lines on an author list of 200 `<xref>`-only contribs, and — emitted from `endElement` — named an article whose `<article-id>` had not been read yet. It says *bmlib read no name*, never *the document carried none*: `<on-behalf-of>` is a fourth spelling, JATS-legal and unextracted (#144), and an article naming its only contributor that way reached the **quiet** branch of the zero-author detector until that spelling was added to `front_contributor_name_count`. Counting is not parsing, and the counter must not let the detector conclude past it. `JATSAuthorInfo.is_named` is where "did any spelling arrive?" is defined — on the public type, so `build()` is a gate and not a second definition, and deliberately **not** a raising `__post_init__`, which is #129 exactly: an exception out of a SAX callback into `service.py`'s tier-level `except Exception` costs the whole article. The rule is spec-driven rather than drawn: `scripts/sample_jats_exhibits.py` gained the counters (section 11) and **has not been re-run**, so how much of a corpus each spelling reaches is *unmeasured here*. The only figure is #120's own — 34 of 1,025 articles losing at least one contributor, from the #118 review rather than a committed corpus — and it counted `<contrib>` elements carrying **no `<surname>`**, a set both spellings share, so it is a rate for neither of them alone. Do not quote it for `<collab>` or for `<string-name>`; the redraw belongs with #132 and #138. Section 11's vocabulary is deliberately **open** — every non-excluded child of a `<contrib>` is counted under its own name, and only the annotation consults a set — because counted against a closed list an unforeseen spelling falls into `(none)` and is reported as a contributor naming nobody, which is #121's mis-certification inside the instrument built to detect the next #120.

And **a `<mixed-citation>` is mixed content, so every descendant's text is its own** (#146). `JATSReferenceInfo.citation` is built from that element's buffer, and a child accumulating a buffer without merging it back has its text *taken and not returned* — `<person-group>`, `<article-title>`, `<source>`, `<year>`, `<volume>`, `<issue>`, `<fpage>`, `<lpage>` and `<pub-id>` all do, which is the whole of a standard NLM deposit, so the rendered string was the punctuation between them: `'. . . ;():-. doi: .'`. **The rule is a property of the context, not of the element**, which is why `_INLINE_ELEMENTS` — right for `<collab>` and `<string-name>`, which carry a name wherever they appear — cannot serve: an `<article-title>` in `<article-meta>` is the article's own title, and merging it unconditionally appends it to whatever buffer is open. So it is an **ancestor** test, not the parent test the module usually makes, because mixed content is inherited down the whole subtree and each merge composes into the one above. `<element-citation>` is excluded and leaves `citation` **empty** — element-only content authored no string, the whitespace between its children is insignificant, and assembling one is a citation-style decision `formatted_citation` already makes — but **excluding it from the merge was necessary and not sufficient**, which is the review's finding rather than the issue's. A child bmlib does not *accumulate* never withheld a buffer to begin with: its characters go straight to whatever is open, so a routine book deposit's `<edition>`/`<publisher-loc>`/`<publisher-name>` gave `'3rd edAmsterdamElsevier'` — the run-together word the exclusion exists to avoid, documented in six places as the empty string. So the close arm writes `citation` for `<mixed-citation>` **only**, which also settles a `<ref>` carrying both spellings (legal as bare siblings and inside `<citation-alternatives>`), where the unconditional write was last-writer-wins and an `<element-citation>` second wiped the string the publisher did typeset. Several `<mixed-citation>` in one `<ref>` is #149, a modelling decision and not a fix. **The blast radius is measured, not reasoned, and the first account had it backwards**: diffing the branch against `main` over 880 local PMC articles / 20,770 references moves `citation` for 4,499 (21.7%) in 191 articles — 3,541 rebuilt by the merge and **958 in 84 articles emptied**, those having been carrying an element-only leak in a public field — **`authors` for 502 in 14 articles**, and rendered HTML for 576 in 23 — so a *structured* field moves, because `<surname>`/`<given-names>` are gated on `in_ref_person_group` and a cited `<string-name>` outside one had no arm fire at all. That path needed the `<collab>` and `<string-name>` reference arms **normalised rather than end-stripped**, a wrapped name having arrived as the literal `'J.\nTan'` in a public list and in the cached HTML. The strict-ancestor slice — which stops the citation merging into whatever encloses the `<ref>` — is **prospective and the comment says so**: at this revision no handler reads that buffer (nothing outside `_TEXT_ACCUMULATING` takes `text`), dropping the slice survives the whole suite, and three document shapes parse identically without it. Two things came out of walking the other paths the merge rule reaches. A `<xref>` to a figure or table is **replaced** by a `[text](#rid)` link rather than merged, and nothing pinned that — dropping the suppression emitted `Figure 1[Figure 1](#f1)` into body prose and passed the full suite. And the same taken-and-not-returned shape loses a `<tex-math>` from the prose around it and a `<disp-formula>` from the article outright, which is #147: delimiting LaTeX in prose is a decision, and `<alternatives>` may hold two encodings of one formula, so it is not one more member of a set. #147 is scoped to prose **outside** a citation — inside one the ancestor test merges `<tex-math>` like anything else, so both consequences #147 names are live there (raw undelimited LaTeX, and an `<alternatives>` pair emitting twice) — but that path measures **0** of 10,671 `<mixed-citation>` across 227 articles and 0 in the local corpus, so it is unexercised rather than a live defect. Finally, `_INLINE_ELEMENTS`' `collab`/`string-name` entries are now **subsumed** for every measured shape: after #146, deleting both passes the whole suite where before it failed two tests, so what they still stand for is a name printed outside a citation — an unmeasured population, pinned by `TestAnUndividedNameInProseStaysInTheProse` so the membership cannot go vacuous again.

Finally, **a value that will not parse costs its own row and nothing more** (#129): `colspan` is CDATA, and a bare `int()` raised out of the SAX callback into `service.py`'s tier-level `except Exception`, so one bad attribute lost the article and the chain reported it as unavailable from that source. Two things the first fix got wrong, both caught in review. A refused span is **not cosmetic** — `_build_html_table` takes the column count from the first row and `_pad_row` pads at the end, so it slides every later cell in that row one column left and renders wrong numbers under the right headings — so refusals are counted and reported once per article at **WARNING** (a deposit can reach this one, unlike the audit). And the bound belongs on **both** ends: guarding only the value `int()` refuses left `colspan="20000000"` to materialise 320 MB of empty cells at ~2.1 GB RSS, cached to disk, or a `MemoryError` that is not a `_BUG_TYPES` member and so loses the article in silence — #129 verbatim. `_MAX_COLSPAN = 1000`.

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

### Replace-per-source child rows

`publication_grants` and `publication_affiliations` each carry a `source`
column — the source that *asserted* the row — and `_replace_child_rows()`
scopes every delete to it: a record's rows replace that source's stored rows
and leave every other source's alone. Scoping by publication alone was a real
defect, caught before release: PubMed's grants replaced OpenAlex's and then
OpenAlex's replaced PubMed's, so the stored answer depended on whichever
source synced last, with no error and no warning. `sync._stamp_source()` fills
the column from `record.source` rather than each fetcher setting it, because a
fetcher that forgets fails silently — its rows land in an unnamed bucket and
stop being scoped.

A row naming **no** source raises `ValueError`. An unnamed row is not merely
unlabelled but unreachable: nothing can ever name it, so no later sync can
replace it and each one stacks a correctly-labelled duplicate beside it. The
check is in the storage layer rather than left to the `NOT NULL` column
because the column rejects `None` while `""` — the dataclass default, and so
the value a forgetful caller actually produces — was stored happily. Nine
tests were silently exercising that path before the guard existed.

**No UNIQUE constraint on the natural key**, on purpose. Every column of a
grant proper is nullable and both backends treat `NULL` as *distinct* in a
unique index, so `UNIQUE(publication_id, source, agency, grant_id)` would let
`(1, 'pubmed', NULL, 'R01')` insert twice — protecting nothing while looking
like it protects something. An expression index over `COALESCE`d columns would
work, but nothing is left for it to catch: the fetcher collapses PubMed's
verbatim repeats at parse time (measured: 31 of 575 entries across 200
records), and the per-source replace is idempotent. Both are reachable from a
test in a way that index would not be.

The empty guard stays for a different reason than it originally had: with no
rows there is no source to scope a delete to, and an absent `<GrantList>`
means the record did not carry the data, not that the funding was withdrawn.

`_consolidate_rows()` must relocate **every** child row before deleting the
dropped publication. Both backends enforce foreign keys
(`connect_sqlite(foreign_keys=True)` is the default), so one stranded grant
makes the `DELETE` raise and aborts the whole store. Rows move *per source* —
a source the keep row already has wins, one only the drop row saw moves
across — since merging two rows' accounts of what PubMed said yields a set
PubMed never asserted. Pinned on both backends by
`test_a_split_identity_merge_relocates_child_rows` and
`test_consolidation_moves_only_sources_the_keep_row_lacks`; both guards
verified by mutation.

### A completed day is a durable claim

`sync()` writes `status='completed'` to `download_days`, and
`_days_needing_fetch()` does not offer a completed day again once it is in
the past and was fetched after the day was over — unless `recheck_days` is
set, which is not the default. So anything that reports success it did not
have does not lose a request — it loses the day's records permanently, and
issues #88–#90 were three separate ways of doing exactly that. Three rules
follow, and all three **fail closed**.

*A day is over at 12:00 UTC the next day* (#95). That, and not local
midnight, is when day *D* has ended in every timezone — UTC−12 finishes it
last — and it is equally the instant beyond which "now" can no longer fall
inside day *D* anywhere, so the one comparison replaces the `if current ==
today` branch it used to sit beside rather than approximating it. Without it,
a day captured *as* today was stored `completed` and, being neither `today`
nor `failed` tomorrow, never revisited: a 09:00 cron durably lost the
following 15 hours of indexing, invisibly to every rule below, since the
source's own count agreed at 09:00. Do not "simplify" this to a date
comparison — all three built-in sources are US-based, so a UTC-date rule
calls a fetch at 00:30 UTC on *D+1* durable while PubMed's day *D* has four
and a half hours to run. A `downloaded_at` that cannot be *read* fails closed
in three shapes; one that reads cleanly but sits in the future cannot be
*true* and fails closed too, since believing it is #95 again. Every day in a
window is judged against its **own** boundary — passing `date_from` there
survived the whole suite once. `last_verified_at` has its own, laxer reader
for the same reason: read raw it raises from inside day selection and takes
the entire multi-source run with it. `docs/DECISIONS.md` has the rest,
including the costs — one extra day-fetch per run, and the whole window once
on upgrade.

*Reconcile the walk.* `fetchers/_reconcile.py` compares what a source
delivered against the count it promised, in one place because three fetchers
share the shape. Three rules of different kinds: a **stalled** walk (a page
delivering nothing while the count says records remain) is broken outright
and carries no threshold — it is also the only rule that catches a session
expiring on the last page, so every fetcher must compute and pass it, and
OpenAlex silently not doing so was a live hole after #88's first round;
**unreconcilable** delivery (records arrived against no count at all) cannot
be shown to have finished and so cannot complete, while nothing delivered
against no count is the ordinary quiet day; and a walk that ended naturally
but came up short is judged against `SHORTFALL_FAILURE_RATIO`. The
`promised=None` that drives the second rule must never be flattened to `0` —
"this day is empty" and "I am not telling you" are different claims, and
collapsing them switches the other two rules off silently.

A shortfall too small to fail on returns a **note** as well as logging one.
`FetchResult.note` carries it to `SyncReport.notes`, kept apart from
`errors`: a day may be missing nearly half its records on that path and is
never re-offered, so "which of my completed days came up short?" has to be
answerable from a return value and not only from a log line.

That floor, rather than strict inequality, is the load-bearing choice. A day
recorded `failed` is re-offered on **every** later run, so failing on a gap
that is benign and permanent re-fetches and re-merges that day for the rest
of an installation's life, growing with the date range and with an ERROR each
time. Benign gaps exist: a record withdrawn between search and fetch, an
index moving under a long walk. **The 0.5 is a rule fixed before
measurement**, unlike every other calibrated threshold here (#79's
allow-list, #68's log levels, #56's corroboration rule, #36's funder stems);
it asserts only that no benign cause plausibly removes half a day. Issue #92
is the follow-up that measures it — do not cite 0.5 as measured, and do not
tighten it without running that.

*Count what the server delivered, not what you parsed.* PubMed's efetch
delivers `<PubmedBookArticle>` elements the fetcher deliberately skips.
Reconciling parsed records would report a phantom shortfall on every day
carrying a book chapter, and then re-fetch it forever. Delivery counts the
two record elements **by name** rather than taking every child of the set:
`<DeleteCitation>` is also a legal child, and counting it inflates delivery
so a real shortfall clears the floor — and, because the stall rule is
`delivered == 0`, stops a page carrying nothing else from looking like the
stall it is.

*Check the envelope; do not read it through defaults.* `data.get("results",
[])` makes an HTTP-200 error body identical to a day with no publications.
PubMed refuses an efetch root that is not `PubmedArticleSet` (the same
refusal `_esearch` makes for a missing `<Count>`), and OpenAlex requires a
list `results` and a `meta` carrying a numeric `count`.

bioRxiv is the one where the obvious guard is wrong. It refuses a body
carrying **neither** a `collection` key **nor** messages — a body making no
claim about the day at all — rather than requiring a list `collection`.
bioRxiv reports a quiet day by omitting `total`, and whether it also omits
`collection` **is not measured**; requiring a key a quiet day may not send
would fail that day on every run for the life of the installation, which is
the runaway-retry cost these rules exist to avoid. The residual is real and
worth stating: an error body that *does* carry messages and no collection
still reads as a quiet day, and cannot be told apart from one without
knowing bioRxiv's `messages[0].status` vocabulary. **Issue #94 is the live
sampler that would measure both**; do not tighten this guard without running
it, and do not "simplify" it to `isinstance(data.get("collection"), list)`.

In `sync()` the same principle gives an **allowlist**, not a denylist, on
both sides. A fetcher status that is neither `"completed"` nor `"failed"` is
recorded as failed, since `register_source()` is public and a third-party
fetcher is exactly the caller who will not know the convention; and
`_days_needing_fetch()` re-offers anything that is not `"completed"`, so a
status the table does not recognise costs a re-fetch rather than silently
counting as done. The validated status is typed `DayStatus`
(`Literal["completed", "failed"]`) from `_resolve_day_status` through
`_upsert_download_day`, which makes writing a third value a type error —
while `FetchResult.status` stays a bare `str`, because it is a boundary value
from a public extension point and narrowing it would break third-party
fetchers under their own type checker. And any record that failed to store
fails its day — `store_publication()` merges, so the retry is idempotent. The accepted cost is that a permanently-unstorable record pins
its day into a retry on every run; that is loud (an ERROR and a
`SyncReport.errors` line each time) where the alternative was silent.

*A day the source will not serve in one session is partitioned, not walked*
(#96, #105).
PubMed's search backend serves only the first 9,999 records of a history
session: `retstart=9999` is HTTP 400, and — the half that matters — a page
whose window crosses the boundary is clamped to it *silently*, so "walk as far
as it goes" yields a last page indistinguishable from a day missing records.
Under `[Date - Publication]`, the field the fetcher queries, this is not an
edge case: a record carrying only a year and a month is indexed at day 1 of
it, so every first-of-month day other than 1 January holds 49,543–90,571
records and every 1 January 212,439–315,282, against a median ordinary day of
4,890. Such a day cannot be
`completed` from one session — that would durably lose the remainder — so it
is split into **Entrez-date (`[EDAT]`) ranges** that each fit: the fixed root
`1900/01/01–2100/12/31`, recursively halved, planned in full before a record
is fetched, then each part walked as an ordinary day with its own session and
its own reconcile, and the day's total reconciled against the day's own count
afterwards. A range and not a facet because disjointness and coverage must be
*structural* — a record carries several publication types, so `AND pt`
double-fetches and inflates delivery past the day's own count, hiding the
shortfall the reconcile exists to catch. Measured: 242,216 records → 37 parts,
depth 13, 40 planning ESearches, parts summing exactly, no stuck Entrez date
in six walks over five real days. The cost is stated rather than flagged off:
~580 requests and ~1 GB for such a day, ~6.2M records and ~25 GB **once**
across a six-year backfill — against the refusal it replaces, which re-offered
those days forever and stored nothing. Only the 40 planning ESearches above are
measured, and only for the ladder *as it was measured*: it now spends one more
probe per derived-zero right child and per single-date leaf it reaches (+3 on a
synthetic 64-part day; not re-measured live). No session ESearch was ever
issued, so the one-per-part session call (~37, arithmetic over the measured
part count) joins the EFetch pages and the byte figures (arithmetic over *those*
at ~4 KB a record) as unmeasured; no full fetch of such a day has ever been run
— do not quote any of them as measured. The pages are **~503, bounded 485–521**,
not 485: `_walk_session` pages *per part*, so the day costs the sum of
`ceil(nᵢ/500)` over 37 parts, and 485 is the floor one single session would
have cost.
Parts are checkpointed in `download_day_parts` (same transaction as their
records, so a checkpoint never attests to records a rollback discarded), which
is what makes an interrupted day resumable and what forced the per-part flush
a 242k-record day needs anyway; a part is skipped only if its stored count
still matches, and a skipped part must be credited or every resumed day fails
its own day-total check. **Flushing and checkpointing are different
questions** and one callback carries both (`PartCheckpoint | None`): every
part that finished walking has its records stored, since that flush is the
memory bound and a bound conditional on the source behaving is not one, while
only a part that reconciled clean earns a checkpoint — checkpointing a noted
part would let a later run skip it and manufacture the records the note was
reporting missing. **Two of bmlib's own counts never settle in favour of the
weaker one**, and that rule is applied at all three scales: a part whose own
session count falls below `SHORTFALL_FAILURE_RATIO` of what planning measured
fails the day rather than being walked at the lower number and checkpointed as
clean (it was once written as exactly `== 0`, which let a part collapsing
5,000 → 1 pass); a **day**-level count of 0 contradicted by this day's own
checkpoints fails rather than completing at zero and deleting them; and a
single Entrez date is *measured* before the day is refused on it, since a
right-hand child's count is derived by subtraction and the surplus of a stale
parent walks down the empty tail to a future date claiming tens of thousands of
records. A derived right-hand count of zero is measured for the same reason and
is the one derivation that cannot heal: any other error still yields a part,
and a part re-counts itself when its session opens, but a zero yields no part
at all, so the range is never visited. A planning ESearch that fails returns a
failed `FetchResult` like the under-cap path rather than raising.
`SourceDescriptor.resumable` gates the new keywords,
defaulting `False` because `register_source()` is public — and a descriptor
declaring `True` over a fetcher that cannot accept them is refused at
registration, since `sync()` reads the descriptor and the mismatch otherwise
failed every day of that source on every run, forever. The one case left is
a **single Entrez date** over the cap, which cannot be split further: that day
is still refused, naming the date and a count that was measured — and it is not the structural
population the month firsts were. A cap NCBI *raises* now costs unnecessary
partitioning rather than a refusal — requests, not records, and quietly, where
it used to be an ERROR; one NCBI *lowers* is still **not** reliably covered,
because for a band up to `EFETCH_PAGE_SIZE` wide no page is ever requested
past the new limit and the part completes on a shortfall note instead — the
sampler is the guard there (`--partition` is what re-measures the ladder), and
`docs/DECISIONS.md` has the measured band. The stride is *not* the defect #96
suspected: `retstart` indexes the session's UID list, measured against
esearch's own `IdList`, so advancing by what arrived would re-request the tail
of every short page and count the duplicates as delivery — which is exactly
what would hide a real shortfall from `reconcile_delivery`.

Finally, *the rule refuses to guess its own inputs* (#98, #99).
`DownloadDay.from_dict()` raises rather than defaulting an absent
`downloaded_at` to now — the most durable-looking value the rule can be
handed, and a fail-open where the SQL path fails closed — while the
dataclass default that stamps now for a *freshly constructed* row is kept,
since that row describes a fetch that has just happened. Every rejection
there is a `ValueError` **naming the field**: delegating to `_parse_datetime`
let a non-string escape as `TypeError`, so the documented `except ValueError`
did not catch it, and an unreadable string reported `Invalid isoformat
string: ''`, which names neither column nor row. `PartCheckpoint` is held to
the same bar for the same reason — it is read back on the same path, before
the per-day handler is entered — so `from_dict()` goes through
`_require_text` / `_require_count` rather than `str()` and `int()`, which
accepted everything: `str(None)` is the literal `"None"`, and a `part_key`
reading `"None"` matches no plan, so resume degrades to re-fetching every
unfinished day with nothing raised. `__post_init__` refuses what cannot
describe a finished part, but deliberately imposes no `record_count <=
promised` rule: `promised` counts record elements the server delivered and
`record_count` those the fetcher parsed, so the two are not commensurable —
the conflation `_EFetchPage` exists to prevent. And the read itself is
guarded, since `_load_day_parts` runs *before* the per-day handler: an
unreadable row fails its day rather than escaping `sync()` and leaving the
whole multi-source run with no `SyncReport` at all.

And `sync()` validates `date_from`, `date_to` and `recheck_days` at its
entry, because anything raised out of day selection escapes a `try` carrying
only a `finally` and loses the whole multi-source run's `SyncReport`.
Validate at the entry, never with an `except OverflowError` at the helpers:
that turns a caller bug into the silent re-fetch this family exists to
remove. Two kinds of check, and **not every one is guarding an exception** —
a negative `recheck_days` walked fine and was swallowed by `recheck_days >
0`, delivering the opposite of what was asked without a word, and `nan`
reached the same silence through both range checks. The **type** checks are
the ones that earn their place hardest: `datetime` subclasses `date`, so
`date_to=datetime.now()` satisfies mypy, defeats every value check
(`datetime.max == date.max` is `False`), and on *both* ends raises nothing at
all — it writes `download_days.date` values carrying a time component that no
date-keyed lookup can ever match. An **empty** window is deliberately *not*
rejected — it is what incremental sync produces once it has caught up — and
neither is a window reaching into the **future**, which cannot complete but
whose past half is perfectly fetchable; it returns a `SyncReport.notes` line
instead, since permanent *and* invisible is the pair these rules exist to
break up. A fetcher that returns a non-`FetchResult` fails its own day rather
than the run: `register_source()` is public, and an `AttributeError` from
`_resolve_day_status` used to escape the one handler that wraps the call.

### Markdown, measured against the markup

`fetchers/pubmed.py` reads titles and abstracts with `_text_with_formatting()`,
not `_text()`. `_text()` returns `el.text`, which is the text *before the first
child*, so it silently truncates any value holding markup — a title reading
`"Effects of H<sub>2</sub>O and <i>E. coli</i> on outcomes"` was being stored
as `"Effects of H"`. Two rules the recursion depends on, each with a named
test:

- **Strip once, at the outermost call.** Upstream stripped at every level,
  which ate the space inside a formatted run and welded
  `<b>Randomised </b><b>trial</b>` into `**Randomised****trial**`.
- **A run's edge whitespace is re-emitted outside its markers.** Simply
  keeping it in place is no better: CommonMark requires an emphasis delimiter
  to be adjacent to non-whitespace, so `**Randomised **` does not emphasise
  either. Moving it out gives `**Randomised** **trial**`.

An abstract section's label comes from `Label` **or** `NlmCategory` — reading
only the first dropped the heading from every section labelled the other way.

Two further rules keep the *declared* format honest, and the section title is
meant literally — both were settled by measuring 3,403 real titles and
abstract sections, not by taste:

- **Prose is escaped; the markers are not.** `_escape_markdown()` escapes
  ``\ ` * ~ ^`` in text taken from the document. Calling a field Markdown
  without this corrupts values that were fine before: `CYP2C19 (*1, *2, *3)`
  renders as `(<em>1, </em>2, …)`, and the `~` of "AUC ~ 0.80" pairs with the
  next one to subscript half a sentence — a hazard the `~x~` mapping itself
  created. That set alters 0.35% of fields and removes every construct a
  CommonMark parser found; adding `_` and `[`/`]` churned 4.3% and fixed
  nothing, since intraword `_` is inert and a bare `[…]` is not a link.
  Affiliations go through the same walker, so they are escaped too — which
  matters because that column is a join key.
- **`<u>` is not mapped.** Markdown has no underline, and `__x__` is *strong*
  emphasis, so mapping it renders `<u>` identically to `<b>` while asserting
  the source said "bold". Underline is presentational, unlike a subscript, so
  it falls through to the undecorated path instead.

### Optional dependencies guarded at the call site
Optional imports are deferred to the constructor or function that needs them, not the module top level, so importing a module never drags in an extra. `PyMuPDFConverter.__init__`, `FullTextService.__init__` and `TransparencyAnalyzer.analyze()` all follow this pattern, and no top-level optional import remains in the package.

**The convention has to hold of the *package*, not just the module.** Importing a submodule imports its parent first, so one eager re-export in an `__init__.py` gates everything beside it. `fulltext/__init__.py` re-exported `service`, whose top-level `import httpx` left **ten** modules across two packages raising a bare `ModuleNotFoundError` on a core install (issue #64) — including the pure-dataclass `models`, the stdlib-only `SectionSegmenter`, and the three publication fetchers, which borrow one dataclass from `models` and take an injected HTTP client of their own. Both `fulltext` and `context_processor` now resolve their extra-bearing exports through a PEP 562 `__getattr__`.

Measure this with **one fresh interpreter per module**. A single process leaves the half-initialised parent in `sys.modules`, and its siblings then falsely read as importable — which is how #64 was first mis-scoped to one module. `tests/test_fulltext_service.py::TestPackageImports` masks `httpx` via a `sys.meta_path` finder in a subprocess for the same reason, and carries a negative control asserting the mask actually masks: every machine that runs the suite has httpx installed, so a mask that silently failed would make every masked test in the class vacuous.

Two rules on the guard itself, both settled by review of #64:

- **Return the module; do not store it on the instance.** `_require_httpx()` imports and returns; `FullTextService.__init__` calls it for the fail-fast check and discards the result, and `_http_get` calls it again where the client is built. A module object cannot be pickled, so `self._httpx` silently cost the ability to hand a configured service to a `ProcessPoolExecutor` — and reading the module back as instance state turns any object that reached `_http_get` without running `__init__` into an `AttributeError` that the tier chain swallows at DEBUG. After the first call the import is a `sys.modules` lookup, on a path that then makes a network request. `PyMuPDFConverter.__init__` still stores `self._pymupdf`: it was never picklable, so nothing there regressed.
- **Report what was raised; do not assert the cause.** `except ImportError` also catches the `ModuleNotFoundError` a *present* extra raises for its own missing dependency, and an `ImportError` from a version skew inside it. "Not installed" then prescribes a `pip install` that answers "Requirement already satisfied" and changes nothing, so the reader runs it, sees success, retries and hits the identical error. Interpolate the caught exception into the message, as `_attach_pdf_text` already does for PyMuPDF.

A PEP 562 `__getattr__` should also **bind what it resolves** into `globals()` — PEP 562's own recommendation, so repeat access skips the function — and its companion `__dir__` must return `sorted(set(__all__) | set(globals()))`, not `sorted(__all__)`. Returning `__all__` alone trades one omission for a larger one: the submodules and every dunder vanish from `dir()`, breaking REPL completion for `bmlib.fulltext.models` and shrinking `inspect.getmembers()`.

### Measured, not assumed, in the batcher
`context_processor` promises that no batch handed to `extract_from_batch()`
exceeds `max_context_chars`. Two upstream bugs came from *assuming* a size
instead of measuring the string that would actually be sent, and both are
guarded by tests. An oversized item is split against a budget derived from
the **measured** overflow of a trial split — `format_item()`'s decoration is
not guessed at — and `TRUNCATE` wraps its output in `_Preformatted` so the
batcher renders it as-is rather than decorating it a second time. Each item
is measured at the index it will actually occupy, and an item that no longer
fits is re-measured at the head of a fresh batch, so `Batch.total_chars`
equals `len(_format_batch_content(batch, config))` exactly. Upstream's
`estimate_item_size()` hook is deliberately absent: the batcher must format
every item anyway, so the estimate bought nothing and let the oversized
decision disagree with the packing measurement. The guarantee is asserted
where it is delivered — `TestTheContextLimitIsNeverExceeded` checks it from
inside `extract_from_batch`, across every oversized strategy and above level
0 where `format_consolidated_item()` supplies the decoration, and carries a
negative control so a guard that cannot fail is not mistaken for a guard
that passes.

### A file bmlib writes for a user is published, never written in place

`bmlib/_atomic.py`'s `atomic_write()` is the one way this library creates a
file a user or a later run will read: bytes to a uniquely-named temporary
file beside the target, `fsync`, then `os.replace`. Both call sites are
there because the same defect was found twice — `fulltext/cache.py`'s two
saves (#70) and `templates/engine.py`'s `install_defaults()` (#73) — and
both had the same shape, which is why it is worth stating as a rule rather
than as two fixes. A partial file written in place does not look partial:
it decodes cleanly and is then trusted forever, because the guard that
would re-create it (`if not dest.exists()`, a cache hit) is satisfied by
the truncated file's mere presence. **A new writer of user-visible files
uses this helper**; the five details its docstring calls load-bearing were
each earned by review and each has a regression test, so re-deriving them
in a second copy is the failure mode the promotion exists to prevent. (The
`O_BINARY` flag beside them is the exception and says so at the site: the
CI matrix is Linux-only, where the `getattr` is `0`, so nothing exercises
it — `test_a_template_is_copied_byte_for_byte` would catch it on Windows.)

**Test a new call site for the publish, not just for the tidy-up.** Where
there is nothing to overwrite, an ordinary in-place write that unlinks on
failure is indistinguishable from an atomic publish *after the fact*, so an
error injection alone proves nothing — mutation confirmed such an
implementation passed every templates test in the first cut of #73. They
differ only while the bytes are in flight, which is exactly what survives
`SIGKILL`, the half of the scenario no injection reaches. Assert on that
instant: hook `os.replace` and check the target name is still absent.

Two things the module does *not* do, deliberately. It does not detect an
entry already corrupt on disk — that is prospective-only, and would want a
checksum sidecar (see #70's entry in `docs/DECISIONS.md`); where the
remedy differs per call site, say so in `docs/manual/` rather than nowhere
(`clear()` for the cache, "check the directory once" for templates). And it
does not swallow `OSError`: every caller propagates, because a caller who
cannot write is better told than left believing the file is there. In
`install_defaults()` that means one failed template aborts the loop with
the rest uninstalled, which is correct — the next call installs whatever is
still missing, so the loop is self-repairing.

Two hazards the publish *creates*, which a bare write did not have. It
replaces a **symlink** at the target rather than writing through it, so a
call site where a symlink is a user's deliberate indirection has to look
for one first (`install_defaults()` does; the cache deliberately does not —
see `docs/DECISIONS.md`). And the failing syscall names the *temporary*
file, which the cleanup then deletes, so `atomic_write` re-points
`OSError.filename` at the target — `str(exc)` is built from it, and that is
what `FullTextService` puts in front of an operator.

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
uv run mypy
```

**`mypy` takes no arguments here — and must not be given any.** Its scope
(`files = ["bmlib"]`) and its four non-default settings live in
`pyproject.toml`, so the bare command is what CI runs and what checks the
guarantee `py.typed` makes to downstreams. It is pinned in the `dev` extra
for the reason ruff is pinned in `ci.yml`.

**Run it in the dev venv, never against a bare interpreter.** Every extra
except psycopg2 ships its own `py.typed` — psycopg2 is covered by
`types-psycopg2` in the `dev` extra — so mypy resolves real types only
where the packages are installed. Run without them — which `uv run mypy`
did before mypy was a declared dependency, silently resolving an isolated
environment — and it reports the optional imports *and `jinja2`, a core
dependency*, as missing stubs: 7 phantom errors on top of the real ones,
which is how issue #81's opening count came to be 24 rather than 22.
`uv pip install -e ".[all,dev]"` is what makes the command honest.

Two conventions the settings encode. `disallow_untyped_defs` is on because
an unannotated function is otherwise skipped in silence, which would let
the gate pass a file carrying no annotations at all — the exact hole
`py.typed` denies. And anything deliberately unchecked is an inline
`# type: ignore[code]` with its reason at the site, never a per-module
`ignore_missing_imports` override: `warn_unused_ignores` reports the
inline form the day it stops suppressing anything, and cannot report the
override.

All tests use in-memory SQLite (`connect_sqlite(":memory:")`) for database tests and mocked HTTP responses for API tests. No external services are required.

`tests/test_backends.py` additionally runs every one of its tests against PostgreSQL when a server is configured — it is the guard against `publications/` drifting back to SQLite-only SQL:

```bash
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/pgrun port=5432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py
```

The DSN must point at a database the tests may drop every table in. Unset, the PostgreSQL half of each test skips.

CI runs this against a `postgres:16` service on every matrix entry and also sets `BMLIB_REQUIRE_POSTGRESQL=1`, which turns that skip into a failure — a DSN that is missing or points at an unreachable server must not leave the PostgreSQL half unrun behind a green build.

**Lint with the CI-pinned ruff, not the one in `.venv`.** CI pins **0.15.20** (`.github/workflows/ci.yml`); a stale local ruff false-flags rules newer versions removed:

```bash
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

`main` carries a `protect_main` ruleset: no deletion, no non-fast-forward push, and **CodeQL code scanning plus code quality required** at the `errors` / `high_or_higher` thresholds. CodeQL runs from GitHub's *default setup*, so there is no workflow file in `.github/workflows/` to read or edit — and its generated workflow does not listen for a pull request's `reopened` action, so a PR that predates the setup needs a fresh commit, not a close/reopen, to get its first analysis. The ruleset says nothing about which merge strategy is used, and neither does anything else — squash, rebase and merge commits are all enabled and all fine. The release recipe no longer depends on which one you press: it tags `main`'s tip after pulling, which is on `main`'s first-parent line under every strategy (issue #78, closed).

## Test file mapping

| Module               | Test file(s)                                               |
|----------------------|------------------------------------------------------------|
| `db/`                | `test_db.py`, `test_migrations.py`, `test_backends.py`     |
| `llm/`               | `test_llm.py`, `test_openai_compat.py`, `test_llm_tools.py`, `test_llm_thinking.py`, `test_llm_embeddings.py`, `test_json_repair.py`, `test_text_utils.py`, `test_json_extraction.py` |
| `agents/`            | `test_agents.py`                                           |
| `citations/`         | `test_citations_parser.py`, `test_citations_formatter.py`, `test_citations_builder.py` |
| `context_processor/` | `test_context_processor.py`, `test_llm_chunk_processor.py` |
| `quality/`           | `test_quality.py`, `test_cochrane.py`, `test_extractors.py` |
| `templates/`         | `test_templates.py`                                        |
| `_atomic.py`         | `test_atomic.py` — only what belongs to the helper itself (the 38-char temp-name overhead `fulltext.cache`'s filename cap is arithmetic over, and the exception the caller gets back). The five load-bearing details are pinned at the call sites, where the behaviour is delivered: `test_templates.py::TestInstallingDefaultsIsAtomic` and `test_fulltext_cache.py::TestWritesAreAtomic` |
| `transparency/`      | `test_transparency.py`                                     |
| `publications/`      | `test_publications.py`, `test_sync.py`, `test_backends.py`, `test_pubmed_fetcher.py`, `test_openalex_fetcher.py`, `test_registry.py`, `test_retractions.py`, `test_fetch_reconciliation.py` |
| `fulltext/`          | `test_fulltext_cache.py`, `test_fulltext_models.py`, `test_fulltext_service.py`, `test_jats_parser.py`, `test_pdf_converter.py`, `test_segmenter.py`, `test_fulltext_titles.py`, `test_pdf_metadata_titles.py` |
| `fulltext/_parse_audit.py` | `test_parse_audit.py` — the pure half only: what an imbalance *reads as*, given a state. That the state is captured correctly, and that every entry point reaches it, is pinned at the call site in `test_jats_parser.py`, where the behaviour is delivered — a struct agreeing with a capture agreeing with nothing would pass `test_parse_audit.py` in full |
| `scripts/`           | `test_databank_sampler.py` (`sample_databank_names.py` only), `test_free_pdf_sampler.py` (`sample_free_pdf_urls.py` only), `test_pdf_title_sampler.py` (`sample_pdf_metadata_titles.py` only), `test_efetch_paging_sampler.py` (`sample_efetch_paging.py` only), `test_jats_exhibit_sampler.py` (`sample_jats_exhibits.py` only), `test_sampling_helpers.py` (`_sampling.py`) |

`scripts/smoke_test_tool_calling.py` is an end-to-end integration runner for tool calling. It hits live providers, so it is not part of the pytest suite — run it manually when changing provider tool-call code.

`scripts/sample_databank_names.py` is a live runner too — it measures PubMed's `DataBankName` vocabulary against `_TRIAL_REGISTRY_NAMES` and `_DEPOSITION_DATABANK_LEVELS`, and is what keeps those curated lists answerable to the records. **Run it before changing either.** Its *reading* is a maintainer's evidence, so `tests/test_databank_sampler.py` covers it offline through a stubbed `_get`: what those tests pin is that a request that failed never prints as a finding, since a zero count is what a dead list member looks like and an `unclassified` is what a vocabulary drift looks like. The module is loaded by path — `scripts/` is not a package.

`scripts/sample_free_pdf_urls.py` is the same shape of live runner. It prints two tables, one per allow-list it is the evidence for, and **must be run before changing either**: PDF-download failure rates per call site (`europepmc`, `unpaywall`, `biorxiv`), behind the per-`(tier, cause)` log-level rule; and the access-label distribution over every `documentStyle=pdf` entry a Europe PMC search returns, behind `_FREE_PDF_AVAILABILITY_CODES`. The distribution is counted **before** the allow-list filters and each row is marked taken/SKIPPED — counted after it, it could only ever confirm the allow-list, and issue #79 was precisely a value that never appeared in what bmlib accepted. A 429/503 counts as unmeasured rather than failed, in the Unpaywall *resolution* phase as well as the probe phase (that is where that API's limiter bites), retried with backoff honouring `Retry-After` **clamped at both ends** — an unclamped hour is a run that prints nothing, gets killed, and loses every population, which is the same loss the zero clamp prevents. Pacing is per host: the sampler's own first live run measured its own throttling — one host hit 300 times in 300 seconds, dominated by HTTP 429 — before that was fixed. `tests/test_free_pdf_sampler.py` covers it offline the same way `test_databank_sampler.py` does: a probe that could not be made must never print as a finding.

`scripts/sample_pdf_metadata_titles.py` is the third live runner, and the evidence behind the corroboration rule in `bmlib/fulltext/_titles.py` — **run it before changing `looks_like_junk`'s reject-list**. It fetches free PDFs from Europe PMC and bioRxiv, reads each one's `/Title` and page 1, and labels the pair against the record's own title (`match` / `truncated` / `unrelated` / `absent`), writing `tests/data/pdf_metadata_titles.json`. Two rules it does not share with the others. It deliberately **does not import `_titles.normalise`** — a corpus labelled by the rule under test can only confirm that rule, so the sampler carries its own comparison, and a future refactor must not "deduplicate" the two. And it writes the corpus **only when every population is reportable**: the summary is computed first, and a run that trips the unmeasured-share threshold writes to `*.unreportable.json` instead, so a throttled run cannot replace the evidence a later reader takes as measured. The journal keeps every row, so refusing costs a re-run and nothing else.

Each bioRxiv attempt records the **posting day** it came from, and each unmeasured attempt also records a `cause` and an `attempts` count. The day is what keeps a retry reachable: that walk covers `[today-30, today-49]` recomputed from `date.today()`, so it slides a day per calendar day and after 20 shares nothing with the window that produced the journal — leaving an unmeasured attempt that `already_seen` holds open but the walk can no longer offer, permanently inflating the population's unmeasured share with no escape but deleting the journal. Days owed a retry are walked *before* the fresh window and *in addition* to it, so retrying old work never costs the run its budget for new work; pinning the window instead would make one date range serve both "what am I sampling" and "what do I owe", and those diverge by a day every day. Europe PMC needs none of this — its walk restarts from cursor `*` and re-offers the same hits. `MAX_UNMEASURED_ATTEMPTS` bounds the tail so a day of permanently dead URLs is not re-downloaded forever: a retired attempt stops being *offered* but keeps being *counted*, in `tally_previous` and in the ERROR rule, since forgetting it is the silent-loss failure the accounting exists to prevent. `summarise()` names how many were retried out, because "we stopped trying" and "not tried yet" call for different actions.

`scripts/sample_efetch_paging.py` is the fourth live runner, and the evidence behind `EFETCH_MAX_RETRIEVABLE` and the fixed stride in `fetch_pubmed`'s page walk — **run it before changing either**. It binary-searches the live backend for the largest `retstart` a history session serves (reporting `agrees` or `DISAGREES` against bmlib's constant), checks whether the page straddling that boundary is still clamped silently, compares a page's record elements against the session's own UID list to re-establish what `retstart` indexes, and sizes `[Date - Publication]` days against the cap. It has a sharper version of the others' rule, because here **the measurement itself arrives as an HTTP 400**: only a 400 is the boundary, and every other non-200 is a failed probe, since one 429 read as a refusal drags the binary search down and prints a cap that no server enforces. `--skip-day-sizes` runs the session probes alone, at a fixed 23 requests; the day-size populations need a full run (~150). It shares the other samplers' rule that a population past `UNMEASURED_SHARE_ERROR_THRESHOLD` reports ERROR rather than a share, retries a throttled request through `_sampling`'s two-ended `Retry-After` clamp, and exits non-zero when any probe or population came back unreportable — a green exit is what a scheduled re-run is judged by. Two mirror-image rules on the 400: every non-200 that is *not* a 400 is a failed probe, and a 400 that does not name `retstart` is one too, since a dropped WebEnv read as a limit collapses the search onto wherever it started.

`scripts/sample_jats_exhibits.py` is the fifth live runner, and the evidence behind the parser's exhibit rules — **run it before changing `_ARCHIVAL_MIME_SUBTYPES`, `_ARCHIVAL_EXTENSIONS`, `_GRAPHIC_TRANSPARENT_WRAPPERS`, or the `<label>` parent test**. It answers eight questions with one walk: is a `<label>` a direct child of its exhibit (the parent rule's whole premise), what else carries one, do `<alternatives>` members declare `mime-subtype`, how are several `<graphic>` deposited and at which end the thumbnail sits, the same two questions for a `<table-wrap>`'s deposits (counted separately, #135, since the figure percentages are cited and widening their denominator would invalidate them), is a `<graphic>` ever owned by something other than its exhibit, how often a `<table-wrap>` carries a `<graphic>` and no `<table>` (#127) — or both, which is the rendition `to_html()` drops — and, as of #123/#125/#130, the same premise-and-owner pair for a `<caption>` plus what owns a `<title>` that a `<sec>` was open for. **The table side is owner-scoped and the figure side is not**, deliberately: `_owned` walks only through transparent wrappers, the way the parser routes, because unscoped a `<td>`'s cell image counts as the table's own deposit and made four of ten recent tables read as multi-deposit; the figure counters keep the subtree walk because their percentages are cited, and both draws record zero nested exhibits so the two walks agree there anyway. It shares the others' rules — the `_sampling` pacer and clamp, an unmeasured article entering no denominator, a population past `UNMEASURED_SHARE_ERROR_THRESHOLD` reporting ERROR and writing `*.unreportable.json`, a non-zero exit — and their prohibition: **it does not import the parser's predicates**, carrying its own thumbnail and archival tests, because a corpus labelled by the rule under test can only confirm that rule. `tests/test_jats_exhibit_sampler.py` asserts the two sets actually differ, which is what a future "deduplication" would break. One rule is its own: **the sample is stratified by publication month**, because a single cursor walk from `*` returns a contiguous block of accessions — the first live run drew 120 articles of which 106 carried no exhibit at all, which is a property of one accession range and not a rate. **And the window is displaceable** (`--months-ago`), because the default draw is the last two years and one of the populations is not in it: #127's image-only tables measure 0 of 662 there and 11 of 93 — 11.8% [6.7-20.0] — in a draw ending 28 years back. A stratified sample of *recent* deposits is still one window, and a population that lives in back-filled material reads as absent from it. A displaced draw must name its own `-o`: the default path is the recent corpus and the journal follows it, so writing one there would replace that corpus under its own name, or pool the two windows into a rate describing neither. The written corpus records the `window` it was drawn from, since the strata are counted back from *today* and the same command later draws a different sample.

`scripts/_sampling.py` holds what the samplers share — the per-host pacer, the two-ended `Retry-After` clamp, `wilson()`, and `is_probeable()` — so a rule learned from one bad live run does not exist in two copies that can drift. `tests/test_sampling_helpers.py` is its test file; a helper moved here must bring its tests with it, or it stays covered only for as long as one particular sampler keeps importing it.
