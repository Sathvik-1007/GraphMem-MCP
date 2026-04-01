# Best Practices

## The Ouroboros Pattern

When you're working on a project that uses graphrag-mcp, the graph should document the
project itself — including the decisions about how to use the graph. This self-referential
pattern means:

- Store project architecture as entities and relationships
- Record design decisions with rationale as observations
- Track which domain overlay you're using and why
- Update the graph when the project evolves

The graph eats its own tail: it documents the work, and the work maintains the graph.

## Memory Hygiene

- **Atomic observations**: One fact per observation. Splitting facts makes them independently searchable.
- **Description freshness**: Entity descriptions should summarize current state. Update after significant changes.
- **Relationship weights**: Use 0.0-1.0 to indicate confidence. Default 1.0 for definite relationships, lower for uncertain ones.
- **Search before storing**: Always check if an entity exists before creating a new one. Duplicates fragment your knowledge.
- **Regular pruning**: Periodically `read_graph` and review. Delete entities that are no longer relevant.

## Granularity Guidelines

**Too granular**: Don't create entities for every variable, function parameter, or config key.
Store things at the level you'd want to recall them — services, decisions, people, concepts.

**Too coarse**: Don't create one entity per project with all facts as observations.
Break knowledge into meaningful, searchable units.

**Right level**: One entity per architectural boundary, major decision, person, system,
or concept. Observations are the details — dates, metrics, rationale, quotes.

## Anti-Patterns

**Stale descriptions**: An entity description that says "handles auth" when the service was
rewritten to handle billing is worse than no description. Update or delete stale entities.

**Search without reading**: `search_nodes` returns summaries. Always follow up with
`get_entity` to see the full picture including observations before making decisions.

**Storing file contents**: Observations should be facts about code, not the code itself.
"AuthService uses bcrypt for password hashing" is useful. Pasting the entire file is not.

**Write-once mentality**: The graph is not an append-only log. Update descriptions,
merge duplicates, delete stale entities. A maintained graph is worth 10x an abandoned one.

**Session-end-only writes**: Don't batch all your graph updates to the end of a session.
You'll forget details and skip most of them. Write as you go.

## When NOT to Store

- Transient debugging information (stack traces, temporary test data)
- Information that changes every session (current file being edited)
- Things better handled by version control (code history, diffs)
- Personal preferences or ephemeral context (your current mood about the codebase)
