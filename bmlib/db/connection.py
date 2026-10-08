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

"""Database connection factories.

Each function returns a standard DB-API 2.0 connection.  SQLite uses the
built-in ``sqlite3`` module; PostgreSQL uses ``psycopg2`` (optional
dependency).
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def connect_sqlite(
    path: str | Path,
    *,
    wal_mode: bool = True,
    foreign_keys: bool = True,
) -> sqlite3.Connection:
    """Open (or create) a SQLite database and return a connection.

    Args:
        path: File path (``":memory:"`` for in-memory).
        wal_mode: Enable WAL journal mode for better concurrent access.
        foreign_keys: Enforce foreign key constraints.
    """
    path = str(Path(path).expanduser()) if path != ":memory:" else ":memory:"

    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    # Legacy transaction control is the mode bmlib.db.transaction() supports.
    # It is requested by name because it is only sqlite3's *current* default:
    # Python has announced the default will become autocommit=False, in which
    # transaction() would refuse every connection opened here (#449's review).
    if sys.version_info >= (3, 12):
        # typeshed annotates `autocommit` as bool, but the documented value for
        # legacy control is this int constant (-1), so no overload matches.
        conn = sqlite3.connect(  # type: ignore[call-overload]
            path,
            check_same_thread=False,
            autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
        )
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    if wal_mode and path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")

    logger.debug("SQLite connection opened: %s", path)
    return conn


def connect_postgresql(
    dsn: str | None = None,
    *,
    host: str = "localhost",
    port: int = 5432,
    database: str = "bmlib",
    user: str = "bmlib",
    password: str = "",
) -> Any:
    """Open a PostgreSQL connection via psycopg2.

    Either provide a full *dsn* string, or individual parameters.

    Returns:
        A ``psycopg2`` connection with ``RealDictCursor`` as the default
        cursor factory.
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError("psycopg2 not installed. Install with: pip install bmlib[postgresql]")

    if dsn:
        conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    logger.debug("PostgreSQL connection opened: %s:%s/%s", host, port, database)
    return conn
