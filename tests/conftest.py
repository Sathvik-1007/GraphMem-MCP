"""Shared test fixtures for graphrag-mcp."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio


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
