"""Unit tests for EntityMerger — entity merge and deduplication."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

from graph_mem.graph.engine import GraphEngine
from graph_mem.graph.merge import EntityMerger
from graph_mem.models.entity import Entity
from graph_mem.models.observation import Observation
from graph_mem.models.relationship import Relationship
from graph_mem.storage import SQLiteBackend
from graph_mem.utils.errors import EntityError


@pytest_asyncio.fixture
async def db_and_engine(tmp_path: Path):
    storage = SQLiteBackend(tmp_path / "test.db")
    await storage.initialize()
    graph = GraphEngine(storage)
    merger = EntityMerger(storage)
    yield storage, graph, merger
    await storage.close()


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
    _db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Alice", description="Original")
    source_id = await _create_entity(graph, "Alice Clone", description="Clone desc")

    result = await merger.merge(target_id, source_id)
    assert result["target_id"] == target_id
    assert result["source_id"] == source_id
    assert result["target_name"] == "Alice"
    assert result["source_name"] == "Alice Clone"


async def test_merge_self_raises(db_and_engine):
    _db, graph, merger = db_and_engine
    eid = await _create_entity(graph, "Solo")
    with pytest.raises(EntityError, match="Cannot merge"):
        await merger.merge(eid, eid)


async def test_merge_moves_observations(db_and_engine):
    _db, graph, merger = db_and_engine
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
    _db, graph, merger = db_and_engine
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
    _db, graph, merger = db_and_engine
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
    _db, graph, merger = db_and_engine
    source_id = await _create_entity(graph, "Source")
    with pytest.raises(EntityError, match="not found"):
        await merger.merge("nonexistent_id", source_id)


async def test_merge_nonexistent_source(db_and_engine):
    _db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target")
    with pytest.raises(EntityError, match="not found"):
        await merger.merge(target_id, "nonexistent_id")


async def test_merge_description_append(db_and_engine):
    _db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Target", description="First")
    source_id = await _create_entity(graph, "Source", description="Second")

    await merger.merge(target_id, source_id)

    entity = await graph.get_entity_by_id(target_id)
    assert "First" in entity.description
    assert "Second" in entity.description


# ── Merging entities that reference each other ───────────────────────────────


async def _relate(
    graph: GraphEngine,
    source_id: str,
    target_id: str,
    rel_type: str = "knows",
    weight: float = 1.0,
    properties: dict | None = None,
) -> str:
    results = await graph.add_relationships(
        [
            Relationship(
                source_id=source_id,
                target_id=target_id,
                relationship_type=rel_type,
                weight=weight,
                properties=properties or {},
            )
        ]
    )
    return str(results[0]["id"])


async def test_merge_forward_edge_between_pair_leaves_no_self_loop(db_and_engine):
    """Merging A into B when A->B exists must not produce B->B.

    Regression: the source endpoint was repointed to the target while the
    target endpoint was already the target, rewriting the edge into a self
    loop. Duplicates usually *are* linked to each other, so this was the
    common merge case rather than an exotic one.
    """
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Bob")
    source_id = await _create_entity(graph, "Bobby")
    await _relate(graph, source_id, target_id, "alias_of")

    await merger.merge(target_id, source_id)

    rows = await db.fetch_all("SELECT source_id, target_id FROM relationships")
    assert not [r for r in rows if r["source_id"] == r["target_id"]], (
        f"merge created a self-loop: {rows}"
    )
    assert rows == []


async def test_merge_reverse_edge_between_pair_leaves_no_self_loop(db_and_engine):
    """Same, with the edge pointing the other way (B->A)."""
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Bob")
    source_id = await _create_entity(graph, "Bobby")
    await _relate(graph, target_id, source_id, "alias_of")

    await merger.merge(target_id, source_id)

    rows = await db.fetch_all("SELECT source_id, target_id FROM relationships")
    assert not [r for r in rows if r["source_id"] == r["target_id"]]
    assert rows == []


async def test_merge_preexisting_self_loop_on_source_is_removed(db_and_engine):
    """A source entity related to itself does not survive the merge as B->B."""
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Bob")
    source_id = await _create_entity(graph, "Bobby")
    await _relate(graph, source_id, source_id, "relates_to")

    await merger.merge(target_id, source_id)

    rows = await db.fetch_all("SELECT source_id, target_id FROM relationships")
    assert rows == []


async def test_merge_keeps_unrelated_edges_and_repoints_them(db_and_engine):
    """Edges to third parties survive, repointed at the surviving entity."""
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Bob")
    source_id = await _create_entity(graph, "Bobby")
    other_id = await _create_entity(graph, "Carol")
    await _relate(graph, source_id, other_id, "knows")

    result = await merger.merge(target_id, source_id)

    assert result["redirected_relationships"] == 1
    rows = await db.fetch_all("SELECT source_id, target_id FROM relationships")
    assert len(rows) == 1
    assert str(rows[0]["source_id"]) == target_id
    assert str(rows[0]["target_id"]) == other_id


async def test_merge_duplicate_edge_unions_properties_and_keeps_max_weight(db_and_engine):
    """Combining duplicate edges keeps both edges' properties, not just one's.

    Regression: the losing edge's properties were dropped entirely, which
    contradicted upsert_relationship, where the same collision merges them.
    """
    db, graph, merger = db_and_engine
    target_id = await _create_entity(graph, "Bob")
    source_id = await _create_entity(graph, "Bobby")
    other_id = await _create_entity(graph, "Carol")

    await _relate(graph, target_id, other_id, "knows", weight=0.4, properties={"since": "2020"})
    await _relate(graph, source_id, other_id, "knows", weight=0.9, properties={"via": "work"})

    result = await merger.merge(target_id, source_id)

    assert result["removed_duplicate_relationships"] == 1
    rows = await db.fetch_all("SELECT weight, properties FROM relationships")
    assert len(rows) == 1
    assert float(rows[0]["weight"]) == 0.9
    import json as _json

    props = _json.loads(str(rows[0]["properties"]))
    assert props == {"since": "2020", "via": "work"}
