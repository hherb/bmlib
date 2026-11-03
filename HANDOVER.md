# HANDOVER — bmlib development

_Last updated: 2026-11-03 (**this session's PR is #639**, on `feat/638-pico-prepare`,
for #638; see *This session* below). PRs #637 (#628, unnamed parent), #631,
#630, #629 (#628), #627 (pdf-5), #626 (#565), #625 (#603), #622 (pdf-4), #621
(Rust CSV), #620 (#617), #615, #614 and #613 merged. **0.12.0 is on PyPI** (tag
`v0.12.0` on `d2ee803`, the merge of #611) and **the Rust crate 0.3.0 is on
crates.io** from the same commit; the CHANGELOG's `[0.12.0]` summary and
*Changed — breaking* are the release notes; see `HANDOVER_RUST.md` for the
crate's publish. Unreleased: PRs #613-#615, #620-#622, #625-#627, #629-#631,
#637 and this session's._

## This session: a long paper is condensed once for both PICO calls (#638)

Filed from StudyAnalyzer's plan 2c review (hherb/StudyAnalyzer#56):
`check_suitability` and `extract` each condensed their own text, and a retried
extraction condensed again. **`PicoAgent.prepare(title, text)`** returns a
frozen, storable **`PicoPrepared`** (stripped title, the text as passed, digest
or `None`, status, notes); **`check_suitability_prepared`** and
**`extract_prepared`** take it, one request each, quotes still verified against
the text. The direct calls route through the same code and send exactly what
they sent (the 37 old oracle expectations did not move); a prepared paper's
note names both calls. One prepared for a larger budget than the agent's own is
refused before any request (Python `ValueError`, Rust the new
`PicoError::PreparedOverBudget`). Rust has the same API, `from_json` refusing
word for word what `from_dict` refuses; the PICO oracle gains 16 cases (the
shared fixture the issue asked for). **For StudyAnalyzer plan 2c**: call
`prepare` once per paper (persist `to_json` if wanted), then
`check_suitability_prepared` and `extract_prepared`, retrying only the latter
on `RequestFailed`; build the agent that uses a stored prepared paper with the
same `condense_config`.

## Earlier, 2026-11-02: an unnamed numbered parent keeps its subsections (PR #637, merged)

A numbered top-level heading naming no section is kept as the new
`SectionType.OTHER` when the next heading numbers from it; 49 of 1,114 local
PDFs move, 326 subsections recovered, none lost; `PDF_CANONICAL_VERSION` 6.
Filed **#636** (the "2. Method"/"3. Result" pattern gap). **#628 stays open**
for run-in headings and bold back matter under 0.9 of the body.

## Earlier, 2026-11-01/02 (all merged; the CHANGELOG entries are the argument)

- **PR #631** (#628, part): an `Ita` suffix is italic; it moves no paper.

- **PR #630** (#628, part): `TextBlock.is_bold`/`is_italic` are the dominant
  span's flag **or** its font's name (`AdvOT….B`/`.I`/`.BI`, `Heavy`,
  `Black`, `Extrabold`, `Demi` but not `DemiLight`, a run-together `…It`).
  44 papers move, 320 heading lines gained, 30 lost (each a lone *Full Text*
  placeholder).

- **PR #629** (#628, part): a line in a paper's own heading face (learned from
  its exact-match headings, less the body's face) is judged as bold;
  Frontiers' unflagged numbered subsections. 177 headings found, none lost.
- **PR #627** (pdf-5): the body size is the median of the lines' *characters*;
  a line naming a section exactly is a heading at any size over the body, or
  bold or italic down to 0.9 of it. Filed **#628**. The title corpus's
  `median_font_size` rows are line medians: the next top-up must bump
  `ROW_DEFINITIONS_VERSION`.
- **PR #626** (#565): `FullTextExhaustedError` / Rust
  `FullTextError::Exhausted { message, faults, absences }`. **For
  StudyAnalyzer**: empty `faults` is "nothing failed", non-empty "a retry may
  succeed"; stop parsing `describe()`'s wording.
- **PR #625** (#603): `FullTextResult.tier_faults`, the fault type names the
  chain swallowed before a returned result; `[]` on a cache hit; a stale
  rendering served over a failed re-fetch carries the re-fetch's faults; a
  refused caller identifier is kept apart (`refuse()`) and not listed. A
  returned result carries no absence count (only `FullTextExhaustedError` does).
- **PR #622** (pdf-4): a PDF set the way Elsevier sets it segments — heading
  floor capped at the body size, numbered headings nest, first-line indents
  start paragraphs, TeX spacing accents joined by glyph position,
  `PDFBlocks.metadata_title`. Its review's residuals are **#623**.
- **PR #621** (Rust only): the CSV reader counted lines from the file start
  per record, so building a `RetractionIndex` from the 68 MB export took
  849 s; now a running count. bmlibrarian_lite must re-pin
  `BmlibFFI.pin.json` to pick it up.
- **PR #620** (#617, OpenAI-compatible half): an error-shaped 200 (`choices`
  empty or null, a non-JSON body, a JSON array) is a `ValueError` naming what
  arrived, and every other field is read as Rust reads it. **The Ollama half
  stays open by the maintainer's choice** (its SDK raises `TypeError` itself).
- **PR #615**: #578 (integer `sample_size` kept as digits; a blank
  `description` re-asked), #573 (`PicoAgent` lets a bmlib defect, an unknown
  provider and a missing SDK or key propagate — changes the "result or
  `PicoError`" contract; Rust keeps `RequestFailed`), #604
  (`FullTextResult.pdf_extraction_failure`). **For StudyAnalyzer**: read it as
  `invalid_input` for `"encrypted"`/`"unreadable"`, a retry for `"engine"`.
- **PR #614** (#599): what MuPDF reads past is reported
  (`extract_blocks_with_warnings` → `PdfBlocks`/`PDFBlocks`; one WARNING per
  read in Python). A capture must flush MuPDF's dedupe first (Rust
  `fz_flush_warnings`, Python `TOOLS.mupdf_warnings(reset=True)`). Left:
  **#619**, **#605**. **For StudyAnalyzer**: show the messages as a notice.

## What 0.12.0 costs a downstream

**Read the CHANGELOG's `[0.12.0]` summary and its *Changed — breaking*
heading** (the 0.11.0 heading likewise). The README's *Upgrading to 0.12* note points
at it; the Rust crate's 0.3.0 breaks are gathered in `rust/bmlib/CHANGELOG.md`.

## Rules carried forward, and previous sessions

**Rules are in [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)**; read
[`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything. **Each
session has a ROADMAP row and a `CHANGELOG.md` entry**; **a PR body is the
record**. **Read PR #285 before the next front-matter change.**

**Last sessions** (argument in `CHANGELOG.md`; most rules are in
`docs/SESSION-RULES.md`): PR #637 (#628, unnamed parent), PR #631 (#628, `Ita`), PR #630 (#628, faces named bold or italic), PR #629 (#628, heading faces), PR #627 (pdf-5, character median), PR #626 (#565), PR #625 (#603, `tier_faults`), PR #622 (pdf-4), PR #621 (Rust CSV line count), PR #620 (#617), PR #615 (#578, #573, #604), PR #614 (#599, what MuPDF reads past is reported), PR #613 (#574, `chat_json(max_retries=0)` makes one attempt; #570, a non-text `extracted_content` is no content, both languages), PR #601 (#594), PRs #596/#597 (#590/#591, MuPDF and page furniture), #593 (#592), #589 (#587/#588), #586 (item 8), #580/#584 (item 5), #575 (item 7), #568 (item 11), #562 (item 6), PR #559 (item 4c, `canonical_from_html`, `assess_full_text`), PR #556 (item 4b, `canonical_from_segmented`), PR #554 (item 3, `chunk_canonical`), PR #547 (item 4a, `bmlib.fulltext.canonical`), PR #545 (item 9, `FullTextResult.jats_xml` and the cache's `jats/`), PR #544 (#533, the `find_passage` timing tests bound windows scored), PRs #543 (#503 part 2, the `biorxiv` rows redrawn from `/pubs`), #542 (#503 part 1, the PDF-title journal stamped), #541 (#540, PubMed's bare `Review` measured as a displaced draw; three extractor corpora are pinned now), #539 (#453, a review read by how it searched; every `extractors.py` edit re-measures the corpora), #536 (#500, a value faking `str` is not text: `_is_text` asks `type()`, restated in `fulltext` and `transparency`; pytest's id generator is fooled by `Mock(spec=str)`, so give such a parametrize value an explicit id), #535 (#532, the embedder's errors typed alike in both languages: one `EmbedError` subclass per Rust variant, every one pickles; Rust `Llama` split into `Load`/`Decode`), #534 (#530, a NaN, infinite or zero embedding refused in both languages; a narrow model refused at load), #531 (`bmlib.embed`, the in-process arctic-embed embedder, Python and Rust; the embedder's review filed #530 and #532), #526 (`find_passage`, #527), #524 (#523/#522, `notice_pmid` canonical; `RetractionIndex::build` refuses an all-`Other` export), #521 (#509/#510, one export replaces the stored retraction notices; the export's `00000000` PMID sentinel), #519 (#518, a cited `<source>` normalised), #517 (#516, every `<label>` and the journal title normalised), #515 (#514, an abstract title normalised), #513 (#498, a title-only abstract section kept, by decision), #506 (#504, retraction status from three sources), #502 (#494, Tier 1d takes Europe PMC's own PDF; a PMID-only caller's Tier 2 asks Unpaywall by the hit's DOI), #501 (#489, a Tier 3 result naming no design leaves a conclusive Tier 1 standing), #499 (#485, a blank cache key), #484 (#475, a caller's DOI stripped and a blank one none, in `transparency` and `fulltext` alike), #482 (#145, a contributor's affiliations; the Rust port in the same PR),
#479 (#472/#478, `FullTextService`'s PMID
canonical and set aside rather than raised), #477 (#474, the PubMed record read is the one
asked about; books by the same rule), #473 (#470, PMID shape), #469 (#227, `pubmed_status`), #466 (**only a
`ValueError` or a declared non-text codec is the remote's bytes**), #464, #461, #458 (**record/replay a
sampler refactor**), #455, #452, and #437 (**every JATS PR trips `tests/test_renderer_version.py`** — bump
`RENDERER_VERSION` if `to_html()` can move, then re-pin). **Worktree recipe**: `git worktree add ../bmlib-x origin/main
-b <branch>`, then `uv venv .venv`, `uv pip install --python .venv/bin/python
-e ".[all,dev]"`, and run `env -u VIRTUAL_ENV uv run …`.

## Previous sessions: PDF failures, MuPDF in Rust, page furniture (#590-#600)

PRs #602 (#598, #600), #601 (#594), #596, #597 and #593, all merged; the
CHANGELOG entries are the argument. **#598**: `PdfErrorKind`/`PDFErrorKind`
(`encrypted`, `unreadable` = MuPDF's `FORMAT`/`SYNTAX`/`UNSUPPORTED`,
`engine` for everything else, `cancelled`, `timed-out`); messages never name
the path. **#600**: `PdfLimit`/`PDFLimit` (cancel flag, deadline) — mid-page
in Rust through MuPDF's cookie and a watcher thread, per-page in Python, the
maintainer's choice. **#594**: `FullTextResult.pdf_download_failure`
(`"http-<status>"`, `"not-a-pdf"`, `"request-failed"`, `"write-failed"`).
**#590**: Rust's `pdf` feature extracts with MuPDF (`mupdf` =0.8.0, MuPDF
1.27.2, statically linked); held by `tests/fixtures/pdf_extraction.{pdf,json}`.
**#591**: page furniture set aside (`page_furniture`, both languages,
measured over 231 PDFs). **#592**: Rust's cache saves create their
directories. Filed and open: **#595** (`render_html`'s weaker furniture
rule); #603 is done (PR #625). Residuals not filed: the MuPDF
1.27/1.28 gap, closing when the crate bundles 1.28; two upstream
`messense/mupdf-rs` bugs, not reported (the maintainer's call).

**For StudyAnalyzer**: map `Unreadable`/`Encrypted` to `invalid_input`,
`Engine` to an error; pass `PdfLimit { cancel, deadline }` to
`extract_blocks_within`/`page_texts_within`; a result with
`pdf_download_failure` set is a free PDF worth a retry, never "no full text
yet"; `MupdfExtractor::new()` replaced `PdfiumExtractor::bundled()`.


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
suggests another model, `TRUNCATED` a larger `max_tokens`; since #573 a
bmlib defect, an unknown provider and a missing SDK or key raise unwrapped in
Python, so catch them beside `PicoError` (#616: during condensation the defect
and the key error are still `CONDENSATION_FAILED`). In Rust, configure
`PicoAgent::pico_condenser` for long papers. Its reviews filed #572, #573,
#574, #576, #577 and #578.

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

**PRs #538-#543**: a non-ISO `RetractionEvidence.date` raises (#507; the
Rust/FFI half is **#537**); a review is read by how it searched (#453);
PubMed's bare `Review` is a displaced draw, so **an `extractors.py` edit
re-measures three corpora** (#540; adding `Review` to the strata is the
maintainer's decision); the PDF-title journal is stamped and its `biorxiv`
rows redrawn from `/pubs` (#503). **PR #524**: `notice_pmid` canonical;
`RetractionIndex::build` refuses an all-`Other` export, a rule **#520** (the
Rust desktop store's import) must take too.

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

- **Version 0.12.0** (released 2026-10-31, on PyPI). The version lives
  in **five** places — `pyproject.toml`, `bmlib/__init__.py`, the README
  version line, `CLAUDE.md`'s header, `docs/manual/index.md`'s header line —
  and all five agree; only `bmlib/__init__.py` is guarded by anything but this
  list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag;
  **0.9.0 moves nothing stored**; **0.9.1 moves stored full text** (#79);
  **0.10.0 moves nothing stored but re-fetches the whole sync window once**
  (#95); **0.11.0 moves stored values broadly** (see above); **0.12.0 re-renders cached full text by itself** (`RENDERER_VERSION` 7) and moves OpenAlex `pmcid` and extractor `study_type` (its CHANGELOG summary). The two questions are independent, and a downstream reading only the
  number must still read this list.
- **Tests: 8,946 passing + 165 skipped** on `feat/638-pico-prepare`
  (`uv run pytest tests/ -v`, 2026-11-03; Rust 1,418 with `cargo test -p bmlib --features pdf,html` on the same branch, manual doctests included; the embedder's live tests skip
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

**128 open** (`gh issue list --state open --limit 300 --json number`,
2026-11-03 once #638 closes, #628 and #636 among them, #617 still open for its Ollama half; #556–#628 are not yet
sorted into the lists below). **PR #615's review filed #616** (condensation turns a bmlib defect into `CONDENSATION_FAILED`; a design choice about the harness), **#617** (its OpenAI-compatible half done by PR #620; Ollama's SDK raises `TypeError` itself, left by decision) and **#618** (the PICO prompt does not ask for a description on a part not reported; a decision). **#619** (MuPDF's messages lost on a failed Rust read and mixed with bmlib's notes in Python; API changes in both), **#623** (pdf-4's review residuals: indent misses, a numbered quantity read as a subsection, the Rust failed-`/Title` test), **#605** (whether a truncated page is more than a warning; the maintainer's call) and **#595** (`render_html`'s weaker
furniture rule; measure the move by diffing before choosing) and **#628**'s
remaining causes are the PDF path's open work. **#528** and
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
