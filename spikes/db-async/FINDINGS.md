# Findings: `bmlib-db` from an async runtime, and pooled

**The question.** `spikes/db-rs` established a *synchronous* design. The Tauri
apps are async (tokio), and I flagged `transaction(db, |tx| ...)` as the first
thing that would break — async closures that lend their argument being the case
Rust historically could not express.

**The answer.** Keep the design synchronous. Bridge with a connection pool and
`spawn_blocking`. The async trait is not merely unnecessary — for SQLite it is
**impossible**, and not for a reason that more effort would fix.

---

## 1. `AsyncDb` cannot be implemented over `rusqlite` at all

`AsyncDb: Send` is forced: tokio's `spawn` needs a `Send` future, and a trait
object held across an `await` is part of that future. But:

- `rusqlite::Connection` is `Send` **and not `Sync`** — it holds a `RefCell`.
- `rusqlite::Transaction<'conn>` holds a `&'conn Connection`, and `&T: Send`
  requires `T: Sync`.
- Therefore `Transaction` is **not `Send`**, and nothing in the backend can
  inhabit the trait.

Verified, not reasoned: `src/async_db.rs`'s `send_evidence` carries a passing
doc-test for `Connection` and a `compile_fail` for `Transaction`, and the raw
error is `RefCell<InnerConnection> cannot be shared between threads safely →
required for &Connection to implement Send → required because it appears within
Transaction`.

This is why every async SQLite wrapper (`tokio-rusqlite`, and `sqlx`'s SQLite
driver internally) runs the connection on a dedicated thread behind a channel.
That is `spawn_blocking` with extra steps and a worse failure mode.

## 2. The async *shape* does work, if a backend ever qualifies

Worth separating, because it means this is a SQLite fact rather than a dead end.
Against a `Send` toy backend, the lending async closure composes:

```rust
pub async fn transaction<T, F>(db: &mut dyn AsyncDb, f: F) -> Result<T>
where F: for<'a> AsyncFnOnce(&'a mut (dyn AsyncDb + Send + 'a)) -> Result<T>
```

Five tests cover nesting, inner-failure isolation, outer-failure discard, and
the helper-in-either-position property. `AsyncFnOnce` (stable 1.85) is what
makes it expressible; before that you had to name the lifetime, which did not
compose for nested calls.

So a *network* backend — a real PostgreSQL client, where async actually buys
something — could take this shape. SQLite cannot, and does not need to: local
file I/O is microseconds.

**The toy backend is a toy.** It models savepoint semantics (a block
accumulates writes, commit flushes to parent, rollback drops) and nothing else.
It exists because no `rusqlite` type can inhabit the trait, so there was no
other way to exercise the type system. It is not evidence about durability.

## 3. The bridge: pool + `spawn_blocking`

```rust
with_conn(&pool, |db| {
    transaction(db, |tx| {
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params!["a"])?;
        Ok(())
    })
}).await
```

The closure body is **ordinary synchronous `bmlib-db`, unchanged**. Five tests,
including 32 concurrent transactions across a pool of 8 from a multi-thread
runtime, all committing.

## 4. The one API change async forces

A borrowed connection cannot cross into a blocking task. `spawn_blocking`
requires `Send + 'static`; `&mut dyn Db` is neither. Verified — the error is
`dyn Db cannot be sent between threads safely`, and it is pinned as a
`compile_fail` doc-test on `with_conn`.

So the caller hands over **work to run against a connection**, not a
connection. That is the entire difference between the Python call style and
this one, and it is confined to the outermost boundary: everything inside the
closure is the same code.

## 5. Pooling is nearly free

`r2d2::PooledConnection<SqliteConnectionManager>` derefs to
`rusqlite::Connection`, which already implements `Db` — so `&mut *checkout` is a
`&mut dyn Db` with no wrapper.

A newtype (`PooledDb`, 30 lines) is needed only to make the *handle itself* a
`Db` — a struct field, or `Box<dyn Db>`. It has to be a newtype because of the
orphan rule: `Db` belongs to `bmlib-db` and `PooledConnection` to `r2d2`, so a
third crate may implement neither for the other. Worth knowing before designing
the backend story, though it does not bite a third party writing their *own*
backend type.

No version skew: `r2d2_sqlite` 0.35 and `bmlib-db` both resolve to a single
`rusqlite` 0.40 in the graph.

## 6. `PRAGMA busy_timeout` is load-bearing

Concurrent writers on one SQLite file get `SQLITE_BUSY`. Without the pragma in
the pool's `with_init`, a Tauri command handler surfaces that to the user as a
failure rather than waiting the millisecond it needs. The pool sets
`foreign_keys`, `busy_timeout=5000` and `journal_mode=WAL`.

## 7. What `async_trait` would have cost, had it been possible

One boxed future per statement, on top of the `Box` per transaction the sync
trait already pays. Native `async fn` in traits avoids the box but is not
dyn-compatible, and the whole design rests on `&mut dyn Db`. Not the deciding
factor here — impossibility was — but worth recording.

## What this spike did not establish

- **No PostgreSQL**, again. That is the backend where async would actually pay,
  and the one where the shape in §2 might be worth taking.
- **No performance numbers.** Not `spawn_blocking` overhead, not pool
  contention, not the boxed futures. The concurrency test asserts correctness,
  not throughput.
- **No Tauri.** These are tokio tests. Tauri runs on tokio, and a command
  handler is an async fn, so the shape transfers — but no actual Tauri app was
  built.
- **Write concurrency was tested on one file with WAL.** Multi-process access
  (several of the pipeline's apps against one database) is a different question
  and is not covered.

## What it means for the migration plan

`db/` stays synchronous, which means `spikes/db-rs` stands as-is — no rework.
Add a pool and one `with_conn`-shaped helper at the boundary where the GUI calls
in, and everything below it is the code already written.

The choice is also not load-bearing for the future: if a PostgreSQL backend
later wants a genuinely async path, §2 says the trait shape can follow it
without redesigning the call sites.
