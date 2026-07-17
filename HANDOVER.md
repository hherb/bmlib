# HANDOVER — bmlib development

_Last updated: 2026-07-17 (handover file created; main at 408 tests passing,
v0.3.0 unreleased, no open PRs)._

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
- Recent merged work (PRs #10 and #11): comprehensive code-review fix
  batches, `perf(publications)` one-commit-per-synced-day, and
  industry-COI detection in full text (`_check_europepmc()` now returns a
  6-tuple adding `industry_coi`).
- **408 tests passing** (`uv run pytest tests/ -q`). No open PRs.

## Open work

### 1. Documentation refresh (do this first)

All documentation must be brought up to date with the current code. Known
stale spots (verified 2026-07-17):

- `README.md` still says **Version: 0.2.1**; `pyproject.toml` is 0.3.0.
- `docs/manual/database.md` does not document the new `transaction()`
  semantics: joining an already-open SQLite transaction now uses a
  savepoint and the owner of the enclosing transaction commits (breaking
  change, see CHANGELOG 0.3.0). Same for `run_migrations()` under an open
  transaction.
- `docs/manual/publications.md` does not document sync buffering: records
  are buffered per day and stored after the fetch; `on_record` fires while
  the fetcher streams, *before* the record is stored.
- `docs/manual/transparency.md` does not document industry-COI detection in
  full text (`industry_coi`).
- While there, sweep the remaining `docs/manual/*.md` pages and `CLAUDE.md`
  against the current APIs and verify the code examples still run.

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
