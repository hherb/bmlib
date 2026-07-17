# bmlibrarian → bmlib porting analysis

_Date: 2026-07-17. Author: analysis pass over the "mother project"
`~/src/bmlibrarian` (v0.1.0, ~48k LOC in `agents/` alone) to find
functionality worth porting into `bmlib` (v0.3.0, the lean shared library)._

This is an **analysis and porting backlog**, not an implementation. It ranks
candidates by value-to-effort and marks what to skip. Nothing here has been
ported yet.

## Method

Six parallel deep-dives read the actual code (imports, signatures,
class-vs-function, DB/GUI coupling, heavy deps) across the in-scope module
clusters: search/retrieval, core agents, agent subpackages, ingestion,
analysis pipelines, and infra/utilities. GUI (`gui/`, flet/Qt), interactive
CLI apps, the task-queue/orchestrator, and user auth were treated as
out-of-scope by definition.

## The refactor tax (cross-cutting realities)

Every candidate is shaped by the same five facts. Budget for them on **any**
port:

1. **The LLM migration is already done.** `bmlibrarian.llm` is now a thin
   compatibility shim that `from bmlib.llm import ...`. So bmlibrarian's own
   `llm/` has nothing to give back (bmlib's 7-provider client is the richer
   one). What remains coupled is **`bmlibrarian.config` and
   `bmlibrarian.database`**, not LLM.
2. **DB access is the anti-pattern bmlib forbids.** Every DB-touching unit
   uses a global `get_db_manager()` singleton + psycopg + `%s` placeholders +
   a fixed PostgreSQL schema (`public.document`, `transparency.*`, `mesh.*`,
   `semantic.chunks`, …). bmlib's convention is a pure function taking a
   DB-API connection as first argument. Porting a DB unit means **inverting
   this** (inject the connection) or, better, **dropping the DB tail entirely**
   and returning dataclasses/dicts for the caller to persist.
3. **Config is read at construction.** Agents call
   `get_model()/get_agent_config()/get_ollama_host()` in `__init__`. Port =
   replace with constructor injection (`model`, `LLMClient`).
4. **Prompts are inline Python strings.** There is no `templates/` dir in
   bmlibrarian. bmlib convention favors `bmlib.templates` (Jinja2). Extracting
   prompts is idiomatic but optional.
5. **Duplicates exist — consolidate, don't port twice.** There are two
   `BaseAgent` classes, two `chunk_text` implementations, two MeSH lookups,
   and two JATS parsers. Pick one each.

Also worth stating plainly: **there is no GRADE engine to port.** GRADE is
named in docstrings and scoring-weight keys but never implemented. The real
evidence-grading artefacts present are Cochrane Risk-of-Bias and Oxford CEBM
levels (CEBM already partly in `bmlib.quality`).

## Master priority table

| # | Candidate | Target in bmlib | Value | Effort | Coupling |
|---|-----------|-----------------|-------|--------|----------|
| 1 | `utils/json_repair.py` | `bmlib/llm/json_repair.py` | HIGH | S | none (pure stdlib) |
| 2 | Text chunking (`agents/text_chunking.py` + `paper_reviewer/text_utils.py`, consolidated) | `bmlib/llm/text_utils.py` | HIGH | S | none (pure) |
| 3 | Cochrane models + formatter (`systematic_review/cochrane_models.py`, `cochrane_formatter.py`) | `bmlib/quality/` | HIGH | S | none (pure) |
| 4 | Citation/reference stack (`writing/citation_formatter.py`, `citation_parser.py`, models+constants subset) | `bmlib/citations/` (new) | HIGH | M | none (pure) |
| 5 | Rule-based extractors (`paper_weight/extractors.py`) | `bmlib/quality/` | HIGH | S | none (pure) |
| 6 | Weighted audit models + MQ/RoB rubrics (`paper_weight/models.py`,`prompts.py`,`llm_assessors.py`) | `bmlib/quality/` | HIGH | S–M | pure + BaseAgent |
| 7 | `pdf_converter.py` (ABC + PyMuPDF + registry) | `bmlib/fulltext/` `[pdf]` extra | HIGH | S | optional PyMuPDF |
| 8 | PDF section segmenter (`pdf_processor/segmenter.py` + `models.py`) | `bmlib/fulltext/` | HIGH | M | pure (segmenter) |
| 9 | Cochrane assessor agent (`systematic_review/cochrane_assessor.py`) | `bmlib/quality/` | HIGH | M | BaseAgent only |
| 10 | Retraction Watch source (`importers/retraction_watch_importer.py`) | `bmlib/publications/fetchers/` | HIGH | S | stdlib parser |
| 11 | PubMed abstract-markdown + grant/affiliation extraction (from `pubmed_bulk_importer.py`) | graft onto `publications/fetchers/pubmed.py` | HIGH | S | none |
| 12 | `discovery/` PDF-acquisition stack (data_types, resolvers, pdf_verifier, PMC downloader, full_text_finder) | `bmlib/discovery/` (new) | HIGH | L | httpx/optional playwright+PyMuPDF |
| 13 | `pubmed_search/` live-search stack (constants, data_types, search_client, query_converter) | `bmlib/pubmed_search/` (new) | HIGH | M | requests→httpx, config→param |
| 14 | ClinicalTrials.gov source (`importers/clinicaltrials_importer.py`) | `bmlib/publications/fetchers/` | HIGH | M | ⚠ legacy XML deprecated (see caveats) |
| 15 | BaseAgent enhancements (metrics, regenerate-on-JSON-fail, `_generate_embedding`) | merge into `bmlib/agents/base.py` | MEDIUM | M | keystone for agent ports |
| 16 | `context_processor/` (iterative/semantic chunk processor) | `bmlib/` | MEDIUM | S–M | callback-injected (clean) |
| 17 | Review building-blocks (`systematic_review/data_models.py`: `ScoringWeights`, `SearchCriteria`, `ReviewStatistics`/PRISMA-flow) | `bmlib/quality/` or `bmlib/review/` | MEDIUM | S | pure |
| 18 | Prompt-driven agents (pico, editor, study_assessment, scoring, citation, counterfactual, transparency, reporting, prisma2020) | `bmlib/agents/` | MEDIUM | M each | config→inject; strip audit/queue |
| 19 | Summarizer (`paper_reviewer/summarizer.py`) | `bmlib/agents/` | MEDIUM | M | BaseAgent |
| 20 | Claim-verification prompts (`paperchecker/components/*`) | `bmlib/agents/` | MEDIUM | M | rewrite onto BaseAgent |
| 21 | Unified MeSH module (`mesh/lookup.py` + `pubmed_search/mesh_lookup.py`, consolidated) | `bmlib/mesh/` (new) | MEDIUM | M | API-mode pure; PG-mode injected |
| 22 | SSRF guard + generic sanitizers/coercers (`utils/url_validation.py`, `utils/validation.py` subset) | `bmlib/utils.py` (new) | MEDIUM | S | pure |
| 23 | `medrxiv_content_extractor` HTML→markdown retrieval tier | graft onto `fulltext/service.py` | MEDIUM | M | bs4/markdownify |
| 24 | `mesh_importer.py` vocabulary parsers | `bmlib/mesh/` | MEDIUM | L | large + schema-heavy |
| 25 | Small pure gems (`transparency_data.py` funders/trial-ids, `_extract_thinking`, `query_syntax`, `citation_validation`, formatters, counterfactual models) | various | LOW–MED | S | pure |

Legend — Effort: S (<0.5d), M (~1–2d), L (3d+).

## Tier 1 — quick wins (pure, near-zero coupling, fill real gaps)

These are the ports to do first: no DB, no config, mostly stdlib, and each
closes a genuine gap in bmlib.

- **`json_repair.py` (663 LOC, stdlib only).** Repairs malformed LLM JSON via
  a quote-state-machine + iterative reparse: single→double quotes, trailing/
  missing commas, unescaped control chars, truncated brackets, unquoted keys.
  bmlib today only *extracts* JSON (`llm/utils.py::extract_json`,
  `BaseAgent.parse_json`) — it has **no repair step**. Flagged independently by
  three of the six passes. Wire `repair_json`/`safe_json_loads` into
  `BaseAgent.chat_json`'s retry fallback. Port its behavior tests too — the
  missing-comma heuristics are aggressive.
- **Text chunking.** bmlib has **no** chunking utility. Two exist upstream:
  `agents/text_chunking.py` (sliding-window char chunker + `chunk_text`) and
  the richer `paper_reviewer/text_utils.py` (paragraph/sentence-aware
  `chunk_text` with overlap that never drops text, plus `process_with_map_reduce`
  and `process_with_rolling_summary`). Consolidate into one
  `bmlib/llm/text_utils.py`; prefer the paper_reviewer version's boundary logic.
- **Cochrane models + formatter (pure, zero-coupling).** `cochrane_models.py`
  is a strict **superset** of bmlib's 5-string `BiasRisk`: 9-domain Risk-of-Bias
  with judgement + rationale, split subjective/objective detection bias, and a
  full **study-characteristics** table (participants, interventions, outcomes,
  funding, COI, trial registration, ethics, follow-up). `cochrane_formatter.py`
  renders MD + HTML tables and a RoB summary matrix with judgement symbols + CSS.
  Drop into `bmlib/quality/` alongside (or to upgrade) `BiasRisk`.
- **Rule-based extractors (`paper_weight/extractors.py`, pure re+math).**
  Keyword study-type detection with **exclusion patterns** (so "non-randomized
  trial" won't match RCT), title-weighting, 9 sample-size regexes with log10
  scoring, power-calc/CI detection. A free rule-based pre-filter/fallback for
  `quality/`'s LLM-only Tier 2.
- **`pdf_converter.py` (317 LOC).** Pluggable PDF→text: `PDFConverter` ABC +
  `PyMuPDFConverter` + `get_converter()` registry, `ConversionResult` with
  `completion_ratio` validation, registry stubs for marker/docling. Already
  matches bmlib's registry pattern and lazy-optional-dep style. Land as a
  `bmlib.fulltext[pdf]` extra.
- **Small pure gems** worth lifting even without their parent module:
  `transparency_data.py::is_likely_industry_funder` + `extract_trial_registry_ids`
  (reconcile with existing `transparency/`); `qa/document_qa.py::_extract_thinking`
  (strip `<think>…</think>` reasoning blocks → `(answer, reasoning)`);
  `agents/utils/query_syntax.py` (`fix_tsquery_syntax`, `extract_keywords_from_question`);
  `agents/formatters/` + `agents/models/counterfactual.py` (pure).

## Tier 2 — net-new capabilities (moderate refactor, high value)

bmlib genuinely lacks these; each is a new subpackage or a substantial graft.

- **PDF acquisition — `bmlib/discovery/` (new).** This is the single biggest
  capability gap. `bmlib.fulltext` returns full *text/URLs*; `discovery`
  **downloads actual PDF files to disk** and verifies them. Port the clean core:
  `data_types.py` (source/priority models, pure), `resolvers.py` (DirectURL,
  DOI via CrossRef+content-negotiation, PMC OA package, Unpaywall, CrossRef-title
  fuzzy match, OpenAthens — a clean plugin pattern, zero coupling),
  `pdf_verifier.py` (magic-byte + PyMuPDF validity, extract DOI/PMID/title,
  prioritized match ladder), the FTP tar.gz **downloader** from
  `pmc_package_downloader.py`, and `full_text_finder.py` (orchestrator with a
  wrong-PDF-avoidance retry loop). During the port: **reuse bmlib's JATSParser**
  (don't port the redundant `NXMLParser`), make playwright + PyMuPDF optional
  deps, inline the SSRF validator, and move `requests`→httpx.
- **PubMed live search — `bmlib/pubmed_search/` (new).** Distinct from bmlib's
  date-based *sync* fetcher: this is *query-driven* search (NL question → LLM
  PubMed-boolean-query conversion → esearch/efetch with history-server paging,
  relevance sort, POST fallback). `search_client.py` and the `PubMedQuery`/
  `QueryConcept` dataclasses are self-contained; `query_converter.py` swaps
  `bmlibrarian.config` for a param and can reuse the new MeSH module. Move the
  inline prompts to `bmlib.templates`.
- **PDF section segmentation — into `bmlib/fulltext/`.** `pdf_processor/segmenter.py`
  is **pure stdlib** and detects standard sections including
  **FUNDING / CONFLICTS / DATA_AVAILABILITY / AUTHOR_CONTRIBUTIONS** via
  font-size/bold heuristics + regex. That directly feeds `transparency/` and
  `quality/` (locating COI/funding/data statements). The PyMuPDF `extractor.py`
  is separable and optional.
- **Cochrane assessor agent — into `bmlib/quality/`.** The **least-coupled
  agent in the whole repo**: only BaseAgent + the Cochrane models, takes
  `model`/`host` as params, no config/DB/orchestrator. Produces a full
  `CochraneStudyAssessment` from title+text. Swap `_generate_and_parse_json`→
  `chat_json` and it lifts cleanly as a "Cochrane tier" for the quality pipeline.
- **New publication sources — into `publications/fetchers/`.**
  - **Retraction Watch** (`retraction_watch_importer.py`, stdlib CSV parser):
    robust `_find_column()` against RW's shifting headers, multi-encoding +
    multi-date-format fallback. Reshape into a `retraction` source whose parser
    yields `(doi, pmid, reason, date, nature)`; drop the transparency-schema
    write tail. New capability: retraction flagging.
  - **ClinicalTrials.gov** (`clinicaltrials_importer.py`): `ClinicalTrial`
    dataclass + `parse_trial_xml()` (NCT id, sponsor `agency_class`
    NIH/Industry/Other, status, has_results) + resumable ranged downloader.
    New transparency signal (trial sponsorship/COI). **See caveat below** —
    the legacy bulk XML dump was deprecated.
- **Graft: richer PubMed metadata.** `pubmed_bulk_importer.py::_format_abstract_markdown`
  preserves structured-abstract section labels and scientific notation
  (`H₂O`→`H~2~O`, `m²`→`m^2^`), and it extracts `<GrantList>` grants,
  `publication_types`, `<AffiliationInfo>`, and retraction status — fields
  bmlib's `FetchedRecord` currently omits. Graft these onto the existing
  `publications/fetchers/pubmed.py::_parse_article_xml`.
- **Graft: HTML→markdown retrieval tier.** `medrxiv_content_extractor`'s
  priority fallback (text → HTML→markdown → JATS → PDF) adds a scraping tier
  (`CONTENT_SELECTORS`/`REMOVE_SELECTORS` + `_clean_markdown`, bs4+markdownify)
  that `fulltext/service.py` lacks. Generalize the medRxiv-specific URL bits.

## Tier 3 — reconcile / consolidate (medium value)

- **BaseAgent enhancement (keystone — do before the agent family).** Merge the
  useful extras from bmlibrarian's `agents/base.py` (`PerformanceMetrics`,
  `_generate_and_parse_json` with regenerate-on-parse-failure, `_generate_embedding`,
  `test_connection`) into bmlib's leaner injected `BaseAgent`. Drop the queue/
  orchestrator hooks. This unblocks clean ports of the prompt-driven agents.
- **Prompt-driven agent family.** These take plain inputs, build prompts, call
  base LLM helpers, and return dataclasses — their only coupling is
  config-at-`__init__` (→ inject) and optional audit/`*_by_id`/DB paths
  (→ strip to the app). Rough order by cleanliness: **`pico_agent`** (cleanest,
  no config/DB imports), `editor_agent`, `study_assessment_agent` (reconcile
  with `quality.StudyClassifier`/`QualityAgent` — its design classification is
  redundant), `scoring_agent`, `citation_agent`, `counterfactual_agent`,
  `transparency_agent` (reconcile with existing `transparency/`), then the large
  ones `reporting_agent` and `prisma2020_agent` (L each; PRISMA's semantic-search
  path needs DB/embeddings injection).
- **`paper_weight` weighted scoring.** Port the pure parts — `models.py`
  (`AssessmentDetail`/`DimensionScore` per-component audit trail),
  `prompts.py` + `llm_assessors.py` (granular methodological-quality and RoB
  rubrics) — and **re-wire the orchestration onto `bmlib.quality`** instead of
  its own study-type/RoB LLM calls. Leave `db.py`/`validators.py`/
  `llm_extractors.py` (schema + embedding coupled) behind. Net add over
  `quality/`: sample-size scoring, replication status, dimension weighting,
  audit trail.
- **Review building-blocks** from `systematic_review/data_models.py`:
  `ScoringWeights` (8-dimension weights with `validate` + presets),
  `SearchCriteria`, `ReviewStatistics` (PRISMA-flow counts) — clean,
  self-contained primitives for anyone assembling a review.
- **`context_processor/`** — a clean, callback-injected iterative
  context-window processor (batch oversized items, consolidate). Good early
  port; no DB/config/ollama.
- **Summarizer** (`paper_reviewer/summarizer.py`) — reusable brief-summary +
  core-hypothesis extraction with chunked map-reduce; port prompts + flow onto
  bmlib BaseAgent.
- **Claim-verification prompts** (`paperchecker/components/`): the value is the
  prompt engineering (semantically-precise negation, HyDE, 3-way verdict rubric)
  and validation dataclasses, not the plumbing (each class re-implements
  ollama-direct retry/JSON boilerplate that `BaseAgent.chat_json` already does).
  Rewrite as thin BaseAgent helpers; keep the prompts, `_normalize_statement_type`,
  and the pure `_format_overall_assessment`.
- **Unified MeSH module** (`bmlib/mesh/`, new). Consolidate the two upstream
  implementations into one (use the fuller `mesh/lookup.py::MeSHService`).
  The NLM-Browser-API + NCBI-esearch fallback path is portable now; make the
  local-PostgreSQL accelerator optional via an injected connection (it assumes
  `mesh.*` stored functions). Optionally add `mesh_importer.py`'s streaming
  XML parsers if consumers need a local MeSH mirror (larger, schema-heavy).
- **Generic validators** into a small `bmlib/utils.py`: `is_private_ip_address`
  + `validate_url_https` (an **SSRF guard** the httpx-based fulltext/transparency/
  publications modules currently lack), `sanitize_filename`, `sanitize_sql_identifier`,
  `ensure_list/dict/string/int/float` coercers. Leave the app-config validators behind.

## Do NOT port (out of scope or redundant)

- **GUI** — all of `gui/` (flet + Qt/PySide6), `factchecker/gui/`,
  `discovery/verification_dialog.py`. Explicitly excluded.
- **Interactive CLIs** — every `cli/`, `verification_prompt.py`,
  `analyze_factcheck_progress.py`.
- **App orchestration/state** — `agents/orchestrator.py`, `queue_manager.py`
  (SIGTERM/atexit side effects are library-hostile), `factory.py`,
  `human_edit_logger.py`, `scoring_agent_audit_methods.py`, and most of
  `systematic_review/` (`agent`, `executor`, `cache_manager`, `resume_mixin`,
  `documenter`, `config`, `quality`, `reporter`, `synthesizer`).
- **DB-schema / embedding coupled** — `semantic_query_agent.py`,
  `query_agent.py` search execution, `pdf_ingestor.py`, `pdf_matcher.py`,
  `paperchecker/agent.py` + `database.py` + `search_coordinator.py`,
  `qa/document_qa.py` (except `_extract_thinking`), `writing/document_store.py`,
  `audit/`, `evaluations/`, `benchmarking/`, `validation/`, `paper_weight/db.py`.
- **`bmlibrarian.llm/`** — already a shim over `bmlib.llm`; bmlib's is richer
  (7 providers vs 3). The only arguable back-port is the client's
  retry-with-Ollama-fallback wrapper, but that reads as app policy.
- **`models/document.py`** — a `TypedDict` shaped for bmlibrarian's PG rows;
  violates the dataclass convention and overlaps `publications.Publication`.
  Extend `Publication` instead if a canonical Document is wanted.
- **`factchecker/db/`** — a domain repository (statements/annotators/annotations)
  over a fact-check schema, with a "Postgres primary + SQLite export package"
  pattern. It does **not** duplicate `bmlib.db` (different layer) but is
  app-specific. The export-package idea is worth remembering, nothing to lift.
- **`thesaurus/expander.py`** — locked to a bespoke `thesaurus.*` PG schema with
  stored functions bmlib doesn't ship. Salvage only the schema-independent
  `_extract_terms` tsquery tokenizer (handles quoted phrases, hyphenated/Greek/
  Unicode medical terms).
- **Heavy/app utils** — `browser_downloader.py` (playwright+selenium+
  undetected-chromedriver, 2402 LOC), `openathens_auth.py` (playwright auth —
  auth is out of scope), `pdf_manager.py` (app orchestration; fulltext already
  covers retrieval), `config_loader.py` + `path_utils.py` config helpers
  (app config system).
- **Duplicate incremental importers** — `pubmed_importer.py`,
  `medrxiv_importer.py`, `europe_pmc_importer.py`, `pmc_bulk_importer.py`
  duplicate existing fetchers/`fulltext` JATS handling (harvest only the deltas
  noted above). **`NXMLParser`** — redundant with bmlib's `JATSParser`; cherry-pick
  only its MathML→Unicode and funding/COI/table-to-markdown extraction into
  `jats_parser` if markdown output is wanted.
- **`medrxiv_meca_importer.py`** — boto3 + paid requester-pays S3; off-ethos for
  a minimal-deps library.
- **europe_pmc bulk downloaders** — clean and decoupled, but "mirror the whole
  OA corpus" is a niche bulk use case, not a library primitive.

## Suggested sequencing

- **Phase 0 — quick wins:** #1 json_repair, #2 text chunking, #3 Cochrane
  models+formatter, #5 extractors, #7 pdf_converter, plus the small pure gems.
  All S, all pure, each closes a real gap. Ship as small independent PRs with
  behavior tests.
- **Phase 1 — foundation:** #15 BaseAgent enhancement (keystone), #16
  context_processor. Do these before the agent family.
- **Phase 2 — new capabilities:** #4 citations, #8 PDF segmenter, #9 Cochrane
  assessor, #10 Retraction Watch, #11 PubMed-metadata graft. Independent,
  parallelizable.
- **Phase 3 — larger subsystems:** #12 `discovery/`, #13 `pubmed_search/`,
  #21 MeSH, #14 ClinicalTrials (pending the caveat).
- **Phase 4 — agent family + reconciliations:** #18 prompt-driven agents, #6
  paper_weight, #17 review building-blocks, #19 summarizer, #20 claim-verification.
  Each reconciled against existing `quality/` and `transparency/`.

## Open questions / caveats

- **ClinicalTrials.gov (#14):** the classic `AllPublicXML.zip` bulk dump and its
  `<clinical_study>` schema were **deprecated in the 2024 API v2 migration**.
  The parser targets the old XML; verify the source still serves before porting,
  or retarget it at API v2 JSON.
- **transparency reconciliation (#18):** bmlib already has a `transparency/`
  module. Diff `transparency_agent.py`/`transparency_data.py` against it before
  porting to avoid a second parallel implementation — likely only the pure
  helpers (`is_likely_industry_funder`, `extract_trial_registry_ids`) and any
  enums are worth merging.
- **quality reconciliation (#3, #6, #9):** upstream quality code is **richer,
  not redundant**, in exactly three areas bmlib lacks — Cochrane 9-domain RoB +
  study characteristics, multi-dimensional weighted scoring with audit trail,
  and text chunking. Its study-*design classification* itself duplicates
  `quality.StudyClassifier` + `metadata_filter` — re-base on those, don't fork.
- **fact_checker duplication:** `agents/fact_checker_agent.py` +
  `agents/fact_checker_db.py` carry an `__init__` note that the agent "moved to
  `bmlibrarian.factchecker`" — the `agents/` copy may be stale. Confirm which is
  canonical before touching either (both are app-orchestration/out-of-scope
  regardless).
- **SSRF (#22):** bmlib's httpx-based modules make outbound requests to
  user/DOI-supplied URLs with no private-IP guard today. Porting
  `is_private_ip_address` is cheap security hardening independent of everything else.
