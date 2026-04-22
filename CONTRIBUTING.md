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
4. **Run tests** — all 520+ tests must pass
5. **Run linting** — code must pass `ruff check` and `ruff format --check`
6. **Submit a pull request** against `master`

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. The configuration lives in `pyproject.toml`:

- **Line length:** 100 characters
- **Target:** Python 3.10+
- **Format:** Ruff formatter (run `ruff format src/` to auto-format)
- **Lint rules:** E, F, W, I (isort), N (naming), UP (pyupgrade), B (bugbear), A (builtins), SIM (simplify), TCH (type-checking), RUF

Before submitting:

```bash
ruff check src/           # lint
ruff format --check src/  # format check
pytest tests/ -x -q       # tests
```

### Conventions

- **Docstrings:** Every public function and class gets a docstring. First line is imperative mood ("Add entities..." not "Adds entities...").
- **Type hints:** All function signatures are typed. We use `from __future__ import annotations` for modern syntax.
- **Imports:** stdlib → third-party → first-party, managed by isort via Ruff.
- **Error handling:** Domain errors inherit from `GraphMemError`. MCP tool functions catch and wrap errors into structured dicts at the boundary.
- **Naming:** `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- **Tests:** Every new feature or bug fix needs tests. Tests live in `tests/` mirroring the `src/graph_mem/` structure.

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

- **Storage-agnostic engine:** `GraphEngine` talks to `StorageBackend`, not directly to SQLite. This allows future backends.
- **Embedding engine is lazy:** Model loads on first use, not at import time. MCP startup stays under 2 seconds.
- **Vec tables don't CASCADE:** `sqlite-vec` virtual tables require manual embedding cleanup on entity/observation deletion.
- **Transaction nesting:** Uses SQLite savepoints. Outer = BEGIN/COMMIT, inner = SAVEPOINT/RELEASE.
- **Hybrid search:** Vector similarity (cosine) + FTS5 keyword matching + Reciprocal Rank Fusion for scoring.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
