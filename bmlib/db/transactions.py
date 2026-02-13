"""Transaction context manager."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

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
    """
    module_name = type(conn).__module__
    is_sqlite = "sqlite3" in module_name

    if is_sqlite:
        conn.execute("BEGIN")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
