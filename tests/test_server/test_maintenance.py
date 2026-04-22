"""Tests for maintenance tools — graph_health, compact_observations, suggest_connections."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

import graph_mem.server as server_mod
from graph_mem.graph.engine import GraphEngine
from graph_mem.graph.merge import EntityMerger
from graph_mem.graph.traversal import GraphTraversal
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import HybridSearch
from graph_mem.server import (
    add_entities,
    add_observations,
    add_relationships,
    audit_graph,
    compact_observations,
    get_entity,
    graph_health,
    suggest_connections,
)
from graph_mem.utils.config import Config

# ---------------------------------------------------------------------------
# Fixture — same pattern as test_tools.py
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def setup_server(tmp_path: Path):
    """Populate the module-level _state so tool functions work."""
    from graph_mem.storage import SQLiteBackend

    db_path = tmp_path / "test.db"
    storage = SQLiteBackend(db_path)
    await storage.initialize()

    embeddings = EmbeddingEngine(model_name="test", use_onnx=False)
    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings)

    server_mod._state.storage = storage
    server_mod._state.graph = graph
    server_mod._state.traversal = traversal
    server_mod._state.merger = merger
    server_mod._state.embeddings = embeddings
    server_mod._state.search = search
    server_mod._state.config = Config(db_path=db_path)

    yield

    await storage.close()
    server_mod._state.storage = None
    server_mod._state.graph = None
    server_mod._state.traversal = None
    server_mod._state.merger = None
    server_mod._state.embeddings = None
    server_mod._state.search = None
    server_mod._state.config = None


# ===========================================================================
# graph_health
# ===========================================================================


class TestGraphHealth:
    @pytest.mark.asyncio
    async def test_empty_graph(self, setup_server):
        """Health check on empty graph returns zero counts."""
        result = await graph_health()
        assert "error" not in result
        assert result["counts"]["entities"] == 0
        assert result["counts"]["relationships"] == 0
        assert result["counts"]["observations"] == 0
        assert result["observation_hotspots"] == []
        assert result["missing_descriptions"] == []

    @pytest.mark.asyncio
    async def test_with_entities(self, setup_server):
        """Health check returns correct counts after adding entities."""
        await add_entities(
            [
                {"name": "Alice", "entity_type": "person", "description": "A developer"},
                {"name": "Bob", "entity_type": "person"},  # no description
            ]
        )
        result = await graph_health()
        assert result["counts"]["entities"] == 2
        assert result["missing_description_count"] >= 1  # Bob has no description
        assert len(result["missing_descriptions"]) >= 1

    @pytest.mark.asyncio
    async def test_hotspots_with_observations(self, setup_server):
        """Entities with many observations appear in hotspots."""
        await add_entities(
            [
                {"name": "HotEntity", "entity_type": "concept", "description": "test"},
            ]
        )
        # Add 20 observations
        obs_texts = [f"Observation number {i}" for i in range(20)]
        await add_observations("HotEntity", obs_texts)

        result = await graph_health()
        assert len(result["observation_hotspots"]) >= 1
        hotspot = result["observation_hotspots"][0]
        assert hotspot["name"] == "HotEntity"
        assert hotspot["observation_count"] == 20

    @pytest.mark.asyncio
    async def test_suggested_actions(self, setup_server):
        """Health check suggests compaction for entities with many observations."""
        await add_entities(
            [
                {"name": "BigEntity", "entity_type": "concept", "description": "test"},
            ]
        )
        obs_texts = [f"Fact {i}" for i in range(20)]
        await add_observations("BigEntity", obs_texts)

        result = await graph_health()
        actions = result["suggested_actions"]
        # Should suggest compaction
        assert any("compact" in a.lower() for a in actions)

    @pytest.mark.asyncio
    async def test_entity_type_distribution_capped(self, setup_server):
        """Type distribution is capped at top 10."""
        # Create entities of 12 different types
        entities = [
            {"name": f"E{i}", "entity_type": f"type_{i}", "description": f"desc {i}"}
            for i in range(12)
        ]
        await add_entities(entities)

        result = await graph_health()
        assert len(result["entity_types"]) <= 10


# ===========================================================================
# compact_observations
# ===========================================================================


class TestCompactObservations:
    @pytest.mark.asyncio
    async def test_basic_compaction(self, setup_server):
        """Compact: keep some, delete others, add new."""
        await add_entities(
            [
                {"name": "Target", "entity_type": "concept", "description": "test"},
            ]
        )
        await add_observations(
            "Target",
            [
                "Fact A — original",
                "Fact B — original",
                "Fact C — original",
                "Fact D — original",
            ],
        )

        # Get observation IDs
        entity = await get_entity("Target")
        obs = entity["observations"]
        assert len(obs) == 4

        # Keep first two, merge last two into one
        keep_ids = [obs[0]["id"], obs[1]["id"]]

        result = await compact_observations(
            "Target",
            keep_ids=keep_ids,
            new_observations=["Merged: C and D combined"],
        )

        assert "error" not in result
        assert result["before"] == 4
        assert result["deleted"] == 2
        assert result["kept"] == 2
        assert result["added"] == 1
        assert result["after"] == 3
        assert result["status"] == "compacted"

        # Verify actual state
        entity_after = await get_entity("Target")
        assert len(entity_after["observations"]) == 3

    @pytest.mark.asyncio
    async def test_compact_delete_all_add_new(self, setup_server):
        """Compact: delete everything, add summaries."""
        await add_entities(
            [
                {"name": "Total", "entity_type": "concept", "description": "test"},
            ]
        )
        await add_observations("Total", ["Old 1", "Old 2", "Old 3"])

        result = await compact_observations(
            "Total",
            keep_ids=[],  # keep nothing
            new_observations=["Summary of all old observations"],
        )

        assert result["deleted"] == 3
        assert result["kept"] == 0
        assert result["added"] == 1
        assert result["after"] == 1

    @pytest.mark.asyncio
    async def test_compact_invalid_keep_ids(self, setup_server):
        """Error when keep_ids reference non-existent observations."""
        await add_entities(
            [
                {"name": "BadKeep", "entity_type": "concept", "description": "test"},
            ]
        )
        await add_observations("BadKeep", ["A fact"])

        result = await compact_observations(
            "BadKeep",
            keep_ids=["nonexistent-id-123"],
            new_observations=["New stuff"],
        )

        assert result.get("error") is True
        assert "not found" in result.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_compact_nonexistent_entity(self, setup_server):
        """Error when entity doesn't exist."""
        result = await compact_observations(
            "NoSuchEntity",
            keep_ids=[],
            new_observations=["New stuff"],
        )
        assert result.get("error") is True

    @pytest.mark.asyncio
    async def test_compact_keep_all(self, setup_server):
        """Compact: keep all, add some new ones."""
        await add_entities(
            [
                {"name": "KeepAll", "entity_type": "concept", "description": "test"},
            ]
        )
        await add_observations("KeepAll", ["Fact X", "Fact Y"])

        entity = await get_entity("KeepAll")
        all_ids = [o["id"] for o in entity["observations"]]

        result = await compact_observations(
            "KeepAll",
            keep_ids=all_ids,
            new_observations=["Extra fact"],
        )

        assert result["deleted"] == 0
        assert result["kept"] == 2
        assert result["added"] == 1
        assert result["after"] == 3


# ===========================================================================
# suggest_connections
# ===========================================================================


class TestSuggestConnections:
    @pytest.mark.asyncio
    async def test_basic_suggestions(self, setup_server):
        """Suggests semantically related entities."""
        await add_entities(
            [
                {
                    "name": "AuthService",
                    "entity_type": "module",
                    "description": "Handles authentication",
                },
                {
                    "name": "JWTLibrary",
                    "entity_type": "dependency",
                    "description": "JSON Web Token library",
                },
                {
                    "name": "UserModel",
                    "entity_type": "class",
                    "description": "Database model for users",
                },
                {
                    "name": "WeatherAPI",
                    "entity_type": "api",
                    "description": "External weather service",
                },
            ]
        )

        result = await suggest_connections("AuthService")
        assert "error" not in result
        assert result["entity"] == "AuthService"
        assert isinstance(result["suggestions"], list)
        # All should be unconnected (no relationships yet)
        assert result["already_connected_count"] == 0

    @pytest.mark.asyncio
    async def test_shows_already_connected(self, setup_server):
        """Already-connected entities are flagged."""
        await add_entities(
            [
                {"name": "ServiceA", "entity_type": "module", "description": "Service A"},
                {"name": "ServiceB", "entity_type": "module", "description": "Service B"},
                {"name": "ServiceC", "entity_type": "module", "description": "Service C"},
            ]
        )
        await add_relationships(
            [
                {"source": "ServiceA", "target": "ServiceB", "relationship_type": "DEPENDS_ON"},
            ]
        )

        result = await suggest_connections("ServiceA")
        assert "error" not in result

        # ServiceB should show as already_connected
        b_suggestion = next(
            (s for s in result["suggestions"] if s["name"] == "ServiceB"),
            None,
        )
        if b_suggestion:
            assert b_suggestion["already_connected"] is True

    @pytest.mark.asyncio
    async def test_nonexistent_entity(self, setup_server):
        """Error when entity doesn't exist."""
        result = await suggest_connections("NoSuchEntity")
        assert result.get("error") is True

    @pytest.mark.asyncio
    async def test_empty_graph(self, setup_server):
        """suggest_connections on entity in empty graph returns no suggestions."""
        await add_entities(
            [
                {"name": "Lonely", "entity_type": "concept", "description": "All alone"},
            ]
        )
        result = await suggest_connections("Lonely")
        assert "error" not in result
        assert result["suggestions"] == []  # no other entities to suggest

    @pytest.mark.asyncio
    async def test_limit_parameter(self, setup_server):
        """Respects the limit parameter."""
        entities = [
            {"name": f"Node{i}", "entity_type": "concept", "description": f"Concept number {i}"}
            for i in range(10)
        ]
        await add_entities(entities)

        result = await suggest_connections("Node0", limit=3)
        assert "error" not in result
        assert len(result["suggestions"]) <= 3


# ---------------------------------------------------------------------------
# audit_graph tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_graph_empty(setup_server):
    """Audit on empty graph returns the empty message."""
    result = await audit_graph()
    assert isinstance(result, str)
    assert "empty" in result.lower()


@pytest.mark.asyncio
async def test_audit_graph_finds_disconnected(setup_server):
    """Entities with no relationships flagged as disconnected."""
    await add_entities(
        [
            {"name": "Lonely", "entity_type": "person"},
            {"name": "Also Lonely", "entity_type": "place"},
        ]
    )
    result = await audit_graph()
    assert isinstance(result, str)
    assert "DISCONNECTED" in result
    assert "Lonely" in result
    assert "Also Lonely" in result
    assert "2/2" in result  # both disconnected


@pytest.mark.asyncio
async def test_audit_graph_finds_missing_descriptions(setup_server):
    """Entities with empty descriptions flagged."""
    await add_entities(
        [
            {"name": "NoDesc", "entity_type": "person"},
            {"name": "HasDesc", "entity_type": "person", "description": "A person"},
        ]
    )
    result = await audit_graph()
    assert "MISSING DESCRIPTIONS" in result
    assert "NoDesc" in result


@pytest.mark.asyncio
async def test_audit_graph_finds_missing_observations(setup_server):
    """Entities with no observations flagged."""
    await add_entities(
        [
            {"name": "NoObs", "entity_type": "concept"},
            {"name": "HasObs", "entity_type": "concept", "observations": ["A fact"]},
        ]
    )
    result = await audit_graph()
    assert "MISSING OBSERVATIONS" in result
    assert "NoObs" in result


@pytest.mark.asyncio
async def test_audit_graph_connected_entities_not_disconnected(setup_server):
    """Connected entities do NOT appear in disconnected list."""
    await add_entities(
        [
            {"name": "Alice", "entity_type": "person"},
            {"name": "Bob", "entity_type": "person"},
        ]
    )
    await add_relationships(
        [
            {"source": "Alice", "target": "Bob", "relationship_type": "knows"},
        ]
    )
    result = await audit_graph()
    assert "DISCONNECTED ENTITIES (0/" in result


@pytest.mark.asyncio
async def test_audit_graph_quality_score(setup_server):
    """Quality score present and is a percentage."""
    await add_entities(
        [
            {
                "name": "Full",
                "entity_type": "person",
                "description": "Desc",
                "properties": {"key": "val"},
                "observations": ["obs1"],
            },
        ]
    )
    result = await audit_graph()
    assert "Quality Score:" in result
    assert "%" in result


@pytest.mark.asyncio
async def test_audit_graph_returns_string_not_dict(setup_server):
    """Audit must return plain text, never a dict."""
    await add_entities([{"name": "X", "entity_type": "t"}])
    result = await audit_graph()
    assert isinstance(result, str)
    assert not isinstance(result, dict)


# ---------------------------------------------------------------------------
# add_entities batch observation optimization tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_entities_batch_observations(setup_server):
    """Multiple entities with observations — all observations stored correctly."""
    result = await add_entities(
        [
            {"name": "E1", "entity_type": "t", "observations": ["obs1a", "obs1b"]},
            {"name": "E2", "entity_type": "t", "observations": ["obs2a"]},
            {"name": "E3", "entity_type": "t"},  # no observations
        ]
    )
    assert result["count"] == 3

    e1 = await get_entity("E1")
    assert len(e1["observations"]) == 2

    e2 = await get_entity("E2")
    assert len(e2["observations"]) == 1

    e3 = await get_entity("E3")
    assert len(e3["observations"]) == 0


# ---------------------------------------------------------------------------
# add_relationships cached resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_relationships_cached_resolution(setup_server):
    """Same entity name referenced multiple times — still works (cache hit)."""
    await add_entities(
        [
            {"name": "Hub", "entity_type": "person"},
            {"name": "Spoke1", "entity_type": "person"},
            {"name": "Spoke2", "entity_type": "person"},
            {"name": "Spoke3", "entity_type": "person"},
        ]
    )
    # Hub referenced 3x as source — should resolve from cache after first
    result = await add_relationships(
        [
            {"source": "Hub", "target": "Spoke1", "relationship_type": "knows"},
            {"source": "Hub", "target": "Spoke2", "relationship_type": "knows"},
            {"source": "Hub", "target": "Spoke3", "relationship_type": "knows"},
        ]
    )
    assert result["count"] == 3
    for r in result["results"]:
        assert r["source"] == "Hub"
