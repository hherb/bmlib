# HANDOVER — bmlib development

_Last updated: 2026-09-02. **0.10.0 is released and on PyPI**; sixteen changes
sit unreleased once this branch merges — #73's atomic template install (PR #102), #96/#105's
partitioning of an over-cap PubMed day (PRs #106 and #114), #109's typed
article-id (PR #113), #110/#111's JATS sub-article and contributor-group
fixes (PR #118), #115/#116/#117/#131's exhibit nesting, ranking and
sampler (PR #126), #127's image-only table (PR #133),
#123/#125/#130/#135's owner-routed title and caption (PR #136),
#134/#121/#129's end-of-parse audit (PR #139), #120/#140's undivided
contributor name (PR #141), #146/#149's mixed-citation text (PR #148),
#151's mechanised buffer-read invariant (PR #153, adding no behaviour),
#112's funder-matching figures (PR #155), #119's article-only full-text
scan (PR #159), and the JATS corpus redraw answering #132, #138 and #158
(PR #163, merged 2026-09-01, which also took #165-#170 from its own review),
#162's invented exhibit number (PR #171, merged 2026-09-02, filing #172 and
#173), and #147's dropped formulas (this branch). All five version places
agree at 0.10.0. Twelve of the sixteen are `fulltext` JATS fixes filed within
days of each other; whoever cuts the next release should describe them
together. Every unreleased ROADMAP row carries an `*(unreleased)*` marker.

**Most of them move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — so the next release notes owe a data
answer as well as an API one. #111 populates an author list that was empty for
the majority of open-access articles; #115/#117 change `JATSArticle.figures`
and `.tables` (missing figures appear, and roughly half of `graphic_url`
changes from a thumbnail to the full image); #127 fills the new
`JATSTableInfo.graphic_url`; #123/#125/#130 move `body_sections` for roughly
one recent article in ten; #120/#140 collect a contributor whose name arrived
undivided (3.3% of 1,025 articles lost at least one); #129 recovers an article
lost to a malformed `colspan`; #162 stops an exhibit the publisher did not
number being given one, which moves the cached HTML for 83 of every 997 recent
articles; #147 puts a formula back into the prose that contains it, moving
prose and cached HTML for 68 of 880 local articles (433 paragraphs gained,
none lost) and taking a LaTeX preamble out of every table cell that held one.
#146/#149 is the largest and the only one
measured by diffing a corpus rather than reasoned: over 880 local PMC articles
/ 20,770 references, `citation` moves for 4,499 (21.7%) in 191 articles —
3,541 rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for 502
in 14, rendered HTML for 576 in 23.

**Two of the fifteen move stored *transparency* values, and both are outside
`fulltext`.** #112 admits `plc`/`pty` to `_INDUSTRY_WORDS`, so `"GSK plc"` now
sets `industry_funding_detected`, which feeds a HIGH-risk rule and a quality
downgrade; neither token is in the labelled corpus, so **no measured figure
moves**, which is exactly why the omission sat unnoticed — it is a rule-2
inclusion and rule 2 is not a measured claim. #119 stops a reviewer's prose
answering for the article: measured over PMC's `oa_comm` baseline package
`PMC012xxxxxx` (2025-06-26, 97,909 articles), 0.61% of the corpus has at least
one scan output move, dominated by `data_availability_level` (499 of 602).

**PR #155's review reshaped the funder rule block rather than the behaviour**,
and left **#156** and **#157** behind. Membership is now four rules with rule 4
as a veto — without a stated precedence they contradicted each other on five
tokens, which is #112's own shape inside its own fix — and rule 2 is a *prior*,
not proof, its old premise being false. The net gained the three checks the
first cut lacked: a row's `in`/`out` and cited rule are checked against the
tuples, the corpus's own size is asserted, and per-token scoring borrows the
matcher's own `_compile_word_re`. Full argument in `CLAUDE.md` and
`docs/manual/transparency.md`.

**The JATS fixes reach a bmlib path through the cached HTML** — a claim this
file had backwards twice. `_build_html` renders authors, figures, tables and
both section lists into one string that `FullTextService` caches via
`parse_with_html()`. Nothing *structured* is stored, but a downstream holding
cached full text should re-fetch, not only one calling `JATSParser` itself.

**Rules carried forward from PRs #133 through #155**, each argued in full in
`CLAUDE.md` and at its call site, so only the shortest form is kept here.

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; one window is not the rate (#127 read 0 of 662 recent tables and
11 of 93 in a 1996-1998 draw; #119 reads 0.7% of one corpus and 3.45% of
another; the redrawn corpora hold 2,448 `<table-wrap>` and 0). **Measure the
population the code actually reads**, prefer a corpus with a public name over
one on your disk — and check that the *rendition* of the named corpus is the
one the code is fed, which is the half #138 had to learn the hard way. A number in a comment goes
stale silently and coherently — `TestTheCitedPopulationsAreWhatTheCorporaHold`
and `TestTheStatedCountsAreWhatTheCorpusHolds` are the answer. A rule can be
spec-driven and still owe an instrument, and an instrument's vocabulary has to
be open or it certifies (#121). State a blast radius **from a diff, not from
the call graph**: PR #148 reasoned soundly from a false premise, and four
review agents missed what two parses over 880 articles showed in minutes.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding. When a fix extends a routing rule, walk every other path it
reaches — the guard written on one branch is the guard the others need, and
the same rule stated in prose on one branch is not applied on the next. Read
the rules *next to* the one you are adding before calling a fix one line. A
stack of frames needs the entries it will not use. **A set keyed on the
element cannot express a rule about the context** (`_INLINE_ELEMENTS` was
right for #120 and wrong for #146). And **suppressing a merge does not empty a
buffer** — only an accumulating child ever withheld anything.

*Diagnostics and tests.* A diagnostic's *level* is a claim that has to be
measured, and a detector must report what it *checked*, not what it concluded.
A net needs its own false-positive net, and it must be free — the autouse
`parser_log` fixture makes all 186 pre-existing fixtures one. Key a counter on
*structure*, never on the routing it is checking. **A rule enforced by prose is
not enforced** (`TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`), and it demands a *choice*
rather than a field. **Checking the arithmetic is not checking the rule**, and
check the denominator too — every count is a numerator over a corpus that can
be cut away underneath it. Tell a vacuous green from one asserting silence:
**ask which line of the fixture the assertion depends on** — the one #119
mutant that survived died to a one-word fixture change. **Mutate the *old* half
of a condition you extend.** An issue can be closed as COMPLETED without being
fixed — so **diff `gh issue list` against the merged commit's own "filed"
list**. And a mutation harness restoring with `git checkout -- <file>` deletes
whatever is uncommitted in it.

**Three older PRs, in one line each** — argued in full in `CHANGELOG.md`,
`CLAUDE.md` and at their call sites; only what a future session must act on is
kept. **#112** (PR #155, funder figures): eight claims cited a measurement
nothing checked, four wrong together because they matched an uncommitted
corpus revision — *when several figures are wrong at once, look for the one
cause*, and *correct a figure everywhere it is read*; left **#154, #156,
#157**. **#119** (PR #159): a reviewer's prose answered for the article, and
*a text scan over markup can be exact if the argument is closed* — in
well-formed XML a literal `<` opens markup, so comment, CDATA, PI and DOCTYPE
subset are the complete set of exceptions, and *"exact" is a claim the review
tests*; left **#160, #161**. **#151** (PR #153, no behaviour): key a net on
the thing and not the names it has, define an exemption by what a statement
*does*, ask for containment not overlap, and size a positive control as the
whole inventory rather than a canary; left **#152**.


**A closing keyword in prose has closed an issue nobody decided — four
times**, the fourth being the commit that warned about the other three. GitHub
reads *"filed rather than ‹keyword›: ‹number›"* literally and does not care
that the sentence says the opposite, nor that the substring sits in a
quotation, a code span or bold markers. #137 went twice (the second in a PR
body quoting the first to warn about it), #142 was born closed by the commit
that filed it, and #160 was closed by `d362271`, whose body used the very
phrasing this paragraph warns against. So the rule is not "phrase it
carefully" but **never reproduce the substring at all** — describe it, or drop
the `#` — and because the rule was written, read and then broken by one
session, the real check is after the fact: **after every merge that mentions
an issue in prose, diff `gh issue list` against what the commit says it
filed.** That is what found #160 reopened, one session late.


**PR #171 settled #162 by refuting it**, and the whole argument is now in
`docs/DECISIONS.md` and `CHANGELOG.md`. The issue read
`exhibits_with_descendant_label` — an exhibit holding *any* `<label>` below it
— as "carries its own label indirectly", so 6,937 against 6,944 printed
`PREMISE VIOLATED`; fetching all seven showed each is a `<table-wrap>` with no
`<label>` and no `<caption>`, every label below it a `<table-wrap-foot><fn>`
marker or a `<list-item>` bullet, so the proposed fallback would have
corrupted 7 of 7. What it fixed instead is 17× larger: **121 exhibits in 83 of
997 recent articles** carry no label and were given an invented `Figure
{i + 1}` — the *index*, so it collides with a real number. Five lessons,
carried forward because they keep recurring:

- **A count is of what you looked for** — before acting on a counter, read its
  increment site, not its name and not the report.
- **Print only the half of a verdict the instrument supports.** `PREMISE
  VIOLATED` went; the sound direction stayed, since `direct` is a subset of
  `descendant` by construction.
- **A zero over an absent population is not a clean result** — the back-filled
  window carries no `<caption>` at all, so its zero certified nothing.
- **The same rule stated on one branch is not applied on the next** —
  `to_html` had invented no heading since #30 and was inventing figure numbers
  four hundred lines away.
- **An issue's own remedy is a hypothesis.** The fetch that refuted this one
  cost ten minutes.

It left **#172** (`to_html`'s output moved and `FullTextCache` has no version
stamp, so an old rendering is served forever) and **#173** (a figure's `alt`
duplicates its own `<figcaption>`, so AT announces it twice). The
cross-platform spec in `bmlibrarian_lite` still has the old behaviour as
normative pseudocode (that repo's #197).

**This session settled #147: a formula now reaches the prose that contains
it.** `<tex-math>` was taken from its sentence and dropped; `<disp-formula>`
was dropped whole, LaTeX, MathML and the `(1)` prose cross-references. The
rule is **choose one rendition, at the formula element** — the encodings never
merge on their own — and the whole argument is in `docs/DECISIONS.md`,
`CLAUDE.md` and `CHANGELOG.md`. Six things worth carrying forward.

- **The obvious fix was refused by measurement, twice over.** Adding
  `<tex-math>` to `_INLINE_ELEMENTS` prints 1,087 corpus formulas (188,473 in
  the package) twice, since both encodings are deposited; and it would print a
  LaTeX *document*, 99.9% of deposits being `\documentclass` … preamble …
  rather than an expression.
- **The change stayed small because MathML needed no membership at all.** It
  accumulates nothing, so its flattening is already in the formula's own
  buffer — which also means a deposit binding the namespace to a prefix other
  than `mml` keeps today's behaviour instead of hanging on a literal prefix
  match the way #128 does. The first design had it in two sets.
- **A live corruption sat beside the missing content**: a table cell takes its
  text from `characters()`, not a buffer, so the LaTeX preamble was pasted
  into the rendered HTML — 24,476 deposits in 856 of 97,909 articles. No
  buffer rule reaches it; the fix had to hold the cell back too.
- **The first cut of the fix corrupted what it recovered, and only the corpus
  diff showed it.** Printing a merged equation's number gave `'eqn (2):2 τ =
  kn'` — a coefficient the deposit does not contain — and welded consecutive
  equations into `'… + OH−2 Al3+ …'`. The number is now printed only where the
  equation stands apart. Likewise the first cut re-spaced text that already
  reached the prose (`'EndMatrix represents'` → one word), fixed by the
  module's own `_text_with_formatting` rule.
- **Say which population a number is of, again.** 4,377 MathML-first formulas
  is 2.3% of formulas and **37 of 97,909 articles** — a house style, so the
  new sampler counter will read zero on almost any draw and a reader must not
  conclude the order never varies.
- **Blast radius from a diff, not the call graph**: 68 of 880 local articles
  (7.7%) gain 433 paragraphs and lose none; abstracts, tables and citations
  move in zero — where the first cut moved 3, 4 and 0.

`scripts/sample_jats_exhibits.py` gains a `formula routing (#147)` counter
generation for the three populations the fix rests on, so the next redraw
re-derives them instead of leaving them in a throwaway script. **Its first
scoping was wrong in this repo's own signature way** — it counted encoding
order inside `<alternatives>`, where the convention is near-uniform, while the
rule is about the whole formula; the narrower counter would have printed
"order never varies" for the rule that exists because it does not.

It left **#174** (flattened MathML loses its spacing and brackets — `mspace`
has no text so words weld, `mfenced` carries its brackets as attributes, and
`msup` renders `c²` as `c2`; latent for inline formulas before, now reaching
1,915 display formulas in 185 of 997 articles) and **#175** (a formula
deposited as an image has no field to reach it, 140 of those 1,915 — #127's
table fix from the other side, and blocked on the same modelling decision a
`JATSFormulaInfo` would settle). A comment on **#172** records that this is
the second renderer change in one unreleased window.

Also corrected in passing: `JATSTableInfo.graphic_url`'s docstring still cited
the pre-#138 draw (600 articles, 755 tables) and had come to say the opposite
of the committed corpora in both directions. PR #163 reconciled five other
files and not that one, and the mechanised check asserts the corpus against
literals in the test, so it cannot catch prose drifting from it.

(The stale-share correction that preceded #147's fix still stands: the 1,087
both-encoding formulas are 9.8% of the 11,136 the counter walks — 1,915
`<disp-formula>` + 9,221 `<inline-formula>` — never "57% of every
`<disp-formula>`", `_record_formula_alternatives` being called from both arms,
so no per-`<disp-formula>` share is derivable at all.)

**Next up: #164.** **The figure-side
graphic walk is still unscoped, and the argument that this costs nothing is
refuted by the committed corpora (7 nested `<fig>`, three foreign owners).
Fixing it re-measures #117 and moves every cited share, so it is a redraw
rather than a code edit.** It is also where the per-exhibit label-owner counter
belongs: #162's spot-check of seven articles is a live one, and a counter
recording what owns the labels inside an unlabelled exhibit would make it
re-derivable at the next redraw rather than adding a dead field now. Then
**#124, #128, #137, #142–#145, #150 and #152 in `fulltext`, #160 and #161 in
`transparency`, the older non-JATS ones (#86, #92, #94, #103), the funder
corpus (#154, #156, #157), or Phase 3 of the bmlibrarian port, whose every row
needs a design conversation.** Of the populations the redraw delivered,
**#142, #143 and #150 measure empty**, so those three are blocked on a
stratified draw rather than on effort. The funder redraw is still one job
answering three. #160 is the cheapest self-contained fix on the list: the
remedy it prefers — match the element name when the depth closes — needs no
invented constant and no corpus. See "Open GitHub issues" below for which is
blocked on what; almost none is a drive-by, and the ones that lose content are
blocked on a modelling decision rather than on effort.

This file briefs the next session on what is done, what is still open, and the
conventions to keep. Update it whenever a session materially changes the plan;
delete sections that are finished and no longer instructive. Per-PR detail
lives in git history, `CHANGELOG.md` and `docs/plans/` — not here.

## Current state

- **Version 0.10.0, released 2026-08-15 and live on PyPI** (0.4.0 → 0.5.0 →
  0.5.1 → 0.6.0 → 0.7.0 → 0.8.0 → 0.9.0 → 0.9.1 → 0.10.0; `CHANGELOG.md` has
  the dates). The version lives in **five** places — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header, and
  `docs/manual/index.md`'s header line — and all five agree. The fifth was
  missing from this list until 0.10.0 and had gone stale at 0.4.0 for five
  releases; only `bmlib/__init__.py` is guarded by anything but this list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag,
  and they compound for anyone upgrading across them; 0.8.0's largest changes
  the shape of every synced PubMed title and abstract. **0.9.0 moves nothing
  stored.** **0.9.1 moves one thing**: #79 makes Tier 1d take the free PDFs it
  had been discarding, so stored full text is not comparable across the
  upgrade and outbound traffic to Europe PMC rises. **0.10.0 moves nothing
  stored but is not free**: no `download_days` row a previous release wrote is
  durable under #95's rule, so the whole window is re-fetched once. The two
  questions are independent — the version number answers the API question,
  never the data one, and a downstream reading only the number must still read
  this list.
- **Tests: 3074 passing + 63 skipped** (`uv run pytest tests/ -q`, measured
  2026-09-02 on this branch; `main` is 3035 + 63). The PostgreSQL half
  has not been re-run since the SQL last moved; the
  last measured figure with `BMLIB_TEST_POSTGRESQL_DSN` set is 2435 + 2 on the
  #105 branch. Of the 63 default skips, 61 are the PostgreSQL
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
  Treat drift as a regression, not expected staleness. The
  `unreleased` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release time; **67 are outstanding for the next release** — 29 `ROADMAP.md`
  rows and 38 spots across `docs/manual/publications.md` (13), `fulltext.md`
  (18), `templates.md` (3) and `transparency.md` (4). Recounted 2026-09-02 with
  `grep -ic unreleased`; the figure is measured, not maintained, so recount it
  rather than adjusting it. Grep case-insensitively for `unreleased` rather
  than for `(unreleased)`: three of 0.10.0's thirteen were spelled
  `*(unreleased, #99)*` and `(changed, unreleased — …)`, which the
  parenthesised pattern misses. Write the marker bare, never with a guessed
  version number: the number is decided when the release is cut, and 0.10.0
  is a case in point — it moved no API and so read as a patch, while costing
  every installation a re-fetch of its whole window. Markers inside
  `docs/superpowers/plans/` are historical records — leave them alone.
- **`main` is protected by the `protect_main` ruleset** (added 2026-08-13):
  no deletion, no non-fast-forward push, and CodeQL code scanning plus code
  quality required to merge. CodeQL comes from GitHub's *default setup*, so
  there is no workflow file in the repo — and its generated workflow ignores a
  PR's `reopened` action, so a PR predating the setup needs a fresh commit
  rather than a close/reopen. It does **not** constrain the merge strategy.
- **The release tag does not depend on the merge button** (#78, closed
  2026-08-13): `main`'s tip is on `main`'s first-parent line under every
  strategy, so the recipe tags the tip after pulling, guarded by two checks
  (see "Cutting a release"). Squash away.

## Next up

### Open GitHub issues

**Twenty-four open** as this file is written, **twenty-three once this branch
merges and closes #147** (it filed #174 and #175) (verified with `gh issue list` 2026-09-02, after PR
#171 merged, which closed #162 and filed #172 and #173; #165-#170 were closed
on PR #163's merge): #86, #92, #94, #103,
#124, #128, #137, #142, #143, #144, #145, #150, #152, #154, #156, #157,
#160, #161, #164, #172, #173, #174, #175. Every one was
found by review or measurement rather than by a
failing test, and **none of them loses records** — though **#124** loses an
exhibit's footnotes, **#150** renders a note-only reference as an empty
bullet, and **#128** would lose every figure image in a document binding XLink
to another prefix.

**#165-#170 were fixed on PR #163's branch and closed when it merged**; they
stay the permanent handle for why that code looks the way it does, which is
what every `(issue #N)` comment here is. **#164 is the one that PR did not
fix**: scoping the figure-side graphic walk re-measures #117 and moves every
cited share, so it is a redraw rather than a code edit, and the PR corrected
the refuted premise in prose instead.

**Count the open issues against the repo before trusting the number.** The
line has been wrong in three consecutive sessions, and an issue **closed as
COMPLETED without being fixed** is invisible to any such count — that has
happened four times by the closing-keyword mechanism above, once to an issue
the same commit filed. The per-PR provenance chain, since every open issue but
five came out of it (released provenance is in `CHANGELOG.md`): **#73** → PR
#102, filing **#103**; **#96** → PR #106, closed as correct rather than fixed;
**#105**, **#107** → PR #114; **#109** → PR #113; **#110**, **#111** → PR
#118, filing **#119**–**#121**; **#115**–**#117**, **#131** → PR #126, filing
**#123**, **#124**, **#127**–**#130**; **#127** → PR #133, filing **#132**,
**#134**, **#135**; **#123**, **#125**, **#130**, **#135** → PR #136, filing
**#137**, **#138**; **#121**, **#129**, **#134** → PR #139, filing **#140**;
**#120**, **#140** → PR #141, filing **#142**–**#146**; **#146**, **#149** →
PR #148, filing **#147**, **#149**–**#151**; **#151** → PR #153, filing
**#152**; **#112** → PR #155, filing **#154**, **#156**, **#157**; **#119** →
PR #159, filing **#158**, **#160**, **#161**; **#162** → PR #171, filing
**#172**, **#173**; **#147** → this branch, filing **#174** and **#175**.


**#151's own filing is the counter-example to the count above.** PR #148 filed
#149 and fixed it in the same PR, so it never appeared as open work; #152 is
PR #153's equivalent. Neither is lost, but neither is visible in a
"filed minus closed" arithmetic either — read the per-PR list, not the total.

Each issue carries its own argument on GitHub; what follows is only what a
session needs to *choose* between them.

**That corpus redraw is done** — #132, #138 and #158 are answered, and #142,
#143, #147 and #150 each have the population they were waiting for (#147 was
then fixed on it). Both corpora are 997 measured articles of 1,000 at `seed
0`, recent from `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`
(2023-2025) and back-filled from `…PMC002xxxxxx…` (1996-1998). Full argument
in `CLAUDE.md`; four rules survive it.

- **The sample and the bytes come from different places.** A package holds an
  *archive* rendition while `FullTextService` feeds the parser Europe PMC's
  `fullTextXML`, and the two differ on exactly the cited populations —
  `last_is_thumb` differs in 156 of 300 compared articles, and where it
  differs the archive measures 0 against 781 served, so a corpus drawn *and*
  measured from the package reads #117's ranking rule as dead code. **Check
  the rendition before trusting an offline corpus**; it generalises past JATS.
  `tests/data/jats_exhibits.rendition.json` records **disagreements only**, so
  no archive total can be read off it.
- **Every figure moved for three reasons at once** — sample, rendition and a
  scoped walk — so no movement may be attributed to any one, the scoping least
  of all, since it is the only one the corpus records (`unscoped`).
- **Three claims did not survive.** The `<label>` premise reported violated is
  #162, corrected by PR #171. #127's image-only-table population cannot be
  re-measured, the back-filled window holding **0 `<table-wrap>` in 997
  articles** — that its material is scanned page images is an *inference* from
  0 tables beside 627 figures and 3,873 `.png` deposits, and quoting that 0 as
  a measurement would be the defect this work removed. The abstract-branch
  guard's "44 exhibits, none titled" is withdrawn as unre-derivable.
- **#158 is answered by naming the population.** "Carries a region" and "loses
  body text to one" are different claims, the first bounding the second, and
  the re-derivable one: **29 of 997 (2.9%)** recent articles carry a region
  (145 regions), 0 of 997 back-filled, agreeing with `transparency`'s 3,382 of
  97,909 (3.45%) over the same package — across a rendition difference, the
  served bytes *adding* regions in 5 of 300. The 4 of 249 counted peer-review
  deposits and the 288 of 1,022 (28.2%) counted articles losing body text,
  both on draws in no commit. The rate is a per-publisher property, so 28.2%
  could be honest for a PLOS/eLife/BMJ/F1000-weighted draw; the defect was
  that three of the four draws cannot be re-taken.


**#128 is weaker than filed**: all 13,617 `<graphic>` hrefs in the two redrawn
corpora use the `xlink` prefix bound to the XLink namespace, so the
literal-prefix match is safe on measured evidence. Worth downgrading rather
than closing — no sample proves no publisher does otherwise.

**Three issues lose content the document carries, and none is a drive-by** —
each is blocked on a modelling decision, not on effort. **#124**: neither
exhibit model has a `footnotes` field, so a `<table-wrap-foot><fn>`'s
abbreviation expansions and per-table funding notes reach nothing while `<sup>`
is flattened into the cell, and the rendered table reads `12.3a` with the note
it points at existing nowhere; #116's fix discards the marker, right only until
there is something to attach it to. **#150**: a `<ref>` whose only content is a `<note>`
renders as an empty `<li>`, 4 instances in one publisher. **#144**: whether
`<on-behalf-of>` is a name or an attribution.

**#142, #143 and #145 are the rest of PR #141's review**, all in the
contributor and reference half of `jats_parser`. #143 — several `<collab>` in
one `<contrib>`, or a `<name-alternatives>` — is bare last-wins with no parent
test and no log, the #116/#127 class. #142 is a `<collab>`'s
`<institution>`/`<addr-line>` children running together with no separator, and
was the closing-keyword mechanism's third victim. #145 is `<aff>` resolution
through `@id`.

**#137 has been auto-closed twice without being decided** (mechanism above): a
section-level `<caption>`'s `<p>` children still reach `body_sections` while
its `<title>` is dropped, so one caption's halves go different ways. The
sampler records the `<title>`'s parent but not the `<caption>`'s owner, so the
population is not yet derivable.

**#152 — neither half of `<article-id>`'s reachability guard is pinned, and
they are not equivalent.** `parent == "article-meta" or self.in_front` decides
whether the identifier is read at all, and each half deletes on its own with
the whole suite green. For valid markup `<article-meta>` is inside `<front>`,
so the parent test can only fire on markup JATS does not admit, while
`in_front` admits an `<article-id>` deposited anywhere in `<front>` — in
`<notes>`, say — as the article's own DOI. Unmeasured, so it pairs with the
redraw. It matters because this is where #109 was: carefully argued rules
behind an unpinned guard. No behaviour is known to be wrong today.

**#160 and #161 came out of PR #159's own review**, and neither is a live data
loss. **#160 was closed as COMPLETED by that PR's last commit without being
fixed** and is reopened — see the closing-keyword paragraph above. #160: `_strip_nested_articles` documents a well-formed-input contract and
enforces none of it — every lexer branch is quadratic on an unterminated
construct (a truncated 256 kB body lexes in 22.9s against 4-9 ms for a
well-formed 3.4 MB one), and the depth is not matched against the element name,
so an unmatched end tag *inside* a region re-admits the outer round's prose.
0 of 3,880 sampled deposits are malformed, so it needs input expat would
reject; the issue names three remedies and prefers the one needing no invented
constant. #161: a full text that was *served* and then refused is
indistinguishable, in the stored `TransparencyResult`, from one that was never
served — both carry `full text unavailable`, which is false for the refusal —
and the score silently loses up to 30 points on that path, enough to reach HIGH
against the default threshold. That is `publications/`' `FetchResult.note` ->
`SyncReport.notes` argument one module over: permanent *and* invisible is the
pair these rules exist to break up. Both refusal paths measure empty.

**#158 is closed** — the nested-article rate is stated per population at every
site now, and the redraw block above carries the figures. Nothing to do.


**#154, #156 and #157 are one job too, and it is the funder corpus.** #154:
`scripts/sample_funder_names.py` writes `tests/data/funder_names.raw.json`,
which is in no commit, so the repo holds the 417 labelled names and not the 816
they were drawn from — a count over the unlabelled remainder is unanswerable
(`Key Laboratory` was justified at eight and the committed corpus holds two,
consistent and unconfirmable), and a redraw has nothing to diff against. The
old draw is not recoverable, so the fix carries a decision: re-label the
intersection, or commit a fresh draw as the baseline for the *next* comparison
and say so. #156: rule 2's premise was false, so it is a prior; measuring what
it costs needs a draw stratified for European funders, and if the rate is
material `gmbh` re-decides on rule 1 alone, where it scores nothing
(`ltd`/`limited` carry rule 1 either way). #157: `plc` collides with
*phospholipase C* and wants a targeted `\bplc\b` draw, the general one having
found none in 816. Both risks are pinned by
`TestTheKnownFalsePositivesAreKnown`, on the #92 precedent — keep the choice,
file the measurement, do not quote the reasoning as measured. `docs/DECISIONS.md`
requires the sampler be run before either list is touched, so any session
extending a funder list owes #154 first.

**#103 — `install_defaults()` reserves no `NAME_MAX` headroom for the
temporary name.** `atomic_write()` stages through a name 38 characters longer
than the target's, and `templates/engine.py` passes the source filename
through verbatim where `fulltext/cache.py` leaves room, so a template named
beyond ~217 characters fails with `ENAMETOOLONG`. Left alone deliberately: the
names come from the caller's own source tree and the failure is loud. The fix
is a docstring line, not a cap — capping renames a caller's template and
`render("<name>")` then does not find it.

**#94, #92 and #86 are the older non-JATS three.** #94 and #92 are the same
shape — a guard resting on an unmeasured quantity, argued in full in
`CLAUDE.md` under *A completed day is a durable claim*, and **neither may be
tightened without running the sampler it asks for**. #94: one bioRxiv error
body (messages, no `collection`) is indistinguishable from a quiet day, and
the tests deliberately pin *both* possible quiet-day shapes so the guard
cannot come to depend on the unmeasured answer. #92:
`SHORTFALL_FAILURE_RATIO = 0.5` is bmlib's only threshold not set from a
sampled population, and a floor tightened past the real benign gap re-fetches
that day on every later run for ever. #86: `docs/manual/llm.md` documents
`LLMClient.generate` and `LLMClient.embed` twice each — not a delete, since
the copies differ (one `generate` has the example, and the two `embed`
sections disagree on the default model, `embed_batch`'s being right).


### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** — `~/src/bmlibrarian` still pins
  `bmlib[ollama]>=0.5.1,<0.6.0` and has missed six releases; a downstream
  change, not a bmlib one. Read the intervening non-comparable behaviour
  changes first: the transparency ones move stored scores, 0.8.0 moves every
  PubMed title and abstract, and **0.9.1 moves stored full text** (#79). The
  widened pin should clear `FullTextService.cache` being nullable, one of
  0.9.0's three API changes.
- **Wire the segmenter and the rule-based extractors in.** Two halves of one
  roadmap item: the segmenter could give `CochraneAssessor` Methods/Results
  boundaries and `TransparencyAnalyzer` the paper's own Funding/COI/Data
  sections; `quality/extractors.py` is called by no tier. Each needs a design
  conversation.
- **Feed the stored grants to `transparency/`.** `TransparencyAnalyzer` runs
  its own `efetch` per paper to read `<GrantList>`, which `fetch_pubmed`
  already stores at sync time. Reading the table saves that request, but it is
  a scoring change that moves stored values — its own decision, not a quiet
  optimisation.

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
  the parser targets was deprecated in the 2024 API v2 migration). Each is a
  larger subsystem than anything in Phase 2 and needs its own design
  conversation rather than a straight port. Phase 4 (the prompt-driven agent
  family, paper_weight, review building-blocks) follows, reconciled against
  the existing `quality/` and `transparency/` rather than forked.

### The port recipe (repeat it)

1. **TDD, always.** Behaviour tests first (upstream is the spec), watch them
   fail (`ModuleNotFoundError` is the correct red for a new module), then
   port. Bug in a test you wrote? Fix the test, not correct code.
2. **Modernise to bmlib style:** AGPL header, `from __future__ import
   annotations`, lowercase builtin generics, `datetime.UTC`.
3. **Sever app coupling:** injected connections/params instead of
   `get_db_manager()`/`bmlibrarian.config`; optional deps behind
   `try/except ImportError` raising `pip install bmlib[extra]`; LLM calls
   through `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** from the package `__init__.py` `__all__` — and if the module
   needs an extra, through a PEP 562 `__getattr__` rather than eagerly
   (#64: one eager re-export made ten modules unimportable on a core install).
5. **Verify** (tests + both ruff commands + mypy), **record** in
   `CHANGELOG.md` under `[Unreleased]`, and **reconcile rather than fork** —
   where a port overlaps existing bmlib, build on the existing module.
6. **Read the spec on both sides; do not decide by eye.** Row 11's reviews
   found this three times. Reading someone's XML, check their DTD:
   `<Affiliation>` looks like a leaf, is declared `(%text;)*`, and a bare
   `.text` silently dropped rows. Declaring an output format, you owe that
   format's rules for *every* value, not only the ones carrying markup: having
   decided titles were Markdown, the fetcher neither escaped the prose it
   wrapped nor checked that `<u>` had a Markdown spelling not already
   `<b>`'s.

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
  extra with its settings in `pyproject.toml`. Give it no arguments — the bare
  command is what the `types` CI job runs — and run it in the dev venv: every
  extra but psycopg2 ships its own `py.typed` (that one via `types-psycopg2`),
  so against a bare interpreter mypy reports the optional imports *and
  `jinja2`, a core dependency*, as missing stubs — which is why #81 opened
  claiming 24 errors when there were 22. Anything deliberately unchecked is an
  inline `# type: ignore[code]` with its reason at the site, never a
  per-module `ignore_missing_imports`: `warn_unused_ignores` reports the first
  when it goes stale and can never report the second.
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
  **The number is a claim about the API, not about the data**, so state the
  data answer in prose every time. Three shapes, all real: 0.9.0 was renumbered
  from 0.8.1 in review (API moved, nothing stored did); 0.9.1 is a patch that
  moves stored full text (#79); 0.10.0 is a minor bump moving nothing stored
  whose real cost is a one-off re-fetch of the whole sync window.
  After CI **and CodeQL** are green — the `protect_main` ruleset requires the
  scan, and a release PR is subject to it like any other — merge it with any
  button, then **tag `main`'s tip rather than a particular commit** (#78):

  ```bash
  git checkout main && git pull --ff-only
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || exit 1
  grep -q '__version__ = "X.Y.Z"' bmlib/__init__.py || exit 1
  git tag -a vX.Y.Z -m "bmlib X.Y.Z" && git push origin vX.Y.Z
  ```

  The two checks are the whole point: the first catches a stale local `main`,
  the second catches tagging a commit that does not carry the version — the
  failure `release.yml` would otherwise find *after* the release is public.
  Verifying the tag afterwards needs one piece of git arcana: these are
  **annotated** tags, so `git rev-parse vX.Y.Z` returns the tag object's SHA
  and must be dereferenced — `git rev-parse 'vX.Y.Z^{commit}'` (verified both
  ways on v0.9.1). Then create the GitHub release, which is
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
