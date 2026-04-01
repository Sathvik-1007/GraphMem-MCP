"""aiohttp web server for graphrag-mcp visualization UI.

Serves a read-only REST API backed by the existing storage and search
engines, plus a built-in React SPA for interactive graph exploration.
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

from graphrag_mcp.semantic import EmbeddingEngine, HybridSearch
from graphrag_mcp.storage import StorageBackend, create_backend
from graphrag_mcp.utils import get_logger, load_config, setup_logging

from .routes import setup_routes

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
        pkg_files = importlib.resources.files("graphrag_mcp.ui") / "frontend"
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
    storage: StorageBackend,
    search: HybridSearch,
) -> web.Application:
    """Build the aiohttp Application with all routes and middleware."""
    app = web.Application(middlewares=[_error_middleware])
    app["storage"] = storage
    app["search"] = search

    # Resolve frontend dir and stash for route setup
    app["frontend_dir"] = _resolve_frontend_dir()

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

    app = await create_app(storage, search)

    runner = web.AppRunner(app)
    await runner.setup()

    if port == 0:
        # Let the OS assign an available port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, 0))
        port = sock.getsockname()[1]
        sock.close()

    site = web.TCPSite(runner, host, port)
    await site.start()

    url = f"http://{host}:{port}"

    if host not in ("127.0.0.1", "localhost"):
        log.warning("WARNING: UI is accessible from the network. No authentication is configured.")

    log.info("Graph UI available at %s", url)
    print(f"Graph UI available at {url}")
    print("Press Ctrl+C to stop")

    if not no_open:
        webbrowser.open(url)

    # Run until interrupted
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await storage.close()
