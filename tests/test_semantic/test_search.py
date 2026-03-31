"""Tests for HybridSearch — FTS5 path only (no embedding model)."""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from graphrag_mcp.db.connection import Database
from graphrag_mcp.db.schema import run_migrations
from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.semantic.embeddings import EmbeddingEngine
from graphrag_mcp.semantic.search import HybridSearch


@pytest_asyncio.fixture
async def search_env(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await run_migrations(db)
    graph = GraphEngine(db)
    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
    search = HybridSearch(db, embeddings)
    yield db, graph, search
    await db.close()


async def test_search_entities_empty(search_env):
    db, graph, search = search_env
    results = await search.search_entities("anything")
    assert results == []


async def test_search_entities_fts_match(search_env):
    db, graph, search = search_env
    await graph.add_entities(
        [
            Entity(
                name="Quantum Computing",
                entity_type="concept",
                description="A computing paradigm using qubits",
            ),
            Entity(
                name="Classical Computing",
                entity_type="concept",
                description="Traditional binary computing",
            ),
        ]
    )

    results = await search.search_entities("Quantum Computing")
    assert len(results) >= 1
    names = {r["name"] for r in results}
    assert "Quantum Computing" in names


async def test_search_entities_with_type_filter(search_env):
    db, graph, search = search_env
    await graph.add_entities(
        [
            Entity(name="Alice", entity_type="person"),
            Entity(name="Alice Springs", entity_type="place"),
        ]
    )

    results = await search.search_entities("Alice", entity_types=["person"])
    for r in results:
        assert r["entity_type"] == "person"


async def test_search_entities_includes_relationships(search_env):
    db, graph, search = search_env
    await graph.add_entities(
        [
            Entity(name="Alice", entity_type="person"),
            Entity(name="Bob", entity_type="person"),
        ]
    )
    from graphrag_mcp.models.relationship import Relationship

    alice = await graph.resolve_entity("Alice")
    bob = await graph.resolve_entity("Bob")
    await graph.add_relationships(
        [
            Relationship(source_id=alice.id, target_id=bob.id, relationship_type="knows"),
        ]
    )

    results = await search.search_entities("Alice")
    assert len(results) >= 1
    alice_result = next(r for r in results if r["name"] == "Alice")
    assert "relationships" in alice_result
    assert len(alice_result["relationships"]) >= 1


async def test_search_entities_with_observations(search_env):
    db, graph, search = search_env
    await graph.add_entities(
        [
            Entity(name="Alice", entity_type="person"),
        ]
    )
    await graph.add_observations("Alice", [Observation.pending("Likes tea")])

    results = await search.search_entities("Alice", include_observations=True)
    assert len(results) >= 1
    alice_result = next(r for r in results if r["name"] == "Alice")
    assert "observations" in alice_result
    assert len(alice_result["observations"]) >= 1


async def test_search_observations_empty(search_env):
    db, graph, search = search_env
    results = await search.search_observations("anything")
    assert results == []


async def test_search_observations_fts_match(search_env):
    db, graph, search = search_env
    await graph.add_entities([Entity(name="Alice", entity_type="person")])
    await graph.add_observations(
        "Alice",
        [
            Observation.pending("Speaks fluent French"),
            Observation.pending("Born in London"),
        ],
    )

    results = await search.search_observations("French")
    assert len(results) >= 1
    assert any("French" in str(r["content"]) for r in results)


async def test_search_observations_entity_filter(search_env):
    db, graph, search = search_env
    await graph.add_entities(
        [
            Entity(name="Alice", entity_type="person"),
            Entity(name="Bob", entity_type="person"),
        ]
    )
    alice = await graph.resolve_entity("Alice")
    _bob = await graph.resolve_entity("Bob")  # resolved to populate DB
    await graph.add_observations("Alice", [Observation.pending("Likes tea")])
    await graph.add_observations("Bob", [Observation.pending("Likes coffee")])

    results = await search.search_observations("Likes", entity_id=alice.id)
    # Should only return Alice's observation
    for r in results:
        assert r["entity_id"] == alice.id
