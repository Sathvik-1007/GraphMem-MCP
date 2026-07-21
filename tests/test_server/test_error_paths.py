"""Tests for server.py error branches and edge cases.

Covers the except clauses in each tool, the embedding helper functions,
and other paths not exercised by the happy-path tool tests.
"""

from __future__ import annotations

from graph_mem.server import (
    add_entities,
    add_observations,
    add_relationships,
    delete_entities,
    delete_observations,
    delete_relationships,
    find_connections,
    find_paths,
    get_entity,
    get_subgraph,
    list_entities,
    merge_entities,
    read_graph,
    search_nodes,
    search_observations,
    update_entity,
    update_observation,
    update_relationship,
)
from graph_mem.tools._core import (
    _embed_entities,
    _embed_observations,
)


async def test_add_entities_missing_name(setup_server):
    """add_entities with missing 'name' key returns error."""
    result = await add_entities(entities=[{"entity_type": "person"}])
    assert result.get("error") is True
    assert "Invalid input" in result["message"]


async def test_add_entities_missing_type(setup_server):
    """add_entities with missing 'entity_type' key returns error."""
    result = await add_entities(entities=[{"name": "Alice"}])
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# add_relationships error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_add_relationships_nonexistent_source(setup_server):
    """add_relationships with nonexistent source returns error."""
    result = await add_relationships(
        relationships=[{"source": "Ghost", "target": "Nobody", "relationship_type": "knows"}]
    )
    assert result.get("error") is True


async def test_add_relationships_missing_key(setup_server):
    """add_relationships with missing required key returns error."""
    result = await add_relationships(relationships=[{"source": "A"}])
    assert result.get("error") is True
    assert "Invalid input" in result["message"]


# ═══════════════════════════════════════════════════════════════════════════
# add_observations error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_add_observations_nonexistent_entity(setup_server):
    """add_observations for nonexistent entity returns error."""
    result = await add_observations(entity_name="Ghost", observations=["fact"])
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# update_entity error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_entity_nonexistent(setup_server):
    """update_entity for nonexistent entity returns error."""
    result = await update_entity(name="Ghost", description="new desc")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# delete_entities error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_entities_nonexistent(setup_server):
    """delete_entities for nonexistent entity succeeds with 0 deleted."""
    result = await delete_entities(names=["Ghost"])
    # Not an error — just 0 deleted
    assert result["deleted"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# merge_entities error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_merge_entities_nonexistent(setup_server):
    """merge_entities with nonexistent entity returns error."""
    result = await merge_entities(target="Ghost1", source="Ghost2")
    assert result.get("error") is True


async def test_merge_entities_self(setup_server):
    """merge_entities merging entity with itself returns error."""
    await add_entities(entities=[{"name": "Alice", "entity_type": "person"}])
    result = await merge_entities(target="Alice", source="Alice")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# delete_relationships error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_relationships_nonexistent(setup_server):
    """delete_relationships for nonexistent entities returns error."""
    result = await delete_relationships(source="Ghost1", target="Ghost2")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# update_relationship error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_relationship_nonexistent(setup_server):
    """update_relationship for nonexistent entities returns error."""
    result = await update_relationship(source="Ghost1", target="Ghost2", relationship_type="knows")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# delete_observations error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_observations_nonexistent(setup_server):
    """delete_observations for nonexistent entity returns error."""
    result = await delete_observations(entity_name="Ghost", observation_ids=["fake-id"])
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# update_observation error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_update_observation_nonexistent(setup_server):
    """update_observation for nonexistent entity returns error."""
    result = await update_observation(entity_name="Ghost", observation_id="fake-id", content="new")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# search error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_search_nodes_empty(setup_server):
    """search_nodes with no data returns empty results."""
    result = await search_nodes(query="anything")
    assert result["count"] == 0


async def test_search_observations_empty(setup_server):
    """search_observations with no data returns empty results."""
    result = await search_observations(query="anything")
    assert result["count"] == 0


async def test_search_observations_with_entity(setup_server):
    """search_observations scoped to a nonexistent entity returns error."""
    result = await search_observations(query="test", entity_name="Ghost")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# find_connections / find_paths / get_subgraph error paths
# ═══════════════════════════════════════════════════════════════════════════


async def test_find_connections_nonexistent(setup_server):
    """find_connections for nonexistent entity returns error."""
    result = await find_connections(entity_name="Ghost")
    assert result.get("error") is True


async def test_find_paths_nonexistent(setup_server):
    """find_paths with nonexistent entities returns error."""
    result = await find_paths(source="Ghost1", target="Ghost2")
    assert result.get("error") is True


async def test_get_subgraph_nonexistent(setup_server):
    """get_subgraph with nonexistent entity returns error."""
    result = await get_subgraph(entity_names=["Ghost"])
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════
# get_entity / read_graph
# ═══════════════════════════════════════════════════════════════════════════


async def test_get_entity_nonexistent(setup_server):
    """get_entity for nonexistent entity returns error with suggestions."""
    result = await get_entity(name="Ghost")
    assert result.get("error") is True


async def test_read_graph_empty(setup_server):
    """read_graph on empty database returns zero counts."""
    result = await read_graph()
    assert result["entities"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# list_entities edge cases
# ═══════════════════════════════════════════════════════════════════════════


async def test_list_entities_clamp_limit(setup_server):
    """list_entities clamps limit to [1, 500]."""
    result = await list_entities(limit=9999)
    assert result["limit"] == 500

    result2 = await list_entities(limit=-5)
    assert result2["limit"] == 1


async def test_list_entities_negative_offset(setup_server):
    """list_entities clamps negative offset to 0."""
    result = await list_entities(offset=-10)
    assert result["offset"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# _embed_entities / _embed_observations when embeddings unavailable
# ═══════════════════════════════════════════════════════════════════════════


async def test_embed_entities_noop_when_unavailable(setup_server):
    """_embed_entities is a no-op when embeddings.available is False."""
    # Embeddings not available (no model loaded) — should return without error
    await _embed_entities(["fake-id"])


async def test_embed_entities_empty_list(setup_server):
    """_embed_entities with empty list is a no-op."""
    await _embed_entities([])


async def test_embed_observations_noop_when_unavailable(setup_server):
    """_embed_observations is a no-op when embeddings.available is False."""
    await _embed_observations([{"id": "x", "entity_id": "y", "content": "test"}])


async def test_embed_observations_empty_list(setup_server):
    """_embed_observations with empty list is a no-op."""
    await _embed_observations([])
