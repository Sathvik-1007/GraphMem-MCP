"""Entity CRUD tools — add, update, delete, merge, get, list."""

from __future__ import annotations

from typing import Any

from graph_mem.models import Entity, Observation
from graph_mem.utils import GraphMemError
from graph_mem.utils.errors import EntityNotFoundError

from ._core import (
    _embed_entities,
    _embed_observations,
    _error_response,
    _require_state,
    log,
    mcp,
)


@mcp.tool()
async def add_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Add entities to the knowledge graph. Entities with the same name and type are
    automatically merged.

    Each entity needs: name (str), entity_type (str, e.g. 'person', 'concept', 'place').
    Optional: description (str), properties (dict), observations (list[str]).
    """
    # Error handling is intentional at the MCP boundary — wraps domain errors
    # and input validation errors into structured error dicts for the client.
    try:
        state = _require_state()

        # Build Entity objects
        entity_objs: list[Entity] = []
        obs_map: dict[str, list[str]] = {}  # name -> observation texts
        for raw in entities:
            name: str = raw["name"]
            entity_type: str = raw["entity_type"]
            description: str = raw.get("description", "")
            properties: dict[str, object] = raw.get("properties", {})
            observations: list[str] = raw.get("observations", [])

            entity = Entity(
                name=name,
                entity_type=entity_type,
                description=description,
                properties=properties,
            )
            entity_objs.append(entity)

            if observations:
                obs_map[name] = observations

        # Persist entities
        results = await state.graph.add_entities(entity_objs)

        # Compute entity embeddings
        entity_ids = [str(r["id"]) for r in results]
        await _embed_entities(entity_ids)

        # Add and embed observations for entities that had them
        for name, obs_texts in obs_map.items():
            obs_objs = [Observation.pending(text) for text in obs_texts]
            obs_results = await state.graph.add_observations(name, obs_objs)
            await _embed_observations(obs_results)

        return {"results": results, "count": len(results)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_entities")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="add_entities")


@mcp.tool()
async def update_entity(
    name: str,
    description: str | None = None,
    properties: dict[str, Any] | None = None,
    entity_type: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing entity. Only provided fields are changed.

    Properties are merged with existing ones (new keys added, existing keys updated).
    Pass a new description to replace the current one. Pass entity_type to reclassify.
    """
    try:
        state = _require_state()

        updated = await state.graph.update_entity(
            name,
            description=description,
            properties=properties,
            entity_type=entity_type,
        )

        # Recompute embedding if description changed
        if description is not None:
            await _embed_entities([updated.id])

        return {"result": updated.to_dict(), "status": "updated"}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_entity")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="update_entity")


@mcp.tool()
async def delete_entities(names: list[str]) -> dict[str, Any]:
    """Remove entities from the knowledge graph by name.

    Cascades to delete all related observations and relationships.
    Returns the count of entities actually deleted.
    """
    try:
        state = _require_state()

        # Resolve entity IDs before deletion for embedding cleanup
        entity_ids: list[str] = []
        for name in names:
            try:
                entity = await state.graph.resolve_entity(name)
                entity_ids.append(entity.id)
            except EntityNotFoundError:
                log.debug("Entity %r not found during deletion, skipping", name)
                continue

        deleted = await state.graph.delete_entities(names)

        # Clean up embeddings for deleted entities
        if state.embeddings.available:
            for eid in entity_ids:
                try:
                    await state.embeddings.delete_entity_embedding(eid)
                except GraphMemError as emb_exc:
                    log.debug(
                        "Failed to clean up embedding for entity %s: %s — "
                        "orphaned embedding row may remain",
                        eid,
                        emb_exc,
                    )

        return {"results": names, "deleted": deleted, "count": deleted}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_entities")


@mcp.tool()
async def merge_entities(
    target: str,
    source: str,
) -> dict[str, Any]:
    """Merge two entities into one. The source entity is absorbed into the target.

    All observations and relationships from the source are moved to the target.
    Duplicate relationships are deduplicated (higher weight kept). The source
    entity is deleted after the merge.
    """
    try:
        state = _require_state()

        target_entity = await state.graph.resolve_entity(target)
        source_entity = await state.graph.resolve_entity(source)

        result = await state.merger.merge(target_entity.id, source_entity.id)

        # Recompute target embedding (description may have changed)
        await _embed_entities([target_entity.id])

        # Clean up source embedding
        if state.embeddings.available:
            try:
                await state.embeddings.delete_entity_embedding(source_entity.id)
            except GraphMemError as emb_exc:
                log.debug(
                    "Failed to clean up embedding for merged entity %s: %s — "
                    "orphaned embedding row may remain",
                    source_entity.id,
                    emb_exc,
                )

        return {"result": result, "status": "merged"}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="merge_entities")


@mcp.tool()
async def get_entity(name: str) -> dict[str, Any]:
    """Get full details of a single entity by name, including observations and relationships.

    Uses fuzzy name resolution: exact match -> case-insensitive -> FTS5 suggestions.
    Returns the entity with all its observations and direct relationships.
    """
    try:
        state = _require_state()

        entity = await state.graph.get_entity(name)
        observations = await state.graph.get_observations(name)
        relationships = await state.graph.get_relationships(name)

        result = entity.to_dict()
        result["observations"] = [obs.to_dict() for obs in observations]
        result["relationships"] = relationships

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="get_entity")


@mcp.tool()
async def list_entities(
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List all entities in the knowledge graph with optional filtering.

    Browse entities with pagination support. Useful for discovering what's
    in the graph without a specific search query, or for iterating over
    entities of a specific type.

    Args:
        entity_type: Optional — filter to only this entity type (e.g. 'person').
        limit: Maximum entities to return (default 100, max 500).
        offset: Skip this many entities for pagination (default 0).
    """
    try:
        state = _require_state()

        # Clamp limit to prevent excessive queries
        limit = min(max(1, limit), 500)
        offset = max(0, offset)

        entities = await state.graph.list_entities(
            entity_type=entity_type,
            limit=limit,
            offset=offset,
        )

        results = [e.to_dict() for e in entities]
        total = await state.storage.count_entities()

        return {
            "results": results,
            "count": len(results),
            "total": total,
            "limit": limit,
            "offset": offset,
            "entity_type": entity_type,
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="list_entities")
