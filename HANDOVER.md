# HANDOVER — bmlib development

_Last updated: 2026-09-04. **0.10.0 is released and on PyPI**; twenty changes
sit unreleased. All five version places agree at 0.10.0. Every unreleased
ROADMAP row carries an `*(unreleased)*` marker._

## What is unreleased, and what it costs a downstream

Twenty changes, fourteen of them `fulltext` JATS fixes filed within days of
each other — whoever cuts the next release should describe those together. Per-PR
detail is in `CHANGELOG.md`; only the *data* answer is kept here, because the
version number answers the API question and never that one.

**Most move what a caller of `JATSParser` gets, and each of those moves what a
bmlib *sync* stores.** #111 populates an author list that was empty for the
majority of open-access articles; #115/#117 change `JATSArticle.figures` and
`.tables` (missing figures appear, and roughly half of `graphic_url` changes
from a thumbnail to the full image); #127 fills the new
`JATSTableInfo.graphic_url`; #123/#125/#130 move `body_sections` for roughly one
recent article in ten; #120/#140 collect a contributor whose name arrived
undivided (3.3% of 1,025 articles lost at least one); #129 recovers an article
lost to a malformed `colspan`; #162 stops an exhibit the publisher did not
number being given one, moving cached HTML for 83 of every 997 recent articles;
#147 puts a formula back into the prose that contains it, moving prose and
cached HTML for 68 of 880 local articles and taking a LaTeX preamble out of
every table cell that held one. **#146/#149 is the largest** and the only one
measured by diffing a corpus rather than reasoned: over 880 local PMC articles /
20,770 references, `citation` moves for 4,499 (21.7%) in 191 articles — 3,541
rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for 502 in 14,
rendered HTML for 576 in 23.

**Five move stored *transparency* values, and all five are outside
`fulltext`.** #112 admits `plc`/`pty` to `_INDUSTRY_WORDS`, so `"GSK plc"` now
sets `industry_funding_detected`, which feeds a HIGH-risk rule and a quality
downgrade; neither token is in the labelled corpus, so **no measured figure
moves** — which is why the omission sat unnoticed. #119 stops a reviewer's prose
answering for the article: 0.61% of PMC's `oa_comm` `PMC012xxxxxx` baseline
package (97,909 articles) has at least one scan output move, dominated by
`data_availability_level` (499 of 602). #160 moves **nothing** — measured, 0 of
98,789 articles across both corpora. #183 moves nothing measurable either — a
body truncated between tags is a network product, and 0 of 106,027 articles
across both corpora is refused by the check — but where it *does* fire it
turns a manufactured `coi_disclosed=False` into `None`, which is the
difference between firing the missing-COI downgrade and not. #161 adds a
field rather than moving one: `TransparencyResult.full_text_status`, plus an
honest indicator string on the refusal paths, so a stored result that used to
say *"full text unavailable"* for a document Europe PMC served now says
*"served but not usable"*. A downstream matching that prose has to widen.

**The JATS fixes reach a bmlib path through the cached HTML**, a claim this file
once had backwards twice: `_build_html` renders authors, figures, tables and both
section lists into one string that `FullTextService` caches via
`parse_with_html()`. Nothing *structured* is stored, but a downstream holding
cached full text should re-fetch, not only one calling `JATSParser` itself.

## Rules carried forward

Each is argued in full in `CLAUDE.md`, `docs/DECISIONS.md` and at its call site;
only the shortest form is kept here, because these are what a session gets wrong
again rather than what it can look up.

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; one window is not the rate (#127 read 0 of 662 recent tables and 11
of 93 in a 1996-1998 draw; #119 reads 0.7% of one corpus and 3.45% of another).
**An issue's own remedy is a hypothesis too** — #162's cost ten minutes to
refute, #183's was refuted by 1,750 articles that end in a legal trailing
comment.
**Measure the population the code actually reads**; prefer a corpus with a public
name over one on your disk — and check that its *rendition* is the one the code
is fed, which is the half #138 learned the hard way. **A share is of a
denominator, and the rendition chooses the denominator** (#164's correction is 18
figures or a quarter of the population, depending only on which bytes you count).
A number in a comment goes stale silently and coherently —
`TestTheCitedPopulationsAreWhatTheCorporaHold` and
`TestTheStatedCountsAreWhatTheCorpusHolds` are the answer. A rule can be
spec-driven and still owe an instrument, and an instrument's vocabulary has to be
open or it certifies (#121). **State a blast radius from a diff, not from the
call graph**: PR #148 reasoned soundly from a false premise, and four review
agents missed what two parses over 880 articles showed in minutes; `gained/lost`
is blind to the commoner case, a value that changed in place.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding. When a fix extends a routing rule, walk every other path it
reaches — the guard written on one branch is the guard the others need, and the
same rule stated in prose on one branch is not applied on the next. Read the
rules *next to* the one you are adding before calling a fix one line. A stack of
frames needs the entries it will not use. **A set keyed on the element cannot
express a rule about the context** (`_INLINE_ELEMENTS` was right for #120 and
wrong for #146). **Suppressing a merge does not empty a buffer** — only an
accumulating child ever withheld anything.

*Diagnostics and tests.* A diagnostic's *level* is a claim that has to be
measured, and a detector must report what it *checked*, not what it concluded. A
net needs its own false-positive net, and it must be free — the autouse
`parser_log` fixture makes all 186 pre-existing fixtures one. Key a counter on
*structure*, never on the routing it is checking, and **read the increment site,
not the name or the report** (a verdict line invented #162 outright). **A rule
enforced by prose is not enforced** (`TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`,
`TestEverySectionIsGatedOnEveryCounterItReads`), and it demands a *choice* rather
than a field. **Checking the arithmetic is not checking the rule**, and check the
denominator too. **A zero over an absent population is not a clean result.** Tell
a vacuous green from one asserting silence: **ask which line of the fixture the
assertion depends on**. **Mutate the *old* half of a condition you extend**, and
give a fixture prose *after* the close as well as before it — two survivors hid
that way in PR #126. **When you re-scope a counter, keep both readings per row**,
because a redraw moves the sample, the bytes and the walk at once.

*Process.* An issue can be closed as COMPLETED without being fixed, and **a
closing keyword in prose has closed one nobody decided — four times**, the fourth
being the commit that warned about the other three. GitHub reads *"filed rather
than ‹keyword›: ‹number›"* literally and does not care that the sentence says the
opposite, nor that the substring sits in a quotation or a code span. So the rule
is **never reproduce the substring at all** — describe it, or drop the `#` — and
because the rule was written, read and then broken by one session, the real check
is after the fact: **after every merge that mentions an issue in prose, diff `gh
issue list` against what the commit says it filed and fixed.** That has now caught
a keyword that fired (#137, #142, #160) *and* two that did not (#147, #164, each
closed by hand one session late). Also, a mutation harness
restoring with `git checkout -- <file>` deletes whatever is uncommitted in it.

## This session: #183 and #161

Both from PR #182's and PR #159's reviews, and both the same question: what a
stored result says about full text it could not use. The argument is in
`CHANGELOG.md`, `ROADMAP.md` and at the call site; what a session repeats is:

- **An issue's own remedy is a hypothesis, and this one was refuted.** #183
  proposed `rstrip().endswith("</article>")` — but trailing comments, PIs and
  whitespace after the root are legal XML, and **1,727 of 97,909 archive
  articles (1.76%) and 23 of 8,118 served ones (0.28%)** end
  `</article><!--requester-ID …-->`. Testing **presence** rather than position
  is cheaper and exact, and refuses **0 of all 106,027**.
- **Order the refusals most-specific-first, and pin the order.** A truncated
  body satisfies several at once — truncation is the cause, the rest are
  symptoms — so the completeness check runs *last*. Ahead of the lex it makes
  #160's construct-and-offset message unreachable for the only input that
  produces it; ahead of the entirely-nested report it makes that unreachable
  too, since a body of nothing but `<sub-article>` carries no `</article>`.
- **A new field's `None` must mean *not recorded*, never a determinate
  member.** `full_text_status` mirrors `unknown_reason` throughout; a legacy
  result may carry `full_text_analyzed=True`, so loading it as `NOT_ATTEMPTED`
  would be a false machine-readable claim. `__post_init__` enforces the one
  direction it can.

**18 of 18 mutants killed**, including the refuted remedy (caught by the
trailing-comment negative control) and each of the three ordering moves.

**The measurement turned up #184, which is larger than either.** Fetching a
live `fullTextXML` body to see what a served response looks like returned 404
for every identifier: `_fetch_europepmc_fulltext` builds
`.../rest/{source}/{ext_id}/fullTextXML`, and the form Europe PMC serves is the
single-segment one `fulltext/service.py:1652` already uses. Four identifiers
spanning 2012-2025, 200 on one form and 404 on the other. **`TransparencyAnalyzer`
has lost full text entirely** — every analysis falls back to the abstract,
`coi_disclosed` can never reach `False`, up to 30 points off every open-access
paper — and it fails silently, a non-200 being the one outcome that
deliberately does not warn. Not fixed here: it moves stored values broadly and
wants its own blast radius.

## Current state

- **Version 0.10.0, released 2026-08-15 and live on PyPI** (0.4.0 → … → 0.10.0;
  `CHANGELOG.md` has the dates). The version lives in **five** places —
  `pyproject.toml`, `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s
  header, and `docs/manual/index.md`'s header line — and all five agree. The
  fifth was missing from this list until 0.10.0 and had gone stale at 0.4.0 for
  five releases; only `bmlib/__init__.py` is guarded by anything but this list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it here.
  0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag, and they
  compound for anyone upgrading across them; 0.8.0's largest changes the shape of
  every synced PubMed title and abstract. **0.9.0 moves nothing stored.**
  **0.9.1 moves one thing**: #79 makes Tier 1d take the free PDFs it had been
  discarding, so stored full text is not comparable across the upgrade.
  **0.10.0 moves nothing stored but is not free**: no `download_days` row a
  previous release wrote is durable under #95's rule, so the whole window is
  re-fetched once. The two questions are independent, and a downstream reading
  only the number must still read this list.
- **Tests: 3170 passing + 63 skipped** (`uv run pytest tests/ -q`, measured
  2026-09-04 on this branch). The PostgreSQL half has not been re-run since the
  SQL last moved; the last measured figure with `BMLIB_TEST_POSTGRESQL_DSN` set
  is 2435 + 2 on the #105 branch. Of the 63 default skips, 61 are the PostgreSQL
  parameterisations, 1 is a PostgreSQL-only schema test, and 1 is
  `test_pymupdf_requires_dependency`, which runs only when PyMuPDF is *absent*.
  **PyMuPDF is installed in the dev venv.**
- **Run the PostgreSQL half locally — it is two minutes and it finds real bugs.**
  Postgres.app ships the binaries. The socket directory must be a *short* path
  (the 103-byte limit bites, and a scratchpad path exceeds it; `createdb` then
  fails while `pg_ctl` reports success):
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
  `ROADMAP.md` are promoted at release time; **83 are outstanding** — 35
  `ROADMAP.md` rows and 48 spots across `docs/manual/fulltext.md` (18),
  `transparency.md` (14), `publications.md` (13) and `templates.md` (3).
  Recounted 2026-09-04 on this branch; the figure is measured, not maintained, so recount rather
  than adjust it. Grep case-insensitively for `unreleased`, not for
  `(unreleased)`: three of 0.10.0's were spelled `*(unreleased, #99)*` and
  `(changed, unreleased — …)`. Write the marker bare, never with a guessed
  version number. Markers inside `docs/superpowers/plans/` are historical records
  — leave them alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, and CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup*, so there is no workflow file
  in the repo — and its generated workflow ignores a PR's `reopened` action, so a
  PR predating the setup needs a fresh commit rather than a close/reopen. It does
  **not** constrain the merge strategy; squash away (#78).

## Next up

### Open GitHub issues

**Twenty-five open** as this file is written, **twenty-three once this branch
merges** (`gh issue list`, 2026-09-04, after #160 was closed by hand — see the
process rule above — and after this session filed **#184**): #86, #92, #94,
#103, #124, #128, #137, #142, #143, #144, #145, #150, #152, #154, #156, #157,
#161, #172, #173, #174, #175, #177, #178, #179, #181, #183, #184. This branch
answers #183 and #161. All but #184 were found by review or measurement rather
than by a failing test, and **none loses records** — though **#184 loses every
open-access paper's full text** from `transparency`, **#124** loses an
exhibit's footnotes, **#150** renders a note-only reference as an empty bullet,
and **#128** would lose every figure image in a document binding XLink to
another prefix.

**#184 is the one to do next, and it is not like the others.** It is the only
open issue found against the *live* API rather than by reading code or a
corpus, the only one costing a whole analysis input, and a one-line fix whose
cost is entirely in the blast radius it needs — restoring full text to every
open-access paper moves `coi_disclosed`, `data_availability_level`,
`industry_funding*` and the score. Its remedy also has two halves the issue
names: the URL, and a test that could have caught it, since every existing
test mocks the client and matches any path ending `/fullTextXML`.

**Count them against the repo before trusting that number.** The line has been
wrong in several sessions, and an issue closed as COMPLETED without being fixed
is invisible to any such count. Provenance, since every open issue but five came
out of a PR (released provenance is in `CHANGELOG.md`): **#73** → PR #102, filing
**#103**; **#96** → PR #106; **#105**, **#107** → PR #114; **#109** → PR #113;
**#110**, **#111** → PR #118, filing **#119**–**#121**; **#115**–**#117**,
**#131** → PR #126, filing **#123**, **#124**, **#127**–**#130**; **#127** → PR
#133, filing **#132**, **#134**, **#135**; **#123**, **#125**, **#130**, **#135**
→ PR #136, filing **#137**, **#138**; **#121**, **#129**, **#134** → PR #139,
filing **#140**; **#120**, **#140** → PR #141, filing **#142**–**#146**;
**#146**, **#149** → PR #148, filing **#147**, **#149**–**#151**; **#151** → PR
#153, filing **#152**; **#112** → PR #155, filing **#154**, **#156**, **#157**;
**#119** → PR #159, filing **#158**, **#160**, **#161**; **#162** → PR #171,
filing **#172**, **#173**; **#147** → PR #176, filing **#174**, **#175**,
**#177**, **#178**; **#164** → PR #180, filing **#179** and **#181**; **#160** →
PR #182, filing **#183**; **#183**, **#161** → this branch, filing **#184**
(from measurement rather than review — see the session note above). (#149 and #152 were filed and fixed inside one
PR, so neither ever appeared as open work — read the per-PR list, not the total.)

Each issue carries its own argument on GitHub; what follows is only what a
session needs to *choose* between them. Almost none is a drive-by.

**The JATS corpus redraw is done** (#132, #138, #158 answered), so #142, #143 and
#150 have a population — and **all three measure empty**, which blocks them on a
stratified draw rather than on effort. Both corpora are 1,000-article draws at
`seed 0`, recent from `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`
(2023-2025, 997 served) and back-filled from `…PMC002xxxxxx…` (1996-1998, 1,000
served). The sample is drawn from the package and the bytes measured from Europe
PMC's `fullTextXML`, because the two renditions disagree on exactly the cited
populations — `last_is_thumb` differs in 156 of 300 compared articles, and where
it differs the archive measures 0 against 781 served.

**Three issues lose content the document carries, and each is blocked on a
modelling decision.** **#124**: neither exhibit model has a `footnotes` field, so
a `<table-wrap-foot><fn>`'s abbreviation expansions and per-table funding notes
reach nothing while `<sup>` is flattened into the cell — the rendered table reads
`12.3a` with the note it points at existing nowhere. **#150**: a `<ref>` whose
only content is a `<note>` renders as an empty `<li>`, 4 instances in one
publisher. **#144**: whether `<on-behalf-of>` is a name or an attribution.

**#142, #143 and #145 are the rest of PR #141's review**, in the contributor and
reference half of `jats_parser`. #143 — several `<collab>` in one `<contrib>`, or
a `<name-alternatives>` — is bare last-wins with no parent test and no log, the
#116/#127 class. #142 is a `<collab>`'s `<institution>`/`<addr-line>` children
running together with no separator. #145 is `<aff>` resolution through `@id`.

**#137 has been auto-closed twice without being decided**: a section-level
`<caption>`'s `<p>` children still reach `body_sections` while its `<title>` is
dropped, so one caption's halves go different ways. The sampler records the
`<title>`'s parent but not the `<caption>`'s owner, so the population is not yet
derivable.

**#152 — neither half of `<article-id>`'s reachability guard is pinned, and they
are not equivalent.** `parent == "article-meta" or self.in_front` decides whether
the identifier is read at all, and each half deletes on its own with the whole
suite green. For valid markup `<article-meta>` is inside `<front>`, so the parent
test can only fire on markup JATS does not admit, while `in_front` admits an
`<article-id>` in `<notes>` as the article's own DOI. Unmeasured, so it pairs with
a draw. No behaviour is known to be wrong today; it matters because this is where
#109 was — carefully argued rules behind an unpinned guard.

**#128 is weaker than filed**: all 13,624 `<graphic>` hrefs in the two corpora use
the `xlink` prefix bound to the XLink namespace, so the literal-prefix match is
safe on measured evidence. Worth downgrading rather than closing.

**#172–#178 are PR #171's and PR #176's leavings, all in `fulltext` rendering.**
**#178 is the open question of the group**: whether LaTeX should win for a
*both-encoding inline* formula at all, since there it replaces prose that was
already correct in 20,046 formulas against the 205 it recovers. #177 routes a
display formula that reaches no section, caption or cell (192 in 23 of 97,909).
#174 is MathML flattening losing spacing and brackets — now reaching far more
prose than before #147. #175 is a formula deposited as an image, which no field
carries. #172 is the cache having no version stamp to invalidate it when a
rendering change moves the HTML. #173 is a figure's `alt` duplicating its own
`figcaption`.

**#179 and #181 came out of PR #180.** #179: the per-host pacer is per-process,
so two concurrent sampler runs double the rate against one host — measured at 103
and 98 articles lost to Europe PMC refusals against 3 sequentially. #181:
`last_is_thumb`/`first_is_thumb` increment only inside `len(graphics) > 1`, so
their population is `figures_multi_graphic`, but the report and five files divide
them by `figures_with_graphic` — over its own population the recent window reads
**99.3%**, not 57.3%, which makes #117's rule far more load-bearing than the
published figure says. One remedy restates a share cited in five files and the
other needs both corpora redrawn, which would destroy #164's attribution.

**#183 and #161 are answered by this branch** — see the session note above.
What is left of that family is **#184**, which the same measurement turned up
and which is larger than either.

**#154, #156 and #157 are one job, and it is the funder corpus.** #154:
`scripts/sample_funder_names.py` writes `tests/data/funder_names.raw.json`, which
is in no commit, so the repo holds the 417 labelled names and not the 816 they
were drawn from — a count over the unlabelled remainder is unanswerable, and a
redraw has nothing to diff against. The old draw is not recoverable, so the fix
carries a decision: re-label the intersection, or commit a fresh draw as the
baseline for the *next* comparison and say so. #156: rule 2's premise was false,
so it is a prior; measuring what it costs needs a draw stratified for European
funders. #157: `plc` collides with *phospholipase C* and wants a targeted
`\bplc\b` draw, the general one having found none in 816. Both risks are pinned by
`TestTheKnownFalsePositivesAreKnown` — keep the choice, file the measurement, do
not quote the reasoning as measured. `docs/DECISIONS.md` requires the sampler be
run before either list is touched, so any session extending a funder list owes
#154 first.

**#103 — `install_defaults()` reserves no `NAME_MAX` headroom for the temporary
name.** `atomic_write()` stages through a name 38 characters longer than the
target's, and `templates/engine.py` passes the source filename through verbatim
where `fulltext/cache.py` leaves room, so a template named beyond ~217 characters
fails with `ENAMETOOLONG`. Left alone deliberately: the names come from the
caller's own source tree and the failure is loud. The fix is a docstring line,
not a cap — capping renames a caller's template and `render("<name>")` then does
not find it.

**#94, #92 and #86 are the older non-JATS three.** #94 and #92 are the same shape
— a guard resting on an unmeasured quantity, argued in full in `CLAUDE.md` under
*A completed day is a durable claim* — and **neither may be tightened without
running the sampler it asks for**. #94: one bioRxiv error body (messages, no
`collection`) is indistinguishable from a quiet day, and the tests deliberately
pin *both* possible quiet-day shapes so the guard cannot come to depend on the
unmeasured answer. #92: `SHORTFALL_FAILURE_RATIO = 0.5` is bmlib's only threshold
not set from a sampled population, and a floor tightened past the real benign gap
re-fetches that day on every later run for ever. #86: `docs/manual/llm.md`
documents `LLMClient.generate` and `LLMClient.embed` twice each — not a delete,
since the copies differ (one `generate` has the example, and the two `embed`
sections disagree on the default model, `embed_batch`'s being right).

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** — `~/src/bmlibrarian` still pins
  `bmlib[ollama]>=0.5.1,<0.6.0` and has missed six releases; a downstream change,
  not a bmlib one. Read the intervening non-comparable behaviour changes first
  (see the release list above). The widened pin should clear
  `FullTextService.cache` being nullable, one of 0.9.0's three API changes.
- **Wire the segmenter and the rule-based extractors in.** Two halves of one
  roadmap item: the segmenter could give `CochraneAssessor` Methods/Results
  boundaries and `TransparencyAnalyzer` the paper's own Funding/COI/Data
  sections; `quality/extractors.py` is called by no tier. Each needs a design
  conversation.
- **Feed the stored grants to `transparency/`.** `TransparencyAnalyzer` runs its
  own `efetch` per paper to read `<GrantList>`, which `fetch_pubmed` already
  stores at sync time. Reading the table saves that request, but it is a scoring
  change that moves stored values — its own decision, not a quiet optimisation.

### bmlibrarian → bmlib porting (Phase 3 is next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list with
reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

- **Phases 0, 1 and 2 are done and shipped** — Phase 0 in 0.4.0, Phase 1 in
  0.7.0, Phase 2 across 0.7.0 and 0.8.0 (rows 10, 9, 8, 4 and 11 of that doc's
  master table — rows, not GitHub issues; PRs #51, #54, #55, #58, #59).
- **Phase 3 is next**: discovery (#12), `pubmed_search` (#13), MeSH (#21),
  ClinicalTrials.gov (#14 — **check the caveat first**, the legacy bulk XML the
  parser targets was deprecated in the 2024 API v2 migration). Each is a larger
  subsystem than anything in Phase 2 and needs its own design conversation rather
  than a straight port. Phase 4 (the prompt-driven agent family, paper_weight,
  review building-blocks) follows, reconciled against the existing `quality/` and
  `transparency/` rather than forked.

### The port recipe (repeat it)

1. **TDD, always.** Behaviour tests first (upstream is the spec), watch them fail
   (`ModuleNotFoundError` is the correct red for a new module), then port. Bug in
   a test you wrote? Fix the test, not correct code.
2. **Modernise to bmlib style:** AGPL header, `from __future__ import
   annotations`, lowercase builtin generics, `datetime.UTC`.
3. **Sever app coupling:** injected connections/params instead of
   `get_db_manager()`/`bmlibrarian.config`; optional deps behind
   `try/except ImportError` raising `pip install bmlib[extra]`; LLM calls through
   `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** from the package `__init__.py` `__all__` — and if the module needs
   an extra, through a PEP 562 `__getattr__` rather than eagerly (#64: one eager
   re-export made ten modules unimportable on a core install).
5. **Verify** (tests + both ruff commands + mypy), **record** in `CHANGELOG.md`
   under `[Unreleased]`, and **reconcile rather than fork**.
6. **Read the spec on both sides; do not decide by eye.** Row 11's reviews found
   this three times. Reading someone's XML, check their DTD: `<Affiliation>` looks
   like a leaf, is declared `(%text;)*`, and a bare `.text` silently dropped rows.
   Declaring an output format, you owe that format's rules for *every* value, not
   only the ones carrying markup.

## Deliberate non-fixes — do not "fix" these

**Moved to [`docs/DECISIONS.md`](docs/DECISIONS.md). Read it before "correcting"
anything that looks wrong in `db/`, `llm/`, `agents/`, `context_processor/`,
`citations/`, `quality/`, `transparency/`, `publications/` or `fulltext/`.** Each
entry there was investigated and closed as correct, so reopening one wastes a
session; the file also records where each argument lives in full. Add new entries
there, not here — this file is for what still needs doing.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API connection as
  first argument, type hints and docstrings on everything public, AGPL-3 header
  on every source file, dataclass models with `to_dict()`/`from_dict()` where they
  persist, explicit SQL (no ORM), optional dependencies guarded with a helpful
  `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`** — CI pins **0.15.20**
  (`.github/workflows/ci.yml`), while `.venv` holds an older one that false-flags
  rules newer ruff removed:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- **`uv run mypy` is a gate too** (#81), pinned to **2.3.0** in the `dev` extra
  with its settings in `pyproject.toml`. Give it no arguments — the bare command
  is what the `types` CI job runs — and run it in the dev venv: every extra but
  psycopg2 ships its own `py.typed` (that one via `types-psycopg2`), so against a
  bare interpreter mypy reports the optional imports *and `jinja2`, a core
  dependency*, as missing stubs. Anything deliberately unchecked is an inline
  `# type: ignore[code]` with its reason at the site, never a per-module
  `ignore_missing_imports`: `warn_unused_ignores` reports the first when it goes
  stale and can never report the second.
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP; no
  external services. `BMLIB_TEST_POSTGRESQL_DSN` must point at a database the
  tests may drop every table in (recipe under "Current state").
- New functionality needs unit tests; see CLAUDE.md's test-file mapping table.
- Session workflow lives in the `nextsession` skill (`.claude/skills/nextsession/`);
  the post-review fix-up workflow lives in the `fixall` skill.
- **Cutting a release** (0.4.0 through 0.10.0 were all cut this way): bump the
  version in the **five** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header,
  `docs/manual/index.md`'s header line — promote the CHANGELOG's `[Unreleased]`
  body under a dated `## [X.Y.Z]` heading (leaving `## [Unreleased]` above it)
  with a short prose summary, promote any `unreleased` markers in `docs/manual/`
  and `ROADMAP.md`, add the release's own `ROADMAP.md` row, then commit on a
  `release/X.Y.Z` branch and open a PR. **The number is a claim about the API, not
  about the data**, so state the data answer in prose every time. Three shapes,
  all real: 0.9.0 was renumbered from 0.8.1 in review (API moved, nothing stored
  did); 0.9.1 is a patch that moves stored full text (#79); 0.10.0 is a minor bump
  moving nothing stored whose real cost is a one-off re-fetch of the whole sync
  window. After CI **and CodeQL** are green, merge it with any button, then **tag
  `main`'s tip rather than a particular commit** (#78):

  ```bash
  git checkout main && git pull --ff-only
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || exit 1
  grep -q '__version__ = "X.Y.Z"' bmlib/__init__.py || exit 1
  git tag -a vX.Y.Z -m "bmlib X.Y.Z" && git push origin vX.Y.Z
  ```

  The two checks are the whole point: the first catches a stale local `main`, the
  second catches tagging a commit that does not carry the version — the failure
  `release.yml` would otherwise find *after* the release is public. These are
  **annotated** tags, so verifying one needs `git rev-parse 'vX.Y.Z^{commit}'`.
  Then create the GitHub release, which is **what publishes** —
  `.github/workflows/release.yml` rebuilds, refuses to go on unless the tag
  matches `bmlib.__version__`, runs `twine check --strict`, asserts `py.typed`
  survived packaging, and uploads via Trusted Publishing. **Hand the `pypi`
  environment gate over rather than approving it**, even when `gh api
  .../pending_deployments` says `current_user_can_approve: true`: a PyPI upload is
  irreversible and the version can never be reused. Nothing is lost by waiting.
  Afterwards verify against `https://pypi.org/simple/bmlib/`, not the JSON API,
  which serves a stale CDN cache. Rehearse the whole path any time with a
  `workflow_dispatch` run, which targets TestPyPI only.
- **Rehearse the release gates locally before opening the PR** — `uv build`,
  `twine check --strict` on both artifacts, and a clean-venv install asserting
  `py.typed` survived packaging. `release.yml` runs them only *after* the version
  is burned and the release is public, so a failure there is expensive and a
  failure locally is free. On any release touching an `__init__.py`, probe the
  built wheel **one fresh interpreter per module** as well: a single process
  leaves the half-initialised parent in `sys.modules` and its siblings then
  falsely read as importable, which is how #64 was first mis-scoped.
- **Do not upload by hand.** The publish job has no `skip-existing`, so a manual
  upload makes it fail on a duplicate — which is why v0.5.0's and v0.6.0's runs
  still sit unapproved. v0.7.0, v0.8.0 and v0.9.0 all went the whole way through
  the workflow.
