# HANDOVER — the Rust port of bmlib

_Last updated: 2026-09-26. **The port is functionally complete and merged.** `main`
is at `3f48134`, the merge of PR #328. No pull request is open; every piece of work
described below is on `main`. The Python library was **not modified** by the port —
`git status --porcelain bmlib/` is empty, and that is the state to preserve._

**Read [`rust/README.md`](rust/README.md) first for how to build and run it, and
`docs/plans/2026-09-26-rust-port-roadblocks.md` §0 and §9 for the fidelity contract
and the divergence register.** This file is the one that says what is *left*, and
what will bite you.

## Where it stands

| | |
|---|---|
| Tests | **826 passing, 0 failing** (`cargo test` — 823 tests in 63 binaries + 3 doc-tests), **834** with `--features pdf` |
| Lint | `cargo clippy --all-targets` **0 warnings**; `cargo fmt --check` clean; `ruff check .` clean |
| Size | 66,620 lines of Rust — 75 source files, 64 test files |
| Oracles | **38 corpora, 2,552 cases**, 40 `oracle/dump_*.py` drivers |
| Python | untouched |

Build and test:

```bash
cd rust
CARGO_HOME="$PWD/.cargo-home" cargo test          # a sandbox that denies ~/.cargo
cargo test --features pdf                          # + 8 PDFium tests over real PDFs
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1   # 6 live requests
```

**The last command needs `--test-threads=1`**: NCBI rate-limits by source address,
so a concurrent run draws 429s that read as parse failures. The live suite is
**gated** — the default `cargo test` opens no socket. Without the variable its six
tests each return immediately, so the binary reports `ok. 6 passed` in ~0.09s; the
count is the same either way, which is deliberate (a suite that silently
disappeared would be worse than one that runs).

## Session note (round 40) — two Appendix defects the port still reproduced

Round 39's audit claimed every enumerated defect was accounted for. Re-deriving
each Appendix row against the Rust **source** rather than its module docs found
**two rows still reproduced**; both are now fixed, with tests, and the Python
library is still untouched (`git status --porcelain bmlib/` empty):

- **#310** — `CochraneStudyCharacteristics::from_json`, and the assessment reader
  above it, required the keys Python indexes directly, so the partial
  `cochrane_assessment` the permissive write path invites could not be read back.
  Both now read leniently. Pinned by the cochrane corpus's **first two `corrected`
  oracle cases** (the Python side still asserted to raise `KeyError: 'methods'`),
  with a companion test asserting both cite #310.
- **#309 part 2** — `FullTextCache::get_pdf` was `path.exists().then_some(path)`,
  so a directory at the PDF entry was a cache hit for ever. It now consults
  `is_readable` and the service quarantines the entry as its HTML branch already
  did. Two tests cover it (cache and service level).

Everything else re-derived clean: all 40 dumpers regenerate from the live Python
and match what is committed, `cargo clippy --all-targets` is clean,
`cargo fmt --check` is clean, and the gated live suite passes 6/6. The plan's
round-39 section is annotated, and a round-40 section records the two fixes.

## The method, which is the part worth keeping

Every module was ported against a **differential oracle**, not written to match a
reading of the Python:

1. Write `rust/oracle/dump_<thing>.py` that runs the **live Python** over a JSON
   case corpus on stdin and writes expectations on stdout.
2. Commit the cases *and* the expectations under `rust/bmlib/tests/data/`.
3. The Rust test diffs **parsed** values, never text.

**All 38 corpora regenerate from the live Python and match what is committed.**
That is what makes them evidence rather than fixtures, and it is re-derivable:

```bash
cd /Users/hherb/src/bmlib
.venv/bin/python rust/oracle/dump_cache.py < rust/bmlib/tests/data/cache_cases.json \
  > /tmp/fresh.json
diff <(python3 -m json.tool /tmp/fresh.json) \
     <(python3 -m json.tool rust/bmlib/tests/data/cache_expected.json)
```

Re-run that over every corpus before believing anything below. A **stale oracle is
worse than no oracle**: it agrees with a port that has drifted.

A case may carry a `corrected` block where the port deliberately differs, with its
reason. Those are the §9 divergences and they are the only differences to accept
silently.

## What is left

**Nothing that blocks a release.** What remains is three categories, and the first
is the one to read.

### 1. Deliberate scope decisions, documented — do not "fix" these

§"What is deliberately dropped" of the plan covers them. In short:

- **Ollama's `list_models`/`show()` metadata** (~600 lines of Python; it exists
  because the Ollama SDK's Pydantic model dropped two fields).
- **Streaming tool-call assembly** — the plan calls it *"the fiddly part"* and
  expects less than the Python does.
- **`list_models`, `test_connection`, `count_tokens`, `embed`/`embed_batch`,
  `get_model_metadata`, `get_provider_info`** are absent. `chat` is complete
  including `tools`, `tool_choice`, `think` and `json_mode`. `count_tokens`'s
  accuracy was never established downstream, which is why it was not ported as-is.
- **No process-wide `get_llm_client()` singleton**, with the reasoning on
  `LlmClient` itself: the Python singleton made construction cheap (an SDK import,
  environment credentials, a pool) and `LlmClient::new` does none of that.

### 2. Documentation drift to repair

- **The plan's §"Phase 4" still says the PDF backend is "outstanding"** and that
  *"linking a PDF library is the one remaining integration"*. That is stale: the
  PDFium backend landed, behind the optional `pdf` feature. Fix the plan.
- `rust/README.md` has been kept current; the plan has not, in that one place.

### 3. Verification that would raise confidence, not features

These are real and open, and each is a *measurement* rather than an implementation:

- **The live suite is not a CI gate, deliberately.** An outage, an egress block or
  a rate-limit would redden it for a reason that is not this code. If you want it
  scheduled, a weekly `workflow_dispatch`-style job is the shape — but decide
  whether a red run would mean anything before adding it.
- **`TransparencyResult::to_dict`/`from_dict` diverges from Python on
  `coi_disclosed`** (#306's correction reaching the persistence path): a row with no
  `coi_disclosed` reads back as `None` here where Python's dataclass default gives
  `True`. Intentional and recorded; a downstream round-tripping rows across the two
  implementations sees it.
- **A few coverage gaps delegated ports named and did not close**: `default_cache()`
  has no test (it would mutate process-global `$HOME`); the condensation map-reduce
  in `cochrane_assessor` runs only against a stub; the funder-count corpus's
  *measurements* have no Rust counterpart (the corpus itself does — see below).
- **`HttpResponse.body` is `Vec<u8>` and the live backend is exercised, but no
  test drives a real provider chat call.** The LLM transport is scripted. A live
  chat test needs a key and would cost money, which is why it does not exist; if
  you add one, gate it exactly as `live_network.rs` is gated.

## Open Python-side issues the port surfaced

**These are Python work, not Rust work, and they are the most valuable things this
session produced.** Each came from an instrument rather than a reading.

- **[#325](https://github.com/hherb/bmlib/issues/325) — `biorxiv.py` reads a dead
  endpoint.** `https://api.biorxiv.org/details` answers **HTTP 200 with a
  zero-byte body** while still sending `content-type: application/json`, so the
  JSON read fails and **every bioRxiv sync day errors**. `/pubs` serves the same
  days, but **its field names differ** (`preprint_doi`, not `doi`) and **its
  population differs** — it returns published pairs, 34 for 2024-01-15 where
  bioRxiv posts several hundred preprints a day. The issue lays out three options
  including "leave `/details` and fail loudly"; the population question is a
  maintainer decision, not a porting one. **The Rust side was corrected on
  instruction** (`BASE_URL` is `/pubs`, `normalize` reads both spellings), so the
  two implementations now differ in this one place.
- **[#317](https://github.com/hherb/bmlib/issues/317)–[#320](https://github.com/hherb/bmlib/issues/320)**
  — four type-contract defects in `cochrane_models` and the quality readers, found
  by the delegated ports and verified independently before filing. #317 is the
  interesting one: `COCHRANE_RESPONSE_FORMAT` tells the model *"Use null for any
  field the text does not report"*, so a **compliant** model reaches
  `data.get(k, default)` returning `None` for a `str`-annotated field.
- **[#316](https://github.com/hherb/bmlib/issues/316) — fixed.** It was a defect in
  the port, not the Python: `HttpResponse.body` was a `String`, so a binary PDF
  reached the cache with every non-UTF-8 byte replaced by U+FFFD, **undetectably**
  (`%PDF` is ASCII and survives; the cache's only check is that prefix).

## Gotchas that cost this session real time

Each of these is written down because it produced a **wrong answer**, not merely
lost minutes.

- **`cargo`'s coarse mtime staleness.** An edit and a rebuild inside the same
  second can read a stale rlib, so the binary behaves like the old code. It bit
  this port **twice**. When behaviour looks impossible, `cargo clean -p bmlib`.
- **Check the gate by grepping the whole output, not a pipe.** Counting
  `^warning:` on a partially-consumed stream reported "clean" while four warnings
  existed. It fails in the **reassuring** direction, which is the worst kind.
- **Concurrent edits produce phantom failures.** While subagents worked, an exit
  code 101 with no matching failure line was a mid-write file, not a regression.
  Re-run before investigating.
- **A process-wide global makes tests order-dependent.** The recording tests in
  `cost.rs` take a lock, because `TokenTracker` is process-wide and two tests
  interleaved their before/after snapshots. Assert **deltas**, and serialise.
- **Mutation testing: assert the pattern matches exactly once, and restore from a
  separate backup.** A `cp`-based restore once clobbered a test file with an
  early snapshot. Brace-balancing mutations often fail to apply — a mutation that
  does not apply is not a passing test.
- **`Document::parse` in `roxmltree` refuses a DTD by default.** Every live NCBI
  response carries one, so the whole PubMed path was dead against the real service
  while every fixture passed. `parse_ncbi_xml` sets `allow_dtd`. **This is the
  single best argument for the live suite.**
- **PDFium's `font_weight()` and `font_is_italic()` return nothing usable for a
  base-14 face** (measured against PyMuPDF on the same files). Bold and italic are
  derived from the **font name**. The measurement is in the module docs.
- **`ruff`'s version matters.** `.venv` has 0.15.1; CI pins **0.15.20**. The older
  one flags `UP012` in a file this work never touches. Use the pinned one:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

## If you are starting fresh, do these in order

1. **Read `rust/README.md`, then the plan's §0 (fidelity) and §9 (divergences).**
   A Rust/Python difference that is *not* in §9 is a bug.
2. **Re-run every oracle** against the live Python (recipe above). If one is stale,
   that is a real find and more important than anything you were about to build.
3. **Run the three gates on a clean build** — `cargo test`, `cargo clippy
   --all-targets`, `cargo fmt --check` — plus `cargo test --features pdf` with
   `PDFIUM_BUNDLED_CACHE_DIR` pointed inside the workspace (a sandbox denies the
   platform default).
4. **Run the live suite once** with `BMLIB_LIVE_TESTS=1 --test-threads=1`. It found
   a service-level defect within a minute of existing; it is the cheapest
   high-yield check in the repository.
5. Then pick up from *What is left*. **Nothing there is urgent**, so prefer the
   verification items over the documentation ones, and treat #325 as the highest
   value work — it is a live defect in the Python library.

## The one thing not to do

**Do not modify the Python library as part of port work.** The port's brief was
functional equivalence to a *corrected* bmlib, and the corrections are an enumerated
list (#294–#309). A defect outside that list was **reproduced and filed**, never
fixed in place — that is why #316–#320 and #325 exist as issues rather than as
diffs. The one deliberate exception on the Rust side is the bioRxiv URL, made on
explicit instruction and recorded in §9.
