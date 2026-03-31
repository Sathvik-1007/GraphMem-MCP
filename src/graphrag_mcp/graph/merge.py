"""Entity merge — combine two entities into one.

When entities are discovered to be duplicates, merge them:
1. Append source description to target
2. Move all observations from source to target
3. Redirect all relationships from source to target
4. Handle duplicate relationships after redirect
5. Delete source entity
6. All within a single transaction
"""

from __future__ import annotations

import time

from graphrag_mcp.db.connection import Database
from graphrag_mcp.utils.errors import EntityError
from graphrag_mcp.utils.logging import get_logger

log = get_logger("graph.merge")


class EntityMerger:
    """Handles entity deduplication and merge operations."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def merge(self, target_id: str, source_id: str) -> dict:
        """Merge source entity into target entity.

        Args:
            target_id: The entity that will absorb the source.
            source_id: The entity that will be deleted after merge.

        Returns:
            Summary dict with counts of moved/redirected items.
        """
        if target_id == source_id:
            raise EntityError("Cannot merge an entity with itself.")

        # Verify both exist
        target = await self._db.fetch_one("SELECT * FROM entities WHERE id = ?", (target_id,))
        source = await self._db.fetch_one("SELECT * FROM entities WHERE id = ?", (source_id,))
        if not target:
            raise EntityError(f"Target entity {target_id} not found.")
        if not source:
            raise EntityError(f"Source entity {source_id} not found.")

        now = time.time()
        moved_observations = 0
        redirected_relationships = 0
        removed_duplicate_rels = 0

        async with self._db.transaction():
            # 1. Merge descriptions
            new_desc = str(target["description"] or "")
            source_desc = str(source["description"] or "")
            if source_desc and source_desc not in new_desc:
                new_desc = f"{new_desc}\n{source_desc}".strip()
                await self._db.execute(
                    "UPDATE entities SET description = ?, updated_at = ? WHERE id = ?",
                    (new_desc, now, target_id),
                )

            # 2. Move observations
            cursor = await self._db.execute(
                "UPDATE observations SET entity_id = ? WHERE entity_id = ?",
                (target_id, source_id),
            )
            moved_observations = cursor.rowcount

            # 3. Redirect relationships (source as source_id)
            rels_as_source = await self._db.fetch_all(
                "SELECT * FROM relationships WHERE source_id = ?", (source_id,)
            )
            for rel in rels_as_source:
                new_target = rel["target_id"]
                if new_target == source_id:
                    new_target = target_id  # self-ref becomes target self-ref
                # Check for duplicate
                existing = await self._db.fetch_one(
                    "SELECT id, weight FROM relationships WHERE source_id = ? AND target_id = ? AND relationship_type = ?",
                    (target_id, new_target, rel["relationship_type"]),
                )
                if existing:
                    # Keep higher weight
                    if rel["weight"] > existing["weight"]:
                        await self._db.execute(
                            "UPDATE relationships SET weight = ?, updated_at = ? WHERE id = ?",
                            (rel["weight"], now, existing["id"]),
                        )
                    await self._db.execute("DELETE FROM relationships WHERE id = ?", (rel["id"],))
                    removed_duplicate_rels += 1
                else:
                    await self._db.execute(
                        "UPDATE relationships SET source_id = ?, target_id = ?, updated_at = ? WHERE id = ?",
                        (target_id, new_target, now, rel["id"]),
                    )
                    redirected_relationships += 1

            # 4. Redirect relationships (source as target_id)
            rels_as_target = await self._db.fetch_all(
                "SELECT * FROM relationships WHERE target_id = ?", (source_id,)
            )
            for rel in rels_as_target:
                new_source = rel["source_id"]
                if new_source == source_id:
                    continue  # already handled above
                existing = await self._db.fetch_one(
                    "SELECT id, weight FROM relationships WHERE source_id = ? AND target_id = ? AND relationship_type = ?",
                    (new_source, target_id, rel["relationship_type"]),
                )
                if existing:
                    if rel["weight"] > existing["weight"]:
                        await self._db.execute(
                            "UPDATE relationships SET weight = ?, updated_at = ? WHERE id = ?",
                            (rel["weight"], now, existing["id"]),
                        )
                    await self._db.execute("DELETE FROM relationships WHERE id = ?", (rel["id"],))
                    removed_duplicate_rels += 1
                else:
                    await self._db.execute(
                        "UPDATE relationships SET target_id = ?, updated_at = ? WHERE id = ?",
                        (target_id, now, rel["id"]),
                    )
                    redirected_relationships += 1

            # 5. Delete source entity (cascades via FK but we already moved everything)
            await self._db.execute("DELETE FROM entities WHERE id = ?", (source_id,))

        log.info(
            "Merged entity %s into %s: %d observations moved, %d relationships redirected, %d duplicates removed",
            source_id,
            target_id,
            moved_observations,
            redirected_relationships,
            removed_duplicate_rels,
        )

        return {
            "target_id": target_id,
            "source_id": source_id,
            "target_name": str(target["name"]),
            "source_name": str(source["name"]),
            "moved_observations": moved_observations,
            "redirected_relationships": redirected_relationships,
            "removed_duplicate_relationships": removed_duplicate_rels,
        }
