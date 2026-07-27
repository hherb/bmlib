# HANDOVER — bmlib development

_Last updated: 2026-07-28. `main` is at v0.5.1 plus a large `[Unreleased]`
section (PostgreSQL `publications`, PDF→text wiring, body-less JATS fix).
818 tests passing + 31 skipped._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Current state

- **Released: 0.5.1** (2026-07-21). `pyproject.toml` and `bmlib/__init__.py`
  agree. Release history: 0.4.0 (2026-07-19) → 0.5.0 (2026-07-20) → 0.5.1.
  0.3.0 was bumped in-tree but never released; its changes shipped inside
  0.4.0.
- **`[Unreleased]` is substantial and unreleased.** Three bodies of work sit
  on `main` waiting for a version:
  1. **`bmlib.publications` on PostgreSQL** (PR #28) — `schema.py`,
     `storage.py`, `sync.py` were SQLite-only; every statement is now
     dual-dialect. Brought `db.is_sqlite()` / `placeholder()` /
     `placeholders()` / `transaction_depth()` / `owns_commit()` into the
     public API, added `publications.pmcid`, fixed `fetch_scalar()` on
     psycopg2 `RealDictRow`, and made `transaction()` nest via savepoints on
     PostgreSQL. `tests/test_backends.py` runs each test against both
     backends; CI sets `BMLIB_REQUIRE_POSTGRESQL=1` so a missing DSN fails
     rather than skips.
  2. **PDF→text wired into `FullTextService`** — a retrieved PDF is extracted
     into `FullTextResult.html` via the new `render_html()`; opt out with
     `convert_pdfs=False`. Added `FullTextResult.content_kind`
     (`fulltext` / `abstract` / `extracted`) and `ConversionResult.page_texts`.
  3. **Body-less JATS handled** (PR #29) — medRxiv serves `<front>`+`<back>`
     with no prose for some preprints; such a document is detected via
     `JATSArticle.has_body`, never cached, and held back as a last resort.
     Also fixed: a missing abstract no longer kills a scoring batch, and the
     Tier 2 classifier's token budget is no longer overridden at the call site.
  4. **Unsectioned JATS `<body>` parsed** (issues #30, #31) — loose `<p>`
     prose becomes a titleless `JATSBodySection` instead of being dropped,
     and `docs/manual/fulltext.md`'s duplicated `PDF Conversion` section is
     merged into one.
  **Deciding whether this is 0.6.0 is open work** — see "Next up" below. The
  `publications` and `fulltext` changes are additive, so a minor bump fits.
- **818 tests passing + 31 skipped** (`uv run pytest tests/ -q`). The 31 skips
  are the PostgreSQL parameterisations of `tests/test_backends.py`, which run
  only when `BMLIB_TEST_POSTGRESQL_DSN` is set.
- **Documentation was rewritten for 0.4.0 and updated through PR #28/#29.**
  Treat drift as a regression worth fixing, not expected staleness — but note
  issue #31 records one real defect (a duplicated section in
  `docs/manual/fulltext.md`).

## Next up

Pick from the open issues below, or resume the bmlibrarian porting effort.
Nothing is blocked on anything else.

### Open GitHub issues

- **#17 — consolidate duplicated JSON extraction** (`llm/utils.py::extract_json`
  vs `llm/json_repair.py::extract_and_repair_json`): unify the span-location
  logic behind one locator. Fold into the Phase 1 BaseAgent work below.
- **#18 — `TransparencyAnalyzer` accepts `pubmed_api_key` but never uses it**:
  remove (breaking) or wire it up when a real NCBI check is added. The manual
  documents it as accepted-but-unused.
- **#21 — transparency `UNKNOWN` results are distinguishable only by
  `risk_indicators` string matching**: add a structured `unknown_reason` enum
  when a consumer needs to branch on disabled vs no-identifier vs unreachable.

### bmlibrarian → bmlib porting (paused, Phase 1 next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

**Phase 0 is done** and shipped in 0.4.0: `llm/json_repair.py`,
`llm/text_utils.py`, `quality/cochrane_models.py` + `cochrane_formatter.py`,
`quality/extractors.py` + `scoring_models.py`, `fulltext/pdf_converter.py`.

**Phase 1 — the two next ports, in order:**

1. **BaseAgent enhancement (keystone — unblocks the Phase 4 agent family).**
   Source: `~/src/bmlibrarian/src/bmlibrarian/agents/base.py` (~1200 lines).
   Target: merge the genuinely useful extras **into** the existing
   `bmlib/agents/base.py` — do **not** ship a second base class:
   - `PerformanceMetrics` (per-agent timing/success/token accounting).
   - `_generate_and_parse_json`'s *regenerate-on-parse-failure* behaviour —
     reconcile with bmlib's existing `chat_json()` retry loop, don't duplicate.
   - `_generate_embedding` — wire to `LLMClient.embed()`, don't reimplement.
   - optionally `test_connection`.
   - **Drop** all queue/orchestrator hooks (`submit_task`, queue_manager
     coupling) and any `bmlibrarian.config` reads — bmlib's base stays
     injection-only.
   - Do **issue #17** while in there.
2. **`context_processor`.** Source:
   `~/src/bmlibrarian/src/bmlibrarian/agents/context_processor/` (~840 lines,
   already callback-injected — clean). Target: `bmlib/context_processor/` or
   under `bmlib/agents/`. It batches oversized items across an LLM context
   window and consolidates results. `create_prisma_chunk_processor` is
   PRISMA-specific — leave that factory in the app, port the generic core. It
   can build on `bmlib/llm/text_utils.py`.

Later phases (2–4: citations, discovery, pubmed_search, MeSH, the
prompt-driven agent family, paper_weight) are in the analysis doc.

### The port recipe (how Phase 0 was done — repeat it)

1. **TDD, always.** Write behaviour tests first (the upstream code is the
   spec), watch them fail (a `ModuleNotFoundError` is the correct red for a
   new module), then port and watch them pass. Bug in a test you wrote? Fix
   the test, not correct code.
2. **Modernise to bmlib style:** AGPL header on every file,
   `from __future__ import annotations`, lowercase builtin generics
   (`list`/`dict`/`X | None`), `datetime.UTC`.
3. **Sever app coupling:** replace `get_db_manager()`/`bmlibrarian.config`
   with injected connections/params; guard optional deps with
   `try/except ImportError` raising `pip install bmlib[extra]`; route LLM
   calls through `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** the public names from the package `__init__.py` `__all__`.
5. **Verify:** tests + both ruff commands clean before done.
6. **Record** each port in `CHANGELOG.md` under `[Unreleased]`.
7. **Reconcile, don't fork:** where a port overlaps existing bmlib (quality
   study-classification, transparency), build on the existing module.

## Deliberate non-fixes — do not "fix" these

Each was investigated and closed as correct. Reopening them wastes a session.

- **`TransparencySettings.filtering_enabled`, `max_concurrent_analyses`,
  `cache_results` are not dead code.** They are orchestration hints for the
  *calling* application. The library analyses one document per call and does
  no filtering, threading, or caching of its own. The docstring says which
  fields the analyzer honours and which the caller owns.
- **`outcome_switching_detected` stays reserved and always `False`.** Deciding
  it means comparing a trial's pre-registered primary outcomes against those
  reported — a real feature with real false-positive risk, not a fix. It stays
  in the schema so persisted results need no migration when detection lands.
- **`Publication.pmcid` is declared last on the dataclass, not beside `pmid`.**
  `Publication` is constructed positionally by downstream projects, so any
  other placement shifts every following argument and lands a caller's
  `abstract` in `pmcid` with no error anywhere. Pinned by
  `test_positional_construction_is_stable_across_versions`.
- **PostgreSQL transaction nesting is detected from bmlib's own open-block
  count, not psycopg2's status.** psycopg2 opens a transaction on the first
  statement of *any* kind, so a bare `SELECT` would make every following block
  look nested and stop committing. The count is keyed by *(thread,
  `id(conn)`)* — see CLAUDE.md for why both parts are load-bearing.
- **The Ollama raw `/api/tags` path re-implements httpx's safety defaults on
  purpose** (HTTP(S)-only scheme, bearer token stripped across cross-origin
  redirects, `"<word>:<digits>"` read as host:port). Simplifying any of these
  back to the obvious one-liner reintroduces a real defect; each has a
  regression test naming it.
- **The unsectioned-`<body>` branch in `_JATSHandler.endElement` sits *below*
  the figure and table branches, not next to the sectioned-prose branch it
  reads as a pair with.** Figure and table captions are `<p>` elements too, and
  outside a `<sec>` they reach the same chain; testing `in_body` first blanks
  the caption, reprints it as article prose, and makes a `<body>` holding only
  a captioned figure report `has_body`. Pinned by
  `TestJATSParserUnsectionedBodyFurniture`.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API connection
  as first argument, type hints and docstrings on everything public, AGPL-3
  header on every source file, dataclass models with `to_dict()`/`from_dict()`,
  explicit SQL (no ORM), optional dependencies guarded with a helpful
  `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`.** CI pins
  **ruff 0.15.20** (`.github/workflows/ci.yml`); the `.venv` currently holds
  0.6.5, which false-flags `UP038` on `ollama.py` — a rule newer ruff removed.
  Use:

  ```bash
  uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
  ```

- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP;
  no external services. To run the PostgreSQL half of `test_backends.py`, set
  `BMLIB_TEST_POSTGRESQL_DSN` to a database the tests may drop every table in.
- New functionality needs unit tests; see CLAUDE.md's test-file mapping table.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
