"""Tests for graphrag_mcp.models.observation — Observation dataclass."""

from __future__ import annotations

import pytest

from graphrag_mcp.models.observation import PENDING_ENTITY_ID, Observation


def test_observation_creation():
    """Observation can be created with entity_id and content."""
    o = Observation(entity_id="ent-1", content="Some fact")
    assert o.entity_id == "ent-1"
    assert o.content == "Some fact"
    assert len(o.id) > 0
    assert o.source == ""
    assert o.created_at > 0


def test_observation_strips_content():
    """Whitespace is stripped from content."""
    o = Observation(entity_id="ent-1", content="  hello world  ")
    assert o.content == "hello world"


def test_observation_empty_entity_id_raises():
    """Empty entity_id raises ValueError."""
    with pytest.raises(ValueError, match="entity_id must not be empty"):
        Observation(entity_id="", content="fact")


def test_observation_empty_content_raises():
    """Empty content raises ValueError."""
    with pytest.raises(ValueError, match="content must not be empty"):
        Observation(entity_id="ent-1", content="")


def test_observation_whitespace_content_raises():
    """Whitespace-only content raises ValueError."""
    with pytest.raises(ValueError, match="content must not be empty"):
        Observation(entity_id="ent-1", content="   ")


def test_observation_pending():
    """Observation.pending creates with placeholder entity_id."""
    o = Observation.pending("Some fact", source="test")
    assert o.entity_id == PENDING_ENTITY_ID
    assert o.content == "Some fact"
    assert o.source == "test"


def test_observation_from_row():
    """from_row reconstructs an Observation from a database row dict."""
    row = {
        "id": "obs-1",
        "entity_id": "ent-1",
        "content": "A fact",
        "source": "test-source",
        "created_at": 1000.0,
    }
    o = Observation.from_row(row)
    assert o.id == "obs-1"
    assert o.entity_id == "ent-1"
    assert o.content == "A fact"
    assert o.source == "test-source"
    assert o.created_at == 1000.0


def test_observation_from_row_missing_optional():
    """from_row handles missing optional fields gracefully."""
    row = {
        "id": "obs-2",
        "entity_id": "ent-2",
        "content": "Another fact",
    }
    o = Observation.from_row(row)
    assert o.source == ""


def test_observation_to_dict():
    """to_dict returns a complete dictionary representation."""
    o = Observation(entity_id="ent-1", content="Some fact", source="src")
    d = o.to_dict()
    assert d["entity_id"] == "ent-1"
    assert d["content"] == "Some fact"
    assert d["source"] == "src"
    assert "id" in d
    assert "created_at" in d


def test_observation_roundtrip():
    """to_dict → from_row preserves all fields."""
    original = Observation(entity_id="ent-1", content="A fact", source="test")
    d = original.to_dict()
    restored = Observation.from_row(d)
    assert restored.id == original.id
    assert restored.entity_id == original.entity_id
    assert restored.content == original.content
    assert restored.source == original.source
