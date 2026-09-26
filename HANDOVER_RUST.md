# HANDOVER — the Rust port of bmlib

_Last updated: 2026-09-27 (round 43). **The port is functionally complete and merged.** `main`
is at `de513f3`. No pull request is open; every piece of work
described below is on `main`. The Python library was **not modified** by the port —
`git status --porcelain bmlib/` is empty, and that is the state to preserve. The
Rust crate is released — see *Publishing to crates.io* below._

**Read [`rust/README.md`](rust/README.md) first for how to build and run it, and
`docs/plans/2026-09-26-rust-port-roadblocks.md` §0 and §9 for the fidelity contract
and the divergence register.** This file is the one that says what is *left*, and
what will bite you.

## Where it stands

| | |
|---|---|
| Tests | **827 passing, 0 failing** (`cargo test` — 824 tests in 63 binaries + 3 doc-tests); **835** with `--features pdf`; **837** with `--features postgres`, whose 10 extra tests are the live suite and **skip** unless `BMLIB_PG_TESTS=1` |
| Lint | `cargo clippy --all-targets` **0 warnings** (default, `pdf` and `postgres`); `cargo fmt --check` clean; `ruff check .` clean |
| Size | 67,767 lines of Rust — 76 source files, 65 test files |
| Oracles | **38 corpora, 2,552 cases**, 40 `oracle/dump_*.py` drivers. **All 40 regenerate and match** as of round 43 — re-run them with `scripts/rerun_rust_oracle.py` |
| Python | untouched |

Build and test:

```bash
cd rust
CARGO_HOME="$PWD/.cargo-home" cargo test          # a sandbox that denies ~/.cargo
cargo test --features pdf                          # + 8 PDFium tests over real PDFs
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1   # 6 live requests
BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live    # 10 against a real server
```

**The network command needs `--test-threads=1`**: NCBI rate-limits by source address,
so a concurrent run draws 429s that read as parse failures. The live suite is
**gated** — the default `cargo test` opens no socket. Without the variable its six
tests each return immediately, so the binary reports `ok. 6 passed` in ~0.09s; the
count is the same either way, which is deliberate (a suite that silently
disappeared would be worse than one that runs). The PostgreSQL suite is gated the
same way and needs no `--test-threads=1`, because each test creates its own
database.

## Publishing to crates.io

**`bmlib` 0.1.0 is published** (2026-09-27) from `677d545`, the merge of PR #336.
The crate is `rust/bmlib` and the name was free.

Cargo reads the token from `$CARGO_HOME/credentials.toml`. Pointing `CARGO_HOME`
inside the workspace — which a sandbox that denies writes to `~/.cargo` requires,
and which `cargo publish` needs twice while it updates the index — therefore
hides the token that lives in `~/.cargo`. Link it rather than copy it, so the
secret keeps one home:

```bash
ln -sfn ~/.cargo/credentials.toml rust/.cargo-home/credentials.toml
```

`.cargo-home/` is gitignored, so the link never enters the repository. It is
**removed after a release** and has to be remade before the next one; leaving it
in place is a standing grant nobody asked for.

```bash
cd rust
CARGO_HOME="$PWD/.cargo-home" cargo publish --dry-run -p bmlib   # review, then:
CARGO_HOME="$PWD/.cargo-home" cargo publish -p bmlib
```

**A release cannot be pushed straight to `main`.** The `protect_main` ruleset
requires CodeQL results *for the exact commit*, and CodeQL here is GitHub's
default setup: there is no workflow file for it in `.github/workflows/`, and it
analyses pull requests, not direct pushes to a protected branch. A new commit
pushed at `main` is declined with *"push declined due to repository rule
violations"* while `git push --dry-run` reports a clean fast-forward, because the
rule is enforced on receive rather than on the probe. Land the release as a PR,
let CodeQL run, merge, and publish from the merge commit. That is the sequence
0.1.0 went through, and it is why the crate's `.cargo_vcs_info.json` names the
merge commit and carries no `dirty` flag.

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
  with a companion test asserting both cite #310. *Superseded in round 43*: Python
  adopted the fix, the assessment reader now requires both sections as Python's
  does, and the corrections were retired.
- **#309 part 2** — `FullTextCache::get_pdf` was `path.exists().then_some(path)`,
  so a directory at the PDF entry was a cache hit for ever. It now consults
  `is_readable` and the service quarantines the entry as its HTML branch already
  did. Two tests cover it (cache and service level).

Everything else re-derived clean: all 40 dumpers regenerate from the live Python
and match what is committed, `cargo clippy --all-targets` is clean,
`cargo fmt --check` is clean, and the gated live suite passes 6/6. The plan's
round-39 section is annotated, and a round-40 section records the two fixes.

## Session note (round 41) — the oracle was stale in three corpora

Step 2 of *"If you are starting fresh"* below — re-run every dumper against the
live Python — was executed, and it **found three stale corpora**. The claim just
above, that all 40 dumpers regenerate and match, was false when written. The
cause is a merge ordering the round-40 pass did not account for: `e9db0f9`,
committed two hours *after* the port commit `4ba04a1`, **fixed seven Appendix
defects in Python** — #299, #300, #301, #302, #303, #308 and #315 — so the
`corrected` blocks that pinned the port's deliberate disagreement were left
describing a Python that no longer existed.

- **`json`** — the four #299 corrections were retired; all 64 cases now diff
  strictly, with a companion test that still names the four interleaved-truncation
  cases so a corpus edit cannot drop them silently.
- **`protocol`** — the #315 system-message correction was retired, and §9 of the
  plan no longer lists it as a divergence: both implementations join now, and the
  source comment on `messages_to_anthropic` records the fix.
- **`sync`** — `window/huge-recheck` read the real clock on both sides, so its
  committed expectation (`739884`) expired at the next midnight and could never
  be regenerated. Both now read the case's pinned `now`; regenerated, the case
  reads `739051` and is reproducible on any day.

**No port defect was found.** The Rust code already implemented #299's closer
order and #315's join; it was the instrument describing the old library. The
plan's round-41 section carries the detail.

**The lesson is the one already in this file**, restated because it cost a round:
an oracle that is not re-run does not merely fail to catch drift — it *agrees*
with a library that has moved, and its `corrected`-block assertions pass only
while nobody regenerates the expectations.

## Session note (round 43) — Python adopted the quality fixes, and the port was still refusing them

Step 2 of *"If you are starting fresh"* — re-run every oracle against the live
Python — was executed over all 40 corpora. **Four were stale, and the port itself
was wrong on three readers.** `07335c1` and `d4a82a0` (2026-09-27) fixed #295,
#310, #312 and #317–#320 **in Python** — the seven defects the Rust quality port
was written against — so the 22 `corrected` blocks pinning the port's deliberate
divergence had become stale notes describing a library that no longer existed.

Re-deriving each block against the live Python split them three ways:

- **19 cases: Python now agreed** with the port's corrected value. The blocks
  were retired; the cases diff strictly with no port change.
- **4 cases: Python had moved to the correct answer and the port had not.** Two
  are [#332](https://github.com/hherb/bmlib/issues/332)'s named divergences, and
  `docs/DECISIONS.md` had already ruled on both: `clamped_confidence` now refuses
  a boolean and a non-finite number (the port read `true` → `1.0` and `"nan"` →
  `0.0`), and `CochraneStudyAssessment::from_json` **refuses an absent
  `risk_of_bias`** rather than defaulting nine "Unclear risk" domains — the
  register names the port's reading as "the fabrication the bullet above
  refuses". The other two are #332's "smaller difference" and one it did not
  name: `methods`/`support_for_judgement` read as a **string** or unstated
  (only a risk-of-bias `judgement` still stringifies, being a vocabulary
  lookup), and `group_sizes` goes through `as_int_map`.

**#332 is answered** — see the review follow-up below for the third part.

**The re-run is a script now**: `scripts/rerun_rust_oracle.py` (add `--write` to
regenerate). It diffs each dumper's output parsed against the committed
expectation and also checks that a corpus keeping two copies of its cases has not
let them drift apart. This is the second round it has found something, and the
first where the finding was a port defect rather than a stale expectation — which
is exactly the case for making it one command.

Verified after the fix: 40/40 corpora regenerate and match (2,552 cases),
`cargo test` **827**, `--features pdf` **835**, `--features postgres` **837**,
clippy and fmt clean, live network **6/6**, live PostgreSQL **10/10**, and
`git status --porcelain bmlib/` empty.

**The review of round 43 found the fix half-done, and closed it.** A
five-reviewer pass over the change, each finding re-checked against Python on
`main`, found `CochraneStudyAssessment::from_json` still defaulting an absent
`study_characteristics` (Python refuses it, and checks it first); #332's "smaller
difference" unaddressed, and wider than filed (`"45.5"` → 45, `"nan"` → 0,
`"inf"` → `i64::MAX` for a count, and `"nan"`/`"inf"` accepted by the Tier 2/3
float reader); the Cochrane counts and scores ignoring a numeric string; the new
`f64::clamp` storing `-0.0` where Python stores `0.0`; and the Cochrane oracle
harness passing any case Python raised on. All fixed: one private
`quality/json_fields.rs` now states Python's `as_int`/`as_float` and its signed-zero
clamp once, `clamped_confidence` logs a refused value at WARNING as Python does,
the risk-of-bias refusals use Python's wording, and the Cochrane harness compares
a refusal's message and cannot pass a Python exception on an op that cannot
refuse. 14 cochrane, 7 assessor and 11 LLM-reader cases were added, and 10
mutants (each fix reverted) were all killed. `scripts/rerun_rust_oracle.py` now
fails a run for a broken dumper, drifted case copies, an unlisted `dump_*.py` or a
`--only` that selects nothing, `--write` or not, and has a test file
(`tests/test_rerun_rust_oracle.py`). **With that, all three parts of #332 are
answered.**

## Session note (round 42) — the PostgreSQL backend, against a real server

The one thing `rust/README.md` still listed as *not yet done* in the code half
was the backend `Dialect::Postgres` had never had: the kind, the numbering, the
catalog SQL and the PostgreSQL DDL all existed and were exercised through a
simulated connection, but nothing could open a socket. It now can.

- **`db/postgres.rs`**, behind an optional `postgres` feature (matching Python's
  optional `psycopg2` extra, so a SQLite-only caller links neither a Postgres
  client nor its async runtime). `postgres` is the *blocking* wrapper, so the
  sync `Db` trait stays sync. It needs **two** `Db` impls, not three: the crate's
  `Transaction` covers a savepoint as well, and `Transaction::transaction()`
  opens the nested one.
- **Ten live tests** in `tests/postgres_live.rs`, each creating and dropping its
  own database, gated on `BMLIB_PG_TESTS=1` exactly as the network suite is
  gated on `BMLIB_LIVE_TESTS`. They cover the boolean mapping, `SERIAL` +
  `RETURNING id`, nested-savepoint rollback, migrations, the publications
  schema, and child reparenting.
- **Three defects the run found**, all invisible to every existing test and all
  written up in `rust/README.md` §"What the live PostgreSQL run found": a Rust
  line continuation that turned `information_schema.columns WHERE` into
  `columnsWHERE` (and three sibling SQL strings in `publications/storage.rs`),
  `postgres::Error`'s `Display` reducing every server rejection to the bare
  string `"db error"`, and the `PgSim` catalog shim that could not have caught
  the first. Fixing the second is what made the first legible.
- **The Python library is still untouched.**

The pattern is the one the DTD defect taught: a full suite of scripted fixtures
proves the code matches the fixtures, not the service. Both times the service
disagreed.

## The method, which is the part worth keeping

Every module was ported against a **differential oracle**, not written to match a
reading of the Python:

1. Write `rust/oracle/dump_<thing>.py` that runs the **live Python** over a JSON
   case corpus on stdin and writes expectations on stdout.
2. Commit the cases *and* the expectations under `rust/bmlib/tests/data/`.
3. The Rust test diffs **parsed** values, never text.

**All 40 corpora regenerate from the live Python and match what is committed** —
with the round-41 and round-43 corrections above: this was *not* true when first
written, and the re-run is what found it both times. That is what makes them
evidence rather than fixtures, and it is now one command:

```bash
cd /Users/hherb/src/bmlib
.venv/bin/python scripts/rerun_rust_oracle.py            # report drift
.venv/bin/python scripts/rerun_rust_oracle.py --write    # regenerate in place
```

It diffs each dumper's output against the committed expectation **parsed**, and it
also fails if a corpus keeping two copies of its cases (see below) has let them
drift apart. Before it existed the recipe was per-corpus and hand-typed:

```bash
.venv/bin/python rust/oracle/dump_cache.py < rust/bmlib/tests/data/cache_cases.json \
  > /tmp/fresh.json
diff <(python3 -m json.tool /tmp/fresh.json) \
     <(python3 -m json.tool rust/bmlib/tests/data/cache_expected.json)
```

Re-run it before believing anything below — and note that a
case edit must land in the copy the test reads: **most corpora keep two copies of
their cases**, one under `rust/oracle/` (the dumper's input in this recipe's
shape) and one under `rust/bmlib/tests/data/` (what `include_str!` pulls in), and
the two must stay identical. There are two exceptions: citations is
`oracle/cases.json` against `tests/data/citations_cases.json`, and `service` has
no `oracle/` copy at all. A **stale oracle is worse than no oracle**: it agrees
with a port that has drifted.

A case may carry a `corrected` block where the port deliberately differs, with its
reason. Those are the §9 divergences and they are the only differences to accept
silently — **unless Python has since adopted the fix**, in which case the block is
a stale note that must be retired, not kept. `json`'s four #299 corrections and
`protocol`'s #315 one were retired that way in round 41, and `cochrane`,
`cochrane_assessor`, `formatter` and `quality_llm` retired all 22 of theirs in
round 43 when Python's quality-narrowing batch adopted #295, #310, #312 and
#317–#320. **A retirement is not always the whole fix**: of round 43's 22, four
turned out to be *port* defects Python had moved past, so a stale block can be
hiding a wrong implementation rather than a stale expectation — ask which side
moved before regenerating.

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

- **Fixed.** The plan's §"Phase 4" no longer says the PDF backend is
  "outstanding" — `4a3593e` corrected it when this handover was added. The
  round-41 pass corrected the rest: the plan's §9 no longer lists #315 as a
  divergence, its Appendix records the seven defects Python has since adopted,
  and `rust/README.md`'s oracle section and *Not yet done* list were brought in
  line with the code — `RETURNING id` **is** implemented (in
  `insert_publication`), and `db/` is the only package without a corpus.

### 3. Verification that would raise confidence, not features

These are real and open, and each is a *measurement* rather than an implementation:

- **The network live suite is not a CI gate, deliberately.** An outage, an egress
  block or a rate-limit would redden it for a reason that is not this code. If you
  want it scheduled on top of the weekly run, a `workflow_dispatch`-style job is
  the shape — but decide whether a red run would mean anything before adding it.
- **The PostgreSQL suite *is* a CI gate now**, and the difference from the line
  above is the whole argument: a server it may create databases on is something CI
  can *provide* deterministically — the `rust-postgres` job in `ci.yml` runs over a
  `postgres:16` service and sets `BMLIB_PG_TESTS=1` — whereas NCBI's rate limiter is
  not. Run it the same way locally before a release that touches `db/` or
  `publications/`; it found three defects on its first run.
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
- **[#317](https://github.com/hherb/bmlib/issues/317)–[#320](https://github.com/hherb/bmlib/issues/320)
  — fixed in Python** (`07335c1`, `d4a82a0`, 2026-09-27). Four type-contract defects
  in `cochrane_models` and the quality readers, found by the delegated ports and
  verified independently before filing. #317 was the interesting one:
  `COCHRANE_RESPONSE_FORMAT` tells the model *"Use null for any field the text does
  not report"*, so a **compliant** model reached `data.get(k, default)` returning
  `None` for a `str`-annotated field. The fix covered #295, #310 and #312 as well,
  which is what made this port's four corpora stale and turned up the three port
  defects round 43 fixed — see the session note above.
- **[#316](https://github.com/hherb/bmlib/issues/316) — fixed.** It was a defect in
  the port, not the Python: `HttpResponse.body` was a `String`, so a binary PDF
  reached the cache with every non-UTF-8 byte replaced by U+FFFD, **undetectably**
  (`%PDF` is ASCII and survives; the cache's only check is that prefix).
- **[#332](https://github.com/hherb/bmlib/issues/332) — answered in round 43**, and
  it is the one issue that was *about* the port: two quality readers reproduced
  Python behaviour the narrowing batch changed, and it asked the port to decide
  whether to follow. It follows — `docs/DECISIONS.md` had already ruled on the
  interesting half. Close it with the next release; the three divergences it names
  are now agreements.

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
2. **Re-run every oracle** against the live Python:
   `.venv/bin/python scripts/rerun_rust_oracle.py`. If one is stale, that is a
   real find and more important than anything you were about to build — and ask
   **which side moved**: round 41's staleness was an expectation describing an
   old library, but round 43's was a *port defect* the expectation was hiding.
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
