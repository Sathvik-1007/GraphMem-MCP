"""Unit tests for EntityMerger — entity merge and deduplication."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from graphrag_mcp.db.connection import Database
from graphrag_mcp.db.schema import run_migrations
from graphrag_mcp.graph.engine import GraphEngine
from graphrag_mcp.graph.merge import EntityMerger
from graphrag_mcp.models.entity import Entity
from graphrag_mcp.models.observation import Observation
from graphrag_mcp.models.relationship import Relationship
from graphrag_mcp.utils.errors import EntityError


@pytest_asyncio.fixture
async def db_and_engine(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await run_migrations(db)
    graph = GraphEngine(db)
    merger = EntityMerger(db)
    yield db, graph, merger
    await db.close()


async def _create_entity(graph: GraphEngine, name: str, **kwargs) -> str:
    results = await graph.add_entities(
        [
            Entity(
                name=name,
                entity_type=kwargs.get("entity_type", "person"),
                description=kwargs.get("description", ""),
            )
        ]
    )
    return str(results[0]["id"])


async def test_merge_basic(db_and_engine):
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Alice", description="Original")
    source_id = await _create_entity(graph, "Alice Clone", description="Clone desc")

    result = await merger.merge(target_id, source_id)
    assert result["target_id"] == target_id
    assert result["source_id"] == source_id
    assert result["target_name"] == "Alice"
    assert result["source_name"] == "Alice Clone"


async def test_merge_self_raises(db_and_engine):
    db, graph, merger = db_and_engine
    eid = await _create_entity(graph, "Solo")
    with pytest.raises(EntityError, match="Cannot merge"):
        await merger.merge(eid, eid)


async def test_merge_moves_observations(db_and_engine):
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target")
    source_id = await _create_entity(graph, "Source")

    await graph.add_observations(
        "Source", [Observation.pending("Fact A"), Observation.pending("Fact B")]
    )

    result = await merger.merge(target_id, source_id)
    assert result["moved_observations"] == 2

    # Observations should now belong to target
    obs = await graph.get_observations("Target")
    assert len(obs) == 2


async def test_merge_redirects_relationships(db_and_engine):
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target")
    source_id = await _create_entity(graph, "Source")
    other_id = await _create_entity(graph, "Other")

    await graph.add_relationships(
        [
            Relationship(source_id=source_id, target_id=other_id, relationship_type="knows"),
        ]
    )

    result = await merger.merge(target_id, source_id)
    assert result["redirected_relationships"] == 1

    # Relationship should now be from Target -> Other
    rels = await graph.get_relationships("Target", direction="outgoing")
    assert len(rels) >= 1
    assert any(r["target_name"] == "Other" for r in rels)


async def test_merge_deduplicates_relationships(db_and_engine):
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target")
    source_id = await _create_entity(graph, "Source")
    other_id = await _create_entity(graph, "Other")

    # Both target and source have a "knows" relationship to Other
    await graph.add_relationships(
        [
            Relationship(
                source_id=target_id, target_id=other_id, relationship_type="knows", weight=0.5
            ),
            Relationship(
                source_id=source_id, target_id=other_id, relationship_type="knows", weight=0.9
            ),
        ]
    )

    result = await merger.merge(target_id, source_id)
    assert result["removed_duplicate_relationships"] == 1

    # Should keep higher weight
    rels = await graph.get_relationships("Target", direction="outgoing")
    knows_rels = [r for r in rels if r["relationship_type"] == "knows"]
    assert len(knows_rels) == 1
    assert knows_rels[0]["weight"] == 0.9


async def test_merge_nonexistent_target(db_and_engine):
    db, graph, merger = db_and_engine
    source_id = await _create_entity(graph, "Source")
    with pytest.raises(EntityError, match="not found"):
        await merger.merge("nonexistent_id", source_id)


async def test_merge_nonexistent_source(db_and_engine):
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target")
    with pytest.raises(EntityError, match="not found"):
        await merger.merge(target_id, "nonexistent_id")


async def test_merge_description_append(db_and_engine):
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target", description="First")
    source_id = await _create_entity(graph, "Source", description="Second")

    await merger.merge(target_id, source_id)

    entity = await graph.get_entity_by_id(target_id)
    assert "First" in entity.description
    assert "Second" in entity.description
