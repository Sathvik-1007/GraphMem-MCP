"""Hybrid search combining vector similarity, FTS5 full-text, and RRF fusion.

Search strategy:
1. Vector similarity via sqlite-vec (cosine distance)
2. FTS5 full-text search (BM25 ranking)
3. Reciprocal Rank Fusion to combine rankings

RRF_score(item) = Σ 1 / (k + rank_in_method)  where k=60
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, TypedDict

from graphrag_mcp.utils.errors import EmbeddingError
from graphrag_mcp.utils.logging import get_logger


from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.semantic.embeddings import _embedding_to_bytes

if TYPE_CHECKING:
    from graphrag_mcp.db.connection import Database
    from graphrag_mcp.semantic.embeddings import EmbeddingEngine


class _RelationshipEntry(TypedDict):
    """Shape of relationship entries nested inside search results."""

    relationship_type: str
    direction: str
    connected_entity: str
    weight: float


class SearchResult(TypedDict, total=False):
    """Return type for entity/observation search results.

    Required keys come from ``Entity.to_dict()`` plus ``relevance_score``.
    Optional keys (``observations``, ``relationships``) are present depending
    on the search method and options.
    """

    # Required keys (from Entity.to_dict)
    id: str
    name: str
    entity_type: str
    description: str
    properties: dict[str, object]
    created_at: float
    updated_at: float
    relevance_score: float
    # Optional keys
    observations: list[dict[str, object]]
    relationships: list[_RelationshipEntry]


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

    # ── Shared retrieval primitives ──────────────────────────────────

    async def _vector_search(
        self,
        query: str,
        table: str,
        limit: int,
    ) -> dict[str, float]:
        """Run vector similarity search and return ``{id: rrf_score}``."""
        results: dict[str, float] = {}
        if not self._embeddings.available:
            return results

        try:
            vectors = await self._embeddings.embed([query], self._db)
            query_vec = vectors[0]
            if query_vec is None:
                raise ValueError("Embedding returned None for query")

            query_blob = _embedding_to_bytes(query_vec)

            rows = await self._db.fetch_all(
                f"""
                SELECT id, distance
                FROM {table}
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (query_blob, limit * 3),
            )
            for rank, row in enumerate(rows):
                results[str(row["id"])] = 1.0 / (RRF_K + rank + 1)
        except (ValueError, EmbeddingError, sqlite3.Error) as exc:
            log.warning(
                "Vector search on %s unavailable: %s — results will rely on FTS5 only", table, exc
            )

        return results

    async def _fts_search(
        self,
        query: str,
        *,
        fts_table: str,
        join_sql: str,
        id_column: str,
        limit: int,
    ) -> dict[str, float]:
        """Run FTS5 search and return ``{id: rrf_score}``."""
        results: dict[str, float] = {}
        try:
            safe_query = query.replace('"', '""')
            rows = await self._db.fetch_all(
                f"""
                SELECT {id_column}
                FROM {fts_table} fts
                {join_sql}
                WHERE {fts_table} MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (f'"{safe_query}" OR {safe_query}', limit * 3),
            )
            for rank, row in enumerate(rows):
                results[str(row["id"])] = 1.0 / (RRF_K + rank + 1)
        except (sqlite3.Error, ValueError) as exc:
            log.warning(
                "FTS5 search on %s unavailable: %s — results will rely on vector search only",
                fts_table,
                exc,
            )

        return results

    @staticmethod
    def _rrf_fuse(
        vec_results: dict[str, float],
        fts_results: dict[str, float],
    ) -> list[tuple[str, float]]:
        """Combine vector and FTS results using Reciprocal Rank Fusion.

        Returns ``[(id, score), ...]`` sorted by descending fused score.
        """
        all_ids = set(vec_results) | set(fts_results)
        if not all_ids:
            return []

        scored = [
            (item_id, vec_results.get(item_id, 0.0) + fts_results.get(item_id, 0.0))
            for item_id in all_ids
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ── Public search methods ────────────────────────────────────────

    async def search_entities(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_types: list[str] | None = None,
        include_observations: bool = False,
    ) -> list[SearchResult]:
        """Search entities using hybrid vector + FTS5 + RRF.

        Returns entities ranked by fused relevance score, with optional
        observations and direct relationships attached.
        """
        vec_results = await self._vector_search(query, "entity_embeddings", limit)
        fts_results = await self._fts_search(
            query,
            fts_table="entities_fts",
            join_sql="JOIN entities e ON e.rowid = fts.rowid",
            id_column="e.id, e.name, rank",
            limit=limit,
        )
        scored = self._rrf_fuse(vec_results, fts_results)
        if not scored:
            return []

        # ── Batch-fetch entities in one query ────────────────────────
        # Fetch more candidates than needed so the type filter can
        # discard some without falling below `limit`.
        candidate_ids = [eid for eid, _ in scored[: limit * 3]]
        if not candidate_ids:
            return []

        placeholders = ",".join("?" for _ in candidate_ids)
        type_clause = ""
        params: list[object] = list(candidate_ids)

        if entity_types:
            type_ph = ",".join("?" for _ in entity_types)
            type_clause = f" AND entity_type IN ({type_ph})"
            params.extend(entity_types)

        rows = await self._db.fetch_all(
            f"SELECT * FROM entities WHERE id IN ({placeholders}){type_clause}",
            tuple(params),
        )

        # Build lookup for O(1) access by id
        row_by_id: dict[str, Any] = {str(r["id"]): r for r in rows}

        # ── Assemble results in score order ──────────────────────────
        results: list[SearchResult] = []
        for entity_id, score in scored:
            if entity_id not in row_by_id:
                continue

            entity = Entity.from_row(row_by_id[entity_id])
            entry = SearchResult(
                **entity.to_dict(),  # type: ignore[typeddict-item]
                relevance_score=round(score, 6),
            )

            # Optionally attach observations
            if include_observations:
                obs_rows = await self._db.fetch_all(
                    "SELECT * FROM observations WHERE entity_id = ? ORDER BY created_at DESC",
                    (entity_id,),
                )

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
                _RelationshipEntry(
                    relationship_type=str(r["relationship_type"]),
                    direction=str(r["direction"]),
                    connected_entity=str(r["connected_entity"]),
                    weight=float(r["weight"]),
                )
                for r in rel_rows
            ]

            results.append(entry)
            if len(results) >= limit:
                break

        return results

    async def search_observations(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search observations using hybrid vector + FTS5 + RRF.

        Optionally scoped to a single entity.
        """
        vec_results = await self._vector_search(query, "observation_embeddings", limit)
        fts_results = await self._fts_search(
            query,
            fts_table="observations_fts",
            join_sql="JOIN observations o ON o.rowid = fts.rowid",
            id_column="o.id",
            limit=limit,
        )
        scored = self._rrf_fuse(vec_results, fts_results)
        if not scored:
            return []

        # ── Batch-fetch observations in one query ────────────────────
        candidate_ids = [oid for oid, _ in scored[: limit * 3]]
        if not candidate_ids:
            return []

        placeholders = ",".join("?" for _ in candidate_ids)
        obs_rows = await self._db.fetch_all(
            f"SELECT * FROM observations WHERE id IN ({placeholders})",
            tuple(candidate_ids),
        )
        obs_by_id: dict[str, Any] = {str(r["id"]): r for r in obs_rows}

        # ── Batch-fetch parent entity names ──────────────────────────
        parent_ids = list({str(r["entity_id"]) for r in obs_rows})
        ent_lookup: dict[str, dict[str, str]] = {}
        if parent_ids:
            ent_ph = ",".join("?" for _ in parent_ids)
            ent_rows = await self._db.fetch_all(
                f"SELECT id, name, entity_type FROM entities WHERE id IN ({ent_ph})",
                tuple(parent_ids),
            )
            ent_lookup = {
                str(r["id"]): {"name": str(r["name"]), "entity_type": str(r["entity_type"])}
                for r in ent_rows
            }

        # ── Assemble results in score order ──────────────────────────
        results: list[dict[str, Any]] = []
        for obs_id, score in scored:
            row = obs_by_id.get(obs_id)
            if not row:
                continue
            if entity_id and str(row["entity_id"]) != entity_id:
                continue

            obs = Observation.from_row(row)
            entry = {**obs.to_dict(), "relevance_score": round(score, 6)}

            parent = ent_lookup.get(str(row["entity_id"]))
            if parent:
                entry["entity_name"] = parent["name"]
                entry["entity_type"] = parent["entity_type"]

            results.append(entry)
            if len(results) >= limit:
                break

        return results
