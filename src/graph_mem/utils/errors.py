"""Custom exception hierarchy for graph-mem.

All exceptions inherit from GraphMemError so callers can catch broadly
or narrowly. Every exception carries a human-readable message with
actionable guidance where possible.
"""

from __future__ import annotations


class GraphMemError(Exception):
    """Base exception for all graph-mem errors."""

    def __init__(self, message: str, *, details: str | None = None) -> None:
        self.details = details
        super().__init__(message)


# ── Input validation ─────────────────────────────────────────────────────────


class ValidationError(GraphMemError):
    """Caller-supplied input failed a boundary check.

    Raised at trust boundaries (MCP tool arguments, HTTP request bodies) when
    an argument is malformed, out of range, or names something the caller is
    not permitted to address.  Distinct from :class:`IntegrityError`, which
    reports a constraint the *database* rejected.
    """


# ── Database errors ──────────────────────────────────────────────────────────


class DatabaseError(GraphMemError):
    """SQLite connection or query failure."""


class SchemaError(DatabaseError):
    """Migration or schema issue."""


class IntegrityError(DatabaseError):
    """Constraint violation."""


# ── Entity errors ────────────────────────────────────────────────────────────


class EntityError(GraphMemError):
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


class RelationshipError(GraphMemError):
    """Relationship operation failure."""


# ── Embedding errors ─────────────────────────────────────────────────────────


class EmbeddingError(GraphMemError):
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


class SearchError(GraphMemError):
    """Query execution failure."""


# ── Config errors ────────────────────────────────────────────────────────────


class ConfigError(GraphMemError):
    """Invalid configuration."""


# ── Export/Import errors ─────────────────────────────────────────────────────


class ExportError(GraphMemError):
    """Import or export failure."""
