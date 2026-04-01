# Workflows

## Session Start — Recall

Before diving into any task, warm-start your memory:

1. `read_graph` — Understand what's in the graph (counts, types, recent entities)
2. `search_nodes` with a task-relevant query — Find related entities
3. `get_entity` on top results — Load full context with observations and relationships
4. `find_connections` if you need to understand how things relate — Explore the neighbourhood

**Why this matters:** Without recall, you start every session cold. Prior decisions,
architecture knowledge, and lessons learned are lost. The graph is your long-term memory —
use it.

## During Work — Continuous Memory

As you work, maintain the graph. Don't batch everything to the end — update as you go:

### When to store (do this immediately, not later)

- **You make a decision** → `add_entities` with type `decision`, include rationale as an observation
- **You discover architecture** → `add_entities` for the component + `add_relationships` for dependencies
- **You learn a new fact** → `add_observations` on the relevant entity
- **You encounter a bug** → `add_entities` with type `bug`, add root cause and fix as observations
- **You complete a milestone** → `add_observations` on the project/feature entity

### When to update

- **A fact changes** → `update_entity` to refresh the description
- **You find duplicates** → `merge_entities` to consolidate
- **Something becomes obsolete** → `delete_entities` to prune

### The 5-minute rule

If you've been working for more than 5 minutes without touching the graph, pause and ask:
"Did I learn anything worth remembering?" If yes, store it now. If you wait until the end
of the session, you'll forget details or skip it entirely.

## After Learning Something — Store

When you discover important information worth remembering:

1. Identify new entities, observations, and relationships from the session
2. `search_nodes` to check if entities already exist — Avoid creating duplicates
3. `add_entities` for genuinely new concepts (include description and type)
4. `add_observations` to attach new facts to existing or new entities
5. `add_relationships` to connect entities with typed, directional edges

## Session End — Persist

Before ending a session, do a memory sweep:

1. Review what you accomplished — what decisions were made? What did you learn?
2. `add_observations` for any facts not yet stored
3. `update_entity` for any descriptions that are now stale
4. `read_graph` to verify the graph reflects current state

## Duplicate Discovery — Merge

When you find two entities that represent the same thing:

1. `get_entity` on both candidates — Review their observations and relationships
2. Verify they represent the same real-world concept (not just similar names)
3. `merge_entities` — Source is absorbed into target (all data moves to target)
4. `update_entity` on the merged entity if the description needs refinement

## Search Strategy

For **specific facts** (dates, numbers, exact events):
- Use `search_observations` — It searches observation text directly

For **concepts and entities** (people, systems, decisions):
- Use `search_nodes` — It searches entity names, types, and descriptions, boosted by matching observations

For **relationships and structure** (how things connect):
- Start with `search_nodes` or `get_entity`, then use `find_connections` or `find_paths`

For **broad overview** (what do we know about X):
- Use `get_subgraph` with seed entities to extract a neighbourhood

## Periodic Maintenance

Every few sessions, do a health check:

1. `read_graph` — Review entity counts and type distributions
2. Look for stale entities with outdated descriptions
3. Look for orphaned entities with no relationships
4. `delete_entities` for anything no longer relevant
5. `merge_entities` for any duplicates that crept in
