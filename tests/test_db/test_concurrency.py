"""Concurrency tests for the transaction layer.

There is one connection and SQLite allows one write transaction on it, but the
MCP runtime and the UI server both dispatch requests as concurrent tasks. These
tests drive that overlap deliberately: every one of them opens transactions
from more than one task at a time, which no test in the suite previously did.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from graph_mem.db.connection import Database
from graph_mem.utils.errors import DatabaseError

if TYPE_CHECKING:
    from pathlib import Path


async def _make_db(tmp_path: Path) -> Database:
    """Open a database with a single-column scratch table."""
    db = Database(tmp_path / "concurrent.db")
    await db.initialize()
    await db.execute("CREATE TABLE note (v TEXT)")
    return db


async def _values(db: Database) -> list[str]:
    rows = await db.fetch_all("SELECT v FROM note ORDER BY v")
    return [str(r["v"]) for r in rows]


# ---------------------------------------------------------------------------
# Isolation between concurrent tasks
# ---------------------------------------------------------------------------


async def test_rollback_does_not_discard_a_concurrent_commit(tmp_path: Path) -> None:
    """One task's rollback must not undo another task's committed work.

    Regression: transaction depth was a plain integer on the shared Database.
    A second task entering while the first was open saw depth == 1, opened a
    SAVEPOINT *inside* the first task's transaction, and its "commit" was
    annihilated when the first rolled back.
    """
    db = await _make_db(tmp_path)
    try:
        first_is_open = asyncio.Event()

        async def failing_writer() -> None:
            with pytest.raises(RuntimeError):
                async with db.transaction():
                    await db.execute("INSERT INTO note VALUES ('A')")
                    first_is_open.set()
                    await asyncio.sleep(0.05)
                    raise RuntimeError("this transaction must roll back alone")

        async def succeeding_writer() -> None:
            await first_is_open.wait()
            async with db.transaction():
                await db.execute("INSERT INTO note VALUES ('B')")

        await asyncio.gather(failing_writer(), succeeding_writer())

        assert await _values(db) == ["B"]
    finally:
        await db.close()


async def test_concurrent_writers_all_commit(tmp_path: Path) -> None:
    """Overlapping writers are serialised, and none of their work is lost."""
    db = await _make_db(tmp_path)
    try:

        async def writer(index: int) -> None:
            async with db.transaction():
                await db.execute("INSERT INTO note VALUES (?)", (f"row{index:02d}",))
                # Yield inside the transaction so the tasks genuinely interleave
                # rather than each running to completion before the next starts.
                await asyncio.sleep(0)

        await asyncio.gather(*(writer(i) for i in range(20)))

        assert await _values(db) == [f"row{i:02d}" for i in range(20)]
    finally:
        await db.close()


async def test_transaction_is_exclusive_while_held(tmp_path: Path) -> None:
    """A second task cannot enter a transaction while the first holds one."""
    db = await _make_db(tmp_path)
    try:
        first_is_open = asyncio.Event()
        second_entered = False

        async def holder() -> None:
            async with db.transaction():
                first_is_open.set()
                await asyncio.sleep(0.05)
                assert not second_entered, "a second task entered a transaction concurrently"

        async def waiter() -> None:
            nonlocal second_entered
            await first_is_open.wait()
            async with db.transaction():
                second_entered = True

        await asyncio.gather(holder(), waiter())
        assert second_entered
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Nesting within a single task
# ---------------------------------------------------------------------------


async def test_nested_transaction_rolls_back_only_the_inner_block(tmp_path: Path) -> None:
    """An inner failure discards inner work and leaves outer work intact."""
    db = await _make_db(tmp_path)
    try:
        async with db.transaction():
            await db.execute("INSERT INTO note VALUES ('outer')")
            with pytest.raises(RuntimeError):
                async with db.transaction():
                    await db.execute("INSERT INTO note VALUES ('inner')")
                    raise RuntimeError("inner fails")
            await db.execute("INSERT INTO note VALUES ('after')")

        assert await _values(db) == ["after", "outer"]
    finally:
        await db.close()


async def test_deeply_nested_transactions_commit_together(tmp_path: Path) -> None:
    """Savepoint nesting is balanced at every depth."""
    db = await _make_db(tmp_path)
    try:
        async with db.transaction():
            await db.execute("INSERT INTO note VALUES ('d1')")
            async with db.transaction():
                await db.execute("INSERT INTO note VALUES ('d2')")
                async with db.transaction():
                    await db.execute("INSERT INTO note VALUES ('d3')")

        assert await _values(db) == ["d1", "d2", "d3"]
    finally:
        await db.close()


async def test_writes_are_possible_again_after_a_failed_transaction(tmp_path: Path) -> None:
    """A failure releases the write lock rather than wedging the connection."""
    db = await _make_db(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await db.execute("INSERT INTO note VALUES ('doomed')")
                raise RuntimeError("boom")

        async with db.transaction():
            await db.execute("INSERT INTO note VALUES ('later')")

        assert await _values(db) == ["later"]
    finally:
        await db.close()


async def test_cancellation_releases_the_write_lock(tmp_path: Path) -> None:
    """A cancelled transaction rolls back and lets the next writer proceed.

    CancelledError inherits from BaseException, not Exception, so an
    ``except Exception`` unwind would leave the lock held forever and every
    later write would hang.
    """
    db = await _make_db(tmp_path)
    try:
        entered = asyncio.Event()

        async def cancelled_writer() -> None:
            async with db.transaction():
                await db.execute("INSERT INTO note VALUES ('cancelled')")
                entered.set()
                await asyncio.sleep(3600)

        task = asyncio.create_task(cancelled_writer())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Would time out rather than fail if the lock had leaked.
        async with asyncio.timeout(5):
            async with db.transaction():
                await db.execute("INSERT INTO note VALUES ('after_cancel')")

        assert await _values(db) == ["after_cancel"]
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------


async def test_sql_text_is_not_included_in_the_raised_error(tmp_path: Path) -> None:
    """Query text stays in the log; it is not echoed back to the caller.

    DatabaseError.details is copied into MCP tool responses, so putting SQL
    there discloses schema and query structure to the model for no benefit.
    """
    db = await _make_db(tmp_path)
    try:
        with pytest.raises(DatabaseError) as caught:
            await db.fetch_all("SELECT * FROM table_that_does_not_exist")

        assert caught.value.details is None
        assert "SELECT" not in str(caught.value)
    finally:
        await db.close()


async def test_close_is_idempotent_and_clears_state(tmp_path: Path) -> None:
    """Closing twice is safe, and the connection is not handed out afterwards."""
    db = await _make_db(tmp_path)
    await db.close()
    await db.close()

    with pytest.raises(DatabaseError, match="not initialized"):
        _ = db.conn


def test_database_accepts_a_string_path(tmp_path: Path) -> None:
    """A str path is coerced rather than failing several frames later.

    Passing a str used to survive construction and raise AttributeError from
    inside ``path.resolve()``, far from the caller that supplied it.
    """
    db = Database(str(tmp_path / "from_string.db"))
    assert db.path == (tmp_path / "from_string.db").resolve()
