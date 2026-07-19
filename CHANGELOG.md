# Changelog

All notable changes to bmlib are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); bmlib follows
[Semantic Versioning](https://semver.org/).

## [0.4.0] — 2026-07-19

### Changed — breaking

- **`bmlib.db.transaction()` no longer commits when joining an open
  transaction** (SQLite savepoint path). Previously, a `transaction(conn)`
  block entered while the connection already held uncommitted writes would
  call `conn.commit()` on success, committing the caller's pending writes
  along with its own. Now the block joins via a savepoint and the owner of
  the enclosing transaction commits. Code that relied on `transaction()` as
  a durability checkpoint after bare `execute()` writes must commit
  explicitly (or wrap the whole batch in an outer `transaction()`). The same
  applies to `run_migrations()` when called with a transaction already open.
  On PostgreSQL the old connection-wide commit behaviour is unchanged (no
  savepoint nesting is implemented there).
- **`bmlib.publications.sync()` buffers each day's records and stores them
  after the fetch.** The `on_record` callback now fires while the fetcher
  streams, *before* the record is stored — callbacks must not expect to read
  the record back from the database. Writes cost one commit per day instead
  of one per statement, and SQLite's write lock is no longer held across
  network I/O; in exchange, a day's records are held in memory during the
  fetch.
- `TransparencyAnalyzer._check_europepmc()` now returns a 6-tuple (adds
  `industry_coi`).

### Added

- transparency: industry conflict-of-interest detection in full-text
  COI/disclosure statements — negation-aware, scoped to the COI region, with
  a guard for non-industry contexts (university/government employment,
  editorial boards). ORs into `industry_funding_detected` at moderate
  confidence (#7).
- llm: embedding support in the LLM abstraction layer (`LLMClient.embed()`,
  `EmbeddingResponse`). Implemented by the Ollama provider; other providers
  inherit `BaseProvider.embed()`, which raises `NotImplementedError`.
- llm: tool calling — `LLMClient.chat()` accepts `tools` and `tool_choice`,
  with the new `LLMToolDefinition` and `LLMToolCall` data types,
  `LLMResponse.tool_calls`, and `LLMMessage.tool_calls` / `tool_call_id` for
  multi-turn tool conversations. Implemented for Anthropic, Ollama, and the
  OpenAI-compatible providers (OpenAI, DeepSeek, Mistral, Gemini). Passing
  `tools` to a provider that does not support them raises
  `NotImplementedError` before any network call. Ollama accepts but ignores
  `tool_choice` — its native API has no equivalent.
- db: nested `transaction()` blocks on SQLite are now composable (savepoint
  join; the outer block owns the commit).
- llm: `bmlib.llm.json_repair` — repairs malformed LLM JSON (single quotes,
  trailing/missing commas, unescaped control chars, truncation, unquoted
  keys) via `repair_json()`, `safe_json_loads()`, `extract_and_repair_json()`.
  `BaseAgent.parse_json()` now uses it as a last-resort fallback. Ported from
  bmlibrarian.
- llm: `bmlib.llm.text_utils` — boundary-aware text chunking (`TextChunk`,
  `TextChunker`, `chunk_text`) that never drops text, plus map-reduce /
  rolling-summary long-document processing and document-text helpers. Ported
  and consolidated from bmlibrarian's two chunkers.
- quality: `bmlib.quality.cochrane_models` and `cochrane_formatter` —
  Cochrane-aligned nine-domain Risk-of-Bias models with judgement + rationale,
  the full study-characteristics table, and Markdown/HTML renderers. A strict
  superset of `BiasRisk`. Ported from bmlibrarian.
- quality: `bmlib.quality.extractors` and `scoring_models` — rule-based
  (LLM-free) study-type detection with exclusion-context guarding and
  sample-size scoring, producing `DimensionScore` audit trails. Ported from
  bmlibrarian's paper_weight.
- fulltext: `bmlib.fulltext.pdf_converter` — pluggable PDF→text conversion
  (`ConversionResult`, `PDFConverter`, `get_converter`, `list_converters`)
  with a PyMuPDF backend behind the new optional `bmlib[pdf]` extra. Ported
  from bmlibrarian.

### Fixed

- publications: batched database commits — one commit per stored publication
  and one per synced day instead of one per statement (#8).
- llm: `get_llm_client()` singleton creation is now thread-safe; the
  openai-compat `list_models()` caches a successful-but-empty response for
  the TTL instead of re-hitting the API every call; the Anthropic provider
  warns (once per model per instance) when an unknown model id falls back to
  estimated pricing (#9).
- fulltext: `FullTextCache` sanitizes identifiers internally, so a raw DOI or
  path-traversal string cannot write outside the cache directory;
  already-safe identifiers keep their exact filenames (#9).
- publications: the OpenAlex fetcher tolerates a `"meta": null` page instead
  of raising `AttributeError` (#9).
- agents: `chat_json()` now fails fast with the real cause when a response is
  truncated at the `max_tokens` ceiling, instead of reporting a generic
  "unparseable response". At `temperature == 0.0` it raises immediately —
  greedy sampling reproduces the identical truncation, so retrying only pays
  for it again; above 0.0 it retries, since a different sample may fit. A
  response that is complete JSON despite hitting the ceiling is returned
  rather than rejected. Truncation detection covers Anthropic's
  `stop_reason="max_tokens"` and the OpenAI-compatible `"length"`, and empty
  responses are now treated as retryable transport errors.
- fulltext: cache keys are now `{sanitized}_{sha1[:10]}`, so DOIs that
  differed only in characters the sanitizer collapsed (for example
  `10.1/a:b` and `10.1/a/b`) no longer share a cache file and serve each
  other's full text.
- fulltext: JATS parsing no longer drops abstract sections, mislabels table
  headers, or loses figure and table captions.
- fulltext: the final fallback result is labelled `source="pubmed"` rather
  than `"doi"` when it resolves to a PubMed URL.
- db: `create_tables()` no longer uses SQLite's `executescript()`, whose
  implicit `COMMIT` broke a surrounding `transaction()` block and left
  migrations non-atomic. Statements are split and executed individually.
- llm: provider names are normalised to lowercase in client routing, so
  `"Anthropic:claude-..."` resolves like `"anthropic:claude-..."`.
- llm: JSON extraction handles responses containing multiple objects and
  braces inside strings.
- llm: OpenAI reasoning models receive `max_completion_tokens` instead of the
  rejected `max_tokens`.
- llm: the Ollama provider no longer clobbers a legitimate zero token count
  when recording usage.
- quality: the Tier 1 metadata filter no longer misclassifies study designs
  from ambiguous PubMed publication types, and `QualityAssessment` records
  `is_randomized` from the new `DESIGN_TO_RANDOMIZED` mapping, so
  `QualityFilter.require_randomization` recognises a Tier 1/2 RCT instead of
  rejecting it.
- transparency: conflict-of-interest detection and the ClinicalTrials.gov
  posted-results check were both under-detecting — the latter requested
  `ResultsSection` but read `resultsSection`. The analyzer now returns an
  `UNKNOWN` risk level with score 0 when no external API was reachable,
  rather than letting an all-zero score read as HIGH risk.
- publications: full-text sources are no longer silently dropped during sync.
- publications: the bioRxiv fetcher records the correct PDF version, and the
  PubMed fetcher handles non-numeric month names in publication dates.

## [0.3.0]

Never released. The version string was bumped in-tree when embedding support
landed, but no release was cut; those changes ship as part of 0.4.0 above.

## [0.2.1] and earlier

No changelog was kept; see the git history.
