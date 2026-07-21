"""End-to-end tests for MCP server tool functions.

Each test calls the tool function directly (not via MCP protocol) with
the module-level ``_state`` populated by a fixture. This exercises the
full stack: tool → engine → database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest_asyncio

import graph_mem.tools._core as core
from graph_mem.graph.engine import GraphEngine
from graph_mem.graph.merge import EntityMerger
from graph_mem.graph.traversal import GraphTraversal
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import HybridSearch
from graph_mem.server import (
    add_entities,
    add_observations,
    add_relationships,
    compact_observations,
    delete_entities,
    delete_observations,
    delete_relationships,
    find_connections,
    find_paths,
    get_entity,
    get_subgraph,
    list_entities,
    list_relationships,
    merge_entities,
    read_graph,
    search_nodes,
    search_observations,
    update_entity,
    update_observation,
    update_relationship,
)
from graph_mem.utils.config import Config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def setup_server(tmp_path: Path):
    """Populate the module-level _state so tool functions work."""
    from graph_mem.storage import SQLiteBackend

    db_path = tmp_path / "test.db"
    storage = SQLiteBackend(db_path)
    await storage.initialize()

    # Leave embeddings uninitialised → available=False (no model download)
    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings)

    core._state.storage = storage
    core._state.graph = graph
    core._state.traversal = traversal
    core._state.merger = merger
    core._state.embeddings = embeddings
    core._state.search = search
    core._state.config = Config(db_path=db_path)

    yield

    await storage.close()
    core._state.storage = None
    core._state.graph = None
    core._state.traversal = None
    core._state.merger = None
    core._state.embeddings = None
    core._state.search = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_no_error(result: dict) -> None:
    assert "error" not in result, f"Unexpected error: {result}"


async def _add_entity(name: str, entity_type: str = "person", **kwargs) -> dict:
    """Shortcut: add a single entity and return its result dict."""
    result = await add_entities(entities=[{"name": name, "entity_type": entity_type, **kwargs}])
    _assert_no_error(result)
    return result["results"][0]


# ═══════════════════════════════════════════════════════════════════════════
# add_entities
# ═══════════════════════════════════════════════════════════════════════════


async def test_add_entities_basic(setup_server):
    result = await add_entities(
        entities=[{"name": "Alice", "entity_type": "person", "description": "A scientist"}]
    )
    _assert_no_error(result)
    assert result["count"] == 1
    r = result["results"][0]
    assert r["name"] == "Alice"
    assert r["status"] == "created"
    assert "id" in r


async def test_add_entities_batch(setup_server):
    result = await add_entities(
        entities=[
            {"name": "Alice", "entity_type": "person"},
            {"name": "Bob", "entity_type": "person"},
            {"name": "ACME Corp", "entity_type": "organization"},
        ]
    )
    _assert_no_error(result)
    assert result["count"] == 3
    names = {r["name"] for r in result["results"]}
    assert names == {"Alice", "Bob", "ACME Corp"}


async def test_add_entities_with_observations(setup_server):
    result = await add_entities(
        entities=[
            {
                "name": "Alice",
                "entity_type": "person",
                "observations": ["Likes tea", "Works at CERN"],
            }
        ]
    )
    _assert_no_error(result)
    assert result["count"] == 1

    # Verify observations were persisted
    entity = await get_entity(name="Alice")
    _assert_no_error(entity)
    assert len(entity["observations"]) == 2
    contents = {o["content"] for o in entity["observations"]}
    assert contents == {"Likes tea", "Works at CERN"}


async def test_add_entities_merge(setup_server):
    r1 = await add_entities(
        entities=[{"name": "Alice", "entity_type": "person", "description": "v1"}]
    )
    _assert_no_error(r1)
    assert r1["results"][0]["status"] == "created"

    r2 = await add_entities(
        entities=[{"name": "Alice", "entity_type": "person", "description": "v2"}]
    )
    _assert_no_error(r2)
    assert r2["results"][0]["status"] == "merged"
    # ID should be the same after merge
    assert r2["results"][0]["id"] == r1["results"][0]["id"]


async def test_add_entities_empty_list(setup_server):
    result = await add_entities(entities=[])
    _assert_no_error(result)
    assert result["count"] == 0
    assert result["results"] == []


# ═══════════════════════════════════════════════════════════════════════════
# add_relationships
# ═══════════════════════════════════════════════════════════════════════════


async def test_add_relationships_basic(setup_server):
    await _add_entity("Alice")
    await _add_entity("Bob")

    result = await add_relationships(
        relationships=[{"source": "Alice", "target": "Bob", "relationship_type": "knows"}]
    )
    _assert_no_error(result)
    assert result["count"] == 1
    r = result["results"][0]
    assert r["source"] == "Alice"
    assert r["target"] == "Bob"
    assert r["relationship_type"] == "knows"
    assert r["status"] == "created"


async def test_add_relationships_entity_not_found(setup_server):
    await _add_entity("Alice")

    result = await add_relationships(
        relationships=[{"source": "Alice", "target": "Ghost", "relationship_type": "knows"}]
    )
    assert result.get("error") is True
    assert "Ghost" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════
# add_observations
# ═══════════════════════════════════════════════════════════════════════════


async def test_add_observations_basic(setup_server):
    await _add_entity("Alice")

    result = await add_observations(
        entity_name="Alice",
        observations=["Speaks French", "Born in 1990"],
    )
    _assert_no_error(result)
    assert result["count"] == 2
    contents = {o["content"] for o in result["results"]}
    assert contents == {"Speaks French", "Born in 1990"}


async def test_add_observations_entity_not_found(setup_server):
    result = await add_observations(
        entity_name="Ghost",
        observations=["Some fact"],
    )
    assert result.get("error") is True
    assert "Ghost" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════
# update_entity
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_entity_description(setup_server):
    await _add_entity("Alice", description="Original")

    result = await update_entity(name="Alice", description="Updated bio")
    _assert_no_error(result)
    assert result["result"]["description"] == "Updated bio"
    assert result["result"]["name"] == "Alice"
    assert result["status"] == "updated"


async def test_update_entity_not_found(setup_server):
    result = await update_entity(name="Ghost", description="nope")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# delete_entities
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_entities_basic(setup_server):
    await _add_entity("Alice")

    result = await delete_entities(names=["Alice"])
    _assert_no_error(result)
    assert result["deleted"] == 1

    # Verify gone
    entity = await get_entity(name="Alice")
    assert entity.get("error") is True


async def test_delete_entities_cascade(setup_server):
    await _add_entity("Alice")
    await _add_entity("Bob")
    await add_relationships(
        relationships=[{"source": "Alice", "target": "Bob", "relationship_type": "knows"}]
    )

    result = await delete_entities(names=["Alice"])
    _assert_no_error(result)
    assert result["deleted"] == 1

    # Bob should still exist but the relationship should be gone
    bob = await get_entity(name="Bob")
    _assert_no_error(bob)
    assert len(bob["relationships"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# merge_entities
# ═══════════════════════════════════════════════════════════════════════════


async def test_merge_entities_basic(setup_server):
    await _add_entity("Alice", description="Person A")
    await _add_entity("Alice Duplicate", entity_type="person", description="Person A dup")

    result = await merge_entities(target="Alice", source="Alice Duplicate")
    _assert_no_error(result)
    assert result["result"]["target_name"] == "Alice"
    assert result["result"]["source_name"] == "Alice Duplicate"
    assert result["status"] == "merged"

    # Source should no longer exist
    gone = await get_entity(name="Alice Duplicate")
    assert gone.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# search_nodes
# ═══════════════════════════════════════════════════════════════════════════


async def test_search_nodes_empty_graph(setup_server):
    result = await search_nodes(query="anything")
    _assert_no_error(result)
    assert result["count"] == 0
    assert result["results"] == []


async def test_search_nodes_fts_only(setup_server):
    """Without embeddings, FTS5 keyword search should still return results."""
    await _add_entity(
        "Quantum Computing", entity_type="concept", description="A computing paradigm"
    )
    await _add_entity(
        "Classical Computing", entity_type="concept", description="Traditional computing"
    )

    result = await search_nodes(query="Quantum Computing")
    _assert_no_error(result)
    # FTS should find at least the exact-match entity
    assert result["count"] >= 1
    names = {r["name"] for r in result["results"]}
    assert "Quantum Computing" in names


# ═══════════════════════════════════════════════════════════════════════════
# get_entity
# ═══════════════════════════════════════════════════════════════════════════


async def test_get_entity_full(setup_server):
    await _add_entity("Alice", description="A researcher")
    await _add_entity("Bob")
    await add_observations(entity_name="Alice", observations=["PhD in Physics"])
    await add_relationships(
        relationships=[
            {"source": "Alice", "target": "Bob", "relationship_type": "collaborates_with"}
        ]
    )

    result = await get_entity(name="Alice")
    _assert_no_error(result)
    assert result["name"] == "Alice"
    assert result["description"] == "A researcher"
    assert len(result["observations"]) == 1
    assert result["observations"][0]["content"] == "PhD in Physics"
    assert len(result["relationships"]) >= 1


async def test_get_entity_not_found(setup_server):
    result = await get_entity(name="Nonexistent")
    assert result.get("error") is True
    assert result.get("error_type") == "EntityNotFoundError"


# ═══════════════════════════════════════════════════════════════════════════
# read_graph
# ═══════════════════════════════════════════════════════════════════════════


async def test_read_graph_empty(setup_server):
    result = await read_graph()
    _assert_no_error(result)
    assert result["entities"] == 0
    assert result["relationships"] == 0
    assert result["observations"] == 0


async def test_read_graph_populated(setup_server):
    await _add_entity("Alice")
    await _add_entity("Bob")
    await add_relationships(
        relationships=[{"source": "Alice", "target": "Bob", "relationship_type": "knows"}]
    )

    result = await read_graph()
    _assert_no_error(result)
    assert result["entities"] == 2
    assert result["relationships"] == 1
    assert "person" in result["entity_types"]
    assert result["entity_types"]["person"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# find_connections
# ═══════════════════════════════════════════════════════════════════════════


async def test_find_connections_basic(setup_server):
    await _add_entity("A")
    await _add_entity("B")
    await _add_entity("C")
    await add_relationships(
        relationships=[
            {"source": "A", "target": "B", "relationship_type": "links_to"},
            {"source": "B", "target": "C", "relationship_type": "links_to"},
        ]
    )

    result = await find_connections(entity_name="A", max_hops=3)
    _assert_no_error(result)
    assert result["source"] == "A"
    assert result["count"] >= 2
    found_names = {r["entity"]["name"] for r in result["results"]}
    assert "B" in found_names
    assert "C" in found_names


# ═══════════════════════════════════════════════════════════════════════════
# get_subgraph
# ═══════════════════════════════════════════════════════════════════════════


async def test_get_subgraph_basic(setup_server):
    await _add_entity("A")
    await _add_entity("B")
    await _add_entity("C")
    await add_relationships(
        relationships=[
            {"source": "A", "target": "B", "relationship_type": "links_to"},
            {"source": "B", "target": "C", "relationship_type": "links_to"},
        ]
    )

    result = await get_subgraph(entity_names=["A"], radius=2)
    _assert_no_error(result)
    entity_names = {e["name"] for e in result["entities"]}
    assert "A" in entity_names
    assert "B" in entity_names
    # C may or may not be included depending on radius interpretation
    assert len(result["relationships"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# find_paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_find_paths_basic(setup_server):
    await _add_entity("X")
    await _add_entity("Y")
    await _add_entity("Z")
    await add_relationships(
        relationships=[
            {"source": "X", "target": "Y", "relationship_type": "connects"},
            {"source": "Y", "target": "Z", "relationship_type": "connects"},
        ]
    )

    result = await find_paths(source="X", target="Z", max_hops=5)
    _assert_no_error(result)
    assert result["source"] == "X"
    assert result["target"] == "Z"
    assert result["count"] >= 1
    # The first (shortest) path should have 3 steps: X → Y → Z
    path = result["paths"][0]
    assert len(path) == 3
    path_names = [step["entity_name"] for step in path]
    assert path_names[0] == "X"
    assert path_names[-1] == "Z"


# ═══════════════════════════════════════════════════════════════════════════
# search_observations
# ═══════════════════════════════════════════════════════════════════════════


async def test_search_observations_tool(setup_server):
    """search_observations MCP tool returns results."""
    await add_entities([{"name": "TestEntity", "entity_type": "concept"}])
    await add_observations("TestEntity", ["The rate limit was increased to 500 req/s"])
    result = await search_observations(query="rate limit", limit=5)
    assert "results" in result
    assert result["count"] >= 0


# ═══════════════════════════════════════════════════════════════════════════
# delete_relationships
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_relationships_tool(setup_server):
    """delete_relationships removes edges between named entities."""
    await add_entities(
        [
            {"name": "DR_A", "entity_type": "person"},
            {"name": "DR_B", "entity_type": "person"},
        ]
    )
    await add_relationships(
        [
            {"source": "DR_A", "target": "DR_B", "relationship_type": "knows"},
        ]
    )
    result = await delete_relationships("DR_A", "DR_B", "knows")
    assert result["deleted"] == 1
    assert result["status"] == "deleted"


async def test_delete_relationships_all_types(setup_server):
    """delete_relationships without type removes all edges between the pair."""
    await add_entities(
        [
            {"name": "DR_X", "entity_type": "concept"},
            {"name": "DR_Y", "entity_type": "concept"},
        ]
    )
    await add_relationships(
        [
            {"source": "DR_X", "target": "DR_Y", "relationship_type": "relates_to"},
            {"source": "DR_X", "target": "DR_Y", "relationship_type": "depends_on"},
        ]
    )
    result = await delete_relationships("DR_X", "DR_Y")
    assert result["deleted"] == 2


async def test_delete_relationships_not_found(setup_server):
    """delete_relationships returns error for non-existent entities."""
    result = await delete_relationships("NoSuchA", "NoSuchB")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# update_relationship
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_relationship_weight(setup_server):
    """update_relationship can change the weight of an edge."""
    await add_entities(
        [
            {"name": "UR_A", "entity_type": "person"},
            {"name": "UR_B", "entity_type": "person"},
        ]
    )
    await add_relationships(
        [
            {"source": "UR_A", "target": "UR_B", "relationship_type": "knows", "weight": 0.5},
        ]
    )
    result = await update_relationship("UR_A", "UR_B", "knows", new_weight=0.9)
    assert result["status"] == "updated"
    assert result["source"] == "UR_A"
    assert result["target"] == "UR_B"


async def test_update_relationship_type(setup_server):
    """update_relationship can change the relationship type."""
    await add_entities(
        [
            {"name": "URT_A", "entity_type": "concept"},
            {"name": "URT_B", "entity_type": "concept"},
        ]
    )
    await add_relationships(
        [
            {"source": "URT_A", "target": "URT_B", "relationship_type": "relates_to"},
        ]
    )
    result = await update_relationship("URT_A", "URT_B", "relates_to", new_type="depends_on")
    assert result["status"] == "updated"
    assert result["relationship_type"] == "depends_on"


async def test_update_relationship_properties(setup_server):
    """update_relationship can merge properties."""
    await add_entities(
        [
            {"name": "URP_A", "entity_type": "concept"},
            {"name": "URP_B", "entity_type": "concept"},
        ]
    )
    await add_relationships(
        [
            {
                "source": "URP_A",
                "target": "URP_B",
                "relationship_type": "uses",
                "properties": {"since": "2024"},
            },
        ]
    )
    result = await update_relationship("URP_A", "URP_B", "uses", properties={"version": "2.0"})
    assert result["status"] == "updated"


async def test_update_relationship_not_found(setup_server):
    """update_relationship returns error for non-existent relationship."""
    await add_entities(
        [
            {"name": "URN_A", "entity_type": "person"},
            {"name": "URN_B", "entity_type": "person"},
        ]
    )
    result = await update_relationship("URN_A", "URN_B", "nonexistent")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# delete_observations
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_observations_tool(setup_server):
    """delete_observations removes specific observations from an entity."""
    await add_entities([{"name": "DO_Ent", "entity_type": "concept"}])
    add_result = await add_observations("DO_Ent", ["fact A", "fact B", "fact C"])
    obs_ids = [r["id"] for r in add_result["results"]]

    # Delete the first two
    result = await delete_observations("DO_Ent", obs_ids[:2])
    assert result["deleted"] == 2
    assert result["status"] == "deleted"

    # Verify only one remains
    entity_data = await get_entity("DO_Ent")
    assert len(entity_data["observations"]) == 1
    assert entity_data["observations"][0]["content"] == "fact C"


async def test_delete_observations_wrong_entity(setup_server):
    """delete_observations skips IDs that don't belong to the entity."""
    await add_entities(
        [
            {"name": "DO_E1", "entity_type": "concept"},
            {"name": "DO_E2", "entity_type": "concept"},
        ]
    )
    add1 = await add_observations("DO_E1", ["owned by E1"])
    obs_id = add1["results"][0]["id"]

    # Try to delete E1's observation from E2
    result = await delete_observations("DO_E2", [obs_id])
    assert result["deleted"] == 0


async def test_delete_observations_not_found(setup_server):
    """delete_observations returns error for non-existent entity."""
    result = await delete_observations("NonExistent", ["fake-id"])
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# update_observation
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_observation_tool(setup_server):
    """update_observation changes content in-place."""
    await add_entities([{"name": "UO_Ent", "entity_type": "concept"}])
    add_result = await add_observations("UO_Ent", ["original text"])
    obs_id = add_result["results"][0]["id"]

    result = await update_observation("UO_Ent", obs_id, "updated text")
    assert result["old_content"] == "original text"
    assert result["new_content"] == "updated text"

    # Verify the update persisted
    entity_data = await get_entity("UO_Ent")
    assert entity_data["observations"][0]["content"] == "updated text"


async def test_update_observation_wrong_entity(setup_server):
    """update_observation rejects IDs that don't belong to the entity."""
    await add_entities(
        [
            {"name": "UO_E1", "entity_type": "concept"},
            {"name": "UO_E2", "entity_type": "concept"},
        ]
    )
    add1 = await add_observations("UO_E1", ["belongs to E1"])
    obs_id = add1["results"][0]["id"]

    result = await update_observation("UO_E2", obs_id, "hijacked")
    assert result.get("error") is True


async def test_update_observation_not_found(setup_server):
    """update_observation returns error for non-existent observation."""
    await add_entities([{"name": "UON_Ent", "entity_type": "concept"}])
    result = await update_observation("UON_Ent", "fake-obs-id", "new text")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# list_entities
# ═══════════════════════════════════════════════════════════════════════════


async def test_list_entities_tool(setup_server):
    """list_entities returns paginated entity list."""
    await add_entities(
        [
            {"name": "LE_A", "entity_type": "person"},
            {"name": "LE_B", "entity_type": "person"},
            {"name": "LE_C", "entity_type": "concept"},
        ]
    )
    result = await list_entities()
    assert result["count"] == 3
    assert result["total"] == 3
    names = {e["name"] for e in result["results"]}
    assert names == {"LE_A", "LE_B", "LE_C"}


async def test_list_entities_filter_type(setup_server):
    """list_entities filters by entity_type."""
    await add_entities(
        [
            {"name": "LT_A", "entity_type": "person"},
            {"name": "LT_B", "entity_type": "concept"},
            {"name": "LT_C", "entity_type": "person"},
        ]
    )
    result = await list_entities(entity_type="person")
    assert result["count"] == 2
    names = {e["name"] for e in result["results"]}
    assert names == {"LT_A", "LT_C"}


async def test_list_entities_pagination(setup_server):
    """list_entities supports limit and offset."""
    await add_entities([{"name": f"LP_{i}", "entity_type": "concept"} for i in range(5)])
    result = await list_entities(limit=2, offset=0)
    assert result["count"] == 2
    assert result["total"] == 5

    result2 = await list_entities(limit=2, offset=2)
    assert result2["count"] == 2

    # No overlap
    names1 = {e["name"] for e in result["results"]}
    names2 = {e["name"] for e in result2["results"]}
    assert names1.isdisjoint(names2)


async def test_list_entities_empty(setup_server):
    """list_entities returns empty list when no entities exist."""
    result = await list_entities()
    assert result["count"] == 0
    assert result["results"] == []


# ═══════════════════════════════════════════════════════════════════════════
# list_relationships
# ═══════════════════════════════════════════════════════════════════════════


async def test_list_relationships_empty(setup_server):
    """list_relationships returns empty when no relationships exist."""
    result = await list_relationships()
    _assert_no_error(result)
    assert result["count"] == 0
    assert result["total"] == 0
    assert result["results"] == []


async def test_list_relationships_global(setup_server):
    """list_relationships without filters returns all relationships."""
    await _add_entity("Alice", "person")
    await _add_entity("Bob", "person")
    await _add_entity("ACME", "organization")
    await add_relationships(
        relationships=[
            {"source": "Alice", "target": "Bob", "relationship_type": "knows"},
            {"source": "Alice", "target": "ACME", "relationship_type": "works_at"},
        ]
    )

    result = await list_relationships()
    _assert_no_error(result)
    assert result["total"] == 2
    assert result["count"] == 2
    assert len(result["results"]) == 2

    # Verify enriched fields
    rel = result["results"][0]
    assert "source_name" in rel
    assert "target_name" in rel
    assert "relationship_type" in rel


async def test_list_relationships_by_entity(setup_server):
    """list_relationships scoped to a specific entity."""
    await _add_entity("Alice", "person")
    await _add_entity("Bob", "person")
    await _add_entity("Carol", "person")
    await add_relationships(
        relationships=[
            {"source": "Alice", "target": "Bob", "relationship_type": "knows"},
            {"source": "Bob", "target": "Carol", "relationship_type": "knows"},
        ]
    )

    # Alice has 1 relationship
    result = await list_relationships(entity_name="Alice")
    _assert_no_error(result)
    assert result["total"] == 1

    # Bob has 2 (one as source, one as target)
    result = await list_relationships(entity_name="Bob")
    _assert_no_error(result)
    assert result["total"] == 2


async def test_list_relationships_by_type(setup_server):
    """list_relationships filtered by relationship_type."""
    await _add_entity("Alice", "person")
    await _add_entity("Bob", "person")
    await _add_entity("ACME", "organization")
    await add_relationships(
        relationships=[
            {"source": "Alice", "target": "Bob", "relationship_type": "knows"},
            {"source": "Alice", "target": "ACME", "relationship_type": "works_at"},
        ]
    )

    result = await list_relationships(relationship_type="knows")
    _assert_no_error(result)
    assert result["total"] == 1
    assert result["results"][0]["relationship_type"] == "knows"

    result = await list_relationships(relationship_type="works_at")
    _assert_no_error(result)
    assert result["total"] == 1


async def test_list_relationships_pagination(setup_server):
    """list_relationships respects limit and offset."""
    await _add_entity("A", "node")
    await _add_entity("B", "node")
    await _add_entity("C", "node")
    await _add_entity("D", "node")
    await add_relationships(
        relationships=[
            {"source": "A", "target": "B", "relationship_type": "links"},
            {"source": "A", "target": "C", "relationship_type": "links"},
            {"source": "A", "target": "D", "relationship_type": "links"},
        ]
    )

    # First page
    result = await list_relationships(limit=2, offset=0)
    _assert_no_error(result)
    assert result["count"] == 2
    assert result["total"] == 3

    # Second page
    result2 = await list_relationships(limit=2, offset=2)
    _assert_no_error(result2)
    assert result2["count"] == 1
    assert result2["total"] == 3


async def test_list_relationships_limit_clamping(setup_server):
    """list_relationships clamps limit to [1, 500] and offset to >= 0."""
    result = await list_relationships(limit=0)
    _assert_no_error(result)
    assert result["limit"] == 1  # clamped from 0 to 1

    result = await list_relationships(limit=999)
    _assert_no_error(result)
    assert result["limit"] == 500  # clamped from 999 to 500

    result = await list_relationships(offset=-5)
    _assert_no_error(result)
    assert result["offset"] == 0  # clamped from -5 to 0


async def test_list_relationships_entity_and_type_combined(setup_server):
    """list_relationships with both entity_name and relationship_type filters."""
    await _add_entity("Alice", "person")
    await _add_entity("Bob", "person")
    await _add_entity("ACME", "organization")
    await add_relationships(
        relationships=[
            {"source": "Alice", "target": "Bob", "relationship_type": "knows"},
            {"source": "Alice", "target": "ACME", "relationship_type": "works_at"},
        ]
    )

    result = await list_relationships(entity_name="Alice", relationship_type="works_at")
    _assert_no_error(result)
    assert result["total"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# update_entity — rename paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_entity_type_change(setup_server):
    """update_entity with entity_type reclassifies the entity."""
    await _add_entity("Alice", "person")

    result = await update_entity(name="Alice", entity_type="scientist")
    _assert_no_error(result)
    assert result["result"]["entity_type"] == "scientist"


async def test_update_entity_noop(setup_server):
    """update_entity with no changes returns entity unchanged."""
    await _add_entity("Alice", "person", description="A scientist")

    result = await update_entity(name="Alice")
    _assert_no_error(result)
    assert result["result"]["name"] == "Alice"
    assert result["result"]["description"] == "A scientist"


# ═══════════════════════════════════════════════════════════════════════════
# search_nodes — entity_types filter
# ═══════════════════════════════════════════════════════════════════════════


async def test_search_nodes_empty_entity_types_treated_as_none(setup_server):
    """search_nodes with empty entity_types list normalizes to None (no filter)."""
    await _add_entity("Alice", "person", description="A scientist")

    # Empty list should not crash — should behave like no filter
    result = await search_nodes(query="Alice", entity_types=[])
    _assert_no_error(result)


async def test_search_nodes_whitespace_entity_types(setup_server):
    """search_nodes strips whitespace-only entity_types."""
    await _add_entity("Alice", "person", description="A scientist")

    result = await search_nodes(query="Alice", entity_types=["  ", ""])
    _assert_no_error(result)
    # Both entries are whitespace/empty, so after strip+filter → None → no filter


# ═══════════════════════════════════════════════════════════════════════════
# compact_observations — edge cases
# ═══════════════════════════════════════════════════════════════════════════


async def test_compact_observations_delete_only(setup_server):
    """compact_observations with empty new_observations just deletes."""
    await _add_entity("Alice", "person")
    obs_result = await add_observations(
        entity_name="Alice",
        observations=["Fact 1", "Fact 2", "Fact 3"],
    )
    _assert_no_error(obs_result)

    entity_data = await get_entity(name="Alice")
    obs = entity_data["observations"]
    keep_id = str(obs[0]["id"])

    result = await compact_observations(
        entity_name="Alice",
        keep_ids=[keep_id],
        new_observations=[],  # no new observations
    )
    _assert_no_error(result)
    assert result["deleted"] == 2
    assert result["added"] == 0
    assert result["after"] == 1


async def test_compact_observations_invalid_keep_ids(setup_server):
    """compact_observations rejects keep_ids that don't belong to the entity."""
    await _add_entity("Alice", "person")
    await add_observations(entity_name="Alice", observations=["Fact 1"])

    result = await compact_observations(
        entity_name="Alice",
        keep_ids=["nonexistent-id"],
        new_observations=["New fact"],
    )
    assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════
# search_observations — scoped to entity
# ═══════════════════════════════════════════════════════════════════════════


async def test_search_observations_scoped_to_entity(setup_server):
    """search_observations with entity_name resolves entity and scopes search."""
    await _add_entity("Alice", "person")
    await add_observations(entity_name="Alice", observations=["Loves quantum physics"])

    # Should not crash — scoped search
    result = await search_observations(query="quantum", entity_name="Alice")
    _assert_no_error(result)


async def test_search_observations_nonexistent_entity(setup_server):
    """search_observations with nonexistent entity_name returns error."""
    result = await search_observations(query="anything", entity_name="Ghost")
    assert "error" in result
