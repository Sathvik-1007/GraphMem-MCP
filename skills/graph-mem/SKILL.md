---
name: graph-mem
description: >-
  Persistent knowledge graph memory for AI agents via MCP. Use when agents need
  to remember facts across sessions, track decisions, build knowledge graphs,
  manage project context, or maintain structured long-term memory. Triggers on:
  remember this, store fact, what do we know about, search memory, knowledge graph,
  graph memory, add entity, find connections, cross-session memory, recall,
  semantic search, entity relationships, persistent memory, structured recall.
license: MIT
metadata:
  author: Sathvik
  version: 2.2.0
  created: 2025-03-15
  last_reviewed: 2026-04-22
  review_interval_days: 30
---

# graph-mem — Persistent Knowledge Graph Memory

You have a persistent, structured knowledge graph via MCP. It survives across
sessions. Runs entirely locally — SQLite with hybrid semantic + full-text search.
No API keys required.

## The Core Contract

**You extract and store knowledge when there is new factual content worth
persisting.** Not every turn — only turns where new knowledge, decisions,
discoveries, or corrections surfaced. Skip purely mechanical turns (formatting,
acknowledgments, tool output with no new facts).

After responding to the user, ask: "Did this turn produce new knowledge?" If yes,
shift into extraction mode. Details in `workflows.md`.

## The Three Primitives

| Primitive | What It Is | Store With |
|-----------|-----------|------------|
| **Entity** | A named node — a person, system, concept, decision, anything worth tracking | `add_entities` |
| **Observation** | An atomic fact attached to an entity — one sentence, one fact | `add_observations` |
| **Relationship** | A typed, directional edge between two entities | `add_relationships` |

Entities give you nodes to search for. Observations give you facts to recall.
Relationships give you structure to traverse. All three matter.

## MCP Tools

### Creating and Updating

| Tool | What It Does |
|------|-------------|
| `add_entities` | Create entities with name, type, description, and initial observations |
| `add_relationships` | Create directed edges between entities (source → type → target) |
| `add_observations` | Attach new facts to an existing entity |
| `update_entity` | Change an entity's description, type, or properties |
| `update_relationship` | Modify an edge's weight, type, or properties |
| `update_observation` | Edit observation text (re-embeds automatically) |
| `delete_entities` | Remove entities — cascades to their observations and relationships |
| `delete_relationships` | Remove edges between entities |
| `delete_observations` | Remove specific observations by ID |
| `merge_entities` | Combine duplicates — source is absorbed into target |

### Searching and Navigating

| Tool | When to Reach For It |
|------|---------------------|
| `search_nodes` | You have a concept and want to find matching entities (default: 5 results) |
| `search_observations` | You need a specific fact buried in observation text |
| `get_entity` | You know the entity name and want its full context |
| `find_connections` | You want to explore what's connected to an entity (default: 2 hops) |
| `find_paths` | You need to understand how two entities relate |
| `get_subgraph` | You want the neighborhood around one or more seed entities |
| `read_graph` | You want a bird's-eye view — counts, types, most connected |
| `list_entities` | You want to browse entities with pagination (default: 50) |

### Maintenance and Health

| Tool | When to Reach For It |
|------|---------------------|
| `graph_health` | Session start or periodic check — shows hotspots, missing descriptions, action items |
| `compact_observations` | Entity has too many observations (>15) — merge old ones into fewer, denser summaries |
| `suggest_connections` | Just added an entity and need to know what to connect it to in a large graph |
| `audit_graph` | Full quality screening — finds disconnected nodes, missing data, weak links. Plain text report |

### Relationships

| Tool | When to Reach For It |
|------|---------------------|
| `list_relationships` | Browse relationships with filtering — by entity, type, or both (paginated) |

### Visualization

| Tool | Purpose |
|------|---------|
| `open_dashboard` | Launch interactive graph explorer in browser (read-only, localhost) |

### Multiple Graphs

| Tool | Purpose |
|------|---------|
| `list_graphs` | Show all graphs in .graphmem/ |
| `create_graph` | Create a new named graph |
| `switch_graph` | Change which graph is active |
| `delete_graph` | Remove a named graph |

## Session Lifecycle

**Starting a session** — warm up before doing work:

```
graph_health              → counts, hotspots, action items
search_nodes("topic")     → what do we already know?
get_entity("Name")        → load full context for key entities
```

**During a session** — extract when new knowledge surfaces (see `workflows.md`).

**Ending a session** — sweep for anything unstored, update stale descriptions,
run `audit_graph` to screen for quality gaps, then `graph_health` to verify.

## Adding Entities in Large Graphs

When the graph has many entities, you can't read everything to know what to
connect a new entity to. Use `suggest_connections` after adding:

```
add_entities([...])                  → create the entity
suggest_connections("NewEntity")     → find semantically similar entities
add_relationships([...])             → connect based on suggestions
```

## MCP Configuration

```json
{
  "mcpServers": {
    "graphmem-mcp": {
      "command": "graph-mem",
      "args": ["server"]
    }
  }
}
```

Project-scoped: `"args": ["server", "--project-dir", "/path/to/project"]`
Named graph: `"args": ["server", "--graph", "my-project"]`

## Reference Files

| File | What's In It |
|------|-------------|
| `conventions.md` | Naming rules for entities, types, relationships, observations |
| `workflows.md` | The extraction pipeline — how to capture knowledge each turn |
| `best-practices.md` | Quality standards, common mistakes, and self-correction |
| `domains/` | Domain-specific entity types and relationship types |
