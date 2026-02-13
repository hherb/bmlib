"""Thin database abstraction — pure functions over DB-API connections.

Supports SQLite (built-in) and PostgreSQL (optional, via psycopg2).

Usage::

    from bmlib.db import connect_sqlite, fetch_all, execute, transaction

    conn = connect_sqlite("~/.myapp/data.db")
    with transaction(conn):
        execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "A paper"))
    rows = fetch_all(conn, "SELECT * FROM papers")
"""

from bmlib.db.connection import connect_sqlite, connect_postgresql
from bmlib.db.operations import (
    execute,
    executemany,
    fetch_one,
    fetch_all,
    fetch_scalar,
    table_exists,
    create_tables,
)
from bmlib.db.transactions import transaction

__all__ = [
    "connect_sqlite",
    "connect_postgresql",
    "execute",
    "executemany",
    "fetch_one",
    "fetch_all",
    "fetch_scalar",
    "table_exists",
    "create_tables",
    "transaction",
]
