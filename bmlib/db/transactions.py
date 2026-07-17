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
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


@contextmanager
def transaction(conn: Any) -> Generator[Any, None, None]:
    """Context manager that commits on success, rolls back on exception.

    Usage::

        with transaction(conn):
            execute(conn, "INSERT INTO ...")
            execute(conn, "UPDATE ...")
        # auto-committed here

    For SQLite, ``conn.execute("BEGIN")`` is issued explicitly so
    that ``conn.commit()`` has a well-defined scope.  For PostgreSQL
    (psycopg2), autocommit is off by default so we simply call
    ``conn.commit()`` or ``conn.rollback()``.

    Joining an open transaction: sqlite3 auto-begins a transaction before
    DML, so the connection may already hold uncommitted writes when the block
    is entered (issuing ``BEGIN`` then would raise "cannot start a transaction
    within a transaction"). In that case the block runs inside a ``SAVEPOINT``:
    on exception only the block's own writes are rolled back — the caller's
    pre-existing pending writes survive, still uncommitted. On success the
    savepoint is released and **no commit is issued** — whoever opened the
    enclosing transaction owns the commit. This is what makes nesting
    composable: a batch loop can wrap many ``transaction()``-using calls in
    one outer ``transaction()`` and pay a single commit, and an outer failure
    rolls back the inner blocks' writes too. The PostgreSQL path commits
    connection-wide on success (it is always inside a transaction once a
    statement has run, with no savepoint nesting implemented).
    """
    module_name = type(conn).__module__
    is_sqlite = "sqlite3" in module_name

    if is_sqlite and conn.in_transaction:
        # Join the already-open transaction via a savepoint so an exception
        # rolls back only the block's writes; the enclosing transaction's
        # owner commits (see docstring).
        conn.execute("SAVEPOINT bmlib_transaction")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT bmlib_transaction")
            conn.execute("RELEASE SAVEPOINT bmlib_transaction")
            raise
        conn.execute("RELEASE SAVEPOINT bmlib_transaction")
        return

    if is_sqlite:
        conn.execute("BEGIN")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
