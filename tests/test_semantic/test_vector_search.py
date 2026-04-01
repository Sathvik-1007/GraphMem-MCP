"""Tests for vector search path using mocked embeddings."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.semantic.embeddings import EmbeddingEngine
from graphrag_mcp.semantic.search import HybridSearch
from graphrag_mcp.storage import SQLiteBackend


@pytest_asyncio.fixture
async def vector_env(tmp_path: Path):
    """Set up a storage backend with vector tables for testing."""
    storage = SQLiteBackend(tmp_path / "test_vec.db")
    await storage.initialize()
    graph = GraphEngine(storage)

    # Create an embedding engine with mocked model
    engine = EmbeddingEngine(model_name="test-model", use_onnx=False)
    engine._available = True
    engine._dimension = 4  # Small dimension for testing
    engine._storage = storage

    # Create vector tables via the storage backend
    vec_available = await storage.ensure_vec_tables(4)

    search = HybridSearch(storage, engine)
    yield storage, graph, engine, search, vec_available
    await storage.close()


@pytest.mark.skipif(
    not pytest.importorskip("sqlite_vec", reason="sqlite-vec not installed"),
    reason="sqlite-vec not installed",
)
class TestVectorSearch:
    """Tests for vector search path with mocked embeddings."""

    async def test_vector_search_returns_ranked_results(self, vector_env):
        db, graph, engine, search, vec_available = vector_env
        if not vec_available:
            pytest.skip("sqlite-vec not available")

        # Add entities
        await graph.add_entities(
            [
                Entity(name="Machine Learning", entity_type="concept", description="ML algorithms"),
                Entity(name="Deep Learning", entity_type="concept", description="Neural networks"),
                Entity(name="Cooking", entity_type="concept", description="Making food"),
            ]
        )

        # Resolve entities to get IDs
        ml = await graph.resolve_entity("Machine Learning")
        dl = await graph.resolve_entity("Deep Learning")
        cooking = await graph.resolve_entity("Cooking")

        # Insert fake embeddings — ML and DL are similar, Cooking is different
        ml_vec = [0.9, 0.1, 0.0, 0.0]
        dl_vec = [0.8, 0.2, 0.0, 0.0]
        cooking_vec = [0.0, 0.0, 0.9, 0.1]

        await engine.upsert_entity_embedding(ml.id, ml_vec, db)
        await engine.upsert_entity_embedding(dl.id, dl_vec, db)
        await engine.upsert_entity_embedding(cooking.id, cooking_vec, db)

        # Mock the embed method to return a query vector similar to ML/DL
        query_vec = [0.85, 0.15, 0.0, 0.0]

        async def mock_embed(texts, db=None):
            return [query_vec]

        engine.embed = mock_embed  # type: ignore[assignment]

        results = await search.search_entities("machine learning concepts", limit=3)
        assert len(results) >= 1
        # ML or DL should appear in results
        names = {r["name"] for r in results}
        assert names & {"Machine Learning", "Deep Learning"}

    async def test_upsert_and_delete_entity_embedding(self, vector_env):
        db, graph, engine, _search, vec_available = vector_env
        if not vec_available:
            pytest.skip("sqlite-vec not available")

        await graph.add_entities([Entity(name="TestEntity", entity_type="thing")])
        entity = await graph.resolve_entity("TestEntity")

        # Upsert
        vec = [1.0, 0.0, 0.0, 0.0]
        await engine.upsert_entity_embedding(entity.id, vec, db)

        # Verify exists
        row = await db.fetch_one("SELECT id FROM entity_embeddings WHERE id = ?", (entity.id,))
        assert row is not None

        # Delete
        await engine.delete_entity_embedding(entity.id, db)

        # Verify gone
        row = await db.fetch_one("SELECT id FROM entity_embeddings WHERE id = ?", (entity.id,))
        assert row is None

    async def test_upsert_and_delete_observation_embedding(self, vector_env):
        db, graph, engine, _search, vec_available = vector_env
        if not vec_available:
            pytest.skip("sqlite-vec not available")

        await graph.add_entities([Entity(name="TestEntity", entity_type="thing")])
        obs_results = await graph.add_observations(
            "TestEntity", [Observation.pending("Test observation")]
        )
        obs_id = obs_results[0]["id"]

        # Upsert
        vec = [0.0, 1.0, 0.0, 0.0]
        await engine.upsert_observation_embedding(obs_id, vec, db)

        # Verify exists
        row = await db.fetch_one("SELECT id FROM observation_embeddings WHERE id = ?", (obs_id,))
        assert row is not None

        # Delete
        await engine.delete_observation_embedding(obs_id, db)

        # Verify gone
        row = await db.fetch_one("SELECT id FROM observation_embeddings WHERE id = ?", (obs_id,))
        assert row is None


class TestEmbeddingHelpers:
    """Tests for embedding utility functions."""

    def test_embedding_to_bytes_roundtrip(self):
        """Test that embedding serialization is lossless."""
        from graphrag_mcp.semantic.embeddings import _bytes_to_embedding, _embedding_to_bytes

        original = [1.0, 2.0, 3.0, 4.5]
        blob = _embedding_to_bytes(original)
        restored = _bytes_to_embedding(blob)
        assert len(restored) == len(original)
        for a, b in zip(original, restored, strict=True):
            assert abs(a - b) < 1e-6

    def test_content_hash_deterministic(self):
        """Test that content hashing is deterministic."""
        from graphrag_mcp.semantic.embeddings import _content_hash

        h1 = _content_hash("hello world")
        h2 = _content_hash("hello world")
        h3 = _content_hash("different text")
        assert h1 == h2
        assert h1 != h3

    def test_embedding_engine_not_available_by_default(self):
        """Test that engine starts as unavailable."""
        engine = EmbeddingEngine(model_name="test")
        assert engine.available is False
        assert engine.model_name == "test"

    def test_embedding_engine_dimension_raises_before_init(self):
        """Test that accessing dimension before init raises."""
        from graphrag_mcp.utils.errors import EmbeddingError

        engine = EmbeddingEngine()
        with pytest.raises(EmbeddingError, match="not initialized"):
            _ = engine.dimension

    async def test_embed_raises_when_unavailable(self):
        """Test that embed raises when engine is not available."""
        from graphrag_mcp.utils.errors import EmbeddingError

        engine = EmbeddingEngine()
        with pytest.raises(EmbeddingError, match="not available"):
            await engine.embed(["test"])


class TestVectorSearchFallback:
    """Tests for vector search graceful degradation when embeddings unavailable."""

    async def test_vector_search_empty_when_unavailable(self, tmp_path: Path):
        """When embeddings are unavailable, _vector_search returns empty dict."""
        storage = SQLiteBackend(tmp_path / "test_fallback.db")
        await storage.initialize()

        engine = EmbeddingEngine(model_name="test", use_onnx=False)
        # engine._available remains False
        search = HybridSearch(storage, engine)

        result = await search._vector_search("query", "entity_embeddings", 10)
        assert result == {}
        await storage.close()
