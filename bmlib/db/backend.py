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

"""Backend detection for code that must write SQL for both backends.

There is no ORM here: every module writes its own SQL, so anything shared
between SQLite and PostgreSQL needs to know which dialect it is talking to.
That question is answered by the connection's module name, which is the only
thing both drivers agree on without importing the optional ``psycopg2``.

Usage::

    from bmlib.db import placeholder

    ph = placeholder(conn)
    fetch_one(conn, f"SELECT * FROM publications WHERE doi = {ph}", (doi,))
"""

from __future__ import annotations

from typing import Any


def is_sqlite(conn: Any) -> bool:
    """Return True if *conn* is a :mod:`sqlite3` connection.

    Anything else is treated as PostgreSQL — the only other backend
    :mod:`bmlib.db` opens connections for.
    """
    return "sqlite3" in type(conn).__module__


def placeholder(conn: Any) -> str:
    """Return the parameter placeholder this connection's driver expects.

    ``"?"`` for SQLite (qmark paramstyle), ``"%s"`` for psycopg2 (format
    paramstyle).
    """
    return "?" if is_sqlite(conn) else "%s"


def placeholders(conn: Any, count: int) -> str:
    """Return *count* comma-separated placeholders for an ``IN`` clause.

    Returns an empty string for ``count <= 0``; callers building an ``IN
    (...)`` list must skip the clause entirely in that case, since neither
    backend accepts an empty list.
    """
    if count <= 0:
        return ""
    return ", ".join([placeholder(conn)] * count)
