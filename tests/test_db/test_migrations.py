"""Tests for graphrag_mcp.db.schema migrations."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from pathlib import Path

from graphrag_mcp.db.connection import Database
from graphrag_mcp.db.schema import run_migrations, get_current_version


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Return an initialized Database (no migrations applied yet)."""
    database = Database(tmp_path / "migrations_test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def migrated_db(tmp_path: Path) -> Database:
    """Return an initialized Database with all migrations applied."""
    database = Database(tmp_path / "migrated_test.db")
    await database.initialize()
    await run_migrations(database)
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _table_exists(db: Database, table_name: str) -> bool:
    """Check whether a table (or virtual table) exists in the database."""
    row = await db.fetch_one(
        "SELECT count(*) AS cnt FROM sqlite_master WHERE name = ?",
        (table_name,),
    )
    return row is not None and row["cnt"] > 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_initial_migration_applies(db: Database) -> None:
    """After run_migrations(), the current version should be 1."""
    applied = await run_migrations(db)
    assert applied >= 1

    version = await get_current_version(db)
    assert version == 1


async def test_migrations_idempotent(db: Database) -> None:
    """Running migrations twice should not fail or change the version."""
    await run_migrations(db)
    version_after_first = await get_current_version(db)

    applied = await run_migrations(db)
    assert applied == 0  # nothing new to apply

    version_after_second = await get_current_version(db)
    assert version_after_second == version_after_first


async def test_entities_table_exists(migrated_db: Database) -> None:
    """The entities table should exist after migration."""
    assert await _table_exists(migrated_db, "entities")


async def test_relationships_table_exists(migrated_db: Database) -> None:
    """The relationships table should exist after migration."""
    assert await _table_exists(migrated_db, "relationships")


async def test_observations_table_exists(migrated_db: Database) -> None:
    """The observations table should exist after migration."""
    assert await _table_exists(migrated_db, "observations")


async def test_fts_tables_exist(migrated_db: Database) -> None:
    """entities_fts and observations_fts virtual tables should exist."""
    assert await _table_exists(migrated_db, "entities_fts")
    assert await _table_exists(migrated_db, "observations_fts")


async def test_metadata_table_exists(migrated_db: Database) -> None:
    """The metadata key-value table should exist after migration."""
    assert await _table_exists(migrated_db, "metadata")


async def test_fts_trigger_insert(migrated_db: Database) -> None:
    """Inserting an entity should auto-populate entities_fts via trigger."""
    now = time.time()
    await migrated_db.execute(
        "INSERT INTO entities (id, name, entity_type, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("e1", "AuthService", "Service", "Handles authentication", now, now),
    )

    # FTS match query
    rows = await migrated_db.fetch_all(
        "SELECT * FROM entities_fts WHERE entities_fts MATCH ?",
        ("AuthService",),
    )
    assert len(rows) >= 1
    assert any("AuthService" in r["name"] for r in rows)


async def test_fts_trigger_delete(migrated_db: Database) -> None:
    """Deleting an entity should remove it from entities_fts via trigger."""
    now = time.time()
    await migrated_db.execute(
        "INSERT INTO entities (id, name, entity_type, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("e2", "TempEntity", "Misc", "Will be deleted", now, now),
    )

    # Verify it appears in FTS
    rows = await migrated_db.fetch_all(
        "SELECT * FROM entities_fts WHERE entities_fts MATCH ?",
        ("TempEntity",),
    )
    assert len(rows) >= 1

    # Delete the entity
    await migrated_db.execute("DELETE FROM entities WHERE id = ?", ("e2",))

    # Verify it's gone from FTS
    rows = await migrated_db.fetch_all(
        "SELECT * FROM entities_fts WHERE entities_fts MATCH ?",
        ("TempEntity",),
    )
    assert len(rows) == 0
