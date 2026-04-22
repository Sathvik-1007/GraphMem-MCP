"""SQLite storage backend — the default and reference implementation.

Wraps the existing ``Database`` class and implements every method of
:class:`StorageBackend`. All SQL is kept in this module so that a
different backend (Neo4j, Memgraph) only needs to implement the same
abstract interface in its own module without touching any SQL.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

from graph_mem.db.connection import Database
from graph_mem.db.schema import run_migrations
from graph_mem.storage.base import StorageBackend
from graph_mem.utils.errors import DatabaseError
from graph_mem.utils.logging import get_logger

log = get_logger("storage.sqlite")


class SQLiteBackend(StorageBackend):
    """SQLite + sqlite-vec + FTS5 implementation of :class:`StorageBackend`.

    Delegates connection management to :class:`Database` and adds the
    higher-level CRUD queries that ``GraphEngine``, ``HybridSearch``,
    and ``EntityMerger`` need.
    """

    _VECTOR_TABLES: frozenset[str] = frozenset({"entity_embeddings", "observation_embeddings"})

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: Database | None = None
        self._vec_available = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        db = Database(self._db_path)
        await db.initialize()
        await run_migrations(db)
        self._db = db
        self._vec_available = db.vec_loaded
        log.info("SQLite backend initialized at %s (vec=%s)", self._db_path, self._vec_available)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.debug("SQLite backend closed")

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        async with self._require_db().transaction():
            yield

    # ── Entity operations ────────────────────────────────────────────────

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
    ) -> Literal["created", "merged"]:
        db = self._require_db()
        existing = await db.fetch_one(
            "SELECT * FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type),
        )
        if existing is not None:
            old_desc = str(existing["description"] or "")
            new_desc = description.strip()
            if new_desc and new_desc not in old_desc:
                merged_desc = f"{old_desc}\n{new_desc}".strip()
            else:
                merged_desc = old_desc

            old_props_raw = existing.get("properties")
            old_props: dict[str, object] = (
                json.loads(old_props_raw) if isinstance(old_props_raw, str) else {}
            )
            merged_props = {**old_props, **properties}

            await db.execute(
                "UPDATE entities SET description = ?, properties = ?, updated_at = ? WHERE id = ?",
                (
                    merged_desc,
                    json.dumps(merged_props, ensure_ascii=False, default=str),
                    updated_at,
                    str(existing["id"]),
                ),
            )
            return "merged"

        await db.execute(
            "INSERT INTO entities "
            "(id, name, entity_type, description, properties, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entity_id,
                name,
                entity_type,
                description,
                json.dumps(properties, ensure_ascii=False, default=str),
                created_at,
                updated_at,
            ),
        )
        return "created"

    async def get_entity_by_id(self, entity_id: str) -> dict[str, Any] | None:
        return await self._require_db().fetch_one(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        )

    async def get_entity_by_name(
        self, name: str, entity_type: str | None = None
    ) -> dict[str, Any] | None:
        db = self._require_db()
        if entity_type is not None:
            return await db.fetch_one(
                "SELECT * FROM entities WHERE name = ? AND entity_type = ?",
                (name, entity_type),
            )
        return await db.fetch_one("SELECT * FROM entities WHERE name = ?", (name,))

    async def get_entity_by_name_nocase(self, name: str) -> dict[str, Any] | None:
        return await self._require_db().fetch_one(
            "SELECT * FROM entities WHERE name = ? COLLATE NOCASE", (name,)
        )

    async def list_entities(
        self, entity_type: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        db = self._require_db()
        limit = max(1, limit)
        offset = max(0, offset)
        if entity_type is not None:
            return await db.fetch_all(
                "SELECT * FROM entities WHERE entity_type = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (entity_type.strip().lower(), limit, offset),
            )
        return await db.fetch_all(
            "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    # Columns that may be updated on the entities table via update_entity_fields.
    _ENTITY_UPDATABLE_COLUMNS: frozenset[str] = frozenset(
        {"name", "entity_type", "description", "properties", "updated_at"}
    )

    async def update_entity_fields(self, entity_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        bad_cols = set(updates) - self._ENTITY_UPDATABLE_COLUMNS
        if bad_cols:
            raise DatabaseError(f"Invalid column(s) for entity update: {bad_cols!r}")
        set_clauses = [f"{col} = ?" for col in updates]
        params = [*list(updates.values()), entity_id]
        sql = f"UPDATE entities SET {', '.join(set_clauses)} WHERE id = ?"
        await self._require_db().execute(sql, tuple(params))

    async def delete_entity(self, entity_id: str) -> None:
        db = self._require_db()
        # Clean up embeddings first (vec tables may not exist — only ignore OperationalError)
        if self._vec_available:
            with contextlib.suppress(sqlite3.OperationalError, DatabaseError):
                obs_rows = await db.fetch_all(
                    "SELECT id FROM observations WHERE entity_id = ?", (entity_id,)
                )
                for obs_row in obs_rows:
                    with contextlib.suppress(sqlite3.OperationalError, DatabaseError):
                        await db.execute(
                            "DELETE FROM observation_embeddings WHERE id = ?",
                            (str(obs_row["id"]),),
                        )
            with contextlib.suppress(sqlite3.OperationalError, DatabaseError):
                await db.execute("DELETE FROM entity_embeddings WHERE id = ?", (entity_id,))
        # Now delete observations, relationships, and entity
        await db.execute("DELETE FROM observations WHERE entity_id = ?", (entity_id,))
        await db.execute(
            "DELETE FROM relationships WHERE source_id = ? OR target_id = ?",
            (entity_id, entity_id),
        )
        await db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))

    async def count_entities(self) -> int:
        row = await self._require_db().fetch_one("SELECT COUNT(*) AS cnt FROM entities")
        return int(row["cnt"]) if row else 0

    async def entity_type_distribution(self) -> dict[str, int]:
        rows = await self._require_db().fetch_all(
            "SELECT entity_type, COUNT(*) AS cnt FROM entities "
            "GROUP BY entity_type ORDER BY cnt DESC"
        )
        return {str(r["entity_type"]): int(r["cnt"]) for r in rows}

    async def most_connected_entities(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self._require_db().fetch_all(
            """
            SELECT e.id, e.name, e.entity_type, COUNT(r.id) AS degree
            FROM entities e
            LEFT JOIN relationships r
              ON r.source_id = e.id OR r.target_id = e.id
            GROUP BY e.id
            ORDER BY degree DESC
            LIMIT ?
            """,
            (limit,),
        )

    async def recent_entities(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self._require_db().fetch_all(
            "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )

    # ── Relationship operations ──────────────────────────────────────────

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
    ) -> Literal["created", "updated"]:
        db = self._require_db()
        existing = await db.fetch_one(
            "SELECT * FROM relationships "
            "WHERE source_id = ? AND target_id = ? AND relationship_type = ?",
            (source_id, target_id, relationship_type),
        )
        if existing is not None:
            new_weight = max(float(existing["weight"]), weight)
            old_props_raw = existing.get("properties")
            old_props: dict[str, object] = (
                json.loads(old_props_raw) if isinstance(old_props_raw, str) else {}
            )
            merged_props = {**old_props, **properties}
            await db.execute(
                "UPDATE relationships SET weight = ?, properties = ?, updated_at = ? WHERE id = ?",
                (
                    new_weight,
                    json.dumps(merged_props, ensure_ascii=False, default=str),
                    updated_at,
                    str(existing["id"]),
                ),
            )
            return "updated"

        await db.execute(
            "INSERT INTO relationships "
            "(id, source_id, target_id, relationship_type, "
            "weight, properties, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rel_id,
                source_id,
                target_id,
                relationship_type,
                weight,
                json.dumps(properties, ensure_ascii=False, default=str),
                created_at,
                updated_at,
            ),
        )
        return "created"

    async def get_relationship(
        self, source_id: str, target_id: str, relationship_type: str
    ) -> dict[str, Any] | None:
        return await self._require_db().fetch_one(
            "SELECT * FROM relationships "
            "WHERE source_id = ? AND target_id = ? AND relationship_type = ?",
            (source_id, target_id, relationship_type),
        )

    async def get_relationships_for_entity(
        self,
        entity_id: str,
        direction: str = "both",
        relationship_type: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[object] = []

        if direction == "outgoing":
            conditions.append("r.source_id = ?")
            params.append(entity_id)
        elif direction == "incoming":
            conditions.append("r.target_id = ?")
            params.append(entity_id)
        else:
            conditions.append("(r.source_id = ? OR r.target_id = ?)")
            params.extend([entity_id, entity_id])

        if relationship_type is not None:
            conditions.append("r.relationship_type = ?")
            params.append(relationship_type.strip().lower())

        where = " AND ".join(conditions)
        sql = (
            "SELECT r.*, "
            "  s.name AS source_name, s.entity_type AS source_type, "
            "  t.name AS target_name, t.entity_type AS target_type "
            "FROM relationships r "
            "JOIN entities s ON s.id = r.source_id "
            "JOIN entities t ON t.id = r.target_id "
            f"WHERE {where} "
            "ORDER BY r.weight DESC, r.updated_at DESC"
        )
        return await self._require_db().fetch_all(sql, tuple(params))

    async def get_relationships_for_entities(
        self,
        entity_ids: list[str],
        direction: str = "both",
    ) -> dict[str, list[dict[str, Any]]]:
        if not entity_ids:
            return {}

        result: dict[str, list[dict[str, Any]]] = {eid: [] for eid in entity_ids}
        placeholders = ",".join("?" for _ in entity_ids)

        if direction == "outgoing":
            where_clause = f"r.source_id IN ({placeholders})"
            params = tuple(entity_ids)
        elif direction == "incoming":
            where_clause = f"r.target_id IN ({placeholders})"
            params = tuple(entity_ids)
        else:  # both
            where_clause = f"r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders})"
            params = tuple(entity_ids) + tuple(entity_ids)

        rows = await self._require_db().fetch_all(
            f"""
            SELECT r.*,
                   s.name AS source_name, s.entity_type AS source_type,
                   t.name AS target_name, t.entity_type AS target_type
            FROM relationships r
            JOIN entities s ON s.id = r.source_id
            JOIN entities t ON t.id = r.target_id
            WHERE {where_clause}
            ORDER BY r.weight DESC, r.updated_at DESC
            """,
            params,
        )

        for row in rows:
            row_dict = dict(row)
            source_id = str(row_dict.get("source_id", ""))
            target_id = str(row_dict.get("target_id", ""))
            if source_id in result:
                result[source_id].append(row_dict)
            if target_id in result and target_id != source_id:
                result[target_id].append(row_dict)

        return result

    async def delete_relationships(
        self, source_id: str, target_id: str, relationship_type: str | None = None
    ) -> int:
        params: list[object] = [source_id, target_id]
        sql = "DELETE FROM relationships WHERE source_id = ? AND target_id = ?"
        if relationship_type is not None:
            sql += " AND relationship_type = ?"
            params.append(relationship_type.strip().lower())
        cursor = await self._require_db().execute(sql, tuple(params))
        return cursor.rowcount

    async def get_relationships_by_column(
        self, column: str, entity_id: str
    ) -> list[dict[str, Any]]:
        if column not in ("source_id", "target_id"):
            raise DatabaseError(f"Invalid column for relationship lookup: {column!r}")
        return await self._require_db().fetch_all(
            f"SELECT * FROM relationships WHERE {column} = ?",
            (entity_id,),
        )

    # Columns that may be updated on the relationships table.
    _RELATIONSHIP_UPDATABLE_COLUMNS: frozenset[str] = frozenset(
        {"source_id", "target_id", "relationship_type", "weight", "properties", "updated_at"}
    )

    async def update_relationship(self, rel_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        bad_cols = set(updates) - self._RELATIONSHIP_UPDATABLE_COLUMNS
        if bad_cols:
            raise DatabaseError(f"Invalid column(s) for relationship update: {bad_cols!r}")
        set_clauses = [f"{col} = ?" for col in updates]
        params = [*list(updates.values()), rel_id]
        sql = f"UPDATE relationships SET {', '.join(set_clauses)} WHERE id = ?"
        await self._require_db().execute(sql, tuple(params))

    async def delete_relationship_by_id(self, rel_id: str) -> None:
        await self._require_db().execute("DELETE FROM relationships WHERE id = ?", (rel_id,))

    async def count_relationships(self) -> int:
        row = await self._require_db().fetch_one("SELECT COUNT(*) AS cnt FROM relationships")
        return int(row["cnt"]) if row else 0

    async def relationship_type_distribution(self) -> dict[str, int]:
        rows = await self._require_db().fetch_all(
            "SELECT relationship_type, COUNT(*) AS cnt "
            "FROM relationships GROUP BY relationship_type ORDER BY cnt DESC"
        )
        return {str(r["relationship_type"]): int(r["cnt"]) for r in rows}

    # ── Observation operations ───────────────────────────────────────────

    async def insert_observation(
        self,
        *,
        obs_id: str,
        entity_id: str,
        content: str,
        source: str,
        created_at: float,
    ) -> None:
        await self._require_db().execute(
            "INSERT INTO observations (id, entity_id, content, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (obs_id, entity_id, content, source, created_at),
        )

    async def get_observations_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        return await self._require_db().fetch_all(
            "SELECT * FROM observations WHERE entity_id = ? ORDER BY created_at DESC",
            (entity_id,),
        )

    async def move_observations(self, from_entity_id: str, to_entity_id: str) -> int:
        cursor = await self._require_db().execute(
            "UPDATE observations SET entity_id = ? WHERE entity_id = ?",
            (to_entity_id, from_entity_id),
        )
        return cursor.rowcount

    async def count_observations(self) -> int:
        row = await self._require_db().fetch_one("SELECT COUNT(*) AS cnt FROM observations")
        return int(row["cnt"]) if row else 0

    async def delete_observation(self, obs_id: str) -> bool:
        db = self._require_db()
        cursor = await db.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        return cursor.rowcount > 0

    async def update_observation(self, obs_id: str, content: str) -> bool:
        db = self._require_db()
        cursor = await db.execute(
            "UPDATE observations SET content = ? WHERE id = ?",
            (content, obs_id),
        )
        return cursor.rowcount > 0

    # ── Embedding operations ─────────────────────────────────────────────

    async def upsert_entity_embedding(self, entity_id: str, embedding: bytes) -> None:
        db = self._require_db()
        # vec0 virtual tables do not support INSERT OR REPLACE — delete first.
        await db.execute("DELETE FROM entity_embeddings WHERE id = ?", (entity_id,))
        await db.execute(
            "INSERT INTO entity_embeddings (id, embedding) VALUES (?, ?)",
            (entity_id, embedding),
        )

    async def upsert_observation_embedding(self, obs_id: str, embedding: bytes) -> None:
        db = self._require_db()
        # vec0 virtual tables do not support INSERT OR REPLACE — delete first.
        await db.execute("DELETE FROM observation_embeddings WHERE id = ?", (obs_id,))
        await db.execute(
            "INSERT INTO observation_embeddings (id, embedding) VALUES (?, ?)",
            (obs_id, embedding),
        )

    async def delete_entity_embedding(self, entity_id: str) -> None:
        await self._require_db().execute("DELETE FROM entity_embeddings WHERE id = ?", (entity_id,))

    async def delete_observation_embedding(self, obs_id: str) -> None:
        await self._require_db().execute(
            "DELETE FROM observation_embeddings WHERE id = ?", (obs_id,)
        )

    async def get_cached_embedding(self, content_hash: str, model_name: str) -> bytes | None:
        row = await self._require_db().fetch_one(
            "SELECT embedding FROM embedding_cache WHERE content_hash = ? AND model_name = ?",
            (content_hash, model_name),
        )
        return row["embedding"] if row else None

    async def set_cached_embedding(
        self,
        content_hash: str,
        embedding: bytes,
        model_name: str,
        created_at: float,
    ) -> None:
        await self._require_db().execute(
            "INSERT OR REPLACE INTO embedding_cache "
            "(content_hash, embedding, model_name, created_at) VALUES (?, ?, ?, ?)",
            (content_hash, embedding, model_name, created_at),
        )

    async def prune_embedding_cache(self, max_size: int) -> None:
        db = self._require_db()
        count_row = await db.fetch_one("SELECT COUNT(*) AS c FROM embedding_cache")
        if count_row and int(count_row["c"]) > max_size:
            excess = int(count_row["c"]) - max_size
            await db.execute(
                "DELETE FROM embedding_cache WHERE content_hash IN "
                "(SELECT content_hash FROM embedding_cache ORDER BY created_at ASC LIMIT ?)",
                (excess,),
            )

    async def ensure_vec_tables(self, dimension: int) -> bool:
        db = self._require_db()
        try:
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS entity_embeddings USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{dimension}]
                )
            """)
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS observation_embeddings USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{dimension}]
                )
            """)
            self._vec_available = True
            return True
        except (sqlite3.Error, OSError, DatabaseError) as exc:
            log.warning("Could not create vector tables: %s", exc)
            self._vec_available = False
            return False

    async def vector_search(
        self, table: str, query_embedding: bytes, limit: int
    ) -> list[tuple[str, float]]:
        if table not in self._VECTOR_TABLES:
            raise ValueError(f"Invalid vector table: {table!r}")
        if not self._vec_available:
            return []
        rows = await self._require_db().fetch_all(
            f"""
            SELECT id, distance
            FROM {table}
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
            """,
            (query_embedding, limit),
        )
        return [(str(r["id"]), float(r["distance"])) for r in rows]

    # ── Full-text search ─────────────────────────────────────────────────

    def _sanitize_fts5_query(self, query: str) -> str:
        """Sanitize a user query for safe use in FTS5 MATCH expressions.

        Strategy: tokenize the query into individual words, quote each
        token to neutralise FTS5 operators (AND, OR, NOT, NEAR), and
        join with OR so that *any* matching term contributes to ranking.

        Args:
            query: Raw user query string.

        Returns:
            A safe FTS5 MATCH expression.
        """
        stripped = query.strip()
        if not stripped:
            return '""'

        # Extract alphanumeric tokens (handles any language's word chars).
        tokens = re.findall(r"[\w']+", stripped, flags=re.UNICODE)
        if not tokens:
            return '""'

        # Quote each token individually to neutralise special characters
        # and FTS5 reserved words (AND, OR, NOT, NEAR).
        quoted = [f'"{t}"' for t in tokens]

        if len(quoted) == 1:
            return quoted[0]

        # Join with OR for broad matching, then boost exact phrase.
        or_clause = " OR ".join(quoted)
        return or_clause

    async def fts_search_entities(self, query: str, limit: int) -> list[tuple[str, float]]:
        try:
            rows = await self._require_db().fetch_all(
                """
                SELECT e.id, rank
                FROM entities_fts fts
                JOIN entities e ON e.rowid = fts.rowid
                WHERE entities_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (self._sanitize_fts5_query(query), limit),
            )
            return [(str(r["id"]), float(r["rank"])) for r in rows]
        except (sqlite3.Error, ValueError) as exc:
            log.warning("FTS5 entity search failed: %s", exc)
            return []

    async def fts_search_observations(self, query: str, limit: int) -> list[tuple[str, float]]:
        try:
            rows = await self._require_db().fetch_all(
                """
                SELECT o.id, rank
                FROM observations_fts fts
                JOIN observations o ON o.rowid = fts.rowid
                WHERE observations_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (self._sanitize_fts5_query(query), limit),
            )
            return [(str(r["id"]), float(r["rank"])) for r in rows]
        except (sqlite3.Error, ValueError) as exc:
            log.warning("FTS5 observation search failed: %s", exc)
            return []

    async def fts_suggest_similar(self, name: str, limit: int = 5) -> list[str]:
        suggestions: list[str] = []
        db = self._require_db()

        # Try FTS5 first
        try:
            rows = await db.fetch_all(
                "SELECT name FROM entities_fts WHERE entities_fts MATCH ? LIMIT ?",
                (self._sanitize_fts5_query(name), limit),
            )
            suggestions = [str(r["name"]) for r in rows]
        except (sqlite3.Error, ValueError, OSError) as exc:
            log.debug("FTS5 suggestion query failed for %r: %s", name, exc)

        # Fallback to LIKE (escape wildcards to prevent semantic injection)
        if not suggestions:
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = await db.fetch_all(
                "SELECT name FROM entities WHERE name LIKE ? ESCAPE '\\' LIMIT ?",
                (f"%{escaped}%", limit),
            )
            suggestions = [str(r["name"]) for r in rows]

        return suggestions

    # ── Metadata ─────────────────────────────────────────────────────────

    async def get_metadata(self, key: str) -> str | None:
        row = await self._require_db().fetch_one("SELECT value FROM metadata WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        await self._require_db().execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )

    # ── Traversal support ────────────────────────────────────────────────

    async def fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
        return await self._require_db().fetch_all(sql, params)

    async def fetch_one(self, sql: str, params: tuple[object, ...] = ()) -> dict[str, Any] | None:
        return await self._require_db().fetch_one(sql, params)

    async def fetch_entity_rows(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        return await self._require_db().fetch_all(
            f"SELECT * FROM entities WHERE id IN ({placeholders})",
            tuple(entity_ids),
        )

    async def fetch_relationships_between(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        return await self._require_db().fetch_all(
            "SELECT r.*, "
            "  s.name AS source_name, s.entity_type AS source_type, "
            "  t.name AS target_name, t.entity_type AS target_type "
            "FROM relationships r "
            "JOIN entities s ON s.id = r.source_id "
            "JOIN entities t ON t.id = r.target_id "
            f"WHERE r.source_id IN ({placeholders}) AND r.target_id IN ({placeholders})",
            tuple(entity_ids) + tuple(entity_ids),
        )

    async def resolve_entity_names(self, entity_ids: set[str]) -> dict[str, str]:
        if not entity_ids:
            return {}
        placeholders = ",".join("?" for _ in entity_ids)
        rows = await self._require_db().fetch_all(
            f"SELECT id, name FROM entities WHERE id IN ({placeholders})",
            tuple(entity_ids),
        )
        return {str(r["id"]): str(r["name"]) for r in rows}

    # ── Schema introspection ────────────────────────────────────────────

    async def get_schema_version(self) -> int:
        from graph_mem.db.schema import get_current_version

        return await get_current_version(self._require_db())

    # ── Backend identity ─────────────────────────────────────────────────

    @property
    def backend_type(self) -> str:
        return "sqlite"

    @property
    def vec_available(self) -> bool:
        return self._vec_available

    # ── Internals ────────────────────────────────────────────────────────

    @property
    def db(self) -> Database:
        """Expose the underlying database for components that still need raw access.

        This is an escape hatch for the migration period. New code should
        use StorageBackend methods instead.
        """
        return self._require_db()

    def _require_db(self) -> Database:
        if self._db is None:
            raise DatabaseError("SQLite backend not initialized. Call initialize() first.")
        return self._db
