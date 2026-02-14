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

"""Thin database abstraction — pure functions over DB-API connections.

Supports SQLite (built-in) and PostgreSQL (optional, via psycopg2).

Usage::

    from bmlib.db import connect_sqlite, fetch_all, execute, transaction

    conn = connect_sqlite("~/.myapp/data.db")
    with transaction(conn):
        execute(conn, "INSERT INTO papers (doi, title) VALUES (?, ?)", ("10.1101/x", "A paper"))
    rows = fetch_all(conn, "SELECT * FROM papers")
"""

from bmlib.db.connection import connect_postgresql, connect_sqlite
from bmlib.db.operations import (
    create_tables,
    execute,
    executemany,
    fetch_all,
    fetch_one,
    fetch_scalar,
    table_exists,
)
from bmlib.db.migrations import Migration, run_migrations
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
    "Migration",
    "run_migrations",
]
