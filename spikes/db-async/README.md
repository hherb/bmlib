# `bmlib-db-async` — a spike

Can [`bmlib-db`](../db-rs)'s synchronous `Db` trait be driven from a
Tauri-style async runtime, and does it survive connection pooling?

**[FINDINGS.md](FINDINGS.md) is the deliverable.** Short version: keep the sync
design, bridge with a pool plus `spawn_blocking`. A fully async trait is not
just unnecessary for SQLite — it is impossible, because `rusqlite::Transaction`
is not `Send`.

## Status

Exploratory. Depends on `../db-rs` by path. Not published, not wired to
anything.

```bash
cd spikes/db-async
cargo test          # 10 tests + 3 doc-tests
cargo clippy --all-targets
cargo fmt --check
```

## Layout

| File | What it answers |
|---|---|
| `src/pool.rs` | the recommended bridge — `open_pool`, `with_conn`, and the `PooledDb` newtype |
| `src/async_db.rs` | the road not taken: the async trait shape, the `Send` evidence against it, and a toy backend to exercise the closure |
| `tests/pool.rs` | sync `bmlib-db` unchanged through the bridge; 32 concurrent transactions |
| `tests/async_shape.rs` | lending async closures compose under nesting |

Two `compile_fail` doc-tests carry the load-bearing negatives, and both were
checked to fail for the stated reason rather than incidentally:

- `rusqlite::Transaction` is not `Send` — `RefCell<InnerConnection> cannot be
  shared between threads safely`
- a borrowed `&mut dyn Db` cannot enter `spawn_blocking` — `dyn Db cannot be
  sent between threads safely`

Two mutations were applied to check the suite bites (a block's commit not
flushing to its parent; rollback flushing instead of dropping). Both caught —
4 and 2 failures.
