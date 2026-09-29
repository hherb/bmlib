# bmlib — Rust

The Rust port of [`bmlib`](../). Functionally equivalent to the Python
library, **and equivalent to a corrected version of it**: where Python is
wrong, this implements the intended behaviour rather than reproducing the
defect.

The analysis behind the port — scope, the four roadblocks, the dependency
policy, the phase order, and the sixteen Python defects this port fixes rather
than reproduces — lives in
[`docs/plans/2026-09-26-rust-port-roadblocks.md`](../docs/plans/2026-09-26-rust-port-roadblocks.md).

## Layout

```
rust/
├── Cargo.toml          workspace
├── oracle/             differential-oracle generators (Python side)
└── bmlib/
    ├── src/
    │   ├── lib.rs
    │   ├── pyvalue.rs  Python's `bool()`/`str()`/`repr()` and `type().__name__`
    │   │               for a decoded JSON value — one home for the six copies of
    │   │               `bool`/`str` (#350) and the eight `repr`/type-name copies
    │   │               (#365)
    │   ├── context_processor/  port of bmlib/context_processor/ (1,710 lines)
    │   │   ├── base.rs         batching, recursion, consolidation
    │   │   ├── data_types.rs   config, results, strategies, status
    │   │   └── mod.rs
    │   ├── fulltext/   port of bmlib/fulltext/ (models, cache, service, readers, converters)
    │   ├── transparency/  port of bmlib/transparency/ (models + the multi-API analyzer)
    │   ├── llm/        port of bmlib/llm/ (complete: types, protocols, client)
    │   │   ├── json_repair.rs   repair malformed LLM JSON (fixes #299)
    │   │   ├── text_utils.rs    TextChunker + the text helpers
    │   │   ├── utils.rs         JSON span location
    │   │   └── mod.rs
    │   ├── publications/ Phase 2 of the port
    │   │   ├── models.rs         Publication, child rows, validators (88 oracle cases)
    │   │   ├── schema.rs         both DDLs, byte-for-byte (9 tests)
    │   │   ├── storage.rs        dedup, merge, per-source child rows
    │   │   ├── retractions.rs    Retraction Watch parse + the retraction rule
    │   │   ├── sync.rs           day selection, the durability rule, day status
    │   │   ├── fetchers/         the source contract
    │   │   │   ├── reconcile.rs  delivered-vs-promised, three rules
    │   │   │   ├── registry.rs   the Fetcher trait, HttpClient, registry
    │   │   │   ├── biorxiv.rs    bioRxiv/medRxiv walker
    │   │   │   ├── openalex.rs   OpenAlex cursor walker (fixed #313; Python adopted it)
    │   │   │   └── pubmed.rs     the PubmedArticle XML reader
    │   │   ├── csv.rs            CSV with Python's physical `line_num`
    │   │   └── mod.rs
    │   ├── quality/    port of bmlib/quality/ (pure half)
    │   │   ├── cochrane_formatter.rs  Markdown + HTML renderers (fixes #312)
    │   │   ├── cochrane_models.rs  nine-domain RoB + study characteristics
    │   │   ├── data_models.rs    StudyDesign, QualityTier, QualityAssessment, QualityFilter
    │   │   ├── extractors.rs     rule-based study-type / sample-size: Python's own
    │   │   │                     tables through `fancy-regex`, so #294/#297/#298 are
    │   │   │                     Python's decisions now and §9 carries the one
    │   │   │                     character-class divergence
    │   │   ├── scoring_models.rs DimensionScore + AssessmentDetail
    │   │   └── mod.rs
    │   ├── citations/  port of bmlib/citations/ (1,129 Python lines)
    │   │   ├── builder.rs      build_references, format_document
    │   │   ├── formatter.rs    Vancouver / APA / Harvard / Chicago
    │   │   ├── models.rs       Citation, DocumentMetadata, CitationStyle
    │   │   ├── mod.rs
    │   │   └── parser.rs       the [@id:N:Label] scanner (hand-rolled)
    │   └── db/         port of bmlib/db/ (787 Python lines)
    │       ├── backend.rs       dialects, placeholder rewriting
    │       ├── error.rs         DbError — backend + caller-abort
    │       ├── migrations.rs    Migration, run_migrations
    │       ├── mod.rs           the public surface
    │       ├── operations.rs    execute / fetch_* / create_tables
    │       ├── postgres.rs      the real PostgreSQL backend (feature `postgres`)
    │       ├── split.rs         multi-statement SQL splitting
    │       ├── sqlite.rs        the three Db impls
    │       ├── traits.rs        the Db trait
    │       ├── transactions.rs  composable savepoints
    │       └── value.rs         Value, Row — the boundary types
    └── tests/
        ├── common/     both_backends! macro + the pg_sim harness, and
        │               oracle.rs — the corpora's response vocabulary,
        │               sink.rs — a FetchSink that keeps what a test wants to
        │               look at, and pubmed_sim.rs — a scripted E-utilities
        │               transport, all shared by every harness that scripts a
        │               transport
        ├── data/       differential-oracle fixtures (vendored)
        └── *.rs        one file per ported Python test module
```

## Running it

```bash
cd rust
cargo test                                   # 952 tests, 3 of them doc-tests
cargo clippy --all-targets                   # expected clean
cargo fmt --check
cargo doc --no-deps                          # expected clean; CI runs it with
                                             # RUSTDOCFLAGS=-D warnings

# The PDFium backend tests, which need a downloaded library
cargo test --features pdf

# The real PostgreSQL backend. Without the variable the ten tests return
# immediately, so the count is the same either way and no socket is opened.
BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live

# The live tests, which make real requests and are **skipped unless the
# variable is set** — the default `cargo test` opens no socket (0.16s).
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1
```

`--test-threads=1` matters for the live tests: **NCBI rate-limits by source
address**, so a concurrent run draws 429s that read as parse failures.

The PostgreSQL suite needs no `--test-threads=1`: every test creates its own
database, so there is nothing to serialise. It needs a server and a role that
may `CREATE DATABASE`; the connection variables are documented at the top of
`bmlib/tests/postgres_live.rs`.

**CI runs all three of the gated-off configurations** (`.github/workflows/ci.yml`,
alongside the Python jobs): `rust-lint` for rustfmt and clippy in both feature
sets, `rust-test` for the `default` and `pdf` matrices, and `rust-postgres` for
the **whole** suite over a `postgres:16` service — not the live binary alone, so a
dialect failure and a feature-gated build failure are told apart by the failing
test's name. The two gates exist so a plain `cargo test` opens no socket; CI sets
`BMLIB_PG_TESTS` and never `BMLIB_LIVE_TESTS`, because a server is something CI
can provide deterministically and NCBI's rate limiter is not.

**If cargo cannot write to your `CARGO_HOME`** — a sandbox that permits writes
only inside this repository, which is the case in the environment this was
started in — point it at a directory inside the workspace:

```bash
CARGO_HOME="$PWD/.cargo-home" cargo test
```

The crate does not use it at runtime; it is only where cargo unpacks the
registry, and `.gitignore` covers it.

## Status

| | Python | Rust | State |
|---|---|---|---|
| `db/` | 787 lines, 5 files | 11 files | **ported** — SQLite always, PostgreSQL behind the optional `postgres` feature. 10 live tests against a real server, and a differential corpus (`tests/db_oracle.rs`, 37 cases) over splitting, dialect, values, tables, transactions and migrations |
| `citations/` | 1,129 lines, 4 files | 5 files | **ported**, 14 named tests + 93 oracle cases |
| `context_processor/` | 1,710 lines, 4 files | 3 files | **ported**, 20 named tests + 62 oracle cases. The rendering hooks (`format_item` / `format_consolidated_item`) are reached through `ItemRouting`; until round 46 nothing called them |
| `fulltext/jats_text` | 1,816 (reader) | 1 file | **ported** — whitespace, locator joining, LaTeX deposits, formula spacing. 12 named tests + 74 oracle cases |
| `fulltext/_parse_audit` | 345 lines | 1 file | **ported** — the unwind audit. 7 named tests + 42 oracle cases |
| `fulltext/_titles` | 289 lines | 1 file | **ported** — the PDF-title corroboration. 15 named tests + 74 oracle cases |
| `fulltext/models` | 915 lines | 1 file | **ported** — 14 structs, 2 enums; every field list diffed against Python's. 4 unit tests |
| `transparency/models` | 707 lines | 1 file | **ported** — 4 enums, both partitions named, `calculate_risk_level`. 9 named tests + 43 oracle cases |
| `fulltext/jats_parser` (reader) | 3,214 code | 1 file | **ported** — 68/68 oracle documents byte-for-byte; 11 QUIRKs recorded. 19 named tests + 68 oracle cases |
| `fulltext/segmenter` | 239 code | 1 file | **ported** — headings, classification, slicing. 10 named tests + 125 oracle cases |
| `_atomic`, `fulltext/cache` | 488 code | 2 files | **ported** — the atomic publish and the disk cache, plus the platform/home table `default_cache_dir` is built from. 20 named tests + 33 oracle cases + 7 unit tests |
| `http` | — | 1 file | **ported** — the real `HttpClient` over `ureq`; without it the library could not fetch. 9 tests against a local server |
| `fulltext/service` | 720 code | 1 file | **ported** — the tier chain, plus `render_jats_html`. 63 named tests + 71 oracle cases |
| `transparency/analyzer` | 1,071 code | 1 file | **ported** — the multi-API analysis. 28 named tests, 351 oracle cases, and `tests/funder_matching.rs` re-deriving the industry-funder matcher's stated counts |
| `fulltext/pdf_converter` (pure half) | 293 code | 1 file | **ported** — assembly rules behind a `PdfTextExtractor` trait. 13 named tests + 53 oracle cases |
| `fulltext/pdf_converter` (backend) | 293 code | 1 file | **ported** — `pdfium-render` behind the optional `pdf` feature, plus the `FullTextService` adapter. 8 tests against real PDFs |
| `llm/text_utils` | 364 lines | 1 file | **ported**, covered by the context oracle |
| `llm/json_repair` | 637 lines | 1 file | **ported** (fixes #299), 15 named tests + 64 oracle cases |
| `llm/utils` | 282 lines | 1 file | **ported**, covered by the JSON oracle |
| `llm/token_tracker` | 167 lines | 1 file | **ported** — process-wide accounting. 8 named tests |
| `templates/engine` | 189 lines | 1 file | **ported** — two-directory lookup and the atomic install. 11 named tests + 22 oracle cases. The Jinja2 subset is refused by name, not guessed |
| `context_processor/llm_processor` | 336 lines | 1 file | **ported** — the only part of the package that calls a model: the pure half, plus `LlmChunkProcessor` (the harness bound to a `ContextModel`) and `ChunkItem`. 17 named tests + 30 oracle cases |
| `quality/` LLM agents | 1,308 lines | 3 files | **ported** — Tier 2 classifier, Tier 3 agent, Tier 4 Cochrane assessor. 1,414-line test file |
| `llm/data_types`, `protocol` | 227 + 1,984 lines | 2 files | **ported** — messages, responses and both wire protocols' transforms |
| `llm/providers/*`, `llm/client` | 3,281 lines | 2 files | **ported** as one client over two protocols; a provider is a row of data |
| `agents/base`, `agents/metrics` | 843 lines | 2 files | **ported** — the retry/truncation loop and the metrics report (fixes #300) |
| `quality/` (LLM tiers) | 1,120 lines | 2 files | **ported** — the answer-reading rules (fixes #295), 15 named tests + 56 oracle cases (all strict since round 43) |
| `quality/metadata_filter`, `manager` | 461 lines | 2 files | **ported** — Tier 1's mapping, the tiering rule, the Cochrane enrichment. 12 named tests + 27 oracle cases |
| `quality/extractors` | 753 lines | 1 file | **ported** — a transcription of Python's own rule tables through `fancy-regex`, whose `is_denied` / `_find_power_mention` / `_find_ci_mention` are Python's. The port's three corrections (#294, #297, #298) are retired: Python's extractor audit adopted two and **refused the third**, and the window defect #366 found is fixed. 20 named tests + 575 oracle cases (three corrected) |
| `quality/scoring_models` | 140 lines | 1 file | **ported** |
| `quality/data_models` | 393 lines | 1 file | **ported**, 15 named tests + 53 oracle cases |
| `quality/cochrane_models` | 704 lines | 1 file | **ported** (fixes #310), 15 named tests + 65 oracle cases (all strict since round 43) |
| `quality/cochrane_formatter` | 380 lines | 1 file | **ported** (fixes #312), 16 named tests + 33 oracle cases (all strict since round 43) |
| `publications/models` | 867 lines | 1 file | **ported**, 22 named tests + 88 oracle cases |
| `publications/schema` | 347 lines | 1 file | **ported**, 9 tests (DDL diffed byte-for-byte) |
| `publications/storage` | 672 lines | 1 file | **ported**, 28 named tests + 38 oracle cases |
| `publications/retractions` | 735 lines | 1 file | **ported**, 27 named tests + 67 oracle cases |
| `publications/sync` | 1,219 lines | 1 file | **ported** — rules, storage helpers, the per-source/per-day loop and the day's part buffer. 53 named tests (11 `sync_source`, 8 `sync_credit`, 29 `sync_rules`, 5 in-module) + 108 oracle cases (87 `sync_cases`, 21 `sync_credit_cases`) |
| `publications/fetchers/_reconcile` | 170 lines | 1 file | **ported**, 17 named tests + 24 oracle cases |
| `publications/fetchers/registry` | 234 lines | 1 file | **ported** — the resume-keyword check is a compile-time matter here; `Fetcher::fetch` hands records to a `FetchSink` as they are read, so a caller can store one part at a time; `builtin_registry(client)` wires all four built-in sources to their fetchers |
| `publications/fetchers/biorxiv` | 371 lines | 1 file | **ported**, 24 named tests + 68 oracle cases (three `corrected`: #349's two and #361's) |
| `publications/fetchers/openalex` | 383 lines | 1 file | **ported** — 24 named tests + 59 oracle cases; #313's correction was retired when Python adopted it and #349's two plus #361's are the current `corrected` blocks |
| `publications/fetchers/pubmed` | 1,583 lines | 1 file | **ported** — reader, ladder, walk, part loop, transport, `fetch_pubmed`, and `PubMedFetcher` over them (6 named tests). 83 named tests + 148 oracle cases |
| `quality/` (pure half) | ~2,000 | — | |
| `llm/` (pure half) | ~1,280 | — | |
| `publications/` | 4,190 | — | |
| `fulltext/` | 11,855 | — | |
| `transparency/` | 4,439 | — | |

Phase order is in §8 of the port plan.

## What `db/` established

The Python module's transaction design was the piece expected to hurt most: it
keys a side table of open blocks by `(thread, id(conn))`, and the whole of
`publications/` is written against "pure functions take a connection, and work
whether or not a block is open".

It did not survive; it was **replaced by something smaller**. In Rust the type
of the value in hand answers "am I nested?" — a `Connection::begin()` opens a
transaction, a `Transaction::begin()` opens a savepoint — so `_depths`,
`_depth_key`, `_depths_lock`, `_is_nested()` and `transaction_depth()` all have
no counterpart. `owns_commit()` survives as a constant per implementation.

Three classes of silent-write-loss bug became *unrepresentable* rather than
merely fixed: reaching around an open block now fails to compile, pinned as a
`compile_fail` doc-test rather than asserted.

Two things the port pays for:

- **`Value`/`Row`/`DbError`** — ~170 lines replacing what Python's duck typing
  and exceptions gave free. This is a **fixed cost for the whole port**, not
  per module; `spikes/publications-rs` measured `storage.py` at parity once it
  was paid.
- **`Db` impls instead of one factory** — most of it delegation, and the four
  lines that differ are exactly the distinctions Python computed at runtime.
  SQLite needs three (connection, transaction, savepoint); the blocking
  `postgres` crate needs two, because its `Transaction` covers a savepoint too.

### One defect fixed in this port's own lineage

The Rust spike derived from `db/` ended a block comment by searching for the
first `*` **or** `/` and skipping two characters. That is correct for
`/* plain */` and wrong for `/* note / still comment */`, where it resumed
inside the comment and handed the comment's text to the driver as SQL. The
spike's 62 tests passed with it in place, because none of them put a `*` or a
`/` inside a block comment.

The Python original searches for the two-character sequence `*/`, and so does
this port. `tests/split.rs` carries eleven regression cases for it; four of
them fail if the defect is reintroduced.

### What the live PostgreSQL run found

Three defects, none of which any existing test could see — the PostgreSQL SQL
was written and reviewed, comfortably, while nothing could execute it.

- **A line continuation ate a space.** `publications/schema.rs`'s
  `existing_columns` wrote `"...information_schema.columns\` on one line and
  `" WHERE table_name = ? …"` on the next. In Rust a trailing backslash strips
  the continuation line's leading whitespace, so the fragments met as
  `columnsWHERE`; Python's adjacent literals keep the space. Every
  `ensure_schema` on PostgreSQL died with `syntax error at or near "="`. It is
  now `concat!`, and the three sibling SQL strings in `publications/storage.rs`
  that had lost their spaces the same way were fixed with it.
- **The driver's message was thrown away.** `postgres::Error`'s `Display` names
  only its *kind*; a rejected statement prints the bare string `"db error"` and
  the server's `ERROR`/`DETAIL`/`HINT` live in `source()`. `From<postgres::Error>
  for DbError` now walks the cause chain, so the first defect was legible at all.
- **A simulated connection cannot answer a catalog question.** `PgSim`'s
  `catalog_shim` rewrites `information_schema.tables` for `table_exists` but
  knows nothing about `information_schema.columns`, which is what hid the first
  defect.

## The differential oracle

Phase 0 of the port plan called for an instrument that compares the Rust port
against the **Python library itself**, rather than against a translator's
reading of its tests. The citations half exists now:

```
rust/oracle/dump_citations.py     runs cases through bmlib.citations -> JSON
rust/oracle/cases.json            93 cases
rust/oracle/dump_context.py       runs cases through bmlib.context_processor
rust/oracle/context_cases.json    62 cases
rust/oracle/dump_llm_processor.py the one part of that package that calls a model
rust/oracle/llm_processor_cases.json 30 cases, all diffed strictly
rust/oracle/dump_db.py            statement splitting, dialect spellings, values,
                                  tables, nested transactions and migrations
rust/oracle/db_cases.json         37 cases, one of them corrected
rust/oracle/dump_cost.py          process-wide token accounting
rust/oracle/cost_expected.json    no separate cases file; the dumper builds its own
rust/oracle/dump_json.py          runs cases through bmlib.llm.json_repair/utils
rust/oracle/json_cases.json       64 cases, all diffed strictly — #299's four
                                  corrections were retired when Python adopted
                                  the fix (see below)
rust/oracle/dump_quality.py       runs cases through bmlib.quality.extractors
rust/oracle/quality_cases.json    575 cases — #294's, #297's and #298's
                                  thirteen corrections were retired when
                                  Python's extractor audit landed, and three
                                  character-class divergences take their place
                                  (see below)
rust/oracle/dump_models.py        runs cases through bmlib.quality.data_models
rust/oracle/model_cases.json      53 cases, all diffed strictly
rust/oracle/dump_cochrane.py      runs cases through bmlib.quality.cochrane_models
rust/oracle/cochrane_cases.json   65 cases, all diffed strictly — #310's two
                                  corrections were retired when Python adopted
                                  the fix (see below)
rust/oracle/dump_cochrane_assessor.py  Tier 4's condensation and answer reading
rust/oracle/cochrane_assessor_cases.json 54 cases, all diffed strictly
rust/oracle/dump_formatter.py     runs cases through bmlib.quality.cochrane_formatter
rust/oracle/formatter_cases.json  33 cases, all diffed strictly — #312's four
                                  corrections were retired (see below)
rust/oracle/dump_pubmodels.py     runs cases through bmlib.publications.models
rust/oracle/pubmodels_cases.json  88 cases, 1 with a corrected expectation
rust/oracle/dump_schema.py        both publications DDLs
rust/oracle/dump_storage.py       the identifier/merge rules from storage.py
rust/oracle/storage_cases.json    38 cases, all diffed strictly
rust/oracle/dump_retractions.py   the retraction rules + the whole parse path
rust/oracle/retraction_cases.json 67 cases, all diffed strictly
rust/oracle/dump_retraction_store.py  storing notices, idempotently
rust/oracle/retraction_store_cases.json 14 cases, all diffed strictly
rust/oracle/dump_sync.py          the day-selection and durability rules
rust/oracle/sync_cases.json       86 cases, all diffed strictly — 11 for the
                                  settle period (#343)
rust/oracle/dump_fetchers.py      reconciliation + the built-in descriptors
rust/oracle/fetcher_cases.json    24 cases, all diffed strictly
rust/oracle/dump_biorxiv.py       normalization + the whole page walk
rust/oracle/biorxiv_cases.json    68 cases, three with corrected expectations
                                  (#349: a non-2xx's message wording; #361: a
                                  transport failure's name). Two more pin the
                                  container `repr` in a validator-style message
                                  (#365)
rust/oracle/dump_openalex.py      normalization, abstract rebuild, cursor walk
rust/oracle/openalex_cases.json   59 cases, three with corrected expectations
                                  — #313's correction was retired when Python
                                  adopted it, and #349's two plus #361's one are
                                  the current ones (see below)
rust/oracle/dump_pubmed.py        Markdown rendering + the whole XML reader
rust/oracle/pubmed_cases.json     80 cases, all diffed strictly
rust/oracle/dump_pubmed_walk.py   the EDAT ladder + the session walk
rust/oracle/pubmed_walk_cases.json 31 cases, all diffed strictly — four of them
                                  a `count_fn` that fails, one per probe site
                                  (#359)
rust/oracle/dump_pubmed_part.py   the per-part skip/reconcile/checkpoint rules
rust/oracle/pubmed_part_cases.json 21 cases, all diffed strictly
rust/oracle/dump_pubmed_transport.py  ESearch reading + the request shape
rust/oracle/pubmed_transport_cases.json 16 cases
rust/oracle/dump_sync_credit.py   the per-day credits, counts and error lines
rust/oracle/sync_credit_cases.json 21 cases, all diffed strictly
rust/oracle/dump_protocol.py      the OpenAI-side wire transforms
rust/oracle/protocol_cases.json   56 cases, all diffed strictly
rust/oracle/dump_quality_llm.py   the two quality agents' answer readers
rust/oracle/quality_llm_cases.json 56 cases, all diffed strictly — #295's
                                  corrections were retired (see below)
rust/oracle/dump_tiering.py       Tier 1's tables, as data, plus 27 cases
rust/oracle/tiering_cases.json    the priority walk and the unmapped types
rust/oracle/dump_jats_text.py     the JATS text primitives
rust/oracle/jats_text_cases.json  74 cases, all diffed strictly
rust/oracle/dump_parse_audit.py   the unwind audit, every field alone
rust/oracle/parse_audit_cases.json 42 cases, all diffed strictly
rust/oracle/dump_titles.py        PDF-title corroboration + normalisation
rust/oracle/titles_cases.json     74 cases, all diffed strictly
rust/oracle/dump_transparency.py  the enum partitions and the risk rule
rust/oracle/transparency_cases.json 43 cases, all diffed strictly
rust/oracle/dump_analyzer.py      the multi-API analysis itself
rust/oracle/analyzer_cases.json   351 cases, all diffed strictly
rust/oracle/dump_result_dict.py   TransparencyResult's persistence path
rust/oracle/result_dict_cases.json 27 cases, all diffed strictly
rust/oracle/dump_funder_matcher.py  industry-funder matching over the labelled corpus
rust/bmlib/tests/data/funder_matcher_expected.json 417 names, 407 of them scoring
                                  — re-derived in Rust by
                                  rust/bmlib/tests/funder_matching.rs, which reads
                                  the stated counts out of the module's own source
rust/oracle/dump_jats.py          the JATS reader, over 68 whole articles
rust/oracle/jats_cases.json       the corpus the reader port targets
rust/oracle/dump_segmenter.py     the PDF section segmenter
rust/oracle/segmenter_cases.json  125 cases, all diffed strictly
rust/oracle/dump_pdf_text.py      PDF line/span assembly behind the PdfTextExtractor trait
rust/oracle/pdf_text_cases.json   53 cases, all diffed strictly
rust/oracle/dump_cache.py         cache-filename sanitisation
rust/oracle/cache_cases.json      33 cases, all diffed strictly
rust/oracle/dump_service.py       the full-text tier chain's helpers
rust/bmlib/tests/data/service_cases.json 67 cases, all diffed strictly
                                  (this corpus has no `rust/oracle/` copy)
rust/oracle/dump_templates.py     the two-directory lookup and the refused Jinja2 subset
rust/oracle/templates_cases.json  22 cases, all diffed strictly
rust/bmlib/tests/data/*.json      the cases and the Python results, committed
rust/bmlib/tests/citations_oracle.rs   runs each case in Rust and diffs
rust/bmlib/tests/context_oracle.rs     the same, for the context processor
```

Phase 4 is nearly complete; only `pdf_converter` and the `_titles`-adjacent odds remain.

Regenerate the expectations (from the repository root):

```bash
.venv/bin/python rust/oracle/dump_citations.py < rust/oracle/cases.json \
    > rust/bmlib/tests/data/citations_expected.json
```

Both files are committed, so the test needs no Python to run. Values are
compared **parsed**, not as text — key order and whitespace cannot matter, which
is what "functionally equivalent" means here.

**It earned its place immediately, twice.** The ported citations code failed
one of 93 cases on the first run: Harvard renders `"John A. Smith"` as `"Smith, J.A."`,
and a natural-name initial builder written as a `String` collect produced
`"Smith, JA."` — dropping the middle initial's period, on every author who has
one. No ported test covered it, because a translated test encodes the
translator's reading. The fix is in `formatter.rs::surname_and_initials_run`,
whose doc-comment now states the three styles' differing separators.

The quality corpus used to exercise the mechanism hardest: **13** of its 76
cases were corrections, across three separate issues (#294 the digit-grouped
sample size, #297 the negation-blind bonuses, #298 priority over evidence).
**Python's extractor audit measured all three on a 5,976-abstract Europe PMC
draw and decided each one**, so all thirteen are retired and the corpus — 575
cases now — diffs strictly except for three measured character-class divergences:
#294 was adopted outright, #297 was
replaced by a narrower denial model that refuses 16 fewer genuine CI reports,
and **#298's veto was refused**, because it moved 55 study-type answers over the
draw and none for the better. The companion test names the fourteen cases Python
decided and requires the only `corrected` blocks left to be the three
character-class ones.

**A transcription inherits the engine's character classes.** The tables are
Python's text compiled by `fancy-regex`, so Rust's `\w` (`[\p{Alphabetic}\p{M}
\p{Nd}\p{Pc}\p{Join_Control}]`) stands where Python's (`[\p{Alphabetic}\p{Nd}
\p{Nl}\p{No}_]`) does, and Rust's `\s` (`\p{White_Space}`) where Python's
`str.isspace()` also holds `U+001C`-`U+001F`. The difference needs a combining
mark abutting a keyword, or a file separator inside a denial, so three cases pin
it and the port plan's §9 carries the row and the reason the rewrite was
declined.

The JSON corpus used to need a mechanism the other two did not. Because the port
targets a **corrected** bmlib, on the defects it fixes the oracle *must* disagree
with Python — and a corpus that simply pinned the corrected output would hide
that, leaving the next porter unable to tell an intentional fix from a mistake.
So a case may carry a `corrected` block: the value the port must produce, the
reason, and the issue number; the test then asserts that Python still says what
the corpus records, that Rust produces the corrected value, and that the two
genuinely differ. **A corpus whose defect Python adopts must retire its blocks**
— and a stale one is worse than none, since its "Python says something else"
assertion passes only while nobody regenerates the expectations. `json`'s four
#299 cases and `protocol`'s #315 one were retired that way in round 41; in round
43 Python's quality-narrowing batch (`07335c1`, `d4a82a0`) adopted #295, #310,
#312 and #317–#320, so `cochrane`, `cochrane_assessor`, `formatter` and
`quality_llm` retired all 22 of theirs and now diff strictly; round 59
retired `quality_cases.json`'s thirteen; and round 63 retired `cache`'s
`safe_filename/161`, which Python's PR #355 adopted by moving the pass-through
bound to `_MAX_KEY_CHARS`. The mechanism is still used by every corpus whose
defect Python has not adopted — the `fetch/http-error` and
`fetch/transport-error` cases — and each such corpus has a companion test
asserting which cases carry one, so a correction cannot be quietly attached to
an unrelated input. `cache` now carries none, and its companion test asserts
that.

**Re-running every dumper is mechanised**, because it is the check that makes the
corpora evidence rather than fixtures and it has now found stale ones twice:

```bash
.venv/bin/python scripts/rerun_rust_oracle.py            # report drift
.venv/bin/python scripts/rerun_rust_oracle.py --write    # regenerate in place
```

It diffs each dumper's output against the committed expectation **parsed**, and
it also asserts that a corpus keeping two copies of its cases (`oracle/` for the
dumper, `tests/data/` for `include_str!`) has not let them drift apart. Run it
before believing anything about this port.

It also found three real fidelity gaps in the port itself, all in the
empty-input path: I had reused one `Empty` error for three call sites that
Python words differently, and — more substantively — returned "No JSON found"
where Python names the parse failure. Those were fixed in the port, not
enshrined in the corpus.

The context corpus found a second, smaller divergence on its first run: the
configuration error for an out-of-range `min_confidence_threshold` omitted the
offending value, where Python's message names it. That one is cosmetic — but it
is exactly the class a translator would not notice, because the message reads
correctly either way.

## Not yet done

- **The PostgreSQL backend is live-tested, and CI provides the server.**
  `db/postgres.rs` is a real driver behind the `postgres` feature, and
  `tests/postgres_live.rs` runs ten tests against an actual server — operations,
  booleans, `SERIAL` + `RETURNING id`, nested savepoints, migrations, the
  publications schema, child reparenting. The `rust-postgres` job in
  `.github/workflows/ci.yml` runs it over a `postgres:16` service, so it *is* a
  gate; the suite's own `BMLIB_PG_TESTS` gate is what keeps a plain `cargo test`
  from opening a socket. `tests/dialect.rs` keeps the same dialect-rule coverage
  ungated, through the simulated connection (`tests/common/pg_sim.rs`), for a
  machine with no server.
- **A source's *live* path is composed but not run end to end.** `builtin_registry`
  wires all four descriptors to concrete fetchers over one HTTP client, and each
  half is tested: `live_network.rs` reaches the real bioRxiv, PubMed E-utilities
  and OpenAlex endpoints through the transports, and the fetcher layer is tested
  over scripted transports. What no test does is call `sync()` against a *live*
  source through `builtin_registry` — deliberately, since that would write to a
  database from a test that cannot be run offline.
- **`db/`'s corpus diffs SQLite, not PostgreSQL.** `tests/db_oracle.rs` and
  `rust/oracle/dump_db.py` compare statement splitting, the dialect spellings, the
  value shapes a fetch returns, table existence, migrations and — the part that
  matters most — what a nested `transaction` block commits and rolls back. The
  PostgreSQL side is not diffed: Python would need a server to answer at all, and
  the layer's dialect-specific surface is the placeholder spelling, which the same
  corpus covers on the SQLite side and `tests/dialect.rs` covers on both.
- **No PostgreSQL TLS.** `connect` and `connect_params` use `NoTls`, matching
  the Python `psycopg2.connect` call, which does not enable TLS unless the DSN
  asks. A caller who needs it builds a `postgres::Config`; every `Db` method is
  implemented on `postgres::Client`, so such a client drops straight in.
