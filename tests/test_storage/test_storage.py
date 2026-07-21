"""Tests for graph_mem.storage — the backend factory and SQLiteBackend."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from graph_mem.storage import (
    SUPPORTED_BACKENDS,
    SQLiteBackend,
    create_backend,
)
from graph_mem.utils.errors import ConfigError

# ── Registry / factory tests ────────────────────────────────────────────────


def test_supported_backends_lists_sqlite():
    """sqlite is the backend this package builds."""
    assert "sqlite" in SUPPORTED_BACKENDS


def test_create_backend_sqlite(tmp_db_path: Path):
    """create_backend('sqlite') returns an SQLiteBackend."""
    backend = create_backend("sqlite", db_path=tmp_db_path)
    assert isinstance(backend, SQLiteBackend)


def test_create_backend_unknown_raises():
    """create_backend with unknown type raises ConfigError."""
    with pytest.raises(ConfigError, match="Unknown storage backend"):
        create_backend("neo4j")


def test_create_backend_sqlite_missing_db_path():
    """create_backend('sqlite') without db_path raises TypeError."""
    with pytest.raises(TypeError, match="db_path"):
        create_backend("sqlite")


def test_create_backend_sqlite_string_path(tmp_path: Path):
    """create_backend accepts string paths and converts to Path."""
    backend = create_backend("sqlite", db_path=str(tmp_path / "graph.db"))
    assert isinstance(backend, SQLiteBackend)


# ── SQLiteBackend lifecycle tests ───────────────────────────────────────────


async def test_sqlite_backend_initialize_and_close(tmp_db_path: Path):
    """SQLiteBackend can initialize and close cleanly."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    assert backend.backend_type == "sqlite"
    assert isinstance(backend.vec_available, bool)
    version = await backend.get_schema_version()
    assert version >= 1
    await backend.close()


async def test_sqlite_backend_not_initialized_raises(tmp_db_path: Path):
    """Using SQLiteBackend before initialize raises DatabaseError."""
    from graph_mem.utils.errors import DatabaseError

    backend = SQLiteBackend(tmp_db_path)
    with pytest.raises(DatabaseError, match="not initialized"):
        await backend.count_entities()


async def test_sqlite_backend_entity_crud(tmp_db_path: Path):
    """Basic entity upsert, get, count, delete through SQLiteBackend."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        # Count starts at 0
        assert await backend.count_entities() == 0

        # Create entity
        import time

        now = time.time()
        result = await backend.upsert_entity(
            entity_id="ent-1",
            name="Alice",
            entity_type="person",
            description="A developer",
            properties={"role": "backend"},
            created_at=now,
            updated_at=now,
        )
        assert result == "created"
        assert await backend.count_entities() == 1

        # Get by ID
        row = await backend.get_entity_by_id("ent-1")
        assert row is not None
        assert row["name"] == "Alice"

        # Get by name
        row = await backend.get_entity_by_name("Alice")
        assert row is not None

        # Get by name case-insensitive
        row = await backend.get_entity_by_name_nocase("alice")
        assert row is not None

        # Upsert same entity merges
        result = await backend.upsert_entity(
            entity_id="ent-1-dup",
            name="Alice",
            entity_type="person",
            description="Also a designer",
            properties={"team": "platform"},
            created_at=now,
            updated_at=now,
        )
        assert result == "merged"
        assert await backend.count_entities() == 1

        # List entities
        entities = await backend.list_entities()
        assert len(entities) == 1

        # Delete entity
        await backend.delete_entity("ent-1")
        assert await backend.count_entities() == 0
    finally:
        await backend.close()


async def test_sqlite_backend_relationship_crud(tmp_db_path: Path):
    """Basic relationship upsert, get, count, delete through SQLiteBackend."""
    import time

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        # Create two entities first
        await backend.upsert_entity(
            entity_id="e1",
            name="A",
            entity_type="t",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )
        await backend.upsert_entity(
            entity_id="e2",
            name="B",
            entity_type="t",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )

        assert await backend.count_relationships() == 0

        # Create relationship
        result = await backend.upsert_relationship(
            rel_id="r1",
            source_id="e1",
            target_id="e2",
            relationship_type="knows",
            weight=0.8,
            properties={"since": "2024"},
            created_at=now,
            updated_at=now,
        )
        assert result == "created"
        assert await backend.count_relationships() == 1

        # Get relationship
        row = await backend.get_relationship("e1", "e2", "knows")
        assert row is not None

        # Get relationships for entity
        rels = await backend.get_relationships_for_entity("e1")
        assert len(rels) == 1

        # Delete relationship
        count = await backend.delete_relationships("e1", "e2", "knows")
        assert count == 1
        assert await backend.count_relationships() == 0
    finally:
        await backend.close()


async def test_sqlite_backend_observation_crud(tmp_db_path: Path):
    """Basic observation insert, get, count through SQLiteBackend."""
    import time

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        await backend.upsert_entity(
            entity_id="e1",
            name="A",
            entity_type="t",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )

        assert await backend.count_observations() == 0

        await backend.insert_observation(
            obs_id="o1",
            entity_id="e1",
            content="Fact one",
            source="test",
            created_at=now,
        )
        assert await backend.count_observations() == 1

        obs = await backend.get_observations_for_entity("e1")
        assert len(obs) == 1
        assert obs[0]["content"] == "Fact one"
    finally:
        await backend.close()


async def test_sqlite_backend_metadata(tmp_db_path: Path):
    """Metadata get/set through SQLiteBackend."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        assert await backend.get_metadata("nonexistent") is None

        await backend.set_metadata("model_name", "test-model")
        value = await backend.get_metadata("model_name")
        assert value == "test-model"

        # Overwrite
        await backend.set_metadata("model_name", "updated-model")
        value = await backend.get_metadata("model_name")
        assert value == "updated-model"
    finally:
        await backend.close()


async def test_sqlite_backend_transaction(tmp_db_path: Path):
    """Transaction context manager commits on success."""
    import time

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        async with backend.transaction():
            await backend.upsert_entity(
                entity_id="e1",
                name="TxnTest",
                entity_type="t",
                description="",
                properties={},
                created_at=now,
                updated_at=now,
            )
        # Entity persists after transaction
        assert await backend.count_entities() == 1
    finally:
        await backend.close()


async def test_sqlite_backend_schema_version(tmp_db_path: Path):
    """Schema version is > 0 after initialization."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        version = await backend.get_schema_version()
        assert version >= 1
    finally:
        await backend.close()


async def test_sqlite_backend_entity_type_distribution(tmp_db_path: Path):
    """entity_type_distribution returns correct counts."""
    import time

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        await backend.upsert_entity(
            entity_id="e1",
            name="A",
            entity_type="person",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )
        await backend.upsert_entity(
            entity_id="e2",
            name="B",
            entity_type="person",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )
        await backend.upsert_entity(
            entity_id="e3",
            name="C",
            entity_type="service",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )

        dist = await backend.entity_type_distribution()
        assert dist["person"] == 2
        assert dist["service"] == 1
    finally:
        await backend.close()


async def test_sqlite_backend_close_idempotent(tmp_db_path: Path):
    """Closing an already-closed backend is safe."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    await backend.close()
    await backend.close()  # Should not raise
