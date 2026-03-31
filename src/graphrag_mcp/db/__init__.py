"""Database layer for graphrag-mcp."""

from __future__ import annotations

from graphrag_mcp.db.connection import Database
from graphrag_mcp.db.schema import get_current_version, run_migrations

__all__ = ["Database", "get_current_version", "run_migrations"]
