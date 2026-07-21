"""Tests for graph_mem.cli.install — skill installer."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_mem.cli.install import (
    _FALLBACK_SKILL,
    _SECTION_BEGIN,
    _SECTION_END,
    SUPPORTED_AGENTS,
    _assemble_skill_content,
    _resolve_skill_dir,
    _skill_dir_candidates,
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
    """All expected agents should be present in SUPPORTED_AGENTS."""
    expected = {
        "claude",
        "opencode",
        "codex",
        "gemini",
        "cursor",
        "windsurf",
        "amp",
        "antigravity",
        "copilot",
        "kiro",
        "roocode",
        "qoder",
        "trae",
        "continue",
        "codebuddy",
        "droid",
        "kilocode",
        "warp",
        "augment",
    }
    assert set(SUPPORTED_AGENTS) == expected
    assert len(SUPPORTED_AGENTS) == 19


# ---------------------------------------------------------------------------
# Project-level installs (overwrite method)
# ---------------------------------------------------------------------------


def test_install_claude_project(project_dir: Path) -> None:
    """Installing claude at project level creates the skill file."""
    result = install_skill("claude", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".claude" / "skills" / "graph-mem" / "SKILL.md"

    content = result.read_text(encoding="utf-8")
    assert "graph-mem" in content.lower()
    assert len(content) > 100  # non-trivial content


def test_install_opencode_project(project_dir: Path) -> None:
    """Installing opencode at project level creates the skill file."""
    result = install_skill("opencode", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".opencode" / "skills" / "graph-mem" / "SKILL.md"

    content = result.read_text(encoding="utf-8")
    assert len(content) > 100


def test_install_cursor_project(project_dir: Path) -> None:
    """Installing cursor at project level creates .cursor/rules/graph-mem.md."""
    result = install_skill("cursor", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".cursor" / "rules" / "graph-mem.md"

    content = result.read_text(encoding="utf-8")
    assert len(content) > 100


# ---------------------------------------------------------------------------
# Global-level installs
# ---------------------------------------------------------------------------


def test_install_claude_global(home_dir: Path) -> None:
    """Installing claude at global level writes under HOME."""
    result = install_skill("claude", scope="global")

    assert result.exists()
    assert result == home_dir / ".claude" / "skills" / "graph-mem" / "SKILL.md"

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
    assert "graph-mem" in content.lower()


def test_install_section_replaces(project_dir: Path) -> None:
    """Installing the same section-based agent twice should not duplicate."""
    install_skill("gemini", scope="project", project_dir=project_dir)
    install_skill("gemini", scope="project", project_dir=project_dir)

    target = project_dir / "AGENTS.md"
    content = target.read_text(encoding="utf-8")

    # There should be exactly one begin and one end marker
    assert content.count(_SECTION_BEGIN) == 1
    assert content.count(_SECTION_END) == 1


def test_install_windsurf_project(project_dir: Path) -> None:
    """Windsurf uses overwrite method, writing into .windsurf/rules/."""
    result = install_skill("windsurf", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".windsurf" / "rules" / "graph-mem.md"

    content = result.read_text(encoding="utf-8")
    assert "graph-mem" in content.lower()


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


# ---------------------------------------------------------------------------
# Domain overlay installs
# ---------------------------------------------------------------------------


def test_install_default_domain_is_general(project_dir: Path) -> None:
    """Install with no domain flag defaults to 'general' overlay."""
    result = install_skill("claude", scope="project", project_dir=project_dir)
    content = result.read_text(encoding="utf-8")
    assert "General Purpose" in content


def test_install_domain_code(project_dir: Path) -> None:
    """Install with domain='code' includes the software engineering overlay."""
    result = install_skill("claude", scope="project", project_dir=project_dir, domain="code")
    content = result.read_text(encoding="utf-8")
    assert "Software Engineering" in content
    # Code domain should include code-specific entity types
    assert "module" in content
    assert "IMPORTS" in content or "DEPENDS_ON" in content


def test_install_domain_research(project_dir: Path) -> None:
    """Install with domain='research' includes the research overlay."""
    result = install_skill("claude", scope="project", project_dir=project_dir, domain="research")
    content = result.read_text(encoding="utf-8")
    assert "Research" in content
    assert "paper" in content
    assert "CITES" in content


def test_install_all_domains_valid(project_dir: Path) -> None:
    """Every supported domain produces a non-trivial assembled skill."""
    for domain in ("general", "code", "research"):
        path = install_skill("opencode", scope="project", project_dir=project_dir, domain=domain)
        content = path.read_text(encoding="utf-8")
        # Core skill content should always be present regardless of domain
        assert "graph-mem" in content.lower() or "knowledge graph" in content.lower()
        assert len(content) > 200  # non-trivial


def test_install_skill_no_domain_leakage(project_dir: Path) -> None:
    """Core skill (no domain overlay) should not contain domain-specific entity types."""
    from graph_mem.cli.install import _assemble_skill_content

    # The core content assembled with general domain should not mention
    # code-specific types like 'module' or 'function' in its entity type lists
    core = _assemble_skill_content(domain="general")
    # General domain should not have code-specific relationship types
    assert "IMPORTS" not in core
    assert "CALLS" not in core


# ── Skill packaging ──────────────────────────────────────────────────────────


class TestSkillResolution:
    """Where the installer looks for the modular skill files.

    Regression: resolution used a single path that only existed in a source
    checkout, so every `pip install` emitted the abbreviated fallback while
    printing a success message. The tests could not catch it because they ran
    in exactly the layout that worked.
    """

    def test_candidates_include_the_installed_bundle_location(self) -> None:
        """The path the wheel actually ships to must be searched."""
        candidates = _skill_dir_candidates()
        assert any(c.parent.name == "_bundled_skills" for c in candidates), (
            f"no _bundled_skills candidate in {candidates}"
        )

    def test_candidates_include_the_source_checkout_location(self) -> None:
        """Developers working from a checkout must still get the real files."""
        candidates = _skill_dir_candidates()
        assert any(c.parent.name == "skills" for c in candidates), (
            f"no source-checkout candidate in {candidates}"
        )

    def test_installed_bundle_is_preferred_over_a_sibling_checkout(self, tmp_path) -> None:
        """When both layouts exist, the copy shipped with the code wins."""
        bundled = tmp_path / "_bundled_skills" / "graph-mem"
        checkout = tmp_path / "skills" / "graph-mem"
        for d in (bundled, checkout):
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("x", encoding="utf-8")

        assert _resolve_skill_dir([bundled, checkout]) == bundled

    def test_resolution_returns_none_when_nothing_is_present(self, tmp_path) -> None:
        """A missing bundle is reported, not papered over."""
        assert _resolve_skill_dir([tmp_path / "absent"]) is None

    def test_assembled_skill_is_the_full_document_not_the_fallback(self) -> None:
        """The assembled skill must be the real thing.

        Measured: the real document is ~25 000 characters and the fallback is
        ~3 700. The threshold sits well above the fallback so this fails loudly
        if resolution ever breaks again.
        """
        content = _assemble_skill_content("general")
        assert content != _FALLBACK_SKILL
        assert len(content) > 15_000, f"got {len(content)} chars — resolution fell back"

    @pytest.mark.parametrize("domain", ["general", "code", "research"])
    def test_each_domain_overlay_is_actually_applied(self, domain: str) -> None:
        """--domain must change the output, not silently do nothing."""
        content = _assemble_skill_content(domain)
        assert len(content) > 15_000

        others = [d for d in ("general", "code", "research") if d != domain]
        for other in others:
            assert content != _assemble_skill_content(other), (
                f"domain {domain!r} produced identical output to {other!r}"
            )

    def test_unknown_domain_falls_back_to_general(self) -> None:
        """An unrecognised domain is not an error, but it is not silent either."""
        assert _assemble_skill_content("nonsense") == _assemble_skill_content("general")
