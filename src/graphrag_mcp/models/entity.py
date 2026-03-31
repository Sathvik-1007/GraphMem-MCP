"""Entity model — nodes in the knowledge graph.

An entity represents any concept worth remembering: a person, place,
event, decision, code component, or abstract idea. Entities carry a
name, type, optional description, and arbitrary JSON properties.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from graphrag_mcp.utils.ids import generate_id


@dataclass(slots=True)
class Entity:
    """A node in the knowledge graph.

    Attributes:
        id: ULID primary key.
        name: Human-readable name (must be non-empty).
        entity_type: Category string (e.g. 'person', 'place', 'concept').
        description: Rich description used for embedding and context.
        properties: Arbitrary key-value pairs stored as JSON.
        created_at: Unix timestamp of creation.
        updated_at: Unix timestamp of last modification.
    """

    name: str
    entity_type: str
    id: str = field(default_factory=generate_id)
    description: str = ""
    properties: dict[str, object] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.entity_type = self.entity_type.strip().lower()
        if not self.name:
            raise ValueError("Entity name must not be empty.")
        if not self.entity_type:
            raise ValueError("Entity type must not be empty.")

    @property
    def embedding_text(self) -> str:
        """Text to embed for semantic search.

        Combines name, type, and description into a single string
        that captures the entity's identity for vector similarity.
        """
        parts = [self.name, f"({self.entity_type})"]
        if self.description:
            parts.append(self.description)
        return " ".join(parts)

    @property
    def properties_json(self) -> str:
        return json.dumps(self.properties, ensure_ascii=False, default=str)

    @classmethod
    def from_row(cls, row: dict[str, object]) -> Entity:
        props_raw = row.get("properties")
        props = json.loads(props_raw) if isinstance(props_raw, str) else {}
        return cls(
            id=str(row["id"]),
            name=str(row["name"]),
            entity_type=str(row["entity_type"]),
            description=str(row.get("description") or ""),
            properties=props,
            created_at=float(row.get("created_at", 0)),
            updated_at=float(row.get("updated_at", 0)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "description": self.description,
            "properties": self.properties,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
