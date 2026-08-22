# HANDOVER — bmlib development

_Last updated: 2026-08-22. **0.10.0 is released and on PyPI**; seven changes
sit unreleased on `main` — #73's atomic template install (PR #102), #96/#105's
partitioning of an over-cap PubMed day (PRs #106 and #114), #109's typed
article-id (PR #113), #110/#111's JATS sub-article and contributor-group
fixes (PR #118), #115/#116/#117/#131's exhibit nesting, ranking and
sampler (PR #126) and #127's image-only table (PR #133) — plus **this
session's #123/#125/#130 and #135, on
`fix/123-125-130-title-caption-owner`**. All five version places agree at
0.10.0.
Five of the seven unreleased changes are `fulltext` JATS fixes filed within
days of each other; whoever cuts the next release should describe them
together. Every unreleased ROADMAP row now carries an `*(unreleased)*`
marker; eight did not, and #109 had no row at all.

**Three of them move what a caller of `JATSParser` gets**, and none moves
what a bmlib *sync* stores. #111 populates an author list that was empty for
the majority of open-access articles. #115/#117 change `JATSArticle.figures`
and `.tables` — figures that were missing now appear, and a figure's
`graphic_url` changes from a thumbnail to the full image for roughly half of
all figures. #127 adds `JATSTableInfo.graphic_url` and fills
it, so a table that came back with no content now carries its image. This
session's #123/#125/#130 move a fourth: a section's `title` and an exhibit's
`caption` are routed by their owning element, so a section renamed by a
footnote group or a boxed text keeps its own heading and a figure caption
truncated by a nested one comes back whole. No bmlib
path carries `figures`, `tables` or `authors` anywhere: `service.py` never
reads them, `FullTextResult` has none, and `publications` takes its authors
from the fetchers. Only a downstream that calls `JATSParser` itself and
persists the result needs to re-parse — but such a downstream should, because
the stored values are not comparable across the upgrade.

**This session fixed #123, #125 and #130 and closed #135**, and the useful
half of it was the measuring, not the routing. The fix itself is in
`CHANGELOG.md`; four things are worth carrying forward.

- **A rule's population can be large, empty, or both, and only a draw says
  which.** The `<title>` half is **10.3% of recent articles** (69 titles in 31
  of 300). The `<caption>` half measures **empty in both windows and in the
  seven-article Swift corpus** — no `<caption>` nests inside another, none
  inside an exhibit is owned by anything else. Same PR, same rule, opposite
  evidence. Ship both; say which is which.
- **A draft comment asserted publisher behaviour nobody had measured** — that
  eLife deposits a captioned `<supplementary-material>` inside its figures. It
  deposits supplements as nested `<fig>`. That is #135's mistake reappearing
  within days of #135 being filed for it, which is the argument for measuring
  *before* writing the comment rather than after.
- **The instrument was wrong in the direction that flatters the rule.** The
  sampler counted a table's `<graphic>` deposits by whole-subtree walk while
  the parser routes by owner, so four of ten recent tables read as
  multi-deposit; every one was a `<td>` cell image. Unnoticed, #135 would have
  closed on a number about cell decoration. **Check that a counter measures
  the population the code acts on**, not a superset of it — the residual #135
  itself had named, which is why it was findable.
- **Two enumerations, two containers nobody listed.** #125 names `<fn-group>`,
  #130 names `<boxed-text>`; the back-filled window's entire population is a
  **`<list>`**. Neither issue would have produced an allow-list that covered
  it. That is the parent test's whole argument, and the third time it has been
  made here (#116's `<label>`, #117's `<graphic>` owner, now this).

**Three notes from the previous session (#127, PR #133) still hold.**
*An issue can be closed as COMPLETED without being fixed* — #127 was, in PR
#126's merge batch, with no commit carrying a closing keyword; when an issue
is closed alongside a PR, check the PR actually closes it, because no count of
open issues catches the ones wrongly shut. *Mutation testing found the one new
test that could not fail*, because each foreign deposit was written **second**,
where plain first-wins already answers it — write the case the rule actually
decides, not the one that merely exercises the code path. And *a stratified
sample of recent deposits is still one window*: #127's population reads 0 of
662 there and 11 of 93 in a 1996-1998 draw, so where a rule is about how
publishers **deposit**, ask whether the draw can see those deposits before
reading a zero as an answer. This session met that same wall twice, in the
opposite direction — see the bullets above.

**Next up: #124, #128, #129 and #134 in `fulltext`, the three from the #118
review (#119, #120, #121), #132, the older non-JATS ones (#86, #92, #94,
#103, #112), or Phase 3 of the bmlibrarian port, whose every row needs a
design conversation.** #124 is the last one that still loses content, and
#132 is now smaller than it was — see "Next up".

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
  first run. The two questions are independent — 0.9.1 is a *patch* bump that
  moves something stored, 0.9.0 and 0.10.0 are *minor* bumps that move nothing
  — so the version number answers the API question, never the data one, and a
  downstream reading only the number must still read this list.
- **Tests: 2541 passing + 63 skipped on `main`**, **2569 + 63** on
  `fix/123-125-130-title-caption-owner`, so 28 are this session's — 17 in
  `test_jats_parser.py` and 11 in `test_jats_exhibit_sampler.py`
  (`uv run pytest tests/ -q`). Both measured, not derived. The PostgreSQL half
  was **not** re-run for this branch and does not need to be: it touches no
  SQL, and the last measured figure with `BMLIB_TEST_POSTGRESQL_DSN` set is
  2435 + 2 on the #105 branch. Of the 63 default skips, 61 are the PostgreSQL
  parameterisations, 1 is a PostgreSQL-only schema test, and 1 is
  `test_pymupdf_requires_dependency`, which runs only when PyMuPDF is
  *absent*. **PyMuPDF is installed in the dev venv** (PR #55 did it so the
  extraction tests run locally).
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
  PR's `reopened` action, so a PR predating the setup needs a fresh commit
  rather than a close/reopen. It does **not** constrain the merge strategy.
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

**Seventeen open** as this session starts, thirteen once this PR closes #123,
#125, #130 and #135. Every one was found by review or
measurement rather than by a failing test, and **none of them loses records**
— though **#120** loses a contributor, **#123** and **#124** lose an exhibit's
caption tail and its footnotes, **#128** would lose every figure image in a
document binding XLink to another prefix, **#129** loses a whole article to
one malformed `colspan`, and **#119** feeds a scan text that is not the
article's. Count this against the repo before trusting it: this line has been
wrong in three consecutive sessions, and this session found a further way for
it to be wrong — an issue **closed as COMPLETED without being fixed**, which
no count of open issues can catch. (**#56, #68, #72 and #79** shipped in
0.9.1. **#78, #81, #88–#91, #95, #98 and #99** shipped in 0.10.0 — PRs #85,
#87, #93, #97, #100. **#73** is on `main` unreleased in PR #102, whose own
review filed **#103**. **#96** closed with PR #106, as correct rather than as
fixed. **#105 and #107** closed with PR #114. **#109** closed with PR #113.
**#110 and #111** closed with PR #118, whose review filed **#119**, **#120**
and **#121**. **#115, #116, #117 and #131** closed with PR #126, which filed
**#123**, **#124**, **#127**, **#128**, **#129** and **#130**. **#127**
closed with PR #133, which filed **#132**, **#134** and **#135**; **#123**,
**#125**, **#130** and **#135** close with this session's PR.)

**#128 is weaker than filed**: every one of the 2,397 `<graphic>` hrefs in the
two redrawn corpora uses the `xlink` prefix bound to the XLink namespace, so
the literal-prefix match is safe on measured evidence. Worth downgrading
rather than closing — no sample proves no publisher does otherwise.

**#134 — the parser has no end-of-parse audit**, so a stack left open loses
content in silence. Filed from the Swift port, where the same shape stranded
a footnote counter above zero and drained every remaining paragraph into it,
one at a time, unremarked — and survived to code review. Expat rejects an
unbalanced *document*, which is exactly why this is a net and not an input
check: it fires only when the *parser* is wrong, and #115, #123 and #130 are
all that class. Wants one pure function over the handler's counters and
stacks, called from `_run_parser()` (the one place `parse`, `to_html` and
`parse_with_html` all funnel through), logging at ERROR. #121's
"parse produced zero authors" belongs in the same place.

**#132 is smaller than it was.** Both corpora were redrawn this session with
every counter present, so the figures a reader can re-derive from the repo now
match the comments that cite them — including #127's (0 of **662** recent,
11 of 93 back-filled, the same two articles), #128's XLink counts and the
`<label>` premise. What remains is the 276-article draw itself: #115's "0.7%,
both eLife" and #117's 49.9%/49.5% still cite a corpus that is not in the
repo, and **nesting measures 0 in both committed draws**, so that figure now
has no in-repo evidence at all. Do it before the release that ships these
rules, while the CHANGELOG body is still free to edit.

**#124 — table and figure footnote prose is dropped entirely.** Neither
exhibit model has a `footnotes` field and nothing collects one, so a
`<table-wrap-foot><fn>`'s text reaches nothing. `<sup>` is flattened into the
surrounding cell, so the rendered table still reads `12.3a` while the note it
points at exists nowhere. #116's fix discards the marker, which is right only
because there is nothing to attach it to; once there is, hold it and prefix it
(`"a — Adjusted for age."`), since with two footnotes the mapping is otherwise
unrecoverable. Needs the model decision first, so not a drive-by. The prose
carries abbreviation expansions and per-table funding notes.

**#119 — `TransparencyAnalyzer` scans raw JATS XML, so `<sub-article>`
reviewer prose still reaches its COI, funding and data-availability scans.**
`_fetch_europepmc_fulltext` returns `resp.text` and `_extract_coi_text`
regexes that string; there is no import path from `bmlib.transparency` to
`bmlib.fulltext`, so #110's suppression does not touch it. Measured on two
articles during the PR #118 review: 6 and 5 reviewer `competing interest`
statements respectively, inside `<sub-article>` regions the scan reads. It
fails *towards* leniency — a reviewer's "no competing interests" counts as
the article's own disclosure. The fix needs measuring, since the scoring is
calibrated against current behaviour.

**#120 — `<collab>` consortium authors are silently dropped.**
`_AuthorBuilder.build()` returns `None` without a `<surname>` and the call
site has no `else`, so *"the INHERIT Trial Group"* never reaches
`JATSArticle.authors`. Pre-existing rather than a regression, but #111's fix
makes it the last thing standing between a correctly-identified contributor
and the list. Measured on 1,025 open-access articles: 138 newly-admitted
contribs carry no surname, and **34 articles (3.3%)** lose at least one — none
loses all of them. Needs a model decision first, since `JATSAuthorInfo` has
`surname`/`given_names` and a collaboration has neither.

**#121 — a zero-author parse is indistinguishable from an author-less
article.** `_build_html` has `if h.authors:` with no `else`, and
`FullTextService` caches the result, so a broken parse renders and persists
exactly like a correct one — the detector missing for the whole life of #111,
during which 108 of 183 sampled articles parsed to zero authors and nothing
said so. Belongs at #134's call site, which is the one place every entry
point can hear it.

**#112 — stated measurements that do not reproduce against the committed
corpus**, the same failure as #132 above but one layer deeper.
`bmlib/transparency/analyzer.py` states a measurement as the
reason for each stem it includes and excludes, and three disagree with
`tests/data/funder_names.json`: the headline pair reads 0.917 / 0.324 and
measures 0.909 / 0.333, `pharmaceutic` is claimed to have no false positive
and has the matcher's *only* one — the thing capping precision below 1.000 —
and `co` is excluded for a collision the corpus does not contain, at the cost
of a real true positive. Not drift: the corpus has one commit (be456a2) and
the matcher is byte-identical since, and the stated figures are
self-consistent with a corpus holding 34 industry entries where this one holds
30, so they were taken against a revision never committed. Nothing is a
behaviour regression — `tests/test_funder_matching.py` passes and both floors
hold. Re-derive every figure in one pass rather than fixing the three named,
and say in each comment which corpus revision it came from.

**#103 — `install_defaults()` reserves no `NAME_MAX` headroom for the
temporary name.** `atomic_write()` stages through a name 38 characters longer
than the target's; `fulltext/cache.py` leaves room (`_MAX_PREFIX_CHARS`),
`templates/engine.py` passes the source filename through verbatim, so a
template named beyond ~217 characters now fails with `ENAMETOOLONG` where the
old `write_text` succeeded. Left alone deliberately: those names come from the
caller's own source tree rather than unbounded input, and the failure is loud
and immediate rather than silent and permanent. The fix is a docstring line,
not a cap — capping renames a caller's template and `render("<name>")` then
does not find it.

**#94 — bioRxiv's envelope shapes are unmeasured**, filed for the reason #92
was: its guard rests on an unmeasured quantity. Full argument in CLAUDE.md
under "A completed day is a durable claim" — one error body (messages, no
`collection`) stays indistinguishable from a quiet day, and the sampler this
issue asks for would measure that and the `messages[0].status` vocabulary.
**Do not tighten the guard without running it**; the tests pin *both* possible
quiet-day shapes so it cannot come to depend on the unmeasured answer.

**#92 — the shortfall floor is unmeasured**, filed as part of the #88 fix
rather than after it, so that the one guessed constant in that change is on
the record. `SHORTFALL_FAILURE_RATIO = 0.5` decides when a page walk that
ended naturally but came up short is treated as truncated; it asserts only
that no benign cause plausibly removes half a day's records, which is what
can be argued without data. Every other threshold in bmlib was set from a
sampled population, so this is the outlier. Two constraints for whoever takes it: a
`failed` day is re-offered on **every** later run, so a floor tightened past
the real benign gap re-fetches that day forever; and OpenAlex is expensive to
sample, at tens of thousands of works per publication date. Follow the
`scripts/` sampler convention. One input from #96: efetch cannot produce the
uniform-half-pages case this floor was worried about — a page is the slice it
named or it is refused.

**#86 — `docs/manual/llm.md` documents `LLMClient.generate` and
`LLMClient.embed` twice each**, found while updating signatures for #81. Same
defect as #31 (`fulltext.md`'s doubled `## PDF Conversion`), and worth a
session rather than a delete for the same reason: the copies differ, so
merging them is a judgement about which prose survives — one `generate` has
the example, and the two `embed` sections disagree about whether the default
is the provider's *chat* default model (`embed_batch`'s has it right).

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** — `~/src/bmlibrarian` still pins
  `bmlib[ollama]>=0.5.1,<0.6.0` and has missed six releases; widening it is a
  downstream change, not a bmlib one. Read the intervening releases'
  non-comparable
  behaviour changes first — the transparency ones move stored scores, 0.8.0
  moves every PubMed title and abstract, and **0.9.1 moves stored full text**
  (#79: Tier 1d now downloads the free PDFs it used to skip). 0.9.0 adds
  nothing to that list, but it carries three API changes, so the widened pin
  should clear `FullTextService.cache` being nullable.
- **Wire the segmenter and the rule-based extractors in.** Two halves of one
  roadmap item: the segmenter could give `CochraneAssessor` Methods/Results
  boundaries and `TransparencyAnalyzer` the paper's own Funding/COI/Data
  sections; `quality/extractors.py` is called by no tier. Each needs a design
  conversation.
- **Feed the stored grants to `transparency/`.** `TransparencyAnalyzer` runs
  its own `efetch` per paper to read `<GrantList>`, which `fetch_pubmed`
  already stores at sync time. Reading the table would save that request — but
  it is a scoring change that moves stored values, so it needs its own
  decision, not a quiet optimisation.

### bmlibrarian → bmlib porting (Phase 3 is next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

- **Phases 0, 1 and 2 are all done and shipped** — Phase 0 in 0.4.0, Phase 1
  in 0.7.0, Phase 2 across 0.7.0 and 0.8.0 (rows 10, 9, 8, 4 and 11 of that
  doc's master table — rows, not GitHub issues; PRs #51, #54, #55, #58, #59).
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
