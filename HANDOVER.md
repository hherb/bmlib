# HANDOVER — bmlib development

_Last updated: 2026-10-17 (**PR open** for #145, branch
`fix/jats-author-affiliations-145`, worktree `../bmlib-aff`).
**0.10.0 is released and on PyPI**; everything below is unreleased, and **the
next release is 0.11.0** (the maintainer's choice, 2026-10-16: the unreleased
batch breaks the API, #470's `ValueError` among it). `main` is at f4b7c52:
PR #479 (#472, #478) is merged and both issues are closed. All five version
places agree at 0.10.0. Every unreleased ROADMAP row carries an
`*(unreleased)*` marker._

## What is unreleased, and what it costs a downstream

Well over fifty changes — count them against `CHANGELOG.md`'s `[Unreleased]`
at release rather than trusting a figure here — most of them `fulltext` JATS
fixes filed within days of each other — whoever cuts the next release should describe those
together. **Per-PR argument is in `CHANGELOG.md`; only the *data* answer is
kept here**, because the version number answers the API question and never
that one. Eight (#211, #212, #216, #417, #368, #217/#223, #459/#460, #462)
touch `scripts/` and its tests — #462 with comments and docs beside them —
and #292 test data alone; those nine cost a downstream nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored. **Since #172 bmlib's own cache re-fetches by itself**: every entry an
earlier bmlib rendered carries no stamp, so upgrading re-fetches each article
with a cached HTML or abstract entry at its next lookup, and again at each
later one for as long as its sources return less than it holds. **A downstream that stored the HTML itself should re-fetch**,
as should one calling `JATSParser` itself. Sixteen of them ride on one re-fetch and are the
largest by population, each diffed against `main`; a served figure is over the
8,118 articles of `PMC10030002_PMC10040000.xml.gz` unless another artifact is
named:

- **#145** — **`JATSAuthorInfo.affiliations` is filled** (it was always
  `[]`). Only that field moves: 7,578 of 8,118 served and 96,502 of 97,909
  archive articles, 96.1% / 96.9% of authors receiving one; HTML in **0**
  (it prints names only, #480). `RENDERER_VERSION` 3.
- **#224** — unsectioned `<back>` prose is no longer dropped: 5,990 articles
  (73.8%), 40,342 paragraphs, every move an insertion.
- **#231** — a container's deposited heading titles its unsectioned prose:
  `body_sections` alone moves, 4,783 served / 74,363 archive, 0 paragraphs or
  titles lost; two adjacent sections may share a heading (10 new pairs).
- **#257 / #284** — **new fields** `funding_statements` and `funding_awards`
  (`JATSFundingAward`, `JATSFundingSource`), rendered as a *Funding* section:
  1,367 / 3,066 served articles. #257's review declined `<institution-id>`
  everywhere (`body_sections` 358 / 1,700, abstracts 14 / 88).
- **#265** — **new** `elocation_id` on article and reference; HTML moves in
  5,399 served (66.5%) and 85,887 archive.
- **#124** — **new** exhibit `footnotes`, marker folded in: 3,707 served
  (45.7%), 16,935 notes.
- **#230/#234** — front-matter prose lands ahead of the body: 3,350 served /
  46,737 archive, every move an insertion; a funding line deposited front and
  back renders twice (108 / 2,984).
- **#254/#259/#152** — the own `title` and locators were another work's in
  notices: `title` moves in 95 served, 1,115 archive, 529 `PMC000`.
- **#243** — an inline table's cells no longer splice into the sentence:
  2,222 served (27.4%), 21,377 archive.
- **#228** — a `<def-list>` term folds into its definition: 840 served.
- **#241/#248** — object metadata no longer welds into prose and `<attrib>` is
  routed: HTML 584 served, 3,098 archive (18 graphical abstracts lose their
  section to the figure's notes).
- **#268** — a partly tagged `<mixed-citation>` renders its whole deposit:
  HTML 347 served / 5,578 archive; a `doi`-only reference loses its link.
- **#261** — `year` skips `*-submitted`/`*-release` dates: 183 served, 456
  archive. **#272** moves nothing (measured 0).
- **Cited names and #276** (PR #387) — a cited `<name>` stored no authors:
  31,143 served references (640 articles) / 522,232 archive gain names; HTML
  931 / 13,706. #264 moves nothing stored.
- **#390** (PR #394) — NLM 2.x `<citation>` is read: 1,134,249 served
  back-file references in 30,800 articles fill in (every one empty on `main`);
  the recent windows move 0.
- **#385** (PR #408) — `et al..` and doubled periods deleted from
  `formatted_citation` and the cached HTML: **223,334 served references in
  6,819 of 8,118 articles** (3,462,932 archive). No `JATSArticle` field moves.
- **#397** (PR #408) — a cited `<pub-id>` is read by its declared type:
  `references[].pmid` gains 11,225 back-file / 538 served PMIDs, loses 873
  archive / 40 served non-PMIDs, and corrects 203 back-file / 54 archive wrong
  ones. HTML moves only where the DOI does (11 served, 179 archive).
- **#406** (PR #410) — **new field** `JATSReferenceInfo.authors_truncated`
  from a cited `<etal/>`; `et al.` is printed after a truncated list. Moves
  **45,061 served references (2,475 articles)**, 439,057 archive.
- **#407** (PR #412) — a `<citation-alternatives>` group is one work: 239
  served references (11 articles) / 5,030 archive gain fields.
- **#413/#415** (PR #418) — the first complete cited page range wins: 22
  served references (18 articles), 153 archive, 224 back-file.
- **#423** (PR #427) — a footnote marker no longer ends the own `title`:
  **88 / 626 / 98** articles, every move a deletion.
- **#425/#429** (PR #433) — an `<xref>` or a member roster no longer welds
  into a name: `collab` moves in **23 served, 144 archive, 2 back-file**
  articles, 9 archive references lose a phantom author; every move a
  deletion, HTML in 2 / 22 / 1.
- **#391/#255** (PR #440) — a citation printed in prose (outside any
  `<ref>`) was cut from its sentence, and Wiley's front-matter self-citation
  `<p>` arrived empty. A typeset one now stays in the sentence. `html_content`
  moves in **237 served and 3,753 archive** articles, every move an insertion:
  `body_sections` 235 / 3,746 (231 / 3,782 paragraphs gained, 12 / 521
  changed in place, 0 lost), the abstract 2 / 169, one archive figure caption;
  no other field. `RENDERER_VERSION` 2, so a populated cache re-fetches.
- **#172** (PR #437) — the cache stamps rendered entries with
  `RENDERER_VERSION`; no field moves, but every cached HTML or abstract entry
  is re-fetched at its next lookup, which is what delivers the rows above.
- **#270/#267/#271/#258/#266** (PR #381) — another work's parts read as this
  work's: 2 of 136,570 articles move (#271's two archive notices).

Then, reasoned or measured on smaller draws (figures in `CHANGELOG.md`):
**#146/#149** (`citation` for 4,499 of 20,770 references), **#111** (author
lists empty for most open-access articles), **#115/#117**, **#147**, **#162**,
**#123/#125/#130**, **#127**, **#120/#140**, **#129**. **#238, #245 and #414
move nothing stored**; #238 and #245 add log lines where there was silence.

**#325 (PR #343) makes bioRxiv and medRxiv sync again, with a different
population**: published preprints only, filed under the publication date; the
first run after upgrading revisits every unsettled completed row and retries
every failed one. **#346 (PR #445) adds a column and moves what `sync()`
stores for a day whose re-fetch shrinks**: `download_days.peak_count`, empty on
upgrade and filled by the next fetch; a completed re-fetch below half the peak
is stored `failed` and retried, while the row is not final and the day is
inside `settle_days` + 30 days; a completed fetch below what the day was last
recorded at otherwise carries a `SyncReport.notes` line. No publication row
moves. A pre-upgrade row has no peak: a preprint row is held to nothing, and
**since #446 (PR #448) a PubMed, OpenAlex or other settle-0 row is held
to its own `record_count`**. **#447 (PR #448)** makes two syncs of one
day take turns, so a concurrent sync on SQLite is no longer refused for having
read first; a wait past the connection's busy timeout (5 s by default) still
raises. **#449 (PR #450) moves nothing stored**: `transaction()`,
`create_tables()`, and so `sync()` at its entry, raise `TransactionModeError`
(a `ValueError`) for psycopg2 with `autocommit` on, Python 3.12+ `sqlite3`
opened with `autocommit=` either way, or any other connection not reporting
`autocommit is False`, where they used to write non-atomically or never
commit; a block whose mode changed inside it raises `RuntimeError` on exit. **The extractor batch (PR #370) and #367 move nothing bmlib
stores** but move what a caller of `bmlib.quality.extractors` gets
(`find_sample_size` in 225 of 5,976 abstracts and 724 of 7,410 full texts —
the audit's full-text count, which the same rule re-derives as 715 (#368);
and since #367 `extract_study_type` in 628 of 5,954 abstracts and 378 of 7,410
full texts; the CHANGELOG lists the constants).

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

**Thirteen move stored *transparency* values.** Two are large enough that **any
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
points for a paper whose `cited_by_count` is malformed. **#457** moves nothing against 0.10.0 (`full_text_status` is itself
unreleased); against `main` it moves that field back from `REQUEST_FAILED` to
`NOT_SERVED` for a record listed not open access whose accession 500'd.
**#463** moves nothing a served body has been seen to produce: a PubMed body
decoding to a lone surrogate, or a full-text body httpx cannot decode, now
costs its component (the second stores the new refusal `UNDECODABLE`) where it raised out of
`analyze()`. **#470** makes `analyze()` raise `ValueError` for a caller PMID
that is not ASCII digits or is all zeros, where a PMCID passed as a PMID stored an unrelated
article's PubMed signals as this paper's; a downstream passing one now gets an
exception (no measured population). **#474** reads only the PubMed record naming
the PMID sent (else the new `pubmed_status` `other_record`), sends PMIDs
without leading zeros, and searches a PMID-only Europe PMC lookup within
`SRC:MED`, which moves the record read for a PMID that is also a numeric `ETH`,
`CBA`, `HIR` or `CIT` id (unmeasured; 1970s PMIDs in every collision probed).
**#472/#478** send `FullTextService`'s PMID canonical (a padded one now finds
the paper) and set an unusable one aside with a WARNING instead of sending it,
using a PMC ID passed as `pmid` as the `pmc_id`; any other refused value with
nothing else beside it then raises `FullTextError` where `main` returned a
PubMed link built from it (no measured population). **#227** adds the field `pubmed_status` to every result, and a
provenance line to `risk_indicators` for every determinate analysis whose
PubMed step read no record — a DOI-only analysis of a record without a PMID (a preprint
above all) and a book record among them; so **a downstream comparing indicator lists
sees a new line there**. **#160**, **#183**, **#202** and **#218** move nothing measurable; **#292** changes only the
test corpus and the documented recall (0.333 → 0.286, matcher untouched).

## Rules carried forward, and previous sessions

**Rules are in [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)**; read
[`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything. **Each
session has a ROADMAP row and a `CHANGELOG.md` entry**; **a PR body is the
record**. **Read PR #285 before the next front-matter change.**

**Last sessions** (argument in `CHANGELOG.md`; most rules are in
`docs/SESSION-RULES.md`): PRs #479 (#472/#478, `FullTextService`'s PMID
canonical and set aside rather than raised), #477 (#474, the PubMed record read is the one
asked about; books by the same rule), #473 (#470, PMID shape), #469 (#227, `pubmed_status`), #466 (**only a
`ValueError` or a declared non-text codec is the remote's bytes**), #464, #461, #458 (**record/replay a
sampler refactor**), #455, #452, and #437 (**every JATS PR trips `tests/test_renderer_version.py`** — bump
`RENDERER_VERSION` if `to_html()` can move, then re-pin). **Worktree recipe**: `git worktree add ../bmlib-x origin/main
-b <branch>`, then `uv venv .venv`, `uv pip install --python .venv/bin/python
-e ".[all,dev]"`, and run `env -u VIRTUAL_ENV uv run …`.

## This session: a contributor's affiliations (#145)

Branch `fix/jats-author-affiliations-145`, worktree `../bmlib-aff`; the
maintainer chose #145 from a list, and three design answers (2026-10-17): the
group + article inheritance rules, splitting a `<target>` block, and the field
only (no HTML, #480). A deposit survey over both artifacts came first
(placement, linking, separators, markers, alternatives), then a two-checkout
comparator: only `affiliations` moves. **The Rust port followed in the same
PR** (the maintainer's choice, PR #440's precedent), since the oracle went
stale: `ContribGroupFrame`, `BuiltAuthor`, the `<aff>` frames and
`resolve_affiliations`, plus `open_affiliations`; twenty-four
`inline/aff-145/*` cases join the JATS corpus (85 → 109), two staying
Python-only until the port has marked spans (#432 has a comment). 22 mutants,
20 killed after six fixtures; the `<sup>`-in-`<target>` guard was redundant
and is gone, the group-serial guard is a recorded equivalent. Filed **#480**
(render affiliations in the HTML?) and **#481** (strip a trailing `;`/`,`?).
**#475 is decided** (strip a DOI, blank counts as none, no shape check;
comment on the issue) and not yet implemented.

## The Rust port

A separate process ports bmlib to Rust under `rust/` (see `HANDOVER_RUST.md`).
**It does not touch the Python library** and may have uncommitted work in the
main checkout, so **work in a `git worktree`** there, never `checkout`/`stash`.
Its audit's #294-#325 are all done in Python (**after a merge, check the issues
a PR names were fixed in *Python***, #295 having been closed with only Rust
fixed). Open: the Rust side's #332, and **#314** (a `<mixed-citation>`
deposit glues name parts; #390's per-deposit rule is one answer). **A Python
change that adds an enum member or reads a new input field reaches the
oracle**: add the field to both case copies rather than regenerating
expectations the port cannot yet meet, and add the enum's wire name in Rust.

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
- **Tests: 6,651 passing + 79 skipped** on this session's branch
  (`uv run pytest tests/ -v`, 2026-10-17); measure `main` with `pytest
  --collect-only` and never subtract from a previous handover's number. The PostgreSQL half was last run in full for
  PR #448's branch (5,810 passed + 2 skipped); PR #450 ran
  `test_backends.py`, `test_db.py` and `test_sync.py` with it. Of the 79
  default skips, 77 are the PostgreSQL parameterisations, 1 a PostgreSQL-only
  schema test, 1 `test_pymupdf_requires_dependency`.
- **Run the PostgreSQL half locally — it finds real bugs.** Postgres.app 16
  runs on `localhost:5432` here (`createdb -h localhost -U postgres bmlib_test`
  once); set `BMLIB_TEST_POSTGRESQL_DSN="host=localhost port=5432
  dbname=bmlib_test user=postgres"` and `BMLIB_REQUIRE_POSTGRESQL=1`.
- **Documentation is kept current; treat drift as a regression.** The
  `unreleased` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release: **349 lines carry one** (2026-10-17, this session's branch, `grep -ric unreleased ROADMAP.md
  docs/manual/*.md`, summed; lines, not markers, so recount rather than adjust).
  Write the marker bare, never with a guessed version, and leave the ones in
  `docs/superpowers/plans/` alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup* (no workflow file), ignores
  a PR's `reopened` action, and does not constrain the merge strategy (#78).

## Next up

### The 0.11.0 release

The maintainer chose **0.11.0** (2026-10-16), not 1.0.0: below 1.0 a minor
bump is semantic versioning's channel for breaking changes, and 1.0 would
promise a stability the unreleased batch does not yet have. Before the
release PR, gather the API breaks scattered under `### Fixed` in
`[Unreleased]` under a `### Changed — breaking` heading (the CHANGELOG has
used that heading before): #470's `ValueError` from `analyze()`, #478's
`FullTextError` from `fetch_fulltext()` for a refused `pmid` given alone, the
`PubMedStatus` members and `pubmed_status` field, and the stored
`risk_indicators` moves. Then add a short "Upgrading to 0.11" note to the
README pointing at that heading. The release recipe is in the memory index
(five version files, release branch and PR, tag the merge commit); rehearse
the gates locally first.

### Open GitHub issues

**Eighty-five open** (`gh issue list --state open --limit 300`, 2026-10-17,
after PR #479 closed #472 and #478 and this session filed #480 and #481; this session's PR takes #145, so eighty-four once it merges). They are: the Rust audit's #314 (a
decision), the Rust side's #332, **#467** (follow #463's port parity), **#468** (follow #227), **#471** (follow #470), **#476** (follow #474), #409 (follow #406), #411 (follow #407),
#416 (follow #413/#415), #421 (follow #414), #426 (follow #423), #432 (follow #425/#429; now also #145's two Python-only fixtures), #436 (follow #172) and **#444** (follow #346), and the Python list: #92, #94, #128, #137, #142, #143, #144,
#150, #154, #156, #157, #173, #174, #175, #177, #178, #179, #197,
#201, #204, #207, #209, #212, #222, #233, #235, #240, #242,
#244, #245, #247, #249, #251, #252, #253, #260, #273, #275, #278, #279,
#281, #282, #283, #286, #287, #288, #290, #291, #341, #342,
#465 (three local transport classes stay `transport-`; a decision),
**#475** (a blank or padded DOI is sent as given; **decided 2026-10-17** —
strip it, blank counts as none, no shape check — and ready to implement),
**#480** (render affiliations in the cached HTML? a decision), **#481**
(strip an affiliation's trailing `;`/`,`? a decision),
#456 (a Rust regex-engine error still yields a determinate answer; a design
decision), #451 (label the phrase-search strata's protocols and
phase-specific trials, a decision), #453 (review wording the keywords miss,
precision unmeasured), #454 (full-text study typing reads a cited trial; a
design question),
#393 (an element-only citation whose text sits only in unread
children renders blank), #396 (those children's text reaches no field and no
counter), #419 and #420
(PR #418's review: count a replaced first page, count a partly tagged
`<string-name>`'s bare text — both "count it?" decisions), #424 (#224's
WARNING calls a citation note's `<p>` missing; measured 0), #430 (a
`<collab>`'s directly held `<email>`/`<ext-link>`/`<on-behalf-of>` welds into
its name; a per-element decision), #431 (a `contrib-type="collab"`
contributor, a consortium or its members, is not an author; a role
decision), #434 (a cited roster's members become cited authors ahead of
the group, and the `citation` string carries one spelling of a member and not
the other), #435 (a double space where a roster sits mid-name), **#439**
(an element-only citation printed in prose — every one an eLife dataset
citation — is counted but its DOI reaches no field; a decision), #441 (a
`<citation-alternatives>` group of two typeset renditions prints both) and
#442 (an element-only citation inside a related work in prose prints its
fields run together; invalid JATS), both decisions from PR #440's review.
Re-count against `gh`.

**Presentation decisions left**: **#279**, the half #231 could not reach —
front matter rarely deposits a heading (`<author-notes>` 25 of 2,444 served
blocks), so its prose still renders under `<h2>Abstract</h2>` in **2,899
served and 43,282 archive** articles. It needs a *rendering* answer, and the obvious one (closing
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

**Wrong values left**: in the reference arms, **#419** and **#420**, each a
"count it?" decision. In names, **#430** (19 archive `<collab>` children)
and **#431** (a role decision, 644 contributors in 12 archive articles).
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

**What still loses content the document carries**: **#439** (an eLife
dataset citation in a data-availability `<p>`, counted since #391 but
printed nowhere: 455 archive / 66 served). **#253** (a `<floats-group>`'s `<boxed-text>` panel reaches nothing —
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
population. **#245** and **#247** are the `<array>` pair. Every one is a
decision rather than effort.

**Measured-empty, want closing rather than building**: #204 and #207 (0 of
124). **#212** is why `sample_api_failures.py` exits 1 on a clean run.
**Instrument-side leavings**: #222, #197 (which members are transient), #201,
#179.

**JATS contributor and reference half**: #142, #143, #144. Formula family: #178 (the open
question), #177 (a float shape measuring 0), #174 (MathML flattening), #173
(a figure's `alt` duplicating its `figcaption`). **#154, #156 and #157 are one job, the
funder corpus** — any session extending a funder list owes #154 first.
#292's leftover, the ROADMAP's brand-layer
row, owes #154 too. **#94 and #92** may not be tightened without their
samplers.

**The instrument debt is real**: most sessions measure from scratch scripts
(the two-checkout comparator, the instrumented `_JATSHandler`,
#458's record/replay harness). The extractor draw has a committed one since #368;
**#453** is the next extractor change it measures.

### Worth doing, not yet an issue

- Widen bmlibrarian's `<0.6.0` pin; wire in the segmenter and extractors (abstracts only, until #454); feed the stored grants to `transparency/` (moves stored values).

### bmlibrarian → bmlib porting (Phase 3 is next)

Read [`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
first. Phase 3 is discovery (#12), `pubmed_search` (#13), MeSH (#21) and
ClinicalTrials.gov (#14), each a design conversation. **Port recipe**: TDD
against upstream, modernise, sever app coupling, export (PEP 562 for an extra),
verify, record in `CHANGELOG.md`; for a JATS rule read the Swift port's
`doc/cross_platform/jats_parsing.md`.

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
