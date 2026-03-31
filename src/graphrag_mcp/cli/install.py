"""Skill installer for graphrag-mcp — writes agent skill/config files.

Usage:
    graphrag-mcp install <agent> [--global]

Supports: claude, opencode, codex, gemini, cursor, windsurf, amp.
"""

from __future__ import annotations

import importlib.resources
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from graphrag_mcp.utils.logging import get_logger

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
)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Install location and strategy for a single agent."""

    project_path: str  # relative to project root
    global_path: str | None  # relative to ~, or None if unsupported
    method: str  # "overwrite" or "section"


AGENTS: dict[str, AgentConfig] = {
    "claude": AgentConfig(
        project_path=".claude/skills/graphrag-mcp/SKILL.md",
        global_path=".claude/skills/graphrag-mcp/SKILL.md",
        method="overwrite",
    ),
    "opencode": AgentConfig(
        project_path=".opencode/skills/graphrag-mcp/SKILL.md",
        global_path=".config/opencode/skills/graphrag-mcp/SKILL.md",
        method="overwrite",
    ),
    "codex": AgentConfig(
        project_path=".agents/skills/graphrag-mcp/SKILL.md",
        global_path=".codex/AGENTS.md",
        method="overwrite",  # project=overwrite, global=section
    ),
    "gemini": AgentConfig(
        project_path="AGENTS.md",
        global_path=".gemini/skills/graphrag-mcp/SKILL.md",
        method="section",  # project=section, global=overwrite
    ),
    "cursor": AgentConfig(
        project_path=".cursor/rules/graphrag-mcp.md",
        global_path=None,
        method="overwrite",
    ),
    "windsurf": AgentConfig(
        project_path="AGENTS.md",
        global_path=None,
        method="section",
    ),
    "amp": AgentConfig(
        project_path=".agents/skills/graphrag-mcp/SKILL.md",
        global_path=".config/agents/skills/graphrag-mcp/SKILL.md",
        method="overwrite",
    ),
}

# ---------------------------------------------------------------------------
# Section markers (for agents that share a single file like AGENTS.md)
# ---------------------------------------------------------------------------

_SECTION_BEGIN = "<!-- graphrag-mcp-begin -->"
_SECTION_END = "<!-- graphrag-mcp-end -->"

_SECTION_RE = re.compile(
    re.escape(_SECTION_BEGIN) + r".*?" + re.escape(_SECTION_END),
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Fallback skill content — used when the bundled SKILL.md cannot be loaded
# ---------------------------------------------------------------------------

_FALLBACK_SKILL = """\
# graphrag-mcp: Knowledge Graph Memory

## What This Does

graphrag-mcp gives you persistent, per-project knowledge graph memory through
the Model Context Protocol (MCP). It stores entities, relationships, and
observations in a local SQLite database with semantic search powered by local
embeddings — no API keys needed for the embedding step.

Use it to remember project architecture, decisions, people, code patterns, and
any structured knowledge across sessions.

## MCP Tools Available

### Write Tools
- **add_entities** — Add one or more entities (name, type, observations) to the
  knowledge graph. Deduplicates by name.
- **add_relationships** — Create directed relationships between existing entities
  (source → relationship_type → target).
- **add_observations** — Append observations to an existing entity.
- **update_entity** — Update fields (description, properties, type) on an
  existing entity.
- **delete_entities** — Remove one or more entities by name, cascading to their
  observations and relationships.
- **merge_entities** — Merge a source entity into a target, moving all
  observations and relationships.

### Read Tools
- **search_nodes** — Hybrid semantic + full-text search over entities using
  natural-language queries.  Returns the most relevant entities with their
  relationships.
- **find_connections** — Discover entities connected to a given entity via
  multi-hop graph traversal (BFS).
- **get_entity** — Retrieve a single entity by exact name, including all its
  observations and relationships.
- **read_graph** — Get an overview of the knowledge graph: counts, type
  distributions, most connected entities, and recent updates.
- **get_subgraph** — Extract a neighbourhood subgraph around one or more seed
  entities within a given radius.
- **find_paths** — Find shortest paths between two entities in the graph.

## When to Use

1. **Session start** — Search for relevant context before diving into the task.
   `search_nodes("current project architecture")` gives you a warm start.
2. **Discovering important concepts** — When you encounter key architectural
   decisions, domain terms, or people, add them as entities.
3. **Establishing relationships** — Link entities together to build a navigable
   graph (e.g., `"AuthService" --uses--> "JWT"`).
4. **Accumulating knowledge** — Add observations to existing entities as you
   learn more, rather than creating duplicates.

## Best Practices

- Use consistent entity names (PascalCase for types, exact names for people).
- Prefer specific entity types: `Person`, `Service`, `Decision`, `Pattern`,
  `Library`, `Endpoint`, `Config`, rather than generic `Thing`.
- Keep observations atomic — one fact per observation.
- Search before adding to avoid duplicates.

## MCP Configuration

Add to your MCP config (claude_desktop_config.json, .mcp.json, etc.):

```json
{
  "mcpServers": {
    "graphrag-mcp": {
      "command": "uvx",
      "args": ["graphrag-mcp", "server"]
    }
  }
}
```

Or with an explicit project directory:

```json
{
  "mcpServers": {
    "graphrag-mcp": {
      "command": "uvx",
      "args": ["graphrag-mcp", "server", "--project-dir", "/path/to/project"]
    }
  }
}
```
"""

# ---------------------------------------------------------------------------
# Skill content loader
# ---------------------------------------------------------------------------


def _load_skill_content() -> str:
    """Load the bundled SKILL.md content.

    Resolution order:
    1. importlib.resources (works in installed packages)
    2. Filesystem relative to this source file (works in editable installs)
    3. Hard-coded fallback (always works)
    """
    # 1. Package resources (Python 3.10+)
    try:
        ref = importlib.resources.files("graphrag_mcp") / ".." / ".." / "skills" / "SKILL.md"
        text = ref.read_text(encoding="utf-8")
        if text.strip():
            return text
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        log.debug("Could not load SKILL.md via importlib.resources: %s — trying filesystem", exc)

    # 2. Filesystem fallback (editable / dev installs)
    skill_path = Path(__file__).resolve().parents[3] / "skills" / "SKILL.md"
    if skill_path.exists():
        text = skill_path.read_text(encoding="utf-8")
        if text.strip():
            return text

    # 3. Hard-coded fallback
    return _FALLBACK_SKILL


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

    Most agents use the same method regardless of scope, but two are special:
    - codex: project → overwrite, global → section
    - gemini: project → section,   global → overwrite
    """
    if agent == "codex":
        return "overwrite" if scope == "project" else "section"
    if agent == "gemini":
        return "section" if scope == "project" else "overwrite"
    return AGENTS[agent].method


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
) -> Path:
    """Install the graphrag-mcp skill for *agent*.

    Args:
        agent: One of :data:`SUPPORTED_AGENTS`.
        scope: ``"project"`` (current working directory) or ``"global"`` (user
            home).
        project_dir: Override the project directory (defaults to ``Path.cwd()``).

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
    content = _load_skill_content()
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
    """Remove the graphrag-mcp skill for *agent*.

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
