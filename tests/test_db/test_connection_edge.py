"""Failure-path tests for :class:`graph_mem.db.connection.Database`.

The happy paths live in ``test_connection.py``.  This module covers what
happens when SQLite, the extension loader, or ``close()`` misbehave — the
paths that decide whether a failure is contained or leaks a half-finished
transaction, a still-loadable extension hook, or a dead connection handle.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from graph_mem.db.connection import Database
from graph_mem.utils.errors import DatabaseError


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An initialised database with a single-column table ``t`` to write into."""
    database = Database(tmp_path / "edge.db")
    await database.initialize()
    await database.execute("CREATE TABLE t (v TEXT)")
    yield database
    await database.close()


async def _values(db: Database) -> list[str]:
    return [row["v"] for row in await db.fetch_all("SELECT v FROM t ORDER BY v")]


class _StatementFailer:
    """Make the *raw* connection raise for statements starting with a prefix.

    Patching the aiosqlite connection rather than :meth:`Database.execute` is
    deliberate: ``transaction``/``_unwind`` issue ``BEGIN``/``COMMIT``/
    ``ROLLBACK``/``RELEASE`` straight through ``self.conn``, so that is the only
    seam a simulated disk or lock error can be injected at.
    """

    def __init__(self, db: Database, prefix: str, *, times: int | None = None) -> None:
        self._db = db
        self._real = db.conn.execute
        self._prefix = prefix.upper()
        self._remaining = times
        self.armed = True
        self.failures = 0
        self.statements: list[str] = []
        db.conn.execute = self._execute  # type: ignore[method-assign]

    async def _execute(self, sql: str, *args: Any, **kwargs: Any) -> aiosqlite.Cursor:
        self.statements.append(sql)
        if (
            self.armed
            and sql.strip().upper().startswith(self._prefix)
            and (self._remaining is None or self.failures < self._remaining)
        ):
            self.failures += 1
            raise sqlite3.OperationalError(f"simulated failure on {self._prefix}")
        return await self._real(sql, *args, **kwargs)

    def disarm(self) -> None:
        self.armed = False


def _assert_no_transaction_open(db: Database) -> None:
    assert db._txn_depth == 0
    assert db._txn_owner is None
    assert not db._write_lock.locked()
    assert db.conn.in_transaction is False


# ---------------------------------------------------------------------------
# Transaction nesting
# ---------------------------------------------------------------------------


async def test_nested_transactions_commit_at_every_depth(db: Database) -> None:
    """Three nested levels that all succeed persist all three writes."""
    depths: list[int] = []

    async with db.transaction():
        depths.append(db._txn_depth)
        await db.execute("INSERT INTO t (v) VALUES ('a')")
        async with db.transaction():
            depths.append(db._txn_depth)
            await db.execute("INSERT INTO t (v) VALUES ('b')")
            async with db.transaction():
                depths.append(db._txn_depth)
                await db.execute("INSERT INTO t (v) VALUES ('c')")

    assert depths == [1, 2, 3]
    assert await _values(db) == ["a", "b", "c"]
    _assert_no_transaction_open(db)


async def test_depth_one_rollback_discards_everything(db: Database) -> None:
    """A failure at the outermost level rolls the whole transaction back."""
    with pytest.raises(RuntimeError, match="outer boom"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('a')")
            raise RuntimeError("outer boom")

    assert await _values(db) == []
    _assert_no_transaction_open(db)


async def test_depth_two_inner_rollback_keeps_outer_work(db: Database) -> None:
    """A savepoint rollback discards only the inner level's writes."""
    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('outer-before')")
        with pytest.raises(RuntimeError, match="inner boom"):
            async with db.transaction():
                await db.execute("INSERT INTO t (v) VALUES ('inner')")
                raise RuntimeError("inner boom")
        # The outer transaction survived its child's failure and can still write.
        assert db._txn_depth == 1
        await db.execute("INSERT INTO t (v) VALUES ('outer-after')")

    assert await _values(db) == ["outer-after", "outer-before"]
    _assert_no_transaction_open(db)


async def test_depth_three_middle_rollback_keeps_outer_work(db: Database) -> None:
    """Rolling back level 2 discards level 3 with it, but not level 1."""
    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('L1')")
        with pytest.raises(RuntimeError, match="mid boom"):
            async with db.transaction():
                await db.execute("INSERT INTO t (v) VALUES ('L2')")
                async with db.transaction():
                    await db.execute("INSERT INTO t (v) VALUES ('L3')")
                raise RuntimeError("mid boom")
        assert db._txn_depth == 1

    assert await _values(db) == ["L1"]
    _assert_no_transaction_open(db)


# ---------------------------------------------------------------------------
# Finalisation failures
# ---------------------------------------------------------------------------


async def test_commit_failure_leaves_connection_clean(db: Database) -> None:
    """A failing COMMIT aborts the work, frees the lock, and lets the next write through."""
    failer = _StatementFailer(db, "COMMIT")

    with pytest.raises(DatabaseError, match="Failed to finalise transaction"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('lost')")

    failer.disarm()
    _assert_no_transaction_open(db)
    # The failed commit was converted into a rollback, not left dangling.
    assert await _values(db) == []

    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('kept')")
    assert await _values(db) == ["kept"]


async def test_transient_rollback_failure_recovers_via_the_retry(db: Database) -> None:
    """A ROLLBACK that fails once is retried by ``_unwind`` and the connection recovers."""
    failer = _StatementFailer(db, "ROLLBACK", times=1)

    with pytest.raises(DatabaseError, match="Failed to finalise transaction"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('x')")
            raise RuntimeError("body boom")

    assert failer.failures == 1
    failer.disarm()
    _assert_no_transaction_open(db)
    assert await _values(db) == []

    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('after')")
    assert await _values(db) == ["after"]


async def test_unrecoverable_rollback_marks_the_connection_unusable(db: Database) -> None:
    """When the recovery ROLLBACK also fails, say so instead of pretending.

    Regression: the failed recovery rollback was swallowed, the bookkeeping was
    reset, and the write lock was handed back while SQLite still held an open
    transaction. Every later BEGIN IMMEDIATE then failed with "cannot start a
    transaction within a transaction" — the connection was permanently broken
    and nothing said why.

    The transaction state is genuinely unknown at that point, so the honest
    outcome is to fail fast with the real reason rather than to claim the
    connection is clean.
    """
    failer = _StatementFailer(db, "ROLLBACK")

    with pytest.raises(DatabaseError, match="Failed to finalise transaction"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('x')")
            raise RuntimeError("body boom")

    failer.disarm()

    # The lock is released and the depth reset, so nothing is wedged waiting.
    assert db._txn_depth == 0
    assert db._txn_owner is None
    assert not db._write_lock.locked()

    # But the connection reports itself unusable, with the reason, rather than
    # accepting work it cannot honour.
    assert db.usable is False
    with pytest.raises(DatabaseError, match="unusable"):
        _ = db.conn


async def test_reopening_clears_the_unusable_state(tmp_path) -> None:
    """The failure is recoverable: close and reopen gives a working database."""
    db = Database(tmp_path / "recover.db")
    await db.initialize()
    await db.execute("CREATE TABLE t (v TEXT)")

    failer = _StatementFailer(db, "ROLLBACK")
    with pytest.raises(DatabaseError):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('x')")
            raise RuntimeError("boom")
    failer.disarm()
    assert db.usable is False

    await db.close()
    await db.initialize()

    assert db.usable is True
    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('after')")
    assert await _values(db) == ["after"]
    await db.close()


async def test_recoverable_rollback_leaves_the_connection_usable(db: Database) -> None:
    """When only the COMMIT fails, the recovery rollback works and life goes on.

    This is the common case and must NOT poison the connection — only an
    unrecoverable rollback does.
    """
    failer = _StatementFailer(db, "COMMIT")

    with pytest.raises(DatabaseError, match="Failed to finalise transaction"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('doomed')")

    failer.disarm()
    assert db.usable is True

    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('after')")
    assert await _values(db) == ["after"]


async def test_savepoint_release_failure_unwinds_the_whole_transaction(db: Database) -> None:
    """A failing RELEASE at a nested level must not strand the outer level."""
    failer = _StatementFailer(db, "RELEASE")

    with pytest.raises(DatabaseError, match="Failed to finalise transaction"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES ('outer')")
            async with db.transaction():
                await db.execute("INSERT INTO t (v) VALUES ('inner')")

    failer.disarm()
    _assert_no_transaction_open(db)
    assert await _values(db) == []

    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('later')")
    assert await _values(db) == ["later"]


async def test_begin_failure_releases_the_write_lock(db: Database) -> None:
    """A transaction that never starts must not keep the write lock."""
    failer = _StatementFailer(db, "BEGIN")

    # NOTE: transaction() documents "Raises: DatabaseError", but a failing
    # BEGIN IMMEDIATE escapes as the raw sqlite3 error.  Asserted as-is so the
    # discrepancy is visible if the contract is ever tightened.
    with pytest.raises(sqlite3.OperationalError, match="simulated failure on BEGIN"):
        async with db.transaction():  # pragma: no cover — body never runs
            pass

    failer.disarm()
    _assert_no_transaction_open(db)

    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES ('ok')")
    assert await _values(db) == ["ok"]


# ---------------------------------------------------------------------------
# Extension loading — must degrade, never raise
# ---------------------------------------------------------------------------


async def test_extension_loading_disables_the_hook_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path loads sqlite-vec and re-disables extension loading."""
    pytest.importorskip("sqlite_vec")
    toggles: list[bool] = []
    real = aiosqlite.Connection.enable_load_extension

    async def spy(self: aiosqlite.Connection, enabled: bool) -> None:
        toggles.append(enabled)
        await real(self, enabled)

    monkeypatch.setattr(aiosqlite.Connection, "enable_load_extension", spy)

    database = Database(tmp_path / "vec.db")
    await database.initialize()
    try:
        assert database.vec_loaded is True
        assert toggles == [True, False], "extension loading must be re-disabled after use"
    finally:
        await database.close()


async def test_missing_sqlite_vec_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An uninstalled sqlite-vec leaves a usable database with vec_loaded False."""
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)

    database = Database(tmp_path / "novec.db")
    await database.initialize()
    try:
        assert database.vec_loaded is False
        assert await database.fetch_one("SELECT 1 AS ok") == {"ok": 1}
    finally:
        await database.close()


async def test_enable_load_extension_attribute_error_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Python built without loadable-extension support degrades, not raises."""
    pytest.importorskip("sqlite_vec")

    async def unsupported(self: aiosqlite.Connection, enabled: bool) -> None:
        raise AttributeError("Connection object has no attribute 'enable_load_extension'")

    monkeypatch.setattr(aiosqlite.Connection, "enable_load_extension", unsupported)

    database = Database(tmp_path / "noext.db")
    await database.initialize()
    try:
        assert database.vec_loaded is False
        assert await database.fetch_one("SELECT 1 AS ok") == {"ok": 1}
    finally:
        await database.close()


async def test_load_extension_failure_degrades_and_redisables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken extension binary must still leave loading switched back off."""
    pytest.importorskip("sqlite_vec")
    toggles: list[bool] = []
    real_enable = aiosqlite.Connection.enable_load_extension

    async def spy(self: aiosqlite.Connection, enabled: bool) -> None:
        toggles.append(enabled)
        await real_enable(self, enabled)

    async def broken(self: aiosqlite.Connection, path: str) -> None:
        raise sqlite3.OperationalError(f"cannot open shared object file: {path}")

    monkeypatch.setattr(aiosqlite.Connection, "enable_load_extension", spy)
    monkeypatch.setattr(aiosqlite.Connection, "load_extension", broken)

    database = Database(tmp_path / "badext.db")
    await database.initialize()
    try:
        assert database.vec_loaded is False
        assert toggles == [True, False], "a failed load must not leave load_extension enabled"
        assert await database.fetch_one("SELECT 1 AS ok") == {"ok": 1}
    finally:
        await database.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_initialize_failure_wraps_the_error(tmp_path: Path) -> None:
    """A path SQLite cannot open surfaces as DatabaseError, not sqlite3.Error."""
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    database = Database(directory)
    with pytest.raises(DatabaseError, match="Failed to open database"):
        await database.initialize()


async def test_close_clears_the_handle_even_when_close_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing close() must not strand callers on a dead connection."""
    database = Database(tmp_path / "badclose.db")
    await database.initialize()
    raw = database._conn
    assert raw is not None

    real_close = aiosqlite.Connection.close

    async def failing_close(self: aiosqlite.Connection) -> None:
        raise sqlite3.OperationalError("cannot close due to unfinalized statements")

    monkeypatch.setattr(aiosqlite.Connection, "close", failing_close)

    await database.close()  # must not raise

    assert database._conn is None
    with pytest.raises(DatabaseError, match="Database not initialized"):
        _ = database.conn

    monkeypatch.setattr(aiosqlite.Connection, "close", real_close)
    await raw.close()


# ---------------------------------------------------------------------------
# Error reporting and cursor hygiene
# ---------------------------------------------------------------------------


_BAD_SQL = "SELECT no_such_column_qq FROM sqlite_master WHERE 1 = 1"


@pytest.mark.parametrize("method", ["execute", "fetch_one", "fetch_all"])
async def test_bad_sql_raises_database_error_without_echoing_the_statement(
    db: Database, method: str
) -> None:
    """DatabaseError.details/message must not carry query text back to the caller."""
    with pytest.raises(DatabaseError) as excinfo:
        await getattr(db, method)(_BAD_SQL)

    message = str(excinfo.value)
    assert message.startswith("SQL error:")
    assert "sqlite_master" not in message
    assert "WHERE 1 = 1" not in message
    # details is copied verbatim into MCP tool responses.
    assert excinfo.value.details is None


async def test_execute_many_bad_sql_raises_database_error(db: Database) -> None:
    """execute_many() follows the same no-SQL-in-the-message rule."""
    with pytest.raises(DatabaseError) as excinfo:
        await db.execute_many("INSERT INTO no_such_table_qq (v) VALUES (?)", [("a",), ("b",)])

    message = str(excinfo.value)
    assert message.startswith("SQL error:")
    assert "INSERT INTO" not in message


async def test_execute_many_inserts_every_row(db: Database) -> None:
    """execute_many() is otherwise a plain batch insert."""
    await db.execute_many("INSERT INTO t (v) VALUES (?)", [("a",), ("b",)])
    assert await _values(db) == ["a", "b"]


async def test_every_helper_closes_its_cursor(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No statement helper may leave a cursor open.

    Regression: execute() and execute_many() used to return aiosqlite's cursor
    without closing it, and nearly every caller discarded it. The garbage
    collector then finalised it after the event loop had closed, and the
    connection's worker thread raised "Event loop is closed" during an
    unrelated test's teardown.
    """
    closed: list[int] = []
    real_close = aiosqlite.Cursor.close

    async def counting_close(self: aiosqlite.Cursor) -> None:
        closed.append(id(self))
        await real_close(self)

    monkeypatch.setattr(aiosqlite.Cursor, "close", counting_close)

    await db.fetch_all("SELECT v FROM t")
    assert len(closed) == 1

    await db.fetch_one("SELECT v FROM t")
    assert len(closed) == 2

    await db.execute("SELECT v FROM t")
    assert len(closed) == 3, "execute() must close the cursor it opened"

    await db.execute_many("INSERT INTO t (v) VALUES (?)", [("x",)])
    assert len(closed) == 4, "execute_many() must close the cursor it opened"


async def test_execute_returns_the_number_of_rows_affected(db: Database) -> None:
    """The row count is what callers wanted from the cursor; hand that over."""
    await db.execute_many("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])

    assert await db.execute("UPDATE t SET v = 'z' WHERE v IN ('a', 'b')") == 2
    assert await db.execute("DELETE FROM t WHERE v = 'nothing_matches'") == 0


async def test_execute_many_returns_the_number_of_rows_affected(db: Database) -> None:
    assert await db.execute_many("INSERT INTO t (v) VALUES (?)", [("a",), ("b",)]) == 2
