"""Shared state, helpers, and MCP app instance for all tool modules.

Every tool module imports ``mcp``, ``_require_state``, ``_error_response``,
and the embed helpers from here.  This module owns no tool registrations
itself — it is purely infrastructure.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine
    from pathlib import Path

    from graph_mem.graph.engine import ObservationResult

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from graph_mem.graph import EntityMerger, GraphEngine, GraphTraversal
from graph_mem.semantic import EmbeddingEngine, HybridSearch
from graph_mem.storage import SQLiteBackend, create_backend
from graph_mem.utils import Config, GraphMemError, get_logger, load_config, setup_logging
from graph_mem.utils.errors import EntityNotFoundError, ValidationError

log = get_logger("server")

# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------


def _new_idle_event() -> asyncio.Event:
    """Return an already-set Event — zero operations are in flight at startup."""
    event = asyncio.Event()
    event.set()
    return event


@dataclass
class AppState:
    """Mutable container for shared engine instances.

    Populated by the lifespan context manager before the server starts
    accepting requests and cleared on shutdown.
    """

    config: Config | None = None
    storage: SQLiteBackend | None = None
    graph: GraphEngine | None = None
    traversal: GraphTraversal | None = None
    merger: EntityMerger | None = None
    embeddings: EmbeddingEngine | None = None
    search: HybridSearch | None = None
    # UI dashboard state (managed by open_dashboard tool)
    _ui_url: str | None = None
    _ui_runner: Any | None = None
    _ui_port: int | None = None
    # The running dashboard's aiohttp Application.  Retained so switch_graph
    # can repoint it at the new engines; without this the dashboard keeps
    # serving from a closed backend after a switch.
    _ui_app: Any | None = None
    # Multi-graph state
    _graphmem_dir: Path | None = None  # Path to .graphmem/ directory
    _active_graph: str = "default"  # Currently active graph name
    # Held exclusively while swapping engines; acquired briefly by every tool
    # call so a switch cannot begin between a caller's entry and its first use
    # of the engines.
    _switch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Number of tool invocations currently holding references to the engines.
    _active_ops: int = 0
    # Set exactly when ``_active_ops == 0``.  ``switch_graph`` waits on this so
    # it never closes a storage backend that an in-flight tool is writing
    # through.
    _ops_idle: asyncio.Event = field(default_factory=_new_idle_event)


@dataclass
class InitializedState:
    """Narrowed view of :class:`AppState` after successful initialization.

    All fields are guaranteed non-None.  Returned by
    :func:`_require_state` so callers don't need individual assertions.
    """

    config: Config
    storage: SQLiteBackend
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
# Engine-swap safety
# ---------------------------------------------------------------------------
# The MCP runtime dispatches every request as its own task, so several tool
# handlers run concurrently against one shared set of engines.  ``switch_graph``
# replaces those engines and closes the previous storage backend.  Closing a
# backend that another task is mid-transaction on aborts that transaction and
# can lose the writes it already made, so a switch must wait until no tool call
# is using the engines.
#
# This is a readers/writer handshake built from the two primitives already on
# AppState:
#
#   readers (tool calls)  acquire ``_switch_lock`` just long enough to register
#                         themselves in ``_active_ops``, then release it and run
#   writer (switch_graph) holds ``_switch_lock`` for its whole duration, which
#                         blocks new readers from registering, and waits on
#                         ``_ops_idle`` until the already-registered readers
#                         finish
#
# A reader never needs the lock again after registering, so the writer's wait
# always terminates.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _op_guard() -> AsyncIterator[None]:
    """Pin the current engines for the duration of one tool invocation."""
    async with _state._switch_lock:
        _state._active_ops += 1
        _state._ops_idle.clear()
    try:
        yield
    finally:
        _state._active_ops -= 1
        if _state._active_ops == 0:
            _state._ops_idle.set()


@asynccontextmanager
async def _exclusive_engines() -> AsyncIterator[None]:
    """Block new tool invocations and wait for in-flight ones to finish.

    Used by ``switch_graph`` so it can close and replace the storage backend
    with no reader holding a reference to it.
    """
    async with _state._switch_lock:
        await _state._ops_idle.wait()
        yield


_AsyncTool = TypeVar("_AsyncTool", bound="Callable[..., Coroutine[Any, Any, Any]]")


def tool(*args: Any, **kwargs: Any) -> Callable[[_AsyncTool], _AsyncTool]:
    """Register an async MCP tool that participates in engine-swap draining.

    Drop-in replacement for ``@mcp.tool()``.  ``functools.wraps`` keeps the
    name, docstring, and signature intact, so FastMCP derives exactly the same
    JSON schema it would from the undecorated function.
    """

    def decorator(fn: _AsyncTool) -> _AsyncTool:
        @functools.wraps(fn)
        async def guarded(*call_args: Any, **call_kwargs: Any) -> Any:
            async with _op_guard():
                return await fn(*call_args, **call_kwargs)

        mcp.tool(*args, **kwargs)(guarded)
        # Return the guarded callable so direct in-process calls (tests, the
        # CLI) take the same path the MCP runtime does.
        return guarded  # type: ignore[return-value]

    return decorator


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
        _state._ui_port = None
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
    if isinstance(exc, EntityNotFoundError):
        log.debug("%snot found: %s", label, exc)
    else:
        log.warning("%sfailed: %s: %s", label, type(exc).__name__, exc)
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
# Helper: bound every caller-supplied count
# ---------------------------------------------------------------------------
# Every tool that accepts a limit, count, depth, or offset routes it through
# :func:`_clamp_limit` before the value reaches SQL or a traversal.  Two
# failures make this mandatory rather than tidy:
#
#   * SQLite treats a negative LIMIT as *unbounded*, not as empty.  A caller
#     passing ``limit=-1`` to a search became ``LIMIT -3`` on both the vector
#     and the FTS5 scan, pulling the entire index into memory.
#   * The consumer of every response is a language model with a fixed context
#     window.  A complete answer that does not fit is as useless as an error,
#     so responses are capped and say when the cap was applied.
#
# The ceilings differ by what a single result costs the reader, not by taste.
# ---------------------------------------------------------------------------

#: Browse/list ceiling.  One row is a compact entity or edge summary, so 500 is
#: about the largest page still cheap enough to hand to a model in one go.
#: Matches the bound ``list_entities``/``list_relationships`` already enforced.
MAX_LIST_LIMIT = 500

#: Search ceiling.  Lower than :data:`MAX_LIST_LIMIT` because each result also
#: carries nested relationships, and each retrieval channel is asked for
#: ``limit * CANDIDATE_MULTIPLIER`` (3) candidates — the work behind the
#: response grows several times faster than the response itself.
MAX_SEARCH_LIMIT = 100

#: Traversal ceiling for ``find_connections``/``get_subgraph``.  The traversal
#: layer's own 5000-node budget protects the database; this protects the
#: context window, where every node is a full entity record plus its path.
MAX_TRAVERSAL_RESULTS = 200

#: Ceiling on a collection nested inside a single response — the observations
#: and relationships ``get_entity`` attaches.  ``graph_health`` already flags
#: an entity with more than 15 observations as a hotspot worth compacting, so
#: 50 is generous for the normal case and still bounded for the pathological
#: one.  Use ``search_observations`` scoped by ``entity_name`` to see the rest.
MAX_NESTED_ITEMS = 50

#: Pagination ceiling.  Past a million rows, OFFSET paging is the wrong tool
#: (SQLite still walks every skipped row); the bound exists so a garbage offset
#: cannot turn one call into a full table scan.
MAX_OFFSET = 1_000_000


def _clamp_limit(value: int, *, maximum: int, minimum: int = 1) -> int:
    """Constrain a caller-supplied count to ``[minimum, maximum]``.

    Negatives and zero are raised to *minimum*, never passed through: a
    negative SQL ``LIMIT`` means "no limit" in SQLite, which is the opposite of
    what a caller asking for ``-1`` results could possibly want.

    Args:
        value: The count the caller supplied.
        maximum: Hard ceiling for this tool — one of the ``MAX_*`` constants.
        minimum: Floor, 1 for limits and 0 for offsets.

    Raises:
        ValidationError: *value* is not an integer.  Returning a default
            instead would silently answer a different question.
    """
    if not isinstance(value, int):
        raise ValidationError(
            f"Invalid input: expected an integer count, got {type(value).__name__}"
        )
    return max(minimum, min(value, maximum))


# ---------------------------------------------------------------------------
# Helper: validate caller-supplied input at the tool boundary
# ---------------------------------------------------------------------------
# A tool argument is a trust boundary: it comes from a language model that
# guesses key names and types.  Constructing a domain model straight from it
# turns ``{"name": null}`` into an ``AttributeError`` inside
# ``Entity.__post_init__``, which escapes the tool as an unstructured framework
# error the caller cannot act on.  These helpers coerce and reject *before* any
# domain object is built, and name the offending field when they refuse.
# ---------------------------------------------------------------------------

_ItemModel = TypeVar("_ItemModel", bound=BaseModel)


def _validate_items(items: Any, model: type[_ItemModel], *, field: str) -> list[_ItemModel]:
    """Validate a list of caller-supplied item dicts into *model* instances.

    Accepts either raw dicts (in-process callers) or already-parsed *model*
    instances (the MCP runtime parses them from the tool's JSON schema).

    Args:
        items: The list the caller supplied.
        model: Per-item pydantic model describing the accepted shape.
        field: Parameter name, used to build the error message.

    Raises:
        ValidationError: *items* is not a list, or an item is malformed.  The
            message names the index and the offending field.
    """
    if not isinstance(items, list):
        raise ValidationError(
            f"Invalid input: '{field}' must be a list of objects, got {type(items).__name__}"
        )
    validated: list[_ItemModel] = []
    for index, item in enumerate(items):
        try:
            validated.append(model.model_validate(item))
        except PydanticValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "<item>"
            raise ValidationError(
                f"Invalid input: {field}[{index}].{location}: {first['msg']}"
            ) from exc
    return validated


def _require_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    """Return *value* as a stripped string, or raise naming *field*.

    Args:
        value: The caller-supplied value.
        field: Parameter name, used to build the error message.
        allow_empty: Whether an empty/whitespace string is acceptable.  True
            for free-text queries, False for anything that has to name a row.

    Raises:
        ValidationError: *value* is not a string, or is empty when it may not be.
    """
    if not isinstance(value, str):
        raise ValidationError(
            f"Invalid input: '{field}' must be a string, got {type(value).__name__}"
        )
    text = value.strip()
    if not text and not allow_empty:
        raise ValidationError(f"Invalid input: '{field}' must not be empty")
    return text


def _require_text_list(value: Any, field: str, *, allow_empty_items: bool = False) -> list[str]:
    """Return *value* as a list of stripped strings, or raise naming *field*.

    Raises:
        ValidationError: *value* is not a list, or an element is not a usable
            string.  The message names the offending index.
    """
    if not isinstance(value, list):
        raise ValidationError(
            f"Invalid input: '{field}' must be a list of strings, got {type(value).__name__}"
        )
    return [
        _require_text(item, f"{field}[{index}]", allow_empty=allow_empty_items)
        for index, item in enumerate(value)
    ]


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


async def _embed_observation_texts(observations: list[tuple[str, str]]) -> None:
    """Compute and upsert embeddings for ``(observation_id, content)`` pairs.

    Takes only the two fields embedding actually needs, so a caller that has
    just an ID and a new body — ``update_observation`` — does not have to
    invent the rest of an :class:`ObservationResult` to call it.

    Silently skips if the embedding engine is not available.
    """
    state = _require_state()

    if not state.embeddings.available or not observations:
        return

    vectors = await state.embeddings.embed([content for _, content in observations])
    for (oid, _content), vec in zip(observations, vectors, strict=True):
        if vec is not None:
            await state.embeddings.upsert_observation_embedding(oid, vec)


async def _embed_observations(obs_results: list[ObservationResult]) -> None:
    """Compute and upsert embeddings for newly created observations.

    Each element of *obs_results* must have ``id`` and ``content`` keys.
    """
    await _embed_observation_texts([(str(o["id"]), str(o["content"])) for o in obs_results])
