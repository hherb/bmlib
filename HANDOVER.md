# HANDOVER — bmlib development

_Last updated: 2026-10-31 (**this session is #598 + #600**, PDF extraction
errors typed and extraction bounded, on `fix/pdf-extract-error-598`). **0.11.0
is released** (`v0.11.0` on 8edd3d7, on PyPI). Every PR merged since — #526
through #601 — is unreleased, **and no StudyAnalyzer item is left in bmlib, so
a 0.12.0 release is a natural next session** (the maintainer chose the PDF
issues over it this session). Its release notes must say: the Rust crate's
`pdf` feature changed API in #590 and again here (`PdfError`, `PdfLimit`),
`FullTextResult` gained a field in #594, and Python's PDF error messages move
(#598)._

## What 0.11.0 costs a downstream

**Read the CHANGELOG's `[0.11.0]` summary and its *Changed — breaking*
heading** — the summary gives the data answer, the heading gathers every API
break (they had been scattered under *Fixed*), and the per-PR argument and
every population figure stay in the entries under *Added*, *Changed* and
*Fixed*. In one line each: cached full text re-fetches by itself (#172);
stored transparency results want recomputing (#184, #194) and
`risk_indicators` strings move (#203, #227); the preprint population is
published preprints only (#325); `ensure_schema()` adds
`download_days.peak_count` and `download_day_parts`. The README has a short
*Upgrading to 0.11* note pointing at the heading.

## Rules carried forward, and previous sessions

**Rules are in [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)**; read
[`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything. **Each
session has a ROADMAP row and a `CHANGELOG.md` entry**; **a PR body is the
record**. **Read PR #285 before the next front-matter change.**

**Last sessions** (argument in `CHANGELOG.md`; most rules are in
`docs/SESSION-RULES.md`): PR #601 (#594), PRs #596/#597 (#590/#591, MuPDF and page furniture), #593 (#592), #589 (#587/#588), #586 (item 8), #580/#584 (item 5), #575 (item 7), #568 (item 11), #562 (item 6), PR #559 (item 4c, `canonical_from_html`, `assess_full_text`), PR #556 (item 4b, `canonical_from_segmented`), PR #554 (item 3, `chunk_canonical`), PR #547 (item 4a, `bmlib.fulltext.canonical`), PR #545 (item 9, `FullTextResult.jats_xml` and the cache's `jats/`), PR #544 (#533, the `find_passage` timing tests bound windows scored), PRs #543 (#503 part 2, the `biorxiv` rows redrawn from `/pubs`), #542 (#503 part 1, the PDF-title journal stamped), #541 (#540, PubMed's bare `Review` measured as a displaced draw; three extractor corpora are pinned now), #539 (#453, a review read by how it searched; every `extractors.py` edit re-measures the corpora), #536 (#500, a value faking `str` is not text: `_is_text` asks `type()`, restated in `fulltext` and `transparency`; pytest's id generator is fooled by `Mock(spec=str)`, so give such a parametrize value an explicit id), #535 (#532, the embedder's errors typed alike in both languages: one `EmbedError` subclass per Rust variant, every one pickles; Rust `Llama` split into `Load`/`Decode`), #534 (#530, a NaN, infinite or zero embedding refused in both languages; a narrow model refused at load), #531 (`bmlib.embed`, the in-process arctic-embed embedder, Python and Rust; the embedder's review filed #530 and #532), #526 (`find_passage`, #527), #524 (#523/#522, `notice_pmid` canonical; `RetractionIndex::build` refuses an all-`Other` export), #521 (#509/#510, one export replaces the stored retraction notices; the export's `00000000` PMID sentinel), #519 (#518, a cited `<source>` normalised), #517 (#516, every `<label>` and the journal title normalised), #515 (#514, an abstract title normalised), #513 (#498, a title-only abstract section kept, by decision), #506 (#504, retraction status from three sources), #502 (#494, Tier 1d takes Europe PMC's own PDF; a PMID-only caller's Tier 2 asks Unpaywall by the hit's DOI), #501 (#489, a Tier 3 result naming no design leaves a conclusive Tier 1 standing), #499 (#485, a blank cache key), #484 (#475, a caller's DOI stripped and a blank one none, in `transparency` and `fulltext` alike), #482 (#145, a contributor's affiliations; the Rust port in the same PR),
#479 (#472/#478, `FullTextService`'s PMID
canonical and set aside rather than raised), #477 (#474, the PubMed record read is the one
asked about; books by the same rule), #473 (#470, PMID shape), #469 (#227, `pubmed_status`), #466 (**only a
`ValueError` or a declared non-text codec is the remote's bytes**), #464, #461, #458 (**record/replay a
sampler refactor**), #455, #452, and #437 (**every JATS PR trips `tests/test_renderer_version.py`** — bump
`RENDERER_VERSION` if `to_html()` can move, then re-pin). **Worktree recipe**: `git worktree add ../bmlib-x origin/main
-b <branch>`, then `uv venv .venv`, `uv pip install --python .venv/bin/python
-e ".[all,dev]"`, and run `env -u VIRTUAL_ENV uv run …`.

## This session: whose fault a PDF failure is (#598), and a bound on extraction (#600)

Both found reviewing hherb/StudyAnalyzer#21. **#598**: `PdfErrorKind` (Rust)
/ `PDFErrorKind` (Python) — `encrypted`, `unreadable`, `engine`, and from
#600 `cancelled`, `timed-out`. MuPDF's `FORMAT`, `SYNTAX`, `UNSUPPORTED` are
the file's (measured: a garbage file is `FORMAT`, a truncated one `SYNTAX`; in
Rust an empty file is `FORMAT` and a missing one `SYSTEM`, while Python sees
PyMuPDF's `EmptyFileError` and its own `FileNotFoundError`); every other code and anything unplaced is
`engine`, so an unknown cause is loud. Rust's `PdfTextExtractor` returns
`PdfError { kind, message }`; Python's `ConversionResult.error_kind` and
`extract_blocks()`'s `PDFExtractionError` (a `ValueError`, pickles). Messages
never name the path (MuPDF's open messages did; it is replaced by "the
file" — a `shortcut:` comment notes MuPDF's 256-byte message cut). **#600**:
`PdfLimit` / `PDFLimit` (cancel flag, deadline). **The maintainer chose
mid-page in Rust, per-page in Python**: Rust runs every page with one MuPDF
cookie and a scoped watcher thread sets its `abort` (the crate's `Cookie` is
`!Send`, so the field is reached through its pointer, one documented
`unsafe`); MuPDF ends an aborted page silently, so the flag is read back. Measured
once, debug build: a 4.6 s page stops ~60 ms after the start given a 50 ms
deadline. PyMuPDF's `get_text()` takes
no cookie. A reached limit logs at INFO in Python's `convert()`. Both
mutation-checked in both languages; **one survivor, timing-equivalent**: the
check before each page, which the watcher always beats on a short page — kept
for determinism. `MupdfPdfExtractor` (the service's adapter) takes no limit,
and neither does `FullTextService`; neither carries the kind on either
(#604). **PR #602's review** added: a drop guard so a panic during the walk
cannot hang the watcher's scope; Python failing the conversion on an engine
fault *on a page* (it was a page warning); `ConversionResult::failure(PdfError)`
setting `error_kind`; `PDFLimit` and `ConversionResult` refusing what cannot
work; `is_the_files` in both languages over `tests/fixtures/pdf_error_kinds.json`;
the cause walk honouring `__suppress_context__`; the `LayoutExtractor`
Protocol left at its old signature. MuPDF's silent stop after too many syntax
errors, which the cookie now counts, is #605.

**For StudyAnalyzer**: map `PdfErrorKind::Unreadable`/`Encrypted` to
`invalid_input` (the encrypted message says to remove the password) and
`Engine` to an error; pass `PdfLimit { cancel, deadline }` to
`extract_blocks_within` / `page_texts_within` from the app's `Limit`.

## Previous session: a failed PDF download says why (#594)

PR #601, merged. `FullTextResult.pdf_download_failure` (Python and Rust)
names why a PDF tier's download left no `file_path`: `"http-<status>"`,
`"not-a-pdf"`, `"request-failed"`, `"write-failed"`; `None` when cached or not
attempted. The maintainer chose the field over falling through to the next
tier. **For StudyAnalyzer**: a result with it set is a free PDF worth a retry,
never "no full text yet".

## Previous sessions: MuPDF in Rust (#590), page furniture (#591), a fresh Rust cache (#592)

PRs #596, #597 and #593, all merged. The CHANGELOG's top entries (both files) are the argument. **#590**: Rust's
`pdf` feature extracts with MuPDF (`mupdf` =0.8.0, MuPDF 1.27.2, linked
statically; `MupdfExtractor` / `MupdfPdfExtractor`, `PdfiumExtractor` gone —
breaking), reading a line as PyMuPDF's `get_text("dict")` does; over the
151-PDF title corpus plus the jptcp paper, 143 give PyMuPDF 1.28.5's lines
exactly and 149 give 1.27.2's (the crate's MuPDF release; page text 151 of
151). `canonical_from_segmented` joins a soft hyphen followed by white space
(`join_soft_hyphens`, both languages). A new contract starts at PDF bytes:
`tests/fixtures/pdf_extraction.{pdf,json}`, written by
`scripts/make_pdf_extraction_fixture.py`. **#591**: `segment_document` sets
page furniture aside first (`page_furniture`, both languages; measured over
231 PDFs, the 151 plus 80 Unpaywall journal PDFs — Europe PMC's `?pdf=render`
answered 403 to every request on 2026-10-31, so the journal sample came from
Unpaywall). `PDF_CANONICAL_VERSION` is 3. **For StudyAnalyzer**: rename
`PdfiumExtractor::bundled()` to `MupdfExtractor::new()` (nothing to bind or
cache any more), and a PDF's canonical text is re-made (`pdf-3`).

A candidate is furniture only at its page's edge (PR review: a table
continued on one grid over pages read as furniture). **Filed: #595** —
`render_html`'s exact-text furniture filter is a second, weaker rule beside
`page_furniture`. Not measured: a figure label alone at a fixed place atop
its own page on 40% of the pages is set aside.

**Residuals, not filed**: the MuPDF version gap (combining accents, a
subset-tagged base-14 face's box) closes when the crate bundles MuPDF 1.28;
two PDFs' no-Unicode glyphs differ; line boxes round maths fonts
(`JM_char_quad`, which the crate cannot reach). The issue's two Android
build workarounds and `Font::name`'s panic on a non-UTF-8 name are
upstream `messense/mupdf-rs` bugs, **not reported** — the maintainer's call.

**#592** (PR #593), found by StudyAnalyzer's manual test: Rust's `FullTextCache` creates no
directory on construction, and `save_pdf`/`save_html` (unlike `save_jats` and
`save_abstract`) never created theirs, so in a new cache directory every PDF
write failed and the service returned no file. Both saves now create their
directory (a rejected PDF still creates nothing). The Rust test helpers had
pre-created `html/` and `pdfs/`, which hid it; they no longer do. Python's
constructor makes both, so it is unchanged. Filed from the same test: **#590**
(Rust's PDF backend to MuPDF: PDFium's U+0002 hyphen markers and duplicated
characters, with a build trial for macOS, iOS and Android) and **#591** (running page headers and footers inside paragraphs).

## Previous session: a stored span is the chunker's (#587), Rust's store tests on PostgreSQL (#588)

The CHANGELOG's two *Fixed* entries at the top of `[Unreleased]` are the
argument. `search` rebuilds the chunks with `chunk_canonical` (the label pins
the text's bytes, the chunker and the converter's version — not the sections,
so a hand-built text sectioned otherwise is refused as corrupt too) and refuses a set unless every stored
`(section, start, end)` is the rebuilt chunk's and the counts agree — which
also caught a set whose rows and count were both zeroed. Python and Rust; the
spans are still stored, so no schema change. `rust/bmlib/tests/vector_store.rs`
now generates each case twice with a local `store_test!` macro (SQLite, and
PostgreSQL via `common::on_postgres` when `BMLIB_PG_TESTS=1`); five cases stay
SQLite-only and say why. `postgres_live.rs`'s `TestDb` moved to
`tests/common/live_pg.rs`. Local run: `BMLIB_PG_TESTS=1 BMLIB_PG_HOST=localhost
BMLIB_PG_PORT=5532 BMLIB_PG_USER=postgres cargo test -p bmlib --features
postgres --test vector_store` (77 = 41 + 36).

## Previous session: a paper's chunks, stored and searched (StudyAnalyzer item 8)

PR #586, merged. `bmlib.embed.store` (imported as that, **not re-exported**)
and `bmlib::embed::store`: `ensure_schema`, `index_paper`, `search`,
`delete_paper`; spec and plan under `docs/superpowers/`, the CHANGELOG entry
*A paper's chunks, stored and searched* is the argument. **Bump
`EMBEDDING_RECIPE_VERSION` with any change that can move a vector.**

**For StudyAnalyzer plan 2b**: download with `download_model`, then build the
embedder with `Embedder::load_model(&ARCTIC_EMBED_L_V2_F16, &path)` (never
`load`; the `ModelFile` must be the one `path` was downloaded as, or the size
check refuses it); call `embed::store::ensure_schema` on the app's database once;
`index_paper(db, paper_key, &canonical, &mut embedder, Some(&mut progress))`
after converting the full text (show progress; `false` cancels and keeps the
old set); `search(db, paper_key, &canonical, question, &mut embedder, k)` for
retrieval mode. On `StoreError::Stale(_)` re-index (re-converting first if
`ConverterVersion` is among the fields); `NotIndexed` means index first;
`Corrupt` means re-index too. Switching F16 ↔ Q8_0 makes every paper stale.

## Previous session: one paper's record by identifier (StudyAnalyzer item 5)

PRs #580 (#579: a caller's `html` source is the last-resort link) and #584
(`lookup_record` / `bmlib::publications::lookup`), both merged; the CHANGELOG
entries are the argument. Filed from its reviews: **#581**, **#582**, **#583**,
**#585**. **For StudyAnalyzer plan 2a**: call `lookup_record` with **a PPR
accession as `ppr_id`**, hand the record's `fulltext_sources`, `pmc_id`, `doi`
and `pmid` to `fetch_fulltext`, show `published_version` as "a published
version exists", and treat `request_failed` at any source as worth a retry,
never "no such paper".

## Previous session: the PICO agent (StudyAnalyzer item 7)

PR #575, merged. `PicoAgent` in `bmlib.quality` / `bmlib::quality`; the
CHANGELOG entry *The PICO agent, with verified quotes* is the argument.
**For StudyAnalyzer plan 2c**: run `check_suitability` on the abstract, show a
"not suitable" answer with a *run anyway* action calling `extract`; pass
`CanonicalText.text` as `text` and `min_similarity` from plan 2b's
measurement; label an `UNVERIFIED` part *Unsure – model could not reproduce
<part> from text provided*. `REQUEST_FAILED` is retried, `UNUSABLE_REPLY`
suggests another model, `TRUNCATED` a larger `max_tokens`. In Rust, configure
`PicoAgent::pico_condenser` for long papers. Its reviews filed #572, #573,
#574, #576, #577 and #578.

## Since: the manual refresh (PR #569)

Every page of `docs/manual/` was audited against the source, and every
exported name is now on its page. A Rust manual was started at
`rust/bmlib/docs/manual/`: `index.md` and `db.md`, compiled as doctests.
**Its next pages are `citations`, `embed`, `llm` and `fulltext::canonical`**,
with `db.md` as the template. Filed: **#570** (`context_processor` stores a
null `extracted_content` as `"None"`) and **#571** (stale docs outside the
manual).

## Earlier: context windows (item 11) and streamed chat (item 6)

PRs #568 and #562; the CHANGELOG entries *Context windows, and Ollama's
`num_ctx`* and the streamed-chat entry are the argument. `context_window()`
on both clients (`None` = the provider did not say, `NoSuchModel` = a mistyped
model). Owed: **#566** (Python's `get_model_metadata()` guesses) and **#561**
(Rust's `chat()` retries nothing, where Python's SDKs retry twice).
**For StudyAnalyzer plan 2b**: clamp the user's setting to `context_window()`,
send `num_ctx` for Ollama, treat `None` as retrieval mode, and show
`NoSuchModel` as a mistyped model. `chat_stream(…, on_delta)` returns
`chat()`'s `LLMResponse`; `on_delta` returning `False` /
`ControlFlow::Break` stops it (`stop_reason="cancelled"`).

**Earlier StudyAnalyzer sessions** (argument in `CHANGELOG.md`): PR #559 (4c,
`canonical_from_html`, `"html-1"`, html5ever in both languages, every walk
iterative, the app must cap a page's size; `assess_full_text`, unmeasured,
**#560**; **#557**, **#558** after 2026-11-04); PR #556 (4b,
`canonical_from_segmented`, `"pdf-1"`, contract from the `SegmentedDocument`);
PRs #554 and #555 (item 3, `chunk_canonical`, contract
`canonical_chunks.json` from its committed generator); PR #547 (4a, the model,
JATS, Markdown, plain text; its reviews left **#548**, **#549**, **#552**,
**#553**); PR #545 (item 9, `FullTextResult.jats_xml`, the cache's `jats/`).

**PR #544** (#533): the maintainer chose the work bound. The three `*_is_fast` tests per language
now count the windows scored (Python monkeypatches a counting
`SequenceMatcher`; Rust counts `set_a` behind `#[cfg(test)]`, so its three
tests moved into `citations::passage_match`'s unit tests, sharing
`rust/bmlib/tests/data/passage_match_sentences.rs` with the integration file)
and assert `MAX_WINDOWS_SCORED` = 1,330, a formula over the search's
constants. Measured 90/130/10 (Rust; Python +1 for the constructor's
`set_seq1`), unchanged at 41 kB. Mutation-checked in both languages. No
library code moves.

**PRs #542 and #543** (#503): the PDF-title sampler
stamps every journal line with `ROW_DEFINITIONS_VERSION` and refuses an
unstamped journal; the corpus's `biorxiv` rows are a 150-row `/pubs` draw, and
nothing in `bmlib` moved. The stale unstamped journal in the main checkout was
moved aside this session.

**PR #541** (#540): PubMed's bare `Review` is a displaced
draw, committed as `tests/data/extractor_abstracts_review.json` and pinned, so
**an `extractors.py` edit re-measures three corpora**. Adding `Review` to the
committed strata is still the maintainer's decision.

**PR #539** (#453): `systematic_review` reads a review by
how it searched; "meta-analyses" and "systematically identified" were refused.

**PR #538** (#507): a non-ISO `RetractionEvidence.date`
raises `ValueError`; the Rust/FFI half is **#537**.

**An earlier session** (PR #524, #523 + #522): `notice_pmid` is stored
canonical in Python and Rust; `RetractionIndex::build` refuses an all-`Other`
export, and Python's nature flag counts findable notices only. **#520** (the
Rust desktop store's import) must take that nature rule too.

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

- **Version 0.11.0, released 2026-10-17 (UTC; the tag's commit is dated 2026-10-18 +1100), is live on PyPI**. The version lives
  in **five** places — `pyproject.toml`, `bmlib/__init__.py`, the README
  version line, `CLAUDE.md`'s header, `docs/manual/index.md`'s header line —
  and all five agree; only `bmlib/__init__.py` is guarded by anything but this
  list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag;
  **0.9.0 moves nothing stored**; **0.9.1 moves stored full text** (#79);
  **0.10.0 moves nothing stored but re-fetches the whole sync window once**
  (#95); **0.11.0 moves stored values broadly** (see above). The two questions are independent, and a downstream reading only the
  number must still read this list.
- **Tests: 8,751 passing + 164 skipped** on `fix/pdf-extract-error-598`
  (`uv run pytest tests/ -v`, 2026-10-31; Rust 1,345 with `cargo test -p bmlib --features pdf,html`; the embedder's live tests skip
  without `BMLIB_EMBED_MODEL` — the GGUF files are in
  `~/.cache/bmlib/models/` on this machine, and the Python live tests need
  `.[all,dev,embed]` installed); measure `main` with `pytest
  --collect-only` and never subtract from a previous handover's number. The PostgreSQL half was last run in full for
  PR #448's branch (5,810 passed + 2 skipped); PR #542's session ran
  `test_backends.py` with it (197 passed + 1 skipped). The default skips are
  the PostgreSQL parameterisations, 1 a PostgreSQL-only schema test, and
  `test_pymupdf_requires_dependency`.
- **Run the PostgreSQL half locally — it finds real bugs.** Postgres.app 16
  runs on `localhost:5432` here (`createdb -h localhost -U postgres bmlib_test`
  once); set `BMLIB_TEST_POSTGRESQL_DSN="host=localhost port=5432
  dbname=bmlib_test user=postgres"` and `BMLIB_REQUIRE_POSTGRESQL=1`.
- **Documentation is kept current; treat drift as a regression.** The
  `unreleased` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release; 0.11.0 promoted all 354, and new ones are written bare again
  (`*(unreleased)*`) — #485's, #489's, #494's and #497's lines are the first. Grep case-insensitively for the bare word, since the markers take
  several spellings, and reword a *prose* use ("is itself unreleased") rather
  than promote it. Write the marker bare, never with a guessed version, and leave the ones in
  `docs/superpowers/plans/` alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup* (no workflow file), ignores
  a PR's `reopened` action, and does not constrain the merge strategy (#78).

## Next up

### Open GitHub issues

**118 open** (`gh issue list --state open --limit 300 --json number`,
2026-10-31, counting #598 and #600 as done; #560–#597 are not yet sorted
into the lists below). **#599** (Rust silences MuPDF's warnings, so a
repaired PDF's lost text goes unreported; the cookie this session added
counts `errors`, which may help) and **#595** (`render_html`'s weaker
furniture rule; measure the move by diffing before choosing) are the PDF
path's open work. **#528** and
**#529** are PR #526's review's, both `find_passage` decisions: a superscript
digit makes different numbers match exactly (`10³` vs `103`), and a quote
whose seeds are all too common is rejected at 0.0. **#537** is #507's Rust/FFI half (refusing a non-ISO evidence date needs `retraction_status` to return a `Result`, an app API change); **#520** is the Rust store's import, which owes #522's nature rule too. **Five more filed on 2026-10-17 are in no list below yet**: the FFI
spike's #491, #492, #493 and #496; and the Rust reader's #495 (per-element recursion aborts on
deep nesting). They are: the Rust audit's #314 (a
decision), the Rust side's #332, **#467** (follow #463's port parity), **#468** (follow #227), **#471** (follow #470), **#476** (follow #474), **#483** (follow #475), #409 (follow #406), #411 (follow #407),
#416 (follow #413/#415), #421 (follow #414), #426 (follow #423), #432 (follow #425/#429; now also #145's five Python-only fixtures), #436 (follow #172) and **#444** (follow #346), and the Python list: #92, #94, #128, #137, #142, #143, #144,
#150, #154, #156, #157, #173, #174, #175, #177, #178, #179, #197,
#201, #204, #207, #209, #212, #222, #233, #235, #240, #242,
#244, #245, #247, #249, #251, #252, #253, #260, #273, #275, #278, #279,
#281, #282, #283, #286, #287, #288, #290, #291, #341, #342,
#465 (three local transport classes stay `transport-`; a decision),
**#480** (render affiliations in the cached HTML? a decision), **#481**
(strip an affiliation's trailing `;`/`,`? a decision), and — **not filed**,
open by decision since PR #479 and recorded only in `docs/DECISIONS.md` —
whether a malformed `pmc_id` should WARN rather than DEBUG (#304's level),
#456 (a Rust regex-engine error still yields a determinate answer; a design
decision), #451 (label the phrase-search strata's protocols and
phase-specific trials, a decision), #454 (full-text study typing reads a cited trial; a
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
- **`uv run mypy` is a gate too** (#81), pinned to **2.3.0** in the `dev`
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
