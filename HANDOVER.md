# HANDOVER — bmlib development

_Last updated: 2026-09-26. **0.10.0 is released and on PyPI**; forty-nine
changes sit unreleased, four of them touching no library code. `main` is at
99b6977, the merge of PR #326 (Rust bioRxiv); this session's llm/agents batch
(#299, #300, #301, #302, #303, #308, #315) is PR #329, on `fix/llm-agents-port-audit`.
All five version places agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Forty-nine changes, thirty of them `fulltext` JATS fixes filed within
days of each other — whoever cuts the next release should describe those
together. **Per-PR argument is in `CHANGELOG.md`; only the *data* answer is
kept here**, because the version number answers the API question and never
that one. Three (#211, #212, #216) touch `scripts/` alone and #292 test data
and docs alone; those four cost a downstream nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored, so **a downstream holding cached full text should re-fetch**, not only
one calling `JATSParser` itself. Twelve of them ride on one re-fetch and are the
largest by population, each diffed against `main`; a served figure is over the
8,118 articles of `PMC10030002_PMC10040000.xml.gz` unless another artifact is
named:

- **#224** — unsectioned `<back>` prose (`<ack>`, `<notes>`, `<fn-group>`,
  `<app>`, `<glossary>`, `<bio>`) used to be dropped. Prose moves in 5,990
  articles (73.8%), every move an insertion: 40,342 paragraphs, 0 lost.
  `has_body`, `figures`, `.tables`, `references` and `abstract_sections` move
  in **0**.
- **#231** — unsectioned `<back>` and `<front>` prose arrived **untitled and
  merged**, the container's own deposited `<title>` having been dropped. Each
  container's heading now titles the prose its element holds. `body_sections`
  is the **only** field that moves — 4,783 of 8,118 served (58.9%) and 74,363
  of 97,909 archive (76.0%) — and `html_content` moves in **exactly** those,
  with **0 paragraphs gained, 0 lost and 0 titles lost**; 14,460 / 254,898
  headings recovered. A downstream rendering `body_sections` sees more
  sections, most now titled, and two adjacent sections may carry one heading
  where the document deposited two blocks (10 new pairs served).
- **#257** — a `<funding-statement>` reached no field. **New field**
  `JATSArticle.funding_statements`, rendered as its own *Funding* section
  after the body. It fills in exactly **1,367 (16.8%)** served and 42,295 of
  97,909 archive articles, and that change moved no other field. A statement
  the publisher repeats in its prose now renders twice (30 / 1,082). The
  review round then declined an `<institution-id>` (almost always a Funder
  Registry DOI) **everywhere**, which also cleans `main`'s identical weld in
  prose: `body_sections` moves in 358 served / 1,700 archive articles and
  abstracts in 14 / 88, every served change a deletion of an id. So the PR's
  HTML moves in the union of the two, which was measured per change and not
  pooled. A statement that is *not* the article's own now reaches no field
  and is counted, with one WARNING per article — invalid markup, measured 0
  on both artifacts, so no line fires today. **A fourth destination of that
  decline went unrecorded** and is corrected in `CHANGELOG.md`: `authors`
  moves in 1 served article (eLife's `PMC10032659`, ROR ids welded into a
  consortium's `<collab>`), found while measuring #284 against #285's own
  commit.
- **#284** — an `<award-group>`'s funder, Funder Registry id and award number
  reached no field. **New fields** `JATSArticle.funding_awards`, holding
  `JATSFundingAward(sources, award_ids)` and `JATSFundingSource(name,
  identifier)`, rendered as lines in #257's own *Funding* section. **It is the
  larger half of the funding disclosure**: it fills in **3,066 (37.8%)** served
  and 49,652 of 97,909 archive articles, of which **2,292 and 27,602 (28.2% of
  each) deposit no statement** and so gain a *Funding* section they never had.
  `html_content` moves in exactly those, every one of `main`'s lines kept, and
  no other field moves on either artifact. A funder tagged in ordinary prose
  (503 served / 2,595 archive articles) is untouched.
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

**The llm/agents batch (this session) moves nothing stored but changes
what four calls do**: `list_providers()` omits a built-in whose SDK is not
installed and `get_provider()` of one raises `ImportError` naming the extra
(#303); `chat_json()` raises at temperature 0, or retries above it, on
truncated output it used to return repaired (#300); `get_recent_records(-n)`
raises (#308); and several system messages all reach Anthropic (#315). A
downstream relying on any of the old answers — a repaired truncation read as
a complete result above all — should read the CHANGELOG entry.

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
**#202** and **#218** move nothing measurable; **#292** changes only the
test corpus and the documented recall (0.333 → 0.286, matcher untouched).

## Rules carried forward

**In [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)** — read it before
measuring, writing an instrument or arguing about a log level, and add a rule
a review teaches there rather than here.

## Previous sessions

**Each has a ROADMAP row and a `CHANGELOG.md` entry carrying the argument, the
measurements and the mutation result.** PRs #256, #263, #269, #274, #277,
#280, #285 and #289 (2026-09-14 to 09-20) were all `fulltext` JATS, measured
against the two named artifacts, and filed most of the open JATS list below;
**read PR #285 before the next front-matter change**. PR #293 (#292) copied
bmlibrarian_lite's re-audited funder labels byte for byte, moving only
recall's denominator (`MIN_RECALL` 0.28). **A PR body is the record**, not a
commit message or GitHub's squash text (two of PR #269's commits state claims
its later commits superseded), and every rule those reviews produced is in
[`docs/SESSION-RULES.md`](docs/SESSION-RULES.md).

## This session: the Rust audit's llm/agents batch (PR #329)

The maintainer picked the llm/agents group of the audit (#299, #300, #301,
#302, #303, #308, #315) over the quality, stored-value and fulltext groups.
`CHANGELOG.md` carries the argument; what a next session needs:

- **#299 and #300 cannot ship apart.** #299's stack made more truncated
  responses repairable, and #300's shortcut returned repairs as answers —
  four existing truncation tests (five items) passed only because their
  content was the shape #299 could not repair. `chat_json()`'s truncation
  path now asks `_try_parse_complete` (stages 1-2, no repair, no fragment).
- **#303's probe widened two neighbours, both fixed**: every `_get_client()`
  said *"not installed"* for an import that fails, now reached mostly by a
  *broken* SDK (it reports the exception now); and `get_provider_info()`
  raised for an SDK-less built-in, the case its `setup_instructions` exist
  for (found by the correctness review; it builds from `_builtin_class`).
  `register_provider()` also runs the built-ins first and lowercases, the
  fix `register_source()` already had.
- **One deliberate difference from the Rust port** (#315): Python skips an
  empty system message, Rust appends the separator first, so `["A", ""]` is
  `"A"` here and `"A\n\n"` there. Raised in the PR, not filed.
- **Mutation**: 25 mutants, 24 killed; the survivor (popping any opener on a
  mismatched closer) is an equivalent, stated at the site. The harness lives
  in the session scratchpad only.
- **Worktree**: work in one (`git worktree add ../bmlib-x`), install with
  `uv pip install --python .venv/bin/python -e ".[all,dev]"` and run
  `env -u VIRTUAL_ENV uv run …` — `VIRTUAL_ENV` points at the main venv.

## The Rust port, and the audit it filed against Python

A separate process is porting bmlib to Rust under `rust/` (PRs #321, #322,
#324, #326, merged 2026-09-26). **It does not touch the Python library** — its
brief is to leave it alone and file what it finds — and it may have
uncommitted work in the main checkout, so **work in a `git worktree`**, never
`git checkout`/`stash` there. Its analysis and the list of Python defects it
fixes rather than reproduces are in
[`docs/plans/2026-09-26-rust-port-roadblocks.md`](docs/plans/2026-09-26-rust-port-roadblocks.md).
Its audit filed **#294-#325** against Python, grouped:

- **llm / agents** — #299, #300, #301, #302, #303, #308, #315: done this
  session, closed by this PR.
- **quality / cochrane type narrowing** — #310, #312, #317, #318, #319, #320:
  one rule (a reader does not narrow to the annotated type), fix together.
- **Small wrong stored values** — #306 (UNKNOWN stores `coi_disclosed=True`),
  #307 (an unreadable CrossRef `message` stores *"No funder information"*),
  #313 (a boolean OpenAlex `meta.count`), #296 (a blank author inline).
- **fulltext** — #304 (a malformed `pmc_id` suppresses Tier 1b), #305 (a
  cached-PDF hit drops the held-back abstract), #309 (cache-key double-hash;
  a directory served as a PDF).
- **extractors** — #294 (digit-grouped sample size), #297 (negation-blind
  bonuses), #298 (priority over evidence); standalone today.
- **Decisions, not fixes** — **#325 is urgent**: bioRxiv's `/details` serves
  an empty 200, so **every Python bioRxiv sync day fails**; `/pubs` works but
  is a *different population* (published preprints only), so the maintainer
  must choose among the issue's three options. PR #326 moved Rust only; #323 is
  the superseded first report. #314 (a `<mixed-citation>` deposit glues name
  parts) wants a separator decision measured against a survey. #316 is Rust's.

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
- **Tests: 4,403 passing + 63 skipped** on this branch (`uv run pytest
  tests/ -v`, 2026-09-26), collecting 4,466; `main` at 99b6977 passes 4,344
  + 63 skipped.
  Measure `main` yourself with
  `pytest --collect-only` and never subtract from a previous handover's number
  — this bullet and a PR's own were stale by exactly one review round's tests
  until PR #274's review read them together. **The PostgreSQL half was not re-run and did not
  need to be** (`llm/`, `agents/` and documentation only); the last measured figure
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
  `ROADMAP.md` are promoted at release time; **186 lines carry one** on this
  branch, recounted 2026-09-26 as
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

**Ninety-nine open** (`gh issue list --state open --limit 300`, 2026-09-26;
**ninety-two once this PR merges**), the Rust audit's #294-#325 grouped in
the section above plus the older list:
#86, #92, #94, #103, #128, #137, #142, #143, #144, #145, #150, #154,
#156, #157, #172, #173, #174, #175, #177, #178, #179, #181, #186, #196, #197,
#200, #201, #204, #207, #209, #210, #212, #214, #215, #217, #221, #222, #223,
#226, #227, #233, #235, #240, #242, #244, #245, #247, #249, #251, #252,
#253, #255, #258, #260, #264, #266, #267, #270, #271, #273, #275,
#276, #278, #279, #281, #282, #283, #286, #287, #288, #290, #291.
Re-count against `gh`.

**Presentation decisions left**: **#279**, the half #231 could not reach —
front matter rarely deposits a heading (`<author-notes>` 25 of 2,444 served
blocks), so its prose still renders under `<h2>Abstract</h2>` in **2,899
served and 43,282 archive** articles (corrected on the issue from a pooled
3,447 / 47,528). It needs a *rendering* answer, and the obvious one (closing
the abstract in `_build_html`) moves `html_content` for every article carrying
an abstract rather than only the affected ones. **#281** is the same kind of
question for the bibliography: a `<ref-list>`'s own heading reaches nothing
and a fixed *References* is printed. **#282** and **#240** are one *nesting*
decision — an umbrella heading shadowed by an inner container's, and a
sectioned container's heading (206 served / 1,691 archive, still dropped
uncounted, `<fn-group>` 85-87% of it) — and may want deciding together.
**#283** is a heading-*content* question beside them: an `<xref>` inside a
`<sec>` title reaches the cached `<h2>` as literal Markdown (`fig`/`table`, 51
xrefs) or a welded footnote marker — 134 titles in 51 of 8,118 served
articles, older than #231, 0 among the container headings it recovers.

**Wrong values left**: **#276**, the residual PR #277 left — a *pair* that
names no work (`authors`+`year`, 841 served / 15,028 archive references),
which needs a second claim rather than a wider reading of the count. **#258** (a `<bio>` name replaces the author's; 0) and
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
**#257 and #284 are both done** (PR #285, PR #289). What #284
leaves is **#288**, an award's `<principal-award-recipient>` — 2,243 elements
in 968 served and 23,450 in 9,445 archive articles, roughly one article in
eight — which needs a shape decision (plain string, own model, or
`JATSAuthorInfo`), with `<award-name>` (2 archive) and `<issue-sponsor>`
(1 / 45) riding on it. **#260** is #257's small neighbour (`<custom-meta>` statements, `<subtitle>`),
and after #284 it is the largest front-matter loss left. PR #285's second
review round filed two more, both **measured 0 on both artifacts** and so
prospective: **#286**, an `<index-term>`'s `<term>` taking
`definition_terms_dropped` and #228's WARNING, which is a false diagnostic
rather than a loss (`<index-term>` is absent from all 97,909 archive and
8,118 served articles); and **#287**, a statement's own `<fn>` routed to
front-matter prose ahead of the body with its marker dropped, which is #124's
question one container over. The literal-`<xref>` half of the Funding section
is **#283**'s, commented there rather than filed twice. This PR's own review
filed two more on the same field, both **measured 0 on both artifacts**:
**#290**, a `<funding-source>` wrapping two `<institution-wrap>` welding their
names into `'NIHNCI'` and dropping the second registry id — an *invented*
funder, so it wants the wrap to become the funder unit rather than a patch,
which reaches affiliation parsing too — and **#291**, `identifier` holding a
Funder Registry id in two spellings (2,697 bare against 2,369 URL-wrapped
served, 27,149 against 23,632 archive), so every consumer folds them itself.
#291's docstring half is done on this branch; what is filed is the
`funder_registry_id` property, deferred to the first real consumer.

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
population. **#245** and **#247** are the `<array>` pair. **#231 is done** (PR #280); what it leaves is **#279** above.
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
funder corpus** — any session extending a funder list owes #154 first.
#292's leftover, the ROADMAP's brand-layer
row, owes #154 too. **#103**
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

- Coding rules live in `CLAUDE.md` under *Coding Conventions*; the standing
  rules a session gets wrong again live in
  [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md), and a rule a review
  teaches is added there rather than here.
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
