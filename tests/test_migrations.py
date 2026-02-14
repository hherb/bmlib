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

"""Tests for bmlib.db.migrations — idempotent migration runner."""

from __future__ import annotations

from bmlib.db import (
    connect_sqlite,
    create_tables,
    table_exists,
    fetch_scalar,
)
from bmlib.db.migrations import Migration, get_applied_versions, run_migrations


def _mem():
    return connect_sqlite(":memory:")


def _m1(conn):
    create_tables(conn, "CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY);")


def _m2(conn):
    create_tables(conn, "CREATE TABLE IF NOT EXISTS t2 (id INTEGER PRIMARY KEY);")


SAMPLE_MIGRATIONS = [
    Migration(1, "create_t1", _m1),
    Migration(2, "create_t2", _m2),
]


class TestRunMigrations:
    def test_applies_all_on_fresh_db(self):
        conn = _mem()
        count = run_migrations(conn, SAMPLE_MIGRATIONS)
        assert count == 2
        assert table_exists(conn, "t1")
        assert table_exists(conn, "t2")
        assert table_exists(conn, "schema_version")

    def test_idempotent_second_call(self):
        conn = _mem()
        run_migrations(conn, SAMPLE_MIGRATIONS)
        count = run_migrations(conn, SAMPLE_MIGRATIONS)
        assert count == 0

    def test_applies_only_pending(self):
        conn = _mem()
        run_migrations(conn, [SAMPLE_MIGRATIONS[0]])
        assert table_exists(conn, "t1")
        assert not table_exists(conn, "t2")

        count = run_migrations(conn, SAMPLE_MIGRATIONS)
        assert count == 1
        assert table_exists(conn, "t2")

    def test_empty_migration_list(self):
        conn = _mem()
        count = run_migrations(conn, [])
        assert count == 0
        assert table_exists(conn, "schema_version")


class TestGetAppliedVersions:
    def test_empty_on_fresh_db(self):
        conn = _mem()
        versions = get_applied_versions(conn)
        assert versions == set()

    def test_returns_applied_versions(self):
        conn = _mem()
        run_migrations(conn, SAMPLE_MIGRATIONS)
        versions = get_applied_versions(conn)
        assert versions == {1, 2}


class TestMigrationOrdering:
    def test_out_of_order_list_still_applies_in_order(self):
        conn = _mem()
        reversed_list = list(reversed(SAMPLE_MIGRATIONS))
        count = run_migrations(conn, reversed_list)
        assert count == 2
        versions = get_applied_versions(conn)
        assert versions == {1, 2}


class TestVersionTableTracking:
    def test_version_names_recorded(self):
        conn = _mem()
        run_migrations(conn, SAMPLE_MIGRATIONS)
        name = fetch_scalar(
            conn, "SELECT name FROM schema_version WHERE version = ?", (1,)
        )
        assert name == "create_t1"
