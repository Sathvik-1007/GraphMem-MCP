"""Tests for graphrag_mcp.db.connection.Database."""

from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

from graphrag_mcp.db.connection import Database
from graphrag_mcp.utils.errors import DatabaseError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Return an initialized Database that is cleaned up after the test."""
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_initialize_creates_file(tmp_path: Path) -> None:
    """initialize() should create the SQLite file on disk."""
    db_path = tmp_path / "new.db"
    database = Database(db_path)
    assert not db_path.exists()

    await database.initialize()
    try:
        assert db_path.exists()
        assert db_path.stat().st_size > 0
    finally:
        await database.close()


async def test_initialize_creates_parent_dirs(tmp_path: Path) -> None:
    """initialize() should create intermediate parent directories."""
    db_path = tmp_path / "a" / "b" / "c" / "deep.db"
    database = Database(db_path)
    assert not db_path.parent.exists()

    await database.initialize()
    try:
        assert db_path.exists()
        assert db_path.parent.is_dir()
    finally:
        await database.close()


async def test_execute_and_fetch(db: Database) -> None:
    """execute(), fetch_one(), and fetch_all() should round-trip data."""
    await db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    await db.execute("INSERT INTO items (id, name) VALUES (?, ?)", (1, "alpha"))
    await db.execute("INSERT INTO items (id, name) VALUES (?, ?)", (2, "beta"))

    row = await db.fetch_one("SELECT * FROM items WHERE id = ?", (1,))
    assert row is not None
    assert row["id"] == 1
    assert row["name"] == "alpha"

    rows = await db.fetch_all("SELECT * FROM items ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["name"] == "alpha"
    assert rows[1]["name"] == "beta"


async def test_transaction_commit(db: Database) -> None:
    """Data written inside a transaction should be visible after commit."""
    await db.execute("CREATE TABLE t (v TEXT)")

    async with db.transaction():
        await db.execute("INSERT INTO t (v) VALUES (?)", ("committed",))

    row = await db.fetch_one("SELECT v FROM t")
    assert row is not None
    assert row["v"] == "committed"


async def test_transaction_rollback(db: Database) -> None:
    """Data written inside a transaction that raises should be rolled back."""
    await db.execute("CREATE TABLE t (v TEXT)")

    with pytest.raises(RuntimeError, match="boom"):
        async with db.transaction():
            await db.execute("INSERT INTO t (v) VALUES (?)", ("nope",))
            raise RuntimeError("boom")

    row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM t")
    assert row is not None
    assert row["cnt"] == 0


async def test_close_and_reopen(tmp_path: Path) -> None:
    """After close(), the internal connection should be None."""
    db_path = tmp_path / "closable.db"
    database = Database(db_path)
    await database.initialize()

    assert database._conn is not None
    await database.close()
    assert database._conn is None


async def test_context_manager(tmp_path: Path) -> None:
    """async with Database(path) as db should initialize and close."""
    db_path = tmp_path / "ctx.db"

    async with Database(db_path) as database:
        assert database._conn is not None
        # Basic operation should work inside the context
        await database.execute("CREATE TABLE ping (v INTEGER)")
        await database.execute("INSERT INTO ping (v) VALUES (1)")
        row = await database.fetch_one("SELECT v FROM ping")
        assert row is not None
        assert row["v"] == 1

    # Connection should be closed after exiting the context
    assert database._conn is None


async def test_pragma_wal_mode(db: Database) -> None:
    """After initialize(), journal_mode should be WAL."""
    row = await db.fetch_one("PRAGMA journal_mode")
    assert row is not None
    # journal_mode returns the mode name as a string
    mode = list(row.values())[0]
    assert mode.lower() == "wal"


async def test_execute_error_raises_database_error(db: Database) -> None:
    """Invalid SQL should raise DatabaseError, not raw sqlite3 errors."""
    with pytest.raises(DatabaseError, match="SQL error"):
        await db.execute("SELECT * FROM nonexistent_table_xyz")


async def test_fetch_one_returns_none(db: Database) -> None:
    """fetch_one() with no matching rows should return None."""
    await db.execute("CREATE TABLE empty (id INTEGER PRIMARY KEY)")

    result = await db.fetch_one("SELECT * FROM empty WHERE id = ?", (999,))
    assert result is None
