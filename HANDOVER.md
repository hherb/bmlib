# HANDOVER — bmlib development

_Last updated: 2026-07-19 (v0.4.0 released; Phase 0 ports merged to `main`;
full documentation refresh done — CHANGELOG, README, CLAUDE.md and all eight
`docs/manual/` pages rewritten against the current APIs. 539 tests passing +
2 skipped on `main`)._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history and `docs/plans/` — do not
re-narrate it here.

## Current state

- **v0.4.0 is released.** `pyproject.toml` and `bmlib/__init__.py` both say
  0.4.0, and `CHANGELOG.md` has a dated `[0.4.0] — 2026-07-19` section. It
  carries three breaking changes (`transaction()` savepoint-join semantics;
  `sync()` per-day commit batching with `on_record` firing before storage;
  `_check_europepmc()` returning a 6-tuple). 0.3.0 was never released — the
  version string was bumped in-tree when embedding support landed but no
  release was cut, so those changes ship inside 0.4.0.
- **Documentation is current as of this release.** CHANGELOG, README,
  CLAUDE.md, and all eight `docs/manual/` pages were rewritten against the
  actual source, with every signature verified and code examples executed.
  Assume they are accurate; if you find drift, that is a regression worth
  fixing rather than expected staleness.
- Merged since the last handover: Phase 0 bmlibrarian ports (PR #16), CI
  hardening (PR #15), plus the earlier code-review fix batches, the
  one-commit-per-synced-day perf work, and industry-COI detection.
- **539 tests passing + 2 skipped** (`uv run pytest tests/ -q`) on `main`.

## bmlibrarian → bmlib porting (active effort)

The "mother project" `~/src/bmlibrarian` holds a lot of functionality that
belongs in bmlib but was never ported. The full assessment and phased backlog
live in [`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

### Phase 0 — DONE (merged, shipped in 0.4.0)

Five pure/near-pure quick-wins, each test-first, exported from its package,
recorded in `CHANGELOG.md` under `[0.4.0]`:

- `bmlib/llm/json_repair.py` — malformed-JSON repair; wired into
  `BaseAgent.parse_json()` as a fallback.
- `bmlib/llm/text_utils.py` — consolidated boundary-aware chunker +
  map-reduce / rolling-summary helpers.
- `bmlib/quality/cochrane_models.py` + `cochrane_formatter.py` — 9-domain RoB
  + study-characteristics + MD/HTML renderers.
- `bmlib/quality/extractors.py` + `scoring_models.py` — rule-based study-type
  / sample-size scoring with `DimensionScore` audit trail.
- `bmlib/fulltext/pdf_converter.py` — pluggable PDF→text, PyMuPDF backend
  behind the new optional `bmlib[pdf]` extra.

Code-review hardening landed on the same branch: whole-word study-type
keyword matching (an "RCT" keyword no longer matches "infarct"), decimal-only
CI patterns, O(n) JSON repair, truncated-JSON extraction in
`extract_and_repair_json`, consistent RoB domain labels across MD/HTML, and
`bmlib.llm` package exports. One refactor was deferred to issue #17:
consolidating the duplicated JSON-extraction logic in `llm/utils.py` vs
`llm/json_repair.py` — fold it into the Phase 1 BaseAgent work.

### Phase 1 — NEXT (do these two, in order)

Both are named in the analysis doc (#15, #16). Follow the **port recipe**
below for each.

1. **BaseAgent enhancement (keystone — do first; unblocks the Phase 4 agent
   family).** Source: `~/src/bmlibrarian/src/bmlibrarian/agents/base.py`
   (the app's own `BaseAgent`, ~1200 lines). Target: merge its genuinely
   useful extras **into** the existing `bmlib/agents/base.py` — do **not**
   ship a second base class:
   - `PerformanceMetrics` (per-agent timing/success/token accounting).
   - `_generate_and_parse_json`'s *regenerate-on-parse-failure* behaviour
     (re-prompt the model when JSON won't parse) — reconcile with bmlib's
     existing `chat_json()` retry loop rather than duplicating it.
   - `_generate_embedding` (bmlib already has `LLMClient.embed()` — wire the
     helper to that, don't reimplement).
   - optionally `test_connection`.
   - **Drop** all queue/orchestrator hooks (`submit_task`, queue_manager
     coupling) and any `bmlibrarian.config` reads — bmlib's base stays
     injection-only.
2. **`context_processor`.** Source:
   `~/src/bmlibrarian/src/bmlibrarian/agents/context_processor/` (base.py ABC
   + data_types.py + semantic_chunk_processor.py, ~840 lines, already
   callback-injected — clean). Target: `bmlib/context_processor/` (new
   subpackage) or under `bmlib/agents/`. It batches oversized items across an
   LLM context window and consolidates results. `create_prisma_chunk_processor`
   is PRISMA-specific — leave that factory in the app, port the generic core.
   It can build on the new `bmlib/llm/text_utils.py` chunker.

### The port recipe (how Phase 0 was done — repeat it)

1. **TDD, always.** Write behaviour tests first (the upstream code is the
   spec), watch them fail (a `ModuleNotFoundError` is the correct red for a
   new module), then port the implementation and watch them pass. Bug in a
   test you wrote? Fix the test, not correct code.
2. **Modernise to bmlib style:** AGPL header on every file (copy from any
   existing source), `from __future__ import annotations`, lowercase builtin
   generics (`list`/`dict`/`X | None`, not `List`/`Optional`), `datetime.UTC`.
3. **Sever app coupling:** replace `get_db_manager()`/`bmlibrarian.config`
   with injected connections/params; guard optional deps with
   `try/except ImportError` raising `pip install bmlib[extra]`; route LLM
   calls through `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** the public names from the package `__init__.py` `__all__`.
5. **Verify:** `uv run pytest tests/ -q`, `uv run ruff check bmlib/ tests/`,
   `uv run ruff format --check bmlib/ tests/` — all clean before done.
6. **Record** each port in `CHANGELOG.md` under a new `[Unreleased]`
   heading (`### Added`); it gets a version and a date when a release is cut.
7. **Reconcile, don't fork:** where a port overlaps existing bmlib (quality
   study-classification, transparency), build on the existing module — see
   the analysis doc's "reconciliation" caveats.

Later phases (2–4: citations, discovery, pubmed_search, MeSH, the
prompt-driven agent family, paper_weight) are laid out in the analysis doc.

## Open work

### 1. Defects found during the 0.4.0 documentation sweep (no issues filed)

Writing the manual against the real source surfaced these. None is fixed;
each was verified against the code, and the manual documents the *actual*
behaviour in every case. Ordered by how much they matter.

- **`fetch_pubmed` never populates `publication_types`.**
  `_parse_article_xml` (`bmlib/publications/fetchers/pubmed.py:122-228`)
  extracts MeSH keywords but no publication types; only the OpenAlex fetcher
  sets the field. This bites directly: `bmlib.quality`'s Tier 1 metadata
  filter classifies *from* `publication_types`, so synced PubMed records
  arrive without the one field the free tier needs and fall straight through
  to the paid LLM tier. Probably the highest-value fix on this list.
- **Overriding a built-in source name is racy.** `register_source()` does
  not call `_ensure_builtins()`, so registering a custom fetcher under
  `"pubmed"` before any lookup is silently overwritten when the first
  `get_fetcher()` / `source_names()` call triggers lazy registration. The
  `_builtins_registered` flag protects the built-ins, not custom overrides.
  Workaround documented in the manual: call `source_names()` first.
- **`TransparencyAnalyzer` is not thread-safe** — `_last_request` and
  `_api_reachable` are unsynchronised instance state, yet
  `TransparencySettings.max_concurrent_analyses` (default 3) invites
  concurrency. The manual documents a per-worker-instance pattern as the
  workaround; either synchronise the state or drop the setting.
- **Four `TransparencySettings` fields are read by nothing**: `enabled`,
  `filtering_enabled`, `max_concurrent_analyses`, `cache_results`. Either
  implement or remove them; as-is they read as configuration that works.
- **`outcome_switching_detected` is never assigned** — always `False`.
  The module intro previously implied outcome switching was analysed; it is
  a reserved field. Implement or document as reserved (the manual now does
  the latter).
- **`create_tables()` cannot parse trigger bodies.**
  `_split_sql_statements` splits on `;`, so a `CREATE TRIGGER ... BEGIN ...
  END;` script raises `sqlite3.OperationalError: incomplete input`
  (confirmed empirically). Workaround: create triggers via a separate
  `execute()`. Worth fixing if any consumer needs triggers.
- **`_check_trial_results`'s fallback is unreachable.** It requests
  `params={"fields": "hasResults"}`, so `resultsSection` can never be in the
  payload and `bool(data.get("resultsSection"))` is dead code. Harmless.
- **Stale type annotations**: all three built-in fetchers annotate
  `on_record` as `Callable[[dict], None]` though they pass a
  `FetchedRecord`. `sync()` has it right. Cosmetic but misleading.

### 2. Open GitHub issues

- **#13 — transparency: tagged COI section without a cue phrase yields
  `coi_disclosed=False`.** A non-empty tagged-section result from
  `_extract_coi_text()` should count as structural proof of disclosure,
  with the cue-phrase scan kept as fallback for untagged text. Add tests
  for the tagged-but-cue-less case, including that `SCORE_COI_DISCLOSED`
  is credited exactly once.
- **#12 — llm: `list_models()` returns the cached list by reference.**
  `openai_compat.py` and `anthropic.py` both return the same mutable list
  object; caller mutation corrupts the cache. Return a copy (or store a
  tuple) and add a regression test.

### Known limitations (no issue filed)

- The `publications/` storage layer is SQLite-specific (`?` placeholders,
  `ON CONFLICT`, `cur.lastrowid`) even though `db/` also supports
  PostgreSQL. Port when a PostgreSQL consumer needs it.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API
  connection as first argument, type hints and docstrings on everything
  public, AGPL-3 header on every source file, dataclass models with
  `to_dict()`/`from_dict()`, explicit SQL (no ORM), optional dependencies
  guarded with a helpful `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`. Lint:
  `ruff check .` and `ruff format --check .` (CI pins ruff — match the
  pinned version, see `.github/`).
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked
  HTTP; no external services. New functionality needs unit tests.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
