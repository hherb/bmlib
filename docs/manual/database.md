# bmlib.db — Database Abstraction

Thin database abstraction layer providing pure functions over standard DB-API 2.0 connections. Supports SQLite (built-in) and PostgreSQL (optional, via psycopg2).

All functions take a DB-API connection as their first argument. SQL is passed directly — callers are responsible for writing backend-appropriate SQL (`?` for SQLite, `%s` for PostgreSQL).

## Installation

SQLite support is built-in. For PostgreSQL:

```bash
pip install bmlib[postgresql]
```

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

**Returns:** `sqlite3.Connection` with `row_factory` set to `sqlite3.Row` (rows accessible by column name).

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

**Raises:** `ImportError` if psycopg2 is not installed.

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

Check whether a table exists. Automatically detects the database backend (SQLite or PostgreSQL) and uses the appropriate system catalog query.

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

Execute a (possibly multi-statement) schema DDL string. For SQLite, the entire string is executed via `executescript()`. For PostgreSQL, statements are executed within an implicit transaction.

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
create_tables(conn, SCHEMA)
```

---

## Transactions

### `transaction`

```python
@contextmanager
def transaction(conn: Any) -> Generator[Any, None, None]
```

Context manager that commits on success and rolls back on exception.

For SQLite, `BEGIN` is issued explicitly so that `conn.commit()` has a well-defined scope. For PostgreSQL (psycopg2), autocommit is off by default so it simply calls `commit()` or `rollback()`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |

**Yields:** The connection itself (for convenience).

**Example:**

```python
with transaction(conn):
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/a", "Paper A"))
    execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/b", "Paper B"))
# Both inserts are committed atomically.
# If either raises, both are rolled back.
```

#### Joining an already-open transaction (SQLite) — changed in 0.3.0

sqlite3 auto-begins a transaction before DML, so the connection may already
hold uncommitted writes when a `transaction()` block is entered. In that case
the block **joins** the open transaction via a `SAVEPOINT` instead of issuing
`BEGIN`:

- On exception, only the block's own writes are rolled back — the caller's
  pre-existing pending writes survive, still uncommitted.
- On success, the savepoint is released and **no commit is issued** — whoever
  opened the enclosing transaction owns the commit.

This makes nesting composable: a batch loop can wrap many
`transaction()`-using calls in one outer `transaction()` and pay a single
commit, and an outer failure rolls back the inner blocks' writes too.

```python
with transaction(conn):                  # outer block owns the commit
    for pub in publications:
        with transaction(conn):          # joins via savepoint, does NOT commit
            store_publication(conn, pub)
# Everything commits here, once.
```

**Breaking change (0.3.0):** previously a `transaction()` block entered while
the connection already held uncommitted writes committed on success — taking
the caller's pending writes with it. Code that relied on `transaction()` as a
durability checkpoint after bare `execute()` writes must now commit
explicitly (or wrap the whole batch in an outer `transaction()`). The same
applies to `run_migrations()` when called with a transaction already open —
it uses `transaction()` internally, so the caller's enclosing transaction
owns the commit. On PostgreSQL the old connection-wide commit behaviour is
unchanged (no savepoint nesting is implemented there).

---

## Migrations

### `Migration`

```python
@dataclass
class Migration:
    version: int                  # sequential integer (1, 2, 3, ...), unique
    name: str                     # short descriptive name, e.g. "initial_schema"
    up: Callable[[Any], None]     # takes a DB-API connection, applies the DDL
```

A single database migration.

---

### `run_migrations`

```python
def run_migrations(conn: Any, migrations: list[Migration]) -> int
```

Apply all pending migrations in version order. Applied versions are tracked
in a `schema_version` table (created on first run). Each migration runs
inside `transaction()`, so a failed migration is rolled back atomically and
re-running is safe — already-applied versions are skipped.

**Returns:** Number of migrations applied.

**Example:**

```python
from bmlib.db import Migration, connect_sqlite, create_tables, run_migrations

def _m001_initial(conn):
    create_tables(conn, "CREATE TABLE IF NOT EXISTS papers (id INTEGER PRIMARY KEY, doi TEXT);")

def _m002_add_title(conn):
    execute(conn, "ALTER TABLE papers ADD COLUMN title TEXT")

MIGRATIONS = [
    Migration(1, "initial_schema", _m001_initial),
    Migration(2, "add_title", _m002_add_title),
]

conn = connect_sqlite("~/.myapp/data.db")
applied = run_migrations(conn, MIGRATIONS)
```

Because each migration runs inside `transaction()`, calling
`run_migrations()` while the connection already holds an open SQLite
transaction joins it via a savepoint — the caller's enclosing transaction
then owns the commit (see [`transaction`](#transaction), changed in 0.3.0).

The module also provides `get_applied_versions(conn) -> set[int]`
(importable from `bmlib.db.migrations`) to inspect which versions have been
applied.

---

## Backend Differences

| Feature | SQLite | PostgreSQL |
|---------|--------|------------|
| Placeholder style | `?` | `%s` |
| Row type | `sqlite3.Row` (index + name access) | `RealDictRow` (dict access) |
| Connection factory | `connect_sqlite()` | `connect_postgresql()` |
| Schema execution | `executescript()` | `cursor.execute()` + `commit()` |
| Transaction begin | Explicit `BEGIN` | Implicit (autocommit off) |
| Nested `transaction()` | Joins via `SAVEPOINT`; outer block owns the commit | Not implemented — inner block commits connection-wide |
