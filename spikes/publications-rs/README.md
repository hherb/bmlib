# `bmlib-publications` — a spike

A slice of [`bmlib/publications/storage.py`](../../bmlib/publications/storage.py),
ported to answer two things [`db-rs`](../db-rs) could not: does `Value`/`Row`
hold up once real code passes through it, and do the `DECISIONS.md` invariants
survive a port?

**[FINDINGS.md](FINDINGS.md) is the deliverable.**

## Status

Exploratory. Depends on `../db-rs` by path. Covers the store path only — no
`sync.py`, no fetchers, SQLite only.

```bash
cd spikes/publications-rs
cargo test          # 22 tests
cargo clippy --all-targets
cargo fmt --check
```

## What is here

| File | Python source |
|---|---|
| `src/storage.rs` | `storage.py` — store, merge, consolidate, the child-row rules |
| `src/identifiers.rs` | `storage.py`'s `_normalize_doi` / `_normalize_pmid` |
| `src/models.rs` | `models.py` — `Publication`, `Grant`, `AuthorAffiliation` |
| `src/schema.rs` | `schema.py` — the four tables this slice touches |
| `src/error.rs` | *(no counterpart — Python raises)* |
| `tests/storage.rs` | `test_backends.py::TestStorage`, `::TestGrantAndAffiliationStorage` |

## Invariants carried

Each is pinned by a test named after its Python original, and each was checked
by mutation:

- child rows are replaced **per source**, never per publication
- consolidation relocates every child row before the parent is deleted, and the
  keep row's sources win
- merge fills NULLs and never overwrites
- open access is a one-way latch
- DOIs are case-folded and prefix-stripped on both store and lookup
- a child row naming no source is refused, and the whole store rolls back

Six mutations applied, six caught.
