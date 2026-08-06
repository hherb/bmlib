# HANDOVER — bmlib development

_Last updated: 2026-08-06. **0.7.0 is released and on PyPI.** **Phase 2 of the
bmlibrarian port is complete**: `[Unreleased]` carries all four of its ports —
the Cochrane assessment agent (row 9, PR #54), the PDF section segmenter
(row 8, PR #55), the citation/reference stack (row 4, PR #58), and the PubMed
metadata graft (row 11, PR #59). Two open issues (#56, #57), both minor
`fulltext` refinements deferred from PR #55's review. 1685 tests passing + 58
skipped (1741 + 2 with a PostgreSQL DSN), ruff clean. **`[Unreleased]` is now
large enough to be worth cutting as 0.8.0** — see "Next up"._

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
  here. Both 0.6.0 (three changes) and 0.7.0 (four) moved stored values, none
  behind a flag, and they compound for anyone upgrading across both.
- **`~/src/bmlibrarian` still pins `bmlib[ollama]>=0.5.1,<0.6.0`**, so it has
  now missed two releases. Widening it is a downstream change, not a bmlib
  one.
- **`[Unreleased]` carries all four Phase 2 ports.** (1) The **Cochrane
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
  `quality/` yet. (3) The **citation/reference stack** (row 4, PR #58, merged):
  new pure-stdlib `bmlib/citations/` — `[@id:N:Label]` marker parsing as
  pure functions, Vancouver/APA/Harvard/Chicago formatters, and
  `build_references()`/`format_document()` with caller-injected
  `Mapping[int, DocumentMetadata]` (the upstream DB fetch severed). (4) The
  **PubMed metadata graft** (row 11, PR #59): `<GrantList>` and
  `<AffiliationInfo>` become `Grant` / `AuthorAffiliation` child rows in two
  new tables, and titles and abstracts are read as Markdown. See
  `CHANGELOG.md` for the full entries and the upstream defects each port
  fixed.
- **Three of the four move stored values, so `[Unreleased]` is not a
  drop-in upgrade.** The largest is the PubMed one: every synced title and
  abstract changes shape (titles because they were being *truncated* at their
  first markup tag; abstracts because they gain `NlmCategory` labels,
  blank-line section breaks and `CO~2~` notation). CHANGELOG says which, and
  the manual tells callers to re-sync or accept a mix.
- **1685 tests passing + 58 skipped** (`uv run pytest tests/ -q`); **1741 + 2
  with `BMLIB_TEST_POSTGRESQL_DSN` set**. 56 of the default skips are the
  PostgreSQL parameterisations of `tests/test_backends.py`; 1 is a
  PostgreSQL-only schema test; 1 is `test_pymupdf_requires_dependency`, which
  runs only when PyMuPDF is *absent*. **PyMuPDF is installed in the dev
  venv** (PR #55 did it so the extraction tests run locally).
- **Run the PostgreSQL half locally — it is two minutes and it finds real
  bugs.** Postgres.app ships the binaries. The socket directory must be a
  *short* path (the 103-byte limit bites, and a scratchpad path exceeds it;
  `createdb` then fails while `pg_ctl` reports success):
  ```bash
  PGBIN=/Applications/Postgres.app/Contents/Versions/16/bin
  mkdir -p /tmp/bmlpg/run
  $PGBIN/initdb -D /tmp/bmlpg/data -U postgres --auth=trust
  $PGBIN/pg_ctl -D /tmp/bmlpg/data \
      -o "-k /tmp/bmlpg/run -p 55432 -c listen_addresses=''" -l /tmp/bmlpg/pg.log start
  $PGBIN/createdb -h /tmp/bmlpg/run -p 55432 -U postgres bmlib_test
  export BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/bmlpg/run port=55432 dbname=bmlib_test user=postgres"
  ```
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

- **Cut 0.8.0.** Phase 2 is complete and `[Unreleased]` now holds four ports,
  three of which move stored values. That is a release's worth of work sitting
  unshipped, and the longer it sits the larger the "not comparable" note the
  next upgrader has to read. Recipe at the bottom of this file; it is a minor
  bump (everything is additive).
- **Widen bmlibrarian's `<0.6.0` pin** so the mother project can consume
  0.6.0 and 0.7.0. Read both releases' non-comparable behaviour changes
  first — the transparency ones move stored scores, and 0.8.0 will move
  every PubMed title and abstract.
- **Wire the segmenter and the rule-based extractors in.** Two halves of the
  same roadmap item: the segmenter could give `CochraneAssessor`
  Methods/Results boundaries and `TransparencyAnalyzer` the paper's own
  Funding/COI/Data-availability sections; `quality/extractors.py` is still
  called by no tier. Each needs its own design conversation.
- **Feed the stored grants to `transparency/`.** `TransparencyAnalyzer` runs
  its own `efetch` per paper to read `<GrantList>`, which `fetch_pubmed` has
  now already stored at sync time. Reading the table instead would save that
  request — but it is a scoring change that moves stored values, so it needs
  its own decision, not a quiet optimisation.

### bmlibrarian → bmlib porting (Phase 2 done)

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
- **Phase 2 is done.** Its rows are rows in the analysis doc's master table,
  not GitHub issues: row 10 Retraction Watch (PR #51, shipped 0.7.0), row 9
  Cochrane assessor (PR #54), row 8 PDF section segmenter (PR #55), row 4
  citation/reference stack (PR #58), row 11 PubMed metadata graft (PR #59).
- **Phase 3 is next**: discovery (#12), `pubmed_search` (#13), MeSH (#21),
  ClinicalTrials.gov (#14 — **check the caveat first**, the legacy bulk XML
  the parser targets was deprecated in the 2024 API v2 migration). These are
  larger subsystems than anything in Phase 2; expect each to need its own
  design conversation rather than a straight port. Phase 4 (the prompt-driven
  agent family, paper_weight, review building-blocks) follows, and each of
  those must be reconciled against the existing `quality/` and
  `transparency/` rather than forked — see the analysis doc's caveats.

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
8. **When the code parses someone's XML, read their DTD** rather than
   deciding by eye which elements are leaves. One of row 11's review findings
   was exactly that: `<Affiliation>` looks like a leaf, is declared
   `(%text;)*`, and a bare `.text` read silently dropped rows.
9. **If the code declares an output format, it owes that format's rules.**
   Row 11's later review found the same class of error twice over: having
   decided titles were Markdown, the fetcher neither escaped the prose it
   wrapped nor checked that `<u>` had a Markdown spelling that was not
   already `<b>`'s. Deciding a format is a promise about every value, not
   only the ones carrying markup.

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
- **Every `analyze()` sub-step takes `_Analysis`, mutates it, and returns
  `None`** — one step threading a value while four mutate is the
  inconsistency that makes the next contributor guess. Pinned by
  `test_the_merge_applies_both_of_its_branches_to_one_list`.
- **The data-deposition rank-merge machinery** (`_DATA_LEVEL_RANK`,
  `note_data_level()`, `_DEPOSITION_DATABANK_LEVELS`) is argued inline in
  `transparency/analyzer.py`. Two rules: every producible level must be a
  key of the ranking or `note_data_level()` raises by design; the deposition
  list deliberately excludes reference-only databases (dbSNP, OMIM, RefSeq…).
  Three tests in `test_transparency.py` pin it.
- **Four more, each argued where it lives:**
  `TransparencySettings.filtering_enabled` / `max_concurrent_analyses` /
  `cache_results` are caller-owned orchestration hints, not dead code;
  `outcome_switching_detected` stays reserved and always `False` (real
  false-positive risk, kept in the schema so persisted results need no
  migration when detection lands); a PubMed record with no `<CoiStatement>`
  leaves `coi_disclosed` alone (absence means the publisher supplied none,
  not that the paper carries none); and `<DataBankList>` accessions are
  validated as `NCT\d{8}` before becoming a URL, though an entry failing
  validation still counts as registered — registration is separate from
  followability. Each has a test naming it.

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
  wherever a candidate is decoded**. `iter_json_spans()` dedupes candidates
  by text, not position (argued inline in `llm/utils.py`). Eleven tests pin
  these — seven in `test_json_extraction.py` (starting at
  `TestExtractJsonPrefersWholeSpans`) and four in `test_agents.py`.
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
- **Six more load-bearing "simplifications" refused**, each with a named test
  in `test_context_processor.py` / `test_llm_chunk_processor.py`: `_render()`
  substitutes in one regex pass (two-pass `.replace()` splices the batch into
  a query containing `{content}`); the package `__init__` reaches
  `llm_processor` through PEP 562 `__getattr__` (a plain re-export drags
  jinja2 into the LLM-free harness); `process()` keeps statistics in a local,
  not on `self`; `success_rate` cannot return 1.0 for a batch-less run that
  dropped everything; the recursion wraps results in `ConsolidatedItem`, not
  a tuple (what made upstream's `format_consolidated_item()` dead code), and
  `min_items_for_recursion` stopping at one result is correct; and
  `LLMChunkProcessor` renders with `str.replace`, not `str.format` (templates
  legitimately hold literal braces).

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
  PMC ID is tried at Europe PMC even when the search said `inEPMC="N"`, since
  a stale flag is one reason the converter exists. Two tests in
  `test_fulltext_service.py` pin it, starting at
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
- **Nothing is dropped for being empty or unclassified**, each with a named
  test: front matter is a 0.5-confidence section (if the real first heading
  was missed, it has swallowed the introduction); a heading with no body is
  reported with `content == ""`; and `SectionType.TITLE`,
  `SegmentedDocument.authors` and `Section.subsections` are reserved, not
  dead (the `outcome_switching_detected` precedent).
- **`extract_blocks()` raises where `convert()` returns a failed result.**
  Partial text is useful and `converted_pages` says how partial; a partial
  block list is indistinguishable from a sparse PDF, so degradation would be
  silent. Pinned by `test_a_corrupt_pdf_raises_rather_than_degrading`.
- **A negative vertical gap (column/page boundary) inserts no paragraph
  break** — a PDF gives no signal distinguishing a paragraph continuing across
  a page from one ending at it. Pinned by
  `test_a_page_boundary_is_not_a_paragraph_break`; the `height == 0`
  degenerate-bbox case is acknowledged in `_join_blocks` and left.
- **CONFLICTS owns the disclosure family, in both numbers** — FUNDING once
  listed the singular `financial disclosure` while CONFLICTS listed both, so
  the two numbers of the same heading landed in different sections, decided
  by dict iteration order. Pinned by
  `test_financial_disclosure_classifies_the_same_in_both_numbers`; a comment
  in FUNDING's pattern list wards off re-adding it.
- **Two known spec-level limits, documented in the manual rather than
  fixed:** the 0.7 partial-match pass can fire on a bold figure caption
  ("Fig. 3 Study results" → RESULTS), and `min_heading_size` is an absolute
  floor (10.0) in an otherwise median-relative design, so it can silence the
  segmenter on a 9pt two-column layout. Callers are told to check
  `Section.confidence`. The floor's test genuinely isolates it
  (`test_a_font_below_the_minimum_is_not_a_heading`, 9.0 against median 6.0 —
  an 8.0-vs-10.0 input is vacuous, rejected by the ratio rule too).

### citations (merged, PR #58)

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
- **Five upstream-faithful oddities kept rather than unified**, each pinned
  by a test naming it: an empty title renders per style (Vancouver/APA
  `Untitled`, Harvard `''`, Chicago `"."`); a lone inverted name as a bare
  `authors` string is ambiguous, so `{"authors": "Smith, John"}` splits into
  two authors and only a semicolon marks the string form as inverted;
  `format_reference_list()` begins `"\n---"` with no leading blank line (a
  document not ending in a blank line renders its last line as a setext
  heading); `generate_label()` without a year yields `"Smithn.d."`; and
  `author_surname("Jan van der Berg")` is `"Berg"` — particles survive only
  in the inverted format. All are documented in the manual.
- **Two deliberate departures from upstream:** `Citation` compares by all
  fields, not upstream's `document_id`-only equality (nothing ported relies
  on the old semantics), and marker ids stay `int` only — a string-id variant
  is a spec change, noted out of scope in the design doc.

### publications — PubMed metadata graft (PR #59)

- **Replace-per-source is the whole design of these two tables.** Both carry a
  `source` column and `_replace_child_rows()` scopes every delete to it, so a
  record's rows replace that source's stored rows and leave other sources'
  alone. Scoping by publication alone was a real defect caught before release:
  PubMed's grants replaced OpenAlex's, then OpenAlex's replaced PubMed's, so
  the stored answer depended on whichever source synced last, silently.
  Deliberately unlike `fulltext_sources`, which simply accumulates — a paper
  genuinely has several URLs, whereas each source states its funding
  completely. Pinned by `test_a_second_source_does_not_displace_the_first` and
  `test_re_syncing_one_source_replaces_only_its_own_rows`, both backends,
  mutation-verified.
- **`sync._stamp_source()` fills the column, not the fetchers.** A fetcher that
  forgets fails silently — its rows land in an unnamed bucket and stop being
  scoped — so provenance is stamped in the one place that authoritatively
  knows it.
- **The empty guard survives for a different reason than it started with.** It
  was there to stop a bioRxiv record erasing PubMed's grants; source scoping
  now handles that structurally. It stays because no rows names no source, so
  there is nothing to scope a delete to, and an absent `<GrantList>` means the
  record lacked the data rather than that funding was withdrawn.
- **No UNIQUE constraint on the natural key, and one must not be added.** Every
  column of a grant proper is nullable and both backends treat `NULL` as
  *distinct* in a unique index, so
  `UNIQUE(publication_id, source, agency, grant_id)` lets
  `(1, 'pubmed', NULL, 'R01')` insert twice. An expression index over
  `COALESCE`d columns would work, but nothing is left for it to catch: exact
  repeats are collapsed at parse time and the per-source replace is idempotent.
- **PubMed repeats a `<Grant>` block verbatim** — 31 of 575 entries across 200
  NIH-funded records, affecting 14 — so `_parse_grants()` collapses exact
  repeats, keeping first-occurrence order. Two grants differing in any field
  are two grants.
- **`_consolidate_rows()` relocates every child row before the parent DELETE**,
  per source: a source the keep row has wins, one only the drop row saw moves
  across. Both backends enforce foreign keys, so a stranded row aborts the
  whole store — verified by removing the relocation and watching both raise
  `ForeignKeyViolation`. Pinned by
  `test_a_split_identity_merge_relocates_child_rows` and
  `test_consolidation_moves_only_sources_the_keep_row_lacks`.
- **`position` indexes `<AuthorList>`, not `Publication.authors`** — it counts
  the `<CollectiveName>` consortia that `authors` skips, so the two lists
  differ in length whenever one is present and `authors[a.position]` is the
  wrong way to resolve an affiliation's author (match on `author`). What
  position is *for* — first or senior author — is right either way. The
  knock-on, accepted: a consortium stating an affiliation loses it, since
  recording it would put an `author` in the table that is absent from
  `authors`, breaking the one join the column exists for. Pinned by
  `test_position_indexes_the_xml_author_list_not_the_authors_field`.
- **`store_publication()` does not write `publication_id` back onto the
  `Grant` / `AuthorAffiliation` objects it is given**, unlike its `pub`
  argument, which it mutates in place and documents as such — the
  `FullTextSource` precedent. Worth knowing because the failure is silent:
  the field reads `0`, a plausible id rather than an obvious sentinel. Pinned
  by `test_the_caller_s_objects_are_not_mutated`.
- **`is_retracted` was not ported.** `publication_types` already carries
  "Retracted Publication" verbatim, `retractions.py` answers authoritatively
  from Retraction Watch, and upstream reads RefType `RetractionOf` — which
  marks an article as *being* the retraction notice — as retracted.
- **Upstream's `_extract_date` was not ported.** `_parse_pubdate` is strictly
  better: upstream defaults a missing month and day to `01`, inventing
  precision the record does not have, and swallows every failure in a bare
  `except:`.
- **`~x~` / `^x^` are Pandoc extensions, knowingly.** A renderer without them
  shows the tildes literally; the alternative flattened `CO<sub>2</sub>` and
  `CO<sup>2</sup>` to the same ambiguous `CO2`. Documented in the manual.
- **The escape set is measured, and re-deciding it needs a measurement, not
  an opinion.** `_escape_markdown()` escapes ``\ ` * ~ ^`` in document prose
  and nothing else. Across 3,403 real titles and abstract sections that
  alters 0.35% and removes every construct a CommonMark parser found;
  escaping `_` and `[`/`]` too churned 4.3% and fixed nothing, because
  intraword `_` is inert in CommonMark and a bare `[…]` is not a link. The
  commonest real hazard is the tilde, which *this module* created by emitting
  `~2~`: "AUC ~ 0.80" and "(~88%)" are ordinary prose and an unescaped pair
  subscripts the span between them. The asterisk case is the star allele,
  `CYP2C19 (*1, *2, *3)`. Affiliations share the walker and so are escaped
  too — which is user-visible, because that column is a join key.
- **`<u>` is deliberately not mapped and must not be re-added.** Markdown has
  no underline; `__x__` is *strong* emphasis, so mapping `<u>` renders it
  identically to `<b>` while asserting the source said "bold" — the ambiguity
  `sub`/`sup` earned Pandoc markers to avoid. It falls through to the
  undecorated path. Pinned by `test_underline_is_not_rendered_as_bold`.
- **A grant or affiliation naming no source raises `ValueError`.** Scoping is
  the whole mechanism, so an unnamed row is unreachable: nothing can name it,
  so no sync can replace it and each one stacks a labelled duplicate beside
  it. The check is in the storage layer, not left to `NOT NULL`, because the
  column rejects `None` while `""` — the dataclass default, and the value a
  forgetful caller actually produces — was stored happily. Nine tests were
  silently exercising that path before the guard landed. Pinned by
  `test_a_row_naming_no_source_is_rejected`.
- **Which elements get the formatting walker is decided by NLM's DTD.**
  `ArticleTitle`, `AbstractText` and `Affiliation` are all declared
  `(%text;)*` — `#PCDATA | b | i | sup | sub | u` — so all three use
  `_text_with_formatting`. `Journal/Title`, `DescriptorName` and
  `PublicationType` are declared `(#PCDATA)`, genuine leaves, and keep plain
  `.text`. Do not widen or narrow this list by eye; check the DTD.

### publications — retractions

- **`bmlib.publications.retractions` has no downloader** (the Crossref
  endpoint 504s freely; acquiring the export is the caller's problem), **is
  not a fetcher and never will be without a protocol change** (a notice
  annotates a paper usually not in the caller's table — see the design
  doc's "Why this is not a fetcher"), **is not wired into `transparency/`
  or `quality/`** (both are scoring changes moving stored values — separate
  decisions), and **has no `is_paper_retracted()` convenience wrapper**
  (keeping the pure rule separable from the I/O is what makes it testable).
- **Two values measured against the live export, not reasoned about**: the
  `%m/%d/%Y` / `%d/%m/%Y` ambiguity resolves US-first (confirmed by same-file
  dates whose day exceeds 12), and `_ABSENT_IDENTIFIER_VALUES` holds exactly
  `{"0", "unavailable"}` — a third sentinel needs its own measurement. Pinned
  by `test_an_ambiguous_date_resolves_month_first` and
  `TestIdentifierSentinels`.

### quality — Cochrane assessor (merged, PR #54)

- **Nothing is fabricated to fill a gap**, each with a named test:
  `assess()` returns `None` on failure rather than nine defaulted "Unclear
  risk" domains (indistinguishable from a real all-unclear judgement);
  `collapse_risk_of_bias()` raises on an unrecognised `bias_type` rather than
  skipping it into a `BiasRisk` that looks complete; `unclear` outranks `low`
  in its worst-wins reduction (an unreported domain is not a clean bill of
  health); `_enrich_with_cochrane()` does not copy Cochrane's
  `evidence_level` onto the assessment's (free-form model text vs Oxford
  CEBM); and `study_id` comes from the caller, never parsed from an author
  list (upstream's `first_author.split()[-1]` read "van der Berg" as "Berg").
- **`_ASSESSMENT_ATTEMPTS = 2`, not 1 or 3** — `chat_json()` already retries
  inside each attempt; two keeps the worst case at six model calls. Pinned
  by `test_it_is_retried_once_before_giving_up`.
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
  no external services. `BMLIB_TEST_POSTGRESQL_DSN` must point at a database
  the tests may drop every table in (recipe under "Current state").
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
  **Do not also upload by hand:** the publish job has no `skip-existing`, so
  a manual upload first makes it fail on a duplicate — which is why v0.5.0's
  and v0.6.0's runs still sit unapproved, both having been published from a
  laptop. v0.7.0 went the whole way through the workflow, so the hand-upload
  habit has no remaining excuse. Rehearse any time with a
  `workflow_dispatch` run, which targets TestPyPI only. Afterwards verify
  against `https://pypi.org/simple/bmlib/` — the JSON API serves a stale CDN
  cache, the simple index is what installers read.
