"""Graph Memory MCP tools package.

Importing this package registers all 28 MCP tools on the shared ``mcp``
FastMCP instance defined in :mod:`._core`.  Each sub-module groups related
tools by domain.

``__all__`` is the package's public surface: the tool functions, the shared
``mcp`` instance, and the two state types.  Internals such as ``_state`` and
``_error_response`` live in :mod:`._core` and are imported from there by the
code and tests that genuinely need them, rather than being re-exported to
look public.
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
from ._core import AppState, InitializedState, mcp
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
