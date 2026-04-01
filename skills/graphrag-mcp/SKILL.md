# graphrag-mcp: Knowledge Graph Memory

Persistent, per-project knowledge graph memory for LLM agents via the Model Context Protocol.

graphrag-mcp stores entities, relationships, and observations in a local SQLite database
with hybrid semantic + full-text search. It runs entirely locally with embedded vector
embeddings — no API keys required.

---

## What It Is

graphrag-mcp implements **semantic memory** (entities and relationships forming a knowledge
graph) and **episodic memory** (timestamped observations capturing specific facts and events)
as defined in the CoALA cognitive architecture framework.

It is NOT working memory (your context window handles that) or procedural memory (your
training and skills handle that). It is long-term structured memory that persists across
sessions.

## Core Principle: Use It Continuously

**The graph is only useful if you maintain it.** Don't treat it as a write-once archive.
Treat it as a living document that you read from at the start of every session and write
to throughout your work. The ouroboros pattern: the tool documents the work, and the work
maintains the tool.

Every session should follow this loop:
1. **Recall** — Search the graph for context before starting work
2. **Work + Record** — Store decisions, discoveries, and facts as you go
3. **Persist** — Sweep for anything you missed before ending the session

If you skip steps 2 and 3, the graph becomes stale and step 1 becomes useless.

## The Three Primitives

**Entity** — A named concept with a type and description. The node in the graph.
- Has a unique name, a type (e.g. "person", "concept", "system"), and a free-text description
- Properties dict for structured metadata

**Observation** — An atomic fact attached to an entity. Timestamped and independently searchable.
- One fact per observation: "The API rate limit was increased from 100 to 500 req/s on 2026-03-15"
- Embedded separately for fine-grained retrieval

**Relationship** — A typed, weighted, directional edge between two entities.
- Has a type (e.g. "DEPENDS_ON"), weight (0.0-1.0), and optional properties

## MCP Tools

### Write Tools
- **add_entities** — Create entities (name, type, description, properties, optional observations)
- **add_relationships** — Create directed edges between entities (source → type → target)
- **add_observations** — Attach atomic facts to an existing entity
- **update_entity** — Modify description, properties, or type of an entity
- **delete_entities** — Remove entities and cascade to observations and relationships
- **merge_entities** — Absorb source entity into target, moving all data

### Read Tools
- **search_nodes** — Hybrid semantic + keyword search over entities
- **search_observations** — Search observation text directly (finds facts entity search may miss)
- **get_entity** — Full entity details including all observations and relationships
- **find_connections** — Multi-hop graph traversal from a starting entity
- **find_paths** — Shortest paths between two entities
- **get_subgraph** — Neighbourhood extraction around seed entities
- **read_graph** — Overview stats: counts, type distributions, most connected entities

## When to Use Memory

**Store** when you:
- Learn a new fact, concept, or relationship
- Make or discover an architectural decision
- Encounter a person, system, or important artifact
- Complete a task with lessons worth remembering
- Encounter a bug, its root cause, and fix

**Retrieve** when you:
- Start a new task or session (warm-start with context)
- Need to make a decision (check for prior decisions and context)
- Encounter something that might already be known
- Need to understand how components relate to each other

**Update** when:
- Facts change or descriptions become stale
- Entities should be merged (duplicates discovered)
- Observations need correction

**Forget** (delete) when:
- Information is confirmed wrong or obsolete
- Entities are no longer relevant to the project

## MCP Configuration

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
