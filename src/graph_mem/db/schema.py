"""Schema creation and migration runner.

Migrations are numbered Python modules in db/migrations/. Each module
exposes an async ``migrate(db)`` function and a ``DESCRIPTION`` string.
The runner applies them in order, tracking progress in schema_version.
"""

from __future__ import annotations

import importlib
import pkgutil
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    from graph_mem.db.connection import Database

from graph_mem.utils.errors import SchemaError
from graph_mem.utils.logging import get_logger

log = get_logger("db.schema")


def _discover_migrations() -> list[tuple[int, ModuleType]]:
    """Find all migration modules in graph_mem.db.migrations.

    Returns a sorted list of (version, module) tuples.
    """
    import graph_mem.db.migrations as pkg

    migrations: list[tuple[int, ModuleType]] = []
    for info in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
        name = info.name
        # Modules are named v001_description, v002_description, etc.
        short = name.rsplit(".", 1)[-1]
        if not short.startswith("v"):
            continue
        try:
            version = int(short.split("_", 1)[0][1:])
        except (ValueError, IndexError):
            log.warning("Skipping migration module with unparseable version: %s", name)
            continue
        mod = importlib.import_module(name)
        if not hasattr(mod, "migrate"):
            log.warning("Migration %s missing migrate() function, skipping", name)
            continue
        migrations.append((version, mod))

    migrations.sort(key=lambda m: m[0])
    return migrations


async def _ensure_version_table(db: Database) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at REAL NOT NULL,
            description TEXT NOT NULL
        )
    """)


async def get_current_version(db: Database) -> int:
    """Return the highest applied migration version, or 0 if none."""
    await _ensure_version_table(db)
    row = await db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
    return int(row["v"]) if row and row["v"] is not None else 0


async def get_applied_versions(db: Database) -> set[int]:
    """Return every migration version recorded as applied.

    The applied *set*, not just the maximum: deciding what to run from
    ``MAX(version)`` means a migration numbered below one already applied is
    skipped forever. That is exactly what happens when a fix is backported —
    v002 authored after v003 shipped would never run on any database in the
    field, and nothing would report it.
    """
    await _ensure_version_table(db)
    rows = await db.fetch_all("SELECT version FROM schema_version")
    return {int(r["version"]) for r in rows}


async def run_migrations(db: Database) -> int:
    """Apply every migration not yet recorded, and return how many ran.

    Each migration runs inside its own transaction together with the row that
    records it, so a failure leaves the database at the last complete version
    rather than partway through one.

    Raises:
        SchemaError: A migration failed, or the database was written by a
            newer version of graph-mem than this one understands.
    """
    await _ensure_version_table(db)
    already_applied = await get_applied_versions(db)
    migrations = _discover_migrations()
    known_versions = {version for version, _ in migrations}

    # A database carrying migrations this build has never heard of was written
    # by a newer graph-mem. Continuing would let old code write rows against a
    # schema it does not understand, so refuse rather than corrupt.
    unknown = already_applied - known_versions
    if unknown:
        raise SchemaError(
            f"Database has migrations this version does not know about: "
            f"{sorted(unknown)}. It was created by a newer graph-mem — "
            f"upgrade rather than downgrade."
        )

    applied = 0
    for version, mod in migrations:
        if version in already_applied:
            continue
        desc = getattr(mod, "DESCRIPTION", f"Migration v{version:03d}")
        log.info("Applying migration v%03d: %s", version, desc)
        try:
            async with db.transaction():
                await mod.migrate(db)
                await db.execute(
                    "INSERT INTO schema_version (version, applied_at, description) "
                    "VALUES (?, ?, ?)",
                    (version, time.time(), desc),
                )
            applied += 1
        except Exception as exc:
            raise SchemaError(f"Migration v{version:03d} failed: {exc}") from exc

    if applied:
        log.info("Applied %d migration(s), now at v%03d", applied, max(known_versions, default=0))
    else:
        log.debug("Schema up to date at v%03d", max(already_applied, default=0))

    return applied
