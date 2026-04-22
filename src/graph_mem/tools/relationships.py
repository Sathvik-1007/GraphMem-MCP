"""Relationship tools — add, update, delete, list."""

from __future__ import annotations

from typing import Any

from graph_mem.models import Relationship
from graph_mem.utils import GraphMemError

from ._core import _error_response, _require_state, mcp


@mcp.tool()
async def add_relationships(relationships: list[dict[str, Any]]) -> dict[str, Any]:
    """Add relationships (edges) between entities in the knowledge graph.

    Each relationship needs: source (str, entity name), target (str, entity name),
    relationship_type (str, e.g. 'knows', 'works_at', 'depends_on').
    Optional: weight (float, 0-1, default 1.0), properties (dict).
    Duplicate edges (same source, target, type) are merged with the higher weight kept.
    """
    try:
        state = _require_state()

        # Cache resolved entities — each unique name resolved only once.
        # For 100 rels referencing 20 unique names, this is 20 lookups not 200.
        # Keys are stripped to match resolve_entity's name.strip() behavior.
        resolved_cache: dict[str, Any] = {}

        async def _cached_resolve(name: str) -> Any:
            key = name.strip()
            if key not in resolved_cache:
                resolved_cache[key] = await state.graph.resolve_entity(name)
            return resolved_cache[key]

        rel_objs: list[Relationship] = []
        for raw in relationships:
            source_name: str = raw["source"]
            target_name: str = raw["target"]
            rel_type: str = raw["relationship_type"]
            weight: float = float(raw.get("weight", 1.0))
            properties: dict[str, object] = raw.get("properties", {})

            source_entity = await _cached_resolve(source_name)
            target_entity = await _cached_resolve(target_name)

            rel = Relationship(
                source_id=source_entity.id,
                target_id=target_entity.id,
                relationship_type=rel_type,
                weight=weight,
                properties=properties,
            )
            rel_objs.append(rel)

        results = await state.graph.add_relationships(rel_objs)

        # Enrich results with names for clarity
        enriched: list[dict[str, Any]] = []
        for raw, result in zip(relationships, results, strict=True):
            enriched.append(
                {
                    **result,
                    "source": raw["source"],
                    "target": raw["target"],
                    "relationship_type": raw["relationship_type"],
                }
            )

        return {"results": enriched, "count": len(enriched)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_relationships")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(
            GraphMemError(f"Invalid input: {exc}"), tool_name="add_relationships"
        )


@mcp.tool()
async def delete_relationships(
    source: str,
    target: str,
    relationship_type: str | None = None,
) -> dict[str, Any]:
    """Remove relationships between two entities by name.

    Deletes matching edges from source to target. If relationship_type is
    provided, only relationships of that type are deleted. Otherwise all
    relationships between the pair are removed.

    Args:
        source: Source entity name.
        target: Target entity name.
        relationship_type: Optional — restrict deletion to this edge type.
    """
    try:
        state = _require_state()

        deleted = await state.graph.delete_relationships(source, target, relationship_type)

        return {
            "source": source,
            "target": target,
            "relationship_type": relationship_type,
            "deleted": deleted,
            "status": "deleted" if deleted > 0 else "not_found",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_relationships")


@mcp.tool()
async def update_relationship(
    source: str,
    target: str,
    relationship_type: str,
    new_weight: float | None = None,
    new_type: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update an existing relationship between two entities in-place.

    Modifies weight, type, or properties of an existing edge without
    deleting and re-creating it. Properties are merged (new keys added,
    existing keys overwritten).

    Args:
        source: Source entity name.
        target: Target entity name.
        relationship_type: Current relationship type to identify the edge.
        new_weight: Optional new weight value (0.0-1.0).
        new_type: Optional new relationship type string.
        properties: Optional properties dict to merge into existing properties.
    """
    try:
        state = _require_state()

        result = await state.graph.update_relationship(
            source,
            target,
            relationship_type,
            new_weight=new_weight,
            new_type=new_type,
            properties=properties,
        )

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_relationship")


@mcp.tool()
async def list_relationships(
    entity_name: str | None = None,
    relationship_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List relationships in the knowledge graph with optional filtering.

    Browse relationships with pagination. Filter by entity name or
    relationship type. Returns source/target names and metadata.

    Args:
        entity_name: Optional — show only relationships involving this entity.
        relationship_type: Optional — filter to this relationship type.
        limit: Maximum relationships to return (default 100, max 500).
        offset: Skip this many relationships for pagination (default 0).
    """
    try:
        state = _require_state()
        limit = min(max(1, limit), 500)
        offset = max(0, offset)

        if entity_name:
            # Scoped to a specific entity
            rels = await state.graph.get_relationships(
                entity_name,
                relationship_type=relationship_type,
            )
            total = len(rels)
            rels = rels[offset : offset + limit]
        else:
            # Global listing via raw SQL
            params: list[object] = []
            where_clause = ""
            if relationship_type:
                where_clause = "WHERE r.relationship_type = ?"
                params.append(relationship_type.strip().lower())

            count_row = await state.storage.fetch_one(
                f"SELECT COUNT(*) AS cnt FROM relationships r {where_clause}",
                tuple(params),
            )
            total = int(count_row["cnt"]) if count_row else 0

            rows = await state.storage.fetch_all(
                f"""
                SELECT r.*,
                       s.name AS source_name, s.entity_type AS source_type,
                       t.name AS target_name, t.entity_type AS target_type
                FROM relationships r
                JOIN entities s ON s.id = r.source_id
                JOIN entities t ON t.id = r.target_id
                {where_clause}
                ORDER BY r.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (*tuple(params), limit, offset),
            )
            rels = [
                {
                    "id": str(r["id"]),
                    "source_name": str(r["source_name"]),
                    "source_type": str(r["source_type"]),
                    "target_name": str(r["target_name"]),
                    "target_type": str(r["target_type"]),
                    "relationship_type": str(r["relationship_type"]),
                    "weight": float(r["weight"]),
                }
                for r in rows
            ]

        return {
            "results": rels,
            "count": len(rels),
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="list_relationships")
