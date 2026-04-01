"""Tests for GraphEngine CRUD operations.

Covers entity, relationship, and observation CRUD plus graph statistics.
Each test uses a fresh temporary SQLite database with migrations applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.models.relationship import Relationship
from graphrag_mcp.storage import SQLiteBackend
from graphrag_mcp.utils.errors import EntityNotFoundError

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> SQLiteBackend:
    """Create a fresh temp storage backend with migrations applied."""
    storage = SQLiteBackend(tmp_path / "test.db")
    await storage.initialize()
    yield storage
    await storage.close()


@pytest_asyncio.fixture
async def engine(db: SQLiteBackend) -> GraphEngine:
    return GraphEngine(db)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _create_entity(
    engine: GraphEngine,
    name: str,
    entity_type: str = "person",
    description: str = "",
    properties: dict | None = None,
) -> str:
    """Create an entity and return its ID."""
    entity = Entity(
        name=name,
        entity_type=entity_type,
        description=description,
        properties=properties or {},
    )
    results = await engine.add_entities([entity])
    return results[0]["id"]


async def _create_relationship(
    engine: GraphEngine,
    source_id: str,
    target_id: str,
    rel_type: str = "knows",
    weight: float = 1.0,
) -> str:
    """Create a relationship and return its ID."""
    rel = Relationship(
        source_id=source_id,
        target_id=target_id,
        relationship_type=rel_type,
        weight=weight,
    )
    results = await engine.add_relationships([rel])
    return results[0]["id"]


# ── Entity CRUD ──────────────────────────────────────────────────────────────


class TestEntityCRUD:
    """Entity create, read, update, delete operations."""

    async def test_add_single_entity(self, engine: GraphEngine) -> None:
        entity = Entity(name="Alice", entity_type="person", description="A researcher")
        results = await engine.add_entities([entity])

        assert len(results) == 1
        result = results[0]
        assert result["name"] == "Alice"
        assert result["status"] == "created"
        assert "id" in result
        assert isinstance(result["id"], str)
        assert len(result["id"]) > 0

    async def test_add_multiple_entities(self, engine: GraphEngine) -> None:
        entities = [
            Entity(name="Alice", entity_type="person"),
            Entity(name="Bob", entity_type="person"),
            Entity(name="MIT", entity_type="organization"),
        ]
        results = await engine.add_entities(entities)

        assert len(results) == 3
        names = {r["name"] for r in results}
        assert names == {"Alice", "Bob", "MIT"}
        assert all(r["status"] == "created" for r in results)

    async def test_add_empty_list(self, engine: GraphEngine) -> None:
        results = await engine.add_entities([])
        assert results == []

    async def test_merge_on_conflict(self, engine: GraphEngine) -> None:
        """Re-inserting same name+type appends description and returns 'merged'."""
        entity_v1 = Entity(name="Alice", entity_type="person", description="A researcher")
        results_v1 = await engine.add_entities([entity_v1])
        assert results_v1[0]["status"] == "created"
        entity_id = results_v1[0]["id"]

        entity_v2 = Entity(name="Alice", entity_type="person", description="Works at MIT")
        results_v2 = await engine.add_entities([entity_v2])
        assert results_v2[0]["status"] == "merged"
        # Merged entity keeps the same ID
        assert results_v2[0]["id"] == entity_id

        # Verify descriptions are combined
        resolved = await engine.get_entity("Alice")
        assert "A researcher" in resolved.description
        assert "Works at MIT" in resolved.description

    async def test_merge_skips_duplicate_description(self, engine: GraphEngine) -> None:
        """Merging with the exact same description does not duplicate it."""
        entity_v1 = Entity(name="Alice", entity_type="person", description="A researcher")
        await engine.add_entities([entity_v1])

        entity_v2 = Entity(name="Alice", entity_type="person", description="A researcher")
        await engine.add_entities([entity_v2])

        resolved = await engine.get_entity("Alice")
        # Description should appear only once
        assert resolved.description.count("A researcher") == 1

    async def test_get_entity_by_name(self, engine: GraphEngine) -> None:
        await _create_entity(engine, "John Smith", "person", "A developer")

        entity = await engine.get_entity("John Smith")
        assert entity.name == "John Smith"
        assert entity.entity_type == "person"
        assert entity.description == "A developer"

    async def test_get_entity_case_insensitive(self, engine: GraphEngine) -> None:
        """Entity resolution falls back to case-insensitive match."""
        await _create_entity(engine, "John Smith", "person")

        entity = await engine.get_entity("john smith")
        assert entity.name == "John Smith"

    async def test_entity_not_found(self, engine: GraphEngine) -> None:
        with pytest.raises(EntityNotFoundError) as exc_info:
            await engine.get_entity("Nonexistent Entity")

        assert "Nonexistent Entity" in str(exc_info.value)
        assert exc_info.value.name == "Nonexistent Entity"
        assert isinstance(exc_info.value.suggestions, list)

    async def test_entity_not_found_with_suggestions(self, engine: GraphEngine) -> None:
        """EntityNotFoundError includes suggestions when similar entities exist."""
        await _create_entity(engine, "Alice Johnson", "person")

        with pytest.raises(EntityNotFoundError) as exc_info:
            await engine.get_entity("Alice")

        # The LIKE fallback should suggest "Alice Johnson"
        assert len(exc_info.value.suggestions) > 0
        assert "Alice Johnson" in exc_info.value.suggestions

    async def test_update_entity_description(self, engine: GraphEngine) -> None:
        await _create_entity(engine, "Alice", "person", "Original description")

        updated = await engine.update_entity("Alice", description="New description")
        assert updated.description == "New description"
        assert updated.name == "Alice"

        # Verify persistence
        fetched = await engine.get_entity("Alice")
        assert fetched.description == "New description"

    async def test_update_entity_properties_merge(self, engine: GraphEngine) -> None:
        """Updating properties merges with existing, not replaces."""
        await _create_entity(
            engine, "Alice", "person", properties={"role": "researcher", "age": 30}
        )

        updated = await engine.update_entity("Alice", properties={"department": "CS", "age": 31})

        # Old key "role" should still be present
        assert updated.properties["role"] == "researcher"
        # "age" should be updated to new value
        assert updated.properties["age"] == 31
        # New key "department" should be added
        assert updated.properties["department"] == "CS"

    async def test_update_entity_type(self, engine: GraphEngine) -> None:
        await _create_entity(engine, "MIT", "organization")

        updated = await engine.update_entity("MIT", entity_type="university")
        assert updated.entity_type == "university"

    async def test_delete_entity(self, engine: GraphEngine) -> None:
        await _create_entity(engine, "Alice", "person")

        # Verify entity exists
        entity = await engine.get_entity("Alice")
        assert entity.name == "Alice"

        deleted_count = await engine.delete_entities(["Alice"])
        assert deleted_count == 1

        # Verify entity is gone
        with pytest.raises(EntityNotFoundError):
            await engine.get_entity("Alice")

    async def test_delete_entity_cascades(self, engine: GraphEngine) -> None:
        """Deleting an entity also removes its observations and relationships."""
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        # Add a relationship
        await _create_relationship(engine, alice_id, bob_id, "knows")

        # Add observations to Alice
        obs = [Observation(entity_id=alice_id, content="Likes coffee")]
        await engine.add_observations("Alice", obs)

        # Delete Alice
        deleted = await engine.delete_entities(["Alice"])
        assert deleted == 1

        # Alice's relationships should be gone
        rels = await engine.get_relationships("Bob", direction="both")
        assert len(rels) == 0

        # Alice's observations should be gone (entity doesn't exist)
        with pytest.raises(EntityNotFoundError):
            await engine.get_observations("Alice")

    async def test_delete_nonexistent_entity(self, engine: GraphEngine) -> None:
        """Deleting a non-existent entity silently returns 0."""
        deleted = await engine.delete_entities(["Ghost"])
        assert deleted == 0

    async def test_delete_empty_list(self, engine: GraphEngine) -> None:
        deleted = await engine.delete_entities([])
        assert deleted == 0

    async def test_list_entities(self, engine: GraphEngine) -> None:
        names = ["Alice", "Bob", "Charlie", "Diana", "Eve"]
        for name in names:
            await _create_entity(engine, name, "person")

        entities = await engine.list_entities()
        assert len(entities) == 5
        returned_names = {e.name for e in entities}
        assert returned_names == set(names)

    async def test_list_entities_by_type(self, engine: GraphEngine) -> None:
        await _create_entity(engine, "Alice", "person")
        await _create_entity(engine, "Bob", "person")
        await _create_entity(engine, "MIT", "organization")
        await _create_entity(engine, "Stanford", "organization")
        await _create_entity(engine, "Python", "technology")

        people = await engine.list_entities(entity_type="person")
        assert len(people) == 2
        assert all(e.entity_type == "person" for e in people)

        orgs = await engine.list_entities(entity_type="organization")
        assert len(orgs) == 2
        assert all(e.entity_type == "organization" for e in orgs)

        tech = await engine.list_entities(entity_type="technology")
        assert len(tech) == 1

    async def test_list_entities_pagination(self, engine: GraphEngine) -> None:
        for i in range(10):
            await _create_entity(engine, f"Entity_{i:02d}", "item")

        page1 = await engine.list_entities(limit=3, offset=0)
        page2 = await engine.list_entities(limit=3, offset=3)

        assert len(page1) == 3
        assert len(page2) == 3
        # Pages should not overlap
        ids_1 = {e.id for e in page1}
        ids_2 = {e.id for e in page2}
        assert ids_1.isdisjoint(ids_2)


# ── Relationship CRUD ────────────────────────────────────────────────────────


class TestRelationshipCRUD:
    """Relationship create, read, delete operations."""

    async def test_add_relationship(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        rel = Relationship(
            source_id=alice_id,
            target_id=bob_id,
            relationship_type="knows",
            weight=0.9,
        )
        results = await engine.add_relationships([rel])

        assert len(results) == 1
        assert results[0]["status"] == "created"
        assert "id" in results[0]

    async def test_add_relationship_empty_list(self, engine: GraphEngine) -> None:
        results = await engine.add_relationships([])
        assert results == []

    async def test_add_relationship_merge_on_conflict(self, engine: GraphEngine) -> None:
        """Re-inserting same source+target+type updates weight and merges properties."""
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        rel_v1 = Relationship(
            source_id=alice_id,
            target_id=bob_id,
            relationship_type="knows",
            weight=0.5,
            properties={"context": "work"},
        )
        results_v1 = await engine.add_relationships([rel_v1])
        assert results_v1[0]["status"] == "created"
        rel_id = results_v1[0]["id"]

        rel_v2 = Relationship(
            source_id=alice_id,
            target_id=bob_id,
            relationship_type="knows",
            weight=0.9,
            properties={"since": "2024"},
        )
        results_v2 = await engine.add_relationships([rel_v2])
        assert results_v2[0]["status"] == "updated"
        # Same relationship ID
        assert results_v2[0]["id"] == rel_id

        # Verify the weight is the max (0.9)
        rels = await engine.get_relationships("Alice", direction="outgoing")
        assert len(rels) == 1
        assert rels[0]["weight"] == 0.9
        # Properties should be merged
        assert rels[0]["properties"]["context"] == "work"
        assert rels[0]["properties"]["since"] == "2024"

    async def test_get_relationships_both_directions(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        await _create_relationship(engine, alice_id, bob_id, "knows")

        # Query from Alice — should find the relationship
        rels_alice = await engine.get_relationships("Alice", direction="both")
        assert len(rels_alice) == 1
        assert rels_alice[0]["source_name"] == "Alice"
        assert rels_alice[0]["target_name"] == "Bob"

        # Query from Bob — should also find it with direction="both"
        rels_bob = await engine.get_relationships("Bob", direction="both")
        assert len(rels_bob) == 1
        assert rels_bob[0]["source_name"] == "Alice"
        assert rels_bob[0]["target_name"] == "Bob"

    async def test_get_relationships_outgoing(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        await _create_relationship(engine, alice_id, bob_id, "knows")

        # Alice has outgoing relationship
        rels = await engine.get_relationships("Alice", direction="outgoing")
        assert len(rels) == 1

        # Bob has no outgoing relationship
        rels_bob = await engine.get_relationships("Bob", direction="outgoing")
        assert len(rels_bob) == 0

    async def test_get_relationships_incoming(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        await _create_relationship(engine, alice_id, bob_id, "knows")

        # Bob has incoming relationship
        rels = await engine.get_relationships("Bob", direction="incoming")
        assert len(rels) == 1

        # Alice has no incoming relationship
        rels_alice = await engine.get_relationships("Alice", direction="incoming")
        assert len(rels_alice) == 0

    async def test_get_relationships_filter_by_type(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")
        mit_id = await _create_entity(engine, "MIT", "organization")

        await _create_relationship(engine, alice_id, bob_id, "knows")
        await _create_relationship(engine, alice_id, mit_id, "works_at")

        # Filter by "knows" only
        rels = await engine.get_relationships(
            "Alice", direction="outgoing", relationship_type="knows"
        )
        assert len(rels) == 1
        assert rels[0]["target_name"] == "Bob"

        # Filter by "works_at" only
        rels_work = await engine.get_relationships(
            "Alice", direction="outgoing", relationship_type="works_at"
        )
        assert len(rels_work) == 1
        assert rels_work[0]["target_name"] == "MIT"

    async def test_delete_relationships(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        await _create_relationship(engine, alice_id, bob_id, "knows")

        deleted = await engine.delete_relationships("Alice", "Bob")
        assert deleted == 1

        # Verify relationship is gone
        rels = await engine.get_relationships("Alice", direction="both")
        assert len(rels) == 0

    async def test_delete_relationships_by_type(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")

        await _create_relationship(engine, alice_id, bob_id, "knows")
        await _create_relationship(engine, alice_id, bob_id, "works_with")

        # Delete only "knows"
        deleted = await engine.delete_relationships("Alice", "Bob", relationship_type="knows")
        assert deleted == 1

        # "works_with" should still exist
        rels = await engine.get_relationships("Alice", direction="outgoing")
        assert len(rels) == 1
        assert rels[0]["relationship_type"] == "works_with"


# ── Observation CRUD ─────────────────────────────────────────────────────────


class TestObservationCRUD:
    """Observation create and read operations."""

    async def test_add_observations(self, engine: GraphEngine) -> None:
        entity_id = await _create_entity(engine, "Alice", "person")

        observations = [
            Observation(entity_id=entity_id, content="Likes coffee"),
            Observation(entity_id=entity_id, content="Works at MIT"),
        ]
        results = await engine.add_observations("Alice", observations)

        assert len(results) == 2
        assert all("id" in r for r in results)
        assert results[0]["content"] == "Likes coffee"
        assert results[1]["content"] == "Works at MIT"
        # entity_id should be the resolved entity's ID
        assert all(r["entity_id"] == entity_id for r in results)

    async def test_add_observations_empty_list(self, engine: GraphEngine) -> None:
        await _create_entity(engine, "Alice", "person")
        results = await engine.add_observations("Alice", [])
        assert results == []

    async def test_get_observations(self, engine: GraphEngine) -> None:
        entity_id = await _create_entity(engine, "Alice", "person")

        observations = [
            Observation(entity_id=entity_id, content="Likes coffee", source="session-1"),
            Observation(entity_id=entity_id, content="Works at MIT", source="session-2"),
        ]
        await engine.add_observations("Alice", observations)

        fetched = await engine.get_observations("Alice")
        assert len(fetched) == 2
        contents = {o.content for o in fetched}
        assert contents == {"Likes coffee", "Works at MIT"}
        # Verify these are actual Observation instances
        assert all(isinstance(o, Observation) for o in fetched)
        assert all(o.entity_id == entity_id for o in fetched)

    async def test_observations_resolve_entity(self, engine: GraphEngine) -> None:
        """add_observations resolves entity by name, not requiring the ID upfront."""
        await _create_entity(engine, "Alice", "person")

        # Use a placeholder entity_id — the engine resolves by name
        obs = [Observation(entity_id="placeholder", content="Loves graphs")]
        results = await engine.add_observations("Alice", obs)

        assert len(results) == 1
        # The returned entity_id should be the real ID, not "placeholder"
        assert results[0]["entity_id"] != "placeholder"

        # Verify it was actually stored
        fetched = await engine.get_observations("Alice")
        assert len(fetched) == 1
        assert fetched[0].content == "Loves graphs"

    async def test_observations_for_nonexistent_entity(self, engine: GraphEngine) -> None:
        obs = [Observation(entity_id="fake", content="Some fact")]
        with pytest.raises(EntityNotFoundError):
            await engine.add_observations("Ghost", obs)


# ── Stats ────────────────────────────────────────────────────────────────────


class TestStats:
    """Graph statistics queries."""

    async def test_get_stats_empty(self, engine: GraphEngine) -> None:
        stats = await engine.get_stats()

        assert stats["entities"] == 0
        assert stats["relationships"] == 0
        assert stats["observations"] == 0
        assert stats["entity_types"] == {}
        assert stats["relationship_types"] == {}
        assert stats["most_connected"] == []
        assert stats["recent_entities"] == []

    async def test_get_stats_populated(self, engine: GraphEngine) -> None:
        alice_id = await _create_entity(engine, "Alice", "person")
        bob_id = await _create_entity(engine, "Bob", "person")
        mit_id = await _create_entity(engine, "MIT", "organization")

        await _create_relationship(engine, alice_id, bob_id, "knows")
        await _create_relationship(engine, alice_id, mit_id, "works_at")

        obs = [
            Observation(entity_id=alice_id, content="Likes coffee"),
            Observation(entity_id=alice_id, content="Expert in ML"),
        ]
        await engine.add_observations("Alice", obs)

        stats = await engine.get_stats()

        assert stats["entities"] == 3
        assert stats["relationships"] == 2
        assert stats["observations"] == 2

        # Entity type distribution
        assert stats["entity_types"]["person"] == 2
        assert stats["entity_types"]["organization"] == 1

        # Relationship type distribution
        assert stats["relationship_types"]["knows"] == 1
        assert stats["relationship_types"]["works_at"] == 1

        # Most connected: Alice has 2 relationships (outgoing)
        assert len(stats["most_connected"]) == 3
        # Alice should be the most connected
        top = stats["most_connected"][0]
        assert top["name"] == "Alice"
        assert top["degree"] == 2

        # Recent entities
        assert len(stats["recent_entities"]) == 3
