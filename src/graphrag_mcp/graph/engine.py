"""Graph engine — CRUD operations for entities, relationships, and observations.

The GraphEngine is the primary interface for mutating and querying the
knowledge graph. All writes are transactional, batch operations are
optimized, and entity resolution uses a cascade strategy:
exact match -> case-insensitive match -> FTS5 suggestions.
"""

from __future__ import annotations

import json
import time
from typing import Any

from graphrag_mcp.db.connection import Database
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.models.relationship import Relationship
from graphrag_mcp.utils.errors import (
    EntityNotFoundError,
    RelationshipError,
)
from graphrag_mcp.utils.ids import generate_id
from graphrag_mcp.utils.logging import get_logger

log = get_logger("graph.engine")


class GraphEngine:
    """Core CRUD engine for the knowledge graph.

    All public methods are async. Write operations are wrapped in
    transactions. Batch methods process items in a single transaction
    for performance.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Entity CRUD ──────────────────────────────────────────────────────

    async def add_entities(self, entities: list[Entity]) -> list[dict[str, Any]]:
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

        results: list[dict[str, Any]] = []
        now = time.time()

        async with self._db.transaction():
            for entity in entities:
                existing = await self._db.fetch_one(
                    "SELECT * FROM entities WHERE name = ? AND entity_type = ?",
                    (entity.name, entity.entity_type),
                )

                if existing is not None:
                    # Merge: append description, merge properties
                    old_desc = str(existing["description"] or "")
                    new_desc = entity.description.strip()
                    if new_desc and new_desc not in old_desc:
                        merged_desc = f"{old_desc}\n{new_desc}".strip()
                    else:
                        merged_desc = old_desc

                    old_props_raw = existing.get("properties")
                    old_props: dict[str, object] = (
                        json.loads(old_props_raw) if isinstance(old_props_raw, str) else {}
                    )
                    merged_props = {**old_props, **entity.properties}

                    await self._db.execute(
                        "UPDATE entities SET description = ?, properties = ?, updated_at = ? WHERE id = ?",
                        (
                            merged_desc,
                            json.dumps(merged_props, ensure_ascii=False, default=str),
                            now,
                            str(existing["id"]),
                        ),
                    )
                    results.append(
                        {"id": str(existing["id"]), "name": entity.name, "status": "merged"}
                    )
                    log.debug("Merged entity %r (type=%s)", entity.name, entity.entity_type)
                else:
                    # Create new
                    await self._db.execute(
                        "INSERT INTO entities (id, name, entity_type, description, properties, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            entity.id,
                            entity.name,
                            entity.entity_type,
                            entity.description,
                            entity.properties_json,
                            entity.created_at,
                            entity.updated_at,
                        ),
                    )
                    results.append({"id": entity.id, "name": entity.name, "status": "created"})
                    log.debug("Created entity %r (type=%s)", entity.name, entity.entity_type)

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
        row = await self._db.fetch_one("SELECT * FROM entities WHERE id = ?", (entity_id,))
        if row is None:
            raise EntityNotFoundError(entity_id)
        return Entity.from_row(row)

    async def update_entity(
        self,
        name: str,
        *,
        description: str | None = None,
        properties: dict[str, object] | None = None,
        entity_type: str | None = None,
    ) -> Entity:
        """Update fields on an existing entity.

        The entity is resolved by name first. Only non-None arguments
        are applied. Properties are merged (not replaced).

        Returns:
            The updated Entity.
        """
        entity = await self.resolve_entity(name)
        now = time.time()

        updates: list[str] = []
        params: list[object] = []

        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if properties is not None:
            # Merge with existing
            merged = {**entity.properties, **properties}
            updates.append("properties = ?")
            params.append(json.dumps(merged, ensure_ascii=False, default=str))
        if entity_type is not None:
            updates.append("entity_type = ?")
            params.append(entity_type.strip().lower())

        if not updates:
            return entity

        updates.append("updated_at = ?")
        params.append(now)
        params.append(entity.id)

        sql = f"UPDATE entities SET {', '.join(updates)} WHERE id = ?"  # noqa: S608

        async with self._db.transaction():
            await self._db.execute(sql, tuple(params))

        return await self.get_entity_by_id(entity.id)

    async def delete_entities(self, names: list[str]) -> int:
        """Delete entities by name, cascading to observations and relationships.

        Returns:
            Count of entities deleted.
        """
        if not names:
            return 0

        deleted = 0
        async with self._db.transaction():
            for name in names:
                # Resolve to get the ID — skip silently if not found
                row = await self._db.fetch_one("SELECT id FROM entities WHERE name = ?", (name,))
                if row is None:
                    row = await self._db.fetch_one(
                        "SELECT id FROM entities WHERE name = ? COLLATE NOCASE", (name,)
                    )
                if row is None:
                    continue

                entity_id = str(row["id"])

                # Delete observations
                await self._db.execute("DELETE FROM observations WHERE entity_id = ?", (entity_id,))
                # Delete relationships where this entity is source or target
                await self._db.execute(
                    "DELETE FROM relationships WHERE source_id = ? OR target_id = ?",
                    (entity_id, entity_id),
                )
                # Delete the entity itself
                await self._db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
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
        if entity_type is not None:
            rows = await self._db.fetch_all(
                "SELECT * FROM entities WHERE entity_type = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (entity_type.strip().lower(), limit, offset),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [Entity.from_row(row) for row in rows]

    # ── Relationship CRUD ────────────────────────────────────────────────

    async def add_relationships(self, relationships: list[Relationship]) -> list[dict[str, Any]]:
        """Insert or update a batch of relationships.

        On conflict (same source, target, type), the weight is updated
        to the maximum of old and new, and properties are merged.

        Returns:
            List of ``{id, status}`` where status is ``"created"`` or ``"updated"``.
        """
        if not relationships:
            return []

        results: list[dict[str, Any]] = []
        now = time.time()

        async with self._db.transaction():
            for rel in relationships:
                existing = await self._db.fetch_one(
                    "SELECT * FROM relationships "
                    "WHERE source_id = ? AND target_id = ? AND relationship_type = ?",
                    (rel.source_id, rel.target_id, rel.relationship_type),
                )

                if existing is not None:
                    new_weight = max(float(existing["weight"]), rel.weight)
                    old_props_raw = existing.get("properties")
                    old_props: dict[str, object] = (
                        json.loads(old_props_raw) if isinstance(old_props_raw, str) else {}
                    )
                    merged_props = {**old_props, **rel.properties}

                    await self._db.execute(
                        "UPDATE relationships SET weight = ?, properties = ?, updated_at = ? WHERE id = ?",
                        (
                            new_weight,
                            json.dumps(merged_props, ensure_ascii=False, default=str),
                            now,
                            str(existing["id"]),
                        ),
                    )
                    results.append({"id": str(existing["id"]), "status": "updated"})
                else:
                    await self._db.execute(
                        "INSERT INTO relationships "
                        "(id, source_id, target_id, relationship_type, weight, properties, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            rel.id,
                            rel.source_id,
                            rel.target_id,
                            rel.relationship_type,
                            rel.weight,
                            rel.properties_json,
                            rel.created_at,
                            rel.updated_at,
                        ),
                    )
                    results.append({"id": rel.id, "status": "created"})

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

        conditions: list[str] = []
        params: list[object] = []

        if direction == "outgoing":
            conditions.append("r.source_id = ?")
            params.append(entity.id)
        elif direction == "incoming":
            conditions.append("r.target_id = ?")
            params.append(entity.id)
        else:  # both
            conditions.append("(r.source_id = ? OR r.target_id = ?)")
            params.extend([entity.id, entity.id])

        if relationship_type is not None:
            conditions.append("r.relationship_type = ?")
            params.append(relationship_type.strip().lower())

        where = " AND ".join(conditions)
        sql = (
            "SELECT r.*, "
            "  s.name AS source_name, s.entity_type AS source_type, "
            "  t.name AS target_name, t.entity_type AS target_type "
            "FROM relationships r "
            "JOIN entities s ON s.id = r.source_id "
            "JOIN entities t ON t.id = r.target_id "
            f"WHERE {where} "
            "ORDER BY r.weight DESC, r.updated_at DESC"
        )

        rows = await self._db.fetch_all(sql, tuple(params))
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

        params: list[object] = [source_entity.id, target_entity.id]

        sql = "DELETE FROM relationships WHERE source_id = ? AND target_id = ?"
        if relationship_type is not None:
            sql += " AND relationship_type = ?"
            params.append(relationship_type.strip().lower())

        async with self._db.transaction():
            cursor = await self._db.execute(sql, tuple(params))
            count = cursor.rowcount

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
    ) -> list[dict[str, Any]]:
        """Attach observations to an entity (resolved by name).

        Each observation's ``entity_id`` is overwritten with the
        resolved entity's ID before insertion.

        Returns:
            List of ``{id, entity_id, content}`` for each created observation.
        """
        if not observations:
            return []

        entity = await self.resolve_entity(entity_name)
        results: list[dict[str, Any]] = []

        async with self._db.transaction():
            for obs in observations:
                obs_id = obs.id if obs.id else generate_id()
                now = time.time()
                await self._db.execute(
                    "INSERT INTO observations (id, entity_id, content, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (obs_id, entity.id, obs.content, obs.source, now),
                )
                results.append(
                    {
                        "id": obs_id,
                        "entity_id": entity.id,
                        "content": obs.content,
                    }
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
        rows = await self._db.fetch_all(
            "SELECT * FROM observations WHERE entity_id = ? ORDER BY created_at DESC",
            (entity.id,),
        )
        return [Observation.from_row(row) for row in rows]

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
            row = await self._db.fetch_one(
                "SELECT * FROM entities WHERE name = ? AND entity_type = ?",
                (name, entity_type.strip().lower()),
            )
            if row is not None:
                return Entity.from_row(row)

        # Step 2: exact match on name only
        row = await self._db.fetch_one("SELECT * FROM entities WHERE name = ?", (name,))
        if row is not None:
            return Entity.from_row(row)

        # Step 3: case-insensitive match
        row = await self._db.fetch_one(
            "SELECT * FROM entities WHERE name = ? COLLATE NOCASE", (name,)
        )
        if row is not None:
            return Entity.from_row(row)

        # Step 4: not found — gather suggestions and raise
        suggestions = await self._suggest_similar(name)
        raise EntityNotFoundError(name, suggestions=suggestions)

    async def _suggest_similar(self, name: str, limit: int = 5) -> list[str]:
        """Query FTS5 for entity names similar to *name*.

        Falls back to LIKE-based prefix/substring matching if FTS5
        yields no results or the table doesn't exist.
        """
        suggestions: list[str] = []

        # Try FTS5 first
        try:
            fts_query = name.replace('"', '""')
            rows = await self._db.fetch_all(
                "SELECT name FROM entities_fts WHERE entities_fts MATCH ? LIMIT ?",
                (f'"{fts_query}"', limit),
            )
            suggestions = [str(r["name"]) for r in rows]
        except Exception:  # noqa: BLE001 — FTS table may not exist
            pass

        # Fallback to LIKE if FTS5 returned nothing
        if not suggestions:
            rows = await self._db.fetch_all(
                "SELECT name FROM entities WHERE name LIKE ? LIMIT ?",
                (f"%{name}%", limit),
            )
            suggestions = [str(r["name"]) for r in rows]

        return suggestions

    # ── Stats ────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict[str, Any]:
        """Compute summary statistics for the knowledge graph.

        Returns a dict with:
        - ``entities``: Total entity count.
        - ``relationships``: Total relationship count.
        - ``observations``: Total observation count.
        - ``entity_types``: ``{type: count}`` distribution.
        - ``relationship_types``: ``{type: count}`` distribution.
        - ``most_connected``: Top 10 entities by degree (in + out).
        - ``recent_entities``: 10 most recently updated entities.
        """
        # Total counts (run in parallel-safe fashion — all are reads)
        entity_count_row = await self._db.fetch_one("SELECT COUNT(*) AS cnt FROM entities")
        rel_count_row = await self._db.fetch_one("SELECT COUNT(*) AS cnt FROM relationships")
        obs_count_row = await self._db.fetch_one("SELECT COUNT(*) AS cnt FROM observations")

        entity_count = int(entity_count_row["cnt"]) if entity_count_row else 0
        rel_count = int(rel_count_row["cnt"]) if rel_count_row else 0
        obs_count = int(obs_count_row["cnt"]) if obs_count_row else 0

        # Entity type distribution
        type_rows = await self._db.fetch_all(
            "SELECT entity_type, COUNT(*) AS cnt FROM entities GROUP BY entity_type ORDER BY cnt DESC"
        )
        entity_types = {str(r["entity_type"]): int(r["cnt"]) for r in type_rows}

        # Relationship type distribution
        rel_type_rows = await self._db.fetch_all(
            "SELECT relationship_type, COUNT(*) AS cnt FROM relationships GROUP BY relationship_type ORDER BY cnt DESC"
        )
        relationship_types = {str(r["relationship_type"]): int(r["cnt"]) for r in rel_type_rows}

        # Top 10 most connected entities (in + out degree)
        connected_rows = await self._db.fetch_all(
            "SELECT e.id, e.name, e.entity_type, "
            "  (SELECT COUNT(*) FROM relationships WHERE source_id = e.id) + "
            "  (SELECT COUNT(*) FROM relationships WHERE target_id = e.id) AS degree "
            "FROM entities e "
            "ORDER BY degree DESC "
            "LIMIT 10"
        )
        most_connected = [
            {
                "id": str(r["id"]),
                "name": str(r["name"]),
                "entity_type": str(r["entity_type"]),
                "degree": int(r["degree"]),
            }
            for r in connected_rows
        ]

        # 10 most recent entities
        recent_rows = await self._db.fetch_all(
            "SELECT * FROM entities ORDER BY updated_at DESC LIMIT 10"
        )
        recent_entities = [Entity.from_row(r).to_dict() for r in recent_rows]

        return {
            "entities": entity_count,
            "relationships": rel_count,
            "observations": obs_count,
            "entity_types": entity_types,
            "relationship_types": relationship_types,
            "most_connected": most_connected,
            "recent_entities": recent_entities,
        }
