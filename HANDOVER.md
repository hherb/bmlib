# HANDOVER — bmlib development

_Last updated: 2026-09-17. **0.10.0 is released and on PyPI**; forty-four
changes sit unreleased, three of them instrument-only. All five version places
agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Forty-four changes, twenty-seven of them `fulltext` JATS fixes filed within
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
one calling `JATSParser` itself. Ten of them ride on one re-fetch and are the
largest by population, each diffed against `main`; a served figure is over the
8,118 articles of `PMC10030002_PMC10040000.xml.gz` unless another artifact is
named:

- **#224** — unsectioned `<back>` prose (`<ack>`, `<notes>`, `<fn-group>`,
  `<app>`, `<glossary>`, `<bio>`) used to be dropped. Prose moves in 5,990
  articles (73.8%), every move an insertion: 40,342 paragraphs, 0 lost.
  `has_body`, `figures`, `.tables`, `references` and `abstract_sections` move
  in **0**.
- **#265** — nothing read `<elocation-id>`. **New fields** `JATSArticle` /
  `JATSReferenceInfo.elocation_id`, printed where there is no page range. HTML
  moves in **5,399 (66.5%)**, archive 85,887 (87.7%); no other field moves.
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
- **#254/#259/#152** — the article's own `title`, `volume`, `issue`, `pages`
  were written by a `<related-article>`, a `<product>` or a citation in
  abstract prose nested in `<article-meta>`, so corrections, editorials and
  retraction notices carried the related or retracted paper's title (and a
  Wiley notice its volume and issue). A **wrong value, not a drop**: `title`
  moves in **95** served, **1,115** archive and **529 of 3,028** `PMC000xxxxxx`
  articles; `volume`/`issue`/`pages` in 2/0/2 served and 171/145/84 archive
  (58 of those pages now blank where the article has no `<fpage>`). No other
  field moves; the `<h1>` and journal line of the cached HTML do.
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
- **#268** — a `<mixed-citation>` tagging one structured component rendered
  that component in place of its whole deposited `citation` (a bare `(2023)`
  for an IRENA report). **A wrong value in the rendered reference list**, so in
  the cached HTML: 828 references in **346 articles (4.3%)** served, 15,748 in
  5,573 of 97,909 archive, 9 in 6 of 3,028 `PMC000xxxxxx` and 7,691 in 1,054 of
  27,515 `PMC001xxxxxx`. **A downstream re-fetching cached HTML wants the
  HTML's own population, which is larger** — 347 served and 5,578 archive
  articles, the extra 1 and 5 being references whose model value does not move
  and whose decoration does. No other field of `JATSArticle` moves. Three of
  the 24,276 get the same information less tidily rather than more of it, and a
  `doi`-only reference gives up its `<a href>` (96 served / 3,157 archive).
- **#261** — the article's `year` was the first `<pub-date>` deposited
  whatever its type, so PMC's `nihms-submitted` (a manuscript reaching NIH)
  and `pmc-release` (an embargo lifting) could be the stored year. A **wrong
  value**: it moves in **183 (2.3%)** served and 456 of 97,909 archive
  articles, 0 in the two back-filled packages, and to blank in none; no other
  field moves. **#272** — an empty repeated `<fpage>`/`<volume>`/`<issue>` no
  longer blanks the article's value; measured 0, so it moves nothing.

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

The rest, briefly (per-issue argument in `CHANGELOG.md`). **#203 has the
widest population**: `risk_indicators` moves for every closed-access paper,
so **a downstream string-matching *"COI disclosure status unknown (full text
unavailable)"* or its two siblings breaks** — read `full_text_status`.
**#161/#198** add fields (`full_text_status`, `trial_results_status`), so a
downstream pinned to an older bmlib raises out of `from_dict` on a new row.
**#193**, **#187/#190/#191**, **#188** and **#206** move a status field and
sometimes one indicator string; **#195** swaps one CT.gov indicator for
another; **#112** flips `industry_funding_detected` for a `"… plc"` funder;
**#119** moves a scan output for 0.61% of 97,909 articles; **#199** costs 5
points for a paper whose `cited_by_count` is malformed. **#160**, **#183**,
**#202** and **#218** move nothing measurable.

## Rules carried forward

Each is argued in full in `CLAUDE.md`, `docs/DECISIONS.md` and at its call
site; only the shortest form is kept here, because these are what a session
gets wrong again rather than what it can look up.

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; one window is not the rate (#127 read 0 of 662 recent tables and 11
of 93 in a 1996-1998 draw). **An issue's own remedy is a hypothesis too** —
#162's cost ten minutes to refute, #183's was refuted by 1,750 articles ending
in a legal trailing comment. **Measure the population the code actually
reads**; prefer a corpus with a public name over one on your disk, and check
that its *rendition* is the one the code is fed (#138). **A live Europe PMC
draw must be stratified by source and publication year**, a cursor page being a
contiguous block of accessions. **Run one live probe at a time** — the per-host
pacer is per-process (#179). **A share is of a denominator, and the rendition
chooses the denominator** (#164). **Probe the contract, not the expression the
reporter noticed** (#199). **State a blast radius from a diff, not from the
call graph** — and the diff's own predicate is a claim to check: prefix where
the honest test was *subsequence* (#224), a `difflib` opcode walk aligning
arbitrarily over a list whose every member changed (#243). **Load both
checkouts in one process** where a corpus makes two dumps expensive, after
validating that comparator on the smaller artifact. **The harness that produces
a blast radius is itself an instrument**, and **a gap between two of bmlib's
own counts is a defect in one of them until it is explained** — reconcile a
routing tally against the diff **per article**, not in total: #230's served gap
of 4 was one article's four empty paragraphs, and the total hid which. **A
mirror over the markup and a tally from the routing are different instruments,
and where they disagree the routing is the finding**; where the code has a
predicate, run the code, and **measure a drop at the drop** — #230's instrument
used the parser's own predicates and checked every classified run against a
before/after fingerprint of every destination, which is what made "0
mismatches" a result rather than an assumption. **Separate what is already
filed from what is lost**: 21,225 served `<p>` in table cells fall past
`_append_prose` too, and `characters()` has filed every one. **A blast radius
must show nothing was lost, not only that the new value appeared** (#265's
first diff missed a lost citation that way). **Assert the number a log line prints,
not that it printed.** **A container you describe in prose is a claim too.**
**A committed corpus is not the only honest population**, but nothing in the
suite re-derives the two named artifacts — state the trade. **A count that
reads as a population but is a subset is the recurring mis-statement** (#274's
1,047 row quoted as the counter's 1,105 in five files). **An issue's published
table is an instrument's output too**: reproduce it before quoting it, and say
which columns reproduced — #268's served and `PMC001xxxxxx` columns reproduced
exactly and its archive column did not, which is a fact about two scripts and
not about bmlib. **Where a measured distribution is smooth, a threshold on it
is an unmeasured constant** — that is what refused #268's coverage test rather
than taste. **A survey can
refuse part of a remedy**, not only size it, **and it can find the issue beside
the one you took**: #230's tally surfaced #234's empty headings (already filed)
and #253.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding — and **a guard you add can widen the defect next door**:
#272's empty-`<fpage>` guard kept `pages` non-empty, which re-admitted the
`100-101-201` range an `<lpage>` appends to a closed one, so a blank became a
corruption. Ask what the *old* behaviour was accidentally hiding. **A guard whose reason moves needs its comment moved with
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
**A count passed to a shared rule is a claim as much as a list of field names
is** — mechanise the call site, not only the rule (#268).
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
mutant is not an untested guard** — and **an equivalence is a claim about the
code around the flag, so a later commit to that code re-opens it**: `in_body`
in `_prose_reaches_output`'s section conjunction is equivalent because the
fallback answers it too, and `in_front` was, until the next commit gave the
fallback a `<ref-list>` test for `<front>`; the claim rode into seven documents
unre-run, and four of PR #256's five reviewers independently found the mutant
losing an `<attrib>` with every test green. **Run a control mutant on the neighbouring flag you
did not touch**: #230's `in_back` control survived, a pre-existing guard nothing
pinned. **A nested fixture is the only one that sets two container flags at
once**, so it is the only one that can pin an order. **A contract net is blind
to a value read *wrongly* without raising** — assert what the run
*concluded*. **Pick the fixture that separates the guard from its own mutant**;
**a malformed fixture reads as a measurement**. **A measured *majority* is an
argument against a diagnostic** (#235) and **a measured-empty population is an
argument for closing an issue, not for building it** (#204, #207, #210).
**Run a correctness review and a claims review before the PR.**

*Owner rules.* **Read the Tag Library's "May be contained in" for every element
an owner rule names** — #254's first cut refused `<volume-issue-group>` and
`<pub-date><string-date>`, both the article's own and both legal, and passed
every test because no artifact deposits either. **Check old DTDs too**: NLM 2.x
deposits `<journal-title>` bare, the majority spelling in the back-files, which
neither named artifact contains. **A guard's *mechanism* is a claim**: the
nested-article guard was written up as keeping a round's values off the
article, when `characters()` is suppressed there and what it stops is the
closes *blanking* the article's fields. **A comment's measured count goes stale
when neighbouring tests change** — re-measure `main` as well as the branch
(the pop comment's 58/65 were 179/191 on `main` already). **A root anchor and a
suffix differ only under a wrapper**, and NCBI efetch sends one.

*Live behaviour.* **A remote's error shape is a property of the *request*, not
of the endpoint** (#218). **A property only a real remote can refute needs a
real probe** (#194). **A sampler must address *and head* requests exactly as
the code does.**

*Instruments.* **Derive a comparator's field list from `dataclasses.fields`,
and print what it could not compare in the headline** — the first cut of this
session's reported "moved: 19" beside 6,870 articles it had silently failed on
(one wrong attribute name). **Validate it by reproducing a published column**
before believing its zeroes (PR #263's title 95 / volume 2 / pages 2).
**Two instruments agreeing to the article** — a markup survey and a routing
diff — is the strongest form of a population claim. **A zero in a second
window has a cause, and it is not always the obvious one**: the back-files'
zero here is not that a refused date is never first (it is, 111 times) but
that the next date states the same year. **A share is of the population the
counter counts**, which is not always the row you quoted: this counter counts
refusals *before* a year is found, so 1,105 articles, not 3,739 deposits.
**A mutant can be wrong** — one this session was placed below the suppression
it meant to defeat, and a control's pattern had moved with the fix, so both
read as evidence they were not. **A list an instrument declares must be
derived from the code it measures, not restated.** **One declared list can hide two rules.** **Where
an instrument is wider than the code, say so at the site and bound the cost.**
**A guard on the page cannot see a loss one level down** (#212). **Do not
background a mutation sweep beside anything that reads the same checkout** —
commit first, restore from the held string *and* a disk backup, clear
`__pycache__` after each restore. **A `ProcessPoolExecutor` script needs its
`__main__` guard** on macOS, where workers spawn and re-import it.

*Cost.* **A test that pins a decision is reversed, not deleted, when the
decision is** (#206, and two rows of #265's per-field test under #268). **The cost of a schema addition is not a constant** —
ask what the batch already costs (#198). **Check before pricing**: #124's
issue priced a `to_dict`/`from_dict` pair neither exhibit model has.

*Process.* **A closing keyword next to an issue number closes it, quotation or
not** — never reproduce the substring outside a PR body meant to close; describe
it or drop the `#`. **After every merge, diff `gh issue list` against what the
commit says it filed and fixed**, both ways. **Check the ROADMAP for an issue
filed beside yours**: #234 had been open for three sessions on the exact shape
#230's survey turned up, with the remedy already written.

## Previous sessions

**PR #256** (#230, #234, merged 2026-09-14): front-matter prose routes into
`body_sections` ahead of the body. **PR #263** (#254, #259, #152, merged
2026-09-15): the article's own metadata arms test an exact owner path
(`_owned_by`); its reviews filed #261 and #264-#267. **PR #269** (#265, merged
2026-09-15): the article's and each reference's `<elocation-id>` is stored and
rendered where there is no page range; its reviews found five defects in the
first cut and three more on the branch, and filed #268 and #270-#272. **PR
#274** (#261, #272, merged 2026-09-16): `year` is the first `<pub-date>` whose
declared type does not end `-submitted` or `-release`, with
`non_publication_years_refused` where the refusal leaves no year (183 of 8,118
served and 456 of 97,909 archive articles move); #272's empty-repeat guard
*widened* a neighbouring defect, so an `<lpage>` now completes only its own
`<fpage>`'s range and `last_pages_dropped` counts a refused one. It filed
**#273** (which publication date `year` should be) and **#275** (four
single-slot attribute readers defeated by a nested element). Two of PR #269's
commits (2e3345d, d4f9896) carry superseded claims, so the PR body is the
record, not GitHub's squash message.

## This session: #268, one structured component is never a citation

**Open as PR #277** (branch `fix/268-lone-component-citation`). The maintainer
picked #268 from the candidates (over #257, #275 and #273) and **chose the
rule** with the measurements in front of them: *one component is never a
citation*, over a flat threshold at three and over a text-coverage test.

- **What shipped.** `formatted_citation` and `_format_ref_html` print the
  deposited `citation` where fewer than two structured components would print
  at all. The count is `len(parts)` — the list each renderer has just built —
  passed to `JATSReferenceInfo._defers_to_the_deposit`, which replaces #265's
  `_carries_only_an_elocation_id`. That deletes the hand-written field list
  whose drift PR #269's review had caught, and it takes the locator's other
  trap with it: `volume` and `first_page` are two populated fields and one
  printed run, so a reference tagging both prints its deposit now.
- **Blast radius** (four artifacts, two checkouts in one process, field list
  from `dataclasses.fields`, **0 uncomparable**): references move in 828 /
  15,748 / 9 / 7,691 of the served, archive, `PMC000xxxxxx` and
  `PMC001xxxxxx` artifacts, in 346 of 8,118 / 5,573 of 97,909 / 6 of 3,028 /
  1,054 of 27,515 **articles** — the artifact sizes are article counts, and
  the reference denominators are 174,458 served and 2,975,128 archive — and
  **no other field of `JATSArticle` moves anywhere**. The reference counts agree to
  the unit with an independent routing tally.
- **Two details the comparator found, both stated rather than rounded off.**
  The HTML moves in **one more served article and five more archive ones** than
  `formatted_citation` does — the same rule through a renderer that decorates,
  where the one component's text *is* the whole deposit and only the
  decoration goes: a `<source>` loses its `<em>` and a whole-deposit
  `doi:<doi>` its `<a href>`, the complete pair, the other four components
  being emitted plain-escaped by both. And **three references of the 24,276
  moved across the four artifacts (two served, one archive, 0 in either
  back-filled package) get the same
  information less tidily**, their whole deposit being the component in the
  run-together form `citation` documents (`'BlockB LMehtaTOrtizG M'` for
  `'B L Block, T Mehta, G M Ortiz'`). Nothing is lost; they are the price of
  not having a text test.
- **The issue's second candidate was refuted by measurement, not declined.**
  *"The structured rendering drops text the deposit has"* moves 96.3% of
  served references at "any deposit word no component holds", and coverage is
  **smooth** — one-component references spread across every decile on both
  artifacts, six-component ones at 0.7-1.0 — so no threshold falls out of it.
  A flat threshold at three moves 3,119 / 52,253 but only by flipping pairs
  that read as citations (`authors`+`article_title`, 507 / 4,838).
- **A gap between two of bmlib's own counts, explained as far as it goes.**
  The issue's archive column reads 15,743 against this tally's 15,748, and
  its archive *articles* cell 5,571 against 5,573 — consistent with each
  other, +5 references in +2 articles. The
  served and `PMC001xxxxxx` columns reproduce the issue's exactly, `da443c4`
  (the commit its numbers were taken against) tallies 15,748 with *identical*
  per-reference identities, and three candidate explanations were tested
  against the corpus and refuted. The issue's script is not in the repo, so it
  is not attributable further; quote 15,748.
- **Mutation: 13 mutants and a control, all killed** — every threshold, both
  arms of the disjunction *deleted*, the deposit guard, an off-by-one in each
  renderer's count, a revert to `main`'s rule in each, and the two
  `_volume_info` edits that would split the locator run into two components.
  **One equivalent mutant is on the record rather than counted as killed**
  (PR #277's review): the first arm can be *widened* to subsume the second —
  `printed_part_count < 2 and bool(self.citation)` — and passes all 4,237
  tests, because both renderers join with `". ".join`, which is `""` at zero
  parts either way. `<=` or `< 2` for `== 1` is equivalent for the same
  reason. The arm is kept and marked prospective at the site; *equivalent is
  not unobserved*.
- **A mechanical guard on the argument, since the rule is only as good as it**:
  `test_every_call_site_passes_the_parts_it_built` walks the whole package with
  `ast` and fails a call that does not pass `len(x)` for a list its own
  function **builds and joins** — "appended to somewhere in this function"
  alone would wave through a renderer that builds a second list and counts
  that. Six teeth controls plus the positive — one for each refusal a wrong
  list, an attribute, a literal, a non-`len` call, a two-argument `len` and a
  second argument reach. **PR #277's review found the walk failing open**: it
  matched `ast.FunctionDef` alone, so a call in an `async def`, at module
  scope, in a `lambda` or in a class body was never *visited*, contributed no
  entry, and left the set equality green — an `async def` renderer passing
  `len(ref.authors)` was invisible against the real package walk. Every call
  node is counted first now and the walk must account for all of them; each
  function is scoped to its own body, so a nested helper's list is not its
  caller's; the join must be in what the function returns, which the
  docstring had claimed and the code had not checked; a keyword-form call is
  accepted rather than reported with the opposite complaint; and two call
  sites under one name are refused instead of collapsing.
- **Filed #276** — the residual the chosen rule leaves: a *pair* that names no
  work (`authors`+`year`, 841 served / 15,028 archive) still renders in place
  of its deposit. It needs a second claim, that a title, a source or a DOI
  names a work and authors, a year and a locator do not, and `source` is its
  weakest member. Also filed `hherb/bmlibrarian_lite` issue 299: both ports
  carry the defect, and their normative pseudocode has **no** deposit fallback
  at all, so a reference tagging nothing renders as the empty string there.
- **Filed #278** — the one affordance this rule trades away: a reference whose
  only printed component is a `doi` printed an `<a href>` and now prints its
  escaped deposit, so 96 served and 3,157 archive references lose the link in
  the cached HTML. No information goes with it (the DOI text is inside the
  deposit, and the deposit names the work the bare `doi:` run did not), and
  `ref.doi` is populated, so it is a rendering question — but it wants the
  deposit's DOI spellings measured first, a rule firing on a prefix of a
  longer DOI being a broken link rather than a missing one.
- **PR #277's review**, run over six aspects, found **no defect in the
  executable change** — the predicate is right at every boundary, both
  renderers provably build the same parts list over all 4,096 field-subset
  cases, and no reader of the deleted member survives — and **one test-net
  defect plus nine prose claims**, all taken in the commit after it. The net
  hole is the one to remember: an `ast` walk that only *visits* some shapes
  fails **open**, because an unvisited call site is an absent entry and a set
  equality cannot see one. Count the population first, then assert the walk
  accounted for all of it.
- **Tests: 4,237 passing + 63 skipped; `main` at 23c77a5 collects 4,257 and
  this branch 4,300**, so **+43** (+31 for the fix, +12 for the review's
  controls). Each measured with `pytest --collect-only` (`main` in a
  `git archive` copy).

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
- **Tests: 4,237 passing + 63 skipped** on this branch (`uv run pytest tests/
  -v`, 2026-09-17); **`main` at 23c77a5 collects 4,257** and this branch 4,300,
  each measured with `pytest --collect-only` (`main` in a `git archive` copy),
  so this branch adds **43** — 31 for the fix and 12 for the controls its own
  review's findings needed. Measure `main` yourself and never subtract from a
  previous handover's number — this bullet and the PR's own were stale by
  exactly one review round's tests until PR #274's review read them together. **The PostgreSQL half was not re-run and did not
  need to be** (`fulltext/` and documentation only); the last measured figure
  with `BMLIB_TEST_POSTGRESQL_DSN` set is 2435 + 2 on the #105 branch. Of the 63
  default skips, 61 are the PostgreSQL parameterisations, 1 a PostgreSQL-only
  schema test, 1 `test_pymupdf_requires_dependency`.
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
  `ROADMAP.md` are promoted at release time; **163 lines carry one** on this
  branch and 160 on `main`, recounted 2026-09-17 as
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

**Sixty-five open** (`gh issue list --state open --limit 300`, 2026-09-17,
after filing #276), and **sixty-four once this PR merges and closes 268**.
Open now: #86, #92, #94, #103, #128, #137, #142, #143, #144, #145, #150, #154,
#156, #157, #172, #173, #174, #175, #177, #178, #179, #181, #186, #196, #197,
#200, #201, #204, #207, #209, #210, #212, #214, #215, #217, #221, #222, #223,
#226, #227, #231, #233, #235, #240, #242, #244, #245, #247, #249, #251, #252,
#253, #255, #257, #258, #260, #264, #266, #267, #268, #270, #271, #273, #275,
#276. Re-count against `gh`.

**Wrong values left**: **#268 is this session's and is closed by this PR**;
what it leaves is **#276**, a *pair* that names no work (`authors`+`year`, 841
served / 15,028 archive references), which needs a second claim rather than a
wider reading of the count. **#258** (a `<bio>` name replaces the author's; 0) and
**#266** (a `<journal-meta>`/`<supplement>` contributor as an author, another
object's abstract as the article's; 0) want an owner test; **#267** (a nested
`<article-title>` cut out of the title; 0); **#270** (a related work nested in
a citation writes the reference's volume and pages; 0), a small guard.
**#273** is a decision rather than a wrong value: which *publication* date
`year` should be, the electronic one or the issue's, sized at 255 of 8,118
served and 742 of 97,909 archive articles for the first and 364 / 2,566 for
the second. **#275** is the one *silent* wrong value left — four single slots
set at a start tag and cleared at the matching close, so a nested element
defeats them with the accept branch firing; 0 instances in the four artifacts,
so it pins a direction.
**#264** is a false WARNING (168 of the archive's 169 zero-author lines name
another work's people).
**Largest content loss left: #257** — no `<funding-statement>` reaches the
article in 16.5% served / 42.1% archive; route, model or both. **#260** is its
small neighbour (`<custom-meta>` statements, `<subtitle>`).

**What still loses content the document carries**: **#271** (a
`<related-article>` in prose loses its `<article-title>`, so two archive
retraction and correction notices read `titled “,”`; 0 served). **#255** (a Wiley
self-citation `<p><mixed-citation>` in front matter, dropped with no line, 231
served). **#253** (a `<floats-group>`'s `<boxed-text>` panel reaches nothing —
925 runs in 192 archive articles — and its `<sec>` is an empty heading after the
body; a position decision). **#249** (an exhibit's second-language caption, plus
a latent abstract-erasing shape at 0 — a fixture for the second is cheap). **#242** (`<inline-graphic>` has no handler). **#251**
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
untitled-section presentation question, now the largest of these by readership:
paragraphs beyond the abstract's own render under the Abstract heading in
47,528 of 97,909 archive articles (5,897 on `main`, front matter being most of
the rise), pinned by a test so the fix changes it on purpose.
Every one is a decision rather than effort.

**Three have a measured-empty population and want closing rather than
building**: #204 and #207 measure 0 of 124, #210 measures 0 of 55 (its
remaining work is one test comment). **#212 blocks nothing but qualifies every
sampler share** — it is why `sample_api_failures.py` exits 1 on a clean run.

**Instrument-side leavings**: #214, #215, #217, #221, #222, #223, #226, #227,
#209, #196 (`publications/sync.py`'s own `User-Agent`, a latent second #194),
#197, #200, #201, #179, #181 (`last_is_thumb` over the wrong denominator).

**JATS contributor and reference half**: #142, #143, #144, #145. Formula family: #178 (the open
question), #177 (a float shape measuring 0), #174 (MathML flattening), #173
(a figure's `alt` duplicating its `figcaption`), #172 (the cache has no version
stamp — every unreleased JATS change above is why that matters). **#186** is
the last full-text-refusal decision. **#154, #156 and #157 are one job, the
funder corpus** — any session extending a funder list owes #154 first. **#103**
is a docstring line; **#94 and #92** may not be tightened without their
samplers; **#86** is a manual duplicating two methods.

**The instrument debt is real and stated.** Nine sessions (#224 through #265)
measured from scratch scripts over the named artifacts, and
`scripts/sample_jats_exhibits.py` carries a counter for none of them: the
**two-checkout comparator** (with #265's subsequence check), the **instrumented
`_JATSHandler`** (landing buffer, arm paths), the **drop-site tally**, and
per-article reconciliation. Adding them to `scripts/` is a session of its own.

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** (six releases missed; read the non-comparable
  changes first); **wire in** the segmenter and extractors (a design conversation
  each); **feed the stored grants to `transparency/`** (moves stored values).

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

1. **TDD, always**: behaviour tests first (upstream is the spec), watched failing.
2. **Modernise** (AGPL header, `from __future__ import annotations`, builtin
   generics, `datetime.UTC`) and **sever app coupling** (injected connections,
   optional deps behind `try/except ImportError`, LLM calls via `bmlib.llm`).
3. **Export** from the package `__init__.py`, via PEP 562 `__getattr__` for an
   extra (#64); **verify** (tests, both ruff commands, mypy); **record** in
   `CHANGELOG.md`; **reconcile rather than fork**.
4. **Read the spec on both sides; do not decide by eye** — for a JATS rule, the
   Swift port's normative `doc/cross_platform/jats_parsing.md`.

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
