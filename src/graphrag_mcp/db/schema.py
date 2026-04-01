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

    from graphrag_mcp.db.connection import Database

from graphrag_mcp.utils.errors import SchemaError
from graphrag_mcp.utils.logging import get_logger

log = get_logger("db.schema")


def _discover_migrations() -> list[tuple[int, ModuleType]]:
    """Find all migration modules in graphrag_mcp.db.migrations.

    Returns a sorted list of (version, module) tuples.
    """
    import graphrag_mcp.db.migrations as pkg

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
    await _ensure_version_table(db)
    row = await db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
    return int(row["v"]) if row and row["v"] is not None else 0


async def run_migrations(db: Database) -> int:
    """Apply all pending migrations and return the number applied."""
    await _ensure_version_table(db)
    current = await get_current_version(db)
    migrations = _discover_migrations()
    applied = 0
    last_applied_version = current

    for version, mod in migrations:
        if version <= current:
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
            last_applied_version = version
        except Exception as exc:
            raise SchemaError(f"Migration v{version:03d} failed: {exc}") from exc

    if applied:
        log.info("Applied %d migration(s), now at v%03d", applied, last_applied_version)
    else:
        log.debug("Schema up to date at v%03d", current)

    return applied
