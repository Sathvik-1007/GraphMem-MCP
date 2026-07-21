# Architecture

Why the pieces are arranged this way, and which decisions are load-bearing.
For *what* each module does, see [how-it-works.md](../how-it-works.md); for
usage, see the [README](../README.md).

---

## Layering

Dependencies point one direction only. Nothing below reaches upward.

```
tools/          MCP tool surface — the trust boundary
   |
graph/  semantic/     traversal, merging, ranking
   |
storage/        every SQL statement in the project
   |
db/             one connection, transactions, PRAGMAs
```

`ui/` sits beside `tools/` — a second, HTTP-shaped entry point onto the same
engines. `cli/` sits above everything and wires it together. `models/` and
`utils/` are leaves that anything may import.

The rule worth stating explicitly: **`graph/` and `semantic/` never open a
connection or write SQL of their own.** They take a backend and call methods on
it. When that rule was broken — raw SQL passed through `fetch_all` — the result
was three unbounded `IN (...)` queries that would raise `OperationalError` on a
large enough graph, and an abstraction that could not be implemented by
anything except SQLite.

---

## Decisions

### Traversal is Python, not a recursive CTE

**Decision:** breadth-first search is level-stepped in Python, one indexed
adjacency query per hop.

**Why:** SQLite's recursive CTE cannot express a global visited set — the
recursive term cannot query the rows the CTE has produced so far. A CTE
carrying a per-row `visited` array looks like BFS but enumerates every simple
path, so cost grows with the number of paths rather than the size of the graph.
Measured on a 14-node, 91-edge graph at `max_hops=6`: **1,409,006 intermediate
rows in 6.4 seconds**, versus 1 millisecond for the BFS returning the identical
13 entities.

**Cost:** up to ten round trips instead of one query. That is the trade, and it
is not close.

**Alternative rejected:** keeping the CTE with a tighter cap. The cap that
existed (`_MAX_VISITED = 1000`) bounded the per-path array length, which cannot
exceed `max_hops + 1`, so it could never fire. Any working cap would have had
to truncate results arbitrarily rather than fix the complexity.

### One connection, one write lock

**Decision:** a single SQLite connection, with an `asyncio` lock held for the
duration of the outermost transaction, and nesting tracked per *task*.

**Why:** SQLite permits one write transaction per connection, and both the MCP
runtime and the UI server dispatch requests as concurrent tasks. Tracking
nesting depth in a plain attribute made "a second task" indistinguishable from
"the same task re-entering": the second opened a savepoint inside the first's
transaction, and a rollback of the first destroyed work the second had already
committed.

**Cost:** writers serialise. For a per-project knowledge graph that is the
correct trade — throughput is not the constraint, and correctness is.

**Known limitation, documented rather than hidden:** a task that opens a
transaction and awaits a *child* task that also writes will deadlock. That is a
wrong program, and deadlocking is preferable to interleaving two transactions
on one connection.

### `BEGIN IMMEDIATE`, not `BEGIN`

Every write path reads before it writes. A deferred transaction that upgrades
from a read lock to a write lock can fail with `SQLITE_BUSY_SNAPSHOT`, which
`busy_timeout` explicitly does not retry. Taking the write lock up front turns
that into an ordinary, retryable wait.

### Search scores are raw RRF

**Decision:** `relevance_score` is the unnormalised Reciprocal Rank Fusion sum,
bounded by `1/(k+1)` ≈ 0.0164 with `k = 60`.

**Why:** normalising to 0-1 forces the top hit to score exactly 1.0 however bad
it is, which makes `min_score` a threshold against the best result rather than
against relevance — useless on a uniformly poor result set. Raw RRF scores are
comparable across queries.

**Cost:** the numbers look small and unfamiliar. Documented in the docstrings
and in how-it-works.md rather than hidden behind a cosmetic rescale.

### Filters run before truncation

Applying `entity_types` or `entity_id` *after* cutting the candidate list to
`limit` means a scoped search returns nothing whenever the match is not also
globally top-ranked — precisely the case scoping exists for. Filters therefore
run first, and the `entity_id` scope is pushed into SQL.

### There is one storage backend, and it is the interface

**Decision:** no abstract base class, no registry.

**Why:** the ABC that existed promised Neo4j, Memgraph, and PostgreSQL while
exposing `fetch_all(sql)` and `fetch_one(sql)` — raw SQL strings no graph
database can implement, which fifteen call sites outside the package already
depended on. The registry meant to select an alternative resolved a class and
then returned `SQLiteBackend` regardless, and `Config` only accepted
`"sqlite"`. The abstraction's only concrete output was a 190-line stub in the
test suite that had to be extended every time a real method was added.

**If a second backend is ever wanted:** the work starts by replacing the
raw-SQL escape hatches with typed operations. That is the work the base class
was pretending had already been done.

### The MCP boundary is a trust boundary

Tool arguments come from a language model, which can be steered by any document
it reads. So: names are validated against a grammar before becoming paths,
limits are clamped before reaching SQL, per-item argument shapes are pydantic
models so the JSON schema actually describes them, and every response is
bounded with truncation reported rather than silent.

See [SECURITY.md](../SECURITY.md) for the full model.

### The UI ships a token, not a trust assumption

Binding to loopback is not a boundary — every page the user visits can reach
it. Host allow-list, Origin allow-list, and a session token, layered so no
single mistake is exploitable. The API accepts the token only in a custom
header, never the cookie, because a cross-site request rides cookies but cannot
set headers.

### The frontend bundle is built into the package

`vite build` writes straight into `src/graph_mem/ui/frontend/`, and CI fails if
the committed bundle differs from a fresh build. The previous arrangement built
into `dist/` and relied on someone copying it across, which is how a shipped
bundle drifts from the source it claims to be built from.

---

## Performance baselines

Measured on the development machine (Python 3.13.12, warm cache). Reproduce
with `python benchmarks/bench_traversal.py`.

| Operation | Graph | Result |
|-----------|-------|--------|
| `find_connections(max_hops=6)` | 14 nodes, 91 edges | 0.002 s |
| `find_connections(max_hops=6)` | 30 nodes, 435 edges | 0.002 s |
| `find_connections(max_hops=6)` | 60 nodes, 1770 edges | 0.005 s |

Frontend force simulation, full tick, median of 20-60 ticks:

| Nodes | Naive O(n²) | Barnes-Hut | Speedup |
|-------|-------------|------------|---------|
| 500 | 2.42 ms | 0.91 ms | 2.7x |
| 1000 | 8.54 ms | 1.69 ms | 5.1x |
| 2000 | 33.68 ms | 4.65 ms | 7.2x |
| 5000 | 271.04 ms | 12.44 ms | 21.8x |

Barnes-Hut approximation error against the exact sum at theta = 0.9: RMS 1.30%.
Settled layouts after 600 ticks are equivalent.

Edge insertion, `Set` versus the previous linear scan: 5000 edges went from
5625 ms to 3.7 ms.

---

## Bounds

Every limit is named, documented, and configurable. Nothing is unbounded.

| Bound | Default | Where |
|-------|---------|-------|
| Traversal node budget | 5000 | `GRAPHMEM_TRAVERSAL_NODE_BUDGET` |
| Traversal depth | 10 hops | `MAX_HOPS_LIMIT` |
| Subgraph radius | 5 | `MAX_RADIUS_LIMIT` |
| Shortest paths returned | 10 | `MAX_SHORTEST_PATHS` |
| List results | 500 | `MAX_LIST_LIMIT` |
| Search results | 100 | `MAX_SEARCH_LIMIT` |
| Traversal results | 200 | `MAX_TRAVERSAL_RESULTS` |
| Nested items per entity | 50 | `MAX_NESTED_ITEMS` |
| SQL bound variables per statement | 900 | `_MAX_SQL_VARIABLES` |
| Graph name length | 64 | `_MAX_GRAPH_NAME_LENGTH` |

A result that hits a bound says so — `truncated`, `observation_count`,
`relationship_count` — rather than returning a silent subset.

---

## Testing

Gates, all enforced in CI:

- `pytest` — warnings are errors. The configuration previously silenced an
  unhandled-exception warning from aiosqlite's worker thread; turning it back
  on immediately surfaced a connection leak.
- `mypy src/graph_mem` — strict. Configured for a long time and run nowhere,
  so the configuration described a standard the code was not held to.
- `ruff check` and `ruff format` over `src/`, `tests/`, and `benchmarks/`.
- A frontend job that fails if the committed bundle differs from a fresh build.
- A wheel-install job asserting the bundled skill and frontend resolve from an
  *installed* package, not just from a source checkout. That check exists
  because a packaging path resolved correctly only in a developer's checkout,
  so every `pip install` shipped a 3.7 KB fallback instead of the 25 KB skill —
  and the tests could not catch it, because they ran in the layout that worked.

Every bug fix carries a test that fails without the fix. Where a defect was
originally reproduced with a script, the reproduction became the test.
