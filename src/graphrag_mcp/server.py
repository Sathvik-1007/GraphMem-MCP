"""FastMCP server — registers all 13 Graph-RAG MCP tools.

This is the core entry point.  A lifespan context manager initialises
shared state (storage backend, engines, search) once at startup and
tears it down on shutdown.  Each tool is a thin async wrapper that
delegates to the appropriate engine, catches ``GraphRAGError`` subtypes,
and returns JSON-serialisable dicts.

The server is **storage-agnostic**: it uses :func:`storage.create_backend`
to instantiate the configured backend (SQLite, Neo4j, Memgraph, etc.).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from graphrag_mcp.graph.engine import ObservationResult

from mcp.server.fastmcp import FastMCP

from graphrag_mcp.graph import EntityMerger, GraphEngine, GraphTraversal
from graphrag_mcp.models import Entity, Observation, Relationship
from graphrag_mcp.semantic import EmbeddingEngine, HybridSearch
from graphrag_mcp.storage import StorageBackend, create_backend
from graphrag_mcp.utils import Config, GraphRAGError, get_logger, load_config, setup_logging
from graphrag_mcp.utils.errors import EntityNotFoundError

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
        raise GraphRAGError("Server not initialised.  Is the lifespan running?")
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
    log.info("Starting graphrag-mcp server (backend=%s)", config.backend_type)

    # Create and initialize storage backend
    db_path = config.ensure_db_dir()
    storage = create_backend(config.backend_type, db_path=db_path)
    await storage.initialize()

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
        "graphrag-mcp server ready (backend=%s, embeddings=%s)",
        storage.backend_type,
        embeddings.available,
    )
    yield

    # Shutdown
    log.info("Shutting down graphrag-mcp server")
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

mcp = FastMCP("graphrag-mcp", lifespan=_lifespan)


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
    if isinstance(exc, GraphRAGError) and exc.details:
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
        except GraphRAGError as exc:
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
# WRITE TOOLS (6)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def add_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Add entities to the knowledge graph. Entities with the same name and type are
    automatically merged.

    Each entity needs: name (str), entity_type (str, e.g. 'person', 'concept', 'place').
    Optional: description (str), properties (dict), observations (list[str]).

    Raises:
        GraphRAGError: If entity creation or embedding fails.
        KeyError/ValueError/TypeError: Returned as error dict if input is malformed.
    """
    # NOTE: Each tool wraps its body in a try/except that catches GraphRAGError
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

    except GraphRAGError as exc:
        return _error_response(exc, tool_name="add_entities")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(GraphRAGError(f"Invalid input: {exc}"), tool_name="add_entities")


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

    except GraphRAGError as exc:
        return _error_response(exc, tool_name="add_relationships")
    except (KeyError, ValueError, TypeError) as exc:
        return _error_response(
            GraphRAGError(f"Invalid input: {exc}"), tool_name="add_relationships"
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

    except GraphRAGError as exc:
        return _error_response(exc, tool_name="add_observations")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphRAGError(f"Invalid input: {exc}"), tool_name="add_observations")


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

    except GraphRAGError as exc:
        return _error_response(exc, tool_name="update_entity")
    except (ValueError, TypeError) as exc:
        return _error_response(GraphRAGError(f"Invalid input: {exc}"), tool_name="update_entity")


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
                except GraphRAGError as emb_exc:
                    log.debug(
                        "Failed to clean up embedding for entity %s: %s — "
                        "orphaned embedding row may remain",
                        eid,
                        emb_exc,
                    )

        return {"results": names, "deleted": deleted, "count": deleted}

    except GraphRAGError as exc:
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
            except GraphRAGError as emb_exc:
                log.debug(
                    "Failed to clean up embedding for merged entity %s: %s — "
                    "orphaned embedding row may remain",
                    source_entity.id,
                    emb_exc,
                )

        return {"result": result, "status": "merged"}

    except GraphRAGError as exc:
        return _error_response(exc, tool_name="merge_entities")


# ═══════════════════════════════════════════════════════════════════════════
# READ TOOLS (7)
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
        GraphRAGError: If the search engine or database encounters an error.
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

    except GraphRAGError as exc:
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

    except GraphRAGError as exc:
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

    except GraphRAGError as exc:
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

    except GraphRAGError as exc:
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

    except GraphRAGError as exc:
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

    except GraphRAGError as exc:
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

    except GraphRAGError as exc:
        return _error_response(exc, tool_name="find_paths")


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
