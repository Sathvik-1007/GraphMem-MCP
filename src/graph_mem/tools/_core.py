"""Shared state, helpers, and MCP app instance for all tool modules.

Every tool module imports ``mcp``, ``_require_state``, ``_error_response``,
and the embed helpers from here.  This module owns no tool registrations
itself — it is purely infrastructure.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from graph_mem.graph.engine import ObservationResult

from mcp.server.fastmcp import FastMCP

from graph_mem.graph import EntityMerger, GraphEngine, GraphTraversal
from graph_mem.semantic import EmbeddingEngine, HybridSearch
from graph_mem.storage import StorageBackend, create_backend
from graph_mem.utils import Config, GraphMemError, get_logger, load_config, setup_logging
from graph_mem.utils.errors import EntityNotFoundError

log = get_logger("server")

# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    """Mutable container for shared engine instances.

    Populated by the lifespan context manager before the server starts
    accepting requests and cleared on shutdown.
    """

    config: Config | None = None
    storage: StorageBackend | None = None
    graph: GraphEngine | None = None
    traversal: GraphTraversal | None = None
    merger: EntityMerger | None = None
    embeddings: EmbeddingEngine | None = None
    search: HybridSearch | None = None
    # UI dashboard state (managed by open_dashboard tool)
    _ui_url: str | None = None
    _ui_runner: Any | None = None
    # Multi-graph state
    _graphmem_dir: Path | None = None  # Path to .graphmem/ directory
    _active_graph: str = "default"  # Currently active graph name
    _switch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class InitializedState:
    """Narrowed view of :class:`AppState` after successful initialization.

    All fields are guaranteed non-None.  Returned by
    :func:`_require_state` so callers don't need individual assertions.
    """

    config: Config
    storage: StorageBackend
    graph: GraphEngine
    traversal: GraphTraversal
    merger: EntityMerger
    embeddings: EmbeddingEngine
    search: HybridSearch


_state = AppState()


def _require_state() -> InitializedState:
    """Return the global state or raise if the server is not initialised."""
    if (
        _state.config is None
        or _state.storage is None
        or _state.graph is None
        or _state.traversal is None
        or _state.merger is None
        or _state.embeddings is None
        or _state.search is None
    ):
        raise GraphMemError("Server not initialised.  Is the lifespan running?")
    return InitializedState(
        config=_state.config,
        storage=_state.storage,
        graph=_state.graph,
        traversal=_state.traversal,
        merger=_state.merger,
        embeddings=_state.embeddings,
        search=_state.search,
    )


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Initialise all engines on startup; close the storage on shutdown."""
    config = _state.config or load_config()
    setup_logging(config.log_level)
    log.info("Starting graph-mem server (backend=%s)", config.backend_type)

    # Create and initialize storage backend
    db_path = config.ensure_db_dir()
    storage = create_backend(config.backend_type, db_path=db_path)
    await storage.initialize()

    # Store the .graphmem directory and derive active graph name
    _state._graphmem_dir = db_path.parent
    db_stem = db_path.stem  # e.g. "graph" from "graph.db"
    _state._active_graph = db_stem if db_stem != "graph" else "default"

    embeddings = EmbeddingEngine(
        model_name=config.embedding_model,
        use_onnx=config.use_onnx,
        device=config.embedding_device,
        cache_size=config.cache_size,
    )
    # initialize() is lightweight — it stores config and reads DB metadata
    # but does NOT load the PyTorch model.  The model loads lazily on the
    # first embed() call, keeping MCP startup fast (< 2 seconds).
    await embeddings.initialize(storage)

    # Pre-warm: load the embedding model in a background thread so the first
    # search_nodes call doesn't block for 30+ seconds (which would exceed
    # typical MCP client timeouts like OpenCode's 30 000 ms default).
    def _prewarm() -> None:
        try:
            embeddings._ensure_model_loaded()
            log.info("Embedding model pre-warmed successfully in background thread")
        except Exception:
            log.warning("Background model pre-warm failed — will retry on first use", exc_info=True)

    prewarm_thread = threading.Thread(target=_prewarm, daemon=True, name="embedding-prewarm")
    prewarm_thread.start()

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings, alpha=config.rrf_alpha)

    _state.config = config
    _state.storage = storage
    _state.graph = graph
    _state.traversal = traversal
    _state.merger = merger
    _state.embeddings = embeddings
    _state.search = search

    log.info(
        "graph-mem server ready (backend=%s, embeddings=%s)",
        storage.backend_type,
        embeddings.available,
    )
    yield

    # Shutdown
    log.info("Shutting down graph-mem server")
    # Clean up UI dashboard server if running
    if _state._ui_runner is not None:
        try:
            await _state._ui_runner.cleanup()
        except Exception:
            log.debug("UI runner cleanup error (ignored)", exc_info=True)
        _state._ui_runner = None
        _state._ui_url = None
    await storage.close()
    _state.storage = None
    _state.graph = None
    _state.traversal = None
    _state.merger = None
    _state.embeddings = None
    _state.search = None


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp = FastMCP("graph-mem", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Helper: structured error response
# ---------------------------------------------------------------------------


def _error_response(exc: Exception, *, tool_name: str = "") -> dict[str, Any]:
    """Build a structured MCP error response dict and log the failure."""
    label = f"{tool_name}: " if tool_name else ""
    log.debug("%sfailed: %s: %s", label, type(exc).__name__, exc)
    resp: dict[str, Any] = {
        "error": True,
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    if isinstance(exc, EntityNotFoundError) and exc.suggestions:
        resp["suggestions"] = exc.suggestions
    if isinstance(exc, GraphMemError) and exc.details:
        resp["details"] = exc.details
    return resp


# ---------------------------------------------------------------------------
# Helper: compute and store embeddings for entities / observations
# ---------------------------------------------------------------------------
# These live at the server layer (rather than in graph or semantic) because
# they need access to *both* the graph engine (to fetch entity text) and
# the embedding engine (to compute vectors), and those two are only wired
# together at the server level via AppState.
# ---------------------------------------------------------------------------


async def _embed_entities(entity_ids: list[str]) -> None:
    """Compute and upsert embeddings for the given entity IDs.

    Silently skips if the embedding engine is not available.
    """
    state = _require_state()

    if not state.embeddings.available or not entity_ids:
        return

    texts: list[str] = []
    valid_ids: list[str] = []
    for eid in entity_ids:
        try:
            entity = await state.graph.get_entity_by_id(eid)
            texts.append(entity.embedding_text)
            valid_ids.append(eid)
        except GraphMemError as exc:
            log.debug("Skipping entity %s during embedding: %s", eid, exc)
            continue

    if not texts:
        return

    vectors = await state.embeddings.embed(texts)
    for eid, vec in zip(valid_ids, vectors, strict=True):
        if vec is not None:
            await state.embeddings.upsert_entity_embedding(eid, vec)


async def _embed_observations(obs_results: list[ObservationResult]) -> None:
    """Compute and upsert embeddings for newly created observations.

    Each element of *obs_results* must have ``id`` and ``content`` keys.
    """
    state = _require_state()

    if not state.embeddings.available or not obs_results:
        return

    texts = [str(o["content"]) for o in obs_results]
    ids = [str(o["id"]) for o in obs_results]

    vectors = await state.embeddings.embed(texts)
    for oid, vec in zip(ids, vectors, strict=True):
        if vec is not None:
            await state.embeddings.upsert_observation_embedding(oid, vec)
