"""Hybrid search combining vector similarity, FTS5 full-text, and RRF fusion.

Search strategy:
1. Vector similarity via sqlite-vec (cosine distance)
2. FTS5 full-text search (BM25 ranking)
3. Reciprocal Rank Fusion to combine rankings

RRF_score(item) = Σ 1 / (k + rank_in_method)  where k=60
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphrag_mcp.utils.errors import SearchError
from graphrag_mcp.utils.logging import get_logger

if TYPE_CHECKING:
    from graphrag_mcp.db.connection import Database
    from graphrag_mcp.semantic.embeddings import EmbeddingEngine

log = get_logger("semantic.search")

RRF_K = 60  # Standard RRF constant


class HybridSearch:
    """Combined vector + full-text search with RRF fusion.

    Usage::
        search = HybridSearch(db, embedding_engine)
        results = await search.search_entities("detective in Berlin", limit=10)
    """

    def __init__(self, db: Database, embeddings: EmbeddingEngine) -> None:
        self._db = db
        self._embeddings = embeddings

    async def search_entities(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_types: list[str] | None = None,
        include_observations: bool = False,
    ) -> list[dict]:
        """Search entities using hybrid vector + FTS5 + RRF.

        Returns entities ranked by fused relevance score, with optional
        observations and direct relationships attached.
        """
        vec_results: dict[str, float] = {}
        fts_results: dict[str, float] = {}

        # ── Vector search ────────────────────────────────────────────
        if self._embeddings.available:
            try:
                vectors = await self._embeddings.embed([query], self._db)
                query_vec = vectors[0]
                from graphrag_mcp.semantic.embeddings import _embedding_to_bytes

                query_blob = _embedding_to_bytes(query_vec)

                vec_rows = await self._db.fetch_all(
                    """
                    SELECT id, distance
                    FROM entity_embeddings
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                    """,
                    (query_blob, limit * 3),  # over-fetch for fusion
                )
                for rank, row in enumerate(vec_rows):
                    vec_results[str(row["id"])] = 1.0 / (RRF_K + rank + 1)
            except Exception as exc:
                log.debug("Vector search failed: %s", exc)

        # ── FTS5 search ──────────────────────────────────────────────
        try:
            # Escape FTS5 special characters
            safe_query = query.replace('"', '""')
            fts_rows = await self._db.fetch_all(
                """
                SELECT e.id, e.name, rank
                FROM entities_fts fts
                JOIN entities e ON e.rowid = fts.rowid
                WHERE entities_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (f'"{safe_query}" OR {safe_query}', limit * 3),
            )
            for rank, row in enumerate(fts_rows):
                fts_results[str(row["id"])] = 1.0 / (RRF_K + rank + 1)
        except Exception as exc:
            log.debug("FTS5 search failed: %s", exc)

        # ── RRF Fusion ───────────────────────────────────────────────
        all_ids = set(vec_results) | set(fts_results)
        if not all_ids:
            return []

        scored: list[tuple[str, float]] = []
        for eid in all_ids:
            score = vec_results.get(eid, 0.0) + fts_results.get(eid, 0.0)
            scored.append((eid, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # ── Fetch full entities ──────────────────────────────────────
        results: list[dict] = []
        for entity_id, score in scored[:limit]:
            row = await self._db.fetch_one("SELECT * FROM entities WHERE id = ?", (entity_id,))
            if not row:
                continue

            # Type filter
            if entity_types and str(row["entity_type"]) not in entity_types:
                continue

            from graphrag_mcp.models.entity import Entity

            entity = Entity.from_row(row)
            entry: dict = {
                **entity.to_dict(),
                "relevance_score": round(score, 6),
            }

            # Optionally attach observations
            if include_observations:
                obs_rows = await self._db.fetch_all(
                    "SELECT * FROM observations WHERE entity_id = ? ORDER BY created_at DESC",
                    (entity_id,),
                )
                from graphrag_mcp.models.observation import Observation

                entry["observations"] = [Observation.from_row(r).to_dict() for r in obs_rows]

            # Attach direct relationships (always useful context)
            rel_rows = await self._db.fetch_all(
                """
                SELECT r.*, 
                    CASE WHEN r.source_id = ? THEN 'outgoing' ELSE 'incoming' END AS direction,
                    CASE WHEN r.source_id = ? THEN te.name ELSE se.name END AS connected_entity
                FROM relationships r
                LEFT JOIN entities se ON se.id = r.source_id
                LEFT JOIN entities te ON te.id = r.target_id
                WHERE r.source_id = ? OR r.target_id = ?
                LIMIT 20
                """,
                (entity_id, entity_id, entity_id, entity_id),
            )
            entry["relationships"] = [
                {
                    "relationship_type": str(r["relationship_type"]),
                    "direction": str(r["direction"]),
                    "connected_entity": str(r["connected_entity"]),
                    "weight": float(r["weight"]),
                }
                for r in rel_rows
            ]

            results.append(entry)

        return results[:limit]

    async def search_observations(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_id: str | None = None,
    ) -> list[dict]:
        """Search observations using hybrid vector + FTS5 + RRF.

        Optionally scoped to a single entity.
        """
        vec_results: dict[str, float] = {}
        fts_results: dict[str, float] = {}

        # Vector search
        if self._embeddings.available:
            try:
                vectors = await self._embeddings.embed([query], self._db)
                query_vec = vectors[0]
                from graphrag_mcp.semantic.embeddings import _embedding_to_bytes

                query_blob = _embedding_to_bytes(query_vec)

                vec_rows = await self._db.fetch_all(
                    """
                    SELECT id, distance
                    FROM observation_embeddings
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                    """,
                    (query_blob, limit * 3),
                )
                for rank, row in enumerate(vec_rows):
                    vec_results[str(row["id"])] = 1.0 / (RRF_K + rank + 1)
            except Exception as exc:
                log.debug("Observation vector search failed: %s", exc)

        # FTS5 search
        try:
            safe_query = query.replace('"', '""')
            fts_rows = await self._db.fetch_all(
                """
                SELECT o.id
                FROM observations_fts fts
                JOIN observations o ON o.rowid = fts.rowid
                WHERE observations_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (f'"{safe_query}" OR {safe_query}', limit * 3),
            )
            for rank, row in enumerate(fts_rows):
                fts_results[str(row["id"])] = 1.0 / (RRF_K + rank + 1)
        except Exception as exc:
            log.debug("Observation FTS5 search failed: %s", exc)

        # RRF fusion
        all_ids = set(vec_results) | set(fts_results)
        if not all_ids:
            return []

        scored = [(oid, vec_results.get(oid, 0.0) + fts_results.get(oid, 0.0)) for oid in all_ids]
        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[dict] = []
        for obs_id, score in scored[:limit]:
            row = await self._db.fetch_one("SELECT * FROM observations WHERE id = ?", (obs_id,))
            if not row:
                continue
            if entity_id and str(row["entity_id"]) != entity_id:
                continue

            from graphrag_mcp.models.observation import Observation

            obs = Observation.from_row(row)
            entry = {**obs.to_dict(), "relevance_score": round(score, 6)}

            # Attach parent entity name
            ent_row = await self._db.fetch_one(
                "SELECT name, entity_type FROM entities WHERE id = ?",
                (str(row["entity_id"]),),
            )
            if ent_row:
                entry["entity_name"] = str(ent_row["name"])
                entry["entity_type"] = str(ent_row["entity_type"])

            results.append(entry)

        return results[:limit]
