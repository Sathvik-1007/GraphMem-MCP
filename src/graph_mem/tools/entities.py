"""Entity CRUD tools — add, update, delete, merge, get, list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from graph_mem.graph.engine import ObservationResult

from graph_mem.models import Entity, Observation
from graph_mem.utils import GraphMemError
from graph_mem.utils.errors import EntityNotFoundError

from ._core import (
    MAX_LIST_LIMIT,
    MAX_NESTED_ITEMS,
    MAX_OFFSET,
    _clamp_limit,
    _embed_entities,
    _embed_observations,
    _error_response,
    _require_state,
    _require_text,
    _require_text_list,
    _validate_items,
    log,
    tool,
)


class EntityInput(BaseModel):
    """One entity for :func:`add_entities`.

    Declared as a model rather than a bare ``dict`` so the tool's JSON schema
    names the keys and their types.  Without it the client sees only
    ``{"type": "object"}`` and the calling model has to guess.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Entity name, e.g. 'Ada Lovelace'.")
    entity_type: str = Field(description="Category, e.g. 'person', 'concept', 'place'.")
    description: str = Field(default="", description="Prose description; improves search.")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary key-value metadata."
    )
    observations: list[str] = Field(
        default_factory=list, description="Atomic facts to attach to this entity."
    )


@tool()
async def add_entities(entities: list[EntityInput]) -> dict[str, Any]:
    """Add entities to the knowledge graph. Entities with the same name and type are
    automatically merged.

    Each entity needs: name (str), entity_type (str, e.g. 'person', 'concept', 'place').
    Optional: description (str), properties (dict), observations (list[str]).
    """
    # Error handling is intentional at the MCP boundary — wraps domain errors
    # and input validation errors into structured error dicts for the client.
    try:
        state = _require_state()

        # Validate and coerce before constructing any domain object: a null or
        # wrong-typed field must produce a structured error naming the field,
        # not an AttributeError from inside Entity.__post_init__.
        items = _validate_items(entities, EntityInput, field="entities")

        # Build Entity objects
        entity_objs: list[Entity] = []
        obs_by_index: list[tuple[int, list[str]]] = []  # (index, obs_texts)
        for idx, item in enumerate(items):
            entity = Entity(
                name=item.name,
                entity_type=item.entity_type,
                description=item.description,
                properties=dict(item.properties),
            )
            entity_objs.append(entity)

            if item.observations:
                obs_by_index.append((idx, item.observations))

        # Persist entities (single transaction inside engine)
        results = await state.graph.add_entities(entity_objs)

        # Compute entity embeddings — single batch embed call
        entity_ids = [str(r["id"]) for r in results]
        await _embed_entities(entity_ids)

        # Batch all observations: collect, insert per-entity, then embed ALL at once
        all_obs_results: list[ObservationResult] = []
        for idx, obs_texts in obs_by_index:
            entity_name = str(results[idx]["name"])
            obs_objs = [Observation.pending(text) for text in obs_texts]
            obs_results = await state.graph.add_observations(entity_name, obs_objs)
            all_obs_results.extend(obs_results)

        # Single batch embed for ALL observations across all entities
        if all_obs_results:
            await _embed_observations(all_obs_results)

        # Auto-screening: lightweight quality check on newly added entities.
        # Returns hints so the LLM can improve quality without extra tool calls.
        screening: dict[str, list[str]] = {}
        no_description: list[str] = []
        no_observations: list[str] = []
        for idx, item in enumerate(items):
            ename = str(results[idx]["name"])
            if not item.description.strip():
                no_description.append(ename)
            if not item.observations:
                no_observations.append(ename)
        if no_description:
            screening["missing_description"] = no_description
        if no_observations:
            screening["missing_observations"] = no_observations

        response: dict[str, Any] = {"results": results, "count": len(results)}
        if screening:
            screening["hint"] = [
                "Add descriptions and observations to improve search quality. "
                "Use update_entity for descriptions, add_observations for facts."
            ]
            response["screening"] = screening
        return response

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_entities")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="add_entities")


@tool()
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
        name = _require_text(name, "name")

        updated = await state.graph.update_entity(
            name,
            description=description,
            properties=properties,
            entity_type=entity_type,
        )

        # Recompute embedding if description or entity_type changed
        # (Entity.embedding_text includes name + entity_type + description)
        if description is not None or entity_type is not None:
            await _embed_entities([updated.id])

        return {"result": updated.to_dict(), "status": "updated"}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_entity")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="update_entity")


@tool()
async def delete_entities(names: list[str]) -> dict[str, Any]:
    """Remove entities from the knowledge graph by name.

    Cascades to delete all related observations and relationships.
    Returns the count of entities actually deleted.
    """
    try:
        state = _require_state()
        names = _require_text_list(names, "names")

        # Resolve entity IDs AND observation IDs before deletion for embedding cleanup.
        # Vec tables don't support CASCADE, so observation embeddings must be
        # cleaned up manually when the parent entity is deleted.
        entity_ids: list[str] = []
        obs_ids_to_clean: list[str] = []
        for name in names:
            try:
                entity = await state.graph.resolve_entity(name)
                entity_ids.append(entity.id)
                # Collect observation IDs — their rows will be cascade-deleted
                # from the main table, but vec table rows won't.
                obs_rows = await state.storage.get_observations_for_entity(entity.id)
                obs_ids_to_clean.extend(str(o["id"]) for o in obs_rows)
            except EntityNotFoundError:
                log.debug("Entity %r not found during deletion, skipping", name)
                continue

        deleted = await state.graph.delete_entities(names)

        # Clean up embeddings for deleted entities AND their observations
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
            for oid in obs_ids_to_clean:
                try:
                    await state.embeddings.delete_observation_embedding(oid)
                except GraphMemError:
                    log.debug("Failed to clean up observation embedding %s", oid)

        return {"results": names, "deleted": deleted, "count": deleted}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_entities")


@tool()
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
        target = _require_text(target, "target")
        source = _require_text(source, "source")

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


@tool()
async def get_entity(name: str) -> dict[str, Any]:
    """Get full details of a single entity by name, including observations and relationships.

    Uses fuzzy name resolution: exact match -> case-insensitive -> FTS5 suggestions.
    At most 50 observations (newest first) and 50 relationships are attached;
    ``observations_truncated`` / ``relationships_truncated`` say when more exist,
    and ``observation_count`` / ``relationship_count`` give the true totals.
    Use search_observations scoped by entity_name to reach the rest.
    """
    try:
        state = _require_state()
        name = _require_text(name, "name")

        entity = await state.graph.get_entity(name)
        # A hot entity can carry thousands of observations, and every one would
        # land in the caller's context window. The cap is applied in SQL rather
        # than by slicing afterwards, so the rows are never read either; the
        # true total comes from a separate COUNT so the caller still learns how
        # much it is not seeing.
        observations = await state.graph.get_observations(name, limit=MAX_NESTED_ITEMS)
        observation_count = await state.graph.count_observations(name)
        relationships = await state.graph.get_relationships(name)

        result = entity.to_dict()
        result["observations"] = [obs.to_dict() for obs in observations]
        result["observation_count"] = observation_count
        result["observations_truncated"] = observation_count > len(observations)
        result["relationships"] = relationships[:MAX_NESTED_ITEMS]
        result["relationship_count"] = len(relationships)
        result["relationships_truncated"] = len(relationships) > MAX_NESTED_ITEMS

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="get_entity")


@tool()
async def list_entities(
    entity_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List all entities in the knowledge graph with optional filtering.

    Browse entities with pagination support. Useful for discovering what's
    in the graph without a specific search query, or for iterating over
    entities of a specific type.

    Args:
        entity_type: Optional — filter to only this entity type (e.g. 'person').
        limit: Maximum entities to return (default 50, clamped to 1-500).
        offset: Skip this many entities for pagination (default 0, max 1000000).

    Returns:
        Matching entities with their summaries and total count.
    """
    try:
        state = _require_state()

        limit = _clamp_limit(limit, maximum=MAX_LIST_LIMIT)
        offset = _clamp_limit(offset, maximum=MAX_OFFSET, minimum=0)
        if entity_type is not None:
            entity_type = _require_text(entity_type, "entity_type")

        entities = await state.graph.list_entities(
            entity_type=entity_type,
            limit=limit,
            offset=offset,
        )

        results = [e.to_dict() for e in entities]

        # Total must respect the same entity_type filter for correct pagination
        if entity_type:
            count_row = await state.storage.fetch_one(
                "SELECT COUNT(*) AS cnt FROM entities WHERE entity_type = ?",
                (entity_type.strip().lower(),),
            )
            total = int(count_row["cnt"]) if count_row else 0
        else:
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
