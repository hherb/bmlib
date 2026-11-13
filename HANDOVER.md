# HANDOVER — bmlib development

_Last updated: 2026-11-13 (**#750 and #751, PR open — read *This session*
first**). Merged since the last update: #749 (#748, below). Before it: #747
(#746), #745 (#744), #740 (#656), #741 (#739), #742 and #743. **0.13.0 is on
PyPI** (tag `v0.13.0` on `161849c0`, the merge of #736) and **the Rust crate
0.4.0 on crates.io** from the same commit._

## This session: PMC Open Data's record and JATS (#750, #751, PR open)

The maintainer chose both of #748's follow-ups, and an own lookup for the
retraction flag rather than a `FullTextResult` field (a field would be empty
whenever the bucket was not consulted, retracted papers included). Both
languages, on `feat/pmc-open-data-json`:

- **`pmc_open_data_record(pmc_id)`** → `PmcOpenDataRecord | None` (Rust
  `Result<Option<_>, FullTextError>`): the versions listing, then the latest
  version's `PMC<n>.<v>.json`. A record not of the shape read (a JSON type,
  another article or version, a file URL naming another key) is refused, not
  guessed at; a 404 record is `None`.
- **The PDF beside JATS** reads the record instead of #748's PDF-key listing
  (same two requests) and checks the bytes against its md5:
  `"checksum-mismatch"`, a new `pdf_download_failure`, which may pass
  (`pdf_failure_may_pass.json`).
- **Tier 1c's third source**: when Europe PMC and NCBI give no body, the
  bucket's JATS, md5-checked, `source` `"pmc_open_data"` (`_try_pmc_id` is now
  a loop in Rust too). Every case where neither gives a body answers one more
  request, so 11 Python tests, 9 Rust tests and 3 `fulltext_source.json` cases
  gained the listing; 4 new contract cases; the two PDF-key listing fixtures
  are replaced by three real-shaped records (their md5s are those of
  `sample_article.xml` and `textless.pdf`: edit either file and update them).
- Rust gains `md-5` 0.10. `test_renderer_version.py` re-pinned (models.py
  moved, no rendering change, no version bump).
- **Live** (Python, 2026-11-13): PMC12609458's record, JATS and PDF read, both
  md5s matched; a wrong md5 gave `checksum-mismatch`. Rust was not run live.
- **Not done**: one record per fetch (a JATS from the bucket reads the record
  again for its PDF, two extra requests); the bucket's `.txt`; feeding
  `is_retracted` to `retraction_status` (a design question, not filed).
- **Gates**: one `cargo test --features pdf,html,keyring` and one
  `-p bmlib --no-default-features` run failed once while the live probe ran
  and passed on a rerun; the first output was overwritten, so the cause is
  unknown. After the review fixes all four Rust test configurations passed at
  the first run.
- **Review fixes (finalcheck, same PR)**:
  - A record missing a key is refused. Live records from 2010 to 2025 write
    every key, with an explicit `null` where there is no value. Before, a
    renamed `xml_url` would have read as "no JATS", an absence, for every
    article.
  - A PMID that is not a positive integer within u64 is refused.
  - The record is decoded as `serde_json` reads it (`strict_json_loads`,
    #572).
  - A 200 listing that is not a `ListBucketResult` is a fault, where it read
    as "no version" (from #748).
  - `PmcOpenDataRecord.from_dict` refuses to guess. Rust's `Deserialize`
    checks types only, which its doc says.
  - Each refusal is asserted with its reason, in both languages.
  - Four more contract cases: no JATS, a JATS 404, a JATS with no content,
    and a listing that is not one. They add `pmc_open_data_record_no_xml.json`,
    `pmc_open_data_record_empty_article.json` and
    `pmc_open_data_empty_article.xml`.
  - Not done:
    - The three older `resp.json()` decodes in `service.py`, filed as
      #753.
    - Rust's public `pmc_open_data_record` gives a transport failure as
      `FullTextError::Other`, where Python raises `httpx.HTTPError`. This is
      documented on the method.

## Earlier: the PDF beside JATS from PMC's AWS Open Data bucket (#748, merged as PR #749)

A `pmc_open_data` candidate after the caller's `pdf` entries, before
`europepmc_pdf`, by the JATS's own PMC ID; a marked hit with a PMC ID can seek
without a DOI or PMID. StudyAnalyzer's PR #105 met a bot screen's 403 from every
other PDF link of a CC BY article; impersonating a browser was refused.

## Earlier: the PDF beside JATS reports the failure that may pass (#746, merged as PR #747)

`http-408` may pass; with no PDF saved, the result carries the first
failure that may pass, else `"search-failed"`, else the first failure, so
the reported failure follows the mark. Seven `fulltext_source.json` cases.

## Earlier: the PDF beside JATS is retried and sought again (#744, merged as PR #745)

A download whose cause may pass is tried `PDF_BESIDE_JATS_ATTEMPTS` (3)
times; with no PDF saved and any failure that may pass, the cache marks the
JATS (`jats/<key>.pdf-retry`), and a later hit seeks again, at most
`PDF_SEEK_AGAIN_LIMIT` (3) hits, counting itself before it seeks; a failed
search is `"search-failed"`. Detail in CHANGELOG and the manual.
- **Not covered**: a JATS cached while the option was off carries no mark
  (the `shortcut:` in `_check_cache`); a failed Tier 0 PDF is not retried in
  the run, only on a later marked hit.
- **Left by the finalcheck review** (minor, no issue filed): the two
  languages read a count past `u32` in a mark differently (no shared fixture
  holds the mark's text); Rust's mark removal names no OS cause and has no
  test; with a broken delete a paper gets `PDF_SEEK_AGAIN_LIMIT` − 1 looks.

## Earlier: a saved page is not refused for its length (#656, merged as PR #740)

Both languages cap a page's `<` (`HTML_MAX_TAGS`, 666,666), not its length;
Rust's element budget is `3 × tags + 1,000` (`ELEMENTS_PER_TAG`). The
measurement (`scripts/sample_html_pages.py --saved-dir`, corpus
`tests/data/html_saved_page_shapes.json`) and argument are in CHANGELOG and
DECISIONS (#656). Still open from it: 61 of 153 saves were Cloudflare's
challenge page (headless Chrome; a non-headless re-run would reach them);
**#726** is answered by its measurement and closeable (the maintainer's
call); **#725** (memory under pressure) stays unmeasured.

## Release 0.13.0 / Rust crate 0.4.0 (released 2026-11-10)

Done on `release/0.13.0` (PR #736, merged as `161849c0`, tagged `v0.13.0`):
CHANGELOGs promoted and their sub-headings merged, versions in the five Python
places and the Rust workspace, 206 `unreleased` markers promoted, the gates
rehearsed. PyPI has the wheel and sdist (02:39Z, with provenance); crates.io has
0.4.0 from a clean worktree of the same commit. Detail: PR #736 and the
CHANGELOGs.

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

- **#609** (PR #674): `PicoAgent`/`CochraneAssessor(condense_progress=)`;
  Rust `LlmCondenser::with_progress`. Rust's harness now stops at a stop
  (`stop_requested`). → pass a progress callback and show the batches.
- **#667** (PRs #669, #671): Ollama's plain-text `404 page not found` is a
  wrong address, not `NoSuchModelError` (Rust's rule, in Python).
- **#619** (PR #670): `engine_messages` (what MuPDF said) apart from
  `warnings` (bmlib's notes), also on a failed read. → rename
  `PdfBlocks.warnings` reads and show a failed read's `engine_messages`.
- **#664** (PR #668): `ContextWindowError` carries the HTTP status (Rust
  `Transport { message, status }`, breaking). → map `Some(401 | 403)` to
  `needs_api_key`.
- **#616** (PR #666): `ProcessingResult.error`/`.batch_exceptions`;
  `ModelCallError` chained; a long paper's 401/404 is `RequestFailed {
  status }`. Filed **#665** (a decision).
- **#659, #658, #646** (PRs #661, #660, #657): `PicoError.retryable`;
  `batch_errors` and `summarise_batch_errors`; the condenser retries only a
  transport failure or 408/429/500/502/503/504/529. → retry `RequestFailed {
  retryable: true }` only, and on the next pin move add a `ChatError::Http`
  arm where a `Transport` arm meant "the request failed".
- **#654, #563** (PRs #655, #653): `canonical_from_html` refuses a page over
  `HTML_MAX_CHARS` unparsed and one past `HTML_PARSE_SECONDS`; Rust
  `HtmlError { TooLarge, TimedOut }`. Filed **#656**. → a too-large page is
  not worth retrying, a timeout may be.
- **#635** (PR #650): `chat_stream(..., cancel=)`, Rust
  `chat_stream_with_cancel`; given up at the socket since #649/#652 (PR #714).
  → an empty `content` with `stop_reason == "cancelled"` is "stopped before
  any text".
- **#632, #640-#642, #638** (PRs #648, #644, #639): `find_passage` rejects a
  contradicting fuzzy match (filed **#647**, **#651**); `PicoAgent(num_ctx=,
  stop=)`; `prepare()` → `PicoPrepared`. → threshold back to 0.95-0.97, call
  `prepare` once per paper, retry only `extract_prepared`, and build `LlmChat`,
  `LlmContextModel` and the agent with the same window, stop flag and
  `condense_config`.
- **PDF segmenter** (PRs #637, #636's, #631/#630/#629, #627, #622): an
  unnamed numbered parent kept as `SectionType.OTHER` (`PDF_CANONICAL_VERSION`
  6); heading faces; pdf-5's character median (the title corpus's
  `median_font_size` rows are line medians, so the next top-up bumps
  `ROW_DEFINITIONS_VERSION`); residuals **#623**, **#628** (run-in headings).
- **Full-text faults** (PRs #626, #625, #615, #614, #620, #621):
  `FullTextExhaustedError.faults`, `FullTextResult.tier_faults`, read
  `pdf_extraction_failure` `"encrypted"`/`"unreadable"` as `invalid_input`,
  `"engine"` as a retry; bmlibrarian_lite must re-pin `BmlibFFI.pin.json`
  (#621); **#605** open.

## Rules carried forward, and previous sessions

**Rules are in [`docs/SESSION-RULES.md`](docs/SESSION-RULES.md)**; read
[`docs/DECISIONS.md`](docs/DECISIONS.md) before "fixing" anything. **Each
session has a ROADMAP row and a `CHANGELOG.md` entry**; **a PR body is the
record**. **Read PR #285 before the next front-matter change.**

**Last sessions** (argument in `CHANGELOG.md`; most rules are in
`docs/SESSION-RULES.md`): this session (#750/#751, PMC Open Data's record and JATS), PR #749 (#748), PR #747 (#746), PR #745 (#744), PR #741 (#739), PR #740 (#656, the `<` cap), PR #738 (#726, the HTML limits measured), PR #736 (release 0.13.0 housekeeping), PRs #732/#735 (live probes), PR #729 (#708), PR #728 (#665), PR #727 (#647, PICO contradicted quotes), PR #724 (#723, HTML memory bound), PR #721 (#607/#444, Rust sync parity), PR #720 (#686, one keychain item), PR #719 (#717, the JSON correction note), PR #715 (#572, numbers JSON does not define), PR #714 (#649/#652, a cancelled stream closes its socket), PR #713 (#710, adaptive thinking's summary), PR #711 (#707, thinking shape), PR #712 (#706's review), PR #709 (#705, Anthropic sampling), PR #706 (#702, a chat waits as a stream does), PR #704 (#698, too-deep bodies), PR #701 (#697, Ollama embed/show bodies), PR #700 (output limits), PR #699 (#617's refusal half), PR #693 (#688, Rust `log`), PR #691 (#606, CT.gov live guard), PR #690 (#689, `SecretErrorKind`), PR #687 (#634, the keychain), PR #682 (#680, Rust model listing), PR #679 (#583, OpenAlex abstract tie), PR #678 (#564, Rust ASCII PMC digits), PR #677 (#624, typed full-text sources), PR #676 (#673, split-item progress), PR #674 (#609, condensation progress), PR #671 (#667, Ollama's plain 404), PR #670 (#619, `engine_messages`), PR #668 (#664, lookup `status`), PR #666 (#616, condensation cause), PR #663 (#662, PICO `status`), PR #661 (#659, PICO `retryable`), PR #660 (#658, batch causes), PR #657 (#646, refusals not retried), PR #655 (#654, the HTML size cap), PR #653 (#563, the HTML deadline), PR #650 (#635, cancellable streams), PR #648 (#632, the contradiction guard), PR #644 (#640-#642, PICO `num_ctx` and stop), PR #643 (#633), PR #639 (#638, PICO prepare), PR #637 (#628, unnamed parent), PR #631 (#628, `Ita`), PR #630 (#628, faces named bold or italic), PR #629 (#628, heading faces), PR #627 (pdf-5, character median), PR #626 (#565), PR #625 (#603, `tier_faults`), PR #622 (pdf-4), PR #621 (Rust CSV line count), PR #620 (#617), PR #615 (#578, #573, #604), PR #614 (#599, what MuPDF reads past is reported), PR #613 (#574, `chat_json(max_retries=0)` makes one attempt; #570, a non-text `extracted_content` is no content, both languages), PR #601 (#594), PRs #596/#597 (#590/#591, MuPDF and page furniture), #593 (#592), #589 (#587/#588), #586 (item 8), #580/#584 (item 5), #575 (item 7), #568 (item 11), #562 (item 6), PR #559 (item 4c, `canonical_from_html`, `assess_full_text`), PR #556 (item 4b, `canonical_from_segmented`), PR #554 (item 3, `chunk_canonical`), PR #547 (item 4a, `bmlib.fulltext.canonical`), PR #545 (item 9, `FullTextResult.jats_xml` and the cache's `jats/`), PR #544 (#533, the `find_passage` timing tests bound windows scored), PRs #543 (#503 part 2, the `biorxiv` rows redrawn from `/pubs`), #542 (#503 part 1, the PDF-title journal stamped), #541 (#540, PubMed's bare `Review` measured as a displaced draw; three extractor corpora are pinned now), #539 (#453, a review read by how it searched; every `extractors.py` edit re-measures the corpora), #536 (#500, a value faking `str` is not text: `_is_text` asks `type()`, restated in `fulltext` and `transparency`; pytest's id generator is fooled by `Mock(spec=str)`, so give such a parametrize value an explicit id), #535 (#532, the embedder's errors typed alike in both languages: one `EmbedError` subclass per Rust variant, every one pickles; Rust `Llama` split into `Load`/`Decode`), #534 (#530, a NaN, infinite or zero embedding refused in both languages; a narrow model refused at load), #531 (`bmlib.embed`, the in-process arctic-embed embedder, Python and Rust; the embedder's review filed #530 and #532), #526 (`find_passage`, #527), #524 (#523/#522, `notice_pmid` canonical; `RetractionIndex::build` refuses an all-`Other` export), #521 (#509/#510, one export replaces the stored retraction notices; the export's `00000000` PMID sentinel), #519 (#518, a cited `<source>` normalised), #517 (#516, every `<label>` and the journal title normalised), #515 (#514, an abstract title normalised), #513 (#498, a title-only abstract section kept, by decision), #506 (#504, retraction status from three sources), #502 (#494, Tier 1d takes Europe PMC's own PDF; a PMID-only caller's Tier 2 asks Unpaywall by the hit's DOI), #501 (#489, a Tier 3 result naming no design leaves a conclusive Tier 1 standing), #499 (#485, a blank cache key), #484 (#475, a caller's DOI stripped and a blank one none, in `transparency` and `fulltext` alike), #482 (#145, a contributor's affiliations; the Rust port in the same PR),
#479 (#472/#478, `FullTextService`'s PMID
canonical and set aside rather than raised), #477 (#474, the PubMed record read is the one
asked about; books by the same rule), #473 (#470, PMID shape), #469 (#227, `pubmed_status`), #466 (**only a
`ValueError` or a declared non-text codec is the remote's bytes**), #464, #461, #458 (**record/replay a
sampler refactor**), #455, #452, and #437 (**every JATS PR trips `tests/test_renderer_version.py`** — bump
`RENDERER_VERSION` if `to_html()` can move, then re-pin). **Worktree recipe**: `git worktree add ../bmlib-x origin/main
-b <branch>`, then `uv venv .venv`, `uv pip install --python .venv/bin/python
-e ".[all,dev]"`, and run `env -u VIRTUAL_ENV uv run …`.

## PDF failures, MuPDF in Rust (#590-#600, merged)

Open: **#595**. **For StudyAnalyzer**: `Unreadable`/`Encrypted` → `invalid_input`,
`Engine` → error; pass `PdfLimit { cancel, deadline }`; `pdf_download_failure`
set is a free PDF worth a retry; `MupdfExtractor::new()` replaced `PdfiumExtractor::bundled()`.

## Older StudyAnalyzer items: guidance still owed downstream

Items 8 (PR #586, `bmlib.embed.store`, imported as that and not re-exported),
5 (PRs #580/#584, `lookup_record`), 7 (PR #575, `PicoAgent`), 11 and 6
(PRs #568/#562, context windows, streamed chat); the CHANGELOG entries are
the argument. **Bump `EMBEDDING_RECIPE_VERSION` with any change that can move
a vector.** Filed from their reviews and open: #581, #582, #585, #577 (its point 3 left open by PR #696; #583, #572 and #618 since fixed), **#566** (Python's `get_model_metadata()` guesses), **#561** (Rust's
`chat()` retries nothing).

- **Plan 2b**: `download_model`, then `Embedder::load_model(&ARCTIC_EMBED_L_V2_F16,
  &path)` (never `load`; the `ModelFile` must be the one downloaded);
  `embed::store::ensure_schema` once; `index_paper(...)` after converting
  (`false` from progress cancels and keeps the old set); `search(...)` for
  retrieval mode. `Stale(_)` → re-index (re-convert first if
  `ConverterVersion` is among the fields); `NotIndexed` → index; `Corrupt` →
  re-index. F16 ↔ Q8_0 makes every paper stale. Clamp the user's window to
  `context_window()`, send `num_ctx` for Ollama, `None` means retrieval mode,
  `NoSuchModel` a mistyped model; `chat_stream(…, on_delta)` returns
  `chat()`'s `LLMResponse`, a `False`/`Break` stopping it.
- **Plan 2a**: `lookup_record` with a PPR accession as `ppr_id`; hand
  `fulltext_sources`, `pmc_id`, `doi`, `pmid` to `fetch_fulltext`; show
  `published_version`; `request_failed` at any source is worth a retry.
- **Plan 2c**: `check_suitability` on the abstract, a "not suitable" answer
  with a *run anyway* action calling `extract`; pass `CanonicalText.text`;
  label `UNVERIFIED` *Unsure – model could not reproduce <part> from text
  provided*; retry `REQUEST_FAILED` (when `retryable`), `UNUSABLE_REPLY`
  suggests another model, `TRUNCATED` a larger `max_tokens`; in Python a bmlib
  defect, an unknown provider and a missing SDK or key raise unwrapped (since
  #616 during condensation too). In Rust configure `PicoAgent::pico_condenser`.

**Earlier StudyAnalyzer sessions** (argument in `CHANGELOG.md`): PR #559 (4c,
`canonical_from_html`, `"html-1"`, html5ever in both languages, every walk
iterative; since #563 a page still parsing after `HTML_PARSE_SECONDS` is refused, read between chunks, since #654 one over `HTML_MAX_CHARS` unparsed, and since #656 one over `HTML_MAX_TAGS` `<` instead; `assess_full_text`, unmeasured,
**#560**; **#557**, **#558** after 2026-11-04); PR #556 (4b,
`canonical_from_segmented`, `"pdf-1"`, contract from the `SegmentedDocument`);
PRs #554 and #555 (item 3, `chunk_canonical`, contract
`canonical_chunks.json` from its committed generator); PR #547 (4a, the model,
JATS, Markdown, plain text; its reviews left #548 and #549 (done), **#552**,
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
- **Tests: 10,532 passing + 18 skipped with the PostgreSQL 18 DSN** (port 5532,
  `BMLIB_REQUIRE_POSTGRESQL=1`) on `feat/pmc-open-data-json` (`uv run pytest tests/`, 2026-11-13; Rust 1,730 with `cargo test --workspace` there, 1,720 with `--all-features` on `measure/656-saved-pages`, and `postgres_live.rs` 13 with `BMLIB_PG_TESTS=1 BMLIB_PG_PORT=5532 BMLIB_PG_USER=postgres`, 1,466 with `--features embed` on `feat/635-stream-cancel`; the embedder's live tests skip
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

**120 open**, three of them this session's own (#750 and #751, which the PR closes, and #753, filed by its review) (#734, #733, #731, #730, #726, #725, #722, #718, #716 and #694 are the newest; #577 stays open for its point 3, which PR #696 left open) (`gh issue list --state open --limit 300 --json number`,
2026-11-13); #651 is #632's residual; #556–#734 are not yet
sorted into the lists below). **PR #615's review filed** **#617** (both halves done, PRs #620 and #692) and #618 (a part not reported may now have a blank description; fixed with #577 in PR #696, the maintainer's call of 2026-11-07). **#623** (pdf-4's review residuals: indent misses, a numbered quantity read as a subsection, the Rust failed-`/Title` test), **#605** (whether a truncated page is more than a warning; the maintainer's call) and **#595** (`render_html`'s weaker
furniture rule; measure the move by diffing before choosing) and **#628**'s
remaining causes are the PDF path's open work. **#528** and
**#529** are PR #526's review's, both `find_passage` decisions: a superscript
digit makes different numbers match exactly (`10³` vs `103`), and a quote
whose seeds are all too common is rejected at 0.0. **#537** is #507's Rust/FFI half (refusing a non-ISO evidence date needs `retraction_status` to return a `Result`, an app API change); **#520** is the Rust store's import, which owes #522's nature rule too. **Five more filed on 2026-10-17 are in no list below yet**: the FFI
spike's #491, #492, #493 and #496; and the Rust reader's #495 (per-element recursion aborts on
deep nesting). They are: the Rust audit's #314 (a
decision), the Rust side's #332, **#467** (follow #463's port parity), **#468** (follow #227), **#471** (follow #470), **#476** (follow #474), **#483** (follow #475), #409 (follow #406), #411 (follow #407),
#416 (follow #413/#415), #421 (follow #414), #426 (follow #423), #432 (follow #425/#429; now also #145's five Python-only fixtures) and #436 (follow #172), and the Python list: #92, #94, #128, #137, #142, #143, #144,
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
