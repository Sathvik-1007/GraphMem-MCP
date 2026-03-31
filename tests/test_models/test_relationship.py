"""Tests for graphrag_mcp.models.relationship — Relationship dataclass."""

from __future__ import annotations

import json

import pytest

from graphrag_mcp.models.relationship import Relationship


def test_relationship_creation():
    """Relationship can be created with source_id, target_id, and type."""
    r = Relationship(source_id="src-1", target_id="tgt-1", relationship_type="depends_on")
    assert r.source_id == "src-1"
    assert r.target_id == "tgt-1"
    assert r.relationship_type == "depends_on"
    assert len(r.id) > 0
    assert r.weight == 1.0
    assert r.properties == {}
    assert r.created_at > 0
    assert r.updated_at > 0


def test_relationship_lowercases_type():
    """Relationship type is lowercased and stripped."""
    r = Relationship(source_id="a", target_id="b", relationship_type="  DEPENDS_ON  ")
    assert r.relationship_type == "depends_on"


def test_relationship_empty_source_raises():
    """Empty source_id raises ValueError."""
    with pytest.raises(ValueError, match="source_id must not be empty"):
        Relationship(source_id="", target_id="b", relationship_type="knows")


def test_relationship_empty_target_raises():
    """Empty target_id raises ValueError."""
    with pytest.raises(ValueError, match="target_id must not be empty"):
        Relationship(source_id="a", target_id="", relationship_type="knows")


def test_relationship_empty_type_raises():
    """Empty relationship_type raises ValueError."""
    with pytest.raises(ValueError, match="type must not be empty"):
        Relationship(source_id="a", target_id="b", relationship_type="")


def test_relationship_whitespace_type_raises():
    """Whitespace-only relationship_type raises ValueError."""
    with pytest.raises(ValueError, match="type must not be empty"):
        Relationship(source_id="a", target_id="b", relationship_type="   ")


def test_relationship_weight_out_of_range_low():
    """Weight below 0 raises ValueError."""
    with pytest.raises(ValueError, match="Weight must be in"):
        Relationship(source_id="a", target_id="b", relationship_type="knows", weight=-0.1)


def test_relationship_weight_out_of_range_high():
    """Weight above 1 raises ValueError."""
    with pytest.raises(ValueError, match="Weight must be in"):
        Relationship(source_id="a", target_id="b", relationship_type="knows", weight=1.5)


def test_relationship_weight_boundaries():
    """Boundary weights 0.0 and 1.0 are accepted."""
    r0 = Relationship(source_id="a", target_id="b", relationship_type="knows", weight=0.0)
    r1 = Relationship(source_id="a", target_id="b", relationship_type="knows", weight=1.0)
    assert r0.weight == 0.0
    assert r1.weight == 1.0


def test_relationship_properties_json():
    """properties_json returns valid JSON string."""
    r = Relationship(
        source_id="a",
        target_id="b",
        relationship_type="knows",
        properties={"since": 2020},
    )
    parsed = json.loads(r.properties_json)
    assert parsed == {"since": 2020}


def test_relationship_to_dict():
    """to_dict returns a complete dictionary representation."""
    r = Relationship(
        source_id="src-1",
        target_id="tgt-1",
        relationship_type="depends_on",
        weight=0.8,
        properties={"version": "3"},
    )
    d = r.to_dict()
    assert d["source_id"] == "src-1"
    assert d["target_id"] == "tgt-1"
    assert d["relationship_type"] == "depends_on"
    assert d["weight"] == 0.8
    assert d["properties"] == {"version": "3"}
    assert "id" in d
    assert "created_at" in d
    assert "updated_at" in d


def test_relationship_from_row():
    """from_row reconstructs a Relationship from a database row dict."""
    row = {
        "id": "rel-1",
        "source_id": "src-1",
        "target_id": "tgt-1",
        "relationship_type": "depends_on",
        "weight": 0.75,
        "properties": '{"note": "critical"}',
        "created_at": 1000.0,
        "updated_at": 2000.0,
    }
    r = Relationship.from_row(row)
    assert r.id == "rel-1"
    assert r.source_id == "src-1"
    assert r.target_id == "tgt-1"
    assert r.relationship_type == "depends_on"
    assert r.weight == 0.75
    assert r.properties == {"note": "critical"}
    assert r.created_at == 1000.0
    assert r.updated_at == 2000.0


def test_relationship_from_row_missing_optional():
    """from_row handles missing optional fields gracefully."""
    row = {
        "id": "rel-2",
        "source_id": "a",
        "target_id": "b",
        "relationship_type": "knows",
    }
    r = Relationship.from_row(row)
    assert r.weight == 1.0
    assert r.properties == {}


def test_relationship_roundtrip():
    """to_dict -> from_row preserves all fields."""
    original = Relationship(
        source_id="src-1",
        target_id="tgt-1",
        relationship_type="uses",
        weight=0.9,
        properties={"env": "prod"},
    )
    d = original.to_dict()
    # Simulate DB serialization (properties becomes JSON string)
    d["properties"] = json.dumps(d["properties"])
    restored = Relationship.from_row(d)
    assert restored.id == original.id
    assert restored.source_id == original.source_id
    assert restored.target_id == original.target_id
    assert restored.relationship_type == original.relationship_type
    assert restored.weight == original.weight
    assert restored.properties == original.properties
