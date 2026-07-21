"""FastMCP server — the public entry point for all 28 Graph Memory MCP tools.

Holds no logic of its own.  Tool implementations live in
:mod:`graph_mem.tools` sub-modules; importing this module triggers their
registration on the shared ``mcp`` FastMCP instance and re-exports them, so
``graph_mem.server`` is the one import an embedder needs.

Only the public surface is re-exported.  Internals (``_state``,
``_error_response``, the embed helpers) belong to
:mod:`graph_mem.tools._core` and are imported from there by the code that
needs them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

# Importing the tools package runs every @tool() registration as a side
# effect; the names below are re-exported so callers have a single import.
from graph_mem.tools import (  # noqa: F401 — re-exported public API
    AppState,
    InitializedState,
    add_entities,
    add_observations,
    add_relationships,
    audit_graph,
    compact_observations,
    create_graph,
    delete_entities,
    delete_graph,
    delete_observations,
    delete_relationships,
    find_connections,
    find_paths,
    get_entity,
    get_subgraph,
    graph_health,
    list_entities,
    list_graphs,
    list_relationships,
    mcp,
    merge_entities,
    open_dashboard,
    read_graph,
    search_nodes,
    search_observations,
    suggest_connections,
    switch_graph,
    update_entity,
    update_observation,
    update_relationship,
)
from graph_mem.tools._core import _state

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from graph_mem.utils import Config

# ---------------------------------------------------------------------------
# Factory & entry point
# ---------------------------------------------------------------------------


def create_server(config: Config | None = None) -> FastMCP:
    """Create and return the FastMCP server instance.

    Optionally accepts a pre-built :class:`Config` for testing or
    programmatic use.  When *config* is ``None`` the lifespan will
    call :func:`load_config` itself.
    """
    if config is not None:
        _state.config = config
    return mcp


def run(
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
) -> None:
    """Start the MCP server.

    Args:
        transport: ``"stdio"`` (default) for CLI usage,
                   ``"sse"`` or ``"streamable-http"`` for network usage.
    """
    mcp.run(transport=transport)
