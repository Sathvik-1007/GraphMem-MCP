"""Relationship tools — add, update, delete, list."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_mem.models import Relationship
from graph_mem.utils import GraphMemError

from ._core import (
    MAX_LIST_LIMIT,
    MAX_OFFSET,
    _clamp_limit,
    _error_response,
    _require_state,
    _require_text,
    _validate_items,
    tool,
)


class RelationshipInput(BaseModel):
    """One relationship for :func:`add_relationships`.

    Declared as a model rather than a bare ``dict`` so the tool's JSON schema
    names the keys and their types.  Without it the client sees only
    ``{"type": "object"}`` and the calling model has to guess.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Name of the entity the edge starts at.")
    target: str = Field(description="Name of the entity the edge points to.")
    relationship_type: str = Field(description="Edge type, e.g. 'knows', 'depends_on'.")
    weight: float = Field(default=1.0, description="Edge strength, 0-1. Default 1.0.")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary key-value metadata for the edge."
    )


@tool()
async def add_relationships(relationships: list[RelationshipInput]) -> dict[str, Any]:
    """Add relationships (edges) between entities in the knowledge graph.

    Each relationship needs: source (str, entity name), target (str, entity name),
    relationship_type (str, e.g. 'knows', 'works_at', 'depends_on').
    Optional: weight (float, 0-1, default 1.0), properties (dict).
    Duplicate edges (same source, target, type) are merged with the higher weight kept.
    """
    try:
        state = _require_state()

        # Validate and coerce before constructing any domain object, so a null
        # or wrong-typed field names itself in the error instead of blowing up
        # as an AttributeError deeper down.
        items = _validate_items(relationships, RelationshipInput, field="relationships")

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
        for item in items:
            source_entity = await _cached_resolve(item.source)
            target_entity = await _cached_resolve(item.target)

            rel = Relationship(
                source_id=source_entity.id,
                target_id=target_entity.id,
                relationship_type=item.relationship_type,
                weight=item.weight,
                properties=dict(item.properties),
            )
            rel_objs.append(rel)

        results = await state.graph.add_relationships(rel_objs)

        # Enrich results with names for clarity
        enriched: list[dict[str, Any]] = []
        for item, result in zip(items, results, strict=True):
            enriched.append(
                {
                    **result,
                    "source": item.source,
                    "target": item.target,
                    "relationship_type": item.relationship_type,
                }
            )

        return {"results": enriched, "count": len(enriched)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_relationships")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(
            GraphMemError(f"Invalid input: {exc}"), tool_name="add_relationships"
        )


@tool()
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
        source = _require_text(source, "source")
        target = _require_text(target, "target")
        if relationship_type is not None:
            relationship_type = _require_text(relationship_type, "relationship_type")

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


@tool()
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
        source = _require_text(source, "source")
        target = _require_text(target, "target")
        relationship_type = _require_text(relationship_type, "relationship_type")
        if new_type is not None:
            new_type = _require_text(new_type, "new_type")

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


@tool()
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
        limit: Maximum relationships to return (default 100, clamped to 1-500).
        offset: Skip this many relationships for pagination (default 0, max 1000000).
    """
    try:
        state = _require_state()
        limit = _clamp_limit(limit, maximum=MAX_LIST_LIMIT)
        offset = _clamp_limit(offset, maximum=MAX_OFFSET, minimum=0)
        if entity_name is not None:
            entity_name = _require_text(entity_name, "entity_name")
        if relationship_type is not None:
            relationship_type = _require_text(relationship_type, "relationship_type")

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
