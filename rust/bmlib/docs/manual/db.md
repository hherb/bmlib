# bmlib::db — Database Abstraction

Pure functions over a `&mut dyn Db`, on SQLite or PostgreSQL. No ORM: SQL is
written by hand, with `?` placeholders on both backends. The Python page is
[database.md](../../../../docs/manual/database.md); this one describes the
Rust API and where it differs.

| Need | Feature |
|---|---|
| SQLite (`open_memory`, `open_path`, `Db` for `rusqlite::Connection`) | `sqlite` (default) |
| PostgreSQL (`connect_postgresql`, `Db` for `postgres::Client`) | `postgres` |
| Dialects, placeholders, SQL splitting, `Value`, `Row`, the operations | none |

`Db`, `DbError`, `Row` and `Value` are also re-exported at the crate root,
and so is the `params!` macro (`bmlib::params`, not `bmlib::db::params`).

## Opening a database

```rust
use bmlib::db::{open_memory, open_path, Db, Dialect};

let mut mem = open_memory()?;                       // foreign keys on
assert_eq!(mem.dialect(), Dialect::Sqlite);

let dir = std::env::temp_dir().join("bmlib-manual-db");
let file = open_path(&dir.join("papers.db"), true, true)?; // WAL, foreign keys
# drop(file); std::fs::remove_dir_all(&dir).ok();
# Ok::<(), bmlib::DbError>(())
```

`open_path(path, wal_mode, foreign_keys)` creates the parent directory and
mirrors Python's `connect_sqlite` (whose two flags default to on). PostgreSQL,
behind the `postgres` feature:

```rust,ignore
use bmlib::db::{connect_postgresql, connect_postgresql_params};

let pg = connect_postgresql("host=/tmp port=5432 dbname=bmlib user=postgres")?;
let pg = connect_postgresql_params("localhost", 5432, "bmlib", "postgres", "")?;
```

An empty password is left unset, so trust/peer authentication, `.pgpass` and
`PGPASSWORD` still apply. The connection is made without TLS.

## Values, rows and operations

`Value` is one of `Null`, `Int(i64)`, `Real(f64)`, `Text(String)`,
`Blob(Vec<u8>)`, with `From` impls for the usual Rust types (`&str`,
`String`, `i64`, `i32`, `usize`, `f64`, `bool`, `Vec<u8>`, and `Option<T>`
for `NULL`). `params![…]` builds a `Vec<Value>` from those.

`Row` is ordered *and* named: `get(name)`, `get_str(name)`,
`get_i64(name)` (each a `Result`, `DbError::Column` for a missing column or
the wrong type), `at(index)`, `columns()`, `values()`. One row type on both
backends, so `fetch_scalar` needs no backend branch as Python's did.

| Function | Returns |
|---|---|
| `execute(db, sql, params)` | rows affected (`u64`) |
| `executemany(db, sql, rows)` | `()`; one statement per row of `&[Vec<Value>]` |
| `fetch_all(db, sql, params)` | `Vec<Row>` |
| `fetch_one(db, sql, params)` | `Option<Row>` |
| `fetch_scalar(db, sql, params)` | `Option<Value>` — the first column of the first row |
| `table_exists(db, name)` | `bool` |
| `create_tables(db, schema_sql)` | `()`; runs a multi-statement DDL script one statement at a time |

```rust
use bmlib::db::{create_tables, execute, fetch_all, fetch_scalar, open_memory, table_exists};
use bmlib::{params, Value};

let mut db = open_memory()?;
create_tables(&mut db, "
    CREATE TABLE paper (id INTEGER PRIMARY KEY, doi TEXT, year INTEGER);
    CREATE INDEX paper_doi ON paper (doi);
")?;
assert!(table_exists(&mut db, "paper")?);

execute(&mut db, "INSERT INTO paper (doi, year) VALUES (?, ?)", &params!["10.1/a", 2024])?;
execute(&mut db, "INSERT INTO paper (doi, year) VALUES (?, ?)", &params!["10.1/b", None::<i64>])?;

let rows = fetch_all(&mut db, "SELECT doi, year FROM paper ORDER BY id", &[])?;
assert_eq!(rows[0].get_str("doi")?, "10.1/a");
assert!(rows[1].get("year")?.is_null());

let n = fetch_scalar(&mut db, "SELECT COUNT(*) FROM paper", &[])?;
assert_eq!(n, Some(Value::Int(2)));
# Ok::<(), bmlib::DbError>(())
```

`create_tables` splits the script itself (`split_sql_statements`), never
through a batch primitive that would commit behind a surrounding transaction.
The splitter knows string literals, comments and `CREATE TRIGGER … BEGIN …
END` bodies; it does **not** know PostgreSQL's dollar-quoted `$$ … $$`
bodies. Unlike Python, it splits on PostgreSQL too.

## Transactions

`transaction(db, |tx| …)` commits when the closure returns `Ok` and rolls
back on `Err`. Called on a connection it opens a real transaction; called on
the `tx` of an open block it opens a **savepoint**, so a helper written
against `&mut dyn Db` composes either way and the outermost block owns the
one commit — the property `publications`' one-commit-per-day sync rests on.

```rust
use bmlib::db::{create_tables, execute, fetch_scalar, open_memory, transaction, Db};
use bmlib::{params, DbError, Value};

fn store(db: &mut dyn Db, v: &str) -> bmlib::db::Result<()> {
    transaction(db, |tx| {
        execute(tx, "INSERT INTO t (v) VALUES (?)", &params![v])?;
        Ok(())
    })
}

let mut conn = open_memory()?;
create_tables(&mut conn, "CREATE TABLE t (v TEXT NOT NULL);")?;

store(&mut conn, "standalone")?;           // its own transaction
transaction(&mut conn, |tx| {
    store(tx, "batched")?;                  // a savepoint
    // An inner failure rolls back to its own savepoint only.
    let inner: Result<(), DbError> = transaction(tx, |t| {
        execute(t, "INSERT INTO t (v) VALUES (?)", &params!["lost"])?;
        Err(DbError::abort(std::io::Error::other("changed my mind")))
    });
    assert!(inner.is_err());
    Ok(())
})?;                                        // one commit

let n = fetch_scalar(&mut conn, "SELECT COUNT(*) FROM t", &[])?;
assert_eq!(n, Some(Value::Int(2)));
# Ok::<(), DbError>(())
```

A closure rather than a guard, because `Drop` cannot see whether the block
succeeded and cannot report a failed commit; here a failed commit is an
`Err`. `DbError::abort(e)` wraps a caller's own error so it travels through
the block (`DbError::Abort`); a rollback that also fails never masks the
caller's error.

A caller whose own error type wraps `DbError` uses `transaction_with`, which
is generic over `E: From<DbError>` so `?` works on both inside the closure:

```rust
use bmlib::db::{execute, open_memory, transaction_with};
use bmlib::{params, DbError};

#[derive(Debug)]
enum StoreError { Db(DbError), Invalid(&'static str) }
impl From<DbError> for StoreError { fn from(e: DbError) -> Self { StoreError::Db(e) } }

let mut db = open_memory().map_err(StoreError::Db)?;
let r: Result<(), StoreError> = transaction_with(&mut db, |tx| {
    execute(tx, "CREATE TABLE t (v TEXT)", &[])?;
    execute(tx, "INSERT INTO t (v) VALUES (?)", &params![""])?;
    Err(StoreError::Invalid("empty value"))   // rolls back the table too
});
assert!(matches!(r, Err(StoreError::Invalid(_))));
assert!(!bmlib::db::table_exists(&mut db, "t").map_err(StoreError::Db)?);
# Ok::<(), StoreError>(())
```

**What is not here, and why.** Python's `transaction_depth()`, its
`(thread, id(conn))` side table, `require_transaction_control()` and
`TransactionModeError` have no counterpart. Nesting is the type of the value
in hand: reaching around an open block to the connection it borrows does not
compile, which removes the class of silent-write-loss bugs that machinery
guarded against. `owns_commit(db)` is kept as public API and is a constant per
implementation (`true` for a connection, `false` inside a block).

## Migrations

```rust
use bmlib::db::{create_tables, get_applied_versions, open_memory, run_migrations, Migration};

let mut db = open_memory()?;
let migrations = || vec![
    Migration::new(1, "papers", |db| create_tables(db, "CREATE TABLE paper (id INTEGER PRIMARY KEY);")),
    Migration::new(2, "doi", |db| create_tables(db, "ALTER TABLE paper ADD COLUMN doi TEXT;")),
];
assert_eq!(run_migrations(&mut db, migrations())?, 2);
assert_eq!(run_migrations(&mut db, migrations())?, 0);   // already applied
assert_eq!(get_applied_versions(&mut db)?.len(), 2);
# Ok::<(), bmlib::DbError>(())
```

`run_migrations` creates `schema_version (version, name, applied_at)` if
absent, applies pending migrations in version order, and returns how many
ran. Each migration and its version row are written in one transaction, so a
failing migration leaves neither its DDL nor a row claiming it succeeded.

## Dialects and placeholders

Write `?` everywhere. Each backend's `Db` impl renumbers to PostgreSQL's
`$1, $2, …` itself (`adapt_sql(sql, dialect)` → `rewrite_placeholders`), so
Python's `placeholder(conn)` idiom is unnecessary; `placeholder()` (always
`"?"`) is kept so a mechanical port compiles. `placeholders(n)` builds an
`IN (…)` list and returns `""` for zero, which neither backend accepts, so
skip the clause.

```rust
use bmlib::db::{adapt_sql, placeholders, Dialect};

assert_eq!(placeholders(3), "?, ?, ?");
assert_eq!(
    adapt_sql("SELECT * FROM t WHERE a = ? AND b IN (?, ?) AND c = '?'", Dialect::Postgres),
    "SELECT * FROM t WHERE a = $1 AND b IN ($2, $3) AND c = '?'",
);
```

A `?` inside a string literal, quoted identifier or comment is left alone.
PostgreSQL's `jsonb` operators `?`, `?|` and `?&` **are** rewritten; no bmlib
SQL uses them.

## Implementing `Db`

The trait is the seam a host application uses to supply its own storage (the
mobile apps, through `bmlib-ffi`, build with `--no-default-features`):
`dialect`, `execute_raw`, `query_raw`, `last_insert_rowid`, `begin` (a
transaction on a connection, a savepoint on a transaction), `commit`,
`rollback`, `owns_commit`. `execute_raw`/`query_raw` receive SQL already
written with `?`; adapting it to the dialect is the implementation's job.
