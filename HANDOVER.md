# HANDOVER — bmlib development

_Last updated: 2026-08-31. **0.10.0 is released and on PyPI**; thirteen changes
sit unreleased on `main` — #73's atomic template install (PR #102), #96/#105's
partitioning of an over-cap PubMed day (PRs #106 and #114), #109's typed
article-id (PR #113), #110/#111's JATS sub-article and contributor-group
fixes (PR #118), #115/#116/#117/#131's exhibit nesting, ranking and
sampler (PR #126), #127's image-only table (PR #133),
#123/#125/#130/#135's owner-routed title and caption (PR #136),
#134/#121/#129's end-of-parse audit (PR #139), #120/#140's undivided
contributor name (PR #141), #146/#149's mixed-citation text (PR #148),
#151's mechanised buffer-read invariant (PR #153, adding no behaviour),
#112's funder-matching figures (PR #155), and #119's article-only full-text
scan (PR #159, merged 2026-08-30). All thirteen are on `main` and the working
tree is clean. All five version places agree at
0.10.0. Nine of the thirteen are `fulltext` JATS fixes filed within days of
each other; whoever cuts the next release should describe them together. Every
unreleased ROADMAP row carries an `*(unreleased)*` marker.

**Most of them move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — so the next release notes owe a data
answer as well as an API one. #111 populates an author list that was empty for
the majority of open-access articles; #115/#117 change `JATSArticle.figures`
and `.tables` (missing figures appear, and roughly half of `graphic_url`
changes from a thumbnail to the full image); #127 fills the new
`JATSTableInfo.graphic_url`; #123/#125/#130 move `body_sections` for roughly
one recent article in ten; #120/#140 collect a contributor whose name arrived
undivided (3.3% of 1,025 articles lost at least one); #129 recovers an article
lost to a malformed `colspan`. #146/#149 is the largest and the only one
measured by diffing a corpus rather than reasoned: over 880 local PMC articles
/ 20,770 references, `citation` moves for 4,499 (21.7%) in 191 articles —
3,541 rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for 502
in 14, rendered HTML for 576 in 23.

**Two of the thirteen move stored *transparency* values, and both are outside
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
another; the redrawn corpora hold 2,363 `<table-wrap>` and 0). **Measure the
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

**The last session settled #112** — the funder-matching figures, corrected and
then made answerable to the corpus. Eight claims in `analyzer.py`, the manual
and the test file gave a measurement as the reason for a token's inclusion, and
nothing checked *them*; the issue had found three. Four rules came out of it
that outlive the fix.

- **When several figures are wrong together, look for the one cause.** All four
  headline numbers were self-consistent with a corpus holding 34 industry names
  against the committed 30 — one uncommitted revision, not four slips.
- **Correct a figure everywhere it is read**, not where the issue points: every
  stale figure had been copied into `docs/manual/transparency.md`, the copy a
  downstream consults.
- **A test can take the comment as its input rather than a copy of it**, and
  should **derive both sides of a comparison** or the older half goes stale
  alone. The cost is a format contract, written at both ends.
- **A stated rule that is not the applied rule is a defect with no behaviour
  wrong** — and a corrected figure can be *uncheckable* rather than wrong,
  which is #154.

**PR #159 settled #119** — a reviewer's prose answered for the article.
`TransparencyAnalyzer` never consumes `JATSParser` output: it fetches
`fullTextXML` itself and regexes the raw string, so every `<sub-article>` /
`<response>` region was scanned as the article's own. The regions are now
removed in `_fetch_europepmc_fulltext`, the one door the full text enters
through. Full argument in `CLAUDE.md`, the manual and the CHANGELOG; five
things worth carrying forward.

- **Measure the population you actually read.** The obvious corpus — the 880
  Europe PMC articles PR #148 diffed against — puts nested articles at 6 of 876
  (0.7%) and moves *nothing*, which reads as "this defect is theoretical".
  PMC's `oa_comm` baseline package over 97,909 articles puts carriers at 3.45%
  and moves a scan output for 602 of them. Same defect, two windows, opposite conclusions.
- **Prefer a corpus with a name over a corpus on your disk.** The figures above
  are re-derivable by anyone from `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`,
  which is the cheap half of what #132 and #154 were asking for. The Europe PMC
  draw is not, and the discrepancy it exposed is **#158** — both of which the
  JATS corpus redraw on this branch has since done, drawing the identifiers
  from that same package. It also found the half this lesson does not cover:
  the package's *bytes* are an archive rendition and not what the parser is
  fed, so naming the artifact is necessary and not sufficient.
- **A text scan over markup can be exact, if the argument is closed.** In
  well-formed XML a literal `<` can only open markup, so comment, CDATA,
  processing instruction and DOCTYPE internal subset are the *complete* set of
  places `<sub-article` is not a start tag — an argument, not a list of hazards
  someone thought of. Say which population each guard has, and *run* it: the
  imbalance path measures empty over all 97,909 articles, while the comment
  token fires on 3 real deposits.
- **A count is of what you grepped for, not of what the code does.** The first
  cut of this fix said `<response>` measured 0; that was a grep for
  `<sub-article`. Running the lexer over the *whole* archive rather than over
  the carriers found 5 more articles changed than any claim accounted for —
  genuine top-level `<response response-type="reply">` deposits. Rare, not
  absent, and the difference is the whole argument for keeping it.
- **Ask which line of the fixture the assertion depends on.** Eleven mutants,
  ten caught; the survivor was the doctype token, and the fault was the
  fixture — its entity held a *self-closing* `<sub-article/>`, which another
  rule already refuses, so the test passed whether or not the doctype was lexed
  at all. Review found the same shape twice more: the comment, CDATA and PI
  fixtures each asserted `"Ours" in stripped`, and each killed its mutant by
  `TypeError` rather than by content, because the fixture's element was
  *unbalanced* — on the balanced shape Springer actually deposits, the mutant
  lives. Exact equality, which one test in the class already used, is the fix.
- **"Exact" is a claim, and the review is where it gets tested.** The lexer
  argued its own completeness from "a literal `<` can only open markup". True,
  and the converse does not hold for `>`, which is legal unescaped in an
  attribute value and a system literal, nor for `]` in an entity's replacement
  text — so a well-formed `<sub-article specific-use="a>b"/>` and a subset
  carrying `]` each cost the article its whole full text. Fail-closed, measured
  0 in both corpora, and still a refusal on a document the publisher deposited
  correctly. Both branches now step over quoted literals, and both shapes have
  a test. Where the argument survived intact it was scoped instead: an
  unmatched *end* tag is harmless only **at depth 0**, the depth not being
  matched against the element name.

**PR #153 settled #151** (no behaviour) — the prospective half of
`_inside_mixed_citation`, mechanised as
`TestOnlyAnAccumulatingElementReadsTheBuffer`. Five rules, argued in full in
`CLAUDE.md`: key a net on the thing and not on the names it currently has;
define an exemption by what a statement *does*, not by what it binds; ask for
containment, not overlap; a positive control sized as a canary decays, so it
carries the whole inventory as a floor; and a control must not be judged
against production data it does not own. That session also measured
`<article-id>`'s reachability guard deciding *nought* tests, filed as **#152**.

**A closing keyword in prose closed an issue nobody decided — four times, and
the fourth was the commit warning about the other three.** A commit body saying
*"filed rather than ‹keyword›: ‹number›"* is read
literally by GitHub, which closes the issue seconds after the merge and does
not care that the sentence says the opposite, nor that the substring sits in a
quotation, a blockquote, a code span or bold markers. #137 went that way twice
— the second time in a PR body *quoting the first in order to warn about it* —
and #142 the third, closed by the commit that *filed* it, so it was born
closed and appeared in no count of what that session left open. The fourth is
**#160**, closed as COMPLETED by `d362271`, whose body announces *two findings
filed rather than ‹keyword›* and then names them — the very phrasing this
paragraph had already been written to warn against. It was reopened at the
start of the following session; nothing in the merged branch touches it.

So the rule is not "phrase it carefully" but **never reproduce the substring
at all** — describe it, or write the number without its `#`, in commit
messages, PR bodies and any quotation of either, which is why this paragraph
names neither. **The rule is not enough on its own** — it was written, read and
then broken by the same session — so the check is the one that catches it after
the fact: after every merge that mentions an issue in prose, diff
`gh issue list` against the commit's own list of what it filed. That check is
what found #160 reopened here, one session late.

**Next up: #124, #128, #137, #138, #142–#145, #147, #150 and #152 in
`fulltext`, #160 and #161 in `transparency`, #132 and #158, the older non-JATS
ones (#86, #92, #94, #103), the
funder corpus (#154, #156, #157), or Phase 3 of the bmlibrarian port, whose
every row needs a design conversation.** The JATS corpus redraw is **done on
this branch** — #132, #138 and #158 are answered and #142, #143, #147 and #150
have their populations — so what is left of that group is the rules those
populations were for, plus the new `<label>`-premise finding below. The funder
redraw is still one job answering three. #160 is the cheapest self-contained fix on the list: the
remedy it prefers — match the element name when the depth closes — needs no
invented constant and no corpus. See "Open GitHub issues" below for which is blocked on
what; almost none is a drive-by, and the ones that lose content are blocked on
a modelling decision rather than on effort.

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
- **Tests: 2837 passing + 63 skipped on `main`** (`uv run pytest tests/ -q`,
  measured 2026-08-31, after PR #159) — 32 of them are
  `TestANestedArticleIsNotThisArticles` in `test_transparency.py`, on the lexer
  and on what the scans then see. The PostgreSQL half
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
  release time; **60 are outstanding for the next release** — 25 `ROADMAP.md`
  rows and 35 spots across `docs/manual/publications.md` (13), `fulltext.md`
  (15), `templates.md` (3) and `transparency.md` (4). Recounted 2026-08-31 with
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

**Twenty-two open** as this file is written (verified with `gh issue list`
2026-08-31, after PR #159 merged, after #119 was closed by hand — the PR
carried no closing keyword, deliberately — and after #160 was reopened, having
been closed as COMPLETED without being fixed): #86, #92, #94, #103, #124, #128,
#132, #137, #138, #142, #143, #144, #145, #147, #150, #152, #154, #156, #157,
#158, #160, #161. Every one was found by review or measurement rather than by a
failing test, and **none of them loses records** — though **#124** loses an
exhibit's footnotes, **#147** loses a formula from the prose that contains it,
**#150** renders a note-only reference as an empty bullet, and **#128** would
lose every figure image in a document binding XLink to another prefix. Count
this against the repo before trusting it: the line has been wrong in three
consecutive sessions, and one further way is an issue **closed as COMPLETED
without being fixed**, which no count of open issues catches — that has now
happened four times by the mechanism described below, the third victim being
an issue the same commit *filed* and the fourth being #160, closed by the very
commit that documented the mechanism. (The chain of what is unreleased, since
every open issue but five came out of it — released provenance is in
`CHANGELOG.md`. **#73** → PR #102, filing **#103**. **#96** → PR #106, closed
as correct rather than fixed. **#105**, **#107** → PR #114. **#109** → PR
#113. **#110**, **#111** → PR #118, filing **#119**–**#121**. **#115**–**#117**,
**#131** → PR #126, filing **#123**, **#124**, **#127**–**#130**. **#127** →
PR #133, filing **#132**, **#134**, **#135**. **#123**, **#125**, **#130**,
**#135** → PR #136, filing **#137**, **#138**. **#121**, **#129**, **#134** →
PR #139, filing **#140**. **#120**, **#140** → PR #141, filing
**#142**–**#146**. **#146**, **#149** → PR #148, filing **#147**,
**#149**–**#151**. **#151** → PR #153, filing **#152**. **#112** → PR #155,
filing **#154**, **#156** and **#157**. **#119** → PR #159, filing **#158**,
**#160** and **#161**.)

**#151's own filing is the counter-example to the count above.** PR #148 filed
#149 and fixed it in the same PR, so it never appeared as open work; #152 is
PR #153's equivalent. Neither is lost, but neither is visible in a
"filed minus closed" arithmetic either — read the per-PR list, not the total.

Each issue carries its own argument on GitHub; what follows is only what a
session needs to *choose* between them.

**That corpus redraw is done** — #132, #138 and #158 are answered, and #142,
#143, #147 and #150 each have the population they were waiting for. What it
cost and what it found is worth carrying forward.

*The sample and the bytes had to come from different places.* The plan was to
draw both windows from a PMC OA baseline package so a reader could re-derive
the identifier list from `(packages, window, target, seed)`. Midway the plan's
own instrument disproved its premise: a package holds an **archive** rendition
while `FullTextService` feeds the parser Europe PMC's `fullTextXML`, and the
two differ on exactly the populations being cited — `last_is_thumb` **differs
in 153 of 294 compared articles, and where it differs the archive measures 0
against 641 served**. A corpus drawn *and* measured from the package would
have read #117's whole ranking rule as dead code. So the sample is
package-defined and deterministic and the bytes are Europe PMC's
(`--measure-europepmc`), with `tests/data/jats_exhibits.rendition.json` as the
committed evidence. **Check the rendition before trusting an offline corpus**
is the transferable lesson, and it generalises past JATS.

*And the second lesson is the first one's own rule, which this branch broke
while writing it down.* The first account of that finding said "archive 0
against 641 over 294 identifiers" and attached a mechanism: the archive
deposits one bare `<graphic xlink:href="…-g001">` per figure where Europe PMC
synthesises an `.jpg`/`.gif` pair. Both halves overreach.
`rendition_delta` records a field **only where the renditions disagree**, so
summing deltas gives a sum over disagreements and the archive's total over all
294 is not in the artifact at all; and `PMC12169732`, in that same file,
deposits its own four thumbnails as `specific-use="thumbnail"` where Europe
PMC re-labels them `content-type="thumb"`, both measuring four — so there is
no one mechanism to name. **A count is of what you looked for, not of what
exists**, and it was the person writing that rule down who got it wrong. The
finding is decisive either way; only the statement was too big.

*Every figure moved for three reasons at once* — a different sample, a
different rendition and a scoped walk — so no movement may be attributed to
any one of them, the scoping least of all, since it is the only one whose
effect the corpus records (`unscoped`).
Both corpora are 997 measured articles of 1,000 at `seed 0`, recent from
`oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` (2023-2025) and
back-filled from `…PMC002xxxxxx…` (1996-1998).

*Three claims did not survive, and one of them is a defect.* The `<label>`
direct-child premise is **violated** on the served rendition — 6,692 of 6,699,
so 7 exhibits in 4 articles lose their label to an invented `Figure {i + 1}`,
which is #116's own symptom from the other side. Three earlier draws measured
it full and none is re-derivable, so this is real and not an archive artifact;
it has a ROADMAP row and wants a decision about a fallback, not a prose repair.
#127's image-only-table population cannot be re-measured — the redrawn
back-filled window holds **0 `<table-wrap>` in 997 articles**, `oa_comm`'s
1996-98 material being, by inference from 0 tables beside 627 figures and
3,873 `.png` deposits rather than by any counter, scanned page images — so its
evidence (11 of 93) is
historical, and quoting the 0 as a measurement would be the exact defect this
work removed. And the abstract-branch guard's "44 exhibits, none titled" was an
ad-hoc walk over the replaced draws with no counter in the sampler, so it is
withdrawn as unre-derivable rather than restated.

*#158 is answered by naming the population.* "Carries a region" and "loses body
text to one" are different claims, the first bounding the second. The
re-derivable figure is the first: **25 of 997 (2.5%)** recent articles carry a
nested-article region (141 regions), 0 of 997 back-filled, agreeing with
`transparency`'s 3,382 of 97,909 (3.45%) over the same `oa_comm` package. The 4
of 249 (1.6%) counted peer-review deposits and the 288 of 1,022 (28.2%) counted
articles losing body text, both on draws that are in no commit. All four sites
now say which population they are of.

**#128 is weaker than filed**: all 13,008 `<graphic>` hrefs in the two redrawn
corpora use the `xlink` prefix bound to the XLink namespace, so the
literal-prefix match is safe on measured evidence. Worth downgrading rather
than closing — no sample proves no publisher does otherwise.

**Four issues lose content the document carries, and none is a drive-by** —
each is blocked on a modelling decision, not on effort. **#124**: neither
exhibit model has a `footnotes` field, so a `<table-wrap-foot><fn>`'s
abbreviation expansions and per-table funding notes reach nothing while `<sup>`
is flattened into the cell, and the rendered table reads `12.3a` with the note
it points at existing nowhere; #116's fix discards the marker, right only until
there is something to attach it to. **#147**: a `<tex-math>` is dropped from
the prose containing it and a `<disp-formula>` from the article outright —
deliberately not one more `_INLINE_ELEMENTS` member, since raw LaTeX leaves a
reader nothing to tell it was markup and an `<alternatives>` pair would emit
twice; scoped to prose outside a citation, a path that measures 0 of 10,671
`<mixed-citation>`. **#150**: a `<ref>` whose only content is a `<note>`
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

**#158 — answered by naming the population, not by picking a number.** The
nested-article rate was cited four times and the figures disagreed by 8x, and
the reason is that they were rates of different things. `jats_parser` said 4 in
249 (1.6%) for *peer-review deposits*; the manual and the CHANGELOG said 288 of
1,022 (28.2%) for articles *losing body text*; #119 measured 3,382 of 97,909
(3.45%) *carrying a region at all*, which bounds the second, since an article
can only lose body text to a region it carries. (Not the converse: a
`<sub-article>` carrying `<front-stub>` and no `<body>` — Europe PMC's injected
`associated-data` block among them — costs the article nothing.) A fourth was 6
of 876 from the Europe PMC draw above. Each site now says which claim it makes,
and the carrier rate is re-derivable from the repo at **25 of 997 (2.5%)** in
the recent corpus and 0 of 997 back-filled — an interval overlapping #119's
3.45% over the same `oa_comm` package, though across a rendition difference
rather than as one measurement: #119 counts archive bytes, the sampler the
served `fullTextXML`, which *adds* regions in 5 of 294 compared articles. The
carrier rate bounds "loses body text" and says nothing about peer review. The rate genuinely is a per-publisher property,
which is why 28.2% could be honest for a draw weighted to PLOS/eLife/BMJ/F1000;
what made it a defect was that three of the four draws are in no commit.

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

**#94 — bioRxiv's envelope shapes are unmeasured**, filed for the reason #92
was: its guard rests on an unmeasured quantity. Full argument in CLAUDE.md
under "A completed day is a durable claim" — one error body (messages, no
`collection`) stays indistinguishable from a quiet day, and the sampler this
issue asks for would measure that and the `messages[0].status` vocabulary.
**Do not tighten the guard without running it**; the tests pin *both* possible
quiet-day shapes so it cannot come to depend on the unmeasured answer.

**#92 — the shortfall floor is unmeasured**, filed as part of the #88 fix so
that its one guessed constant is on the record.
`SHORTFALL_FAILURE_RATIO = 0.5` is the only threshold in bmlib not set from a
sampled population; full argument in `CLAUDE.md`. Two constraints on measuring
it: a `failed` day is re-offered on **every** later run, so a floor tightened
past the real benign gap re-fetches that day forever, and OpenAlex is
expensive to sample. Follow the `scripts/` sampler convention.

**#86 — `docs/manual/llm.md` documents `LLMClient.generate` and
`LLMClient.embed` twice each** (found for #81; same defect as #31). Not a
delete: the copies differ, so merging is a judgement about which prose
survives — one `generate` has the example, and the two `embed` sections
disagree on the default model (`embed_batch`'s is right).

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
