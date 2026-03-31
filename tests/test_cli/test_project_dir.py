"""Tests for --project-dir flag across all CLI commands."""

from __future__ import annotations

import json
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


# ── init ────────────────────────────────────────────────────────────────────


def test_init_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir creates .graphrag/graph.db inside the project dir."""
    result = runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Initialized" in result.output
    db = tmp_path / ".graphrag" / "graph.db"
    assert db.exists(), f"Expected {db} to exist"


def test_init_db_overrides_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """--db takes priority over --project-dir."""
    explicit_db = tmp_path / "custom" / "my.db"
    result = runner.invoke(cli, ["init", "--project-dir", str(tmp_path), "--db", str(explicit_db)])
    assert result.exit_code == 0, result.output
    assert explicit_db.exists()
    # .graphrag dir should NOT be created when --db is explicit
    assert not (tmp_path / ".graphrag").exists()


# ── status ──────────────────────────────────────────────────────────────────


def test_status_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir works for the status command."""
    runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])
    result = runner.invoke(cli, ["status", "--project-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["entities"] == 0


# ── validate ────────────────────────────────────────────────────────────────


def test_validate_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir works for the validate command."""
    runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])
    result = runner.invoke(cli, ["validate", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "All checks passed" in result.output


# ── export ──────────────────────────────────────────────────────────────────


def test_export_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir works for the export command."""
    runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])
    output_file = tmp_path / "export.json"
    result = runner.invoke(
        cli,
        ["export", "--project-dir", str(tmp_path), "--output", str(output_file)],
    )
    assert result.exit_code == 0, result.output
    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert "entities" in data


# ── import ──────────────────────────────────────────────────────────────────


def test_import_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir works for the import command."""
    runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])

    import_data = {
        "entities": [
            {
                "id": "e1",
                "name": "TestNode",
                "entity_type": "concept",
                "description": "",
                "properties": {},
            }
        ],
        "relationships": [],
        "observations": [],
    }
    import_file = tmp_path / "data.json"
    import_file.write_text(json.dumps(import_data))

    result = runner.invoke(
        cli,
        ["import", str(import_file), "--project-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Import complete" in result.output


# ── server --help ───────────────────────────────────────────────────────────


def test_server_project_dir_in_help(runner: CliRunner) -> None:
    """server --help mentions --project-dir."""
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    assert "--project-dir" in result.output


# ── install ─────────────────────────────────────────────────────────────────


def test_install_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """install --project-dir installs the skill in the given directory."""
    result = runner.invoke(cli, ["install", "claude", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    skill_file = tmp_path / ".claude" / "skills" / "graphrag-mcp" / "SKILL.md"
    assert skill_file.exists()


# ── edge cases ──────────────────────────────────────────────────────────────


def test_project_dir_nonexistent(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir with a nonexistent directory fails gracefully."""
    bad_dir = tmp_path / "does_not_exist"
    result = runner.invoke(cli, ["init", "--project-dir", str(bad_dir)])
    assert result.exit_code != 0
    # Click should report the path doesn't exist
    assert "does not exist" in result.output.lower() or "error" in result.output.lower()


def test_project_dir_is_file(runner: CliRunner, tmp_path: Path) -> None:
    """--project-dir pointing at a file fails gracefully."""
    file_path = tmp_path / "afile.txt"
    file_path.write_text("hello")
    result = runner.invoke(cli, ["init", "--project-dir", str(file_path)])
    assert result.exit_code != 0
