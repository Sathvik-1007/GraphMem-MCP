"""Tests for multi-graph management tools, utility functions, and factory.

Covers: list_graphs, create_graph, switch_graph, delete_graph,
open_dashboard, _require_state, _error_response, create_server.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP

import graph_mem.server as server_mod
from graph_mem.graph.engine import GraphEngine
from graph_mem.graph.merge import EntityMerger
from graph_mem.graph.traversal import GraphTraversal
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import HybridSearch
from graph_mem.server import (
    _error_response,
    _require_state,
    create_graph,
    create_server,
    delete_graph,
    list_graphs,
    open_dashboard,
    switch_graph,
)
from graph_mem.utils import GraphMemError
from graph_mem.utils.config import Config
from graph_mem.utils.errors import EntityNotFoundError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_db(path: Path) -> None:
    """Create a minimal graphmem DB with required tables."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            properties TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS relationships (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT DEFAULT '',
            created_at TEXT
        );
        """
    )
    conn.close()


@pytest_asyncio.fixture
async def setup_server(tmp_path: Path):
    """Populate module-level _state so tool functions work.

    Also sets _graphmem_dir to a tmp dir with a default graph.db.
    """
    from graph_mem.storage import SQLiteBackend

    graphmem_dir = tmp_path / ".graphmem"
    graphmem_dir.mkdir()

    db_path = graphmem_dir / "graph.db"
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
    server_mod._state._graphmem_dir = graphmem_dir
    server_mod._state._active_graph = "default"

    yield graphmem_dir

    # Close whichever storage is currently active (may differ from original
    # if switch_graph replaced it). The original is already closed by
    # _switch_engines; closing twice is harmless on SQLiteBackend.
    current_storage = server_mod._state.storage
    if current_storage is not None:
        await current_storage.close()
    if current_storage is not storage:
        with contextlib.suppress(Exception):
            await storage.close()
    server_mod._state.storage = None
    server_mod._state.graph = None
    server_mod._state.traversal = None
    server_mod._state.merger = None
    server_mod._state.embeddings = None
    server_mod._state.search = None
    server_mod._state._graphmem_dir = None
    server_mod._state._active_graph = "default"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_no_error(result: dict[str, Any]) -> None:
    assert "error" not in result, f"Unexpected error: {result}"


# ═══════════════════════════════════════════════════════════════════════════
# _require_state
# ═══════════════════════════════════════════════════════════════════════════


def test_require_state_raises_when_uninitialised():
    """_require_state raises GraphMemError when storage is None."""
    old = server_mod._state.storage
    server_mod._state.storage = None
    try:
        with pytest.raises(GraphMemError, match="Server not initialised"):
            _require_state()
    finally:
        server_mod._state.storage = old


def test_require_state_returns_initialized(setup_server):
    """_require_state returns InitializedState when all fields set."""
    state = _require_state()
    assert state.storage is not None
    assert state.graph is not None
    assert state.config is not None


# ═══════════════════════════════════════════════════════════════════════════
# _error_response
# ═══════════════════════════════════════════════════════════════════════════


def test_error_response_basic():
    """_error_response returns structured dict with error info."""
    exc = GraphMemError("something broke")
    resp = _error_response(exc, tool_name="test_tool")
    assert resp["error"] is True
    assert resp["error_type"] == "GraphMemError"
    assert "something broke" in resp["message"]


def test_error_response_with_suggestions():
    """_error_response includes suggestions from EntityNotFoundError."""
    exc = EntityNotFoundError("Ghost", suggestions=["Ghast", "Gust"])
    resp = _error_response(exc)
    assert resp["error"] is True
    assert resp["suggestions"] == ["Ghast", "Gust"]


def test_error_response_with_details():
    """_error_response includes details from GraphMemError."""
    exc = GraphMemError("bad", details={"field": "name"})
    resp = _error_response(exc)
    assert resp["details"] == {"field": "name"}


# ═══════════════════════════════════════════════════════════════════════════
# create_server
# ═══════════════════════════════════════════════════════════════════════════


def test_create_server_returns_fastmcp():
    """create_server returns a FastMCP instance."""
    server = create_server()
    assert isinstance(server, FastMCP)


def test_create_server_with_config():
    """create_server stores config in _state when provided."""
    cfg = Config(db_path=Path("/tmp/fake.db"))
    old = server_mod._state.config
    try:
        server = create_server(cfg)
        assert isinstance(server, FastMCP)
        assert server_mod._state.config is cfg
    finally:
        server_mod._state.config = old


# ═══════════════════════════════════════════════════════════════════════════
# list_graphs
# ═══════════════════════════════════════════════════════════════════════════


async def test_list_graphs_single_default(setup_server):
    """list_graphs finds the default graph.db."""
    result = await list_graphs()
    _assert_no_error(result)
    assert result["count"] >= 1
    assert result["active_graph"] == "default"
    names = {g["name"] for g in result["graphs"]}
    assert "default" in names
    # Active flag check
    default_graph = next(g for g in result["graphs"] if g["name"] == "default")
    assert default_graph["active"] is True


async def test_list_graphs_multiple(setup_server):
    """list_graphs finds multiple .db files."""
    graphmem_dir = setup_server
    # Create an extra DB
    extra_path = graphmem_dir / "research.db"
    _init_db(extra_path)

    result = await list_graphs()
    _assert_no_error(result)
    assert result["count"] >= 2
    names = {g["name"] for g in result["graphs"]}
    assert "default" in names
    assert "research" in names

    # Non-active graph should have active=False
    research = next(g for g in result["graphs"] if g["name"] == "research")
    assert research["active"] is False


async def test_list_graphs_uninitialised():
    """list_graphs returns error when _graphmem_dir is None."""
    old = server_mod._state._graphmem_dir
    server_mod._state._graphmem_dir = None
    try:
        result = await list_graphs()
        assert result.get("error") is True
    finally:
        server_mod._state._graphmem_dir = old


# ═══════════════════════════════════════════════════════════════════════════
# create_graph
# ═══════════════════════════════════════════════════════════════════════════


async def test_create_graph_basic(setup_server):
    """create_graph creates a new .db file."""
    graphmem_dir = setup_server
    result = await create_graph(name="test-graph")
    _assert_no_error(result)
    assert result["status"] == "created"
    assert result["name"] == "test-graph"
    assert (graphmem_dir / "test-graph.db").exists()


async def test_create_graph_duplicate(setup_server):
    """create_graph returns error for existing graph."""
    await create_graph(name="dupe")
    result = await create_graph(name="dupe")
    assert result.get("error") is True
    assert result["error_type"] == "AlreadyExists"


async def test_create_graph_invalid_name(setup_server):
    """create_graph rejects names with special chars."""
    result = await create_graph(name="bad name!")
    assert result.get("error") is True
    assert result["error_type"] == "ValidationError"


async def test_create_graph_invalid_name_dots(setup_server):
    """create_graph rejects names with dots."""
    result = await create_graph(name="has.dot")
    assert result.get("error") is True
    assert result["error_type"] == "ValidationError"


async def test_create_graph_default_name(setup_server):
    """create_graph with name='default' maps to graph.db which already exists."""
    result = await create_graph(name="default")
    assert result.get("error") is True
    assert result["error_type"] == "AlreadyExists"


# ═══════════════════════════════════════════════════════════════════════════
# switch_graph
# ═══════════════════════════════════════════════════════════════════════════


async def test_switch_graph_basic(setup_server):
    """switch_graph switches to a different graph."""
    # Create a graph to switch to
    await create_graph(name="other")
    result = await switch_graph(name="other")
    _assert_no_error(result)
    assert result["status"] == "switched"
    assert result["name"] == "other"
    assert server_mod._state._active_graph == "other"


async def test_switch_graph_already_active(setup_server):
    """switch_graph returns already_active when switching to current."""
    result = await switch_graph(name="default")
    _assert_no_error(result)
    assert result["status"] == "already_active"


async def test_switch_graph_nonexistent(setup_server):
    """switch_graph returns error for missing graph."""
    result = await switch_graph(name="nope")
    assert result.get("error") is True
    assert result["error_type"] == "NotFound"


async def test_switch_graph_and_back(setup_server):
    """switch_graph can round-trip between graphs."""
    await create_graph(name="tmp")
    await switch_graph(name="tmp")
    assert server_mod._state._active_graph == "tmp"

    result = await switch_graph(name="default")
    _assert_no_error(result)
    assert result["status"] == "switched"
    assert server_mod._state._active_graph == "default"


# ═══════════════════════════════════════════════════════════════════════════
# delete_graph
# ═══════════════════════════════════════════════════════════════════════════


async def test_delete_graph_basic(setup_server):
    """delete_graph removes the .db file."""
    graphmem_dir = setup_server
    await create_graph(name="to-delete")
    assert (graphmem_dir / "to-delete.db").exists()

    result = await delete_graph(name="to-delete")
    _assert_no_error(result)
    assert result["status"] == "deleted"
    assert not (graphmem_dir / "to-delete.db").exists()


async def test_delete_graph_active(setup_server):
    """delete_graph refuses to delete the active graph."""
    result = await delete_graph(name="default")
    assert result.get("error") is True
    assert result["error_type"] == "ValidationError"
    assert "active" in result["message"].lower()


async def test_delete_graph_nonexistent(setup_server):
    """delete_graph returns error for missing graph."""
    result = await delete_graph(name="ghost")
    assert result.get("error") is True
    assert result["error_type"] == "NotFound"


async def test_delete_graph_with_wal_shm(setup_server):
    """delete_graph also removes -wal and -shm files."""
    graphmem_dir = setup_server
    await create_graph(name="waltest")
    # Simulate WAL/SHM files
    (graphmem_dir / "waltest.db-wal").touch()
    (graphmem_dir / "waltest.db-shm").touch()

    result = await delete_graph(name="waltest")
    _assert_no_error(result)
    assert not (graphmem_dir / "waltest.db").exists()
    assert not (graphmem_dir / "waltest.db-wal").exists()
    assert not (graphmem_dir / "waltest.db-shm").exists()
    assert len(result["deleted_files"]) == 3


# ═══════════════════════════════════════════════════════════════════════════
# open_dashboard
# ═══════════════════════════════════════════════════════════════════════════


async def test_open_dashboard_already_running(setup_server):
    """open_dashboard returns existing URL if already running."""
    server_mod._state._ui_url = "http://127.0.0.1:9999"
    try:
        result = await open_dashboard()
        assert result["status"] == "already_running"
        assert result["url"] == "http://127.0.0.1:9999"
    finally:
        server_mod._state._ui_url = None


async def test_open_dashboard_missing_dependency(setup_server):
    """open_dashboard returns error when aiohttp not installed."""
    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "aiohttp" or (isinstance(name, str) and name.startswith("aiohttp")):
            raise ImportError("No module named 'aiohttp'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        result = await open_dashboard()
        assert result.get("error") is True
        assert result["error_type"] == "MissingDependency"
        assert "aiohttp" in result["message"]
