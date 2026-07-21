"""Tests for the graph-mem UI API routes.

Uses aiohttp.test_utils to exercise each endpoint with mock storage/search.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from graph_mem.ui._keys import (
    db_path_key,
    frontend_dir_key,
    graph_key,
    search_key,
    storage_key,
)
from graph_mem.ui.routes import setup_routes

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
    Path(__file__).resolve().parent.parent.parent / "src" / "graph_mem" / "ui" / "frontend"
)


def _make_app(*, with_frontend: bool = False) -> web.Application:
    """Build a minimal aiohttp app wired to mocks."""
    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[frontend_dir_key] = FRONTEND_DIR if (with_frontend and FRONTEND_DIR.is_dir()) else None
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


def test_ui_cli_command_exists(tmp_path):
    """`graph-mem ui --help` exits 0 and prints help from an unrelated cwd."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import graph_mem

    # Run from a directory with no relation to the project so nothing is picked
    # up implicitly from the cwd.  The child process therefore needs to be told
    # where the package lives; deriving it from the imported module keeps this
    # correct whether the suite runs against a source checkout or an install.
    package_parent = str(Path(graph_mem.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": package_parent}

    result = subprocess.run(
        [sys.executable, "-m", "graph_mem.cli.main", "ui", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout
    assert "--graph" in result.stdout


# ---------------------------------------------------------------------------
# Safety: no write endpoints
# ---------------------------------------------------------------------------


async def test_ui_write_endpoints_exist(client):
    """POST routes exist for entity/relationship/observation creation."""
    post_routes = []
    for route in client.app.router.routes():
        if route.method == "POST":
            post_routes.append(str(route.resource))
    # We expect at least the entity, relationship, and observations POST routes
    assert len(post_routes) >= 3, f"Expected >=3 POST routes, got {post_routes}"


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


# ---------------------------------------------------------------------------
# MockGraph — used by write/mutate endpoints
# ---------------------------------------------------------------------------


class _FakeEntity:
    """Minimal stand-in for Entity returned by graph engine."""

    def __init__(self, name="TestEntity", entity_type="concept", description="desc"):
        self.id = "fake-id-1"
        self.name = name
        self.entity_type = entity_type
        self.description = description


class MockGraph:
    """Minimal mock of GraphEngine for write-endpoint tests."""

    async def add_entities(self, entities):
        return [{"id": "new-ent-1", "name": entities[0].name}]

    async def resolve_entity(self, name):
        if name == "Unknown":
            from graph_mem.utils.errors import GraphMemError

            raise GraphMemError(f"Entity '{name}' not found")
        return _FakeEntity(name=name)

    async def add_relationships(self, rels):
        return [{"id": "new-rel-1"}]

    async def add_observations(self, entity_name, obs_list):
        return [{"id": f"obs-{i}"} for i in range(len(obs_list))]

    async def update_entity(
        self, name, *, new_name=None, description=None, entity_type=None, properties=None
    ):
        if name == "Ghost":
            from graph_mem.utils.errors import EntityNotFoundError

            raise EntityNotFoundError(name)
        return _FakeEntity(
            name=new_name or name,
            entity_type=entity_type or "concept",
            description=description or "updated",
        )

    async def delete_entities(self, names):
        if names[0] == "Ghost":
            return 0
        return 1

    async def update_observation(self, entity_name, obs_id, content):
        return {"id": obs_id, "content": content}

    async def delete_observations(self, entity_name, obs_ids):
        return len(obs_ids)


def _make_app_with_graph(*, with_frontend: bool = False) -> web.Application:
    """Build app wired to mocks including a MockGraph."""
    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[graph_key] = MockGraph()
    app[frontend_dir_key] = FRONTEND_DIR if (with_frontend and FRONTEND_DIR.is_dir()) else None
    setup_routes(app)
    return app


@pytest.fixture
async def gclient(aiohttp_client):
    """Test client with graph engine (write endpoints)."""
    app = _make_app_with_graph()
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# POST /api/entity — Create entity
# ---------------------------------------------------------------------------


async def test_create_entity_success(gclient):
    """POST /api/entity with valid JSON creates entity."""
    resp = await gclient.post(
        "/api/entity",
        json={
            "name": "NewNode",
            "entity_type": "concept",
            "description": "A new concept",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["name"] == "NewNode"
    assert "id" in data


async def test_create_entity_missing_name(gclient):
    """POST /api/entity without name returns 400."""
    resp = await gclient.post(
        "/api/entity",
        json={
            "entity_type": "concept",
        },
    )
    assert resp.status == 400
    data = await resp.json()
    assert "name" in data["error"].lower()


async def test_create_entity_missing_type(gclient):
    """POST /api/entity without entity_type returns 400."""
    resp = await gclient.post(
        "/api/entity",
        json={
            "name": "Foo",
        },
    )
    assert resp.status == 400
    data = await resp.json()
    assert "entity_type" in data["error"]


async def test_create_entity_invalid_json(gclient):
    """POST /api/entity with bad body returns 400."""
    resp = await gclient.post(
        "/api/entity", data=b"not json", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400


async def test_create_entity_no_graph(client):
    """POST /api/entity without graph engine returns 500."""
    resp = await client.post(
        "/api/entity",
        json={
            "name": "X",
            "entity_type": "t",
        },
    )
    assert resp.status == 500
    data = await resp.json()
    assert "not available" in data["error"].lower()


# ---------------------------------------------------------------------------
# POST /api/relationship — Create relationship
# ---------------------------------------------------------------------------


async def test_create_relationship_success(gclient):
    """POST /api/relationship with valid data returns 201."""
    resp = await gclient.post(
        "/api/relationship",
        json={
            "source": "AuthService",
            "target": "TokenStore",
            "relationship_type": "DEPENDS_ON",
            "weight": 0.8,
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert "id" in data


async def test_create_relationship_missing_fields(gclient):
    """POST /api/relationship without source/target returns 400."""
    resp = await gclient.post(
        "/api/relationship",
        json={
            "relationship_type": "CALLS",
        },
    )
    assert resp.status == 400


async def test_create_relationship_missing_type(gclient):
    """POST /api/relationship without relationship_type returns 400."""
    resp = await gclient.post(
        "/api/relationship",
        json={
            "source": "A",
            "target": "B",
        },
    )
    assert resp.status == 400


async def test_create_relationship_source_not_found(gclient):
    """POST /api/relationship with unknown source returns 404."""
    resp = await gclient.post(
        "/api/relationship",
        json={
            "source": "Unknown",
            "target": "TokenStore",
            "relationship_type": "CALLS",
        },
    )
    assert resp.status == 404


async def test_create_relationship_invalid_json(gclient):
    """POST /api/relationship with bad body returns 400."""
    resp = await gclient.post(
        "/api/relationship", data=b"{bad", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# POST /api/observations — Add observations
# ---------------------------------------------------------------------------


async def test_create_observations_success(gclient):
    """POST /api/observations with valid data returns 201."""
    resp = await gclient.post(
        "/api/observations",
        json={
            "entity_name": "AuthService",
            "observations": ["Fact one", "Fact two"],
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["count"] == 2


async def test_create_observations_missing_entity(gclient):
    """POST /api/observations without entity_name returns 400."""
    resp = await gclient.post(
        "/api/observations",
        json={
            "observations": ["Something"],
        },
    )
    assert resp.status == 400


async def test_create_observations_empty_list(gclient):
    """POST /api/observations with empty list returns 400."""
    resp = await gclient.post(
        "/api/observations",
        json={
            "entity_name": "AuthService",
            "observations": [],
        },
    )
    assert resp.status == 400


async def test_create_observations_invalid_json(gclient):
    """POST /api/observations with bad body returns 400."""
    resp = await gclient.post(
        "/api/observations", data=b"nope", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400


async def test_create_observations_no_graph(client):
    """POST /api/observations without graph engine returns 500."""
    resp = await client.post(
        "/api/observations",
        json={
            "entity_name": "X",
            "observations": ["y"],
        },
    )
    assert resp.status == 500


# ---------------------------------------------------------------------------
# PUT /api/entity/{name} — Update entity
# ---------------------------------------------------------------------------


async def test_update_entity_success(gclient):
    """PUT /api/entity/AuthService updates and returns entity."""
    resp = await gclient.put(
        "/api/entity/AuthService",
        json={
            "description": "Updated auth service",
            "entity_type": "service",
        },
    )
    assert resp.status == 200
    data = await resp.json()
    assert "name" in data
    assert "entity_type" in data


async def test_update_entity_not_found(gclient):
    """PUT /api/entity/Ghost returns 404."""
    resp = await gclient.put(
        "/api/entity/Ghost",
        json={
            "description": "nope",
        },
    )
    assert resp.status == 404


async def test_update_entity_invalid_json(gclient):
    """PUT /api/entity/X with bad body returns 400."""
    resp = await gclient.put(
        "/api/entity/AuthService", data=b"bad", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400


async def test_update_entity_no_graph(client):
    """PUT /api/entity/X without graph engine returns 500."""
    resp = await client.put("/api/entity/AuthService", json={"description": "x"})
    assert resp.status == 500


# ---------------------------------------------------------------------------
# DELETE /api/entity/{name} — Delete entity
# ---------------------------------------------------------------------------


async def test_delete_entity_success(gclient):
    """DELETE /api/entity/AuthService deletes and returns ok."""
    resp = await gclient.delete("/api/entity/AuthService")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "deleted"
    assert data["count"] == 1


async def test_delete_entity_not_found(gclient):
    """DELETE /api/entity/Ghost returns 404."""
    resp = await gclient.delete("/api/entity/Ghost")
    assert resp.status == 404


async def test_delete_entity_no_graph(client):
    """DELETE /api/entity/X without graph engine returns 500."""
    resp = await client.delete("/api/entity/AuthService")
    assert resp.status == 500


# ---------------------------------------------------------------------------
# PUT /api/observation/{obs_id} — Update observation
# ---------------------------------------------------------------------------


async def test_update_observation_success(gclient):
    """PUT /api/observation/{id} updates content."""
    resp = await gclient.put(
        "/api/observation/obs-123",
        json={
            "entity_name": "AuthService",
            "content": "Updated fact",
        },
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["content"] == "Updated fact"


async def test_update_observation_missing_fields(gclient):
    """PUT /api/observation/{id} without entity_name/content returns 400."""
    resp = await gclient.put("/api/observation/obs-123", json={})
    assert resp.status == 400


async def test_update_observation_no_graph(client):
    """PUT /api/observation/{id} without graph returns 500."""
    resp = await client.put(
        "/api/observation/obs-123",
        json={
            "entity_name": "X",
            "content": "y",
        },
    )
    assert resp.status == 500


# ---------------------------------------------------------------------------
# DELETE /api/observation/{obs_id} — Delete observation
# ---------------------------------------------------------------------------


async def test_delete_observation_success(gclient):
    """DELETE /api/observation/{id} with query param entity_name."""
    resp = await gclient.delete("/api/observation/obs-123?entity_name=AuthService")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["deleted"] == 1


async def test_delete_observation_missing_entity(gclient):
    """DELETE /api/observation/{id} without entity_name returns 400."""
    resp = await gclient.delete("/api/observation/obs-123")
    assert resp.status == 400


async def test_delete_observation_no_graph(client):
    """DELETE /api/observation/{id} without graph returns 500."""
    resp = await client.delete("/api/observation/obs-123?entity_name=X")
    assert resp.status == 500


# ---------------------------------------------------------------------------
# GET /api/graphs — List graphs
# ---------------------------------------------------------------------------


async def test_list_graphs_no_db_path(client):
    """GET /api/graphs when no db_path returns empty list."""
    resp = await client.get("/api/graphs")
    assert resp.status == 200
    data = await resp.json()
    assert data["graphs"] == []
    assert data["active"] is None


async def test_list_graphs_with_db_path(aiohttp_client, tmp_path):
    """GET /api/graphs with valid .graphmem dir lists .db files."""
    gm_dir = tmp_path / ".graphmem"
    gm_dir.mkdir()
    # Create a fake db file with sqlite tables
    import sqlite3

    db_file = gm_dir / "testgraph.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE entities (id TEXT)")
    conn.execute("CREATE TABLE relationships (id TEXT)")
    conn.execute("CREATE TABLE observations (id TEXT)")
    conn.close()

    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[db_path_key] = str(db_file)
    app[frontend_dir_key] = None
    setup_routes(app)

    c = await aiohttp_client(app)
    resp = await c.get("/api/graphs")
    assert resp.status == 200
    data = await resp.json()
    assert len(data["graphs"]) == 1
    assert data["graphs"][0]["name"] == "testgraph"
    assert data["active"] == "testgraph"


# ---------------------------------------------------------------------------
# POST /api/graphs/switch — Switch graph
# ---------------------------------------------------------------------------


async def test_switch_graph_no_graphmem_dir(client):
    """POST /api/graphs/switch when no db_path returns 500."""
    resp = await client.post("/api/graphs/switch", json={"name": "foo"})
    assert resp.status == 500


async def test_switch_graph_missing_name(aiohttp_client, tmp_path):
    """POST /api/graphs/switch without name returns 400."""
    gm_dir = tmp_path / ".graphmem"
    gm_dir.mkdir()
    db_file = gm_dir / "graph.db"
    db_file.touch()

    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[db_path_key] = str(db_file)
    app[frontend_dir_key] = None
    setup_routes(app)

    c = await aiohttp_client(app)
    resp = await c.post("/api/graphs/switch", json={"name": ""})
    assert resp.status == 400


async def test_switch_graph_invalid_name(aiohttp_client, tmp_path):
    """POST /api/graphs/switch with bad chars returns 400."""
    gm_dir = tmp_path / ".graphmem"
    gm_dir.mkdir()
    db_file = gm_dir / "graph.db"
    db_file.touch()

    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[db_path_key] = str(db_file)
    app[frontend_dir_key] = None
    setup_routes(app)

    c = await aiohttp_client(app)
    resp = await c.post("/api/graphs/switch", json={"name": "../evil"})
    assert resp.status == 400


async def test_switch_graph_not_found(aiohttp_client, tmp_path):
    """POST /api/graphs/switch with nonexistent graph returns 404."""
    gm_dir = tmp_path / ".graphmem"
    gm_dir.mkdir()
    db_file = gm_dir / "graph.db"
    db_file.touch()

    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[db_path_key] = str(db_file)
    app[frontend_dir_key] = None
    setup_routes(app)

    c = await aiohttp_client(app)
    resp = await c.post("/api/graphs/switch", json={"name": "nonexistent"})
    assert resp.status == 404


async def test_switch_graph_invalid_json(aiohttp_client, tmp_path):
    """POST /api/graphs/switch with bad JSON returns 400."""
    gm_dir = tmp_path / ".graphmem"
    gm_dir.mkdir()
    db_file = gm_dir / "graph.db"
    db_file.touch()

    app = web.Application()
    app[storage_key] = MockStorage()
    app[search_key] = MockSearch()
    app[db_path_key] = str(db_file)
    app[frontend_dir_key] = None
    setup_routes(app)

    c = await aiohttp_client(app)
    resp = await c.post(
        "/api/graphs/switch", data=b"nope", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400
