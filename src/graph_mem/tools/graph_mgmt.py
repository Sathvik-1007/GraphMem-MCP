"""Multi-graph management tools — list, create, switch, delete graphs.

Every tool here maps a caller-supplied *graph name* onto a file inside the
``.graphmem/`` directory.  That mapping is the module's trust boundary: the
name arrives from a language model and is used to build a filesystem path that
gets opened, written to, or deleted.  All four tools route through
:func:`_resolve_graph_path`, which is the only place that mapping happens.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from graph_mem.graph import EntityMerger, GraphEngine, GraphTraversal
from graph_mem.semantic import HybridSearch
from graph_mem.storage import create_backend
from graph_mem.utils import GraphMemError, ValidationError, get_logger

from ._core import _error_response, _exclusive_engines, _require_state, _state, mcp, tool

log = get_logger("server")

# ── Graph-name grammar ───────────────────────────────────────────────────────
# A graph name must be a single bare token.  This deliberately excludes path
# separators, "..", drive letters, leading "~", NUL bytes, and absolute paths,
# so no caller-supplied name can address a file outside .graphmem/.
_GRAPH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# Upper bound on a graph name.  Chosen so that "<name>.db-journal" stays well
# inside the 255-byte per-component limit that ext4, APFS, XFS, and NTFS all
# share, leaving room for SQLite's longest sidecar suffix.
_MAX_GRAPH_NAME_LENGTH = 64

# The default graph is addressed as "default" but stored as "graph.db", so the
# literal stem "graph" is reserved: allowing it would give one database two
# distinct names and let ``delete_graph("graph")`` bypass a guard written
# against ``"default"``.
_DEFAULT_GRAPH_NAME = "default"
_DEFAULT_GRAPH_STEM = "graph"


def _get_graphmem_dir() -> Path:
    """Return the .graphmem directory, raising if not initialised."""
    d = _state._graphmem_dir
    if d is None:
        raise GraphMemError("Server not initialised.")
    return d


def _graph_display_name(stem: str) -> str:
    """Invert :func:`_resolve_graph_path`'s stem mapping for display."""
    return _DEFAULT_GRAPH_NAME if stem == _DEFAULT_GRAPH_STEM else stem


def _resolve_graph_path(name: str) -> Path:
    """Map a caller-supplied graph name to its database path in ``.graphmem/``.

    This is the single trust boundary for multi-graph tools.  Validation runs
    in two independent layers so a bug in either one alone is not exploitable:

    1. The name must match :data:`_GRAPH_NAME_PATTERN` and fit within
       :data:`_MAX_GRAPH_NAME_LENGTH`.  That rejects every character usable to
       traverse or escape a directory.
    2. The resolved path's parent must still be the resolved ``.graphmem/``
       directory.  This catches anything layer 1 missed and any escape via a
       symlinked component.

    Args:
        name: Graph name from the caller.  ``"default"`` maps to ``graph.db``.

    Returns:
        Absolute, resolved path to the graph's SQLite file.  The file may or
        may not exist; callers check existence themselves.

    Raises:
        ValidationError: The name is empty, too long, contains a character
            outside the grammar, is the reserved stem ``"graph"``, or resolves
            outside ``.graphmem/``.
        GraphMemError: The server has not been initialised.
    """
    if not name:
        raise ValidationError("Graph name must not be empty.")
    if len(name) > _MAX_GRAPH_NAME_LENGTH:
        raise ValidationError(
            f"Graph name must be at most {_MAX_GRAPH_NAME_LENGTH} characters, got {len(name)}."
        )
    if not _GRAPH_NAME_PATTERN.match(name):
        raise ValidationError(
            f"Graph name must contain only letters, digits, hyphens, and underscores, got: {name!r}"
        )
    if name == _DEFAULT_GRAPH_STEM:
        raise ValidationError(
            f"Graph name {_DEFAULT_GRAPH_STEM!r} is reserved for the default "
            f"graph's file. Use {_DEFAULT_GRAPH_NAME!r} to address it."
        )

    graphmem_dir = _get_graphmem_dir().resolve()
    stem = _DEFAULT_GRAPH_STEM if name == _DEFAULT_GRAPH_NAME else name
    db_path = (graphmem_dir / f"{stem}.db").resolve()

    # Layer 2: containment re-check after resolution.
    if db_path.parent != graphmem_dir:
        raise ValidationError(f"Graph {name!r} resolves outside the .graphmem directory.")

    return db_path


def _repoint_dashboard(
    storage: Any,
    graph: GraphEngine,
    search: HybridSearch,
    db_path: Path,
) -> None:
    """Point a running dashboard at the newly activated engines.

    No-op when no dashboard is running.  Failures are logged rather than
    raised: the graph switch itself has already succeeded, and a stale
    dashboard is not worth undoing it for.
    """
    app = _state._ui_app
    if app is None:
        return
    try:
        from graph_mem.ui._keys import db_path_key, graph_key, search_key, storage_key

        app[storage_key] = storage
        app[search_key] = search
        app[graph_key] = graph
        app[db_path_key] = str(db_path)
    except (ImportError, KeyError, TypeError):
        log.warning("Could not repoint the running dashboard at the new graph", exc_info=True)


async def _switch_engines(db_path: Path, graph_name: str) -> dict[str, Any]:
    """Rebuild every engine against *db_path* and retire the previous storage.

    The caller must already hold exclusive access via
    :func:`~graph_mem.tools._core._exclusive_engines`; this function does not
    acquire it.

    Ordering matters for failure atomicity: the replacement backend is fully
    opened *before* anything is swapped, and the previous backend is closed
    only after the swap succeeds.  If opening the new backend fails, every
    engine is left pointing at the still-open previous backend and the error
    propagates, so a failed switch is a no-op rather than a server that can
    never serve another request.
    """
    state = _require_state()

    old_storage = state.storage
    # The embedding engine holds a loaded model — expensive to rebuild, and
    # independent of which database it persists to — so it is reused.
    embeddings = state.embeddings

    storage = create_backend(state.config.backend_type, db_path=db_path)
    try:
        await storage.initialize()
    except BaseException:
        # Nothing has been swapped yet; discard the half-built backend and
        # leave the server exactly as it was.
        with contextlib.suppress(Exception):
            await storage.close()
        raise

    # initialize() reports failure by setting `available = False` rather than
    # raising, so there is no partial-failure window to unwind here.
    embeddings.set_storage(storage)
    await embeddings.initialize(storage)

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings, alpha=state.config.rrf_alpha)

    _state.storage = storage
    _state.graph = graph
    _state.traversal = traversal
    _state.merger = merger
    _state.embeddings = embeddings
    _state.search = search
    _state._active_graph = graph_name

    # A dashboard started by open_dashboard captured the previous engines in
    # its aiohttp app.  Repoint it, or every request it serves after this
    # would hit a closed backend with no way to recover short of a restart.
    _repoint_dashboard(storage, graph, search, db_path)

    # Safe now: no engine and no in-flight tool call references old_storage.
    await old_storage.close()

    entity_count = await storage.count_entities()
    relationship_count = await storage.count_relationships()
    observation_count = await storage.count_observations()

    return {
        "name": graph_name,
        "db_path": str(db_path),
        "entities": entity_count,
        "relationships": relationship_count,
        "observations": observation_count,
    }


@tool()
async def list_graphs() -> dict[str, Any]:
    """List all available knowledge graphs with summary statistics.

    Scans the .graphmem/ directory for .db files. Each file represents a
    separate knowledge graph. Returns name, entity/relationship/observation
    counts, file size, and last modified time. The active graph is marked.
    """
    try:
        graphmem_dir = _get_graphmem_dir()
        graphs: list[dict[str, Any]] = []

        for db_file in sorted(graphmem_dir.glob("*.db")):
            name = _graph_display_name(db_file.stem)
            stat = db_file.stat()

            # Open each DB briefly to get counts (off event loop).  A file in
            # .graphmem/ that is not a graph-mem database is reported with its
            # error rather than with fabricated counts — a caller must be able
            # to tell "this graph is empty" from "this graph is unreadable".
            def _sync_counts(path: str = str(db_file)) -> tuple[int, int, int] | str:
                conn: sqlite3.Connection | None = None
                try:
                    conn = sqlite3.connect(path)
                    ec = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
                    rc = conn.execute("SELECT count(*) FROM relationships").fetchone()[0]
                    oc = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
                    return int(ec), int(rc), int(oc)
                except (sqlite3.Error, OSError) as exc:
                    return f"{type(exc).__name__}: {exc}"
                finally:
                    if conn:
                        conn.close()

            counts = await asyncio.get_running_loop().run_in_executor(None, _sync_counts)

            entry: dict[str, Any] = {
                "name": name,
                "file": db_file.name,
                "size_bytes": stat.st_size,
                "last_modified": stat.st_mtime,
                "active": name == _state._active_graph,
            }
            if isinstance(counts, str):
                log.warning("Cannot read counts from %s: %s", db_file, counts)
                entry["unreadable"] = True
                entry["error"] = counts
            else:
                entry["entities"], entry["relationships"], entry["observations"] = counts
            graphs.append(entry)

        return {
            "graphs": graphs,
            "count": len(graphs),
            "active_graph": _state._active_graph,
            "graphmem_dir": str(graphmem_dir),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="list_graphs")
    except (sqlite3.Error, OSError) as exc:
        log.exception("Failed to list graphs")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


@tool()
async def create_graph(
    name: str,
) -> dict[str, Any]:
    """Create a new empty knowledge graph.

    Creates a new .db file in the .graphmem/ directory.
    The graph can then be activated with switch_graph.

    Args:
        name: Name for the new graph. Letters, digits, hyphens, and
              underscores only, at most 64 characters. Cannot be 'graph'
              (reserved for the default graph's file — use 'default').
    """
    try:
        db_path = _resolve_graph_path(name)

        if db_path.exists():
            return {
                "error": True,
                "error_type": "AlreadyExists",
                "message": f"Graph '{name}' already exists at {db_path}",
            }

        # Create and initialise the DB (creates tables)
        state = _require_state()
        storage = create_backend(state.config.backend_type, db_path=db_path)
        try:
            await storage.initialize()
        finally:
            await storage.close()

        return {
            "name": name,
            "file": db_path.name,
            "db_path": str(db_path),
            "status": "created",
            "message": f"Graph '{name}' created. Use switch_graph to activate it.",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="create_graph")
    except (sqlite3.Error, OSError, ValueError) as exc:
        log.exception("Failed to create graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


# Registered with the raw FastMCP decorator, not the draining ``tool()``
# wrapper: this handler *is* the writer in the readers/writer handshake, and
# registering itself as a reader would make it wait for itself to finish.
@mcp.tool()
async def switch_graph(
    name: str,
) -> dict[str, Any]:
    """Switch the active knowledge graph.

    Waits for in-flight tool calls to finish, then opens the specified graph
    and closes the previous one. All subsequent tool calls operate on the new
    graph. A failure to open the new graph leaves the current one active.

    Args:
        name: Name of the graph to switch to. Use 'default' for the
              default graph (graph.db).
    """
    try:
        db_path = _resolve_graph_path(name)

        if not db_path.exists():
            return {
                "error": True,
                "error_type": "NotFound",
                "message": (f"Graph '{name}' not found. Use list_graphs to see available graphs."),
            }

        async with _exclusive_engines():
            # Re-checked under the lock: a concurrent switch may have made
            # this graph active between the call arriving and the drain
            # completing, and switching to the already-active graph would
            # needlessly close and reopen a healthy backend.
            if name == _state._active_graph:
                return {
                    "name": name,
                    "status": "already_active",
                    "message": f"Graph '{name}' is already the active graph.",
                }
            stats = await _switch_engines(db_path, name)

        return {
            **stats,
            "status": "switched",
            "message": f"Switched to graph '{name}' ({stats['entities']} entities, "
            f"{stats['relationships']} relationships, {stats['observations']} observations).",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="switch_graph")
    except (sqlite3.Error, OSError) as exc:
        log.exception("Failed to switch graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


@tool()
async def delete_graph(
    name: str,
) -> dict[str, Any]:
    """Delete a knowledge graph permanently.

    Removes the .db file and associated WAL/SHM files from the
    .graphmem/ directory. Cannot delete the currently active graph
    — switch to a different graph first.

    Args:
        name: Name of the graph to delete. Cannot be the active graph.
    """
    try:
        db_path = _resolve_graph_path(name)

        # Compared as resolved paths, not as name strings: two different names
        # can address the same file, so a string comparison is not a guard.
        active_path = _resolve_graph_path(_state._active_graph)
        if db_path == active_path:
            return {
                "error": True,
                "error_type": "ValidationError",
                "message": (
                    f"Cannot delete the active graph '{name}'. Switch to a different graph first."
                ),
            }

        if not db_path.exists():
            return {
                "error": True,
                "error_type": "NotFound",
                "message": f"Graph '{name}' not found.",
            }

        # Remove the database and the two sidecar files WAL mode creates.
        deleted_files = []
        for suffix in ("", "-wal", "-shm"):
            sidecar = Path(str(db_path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
                deleted_files.append(sidecar.name)

        return {
            "name": name,
            "status": "deleted",
            "deleted_files": deleted_files,
            "message": f"Graph '{name}' deleted permanently.",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_graph")
    except OSError as exc:
        log.exception("Failed to delete graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}
