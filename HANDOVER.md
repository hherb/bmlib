# HANDOVER — bmlib development

_Last updated: 2026-10-01 (session end; **PR #408 open**). **0.10.0 is released and on
PyPI**; everything below is unreleased. `main` is at 9c78b39: PR #394 (#390,
the NLM 2.x `<citation>`) merged 2026-09-29, and the Rust port's PRs
#398-#405 since, none of which touch the Python library. This session took
**#385** (`et al..`), **#397** (a declared six-digit cited PMID refused) and
**#386** (bioRxiv's stale `/details` docstrings), on branch
`fix/jats-etal-pmid-385-397`, worktree `../bmlib-etal`; see *This session*.
All five version places agree at 0.10.0. Every unreleased ROADMAP row carries
an `*(unreleased)*` marker._

## What is unreleased, and what it costs a downstream

Well over fifty changes — count them against `CHANGELOG.md`'s `[Unreleased]`
at release rather than trusting a figure here — most of them `fulltext` JATS
fixes filed within days of each other — whoever cuts the next release should describe those
together. **Per-PR argument is in `CHANGELOG.md`; only the *data* answer is
kept here**, because the version number answers the API question and never
that one. Three (#211, #212, #216) touch `scripts/` alone and #292 test data
and docs alone; those four cost a downstream nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored, so **a downstream holding cached full text should re-fetch**, not only
one calling `JATSParser` itself. Sixteen of them ride on one re-fetch and are the
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
  `JATSArticle.funding_statements`, rendered as its own *Funding* section:
  1,367 (16.8%) served and 42,295 archive articles. Its review declined an
  `<institution-id>` everywhere, moving `body_sections` in 358 / 1,700 and
  abstracts in 14 / 88 (every served change an id deleted), and `authors` in
  one served article (`PMC10032659`). A statement repeated in prose renders
  twice (30 / 1,082).
- **#284** — an `<award-group>`'s funder, Funder Registry id and award number
  reached no field. **New fields** `JATSArticle.funding_awards`
  (`JATSFundingAward`, `JATSFundingSource`), rendered in #257's *Funding*
  section: 3,066 (37.8%) served and 49,652 archive articles, of which 2,292
  and 27,602 gain a *Funding* section they never had. No other field moves.
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
  for an IRENA report). A wrong value in the rendered reference list: HTML
  moves in 347 served and 5,578 archive articles (828 / 15,748 references);
  no other field moves. A `doi`-only reference gives up its `<a href>` (96
  served / 3,157 archive).
- **#261** — the article's `year` was the first `<pub-date>` deposited
  whatever its type, so PMC's `nihms-submitted` (a manuscript reaching NIH)
  and `pmc-release` (an embargo lifting) could be the stored year. A **wrong
  value**: it moves in **183 (2.3%)** served and 456 of 97,909 archive
  articles, 0 in the two back-filled packages, and to blank in none; no other
  field moves. **#272** — an empty repeated `<fpage>`/`<volume>`/`<issue>` no
  longer blanks the article's value; measured 0, so it moves nothing.
- **Cited names and #276** (PR #387) — a `<name>` deposited directly
  in a citation stored no authors. `authors` now gains names in **31,143
  served references (640 articles) and 522,232 archive (10,038)**, every
  move an addition, and a mononym `<name>` is its own author. #276's rule
  prints the deposit where nothing names the work: 873 served (312 articles)
  / 16,276 archive (5,186), #276's own figures to the unit. HTML moves in
  **931 served and 13,706 archive articles**; no other field moves. #264 moves
  nothing stored: the zero-author WARNING goes 169 → 1 on the archive.
- **#390** (PR #394) — an NLM 2.x `<citation>`, most of PMC's
  back-files, was read by nothing, so each reference rendered as an empty
  `<li>`. The structured fields now fill in **1,134,249 served references in
  30,800 of the 55,543 served back-file articles (PMC0–PMC1999999)**, 81,629
  in 2,296 of 3,028 `PMC000` articles and 624,186 in 16,600 of 27,515
  `PMC001` articles. Every move is a gain, every moved reference rendered
  empty on `main`, and `html_content` moves in exactly those articles. The
  `citation` string is written only where the deposit is typeset (31,028
  served). One served figure caption gains the tagged parts of a `<citation>`
  printed in it. The recent windows move 0.
- **#385** (this session) — both reference renderers printed `et al..`,
  `Nat Commun.. (2020)` and `safe?. Lancet`. Only deleted periods move, in
  `formatted_citation` and the cached HTML: **223,334 served references in
  6,819 of 8,118 articles**, 877,855 in 38,719 of 55,543 served back-file
  articles, and 3,462,932 in 92,394 of 97,909 archive articles. No field of
  `JATSArticle` moves. A downstream holding cached HTML re-fetches for this
  alone in most articles.
- **#397** (this session) — a cited `<pub-id>` is read by its declared type.
  `references[].pmid` gains 11,225 back-file and 538 served PMIDs (declared,
  six digits or fewer) and **loses numbers that were never PMIDs** (873
  archive `publisher-id`/`isbn`/`arxiv` values, 40 served Hindawi
  `publisher-id`s). **A wrong PMID is corrected** in 203 back-file references,
  a MEDLINE UI having replaced the real one, and in 54 archive references,
  where a `pii` number had been stored. `pmid` is never rendered, so HTML
  moves only where the DOI does: 11 served and 179 archive articles, a `pii`
  spelling giving way to the declared DOI.
- **#270/#267/#271/#258/#266** (PR #381) — another work's parts read as this
  work's. Diffed after merge over all four artifacts (136,570 articles, 0
  uncomparable), **2 move**: #271's two archive notices (`PMC12105076`,
  `PMC12180358`), one `body_sections` paragraph each repaired in place
  (`titled “,”` regains the title) and `html_content` with it. 0 served.

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

**#325 (PR #343) makes bioRxiv and medRxiv sync again, with a different
population**: published preprints only, filed under the publication date; the
first run after upgrading revisits every unsettled completed row and retries
every failed one. **The extractor batch (PR #370) moves nothing bmlib
stores** but moves what a caller of `bmlib.quality.extractors` gets
(`find_sample_size` in 225 of 5,976 abstracts and 724 of 7,410 full texts;
the CHANGELOG lists the constants).

**Three Rust-audit batches move values without a corpus to size them**
(per-change detail in `CHANGELOG.md`): **PR #347** — every stored `UNKNOWN`
transparency row's `coi_disclosed` goes `True` → `None` (#306), a malformed
CrossRef body's indicator changes (#307), a boolean count fails its day
(#313), and inline citations drop blank authors (#296);
`sync(recheck_days=True)` now raises. **PR #333** — Tier 3 `null` replies
are classified rather than replacing a conclusive Tier 1 result, booleans stop
reading as sample sizes or confidences, and `from_dict` narrows stored rows.
**PR #329** moves nothing stored but changes four calls — `list_providers()`
omits an uninstalled SDK, `chat_json()` refuses a repaired truncation,
`get_recent_records(-n)` raises, every system message reaches Anthropic — and
a downstream relying on a repaired truncation should read its entry.

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

## Rules carried forward, and previous sessions

**Rules are in [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)**: read it
before measuring, writing an instrument or arguing about a log level; read
[`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything. **Each
session has a ROADMAP row and a `CHANGELOG.md` entry** with the argument, the
measurements and the mutation result. PRs #256-#289 (2026-09-14 to 09-20) were
`fulltext` JATS; **read PR #285 before the next front-matter change**. **A PR
body is the record**, not a commit message or GitHub's squash text.

**Last sessions** (argument and measurements in `CHANGELOG.md`): PR #394
(NLM 2.x `<citation>`, #390; **a content model is not the deposit** — PMC
deposits the mixed-content `<citation>` element-only, so its string is
written per deposit; filed #393, #396, #397), PR #387
(cited names, #276, #264; **a guard can widen the defect next door** — a
mononym flush at every close split Wiley's two-group editors; filed #385),
PR #383
(PR #381 measured after merge: 2 of 136,570 articles move; **`Closes #a, #b`
closes only `#a`**), PR #381 (JATS owner tests), PR #379 (Python issue sweep;
#209 and #227 keep their schema halves open), PR #370 (extractors; **measure
denials on full text as well as abstracts**), PR #347 (small wrong stored
values; **an `int()` call accepts a boolean too**), PR #343 (bioRxiv `/pubs`,
#325); older ones are in `CHANGELOG.md`. **Worktree recipe**: `git
worktree add ../bmlib-x origin/main -b <branch>`, then `uv venv .venv`, `uv pip
install --python .venv/bin/python -e ".[all,dev]"`, and run `env -u VIRTUAL_ENV
uv run …`.

## This session: `et al..`, cited PMIDs, bioRxiv docs (#385, #397, #386; PR #408)

Branch `fix/jats-etal-pmid-385-397`, worktree `../bmlib-etal`.

- **#385 was wider than its title.** A survey of the 337,548 served
  references rendering from fields found the doubled mark after `et al.`
  (168,800), a source ending `.` (98,131), a title ending `.`/`?`
  (13,294/4,835) and an initial (2,598). So one shared
  `_join_citation_parts` serves both renderers, and the HTML one reads past a
  closing tag. A deposited trailing `,`/`;`/`:` (540) is deliberately left
  alone (DECISIONS). The survey and the comparator agree to the unit.
- **#397 turned into a modelling decision once surveyed.** Every cited
  `<pub-id>` declares a type, and the shape test was storing Hindawi
  `publisher-id`s, ISBNs and `pii` numbers as PMIDs. **The maintainer chose
  type-first with no number guessed, then revised the `medline` half** after
  the recent `PMC012` archive measured 1,011 real PMIDs deposited under
  `medline` alone (the first choice was made on the 67 served recent ones).
  The split is by era, not rendition: `PMC001`'s archive deposits carry MUIs.
  A `medline` number now fills an
  empty PMID only, which still fixes the 202 back-file MUI overwrites. **Ask
  again when a second rendition changes the numbers a decision was made on.**
- **#386**: `/details` was re-probed live (2026-10-01, 207 declared against
  `/pubs`' 34). Eight documents were corrected to the past tense, two test
  docstrings among them. No code changed.
- **Filed #406** (a cited `<etal/>` is read by nothing: up to 45,074 served
  references render a truncated list as complete) and **#407** (an untagged
  first alternative in `<citation-alternatives>` discards the rest: 184
  served references, the 2 served short PMIDs still unread).
- Mutation: 33 mutants (9 for #385, 24 for #397), all killed. The two
  first-sweep survivors were unmade decisions, now pinned.
- The comparator (`compare.py`), the surveys and `pmid_detail.py` are
  scratch, like their predecessors: **the twelfth session measuring without a
  committed instrument**.
- **The Rust port follows in this PR** (the maintainer's choice). Its corpus
  pinned #385 and #397 as reproductions, and Python CI's oracle step
  (`scripts/rerun_rust_oracle.py`) fails on a stale corpus. So a Python fix to
  a pinned reproduction cannot go green without touching `rust/`, which is new
  since #394. `cargo fmt`/`clippy` (both feature sets)/`doc`/`test` are clean.
  `HANDOVER_RUST.md` carries a dated note for the Rust session. **Run the
  oracle script before opening a JATS PR**: the correctness review, not the
  local gate, found this.

## The Rust port, and the audit it filed against Python

A separate process ports bmlib to Rust under `rust/` (PRs #321, #322, #324,
#326-#328, #330, #331; see `HANDOVER_RUST.md`). **It does not touch the Python library**
— its brief is to leave it alone and file what it finds — and it may have
uncommitted work in the main checkout, so **work in a `git worktree`**, never
`git checkout`/`stash` there. Its analysis and the list of Python defects it
fixes rather than reproduces are in
[`docs/plans/2026-09-26-rust-port-roadblocks.md`](docs/plans/2026-09-26-rust-port-roadblocks.md).
Its audit filed **#294-#325** against Python, and every group is done:
llm/agents (PR #329), quality narrowing (PR #333; **after a merge, check the
issues a PR names were fixed in *Python***, #295 having been closed with only
Rust fixed), small wrong stored values (PR #347), fulltext (PR #355), the
extractors (PR #370) and bioRxiv `/details` (PR #343; follow-ups #341, #342).
The Rust side's #332 is open; #356, #388 and #395 are closed. **#314** (a
`<mixed-citation>` deposit glues name parts) is a decision left, and #390's
per-deposit rule is one answer it could adopt (see `docs/DECISIONS.md`).

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
- **Tests: 5,049 passing + 65 skipped** on this session's branch
  (`uv run pytest tests/ -v`, 2026-10-01), against 5,068 collected on `main`
  at 9c78b39. Measure `main` yourself with `pytest
  --collect-only` and never subtract from a previous handover's number. The PostgreSQL half was last run for PR #343
  (`tests/test_backends.py` 125 passed + 1 skipped); this session touched no
  SQL. Of the 65
  default skips, 63 are the PostgreSQL parameterisations, 1 a PostgreSQL-only
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
- **Documentation is kept current; treat drift as a regression.** The
  `unreleased` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release: **255 lines carry one** (2026-10-01, this session's branch, `grep -ric unreleased ROADMAP.md
  docs/manual/*.md`, summed; lines, not markers, so recount rather than adjust).
  Write the marker bare, never with a guessed version, and leave the ones in
  `docs/superpowers/plans/` alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup* (no workflow file), ignores
  a PR's `reopened` action, and does not constrain the merge strategy (#78).

## Next up

### Open GitHub issues

**Sixty-nine open** (`gh issue list --state open --limit 300`, 2026-10-01,
after PR #394 took #390, and #356, #388 and #395 were shut on the Rust side;
#376 was shut as fixed by PR #355; then #406 and #407 filed). They are: the Rust audit's #314 (a decision), the
Rust side's #332, and the Python list: #92, #94, #128, #137, #142, #143, #144,
#145, #150, #154, #156, #157, #172, #173, #174, #175, #177, #178, #179, #197,
#201, #204, #207, #209, #212, #217, #222, #223, #227, #233, #235, #240, #242,
#244, #245, #247, #249, #251, #252, #253, #255, #260, #273, #275, #278, #279,
#281, #282, #283, #286, #287, #288, #290, #291, #341, #342, #346, #367, #368,
#385 (`et al..`), #386 (a stale bioRxiv docstring), #391 (a citation printed
in a `<p>` outside a `<ref>` is cut out of the sentence), #393 (an
element-only citation whose text sits only in unread children renders blank),
#396 (those children's text reaches no field and no counter) and #397 (a cited
PMID of six digits or fewer refused by shape), and this session's **#406** (a
cited `<etal/>` read by nothing) and **#407** (`<citation-alternatives>`
first-wins). This session's PR takes #385, #386 and #397, which leaves
**sixty-six** once it merges. Re-count against `gh`.

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

**Wrong values left**: #276 is done (PR #387), and #258, #266, #267, #270 and #271 are done (PR #381).
#385 and #397 are done (this session). **#406** (a cited `<etal/>` read by
nothing) and **#407** (a `<citation-alternatives>` group read as several
works) are its findings, and each needs a shape decision.
**#273** is a decision rather than a wrong value: which *publication* date
`year` should be, the electronic one or the issue's, sized at 255 of 8,118
served and 742 of 97,909 archive articles for the first and 364 / 2,566 for
the second. **#275** is the one *silent* wrong value left — four single slots
set at a start tag and cleared at the matching close, so a nested element
defeats them with the accept branch firing; 0 instances in the four artifacts,
so it pins a direction.
**The funding field's leftovers** (#257 and #284 are done, PRs #285 and
#289): **#288**, an award's `<principal-award-recipient>` (968 served / 9,445
archive articles), needs a shape decision, and `<award-name>` and
`<issue-sponsor>` ride on it. **#260** (`<custom-meta>` statements,
`<subtitle>`) is the largest front-matter loss left. Four more measure **0 on
both artifacts** and are prospective: **#286** (an `<index-term>`'s `<term>`
counted as a definition drop), **#287** (a statement's own `<fn>` detached
into front matter), **#290** (two `<institution-wrap>` welded into one
invented funder, which wants the wrap to become the funder unit) and **#291**
(two spellings of a Funder Registry id; the property is deferred to the first
consumer).

**What still loses content the document carries**: **#255** (a Wiley
self-citation `<p><mixed-citation>` in front matter, dropped with no line, 231
served). **#253** (a `<floats-group>`'s `<boxed-text>` panel reaches nothing —
925 runs in 192 archive articles — and its `<sec>` is an empty heading after the
body; a position decision). **#249** (an exhibit's second-language caption;
its latent abstract-erasing half is done with #266). **#242** (`<inline-graphic>` has no handler). **#251**
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

**Measured-empty, want closing rather than building**: #204 and #207 (0 of
124); #210 is closed. **#212 qualifies every sampler share** — it is why
`sample_api_failures.py` exits 1 on a clean run.

**Instrument-side leavings**: #217 and #223 (one sweep: derive
`ProbeOutcome.cause`, and `StrEnum` the four vocabularies — #215 added two
cause kinds, which strengthens #217's case), #222, #197 (the choice of which
members are transient is the whole issue), #201, #179.

**JATS contributor and reference half**: #142, #143, #144, #145. Formula family: #178 (the open
question), #177 (a float shape measuring 0), #174 (MathML flattening), #173
(a figure's `alt` duplicating its `figcaption`), #172 (the cache has no version
stamp — every unreleased JATS change above is why that matters; any stamp in
the filename or a sidecar is a cache-key change the Rust port mirrors, #356). **#154, #156 and #157 are one job, the
funder corpus** — any session extending a funder list owes #154 first.
#292's leftover, the ROADMAP's brand-layer
row, owes #154 too. **#94 and #92** may not be tightened without their
samplers.

**The instrument debt is real**: eleven sessions have measured from scratch
scripts (the two-checkout comparator, the instrumented `_JATSHandler`, the
drop-site tally) and the extractor draw has none either (#368).

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** (read the non-comparable changes first);
  **wire in** the segmenter and extractors (a design conversation each; #367
  first); **feed the stored grants to `transparency/`** (moves stored values).

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

**TDD** (upstream is the spec, watched failing); **modernise** (AGPL header,
builtin generics, `datetime.UTC`) and **sever app coupling** (injected
connections, optional deps behind `ImportError`, LLM calls via `bmlib.llm`);
**export** (PEP 562 `__getattr__` for an extra, #64), **verify** (tests, both
ruff commands, mypy), **record** in `CHANGELOG.md`, **reconcile rather than
fork**; for a JATS rule read the Swift port's `doc/cross_platform/jats_parsing.md`.

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
