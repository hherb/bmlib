# HANDOVER — bmlib development

_Last updated: 2026-11-14 (**#767 and #768, the library's kind-aware lookup and
durable save, on `feat/library-find-kind-durable-save` — read *This session*
first**). Merged since the last update: #764 (the library, 1c), #763 (1b),
#760 (#341, the `*-details` sources), #757 (#753), #754 (the library, 1a),
#752 (#750, #751). **0.13.0 is on PyPI** (tag `v0.13.0` on `161849c0`, the
merge of #736) and **the Rust crate 0.4.0 on crates.io** from the same
commit._

## This session: a file found by its kind, and a durable save (#767, #768)

StudyAnalyzer's step 2 filed both; each was worked around there with a
`shortcut:` that can go once this is pinned. Both languages and bmlib-ffi.

- **#767**: `find_digest(scan, sha256, kind)` (Python `kind=None`, Rust
  `Option<SourceKind>`, FFI `find_digest(sha256, kind)`). The root cause was
  wider than the lookup: a record's entries were keyed by digest alone, so the
  second kind of the same bytes was written but never recorded, `release` of
  either kind dropped both, and `parse_record` refused a record listing both.
  Entries are now keyed by digest and kind (`parse_record`, the merge,
  `release`, `record_use`'s origin). `used_by(sha256)` stays about the bytes,
  by decision (DECISIONS). Cases: `library_cases.json` *the same bytes as two
  kinds* (records) and *the same bytes kept as two kinds* (`save_scenarios`,
  with a new `find_digest` step).
- **#768**: the library's `files` module syncs the directory after every name
  it makes (file, record, `records/`, folder, `papers/`, marker), and after a
  file found already there. `F_FULLFSYNC` on macOS in both languages, `fsync`
  elsewhere, a no-op on Windows (a `shortcut:` and the owed list below). The
  shared `atomic_write` is unchanged. Cost: about 26 ms per new paper's save on
  a Mac (32 against 6.5 ms). Pinned by
  `test_every_name_a_save_makes_is_synced_into_its_directory` (Python and a
  Rust twin with a `cfg(test)` log of syncs) and `test_sync_dir_flushes_...`.
- Every change was mutation-checked: 5 + 5 for #767, 5 + 4 for #768, each
  mutant caught.
- **Downstream**: StudyAnalyzer can drop its `stored_name` shortcut (#767) once
  it re-pins; MedicalFactChecker's Swift calls `findDigest(sha256:kind:)`.

## The shared paper library (1a #754, 1b #763, 1c #764, all merged)

StudyAnalyzer's design
(`docs/superpowers/specs/2026-11-13-shared-paper-library-design.md`, plan
`...-library-1a-bmlib-module.md`) asks bmlib for a module that several apps
use to share one folder of papers. Built in both languages, in a worktree
(`~/src/bmlib-library`), six tasks, each reviewed:

- **What landed**: `bmlib/library/` and `rust/bmlib/src/library/`. Open, scan,
  find (the whole linked group), find_digest, group, read (digest-checked),
  save (write-once file, then the record), record_use (note a file another app
  saved), release, remove_file, remove_folder; folder names (`folder_name`,
  `folder_name_numbered`); versioned records; thirteen error kinds. Held
  together by `tests/fixtures/library_cases.json` and the sample library
  `tests/fixtures/library/sample/`; manuals `docs/manual/library.md` and
  `rust/bmlib/docs/manual/library.md` (the Rust one is doc-tested). PR #754.
  Review follow-ups: #755.
- **The final review's fixes**: save, record_use and release validate `who`
  and the ids before writing (a record `parse_record` refuses was being
  written); `component()` refuses names
  Windows resolves elsewhere (`...`, `x.`, `C:`, `con`); every record's ids
  count for find and group, a conflict is `ConflictingIds`; `group` is linear;
  an evicted own record is `NotDownloaded` (`RecordNotDownloaded` in a scan);
  atomic-write temporaries are skipped and reported (`LeftoverTemp`); titles
  are NFC before the cut.
- **Rules to keep** (the reasoning is in `docs/DECISIONS.md`, "library"):
  files are write-once; one record per installation; **nothing is deleted
  automatically** (only the user's `remove_*`); no database; name clashes
  judged under case folding; a folder of only newer records fails the save
  with `NewerRecord`; iCloud placeholders count as user files on open; record
  updates are serialised in one process only.
- **1b, merged as PR #763**: `FullTextService`
  reads from and fills a library (`library=` and `installation=`, Rust
  `with_library`). Results carry `library_files` and `library_failure`; source
  `"library"`. Fixtures `tests/fixtures/fulltext_library_*.json`; manual
  section "FullTextService on a library"; reasoning in `docs/DECISIONS.md`.
  `save` keeps bytes once by digest (#755 item 7). `save` still rescans per
  call (0.5 s at 4,000 papers in Python); its `shortcut:` now says to pass a
  `Scan` when a scan's cost shows in a lookup's time.
- **Parked, from the #755-7 decision**: byte-identical files of two different
  papers (a stub PDF, say) still put both papers' records into one folder, but
  the service no longer serves the second paper a file only the first's
  records list (an identity check on the records). And a library holding only a
  PDF still runs the JATS tiers on every lookup, a cost a cached PDF did not
  have; it ends when a JATS is kept in the library.
- **1c, merged as PR #764**: the library through
  bmlib-ffi (`rust/bmlib-ffi/src/library.rs`): `Library` and `LibraryScan`
  objects, owned records, `LibraryError` with bmlib's wording as its exported
  `Display`, `paper_ids_canonical`, `installation_new`. Identifiers and
  installations coming in pass the validating constructors; names are checked
  where bmlib uses them. `library_cases.json`'s identifier, installation, open,
  scan, find, find_digest, group and read cases run through the FFI; `save`
  has round-trip tests, not `save_scenarios`. `find_digest`, `used_by` and
  `release` refuse a malformed digest; `used_by` is `None` for an unknown
  folder and says whether its list is `complete`; all unlike bmlib
  (DECISIONS). The review's core finding, `FileClash.folder` holding a full
  path in both languages, is filed on #755 (the `FileClash` item; the issue
  has two items numbered 7). Not exposed: `FullTextService`'s library use
  (MedicalFactChecker fetches in Swift; the FFI's service is the #492
  sketch). Swift needs
  `extension LibraryError: CustomStringConvertible {}` (UniFFI 0.32.2 omits
  the conformance; README). Reasoning in `docs/DECISIONS.md`, *The library
  through bmlib-ffi (step 1c)*. **Next**: StudyAnalyzer's step 2 and
  MedicalFactChecker's step 3 (re-pin the xcframework). Not released: the
  changelog entries are under Unreleased, and the version is still 0.13.0 /
  0.4.0.
- **Owed before the Windows build gets the Library setting**: (1) renaming
  over an open file fails on Windows; retry with backoff, and stop mapping a
  rename's `PermissionDenied` to `ReadOnly` (review item I5); (2) the long-path
  limit: a 150-byte folder name plus `records/` and an atomic write's temporary
  name can pass 260 characters; (3) the Windows-only gaps the final review
  deferred (superscript `COM¹`-style device names, U+001C-U+001F as
  whitespace in Python only).
  (4) a directory sync: `sync_dir` is a no-op there (#768), so a save
  is not yet durable on Windows; use `MoveFileEx`'s write-through.
- **Owed by Android (phase 4)**: the file operations are free functions on
  `&Path` in `files.rs`; Kotlin's storage access needs them behind a trait
  passed into `Library`.
- **Open library issues**: #766 (`save` beside another paper's JATS; a choice
  between a typed refusal and a public check), #769 (macOS dataless files,
  measured on a device; four options, the maintainer's pick), #761 (symlinks
  and special files), #765 (the FFI runs no `save_scenarios`), #755 (1a's
  review follow-ups).
- **Left open on purpose**: separate processes of one installation writing one
  record (out of scope; an app has one process per installation).

## Earlier: every bioRxiv/medRxiv posting, through `/details` (#341, PR #760)

`biorxiv-details` and `medrxiv-details` read `/details` (30 records a page,
measured); `biorxiv`/`medrxiv` still read `/pubs`. Records keep `source`
`"biorxiv"`/`"medrxiv"`, so both populations meet on one row per DOI, and
**`sync(sources=None)` includes them** (the maintainer's call). Each full-text
source carries the record's `version`; the newest posting is the highest
`version` compared as an integer. A page longer than the walk's step, or a
`/details` record with no version, fails its day. **`DETAILS_SETTLE_DAYS` = 7
is a bound; measuring it is #759.** BMLibrarian bumps its pin once released;
MECA bulk is the expected next request.

## Earlier: full text's JSON as Rust reads it, PMC Open Data (PRs #757, #752, #749, #747, #745)

- **#753 (PR #757)**: the Europe PMC search, NCBI's ID Converter and Unpaywall
  decode through `strict_json_loads`, so a new full-text test must script
  `content`, not `json()`. Deep nesting is still `RecursionError` in Python:
  **#758**.
- **#750/#751 (PR #752)**: `pmc_open_data_record`; the PDF beside JATS checks
  the record's md5; Tier 1c's third source is the bucket's JATS. The record
  fixtures' md5s are those of `sample_article.xml` and `textless.pdf`: edit
  either file and update them.
- **#744-#748 (PRs #745-#749)**: the PDF beside JATS is retried
  (`PDF_BESIDE_JATS_ATTEMPTS`), marked and sought again
  (`PDF_SEEK_AGAIN_LIMIT`); not covered: a JATS cached with the option off
  (the `shortcut:` in `_check_cache`).

## Earlier: a saved page is not refused for its length (#656, PR #740)

Both languages cap a page's `<` (`HTML_MAX_TAGS`), not its length. Open:
**#726** answered and closeable (the maintainer's call); **#725** unmeasured.

## Release 0.13.0 / Rust crate 0.4.0 (released 2026-11-10)

PR #736, merged as `161849c0` and tagged `v0.13.0`; PyPI and crates.io
carry it. Detail: PR #736 and the CHANGELOGs.

**Left open past the release**: #694 (bmlib-ffi forwards no log records), #495 (Rust
JATS reader aborts on deep nesting), #722 (Rust `sync()` logs few of Python's
lines), #733 (pricing rows name retired models, so the new defaults cost the
fallback estimate), #734 (Rust's DeepSeek `/v1`; both paths answered 200),
**Mistral's default** (`mistral-large-latest` 403 `tier_not_allowed` on the
probed key; re-probe with a paid-tier key), #730/#731 (reasoning-model rules),
#716, #718, #725 (#726 measured, #656 fixed since).

## What 0.13.0 costs StudyAnalyzer (and any downstream)

Read the CHANGELOG's `[0.13.0]` summary and *Changed — breaking*, and the
crate's `[0.4.0]`. The points an app must act on:

- **Rust `sync()`** returns `Result<SyncReport, SyncError>` and takes a clock
  (`&Utc::now`) (PR #721).
- **`LimitedClient`** (or any wrapping `HttpClient`) must forward
  `post_json_stream_with_cancel` (#649/#652) and clip
  `stream_timeout_seconds` as well as `timeout_seconds` (#702).
- **The keychain item is one item in both languages** on every platform
  (PR #720); show "saving a key replaces the damaged item" only for
  `UnreadableItem` (#689); iOS needs the crate's `protected` store
  (StudyAnalyzer #74).
- **PICO**: show `CONTRADICTED` apart from *Unsure* and add the `match` arm
  (#647); `RequestFailed { message, retryable, status }` — retry only
  `retryable: true`, map `Some(401 | 403)` to `needs_api_key`; a `ChatError::Http`
  arm wherever a `Transport` arm meant "the request failed" (#646).
- **`HtmlError`** is `#[non_exhaustive]`: match it with `_` (#723).
- **Warnings are `log` records** (#688): re-pin, and drop the app's own
  damaged-item line if tauri-plugin-log shows bmlib's.
- **Stored PDF and Markdown canonical texts are stale** (pdf-7,
  markdown-2): re-convert and re-index.
- Whether a misreported PICO part's confidence lifts `overall_confidence` is
  the maintainer's to revisit (`docs/DECISIONS.md`).

## Earlier, 2026-11-01 to 2026-11-05 (all merged): what StudyAnalyzer owes

PRs #614-#674 (#609, #667, #619, #664, #616, #659, #658, #646, #654, #563,
#635, #632, #640-#642, #638 and the PDF segmenter's): each CHANGELOG entry
under `[0.13.0]` names the app's action, and *What 0.13.0 costs StudyAnalyzer*
above gathers them. Residuals open: **#605**, **#623**, **#628**. The title
corpus's `median_font_size` rows are line medians, so its next top-up bumps
`ROW_DEFINITIONS_VERSION`.

## Rules carried forward, and previous sessions

**Rules are in [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)**; read
[`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything. **Each
session has a ROADMAP row and a `CHANGELOG.md` entry**; **a PR body is the
record**. **Read PR #285 before the next front-matter change.**

**Earlier sessions** are each a `CHANGELOG.md` entry, a ROADMAP row and a PR
body; read those, not a list here. Rules from them that are not yet in
`docs/SESSION-RULES.md`: only a `ValueError` or a declared non-text codec is
the remote's bytes (#466); record/replay a sampler refactor (#458); **every
JATS PR trips `tests/test_renderer_version.py`** (#437): bump
`RENDERER_VERSION` if `to_html()` can move, then re-pin. **Worktree recipe**:
`git worktree add ../bmlib-x origin/main -b <branch>`, then `uv venv .venv`,
`uv pip install --python .venv/bin/python -e ".[all,dev]"`, and run
`env -u VIRTUAL_ENV uv run …`.

## PDF failures, MuPDF in Rust (#590-#600, merged)

Open: **#595**. Downstream: pass `PdfLimit { cancel, deadline }`;
`MupdfExtractor::new()` replaced `PdfiumExtractor::bundled()`.

## Older StudyAnalyzer items: guidance still owed downstream

Items 8 (PR #586, `bmlib.embed.store`, imported as that and not re-exported),
5 (PRs #580/#584, `lookup_record`), 7 (PR #575, `PicoAgent`), 11 and 6
(PRs #568/#562, context windows, streamed chat); the CHANGELOG entries are
the argument. **Bump `EMBEDDING_RECIPE_VERSION` with any change that can move
a vector.** Filed from their reviews and open: #581, #582, #585, #577 (its point 3 left open by PR #696; #583, #572 and #618 since fixed), **#566** (Python's `get_model_metadata()` guesses), **#561** (Rust's
`chat()` retries nothing).

Plans 2a-2c (`lookup_record`, the embedder and its store, `PicoAgent`) have
their app-side steps in those PRs' CHANGELOG entries.

**Earlier StudyAnalyzer sessions** (PRs #524-#559; argument in
`CHANGELOG.md`). Still open from them: **#537** (#507's Rust/FFI half),
**#520** (the Rust store's import owes `RetractionIndex::build`'s all-`Other`
refusal), **#552**, **#553**, **#557**, **#558**, **#560**; adding `Review` to
the extractor strata is the maintainer's decision (#540).

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

- **Version 0.13.0, released 2026-11-10** (on PyPI; 0.12.0 released 2026-10-31). The version lives
  in **five** places — `pyproject.toml`, `bmlib/__init__.py`, the README
  version line, `CLAUDE.md`'s header, `docs/manual/index.md`'s header line —
  and all five agree; only `bmlib/__init__.py` is guarded by anything but this
  list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag;
  **0.9.0 moves nothing stored**; **0.9.1 moves stored full text** (#79);
  **0.10.0 moves nothing stored but re-fetches the whole sync window once**
  (#95); **0.11.0 moves stored values broadly** (see above); **0.12.0 re-renders cached full text by itself** (`RENDERER_VERSION` 7) and moves OpenAlex `pmcid` and extractor `study_type` (its CHANGELOG summary); **0.13.0 makes stored PDF and Markdown canonical texts stale** (pdf-7, markdown-2) and keeps the full-text cache. The two questions are independent, and a downstream reading only the
  number must still read this list.
- **Tests on `feat/library-find-kind-durable-save`** (2026-11-14, PostgreSQL
  18 DSN on port 5532, `BMLIB_REQUIRE_POSTGRESQL=1`): Python 10,900 passed + 20
  skipped (10,920 collected); Rust 1,832 (`cargo test --workspace`, clippy
  clean), `cargo test -p bmlib-ffi --features http-sketch` 35; the Rust oracle
  44 clean. Other recipes: `postgres_live.rs` with `BMLIB_PG_TESTS=1
  BMLIB_PG_PORT=5532 BMLIB_PG_USER=postgres`; `--features embed` and the
  embedder's live tests need `BMLIB_EMBED_MODEL` (the GGUFs are in
  `~/.cache/bmlib/models/`; the Python live tests need `.[all,dev,embed]`).
  Measure `main` with `pytest --collect-only` and never subtract from a
  previous handover's number. The default skips are the PostgreSQL
  parameterisations, a PostgreSQL-only schema test, a non-UTF-8 file name
  (APFS) and `test_pymupdf_requires_dependency`.
- **Run the PostgreSQL half locally — it finds real bugs.** Postgres.app 16
  runs on `localhost:5432` here (`createdb -h localhost -U postgres bmlib_test`
  once); set `BMLIB_TEST_POSTGRESQL_DSN="host=localhost port=5432
  dbname=bmlib_test user=postgres"` and `BMLIB_REQUIRE_POSTGRESQL=1`.
- **Documentation is kept current; treat drift as a regression.** The
  `unreleased` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release; 0.11.0 promoted all 354 and 0.12.0 all 189 (rewriting the `#…-unreleased` anchor links to `#…-0120` with them), and new ones are written bare again
  (`*(unreleased)*`). Grep case-insensitively for the bare word, since the markers take
  several spellings, and reword a *prose* use ("is itself unreleased") rather
  than promote it. Write the marker bare, never with a guessed version, and leave the ones in
  `docs/superpowers/plans/` alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup* (no workflow file), ignores
  a PR's `reopened` action, and does not constrain the merge strategy (#78).

## Next up

### Open GitHub issues

**126 open** (124 once #767 and #768 are closed by this branch's PR): the
library's #761, #765, #766, #769 and #755 (above), **#759** (PR #760's:
measure the `/details` settle period) and **#758** (#753's review
residual, `RecursionError`) among them (#734, #733,
#731, #730, #726, #725, #722, #718, #716 and #694 are the newest; #577 stays
open for its point 3, which PR #696 left open)
(`gh issue list --state open --limit 300 --json number`, 2026-11-14, after
#764 merged); #651 is
#632's residual; #556–#734 are not yet sorted into the lists below). **PR
#615's review filed** **#617** (both halves done, PRs #620 and #692) and #618
(a part not reported may now have a blank description; fixed with #577 in PR
#696, the maintainer's call of 2026-11-07). **#623** (pdf-4's review
residuals: indent misses, a numbered quantity read as a subsection, the Rust
failed-`/Title` test), **#605** (whether a truncated page is more than a
warning; the maintainer's call) and **#595** (`render_html`'s weaker furniture
rule; measure the move by diffing before choosing) and **#628**'s remaining
causes are the PDF path's open work. **#528** and **#529** are PR #526's
review's, both `find_passage` decisions: a superscript digit makes different
numbers match exactly (`10³` vs `103`), and a quote whose seeds are all too
common is rejected at 0.0. **#537** is #507's Rust/FFI half (refusing a
non-ISO evidence date needs `retraction_status` to return a `Result`, an app
API change); **#520** is the Rust store's import, which owes #522's nature
rule too. **Five more filed on 2026-10-17 are in no list below yet**: the FFI
spike's #491, #492, #493 and #496; and the Rust reader's #495 (per-element
recursion aborts on deep nesting). They are: the Rust audit's #314 (a
decision), the Rust side's #332, **#467** (follow #463's port parity),
**#468** (follow #227), **#471** (follow #470), **#476** (follow #474),
**#483** (follow #475), #409 (follow #406), #411 (follow #407), #416 (follow
#413/#415), #421 (follow #414), #426 (follow #423), #432 (follow #425/#429;
now also #145's five Python-only fixtures) and #436 (follow #172), and the
Python list: #92, #94, #128, #137, #142, #143, #144, #150, #154, #156, #157,
#173, #174, #175, #177, #178, #179, #197, #201, #204, #207, #209, #212, #222,
#233, #235, #240, #242, #244, #245, #247, #249, #251, #252, #253, #260, #273,
#275, #278, #279, #281, #282, #283, #286, #287, #288, #290, #291, #342,
#465 (three local transport classes stay `transport-`; a decision), **#480**
(render affiliations in the cached HTML? a decision), **#481** (strip an
affiliation's trailing `;`/`,`? a decision), and — **not filed**, open by
decision since PR #479 and recorded only in `docs/DECISIONS.md` — whether a
malformed `pmc_id` should WARN rather than DEBUG (#304's level), #456 (a Rust
regex-engine error still yields a determinate answer; a design decision), #451
(label the phrase-search strata's protocols and phase-specific trials, a
decision), #454 (full-text study typing reads a cited trial; a design
question), #393 (an element-only citation whose text sits only in unread
children renders blank), #396 (those children's text reaches no field and no
counter), #419 and #420 (PR #418's review: count a replaced first page, count
a partly tagged `<string-name>`'s bare text — both "count it?" decisions),
#424 (#224's WARNING calls a citation note's `<p>` missing; measured 0), #430
(a `<collab>`'s directly held `<email>`/`<ext-link>`/`<on-behalf-of>` welds
into its name; a per-element decision), #431 (a `contrib-type="collab"`
contributor, a consortium or its members, is not an author; a role decision),
#434 (a cited roster's members become cited authors ahead of the group, and
the `citation` string carries one spelling of a member and not the other),
#435 (a double space where a roster sits mid-name), **#439** (an element-only
citation printed in prose — every one an eLife dataset citation — is counted
but its DOI reaches no field; a decision), #441 (a `<citation-alternatives>`
group of two typeset renditions prints both) and #442 (an element-only
citation inside a related work in prose prints its fields run together;
invalid JATS), both decisions from PR #440's review. Re-count against `gh`.

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
#453 used it (record-level texts cached in a scratch directory for the
counterfactuals, since the corpus holds hashes only).

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
- **`uv run mypy` is a gate too** (#81), pinned to **2.4.1** in the `dev`
  extra with its settings in `pyproject.toml`. Give it no arguments and run it
  in the dev venv; anything deliberately unchecked is an inline
  `# type: ignore[code]` with its reason, never a per-module
  `ignore_missing_imports`.
- Tests use in-memory SQLite and mocked HTTP; no external services.
  `BMLIB_TEST_POSTGRESQL_DSN` must point at a database the tests may drop
  every table in (recipe under *Current state*).
- Session workflow: the `nextsession` skill; post-review fix-up: `fixall`.
- **Cutting a release** (0.4.0 through 0.11.0 were all cut this way): bump the
  version in the **five** places, promote the CHANGELOG's `[Unreleased]` body
  under a dated heading with a prose summary (merging repeated sub-headings,
  which sessions add at the top, with a check that every line survives, and
  gathering any API break under *Changed — breaking*), promote the
  `unreleased` markers in `docs/manual/` and `ROADMAP.md`, add the release's own `ROADMAP.md` row,
  bump the Rust port's `BMLIB_VERSION` and re-run
  `scripts/rerun_rust_oracle.py --write` (the `User-Agent` corpus carries the
  version), commit on a `release/X.Y.Z` branch and open a PR. **The number is a claim
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
