#!/usr/bin/env python3
"""Dump bmlib.db's observable rules, for the Rust port.

`db/` is the one package with no corpus. What is diffable is what a *caller* can
see, not how the layer is built:

* `_split_sql_statements` — the one piece of `db/` that is pure string scanning,
  and the piece a trigger body or a comment containing `*` breaks quietly;
* `is_sqlite` / `placeholder` / `placeholders` — the dialect spellings;
* the value shapes a fetch returns, and which tables exist;
* and the part that matters most, **what a nested `transaction()` block commits
  when it succeeds and rolls back when it raises**. The port replaces Python's
  depth table with the type of the value in hand, so the *mechanism* has no
  counterpart; the outcomes must still agree.

A stateful case is a **step script** over one in-memory SQLite connection. The
result carries a trace, one entry per step, plus the tables that exist at the end
and what they hold — so a rollback is visible as state rather than inferred from a
return value.

Values cross as JSON: `NULL` as null, integers and reals as numbers, text as a
string, and a blob as `{"blob": "<hex>"}` (JSON has no bytes).
"""

from __future__ import annotations

import json
import sqlite3
import sys

from bmlib.db.backend import is_sqlite, placeholder, placeholders  # noqa: E402
from bmlib.db.migrations import Migration, get_applied_versions, run_migrations  # noqa: E402
from bmlib.db.operations import (  # noqa: E402
    _split_sql_statements,
    create_tables,
    execute,
    executemany,
    fetch_all,
    fetch_one,
    fetch_scalar,
    table_exists,
)
from bmlib.db.transactions import owns_commit, transaction  # noqa: E402


def as_json(value):
    """A database value as JSON."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"blob": bytes(value).hex()}
    return value


def row_as_json(row):
    """A row as a list of values, in column order.

    `sqlite3`'s default row factory is a tuple, so the order is the query's — and
    the port's `Row` keeps values in column order for the same reason.
    """
    if row is None:
        return None
    return [as_json(value) for value in row]


def rows_as_json(rows):
    return [row_as_json(row) for row in rows]


def state(conn):
    """Every table that exists, and its rows.

    Read through the same helpers a caller has, so the snapshot cannot see more
    than the layer under test does.
    """
    tables = {}
    names = fetch_all(
        conn,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name",
    )
    for (name,) in names:
        if name == "schema_version":
            # `applied_at` defaults to `datetime('now')`, so it differs between
            # the two dumps by construction and would fail on every run for no
            # reason. What a migration run is judged by is which versions it
            # recorded, under which names.
            tables[name] = rows_as_json(
                fetch_all(conn, "SELECT version, name FROM schema_version ORDER BY version")
            )
        else:
            tables[name] = rows_as_json(fetch_all(conn, f'SELECT * FROM "{name}"'))
    return tables


def from_json(value):
    """A scripted JSON value as a database parameter, the inverse of `as_json`."""
    if isinstance(value, dict) and set(value) == {"blob"}:
        return bytes.fromhex(value["blob"])
    if isinstance(value, list):
        return [from_json(item) for item in value]
    return value


def run_step(conn, step):
    """Run one scripted step, returning what a caller can see of it."""
    op = step["op"]
    params = tuple(from_json(value) for value in step.get("params", []))
    if op == "execute":
        cur = execute(conn, step["sql"], params)
        # `cursor.rowcount` is -1 for a statement that changes no rows — which is
        # every DDL statement — so the trace carries it as it is.
        return {"rowcount": cur.rowcount}
    if op == "executemany":
        executemany(
            conn,
            step["sql"],
            [tuple(from_json(value) for value in row) for row in step["rows"]],
        )
        return None
    if op == "fetch_one":
        return row_as_json(fetch_one(conn, step["sql"], params))
    if op == "fetch_all":
        return rows_as_json(fetch_all(conn, step["sql"], params))
    if op == "fetch_scalar":
        return as_json(fetch_scalar(conn, step["sql"], params))
    if op == "table_exists":
        return table_exists(conn, step["name"])
    if op == "create_tables":
        create_tables(conn, step["sql"])
        return None
    if op == "owns_commit":
        return owns_commit(conn)
    if op == "transaction":
        return run_transaction(conn, step)
    raise ValueError(f"unknown op {op!r}")


def run_transaction(conn, step):
    """Run a scripted block, nested as the script says, and report its trace.

    `fail` makes the block raise **after** its steps have run, which is the shape
    a rollback is decided by: on the outermost block it rolls the whole thing
    back, and inside another one it rolls back to the savepoint and leaves the
    outer block's writes pending.
    """
    trace = []
    try:
        with transaction(conn):
            for inner in step.get("steps", []):
                trace.append(run_step(conn, inner))
            if step.get("fail"):
                raise RuntimeError("scripted failure")
    except RuntimeError as exc:
        trace.append({"raised": f"{type(exc).__name__}: {exc}"})
        if not step.get("raised_ok"):
            return trace
    return trace


def run(case):
    fn = case["fn"]
    args = case.get("args", {})
    if fn == "split":
        return _split_sql_statements(args["script"])
    if fn == "dialect":
        conn = sqlite3.connect(":memory:")
        try:
            return {
                "is_sqlite": is_sqlite(conn),
                "placeholder": placeholder(conn),
                "placeholders": [placeholders(conn, n) for n in args["counts"]],
            }
        finally:
            conn.close()
    if fn == "script":
        conn = sqlite3.connect(":memory:")
        try:
            trace = [run_step(conn, step) for step in args["steps"]]
            return {"trace": trace, "tables": state(conn)}
        finally:
            conn.close()
    if fn == "migrations":
        conn = sqlite3.connect(":memory:")
        try:
            migrations = [
                Migration(version=m["version"], name=m["name"], up=_sql_migration(m["sql"]))
                for m in args["migrations"]
            ]
            runs = [run_migrations(conn, migrations) for _ in range(args.get("runs", 1))]
            return {
                "runs": runs,
                "versions": sorted(get_applied_versions(conn)),
                "tables": state(conn),
            }
        finally:
            conn.close()
    raise ValueError(f"unknown fn {fn!r}")


def _sql_migration(sql):
    """One migration, as a function running a script.

    The port's `Migration` holds a closure, so a corpus that named a *function*
    could not be replayed on both sides; naming SQL can.
    """

    def apply(conn):
        for statement in _split_sql_statements(sql):
            conn.execute(statement)

    return apply


def main() -> int:
    cases = json.load(sys.stdin)
    out = []
    for case in cases:
        try:
            out.append({"name": case["name"], "ok": True, "value": run(case)})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": case["name"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    json.dump(out, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
