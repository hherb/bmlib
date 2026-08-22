# HANDOVER — bmlib development

_Last updated: 2026-08-22. **0.10.0 is released and on PyPI**; six changes
sit unreleased on `main` — #73's atomic template install (PR #102), #96/#105's
partitioning of an over-cap PubMed day (PRs #106 and #114), #109's typed
article-id (PR #113), #110/#111's JATS sub-article and contributor-group
fixes (PR #118) and #115/#116/#117/#131's exhibit nesting, ranking and
sampler (PR #126, merged) — plus **this session's #127, on
`fix/127-table-graphic`**. All five version places agree at 0.10.0.
Five of the six unreleased changes are `fulltext` JATS fixes filed within
days of each other; whoever cuts the next release should describe them
together. Each unreleased ROADMAP row now carries an `*(unreleased)*` marker
— eight of them did not, so there was nothing for the release recipe to
promote and no way to tell a released row from an unreleased one; #109 had no
row at all.

**Two of them move what a caller of `JATSParser` gets**, and neither moves
what a bmlib *sync* stores. #111 populates an author list that was empty for
the majority of open-access articles. This session's #115/#117 change
`JATSArticle.figures` and `.tables` — figures that were missing now appear,
and a figure's `graphic_url` changes from a thumbnail to the full image for
roughly half of all figures. No bmlib path carries `figures`, `tables` or
`authors` anywhere: `service.py` never reads them, `FullTextResult` has none,
and `publications` takes its authors from the fetchers. Only a downstream
that calls `JATSParser` itself and persists the result needs to re-parse —
but such a downstream should, because the stored values are not comparable
across the upgrade.

**This session fixed #115, #116 and #117**, the three measured JATS exhibit
defects, and two more found while pinning them. They are the same family as
#110/#111 and were found the same way — on the way *out* of bmlib, by porting
fixes to the Swift `BioMedLit` parser this module descends from
(hherb/bmlibrarian_lite#166, whose `doc/cross_platform/jats_parsing.md`
carries the normative state machine and whose `JATSNestingTests.swift` has the
cases). All of them produce a well-formed result that reads as correct, so
none raised anything.

- **#115 — a nested `<fig>` dropped its parent.** eLife wraps every figure
  supplement inside the figure it belongs to; the single `current_figure` slot
  was overwritten by the inner open and cleared by the inner close, so the
  parent's own `</fig>` found nothing to build. The original survey put
  nesting at 19.6% of 225 articles; the new sampler re-measures it at **0.7%**
  of a general draw (2 of 276, and 0 of a 300-article stratified draw), both
  eLife — one publisher's house style costing about half of *its* figures,
  not a general convention. PMC8754430 returned 9 of its 12. `in_figure` was cleared
  too, so the parent's remaining internals were reprinted as article prose.
- **#116 — a `<table-wrap-foot><fn><label>` overwrote the table's number.**
  12.0% of the same 225 articles carry one; PMC12661592's single table was
  labelled `a`. A swallowed label is not a blank — the renderer substitutes
  `Table {i + 1}`, so the symptom is an invented number.
- **#117 — a figure with several `<graphic>` resolved to the thumbnail.**
  58.0% of 959 figures carry more than one and 52.9% end on a thumbnail.

**Two more defects were found while pinning those, and both are fixed here.**
`current_table` was the same single slot `current_figure` was, so a
`<table-wrap>` inside another's `<table-wrap-foot>` lost the outer table
outright — #115 on the other exhibit, unmeasured but structural. And `<label>`
and caption text asked whether a figure was open *anywhere above* before
considering the table, so an inner table's own number and legend went to the
figure enclosing it; both now route to the innermost open exhibit. Neither the
issues nor the Swift reference had the second one — it was the test for
#116's "compare against the exhibit's depth, not zero" rule that exposed it.

**Mutation testing killed 28 of 29 in the first pass, and code review found
two more survivors it had missed.** Deriving either ambient flag from the
*slot* list rather than the stack — `bool(self.figure_slots)`, a
five-character edit — passed all 121 tests while swallowing every paragraph
and section title after the first `</fig>`, which is the exact symptom the
derived-flag design exists to prevent. Cause: **no fixture placed body prose
after an exhibit close**, so the suite pinned the flag going on and never off.
Both leak fixtures now carry prose after the close and a following section,
and both mutants die.

The one true survivor is the `is not None` filter on the reserved slots,
unreachable by construction — expat rejects an unbalanced document, so no
reservation is ever left unfilled. That premise is now itself pinned by
`test_an_unbalanced_document_is_refused_outright`; without it a future lenient
feed would turn two documented-unreachable filters into live hole-hiders in
silence. Two gaps mutation found first were closed by adding the tests that
earn them: a `<fn-group>`'s own heading, and two of the four archival
mime-subtypes.

**Code review of the PR found three more defects, all fixed here.** Two were
regressions this branch introduced, both of them silent and both on the axis
the branch exists to fix:

- **An undeclared archival master beat the web image beside it.** `mime-subtype`
  is optional, so an `<alternatives>` TIFF declaring none ranked `FULL` and,
  deposited first under #117's strictly-better rule, permanently beat the JPEG
  after it. Pre-#117 "keep the last" got that case right. An archival master
  is now also recognised by extension; a thumbnail still is not, and the
  asymmetry is argued at the site — a first deposit is accepted whatever its
  rank, so demoting can only break a tie, never empty a figure.
- **A nested exhibit's `<graphic>` was donated to the figure enclosing it.**
  `<label>` and caption moved onto the stacks; `<graphic>` kept asking
  `current_figure`. A `<table-wrap>`, `<fn>` or `<supplementary-material>`
  nested in a `<fig>` handed over its image, and #117's ranking made it stick
  where "keep the last" had overwritten it. Now routed by its owning element,
  with `<alternatives>` transparent.
- **The #116 footnote-depth rule was replaced by a parent test.** The depth
  needed `_FOOTNOTE_CONTAINERS` to enumerate every container whose `<label>`
  is not the exhibit's, and the enumeration could not be completed by
  inspection: an `<fn-group>` directly inside a `<fig>`, a `<disp-formula>`'s
  `(1)`, a `<media>`'s `Video 1` and eLife's `<supplementary-material>`
  `Figure 1-source data 1` all still overwrote the number. A `<label>` is a
  direct child of the exhibit it numbers, so `element_stack[-2]` decides
  outright — no list, and exact where the depth was merely close.

**Five further defects were filed rather than fixed**: **#127** (a table
deposited as a `<graphic>` has nowhere to go — the drop is now logged, but
`JATSTableInfo` needs the field), **#128** (`xlink:href` matched by literal
prefix, so a document binding XLink elsewhere loses every figure image),
**#129** (a non-numeric `colspan` raises out of the SAX handler and the
service swallows it at DEBUG, costing the whole article), **#130** (a
`<boxed-text><caption><title>` renames the enclosing section — #125's mirror,
and best done with #123) and **#131** (these JATS populations have no in-repo
sampler, unlike every other curated list).

**Two further defects were filed rather than fixed** earlier in the session,
both reproducing on `main` after this branch: **#123** (a nested `<caption>`
truncates the enclosing caption *and* absorbs the inner element's legend — same shape as
#115, but a depth counter is not enough, since the caption's *owner* is what
the routing needs) and **#124** (footnote prose is dropped entirely, so the
rendered cell reads `12.3a` while the note it points at exists nowhere; needs
a model decision first, a `footnotes` field on both exhibit models).

**Next up is one of the remaining non-JATS issues, the seven JATS ones filed
this session (#123, #124, #127-#131), or Phase 3 of the bmlibrarian port,
whose every row needs a design conversation.** #131 — a sampler for the JATS
exhibit populations — is the one that unblocks the others: #127 and #128 both
turn on quantities nothing in the repo can currently measure. See "Next up".

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
- **Tests: 2414 passing + 63 skipped on `main`**, **2505 + 63** on
  `fix/115-117-jats-exhibit-nesting`, so 91 are this session's
  (`uv run pytest tests/ -q`). Both measured, not derived. The
  PostgreSQL half was **not** re-run for this session's branch and does not
  need to be: it touches no SQL, and the last measured figure with
  `BMLIB_TEST_POSTGRESQL_DSN` set is 2435 + 2 on the #105 branch. Of the 63
  default skips, 61 are the PostgreSQL parameterisations, 1 is a
  PostgreSQL-only schema test, and 1 is `test_pymupdf_requires_dependency`,
  which runs only when PyMuPDF is *absent*. **PyMuPDF is installed in the dev
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

Sixteen once this PR closes #115-#117 (19 open as this is written), every one
found by review or measurement rather than by a failing test, and **none of
them loses records** — though **#120** loses a contributor, **#123**, **#124**
and **#127** lose an exhibit's caption tail, its footnotes and a scanned
table's only content, **#128** would lose every figure image in a document
binding XLink to another prefix, **#129** loses a whole article to one
malformed `colspan`, and **#119** feeds a scan text that is not the article's.
Count this against the repo before trusting it: this line has been wrong in
two consecutive sessions. (**#56, #68, #72 and
#79** shipped in 0.9.1. **#78, #81, #88–#91, #95, #98 and #99** shipped in
0.10.0 — PRs #85, #87, #93, #97, #100. **#73** is on `main` unreleased in PR
#102, whose own review filed **#103**. **#96** closed with PR #106, as correct
rather than as fixed. **#105 and #107** closed with PR #114 — #107 dissolved
rather than being built: its saturation of `SyncReport.errors` came from a
permanent, structural refusal that no longer happens, and what remains of it
is in `docs/DECISIONS.md`. **#109** closed with PR #113. **#110 and #111**
closed with PR #118, whose review filed **#119**, **#120** and **#121**.
**#115, #116 and #117** close with this session's PR, which filed **#123**,
**#124**, and — from its own review — **#127**, **#128**, **#129**, **#130**
and **#131**.)

**#131 is closed by this session**: `scripts/sample_jats_exhibits.py` is the
missing sampler, and running it settled three things the code was asserting
rather than measuring. Two rules have an **empty** population — no
`<alternatives>` member in 276 articles declares a `mime-subtype` or is
archival at all, and exactly one `<graphic>` is owned by a non-exhibit inside
an exhibit (a `<td>`, resolving the same either way) — so both are defensive,
and the comments now say so instead of implying a population. The `<label>`
parent rule's premise measures **full**: 2,033 exhibits carry a direct-child
label and 2,033 carry one anywhere, so it cannot lose a label. And **the
19.6% nesting figure does not reproduce**: it is 0.7% of a general draw (2 of
276) and 0.0% of a 300-article stratified draw, with both nesting articles
eLife — so #115 fixes one publisher's house style, which costs about half of
*that publisher's* figures, rather than a general convention. #117's own
figures re-measure at 49.9% / 49.5% against the cited 58.0% / 52.9%.

**#128 is weaker than filed**: all 2,811 `<graphic>` hrefs in the sample use
the `xlink` prefix and every article binds XLink to it, so the literal-prefix
match is safe on measured evidence. Worth downgrading rather than closing —
the sample cannot prove no publisher does otherwise.

**#127-#130 came from the review of PR #126** and are described in the session
summary above. **#131** is the one to do first: it is the missing sampler for
the JATS exhibit populations, and both #127 and #128 turn on quantities
nothing in the repo can currently measure.

**#123 — a nested `<caption>` truncates the enclosing caption and absorbs the
inner element's legend.** `in_caption` is a stored boolean cleared by the
inner `</caption>`, so a `<media>` legend inside a figure's caption is
appended to the figure while the figure's own caption tail is dropped. The
same shape as #115, but **a depth counter is not enough**: `caption_depth > 0`
keeps the tail and still files the inner legend on the enclosing figure,
because that legend's owner is not an exhibit bmlib models. What the routing
needs is the caption's *owner*, built on `_innermost_exhibit()`. Prevalence is
not measured — the 225-article survey counted nested `<fig>` and labelled
footnotes, not nested `<caption>` — and the same corpus can answer it.

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
exactly like a correct one. This is the detector that was missing for the
whole life of #111 — 108 of 183 sampled articles parsed to zero authors
before the fix and nothing said so. #111 is fixed; the detector is not, so
the next cause hides just as long.

**#112 — three of the transparency funder-matching counts do not reproduce
against the committed corpus.** Filed with #109–#111, from the same Swift
port: `_is_industry_funder` was re-derived against
`tests/data/funder_names.json` so the Swift side could carry the same
justification, and three numbers disagree. **Nothing is a behaviour
regression** — `tests/test_funder_matching.py` passes and both floors still
hold — but `bmlib/transparency/analyzer.py` states measurements as the reason
for each inclusion and exclusion, and those are what a future edit is checked
against. The headline pair is stated as precision 0.917 / recall 0.324 and
measures 0.909 / 0.333; the stated figures are self-consistent with a corpus
holding 34 industry entries where this one holds 30, so they were taken
against a revision never committed — the corpus has exactly one commit
(be456a2) and the matcher is byte-identical since, so this is not drift.
`pharmaceutic` is claimed to have no false positive and has one, and it is
the *only* false positive in the matcher — the thing capping precision below
1.000, and precisely the claim an editor adding a fourth stem would rely on.
`co` is excluded for a collision the corpus does not contain, at the cost of
a real true positive. Whoever takes this should re-derive every figure in
those comments in one pass and say in each comment which corpus revision it
was measured against, rather than fixing the three named.

**#103 — `install_defaults()` reserves no `NAME_MAX` headroom for the
temporary name.** `atomic_write()` stages through a name 38 characters longer
than the target's and tells callers to leave room for it;
`fulltext/cache.py` does (`_MAX_PREFIX_CHARS`), `templates/engine.py` passes
the source filename through verbatim, so a default template named beyond
~217 characters now fails with `ENAMETOOLONG` where the old `write_text`
succeeded. Left alone deliberately: those names come from the caller's own
source tree rather than from unbounded input, and the failure is loud and
immediate rather than silent and permanent. The issue's own recommendation is
to say so in the docstring, not to cap — capping would rename a caller's
template and `render("<name>")` would then not find it.

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
probe that could not be made never printing as a finding. One input from
#96's measurement: efetch cannot itself produce the uniform-half-pages case
this floor was worried about — a page is the slice it named or it is refused —
so whatever the sample shows, it will not be that.

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
