"""Tests for the shared tool infrastructure in :mod:`graph_mem.tools._core`.

Covers the pieces every tool module depends on but no single tool owns: the
engine-swap handshake, the input-boundary helpers, the ``tool()`` decorator's
schema fidelity, the structured error response, and the lifespan.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

import pytest
from mcp.server.fastmcp.tools import Tool

import graph_mem.tools._core as core
from graph_mem.db.connection import Database
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.tools.entities import EntityInput, add_entities
from graph_mem.utils.config import Config
from graph_mem.utils.errors import (
    DatabaseError,
    EntityNotFoundError,
    GraphMemError,
    IntegrityError,
    SchemaError,
    ValidationError,
)


@pytest.fixture
def isolated_sync_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give the test its own lock/idle-event pair on the global ``_state``.

    ``asyncio.Lock`` and ``asyncio.Event`` bind themselves to the first event
    loop that actually *contends* them, and pytest-asyncio gives every test a
    fresh loop.  The tests below contend both on purpose, so they must not do
    it to the process-wide instances every other test shares.
    """
    monkeypatch.setattr(core._state, "_switch_lock", asyncio.Lock())
    monkeypatch.setattr(core._state, "_ops_idle", core._new_idle_event())
    monkeypatch.setattr(core._state, "_active_ops", 0)
    yield


@pytest.fixture
def unregister_probe_tools() -> Iterator[list[str]]:
    """Drop tools registered by a test from the process-wide FastMCP instance."""
    names: list[str] = []
    yield names
    for name in names:
        core.mcp._tool_manager._tools.pop(name, None)


# ═══════════════════════════════════════════════════════════════════════════
# _op_guard
# ═══════════════════════════════════════════════════════════════════════════


async def test_op_guard_registers_then_deregisters(isolated_sync_state: None) -> None:
    """Entering marks the engines busy; leaving marks them idle again."""
    assert core._state._active_ops == 0
    assert core._state._ops_idle.is_set()

    async with core._op_guard():
        assert core._state._active_ops == 1
        assert not core._state._ops_idle.is_set()

    assert core._state._active_ops == 0
    assert core._state._ops_idle.is_set()


async def test_op_guard_deregisters_when_the_body_raises(isolated_sync_state: None) -> None:
    """A failing tool must not leave the engines permanently pinned."""
    with pytest.raises(RuntimeError, match="boom"):
        async with core._op_guard():
            assert core._state._active_ops == 1
            raise RuntimeError("boom")

    assert core._state._active_ops == 0
    assert core._state._ops_idle.is_set()


async def test_op_guard_deregisters_when_the_task_is_cancelled(
    isolated_sync_state: None,
) -> None:
    """A cancelled tool call releases its registration, so a switch can proceed."""
    started = asyncio.Event()

    async def op() -> None:
        async with core._op_guard():
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.create_task(op())
    await started.wait()
    assert core._state._active_ops == 1
    assert not core._state._ops_idle.is_set()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert core._state._active_ops == 0
    assert core._state._ops_idle.is_set()


async def test_op_guard_stays_busy_until_the_last_op_leaves(
    isolated_sync_state: None,
) -> None:
    """Idle is signalled on the *last* departure, not the first."""
    async with core._op_guard():
        async with core._op_guard():
            assert core._state._active_ops == 2
            assert not core._state._ops_idle.is_set()
        assert core._state._active_ops == 1
        assert not core._state._ops_idle.is_set()

    assert core._state._active_ops == 0
    assert core._state._ops_idle.is_set()


# ═══════════════════════════════════════════════════════════════════════════
# _exclusive_engines
# ═══════════════════════════════════════════════════════════════════════════


async def test_exclusive_engines_waits_for_in_flight_ops(isolated_sync_state: None) -> None:
    """The writer does not enter until every registered reader has left."""
    order: list[str] = []
    registered = asyncio.Event()
    release = asyncio.Event()

    async def reader() -> None:
        async with core._op_guard():
            registered.set()
            await release.wait()
            order.append("reader-done")

    async def writer() -> None:
        async with core._exclusive_engines():
            order.append("writer-in")

    reader_task = asyncio.create_task(reader())
    await registered.wait()
    writer_task = asyncio.create_task(writer())

    await asyncio.sleep(0.05)
    assert order == [], "writer entered while a reader was still registered"

    release.set()
    await asyncio.gather(reader_task, writer_task)
    assert order == ["reader-done", "writer-in"]


async def test_exclusive_engines_blocks_new_ops_while_held(isolated_sync_state: None) -> None:
    """A reader arriving mid-switch waits rather than pinning the old engines."""
    order: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def writer() -> None:
        async with core._exclusive_engines():
            order.append("writer-in")
            entered.set()
            await release.wait()
            order.append("writer-out")

    async def reader() -> None:
        async with core._op_guard():
            order.append("reader-in")

    writer_task = asyncio.create_task(writer())
    await entered.wait()
    reader_task = asyncio.create_task(reader())

    await asyncio.sleep(0.05)
    assert order == ["writer-in"], "a new op registered while the switch was held"

    release.set()
    await asyncio.gather(writer_task, reader_task)
    assert order == ["writer-in", "writer-out", "reader-in"]


# ═══════════════════════════════════════════════════════════════════════════
# _clamp_limit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),  # exactly the minimum
        (2, 2),
        (499, 499),
        (500, 500),  # exactly the maximum
        (501, 500),  # above the maximum
        (0, 1),  # below the minimum
        (-1, 1),
        (-(10**9), 1),
        (10**20, 500),  # very large
    ],
)
def test_clamp_limit_boundaries(value: int, expected: int) -> None:
    """Values are constrained to [1, maximum] with no pass-through."""
    assert core._clamp_limit(value, maximum=core.MAX_LIST_LIMIT) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (-1, 0), (core.MAX_OFFSET, core.MAX_OFFSET), (core.MAX_OFFSET + 1, core.MAX_OFFSET)],
)
def test_clamp_limit_honours_a_zero_minimum(value: int, expected: int) -> None:
    """Offsets use minimum=0, so zero is a legal answer rather than a floor violation."""
    assert core._clamp_limit(value, maximum=core.MAX_OFFSET, minimum=0) == expected


@pytest.mark.parametrize("value", ["10", 1.5, None, [1], float("inf"), float("nan")])
def test_clamp_limit_rejects_non_integers(value: object) -> None:
    """A non-integer is refused rather than silently defaulted."""
    with pytest.raises(ValidationError) as excinfo:
        core._clamp_limit(value, maximum=core.MAX_LIST_LIMIT)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "expected an integer count" in message
    assert type(value).__name__ in message


def test_clamp_limit_treats_bool_as_the_int_it_subclasses() -> None:
    """bool passes the isinstance check and is clamped, not rejected.

    Documented rather than endorsed: ``open_dashboard`` explicitly rejects
    ``bool`` for its port, so the two input boundaries disagree.  If
    ``_clamp_limit`` is tightened to match, this test is the one to update.
    """
    assert core._clamp_limit(True, maximum=core.MAX_LIST_LIMIT) == 1
    assert core._clamp_limit(False, maximum=core.MAX_LIST_LIMIT) == 1
    assert core._clamp_limit(False, maximum=core.MAX_OFFSET, minimum=0) == 0


# ═══════════════════════════════════════════════════════════════════════════
# _validate_items
# ═══════════════════════════════════════════════════════════════════════════


def test_validate_items_rejects_a_non_list() -> None:
    """The message names the parameter and the type actually received."""
    with pytest.raises(ValidationError) as excinfo:
        core._validate_items({"name": "Ada"}, EntityInput, field="entities")

    assert str(excinfo.value) == ("Invalid input: 'entities' must be a list of objects, got dict")


def test_validate_items_names_the_index_and_the_missing_field() -> None:
    """A malformed item is located precisely, not reported as 'invalid input'."""
    items = [
        {"name": "Ada", "entity_type": "person"},
        {"name": "Babbage"},  # entity_type missing
    ]
    with pytest.raises(ValidationError) as excinfo:
        core._validate_items(items, EntityInput, field="entities")

    message = str(excinfo.value)
    assert message.startswith("Invalid input: entities[1].entity_type: ")
    assert "Field required" in message


def test_validate_items_names_the_index_and_the_wrong_typed_field() -> None:
    """A field of the wrong type is reported against its own index."""
    with pytest.raises(ValidationError) as excinfo:
        core._validate_items(
            [{"name": 123, "entity_type": "person"}], EntityInput, field="entities"
        )

    assert str(excinfo.value).startswith("Invalid input: entities[0].name: ")


def test_validate_items_names_an_unexpected_key() -> None:
    """``extra='forbid'`` surfaces a hallucinated key by name, not as a silent drop."""
    with pytest.raises(ValidationError) as excinfo:
        core._validate_items(
            [{"name": "Ada", "entity_type": "person", "colour": "blue"}],
            EntityInput,
            field="entities",
        )

    assert "entities[0].colour" in str(excinfo.value)


def test_validate_items_accepts_both_dicts_and_model_instances() -> None:
    """In-process callers pass dicts; the MCP runtime passes parsed models."""
    validated = core._validate_items(
        [
            {"name": "Ada", "entity_type": "person"},
            EntityInput(name="Babbage", entity_type="person"),
        ],
        EntityInput,
        field="entities",
    )

    assert [item.name for item in validated] == ["Ada", "Babbage"]
    assert all(isinstance(item, EntityInput) for item in validated)


# ═══════════════════════════════════════════════════════════════════════════
# _require_text / _require_text_list
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("value", [None, 42, 4.2, ["Ada"], {"name": "Ada"}, b"Ada"])
def test_require_text_rejects_non_strings(value: object) -> None:
    with pytest.raises(ValidationError) as excinfo:
        core._require_text(value, "name")

    assert str(excinfo.value) == (
        f"Invalid input: 'name' must be a string, got {type(value).__name__}"
    )


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_require_text_rejects_blank_strings(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        core._require_text(value, "name")

    assert str(excinfo.value) == "Invalid input: 'name' must not be empty"


def test_require_text_strips_and_returns() -> None:
    assert core._require_text("  Ada Lovelace \n", "name") == "Ada Lovelace"


def test_require_text_allows_blank_when_asked() -> None:
    """Free-text queries may legitimately be empty; names may not."""
    assert core._require_text("   ", "query", allow_empty=True) == ""


def test_require_text_list_rejects_a_non_list() -> None:
    with pytest.raises(ValidationError) as excinfo:
        core._require_text_list("Ada,Babbage", "names")

    assert str(excinfo.value) == ("Invalid input: 'names' must be a list of strings, got str")


def test_require_text_list_names_the_offending_index() -> None:
    with pytest.raises(ValidationError) as excinfo:
        core._require_text_list(["Ada", 7, "Babbage"], "names")

    assert str(excinfo.value) == "Invalid input: 'names[1]' must be a string, got int"


def test_require_text_list_names_the_index_of_a_blank_element() -> None:
    with pytest.raises(ValidationError) as excinfo:
        core._require_text_list(["Ada", "Babbage", "   "], "names")

    assert str(excinfo.value) == "Invalid input: 'names[2]' must not be empty"


def test_require_text_list_strips_every_element() -> None:
    assert core._require_text_list([" Ada ", "Babbage\n"], "names") == ["Ada", "Babbage"]


def test_require_text_list_allows_blank_elements_when_asked() -> None:
    assert core._require_text_list(["", " x "], "names", allow_empty_items=True) == ["", "x"]


def test_require_text_list_accepts_the_empty_list() -> None:
    assert core._require_text_list([], "names") == []


# ═══════════════════════════════════════════════════════════════════════════
# tool() decorator
# ═══════════════════════════════════════════════════════════════════════════


async def _probe_schema_tool(name: str, limit: int = 10, verbose: bool = False) -> dict[str, Any]:
    """Probe tool used to compare derived schemas.

    Args:
        name: Who to look up.
        limit: How many results.
        verbose: Whether to be loud.
    """
    return {"name": name, "limit": limit, "verbose": verbose}


def test_tool_decorator_preserves_identity_and_derived_schema(
    unregister_probe_tools: list[str],
) -> None:
    """FastMCP must derive the same tool from the wrapped and unwrapped function."""
    guarded = core.tool()(_probe_schema_tool)
    unregister_probe_tools.append("_probe_schema_tool")

    assert guarded is not _probe_schema_tool
    assert guarded.__name__ == _probe_schema_tool.__name__
    assert guarded.__doc__ == _probe_schema_tool.__doc__
    assert inspect.signature(guarded) == inspect.signature(_probe_schema_tool)

    raw = Tool.from_function(_probe_schema_tool)
    wrapped = Tool.from_function(guarded)

    assert wrapped.name == raw.name == "_probe_schema_tool"
    assert wrapped.description == raw.description
    assert wrapped.parameters == raw.parameters
    # Guard against both schemas being equally empty.
    assert set(raw.parameters["properties"]) == {"name", "limit", "verbose"}
    assert raw.parameters["required"] == ["name"]


def test_tool_decorator_registers_with_the_shared_mcp_instance(
    unregister_probe_tools: list[str],
) -> None:
    """The decorator is a drop-in for ``@mcp.tool()``, so it must register."""
    assert "_probe_schema_tool" not in core.mcp._tool_manager._tools

    core.tool()(_probe_schema_tool)
    unregister_probe_tools.append("_probe_schema_tool")

    assert "_probe_schema_tool" in core.mcp._tool_manager._tools


async def test_tool_decorator_runs_the_body_inside_the_op_guard(
    isolated_sync_state: None, unregister_probe_tools: list[str]
) -> None:
    """A decorated tool registers itself as a reader for exactly its own duration."""
    observed: list[int] = []

    async def _probe_guarded_tool() -> str:
        """Probe tool that observes the in-flight op count."""
        observed.append(core._state._active_ops)
        return "done"

    guarded = core.tool()(_probe_guarded_tool)
    unregister_probe_tools.append("_probe_guarded_tool")

    assert await guarded() == "done"
    assert observed == [1]
    assert core._state._active_ops == 0
    assert core._state._ops_idle.is_set()


async def test_tool_decorator_forwards_arguments_and_return_value(
    isolated_sync_state: None, unregister_probe_tools: list[str]
) -> None:
    """The wrapper is transparent to positional and keyword arguments."""
    guarded = core.tool()(_probe_schema_tool)
    unregister_probe_tools.append("_probe_schema_tool")

    assert await guarded("Ada", limit=3) == {"name": "Ada", "limit": 3, "verbose": False}


# ═══════════════════════════════════════════════════════════════════════════
# _error_response
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "exc",
    [
        ValidationError("bad input"),
        DatabaseError("bad input"),
        SchemaError("bad input"),
        IntegrityError("bad input"),
        GraphMemError("bad input"),
        ValueError("bad input"),
    ],
)
def test_error_response_reports_the_concrete_error_class(exc: Exception) -> None:
    """error_type is the exception's own class name, so callers can branch on it."""
    assert core._error_response(exc, tool_name="probe") == {
        "error": True,
        "error_type": type(exc).__name__,
        "message": "bad input",
    }


def test_error_response_includes_details_for_graphmem_errors() -> None:
    resp = core._error_response(GraphMemError("nope", details="entity_type must be lowercase"))
    assert resp["details"] == "entity_type must be lowercase"


def test_error_response_omits_details_when_absent() -> None:
    assert "details" not in core._error_response(GraphMemError("nope"))


def test_error_response_surfaces_entity_suggestions() -> None:
    resp = core._error_response(EntityNotFoundError("Ada", suggestions=["Ada Lovelace", "Adam"]))
    assert resp["error_type"] == "EntityNotFoundError"
    assert resp["suggestions"] == ["Ada Lovelace", "Adam"]
    assert "Did you mean" in resp["message"]


def test_error_response_omits_empty_suggestions() -> None:
    resp = core._error_response(EntityNotFoundError("Ada"))
    assert "suggestions" not in resp
    assert resp["message"] == "Entity 'Ada' not found."


async def test_error_response_does_not_leak_sql_from_a_database_error(tmp_path: Path) -> None:
    """A DatabaseError raised by a real failed query carries no query text."""
    database = Database(tmp_path / "leak.db")
    await database.initialize()
    try:
        with pytest.raises(DatabaseError) as excinfo:
            await database.fetch_all(
                "SELECT secret_column FROM sqlite_master WHERE name = 'hidden'"
            )
    finally:
        await database.close()

    resp = core._error_response(excinfo.value, tool_name="search_nodes")

    assert resp["error_type"] == "DatabaseError"
    assert resp["message"].startswith("SQL error:")
    assert "sqlite_master" not in resp["message"]
    assert "SELECT" not in resp["message"]
    assert "hidden" not in resp["message"]
    assert "details" not in resp


# ═══════════════════════════════════════════════════════════════════════════
# _require_state
# ═══════════════════════════════════════════════════════════════════════════


def test_require_state_refuses_when_uninitialised() -> None:
    """Tools must get a clear error rather than an AttributeError on None."""
    assert core._state.storage is None, "state leaked from another test"
    with pytest.raises(GraphMemError, match="Server not initialised"):
        core._require_state()


async def test_require_state_returns_every_engine(setup_server: Path) -> None:
    state = core._require_state()
    assert state.storage is core._state.storage
    assert state.graph is core._state.graph
    assert state.traversal is core._state.traversal
    assert state.merger is core._state.merger
    assert state.embeddings is core._state.embeddings
    assert state.search is core._state.search
    assert state.config is core._state.config


# ═══════════════════════════════════════════════════════════════════════════
# Embedding helpers
# ═══════════════════════════════════════════════════════════════════════════


class _RecordingEmbeddings:
    """Stand-in embedding engine that records what it was asked to do."""

    def __init__(self, *, available: bool = True, vectors: list[Any] | None = None) -> None:
        self.available = available
        self._vectors = vectors
        self.embed_calls: list[list[str]] = []
        self.entity_upserts: list[str] = []
        self.observation_upserts: list[str] = []

    async def embed(self, texts: list[str]) -> list[Any]:
        self.embed_calls.append(list(texts))
        if self._vectors is not None:
            return self._vectors
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def upsert_entity_embedding(self, entity_id: str, vector: Any) -> None:
        self.entity_upserts.append(entity_id)

    async def upsert_observation_embedding(self, observation_id: str, vector: Any) -> None:
        self.observation_upserts.append(observation_id)


async def _make_entities(names: list[str]) -> list[str]:
    result = await add_entities(
        entities=[{"name": name, "entity_type": "person"} for name in names]
    )
    assert not result.get("error"), result
    return [str(row["id"]) for row in result["results"]]


async def test_embed_entities_upserts_one_vector_per_entity(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _make_entities(["Ada", "Babbage"])
    fake = _RecordingEmbeddings()
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_entities(ids)

    assert fake.entity_upserts == ids
    assert len(fake.embed_calls) == 1
    assert len(fake.embed_calls[0]) == 2
    assert any("Ada" in text for text in fake.embed_calls[0])


async def test_embed_entities_skips_ids_that_no_longer_exist(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted entity must not abort embedding for its surviving siblings."""
    ids = await _make_entities(["Ada"])
    fake = _RecordingEmbeddings()
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_entities(["missing-id-qq", *ids])

    assert fake.entity_upserts == ids
    assert len(fake.embed_calls[0]) == 1


async def test_embed_entities_is_a_noop_when_nothing_resolves(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _RecordingEmbeddings()
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_entities(["missing-a", "missing-b"])

    assert fake.embed_calls == []
    assert fake.entity_upserts == []


@pytest.mark.parametrize("entity_ids", [[], ["anything"]])
async def test_embed_entities_is_a_noop_when_embeddings_unavailable(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch, entity_ids: list[str]
) -> None:
    fake = _RecordingEmbeddings(available=False)
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_entities(entity_ids)

    assert fake.embed_calls == []
    assert fake.entity_upserts == []


async def test_embed_entities_skips_null_vectors(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that fails on one text must not upsert a null embedding for it."""
    ids = await _make_entities(["Ada", "Babbage"])
    fake = _RecordingEmbeddings(vectors=[None, [0.1, 0.2, 0.3]])
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_entities(ids)

    assert fake.entity_upserts == [ids[1]]


async def test_embed_observation_texts_upserts_by_id(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _RecordingEmbeddings()
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_observation_texts([("obs-1", "first"), ("obs-2", "second")])

    assert fake.embed_calls == [["first", "second"]]
    assert fake.observation_upserts == ["obs-1", "obs-2"]


async def test_embed_observation_texts_skips_null_vectors(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _RecordingEmbeddings(vectors=[[0.1], None])
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_observation_texts([("obs-1", "first"), ("obs-2", "second")])

    assert fake.observation_upserts == ["obs-1"]


@pytest.mark.parametrize("available", [True, False])
async def test_embed_observation_texts_is_a_noop_for_nothing_to_do(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    fake = _RecordingEmbeddings(available=available)
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_observation_texts([] if available else [("obs-1", "x")])

    assert fake.embed_calls == []


async def test_embed_observations_maps_result_rows_onto_ids(
    setup_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_embed_observations`` is the ObservationResult-shaped door to the same path."""
    fake = _RecordingEmbeddings()
    monkeypatch.setattr(core._state, "embeddings", fake)

    await core._embed_observations(
        [{"id": "obs-1", "content": "alpha"}, {"id": "obs-2", "content": "beta"}]  # type: ignore[list-item]
    )

    assert fake.embed_calls == [["alpha", "beta"]]
    assert fake.observation_upserts == ["obs-1", "obs-2"]


# ═══════════════════════════════════════════════════════════════════════════
# _lifespan
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def clean_global_state() -> Iterator[None]:
    """Snapshot and restore every field ``_lifespan`` writes to."""
    fields = (
        "config",
        "storage",
        "graph",
        "traversal",
        "merger",
        "embeddings",
        "search",
        "_ui_url",
        "_ui_runner",
        "_ui_port",
        "_ui_app",
        "_graphmem_dir",
        "_active_graph",
    )
    saved = {name: getattr(core._state, name) for name in fields}
    yield
    for name, value in saved.items():
        setattr(core._state, name, value)


@pytest.fixture
def stub_prewarm(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Replace the real model load so the lifespan never touches PyTorch."""
    done = threading.Event()

    def fake_load(self: EmbeddingEngine) -> None:
        done.set()

    monkeypatch.setattr(EmbeddingEngine, "_ensure_model_loaded", fake_load)
    return done


async def test_lifespan_wires_engines_and_closes_storage_on_exit(
    tmp_path: Path, clean_global_state: None, stub_prewarm: threading.Event
) -> None:
    core._state.config = Config(db_path=tmp_path / ".graphmem" / "graph.db")

    async with core._lifespan(core.mcp):
        state = core._require_state()
        storage = state.storage
        assert storage.backend_type == "sqlite"
        assert core._state._graphmem_dir == tmp_path / ".graphmem"
        assert core._state._active_graph == "default"
        # Every engine reads through the one backend the lifespan opened.
        assert state.graph._storage is storage
        assert state.traversal._storage is storage
        assert state.merger._storage is storage
        assert state.search._storage is storage

    assert stub_prewarm.wait(10), "the pre-warm thread never ran"
    assert core._state.storage is None
    with pytest.raises(GraphMemError, match="Server not initialised"):
        core._require_state()
    with pytest.raises(DatabaseError, match="not initialized"):
        await storage.get_entity_by_id("anything")


async def test_lifespan_derives_the_active_graph_name_from_the_db_stem(
    tmp_path: Path, clean_global_state: None, stub_prewarm: threading.Event
) -> None:
    """A non-default database file names the active graph after itself."""
    core._state.config = Config(db_path=tmp_path / ".graphmem" / "work.db")

    async with core._lifespan(core.mcp):
        assert core._state._active_graph == "work"

    assert stub_prewarm.wait(10)


async def test_lifespan_survives_a_failing_prewarm(
    tmp_path: Path, clean_global_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that cannot load must not stop the server from serving."""
    attempted = threading.Event()

    def exploding_load(self: EmbeddingEngine) -> None:
        attempted.set()
        raise RuntimeError("no such model")

    monkeypatch.setattr(EmbeddingEngine, "_ensure_model_loaded", exploding_load)
    core._state.config = Config(db_path=tmp_path / ".graphmem" / "graph.db")

    async with core._lifespan(core.mcp):
        assert core._require_state().storage is not None

    assert attempted.wait(10)


async def test_lifespan_shutdown_cleans_up_the_dashboard(
    tmp_path: Path, clean_global_state: None, stub_prewarm: threading.Event
) -> None:
    """A dashboard started during the session is torn down with the server."""

    class _FakeRunner:
        def __init__(self) -> None:
            self.cleanups = 0

        async def cleanup(self) -> None:
            self.cleanups += 1

    runner = _FakeRunner()
    core._state.config = Config(db_path=tmp_path / ".graphmem" / "graph.db")

    async with core._lifespan(core.mcp):
        core._state._ui_runner = runner
        core._state._ui_url = "http://127.0.0.1:1234/?token=secret"
        core._state._ui_port = 1234

    assert runner.cleanups == 1
    assert core._state._ui_runner is None
    assert core._state._ui_url is None
    assert core._state._ui_port is None
    assert stub_prewarm.wait(10)


async def test_lifespan_shutdown_survives_a_failing_dashboard_cleanup(
    tmp_path: Path, clean_global_state: None, stub_prewarm: threading.Event
) -> None:
    """A runner that refuses to shut down must not block storage teardown."""

    class _BrokenRunner:
        async def cleanup(self) -> None:
            raise RuntimeError("runner already gone")

    core._state.config = Config(db_path=tmp_path / ".graphmem" / "graph.db")

    async with core._lifespan(core.mcp):
        storage = core._require_state().storage
        core._state._ui_runner = _BrokenRunner()
        core._state._ui_url = "http://127.0.0.1:1234/?token=secret"

    assert core._state._ui_runner is None
    assert core._state._ui_url is None
    assert core._state.storage is None
    with pytest.raises(DatabaseError, match="not initialized"):
        await storage.get_entity_by_id("anything")
    assert stub_prewarm.wait(10)
