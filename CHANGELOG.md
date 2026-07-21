# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-07-21

The package is now published as **`graphmem-mcp`**. The name `graph-mem` was
already taken on PyPI by an unrelated, abandoned package, so `pip install
graph-mem` never installed this project. The import name is unchanged
(`import graph_mem`) and the CLI is still `graph-mem`.

This release fixes seven defects that made the previous version unsafe or
unusable under load. Every one was reproduced before and after the fix.

### Security

- **Graph names are validated at the MCP trust boundary.** `switch_graph` and
  `delete_graph` turned an unvalidated, model-supplied string into a filesystem
  path. `delete_graph("../outside")` unlinked files outside `.graphmem/`, and
  `delete_graph("graph")` deleted the *active* database by aliasing around a
  guard written against the name `"default"`. All four multi-graph tools now
  route through one resolver with a name grammar, a length bound, a reserved
  stem, and a post-resolution containment check.
- **The web UI authenticates.** It previously exposed seven unauthenticated
  write endpoints on loopback with no `Origin` check, no `Host` check, and no
  token. A cross-origin `POST` with `Content-Type: text/plain` is a CORS simple
  request — sent with no preflight — so any website the user visited while the
  UI was running could rewrite their knowledge graph. Now guarded by a Host
  allow-list (blocks DNS rebinding), an Origin allow-list, and a session token
  required in a custom header. The API never accepts the session cookie as
  proof, so it cannot be used as a CSRF credential.
- **`open_dashboard` no longer takes a bind address.** It was model-controlled,
  so a prompt-injected agent could call `open_dashboard(host="0.0.0.0")` and
  publish the graph to the local network. It always binds loopback.
- SQL is no longer echoed back to the model in error payloads; it is logged.

### Fixed

- **Traversal is breadth-first.** The recursive-CTE implementation carried a
  per-row `visited` array, which cannot stop a node being re-expanded along
  another route, so it enumerated every simple path. On a 14-node, 91-edge
  graph at `max_hops=6` it materialised 1,409,006 intermediate rows in 6.4
  seconds to return 13 entities; the replacement returns the same 13 in 1 ms.
  The `_MAX_VISITED` guard could never fire — it bounded a per-path array whose
  length cannot exceed `max_hops + 1`.
- **Concurrent transactions no longer destroy each other.** Nesting depth was a
  plain integer on the shared connection, so a second task opened a savepoint
  inside the first's transaction and the first's rollback erased work the
  second had committed. Transactions now serialise on a write lock with nesting
  tracked per task, and use `BEGIN IMMEDIATE`.
- **Case-variant names merge instead of aborting the batch.** The existence
  probe was case-sensitive while the unique index is `COLLATE NOCASE`, so
  adding `"alice"` after `"Alice"` raised a constraint error that rolled back
  every entity in the batch.
- **Merging linked entities no longer creates self-loops.** Merging A into B
  when `A→B` existed rewrote the edge to `B→B` — the most common merge case.
- **`pip install` ships the real skill.** The installer resolved its files
  through a path that only existed in a source checkout, so every install wrote
  a 3.7 KB fallback instead of the 25 KB skill while reporting success.
  `--domain` was a silent no-op.
- **Search scores mean something.** Reciprocal Rank Fusion was computed
  correctly and then max-normalised, forcing the top hit to exactly 1.0 however
  poor it was and making `min_score` a threshold against the best result rather
  than against relevance.
- **Scoped search returns what it was asked for.** `entity_types` and
  `entity_id` were applied *after* a fixed-size candidate pool had been drawn,
  so entities of the requested type never entered the pool when enough entities
  of other types outranked them. Measured: 200 matching notes plus 3 matching
  people, `limit=3` filtered to people, returned 0 results. Both filters are now
  `WHERE` clauses on the retrieval query itself.
- **Search degrades instead of failing when its index is damaged.** Four
  handlers listed only `sqlite3.Error`, but every query runs through
  `Database.fetch_all`, which wraps failures in `DatabaseError` — not a
  subclass. The handlers were unreachable, so a damaged full-text index
  propagated out of search, and `EmbeddingEngine.initialize` raised despite
  documenting that it never does, taking down start-up rather than disabling
  semantic search.
- Model inference runs off the event loop. Only the model *load* was wrapped in
  `to_thread`; the 50–500 ms inference was not.
- The embedding cache primary key is `(content_hash, model_name)`. It was
  `content_hash` alone while every read filtered on both, so two models could
  not coexist: every lookup missed and every embed recomputed.
- Switching to a different model of the same dimension now warns instead of
  silently querying stale vectors in the new model's embedding space.
- `initialize()` is idempotent; it used to leak a connection per call.
- Migrations are selected by applied set, not `MAX(version)`, so a backported
  migration is no longer skipped forever. A database written by a newer version
  is refused rather than written to by older code.
- Entity name resolution is deterministic. Without an `ORDER BY`, which entity a
  shared name resolved to depended on the query plan, so observations could
  land on a different entity from one call to the next.
- Eight of nineteen agent install paths pointed where the tool does not read.
  Cursor is the starkest: `.cursor/rules/*.md` is *silently ignored*; only
  `.mdc` is loaded.
- `python -m build` was broken — the frontend was force-included on top of the
  package walk, so hatchling aborted on duplicate files.
- `pytest` works from a clean clone without `PYTHONPATH`.

### Changed

- **Breaking:** `find_connections` paths now include the origin entity, so a
  two-hop path has three entries. `find_paths` already did this; the two
  disagreed.
- **Breaking:** `audit_graph` returns a dict like every other tool, with the
  human-readable text in a `report` field. It returned a bare `str`, and its
  error path returned `"AUDIT ERROR: ..."` — indistinguishable from success.
- **Breaking:** the installer supports 13 agents, down from 19. Six
  (`qoder`, `trae`, `codebuddy`, `kilocode`, `warp`, `augment`) were removed
  because no vendor documentation for their instruction paths could be found.
  Every remaining path cites its source, and that citation is now required.
- `get_subgraph` returns `truncated`; capped responses report the cap rather
  than silently returning a subset.
- `add_entities` and `add_relationships` have real JSON schemas. They took
  `list[dict[str, Any]]`, so clients received `{"type": "object"}` with no
  properties and the model had to guess key names.
- `find_connections(direction=...)` is constrained to
  `outgoing | incoming | both` in the schema, and an unrecognised value raises
  instead of silently falling back to `both`.
- The storage abstraction was removed. A 476-line base class advertised Neo4j,
  Memgraph, and PostgreSQL while exposing `fetch_all(sql)` — unimplementable by
  any of them, and already depended on by fifteen call sites.

### Performance

- Traversal: 5374x on the measured case above; linear in visited nodes and
  edges rather than in simple paths.
- Graph canvas: Barnes-Hut replaces all-pairs repulsion. 5000 nodes went from
  3.7 fps to 80 fps; edge insertion on 5000 edges from 5625 ms to 3.7 ms.
- `most_connected_entities` no longer joins on
  `source_id = e.id OR target_id = e.id`, which can use neither index.
- Embedding cache reads and writes are batched; pruning is amortised.
- Frontend CSS dropped from 26.7 kB to 17.3 kB by removing an unused Tailwind
  dependency.

### Added

- `SECURITY.md` — threat model and both trust boundaries.
- `docs/ARCHITECTURE.md` — the load-bearing decisions, their costs, and the
  measured baselines.
- `server.json` for the official MCP Registry.
- `py.typed`, so the package's types are visible to consumers.
- `benchmarks/bench_traversal.py`.
- CI now runs mypy strict, builds the frontend and fails if the committed
  bundle has drifted, and installs the wheel to verify the bundled skill and
  frontend resolve from an installed package rather than only from a checkout.

### Testing

520 tests to 1055. Warnings are errors — the previous configuration silenced the
aiosqlite worker-thread exception warning, which was hiding a real connection
leak. Every fix carries a test that fails without it, verified by reverting.

## [0.1.0]

Initial release.

[0.2.0]: https://github.com/Sathvik-1007/GraphMem-MCP/releases/tag/v0.2.0
[0.1.0]: https://github.com/Sathvik-1007/GraphMem-MCP/releases/tag/v0.1.0
