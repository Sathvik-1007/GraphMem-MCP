"""Name resolution and degree counting.

Both are cases where the previous SQL returned *a* correct-looking answer
without guaranteeing it was the same answer twice, or without using an index.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import pytest_asyncio

from graph_mem.graph.engine import GraphEngine
from graph_mem.storage import SQLiteBackend

if TYPE_CHECKING:
    from pathlib import Path


@pytest_asyncio.fixture
async def backend(tmp_path: Path):
    storage = SQLiteBackend(tmp_path / "names.db")
    await storage.initialize()
    yield storage
    await storage.close()


async def _add(storage: SQLiteBackend, name: str, entity_type: str, updated_at: float) -> str:
    entity_id = f"id-{name}-{entity_type}"
    await storage.upsert_entity(
        entity_id=entity_id,
        name=name,
        entity_type=entity_type,
        description="",
        properties={},
        created_at=updated_at,
        updated_at=updated_at,
    )
    return entity_id


# ---------------------------------------------------------------------------
# Deterministic resolution
# ---------------------------------------------------------------------------


async def test_shared_name_resolves_to_the_same_entity_every_time(backend) -> None:
    """A name used by several types must not resolve differently per call.

    Regression: the lookup had no ORDER BY, so which row came back depended on
    the query plan. resolve_entity feeds add_observations, update_entity, and
    delete_entities, so a shifting answer means observations landing on the
    wrong entity.
    """
    now = time.time()
    await _add(backend, "Mercury", "planet", now - 100)
    newest = await _add(backend, "Mercury", "project", now)
    await _add(backend, "Mercury", "element", now - 50)

    seen = {(await backend.get_entity_by_name("Mercury"))["id"] for _ in range(10)}

    assert seen == {newest}, "resolution is not deterministic"


async def test_shared_name_resolves_to_the_most_recently_updated(backend) -> None:
    """Of several candidates, the freshest is the useful default."""
    now = time.time()
    await _add(backend, "Mercury", "planet", now - 100)
    recent = await _add(backend, "Mercury", "project", now)

    row = await backend.get_entity_by_name("Mercury")

    assert row is not None
    assert row["id"] == recent


async def test_entity_type_still_narrows_a_shared_name(backend) -> None:
    """Passing the type resolves exactly, regardless of recency."""
    now = time.time()
    planet = await _add(backend, "Mercury", "planet", now - 100)
    await _add(backend, "Mercury", "project", now)

    row = await backend.get_entity_by_name("Mercury", "planet")

    assert row is not None
    assert row["id"] == planet


async def test_nocase_lookup_is_deterministic(backend) -> None:
    """A case-insensitive match can hit several rows; it must still be stable."""
    now = time.time()
    await _add(backend, "mercury", "planet", now - 100)
    newest = await _add(backend, "MERCURY", "project", now)

    seen = {(await backend.get_entity_by_name_nocase("Mercury"))["id"] for _ in range(10)}

    assert seen == {newest}


async def test_count_entities_by_name_reports_ambiguity(backend) -> None:
    """Callers can distinguish 'the only match' from 'one of several'."""
    now = time.time()
    await _add(backend, "Mercury", "planet", now)
    await _add(backend, "Mercury", "project", now)
    await _add(backend, "Venus", "planet", now)

    assert await backend.count_entities_by_name("Mercury") == 2
    assert await backend.count_entities_by_name("Venus") == 1
    assert await backend.count_entities_by_name("Pluto") == 0


async def test_ambiguous_resolution_is_logged(backend, caplog) -> None:
    """An ambiguous name resolves, but does not resolve silently."""
    now = time.time()
    await _add(backend, "Mercury", "planet", now - 10)
    await _add(backend, "Mercury", "project", now)

    engine = GraphEngine(backend)
    with caplog.at_level(logging.WARNING):
        resolved = await engine.resolve_entity("Mercury")

    assert resolved.name == "Mercury"
    assert any("matches 2 entities" in record.getMessage() for record in caplog.records)


async def test_unambiguous_resolution_is_not_logged(backend, caplog) -> None:
    """The common case stays quiet — a warning per lookup would be noise."""
    await _add(backend, "Venus", "planet", time.time())

    engine = GraphEngine(backend)
    with caplog.at_level(logging.WARNING):
        await engine.resolve_entity("Venus")

    assert not [r for r in caplog.records if "matches" in r.getMessage()]


# ---------------------------------------------------------------------------
# Degree counting
# ---------------------------------------------------------------------------


async def test_degree_counts_both_endpoints(backend) -> None:
    """An edge contributes to the degree of each entity it joins."""
    now = time.time()
    hub = await _add(backend, "Hub", "concept", now)
    spokes = [await _add(backend, f"Spoke{i}", "concept", now) for i in range(3)]
    for spoke in spokes:
        await backend.upsert_relationship(
            rel_id=f"r-{spoke}",
            source_id=hub,
            target_id=spoke,
            relationship_type="links",
            weight=1.0,
            properties={},
            created_at=now,
            updated_at=now,
        )

    rows = await backend.most_connected_entities(limit=10)
    degrees = {str(r["name"]): int(r["degree"]) for r in rows}

    assert degrees["Hub"] == 3
    assert degrees["Spoke0"] == 1


async def test_isolated_entities_have_degree_zero(backend) -> None:
    """An entity with no edges appears with degree 0, not missing."""
    await _add(backend, "Lonely", "concept", time.time())

    rows = await backend.most_connected_entities(limit=10)

    assert {str(r["name"]): int(r["degree"]) for r in rows} == {"Lonely": 0}


async def test_most_connected_orders_by_degree_then_name(backend) -> None:
    """Ties break on name so the ordering is reproducible."""
    now = time.time()
    a = await _add(backend, "Aaa", "concept", now)
    b = await _add(backend, "Bbb", "concept", now)
    c = await _add(backend, "Ccc", "concept", now)
    await backend.upsert_relationship(
        rel_id="r1",
        source_id=b,
        target_id=c,
        relationship_type="links",
        weight=1.0,
        properties={},
        created_at=now,
        updated_at=now,
    )
    assert a  # created, deliberately unconnected

    rows = await backend.most_connected_entities(limit=10)
    names = [str(r["name"]) for r in rows]

    # Bbb and Ccc both have degree 1 and sort alphabetically; Aaa has 0.
    assert names == ["Bbb", "Ccc", "Aaa"]
