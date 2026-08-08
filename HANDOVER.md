# HANDOVER — bmlib development

_Last updated: 2026-08-09. **0.8.0 is released and on PyPI**, and with it
**Phase 2 of the bmlibrarian port is complete** — all four ports
(Cochrane assessor PR #54, PDF section segmenter PR #55, citation/reference
stack PR #58, PubMed metadata graft PR #59), plus the encrypted-PDF fix
(#57, PR #60). **#64 is fixed** (PR #66, review round applied) —
`bmlib.fulltext` now imports on a core install, and a new `fulltext` extra
exists. Two open issues, **#56** and **#67**.
1706 tests + 58 skipped (1762 + 2 with a PostgreSQL DSN), ruff clean.
`[Unreleased]` carries #64's fix, so the next release is at least a patch.
**Phase 3 is next, and each of its rows needs a design conversation before
any porting** — see "Next up"._

This file briefs the next session on what is done, what is still open, and
the conventions to keep. Update it whenever a session materially changes the
plan; delete sections that are finished and no longer instructive. Per-PR
implementation detail lives in git history, `CHANGELOG.md` and `docs/plans/`
— do not re-narrate it here.

## Current state

- **Version 0.8.0**, released 2026-08-08 and live on PyPI. Release history:
  0.4.0 (2026-07-19) → 0.5.0 (2026-07-20) → 0.5.1 (2026-07-21) → 0.6.0
  (2026-07-30) → 0.7.0 (2026-08-04) → 0.8.0. 0.3.0 was bumped in-tree but
  never released; its changes shipped inside 0.4.0. The version lives in
  **four** places — `pyproject.toml`, `bmlib/__init__.py`, the README version
  line, `CLAUDE.md`'s header — and all four agree. 0.7.0 was the first
  release the Release workflow published rather than a laptop, so that path
  is proven end to end (tag → GitHub release → `pypi` environment gate →
  Trusted Publishing upload); 0.8.0 went the same way.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it
  here. 0.6.0 (three changes), 0.7.0 (four) and 0.8.0 (three) each moved
  stored values, none behind a flag, and they compound for anyone upgrading
  across them. 0.8.0's largest is the PubMed one: every synced title and
  abstract changes shape, titles because they were being *truncated* at their
  first markup tag.
- **`~/src/bmlibrarian` still pins `bmlib[ollama]>=0.5.1,<0.6.0`**, so it has
  now missed three releases. Widening it is a downstream change, not a bmlib
  one.
- **1706 tests passing + 58 skipped** (`uv run pytest tests/ -q`); **1762 + 2
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
- **Documentation was rewritten for 0.4.0 and has been kept current since.**
  Treat drift as a regression worth fixing, not expected staleness. The
  `(unreleased)` markers in `docs/manual/` and `ROADMAP.md` are promoted at
  release time; ROADMAP currently carries one, for #64. Markers inside
  `docs/superpowers/plans/` are historical records — leave them alone.

## Next up

### Open GitHub issues

Two.

**#67 — a total retrieval failure is indistinguishable from "no free full
text"**, found by the review of PR #66 and filed rather than folded into it,
since it predates that PR. `fetch_fulltext()` wraps all nine tiers in `except
Exception` logging at DEBUG; when every one fails, `service.py:379-380`
returns a normal-looking `FullTextResult` and emits nothing above DEBUG. The
only WARNING on that path sits inside `if abstract_only is not None:`, so the
*more* complete the failure, the quieter it is — a caller who has lost
network or hit a bmlib bug sees "no full text" for a whole corpus with nothing
in the logs. `_cache_html` (`service.py:508-514`) has the same shape: a
read-only cache directory means every run silently re-fetches. `_attach_pdf_text`
is the standard to fix them to — every empty path there logs at WARNING.

**#56 — `_extract_title()` trusts junk PDF metadata titles** ("Microsoft
Word - manuscript.docx" wins over the large-font first-page line), deferred
from PR #55's review. Note what it will cost to do *properly*: this repo
settles list-shaped questions by measuring a corpus, not by taste (the
industry-funder stems, the Markdown escape set, the DataBankName allow-list
are all precedents), so a reject-list for junk titles wants a sample of real
PDF metadata behind it rather than a guessed set of prefixes and suffixes.
The issue already asks for a regression test per rejected shape plus a
negative control. Every closed design stays in `docs/superpowers/specs/` as
the record of what was rejected and why.

### Worth doing, not yet an issue

- **Cut 0.8.1.** `[Unreleased]` holds #64's fix, which is exactly the kind of
  thing a patch release is for — a headline 0.8.0 addition
  (`SectionSegmenter`) is unreachable for anyone who installed core bmlib,
  and the fix is additive with no behaviour change to stored values. The
  release recipe is at the bottom of this file; note the version now lives in
  four places *and* the extras tables in README, `docs/manual/index.md` and
  CLAUDE.md gained a `fulltext` row.
- **Widen bmlibrarian's `<0.6.0` pin** so the mother project can consume
  0.6.0, 0.7.0 and 0.8.0. Read all three releases' non-comparable behaviour
  changes first — the transparency ones move stored scores, and 0.8.0 moves
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

### bmlibrarian → bmlib porting (Phase 3 is next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list
with reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

- **Phases 0, 1 and 2 are all done and shipped.** Phase 0 in 0.4.0
  (json_repair, text_utils, Cochrane models + formatter, extractors +
  scoring_models, pdf_converter); Phase 1 in 0.7.0 (BaseAgent enhancement
  PR #34, `context_processor` #49/PR #50); Phase 2 across 0.7.0 and 0.8.0 —
  its rows are rows in the analysis doc's master table, not GitHub issues:
  row 10 Retraction Watch (PR #51), row 9 Cochrane assessor (PR #54), row 8
  PDF section segmenter (PR #55), row 4 citations (PR #58), row 11 PubMed
  metadata graft (PR #59).
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
8. **Read the spec on both sides; do not decide by eye.** Row 11's reviews
   found this three times. Reading someone's XML, check their DTD:
   `<Affiliation>` looks like a leaf, is declared `(%text;)*`, and a bare
   `.text` read silently dropped rows. Declaring an output format, you owe
   that format's rules for *every* value, not only the ones carrying markup:
   having decided titles were Markdown, the fetcher neither escaped the
   prose it wrapped nor checked that `<u>` had a Markdown spelling that was
   not already `<b>`'s.

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
- **Four more, each argued where it lives and each with a test naming it:**
  `TransparencySettings.filtering_enabled` / `max_concurrent_analyses` /
  `cache_results` are caller-owned orchestration hints, not dead code;
  `outcome_switching_detected` stays reserved and always `False` (kept in the
  schema so persisted results need no migration when detection lands); a
  PubMed record with no `<CoiStatement>` leaves `coi_disclosed` alone (absence
  means the publisher supplied none); and `<DataBankList>` accessions are
  validated as `NCT\d{8}` before becoming a URL, though an entry failing
  validation still counts as registered — registration is separate from
  followability.

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
  wherever a candidate is decoded**. `iter_json_spans()` dedupes candidates by
  text, not position. Eleven tests pin these — seven in
  `test_json_extraction.py` (from `TestExtractJsonPrefersWholeSpans`) and four
  in `test_agents.py`.
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
  `Batch.total_chars == len(_format_batch_content(batch, config))`.
  `estimate_item_size()` was deliberately not ported — it let the oversized
  decision disagree with the packing measurement.
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

### fulltext — importable on a core install (#64, PR #66)

- **Both halves of the fix stay, and the reason is counter-intuitive — read
  the mutation table in
  `docs/superpowers/specs/2026-08-08-fulltext-import-without-httpx-design.md`
  before removing either.** They overlap: once httpx moved into
  `FullTextService.__init__`, `service.py` has no top-level `import httpx`,
  so restoring the eager re-export in `__init__.py` gates nothing and **no
  test failed**. Either change alone restores importability. What the PEP 562
  deferral buys on its own is that `import bmlib.fulltext` never loads
  `service`, so no future top-level import there can gate the parser, the
  models or the segmenter again —
  `test_importing_the_package_does_not_load_the_service` is the guard that
  isolates it, and it was written *because* mutation testing found nothing
  else did.
- **Measure package-import claims with one fresh interpreter per module.** A
  single process leaves the half-initialised parent in `sys.modules` and the
  siblings then falsely read as importable; that is how #64 was first
  mis-scoped to one module when the real blast radius was ten across two
  packages. `TestPackageImports` masks `httpx` with a `sys.meta_path` finder
  in a subprocess for the same reason, and carries a negative control
  asserting the mask actually masks — every machine running this suite has
  httpx, so a mask that silently failed would make the whole class vacuous.
- **The guard is the first statement of `__init__`**, so a failed
  construction leaves no cache directory behind (`FullTextCache()` creates
  three); the extra-naming test asserts the redirected home directory stayed
  *entirely* empty, rather than naming platform cache paths — a named subset
  passes vacuously wherever it guessed wrong.
- **`_require_httpx()` returns the module; nothing stores it.** Review of PR
  #66 killed the `self._httpx` the design had specified. A module object
  cannot be pickled, so storing one silently cost the ability to hand a
  configured service to a `ProcessPoolExecutor` — a regression against 0.8.0
  — and reading it back as *instance* state makes anything that reaches
  `_http_get` without running `__init__` raise `AttributeError`, which the
  tier chain swallows at DEBUG and returns a success-shaped result for.
  `PyMuPDFConverter` still stores `self._fitz`: it was never picklable, so
  nothing there regressed. **Do not "simplify" this back.**
- **The guard reports the caught exception instead of asserting the cause.**
  `except ImportError` also catches the `ModuleNotFoundError` a *present*
  httpx raises for its own missing dependency; "not installed" then
  prescribes a `pip install` that answers "Requirement already satisfied",
  so the reader runs it, sees success, retries and hits the same error.
  `_attach_pdf_text` already documents this for PyMuPDF.
- **`_http_get` had no test at all** until this review — all ~45 tests in the
  file patch `_http_get` itself, so replacing its body with `raise
  AssertionError` left the suite green. `TestHttpGet` is what covers it now.
  Any future change to how the client is built needs a test there, because
  nothing else in the file will notice.
- **A PEP 562 `__dir__` returns `sorted(set(__all__) | set(globals()))`.**
  `sorted(__all__)` is a narrowing — it drops the submodules and every dunder
  — and a test asserting only that the lazy names are present passes under
  it. Fixed in `context_processor` at the same time.
- **`fulltext = ["httpx>=0.25"]` is httpx only**; `pdf` stays separate (it
  already exists, is separately documented, and bundling would drag a ~20 MB
  binary wheel onto anyone who only wants JATS retrieval). `publications` and
  `transparency` keep their own httpx, so no existing install changed.
  `test_the_extra_the_error_message_names_is_a_real_one` reads
  `Provides-Extra` from the installed metadata, so the message in
  `_require_httpx` and `pyproject.toml` cannot drift apart.

### fulltext — the PDF converter (PR #60)

- **A password-protected PDF is rejected on `doc.needs_pass`, never on
  `doc.is_encrypted`.** An *owner* password restricts permissions without
  blocking reads, so such a file is encrypted and converts perfectly;
  widening the check to `is_encrypted` would reject it. Both guards carry an
  owner-password negative control for exactly that
  (`test_an_owner_password_alone_does_not_block_conversion` /
  `..._extraction`), so neither is a check that cannot fail.
- **`extract_blocks()` keeps its explicit check even though it already
  raised** — it raised only because `get_text()` failed of its own accord,
  and had that stopped, it would have returned `[]`, exactly what an
  image-only scan returns. The general lesson, and the reason #57 existed:
  `except` blocks written to keep one bad page from aborting the rest will
  also absorb a whole-file failure, and the result reports as a success.

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
  a page from one ending at it. The `height == 0` degenerate-bbox case is
  acknowledged in `_join_blocks` and left.
- **CONFLICTS owns the disclosure family, in both numbers** — FUNDING once
  listed the singular `financial disclosure` while CONFLICTS listed both, so
  the two numbers of the same heading landed in different sections, decided by
  dict iteration order. A comment in FUNDING's pattern list wards off
  re-adding it.
- **Two known spec-level limits, documented in `docs/manual/fulltext.md`
  rather than fixed:** the 0.7 partial-match pass can fire on a bold figure
  caption ("Fig. 3 Study results" → RESULTS), and `min_heading_size` is an
  absolute floor (10.0) in an otherwise median-relative design, so it can
  silence the segmenter on a 9pt two-column layout. Callers are told to check
  `Section.confidence`.

### citations (merged, PR #58)

**Argued in full in `docs/manual/citations.md` and
`docs/superpowers/specs/2026-08-06-citations-port-design.md` — read them
before "correcting" anything here.** Upstream's *code* is the output spec,
not its docstrings, where the two disagree. Five upstream-faithful oddities
are kept rather than unified (per-style empty-title rendering, the ambiguous
bare inverted `authors` string, `"\n---"` with no leading blank line,
`"Smithn.d."`, `author_surname("Jan van der Berg") == "Berg"`), each pinned
by a test naming it. Two deliberate departures: `Citation` compares by all
fields, and marker ids stay `int` only. Five upstream defects were fixed,
the fifth from PR #58's review — a whitespace-only author entry crashed every
style with `IndexError`.

### publications — PubMed metadata graft (PR #59)

**CLAUDE.md argues most of this port in full — read it there, and do not
re-derive any of it.** Its "Replace-per-source child rows" settles the
`source` column and the scoped delete, `_stamp_source()`, the `ValueError`
on an unnamed row, the deliberate absence of a UNIQUE constraint, the empty
guard, and `_consolidate_rows()`'s relocation; its "Markdown, measured
against the markup" settles the mixed-content walker, strip-once, edge
whitespace outside the markers, `Label` **or** `NlmCategory`, the measured
escape set, and `<u>`. Each is pinned by a named test on both backends,
several verified by mutation. What follows is only what CLAUDE.md omits.

- **PubMed repeats a `<Grant>` block verbatim** — 31 of 575 entries across 200
  NIH-funded records, affecting 14 — so `_parse_grants()` collapses exact
  repeats, keeping first-occurrence order. Two grants differing in any field
  are two grants.
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
- **Which elements get the formatting walker is decided by NLM's DTD.**
  `ArticleTitle`, `AbstractText` and `Affiliation` are all declared
  `(%text;)*` — `#PCDATA | b | i | sup | sub | u` — so all three use
  `_text_with_formatting`. `Journal/Title`, `DescriptorName` and
  `PublicationType` are declared `(#PCDATA)`, genuine leaves, and keep plain
  `.text`. Do not widen or narrow this list by eye; check the DTD.

### publications — retractions

- **`bmlib.publications.retractions` has no downloader** (the Crossref
  endpoint 504s freely), **is not a fetcher and never will be without a
  protocol change** (a notice annotates a paper usually not in the caller's
  table — see the design doc's "Why this is not a fetcher"), **is not wired
  into `transparency/` or `quality/`** (both are scoring changes moving
  stored values), and **has no `is_paper_retracted()` wrapper** (keeping the
  pure rule separable from the I/O is what makes it testable).
- **Two values measured against the live export, not reasoned about**: the
  `%m/%d/%Y` / `%d/%m/%Y` ambiguity resolves US-first (confirmed by same-file
  dates whose day exceeds 12), and `_ABSENT_IDENTIFIER_VALUES` holds exactly
  `{"0", "unavailable"}` — a third sentinel needs its own measurement.

### quality — Cochrane assessor (merged, PR #54)

Full reasoning in `docs/superpowers/specs/2026-08-05-cochrane-assessor-design.md`
and `docs/manual/quality.md`; every claim below has a named test.

- **Nothing is fabricated to fill a gap:** `assess()` returns `None` on
  failure rather than nine defaulted "Unclear risk" domains;
  `collapse_risk_of_bias()` raises on an unrecognised `bias_type` rather than
  skipping it into a `BiasRisk` that looks complete; `unclear` outranks `low`
  in its worst-wins reduction; `_enrich_with_cochrane()` does not copy
  Cochrane's `evidence_level` onto the assessment's; `study_id` comes from the
  caller, never parsed from an author list.
- **`_ASSESSMENT_ATTEMPTS = 2`, not 1 or 3** — `chat_json()` already retries
  inside each attempt; two keeps the worst case at six model calls.
- **Oversized text is condensed in exactly two passes** — digest, then one
  nine-domain judgement, no per-chunk verdicts to merge (blinding needs the
  whole Methods in view) — and **`_condense()` checks `len(digest)` against
  the budget, not `ProcessingStatus`**: `TRUNCATED` names the recursion
  ceiling, not the size of what it produced (a 21,269-char digest was measured
  emerging from a 200-char budget). Carries a negative control,
  `test_the_guard_does_not_reject_a_digest_that_actually_fits`.

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
- **Cutting a release** (0.4.0 through 0.8.0 were all cut this way): bump
  the version in the **four** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header —
  promote the CHANGELOG's `[Unreleased]` body under a dated `## [X.Y.Z]`
  heading (leaving `## [Unreleased]` above it) with a short prose summary
  under it, promote any `(unreleased)` markers in `docs/manual/` and
  `ROADMAP.md` (0.8.0's six were all in ROADMAP), then commit on a
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
  laptop. v0.7.0 and v0.8.0 both went the whole way through the workflow, so
  the hand-upload habit has no remaining excuse. Rehearse any time with a
  `workflow_dispatch` run, which targets TestPyPI only. Afterwards verify
  against `https://pypi.org/simple/bmlib/` — the JSON API serves a stale CDN
  cache, the simple index is what installers read.
