"""End-to-end tests for MCP server tool functions.

Each test calls the tool function directly (not via MCP protocol) with
the module-level ``_state`` populated by a fixture. This exercises the
full stack: tool → engine → database.
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio

import graphrag_mcp.server as server_mod
from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.graph.merge import EntityMerger
from graphrag_mcp.graph.traversal import GraphTraversal
from graphrag_mcp.semantic.embeddings import EmbeddingEngine
from graphrag_mcp.semantic.search import HybridSearch
from graphrag_mcp.server import (
    add_entities,
    add_observations,
    add_relationships,
    delete_entities,
    find_connections,
    find_paths,
    get_entity,
    get_subgraph,
    merge_entities,
    read_graph,
    search_nodes,
    update_entity,
)
from graphrag_mcp.utils.config import Config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def setup_server(tmp_path: Path):
    """Populate the module-level _state so tool functions work."""
    from graphrag_mcp.storage import SQLiteBackend

    db_path = tmp_path / "test.db"
    storage = SQLiteBackend(db_path)
    await storage.initialize()

    # Leave embeddings uninitialised → available=False (no model download)
    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings)

    server_mod._state.storage = storage
    server_mod._state.graph = graph
    server_mod._state.traversal = traversal
    server_mod._state.merger = merger
    server_mod._state.embeddings = embeddings
    server_mod._state.search = search
    server_mod._state.config = Config(db_path=db_path)

    yield

    await storage.close()
    server_mod._state.storage = None
    server_mod._state.graph = None
    server_mod._state.traversal = None
    server_mod._state.merger = None
    server_mod._state.embeddings = None
    server_mod._state.search = None


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
