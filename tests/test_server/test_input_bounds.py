"""Tool-boundary tests: response bounds, input validation, and tool schemas.

Every test here fails against the pre-fix tools.  Grouped by the defect it
guards:

* ``TestLimitsNeverReachSqlUnbounded`` — a negative limit is unbounded in
  SQLite, so it must be clamped before the query is built.
* ``TestResponsesAreCapped`` — a response that does not fit the caller's
  context window is unusable, so caps exist and are declared.
* ``TestMalformedInput`` — a null or wrong-typed field must come back as a
  structured error naming the field, not as an AttributeError.
* ``TestToolSchemas`` — the per-item shape must be in the JSON schema, not
  only in the docstring.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp.tools.base import Tool

import graph_mem.tools._core as core
import graph_mem.tools.maintenance as maintenance_mod
import graph_mem.tools.search as search_mod
from graph_mem.server import (
    add_entities,
    add_observations,
    add_relationships,
    compact_observations,
    delete_entities,
    delete_observations,
    find_connections,
    find_paths,
    get_entity,
    get_subgraph,
    list_entities,
    list_relationships,
    merge_entities,
    search_nodes,
    search_observations,
    suggest_connections,
    update_entity,
    update_observation,
)

# ===========================================================================
# Limits
# ===========================================================================


class TestLimitsNeverReachSqlUnbounded:
    """A negative SQL ``LIMIT`` means *no limit* in SQLite, not zero rows."""

    @staticmethod
    def _spy_fts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """Record every limit the search layer passes to the FTS5 queries."""
        seen: list[int] = []
        storage = core._state.storage
        assert storage is not None

        original_entities = storage.fts_search_entities
        original_observations = storage.fts_search_observations

        async def spy_entities(query: str, limit: int, entity_types: Any = None) -> Any:
            seen.append(limit)
            return await original_entities(query, limit, entity_types)

        async def spy_observations(query: str, limit: int) -> Any:
            seen.append(limit)
            return await original_observations(query, limit)

        monkeypatch.setattr(storage, "fts_search_entities", spy_entities)
        monkeypatch.setattr(storage, "fts_search_observations", spy_observations)
        return seen

    @pytest.mark.asyncio
    async def test_search_nodes_negative_limit(self, setup_server, monkeypatch):
        seen = self._spy_fts(monkeypatch)
        await add_entities([{"name": "Alpha", "entity_type": "concept"}])

        result = await search_nodes("alpha", limit=-1)

        assert "error" not in result
        assert seen and all(limit > 0 for limit in seen)
        assert result["limit"] == 1

    @pytest.mark.asyncio
    async def test_search_observations_negative_limit(self, setup_server, monkeypatch):
        await add_entities([{"name": "Alpha", "entity_type": "concept"}])
        await add_observations("Alpha", ["a fact"])
        seen = self._spy_fts(monkeypatch)

        result = await search_observations("fact", limit=-100)

        assert "error" not in result
        assert seen and all(limit > 0 for limit in seen)
        assert result["limit"] == 1

    @pytest.mark.asyncio
    async def test_search_limit_capped_at_maximum(self, setup_server, monkeypatch):
        seen = self._spy_fts(monkeypatch)
        await add_entities([{"name": "Alpha", "entity_type": "concept"}])

        result = await search_nodes("alpha", limit=10_000)

        assert result["limit"] == core.MAX_SEARCH_LIMIT
        assert max(seen) <= core.MAX_SEARCH_LIMIT * 3

    @pytest.mark.asyncio
    async def test_suggest_connections_negative_limit(self, setup_server, monkeypatch):
        await add_entities([{"name": "Alpha", "entity_type": "concept"}])
        seen = self._spy_fts(monkeypatch)

        result = await suggest_connections("Alpha", limit=-5)

        assert "error" not in result
        assert seen and all(limit > 0 for limit in seen)

    @pytest.mark.asyncio
    async def test_list_negative_limit_and_offset(self, setup_server):
        await add_entities([{"name": "Alpha", "entity_type": "concept"}])

        entities = await list_entities(limit=-1, offset=-5)
        assert entities["limit"] == 1
        assert entities["offset"] == 0

        rels = await list_relationships(limit=-1, offset=-5)
        assert rels["limit"] == 1
        assert rels["offset"] == 0

    @pytest.mark.asyncio
    async def test_non_integer_limit_is_a_structured_error(self, setup_server):
        result = await search_nodes("anything", limit="lots")  # type: ignore[arg-type]
        assert result["error"] is True
        assert "integer" in result["message"]

    @pytest.mark.asyncio
    async def test_negative_hops_and_radius_are_clamped(self, setup_server):
        await add_entities(
            [
                {"name": "A", "entity_type": "concept"},
                {"name": "B", "entity_type": "concept"},
            ]
        )
        await add_relationships([{"source": "A", "target": "B", "relationship_type": "knows"}])

        connections = await find_connections("A", max_hops=-3)
        assert "error" not in connections
        assert connections["count"] == 1

        subgraph = await get_subgraph(["A"], radius=-3)
        assert "error" not in subgraph
        assert len(subgraph["entities"]) == 2

        paths = await find_paths("A", "B", max_hops=-3)
        assert "error" not in paths
        assert paths["count"] == 1


class TestResponsesAreCapped:
    @pytest.mark.asyncio
    async def test_get_entity_caps_observations(self, setup_server):
        """A hot entity must not dump every observation into the response."""
        over_cap = core.MAX_NESTED_ITEMS + 5
        await add_entities([{"name": "Hot", "entity_type": "concept"}])
        await add_observations("Hot", [f"fact {i}" for i in range(over_cap)])

        result = await get_entity("Hot")

        assert len(result["observations"]) == core.MAX_NESTED_ITEMS
        assert result["observation_count"] == over_cap
        assert result["observations_truncated"] is True
        assert result["relationships_truncated"] is False

    @pytest.mark.asyncio
    async def test_find_connections_caps_results(self, setup_server, monkeypatch):
        monkeypatch.setattr(search_mod, "MAX_TRAVERSAL_RESULTS", 2)
        await add_entities(
            [{"name": f"N{i}", "entity_type": "concept"} for i in range(5)],
        )
        await add_relationships(
            [{"source": "N0", "target": f"N{i}", "relationship_type": "knows"} for i in range(1, 5)]
        )

        result = await find_connections("N0", max_hops=1)

        assert result["count"] == 2
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_get_subgraph_caps_entities_and_prunes_dangling_edges(
        self, setup_server, monkeypatch
    ):
        monkeypatch.setattr(search_mod, "MAX_TRAVERSAL_RESULTS", 2)
        await add_entities(
            [{"name": f"N{i}", "entity_type": "concept"} for i in range(5)],
        )
        await add_relationships(
            [{"source": "N0", "target": f"N{i}", "relationship_type": "knows"} for i in range(1, 5)]
        )

        result = await get_subgraph(["N0"], radius=1)

        assert len(result["entities"]) == 2
        assert result["truncated"] is True
        kept = {e["id"] for e in result["entities"]}
        # No edge may reference an entity the caller was not given.
        for rel in result["relationships"]:
            assert rel["source_id"] in kept
            assert rel["target_id"] in kept

    @pytest.mark.asyncio
    async def test_audit_graph_scan_is_bounded(self, setup_server, monkeypatch):
        monkeypatch.setattr(maintenance_mod, "AUDIT_ENTITY_SCAN_LIMIT", 3)
        await add_entities([{"name": f"E{i}", "entity_type": "concept"} for i in range(6)])

        result = await maintenance_mod.audit_graph()

        assert result["scanned_entities"] == 3
        assert result["truncated"] is True
        assert result["counts"]["entities"] == 6


# ===========================================================================
# Input validation
# ===========================================================================


class TestMalformedInput:
    """Type-sloppy callers get a structured error naming the field."""

    @pytest.mark.asyncio
    async def test_null_description(self, setup_server):
        result = await add_entities([{"name": "A", "entity_type": "t", "description": None}])
        assert result["error"] is True
        assert "entities[0].description" in result["message"]

    @pytest.mark.asyncio
    async def test_null_name(self, setup_server):
        result = await add_entities([{"name": None, "entity_type": "t"}])
        assert result["error"] is True
        assert "entities[0].name" in result["message"]

    @pytest.mark.asyncio
    async def test_int_where_string_expected(self, setup_server):
        result = await add_entities([{"name": 42, "entity_type": "t"}])
        assert result["error"] is True
        assert result["error_type"] == "ValidationError"
        assert "entities[0].name" in result["message"]

    @pytest.mark.asyncio
    async def test_string_where_list_expected(self, setup_server):
        result = await add_entities([{"name": "A", "entity_type": "t", "observations": "one fact"}])
        assert result["error"] is True
        assert "entities[0].observations" in result["message"]

    @pytest.mark.asyncio
    async def test_string_where_list_of_dicts_expected(self, setup_server):
        result = await add_entities("Alice")  # type: ignore[arg-type]
        assert result["error"] is True
        assert "'entities' must be a list" in result["message"]

    @pytest.mark.asyncio
    async def test_item_is_a_string_not_an_object(self, setup_server):
        result = await add_entities(["Alice"])  # type: ignore[list-item]
        assert result["error"] is True
        assert "entities[0]" in result["message"]

    @pytest.mark.asyncio
    async def test_missing_key_names_the_key(self, setup_server):
        result = await add_entities([{"name": "A"}])
        assert result["error"] is True
        assert "entities[0].entity_type" in result["message"]

    @pytest.mark.asyncio
    async def test_unknown_key_is_reported_not_ignored(self, setup_server):
        result = await add_entities([{"name": "A", "type": "person"}])
        assert result["error"] is True
        assert "entities[0]" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_name(self, setup_server):
        result = await add_entities([{"name": "   ", "entity_type": "t"}])
        assert result["error"] is True
        assert "Invalid input" in result["message"]

    @pytest.mark.asyncio
    async def test_relationship_null_weight(self, setup_server):
        result = await add_relationships(
            [{"source": "A", "target": "B", "relationship_type": "knows", "weight": None}]
        )
        assert result["error"] is True
        assert "relationships[0].weight" in result["message"]

    @pytest.mark.asyncio
    async def test_relationship_list_where_string_expected(self, setup_server):
        result = await add_relationships(
            [{"source": ["A"], "target": "B", "relationship_type": "knows"}]
        )
        assert result["error"] is True
        assert "relationships[0].source" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_entities_wrong_element_type(self, setup_server):
        result = await delete_entities([42])  # type: ignore[list-item]
        assert result["error"] is True
        assert "names[0]" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_entities_not_a_list(self, setup_server):
        result = await delete_entities("Alice")  # type: ignore[arg-type]
        assert result["error"] is True
        assert "'names' must be a list" in result["message"]

    @pytest.mark.asyncio
    async def test_name_arguments_reject_none(self, setup_server):
        # Built lazily: one call raising would leave the rest of the
        # coroutines un-awaited, and an un-awaited coroutine warning is an
        # error under this suite's filterwarnings.
        calls = [
            lambda: get_entity(None),  # type: ignore[arg-type]
            lambda: update_entity(None),  # type: ignore[arg-type]
            lambda: merge_entities(None, "b"),  # type: ignore[arg-type]
            lambda: add_observations(None, ["x"]),  # type: ignore[arg-type]
            lambda: delete_observations(None, ["x"]),  # type: ignore[arg-type]
            lambda: update_observation(None, "id", "text"),  # type: ignore[arg-type]
            lambda: find_connections(None),  # type: ignore[arg-type]
            lambda: get_subgraph(None),  # type: ignore[arg-type]
            lambda: find_paths(None, "b"),  # type: ignore[arg-type]
            lambda: suggest_connections(None),  # type: ignore[arg-type]
            lambda: compact_observations(None, [], []),  # type: ignore[arg-type]
        ]
        for call in calls:
            result = await call()
            assert result["error"] is True, result
            assert "Invalid input" in result["message"]

    @pytest.mark.asyncio
    async def test_add_observations_rejects_non_string_items(self, setup_server):
        await add_entities([{"name": "A", "entity_type": "t"}])
        result = await add_observations("A", ["fine", None])  # type: ignore[list-item]
        assert result["error"] is True
        assert "observations[1]" in result["message"]

    @pytest.mark.asyncio
    async def test_invalid_direction_is_structured(self, setup_server):
        await add_entities([{"name": "A", "entity_type": "t"}])
        result = await find_connections("A", direction="sideways")  # type: ignore[arg-type]
        assert result["error"] is True
        assert result["error_type"] == "ValidationError"
        assert "direction" in result["message"]


# ===========================================================================
# compact_observations
# ===========================================================================


class TestCompactObservations:
    @pytest.mark.asyncio
    async def test_duplicate_keep_ids_counted_once(self, setup_server):
        await add_entities([{"name": "E", "entity_type": "t"}])
        await add_observations("E", ["one", "two"])
        entity = await get_entity("E")
        kept_id = str(entity["observations"][0]["id"])

        result = await compact_observations("E", keep_ids=[kept_id, kept_id], new_observations=[])

        assert result["kept"] == 1
        assert result["after"] == 1
        # And the stored graph agrees with the count reported.
        assert (await get_entity("E"))["observation_count"] == 1

    @pytest.mark.asyncio
    async def test_embedding_runs_outside_the_write_transaction(self, setup_server, monkeypatch):
        """Loading a cold embedding model blocks every other tool if a write
        transaction is still open."""
        storage = core._state.storage
        assert storage is not None
        depths: list[int] = []

        async def spy(results):
            depths.append(storage._db._txn_depth)  # type: ignore[attr-defined]

        monkeypatch.setattr(maintenance_mod, "_embed_observations", spy)

        await add_entities([{"name": "E", "entity_type": "t"}])
        await add_observations("E", ["one"])
        result = await compact_observations("E", keep_ids=[], new_observations=["merged"])

        assert result["status"] == "compacted"
        assert depths == [0]


# ===========================================================================
# Schemas
# ===========================================================================


class TestToolSchemas:
    @pytest.mark.parametrize(
        ("fn", "argument", "required", "string_field"),
        [
            (add_entities, "entities", {"name", "entity_type"}, "name"),
            (
                add_relationships,
                "relationships",
                {"source", "target", "relationship_type"},
                "source",
            ),
        ],
    )
    def test_per_item_shape_is_in_the_schema(self, fn, argument, required, string_field):
        """The client must see the item keys, not a bare ``{"type": "object"}``."""
        schema = Tool.from_function(fn).parameters
        ref = schema["properties"][argument]["items"]["$ref"]
        item_schema = schema["$defs"][ref.rsplit("/", 1)[-1]]
        assert required <= set(item_schema["properties"])
        assert required == set(item_schema["required"])
        assert item_schema["properties"][string_field]["type"] == "string"

    def test_direction_is_constrained_by_the_schema(self):
        schema = Tool.from_function(find_connections).parameters
        assert schema["properties"]["direction"]["enum"] == ["outgoing", "incoming", "both"]
