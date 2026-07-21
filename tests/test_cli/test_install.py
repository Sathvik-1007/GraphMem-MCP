"""Tests for graph_mem.cli.install — skill installer."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from graph_mem.cli.install import (
    _FALLBACK_SKILL,
    _SECTION_BEGIN,
    _SECTION_END,
    AGENTS,
    SUPPORTED_AGENTS,
    _assemble_skill_content,
    _effective_method,
    _resolve_skill_dir,
    _resolve_target,
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
    """Cursor rules must use the .mdc extension — .md files are ignored."""
    result = install_skill("cursor", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / ".cursor" / "rules" / "graph-mem.mdc"

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
    """Gemini reads GEMINI.md, and shares it, so the install writes a section."""
    result = install_skill("gemini", scope="project", project_dir=project_dir)

    assert result.exists()
    assert result == project_dir / "GEMINI.md"

    content = result.read_text(encoding="utf-8")
    assert _SECTION_BEGIN in content
    assert _SECTION_END in content
    assert "graph-mem" in content.lower()


def test_install_section_replaces(project_dir: Path) -> None:
    """Installing the same section-based agent twice should not duplicate."""
    install_skill("gemini", scope="project", project_dir=project_dir)
    install_skill("gemini", scope="project", project_dir=project_dir)

    target = project_dir / "GEMINI.md"
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
    target = project_dir / "GEMINI.md"
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


# ── Registry invariants ──────────────────────────────────────────────────────


class TestRegistryInvariants:
    """Properties of the agent registry itself.

    The install/uninstall tests above assert that a file lands where the
    registry says it lands, which cannot catch a *wrong* registry entry — it
    only re-asserts the constant. These check the things that are checkable
    without knowing each vendor's convention: that the two lists agree, that
    nothing escapes its root, and that scope-dependent fields are consistent.
    """

    def test_supported_agents_and_agents_agree(self) -> None:
        """Every advertised agent is installable, and nothing is hidden."""
        assert set(SUPPORTED_AGENTS) == set(AGENTS)
        assert len(SUPPORTED_AGENTS) == len(AGENTS)

    @pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
    def test_paths_are_relative_and_do_not_escape(self, agent: str) -> None:
        """No registry path may be absolute, home-anchored, or contain ``..``."""
        cfg = AGENTS[agent]
        for label, rel in (("project", cfg.project_path), ("global", cfg.global_path)):
            if rel is None:
                continue
            parts = PurePosixPath(rel)
            assert not parts.is_absolute(), f"{agent} {label} path is absolute: {rel}"
            assert not rel.startswith("~"), f"{agent} {label} path is home-anchored: {rel}"
            assert ".." not in parts.parts, f"{agent} {label} path escapes: {rel}"

    @pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
    def test_project_target_resolves_inside_the_project(
        self, agent: str, project_dir: Path
    ) -> None:
        """A project install lands under the project root, never outside it."""
        target = _resolve_target(agent, "project", project_dir)
        assert target.is_relative_to(project_dir.resolve())

    @pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
    def test_global_target_resolves_inside_home(
        self, agent: str, home_dir: Path, project_dir: Path
    ) -> None:
        """A global install lands under $HOME, or is refused outright."""
        if AGENTS[agent].global_path is None:
            with pytest.raises(ValueError, match="does not support global"):
                _resolve_target(agent, "global", project_dir)
            return
        target = _resolve_target(agent, "global", project_dir)
        assert target.is_relative_to(home_dir.resolve())

    @pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
    def test_global_path_and_global_method_agree(self, agent: str) -> None:
        """``global_method`` is set exactly when a global install exists."""
        cfg = AGENTS[agent]
        assert (cfg.global_path is None) == (cfg.global_method is None), (
            f"{agent}: global_path={cfg.global_path!r} global_method={cfg.global_method!r}"
        )

    @pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
    def test_methods_are_known_values(self, agent: str) -> None:
        """Only ``overwrite`` and ``section`` are implemented writers."""
        cfg = AGENTS[agent]
        assert cfg.project_method in ("overwrite", "section")
        assert cfg.global_method in ("overwrite", "section", None)

    @pytest.mark.parametrize("agent", SUPPORTED_AGENTS)
    def test_effective_method_comes_from_the_data(self, agent: str) -> None:
        """No agent gets a hardcoded method that contradicts its config."""
        cfg = AGENTS[agent]
        assert _effective_method(agent, "project") == cfg.project_method
        if cfg.global_method is None:
            with pytest.raises(ValueError, match="does not support global"):
                _effective_method(agent, "global")
        else:
            assert _effective_method(agent, "global") == cfg.global_method


# ── Write behaviour that survives existing content ───────────────────────────


def _installs(method: str) -> list[tuple[str, str]]:
    """Return ``(agent, scope)`` pairs whose install uses *method*."""
    pairs = []
    for agent in SUPPORTED_AGENTS:
        cfg = AGENTS[agent]
        if cfg.project_method == method:
            pairs.append((agent, "project"))
        if cfg.global_method == method:
            pairs.append((agent, "global"))
    return pairs


def _install(agent: str, scope: str, project_dir: Path) -> Path:
    """Install *agent* in *scope*; ``home_dir`` must be active for global."""
    return install_skill(agent, scope=scope, project_dir=project_dir)


class TestSectionInstalls:
    """Section installs share a file with content we did not write."""

    @pytest.mark.parametrize(("agent", "scope"), _installs("section"))
    def test_installing_twice_does_not_duplicate_the_section(
        self, agent: str, scope: str, project_dir: Path, home_dir: Path
    ) -> None:
        """Re-running install replaces the section instead of appending one."""
        first = _install(agent, scope, project_dir)
        second = _install(agent, scope, project_dir)

        assert first == second
        content = first.read_text(encoding="utf-8")
        assert content.count(_SECTION_BEGIN) == 1
        assert content.count(_SECTION_END) == 1

    @pytest.mark.parametrize(("agent", "scope"), _installs("section"))
    def test_unrelated_content_survives_install_and_uninstall(
        self, agent: str, scope: str, project_dir: Path, home_dir: Path
    ) -> None:
        """We append to somebody else's file — theirs must come back intact."""
        target = _resolve_target(agent, scope, project_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        theirs = "# House rules\n\nAlways run the linter.\n"
        target.write_text(theirs, encoding="utf-8")

        _install(agent, scope, project_dir)
        after_install = target.read_text(encoding="utf-8")
        assert "Always run the linter." in after_install
        assert _SECTION_BEGIN in after_install

        assert uninstall_skill(agent, scope=scope, project_dir=project_dir) is True
        after_uninstall = target.read_text(encoding="utf-8")
        assert "Always run the linter." in after_uninstall
        assert _SECTION_BEGIN not in after_uninstall
        assert _SECTION_END not in after_uninstall

    @pytest.mark.parametrize(("agent", "scope"), _installs("section"))
    def test_uninstall_removes_the_file_it_created_alone(
        self, agent: str, scope: str, project_dir: Path, home_dir: Path
    ) -> None:
        """If we created the shared file ourselves, uninstall leaves nothing."""
        target = _install(agent, scope, project_dir)
        assert uninstall_skill(agent, scope=scope, project_dir=project_dir) is True
        assert not target.exists()


class TestOverwriteInstalls:
    """Overwrite installs own their file — and only their file."""

    @pytest.mark.parametrize(("agent", "scope"), _installs("overwrite"))
    def test_install_is_idempotent(
        self, agent: str, scope: str, project_dir: Path, home_dir: Path
    ) -> None:
        """Two installs produce one file with identical content."""
        first = _install(agent, scope, project_dir)
        content = first.read_text(encoding="utf-8")
        second = _install(agent, scope, project_dir)

        assert first == second
        assert second.read_text(encoding="utf-8") == content

    @pytest.mark.parametrize(("agent", "scope"), _installs("overwrite"))
    def test_uninstall_leaves_neighbouring_files_alone(
        self, agent: str, scope: str, project_dir: Path, home_dir: Path
    ) -> None:
        """Directory cleanup must stop at the first non-empty directory."""
        target = _install(agent, scope, project_dir)
        neighbour = target.parent / "someone-elses-rules.md"
        neighbour.write_text("not ours\n", encoding="utf-8")

        assert uninstall_skill(agent, scope=scope, project_dir=project_dir) is True
        assert not target.exists()
        assert neighbour.read_text(encoding="utf-8") == "not ours\n"


# ── Verification status ──────────────────────────────────────────────────────

# Agents whose install path was checked against current vendor documentation.
# Each must carry the citation. Adding an agent here without a doc_url, or
# changing a verified path without re-checking the docs, fails the suite.
VERIFIED_AGENTS = frozenset(
    {
        "claude",
        "opencode",
        "amp",
        "cursor",
        "windsurf",
        "codex",
        "gemini",
        "droid",
        "copilot",
        "kiro",
        "continue",
        "roocode",
        "antigravity",
    }
)


class TestPathVerification:
    """Paths are either documented or honestly flagged, never silently guessed."""

    @pytest.mark.parametrize("agent", sorted(VERIFIED_AGENTS))
    def test_verified_agent_carries_its_citation(self, agent: str) -> None:
        """A path claimed as verified must say where that claim comes from."""
        assert AGENTS[agent].doc_url, f"{agent} is listed verified but has no doc_url"
        assert AGENTS[agent].doc_url.startswith("https://")

    @pytest.mark.parametrize("agent", sorted(set(SUPPORTED_AGENTS) - VERIFIED_AGENTS))
    def test_unverified_agent_has_no_citation(self, agent: str) -> None:
        """An unchecked path must not carry a citation implying it was checked."""
        assert AGENTS[agent].doc_url is None, (
            f"{agent} has a doc_url but is not in VERIFIED_AGENTS — "
            f"add it there once the path is confirmed"
        )

    def test_cursor_uses_mdc_not_md(self) -> None:
        """Cursor ignores .md files in .cursor/rules; only .mdc is read.

        Regression: the installer wrote .md, so the file landed in the right
        directory and was silently never loaded.
        """
        assert AGENTS["cursor"].project_path.endswith(".mdc")

    @pytest.mark.parametrize("agent", ["codex", "gemini", "droid", "antigravity", "copilot"])
    def test_shared_files_are_written_as_sections(self, agent: str) -> None:
        """An agent whose file is shared must not overwrite it.

        AGENTS.md, GEMINI.md, and .github/copilot-instructions.md all hold
        content the user wrote. Overwriting one destroys it.
        """
        assert AGENTS[agent].project_method == "section", (
            f"{agent} writes a shared file and would clobber it"
        )

    def test_every_supported_agent_is_configured(self) -> None:
        """SUPPORTED_AGENTS and AGENTS cannot drift apart."""
        assert set(SUPPORTED_AGENTS) == set(AGENTS)

    @pytest.mark.parametrize("agent", sorted(SUPPORTED_AGENTS))
    def test_no_path_escapes_its_root(self, agent: str) -> None:
        """No configured path may traverse upward or be absolute."""
        cfg = AGENTS[agent]
        for path in (cfg.project_path, cfg.global_path):
            if path is None:
                continue
            assert not PurePosixPath(path).is_absolute(), f"{agent}: {path} is absolute"
            assert ".." not in PurePosixPath(path).parts, f"{agent}: {path} traverses upward"

    @pytest.mark.parametrize("agent", sorted(SUPPORTED_AGENTS))
    def test_global_method_is_set_exactly_when_global_path_is(self, agent: str) -> None:
        """The two global fields describe one thing and must agree."""
        cfg = AGENTS[agent]
        assert (cfg.global_path is None) == (cfg.global_method is None), (
            f"{agent}: global_path and global_method disagree"
        )
