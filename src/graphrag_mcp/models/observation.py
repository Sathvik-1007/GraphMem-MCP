"""Observation model — factual statements attached to entities.

Observations are atomic pieces of knowledge (facts, quotes, events)
that are linked to a specific entity. They are separately embeddable
for fine-grained semantic search.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from graphrag_mcp.utils.ids import generate_id


@dataclass(slots=True)
class Observation:
    """A factual statement attached to an entity.

    Attributes:
        id: ULID primary key.
        entity_id: ULID of the parent entity.
        content: The observation text (must be non-empty).
        source: Provenance string (session ID, document name, etc.).
        created_at: Unix timestamp of creation.
    """

    entity_id: str
    content: str
    id: str = field(default_factory=generate_id)
    source: str = ""
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.content = self.content.strip()
        if not self.entity_id:
            raise ValueError("Observation entity_id must not be empty.")
        if not self.content:
            raise ValueError("Observation content must not be empty.")

    @classmethod
    def from_row(cls, row: dict[str, object]) -> Observation:
        return cls(
            id=str(row["id"]),
            entity_id=str(row["entity_id"]),
            content=str(row["content"]),
            source=str(row.get("source") or ""),
            created_at=float(row.get("created_at", 0)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at,
        }
