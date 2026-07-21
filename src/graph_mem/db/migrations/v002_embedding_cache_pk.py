"""v002: embedding_cache keyed by (content_hash, model_name), not content_hash alone."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_mem.db.connection import Database

DESCRIPTION = "embedding_cache: composite primary key (content_hash, model_name)"

# Columns of the rebuilt table, in order, so the copy cannot depend on the
# column order the old table happened to have.
_COLUMNS = "content_hash, embedding, model_name, created_at"


async def _primary_key_columns(db: Database) -> list[str]:
    """Return embedding_cache's primary-key columns in key order."""
    rows = await db.fetch_all("PRAGMA table_info(embedding_cache)")
    keyed = [r for r in rows if int(r["pk"]) > 0]
    keyed.sort(key=lambda r: int(r["pk"]))
    return [str(r["name"]) for r in keyed]


async def migrate(db: Database) -> None:
    """Rebuild embedding_cache with a composite primary key, preserving rows.

    v001 made ``content_hash`` alone the primary key while every read filters
    on ``content_hash AND model_name`` and every write is an
    ``INSERT OR REPLACE``.  Two models therefore evicted each other's rows on
    write and missed on every read, so the cache never returned a hit once a
    second model was in play.

    SQLite cannot alter a primary key in place, so the table is recreated and
    the rows copied.  Idempotent: it returns early if the key is already
    composite, and the copy uses ``INSERT OR IGNORE`` so duplicate
    ``(content_hash, model_name)`` pairs — impossible under the old key, but
    cheap to tolerate — do not abort the migration.
    """
    if await _primary_key_columns(db) == ["content_hash", "model_name"]:
        return

    await db.execute("DROP TABLE IF EXISTS embedding_cache_new")
    await db.execute("""
        CREATE TABLE embedding_cache_new (
            content_hash TEXT NOT NULL,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (content_hash, model_name)
        )
    """)
    await db.execute(
        f"INSERT OR IGNORE INTO embedding_cache_new ({_COLUMNS}) "
        f"SELECT {_COLUMNS} FROM embedding_cache"
    )
    await db.execute("DROP TABLE embedding_cache")
    await db.execute("ALTER TABLE embedding_cache_new RENAME TO embedding_cache")
