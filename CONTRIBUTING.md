# Contributing to Graph-Mem MCP

Everyone is welcome to contribute. Whether you're fixing a typo, reporting a bug, or building a new feature — contributions of all sizes are appreciated.

## Getting Started

```bash
git clone https://github.com/Sathvik-1007/GraphMem-MCP
cd GraphMem-MCP
pip install -e ".[full,dev]"
pytest
```

## Development Workflow

1. **Fork and clone** the repository
2. **Create a branch** from `master` for your work
3. **Make your changes** — follow the code style below
4. **Run the gates** — all 894 tests, plus mypy and ruff, must pass
5. **Run linting** — code must pass `ruff check` and `ruff format --check`
6. **Submit a pull request** against `master`

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. The configuration lives in `pyproject.toml`:

- **Line length:** 100 characters
- **Target:** Python 3.10+
- **Format:** Ruff formatter (run `ruff format src/` to auto-format)
- **Lint rules:** E, F, W, I (isort), N (naming), UP (pyupgrade), B (bugbear), A (builtins), SIM (simplify), TCH (type-checking), RUF

Before submitting — these are exactly what CI runs, so a clean run here is a
clean run there:

```bash
ruff check src/ tests/ benchmarks/          # lint
ruff format --check src/ tests/ benchmarks/ # format check
mypy src/graph_mem                          # strict type check — must be clean
pytest                                      # tests; warnings are errors
```

`pytest` needs no `PYTHONPATH`: `pythonpath = ["src"]` is set in
`pyproject.toml`, so a fresh clone runs the suite without installing anything.

If you touch `ui-frontend/`, also run `npm run build` in that directory and
commit the result. The bundle is built into `src/graph_mem/ui/frontend/` and
CI fails if the committed output differs from a fresh build.

### Conventions

- **Docstrings:** Every public function and class gets a docstring. First line is imperative mood ("Add entities..." not "Adds entities...").
- **Type hints:** All function signatures are typed. We use `from __future__ import annotations` for modern syntax.
- **Imports:** stdlib → third-party → first-party, managed by isort via Ruff.
- **Error handling:** Domain errors inherit from `GraphMemError`. MCP tool functions catch and wrap errors into structured dicts at the boundary.
- **Naming:** `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- **Tests:** Every new feature or bug fix needs tests, in `tests/` mirroring
  `src/graph_mem/`. A bug-fix test must **fail without the fix** — check that
  by reverting your change and watching it go red. A test that passes either
  way documents nothing.
- **Comments must be true.** If you change behaviour, change the prose
  describing it in the same commit. A stale comment is worse than none: the
  next reader trusts it.

## Project Structure

```
src/graph_mem/
├── tools/           # MCP tool functions (the API surface)
├── graph/           # Graph engine, traversal, merge
├── semantic/        # Embeddings + hybrid search
├── storage/         # SQLite storage backend
├── db/              # Database connection + schema migrations
├── models/          # Data models (Entity, Relationship, Observation)
├── ui/              # React dashboard + aiohttp server
├── cli/             # Click CLI commands + skill installer
└── utils/           # Config, logging, errors, ID generation
```

## Running Tests

```bash
pytest                            # all tests
pytest tests/test_server/         # MCP tool tests
pytest tests/test_graph/          # graph engine tests
pytest tests/test_ui/             # UI route tests
pytest tests/test_storage/        # storage backend tests
pytest -x -q --tb=short           # quick mode — stop on first failure
```

## Adding an Agent

`graph-mem install <agent>` writes the skill file where that agent reads its
instructions. Thirteen agents are supported, and **every one of them cites the
vendor documentation for its path**. That rule is enforced by the test suite,
and it exists because a guessed path is worse than no support: the install
prints success, writes a file, and the agent never reads it. Six agents were
removed from this project for exactly that reason.

So adding one starts with research, not code.

### 1. Find out where the agent actually reads from

Locate the vendor's own documentation — not a blog post, not an inference from
a similar tool. You need to answer four questions:

- What is the **project-level** path, exactly, including the file extension?
  (Cursor loads `.cursor/rules/*.mdc` and *silently ignores* `.md` in the same
  directory. That distinction cost this project a broken integration.)
- Is there a **user-level** path, and what is it? It is often not a mirror of
  the project path — Windsurf's is
  `~/.codeium/windsurf/memories/global_rules.md`, nothing like its project
  path.
- Is the target file **dedicated to us, or shared**? `AGENTS.md`, `GEMINI.md`,
  and `.github/copilot-instructions.md` all hold content the user wrote.
- Does the agent read the file **automatically**, or only when invoked?

If you cannot find documentation, stop. Open an issue instead — that is a more
useful contribution than a plausible guess.

### 2. Add the entry

In `src/graph_mem/cli/install.py`:

```python
SUPPORTED_AGENTS: tuple[str, ...] = (
    ...,
    "youragent",
)

AGENTS: dict[str, AgentConfig] = {
    ...,
    "youragent": AgentConfig(
        # A note here if the path is surprising — future readers will wonder.
        project_path=".youragent/rules/graph-mem.md",
        global_path=".config/youragent/rules/graph-mem.md",  # or None
        project_method="overwrite",
        global_method="overwrite",                            # None iff global_path is None
        doc_url="https://docs.youragent.dev/rules",
    ),
}
```

Field by field:

| Field | Meaning |
|-------|---------|
| `project_path` | Relative to the project root. Never absolute, never contains `..`. |
| `global_path` | Relative to `~`. `None` if the agent has no user scope. |
| `project_method` | `"overwrite"` for a file that is ours; `"section"` for a shared one. |
| `global_method` | Same, for the user scope. Must be `None` exactly when `global_path` is. |
| `doc_url` | The vendor page you found in step 1. Required. |

**Choosing the method.** `"overwrite"` replaces the whole file, so use it only
when the path is ours alone — a dedicated `graph-mem.md` or
`graph-mem/SKILL.md`. `"section"` writes between `<!-- graph-mem-begin -->` and
`<!-- graph-mem-end -->` markers, leaving everything else in the file intact and
replacing the section on re-install. Use it for any shared file. Getting this
wrong destroys the user's own instructions, so when in doubt, use `"section"`.

### 3. Add the tests

The registry tests in `tests/test_cli/test_install.py` cover your agent
automatically — citation present, no path escapes its root, global fields
agree, methods are known values. Two things you add by hand:

- the agent's name in `test_supported_agents_list`
- an install test asserting the exact resolved path, following the existing
  `test_install_*_project` pattern

If the agent uses `"section"`, also cover that installing twice leaves one
section and that unrelated content in the file survives.

### 4. Update the docs

The agent table in the README lists every path with its citation. Keep it in
step — it is the table users read before trusting the installer.

---

## Reporting Bugs

Open an issue at [GitHub Issues](https://github.com/Sathvik-1007/GraphMem-MCP/issues) with:

1. What you expected to happen
2. What actually happened
3. Steps to reproduce
4. Python version and OS

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include tests for new functionality
- Update docstrings if you change function signatures
- Don't break existing tests
- The CI pipeline (lint → test → build) must pass

## Architecture Notes

Full reasoning, including the decisions that were reversed and why, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The short version:

- **One direction of dependency:** `tools/` → `graph/`+`semantic/` → `storage/`
  → `db/`. `graph/` and `semantic/` never write SQL or open a connection; they
  call methods on the backend.
- **One storage backend, and it is the interface.** There is no abstract base
  class and no registry — see ARCHITECTURE.md for what was there and why it
  could not work.
- **Embedding engine is lazy:** the model loads on first use, not at import,
  and inference runs off the event loop. MCP startup stays under 2 seconds.
- **Vec tables don't CASCADE:** `sqlite-vec` virtual tables need explicit
  embedding cleanup on entity and observation deletion.
- **Transactions:** one connection, one write lock held for the outermost
  transaction, nesting tracked per task with savepoints. `BEGIN IMMEDIATE`, not
  `BEGIN`.
- **Traversal is breadth-first in Python,** not a recursive CTE — a CTE cannot
  express a global visited set, and without one it enumerates every simple path.
- **Hybrid search:** cosine vector similarity + FTS5 + Reciprocal Rank Fusion.
  Scores are raw RRF, deliberately not normalised to 0-1.
- **The MCP boundary is a trust boundary.** Tool arguments come from a language
  model and are validated as hostile input. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
