# HANDOVER — bmlib development

_Last updated: 2026-09-13. **0.10.0 is released and on PyPI**; thirty-nine
changes sit unreleased, three of them instrument-only. All five version places
agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Thirty-nine changes, twenty-two of them `fulltext` JATS fixes filed within
days of each other — whoever cuts the next release should describe those
together. **Per-PR argument is in `CHANGELOG.md`; only the *data* answer is
kept here**, because the version number answers the API question and never
that one. Three (#211, #212, #216) touch `scripts/` alone and cost a downstream
nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored, so **a downstream holding cached full text should re-fetch**, not only
one calling `JATSParser` itself. Six of them ride on one re-fetch and are the
largest by population, all diffed against `main` over the 8,118 served
articles of `PMC10030002_PMC10040000.xml.gz`:

- **#224** — unsectioned `<back>` prose (`<ack>`, `<notes>`, `<fn-group>`,
  `<app>`, `<glossary>`, `<bio>`) used to be dropped. Prose moves in 5,990
  articles (73.8%), every move an insertion: 40,342 paragraphs, 0 lost.
  `has_body`, `figures`, `.tables`, `references` and `abstract_sections` move
  in **0**.
- **#124** — an exhibit's footnotes used to reach nothing; they now fill
  `JATSFigureInfo.footnotes` / `JATSTableInfo.footnotes`, marker folded in.
  Notes appear in **3,707 (45.7%)**, 16,935 of them.
- **#230/#234** — front-matter prose (`<author-notes>` COI and funding
  statements, `<front><notes>` data availability, `<trans-abstract>`) used to
  be dropped, and a front `<sec>` arrived titled and empty ahead of the body.
  It now lands **ahead of the body** in `body_sections`. Prose moves in
  **3,350 (41.3%)**, every move an insertion: 9,332 paragraphs; archive 46,737
  of 97,909 and 114,549. No other public field moves, but a paragraph the
  publisher deposits front *and* back (Springer's open-access funding line)
  now renders twice in 108 served and 2,984 archive articles.
- **#243** — a cell's text used to reach the buffer above it as well as the
  cell, so a `<table-wrap>` inside a `<p>` spliced the table's numbers into the
  sentence. A paragraph moves in **2,222 (27.4%)**: 6,356 stripped in place, 10
  dropped, 0 gained; `html_content` in exactly those. Archive: 21,377 of 97,909.
- **#228** — a `<def-list>`'s `<term>` is folded into its definition's
  paragraph. A paragraph moves in **840 (10.3%)**, 12,667 in place.
- **#241/#248** — an object's `<alt-text>`, `<long-desc>`, `<object-id>` and
  `<permissions>` no longer weld into prose, abstracts or cells, and `<attrib>`
  is routed (a quote's as a paragraph, an exhibit's into its `footnotes`).
  HTML moves in **584 (7.2%)**. Archive: 3,098 of 97,909, where 18 graphical
  abstracts whose only text was their figure's attribution **lose that
  abstract section** to the figure's notes.

Then, reasoned or measured on smaller draws: **#146/#149** (over 880 local
articles / 20,770 references, `citation` moves for 4,499 in 191 articles —
3,541 rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for
502 in 14, HTML for 576 in 23), **#111** (an author list empty for the
majority of open-access articles), **#115/#117** (`figures` and `.tables`;
roughly half of `graphic_url` moves from a thumbnail to the full image),
**#147** (prose and HTML for 68 of 880, and a LaTeX preamble out of every
table cell), **#162** (HTML for 83 of every 997 recent), **#123/#125/#130**
(`body_sections`, about one recent article in ten), **#127**, **#120/#140**,
**#129**. **#238 and #245 move nothing stored** — three log lines where there
was silence, #245's naming content an `<array>` deposit loses (355 cells in 8
of the 8,118 served articles), which #243 turns from a corrupt survival into a
clean one.

**Eleven move stored *transparency* values.** Two are large enough that **any
downstream holding stored transparency results should recompute them**:

- **#184** — every Europe PMC full-text fetch was 404ing, so every analysis
  ran on the abstract. Over 48 real open-access analyses diffed against
  `main`: `coi_disclosed` moved in 32, `transparency_score` in 20 (+10 or +20,
  never negative), `risk_level` HIGH→MEDIUM in 5. The missing-COI downgrade is
  now reachable at all.
- **#194** — ClinicalTrials.gov had been refusing bmlib's own `User-Agent`
  with a 403 since the endpoint was first called, so `SCORE_RESULTS_POSTED`
  (15) had never been awarded and *"Registered trial without posted results"*
  was stored about every registered trial. How many papers gain those 15
  points is unmeasured — a class of paper, not a rate.

The rest, briefly. **#203 has the widest population**: `risk_indicators`
moves for every analysis that did not scan full text, i.e. every
closed-access paper, and **a downstream string-matching *"COI disclosure
status unknown (full text unavailable)"* or its two siblings breaks** — read
`full_text_status` instead. **#161/#198** add fields (`full_text_status`,
`trial_results_status`) rather than moving values; a downstream pinned to an
older bmlib raises out of `from_dict` on a row this version writes. **#193**
moves `full_text_status` and adds a COI indicator; **#187/#190/#191** move
that field alone; **#195**'s tri-state swaps one CT.gov indicator for
another. **#112** flips `industry_funding_detected` for a `"… plc"` funder.
**#119** moves a scan output for 0.61% of 97,909 articles. **#160**, **#183**
move nothing measurable; **#202** nothing at all. **#188** stores
`NOT_ATTEMPTED` where it stored `NOT_SERVED` for a record whose only
identifier is neither a `PMC…` nor a `PPR…` accession — 43 of 123 in one
source-stratified draw, all `MED`, so the share over a caller's corpus
follows its source mix. **#206** moves `trial_results_status` `NOT_POSTED` →
`PARTLY_ANSWERED` and swaps the indicator string for a paper whose results
check was partial; no score moves. **#199** refuses a `true` or fractional
`cited_by_count`, 5 points lower for such a paper; two draws saw every served
body well-formed, so no coercer was observed to fire. **#218** moves nothing
stored.

## Rules carried forward

Each is argued in full in `CLAUDE.md`, `docs/DECISIONS.md` and at its call
site; only the shortest form is kept here, because these are what a session
gets wrong again rather than what it can look up.

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; one window is not the rate (#127 read 0 of 662 recent tables and
11 of 93 in a 1996-1998 draw). **An issue's own remedy is a hypothesis too**
— #162's cost ten minutes to refute, #183's was refuted by 1,750 articles
ending in a legal trailing comment. **Measure the population the code
actually reads**; prefer a corpus with a public name over one on your disk,
and check that its *rendition* is the one the code is fed (#138). **A live
Europe PMC draw must be stratified by source and publication year**, a cursor
page being a contiguous block of accessions. **Run one live probe at a time**
— the per-host pacer is per-process (#179). **A share is of a denominator,
and the rendition chooses the denominator** (#164). **Probe the contract, not
the expression the reporter noticed** (#199). **State a blast radius from a
diff, not from the call graph** — and the diff's own predicate is a claim to
check: prefix where the honest test was *subsequence* (#224), a `difflib`
opcode walk aligning arbitrarily over a list whose every member changed
(#243). **Load both checkouts in one process** where a corpus makes two dumps
expensive, after validating that comparator on the smaller artifact. **The
harness that produces a blast radius is itself an instrument**, and **a gap
between two of bmlib's own counts is a defect in one of them until it is
explained** — reconcile a routing tally against the diff **per article**, not
in total: #230's served gap of 4 was one article's four empty paragraphs, and
the total hid which. **A mirror over the markup and a tally from the routing
are different instruments, and where they disagree the routing is the
finding**; where the code has a predicate, run the code, and **measure a drop
at the drop** — #230's instrument used the parser's own predicates and checked
every classified run against a before/after fingerprint of every destination,
which is what made "0 mismatches" a result rather than an assumption.
**Separate what is already filed from what is lost**: 21,225 served `<p>` in
table cells fall past `_append_prose` too, and `characters()` has filed every
one. **Assert the number a log line prints, not that it printed.** **A
container you describe in prose is a claim too.** **A committed corpus is not
the only honest population**, but nothing in the suite re-derives the two named
artifacts — state the trade. **A survey can refuse part of a remedy**, not
only size it, **and it can find the issue beside the one you took**: #230's
tally surfaced #234's empty headings (already filed) and #253.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding. **A guard whose reason moves needs its comment moved with
it** — #230 made the object-metadata refusal load-bearing for 19 archive
licences that had been falling past every branch anyway. **An `Any`-returning
helper launders every annotation above it.** When a fix extends a routing rule,
walk every other path it reaches — the guard written on one branch is the
guard the others need. **An issue's suggested remedy can be narrower than the
rule it invokes** (#243's `characters()` hold). **A fix that removes a corrupt
survival can create a total loss elsewhere** — measure it and count it (#245).
**Before discarding an element, find where its text lands on `main`**, off the
real handler's `text_stack`. **Isolating a buffer answers the children that
merge, not those that route or write a builder directly** — name the routes by
kind and guard each; **an exception is not made until it reaches every route**
(#241/#248's `<xref>` exception lived at the buffer pop alone). **A set keyed on
the element cannot express a rule about the context.** **Suppressing a merge
does not empty a buffer.** **A counter's scope is a decision, and the deposit
survey is what makes it**; **say which guard makes which exclusion**. **An
empty deposit costs nothing.** **When a fix routes a container, find every test
that used that container as *the* example of the old behaviour** — #230 moved
three tests and a parametrised row onto `<floats-group>`, the shape that still
has the property, rather than flipping them into duplicates. **Ask once the
numbers are in** when scope is a modelling choice: the routing question for
#230 went to the maintainer with the owner table in front of them.

*Diagnostics and tests.* A diagnostic's *level* is a claim that has to be
measured, **and the branch it sits on must be no wider than the draw** (#191).
**A branch that is several populations has to be split before it can be
levelled** (#218). **When a fix takes a counter's only measured population,
the counter needs a test of its own or it goes vacuous the same day** (#224,
#230's `definition_terms_dropped`). **A status enum member is a stored claim.**
**Before arguing about a level, check the diagnostic exists** (#193). A net
needs its own false-positive net, and it must be free — the autouse
`parser_log` fixture makes every pre-existing fixture one. Key a counter on
*structure*, and **read the increment site, not the name or the report**.
**Asserting that a constant was imported is not asserting that it is used.**
**A rule enforced by prose is not enforced** (`TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`,
`TestEverySectionIsGatedOnEveryCounterItReads`). **Checking the arithmetic is
not checking the rule**, and check the denominator too. **A zero over an
absent population is not a clean result.** **Ask which line of the fixture the
assertion depends on.** **Mutate the *old* half of a condition you extend**,
and give a fixture prose *after* the close. **A guard's mutant can be inert for
the very fixture that names it.** **A surviving mutant is sometimes an unmade
decision.** **A membership is invisible wherever a sibling member is also in
the walk's path.** **Aim mutants at what a guard *reads*.** **An equivalent
mutant is not an untested guard** — but **it can be equivalent by
construction**: `in_front` in `_prose_reaches_output`'s section conjunction
changes no answer because the fallback answers it too, and saying so at the
site is the deliverable. **Run a control mutant on the neighbouring flag you
did not touch**: #230's `in_back` control survived, a pre-existing guard nothing
pinned. **A nested fixture is the only one that sets two container flags at
once**, so it is the only one that can pin an order. **A contract net is blind
to a value read *wrongly* without raising** — assert what the run
*concluded*. **Pick the fixture that separates the guard from its own mutant**;
**a malformed fixture reads as a measurement**. **A measured *majority* is an
argument against a diagnostic** (#235) and **a measured-empty population is an
argument for closing an issue, not for building it** (#204, #207, #210).
**Run a correctness review and a claims review before the PR.**

*Live behaviour.* **A remote's error shape is a property of the *request*, not
of the endpoint** (#218). **A property only a real remote can refute needs a
real probe** (#194). **A sampler must address *and head* requests exactly as
the code does.**

*Instruments.* **A list an instrument declares must be derived from the code
it measures, not restated.** **One declared list can hide two rules.** **Where
an instrument is wider than the code, say so at the site and bound the cost.**
**A guard on the page cannot see a loss one level down** (#212). **Do not
background a mutation sweep beside anything that reads the same checkout** —
commit first, restore from the held string *and* a disk backup, clear
`__pycache__` after each restore. **A `ProcessPoolExecutor` script needs its
`__main__` guard** on macOS, where workers spawn and re-import it.

*Cost.* **A test that pins a decision is reversed, not deleted, when the
decision is** (#206). **The cost of a schema addition is not a constant** —
ask what the batch already costs (#198). **Check before pricing**: #124's
issue priced a `to_dict`/`from_dict` pair neither exhibit model has.

*Process.* **A closing keyword next to an issue number closes it, quotation or
not** — never reproduce the substring outside a PR body meant to close; describe
it or drop the `#`. **After every merge, diff `gh issue list` against what the
commit says it filed and fixed**, both ways. **Check the ROADMAP for an issue
filed beside yours**: #234 had been open for three sessions on the exact shape
#230's survey turned up, with the remedy already written.

## Previous session: #241 and #248, an object's metadata and its attribution

**Merged as PR #250**; both issues shut on merge. `<alt-text>`, `<long-desc>`,
`<object-id>` and `<permissions>` are declined by three guards (one per route);
`<attrib>` is routed as a `<p>` or into its exhibit's `footnotes`. A second
review found the `<xref>` exception reaching one route of three and four more
shapes; all fixed, diffed to move nothing further. Filed #249, #251, #252. The
reasoning is in `CHANGELOG.md` and `docs/DECISIONS.md`.

## This session: #230 and #234, front-matter prose

**Answered in one patch, on measurement and by the maintainer's choice.** Prose
in `<front>` fell past `_append_prose` with no counter and no line, while a
front `<sec>` was filed titled and empty ahead of the body (#234, already open,
with exactly this remedy written in it).

- **Measured at the drop** with the parser's own predicates, fingerprinted
  against every destination (0 mismatches), cells excluded: 9,328 runs in
  3,350 of 8,118 served articles (41.3%), 114,519 in 46,737 of 97,909 archive
  (47.7%); `<author-notes>` the bulk (9,865 archive `COI-statement` runs), then
  `<notes>`, `<def-list>`, `<funding-group>`, `<trans-abstract>`, `<bio>`,
  `<title-group>` notes. 263 and 3,099 empty front headings.
- **Asked, and the answer was the recommended one on both questions**: route
  into `body_sections` in document order (a third slot flushed at `</front>`
  and at each front `<sec>`, so front matter lands ahead of the body, rendered
  after the abstract), and no special case for `<trans-abstract>`.
  `fn-type="edited-by"` is not filtered.
- **Blast radius** diffed against `main` over both artifacts: every move an
  insertion, reconciled per article (excess = empty paragraphs from author
  photos in `<bio><sec>`); no other public field moves. `definition_terms_dropped`
  now equals the `<def-item>` carrying no `<def>`, per article, on both. **A
  paragraph deposited twice renders twice** (Springer's open-access funding
  line, front and back: 108 served, 2,984 archive) — not deduplicated.
- **Mutation**: 16 mutants (14 on the change, plus two follow-ups). One fixture
  gap (predicate order) and one pre-existing unpinned guard (`in_back`, found by
  a control) were pinned; one equivalent by construction, stated at the site.
- **Three reviews before the PR.** Correctness found no defect, and the
  duplicated-paragraph consequence. **Claims found a real one**: a comment said
  JATS admits no `<ref-list>` in `<front>`, which is false (`<notes>` admits
  one), so front apparatus was routed as prose — the refusal now covers
  `<front>` (0 in either artifact; diffed against the commit before, nothing
  moves), which made the predicate-order fixture moot. It also corrected the
  `<floats-group>` composition and a dozen overstated comments.
- **Filed #253** (a `<floats-group>`'s non-float content: `<boxed-text>` prose
  and `<fig-group>`/`<table-wrap-group>` captions, with the same empty heading
  after the body). **#233 is narrowed**: its front-matter shape is gone.

## Current state

- **Version 0.10.0, released 2026-08-15 and live on PyPI**. The version lives
  in **five** places — `pyproject.toml`, `bmlib/__init__.py`, the README
  version line, `CLAUDE.md`'s header, `docs/manual/index.md`'s header line —
  and all five agree; only `bmlib/__init__.py` is guarded by anything but this
  list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag;
  **0.9.0 moves nothing stored**; **0.9.1 moves stored full text** (#79);
  **0.10.0 moves nothing stored but re-fetches the whole sync window once**
  (#95). The two questions are independent, and a downstream reading only the
  number must still read this list.
- **Tests: 4015 passing + 63 skipped** on this branch (`uv run pytest tests/
  -v`, 2026-09-13); **`main` at 8119e46 collects 4051**, i.e. 3988 + 63,
  measured in a worktree of `main` with `pytest --collect-only`, so this branch
  adds **27**, all in `tests/test_jats_parser.py`. Measure `main` yourself and
  never subtract from a previous handover's number. **The PostgreSQL half was
  not re-run and did not need to be** (`fulltext/` and documentation only); the
  last measured figure with `BMLIB_TEST_POSTGRESQL_DSN` set is 2435 + 2 on the
  #105 branch. Of the 63 default skips, 61 are the PostgreSQL
  parameterisations, 1 a PostgreSQL-only schema test, 1
  `test_pymupdf_requires_dependency`.
- **Run the PostgreSQL half locally — two minutes, and it finds real bugs.**
  Postgres.app ships the binaries; the socket directory must be a *short* path:
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
  Treat drift as a regression. The `unreleased` markers in `docs/manual/` and
  `ROADMAP.md` are promoted at release time; **146 lines carry one**,
  recounted 2026-09-13 on this branch as
  `grep -ric unreleased ROADMAP.md docs/manual/*.md` — it counts *lines*, not
  markers, and it is measured, not maintained, so recount rather than adjust.
  Grep case-insensitively for `unreleased`, not for `(unreleased)`. Write the
  marker bare, never with a guessed version. Markers inside
  `docs/superpowers/plans/` are historical records — leave them alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup* (no workflow file), ignores
  a PR's `reopened` action, and does not constrain the merge strategy (#78).

## Next up

### Open GitHub issues

**Fifty-five open** (`gh issue list --state open --limit 200`, 2026-09-13,
after filing 253), and **fifty-three once this PR merges and the two issues it
answers are shut**. Open now: #86, #92, #94, #103, #128, #137, #142, #143,
#144, #145, #150, #152, #154, #156, #157, #172, #173, #174, #175, #177, #178,
#179, #181, #186, #196, #197, #200, #201, #204, #207, #209, #210, #212, #214,
#215, #217, #221, #222, #223, #226, #227, #230, #231, #233, #234, #235, #240,
#242, #244, #245, #247, #249, #251, #252, #253. Re-count at the end against
`gh`, and again after any review round — and check that 230 and 234 actually
went.

**What still loses content the document carries**: **#253** (new: a
`<floats-group>`'s `<boxed-text>` — a *"Research in context"* panel, a
highlights list — reaches nothing, 30 runs in 9 served and 925 in 192 archive
articles (a `<boxed-text>`'s 28 and 894, a `<fig-group>`/`<table-wrap-group>`
caption's the rest), and a `<sec>` there is filed as an empty heading *after* the
body; a position decision, since document order puts a panel after the back
matter). **#249** (an exhibit's second-language caption, plus a latent
abstract-erasing shape at 0 population — a fixture for the second is cheap and
the first is a decision). **#242** (`<inline-graphic>` has no handler). **#251**
(declined metadata that is real content) and **#252** (a nested block
reordering a caption or abstract string). **#240** (a sectioned `<fn-group>`'s
heading, dropped uncounted), **#244** (a `<graphic>` owned by neither an
exhibit nor its footnote matter), **#233** (a formula merged into a dropped
`<p>` — narrowed by #230 to floats and `<floats-group>`), **#150** (a note-only
`<ref>` as an empty `<li>` — re-measure on the two artifacts first), **#235**'s
`<sec>` half, **#128** (all 13,624 hrefs measured use `xlink`, so downgrade it
rather than shut it), and **#175** (a formula deposited as an image). **#137 is
measured and larger than its title suggests** — every supplementary-material
and media legend reaches the prose without its title, in between 8.7% and about
40% of served articles — so it is a presentation decision about a big
population. **#245** and **#247** are the `<array>` pair. **#231** is the
untitled-section presentation residual, and #230 added front matter to it.
Every one is a decision rather than effort.

**Three have a measured-empty population and want closing rather than
building**: #204 and #207 measure 0 of 124, #210 measures 0 of 55 (its
remaining work is one test comment). **#212 blocks nothing but qualifies every
sampler share** — it is why `sample_api_failures.py` exits 1 on a clean run.

**Instrument-side leavings**: #214, #215, #217, #221, #222, #223, #226, #227,
#209, #196 (`publications/sync.py`'s own `User-Agent`, a latent second #194),
#197, #200, #201, #179, #181 (`last_is_thumb` over the wrong denominator).

**JATS contributor and reference half**: #142, #143, #144, #145; #152
(`<article-id>`'s reachability guard). Formula family: #178 (the open
question), #177 (a float shape measuring 0), #174 (MathML flattening), #173
(a figure's `alt` duplicating its `figcaption`), #172 (the cache has no version
stamp — every unreleased JATS change above is why that matters). **#186** is
the last full-text-refusal decision. **#154, #156 and #157 are one job, the
funder corpus** — any session extending a funder list owes #154 first. **#103**
is a docstring line; **#94 and #92** may not be tightened without their
samplers; **#86** is a manual duplicating two methods.

**The instrument debt is real and stated.** Seven sessions now (#224, #228,
#124, #238, #243, #241/#248, #230) measured from scratch scripts over the two
named artifacts; `scripts/sample_jats_exhibits.py` carries a counter for none
of them. Worth keeping, and rebuilt every session: the **two-checkout
comparator**, the **instrumented `_JATSHandler` subclass** (landing buffer,
arm destinations), and this session's **drop-site tally** — classify each
`_append_prose` run with the parser's own predicates and assert a before/after
fingerprint of every destination moved or did not — plus the per-article
reconciliation of a tally against the diff. Adding them to `scripts/` is a
session of its own.

**Provenance is a chain**: almost every open issue was filed by a PR reviewing
an earlier change; #245, #249 and #253 were found by measurement instead, as
#224 and #228 (both closed) were.

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** — `~/src/bmlibrarian` has missed six
  releases; read the intervening non-comparable behaviour changes first.
- **Wire the segmenter and the rule-based extractors in** — each needs a design conversation.
- **Feed the stored grants to `transparency/`** — a scoring change moving stored values.

### bmlibrarian → bmlib porting (Phase 3 is next)

The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** Phases 0–2 shipped (0.4.0, 0.7.0, 0.8.0). Phase 3 is
discovery (#12), `pubmed_search` (#13), MeSH (#21), ClinicalTrials.gov (#14 —
**check the caveat first**: the legacy bulk XML was deprecated in the 2024 API
v2 migration). Each needs its own design conversation rather than a straight
port; Phase 4 (the prompt-driven agent family) follows, reconciled against
`quality/` and `transparency/` rather than forked.

### The port recipe (repeat it)

1. **TDD, always.** Behaviour tests first (upstream is the spec), watch them
   fail, then port. Bug in a test you wrote? Fix the test, not correct code.
2. **Modernise to bmlib style:** AGPL header, `from __future__ import
   annotations`, lowercase builtin generics, `datetime.UTC`.
3. **Sever app coupling:** injected connections instead of
   `get_db_manager()`/`bmlibrarian.config`; optional deps behind
   `try/except ImportError`; LLM calls through `bmlib.llm` / `BaseAgent`.
4. **Export** from the package `__init__.py` — through a PEP 562
   `__getattr__` if the module needs an extra (#64).
5. **Verify** (tests + both ruff commands + mypy), **record** in
   `CHANGELOG.md` under `[Unreleased]`, and **reconcile rather than fork**.
6. **Read the spec on both sides; do not decide by eye** — `<Affiliation>` is
   declared `(%text;)*`, and a bare `.text` dropped rows. For a JATS parser
   rule, read the Swift port's normative `doc/cross_platform/jats_parsing.md`.

## Deliberate non-fixes — do not "fix" these

**Moved to [`docs/DECISIONS.md`](docs/DECISIONS.md). Read it before
"correcting" anything that looks wrong** in any package. Each entry was
investigated and closed as correct; add new entries there, not here.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` under *Coding Conventions*.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`** — CI pins
  **0.15.20**: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- **`uv run mypy` is a gate too** (#81), pinned to **2.3.0** in the `dev`
  extra with its settings in `pyproject.toml`. Give it no arguments and run it
  in the dev venv; anything deliberately unchecked is an inline
  `# type: ignore[code]` with its reason, never a per-module
  `ignore_missing_imports`.
- Tests use in-memory SQLite and mocked HTTP; no external services.
  `BMLIB_TEST_POSTGRESQL_DSN` must point at a database the tests may drop
  every table in (recipe under *Current state*).
- Session workflow: the `nextsession` skill; post-review fix-up: `fixall`.
- **Cutting a release** (0.4.0 through 0.10.0 were all cut this way): bump the
  version in the **five** places, promote the CHANGELOG's `[Unreleased]` body
  under a dated heading with a prose summary, promote the `unreleased` markers
  in `docs/manual/` and `ROADMAP.md`, add the release's own `ROADMAP.md` row,
  commit on a `release/X.Y.Z` branch and open a PR. **The number is a claim
  about the API, not about the data**, so state the data answer in prose every
  time. After CI **and CodeQL** are green, merge with any button, then **tag
  `main`'s tip rather than a particular commit** (#78):

  ```bash
  git checkout main && git pull --ff-only
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || exit 1
  grep -q '__version__ = "X.Y.Z"' bmlib/__init__.py || exit 1
  git tag -a vX.Y.Z -m "bmlib X.Y.Z" && git push origin vX.Y.Z
  ```

  These are **annotated** tags (`git rev-parse 'vX.Y.Z^{commit}'`). Then create
  the GitHub release, which is **what publishes** — `release.yml` refuses to go
  on unless the tag matches `bmlib.__version__`, runs `twine check --strict`,
  asserts `py.typed` survived packaging, and uploads via Trusted Publishing.
  **Hand the `pypi` environment gate over rather than approving it** — an
  upload is irreversible. Verify against `https://pypi.org/simple/bmlib/`, not
  the JSON API. Rehearse with a `workflow_dispatch` run (TestPyPI only).
- **Rehearse the release gates locally before opening the PR** — `uv build`,
  `twine check --strict`, a clean-venv install asserting `py.typed` survived,
  the wheel probed **one fresh interpreter per module** (#64). **Do not
  upload by hand**: the publish job has no `skip-existing`.
