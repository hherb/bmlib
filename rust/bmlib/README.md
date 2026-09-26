# bmlib

Shared library for biomedical literature tools — a Rust port of the Python
[`bmlib`](https://github.com/hherb/bmlib).

It covers the plumbing a biomedical literature tool needs and would otherwise
rewrite: a database abstraction over SQLite and PostgreSQL, citation-marker
parsing and reference formatting, PubMed/bioRxiv/OpenAlex ingestion with
deduplication, retraction lookup, JATS XML and PDF full-text handling, a
tiered study-quality assessment pipeline, and a small LLM client with
malformed-JSON repair and prompt templates.

## Install

```bash
cargo add bmlib
```

Two optional features, both off by default so a caller that needs neither
pulls in neither:

| Feature | Adds | Note |
|---|---|---|
| `pdf` | `pdfium-render` + `pdfium-bundled` | PDF → text. Downloads a prebuilt PDFium binary at build time; this is the crate's one non-Rust dependency. |
| `postgres` | `postgres` | A real PostgreSQL backend. `Dialect::Postgres`, the placeholder rewriter and the PostgreSQL DDL are always present — this adds the driver that can run a statement against a server. |

```bash
cargo add bmlib --features pdf,postgres
```

## Quick start

An in-memory database, using the same `execute` / `fetch_scalar` functions on
either backend:

```rust
use bmlib::db::{execute, fetch_scalar, open_memory};
use bmlib::params;
use bmlib::Value;

fn main() -> Result<(), bmlib::db::DbError> {
    let mut db = open_memory()?;
    execute(
        &mut db,
        "CREATE TABLE paper (id INTEGER PRIMARY KEY, doi TEXT, pmid TEXT)",
        &[],
    )?;
    execute(
        &mut db,
        "INSERT INTO paper (doi, pmid) VALUES (?, ?)",
        &params!["10.1101/2020.01.01.000001", "31912345"],
    )?;

    let pmid = fetch_scalar(&mut db, "SELECT pmid FROM paper WHERE id = ?", &params![1])?;
    assert_eq!(pmid, Some(Value::Text("31912345".into())));
    Ok(())
}
```

`params!` is exported at the crate root, not under `db`.

Models emit JSON that is not JSON — single quotes, trailing commas, a response
cut off mid-object. `llm::json_repair` repairs it rather than failing the call:

```rust
use bmlib::llm::json_repair::{repair_json_default, RepairError};

fn main() -> Result<(), RepairError> {
    let repaired = repair_json_default("{'title': 'A study', 'year': 2024,}")?;
    assert_eq!(repaired, r#"{"title": "A study", "year": 2024}"#);
    Ok(())
}
```

## What is in it

| Module | Contents |
|---|---|
| `db` | Dialects, placeholder rewriting, transactions and nested savepoints, migrations, multi-statement splitting. SQLite always; PostgreSQL behind the feature. |
| `citations` | `[@id:12345:Smith2023]` marker parsing, Vancouver / APA / Harvard / Chicago formatting, reference lists, missing-document placeholders. |
| `context_processor` | Hierarchical map-reduce over content too large for one context window. |
| `fulltext` | Tiered retrieval, JATS XML parsing, section segmentation, a disk cache, and PDF → text behind the `pdf` feature. |
| `publications` | PubMed / bioRxiv / medRxiv / OpenAlex fetchers, dedup-by-DOI/PMID with merge-on-upsert, date-range sync, Retraction Watch lookup. |
| `quality` | Four tiers — free metadata classification, an LLM classifier, a deep assessment agent, and a Cochrane-aligned nine-domain risk-of-bias assessment. |
| `transparency` | CrossRef / Europe PMC / PubMed / OpenAlex / ClinicalTrials.gov analysis producing a 0–100 transparency score. |
| `llm` | One client over two wire protocols, `json_repair`, `text_utils` chunking, a process-wide token tracker. |
| `agents` | The retry/truncation loop for LLM-driven tasks, plus per-agent metrics. |
| `templates` | A Jinja2-subset template engine with a two-directory lookup and an atomic install. |
| `http` | The `HttpClient` trait and its `ureq` implementation; every fetcher is written against the trait. |
| `atomic` | `atomic_write` — publish a file so no partial version is ever visible. |

## Relationship to the Python library

Functionally equivalent to the Python `bmlib`, **and equivalent to a corrected
version of it**: where the Python library is wrong, this implements the
intended behaviour rather than reproducing the defect. Sixteen defects found
while planning the port are fixed here, and each Rust module that fixes one
says so where it does.

What is deliberately *kept* is the Python repository's
[`docs/DECISIONS.md`](https://github.com/hherb/bmlib/blob/main/docs/DECISIONS.md)
— its register of investigated non-fixes. Those are the specification, not a
defect list, so a clean-up here never changes one.

The port is verified against the Python implementation rather than by
inspection: the committed corpora under the repository's `rust/oracle/` and
`rust/bmlib/tests/data/` are regenerated from the live Python library by the
`oracle/dump_*.py` drivers, and the Rust tests must match them case by case.

## Testing

```bash
cargo test                      # 824 tests + 3 doc-tests; opens no socket
cargo test --features pdf       # + 8 PDFium tests over real PDFs
BMLIB_PG_TESTS=1 cargo test --features postgres --test postgres_live
BMLIB_LIVE_TESTS=1 cargo test --test live_network -- --test-threads=1
```

The PostgreSQL and network suites are **gated** and skip without their
environment variable, so a plain `cargo test` opens no socket. The live suite
needs `--test-threads=1`: NCBI rate-limits by source address, and a concurrent
run draws 429s that read as parse failures.

## Minimum supported Rust version

1.85 (edition 2021). Raising it is a breaking change.

## License

AGPL-3.0-or-later — see [LICENSE](https://github.com/hherb/bmlib/blob/main/LICENSE).
