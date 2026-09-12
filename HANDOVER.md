# HANDOVER — bmlib development

_Last updated: 2026-09-13 (start of session: PR #246 merged, issue 243 closed by
hand). **0.10.0 is released and on PyPI**; thirty-seven
changes sit unreleased, three of them instrument-only. All five version places
agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Thirty-seven changes, twenty of them `fulltext` JATS fixes filed within days
of each other — whoever cuts the next release should describe those together.
**Per-PR argument is in `CHANGELOG.md`; only the *data* answer is kept here**,
because the version number answers the API question and never that one. Three
(#211, #212, #216) touch `scripts/` alone and cost a downstream nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored, so **a downstream holding cached full text should re-fetch**, not only
one calling `JATSParser` itself. Four of them ride on one re-fetch and are
the largest by population, all diffed against `main` over the 8,118 served
articles of `PMC10030002_PMC10040000.xml.gz`:

- **#243** — a cell's text used to reach the buffer above it as well as the
  cell, so a `<table-wrap>` inside a `<p>` spliced the table's numbers into the
  sentence. A paragraph moves in **2,222 (27.4%)**: 6,356 stripped in place, 10
  dropped, 0 gained; `html_content` in exactly those; `abstract_sections`,
  captions, exhibit footnotes, `references`, every table's own `html_content`
  and `has_body` in **0**. Archive: 21,377 of 97,909 (21.8%), plus 11 abstracts
  and 1 figure caption.
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
not** — four times now, the fourth being the commit that warned about the
other three. Never reproduce the substring; describe it or drop the `#`. And
**after every merge that mentions an issue in prose, diff `gh issue list`
against what the commit says it filed and fixed** — that diff has caught
keywords that fired (#137, #142, #160, #230) and eleven merges that closed
nothing (#124 this session, #228, #224, #206/#218, #211, #199,
#198/#202/#203, #193/#194, #187/#190/#191, #184, #183, #161, #188/#216), each
closed by hand a session late. It catches the other direction too: issues
filed after a census was written (#238 this time).

## Two sessions ago: #238, a footnote block's heading and image

**Answered and merged as PR #239**, closed by hand at the start of this one.
The reasoning is in `CHANGELOG.md`, the decisions in `docs/DECISIONS.md`, the
lessons folded into *Rules carried forward*. Its review filed **#240–#244**,
of which #243 is this session's issue.

## Previous session: #243, a cell's text is the cell's own

**Answered and merged as PR #246**, closed by hand at the start of the next
session. **Answered, and the population is 27.4% of served articles rather than the
edge case the issue supposed.** `characters()` delivered every cell's text to
the open buffer *as well as* to the cell, so a `<table-wrap>` deposited inside
a `<p>` spliced the table's numbers into the sentence —
`'Before12.3after.'` in `body_sections` and in the cached HTML — and an
exhibit inside a footnote's `<p>` gave the outer note `'a — See12.3'`. A
**wrong value** where a blank was the alternative.

**The issue's own remedy reaches two of the four routes found and makes a
third worse.** A hold inside `characters()` catches raw character data and an
inline run merging back; an `<xref>` *replaces* its text with a link built
from the popped buffer, so emptying it fires the arm's own `text or "Figure"`
fallback and yields `'[Figure](#f1)'` — an **invented** label, #162's own
symptom and worse than the blank it replaces — and the formula arm appends its
rendition through its own `_append_text` the method never sees. Enumerating
the arms that merge is #116's uncompletable list, so the argument is about the
fifth route nobody has found. `td`/`th` join `_TEXT_ACCUMULATING` instead.
**Accumulating in order to discard is not what is particular about them**:
nine other members do (`<sec>`, `<abstract>`, `<caption>`, `<def>`,
`<list-item>`, `<person-group>`, `<element-citation>`, `<alt-title>`,
`<kwd>`), two documented as such in the module. What is particular is that a
cell's children route to a *builder*; and one arm does consult a cell's
buffer, for **emptiness alone**, to decide whether an unmodelled cell lost
anything (#245) — its content is read nowhere. **Membership needed one
exclusion of its own**: `_inside_mixed_citation()` was the single path left by
which a cell's buffer could merge, so the pop carries `not is_cell` beside the
terms `_FORMULA_PARTS` and `_UNDIVIDED_NAME_ELEMENTS` already earn; without
it a cell under a citation reached `JATSReferenceInfo.citation` *and* the cell,
and for the unmodelled half `cell_text_dropped` claimed a loss that had not
happened. Measured 0 such cells over both artifacts, so it pins a direction.
The paragraph then reads `'Beforeafter.'` **of the cells** — an `<alt-text>`,
`<attrib>`, `<long-desc>`, `<object-id>`, `<copyright-statement>` or
`<copyright-year>` still welds in, 537 of 8,118 served articles, which is #248
beside #241 — and block spacing stays #147's question.
`docs/DECISIONS.md` has all of it, with the `<xref>` fixture named as what
separates the two remedies.

**Blast radius, diffed against `main` over two named public artifacts.**
Served (`PMC10030002_PMC10040000.xml.gz`, 8,118 articles): a paragraph moves
in **2,222 (27.4%)** — 6,356 stripped in place, 10 dropped, **0 unexplained**
— `html_content` in exactly those, and `abstract_sections`, captions, exhibit
footnotes, `references`, every table's own `html_content` and `has_body` in
**0**. Archive (97,909): **21,377 (21.8%)**, 50,042 stripped, 286 dropped, 0
unexplained, plus **11 abstracts and 1 figure caption** — two destinations the
served bundle happens not to exercise. **A downstream holding cached full text
should re-fetch.**

**The diff's own predicate was wrong first.** A `difflib` opcode walk over a
list whose every member changed aligns arbitrarily: it reported 15 paragraphs
lost, one of them present and merely stripped. This change can only *remove*
characters, so a character-level subsequence test is the exact predicate. The
10 dropped are each a `<p>` whose only content was the table; three are
Springer/Adis *"Key Points"* panels present in `.tables` with proper rows, so
`main` stored that content **twice**. Every lost section title is an *empty*
one, on both artifacts — 6 titles in 6 served articles and 68 in 68 archive ones, so the two units coincide; 0 non-empty either way. A paragraph whose
whole content was the table is now an empty string rather than absent — 697
more across 283 served articles, which `_format_body_section_html` skips, so
no rendered HTML moves for it.

**Each gap between two counts was closed.** 7,248 `<table-wrap>` inside a `<p>`
in 2,237 of 8,118 served articles, a `<p>` being the only reading buffer that
carries one. The survey's 2,223 articles with a cell under a reading buffer
against the diff's 2,222 is one paper whose single inline table holds one
*empty* cell. On the archive the counter's 248,720 against the survey's
251,362 is 2,141 blank cells plus 501 whose text sits only in a non-merging
child. Every one of those children resolves to a `<p>`, whose own arm files
the text — a direction rather than a guarantee, since a child with no arm at
all (a `<list-item>`) would be dropped with nothing counted, measured 0 on
both.

**#245 is what the fix makes total rather than partial, and it is counted.**
`<array>` opens no `_TableBuilder`, so its cells reach nothing; their text used
to reach the buffer above (a `<sec>`'s, discarded; a `<p>`'s, spliced). A blank
beats a wrong value, so the drop stays and `cell_text_dropped` reports it once
per article at WARNING, counting the **cell** and never the character, an empty
cell costing nothing. **355 cells in 8 of 8,118 served articles** — 173 in the 3 where the splice
was visible, the other 182 in 5 inside a `<glossary>` where the text was
already being discarded — against 248,720 in 6,726 of 97,909 archive ones. The
two renditions disagree roughly seventy-fold, on draws from different accession
ranges, so rendition and corpus cannot be separated; the served figure sizes
the priority, being the bytes `FullTextService` is fed. **The counter is keyed
on no builder being open, which is narrower than "no table received this
cell"**: an `<array>` inside an open `<table-wrap>` routes into that builder,
splices a phantom row into a real table and takes the silent branch —
pre-existing, 0 of 8,118 served and 0 of 97,909 archive, filed as #247.

**Nine mutants in the first sweep, all killed, each attributed rather than
counted** — each set
member separately and together, `td`/`th` made *inline* (which is `main`'s
behaviour spelled differently), the counter reading the unstripped buffer,
firing on an empty cell and double-incrementing, and the audit line removed and
chained as an `elif` of the block above, PR #239's own surviving mutant, for
which a fixture holding two counters at once was written. **Two independent
protections turned up rather than one**: the three membership mutants also
redden `test_no_arm_reads_a_buffer_for_an_element_that_does_not_accumulate`,
#151's `ast` net, because the counter's `elif text:` reads the ancestor's
buffer the moment `td` leaves the set.

**One process rule was broken and is worth recording.** A comment in
`jats_parser.py` was edited while the sweep held that file, so the restore
discarded the edit. Nothing was lost beyond the edit, re-applied and
re-verified — but the same slip on a *test* file would have been silent.

**The review found three of those nine mutants had a fourth sibling that
survived, and the gap was one axis rather than three.** Every `<array>`
fixture in `TestACellThatReachesNoTableIsCounted` deposited body cells inside
a `<p>` inside `<body>`, so `elif text and name == "td":`,
`elif text and len(self.text_stack) > 2:` and `elif text and self.in_body:`
each passed the whole file. Three fixtures close it — a `<th>` in an
`<array>`, an `<array>` between two paragraphs rather than inside one, and
one in `<front><abstract>` and `<back><ack>` — and the second is the
important one: **182 of the 355 served cells, in 5 of the 8 articles, sit in
a `<glossary>`**, which is the *"pre-existing and was silent"* half the
counter exists for and the half no fixture reached. Seven mutants were
re-swept after the fix and all seven die.

**Eight claims were corrected and none of the code they described was
wrong.** The `<xref>` fallback makes the rejected remedy yield
`'[Figure](#f1)'` and not `'[](#f1)'` — which strengthens the argument, an
invented label being worse than a blank — and that string stood in eight
files. *"No arm reads the buffer a `<td>` takes"* was refuted by the counter
added in the same commit; the honest claim is that its **content** is read
nowhere. *"The only two members that accumulate in order to discard"* has
nine counterexamples, two of them documented as such in this module. And the
`'Beforeafter.'` claim is true of **cells**, not of everything an inline
exhibit holds — #248. A measurement settled the one genuine ambiguity: 68
titles in 68 archive articles, so both readings of a bare *"68"* were right.

**Two issues were filed rather than fixed, both measuring 0 on both
artifacts.** #247 is `cell_text_dropped` being keyed on *no builder open*
rather than on *no table received this cell*, so an `<array>` under a
`<table-wrap>` splices a phantom row into a real table and takes the silent
branch. #248 is the exhibit-metadata leak, 3,877 runs in 537 of 8,118 served
articles, neighbouring #241 and wanting one patch with it.

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
- **Tests: 3917 passing + 63 skipped** on this branch (`uv run pytest tests/
  -q`, 2026-09-12); **`main` at 9cbfd42 measures 3904 + 63**, measured in this
  checkout before the branch was cut, so this branch adds **13**, all in
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
  `ROADMAP.md` are promoted at release time; **141 lines carry one**,
  recounted 2026-09-12 on this branch as
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

**Fifty-two open**, counted from `gh` at the moment of writing with #238
closed by hand, this session's PR open and #245 filed from its measurement,
and **fifty-one once this PR merges and #243 is closed by hand**
(`gh issue list --state open --limit 200`, 2026-09-12; the limit matters, `gh`
pages at 30): #86, #92, #94, #103, #128, #137, #142, #143, #144, #145, #150,
#152, #154, #156, #157, #172, #173, #174, #175, #177, #178, #179, #181, #186,
#196, #197, #200, #201, #204, #207, #209, #210, #212, #214, #215, #217, #221,
#222, #223, #226, #227, #230, #231, #233, #234, #235, #240, #241, #242, #243,
#244, #245. Re-count at the end against `gh`, and again after any review
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
review added five and this session answered one of them** (#243): what is left
is #240 (a sectioned `<fn-group>`'s heading, dropped uncounted), #241 (a
`<graphic>`'s `<alt-text>` welded into the sentence and its `<caption>` filed
as a stray paragraph — a wrong value), #242 (`<inline-graphic>` has no handler,
so a marker deposited as an image is lost with the note unmarked), and #244 (a
`<graphic>` owned by neither an exhibit nor its footnote matter, the `<td>`'s
82 in 8 of 997 first). **#241 is the one to take first**, being the last
*corruption* of the four and the shape #243 turned out to be — its own
measurement is untaken, so size it before pricing it. **#245** is new and is
a modelling decision rather than a loss to stop: an `<array>`'s cells reach
nothing, the drop is counted now, and the two candidate answers both move
stored values. Every one is a decision
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

**The instrument debt of the last five sessions is real and stated.** #224,
#228, #124, #238 and now #243 were all measured from scratch scripts over two
named public artifacts, not from `scripts/sample_jats_exhibits.py`, which
carries a counter for none of them; the scripts go with each session's
scratchpad, and this session rebuilt a deposit survey, a routing tally *and* a
two-checkout comparator from nothing again. Adding the counters is a generation
on that sampler plus a full live redraw of both committed corpora (~50 min,
moving every figure they pin) — a session of its own, and worth weighing
against the package draws being 8,118 and 97,909 articles against the corpora's
997 and 1,000. **The comparator is the piece most worth keeping**: it loads
both checkouts in one process (`sys.meta_path` stripped of the editable
finder, then `sys.path` pointed at a worktree) and compares in place, so the
97,909-article artifact costs no intermediate file, where two dumps would have
been ~3.6 GB a side.

**Provenance is a chain**: almost every open issue was filed by a PR reviewing
an earlier fix (#224 → PR #232 → #228 → PR #236 → #124 → PR #237 → #238 → PR
#239 → #240–#244 → #243's own fix → #245 is the recent run; #224 came from
outside the chain, #228
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
