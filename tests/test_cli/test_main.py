"""Tests for CLI commands using Click's CliRunner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from click.testing import CliRunner

from graphrag_mcp.cli.main import cli


def _extract_json(output: str) -> dict:
    """Extract the first JSON object from CLI output that may contain logging noise."""
    text = output.strip()
    json_start = text.find("{")
    if json_start < 0:
        raise ValueError(f"No JSON object found in output: {text!r}")
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(text, json_start)
    return obj


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "graphrag-mcp" in result.output


def test_init_creates_database(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_init.db"
    result = runner.invoke(cli, ["init", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Initialized" in result.output
    assert db_path.exists()


def test_init_with_default_path(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert "Initialized" in result.output


def test_status_json_output(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_status.db"
    # First init the database
    runner.invoke(cli, ["init", "--db", str(db_path)])
    result = runner.invoke(cli, ["status", "--db", str(db_path), "--json"], catch_exceptions=False)
    assert result.exit_code == 0
    data = _extract_json(result.output)
    assert "entities" in data
    assert "relationships" in data
    assert "observations" in data
    assert data["entities"] == 0


def test_status_pretty_output(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_status_pretty.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])
    result = runner.invoke(cli, ["status", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Entities:" in result.output
    assert "Relationships:" in result.output


def test_validate_clean_database(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_validate.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])
    result = runner.invoke(cli, ["validate", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "All checks passed" in result.output


def test_export_empty_database(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_export.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])
    result = runner.invoke(cli, ["export", "--db", str(db_path)])
    assert result.exit_code == 0
    data = _extract_json(result.output)
    assert data["entities"] == []
    assert data["relationships"] == []
    assert data["observations"] == []


def test_export_to_file(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_export_file.db"
    output_path = tmp_path / "export.json"
    runner.invoke(cli, ["init", "--db", str(db_path)])
    result = runner.invoke(cli, ["export", "--db", str(db_path), "--output", str(output_path)])
    assert result.exit_code == 0
    assert output_path.exists()
    data = json.loads(output_path.read_text())
    assert "entities" in data


def test_import_roundtrip(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_import.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])

    # Create import data
    import_data = {
        "version": "0.1.0",
        "entities": [
            {
                "id": "ent_001",
                "name": "TestEntity",
                "entity_type": "concept",
                "description": "A test entity",
                "properties": {},
            }
        ],
        "relationships": [],
        "observations": [],
    }
    import_file = tmp_path / "import.json"
    import_file.write_text(json.dumps(import_data))

    result = runner.invoke(cli, ["import", str(import_file), "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Import complete" in result.output
    assert "Entities:" in result.output


def test_import_invalid_json(runner: CliRunner, tmp_path: Path) -> None:
    db_path = tmp_path / "test_import_bad.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])

    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not valid json {{{")

    result = runner.invoke(cli, ["import", str(bad_file), "--db", str(db_path)])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_server_command_exists(runner: CliRunner) -> None:
    """Verify the server command is registered (doesn't start the server)."""
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    assert "MCP transport protocol" in result.output


def test_install_command_exists(runner: CliRunner) -> None:
    """Verify the install command is registered."""
    result = runner.invoke(cli, ["install", "--help"])
    assert result.exit_code == 0
    assert "AGENT" in result.output
