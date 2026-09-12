# HANDOVER — bmlib development

_Last updated: 2026-09-13. **0.10.0 is released and on PyPI**; thirty-eight
changes sit unreleased, three of them instrument-only. All five version places
agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Thirty-eight changes, twenty-one of them `fulltext` JATS fixes filed within
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
one calling `JATSParser` itself. Five of them ride on one re-fetch and are
the largest by population, all diffed against `main` over the 8,118 served
articles of `PMC10030002_PMC10040000.xml.gz`:

- **#243** — a cell's text used to reach the buffer above it as well as the
  cell, so a `<table-wrap>` inside a `<p>` spliced the table's numbers into the
  sentence. A paragraph moves in **2,222 (27.4%)**: 6,356 stripped in place, 10
  dropped, 0 gained; `html_content` in exactly those. Archive: 21,377 of 97,909.
- **#224** — unsectioned `<back>` prose (`<ack>`, `<notes>`, `<fn-group>`,
  `<app>`, `<glossary>`, `<bio>`) used to be dropped. Prose moves in 5,990
  articles (73.8%), every move an insertion: 40,342 paragraphs, 0 lost.
  `has_body`, `figures`, `.tables`, `references` and `abstract_sections` move
  in **0**.
- **#124** — an exhibit's footnotes used to reach nothing; they now fill
  `JATSFigureInfo.footnotes` / `JATSTableInfo.footnotes`, marker folded in.
  Notes appear in **3,707 (45.7%)**, 16,935 of them.
- **#228** — a `<def-list>`'s `<term>` is folded into its definition's
  paragraph. A paragraph moves in **840 (10.3%)**, 12,667 in place.
- **#241/#248** — an object's `<alt-text>`, `<long-desc>`, `<object-id>` and
  `<permissions>` no longer weld into prose, abstracts or cells, and `<attrib>`
  is routed (a quote's as a paragraph, an exhibit's into its `footnotes`).
  HTML moves in **584 (7.2%)**: 3,200 paragraphs stripped, 239 attribution
  paragraphs and 146 exhibit notes added, 11 wholly-metadata paragraphs
  dropped, 121 graphical-abstract sections stripped of `"Image 1"`. Archive:
  3,098 of 97,909, where 18 graphical abstracts whose only text was their
  figure's attribution **lose that abstract section** to the figure's notes.

Then, reasoned or measured on smaller draws: **#146/#149** (over 880 local
articles / 20,770 references, `citation` moves for 4,499 in 191 articles —
3,541 rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for
502 in 14, HTML for 576 in 23), **#111** (an author list empty for the
majority of open-access articles), **#115/#117** (`figures` and `.tables`;
roughly half of `graphic_url` moves from a thumbnail to the full image),
**#147** (prose and HTML for 68 of 880, and a LaTeX preamble out of every
table cell), **#162** (HTML for 83 of every 997 recent), **#123/#125/#130**
(`body_sections`, about one recent article in ten), **#127**, **#120/#140**,
**#129**. **#238 and #245 move nothing stored** — three log
lines where there was silence, #245's naming content an `<array>` deposit
loses (355 cells in 8 of the 8,118 served articles), which #243 turns from a
corrupt survival into a clean one.

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
and the rendition chooses the denominator** (#164's correction is 18 figures
or a quarter of the population, depending on which bytes you count).
**Probe the contract, not the expression the reporter noticed** (#199 was
filed as four `.get()` calls and measured as 23 escapes). **State a blast
radius from a diff, not from the call graph** — and the diff's own predicate
is a claim to check: #224's first cut asked *prefix* where the honest test was
*subsequence*, and a metric that alarms is as wrong as one that flatters. **Pick
that predicate from what the change can do to a *string*, not only to the
list**: over a list whose every member changed, a `difflib` opcode walk aligns
arbitrarily and reported a stripped paragraph as lost (#243). **Where a large
corpus makes two dumps expensive, load both checkouts in one process and
compare in place** — after validating that comparator against the two-dump
result on the smaller artifact, which is what made #243's 2,222 trustworthy.
**The harness that produces a blast radius is itself an instrument**: three
sessions running it was the defect (an article skipped; `subsections` not
recursed into, 48% of paragraphs invisible; `f.footnotes` read unguarded on
`main`, 6,961 rows erroring). A gap between two of bmlib's own counts is a
defect in one of them until it is explained. **A mirror over the markup and a
tally from the routing are different instruments, and where they disagree the
routing is the finding** — quote the routing tally for what the code does
and the markup walk only for questions about the *deposit*. **A survey that
mirrors a routing rule by hand is the same class of defect even when it gets
the answer right**; where the code has a predicate, run the code. **Measure
a drop at the drop**. **Assert the number a log line prints, not that it
printed** (#224's counter over-reported through a green CI). **A container
you describe in prose is a claim too** — five files called `<fn-group>` a
wrapper *"some publishers put round a run of notes"*, and 0 of 106,027
articles deposit one inside an exhibit. **A committed corpus is not the only
honest population**: two named public artifacts at 8,118 and 97,909 are
bigger and re-derivable, but nothing in the suite re-derives them — state the
trade. **A survey can refuse part of a remedy**, not only size it (#224's
found the `<ref-list>` apparatus; #238's found #175's population under the
obvious ancestor spelling, which the issue's own owner scope excludes).

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding. **A guard whose reason moves needs its comment moved with
it.** **An `Any`-returning helper launders every annotation above it.** When a
fix extends a routing rule, walk every other path it reaches — the guard
written on one branch is the guard the others need. **An issue's suggested
remedy can be narrower than the rule it invokes**: #243's proposed
`characters()` hold reaches two of four routes and makes a third *worse*, an
`<xref>` building its link from the buffer the hold empties. **And a fix that
removes a corrupt survival can create a total loss elsewhere** — measure it
and count it (#245) rather than restoring the corruption. Read the rules *next to*
the one you are adding before calling a fix one line. **A set keyed on the
element cannot express a rule about the context** (`_INLINE_ELEMENTS` was
right for #120 and wrong for #146). **Suppressing a merge does not empty a
buffer.** **A counter's scope is a decision, and the deposit survey is what
makes it** — an ancestor test for #238's image counter would have handed
#175 a counter it never asked for, and a bare parent test would have pooled
#231's population under #238's name. **And say which guard makes which
exclusion**: four documents credited the parent test with keeping a
`<back><fn-group>`'s heading out, which it cannot — `fn-group` is in the set,
and it is the owner walk — and the same walk excludes a *sectioned* group's
heading, which is not #231's population and had no issue until PR #239's
review filed #240. **An empty deposit costs nothing**: every sibling counter
already made that rule and the first cut of #238's two did not.

*Diagnostics and tests.* A diagnostic's *level* is a claim that has to be
measured, **and the branch it sits on must be no wider than the draw** (#191).
**A branch that is several populations has to be split before it can be
levelled** (#218). **When a fix takes a counter's only measured population,
the counter needs a test of its own or it goes vacuous the same day** (#224
and `formulas_dropped`). **A status enum member is a stored claim**, so one
covering several causes puts words in a third party's mouth. **Before arguing
about a level, check the diagnostic exists** (#193). A net needs its own
false-positive net, and it must be free — the autouse `parser_log` fixture
makes every pre-existing fixture one. Key a counter on *structure*, never on
the routing it is checking, and **read the increment site, not the name or
the report** (a verdict line invented #162 outright). **Asserting that a
constant was imported is not asserting that it is used** — pick a fixture
where the restatement and the real rule disagree. **A rule enforced by prose
is not enforced** (`TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`,
`TestEverySectionIsGatedOnEveryCounterItReads`). **Checking the arithmetic is
not checking the rule**, and check the denominator too. **A zero over an
absent population is not a clean result.** Tell a vacuous green from one
asserting silence: **ask which line of the fixture the assertion depends on**.
**Mutate the *old* half of a condition you extend**, and give a fixture prose
*after* the close as well as before it. **A guard's mutant can be inert for
the very fixture that names it** (#228's `<term>` parent test). **A surviving
mutant is sometimes an unmade decision rather than a missing test** (#124's
caption-before-footnote order). **A membership is invisible wherever a
sibling member is also in the walk's path** (`<fn-group>` behind
`<table-wrap-foot>`). **Aim mutants at what a guard *reads*** — the counter,
the stack index, the branch order — not only at the guard. **An equivalent
mutant is not an untested guard**: where two independent protections cover
one defect, say which pair you broke and which edit you made. **A contract
net is blind to a value read *wrongly* without raising** (#199) — assert what
the run *concluded*. **Pick the fixture that separates the guard from its own
mutant**; **where no fixture separates two guards, pin the wire and say why**;
**a malformed fixture reads as a measurement** (`NCT01`). **A measured
*majority* is an argument against a diagnostic** (#235: 77% of articles) and
**a measured-empty population is an argument for closing an issue, not for
building it** (#204, #207, #210) — but a handful on the archive against zero
served is neither, and a counter costs nothing stored (#238).

*Live behaviour.* **A remote's error shape is a property of the *request*, not
of the endpoint** (#218: an evicted history session serves
`<eFetchResult><ERROR>` at 200, an id-based efetch answers 400 — do not
"reconcile" the two comments). **A property only a real remote can refute
needs a real probe**: #194 was invisible to the whole suite because no test in
it makes a live request. **A sampler must address *and head* requests exactly
as the code does.**

*Instruments.* **A list an instrument declares must be derived from the code
it measures, not restated** — an `ast` walk holding the two *equal* is what
catches it. **One declared list can hide two rules** (every-funder against
`records[0]`). **Where an instrument is wider than the code, say so at the
site and bound the cost.** **A guard on the page cannot see a loss one level
down** (#212). **Do not background a mutation sweep beside anything that
reads the same checkout** — and if the reader has provably finished its
import, back the file up to disk first, restore from the held string *and* the
backup, and clear `__pycache__` after each restore, which is what this session
did.

*Cost.* **A test that pins a decision is reversed, not deleted, when the
decision is** (#206's flip of `test_one_refusal_does_not_hide_…`). **The cost
of a schema addition is not a constant — it depends on what else is unreleased
beside it** (#198 was free beside #184 and #194). Ask what the batch already
costs before pricing a change against zero. **Check before pricing**: #124's
issue priced a `to_dict`/`from_dict` pair that neither exhibit model has ever
had.

*Process.* **A closing keyword next to an issue number closes it, quotation or
not** — never reproduce the substring; describe it or drop the `#`. **After
every merge, diff `gh issue list` against what the commit says it filed and
fixed**, both ways: it has caught four keywords that fired and a dozen merges
that left their issue open (243 this time, shut by hand a session late).

*This session's.* **Before discarding an element, find where its text lands
on `main`**, off the real handler's `text_stack`, counting every text-bearing
element (a direct-text-only count put `<attrib>`'s silent loss at 3,663 of 5,072
where it is 3,844 of 5,266). **Isolating a buffer answers the children that
merge, not those that route or write a builder directly** — name the routes by
kind and guard each. **Reconcile a routing tally against the diff per
article**, not in total. **A child census of the owners you touch finds the
next issue** (#249). **Ask once the numbers are in** when scope is a modelling
choice. **Run a correctness review and a claims review before the PR**: they
found five legal shapes at 0 population and a "equivalent" mutant pair that was
equivalent only in its sectioned fixture.

## Previous session: #243, a cell's text is the cell's own

**Merged as PR #246**, closed by hand at the start of this session. `td`/`th`
joined `_TEXT_ACCUMULATING` rather than taking the issue's `characters()` hold,
which reached two of four routes and made the `<xref>` one worse. `<array>`
cells now reach nothing and are counted (`cell_text_dropped`, #245). Filed:
#247 (an `<array>` under a `<table-wrap>`) and #248. The reasoning is in
`CHANGELOG.md` and `docs/DECISIONS.md`.

## This session: #241 and #248, an object's metadata and its attribution

**Answered in one patch, with the scope widened on measurement and by the
user's choice.** Five elements accumulated nowhere, so an exhibit or image
inside a `<p>` welded their text into the sentence and an image in a cell into
the table: 4,018 `<alt-text>` in 522 of 8,118 served articles into a `<p>`,
67 (in 9) into a cell, almost all placeholders.

- **`<alt-text>`, `<long-desc>`, `<object-id>`, `<permissions>` are declined**
  (`_NON_PROSE_METADATA`) by three guards, one per route: buffer membership for
  merges, a refusal in `_append_prose` (mirrored in `_prose_reaches_output`)
  for a `<p>` routing out of a `<license>`, and `_offer_cell_text` as the one
  door for `characters()` and the formula arm. Nothing is counted. The text
  is kept under a `<mixed-citation>` (#146) and an `<xref>` (whose arm would
  otherwise invent `"Figure"` for an image-only link), 0 deposits either way.
- **`<attrib>` is routed, not discarded** — asked of the user once the survey
  showed it is printed and already silently lost where its owner stood in a
  section (3,844 of 5,266 archive quote attributions). It routes as a `<p>`;
  an exhibit's attribution, or its image's, joins that exhibit's `footnotes`
  by a parent test; it spends no pending marker or term; in a
  `<mixed-citation>` it is the citation's alone. All 6,343 archive and 385
  served attributions are accounted for; notes equal the diff's insertions on
  both artifacts, and paragraphs on the served one (archive +20, traced).
- **Blast radius** (unreleased list above), re-run unchanged after the
  review fixes, and **mutation** (36 mutants and 2 pairs; four first-sweep
  fixture gaps and five review-found shapes, all pinned) are in
  `CHANGELOG.md`. The mirror alone is an equivalent mutant: the definition
  fold is also protected by the refusal's position, and the formula counter
  by the arm's own subtraction.
- **Filed #249** (an `<abstract>` inside an exhibit: a second-language
  caption dropped, and a latent abstract-erasing shape). **Commented #137**
  with its first measurement (every supplementary-material/media legend is
  prose without its title — it absorbs #241's `<caption>` half, 0 for a
  `<graphic>` owner) and **#173** (2,390 of 2,491 served figure-level
  `<alt-text>` are placeholders).

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
- **Tests: 3964 passing + 63 skipped** on this branch (`uv run pytest tests/
  -q`, 2026-09-13); **`main` at 3547dcd collects 3985**, i.e. 3922 + 63,
  measured in a worktree of `main` with `pytest --collect-only`, so this branch
  adds **42**, all in `tests/test_jats_parser.py`. Measure `main` yourself and
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
  `ROADMAP.md` are promoted at release time; **144 lines carry one**,
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

**Fifty-four open** (`gh issue list --state open --limit 200`, 2026-09-13,
after shutting 243 by hand and filing 249), and **fifty-two once this PR
merges and the two issues it answers are shut**. Open now: #86, #92, #94, #103, #128, #137, #142, #143, #144,
#145, #150, #152, #154, #156, #157, #172, #173, #174, #175, #177, #178, #179,
#181, #186, #196, #197, #200, #201, #204, #207, #209, #210, #212, #214, #215,
#217, #221, #222, #223, #226, #227, #230, #231, #233, #234, #235, #240, #241,
#242, #244, #245, #247, #248, #249. Re-count at the end against `gh`, and again
after any review round — and check that 241 and 248 actually went.

**What still loses content the document carries**: **#230** (front-matter
prose, the largest silent drop left — 17,612 paragraphs in 49.8% of 15,000
articles, `<author-notes><fn fn-type="COI-statement">` among them, no counter
and no line). **#249** (new: an exhibit's second-language caption, plus a
latent abstract-erasing shape at 0 population — a fixture for the second is
cheap and the first is a decision). **#242** (`<inline-graphic>` has no
handler; the captions this session stripped of `"Image 1"` are its
population). **#240** (a sectioned `<fn-group>`'s heading, dropped
uncounted), **#244** (a `<graphic>` owned by neither an exhibit nor its
footnote matter, the `<td>`'s first), **#150** (a note-only `<ref>` as an empty
`<li>` — re-measure on the two artifacts first), **#235**'s `<sec>` half,
**#128** (all 13,624 hrefs measured use `xlink`, so downgrade it rather than
shut it), and **#175** (a formula deposited as an image). **#137 is now measured
and larger than its title suggests** — every supplementary-material and media
legend reaches the prose without its title, ≥30.7% of served articles — so it
is a presentation decision about a big population rather than an edge case.
**#245** and **#247** are the `<array>` pair: a modelling decision, and a
phantom row in a real table. **#231** is #224's presentation residual.
Every one is a decision rather than effort.

**Three have a measured-empty population and want closing rather than
building**: #204 and #207 measure 0 of 124, #210 measures 0 of 55. **#212
blocks nothing but qualifies every sampler share** — it is why
`sample_api_failures.py` exits 1 on a clean run.

**Instrument-side leavings**: #214, #215, #217, #221, #222, #223, #226, #227,
#209, #196 (`publications/sync.py`'s own `User-Agent`, a latent second #194),
#197, #200, #201, #179, #181 (`last_is_thumb` over the wrong denominator).

**JATS contributor and reference half**: #142, #143, #144, #145; #152
(`<article-id>`'s reachability guard). Formula family: #178 (the open
question), #177 (a float shape measuring 0), #174 (MathML flattening), #173
(a figure's `alt` duplicating its `figcaption`, now with the `<alt-text>`
measurement), #172 (the cache has no version stamp — every unreleased JATS
change above is why that matters). **#186** is the last full-text-refusal
decision. **#154, #156 and #157 are one job, the funder corpus** — any session
extending a funder list owes #154 first. **#103** is a docstring line; **#94
and #92** may not be tightened without their samplers; **#86** is a manual
duplicating two methods.

**The instrument debt is real and stated.** Six sessions now (#224, #228,
#124, #238, #243, #241/#248) measured from scratch scripts over the two named
artifacts; `scripts/sample_jats_exhibits.py` carries a counter for none of
them. The comparator is the piece most worth keeping, and this session added
two more worth keeping beside it: an **instrumented `_JATSHandler` subclass**
that reads a run's landing buffer off `text_stack`, and a **routing tally of
an arm's destinations by before/after snapshot**, reconciled per article
against the diff's insertions. Adding them to `scripts/` is a session of its
own.

**Provenance is a chain**: almost every open issue was filed by a PR reviewing
an earlier change; #224, #228 and #249 are the exceptions.


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
