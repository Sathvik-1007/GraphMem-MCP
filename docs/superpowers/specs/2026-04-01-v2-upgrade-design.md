# graphrag-mcp v2 Upgrade Design Spec

**Date**: 2026-04-01
**Status**: Design
**Quality bar**: Publishable OSS, zero errors, no hardcoding, no simplification, no placeholders

---

## 1. Overview

This spec covers four upgrade areas for graphrag-mcp v2:

1. **Domain-Agnostic Skill System** — Restructure the monolithic 306-line `skills/SKILL.md` into a plugin architecture with domain overlays
2. **Search Algorithm Optimization** — Fix 6 concrete bugs/inefficiencies in the search pipeline
3. **Visualization Frontend** — React + Vite + Tailwind SPA served via new `graphrag-mcp ui` CLI command
4. **Desloppify** — Enforce zero-tolerance quality across the codebase

All 279 existing tests must continue to pass. Every new component gets its own test coverage.

---

## 2. Domain-Agnostic Skill System

### 2.1 Problem

The current `skills/SKILL.md` (306 lines) is a monolithic file that mixes:

- Core graph-RAG concepts (entity/observation/relationship lifecycle)
- Domain-specific heuristics (code-oriented entity types, naming conventions)
- Agent-specific formatting (tool name references, prompt patterns)
- Installation metadata (agent paths, overwrite strategies)

This makes it impossible to use graphrag-mcp for non-code domains (research, writing, world-building) without the skill injecting irrelevant code-centric patterns into the agent's context.

### 2.2 Research Foundation

The skill restructuring is grounded in these papers and systems:

- **GraphRAG** (Microsoft, 2024): Community-based summarization over graph structures. Key insight: graph queries need different retrieval strategies for local vs. global questions. Our skill must teach agents WHEN to use `find_connections` (local) vs. `search_nodes` (global).
- **LightRAG** (2024): Dual-level retrieval combining low-level entity pairs with high-level topic summaries. Key insight: observations serve as our "low-level" facts while entity descriptions serve as "high-level" summaries. The skill must teach this two-tier mental model.
- **CoALA** (Cognitive Architectures for Language Agents, 2023): Framework categorizing agent memory into working memory, episodic memory, semantic memory, and procedural memory. graphrag-mcp implements semantic memory (entities/relationships) and episodic memory (observations with timestamps). The skill must position itself within this framework so agents understand what graphrag-mcp is and isn't.
- **MemGPT** (2023): Tiered memory with explicit memory management functions. Key insight: agents must be taught explicit memory hygiene — when to store, when to retrieve, when to update, when to forget. Our skill must include a memory lifecycle section.
- **SCM (Structured Context Memory)** (2024): Schema-driven memory that separates structure from content. Key insight: teaching the agent the graph schema (entity types, relationship types) separately from domain content yields better retrieval. Our conventions file implements this.

### 2.3 New File Structure

```
skills/
├── graphrag-mcp/
│   ├── SKILL.md              # Core skill (always installed, ~120 lines)
│   ├── conventions.md        # Entity/relationship naming conventions (~60 lines)
│   ├── workflows.md          # Step-by-step retrieval workflows (~80 lines)
│   ├── best-practices.md     # Memory hygiene, anti-patterns (~60 lines)
│   └── domains/
│       ├── code.md           # Software engineering domain overlay (~40 lines)
│       ├── research.md       # Academic/research domain overlay (~40 lines)
│       └── general.md        # General-purpose domain overlay (~40 lines)
└── SKILL.md                  # DEPRECATED — symlink to graphrag-mcp/SKILL.md
                              # for backward compat during transition
```

### 2.4 Core Skill Content (`SKILL.md`)

The core skill teaches four things in this order:

**WHY** — What graphrag-mcp is, what memory problem it solves, where it sits in CoALA's memory taxonomy (semantic + episodic memory layer).

**WHAT** — The three primitives:
- **Entity**: A named concept with a type and description. The node in the graph.
- **Observation**: An atomic fact attached to an entity. Timestamped, embedded separately for fine-grained retrieval.
- **Relationship**: A typed, weighted edge between two entities. Directional.

**HOW** — The 12 MCP tools organized by operation:
- Write tools: `add_entities`, `add_relationships`, `add_observations`, `update_entity`, `delete_entities`, `merge_entities`
- Read tools: `search_nodes`, `search_observations`, `get_entity`, `find_connections`, `find_paths`, `get_subgraph`, `read_graph`

Note: `search_observations` is listed here because it will be wired as an MCP tool in the search optimization work (Section 3.5).

**WHEN** — Decision framework for memory operations:
- **Store** when you learn a new fact, make a decision, or discover a relationship
- **Retrieve** when you need context, are making a decision, or starting a new task
- **Update** when facts change, descriptions become stale, or entities need merging
- **Forget** (`delete_entities`) when information is confirmed wrong or obsolete

### 2.5 Conventions File (`conventions.md`)

Naming rules that apply across all domains:

```
Entity names:   PascalCase for types, descriptive names for instances
                "AuthService" not "auth_service" or "the auth service"

Entity types:   Lowercase, singular nouns
                "person", "concept", "system", "decision", "event"

Relationships:  UPPER_SNAKE_CASE verb phrases
                "DEPENDS_ON", "AUTHORED_BY", "DECIDED_TO", "RELATES_TO"

Observations:   Complete sentences, single atomic fact per observation
                "The API rate limit was increased from 100 to 500 req/s on 2026-03-15"
                NOT "rate limit stuff changed"
```

### 2.6 Workflows File (`workflows.md`)

Step-by-step retrieval patterns:

**Recall workflow** (before starting any task):
1. `read_graph` — Get overview stats, understand what's in the graph
2. `search_nodes` with task-relevant query — Find related entities
3. `get_entity` on top results — Load full context with observations
4. `find_connections` if relationships matter — Explore neighborhood

**Store workflow** (after completing a task or learning something):
1. Identify new entities, observations, and relationships from the session
2. `search_nodes` to check if entities already exist (avoid duplicates)
3. `add_entities` for genuinely new entities
4. `add_observations` to attach new facts to existing entities
5. `add_relationships` to connect entities

**Merge workflow** (when duplicates are discovered):
1. `get_entity` on both candidates
2. Verify they represent the same real-world concept
3. `merge_entities` — source is absorbed into target
4. Review merged entity, `update_entity` if description needs refinement

### 2.7 Best Practices File (`best-practices.md`)

Memory hygiene rules derived from MemGPT and SCM research:

- **Atomic observations**: One fact per observation. "X depends on Y" is one observation, not "X depends on Y and Z was refactored."
- **Description freshness**: Entity descriptions should summarize current state. Update after significant changes.
- **Relationship weights**: Use 0.0-1.0 to indicate strength/confidence. Default 1.0 for definite relationships.
- **Graph pruning**: Periodically review with `read_graph`. Delete entities that are no longer relevant.
- **Anti-patterns**:
  - Storing entire file contents as observations (too large, not atomic)
  - Creating entities for every variable/function (too granular)
  - Never updating descriptions (staleness)
  - Using `search_nodes` without reading results via `get_entity` (missing observations)

### 2.8 Domain Overlays

Each domain file provides:
- Recommended entity types for that domain
- Recommended relationship types for that domain
- Domain-specific storage heuristics
- Example entities and observations

**`domains/code.md`** — Entity types: `module`, `function`, `class`, `api`, `decision`, `bug`, `dependency`. Relationship types: `IMPORTS`, `CALLS`, `DEPENDS_ON`, `IMPLEMENTS`, `DECIDED_TO`.

**`domains/research.md`** — Entity types: `paper`, `author`, `concept`, `dataset`, `method`, `finding`, `hypothesis`. Relationship types: `CITES`, `AUTHORED_BY`, `USES_METHOD`, `CONTRADICTS`, `SUPPORTS`, `EXTENDS`.

**`domains/general.md`** — Entity types: `person`, `place`, `event`, `concept`, `organization`, `artifact`. Relationship types: `RELATES_TO`, `PART_OF`, `CAUSED_BY`, `LOCATED_IN`, `CREATED_BY`.

### 2.9 Installer Changes

Current installer (`cli/install.py`, 423 lines) has an agent registry and writes `SKILL.md` to agent-specific paths.

**New `--domain` flag**:

```
graphrag-mcp install claude                    # Installs core skill only (general domain)
graphrag-mcp install claude --domain code      # Installs core + code domain overlay
graphrag-mcp install claude --domain research  # Installs core + research domain overlay
graphrag-mcp install opencode --domain code    # Same for opencode
```

**Implementation**:

```python
# In cli/install.py — add to argument parser
install_parser.add_argument(
    "--domain",
    choices=["code", "research", "general"],
    default="general",
    help="Domain overlay to install alongside core skill",
)
```

The installer assembles the final skill content by concatenating:
1. `graphrag-mcp/SKILL.md` (always)
2. `graphrag-mcp/conventions.md` (always)
3. `graphrag-mcp/workflows.md` (always)
4. `graphrag-mcp/best-practices.md` (always)
5. `graphrag-mcp/domains/{domain}.md` (based on `--domain` flag)

This produces a single assembled file written to the agent's skill location, keeping the agent-side integration unchanged (one file to read) while the source is modular.

### 2.10 Backward Compatibility

- The root `skills/SKILL.md` becomes a symlink to `skills/graphrag-mcp/SKILL.md` during the transition period
- Agents that already have the v1 skill installed continue working — the installer's `--force` flag overwrites with the new assembled skill
- The assembled output format is identical (single markdown file) so no agent-side changes are required

### 2.11 Test Strategy

| Test | What it verifies |
|------|-----------------|
| `test_skill_assembly_core_only` | Assembling with no domain includes all 4 core files |
| `test_skill_assembly_with_domain` | Assembling with `--domain code` appends code overlay |
| `test_skill_assembly_all_domains` | Each domain file exists and is valid markdown |
| `test_install_with_domain_flag` | CLI `install --domain code` produces correct output |
| `test_install_default_domain` | CLI `install` without `--domain` defaults to `general` |
| `test_backward_compat_symlink` | Root `skills/SKILL.md` symlink resolves correctly |
| `test_skill_no_domain_leakage` | Core skill contains zero domain-specific entity types |
| `test_assembled_skill_line_count` | Assembled skill is within expected size bounds |

---

## 3. Search Algorithm Optimization

### 3.1 Current Architecture

```
                    ┌──────────────────────┐
                    │   search_nodes()     │  MCP tool (server.py)
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  HybridSearch        │  semantic/search.py
                    │  .search_entities()  │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌──────▼───────┐ ┌──────▼───────┐
    │ Vector search  │ │ FTS5 search  │ │ Relationship │
    │ (sqlite-vec)   │ │ (SQLite FTS5)│ │ fetch (N+1!) │
    │                │ │              │ │              │
    │ cosine sim     │ │ MATCH query  │ │ per-entity   │
    │ on embeddings  │ │ on text      │ │ loop query   │
    └────────┬───────┘ └──────┬───────┘ └──────┬───────┘
             │                │                │
             └────────┬───────┘                │
                      │                        │
            ┌─────────▼──────────┐             │
            │  _rrf_fuse()       │             │
            │  k=60 (hardcoded)  │             │
            │  sum(scores) only  │             │
            │  NO alpha weighting│             │
            └─────────┬──────────┘             │
                      │                        │
            ┌─────────▼────────────────────────▼──┐
            │  Build result dicts with             │
            │  entity + relationships              │
            └──────────────────────────────────────┘
```

### 3.2 Fix 1: Cross-Channel Entity + Observation Fusion

**Problem**: `search_entities()` and `search_observations()` operate independently. When a user searches for "rate limit changes", an entity named "APIRateLimiter" might not match well on its name/description, but an observation "Rate limit was increased to 500 req/s" would match perfectly. Currently there's no way to boost an entity's search rank based on its observations matching the query.

**Solution**: Implement observation-boosted entity search that fuses results from both channels.

```
search_entities_fused(query, limit):
    1. entity_results = vector_search(query, entities_table) + fts_search(query, entities_fts)
    2. obs_results = vector_search(query, observations_table) + fts_search(query, observations_fts)
    3. entity_scores = rrf_fuse(entity_results)      # {entity_id: score}
    4. obs_entity_scores = {}                          # {entity_id: score}
       for obs in rrf_fuse(obs_results):
           obs_entity_scores[obs.entity_id] += obs.score * obs_boost_factor
    5. final_scores = merge(entity_scores, obs_entity_scores)
       for entity_id in union(entity_scores, obs_entity_scores):
           final_scores[entity_id] = entity_scores.get(entity_id, 0) + obs_entity_scores.get(entity_id, 0)
    6. return top-k entities sorted by final_scores
```

**Configuration**: `obs_boost_factor` defaults to `0.5` (observations contribute at half weight relative to direct entity matches). Configurable via `GRAPHRAG_OBS_BOOST` env var.

**Data flow**:

```
Query "rate limit changes"
         │
         ├──► Entity vector search ──► "APIRateLimiter" score=0.3
         ├──► Entity FTS search    ──► (no match)
         ├──► Obs vector search    ──► obs:"Rate limit increased to 500" score=0.9
         │                              (attached to entity "APIRateLimiter")
         └──► Obs FTS search       ──► obs:"rate limit was increased" score=0.8
                                        (attached to entity "APIRateLimiter")
         │
         ▼
    Entity RRF: "APIRateLimiter" = 1/(60+1) = 0.0164
    Obs RRF:    "APIRateLimiter" = 1/(60+1) + 1/(60+2) = 0.0325, * 0.5 = 0.0163
    Final:      "APIRateLimiter" = 0.0164 + 0.0163 = 0.0327  (boosted from 0.0164)
```

### 3.3 Fix 2: FTS5 Query Hardening

**Problem**: In `sqlite_backend.py` lines 471 and 489, FTS5 queries only escape double quotes:

```python
safe_query = query.replace('"', '""')
fts_query = f'"{safe_query}"'
```

This fails for queries containing FTS5 metacharacters: `*`, `-`, `NOT`, `AND`, `OR`, `(`, `)`, `NEAR`, `:`, `^`, `+`.

Example failures:
- Query `"C++ templates"` → FTS5 interprets `+` as a prefix operator
- Query `"NOT a bug"` → FTS5 interprets `NOT` as a boolean operator
- Query `"error (timeout)"` → FTS5 interprets parentheses as grouping
- Query `"module:auth"` → FTS5 interprets `:` as a column filter

**Solution**: Wrap the entire query as a quoted FTS5 phrase after escaping internal quotes. This is the correct approach per SQLite FTS5 documentation — a quoted string is treated as a phrase literal with no operator interpretation.

```python
def _sanitize_fts5_query(self, query: str) -> str:
    """Sanitize a user query for safe use in FTS5 MATCH expressions.

    FTS5 interprets many characters and words as operators:
    - Boolean: AND, OR, NOT
    - Prefix: *
    - Negation: -
    - Grouping: ( )
    - Column filter: :
    - Proximity: NEAR
    - Caret: ^
    - Plus: +

    Wrapping in double quotes makes FTS5 treat the entire input as
    a literal phrase. Internal double quotes are escaped by doubling.

    Args:
        query: Raw user query string.

    Returns:
        A quoted, escaped string safe for FTS5 MATCH.
    """
    # Step 1: Escape internal double quotes by doubling them
    escaped = query.replace('"', '""')
    # Step 2: Wrap in double quotes to create a phrase literal
    return f'"{escaped}"'
```

Replace both instances in `sqlite_backend.py` (lines 471 and 489) with a call to `self._sanitize_fts5_query(query)`.

**Edge cases to handle**:
- Empty string → return `""` (empty phrase, matches nothing)
- String of only whitespace → strip and return `""` if empty
- Unicode characters → pass through unchanged (FTS5 handles UTF-8)
- Very long strings → no special handling needed (SQLite handles arbitrary lengths)

### 3.4 Fix 3: Batch Relationship Fetch (N+1 Elimination)

**Problem**: In `search.py` line 199, after finding matching entities, relationships are fetched one at a time:

```python
for entity in results:
    relationships = await self.storage.get_relationships_for_entity(entity.name)
    entity_dict["relationships"] = relationships
```

For a search returning 10 entities, this executes 10 separate SQL queries.

**Solution**: Add a batch method to `StorageBackend` and use it in `HybridSearch`.

**New method on `StorageBackend` ABC** (`storage/base.py`):

```python
@abstractmethod
async def get_relationships_for_entities(
    self, entity_names: list[str]
) -> dict[str, list[Relationship]]:
    """Fetch relationships for multiple entities in a single query.

    Args:
        entity_names: List of entity names to fetch relationships for.

    Returns:
        Dict mapping entity name to list of its relationships.
        Entities with no relationships map to empty lists.
    """
    ...
```

**Implementation in `SQLiteBackend`** (`storage/sqlite_backend.py`):

```python
async def get_relationships_for_entities(
    self, entity_names: list[str]
) -> dict[str, list[Relationship]]:
    if not entity_names:
        return {}

    placeholders = ",".join("?" * len(entity_names))
    query = f"""
        SELECT source, target, relationship_type, weight, properties
        FROM relationships
        WHERE source IN ({placeholders}) OR target IN ({placeholders})
    """
    params = list(entity_names) + list(entity_names)

    async with self._connection() as conn:
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()

    result: dict[str, list[Relationship]] = {name: [] for name in entity_names}
    for row in rows:
        rel = Relationship(
            source=row[0],
            target=row[1],
            relationship_type=row[2],
            weight=row[3],
            properties=json.loads(row[4]) if row[4] else {},
        )
        if rel.source in result:
            result[rel.source].append(rel)
        if rel.target in result and rel.target != rel.source:
            result[rel.target].append(rel)

    return result
```

**Update `HybridSearch.search_entities()`**:

```python
# Before (N+1):
for entity in results:
    rels = await self.storage.get_relationships_for_entity(entity.name)

# After (single batch):
entity_names = [e.name for e in results]
all_rels = await self.storage.get_relationships_for_entities(entity_names)
for entity in results:
    rels = all_rels.get(entity.name, [])
```

**Performance impact**: For a search returning `n` entities, this reduces database round-trips from `n+1` to `2` (one for the search, one for all relationships).

### 3.5 Fix 4: Configurable RRF Alpha Weighting

**Problem**: The `_rrf_fuse()` method in `search.py` uses a static `k=60` and simply sums reciprocal ranks without any weighting between vector and FTS5 channels:

```python
def _rrf_fuse(self, vec_results, fts_results, k=60):
    scores = {}
    for rank, item in enumerate(vec_results):
        scores[item.id] = scores.get(item.id, 0) + 1 / (k + rank + 1)
    for rank, item in enumerate(fts_results):
        scores[item.id] = scores.get(item.id, 0) + 1 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

This gives equal weight to vector and FTS5 results. For some use cases (short keyword queries), FTS5 should dominate. For others (semantic/conceptual queries), vector search should dominate.

**Solution**: Add configurable `alpha` parameter that controls the balance:

```python
def _rrf_fuse(
    self,
    vec_results: list,
    fts_results: list,
    k: int = 60,
    alpha: float = 0.5,
) -> list[tuple[str, float]]:
    """Fuse vector and FTS5 results using Reciprocal Rank Fusion.

    Args:
        vec_results: Results from vector similarity search, ordered by similarity.
        fts_results: Results from FTS5 full-text search, ordered by relevance.
        k: RRF constant. Higher values reduce the influence of high-ranking results.
            Default 60 per the original RRF paper (Cormack et al., 2009).
        alpha: Balance between vector (alpha) and FTS5 (1-alpha) results.
            0.0 = FTS5 only, 1.0 = vector only, 0.5 = equal weight.
            Default 0.5.

    Returns:
        List of (item_id, fused_score) tuples sorted by descending score.
    """
    if not (0.0 <= alpha <= 1.0):
        raise ValueError(f"alpha must be between 0.0 and 1.0, got {alpha}")

    scores: dict[str, float] = {}
    for rank, item in enumerate(vec_results):
        scores[item.id] = scores.get(item.id, 0.0) + alpha * (1.0 / (k + rank + 1))
    for rank, item in enumerate(fts_results):
        scores[item.id] = scores.get(item.id, 0.0) + (1.0 - alpha) * (1.0 / (k + rank + 1))

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

**Configuration**: `GRAPHRAG_RRF_ALPHA` env var, parsed in `utils/config.py`:

```python
@dataclass
class GraphRAGConfig:
    # ... existing fields ...
    rrf_alpha: float = field(
        default=0.5,
        metadata={"env": "GRAPHRAG_RRF_ALPHA"},
    )
    obs_boost: float = field(
        default=0.5,
        metadata={"env": "GRAPHRAG_OBS_BOOST"},
    )
```

### 3.6 Fix 5: Expose `search_observations` as MCP Tool

**Problem**: `HybridSearch.search_observations()` exists as a method (semantic/search.py) but is NOT wired as an MCP tool in `server.py`. Agents cannot search observations directly — they can only search entities and then read observations from entity results.

This is a significant gap: observations contain fine-grained facts that may not be findable through entity-level search. An observation "The deployment failed due to OOM at 2AM" attached to entity "ProductionIncident-2026-03-28" is hard to find by searching for "OOM deployment failure" at the entity level.

**Solution**: Add `search_observations` as the 13th MCP tool in `server.py`:

```python
@mcp.tool()
async def search_observations(
    ctx: Context,
    query: str,
    limit: int = 10,
    entity_names: list[str] | None = None,
) -> list[dict]:
    """Search observations using hybrid semantic + full-text search.

    Searches the text content of observations (atomic facts attached to entities)
    using combined vector similarity and FTS5 keyword matching. Useful for finding
    specific facts, events, or details that may not be reflected in entity names
    or descriptions.

    Args:
        query: Natural language search query.
        limit: Maximum results to return (default 10).
        entity_names: Optional filter to observations belonging to specific entities.

    Returns:
        List of matching observations with their parent entity names and scores.
    """
    state = _get_state(ctx)
    results = await state.search.search_observations(
        query=query,
        limit=limit,
        entity_names=entity_names,
    )
    return [
        {
            "entity_name": obs.entity_name,
            "content": obs.content,
            "created_at": obs.created_at.isoformat() if obs.created_at else None,
            "score": obs.score,
        }
        for obs in results
    ]
```

**Note**: The `entity_names` filter parameter requires a corresponding change to `HybridSearch.search_observations()` to accept and apply this filter. The current implementation does not support filtering by entity — add a `WHERE entity_name IN (...)` clause to both the vector and FTS5 queries when `entity_names` is provided.

### 3.7 Fix 6: Observation-Boosted Entity Search

This is the implementation detail of Fix 1 (Section 3.2) within the `search_entities` method. Rather than adding a separate method, the existing `search_entities()` gains an optional `boost_from_observations` parameter:

```python
async def search_entities(
    self,
    query: str,
    limit: int = 10,
    entity_types: list[str] | None = None,
    include_observations: bool = False,
    boost_from_observations: bool = True,  # NEW
) -> list[EntityResult]:
```

When `boost_from_observations=True` (the default), the method internally calls `search_observations()`, maps observation scores back to their parent entities, and merges with entity-level scores using `obs_boost_factor` from config.

When `boost_from_observations=False`, behavior is identical to v1 (useful for performance-sensitive callers or when observation boosting produces noise).

### 3.8 Search Optimization Data Flow (After All Fixes)

```
Query "rate limit changes"
         │
         ▼
┌────────────────────────────────┐
│  search_entities(query, ...)   │  MCP tool
└────────────┬───────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────────┐
│  HybridSearch.search_entities()                        │
│                                                        │
│  ┌─────────────────┐    ┌─────────────────────┐        │
│  │ Entity channel   │    │ Observation channel  │       │
│  │                  │    │ (boost_from_obs=True)│       │
│  │ ┌─────────────┐ │    │ ┌─────────────────┐  │       │
│  │ │Vec search   │ │    │ │Vec search       │  │       │
│  │ │(sqlite-vec) │ │    │ │(sqlite-vec)     │  │       │
│  │ └──────┬──────┘ │    │ └───────┬─────────┘  │       │
│  │ ┌──────▼──────┐ │    │ ┌───────▼─────────┐  │       │
│  │ │FTS5 search  │ │    │ │FTS5 search      │  │       │
│  │ │(hardened!)  │ │    │ │(hardened!)       │  │       │
│  │ └──────┬──────┘ │    │ └───────┬─────────┘  │       │
│  │ ┌──────▼──────┐ │    │ ┌───────▼─────────┐  │       │
│  │ │RRF fuse     │ │    │ │RRF fuse         │  │       │
│  │ │(alpha=cfg)  │ │    │ │(alpha=cfg)      │  │       │
│  │ └──────┬──────┘ │    │ └───────┬─────────┘  │       │
│  │        │        │    │         │             │       │
│  └────────┼────────┘    └─────────┼─────────────┘       │
│           │                       │                     │
│  ┌────────▼───────────────────────▼──────────────┐      │
│  │  Cross-channel fusion                         │      │
│  │  entity_score + obs_score * obs_boost_factor  │      │
│  └────────────────────┬──────────────────────────┘      │
│                       │                                 │
│  ┌────────────────────▼──────────────────────────┐      │
│  │  Batch relationship fetch (single query!)     │      │
│  │  get_relationships_for_entities([...])         │      │
│  └────────────────────┬──────────────────────────┘      │
│                       │                                 │
└───────────────────────┼─────────────────────────────────┘
                        │
                        ▼
                   Result list
```

### 3.9 Search Test Strategy

| Test | What it verifies |
|------|-----------------|
| `test_fts5_escape_double_quotes` | Queries with `"` don't break FTS5 |
| `test_fts5_escape_asterisk` | Queries with `*` are treated as literals |
| `test_fts5_escape_minus` | Queries with `-` are treated as literals |
| `test_fts5_escape_boolean_operators` | `NOT`, `AND`, `OR` in queries are literal |
| `test_fts5_escape_parentheses` | `(` and `)` in queries are treated as literals |
| `test_fts5_escape_column_filter` | `:` in queries doesn't trigger column filter |
| `test_fts5_escape_near` | `NEAR` in queries is treated as literal |
| `test_fts5_escape_caret_plus` | `^` and `+` in queries are treated as literals |
| `test_fts5_escape_empty_string` | Empty query returns empty results |
| `test_fts5_escape_unicode` | Unicode queries pass through correctly |
| `test_batch_relationships_single` | Batch fetch with 1 entity matches single fetch |
| `test_batch_relationships_multiple` | Batch fetch with N entities returns all relationships |
| `test_batch_relationships_empty` | Batch fetch with empty list returns empty dict |
| `test_batch_relationships_no_rels` | Entities with no relationships map to empty lists |
| `test_rrf_alpha_zero` | alpha=0.0 uses only FTS5 scores |
| `test_rrf_alpha_one` | alpha=1.0 uses only vector scores |
| `test_rrf_alpha_half` | alpha=0.5 weights equally (backward compat) |
| `test_rrf_alpha_invalid` | alpha outside [0,1] raises ValueError |
| `test_obs_boost_increases_entity_rank` | Entity with matching observation ranks higher |
| `test_obs_boost_zero` | obs_boost=0 disables observation boosting |
| `test_obs_boost_disabled` | boost_from_observations=False skips observation search |
| `test_search_observations_mcp_tool` | MCP tool returns correct observation format |
| `test_search_observations_entity_filter` | entity_names filter restricts results |
| `test_search_observations_no_filter` | Without filter, searches all observations |
| `test_cross_channel_fusion_disjoint` | Entities found only via observations appear in results |
| `test_cross_channel_fusion_overlap` | Entities found in both channels get boosted scores |
| `test_existing_279_tests_pass` | All pre-existing tests remain green |

---

## 4. Visualization Frontend

### 4.1 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  React SPA (Vite + Tailwind CSS)                          │  │
│  │                                                           │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐  │  │
│  │  │ Graph View  │  │ Entity Panel │  │ Search Bar      │  │  │
│  │  │ (WebGL      │  │ (details,    │  │ (hybrid search  │  │  │
│  │  │  force-     │  │  observations│  │  via REST API)  │  │  │
│  │  │  directed)  │  │  rels)       │  │                 │  │  │
│  │  └──────┬──────┘  └──────┬───────┘  └────────┬────────┘  │  │
│  │         │                │                    │           │  │
│  │  ┌──────▼────────────────▼────────────────────▼────────┐  │  │
│  │  │  REST API Client (fetch)                            │  │  │
│  │  └──────────────────────┬──────────────────────────────┘  │  │
│  └─────────────────────────┼─────────────────────────────────┘  │
│                            │ HTTP (localhost:PORT)               │
└────────────────────────────┼────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  graphrag-mcp ui server (Python, aiohttp)                       │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Routes                                                   │   │
│  │                                                           │   │
│  │  GET /api/graph        → Full graph (entities + rels)     │   │
│  │  GET /api/entity/:id   → Single entity with observations  │   │
│  │  GET /api/search?q=    → Hybrid search results            │   │
│  │  GET /api/stats        → Graph statistics                 │   │
│  │  GET /                 → Serve built SPA (index.html)     │   │
│  │  GET /assets/*         → Serve built SPA assets           │   │
│  │                                                           │   │
│  └──────────────────────┬────────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────▼────────────────────────────────────┐   │
│  │  Reuse existing backend (read-only)                       │   │
│  │                                                           │   │
│  │  StorageBackend  ←──  SQLiteBackend                       │   │
│  │  HybridSearch    ←──  EmbeddingEngine                     │   │
│  │  GraphTraversal  ←──  GraphEngine                         │   │
│  │                                                           │   │
│  │  NO WRITE OPERATIONS EXPOSED                              │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Framework | React 19 | Dominant ecosystem, component model fits panel-based UI |
| Bundler | Vite 6 | Fast dev builds, optimized production output, native ESM |
| Styling | Tailwind CSS 4 | Utility-first, small bundle with purging, dark mode built-in |
| Graph rendering | @react-three/fiber + force-graph-3d OR sigma.js | WebGL-based force-directed layout. sigma.js is lighter (2D) — choose based on graph size. For <1000 nodes, sigma.js. For larger, force-graph-3d with WebGL. |
| HTTP client | Native fetch | No dependency needed for simple REST calls |
| State management | React context + useReducer | Graph state is simple — no need for Redux/Zustand |

**Decision**: Use **sigma.js** for graph rendering. Rationale: graphrag-mcp graphs are per-project and typically contain hundreds to low thousands of entities. sigma.js provides WebGL-accelerated 2D rendering with excellent performance at this scale, a smaller bundle than 3D alternatives, and built-in interactions (pan, zoom, click, hover). 3D adds visual complexity without proportional value for knowledge graphs.

### 4.3 Frontend File Structure

```
src/graphrag_mcp/
├── ui/                         # New package
│   ├── __init__.py
│   ├── server.py               # aiohttp server (~150 lines)
│   ├── routes.py               # API route handlers (~120 lines)
│   └── frontend/               # Built SPA assets (generated)
│       ├── index.html
│       └── assets/
│           ├── index-[hash].js
│           └── index-[hash].css
│
ui-frontend/                    # Frontend source (NOT in Python package src)
├── package.json
├── vite.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx                # Entry point
│   ├── App.tsx                 # Root component with layout
│   ├── api/
│   │   └── client.ts           # REST API client (~60 lines)
│   ├── components/
│   │   ├── GraphView.tsx       # sigma.js graph canvas (~120 lines)
│   │   ├── EntityPanel.tsx     # Entity detail sidebar (~100 lines)
│   │   ├── SearchBar.tsx       # Search input with results (~80 lines)
│   │   ├── StatsBar.tsx        # Graph statistics bar (~40 lines)
│   │   ├── FilterPanel.tsx     # Entity type / relationship filters (~60 lines)
│   │   └── ThemeToggle.tsx     # Dark/light mode toggle (~20 lines)
│   ├── hooks/
│   │   ├── useGraph.ts         # Graph data fetching + state (~50 lines)
│   │   ├── useSearch.ts        # Search with debounce (~30 lines)
│   │   └── useTheme.ts         # Theme persistence (~20 lines)
│   ├── types/
│   │   └── graph.ts            # TypeScript types matching Python models (~40 lines)
│   └── styles/
│       └── globals.css         # Tailwind directives + custom properties
└── public/
    └── favicon.svg
```

### 4.4 REST API Specification

#### `GET /api/graph`

Returns the complete graph for visualization.

**Query parameters**:
- `entity_types` (optional): Comma-separated list of entity types to filter by
- `limit` (optional): Maximum number of entities to return (default: 500)

**Response** (200):
```json
{
  "entities": [
    {
      "name": "AuthService",
      "entity_type": "module",
      "description": "Handles authentication and token management",
      "properties": {},
      "observation_count": 5,
      "relationship_count": 3
    }
  ],
  "relationships": [
    {
      "source": "AuthService",
      "target": "TokenStore",
      "relationship_type": "DEPENDS_ON",
      "weight": 1.0,
      "properties": {}
    }
  ],
  "total_entities": 42,
  "total_relationships": 67
}
```

**Notes**:
- Observations are NOT included in the graph endpoint (too large). Use `/api/entity/:id` to fetch observations for a specific entity.
- When `limit` is less than `total_entities`, entities are sorted by relationship count (most connected first) to show the most important nodes.

#### `GET /api/entity/:name`

Returns full entity details including observations and relationships.

**Path parameters**:
- `name`: URL-encoded entity name

**Response** (200):
```json
{
  "name": "AuthService",
  "entity_type": "module",
  "description": "Handles authentication and token management",
  "properties": {"created": "2026-03-15"},
  "observations": [
    {
      "content": "Migrated from JWT to Paseto tokens on 2026-03-20",
      "created_at": "2026-03-20T14:30:00Z"
    }
  ],
  "relationships": [
    {
      "source": "AuthService",
      "target": "TokenStore",
      "relationship_type": "DEPENDS_ON",
      "weight": 1.0,
      "direction": "outgoing"
    }
  ]
}
```

**Response** (404):
```json
{
  "error": "Entity not found",
  "name": "NonexistentEntity"
}
```

#### `GET /api/search?q=`

Hybrid search over entities and observations.

**Query parameters**:
- `q` (required): Search query string
- `limit` (optional): Maximum results (default: 10)
- `entity_types` (optional): Comma-separated entity type filter

**Response** (200):
```json
{
  "query": "authentication tokens",
  "results": [
    {
      "name": "AuthService",
      "entity_type": "module",
      "description": "Handles authentication and token management",
      "score": 0.847,
      "matched_observations": [
        "Migrated from JWT to Paseto tokens on 2026-03-20"
      ]
    }
  ],
  "total_results": 3
}
```

**Notes**:
- Uses the same `HybridSearch.search_entities()` as the MCP tool, with observation boosting enabled
- `matched_observations` shows which observations contributed to the match (top 3 per entity)

#### `GET /api/stats`

Returns graph statistics for the stats bar.

**Response** (200):
```json
{
  "entity_count": 42,
  "relationship_count": 67,
  "observation_count": 156,
  "entity_type_distribution": {
    "module": 12,
    "function": 18,
    "decision": 7,
    "bug": 5
  },
  "relationship_type_distribution": {
    "DEPENDS_ON": 23,
    "CALLS": 31,
    "DECIDED_TO": 7,
    "FIXES": 6
  },
  "most_connected_entities": [
    {"name": "AuthService", "connection_count": 12},
    {"name": "DatabasePool", "connection_count": 9}
  ],
  "recently_updated": [
    {"name": "AuthService", "updated_at": "2026-03-31T10:00:00Z"},
    {"name": "TokenStore", "updated_at": "2026-03-30T15:30:00Z"}
  ]
}
```

#### `GET /` and `GET /assets/*`

Serves the built SPA. `index.html` at root, hashed JS/CSS bundles from `/assets/`.

### 4.5 CLI Command

Add `ui` command to `cli/main.py`:

```python
ui_parser = subparsers.add_parser(
    "ui",
    help="Launch the graph visualization UI in your browser",
)
ui_parser.add_argument(
    "--port",
    type=int,
    default=0,  # 0 = find an available port
    help="Port to serve the UI on (default: auto-select available port)",
)
ui_parser.add_argument(
    "--host",
    default="127.0.0.1",
    help="Host to bind to (default: 127.0.0.1)",
)
ui_parser.add_argument(
    "--no-open",
    action="store_true",
    help="Don't automatically open the browser",
)
```

**Behavior**:
1. Initialize `SQLiteBackend` from the current project's `.graphrag/graph.db`
2. Initialize `HybridSearch` and `GraphEngine` (same as MCP server startup)
3. Start aiohttp server on `host:port`
4. Print URL to stdout: `Graph UI available at http://127.0.0.1:PORT`
5. Open browser unless `--no-open` is passed
6. Serve until Ctrl+C

**Port selection**: If `--port 0`, use `socket.bind(('', 0))` to let the OS assign an available port. This avoids port conflicts when multiple projects run UIs simultaneously.

### 4.6 Frontend Build Integration

The frontend is built separately and the output is bundled into the Python package:

```
# Build step (run during development or CI):
cd ui-frontend && npm run build
# Vite outputs to: ui-frontend/dist/

# Copy built assets into Python package:
cp -r ui-frontend/dist/* src/graphrag_mcp/ui/frontend/
```

**In `pyproject.toml`**, add the built frontend as package data:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/graphrag_mcp"]

[tool.hatch.build.targets.wheel.force-include]
"src/graphrag_mcp/ui/frontend" = "graphrag_mcp/ui/frontend"
```

**In the aiohttp server**, serve static files from the package's frontend directory:

```python
import importlib.resources

frontend_path = importlib.resources.files("graphrag_mcp.ui") / "frontend"
app.router.add_static("/assets", frontend_path / "assets")

async def serve_index(request):
    index_path = frontend_path / "index.html"
    return web.FileResponse(index_path)

app.router.add_get("/", serve_index)
```

### 4.7 Frontend Components

**GraphView** (sigma.js):
- Force-directed layout with ForceAtlas2 algorithm
- Nodes colored by entity type (consistent color map derived from type name hash)
- Node size proportional to connection count
- Edge width proportional to relationship weight
- Edge labels showing relationship type (toggle-able)
- Click node → populate EntityPanel
- Hover node → tooltip with name + type
- Pan, zoom, box select via mouse/trackpad
- Minimap in bottom-right corner

**EntityPanel** (sidebar):
- Shows on entity click from GraphView or SearchBar result click
- Displays: name, type, description, properties (as key-value table)
- Observations list, sorted by created_at descending
- Relationships list, grouped by direction (outgoing/incoming)
- Each relationship is clickable → navigates to connected entity
- Close button returns to search/filter view

**SearchBar**:
- Debounced input (300ms) calling `/api/search?q=`
- Results shown as dropdown list below input
- Each result shows: name, type, score, snippet from top matched observation
- Click result → highlight node in GraphView + open EntityPanel
- Enter key → open first result
- Escape → close dropdown

**FilterPanel**:
- Checkboxes for each entity type present in the graph
- Checkboxes for each relationship type present in the graph
- Toggling a filter hides/shows corresponding nodes and edges in GraphView
- "Select All" / "Clear All" buttons
- Filter state persisted in URL query params for shareability

**StatsBar** (top bar):
- Entity count, relationship count, observation count
- Refreshes on initial load only (read-only, no live updates needed)

**ThemeToggle**:
- Dark/light mode toggle button in the top-right corner
- Respects `prefers-color-scheme` on first visit
- Persisted in `localStorage`
- Dark mode: dark background, light text, adjusted node/edge colors
- Light mode: white background, dark text, standard node/edge colors

### 4.8 Security Considerations

- **Read-only**: The UI server exposes NO write endpoints. All mutations happen through the MCP protocol only.
- **Local-only**: Default bind to `127.0.0.1`. Binding to `0.0.0.0` requires explicit `--host 0.0.0.0` flag.
- **No authentication**: Since it's local-only, no auth is needed. If bound to a non-loopback address, print a warning: "WARNING: UI is accessible from the network. No authentication is configured."
- **CORS**: Not needed for same-origin requests (SPA served from same server). If needed for development, add CORS headers only when `--dev` flag is passed.
- **Input sanitization**: Search queries go through the same FTS5 hardening (Fix 2) as MCP tool queries.

### 4.9 Visualization Test Strategy

| Test | What it verifies |
|------|-----------------|
| `test_ui_server_starts` | aiohttp server starts and responds on `/` |
| `test_api_graph_returns_entities` | `/api/graph` returns entities and relationships |
| `test_api_graph_type_filter` | `?entity_types=module` filters correctly |
| `test_api_graph_limit` | `?limit=5` returns at most 5 entities |
| `test_api_entity_found` | `/api/entity/AuthService` returns full details |
| `test_api_entity_not_found` | `/api/entity/Nonexistent` returns 404 |
| `test_api_entity_url_encoded` | `/api/entity/My%20Entity` handles URL encoding |
| `test_api_search_returns_results` | `/api/search?q=auth` returns scored results |
| `test_api_search_empty_query` | `/api/search?q=` returns 400 |
| `test_api_search_special_chars` | `/api/search?q=C%2B%2B` handles special chars |
| `test_api_stats_counts` | `/api/stats` returns correct counts |
| `test_api_stats_distributions` | Type distributions match actual data |
| `test_ui_cli_command_exists` | `graphrag-mcp ui --help` doesn't error |
| `test_ui_port_auto_select` | `--port 0` selects an available port |
| `test_ui_host_default` | Default host is 127.0.0.1 |
| `test_ui_no_write_endpoints` | No POST/PUT/DELETE routes exist |
| `test_frontend_assets_bundled` | `graphrag_mcp.ui.frontend` contains index.html |
| `test_index_html_served` | GET `/` returns HTML with correct content-type |

---

## 5. Desloppify

### 5.1 Principles

Every line of code in graphrag-mcp v2 must meet these standards:

1. **No placeholders**: No `pass`, no `...`, no `raise NotImplementedError` in non-abstract methods, no `# TODO`, no `# FIXME`, no `# HACK`, no `# XXX`
2. **No dead code**: No commented-out code blocks, no unused imports, no unreachable branches, no functions that are defined but never called
3. **Explicit error handling**: Every `try` block catches specific exceptions (not bare `except:` or `except Exception:`). Every error path either recovers meaningfully or raises with a clear message.
4. **Tests for everything**: Every public method has at least one test. Every bug fix has a regression test. Every error path has a test that triggers it.
5. **Clear docstrings**: Every public function, class, and module has a docstring. Docstrings follow Google style: one-line summary, blank line, Args/Returns/Raises sections. No docstrings that just restate the function name.
6. **Type annotations**: Every function signature has full type annotations including return type. No `Any` except where genuinely needed. No `# type: ignore` without a comment explaining why.

### 5.2 Specific Desloppify Targets

Based on the codebase analysis, these specific areas need attention:

**`server.py` (753 lines)**:
- Extract the `_get_state` helper into a proper dependency pattern rather than relying on `ctx.request_context.lifespan_state`
- The `AppState` / `InitializedState` pattern with `__getattr__` delegation is clever but fragile — add explicit type narrowing
- Tool docstrings are the MCP tool descriptions shown to agents — review each for clarity and completeness

**`sqlite_backend.py` (612 lines)**:
- The `_sanitize_fts5_query` fix (Section 3.3) must be applied consistently to ALL FTS5 query points, not just the two identified
- Connection management: verify that all `async with self._connection()` blocks handle `aiosqlite.OperationalError` for database locked scenarios
- The `fetch_entity_rows` batch method exists but is underused — audit all entity fetch patterns

**`search.py` (280 lines)**:
- After applying all 6 fixes, the file will grow substantially — consider splitting into `search/entity_search.py`, `search/observation_search.py`, `search/fusion.py`
- The embedding call in vector search should handle model load failures gracefully (embedding engine can fail to load)

**`install.py` (423 lines)**:
- Agent registry uses string literals for paths — extract into a typed `AgentConfig` dataclass
- Error messages for missing agent directories should suggest the most likely fix

**`config.py`**:
- Add validation for all new config values (`rrf_alpha` must be 0.0-1.0, `obs_boost` must be >= 0.0)
- Add `GRAPHRAG_UI_PORT` and `GRAPHRAG_UI_HOST` config for the UI server defaults

### 5.3 Desloppify Audit Process

For each file in the codebase:

1. **Lint**: Run `ruff check` with all rules enabled. Fix all violations.
2. **Type check**: Run `mypy --strict`. Fix all type errors.
3. **Dead code**: Run `vulture` or manual audit. Remove all dead code.
4. **Docstrings**: Run `pydocstyle`. Fix all missing or malformed docstrings.
5. **Test coverage**: Run `pytest --cov`. Identify untested public methods. Write tests.
6. **Error paths**: For each `try/except`, verify the except clause is specific and the error handling is meaningful.

### 5.4 Desloppify Test Strategy

| Test | What it verifies |
|------|-----------------|
| `test_ruff_clean` | `ruff check` passes with zero violations |
| `test_mypy_strict` | `mypy --strict` passes with zero errors |
| `test_no_todo_comments` | No `TODO`, `FIXME`, `HACK`, `XXX` comments in source |
| `test_no_bare_except` | No `except:` or `except Exception:` without re-raise |
| `test_all_public_functions_documented` | Every public function has a docstring |
| `test_all_functions_typed` | Every function has complete type annotations |
| `test_coverage_above_threshold` | Test coverage >= 90% for all modules |
| `test_no_unused_imports` | No unused imports in any source file |

---

## 6. Implementation Order

The four areas have dependencies that determine implementation order:

```
Phase 1: Search Algorithm Optimization (Section 3)
         ├── Fix 2: FTS5 hardening (prerequisite for all search work)
         ├── Fix 3: Batch relationship fetch (independent)
         ├── Fix 4: Configurable RRF alpha (independent)
         ├── Fix 5: Expose search_observations MCP tool (independent)
         ├── Fix 1 + Fix 6: Cross-channel fusion (depends on Fix 4, Fix 5)
         └── All 279 existing tests must pass after each fix

Phase 2: Domain-Agnostic Skill System (Section 2)
         ├── Write core skill files (conventions, workflows, best-practices)
         ├── Write domain overlays (code, research, general)
         ├── Update installer with --domain flag and assembly logic
         └── Update skill documentation with search_observations tool

Phase 3: Visualization Frontend (Section 4)
         ├── Build Python UI server (aiohttp routes)
         ├── Build React frontend (sigma.js graph, panels, search)
         ├── Add CLI command
         ├── Bundle frontend into Python package
         └── Depends on Phase 1 (search API used by frontend)

Phase 4: Desloppify (Section 5)
         ├── Runs continuously during Phases 1-3
         ├── Final comprehensive audit after all features complete
         └── Lint, type check, dead code removal, docstring review

```

### 6.1 Estimated Scope

| Phase | New files | Modified files | New tests | Estimated lines |
|-------|-----------|----------------|-----------|-----------------|
| Search Optimization | 0-1 | 4 (search.py, sqlite_backend.py, base.py, server.py) | ~27 | ~300 net new |
| Skill System | 7 | 2 (install.py, cli/main.py) | ~8 | ~500 net new |
| Visualization Frontend | ~15 (Python) + ~15 (TypeScript) | 2 (cli/main.py, pyproject.toml) | ~18 | ~1200 net new |
| Desloppify | 0 | All | ~8 | Net negative (removing code) |

---

## 7. Non-Goals

These are explicitly out of scope for v2:

- **Write operations through the UI**: The visualization is read-only. All mutations happen through MCP tools.
- **Real-time sync**: The UI does not live-update when the graph changes via MCP. Refresh the page to see changes.
- **Multi-project UI**: The UI serves one project's graph at a time (the `.graphrag/graph.db` in the current directory).
- **Authentication/authorization**: The UI is local-only. Network access requires explicit opt-in and comes with a warning.
- **Graph layout persistence**: Node positions are computed fresh on each page load via ForceAtlas2. No saved layouts.
- **Export from UI**: No CSV/JSON/image export from the visualization. Use the existing `graphrag-mcp export` CLI command.
- **Plugin system for UI**: No third-party UI plugins or custom views. The UI is a fixed set of components.
- **Mobile support**: The UI is designed for desktop browsers. Responsive layout is not a priority.
- **Embedding model selection UI**: Model is configured via environment variable, not through the UI.
