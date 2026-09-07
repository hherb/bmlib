# HANDOVER — bmlib development

_Last updated: 2026-09-07. **0.10.0 is released and on PyPI**; twenty-four
changes sit unreleased. All five version places agree at 0.10.0. Every
unreleased ROADMAP row carries an `*(unreleased)*` marker._

## What is unreleased, and what it costs a downstream

Twenty-four changes, fourteen of them `fulltext` JATS fixes filed within days of
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

**Ten move stored *transparency* values, and all ten are outside
`fulltext`.** **#184 is the largest *measured* one** — #194 may well be larger
and nobody has counted it, see below —: every Europe PMC full-text fetch was 404ing, so every analysis
ran on the abstract. Restoring it moved, over 48 real open-access analyses
diffed against `main`, `coi_disclosed` in 32 (19 to `True`, 13 to `False`),
`transparency_score` in 20 (+10 or +20, never negative), and `risk_level`
HIGH→MEDIUM in 5 with `tier_downgrade_applied` 1→0 in the same five. Nothing
was scored worse *in that draw* — but the missing-COI downgrade is now
reachable at all, so a paper above `score_threshold` with no COI statement in
its full text can be downgraded where before it could not. **Any downstream
holding stored transparency results should recompute them.**

**#194 is the other one that moves points, and its population is unmeasured.**
ClinicalTrials.gov had been refusing bmlib's own `User-Agent` with a 403 since
the endpoint was first called, so `_check_trial_results` always returned
`False`, `SCORE_RESULTS_POSTED` (15) had never been awarded to any paper, and
*"Registered trial without posted results"* was stored as a false claim about
every registered trial. With the header corrected, any paper whose registered
trial has posted results gains 15 points and loses that indicator, so
`transparency_score` and `risk_level` both move — favourably. **How many papers
that is has not been measured**, so this is not comparable with #184's 48-analysis
diff; it is a *class* of paper rather than a rate. **Any downstream holding
stored transparency results for papers with registered trials should recompute
them.**

The other seven are smaller, and the *data* answer is all this file keeps —
`CHANGELOG.md` has each argument. **#112** (`plc`/`pty` admitted to
`_INDUSTRY_WORDS`) moves no *measured* figure, neither token being in the
labelled corpus, which is why the omission sat unnoticed; it still flips
`industry_funding_detected` for a `"… plc"` funder, feeding a HIGH-risk rule.
**#119** moves a scan output for 0.61% of the `oa_comm` `PMC012xxxxxx`
package's 97,909 articles, dominated by `data_availability_level` (499 of 602).
**#160** moves **nothing** (0 of 98,789 across both corpora) and **#183**
nothing measurable (0 of 97,909 refused) — but where #183 fires it turns a
manufactured `coi_disclosed=False` into `None`, which is the missing-COI
downgrade firing or not. **#161** adds `TransparencyResult.full_text_status`
rather than moving anything, plus an honest indicator on the refusal paths;
**#187/#190/#191** move that field alone; **#193** moves it *and* adds a COI
indicator to the persisted `risk_indicators` — "moves that field alone" was
wrong here in the shorthand PR #192's review had already corrected once. A
downstream matching that prose, or branching on a member rather than on
`is_refusal`, has to widen; one pinned to an older bmlib raises out of
`from_dict` on a row this version writes.

Correcting the header was **not** the whole of #194 (PR #195's review): the
`bool` stood, so a 404, a 403 or an unusable body still stored *"Registered
trial without posted results"* — the same lie over a smaller population.
`_check_trial_results` is a tri-state now and the caller reports three
outcomes, so a paper whose CT.gov requests all failed swaps that line for
*"Trial registration found; posted-results status could not be checked"*. Both
downstreams render the flag rather than the indicator — BioMedicalNews's
`reading_pane.html`, bmlibrarian_lite's risk badge — which is what #198 below
is for.

**#198/#202/#203 is the tenth, and #203's half is the widest population in the
transparency list.** #198 adds `TransparencyResult.trial_results_status`
(`TrialResultsStatus`), so the flag both downstreams render stops being the
only readable answer — it moves no existing value, being a field. #202 moves
nothing stored at all: one fewer request and one fewer log line per document
during a Europe PMC outage. **#203 moves `risk_indicators` for every analysis
that did not scan full text**, which is every closed-access paper, not only the
outage case: *"COI disclosure status unknown (full text unavailable)"* and its
two siblings become *"COI disclosure status unknown"* plus a provenance line
naming what became of the full text. **A downstream string-matching any of
those three strings breaks**, and the remedy is to read `full_text_status`
instead — which is what it is for.

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
is fed, which is the half #138 learned the hard way. **A live Europe PMC draw
must be stratified by source and publication year**, a cursor page being a
contiguous block of accessions: unstratified, `IN_EPMC:Y` read 48% 404 where a
stratified draw read 3%, and no preprints where a source-stratified one read a
third. Needed twice now (#184, #191). And **run one live probe at a time** —
the per-host pacer is per-process (#179), so two concurrent runs poisoned
#184's first blast-radius measurement into reading "nothing moved". **A share is of a
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
measured — **and the branch it sits on must be no wider than the draw**, which
is #191: DEBUG was measured on 404s and applied to every status code. A
detector must report what it *checked*, not what it concluded. **A status enum
member is a stored claim**, so one covering several causes puts words in a
third party's mouth (#187/#190/#191, `NOT_SERVED` for a bmlib bug, a 503 and
an empty 200; #193, `NOT_ATTEMPTED` for an outage). **And before arguing about
a level, check the diagnostic exists** — #193's five branches emitted nothing
at any level, so there was none to raise. A
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
that way in PR #126. **An equivalent mutant is not an untested guard**: where
two independent protections cover one defect (#203's — the line is out of the
retraction's set *and* appended after it), every single edit survives and only
breaking both reddens anything. Read that as the redundancy working, and say
which pair you broke. **When you re-scope a counter, keep both readings per row**,
because a redraw moves the sample, the bytes and the walk at once.

*Live behaviour.* **A property only a real remote can refute needs a real
probe.** #194 — ClinicalTrials.gov 403ing bmlib's `User-Agent`, so no paper had
ever been credited with posted results — was invisible to the whole suite because
**no test in it makes a live request**, and it surfaced the first time an
instrument presented bmlib's own identity to the live endpoint. **A sampler
must address *and head* requests exactly as the code does**: #184's lesson is
usually told about URLs, and the header is the same lesson.

*Cost.* **The cost of a schema addition is not a constant — it depends on what
else is unreleased beside it.** #198 was deferred in `docs/DECISIONS.md` as
wanting "its own release note and a downstream recompute", which was true and
already paid for: #184 and #194 force that recompute anyway, so the field was
free before the release and a second recompute after it. Ask what the batch
already costs before pricing a change against zero.

*Process.* An issue can be closed as COMPLETED without being fixed, and **a
closing keyword in prose has closed one nobody decided — four times**, the fourth
being the commit that warned about the other three. GitHub reads *"filed rather
than ‹keyword›: ‹number›"* literally and does not care that the sentence says the
opposite, nor that the substring sits in a quotation or a code span. So the rule
is **never reproduce the substring at all** — describe it, or drop the `#` — and
because the rule was written, read and then broken by one session, the real check
is after the fact: **after every merge that mentions an issue in prose, diff `gh
issue list` against what the commit says it filed and fixed.** That has now caught
a keyword that fired (#137, #142, #160) *and* four that did not (#147, #164,
#184, and #187/#190/#191 — each closed by hand a session late). Also, a mutation
harness restoring with `git checkout -- <file>` deletes whatever is uncommitted
in it.

## This session: #198, #202 and #203

Three of PR #195's eight follow-ups, taken together because they are one
question — what a stored result says about itself — and taken **now** because
the answer to that question is free while the batch is unreleased. The
arguments are in `CHANGELOG.md`, `ROADMAP.md`, `CLAUDE.md`,
`docs/manual/transparency.md`, three new `docs/DECISIONS.md` entries and at
each branch. What a next session should know concretely:

- **The ordering was the decision, not the code.** `TransparencyResult`'s
  schema is already moving unreleased (#161 added `full_text_status`, #184 and
  #194 already force a downstream recompute), so #198's *"wants its own release
  note and a downstream recompute"* — the reason PR #195 deferred it — costs
  nothing here and a second recompute after a release. **The cost of a schema
  addition is not a constant**; `docs/DECISIONS.md`'s own entry had priced it
  as one, and that is the correction worth carrying forward.
- **#203 was taken wider than filed, deliberately.** The issue names the
  search-failure branch; the other two COI-unknown lines have the same shape
  and the same consequence, so all three were split. That is the standing rule
  — the guard written on one branch is the guard the others need — and the
  cost is stated rather than discovered: it moves stored `risk_indicators` for
  every analysis that did not scan full text.
- **#202's fix is a deletion.** The issue offers a sentinel or a skip; the
  parameter being mandatory beats both, because `analyze()` was always the only
  caller and had the record in hand. Two other things followed from it —
  `_check_trial_registration` lost `pmid`/`doi`, and the sampler lost the
  analyzer instance it held only for this.
- **Six mutants killed, and the seventh taught something.** Adding a provenance
  line to the retraction set survives the whole suite — because the append
  happens after the retraction, so the mutant is *equivalent*. Two independent
  protections mean no single edit can reintroduce #203; breaking both together
  reddens exactly the three tests written for it. Do not read an equivalent
  mutant as an untested guard, and do not read it as a redundant one either.
- **#204 was filed from the work**: `trial_registered` is `False` both for a
  paper with no trial and for one whose sources never answered, which
  `NOT_REGISTERED` inherits. Its population is unmeasured and that is the
  blocking half.

**Five of PR #195's follow-ups remain**: #196 a second `User-Agent` built
inline in `publications/sync.py`, outside `_user_agent` and outside any
sampler; #197 the retryability grouping `FullTextStatus` names as the reason
two members exist and mechanises nowhere; #199 four JSON consumers calling
`.get()` on an unchecked shape, so a JSON list escapes the public `analyze()`
— the loudest of the five, and it wants a decision about whether the guard
belongs at `_request_json`'s boundary; #200 and #201 are shape.

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
- **Tests: 3383 passing + 63 skipped** (`uv run pytest tests/ -q`, measured
  2026-09-07 on this branch; `main` at PR #195's merge read 3360 + 63). The PostgreSQL half has not been re-run since the
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
  `ROADMAP.md` are promoted at release time; **105 lines carry one** — 39
  `ROADMAP.md` rows and 66 spots across `docs/manual/transparency.md` (32),
  `fulltext.md` (18), `publications.md` (13) and `templates.md` (3).
  Recounted 2026-09-07 on this branch as
  `grep -ric unreleased ROADMAP.md docs/manual/*.md`, so it counts *lines* and
  not markers; the figure is measured, not maintained, so recount rather
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

**Thirty-five open** as this file is written, **thirty-two once this branch
merges** (`gh issue list`, 2026-09-07): #86, #92, #94, #103, #124, #128, #137,
#142, #143, #144, #145, #150, #152, #154, #156, #157, #172, #173, #174, #175,
#177, #178, #179, #181, #186, #188, #196, #197, #198, #199, #200, #201, #202,
#203, #204. This branch answers **#198, #202 and #203**, and filed **#204**
from the work. **#193 and #194 were closed by hand at the start of this
session** — PR #195 merged and named them in prose only, the process rule
below catching its **seventh and eighth** instances; #187/#190/#191, #184,
#183 and #161 went the same way in the sessions before. Every open issue was
found by review or measurement rather than by a failing test, and **none loses
records** — though **#124** loses an exhibit's footnotes, **#150** renders a
note-only reference as an empty bullet, and **#128** would lose every figure
image in a document binding XLink to another prefix.

**Nothing in the list is urgent in the way #184 and #194 were**, so the next
choice is a judgement rather than a forced move. #186 and #188 are the last of
the full-text-refusal family and both are decisions rather than fixes (below);
#178 is the one open *question* rather than a defect; and of PR #195's review
**#199 is the loudest survivor** — a 200 carrying a JSON list raises
`AttributeError` out of the public `analyze()`, whose whole contract is that a
misbehaving API costs a component and not the analysis. It is unmeasured and
pre-existing, and it wants a decision about where the guard belongs: four call
sites, or one validation at `_request_json`'s boundary, which changes what that
helper promises. **The lesson of #194 is
worth acting on rather than only recording**: it was a live-only defect that
the whole suite missed — no test in it makes a live request — found the first time an
instrument presented bmlib's real identity to a real remote. Nothing else in
`transparency/` or `fulltext/` has ever been probed that way, so the *class*
is open even though the instance is closed.

**Count them against the repo before trusting that number.** The line has been
wrong in several sessions, and an issue closed as COMPLETED without being fixed
is invisible to any such count. Almost every open issue was filed *by* a PR
reviewing an earlier fix, so the provenance is a chain: **#119** → PR #159,
filing #158/#160/#161; **#160** → PR #182, filing #183; **#183**/**#161** → PR
#185, filing #184/#186/#187; **#184** → PR #189, filing #188/#190/#191, which
this branch answers — and it filed nothing. Older provenance is in `gh issue
view <n>` and `CHANGELOG.md`. (#149 and #152 were filed and fixed inside one
PR, so neither ever appeared as open work.)

Each issue carries its own argument on GitHub; what follows is only what a
session needs to *choose* between them. Almost none is a drive-by.

**The JATS corpus redraw is done** (#132, #138, #158 answered), so #142, #143
and #150 have a population — and **all three measure empty**, which blocks them
on a stratified draw rather than on effort. Both corpora are 1,000-article draws
at `seed 0`: recent from `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`
(2023-2025, 997 served), back-filled from `…PMC002xxxxxx…` (1996-1998, 1,000).
The sample is drawn from the package and the bytes measured from Europe PMC's
`fullTextXML`, the two renditions disagreeing on exactly the cited populations
(`last_is_thumb` differs in 156 of 300 compared articles, the archive measuring
0 against 781 served where it does).

**Three issues lose content the document carries, each blocked on a modelling
decision, not on effort.** **#124** — no exhibit model has a `footnotes` field,
so a `<table-wrap-foot><fn>`'s abbreviations and funding notes reach nothing
while `<sup>` is flattened into the cell (`12.3a` pointing at a note that
exists nowhere). **#150** — a `<ref>` whose only content is a `<note>` renders
as an empty `<li>`, 4 instances in one publisher. **#144** — whether
`<on-behalf-of>` is a name or an attribution.

**#142, #143 and #145 are the rest of PR #141's review**, in `jats_parser`'s
contributor and reference half: #143 is bare last-wins with no parent test and
no log (the #116/#127 class), #142 is a `<collab>`'s children running together
with no separator, #145 is `<aff>` resolution through `@id`.

**#137 has been auto-closed twice without being decided** — a section-level
`<caption>`'s `<p>` children reach `body_sections` while its `<title>` is
dropped, and the sampler records the `<title>`'s parent but not the
`<caption>`'s owner, so the population is not derivable yet.

**#152** — `<article-id>`'s reachability guard is `parent == "article-meta" or
self.in_front`, and each half deletes on its own with the suite green: the
parent test can only fire on markup JATS does not admit, while `in_front`
admits an `<article-id>` in `<notes>` as the article's own DOI. Unmeasured, so
it pairs with a draw; nothing is known to be wrong today, but this is where
#109 was.

**#128 is weaker than filed** — all 13,624 `<graphic>` hrefs in the two corpora
use the `xlink` prefix bound to the XLink namespace, so the literal-prefix
match is safe on measured evidence. Downgrade rather than close.

**#172–#181 are PR #171's, #176's and #180's leavings.** #178 is the group's
open *question*: whether LaTeX should win for a both-encoding inline formula at
all, replacing prose already correct in 20,046 formulas to recover 205. #177
routes a display formula reaching no section, caption or cell (192 in 23 of
97,909); #174 is MathML flattening losing spacing and brackets, now reaching
far more prose than before #147; #175 is a formula deposited as an image, which
no field carries; #172 is the cache having no version stamp to invalidate it
when a rendering change moves the HTML; #173 is a figure's `alt` duplicating
its own `figcaption`. #179 — the per-host pacer is per-process, so two
concurrent sampler runs double the rate against one host (103 and 98 articles
lost to refusals against 3 sequentially). #181 — `last_is_thumb` increments
only inside `len(graphics) > 1`, so its population is `figures_multi_graphic`
while the report and five files divide it by `figures_with_graphic`; over its
own population the recent window reads **99.3%**, not 57.3%, which makes #117's
rule far more load-bearing than the published figure says. One remedy restates
a share cited in five files and the other needs both corpora redrawn, which
would destroy #164's attribution.

**#186 and #188 are the last of the full-text-refusal family**, both in
`_fetch_europepmc_fulltext` and both a decision rather than a fix — which is
why #187/#190/#191 and then #193 were taken first. **#186**: the unclosed-region
refusal holds the element names in a stack and discards them at the return, so
neither the WARNING nor the stored `UNCLOSED_REGION` says whether a
`<sub-article>` or a `<response>` was left open. Naming one moves a
heavily-argued return contract — `_strip_nested_articles` returns `str | None`
and the `None` *is* the signal — so it wants a small result type or a dedicated
exception, the shape #160 chose one case over. **#188**: a `MED` record with no
`pmcid` is addressed by `record["id"]`, a bare PMID, so the request is made and
its 404 is known before it leaves; `NOT_SERVED` is now precisely right about
*what happened*, and `NOT_ATTEMPTED` is the honest thing to store for a
question not worth asking. It wants the population first — how many `IN_EPMC:Y`
records carry no `pmcid`, and `HAS_PMCID` is not a field, so it needs a draw.
The `PPR` fallback is load-bearing and pinned (deleting it loses the address
for all 75,760 preprints), so the test cannot be *"is it a PMCID?"* — the
record's own `source` is what distinguishes them.

**#154, #156 and #157 are one job, and it is the funder corpus.** #154:
`scripts/sample_funder_names.py` writes `tests/data/funder_names.raw.json`,
which is in no commit, so the repo holds the 417 labelled names and not the 816
they were drawn from — a count over the unlabelled remainder is unanswerable
and a redraw has nothing to diff against. The old draw is unrecoverable, so the
fix carries a decision: re-label the intersection, or commit a fresh draw as
the baseline for the *next* comparison and say so. #156 needs a draw stratified
for European funders; #157 a targeted `\bplc\b` draw, the general one having
found none in 816. Both risks are pinned by
`TestTheKnownFalsePositivesAreKnown` — keep the choice, file the measurement,
do not quote the reasoning as measured. **Any session extending a funder list
owes #154 first**, `docs/DECISIONS.md` requiring the sampler be run before
either list is touched.

**#103 — `install_defaults()` reserves no `NAME_MAX` headroom for the temp
name**, so a template named beyond ~217 characters fails with `ENAMETOOLONG`.
Left alone deliberately: the names come from the caller's own source tree and
the failure is loud. The fix is a docstring line, not a cap — capping renames a
caller's template and `render("<name>")` then does not find it.

**#94, #92 and #86 are the older non-JATS three.** #94 and #92 are the same
shape — a guard resting on an unmeasured quantity, argued in full in
`CLAUDE.md` under *A completed day is a durable claim* — and **neither may be
tightened without running the sampler it asks for**: #94 because one bioRxiv
error body is indistinguishable from a quiet day (the tests pin *both* possible
quiet-day shapes so the guard cannot come to depend on the unmeasured answer),
#92 because `SHORTFALL_FAILURE_RATIO = 0.5` is bmlib's only threshold not set
from a sampled population and a floor tightened past the real benign gap
re-fetches that day for ever. #86: `docs/manual/llm.md` documents
`LLMClient.generate` and `LLMClient.embed` twice each — not a delete, the
copies differing (the two `embed` sections disagree on the default model,
`embed_batch`'s being right).

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
