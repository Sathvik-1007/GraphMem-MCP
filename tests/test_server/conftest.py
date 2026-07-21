"""Shared fixtures for MCP tool tests.

The tool functions read their engines from the module-level ``_state`` in
``graph_mem.tools._core``, so exercising them in-process means populating that
state and tearing it down again.  One fixture does that for every test module
in this package.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest_asyncio

if TYPE_CHECKING:
    from pathlib import Path

import graph_mem.server as server_mod
from graph_mem.graph.engine import GraphEngine
from graph_mem.graph.merge import EntityMerger
from graph_mem.graph.traversal import GraphTraversal
from graph_mem.semantic.embeddings import EmbeddingEngine
from graph_mem.semantic.search import HybridSearch
from graph_mem.utils.config import Config


@pytest_asyncio.fixture
async def setup_server(tmp_path: Path):
    """Populate ``_state`` with real engines over a temporary database.

    Yields the ``.graphmem/`` directory so multi-graph tests can create sibling
    graph files next to the default ``graph.db``.
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

    # switch_graph may have replaced the backend; close whichever is current
    # and, if it differs, the original too.  Closing twice is a no-op.
    current_storage = server_mod._state.storage
    if current_storage is not None:
        with contextlib.suppress(Exception):
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
    server_mod._state.config = None
    server_mod._state._graphmem_dir = None
    server_mod._state._active_graph = "default"
