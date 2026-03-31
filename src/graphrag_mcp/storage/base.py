"""Abstract storage backend protocol for graphrag-mcp.

Defines the contract that all storage backends must implement.
The default backend is SQLite (see ``sqlite_backend.py``); future
backends (Neo4j, Memgraph, PostgreSQL) implement the same ABC.

Design choices:
- Methods are grouped by domain: entities, relationships, observations,
  embeddings, metadata, and lifecycle.
- All methods are async — even if the underlying store is synchronous,
  this keeps the API consistent for network-backed stores.
- Return types use the project's domain models (Entity, Relationship,
  Observation) rather than raw dicts so callers get type safety.
- The ABC uses ``abc.abstractmethod`` rather than ``Protocol`` because
  backends must explicitly opt in (``class MyBackend(StorageBackend)``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any


class StorageBackend(ABC):
    """Contract for pluggable storage backends.

    Every backend manages:
    - **Lifecycle**: initialize / close / transaction context
    - **Entities**: CRUD + resolution + listing + stats
    - **Relationships**: CRUD + filtered queries
    - **Observations**: CRUD + per-entity listing
    - **Embeddings**: CRUD for entity/observation vectors + cache
    - **Metadata**: key-value pairs (embedding dimension, model name, etc.)
    - **Full-text search**: FTS indexing is backend-specific, but search
      results go through the same interface.

    Backends are instantiated by :func:`storage.create_backend` and
    wired into the server via ``AppState``.
    """

    # ── Lifecycle ────────────────────────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Open connections, run migrations, prepare indexes."""

    @abstractmethod
    async def close(self) -> None:
        """Release all resources (connections, file handles)."""

    @abstractmethod
    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        """Explicit write-transaction context manager.

        Usage::

            async with backend.transaction():
                await backend.upsert_entity(...)
                await backend.upsert_relationship(...)

        Implementations must COMMIT on clean exit and ROLLBACK on exception.
        """
        yield  # pragma: no cover — abstract, never executed

    # ── Entity operations ────────────────────────────────────────────────

    @abstractmethod
    async def upsert_entity(
        self,
        *,
        entity_id: str,
        name: str,
        entity_type: str,
        description: str,
        properties: dict[str, object],
        created_at: float,
        updated_at: float,
    ) -> str:
        """Insert or update an entity, returning ``"created"`` or ``"merged"``."""

    @abstractmethod
    async def get_entity_by_id(self, entity_id: str) -> dict[str, Any] | None:
        """Fetch a single entity row by ID. Returns None if not found."""

    @abstractmethod
    async def get_entity_by_name(
        self,
        name: str,
        entity_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Exact-match lookup by name (optionally scoped by type)."""

    @abstractmethod
    async def get_entity_by_name_nocase(self, name: str) -> dict[str, Any] | None:
        """Case-insensitive name lookup. Returns the first match or None."""

    @abstractmethod
    async def list_entities(
        self,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List entities with optional type filter and pagination."""

    @abstractmethod
    async def update_entity_fields(
        self,
        entity_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Apply arbitrary field updates to an entity by ID.

        *updates* is a mapping of column names to new values. The backend
        must handle merging ``properties`` JSON and updating ``updated_at``.
        """

    @abstractmethod
    async def delete_entity(self, entity_id: str) -> None:
        """Delete an entity and cascade to its observations and relationships."""

    @abstractmethod
    async def count_entities(self) -> int:
        """Return the total number of entities."""

    @abstractmethod
    async def entity_type_distribution(self) -> dict[str, int]:
        """Return ``{entity_type: count}`` across all entities."""

    @abstractmethod
    async def most_connected_entities(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the top *limit* entities ranked by degree (in + out)."""

    @abstractmethod
    async def recent_entities(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the *limit* most recently updated entities."""

    # ── Relationship operations ──────────────────────────────────────────

    @abstractmethod
    async def upsert_relationship(
        self,
        *,
        rel_id: str,
        source_id: str,
        target_id: str,
        relationship_type: str,
        weight: float,
        properties: dict[str, object],
        created_at: float,
        updated_at: float,
    ) -> str:
        """Insert or update a relationship, returning ``"created"`` or ``"updated"``."""

    @abstractmethod
    async def get_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
    ) -> dict[str, Any] | None:
        """Look up a specific relationship by its unique key."""

    @abstractmethod
    async def get_relationships_for_entity(
        self,
        entity_id: str,
        direction: str = "both",
        relationship_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get relationships connected to *entity_id* with optional filters.

        Returned dicts must include ``source_name``, ``source_type``,
        ``target_name``, ``target_type`` fields.
        """

    @abstractmethod
    async def delete_relationships(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str | None = None,
    ) -> int:
        """Delete matching relationships and return the count removed."""

    @abstractmethod
    async def get_relationships_by_column(
        self,
        column: str,
        entity_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch all relationships where *column* equals *entity_id*.

        Used during merge operations. *column* is either
        ``"source_id"`` or ``"target_id"``.
        """

    @abstractmethod
    async def update_relationship(
        self,
        rel_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Apply field updates to a relationship by ID."""

    @abstractmethod
    async def delete_relationship_by_id(self, rel_id: str) -> None:
        """Delete a single relationship by primary key."""

    @abstractmethod
    async def count_relationships(self) -> int:
        """Return the total number of relationships."""

    @abstractmethod
    async def relationship_type_distribution(self) -> dict[str, int]:
        """Return ``{relationship_type: count}`` across all relationships."""

    # ── Observation operations ───────────────────────────────────────────

    @abstractmethod
    async def insert_observation(
        self,
        *,
        obs_id: str,
        entity_id: str,
        content: str,
        source: str,
        created_at: float,
    ) -> None:
        """Insert a new observation."""

    @abstractmethod
    async def get_observations_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        """Return observations for *entity_id*, newest first."""

    @abstractmethod
    async def move_observations(self, from_entity_id: str, to_entity_id: str) -> int:
        """Reassign all observations from one entity to another. Returns count."""

    @abstractmethod
    async def count_observations(self) -> int:
        """Return the total number of observations."""

    # ── Embedding operations ─────────────────────────────────────────────

    @abstractmethod
    async def upsert_entity_embedding(self, entity_id: str, embedding: bytes) -> None:
        """Store or update the embedding vector for an entity."""

    @abstractmethod
    async def upsert_observation_embedding(self, obs_id: str, embedding: bytes) -> None:
        """Store or update the embedding vector for an observation."""

    @abstractmethod
    async def delete_entity_embedding(self, entity_id: str) -> None:
        """Remove the embedding for an entity."""

    @abstractmethod
    async def delete_observation_embedding(self, obs_id: str) -> None:
        """Remove the embedding for an observation."""

    @abstractmethod
    async def get_cached_embedding(self, content_hash: str, model_name: str) -> bytes | None:
        """Look up a cached embedding by content hash and model name."""

    @abstractmethod
    async def set_cached_embedding(
        self,
        content_hash: str,
        embedding: bytes,
        model_name: str,
        created_at: float,
    ) -> None:
        """Store an embedding in the cache."""

    @abstractmethod
    async def prune_embedding_cache(self, max_size: int) -> None:
        """Evict the oldest cached embeddings if the cache exceeds *max_size*."""

    @abstractmethod
    async def ensure_vec_tables(self, dimension: int) -> bool:
        """Create vector-search tables/indexes for the given dimension.

        Returns True if vector tables are operational, False if the
        backend doesn't support vector search (e.g. sqlite-vec not installed).
        """

    @abstractmethod
    async def vector_search(
        self, table: str, query_embedding: bytes, limit: int
    ) -> list[tuple[str, float]]:
        """Run a vector similarity search.

        Returns ``[(id, distance), ...]`` ordered by ascending distance.
        """

    # ── Full-text search ─────────────────────────────────────────────────

    @abstractmethod
    async def fts_search_entities(self, query: str, limit: int) -> list[tuple[str, float]]:
        """Full-text search over entities.

        Returns ``[(entity_id, rank), ...]`` ordered by relevance.
        """

    @abstractmethod
    async def fts_search_observations(self, query: str, limit: int) -> list[tuple[str, float]]:
        """Full-text search over observations.

        Returns ``[(observation_id, rank), ...]`` ordered by relevance.
        """

    @abstractmethod
    async def fts_suggest_similar(self, name: str, limit: int = 5) -> list[str]:
        """Suggest entity names similar to *name* using FTS or LIKE fallback."""

    # ── Metadata ─────────────────────────────────────────────────────────

    @abstractmethod
    async def get_metadata(self, key: str) -> str | None:
        """Read a metadata value by key. Returns None if not set."""

    @abstractmethod
    async def set_metadata(self, key: str, value: str) -> None:
        """Write a metadata key-value pair (upsert)."""

    # ── Traversal support ────────────────────────────────────────────────

    @abstractmethod
    async def fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
        """Execute a read query and return all rows as dicts.

        This escape hatch exists for the traversal layer which builds
        complex recursive CTEs. Graph-database backends will override
        this with their native query language (Cypher, GQL, etc.).
        """

    @abstractmethod
    async def fetch_one(self, sql: str, params: tuple[object, ...] = ()) -> dict[str, Any] | None:
        """Execute a read query and return the first row or ``None``.

        Escape hatch for one-off lookups (e.g. ``PRAGMA integrity_check``).
        Graph-database backends translate the query to their native language.
        """

    @abstractmethod
    async def fetch_entity_rows(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        """Batch-fetch entity rows by ID list."""

    @abstractmethod
    async def fetch_relationships_between(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch all relationships where both source and target are in *entity_ids*."""

    @abstractmethod
    async def resolve_entity_names(self, entity_ids: set[str]) -> dict[str, str]:
        """Batch-resolve entity IDs to names.

        Returns ``{entity_id: name}`` for all found entities.
        """

    # ── Schema introspection ────────────────────────────────────────────

    @abstractmethod
    async def get_schema_version(self) -> int:
        """Return the current schema migration version (0 if no migrations applied)."""

    # ── Backend identity ─────────────────────────────────────────────────

    @property
    @abstractmethod
    def backend_type(self) -> str:
        """Return a short identifier like ``"sqlite"``, ``"neo4j"``, ``"memgraph"``."""

    @property
    @abstractmethod
    def vec_available(self) -> bool:
        """Whether vector search is operational on this backend."""
