"""Skill installer for graph-mem — writes agent skill/config files.

Usage:
    graph-mem install <agent> [--global]

Supports: claude, opencode, codex, gemini, cursor, windsurf, amp,
          antigravity, copilot, kiro, roocode, qoder, trae, continue,
          codebuddy, droid, kilocode, warp, augment.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from graph_mem.utils.logging import get_logger

log = get_logger("cli.install")

# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

SUPPORTED_AGENTS: tuple[str, ...] = (
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
)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Install location and strategy for a single agent.

    The write method is stored per scope because it genuinely differs per
    scope: codex owns a file in a project but shares ``~/.codex/AGENTS.md``
    globally, and gemini is the mirror image. Storing one method per config
    forced those two into hardcoded branches that contradicted the stored
    value.

    ``doc_url`` is the vendor page that documents ``project_path``. It is
    ``None`` when no authoritative documentation for the path could be found;
    the CLI says so at install time rather than implying the location is
    confirmed.
    """

    project_path: str  # relative to project root
    global_path: str | None  # relative to ~, or None if the agent has no user scope
    project_method: str  # "overwrite" or "section"
    global_method: str | None  # as above; None exactly when global_path is None
    doc_url: str | None  # vendor documentation for the path, or None if unverified


AGENTS: dict[str, AgentConfig] = {
    "claude": AgentConfig(
        project_path=".claude/skills/graph-mem/SKILL.md",
        global_path=".claude/skills/graph-mem/SKILL.md",
        project_method="overwrite",
        global_method="overwrite",
        doc_url=None,
    ),
    "opencode": AgentConfig(
        project_path=".opencode/skills/graph-mem/SKILL.md",
        global_path=".config/opencode/skills/graph-mem/SKILL.md",
        project_method="overwrite",
        global_method="overwrite",
        doc_url=None,
    ),
    "codex": AgentConfig(
        project_path=".agents/skills/graph-mem/SKILL.md",
        global_path=".codex/AGENTS.md",
        project_method="overwrite",
        global_method="section",
        doc_url=None,
    ),
    "gemini": AgentConfig(
        project_path="AGENTS.md",
        global_path=".gemini/skills/graph-mem/SKILL.md",
        project_method="section",
        global_method="overwrite",
        doc_url=None,
    ),
    "cursor": AgentConfig(
        project_path=".cursor/rules/graph-mem.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "windsurf": AgentConfig(
        project_path=".windsurf/rules/graph-mem.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "amp": AgentConfig(
        project_path=".agents/skills/graph-mem/SKILL.md",
        global_path=".config/agents/skills/graph-mem/SKILL.md",
        project_method="overwrite",
        global_method="overwrite",
        doc_url=None,
    ),
    "antigravity": AgentConfig(
        project_path=".agents/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "copilot": AgentConfig(
        # Repository-wide instructions are a single file, always applied.
        # Written as a section so instructions the user already wrote survive.
        project_path=".github/copilot-instructions.md",
        global_path=None,
        project_method="section",
        global_method=None,
        doc_url="https://docs.github.com/en/copilot/how-tos/configure-custom-instructions",
    ),
    "kiro": AgentConfig(
        # Steering is a flat directory of markdown files, not of directories.
        project_path=".kiro/steering/graph-mem.md",
        global_path=".kiro/steering/graph-mem.md",
        project_method="overwrite",
        global_method="overwrite",
        doc_url="https://kiro.dev/docs/steering/",
    ),
    "roocode": AgentConfig(
        # Roo reads .roo/rules/ recursively; the global scope is ~/.roo/rules/.
        project_path=".roo/rules/graph-mem.md",
        global_path=".roo/rules/graph-mem.md",
        project_method="overwrite",
        global_method="overwrite",
        doc_url="https://docs.roocode.com/features/custom-instructions",
    ),
    "qoder": AgentConfig(
        project_path=".qoder/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "trae": AgentConfig(
        project_path=".trae/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "continue": AgentConfig(
        # Continue reads rules from .continue/rules/*.md — there is no skills/.
        project_path=".continue/rules/graph-mem.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url="https://docs.continue.dev/customize/deep-dives/rules",
    ),
    "codebuddy": AgentConfig(
        project_path=".codebuddy/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "droid": AgentConfig(
        project_path=".factory/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "kilocode": AgentConfig(
        # Kilo inherited .kilocode/rules/ from Roo, but its own docs also
        # describe a newer single-file kilo.jsonc config. The two accounts
        # disagree, so this path stays flagged unverified rather than guessed
        # with false confidence.
        project_path=".kilocode/rules/graph-mem.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "warp": AgentConfig(
        project_path=".warp/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
    "augment": AgentConfig(
        project_path=".augment/skills/graph-mem/SKILL.md",
        global_path=None,
        project_method="overwrite",
        global_method=None,
        doc_url=None,
    ),
}

# ---------------------------------------------------------------------------
# Section markers (for agents that share a single file like AGENTS.md)
# ---------------------------------------------------------------------------

_SECTION_BEGIN = "<!-- graph-mem-begin -->"
_SECTION_END = "<!-- graph-mem-end -->"

_SECTION_RE = re.compile(
    re.escape(_SECTION_BEGIN) + r".*?" + re.escape(_SECTION_END),
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Fallback skill content — used when the bundled SKILL.md cannot be loaded
# ---------------------------------------------------------------------------

_FALLBACK_SKILL = """\
# graph-mem: Knowledge Graph Memory

## What This Does

graph-mem gives you persistent, per-project knowledge graph memory through
the Model Context Protocol (MCP). It stores entities, relationships, and
observations in a local SQLite database with semantic search powered by local
embeddings — no API keys needed.

Use it to remember project architecture, decisions, people, code patterns, and
any structured knowledge across sessions.

## MCP Tools Available

### Write Tools
- **add_entities** — Add entities (name, type, description, observations).
  Deduplicates by name.
- **add_relationships** — Create directed edges between entities
  (source → relationship_type → target).
- **add_observations** — Append observations to an existing entity.
- **update_entity** — Update description, properties, or type on an entity.
- **update_relationship** — Modify an edge's weight, type, or properties.
- **update_observation** — Edit observation text (re-embeds automatically).
- **delete_entities** — Remove entities by name, cascading to observations
  and relationships.
- **delete_relationships** — Remove edges between entities.
- **delete_observations** — Remove specific observations by ID.
- **merge_entities** — Merge source entity into target, moving all data.

### Read Tools
- **search_nodes** — Hybrid semantic + full-text search over entities (default 5).
- **search_observations** — Search observation text content.
- **find_connections** — Multi-hop BFS traversal from an entity (default 2 hops).
- **get_entity** — Get entity by name with all observations and relationships.
- **read_graph** — Graph overview: counts, types, most connected, recent updates.
- **get_subgraph** — Extract neighbourhood subgraph around seed entities.
- **find_paths** — Find shortest paths between two entities.
- **list_entities** — Browse entities with pagination (default 50).
- **list_relationships** — List relationships with optional entity/type filter.

### Maintenance Tools
- **graph_health** — Health stats: counts, hotspots, missing descriptions,
  suggested actions. Run at session start.
- **compact_observations** — Atomic observation compaction: delete old observations
  and add merged summaries in one step.
- **suggest_connections** — Find semantically related entities to connect to.
  Essential for large graphs where you can't read everything.
- **audit_graph** — Full quality screening: disconnected nodes, missing
  descriptions/observations/properties, weak links. Returns plain text report.

### Multi-Graph Tools
- **list_graphs** — Show all graphs in .graphmem/.
- **create_graph** — Create a new named graph.
- **switch_graph** — Change active graph.
- **delete_graph** — Remove a graph.

### Dashboard
- **open_dashboard** — Launch interactive graph visualisation in browser.

## When to Use

1. **Session start** — `graph_health` then `search_nodes("current topic")`.
2. **New knowledge surfaces** — Extract entities, observations, relationships.
3. **After adding entities** — `suggest_connections` to find what to link to.
4. **Periodically** — `graph_health` to check for bloat, run `compact_observations`
   on entities with 15+ observations.

## Best Practices

- Search before adding to avoid duplicates.
- One fact per observation, with dates and specifics.
- Use `suggest_connections` after adding entities in large graphs.
- Run `graph_health` at session start.
- Compact observations when entities accumulate >15.

## MCP Configuration

```json
{
  "mcpServers": {
    "graph-mem": {
      "command": "graph-mem",
      "args": ["server"]
    }
  }
}
```

Project-scoped: `"args": ["server", "--project-dir", "/path/to/project"]`
Named graph: `"args": ["server", "--graph", "my-project"]`
"""

# ---------------------------------------------------------------------------
# Skill content loader
# ---------------------------------------------------------------------------

# Files that compose the modular skill, in assembly order.
_SKILL_PARTS: tuple[str, ...] = (
    "SKILL.md",
    "conventions.md",
    "workflows.md",
    "best-practices.md",
)

_VALID_DOMAINS: tuple[str, ...] = ("general", "code", "research")


def _skill_dir_candidates() -> list[Path]:
    """Return the directories that may hold the modular skill files, in order.

    Two layouts have to work, and the previous single-path lookup only worked
    in one of them:

    - Installed: ``pyproject.toml`` force-includes ``skills/graph-mem`` into
      the wheel as ``graph_mem/_bundled_skills/graph-mem``. Nothing read that
      path, so every ``pip install`` silently emitted the short fallback while
      reporting success.
    - Source checkout: ``<repo>/skills/graph-mem``, three levels above this
      file. That is the layout the original ``parents[3]`` assumed, and the
      only one it resolved in — including when the tests ran, which is why
      the tests never caught it.

    Installed comes first: when both exist, the copy shipped with the running
    package is the one that matches its code.
    """
    package_root = Path(__file__).resolve().parent.parent
    return [
        package_root / "_bundled_skills" / "graph-mem",
        package_root.parent.parent / "skills" / "graph-mem",
    ]


def _resolve_skill_dir(candidates: list[Path] | None = None) -> Path | None:
    """Return the first candidate directory that actually holds the skill.

    Args:
        candidates: Directories to try, defaulting to
            :func:`_skill_dir_candidates`. Injectable so a test can exercise
            the installed layout without installing anything.

    Returns:
        The first directory containing ``SKILL.md``, or ``None`` if none does.
    """
    for candidate in candidates if candidates is not None else _skill_dir_candidates():
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None


def _assemble_skill_content(domain: str = "general") -> str:
    """Assemble modular skill content from the bundled skill directory.

    Reads each component file and the requested domain overlay, joining them
    with ``---`` separators.

    Args:
        domain: One of ``"general"``, ``"code"``, or ``"research"``.
            ``None`` and unknown values fall back to ``"general"``.

    Returns:
        The assembled skill document, or :data:`_FALLBACK_SKILL` when the
        bundled files cannot be found. The fallback is a fraction of the real
        content, so it is a degraded result, not an equivalent one.
    """
    if domain not in _VALID_DOMAINS:
        log.warning("Unknown domain %r — falling back to 'general'", domain)
        domain = "general"

    skill_dir = _resolve_skill_dir()
    if skill_dir is None:
        log.warning(
            "Bundled skill files not found — installing the abbreviated fallback. "
            "This is a packaging problem; please report it."
        )
        return _FALLBACK_SKILL

    parts: list[str] = []
    for filename in _SKILL_PARTS:
        filepath = skill_dir / filename
        try:
            text = filepath.read_text(encoding="utf-8")
            if text.strip():
                parts.append(text.strip())
                continue
        except (FileNotFoundError, OSError) as exc:
            log.debug("Could not load %s: %s — falling back", filename, exc)

        # Any missing file triggers full fallback.
        return _FALLBACK_SKILL

    # Load domain overlay.
    domain_path = skill_dir / "domains" / f"{domain}.md"
    try:
        domain_text = domain_path.read_text(encoding="utf-8")
        if domain_text.strip():
            parts.append(domain_text.strip())
    except (FileNotFoundError, OSError) as exc:
        log.debug("Could not load domain overlay %s: %s — skipping", domain, exc)

    return "\n\n---\n\n".join(parts) + "\n"


def _load_skill_content() -> str:
    """Load the assembled skill content with the default (general) domain.

    Retained for backward compatibility.
    """
    return _assemble_skill_content(domain="general")


# ---------------------------------------------------------------------------
# Atomic file writer
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically using temp file + rename.

    Ensures that a crash or power loss never leaves a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError as cleanup_exc:
            log.debug("Failed to clean up temp file %s: %s", tmp_path, cleanup_exc)
        raise


# ---------------------------------------------------------------------------
# Low-level writers
# ---------------------------------------------------------------------------


def _write_overwrite(path: Path, content: str) -> None:
    """Write *content* to a dedicated file, creating directories as needed."""
    _atomic_write(path, content)


def _write_section(path: Path, content: str) -> None:
    """Write *content* between section markers in a shared file.

    If the markers already exist the section is replaced in-place; otherwise it
    is appended to the end of the file.
    """
    section = f"{_SECTION_BEGIN}\n{content}\n{_SECTION_END}"

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if _SECTION_BEGIN in existing and _SECTION_END in existing:
            new_content = _SECTION_RE.sub(section, existing, count=1)
            _atomic_write(path, new_content)
            return
        # Markers absent — append.
        separator = (
            "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        )
        _atomic_write(path, f"{existing}{separator}{section}\n")
    else:
        _atomic_write(path, f"{section}\n")


# ---------------------------------------------------------------------------
# Method resolution helpers
# ---------------------------------------------------------------------------


def _effective_method(agent: str, scope: str) -> str:
    """Return the write method for *agent* in the given *scope*.

    The method comes straight from :class:`AgentConfig`, which stores one per
    scope. Scope matters because an agent can own a dedicated file in one
    scope and share a file in the other (codex, gemini).

    Raises:
        ValueError: If *scope* is ``"global"`` and the agent has no global
            install location.
    """
    cfg = AGENTS[agent]
    if scope == "project":
        return cfg.project_method
    if cfg.global_method is None:
        msg = f"Agent {agent!r} does not support global installation"
        raise ValueError(msg)
    return cfg.global_method


def _resolve_target(agent: str, scope: str, project_dir: Path) -> Path:
    """Return the absolute target path for the skill file."""
    cfg = AGENTS[agent]

    if scope == "global":
        if cfg.global_path is None:
            msg = f"Agent {agent!r} does not support global installation"
            raise ValueError(msg)
        return Path.home().resolve() / cfg.global_path

    return project_dir.resolve() / cfg.project_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_skill(
    agent: str,
    *,
    scope: str = "project",
    project_dir: Path | None = None,
    domain: str = "general",
) -> Path:
    """Install the graph-mem skill for *agent*.

    Args:
        agent: One of :data:`SUPPORTED_AGENTS`.
        scope: ``"project"`` (current working directory) or ``"global"`` (user
            home).
        project_dir: Override the project directory (defaults to ``Path.cwd()``).
        domain: Domain overlay to include. One of ``"general"``, ``"code"``,
            or ``"research"``.

    Returns:
        Absolute :class:`~pathlib.Path` to the installed skill file.

    Raises:
        ValueError: If *agent* is not supported or *scope* is invalid.
        ValueError: If global install is not available for this agent.
    """
    agent = agent.lower().strip()
    if agent not in AGENTS:
        msg = f"Unsupported agent {agent!r}. Choose from: {', '.join(SUPPORTED_AGENTS)}"
        raise ValueError(msg)

    if scope not in ("project", "global"):
        msg = f"Invalid scope {scope!r}. Choose 'project' or 'global'."
        raise ValueError(msg)

    if project_dir is None:
        project_dir = Path.cwd()

    target = _resolve_target(agent, scope, project_dir)
    content = _assemble_skill_content(domain)
    method = _effective_method(agent, scope)

    if method == "section":
        _write_section(target, content)
    else:
        _write_overwrite(target, content)

    return target


def uninstall_skill(
    agent: str,
    *,
    scope: str = "project",
    project_dir: Path | None = None,
) -> bool:
    """Remove the graph-mem skill for *agent*.

    Returns ``True`` if something was removed, ``False`` if nothing was found.
    """
    agent = agent.lower().strip()
    if agent not in AGENTS:
        msg = f"Unsupported agent {agent!r}. Choose from: {', '.join(SUPPORTED_AGENTS)}"
        raise ValueError(msg)

    if scope not in ("project", "global"):
        msg = f"Invalid scope {scope!r}. Choose 'project' or 'global'."
        raise ValueError(msg)

    if project_dir is None:
        project_dir = Path.cwd()

    try:
        target = _resolve_target(agent, scope, project_dir)
    except ValueError:
        return False

    if not target.exists():
        return False

    method = _effective_method(agent, scope)

    if method == "section":
        existing = target.read_text(encoding="utf-8")
        if _SECTION_BEGIN not in existing:
            return False
        cleaned = _SECTION_RE.sub("", existing).strip()
        if cleaned:
            _atomic_write(target, cleaned + "\n")
        else:
            # File is now empty — remove it.
            target.unlink()
        return True

    # Overwrite method — just delete the file.
    target.unlink()
    # Clean up empty parent dirs up to (but not including) the project/home root.
    boundary = Path.home().resolve() if scope == "global" else project_dir.resolve()
    _remove_empty_parents(target.parent, boundary)
    return True


def _remove_empty_parents(directory: Path, boundary: Path) -> None:
    """Remove empty directories upward, stopping at *boundary*."""
    current = directory.resolve()
    boundary = boundary.resolve()
    while current != boundary and current.is_dir():
        try:
            current.rmdir()  # only succeeds if empty
        except OSError:
            break  # directory not empty or not removable — stop climbing
        current = current.parent
