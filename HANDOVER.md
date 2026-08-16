# HANDOVER — bmlib development

_Last updated: 2026-08-16. **0.10.0 is released and on PyPI** — PR #101
merged, `v0.10.0` tags `main`'s tip (`44e803d`), the Release workflow's
publish job succeeded, and both artefacts are on
`https://pypi.org/simple/bmlib/`. Nothing about the release is outstanding;
all five version places agree and no `unreleased` markers remain outside
`CHANGELOG.md`'s standing `## [Unreleased]` heading.

**The whole day-durability family shipped in it**, closed across five PRs —
#78 and #81 (PRs #85, #87), #88–#91 (PR #93), #95 (PR #97) and #98/#99 (PR
#100). `sync()` writes `status='completed'` to `download_days` and
`_days_needing_fetch()` does not offer that day again, so anything reporting
success it did not have loses the day's records permanently rather than losing
a request; seven issues were seven ways of doing exactly that. #95 is the one
worth remembering — it needed no API malfunction at all, fired on every
ordinary run, and cost a 09:00 cron the following 15 hours of indexing.
`docs/DECISIONS.md` has the rules and their costs; `CLAUDE.md`'s "A completed
day is a durable claim" is the one place to read before touching a fetcher's
page loop or `sync()`'s status handling.

**Tell downstreams what the version number cannot**: 0.10.0 is a minor bump on
the API axis alone, and nothing stored moves — but no `download_days` row a
previous release wrote is durable under #95's rule, so the **whole window is
re-fetched once on the first run after upgrading** (29 of 29 days measured for
a 30-day window, per source). Idempotent and self-correcting, but long for a
wide window across several sources and capable of meeting a rate limiter.

**Next up is either an open issue (five, all small and none blocking) or
Phase 3 of the bmlibrarian port, whose every row needs a design conversation
before any porting** — see "Next up".

**Four review rounds in a row found the fix carrying the same shape of bug as
the bug.** Worth carrying forward as method, not as history:

- **When a guard is opt-in, audit every call site, not the module.** A rule
  extracted into a shared module is not thereby applied — `stalled` defaults
  to the value that *disables* it, and OpenAlex simply never passed it.
- **A rule that selects over a *range* needs a test whose range has more than
  one answer in it.** Judging every day against `date_from` instead of
  `current` survived the entire suite, because all eleven of that rule's tests
  used a one-day window — and it silently reintroduced #95 for any cron after
  12:00 UTC.
- **A doc claiming one test is the sole pin for something must be checked
  against its neighbours.** The first replacement multi-day test landed on the
  boundary instant and quietly made that claim false a second time, caught
  only by re-running the mutation.
- **A guard that is loud about what it cannot parse can still be silent about
  what cannot be true.** A `downloaded_at` in the future read as durable.
- **A guard that checks values is blind to a wrong type that the type checker
  accepts.** `datetime` subclasses `date`, so the likeliest caller slip in
  the whole family passed both mypy and every value check, and on both ends
  of the window failed *silently*.

**#86, #92, #94 and #96 are open** — four, none of them in this family's
critical path. #92 is the measurement the #88 fix deliberately deferred; #94
is the bioRxiv envelope sampler the second round deferred for the same reason
(its guard is deliberately weaker than it looks — read `docs/DECISIONS.md`
before "simplifying" it); #96 is efetch's retstart skew. **#73 is closed on
this branch** — `install_defaults()` now writes through the promoted
`bmlib/_atomic.py`.

On `release/0.10.0`: **2172 tests + 59 skipped** on SQLite alone, and **2229 +
2 with a PostgreSQL DSN** (PostgreSQL 16, local), so the dual-backend half of
`test_backends.py` actually ran. ruff 0.15.20 and `uv run mypy` both clean.
`release.yml`'s own gates were rehearsed locally before the PR was opened —
`uv build`, `twine check --strict` on both artefacts, `bmlib/py.typed` present
in the wheel, and the wheel installed into a venv holding only jinja2 and
probed **one fresh interpreter per module: 71 importable, 0 not**. Rehearsing
is the whole point: those gates run in CI only *after* the release is public
and the version is burned.

**The mutation lesson from that review is the one to carry forward.** The
first round reported 7 of 7 caught and the count was honest, but the set was
chosen from the same mental model as the code, so it contained no
boundary-shift and no call-relocation mutant. Consequently the `recheck_days`
bound was pinned only as "somewhere below a billion" — every value between
the real bound (~739,842) and `10**9` was indistinguishable, including one
that accepts a value which really does overflow — nothing pinned `date.max -
1 day` as *accepted*, and moving `_validate_window` below the `httpx.Client`
build passed the entire suite, though the client is built *outside* the `try`
whose `finally` closes it. **A mutation set written by the author of a guard
tends to test that the guard exists, not that it is correctly bounded or
correctly placed.** Mutate the boundary by one in both directions, and move
the call.

**One documentation drift was found cutting this release and is fixed here:
the version lives in *five* places, not four.** `docs/manual/index.md`'s
header line carries it too, and because no list named it, it sat at 0.4.0
through five releases until 0.9.1 caught it by accident. `release.yml` checks
only `bmlib.__version__` against the tag, so nothing but the list guards the
other four. **After the release, Phase 3 of the bmlibrarian port is next, and
each of its rows needs a design conversation before any porting** — see "Next
up"._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Current state

- **Version 0.10.0, released 2026-08-15 and live on PyPI.** Release history:
  0.4.0 (2026-07-19) → 0.5.0 → 0.5.1 →
  0.6.0 (2026-07-30) → 0.7.0 (2026-08-04) → 0.8.0 (2026-08-08) → 0.9.0
  (2026-08-10) → 0.9.1 (2026-08-13) → 0.10.0 (2026-08-15). 0.3.0 was bumped
  in-tree but never released; its changes shipped inside 0.4.0. The version
  lives in **five** places — `pyproject.toml`, `bmlib/__init__.py`, the README
  version line, `CLAUDE.md`'s header, and `docs/manual/index.md`'s header line
  — and all five agree. The fifth was missing from this list until 0.10.0 and
  had gone stale at 0.4.0 for five releases; only `bmlib/__init__.py` is
  guarded by anything but this list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag,
  and they compound for anyone upgrading across them; 0.8.0's largest is the
  PubMed one, which changes the shape of every synced title and abstract.
  **0.9.0 moves nothing stored.** **0.9.1 moves one thing**: #79 makes Tier 1d
  take the free PDFs it had been discarding, so many more articles come back
  with `pdf_url` / `file_path` / extracted text instead of a bare link and a
  corpus's stored full text is not comparable across the upgrade — outbound
  traffic to Europe PMC rises with it. **0.10.0 moves nothing stored but is
  not free to upgrade to**: no `download_days` row a previous release wrote is
  durable under #95's rule, so the whole window is re-fetched once on the
  first run. Note the two questions are independent: 0.9.1 is a *patch* bump
  that does move something stored, while 0.9.0 and 0.10.0 are *minor* bumps
  that move nothing (each carrying three public-API changes). The version
  number answers the API question, never the data one, so a downstream reading
  only the number must still read this list.
- **`~/src/bmlibrarian` still pins `bmlib[ollama]>=0.5.1,<0.6.0`**, so it has
  now missed six releases. Widening it is a downstream change, not a bmlib
  one.
- **Tests: 2172 passing + 59 skipped** (`uv run pytest tests/ -q`);
  **2229 + 2 with `BMLIB_TEST_POSTGRESQL_DSN` set**. 57 of the default skips
  are the PostgreSQL parameterisations of `tests/test_backends.py`; 1 is a
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
  release time. 0.10.0 promoted all thirteen it inherited — five `ROADMAP.md`
  rows and eight spots in `docs/manual/publications.md` — and **none are
  outstanding now**. Grep case-insensitively for `unreleased` rather than for
  `(unreleased)`: three of those thirteen were spelled `*(unreleased, #99)*`
  and `(changed, unreleased — …)`, which the parenthesised pattern misses.
  Write the marker bare, never with a guessed version number: the number is
  decided when the release is cut, and this family is a case in point — it
  moves no API and so reads as a patch, while costing every installation a
  re-fetch of its whole window. Markers inside `docs/superpowers/plans/` are
  historical records — leave them alone.
- **`main` is protected by the `protect_main` ruleset** (added 2026-08-13):
  no deletion, no non-fast-forward push, and CodeQL code scanning plus code
  quality required to merge. CodeQL comes from GitHub's *default setup*, so
  there is no workflow file in the repo — and its generated workflow ignores a
  pull request's `reopened` action, so a PR that predates the setup needs a
  fresh commit rather than a close/reopen before its first analysis exists.
  The ruleset does **not** constrain the merge strategy, and nothing does —
  see the next bullet.
- **The release tag does not depend on the merge button** (#78, closed
  2026-08-13). The old recipe required a release PR be merged with `--merge`,
  enforced by prose alone, and that was measurably not holding: **8 of the
  last 40 merged PRs landed as single-parent commits** (#60, #62, #63, #65,
  #66, #69, #74, #76), each collapsing a 3–7 commit branch. The fix #78
  proposed — disabling squash and rebase repo-wide — was rejected because
  that habit is deliberate and squash is wanted for ordinary feature PRs;
  GitHub cannot condition the merge method on the branch, so there was no
  setting that gives release PRs one rule and feature PRs another.
  **The requirement was removed instead of enforced.** `main`'s tip is on
  `main`'s first-parent line under *every* merge strategy, so the recipe now
  tags `main`'s tip after pulling, guarded by two checks (see "Cutting a
  release"). Squash away.

## Next up

### Open GitHub issues

Four, every one found by review rather than by a failing test. (**#56, #68,
#72 and #79** shipped in 0.9.1. **#78, #81, #88–#91, #95, #98 and #99**
shipped in 0.10.0 — PRs #85, #87, #93, #97, #100. **#73** is fixed and
awaiting merge on `fix/73-atomic-template-install`.)

**#94 — bioRxiv's envelope shapes are unmeasured**, filed for the reason #92
was: the second round's guard refuses a body carrying *neither* a
`collection` key *nor* messages, rather than the obvious
`isinstance(data.get("collection"), list)`. bioRxiv is known to report a
quiet day by omitting `total`; whether it also omits `collection` is not
known, and requiring a key a quiet day may not send would fail that day on
every later run for the life of the installation. One case therefore stays
indistinguishable from a quiet day — an error body carrying messages and no
collection. The sampler measures both that and the `messages[0].status`
vocabulary. **Do not tighten the guard without running it**, and note that
the tests deliberately pin *both* possible quiet-day shapes so the guard
cannot come to depend on the unmeasured answer.

**#96 — efetch paging advances by the page size requested, not delivered.**
Found by reading, not reproduced: a short non-empty page would leave the
records between what arrived and the next `retstart` never requested, and
uniform half-pages land on exactly the exclusive floor and complete. The
first task is establishing whether efetch can do that at all; if it cannot,
the outcome is a line in `docs/DECISIONS.md`, not code.

**#92 — the shortfall floor is unmeasured**, filed as part of the #88 fix
rather than after it, so that the one guessed constant in that change is on
the record. `SHORTFALL_FAILURE_RATIO = 0.5` decides when a page walk that
ended naturally but came up short is treated as truncated; it asserts only
that no benign cause plausibly removes half a day's records, which is what
can be argued without data. Every other threshold in bmlib was set from a
sampled population, so this is the outlier. Two constraints for whoever takes
it: a `failed` day is re-offered on **every** later run, so a floor tightened
past the real benign gap re-fetches that day forever; and OpenAlex is the
expensive source to sample, at tens of thousands of works per publication
date. Follow the `scripts/` sampler convention — offline test file, and a
probe that could not be made never printing as a finding.

**#86 — `docs/manual/llm.md` documents `LLMClient.generate` and
`LLMClient.embed` twice each**, found while updating signatures for #81 and
deliberately not folded into it. The same defect as #31 (`fulltext.md`'s
doubled `## PDF Conversion`), and the same reason it is worth a session
rather than a delete: the copies differ, so merging them is a judgement
about which prose and which examples survive — one `generate` has the
example, and the two `embed` sections disagree about whether the default is
the provider's *chat* default model (the `embed_batch` section has it
right). #81 updated **both** copies of `generate` so the duplication did not
silently become a drift.

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** so the mother project can consume
  0.6.0 through 0.9.1. Read the intervening releases' non-comparable
  behaviour changes first — the transparency ones move stored scores, 0.8.0
  moves every PubMed title and abstract, and **0.9.1 moves stored full text**
  (#79: Tier 1d now downloads the free PDFs it used to skip). 0.9.0 adds
  nothing to that list, but it carries three API changes, so the widened pin
  should clear `FullTextService.cache` being nullable.
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
- **`uv run mypy` is a gate now too** (#81), pinned to **2.3.0** in the `dev`
  extra with its settings in `pyproject.toml`. Take no arguments and add
  none — the bare command is what the `types` CI job runs. It must run in
  the dev venv: every extra but psycopg2 ships its own `py.typed` (that one
  is covered by `types-psycopg2`), so against a bare interpreter mypy
  reports the optional imports *and `jinja2`, a core dependency*, as missing
  stubs. That is not hypothetical — it is why #81 opened claiming 24 errors
  when there were 22. Anything deliberately
  unchecked is an inline `# type: ignore[code]` with its reason at the site,
  never a per-module `ignore_missing_imports`: `warn_unused_ignores` reports
  the first when it goes stale and can never report the second.
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP;
  no external services. `BMLIB_TEST_POSTGRESQL_DSN` must point at a database
  the tests may drop every table in (recipe under "Current state").
- New functionality needs unit tests; see CLAUDE.md's test-file mapping
  table.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
- **Cutting a release** (0.4.0 through 0.10.0 were all cut this way): bump
  the version in the **five** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header,
  `docs/manual/index.md`'s header line — promote the CHANGELOG's
  `[Unreleased]` body under a dated `## [X.Y.Z]` heading (leaving
  `## [Unreleased]` above it) with a short prose summary under it, promote any
  `unreleased` markers in `docs/manual/` and `ROADMAP.md`, add the release's
  own `ROADMAP.md` row, then commit on a `release/X.Y.Z` branch and open a PR.
  **The number is a claim about the API, not about the data**: 0.9.0 was cut
  as 0.8.1 and renumbered in review, because three of its fixes changed a
  public API while nothing stored moved, and bmlib's downstream pins are
  written on the convention that a minor bump is the one that may break.
  0.9.1 is the other side of that same convention: #79 moves stored full text
  while nothing breaks an API, so it is a patch. 0.10.0 is a third shape and
  the reason to state the data answer in prose every time — a minor bump on
  the API axis whose real cost to a downstream is a one-off re-fetch of the
  whole sync window, which no version number can express.
  After CI **and CodeQL** are green — the `protect_main` ruleset requires the
  scan, and a release PR is subject to it like any other — merge it with any
  button, then **tag `main`'s tip rather than a particular commit** (#78):

  ```bash
  git checkout main && git pull --ff-only
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || exit 1
  grep -q '__version__ = "X.Y.Z"' bmlib/__init__.py || exit 1
  git tag -a vX.Y.Z -m "bmlib X.Y.Z" && git push origin vX.Y.Z
  ```

  The two checks are the whole point: the first catches a stale local `main`
  (someone else's merge landing between yours and your pull), the second
  catches tagging a commit that does not carry the version — which is the
  failure `release.yml` would otherwise find *after* the release is public.
  Checking the tag *afterwards* needs one piece of git arcana: these are
  **annotated** tags, so `git rev-parse vX.Y.Z` returns the tag object's SHA
  and not the commit's, and a comparison against a commit SHA fails for a
  reason that has nothing to do with the release. Dereference it —
  `git rev-parse 'vX.Y.Z^{commit}'`. Verified on v0.9.1: dereferenced, it is
  `main`'s tip and is on `main`'s first-parent line; undereferenced, the same
  check reports a mismatch.
  A merge commit is still the nicer shape for a release, since it keeps the
  branch's commits on `main`, but it is now a preference and not a
  correctness requirement; then create the GitHub release, which is
  **what publishes** — `.github/workflows/release.yml` rebuilds, refuses to go
  on unless the tag matches `bmlib.__version__`, runs `twine check --strict`,
  asserts `py.typed` survived packaging, and uploads via Trusted Publishing.
  **Hand the `pypi` environment gate over rather than approving it**, even
  when `gh api .../pending_deployments` says `current_user_can_approve: true`:
  a PyPI upload is irreversible and the version can never be reused. Nothing
  is lost by waiting — the run stays approvable indefinitely. Afterwards
  verify against `https://pypi.org/simple/bmlib/`, not the JSON API, which
  serves a stale CDN cache; a failed `pip install` immediately after is
  propagation, not a bad upload. Rehearse the whole path any time with a
  `workflow_dispatch` run, which targets TestPyPI only.
- **Rehearse the release gates locally before opening the PR** — `uv build`,
  `twine check --strict` on both artifacts, and a clean-venv install asserting
  `py.typed` survived packaging. `release.yml` runs them only *after* the
  version is burned and the release is public, so a failure there is expensive
  and a failure locally is free. On any release touching an `__init__.py`,
  probe the built wheel **one fresh interpreter per module** as well: a single
  process leaves the half-initialised parent in `sys.modules` and its siblings
  then falsely read as importable, which is how #64 was first mis-scoped.
  0.9.0 was checked that way twice — 69 importable / 0 not, locally and again
  against the published wheel.
- **Do not upload by hand.** The publish job has no `skip-existing`, so a
  manual upload makes it fail on a duplicate — which is why v0.5.0's and
  v0.6.0's runs still sit unapproved. v0.7.0, v0.8.0 and v0.9.0 all went the
  whole way through the workflow.
