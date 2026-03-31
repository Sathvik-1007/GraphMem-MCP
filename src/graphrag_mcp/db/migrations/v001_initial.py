"""v001: Initial schema — entities, relationships, observations, FTS5, embedding cache."""

from __future__ import annotations

from graphrag_mcp.db.connection import Database

DESCRIPTION = "Initial schema: entities, relationships, observations, FTS5, embedding cache"


async def migrate(db: Database) -> None:
    """Create all base tables and indexes."""

    # ── Core tables ──────────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            properties TEXT DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type
        ON entities(name COLLATE NOCASE, entity_type)
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_entities_updated ON entities(updated_at)")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            target_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            relationship_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_source_target_type
        ON relationships(source_id, target_id, relationship_type)
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relationship_type)")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            source TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id)")

    # ── Embedding cache ──────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS embedding_cache (
            content_hash TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    # ── Metadata (stores embedding dimension, model name, etc.) ──────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # ── FTS5 full-text search ────────────────────────────────────────────
    await db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
            name, description, entity_type,
            content=entities, content_rowid=rowid
        )
    """)
    await db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
            content,
            content=observations, content_rowid=rowid
        )
    """)

    # ── FTS5 sync triggers ───────────────────────────────────────────────
    # After INSERT
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
            INSERT INTO entities_fts(rowid, name, description, entity_type)
            VALUES (NEW.rowid, NEW.name, NEW.description, NEW.entity_type);
        END
    """)
    # After UPDATE
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS entities_fts_update AFTER UPDATE ON entities BEGIN
            INSERT INTO entities_fts(entities_fts, rowid, name, description, entity_type)
            VALUES ('delete', OLD.rowid, OLD.name, OLD.description, OLD.entity_type);
            INSERT INTO entities_fts(rowid, name, description, entity_type)
            VALUES (NEW.rowid, NEW.name, NEW.description, NEW.entity_type);
        END
    """)
    # After DELETE
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
            INSERT INTO entities_fts(entities_fts, rowid, name, description, entity_type)
            VALUES ('delete', OLD.rowid, OLD.name, OLD.description, OLD.entity_type);
        END
    """)

    # Observations FTS triggers
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
            INSERT INTO observations_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
        END
    """)
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_fts_update AFTER UPDATE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, content)
            VALUES ('delete', OLD.rowid, OLD.content);
            INSERT INTO observations_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
        END
    """)
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, content)
            VALUES ('delete', OLD.rowid, OLD.content);
        END
    """)
