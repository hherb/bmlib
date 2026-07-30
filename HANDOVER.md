# HANDOVER — bmlib development

_Last updated: 2026-07-30. `main` is at v0.5.1 plus a large `[Unreleased]`
section (PostgreSQL `publications`, PDF→text wiring, body-less JATS fix,
BaseAgent metrics/embeddings, consolidated JSON extraction, transparency
PubMed step, the `parse_json` contract, measured industry-funder matching).
1033 tests passing + 32 skipped._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Current state

- **Released: 0.5.1** (2026-07-21). `pyproject.toml` and `bmlib/__init__.py`
  agree. Release history: 0.4.0 (2026-07-19) → 0.5.0 (2026-07-20) → 0.5.1.
  0.3.0 was bumped in-tree but never released; its changes shipped inside
  0.4.0.
- **`[Unreleased]` is substantial and unreleased.** Three bodies of work sit
  on `main` waiting for a version:
  1. **`bmlib.publications` on PostgreSQL** (PR #28) — `schema.py`,
     `storage.py`, `sync.py` were SQLite-only; every statement is now
     dual-dialect. Brought `db.is_sqlite()` / `placeholder()` /
     `placeholders()` / `transaction_depth()` / `owns_commit()` into the
     public API, added `publications.pmcid`, fixed `fetch_scalar()` on
     psycopg2 `RealDictRow`, and made `transaction()` nest via savepoints on
     PostgreSQL. `tests/test_backends.py` runs each test against both
     backends; CI sets `BMLIB_REQUIRE_POSTGRESQL=1` so a missing DSN fails
     rather than skips.
  2. **PDF→text wired into `FullTextService`** — a retrieved PDF is extracted
     into `FullTextResult.html` via the new `render_html()`; opt out with
     `convert_pdfs=False`. Added `FullTextResult.content_kind`
     (`fulltext` / `abstract` / `extracted`) and `ConversionResult.page_texts`.
  3. **Body-less JATS handled** (PR #29) — medRxiv serves `<front>`+`<back>`
     with no prose for some preprints; such a document is detected via
     `JATSArticle.has_body`, never cached, and held back as a last resort.
     Also fixed: a missing abstract no longer kills a scoring batch, and the
     Tier 2 classifier's token budget is no longer overridden at the call site.
  4. **Unsectioned JATS `<body>` parsed** (issues #30, #31) — loose `<p>`
     prose becomes a titleless `JATSBodySection` instead of being dropped,
     and `docs/manual/fulltext.md`'s duplicated `PDF Conversion` section is
     merged into one. Review of that work turned up a second, older defect,
     fixed in the same PR: figure and table captions were lost whenever the
     figure sat inside a `<sec>` — the ordinary PMC layout — and their text
     was reprinted as article prose.
  5. **BaseAgent metrics/embeddings + consolidated JSON extraction** (PR #34,
     closes #17) — `PerformanceMetrics` (per-agent, thread-safe, independent
     of the global `TokenTracker`), `BaseAgent.embed()`/`embed_batch()`/
     `test_connection()`/`embedding_model`, `chat_json(retry_context=...)`,
     and a WARNING when `parse_json()`'s repair stage rescued a response.
     bmlib's two JSON extractors now share one locator, `iter_json_spans()`,
     plus a new `salvage_json_fields()` for field-level recovery. This is
     **Phase 1 item 1** of the bmlibrarian port — the keystone; the agent
     family is now unblocked.
  6. **Transparency: a real PubMed step + `unknown_reason`** (PR #35, closed
     #18 and #21) — `pubmed_api_key` was accepted and never read; the
     analyzer now spends one E-utilities `efetch` per analysis (skipped
     without a PMID, which it will take from the Europe PMC record it already
     fetched) for `<CoiStatement>`, `<DataBankList>` and `<GrantList>` —
     signals Europe PMC cannot supply for a closed-access paper.
     `TransparencyUnknownReason` makes the three `UNKNOWN` causes branchable
     without matching indicator prose. **No signature changed, but analyzer
     *behaviour* did**: one more request per analysis, and scores on
     closed-access papers can rise, so stored scores are not comparable
     across this change.
  7. **`parse_json`'s return contract, and the fragment it hid** (PR #39,
     closed #33) — `extract_json()` was reducing an unfenced array of objects
     to its first element, dropping every sibling with no error, on a path both
     the Anthropic and OpenAI-compatible providers run for every `json_mode`
     response. Its acceptance policy is now `_first_acceptable()`, run twice —
     whole spans first, the nested-object stage only as a last resort — with a
     *ranked* non-dict fallback so an incidental `[]` cannot mask the payload.
     `parse_json()`/`chat_json()` are annotated `dict | list` with an opt-in
     `require_dict` that retries the wrong shape (and fails fast at temperature
     0, like truncation); bmlib's two quality tiers pass it. Review of the PR
     turned up the same defect one layer down and it is fixed in the same
     change: a **truncated** array of objects never balances, so `parse_json()`
     was taking the first object out of it and skipping the repair stage that
     recovers the whole array — the fragment now waits until repair has failed
     (`extract_json(allow_fragments=False)`). `dict | list` is also *enforced*
     rather than only annotated: a bare scalar response raises.
     **Behaviour changes, on two response shapes**: an unfenced array of
     objects in prose now arrives whole (`extract_json()`, so both providers
     too), and a truncated array of objects arrives whole through
     `parse_json()`. A fenced array already did, and a bare array never reached
     `extract_json()`.
  8. **Measured industry-funder matching** (PR #40, closes #36) — the
     substring keyword list is split into stems and whole words behind one
     `_is_industry_funder()` predicate, calibrated against 833 real
     CrossRef/PubMed names (417 hand-labelled, committed as
     `tests/data/funder_names.json`, sampled by the new live runner
     `scripts/sample_funder_names.py`). **Precision 0.400 → 0.917, recall
     0.176 → 0.324.** The data removed two obvious-looking keywords —
     `"pharma"` reached "Pharmacy"/"Pharmacogenetics", `"biotech"` reached a
     ministry and a research council — so `industry_funding_detected` moves in
     **both** directions and stored values are not comparable.
  **Deciding whether this is 0.6.0 is open work** — see "Next up" below. The
  `publications` and `fulltext` changes are additive, so a minor bump fits.
  Note `~/src/bmlibrarian` pins `bmlib[ollama]>=0.5.1,<0.6.0`, so a 0.6.0
  release needs that pin lifted downstream.
- **1033 tests passing + 32 skipped** (`uv run pytest tests/ -q`). 30 skips are
  the PostgreSQL parameterisations of `tests/test_backends.py`, which run only
  when `BMLIB_TEST_POSTGRESQL_DSN` is set; the other 2 are `test_pdf_converter`
  tests needing PyMuPDF, which the dev venv does not install.
- **Documentation was rewritten for 0.4.0 and updated through PR #28/#29/#32.**
  Treat drift as a regression worth fixing, not expected staleness. Issue #31
  (a duplicated `PDF Conversion` section in `docs/manual/fulltext.md`) is
  closed.

## Next up

Pick from the open issues below, or resume the bmlibrarian porting effort.
Nothing is blocked on anything else.

### Open GitHub issues

- **#37 — `analyze()` threads eight accumulators through 4-to-6-element
  tuples.** Nothing is broken; a mis-ordered unpacking would be a silent
  type-compatible swap, and adding one boolean meant widening two signatures.
  A mutable `_Analysis` dataclass mutated in place would remove the arity
  entirely. Worth doing before the next signal source lands in `analyze()`.

#37 is the only issue still open. #18 and #21 closed with PR #35, #33 with
PR #39, and #36 closes with PR #40 — the industry-funder matching in this
branch. Both closed designs stay in `docs/superpowers/specs/` as the record of
what was rejected and why: for #33, raising unconditionally on a non-dict; for
#36, word-boundary matching applied uniformly across the keyword list.

### Worth doing, not yet an issue

- **Data-deposition accessions in PubMed's `<DataBankList>`** (GENBANK, PDB,
  SRA, Dryad, figshare) are structured proof of data sharing, strictly
  stronger than the current substring scan of the full text. Deliberately left
  out of the PubMed step to keep the data-availability scoring path out of
  that change; `_parse_pubmed_signals()` already walks the databanks, so this
  is a small follow-up rather than a rewrite.
- **Repo housekeeping is done (2026-07-30).** The three stale worktrees under
  `.claude/worktrees/` — which shadowed every repo-wide `grep` — are removed,
  and 27 merged local branches are deleted. Only `main` and any in-flight
  branch remain. `git branch -r` still lists several merged branches on
  `origin`; those are untouched, since deleting shared refs is a separate
  decision.

### bmlibrarian → bmlib porting (paused, Phase 1 next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

**Phase 0 is done** and shipped in 0.4.0: `llm/json_repair.py`,
`llm/text_utils.py`, `quality/cochrane_models.py` + `cochrane_formatter.py`,
`quality/extractors.py` + `scoring_models.py`, `fulltext/pdf_converter.py`.

**Phase 1 item 1 — BaseAgent enhancement — is done** (PR #34). The extras
merged into the existing `bmlib/agents/base.py`; the queue/orchestrator hooks
and `bmlibrarian.config` reads were dropped as planned; upstream's
`_generate_and_parse_json` needed no port because bmlib's `chat_json()`
already regenerates on parse failure. Issue #17 closed in the same PR.
Design and plan: `docs/superpowers/specs/2026-07-28-*` and
`docs/superpowers/plans/2026-07-28-*`.

**Phase 1 item 2 — the next port:**

1. **`context_processor`.** Source:
   `~/src/bmlibrarian/src/bmlibrarian/agents/context_processor/` (~840 lines,
   already callback-injected — clean). Target: `bmlib/context_processor/` or
   under `bmlib/agents/`. It batches oversized items across an LLM context
   window and consolidates results. `create_prisma_chunk_processor` is
   PRISMA-specific — leave that factory in the app, port the generic core. It
   can build on `bmlib/llm/text_utils.py`.

Later phases (2–4: citations, discovery, pubmed_search, MeSH, the
prompt-driven agent family, paper_weight) are in the analysis doc.

### The port recipe (how Phase 0 was done — repeat it)

1. **TDD, always.** Write behaviour tests first (the upstream code is the
   spec), watch them fail (a `ModuleNotFoundError` is the correct red for a
   new module), then port and watch them pass. Bug in a test you wrote? Fix
   the test, not correct code.
2. **Modernise to bmlib style:** AGPL header on every file,
   `from __future__ import annotations`, lowercase builtin generics
   (`list`/`dict`/`X | None`), `datetime.UTC`.
3. **Sever app coupling:** replace `get_db_manager()`/`bmlibrarian.config`
   with injected connections/params; guard optional deps with
   `try/except ImportError` raising `pip install bmlib[extra]`; route LLM
   calls through `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** the public names from the package `__init__.py` `__all__`.
5. **Verify:** tests + both ruff commands clean before done.
6. **Record** each port in `CHANGELOG.md` under `[Unreleased]`.
7. **Reconcile, don't fork:** where a port overlaps existing bmlib (quality
   study-classification, transparency), build on the existing module.

## Deliberate non-fixes — do not "fix" these

Each was investigated and closed as correct. Reopening them wastes a session.

- **`_INDUSTRY_STEMS` and `_INDUSTRY_WORDS` must not be merged back into one
  list**, and neither may be extended without re-running the measurement. The
  stems have to reach inside longer words; the whole words must not, or `"inc"`
  matches `"Lincoln"`. Every member was decided against
  `tests/data/funder_names.json`, and two intuitive members were *removed* by
  that data: `"pharma"` as a substring scored 3 true positives to 5 false ones
  (it reaches `"Pharmacy"`, `"Pharmacogenetics"`), and `"biotech"` scored 0 to 4
  (`"Department of Biotechnology"`, `"…Research Council"` — it names a field, not
  a company type). `"co"`, `"corporation"`, `"ab"` and `"labs"` were rejected for
  reasons recorded in the source comment. Regenerate the corpus with
  `scripts/sample_funder_names.py` before changing any of it; the metric test is
  `tests/test_funder_matching.py::TestAgainstTheLabelledCorpus`.
- **`_is_industry_funder()` is deliberately not applied to COI prose.**
  `_INDUSTRY_COI_KEYWORDS` stays separate: the org suffixes match far too freely
  in running text, and those phrases never occur in a funder name.
- **`TransparencySettings.filtering_enabled`, `max_concurrent_analyses`,
  `cache_results` are not dead code.** They are orchestration hints for the
  *calling* application. The library analyses one document per call and does
  no filtering, threading, or caching of its own. The docstring says which
  fields the analyzer honours and which the caller owns.
- **`outcome_switching_detected` stays reserved and always `False`.** Deciding
  it means comparing a trial's pre-registered primary outcomes against those
  reported — a real feature with real false-positive risk, not a fix. It stays
  in the schema so persisted results need no migration when detection lands.
- **`Publication.pmcid` is declared last on the dataclass, not beside `pmid`.**
  `Publication` is constructed positionally by downstream projects, so any
  other placement shifts every following argument and lands a caller's
  `abstract` in `pmcid` with no error anywhere. Pinned by
  `test_positional_construction_is_stable_across_versions`.
- **PostgreSQL transaction nesting is detected from bmlib's own open-block
  count, not psycopg2's status.** psycopg2 opens a transaction on the first
  statement of *any* kind, so a bare `SELECT` would make every following block
  look nested and stop committing. The count is keyed by *(thread,
  `id(conn)`)* — see CLAUDE.md for why both parts are load-bearing.
- **The Ollama raw `/api/tags` path re-implements httpx's safety defaults on
  purpose** (HTTP(S)-only scheme, bearer token stripped across cross-origin
  redirects, `"<word>:<digits>"` read as host:port). Simplifying any of these
  back to the obvious one-liner reintroduces a real defect; each has a
  regression test naming it.
- **`extract_json()` lets a fenced candidate win on parse alone, ahead of its
  dict preference.** A fence is the model's own delimitation of its answer, so
  it must not lose to an object found elsewhere in the response — a fenced
  `[1, 2]` alongside a later top-level `{"a": 1}` returns the fenced array.
  Since the whole-span policy landed, the fence rule is no longer the *only*
  thing keeping a fenced array of objects intact (the ranked fallback would
  also return it), but it is still what makes a fence outrank a competing
  top-level dict. Pinned by `test_fenced_array_of_objects_is_returned_whole`.
- **`extract_json()` runs its acceptance policy twice, and its non-dict
  fallback is ranked.** Collapsing the two walks back into one restores the
  silent truncation #33 fixed: dict preference is satisfied by the object
  `iter_json_spans()` digs out of an array, so `'[{"a": 1}, {"b": 2}]'` in
  prose returns `{"a": 1}` and the sibling vanishes. Reducing the ranked
  fallback to first-parseable is the *other* way to restore it, and a worse
  one: the first walk would accept an incidental `[]`, the second walk would
  never run, and the caller would get unrelated data that parses cleanly and
  passes every downstream check. `extract_and_repair_json()` deliberately has
  no equivalent second walk (next entry). Pinned by
  `TestExtractJsonPrefersWholeSpans`.
- **`extract_and_repair_json()` passes `nested_objects=False`.** It *repairs*
  candidates, and repairing an object nested inside a span it already rejected
  discards the structure around it — `'[{"a": 1}, invalid junk]'` would return
  `{"a": 1}` where it should raise. `extract_json()` keeps the nested stage,
  but as a **last resort only**: the asymmetry between the two is that
  validating a fragment reports what is there, while repairing one fabricates a
  structure the model never emitted — not that validation has a general licence
  to prefer fragments. Pinned by
  `test_raises_rather_than_returning_a_fragment_of_a_broken_array`.
- **`BaseAgent.parse_json()` asks `extract_json()` for whole spans first and
  re-asks with fragments only after repair has failed.** Collapsing the two
  calls back into one `extract_json(text)` at stage 2 looks like an obvious
  simplification and reintroduces a silent data loss: a *truncated* array of
  objects never balances, so the only span extraction can offer is the first
  object, and taking it drops the sibling and skips the repair stage's
  possibly-truncated WARNING. Repair closes the bracket and recovers the whole
  array, which is why it must go first. The fragment is still reachable —
  `'[{"a": 1}, invalid junk]'` neither parses nor repairs — just last. Pinned
  by `test_a_truncated_array_of_objects_is_repaired_whole` and
  `test_a_fragment_is_still_the_last_resort`.
- **`parse_json()` enforces `dict | list` rather than only annotating it.** A
  bare scalar — `42`, `"done"`, `true`, `null` — raises. Letting it through
  would make the annotation a lie again (which is what #33 was about) and only
  defer the failure to the caller's first subscript; inside `chat_json()` the
  raise becomes an ordinary retry, which is the right response to a model that
  answered a `json_mode` request with a number. Pinned by
  `test_a_bare_scalar_is_not_a_structured_answer`.
- **`require_dict` has a third `bool` overload on both methods.** It looks
  redundant beside the two `Literal` ones and is not: mypy does not expand
  `bool` into `Literal[True] | Literal[False]` to match them, so a caller
  writing `require_dict=self.strict` gets "no overload variant matches" with no
  way to satisfy it. Verified with mypy 1.14 — CI runs ruff only, so nothing in
  the build will catch its removal.
- **`salvage_json_fields()` bounds *both* of its passes.** Every failed
  `raw_decode()` scans forward to the end of the document, so an unbounded
  pass is quadratic in the response length, and a repetition-looping model —
  the failure mode salvage exists for — is what produces thousands of
  matches. Repair runs at most once per key, at the last match, because
  repair exists to close a value truncated at the end of the document and
  earlier matches cannot need it (3000 matches: 135 s → 0.19 s). The fast
  pass stops after `MAX_SALVAGE_MATCHES`, which is what makes the whole
  function linear (50,000 matches: ~1.0 s → ~0.08 s). Pinned by
  `test_repair_is_attempted_at_most_once_per_key` and
  `test_fast_pass_is_bounded_to_the_match_cap`; the last match staying
  reachable past the cap is pinned by
  `test_the_last_match_is_still_reached_beyond_the_cap`.
- **`RecursionError` is caught wherever a JSON candidate is decoded.**
  `json.loads()` / `raw_decode()` descend recursively, so text nested past
  the interpreter's stack limit blows the stack instead of raising
  `ValueError` — and `'{"j": ' * 20000` is a shape repetition-looping models
  actually emit. `extract_json()` is the one that matters: it is documented
  never to raise and runs unconditionally on every `json_mode` response in
  the Anthropic and OpenAI-compatible providers, so an escape takes out the
  provider call. Narrowing any of these back to `except json.JSONDecodeError`
  reintroduces it. Pinned by
  `test_deep_nesting_returns_the_text_rather_than_raising`,
  `test_deep_nesting_raises_valueerror_not_recursionerror` (×2) and
  `test_deeply_nested_text_raises_valueerror_not_recursionerror`.
- **`iter_json_spans()` dedupes candidates by text, not position.** Stages 4
  and 5 rescan fence interiors as plain text, so without it every fenced body
  is offered twice and an unrepairable one pays `repair_json()`'s attempt
  loop twice. `balanced_found` is set *before* the dedup check — a span that
  repeats one already yielded still means the text balanced, so the stage-6
  truncation tail must not fire.
- **`PerformanceMetrics.elapsed_time_seconds` reads `time.monotonic()`, not
  the `time.time()` timestamps it stores.** `start_time` / `end_time` stay
  absolute so a caller can render them as dates, but a wall-clock difference
  can be distorted or made negative by an NTP step or DST change mid-run, and
  `format_report()` prints elapsed directly against `total_wall_time_seconds`,
  which `BaseAgent` accumulates monotonically. `snapshot()` must copy the
  monotonic marks by hand — they are `init=False`, so a keyword-only copy
  silently drops them to the wall-clock fallback. Pinned by
  `test_elapsed_survives_a_wall_clock_step` and
  `test_snapshot_carries_the_monotonic_marks`.
- **`PerformanceMetrics` omits model-inference and prompt-eval timers.** No
  provider reports them through bmlib — `LLMResponse.duration_seconds` is
  declared but never populated — so they would be permanently `0.0` and every
  derived figure would lie. `tokens_per_second` uses wall time instead.
- **`BaseAgent.__init__`'s `embedding_model` is declared last**, for the same
  reason as `Publication.pmcid`: downstream projects construct subclasses
  positionally.
- **A PubMed record with no `<CoiStatement>` leaves `coi_disclosed` alone.**
  Absence there means the publisher supplied no statement to PubMed, not that
  the paper carries none; demoting `None` to `False` would trigger the
  missing-COI HIGH-risk rule on no evidence. Only a *present* statement moves
  the field, and only upwards. Pinned by
  `test_an_absent_pubmed_statement_leaves_the_status_unknown`.
- **`<DataBankList>` accessions are validated as `NCT\d{8}` before use, and a
  ClinicalTrials.gov entry that fails validation still counts as registered.**
  The accession is publisher-supplied text that would otherwise be
  interpolated into a ClinicalTrials.gov URL path unchecked. Registration is a
  separate fact from followability — that is what `registration_not_checkable`
  means, and why `trial_results_compliant is False` covers both "checked and
  absent" and "not checkable" (the indicator distinguishes them). The
  indicator names the consequence, not a registry, because the same flag
  covers a ClinicalTrials.gov entry with an unusable accession. Pinned by
  `test_a_malformed_accession_never_reaches_a_url` and
  `test_an_unusable_clinicaltrials_accession_is_not_called_another_registry`.
- **`TransparencyResult.unknown_reason` is declared last**, for the same reason
  as `Publication.pmcid` and `BaseAgent.embedding_model`.
- **The transparency indicator strings are module constants, not literals.**
  The PubMed step must retract the two full-text COI lines when it establishes
  a disclosure they contradict, and appending and filtering through the same
  name is what keeps that honest. Pinned by
  `test_a_pubmed_statement_retracts_the_full_text_absence_indicator`.
- **`_JATSHandler.endElement` tests `in_figure or in_table_wrap` before any
  prose branch, for both `<p>` and `<title>`, and routes on `in_caption`
  rather than on which `in_*` flag is set.** JATS reuses `<p>` for caption
  body and `<title>` for the caption lead, and a `<fig>` or `<table-wrap>`
  normally sits *inside* a `<sec>`, so asking about the section first blanks
  the caption, reprints it as article prose, and — for `<title>` — renames the
  enclosing section after the figure. The same branch deliberately **drops**
  non-caption `<p>`: table cell text is collected by `characters()`, so
  letting it through duplicates cells and footnotes into the prose and counts
  them towards `has_body`. Pinned by `TestJATSParserCaptionScoping` and
  `TestJATSParserUnsectionedBodyFurniture`.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API connection
  as first argument, type hints and docstrings on everything public, AGPL-3
  header on every source file, dataclass models with `to_dict()`/`from_dict()`,
  explicit SQL (no ORM), optional dependencies guarded with a helpful
  `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`.** CI pins
  **ruff 0.15.20** (`.github/workflows/ci.yml`); the `.venv` currently holds
  0.6.5, which false-flags `UP038` on `ollama.py` — a rule newer ruff removed.
  Use:

  ```bash
  uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
  ```

- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP;
  no external services. To run the PostgreSQL half of `test_backends.py`, set
  `BMLIB_TEST_POSTGRESQL_DSN` to a database the tests may drop every table in.
- New functionality needs unit tests; see CLAUDE.md's test-file mapping table.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
