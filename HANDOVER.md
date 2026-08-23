# HANDOVER — bmlib development

_Last updated: 2026-08-23. **0.10.0 is released and on PyPI**; nine changes
sit unreleased on `main` — #73's atomic template install (PR #102), #96/#105's
partitioning of an over-cap PubMed day (PRs #106 and #114), #109's typed
article-id (PR #113), #110/#111's JATS sub-article and contributor-group
fixes (PR #118), #115/#116/#117/#131's exhibit nesting, ranking and
sampler (PR #126), #127's image-only table (PR #133),
#123/#125/#130/#135's owner-routed title and caption (PR #136) and
#134/#121/#129's end-of-parse audit (PR #139) — plus **this session's
#120/#140, on `fix/120-140-undivided-author-names`**. All
five version places agree at 0.10.0.
Seven of the nine unreleased changes are `fulltext` JATS fixes filed within
days of each other; whoever cuts the next release should describe them
together. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker.

**Six of them move what a caller of `JATSParser` gets, and every one of those
six moves what a bmlib *sync* stores.** #111 populates an author list that was
empty for the majority of open-access articles. #115/#117 change
`JATSArticle.figures` and `.tables` — figures that were missing now appear,
and a figure's `graphic_url` changes from a thumbnail to the full image for
roughly half of all figures. #127 adds `JATSTableInfo.graphic_url` and fills
it, so a table that came back with no content now carries its image.
#123/#125/#130 route a section's `title` and an exhibit's `caption` by their
owning element, so `body_sections` moves for roughly one recent article in
ten. #129 is narrower: an article lost to a malformed `colspan` now parses.
This session's #120/#140 collect a contributor whose name arrived undivided —
a contributor named undivided (34 of the 1,025 open-access articles drawn
in the PR #118 review, 3.3%, lost at least
one) and a `<string-name>` one (an article deposited that way lost *all* of
them).

**All six reach a bmlib path, through the cached HTML** — a claim this file
had backwards twice, first saying `authors` reached nothing and then saying
`figures` and `tables` do. `_build_html` renders `h.authors` as
`<p class="authors">` from `a.full_name`, and renders `h.figures` and
`h.tables` into the same string; `FullTextService` calls `parse_with_html()`
and caches it. So #111, #115, #117, #120, #127 and #140 all change what a
*sync* stores, and `body_sections` and `abstract_sections` reach it the same
way, which is #125's argument one branch over. `FullTextResult` still
has no author field and `publications` still takes its authors from the
fetchers, so nothing *structured* is stored — but a downstream holding cached
full text should re-fetch, not only one calling `JATSParser` itself. The
stored values are not comparable across the upgrade either way.

**Seven rules carried forward from PRs #133 and #136**, all argued in full in
`CLAUDE.md` and at their call sites, so only the shortest form is kept here. A
rule's population can be large, empty, or both, and only a draw says which.
When a rule replaces a guard, ask what else that guard was holding. A number
in a comment goes stale silently and coherently, which is why
`TestTheCitedPopulationsAreWhatTheCorporaHold` exists. Two enumerations can
both miss a container nobody listed. An issue can be closed as COMPLETED
without being fixed (see below, which now has a mechanism, twice). Write the
case the rule *decides*, not one that merely exercises the path. And a
stratified sample of recent deposits is still one window — #127's population
reads 0 of 662 in the recent draw and 11 of 93 in a 1996-1998 one.

**PR #139 (#134/#121/#129) put a net under the class the previous five
sessions kept finding**, and its six lessons are argued in full in `CLAUDE.md`
and at the call sites, so only the shortest form is kept here. A diagnostic's
*level* is a claim that has to be measured. A net needs its own
false-positive net, and it must be free — the autouse `parser_log` fixture
makes all 186 pre-existing fixtures one without being written as one. Key a
counter on *structure*, never on the routing it is checking. A rule enforced
by prose is not enforced, which is why `TestTheAuditNetIsComplete` exists — it
forced the accounting decision for this session's two stacks the moment they
existed. It demands a *choice*, not a field: its exclusion sets are name
lists, so nothing ties a member to a `ParseUnwindState` field.
A detector must report what it *checked*, not what it concluded. And a test
can pass before its code exists, vacuously (a bug) or because it asserts
silence (the point); tell those apart before reading a green as evidence.

**This session settled 120 and 140** — the two spellings of a contributor's
name that give one undivided string. The fix is in `CHANGELOG.md`; six things
are worth carrying forward.

- **The fix's own review found two silent regressions, both in the paths it
  touched but did not guard.** A `<string-name>` that *divides* put a bare
  `","` in `references[].authors` ahead of the name it belongs to — the
  contributor branch carried exactly that guard and the reference branch did
  not — and two divided siblings collapsed onto the last of them, which
  predated the work. And making `<string-name>` inline so a `<mixed-citation>`
  keeps a name it prints appended every roster member to the enclosing
  `<collab>`'s own name, in the very shape #120 exists to collect. Both landed
  in the HTML `FullTextService` caches, both passed the full suite. **When a
  fix extends a routing rule, walk every other path that rule reaches** — the
  guard you wrote on one branch is the guard the others need.
- **The same rule stated in prose on one branch is not applied on the next.**
  The comment added for `<string-name>`'s inline merge described a defect
  `<collab>` had all along, one line above it in the same set: consortium-
  authored references were losing their author from the rendered citation.
  A comment arguing for a rule is a place to check the rule's other instances.

- **The same defect shape three times, and the module had already been caught
  by all three.** A single slot where the markup nests (#115's exhibits), a
  list appended at the close where the element opened (#115's slots), and a
  text buffer read from the ancestor rather than the element (the
  `_TEXT_ACCUMULATING` idiom). None of the three was in the issue; each was
  found by asking what the *neighbouring* rules already knew. Read the rules
  next to the one you are adding before deciding a fix is one line.
- **A stack of frames needs the entries it will not use.** A `<contrib>` bmlib
  does not collect still pushes a `None` frame, and `current_author` reads the
  *top* of the stack rather than the nearest entry holding a builder. Both are
  one-line edits away from wrong, both survived the first mutation run, and
  one fixture — an editor nested inside an author's `<collab>` roster — kills
  both. The population that distinguishes them is exactly the one no fixture
  had.
- **A rule can be spec-driven and still owe an instrument.** JATS says the
  name is undivided, so no draw changes what to do with it — #140's "measure
  first" asks about *reach*, not about the rule. The sampler gained the
  counters (section 11) and was deliberately **not** re-run: a run redraws both
  committed windows from today's strata, moving every figure cited in
  `CLAUDE.md`, and would bake in #138's scope defect. So no rate exists for
  `<string-name>`, and none should be quoted — nor for `<collab>`, since #120's
  own 3.3% counted `<contrib>` elements carrying no `<surname>`, a set both
  spellings share. Seven sites had re-labelled that figure as `<collab>`'s.
- **An instrument's vocabulary has to be open, or it certifies.** Section 11
  counted spellings against a closed frozenset while its comment claimed the
  opposite, so an unforeseen spelling fell into `(none)` and was reported as a
  contributor naming nobody — #121's mis-certification inside the tool built
  to detect the next #120. `<on-behalf-of>` is the live instance: a fourth
  JATS spelling, unextracted (#144), which reached the *quiet* branch of the
  zero-author detector until it was added to `front_contributor_name_count`.
- **A mutation harness that restores with `git checkout -- <file>` deletes
  whatever is uncommitted in it.** It ate this session's sampler edits.
  Commit first, or hold the original source in the harness and write it back.

**A closing keyword in prose closed an issue nobody decided — twice, and the
second time it was the warning about the first.** PR #136's filing commit said
*"filed rather than ‹keyword›: ‹number›"* of #137; GitHub read the
keyword-and-number substring literally and closed it one second after the
merge, while #138, in the same sentence with nothing in front of it, stayed
open. It was reopened 2026-08-22 with a comment explaining the mechanism — and
PR #139's body then **quoted that sentence in order to warn about it**, closing
#137 again two seconds after *that* merge. Reopened 2026-08-23.

The parser does not care that the substring sits in a quotation, a blockquote,
a code span or bold markers, nor that the sentence says the opposite. So the
rule is not "phrase it carefully" but **never reproduce the substring at
all** — describe it, or write the number without its `#`, in commit messages,
PR bodies and any quotation of either, which is why this paragraph names
neither. And check `gh issue view` after every merge that mentions an issue in
prose.

**Next up: #124, #128, #137, #138 and #142–#146 in `fulltext`, #119 from the
#118 review, #132, the older non-JATS ones (#86, #92, #94, #103, #112), or
Phase 3 of the bmlibrarian port, whose every row needs a design conversation.**
#124 and #146 are the two open issues that still lose content — #146 is the
rendered citation string, where every structured field is fine. #124 needs a
model decision first. #142 and #143 both want section 11 to measure a
population before a rule is picked, so they pair with the redraw below;
#144 and #145 are extraction, each behind a design question the issue states. #132 and #138 both want a corpus redraw and should be paired — with a
third reason now, since this session's sampler counters are in no committed
draw.

This file briefs the next session on what is done, what is still open, and the
conventions to keep. Update it whenever a session materially changes the plan;
delete sections that are finished and no longer instructive. Per-PR detail
lives in git history, `CHANGELOG.md` and `docs/plans/` — not here.

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
  first run. The two questions are independent — 0.9.1 is a *patch* bump that
  moves something stored, 0.9.0 and 0.10.0 are *minor* bumps that move nothing
  — so the version number answers the API question, never the data one, and a
  downstream reading only the number must still read this list.
- **Tests: 2644 passing + 63 skipped on `main`**, **2679 + 63** on
  `fix/120-140-undivided-author-names`, so 35 are this session's — 7 in
  `test_fulltext_models.py`, 17 in `test_jats_parser.py`, 2 in
  `test_parse_audit.py` and 9 in `test_jats_exhibit_sampler.py`
  (`uv run pytest tests/ -q`). All measured, not derived. The PostgreSQL half
  was **not** re-run and does not need to be — this branch touches no SQL; the
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
  release time; **49 are outstanding for the next release** — 21 `ROADMAP.md`
  rows and 28 spots across `docs/manual/publications.md` (13), `fulltext.md`
  (12) and `templates.md` (3). Grep case-insensitively for `unreleased` rather
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
  2026-08-13). The old recipe required a release PR be merged with `--merge`,
  enforced by prose alone, and 8 of 40 merged PRs were not honouring it.
  Rather than disable squash and rebase repo-wide — deliberate habits, and
  GitHub cannot condition the merge method on the branch — the requirement was
  removed: `main`'s tip is on `main`'s first-parent line under every strategy,
  so the recipe tags the tip after pulling, guarded by two checks (see
  "Cutting a release"). Squash away.

## Next up

### Open GitHub issues

**Thirteen open** as this file is written (verified with `gh issue list`,
after reopening #137 for the second time), eleven once this PR lands 120 and
#140: #86, #92, #94, #103, #112, #119, #124, #128, #132, #137, #138. Every one
was found by review or measurement rather than by a failing test, and **none
of them loses records** — though **#124** loses an exhibit's footnotes,
**#128** would lose every figure image in a document binding XLink to another
prefix, and **#119** feeds a scan text that is not the article's. Count this
against the repo before trusting it: the line has been wrong in three
consecutive sessions, and one further way is an issue **closed as COMPLETED
without being fixed**, which no count of open issues catches — #137 has now
been closed that way twice, by the mechanism described above. (**#56, #68,
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
#139, whose review filed **#140**. **#120** and **#140** close with this
session's PR.)

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
#138 wants a redraw too, and this session's `_CONTRIB_SIDE_COUNTERS` are in no
committed draw — three reasons for one redraw. Pair them.

**#137 and #138 came out of PR #136's own review**, and #137 has since been
auto-closed twice without being decided (mechanism above). #137: a section-level
`<caption>`'s `<p>` children still reach `body_sections` while its `<title>`
is now dropped, so one caption's two halves go different ways — a decision to
make (keep, drop both, or model the containers), and the sampler records the
`<title>`'s parent but not the `<caption>`'s owner, so the population is not
yet derivable. #138: `measure_article()` walks into `<sub-article>`, which the
parser suppresses, so every sampler counter is a whole-document count.
Measured harmless for the cited population (69 outside, 0 inside), but the fix
is scope *and* redraw — scoping alone leaves the committed corpora
unre-derivable.

**#124 — table and figure footnote prose is dropped entirely.** Neither
exhibit model has a `footnotes` field and nothing collects one, so a
`<table-wrap-foot><fn>`'s text — abbreviation expansions, per-table funding
notes — reaches nothing, while `<sup>` is flattened into the surrounding cell,
so the rendered table reads `12.3a` with the note it points at existing
nowhere. #116's fix discards the marker, right only because there is nothing
to attach it to; once there is, hold it and prefix it (`"a — Adjusted for
age."`), since with two footnotes the mapping is otherwise unrecoverable.
Needs the model decision first, so not a drive-by.

**#119 — `TransparencyAnalyzer` scans raw JATS XML, so `<sub-article>`
reviewer prose still reaches its COI, funding and data-availability scans.**
It regexes `resp.text` directly and never imports `bmlib.fulltext`, so #110's
suppression does not touch it. Measured on two articles in the PR #118 review:
6 and 5 reviewer `competing interest` hits against the article's own.

**#112 — stated measurements that do not reproduce against the committed
corpus**, the same failure as #132 above but one layer deeper.
`bmlib/transparency/analyzer.py` gives a measurement as the reason for each
funder stem it includes and excludes, and three disagree with
`tests/data/funder_names.json`: the headline pair reads 0.917 / 0.324 and
measures 0.909 / 0.333, `pharmaceutic` is claimed to have no false positive
and has the matcher's *only* one, and `co` is excluded for a collision the
corpus does not contain. Not drift — the corpus has one commit (be456a2), the
matcher is byte-identical since, and the figures are self-consistent with a
corpus holding 34 industry entries where this one holds 30, so they were taken
against a revision never committed. No behaviour regression. Re-derive every
figure in one pass rather than fixing the three named, and name the corpus
revision in each comment.

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
  extra with its settings in `pyproject.toml`. Take no arguments — the bare
  command is what the `types` CI job runs — and run it in the dev venv: every
  extra but psycopg2 ships its own `py.typed` (that one is covered by
  `types-psycopg2`), so against a bare interpreter mypy reports the optional
  imports *and `jinja2`, a core dependency*, as missing stubs, which is why
  #81 opened claiming 24 errors when there were 22. Anything deliberately
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
  **annotated** tags, so `git rev-parse vX.Y.Z` returns the tag object's SHA,
  and a comparison against a commit SHA fails for a reason that has nothing to
  do with the release. Dereference it — `git rev-parse 'vX.Y.Z^{commit}'`
  (verified both ways on v0.9.1).
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
