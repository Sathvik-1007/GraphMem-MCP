# graphrag-mcp

Persistent knowledge graph memory for LLM-powered CLI agents.

graphrag-mcp is an MCP server that stores entities, relationships, and observations
in a local SQLite database with hybrid semantic + full-text search. It runs entirely
locally with embedded vector embeddings -- no API keys required. Use it to maintain
structured memory across sessions: architecture decisions, people, concepts, code
components, and any knowledge worth remembering.

---

## MCP Configuration

Ensure the following is in your MCP configuration (`.mcp.json`, `claude_desktop_config.json`, etc.):

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

If the graphrag-mcp server is not configured, suggest adding it before proceeding.

---

## Core Concepts

- **Entities** -- Named nodes with a `name` (primary identifier), `entity_type`
  (e.g. person, concept, code_component), optional `description`, and `properties`.
  Names are case-insensitive for lookup but stored as given.
- **Relationships** -- Directed, typed, weighted edges between entities.
  `source --relationship_type--> target`. Weight is a float in [0, 1].
- **Observations** -- Atomic factual statements attached to an entity, individually
  embedded for fine-grained semantic search. Use for specific facts, quotes, and details.
- **Knowledge Graph** -- The combination of all three, stored in `.graphrag/graph.db`.

---

## When to Use

### At Session Start (most important)

ALWAYS search the knowledge graph at the beginning of a session. Without this,
accumulated knowledge is never retrieved and the graph provides no value.

```
search_nodes(query: "<topic of current task>")
read_graph()
```

### During Work

- **New concepts, people, places, events, decisions** -- call `add_entities`
- **Connections between entities** -- call `add_relationships`
- **New facts about existing entities** -- call `add_observations`
- **Outdated descriptions** -- call `update_entity`
- **Duplicate entities discovered** -- call `merge_entities` immediately
- **Exploring context** -- call `find_connections` or `get_subgraph`

### At Session End

Review what was learned. Capture any entities, relationships, or observations
that were missed during the session.

---

## Available Tools

### Write Tools

**add_entities** -- Batch-create entities. Auto-merges on name+type conflict.

```
add_entities(entities: [
  {
    "name": "AuthService",
    "entity_type": "code_component",
    "description": "Handles JWT-based authentication and session management",
    "properties": {"language": "TypeScript"},
    "observations": ["Uses RS256 for token signing", "Refresh tokens expire after 30 days"]
  }
])
```

Required: `name`, `entity_type`. Optional: `description`, `properties`, `observations`.

**add_relationships** -- Batch-create directed edges between entities.

```
add_relationships(relationships: [
  {"source": "AuthService", "target": "UserModel", "relationship_type": "depends_on"}
])
```

Required: `source`, `target`, `relationship_type`. Optional: `weight` (default 1.0), `properties`.
Duplicate edges (same source + target + type) merge, keeping the higher weight.

**add_observations** -- Attach new facts to an existing entity.

```
add_observations(
  entity_name: "AuthService",
  observations: ["Rate-limited to 10 attempts/min per IP", "Migrated from sessions in v2.3"],
  source: "code-review-2026-03"
)
```

`source` is optional but recommended for provenance tracking.

**update_entity** -- Modify description, properties, or type. Only provided fields change.

```
update_entity(name: "AuthService", description: "Handles JWT auth and OAuth2 flows")
```

Properties are merged (new keys added, existing updated).

**delete_entities** -- Remove entities by name. Cascades to relationships and observations.

```
delete_entities(names: ["OldService", "DeprecatedHelper"])
```

**merge_entities** -- Absorb source into target. Moves observations and relationships, deduplicates edges, deletes source.

```
merge_entities(target: "AuthService", source: "AuthenticationService")
```

### Read Tools

**search_nodes** -- Hybrid semantic + full-text search. The primary discovery tool.

```
search_nodes(
  query: "authentication and user sessions",
  limit: 10,
  entity_types: ["code_component", "decision"],
  include_observations: true
)
```

All parameters except `query` are optional. Uses vector similarity and FTS5 with
Reciprocal Rank Fusion for ranking.

**find_connections** -- Multi-hop graph traversal from a starting entity.

```
find_connections(entity_name: "AuthService", max_hops: 3, direction: "both")
```

Optional: `relationship_types` filter, `direction` (`"outgoing"`, `"incoming"`, `"both"`).

**get_entity** -- Full details of a single entity with observations and relationships.

```
get_entity(name: "AuthService")
```

Fuzzy name resolution: exact match, case-insensitive, then FTS5 suggestions.

**read_graph** -- Overview statistics. No arguments.

```
read_graph()
```

Returns counts, type distributions, most connected entities, recent updates.

**get_subgraph** -- Neighbourhood around seed entities.

```
get_subgraph(entity_names: ["AuthService", "UserModel"], radius: 2)
```

Expands outward up to `radius` hops (1-5, default 2).

**find_paths** -- Shortest paths between two entities.

```
find_paths(source: "AuthService", target: "PaymentGateway", max_hops: 5)
```

Returns up to 10 shortest paths as sequences of entities and relationship types.

---

## Entity Type Guidance

Types are flexible lowercase strings, not a fixed ontology. Common conventions:

| Type | Use for |
|------|---------|
| `person` | People, characters, authors, team members |
| `place` | Locations, cities, regions |
| `concept` | Abstract ideas, patterns, principles, domain terms |
| `event` | Meetings, incidents, releases, milestones |
| `decision` | Architectural choices, design trade-offs |
| `code_component` | Functions, classes, modules, services, APIs |
| `project` | Projects, repositories, systems |
| `document` | Files, articles, papers, specs |
| `organization` | Companies, teams, departments |
| `tool` | Libraries, frameworks, platforms |
| `bug` / `issue` | Known problems, feature requests, tickets |

Create new types as needed. Be consistent within a project.

---

## Relationship Conventions

Types are lowercase, underscore-separated. Direction: `source --type--> target`.

- **People:** `knows`, `works_with`, `reports_to`, `mentors`, `authored_by`
- **Structure:** `part_of`, `contains`, `located_in`, `belongs_to`
- **Code:** `depends_on`, `imports`, `calls`, `extends`, `implements`
- **Causality:** `causes`, `leads_to`, `blocks`, `enables`, `triggers`
- **Knowledge:** `contradicts`, `supports`, `related_to`, `references`
- **Ownership:** `created_by`, `maintained_by`, `owned_by`, `assigned_to`
- **Decisions:** `decided_on`, `rejected`, `supersedes`, `motivated_by`

---

## Best Practices

- **Search before creating.** Always check if an entity exists before adding it.
  Duplicates fragment knowledge and degrade search quality.
- **Be specific with names.** Use "John Smith" not "the detective". Names are primary keys.
- **Write rich descriptions.** Descriptions are embedded for semantic search -- the more
  descriptive, the better the results.
- **Use observations for facts.** Atomic statements like "Uses RS256 for JWT signing" go
  as observations. The description is a general summary; observations capture specifics.
- **Merge duplicates immediately.** If you find two entities for the same thing, merge
  them right away. Use the more common name as the target.
- **Batch operations.** Pass arrays to `add_entities` and `add_relationships` for
  efficiency and transactional consistency.
- **Track provenance.** Use `source` in `add_observations` to record where facts came from.
- **Keep the graph current.** Update stale descriptions. Record changes as observations.

---

## Common Workflows

### Starting a session on an existing project

```
1. read_graph()                                    -- Graph state overview
2. search_nodes(query: "<current task or topic>")  -- Retrieve prior knowledge
3. get_entity(name: "<key entity>")                -- Deep-dive as needed
4. Proceed with work, informed by retrieved context
```

### Onboarding to a new project

```
1. read_graph()                                    -- Check for existing knowledge
2. add_entities([
     {name: "ProjectName", entity_type: "project", description: "..."},
     {name: "MainService", entity_type: "code_component", description: "..."}
   ])
3. add_relationships([
     {source: "MainService", target: "ProjectName", relationship_type: "part_of"}
   ])
```

### Researching a topic

```
1. search_nodes(query: "topic keywords")
2. find_connections(entity_name: "RelevantEntity", max_hops: 2)
3. Add new entities and relationships as you discover them
4. add_observations for new facts on existing entities
```

### Architecture review

```
1. search_nodes(query: "system architecture", entity_types: ["code_component"])
2. get_subgraph(entity_names: ["ServiceA", "ServiceB"], radius: 2)
3. find_paths(source: "Frontend", target: "Database")
4. add_observations for new insights, add_entities for new components
```

### Recording a decision

```
1. add_entities([{
     name: "Use PostgreSQL for user data",
     entity_type: "decision",
     description: "Chose PostgreSQL over MongoDB for the user data store",
     observations: [
       "Decided during sprint 12 planning",
       "Key factors: ACID compliance, existing team expertise"
     ]
   }])
2. add_relationships([
     {source: "Use PostgreSQL for user data", target: "UserService",
      relationship_type: "decided_on"}
   ])
```
