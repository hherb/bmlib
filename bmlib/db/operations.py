# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Pure-function query helpers.

All functions take a DB-API connection as their first argument.  SQL is
passed in directly — callers are responsible for writing backend-appropriate
SQL (``?`` for SQLite, ``%s`` for PostgreSQL).

These helpers wrap the DB-API cursor pattern to provide a cleaner call
interface while remaining completely transparent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


def execute(conn: Any, sql: str, params: Sequence = ()) -> Any:
    """Execute a single statement and return the cursor.

    Useful for INSERT / UPDATE / DELETE where you might need
    ``cursor.lastrowid`` or ``cursor.rowcount``.
    """
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def executemany(conn: Any, sql: str, params_seq: Sequence[Sequence]) -> None:
    """Execute a statement for each parameter set in *params_seq*."""
    cur = conn.cursor()
    cur.executemany(sql, params_seq)


def fetch_one(conn: Any, sql: str, params: Sequence = ()) -> Any:
    """Execute and return the first row, or ``None``."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()


def fetch_all(conn: Any, sql: str, params: Sequence = ()) -> list[Any]:
    """Execute and return all rows."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def fetch_scalar(conn: Any, sql: str, params: Sequence = ()) -> Any:
    """Execute and return the first column of the first row, or ``None``."""
    row = fetch_one(conn, sql, params)
    if row is None:
        return None
    # Both sqlite3.Row and psycopg2 RealDictRow support index access.
    # sqlite3.Row has keys() but NOT values(), so index access is universal.
    try:
        return row[0]
    except (IndexError, KeyError):
        return None


def table_exists(conn: Any, name: str) -> bool:
    """Check whether a table exists (works on both SQLite and PostgreSQL)."""
    # Detect backend
    module_name = type(conn).__module__
    if "sqlite3" in module_name:
        row = fetch_one(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        )
    else:
        row = fetch_one(
            conn,
            "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
            (name,),
        )
    return row is not None


def _split_sql_statements(script: str) -> list[str]:
    """Split a multi-statement SQL script into individual statements.

    Splits on semicolons that are outside string literals and comments.
    Handles single/double-quoted strings (with SQL ``''`` escaping), ``--``
    line comments, and ``/* */`` block comments. This is sufficient for the
    plain ``CREATE TABLE``/``CREATE INDEX`` schemas used in this project; it
    does not attempt to parse trigger bodies (``BEGIN ... END;``).
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    quote: str | None = None
    while i < n:
        ch = script[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                # A doubled quote is an escaped quote, not a terminator.
                if i + 1 < n and script[i + 1] == quote:
                    buf.append(script[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        # Not inside a string literal.
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
        elif ch == "-" and i + 1 < n and script[i + 1] == "-":
            # Line comment: skip to end of line.
            j = script.find("\n", i)
            i = n if j == -1 else j
        elif ch == "/" and i + 1 < n and script[i + 1] == "*":
            # Block comment: skip to closing */.
            j = script.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def create_tables(conn: Any, schema_sql: str) -> None:
    """Execute a (possibly multi-statement) schema DDL string.

    Statements are executed individually via a cursor for both backends.
    SQLite's ``executescript()`` is deliberately avoided because it issues an
    implicit ``COMMIT`` before running, which would break the atomicity of a
    surrounding :func:`~bmlib.db.transactions.transaction` (e.g. during
    migrations). Executing statements one at a time keeps the DDL inside the
    active transaction — SQLite supports transactional DDL — so a failure
    rolls back cleanly.
    """
    module_name = type(conn).__module__
    if "sqlite3" in module_name:
        cur = conn.cursor()
        for stmt in _split_sql_statements(schema_sql):
            cur.execute(stmt)
        # Persist when called standalone, but leave commit to the caller when
        # an explicit transaction is active (e.g. the migration runner), so
        # the DDL stays atomic with the rest of that transaction.
        if not conn.in_transaction:
            conn.commit()
    else:
        cur = conn.cursor()
        cur.execute(schema_sql)
        conn.commit()
