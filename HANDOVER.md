# HANDOVER — bmlib development

_Last updated: 2026-07-18 (0.3.0 documentation refresh done; issues #12 and
#13 fixed; 547 tests passing + 1 skipped on this branch, PR pending)._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history and `docs/plans/` — do not
re-narrate it here.

## Current state

- **v0.3.0 is unreleased**: `pyproject.toml` says 0.3.0 and `CHANGELOG.md`
  has a populated `[0.3.0] — Unreleased` section with two breaking changes
  (`transaction()` savepoint-join semantics; `sync()` per-day commit
  batching with `on_record` firing before storage).
- Phase 0 of the bmlibrarian port is merged (PR #16): `llm/json_repair`,
  `llm/text_utils`, `quality/cochrane_*`, `quality/extractors` +
  `scoring_models`, `fulltext/pdf_converter` (new `bmlib[pdf]` extra).
  CI hardening + dependabot merged (PR #15).
- **Documentation is refreshed for 0.3.0** (this branch): README and manual
  version strings, `transaction()` savepoint semantics + a new Migrations
  section (`docs/manual/database.md`), sync write-batching and
  `FetchedRecord`/registry docs (`publications.md`), full-text COI pipeline
  incl. industry-COI and structural tagged-section detection
  (`transparency.md`), new-module sections in `llm.md`, `quality.md`,
  `fulltext.md`, `chat_json()` in `agents.md`, and a CLAUDE.md sweep. All
  runnable doc examples were executed against the code.
- Issues **#12** (`list_models()` cache aliasing) and **#13** (tagged COI
  section without cue phrase) are fixed with regression tests (this branch).
- **547 tests passing + 1 skipped** (`uv run pytest tests/ -q`).

## bmlibrarian → bmlib porting (active effort)

The "mother project" `~/src/bmlibrarian` holds a lot of functionality that
belongs in bmlib but was never ported. The full assessment and phased backlog
live in [`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

Phase 0 (five pure/near-pure quick-wins) is merged — see `CHANGELOG.md`.

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
   - While in there, do **issue #17**: consolidate the duplicated
     JSON-extraction logic in `llm/utils.py::extract_json` vs
     `llm/json_repair.py::extract_and_repair_json` behind one shared span
     locator (see the issue for the suggested split).
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
6. **Record** each port in `CHANGELOG.md` (`0.3.0 — Unreleased`, `### Added`).
7. **Reconcile, don't fork:** where a port overlaps existing bmlib (quality
   study-classification, transparency), build on the existing module — see
   the analysis doc's "reconciliation" caveats.

Later phases (2–4: citations, discovery, pubmed_search, MeSH, the
prompt-driven agent family, paper_weight) are laid out in the analysis doc.

## Other open work

- **Release 0.3.0** once the current PR lands — CHANGELOG `Unreleased` and
  the documentation are now in sync, so this is mostly tagging/packaging.
  Decide with the maintainer whether to release before or after Phase 1.
- **#17 — consolidate duplicated JSON extraction** (`llm/utils.py` vs
  `llm/json_repair.py`): folded into the Phase 1 BaseAgent work above.
- **#18 — `TransparencyAnalyzer` accepts `pubmed_api_key` but never uses
  it**: remove (breaking) or wire it up when a real NCBI check is added.
  The manual documents it as accepted-but-unused for now.

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
  pinned version, see `.github/workflows/ci.yml`; `uvx ruff@<pinned>` works).
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked
  HTTP; no external services. New functionality needs unit tests.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
