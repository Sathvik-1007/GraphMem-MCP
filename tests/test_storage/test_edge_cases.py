"""Edge-case and error-path tests for SQLiteBackend."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from graphrag_mcp.storage import SQLiteBackend

# ── Error paths ─────────────────────────────────────────────────────────────


async def test_get_entity_by_id_missing(tmp_db_path: Path) -> None:
    """get_entity_by_id returns None for nonexistent ID."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.get_entity_by_id("nonexistent-id")
        assert result is None
    finally:
        await backend.close()


async def test_get_entity_by_name_missing(tmp_db_path: Path) -> None:
    """get_entity_by_name returns None for nonexistent name."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.get_entity_by_name("NoSuchEntity")
        assert result is None
    finally:
        await backend.close()


async def test_get_entity_by_name_nocase_missing(tmp_db_path: Path) -> None:
    """get_entity_by_name_nocase returns None for nonexistent name."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.get_entity_by_name_nocase("nosuch")
        assert result is None
    finally:
        await backend.close()


async def test_get_relationship_missing(tmp_db_path: Path) -> None:
    """get_relationship returns None when no matching edge exists."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.get_relationship("a", "b", "knows")
        assert result is None
    finally:
        await backend.close()


async def test_delete_relationships_zero(tmp_db_path: Path) -> None:
    """delete_relationships returns 0 when nothing matches."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        count = await backend.delete_relationships("a", "b", "knows")
        assert count == 0
    finally:
        await backend.close()


async def test_get_observations_for_missing_entity(tmp_db_path: Path) -> None:
    """get_observations_for_entity returns [] for nonexistent entity."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        obs = await backend.get_observations_for_entity("missing-id")
        assert obs == []
    finally:
        await backend.close()


async def test_move_observations_no_op(tmp_db_path: Path) -> None:
    """move_observations returns 0 when source has no observations."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        count = await backend.move_observations("from-id", "to-id")
        assert count == 0
    finally:
        await backend.close()


# ── Empty collection queries ────────────────────────────────────────────────


async def test_list_entities_empty(tmp_db_path: Path) -> None:
    """list_entities returns [] on empty database."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        entities = await backend.list_entities()
        assert entities == []
    finally:
        await backend.close()


async def test_most_connected_entities_empty(tmp_db_path: Path) -> None:
    """most_connected_entities returns [] on empty database."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.most_connected_entities(limit=5)
        assert result == []
    finally:
        await backend.close()


async def test_recent_entities_empty(tmp_db_path: Path) -> None:
    """recent_entities returns [] on empty database."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.recent_entities(limit=5)
        assert result == []
    finally:
        await backend.close()


async def test_entity_type_distribution_empty(tmp_db_path: Path) -> None:
    """entity_type_distribution returns {} on empty database."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        dist = await backend.entity_type_distribution()
        assert dist == {}
    finally:
        await backend.close()


async def test_relationship_type_distribution_empty(tmp_db_path: Path) -> None:
    """relationship_type_distribution returns {} on empty database."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        dist = await backend.relationship_type_distribution()
        assert dist == {}
    finally:
        await backend.close()


# ── FTS search on empty database ────────────────────────────────────────────


async def test_fts_search_entities_empty(tmp_db_path: Path) -> None:
    """FTS search on empty database returns []."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        results = await backend.fts_search_entities("anything", limit=10)
        assert results == []
    finally:
        await backend.close()


async def test_fts_search_observations_empty(tmp_db_path: Path) -> None:
    """FTS observation search on empty database returns []."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        results = await backend.fts_search_observations("anything", limit=10)
        assert results == []
    finally:
        await backend.close()


async def test_fts_suggest_similar_empty(tmp_db_path: Path) -> None:
    """FTS suggestion on empty database returns []."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        results = await backend.fts_suggest_similar("anything")
        assert results == []
    finally:
        await backend.close()


# ── Metadata edge cases ────────────────────────────────────────────────────


async def test_metadata_overwrite(tmp_db_path: Path) -> None:
    """set_metadata overwrites existing value."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        await backend.set_metadata("key", "v1")
        await backend.set_metadata("key", "v2")
        value = await backend.get_metadata("key")
        assert value == "v2"
    finally:
        await backend.close()


async def test_metadata_nonexistent_key(tmp_db_path: Path) -> None:
    """get_metadata returns None for missing key."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        value = await backend.get_metadata("i_do_not_exist")
        assert value is None
    finally:
        await backend.close()


# ── Entity list with filter ────────────────────────────────────────────────


async def test_list_entities_with_type_filter(tmp_db_path: Path) -> None:
    """list_entities filters by entity_type."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        await backend.upsert_entity(
            entity_id="e1",
            name="A",
            entity_type="person",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )
        await backend.upsert_entity(
            entity_id="e2",
            name="B",
            entity_type="service",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )

        persons = await backend.list_entities(entity_type="person")
        assert len(persons) == 1
        assert persons[0]["name"] == "A"
    finally:
        await backend.close()


# ── Pagination ──────────────────────────────────────────────────────────────


async def test_list_entities_pagination(tmp_db_path: Path) -> None:
    """list_entities respects limit and offset."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        for i in range(5):
            await backend.upsert_entity(
                entity_id=f"e{i}",
                name=f"Entity{i}",
                entity_type="t",
                description="",
                properties={},
                created_at=now,
                updated_at=now,
            )

        page1 = await backend.list_entities(limit=2, offset=0)
        page2 = await backend.list_entities(limit=2, offset=2)
        page3 = await backend.list_entities(limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

        all_names = {e["name"] for e in page1 + page2 + page3}
        assert len(all_names) == 5
    finally:
        await backend.close()


# ── Transaction rollback ───────────────────────────────────────────────────


async def test_transaction_rollback(tmp_db_path: Path) -> None:
    """Failed transaction rolls back changes."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()

        with pytest.raises(ValueError, match="intentional"):
            async with backend.transaction():
                await backend.upsert_entity(
                    entity_id="e1",
                    name="ShouldNotExist",
                    entity_type="t",
                    description="",
                    properties={},
                    created_at=now,
                    updated_at=now,
                )
                raise ValueError("intentional rollback")

        # Entity should not have been persisted
        assert await backend.count_entities() == 0
    finally:
        await backend.close()


# ── fetch_all / fetch_one ───────────────────────────────────────────────────


async def test_fetch_one_returns_none(tmp_db_path: Path) -> None:
    """fetch_one returns None when no rows match."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        result = await backend.fetch_one("SELECT id FROM entities WHERE id = ?", ("nonexistent",))
        assert result is None
    finally:
        await backend.close()


async def test_fetch_all_returns_empty_list(tmp_db_path: Path) -> None:
    """fetch_all returns [] when no rows match."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        results = await backend.fetch_all(
            "SELECT id FROM entities WHERE entity_type = ?", ("nope",)
        )
        assert results == []
    finally:
        await backend.close()


# ── Relationship direction filtering ────────────────────────────────────────


async def test_get_relationships_direction(tmp_db_path: Path) -> None:
    """get_relationships_for_entity respects direction filter."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        now = time.time()
        # Create A -> B
        await backend.upsert_entity(
            entity_id="a",
            name="A",
            entity_type="t",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )
        await backend.upsert_entity(
            entity_id="b",
            name="B",
            entity_type="t",
            description="",
            properties={},
            created_at=now,
            updated_at=now,
        )
        await backend.upsert_relationship(
            rel_id="r1",
            source_id="a",
            target_id="b",
            relationship_type="links",
            weight=1.0,
            properties={},
            created_at=now,
            updated_at=now,
        )

        outgoing = await backend.get_relationships_for_entity("a", direction="outgoing")
        assert len(outgoing) == 1

        incoming = await backend.get_relationships_for_entity("a", direction="incoming")
        assert len(incoming) == 0

        both = await backend.get_relationships_for_entity("a", direction="both")
        assert len(both) == 1

        # From B's perspective
        b_incoming = await backend.get_relationships_for_entity("b", direction="incoming")
        assert len(b_incoming) == 1

        b_outgoing = await backend.get_relationships_for_entity("b", direction="outgoing")
        assert len(b_outgoing) == 0
    finally:
        await backend.close()


# ── Double initialization ───────────────────────────────────────────────────


async def test_double_initialize(tmp_db_path: Path) -> None:
    """Initializing a backend twice is safe (idempotent)."""
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    await backend.initialize()  # Should not raise
    assert await backend.count_entities() == 0
    await backend.close()
