"""API route handlers for the graphrag-mcp visualisation UI.

All endpoints are read-only (GET).  Handlers pull ``storage`` and ``search``
from ``request.app`` — no direct database connections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

if TYPE_CHECKING:
    from pathlib import Path

from aiohttp import web

from graphrag_mcp.utils import get_logger

log = get_logger("ui.routes")


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def setup_routes(app: web.Application) -> None:
    """Register all API and static-file routes on *app*."""
    app.router.add_get("/api/graph", handle_graph)
    app.router.add_get("/api/entity/{name}", handle_entity)
    app.router.add_get("/api/search", handle_search)
    app.router.add_get("/api/stats", handle_stats)

    # SPA static files — only if the frontend has been built
    frontend_dir: Path | None = app.get("frontend_dir")
    if frontend_dir is not None:
        assets_dir = frontend_dir / "assets"
        if assets_dir.is_dir():
            app.router.add_static("/assets", assets_dir, show_index=False)
        app.router.add_get("/", _handle_index)
        # Catch-all for SPA client-side routing (must be last)
        app.router.add_get("/{path:.*}", _handle_spa_fallback)
    else:
        app.router.add_get("/", _handle_no_frontend)


# ---------------------------------------------------------------------------
# /api/graph
# ---------------------------------------------------------------------------


async def handle_graph(request: web.Request) -> web.Response:
    """Return the complete graph (entities + relationships) for visualisation."""
    storage = request.app["storage"]

    # Parse query parameters
    entity_types_raw = request.query.get("entity_types", "")
    entity_types: list[str] | None = (
        [t.strip() for t in entity_types_raw.split(",") if t.strip()] if entity_types_raw else None
    )
    limit = _parse_int(request.query.get("limit"), default=500, minimum=1, maximum=5000)

    # Fetch entities (one type-filter at a time, or all)
    if entity_types:
        entities: list[dict[str, Any]] = []
        for et in entity_types:
            entities.extend(await storage.list_entities(entity_type=et, limit=limit))
        entities = entities[:limit]
    else:
        entities = await storage.list_entities(limit=limit)

    # Collect entity IDs so we can fetch internal relationships
    entity_ids = [str(e["id"]) for e in entities]

    relationships: list[dict[str, Any]] = []
    if entity_ids:
        relationships = await storage.fetch_relationships_between(entity_ids)

    # Totals (un-filtered)
    total_entities = await storage.count_entities()
    total_relationships = await storage.count_relationships()

    # Build response entities (strip internal IDs, add counts)
    resp_entities = []
    for e in entities:
        resp_entities.append(
            {
                "name": e.get("name", ""),
                "entity_type": e.get("entity_type", ""),
                "description": e.get("description", ""),
                "properties": _safe_json(e.get("properties", {})),
            }
        )

    resp_relationships = []
    for r in relationships:
        resp_relationships.append(
            {
                "source": r.get("source_name", ""),
                "target": r.get("target_name", ""),
                "relationship_type": r.get("relationship_type", ""),
                "weight": r.get("weight", 1.0),
                "properties": _safe_json(r.get("properties", {})),
            }
        )

    return web.json_response(
        {
            "entities": resp_entities,
            "relationships": resp_relationships,
            "total_entities": total_entities,
            "total_relationships": total_relationships,
        }
    )


# ---------------------------------------------------------------------------
# /api/entity/{name}
# ---------------------------------------------------------------------------


async def handle_entity(request: web.Request) -> web.Response:
    """Return full details for a single entity including observations."""
    storage = request.app["storage"]
    raw_name = request.match_info["name"]
    name = unquote(raw_name)

    # Resolve entity: exact → case-insensitive
    entity = await storage.get_entity_by_name(name)
    if entity is None:
        entity = await storage.get_entity_by_name_nocase(name)
    if entity is None:
        return web.json_response(
            {"error": "Entity not found", "name": name},
            status=404,
        )

    entity_id = str(entity["id"])

    # Fetch observations and relationships
    observations = await storage.get_observations_for_entity(entity_id)
    rel_rows = await storage.get_relationships_for_entity(entity_id)

    resp_observations = [
        {
            "content": o.get("content", ""),
            "source": o.get("source", ""),
            "created_at": o.get("created_at"),
        }
        for o in observations
    ]

    resp_relationships = []
    for r in rel_rows:
        direction = (
            "outgoing" if str(r.get("source_name", "")).lower() == name.lower() else "incoming"
        )
        resp_relationships.append(
            {
                "source": r.get("source_name", ""),
                "target": r.get("target_name", ""),
                "relationship_type": r.get("relationship_type", ""),
                "weight": r.get("weight", 1.0),
                "properties": _safe_json(r.get("properties", {})),
                "direction": direction,
            }
        )

    return web.json_response(
        {
            "name": entity.get("name", ""),
            "entity_type": entity.get("entity_type", ""),
            "description": entity.get("description", ""),
            "properties": _safe_json(entity.get("properties", {})),
            "observations": resp_observations,
            "relationships": resp_relationships,
        }
    )


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------


async def handle_search(request: web.Request) -> web.Response:
    """Hybrid search over entities."""
    search = request.app["search"]

    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response(
            {"error": "Missing required query parameter 'q'"},
            status=400,
        )

    limit = _parse_int(request.query.get("limit"), default=10, minimum=1, maximum=100)

    entity_types_raw = request.query.get("entity_types", "")
    entity_types: list[str] | None = (
        [t.strip() for t in entity_types_raw.split(",") if t.strip()] if entity_types_raw else None
    )

    results = await search.search_entities(
        query,
        limit=limit,
        entity_types=entity_types,
        include_observations=True,
    )

    resp_results = []
    for r in results:
        matched_obs = [str(o.get("content", "")) for o in r.get("observations", [])]
        resp_results.append(
            {
                "name": r.get("name", ""),
                "entity_type": r.get("entity_type", ""),
                "description": r.get("description", ""),
                "score": r.get("relevance_score", 0.0),
                "matched_observations": matched_obs,
            }
        )

    return web.json_response(
        {
            "query": query,
            "results": resp_results,
            "total_results": len(resp_results),
        }
    )


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------


async def handle_stats(request: web.Request) -> web.Response:
    """Return aggregate graph statistics."""
    storage = request.app["storage"]

    entity_count = await storage.count_entities()
    relationship_count = await storage.count_relationships()
    observation_count = await storage.count_observations()
    entity_type_dist = await storage.entity_type_distribution()
    rel_type_dist = await storage.relationship_type_distribution()
    most_connected = await storage.most_connected_entities(limit=10)
    recently_updated = await storage.recent_entities(limit=10)

    return web.json_response(
        {
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            "observation_count": observation_count,
            "entity_type_distribution": entity_type_dist,
            "relationship_type_distribution": rel_type_dist,
            "most_connected_entities": [
                {
                    "name": e.get("name", ""),
                    "connection_count": e.get("connection_count", 0),
                }
                for e in most_connected
            ],
            "recently_updated": [
                {
                    "name": e.get("name", ""),
                    "updated_at": e.get("updated_at"),
                }
                for e in recently_updated
            ],
        }
    )


# ---------------------------------------------------------------------------
# SPA serving
# ---------------------------------------------------------------------------


async def _handle_index(request: web.Request) -> web.StreamResponse:
    """Serve ``index.html`` at the root."""
    frontend_dir: Path | None = request.app.get("frontend_dir")
    if frontend_dir is None:
        return _no_frontend_response()
    return web.FileResponse(frontend_dir / "index.html")


async def _handle_spa_fallback(request: web.Request) -> web.StreamResponse:
    """SPA fallback — serve ``index.html`` for all non-API paths."""
    frontend_dir: Path | None = request.app.get("frontend_dir")
    if frontend_dir is None:
        return _no_frontend_response()
    # Serve actual files if they exist, otherwise fall back to index.html
    requested = frontend_dir / request.match_info["path"]
    if requested.is_file():
        return web.FileResponse(requested)
    return web.FileResponse(frontend_dir / "index.html")


async def _handle_no_frontend(request: web.Request) -> web.Response:
    """Placeholder when the frontend hasn't been built yet."""
    return _no_frontend_response()


def _no_frontend_response() -> web.Response:
    return web.Response(
        text=(
            "<html><body>"
            "<h1>graphrag-mcp UI</h1>"
            "<p>Frontend not built yet. API is available at <code>/api/</code>.</p>"
            "<ul>"
            "<li><a href='/api/stats'>/api/stats</a></li>"
            "<li><a href='/api/graph'>/api/graph</a></li>"
            "<li><a href='/api/search?q=test'>/api/search?q=test</a></li>"
            "</ul>"
            "</body></html>"
        ),
        content_type="text/html",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_int(
    raw: str | None,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 10000,
) -> int:
    """Parse and clamp an integer query parameter."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _safe_json(value: object) -> object:
    """Ensure *value* is JSON-serialisable.

    If it's already a dict/list/str/int/float/bool/None, pass through.
    Otherwise, coerce to string to avoid serialisation errors.
    """
    if isinstance(value, (dict, list, str, int, float, bool, type(None))):
        return value
    return str(value)
