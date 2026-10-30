# bmlib (Rust) — Manual

**Crate version 0.2.0** | **License: AGPL-3.0-or-later** | **MSRV 1.85 (edition 2021)**

The Rust port of the Python [`bmlib`](../../../../docs/manual/index.md): a
database abstraction over SQLite and PostgreSQL, citation-marker parsing and
reference formatting, PubMed / bioRxiv / medRxiv / OpenAlex ingestion with
deduplication, retraction lookup, JATS XML, PDF and HTML full-text handling,
canonical text, a tiered study-quality assessment pipeline, transparency
analysis, in-process embedding, and an LLM client with streaming and
malformed-JSON repair.

This manual is the usage guide: what each module is for, how its pieces fit
together, and where it differs from the Python library. The item-level
reference is the rustdoc (`cargo doc -p bmlib --open`), which every public
item carries. Every `rust` block in these pages is compiled and run as a
doc-test, so an example that stops matching the API fails `cargo test`.

## Install

```bash
cargo add bmlib
cargo add bmlib --features pdf,html,postgres,embed
# A build that owns its storage and its transport (the mobile apps, through
# rust/bmlib-ffi): neither SQLite's C build nor rustls/ring is compiled.
cargo add bmlib --no-default-features
```

### Features

| Feature | Default | Adds | Enables |
|---|---|---|---|
| `sqlite` | on | `rusqlite` (SQLite bundled) | `db::open_memory`, `db::open_path`, the `Db` impl for `rusqlite::Connection` |
| `http-ureq` | on | `ureq` | `bmlib::http::UreqClient`, the real `HttpClient`. Without it a caller injects its own transport |
| `pdf` | off | `mupdf`, `mupdf-sys` | PDF → text with MuPDF, the engine PyMuPDF binds. Compiled from source and linked statically (a C compiler and libclang) |
| `html` | off | `scraper`, `ego-tree` | `fulltext::canonical`'s HTML → canonical text (html5ever, as Python's `html` extra) |
| `postgres` | off | `postgres` | `db::connect_postgresql`. `Dialect::Postgres`, the placeholder rewriter and the PostgreSQL DDL are always present |
| `embed` | off | `llama-cpp-2` | `embed::Embedder` and model verification; the download also needs `http-ureq`. Builds llama.cpp (cmake and a C++ compiler) |
| `embed-metal` | off | `embed` + llama.cpp's Metal backend | Names Metal explicitly; on macOS `embed` already uses it |

The constants and pure rules of every module (the embedding vector rules, the
canonical-text model, the JSON repair, the citation formatter) need no feature.

## Modules

| Module | Contents | Python page | Rust page |
|---|---|---|---|
| `db` | `Db` trait over SQLite and PostgreSQL, one `Row`/`Value` type, savepoint-nesting transactions, migrations, placeholder rewriting | [database.md](../../../../docs/manual/database.md) | [db.md](db.md) |
| `citations` | `[@id:N:Label]` markers, Vancouver / APA / Harvard / Chicago, reference lists, `find_passage` | [citations.md](../../../../docs/manual/citations.md) | — |
| `context_processor` | Hierarchical map-reduce over content larger than one context window | [context_processor.md](../../../../docs/manual/context_processor.md) | — |
| `fulltext` | Tiered retrieval, JATS parsing and rendering, PDF → text, section segmentation, disk cache, canonical text and chunks | [fulltext.md](../../../../docs/manual/fulltext.md) | — |
| `publications` | Fetchers, dedup-by-DOI/PMID storage, date-range sync, Retraction Watch, `RetractionIndex`, retraction status | [publications.md](../../../../docs/manual/publications.md) | — |
| `quality` | Metadata classification, LLM classifier, deep assessment, Cochrane risk of bias, rule-based extractors | [quality.md](../../../../docs/manual/quality.md) | — |
| `transparency` | CrossRef / Europe PMC / PubMed / OpenAlex / ClinicalTrials.gov → a 0–100 transparency score | [transparency.md](../../../../docs/manual/transparency.md) | — |
| `llm` | `LlmClient` over the OpenAI-compatible, Anthropic and Ollama wire protocols; streaming; context windows; JSON repair; chunking; token tracking | [llm.md](../../../../docs/manual/llm.md) | — |
| `agents` | `BaseAgent`'s retry/truncation loop and per-agent metrics | [agents.md](../../../../docs/manual/agents.md) | — |
| `embed` | arctic-embed-l-v2.0 GGUF in process, pinned and verified model files, vector rules, a paper's chunks stored and searched | [embeddings.md](../../../../docs/manual/embeddings.md) | [embed.md](embed.md) |
| `templates` | `{{ variable }}` prompt templates with a user/default directory lookup | [templates.md](../../../../docs/manual/templates.md) | — |
| `http` | `UreqClient`, the one implementation of `HttpClient` that goes out | — | — |
| `atomic` | `atomic_write`: publish a file so no partial version is visible | — | — |

A dash in the last column is a page not yet written; until it is, the
module's rustdoc and the Python page are the reference.

## Conventions across the crate

**Same names, Rust spelling.** A Python function keeps its name
(`fetch_scalar`, `find_passage`, `chunk_canonical`); a type keeps its name
where it was already a type (`LLMMessage`, `CanonicalText`). Where Python
returned `None` for "absent", Rust returns `Option`; where Python raised,
Rust returns `Result` with one error enum per module (`DbError`,
`ChatError`, `FetchError`, `EmbedError`, …). Those enums that may grow are
`#[non_exhaustive]`, so match them with a wildcard arm.

**Synchronous, with the transport injected.** Nothing is `async`. Every
fetcher, the LLM client and the transparency analyzer take an
`HttpClient` (`bmlib::publications::fetchers::HttpClient`) rather than
building one, which is what lets them be tested without a network:
`bmlib::http::UreqClient` is the real one, and a test or a host application
(the mobile apps, through `rust/bmlib-ffi`) supplies its own. Likewise every
database function takes `&mut dyn Db`.

**Equivalent to a corrected Python.** Where the Python library is wrong, the
port implements the intended behaviour and says so in the module that does.
What is deliberately *kept* is the Python repository's
[`docs/DECISIONS.md`](../../../../docs/DECISIONS.md), the register of
investigated non-fixes: those are specification, not defects.

**Held to Python by tests, not by reading.** Two mechanisms:

- *Shared contract fixtures* under the repository's `tests/fixtures/`
  (`canonical_text.json`, `passage_match.json`, `retraction_status.json`,
  `embedding_contract.json`, …), asserted by both test suites.
- *The differential oracle*: corpora under `rust/oracle/` and
  `rust/bmlib/tests/data/`, regenerated from the live Python library by the
  `oracle/dump_*.py` drivers (`scripts/rerun_rust_oracle.py`), which the Rust
  tests must match case by case. `db` has no oracle; its rules are pinned by
  named tests run under both dialects.

## Testing

```bash
cargo test                      # opens no socket
cargo test --features pdf       # + the MuPDF tests and the PDF-bytes contract
BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1
```

The PostgreSQL, network and embedder suites are gated on an environment
variable and return immediately without it. The live suite needs
`--test-threads=1`: NCBI rate-limits by source address. `rust/README.md` has
the full list, including the embedder and the end-to-end sync run.
