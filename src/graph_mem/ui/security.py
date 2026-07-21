"""Authentication and cross-origin defences for the local UI server.

The UI server binds to loopback and exposes the full knowledge graph, including
write endpoints.  "Only listening on 127.0.0.1" is not by itself a security
boundary: every web page the user visits can issue requests to loopback, and
every other process on the machine can too.  This module closes both holes.

Three independent checks guard the API, so no single mistake is exploitable:

Host allow-list
    ``Host`` must name the interface the server was bound to.  A DNS-rebinding
    attack resolves an attacker-controlled domain to 127.0.0.1 and thereby
    reaches loopback with the *browser's* same-origin privileges; the request
    still carries ``Host: evil.example``, which this rejects.

Origin allow-list
    A cross-origin ``POST`` with ``Content-Type: text/plain`` is a CORS *simple
    request*: the browser sends it with no preflight and merely hides the
    response.  The write already happened.  Browsers always attach ``Origin``
    to such requests, so rejecting foreign origins blocks them.

Session token
    Blocks non-browser callers — any other local process, or a container
    sharing the network namespace, that is unaffected by the two header checks.
    API callers must present it in a custom header, which a cross-site request
    cannot set without triggering a preflight that the Origin check then fails.

The token reaches the browser exactly once, as a query parameter on the URL the
server opens.  The document route exchanges it for a ``SameSite=Strict``
cookie so reloads and client-side navigation keep working, and the token itself
is injected into the served HTML for the SPA to use on API calls.  The API
never accepts the cookie as proof — only the header — so a cross-site request
riding the user's cookie still fails.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from aiohttp import web

from graph_mem.utils import get_logger

from ._keys import allowed_hosts_key, session_token_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = get_logger("ui.security")

# 32 bytes of ``secrets`` entropy, URL-safe base64.  Matches the strength of
# the session tokens used by comparable local-server tools and is far beyond
# brute-forcing over a loopback socket.
_SESSION_TOKEN_BYTES = 32

# API callers authenticate with this header.  A custom header cannot be set by
# a cross-site fetch without a CORS preflight, and the preflight fails the
# Origin check, so this header is unforgeable from another web origin.
TOKEN_HEADER = "X-GraphMem-Token"

# Accepted on the document route only, to bootstrap a browser session.
TOKEN_QUERY_PARAM = "token"

# Set on the document route after a successful token handshake.  SameSite=Strict
# keeps browsers from attaching it to any cross-site navigation or subresource
# request, so it cannot be used as a CSRF credential.
SESSION_COOKIE = "graphmem_session"

# Hostnames that mean "this machine" when the server is bound to loopback.
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Static build artefacts: the compiled JS/CSS bundle and the favicon.  These
# are the same bytes for every install and contain no graph data, so requiring
# a credential on them buys nothing while breaking the browser's subresource
# loads, which carry neither the query parameter nor — for a `SameSite=Strict`
# cookie on a freshly-opened tab — a reliable cookie.  Host and Origin are
# still enforced on them.
_PUBLIC_PREFIXES = ("/assets/", "/favicon.")

# Placeholder in the built index.html, replaced with the live session token
# when the document is served.  Injecting server-side keeps the token out of
# the shipped bundle on disk.
TOKEN_PLACEHOLDER = "__GRAPHMEM_SESSION_TOKEN__"


def generate_session_token() -> str:
    """Return a fresh, unguessable session token for one server run."""
    return secrets.token_urlsafe(_SESSION_TOKEN_BYTES)


def allowed_hosts_for(bind_host: str) -> frozenset[str]:
    """Return the hostnames the ``Host`` header may legitimately carry.

    Args:
        bind_host: The interface the server was asked to bind, e.g.
            ``"127.0.0.1"`` or ``"0.0.0.0"``.

    Returns:
        Lower-cased hostnames, without port.  Binding to loopback accepts every
        spelling of loopback; binding anywhere else accepts only that literal,
        because a wildcard bind has no single correct name and the operator has
        already been warned that the surface is exposed.
    """
    host = bind_host.strip().lower()
    if host in _LOOPBACK_HOSTNAMES:
        return frozenset(_LOOPBACK_HOSTNAMES)
    return frozenset({host})


def _hostname_of(authority: str) -> str:
    """Strip the port from a ``host[:port]`` authority, keeping IPv6 brackets."""
    authority = authority.strip().lower()
    if authority.startswith("["):
        # IPv6 literal: "[::1]:8080" -> "[::1]"
        closing = authority.find("]")
        if closing != -1:
            return authority[: closing + 1]
        return authority
    return authority.rsplit(":", 1)[0] if ":" in authority else authority


def _is_public_asset(request: web.Request) -> bool:
    """Whether *request* is for a static build artefact that carries no data."""
    return request.path.startswith(_PUBLIC_PREFIXES)


def _is_api_request(request: web.Request) -> bool:
    """Whether *request* targets the JSON API rather than the SPA shell."""
    return request.path.startswith("/api/")


def _reject(reason: str, request: web.Request) -> web.Response:
    """Log and build a 403 that reveals nothing about why to the caller."""
    log.warning(
        "Rejected %s %s: %s (Host=%r Origin=%r)",
        request.method,
        request.path,
        reason,
        request.headers.get("Host"),
        request.headers.get("Origin"),
    )
    return web.json_response({"error": "Forbidden"}, status=403)


def _token_is_valid(candidate: str | None, expected: str) -> bool:
    """Constant-time token comparison that tolerates a missing candidate."""
    if not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


@web.middleware
async def security_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Enforce the Host, Origin, and token checks described in the module docstring."""
    allowed_hosts: frozenset[str] | None = request.app.get(allowed_hosts_key)
    expected_token: str | None = request.app.get(session_token_key)

    # An app built without security configured (unit tests of individual
    # handlers) is left alone; start_server always configures both.
    if allowed_hosts is None or expected_token is None:
        return await handler(request)

    # ── Host allow-list ──────────────────────────────────────────────────
    host_header = request.headers.get("Host")
    if host_header is None or _hostname_of(host_header) not in allowed_hosts:
        return _reject("Host header not in allow-list", request)

    # ── Origin allow-list ────────────────────────────────────────────────
    # Absent on same-origin GETs, always present on cross-origin requests.
    origin = request.headers.get("Origin")
    if origin is not None:
        origin_host = _hostname_of(urlsplit(origin).netloc)
        if origin_host not in allowed_hosts:
            return _reject("Origin not in allow-list", request)

    # ── Session token ────────────────────────────────────────────────────
    # Static bundle: no credential, because it holds no graph data and the
    # browser fetches it as a subresource that carries neither the query
    # parameter nor a guaranteed cookie.
    if _is_public_asset(request):
        return await handler(request)

    # API: header only.  Deliberately not the cookie — accepting the cookie
    # here would reintroduce exactly the CSRF hole this module exists to close.
    if _is_api_request(request):
        if not _token_is_valid(request.headers.get(TOKEN_HEADER), expected_token):
            return _reject("API request without a valid token", request)
        return await handler(request)

    # Document: the URL the server opened carries the token; afterwards the
    # SameSite=Strict cookie stands in for it so reloads and client-side
    # navigation keep working without the token reappearing in the address bar.
    if _token_is_valid(request.query.get(TOKEN_QUERY_PARAM), expected_token):
        response = await handler(request)
        response.set_cookie(
            SESSION_COOKIE,
            expected_token,
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response
    if _token_is_valid(request.cookies.get(SESSION_COOKIE), expected_token):
        return await handler(request)
    return _reject("document request without a valid token", request)
