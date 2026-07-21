"""Tests for the ``open_dashboard`` MCP tool.

This tool starts a real HTTP server holding the whole knowledge graph, at the
request of a language model. The tests therefore care about two things beyond
"does it work": that the model cannot influence *where* it binds, and that a
failed start-up does not leave a socket or an AppRunner behind.
"""

from __future__ import annotations

import contextlib
import inspect
import socket

import pytest

import graph_mem.tools._core as core
from graph_mem.tools.dashboard import _DASHBOARD_HOST, _MAX_PORT, open_dashboard


@pytest.fixture(autouse=True)
async def _clean_dashboard_state():
    """Ensure no dashboard state leaks between tests."""
    yield
    runner = core._state._ui_runner
    if runner is not None:
        with contextlib.suppress(Exception):
            await runner.cleanup()
    core._state._ui_url = None
    core._state._ui_runner = None
    core._state._ui_port = None
    core._state._ui_app = None


def _port_is_free(port: int) -> bool:
    """Whether *port* can still be bound on loopback."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((_DASHBOARD_HOST, port))
    except OSError:
        return False
    else:
        return True
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# The bind address is not the model's to choose
# ---------------------------------------------------------------------------


def test_open_dashboard_has_no_host_parameter() -> None:
    """The bind address must not be reachable from a tool call.

    Regression: ``host`` was a parameter, so a prompt-injected agent could call
    ``open_dashboard(host="0.0.0.0")`` and publish the entire knowledge graph
    to the local network.
    """
    params = inspect.signature(open_dashboard).parameters
    assert "host" not in params
    assert set(params) == {"port"}


async def test_dashboard_binds_loopback(setup_server) -> None:
    """The served URL is always on loopback."""
    result = await open_dashboard()

    assert "error" not in result
    assert result["url"].startswith(f"http://{_DASHBOARD_HOST}:")


async def test_dashboard_url_carries_a_session_token(setup_server) -> None:
    """The URL is the only place the token is handed out, so it must be there."""
    result = await open_dashboard()

    assert "token=" in result["url"]
    token = result["url"].split("token=", 1)[1]
    assert len(token) >= 40, "token is too short to be unguessable"


async def test_each_launch_uses_a_fresh_token(setup_server) -> None:
    """Restarting must invalidate the previous token."""
    first = await open_dashboard()
    first_token = first["url"].split("token=", 1)[1]

    runner = core._state._ui_runner
    await runner.cleanup()
    core._state._ui_url = None
    core._state._ui_runner = None

    second = await open_dashboard()
    second_token = second["url"].split("token=", 1)[1]

    assert first_token != second_token


# ---------------------------------------------------------------------------
# Port handling
# ---------------------------------------------------------------------------


async def test_port_zero_resolves_to_a_real_port(setup_server) -> None:
    """Port 0 means 'let the OS choose', and the choice is reported back."""
    result = await open_dashboard(port=0)

    assert "error" not in result
    assert isinstance(result["port"], int)
    assert 1 <= result["port"] <= _MAX_PORT
    assert f":{result['port']}" in result["url"]


@pytest.mark.parametrize("port", [-1, -65535, _MAX_PORT + 1, 999999])
async def test_out_of_range_port_is_rejected(setup_server, port: int) -> None:
    """A port outside the addressable range is a validation error, not a crash."""
    result = await open_dashboard(port=port)

    assert result["error"] is True
    assert result["error_type"] == "ValidationError"
    assert core._state._ui_runner is None


@pytest.mark.parametrize("port", ["8080", 8080.5, None, [8080]])
async def test_non_integer_port_is_rejected(setup_server, port: object) -> None:
    """A model that sends the wrong type gets a structured error."""
    result = await open_dashboard(port=port)  # type: ignore[arg-type]

    assert result["error"] is True
    assert result["error_type"] == "ValidationError"


async def test_bool_port_is_rejected(setup_server) -> None:
    """``True`` is an int subclass and would otherwise bind port 1.

    Worth its own test: ``isinstance(True, int)`` is True, so a naive check
    accepts it and the server silently tries a privileged port.
    """
    result = await open_dashboard(port=True)  # type: ignore[arg-type]

    assert result["error"] is True
    assert result["error_type"] == "ValidationError"


# ---------------------------------------------------------------------------
# Idempotence and cleanup
# ---------------------------------------------------------------------------


async def test_second_call_returns_the_running_instance(setup_server) -> None:
    """Calling twice does not start a second server."""
    first = await open_dashboard()
    runner_after_first = core._state._ui_runner

    second = await open_dashboard()

    assert second["status"] == "already_running"
    assert second["url"] == first["url"]
    assert core._state._ui_runner is runner_after_first


async def test_failed_start_leaves_no_socket_or_runner(setup_server, monkeypatch) -> None:
    """A failure partway through start-up must not leak the bound port.

    Without cleanup the socket stays bound, so the port is unusable and the
    next attempt fails for a reason unrelated to the original problem.
    """
    from aiohttp import web

    # Take a port, learn its number, release it — then force start() to fail
    # on that port so we can assert it is free again afterwards.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((_DASHBOARD_HOST, 0))
    port = probe.getsockname()[1]
    probe.close()

    async def _boom(self) -> None:
        raise OSError("simulated bind failure")

    monkeypatch.setattr(web.TCPSite, "start", _boom)

    result = await open_dashboard(port=port)

    assert result["error"] is True
    assert core._state._ui_runner is None
    assert core._state._ui_url is None
    assert _port_is_free(port), "the port stayed bound after a failed launch"


async def test_missing_aiohttp_reports_the_dependency(setup_server, monkeypatch) -> None:
    """The UI is an optional extra, so its absence is an actionable message."""
    import builtins

    real_import = builtins.__import__

    def _no_aiohttp(name, *args, **kwargs):
        if name == "aiohttp":
            raise ImportError("No module named 'aiohttp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_aiohttp)

    result = await open_dashboard()

    assert result["error"] is True
    assert result["error_type"] == "MissingDependency"
    assert "graph-mem[ui]" in result["message"]
