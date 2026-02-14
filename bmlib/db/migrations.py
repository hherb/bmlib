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

"""Idempotent database migration runner.

Provides a simple, sequential migration system that tracks applied
migrations in a ``schema_version`` table.  Each migration is a Python
function that receives a DB-API connection.

Usage::

    from bmlib.db.migrations import Migration, run_migrations

    def _m001_create_users(conn):
        create_tables(conn, "CREATE TABLE IF NOT EXISTS users (...);")

    MIGRATIONS = [Migration(1, "create_users", _m001_create_users)]

    run_migrations(conn, MIGRATIONS)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from bmlib.db.operations import create_tables, execute, fetch_all, table_exists
from bmlib.db.transactions import transaction

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    """A single database migration.

    Attributes:
        version: Sequential integer (1, 2, 3, ...). Must be unique.
        name: Short descriptive name (e.g. ``"initial_schema"``).
        up: Callable that takes a DB-API connection and applies the DDL.
    """

    version: int
    name: str
    up: Callable[[Any], None]


def _is_sqlite(conn: Any) -> bool:
    """Return True if the connection is SQLite."""
    return "sqlite3" in type(conn).__module__


def _placeholder(conn: Any) -> str:
    """Return the correct parameter placeholder for this connection."""
    return "?" if _is_sqlite(conn) else "%s"


def _ensure_version_table(conn: Any) -> None:
    """Create the ``schema_version`` table if it does not exist."""
    if table_exists(conn, "schema_version"):
        return

    if _is_sqlite(conn):
        ddl = """\
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
    else:
        ddl = """\
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""
    create_tables(conn, ddl)
    logger.info("Created schema_version table")


def get_applied_versions(conn: Any) -> set[int]:
    """Return the set of migration version numbers already applied.

    Returns an empty set if the ``schema_version`` table does not exist.
    """
    if not table_exists(conn, "schema_version"):
        return set()

    rows = fetch_all(conn, "SELECT version FROM schema_version")
    return {r[0] if not isinstance(r, dict) else r["version"] for r in rows}


def run_migrations(conn: Any, migrations: list[Migration]) -> int:
    """Apply all pending migrations in version order.

    Args:
        conn: A DB-API connection (sqlite3 or psycopg2).
        migrations: Ordered list of ``Migration`` objects.

    Returns:
        Number of migrations applied.
    """
    _ensure_version_table(conn)
    applied = get_applied_versions(conn)
    ph = _placeholder(conn)

    count = 0
    for migration in sorted(migrations, key=lambda m: m.version):
        if migration.version in applied:
            continue

        logger.info(
            "Applying migration %d: %s", migration.version, migration.name
        )
        with transaction(conn):
            migration.up(conn)
            execute(
                conn,
                f"INSERT INTO schema_version (version, name) VALUES ({ph}, {ph})",
                (migration.version, migration.name),
            )
        count += 1

    if count:
        logger.info("Applied %d migration(s)", count)
    return count
