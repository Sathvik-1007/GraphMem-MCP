"""Search and traversal tools — search_nodes, search_observations,
find_connections, find_paths, get_subgraph, read_graph.
"""

from __future__ import annotations

from typing import Any

from graph_mem.utils import GraphMemError

from ._core import _error_response, _require_state, mcp


@mcp.tool()
async def search_nodes(
    query: str,
    limit: int = 10,
    entity_types: list[str] | None = None,
    include_observations: bool = False,
) -> dict[str, Any]:
    """Search the knowledge graph using hybrid semantic + full-text search.

    Combines vector similarity (cosine distance) and FTS5 keyword matching
    using Reciprocal Rank Fusion for robust ranking. Returns entities sorted
    by relevance with their direct relationships.

    Args:
        query: Natural language search query.
        limit: Maximum results to return (default 10).
        entity_types: Optional filter to specific types (e.g. ['person', 'concept']).
        include_observations: Whether to include entity observations in results.
    """
    try:
        state = _require_state()

        results = await state.search.search_entities(
            query,
            limit=limit,
            entity_types=entity_types,
            include_observations=include_observations,
        )

        return {"results": results, "count": len(results), "query": query}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="search_nodes")


@mcp.tool()
async def search_observations(
    query: str,
    limit: int = 10,
    entity_name: str | None = None,
) -> dict[str, Any]:
    """Search observations using hybrid semantic + full-text search.

    Searches the text content of observations (atomic facts attached to entities)
    using combined vector similarity and FTS5 keyword matching. Useful for finding
    specific facts, events, or details that may not be reflected in entity names
    or descriptions.

    Args:
        query: Natural language search query.
        limit: Maximum results to return (default 10).
        entity_name: Optional — restrict search to observations belonging to this entity.
    """
    try:
        state = _require_state()

        # If entity_name given, resolve to entity_id
        entity_id: str | None = None
        if entity_name:
            entity = await state.graph.resolve_entity(entity_name)
            entity_id = entity.id

        results = await state.search.search_observations(
            query,
            limit=limit,
            entity_id=entity_id,
        )

        return {"results": results, "count": len(results), "query": query}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="search_observations")


@mcp.tool()
async def find_connections(
    entity_name: str,
    max_hops: int = 3,
    relationship_types: list[str] | None = None,
    direction: str = "both",
) -> dict[str, Any]:
    """Discover entities connected to a given entity via multi-hop graph traversal.

    Uses BFS with cycle detection. Useful for exploring the neighbourhood of
    an entity — finding related people, concepts, or dependencies.

    Args:
        entity_name: Starting entity name.
        max_hops: How far to traverse (1-10, default 3).
        relationship_types: Optional filter on edge types (e.g. ['knows', 'works_at']).
        direction: 'outgoing', 'incoming', or 'both' (default).
    """
    try:
        state = _require_state()

        entity = await state.graph.resolve_entity(entity_name)
        results = await state.traversal.find_connections(
            entity.id,
            max_hops=max_hops,
            relationship_types=relationship_types,
            direction=direction,
        )

        return {
            "source": entity_name,
            "results": results,
            "count": len(results),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="find_connections")


@mcp.tool()
async def get_subgraph(
    entity_names: list[str],
    radius: int = 2,
) -> dict[str, Any]:
    """Extract a neighbourhood subgraph around one or more seed entities.

    Expands outward from each seed entity up to *radius* hops, collecting
    all reachable entities and the relationships between them. Useful for
    visualisation or context gathering around a set of topics.

    Args:
        entity_names: Seed entity names to expand from.
        radius: How many hops to expand (1-5, default 2).
    """
    try:
        state = _require_state()

        entity_ids: list[str] = []
        for name in entity_names:
            entity = await state.graph.resolve_entity(name)
            entity_ids.append(entity.id)

        result = await state.traversal.get_subgraph(entity_ids, radius=radius)
        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="get_subgraph")


@mcp.tool()
async def find_paths(
    source: str,
    target: str,
    max_hops: int = 5,
) -> dict[str, Any]:
    """Find shortest paths between two entities in the knowledge graph.

    Uses BFS to discover how two entities are connected. Returns up to 10
    shortest paths, each as a sequence of entities and relationship types.

    Args:
        source: Starting entity name.
        target: Destination entity name.
        max_hops: Maximum path length to consider (1-10, default 5).
    """
    try:
        state = _require_state()

        source_entity = await state.graph.resolve_entity(source)
        target_entity = await state.graph.resolve_entity(target)

        paths = await state.traversal.find_paths(
            source_entity.id,
            target_entity.id,
            max_hops=max_hops,
        )

        return {
            "source": source,
            "target": target,
            "paths": paths,
            "count": len(paths),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="find_paths")


@mcp.tool()
async def read_graph() -> dict[str, Any]:
    """Get an overview of the knowledge graph: entity/relationship/observation counts,
    type distributions, most connected entities, and recently updated entities.

    Takes no arguments. Useful for understanding the current state of the graph.
    """
    try:
        state = _require_state()

        result: dict[str, Any] = dict(await state.graph.get_stats())
        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="read_graph")
