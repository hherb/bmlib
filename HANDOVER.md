# HANDOVER — bmlib development

_Last updated: 2026-08-05. **0.7.0 is released and on PyPI.** `[Unreleased]`
carries the Cochrane assessment agent (Phase 2 row 9 of the bmlibrarian port,
on `feature/cochrane-assessor`, not yet merged or released). No open issues,
no open PRs yet — this branch's PR is the next one. 1446 tests passing + 50
skipped, ruff clean. **The next piece of work is another Phase 2 port** — see
"Next up"._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Current state

- **Version 0.7.0**, released 2026-08-04 and live on PyPI — the **first
  release published by the Release workflow** rather than by hand, so that
  path is now proven end to end (tag → GitHub release → `pypi` environment
  gate → Trusted Publishing upload). Release
  history: 0.4.0 (2026-07-19) → 0.5.0 (2026-07-20) → 0.5.1 (2026-07-21) →
  0.6.0 (2026-07-30) → 0.7.0. 0.3.0 was bumped in-tree but never released;
  its changes shipped inside 0.4.0. The version lives in **four** places —
  `pyproject.toml`, `bmlib/__init__.py`, the README version line,
  `CLAUDE.md`'s header — and all four agree.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.7.0 carries six changes, all merged to `main` before the release
  branch was cut: the `_Analysis` carrier (#37, PR #42), `<DataBankList>`
  deposition (PR #43) plus `scripts/sample_databank_names.py` (PR #46), the
  PMC ID resolution fallback and NCBI full-text tier (#47, PR #48),
  `bmlib.context_processor` (#49, PR #50), and Retraction Watch notices
  (Phase 2 row 10, PR #51). Designs for all of them are in
  `docs/superpowers/specs/`.
- **Four behaviour changes in 0.7.0 move stored values**, none behind a
  flag: `transparency_score` rises and `data_availability_level` strengthens
  for papers whose PubMed record names a deposition repository;
  `trial_registered` becomes `True` and `transparency_score` rises by 20 for
  papers in the three registries `_TRIAL_REGISTRY_NAMES` did not recognise
  (`JMACCT`, `REPEC`, `UMIN CTR`); a funder CrossRef repeats
  collapses to one `risk_indicators` line; and `FullTextResult.source` gains
  `"ncbi_pmc"`. 0.6.0's three non-comparable changes are separate and still
  apply to anyone upgrading across both. Anything persisting these values
  needs to know.
- **`~/src/bmlibrarian` still pins `bmlib[ollama]>=0.5.1,<0.6.0`**, so it has
  now missed two releases. Widening it is a downstream change, not a bmlib
  one.
- **`[Unreleased]` carries the Cochrane assessment agent** — Phase 2 row 9 of
  the bmlibrarian port, on `feature/cochrane-assessor`. `CochraneAssessor`
  (Tier 4) turns a title and text into a `CochraneStudyAssessment`;
  `collapse_risk_of_bias()` bridges its nine domains onto the five-domain
  `BiasRisk`; `QualityFilter(use_cochrane_assessment=True)` plus a
  `full_text=` keyword on `QualityManager.assess()` wire it into
  `QualityManager`, enriching the free Tier 1 result rather than replacing
  it. Closes the Cochrane half of the "wire the new quality tools into the
  pipeline" roadmap item — the rule-based extractors half is still open. Both
  names are exported from `bmlib.quality`. See `CHANGELOG.md` for the full
  entry and the six upstream defects fixed in the port.
- **1446 tests passing + 50 skipped** (`uv run pytest tests/ -q`). 47 skips
  are the PostgreSQL parameterisations of `tests/test_backends.py`, which run
  only when `BMLIB_TEST_POSTGRESQL_DSN` is set; 2 are `test_pdf_converter`
  tests needing PyMuPDF, which the dev venv does not install; 1 is a
  PostgreSQL-only schema test that does not apply to SQLite. With a DSN set
  it is **1493 passing + 3 skipped**.
- **Documentation was rewritten for 0.4.0 and kept current through 0.7.0.**
  Treat drift as a regression worth fixing, not expected staleness. The
  `(unreleased)` markers in `docs/manual/` and `ROADMAP.md` were promoted to
  `0.7.0` at release; if you add one, it is the next release's job to promote
  it. Markers inside `docs/superpowers/plans/` are historical records of what
  a plan said at the time — leave them alone.

## Next up

### Open GitHub issues

**None.** #49 closed with PR #50, #47 with PR #48, #18 and #21 with PR #35,
#33 with PR #39, #36 with PR #40, and #37 with the `_Analysis` carrier. Every closed design stays in
`docs/superpowers/specs/` as the record of what was rejected and why: for #33,
raising unconditionally on a non-dict; for #36, word-boundary matching applied
uniformly across the keyword list; for #37, a `NamedTuple` carrier (immutable,
so every step would still rebuild and return it — the arity survives).

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** so the mother project can consume 0.6.0
  and 0.7.0. Read both releases' non-comparable behaviour changes first — the
  transparency ones move stored scores, so a project holding historical
  assessments wants to know before it upgrades.
- **Repo housekeeping is done.** Stale worktrees under `.claude/worktrees/`
  (which shadowed every repo-wide `grep`) and 31 merged local branches are
  deleted; only `main` and any in-flight branch remain. `git branch -r` still
  lists several merged branches on `origin` — untouched, since deleting shared
  refs is a separate decision.

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

**Phase 1 item 2 — `context_processor` — is done** (#49, this branch), which
completes Phase 1. It landed as the top-level `bmlib/context_processor/`, not
under `agents/`, because the harness has no LLM dependency at all; only
`LLMChunkProcessor` imports `BaseAgent`. `create_prisma_chunk_processor` was
left in the app as planned, and upstream's `SemanticChunkProcessor` was
rewritten rather than copied — it called the raw Ollama client, which is the
coupling the port existed to sever.

**Phase 2.** Independent and parallelisable, so pick any remaining row.
The numbers below are **rows in the analysis doc's master priority table, not
GitHub issues** — GitHub #8 and #9 exist and are about something else
entirely: #4 citations, #8 PDF section segmenter, #9 Cochrane assessor,
#11 PubMed-metadata graft. **Row 10, Retraction Watch, is done** (PR #51,
shipping in 0.7.0) — and shipped deliberately as notice storage plus a pure
rule, not a fetcher; see the design doc's "Why this is not a fetcher" for why
that word doesn't belong here. **Row 9, the Cochrane assessor, is also done**
(`feature/cochrane-assessor`, unreleased) — `bmlib.quality.CochraneAssessor`
(Tier 4) plus `collapse_risk_of_bias()`, wired into `QualityManager` behind
`QualityFilter(use_cochrane_assessment=True)`. This is the row that also
answered the Cochrane half of the standing "wire the new quality tools into
the pipeline" roadmap item — `quality/cochrane_models.py` is no longer
standalone, though the rule-based extractors half of that item is still
open. Design: `docs/superpowers/specs/2026-08-05-cochrane-assessor-design.md`.
Phases 3–4 (discovery, pubmed_search, MeSH, the prompt-driven agent family,
paper_weight) are in the analysis doc.

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
  `tests/data/funder_names.json`, which *removed* two intuitive ones —
  `"pharma"` as a substring (3 true positives to 5 false: "Pharmacy",
  "Pharmacogenetics") and `"biotech"` (0 to 4: it names a field, not a company
  type) — and rejected `"co"`, `"corporation"`, `"ab"`, `"labs"` for reasons in
  the source comment. Regenerate with `scripts/sample_funder_names.py` before
  changing any of it; the metric test is
  `tests/test_funder_matching.py::TestAgainstTheLabelledCorpus`.
- **`_is_industry_funder()` is deliberately not applied to COI prose.**
  `_INDUSTRY_COI_KEYWORDS` stays separate: the org suffixes match far too freely
  in running text, and those phrases never occur in a funder name.
- **`_merge_pubmed_signals()` mutates the `_Analysis` it is given and returns
  nothing.** It used to copy `indicators` on the way in and return the copy,
  because its COI branch rebinds the list while its funder branch appends — so
  a caller that ignored the return value got a half-applied merge. With no
  return value that is unrepresentable, and restoring the copy would silently
  discard the whole merge. Pinned by
  `test_the_merge_applies_both_of_its_branches_to_one_list`.
- **Every `analyze()` sub-step takes `_Analysis` and returns `None`, including
  the two that carry a single value.** `_check_openalex` only ever accumulated
  `score`, and `_check_europepmc`'s industry-COI finding could plausibly be
  handed back for the caller to fold. Both are on the carrier anyway: one step
  threading a value while four mutate is the inconsistency that makes the next
  contributor guess, and the industry-COI confidence
  (`TEXT_INDUSTRY_CONFIDENCE` vs. a funder record's 0.8) belongs to the step
  that found it, not to `analyze()`.
- **The data-deposition rank-merge machinery (`_DATA_LEVEL_RANK`,
  `_Analysis.note_data_level()`, `_DEPOSITION_DATABANK_LEVELS`) is argued in
  full inline in `transparency/analyzer.py`'s own comments — not re-narrated
  here.** Two rules worth knowing before opening the source: (1) every level
  a producer can nominate must be a key of `_DATA_LEVEL_RANK`, or
  `note_data_level()` raises by design rather than scoring a wrong level at
  zero (`test_every_pattern_maps_to_a_level_the_ranking_knows`,
  `test_every_repository_maps_to_a_level_the_ranking_knows`); (2)
  `_DEPOSITION_DATABANK_LEVELS` deliberately excludes reference-only
  databases (dbSNP, OMIM, RefSeq, …) since citing one does not show *these*
  authors deposited *their own* data — argued member-by-member in the source
  comment, and pinned by `test_the_deposition_and_registry_name_sets_are_disjoint`.
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
- **The JSON extractors prefer a *whole span* to a nested fragment in three
  places, argued inline in `llm/utils.py`, `llm/json_repair.py` and
  `agents/base.py` — not re-narrated here.** All three guard the silent
  truncation #33 fixed (an array of objects reduced to its first element, run
  on every `json_mode` response). Each has a named test that fails if the
  guard is removed: `TestExtractJsonPrefersWholeSpans`,
  `test_raises_rather_than_returning_a_fragment_of_a_broken_array`,
  `test_a_truncated_array_of_objects_is_repaired_whole` +
  `test_a_fragment_is_still_the_last_resort`.
- **A fenced candidate wins on parse alone, ahead of the dict preference.** A
  fence is the model's own delimitation of its answer, so a fenced `[1, 2]`
  beats a later top-level `{"a": 1}`. Pinned by
  `test_fenced_array_of_objects_is_returned_whole`.
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
- **`salvage_json_fields()` bounds *both* of its passes, and `RecursionError`
  is caught wherever a JSON candidate is decoded** — both argued inline in
  `llm/json_repair.py` and `llm/utils.py`, with the profiling numbers that
  justify the bound in the source comment, not here. Pinned by
  `test_repair_is_attempted_at_most_once_per_key`,
  `test_fast_pass_is_bounded_to_the_match_cap`,
  `test_the_last_match_is_still_reached_beyond_the_cap`,
  `test_deep_nesting_returns_the_text_rather_than_raising`, and
  `test_deeply_nested_text_raises_valueerror_not_recursionerror`.
- **`iter_json_spans()` dedupes candidates by text, not position** — stages 4
  and 5 rescan fence interiors as plain text, so without it every fenced body
  is offered twice. Argued inline in `llm/utils.py`.
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
- **NCBI's ID Converter is consulted *after* the Europe PMC search, not
  before.** The search returns the PMC ID **and** the free-PDF URL that feeds
  Tier 1d in a single request. Querying the converter first would either cost
  a second HTTP request on every lookup or forfeit that URL — and a deleted
  prior branch did exactly that, which is why issue #47 recorded the ordering
  as the part to invert. Pinned by
  `test_the_converter_is_not_consulted_when_the_search_found_an_id`.
- **…but it sits *outside* the search's `except`, in its own statement.** The
  Tier 1b block is three pieces — search, converter, fetch — with the search
  and the fetch each carrying their own handler and the converter between
  them, deliberately bare. Tidying the three back into one `try` reads as an
  obvious simplification and silently costs the feature its best case: a
  search that *raised* is exactly when a second, independent resolver is worth
  its request, and one enclosing handler would swallow the error and leave the
  block before the converter was ever reached. `_resolve_pmc_id_via_idconv()`
  never raises, so it needs no handler of its own. Pinned by
  `test_the_converter_is_consulted_when_the_search_itself_failed`.
- **A converter-discovered PMC ID is tried at Europe PMC even when the search
  hit said `inEPMC="N"`.** For that sub-case Europe PMC has already said it
  lacks the full text, so the attempt is near-certainly a 404 before NCBI gets
  the ID. Believing the flag needs a third value out of
  `_resolve_pmc_id_and_pdf_url()` — the multi-element tuple PR #42 spent a
  whole change removing from this module's neighbour — plus the state to carry
  it, and a stale flag is one of the reasons the converter exists. Deferred
  deliberately: revisit only if it measures as a real cost in a bulk run.
- **`_fetch_ncbi_pmc()` raises on a reply with neither body nor abstract.**
  efetch answers an article whose publisher does not release XML with a stub
  that is HTTP 200 and parses cleanly. Returned rather than raised, the
  body-less machinery promotes it to `abstract_only` and the caller gets
  near-empty HTML labelled `content_kind="abstract"` — worse than the DOI link
  it displaced, and permanent for anything persisting results. A genuine
  body-less article carrying a real abstract still returns. Pinned by
  `test_a_stub_with_no_article_raises` and
  `test_a_body_less_article_with_an_abstract_is_returned`.
- **`context_processor` measures the string it will actually send; it never
  assumes a size.** Three parts of the batcher look like they could be
  simplified into an arithmetic shortcut, and each shortcut is a way
  `max_context_chars` was already broken upstream. (1) `_split_to_fit()` runs
  a trial split, *measures* the formatted overflow, and reduces the budget by
  exactly that before re-splitting — because `split_oversized_item()` cuts raw
  content while the batcher measures decorated content, so a piece cut to the
  limit exceeds it. (2) `TRUNCATE` returns `_Preformatted`, which
  `_format_one()` renders as-is; handing the truncated string back as an
  ordinary item decorates it twice. (3) Each item is measured at the index it
  will occupy, and re-measured at index 0 when it starts a fresh batch,
  because a `format_item()` that renders the index changes width with it. The
  invariant all three protect is
  `Batch.total_chars == len(_format_batch_content(batch, config))`, and it is
  asserted directly by `test_every_batch_reports_the_size_it_actually_formats_to`
  and `test_a_boundary_item_is_measured_where_it_lands`. Each of the three has
  its own named test that fails when the fix is reverted — verified by
  mutation, not by inspection.
- **Four more things in `context_processor` look like they want simplifying,
  and each is load-bearing.** (1) `_render()` substitutes in one regex pass;
  the obvious `template.replace("{query}", …).replace("{content}", …)` runs
  its second pass over what the first substituted, so a query containing the
  literal `{content}` gets the whole batch spliced into it — doubling a
  prompt sized to fit exactly. (2) The package `__init__` reaches
  `llm_processor` through a PEP 562 `__getattr__`; a plain re-export pulls
  `BaseAgent`, `bmlib.templates` and jinja2 into every import of the LLM-free
  harness, which is the reason the package is top-level at all. (3)
  `process()` keeps its statistics in a local, not on `self` — as instance
  state, two concurrent runs on one processor each returned the other's
  counts. (4) `success_rate` cannot just return 1.0 for a batch-less run: an
  empty input had nothing to lose, but a run whose every item was dropped as
  oversized lost everything, and one number covering both reads a total loss
  as a clean run. Each has a named test that fails when the fix is reverted.
- **`estimate_item_size()` was deliberately not ported**, though it is in the
  upstream ABC and looks like a free performance win. The batcher calls
  `format_item()` on every item anyway to pack it, so the estimate saves
  nothing — and it is the reason upstream could deem an item small enough
  while the packing measurement disagreed, leaving an oversized item unsplit
  and its batch silently over the limit. Re-adding it re-opens that gap.
- **The recursion wraps results in `ConsolidatedItem`, not a
  `(content, metadata)` tuple.** The tuple is what made upstream's
  `format_consolidated_item()` dead code — defined and never called, so
  `SemanticChunkProcessor.format_item()` had to `isinstance`-sniff whether a
  2-tuple was its own `(text, score)` or the harness's. With a declared type
  `_format_one()` routes it, and `format_item()` only ever sees the caller's
  own items. The harness writes `recursion_level` into that metadata itself
  rather than trusting the extractor to have copied its batch metadata
  forward. Pinned by `test_a_recursion_level_receives_consolidated_items` and
  `test_consolidated_items_route_to_their_own_formatter`.
- **`min_items_for_recursion` stopping the run at one result is correct, not
  an early exit to remove.** One result has nothing to be consolidated
  *with*, so recursing re-summarises it once per level until the ceiling —
  burning `max_recursion_depth` model calls to make the answer shorter and no
  better. Pinned by
  `test_too_few_results_to_consolidate_returns_what_there_is`.
- **`LLMChunkProcessor` renders prompts with `str.replace`, not
  `str.format`, and clamps a structured-output confidence to 0.0–1.0.** A
  caller's template may legitimately hold literal braces — a JSON example, a
  regex — which `format()` would reject or demand be doubled; only `{query}`
  and `{content}` are substituted, both checked present at construction. The
  clamp matters because `min_confidence_threshold` and the weighted merge
  both assume the range, so a model reporting 1.4 would outrank every honest
  result. Pinned by `test_literal_braces_survive_rendering` and
  `test_a_confidence_outside_the_range_is_clamped`.
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
- **`bmlib.publications.retractions` has no downloader.**
  `parse_retraction_watch_csv()` takes a path or an open binary stream;
  acquiring the Crossref export is the caller's problem, not this module's.
  The endpoint is also slow enough to make that the right call regardless of
  taste: it returned `504 Gateway Time-out` on three attempts before serving
  the full file, which is a fair warning about owning that dependency inside
  a library.
- **Retraction Watch is not registered as a fetcher, and never will be
  without a protocol change.** Every registered fetcher is a date-keyed feed
  of `FetchedRecord`s that `store_publication()` upserts; Retraction Watch is
  one bulk file with no date to iterate, annotating papers that are usually
  not in the caller's `publications` table at all. Forcing it through the
  registry means either a fake `target_date` loop or a second, divergent
  protocol behind the same name — argued in full in the design doc's "Why
  this is not a fetcher".
- **No wiring into `transparency/` or `quality/`.** A retracted paper
  arguably belongs in a transparency score or as a quality veto, but both are
  scoring changes that move stored values for existing users, and neither is
  needed to deliver the lookup. A separate decision, not an oversight here.
- **No `is_paper_retracted(conn, doi=...)` convenience wrapper.** It would be
  exactly `is_retracted(lookup_retractions(conn, doi=...))` — two named steps
  that keep the pure rule (`is_retracted()`, no connection) separable from
  the I/O (`lookup_retractions()`), which is what makes the rule testable and
  re-derivable without a database.
- **The `%m/%d/%Y` / `%d/%m/%Y` date ambiguity in the Retraction Watch export
  is real and is deliberately left unresolved, not a bug to disambiguate
  further.** For any day <= 12 both formats parse and disagree, and nothing
  in the row says which was meant. US-first is kept because Retraction Watch
  is a US publication — confirmed correct for this export by same-file dates
  whose day exceeds 12, not merely assumed. Pinned by
  `test_an_ambiguous_date_resolves_month_first`.
- **`_ABSENT_IDENTIFIER_VALUES` holds exactly `{"0", "unavailable"}`, and
  adding a third sentinel needs its own measurement, not a guess.** Each was
  measured against the live 2026-08-03 export — `"0"` in 46.04% of PubMed ID
  cells, `"unavailable"` (either casing) in 4.80% of DOI cells — because a
  truthiness test accepts both and each collapses tens of thousands of
  unrelated notices onto one fake key. Pinned by the `TestIdentifierSentinels`
  class in `tests/test_retractions.py`.
- **`QualityManager._enrich_with_cochrane()` does not copy
  `CochraneStudyAssessment.evidence_level` onto `QualityAssessment.evidence_level`.**
  They are different vocabularies — Cochrane's is free-form model text
  (`"Level 2 (moderate-high)"`), the metadata tier's is an Oxford CEBM level —
  and merging them would silently coerce one into looking like the other. The
  Cochrane value stays reachable at `result.cochrane_assessment.evidence_level`.
  Pinned by `test_the_evidence_level_vocabularies_are_not_mixed`.
- **`CochraneAssessor.assess()` returns `None` on failure, never a
  `CochraneStudyAssessment` with all nine domains defaulted to "Unclear
  risk".** A fabricated all-unclear result is indistinguishable from a real
  assessment in which the model genuinely judged every domain unclear, and
  anything persisting results would store the fabrication permanently.
  Pinned by `test_a_response_with_no_risk_of_bias_block_is_rejected` and
  `test_a_blank_title_and_text_returns_none_without_calling_the_model`.
- **`collapse_risk_of_bias()` raises `ValueError` on an unrecognised
  `bias_type` rather than skipping the item.** Silently dropping it would
  return a `BiasRisk` that looks complete and is not — a caller filtering on
  `bias_risk.selection` would trust a value that one of the five domains
  feeding it never actually reported. Pinned by
  `test_an_unrecognised_bias_type_raises`.
- **In `collapse_risk_of_bias()`'s worst-wins reduction, `unclear` outranks
  `low`, not the other way round.** An unreported domain is not a clean bill
  of health: a study with one undescribed allocation-concealment method and
  three well-described low-risk domains does not get to claim low
  selection-bias risk. Pinned by `test_unclear_outranks_low`.
- **`CochraneAssessor._ASSESSMENT_ATTEMPTS = 2`, not 1 or 3.** `chat_json()`
  already retries transport and JSON-shape failures inside each attempt
  (`max_retries=3`); this outer bound covers a reply that parses but carries
  no `risk_of_bias` section at all. Two, not three: a model that omits the
  section twice has misread the prompt, and two keeps the worst case at six
  model calls (2 × 3) rather than nine (3 × 3). Pinned by
  `test_it_is_retried_once_before_giving_up`.
- **`CochraneAssessor.assess()` takes `study_id` from the caller and never
  guesses one from an author list.** Upstream derived a label with
  `first_author.split()[-1]`, which reads "van der Berg" as "Berg" and any
  "Surname, Given" string backwards. Unset, it falls back to
  `f"Study {document_id}"` and then to the title — never a parsed name.
  Pinned by `test_a_document_id_is_the_first_fallback`.
- **Oversized text is condensed in exactly two passes — digest, then
  assess — with no per-chunk risk-of-bias judgement and no merge step.**
  `bmlib.context_processor.LLMChunkProcessor` reduces the paper to one
  evidence digest first; the nine-domain judgement is then made once, over
  that digest. Judging each chunk separately and merging nine-domain
  verdicts across chunks was rejected: a domain like "blinding of
  participants and personnel" needs the whole Methods section in view at
  once, not a vote over pieces that each saw only part of it — there is no
  sensible way to merge two conflicting per-chunk judgements of the same
  domain. Truncation was rejected for the opposite reason: allocation
  concealment and blinding live in Methods and attrition in Results, so a
  head-of-string cut drops exactly the evidence the domains rest on.
  Exercised end-to-end by `test_condensation_reduces_every_chunk_of_the_paper`
  and `test_the_digest_reaches_the_model_instead_of_the_paper`.
- **`CochraneAssessor._condense()` checks `len(digest)` against
  `condense_config.max_context_chars`, not `ProcessingStatus`.** The tempting
  simplification is to treat a non-`FAILED` status as "the digest is usable" —
  it reads as free, since `process()` already reports failure on the result.
  It is wrong: `TRUNCATED` names the harness's own recursion ceiling, not the
  size of what it produced, and `_merge_results()` returns whatever the last
  level held once `max_recursion_depth` is reached, oversized or not. A
  `TRUNCATED` run whose digest happens to fit is fine, and a `PARTIAL` run
  whose digest does not fit is not — the status and the size are independent
  facts. Measured, not assumed: a 200-character budget was observed producing
  a 21,269-character digest, fed straight into the assessment prompt with no
  check. Pinned by `test_a_digest_that_still_exceeds_the_budget_is_not_judged`,
  with `test_the_guard_does_not_reject_a_digest_that_actually_fits` as the
  negative control proving the guard can fail.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API connection
  as first argument, type hints and docstrings on everything public, AGPL-3
  header on every source file, dataclass models with `to_dict()`/`from_dict()`,
  explicit SQL (no ORM), optional dependencies guarded with a helpful
  `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`** — CI pins
  **0.15.20** (`.github/workflows/ci.yml`), while `.venv` holds 0.6.5, which
  false-flags `UP038` on `ollama.py`, a rule newer ruff removed:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP;
  no external services. To run the PostgreSQL half of `test_backends.py`, set
  `BMLIB_TEST_POSTGRESQL_DSN` to a database the tests may drop every table in.
- New functionality needs unit tests; see CLAUDE.md's test-file mapping table.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
- **Cutting a release** (0.4.0 through 0.7.0 were all cut this way): bump the
  version in the **four** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header — promote
  the CHANGELOG's `[Unreleased]` body under a dated `## [X.Y.Z]` heading
  (leaving `## [Unreleased]` above it), promote any `(unreleased)` markers in
  `docs/manual/` and `ROADMAP.md`, then commit on a `release/X.Y.Z` branch and
  open a PR. After CI is green merge with `--merge` (**not** squash) so the
  tag lands on main's first-parent line, tag the *merge commit*, push the tag,
  and create the GitHub release. **The workflow publishes to PyPI, not you** —
  creating the release fires `.github/workflows/release.yml`, which rebuilds,
  refuses to go on unless the tag matches `bmlib.__version__`, runs
  `twine check --strict`, asserts `py.typed` survived packaging, and uploads
  via Trusted Publishing with no stored token. Approve the `pypi` environment
  gate to let it through. **Do not also upload by hand:** the publish job has
  no `skip-existing`, so a manual upload first makes it fail on a duplicate —
  which is why v0.5.0's and v0.6.0's runs are still sitting unapproved, those
  two having been published from a laptop instead. **v0.7.0 was the first
  release to go the whole way through the workflow, and it worked** — so the
  hand-upload habit has no remaining excuse. The tag may sit on any merge
  commit on main's first-parent line that contains the version bump; v0.7.0's
  landed on a dependabot merge that followed the release merge, which the
  workflow's `tag == bmlib.__version__` check accepts and which is fine.
  Rehearse the whole path
  any time with a `workflow_dispatch` run, which targets TestPyPI only. PyPI's
  JSON API serves a stale CDN cache afterwards; verify against
  `https://pypi.org/simple/bmlib/`, which is what installers actually read.
