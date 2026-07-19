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

import pytest

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

    def test_works_with_pending_write(self):
        # Regression: entering transaction() while sqlite has already auto-begun
        # a transaction (an uncommitted write) must not raise "cannot start a
        # transaction within a transaction".
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")

        execute(conn, "INSERT INTO t (v) VALUES (?)", ("pending",))
        assert conn.in_transaction

        with transaction(conn):
            execute(conn, "INSERT INTO t (v) VALUES (?)", ("inside",))

        rows = {r["v"] for r in fetch_all(conn, "SELECT v FROM t")}
        assert rows == {"pending", "inside"}

    def test_nested_transaction_defers_commit_to_outer(self):
        # A transaction() block that joins an outer transaction() must not
        # commit on success — the outer block owns the commit, so a failure
        # after the inner block rolls back the inner block's writes too.
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")

        with pytest.raises(RuntimeError):
            with transaction(conn):
                with transaction(conn):
                    execute(conn, "INSERT INTO t (v) VALUES (?)", ("inner",))
                assert conn.in_transaction  # inner exit must not have committed
                raise RuntimeError("boom")

        assert fetch_one(conn, "SELECT * FROM t") is None

    def test_nested_transaction_commits_with_outer(self):
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")

        with transaction(conn):
            with transaction(conn):
                execute(conn, "INSERT INTO t (v) VALUES (?)", ("inner",))
            execute(conn, "INSERT INTO t (v) VALUES (?)", ("outer",))

        assert not conn.in_transaction
        rows = {r["v"] for r in fetch_all(conn, "SELECT v FROM t")}
        assert rows == {"inner", "outer"}

    def test_exception_preserves_pending_write(self):
        # When transaction() joins an already-open transaction, an exception
        # inside the block must roll back only the block's own writes — the
        # caller's pre-existing pending write is not ours to destroy.
        conn = _mem_conn()
        create_tables(conn, "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")

        execute(conn, "INSERT INTO t (v) VALUES (?)", ("pending",))
        assert conn.in_transaction

        with pytest.raises(RuntimeError):
            with transaction(conn):
                execute(conn, "INSERT INTO t (v) VALUES (?)", ("inside",))
                raise RuntimeError("boom")

        rows = {r["v"] for r in fetch_all(conn, "SELECT v FROM t")}
        assert rows == {"pending"}

        # The pending write is still the caller's to commit.
        conn.commit()
        assert fetch_scalar(conn, "SELECT v FROM t") == "pending"


class TestCreateTablesTriggers:
    """create_tables must not split inside a compound (trigger) body."""

    def test_trigger_with_body_is_one_statement(self):
        # Regression: _split_sql_statements split on every semicolon, so the
        # semicolons inside BEGIN ... END arrived as fragments and SQLite
        # raised "incomplete input".
        conn = _mem_conn()
        create_tables(
            conn,
            """
            CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT, updated TEXT);
            CREATE TABLE audit (id INTEGER PRIMARY KEY, note TEXT);
            CREATE TRIGGER t_after_insert AFTER INSERT ON t
            BEGIN
                UPDATE t SET updated = 'yes' WHERE id = NEW.id;
                INSERT INTO audit (note) VALUES ('inserted');
            END;
            """,
        )
        execute(conn, "INSERT INTO t (v) VALUES (?)", ("x",))
        assert fetch_scalar(conn, "SELECT updated FROM t") == "yes"
        assert fetch_scalar(conn, "SELECT note FROM audit") == "inserted"

    def test_case_expression_inside_trigger_body(self):
        # CASE ... END nests inside BEGIN ... END; depth tracking must not
        # treat the CASE's END as closing the trigger body.
        conn = _mem_conn()
        create_tables(
            conn,
            """
            CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER, label TEXT);
            CREATE TRIGGER t_label AFTER INSERT ON t
            BEGIN
                UPDATE t
                SET label = CASE WHEN NEW.n > 10 THEN 'big' ELSE 'small' END
                WHERE id = NEW.id;
            END;
            """,
        )
        execute(conn, "INSERT INTO t (n) VALUES (?)", (42,))
        assert fetch_scalar(conn, "SELECT label FROM t") == "big"

    def test_plain_schema_still_splits(self):
        # The common path must be unaffected: multiple plain statements.
        conn = _mem_conn()
        create_tables(
            conn,
            """
            CREATE TABLE a (id INTEGER PRIMARY KEY);
            CREATE TABLE b (id INTEGER PRIMARY KEY);
            CREATE INDEX idx_b ON b (id);
            """,
        )
        assert table_exists(conn, "a")
        assert table_exists(conn, "b")

    def test_bare_begin_outside_trigger_is_not_treated_as_a_body(self):
        # A statement literally named BEGIN must not open a compound body,
        # or everything after it would be swallowed into one statement.
        from bmlib.db.operations import _split_sql_statements

        stmts = _split_sql_statements("CREATE TABLE a (id INT); BEGIN; CREATE TABLE b (id INT);")
        assert len(stmts) == 3
