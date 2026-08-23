# HANDOVER — bmlib development

_Last updated: 2026-08-23. **0.10.0 is released and on PyPI**; ten changes
sit unreleased on `main` — #73's atomic template install (PR #102), #96/#105's
partitioning of an over-cap PubMed day (PRs #106 and #114), #109's typed
article-id (PR #113), #110/#111's JATS sub-article and contributor-group
fixes (PR #118), #115/#116/#117/#131's exhibit nesting, ranking and
sampler (PR #126), #127's image-only table (PR #133),
#123/#125/#130/#135's owner-routed title and caption (PR #136),
#134/#121/#129's end-of-parse audit (PR #139), #120/#140's undivided
contributor name (PR #141) and #146/#149's mixed-citation text (PR #148,
merged 2026-08-23) — plus **this session's #151, on
`fix/151-endelement-buffer-read-invariant`**, which adds no behaviour. All
five version places agree at 0.10.0.
Nine of the eleven are `fulltext` JATS fixes filed within
days of each other; whoever cuts the next release should describe them
together. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker.

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

**They reach a bmlib path through the cached HTML** — a claim this file had
backwards twice. `_build_html` renders authors, figures and tables into one
string that `FullTextService` caches via `parse_with_html()`, and
`body_sections`/`abstract_sections` reach it the same way (#125's argument one
branch over). Nothing *structured* is stored — `FullTextResult` has no author
field and `publications` takes its authors from the fetchers — but a
downstream holding cached full text should re-fetch, not only one calling
`JATSParser` itself.

**Rules carried forward from PRs #133 through #148**, each argued in full in
`CLAUDE.md` and at its call site, so only the shortest form is kept here.

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; a stratified sample of recent deposits is still one window (#127
reads 0 of 662 recent and 11 of 93 in a 1996-1998 draw). A number in a comment
goes stale silently and coherently — `TestTheCitedPopulationsAreWhatTheCorporaHold`
is the answer. A rule can be spec-driven and still owe an instrument. An
instrument's vocabulary has to be open or it certifies (#121's
mis-certification inside the tool built to detect the next #120). And state a
blast radius **from a diff, not from the call graph**: PR #148 reasoned
soundly from a false premise, and four review agents missed what two parses
over 880 articles showed in minutes.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding. When a fix extends a routing rule, walk every other path it
reaches — the guard written on one branch is the guard the others need, and
the same rule stated in prose on one branch is not applied on the next. Read
the rules *next to* the one you are adding before calling a fix one line. A
stack of frames needs the entries it will not use. **A set keyed on the
element cannot express a rule about the context** (`_INLINE_ELEMENTS` was
right for #120 and wrong for #146), so ask whether the candidate belongs in
the set or the set is the wrong shape. And **suppressing a merge does not
empty a buffer** — only an accumulating child ever withheld anything.

*Diagnostics and tests.* A diagnostic's *level* is a claim that has to be
measured, and a detector must report what it *checked*, not what it concluded.
A net needs its own false-positive net, and it must be free — the autouse
`parser_log` fixture makes all 186 pre-existing fixtures one. Key a counter on
*structure*, never on the routing it is checking. **A rule enforced by prose
is not enforced** (`TestTheAuditNetIsComplete`, and now
`TestOnlyAnAccumulatingElementReadsTheBuffer`), and it demands a *choice*
rather than a field. Write the case the rule *decides*. Tell a vacuous green
from one asserting silence — ask which line of the fixture the assertion
depends on. **Mutate the *old* half of a condition you extend**: dropping a
guard the new condition composes with passed all 2,699 tests. An issue can be
closed as COMPLETED without being fixed — so **diff `gh issue list` against
the merged commit's own "filed" list**. And a mutation harness restoring with
`git checkout -- <file>` deletes whatever is uncommitted in it.

**This session settled #151** — the prospective half of
`_inside_mixed_citation`, mechanised. That helper argues its strict-ancestor
slice is currently harmless with a whole-method claim: no arm of `endElement`
reads `text` for an element outside `_TEXT_ACCUMULATING`. True when written,
tied to nothing, and #142 is exactly the change that breaks it. No behaviour
moves; `TestOnlyAnAccumulatingElementReadsTheBuffer` walks the arms with `ast`
and reports every read of the popped buffer with the element names reaching
it. Six things worth carrying forward.

- **Every outcome of a net must fail closed, including "I could not tell".**
  A guard the walker cannot read is a finding in its own right, never a read
  passed over — a walk that reports only what it understands is the vacuous
  green the issue predicted. The same rule gives the raise: *no reads* must
  not be an answer the walker can return for a class or method it cannot
  find, or one restructure turns four tests green at once.
- **Ask for containment, not overlap.** The likeliest breakage is an existing
  arm gaining an element, not a new arm appearing — `name in ("collab", ...)`
  acquiring `"institution"` is #142 almost exactly. An overlap test passes
  that silently, and it was the one mutation in the class that *lost* a
  violation rather than inventing one.
- **A control must not be judged against production data it does not own.**
  Four synthetic controls asserted that `<institution>` does not accumulate —
  which is precisely what #142 is entitled to change, so the day it does they
  fail for the opposite of their own reason. Found by running the *permitted*
  change end to end, not by reading them. They now carry their own
  accumulating set; only the real handler is judged against the real one.
- **Verify a net in both directions on the real file.** The violating arm
  added verbatim to `jats_parser.py` is reported by line and element; the arm
  *plus* its `_TEXT_ACCUMULATING` membership goes green. Twelve tests and 16
  mutants of the walker say the machinery works; only those two say it decides
  the right thing.
- **"Move the pop up" had to name how far.** The issue's own claim — that
  `test_the_citation_string_is_what_the_publisher_typeset` pins the
  strict-ancestor slice — is true only for a pop moved *above* the buffer pop
  at the top of `endElement`, since that is where `_inside_mixed_citation` is
  evaluated. A pop moved anywhere below it reddens 58 tests and leaves all
  four citation tests green. Measured, not reasoned; the first mutation I ran
  was in the wrong place and quietly proved nothing.
- **A guard that decides nothing is worth finding.** The same measurement
  showed `<article-id>`'s reachability guard reddening *nought* tests, and
  both halves of `parent == "article-meta" or self.in_front` turn out to be
  individually deletable with the whole suite green — while not being
  equivalent to each other. Filed as **#152**.

**A closing keyword in prose closed an issue nobody decided — three times.**
A commit body saying *"filed rather than ‹keyword›: ‹number›"* is read
literally by GitHub, which closes the issue seconds after the merge and does
not care that the sentence says the opposite, nor that the substring sits in a
quotation, a blockquote, a code span or bold markers. #137 went that way twice
— the second time in a PR body *quoting the first in order to warn about it* —
and #142 the third, closed by the commit that *filed* it, so it was born
closed and appeared in no count of what that session left open.

So the rule is not "phrase it carefully" but **never reproduce the substring
at all** — describe it, or write the number without its `#`, in commit
messages, PR bodies and any quotation of either, which is why this paragraph
names neither. And after every merge that mentions an issue in prose, diff
`gh issue list` against the commit's own list of what it filed.

**Next up: #124, #128, #137, #138, #142–#145, #147, #150 and #152 in
`fulltext`, #119 from the #118 review, #132, the older non-JATS ones (#86,
#92, #94, #103, #112), or Phase 3 of the bmlibrarian port, whose every row
needs a design conversation.** **#124, #144, #147 and #150 all lose
content the document carries**, and all four are blocked on the same kind of
decision — what to attach a footnote marker to, which contributor spelling to
extract, how to delimit LaTeX in prose, whether a note-only `<ref>` is a
reference at all — so none is a drive-by. (An earlier revision of this line
named only #124 and #147, which was wrong twice: #144 is a contributor the
document names and bmlib does not collect, and #150 is a reference rendered as
an empty bullet. #149 was a fifth and is fixed in this PR — its decision came
from measuring the population, which is the move the other four are still
waiting on.) #142, #143, #147 and #150 all
want a population measured before a rule is picked, so they pair with the
redraw below; #144 and #145 are extraction, each behind a design question the
issue states. #152 is the one that needs no draw to *start* — both halves of one guard
are unpinned and the suite cannot tell them apart — though picking between
them wants a population, so it pairs with the redraw. #132 and #138 both want
a corpus redraw and should be paired — with the further reasons that PR #141's
sampler counters are in no committed draw, and #142, #143, #147 and
#150 each want a population from them.

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
- **Tests: 2718 passing + 63 skipped on `main`**, **2730 + 63** on
  `fix/151-endelement-buffer-read-invariant` — 12 in
  `TestOnlyAnAccumulatingElementReadsTheBuffer` in `test_jats_parser.py`
  (`uv run pytest tests/ -q`, measured 2026-08-23). The
  PostgreSQL half
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
  release time; **53 are outstanding for the next release** — 23 `ROADMAP.md`
  rows and 30 spots across `docs/manual/publications.md` (13), `fulltext.md`
  (14) and `templates.md` (3). Recounted this session with
  `grep -ic unreleased`, which is one more in `fulltext.md` than the previous
  count carried — a reminder that this figure is measured, not maintained. Grep case-insensitively for `unreleased` rather
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

**Nineteen open** as this file is written (verified with `gh issue list`,
after filing #152 from this session's own measurement): #86, #92, #94, #103,
#112, #119, #124, #128, #132, #137, #138, #142, #143, #144, #145, #147, #150,
#151, #152; eighteen once this PR lands #151. Every one
was found by review or measurement rather than by a failing test, and **none
of them loses records** — though **#124** loses an exhibit's footnotes,
**#147** loses a formula from the prose that contains it, **#150** renders a
note-only reference as an empty bullet, **#128** would lose every figure image
in a document binding XLink to another prefix, and **#119** feeds a scan text
that is not the article's. Count this
against the repo before trusting it: the line has been wrong in three
consecutive sessions, and one further way is an issue **closed as COMPLETED
without being fixed**, which no count of open issues catches — that has now
happened three times by the mechanism described below, and the third victim
was an issue the same commit *filed*. (**#56, #68,
#72 and #79** shipped in
0.9.1. **#78, #81, #88–#91, #95, #98 and #99** shipped in 0.10.0 — PRs #85,
#87, #93, #97, #100. **#73** is on `main` unreleased in PR #102, whose own
review filed **#103**. **#96** closed with PR #106, as correct rather than as
fixed. **#105 and #107** closed with PR #114. **#109** closed with PR #113.
**#110 and #111** closed with PR #118, whose review filed **#119**, **#120**
and **#121**. **#115, #116, #117 and #131** closed with PR #126, which filed
**#123**, **#124**, **#127**, **#128**, **#129** and **#130**. **#127**
closed with PR #133, which filed **#132**, **#134** and **#135**; **#123**,
**#125**, **#130** and **#135** closed with PR #136, whose review
filed **#137** and **#138**. **#121**, **#129** and **#134** closed with PR
#139, whose review filed **#140**. **#120** and **#140** closed with PR #141,
whose review filed **#142**–**#146**. **#146** and **#149** closed with PR
#148, which filed **#147**, **#149**, **#150** and **#151**. **#151** closes
with this session's PR, which filed **#152**.)

**#151's own filing is the counter-example to the count above.** PR #148 filed
#149 and fixed it in the same PR, so it never appeared as open work; #152 is
this session's equivalent. Neither is lost, but neither is visible in a
"filed minus closed" arithmetic either — read the per-PR list, not the total.

**#128 is weaker than filed**: all 2,397 `<graphic>` hrefs in the two redrawn
corpora use the `xlink` prefix bound to the XLink namespace, so the
literal-prefix match is safe on measured evidence. Worth downgrading rather
than closing — no sample proves no publisher does otherwise.

**#132 is smaller than it was.** Both corpora were redrawn with every counter
present, so every figure a comment cites is re-derivable from the repo and
`TestTheCitedPopulationsAreWhatTheCorporaHold` keeps it that way. What remains
is the 276-article draw itself: #115's "0.7%, both eLife" and #117's
49.9%/49.5% cite a corpus not in the repo, and **nesting measures 0 in both
committed draws**, so that figure has no in-repo evidence at all. Do it before
the release that ships these rules, while the CHANGELOG is still free to edit.
#138 wants a redraw too, PR #141's `_CONTRIB_SIDE_COUNTERS` are in no
committed draw, and #142/#143 each need a population section 11 would now
measure — five reasons for one redraw. Pair them.

**#137 and #138 came out of PR #136's own review**, and #137 has since been
auto-closed twice without being decided (mechanism above). #137: a
section-level `<caption>`'s `<p>` children still reach `body_sections` while
its `<title>` is now dropped, so one caption's halves go different ways — a
decision to make (keep, drop both, or model the containers), and the sampler
records the `<title>`'s parent but not the `<caption>`'s owner, so the
population is not yet derivable. #138: `measure_article()` walks into
`<sub-article>`, which the parser suppresses, so every counter is a
whole-document count — measured harmless for the cited population (69 outside,
0 inside), but the fix is scope *and* redraw, since scoping alone leaves the
committed corpora unre-derivable.

**#124 — table and figure footnote prose is dropped entirely.** Neither
exhibit model has a `footnotes` field and nothing collects one, so a
`<table-wrap-foot><fn>`'s text — abbreviation expansions, per-table funding
notes — reaches nothing, while `<sup>` is flattened into the surrounding cell,
so the rendered table reads `12.3a` with the note it points at existing
nowhere. #116's fix discards the marker, right only because there is nothing
to attach it to; once there is, hold it and prefix it (`"a — Adjusted for
age."`), since with two footnotes the mapping is otherwise unrecoverable.
Needs the model decision first, so not a drive-by.

**#142–#145 are what is left of PR #141's own review**, all in the
contributor and reference half of `jats_parser`; #146 closes with this
session's PR. **#143** — several `<collab>` in one `<contrib>`, or a
`<name-alternatives>`, are bare last-wins with no parent test and no log, the
#116/#127 class; measure the multiplicity before picking first-wins,
last-wins or a ranking. **#144** and **#145** are extraction behind a design
question each: whether `<on-behalf-of>` is a name or an attribution, and
`<aff>` resolution through `@id` from `<article-meta>`. **#142** is a
`<collab>`'s `<institution>`/`<addr-line>` children running together with no
separator — and is the closing-keyword mechanism's third victim, closed by the
commit that filed it and reopened 2026-08-23.

**#147 — a `<tex-math>` formula is dropped from the prose containing it, and
a `<disp-formula>` from the article outright.** #146's shape one context over,
found by walking the other paths its merge rule reaches:
`<inline-formula><tex-math>y = mx + b</tex-math></inline-formula>` renders as
`'The model is throughout.'` — and a MathML formula is unaffected, so the
sentence reads as ordinary prose rather than as a gap. The `<disp-formula>`
half is worse: buffer popped, no handler, so neither the equation nor the
`(1)` body prose cites reaches `body_sections` or the cached HTML. Not a
drive-by and deliberately not one more member of `_INLINE_ELEMENTS`:
`<tex-math>` is LaTeX source, so merging it raw leaves a reader nothing to
tell it was ever markup, and `<alternatives>` may hold both encodings of one
formula, which merging would emit twice. **Scoped to prose outside a
citation**, since #146's ancestor test merges `<tex-math>` inside one like
anything else, making both consequences live there — but that path measures 0
of 10,671 `<mixed-citation>` across 227 articles and 0 in the local corpus, so
it is unexercised rather than a second live defect.

**#150 is what is left of PR #148's own review** — a `<ref>` whose only
content is a `<note>` renders as an empty `<li>`, 4 instances in one
publisher. Its sibling #149 was fixed inside that PR (a `<ref>` may carry
several citation elements; measurement picked the rule — 216 such references
in 21 of 880 local articles, 0 using `<citation-alternatives>`), and #151
closes with this session's.

**#152 — neither half of `<article-id>`'s reachability guard is pinned, and
they are not equivalent.** `parent == "article-meta" or self.in_front` decides
whether the identifier is read at all, and each half can be deleted on its own
with all 2730 tests green. They are not the same rule: for valid markup
`<article-meta>` is inside `<front>`, so the parent test can only fire on
markup JATS does not admit, while `in_front` is the wider of the two and
admits an `<article-id>` deposited anywhere in `<front>` — in `<notes>`, say —
as the article's own DOI. Whether that ever happens is unmeasured, so the
choice wants a population and this pairs with the redraw. It matters because
`<article-id>` is where #109 was: the typed/fallback rules that fixed SAGE's
`publisher-id` are carefully argued and well tested, and the reachability
guard in front of them is neither. No behaviour is known to be wrong today.

**#119 — `TransparencyAnalyzer` scans raw JATS XML, so `<sub-article>`
reviewer prose still reaches its COI, funding and data-availability scans.**
It regexes `resp.text` directly and never imports `bmlib.fulltext`, so #110's
suppression does not touch it. Measured on two articles in the PR #118 review:
6 and 5 reviewer `competing interest` hits against the article's own.

**#112 — stated measurements that do not reproduce against the committed
corpus**, the same failure as #132 but one layer deeper.
`bmlib/transparency/analyzer.py` gives a measurement as the reason for each
funder stem it includes and excludes, and three disagree with
`tests/data/funder_names.json` (headline pair 0.917 / 0.324 claimed against
0.909 / 0.333 measured; `pharmaceutic` claimed clean and holding the matcher's
*only* false positive; `co` excluded for a collision the corpus lacks). Not
drift — one corpus commit, byte-identical matcher, and the figures are
self-consistent with a 34-entry industry set where this one holds 30, so they
were taken against a revision never committed. No behaviour regression.
Re-derive every figure in one pass, and name the corpus revision in each
comment.

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
