"""Entity merge — combine two entities into one.

When entities are discovered to be duplicates, merge them:
1. Append source description to target
2. Move all observations from source to target
3. Redirect all relationships from source to target
4. Handle duplicate relationships after redirect
5. Delete source entity
6. All within a single transaction

The merger accepts a :class:`StorageBackend` and delegates all
persistence to it, making it compatible with any backend.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, TypedDict

from graph_mem.utils.errors import EntityError
from graph_mem.utils.logging import get_logger

if TYPE_CHECKING:
    from graph_mem.storage.base import StorageBackend

log = get_logger("graph.merge")


def _as_properties(raw: object) -> dict[str, Any]:
    """Coerce a stored properties column into a dict.

    Backends may hand back either a decoded dict or the raw JSON text. Anything
    that is neither — NULL, malformed JSON, a JSON scalar — becomes an empty
    dict, because a merge must not fail on one bad row.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            log.warning("Ignoring unparseable relationship properties during merge")
            return {}
        if isinstance(decoded, dict):
            return decoded
        log.warning("Ignoring non-object relationship properties during merge")
    return {}


class MergeResult(TypedDict):
    target_id: str
    source_id: str
    target_name: str
    source_name: str
    moved_observations: int
    redirected_relationships: int
    removed_duplicate_relationships: int


class EntityMerger:
    """Handles entity deduplication and merge operations."""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    async def _redirect_relationship(
        self,
        rel: dict[str, Any],
        source_id: str,
        target_id: str,
        *,
        redirect_column: str,
        now: float,
    ) -> tuple[int, int]:
        """Redirect a single relationship from *source_id* to *target_id*.

        When *redirect_column* is ``"source_id"``, the relationship's source
        endpoint is rewritten; when ``"target_id"``, the target endpoint is
        rewritten.

        Three outcomes:

        - the edge connected the two entities being merged, so after the
          redirect it would join the surviving entity to itself: dropped
        - an equivalent edge already exists on the target: the two are
          combined and this one is dropped
        - otherwise: the endpoint is repointed

        Returns:
            ``(redirected_count, removed_count)`` — exactly one is ``1``.
        """
        # Work out the endpoint that stays fixed, and the pair to check for an
        # existing equivalent edge.
        if redirect_column == "source_id":
            other = str(rel["target_id"])
            check_source, check_target = target_id, other
        else:
            other = str(rel["source_id"])
            if other == source_id:
                # This edge's other end is the source too, so it is a self-loop
                # on the entity being merged away; the source_id pass has
                # already dealt with it.
                return 0, 0
            check_source, check_target = other, target_id

        # An edge between the two entities being merged describes a
        # relationship an entity would have with itself once they are one
        # entity, which carries no information. Drop it rather than rewrite it
        # into a self-loop — the previous code produced exactly that, and
        # merging two duplicates that reference each other is the *common*
        # case, not an edge case.
        if other in (target_id, source_id):
            await self._storage.delete_relationship_by_id(str(rel["id"]))
            log.debug(
                "Dropped relationship %s: it linked the merged pair and would "
                "have become a self-loop on %s",
                rel["id"],
                target_id,
            )
            return 0, 1

        existing = await self._storage.get_relationship(
            check_source, check_target, str(rel["relationship_type"])
        )

        if existing:
            # Combine the two edges: keep the higher weight, and union the
            # properties rather than discarding the losing edge's. Dropping
            # them silently lost data that upsert_relationship preserves.
            updates: dict[str, Any] = {"updated_at": now}
            if float(rel["weight"]) > float(existing["weight"]):
                updates["weight"] = float(rel["weight"])

            merged_properties = {
                **_as_properties(existing.get("properties")),
                **_as_properties(rel.get("properties")),
            }
            if merged_properties != _as_properties(existing.get("properties")):
                updates["properties"] = merged_properties

            await self._storage.update_relationship(str(existing["id"]), updates)
            await self._storage.delete_relationship_by_id(str(rel["id"]))
            return 0, 1

        # No equivalent edge — just repoint the endpoint.
        if redirect_column == "source_id":
            await self._storage.update_relationship(
                str(rel["id"]),
                {"source_id": target_id, "updated_at": now},
            )
        else:
            await self._storage.update_relationship(
                str(rel["id"]),
                {"target_id": target_id, "updated_at": now},
            )
        return 1, 0

    async def merge(self, target_id: str, source_id: str) -> MergeResult:
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
        target = await self._storage.get_entity_by_id(target_id)
        source = await self._storage.get_entity_by_id(source_id)
        if not target:
            raise EntityError(f"Target entity {target_id} not found.")
        if not source:
            raise EntityError(f"Source entity {source_id} not found.")

        now = time.time()
        moved_observations = 0
        redirected_relationships = 0
        removed_duplicate_rels = 0

        async with self._storage.transaction():
            # 1. Merge descriptions
            new_desc = str(target["description"] or "")
            source_desc = str(source["description"] or "")
            if source_desc and source_desc not in new_desc:
                new_desc = f"{new_desc}\n{source_desc}".strip()
                await self._storage.update_entity_fields(
                    target_id, {"description": new_desc, "updated_at": now}
                )

            # 2. Move observations
            moved_observations = await self._storage.move_observations(source_id, target_id)

            # 3-4. Redirect relationships (both endpoints)
            for column in ("source_id", "target_id"):
                rels = await self._storage.get_relationships_by_column(column, source_id)
                for rel in rels:
                    redirected, removed = await self._redirect_relationship(
                        rel,
                        source_id,
                        target_id,
                        redirect_column=column,
                        now=now,
                    )
                    redirected_relationships += redirected
                    removed_duplicate_rels += removed

            # 5. Delete source entity (cascades via FK but we already moved everything)
            await self._storage.delete_entity(source_id)

        log.info(
            "Merged entity %s into %s: %d observations moved, "
            "%d relationships redirected, %d duplicates removed",
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
