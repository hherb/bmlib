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

import sqlite3
import sys

import pytest

from bmlib.db import (
    TransactionModeError,
    connect_sqlite,
    create_tables,
    execute,
    executemany,
    fetch_all,
    fetch_one,
    fetch_scalar,
    require_transaction_control,
    run_migrations,
    table_exists,
    transaction,
    transaction_depth,
)
from bmlib.db.transactions import _SAVEPOINT
from bmlib.publications.schema import ensure_schema


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


_needs_sqlite_autocommit = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="sqlite3's autocommit= is Python 3.12+"
)


class TestSqliteTransactionControl:
    """#449: the SQLite half of the refusal; the both-backend half is in test_backends.

    Measured before the fix (Python 3.13, SQLite 3.53.1): both ``autocommit=``
    modes left a second connection seeing 0 rows after a block that "committed",
    with nothing raised.
    """

    @_needs_sqlite_autocommit
    @pytest.mark.parametrize(
        ("mode", "refusal"),
        [
            (True, r"opened with autocommit=True: commit\(\) and rollback\(\)"),
            (False, r"opened with autocommit=False, which keeps a transaction open"),
        ],
    )
    def test_a_connection_opened_with_autocommit_is_refused(self, tmp_path, mode, refusal):
        conn = sqlite3.connect(tmp_path / "x.db", autocommit=mode)
        try:
            ran = []
            with pytest.raises(TransactionModeError, match=refusal):
                with transaction(conn):
                    ran.append(True)
            assert ran == []
            assert transaction_depth(conn) == 0
        finally:
            conn.close()

    @_needs_sqlite_autocommit
    def test_autocommit_false_is_refused_although_it_reads_as_nested(self, tmp_path):
        """PEP 249 mode keeps a transaction open, so the block takes the nested branch.

        The refusal has to come ahead of that branch, or it would open a
        savepoint and never commit.
        """
        conn = sqlite3.connect(tmp_path / "x.db", autocommit=False)
        try:
            assert conn.in_transaction
            with pytest.raises(TransactionModeError, match="autocommit=False"):
                with transaction(conn):
                    pass
            # Ahead of the branch, not merely somewhere in it: no savepoint was
            # opened on the way to the refusal.
            with pytest.raises(sqlite3.OperationalError, match="no such savepoint"):
                conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        finally:
            conn.close()

    @pytest.mark.parametrize("isolation_level", ["", "DEFERRED", "IMMEDIATE", None])
    def test_legacy_transaction_control_commits_whatever_the_isolation_level(
        self, tmp_path, isolation_level
    ):
        """Legacy mode is the one supported, and ``isolation_level=None`` is part of it."""
        path = tmp_path / "x.db"
        conn = sqlite3.connect(path, isolation_level=isolation_level)
        reader = sqlite3.connect(path)
        try:
            require_transaction_control(conn)
            create_tables(conn, "CREATE TABLE t (v TEXT);")
            with transaction(conn):
                execute(conn, "INSERT INTO t (v) VALUES (?)", ("a",))
            assert reader.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        finally:
            reader.close()
            conn.close()

    def test_the_refusal_is_a_value_error(self):
        """It was a bare ``ValueError`` before it had a type of its own."""
        assert issubclass(TransactionModeError, ValueError)


class _NotSqlite:
    """A connection-shaped object that ``is_sqlite`` takes for PostgreSQL."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class TestAConnectionThatIsNotSqlitesMustReportAutocommitOff:
    """Anything not ``sqlite3``'s is taken for psycopg2's, so it must say autocommit is off.

    A missing or non-bool ``autocommit`` cannot show the block would be
    atomic — a non-delegating wrapper round a psycopg2 connection with
    autocommit on is exactly #449's silent case — so it is refused rather
    than trusted. Python 3.11's ``sqlite3``, which has no such attribute, is
    the SQLite branch and not this one: every SQLite test here runs on 3.11 in
    CI's matrix, ``test_legacy_transaction_control_commits_whatever_the_isolation_level``
    unskipped.
    """

    @pytest.mark.parametrize(
        "conn",
        [_NotSqlite(), _NotSqlite(autocommit=None), _NotSqlite(autocommit=1)],
        ids=["no-attribute", "none", "truthy-non-bool"],
    )
    def test_anything_but_false_is_refused(self, conn):
        with pytest.raises(TransactionModeError, match="cannot be shown to make a block atomic"):
            require_transaction_control(conn)

    def test_a_falsy_non_bool_is_refused_too(self):
        """``0 == False``; the test is identity, so it is not mistaken for off."""
        with pytest.raises(TransactionModeError, match=r"autocommit=0,"):
            require_transaction_control(_NotSqlite(autocommit=0))

    def test_autocommit_off_is_accepted(self):
        require_transaction_control(_NotSqlite(autocommit=False))


class TestTheSchemaHelpersAreRefusedToo:
    """#449's review: ``create_tables()`` decides its commit by ``transaction()``'s rule.

    On ``autocommit=False`` its ``in_transaction`` test is always true, so the
    DDL was never committed and a reopened file held no tables, with nothing
    raised — through ``ensure_schema()`` and ``run_migrations()`` as well,
    neither of which enters a ``transaction()`` on a fresh database.
    """

    @_needs_sqlite_autocommit
    @pytest.mark.parametrize("mode", [False, True])
    @pytest.mark.parametrize(
        "entry",
        [
            lambda c: create_tables(c, "CREATE TABLE t (v TEXT);"),
            ensure_schema,
            lambda c: run_migrations(c, []),
        ],
        ids=["create_tables", "ensure_schema", "run_migrations"],
    )
    def test_no_ddl_runs(self, tmp_path, mode, entry):
        path = tmp_path / "x.db"
        conn = sqlite3.connect(path, autocommit=mode)
        conn.row_factory = sqlite3.Row
        try:
            with pytest.raises(TransactionModeError, match=f"autocommit={mode}"):
                entry(conn)
        finally:
            conn.close()
        reader = sqlite3.connect(path)
        try:
            assert reader.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        finally:
            reader.close()


class TestConnectSqliteAsksForLegacyControl:
    """It names the mode rather than inheriting ``sqlite3``'s default (#449's review).

    Python has announced that default will become ``autocommit=False``, which
    ``transaction()`` refuses, and in which ``PRAGMA foreign_keys`` is ignored
    inside the transaction that mode always holds open.
    """

    @_needs_sqlite_autocommit
    @pytest.mark.parametrize("target", [":memory:", "file"])
    def test_the_mode_is_legacy(self, tmp_path, target):
        conn = connect_sqlite(tmp_path / "x.db" if target == "file" else ":memory:")
        try:
            assert conn.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
            require_transaction_control(conn)
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()

    @_needs_sqlite_autocommit
    def test_the_mode_is_asked_for_by_name(self, monkeypatch):
        """Today the default *is* legacy, so only the call shows it was not inherited."""
        calls = []
        real_connect = sqlite3.connect

        def recording_connect(*args, **kwargs):
            calls.append(kwargs)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr("bmlib.db.connection.sqlite3.connect", recording_connect)
        connect_sqlite(":memory:").close()
        assert calls == [
            {"check_same_thread": False, "autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL}
        ]


class TestAModeChangedInsideABlockIsCaughtOnExit:
    """#449's review: the entry check cannot see a change made inside the block.

    These are the SQLite-only ways; the change both backends share is pinned
    in ``test_backends.py``.
    """

    def _table(self, path):
        conn = sqlite3.connect(path)
        create_tables(conn, "CREATE TABLE t (v INTEGER);")
        return conn

    def _rows(self, path):
        reader = sqlite3.connect(path)
        try:
            return reader.execute("SELECT count(*) FROM t").fetchone()[0]
        finally:
            reader.close()

    def test_isolation_level_none_inside_a_block_is_reported(self, tmp_path):
        """Legacy mode either way, so only the ended ``BEGIN`` shows it."""
        path = tmp_path / "x.db"
        conn = self._table(path)
        try:
            with pytest.raises(RuntimeError, match="was not committed as one unit") as err:
                with transaction(conn):
                    execute(conn, "INSERT INTO t (v) VALUES (1)")
                    conn.isolation_level = None
                    execute(conn, "INSERT INTO t (v) VALUES (2)")
            assert "no longer open at its end" in str(err.value)
            assert transaction_depth(conn) == 0
            # Both writes committed: what the error exists to report.
            assert self._rows(path) == 2
        finally:
            conn.close()

    def test_a_failed_block_reports_it_and_keeps_its_own_exception(self, tmp_path):
        path = tmp_path / "x.db"
        conn = self._table(path)
        try:
            with pytest.raises(RuntimeError, match="may not all have been rolled back") as err:
                with transaction(conn):
                    execute(conn, "INSERT INTO t (v) VALUES (1)")
                    conn.isolation_level = None
                    raise KeyError("the block's own failure")
            assert isinstance(err.value.__cause__, KeyError)
            assert self._rows(path) == 1
        finally:
            conn.close()

    @_needs_sqlite_autocommit
    def test_autocommit_false_inside_a_block_is_reported(self, tmp_path):
        """Measured: the switch keeps the pending writes, and the exit rolls them back."""
        path = tmp_path / "x.db"
        conn = self._table(path)
        try:
            with pytest.raises(RuntimeError, match="mode was changed inside it"):
                with transaction(conn):
                    execute(conn, "INSERT INTO t (v) VALUES (1)")
                    conn.autocommit = False
                    execute(conn, "INSERT INTO t (v) VALUES (2)")
            assert transaction_depth(conn) == 0
            assert self._rows(path) == 0
            # Rolled back, not merely left uncommitted: the connection's own
            # view would still hold both rows.
            assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0
        finally:
            conn.close()

    def test_an_unchanged_block_commits(self, tmp_path):
        """The control: the exit check passes a block nobody tampered with."""
        path = tmp_path / "x.db"
        conn = self._table(path)
        try:
            with transaction(conn):
                execute(conn, "INSERT INTO t (v) VALUES (1)")
            assert self._rows(path) == 1
        finally:
            conn.close()


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
