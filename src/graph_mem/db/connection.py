"""Async SQLite connection management with WAL mode and PRAGMA tuning.

Owns one connection and its lifecycle: PRAGMAs, extension loading, statement
execution, and transaction scoping.  It does not know what the rows mean — that
is :mod:`graph_mem.storage.sqlite_backend`'s job.

Concurrency model
-----------------
There is exactly one connection, and SQLite permits one write transaction on it
at a time.  The MCP runtime and the UI server both dispatch requests as
concurrent tasks, so that limit has to be enforced here rather than assumed.

:meth:`Database.transaction` therefore serialises writers with a lock held for
the whole outermost transaction, and tracks nesting per *task*:

- A task with no transaction open acquires the lock and issues
  ``BEGIN IMMEDIATE``.
- The same task re-entering opens a ``SAVEPOINT`` instead, so an inner failure
  rolls back only the inner work.
- A different task waits for the lock, rather than opening a savepoint inside
  someone else's transaction.

Tracking depth in a plain attribute — as this module previously did — makes the
third case indistinguishable from the second: two overlapping transactions both
read ``depth == 0``/``1`` off the same object, the second nests itself inside
the first, and a rollback of the first silently discards work the second had
already committed.

Known constraint: a task that opens a transaction and then awaits a *child*
task that also writes will deadlock, because the child is a different task and
waits for a lock its parent holds. That is a wrong program rather than a
tolerable pattern, and deadlocking is preferable to interleaving two
transactions on one connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import aiosqlite

from graph_mem.utils.errors import DatabaseError
from graph_mem.utils.logging import get_logger

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


def _sql_error_message(exc: sqlite3.Error, sql: str) -> str:
    """Build the message for a failed statement, logging the statement itself.

    The SQL stays in the log rather than in the exception because
    ``DatabaseError.details`` is copied into MCP tool responses, and echoing
    query text back to a language model discloses schema and query structure
    for no diagnostic benefit to the caller.
    """
    log.error("SQL error: %s | statement: %s", exc, sql)
    return f"SQL error: {exc}"


class Database:
    """Async SQLite database wrapper with connection lifecycle management.

    Handles PRAGMAs, extension loading, and provides a unified
    ``execute``/``fetch_*`` interface used by :class:`SQLiteBackend`.
    """

    def __init__(self, path: Path | str) -> None:
        # Accepts str so a caller that read a path out of config or a CLI flag
        # cannot produce an AttributeError several frames later.
        self._path = Path(path).resolve()
        self._conn: aiosqlite.Connection | None = None
        self._vec_loaded = False
        # Serialises outermost transactions; see the module docstring.
        self._write_lock = asyncio.Lock()
        # The task currently inside a transaction, and how deeply nested it is.
        # Both are only ever touched by the lock holder, or by a task comparing
        # itself against _txn_owner, so they need no further synchronisation.
        self._txn_owner: asyncio.Task[Any] | None = None
        self._txn_depth = 0

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
        """Load the sqlite-vec extension for vector search.

        Never raises: vector search is optional, and a build without extension
        support should degrade to full-text search rather than refuse to open
        the database.  Extension loading is re-disabled on every exit path,
        including failures, so a partially-loaded connection is not left able
        to ``load_extension()`` arbitrary shared objects from SQL.
        """
        if self._conn is None:  # pragma: no cover — always set when called from initialize()
            return
        try:
            import sqlite_vec
        except ImportError:
            log.warning("sqlite-vec not installed — vector search disabled")
            return

        try:
            # AttributeError, not sqlite3.Error, is what a CPython built
            # without --enable-loadable-sqlite-extensions raises here.
            await self._conn.enable_load_extension(True)
        except (AttributeError, sqlite3.Error, OSError) as exc:
            log.warning(
                "This Python build cannot load SQLite extensions (%s) — vector search disabled",
                exc,
            )
            return

        try:
            await self._conn.load_extension(sqlite_vec.loadable_path())
            self._vec_loaded = True
            log.debug("sqlite-vec extension loaded")
        except (AttributeError, sqlite3.Error, OSError) as exc:
            log.warning("Failed to load sqlite-vec: %s — vector search disabled", exc)
        finally:
            with contextlib.suppress(AttributeError, sqlite3.Error, OSError):
                await self._conn.enable_load_extension(False)

    async def close(self) -> None:
        """Close the connection, releasing it even if closing errors."""
        conn, self._conn = self._conn, None
        self._txn_owner = None
        self._txn_depth = 0
        if conn is None:
            return
        try:
            await conn.close()
        except (sqlite3.Error, OSError) as exc:
            # The handle is unusable either way; clearing it first means a
            # failed close cannot strand callers on a dead connection.
            log.warning("Error closing database: %s", exc)
        else:
            log.debug("Database closed")

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> aiosqlite.Cursor:
        """Execute *sql*, returning the cursor.

        The caller owns the returned cursor and must close it. Prefer
        :meth:`fetch_one` or :meth:`fetch_all`, which handle that.
        """
        try:
            return await self.conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise DatabaseError(_sql_error_message(exc, sql)) from exc

    async def execute_many(self, sql: str, params_seq: list[tuple[object, ...]]) -> None:
        try:
            await self.conn.executemany(sql, params_seq)
        except sqlite3.Error as exc:
            raise DatabaseError(_sql_error_message(exc, sql)) from exc

    async def fetch_one(self, sql: str, params: tuple[object, ...] = ()) -> dict[str, Any] | None:
        try:
            cursor = await self.conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise DatabaseError(_sql_error_message(exc, sql)) from exc
        # Closing the cursor is what returns its statement to SQLite's cache;
        # leaving it to the garbage collector leaks one per query.
        async with cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
        try:
            cursor = await self.conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise DatabaseError(_sql_error_message(exc, sql)) from exc
        async with cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        """Run a block inside a transaction, nesting via savepoints.

        The outermost entry for a task takes the write lock and issues
        ``BEGIN IMMEDIATE``; re-entry from the same task opens a ``SAVEPOINT``
        so an inner failure discards only the inner work.  A different task
        waits for the lock instead of nesting inside a transaction it does not
        own — see the module docstring for why that distinction matters.

        ``BEGIN IMMEDIATE`` rather than plain ``BEGIN``: every write path here
        reads before it writes, and a deferred transaction that upgrades from a
        read lock to a write lock can fail with ``SQLITE_BUSY_SNAPSHOT``, which
        ``busy_timeout`` explicitly does not retry.  Taking the write lock up
        front turns that into an ordinary, retryable wait.

        Raises:
            DatabaseError: The transaction could not be started, or finalising
                it failed. In either case the connection is left with no
                transaction open.
        """
        task = asyncio.current_task()
        is_outermost = self._txn_owner is not task or self._txn_depth == 0

        if is_outermost:
            await self._write_lock.acquire()
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
            except BaseException:
                self._write_lock.release()
                raise
            self._txn_owner = task
            self._txn_depth = 1
            savepoint = ""
        else:
            savepoint = f"sp_{self._txn_depth}"
            await self.conn.execute(f"SAVEPOINT {savepoint}")
            self._txn_depth += 1

        try:
            yield
        except BaseException:
            await self._unwind(is_outermost, savepoint, committing=False)
            raise
        else:
            await self._unwind(is_outermost, savepoint, committing=True)

    async def _unwind(self, is_outermost: bool, savepoint: str, *, committing: bool) -> None:
        """Finalise one transaction level and restore the bookkeeping.

        Args:
            is_outermost: Whether this level owns the write lock.
            savepoint: Savepoint name for a nested level; empty when outermost.
            committing: ``True`` to commit/release, ``False`` to roll back.

        Raises:
            DatabaseError: Committing failed. The transaction is rolled back
                first, so the connection is always left clean.
        """
        try:
            if is_outermost:
                await self.conn.execute("COMMIT" if committing else "ROLLBACK")
            elif committing:
                await self.conn.execute(f"RELEASE {savepoint}")
            else:
                # ROLLBACK TO rewinds the savepoint but leaves it on the stack;
                # RELEASE then pops it, so the depth we track and SQLite's own
                # savepoint stack stay in agreement.
                await self.conn.execute(f"ROLLBACK TO {savepoint}")
                await self.conn.execute(f"RELEASE {savepoint}")
        except sqlite3.Error as exc:
            # Finalisation failed — most often a disk or lock error. Abandon
            # the whole transaction rather than leave the connection holding a
            # half-finished one that the next caller would inherit.
            log.error("Failed to %s transaction: %s", "commit" if committing else "roll back", exc)
            with contextlib.suppress(sqlite3.Error):
                await self.conn.execute("ROLLBACK")
            self._reset_transaction_state(is_outermost)
            raise DatabaseError(f"Failed to finalise transaction: {exc}") from exc
        else:
            if is_outermost:
                self._reset_transaction_state(is_outermost=True)
            else:
                self._txn_depth -= 1

    def _reset_transaction_state(self, is_outermost: bool) -> None:
        """Return to the no-transaction-open state and hand back the lock."""
        if not is_outermost:
            self._txn_depth = max(0, self._txn_depth - 1)
            return
        self._txn_owner = None
        self._txn_depth = 0
        if self._write_lock.locked():
            self._write_lock.release()

    async def __aenter__(self) -> Database:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
