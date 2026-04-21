"""Multi-graph management tools — list, create, switch, delete graphs."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path
from typing import Any

from graph_mem.graph import EntityMerger, GraphEngine, GraphTraversal
from graph_mem.semantic import EmbeddingEngine, HybridSearch
from graph_mem.storage import create_backend
from graph_mem.utils import GraphMemError, get_logger

from ._core import _error_response, _require_state, _state, mcp

log = get_logger("server")


def _get_graphmem_dir() -> Path:
    """Return the .graphmem directory, raising if not initialised."""
    d = _state._graphmem_dir
    if d is None:
        raise GraphMemError("Server not initialised.")
    return d


async def _switch_engines(db_path: Path, graph_name: str) -> dict[str, Any]:
    """Close current storage and reinitialise all engines for a new DB."""
    state = _require_state()

    # Close current storage
    await state.storage.close()

    # Create new storage
    storage = create_backend(state.config.backend_type, db_path=db_path)
    await storage.initialize()

    # Reinitialise engines with new storage
    embeddings = EmbeddingEngine(
        model_name=state.config.embedding_model,
        use_onnx=state.config.use_onnx,
        device=state.config.embedding_device,
        cache_size=state.config.cache_size,
    )
    await embeddings.initialize(storage)

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings, alpha=state.config.rrf_alpha)

    # Update global state
    _state.storage = storage
    _state.graph = graph
    _state.traversal = traversal
    _state.merger = merger
    _state.embeddings = embeddings
    _state.search = search
    _state._active_graph = graph_name

    # Get stats for response
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


@mcp.tool()
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
            stem = db_file.stem
            name = "default" if stem == "graph" else stem
            stat = db_file.stat()

            # Open each DB briefly to get counts (off event loop)
            def _sync_counts(path: str = str(db_file)) -> tuple[int, int, int]:
                try:
                    conn = sqlite3.connect(path)
                    ec = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
                    rc = conn.execute("SELECT count(*) FROM relationships").fetchone()[0]
                    oc = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
                    conn.close()
                    return ec, rc, oc
                except (sqlite3.Error, OSError):
                    return -1, -1, -1

            ent_count, rel_count, obs_count = await asyncio.get_event_loop().run_in_executor(
                None, _sync_counts
            )

            graphs.append(
                {
                    "name": name,
                    "file": db_file.name,
                    "entities": ent_count,
                    "relationships": rel_count,
                    "observations": obs_count,
                    "size_bytes": stat.st_size,
                    "last_modified": stat.st_mtime,
                    "active": name == _state._active_graph,
                }
            )

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


@mcp.tool()
async def create_graph(
    name: str,
) -> dict[str, Any]:
    """Create a new empty knowledge graph.

    Creates a new .db file in the .graphmem/ directory.
    The graph can then be activated with switch_graph.

    Args:
        name: Name for the new graph (alphanumeric, hyphens, underscores).
              Cannot be 'graph' (reserved for default).
    """
    try:
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return {
                "error": True,
                "error_type": "ValidationError",
                "message": (
                    f"Graph name must be alphanumeric with hyphens/underscores, got: {name!r}"
                ),
            }

        graphmem_dir = _get_graphmem_dir()
        db_name = "graph.db" if name == "default" else f"{name}.db"
        db_path = graphmem_dir / db_name

        if db_path.exists():
            return {
                "error": True,
                "error_type": "AlreadyExists",
                "message": f"Graph '{name}' already exists at {db_path}",
            }

        # Create and initialise the DB (creates tables)
        state = _require_state()
        storage = create_backend(state.config.backend_type, db_path=db_path)
        await storage.initialize()
        await storage.close()

        return {
            "name": name,
            "file": db_name,
            "db_path": str(db_path),
            "status": "created",
            "message": f"Graph '{name}' created. Use switch_graph to activate it.",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="create_graph")
    except (sqlite3.Error, OSError, ValueError) as exc:
        log.exception("Failed to create graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


@mcp.tool()
async def switch_graph(
    name: str,
) -> dict[str, Any]:
    """Switch the active knowledge graph.

    Closes the current graph's database connection and opens the specified
    graph instead. All subsequent tool calls will operate on the new graph.

    Args:
        name: Name of the graph to switch to. Use 'default' for the
              default graph (graph.db).
    """
    try:
        if name == _state._active_graph:
            return {
                "name": name,
                "status": "already_active",
                "message": f"Graph '{name}' is already the active graph.",
            }

        graphmem_dir = _get_graphmem_dir()
        db_name = "graph.db" if name == "default" else f"{name}.db"
        db_path = graphmem_dir / db_name

        if not db_path.exists():
            return {
                "error": True,
                "error_type": "NotFound",
                "message": (f"Graph '{name}' not found. Use list_graphs to see available graphs."),
            }

        async with _state._switch_lock:
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


@mcp.tool()
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
        if name == _state._active_graph:
            return {
                "error": True,
                "error_type": "ValidationError",
                "message": (
                    f"Cannot delete the active graph '{name}'. Switch to a different graph first."
                ),
            }

        graphmem_dir = _get_graphmem_dir()
        db_name = "graph.db" if name == "default" else f"{name}.db"
        db_path = graphmem_dir / db_name

        if not db_path.exists():
            return {
                "error": True,
                "error_type": "NotFound",
                "message": f"Graph '{name}' not found.",
            }

        # Remove DB file and WAL/SHM files
        deleted_files = []
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
                deleted_files.append(f.name)

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
