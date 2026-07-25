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

"""Shared fixtures — notably the two-backend database fixture.

SQLite runs everywhere and needs no setup. PostgreSQL needs a live server, so
it is opt-in: set ``BMLIB_TEST_POSTGRESQL_DSN`` to a DSN for a database the
tests may freely drop tables in, e.g.::

    BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/pgrun port=5432 dbname=bmlib_test user=postgres" \\
        uv run pytest tests/

Without it the PostgreSQL parameterisation skips, so the suite stays green on
a machine with no server.

CI sets ``BMLIB_REQUIRE_POSTGRESQL=1`` alongside the DSN. That turns the skip
into a failure, so a DSN that is missing or points at an unreachable server
cannot leave the PostgreSQL half quietly unrun behind a green build.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from bmlib.db import connect_postgresql, connect_sqlite, execute, fetch_all, transaction

POSTGRESQL_DSN_ENV = "BMLIB_TEST_POSTGRESQL_DSN"
POSTGRESQL_REQUIRED_ENV = "BMLIB_REQUIRE_POSTGRESQL"


def postgresql_dsn() -> str | None:
    """Return the configured PostgreSQL test DSN, or None if unset."""
    return os.environ.get(POSTGRESQL_DSN_ENV) or None


def postgresql_required() -> bool:
    """Return True if a missing PostgreSQL server must fail rather than skip."""
    return os.environ.get(POSTGRESQL_REQUIRED_ENV, "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def _fresh_postgresql_conn() -> Any:
    """Open a PostgreSQL connection with the public schema emptied.

    Every table is dropped, not just the ones bmlib creates, so a test's own
    scratch tables cannot leak into the next one — the clean slate an
    in-memory SQLite connection gets for free.
    """
    conn = connect_postgresql(dsn=postgresql_dsn())
    with transaction(conn):
        rows = fetch_all(
            conn,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
        )
        for row in rows:
            execute(conn, f'DROP TABLE IF EXISTS "{row["tablename"]}" CASCADE')
    return conn


@pytest.fixture(params=["sqlite", "postgresql"])
def backend_conn(request: pytest.FixtureRequest) -> Any:
    """An empty database connection, once per supported backend.

    Tests using this fixture run twice: against in-memory SQLite, and against
    PostgreSQL when a DSN is configured.
    """
    if request.param == "sqlite":
        conn = connect_sqlite(":memory:")
        yield conn
        conn.close()
        return

    if not postgresql_dsn():
        if postgresql_required():
            pytest.fail(
                f"{POSTGRESQL_REQUIRED_ENV} is set but {POSTGRESQL_DSN_ENV} is empty — "
                "the PostgreSQL backend would have been skipped silently"
            )
        pytest.skip(f"{POSTGRESQL_DSN_ENV} not set — skipping PostgreSQL backend")

    conn = _fresh_postgresql_conn()
    try:
        yield conn
    finally:
        conn.close()
