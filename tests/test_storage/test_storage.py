"""Tests for graphrag_mcp.storage — backend registry, factory, and SQLiteBackend."""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag_mcp.storage import (
    SQLiteBackend,
    available_backends,
    create_backend,
    register_backend,
)
from graphrag_mcp.storage.base import StorageBackend as StorageBackendABC
from graphrag_mcp.utils.errors import ConfigError

# ── Registry / factory tests ────────────────────────────────────────────────


def test_available_backends_includes_sqlite():
    """sqlite is always registered."""
    assert "sqlite" in available_backends()


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


def test_register_backend_duplicate_raises():
    """Registering 'sqlite' again raises ValueError."""
    with pytest.raises(ValueError, match="already registered"):
        register_backend("sqlite", SQLiteBackend)


def test_register_and_use_custom_backend():
    """A registered custom backend can be instantiated via create_backend."""

    class FakeBackend(StorageBackendABC):
        """Minimal concrete stub for testing the registry."""

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        # Implement all abstract methods as no-ops for instantiation
        async def initialize(self):
            pass

        async def close(self):
            pass

        async def transaction(self):
            yield  # pragma: no cover

        async def upsert_entity(self, **kw):
            return "created"

        async def get_entity_by_id(self, entity_id):
            return None

        async def get_entity_by_name(self, name, entity_type=None):
            return None

        async def get_entity_by_name_nocase(self, name):
            return None

        async def list_entities(self, entity_type=None, limit=100, offset=0):
            return []

        async def update_entity_fields(self, entity_id, updates):
            pass

        async def delete_entity(self, entity_id):
            pass

        async def count_entities(self):
            return 0

        async def entity_type_distribution(self):
            return {}

        async def most_connected_entities(self, limit=10):
            return []

        async def recent_entities(self, limit=10):
            return []

        async def upsert_relationship(self, **kw):
            return "created"

        async def get_relationship(self, source_id, target_id, relationship_type):
            return None

        async def get_relationships_for_entity(
            self, entity_id, direction="both", relationship_type=None
        ):
            return []

        async def delete_relationships(self, source_id, target_id, relationship_type=None):
            return 0

        async def get_relationships_by_column(self, column, entity_id):
            return []

        async def update_relationship(self, rel_id, updates):
            pass

        async def delete_relationship_by_id(self, rel_id):
            pass

        async def count_relationships(self):
            return 0

        async def relationship_type_distribution(self):
            return {}

        async def insert_observation(self, **kw):
            pass

        async def get_observations_for_entity(self, entity_id):
            return []

        async def move_observations(self, from_entity_id, to_entity_id):
            return 0

        async def count_observations(self):
            return 0

        async def upsert_entity_embedding(self, entity_id, embedding):
            pass

        async def upsert_observation_embedding(self, obs_id, embedding):
            pass

        async def delete_entity_embedding(self, entity_id):
            pass

        async def delete_observation_embedding(self, obs_id):
            pass

        async def get_cached_embedding(self, content_hash, model_name):
            return None

        async def set_cached_embedding(self, content_hash, embedding, model_name, created_at):
            pass

        async def prune_embedding_cache(self, max_size):
            pass

        async def ensure_vec_tables(self, dimension):
            return False

        async def vector_search(self, table, query_embedding, limit):
            return []

        async def fts_search_entities(self, query, limit):
            return []

        async def fts_search_observations(self, query, limit):
            return []

        async def fts_suggest_similar(self, name, limit=5):
            return []

        async def get_metadata(self, key):
            return None

        async def set_metadata(self, key, value):
            pass

        async def fetch_all(self, sql, params=()):
            return []

        async def fetch_one(self, sql, params=()):
            return None

        async def fetch_entity_rows(self, entity_ids):
            return []

        async def fetch_relationships_between(self, entity_ids):
            return []

        async def resolve_entity_names(self, entity_ids):
            return {}

        async def get_schema_version(self):
            return 0

        @property
        def backend_type(self):
            return "fake"

        @property
        def vec_available(self):
            return False

    try:
        register_backend("_test_fake", FakeBackend)
        backend = create_backend("_test_fake", some_opt="hello")
        assert isinstance(backend, FakeBackend)
        assert backend.kwargs == {"some_opt": "hello"}
        assert backend.backend_type == "fake"
    finally:
        # Clean up the registry so other tests are not affected
        from graphrag_mcp.storage import _REGISTRY

        _REGISTRY.pop("_test_fake", None)


# ── StorageBackend ABC tests ────────────────────────────────────────────────


def test_storage_backend_is_abstract():
    """Cannot instantiate StorageBackend directly."""
    with pytest.raises(TypeError):
        StorageBackendABC()


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
    from graphrag_mcp.utils.errors import DatabaseError

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
