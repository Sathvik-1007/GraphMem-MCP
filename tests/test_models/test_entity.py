"""Tests for graphrag_mcp.models.entity — Entity dataclass."""

from __future__ import annotations

import json

import pytest

from graphrag_mcp.models.entity import Entity


def test_entity_creation():
    """Entity can be created with name and type."""
    e = Entity(name="Alice", entity_type="person")
    assert e.name == "Alice"
    assert e.entity_type == "person"
    assert len(e.id) > 0
    assert e.description == ""
    assert e.properties == {}
    assert e.created_at > 0
    assert e.updated_at > 0


def test_entity_strips_name():
    """Whitespace is stripped from entity name."""
    e = Entity(name="  Bob  ", entity_type="person")
    assert e.name == "Bob"


def test_entity_lowercases_type():
    """Entity type is lowercased."""
    e = Entity(name="Paris", entity_type="PLACE")
    assert e.entity_type == "place"


def test_entity_empty_name_raises():
    """Empty name raises ValueError."""
    with pytest.raises(ValueError, match="name must not be empty"):
        Entity(name="", entity_type="thing")


def test_entity_whitespace_name_raises():
    """Whitespace-only name raises ValueError."""
    with pytest.raises(ValueError, match="name must not be empty"):
        Entity(name="   ", entity_type="thing")


def test_entity_empty_type_raises():
    """Empty entity_type raises ValueError."""
    with pytest.raises(ValueError, match="type must not be empty"):
        Entity(name="X", entity_type="")


def test_entity_embedding_text():
    """embedding_text combines name, type, and description."""
    e = Entity(name="Alice", entity_type="person", description="A developer")
    assert e.embedding_text == "Alice (person) A developer"


def test_entity_embedding_text_no_description():
    """embedding_text without description includes only name and type."""
    e = Entity(name="Alice", entity_type="person")
    assert e.embedding_text == "Alice (person)"


def test_entity_properties_json():
    """properties_json returns valid JSON string."""
    e = Entity(name="X", entity_type="t", properties={"key": "value"})
    parsed = json.loads(e.properties_json)
    assert parsed == {"key": "value"}


def test_entity_to_dict():
    """to_dict returns a complete dictionary representation."""
    e = Entity(name="Alice", entity_type="person", description="dev")
    d = e.to_dict()
    assert d["name"] == "Alice"
    assert d["entity_type"] == "person"
    assert d["description"] == "dev"
    assert "id" in d
    assert "created_at" in d
    assert "updated_at" in d
    assert "properties" in d


def test_entity_from_row():
    """from_row reconstructs an Entity from a database row dict."""
    row = {
        "id": "test-id",
        "name": "Alice",
        "entity_type": "person",
        "description": "A tester",
        "properties": '{"role": "admin"}',
        "created_at": 1000.0,
        "updated_at": 2000.0,
    }
    e = Entity.from_row(row)
    assert e.id == "test-id"
    assert e.name == "Alice"
    assert e.entity_type == "person"
    assert e.description == "A tester"
    assert e.properties == {"role": "admin"}
    assert e.created_at == 1000.0
    assert e.updated_at == 2000.0


def test_entity_from_row_missing_optional_fields():
    """from_row handles missing optional fields gracefully."""
    row = {
        "id": "id1",
        "name": "Bob",
        "entity_type": "concept",
    }
    e = Entity.from_row(row)
    assert e.description == ""
    assert e.properties == {}


def test_entity_roundtrip():
    """to_dict → from_row preserves all fields."""
    original = Entity(name="Test", entity_type="concept", description="A test entity")
    d = original.to_dict()
    # Simulate DB serialization (properties becomes JSON string)
    d["properties"] = json.dumps(d["properties"])
    restored = Entity.from_row(d)
    assert restored.name == original.name
    assert restored.entity_type == original.entity_type
    assert restored.description == original.description
    assert restored.id == original.id
