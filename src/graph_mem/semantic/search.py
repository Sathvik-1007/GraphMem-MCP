"""Hybrid search combining vector similarity, FTS5 full-text, and RRF fusion.

Search strategy:
1. Vector similarity via StorageBackend.vector_search (cosine distance)
2. FTS5 full-text search via StorageBackend.fts_search_*
3. Reciprocal Rank Fusion to combine rankings

RRF_score(item) = sum(1 / (k + rank_in_method))  where k=60

The search layer is **storage-agnostic**: it delegates all persistence
and query operations to a :class:`StorageBackend`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from graph_mem.models.entity import Entity
from graph_mem.models.observation import Observation
from graph_mem.utils.errors import EmbeddingError
from graph_mem.utils.logging import get_logger

if TYPE_CHECKING:
    from graph_mem.semantic.embeddings import EmbeddingEngine
    from graph_mem.storage.base import StorageBackend


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
        search = HybridSearch(storage, embedding_engine)
        results = await search.search_entities("detective in Berlin", limit=10)
    """

    def __init__(
        self, storage: StorageBackend, embeddings: EmbeddingEngine, *, alpha: float = 0.5
    ) -> None:
        self._storage = storage
        self._embeddings = embeddings
        self._alpha = alpha

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
            vectors = await self._embeddings.embed([query])
            query_vec = vectors[0]
            if query_vec is None:
                raise ValueError("Embedding returned None for query")

            from graph_mem.semantic.embeddings import _embedding_to_bytes

            query_blob = _embedding_to_bytes(query_vec)

            rows = await self._storage.vector_search(table, query_blob, limit * 3)
            for rank, (item_id, _distance) in enumerate(rows):
                results[item_id] = 1.0 / (RRF_K + rank + 1)
        except (ValueError, EmbeddingError) as exc:
            log.warning(
                "Vector search on %s unavailable: %s — results will rely on FTS5 only", table, exc
            )

        return results

    async def _fts_entity_search(self, query: str, limit: int) -> dict[str, float]:
        """Run FTS5 search on entities and return ``{id: rrf_score}``."""
        results: dict[str, float] = {}
        rows = await self._storage.fts_search_entities(query, limit * 3)
        for rank, (entity_id, _rank_score) in enumerate(rows):
            results[entity_id] = 1.0 / (RRF_K + rank + 1)
        return results

    async def _fts_observation_search(self, query: str, limit: int) -> dict[str, float]:
        """Run FTS5 search on observations and return ``{id: rrf_score}``."""
        results: dict[str, float] = {}
        rows = await self._storage.fts_search_observations(query, limit * 3)
        for rank, (obs_id, _rank_score) in enumerate(rows):
            results[obs_id] = 1.0 / (RRF_K + rank + 1)
        return results

    @staticmethod
    def _rrf_fuse(
        vec_results: dict[str, float],
        fts_results: dict[str, float],
        alpha: float = 0.5,
    ) -> list[tuple[str, float]]:
        """Combine vector and FTS results using Reciprocal Rank Fusion.

        Scores are **normalized to [0, 1]** by dividing by the maximum
        fused score.  This makes relevance_score meaningful and
        comparable across queries.

        Args:
            vec_results: ``{id: rrf_score}`` from vector similarity search.
            fts_results: ``{id: rrf_score}`` from FTS5 full-text search.
            alpha: Balance between vector (alpha) and FTS5 (1-alpha) results.
                0.0 = FTS5 only, 1.0 = vector only, 0.5 = equal weight.

        Returns:
            ``[(id, score), ...]`` sorted by descending fused score,
            normalized to [0, 1].

        Raises:
            ValueError: If *alpha* is not in ``[0.0, 1.0]``.
        """
        if not (0.0 <= alpha <= 1.0):
            raise ValueError(f"alpha must be between 0.0 and 1.0, got {alpha}")

        all_ids = set(vec_results) | set(fts_results)
        if not all_ids:
            return []

        scored = [
            (
                item_id,
                alpha * vec_results.get(item_id, 0.0)
                + (1.0 - alpha) * fts_results.get(item_id, 0.0),
            )
            for item_id in all_ids
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Normalize to [0, 1] so scores are interpretable.
        max_score = scored[0][1] if scored else 0.0
        if max_score > 0.0:
            scored = [(item_id, raw / max_score) for item_id, raw in scored]

        return scored

    async def _boost_from_observations(
        self,
        query: str,
        entity_scores: list[tuple[str, float]],
        limit: int,
        obs_boost_factor: float,
    ) -> list[tuple[str, float]]:
        """Boost entity scores based on matching observations.

        Runs observation search, maps observation scores back to parent
        entities, and merges with entity-level scores.

        Args:
            query: The search query.
            entity_scores: Current ``[(entity_id, score)]`` from entity search.
            limit: Search limit (controls observation search breadth).
            obs_boost_factor: Weight multiplier for observation-derived scores.

        Returns:
            Re-ranked ``[(entity_id, score)]`` with observation boost applied.
        """
        # Run observation search through the same channels
        obs_vec = await self._vector_search(query, "observation_embeddings", limit)
        obs_fts = await self._fts_observation_search(query, limit)
        obs_scored = self._rrf_fuse(obs_vec, obs_fts, alpha=self._alpha)

        if not obs_scored:
            return entity_scores

        # Batch-fetch observations to find parent entity IDs
        obs_ids = [oid for oid, _ in obs_scored[: limit * 3]]
        if not obs_ids:
            return entity_scores

        placeholders = ",".join("?" for _ in obs_ids)
        obs_rows = await self._storage.fetch_all(
            f"SELECT id, entity_id FROM observations WHERE id IN ({placeholders})",
            tuple(obs_ids),
        )
        obs_to_entity: dict[str, str] = {str(r["id"]): str(r["entity_id"]) for r in obs_rows}

        # Accumulate observation scores per parent entity
        obs_entity_scores: dict[str, float] = {}
        for obs_id, obs_score in obs_scored:
            parent_eid = obs_to_entity.get(obs_id)
            if parent_eid:
                obs_entity_scores[parent_eid] = obs_entity_scores.get(parent_eid, 0.0) + obs_score

        # Merge: entity_score + obs_score * boost_factor
        entity_score_map = dict(entity_scores)
        all_ids = set(entity_score_map) | set(obs_entity_scores)

        merged = [
            (
                eid,
                entity_score_map.get(eid, 0.0) + obs_entity_scores.get(eid, 0.0) * obs_boost_factor,
            )
            for eid in all_ids
        ]
        merged.sort(key=lambda x: x[1], reverse=True)
        return merged

    # ── Public search methods ────────────────────────────────────────

    async def search_entities(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_types: list[str] | None = None,
        include_observations: bool = False,
        boost_from_observations: bool = True,
        obs_boost_factor: float = 0.5,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Search entities using hybrid vector + FTS5 + RRF.

        Returns entities ranked by fused relevance score, with optional
        observations and direct relationships attached.

        When *boost_from_observations* is True (default), observation search
        results are used to boost parent entity scores, allowing entities
        to be found through their observations even when entity-level
        text doesn't match well.

        Args:
            query: The search query.
            limit: Maximum number of results to return.
            entity_types: Optional filter to specific entity types.
            include_observations: Whether to include entity observations.
            boost_from_observations: Use observation matches to boost entity scores.
            obs_boost_factor: Weight multiplier for observation-derived scores.
            min_score: Minimum relevance score (0.0-1.0) to include in results.
                Results below this threshold are discarded.  Default 0.0
                (no filtering).
        """
        vec_results = await self._vector_search(query, "entity_embeddings", limit)
        fts_results = await self._fts_entity_search(query, limit)
        scored = self._rrf_fuse(vec_results, fts_results, alpha=self._alpha)

        # ── Observation-boosted entity fusion ────────────────────
        if boost_from_observations and obs_boost_factor > 0.0:
            scored = await self._boost_from_observations(query, scored, limit, obs_boost_factor)

        if not scored:
            return []

        # ── Apply minimum score threshold ────────────────────────
        if min_score > 0.0:
            scored = [(eid, s) for eid, s in scored if s >= min_score]
            if not scored:
                return []

        # ── Batch-fetch entities in one query ────────────────────────
        candidate_ids = [eid for eid, _ in scored[: limit * 3]]
        if not candidate_ids:
            return []

        rows = await self._storage.fetch_entity_rows(candidate_ids)

        # Apply type filter if needed
        if entity_types:
            type_set = set(entity_types)
            rows = [r for r in rows if str(r.get("entity_type", "")) in type_set]

        # Build lookup for O(1) access by id
        row_by_id: dict[str, Any] = {str(r["id"]): r for r in rows}

        # Batch-fetch relationships for all result entities (eliminates N+1)
        all_entity_ids = [eid for eid, _ in scored[: limit * 3] if eid in row_by_id]
        all_rels = await self._storage.get_relationships_for_entities(all_entity_ids)

        # ── Batch-fetch observations if requested (eliminates N+1) ────────
        all_obs_by_entity: dict[str, list[dict[str, object]]] = {}
        if include_observations and all_entity_ids:
            obs_placeholders = ",".join("?" for _ in all_entity_ids)
            obs_rows = await self._storage.fetch_all(
                f"SELECT * FROM observations WHERE entity_id IN ({obs_placeholders}) "
                "ORDER BY created_at DESC",
                tuple(all_entity_ids),
            )
            for r in obs_rows:
                eid = str(r["entity_id"])
                all_obs_by_entity.setdefault(eid, []).append(Observation.from_row(r).to_dict())

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

            # Attach observations from batch
            if include_observations:
                entry["observations"] = all_obs_by_entity.get(entity_id, [])

            # Attach direct relationships (always useful context)
            rel_rows = all_rels.get(entity_id, [])
            entry["relationships"] = [
                _RelationshipEntry(
                    relationship_type=str(r["relationship_type"]),
                    direction="outgoing" if str(r.get("source_id")) == entity_id else "incoming",
                    connected_entity=(
                        str(r.get("target_name", ""))
                        if str(r.get("source_id")) == entity_id
                        else str(r.get("source_name", ""))
                    ),
                    weight=float(r["weight"]),
                )
                for r in rel_rows[:20]  # Limit to 20 relationships per entity
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
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search observations using hybrid vector + FTS5 + RRF.

        Optionally scoped to a single entity.

        Args:
            query: The search query.
            limit: Maximum number of results to return.
            entity_id: Optional — restrict search to this entity.
            min_score: Minimum relevance score (0.0-1.0) to include in results.
                Results below this threshold are discarded.  Default 0.0
                (no filtering).
        """
        vec_results = await self._vector_search(query, "observation_embeddings", limit)
        fts_results = await self._fts_observation_search(query, limit)
        scored = self._rrf_fuse(vec_results, fts_results, alpha=self._alpha)
        if not scored:
            return []

        # ── Apply minimum score threshold ────────────────────────
        if min_score > 0.0:
            scored = [(oid, s) for oid, s in scored if s >= min_score]
            if not scored:
                return []

        # ── Batch-fetch observations ─────────────────────────────────
        candidate_ids = [oid for oid, _ in scored[: limit * 3]]
        if not candidate_ids:
            return []

        # Use raw fetch for observation batch lookup
        obs_rows = await self._storage.fetch_all(
            f"SELECT * FROM observations WHERE id IN ({','.join('?' for _ in candidate_ids)})",
            tuple(candidate_ids),
        )
        obs_by_id: dict[str, Any] = {str(r["id"]): r for r in obs_rows}

        # ── Batch-fetch parent entity names ──────────────────────────
        parent_ids = {str(r["entity_id"]) for r in obs_rows}
        ent_lookup = await self._storage.resolve_entity_names(parent_ids)
        # Also need entity_type — fetch full rows
        ent_type_lookup: dict[str, str] = {}
        if parent_ids:
            ent_rows = await self._storage.fetch_entity_rows(list(parent_ids))
            ent_type_lookup = {str(r["id"]): str(r.get("entity_type", "")) for r in ent_rows}

        # ── Assemble results in score order ──────────────────────────
        results: list[dict[str, Any]] = []
        for obs_id, score in scored:
            row = obs_by_id.get(obs_id)
            if not row:
                continue
            if entity_id and str(row["entity_id"]) != entity_id:
                continue

            obs = Observation.from_row(row)
            entry: dict[str, Any] = {**obs.to_dict(), "relevance_score": round(score, 6)}

            parent_eid = str(row["entity_id"])
            if parent_eid in ent_lookup:
                entry["entity_name"] = ent_lookup[parent_eid]
            if parent_eid in ent_type_lookup:
                entry["entity_type"] = ent_type_lookup[parent_eid]

            results.append(entry)
            if len(results) >= limit:
                break

        return results
