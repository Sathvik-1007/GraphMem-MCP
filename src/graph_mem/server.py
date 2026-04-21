"""FastMCP server — registers all 24 Graph Memory MCP tools.

This is the public entry point.  The actual tool implementations live in
:mod:`graph_mem.tools` sub-modules; importing this module triggers their
registration on the shared ``mcp`` FastMCP instance.

Backwards-compatible: all tool functions and internal helpers that were
previously importable from ``graph_mem.server`` are re-exported here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

# Import everything from the tools package — this triggers @mcp.tool()
# registration and makes all symbols available at ``graph_mem.server.*``
# for backwards compatibility with existing tests and CLI.
from graph_mem.tools import (  # noqa: F401 — re-exported
    AppState,
    InitializedState,
    _embed_entities,
    _embed_observations,
    _error_response,
    _require_state,
    _state,
    add_entities,
    add_observations,
    add_relationships,
    create_graph,
    delete_entities,
    delete_graph,
    delete_observations,
    delete_relationships,
    find_connections,
    find_paths,
    get_entity,
    get_subgraph,
    list_entities,
    list_graphs,
    list_relationships,
    mcp,
    merge_entities,
    open_dashboard,
    read_graph,
    search_nodes,
    search_observations,
    switch_graph,
    update_entity,
    update_observation,
    update_relationship,
)

if TYPE_CHECKING:
    from graph_mem.utils import Config

# ---------------------------------------------------------------------------
# Factory & entry point
# ---------------------------------------------------------------------------


def create_server(config: Config | None = None) -> mcp.__class__:
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
