"""Search and traversal tools — search_nodes, search_observations,
find_connections, find_paths, get_subgraph, read_graph.
"""

from __future__ import annotations

from typing import Any, Literal

from graph_mem.graph.traversal import MAX_HOPS_LIMIT, MAX_RADIUS_LIMIT
from graph_mem.utils import GraphMemError

from ._core import (
    MAX_SEARCH_LIMIT,
    MAX_TRAVERSAL_RESULTS,
    _clamp_limit,
    _error_response,
    _require_state,
    _require_text,
    _require_text_list,
    tool,
)


@tool()
async def search_nodes(
    query: str,
    limit: int = 5,
    entity_types: list[str] | None = None,
    include_observations: bool = False,
) -> dict[str, Any]:
    """Search the knowledge graph using hybrid semantic + full-text search.

    Combines vector similarity (cosine distance) and FTS5 keyword matching
    using Reciprocal Rank Fusion for robust ranking. Returns entities sorted
    by relevance with their direct relationships.

    Args:
        query: Natural language search query.
        limit: Maximum results to return (default 5, clamped to 1-100).
        entity_types: Optional filter to specific types (e.g. ['person', 'concept']).
        include_observations: Whether to include entity observations in results.

    Raises:
        GraphMemError: If the search engine or database encounters an error.
    """
    try:
        state = _require_state()

        query = _require_text(query, "query", allow_empty=True)
        # Clamped before it reaches the retrieval channels: each is asked for
        # limit * 3 rows, and a negative SQL LIMIT means "unbounded" in SQLite.
        limit = _clamp_limit(limit, maximum=MAX_SEARCH_LIMIT)

        # Normalize entity_types to match stored lowercase values
        if entity_types is not None:
            checked = _require_text_list(entity_types, "entity_types", allow_empty_items=True)
            entity_types = [t.lower() for t in checked if t]
            if not entity_types:
                entity_types = None

        results = await state.search.search_entities(
            query,
            limit=limit,
            entity_types=entity_types,
            include_observations=include_observations,
        )

        return {"results": results, "count": len(results), "query": query, "limit": limit}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="search_nodes")


@tool()
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
        limit: Maximum results to return (default 10, clamped to 1-100).
        entity_name: Optional — restrict search to observations belonging to this entity.
    """
    try:
        state = _require_state()

        query = _require_text(query, "query", allow_empty=True)
        limit = _clamp_limit(limit, maximum=MAX_SEARCH_LIMIT)

        # If entity_name given, resolve to entity_id
        entity_id: str | None = None
        if entity_name:
            entity = await state.graph.resolve_entity(_require_text(entity_name, "entity_name"))
            entity_id = entity.id

        results = await state.search.search_observations(
            query,
            limit=limit,
            entity_id=entity_id,
        )

        return {"results": results, "count": len(results), "query": query, "limit": limit}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="search_observations")


@tool()
async def find_connections(
    entity_name: str,
    max_hops: int = 2,
    relationship_types: list[str] | None = None,
    direction: Literal["outgoing", "incoming", "both"] = "both",
) -> dict[str, Any]:
    """Start from ONE entity and list everything reachable from it — use this to
    answer "what is related to X?".

    Differs from get_subgraph (many seeds, returns entities *and* the edges
    between them, for visualising a region) and from find_paths (two fixed
    endpoints, returns the routes between them). This one returns a flat,
    depth-ordered list with one shortest route to each entity found.

    Args:
        entity_name: Starting entity name.
        max_hops: How far to traverse (clamped to 1-10, default 2).
        relationship_types: Optional filter on edge types (e.g. ['knows', 'works_at']).
        direction: 'outgoing', 'incoming', or 'both' (default).

    Returns:
        Up to 200 entities; ``truncated`` is true when more were reachable.
    """
    try:
        state = _require_state()

        entity_name = _require_text(entity_name, "entity_name")
        max_hops = _clamp_limit(max_hops, maximum=MAX_HOPS_LIMIT)
        if relationship_types is not None:
            relationship_types = _require_text_list(relationship_types, "relationship_types")

        entity = await state.graph.resolve_entity(entity_name)
        # An unrecognised direction raises ValidationError (a GraphMemError)
        # from the traversal layer, which the handler below turns into a
        # structured response.
        results = await state.traversal.find_connections(
            entity.id,
            max_hops=max_hops,
            relationship_types=relationship_types,
            direction=direction,
        )

        capped = results[:MAX_TRAVERSAL_RESULTS]
        return {
            "source": entity_name,
            "results": capped,
            "count": len(capped),
            "truncated": len(results) > MAX_TRAVERSAL_RESULTS,
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="find_connections")


@tool()
async def get_subgraph(
    entity_names: list[str],
    radius: int = 2,
) -> dict[str, Any]:
    """Extract a whole region around SEVERAL seed entities — use this when you
    need the edges too, e.g. to draw or reason about a neighbourhood.

    Differs from find_connections (single seed, flat list of reachable entities,
    no edge list) and from find_paths (routes between two named endpoints).

    Args:
        entity_names: Seed entity names to expand from.
        radius: How many hops to expand (clamped to 1-5, default 2).

    Returns:
        ``{entities, relationships, truncated}``. At most 200 entities are
        returned, with relationships restricted to that set; ``truncated`` is
        true when the region was larger than that (or larger than the
        traversal layer's own node budget).
    """
    try:
        state = _require_state()

        names = _require_text_list(entity_names, "entity_names")
        radius = _clamp_limit(radius, maximum=MAX_RADIUS_LIMIT)

        entity_ids: list[str] = []
        for name in names:
            entity = await state.graph.resolve_entity(name)
            entity_ids.append(entity.id)

        result = await state.traversal.get_subgraph(entity_ids, radius=radius)

        entities = result["entities"]
        truncated = bool(result["truncated"])
        if len(entities) > MAX_TRAVERSAL_RESULTS:
            entities = entities[:MAX_TRAVERSAL_RESULTS]
            truncated = True
            # Edges pointing at dropped entities would describe a graph the
            # caller cannot see, so keep only edges between the ones returned.
            kept_ids = {str(e["id"]) for e in entities}
            result["relationships"] = [
                r
                for r in result["relationships"]
                if str(r["source_id"]) in kept_ids and str(r["target_id"]) in kept_ids
            ]
            result["entities"] = entities

        result["truncated"] = truncated
        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="get_subgraph")


@tool()
async def find_paths(
    source: str,
    target: str,
    max_hops: int = 5,
) -> dict[str, Any]:
    """Show how TWO named entities connect — use this when you already know both
    endpoints and want the chain between them.

    Differs from find_connections (one seed, everything reachable) and from
    get_subgraph (a region, not a route). Returns up to 10 shortest paths, each
    a sequence of entities and relationship types.

    Args:
        source: Starting entity name.
        target: Destination entity name.
        max_hops: Maximum path length to consider (clamped to 1-10, default 5).
    """
    try:
        state = _require_state()

        source = _require_text(source, "source")
        target = _require_text(target, "target")
        max_hops = _clamp_limit(max_hops, maximum=MAX_HOPS_LIMIT)

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


@tool()
async def read_graph() -> dict[str, Any]:
    """Get the graph's shape in one cheap call — counts, type distributions, and
    the most connected and most recently touched entities.

    Start here to orient yourself in an unfamiliar graph. For maintenance
    signals (hotspots, missing descriptions, suggested cleanups) use
    graph_health; for a per-entity quality report naming every problem entity
    use audit_graph, which is the expensive one.

    Takes no arguments. Type distributions are capped at top 10,
    most_connected at top 5, and recent_entities at 5 to keep output compact.
    """
    try:
        state = _require_state()

        result: dict[str, Any] = dict(await state.graph.get_stats())

        # Cap distributions to reduce token output
        if "entity_types" in result and isinstance(result["entity_types"], dict):
            sorted_et = sorted(result["entity_types"].items(), key=lambda x: x[1], reverse=True)
            result["entity_types"] = dict(sorted_et[:10])
        if "relationship_types" in result and isinstance(result["relationship_types"], dict):
            sorted_rt = sorted(
                result["relationship_types"].items(), key=lambda x: x[1], reverse=True
            )
            result["relationship_types"] = dict(sorted_rt[:10])
        if "most_connected" in result and isinstance(result["most_connected"], list):
            result["most_connected"] = result["most_connected"][:5]
        if "recent_entities" in result and isinstance(result["recent_entities"], list):
            result["recent_entities"] = result["recent_entities"][:5]

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="read_graph")
