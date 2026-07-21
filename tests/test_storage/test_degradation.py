"""Graceful-degradation paths when part of the database is unusable.

Search is layered: vector, full-text, and their fusion. Losing one layer should
narrow the results, not fail the request. These tests break a layer for real —
by dropping its table — rather than by mocking, because the defect they guard
against was precisely that the handler could not catch what the real code path
raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio

from graph_mem.semantic import EmbeddingEngine
from graph_mem.storage import SQLiteBackend

if TYPE_CHECKING:
    from pathlib import Path


@pytest_asyncio.fixture
async def backend(tmp_path: Path):
    storage = SQLiteBackend(tmp_path / "degraded.db")
    await storage.initialize()
    yield storage
    await storage.close()


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


async def test_fts_entity_search_degrades_when_the_index_is_gone(backend) -> None:
    """A damaged FTS index yields no results rather than an exception.

    Regression: these handlers listed only ``sqlite3.Error``, but every query
    runs through ``Database.fetch_all``, which wraps SQLite failures in
    ``DatabaseError``. ``DatabaseError`` is not a ``sqlite3.Error``, so the
    handler was unreachable and a damaged index propagated out of search
    instead of degrading to vector-only.
    """
    await backend._require_db().execute("DROP TABLE entities_fts")

    assert await backend.fts_search_entities("anything", 5) == []


async def test_fts_observation_search_degrades_when_the_index_is_gone(backend) -> None:
    """Same for the observation index."""
    await backend._require_db().execute("DROP TABLE observations_fts")

    assert await backend.fts_search_observations("anything", 5) == []


async def test_name_suggestions_degrade_when_the_index_is_gone(backend) -> None:
    """Suggestions are a nicety; losing them must not fail entity resolution."""
    await backend._require_db().execute("DROP TABLE entities_fts")

    assert await backend.fts_suggest_similar("anything") == []


async def test_hybrid_search_survives_losing_both_layers(backend) -> None:
    """End to end: search returns a result set rather than raising.

    Both retrieval layers are disabled — the FTS index is dropped and the
    embedding engine is left unavailable — which is the worst case. The engine
    is deliberately *not* initialised against a working database, so no model
    is loaded: pulling real sentence-transformers weights into a unit test is
    slow, needs network, and leaves threads that crash the interpreter at exit.
    """
    import time

    from graph_mem.semantic import HybridSearch

    await backend.upsert_entity(
        entity_id="e1",
        name="AuthService",
        entity_type="service",
        description="handles login",
        properties={},
        created_at=time.time(),
        updated_at=time.time(),
    )
    await backend._require_db().execute("DROP TABLE entities_fts")
    await backend._require_db().execute("DROP TABLE metadata")

    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
    await embeddings.initialize(backend)
    assert embeddings.available is False, "guard: this test must not load a model"

    results = await HybridSearch(backend, embeddings).search_entities("auth", limit=5)

    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Embedding engine
# ---------------------------------------------------------------------------


async def test_embedding_initialize_never_raises(backend) -> None:
    """``initialize`` documents that it never raises — hold it to that.

    Regression: its handler also listed only ``sqlite3.Error``, so a storage
    failure escaped and took down server start-up instead of disabling
    semantic search.
    """
    await backend._require_db().execute("DROP TABLE metadata")

    engine = EmbeddingEngine(model_name="test", use_onnx=False)
    await engine.initialize(backend)

    assert engine.available is False
