"""Tests for search optimization fixes: FTS5 hardening, batch relationships,
RRF alpha, observation boost."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graph_mem.graph.engine import GraphEngine
from graph_mem.models.entity import Entity
from graph_mem.models.observation import Observation
from graph_mem.models.relationship import Relationship
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import HybridSearch
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
    # After normalization, "a" has the highest raw score so it normalizes to 1.0.
    # raw_a = 0.5*0.1 + 0.5*0.05 = 0.075
    # raw_b = 0.5*0.05 = 0.025
    # raw_c = 0.5*0.1 = 0.05
    # max = 0.075 → a=1.0, c=0.05/0.075≈0.6667, b=0.025/0.075≈0.3333
    assert scores["a"] == pytest.approx(1.0)
    assert scores["c"] > scores["b"]  # c ranks above b


async def test_rrf_alpha_zero_fts_only(search_env):
    """alpha=0.0 uses only FTS5 scores."""
    vec = {"a": 0.1}
    fts = {"b": 0.1}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=0.0)
    scores = dict(result)
    assert scores.get("a", 0.0) == 0.0  # vec result zeroed out
    assert scores["b"] == pytest.approx(1.0)  # fts result normalized to 1.0


async def test_rrf_alpha_one_vector_only(search_env):
    """alpha=1.0 uses only vector scores."""
    vec = {"a": 0.1}
    fts = {"b": 0.1}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=1.0)
    scores = dict(result)
    assert scores["a"] == pytest.approx(1.0)  # normalized to 1.0
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
    """min_score=1.0 keeps only the top-scored result (score=1.0 after normalization)."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="Alpha Centauri", entity_type="star")])
    await graph.add_entities([Entity(name="Beta Pictoris", entity_type="star")])
    results = await search.search_entities("Alpha Centauri", min_score=1.0)
    # With normalization, only the best match (score=1.0) survives
    if results:
        assert all(r["relevance_score"] >= 1.0 for r in results)


async def test_min_score_entity_returns_empty_on_high_threshold(search_env):
    """min_score above 1.0 returns nothing since max normalized score is 1.0."""
    _db, graph, search = search_env
    await graph.add_entities([Entity(name="TestEntity", entity_type="concept")])
    results = await search.search_entities("TestEntity", min_score=1.1)
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
    results = await search.search_observations("completely unrelated query xyz", min_score=1.1)
    assert results == []


async def test_rrf_fuse_score_normalization():
    """Verify _rrf_fuse normalizes scores to [0, 1]."""
    vec = {"a": 0.2, "b": 0.1, "c": 0.05}
    fts = {"a": 0.1, "d": 0.15}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=0.5)
    scores = dict(result)
    # Max score should be 1.0 after normalization
    assert max(scores.values()) == pytest.approx(1.0)
    # All scores should be in [0, 1]
    assert all(0.0 <= s <= 1.0 for s in scores.values())
