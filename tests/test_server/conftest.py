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

import graph_mem.tools._core as core
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

    core._state.storage = storage
    core._state.graph = graph
    core._state.traversal = traversal
    core._state.merger = merger
    core._state.embeddings = embeddings
    core._state.search = search
    core._state.config = Config(db_path=db_path)
    core._state._graphmem_dir = graphmem_dir
    core._state._active_graph = "default"

    yield graphmem_dir

    # switch_graph may have replaced the backend; close whichever is current
    # and, if it differs, the original too.  Closing twice is a no-op.
    current_storage = core._state.storage
    if current_storage is not None:
        with contextlib.suppress(Exception):
            await current_storage.close()
    if current_storage is not storage:
        with contextlib.suppress(Exception):
            await storage.close()

    core._state.storage = None
    core._state.graph = None
    core._state.traversal = None
    core._state.merger = None
    core._state.embeddings = None
    core._state.search = None
    core._state.config = None
    core._state._graphmem_dir = None
    core._state._active_graph = "default"
