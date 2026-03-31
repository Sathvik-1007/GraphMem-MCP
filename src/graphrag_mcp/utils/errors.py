"""Custom exception hierarchy for graphrag-mcp.

All exceptions inherit from GraphRAGError so callers can catch broadly
or narrowly. Every exception carries a human-readable message with
actionable guidance where possible.
"""

from __future__ import annotations


class GraphRAGError(Exception):
    """Base exception for all graphrag-mcp errors."""

    def __init__(self, message: str, *, details: str | None = None) -> None:
        self.details = details
        super().__init__(message)


# ── Database errors ──────────────────────────────────────────────────────────


class DatabaseError(GraphRAGError):
    """SQLite connection or query failure."""


class SchemaError(DatabaseError):
    """Migration or schema issue."""


class IntegrityError(DatabaseError):
    """Constraint violation."""


# ── Entity errors ────────────────────────────────────────────────────────────


class EntityError(GraphRAGError):
    """Entity operation failure."""


class EntityNotFoundError(EntityError):
    """Entity name resolution failed.

    Attributes:
        name: The entity name that was searched for.
        suggestions: Similar entity names that might match.
    """

    def __init__(
        self,
        name: str,
        *,
        suggestions: list[str] | None = None,
    ) -> None:
        self.name = name
        self.suggestions = suggestions or []
        hint = ""
        if self.suggestions:
            hint = f" Did you mean: {', '.join(repr(s) for s in self.suggestions[:5])}?"
        super().__init__(f"Entity {name!r} not found.{hint}")


class DuplicateEntityError(EntityError):
    """Merge conflict that cannot be auto-resolved."""


# ── Relationship errors ─────────────────────────────────────────────────────


class RelationshipError(GraphRAGError):
    """Relationship operation failure."""


# ── Embedding errors ─────────────────────────────────────────────────────────


class EmbeddingError(GraphRAGError):
    """Model loading or inference failure."""


class ModelLoadError(EmbeddingError):
    """Embedding model not found or cannot be loaded."""


class DimensionMismatchError(EmbeddingError):
    """Embedding dimension does not match the database schema.

    Attributes:
        expected: Dimension the database was initialized with.
        actual: Dimension the current model produces.
    """

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Embedding dimension mismatch: database expects {expected}, "
            f"model produces {actual}. Use the same model that initialized "
            f"this database, or re-create it."
        )


# ── Search errors ────────────────────────────────────────────────────────────


class SearchError(GraphRAGError):
    """Query execution failure."""


# ── Config errors ────────────────────────────────────────────────────────────


class ConfigError(GraphRAGError):
    """Invalid configuration."""


# ── Export/Import errors ─────────────────────────────────────────────────────


class ExportError(GraphRAGError):
    """Import or export failure."""
