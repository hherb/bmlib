# Findings: porting `bmlib/db/` to Rust

**The question.** `bmlib/db/transactions.py` is the module I expected to hurt
most in a Rust port. Its composable-savepoint design rests on a side table of
open blocks keyed by `(thread, id(conn))`, and the API convention it serves —
"pure functions take a DB-API connection as first argument, and work whether or
not a block is already open" — is what the whole of `publications/` is written
against. Rust's borrow checker looked likely to reject that shape outright.

**The answer.** It does not survive; it is *replaced*, and the replacement is
smaller and safer. `transactions.py` is 116 code lines, about 60 of them the
nesting bookkeeping and its longest comment. The Rust equivalent is **21 code
lines** and has no bookkeeping at all. The convention survives intact:
`&mut dyn Db` sits exactly where `conn: Any` sat.

The cost lands somewhere else entirely — in modelling values and errors, which
Python got free from duck typing and exceptions.

---

## What disappears

Gone, with nothing put in their place:

| Python | Why it is not needed |
|---|---|
| `_depths`, `_depth_key`, `_depths_lock` | nesting is the type of the value in hand |
| `_is_nested()` | ditto — a `Connection` begins, a `Transaction` opens a savepoint |
| `transaction_depth()` | nothing needs a count |
| `is_sqlite(conn)` | the implementation reports its own dialect |
| the `(thread, id(conn))` keying and the strong-reference-keeps-the-id-valid argument | no shared state to key |
| `fetch_scalar`'s `isinstance(row, dict)` branch | one `Row` type on both backends, ordered *and* named |

`owns_commit()` survives as public API but is now a constant per
implementation, and **its only Python caller no longer needs it**:
`create_tables` had to ask because the same `conn` object arrives whether or
not a block is open. Here a connection is autocommit and a transaction is owned
by its opener, so neither has a decision to make.

## The API shape that works

```rust
pub trait Db {
    fn begin(&mut self) -> Result<Box<dyn Db + '_>>;
    fn commit(self: Box<Self>) -> Result<()>;
    fn owns_commit(&self) -> bool;
    // execute_raw, query_raw, dialect, last_insert_rowid
}

pub fn transaction<T, F>(db: &mut dyn Db, f: F) -> Result<T>
where F: FnOnce(&mut dyn Db) -> Result<T>
```

`Connection`, `Transaction<'_>` and `Savepoint<'_>` all implement `Db`, so a
helper written against `&mut dyn Db` composes in any of the three positions —
the property `publications.sync()`'s one-commit-per-day batching depends on.
`tests/transactions.rs::a_helper_composes_in_either_position` is that test.

Two mechanical notes, both load-bearing: `begin` returns a *boxed* trait object
rather than an associated type, which is what keeps the trait dyn-compatible;
and `commit`/`rollback` take `self: Box<Self>`, which is how a consuming method
stays callable on a trait object. `src/db.rs` records why this beat the generic
GAT alternative — including one commonly-cited reason (monomorphisation
recursion) that was **measured and found not to apply**, since rusqlite's
savepoint type is its own parent type.

## Two hazards that cannot occur

Like the `datetime`/`date` case in the wider port, these are tests that cannot
be ported because the bug they pin is unrepresentable.

**1. Reaching around an open block.** The `(thread, id(conn))` key exists
because Python hands the same `conn` into helpers regardless of nesting, and
keying it wrong silently stopped committing. In Rust the connection is mutably
borrowed for the block's whole life, so the ambiguous call does not compile.
Verified as `E0499`, not merely asserted — `src/transactions.rs` carries it as
a `compile_fail` doc-test.

By the same token, `test_a_block_on_another_thread_does_not_look_like_nesting`
has no Rust counterpart: holding a block open on one thread while opening
another elsewhere on the same connection requires two simultaneous `&mut`, and
`rusqlite::Connection` is `Send` but not `Sync`.

**2. The pending write.** `test_works_with_pending_write` and
`test_exception_preserves_pending_write` both describe Python's `sqlite3`
auto-beginning before DML and leaving a write uncommitted. `rusqlite` is
autocommit, so there is no pending state to collide with or to preserve. A
caller who issues a raw `BEGIN` can still create one — but that is going around
the API, not the default behaviour these tests guard.

## What costs more

| | Python | Rust |
|---|---|---|
| `transactions` | 116 | **21** |
| `operations` (+ splitter) | 166 | 121 |
| `migrations` | 90 | 79 |
| `backend` | 33 | 72 |
| `connection` / `sqlite` | 67 | 152 |
| `__init__` / `lib` | 41 | 16 |
| `db` + `value` + `error` | — | 168 |
| **total** | **513** | **629** |

Code lines, blanks and comments excluded; the 138-line `pg_sim` harness is not
counted. Python's figure includes docstring bodies, so the real gap is a little
wider than 1.23×.

Three things drive it:

- **`Value` and `Row` (125 lines).** Python passes a tuple of anything and gets
  back a dict-ish row. Rust needs one concrete type both backends accept, so
  every call site gains a conversion. `params![]` hides it — `&params!["a", 1,
  None::<&str>]` — but the type has to exist.
- **Three impls instead of one factory (152 vs 67).** `Connection`,
  `Transaction` and `Savepoint` each implement the trait. Most of it is
  delegation; the *distinctions* are four lines total, and they are exactly the
  distinctions Python computed at runtime.
- **`DbError` (30 lines).** Python raises. Rust needs a type that carries both a
  driver failure and the caller's own — the latter being what tells
  `transaction()` to roll back, the role `raise RuntimeError("boom")` plays in
  the tests.

None of this is transaction-related. **The nesting design was the cheap part.**

## Deliberate divergences

1. **A closure, not a context manager.** `Drop` cannot return a `Result` and
   cannot see whether the block succeeded, so an RAII guard can only
   auto-*rollback*. `transaction(db, |tx| { ... })` is the one place the API
   reads differently from `with transaction(conn):`, and it is strictly
   stronger: a failing commit is reported rather than swallowed.
2. **`create_tables` splits for both dialects.** Python splits for SQLite only
   and hands psycopg2 the whole script. Splitting is correct on both, so the
   dialect branch goes — at the cost of one round trip per statement, which for
   `publications/schema.py` is 25. Two caveats before adopting this: measure it
   against a real server, and note that the splitter does not know PostgreSQL's
   dollar-quoted bodies (`$$ ... $$`). bmlib's schema has no `CREATE FUNCTION`,
   so nothing there needs them today.
3. **Numbered placeholders are produced in the backend, not the call site.**
   PostgreSQL wants `$1, $2`, not psycopg2's `%s`, so Python's
   `", ".join([placeholder] * n)` idiom does not carry over. Rather than change
   every call site in `publications/`, callers keep writing `?` and
   `rewrite_placeholders` renumbers — skipping literals, quoted identifiers and
   comments. This makes `placeholder()` vestigial; it is kept only so a
   mechanical port compiles unchanged.
4. **The splitter scans a `Vec<char>`.** The Python original indexes by code
   point; transliterating that arithmetic onto a `&str` makes every slice a
   potential panic on a non-ASCII boundary. One allocation buys exact
   equivalence. `non_ascii_in_a_script_does_not_panic` is the guard, and it has
   no Python counterpart because there is nothing there to catch.

## What this spike did not establish

Read this before quoting anything above.

- **There is no PostgreSQL backend.** No server was available. `pg_sim` is a
  harness reproducing exactly two psycopg2 behaviours — numbered placeholders,
  and "any statement leaves the connection INTRANS" — over a wrapped SQLite
  connection. It does not simulate PostgreSQL's SQL, types, or locking. The
  claim it *does* support is the one that matters most here: `owns_commit()`
  never consults driver status, so the INTRANS hazard cannot arise. Everything
  else about the PostgreSQL side is design intent, not measurement.
- **`ensure_version_table`'s PostgreSQL DDL branch is unverified** — `TIMESTAMP
  NOT NULL DEFAULT NOW()` is not SQLite-parseable, so the migration tests are
  SQLite-only. Shimming the harness to make them pass would have widened the
  coverage without testing anything.
- **No performance measurement at all.** Not the `Box` per transaction, not the
  placeholder rewrite per statement, not the 25 round trips of a split schema.
- **No concurrency, no pooling, no async.** A Tauri app will want a connection
  pool and will call from a tokio runtime; neither is touched here. This is the
  next thing to spike if the port goes ahead.
- **Nothing above `db/`.** `publications/storage.py` and `sync.py` are what
  actually exercise the batching, and they carry the density of DECISIONS.md
  invariants. That the shape *compiles* is established; that it is pleasant
  across 5,364 lines of `publications/` is not.

## What it means for the migration plan

The `db/`-first ordering holds, and for a better reason than "it is small".
`transactions.py` was the piece I flagged as the biggest borrow-checker
rewrite; it is instead an 82% reduction with three classes of silent-write-loss
bug made unrepresentable. The redesign risk was mispriced — on the evidence
here, downward.

The trait is also the seam the migration needs: `&mut dyn Db` can be
implemented by something that forwards to the Python sidecar, so `db/` can go
to Rust before `publications/` does, with the storage layer still in Python
during the changeover.

What now looks like the real work is not `db/` at all. It is `Value`/`Row` —
the boundary type every ported module will pass through, and the thing to get
right early, because changing it later touches everything.
