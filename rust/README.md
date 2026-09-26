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
    │   ├── context_processor/  port of bmlib/context_processor/ (1,710 lines)
    │   │   ├── base.rs         batching, recursion, consolidation
    │   │   ├── data_types.rs   config, results, strategies, status
    │   │   └── mod.rs
    │   ├── fulltext/   port of bmlib/fulltext/ (reader in flight)
    │   ├── transparency/  port of bmlib/transparency/ (models done)
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
    │   │   │   ├── openalex.rs   OpenAlex cursor walker (fixes #313)
    │   │   │   └── pubmed.rs     the PubmedArticle XML reader
    │   │   ├── csv.rs            CSV with Python's physical `line_num`
    │   │   └── mod.rs
    │   ├── quality/    port of bmlib/quality/ (pure half)
    │   │   ├── cochrane_formatter.rs  Markdown + HTML renderers (fixes #312)
    │   │   ├── cochrane_models.rs  nine-domain RoB + study characteristics
    │   │   ├── data_models.rs    StudyDesign, QualityTier, QualityAssessment, QualityFilter
    │   │   ├── extractors.rs     rule-based study-type / sample-size (fixes #294, #297, #298)
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
    │       ├── split.rs         multi-statement SQL splitting
    │       ├── sqlite.rs        the three Db impls
    │       ├── traits.rs        the Db trait
    │       ├── transactions.rs  composable savepoints
    │       └── value.rs         Value, Row — the boundary types
    └── tests/
        ├── common/     both_backends! macro + the pg_sim harness
        ├── data/       differential-oracle fixtures (vendored)
        └── *.rs        one file per ported Python test module
```

## Running it

```bash
cd rust
cargo test                                   # 802 tests + 3 doc-tests
cargo clippy --all-targets                   # expected clean
cargo fmt --check

# The PDFium backend tests, which need a downloaded library
cargo test --features pdf

# The live tests, which make real requests and are **skipped unless the
# variable is set** — the default `cargo test` opens no socket (0.16s).
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1
```

`--test-threads=1` matters for the live tests: **NCBI rate-limits by source
address**, so a concurrent run draws 429s that read as parse failures.

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
| `db/` | 787 lines, 5 files | 10 files | **ported**, clippy+fmt clean |
| `citations/` | 1,129 lines, 4 files | 5 files | **ported**, 14 named tests + 93 oracle cases |
| `context_processor/` | 1,710 lines, 4 files | 3 files | **ported**, 16 named tests + 62 oracle cases (base + data_types; `llm_processor` follows `llm`) |
| `fulltext/jats_text` | 1,816 (reader) | 1 file | **ported** — whitespace, locator joining, LaTeX deposits, formula spacing. 12 named tests + 74 oracle cases |
| `fulltext/_parse_audit` | 345 lines | 1 file | **ported** — the unwind audit. 7 named tests + 42 oracle cases |
| `fulltext/_titles` | 289 lines | 1 file | **ported** — the PDF-title corroboration. 15 named tests + 74 oracle cases |
| `fulltext/models` | 915 lines | 1 file | **ported** — 14 structs, 2 enums; every field list diffed against Python's. 4 unit tests |
| `transparency/models` | 707 lines | 1 file | **ported** — 4 enums, both partitions named, `calculate_risk_level`. 9 named tests + 43 oracle cases |
| `fulltext/jats_parser` (reader) | 1,816 code | 1 file | **ported** — 18/18 oracle documents byte-for-byte; 8 QUIRKs recorded. 5 named tests + 18 oracle cases |
| `fulltext/segmenter` | 239 code | 1 file | **ported** — headings, classification, slicing. 10 named tests + 125 oracle cases |
| `_atomic`, `fulltext/cache` | 488 code | 2 files | **ported** — the atomic publish and the disk cache. 11 named tests + 31 oracle cases |
| `http` | — | 1 file | **ported** — the real `HttpClient` over `ureq`; without it the library could not fetch. 9 tests against a local server |
| `fulltext/service` | 720 code | 1 file | **ported** — the tier chain, plus `render_jats_html`. 40 named tests + 67 oracle cases |
| `transparency/analyzer` | 1,071 code | 1 file | **ported** — the multi-API analysis. 27 named tests |
| `fulltext/pdf_converter` (pure half) | 293 code | 1 file | **ported** — assembly rules behind a `PdfTextExtractor` trait. 13 named tests + 53 oracle cases |
| `fulltext/pdf_converter` (backend) | 293 code | 1 file | **ported** — `pdfium-render` behind the optional `pdf` feature, plus the `FullTextService` adapter. 8 tests against real PDFs |
| `http` | — | 1 file | **ported** — the real `HttpClient` over `ureq`; without it the library could not fetch. 9 tests against a local server |
| `fulltext/service` | 720 code | 1 file | **ported** — the tier chain. 30 named tests |
| `transparency/analyzer` | 1,071 code | 1 file | **ported** — the multi-API analysis. 27 named tests |
| `fulltext/pdf_converter` (pure half) | 293 code | 1 file | **ported** — assembly rules behind a `PdfTextExtractor` trait. 13 named tests + 53 oracle cases |
| `fulltext/pdf_converter` (backend) | 293 code | 1 file | **ported** — `pdfium-render` behind the optional `pdf` feature, plus the `FullTextService` adapter. 8 tests against real PDFs |
| `llm/text_utils` | 364 lines | 1 file | **ported**, covered by the context oracle |
| `llm/json_repair` | 637 lines | 1 file | **ported** (fixes #299), 15 named tests + 64 oracle cases |
| `llm/utils` | 282 lines | 1 file | **ported**, covered by the JSON oracle |
| `llm/token_tracker` | 167 lines | 1 file | **ported** — process-wide accounting. 8 named tests |
| `templates/engine` | 189 lines | 1 file | **ported** — two-directory lookup and the atomic install. 11 named tests + 22 oracle cases. The Jinja2 subset is refused by name, not guessed |
| `context_processor/llm_processor` | 336 lines | 1 file | **ported** — the only part of the package that calls a model. 12 named tests + 30 oracle cases |
| `quality/` LLM agents | 1,308 lines | 3 files | **ported** — Tier 2 classifier, Tier 3 agent, Tier 4 Cochrane assessor. 1,414-line test file |
| `llm/data_types`, `protocol` | 227 + 1,984 lines | 2 files | **ported** — messages, responses and both wire protocols' transforms |
| `llm/providers/*`, `llm/client` | 3,281 lines | 2 files | **ported** as one client over two protocols; a provider is a row of data |
| `agents/base`, `agents/metrics` | 843 lines | 2 files | **ported** — the retry/truncation loop and the metrics report (fixes #300) |
| `quality/` (LLM tiers) | 1,120 lines | 2 files | **ported** — the answer-reading rules (fixes #295), 13 named tests + 45 oracle cases |
| `quality/metadata_filter`, `manager` | 461 lines | 2 files | **ported** — Tier 1's mapping, the tiering rule, the Cochrane enrichment. 12 named tests + 27 oracle cases |
| `quality/extractors` | 487 lines | 1 file | **ported** (fixes #294, #297, #298), 16 named tests + 76 oracle cases |
| `quality/scoring_models` | 140 lines | 1 file | **ported** |
| `quality/data_models` | 393 lines | 1 file | **ported**, 15 named tests + 53 oracle cases |
| `quality/cochrane_models` | 704 lines | 1 file | **ported**, 15 named tests + 49 oracle cases |
| `quality/cochrane_formatter` | 380 lines | 1 file | **ported** (fixes #312), 16 named tests + 33 oracle cases |
| `publications/models` | 867 lines | 1 file | **ported**, 22 named tests + 88 oracle cases |
| `publications/schema` | 347 lines | 1 file | **ported**, 9 tests (DDL diffed byte-for-byte) |
| `publications/storage` | 672 lines | 1 file | **ported**, 28 named tests + 38 oracle cases |
| `publications/retractions` | 735 lines | 1 file | **ported**, 27 named tests + 67 oracle cases |
| `publications/sync` | 1,219 lines | 1 file | **ported** — rules, storage helpers and the per-source/per-day loop. 38 named tests + 96 oracle cases |
| `publications/fetchers/_reconcile` | 170 lines | 1 file | **ported**, 17 named tests + 24 oracle cases |
| `publications/fetchers/registry` | 234 lines | 1 file | **ported** — the resume-keyword check is a compile-time matter here |
| `publications/fetchers/biorxiv` | 277 lines | 1 file | **ported**, 19 named tests + 43 oracle cases |
| `publications/fetchers/openalex` | 383 lines | 1 file | **ported** (fixes #313), 21 named tests + 57 oracle cases |
| `publications/fetchers/pubmed` | 1,583 lines | 1 file | **ported** — reader, ladder, walk, part loop, transport, `fetch_pubmed`. 74 named tests + 144 oracle cases |
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
- **Three `Db` impls instead of one factory** — most of it delegation, and the
  four lines that differ are exactly the distinctions Python computed at
  runtime.

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

## The differential oracle

Phase 0 of the port plan called for an instrument that compares the Rust port
against the **Python library itself**, rather than against a translator's
reading of its tests. The citations half exists now:

```
rust/oracle/dump_citations.py     runs cases through bmlib.citations -> JSON
rust/oracle/cases.json            93 cases
rust/oracle/dump_context.py       runs cases through bmlib.context_processor
rust/oracle/context_cases.json    62 cases
rust/oracle/dump_json.py          runs cases through bmlib.llm.json_repair/utils
rust/oracle/json_cases.json       64 cases, 4 with corrected expectations
rust/oracle/dump_quality.py       runs cases through bmlib.quality.extractors
rust/oracle/quality_cases.json    76 cases, 13 with corrected expectations
rust/oracle/dump_models.py        runs cases through bmlib.quality.data_models
rust/oracle/model_cases.json      53 cases, all diffed strictly
rust/oracle/dump_cochrane.py      runs cases through bmlib.quality.cochrane_models
rust/oracle/cochrane_cases.json   49 cases, all diffed strictly
rust/oracle/dump_formatter.py     runs cases through bmlib.quality.cochrane_formatter
rust/oracle/formatter_cases.json  33 cases, 4 with corrected expectations
rust/oracle/dump_pubmodels.py     runs cases through bmlib.publications.models
rust/oracle/pubmodels_cases.json  88 cases, 1 with a corrected expectation
rust/oracle/dump_schema.py        both publications DDLs
rust/oracle/dump_storage.py       the identifier/merge rules from storage.py
rust/oracle/storage_cases.json    38 cases, all diffed strictly
rust/oracle/dump_retractions.py   the retraction rules + the whole parse path
rust/oracle/retraction_cases.json 67 cases, all diffed strictly
rust/oracle/dump_sync.py          the day-selection and durability rules
rust/oracle/sync_cases.json       75 cases, all diffed strictly
rust/oracle/dump_fetchers.py      reconciliation + the built-in descriptors
rust/oracle/fetcher_cases.json    24 cases, all diffed strictly
rust/oracle/dump_biorxiv.py       normalization + the whole page walk
rust/oracle/biorxiv_cases.json    43 cases, all diffed strictly
rust/oracle/dump_openalex.py      normalization, abstract rebuild, cursor walk
rust/oracle/openalex_cases.json   57 cases, 1 with a corrected expectation
rust/oracle/dump_pubmed.py        Markdown rendering + the whole XML reader
rust/oracle/pubmed_cases.json     80 cases, all diffed strictly
rust/oracle/dump_pubmed_walk.py   the EDAT ladder + the session walk
rust/oracle/pubmed_walk_cases.json 27 cases, all diffed strictly
rust/oracle/dump_pubmed_part.py   the per-part skip/reconcile/checkpoint rules
rust/oracle/pubmed_part_cases.json 21 cases, all diffed strictly
rust/oracle/dump_pubmed_transport.py  ESearch reading + the request shape
rust/oracle/pubmed_transport_cases.json 16 cases
rust/oracle/dump_sync_credit.py   the per-day credits, counts and error lines
rust/oracle/sync_credit_cases.json 21 cases, all diffed strictly
rust/oracle/dump_protocol.py      the OpenAI-side wire transforms
rust/oracle/protocol_cases.json   56 cases, all diffed strictly
rust/oracle/dump_quality_llm.py   the two quality agents' answer readers
rust/oracle/quality_llm_cases.json 45 cases (7 corrected, #295)
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
rust/oracle/dump_jats.py          the JATS reader, over 18 whole articles
rust/oracle/jats_cases.json       the corpus the reader port targets
rust/oracle/dump_segmenter.py     the PDF section segmenter
rust/oracle/segmenter_cases.json  125 cases, all diffed strictly
rust/oracle/dump_cache.py         cache-filename sanitisation
rust/oracle/cache_cases.json      31 cases, all diffed strictly
rust/oracle/dump_service.py       the full-text tier chain's helpers
rust/oracle/service_cases.json    67 cases, all diffed strictly
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

The quality corpus exercises the mechanism hardest: **13** of its 76 cases are
corrections, across three separate issues (#294 the digit-grouped sample size,
#297 the negation-blind bonuses, #298 priority over evidence). A companion test
asserts there are exactly thirteen, that they cite exactly those three issues,
and that each is named for the issue it cites — so a correction cannot be
quietly attached to an unrelated input.

The JSON corpus needed a mechanism the other two did not. Because the port
targets a **corrected** bmlib, on the defects it fixes the oracle *must*
disagree with Python — and a corpus that simply pinned the corrected output
would hide that, leaving the next porter unable to tell an intentional fix from
a mistake. So a case may carry a `corrected` block: the value the port must
produce, the reason, and the issue number. The test then asserts that Python
still says what the corpus records, that Rust produces the corrected value, and
that the two genuinely differ. Four cases carry one, all #299. A companion test
asserts there are exactly four and that each cites #299, so the mechanism cannot
be quietly attached to an unrelated case.

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

- **No PostgreSQL backend.** `Dialect::Postgres` exists and the numbered
  placeholder rewriting is exercised, but no statement has been run against a
  real server. `RETURNING id` — the one irreducibly dialect-specific need — is
  not implemented.
- **The oracle covers `citations/`, `context_processor/`, the pure `llm/` half
  `quality/` in full, and `publications/models` + `storage`.** `db/` has none,
  and the LLM tiers have none yet. Extending it is part of porting each one.
- **`run_migrations` is untested against PostgreSQL DDL** (`NOW()` is not
  SQLite-parseable).
