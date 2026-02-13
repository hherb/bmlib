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

"""Tests for bmlib.db — connection, operations, and transactions."""

from __future__ import annotations

from bmlib.db import (
    connect_sqlite,
    create_tables,
    execute,
    executemany,
    fetch_all,
    fetch_one,
    fetch_scalar,
    table_exists,
    transaction,
)


def _mem_conn():
    return connect_sqlite(":memory:")


class TestConnection:
    def test_sqlite_memory(self):
        conn = _mem_conn()
        assert conn is not None
        conn.close()


class TestOperations:
    def test_create_and_query(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, name TEXT);")
        assert table_exists(conn, "t")
        assert not table_exists(conn, "nonexistent")

    def test_execute_insert_and_fetch(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT);")

        cur = execute(conn, "INSERT INTO t (val) VALUES (?)", ("hello",))
        assert cur.lastrowid == 1

        row = fetch_one(conn, "SELECT val FROM t WHERE id=?", (1,))
        assert row["val"] == "hello"

        rows = fetch_all(conn, "SELECT * FROM t")
        assert len(rows) == 1

    def test_fetch_scalar(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER);")
        execute(conn, "INSERT INTO t (n) VALUES (?)", (42,))
        conn.commit()

        val = fetch_scalar(conn, "SELECT n FROM t WHERE id=1")
        assert val == 42

    def test_fetch_one_returns_none(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY);")
        assert fetch_one(conn, "SELECT * FROM t WHERE id=999") is None

    def test_executemany(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")
        executemany(conn, "INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
        conn.commit()
        rows = fetch_all(conn, "SELECT v FROM t ORDER BY v")
        assert [r["v"] for r in rows] == ["a", "b", "c"]


class TestTransaction:
    def test_commit_on_success(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")

        with transaction(conn):
            execute(conn, "INSERT INTO t (v) VALUES (?)", ("committed",))

        assert fetch_scalar(conn, "SELECT v FROM t") == "committed"

    def test_rollback_on_error(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")

        try:
            with transaction(conn):
                execute(conn, "INSERT INTO t (v) VALUES (?)", ("rollback",))
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert fetch_one(conn, "SELECT * FROM t") is None
