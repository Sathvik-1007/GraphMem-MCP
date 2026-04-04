"""API route handlers for the graph-mem visualisation UI.

All endpoints are read-only (GET) for graph data, with write endpoints
for entity/relationship/observation management.  Handlers pull ``storage``
and ``search`` from ``request.app`` — no direct database connections.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

if TYPE_CHECKING:
    from pathlib import Path

from aiohttp import web

from graph_mem.utils import GraphMemError, get_logger

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
    app.router.add_post("/api/entity", handle_create_entity)
    app.router.add_post("/api/relationship", handle_create_relationship)
    app.router.add_post("/api/observations", handle_create_observations)
    app.router.add_put("/api/entity/{name}", handle_update_entity)
    app.router.add_delete("/api/entity/{name}", handle_delete_entity)
    app.router.add_get("/api/graphs", handle_list_graphs)
    app.router.add_post("/api/graphs/switch", handle_switch_graph)

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
    try:
        entity = await storage.get_entity_by_name(name)
        if entity is None:
            entity = await storage.get_entity_by_name_nocase(name)
    except (sqlite3.Error, GraphMemError) as exc:
        log.error("Error resolving entity %r: %s", name, exc)
        return web.json_response(
            {"error": f"Error resolving entity: {exc}", "name": name},
            status=500,
        )

    if entity is None:
        return web.json_response(
            {"error": "Entity not found", "name": name},
            status=404,
        )

    entity_id = str(entity["id"])

    # Fetch observations and relationships — wrap each in try/except
    # so a failure in one doesn't prevent the other from loading
    observations: list[dict] = []
    try:
        observations = await storage.get_observations_for_entity(entity_id)
    except (sqlite3.Error, GraphMemError) as exc:
        log.warning("Failed to fetch observations for %r: %s", name, exc)

    rel_rows: list[dict] = []
    try:
        rel_rows = await storage.get_relationships_for_entity(entity_id)
    except (sqlite3.Error, GraphMemError) as exc:
        log.warning("Failed to fetch relationships for %r: %s", name, exc)

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

    try:
        entity_count = await storage.count_entities()
    except (sqlite3.Error, GraphMemError):
        entity_count = 0
    try:
        relationship_count = await storage.count_relationships()
    except (sqlite3.Error, GraphMemError):
        relationship_count = 0
    try:
        observation_count = await storage.count_observations()
    except (sqlite3.Error, GraphMemError):
        observation_count = 0
    try:
        entity_type_dist = await storage.entity_type_distribution()
    except (sqlite3.Error, GraphMemError):
        entity_type_dist = {}
    try:
        rel_type_dist = await storage.relationship_type_distribution()
    except (sqlite3.Error, GraphMemError):
        rel_type_dist = {}
    try:
        most_connected = await storage.most_connected_entities(limit=10)
    except (sqlite3.Error, GraphMemError):
        most_connected = []
    try:
        recently_updated = await storage.recent_entities(limit=10)
    except (sqlite3.Error, GraphMemError):
        recently_updated = []

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
                    "connection_count": e.get("degree", e.get("connection_count", 0)),
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
# POST /api/entity — Create a new entity
# ---------------------------------------------------------------------------


async def handle_create_entity(request: web.Request) -> web.Response:
    """Create a new entity from the UI."""
    from graph_mem.models.entity import Entity

    graph = request.app.get("graph")
    if graph is None:
        return web.json_response({"error": "Graph engine not available"}, status=500)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = body.get("name", "").strip()
    entity_type = body.get("entity_type", "").strip()
    description = body.get("description", "").strip()

    if not name:
        return web.json_response({"error": "name is required"}, status=400)
    if not entity_type:
        return web.json_response({"error": "entity_type is required"}, status=400)

    try:
        entity = Entity(name=name, entity_type=entity_type, description=description)
        results = await graph.add_entities([entity])
        result = results[0] if results else None
        entity_id = result["id"] if result else name
    except (GraphMemError, ValueError, TypeError) as exc:
        log.error("Failed to create entity: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"id": str(entity_id), "name": name}, status=201)


# ---------------------------------------------------------------------------
# POST /api/relationship — Create a new relationship
# ---------------------------------------------------------------------------


async def handle_create_relationship(request: web.Request) -> web.Response:
    """Create a new relationship between two entities from the UI."""
    from graph_mem.models.relationship import Relationship

    graph = request.app.get("graph")
    if graph is None:
        return web.json_response({"error": "Graph engine not available"}, status=500)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    source_name = body.get("source", "").strip()
    target_name = body.get("target", "").strip()
    rel_type = body.get("relationship_type", "").strip()
    weight = body.get("weight", 1.0)

    if not source_name or not target_name:
        return web.json_response({"error": "source and target are required"}, status=400)
    if not rel_type:
        return web.json_response({"error": "relationship_type is required"}, status=400)

    # Resolve entity names to IDs via graph engine
    try:
        source_entity = await graph.resolve_entity(source_name)
    except GraphMemError:
        return web.json_response({"error": f"Source entity '{source_name}' not found"}, status=404)
    try:
        target_entity = await graph.resolve_entity(target_name)
    except GraphMemError:
        return web.json_response({"error": f"Target entity '{target_name}' not found"}, status=404)

    try:
        rel = Relationship(
            source_id=source_entity.id,
            target_id=target_entity.id,
            relationship_type=rel_type,
            weight=float(weight),
        )
        results = await graph.add_relationships([rel])
        result = results[0] if results else None
        rel_id = result["id"] if result else rel.id
    except (GraphMemError, ValueError, TypeError) as exc:
        log.error("Failed to create relationship: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"id": str(rel_id)}, status=201)


# ---------------------------------------------------------------------------
# POST /api/observations — Add observations to an existing entity
# ---------------------------------------------------------------------------


async def handle_create_observations(request: web.Request) -> web.Response:
    """Add observations to an existing entity from the UI."""
    from graph_mem.models.observation import Observation

    graph = request.app.get("graph")
    if graph is None:
        return web.json_response({"error": "Graph engine not available"}, status=500)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    entity_name = body.get("entity_name", "").strip()
    observations = body.get("observations", [])

    if not entity_name:
        return web.json_response({"error": "entity_name is required"}, status=400)
    if not observations or not isinstance(observations, list):
        return web.json_response({"error": "observations must be a non-empty list"}, status=400)

    obs_objs = [Observation.pending(content=str(o)) for o in observations if str(o).strip()]
    if not obs_objs:
        return web.json_response({"error": "No valid observations provided"}, status=400)

    try:
        results = await graph.add_observations(entity_name, obs_objs)
    except (GraphMemError, ValueError) as exc:
        log.error("Failed to add observations: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response(
        {"status": "ok", "count": len(results)},
        status=201,
    )


# ---------------------------------------------------------------------------
# PUT /api/entity/{name} — Update an entity
# ---------------------------------------------------------------------------


async def handle_update_entity(request: web.Request) -> web.Response:
    """Update an existing entity's description, type, name, or properties."""
    from graph_mem.utils.errors import EntityNotFoundError

    graph = request.app.get("graph")
    if graph is None:
        return web.json_response({"error": "Graph engine not available"}, status=500)

    raw_name = request.match_info["name"]
    name = unquote(raw_name)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    new_name = body.get("name")
    description = body.get("description")
    entity_type = body.get("entity_type")
    properties = body.get("properties")

    try:
        updated = await graph.update_entity(
            name,
            new_name=new_name,
            description=description,
            entity_type=entity_type,
            properties=properties,
        )
    except EntityNotFoundError as exc:
        log.warning("Entity not found for update %r: %s", name, exc)
        return web.json_response({"error": str(exc)}, status=404)
    except ValueError as exc:
        log.warning("Invalid update for entity %r: %s", name, exc)
        return web.json_response({"error": str(exc)}, status=400)
    except GraphMemError as exc:
        log.error("Failed to update entity %r: %s", name, exc)
        return web.json_response({"error": f"Internal error: {exc}"}, status=500)

    return web.json_response(
        {
            "name": updated.name,
            "entity_type": updated.entity_type,
            "description": updated.description,
        }
    )


# ---------------------------------------------------------------------------
# DELETE /api/entity/{name} — Delete an entity
# ---------------------------------------------------------------------------


async def handle_delete_entity(request: web.Request) -> web.Response:
    """Delete an entity and its observations/relationships."""
    from graph_mem.utils.errors import EntityNotFoundError

    graph = request.app.get("graph")
    if graph is None:
        return web.json_response({"error": "Graph engine not available"}, status=500)

    raw_name = request.match_info["name"]
    name = unquote(raw_name)

    try:
        deleted = await graph.delete_entities([name])
    except EntityNotFoundError as exc:
        log.warning("Entity not found for deletion %r: %s", name, exc)
        return web.json_response({"error": f"Entity '{name}' not found"}, status=404)
    except GraphMemError as exc:
        log.error("Failed to delete entity %r: %s", name, exc, exc_info=True)
        return web.json_response({"error": f"Delete failed: {exc}"}, status=500)

    if deleted == 0:
        return web.json_response({"error": f"Entity '{name}' not found"}, status=404)

    return web.json_response({"status": "deleted", "name": name, "count": deleted})


# ---------------------------------------------------------------------------
# GET /api/graphs — List available named graphs
# ---------------------------------------------------------------------------


def _get_graphmem_dir(app: web.Application) -> Path | None:
    """Return the .graphmem directory from app config, or None."""
    from pathlib import Path as _Path

    db_path = app.get("db_path")
    if db_path:
        p = _Path(str(db_path))
        if p.parent.name == ".graphmem":
            return p.parent
    return None


def _quick_db_counts(db_file: Path) -> dict[str, int]:
    """Open a .db file briefly with sqlite3 to get entity/rel/obs counts."""
    counts: dict[str, int] = {
        "entities": 0,
        "relationships": 0,
        "observations": 0,
    }
    try:
        conn = sqlite3.connect(str(db_file))
        for table, key in [
            ("entities", "entities"),
            ("relationships", "relationships"),
            ("observations", "observations"),
        ]:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[key] = row[0] if row else 0
            except sqlite3.OperationalError:
                pass
        conn.close()
    except (sqlite3.Error, OSError):
        pass
    return counts


async def handle_list_graphs(request: web.Request) -> web.Response:
    """List all named graphs in the .graphmem directory."""
    graphmem_dir = _get_graphmem_dir(request.app)
    if graphmem_dir is None or not graphmem_dir.is_dir():
        return web.json_response({"graphs": [], "active": None})

    # Determine active graph name from current db_path
    db_path = request.app.get("db_path", "")
    from pathlib import Path as _Path

    active_file = _Path(str(db_path)).name if db_path else ""
    active_name = active_file.removesuffix(".db") if active_file else ""

    graphs = []
    for f in sorted(graphmem_dir.glob("*.db")):
        name = f.stem
        counts = _quick_db_counts(f)
        graphs.append(
            {
                "name": name,
                "file": f.name,
                "entities": counts["entities"],
                "relationships": counts["relationships"],
                "observations": counts["observations"],
                "active": (f.name == active_file),
            }
        )

    return web.json_response(
        {
            "graphs": graphs,
            "active": active_name,
            "graphmem_dir": str(graphmem_dir),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/graphs/switch — Switch to a different named graph
# ---------------------------------------------------------------------------


async def handle_switch_graph(request: web.Request) -> web.Response:
    """Switch the active graph database.

    Expects JSON body: {"name": "graph-name"} (without .db extension).
    Reinitialises storage, search, and graph engines on the app.
    """
    graphmem_dir = _get_graphmem_dir(request.app)
    if graphmem_dir is None:
        return web.json_response(
            {"error": "Cannot determine .graphmem directory"},
            status=500,
        )

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    graph_name = body.get("name", "").strip()
    if not graph_name:
        return web.json_response(
            {"error": "name is required"},
            status=400,
        )

    # Sanitise: only alphanumeric, hyphens, underscores
    if not re.match(r"^[a-zA-Z0-9_-]+$", graph_name):
        return web.json_response(
            {"error": "Invalid graph name (use a-z, 0-9, hyphens, underscores)"},
            status=400,
        )

    target_db = graphmem_dir / f"{graph_name}.db"
    if not target_db.exists():
        return web.json_response(
            {"error": f"Graph '{graph_name}' not found"},
            status=404,
        )

    # Close old storage
    old_storage = request.app.get("storage")
    if old_storage is not None:
        try:
            await old_storage.close()
        except (GraphMemError, OSError) as exc:
            log.warning("Error closing old storage: %s", exc)

    # Create new storage, graph, search engines
    from graph_mem.graph.engine import GraphEngine
    from graph_mem.semantic import EmbeddingEngine, HybridSearch
    from graph_mem.storage import create_backend
    from graph_mem.utils import load_config

    config = load_config()
    new_storage = create_backend(
        config.backend_type,
        db_path=str(target_db),
    )
    await new_storage.initialize()

    embeddings = EmbeddingEngine(
        model_name=config.embedding_model,
        use_onnx=config.use_onnx,
        device=config.embedding_device,
        cache_size=config.cache_size,
    )
    try:
        await embeddings.initialize(new_storage)
    except (GraphMemError, OSError):
        log.warning("Embedding engine unavailable after switch")

    new_search = HybridSearch(new_storage, embeddings)
    new_graph = GraphEngine(new_storage)

    # Hot-swap on the app
    request.app["storage"] = new_storage
    request.app["search"] = new_search
    request.app["graph"] = new_graph
    request.app["db_path"] = str(target_db)

    log.info("Switched to graph: %s (%s)", graph_name, target_db)
    return web.json_response(
        {
            "status": "switched",
            "name": graph_name,
            "db_path": str(target_db),
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
            "<h1>graph-mem UI</h1>"
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
    """Ensure *value* is a parsed, JSON-serialisable object.

    If *value* is a JSON-encoded string (common for SQLite TEXT columns
    that store ``{...}`` or ``[...]``), parse it first so the API returns
    a real object instead of a raw string that the frontend would iterate
    character-by-character.
    """
    import json as _json

    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[0] in ("{", "["):
            try:
                return _json.loads(stripped)
            except (ValueError, TypeError):
                pass
        return value
    if isinstance(value, (dict, list, int, float, bool, type(None))):
        return value
    return str(value)
