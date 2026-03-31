"""Tests for GraphTraversal — multi-hop BFS, path-finding, and subgraph extraction.

Each test uses a fresh temporary SQLite database with migrations applied.
Graph structures are built from scratch per test for full isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.graph.traversal import GraphTraversal
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.relationship import Relationship
from graphrag_mcp.storage import SQLiteBackend

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


@pytest_asyncio.fixture
async def traversal(db: SQLiteBackend) -> GraphTraversal:
    return GraphTraversal(db)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _create_entity(
    engine: GraphEngine,
    name: str,
    entity_type: str = "person",
    description: str = "",
) -> str:
    """Create an entity and return its ID."""
    entity = Entity(name=name, entity_type=entity_type, description=description)
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


async def _build_chain(engine: GraphEngine, names: list[str], rel_type: str = "knows") -> list[str]:
    """Build a linear chain A→B→C→... and return the entity IDs in order."""
    ids = []
    for name in names:
        eid = await _create_entity(engine, name)
        ids.append(eid)
    for i in range(len(ids) - 1):
        await _create_relationship(engine, ids[i], ids[i + 1], rel_type)
    return ids


# ── find_connections ─────────────────────────────────────────────────────────


class TestFindConnections:
    """BFS connection discovery from a starting entity."""

    async def test_find_connections_direct(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B: finding connections from A with max_hops=1 should yield B."""
        ids = await _build_chain(engine, ["Alice", "Bob"])

        results = await traversal.find_connections(ids[0], max_hops=1)

        assert len(results) == 1
        assert results[0]["entity"]["name"] == "Bob"
        assert results[0]["depth"] == 1

    async def test_find_connections_multi_hop(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C: finding from A with max_hops=2 should find both B and C."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        results = await traversal.find_connections(ids[0], max_hops=2)

        found_names = {r["entity"]["name"] for r in results}
        assert found_names == {"Bob", "Charlie"}

        # Verify depths
        depths = {r["entity"]["name"]: r["depth"] for r in results}
        assert depths["Bob"] == 1
        assert depths["Charlie"] == 2

    async def test_find_connections_respects_max_hops(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C: finding from A with max_hops=1 should only find B, not C."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        results = await traversal.find_connections(ids[0], max_hops=1)

        found_names = {r["entity"]["name"] for r in results}
        assert found_names == {"Bob"}

    async def test_find_connections_cycle_detection(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C→A cycle: traversal should not loop infinitely."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        c_id = await _create_entity(engine, "Charlie")

        await _create_relationship(engine, a_id, b_id, "knows")
        await _create_relationship(engine, b_id, c_id, "knows")
        await _create_relationship(engine, c_id, a_id, "knows")

        # Should complete without hanging and find B and C (not A again)
        results = await traversal.find_connections(a_id, max_hops=5)

        found_names = {r["entity"]["name"] for r in results}
        assert "Bob" in found_names
        assert "Charlie" in found_names
        # A should not appear in its own results (depth > 0 filter)
        assert "Alice" not in found_names

    async def test_find_connections_direction_outgoing(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C with direction=outgoing from B should only find C."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        results = await traversal.find_connections(ids[1], max_hops=2, direction="outgoing")

        found_names = {r["entity"]["name"] for r in results}
        assert "Charlie" in found_names
        # Alice is upstream, should not be found with outgoing-only
        assert "Alice" not in found_names

    async def test_find_connections_direction_incoming(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C with direction=incoming from B should only find A."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        results = await traversal.find_connections(ids[1], max_hops=2, direction="incoming")

        found_names = {r["entity"]["name"] for r in results}
        assert "Alice" in found_names
        assert "Charlie" not in found_names

    async def test_find_connections_direction_both(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C with direction=both from B should find both A and C."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        results = await traversal.find_connections(ids[1], max_hops=2, direction="both")

        found_names = {r["entity"]["name"] for r in results}
        assert "Alice" in found_names
        assert "Charlie" in found_names

    async def test_find_connections_relationship_type_filter(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Filter connections by specific relationship type."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        c_id = await _create_entity(engine, "MIT", "organization")

        await _create_relationship(engine, a_id, b_id, "knows")
        await _create_relationship(engine, a_id, c_id, "works_at")

        # Only follow "knows" relationships
        results = await traversal.find_connections(a_id, max_hops=1, relationship_types=["knows"])

        found_names = {r["entity"]["name"] for r in results}
        assert found_names == {"Bob"}
        assert "MIT" not in found_names

    async def test_find_connections_no_neighbors(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Isolated entity should have no connections."""
        a_id = await _create_entity(engine, "Alice")

        results = await traversal.find_connections(a_id, max_hops=3)
        assert results == []

    async def test_find_connections_path_info(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Verify that path information is included in results."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        results = await traversal.find_connections(ids[0], max_hops=2)

        # Find Charlie's result (depth 2) and check the path
        charlie_result = next(r for r in results if r["entity"]["name"] == "Charlie")
        assert "path" in charlie_result
        assert len(charlie_result["path"]) == 2  # Two hops: A→B, B→C


# ── find_paths ───────────────────────────────────────────────────────────────


class TestFindPaths:
    """Shortest path discovery between two entities."""

    async def test_find_paths_direct(self, engine: GraphEngine, traversal: GraphTraversal) -> None:
        """A→B: direct path should be found."""
        ids = await _build_chain(engine, ["Alice", "Bob"])

        paths = await traversal.find_paths(ids[0], ids[1], max_hops=3)

        assert len(paths) >= 1
        # The shortest path has 2 steps (source + target)
        shortest = paths[0]
        entity_names = [step["entity_name"] for step in shortest]
        assert "Alice" in entity_names
        assert "Bob" in entity_names

    async def test_find_paths_multi_hop(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C: find path from A to C through B."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie"])

        paths = await traversal.find_paths(ids[0], ids[2], max_hops=5)

        assert len(paths) >= 1
        shortest = paths[0]
        entity_names = [step["entity_name"] for step in shortest]
        assert "Alice" in entity_names
        assert "Bob" in entity_names
        assert "Charlie" in entity_names

    async def test_find_paths_no_path(self, engine: GraphEngine, traversal: GraphTraversal) -> None:
        """Two disconnected entities should have no paths between them."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        # No relationship between them

        paths = await traversal.find_paths(a_id, b_id, max_hops=5)
        assert paths == []

    async def test_find_paths_same_entity(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Source == target should return empty (no trivial self-path)."""
        a_id = await _create_entity(engine, "Alice")

        paths = await traversal.find_paths(a_id, a_id, max_hops=5)
        assert paths == []

    async def test_find_paths_respects_max_hops(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """A→B→C→D: path from A to D with max_hops=2 should find nothing (needs 3 hops)."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie", "Diana"])

        paths = await traversal.find_paths(ids[0], ids[3], max_hops=2)
        assert paths == []

        # But with max_hops=3 it should work
        paths = await traversal.find_paths(ids[0], ids[3], max_hops=3)
        assert len(paths) >= 1

    async def test_find_paths_with_cycle(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Graph with cycle: still finds a valid path without looping."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        c_id = await _create_entity(engine, "Charlie")
        d_id = await _create_entity(engine, "Diana")

        # A→B→C→D with a back-edge C→A creating a cycle
        await _create_relationship(engine, a_id, b_id, "knows")
        await _create_relationship(engine, b_id, c_id, "knows")
        await _create_relationship(engine, c_id, d_id, "knows")
        await _create_relationship(engine, c_id, a_id, "knows")

        paths = await traversal.find_paths(a_id, d_id, max_hops=5)
        assert len(paths) >= 1


# ── get_subgraph ─────────────────────────────────────────────────────────────


class TestGetSubgraph:
    """Subgraph extraction around seed entities."""

    async def test_get_subgraph_single_seed(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Single seed with radius=1 should include direct neighbors."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        c_id = await _create_entity(engine, "Charlie")
        # Disconnected
        await _create_entity(engine, "Diana")

        await _create_relationship(engine, a_id, b_id, "knows")
        await _create_relationship(engine, a_id, c_id, "knows")

        subgraph = await traversal.get_subgraph([a_id], radius=1)

        entity_names = {e["name"] for e in subgraph["entities"]}
        # Alice, Bob, Charlie should be in the subgraph; Diana should not
        assert "Alice" in entity_names
        assert "Bob" in entity_names
        assert "Charlie" in entity_names
        assert "Diana" not in entity_names

        # Relationships between them should be included
        assert len(subgraph["relationships"]) == 2

    async def test_get_subgraph_multiple_seeds(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Multiple seeds should produce a union of their neighborhoods."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        c_id = await _create_entity(engine, "Charlie")
        d_id = await _create_entity(engine, "Diana")

        # Two disconnected pairs: A→B and C→D
        await _create_relationship(engine, a_id, b_id, "knows")
        await _create_relationship(engine, c_id, d_id, "knows")

        # Using both A and C as seeds
        subgraph = await traversal.get_subgraph([a_id, c_id], radius=1)

        entity_names = {e["name"] for e in subgraph["entities"]}
        assert entity_names == {"Alice", "Bob", "Charlie", "Diana"}
        assert len(subgraph["relationships"]) == 2

    async def test_get_subgraph_empty(self, engine: GraphEngine, traversal: GraphTraversal) -> None:
        """No entity IDs should return empty subgraph."""
        subgraph = await traversal.get_subgraph([])
        assert subgraph == {"entities": [], "relationships": []}

    async def test_get_subgraph_radius_expansion(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Radius=2 should reach two hops from seed."""
        ids = await _build_chain(engine, ["Alice", "Bob", "Charlie", "Diana"])

        # Seed is Alice, radius=2: should reach Alice, Bob, Charlie but not Diana
        subgraph = await traversal.get_subgraph([ids[0]], radius=2)

        entity_names = {e["name"] for e in subgraph["entities"]}
        assert "Alice" in entity_names
        assert "Bob" in entity_names
        assert "Charlie" in entity_names
        # Diana is 3 hops away, should not be included
        assert "Diana" not in entity_names

    async def test_get_subgraph_includes_internal_relationships(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Subgraph should include all relationships between discovered entities."""
        a_id = await _create_entity(engine, "Alice")
        b_id = await _create_entity(engine, "Bob")
        c_id = await _create_entity(engine, "Charlie")

        # Triangle: A→B, B→C, A→C
        await _create_relationship(engine, a_id, b_id, "knows")
        await _create_relationship(engine, b_id, c_id, "knows")
        await _create_relationship(engine, a_id, c_id, "knows")

        subgraph = await traversal.get_subgraph([a_id], radius=1)

        assert len(subgraph["entities"]) == 3
        # All 3 internal relationships should be included
        assert len(subgraph["relationships"]) == 3

    async def test_get_subgraph_isolated_seed(
        self, engine: GraphEngine, traversal: GraphTraversal
    ) -> None:
        """Seed with no relationships returns just the seed entity."""
        a_id = await _create_entity(engine, "Alice")

        subgraph = await traversal.get_subgraph([a_id], radius=2)

        assert len(subgraph["entities"]) == 1
        assert subgraph["entities"][0]["name"] == "Alice"
        assert subgraph["relationships"] == []
