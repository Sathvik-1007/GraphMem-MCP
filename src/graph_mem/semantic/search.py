"""Hybrid search combining vector similarity, FTS5 full-text, and RRF fusion.

Search strategy:
1. Vector similarity via SQLiteBackend.vector_search (cosine distance)
2. FTS5 full-text search via SQLiteBackend.fts_search_*
3. Reciprocal Rank Fusion to combine rankings

RRF_score(item) = sum(1 / (k + rank_in_method))  where k=60

Fused scores are reported raw, so they top out at ``MAX_RRF_SCORE`` ≈ 0.0164
and mean the same thing from one query to the next.

The search layer is **storage-agnostic**: it delegates all persistence
and query operations to a :class:`SQLiteBackend`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from graph_mem.models.entity import Entity
from graph_mem.models.observation import Observation
from graph_mem.utils.errors import EmbeddingError
from graph_mem.utils.logging import get_logger

if TYPE_CHECKING:
    from graph_mem.semantic.embeddings import EmbeddingEngine
    from graph_mem.storage import SQLiteBackend


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
    relationships_truncated: bool


log = get_logger("semantic.search")

RRF_K = 60  # Standard RRF constant

# Best score one ranked list can contribute: rank 0 → 1 / (RRF_K + 1) ≈ 0.0164.
# A fused score is a convex combination of two such contributions, so it never
# exceeds this value either.  Callers picking a ``min_score`` need this number.
MAX_RRF_SCORE = 1.0 / (RRF_K + 1)

# Each retrieval channel is asked for ``limit * CANDIDATE_MULTIPLIER`` rows so
# that filtering (entity type, entity scope) and de-duplication across channels
# still leave enough survivors to fill ``limit``.  3 covers the common case
# where most of the top hits survive filtering, without making the FTS5 and
# vector scans meaningfully more expensive.
CANDIDATE_MULTIPLIER = 3

# Relationships attached to a single search result.  A hub entity can have
# thousands of edges and the consumer is a language model with a context
# budget, so the list is capped; results report whether the cap was hit via
# ``relationships_truncated``.
MAX_RELATIONSHIPS_PER_RESULT = 20

# Ceiling on the observation-derived score an entity can accumulate, before
# ``obs_boost_factor`` is applied: exactly what one perfectly-ranked
# observation is worth.  Without it the boost is an unbounded sum and an
# entity with ten mediocre observations outranks one with a single perfect
# match.
MAX_OBS_BOOST = MAX_RRF_SCORE


class HybridSearch:
    """Combined vector + full-text search with RRF fusion.

    Usage::
        search = HybridSearch(storage, embedding_engine)
        results = await search.search_entities("detective in Berlin", limit=10)
    """

    def __init__(
        self, storage: SQLiteBackend, embeddings: EmbeddingEngine, *, alpha: float = 0.5
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

            rows = await self._storage.vector_search(
                table, query_blob, limit * CANDIDATE_MULTIPLIER
            )
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
        rows = await self._storage.fts_search_entities(query, limit * CANDIDATE_MULTIPLIER)
        for rank, (entity_id, _rank_score) in enumerate(rows):
            results[entity_id] = 1.0 / (RRF_K + rank + 1)
        return results

    async def _fts_observation_search(self, query: str, limit: int) -> dict[str, float]:
        """Run FTS5 search on observations and return ``{id: rrf_score}``."""
        results: dict[str, float] = {}
        rows = await self._storage.fts_search_observations(query, limit * CANDIDATE_MULTIPLIER)
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

        Scores are **raw RRF sums** in ``(0, MAX_RRF_SCORE]`` — a result
        ranked first by every channel scores ``1 / (RRF_K + 1)`` ≈ 0.0164,
        and everything below it scores strictly less.  They are absolute:
        a weak result set produces uniformly small scores, which is what
        makes a ``min_score`` threshold able to reject one.

        Scores are deliberately *not* divided by the top score.  Doing that
        gave a lone junk hit a perfect 1.0 and compressed a realistic spread
        into the top percent, so no threshold could tell the two apart.

        Args:
            vec_results: ``{id: rrf_score}`` from vector similarity search.
            fts_results: ``{id: rrf_score}`` from FTS5 full-text search.
            alpha: Balance between vector (alpha) and FTS5 (1-alpha) results.
                0.0 = FTS5 only, 1.0 = vector only, 0.5 = equal weight.

        Returns:
            ``[(id, score), ...]`` sorted by descending fused score.

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
        entities, and merges with entity-level scores.  An entity's
        observation contribution is summed but clamped to
        :data:`MAX_OBS_BOOST` — the worth of one perfectly-ranked
        observation — so quantity of mediocre matches cannot beat quality.

        The result stays on the raw RRF scale of :meth:`_rrf_fuse`: nothing
        here is re-normalized, so a boosted and an unboosted search produce
        scores a caller can compare against the same ``min_score``.

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
        obs_ids = [oid for oid, _ in obs_scored]
        if not obs_ids:
            return entity_scores

        obs_to_entity = await self._storage.fetch_observation_parents(obs_ids)

        # Accumulate observation scores per parent entity, capped so that many
        # weak observations cannot outweigh one strong one.
        obs_entity_scores: dict[str, float] = {}
        for obs_id, obs_score in obs_scored:
            parent_eid = obs_to_entity.get(obs_id)
            if parent_eid:
                obs_entity_scores[parent_eid] = min(
                    obs_entity_scores.get(parent_eid, 0.0) + obs_score, MAX_OBS_BOOST
                )

        # Merge: entity_score + capped obs_score * boost_factor
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
        observations and direct relationships attached.  At most
        :data:`MAX_RELATIONSHIPS_PER_RESULT` relationships are attached per
        entity; ``relationships_truncated`` says whether more were dropped.

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
            min_score: Minimum raw RRF relevance score to include in results.
                Scores are small and absolute: an entity ranked first by every
                channel scores ``MAX_RRF_SCORE`` ≈ 0.0164, plus at most
                ``obs_boost_factor * MAX_OBS_BOOST`` from observation matches.
                Default 0.0 (no filtering).
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

        # ── Batch-fetch every candidate, then filter, then truncate ──────
        # Order matters: truncating first and filtering afterwards silently
        # under-returns whenever the top candidates are of the wrong type
        # while matching entities rank just below them.
        rows = await self._storage.fetch_entity_rows([eid for eid, _ in scored])
        row_by_id: dict[str, Any] = {str(r["id"]): r for r in rows}

        if entity_types:
            type_set = set(entity_types)
            row_by_id = {
                eid: r for eid, r in row_by_id.items() if str(r.get("entity_type", "")) in type_set
            }

        result_ids = [eid for eid, _ in scored if eid in row_by_id][:limit]
        if not result_ids:
            return []

        score_by_id = dict(scored)

        # Batch-fetch relationships for the result entities (eliminates N+1)
        all_rels = await self._storage.get_relationships_for_entities(result_ids)

        # ── Batch-fetch observations if requested (eliminates N+1) ────────
        all_obs_by_entity: dict[str, list[dict[str, object]]] = {}
        if include_observations:
            obs_rows = await self._storage.fetch_observations_for_entities(result_ids)
            for r in obs_rows:
                eid = str(r["entity_id"])
                all_obs_by_entity.setdefault(eid, []).append(Observation.from_row(r).to_dict())

        # ── Assemble results in score order ──────────────────────────
        results: list[SearchResult] = []
        for entity_id in result_ids:
            entity = Entity.from_row(row_by_id[entity_id])
            entry = SearchResult(
                **entity.to_dict(),  # type: ignore[typeddict-item]
                relevance_score=round(score_by_id[entity_id], 6),
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
                for r in rel_rows[:MAX_RELATIONSHIPS_PER_RESULT]
            ]
            entry["relationships_truncated"] = len(rel_rows) > MAX_RELATIONSHIPS_PER_RESULT

            results.append(entry)

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
            entity_id: Optional — restrict search to this entity.  The scope is
                applied to every retrieved candidate, not just the top ones.
            min_score: Minimum raw RRF relevance score to include in results.
                Scores are small and absolute: an observation ranked first by
                every channel scores ``MAX_RRF_SCORE`` ≈ 0.0164.  Default 0.0
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

        # ── Batch-fetch every candidate, scoped in SQL ───────────────
        # The entity scope belongs in the query, not in the assembly loop:
        # filtering after a truncation returned almost nothing whenever the
        # entity's own observations were not globally top-ranked, which is
        # precisely the case scoping exists for.
        candidate_ids = [oid for oid, _ in scored]
        obs_rows = await self._storage.fetch_observation_rows(
            candidate_ids, entity_id=entity_id or None
        )
        obs_by_id: dict[str, Any] = {str(r["id"]): r for r in obs_rows}

        # ── Batch-fetch parent entity rows (name + type in one query) ─────
        parent_ids = {str(r["entity_id"]) for r in obs_rows}
        ent_rows = await self._storage.fetch_entity_rows(list(parent_ids))
        ent_lookup = {str(r["id"]): r for r in ent_rows}

        # ── Assemble results in score order, truncating last ─────────
        results: list[dict[str, Any]] = []
        for obs_id, score in scored:
            row = obs_by_id.get(obs_id)
            if not row:
                continue

            obs = Observation.from_row(row)
            entry: dict[str, Any] = {**obs.to_dict(), "relevance_score": round(score, 6)}

            parent = ent_lookup.get(str(row["entity_id"]))
            if parent is not None:
                entry["entity_name"] = str(parent["name"])
                entry["entity_type"] = str(parent.get("entity_type", ""))

            results.append(entry)
            if len(results) >= limit:
                break

        return results
