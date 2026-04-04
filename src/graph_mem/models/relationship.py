"""Relationship model — directed edges in the knowledge graph.

A relationship connects two entities with a typed, weighted, directed edge.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from graph_mem.models.utils import safe_float
from graph_mem.utils.ids import generate_id


@dataclass(slots=True)
class Relationship:
    """A directed edge between two entities.

    Attributes:
        id: ULID primary key.
        source_id: ULID of the source entity.
        target_id: ULID of the target entity.
        relationship_type: Edge label (e.g. 'knows', 'lives_in').
        weight: Strength/confidence in [0, 1]. Defaults to 1.0.
        properties: Arbitrary key-value pairs stored as JSON.
        created_at: Unix timestamp of creation.
        updated_at: Unix timestamp of last modification.
    """

    source_id: str
    target_id: str
    relationship_type: str
    id: str = field(default_factory=generate_id)
    weight: float = 1.0
    properties: dict[str, object] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.relationship_type = self.relationship_type.strip().lower()
        if not self.source_id:
            raise ValueError("Relationship source_id must not be empty.")
        if not self.target_id:
            raise ValueError("Relationship target_id must not be empty.")
        if not self.relationship_type:
            raise ValueError("Relationship type must not be empty.")
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"Weight must be in [0, 1], got {self.weight}")

    @property
    def properties_json(self) -> str:
        return json.dumps(self.properties, ensure_ascii=False, default=str)

    @classmethod
    def from_row(cls, row: dict[str, object]) -> Relationship:
        props_raw = row.get("properties")
        props = json.loads(props_raw) if isinstance(props_raw, str) else {}
        return cls(
            id=str(row["id"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            relationship_type=str(row["relationship_type"]),
            weight=safe_float(row.get("weight"), 1.0),
            properties=props,
            created_at=safe_float(row.get("created_at")),
            updated_at=safe_float(row.get("updated_at")),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type,
            "weight": self.weight,
            "properties": self.properties,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
