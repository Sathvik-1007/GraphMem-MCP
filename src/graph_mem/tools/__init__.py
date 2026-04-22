"""Graph Memory MCP tools package.

Importing this package registers all 27 tools on the shared ``mcp``
FastMCP instance defined in :mod:`._core`.  Each sub-module groups
related tools by domain.

Re-exports the ``mcp`` instance and key helpers so that ``server.py``
and tests can import them from a single location.
"""

# Import tool modules — side effect: registers @mcp.tool() decorators
from . import (  # noqa: F401  — imported for side effects
    dashboard,
    entities,
    graph_mgmt,
    maintenance,
    observations,
    relationships,
    search,
)
from ._core import (
    AppState,
    InitializedState,
    _embed_entities,  # noqa: F401 — re-exported for server.py backwards compat
    _embed_observations,  # noqa: F401
    _error_response,  # noqa: F401
    _require_state,  # noqa: F401
    _state,  # noqa: F401
    mcp,
)
from .dashboard import open_dashboard

# Re-export all tool functions for backwards compatibility
from .entities import (
    add_entities,
    delete_entities,
    get_entity,
    list_entities,
    merge_entities,
    update_entity,
)
from .graph_mgmt import (
    create_graph,
    delete_graph,
    list_graphs,
    switch_graph,
)
from .maintenance import (
    audit_graph,
    compact_observations,
    graph_health,
    suggest_connections,
)
from .observations import (
    add_observations,
    delete_observations,
    update_observation,
)
from .relationships import (
    add_relationships,
    delete_relationships,
    list_relationships,
    update_relationship,
)
from .search import (
    find_connections,
    find_paths,
    get_subgraph,
    read_graph,
    search_nodes,
    search_observations,
)

__all__ = [
    "AppState",
    "InitializedState",
    "add_entities",
    "add_observations",
    "add_relationships",
    "audit_graph",
    "compact_observations",
    "create_graph",
    "delete_entities",
    "delete_graph",
    "delete_observations",
    "delete_relationships",
    "find_connections",
    "find_paths",
    "get_entity",
    "get_subgraph",
    "graph_health",
    "list_entities",
    "list_graphs",
    "list_relationships",
    "mcp",
    "merge_entities",
    "open_dashboard",
    "read_graph",
    "search_nodes",
    "search_observations",
    "suggest_connections",
    "switch_graph",
    "update_entity",
    "update_observation",
    "update_relationship",
]
