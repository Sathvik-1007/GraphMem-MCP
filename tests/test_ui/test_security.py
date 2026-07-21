"""Authentication and cross-origin tests for the UI server.

The exploit these guard against was reproduced against the previous code:

    POST /api/entity
    Origin: https://evil.example
    Content-Type: text/plain

    -> 201 {"id": "...", "name": "pwned"}

``text/plain`` makes it a CORS *simple request*, so the browser sends it with
no preflight and only hides the response — the write had already happened.
Every test below therefore asserts on the HTTP status, not on whether the
browser would have shown the result.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import CookieJar, web

from graph_mem.ui._keys import (
    allowed_hosts_key,
    frontend_dir_key,
    graph_key,
    search_key,
    session_token_key,
    storage_key,
)
from graph_mem.ui.routes import setup_routes
from graph_mem.ui.security import (
    SESSION_COOKIE,
    TOKEN_HEADER,
    TOKEN_QUERY_PARAM,
    allowed_hosts_for,
    generate_session_token,
    security_middleware,
)

from .test_routes import MockGraph, MockSearch, MockStorage

TEST_TOKEN = "test-session-token-not-random"


def _make_secured_app(bind_host: str = "127.0.0.1") -> web.Application:
    """Build an app with the security middleware active."""
    app = web.Application(middlewares=[security_middleware])
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[graph_key] = MockGraph()
    app[frontend_dir_key] = None
    app[session_token_key] = TEST_TOKEN
    app[allowed_hosts_key] = allowed_hosts_for(bind_host)
    setup_routes(app)
    return app


@pytest.fixture
async def secured(aiohttp_client):
    """Client for an app with authentication enabled."""
    return await aiohttp_client(_make_secured_app())


def _auth() -> dict[str, str]:
    return {TOKEN_HEADER: TEST_TOKEN}


# ---------------------------------------------------------------------------
# The reproduced exploit
# ---------------------------------------------------------------------------


async def test_cross_origin_simple_post_is_rejected(secured):
    """The exact confirmed attack: text/plain POST from a foreign origin."""
    resp = await secured.post(
        "/api/entity",
        data=json.dumps({"name": "pwned", "entity_type": "concept"}),
        headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
    )
    assert resp.status == 403


async def test_cross_origin_json_post_is_rejected(secured):
    """The same request with an honest Content-Type is rejected too."""
    resp = await secured.post(
        "/api/entity",
        json={"name": "pwned", "entity_type": "concept"},
        headers={"Origin": "https://evil.example", **_auth()},
    )
    assert resp.status == 403


async def test_cross_origin_read_is_rejected(secured):
    """Reads are protected as well — the graph is not public data."""
    resp = await secured.get("/api/graph", headers={"Origin": "https://evil.example", **_auth()})
    assert resp.status == 403


# ---------------------------------------------------------------------------
# Token enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/graph"),
        ("GET", "/api/stats"),
        ("GET", "/api/search?q=x"),
        ("GET", "/api/entity/AuthService"),
        ("GET", "/api/graphs"),
        ("POST", "/api/entity"),
        ("POST", "/api/relationship"),
        ("POST", "/api/observations"),
        ("POST", "/api/graphs/switch"),
        ("PUT", "/api/entity/AuthService"),
        ("PUT", "/api/observation/1"),
        ("DELETE", "/api/entity/AuthService"),
        ("DELETE", "/api/observation/1"),
    ],
)
async def test_every_api_endpoint_requires_a_token(secured, method, path):
    """No API route is reachable without the session token."""
    resp = await secured.request(method, path, json={})
    assert resp.status == 403, f"{method} {path} was reachable without a token"


async def test_wrong_token_is_rejected(secured):
    """A token that is merely present is not enough."""
    resp = await secured.get("/api/stats", headers={TOKEN_HEADER: "wrong"})
    assert resp.status == 403


async def test_valid_token_is_accepted(secured):
    """The happy path still works — this is not a test of a broken server."""
    resp = await secured.get("/api/stats", headers=_auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["entity_count"] == 3


async def test_session_cookie_is_not_accepted_for_api_calls(secured):
    """A cookie must never authenticate the API.

    Accepting it would recreate the CSRF hole: a cross-site request rides
    ambient cookies, but cannot set a custom header.
    """
    secured.session.cookie_jar.update_cookies({SESSION_COOKIE: TEST_TOKEN})
    resp = await secured.get("/api/stats")
    assert resp.status == 403


# ---------------------------------------------------------------------------
# Host allow-list (DNS rebinding)
# ---------------------------------------------------------------------------


async def test_foreign_host_header_is_rejected(secured):
    """A rebound DNS name reaches loopback but still carries its own Host."""
    resp = await secured.get("/api/stats", headers={"Host": "evil.example", **_auth()})
    assert resp.status == 403


async def test_loopback_host_spellings_are_accepted(secured):
    """localhost and 127.0.0.1 are the same machine and both work."""
    for host in ("127.0.0.1:1234", "localhost:1234"):
        resp = await secured.get("/api/stats", headers={"Host": host, **_auth()})
        assert resp.status == 200, host


def test_allowed_hosts_for_loopback_covers_every_spelling():
    """Binding to one loopback spelling accepts the others."""
    allowed = allowed_hosts_for("127.0.0.1")
    assert "localhost" in allowed
    assert "::1" in allowed
    assert "127.0.0.1" in allowed


def test_allowed_hosts_for_explicit_interface_is_exact():
    """A non-loopback bind accepts only the name it was given."""
    allowed = allowed_hosts_for("192.168.1.50")
    assert allowed == frozenset({"192.168.1.50"})
    assert "localhost" not in allowed


# ---------------------------------------------------------------------------
# Document route handshake
# ---------------------------------------------------------------------------


async def test_document_request_without_token_is_rejected(secured):
    """The HTML shell carries the token, so it is not served unauthenticated."""
    resp = await secured.get("/")
    assert resp.status == 403


async def test_document_request_with_query_token_sets_session_cookie(aiohttp_client):
    """The bootstrap URL is exchanged for a SameSite=Strict cookie."""
    client = await aiohttp_client(_make_secured_app())
    resp = await client.get(f"/?{TOKEN_QUERY_PARAM}={TEST_TOKEN}")
    assert resp.status == 200
    cookie = resp.cookies.get(SESSION_COOKIE)
    assert cookie is not None
    assert cookie.value == TEST_TOKEN
    assert cookie["samesite"].lower() == "strict"
    assert cookie["httponly"] != ""


async def test_document_request_with_cookie_alone_is_accepted(aiohttp_client):
    """After the handshake, reloads work without the token in the URL."""
    client = await aiohttp_client(_make_secured_app(), cookie_jar=CookieJar(unsafe=True))
    first = await client.get(f"/?{TOKEN_QUERY_PARAM}={TEST_TOKEN}")
    assert first.status == 200

    second = await client.get("/")
    assert second.status == 200


# ---------------------------------------------------------------------------
# Static bundle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/assets/index-abc123.js", "/favicon.svg"])
async def test_static_assets_do_not_require_a_token(aiohttp_client, path):
    """The browser fetches these as subresources with no credential attached.

    They are the same bytes for every install and hold no graph data, so they
    are exempt from the token — but only from the token. A 404 here means the
    request reached routing; a 403 would mean the SPA can never boot.
    """
    client = await aiohttp_client(_make_secured_app())
    resp = await client.get(path)
    assert resp.status != 403


async def test_static_assets_still_enforce_the_host_allow_list(aiohttp_client):
    """Exempting assets from the token does not exempt them from rebinding checks."""
    client = await aiohttp_client(_make_secured_app())
    resp = await client.get("/assets/index-abc123.js", headers={"Host": "evil.example"})
    assert resp.status == 403


# ---------------------------------------------------------------------------
# Unsecured app (handler-level unit tests must keep working)
# ---------------------------------------------------------------------------


async def test_app_without_security_context_is_not_gated(aiohttp_client):
    """Omitting the token disables the middleware rather than locking everyone out."""
    app = web.Application(middlewares=[security_middleware])
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[frontend_dir_key] = None
    setup_routes(app)
    client = await aiohttp_client(app)

    resp = await client.get("/api/stats")
    assert resp.status == 200


async def test_create_app_rejects_token_without_bind_host():
    """A token with no host allow-list would be a half-configured guard."""
    from graph_mem.ui.server import create_app

    with pytest.raises(ValueError, match="bind_host"):
        await create_app(
            MockStorage(),  # type: ignore[arg-type]
            MockSearch(),  # type: ignore[arg-type]
            session_token="tok",
        )


def test_generated_tokens_are_unique_and_long():
    """Tokens must not be guessable or repeated across runs."""
    tokens = {generate_session_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 40 for t in tokens)
