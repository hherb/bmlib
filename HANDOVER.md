# HANDOVER — bmlib development

_Last updated: 2026-08-10. **0.8.1 is cut on `release/0.8.1`** — five
`fulltext` fixes (#64, #67, #70, #71, #75), nothing stored moved. **The
release is not finished until the tag and the GitHub release exist** — see
"Finishing 0.8.1" immediately below, which is the first thing the next
session should check. Four open issues: **#56**, **#68**, **#72**, **#73**.
1774 tests + 58 skipped (1830 + 2 with a PostgreSQL DSN), ruff clean.
**After the release, Phase 3 of the bmlibrarian port is next, and each of its
rows needs a design conversation before any porting** — see "Next up"._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Finishing 0.8.1

Check this first: `git tag --list 'v0.8.1'` and `gh release view v0.8.1`. If
both exist and https://pypi.org/simple/bmlib/ lists 0.8.1, delete this
section and move on. Otherwise the version bump is merged but the release was
never published, and these steps remain:

1. Merge the release PR with **`--merge`, not squash**, so the tag lands on
   main's first-parent line.
2. Tag the **merge commit** `v0.8.1` and push the tag.
3. Create the GitHub release. **This is what publishes** —
   `.github/workflows/release.yml` rebuilds, refuses to go on unless the tag
   matches `bmlib.__version__`, runs `twine check --strict`, asserts
   `py.typed` survived packaging, and uploads via Trusted Publishing.
4. **Stop here and hand over the `pypi` environment gate.** Do not approve it
   yourself even when `gh api .../pending_deployments` reports
   `current_user_can_approve: true` — a PyPI upload is irreversible, the
   version number cannot be reused, and the gate exists as the human
   checkpoint for exactly that. Nothing is lost by waiting; the run stays
   approvable indefinitely.
5. Once approved, verify against `https://pypi.org/simple/bmlib/` — the JSON
   API serves a stale CDN cache, and the simple index is what installers
   read. A failed `pip install` immediately after is propagation, not a bad
   upload.

The release-workflow gates were already rehearsed locally on the release
branch — `uv build`, `twine check --strict` on both artifacts, `py.typed`
present in the wheel — and the wheel was installed into a venv holding only
`bmlib`, `jinja2` and `markupsafe` and probed one fresh interpreter per
module: **69 importable, 0 not**, which is #64's claim verified against the
artefact rather than the source tree. Worth repeating on any release that
touches an `__init__.py`.

**Do not upload by hand.** The publish job has no `skip-existing`, so a manual
upload makes it fail on a duplicate — which is why v0.5.0's and v0.6.0's runs
still sit unapproved. v0.7.0 and v0.8.0 both went the whole way through the
workflow.

## Current state

- **Version 0.8.1.** Release history: 0.4.0 (2026-07-19) → 0.5.0 → 0.5.1 →
  0.6.0 (2026-07-30) → 0.7.0 (2026-08-04) → 0.8.0 (2026-08-08) → 0.8.1
  (2026-08-10). 0.3.0 was bumped in-tree but never released; its changes
  shipped inside 0.4.0. The version lives in **four** places —
  `pyproject.toml`, `bmlib/__init__.py`, the README version line,
  `CLAUDE.md`'s header — and all four agree.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag,
  and they compound for anyone upgrading across them; 0.8.0's largest is the
  PubMed one, which changes the shape of every synced title and abstract.
  **0.8.1 moves nothing stored** — it is five fixes and some log lines.
- **`~/src/bmlibrarian` still pins `bmlib[ollama]>=0.5.1,<0.6.0`**, so it has
  now missed four releases. Widening it is a downstream change, not a bmlib
  one.
- **1774 tests passing + 58 skipped** (`uv run pytest tests/ -q`); **1830 + 2
  with `BMLIB_TEST_POSTGRESQL_DSN` set**. 56 of the default skips are the
  PostgreSQL parameterisations of `tests/test_backends.py`; 1 is a
  PostgreSQL-only schema test; 1 is `test_pymupdf_requires_dependency`, which
  runs only when PyMuPDF is *absent*. **PyMuPDF is installed in the dev
  venv** (PR #55 did it so the extraction tests run locally).
- **Run the PostgreSQL half locally — it is two minutes and it finds real
  bugs.** Postgres.app ships the binaries. The socket directory must be a
  *short* path (the 103-byte limit bites, and a scratchpad path exceeds it;
  `createdb` then fails while `pg_ctl` reports success):
  ```bash
  PGBIN=/Applications/Postgres.app/Contents/Versions/16/bin
  mkdir -p /tmp/bmlpg/run
  $PGBIN/initdb -D /tmp/bmlpg/data -U postgres --auth=trust
  $PGBIN/pg_ctl -D /tmp/bmlpg/data \
      -o "-k /tmp/bmlpg/run -p 55432 -c listen_addresses=''" -l /tmp/bmlpg/pg.log start
  $PGBIN/createdb -h /tmp/bmlpg/run -p 55432 -U postgres bmlib_test
  export BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/bmlpg/run port=55432 dbname=bmlib_test user=postgres"
  ```
- **Documentation was rewritten for 0.4.0 and has been kept current since.**
  Treat drift as a regression worth fixing, not expected staleness. The
  `(unreleased)` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release time; **none is outstanding** — 0.8.1 promoted the last five.
  Markers inside `docs/superpowers/plans/` are historical records — leave them
  alone.

## Next up

### Open GitHub issues

Four, all found by review rather than by a failing test.

**#72 — a bmlib bug hides behind any tier that still works.** #67's summary is
consulted only on *total* exhaustion, so an `AttributeError` from every PMC
tier with Unpaywall healthy silently degrades a whole corpus from JATS full
text to bare links. Wants the same level decision as #68, so do them together.

**#68 — a failed PDF download is invisible at default log level**, split out
of #67 rather than folded into its fix. `_download_and_cache_pdf`'s download
half is the one of #67's nine swallowers that was left alone, and it *cannot*
usefully feed the exhaustion counter: all three of its call sites return after
it, so a failure there never reaches the report. It is a milder bug than #67
— `pdf_url` set with no `file_path` is a real signal, where #67's result was
byte-identical to a paywalled paper — but with `convert_pdfs=True` the caller
asked for text and got none, and a full disk looks exactly like 10,000
publishers 404ing. **The issue is a decision, not a patch**: these URLs come
from Europe PMC's `fullTextUrlList` and Unpaywall, and a `Free` PDF URL that
404s is common enough that per-article WARNINGs could drown a bulk run. Sample
how often that happens before choosing a level — this repo settles list-shaped
questions by measuring.

**#73 — `install_defaults()` copies templates non-atomically**, found while
fixing #70 and deliberately not folded into it. A copy interrupted partway
leaves a truncated template that `if not dest.exists()` then skips forever, so
a prompt missing its last half renders and is sent to a model. Milder than #70
— one-time setup from a local file, degrading a prompt rather than fabricating
an article — and it carries a decision: `_atomic_write` is private to
`fulltext/cache.py`, so fixing this means either promoting it to a shared
internal module or accepting a second copy. Worth deciding once.

**#56 — `_extract_title()` trusts junk PDF metadata titles** ("Microsoft
Word - manuscript.docx" wins over the large-font first-page line), deferred
from PR #55's review. Note what it will cost to do *properly*: this repo
settles list-shaped questions by measuring a corpus, not by taste (the
industry-funder stems, the Markdown escape set, the DataBankName allow-list
are all precedents), so a reject-list for junk titles wants a sample of real
PDF metadata behind it rather than a guessed set of prefixes and suffixes.
The issue already asks for a regression test per rejected shape plus a
negative control. Every closed design stays in `docs/superpowers/specs/` as
the record of what was rejected and why.

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** so the mother project can consume
  0.6.0 through 0.8.1. Read the three intervening releases' non-comparable
  behaviour changes first — the transparency ones move stored scores, and
  0.8.0 moves every PubMed title and abstract. 0.8.1 adds nothing to that
  list.
- **Wire the segmenter and the rule-based extractors in.** Two halves of the
  same roadmap item: the segmenter could give `CochraneAssessor`
  Methods/Results boundaries and `TransparencyAnalyzer` the paper's own
  Funding/COI/Data-availability sections; `quality/extractors.py` is still
  called by no tier. Each needs its own design conversation.
- **Feed the stored grants to `transparency/`.** `TransparencyAnalyzer` runs
  its own `efetch` per paper to read `<GrantList>`, which `fetch_pubmed` has
  now already stored at sync time. Reading the table instead would save that
  request — but it is a scoring change that moves stored values, so it needs
  its own decision, not a quiet optimisation.

### bmlibrarian → bmlib porting (Phase 3 is next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

- **Phases 0, 1 and 2 are all done and shipped** — Phase 0 in 0.4.0, Phase 1
  in 0.7.0, Phase 2 across 0.7.0 and 0.8.0 (rows 10, 9, 8, 4 and 11 of the
  analysis doc's master table; PRs #51, #54, #55, #58, #59). Note those are
  rows in that table, not GitHub issues. CHANGELOG has what each shipped.
- **Phase 3 is next**: discovery (#12), `pubmed_search` (#13), MeSH (#21),
  ClinicalTrials.gov (#14 — **check the caveat first**, the legacy bulk XML
  the parser targets was deprecated in the 2024 API v2 migration). These are
  larger subsystems than anything in Phase 2; expect each to need its own
  design conversation rather than a straight port. Phase 4 (the prompt-driven
  agent family, paper_weight, review building-blocks) follows, and each of
  those must be reconciled against the existing `quality/` and
  `transparency/` rather than forked — see the analysis doc's caveats.

### The port recipe (repeat it)

1. **TDD, always.** Write behaviour tests first (the upstream code is the
   spec), watch them fail (a `ModuleNotFoundError` is the correct red for a
   new module), then port and watch them pass. Bug in a test you wrote? Fix
   the test, not correct code.
2. **Modernise to bmlib style:** AGPL header on every file,
   `from __future__ import annotations`, lowercase builtin generics,
   `datetime.UTC`.
3. **Sever app coupling:** replace `get_db_manager()`/`bmlibrarian.config`
   with injected connections/params; guard optional deps with
   `try/except ImportError` raising `pip install bmlib[extra]`; route LLM
   calls through `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** the public names from the package `__init__.py` `__all__` —
   and if the new module needs an extra, resolve it through a PEP 562
   `__getattr__` rather than re-exporting it eagerly (issue #64: one eager
   re-export made ten modules unimportable on a core install).
5. **Verify:** tests + both ruff commands clean before done.
6. **Record** each port in `CHANGELOG.md` under `[Unreleased]`.
7. **Reconcile, don't fork:** where a port overlaps existing bmlib, build on
   the existing module.
8. **Read the spec on both sides; do not decide by eye.** Row 11's reviews
   found this three times. Reading someone's XML, check their DTD:
   `<Affiliation>` looks like a leaf, is declared `(%text;)*`, and a bare
   `.text` read silently dropped rows. Declaring an output format, you owe
   that format's rules for *every* value, not only the ones carrying markup:
   having decided titles were Markdown, the fetcher neither escaped the
   prose it wrapped nor checked that `<u>` had a Markdown spelling that was
   not already `<b>`'s.

## Deliberate non-fixes — do not "fix" these

**Moved to [`docs/DECISIONS.md`](docs/DECISIONS.md). Read it before
"correcting" anything that looks wrong in `db/`, `llm/`, `agents/`,
`context_processor/`, `citations/`, `quality/`, `transparency/`,
`publications/` or `fulltext/`.** Each entry there was investigated and closed
as correct, so reopening one wastes a session; the file also records where
each argument lives in full (CLAUDE.md, `docs/manual/`,
`docs/superpowers/specs/`). Add new entries there, not here — this file is for
what still needs doing.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API
  connection as first argument, type hints and docstrings on everything
  public, AGPL-3 header on every source file, dataclass models with
  `to_dict()`/`from_dict()` where they persist, explicit SQL (no ORM),
  optional dependencies guarded with a helpful `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`** — CI pins
  **0.15.20** (`.github/workflows/ci.yml`), while `.venv` holds an older
  one that false-flags rules newer ruff removed:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP;
  no external services. `BMLIB_TEST_POSTGRESQL_DSN` must point at a database
  the tests may drop every table in (recipe under "Current state").
- New functionality needs unit tests; see CLAUDE.md's test-file mapping
  table.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
- **Cutting a release** (0.4.0 through 0.8.1 were all cut this way): bump
  the version in the **four** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header —
  promote the CHANGELOG's `[Unreleased]` body under a dated `## [X.Y.Z]`
  heading (leaving `## [Unreleased]` above it) with a short prose summary
  under it, promote any `(unreleased)` markers in `docs/manual/` and
  `ROADMAP.md`, then commit on a `release/X.Y.Z` branch and open a PR. After
  CI is green, the publishing half is the numbered list under "Finishing
  0.8.1" at the top of this file — merge with `--merge`, tag the *merge
  commit*, create the release, approve the gate, verify the simple index.
  Rehearse any time with a `workflow_dispatch` run, which targets TestPyPI
  only.
