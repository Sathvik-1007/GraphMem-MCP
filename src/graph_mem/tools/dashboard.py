"""Dashboard tool — launch the graph-visualisation UI."""

from __future__ import annotations

import contextlib
import socket
from typing import Any

from graph_mem.utils import GraphMemError, ValidationError, get_logger

from ._core import _error_response, _require_state, _state, tool

log = get_logger("server")

# The dashboard is always bound to loopback.  It is deliberately not a tool
# parameter: the caller is a language model, the server it starts has full
# read/write access to the knowledge graph, and no prompt has the standing to
# decide that graph should be reachable from the network.  A human who wants
# that runs `graph-mem ui --host ...` themselves.
_DASHBOARD_HOST = "127.0.0.1"

# 0 means "let the OS pick"; 1-65535 are the addressable TCP ports.  Ports
# below 1024 additionally require privileges the server will not have, but
# that failure is the OS's to report, with a clearer message than a guess here.
_MIN_PORT = 0
_MAX_PORT = 65535


@tool()
async def open_dashboard(
    port: int = 0,
) -> dict[str, Any]:
    """Launch the interactive graph-visualisation dashboard and return its URL.

    Starts a web server that serves a React-based graph explorer backed by the
    same knowledge graph the MCP server manages. If the dashboard is already
    running, the existing URL is returned immediately.

    The dashboard can both read and modify the graph. It binds to localhost
    only, and the returned URL carries a single-use session token that the
    browser exchanges for a session — treat that URL as a password and do not
    paste it anywhere shared.

    Requires the [ui] optional dependency (pip install graph-mem[ui]).

    Args:
        port: Port number, 0-65535. 0 (default) auto-selects a free port.
    """
    runner = None
    sock: socket.socket | None = None
    try:
        # Already running? Return existing URL.
        if _state._ui_url is not None:
            return {
                "url": _state._ui_url,
                "port": _state._ui_port,
                "status": "already_running",
                "message": f"Dashboard is already running at {_state._ui_url}",
            }

        if not isinstance(port, int) or isinstance(port, bool):
            raise ValidationError(f"port must be an integer, got {type(port).__name__}")
        if not _MIN_PORT <= port <= _MAX_PORT:
            raise ValidationError(f"port must be between {_MIN_PORT} and {_MAX_PORT}, got {port}")

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

        from graph_mem.ui.security import TOKEN_QUERY_PARAM, generate_session_token
        from graph_mem.ui.server import create_app

        session_token = generate_session_token()
        app = await create_app(
            state.storage,
            state.search,
            graph=state.graph,
            session_token=session_token,
            bind_host=_DASHBOARD_HOST,
        )

        runner = aio_web.AppRunner(app)
        await runner.setup()

        # Auto-select a free port if port == 0, using SockSite to avoid a
        # TOCTOU race between discovering a free port and binding it.
        if port == 0:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((_DASHBOARD_HOST, 0))
            resolved_port = sock.getsockname()[1]
            sock.listen(128)
            site: Any = aio_web.SockSite(runner, sock)
        else:
            resolved_port = port
            site = aio_web.TCPSite(runner, _DASHBOARD_HOST, resolved_port)

        await site.start()
        # Ownership of both has passed to the running site.
        sock = None
        started_runner, runner = runner, None

        url = f"http://{_DASHBOARD_HOST}:{resolved_port}/?{TOKEN_QUERY_PARAM}={session_token}"
        _state._ui_url = url
        _state._ui_runner = started_runner
        _state._ui_app = app
        _state._ui_port = resolved_port

        log.info("Dashboard started on port %d", resolved_port)

        return {
            "url": url,
            "port": resolved_port,
            "status": "started",
            "message": (
                f"Dashboard is now running at {url} — this URL contains a "
                f"session token, so treat it as a password."
            ),
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
    finally:
        # Reached with non-None values only when start-up failed partway.
        # Without this, a failed launch leaks a bound socket and an AppRunner,
        # and the leaked socket keeps the port occupied for the next attempt.
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()
        if runner is not None:
            with contextlib.suppress(Exception):
                await runner.cleanup()
