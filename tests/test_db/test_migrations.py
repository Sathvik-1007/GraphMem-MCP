"""Tests for graph_mem.db.schema migrations."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graph_mem.db.connection import Database
from graph_mem.db.schema import get_current_version, run_migrations

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
    """After run_migrations(), the schema sits at the newest migration."""
    applied = await run_migrations(db)
    assert applied >= 1

    # Fresh database: every migration was just applied, so the version equals
    # the number applied. Stays true as migrations are added.
    version = await get_current_version(db)
    assert version == applied


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


# ── Version tracking ─────────────────────────────────────────────────────────


async def test_applied_versions_returns_the_full_set(db) -> None:
    """Tracking is by applied set, not by maximum."""
    from graph_mem.db.schema import get_applied_versions, run_migrations

    await run_migrations(db)
    applied = await get_applied_versions(db)

    assert applied == {1, 2}


async def test_a_gap_below_the_maximum_is_still_applied(db) -> None:
    """A migration numbered below one already applied must still run.

    Regression: the runner skipped anything with `version <= MAX(version)`, so
    a fix backported as v002 after v003 had shipped would never run on any
    database in the field — and nothing would say so.
    """
    from graph_mem.db.schema import get_applied_versions, run_migrations

    await run_migrations(db)

    # Simulate the backport case: forget v001 was applied, keep the higher one.
    await db.execute("DELETE FROM schema_version WHERE version = 1")
    assert await get_applied_versions(db) == {2}

    applied_count = await run_migrations(db)

    assert applied_count == 1, "the lower-numbered migration was skipped"
    assert await get_applied_versions(db) == {1, 2}


async def test_database_from_a_newer_version_is_refused(db) -> None:
    """An unknown migration means the file was written by newer code.

    Opening it anyway would let this build write rows against a schema it does
    not understand.
    """
    import time as _time

    from graph_mem.db.schema import run_migrations
    from graph_mem.utils.errors import SchemaError

    await run_migrations(db)
    await db.execute(
        "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
        (999, _time.time(), "from the future"),
    )

    with pytest.raises(SchemaError, match="newer graph-mem"):
        await run_migrations(db)


async def test_running_migrations_twice_applies_nothing_the_second_time(db) -> None:
    """Migration is idempotent — startup runs it on every open."""
    from graph_mem.db.schema import run_migrations

    first = await run_migrations(db)
    second = await run_migrations(db)

    assert first >= 1
    assert second == 0
