# `bmlib-db` — a spike

A Rust port of [`bmlib/db/`](../../bmlib/db), written to answer one question:

> Does the composable-savepoint transaction design survive the borrow checker,
> and what does the API cost?

**[FINDINGS.md](FINDINGS.md) is the deliverable.** This crate is the evidence
behind it.

## Status

Exploratory. Not published, not wired to anything, not a dependency of
anything. It covers `bmlib/db/` and nothing else. Do not build on it without
reading "What this spike did not establish" in FINDINGS.md — in particular,
**there is no PostgreSQL backend here**, only a harness that reproduces two of
psycopg2's behaviours.

## Running it

```bash
cd spikes/db-rs
cargo test          # 62 tests + 2 doc-tests
cargo clippy --all-targets
cargo fmt --check
```

No system SQLite is needed — `rusqlite`'s `bundled` feature compiles its own
(about 20 s on a cold build). Built and tested on rustc 1.94.1; no minimum
supported version has been established, so none is claimed in `Cargo.toml`.

## Layout

| Python                 | Here                          | Notes |
|------------------------|-------------------------------|-------|
| `db/backend.py`        | `src/backend.rs`              | plus the `?` → `$n` rewriter |
| `db/connection.py`     | `src/sqlite.rs`               | plus three `Db` impls |
| `db/operations.py`     | `src/operations.rs`, `src/split.rs` | |
| `db/transactions.py`   | `src/transactions.rs`         | 116 code lines → 21 |
| `db/migrations.py`     | `src/migrations.rs`           | |
| —                      | `src/db.rs`                   | the `Db` trait; no Python counterpart |
| —                      | `src/value.rs`, `src/error.rs` | what duck typing and exceptions gave free |
| —                      | `src/pg_sim.rs`               | test harness, *not* a backend |

## Tests

Ported from the Python suite, each naming its source so a divergence is
traceable to a decision rather than an oversight:

| Rust                    | Python |
|-------------------------|--------|
| `tests/transactions.rs` | `test_db.py::TestTransaction`, `test_backends.py::TestTransactions` |
| `tests/operations.rs`   | `test_db.py::TestOperations`, `::TestCreateTablesTriggers`, `test_migrations.py::TestSplitSqlStatements` |
| `tests/migrations.rs`   | `test_migrations.py` |
| `tests/dialect.rs`      | *(no counterpart — psycopg2 and `is_sqlite()` answered these in Python)* |

`tests/common/mod.rs` carries `both_backends!`, the equivalent of
`test_backends.py`'s `backend_conn` fixture: one body, two tests.

Four mutations were applied to check the suite bites — savepoint commit
rolling back, `owns_commit` always true, the placeholder rewriter ignoring
quoting, and the splitter dropping its trigger-body rule. All four were caught
(6, 2, 2 and 2 failures). The `compile_fail` doc-test in `transactions.rs` was
checked to fail on `E0499` specifically, not incidentally.
