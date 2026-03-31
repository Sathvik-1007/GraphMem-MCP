"""CLI entry points for graphrag-mcp."""

from __future__ import annotations

from graphrag_mcp.cli.install import install_skill, uninstall_skill
from graphrag_mcp.cli.main import cli, main

__all__ = ["cli", "install_skill", "main", "uninstall_skill"]
