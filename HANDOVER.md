# HANDOVER — bmlib development

_Last updated: 2026-09-11. **0.10.0 is released and on PyPI**; thirty-five
changes sit unreleased, three of them instrument-only. All five version places
agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Thirty-five changes, eighteen of them `fulltext` JATS fixes filed within days
of each other — whoever cuts the next release should describe those together.
**Per-PR argument is in `CHANGELOG.md`; only the *data* answer is kept here**,
because the version number answers the API question and never that one. Three
(#211, #212, #216) touch `scripts/` alone and cost a downstream nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored, so **a downstream holding cached full text should re-fetch**, not only
one calling `JATSParser` itself. Three of them ride on one re-fetch and are
the largest by population, all diffed against `main` over the 8,118 served
articles of `PMC10030002_PMC10040000.xml.gz`:

- **#224** — unsectioned `<back>` prose (`<ack>`, `<notes>`, `<fn-group>`,
  `<app>`, `<glossary>`, `<bio>`) used to be dropped. Prose moves in 5,990
  articles (73.8%) and **every move is an insertion**: 40,342 paragraphs and
  5.91 MB gained, 0 lost, 0 altered; `html_content` moves in all 5,990.
  `has_body`, `figures`, `.tables`, `references` and `abstract_sections`
  move in **0** — the invariant the fix is built around.
- **#124** — an exhibit's footnotes used to reach nothing; they now fill
  `JATSFigureInfo.footnotes` / `JATSTableInfo.footnotes`, marker folded in
  (`"a — Adjusted for age."`). Notes appear in **3,707 (45.7%)** — 16,935 of
  them, 2 a figure's — and `html_content` moves in exactly those. Prose,
  titles, captions, `references` and `has_body` move in **0**. The archive
  artifact agrees as a routing tally: 190,198 notes in 45,099 of 97,909.
- **#228** — a `<def-list>`'s `<term>` is folded into its definition's
  paragraph. A paragraph moves in **840 (10.3%)**, 12,667 change in place, 0
  gained, 0 lost; `html_content` moves in the same 840.

Then, reasoned or measured on smaller draws: **#146/#149** (over 880 local
articles / 20,770 references, `citation` moves for 4,499 in 191 articles —
3,541 rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for
502 in 14, HTML for 576 in 23), **#111** (an author list empty for the
majority of open-access articles), **#115/#117** (`figures` and `.tables`;
roughly half of `graphic_url` moves from a thumbnail to the full image),
**#147** (prose and HTML for 68 of 880, and a LaTeX preamble out of every
table cell), **#162** (HTML for 83 of every 997 recent), **#123/#125/#130**
(`body_sections`, about one recent article in ten), **#127**, **#120/#140**,
**#129**. **#238 moves nothing stored** — two log lines where there was
silence.

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
*subsequence*, and a metric that alarms is as wrong as one that flatters.
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
written on one branch is the guard the others need. Read the rules *next to*
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
not** — four times now, the fourth being the commit that warned about the
other three. Never reproduce the substring; describe it or drop the `#`. And
**after every merge that mentions an issue in prose, diff `gh issue list`
against what the commit says it filed and fixed** — that diff has caught
keywords that fired (#137, #142, #160, #230) and eleven merges that closed
nothing (#124 this session, #228, #224, #206/#218, #211, #199,
#198/#202/#203, #193/#194, #187/#190/#191, #184, #183, #161, #188/#216), each
closed by hand a session late. It catches the other direction too: issues
filed after a census was written (#238 this time).

## Previous session: #124, an exhibit's footnotes

**Answered and merged as PR #237**, closed by hand at the start of this one.
The reasoning is in `CHANGELOG.md`, the four undoable rules in
`docs/DECISIONS.md`, the lessons folded into *Rules carried forward*. Its
review filed **#238**, this session's issue.

## This session: #238, a footnote block's own heading and image

**Answered, the issue's own suggested resolution taken, and nothing stored
moves.** A `<table-wrap-foot>`'s or exhibit `<fn-group>`'s `<title>` is
refused by the `<title>` owner rule (#125, #130) and a `<graphic>` the
footnote matter owns by `_graphic_owner`'s opacity (#127); once #124 made the
block a destination, those were the two things in it leaving no trace beside
its own `<label>`, which is #235's. Both drops stay — each is a rule this module argued for — and each is now counted
and reported once per article at WARNING (`footnote_headings_dropped`,
`footnote_graphics_dropped`), `refused_apparatus_prose`'s rule. Folding the
heading in was refused on **shape**, not population: the block is a list of
notes, so the fold either files a heading as a note or welds it onto the first
note's marker and breaks the `split` the #124 decision promises — a wrong
value against a blank. `docs/DECISIONS.md` has it.

**The deposit survey decided the scope, and both exclusions have a home
elsewhere.** Walked as the parser routes (suppressed regions skipped, a cell
ending the owner walk) over both artifacts: the served bundle deposits **no**
block heading and **one** footnote-matter image, an `<inline-formula>`'s; the
archive deposits 7 headings in 4 articles, every one a `<table-wrap-foot>`'s
reading *"Note"*, *"Note:"* or *"Fontes:"*, and **329** footnote-matter images
of which **319 (in 70 articles) are a formula's** — #175's population, a
formula deposited as an image — 3 a `<boxed-text>`'s, and **7 in 4 the
`<fn>`'s**. An ancestor test, the obvious spelling, would have pooled all
three under #238's name; the counter is keyed on an owner *in*
`_EXHIBIT_FOOTNOTE_CONTAINERS`; every owner outside the three sets is #244's
residual. The heading counter is keyed on the block's own parent *and* on the
owner walk finding an exhibit, for the matching reason, and each guard keeps
a different population out: the parent a `<list><title>` in a note, the same
drop as one in body prose; the walk every `<fn-group>` heading belonging to
no exhibit — an unsectioned `<back>`'s is #231's, and a sectioned one is
#125's own residual, #240. **The image counter counts deposits**: an
`<alternatives>` pair reads 2 and its line says *graphic deposit(s)*, the
unit the survey counts. **An empty deposit costs nothing on either.**
**Measured by the counters themselves** (the routing tally, not the survey):
0 and 0 over 8,118 served, and over 97,909 archive 7 headings in 4 articles
and 7 deposits in 4 — the *scoped* survey and the tally agreeing to the unit,
and `footnote_markers_dropped` re-read at its recorded 0 on both artifacts.
The issue's own unscoped whole-document walk agrees for the image (7) and not
for the heading (8). Re-tallied after PR #239's review's guards landed: the
same 7 and 7.

**Eighteen mutants, seventeen killed, each by exactly the fixture written
for it** — the three guards on each arm (empty deposit, parent or owner test,
exhibit walk), `and not self.in_abstract` on the heading guard, `fn-group`
refused as an image's owner, both block-set members, a double increment on
each counter, the audit lines chained, cross-gated and removed, and the image
line's unit reverted. The survivor widens `_EXHIBIT_FOOTNOTE_BLOCKS` to the
container set, which only an `<fn><title>` — a shape JATS does not admit —
could tell apart; the set's comment says so. The sweep ran with the on-disk
backup, the held-string restore and a `__pycache__` clear after every mutant
— the rule above says why that is not licence to do it casually.

**PR #239's review, applied in the same PR.** Four mutants survived the first
cut's nine: the image audit line chained as an `elif` of the heading one
(no fixture held both counters), `and not self.in_abstract` on the heading
guard (the abstract-exhibit route was exercised by a pre-existing test that
asserted nothing about it), `fn-group` excluded as an image's *direct* owner,
and `_EXHIBIT_FOOTNOTE_BLOCKS` widened to the container set — equivalent for
valid JATS, so documentary rather than pinnable. Two claims were false: both
counters fired on an *empty* deposit (`<title/>`, an href-less `<graphic/>`)
and stated a loss that did not happen, where every sibling counter excludes
empties; and an `<alternatives>` pair printed *"2 image(s)"* for one image,
so the line now names the deposit as its unit. Four documents attributed the
`<back><fn-group>` exclusion to the parent test. And five defects older than
this change were verified by parse and filed rather than fixed — #240
(sectioned `<fn-group>` heading, #125's residual, 12 in 3 of 997 served),
#241 (`<alt-text>` welded into prose, a wrong value), #242
(`<inline-graphic>` unhandled), #243 (prose around an inline `<table-wrap>`
absorbing cell text, a wrong value), #244 (a `<graphic>` owned by anything
else, `<td>`'s 82 in 8 of 997 among them) — with the block's own `<label>`
noted on #235.

**One correction to the record.** `docs/DECISIONS.md`'s #124 entry said the
block's own `<title>` residual was *"not worth filing"* on a measured zero —
a zero measured for the `<fn-group>` alone, where the `<table-wrap-foot>`'s
heading is deposited in the archive. The sentence now says so.

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
- **Tests: 3895 passing + 63 skipped** on this branch (`uv run pytest tests/
  -q`, 2026-09-11); **`main` at 7b253a6 measures 3881 + 63**, measured in this
  checkout before the branch was cut, so this branch adds **14**, all in
  `tests/test_jats_parser.py`. Measure `main` yourself and never subtract from
  a previous handover's number — four sessions in a row published a wrong one
  before the last two measured it. **The PostgreSQL half was not re-run for this branch and did
  not need to be** (it touches `fulltext/` and documentation); the last
  measured figure with `BMLIB_TEST_POSTGRESQL_DSN` set is 2435 + 2 on the #105
  branch. Of the 63 default skips, 61 are the PostgreSQL parameterisations, 1
  a PostgreSQL-only schema test, 1 `test_pymupdf_requires_dependency` (runs
  only when PyMuPDF is *absent*; it is installed in the dev venv).
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
  `ROADMAP.md` are promoted at release time; **137 lines carry one**,
  recounted 2026-09-11 on this branch as
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

**Fifty-two open**, counted from `gh` at the moment of writing with #124
closed by hand, this session's PR open and its review's five filed, and
**fifty-one once this PR merges and #238 is closed by hand**
(`gh issue list --state open --limit 200`, 2026-09-11; the limit matters, `gh`
pages at 30): #86, #92, #94, #103, #128, #137, #142, #143, #144, #145, #150,
#152, #154, #156, #157, #172, #173, #174, #175, #177, #178, #179, #181, #186,
#196, #197, #200, #201, #204, #207, #209, #210, #212, #214, #215, #217, #221,
#222, #223, #226, #227, #230, #231, #233, #234, #235, #238, #240, #241, #242,
#243, #244. Re-count at the end against `gh`, and again after any review
round.

**What still loses content the document carries**: **#230** (front-matter
prose, the largest silent drop left — 17,612 paragraphs in 49.8% of 15,000
articles, `<author-notes><fn fn-type="COI-statement">` among them, with no
counter and no line), **#150** (a note-only `<ref>` as an empty `<li>` —
measured 0 of 72,416 `<ref>` in both committed windows, 4 in one publisher in
the 880-article draw, so it wants re-measuring on the two named artifacts
first), **#235**'s `<sec>` half (a numbered section's number; `<aff>` and
`<list-item>` halves are drops to record, the `<fn>` half narrowed by #124 to
the 2,789 markers outside an exhibit, and `<supplementary-material>`'s 2,998
named by nobody), **#128** (every figure image in a document binding XLink to
another prefix — all 13,624 hrefs measured use `xlink`, so downgrade rather
than close), and **#175** (a formula deposited as an image — 319 of them sit
in exhibit footnote matter alone, per this session's survey). **PR #239's
review added five**, all older than #238 and each verified by parse: #240
(a sectioned `<fn-group>`'s heading, dropped uncounted), #241 (a `<graphic>`'s
`<alt-text>` welded into the sentence and its `<caption>` filed as a stray
paragraph — a wrong value), #242 (`<inline-graphic>` has no handler, so a
marker deposited as an image is lost with the note unmarked), #243 (cell
text reaching the paragraph around an inline `<table-wrap>`,
`'Before12.3after.'` — a wrong value), and #244 (a `<graphic>` owned by
neither an exhibit nor its footnote matter, the `<td>`'s 82 in 8 of 997
first). #241 and #243 are the two to take first, being corruptions rather
than blanks. Every one is a decision
rather than effort. **#231** is the presentation residual of #224: back
matter renders as one untitled section; deciding wants a measurement nobody
has taken, how often one `<back>` carries several distinct containers. The
tempting answer — invent a heading from the container — is probably wrong for
#116's and #162's reasons.

**Three have a measured-empty population and want closing rather than
building**: #204 and #207 measure 0 of 124, #210 measures 0 of 55. **#212
blocks nothing but qualifies every sampler share** — it is why
`sample_api_failures.py` exits 1 on a clean run; three options, three
populations (drop the PMC strata, condition each query on analysability, or
page each stratum until it fills).

**Instrument-side leavings**: #214, #215, #217 (PR #213's review), #221, #222,
#223 (PR #219's, one sweep with #217), #226 (`_json_bool` refusing a value in
silence at the one site where it decides a stored status), #227 (the PubMed
step that was never asked leaves no trace), #209 (a coerced value leaves no
line), #196 (`publications/sync.py` builds its own `User-Agent` outside the
sampler's guard — a latent second #194), #197, #200, #201, #179, #181 (the
sharpest: `last_is_thumb` is reported over the wrong denominator, so #117's
rule is far more load-bearing than the published 57.3%).

**JATS parser, contributor and reference half**: #142 (`<collab>` children
run together), #143 (bare last-wins with no parent test), #144
(`<on-behalf-of>`), #145 (`<aff>` resolution through `@id`) — PR #141's
review; #137 (a section-level `<caption>`'s `<title>`, auto-closed twice and
never decided); #152 (`<article-id>`'s reachability guard, neither half
pinned). Formula family: #178 is the open *question* (should LaTeX win for a
both-encoding inline formula, replacing prose correct in 20,046 to recover
205?), #177 narrowed by #224 to a float shape measuring 0, #174 (MathML
flattening loses spacing and brackets), #173 (a figure's `alt` duplicating its
own `figcaption`), #172 (the cache has no version stamp — and every unreleased
JATS change above is why that matters).

**#186** is the last of the full-text-refusal family and a decision: the
unclosed-region refusal knows which element was left open and discards it, and
naming it moves `_strip_nested_articles`' `str | None` contract.

**#154, #156 and #157 are one job, the funder corpus**: the raw draw was never
committed, so a redraw has nothing to diff against; #156 needs a draw
stratified for European funders, #157 a targeted `\bplc\b` draw. **Any session
extending a funder list owes #154 first.**

**#103** (`install_defaults()` and `NAME_MAX`) is a docstring line, not a cap.
**#94 and #92** are guards resting on an unmeasured quantity — **neither may
be tightened without running the sampler it asks for**; **#86** is
`docs/manual/llm.md` documenting `generate` and `embed` twice, copies differing.

**The instrument debt of the last four sessions is real and stated.** #224,
#228, #124 and #238 were all measured from scratch scripts over two named
public artifacts, not from `scripts/sample_jats_exhibits.py`, which carries a
counter for none of them; the scripts go with each session's scratchpad, and
this session rebuilt a deposit survey and a routing tally from nothing again.
Adding the counters is a generation on that sampler plus a full live redraw of
both committed corpora (~50 min, moving every figure they pin) — a session of
its own, and worth weighing against the package draws being 8,118 and 97,909
articles against the corpora's 997 and 1,000.

**Provenance is a chain**: almost every open issue was filed by a PR reviewing
an earlier fix (#224 → PR #232 → #228 → PR #236 → #124 → PR #237 → #238 → PR
#239 → #240–#244 is the recent run; #224 came from outside the chain, #228
from a measurement). `gh issue view <n>` and `CHANGELOG.md` hold the rest.

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
