"""Tests for graphrag_mcp.cli.install — skill installer."""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag_mcp.cli.install import (
    _SECTION_BEGIN,
    _SECTION_END,
    SUPPORTED_AGENTS,
    install_skill,
    uninstall_skill,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Provide a temporary project directory."""
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def home_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a fake HOME so global installs write to a temp directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


# ---------------------------------------------------------------------------
# SUPPORTED_AGENTS
# ---------------------------------------------------------------------------


def test_supported_agents_list() -> None:
    """All seven expected agents should be present in SUPPORTED_AGENTS."""
    expected = {"claude", "opencode", "codex", "gemini", "cursor", "windsurf", "amp"}
    assert set(SUPPORTED_AGENTS) == expected
    assert len(SUPPORTED_AGENTS) == 7


# ---------------------------------------------------------------------------
# Project-level installs (overwrite method)
# ---------------------------------------------------------------------------


def test_install_claude_project(project_dir: Path) -> None:
    """Installing claude at project level creates the skill file."""
    result = install_skill("claude", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".claude" / "skills" / "graphrag-mcp" / "SKILL.md"

    content = result.read_text(encoding="utf-8")
    assert "graphrag-mcp" in content.lower() or "graphrag" in content.lower()
    assert len(content) > 100  # non-trivial content


def test_install_opencode_project(project_dir: Path) -> None:
    """Installing opencode at project level creates the skill file."""
    result = install_skill("opencode", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".opencode" / "skills" / "graphrag-mcp" / "SKILL.md"

    content = result.read_text(encoding="utf-8")
    assert len(content) > 100


def test_install_cursor_project(project_dir: Path) -> None:
    """Installing cursor at project level creates .cursor/rules/graphrag-mcp.md."""
    result = install_skill("cursor", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".cursor" / "rules" / "graphrag-mcp.md"

    content = result.read_text(encoding="utf-8")
    assert len(content) > 100


# ---------------------------------------------------------------------------
# Global-level installs
# ---------------------------------------------------------------------------


def test_install_claude_global(home_dir: Path) -> None:
    """Installing claude at global level writes under HOME."""
    result = install_skill("claude", scope="global")

    assert result.exists()
    assert result == home_dir / ".claude" / "skills" / "graphrag-mcp" / "SKILL.md"

    content = result.read_text(encoding="utf-8")
    assert len(content) > 100


def test_install_cursor_global_raises(home_dir: Path) -> None:
    """Cursor has no global path — should raise ValueError."""
    with pytest.raises(ValueError, match="does not support global"):
        install_skill("cursor", scope="global")


# ---------------------------------------------------------------------------
# Section-method installs
# ---------------------------------------------------------------------------


def test_install_gemini_project_section(project_dir: Path) -> None:
    """Gemini uses section method for project, writing into AGENTS.md."""
    result = install_skill("gemini", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / "AGENTS.md"

    content = result.read_text(encoding="utf-8")
    assert _SECTION_BEGIN in content
    assert _SECTION_END in content
    assert "graphrag" in content.lower()


def test_install_section_replaces(project_dir: Path) -> None:
    """Installing the same section-based agent twice should not duplicate."""
    install_skill("gemini", scope="project", project_dir=project_dir)
    install_skill("gemini", scope="project", project_dir=project_dir)

    target = project_dir / "AGENTS.md"
    content = target.read_text(encoding="utf-8")

    # There should be exactly one begin and one end marker
    assert content.count(_SECTION_BEGIN) == 1
    assert content.count(_SECTION_END) == 1


def test_install_windsurf_section(project_dir: Path) -> None:
    """Windsurf uses section method, writing into AGENTS.md."""
    result = install_skill("windsurf", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / "AGENTS.md"

    content = result.read_text(encoding="utf-8")
    assert _SECTION_BEGIN in content
    assert _SECTION_END in content


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def test_uninstall_overwrite(project_dir: Path) -> None:
    """Uninstalling an overwrite agent should remove the file entirely."""
    path = install_skill("claude", scope="project", project_dir=project_dir)
    assert path.exists()

    removed = uninstall_skill("claude", scope="project", project_dir=project_dir)
    assert removed is True
    assert not path.exists()


def test_uninstall_section(project_dir: Path) -> None:
    """Uninstalling a section agent should remove its markers from the file."""
    install_skill("gemini", scope="project", project_dir=project_dir)
    target = project_dir / "AGENTS.md"
    assert target.exists()
    assert _SECTION_BEGIN in target.read_text(encoding="utf-8")

    removed = uninstall_skill("gemini", scope="project", project_dir=project_dir)
    assert removed is True

    # The file should either be removed (if it's now empty) or have
    # the section stripped out.
    if target.exists():
        remaining = target.read_text(encoding="utf-8")
        assert _SECTION_BEGIN not in remaining
        assert _SECTION_END not in remaining


def test_uninstall_nonexistent(project_dir: Path) -> None:
    """Uninstalling when nothing is installed should return False."""
    removed = uninstall_skill("claude", scope="project", project_dir=project_dir)
    assert removed is False


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_invalid_agent_raises() -> None:
    """Passing an unsupported agent name should raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported agent"):
        install_skill("notepad_plus_plus", scope="project")
