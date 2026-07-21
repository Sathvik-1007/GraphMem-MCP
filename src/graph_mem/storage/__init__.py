"""Storage layer for graph-mem.

Owns persistence: SQL, schema migrations, and the connection lifecycle. It does
not know what a traversal or a search ranking is — those live in
:mod:`graph_mem.graph` and :mod:`graph_mem.semantic`, which depend on this
package one way only.

Usage::

    from graph_mem.storage import create_backend

    backend = create_backend("sqlite", db_path=Path(".graphmem/graph.db"))
    await backend.initialize()

There is one backend. A previous version of this module carried a plugin
registry and a 476-line abstract base class advertising Neo4j, Memgraph, and
PostgreSQL support. Neither worked: ``create_backend`` resolved a class from
the registry and then returned ``SQLiteBackend`` regardless, ``Config`` only
accepted ``"sqlite"`` so no other backend could have been selected anyway, and
the base class's own ``fetch_all(sql)`` / ``fetch_one(sql)`` signatures took
raw SQL strings that no graph database can implement — while fifteen call sites
outside this package already relied on exactly those methods. The abstraction
could not deliver what it promised. All it produced in practice was a 190-line
stub in the test suite that had to be extended every time a real method was
added. The concrete backend is now the interface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from graph_mem.storage.sqlite_backend import SQLiteBackend
from graph_mem.utils.errors import ConfigError

# Backends this package can construct. A single entry, named explicitly so
# create_backend and Config validate against one source of truth and an unknown
# name fails with a clear message instead of something confusing further down.
SUPPORTED_BACKENDS: tuple[str, ...] = ("sqlite",)


def create_backend(backend_type: str = "sqlite", **kwargs: Any) -> SQLiteBackend:
    """Construct a storage backend by name.

    Args:
        backend_type: Backend name; must be one of :data:`SUPPORTED_BACKENDS`.
        **kwargs: Backend arguments. ``sqlite`` requires ``db_path``, which may
            be a :class:`~pathlib.Path` or a string.

    Returns:
        An **uninitialised** backend — call ``await backend.initialize()``
        before use.

    Raises:
        ConfigError: *backend_type* is not a supported backend.
        TypeError: A required argument is missing.
    """
    if backend_type not in SUPPORTED_BACKENDS:
        raise ConfigError(
            f"Unknown storage backend {backend_type!r}. "
            f"Supported backends: {', '.join(SUPPORTED_BACKENDS)}"
        )

    db_path = kwargs.get("db_path")
    if db_path is None:
        raise TypeError("SQLite backend requires 'db_path' argument.")
    return SQLiteBackend(Path(db_path))


__all__ = [
    "SUPPORTED_BACKENDS",
    "SQLiteBackend",
    "create_backend",
]
