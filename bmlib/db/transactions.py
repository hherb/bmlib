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

"""Transaction context manager."""

from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from bmlib.db.backend import is_sqlite

logger = logging.getLogger(__name__)

_SAVEPOINT = "bmlib_transaction"

# How many :func:`transaction` blocks the calling thread currently has open on
# a connection. Only PostgreSQL needs this: psycopg2 opens a transaction on the
# *first statement of any kind* — a bare ``SELECT`` leaves the connection
# INTRANS — so the driver's own transaction status cannot tell "someone called
# transaction() around me" from "someone ran a query". Getting that wrong would
# silently stop committing. SQLite is not consulted here; its
# ``conn.in_transaction`` answers the same question directly.
#
# Keyed by *(thread, connection)*, not by connection alone. Nesting is a
# property of one call stack: "am I inside another transaction() block?" can
# only be answered about the thread asking. Keying by connection alone would
# let a block on thread A make an unrelated outermost block on thread B look
# nested, so B would open a savepoint and never commit — losing B's writes
# silently. Sharing one connection across threads is still not something either
# backend makes safe (interleaved statements land in one server-side
# transaction), but per-thread counting keeps each thread's commit behaviour
# what it would be on its own connection.
#
# The connection cannot be keyed on directly — psycopg2's connection is a C
# type that rejects attribute assignment, and ``sqlite3.Connection`` supports
# neither weak references nor useful equality — so the key holds ``id(conn)``
# and the *value* holds a strong reference to the connection. That reference is
# what makes the id trustworthy: while an entry exists the connection cannot be
# collected, so its id cannot be recycled onto a different connection. Entries
# are dropped as the outermost block exits.
_depths: dict[tuple[int, int], tuple[Any, int]] = {}
_depths_lock = threading.Lock()


def _depth_key(conn: Any) -> tuple[int, int]:
    """Return the ``_depths`` key for *conn* in the calling thread."""
    return (threading.get_ident(), id(conn))


def transaction_depth(conn: Any) -> int:
    """Return how many :func:`transaction` blocks the calling thread has open.

    Zero means the next :func:`transaction` block on this thread owns the
    commit. Blocks opened by other threads are not counted — see
    :func:`transaction`.
    """
    with _depths_lock:
        return _depths.get(_depth_key(conn), (None, 0))[1]


@contextmanager
def _depth_tracked(conn: Any) -> Generator[None, None, None]:
    """Count one open :func:`transaction` block for *conn* on this thread."""
    key = _depth_key(conn)
    with _depths_lock:
        _depths[key] = (conn, _depths.get(key, (conn, 0))[1] + 1)
    try:
        yield
    finally:
        with _depths_lock:
            remaining = _depths.get(key, (conn, 1))[1] - 1
            if remaining > 0:
                _depths[key] = (conn, remaining)
            else:
                _depths.pop(key, None)


def owns_commit(conn: Any) -> bool:
    """Return True if a write on *conn* right now would need its own commit.

    False means the caller is inside a :func:`transaction` block that will
    commit on its way out, so an inner helper must not commit on its own.
    """
    return transaction_depth(conn) == 0


def _is_nested(conn: Any) -> bool:
    """Return True if this :func:`transaction` block is inside another one."""
    if is_sqlite(conn):
        # sqlite3 auto-begins only before DML, so pending writes here really do
        # mean an enclosing transaction whose owner will commit.
        return bool(conn.in_transaction)
    return transaction_depth(conn) > 0


def _run(conn: Any, sql: str) -> None:
    """Execute a bare statement on either backend.

    ``sqlite3.Connection`` has a convenience ``execute()``; psycopg2's does
    not, so savepoint control has to go through a cursor there.
    """
    if is_sqlite(conn):
        conn.execute(sql)
    else:
        conn.cursor().execute(sql)


@contextmanager
def transaction(conn: Any) -> Generator[Any, None, None]:
    """Context manager that commits on success, rolls back on exception.

    Usage::

        with transaction(conn):
            execute(conn, "INSERT INTO ...")
            execute(conn, "UPDATE ...")
        # auto-committed here

    For SQLite, ``conn.execute("BEGIN")`` is issued explicitly so that
    ``conn.commit()`` has a well-defined scope.  For PostgreSQL (psycopg2),
    autocommit is off and a transaction begins implicitly with the first
    statement, so the outermost block just commits or rolls back.

    Nesting: entering a block while another is already open runs the inner one
    inside a ``SAVEPOINT``. On exception only the inner block's writes are
    rolled back — the outer block's pending writes survive, still uncommitted.
    On success the savepoint is released and **no commit is issued**: whoever
    opened the outermost block owns the commit. This is what makes nesting
    composable — a batch loop can wrap many ``transaction()``-using calls in
    one outer ``transaction()`` and pay a single commit, and an outer failure
    rolls back the inner blocks' writes too. :func:`bmlib.publications.sync`
    depends on it for one-commit-per-day batching.

    How "already open" is decided differs by backend, and deliberately so.
    SQLite auto-begins only before DML, so ``conn.in_transaction`` means what
    it says. psycopg2 begins a transaction on the first statement of any kind
    — a bare ``SELECT`` is enough — so its transaction status would report
    "already open" for a connection nobody has wrapped, and every write would
    quietly stop committing. PostgreSQL therefore counts bmlib's own open
    blocks (see :func:`transaction_depth`) instead of asking the driver.

    Threads: the count is per *(thread, connection)*, because nesting is a
    property of one call stack. A block open on another thread therefore never
    makes this block look nested, and each thread commits its own work as if it
    held the connection alone. That is not a licence to share a connection
    between threads — interleaved statements still land in one server-side
    transaction, on either backend — but it keeps the failure mode from being
    silently dropped writes.

    Reusing one savepoint name at every level is safe: ``ROLLBACK TO`` and
    ``RELEASE`` address the *most recent* savepoint of that name, which —
    because the blocks are strictly nested — is always this block's own.
    """
    if _is_nested(conn):
        # Join the enclosing transaction via a savepoint so an exception rolls
        # back only this block's writes (see docstring).
        _run(conn, f"SAVEPOINT {_SAVEPOINT}")
        with _depth_tracked(conn):
            try:
                yield conn
            except Exception:
                _run(conn, f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
                _run(conn, f"RELEASE SAVEPOINT {_SAVEPOINT}")
                raise
            _run(conn, f"RELEASE SAVEPOINT {_SAVEPOINT}")
        return

    if is_sqlite(conn):
        conn.execute("BEGIN")

    with _depth_tracked(conn):
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
