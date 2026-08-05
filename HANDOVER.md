# HANDOVER — bmlib development

_Last updated: 2026-08-06. **0.7.0 is released and on PyPI.** `[Unreleased]`
carries three Phase 2 ports: the Cochrane assessment agent (row 9, PR #54,
merged), the PDF section segmenter (row 8, PR #55, merged), and the
citation/reference stack (row 4, on `feature/citations`, PR open). Two open
issues (#56, #57), both minor `fulltext` refinements deferred from PR #55's
review. 1602 tests passing + 49 skipped, ruff clean. **The next piece of
work is the last Phase 2 port, row 11** — see "Next up"._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Current state

- **Version 0.7.0**, released 2026-08-04 and live on PyPI — the **first
  release published by the Release workflow** rather than by hand, so that
  path is now proven end to end (tag → GitHub release → `pypi` environment
  gate → Trusted Publishing upload). Release history: 0.4.0 (2026-07-19) →
  0.5.0 (2026-07-20) → 0.5.1 (2026-07-21) → 0.6.0 (2026-07-30) → 0.7.0.
  0.3.0 was bumped in-tree but never released; its changes shipped inside
  0.4.0. The version lives in **four** places — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header — and
  all four agree.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.7.0 carried four behaviour changes that move stored values, none
  behind a flag (deposition repositories raise `transparency_score` /
  `data_availability_level`; three previously-unrecognised trial registries
  move `trial_registered`; a funder CrossRef repeats collapses to one
  indicator line; `FullTextResult.source` gains `"ncbi_pmc"`). 0.6.0's three
  non-comparable changes are separate and still apply to anyone upgrading
  across both. Anything persisting these values needs to know.
- **`~/src/bmlibrarian` still pins `bmlib[ollama]>=0.5.1,<0.6.0`**, so it has
  now missed two releases. Widening it is a downstream change, not a bmlib
  one.
- **`[Unreleased]` carries three Phase 2 ports.** (1) The **Cochrane
  assessment agent** (row 9, PR #54, merged): `CochraneAssessor` (Tier 4)
  turns a title and text into a `CochraneStudyAssessment`;
  `collapse_risk_of_bias()` bridges its nine domains onto the five-domain
  `BiasRisk`; `QualityFilter(use_cochrane_assessment=True)` plus
  `full_text=` on `QualityManager.assess()` wire it in, enriching a
  classification rather than replacing it. (2) The **PDF section
  segmenter** (row 8, PR #55, merged): `SectionSegmenter` turns the
  `TextBlock` lines from the new `PyMuPDFConverter.extract_blocks()`
  (behind the `LayoutExtractor` protocol) into a `SegmentedDocument` of
  typed sections — standalone, nothing wires it into `FullTextService` or
  `quality/` yet. (3) The **citation/reference stack** (row 4, PR open):
  new pure-stdlib `bmlib/citations/` — `[@id:N:Label]` marker parsing as
  pure functions, Vancouver/APA/Harvard/Chicago formatters, and
  `build_references()`/`format_document()` with caller-injected
  `Mapping[int, DocumentMetadata]` (the upstream DB fetch severed). See
  `CHANGELOG.md` for the full entries and the upstream defects each port
  fixed.
- **1602 tests passing + 49 skipped** (`uv run pytest tests/ -q`). 47 skips
  are the PostgreSQL parameterisations of `tests/test_backends.py`, which run
  only when `BMLIB_TEST_POSTGRESQL_DSN` is set; 1 is a PostgreSQL-only schema
  test; 1 is `test_pymupdf_requires_dependency`, which runs only when
  PyMuPDF is *absent*. **PyMuPDF is now installed in the dev venv** (PR #55
  did it so the extraction tests run locally), which is why the old "2 skips
  need PyMuPDF" note is gone. With a PostgreSQL DSN set the counts shift
  accordingly.
- **Documentation was rewritten for 0.4.0 and kept current through 0.7.0.**
  Treat drift as a regression worth fixing, not expected staleness. The
  `(unreleased)` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release time; if you add one, it is the next release's job to promote it.
  Markers inside `docs/superpowers/plans/` are historical records — leave
  them alone.

## Next up

### Open GitHub issues

Two, both minor `fulltext` refinements deferred from PR #55's review rather
than folded into it: **#56** (`_extract_title()` trusts junk PDF metadata
titles — "Microsoft Word - manuscript.docx" wins over the large-font
first-page line) and **#57** (`convert()` on a password-protected PDF
returns `success=True` with empty text; `is_complete` already says `False`,
and `extract_blocks()` already raises for the same file). Every closed
design stays in `docs/superpowers/specs/` as the record of what was rejected
and why.

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** so the mother project can consume
  0.6.0 and 0.7.0. Read both releases' non-comparable behaviour changes
  first — the transparency ones move stored scores.
- **Wire the segmenter and the rule-based extractors in.** Two halves of the
  same roadmap item: the segmenter (this PR) could give `CochraneAssessor`
  Methods/Results boundaries and `TransparencyAnalyzer` the paper's own
  Funding/COI/Data-availability sections; `quality/extractors.py` is still
  called by no tier. Each needs its own design conversation.

### bmlibrarian → bmlib porting (Phase 2 nearly done)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

- **Phase 0 is done** (0.4.0): json_repair, text_utils, Cochrane models +
  formatter, extractors + scoring_models, pdf_converter.
- **Phase 1 is done**: BaseAgent enhancement (PR #34), `context_processor`
  (#49, PR #50).
- **Phase 2** rows are rows in the analysis doc's master table, not GitHub
  issues. Done: row 10 Retraction Watch (PR #51, shipped 0.7.0), row 9
  Cochrane assessor (PR #54, merged), row 8 PDF section segmenter (PR #55,
  merged), row 4 citation/reference stack (`feature/citations`, PR open).
  **Remaining: row 11 (PubMed abstract-markdown + grant/affiliation
  extraction, grafted onto `publications/fetchers/pubmed.py`) — the last
  Phase 2 row.**
- Phases 3–4 (discovery, pubmed_search, MeSH, the prompt-driven agent
  family, paper_weight) are in the analysis doc.

### The port recipe (repeat it)

1. **TDD, always.** Write behaviour tests first (the upstream code is the
   spec), watch them fail (a `ModuleNotFoundError` is the correct red for a
   new module), then port and watch them pass. Bug in a test you wrote? Fix
   the test, not correct code.
2. **Modernise to bmlib style:** AGPL header on every file,
   `from __future__ import annotations`, lowercase builtin generics,
   `datetime.UTC`.
3. **Sever app coupling:** replace `get_db_manager()`/`bmlibrarian.config`
   with injected connections/params; guard optional deps with
   `try/except ImportError` raising `pip install bmlib[extra]`; route LLM
   calls through `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** the public names from the package `__init__.py` `__all__`.
5. **Verify:** tests + both ruff commands clean before done.
6. **Record** each port in `CHANGELOG.md` under `[Unreleased]`.
7. **Reconcile, don't fork:** where a port overlaps existing bmlib, build on
   the existing module.

## Deliberate non-fixes — do not "fix" these

Each was investigated and closed as correct. Reopening them wastes a session.
Entries marked "argued inline" carry their full reasoning as comments in the
named source file; the entry here is the pointer, not the argument.

### Transparency

- **`_INDUSTRY_STEMS` and `_INDUSTRY_WORDS` must not be merged into one
  list**, and neither may be extended without re-running
  `scripts/sample_funder_names.py` against `tests/data/funder_names.json` —
  the corpus *removed* intuitive members (`pharma`, `biotech`) on measured
  false positives. Metric test:
  `tests/test_funder_matching.py::TestAgainstTheLabelledCorpus`.
- **`_is_industry_funder()` is deliberately not applied to COI prose**;
  `_INDUSTRY_COI_KEYWORDS` stays separate — org suffixes match far too
  freely in running text.
- **`_merge_pubmed_signals()` mutates the `_Analysis` it is given and
  returns nothing**, and **every `analyze()` sub-step takes `_Analysis` and
  returns `None`** — one step threading a value while four mutate is the
  inconsistency that makes the next contributor guess. Pinned by
  `test_the_merge_applies_both_of_its_branches_to_one_list`.
- **The data-deposition rank-merge machinery** (`_DATA_LEVEL_RANK`,
  `note_data_level()`, `_DEPOSITION_DATABANK_LEVELS`) is argued inline in
  `transparency/analyzer.py`. Two rules: every producible level must be a
  key of the ranking or `note_data_level()` raises by design; the
  deposition list deliberately excludes reference-only databases (dbSNP,
  OMIM, RefSeq…). Pinned by
  `test_every_pattern_maps_to_a_level_the_ranking_knows`,
  `test_every_repository_maps_to_a_level_the_ranking_knows`,
  `test_the_deposition_and_registry_name_sets_are_disjoint`.
- **`TransparencySettings.filtering_enabled`, `max_concurrent_analyses`,
  `cache_results` are not dead code** — caller-owned orchestration hints,
  documented as such.
- **`outcome_switching_detected` stays reserved and always `False`** — a
  real feature with real false-positive risk, kept in the schema so
  persisted results need no migration when detection lands.
- **A PubMed record with no `<CoiStatement>` leaves `coi_disclosed` alone.**
  Absence means the publisher supplied no statement, not that the paper
  carries none. Pinned by
  `test_an_absent_pubmed_statement_leaves_the_status_unknown`.
- **`<DataBankList>` accessions are validated as `NCT\d{8}` before use, and
  a ClinicalTrials.gov entry failing validation still counts as
  registered** — registration is separate from followability. Pinned by
  `test_a_malformed_accession_never_reaches_a_url` and
  `test_an_unusable_clinicaltrials_accession_is_not_called_another_registry`.
- **The transparency indicator strings are module constants, not literals**
  — the PubMed step retracts the two full-text COI lines by name. Pinned by
  `test_a_pubmed_statement_retracts_the_full_text_absence_indicator`.

### Positional stability

- **`Publication.pmcid`, `BaseAgent.__init__`'s `embedding_model`, and
  `TransparencyResult.unknown_reason` are each declared last** on their
  dataclass/signature — downstream projects construct positionally, and any
  other placement shifts every following argument silently. Pinned by
  `test_positional_construction_is_stable_across_versions`.

### db / llm / agents

- **PostgreSQL transaction nesting is detected from bmlib's own open-block
  count, not psycopg2's status**, keyed by *(thread, `id(conn)`)* — see
  CLAUDE.md for why both parts are load-bearing.
- **The Ollama raw `/api/tags` path re-implements httpx's safety defaults on
  purpose** (HTTP(S)-only scheme, bearer token stripped across cross-origin
  redirects, `"<word>:<digits>"` read as host:port). Each has a regression
  test naming it.
- **The JSON extractors prefer a whole span to a nested fragment in three
  places** (argued inline in `llm/utils.py`, `llm/json_repair.py`,
  `agents/base.py` — all guarding #33's silent truncation), **a fenced
  candidate wins on parse alone**, **`parse_json()` enforces `dict | list`**
  (a bare scalar raises → retry inside `chat_json()`), **`require_dict` has
  a third `bool` overload** (mypy does not expand `bool` into the two
  `Literal`s; CI runs ruff only, so nothing catches its removal), and
  **`salvage_json_fields()` bounds both passes with `RecursionError` caught
  wherever a candidate is decoded**. Pinning tests:
  `TestExtractJsonPrefersWholeSpans`,
  `test_raises_rather_than_returning_a_fragment_of_a_broken_array`,
  `test_a_truncated_array_of_objects_is_repaired_whole`,
  `test_a_fragment_is_still_the_last_resort`,
  `test_fenced_array_of_objects_is_returned_whole`,
  `test_a_bare_scalar_is_not_a_structured_answer`,
  `test_repair_is_attempted_at_most_once_per_key`,
  `test_fast_pass_is_bounded_to_the_match_cap`,
  `test_the_last_match_is_still_reached_beyond_the_cap`,
  `test_deep_nesting_returns_the_text_rather_than_raising`,
  `test_deeply_nested_text_raises_valueerror_not_recursionerror`.
  `iter_json_spans()` dedupes candidates by text, not position (argued
  inline in `llm/utils.py`).
- **`PerformanceMetrics.elapsed_time_seconds` reads `time.monotonic()`**,
  not the wall-clock timestamps it stores; `snapshot()` must copy the
  monotonic marks by hand (`init=False`). Model-inference and prompt-eval
  timers are deliberately omitted — no provider reports them through bmlib.
  Pinned by `test_elapsed_survives_a_wall_clock_step` and
  `test_snapshot_carries_the_monotonic_marks`.

### context_processor

- **The batcher measures the string it will actually send; it never assumes
  a size.** Three tempting arithmetic shortcuts each re-break
  `max_context_chars` the way upstream did; the invariant is
  `Batch.total_chars == len(_format_batch_content(batch, config))`, pinned
  by `test_every_batch_reports_the_size_it_actually_formats_to` and
  `test_a_boundary_item_is_measured_where_it_lands`. `estimate_item_size()`
  was deliberately not ported — it let the oversized decision disagree with
  the packing measurement.
- **Four more load-bearing "simplifications" refused**, each with a named
  test: `_render()` substitutes in one regex pass (two-pass `.replace()`
  splices the batch into a query containing `{content}`); the package
  `__init__` reaches `llm_processor` through PEP 562 `__getattr__` (a plain
  re-export drags jinja2 into the LLM-free harness); `process()` keeps
  statistics in a local, not on `self`; `success_rate` cannot return 1.0 for
  a batch-less run that dropped everything as oversized.
- **The recursion wraps results in `ConsolidatedItem`, not a tuple** (what
  made upstream's `format_consolidated_item()` dead code), and
  **`min_items_for_recursion` stopping at one result is correct** — one
  result has nothing to be consolidated with. Pinned by
  `test_a_recursion_level_receives_consolidated_items`,
  `test_consolidated_items_route_to_their_own_formatter`,
  `test_too_few_results_to_consolidate_returns_what_there_is`.
- **`LLMChunkProcessor` renders prompts with `str.replace`, not
  `str.format`** (templates legitimately hold literal braces), and clamps a
  structured-output confidence to 0.0–1.0. Pinned by
  `test_literal_braces_survive_rendering` and
  `test_a_confidence_outside_the_range_is_clamped`.

### fulltext — retrieval and JATS

- **`_JATSHandler.endElement` tests `in_figure or in_table_wrap` before any
  prose branch and routes on `in_caption`** — asking about the section first
  blanks the caption and renames the section; the same branch deliberately
  drops non-caption `<p>` inside figures/tables. Pinned by
  `TestJATSParserCaptionScoping` and
  `TestJATSParserUnsectionedBodyFurniture`.
- **NCBI's ID Converter is consulted *after* the Europe PMC search** (the
  search also carries the free-PDF URL) **but *outside* the search's
  `except`, in its own statement** — a search that raised is exactly when a
  second resolver is worth its request, and one enclosing handler would
  swallow the error before the converter was reached. A converter-discovered
  PMC ID is tried at Europe PMC even when the search hit said
  `inEPMC="N"` (believing the flag needs a third tuple element and a stale
  flag is one reason the converter exists — revisit only if it measures as a
  real cost). Pinned by
  `test_the_converter_is_not_consulted_when_the_search_found_an_id` and
  `test_the_converter_is_consulted_when_the_search_itself_failed`.
- **`_fetch_ncbi_pmc()` raises on a reply with neither body nor abstract** —
  efetch answers a publisher-withheld article with a stub that is HTTP 200
  and parses cleanly; returned instead of raised, it becomes near-empty HTML
  labelled `content_kind="abstract"`. Pinned by
  `test_a_stub_with_no_article_raises` and
  `test_a_body_less_article_with_an_abstract_is_returned`.

### fulltext — PDF section segmenter (PR #55)

- **`TextBlock` is one PDF *line*, not a span, with font attributes from the
  dominant span** (most non-whitespace characters, ties to the first).
  PyMuPDF starts a new span at every font change, so span-level blocks —
  upstream's shape — shatter a mixed-font heading into fragments no anchored
  pattern can match, and a superscript marker must not restyle its line.
  Pinned by `test_a_heading_split_across_spans_is_one_block` and
  `test_a_superscript_marker_does_not_restyle_the_line`.
- **Front matter is a 0.5-confidence section, not dropped** — what precedes
  the first detected heading is a container, not a classification; if the
  real first heading was missed, it has swallowed the introduction. Same
  confidence, same reason, as the no-markers "Full Text" fallback. Pinned by
  `test_front_matter_is_kept`.
- **A heading with no body is reported with `content == ""`, never
  dropped** — dropping it says the paper has no such section when it has an
  empty one. Pinned by `test_a_heading_with_no_body_is_still_reported`.
- **`SectionType.TITLE`, `SegmentedDocument.authors` and
  `Section.subsections` are reserved, not dead** — documented as never
  populated today, kept so a future producer needs no schema change (the
  `outcome_switching_detected` precedent).
- **`extract_blocks()` raises where `convert()` returns a failed result.**
  Partial text is useful and `converted_pages` says how partial; a partial
  block list is indistinguishable from a sparse PDF, so degradation would be
  silent. Pinned by `test_a_corrupt_pdf_raises_rather_than_degrading`.
- **A negative vertical gap (column/page boundary) inserts no paragraph
  break, documented rather than "fixed"** — a PDF gives no signal that
  distinguishes a paragraph continuing across a page from one ending at it.
  Pinned by `test_a_page_boundary_is_not_a_paragraph_break`. The
  `height == 0` degenerate-bbox case is likewise acknowledged in a comment
  in `_join_blocks` and left — guessing a floor would be an assumed size.
- **CONFLICTS owns the disclosure family, in both numbers** — FUNDING once
  listed the singular `financial disclosure` while CONFLICTS listed both, so
  the two numbers of the same heading landed in different sections, decided
  by dict iteration order. Pinned by
  `test_financial_disclosure_classifies_the_same_in_both_numbers`; a comment
  in FUNDING's pattern list wards off re-adding it.
- **The 0.7 partial-match pass can fire on a bold figure caption** ("Fig. 3
  Study results" → RESULTS at 0.7) — a knowing spec-level choice, kept
  upstream-faithful; the manual tells callers to check `Section.confidence`.
  Related: `min_heading_size` is an absolute floor (default 10.0) in an
  otherwise median-relative design — it can silence the segmenter on a 9pt
  two-column layout, which is documented in the manual and guarded by a test
  that genuinely isolates the floor
  (`test_a_font_below_the_minimum_is_not_a_heading`, inputs 9.0 vs median
  6.0 — an 8.0-vs-10.0 input is vacuous, rejected by the ratio rule too).
- **Two final-review minors parked with rulings:** the manual's headline
  example calls `extract_blocks()` without an `isinstance(converter,
  LayoutExtractor)` check (brevity; the check is documented four paragraphs
  later; no mypy in CI), and `pdf_converter.py`'s literal `12.0` default
  font size is not shared with `segmenter.py`'s `_DEFAULT_FONT_SIZE`
  (sharing it inverts the import direction for a cosmetic gain).

### citations (PR open)

- **Upstream's code is the output spec, not its docstrings** — where the two
  disagreed (APA renders `"(2023) Title"`, no period after the year, though
  upstream's docstring example shows one), the code's output was kept.
  Only five confirmed defects were fixed, each with a named regression test
  — four listed in the design doc
  (`docs/superpowers/specs/2026-08-06-citations-port-design.md`), plus a
  fifth from the PR #58 review: a whitespace-only author entry crashed
  every style's `format_reference` with `IndexError` (upstream ran
  `parts[-1]` on an empty split); blank entries are now dropped via
  `_named_authors()`.
- **An empty title renders per style, not uniformly** — Vancouver/APA say
  `Untitled`, Harvard `''`, Chicago `"."` — upstream-faithful, pinned by
  `TestEmptyTitles` rather than unified.
- **A lone inverted name as a bare `authors` string is ambiguous** —
  `from_dict({"authors": "Smith, John"})` splits into two authors; only a
  semicolon marks the string form as inverted. Documented in the manual;
  callers wanting exactness pass a list.
- **`format_reference_list()` begins with `"\n---"`, no leading blank
  line** — upstream-faithful; a document not ending in a blank line renders
  its last line as a setext heading, documented in the manual rather than
  changed.
- **`generate_label()` without a year yields `"Smithn.d."`**, and
  **`author_surname("Jan van der Berg")` is `"Berg"`** (particles survive
  only in the inverted format) — both upstream-faithful, both pinned by
  tests naming them.
- **`Citation` compares by all fields**, not upstream's `document_id`-only
  equality — nothing ported relies on the old semantics; pinned by
  `test_two_citations_of_one_document_at_different_positions_differ`.
- **Marker ids are `int` only** — upstream's grammar; a string-id variant is
  a spec change, noted out of scope in the design doc.

### publications — retractions

- **`bmlib.publications.retractions` has no downloader** (the Crossref
  endpoint 504s freely; acquiring the export is the caller's problem), **is
  not a fetcher and never will be without a protocol change** (a notice
  annotates a paper usually not in the caller's table — see the design
  doc's "Why this is not a fetcher"), **is not wired into `transparency/`
  or `quality/`** (both are scoring changes moving stored values — separate
  decisions), and **has no `is_paper_retracted()` convenience wrapper**
  (keeping the pure rule separable from the I/O is what makes it testable).
- **The `%m/%d/%Y` / `%d/%m/%Y` ambiguity is real and deliberately
  unresolved** — US-first, confirmed by same-file dates whose day exceeds
  12. Pinned by `test_an_ambiguous_date_resolves_month_first`.
- **`_ABSENT_IDENTIFIER_VALUES` holds exactly `{"0", "unavailable"}`**, each
  measured against the live export; a third sentinel needs its own
  measurement. Pinned by `TestIdentifierSentinels`.

### quality — Cochrane assessor (merged, PR #54)

- **`_enrich_with_cochrane()` does not copy the Cochrane `evidence_level`
  onto `QualityAssessment.evidence_level`** — different vocabularies
  (free-form model text vs Oxford CEBM). Pinned by
  `test_the_evidence_level_vocabularies_are_not_mixed`.
- **`assess()` returns `None` on failure, never nine defaulted "Unclear
  risk" domains** — a fabrication indistinguishable from a real all-unclear
  judgement. Pinned by
  `test_a_response_with_no_risk_of_bias_block_is_rejected`.
- **`collapse_risk_of_bias()` raises on an unrecognised `bias_type`**
  (silently skipping returns a `BiasRisk` that looks complete and is not),
  and **`unclear` outranks `low` in its worst-wins reduction** (an
  unreported domain is not a clean bill of health). Pinned by
  `test_an_unrecognised_bias_type_raises` and `test_unclear_outranks_low`.
- **`_ASSESSMENT_ATTEMPTS = 2`, not 1 or 3** — `chat_json()` already retries
  inside each attempt; two keeps the worst case at six model calls. Pinned
  by `test_it_is_retried_once_before_giving_up`.
- **`study_id` comes from the caller, never parsed from an author list**
  (upstream's `first_author.split()[-1]` read "van der Berg" as "Berg").
  Pinned by `test_a_document_id_is_the_first_fallback`.
- **Oversized text is condensed in exactly two passes — digest, then one
  nine-domain judgement — with no per-chunk verdicts to merge** (a domain
  like blinding needs the whole Methods in view; truncation drops exactly
  the evidence the domains rest on), and **`_condense()` checks
  `len(digest)` against the budget, not `ProcessingStatus`** — `TRUNCATED`
  names the recursion ceiling, not the size of what it produced (a
  21,269-char digest was measured emerging from a 200-char budget). Pinned
  by `test_condensation_reduces_every_chunk_of_the_paper`,
  `test_the_digest_reaches_the_model_instead_of_the_paper`,
  `test_a_digest_that_still_exceeds_the_budget_is_not_judged` (with
  `test_the_guard_does_not_reject_a_digest_that_actually_fits` as the
  negative control).

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` — pure functions with the DB-API
  connection as first argument, type hints and docstrings on everything
  public, AGPL-3 header on every source file, dataclass models with
  `to_dict()`/`from_dict()` where they persist, explicit SQL (no ORM),
  optional dependencies guarded with a helpful `ImportError`.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`** — CI pins
  **0.15.20** (`.github/workflows/ci.yml`), while `.venv` holds an older
  one that false-flags rules newer ruff removed:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP;
  no external services. PyMuPDF-needing tests sit behind
  `skipif(not _HAS_FITZ)` and run locally now that the dev venv has it. To
  run the PostgreSQL half of `test_backends.py`, set
  `BMLIB_TEST_POSTGRESQL_DSN` to a database the tests may drop every table
  in.
- New functionality needs unit tests; see CLAUDE.md's test-file mapping
  table.
- Session workflow lives in the `nextsession` skill
  (`.claude/skills/nextsession/`); the post-review fix-up workflow lives in
  the `fixall` skill (`.claude/skills/fixall/`).
- **Cutting a release** (0.4.0 through 0.7.0 were all cut this way): bump
  the version in the **four** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header —
  promote the CHANGELOG's `[Unreleased]` body under a dated `## [X.Y.Z]`
  heading (leaving `## [Unreleased]` above it), promote any `(unreleased)`
  markers in `docs/manual/` and `ROADMAP.md`, then commit on a
  `release/X.Y.Z` branch and open a PR. After CI is green merge with
  `--merge` (**not** squash) so the tag lands on main's first-parent line,
  tag the *merge commit*, push the tag, and create the GitHub release.
  **The workflow publishes to PyPI, not you** — creating the release fires
  `.github/workflows/release.yml`, which rebuilds, refuses to go on unless
  the tag matches `bmlib.__version__`, runs `twine check --strict`, asserts
  `py.typed` survived packaging, and uploads via Trusted Publishing with no
  stored token. Approve the `pypi` environment gate to let it through.
  **Do not also upload by hand:** the publish job has no `skip-existing`,
  so a manual upload first makes it fail on a duplicate — which is why
  v0.5.0's and v0.6.0's runs are still sitting unapproved, those two having
  been published from a laptop. **v0.7.0 went the whole way through the
  workflow and it worked**, so the hand-upload habit has no remaining
  excuse. The tag may sit on any merge commit on main's first-parent line
  that contains the version bump. Rehearse the whole path any time with a
  `workflow_dispatch` run, which targets TestPyPI only. PyPI's JSON API
  serves a stale CDN cache afterwards; verify against
  `https://pypi.org/simple/bmlib/`, which is what installers actually read.
