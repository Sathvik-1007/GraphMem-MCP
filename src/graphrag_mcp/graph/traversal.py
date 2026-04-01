"""Graph traversal — multi-hop BFS and path-finding over the knowledge graph.

Provides efficient traversal using SQLite recursive CTEs with cycle
detection via json_array. All operations are read-only and async.

The traversal layer accepts a :class:`StorageBackend` and uses its
``fetch_all`` escape hatch for complex recursive queries. Graph-database
backends (Neo4j, Memgraph) will override ``fetch_all`` with their
native traversal queries (Cypher, GQL).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.relationship import Relationship
from graphrag_mcp.utils.logging import get_logger

if TYPE_CHECKING:
    from graphrag_mcp.storage.base import StorageBackend

log = get_logger("graph.traversal")

# Safety limit: maximum number of visited nodes in a single traversal
# to prevent runaway CTEs on dense graphs.
_MAX_VISITED = 1000

# ── Shared CTE fragments ────────────────────────────────────────────────
# The bidirectional CASE expression that resolves the "next" entity in a
# traversal step.  Used in find_connections, find_paths, and get_subgraph.
_NEXT_ENTITY_BOTH = "CASE WHEN r.source_id = t.entity_id THEN r.target_id ELSE r.source_id END"

# Bidirectional JOIN condition
_JOIN_BOTH = "(r.source_id = t.entity_id OR r.target_id = t.entity_id)"

# Cycle-detection sub-query — prevents revisiting nodes already in the
# ``visited`` json_array.  Use with ``.format(next_entity=...)`` to plug
# in the correct next-entity expression.
_CYCLE_GUARD = (
    "AND NOT EXISTS (\n"
    "                  SELECT 1 FROM json_each(t.visited)\n"
    "                  WHERE value = {next_entity}\n"
    "              )"
)


class GraphTraversal:
    """Multi-hop graph traversal using recursive CTEs.

    All methods are async and read-only.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    # ── Connection discovery ─────────────────────────────────────────────

    async def find_connections(
        self,
        entity_id: str,
        *,
        max_hops: int = 3,
        relationship_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Find all entities reachable within *max_hops* from *entity_id*.

        Args:
            entity_id: Starting entity's ID.
            max_hops: Maximum traversal depth (1-10, clamped).
            relationship_types: Optional whitelist of relationship types.
            direction: ``"outgoing"``, ``"incoming"``, or ``"both"``.

        Returns:
            List of dicts, each containing:
            - ``entity``: The discovered entity as a dict.
            - ``depth``: Hop count from the starting entity.
            - ``path``: List of
              ``{entity_name, relationship_type, direction}`` steps.
        """
        max_hops = max(1, min(max_hops, 10))

        # Build the JOIN condition based on direction
        if direction == "outgoing":
            join_condition = "r.source_id = t.entity_id"
            next_entity = "r.target_id"
            dir_expr = "'outgoing'"
        elif direction == "incoming":
            join_condition = "r.target_id = t.entity_id"
            next_entity = "r.source_id"
            dir_expr = "'incoming'"
        else:  # both
            join_condition = _JOIN_BOTH
            next_entity = _NEXT_ENTITY_BOTH
            dir_expr = "CASE WHEN r.source_id = t.entity_id THEN 'outgoing' ELSE 'incoming' END"

        # Optional relationship type filter
        rel_type_filter = ""
        params: list[object] = [entity_id, entity_id, max_hops]
        if relationship_types:
            placeholders = ", ".join("?" for _ in relationship_types)
            rel_type_filter = f"AND r.relationship_type IN ({placeholders})"
            params.extend(rt.strip().lower() for rt in relationship_types)

        cycle_guard = _CYCLE_GUARD.format(next_entity=next_entity)

        sql = f"""
        WITH RECURSIVE traverse(entity_id, depth, visited, path) AS (
            SELECT ?, 0, json_array(?), json_array()
            UNION ALL
            SELECT
                {next_entity},
                t.depth + 1,
                json_insert(t.visited, '$[#]', {next_entity}),
                json_insert(t.path, '$[#]', json_object(
                    'relationship_type', r.relationship_type,
                    'direction', {dir_expr},
                    'entity_id', {next_entity}
                ))
            FROM traverse t
            JOIN relationships r ON ({join_condition})
            {rel_type_filter}
            WHERE t.depth < ?
              AND json_array_length(t.visited) < {_MAX_VISITED}
              {cycle_guard}
        )
        SELECT DISTINCT t.entity_id, t.depth, t.path, e.*
        FROM traverse t
        JOIN entities e ON e.id = t.entity_id
        WHERE t.depth > 0
        ORDER BY t.depth, e.name
        """

        # Parameters: entity_id (seed), entity_id (initial visited), max_hops,
        # then optional relationship_types.
        # Rearrange: relationship_type params go before max_hops in the CTE,
        # but we placed max_hops in the WHERE clause position.
        # The CTE references ? in order: seed, seed, then rel_types, then max_hops.
        # Fix ordering: seed, seed, [rel_types...], max_hops
        if relationship_types:
            # Rebuild params in correct positional order
            ordered_params: list[object] = [entity_id, entity_id]
            ordered_params.extend(rt.strip().lower() for rt in relationship_types)
            ordered_params.append(max_hops)
            params = ordered_params

        rows = await self._storage.fetch_all(sql, tuple(params))

        # Deduplicate by entity_id — keep the shallowest depth
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            eid = str(row["entity_id"])
            depth = int(row["depth"])
            if eid not in seen or depth < seen[eid]["depth"]:
                path_raw = row["path"]
                path = json.loads(path_raw) if isinstance(path_raw, str) else path_raw

                # Enrich path with entity names
                enriched_path = await self._enrich_path(path)

                seen[eid] = {
                    "entity": Entity.from_row(row).to_dict(),
                    "depth": depth,
                    "path": enriched_path,
                }

        results = sorted(
            seen.values(), key=lambda x: (x["depth"], str(x["entity"].get("name", "")))
        )
        log.debug(
            "find_connections from %s: %d entities found within %d hops",
            entity_id,
            len(results),
            max_hops,
        )
        return results

    # ── Shortest paths ───────────────────────────────────────────────────

    async def find_paths(
        self,
        source_id: str,
        target_id: str,
        *,
        max_hops: int = 5,
    ) -> list[list[dict[str, Any]]]:
        """Find shortest paths between two entities using BFS.

        Args:
            source_id: Starting entity ID.
            target_id: Destination entity ID.
            max_hops: Maximum path length (1-10, clamped).

        Returns:
            List of paths. Each path is a list of
            ``{entity_name, entity_id, relationship_type}`` steps.
            Paths are ordered shortest-first.
        """
        max_hops = max(1, min(max_hops, 10))

        if source_id == target_id:
            return []

        cycle_guard = _CYCLE_GUARD.format(next_entity=_NEXT_ENTITY_BOTH)

        sql = f"""
        WITH RECURSIVE traverse(entity_id, depth, visited, path) AS (
            SELECT ?, 0, json_array(?), json_array(json_object(
                'entity_id', ?, 'entity_name', '', 'relationship_type', ''))
            UNION ALL
            SELECT
                {_NEXT_ENTITY_BOTH},
                t.depth + 1,
                json_insert(t.visited, '$[#]',
                    {_NEXT_ENTITY_BOTH}
                ),
                json_insert(t.path, '$[#]', json_object(
                    'entity_id', {_NEXT_ENTITY_BOTH},
                    'entity_name', '',
                    'relationship_type', r.relationship_type
                ))
            FROM traverse t
            JOIN relationships r ON {_JOIN_BOTH}
            WHERE t.depth < ?
              AND json_array_length(t.visited) < {_MAX_VISITED}
              {cycle_guard}
        )
        SELECT t.path, t.depth
        FROM traverse t
        WHERE t.entity_id = ?
        ORDER BY t.depth
        LIMIT 10
        """

        rows = await self._storage.fetch_all(
            sql, (source_id, source_id, source_id, max_hops, target_id)
        )

        if not rows:
            return []

        # Collect all entity IDs across all paths for batch name resolution
        all_entity_ids: set[str] = set()
        parsed_paths: list[list[dict[str, Any]]] = []
        for row in rows:
            path_raw = row["path"]
            path = json.loads(path_raw) if isinstance(path_raw, str) else path_raw
            parsed_paths.append(path)
            for step in path:
                all_entity_ids.add(str(step["entity_id"]))

        # Batch-resolve entity names
        name_map = await self._storage.resolve_entity_names(all_entity_ids)

        results: list[list[dict[str, Any]]] = []
        for path in parsed_paths:
            enriched: list[dict[str, Any]] = []
            for step in path:
                eid = str(step["entity_id"])
                enriched.append(
                    {
                        "entity_id": eid,
                        "entity_name": name_map.get(eid, ""),
                        "relationship_type": str(step.get("relationship_type", "")),
                    }
                )
            results.append(enriched)

        log.debug(
            "find_paths %s -> %s: %d paths found",
            source_id,
            target_id,
            len(results),
        )
        return results

    # ── Subgraph extraction ──────────────────────────────────────────────

    async def get_subgraph(
        self,
        entity_ids: list[str],
        *,
        radius: int = 2,
    ) -> dict[str, Any]:
        """Extract a subgraph around a set of seed entities.

        BFS from each seed up to *radius* hops, then collect all
        unique entities and the relationships between them.

        Args:
            entity_ids: Seed entity IDs.
            radius: How many hops to expand from each seed (1-5, clamped).

        Returns:
            ``{entities: [...], relationships: [...]}`` with deduplicated
            items serialized via ``to_dict()``.
        """
        radius = max(1, min(radius, 5))

        if not entity_ids:
            return {"entities": [], "relationships": []}

        # Collect all reachable entity IDs via BFS from each seed
        all_entity_ids: set[str] = set()

        cycle_guard = _CYCLE_GUARD.format(next_entity=_NEXT_ENTITY_BOTH)

        for seed_id in entity_ids:
            sql = f"""
            WITH RECURSIVE traverse(entity_id, depth, visited) AS (
                SELECT ?, 0, json_array(?)
                UNION ALL
                SELECT
                    {_NEXT_ENTITY_BOTH},
                    t.depth + 1,
                    json_insert(t.visited, '$[#]',
                        {_NEXT_ENTITY_BOTH}
                    )
                FROM traverse t
                JOIN relationships r ON {_JOIN_BOTH}
                WHERE t.depth < ?
                  AND json_array_length(t.visited) < {_MAX_VISITED}
                  {cycle_guard}
            )
            SELECT DISTINCT entity_id FROM traverse
            """
            rows = await self._storage.fetch_all(sql, (seed_id, seed_id, radius))
            for row in rows:
                all_entity_ids.add(str(row["entity_id"]))

        if not all_entity_ids:
            return {"entities": [], "relationships": []}

        # Fetch all entities in the subgraph
        entity_rows = await self._storage.fetch_entity_rows(list(all_entity_ids))
        entities = [Entity.from_row(r).to_dict() for r in entity_rows]

        # Fetch all relationships between entities in the subgraph
        rel_rows = await self._storage.fetch_relationships_between(list(all_entity_ids))
        relationships = [Relationship.from_row(r).to_dict() for r in rel_rows]

        log.debug(
            "get_subgraph: %d seeds, radius=%d -> %d entities, %d relationships",
            len(entity_ids),
            radius,
            len(entities),
            len(relationships),
        )
        return {"entities": entities, "relationships": relationships}

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _enrich_path(self, path: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add entity_name to each step in a path."""
        if not path:
            return path

        entity_ids = {str(step["entity_id"]) for step in path if step.get("entity_id")}
        name_map = await self._storage.resolve_entity_names(entity_ids)

        enriched: list[dict[str, Any]] = []
        for step in path:
            eid = str(step.get("entity_id", ""))
            enriched.append(
                {
                    "entity_id": eid,
                    "entity_name": name_map.get(eid, ""),
                    "relationship_type": str(step.get("relationship_type", "")),
                    "direction": str(step.get("direction", "")),
                }
            )
        return enriched
