"""Tests for the graphrag-mcp UI API routes.

Uses aiohttp.test_utils to exercise each endpoint with mock storage/search.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from graphrag_mcp.ui.routes import setup_routes

# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockStorage:
    """Minimal mock of StorageBackend for UI route tests."""

    async def list_entities(self, entity_type=None, limit=100, offset=0):
        entities = [
            {
                "id": "1",
                "name": "AuthService",
                "entity_type": "module",
                "description": "Auth",
                "properties": {},
            },
            {
                "id": "2",
                "name": "TokenStore",
                "entity_type": "module",
                "description": "Tokens",
                "properties": {},
            },
            {
                "id": "3",
                "name": "process_login",
                "entity_type": "function",
                "description": "Login",
                "properties": {},
            },
        ]
        if entity_type:
            entities = [e for e in entities if e["entity_type"] == entity_type]
        return entities[:limit]

    async def count_entities(self):
        return 3

    async def count_relationships(self):
        return 2

    async def count_observations(self):
        return 5

    async def entity_type_distribution(self):
        return {"module": 2, "function": 1}

    async def relationship_type_distribution(self):
        return {"DEPENDS_ON": 1, "CALLS": 1}

    async def most_connected_entities(self, limit=10):
        return [{"name": "AuthService", "connection_count": 3}]

    async def recent_entities(self, limit=10):
        return [{"name": "AuthService", "updated_at": "2026-03-31T10:00:00"}]

    async def get_entity_by_name(self, name, entity_type=None):
        entities = {
            "AuthService": {
                "id": "1",
                "name": "AuthService",
                "entity_type": "module",
                "description": "Handles auth",
                "properties": {},
            },
            "My Entity": {
                "id": "4",
                "name": "My Entity",
                "entity_type": "concept",
                "description": "Has spaces",
                "properties": {},
            },
        }
        return entities.get(name)

    async def get_entity_by_name_nocase(self, name):
        return await self.get_entity_by_name(name)

    async def get_observations_for_entity(self, entity_id):
        return [
            {
                "content": "Migrated to Paseto",
                "source": "session-1",
                "created_at": "2026-03-20T14:30:00",
            }
        ]

    async def get_relationships_for_entity(self, entity_id, direction="both"):
        return [
            {
                "source_name": "AuthService",
                "target_name": "TokenStore",
                "relationship_type": "DEPENDS_ON",
                "weight": 1.0,
                "properties": {},
            }
        ]

    async def fetch_relationships_between(self, entity_ids):
        return [
            {
                "source_name": "AuthService",
                "target_name": "TokenStore",
                "relationship_type": "DEPENDS_ON",
                "weight": 1.0,
                "properties": {},
            }
        ]


class MockSearch:
    """Minimal mock of HybridSearch for UI route tests."""

    async def search_entities(
        self, query, limit=10, entity_types=None, include_observations=False, **kwargs
    ):
        if not query:
            return []
        return [
            {
                "id": "1",
                "name": "AuthService",
                "entity_type": "module",
                "description": "Handles auth",
                "relevance_score": 0.85,
                "observations": [{"content": "Migrated to Paseto"}] if include_observations else [],
            }
        ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FRONTEND_DIR = (
    Path(__file__).resolve().parent.parent.parent / "src" / "graphrag_mcp" / "ui" / "frontend"
)


def _make_app(*, with_frontend: bool = False) -> web.Application:
    """Build a minimal aiohttp app wired to mocks."""
    app = web.Application()
    app["storage"] = MockStorage()
    app["search"] = MockSearch()
    app["frontend_dir"] = FRONTEND_DIR if (with_frontend and FRONTEND_DIR.is_dir()) else None
    setup_routes(app)
    return app


@pytest.fixture
async def client(aiohttp_client):
    """Test client without frontend (API-only)."""
    app = _make_app(with_frontend=False)
    return await aiohttp_client(app)


@pytest.fixture
async def client_with_frontend(aiohttp_client):
    """Test client with frontend directory wired up."""
    app = _make_app(with_frontend=True)
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# /api/graph
# ---------------------------------------------------------------------------


async def test_api_graph_returns_entities(client):
    """/api/graph returns entities and relationships."""
    resp = await client.get("/api/graph")
    assert resp.status == 200
    data = await resp.json()
    assert "entities" in data
    assert "relationships" in data
    assert len(data["entities"]) == 3
    assert data["total_entities"] == 3
    assert data["total_relationships"] == 2
    # Verify entity shape
    names = {e["name"] for e in data["entities"]}
    assert "AuthService" in names
    assert "TokenStore" in names
    # Verify relationship shape
    assert len(data["relationships"]) >= 1
    rel = data["relationships"][0]
    assert rel["source"] == "AuthService"
    assert rel["target"] == "TokenStore"
    assert rel["relationship_type"] == "DEPENDS_ON"


async def test_api_graph_type_filter(client):
    """`?entity_types=module` filters correctly."""
    resp = await client.get("/api/graph", params={"entity_types": "module"})
    assert resp.status == 200
    data = await resp.json()
    # Only modules should appear
    for entity in data["entities"]:
        assert entity["entity_type"] == "module"
    assert len(data["entities"]) == 2


async def test_api_graph_limit(client):
    """`?limit=1` returns at most 1 entity."""
    resp = await client.get("/api/graph", params={"limit": "1"})
    assert resp.status == 200
    data = await resp.json()
    assert len(data["entities"]) <= 1


# ---------------------------------------------------------------------------
# /api/entity/{name}
# ---------------------------------------------------------------------------


async def test_api_entity_found(client):
    """/api/entity/AuthService returns full details."""
    resp = await client.get("/api/entity/AuthService")
    assert resp.status == 200
    data = await resp.json()
    assert data["name"] == "AuthService"
    assert data["entity_type"] == "module"
    assert data["description"] == "Handles auth"
    # Observations
    assert len(data["observations"]) >= 1
    assert data["observations"][0]["content"] == "Migrated to Paseto"
    # Relationships
    assert len(data["relationships"]) >= 1
    rel = data["relationships"][0]
    assert rel["source"] == "AuthService"
    assert rel["target"] == "TokenStore"
    assert "direction" in rel


async def test_api_entity_not_found(client):
    """/api/entity/Nonexistent returns 404."""
    resp = await client.get("/api/entity/Nonexistent")
    assert resp.status == 404
    data = await resp.json()
    assert "error" in data
    assert data["name"] == "Nonexistent"


async def test_api_entity_url_encoded(client):
    """/api/entity/My%20Entity handles URL encoding."""
    resp = await client.get("/api/entity/My%20Entity")
    assert resp.status == 200
    data = await resp.json()
    assert data["name"] == "My Entity"
    assert data["entity_type"] == "concept"


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------


async def test_api_search_returns_results(client):
    """/api/search?q=auth returns scored results."""
    resp = await client.get("/api/search", params={"q": "auth"})
    assert resp.status == 200
    data = await resp.json()
    assert data["query"] == "auth"
    assert data["total_results"] >= 1
    result = data["results"][0]
    assert result["name"] == "AuthService"
    assert result["score"] == pytest.approx(0.85)
    assert "matched_observations" in result


async def test_api_search_empty_query(client):
    """/api/search?q= returns 400."""
    resp = await client.get("/api/search", params={"q": ""})
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------


async def test_api_stats_counts(client):
    """/api/stats returns correct counts."""
    resp = await client.get("/api/stats")
    assert resp.status == 200
    data = await resp.json()
    assert data["entity_count"] == 3
    assert data["relationship_count"] == 2
    assert data["observation_count"] == 5
    assert len(data["most_connected_entities"]) >= 1
    assert data["most_connected_entities"][0]["name"] == "AuthService"
    assert len(data["recently_updated"]) >= 1


async def test_api_stats_distributions(client):
    """Type distributions match actual data."""
    resp = await client.get("/api/stats")
    assert resp.status == 200
    data = await resp.json()
    et_dist = data["entity_type_distribution"]
    assert et_dist == {"module": 2, "function": 1}
    rt_dist = data["relationship_type_distribution"]
    assert rt_dist == {"DEPENDS_ON": 1, "CALLS": 1}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_ui_cli_command_exists():
    """`graphrag-mcp ui --help` doesn't error."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "graphrag_mcp.cli.main", "ui", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd="/home/sathvik/D/N/graphrag-mcp",
    )
    # Click prints help text to stdout and exits 0
    assert result.returncode == 0
    assert "ui" in result.stdout.lower() or "launch" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Safety: no write endpoints
# ---------------------------------------------------------------------------


async def test_ui_no_write_endpoints(client):
    """No POST/PUT/DELETE routes exist."""
    write_methods = {"POST", "PUT", "DELETE", "PATCH"}
    for route in client.app.router.routes():
        method = route.method
        if method == "*":
            # Wildcard route — skip (used by static resources)
            continue
        assert method not in write_methods, f"Found {method} route: {route.resource}"


# ---------------------------------------------------------------------------
# Frontend / index.html
# ---------------------------------------------------------------------------


async def test_index_html_served(client_with_frontend):
    """GET `/` returns HTML with correct content-type."""
    resp = await client_with_frontend.get("/")
    assert resp.status == 200
    ct = resp.headers.get("Content-Type", "")
    assert "text/html" in ct
    body = await resp.text()
    assert "<!DOCTYPE html>" in body or "<html" in body
