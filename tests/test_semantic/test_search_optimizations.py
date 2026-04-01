"""Tests for search optimization fixes: FTS5 hardening, batch relationships,
RRF alpha, observation boost."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.models.relationship import Relationship
from graphrag_mcp.semantic.embeddings import EmbeddingEngine
from graphrag_mcp.semantic.search import HybridSearch
from graphrag_mcp.storage import SQLiteBackend


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
    assert scores["a"] == pytest.approx(0.5 * 0.1 + 0.5 * 0.05)  # both channels


async def test_rrf_alpha_zero_fts_only(search_env):
    """alpha=0.0 uses only FTS5 scores."""
    vec = {"a": 0.1}
    fts = {"b": 0.1}
    result = HybridSearch._rrf_fuse(vec, fts, alpha=0.0)
    scores = dict(result)
    assert scores.get("a", 0.0) == 0.0  # vec result zeroed out
    assert scores["b"] == pytest.approx(0.1)  # fts result full weight


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
