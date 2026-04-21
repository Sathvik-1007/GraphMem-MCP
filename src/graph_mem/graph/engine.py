"""Graph engine — CRUD operations for entities, relationships, and observations.

The GraphEngine is the primary interface for mutating and querying the
knowledge graph. All writes are transactional, batch operations are
optimized, and entity resolution uses a cascade strategy:
exact match -> case-insensitive match -> FTS5 suggestions.

The engine is **storage-agnostic**: it delegates all persistence to a
:class:`StorageBackend` instance, which can be SQLite, Neo4j, Memgraph,
or any other registered backend.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from graph_mem.models.entity import Entity
from graph_mem.models.observation import Observation
from graph_mem.models.relationship import Relationship
from graph_mem.utils.errors import EntityNotFoundError
from graph_mem.utils.ids import generate_id
from graph_mem.utils.logging import get_logger

if TYPE_CHECKING:
    from graph_mem.storage.base import StorageBackend

log = get_logger("graph.engine")


class EntityResult(TypedDict):
    """Return type for individual results from :meth:`GraphEngine.add_entities`."""

    id: str
    name: str
    status: Literal["created", "merged"]


class RelationshipResult(TypedDict):
    """Return type for individual results from :meth:`GraphEngine.add_relationships`."""

    id: str
    status: Literal["created", "updated"]


class ObservationResult(TypedDict):
    """Return type for individual results from :meth:`GraphEngine.add_observations`."""

    id: str
    entity_id: str
    content: str


class ConnectedEntry(TypedDict):
    """Shape of entries in ``StatsResult.most_connected``."""

    id: str
    name: str
    entity_type: str
    degree: int


class StatsResult(TypedDict):
    """Return type for :meth:`GraphEngine.get_stats`."""

    entities: int
    relationships: int
    observations: int
    entity_types: dict[str, int]
    relationship_types: dict[str, int]
    most_connected: list[ConnectedEntry]
    recent_entities: list[dict[str, object]]


class GraphEngine:
    """Core CRUD engine for the knowledge graph.

    All public methods are async. Write operations are wrapped in
    transactions. Batch methods process items in a single transaction
    for performance.

    The engine accepts a :class:`StorageBackend` and delegates all
    persistence and query operations to it. This makes the engine
    compatible with any backend (SQLite, Neo4j, Memgraph, etc.).
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    # ── Entity CRUD ──────────────────────────────────────────────────────

    async def add_entities(self, entities: list[Entity]) -> list[EntityResult]:
        """Insert or merge a batch of entities.

        On conflict (same name + entity_type), descriptions are appended
        and updated_at is set to the current time. Properties are merged
        with new values overwriting old keys.

        Returns:
            List of ``{id, name, status}`` where status is
            ``"created"`` or ``"merged"``.
        """
        if not entities:
            return []

        results: list[EntityResult] = []
        now = time.time()

        async with self._storage.transaction():
            for entity in entities:
                # Check for existing entity to determine the returned ID
                existing = await self._storage.get_entity_by_name(entity.name, entity.entity_type)

                status = await self._storage.upsert_entity(
                    entity_id=entity.id,
                    name=entity.name,
                    entity_type=entity.entity_type,
                    description=entity.description,
                    properties=entity.properties,
                    created_at=entity.created_at,
                    updated_at=now if existing else entity.updated_at,
                )

                result_id = str(existing["id"]) if existing else entity.id
                results.append(EntityResult(id=result_id, name=entity.name, status=status))
                log.debug(
                    "%s entity %r (type=%s)",
                    "Merged" if status == "merged" else "Created",
                    entity.name,
                    entity.entity_type,
                )

        log.info(
            "add_entities: %d processed (%d created, %d merged)",
            len(results),
            sum(1 for r in results if r["status"] == "created"),
            sum(1 for r in results if r["status"] == "merged"),
        )
        return results

    async def get_entity(self, name: str, entity_type: str | None = None) -> Entity:
        """Retrieve an entity by name, using the resolution cascade.

        Args:
            name: Entity name to look up.
            entity_type: Optional type constraint for exact matching.

        Returns:
            The resolved Entity.

        Raises:
            EntityNotFoundError: If no entity matches.
        """
        return await self.resolve_entity(name, entity_type)

    async def get_entity_by_id(self, entity_id: str) -> Entity:
        """Retrieve an entity by its primary key.

        Raises:
            EntityNotFoundError: If no entity has this ID.
        """
        row = await self._storage.get_entity_by_id(entity_id)
        if row is None:
            raise EntityNotFoundError(entity_id)
        return Entity.from_row(row)

    async def update_entity(
        self,
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
        properties: dict[str, object] | None = None,
        entity_type: str | None = None,
    ) -> Entity:
        """Update fields on an existing entity.

        The entity is resolved by name first. Only non-None arguments
        are applied. Properties are merged (not replaced).

        Args:
            name: Current entity name (used for resolution).
            new_name: Optional new name for the entity.
            description: Optional new description.
            properties: Optional properties to merge.
            entity_type: Optional new entity type.

        Returns:
            The updated Entity.
        """
        entity = await self.resolve_entity(name)
        now = time.time()

        updates: dict[str, Any] = {}

        if new_name is not None:
            stripped = new_name.strip()
            if stripped and stripped != entity.name:
                # Check that no other entity already uses this name
                existing = await self._storage.get_entity_by_name(stripped)
                if existing is not None and str(existing["id"]) != entity.id:
                    raise ValueError(
                        f"Cannot rename to {stripped!r}: another entity already has that name"
                    )
                updates["name"] = stripped
        if description is not None:
            updates["description"] = description
        if properties is not None:
            merged = {**entity.properties, **properties}
            updates["properties"] = json.dumps(merged, ensure_ascii=False, default=str)
        if entity_type is not None:
            updates["entity_type"] = entity_type.strip().lower()

        if not updates:
            return entity

        updates["updated_at"] = now

        async with self._storage.transaction():
            await self._storage.update_entity_fields(entity.id, updates)

        return await self.get_entity_by_id(entity.id)

    async def delete_entities(self, names: list[str]) -> int:
        """Delete entities by name, cascading to observations and relationships.

        Returns:
            Count of entities deleted.
        """
        if not names:
            return 0

        deleted = 0
        async with self._storage.transaction():
            for name in names:
                # Resolve to get the ID — skip silently if not found
                row = await self._storage.get_entity_by_name(name)
                if row is None:
                    row = await self._storage.get_entity_by_name_nocase(name)
                if row is None:
                    log.debug("Entity %r not found for deletion, skipping", name)
                    continue

                entity_id = str(row["id"])
                await self._storage.delete_entity(entity_id)
                deleted += 1

        log.info("delete_entities: %d deleted out of %d requested", deleted, len(names))
        return deleted

    async def list_entities(
        self,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Entity]:
        """List entities with optional type filter and pagination."""
        rows = await self._storage.list_entities(entity_type, limit, offset)
        return [Entity.from_row(row) for row in rows]

    # ── Relationship CRUD ────────────────────────────────────────────────

    async def add_relationships(
        self, relationships: list[Relationship]
    ) -> list[RelationshipResult]:
        """Insert or update a batch of relationships.

        On conflict (same source, target, type), the weight is updated
        to the maximum of old and new, and properties are merged.

        Returns:
            List of ``{id, status}`` where status is ``"created"`` or ``"updated"``.
        """
        if not relationships:
            return []

        results: list[RelationshipResult] = []
        now = time.time()

        async with self._storage.transaction():
            for rel in relationships:
                # Check for existing to determine the returned ID
                existing = await self._storage.get_relationship(
                    rel.source_id, rel.target_id, rel.relationship_type
                )

                status = await self._storage.upsert_relationship(
                    rel_id=rel.id,
                    source_id=rel.source_id,
                    target_id=rel.target_id,
                    relationship_type=rel.relationship_type,
                    weight=rel.weight,
                    properties=rel.properties,
                    created_at=rel.created_at,
                    updated_at=now if existing else rel.updated_at,
                )

                result_id = str(existing["id"]) if existing else rel.id
                results.append(RelationshipResult(id=result_id, status=status))

        log.info(
            "add_relationships: %d processed (%d created, %d updated)",
            len(results),
            sum(1 for r in results if r["status"] == "created"),
            sum(1 for r in results if r["status"] == "updated"),
        )
        return results

    async def get_relationships(
        self,
        entity_name: str,
        direction: str = "both",
        relationship_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get relationships for an entity, with resolved entity names.

        Args:
            entity_name: Name of the entity to query.
            direction: ``"outgoing"``, ``"incoming"``, or ``"both"``.
            relationship_type: Optional filter on relationship type.

        Returns:
            List of relationship dicts with ``source_name`` and
            ``target_name`` fields attached.
        """
        entity = await self.resolve_entity(entity_name)
        rows = await self._storage.get_relationships_for_entity(
            entity.id, direction, relationship_type
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            rel = Relationship.from_row(row)
            d = rel.to_dict()
            d["source_name"] = str(row["source_name"])
            d["source_type"] = str(row["source_type"])
            d["target_name"] = str(row["target_name"])
            d["target_type"] = str(row["target_type"])
            results.append(d)

        return results

    async def delete_relationships(
        self,
        source: str,
        target: str,
        relationship_type: str | None = None,
    ) -> int:
        """Delete relationships between two named entities.

        Returns:
            Count of relationships deleted.
        """
        source_entity = await self.resolve_entity(source)
        target_entity = await self.resolve_entity(target)

        async with self._storage.transaction():
            count = await self._storage.delete_relationships(
                source_entity.id, target_entity.id, relationship_type
            )

        log.info(
            "delete_relationships: %d deleted (%s -> %s, type=%s)",
            count,
            source,
            target,
            relationship_type,
        )
        return count

    # ── Observation CRUD ─────────────────────────────────────────────────

    async def add_observations(
        self, entity_name: str, observations: list[Observation]
    ) -> list[ObservationResult]:
        """Attach observations to an entity (resolved by name).

        Each observation's ``entity_id`` is overwritten with the
        resolved entity's ID before insertion.

        Returns:
            List of ``{id, entity_id, content}`` for each created observation.
        """
        if not observations:
            return []

        entity = await self.resolve_entity(entity_name)
        results: list[ObservationResult] = []

        async with self._storage.transaction():
            for obs in observations:
                obs_id = obs.id if obs.id else generate_id()
                now = time.time()
                await self._storage.insert_observation(
                    obs_id=obs_id,
                    entity_id=entity.id,
                    content=obs.content,
                    source=obs.source,
                    created_at=now,
                )
                results.append(
                    ObservationResult(
                        id=obs_id,
                        entity_id=entity.id,
                        content=obs.content,
                    )
                )

        log.info(
            "add_observations: %d added to entity %r",
            len(results),
            entity_name,
        )
        return results

    async def get_observations(self, entity_name: str) -> list[Observation]:
        """Get all observations for a named entity, newest first."""
        entity = await self.resolve_entity(entity_name)
        rows = await self._storage.get_observations_for_entity(entity.id)
        return [Observation.from_row(row) for row in rows]

    async def delete_observations(self, entity_name: str, observation_ids: list[str]) -> int:
        """Delete specific observations from an entity.

        Validates that the observations belong to the entity before deleting.
        Also cleans up observation embeddings in the storage layer.

        Returns:
            Count of observations deleted.
        """
        if not observation_ids:
            return 0

        entity = await self.resolve_entity(entity_name)
        # Get current observations to validate ownership
        existing = await self._storage.get_observations_for_entity(entity.id)
        existing_ids = {str(r["id"]) for r in existing}

        deleted = 0
        async with self._storage.transaction():
            for obs_id in observation_ids:
                if obs_id not in existing_ids:
                    log.debug(
                        "Observation %s does not belong to entity %r, skipping",
                        obs_id,
                        entity_name,
                    )
                    continue
                if await self._storage.delete_observation(obs_id):
                    # Clean up embedding — vec table may not exist
                    with contextlib.suppress(Exception):
                        await self._storage.delete_observation_embedding(obs_id)
                    deleted += 1

        log.info(
            "delete_observations: %d deleted from entity %r",
            deleted,
            entity_name,
        )
        return deleted

    async def update_observation(
        self,
        entity_name: str,
        observation_id: str,
        content: str,
    ) -> dict[str, Any]:
        """Update the text content of an observation in-place.

        Validates that the observation belongs to the entity.

        Returns:
            Dict with id, entity_name, old_content, new_content.
        """
        entity = await self.resolve_entity(entity_name)
        existing = await self._storage.get_observations_for_entity(entity.id)
        existing_map = {str(r["id"]): r for r in existing}

        if observation_id not in existing_map:
            raise EntityNotFoundError(
                f"Observation {observation_id} not found on entity {entity_name!r}"
            )

        old_content = str(existing_map[observation_id]["content"])

        async with self._storage.transaction():
            updated = await self._storage.update_observation(observation_id, content)

        if not updated:
            raise EntityNotFoundError(f"Observation {observation_id} could not be updated")

        log.info(
            "update_observation: %s on entity %r updated",
            observation_id,
            entity_name,
        )
        return {
            "id": observation_id,
            "entity_name": entity_name,
            "old_content": old_content,
            "new_content": content,
        }

    # ── Relationship updates ─────────────────────────────────────────────

    async def update_relationship(
        self,
        source: str,
        target: str,
        relationship_type: str,
        *,
        new_weight: float | None = None,
        new_type: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Update a relationship between two named entities in-place.

        Returns:
            Dict with the updated relationship details.
        """
        source_entity = await self.resolve_entity(source)
        target_entity = await self.resolve_entity(target)

        existing = await self._storage.get_relationship(
            source_entity.id, target_entity.id, relationship_type
        )
        if existing is None:
            raise EntityNotFoundError(
                f"No relationship of type {relationship_type!r} between {source!r} and {target!r}"
            )

        updates: dict[str, Any] = {"updated_at": time.time()}
        if new_weight is not None:
            updates["weight"] = new_weight
        if new_type is not None:
            updates["relationship_type"] = new_type
        if properties is not None:
            old_props = json.loads(str(existing.get("properties", "{}")))
            merged = {**old_props, **properties}
            updates["properties"] = json.dumps(merged, ensure_ascii=False, default=str)

        rel_id = str(existing["id"])
        async with self._storage.transaction():
            await self._storage.update_relationship(rel_id, updates)

        log.info(
            "update_relationship: %s -> %s (type=%s) updated",
            source,
            target,
            relationship_type,
        )
        return {
            "id": rel_id,
            "source": source,
            "target": target,
            "relationship_type": new_type or relationship_type,
            "status": "updated",
        }

    # ── Entity resolution ────────────────────────────────────────────────

    async def resolve_entity(self, name: str, entity_type: str | None = None) -> Entity:
        """Resolve an entity name to an Entity using a cascade strategy.

        Resolution order:
        1. Exact match on (name, entity_type) if type is given.
        2. Exact match on name alone.
        3. Case-insensitive match on name.
        4. If no match, query FTS5 for suggestions and raise
           :class:`EntityNotFoundError` with those suggestions.
        """
        name = name.strip()

        # Step 1: exact match with type constraint
        if entity_type is not None:
            row = await self._storage.get_entity_by_name(name, entity_type.strip().lower())
            if row is not None:
                return Entity.from_row(row)

        # Step 2: exact match on name only
        row = await self._storage.get_entity_by_name(name)
        if row is not None:
            return Entity.from_row(row)

        # Step 3: case-insensitive match
        row = await self._storage.get_entity_by_name_nocase(name)
        if row is not None:
            return Entity.from_row(row)

        # Step 4: not found — gather suggestions and raise
        suggestions = await self._storage.fts_suggest_similar(name)
        raise EntityNotFoundError(name, suggestions=suggestions)

    # ── Stats ────────────────────────────────────────────────────────────

    async def get_stats(self) -> StatsResult:
        """Compute summary statistics for the knowledge graph.

        Returns a :class:`StatsResult` with:
        - ``entities``: Total entity count.
        - ``relationships``: Total relationship count.
        - ``observations``: Total observation count.
        - ``entity_types``: ``{type: count}`` distribution.
        - ``relationship_types``: ``{type: count}`` distribution.
        - ``most_connected``: Top 10 entities by degree (in + out).
        - ``recent_entities``: 10 most recently updated entities.
        """
        entity_count = await self._storage.count_entities()
        rel_count = await self._storage.count_relationships()
        obs_count = await self._storage.count_observations()
        entity_types = await self._storage.entity_type_distribution()
        relationship_types = await self._storage.relationship_type_distribution()

        connected_rows = await self._storage.most_connected_entities(10)
        most_connected = [
            ConnectedEntry(
                id=str(r["id"]),
                name=str(r["name"]),
                entity_type=str(r["entity_type"]),
                degree=int(r["degree"]),
            )
            for r in connected_rows
        ]

        recent_rows = await self._storage.recent_entities(10)
        recent_entities = [Entity.from_row(r).to_dict() for r in recent_rows]

        return StatsResult(
            entities=entity_count,
            relationships=rel_count,
            observations=obs_count,
            entity_types=entity_types,
            relationship_types=relationship_types,
            most_connected=most_connected,
            recent_entities=recent_entities,
        )
