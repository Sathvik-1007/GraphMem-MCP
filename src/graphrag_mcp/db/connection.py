"""Async SQLite connection management with WAL mode and PRAGMA tuning.

Provides a connection pool that reuses a single writer connection (SQLite
allows only one writer) and configures optimal PRAGMAs for Graph-RAG
workloads: WAL journal, large page cache, memory-mapped I/O.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

import aiosqlite

from graphrag_mcp.utils.errors import DatabaseError
from graphrag_mcp.utils.logging import get_logger

log = get_logger("db.connection")

# ── PRAGMA configuration ─────────────────────────────────────────────────────
_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA cache_size = -64000",  # 64 MB page cache
    "PRAGMA mmap_size = 268435456",  # 256 MB memory-mapped I/O
    "PRAGMA foreign_keys = ON",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA busy_timeout = 5000",  # 5 s retry on lock
    "PRAGMA wal_autocheckpoint = 1000",  # checkpoint every 1000 pages
]


async def _apply_pragmas(db: aiosqlite.Connection) -> None:
    for pragma in _PRAGMAS:
        await db.execute(pragma)


class Database:
    """Async SQLite database handle with connection lifecycle management.

    Usage::

        db = Database(Path(".graphrag/graph.db"))
        await db.initialize()      # opens connection, applies PRAGMAs, runs migrations
        ...
        await db.close()

    Or as an async context manager::

        async with Database(path) as db:
            ...
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._conn: aiosqlite.Connection | None = None
        self._vec_loaded = False

    @property
    def vec_loaded(self) -> bool:
        """Whether the sqlite-vec extension was loaded successfully."""
        return self._vec_loaded

    @property
    def path(self) -> Path:
        return self._path

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise DatabaseError("Database not initialized. Call initialize() first.")
        return self._conn

    async def initialize(self) -> None:
        """Open the database, apply PRAGMAs, and load extensions."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Opening database at %s", self._path)
        try:
            self._conn = await aiosqlite.connect(
                str(self._path),
                isolation_level=None,  # autocommit; we manage transactions explicitly
            )
            self._conn.row_factory = aiosqlite.Row
            await _apply_pragmas(self._conn)
            await self._load_extensions()
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            raise DatabaseError(f"Failed to open database: {exc}") from exc

    async def _load_extensions(self) -> None:
        """Load the sqlite-vec extension for vector search."""
        try:
            import sqlite_vec  # type: ignore[import-untyped]

            if self._conn is None:  # pragma: no cover — always true when called from open()
                return
            await self._conn.enable_load_extension(True)
            conn_raw = self._conn._conn  # underlying sqlite3.Connection
            sqlite_vec.load(conn_raw)
            await self._conn.enable_load_extension(False)
            self._vec_loaded = True
            log.debug("sqlite-vec extension loaded")
        except ImportError:
            log.warning("sqlite-vec not installed — vector search disabled")
        except (sqlite3.Error, OSError) as exc:
            log.warning("Failed to load sqlite-vec: %s — vector search disabled", exc)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.debug("Database closed")

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> aiosqlite.Cursor:
        try:
            return await self.conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQL error: {exc}", details=sql) from exc

    async def execute_many(self, sql: str, params_seq: list[tuple[object, ...]]) -> None:
        try:
            await self.conn.executemany(sql, params_seq)
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQL error in executemany: {exc}", details=sql) from exc

    async def fetch_one(self, sql: str, params: tuple[object, ...] = ()) -> dict[str, Any] | None:
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        """Explicit transaction context manager.

        Usage::
            async with db.transaction():
                await db.execute("INSERT ...")
                await db.execute("INSERT ...")
        """
        await self.conn.execute("BEGIN")
        try:
            yield
            await self.conn.execute("COMMIT")
        except Exception:
            await self.conn.execute("ROLLBACK")
            raise

    async def __aenter__(self) -> Database:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
