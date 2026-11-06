# HANDOVER — bmlib development

_Last updated: 2026-11-07 (**this session's PR is on `fix/606-ctgov-user-agent`**, for #606; PRs #690 (#689), #687 (#634), #684 (finalcheck skill), #683 (#608), #682 (#680) and #679 (#583) merged). **0.12.0 is on PyPI** (tag `v0.12.0`
on `d2ee803`, the merge of #611) and **the Rust crate 0.3.0 is on
crates.io** from the same commit; the CHANGELOG's `[0.12.0]` summary and
*Changed — breaking* are the release notes; see `HANDOVER_RUST.md` for the
crate's publish. Unreleased: PRs #613-#615, #620-#622, #625-#627, #629-#631,
#637, #639, #643, #644, #648, #650, #653, #655, #657, #660, #661, #663, #666,
#668-#672, #674-#679, #682-#684, #687, #690 and this session's._

## This session: ClinicalTrials.gov answers a default Rust client (#606)

Branch `fix/606-ctgov-user-agent`. **The issue's premise is refuted**: the
edge that 403'd any `User-Agent` without `python-httpx` on 2026-09-06 (#194)
served 200 on 2026-11-07 to bare `bmlib/0.3.0` through `UreqClient` (6 of 6,
alternating with the token's shape over six trials), and to `curl`,
`python-requests`, a browser string and bare `bmlib/0.12.0` through curl and
httpx. So **no code change**: the docs that said "refuses" now give both
dates (Rust manual, `http.rs`, `analyzer::user_agent`, Python's
`_user_agent.py`, the transparency manual, `CLAUDE.md`), Python's header
keeps its token as harmless, and the maintainer chose a Rust live guard:
`clinicaltrials_gov_answers_a_default_client` in `tests/live_network.rs`
(`BMLIB_LIVE_TESTS=1`) requests a trial with the default client and runs
`analyze()` on its paper (PMID 33301246), asserting the posted-results check
was answered. Breaking only the analyzer's request (`?fields=hasResultsBogus`)
fails it with `RequestFailed`. **No CI job runs it**, like the rest of that
file.

## Earlier, 2026-11-06: a `SecretError` says whether saving a key helps (#689, PR #690, merged)

Filed by StudyAnalyzer PR
hherb/StudyAnalyzer#92's review. **Half 1 was already fixed** by PR #687's review commit `cb7c35cf`
(the issue was filed against `26f69db9`, before it): a non-UTF-8 item reads
as unreadable and `set` replaces it. **Half 2**: `SecretError.kind` /
`SecretError::kind()`, a `SecretErrorKind` the same in both languages —
`unreadable-item` (saving a key replaces it), `ambiguous-item` (Rust's
`keyring` `Ambiguous`, on a read or a write; **not replaced**, the
maintainer's choice, since `set` would overwrite one of two items it cannot
see), `store` (everything else, Python's lone-surrogate refusal included).
Rust adds `SecretError::with_kind` (`new` stays, kind `Store`); Python's
`SecretError(message, kind=STORE)` pickles with its kind. The fixture's
`fail_read`/`fail_write` now name a kind and an error expectation names the
kind expected; a 13th scenario pins the ambiguous item. Review fixes: a
`Vault` that raises `unreadable-item` itself is replaced by `set` too (a 14th
scenario), Python's `kind` is coerced to the member (an unknown name raises),
and the fixture's `kinds` list holds both languages' names to it. **For StudyAnalyzer**:
show "saving a key replaces the damaged item" only for `UnreadableItem`;
`AmbiguousItem` asks the user to remove one item in the keychain.

## Earlier, 2026-11-06: API keys in the OS keychain, both languages (#634, PR #687, merged)

Moved from StudyAnalyzer (its phase 2b
built it; its follow-up PR deletes its copy and pins this). `bmlib/secrets.py`
and `rust/bmlib/src/secrets.rs`: `SecretStore` over a `Vault`, `KeyBundle`
(one item `{provider: key}`, read once for `get`s and cached, read again
before a `set`/`delete` rewrites it), `KeyringVault` (Python's
`keyring` package, extra `secrets`, in `all`; Rust's `keyring` crate v1,
feature `keyring`, off by default, its own CI clippy and doc steps, tested in
the `pdf` test job),
`MemorySecrets`, `read_key_list` / `write_key_list`. Shared fixture
`tests/fixtures/secret_key_list.json`: 26 reads (lone surrogates in a key
and a value, BOM, a duplicate provider hiding a non-string: Python's
`object_pairs_hook` refuses it as serde does), 8 exact writes, 12 `KeyBundle`
scenarios (a failed read is not cached; a write keeps another writer's key); a 100,000-deep value is a unit test
in each language (Python's `RecursionError`). No test touches a real
keychain. **Unverified live**, from both libraries' source: macOS (generic
password, service + account) is one item from both languages; **Linux** is
only while Python writes first (its `application` attribute makes a second
item beside Rust's, then Rust's lookup is `Ambiguous`); **Windows is not**
(the crate's target `account.service`, Python's `service`); both **#686**. iOS needs the crate's `protected` store, which the v1 stores do not
configure (v1 refuses iOS; StudyAnalyzer #74). **PR #687's review** made a
write re-read the item (a stale cache lost another writer's key), a non-UTF-8
item replaceable, Python's `SecretError` chain-free (a chained parse or
Unicode error held the item), Python's import failure say what was raised,
and Rust's `SecretError` field private (`new`, `message`). Left as is: the
`secret store: ` prefix on Rust's `Display` only; on Linux Python's `keyring`
reads whichever of two items Secret Service lists first (noted on #686).

## Earlier, 2026-11-06: Rust reads a closed-access record's full-text 500 as not served (#608, PR #683, merged)

Rust only. Python since #457 stores
`NOT_SERVED` (DEBUG) for a `fullTextXML` 500 when the record's
`isOpenAccess` is exactly `"N"`; Rust stored `RequestFailed` for every
non-404. `fetch_europepmc_fulltext` now takes the flag (read only to
interpret a 500, never to gate the request). Pinned by
`a_500_is_not_served_only_for_a_closed_access_record` in `tests/analyzer.rs`
(N/Y/absent/non-string flag, `n`/` N`/`No`, an error-page body, a 503, a
served `N` record; the request URL and the stored provenance line asserted);
six mutants killed (branch removed, any 500, trim, case-fold, prefix, a body
test). PR #683's review also corrected the function's rustdoc, the
`NotServed`/`RequestFailed` docs and the manual's differences list, which
gains the missing coercion tally (#209) as a stated gap. **Filed: #685** — no Rust
test can assert a log line's level (`log_line` is a bare `eprintln!`). No oracle change: the
transparency oracle covers the model rules only.

## Earlier, 2026-11-06 (merged): Rust lists a provider's models (#680, PR #682); an OpenAlex abstract's tie (#583, PR #679)

**#680**: `LlmClient::list_models(provider)` beside `context_window`; the pure
`read_model_listing` in `llm/model_listing.rs` and Python's
`bmlib/llm/model_listing.py`, held by `tests/fixtures/model_listing.json`
(the cloud bodies from each provider's API reference, not live). Ids only,
sorted, every page read; every failure an error in Rust (Python's
`LLMClient.list_models` answers `[]`, unchanged). **Filed: #681** — Rust
posts Gemini chats to `…/v1beta/openai/v1/chat/completions`; one live chat
decides it. **#583**: Python's `_reconstruct_abstract` sorts `(position,
word)` tuples, as Rust does; oracle case `abstract/tie-at-one-position`.

## Earlier, 2026-11-06 (all merged; the CHANGELOG entries are the argument)

- **#564** (PR #678): Rust's `normalise_pmc_id` and `EUROPEPMC_ACCESSION_RE`
  read `[0-9]`, not Unicode `\d`. Of the crate's 22 `\d` uses only these two
  port a Python `re.ASCII` pattern. **#471 keeps its other half** (Rust does
  not send an NCT id in ASCII as `_nct_id` does, and the PMID shape guard).
- **#624** (PR #677): Rust's `FetchedRecord.fulltext_sources` holds
  `fulltext::FullTextSourceEntry` (breaking for JSON indexing); shared
  fixture `fulltext_source_entry.json`. StudyAnalyzer drops its
  `retrieval::source_entries` shortcut.
- **#673** (PR #676): a split item's progress counts once, when the batch
  holding its last piece is done. `skipped_items` can still list one item
  twice (the count no longer reads it).

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
  `chat_stream_with_cancel`; abandoned, not interrupted (**#649**, **#652**).
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
`docs/SESSION-RULES.md`): PR #690 (#689, `SecretErrorKind`), PR #687 (#634, the keychain), PR #682 (#680, Rust model listing), PR #679 (#583, OpenAlex abstract tie), PR #678 (#564, Rust ASCII PMC digits), PR #677 (#624, typed full-text sources), PR #676 (#673, split-item progress), PR #674 (#609, condensation progress), PR #671 (#667, Ollama's plain 404), PR #670 (#619, `engine_messages`), PR #668 (#664, lookup `status`), PR #666 (#616, condensation cause), PR #663 (#662, PICO `status`), PR #661 (#659, PICO `retryable`), PR #660 (#658, batch causes), PR #657 (#646, refusals not retried), PR #655 (#654, the HTML size cap), PR #653 (#563, the HTML deadline), PR #650 (#635, cancellable streams), PR #648 (#632, the contradiction guard), PR #644 (#640-#642, PICO `num_ctx` and stop), PR #643 (#633), PR #639 (#638, PICO prepare), PR #637 (#628, unnamed parent), PR #631 (#628, `Ita`), PR #630 (#628, faces named bold or italic), PR #629 (#628, heading faces), PR #627 (pdf-5, character median), PR #626 (#565), PR #625 (#603, `tier_faults`), PR #622 (pdf-4), PR #621 (Rust CSV line count), PR #620 (#617), PR #615 (#578, #573, #604), PR #614 (#599, what MuPDF reads past is reported), PR #613 (#574, `chat_json(max_retries=0)` makes one attempt; #570, a non-text `extracted_content` is no content, both languages), PR #601 (#594), PRs #596/#597 (#590/#591, MuPDF and page furniture), #593 (#592), #589 (#587/#588), #586 (item 8), #580/#584 (item 5), #575 (item 7), #568 (item 11), #562 (item 6), PR #559 (item 4c, `canonical_from_html`, `assess_full_text`), PR #556 (item 4b, `canonical_from_segmented`), PR #554 (item 3, `chunk_canonical`), PR #547 (item 4a, `bmlib.fulltext.canonical`), PR #545 (item 9, `FullTextResult.jats_xml` and the cache's `jats/`), PR #544 (#533, the `find_passage` timing tests bound windows scored), PRs #543 (#503 part 2, the `biorxiv` rows redrawn from `/pubs`), #542 (#503 part 1, the PDF-title journal stamped), #541 (#540, PubMed's bare `Review` measured as a displaced draw; three extractor corpora are pinned now), #539 (#453, a review read by how it searched; every `extractors.py` edit re-measures the corpora), #536 (#500, a value faking `str` is not text: `_is_text` asks `type()`, restated in `fulltext` and `transparency`; pytest's id generator is fooled by `Mock(spec=str)`, so give such a parametrize value an explicit id), #535 (#532, the embedder's errors typed alike in both languages: one `EmbedError` subclass per Rust variant, every one pickles; Rust `Llama` split into `Load`/`Decode`), #534 (#530, a NaN, infinite or zero embedding refused in both languages; a narrow model refused at load), #531 (`bmlib.embed`, the in-process arctic-embed embedder, Python and Rust; the embedder's review filed #530 and #532), #526 (`find_passage`, #527), #524 (#523/#522, `notice_pmid` canonical; `RetractionIndex::build` refuses an all-`Other` export), #521 (#509/#510, one export replaces the stored retraction notices; the export's `00000000` PMID sentinel), #519 (#518, a cited `<source>` normalised), #517 (#516, every `<label>` and the journal title normalised), #515 (#514, an abstract title normalised), #513 (#498, a title-only abstract section kept, by decision), #506 (#504, retraction status from three sources), #502 (#494, Tier 1d takes Europe PMC's own PDF; a PMID-only caller's Tier 2 asks Unpaywall by the hit's DOI), #501 (#489, a Tier 3 result naming no design leaves a conclusive Tier 1 standing), #499 (#485, a blank cache key), #484 (#475, a caller's DOI stripped and a blank one none, in `transparency` and `fulltext` alike), #482 (#145, a contributor's affiliations; the Rust port in the same PR),
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


## Older StudyAnalyzer items: guidance still owed downstream

Items 8 (PR #586, `bmlib.embed.store`, imported as that and not re-exported),
5 (PRs #580/#584, `lookup_record`), 7 (PR #575, `PicoAgent`), 11 and 6
(PRs #568/#562, context windows, streamed chat); the CHANGELOG entries are
the argument. **Bump `EMBEDDING_RECIPE_VERSION` with any change that can move
a vector.** Filed from their reviews and open: #581-#583, #585, #572, #577,
#618, **#566** (Python's `get_model_metadata()` guesses), **#561** (Rust's
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
iterative; since #563 a page still parsing after `HTML_PARSE_SECONDS` is refused, read between chunks, and since #654 one over `HTML_MAX_CHARS` unparsed; `assess_full_text`, unmeasured,
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
- **Tests: 9,418 passing + 165 skipped** on `fix/606-ctgov-user-agent`
  (`uv run pytest tests/ -v`, 2026-11-07; 9,569 + 14 with the PostgreSQL DSN; Rust 1,613 with `cargo test -p bmlib --all-features` on the same branch before the review's two tests, 1,466 with `--features embed` on `feat/635-stream-cancel`; the embedder's live tests skip
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

**121 open once #606 closes** (#688 is a design choice, the `log` facade or a per-module sink) (`gh issue list --state open --limit 300 --json number`,
2026-11-07); #665 is #616's residual; #651 is #632's residual; #649 and #652 are #635's residuals (decisions: closing the socket; bounding Python's abandoned worker), #647 the PICO path's newest, #634 StudyAnalyzer's, #617 still open for its Ollama half; #556–#647 are not yet
sorted into the lists below). **PR #615's review filed** **#617** (its OpenAI-compatible half done by PR #620; Ollama's SDK raises `TypeError` itself, left by decision) and **#618** (the PICO prompt does not ask for a description on a part not reported; a decision). **#623** (pdf-4's review residuals: indent misses, a numbered quantity read as a subsection, the Rust failed-`/Title` test), **#605** (whether a truncated page is more than a warning; the maintainer's call) and **#595** (`render_html`'s weaker
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
