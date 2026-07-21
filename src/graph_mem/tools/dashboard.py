"""Dashboard tool — launch the graph-visualisation UI."""

from __future__ import annotations

import socket
from typing import Any

from graph_mem.utils import GraphMemError, get_logger

from ._core import _error_response, _require_state, _state, tool

log = get_logger("server")


@tool()
async def open_dashboard(
    host: str = "127.0.0.1",
    port: int = 0,
) -> dict[str, Any]:
    """Launch the interactive graph-visualisation dashboard and return its URL.

    Starts a lightweight web server that serves a React-based graph explorer
    backed by the same knowledge graph the MCP server manages. If the
    dashboard is already running, the existing URL is returned immediately.

    The dashboard is read-only and runs on localhost by default.

    Requires the [ui] optional dependency (pip install graph-mem[ui]).

    Args:
        host: Bind address (default 127.0.0.1 — local only).
        port: Port number. 0 (default) auto-selects a free port.
    """
    try:
        # Already running? Return existing URL.
        if _state._ui_url is not None:
            return {
                "url": _state._ui_url,
                "port": _state._ui_port,
                "status": "already_running",
                "message": f"Dashboard is already running at {_state._ui_url}",
            }

        state = _require_state()

        # Lazy-import aiohttp (optional dependency)
        try:
            from aiohttp import web as aio_web
        except ImportError:
            return {
                "error": True,
                "error_type": "MissingDependency",
                "message": (
                    "The UI dependency 'aiohttp' is not installed. "
                    "Install it with: pip install graph-mem[ui]"
                ),
            }

        from graph_mem.ui.server import create_app

        app = await create_app(state.storage, state.search, graph=state.graph)

        runner = aio_web.AppRunner(app)
        await runner.setup()

        # Auto-select a free port if port == 0, using SockSite to avoid TOCTOU race
        if port == 0:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, 0))
            resolved_port = sock.getsockname()[1]
            sock.listen(128)
            site = aio_web.SockSite(runner, sock)
        else:
            resolved_port = port
            site = aio_web.TCPSite(runner, host, resolved_port)

        await site.start()

        url = f"http://{host}:{resolved_port}"
        _state._ui_url = url
        _state._ui_runner = runner
        _state._ui_port = resolved_port

        log.info("Dashboard started at %s", url)

        return {
            "url": url,
            "port": resolved_port,
            "status": "started",
            "message": f"Dashboard is now running at {url}",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="open_dashboard")
    except (OSError, ImportError) as exc:
        log.exception("Failed to start dashboard")
        return {
            "error": True,
            "error_type": type(exc).__name__,
            "message": f"Failed to start dashboard: {exc}",
        }
