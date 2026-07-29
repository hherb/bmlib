# HANDOVER — bmlib development

_Last updated: 2026-07-29. `main` is at v0.5.1 plus a large `[Unreleased]`
section (PostgreSQL `publications`, PDF→text wiring, body-less JATS fix,
BaseAgent metrics/embeddings, consolidated JSON extraction, transparency
PubMed step). 953 tests passing + 32 skipped._

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
  6. **Transparency: a real PubMed step + `unknown_reason`** (PR pending,
     closes #18 and #21) — `pubmed_api_key` was accepted and never read; the
     analyzer now spends one E-utilities `efetch` per analysis (skipped
     without a PMID, which it will take from the Europe PMC record it already
     fetched) for `<CoiStatement>`, `<DataBankList>` and `<GrantList>` —
     signals Europe PMC cannot supply for a closed-access paper.
     `TransparencyUnknownReason` makes the three `UNKNOWN` causes branchable
     without matching indicator prose. **No signature changed, but analyzer
     *behaviour* did**: one more request per analysis, and scores on
     closed-access papers can rise, so stored scores are not comparable
     across this change.
  **Deciding whether this is 0.6.0 is open work** — see "Next up" below. The
  `publications` and `fulltext` changes are additive, so a minor bump fits.
  Note `~/src/bmlibrarian` pins `bmlib[ollama]>=0.5.1,<0.6.0`, so a 0.6.0
  release needs that pin lifted downstream.
- **953 tests passing + 32 skipped** (`uv run pytest tests/ -q`). 30 skips are
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

- **#33 — `BaseAgent.parse_json` is annotated `-> dict` but can return a list**
  when a response contains only a top-level array. Pre-existing; dict-preference
  narrowed it but did not close it. Decide between a runtime guard (breaking)
  and widening the annotation. Same decision covers `extract_json` silently
  returning the first object of `[{...}, {...}]`.

#18 and #21 close with the pending transparency PR.

### Worth doing, not yet an issue

- **Data-deposition accessions in PubMed's `<DataBankList>`** (GENBANK, PDB,
  SRA, Dryad, figshare) are structured proof of data sharing, strictly
  stronger than the current substring scan of the full text. Deliberately left
  out of the PubMed step to keep the data-availability scoring path out of
  that change; `_parse_pubmed_signals()` already walks the databanks, so this
  is a small follow-up rather than a rewrite.
- **`.claude/worktrees/` holds three stale worktrees** (`next-session-5b78ba`,
  `next-session-180be7`, `review-19-bde68f`) from earlier sessions. They shadow
  every repo-wide `grep`. Worth pruning with `git worktree remove` /
  `git worktree prune` if they are genuinely dead.

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
  reducing a fenced `[{...}, {...}]` to the first object — which dict
  preference alone does, via the nested-object stage — silently drops every
  sibling on a path both providers run for every `json_mode` response. Pinned
  by `test_fenced_array_of_objects_is_returned_whole`.
- **`extract_and_repair_json()` passes `nested_objects=False`.** It *repairs*
  candidates, and repairing an object nested inside a span it already rejected
  discards the structure around it — `'[{"a": 1}, invalid junk]'` would return
  `{"a": 1}` where it should raise. `extract_json()` keeps the nested stage,
  because it only ever validates. Pinned by
  `test_raises_rather_than_returning_a_fragment_of_a_broken_array`.
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
  separate fact from followability — that is what `other_registry` means, and
  why `trial_results_compliant is False` covers both "checked and absent" and
  "not checkable" (the indicator distinguishes them). Pinned by
  `test_a_malformed_accession_never_reaches_a_url`.
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
