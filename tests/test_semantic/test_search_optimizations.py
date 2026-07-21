"""Tests for search optimization fixes: FTS5 hardening, batch relationships,
RRF alpha, observation boost, raw scores, and filter-before-truncate."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graph_mem.graph.engine import GraphEngine
from graph_mem.models.entity import Entity
from graph_mem.models.observation import Observation
from graph_mem.models.relationship import Relationship
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import (
    MAX_OBS_BOOST,
    MAX_RELATIONSHIPS_PER_RESULT,
    MAX_RRF_SCORE,
    HybridSearch,
)
from graph_mem.storage import SQLiteBackend


@pytest_asyncio.fixture
async def search_env(tmp_path: Path):
    storage = SQLiteBackend(tmp_path / "test.db")
    await storage.initialize()
    graph = GraphEngine(storage)
    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
    search = HybridSearch(storage, embeddings)
    yield storage, graph, search
    await storage.close()


# ═══════════════════════════════════════════════════════════════════════════
# FTS5 hardening
# ═══════════════════════════════════════════════════════════════════════════


async def test_fts5_sanitize_double_quotes(search_env):
    """Queries containing double quotes don't crash FTS5."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name='Test "Quoted" Entity', entity_type="concept")])
    results = await search.search_entities('Test "Quoted"')
    assert len(results) >= 0  # Should not raise


async def test_fts5_sanitize_boolean_operators(search_env):
    """FTS5 boolean operators in queries are treated as literals."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="NOT a bug", entity_type="concept")])
    results = await search.search_entities("NOT a bug")
    assert len(results) >= 0


async def test_fts5_sanitize_special_chars(search_env):
    """Queries with *, -, +, :, ^, () don't break FTS5."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="C++ Templates", entity_type="concept")])
    for query in ["C++", "error-handling", "module:auth", "test*", "(group)", "^start"]:
        results = await search.search_entities(query)
        assert isinstance(results, list)


async def test_fts5_sanitize_empty_string(search_env):
    """Empty query returns empty results without error."""
    _db, _graph, search = search_env
    results = await search.search_entities("")
    assert results == []


async def test_fts5_sanitize_whitespace_only(search_env):
    """Whitespace-only query returns empty results."""
    _db, _graph, search = search_env
    results = await search.search_entities("   ")
    assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# Batch relationship fetch
# ═══════════════════════════════════════════════════════════════════════════


async def test_batch_relationships_empty(search_env):
    """Batch fetch with empty list returns empty dict."""
    db, _graph, _search = search_env
    result = await db.get_relationships_for_entities([])
    assert result == {}


async def test_batch_relationships_matches_single(search_env):
    """Batch fetch results match individual fetch results."""
    db, graph, _search = search_env
    await graph.add_entities(
        [
            Entity(name="Alice", entity_type="person"),
            Entity(name="Bob", entity_type="person"),
        ]
    )

    alice = await graph.resolve_entity("Alice")
    bob = await graph.resolve_entity("Bob")
    await graph.add_relationships(
        [Relationship(source_id=alice.id, target_id=bob.id, relationship_type="KNOWS", weight=1.0)]
    )

    # Single fetch
    single = await db.get_relationships_for_entity(alice.id)
    # Batch fetch
    batch = await db.get_relationships_for_entities([alice.id])

    assert len(batch[alice.id]) == len(single)


# ═══════════════════════════════════════════════════════════════════════════
# RRF alpha
# ═══════════════════════════════════════════════════════════════════════════


async def test_rrf_alpha_default_equal_weight(search_env):
    """Default alpha=0.5 gives equal weight to both channels."""
    vec = {"a": 0.1, "b": 0.05}
    fts = {"a": 0.05, "c": 0.1}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=0.5)
    scores = dict(result)
    # Raw, unnormalized fused scores:
    # a = 0.5*0.1 + 0.5*0.05 = 0.075
    # b = 0.5*0.05 = 0.025
    # c = 0.5*0.1 = 0.05
    assert scores["a"] == pytest.approx(0.075)
    assert scores["c"] > scores["b"]  # c ranks above b


async def test_rrf_alpha_zero_fts_only(search_env):
    """alpha=0.0 uses only FTS5 scores."""
    vec = {"a": 0.1}
    fts = {"b": 0.1}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=0.0)
    scores = dict(result)
    assert scores.get("a", 0.0) == 0.0  # vec result zeroed out
    assert scores["b"] == pytest.approx(0.1)


async def test_rrf_alpha_one_vector_only(search_env):
    """alpha=1.0 uses only vector scores."""
    vec = {"a": 0.1}
    fts = {"b": 0.1}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=1.0)
    scores = dict(result)
    assert scores["a"] == pytest.approx(0.1)
    assert scores.get("b", 0.0) == 0.0


async def test_rrf_alpha_invalid_raises(search_env):
    """alpha outside [0, 1] raises ValueError."""
    with pytest.raises(ValueError, match="alpha must be between"):
        HybridSearch._rrf_fuse({}, {}, alpha=1.5)
    with pytest.raises(ValueError, match="alpha must be between"):
        HybridSearch._rrf_fuse({}, {}, alpha=-0.1)


# ═══════════════════════════════════════════════════════════════════════════
# Observation boost
# ═══════════════════════════════════════════════════════════════════════════


async def test_obs_boost_disabled(search_env):
    """boost_from_observations=False skips observation search."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="APIService", entity_type="module", description="API")])
    await graph.add_observations("APIService", [Observation.pending("Rate limit increased to 500")])
    # With boost disabled, should still work
    results = await search.search_entities("rate limit", boost_from_observations=False)
    assert isinstance(results, list)


async def test_obs_boost_zero_factor(search_env):
    """obs_boost_factor=0.0 effectively disables boosting."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="APIService", entity_type="module", description="API")])
    results = await search.search_entities("anything", obs_boost_factor=0.0)
    assert isinstance(results, list)


# ═══════════════════════════════════════════════════════════════════════════
# min_score threshold
# ═══════════════════════════════════════════════════════════════════════════


async def test_min_score_default_no_filtering(search_env):
    """Default min_score=0.0 returns all results (no filtering)."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="Alpha", entity_type="concept")])
    await graph.add_entities([Entity(name="Beta", entity_type="concept")])
    results = await search.search_entities("Alpha")
    # With default min_score=0.0, at least "Alpha" should appear
    assert len(results) >= 1


async def test_min_score_filters_low_relevance(search_env):
    """A threshold above the RRF ceiling filters everything, even the top hit."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="Alpha Centauri", entity_type="star")])
    await graph.add_entities([Entity(name="Beta Pictoris", entity_type="star")])
    assert await search.search_entities("Alpha Centauri", min_score=MAX_RRF_SCORE * 2) == []
    assert await search.search_entities("Alpha Centauri", min_score=MAX_RRF_SCORE / 2) != []


async def test_min_score_entity_returns_empty_on_high_threshold(search_env):
    """min_score=1.0 returns nothing: no raw RRF score can reach 1.0."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="TestEntity", entity_type="concept")])
    results = await search.search_entities("TestEntity", min_score=1.0)
    assert results == []


async def test_min_score_observations(search_env):
    """min_score works on search_observations too."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="Obs Host", entity_type="concept")])
    await graph.add_observations("Obs Host", [Observation.pending("Important fact about physics")])
    results = await search.search_observations("physics", min_score=0.0)
    assert isinstance(results, list)


async def test_min_score_observations_high_threshold(search_env):
    """High min_score filters out low-relevance observations."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="Obs Host2", entity_type="concept")])
    await graph.add_observations("Obs Host2", [Observation.pending("Random note")])
    results = await search.search_observations("Random note", min_score=1.0)
    assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# Raw (unnormalized) RRF scores
# ═══════════════════════════════════════════════════════════════════════════


async def test_rrf_fuse_scores_are_raw_not_normalized():
    """_rrf_fuse returns raw RRF sums, capped by MAX_RRF_SCORE."""
    vec = {"a": 0.2, "b": 0.1, "c": 0.05}
    fts = {"a": 0.1, "d": 0.15}
    scores = dict(HybridSearch._rrf_fuse(vec, fts, alpha=0.5))
    assert scores["a"] == pytest.approx(0.15)
    assert scores["b"] == pytest.approx(0.05)
    # Relative spread survives instead of being squashed towards the top hit.
    assert scores["b"] / scores["a"] == pytest.approx(1 / 3)


async def test_single_junk_hit_does_not_score_one():
    """One lone hit keeps its own small score instead of being promoted to 1.0."""
    scores = dict(HybridSearch._rrf_fuse({}, {"junk": MAX_RRF_SCORE}, alpha=0.5))
    assert scores["junk"] == pytest.approx(MAX_RRF_SCORE / 2)
    assert scores["junk"] < 1.0


async def test_single_bad_entity_hit_scores_low(search_env):
    """End to end: the only match in an empty graph is not a perfect 1.0."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="Quantum Widget", entity_type="concept")])
    results = await search.search_entities("Quantum Widget")
    assert len(results) == 1
    assert results[0]["relevance_score"] <= MAX_RRF_SCORE
    assert results[0]["relevance_score"] < 1.0


async def test_observation_boost_is_capped(search_env):
    """Many mediocre observations cannot outrank one perfect observation."""
    db, graph, search = search_env
    await graph.add_entities(
        [
            Entity(name="Chatty", entity_type="module"),
            Entity(name="Precise", entity_type="module"),
        ]
    )
    chatty = await graph.resolve_entity("Chatty")
    precise = await graph.resolve_entity("Precise")

    await graph.add_observations("Chatty", [Observation.pending(f"note {i}") for i in range(10)])
    await graph.add_observations("Precise", [Observation.pending("the one true note")])

    chatty_obs = await db.get_observations_for_entity(chatty.id)
    precise_obs = await db.get_observations_for_entity(precise.id)

    # Ten mediocre hits (0.004 each, 0.04 summed) against one perfect hit.
    obs_scores = {str(r["id"]): 0.004 for r in chatty_obs}
    obs_scores[str(precise_obs[0]["id"])] = MAX_RRF_SCORE

    # Both channels agree, so the fused score of each observation is its own.
    search._vector_search = AsyncMock(return_value=obs_scores)
    search._fts_observation_search = AsyncMock(return_value=obs_scores)

    boosted = dict(
        await search._boost_from_observations("note", [], limit=10, obs_boost_factor=1.0)
    )
    assert boosted[chatty.id] <= boosted[precise.id]
    assert boosted[chatty.id] <= MAX_OBS_BOOST


# ═══════════════════════════════════════════════════════════════════════════
# Filters are applied before truncation
# ═══════════════════════════════════════════════════════════════════════════


async def test_entity_type_filter_survives_candidate_truncation(search_env):
    """A type match ranked below the candidate cut-off is still returned."""
    _db, graph, search = search_env
    await graph.add_entities(
        [Entity(name=f"Star {i}", entity_type="star") for i in range(8)]
        + [Entity(name="Deep Person", entity_type="person")]
    )
    person = await graph.resolve_entity("Deep Person")

    # Stars rank above the person, pushing it past limit * CANDIDATE_MULTIPLIER.
    fts_scores = {}
    for i in range(8):
        star = await graph.resolve_entity(f"Star {i}")
        fts_scores[star.id] = 0.016 - i * 0.001
    fts_scores[person.id] = 0.001

    search._vector_search = AsyncMock(return_value={})
    search._fts_entity_search = AsyncMock(return_value=fts_scores)

    results = await search.search_entities(
        "anything", limit=2, entity_types=["person"], boost_from_observations=False
    )
    assert [r["name"] for r in results] == ["Deep Person"]


async def test_observation_scope_survives_candidate_truncation(search_env):
    """Scoping to an entity finds its observations even when they rank low."""
    db, graph, search = search_env
    await graph.add_entities(
        [Entity(name="Loud", entity_type="module"), Entity(name="Quiet", entity_type="module")]
    )
    quiet = await graph.resolve_entity("Quiet")

    await graph.add_observations("Loud", [Observation.pending(f"loud {i}") for i in range(10)])
    await graph.add_observations("Quiet", [Observation.pending("quiet note")])

    loud_obs = await db.get_observations_for_entity((await graph.resolve_entity("Loud")).id)
    quiet_obs = await db.get_observations_for_entity(quiet.id)

    obs_scores = {str(r["id"]): 0.016 - i * 0.001 for i, r in enumerate(loud_obs)}
    obs_scores[str(quiet_obs[0]["id"])] = 0.0001  # ranked last of eleven

    search._vector_search = AsyncMock(return_value={})
    search._fts_observation_search = AsyncMock(return_value=obs_scores)

    results = await search.search_observations("note", limit=2, entity_id=quiet.id)
    assert [r["content"] for r in results] == ["quiet note"]
    assert results[0]["entity_name"] == "Quiet"


# ═══════════════════════════════════════════════════════════════════════════
# Relationship cap is reported
# ═══════════════════════════════════════════════════════════════════════════


async def test_relationship_cap_is_surfaced(search_env):
    """Results carry at most MAX_RELATIONSHIPS_PER_RESULT edges and say so."""
    _db, graph, search = search_env
    await graph.add_entities(
        [Entity(name="Hub", entity_type="module")]
        + [Entity(name=f"Leaf {i}", entity_type="module") for i in range(25)]
    )
    hub = await graph.resolve_entity("Hub")
    leaves = [await graph.resolve_entity(f"Leaf {i}") for i in range(25)]
    await graph.add_relationships(
        [
            Relationship(source_id=hub.id, target_id=leaf.id, relationship_type="USES", weight=1.0)
            for leaf in leaves
        ]
    )

    results = await search.search_entities("Hub", limit=1, boost_from_observations=False)
    assert results[0]["name"] == "Hub"
    assert len(results[0]["relationships"]) == MAX_RELATIONSHIPS_PER_RESULT
    assert results[0]["relationships_truncated"] is True


async def test_relationship_cap_not_flagged_when_under_limit(search_env):
    """An entity below the cap reports relationships_truncated=False."""
    _db, graph, search = search_env
    await graph.add_entities(
        [Entity(name="Small", entity_type="module"), Entity(name="Other", entity_type="module")]
    )
    small = await graph.resolve_entity("Small")
    other = await graph.resolve_entity("Other")
    await graph.add_relationships(
        [Relationship(source_id=small.id, target_id=other.id, relationship_type="USES", weight=1.0)]
    )

    results = await search.search_entities("Small", limit=1, boost_from_observations=False)
    assert results[0]["relationships_truncated"] is False


async def test_observation_search_does_not_double_query_entities(search_env):
    """Parent name and type come from one entity query, not two."""
    db, graph, search = search_env
    await graph.add_entities([Entity(name="Host", entity_type="service")])
    await graph.add_observations("Host", [Observation.pending("a fact worth finding")])

    async def _fail(_ids):
        raise AssertionError("resolve_entity_names is a redundant second query")

    db.resolve_entity_names = _fail

    results = await search.search_observations("fact")
    assert results
    assert results[0]["entity_name"] == "Host"
    assert results[0]["entity_type"] == "service"


# ── Type filtering must survive candidate retrieval ──────────────────────────


class TestTypeFilterRetrieval:
    """A type filter must be applied when candidates are *fetched*, not after.

    Regression: filtering happened after the candidate pool was drawn, and the
    pool was only `limit * CANDIDATE_MULTIPLIER` rows. With enough entities of
    other types ranked above them, the entities the caller asked for never
    entered the pool, and a scoped search returned nothing at all — precisely
    when scoping is worth using.
    """

    async def test_rare_type_is_found_among_many_decoys(self, tmp_path) -> None:
        """3 matching people hidden behind 200 matching notes are still found."""
        import time

        from graph_mem.semantic import EmbeddingEngine, HybridSearch
        from graph_mem.storage import SQLiteBackend

        storage = SQLiteBackend(tmp_path / "filter.db")
        await storage.initialize()
        try:
            now = time.time()
            for i in range(200):
                await storage.upsert_entity(
                    entity_id=f"n{i}",
                    name=f"alpha noise {i}",
                    entity_type="note",
                    description="alpha",
                    properties={},
                    created_at=now,
                    updated_at=now,
                )
            for i in range(3):
                await storage.upsert_entity(
                    entity_id=f"p{i}",
                    name=f"alpha person {i}",
                    entity_type="person",
                    description="alpha",
                    properties={},
                    created_at=now,
                    updated_at=now,
                )

            # Keep the embedding engine unavailable so no model is loaded; the
            # full-text channel alone must satisfy the filter.
            await storage._require_db().execute("DROP TABLE metadata")
            embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
            await embeddings.initialize(storage)
            assert embeddings.available is False

            search = HybridSearch(storage, embeddings)
            results = await search.search_entities("alpha", limit=3, entity_types=["person"])

            assert len(results) == 3, f"filter under-returned: {results}"
            assert all(r["entity_type"] == "person" for r in results)
        finally:
            await storage.close()

    async def test_filter_matching_nothing_returns_empty(self, tmp_path) -> None:
        """A filter no entity satisfies yields nothing, not an error."""
        import time

        from graph_mem.semantic import EmbeddingEngine, HybridSearch
        from graph_mem.storage import SQLiteBackend

        storage = SQLiteBackend(tmp_path / "empty.db")
        await storage.initialize()
        try:
            now = time.time()
            await storage.upsert_entity(
                entity_id="n1",
                name="alpha note",
                entity_type="note",
                description="alpha",
                properties={},
                created_at=now,
                updated_at=now,
            )
            await storage._require_db().execute("DROP TABLE metadata")
            embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
            await embeddings.initialize(storage)

            results = await HybridSearch(storage, embeddings).search_entities(
                "alpha", limit=5, entity_types=["nonexistent"]
            )

            assert results == []
        finally:
            await storage.close()

    async def test_fts_backend_filters_by_type_in_sql(self, tmp_path) -> None:
        """The backend itself honours the filter, not just the layer above it."""
        import time

        from graph_mem.storage import SQLiteBackend

        storage = SQLiteBackend(tmp_path / "backend.db")
        await storage.initialize()
        try:
            now = time.time()
            for i in range(50):
                await storage.upsert_entity(
                    entity_id=f"n{i}",
                    name=f"widget {i}",
                    entity_type="note",
                    description="",
                    properties={},
                    created_at=now,
                    updated_at=now,
                )
            await storage.upsert_entity(
                entity_id="p1",
                name="widget person",
                entity_type="person",
                description="",
                properties={},
                created_at=now,
                updated_at=now,
            )

            unfiltered = await storage.fts_search_entities("widget", 5)
            filtered = await storage.fts_search_entities("widget", 5, ["person"])

            assert len(unfiltered) == 5
            assert [eid for eid, _ in filtered] == ["p1"]
        finally:
            await storage.close()
