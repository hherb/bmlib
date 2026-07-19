# bmlib.db — Database Abstraction

Thin database abstraction layer providing pure functions over standard DB-API 2.0 connections. Supports SQLite (built-in) and PostgreSQL (optional, via psycopg2).

All functions take a DB-API connection as their first argument. SQL is passed directly — callers are responsible for writing backend-appropriate SQL (`?` for SQLite, `%s` for PostgreSQL).

> **Breaking change in 0.4.0 — `transaction()` now nests via savepoints.**
> On SQLite, a `transaction(conn)` block entered while the connection already holds uncommitted writes no longer commits. It joins the open transaction through a `SAVEPOINT`, and the owner of the enclosing transaction commits. Code that relied on `transaction()` to flush earlier bare `execute()` writes must now commit explicitly. See [Nested transactions](#nested-transactions) and [Upgrading from 0.3.x](#upgrading-from-03x).

## Installation

SQLite support is built-in. For PostgreSQL:

```bash
pip install bmlib[postgresql]
```

## Module layout

| Submodule | Contents | Backend-aware? |
|-----------|----------|----------------|
| `connection` | `connect_sqlite()`, `connect_postgresql()` | Separate factory per backend |
| `operations` | `execute()`, `executemany()`, `fetch_one()`, `fetch_all()`, `fetch_scalar()`, `table_exists()`, `create_tables()` | Yes — `table_exists()` and `create_tables()` dispatch on `type(conn).__module__` |
| `transactions` | `transaction()` | Yes — savepoint nesting on SQLite only |
| `migrations` | `Migration`, `run_migrations()`, `get_applied_versions()` | Yes — placeholder style and `schema_version` DDL |

Backend detection is by connection type, never by configuration: any connection whose `type(conn).__module__` contains `"sqlite3"` takes the SQLite path, everything else takes the PostgreSQL path.

## Imports

```python
from bmlib.db import (
    connect_sqlite,
    connect_postgresql,
    execute,
    executemany,
    fetch_one,
    fetch_all,
    fetch_scalar,
    table_exists,
    create_tables,
    transaction,
    Migration,
    run_migrations,
)
```

The list above is the complete `bmlib.db.__all__` (12 names). One public symbol is **not** re-exported at package level and must be imported from its submodule:

```python
from bmlib.db.migrations import get_applied_versions
```

---

## Connection Factories

### `connect_sqlite`

```python
def connect_sqlite(
    path: str | Path,
    *,
    wal_mode: bool = True,
    foreign_keys: bool = True,
) -> sqlite3.Connection
```

Open (or create) a SQLite database and return a connection.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str \| Path` | *(required)* | File path. Use `":memory:"` for an in-memory database. Paths are expanded via `Path.expanduser()`. Parent directories are created automatically. |
| `wal_mode` | `bool` | `True` | Enable WAL journal mode for better concurrent read access. Not applied to in-memory databases. |
| `foreign_keys` | `bool` | `True` | Enforce foreign key constraints via `PRAGMA foreign_keys=ON`. |

**Returns:** `sqlite3.Connection` with `row_factory` set to `sqlite3.Row` (rows accessible by column name) and `check_same_thread=False`.

**Example:**

```python
# File-based database
conn = connect_sqlite("~/.myapp/data.db")

# In-memory database (useful for tests)
conn = connect_sqlite(":memory:")

# Without WAL mode
conn = connect_sqlite("/tmp/test.db", wal_mode=False)
```

---

### `connect_postgresql`

```python
def connect_postgresql(
    dsn: str | None = None,
    *,
    host: str = "localhost",
    port: int = 5432,
    database: str = "bmlib",
    user: str = "bmlib",
    password: str = "",
) -> Any
```

Open a PostgreSQL connection via psycopg2. Either provide a full DSN string, or individual connection parameters.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dsn` | `str \| None` | `None` | Full DSN connection string. If provided, individual parameters are ignored. |
| `host` | `str` | `"localhost"` | Database server hostname. |
| `port` | `int` | `5432` | Database server port. |
| `database` | `str` | `"bmlib"` | Database name. |
| `user` | `str` | `"bmlib"` | Database user. |
| `password` | `str` | `""` | Database password. |

**Returns:** A `psycopg2` connection with `RealDictCursor` as the default cursor factory (rows are dictionaries).

**Raises:** `ImportError` if psycopg2 is not installed — `"psycopg2 not installed. Install with: pip install bmlib[postgresql]"`.

**Example:**

```python
# Using DSN
conn = connect_postgresql("postgresql://user:pass@host:5432/dbname")

# Using individual parameters
conn = connect_postgresql(host="db.example.com", database="papers", user="app", password="secret")
```

---

## Query Operations

### `execute`

```python
def execute(conn: Any, sql: str, params: Sequence = ()) -> Any
```

Execute a single SQL statement and return the cursor. Useful for INSERT/UPDATE/DELETE where you need `cursor.lastrowid` or `cursor.rowcount`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sql` | `str` | *(required)* | The SQL statement to execute. |
| `params` | `Sequence` | `()` | Parameter values for placeholders. |

**Returns:** The DB-API cursor after execution.

**Example:**

```python
cursor = execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "Title"))
new_id = cursor.lastrowid
```

> **`execute()` does not commit.** On SQLite it leaves the connection inside an auto-begun transaction (`conn.in_transaction` is `True`), so the write is not durable until you commit — either directly or by wrapping the work in a [`transaction()`](#transaction) block from the start.

---

### `executemany`

```python
def executemany(conn: Any, sql: str, params_seq: Sequence[Sequence]) -> None
```

Execute a statement for each parameter set in `params_seq`. Useful for bulk inserts.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sql` | `str` | *(required)* | The SQL statement to execute repeatedly. |
| `params_seq` | `Sequence[Sequence]` | *(required)* | Sequence of parameter tuples. |

**Example:**

```python
executemany(conn, "INSERT INTO tags (name) VALUES (?)", [("cancer",), ("genomics",), ("rct",)])
```

---

### `fetch_one`

```python
def fetch_one(conn: Any, sql: str, params: Sequence = ()) -> Any
```

Execute a query and return the first row, or `None` if no rows match.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sql` | `str` | *(required)* | The SQL query. |
| `params` | `Sequence` | `()` | Parameter values for placeholders. |

**Returns:** The first row (as `sqlite3.Row` or `RealDictRow`), or `None`.

**Example:**

```python
row = fetch_one(conn, "SELECT * FROM papers WHERE doi = ?", ("10.1101/x",))
if row:
    print(row["title"])
```

---

### `fetch_all`

```python
def fetch_all(conn: Any, sql: str, params: Sequence = ()) -> list[Any]
```

Execute a query and return all rows.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sql` | `str` | *(required)* | The SQL query. |
| `params` | `Sequence` | `()` | Parameter values for placeholders. |

**Returns:** List of rows. Empty list if no rows match.

**Example:**

```python
rows = fetch_all(conn, "SELECT title, doi FROM papers WHERE journal = ?", ("Nature",))
for row in rows:
    print(row["title"], row["doi"])
```

---

### `fetch_scalar`

```python
def fetch_scalar(conn: Any, sql: str, params: Sequence = ()) -> Any
```

Execute a query and return the first column of the first row, or `None`. Convenient for `COUNT(*)`, `MAX()`, and similar single-value queries.

The value is read by **index** (`row[0]`), which works for both `sqlite3.Row` and psycopg2's `RealDictRow`. If the row cannot be indexed, `None` is returned rather than raising.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sql` | `str` | *(required)* | The SQL query. |
| `params` | `Sequence` | `()` | Parameter values for placeholders. |

**Returns:** The scalar value, or `None`.

**Example:**

```python
count = fetch_scalar(conn, "SELECT COUNT(*) FROM papers")
print(f"Total papers: {count}")
```

---

## Schema Utilities

### `table_exists`

```python
def table_exists(conn: Any, name: str) -> bool
```

Check whether a table exists. Automatically detects the database backend and uses the appropriate system catalog query — `sqlite_master` for SQLite, `information_schema.tables` for PostgreSQL.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `name` | `str` | *(required)* | Table name to check. |

**Returns:** `True` if the table exists, `False` otherwise.

**Example:**

```python
if not table_exists(conn, "papers"):
    create_tables(conn, SCHEMA_SQL)
```

---

### `create_tables`

```python
def create_tables(conn: Any, schema_sql: str) -> None
```

Execute a (possibly multi-statement) schema DDL string.

**Changed in 0.4.0.** On SQLite the script is now split into individual statements and executed one at a time through a cursor; `sqlite3`'s `executescript()` is deliberately **not** used, because it issues an implicit `COMMIT` before running. That commit would break the atomicity of a surrounding [`transaction()`](#transaction) block and leave migrations non-atomic. Executing statements individually keeps the DDL inside the active transaction — SQLite supports transactional DDL — so a failure rolls the whole thing back cleanly.

Commit behaviour follows from that:

| Backend | Execution | Commit |
|---------|-----------|--------|
| SQLite | Statements split and executed one at a time | Commits **only** when `conn.in_transaction` is `False` (i.e. a standalone call). Inside an open transaction the commit is left to its owner. |
| PostgreSQL | Whole script passed to a single `cursor.execute()` | Always commits. |

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `schema_sql` | `str` | *(required)* | The SQL DDL string (may contain multiple statements separated by `;`). |

**Example:**

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    doi   TEXT UNIQUE,
    title TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi);
"""
create_tables(conn, SCHEMA)          # standalone: committed
```

Because the DDL now stays inside an enclosing transaction, a failure downstream discards the schema too:

```python
try:
    with transaction(conn):
        create_tables(conn, SCHEMA)
        raise RuntimeError("something went wrong after the DDL")
except RuntimeError:
    pass

table_exists(conn, "papers")         # False — the DDL rolled back with the block
```

> **Caveat: the statement splitter does not parse trigger bodies.**
> Splitting happens on semicolons outside string literals and comments. It understands single- and double-quoted strings (including SQL `''` escaping), `--` line comments, and `/* */` block comments — which covers the plain `CREATE TABLE` / `CREATE INDEX` schemas used across bmlib. It does **not** understand `CREATE TRIGGER ... BEGIN ... END;`, whose body contains semicolons: such a script will be split mid-trigger and the fragments will fail. Create triggers with a separate `execute()` call rather than through `create_tables()`. The PostgreSQL path is unaffected — it hands the whole script to the server in one call.

---

## Transactions

### `transaction`

```python
@contextmanager
def transaction(conn: Any) -> Generator[Any, None, None]
```

Context manager that commits on success and rolls back on exception.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |

**Yields:** The connection itself (for convenience).

Behaviour depends on the backend and on whether a transaction is already open:

| Situation | On entry | On success | On exception |
|-----------|----------|------------|--------------|
| SQLite, no transaction open | Explicit `BEGIN` | `conn.commit()` | `conn.rollback()`, then re-raise |
| SQLite, `conn.in_transaction` is `True` | `SAVEPOINT bmlib_transaction` | `RELEASE SAVEPOINT` — **no commit** | `ROLLBACK TO SAVEPOINT`, `RELEASE SAVEPOINT`, then re-raise |
| PostgreSQL | Nothing (psycopg2 has autocommit off) | `conn.commit()` — connection-wide | `conn.rollback()` — connection-wide, then re-raise |

**Example:**

```python
with transaction(conn):
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/a", "Paper A"))
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/b", "Paper B"))
# Both inserts are committed atomically.
# If either raises, both are rolled back.
```

---

### Nested transactions

**New in 0.4.0 (SQLite only).** `sqlite3` auto-begins a transaction before DML, so by the time a nested `transaction()` block is entered the connection may already hold uncommitted writes — issuing `BEGIN` there would raise *"cannot start a transaction within a transaction"*. Instead, the inner block runs inside a savepoint. This is what makes nesting composable:

- On **inner failure**, only the inner block's writes are rolled back. The outer block's writes survive, still uncommitted, and the outer block can carry on.
- On **inner success**, the savepoint is released and **no commit is issued** — whoever opened the enclosing transaction owns the commit.
- On **outer failure**, everything rolls back, including the writes of inner blocks that already succeeded.

The practical payoff is one commit per batch instead of one per call. A helper function written with its own `transaction()` block stays correct standalone *and* composes into a batch loop with no changes:

```python
from bmlib.db import connect_sqlite, create_tables, execute, fetch_all, fetch_scalar, transaction

conn = connect_sqlite(":memory:")
create_tables(conn, "CREATE TABLE papers (doi TEXT PRIMARY KEY, title TEXT NOT NULL);")

def store_paper(conn, doi, title):
    """Atomic on its own; joins the caller's transaction when nested."""
    with transaction(conn):
        execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", (doi, title))

# One commit for the whole batch, not one per paper.
batch = [("10.1101/a", "Paper A"), ("10.1101/b", "Paper B")]
with transaction(conn):
    for doi, title in batch:
        store_paper(conn, doi, title)
        assert conn.in_transaction        # inner exit did not commit
assert not conn.in_transaction            # the outer block committed, once

print(fetch_scalar(conn, "SELECT COUNT(*) FROM papers"))   # 2
```

An inner failure rolls back only its own writes; the batch continues:

```python
import sqlite3

with transaction(conn):
    store_paper(conn, "10.1101/c", "Paper C")
    try:
        store_paper(conn, "10.1101/c", "Duplicate")        # UNIQUE constraint fails
    except sqlite3.IntegrityError:
        pass                                               # rolled back to its savepoint
    store_paper(conn, "10.1101/d", "Paper D")

print([r[0] for r in fetch_all(conn, "SELECT doi FROM papers ORDER BY doi")])
# ['10.1101/a', '10.1101/b', '10.1101/c', '10.1101/d']
```

And an outer failure discards the inner blocks' writes too:

```python
try:
    with transaction(conn):
        store_paper(conn, "10.1101/e", "Paper E")          # inner block succeeded
        raise RuntimeError("boom")
except RuntimeError:
    pass

print(fetch_scalar(conn, "SELECT COUNT(*) FROM papers"))   # still 4 — 'e' was discarded
```

> **PostgreSQL does not nest.**
> No savepoint path is implemented for psycopg2. A nested `transaction()` block on a PostgreSQL connection commits **connection-wide** on success and rolls back connection-wide on exception — so an inner failure discards the outer block's writes as well, and an inner success makes the outer block's work durable early. Do not rely on nesting semantics in code that must run against both backends; keep a single `transaction()` block at the outermost level instead.

[`bmlib.publications.sync()`](publications.md) is the reference consumer of this behaviour: it wraps a day's worth of `store_publication()` calls — each of which opens its own `transaction()` — in one outer block, paying a single commit per synced day rather than one per statement.

---

### Upgrading from 0.3.x

The savepoint path changes what happens when `transaction()` is entered on a connection that **already** has uncommitted writes.

| | 0.3.x | 0.4.0 |
|---|-------|-------|
| Nested `transaction()` on success (SQLite) | `conn.commit()` — committed the caller's pending writes too | `RELEASE SAVEPOINT`; the enclosing owner commits |
| Nested `transaction()` on exception (SQLite) | `conn.rollback()` — discarded the caller's pending writes too | `ROLLBACK TO SAVEPOINT` — only the block's own writes |
| PostgreSQL | Connection-wide commit / rollback | Unchanged |

The pattern that breaks is using `transaction()` as a durability checkpoint after bare `execute()` writes:

```python
execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "X"))
# conn.in_transaction is now True

with transaction(conn):                    # 0.3.x: committed BOTH rows here
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/y", "Y"))
# 0.4.0: nothing is committed — conn.in_transaction is still True
```

Two fixes, in order of preference:

```python
# Preferred: open the transaction first so it owns the whole unit of work.
with transaction(conn):
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "X"))
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/y", "Y"))

# Or commit explicitly after the block.
execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "X"))
with transaction(conn):
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/y", "Y"))
conn.commit()
```

The same applies to [`run_migrations()`](#run_migrations): called with a transaction already open, its per-migration blocks join that transaction and nothing is committed until the caller commits.

---

## Migrations

Sequential, idempotent schema migrations tracked in a `schema_version` table. Each migration is a plain Python function that receives a DB-API connection.

```python
from bmlib.db import Migration, run_migrations
from bmlib.db.migrations import get_applied_versions
```

### `Migration`

```python
@dataclass
class Migration:
    version: int
    name: str
    up: Callable[[Any], None]
```

| Field | Type | Description |
|-------|------|-------------|
| `version` | `int` | Sequential integer (1, 2, 3, ...). Must be unique. |
| `name` | `str` | Short descriptive name, e.g. `"initial_schema"`. Recorded in `schema_version`. |
| `up` | `Callable[[Any], None]` | Applies the migration. Receives the connection; must not commit. |

---

### `run_migrations`

```python
def run_migrations(conn: Any, migrations: list[Migration]) -> int
```

Apply all pending migrations in version order. Creates the `schema_version` table if it does not exist, reads the applied versions, sorts the supplied migrations by `version`, and skips any already recorded.

Each pending migration runs inside a **single** `transaction(conn)` block that covers both `migration.up(conn)` and the `INSERT INTO schema_version` row — so a migration can never be recorded as applied unless its DDL succeeded, and vice versa. This is why [`create_tables()`](#create_tables) must not commit or `executescript()` its way out of the enclosing transaction.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection (sqlite3 or psycopg2). |
| `migrations` | `list[Migration]` | *(required)* | Migrations to consider. Order in the list does not matter; they are sorted by `version`. |

**Returns:** The number of migrations actually applied.

**Example:**

```python
from bmlib.db import Migration, connect_sqlite, create_tables, execute, run_migrations

def _m001_create_papers(conn):
    create_tables(conn, """
    CREATE TABLE papers (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        doi   TEXT UNIQUE,
        title TEXT NOT NULL
    );
    """)

def _m002_add_journal(conn):
    execute(conn, "ALTER TABLE papers ADD COLUMN journal TEXT")

MIGRATIONS = [
    Migration(1, "create_papers", _m001_create_papers),
    Migration(2, "add_journal", _m002_add_journal),
]

conn = connect_sqlite("~/.myapp/data.db")
applied = run_migrations(conn, MIGRATIONS)     # 2 on a fresh database
applied = run_migrations(conn, MIGRATIONS)     # 0 on the second run
```

---

### `get_applied_versions`

```python
def get_applied_versions(conn: Any) -> set[int]
```

Return the set of migration version numbers already recorded in `schema_version`. Returns an **empty set** — not an error — if the table does not exist yet. Handles both row types (`sqlite3.Row` index access and dict-style `row["version"]`).

Not re-exported from `bmlib.db`; import it from `bmlib.db.migrations`.

**Example:**

```python
from bmlib.db.migrations import get_applied_versions

pending = [m for m in MIGRATIONS if m.version not in get_applied_versions(conn)]
print(f"{len(pending)} migration(s) pending")
```

### The `schema_version` table

Created automatically on first use, with backend-appropriate DDL:

| Column | SQLite | PostgreSQL |
|--------|--------|------------|
| `version` | `INTEGER PRIMARY KEY` | `INTEGER PRIMARY KEY` |
| `name` | `TEXT NOT NULL` | `TEXT NOT NULL` |
| `applied_at` | `TEXT NOT NULL DEFAULT (datetime('now'))` | `TIMESTAMP NOT NULL DEFAULT NOW()` |

---

## Backend Differences

| Feature | SQLite | PostgreSQL |
|---------|--------|------------|
| Placeholder style | `?` | `%s` |
| Row type | `sqlite3.Row` (index + name access) | `RealDictRow` (dict access) |
| Connection factory | `connect_sqlite()` | `connect_postgresql()` |
| Schema execution | Statements split and executed one at a time | Whole script via one `cursor.execute()` |
| `create_tables()` commit | Only when no transaction is open | Always |
| Transaction begin | Explicit `BEGIN` | Implicit (autocommit off) |
| Nested `transaction()` | `SAVEPOINT bmlib_transaction`; outer owner commits | Not implemented — commits connection-wide |
| Migration timestamp default | `datetime('now')` | `NOW()` |
