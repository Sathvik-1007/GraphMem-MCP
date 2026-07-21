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
