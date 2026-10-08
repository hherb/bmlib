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


class TransactionModeError(ValueError):
    """*conn* is in a mode :func:`transaction` cannot honour (#449).

    A ``ValueError``, since that is what :func:`require_transaction_control`
    raised before the type existed. It is its own type so that a handler
    written to catch a *task's* failure can let this one through: it is a
    property of the connection, and every later block on it will fail the same
    way.
    """


def require_transaction_control(conn: Any) -> None:
    """Raise :class:`TransactionModeError` if :func:`transaction` cannot honour *conn*'s mode.

    :func:`transaction` relies on each driver's default transaction handling,
    so it cannot keep its promise on a connection set up any other way, and
    every one of those ways fails silently (#449):

    - **psycopg2, ``autocommit = True``.** Every statement commits on its own.
      A failure cannot roll the block back, and a transaction-scoped lock such
      as ``pg_advisory_xact_lock`` is released at the end of its own
      statement. Only a *nested* block fails loudly, because ``SAVEPOINT``
      outside a transaction block is an error.
    - **sqlite3, ``autocommit=True``** (Python 3.12+). ``commit()`` and
      ``rollback()`` do nothing, so the explicit ``BEGIN`` is never ended. The
      block never commits, and it holds the write lock until the connection
      closes.
    - **sqlite3, ``autocommit=False``** (Python 3.12+, PEP 249 mode). A
      transaction is open at all times, so ``in_transaction`` cannot tell the
      outermost block from a nested one. The block opens a savepoint and never
      commits, and it too holds the write lock until the connection closes.

    sqlite3's default *legacy transaction control* is the mode it supports,
    whatever the ``isolation_level``. Python 3.11's ``sqlite3`` has no
    ``autocommit`` attribute and offers no other mode.

    Any connection that is not ``sqlite3``'s is taken for psycopg2's, as
    everywhere in :mod:`bmlib.db`, and is accepted only if its ``autocommit``
    is ``False``. A connection that reports anything else, or has no such
    attribute, cannot be shown to make a block atomic, so it is refused
    rather than trusted.

    :func:`~bmlib.db.connect_sqlite` and :func:`~bmlib.db.connect_postgresql`
    both open connections this accepts, ``connect_sqlite()`` by asking for
    legacy control explicitly rather than relying on ``sqlite3``'s default,
    which Python has announced will change to ``autocommit=False``.

    :func:`transaction` calls this at every level and again as the outermost
    block exits, and :func:`~bmlib.db.create_tables` calls it before any DDL.
    A caller that does network or other costly work before its first block can
    call it up front, as :func:`bmlib.publications.sync` does, so the refusal
    comes before that work and not after it.

    Raises:
        TransactionModeError: *conn* is in a mode :func:`transaction` cannot
            honour.
    """
    mode = getattr(conn, "autocommit", None)
    # Identity, not equality or truthiness: only a real bool is a mode named
    # here. sqlite3 accepts no other value for `autocommit` but its legacy
    # constant (-1), and a non-bool on another driver is refused below.
    if is_sqlite(conn):
        if mode is True:
            raise TransactionModeError(
                "transaction() needs sqlite3's legacy transaction control, but this"
                " connection was opened with autocommit=True: commit() and rollback()"
                " would do nothing, so the block would never commit and would hold the"
                " write lock until the connection closed. Open it without autocommit="
                " (connect_sqlite() does)."
            )
        if mode is False:
            raise TransactionModeError(
                "transaction() needs sqlite3's legacy transaction control, but this"
                " connection was opened with autocommit=False, which keeps a transaction"
                " open at all times: the outermost block would be taken for a nested one"
                " and would never commit, and would hold the write lock until the"
                " connection closed. Open it without autocommit= (connect_sqlite() does)."
            )
        return
    if mode is True:
        raise TransactionModeError(
            "transaction() cannot make a block atomic on a connection with autocommit"
            " on: every statement would commit on its own, so a failure could not be"
            " rolled back and a transaction-scoped lock would be released at the end"
            " of its own statement. Set conn.autocommit = False."
        )
    if mode is not False:
        raise TransactionModeError(
            "transaction() takes any connection that is not sqlite3's for psycopg2's"
            f" and needs its autocommit off, but this {type(conn).__name__} reports"
            f" autocommit={mode!r}, so it cannot be shown to make a block atomic."
            " Pass a psycopg2 connection with conn.autocommit = False"
            " (connect_postgresql() opens one)."
        )


def _why_the_block_was_not_atomic(conn: Any) -> str | None:
    """Return why an outermost block's transaction did not last to its exit, or None.

    :func:`require_transaction_control` runs as the block is entered, so a mode
    changed *inside* the block would otherwise go unseen. On sqlite3 a switch
    to ``autocommit=True`` or ``isolation_level = None`` commits the writes
    before it and lets every later one commit on its own, and psycopg2's
    switch to autocommit does the latter, so the ``rollback()`` on the way
    out restores none of them (#449's review). sqlite3 is also asked whether
    the ``BEGIN`` this block issued is still open, which catches an
    ``isolation_level = None`` set inside it. That is not a test for every
    way to end a transaction early: under the default ``isolation_level`` a
    ``commit()`` followed by more DML begins a new transaction implicitly,
    which looks the same as the old one.
    """
    try:
        require_transaction_control(conn)
    except TransactionModeError as exc:
        return f"the connection's transaction mode was changed inside it ({exc})"
    if is_sqlite(conn) and not conn.in_transaction:
        return (
            "the transaction it began was no longer open at its end (an"
            " isolation_level change, commit(), rollback() or executescript()"
            " inside the block ends it)"
        )
    return None


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
    autocommit must be off, and a transaction begins implicitly with the first
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

    A connection in a mode this cannot honour — psycopg2 with autocommit on,
    or Python 3.12+ ``sqlite3`` opened with ``autocommit=`` either way — is
    refused with :class:`TransactionModeError` (a ``ValueError``) before the
    block runs, at every level. See :func:`require_transaction_control`.

    The outermost block checks again as it exits, because a mode changed
    *inside* it can leave the block non-atomic, and a ``rollback()`` cannot
    restore what has already committed. A block found that way rolls back
    whatever is still pending and raises ``RuntimeError`` rather than
    returning as if it had committed, chained to the block's own exception
    when there was one.
    """
    require_transaction_control(conn)
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
        except Exception as exc:
            broken = _why_the_block_was_not_atomic(conn)
            conn.rollback()
            if broken is not None:
                raise RuntimeError(
                    f"transaction() block failed, and its writes may not all have been"
                    f" rolled back: {broken}. Anything committed inside the block stays"
                    " committed."
                ) from exc
            raise
        broken = _why_the_block_was_not_atomic(conn)
        if broken is not None:
            conn.rollback()
            raise RuntimeError(
                f"transaction() block was not committed as one unit: {broken}. What was"
                " still pending has been rolled back; anything committed inside the"
                " block stays committed."
            )
        try:
            conn.commit()
        except Exception:
            conn.rollback()
            raise
