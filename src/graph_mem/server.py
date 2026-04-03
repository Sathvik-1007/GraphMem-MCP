"""FastMCP server — registers all 23 Graph Memory MCP tools.

This is the core entry point.  A lifespan context manager initialises
shared state (storage backend, engines, search) once at startup and
tears it down on shutdown.  Each tool is a thin async wrapper that
delegates to the appropriate engine, catches ``GraphMemError`` subtypes,
and returns JSON-serialisable dicts.

The server is **storage-agnostic**: it uses :func:`storage.create_backend`
to instantiate the configured backend (SQLite, Neo4j, Memgraph, etc.).
"""

from __future__ import annotations

import socket
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from graph_mem.graph.engine import ObservationResult

from mcp.server.fastmcp import FastMCP

from graph_mem.graph import EntityMerger, GraphEngine, GraphTraversal
from graph_mem.models import Entity, Observation, Relationship
from graph_mem.semantic import EmbeddingEngine, HybridSearch
from graph_mem.storage import StorageBackend, create_backend
from graph_mem.utils import Config, GraphMemError, get_logger, load_config, setup_logging
from graph_mem.utils.errors import EntityNotFoundError

log = get_logger("server")

# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    """Mutable container for shared engine instances.

    Populated by the lifespan context manager before the server starts
    accepting requests and cleared on shutdown.
    """

    config: Config | None = None
    storage: StorageBackend | None = None
    graph: GraphEngine | None = None
    traversal: GraphTraversal | None = None
    merger: EntityMerger | None = None
    embeddings: EmbeddingEngine | None = None
    search: HybridSearch | None = None
    # UI dashboard state (managed by open_dashboard tool)
    _ui_url: str | None = None
    _ui_runner: Any | None = None
    # Multi-graph state
    _graphmem_dir: Any | None = None  # Path to .graphmem/ directory
    _active_graph: str = "default"  # Currently active graph name


@dataclass
class InitializedState:
    """Narrowed view of :class:`AppState` after successful initialization.

    All fields are guaranteed non-``None``.  Returned by
    :func:`_require_state` so callers don't need individual assertions.
    """

    config: Config
    storage: StorageBackend
    graph: GraphEngine
    traversal: GraphTraversal
    merger: EntityMerger
    embeddings: EmbeddingEngine
    search: HybridSearch


_state = AppState()


def _require_state() -> InitializedState:
    """Return the global state or raise if the server is not initialised.

    Validates that all required fields are populated and returns an
    :class:`InitializedState` with non-Optional types so callers don't
    need individual ``assert`` statements.
    """
    if (
        _state.config is None
        or _state.storage is None
        or _state.graph is None
        or _state.traversal is None
        or _state.merger is None
        or _state.embeddings is None
        or _state.search is None
    ):
        raise GraphMemError("Server not initialised.  Is the lifespan running?")
    return InitializedState(
        config=_state.config,
        storage=_state.storage,
        graph=_state.graph,
        traversal=_state.traversal,
        merger=_state.merger,
        embeddings=_state.embeddings,
        search=_state.search,
    )


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Initialise all engines on startup; close the storage on shutdown."""
    config = _state.config or load_config()
    setup_logging(config.log_level)
    log.info("Starting graph-mem server (backend=%s)", config.backend_type)

    # Create and initialize storage backend
    db_path = config.ensure_db_dir()
    storage = create_backend(config.backend_type, db_path=db_path)
    await storage.initialize()

    # Store the .graphmem directory and derive active graph name
    _state._graphmem_dir = db_path.parent
    db_stem = db_path.stem  # e.g. "graph" from "graph.db"
    _state._active_graph = db_stem if db_stem != "graph" else "default"

    embeddings = EmbeddingEngine(
        model_name=config.embedding_model,
        use_onnx=config.use_onnx,
        device=config.embedding_device,
        cache_size=config.cache_size,
    )
    # initialize() is lightweight — it stores config and reads DB metadata
    # but does NOT load the PyTorch model.  The model loads lazily on the
    # first embed() call, keeping MCP startup fast (< 2 seconds).
    await embeddings.initialize(storage)

    # Pre-warm: load the embedding model in a background thread so the first
    # search_nodes call doesn't block for 30+ seconds (which would exceed
    # typical MCP client timeouts like OpenCode's 30 000 ms default).
    def _prewarm() -> None:
        try:
            embeddings._ensure_model_loaded()
            log.info("Embedding model pre-warmed successfully in background thread")
        except Exception:
            log.warning("Background model pre-warm failed — will retry on first use", exc_info=True)

    prewarm_thread = threading.Thread(target=_prewarm, daemon=True, name="embedding-prewarm")
    prewarm_thread.start()

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings)

    _state.config = config
    _state.storage = storage
    _state.graph = graph
    _state.traversal = traversal
    _state.merger = merger
    _state.embeddings = embeddings
    _state.search = search

    log.info(
        "graph-mem server ready (backend=%s, embeddings=%s)",
        storage.backend_type,
        embeddings.available,
    )
    yield

    # Shutdown
    log.info("Shutting down graph-mem server")
    # Clean up UI dashboard server if running
    if _state._ui_runner is not None:
        try:
            await _state._ui_runner.cleanup()
        except Exception:
            log.debug("UI runner cleanup error (ignored)", exc_info=True)
        _state._ui_runner = None
        _state._ui_url = None
    await storage.close()
    _state.storage = None
    _state.graph = None
    _state.traversal = None
    _state.merger = None
    _state.embeddings = None
    _state.search = None


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp = FastMCP("graph-mem", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Helper: structured error response
# ---------------------------------------------------------------------------


def _error_response(exc: Exception, *, tool_name: str = "") -> dict[str, Any]:
    """Build a structured MCP error response dict and log the failure."""
    label = f"{tool_name}: " if tool_name else ""
    log.debug("%sfailed: %s: %s", label, type(exc).__name__, exc)
    resp: dict[str, Any] = {
        "error": True,
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    if isinstance(exc, EntityNotFoundError) and exc.suggestions:
        resp["suggestions"] = exc.suggestions
    if isinstance(exc, GraphMemError) and exc.details:
        resp["details"] = exc.details
    return resp


# ---------------------------------------------------------------------------
# Helper: compute and store embeddings for entities / observations
# ---------------------------------------------------------------------------
# These live in server.py (rather than in the graph or semantic layer)
# because they need access to *both* the graph engine (to fetch entity
# text) and the embedding engine (to compute vectors), and those two are
# only wired together at the server level via AppState.  Moving them
# into a separate module would require either passing both engines as
# arguments or creating a dedicated orchestration class — neither of
# which improves clarity given the current codebase size.
# ---------------------------------------------------------------------------


async def _embed_entities(entity_ids: list[str]) -> None:
    """Compute and upsert embeddings for the given entity IDs.

    Silently skips if the embedding engine is not available.
    """
    state = _require_state()

    if not state.embeddings.available or not entity_ids:
        return

    texts: list[str] = []
    valid_ids: list[str] = []
    for eid in entity_ids:
        try:
            entity = await state.graph.get_entity_by_id(eid)
            texts.append(entity.embedding_text)
            valid_ids.append(eid)
        except GraphMemError as exc:
            log.debug("Skipping entity %s during embedding: %s", eid, exc)
            continue

    if not texts:
        return

    vectors = await state.embeddings.embed(texts)
    for eid, vec in zip(valid_ids, vectors, strict=True):
        if vec is not None:
            await state.embeddings.upsert_entity_embedding(eid, vec)


async def _embed_observations(obs_results: list[ObservationResult]) -> None:
    """Compute and upsert embeddings for newly created observations.

    Each element of *obs_results* must have ``id`` and ``content`` keys.
    """
    state = _require_state()

    if not state.embeddings.available or not obs_results:
        return

    texts = [str(o["content"]) for o in obs_results]
    ids = [str(o["id"]) for o in obs_results]

    vectors = await state.embeddings.embed(texts)
    for oid, vec in zip(ids, vectors, strict=True):
        if vec is not None:
            await state.embeddings.upsert_observation_embedding(oid, vec)


# ═══════════════════════════════════════════════════════════════════════════
# WRITE TOOLS (10)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def add_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Add entities to the knowledge graph. Entities with the same name and type are
    automatically merged.

    Each entity needs: name (str), entity_type (str, e.g. 'person', 'concept', 'place').
    Optional: description (str), properties (dict), observations (list[str]).

    Raises:
        GraphMemError: If entity creation or embedding fails.
        KeyError/ValueError/TypeError: Returned as error dict if input is malformed.
    """
    # NOTE: Each tool wraps its body in a try/except that catches GraphMemError
    # (domain errors) and input validation errors (KeyError, ValueError, TypeError),
    # returning structured error dicts. This repetition is intentional at the MCP
    # boundary — a decorator would obscure the error handling from tool documentation.
    try:
        state = _require_state()

        # Build Entity objects
        entity_objs: list[Entity] = []
        obs_map: dict[str, list[str]] = {}  # name -> observation texts
        for raw in entities:
            name: str = raw["name"]
            entity_type: str = raw["entity_type"]
            description: str = raw.get("description", "")
            properties: dict[str, object] = raw.get("properties", {})
            observations: list[str] = raw.get("observations", [])

            entity = Entity(
                name=name,
                entity_type=entity_type,
                description=description,
                properties=properties,
            )
            entity_objs.append(entity)

            if observations:
                obs_map[name] = observations

        # Persist entities
        results = await state.graph.add_entities(entity_objs)

        # Compute entity embeddings
        entity_ids = [str(r["id"]) for r in results]
        await _embed_entities(entity_ids)

        # Add and embed observations for entities that had them
        for name, obs_texts in obs_map.items():
            obs_objs = [Observation.pending(text) for text in obs_texts]
            obs_results = await state.graph.add_observations(name, obs_objs)
            await _embed_observations(obs_results)

        return {"results": results, "count": len(results)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_entities")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="add_entities")


@mcp.tool()
async def add_relationships(relationships: list[dict[str, Any]]) -> dict[str, Any]:
    """Add relationships (edges) between entities in the knowledge graph.

    Each relationship needs: source (str, entity name), target (str, entity name),
    relationship_type (str, e.g. 'knows', 'works_at', 'depends_on').
    Optional: weight (float, 0-1, default 1.0), properties (dict).
    Duplicate edges (same source, target, type) are merged with the higher weight kept.
    """
    try:
        state = _require_state()

        rel_objs: list[Relationship] = []
        for raw in relationships:
            source_name: str = raw["source"]
            target_name: str = raw["target"]
            rel_type: str = raw["relationship_type"]
            weight: float = float(raw.get("weight", 1.0))
            properties: dict[str, object] = raw.get("properties", {})

            source_entity = await state.graph.resolve_entity(source_name)
            target_entity = await state.graph.resolve_entity(target_name)

            rel = Relationship(
                source_id=source_entity.id,
                target_id=target_entity.id,
                relationship_type=rel_type,
                weight=weight,
                properties=properties,
            )
            rel_objs.append(rel)

        results = await state.graph.add_relationships(rel_objs)

        # Enrich results with names for clarity
        enriched: list[dict[str, Any]] = []
        for raw, result in zip(relationships, results, strict=True):
            enriched.append(
                {
                    **result,
                    "source": raw["source"],
                    "target": raw["target"],
                    "relationship_type": raw["relationship_type"],
                }
            )

        return {"results": enriched, "count": len(enriched)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_relationships")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(
            GraphMemError(f"Invalid input: {exc}"), tool_name="add_relationships"
        )


@mcp.tool()
async def add_observations(
    entity_name: str,
    observations: list[str],
    source: str = "",
) -> dict[str, Any]:
    """Add factual observations to an existing entity.

    Observations are atomic statements about an entity (facts, quotes, events).
    They are embedded separately for fine-grained semantic search.

    Args:
        entity_name: Name of the entity to attach observations to.
        observations: List of observation text strings.
        source: Optional provenance string (e.g. session ID, document name).
    """
    try:
        state = _require_state()

        obs_objs = [Observation.pending(text, source=source) for text in observations]

        results = await state.graph.add_observations(entity_name, obs_objs)
        await _embed_observations(results)

        return {"results": results, "count": len(results)}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="add_observations")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="add_observations")


@mcp.tool()
async def update_entity(
    name: str,
    description: str | None = None,
    properties: dict[str, Any] | None = None,
    entity_type: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing entity. Only provided fields are changed.

    Properties are merged with existing ones (new keys added, existing keys updated).
    Pass a new description to replace the current one. Pass entity_type to reclassify.
    """
    try:
        state = _require_state()

        updated = await state.graph.update_entity(
            name,
            description=description,
            properties=properties,
            entity_type=entity_type,
        )

        # Recompute embedding if description changed
        if description is not None:
            await _embed_entities([updated.id])

        return {"result": updated.to_dict(), "status": "updated"}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_entity")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphMemError(f"Invalid input: {exc}"), tool_name="update_entity")


@mcp.tool()
async def delete_entities(names: list[str]) -> dict[str, Any]:
    """Remove entities from the knowledge graph by name.

    Cascades to delete all related observations and relationships.
    Returns the count of entities actually deleted.
    """
    try:
        state = _require_state()

        # Resolve entity IDs before deletion for embedding cleanup
        entity_ids: list[str] = []
        for name in names:
            try:
                entity = await state.graph.resolve_entity(name)
                entity_ids.append(entity.id)
            except EntityNotFoundError:
                log.debug("Entity %r not found during deletion, skipping", name)
                continue

        deleted = await state.graph.delete_entities(names)

        # Clean up embeddings for deleted entities
        if state.embeddings.available:
            for eid in entity_ids:
                try:
                    await state.embeddings.delete_entity_embedding(eid)
                except GraphMemError as emb_exc:
                    log.debug(
                        "Failed to clean up embedding for entity %s: %s — "
                        "orphaned embedding row may remain",
                        eid,
                        emb_exc,
                    )

        return {"results": names, "deleted": deleted, "count": deleted}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_entities")


@mcp.tool()
async def merge_entities(
    target: str,
    source: str,
) -> dict[str, Any]:
    """Merge two entities into one. The source entity is absorbed into the target.

    All observations and relationships from the source are moved to the target.
    Duplicate relationships are deduplicated (higher weight kept). The source
    entity is deleted after the merge.
    """
    try:
        state = _require_state()

        target_entity = await state.graph.resolve_entity(target)
        source_entity = await state.graph.resolve_entity(source)

        result = await state.merger.merge(target_entity.id, source_entity.id)

        # Recompute target embedding (description may have changed)
        await _embed_entities([target_entity.id])

        # Clean up source embedding
        if state.embeddings.available:
            try:
                await state.embeddings.delete_entity_embedding(source_entity.id)
            except GraphMemError as emb_exc:
                log.debug(
                    "Failed to clean up embedding for merged entity %s: %s — "
                    "orphaned embedding row may remain",
                    source_entity.id,
                    emb_exc,
                )

        return {"result": result, "status": "merged"}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="merge_entities")


@mcp.tool()
async def delete_relationships(
    source: str,
    target: str,
    relationship_type: str | None = None,
) -> dict[str, Any]:
    """Remove relationships between two entities by name.

    Deletes matching edges from source to target. If relationship_type is
    provided, only relationships of that type are deleted. Otherwise all
    relationships between the pair are removed.

    Args:
        source: Source entity name.
        target: Target entity name.
        relationship_type: Optional — restrict deletion to this edge type.

    Returns:
        Count of relationships deleted.
    """
    try:
        state = _require_state()

        deleted = await state.graph.delete_relationships(source, target, relationship_type)

        return {
            "source": source,
            "target": target,
            "relationship_type": relationship_type,
            "deleted": deleted,
            "status": "deleted" if deleted > 0 else "not_found",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_relationships")


@mcp.tool()
async def update_relationship(
    source: str,
    target: str,
    relationship_type: str,
    new_weight: float | None = None,
    new_type: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update an existing relationship between two entities in-place.

    Modifies weight, type, or properties of an existing edge without
    deleting and re-creating it. Properties are merged (new keys added,
    existing keys overwritten).

    Args:
        source: Source entity name.
        target: Target entity name.
        relationship_type: Current relationship type to identify the edge.
        new_weight: Optional new weight value (0.0-1.0).
        new_type: Optional new relationship type string.
        properties: Optional properties dict to merge into existing properties.
    """
    try:
        state = _require_state()

        result = await state.graph.update_relationship(
            source,
            target,
            relationship_type,
            new_weight=new_weight,
            new_type=new_type,
            properties=properties,
        )

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_relationship")


@mcp.tool()
async def delete_observations(
    entity_name: str,
    observation_ids: list[str],
) -> dict[str, Any]:
    """Remove specific observations from an entity by observation ID.

    Validates that the observations belong to the specified entity before
    deleting. Also cleans up associated embeddings.

    Args:
        entity_name: Name of the entity the observations belong to.
        observation_ids: List of observation IDs to delete.

    Returns:
        Count of observations actually deleted.
    """
    try:
        state = _require_state()

        deleted = await state.graph.delete_observations(entity_name, observation_ids)

        # Clean up observation embeddings
        if state.embeddings.available:
            for obs_id in observation_ids:
                try:
                    await state.embeddings.delete_observation_embedding(obs_id)
                except GraphMemError:
                    log.debug("Failed to clean embedding for obs %s", obs_id)

        return {
            "entity_name": entity_name,
            "deleted": deleted,
            "requested": len(observation_ids),
            "status": "deleted" if deleted > 0 else "not_found",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_observations")


@mcp.tool()
async def update_observation(
    entity_name: str,
    observation_id: str,
    content: str,
) -> dict[str, Any]:
    """Update the text content of an existing observation in-place.

    Modifies the observation content directly — does not delete and re-create.
    Recomputes the embedding for the updated content automatically.

    Args:
        entity_name: Name of the entity the observation belongs to.
        observation_id: ID of the observation to update.
        content: New text content for the observation.
    """
    try:
        state = _require_state()

        result = await state.graph.update_observation(entity_name, observation_id, content)

        # Recompute embedding for updated content
        if state.embeddings.available:
            obs_results = [{"id": observation_id, "content": content}]
            await _embed_observations(obs_results)

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="update_observation")


# ═══════════════════════════════════════════════════════════════════════════
# WRITE TOOLS (10)  |  READ TOOLS (8)  |  UTILITY (1) — 19 total
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# READ TOOLS (8)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def search_nodes(
    query: str,
    limit: int = 10,
    entity_types: list[str] | None = None,
    include_observations: bool = False,
) -> dict[str, Any]:
    """Search the knowledge graph using hybrid semantic + full-text search.

    Combines vector similarity (cosine distance) and FTS5 keyword matching
    using Reciprocal Rank Fusion for robust ranking. Returns entities sorted
    by relevance with their direct relationships.

    Args:
        query: Natural language search query.
        limit: Maximum results to return (default 10).
        entity_types: Optional filter to specific types (e.g. ['person', 'concept']).
        include_observations: Whether to include entity observations in results.

    Raises:
        GraphMemError: If the search engine or database encounters an error.
    """
    try:
        state = _require_state()

        results = await state.search.search_entities(
            query,
            limit=limit,
            entity_types=entity_types,
            include_observations=include_observations,
        )

        return {"results": results, "count": len(results), "query": query}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="search_nodes")


@mcp.tool()
async def search_observations(
    query: str,
    limit: int = 10,
    entity_name: str | None = None,
) -> dict[str, Any]:
    """Search observations using hybrid semantic + full-text search.

    Searches the text content of observations (atomic facts attached to entities)
    using combined vector similarity and FTS5 keyword matching. Useful for finding
    specific facts, events, or details that may not be reflected in entity names
    or descriptions.

    Args:
        query: Natural language search query.
        limit: Maximum results to return (default 10).
        entity_name: Optional — restrict search to observations belonging to this entity.

    Returns:
        Matching observations with their parent entity names and relevance scores.
    """
    try:
        state = _require_state()

        # If entity_name given, resolve to entity_id
        entity_id: str | None = None
        if entity_name:
            entity = await state.graph.resolve_entity(entity_name)
            entity_id = entity.id

        results = await state.search.search_observations(
            query,
            limit=limit,
            entity_id=entity_id,
        )

        return {"results": results, "count": len(results), "query": query}

    except GraphMemError as exc:
        return _error_response(exc, tool_name="search_observations")


@mcp.tool()
async def find_connections(
    entity_name: str,
    max_hops: int = 3,
    relationship_types: list[str] | None = None,
    direction: str = "both",
) -> dict[str, Any]:
    """Discover entities connected to a given entity via multi-hop graph traversal.

    Uses BFS with cycle detection. Useful for exploring the neighbourhood of
    an entity — finding related people, concepts, or dependencies.

    Args:
        entity_name: Starting entity name.
        max_hops: How far to traverse (1-10, default 3).
        relationship_types: Optional filter on edge types (e.g. ['knows', 'works_at']).
        direction: 'outgoing', 'incoming', or 'both' (default).
    """
    try:
        state = _require_state()

        entity = await state.graph.resolve_entity(entity_name)
        results = await state.traversal.find_connections(
            entity.id,
            max_hops=max_hops,
            relationship_types=relationship_types,
            direction=direction,
        )

        return {
            "source": entity_name,
            "results": results,
            "count": len(results),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="find_connections")


@mcp.tool()
async def get_entity(name: str) -> dict[str, Any]:
    """Get full details of a single entity by name, including observations and relationships.

    Uses fuzzy name resolution: exact match -> case-insensitive -> FTS5 suggestions.
    Returns the entity with all its observations and direct relationships.
    """
    try:
        state = _require_state()

        entity = await state.graph.get_entity(name)
        observations = await state.graph.get_observations(name)
        relationships = await state.graph.get_relationships(name)

        result = entity.to_dict()
        result["observations"] = [obs.to_dict() for obs in observations]
        result["relationships"] = relationships

        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="get_entity")


@mcp.tool()
async def read_graph() -> dict[str, Any]:
    """Get an overview of the knowledge graph: entity/relationship/observation counts,
    type distributions, most connected entities, and recently updated entities.

    Takes no arguments. Useful for understanding the current state of the graph.
    """
    try:
        state = _require_state()

        result: dict[str, Any] = dict(await state.graph.get_stats())
        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="read_graph")


@mcp.tool()
async def get_subgraph(
    entity_names: list[str],
    radius: int = 2,
) -> dict[str, Any]:
    """Extract a neighbourhood subgraph around one or more seed entities.

    Expands outward from each seed entity up to *radius* hops, collecting
    all reachable entities and the relationships between them. Useful for
    visualisation or context gathering around a set of topics.

    Args:
        entity_names: Seed entity names to expand from.
        radius: How many hops to expand (1-5, default 2).
    """
    try:
        state = _require_state()

        entity_ids: list[str] = []
        for name in entity_names:
            entity = await state.graph.resolve_entity(name)
            entity_ids.append(entity.id)

        result = await state.traversal.get_subgraph(entity_ids, radius=radius)
        return result

    except GraphMemError as exc:
        return _error_response(exc, tool_name="get_subgraph")


@mcp.tool()
async def find_paths(
    source: str,
    target: str,
    max_hops: int = 5,
) -> dict[str, Any]:
    """Find shortest paths between two entities in the knowledge graph.

    Uses BFS to discover how two entities are connected. Returns up to 10
    shortest paths, each as a sequence of entities and relationship types.

    Args:
        source: Starting entity name.
        target: Destination entity name.
        max_hops: Maximum path length to consider (1-10, default 5).
    """
    try:
        state = _require_state()

        source_entity = await state.graph.resolve_entity(source)
        target_entity = await state.graph.resolve_entity(target)

        paths = await state.traversal.find_paths(
            source_entity.id,
            target_entity.id,
            max_hops=max_hops,
        )

        return {
            "source": source,
            "target": target,
            "paths": paths,
            "count": len(paths),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="find_paths")


@mcp.tool()
async def list_entities(
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List all entities in the knowledge graph with optional filtering.

    Browse entities with pagination support. Useful for discovering what's
    in the graph without a specific search query, or for iterating over
    entities of a specific type.

    Args:
        entity_type: Optional — filter to only this entity type (e.g. 'person').
        limit: Maximum entities to return (default 100, max 500).
        offset: Skip this many entities for pagination (default 0).

    Returns:
        List of entity summaries with name, type, description, and counts.
    """
    try:
        state = _require_state()

        # Clamp limit to prevent excessive queries
        limit = min(max(1, limit), 500)
        offset = max(0, offset)

        entities = await state.graph.list_entities(
            entity_type=entity_type,
            limit=limit,
            offset=offset,
        )

        results = [e.to_dict() for e in entities]
        total = await state.storage.count_entities()

        return {
            "results": results,
            "count": len(results),
            "total": total,
            "limit": limit,
            "offset": offset,
            "entity_type": entity_type,
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="list_entities")


# ═══════════════════════════════════════════════════════════════════════════
# Multi-graph management
# ═══════════════════════════════════════════════════════════════════════════


def _get_graphmem_dir() -> Path:
    """Return the .graphmem directory, raising if not initialised."""
    d = _state._graphmem_dir
    if d is None:
        raise GraphMemError("Server not initialised.")
    return Path(d) if not isinstance(d, Path) else d


async def _switch_engines(db_path: Path, graph_name: str) -> dict[str, Any]:
    """Close current storage and reinitialise all engines for a new DB."""
    state = _require_state()

    # Close current storage
    await state.storage.close()

    # Create new storage
    storage = create_backend(state.config.backend_type, db_path=db_path)
    await storage.initialize()

    # Reinitialise engines with new storage
    embeddings = EmbeddingEngine(
        model_name=state.config.embedding_model,
        use_onnx=state.config.use_onnx,
        device=state.config.embedding_device,
        cache_size=state.config.cache_size,
    )
    await embeddings.initialize(storage)

    graph = GraphEngine(storage)
    traversal = GraphTraversal(storage)
    merger = EntityMerger(storage)
    search = HybridSearch(storage, embeddings)

    # Update global state
    _state.storage = storage
    _state.graph = graph
    _state.traversal = traversal
    _state.merger = merger
    _state.embeddings = embeddings
    _state.search = search
    _state._active_graph = graph_name

    # Get stats for response
    entity_count = await storage.count_entities()
    relationship_count = await storage.count_relationships()
    observation_count = await storage.count_observations()

    return {
        "name": graph_name,
        "db_path": str(db_path),
        "entities": entity_count,
        "relationships": relationship_count,
        "observations": observation_count,
    }


@mcp.tool()
async def list_graphs() -> dict[str, Any]:
    """List all available knowledge graphs with summary statistics.

    Scans the ``.graphmem/`` directory for ``.db`` files. Each file
    represents a separate knowledge graph. Returns the name, entity count,
    relationship count, observation count, file size, and last modified time
    for each graph.

    The currently active graph is marked with ``active: true``.
    """
    try:
        graphmem_dir = _get_graphmem_dir()
        graphs: list[dict[str, Any]] = []

        for db_file in sorted(graphmem_dir.glob("*.db")):
            stem = db_file.stem
            name = "default" if stem == "graph" else stem
            stat = db_file.stat()

            # Open each DB briefly to get counts
            try:
                import sqlite3

                conn = sqlite3.connect(str(db_file))
                ent_count = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
                rel_count = conn.execute("SELECT count(*) FROM relationships").fetchone()[0]
                obs_count = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
                conn.close()
            except Exception:
                ent_count = rel_count = obs_count = -1

            graphs.append(
                {
                    "name": name,
                    "file": db_file.name,
                    "entities": ent_count,
                    "relationships": rel_count,
                    "observations": obs_count,
                    "size_bytes": stat.st_size,
                    "last_modified": stat.st_mtime,
                    "active": name == _state._active_graph,
                }
            )

        return {
            "graphs": graphs,
            "count": len(graphs),
            "active_graph": _state._active_graph,
            "graphmem_dir": str(graphmem_dir),
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="list_graphs")
    except Exception as exc:
        log.exception("Failed to list graphs")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


@mcp.tool()
async def create_graph(
    name: str,
) -> dict[str, Any]:
    """Create a new empty knowledge graph.

    Creates a new ``.db`` file in the ``.graphmem/`` directory.
    The graph can then be activated with ``switch_graph``.

    Args:
        name: Name for the new graph (alphanumeric, hyphens, underscores).
              Cannot be 'graph' (reserved for default).
    """
    try:
        import re

        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return {
                "error": True,
                "error_type": "ValidationError",
                "message": (
                    f"Graph name must be alphanumeric with hyphens/underscores, got: {name!r}"
                ),
            }

        graphmem_dir = _get_graphmem_dir()
        db_name = "graph.db" if name == "default" else f"{name}.db"
        db_path = graphmem_dir / db_name

        if db_path.exists():
            return {
                "error": True,
                "error_type": "AlreadyExists",
                "message": f"Graph '{name}' already exists at {db_path}",
            }

        # Create and initialise the DB (creates tables)
        state = _require_state()
        storage = create_backend(state.config.backend_type, db_path=db_path)
        await storage.initialize()
        await storage.close()

        return {
            "name": name,
            "file": db_name,
            "db_path": str(db_path),
            "status": "created",
            "message": f"Graph '{name}' created. Use switch_graph to activate it.",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="create_graph")
    except Exception as exc:
        log.exception("Failed to create graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


@mcp.tool()
async def switch_graph(
    name: str,
) -> dict[str, Any]:
    """Switch the active knowledge graph.

    Closes the current graph's database connection and opens the specified
    graph instead. All subsequent tool calls will operate on the new graph.

    Args:
        name: Name of the graph to switch to. Use 'default' for the
              default graph (``graph.db``).
    """
    try:
        if name == _state._active_graph:
            return {
                "name": name,
                "status": "already_active",
                "message": f"Graph '{name}' is already the active graph.",
            }

        graphmem_dir = _get_graphmem_dir()
        db_name = "graph.db" if name == "default" else f"{name}.db"
        db_path = graphmem_dir / db_name

        if not db_path.exists():
            return {
                "error": True,
                "error_type": "NotFound",
                "message": (f"Graph '{name}' not found. Use list_graphs to see available graphs."),
            }

        stats = await _switch_engines(db_path, name)

        return {
            **stats,
            "status": "switched",
            "message": f"Switched to graph '{name}' ({stats['entities']} entities, "
            f"{stats['relationships']} relationships, {stats['observations']} observations).",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="switch_graph")
    except Exception as exc:
        log.exception("Failed to switch graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


@mcp.tool()
async def delete_graph(
    name: str,
) -> dict[str, Any]:
    """Delete a knowledge graph permanently.

    Removes the ``.db`` file and associated WAL/SHM files from the
    ``.graphmem/`` directory. Cannot delete the currently active graph
    — switch to a different graph first.

    Args:
        name: Name of the graph to delete. Cannot be the active graph.
    """
    try:
        if name == _state._active_graph:
            return {
                "error": True,
                "error_type": "ValidationError",
                "message": (
                    f"Cannot delete the active graph '{name}'. Switch to a different graph first."
                ),
            }

        graphmem_dir = _get_graphmem_dir()
        db_name = "graph.db" if name == "default" else f"{name}.db"
        db_path = graphmem_dir / db_name

        if not db_path.exists():
            return {
                "error": True,
                "error_type": "NotFound",
                "message": f"Graph '{name}' not found.",
            }

        # Remove DB file and WAL/SHM files
        deleted_files = []
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
                deleted_files.append(f.name)

        return {
            "name": name,
            "status": "deleted",
            "deleted_files": deleted_files,
            "message": f"Graph '{name}' deleted permanently.",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="delete_graph")
    except Exception as exc:
        log.exception("Failed to delete graph")
        return {"error": True, "error_type": type(exc).__name__, "message": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard / UI
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def open_dashboard(
    host: str = "127.0.0.1",
    port: int = 0,
) -> dict[str, Any]:
    """Launch the interactive graph-visualisation dashboard and return its URL.

    Starts a lightweight web server that serves a React-based graph explorer
    backed by the same knowledge graph the MCP server manages.  If the
    dashboard is already running, the existing URL is returned immediately.

    The dashboard is read-only and runs on ``localhost`` by default.
    Open the returned URL in any browser to explore the graph visually.

    Requires the ``[ui]`` optional dependency (``pip install graph-mem[ui]``).

    Args:
        host: Bind address (default ``127.0.0.1`` — local only).
        port: Port number.  ``0`` (default) auto-selects a free port.
    """
    try:
        # Already running? Return existing URL.
        if _state._ui_url is not None:
            return {
                "url": _state._ui_url,
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

        # Auto-select a free port if port == 0
        resolved_port = port
        if resolved_port == 0:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((host, 0))
            resolved_port = sock.getsockname()[1]
            sock.close()

        site = aio_web.TCPSite(runner, host, resolved_port)
        await site.start()

        url = f"http://{host}:{resolved_port}"
        _state._ui_url = url
        _state._ui_runner = runner

        log.info("Dashboard started at %s", url)

        return {
            "url": url,
            "status": "started",
            "message": f"Dashboard is now running at {url}",
        }

    except GraphMemError as exc:
        return _error_response(exc, tool_name="open_dashboard")
    except Exception as exc:
        log.exception("Failed to start dashboard")
        return {
            "error": True,
            "error_type": type(exc).__name__,
            "message": f"Failed to start dashboard: {exc}",
        }


# ═══════════════════════════════════════════════════════════════════════════
# Factory & entry point
# ═══════════════════════════════════════════════════════════════════════════


def create_server(config: Config | None = None) -> FastMCP:
    """Create and return the FastMCP server instance.

    Optionally accepts a pre-built :class:`Config` for testing or
    programmatic use.  When *config* is ``None`` the lifespan will
    call :func:`load_config` itself.
    """
    if config is not None:
        _state.config = config
    return mcp


def run(
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    """Start the MCP server.

    Args:
        transport: ``"stdio"`` (default) for CLI usage,
                   ``"sse"`` or ``"streamable-http"`` for network usage.
        host: Bind address for network transports (default ``127.0.0.1``).
        port: Port for network transports.
    """
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        log.warning(
            "Running with %s transport on %s:%d — no authentication is configured. "
            "Consider binding to 127.0.0.1 for local-only access.",
            transport,
            host,
            port,
        )
        # host/port are not supported by FastMCP.run() in mcp 1.x; pass
        # only transport for now until the SDK adds network binding options.
        mcp.run(transport=transport)
