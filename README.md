# graphrag-mcp

> Persistent knowledge graph memory for LLM-powered CLI agents

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)

---

## What is this?

LLM-powered coding agents forget everything between sessions. They re-read files, re-discover architecture, and repeat mistakes. **graphrag-mcp** fixes this by giving any MCP-compatible agent a persistent, per-project knowledge graph that combines graph storage, semantic vector search, and multi-hop traversal. It runs entirely locally with zero API keys required — just install and go.

### Why a graph, not just a vector store?

Vector search finds _similar_ things. Graphs find _connected_ things. When an agent asks "what depends on the auth service?", a vector store returns text that mentions auth. A knowledge graph traverses the actual dependency edges and returns every upstream consumer — even ones that never mention "auth" in their description. graphrag-mcp gives you both: vector similarity for fuzzy discovery, graph traversal for structural queries.

## Installation

### Option 1: uvx (Recommended — zero pre-install)

```bash
uvx graphrag-mcp server
```

`uvx` downloads the package into an isolated environment and runs it in one command. Nothing to pre-install beyond [uv](https://docs.astral.sh/uv/).

### Option 2: pip

```bash
pip install graphrag-mcp
graphrag-mcp server
```

### Option 3: From source

```bash
git clone https://github.com/Sathvik-1007/graphrag-mcp
cd graphrag-mcp
pip install -e ".[full,dev]"
graphrag-mcp server
```

### Optional extras

```bash
pip install "graphrag-mcp[embeddings]"   # sentence-transformers for local embeddings
pip install "graphrag-mcp[onnx]"         # ONNX runtime for ~3x faster inference
pip install "graphrag-mcp[full]"         # both of the above
```

## Quick Start

**1. Install the skill for your agent:**

```bash
graphrag-mcp install claude        # Claude Code
graphrag-mcp install opencode      # OpenCode
graphrag-mcp install codex         # Codex CLI
graphrag-mcp install gemini        # Gemini CLI
graphrag-mcp install cursor        # Cursor
graphrag-mcp install windsurf      # Windsurf
graphrag-mcp install amp           # Amp
```

**2. Or configure MCP manually** by adding this to your agent's MCP config:

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

If you installed via pip instead of uvx, use:

```json
{
  "mcpServers": {
    "graphrag-mcp": {
      "command": "graphrag-mcp",
      "args": ["server"]
    }
  }
}
```

That's it. Your agent now has persistent memory.

## Features

graphrag-mcp exposes 12 MCP tools — six for writing, six for reading:

### Write Tools

| Tool | Description |
|------|-------------|
| `add_entities` | Batch-create entities with optional observations |
| `add_relationships` | Create typed, directed edges between entities |
| `add_observations` | Attach factual statements to entities |
| `update_entity` | Modify entity description, properties, or type |
| `delete_entities` | Remove entities with cascade to relationships and observations |
| `merge_entities` | Combine duplicate entities into one |

### Read Tools

| Tool | Description |
|------|-------------|
| `search_nodes` | Hybrid semantic + full-text search with RRF fusion |
| `find_connections` | Multi-hop graph traversal between entities |
| `get_entity` | Full entity details with observations and relationships |
| `read_graph` | Graph statistics overview |
| `get_subgraph` | Extract neighborhood around seed entities |
| `find_paths` | Shortest paths between two entities |

## Architecture

```
Agent (Claude/Codex/Gemini/OpenCode)
  |
  | MCP Protocol (stdio or SSE)
  v
graphrag-mcp server
  |-- Graph Engine ---- entity/relationship/observation CRUD
  |-- Semantic Engine -- sentence-transformers embeddings + hybrid search
  '-- Storage --------- pluggable backend (SQLite default, .graphrag/graph.db)
```

Everything lives in a single SQLite database per project. No external services, no Docker containers, no API keys. The server communicates over MCP's standard stdio transport (SSE also supported) and stores all data in `.graphrag/graph.db` at your project root.

The database file is portable — copy it between machines, check it into version control, or back it up like any other file.

## How It Works

### Entities, Relationships, and Observations

The knowledge graph has three core primitives:

- **Entities** are named nodes with a type and description (e.g., `AuthService`, type `service`).
- **Relationships** are typed, directed edges between entities (e.g., `AuthService --DEPENDS_ON--> Database`).
- **Observations** are factual statements attached to entities (e.g., "Uses bcrypt for password hashing").

### Hybrid Search

`search_nodes` combines three retrieval strategies using Reciprocal Rank Fusion (RRF):

1. **Vector similarity** — cosine distance against sentence-transformer embeddings of entity names, descriptions, and observations.
2. **Full-text search** — SQLite FTS5 with BM25 ranking for keyword matching.
3. **RRF fusion** — merges and re-ranks results from both strategies into a single scored list.

### Multi-Hop Traversal

`find_connections` walks the graph recursively using SQL CTEs, discovering indirect relationships up to a configurable depth. This surfaces connections that no flat search can find — like tracing a function through three layers of abstraction to the database schema it ultimately modifies.

### Entity Resolution

When the agent references an entity by name, graphrag-mcp resolves it through a cascade:

1. **Exact match** — case-sensitive name lookup.
2. **Case-insensitive match** — normalized comparison.
3. **FTS5 match** — full-text search for partial or fuzzy names.
4. **Suggestions** — if nothing matches, return the closest candidates so the agent can self-correct.

## CLI Reference

### Server

```bash
graphrag-mcp server                          # stdio transport (default)
graphrag-mcp server --transport sse          # SSE transport
graphrag-mcp server --db /path/to/graph.db   # custom database path
```

### Skill Installation

```bash
graphrag-mcp install <agent>                 # project-level install
graphrag-mcp install <agent> --global        # global/user-level install
```

Supported agents: `claude`, `opencode`, `codex`, `gemini`, `cursor`, `windsurf`, `amp`.

### Graph Management

```bash
graphrag-mcp init                            # create .graphrag/ directory
graphrag-mcp status                          # print graph statistics
graphrag-mcp status --json                   # graph statistics as JSON
graphrag-mcp export --format json            # export entire graph
graphrag-mcp import graph.json               # import graph from file
graphrag-mcp validate                        # run integrity checks
```

## Configuration

All settings are optional. Defaults work out of the box.

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPHRAG_BACKEND_TYPE` | `sqlite` | Storage backend (`sqlite` by default) |
| `GRAPHRAG_DB_PATH` | `.graphrag/graph.db` | Database file path |
| `GRAPHRAG_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model name |
| `GRAPHRAG_USE_ONNX` | `true` | Use ONNX runtime if available |
| `GRAPHRAG_EMBEDDING_DEVICE` | `cpu` | Inference device (`cpu` or `cuda`) |
| `GRAPHRAG_CACHE_SIZE` | `10000` | Embedding cache max entries |
| `GRAPHRAG_SEARCH_LIMIT` | `10` | Default search result limit |
| `GRAPHRAG_MAX_HOPS` | `4` | Default max traversal depth |
| `GRAPHRAG_LOG_LEVEL` | `WARNING` | Logging verbosity |
| `GRAPHRAG_TRANSPORT` | `stdio` | MCP transport protocol |

## Performance

graphrag-mcp is optimized for low-latency, single-user workloads typical of CLI agent usage:

- **WAL mode + PRAGMA tuning** — concurrent reads with fast writes, optimized journal and cache settings.
- **ONNX-optimized embeddings** — ~3x faster inference than default PyTorch, with automatic fallback.
- **Content-hash embedding cache** — identical text is never embedded twice.
- **Lazy model loading** — the embedding model loads on first search, not on server start.
- **Memory-mapped I/O** — large databases stay fast without consuming proportional RAM.
- **Batch transactions** — bulk operations (e.g., `add_entities` with 50 items) execute in a single transaction.

## Development

### Setup

```bash
git clone https://github.com/Sathvik-1007/graphrag-mcp
cd graphrag-mcp

# Using uv (recommended)
uv venv
uv pip install -e ".[full,dev]"

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[full,dev]"
```

### Running Tests

```bash
pytest                            # all tests
pytest tests/test_graph/          # graph engine tests
pytest tests/test_server/         # MCP server tool tests
pytest tests/test_cli/            # CLI command tests
pytest tests/test_models/         # data model tests
pytest tests/test_semantic/       # search + vector tests
pytest tests/test_storage/        # storage backend tests
pytest tests/test_db/             # database + migration tests
pytest tests/test_utils/          # config, logging, ID generation tests
pytest -x -q                      # stop on first failure, quiet output
```

### Project Structure

```
src/graphrag_mcp/
  __init__.py              # package version
  __main__.py              # python -m graphrag_mcp support
  server.py                # MCP server entry point and 12 tool definitions
  cli/
    main.py                # Click CLI: server, init, status, export, import, validate
    install.py             # Skill installer for 7 agent types
  db/
    connection.py          # Database class (aiosqlite, WAL, PRAGMA tuning)
    schema.py              # Migration runner + version tracking
    migrations/            # Versioned SQL migration scripts
  graph/
    engine.py              # GraphEngine — entity/relationship/observation CRUD
    traversal.py           # GraphTraversal — BFS, path-finding, subgraph extraction
    merge.py               # EntityMerger — entity deduplication and merging
  models/
    entity.py              # Entity dataclass
    relationship.py        # Relationship dataclass
    observation.py         # Observation dataclass
  semantic/
    embeddings.py          # EmbeddingEngine — lazy loading, ONNX, content-hash cache
    search.py              # HybridSearch — vector + FTS5 + RRF fusion
  storage/
    base.py                # StorageBackend ABC (~37 abstract methods)
    sqlite_backend.py      # SQLiteBackend — reference implementation
  utils/
    config.py              # Environment-variable-based Config dataclass
    errors.py              # 14-class exception hierarchy
    ids.py                 # ULID generation
    logging.py             # Structured logging setup
```

## License

[MIT](LICENSE)
