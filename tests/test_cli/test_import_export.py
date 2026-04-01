"""Tests for import/export roundtrip with full data (entities, relationships, observations)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from click.testing import CliRunner

from graphrag_mcp.cli.main import cli


def _extract_json(output: str) -> dict:
    """Extract the first JSON object from CLI output."""
    text = output.strip()
    start = text.find("{")
    if start < 0:
        raise ValueError(f"No JSON found in: {text!r}")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text, start)
    return obj


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── Full roundtrip ──────────────────────────────────────────────────────────


def test_import_full_roundtrip(runner: CliRunner, tmp_path: Path) -> None:
    """Import entities + relationships + observations, export, and verify."""
    db_path = tmp_path / "roundtrip.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])

    # Create data with entities, relationships, and observations
    import_data = {
        "version": "0.1.0",
        "entities": [
            {
                "id": "ent_aaa",
                "name": "Alice",
                "entity_type": "person",
                "description": "A researcher",
                "properties": {"affiliation": "MIT"},
            },
            {
                "id": "ent_bbb",
                "name": "Bob",
                "entity_type": "person",
                "description": "An engineer",
                "properties": {},
            },
        ],
        "relationships": [
            {
                "id": "rel_001",
                "source_id": "ent_aaa",
                "target_id": "ent_bbb",
                "relationship_type": "collaborates_with",
                "weight": 0.9,
                "properties": {"project": "Alpha"},
            }
        ],
        "observations": [
            {
                "id": "obs_001",
                "entity_id": "ent_aaa",
                "content": "Published 5 papers",
                "source": "cv",
            },
            {
                "id": "obs_002",
                "entity_id": "ent_bbb",
                "content": "Loves Rust",
                "source": "chat",
            },
        ],
    }
    import_file = tmp_path / "full.json"
    import_file.write_text(json.dumps(import_data))

    # Import
    result = runner.invoke(cli, ["import", str(import_file), "--db", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "Import complete" in result.output
    assert "Entities:      2" in result.output

    # Export back
    export_file = tmp_path / "exported.json"
    result = runner.invoke(cli, ["export", "--db", str(db_path), "--output", str(export_file)])
    assert result.exit_code == 0, result.output

    exported = json.loads(export_file.read_text())
    assert len(exported["entities"]) == 2
    assert len(exported["observations"]) >= 2

    entity_names = {e["name"] for e in exported["entities"]}
    assert entity_names == {"Alice", "Bob"}

    obs_contents = {o["content"] for o in exported["observations"]}
    assert "Published 5 papers" in obs_contents
    assert "Loves Rust" in obs_contents


def test_import_entities_only(runner: CliRunner, tmp_path: Path) -> None:
    """Import with only entities (no relationships/observations)."""
    db_path = tmp_path / "ent_only.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])

    import_data = {
        "entities": [
            {"id": "e1", "name": "X", "entity_type": "concept", "description": ""},
            {"id": "e2", "name": "Y", "entity_type": "concept", "description": ""},
        ],
        "relationships": [],
        "observations": [],
    }
    f = tmp_path / "ent.json"
    f.write_text(json.dumps(import_data))

    result = runner.invoke(cli, ["import", str(f), "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Entities:      2" in result.output
    assert "Relationships: 0" in result.output
    assert "Observations:  0" in result.output


def test_import_missing_entity_for_observations(runner: CliRunner, tmp_path: Path) -> None:
    """Observations referencing unknown entity_ids are skipped gracefully."""
    db_path = tmp_path / "orphan_obs.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])

    import_data = {
        "entities": [
            {"id": "e1", "name": "Known", "entity_type": "concept", "description": ""},
        ],
        "relationships": [],
        "observations": [
            {
                "id": "o1",
                "entity_id": "UNKNOWN_ENTITY_ID",
                "content": "Orphaned fact",
                "source": "",
            },
            {
                "id": "o2",
                "entity_id": "e1",
                "content": "Valid fact",
                "source": "",
            },
        ],
    }
    f = tmp_path / "orphan.json"
    f.write_text(json.dumps(import_data))

    result = runner.invoke(cli, ["import", str(f), "--db", str(db_path)])
    assert result.exit_code == 0
    # Only the valid observation should be imported
    assert "Observations:  1" in result.output


def test_export_to_stdout(runner: CliRunner, tmp_path: Path) -> None:
    """Export without --output prints JSON to stdout."""
    db_path = tmp_path / "stdout.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])
    result = runner.invoke(cli, ["export", "--db", str(db_path)])
    assert result.exit_code == 0
    data = _extract_json(result.output)
    assert "entities" in data
    assert data["entities"] == []


def test_export_creates_parent_dirs(runner: CliRunner, tmp_path: Path) -> None:
    """Export --output creates parent directories if needed."""
    db_path = tmp_path / "export_dirs.db"
    runner.invoke(cli, ["init", "--db", str(db_path)])
    out = tmp_path / "deep" / "nested" / "output.json"
    result = runner.invoke(cli, ["export", "--db", str(db_path), "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
