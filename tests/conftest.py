"""Shared test fixtures for graphrag-mcp."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Provide a temporary database path."""
    return tmp_path / "test_graph.db"


@pytest.fixture
def tmp_graphrag_dir(tmp_path: Path) -> Path:
    """Provide a temporary .graphrag directory."""
    d = tmp_path / ".graphrag"
    d.mkdir()
    return d
