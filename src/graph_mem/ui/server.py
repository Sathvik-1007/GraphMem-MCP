"""aiohttp web server for graph-mem visualization UI.

Serves a token-authenticated REST API backed by the existing storage and
search engines, plus a built-in React SPA for interactive graph exploration.

The API exposes write endpoints as well as reads, so it is guarded by the
checks in ``graph_mem.ui.security`` rather than by the bind address alone.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import socket
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from aiohttp import web

from graph_mem.graph.engine import GraphEngine
from graph_mem.semantic import EmbeddingEngine, HybridSearch
from graph_mem.storage import SQLiteBackend, create_backend
from graph_mem.utils import get_logger, load_config, setup_logging

from ._keys import (
    allowed_hosts_key,
    db_path_key,
    frontend_dir_key,
    graph_key,
    search_key,
    session_token_key,
    storage_key,
    switch_lock_key,
)
from .routes import setup_routes
from .security import (
    TOKEN_QUERY_PARAM,
    allowed_hosts_for,
    generate_session_token,
    security_middleware,
)

log = get_logger("ui")

# ---------------------------------------------------------------------------
# Frontend asset resolution
# ---------------------------------------------------------------------------

_FRONTEND_DIR: Path | None = None


def _resolve_frontend_dir() -> Path | None:
    """Locate the bundled frontend directory.

    Returns the path if it exists and contains ``index.html``, else ``None``.
    """
    global _FRONTEND_DIR
    if _FRONTEND_DIR is not None:
        return _FRONTEND_DIR

    try:
        pkg_files = importlib.resources.files("graph_mem.ui") / "frontend"
        candidate = Path(str(pkg_files))
        if candidate.is_dir() and (candidate / "index.html").is_file():
            _FRONTEND_DIR = candidate
            return _FRONTEND_DIR
    except (TypeError, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


async def create_app(
    storage: SQLiteBackend,
    search: HybridSearch,
    graph: GraphEngine | None = None,
    db_path: str | None = None,
    *,
    session_token: str | None = None,
    bind_host: str | None = None,
) -> web.Application:
    """Build the aiohttp Application with all routes and middleware.

    Args:
        storage: Backend the handlers read and write through.
        search: Hybrid search engine backing ``/api/search``.
        graph: Graph engine; required by the write endpoints.
        db_path: Path of the active database, reported by ``/api/graphs``.
        session_token: Secret every API caller must present. Omitting it
            disables authentication, which is only appropriate for in-process
            handler tests — ``start_server`` always supplies one.
        bind_host: Interface the server will listen on. Used to build the
            ``Host``/``Origin`` allow-list. Required whenever *session_token*
            is given.

    Raises:
        ValueError: *session_token* was supplied without *bind_host*.
    """
    if session_token is not None and bind_host is None:
        raise ValueError("bind_host is required whenever session_token is supplied")

    app = web.Application(middlewares=[_error_middleware, security_middleware])
    app[storage_key] = storage
    app[search_key] = search
    if graph is not None:
        app[graph_key] = graph
    if db_path is not None:
        app[db_path_key] = db_path

    if session_token is not None and bind_host is not None:
        app[session_token_key] = session_token
        app[allowed_hosts_key] = allowed_hosts_for(bind_host)

    app[switch_lock_key] = asyncio.Lock()

    # Resolve frontend dir and stash for route setup
    app[frontend_dir_key] = _resolve_frontend_dir()

    setup_routes(app)
    return app


# ---------------------------------------------------------------------------
# Error-handling middleware
# ---------------------------------------------------------------------------


@web.middleware
async def _error_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Catch unhandled exceptions and return structured JSON errors."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise  # Let aiohttp handle its own HTTP errors
    except Exception:
        log.exception("Unhandled error in %s %s", request.method, request.path)
        return web.json_response(
            {"error": "Internal server error"},
            status=500,
        )


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


async def start_server(
    host: str = "127.0.0.1",
    port: int = 0,
    no_open: bool = False,
    db_path: str | None = None,
) -> None:
    """Initialise backends, create app, and run the server.

    If *port* is ``0``, an available port is auto-selected by the OS.
    """
    config = load_config()
    setup_logging(config.log_level)

    resolved_db = db_path or str(config.ensure_db_dir())
    storage = create_backend(config.backend_type, db_path=resolved_db)
    await storage.initialize()

    embeddings = EmbeddingEngine(
        model_name=config.embedding_model,
        use_onnx=config.use_onnx,
        device=config.embedding_device,
        cache_size=config.cache_size,
    )
    try:
        await embeddings.initialize(storage)
    except Exception:
        log.warning("Embedding engine unavailable — search will be limited")

    search = HybridSearch(storage, embeddings)

    graph = GraphEngine(storage)
    session_token = generate_session_token()
    app = await create_app(
        storage,
        search,
        graph,
        db_path=resolved_db,
        session_token=session_token,
        bind_host=host,
    )

    runner = web.AppRunner(app)
    await runner.setup()

    site: web.BaseSite
    if port == 0:
        # Use SockSite to avoid TOCTOU race — bind once, pass socket directly
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        port = sock.getsockname()[1]
        sock.listen(128)
        site = web.SockSite(runner, sock)
    else:
        site = web.TCPSite(runner, host, port)

    await site.start()

    # The token travels to the browser exactly once, in the URL that opens the
    # UI.  The document route swaps it for a SameSite=Strict session cookie, so
    # it never needs to appear in a URL again.
    url = f"http://{host}:{port}/?{TOKEN_QUERY_PARAM}={session_token}"

    if host not in ("127.0.0.1", "localhost"):
        log.warning(
            "UI is bound to %s and reachable from the network. Requests still "
            "require the session token, but anyone who observes the URL gains "
            "full read/write access to this knowledge graph.",
            host,
        )

    log.info("Graph UI available at %s", url)
    print(f"Graph UI available at {url}")
    print("This URL contains a session token — treat it as a password.")
    print("Press Ctrl+C to stop")

    if not no_open:
        webbrowser.open(url)

    # Run until interrupted
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await storage.close()
