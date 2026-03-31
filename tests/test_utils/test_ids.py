"""Tests for graphrag_mcp.utils.ids — ULID-based ID generation."""

from __future__ import annotations

from graphrag_mcp.utils.ids import generate_id


def test_generate_id_returns_string():
    """generate_id returns a non-empty lowercase string."""
    result = generate_id()
    assert isinstance(result, str)
    assert len(result) > 0
    assert result == result.lower()


def test_generate_id_uniqueness():
    """Successive calls return distinct IDs."""
    ids = {generate_id() for _ in range(100)}
    assert len(ids) == 100


def test_generate_id_lexicographic_order():
    """IDs generated later sort after earlier ones (ULID property)."""
    a = generate_id()
    b = generate_id()
    assert b >= a
